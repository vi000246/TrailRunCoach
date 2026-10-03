"""Activity tags (engine/activity_tags.py): auto rules, the user-override
store, the API, capacity-sample gating, the seed and the trail HR model.
Never touches the user's DB: tmp / in-memory DBs only (conftest also blocks
the default DB path)."""
import asyncio
import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.engine import activity_tags as AT


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---- auto rules ---------------------------------------------------------------

def test_effort_hr_rules():
    lthr, aet = 160.0, 142.0
    base = {"hr_avg": 152.0, "above_aet": 0.8, "low_share": 0.2, "moving_s": 7200.0, "elapsed_s": 7500.0,
            "rest_share": 0.03}
    assert AT.effort_hr(base, lthr, aet)["effort"] == "max"
    # same HR, long rests → 有拼但有休息
    assert AT.effort_hr({**base, "rest_share": 0.14}, lthr, aet)["effort"] == "hard_with_rests"
    # HR below 0.90 × LTHR → not max
    assert AT.effort_hr({**base, "hr_avg": 0.89 * lthr}, lthr, aet)["effort"] == "moderate"
    # above-AeT share below 2/3 → moderate (racing by feel)
    assert AT.effort_hr({**base, "above_aet": 0.61}, lthr, aet)["effort"] == "moderate"
    easy = {**base, "hr_avg": 135.0, "low_share": 0.7, "above_aet": 0.3}
    assert AT.effort_hr(easy, lthr, aet)["effort"] == "easy"
    assert AT.effort_hr({**base, "hr_avg": None}, lthr, aet)["effort"] == "moderate"


def test_rest_spells_counts_only_long_stops_and_gaps():
    t = np.arange(0, 3600.0, 1.0)
    mv = np.ones(len(t), bool)
    mv[100:160] = False                       # a 1-min aid station: not a long rest
    mv[1000:1400] = False                     # a 400-s stop: a long rest
    r = AT.rest_spells(t, mv)
    assert r["rest_s"] == pytest.approx(400.0, abs=2)
    assert r["stopped_s"] == pytest.approx(460.0, abs=3)
    # a recording gap (auto-pause) of 10 min counts as a long rest
    t2 = np.concatenate([np.arange(0, 1800.0), np.arange(2400.0, 4200.0)])
    r2 = AT.rest_spells(t2, np.ones(len(t2), bool))
    assert r2["rest_s"] == pytest.approx(601.0, abs=2)
    assert r2["rest_share"] == pytest.approx(601.0 / 4199.0, rel=0.01)


def test_auto_type_order():
    assert AT.auto_type(plan_race={"name": "東眼山"}, test="CP", sport="run")[0] == "race"
    assert AT.auto_type(test="CP 測試", sport="run", title="越野賽")[0] == "test"
    assert AT.auto_type(sport="run", title="五寮尖越野賽")[0] == "race"
    assert AT.auto_type(sport="walk", sport_type="hiking", baiyue_event="玉山")[0] == "baiyue_group"
    assert AT.auto_type(sport="walk", sport_type="mountaineering")[0] == "hike"
    assert AT.auto_type(sport="run", trail=True, title="郊山爬山")[0] == "hike"
    assert AT.auto_type(sport="run", title="Trail Running", trail=True)[0] == "training"
    assert AT.auto_type(sport="strength")[0] == "other"


def test_merge_user_wins_and_none_means_auto():
    auto = {"activity_type": "race", "activity_type_reason": "plan", "effort": "max", "effort_reason": "hr"}
    m = AT.merge(auto, None)
    assert (m["activity_type"], m["effort"], m["effort_overridden"]) == ("race", "max", False)
    u = {"activity_type": None, "activity_type_overridden": False, "effort": "moderate", "effort_overridden": True,
         "note": "by feel", "start_local": "2025-09-06T07:58"}
    m = AT.merge(auto, u)
    assert m["effort"] == "moderate" and m["effort_overridden"] and m["effort_auto"] == "max"
    assert m["activity_type"] == "race" and not m["activity_type_overridden"] and m["note"] == "by feel"


# ---- store --------------------------------------------------------------------

def test_store_upsert_find_and_clear(tmp_path):
    db = tmp_path / "t.db"
    AT.upsert(db, start_local="2025-05-17T09:36", file="2025/Example_2025_05_17_09_36.wko4", source="wko5",
              activity_type="hike", effort="hard_with_rests")
    AT.upsert(db, start_local="2025-05-17T09:36", note="中級山")        # same key: idempotent update
    rows = AT.load(db)
    assert len(rows) == 1 and rows[0]["effort_overridden"] and rows[0]["note"] == "中級山"
    st = dt.datetime(2025, 5, 17, 9, 36, 40)
    assert AT.find(rows, st, "2025/Example_2025_05_17_09_36.wko4")["activity_type"] == "hike"
    assert AT.find(rows, st, "other.fit") is not None                    # by start minute
    assert AT.find(rows, st + dt.timedelta(minutes=2), "x.fit") is not None   # ±3 min (another source)
    assert AT.find(rows, st + dt.timedelta(minutes=9), "x.fit") is None
    AT.upsert(db, start_local="2025-05-17T09:36", effort=None)           # back to auto
    r = AT.load(db)[0]
    assert not r["effort_overridden"] and r["effort"] is None and r["activity_type"] == "hike"
    with pytest.raises(ValueError):
        AT.upsert(db, start_local="2025-05-17T09:36", effort="all_out")


def test_default_db_is_blocked_in_tests():
    assert AT.load() == []


def test_migration_creates_activity_tags_table():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from backend.db.models import Base
        import backend.db.database as db_mod
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        orig = db_mod.engine
        db_mod.engine = engine
        try:
            await db_mod._migrate_schema()
            async with engine.begin() as conn:
                cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(activity_tags)"))).fetchall()}
        finally:
            db_mod.engine = orig
        assert {"start_local", "file", "activity_type", "activity_type_overridden", "effort",
                "effort_overridden", "note"} <= cols
    _run(_inner())


# ---- API (workout_files rows) -------------------------------------------------

async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, WorkoutFile
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = async_sessionmaker(engine, expire_on_commit=False)()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    s.add(WorkoutFile(id=10, athlete_id=1, file_path="/r.fit", file_format="fit", sport="running",
                      total_distance_m=5000, start_time_utc=dt.datetime(2025, 10, 18, 12, 54),
                      workout_date=dt.date(2025, 10, 18), trail_classification="road"))
    s.add(WorkoutFile(id=11, athlete_id=1, file_path="/n.fit", file_format="fit", sport="running"))
    await s.commit()
    return s


def test_patch_activity_sets_user_values_and_list_shows_them():
    async def _inner():
        from backend.api.workouts import update_activity, ActivityUpdate, list_workouts, get_workout
        s = await _session()
        r = await update_activity(10, ActivityUpdate(activity_type="training", effort="moderate", note="平日"), s)
        assert r["activity_type"] == "training" and r["activity_type_overridden"]
        assert r["effort"] == "moderate" and r["effort_overridden"] and r["note"] == "平日"
        r = await update_activity(10, ActivityUpdate(effort=None), s)          # null → back to auto
        assert r["effort"] is None and not r["effort_overridden"] and r["activity_type"] == "training"
        lst = await list_workouts(athlete_id=1, page=1, per_page=20, sport=None, date_from=None, date_to=None, db=s)
        it = next(x for x in lst["items"] if x["id"] == 10)
        assert it["activity"]["activity_type"] == "training" and it["trail_classification"] == "road"
        det = await get_workout(10, s)
        assert det["activity"]["note"] == "平日"
    _run(_inner())


def test_patch_activity_errors():
    async def _inner():
        from backend.api.workouts import update_activity, ActivityUpdate
        s = await _session()
        with pytest.raises(HTTPException) as e:
            await update_activity(10, ActivityUpdate(effort="all_out"), s)
        assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await update_activity(999, ActivityUpdate(effort="max"), s)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await update_activity(11, ActivityUpdate(effort="max"), s)           # no start time
        assert e.value.status_code == 422
    _run(_inner())


# ---- capacity samples: the user's effort mark wins ------------------------------

def _fake_capacity_ds(monkeypatch, st, ms):
    from backend.engine.racepower import athlete as A
    w = SimpleNamespace(idx=0, sport="run", sport_type="trail running", tags=["runningtrail"], day=100.0,
                        metrics={"distance": 14.4, "climbing": 1400.0},
                        entry=SimpleNamespace(start=dt.datetime(2025, 4, 12, 10, 32), file="2025/x.wko4",
                                              title="Trail Running"))
    ds = SimpleNamespace(workouts=[w], plan=SimpleNamespace(events=[], thresholds=[]), flush_series=lambda: None)
    monkeypatch.setattr(A, "plan_race_runs", lambda ds: {})
    monkeypatch.setattr(A, "intensity_stats", lambda ds, w: st)
    monkeypatch.setattr(A, "maximal_stats", lambda ds, w: ms)
    monkeypatch.setattr(A, "thresholds_as_of", lambda ds, d: {"lthr": 160.0, "aet": 142.0})
    return ds, w


def _hist(bpm, secs):
    h = [0.0] * 181
    h[int(bpm) - 40] = secs
    return h


def test_capacity_sample_needs_max_effort_and_user_mark_wins(monkeypatch):
    from backend.engine.racepower import athlete as A
    st = {"moving_s": 13900.0, "hr_avg": 152.0, "hist": _hist(152, 13900.0), "hist_lo": 40, "hr_s": 13900.0}
    # long rests 14 % → auto 有拼但有休息 → not a sample
    ds, w = _fake_capacity_ds(monkeypatch, st, {"elapsed_s": 20000.0, "rest_share": 0.14})
    c = A.capacity_samples(ds, [w], tags=[])[0]
    assert c["effort"]["effort"] == "hard_with_rests" and not c["ok"]
    # no long rests → auto 全力 → a sample
    ds, w = _fake_capacity_ds(monkeypatch, st, {"elapsed_s": 14500.0, "rest_share": 0.02})
    assert A.capacity_samples(ds, [w], tags=[])[0]["ok"]
    # the user's 一般 always excludes it, even though the auto rule says 全力
    tag = {"start_local": "2025-04-12T10:32", "file": "2025/x.wko4", "activity_type": "hike",
           "activity_type_overridden": True, "effort": "moderate", "effort_overridden": True}
    c = A.capacity_samples(ds, [w], tags=[tag])[0]
    assert not c["ok"] and c["kind"] == "user" and c["tags"]["activity_type"] == "hike"
    # the user's 全力 always includes it, even when the auto rule disagrees
    ds, w = _fake_capacity_ds(monkeypatch, st, {"elapsed_s": 20000.0, "rest_share": 0.14})
    c = A.capacity_samples(ds, [w], tags=[{**tag, "effort": "max"}])[0]
    assert c["ok"] and c["kind"] == "user"
    # a test mark goes through the CP-test path
    c = A.capacity_samples(ds, [w], tags=[{**tag, "activity_type": "test", "effort": "max"}])[0]
    assert not c["ok"] and c["kind"] == "test"


# ---- seed -----------------------------------------------------------------------

def _wk(idx, start, km, trail, file):
    return SimpleNamespace(idx=idx, sport="run", sport_type="trail running" if trail else "running",
                           tags=["runningtrail"] if trail else [], metrics={"distance": km},
                           entry=SimpleNamespace(start=start, file=file))


def test_seed_matches_by_date_distance_and_file_and_is_idempotent(tmp_path):
    from backend.scripts import seed_activity_tags as SD
    ws = [_wk(0, dt.datetime(2025, 8, 9, 20, 54), 5.02, False, "2025/Example_2025_08_09_20_54.wko4"),
          _wk(1, dt.datetime(2025, 4, 12, 10, 29), 0.1, True, "2025/a.wko4"),
          _wk(2, dt.datetime(2025, 4, 12, 10, 32), 14.4, True, "2025/b.wko4"),
          _wk(3, dt.datetime(2024, 5, 11, 5, 55), 13.8, True, "2024/Example_2024_05_11_05_55.wko4")]
    db = tmp_path / "seed.db"
    items = SD.plan(ws, AT.load(db), seed=SD.EXAMPLE_SEED)
    by = {it["spec"]["date"]: it for it in items if it["found"]}
    assert by["2025-04-12"]["file"] == "2025/b.wko4"                  # the 14.4 km run, not the 0.1 km stub
    assert by["2024-05-11"]["how"] == "file" and by["2024-05-11"]["want"] == {"activity_type": "race"}
    assert sum(1 for it in items if not it["found"]) == len(SD.EXAMPLE_SEED) - 3
    assert SD.apply(db, items) == 3
    rows = AT.load(db)
    r = AT.find(rows, ws[3].entry.start, ws[3].entry.file)
    assert r["activity_type"] == "race" and not r["effort_overridden"]   # effort stays auto
    again = SD.plan(ws, rows, seed=SD.EXAMPLE_SEED)
    assert all(not it.get("change") for it in again if it["found"])      # idempotent
    assert SD.apply(db, again) == 0


def test_seed_path_argument_then_env_then_default(tmp_path, monkeypatch):
    from backend.scripts import seed_activity_tags as SD
    monkeypatch.delenv(SD.SEED_ENV, raising=False)
    assert SD.seed_path() == SD.DEFAULT_SEED
    monkeypatch.setenv(SD.SEED_ENV, str(tmp_path / "env.json"))
    assert SD.seed_path() == tmp_path / "env.json"
    assert SD.seed_path(str(tmp_path / "arg.json")) == tmp_path / "arg.json"
    assert SD.main(["--seed", str(tmp_path / "missing.json")]) == 2   # no file: nothing read or written


# ---- trail HR pace model -----------------------------------------------------------

def test_trailhr_fit_and_predict():
    from backend.engine.racepower import trailhr as TH
    assert TH.dbar(0.8, 0.1) == 1.0
    assert TH.dbar(3.0, 0.1) == pytest.approx(1 - 0.1 * 4 / 6)
    # runs on a known line v0 = 2 + 4x with 5 %/h durability
    pts = []
    for i, x in enumerate(np.linspace(0.75, 1.0, 8)):
        T = 1.0 + 0.3 * i
        v = (2.0 + 4.0 * x) * TH.dbar(T, 0.05)
        pts.append({"x": float(x), "v": v, "T_h": T, "eff_km": v * T})
    m = TH.fit(pts, 0.05)
    assert m["kind"] == "ols" and m["a"] == pytest.approx(2.0, abs=1e-6) and m["b"] == pytest.approx(4.0, abs=1e-6)
    # predict reproduces a run, and durability makes a long race slower
    p = pts[-1]
    assert TH.predict_time(m, p["eff_km"], p["x"]) == pytest.approx(p["T_h"] * 3600, rel=1e-4)
    assert TH.predict_time(m, 30.0, 0.9) > TH.predict_time(m, 30.0, 0.9, delta=0.0)
    few = TH.fit(pts[:3], None)
    assert few["kind"] == "proportional" and TH.v0_at(few, 0.9) == pytest.approx(few["c"] * 0.9)
    assert TH.race_level([]) [0] == 0.90 and TH.race_level([0.95, 1.0, 1.05])[0] == 1.0


def test_durability_delta_reads_the_decline_after_one_hour():
    from backend.engine.racepower import trailhr as TH
    hours = np.linspace(0.3, 3.0, 200)
    pct = np.where(hours < 1.0, 100.0, 100.0 - 6.0 * (hours - 1.0))
    assert TH.durability_delta(np.column_stack([hours, pct]).tolist()) == pytest.approx(0.06, rel=1e-3)
    assert TH.durability_delta([[0.5, 100.0]]) is None


def test_planner_trail_hr_estimate():
    from backend.engine.racepower import planner as PL
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9}
    e = PL.trail_hr_estimate(m, 12.0, 700.0, 1.0)
    from backend.engine.algorithms.effort import divisor_of
    assert e["eff_km"] == pytest.approx(12.0 + 700.0 / divisor_of("fitted_run"))   # per athlete (ITRA 100 here)
    assert e["time_s"] > e["time_no_durability_s"] > 0
    assert PL.trail_hr_estimate(m, 12.0, 700.0, 0.9)["time_s"] > e["time_s"]   # easier effort → slower
    assert PL.trail_hr_estimate(None, 12.0, 700.0) is None
