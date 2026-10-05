"""
SP-97: the recovery week (overview.weeks_since_recovery / recovery_reason / recovery_long_minutes,
projection.week_hours): the 專項期's recovery weeks are counted back from the race (賽前第 5、3 週,
specific_phase.EASY_WEEKS, FRAC's low points) instead of the history-triggered 3:1, which stays the
base phase's; after 6 weeks without one the next week is one; the week keeps a shorter long run and
the short fartlek. Synthetic athlete and plan — nothing reads the athlete's data.
"""
import datetime as dt
import types
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as P
from backend.engine import specific_phase as SP
from backend.engine.planning import Event, Plan, Threshold, Weight
from backend.engine.status import Status
from backend.tests.test_b2b import _phases
from backend.tests.test_quality_gate import TODAY          # Wed 2026-09-30
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.wko5_fakes import FakeDataset

MON = TODAY - dt.timedelta(days=TODAY.weekday())           # 2026-09-28
BUILD = {1: 50, 2: 60, 3: 50, 5: 150, 6: 60}               # 370 min
LIGHT = {1: 40, 3: 40, 5: 80}                               # 160 min: ≤ 80 % of the weeks before it


def _ds(weeks: dict, default=BUILD):
    """Runs from 8/3 to TODAY; `weeks` = {weeks before MON: weekday → minutes} (1 = last week)."""
    ws, d = [], date(2026, 8, 3)
    while d < TODAY:
        k = (MON - (d - dt.timedelta(days=d.weekday()))).days // 7
        pat = {1: 45} if k == 0 else weeks.get(k, default)
        m = pat.get(d.weekday(), 0)
        if m:
            w = _run(d, minutes=m, power=180.0)
            w.metrics["tss"] = m * 0.8
            ws.append(w)
        d += dt.timedelta(days=1)
    ds = FakeDataset(ws, TODAY, settings=SETTINGS)
    ds.aethr = lambda w: 0.89 * 160.0
    ds.cp = lambda w: 250.0
    ds.mftp_run = None
    ds.config = types.SimpleNamespace(parity=True)
    return ds


def _plan(race: str, kind="race"):
    plan = Plan()
    plan.thresholds.append(Threshold("2026-08-01", lthr=165, cp=250.0))
    plan.weights.append(Weight("2026-08-01", 62.0))
    plan.events.append(Event(id="e1", name="測試越野", date=race, kind=kind, priority="A", distance_km=30,
                             climbing_m=2000, est_hours=5.0))
    return plan


def _week(race: str, weeks: dict, default=BUILD):
    ds = _ds(weeks, default)
    plan = _plan(race)
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return ds, plan, O.week_plan(ds, st, TODAY)


# ---- the bug: a 3:1 from the history landed on 賽前第 4 週 and deleted its long day -------------

def test_reproduce_recovery_week_deleting_the_week_4_long_day():
    """Three build weeks after a light one, 賽前第 4 週 (Sat 10/24): the old rule (three complete weeks
    each ≥ 0.95 × the one before) made this a recovery week with no long run — the 90 % long day of
    FRAC gone. The 專項期 now counts back from the race: week 4 is a build week with its long day."""
    ds, plan, wp = _week("2026-10-24", {4: LIGHT})
    assert wp["phase"] == "specific" and SP.weeks_out(date(2026, 10, 24), MON) == 4
    h = [x["hours"] for x in wp["history"]]
    assert all(h[i] >= 0.95 * h[i - 1] and h[i] > 0.5 for i in range(len(h) - 3, len(h)))   # the old trigger
    assert wp["mode"] == "specific"
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert long_s["minutes"] >= 150 and "恢復週" not in long_s["detail"]


# ---- 專項期: by the countdown ------------------------------------------------------------------

def test_specific_recovery_weeks_follow_the_countdown():
    _, _, wp5 = _week("2026-10-31", {4: LIGHT})             # 賽前第 5 週
    assert wp5["mode"] == "recovery_week" and "賽前第 5 週" in wp5["why"][-1]
    assert wp5["specific"]["recovery"]
    long_s = next(s for s in wp5["sessions"] if s["id"] == "long")
    assert long_s["detail"].startswith("這次目標定數約") and "恢復週" in long_s["detail"] and "+15%" not in long_s["detail"]
    assert long_s["minutes"] <= 0.70 * 150 + 5                # 65 % of the usual 150′ (≤ half the week)
    assert [s["title"] for s in wp5["sessions"] if s["kind"] == "quality"] == ["恢復週 fartlek 4×1 分"]
    assert not any(s["id"] in ("climb", "tech") for s in wp5["sessions"])
    # 賽前第 6 週 with three build weeks: no history 3:1 in the 專項期 any more
    _, _, wp6 = _week("2026-11-07", {4: LIGHT})
    assert wp6["mode"] == "specific"


def test_countdown_week_right_after_a_light_week_is_a_normal_one():
    _, _, wp = _week("2026-10-31", {1: LIGHT})               # 賽前第 5 週, last week already light
    assert wp["mode"] == "specific"


def test_projection_specific_countdown_and_the_long_base():
    _, plan, wp = _week("2026-11-14", {4: LIGHT})            # 賽前第 7 週 now
    assert wp["mode"] == "specific"
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8))
    by = {SP.weeks_out(date(2026, 11, 14), date.fromisoformat(w["start"])): w for w in weeks if w["phase"] == "specific"}
    assert [by[k]["mode"] for k in (6, 5, 4, 3)] == ["specific", "recovery_week", "specific", "recovery_week"]
    lg = {k: next(s for s in by[k]["sessions"] if s["id"] == "long")["minutes"] for k in (6, 5, 4, 3)}
    assert lg[5] < lg[6] and lg[3] < lg[4]
    assert lg[5] <= 0.65 * lg[6] + 5
    assert lg[4] >= lg[6] * 0.9                              # the recovery week's long run doesn't lower the base
    for k in (5, 3):
        assert [s["title"] for s in by[k]["sessions"] if s["kind"] == "quality"] == ["恢復週 fartlek 4×1 分"]
        assert "賽前第" in by[k]["why"][0]


# ---- both phases: the 6-week cap ------------------------------------------------------------

def test_weeks_since_recovery():
    assert O.weeks_since_recovery([6.0, 6.0, 6.0, 3.0, 6.0, 6.0], [False] * 6) == 2
    assert O.weeks_since_recovery([6.0, 6.2, 5.8, 6.1, 6.0, 6.3, 6.1], [False] * 7) == 7       # none found
    assert O.weeks_since_recovery([6.0, 6.0, 6.0, 6.0], [False, True, False, False]) == 2       # a 減量期 week
    assert O.weeks_since_recovery([0.0, 0.0, 0.0], [False] * 3) == 0                            # a new user


def test_recovery_reason_cap_and_countdown():
    race = date(2026, 11, 7)                                  # Sat: 賽前第 6 週 on 9/28
    assert O.recovery_reason("base", MON, None, 6).startswith("已經連續 6 週")
    assert O.recovery_reason("base", MON, None, 5) is None
    assert O.recovery_reason("specific", MON, race, 6) is None                   # waits one week for 賽前第 5 週
    assert "賽前第 5 週" in O.recovery_reason("specific", MON + dt.timedelta(weeks=1), race, 7)
    assert "賽前第 3 週" in O.recovery_reason("specific", MON + dt.timedelta(weeks=3), race, 1)
    assert O.recovery_reason("specific", MON + dt.timedelta(weeks=3), race, 0) is None    # last week was light
    assert O.recovery_reason("taper", MON, race, 9) is None


def test_base_six_weeks_without_a_recovery_week_forces_one():
    """Flat-ish base weeks that never make three ≥ 0.95 builds (so no 3:1) and never a light week."""
    alt = {k: ({1: 50, 2: 50, 3: 50, 5: 130, 6: 60} if k % 2 else BUILD) for k in range(1, 12)}   # 340 / 370
    ds, plan, wp = _week("2027-06-05", alt)
    assert wp["phase"] == "base" and wp["mode"] == "recovery_week"
    assert any("連續" in w and "Koop" in w for w in wp["why"])


# ---- base: the 3:1 unchanged, the content new ---------------------------------------------

def test_base_31_keeps_a_shorter_long_run_and_the_fartlek():
    ds, plan, wp = _week("2027-06-05", {4: LIGHT})
    assert wp["phase"] == "base" and wp["mode"] == "recovery_week"
    assert "連續 3 週加量" in wp["why"][-1]
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    # 65 % of a normal week's long run (the base rule: ≤ 30 % of the week, ≤ the longest 150′)
    assert long_s["detail"].startswith("恢復週") and O.RECOVERY_LONG_MIN <= long_s["minutes"] <= 0.65 * 150 + 5
    assert O.SRC_RECOVERY_WEEK in long_s["source"]
    assert [s["title"] for s in wp["sessions"] if s["kind"] == "quality"] == ["恢復週 fartlek 4×1 分"]


def test_recovery_long_minutes():
    assert O.recovery_long_minutes(150, 400) == pytest.approx(97.5)
    assert O.recovery_long_minutes(150, 400, spec_min=80) == 80
    assert O.recovery_long_minutes(20, 400) == O.RECOVERY_LONG_MIN
    assert O.recovery_long_minutes(300, 200) == 100                       # ≤ half the week


def test_recovery_week_long_run_respects_the_single_run_cap(monkeypatch):
    """Integration SP-66 × SP-97: RECOVERY_LONG_MIN is a floor, but load_guard.LONG_CAP wins (it is
    applied after the recovery-week long run, in week_plan and in the projection's week_sessions).
    The floor is raised to 90′ here so it crosses the cap of an athlete whose longest run is 45′."""
    from backend.engine import load_guard as LG
    monkeypatch.setattr(O, "RECOVERY_LONG_MIN", 90)
    short = {d: 35 for d in range(7)}                       # 245 min; this week's Tue run is 45′
    light = {d: 20 for d in range(7)}                       # 140 min: ≤ 80 % → the week before three builds
    ds, plan, wp = _week("2027-06-05", {4: light}, default=short)
    assert wp["phase"] == "base" and wp["mode"] == "recovery_week"
    long_s = next(s for s in wp["sessions"] if s["id"] == "long")
    assert long_s["minutes"] <= LG.LONG_CAP * 45 and long_s["detail"].startswith("恢復週")
    assert any(n.get("src") == "long_cap" for n in wp["notes"])
    tgt = {"z2": "", "long": ""}
    ss = P.week_sessions(MON + dt.timedelta(weeks=1), "base", "recovery_week", 4.0, 50.0, tgt, 5, 45.0, False,
                         False, 20.0, 150.0)
    lg = next(s for s in ss if s["id"] == "long")
    assert lg["minutes"] <= LG.LONG_CAP * 45
