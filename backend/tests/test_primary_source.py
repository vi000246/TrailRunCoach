"""主要資料來源 (backend/sync/primary.py): 自動's pick, the COROS / TP merge
(one file per activity, no per-value back-fill), the sync order and the
settings API. Synthetic data only; no live COROS / TP."""
import asyncio
import datetime as dt
from datetime import datetime, timezone

import pytest

from backend.engine.wko5expr.config import EngineConfig
from backend.sync import primary as P
from backend.tests.fit_builder import build_run

D = dt.date


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- 自動 ----------------------------------------------------------------------

def test_choose_auto_newest_then_complete_then_recent_then_coros():
    s = lambda latest, recent=10, status="ok": {"latest": latest, "recent": recent, "status": status}
    assert P.choose_auto({"coros": s(D(2026, 10, 1)), "trainingpeaks": s(D(2026, 9, 30))}) == "coros"
    assert P.choose_auto({"coros": s(D(2026, 9, 1)), "trainingpeaks": s(D(2026, 9, 30))}) == "trainingpeaks"
    # same day: a clean last sync beats a failed / partial one
    assert P.choose_auto({"coros": s(D(2026, 10, 1), status="partial"),
                          "trainingpeaks": s(D(2026, 10, 1))}) == "trainingpeaks"
    # then more recent activities, then COROS
    assert P.choose_auto({"coros": s(D(2026, 10, 1), 5), "trainingpeaks": s(D(2026, 10, 1), 9)}) == "trainingpeaks"
    assert P.choose_auto({"coros": s(D(2026, 10, 1)), "trainingpeaks": s(D(2026, 10, 1))}) == "coros"
    assert P.choose_auto({"coros": s(None), "trainingpeaks": s(D(2020, 1, 1), status=None)}) == "trainingpeaks"
    assert P.choose_auto({}) is None and P.choose_auto({"coros": s(None)}) is None


def test_normalize_and_resolve():
    assert P.normalize(None) == P.normalize("") == P.normalize("auto") == "auto"
    assert P.resolve("coros", {}) == "coros"
    assert P.resolve(None, {"trainingpeaks": {"latest": D(2026, 1, 1), "recent": 1, "status": None}}) == "trainingpeaks"
    assert P.to_db("tp") == "trainingpeaks" and P.to_db("coros") == "coros" and P.to_db("auto") == "auto"
    st = P.stats_from_starts([datetime(2026, 1, 1), datetime(2026, 7, 1), datetime(2026, 9, 1)], "ok")
    assert st == {"latest": D(2026, 9, 1), "recent": 2, "status": "ok"}


def test_merge_keeps_primary_and_fills_only_missing():
    t = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    items = [(t, "coros", "c1"), (t + dt.timedelta(seconds=90), "trainingpeaks", "t1"),      # same activity
             (t + dt.timedelta(days=1), "trainingpeaks", "t2"),                              # TP only
             (t + dt.timedelta(days=2), "coros", "c3"),
             (t + dt.timedelta(days=2, seconds=30), "coros", "c3bb")]                        # same source, same activity
    kept, dropped = P.merge(items, "coros")
    assert [x[2] for x in kept] == ["c1", "t2", "c3"] and [x[2] for x in dropped] == ["t1", "c3bb"]
    kept, dropped = P.merge(items, "trainingpeaks")
    assert [x[2] for x in kept] == ["t1", "t2", "c3"] and [x[2] for x in dropped] == ["c1", "c3bb"]
    # same-source copies: the better one by `quality` (e.g. more samples)
    assert [x[2] for x in P.merge(items, "coros", quality=len)[0]] == ["c1", "t2", "c3bb"]
    assert [x[2] for x in P.merge(items, None)[0]] == ["c1", "t2", "c3"]
    # 3 min apart is another activity
    far = [(t, "coros", "a"), (t + dt.timedelta(minutes=3), "trainingpeaks", "b")]
    assert len(P.merge(far, "coros")[0]) == 2


def test_sync_order_primary_first_secondary_optional():
    ready = {"coros": "ready", "tp": "ready"}
    assert P.sync_order(ready, "trainingpeaks", False) == (["tp"], {"coros": "secondary"})
    assert P.sync_order(ready, "trainingpeaks", True) == (["tp", "coros"], {})
    assert P.sync_order(ready, "coros", False) == (["coros"], {"tp": "secondary"})
    # no data yet (自動 has no pick): every ready source
    assert P.sync_order(ready, None, False) == (["coros", "tp"], {})
    # the primary switched off / logged out: the other one syncs
    assert P.sync_order({"coros": "disabled", "tp": "ready"}, "coros", False) == (["tp"], {"coros": "disabled"})
    # busy primary: nothing else instead
    assert P.sync_order({"coros": "busy", "tp": "ready"}, "coros", False) == ([], {"coros": "busy", "tp": "secondary"})


# ---- the merged Dataset --------------------------------------------------------

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def _write(root, folder, name, data):
    d = root / folder / "2026"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)


def _fixture_files(root):
    # A: both sources; COROS has no power, TP has power -> never back-filled from TP
    _write(root, "coros", "a.fit", build_run(T0, seconds=900))
    _write(root, "tp", "tp_a.fit", build_run(T0 + dt.timedelta(seconds=40), seconds=900, power=230, stryd=True))
    # B: TP only (COROS lacks it) -> filled from TP
    _write(root, "tp", "tp_b.fit", build_run(T0 + dt.timedelta(days=1), seconds=600))
    # C: COROS only, the newest activity
    _write(root, "coros", "c.fit", build_run(T0 + dt.timedelta(days=2), seconds=600))


def _ds(root, primary, status=None, parity=True, tmp_path=None):
    from backend.engine.wko5expr.corrections import CorrectionStore
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    return FitFolderDataset(root, config=EngineConfig(parity=parity), today=dt.date(2026, 10, 1),
                            corrections=CorrectionStore((tmp_path or root) / "corr.json"), classifications={},
                            athlete_settings=[], estimate_thresholds=False, tz=timezone.utc,
                            source="synced", merge=("coros", "tp"), primary=primary, primary_status=status or {})


def test_merged_dataset_primary_coros(_fit_root_in_tmp, no_plan):
    root = _fit_root_in_tmp
    _fixture_files(root)
    ds = _ds(root, "coros")
    assert [w.entry.file for w in ds.workouts] == ["coros/2026/a.fit", "tp/2026/tp_b.fit", "coros/2026/c.fit"]
    assert [ds.file_origin(w) for w in ds.workouts] == ["coros", "tp", "coros"]
    a = ds.workouts[0]
    # 「算不出來就不要補了」: COROS's A has no power, TP's power is not used
    assert a.metrics.get("np") is None and ds.channel(0, "power") is None
    assert ds.channel(1, "heartrate") is not None                # the TP file's channels load
    assert ds.merge_info["primary"] == "coros" and ds.merge_info["replaced"] == 1
    assert ds.merge_info["kept"] == {"coros": 2, "trainingpeaks": 1}


def test_merged_dataset_primary_tp_and_auto(_fit_root_in_tmp, no_plan):
    root = _fit_root_in_tmp
    _fixture_files(root)
    ds = _ds(root, "tp")
    assert [w.entry.file for w in ds.workouts] == ["tp/2026/tp_a.fit", "tp/2026/tp_b.fit", "coros/2026/c.fit"]
    assert ds.workouts[0].metrics.get("np") == pytest.approx(230, abs=2)
    # 自動: COROS has the newest activity (C)
    ds = _ds(root, "auto")
    assert ds.merge_info["auto"] and ds.merge_info["primary"] == "coros"
    # a newer TP-only activity moves 自動 to TP
    _write(root, "tp", "tp_d.fit", build_run(T0 + dt.timedelta(days=3), seconds=600))
    ds = _ds(root, "auto")
    assert ds.merge_info["primary"] == "trainingpeaks"
    assert ds.workouts[0].entry.file == "tp/2026/tp_a.fit"


def test_same_source_copies_keep_the_fuller_file(_fit_root_in_tmp, no_plan):
    root = _fit_root_in_tmp
    _fixture_files(root)
    _write(root, "tp", "tp_b_copy.fit", build_run(T0 + dt.timedelta(days=1, seconds=10), seconds=30))
    ds = _ds(root, "coros")
    assert [w.entry.file for w in ds.workouts] == ["coros/2026/a.fit", "tp/2026/tp_b.fit", "coros/2026/c.fit"]


def test_bad_primary_file_is_not_replaced(_fit_root_in_tmp, no_plan, tmp_path):
    """The primary's file of an activity is a car ride (excluded): the
    activity is excluded, the other source's file does not stand in."""
    root = _fit_root_in_tmp
    _write(root, "coros", "car.fit", build_run(T0, seconds=1037, speed_m_s=12.0, hr=66))
    _write(root, "tp", "tp_car.fit", build_run(T0 + dt.timedelta(seconds=20), seconds=1037, speed_m_s=3.0))
    ds = _ds(root, "coros", parity=False, tmp_path=tmp_path)
    assert ds.workouts == []
    assert [x["file"] for x in ds.excluded] == ["coros/2026/car.fit"]


def test_merged_reuses_the_single_source_cache(_fit_root_in_tmp, no_plan, monkeypatch):
    """The merged Dataset reads the per-folder caches: nothing is re-parsed."""
    from backend.engine.wko5expr import fitcache
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    root = _fit_root_in_tmp
    _fixture_files(root)
    for f in ("coros", "tp"):
        FitFolderDataset(root / f, config=EngineConfig(parity=True), classifications={}, athlete_settings=[],
                         estimate_thresholds=False, tz=timezone.utc, source=f)
    calls = []
    real = fitcache.parse_file
    monkeypatch.setattr(fitcache, "parse_file", lambda *a: calls.append(a) or real(*a))
    ds = _ds(root, "coros")
    assert len(ds.workouts) == 3 and calls == []


def test_dataset_for_source_synced(_fit_root_in_tmp, no_plan, monkeypatch):
    from backend.engine.wko5expr import fitdataset as FD
    _fixture_files(_fit_root_in_tmp)
    monkeypatch.setattr(FD, "load_classifications", lambda db=None: {})
    ds = FD.dataset_for_source("synced", _fit_root_in_tmp / "no-wko5", config=EngineConfig(parity=True))
    assert ds.source == "synced" and ds.merge == ("coros", "tp") and len(ds.workouts) == 3
    assert ds.merge_info["setting"] == "auto"                    # no app DB: the default


def test_activity_list_shows_the_origin(_fit_root_in_tmp, no_plan, monkeypatch):
    from backend.api import wko5views as V
    _fixture_files(_fit_root_in_tmp)
    ds = _ds(_fit_root_in_tmp, "coros")
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    r = V.activities_list()
    assert {a["file"]: a["origin"] for a in r["activities"]} == {
        "coros/2026/a.fit": "coros", "tp/2026/tp_b.fit": "tp", "coros/2026/c.fit": "coros"}
    assert r["source"] == "synced" and r["merge"]["primary"] == "coros"
    assert V._activity_json(ds, ds.workouts[1])["origin"] == "tp"


def test_source_stamp_synced_follows_the_setting(tmp_path, monkeypatch, _fit_root_in_tmp):
    import json
    import sqlite3
    from backend.engine.wko5expr import datasource as DS
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE user_settings (id INTEGER PRIMARY KEY, user_id INT, key TEXT, value_json TEXT, updated_at TEXT)")
    con.commit()
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    a = DS.source_stamp("synced", tmp_path)
    con.execute("INSERT INTO user_settings (user_id, key, value_json) VALUES (1, 'sync.primary_source', ?)",
                (json.dumps("trainingpeaks"),))
    con.commit()
    b = DS.source_stamp("synced", tmp_path)
    con.close()
    assert a != b and "synced[auto;" in a and "synced[trainingpeaks;" in b
    assert DS.current_source() == "synced"


# ---- the DB side ---------------------------------------------------------------

def test_db_auto_dedup_and_settings_api(tmp_path):
    from backend.api.sync import SyncSettingsBody, get_sync_settings, put_sync_settings
    from backend.db.models import WorkoutFile
    from backend.sync import dedup, runner
    from backend.tests.test_sync_e2e import make_session

    async def go():
        s = await make_session(tmp_path)
        a = WorkoutFile(athlete_id=1, file_path="a", file_format="fit", source="coros",
                        start_time_utc=datetime(2026, 9, 1, 8, 0), workout_date=D(2026, 9, 1))
        b = WorkoutFile(athlete_id=1, file_path="b", file_format="fit", source="trainingpeaks",
                        start_time_utc=datetime(2026, 9, 1, 8, 1), workout_date=D(2026, 9, 1))
        c = WorkoutFile(athlete_id=1, file_path="c", file_format="fit", source="trainingpeaks",
                        start_time_utc=datetime(2026, 9, 5, 8, 0), workout_date=D(2026, 9, 5))
        s.add_all([a, b, c])
        await s.commit()
        got = await get_sync_settings(1, s)
        # 自動: TP has the newest activity
        assert got["primary_source"] == "auto" and got["primary_effective"] == "trainingpeaks"
        assert got["secondary_auto"] is False and got["chart_data_source"] == "synced"
        await dedup.rebuild(s, 1)
        assert a.duplicate_of == b.id and b.duplicate_of is None
        r = await put_sync_settings(SyncSettingsBody(primary_source="coros"), 1, s)
        assert r["primary_effective"] == "coros" and b.duplicate_of == a.id and a.duplicate_of is None
        # the auto-sync plan: primary only, the other with 進階 secondary_auto
        async def ready(db, athlete_id=1):
            return {"coros": "ready", "tp": "ready"}
        runner_ready = runner.ready_sources
        runner.ready_sources = ready
        try:
            assert await runner.auto_plan(s, 1) == (["coros"], {"tp": "secondary"})
            await put_sync_settings(SyncSettingsBody(secondary_auto=True), 1, s)
            assert await runner.auto_plan(s, 1) == (["coros", "tp"], {})
        finally:
            runner.ready_sources = runner_ready
        r = await put_sync_settings(SyncSettingsBody(primary_source="auto"), 1, s)
        assert r["primary_source"] == "auto" and r["primary_effective"] == "trainingpeaks"
    _run(go())
