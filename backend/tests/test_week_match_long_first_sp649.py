"""週內配對先判長跑 (SP-649): week_plan's done-matching took the long run first — any run ≥ 80 % of
the long run's minutes — so a 37′ recovery-week fartlek (4×1′ ≥ 95 % CP) was the 45′ LSD and the
fartlek stayed to do. A run that does an open quality session's work and is nearer that session's
time than the long run's is the interval session; a real long run (with or without a set of
efforts in it, SP-519) stays the long run. plan_match follows the generator's week-wide pick (pass 2).

The week: test_quality_gate's flat 8 weeks → a recovery week (TODAY = Wed 2026-09-30) with
「恢復週 fartlek 4×1 分」 37′ (hard need 0.6 × 4′ = 144 s ≥ 95 % CP) and 「LSD（路跑）」 45′."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import interval_reps as IR
from backend.engine import overview as O
from backend.engine import plan_match as PM
from backend.engine import plan_prefs as PP
from backend.engine import workout_review as WR
from backend.engine.planning import Threshold
from backend.engine.status import Status
from backend.tests import test_quality_gate as TQ
from backend.tests.test_workout_review import _run

TODAY = TQ.TODAY
MON = TODAY - dt.timedelta(days=TODAY.weekday())          # 2026-09-28
CP = 250.0
FARTLEK = "恢復週 fartlek 4×1 分"


def _efforts(day, minutes, reps=4, on=262.0, work_s=60, rest_s=120, warm_s=900, base=175.0):
    """`warm_s` easy, `reps` × `work_s` at `on` W (105 % CP) with `rest_s` at 150 W between, easy to
    `minutes` — the fartlek, or a long run with a set of efforts in it."""
    p = [base] * warm_s
    for k in range(reps):
        p += [on] * work_s
        if k < reps - 1:
            p += [150.0] * rest_s
    p += [base] * (minutes * 60 - len(p))
    return _run(day, minutes=minutes, power=np.array(p))


def _week(*this_week):
    plan = TQ._plan(lthr=165, day="2026-09-01")
    plan.thresholds.append(Threshold("2026-09-20", cp=CP))
    ws = [w for w in TQ._daily() if w.start.date() < MON] + list(this_week)
    for w in this_week:
        w.metrics["tss"] = 40.0
    ds = TQ._ds(ws, plan)
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    assert wp["mode"] == "recovery_week"
    by = {s["id"]: s for s in wp["sessions"]}
    assert by["quality"]["title"] == FARTLEK      # planned 37′ (a done one shows its own structure's minutes)
    assert by["long"]["minutes"] == 45
    return ds, wp, by


def _done_on(s):
    return (s.get("done_by") or {}).get("date") if s.get("done") else None


def test_a_37_minute_fartlek_is_the_interval_session_not_the_lsd():
    """The bug: 37′ ≥ 0.8 × 45′ → the long slot took the fartlek, the fartlek stayed to do."""
    _ds, _wp, by = _week(_efforts(MON, 37))
    assert _done_on(by["quality"]) == MON.isoformat()
    assert not by["long"]["done"]


def test_fartlek_and_lsd_in_one_week_each_take_their_own():
    """The fartlek on Monday, a 50′ easy LSD on Tuesday: the LSD is the long run (it went to
    easy1 before), the fartlek the interval session."""
    lsd = _run(MON + dt.timedelta(days=1), minutes=50, power=180.0)
    _ds, _wp, by = _week(_efforts(MON, 37), lsd)
    assert _done_on(by["quality"]) == MON.isoformat()
    assert _done_on(by["long"]) == (MON + dt.timedelta(days=1)).isoformat()


def test_a_real_long_run_stays_the_long_run():
    _ds, _wp, by = _week(_run(MON + dt.timedelta(days=1), minutes=48, power=180.0))
    assert _done_on(by["long"]) == (MON + dt.timedelta(days=1)).isoformat()
    assert not by["quality"]["done"]


@pytest.mark.parametrize("minutes", [44, 55])
def test_a_long_run_with_a_set_of_efforts_stays_the_long_run(minutes):
    """SP-519's case: a long run with ≥ 3 similar efforts ≥ 95 % CP (enough for the fartlek's
    144 s) — the 間歇 page shows itself, but the run is nearer the long run's 45′ than the
    fartlek's 37′, so the plan match keeps it the long run (SP-519 is display only)."""
    run = _efforts(MON + dt.timedelta(days=1), minutes)
    ds, _wp, by = _week(run)
    w = next(x for x in ds.workouts if O.wdate(x) == MON + dt.timedelta(days=1))
    assert IR.auto_reps(ds, w, WR._samples(ds, w), CP) is not None
    assert _done_on(by["long"]) == (MON + dt.timedelta(days=1)).isoformat()
    assert not by["quality"]["done"]


def test_plan_match_links_the_fartlek_to_the_interval_session_through_the_generator():
    """plan_match pass 2 follows the generator's week-wide pick: the stored fartlek (Wed) takes the
    Monday run as 「同一週（課表自動對到）」 and the stored LSD (Sun, ahead) stays open."""
    _ds, wp, by = _week(_efforts(MON, 37))
    a = by["quality"]["done_by"]
    ws_ = MON.isoformat()
    gen_done = {(ws_, k): by[k] for k in ("quality", "long")}
    base = {"week_start": ws_, "target": "", "detail": "", "source": "", "origin": "auto", "edited": False,
            "provisional": False, "done_by": None, "note": None, "state": "active"}
    out = [{**base, "uid": "q", "gen_key": "quality", "day": TODAY.isoformat(), "kind": "quality",
            "title": FARTLEK, "minutes": 37, "tss": 30.0},
           {**base, "uid": "l", "gen_key": "long", "day": "2026-10-04", "kind": "long",
            "title": "LSD（路跑）", "minutes": 45, "tss": 36.0}]
    PM.assign(out, [a], TODAY.isoformat(), gen_done)
    q, lng = out
    assert q["state"] == "done" and q["done_by"]["index"] == a["index"] and q["done_by"]["match"] == "plan"
    assert lng["state"] == "active"
