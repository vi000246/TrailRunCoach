"""Bad activity files (backend/engine/bad_activity.py): detection rules,
overrides, the Dataset exclusion and the API. Synthetic data only."""
from __future__ import annotations

import numpy as np
import pytest

from backend.engine import bad_activity as B


def _track(speeds_kmh, dt_s: float = 1.0):
    """(t, distance km) for consecutive constant-speed seconds."""
    v = np.asarray(speeds_kmh, float)
    t = np.arange(1, len(v) + 1) * dt_s
    d = np.cumsum(v * dt_s / 3600.0)
    return list(t), list(d)


# ---- limits ---------------------------------------------------------------

def test_world_record_speeds_at_the_record_durations():
    assert B.wr_speed_kmh(43.03) == pytest.approx(400 / 43.03 * 3.6)
    assert B.wr_speed_kmh(7235.0) == pytest.approx(42195 / 7235 * 3.6)        # 21.0 km/h
    assert B.wr_speed_kmh(10.0) == B.wr_speed_kmh(43.03)                       # clamped
    assert B.wr_speed_kmh(30000.0) == B.wr_speed_kmh(7235.0)
    # monotone: longer = slower
    xs = [60, 120, 300, 600, 1200, 3600]
    ys = [B.wr_speed_kmh(x) for x in xs]
    assert ys == sorted(ys, reverse=True)


# ---- the rules ------------------------------------------------------------

def test_car_file_is_flagged_by_average_speed():
    # the TP 2025-12-14 shape: 17 min at ~43 km/h
    t, d = _track([43.0] * 1037)
    f = B.features(t, d)
    v = B.judge(f, "run")
    assert v and v["rule"] == "avg_speed"
    assert v["reason"] == "疑似交通工具／騎車（均速 43 km/h）"
    assert f["distance_km"] == pytest.approx(12.39, abs=0.05)


def test_real_runs_are_not_flagged():
    # an easy run, a 5 K race at 20 km/h, a 3 h trail run
    for speeds in ([10.0] * 3600, [20.0] * 900, [6.0] * 10800):
        t, d = _track(speeds)
        assert B.judge(B.features(t, d), "run") is None


def test_fast_downhill_and_sprints_are_not_flagged():
    # 2 min at 28 km/h on a descent inside a 1 h run; a 30 s sprint at 36 km/h
    speeds = [10.0] * 1500 + [28.0] * 120 + [10.0] * 1500 + [36.0] * 30 + [10.0] * 450
    t, d = _track(speeds)
    assert B.judge(B.features(t, d), "run") is None


def test_gps_spike_is_ignored():
    t, d = _track([10.0] * 1800)
    d = list(d)
    for i in range(900, len(d)):          # a 2 km jump in one second, then normal
        d[i] += 2.0
    f = B.features(t, d)
    assert B.judge(f, "run") is None
    assert f["distance_km"] == pytest.approx(5.0, abs=0.05)                    # the jump is no distance


def test_vehicle_segment_at_the_end_is_flagged_by_a_sustained_window():
    # a 2 h trail run at 6 km/h, then 10 min in a car at 45 km/h: the average
    # (≈ 9 km/h) is fine, the 5-min window is not
    t, d = _track([6.0] * 7200 + [45.0] * 600)
    f = B.features(t, d)
    assert f["avg_kmh"] < B.limit_kmh(f["moving_s"])
    v = B.judge(f, "run")
    assert v and v["rule"] == "window_speed"
    assert "疑似交通工具／騎車" in v["reason"] and "第 1" in v["reason"]
    assert v["window_start_s"] >= 7200 - 60


def test_impossible_power_is_flagged():
    t, d = _track([10.0] * 1200)
    f = B.features(t, d, power=[900.0] * 1200)
    v = B.judge(f, "run", weight_kg=65.0)
    assert v and v["rule"] == "power" and "W/kg" in v["reason"]
    assert B.judge(B.features(t, d, power=[250.0] * 1200), "run", weight_kg=65.0) is None


def test_only_foot_sports_are_judged():
    t, d = _track([43.0] * 1800)
    f = B.features(t, d)
    assert B.judge(f, "bike") is None
    assert B.judge(f, "walk") is not None          # a hike can include running: same limits


def test_paused_recording_does_not_make_speed():
    # 10 km/h, a 30-min pause (no samples), 10 km/h again
    t1, d1 = _track([10.0] * 1200)
    t2 = [x + 1200 + 1800 for x in t1]
    d2 = [x + d1[-1] for x in d1]
    f = B.features(t1 + t2, d1 + d2)
    assert B.judge(f, "run") is None
    assert f["moving_s"] == pytest.approx(2400, abs=2)


# ---- overrides ------------------------------------------------------------

def test_decide_overrides():
    auto = {"reason": "疑似交通工具／騎車（均速 43 km/h）"}
    assert B.decide(auto, None)["label"] == "已排除：疑似交通工具／騎車（均速 43 km/h）"
    assert B.decide(auto, B.KEEP) is None                       # 這筆是正常的，不要排除
    assert B.decide(None, B.EXCLUDE)["label"] == "已排除：手動排除"
    assert B.decide(auto, None, enabled=False) is None          # setting off: no auto exclusion
    assert B.decide(None, B.EXCLUDE, enabled=False)["manual"]   # manual still applies
    assert B.decide(None, None) is None


def test_overrides_stamp_changes_with_the_rows():
    a = B.overrides_stamp([{"start_local": "2025-12-14T10:00", "file": "x.fit", "exclusion": "keep"}])
    b = B.overrides_stamp([{"start_local": "2025-12-14T10:00", "file": "x.fit", "exclusion": "exclude"}])
    assert a and b and a != b
    assert B.overrides_stamp([{"start_local": "2025-12-14T10:00", "exclusion": None}]) == ""


# ---- the Dataset: excluded files leave ds.workouts --------------------------

import asyncio
import datetime as dt
from datetime import datetime, timezone

from backend.engine import activity_tags as AT
from backend.engine.wko5expr.config import EngineConfig
from backend.tests.fit_builder import build_run

T0 = datetime(2025, 12, 13, 1, 0, tzinfo=timezone.utc)
CAR = 12.0                    # m/s = 43.2 km/h
RUNS = [dict(start=T0, seconds=1800, speed_m_s=3.0, hr=140),                                  # 0: a run
        dict(start=T0 + dt.timedelta(days=1), seconds=1037, speed_m_s=CAR, hr=66,
             power=[900] * 1037),                                                               # 1: the car
        dict(start=T0 + dt.timedelta(days=2), speeds_m_s=[1.7] * 3600 + [CAR] * 600, hr=130),  # 2: run + car at the end
        dict(start=T0 + dt.timedelta(days=3), seconds=1800, speed_m_s=CAR, sport=2)]           # 3: a bike ride (not judged)


@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


@pytest.fixture
def tags_db(monkeypatch, tmp_path):
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    AT._memo.clear()
    return db


def _write(tmp_path, runs=RUNS):
    d = tmp_path / "fit" / "coros" / "2025"
    d.mkdir(parents=True, exist_ok=True)
    for i, kw in enumerate(runs):
        (d / f"{i}.fit").write_bytes(build_run(**kw))
    return tmp_path / "fit" / "coros"


def _ds(tmp_path, exclude=None, parity=False, runs=RUNS):
    from backend.engine.wko5expr.corrections import CorrectionStore
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    root = _write(tmp_path, runs)
    return FitFolderDataset(root, config=EngineConfig(parity=parity), today=dt.date(2026, 1, 1),
                            corrections=CorrectionStore(tmp_path / "corr.json"), classifications={},
                            athlete_settings=[], estimate_thresholds=False, exclude_bad=exclude,
                            tz=timezone.utc)


def test_fit_dataset_leaves_bad_files_out(tmp_path, no_plan):
    ds = _ds(tmp_path)
    files = [w.entry.file for w in ds.workouts]
    assert files == ["2025/0.fit", "2025/3.fit"]                    # the bike ride is not judged
    assert [w.idx for w in ds.workouts] == [0, 1]                   # indices stay contiguous
    assert ds.channel(1, "speed") is not None and ds.wko4(1).path.endswith("3.fit")
    ex = {x["file"]: x for x in ds.excluded}
    assert set(ex) == {"2025/1.fit", "2025/2.fit"}
    assert ex["2025/1.fit"]["label"] == "已排除：疑似交通工具／騎車（均速 43 km/h）"
    assert ex["2025/1.fit"]["distance"] == pytest.approx(12.44, abs=0.1) and ex["2025/1.fit"]["auto"]
    assert ex["2025/2.fit"]["rule"] == "window_speed" and "第 6" in ex["2025/2.fit"]["reason"]
    # the excluded run reaches no aggregate: the PMC / TSS input is ds.workouts
    assert all(w.entry.file not in ex for w in ds.workouts)


def test_setting_off_and_parity_keep_every_file(tmp_path, no_plan):
    assert len(_ds(tmp_path, exclude=False).workouts) == 4
    ds = _ds(tmp_path, parity=True)
    assert len(ds.workouts) == 4 and ds.excluded == []             # WKO5 reads every file


def test_user_keep_and_manual_exclude(tmp_path, no_plan, tags_db):
    AT.upsert(tags_db, start_local="2025-12-14T01:00", file="2025/1.fit", exclusion="keep")
    AT.upsert(tags_db, start_local="2025-12-13T01:00", exclusion="exclude")       # by start minute
    ds = _ds(tmp_path)
    assert [w.entry.file for w in ds.workouts] == ["2025/1.fit", "2025/3.fit"]
    kept = ds.exclusion_kept
    assert len(kept) == 1 and kept[0]["file"] == "2025/1.fit" and kept[0]["override"] == "keep"
    ex = {x["file"]: x for x in ds.excluded}
    assert ex["2025/0.fit"]["label"] == "已排除：手動排除" and ex["2025/0.fit"]["manual"]
    # manual exclusions apply with the auto rule off; the rule's files come back
    ds = _ds(tmp_path, exclude=False)
    assert [w.entry.file for w in ds.workouts] == ["2025/1.fit", "2025/2.fit", "2025/3.fit"]


def test_wko5_dataset_policy_renumbers(no_plan):
    from backend.engine.wko5expr.dataset import Dataset, Workout
    from backend.files.wko5_athlete import WorkoutEntry
    ds = Dataset.__new__(Dataset)
    ds.config = EngineConfig(parity=False)
    ds.corrections = None
    ds._init_exclusion_policy(True)
    car = B.features(*_track([43.0] * 1000))
    run = B.features(*_track([10.0] * 1000))
    ws = []
    for i, f in enumerate((run, car, run)):
        e = WorkoutEntry(file=f"2025/{i}.wko4", sport="Running", sport_group="Run",
                         start=dt.datetime(2025, 12, 13 + i, 9), ftp=None, metrics={4217: 12.0, 4206: 1000.0})
        ws.append(Workout(idx=i, entry=e, day=float(i), sport="run", sport_type="running", tags=[]))
    ds.workouts = ws
    feats = {"2025/0.wko4": run, "2025/1.wko4": car, "2025/2.wko4": run}
    ds._bad_features = lambda w: feats[w.entry.file]
    ds.setting = lambda name, day: 65.0
    ds._apply_exclusion_policy()
    assert [(w.idx, w.entry.file) for w in ds.workouts] == [(0, "2025/0.wko4"), (1, "2025/2.wko4")]
    assert ds.excluded[0]["file"] == "2025/1.wko4" and ds.excluded[0]["distance"] == 12.0


def test_source_stamp_changes_with_an_override(tmp_path, tags_db):
    from backend.engine.wko5expr import datasource
    a = datasource.source_stamp("wko5", tmp_path)
    AT.upsert(tags_db, start_local="2025-12-14T01:00", file="2025/1.fit", exclusion="keep")
    assert datasource.source_stamp("wko5", tmp_path) != a


def test_cptest_curves_skip_bad_files(tmp_path, monkeypatch):
    from backend.engine.racepower import cptest as T
    monkeypatch.setenv("WKO5COACH_TZ", "UTC")            # the FIT's local start = its UTC start
    d = tmp_path / "fit" / "coros" / "2025"
    d.mkdir(parents=True)
    (d / "2025-12-14_car.fit").write_bytes(build_run(T0, seconds=1037, speed_m_s=CAR, power=[900] * 1037))
    (d / "2025-12-15_run.fit").write_bytes(build_run(T0 + dt.timedelta(days=1), seconds=900, speed_m_s=3.0,
                                                     power=[220] * 900))
    got = T.curves(tmp_path, dt.date(2025, 12, 1), dt.date(2025, 12, 31), accept_watch=True)
    assert [c["file"] for c in got] == ["2025-12-15_run.fit"]
    bad = T.bad_files(tmp_path, ["coros/2025/2025-12-14_car.fit", "coros/2025/2025-12-15_run.fit"])
    assert list(bad) == ["coros/2025/2025-12-14_car.fit"]
    keep = [{"start_local": "2025-12-13T01:00", "file": None, "exclusion": "keep"}]   # the car's local start
    assert T.bad_files(tmp_path, ["coros/2025/2025-12-14_car.fit"], tags=keep) == {}
    assert T.bad_files(tmp_path, ["coros/2025/2025-12-14_car.fit"], enabled=False) == {}


# ---- API ----------------------------------------------------------------------

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_activity_list_and_exclusions_endpoints(tmp_path, no_plan, monkeypatch):
    from backend.api import wko5views as V
    ds = _ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    lst = V.workouts(begin="2025-12-01", end="2025-12-31", sports=None, parity=None)
    assert [a["start"][:10] for a in lst] == ["2025-12-16", "2025-12-15", "2025-12-14", "2025-12-13"]
    car = next(a for a in lst if a["file"] == "2025/1.fit")
    assert car["index"] is None and car["excluded"]["label"] == "已排除：疑似交通工具／騎車（均速 43 km/h）"
    assert all(a["index"] is not None for a in lst if not a.get("excluded"))
    assert [a["sport"] for a in V.workouts(begin="2025-12-01", end="2025-12-31", sports="bike", parity=None)] == ["bike"]
    r = V.exclusions()
    assert r["enabled"] is True and [x["file"] for x in r["excluded"]] == ["2025/2.fit", "2025/1.fit"]
    assert r["kept"] == []


def test_put_exclusion_and_patch_validation(monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.api import wko5views as V
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    import backend.db.database as D
    from backend.db.models import ActivityTag, Base

    async def _inner():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, expire_on_commit=False)
        monkeypatch.setattr(D, "AsyncSessionLocal", maker)
        r = await V.put_exclusion(V.ExclusionBody(key="2025-12-14T01:00", file="2025/1.fit", exclusion="keep"))
        assert r == {"key": "2025-12-14T01:00", "exclusion": "keep"}
        async with maker() as s:
            row = (await s.execute(select(ActivityTag))).scalar_one()
            assert row.exclusion == "keep" and row.file == "2025/1.fit"
            assert not row.activity_type_overridden                     # nothing else touched
            with pytest.raises(HTTPException) as e:
                await save_activity_tag(s, ActivityUpdate(exclusion="drop"), start_local="2025-12-14T01:00")
            assert e.value.status_code == 400
        await V.put_exclusion(V.ExclusionBody(key="2025-12-14T01:00", file="2025/1.fit", exclusion=None))
        async with maker() as s:
            assert (await s.execute(select(ActivityTag))).scalar_one().exclusion is None   # back to the rule
        with pytest.raises(HTTPException):
            await V.put_exclusion(V.ExclusionBody(key="yesterday", exclusion="keep"))
        await eng.dispose()
    _run(_inner())


def test_tags_load_tolerates_a_table_without_the_column(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE activity_tags (id INTEGER PRIMARY KEY, athlete_id INT, start_local TEXT, source TEXT,"
                " file TEXT, workout_id INT, distance_km REAL, label TEXT, activity_type TEXT,"
                " activity_type_overridden INT, effort TEXT, effort_overridden INT, note TEXT, updated_at TEXT)")
    con.execute("INSERT INTO activity_tags (athlete_id, start_local, activity_type, activity_type_overridden,"
                " effort_overridden) VALUES (1, '2025-12-14T01:00', 'race', 1, 0)")
    con.commit()
    con.close()
    rows = AT.load(db)
    assert rows[0]["activity_type"] == "race" and rows[0]["exclusion"] is None
    AT.upsert(db, start_local="2025-12-14T01:00", exclusion="exclude")             # migrates the column
    assert AT.load(db)[0]["exclusion"] == "exclude"


def test_settings_toggle_key():
    from backend.settings import repository as R
    from backend.api.sync import _SETTING_KEYS
    assert R.DEFAULTS[B.SETTING_KEY] is True
    assert _SETTING_KEYS["exclude_bad_activities"] == B.SETTING_KEY
    R.validate(B.SETTING_KEY, False)
    with pytest.raises(ValueError):
        R.validate(B.SETTING_KEY, "yes")
    assert B.read_setting() is True                                  # no DB in tests: the default
