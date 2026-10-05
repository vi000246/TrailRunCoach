"""
SP-120: the 平衡／腳踝 mini-session (engine/balance_plan.py) — only before a 越野賽 / 百岳 A race,
2–3 a week, stage 1 → 2 → 3 by the weeks since the cycle's start, stage 4 (pack) in the 專項期,
2 a week after 12 weeks; kept in the 減量期 and race week (not strength: SP-86 doesn't drop it),
stage 1–2 only in the last 7 days; never pushed to the watch. Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import balance_plan as BP
from backend.engine import overview as O
from backend.engine import plan_match as PM
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.engine.planning import Event
from backend.sync import coros_workouts as CW
from backend.tests.test_b2b import _phases
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30
from backend.tests.test_strength_plan import _ev, _week


def _bal(ss):
    return [s for s in ss if s["kind"] == "balance"]


def _ph(*rows):
    return [{"kind": k, "start": s, "end": e} for k, s, e in rows]


# a race 2027-01-16 after an earlier one: 恢復期, 轉換期 from 9/07, 基礎期, 專項期 11/07–1/01, 減量期
PH = _ph(("event", "2026-08-29", "2026-08-29"), ("recovery", "2026-08-30", "2026-09-06"),
         ("transition", "2026-09-07", "2026-09-27"), ("base", "2026-09-28", "2026-11-06"),
         ("specific", "2026-11-07", "2027-01-01"), ("taper", "2027-01-02", "2027-01-15"),
         ("event", "2027-01-16", "2027-01-16"))
RACE = [_ev("2027-01-16")]


def test_stage_follows_the_weeks_since_the_cycle_start():
    mon = date(2026, 9, 7)
    got = [BP.week_context(RACE, PH, mon + dt.timedelta(weeks=i),
                           "transition" if i < 3 else "base")["stage"] for i in range(8)]
    assert got == [1, 1, 1, 2, 2, 2, 3, 3]
    sp = BP.week_context(RACE, PH, date(2026, 11, 9), "specific")
    assert sp["stage"] == 4 and sp["weeks_in"] == 10 and sp["n"] == 3
    # after 12 weeks: 2 a week to keep it
    late = BP.week_context(RACE, PH, date(2026, 11, 30), "specific")
    assert late["weeks_in"] == 13 and late["n"] == 2 and late["stage"] == 4
    assert BP.week_context(RACE, PH, date(2027, 1, 4), "taper")["stage"] == 4
    # not in the 恢復期 / race days
    assert not BP.week_context(RACE, PH, date(2026, 8, 31), "recovery")["active"]


def test_an_open_ended_base_counts_from_the_first_planned_session():
    ph = _ph(("base", "2025-09-01", "2026-11-06"), ("specific", "2026-11-07", "2027-01-01"),
             ("taper", "2027-01-02", "2027-01-15"), ("event", "2027-01-16", "2027-01-16"))
    mon = date(2026, 9, 28)
    assert BP.week_context(RACE, ph, mon, "base")["stage"] == 1                         # starts now
    c = BP.week_context(RACE, ph, mon, "base", start=date(2026, 9, 2))
    assert c["weeks_in"] == 5 and c["stage"] == 2 and c["start"] == "2026-08-31"


def test_only_a_trail_or_baiyue_a_race():
    mon = date(2026, 10, 5)
    assert BP.week_context([_ev("2027-01-16", "baiyue")], PH, mon, "base")["active"]
    assert not BP.week_context([_ev("2027-01-16", "road")], PH, mon, "base")["active"]
    assert not BP.week_context([_ev("2027-01-16", "other")], PH, mon, "base")["active"]
    assert not BP.week_context([], PH, mon, "base")["active"]


def test_session_text_warm_up_sources_and_estimates():
    s = BP.session(1, "2026-10-01")
    assert s["kind"] == "balance" and s["minutes"] == 12 and s["tss"] == 0.0
    assert s["title"] == "平衡／腳踝 12 分（階段 1）"
    assert s["detail"].startswith("熱身 1–2 分：承重式腳踝活動度") and "內翻、外翻（徐國峰）" in s["detail"]
    assert "2 組 × 20–40 秒／腳" in s["detail"] and "單腳站（張眼）" in s["detail"] and "推估" in s["detail"]
    for src in ("Schiftan 2015", "Hupperets 2009", "Lesinski 2015", "徐國峰", "推估"):
        assert src in s["source"]
    assert "背包 5–10 % 體重" in BP.session(4, None)["detail"]
    assert "賽前 7 天" in BP.session(2, None, race_week=True)["detail"]


def test_days_easy_runs_first_spread_never_the_race_or_after():
    mon = date(2026, 10, 5)
    ss = [{"id": "easy1", "kind": "easy", "day": "2026-10-06"}, {"id": "quality", "kind": "quality", "day": "2026-10-07"},
          {"id": "easy2", "kind": "easy", "day": "2026-10-08"}, {"id": "long", "kind": "long", "day": "2026-10-10"}]
    days = [mon + dt.timedelta(days=i) for i in range(7)]
    ctx = {"active": True, "stage": 2, "n": 3, "race_start": "2026-12-01"}
    BP.apply(ss, ctx, days)
    got = [s["day"] for s in _bal(ss)]
    assert got == ["2026-10-06", "2026-10-08", "2026-10-11"]          # Tue / Thu easy days, then a free Sun
    # 課表偏好 可練日 and the race day
    ss = [{"id": "easy1", "kind": "easy", "day": "2026-10-06"}]
    BP.apply(ss, {**ctx, "race_start": "2026-10-09"}, days, allowed=lambda d: d.weekday() != 0)
    got = [s["day"] for s in _bal(ss)]
    assert got and all("2026-10-06" <= d < "2026-10-09" for d in got)
    # 5 and 3 days out: stage 1–2 only, with the race-week line
    assert all("階段 2" in s["title"] and "賽前 7 天" in s["detail"] for s in _bal(ss))


def test_week_plan_trail_race_three_sessions_road_none():
    _plan, wp = _week([_ev("2027-01-02")])
    bs = _bal(wp["sessions"])
    assert len(bs) == 3 and all(s["title"] == "平衡／腳踝 12 分（階段 1）" for s in bs)
    assert len({s["day"] for s in bs}) == 3 and all(s["day"] >= TODAY.isoformat() for s in bs)
    assert wp["balance"]["active"] and wp["balance"]["n"] == 3
    # the run minutes are untouched: same easy runs as a road race week
    _plan, wr = _week([_ev("2027-01-02", "road")])
    assert not _bal(wr["sessions"]) and not wr["balance"]["active"]
    _plan, wn = _week([])
    assert not _bal(wn["sessions"])


def test_taper_and_race_week_keep_it_strength_stops():
    # trail A race Sat 10/17: 減量期 from 10/03; the projection's race week is stage 1–2, no strength
    plan, wp = _week([_ev("2026-10-17")])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18))
    by = {w["start"]: w for w in weeks}
    w1, w2 = by["2026-10-05"], by["2026-10-12"]
    assert w1["phase"] == w2["phase"] == "taper"
    assert not [s for s in w1["sessions"] + w2["sessions"] if s["kind"] == "strength"]     # SP-86
    b1, b2 = _bal(w1["sessions"]), _bal(w2["sessions"])
    assert len(b1) == 3 and len(b2) == 3
    for s in b1 + b2:
        late = (date(2026, 10, 17) - date.fromisoformat(s["day"])).days <= 7
        assert ("階段 4" in s["title"]) != late and (("賽前 7 天" in s["detail"]) == late)
    assert all(s["day"] < "2026-10-17" for s in b2)
    # a 12-minute session is not training hours
    assert O.drop_strength_before_a(b2, wp["strength_stop"], date(2026, 10, 12)) == b2


def test_projection_stage_switches_and_specific_gets_the_pack():
    # trail A race 1/02 (專項期 from 10/24); the count starts this week (no stored session)
    plan, wp = _week([_ev("2027-01-02")])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 22))
    st = {w["start"]: {s["title"][-5:] for s in _bal(w["sessions"])} for w in weeks}
    assert st["2026-10-12"] == {"階段 1）"} and st["2026-10-19"] == {"階段 2）"}
    assert st["2026-10-26"] == {"階段 4）"}
    assert all(len(_bal(w["sessions"])) == 3 for w in weeks)


def test_never_pushed_or_matched():
    s = BP.session(1, "2026-10-01")
    with pytest.raises(CW.Unsupported):
        CW.session_steps(s, CW.Thresholds(cp=250.0, lthr=165.0, aet=140.0))
    assert "balance" in PM.NEVER
    assert PP.Prefs is not None
