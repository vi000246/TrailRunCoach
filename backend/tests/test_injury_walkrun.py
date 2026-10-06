"""
SP-272 紅燈之後先走跑交替 (engine/injuries.return_state / walkrun_sessions / walkrun_steps,
overview.walkrun_apply / walkrun_session, reentry.find_all, projection, api/injuries 「可以開始走跑」,
sync/coros_workouts): after a red light a ≥ 30-min walk marked 沒痛／痠 (or the button) starts the
walk / run stages 4/1 → 3/2 → 2/3 → 1/4, then 30 min continuous × 3, a rest day between run days; a 痛
repeats the stage, 中斷 goes back to red, an unmarked run moves on; the Daniels block starts after the
last continuous 30 (the stages are not block days). Illness: never. Synthetic data and tmp SQLite only
(docs/research/injury-graded-return.md §2.4, §4.3, §6.1 point 5).
"""
from __future__ import annotations

import datetime as dt
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.engine import activity_tags as AT
from backend.engine import injuries as INJ
from backend.engine import overview as O
from backend.engine import reentry as RE
from backend.tests.test_injuries import _run, api, ev  # noqa: F401 — api is a fixture
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

N_SESSIONS = len(INJ.WALKRUN) * INJ.WALKRUN_PER_STAGE + INJ.CONT_N      # 15


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    INJ._memo.clear()
    yield
    INJ._memo.clear()


def mk(day, pain, cat="run", minutes=30.0, score=None, eid=1):
    return {"date": day, "key": day + "T07:00", "cat": cat, "minutes": minutes, "pain": pain, "score": score,
            "area": None, "injury_id": eid if pain is not None and pain >= 2 else None}


def days(a: date, n: int, step: int = 2) -> list[str]:
    return [(a + dt.timedelta(days=step * i)).isoformat() for i in range(n)]


# ---------------------------------------------------------------------------
# the state machine
# ---------------------------------------------------------------------------

def test_red_waits_for_the_walk_check_then_the_stages_move():
    e = ev(1, "2026-09-10", side="right")
    m = [mk("2026-09-10", 3)]                                              # 中斷 → red
    st = INJ.return_state(e, m, date(2026, 9, 15))
    assert st["phase"] == "red" and st["episodes"][0]["red"] == "2026-09-10"
    # a 20-min walk or one marked 痛 doesn't start it; a 30-min walk marked 沒痛 does
    m += [mk("2026-09-14", 0, "walk", 20), mk("2026-09-15", 2, "walk", 40), mk("2026-09-16", 1, "walk", 35)]
    st = INJ.return_state(e, m, date(2026, 9, 16))
    assert st["phase"] == "walkrun" and st["stage"] == 0 and st["n"] == 0 and st["episodes"][0]["start"] == "2026-09-16"
    runs = days(date(2026, 9, 17), 5)
    st = INJ.return_state(e, m + [mk(d, 0) for d in runs], date(2026, 9, 26))
    assert st["phase"] == "walkrun" and st["stage"] == 1 and st["n"] == 2 and st["last_run"] == runs[-1]


def test_pain_repeats_the_stage_stop_goes_back_to_red_unmarked_moves_on():
    e = ev(1, "2026-09-10")
    base = [mk("2026-09-10", 3), mk("2026-09-11", 0, "walk", 30)]
    r = days(date(2026, 9, 12), 3)
    st = INJ.return_state(e, base + [mk(r[0], 1), mk(r[1], 2), mk(r[2], None)], date(2026, 9, 20))
    assert st["phase"] == "walkrun" and st["stage"] == 0 and st["n"] == 2      # the 痛 one didn't count
    st = INJ.return_state(e, base + [mk(r[0], 1), mk(r[1], 3)], date(2026, 9, 20))
    assert st["phase"] == "red" and len(st["episodes"]) == 2 and st["episodes"][1]["red"] == r[1]
    st = INJ.return_state(e, base + [mk(d, None) for d in days(date(2026, 9, 12), 4)], date(2026, 9, 25))
    assert st["stage"] == 1 and st["n"] == 1                                      # unmarked = 沒痛


def test_all_stages_then_back_to_the_light():
    e = ev(1, "2026-09-10")
    r = days(date(2026, 9, 12), N_SESSIONS)
    m = [mk("2026-09-10", 3), mk("2026-09-11", 0, "walk", 30)] + [mk(d, 0) for d in r]
    st = INJ.return_state(e, m, date(2026, 10, 20))
    assert st["phase"] == "light" and st["episodes"][0]["done"] == r[-1]
    assert st["since"] == (date.fromisoformat(r[-1]) + dt.timedelta(days=1)).isoformat()
    lt = INJ.light([e], m, date(2026, 10, 20))
    assert lt["color"] == "green"


def test_severe_starts_red_and_the_button_starts_the_walk_run():
    e = {**ev(1, "2026-09-10", severity="severe")}
    assert INJ.return_state(e, [], date(2026, 9, 20))["phase"] == "red"
    assert INJ.light([e], [], date(2026, 9, 20))["color"] == "red"
    e["walkrun_from"] = "2026-09-18"
    st = INJ.return_state(e, [mk("2026-09-19", 0)], date(2026, 9, 20))
    assert st["phase"] == "walkrun" and st["n"] == 1
    lt = INJ.light([e], [mk("2026-09-19", 0)], date(2026, 9, 20))
    assert lt["color"] == "walkrun" and lt["walkrun"]["stage"] == 0


def test_illness_never_takes_the_walk_run():
    ill = {**ev(1, "2026-09-10", severity="severe"), "category": "illness", "illness": "fever"}
    assert INJ.return_state(ill, [mk("2026-09-10", 3)], date(2026, 9, 20)) is None
    assert INJ.light([ill], [], date(2026, 9, 20)) is None


def test_sessions_and_steps():
    left = INJ.walkrun_sessions({"stage": 0, "n": 1})
    assert len(left) == N_SESSIONS - 1 and left[0]["walk"] == 4 and left[0]["run"] == 1
    assert [x["run"] for x in left[-3:]] == [30, 30, 30] and all(x["walk"] == 0 for x in left[-3:])
    doc = INJ.walkrun_steps(left[0])
    rep = doc["items"][0]
    assert rep["kind"] == "repeat" and rep["times"] == INJ.WALKRUN_REPS
    assert [(c["kind"], c["dur"]["value"]) for c in rep["items"]] == [("rest", 240), ("work", 60)]


def test_coros_payload_alternates_walk_and_run():
    from backend.sync import coros_workouts as CW
    s = O.walkrun_session(INJ.walkrun_sessions({"stage": 1, "n": 0})[0], "2099-01-05")
    spec = CW.session_workout(s, {"lthr": 170, "aet": 145, "cp": 250})
    ex = spec.payload["exercises"]
    grp = [x for x in ex if x.get("isGroup")]
    assert len(grp) == 1 and grp[0]["sets"] == INJ.WALKRUN_REPS
    kids = [x for x in ex if x.get("groupId") == str(grp[0]["id"])]
    assert len(kids) == 2 and kids[0]["targetValue"] == 180 and kids[1]["targetValue"] == 120
    assert "走" in kids[0]["name"] and "跑" in kids[1]["name"]
    assert spec.payload["estimatedTime"] == 30 * 60


# ---------------------------------------------------------------------------
# the week plan
# ---------------------------------------------------------------------------

def _s(id, kind, day, minutes, **kw):
    return O.Session(id=id, kind=kind, title=kw.pop("title", id), minutes=minutes, day=day, tss=minutes * 0.8, **kw)


def test_walkrun_week_every_other_day_strength_kept_blackouts_skipped():
    e = ev(1, "2026-09-10")
    m = [mk("2026-09-10", 3), mk("2026-09-27", 0, "walk", 30), mk("2026-09-29", 0)]   # 1 session done Tue
    today = date(2026, 9, 30)
    lt = INJ.light([e], m, today)
    assert lt["color"] == "walkrun"
    ss = [_s("q", "quality", "2026-09-30", 60), _s("strength1", "strength", "2026-10-01", 35),
          _s("easy2", "easy", "2026-10-02", 50), _s("long", "long", "2026-10-04", 120),
          _s("done", "easy", "2026-09-29", 30, done=True)]
    notes: list = []
    out = O.walkrun_apply(ss, lt, today, blocked={"2026-10-03"}, notes=notes)
    wr = [s for s in out if s.id.startswith("walkrun")]
    assert [s.day for s in wr] == ["2026-10-01", "2026-10-04"]           # rest day after Tue; Sat blocked
    assert all(s.steps and s.kind == "easy" for s in wr) and "第 1 階 2/3" in wr[0].title
    assert not any(s.id in ("q", "easy2", "long") for s in out) and any(s.id == "strength1" for s in out)
    assert lt["walkrun_left"] and lt["walkrun_next"] == "2026-10-06"
    assert any("走跑階段不算進恢復期" in n["text"] for n in notes)


def test_red_week_says_how_to_start():
    e = ev(1, "2026-09-10")
    lt = INJ.light([e], [mk("2026-09-28", 3)], date(2026, 9, 30))
    notes: list = []
    O.light_apply([_s("easy2", "easy", "2026-10-02", 50)], lt, date(2026, 9, 30), 200.0, 0.0, notes)
    assert any("可以開始走跑" in n["text"] for n in notes)


# ---------------------------------------------------------------------------
# the Daniels block after the walk-run (10 days off with a knee)
# ---------------------------------------------------------------------------

def _ds_and_tags(today: date, n_runs: int):
    """45-min runs every day June → 9/10 (9/10 marked 中斷); off 9/11–9/20; a 40-min walk 9/20 marked
    沒痛; then `n_runs` walk-run runs every other day from 9/21."""
    ws, tags = [], []
    d = date(2026, 6, 1)
    while d <= date(2026, 9, 10):
        ws.append(FakeWorkout(start=dt.datetime(d.year, d.month, d.day, 7), sport="run",
                              metrics={"duration": 2700.0, "distance": 7.5, "tss": 40.0}))
        d += dt.timedelta(days=1)
    tags.append({"start_local": "2026-09-10T07:00", "file": None, "pain": 3, "pain_area": "knee", "injury_id": 1})
    ws.append(FakeWorkout(start=dt.datetime(2026, 9, 20, 9), sport="walk", metrics={"duration": 2400.0}))
    tags.append({"start_local": "2026-09-20T09:00", "file": None, "pain": 0, "pain_area": None, "injury_id": None})
    for x in days(date(2026, 9, 21), n_runs):
        y = date.fromisoformat(x)
        ws.append(FakeWorkout(start=dt.datetime(y.year, y.month, y.day, 7), sport="run",
                              metrics={"duration": 1800.0, "distance": 4.0, "tss": 20.0}))
    return FakeDataset(ws, today), tags


def test_ten_days_off_knee_walk_check_stages_then_the_block(monkeypatch):
    e = ev(1, "2026-09-10", side="right")
    today = date(2026, 10, 25)
    ds, tags = _ds_and_tags(today, N_SESSIONS)
    monkeypatch.setattr(AT, "load", lambda *a, **k: tags)
    p = RE.find(ds, today, injuries=[e], step_up=False)
    done = date(2026, 9, 21) + dt.timedelta(days=2 * (N_SESSIONS - 1))
    assert p["walkrun"]["done"] == done.isoformat() and p["walkrun"]["start"] == "2026-09-20"
    assert p["return"] == (done + dt.timedelta(days=1)).isoformat()         # the stages are not block days
    assert p["days"] == 10 and p["category"] == "6-13"
    assert [f for _a, _b, f in p["segments"]] == [0.5, 0.75]
    assert "傷停 10 天" in p["text"]


def test_no_block_while_in_the_walk_run_and_none_for_old_resolved(monkeypatch):
    e = ev(1, "2026-09-10")
    today = date(2026, 9, 30)
    ds, tags = _ds_and_tags(today, 5)
    monkeypatch.setattr(AT, "load", lambda *a, **k: tags)
    assert RE.find(ds, today, injuries=[e], step_up=False) is None              # open, still in the stages
    # a resolved event that never went through the stages keeps the old behaviour (block from the first run)
    old = ev(1, "2026-09-10", status="resolved", resolved="2026-09-25")
    p = RE.find(ds, today, injuries=[old], step_up=False)
    assert p and p["return"] == "2026-09-21" and "walkrun" not in p


def test_illness_layoff_has_no_walk_run(monkeypatch):
    today = date(2026, 9, 30)
    ds, tags = _ds_and_tags(today, 5)
    monkeypatch.setattr(AT, "load", lambda *a, **k: tags)
    ill = {**ev(1, "2026-09-11", status="resolved", resolved="2026-09-19"), "category": "illness", "illness": "fever"}
    p = RE.find(ds, today, injuries=[ill], step_up=False)
    assert p and p["return"] == "2026-09-21" and "walkrun" not in p


# ---------------------------------------------------------------------------
# the button and the DB
# ---------------------------------------------------------------------------

def test_api_walkrun_button(api):  # noqa: F811
    m = api.get("/api/v1/wko5/injuries/meta").json()
    assert m["labels"]["walkrun_button"] == "可以開始走跑" and "Ohio State Wexner" in m["labels"]["walkrun_help"]
    e = api.post("/api/v1/wko5/injuries", json={"area": "knee", "severity": "severe", "onset_date": "2026-09-10"}).json()
    r = api.post(f"/api/v1/wko5/injuries/{e['id']}/walkrun", json={"date": "2026-09-20"})
    assert r.status_code == 200 and r.json()["walkrun_from"] == "2026-09-20"
    assert api.post(f"/api/v1/wko5/injuries/{e['id']}/walkrun", json={"date": "2026-09-01"}).status_code == 400
    ill = api.post("/api/v1/wko5/injuries", json={"category": "illness", "illness": "cold",
                                                  "onset_date": "2026-09-20"}).json()
    assert api.post(f"/api/v1/wko5/injuries/{ill['id']}/walkrun").status_code == 400


def test_migration_adds_walkrun_from():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        import backend.db.database as db_mod
        from backend.db.models import Base
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE injury_events (id INTEGER PRIMARY KEY, athlete_id INTEGER, "
                                    "area TEXT, onset_date TEXT, status TEXT)"))
            await conn.execute(text("INSERT INTO injury_events (athlete_id, area, onset_date, status) "
                                    "VALUES (1, 'knee', '2026-01-01', 'active')"))
            await conn.run_sync(Base.metadata.create_all)
        orig = db_mod.engine
        db_mod.engine = engine
        try:
            await db_mod._migrate_schema()
            await db_mod._migrate_schema()                       # idempotent
            async with engine.begin() as conn:
                row = (await conn.execute(text("SELECT area, walkrun_from, condition FROM injury_events"))).one()
        finally:
            db_mod.engine = orig
        assert tuple(row) == ("knee", None, None)
    _run(_inner())


def test_week_plan_and_projection_in_the_walk_run(monkeypatch):
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as P
    from backend.engine.status import Status
    from backend.tests.test_b2b import _phases
    from backend.tests.test_quality_gate import TODAY
    from backend.tests.test_recovery_week import LIGHT, _week
    ds, plan, _ = _week("2026-11-28", {4: LIGHT})
    e = {**ev(1, "2026-09-20", severity="severe"), "walkrun_from": "2026-09-29"}
    monkeypatch.setattr(INJ, "load_events", lambda *a, **k: [e])
    monkeypatch.setattr(AT, "load", lambda *a, **k: [])
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    assert wp["injury_light"]["color"] == "walkrun"
    left = [s for s in wp["sessions"] if not s["done"] and s["day"] and s["day"] >= TODAY.isoformat()
            and s["kind"] in O.RUN_KINDS]
    assert left and all(s["id"].startswith("walkrun") and s["steps"] for s in left)
    ds_ = [date.fromisoformat(s["day"]) for s in left]
    assert all((b - a).days >= 2 for a, b in zip(ds_, ds_[1:]))
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 28), events=plan.events)
    nxt = [s for s in weeks[0]["sessions"] if s["kind"] in O.RUN_KINDS]
    assert nxt and all(s["id"].startswith("walkrun") for s in nxt)
