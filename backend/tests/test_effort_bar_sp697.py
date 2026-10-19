"""
SP-697 — what the race calculator's 努力度 shows:

trail 「模型預測」 with the HR model: the total comes from the HR level picked × the all-out race HR,
so 努力度 is that level (95 % picked → 95 %); the power ratio (the old number) moves to effort.power
for the ? help. Target pace / power (and plans without the HR model) keep the power ratio.
(The bar's drag rules and the recompute — debounce, abort, one busy line — are in the page:
backend/tests/e2e/test_racepower_effort_sp697_e2e.py.)
"""
from __future__ import annotations

import math

from pytest import approx

from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.tests.test_racepower_v2 import RE0, fake_v1, synthetic_track

HR_MODEL = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9}


def _plan(mode="auto", effort=1.0, trail_hr=HR_MODEL, **opts):
    tr = synthetic_track({"len": 20000, "z": lambda x: 300 + 200 * math.sin(x / 2500.0)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 20.0, c["totals"]["gain_m"])
    return PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(RE0), effort_validated=False,
                       opts={"mode": mode, "effort_target": effort, **opts}, validated={"trail": True}, trail_hr=trail_hr)


def test_effort_basis_keeps_the_power_ratio_for_the_help():
    eff = {"f": 0.88, "key": "steady", "label": "穩定", "multiple": 3.1, "t_lim_s": 30000.0, "band": [0.85, 0.9],
           "p_sus": 250.0, "cuts": [], "badge": "推估"}
    hr = PL.effort_basis(eff, 0.95)
    assert hr["f"] == 0.95 and hr["label"] == "吃力" and hr["basis"] == "hr" and hr["band"] is None
    assert hr["power"]["f"] == 0.88 and hr["power"]["label"] == "穩定" and hr["power"]["multiple"] == 3.1
    assert hr["badge"] == "推估" and hr["cuts"] == []
    pw = PL.effort_basis(eff, None)
    assert pw["f"] == 0.88 and pw["basis"] == "power" and "power" not in pw


def test_trail_model_effort_is_the_hr_level_picked():
    for level, label in ((1.0, "極限"), (0.95, "吃力"), (0.85, "穩定")):
        p = _plan(effort=level)
        assert p["summary"]["total_method"] == "trail_hr"
        e = p["effort"]
        assert e["basis"] == "hr" and e["f"] == approx(level) and e["label"] == label
        assert e["power"]["f"] > 0 and e["power"]["label"]
    slower = _plan(effort=0.85)["summary"]["time_s"]
    assert slower > _plan(effort=1.0)["summary"]["time_s"]          # the level picked still sets the time


def test_target_power_and_pace_keep_the_power_ratio():
    p = _plan(mode="power", target_power=220.0)
    assert p["effort"]["basis"] == "power" and "power" not in p["effort"]
    t = _plan(mode="time", target_time_s=3 * 3600.0)
    assert t["effort"]["basis"] == "power"
