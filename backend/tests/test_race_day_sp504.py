"""
SP-504: a race day holds no generated session. A B / C race 「以賽代訓」: the week's rules stay (a B race's
mini-taper and its day cleared, a C race in place of one quality session or the long run), but the
generator no longer puts a 「B 賽事：X」 card on the day (post_race._race_session, SP-95's extra). The race
is a mark on the day instead (like the A race's phase label): `races` of week_plan / each projected week,
its predicted TSS a separate field that still counts in the week's planned load and the projected CTL.
The 課表 calendar shows the marks (`races`), counts their load in the week's 計畫, and an activity on a race
day is the race (`race` on the activity), not an unplanned run. The 賽事計算機's 「匯出至課表」 adds its
own race session (no generator row to claim any more). Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import plan_store as PS
from backend.engine import post_race as PR
from backend.engine import projection as PJ
from backend.engine import reconcile as R
from backend.engine.planning import Event
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30

RACE_WEEK = date(2026, 10, 5)                                # Mon: the projected week after TODAY's
RACE_DAY = date(2026, 10, 10)                                # Sat


def _plan(today: date, *events):
    ds = _history(today)
    plan = _plan_with("2027-06-01", 1, today)
    plan.events = list(plan.events) + list(events)
    ds.plan = plan
    st = Status(ds, plan, today, prefs=PP.Prefs()).compute()
    return ds, plan, O.week_plan(ds, st, today)


def _race(prio: str, day: date = RACE_DAY, eid: str = "e1", hours: float = 5.0) -> Event:
    return Event(eid, "北插天山", day.isoformat(), kind="race", priority=prio, distance_km=30, climbing_m=2000,
                 est_hours=hours)


def s(id_, kind, day, minutes=50, **kw):
    return {"id": id_, "kind": kind, "day": day, "minutes": minutes, "title": "x", "tss": 50.0, "done": False,
            "detail": "", "source": "", "terrain": None, **kw}


# ---- the engine: no race session, a race mark ---------------------------------------------------

@pytest.mark.parametrize("prio", ["B", "C"])
def test_bc_apply_puts_no_session_on_the_race_day(prio):
    ss = [s("long", "long", "2026-10-11", 120), s("quality", "quality", "2026-10-07", 60),
          s("easy1", "easy", "2026-10-10"), s("easy2", "easy", "2026-10-08")]
    marks: list = []
    out = PR.bc_apply(ss, [_race(prio)], RACE_WEEK, [], rate=60.0, races=marks)
    assert not [x for x in out if x["kind"] == "race"]
    assert not [x for x in out if x["day"] == "2026-10-10"]
    assert marks == [{"id": "e1", "name": "北插天山", "priority": prio, "day": "2026-10-10", "end": "2026-10-10",
                      "minutes": 300, "tss": 300.0, "done": False}]
    if prio == "C":                                  # 以賽代訓: one quality session or the long run out
        assert len([x for x in out if x["kind"] in ("long", "quality")]) == 1


def test_bc_apply_without_a_marks_list_still_applies_the_rules():
    out = PR.bc_apply([s("easy1", "easy", "2026-10-10"), s("easy2", "easy", "2026-10-08")], [_race("B")], RACE_WEEK)
    assert [x["day"] for x in out] == ["2026-10-08"]


@pytest.mark.parametrize("prio", ["B", "C"])
def test_week_plan_race_week_has_a_mark_and_no_race_card(prio):
    """Today the race week's Monday: no `kind race` session; the mark with its predicted load, and the
    projection to Sunday reaches the race day with it."""
    _ds, _plan_, wp = _plan(RACE_WEEK, _race(prio))
    assert not [x for x in wp["sessions"] if x["kind"] == "race"]
    assert not [x for x in wp["sessions"] if x["day"] == RACE_DAY.isoformat() and not x["done"]]
    m = wp["races"]
    assert [(x["day"], x["priority"], x["minutes"]) for x in m] == [(RACE_DAY.isoformat(), prio, 300)]
    assert m[0]["tss"] == pytest.approx(300 * (wp["tss_per_category"]["trail"] or 60.0) / 60.0, abs=0.1)
    # the projection to Sunday: the race day's load is the race's
    rows = {r["date"]: r for r in wp["projection"]}
    prev = rows[(RACE_DAY - dt.timedelta(days=1)).isoformat()]
    on = rows[RACE_DAY.isoformat()]
    assert on["atl"] > prev["atl"] + 20                # 300 TSS in a 7-day ATL: a big step up


@pytest.mark.parametrize("prio", ["B", "C"])
def test_projected_race_week_counts_the_race_load(prio):
    """The projected week: no race session, the mark in `races`, the week's TSS = its sessions + the race."""
    _ds, plan, wp = _plan(TODAY, _race(prio))
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), RACE_DAY + dt.timedelta(days=14), events=plan.events)
    wk = next(w for w in weeks if w["start"] == RACE_WEEK.isoformat())
    assert not [x for x in wk["sessions"] if x["kind"] == "race"]
    assert not [x for x in wk["sessions"] if x["day"] == RACE_DAY.isoformat()]
    m = wk["races"]
    assert [(x["day"], x["priority"], x["minutes"]) for x in m] == [(RACE_DAY.isoformat(), prio, 300)]
    assert m[0]["tss"] > 200
    assert wk["tss"] == pytest.approx(sum(x["tss"] for x in wk["sessions"]) + m[0]["tss"])


def test_a_race_week_has_no_race_card_either():
    _ds, _plan_, wp = _plan(RACE_WEEK, _race("A"))
    assert not [x for x in wp["sessions"] if x["kind"] == "race"]
    assert wp.get("races") == []                    # the A race: the phase label on the day (比賽)


def test_week_plan_leaves_the_race_activity_to_the_race(monkeypatch):
    """The race run (Sat) is not taken as the week's long run done: it is the race (plan_match pass 2 would
    otherwise move the stored long run onto it)."""
    from backend.tests import wko5_fakes as WF
    from backend.tests.test_workout_review import _run
    real = WF.FakeDataset
    race_run = _run(RACE_DAY, minutes=300, power=180.0)
    race_run.metrics["tss"] = 300.0
    monkeypatch.setattr(WF, "FakeDataset", lambda ws, t, **kw: real(list(ws) + [race_run], t, **kw))
    today = RACE_DAY + dt.timedelta(days=1)          # Sun: the race was run yesterday
    ds = _history(today)
    plan = _plan_with("2027-06-01", 1, today)
    plan.events = list(plan.events) + [_race("B")]
    ds.plan = plan
    st = Status(ds, plan, today, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, today)
    assert not [x for x in wp["sessions"] if x.get("done") and x["day"] == RACE_DAY.isoformat()]
    assert wp["races"][0]["done"] is True


# ---- stored rows: reconcile and the 賽事計算機's export ---------------------------------------------

def _row(key, kind, day, **kw):
    return {"uid": f"u-{key}-{day}", "week_start": R.monday_of(day), "gen_key": key, "day": day,
            "kind": kind, "title": "B 賽事：北插天山", "minutes": 0, "target": "", "detail": "比賽本身算這週的重點課",
            "source": "", "tss": 0.0, "origin": "auto", "edited": False, "provisional": False,
            "state": "active", "done_by": None, "note": None, **kw}


def test_reconcile_removes_only_the_future_unedited_generated_race():
    """The stored race rows of the old generator: a future, unedited auto one goes; a past one, an edited
    one and the calculator's export (ext_key) stay."""
    gen = [{"start": "2026-10-12", "mode": "base", "provisional": False, "sessions": []},
           {"start": "2026-09-28", "mode": "base", "provisional": False, "sessions": []}]
    stored = [_row("race", "race", "2026-10-18"),                                      # future, auto: goes
              _row("race", "race", "2026-09-29", state="done",
                   done_by={"index": 1, "date": "2026-09-29"}),                           # past: history
              ]
    out, ch = R.reconcile(stored, gen, [], "2026-09-30", None)
    assert [x["day"] for x in out] == ["2026-09-29"]
    assert [c["action"] for c in ch] == ["removed"]
    kept = [_row("race", "race", "2026-10-17", edited=True),
            _row("race", "race", "2026-10-18", uid="u-ext", origin="custom", gen_key=None, edited=True,
                 ext_key="racecalc:e1")]
    out, ch = R.reconcile(kept, gen, [], "2026-09-30", None)
    assert sorted(x["day"] for x in out) == ["2026-10-17", "2026-10-18"] and not ch


def test_export_adds_its_own_race_and_pushes_it():
    """「匯出至課表」 adds a row of its own (no generator race to claim); the old generator row of that day
    goes on the next reconcile, the exported one stays and is pushed to the watch."""
    from backend.tests.test_coros_workouts import run
    from backend.tests.test_plan_store import KEY, inputs, make_db, race_data, race_week, races
    db = run(make_db())
    run(PS.plan_reconcile(db, inputs(weeks=[race_week()]), apply=True))           # the old generator's row
    old = races(run(PS.load(db)))[0]
    r = run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))
    assert r["action"] == "add" and r["session"]["uid"] != old["uid"] and r["session"]["origin"] == "custom"
    run(PS.plan_reconcile(db, inputs(weeks=[race_week(with_race=False)]), apply=True))
    rs = races(run(PS.load(db)))
    assert [x["uid"] for x in rs] == [r["session"]["uid"]] and rs[0]["ext_key"] == KEY
    assert not PS.off_watch(rs[0])
    from backend.sync import coros_workouts as CW
    assert CW.session_steps(PS.push_dict(rs[0], prefs=None), CW.Thresholds(cp=250.0, lthr=165.0))


# ---- the 課表 calendar: the mark, its load in the week's 計畫, the race activity ---------------------

MARK = {"id": "e1", "name": "北插天山", "priority": "B", "day": "2026-10-10", "end": "2026-10-10", "minutes": 300,
        "tss": 300.0, "done": False}
EV = {"id": "e1", "name": "北插天山", "priority": "B", "kind": "race", "start": "2026-10-10", "end": "2026-10-10"}
EV_C = {"id": "e2", "name": "C 練習賽", "priority": "C", "kind": "race", "start": "2026-10-18", "end": "2026-10-18"}
EV_A = {"id": "e0", "name": "去年的 A 賽", "priority": "A", "kind": "race", "start": "2026-09-29",
        "end": "2026-09-29"}
ACT_RACE = {"index": 21, "date": "2026-09-29", "category": "trail", "category_label": "越野跑",
            "moving_s": 18000, "tss": 310}


def _cal(monkeypatch, inp_edit=None, acts=(), events=(EV, EV_C)):
    from backend.api import plan_sessions
    from backend.sync import coros_workouts as CW
    from backend.tests.test_plan_store import Env
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda start, end: {
        "activities": [dict(a) for a in acts if start <= a["date"] <= end], "phases": [],
        "races": [e for e in events if e["end"] >= start and e["start"] <= end]})
    return Env(monkeypatch)


def test_calendar_marks_the_race_and_counts_its_load(monkeypatch):
    from backend.tests.test_plan_store import API
    with _cal(monkeypatch) as e:
        base = e.c.get(f"{API}/calendar?start=2026-10-05&end=2026-10-11").json()
        w0 = base["week_rows"][0]
        p0 = {r["date"]: r for r in e.c.get(f"{API}/sessions").json()["summary"]["projection"]}
        e.inp["weeks"][0]["races"] = [MARK]                       # the generator's projected race week
        b = e.c.get(f"{API}/calendar?start=2026-10-05&end=2026-10-18").json()
        assert not [s for s in b["sessions"] if s["kind"] == "race"]
        assert [(r["day"], r["priority"], r["tss"], r["counted"]) for r in b["races"]] == \
            [("2026-10-10", "B", 300.0, True), ("2026-10-18", "C", None, False)]
        w = b["week_rows"][0]
        assert w["planned_tss"] == pytest.approx(w0["planned_tss"] + 300.0)
        assert w["planned_hours"] == pytest.approx(w0["planned_hours"] + 5.0)
        # the stored plan's projection (_summary: the PMC's planned load) counts it too
        p1 = {r["date"]: r for r in e.c.get(f"{API}/sessions").json()["summary"]["projection"]}
        assert p1["2026-10-09"]["ctl"] == pytest.approx(p0["2026-10-09"]["ctl"])
        assert p1["2026-10-10"]["ctl"] - p0["2026-10-10"]["ctl"] == pytest.approx(300.0 / 42.0, rel=0.01)


def test_calendar_a_stored_race_session_holds_the_race_load(monkeypatch):
    """The 賽事計算機's export on the day: its own TSS is the race's, the mark's is not added on top."""
    from backend.engine import plan_store as PS
    from backend.tests.test_coros_workouts import run
    from backend.tests.test_plan_store import API, KEY, race_data
    with _cal(monkeypatch) as e:
        e.inp["weeks"][0]["races"] = [MARK]
        e.c.get(f"{API}/calendar?start=2026-10-05&end=2026-10-11")
        run(PS.upsert_external(e.db, KEY, race_data(), "2026-09-30"))          # 2026-10-10, 120 TSS
        b = e.c.get(f"{API}/calendar?start=2026-10-05&end=2026-10-11").json()
        assert [r["counted"] for r in b["races"]] == [False]
        ex = next(s for s in b["sessions"] if s["kind"] == "race")
        e.inp["weeks"][0]["races"] = []
        nomark = e.c.get(f"{API}/calendar?start=2026-10-05&end=2026-10-11").json()
        assert b["week_rows"][0]["planned_tss"] == pytest.approx(nomark["week_rows"][0]["planned_tss"])
        assert ex["tss"] == 120.0


def test_calendar_race_day_activity_is_the_race(monkeypatch):
    """An A / B / C race day's run is tagged as the race (比賽), not left as 沒排課的活動; a bike ride isn't."""
    from backend.tests.test_plan_store import API
    ride = {**ACT_RACE, "index": 22, "category": "bike", "category_label": "自行車"}
    with _cal(monkeypatch, acts=(ACT_RACE, ride), events=(EV_A, EV)) as e:
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()
        by = {a["index"]: a for a in b["activities"]}
        assert by[21]["race"] == {"id": "e0", "name": "去年的 A 賽", "priority": "A", "kind": "race"}
        assert "race" not in by[22]


def test_ics_has_no_race_mark(monkeypatch):
    """The mark is no stored session: the ICS feed (stored sessions) never has it."""
    from backend.engine import calendar_feed as CF
    from backend.engine import plan_store as PS
    from backend.tests.test_coros_workouts import run
    from backend.tests.test_plan_store import inputs, make_db, next_week
    db = run(make_db())
    wk = {**next_week(), "races": [MARK]}
    run(PS.plan_reconcile(db, inputs(weeks=[wk]), apply=True))
    ss = run(PS.load(db))
    assert not [s for s in ss if s["kind"] == "race" or s["day"] == "2026-10-10"]
    ics = CF.build([(s, None) for s in ss], date(2026, 9, 30), None)
    assert "北插天山" not in ics and "賽" not in ics.split("BEGIN:VEVENT", 1)[-1]
