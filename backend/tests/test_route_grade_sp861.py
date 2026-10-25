"""
SP-861 (SP-639 Q1-1 「之後 (b)」): the descents of a template's step chart, the 坡段表 and a route
session by the race calculator's personal grade model (user_templates.GradeCurve) instead of SP-639's
steep-descent rule — the same speed the calculator gives that grade relative to its flat (trail RE(g),
its downhill power, the personal descent cap); climbs stay on Minetti. Without a model of the
athlete's own (no windows) everything is as before.
"""
import dataclasses

import pytest

from backend.api import plan_sessions
from backend.engine import route_session as RS
from backend.engine import user_templates as UT
from backend.engine import workout_steps as WS
from backend.engine.algorithms.minetti import grade_factor
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import pacing as PC
from backend.tests.test_route_library_sp640 import LAPI, LEnv, upload
from backend.tests.test_user_templates import _pin, st  # noqa: F401 (_pin: autouse fixture)

RE0 = 1.0
C = WS.Ctx.of({"cp": 250.0}, "power", speeds={"v_easy": 6.0, "ep_kmh": 6.0, "terrain": "trail"})
TG = {"type": "power", "mode": "pct", "lo": 0.77, "hi": 0.79}          # ≈ 0.78 CP = the easy anchor: 6 km/h
STEEP = -0.20


def model(steep_n=60, steep_re=0.55, v50=2.0, flat_n=100):
    """A trail GaitRE: the athlete's own windows at −20 % (slow: RE 0.55 × the flat, median 2 m/s) and
    on the flat; the other bins Minetti's prior."""
    bins = {GM._bin_of(0.0): {"n": flat_n, "re": RE0, "v90": 4.0, "v50": 3.0}}
    if steep_n:
        bins[GM._bin_of(STEEP)] = {"n": steep_n, "re": RE0 * steep_re, "v90": v50 * 1.5, "v50": v50}
    run = GM.GradeRE(RE0, bins, n_samples=flat_n + steep_n, n_activities=10)
    walk = GM.GradeRE(RE0, {}, walking=True)
    return GM.GaitRE(run=run, walk=walk).for_trail("race")


def ctx(**kw):
    return dataclasses.replace(C, grade=UT.GradeCurve(model(**kw)))


def calc_speeds(gre, lam):
    """The race calculator's segment speeds (pacing.course_time, as planner.plan_run runs it) on a flat
    km then a −20 % km, no strategy ramp: (flat m/s, descent m/s)."""
    segs = [{"dist_m": 1000.0, "grade": 0.0, "M": 1.0}, {"dist_m": 1000.0, "grade": STEEP, "M": 1.0}]
    rows = PC.course_time(lam, segs, PC.RunModel(60.0, gre.re, gre.v_max))["rows"]
    return rows[0]["v"], rows[1]["v"]


@pytest.mark.parametrize("lam", [120.0, 200.0, 400.0])     # below / at / above the personal descent cap
def test_the_descent_speed_is_the_calculators_on_the_same_grade(lam):
    gre = model()
    v_flat, v_down = calc_speeds(gre, lam)
    got = UT.GradeCurve(gre).kmh(v_flat * 3.6, STEEP)
    assert got == pytest.approx(v_down * 3.6, rel=1e-9)
    # and slower than SP-639's rule (the flat speed) — the athlete's own steep descents are slow
    assert got < v_flat * 3.6


def test_climbs_and_flats_stay_on_minetti():
    gc = UT.GradeCurve(model())
    for g in (0.0, 0.05, 0.15):
        assert gc.kmh(6.0, g) == pytest.approx(6.0 / grade_factor(g))


def test_a_steep_descent_with_few_own_windows_is_no_faster_than_the_flat():
    # 5 windows: the bin is mostly Minetti's prior (up to ≈ +11 %) — SP-639's floor stays
    gc = UT.GradeCurve(model(steep_n=5, steep_re=1.3, v50=9.0))
    assert gc.kmh(6.0, STEEP) <= 6.0 + 1e-9
    # with enough of the athlete's own windows saying faster, the model is trusted
    fast = UT.GradeCurve(model(steep_n=60, steep_re=1.3, v50=9.0))
    assert fast.kmh(6.0, STEEP) > 6.0


def test_no_own_windows_no_personal_curve():
    assert UT.grade_curve(None) is None and UT.grade_curve({}) is None
    empty = GM.GaitRE(run=GM.GradeRE(RE0, {}), walk=GM.GradeRE(RE0, {}, walking=True))
    assert UT.grade_curve({"grade_re": empty}) is None
    m = model()
    got = UT.grade_curve({"grade_re": GM.GaitRE(run=m.run, walk=m.walk)})     # the calculator's: made trail here
    assert isinstance(got, UT.GradeCurve) and got.gre.trail and got.gre.tech_class == "race"


def test_the_table_and_the_chart_walk_the_descent_by_the_personal_model():
    c = ctx()
    course = {"segments": [{"i": 0, "start_km": 0.0, "end_km": 1.0, "dist_m": 1000.0, "gain_m": 0.0,
                            "loss_m": 200.0, "grade": STEEP, "max_grade": STEEP, "z_start": 200.0,
                            "z_end": 0.0, "cls": "down"}], "climbs": []}
    lv = [("easy", "easy", c.easy_frac())]
    v = UT._flat_kmh(c.easy_frac(), c)
    want = c.grade.kmh(v, STEEP)
    tb = UT.course_table(course, c, lv)
    seg = tb["segments"][0]["est"]["easy"]
    assert seg["pace_s_per_km"] == pytest.approx(3600.0 / want, rel=1e-3)
    assert seg["t_s"] == pytest.approx(3600.0 / want, abs=1)
    assert tb["down_model"] == "personal" and "個人坡度模型" in tb["note"]
    old = UT.course_table(course, C, lv)
    assert old["down_model"] == "minetti" and old["segments"][0]["est"]["easy"]["pace_s_per_km"] < seg["pace_s_per_km"]
    # the chart: a 1 km distance step down the −20 % takes the same time
    steps = WS.normalize({"items": [st("work", {"type": "distance", "value": 1000}, TG)]})
    el = UT.route_elevation(steps, c, {"km": [0.0, 1.0], "z": [200.0, 0.0], "route_km": 1.0})
    v_step = UT._flat_kmh(WS.resolve(steps["items"][0], c).frac, c)
    assert el["s"][0] == pytest.approx(1.0 / c.grade.kmh(v_step, STEEP) * 3600.0, rel=0.01)
    assert el["down_model"] == "personal" and "個人坡度模型" in el["note"]
    assert UT.route_elevation(steps, C, {"km": [0.0, 1.0], "z": [200.0, 0.0]})["down_model"] == "minetti"


def test_a_route_sessions_descent_step_is_timed_by_the_personal_model():
    prof = {"km": [0.0, 1.0, 2.0], "z": [0.0, 0.0, -200.0]}
    course = {"segments": [
        {"start_km": 0.0, "end_km": 1.0, "grade": 0.0},
        {"start_km": 1.0, "end_km": 2.0, "grade": STEEP}], "climbs": []}
    new = RS.build(course, prof, "r", {"kind": "gpx", "id": 1}, ctx())
    old = RS.build(course, prof, "r", {"kind": "gpx", "id": 1}, C)
    dn, dn_old = new["legs"][-1], old["legs"][-1]
    assert dn["type"] == "down" and dn["est_s"] > dn_old["est_s"]
    assert new["legs"][0]["est_s"] == old["legs"][0]["est_s"]          # the flat as before


def test_the_route_pages_table_uses_it_when_the_calculator_has_a_model(monkeypatch):
    with LEnv(monkeypatch) as e:
        rid = upload(e)["id"]
        monkeypatch.setattr(plan_sessions, "_grade_curve", lambda: None)
        before = e.c.get(f"{LAPI}/{rid}").json()["table"]
        monkeypatch.setattr(plan_sessions, "_grade_curve", lambda: UT.GradeCurve(model()))
        after = e.c.get(f"{LAPI}/{rid}").json()["table"]
        assert before["down_model"] == "minetti" and after["down_model"] == "personal"
        for b, a in zip(before["segments"], after["segments"]):
            if b["grade"] < 0:
                assert a["est"]["easy"]["t_s"] != b["est"]["easy"]["t_s"]
            else:
                assert a["est"]["easy"]["t_s"] == b["est"]["easy"]["t_s"]    # climbs: Minetti, unchanged
        assert any(s["grade"] < 0 for s in after["segments"])
