"""
SP-66 單次長跑護欄 (engine/load_guard.py, Frandsen 2025): a run ≤ +10 % over the longest foot
session of the 30 days before — the planner's long day stays under it (week_plan, projection,
specific_phase's 90-min floor no longer lifts it), a done run over it is a 提醒 in status.
Synthetic workouts only.
"""
import datetime as dt
import types
from datetime import date

import pytest

from backend.engine import load_guard as LG
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import specific_phase as SP
from backend.engine.planning import Event, Plan, Threshold, Weight
from backend.engine.status import WATCH, Status
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.wko5_fakes import FakeDataset

TODAY = date(2026, 9, 30)             # a Wednesday


def test_cap_is_ten_percent_of_the_30_day_longest():
    assert LG.LONG_CAP == pytest.approx(1.10) and LG.LONG_DAYS == 30
    assert LG.long_cap(100) == pytest.approx(110) and LG.long_cap(0) is None and LG.long_cap(None) is None
    assert LG.cap_long(105, 100) == (105, False)
    assert LG.cap_long(120, 100) == (110, True)
    assert LG.cap_long(80, 60) == (65, True)            # 66 → rounded DOWN to 5 (rounding up would cross)
    assert LG.cap_long(60, 0) == (60, False)            # no history: the planner's own default


def _row(day, minutes, ekm=None, run=True):
    return {"day": day, "minutes": minutes, "ekm": ekm, "run": run}


def test_session_spikes_by_effort_km_then_time():
    rows = [_row(0, 120, 20.0), _row(10, 130, 23.0), _row(12, 125, 21.5)]
    (h,) = LG.session_spikes(rows, 0, 20)
    assert h["day"] == 10 and h["by"] == "ekm" and h["ref"] == 20.0 and h["excess"] == pytest.approx(0.15)
    # a slow mountain run: more minutes but not more effort-km is not a spike (time alone would be)
    assert LG.session_spikes([_row(0, 120, 20.0), _row(5, 160, 21.0)], 0, 20) == []
    # no distance on either side: by time
    (h,) = LG.session_spikes([_row(0, 60), _row(3, 70)], 0, 20)
    assert h["by"] == "min" and h["excess"] == pytest.approx(70 / 60 - 1)


def test_session_spikes_window_hikes_and_same_day():
    # the reference is the 30 days BEFORE the run's day: day 0 is out of reach of day 31
    assert LG.session_spikes([_row(0, 60, 10.0), _row(31, 90, 15.0)], 0, 40) == []
    assert len(LG.session_spikes([_row(1, 60, 10.0), _row(31, 90, 15.0)], 0, 40)) == 1
    # a long hike is time on feet: it makes the reference; a hike itself is never flagged
    assert LG.session_spikes([_row(0, 300, 30.0, run=False), _row(5, 150, 25.0)], 0, 20) == []
    assert LG.session_spikes([_row(0, 60, 10.0), _row(5, 300, 30.0, run=False)], 0, 20) == []
    # the run's own day is not its reference; outside [lo, hi] is not checked
    assert len(LG.session_spikes([_row(0, 60, 10.0), _row(5, 60, 10.0), _row(5, 90, 15.0)], 0, 20)) == 1
    assert LG.session_spikes([_row(0, 60, 10.0), _row(5, 90, 15.0)], 6, 20) == []


def test_specific_floor_no_longer_lifts_the_long_day_over_the_cap():
    from backend.tests.test_specific_phase import MON, _race
    a = SP.week_context(kind="specific", mode="specific", monday=MON, race=_race())
    assert SP.long_minutes(a, 400) == pytest.approx(0.85 * 300)
    # longest 70 → cap 77: the old 90-min floor gave 90 (+29 %)
    assert SP.long_minutes(a, 70) == pytest.approx(77)
    assert SP.long_minutes(a, 100) == pytest.approx(110)


def _history(longest_min, today=TODAY, weeks=8):
    """Weeks of Tue / Thu / Sat runs, Sat = `longest_min` (the other runs ≤ 40 min); last week a
    lighter one (no 3:1 recovery week this week)."""
    ws, d = [], today - dt.timedelta(weeks=weeks)
    last = today - dt.timedelta(days=today.weekday() + 7)
    while d < today:
        k = 0.6 if last <= d < last + dt.timedelta(days=7) else 1.0
        m = round(k * {1: min(40, longest_min), 3: min(40, longest_min), 5: longest_min}.get(d.weekday(), 0))
        if m:
            w = _run(d, minutes=m, power=180.0)
            w.metrics["tss"] = m * 0.8
            ws.append(w)
        d += dt.timedelta(days=1)
    ds = FakeDataset(ws, today, settings=SETTINGS)
    ds.aethr = lambda w: 0.89 * 160.0
    ds.cp = lambda w: 250.0
    ds.mftp_run = None
    ds.config = types.SimpleNamespace(parity=True)
    return ds


def _plan(events=()):
    plan = Plan()
    plan.thresholds.append(Threshold("2026-08-01", lthr=165, cp=250.0))
    plan.weights.append(Weight("2026-08-01", 62.0))
    plan.events.extend(events)
    return plan


def _week(ds, plan):
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return st, O.week_plan(ds, st, TODAY)


def test_week_plan_long_run_capped_with_a_note():
    """Longest of 30 days 45 min: the 60-min floor used to plan 60 (+33 %); now ≤ 49.5 → 45, with a note."""
    _st, wp = _week(_history(45), _plan())
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert long_s["minutes"] <= 45 * LG.LONG_CAP
    note = next(n for n in wp["notes"] if n.get("src") == "long_cap")
    assert "過去 30 天最長一次 45 分" in note["text"] and "Frandsen 2025" in note["text"]


def test_week_plan_long_run_uncapped_when_within_ten_percent():
    _st, wp = _week(_history(120), _plan())
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert long_s["minutes"] <= 120 * LG.LONG_CAP
    assert not any(n.get("src") == "long_cap" for n in wp["notes"])


def _status(extra, events=()):
    """60-min long runs for 8 weeks, then `extra` = (date, minutes) runs."""
    ws, d = [], TODAY - dt.timedelta(weeks=8)
    while d < TODAY - dt.timedelta(days=6):
        m = {1: 40, 3: 40, 5: 60}.get(d.weekday(), 0)
        if m:
            ws.append(_run(d, minutes=m))
        d += dt.timedelta(days=1)
    ws += [_run(day, minutes=m) for day, m in extra]
    ds = FakeDataset(ws, TODAY, settings=SETTINGS)
    plan = _plan(events)
    ds.plan = plan
    return Status(ds, plan, TODAY, prefs=PP.Prefs())


def test_status_reminds_of_a_run_over_ten_percent():
    st = _status([(TODAY - dt.timedelta(days=2), 80)])
    ind = st.i_long()
    assert ind.level == WATCH and "單次跑太長" in ind.verdict and "Frandsen 2025" in ind.source
    sp = ind.extra["spike"]
    assert sp["date"] == (TODAY - dt.timedelta(days=2)).isoformat() and sp["by"] == "ekm"
    assert sp["excess"] == pytest.approx(80 / 60 - 1, abs=0.02)          # same pace, same climb per run
    assert "比前 30 天最長一次" in ind.why and ind.action


def test_status_quiet_within_ten_percent_old_spikes_and_race_days():
    assert "spike" not in _status([(TODAY - dt.timedelta(days=2), 65)]).i_long().extra
    assert "spike" not in _status([(TODAY - dt.timedelta(days=10), 90)]).i_long().extra   # > 7 days ago
    race = Event("r1", "城市馬", (TODAY - dt.timedelta(days=2)).isoformat(), kind="race", priority="B")
    assert "spike" not in _status([(TODAY - dt.timedelta(days=2), 90)], [race]).i_long().extra
