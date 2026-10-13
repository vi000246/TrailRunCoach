"""
SP-492: a race's day holds the race only. The placement puts sessions on any free day of the week, and
the 減量期 rules (overview.taper_rules) stopped the day before the race (`d < race`), so a projected
race week with the A race on a Tuesday kept its 「有氧間歇（巡航）2×8 分」 on the race day. Now the
taper rules read the race day too (a quality session there follows the race-week rule: onto an easy
day 3–5 days out, else an easy run) and the post-race day rules (post_race.apply) treat the race days
like the no-run days after them: whatever is left there moves to a free day after them, else goes.
Every weekday, week_plan and the projection; B / C races keep their own race-day rules. Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import post_race as PR_
from backend.engine import projection as PJ
from backend.engine.planning import Event
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30

RACE_WEEK = date(2026, 10, 5)                                # Mon: the projected week after TODAY's
RACE_DAYS = [RACE_WEEK + dt.timedelta(days=i) for i in range(7)]
WD = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _plan(race: date, today: date, prio: str = "A", prefs=None):
    ds = _history(today)
    plan = _plan_with("2027-06-01", 1, today)
    plan.events = [Event("e1", "越野賽", race.isoformat(), kind="race", priority=prio, distance_km=30,
                         climbing_m=2000, est_hours=5.0)]
    ds.plan = plan
    st = Status(ds, plan, today, prefs=PP.Prefs()).compute()
    return plan, O.week_plan(ds, st, today, prefs=prefs)


def _on_race_day(ss, race: date) -> list:
    """The sessions on the race day besides the race itself (a done one is what was run)."""
    return [(x["kind"], x["title"]) for x in ss
            if x.get("day") == race.isoformat() and not x.get("done") and x["kind"] != "race"]


@pytest.mark.parametrize("race", RACE_DAYS, ids=WD)
def test_projection_leaves_an_a_race_day_to_the_race(race):
    """The bug: race on Tue → the projected week's 2×8′ (quality's first pick is Tue) on the race day."""
    plan, wp = _plan(race, TODAY)
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), race + dt.timedelta(days=7), events=plan.events)
    wk = next(w for w in weeks if w["start"] == RACE_WEEK.isoformat())
    assert _on_race_day(wk["sessions"], race) == []


@pytest.mark.parametrize("race", RACE_DAYS, ids=WD)
@pytest.mark.parametrize("prefs", ["none", "quality_on_race_day"])
def test_week_plan_leaves_an_a_race_day_to_the_race(race, prefs):
    """The race week itself (today = its Monday): no plan rule, and no 課表偏好 asking for the interval
    on that weekday, leaves a session on the race day."""
    p = None if prefs == "none" else PP.Prefs(long_day="sat", pref_days=(("quality", (race.weekday(),)),))
    _, wp = _plan(race, RACE_WEEK, prefs=p)
    assert _on_race_day(wp["sessions"], race) == []


@pytest.mark.parametrize("race", RACE_DAYS, ids=WD)
@pytest.mark.parametrize("prio", ["B", "C"])
def test_b_and_c_race_days_hold_no_quality(race, prio):
    """B / C races (post_race.bc_apply) already put their race on the day: no interval beside it, in
    week_plan or the projection."""
    plan, wp = _plan(race, RACE_WEEK, prio)
    assert not [k for k, _t in _on_race_day(wp["sessions"], race) if k in ("quality", "test")]
    plan, wp = _plan(race, TODAY, prio)
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), race + dt.timedelta(days=7), events=plan.events)
    wk = next(w for w in weeks if w["start"] == RACE_WEEK.isoformat())
    assert not [k for k, _t in _on_race_day(wk["sessions"], race) if k in ("quality", "test")]


# ---- the two day rules -------------------------------------------------------------------------

RACE = "2026-10-17"                                          # Sat


def tc():
    return {"race": "r", "id": "e", "start": RACE, "taper_start": "2026-10-03", "days": 14, "trail": True,
            "sore": False, "long_days": 7}


def s(id_, kind, day, minutes=50, **kw):
    return {"id": id_, "kind": kind, "day": day, "minutes": minutes, "title": "x", "tss": 50.0, "done": False,
            "detail": "", "source": "", "terrain": None, **kw}


def test_taper_rules_read_the_race_day():
    """A quality session on the race day follows the race-week rule: onto an easy day 3–5 days out as
    the short one (the easy run takes the race day — post_race moves it on), else an easy run."""
    ss = [s("quality", "quality", RACE), s("easy1", "easy", "2026-10-13")]
    O.taper_rules(ss, tc(), date(2026, 10, 12))
    assert ss[0]["day"] == "2026-10-13" and ss[0]["title"] == O.TAPER_SHORT["title"]
    assert ss[1]["day"] == RACE
    ss = [s("quality", "quality", RACE)]
    notes = []
    O.taper_rules(ss, tc(), date(2026, 10, 12), notes)
    assert ss[0]["kind"] == "easy" and ss[0]["minutes"] <= 40 and "賽前 3 天內不排強度課" in notes[0]["text"]


def _win(start: str, end: str):
    s_, e_ = date.fromisoformat(start), date.fromisoformat(end)
    return {"race": "r", "id": "e", "start": s_, "end": e_, "no_run": e_ + dt.timedelta(days=2),
            "short": e_ + dt.timedelta(days=7), "flat": None}


def test_post_race_rules_clear_the_race_days():
    """A session left on a race day moves to a free day after the no-run days (an easy run ≤ 40 min
    there), else goes; the note says the race day is the race's."""
    mon = date(2026, 10, 5)
    ss = [s("quality", "quality", "2026-10-06", 45), s("strength1", "strength", "2026-10-06", 25),
          s("easy1", "easy", "2026-10-10")]
    notes = []
    out = PR_.apply(ss, [_win("2026-10-06", "2026-10-06")], mon, notes)
    q = next(x for x in out if x["id"] in ("quality", "easy2"))
    assert q["day"] == "2026-10-09" and q["kind"] == "easy" and q["minutes"] <= 40
    assert all(x["day"] != "2026-10-06" for x in out)
    assert "當天只排比賽" in notes[0]["text"]
    # a Sunday race: no free day after it in the week — the session goes, said in the note
    ss = [s("easy1", "easy", "2026-10-11"), s("easy2", "easy", "2026-10-09")]
    notes = []
    out = PR_.apply(ss, PR_.a_windows([{"kind": "event", "start": "2026-10-11", "end": "2026-10-11",
                                        "event_id": "e"}], [], mon), mon, notes)
    assert [x["id"] for x in out] == ["easy2"] and "拿掉" in notes[0]["text"] and "當天只排比賽" in notes[0]["text"]
    # a 2-day race: both days
    ss = [s("easy1", "easy", "2026-10-07"), s("easy2", "easy", "2026-10-08")]
    out = PR_.apply(ss, [_win("2026-10-07", "2026-10-08")], mon, [])
    assert sorted(x["day"] for x in out) == ["2026-10-11"]
