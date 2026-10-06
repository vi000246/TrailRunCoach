"""
SP-239: the trail HR model's durability decline is linear, then flat at TRAILHR["v_floor"]
(engine/racepower/trailhr.py speed_mult / dist_nodecay / dbar / time_for_nodecay;
docs/research/long-race-durability-shape.md §4.1). Before, the line ran on to 0 and below and
D̄ hit its 0.5 guard, so any course over ~11.3 h of no-decay time (δ 0.05) came out at exactly 2×,
with jumps around 11 h. Synthetic numbers only: no WKO5 folder, no app DB.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from pytest import approx

from backend.engine import race_feasibility as F
from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.engine.racepower import trailhr as TH
from backend.tests.test_racepower_fade import _rows
from backend.tests.test_racepower_v2 import RE0, fake_v1, synthetic_track

V1 = {"kind": "proportional", "c": 1.0, "delta": 0.05}     # v₀ = 1 effort-km/h at x = 1: E = no-decay hours


def old_dbar(T, d, t0=1.0):
    """The pre-SP-239 straight line (for the ≤ 5 h comparison)."""
    if T <= t0 or d <= 0:
        return 1.0
    return max(0.5, 1.0 - d * (T - t0) ** 2 / (2.0 * T))


def old_time(tn, d):
    T = tn
    for _ in range(200):
        T2 = tn / old_dbar(T, d)
        if abs(T2 - T) < 1e-9:
            break
        T = T2
    return T


def t_of(tn, d):
    return TH.predict_time(V1, tn, 1.0, delta=d) / 3600.0


def test_the_floor_constant_and_the_shape():
    assert TH.TRAILHR["v_floor"] == 0.80
    assert TH.speed_mult(0.5, 0.05) == 1.0 and TH.speed_mult(3.0, 0.05) == approx(0.90)
    assert TH.speed_mult(5.0, 0.05) == approx(0.80) and TH.speed_mult(30.0, 0.05) == approx(0.80)
    assert TH.speed_mult(30.0, 0.0) == 1.0


@pytest.mark.parametrize("d", [0.03, 0.05, 0.10, 0.15])
def test_dbar_is_the_mean_of_the_linear_then_floor_speed(d):
    """The closed form = the numerical mean of max(f_min, 1 − δ(t − T0)⁺), across the bend at t1."""
    for T in (0.7, 1.0, 2.0, 3.5, 5.0, 8.0, 12.0, 24.0, 40.0):
        t = np.linspace(0.0, T, 200001)
        num = float(np.trapezoid([TH.speed_mult(x, d) for x in t[::100]], t[::100]) / T)
        assert TH.dbar(T, d) == approx(num, abs=2e-5)
        assert TH.dbar(T, d) >= TH.TRAILHR["v_floor"] - 1e-12
    t1 = 1.0 + (1.0 - 0.8) / d
    assert TH.dbar(t1, d) == approx(1.0 - d * (t1 - 1.0) ** 2 / (2 * t1))       # same as before up to t1


@pytest.mark.parametrize("d", [0.03, 0.05, 0.10, 0.15])
def test_predicted_time_is_continuous_and_increasing_from_1_to_40_h(d):
    """SP-239 acceptance: no-decay time 1 … 40 h → the prediction is continuous and strictly
    increasing, and nowhere the old no-decay ÷ 0.5."""
    tn = np.arange(1.0, 40.0 + 1e-9, 0.01)
    T = np.array([t_of(x, d) for x in tn])
    step = np.diff(T)
    assert (step > 0).all()
    # continuous: a 0.01 h step in no-decay time moves T by at most 0.01 / f_min (the slowest speed)
    assert step.max() <= 0.01 / TH.TRAILHR["v_floor"] + 1e-9
    ratio = T / tn
    assert (np.abs(ratio - 2.0) > 0.5).all() and ratio.max() <= 1.0 / TH.TRAILHR["v_floor"] + 1e-9
    # it solves the race: distance covered in T (no-decay hours) = the course
    for x, y in zip(tn[::97], T[::97]):
        assert TH.dist_nodecay(y, d) == approx(x, rel=1e-12)
        assert y * TH.dbar(y, d) == approx(x, rel=1e-12)


@pytest.mark.parametrize("d", [0.03, 0.05])
def test_up_to_5_h_the_prediction_barely_changes(d):
    """SP-239 acceptance: no-decay ≤ 5 h → < 0.5 % change (at the prior δ and below; the floor is
    reached at 1 + 0.2/δ h, 5 h at δ 0.05)."""
    for tn in np.arange(0.5, 5.0 + 1e-9, 0.05):
        assert abs(t_of(tn, d) / old_time(tn, d) - 1.0) < 0.005


def test_the_old_doubling_and_jump_are_gone():
    # δ 0.05: 10.5 h → 16.5 h, 11.5 h → 23 h before; now about 12.4 and 13.6
    assert old_time(11.5, 0.05) == approx(23.0, rel=1e-6)
    assert t_of(10.5, 0.05) == approx(12.375, abs=0.01) and t_of(11.5, 0.05) == approx(13.625, abs=0.01)
    assert t_of(12.0, 0.05) == approx(14.25, abs=0.01)                    # research table §4.1
    assert t_of(20.0, 0.05) == approx(24.25, abs=0.01)
    # SP-239 acceptance: δ 0.15 (the clamp) — a 6 h route is no longer 12 h
    assert old_time(6.0, 0.15) == approx(12.0, rel=1e-6)
    assert t_of(6.0, 0.15) == approx(7.083, abs=0.01) and t_of(6.0, 0.15) < 8.0


def test_fit_corrects_long_training_runs_with_the_new_shape():
    """SP-239 acceptance: v₀ fit (v / D̄(T)) uses the linear-then-floor D̄, so runs made on a known
    line v₀ = 2 + 4x with the new shape — some far past the floor — give the line back exactly and
    the prediction reproduces each run."""
    pts = []
    for i, x in enumerate(np.linspace(0.75, 1.0, 8)):
        T = 1.5 + 2.0 * i                              # 1.5 … 15.5 h, most past t1 = 5 h
        v = (2.0 + 4.0 * x) * TH.dbar(T, 0.05)
        pts.append({"x": float(x), "v": v, "T_h": T, "eff_km": v * T})
    m = TH.fit(pts, 0.05)
    assert m["kind"] == "ols" and m["a"] == approx(2.0, abs=1e-9) and m["b"] == approx(4.0, abs=1e-9)
    for p in pts:
        assert TH.predict_time(m, p["eff_km"], p["x"]) == approx(p["T_h"] * 3600.0, rel=1e-9)
    # the 15.5 h run is corrected by the floor shape (D̄ = 13 / 15.5 ≈ 0.84), not the old line (≈ 0.66)
    assert pts[-1]["v"] / (2.0 + 4.0 * pts[-1]["x"]) == approx(TH.dbar(15.5, 0.05))
    assert TH.dbar(15.5, 0.05) == approx(13.0 / 15.5, abs=1e-12) and old_dbar(15.5, 0.05) == approx(0.6609, abs=1e-3)


def test_predict_race_on_the_x_star_curve_is_continuous_for_long_races():
    pts = [{"x": x, "v": 2 + 4 * x, "T_h": 2.0, "eff_km": 10.0} for x in np.linspace(0.8, 1.2, 8)]
    m = TH.fit(pts, 0.05)
    xs = TH.xstar_prior()
    E = np.arange(20.0, 200.0, 0.5)
    T = np.array([TH.predict_race(m, e, xs)[0] for e in E]) / 3600.0
    Tn = np.array([TH.predict_race(m, e, xs, delta=0.0)[0] for e in E]) / 3600.0
    assert (np.diff(T) > 0).all() and np.diff(T).max() < 0.5 / (5.0 * 0.8)   # no jump
    assert (T / Tn).max() < 1.0 / 0.8 + 1e-6 and T.max() > 24.0


# ---- SP-222: the segment ETAs sum to the (new) whole-race time on a long race ------------------

def _long_plan(fade):
    tr = synthetic_track({"len": 90000, "z": lambda x: 300 + 200 * math.sin(x / 4000.0)}, step_m=25.0)
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 90.0, c["totals"]["gain_m"])
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9,
         "fade": fade}
    return c, m, PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(RE0), effort_validated=False,
                             opts={"mode": "auto"}, validated={"trail": True}, trail_hr=m)


@pytest.mark.parametrize("fade", [None, "own"])
def test_long_race_segment_etas_sum_to_the_race_prediction(fade):
    """A > 12 h trail race: the whole-race time is the linear-then-floor model's (not 2× the
    no-decay time), and the segment times — evenly spread, or by the athlete's own fade
    (SP-222) — add up to it within a second."""
    c, m, p = _long_plan(TH.fade_shape(_rows(6)) if fade else None)
    s = p["summary"]
    e = c["totals"]["km"] + c["totals"]["gain_m"] / TH.effort_divisor()
    th = s["trail_hr"]
    assert s["total_method"] == "trail_hr" and th["time_s"] == approx(TH.predict_time(m, e, 1.0), abs=1e-6)
    tn = TH.predict_time(m, e, 1.0, delta=0.0)
    assert th["time_no_durability_s"] == approx(tn, abs=1e-6) and tn > 11.5 * 3600.0
    assert th["time_s"] > 12 * 3600.0 and th["time_s"] / tn < 1.25 and abs(th["time_s"] / tn - 2.0) > 0.5
    # the planner's whole-race time = the HR model's ÷ the course's altitude M (close to 1 here)
    assert s["time_s"] == approx(th["time_s"], rel=0.02)
    assert abs(sum(x["t"] for x in p["segments"]) - s["time_s"]) < 1.0
    assert p["segments"][-1]["cum_s"] == approx(s["time_s"], abs=1.0)
    assert bool(s.get("fade") and s["fade"].get("applied")) is bool(fade)


# ---- SP-220: a long race's cutoff is not missed by the old 2× artifact -------------------------

def test_long_trail_race_is_not_flagged_over_the_cutoff_by_the_old_doubling():
    """A 14 h-no-decay course with a 24 h cutoff: the old line said 28 h (over); the floor model says
    ≈ 16.75 h → 70 % of the cutoff, 「ok」. The moving time is the planner's trail_hr_estimate, as
    trail_finish() reads it from POST /plan."""
    from backend.tests.test_race_feasibility import TODAY, ev, fin, hist, line
    m = {"kind": "proportional", "c": 5.0, "delta": 0.05, "x_race": 1.0, "n": 9}
    est = PL.trail_hr_estimate(m, 70.0, 0.0)                     # 70 effort-km at 5 /h: 14 h no-decay
    assert est["time_no_durability_s"] == approx(14 * 3600.0)
    assert old_time(14.0, 0.05) == approx(28.0, rel=1e-6)          # what SP-220 would have used before
    mv = est["time_s"] / 3600.0
    assert mv == approx(16.75, abs=0.01)
    e = ev(cutoff_hours=24.0, distance_km=70, climbing_m=0, est_hours=16.0)
    r = F.assess(e, line(e, [16.0]), TODAY, hist(km=60.0, climb=1000.0, hours=10.0), finish=fin(mv))
    c = next(c for c in r["checks"] if c["id"] == "cutoff")
    assert c["level"] == "ok" and c["moving_h"] == approx(16.75, abs=0.01)
    r = F.assess(e, line(e, [16.0]), TODAY, hist(km=60.0, climb=1000.0, hours=10.0), finish=fin(28.0))
    assert next(c for c in r["checks"] if c["id"] == "cutoff")["level"] == "over"   # the old artifact
