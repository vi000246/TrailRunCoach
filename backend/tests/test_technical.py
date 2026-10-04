"""
SP-74: 技術地形課 in the generated plan (engine/technical.py) — 基礎期 every other week's LSD as a
low-RPE technical run, 專項期 one session a week as a quality session (RPE 6–7: 48 h spacing, the
week's 20 % budget) or, without room, an easy one (RPE 4–5); road athletes get none. Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import technical as T
from backend.engine import workout_steps as WS
from backend.engine import workout_templates as WT

MON = date(2026, 10, 5)            # ISO week 41 (odd)
EVEN = date(2026, 10, 12)          # ISO week 42


def d(i, mon=MON):
    return (mon + dt.timedelta(days=i)).isoformat()


def test_week_context_rules():
    on = dict(kind="specific", mode="specific", monday=MON, road=False)
    assert T.week_context(**on)["active"]
    assert not T.week_context(**{**on, "road": True})["active"]                    # 路跑: none
    assert not T.week_context(**{**on, "mode": "recovery_week"})["active"]
    assert not T.week_context(**{**on, "mode": "reentry"})["active"]
    assert not T.week_context(**{**on, "kind": "taper", "mode": "taper"})["active"]
    assert not T.week_context(**{**on, "kind": "transition", "mode": "transition"})["active"]
    base = dict(kind="base", mode="base", road=False)
    assert T.week_context(**base, monday=EVEN)["active"]
    assert not T.week_context(**base, monday=MON)["active"]                        # every other week
    assert not T.week_context(**base, monday=EVEN, b2b={"due": True})["active"]


def _week(mon=MON, q_day=1, long_min=150, easy=(50, 60, 50), q_title="閾值 2×20 分"):
    ss = [{"id": "long", "kind": "long", "day": d(5, mon), "minutes": long_min, "tss": 120.0, "title": "LSD"},
          {"id": "quality", "kind": "quality", "day": d(q_day, mon), "minutes": 70, "tss": 70.0, "title": q_title}]
    for i, (m, day) in enumerate(zip(easy, (2, 3, 6))):
        ss.append({"id": f"easy{i + 1}", "kind": "easy", "day": d(day, mon), "minutes": m, "tss": m * 0.9,
                   "title": "輕鬆跑"})
    ss.append({"id": "strength1", "kind": "strength", "day": d(2, mon), "minutes": 35, "tss": 20.0, "title": "肌力"})
    return ss


def test_base_long_becomes_low_rpe_technical_of_the_same_time():
    info = T.week_context(kind="base", mode="base", monday=EVEN, road=False)
    ss, notes = _week(EVEN), []
    T.apply(ss, info, hours=7.0, rates={"trail": 60.0}, notes=notes)
    s = next(x for x in ss if x["id"] == "long")
    assert s["kind"] == "long" and s["minutes"] == 150 and s["day"] == d(5, EVEN)   # same slot, same time
    assert s["title"] == "技術地形 150′（低 RPE 3–4）" and s["terrain"] == "trail" and s["target"] == ""
    assert WS.rpe_role(s["steps"]["items"]) == "easy" and WT.session_role(s) == "easy"
    assert WS.normalize(s["steps"])["items"][1]["target"]["type"] == "rpe"
    assert s["climb_m"] and s["tss"] == pytest.approx(150.0)
    assert info["planned"][0]["replaces"] == "long"
    assert any(n["src"] == "technical" and "隔週" in n["text"] for n in notes)
    # done / already technical: untouched
    done = _week(EVEN)
    done[0]["done"] = True
    T.apply(done, T.week_context(kind="base", mode="base", monday=EVEN, road=False))
    assert done[0]["title"] == "LSD"


def test_specific_quality_role_spaced_and_within_the_budget():
    info = T.week_context(kind="specific", mode="specific", monday=MON, road=False)
    ss, notes = _week(), []
    before = sum(x["minutes"] for x in ss if x["kind"] != "strength")
    T.apply(ss, info, hours=8.0, rates={"trail": 60.0}, notes=notes)
    s = next(x for x in ss if x["id"] == "tech")
    assert s["kind"] == "hike" and WT.session_role(s) == "quality"                 # RPE 6–7
    day = date.fromisoformat(s["day"])
    for h in (date.fromisoformat(d(1)), date.fromisoformat(d(5))):                 # quality Tue, long Sat
        assert abs((day - h).days) >= 2
    # 20 % of 8 h = 96 min − the interval's 40 → 56 → 55′ of RPE 6–7 work
    work = s["steps"]["items"][1]["dur"]["value"] / 60
    assert work == 55 and s["minutes"] == 55 + 25
    assert sum(x["minutes"] for x in ss if x["kind"] != "strength") == before     # other easy runs gave it
    assert all(x["minutes"] >= T.EASY_MIN for x in ss if x["kind"] == "easy")
    assert info["planned"][0]["role"] == "quality" and "強度預算" in notes[0]["text"]
    # a big week: capped at the template's 90′
    big = _week()
    T.apply(big, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=14.0)
    assert next(x for x in big if x["id"] == "tech")["steps"]["items"][1]["dur"]["value"] == 90 * 60


def test_specific_without_budget_or_spacing_is_kept_easy():
    # the intervals fill the 20 %: 20 % of 4 h = 48 − 40 → 8 < SPEC_WORK_MIN
    ss, notes = _week(), []
    info = T.week_context(kind="specific", mode="specific", monday=MON, road=False)
    T.apply(ss, info, hours=4.0, notes=notes)
    s = next(x for x in ss if x["id"] == "tech")
    assert WT.session_role(s) == "easy" and s["title"].endswith("（RPE 4–5）")
    assert "強度預算不夠" in notes[0]["text"] and info["planned"][0]["role"] == "easy"
    # quality on Thu: no easy day ≥ 2 days from it and the Sat long run
    ss, notes = _week(q_day=3), []
    T.apply(ss, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=8.0, notes=notes)
    s = next(x for x in ss if x["id"] == "tech")
    assert WT.session_role(s) == "easy" and "≥ 2 天" in notes[0]["text"]
    # a hard run already done on Wed (hard_done) blocks Wed too
    ss = _week()
    T.apply(ss, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=8.0,
            hard_done=[date.fromisoformat(d(3))])
    s = next(x for x in ss if x["id"] == "tech")
    assert WT.session_role(s) == "easy"


def test_specific_respects_the_weekday_cap():
    from backend.engine import plan_prefs as PP
    # weekday cap 60: Thu's work = 60 − 15 − 10 = 35′ (the budget would allow 55′)
    ss = _week()
    T.apply(ss, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=8.0,
            prefs=PP.Prefs(cap_weekday=60, cap_long=200))
    s = next(x for x in ss if x["id"] == "tech")
    assert s["day"] == d(3) and s["minutes"] == 60 and WT.session_role(s) == "quality"
    # cap 50: < 30′ of work fits → kept easy on the easy run's time, the note says why
    ss, notes = _week(), []
    T.apply(ss, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=8.0,
            prefs=PP.Prefs(cap_weekday=50, cap_long=200), notes=notes)
    s = next(x for x in ss if x["id"] == "tech")
    assert WT.session_role(s) == "easy" and "時間上限" in notes[0]["text"]


def test_week_plan_and_projection_trail_vs_road():
    """A trail athlete: 基礎期 even ISO weeks' LSD is technical, odd weeks plain, in week_plan and
    in the projection alike; 專項期 one tech session a week; road athletes none."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as PJ
    from backend.engine.planning import Event
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = []
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY, sport="trail")                    # week of 9/28 = ISO 40 (even)
    lg = next(s for s in wp["sessions"] if s["id"] == "long")
    assert lg["title"].startswith("技術地形") and lg["steps"] and wp["technical"]["planned"]
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8))
    for w in weeks:
        long_s = next((s for s in w["sessions"] if s["id"] == "long"), None)
        if long_s is None:
            continue
        even = date.fromisoformat(w["start"]).isocalendar()[1] % 2 == 0
        assert long_s["title"].startswith("技術地形") == even, w["start"]
    road = O.week_plan(ds, st, TODAY, sport="road")
    assert not road["technical"]["active"] and not any("技術地形" in s["title"] for s in road["sessions"])
    for w in PJ.project_weeks(road, _phases(plan, TODAY), date(2026, 11, 8)):
        assert not any("技術地形" in s["title"] for s in w["sessions"]) and "technical" not in w
    # 專項期 (a trail A race 12/5): one tech session in each 專項期 build week, never two
    plan.events = [Event("e1", "越野賽", "2026-12-05", kind="race", priority="A", distance_km=30, climbing_m=2000,
                         est_hours=5.0)]
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY, sport="trail")
    assert wp["phase"] == "specific"
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8))
    built = [w for w in weeks if w["mode"] == "specific"]
    assert built and all(sum(1 for s in w["sessions"] if s["id"] == "tech") == 1 for w in built)
    for w in built:
        t = next(s for s in w["sessions"] if s["id"] == "tech")
        if WT.session_role(t) == "quality":
            hard = [date.fromisoformat(s["day"]) for s in w["sessions"] if s["kind"] in ("quality", "long")]
            assert all(abs((date.fromisoformat(t["day"]) - h).days) >= 2 for h in hard)
        assert any(n.get("src") == "technical" for n in w["notes"])
    assert any(WT.session_role(next(s for s in w["sessions"] if s["id"] == "tech")) == "quality" for w in built)
