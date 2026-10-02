"""資料來源 (backend/sync/primary.py): ONE synced source at a time — the
migration of the old 自動 setting, the chart Dataset reading only that
folder, automatic syncs, the CP scan, the DB totals and the settings API.
Synthetic data only; no live COROS / TP."""
import asyncio
import datetime as dt
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.engine.wko5expr.config import EngineConfig
from backend.sync import primary as P
from backend.tests.fit_builder import build_run

D = dt.date


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- the setting ---------------------------------------------------------------

def test_normalize_effective_and_folders():
    assert P.normalize("coros") == "coros" and P.normalize("trainingpeaks") == "trainingpeaks"
    assert P.normalize("tp") == "trainingpeaks"
    assert P.normalize(None) is None and P.normalize("auto") is None and P.normalize("local") is None
    assert P.effective(None) == P.effective("auto") == "coros"
    assert P.folder("trainingpeaks") == "tp" and P.other_folder("trainingpeaks") == "coros"
    assert P.folder(None) == "coros" and P.other_folder(None) == "tp"


def test_choose_initial_is_the_old_auto_pick():
    assert P.choose_initial({"coros": D(2026, 10, 1), "trainingpeaks": D(2026, 9, 30)}) == "coros"
    assert P.choose_initial({"coros": D(2026, 9, 1), "trainingpeaks": D(2026, 9, 30)}) == "trainingpeaks"
    assert P.choose_initial({"coros": D(2026, 9, 1), "trainingpeaks": D(2026, 9, 1)}) == "coros"
    assert P.choose_initial({"coros": None, "trainingpeaks": None}) == "coros"
    assert P.choose_initial({}) == "coros"


# ---- the chart Dataset: one folder only ----------------------------------------

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
    # A: both sources; COROS has no power, TP has power
    _write(root, "coros", "a.fit", build_run(T0, seconds=900))
    _write(root, "tp", "tp_a.fit", build_run(T0 + dt.timedelta(seconds=40), seconds=900, power=230, stryd=True))
    # B: TP only
    _write(root, "tp", "tp_b.fit", build_run(T0 + dt.timedelta(days=1), seconds=600))
    # C: COROS only
    _write(root, "coros", "c.fit", build_run(T0 + dt.timedelta(days=2), seconds=600))


def _settings_db(tmp_path, monkeypatch, **values):
    from backend.engine.wko5expr import datasource as DS
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS user_settings (id INTEGER PRIMARY KEY, user_id INT, key TEXT, "
                "value_json TEXT, updated_at TEXT)")
    con.execute("DELETE FROM user_settings")
    for k, v in values.items():
        con.execute("INSERT INTO user_settings (user_id, key, value_json) VALUES (1, ?, ?)",
                    (k.replace("__", "."), json.dumps(v)))
    con.commit()
    con.close()
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    return db


def test_current_source_is_the_chosen_folder(tmp_path, monkeypatch):
    from backend.engine.wko5expr import datasource as DS
    monkeypatch.delenv("WKO5_ATHLETE_DIR", raising=False)
    nowko5 = tmp_path / "no-wko5"
    _settings_db(tmp_path, monkeypatch)
    assert DS.current_source(wko5_dir=nowko5) == "coros"                 # nothing stored yet: COROS
    _settings_db(tmp_path, monkeypatch, sync__primary_source="trainingpeaks")
    assert DS.current_source(wko5_dir=nowko5) == "tp"
    # an old merged / per-folder chart value follows the 資料來源
    for old in ("synced", "coros", "tp"):
        _settings_db(tmp_path, monkeypatch, sync__primary_source="trainingpeaks", charts__data_source=old)
        assert DS.current_source(wko5_dir=nowko5) == "tp"
    # wko5 only with a WKO5 folder
    _settings_db(tmp_path, monkeypatch, sync__primary_source="coros", charts__data_source="wko5")
    assert DS.current_source(wko5_dir=nowko5) == "coros"
    w = tmp_path / "wko5"
    w.mkdir()
    (w / "A.wko5athlete").write_bytes(b"")
    assert DS.current_source(wko5_dir=w) == "wko5"


def test_dataset_reads_only_the_chosen_source(_fit_root_in_tmp, no_plan, monkeypatch):
    from backend.engine.wko5expr import fitdataset as FD
    _fixture_files(_fit_root_in_tmp)
    monkeypatch.setattr(FD, "load_classifications", lambda db=None: {})
    ds = FD.dataset_for_source("coros", _fit_root_in_tmp / "no-wko5", config=EngineConfig(parity=True))
    # B (TP only) is not back-filled; A keeps COROS's file without power
    assert [w.entry.file for w in ds.workouts] == ["2026/a.fit", "2026/c.fit"]
    assert ds.workouts[0].metrics.get("np") is None
    assert {ds.file_origin(w) for w in ds.workouts} == {"coros"}
    assert not hasattr(ds, "merge_info")
    ds = FD.dataset_for_source("tp", _fit_root_in_tmp / "no-wko5", config=EngineConfig(parity=True))
    assert [w.entry.file for w in ds.workouts] == ["2026/tp_a.fit", "2026/tp_b.fit"]
    assert ds.workouts[0].metrics.get("np") == pytest.approx(230, abs=2)
    with pytest.raises(Exception):
        FD.FitFolderDataset(_fit_root_in_tmp, merge=("coros", "tp"))      # the merge is gone


def test_cp_scan_reads_only_the_chosen_folder(tmp_path, monkeypatch):
    from backend.engine.racepower import cptest as T
    home = tmp_path / "home"
    for f, name in (("coros", "run_2026-09-01_a.fit"), ("tp", "tp_2026_09_02_b.fit")):
        (home / "fit" / f).mkdir(parents=True, exist_ok=True)
        (home / "fit" / f / name).write_bytes(build_run(T0, seconds=120, power=250, stryd=True))
    seen = []
    monkeypatch.setattr(T, "_read", lambda p: seen.append(p.parent.name) or None)
    T._primary_files(home, D(2026, 1, 1), D(2026, 12, 31))
    assert seen == ["coros"]                                             # no DB: COROS
    seen.clear()
    _settings_db(tmp_path, monkeypatch, sync__primary_source="trainingpeaks")
    T._primary_files(home, D(2026, 1, 1), D(2026, 12, 31))
    assert seen == ["tp"]


# ---- the DB side ---------------------------------------------------------------

def _rows():
    from backend.db.models import WorkoutFile
    a = WorkoutFile(athlete_id=1, file_path="a", file_format="fit", source="coros", sport="run",
                    start_time_utc=datetime(2026, 9, 1, 8, 0), workout_date=D(2026, 9, 1))
    b = WorkoutFile(athlete_id=1, file_path="b", file_format="fit", source="trainingpeaks", sport="run",
                    start_time_utc=datetime(2026, 9, 1, 8, 1), workout_date=D(2026, 9, 1))
    c = WorkoutFile(athlete_id=1, file_path="c", file_format="fit", source="trainingpeaks", sport="run",
                    start_time_utc=datetime(2026, 9, 5, 8, 0), workout_date=D(2026, 9, 5))
    return a, b, c


def test_migration_of_the_old_auto_setting(tmp_path):
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_sync_e2e import make_session
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async def go():
        s = await make_session(tmp_path)
        s.add_all(_rows())
        # what the old setup stored: "auto"
        from backend.db.models import UserSetting
        s.add(UserSetting(user_id=1, key=P.SETTING_KEY, value_json=json.dumps("auto")))
        await s.commit()
        assert P.normalize(await SettingsRepository(s, 1).get(P.SETTING_KEY)) is None
        factory = async_sessionmaker(s.bind, expire_on_commit=False)
        # TP has the newest activity -> TP (what 自動 used)
        assert await P.migrate(factory) == "trainingpeaks"
        assert await SettingsRepository(s, 1).get(P.SETTING_KEY) == "trainingpeaks"
        assert await P.migrate(factory) == "trainingpeaks"                # idempotent
    _run(go())


def test_owner_migration_defaults_to_coros(tmp_path):
    from backend.tests.test_sync_e2e import make_session

    async def go():
        s = await make_session(tmp_path)
        a, b, _ = _rows()
        s.add_all([a, b])                                                # same day: COROS
        await s.commit()
        assert await P.current(s, 1) == "coros"
    _run(go())


def test_settings_api_switch_dedup_totals_and_auto_sync(tmp_path):
    from sqlalchemy import func, select
    from backend.api.sync import SyncSettingsBody, get_sync_settings, put_sync_settings
    from backend.db.models import WorkoutFile
    from backend.sync import dedup, runner
    from backend.tests.test_sync_e2e import make_session

    async def go():
        s = await make_session(tmp_path, sync__primary_source="coros")
        a, b, c = _rows()
        s.add_all([a, b, c])
        await s.commit()
        got = await get_sync_settings(1, s)
        assert got["primary_source"] == "coros" and got["primary_label"] == "COROS"
        assert got["primary_folder"] == "coros" and got["chart_data_source"] == "source"
        assert "secondary_auto" not in got and "primary_effective" not in got
        await dedup.rebuild(s, 1)
        assert b.duplicate_of == a.id and a.duplicate_of is None

        async def used():
            return (await s.execute(select(WorkoutFile.file_path).where(await dedup.in_use(s, 1))
                                    .order_by(WorkoutFile.file_path))).scalars().all()
        # totals: COROS only — c (TP only) is not back-filled
        assert await used() == ["a"]
        r = await put_sync_settings(SyncSettingsBody(primary_source="trainingpeaks"), 1, s)
        assert r["primary_source"] == "trainingpeaks" and a.duplicate_of == b.id
        assert await used() == ["b", "c"]
        assert (await s.execute(select(func.count(WorkoutFile.id)))).scalar() == 3   # nothing deleted

        # automatic syncs: only the source in use
        async def ready(db, athlete_id=1):
            return {"coros": "ready", "tp": "ready"}
        real = runner.ready_sources
        runner.ready_sources = ready
        try:
            assert await runner.auto_plan(s, 1) == (["tp"], {"coros": "not_in_use"})
            await put_sync_settings(SyncSettingsBody(primary_source="coros"), 1, s)
            assert await runner.auto_plan(s, 1) == (["coros"], {"tp": "not_in_use"})

            async def tp_off(db, athlete_id=1):
                return {"coros": "not_logged_in", "tp": "ready"}
            runner.ready_sources = tp_off
            # the source in use logged out: nothing syncs (never the other one instead)
            assert await runner.auto_plan(s, 1) == ([], {"coros": "not_logged_in", "tp": "not_in_use"})
        finally:
            runner.ready_sources = real
        # 自動 and unknown values are refused now
        from fastapi import HTTPException
        for bad in ("auto", "local", "garmin"):
            with pytest.raises(HTTPException):
                await put_sync_settings(SyncSettingsBody(primary_source=bad), 1, s)
        with pytest.raises(HTTPException):
            await put_sync_settings(SyncSettingsBody(chart_data_source="synced"), 1, s)
        r = await put_sync_settings(SyncSettingsBody(chart_data_source="wko5"), 1, s)
        assert r["chart_data_source"] == "wko5"
    _run(go())


def test_switching_keeps_the_other_sources_login(tmp_path):
    """The other source's saved credentials stay untouched (just unused)."""
    from sqlalchemy import select
    from backend.api.sync import SyncSettingsBody, put_sync_settings
    from backend.db.models import SyncState
    from backend.tests.test_sync_e2e import make_session

    async def go():
        s = await make_session(tmp_path, sync__primary_source="coros")
        s.add(SyncState(athlete_id=1, tp_access_token="tp-token", coros_access_token="coros-token"))
        await s.commit()
        await put_sync_settings(SyncSettingsBody(primary_source="trainingpeaks"), 1, s)
        await put_sync_settings(SyncSettingsBody(primary_source="coros"), 1, s)
        st = (await s.execute(select(SyncState))).scalar_one()
        assert st.tp_access_token == "tp-token" and st.coros_access_token == "coros-token"
    _run(go())
