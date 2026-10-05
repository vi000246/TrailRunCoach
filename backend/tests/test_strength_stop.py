"""
SP-86: no strength in the 14 days before an A event (Bompa & Buzzichelli p.184 / p.327) — the
減量期 and the race week, in week_plan and in the projection alike, 課表偏好 每週肌力 / 肌力日
included; B / C events unchanged. Synthetic.
"""
import datetime as dt
from datetime import date

from backend.engine import overview as O
from backend.engine import plan_auto as PA
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.engine.planning import Event
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30


def _ev(day, prio="A", name="越野賽"):
    return Event("e1", name, day, kind="race", priority=prio, distance_km=30, climbing_m=2000, est_hours=5.0)


def test_strength_stops_a_events_only_from_14_days_before():
    evs = [_ev("2026-10-24"), _ev("2026-10-31", "B", "B 賽"), _ev("2026-09-01", name="過去")]
    st = O.strength_stops(evs, date(2026, 9, 28))
    assert st == [{"from": "2026-10-10", "to": "2026-10-24", "race": "越野賽"}]


def test_drop_strength_before_a_keeps_done_and_days_before_the_window():
    mon = date(2026, 10, 5)
    stops = [{"from": "2026-10-08", "to": "2026-10-22", "race": "越野賽"}]          # from Thu
    ss = [{"id": "strength1", "kind": "strength", "day": "2026-10-06", "done": False},
          {"id": "strength2", "kind": "strength", "day": "2026-10-09", "done": False},
          {"id": "strength3", "kind": "strength", "day": "2026-10-10", "done": True},
          {"id": "easy1", "kind": "easy", "day": "2026-10-09", "done": False}]
    notes = []
    out = O.drop_strength_before_a(ss, stops, mon, notes)
    assert [s["id"] for s in out] == ["strength1", "strength3", "easy1"]
    assert len(notes) == 1 and notes[0]["src"] == "strength" and "越野賽" in notes[0]["text"]
    assert "14 天" in notes[0]["text"]
    # a week the window doesn't touch: unchanged, no note
    notes = []
    assert O.drop_strength_before_a(ss, stops, date(2026, 9, 28), notes) == ss and not notes


def _plan(ev_day, prio="A", prefs=None):
    ds = _history(TODAY)
    plan = _plan_with("2027-06-01", 1, TODAY)
    plan.events = [_ev(ev_day, prio)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return plan, O.week_plan(ds, st, TODAY, prefs=prefs)


def _strength(ss):
    return [s for s in ss if s["kind"] == "strength" and not s.get("done")]


def test_week_plan_race_week_and_taper_have_no_strength_b_race_keeps_it():
    # A race Sat 10/10: this week (9/28–10/4) is inside its 14 days
    plan, wp = _plan("2026-10-10")
    assert not _strength(wp["sessions"])
    assert any(n.get("src") == "strength" for n in wp["notes"])
    assert wp["strength_stop"] == [{"from": "2026-09-26", "to": "2026-10-10", "race": "越野賽"}]
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 25))
    by = {w["start"]: w for w in weeks}
    assert not _strength(by["2026-10-05"]["sessions"])                              # race week
    assert any(n.get("src") == "strength" for n in by["2026-10-05"].get("notes") or [])
    assert _strength(by["2026-10-12"]["sessions"])                                  # after the race: back
    # the same date as a B race: strength as before
    _plan_b, wp_b = _plan("2026-10-10", "B")
    assert _strength(wp_b["sessions"]) and not any(n.get("src") == "strength" for n in wp_b["notes"])


def test_projection_and_preferences_respect_the_window():
    # A race Sat 11/07: no strength on 10/24–11/07, before it the weeks keep theirs
    prefs = PP.Prefs(strength=2, strength_days=(1, 5))                             # Tue + Sat
    plan, wp = _plan("2026-11-07", prefs=prefs)
    assert _strength(wp["sessions"])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8), prefs=prefs)
    days = [s["day"] for w in weeks for s in _strength(w["sessions"])]
    assert days and not any("2026-10-24" <= d <= "2026-11-07" for d in days)
    by = {w["start"]: w for w in weeks}
    w19 = by["2026-10-19"]                       # Tue 10/20 kept, Sat 10/24 (in the window) dropped
    assert [s["day"] for s in _strength(w19["sessions"])] == ["2026-10-20"]
    assert any(n.get("src") == "strength" for n in w19["notes"])
    assert not _strength(by["2026-10-26"]["sessions"]) and not _strength(by["2026-11-02"]["sessions"])


def test_auto_adjust_removes_stored_strength_without_asking():
    today = "2026-10-05"
    items = [{"action": "removed", "day": f"2026-10-{d:02d}", "kind": "strength", "title": "肌力", "tss": 20.0}
             for d in (6, 8, 13, 15)]
    big = PA.classify([], [], items, today, "2026-10-18", "taper", "taper", 12)
    assert big == []                         # a reduction, not a hard / long session: no confirmation
