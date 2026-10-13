"""
SP-87 acceptance 8: a road athlete's plan (主要訓練項目 路跑) is unchanged — no 長爬坡有氧, no downhill
session, no 技術地形 session, no weekly climb target, in the 基礎期 and before a road A race, in
week_plan and the projection alike. Synthetic.
"""
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.engine import vert_target as VT
from backend.engine.planning import Event
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY

TRAIL_IDS = ("climb_base", "downhill", "tech")
TRAIL_WORDS = ("長爬坡有氧", "下坡", "技術地形")


@pytest.mark.parametrize("events", [
    [],                                                                       # 基礎期, no race
    [Event("m1", "台北馬", "2026-12-05", kind="road", priority="A", distance_km=42.2, climbing_m=150,
           est_hours=3.8)],                                                   # 專項期 before a marathon
    [Event("t1", "越野賽", "2027-03-27", kind="race", priority="A", distance_km=50, climbing_m=3000,
           est_hours=8.0, trail_pct=90)],                                     # a trail race, the athlete set 路跑
])
def test_a_road_athlete_gets_none_of_the_trail_sessions(events):
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = list(events)
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY, sport="road")
    assert wp["primary_sport"] == "road"
    for k in ("base_climb", "technical"):
        assert not wp[k]["active"] and wp[k]["why"] == "主要訓練項目：路跑", k
    assert "downhill" not in wp                                               # SP-442: no separate 下坡課
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 29), events=plan.events)
    assert weeks
    for w in [wp] + weeks:
        for s in w["sessions"]:
            assert s["id"] not in TRAIL_IDS and not any(x in (s.get("title") or "") for x in TRAIL_WORDS), s
        assert not any(k in w for k in ("base_climb", "downhill", "technical")) or w is wp
    assert VT.annotate(wp, weeks, plan.events, []) == {}
