"""
The downhill sessions (engine/downhill.py). SP-99: before an A race with a clear descent, the first one
small with 2 easy days after it, the last ≥ 14 days out and never in the race's real 減量期; none for a
road race. SP-87 (owner 2026-10-05, decision 3): every week in the 專項期 (the downhill part rising
15 → 20 → 25′), every 2 weeks in the 基礎期 (a gentle 10–15′ dose), none in the last 2 weeks before
the race (was: 賽前第 9、6、3 週 only). Synthetic athlete and plan.
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


def test_which_weeks_and_which_races():
    """SP-87: every 專項期 week from 賽前第 10 週 to 第 3 週, none in the last 2 weeks; the 基礎期 every 2
    weeks (an even ISO week)."""
    on = {w: ctx(RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=w - 1))["active"] for w in range(1, 11)}
    assert [w for w, a in on.items() if a] == list(range(3, 11))
    m2 = RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=1)
    assert "2 週" in ctx(m2)["why"]
    m6 = RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=5)
    assert DH.weeks_out(RACE, m6) == 6
    assert not ctx(m6, mode="reentry")["active"]
    assert ctx(m6, mode="recovery_week")["active"]                         # RPE 3–5: a recovery week keeps it
    assert not ctx(m6, road=True)["active"]                                # 主要訓練項目 = 路跑
    assert not ctx(m6, events=[ev(kind="road", climbing_m=0)])["active"]  # 路跑賽事
    flat = ctx(m6, events=[ev(climbing_m=300)])                            # 10 m/km
    assert not flat["active"] and "不明顯" in flat["why"]
    assert ctx(m6, events=[ev(kind="baiyue")])["active"]
    for k in ("taper", "transition", "rebuild", "recovery"):
        assert not ctx(m6, kind=k, mode=k)["active"], k
    # 基礎期: every 2 weeks — even ISO weeks (m6 = 10/26, ISO 44); the next A race's rules hold too
    m7 = m6 - dt.timedelta(weeks=1)
    assert m6.isocalendar()[1] % 2 == 0 and ctx(m6, kind="base", mode="base")["active"]
    assert not ctx(m7, kind="base", mode="base")["active"] and "2 週" in ctx(m7, kind="base", mode="base")["why"]
    assert not ctx(m6, kind="base", mode="base", events=[ev(climbing_m=300)])["active"]
    assert not ctx(m6, kind="base", mode="base", road=True)["active"]
    # 基礎期 with no A race (a trail athlete between races): still every 2 weeks
    free = ctx(m6, kind="base", mode="base", events=[])
    assert free["active"] and free.get("race") is None
    assert not ctx(m6, kind="specific", events=[])["active"]
    # the first one of the 專項期 (no 基礎期 right before it): its first week, smaller
    ph = [{"kind": "specific", "start": (RACE - dt.timedelta(days=14 + 56)).isoformat(),
           "end": (RACE - dt.timedelta(days=15)).isoformat()}]
    m10, m9, m8 = m6 - dt.timedelta(weeks=4), m6 - dt.timedelta(weeks=3), m6 - dt.timedelta(weeks=2)
    assert ctx(m10, phases=ph)["first"] and not ctx(m9, phases=ph)["first"]
    # the downhill part rises 15 → 20 → 25′ over the 專項期's weeks
    assert [ctx(m, phases=ph)["work"] for m in (m10, m9, m8, m6)] == [15, 20, 25, 25]
    short = [{"kind": "specific", "start": (m6 - dt.timedelta(days=2)).isoformat(),
              "end": (RACE - dt.timedelta(days=15)).isoformat()}]
    assert ctx(m6, phases=short)["first"]
    # after a 基礎期 (which had the small doses) the 專項期's first week is not the first one
    after = [{"kind": "base", "start": "2026-08-01", "end": (RACE - dt.timedelta(days=71)).isoformat()}] + ph
    assert not ctx(m10, phases=after)["first"] and ctx(m10, phases=after)["work"] == 15
    # 基礎期: the first even week of the phase is the first one (10′), then 15′
    bph = [{"kind": "base", "start": (m6 - dt.timedelta(weeks=2)).isoformat(),
            "end": (m6 + dt.timedelta(weeks=8)).isoformat()}]
    b1 = ctx(m6 - dt.timedelta(weeks=2), kind="base", mode="base", phases=bph)
    b2 = ctx(m6, kind="base", mode="base", phases=bph)
    assert b1["first"] and b1["work"] == DH.BASE_FIRST_WORK == 10
    assert not b2["first"] and b2["work"] == DH.BASE_WORK == 15


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
    """賽前第 10 週 now (Sat 12/5, a trail race descending ~67 m/km), SP-87: one downhill session in this
    week and in every projected 專項期 week (賽前第 10 → 3 週), the downhill part 15 → 20 → 25′ (the
    基礎期 before it had the small doses: not 「第一次」), the last ≥ 14 days out, none in the 減量期 or
    the last 2 weeks."""
    ds, plan, wp = _week(RACE.isoformat(), {4: LIGHT})
    assert wp["phase"] == "specific" and wp["downhill"]["active"]              # 賽前第 10 週
    mine = [s for s in wp["sessions"] if s["id"] == "downhill"]
    assert len(mine) == 1 and mine[0]["title"] == "下坡離心（下坡 15′）"
    weeks = P.project_weeks(wp, _phases(plan, TODAY), RACE, events=plan.events)
    spec = [w for w in weeks if w["phase"] == "specific"]
    assert spec
    works = []
    for w in spec:
        n = sum(1 for s in w["sessions"] if s["id"] == "downhill")
        assert n == 1, w["start"]
        dh = next(s for s in w["sessions"] if s["id"] == "downhill")
        assert "第一次" not in dh["title"]
        works.append(w["downhill"]["work"])
    assert works[:2] == [20, 25] and set(works[2:]) == {25}
    days = sorted(s["day"] for w in weeks for s in w["sessions"] if s["id"] == "downhill")
    wk = [DH.weeks_out(RACE, date.fromisoformat(d) - dt.timedelta(days=date.fromisoformat(d).weekday())) for d in days]
    assert wk == list(range(9, 2, -1))
    assert (RACE - date.fromisoformat(days[-1])).days >= 14
    assert not any(s["id"] == "downhill" for w in weeks if w["phase"] == "taper" for s in w["sessions"])
    # a road A race: none
    ds, plan, wp = _week(RACE.isoformat(), {4: LIGHT})
    plan.events[0].kind = "road"
    weeks = P.project_weeks(wp, _phases(plan, TODAY), RACE, events=plan.events)
    assert not any(s["id"] == "downhill" for w in weeks for s in w["sessions"])


def test_base_phase_every_other_week_a_gentle_dose():
    """SP-87: a trail athlete's 基礎期 (an A race 25 weeks out) — a gentle downhill (−5～−10 %, 10–15′
    downhill, RPE 3–5) every even ISO week, none in the odd ones; week_plan and the projection alike."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    far = date(2027, 3, 27)
    ds = _history(TODAY)
    plan = _plan_with(far.isoformat(), 1, TODAY)
    plan.events = [Event("e1", "春季越野", far.isoformat(), kind="race", priority="A", distance_km=50,
                         climbing_m=3000, est_hours=8.0)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY, sport="trail")                    # week of 9/28 = ISO 40 (even)
    assert wp["phase"] == "base" and wp["downhill"]["active"]
    d0 = next(s for s in wp["sessions"] if s["id"] == "downhill")
    assert d0["kind"] == "easy" and "緩下坡" in d0["title"] and "−5～−10%" in d0["detail"]
    work = d0["steps"]["items"][1]["dur"]["value"] / 60
    assert work in (DH.BASE_FIRST_WORK, DH.BASE_WORK)
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 12, 20), events=plan.events)
    base = [w for w in weeks if w["phase"] == "base" and w["mode"] != "reentry"]
    assert len(base) >= 4
    for w in base:
        even = date.fromisoformat(w["start"]).isocalendar()[1] % 2 == 0
        n = sum(1 for s in w["sessions"] if s["id"] == "downhill")
        assert n == (1 if even else 0), w["start"]


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


def test_no_downhill_on_a_race_s_post_race_days(monkeypatch):
    """Integration SP-98 / SP-95 × SP-99: a B race with a big downhill for the athlete on the Thursday
    before 賽前第 6 週 — its 7 easy days (the 72 h flat days inside them) take no downhill session; the
    week's Sunday, after them, can."""
    from backend.engine import planning as PL
    monkeypatch.setattr(PL, "RACE_DOWNHILL_OF", lambda e: 30.0)
    monkeypatch.setattr(PL, "ATHLETE_DOWNHILL_OF", lambda d: 10.0)
    m6 = RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=5)
    b = Event(id="b1", name="B 越野", date=(m6 - dt.timedelta(days=4)).isoformat(), kind="race", priority="B",
              distance_km=21, climbing_m=1500, est_hours=3.5)
    info = ctx(m6, events=[ev(), b])
    rp = PL.recovery_plan(b, b=True)
    assert rp["downhill"]["big"]
    last = max(rp["days"], PL.DOWNHILL_FLAT_DAYS)
    assert info["post_race"] == [(b.end + dt.timedelta(days=k)).isoformat() for k in range(1, last + 1)
                                 if b.end + dt.timedelta(days=k) >= m6]
    ss = _ss(m6)
    DH.apply(ss, {**info, "first": False})
    d = [s for s in ss if s["id"] == "downhill"]
    assert all(s["day"] not in info["post_race"] for s in d)
    assert d and d[0]["day"] > info["post_race"][-1]


def test_illness_wins_over_the_downhill_session(monkeypatch):
    """Integration SP-117 × SP-99 (week_plan's pass order): the illness rules run last — a cold this
    week turns the countdown week's downhill session into a Z1 recovery run like every other run."""
    from backend.engine import injuries as INJ
    race = date(2026, 11, 28)                                          # 賽前第 9 週 this week
    ds, plan, wp = _week(race.isoformat(), {4: LIGHT})
    assert wp["downhill"]["active"] and any(s["id"] == "downhill" for s in wp["sessions"])
    cold = {"id": 1, "category": "illness", "illness": "cold", "status": "active", "onset_date": "2026-09-28",
            "resolved_date": None, "area": "unknown", "side": None, "severity": "mild"}
    monkeypatch.setattr(INJ, "load_events", lambda *a, **k: [cold])
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    left = [s for s in wp["sessions"] if not s["done"] and s["day"] and s["day"] >= TODAY.isoformat()
            and s["kind"] in O.RUN_KINDS]
    assert left and all(s["title"] == "恢復跑（心率 1 區）" for s in left)
    assert not any(s["id"] == "downhill" and s["title"].startswith("下坡離心") for s in wp["sessions"]
                   if not s["done"] and s["day"] and s["day"] >= TODAY.isoformat())


def test_a_downhill_session_already_behind_blocks_a_second_one():
    """SP-87 (plan_store.hook_days): this week's stored downhill session already behind (done, missed,
    or today's with a run logged) → no second one that week."""
    m6 = RACE - dt.timedelta(days=RACE.weekday()) - dt.timedelta(weeks=5)
    c = ctx(m6, behind=(m6 + dt.timedelta(days=1)).isoformat())
    assert not c["active"] and "已經排在" in c["why"]
    assert ctx(m6, behind=None)["active"]
