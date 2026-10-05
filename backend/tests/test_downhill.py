"""
SP-99: the 專項期's downhill sessions by countdown (engine/downhill.py) — 賽前第 9、6、3 週 before an
A race with a clear descent, ≤ 3 weeks apart, the last one 14–21 days out, the first one small with
2 easy days after it; none for a road race. Synthetic athlete and plan.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import downhill as DH
from backend.engine import projection as P
from backend.engine.planning import Event
from backend.tests.test_b2b import _phases
from backend.tests.test_quality_gate import TODAY
from backend.tests.test_recovery_week import LIGHT, MON, _week

RACE = date(2026, 12, 5)                                   # Sat: 賽前第 10 週 on 9/28


def ev(kind="race", climbing_m=2000, start=RACE):
    return Event(id="e1", name="測試越野", date=start.isoformat(), kind=kind, priority="A", distance_km=30,
                 climbing_m=climbing_m, est_hours=5.0)


def ctx(monday, **kw):
    a = dict(kind="specific", mode="specific", monday=monday, events=[ev()], phases=None, road=False)
    a.update(kw)
    return DH.week_context(**a)


def test_countdown_weeks_and_which_races():
    on = {w: ctx(RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=w - 1))["active"] for w in range(2, 11)}
    assert [w for w, a in on.items() if a] == [3, 6, 9]
    m6 = RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=5)
    assert DH.weeks_out(RACE, m6) == 6
    assert not ctx(m6, kind="base")["active"] and not ctx(m6, mode="reentry")["active"]
    assert ctx(m6, mode="recovery_week")["active"]                         # RPE 3–5: a recovery week keeps it
    assert not ctx(m6, road=True)["active"]                                # 主要訓練項目 = 路跑
    assert not ctx(m6, events=[ev(kind="road", climbing_m=0)])["active"]  # 路跑賽事
    flat = ctx(m6, events=[ev(climbing_m=300)])                            # 10 m/km
    assert not flat["active"] and "不明顯" in flat["why"]
    assert ctx(m6, events=[ev(kind="baiyue")])["active"]
    # the first one: the earliest countdown week inside the 專項期 (8 weeks: week 9; a short one: week 6)
    ph = [{"kind": "specific", "start": (RACE - dt.timedelta(days=14 + 56)).isoformat(),
           "end": (RACE - dt.timedelta(days=15)).isoformat()}]
    m9 = m6 - dt.timedelta(weeks=3)
    assert ctx(m9, phases=ph)["first"] and not ctx(m6, phases=ph)["first"]
    short = [{"kind": "specific", "start": (m6 - dt.timedelta(days=2)).isoformat(),
              "end": (RACE - dt.timedelta(days=15)).isoformat()}]
    assert ctx(m6, phases=short)["first"]


def _ss(monday):
    """A placed week: Tue interval, Sat long, easy Mon / Wed / Thu / Sun."""
    def s(i, kind, wd, m):
        return {"id": i, "kind": kind, "title": i, "minutes": m, "tss": m * 0.8, "detail": "", "done": False,
                "day": (monday + dt.timedelta(days=wd)).isoformat()}
    return [s("easy1", "easy", 0, 40), s("quality", "quality", 1, 60), s("easy2", "easy", 2, 50),
            s("easy3", "easy", 3, 45), s("long", "long", 5, 150), s("easy4", "easy", 6, 40)]


def test_apply_picks_a_day_away_from_hard_days_and_keeps_the_week():
    m6 = RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=5)
    info = {**ctx(m6), "first": False}
    ss = _ss(m6)
    total = sum(s["minutes"] for s in ss)
    notes = []
    DH.apply(ss, info, notes=notes)
    (d,) = [s for s in ss if s["id"] == "downhill"]
    # Wed (50′, the longest allowed): not Mon (the day before Tue's interval) nor Fri (before the long run)
    assert d["day"] == (m6 + dt.timedelta(days=2)).isoformat()
    assert d["title"] == "下坡離心（下坡 25′）" and d["minutes"] == 50 and d["terrain"] == "trail"
    assert d["steps"]["origin"] == "template:lib:downhill_ecc"
    assert sum(s["minutes"] for s in ss) == total
    assert info["planned"][0]["descent_m"] == 350 and notes and "賽前第 6 週" in notes[0]["text"]
    # a short easy run grows to fit; the other easy runs give the minutes
    ss = _ss(m6)
    ss[2]["minutes"] = 30
    total = sum(s["minutes"] for s in ss)
    DH.apply(ss, {**ctx(m6), "first": False})
    (d,) = [s for s in ss if s["id"] == "downhill"]
    assert d["minutes"] == 45 and sum(s["minutes"] for s in ss) == total

    # the first one: smaller, and the 2 days after it easy (interval Wed, long Sat)
    def wk(with_sun):
        ss = _ss(m6)
        ss[1]["day"], ss[2]["day"] = (m6 + dt.timedelta(days=2)).isoformat(), (m6 + dt.timedelta(days=1)).isoformat()
        return ss if with_sun else ss[:-1]
    info = {**ctx(m6), "first": True}
    ss, notes = wk(False), []
    DH.apply(ss, info, notes=notes)                     # Mon / Tue / Thu all have a hard day within 2 days
    assert not any(s["id"] == "downhill" for s in ss) and "2 天也要輕鬆" in notes[0]["text"]
    ss = wk(True)
    DH.apply(ss, info)
    (d,) = [s for s in ss if s["id"] == "downhill"]
    assert d["title"].startswith("下坡離心（第一次") and "15′" in d["title"]
    assert d["day"] == (m6 + dt.timedelta(days=6)).isoformat()           # Sun

def test_apply_never_within_14_days_of_the_race_and_respects_the_weekday_cap():
    m3 = RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=2)
    info = {**ctx(m3), "first": False}
    ss = _ss(m3)
    DH.apply(ss, info)
    (d,) = [s for s in ss if s["id"] == "downhill"]
    assert 14 <= (RACE - date.fromisoformat(d["day"])).days <= 21

    class Prefs:
        active, cap_weekday, long_cap = True, 30, None
    m6 = m3 - dt.timedelta(weeks=3)
    ss = _ss(m6)
    DH.apply(ss, {**ctx(m6), "first": False}, prefs=Prefs())
    (d,) = [s for s in ss if s["id"] == "downhill"]
    assert d["title"] == "下坡離心（下坡 10′）" and "15′" not in d["title"]


def test_week_plan_and_projection():
    """賽前第 10 週 now (Sat 12/5, a trail race descending ~67 m/km): the projected 專項期 has a
    downhill session in weeks 9, 6 and 3 — ≤ 3 weeks apart, the last 14–21 days out, the first the
    small one — and none in the 減量期."""
    ds, plan, wp = _week(RACE.isoformat(), {4: LIGHT})
    assert wp["phase"] == "specific" and not wp["downhill"]["active"]          # 賽前第 10 週
    weeks = P.project_weeks(wp, _phases(plan, TODAY), RACE, events=plan.events)
    days = sorted(s["day"] for w in weeks for s in w["sessions"] if s["id"] == "downhill")
    wk = [DH.weeks_out(RACE, date.fromisoformat(d) - dt.timedelta(days=date.fromisoformat(d).weekday())) for d in days]
    assert wk == [9, 6, 3]
    ds_ = [date.fromisoformat(d) for d in days]
    # every 3rd week (the day inside the week follows the easy days, so ± a few days around 21)
    assert all(17 <= (b - a).days <= 25 for a, b in zip(ds_, ds_[1:]))
    assert 14 <= (RACE - ds_[-1]).days <= 21
    first = next(s for w in weeks for s in w["sessions"] if s["id"] == "downhill")
    assert "第一次" in first["title"]
    assert not any(s["id"] == "downhill" for w in weeks if w["phase"] == "taper" for s in w["sessions"])
    # a road A race: none
    ds, plan, wp = _week(RACE.isoformat(), {4: LIGHT})
    plan.events[0].kind = "road"
    weeks = P.project_weeks(wp, _phases(plan, TODAY), RACE, events=plan.events)
    assert not any(s["id"] == "downhill" for w in weeks for s in w["sessions"])


def test_never_in_the_real_taper_a_21_day_one_starting_mid_week():
    """Integration SP-96 × SP-99: the session stays out of the race's real 減量期 (planning.taper_start),
    not only the last DOWNHILL_LAST_DAYS — a 17-day taper (課表偏好 / a manual phase) starting on the
    Wednesday of 賽前第 3 週 leaves only its Monday, the day before the interval."""
    m3 = RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=2)     # 賽前第 3 週 (Mon 11/16)
    t0 = RACE - dt.timedelta(days=17)                                          # Wed 11/18
    ph = [{"kind": "specific", "start": (t0 - dt.timedelta(days=56)).isoformat(),
           "end": (t0 - dt.timedelta(days=1)).isoformat()},
          {"kind": "taper", "start": t0.isoformat(), "end": (RACE - dt.timedelta(days=1)).isoformat(), "event_id": "e1"}]
    info = ctx(m3, phases=ph)
    assert info["active"] and info["taper_start"] == t0.isoformat()
    ss = _ss(m3)
    DH.apply(ss, info)
    assert not any(s["id"] == "downhill" for s in ss)          # Wed / Thu / Sun in the taper, Mon before the interval
    # the default 14-day taper: week 3 keeps it
    info = ctx(m3)
    assert info["taper_start"] == (RACE - dt.timedelta(days=14)).isoformat()
    ss = _ss(m3)
    DH.apply(ss, info)
    d = next(s for s in ss if s["id"] == "downhill")
    assert d["day"] == t0.isoformat() and (RACE - date.fromisoformat(d["day"])).days >= 14
