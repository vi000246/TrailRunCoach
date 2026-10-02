"""
Race-power v2 — docs/research/racepower-v2.md §3A (V-numbered verification
tests) and §10.5 (T1–T15). Each test's docstring names the worked example's
source. No network, no real athlete data.
"""
import math
import random

import numpy as np
import pytest

from backend.engine.algorithms import minetti
from backend.engine.racepower import course as CO
from backend.engine.racepower import difficulty as DF
from backend.engine.racepower import env as ENV
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import hike as HK
from backend.engine.racepower import pacing as PC
from backend.engine.racepower import planner as PL
from backend.engine.racepower import predict as PR
from backend.engine.racepower import riegel as R

approx = pytest.approx
CP, WP, TTE, K, W, RE0 = 300.0, 15000.0, 3000.0, -0.07, 70.0, 1.0
MARATHON = 42195.0


def flat_model(re=RE0, vmax=None):
    gre = GM.GradeRE(re)
    return PC.RunModel(W, gre.re, (lambda g: vmax if g < -0.02 else None) if vmax else gre.v_max), gre


def seg(d, g, m=1.0, **kw):
    return {"dist_m": d, "grade": g, "M": m, **kw}


def hilly():
    return [seg(3000, 0.0), seg(2000, 0.06), seg(1500, 0.12, m=0.98), seg(2500, -0.07), seg(1000, -0.18),
            seg(3000, 0.01)]


# ---- F1 / F18: Stryd's table rebuilt with Riegel D1 --------------------------

STRYD = {0.8: 128.5, 1.6: 116.0, 3: 109.4, 4: 106.1, 5: 103.8, 10: 100.0, 21.1: 94.6, 42.2: 89.9}


def test_V_F1_stryd_table_rows_from_riegel_d1():
    """Stryd Race Power Calculator table (help.stryd.com 6879547): D1 form,
    k −0.07 → 21.1 km 94.5, 42.2 km 89.7 (§2.1.1 independent recomputation)."""
    for km in (21.1, 42.2):
        p = R.power_from_prior_distance(100.0, 10.0, km, -0.07)["power"]
        assert abs(p - STRYD[km]) <= 0.3


def test_V_F18_table_is_a_crosscheck_and_the_linear_formula_is_absent():
    """F18: the table (verbatim) is used; −0.325·km + 103.25 is 不採用."""
    assert PL.stryd_table_power(100.0, 21.1) == approx(94.6)
    assert PL.stryd_table_power(100.0, 42.2) == approx(89.9)
    assert PL.stryd_table_power(100.0, 5.0) is None
    import inspect
    src = inspect.getsource(PL)
    assert "0.325" not in src and "103.25" not in src


# ---- F2–F6 ---------------------------------------------------------------------

def test_V_F2_cp_model():
    """v1 §1.8: 180 s @ 345 W and 720 s @ 300 W → CP 285, W′ 10800;
    p_sus(600) = 285 + 10800/600 = 303."""
    assert DF.p_sus(600, 285, 10800, 3000, K) == approx(303.0)


def test_V_F3_continuity_at_both_ends():
    b = min(DF.SHORT_MAX_S, 0.5 * TTE)
    assert DF.p_sus(b, CP, WP, TTE, K) == approx(CP + WP / b)
    assert DF.p_sus(b * (1 + 1e-9), CP, WP, TTE, K) == approx(CP + WP / b, rel=1e-6)
    assert DF.p_sus(TTE * (1 - 1e-9), CP, WP, TTE, K) == approx(CP, rel=1e-6)
    assert DF.p_sus(TTE, CP, WP, TTE, K) == approx(CP)
    ts = np.geomspace(60, 40000, 200)
    ps = [DF.p_sus(t, CP, WP, TTE, K) for t in ts]
    assert all(a > b_ for a, b_ in zip(ps, ps[1:]))


def test_V_F4_t_lim_round_trip_and_hand_value():
    """285 W / 15 kJ: 105 % CP → 15000 / 14.25 = 1052.6 s (§2.2 hand value)."""
    assert DF.t_lim(1.05 * 285, 285, 15000, 3000, K) == approx(15000 / 14.25, rel=1e-6)
    for t in (300, 900, 1800, 3000, 7200, 36000):
        assert DF.t_lim(DF.p_sus(t, CP, WP, TTE, K), CP, WP, TTE, K) == approx(t, rel=1e-6)


def test_V_F6_endurance_multiple():
    """§3A: k −0.07, f 0.90 → 4.50 ×, 0.95 → 2.08 ×."""
    assert DF.endurance_multiple(0.90, -0.07) == approx(4.50, abs=0.005)
    assert DF.endurance_multiple(0.95, -0.07) == approx(2.08, abs=0.005)
    assert DF.endurance_multiple(1.0, -0.07) == approx(1.0)


@pytest.mark.parametrize("f,key", [
    (0.79, "easy"), (0.80, "steady"), (0.8999, "steady"), (0.90, "hard"), (0.9699, "hard"),
    (0.97, "max"), (1.00, "max"), (1.0001, "over"), (1.2, "over"),
])
def test_V_F5_effort_bar_cut_points(f, key):
    """User decision 2026-09-30: 輕鬆 <80, 穩定 80–90, 吃力 90–97, 極限 97–100,
    超出 >100. Lower bounds inclusive; exactly 100 % is still 極限."""
    assert DF.label_of(f)["key"] == key


def test_effort_band_contains_f():
    e = DF.effort(280.0, 7200, CP, WP, TTE, K)
    assert e["band"][0] <= e["f"] <= e["band"][1]
    assert len(e["cuts"]) == 5 and [c["from"] for c in e["cuts"]] == [0.0, 0.80, 0.90, 0.97, 1.00]


# ---- F10, F11b, F15, F16 ---------------------------------------------------------

def test_V_F10_hill_factor():
    assert all(PC.hill_factor(g, 0, 0) == 1.0 for g in (-0.3, -0.05, 0, 0.05, 0.3))
    assert PC.hill_factor(0.04) == approx(1.025)
    assert PC.hill_factor(0.08) == approx(1.05) and PC.hill_factor(0.25) == approx(1.05)
    assert PC.hill_factor(-0.05) == approx(0.95) and PC.hill_factor(-0.3) == approx(0.90)
    # Townshend 2010 recomputation: spontaneous ratios 1.124 / 0.884
    assert 100.4 / 89.3 == approx(1.124, abs=5e-4) and 78.9 / 89.3 == approx(0.884, abs=5e-4)


def test_V_F11b_wprime_budget():
    """Constant 310 W, CP 285, 600 s → (310 − 285)·600 = 15000 J; below CP
    nothing is spent."""
    rows = [{"P": 310.0, "t": 600.0}, {"P": 250.0, "t": 900.0}]
    runs = PC.wprime_budget(rows, [seg(1, 0), seg(1, 0)], 285.0, 20000.0)
    assert len(runs) == 1 and runs[0]["used_j"] == approx(15000.0) and not runs[0]["over"]
    runs = PC.wprime_budget(rows, [seg(1, 0), seg(1, 0)], 285.0, 18000.0)
    assert runs[0]["over"]                      # 15000 > 0.75 × 18000
    assert PC.wprime_budget([{"P": 250.0, "t": 3000.0}], [seg(1, 0)], 285.0, 15000.0) == []


def test_V_F15_ramp_integrates_to_one():
    for sigma in (-0.05, -0.02, 0.0, 0.03):
        xs = np.linspace(0, 1, 100001)
        assert np.trapezoid([PC.ramp(x, sigma) for x in xs], xs) == approx(1.0, abs=1e-9)
    assert PC.ramp(0, 0.02) == approx(1.02) and PC.ramp(1, 0.02) == approx(0.98)


@pytest.mark.parametrize("sigma", [0.0, 0.02, -0.02])
def test_V_F16_average_power_is_conserved(sigma):
    """F16: time-weighted average power = target (< 0.1 W), with hills,
    altitude weights and a ramp; T5: σ > 0 → the first segments are harder."""
    model, _ = flat_model()
    segs = hilly()
    r = PC.solve_power_mode(250.0, segs, model, sigma=sigma)
    assert abs(r["P"] - 250.0) < 0.1
    if sigma == 0:
        # equal grades, equal weights → equal power (flat first and last)
        assert r["rows"][0]["P"] / PC.altitude_weights(segs)[0] == approx(
            r["rows"][-1]["P"] / PC.hill_factor(0.01) / PC.altitude_weights(segs)[-1], rel=1e-9)
    elif sigma > 0:
        flat = [PC.course_time(r["lam"], [seg(1000, 0)] * 4, model, sigma=sigma)["rows"][i]["P"] for i in (0, 3)]
        assert flat[0] > flat[1]


def test_T5_even_strategy_has_unit_ramp():
    model, _ = flat_model()
    r = PC.course_time(250.0, [seg(1000, 0.0)] * 5, model, sigma=0.0)
    assert all(row["P"] == approx(250.0) for row in r["rows"])


def test_T6_downhill_cap_lowers_power_and_keeps_the_average():
    model, _ = flat_model(vmax=3.0)
    segs = [seg(5000, 0.0), seg(3000, -0.12), seg(5000, 0.0)]
    r = PC.solve_power_mode(250.0, segs, model)
    assert r["rows"][1]["capped"]
    assert r["rows"][1]["v"] == approx(3.0)
    assert abs(r["P"] - 250.0) < 0.1


def test_T6_T7_wprime_budget_shrinks_alpha():
    """A long steep climb at +12 % above CP would spend more than 0.75 W′;
    the solver shrinks α until it fits."""
    model, _ = flat_model()
    segs = [seg(2000, 0.0), seg(4000, 0.10), seg(2000, 0.0)]
    cp, wp = 260.0, 8000.0

    def solve(a):
        return PC.solve_power_mode(262.0, segs, model, alpha=a)
    res0 = solve(0.12)
    assert any(c["over"] for c in PC.wprime_budget(res0["rows"], segs, cp, wp))
    res, a, runs = PC.solve_with_budget(solve, segs, cp, wp, 0.12)
    assert a < 0.12
    assert not any(c["over"] for c in runs) or a == 0.0


def test_wbal_curves_are_display_only_and_sane():
    model, _ = flat_model()
    segs = [seg(2000, 0.0), seg(1500, 0.10), seg(2000, -0.05)]
    r = PC.solve_power_mode(300.0, segs, model, alpha=0.12)
    wk = PC.wbal_wko5(r["rows"], segs, 290.0, 15000.0)
    sk = PC.wbal_skiba(r["rows"], segs, 290.0, 15000.0)
    assert len(wk) == len(sk) == 3
    assert wk[1] < 15000.0 and sk[1] < 15000.0
    assert wk[2] >= wk[1] and sk[2] >= sk[1]    # recovery on the descent


# ---- T1 regression / T2 / T3 ------------------------------------------------------

def test_T1_auto_mode_on_a_flat_road_reproduces_v1():
    """v1 §1.8 worked example: CP 300, marathon, TTE 3000, k −0.07, RE 1.0,
    70 kg → 10766.75 s, 274.330 W (solve_riegel_re)."""
    v1 = PR.solve_riegel_re(MARATHON, CP, TTE, K, RE0, W)
    assert v1["time_s"] == approx(10766.75, abs=0.05) and v1["power"] == approx(274.330, abs=0.001)
    model, _ = flat_model()
    psus = lambda t: DF.p_sus(t, CP, WP, TTE, K)          # noqa: E731
    r = PC.solve_auto_mode(1.0, psus, [seg(MARATHON, 0.0)], model, CP)
    assert abs(r["T"] - v1["time_s"]) < 0.5 and abs(r["P"] - v1["power"]) < 0.1
    t = PL.solve_whole(MARATHON, RE0, W, 1.0, 1.0, psus)
    assert abs(t - v1["time_s"]) < 0.5
    # with an environment multiplier too
    v1m = PR.solve_riegel_re(MARATHON, CP, TTE, K, RE0, W, m=0.95)
    rm = PC.solve_auto_mode(1.0, psus, [seg(MARATHON, 0.0, m=0.95)], model, CP)
    assert abs(rm["T"] - v1m["time_s"]) < 0.5


def test_T2_time_and_power_modes_round_trip():
    model, _ = flat_model()
    segs = hilly()
    a = PC.solve_time_mode(4800.0, segs, model, CP)
    assert a["T"] == approx(4800.0, abs=0.5)
    b = PC.solve_power_mode(a["P"], segs, model)
    assert b["T"] == approx(4800.0, abs=0.5)


def test_T3_effort_is_one_at_the_auto_time():
    model, _ = flat_model()
    psus = lambda t: DF.p_sus(t, CP, WP, TTE, K)          # noqa: E731
    segs = hilly()
    c = PC.solve_auto_mode(1.0, psus, segs, model, CP)
    a = PC.solve_time_mode(c["T"], segs, model, CP)
    e = DF.effort(a["P_train"], a["T"], CP, WP, TTE, K)
    assert e["f"] == approx(1.0, abs=1e-4)
    assert e["key"] == "max"


# ---- F13 / F14 / F17 / Pandolf ---------------------------------------------------

def test_V_F13_tobler_points():
    """Tobler 1993 (via Wikipedia): S = −0.05 → 6.00 km/h, S = 0 → 5.04 km/h."""
    assert GM.tobler_kmh(-0.05) == approx(6.0)
    assert GM.tobler_kmh(0.0) == approx(5.04, abs=0.005)


def test_V_F14_wehrlin_linear():
    """Wehrlin & Hallén 2006: −6.3 % per 1000 m from 300 m → 1300 m = 0.937."""
    assert ENV.altitude_factor_linear(300) == 1.0
    assert ENV.altitude_factor_linear(100) == 1.0
    assert ENV.altitude_factor_linear(1300) == approx(0.937)


def test_T14_equal_segment_altitudes_give_v1_m():
    frm = {"altitude_m": 100, "temp_c": 25, "rh_pct": 75}
    to = {"altitude_m": 1500, "temp_c": 20, "rh_pct": 60}
    m = ENV.multiplier(frm, to)["M"]
    assert ENV.segment_factors([1500, 1500, 1500], frm, to) == approx([m, m, m], abs=1e-12)
    un = ENV.segment_factors([1500], frm, to, "unacclimatised")[0]
    part = ENV.segment_factors([1500], frm, to, "partial")[0]
    assert un < part < m


def test_V_F17_multi_day_needs_three_trips():
    days = [{"trip": "a", "days": 2, "day": 1, "ep_per_h": 3.0}, {"trip": "a", "days": 2, "day": 2, "ep_per_h": 2.7}]
    f = HK.day_fatigue(days)
    assert f["factors"] == {} and f["warning"] and HK.fatigue_of(2, f) == 1.0
    many = []
    for t, r in (("a", 0.9), ("b", 0.8), ("c", 0.95)):
        many += [{"trip": t, "days": 2, "day": 1, "ep_per_h": 3.0}, {"trip": t, "days": 2, "day": 2, "ep_per_h": 3.0 * r}]
    f = HK.day_fatigue(many)
    assert f["warning"] is None and HK.fatigue_of(2, f) == approx(0.9) and HK.fatigue_of(1, f) == 1.0


def test_T13_pandolf():
    """Pandolf with L = 0 is the unloaded walking cost; the loaded speed at
    equal metabolic rate falls with load."""
    wt, v, g = 70.0, 1.2, 10.0
    assert HK.pandolf(wt, 0, v, g) == approx(1.5 * wt + wt * (1.5 * v * v + 0.35 * v * g))
    vs = [HK.pandolf_speed(wt, 0.0, L, v, g) for L in (0, 5, 10, 20)]
    assert vs[0] == approx(v, abs=1e-6) and all(a > b for a, b in zip(vs, vs[1:]))
    assert HK.pack_factor(70, 5, 12) == approx(75 / 82)


def test_V_F12_pandolf_recomputation_and_no_downhill():
    """racepower-v2.md §3C.2 independent recomputation: W 70, L 20, V 1.34,
    G 5, η 1 → 573.15 W; W 70, L 0, V 1.34, G 0 → 293.54 W. Santee's downhill
    correction is single-source, so G < 0 is refused."""
    assert HK.pandolf(70, 20, 1.34, 5) == approx(573.15, abs=0.01)
    assert HK.pandolf(70, 0, 1.34, 0) == approx(293.54, abs=0.01)
    with pytest.raises(ValueError):
        HK.pandolf(70, 10, 1.2, -5)


def test_V_F14b_bassett_curves():
    """Bassett 1999 via TrainingPeaks / Simmons: 2000 m → 91.62 (acclimatised),
    87.564 (unacclimatised); clamped to 0–4000 m."""
    assert ENV.bassett_pct(2000, True) == approx(91.62)
    assert ENV.bassett_pct(2000, False) == approx(87.564)
    assert ENV.bassett_pct(6000, False) == ENV.bassett_pct(4000, False)
    # the acclimatised curve corroborates env.py's polynomial within 1 point (§3C.3 table, 15 °C lapse)
    for alt in (500, 1500, 2500, 3500):
        t = 15.0 - 6.5 * alt / 1000.0
        a = ENV.altitude_factor(ENV.pressure_torr(alt, t)) / ENV.altitude_factor(ENV.pressure_torr(0, 15.0)) * 100
        assert abs(a - ENV.bassett_pct(alt, True)) <= 1.05


def test_V_F11_skiba_tau_values():
    """§3C.1: D_CP 0 → 862 s, 100 W → 516.86 s, 219 W → 377.1 s (Jones &
    Vanhatalo 2017's 377 s); Vassallo 2020's running refit 372·e^(−0.02D) + 102."""
    assert PC.skiba_tau(0) == approx(862.0)
    assert PC.skiba_tau(100) == approx(516.86, abs=0.01)
    assert PC.skiba_tau(219) == approx(377.1, abs=0.05)
    assert PC.skiba_tau(0, "running") == approx(474.0)
    assert PC.skiba_tau(50, "running") == approx(372 * math.exp(-1) + 102)


def test_V_HE_hike_effort_quantiles():
    cuts = DF.hike_cuts(2.4, 3.0, 3.45, 3.9)
    assert cuts == approx([0.8, 1.0, 1.15, 1.3])
    assert DF.hike_effort(0.8, cuts)["key"] == "easy"
    assert DF.hike_effort(0.95, cuts)["key"] == "steady"
    assert DF.hike_effort(1.0, cuts)["key"] == "steady"
    assert DF.hike_effort(1.1, cuts)["key"] == "hard"
    assert DF.hike_effort(1.25, cuts)["key"] == "max"
    assert DF.hike_effort(1.4, cuts)["key"] == "over"


# ---- grade model (F7, F8, F9) -----------------------------------------------------

def test_T12_no_samples_equals_minetti_prior():
    gre = GM.fit_grade_re([], 1.0)
    for g in (-0.3, -0.2, -0.1, 0.0, 0.06, 0.2):
        assert gre.re(g) == approx(1.0 / minetti.grade_factor(g, downhill_floor=0.9))
    assert gre.re(-0.2) == approx(1.0 / 0.9)            # the 0.9 downhill floor
    assert gre.v_max(-0.1) is None


def test_V_F7_fit_recovers_a_known_curve():
    """Synthetic RE(g) = 0.9·prior(g) with 3 % noise, many samples per bin
    → the fit is within 2 % of the truth where there is data."""
    rng = random.Random(1)
    truth = lambda g: 0.9 * GM.re_prior(g, 1.0)          # noqa: E731
    samples = []
    for _ in range(20000):
        g = rng.uniform(-0.2, 0.2)
        samples.append({"g": g, "re": truth(g) * (1 + rng.gauss(0, 0.03)), "v": 3.0, "a": 1})
    gre = GM.fit_grade_re(samples, 1.0)
    for g in (-0.16, -0.08, 0.0, 0.08, 0.16):
        assert gre.re(g) == approx(truth(g), rel=0.02)
    assert math.isfinite(gre.re(0.4)) and math.isfinite(gre.re(-0.4))


def test_V_F9_vmax_is_the_personal_p90():
    samples = [{"g": -0.10, "re": 1.1, "v": v, "a": 1} for v in np.linspace(2.0, 4.0, 101)]
    gre = GM.fit_grade_re(samples, 1.0)
    assert gre.v_max(-0.10) == approx(np.percentile(np.linspace(2.0, 4.0, 101), 90))
    assert gre.v_max(0.05) is None


def test_hike_speed_shrinks_to_tobler():
    hs = GM.fit_hike_speed([])
    assert hs.v(0.0) == approx(GM.tobler_kmh(0.0) / 3.6)
    hs = GM.fit_hike_speed([{"g": 0.0, "v": 1.0, "z": 1500, "a": 1}] * 3000)
    assert hs.v(0.0) == approx(1.0, rel=0.02) and hs.ref_alt_m == 1500


def test_windows_sampler():
    t = np.arange(0, 1000.0)
    d = t * 3.0
    z = d * 0.05
    p = np.full_like(t, 250.0)
    ws = GM.windows(t, d, z, p, np.ones_like(t, bool))
    assert len(ws) >= 25
    assert all(w["g"] == approx(0.05, abs=1e-6) and w["v"] == approx(3.0, rel=1e-3) and w["p"] == approx(250) for w in ws)


# ---- course (V-DP, V-SM, V-CL, T8–T11) ----------------------------------------------

def test_V_DP_triangle_and_property():
    xs = np.arange(0, 10001, 10.0)
    ys = np.where(xs <= 5000, xs * 0.1, (10000 - xs) * 0.1)
    keep = CO.douglas_peucker(xs, ys, 10.0)
    assert keep == [0, 500, 1000]
    rng = np.random.default_rng(3)
    y = np.cumsum(rng.normal(0, 2, 3000))
    x = np.arange(3000) * 10.0
    k = CO.douglas_peucker(x, y, 5.0)
    line = np.interp(x, x[k], y[k])
    assert np.max(np.abs(line - y)) <= 5.0 + 1e-9


def test_V_SM_T10_smoothing_gain_and_official_scaling():
    """A true 600 m climb and descent with ±5 m noise every 10 m: smoothed
    gain within 5 %; scaled to an official gain it matches exactly."""
    rng = np.random.default_rng(7)
    xs = np.arange(0, 20000, 10.0)
    true = np.where(xs <= 10000, xs * 0.06, (20000 - xs) * 0.06)
    noisy = true + rng.uniform(-5, 5, len(xs))
    sm = CO.smooth(noisy, 10.0, 50.0)
    g, l = CO.gain_loss(sm)
    assert abs(g - 600) / 600 < 0.05 and abs(l - 600) / 600 < 0.05
    raw_gain = CO.gain_loss(noisy, 0.0)[0]
    assert raw_gain > 800                       # the noise really inflates gain
    scaled, c = CO._scale_to_gain(sm, 650.0)
    assert CO.gain_loss(scaled)[0] == approx(650.0, abs=0.01)


def test_hysteresis_counts_every_climb():
    z = [0, 10, 20, 10, 0, 50, 49, 48, 60]
    assert CO.gain_loss(z, 3.0) == approx((80.0, 20.0))


@pytest.mark.parametrize("g,cls", [(-0.15, "steep_down"), (-0.149, "down"), (-0.021, "down"), (-0.02, "flat"),
                                   (0.02, "flat"), (0.021, "up"), (0.149, "up"), (0.15, "steep_up")])
def test_V_CL_classes(g, cls):
    assert CO.classify(g) == cls


def test_V_CL_walk_labels():
    """Giovanelli 2016: 9.4° = 16.6 %; Ortiz 2017 30° walking cheaper → 15.8° = 28.3 %."""
    assert math.tan(math.radians(9.4)) == approx(0.166, abs=5e-4)
    assert math.tan(math.radians(15.8)) == approx(0.283, abs=5e-4)
    assert CO.walk_label(0.14) is None and CO.walk_label(0.15) == "走跑皆可" and CO.walk_label(0.28) == "建議快走"


def test_T9_haversine():
    """1° of latitude on the mean-radius sphere = π/180 · 6371008.8 m."""
    assert float(CO.haversine(23.0, 121.0, 24.0, 121.0)) == approx(math.pi / 180 * 6371008.8, rel=1e-9)
    # 玉山主峰 (23.4700, 120.9572) → 東峰 (23.4697, 120.9644): ≈ 735 m
    assert float(CO.haversine(23.4700, 120.9572, 23.4697, 120.9644)) == approx(735, abs=10)


def synthetic_track(profile, step_m=10.0, lat0=24.0, lon0=121.0, times=False, pace=None):
    """Straight northward track with the elevation profile(x) given in metres."""
    n = int(profile["len"] // step_m) + 1
    xs = np.arange(n) * step_m
    lat = lat0 + xs / (math.pi / 180 * CO.EARTH_R)
    ele = [float(profile["z"](x)) for x in xs]
    t = [float(x / (pace or 3.0)) for x in xs] if times else None
    return GPX.Track(list(lat), [lon0] * n, ele, t)


def test_T11_triangle_is_two_segments():
    tr = synthetic_track({"len": 10000, "z": lambda x: 500 + (x if x <= 5000 else 10000 - x) * 0.08})
    c = CO.build_course(tr)
    assert [s["cls"] for s in c["segments"]] == ["up", "down"]
    assert c["segments"][0]["dist_m"] == approx(5000, abs=150)
    assert c["totals"]["gain_m"] == approx(400, rel=0.05)
    assert sum(s["dist_m"] for s in c["segments"]) == approx(10000, abs=1)


def test_T11_sawtooth_merges_short_segments():
    tr = synthetic_track({"len": 12000, "z": lambda x: 300 + 40 * math.sin(x / 90.0) + 0.05 * x})
    c = CO.build_course(tr, eps_m=5.0)
    assert all(s["dist_m"] >= c["opts"]["min_len_m"] - 1e-6 for s in c["segments"])


def test_T11_km_split_and_long_flat_split():
    tr = synthetic_track({"len": 7300, "z": lambda x: 100.0})
    c = CO.build_course(tr, split="km")
    assert len(c["segments"]) == 8
    assert sum(s["dist_m"] for s in c["segments"]) == approx(7300, abs=1)
    g = CO.build_course(tr)
    assert len(g["segments"]) >= 7 and all(s["cls"] == "flat" for s in g["segments"])


def test_manual_course_and_day_cuts():
    m = CO.manual_course(21.1, 0, None, "km")
    assert len(m["segments"]) == 22 and sum(s["dist_m"] for s in m["segments"]) == approx(21100)
    one = CO.manual_course(10, 200, 200, "none")
    assert len(one["segments"]) == 1 and one["segments"][0]["grade"] == 0
    cut = CO.cut_at(m["segments"][:5], [2.5])
    assert [s["day"] for s in cut] == [1, 1, 1, 2, 2, 2]
    assert sum(s["dist_m"] for s in cut) == approx(5000)


# ---- planner (the /plan payload) ----------------------------------------------

def fake_v1(kind="road", km=42.195, gain=0.0, re=RE0, m=1.0, alt=100.0):
    d_eff = km + gain / 153.0 if kind == "trail" else km
    sol = PR.solve_riegel_re(d_eff * 1000.0, CP, TTE, K, re, W, m)
    side = {"altitude_m": alt, "temp_c": 12.0, "rh_pct": 70.0}
    res = {"time_s": sol["time_s"], "power": sol["power"], "distance_km": km,
           "effort_km": d_eff if kind == "trail" else None}
    return {"type": kind, "env": {"M": m, "from": dict(side), "to": dict(side)},
            "used": {"cp": {"value": CP}, "w_prime": {"value": WP}, "tte": {"value": TTE}, "k": {"value": K},
                     "weight": {"value": W}, "re": {"value": re}},
            "result": res, "warnings": []}


def test_planner_T1_manual_flat_auto_equals_v1():
    v1 = fake_v1()
    ref = PR.solve_riegel_re(MARATHON, CP, TTE, K, RE0, W)
    p = PL.plan_run(v1=v1, course=CO.manual_course(42.195, 0.0, 0.0, "km"), grade_re=GM.GradeRE(RE0),
                    opts={"mode": "auto"}, validated={}, effort_validated=False)
    s = p["summary"]
    assert abs(s["time_s"] - ref["time_s"]) < 0.5 and abs(s["power"] - ref["power"]) < 0.1
    assert s["total_method"] == "v1" and p["effort"]["f"] == approx(1.0, abs=1e-4)
    assert p["effort"]["badge"] == "推估" and all(x["badge"] == "推估" for x in p["segments"])
    assert len(p["segments"]) == 43 and all(x["hint"] == "看 30 秒平均功率" for x in p["segments"])


def test_planner_modes_on_a_gpx_course_and_the_validation_gate():
    tr = synthetic_track({"len": 12000, "z": lambda x: 200 + (x * 0.05 if x < 6000 else (12000 - x) * 0.05)})
    c = CO.build_course(tr)
    gre = GM.GradeRE(RE0)
    v1 = fake_v1("trail", 12.0, c["totals"]["gain_m"])
    common = dict(v1=v1, course=c, grade_re=gre, effort_validated=False)
    deg = PL.plan_run(opts={"mode": "auto"}, validated={}, **common)
    assert deg["summary"]["total_method"] == "v1"
    # v1 total (effort km × trail RE, at the course's mean M from the GPX elevations), v2 allocation
    ref = PR.solve_riegel_re(v1["result"]["effort_km"] * 1000.0, CP, TTE, K, RE0, W, deg["summary"]["M"])
    assert deg["summary"]["M"] < 1.0
    # the effort at the auto time is exactly f* (whole-race M = the effort's time-weighted M)
    assert deg["effort"]["f"] == approx(1.0, abs=1e-4) and deg["effort"]["key"] == "max"
    ref = PR.solve_riegel_re(v1["result"]["effort_km"] * 1000.0, CP, TTE, K, RE0, W,
                             deg["summary"]["power"] / deg["summary"]["power_train"])
    assert deg["summary"]["time_s"] == approx(ref["time_s"], abs=0.5)
    hard = PL.plan_run(opts={"mode": "auto", "effort_target": 0.95}, validated={}, **common)
    assert hard["effort"]["f"] == approx(0.95, abs=1e-4) and hard["summary"]["time_s"] > deg["summary"]["time_s"]
    ok = PL.plan_run(opts={"mode": "auto"}, validated={"trail": True}, **common)
    assert ok["summary"]["total_method"] == "v2"
    assert all(x["badge"] is None for x in ok["segments"] if x["trusted"])
    assert ok["effort"]["f"] == approx(1.0, abs=1e-3)
    a = PL.plan_run(opts={"mode": "time", "target_time_s": 5400}, validated={"trail": True}, **common)
    assert a["summary"]["time_s"] == approx(5400, abs=1)
    b = PL.plan_run(opts={"mode": "power", "target_power": a["summary"]["power"]}, validated={"trail": True}, **common)
    assert b["summary"]["time_s"] == approx(5400, abs=1)
    lock = PL.plan_run(opts={"mode": "power", "target_power": 250, "locks": [{"seg": 1, "power": 400}]},
                       validated={"trail": True}, **common)
    assert lock["segments"][0]["power"] == approx(400) and lock["segments"][0]["locked"]
    assert lock["summary"]["power"] == approx(250, abs=0.1)
    up = [x for x in ok["segments"] if x["cls"] == "up"]
    down = [x for x in ok["segments"] if x["cls"] == "down"]
    assert up[0]["power"] > down[0]["power"]
    steps = PL.coros_steps(ok["segments"])
    assert len(steps) == len(ok["segments"]) and steps[0].intensity[0] == "power"


def test_planner_trail_total_from_the_hr_pace_model():
    tr = synthetic_track({"len": 12000, "z": lambda x: 200 + (x * 0.05 if x < 6000 else (12000 - x) * 0.05)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 12.0, c["totals"]["gain_m"])
    common = dict(v1=v1, course=c, grade_re=GM.GradeRE(RE0), effort_validated=False)
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9}
    r = PL.plan_run(opts={"mode": "auto"}, validated={"trail": True}, trail_hr=m, **common)
    est = PL.trail_hr_estimate(m, c["totals"]["km"], c["totals"]["gain_m"], 1.0)
    assert r["summary"]["total_method"] == "trail_hr"
    assert r["summary"]["time_s"] == approx(est["time_s"] / r["summary"]["M"], rel=1e-3)
    assert r["crosscheck"]["power_envelope"]["time_s"] > 0
    # time / power modes keep the power path
    t = PL.plan_run(opts={"mode": "time", "target_time_s": 5400}, validated={}, trail_hr=m, **common)
    assert t["summary"]["total_method"] == "v1" and t["summary"]["time_s"] == approx(5400, abs=1)


def test_planner_hike_three_modes_and_days():
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    c = CO.build_course(tr)
    v1 = {"type": "baiyue", "env": {"M": 0.93, "from": {"altitude_m": 100, "temp_c": 20, "rh_pct": 70},
                                    "to": {"altitude_m": 3400, "temp_c": 10, "rh_pct": 70}},
          "used": {"weight": {"value": 70}, "eph": {"value": 3.0}, "aet": {"value": 145}},
          "result": {"pack_factor": 0.9, "pack_kg": 12, "M": 0.93, "time_s": 20000,
                     "days": [{"day": 1, "km": 16, "gain_m": 800, "loss_m": 800, "moving_h": 5.5, "kcal": 3000,
                               "water_ml": [2100, 2400]}]}, "warnings": []}
    hs = GM.fit_hike_speed([])
    inp = {"hiking": {"days": [{"ep_per_h": x, "days": 1} for x in (2.4, 2.8, 3.0, 3.2, 3.5, 3.9)]}}
    base = dict(v1=v1, course=c, hike_speed=hs, inp=inp, validated={})
    auto = PL.plan_hike(opts={"mode": "auto", "day_splits_km": [8.0], "terrain": {"1": "bamboo"}}, **base)
    assert [d["day"] for d in auto["days"]] == [1, 2]
    assert auto["segments"][0]["terrain"] == "bamboo" and auto["segments"][0]["eta_factor"] == 1.35
    t1 = auto["summary"]["time_s"]
    a = PL.plan_hike(opts={"mode": "time", "target_time_s": t1 / 1.1}, **base)
    assert a["summary"]["speed_factor"] == approx(1.1)
    b = PL.plan_hike(opts={"mode": "power", "speed_factor": 0.9}, **base)
    assert b["summary"]["time_s"] == approx(t1 / 0.9, rel=1e-9)
    assert auto["effort"]["key"] == "steady" and auto["summary"]["total_method"] == "v1"
    manual = PL.plan_hike(opts={"mode": "auto"}, **{**base, "course": CO.manual_course(16, 800)})
    assert manual["summary"]["time_s"] > 0 and manual["days"]


GPX11 = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">
<metadata><name>demo</name></metadata>
<wpt lat="24.001" lon="121.0"><name>排雲山莊</name></wpt>
<trk><name>t</name>
<trkseg><trkpt lat="24.0" lon="121.0"><ele>100</ele><time>2026-01-01T00:00:00Z</time></trkpt>
<trkpt lat="24.001" lon="121.0"><ele>110</ele><time>2026-01-01T00:01:00Z</time></trkpt></trkseg>
<trkseg><trkpt lat="24.002" lon="121.0"><ele>120</ele><time>2026-01-01T00:02:00Z</time></trkpt></trkseg>
</trk></gpx>""".encode("utf-8")

GPX10_RTE = b"""<?xml version="1.0"?>
<gpx version="1.0" creator="t" xmlns="http://www.topografix.com/GPX/1/0">
<rte><name>r</name><rtept lat="24.0" lon="121.0"><ele>5</ele></rtept><rtept lat="24.01" lon="121.0"><ele>15</ele></rtept></rte>
</gpx>"""


def test_T8_gpx_parsing():
    t = GPX.parse(GPX11, "a.gpx")
    assert len(t) == 3 and t.ele == [100, 110, 120] and t.time == [0, 60, 120]
    assert t.wpts[0]["name"] == "排雲山莊" and t.name == "demo"
    r = GPX.parse(GPX10_RTE, "b.gpx")
    assert len(r) == 2 and r.time is None
    with pytest.raises(GPX.GpxError, match="海拔"):
        GPX.parse(GPX10_RTE.replace(b"<ele>5</ele>", b"").replace(b"<ele>15</ele>", b""), "c.gpx")
    with pytest.raises(GPX.GpxError, match="DOCTYPE"):
        GPX.parse(b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "x">]><gpx></gpx>', "d.gpx")
    with pytest.raises(GPX.GpxError):
        GPX.parse(b"<kml></kml>", "e.gpx")


def test_T8_size_limit(monkeypatch):
    monkeypatch.setattr(GPX, "MAX_BYTES", 100)
    with pytest.raises(GPX.GpxError, match="MB"):
        GPX.parse(GPX11, "a.gpx")


def test_gpx_write_round_trip_and_camp_waypoint():
    tr = synthetic_track({"len": 3000, "z": lambda x: 1000 + 0.1 * x}, times=True)
    tr.wpts = [{"name": "營地", "lat": tr.lat[150], "lon": tr.lon[150]}]
    back = GPX.parse(GPX.write_gpx(tr).encode("utf-8"), "x.gpx")
    assert len(back) == len(tr) and back.ele[-1] == approx(tr.ele[-1], abs=0.05)
    c = CO.build_course(back)
    assert c["wpts"][0]["camp"] and c["wpts"][0]["km"] == approx(1.5, abs=0.02)


def test_device_distance_is_the_ruler_when_given():
    tr = synthetic_track({"len": 2000, "z": lambda x: 100.0})
    tr.dist = [i * 11.0 for i in range(len(tr))]          # the footpod says 10 % longer
    td = CO.track_distance(tr)
    assert td["d"][-1] == approx(11.0 * (len(tr) - 1))


# ---- V-BT: the back-test harness ------------------------------------------------

from backend.engine.racepower import backtest as BT   # noqa: E402


def test_V_BT_leave_one_out_excludes_the_case_and_time_travels():
    import datetime as dt
    cases = [{"idx": i, "date": f"2026-0{i}-15", "category": "trail", "label": str(i)} for i in (3, 5, 7)]
    seen = []

    def context(c):
        seen.append((c["idx"], set(c["exclude"]), c["as_of"]))
        return {}
    rows = BT.run_harness(cases, context, lambda c, ctx: {"err_v2": 0.0, "segments": []})
    assert len(rows) == 3
    for (idx, exc, as_of), c in zip(seen, cases):
        assert exc == {c["idx"]} and idx == c["idx"]
        assert as_of == dt.date.fromisoformat(c["date"]) - dt.timedelta(days=1)


def _synthetic_run_arrays(n=4000, v=3.0, p=250.0, grade=0.0):
    t = np.arange(n, dtype=float)
    d = t * v
    z = 100 + d * grade
    lat = 24.0 + d / (math.pi / 180 * CO.EARTH_R)
    return {"t": t, "d": d, "z": z, "lat": lat, "lon": np.full(n, 121.0), "p": np.full(n, p), "kmh": np.full(n, v * 3.6)}


def test_V_BT_evaluate_run_is_exact_when_the_model_is_the_truth():
    """A synthetic run at RE 1.0 exactly (v = RE·P/W): the model with
    RE(g) = 1.0 predicts the actual time → error ≈ 0."""
    arr = _synthetic_run_arrays(v=250.0 / W)
    inp = {"weight": {"value": W}, "training_conditions": {"altitude_m": 100.0, "temp_c": 15.0, "rh_pct": 60.0},
           "re": {"road": {"median": 1.0}, "trail": {}},
           "cp": {"sources": [{"id": "x", "cp": 280.0}], "default": "x", "activities": {"w_prime": 15000.0}},
           "tte": {"value": 3000.0}, "riegel": {"k": -0.07, "valid": True}, "auto_prior": None}
    r = BT.evaluate_run({"category": "road"}, {"arrays": arr, "inputs": inp, "grade_re": GM.GradeRE(1.0)})
    assert abs(r["err_v2"]) < 0.002 and abs(r["err_v1"]) < 0.002
    assert r["effort"]["f"] == approx(250.0 / DF.p_sus(r["t_act"], 280.0, 15000.0, 3000.0, -0.07), rel=1e-3)
    assert all(abs(s["err"]) < 0.002 for s in r["segments"] if s["err"] is not None)


def _bt_row(cat, err, seg_err=0.0, cls="race", f=1.0, err_c=None):
    return {"category": cat, "err_v2": err, "err_v1": err, "intensity": cls, "f": f,
            "err_c": err if err_c is None else err_c, "err_p": 0.0,
            "segments": [{"cls": "down", "bin": "−8…−2%", "err": seg_err, "err_cls": seg_err}]}


def test_V_BT_capacity_pass_rule():
    few = BT.summarise_capacity([_bt_row("trail", 0.01)] * 3, [])
    assert not few["categories"]["trail"]["passed"] and few["message"] == BT.NO_CAPACITY_MSG
    ok = BT.summarise_capacity([_bt_row("trail", 0.01)] * 6, [])
    assert ok["categories"]["trail"]["passed"] and ok["effort"]["passed"] and ok["message"] is None
    # trail threshold 8 % since 2026-10-02 (unsourced-rules.md §0.9)
    slow = BT.summarise_capacity([_bt_row("trail", 0.01, err_c=0.09)] * 6, [])
    assert not slow["categories"]["trail"]["passed"]
    # one run above the model's sustainable power fails the category
    lb = [{"category": "trail", "f": 1.05, "date": "2026-01-01"}]
    viol = BT.summarise_capacity([_bt_row("trail", 0.01)] * 6, lb)
    assert not viol["categories"]["trail"]["passed"] and viol["lower_bound"]["violations"] == 1
    easy = BT.summarise_capacity([_bt_row("road", 0.01, f=0.8)] * 6, [])
    assert not easy["effort"]["passed"]                       # median f 0.80 is not 0.97–1.03


def test_V_BT_terrain_summary_and_flags():
    rows = [_bt_row("trail", 0.01)] * 6
    te = BT.summarise_terrain(rows)
    assert te["classes"]["race"]["activities"] == 6 and te["grid"]["race"]["−8…−2%"]["n"] == 6
    cap = BT.summarise_capacity(rows, [])
    assert BT.validated_flags(te, cap, rows)["trail"]
    fast_down = [_bt_row("trail", 0.01, 0.07)] * 6
    assert not BT.validated_flags(BT.summarise_terrain(fast_down), cap, fast_down)["trail"]
    assert BT.grade_bin(-0.2) == "≤ −15%" and BT.grade_bin(0.0) == "±2%" and BT.grade_bin(0.2) == "≥ +15%"


def test_V_BT_flags_follow_the_stored_result(tmp_path):
    p = tmp_path / "bt.json"
    none = {"road": False, "trail": False, "hike": False, "hike_capacity": False}
    assert BT.flags(p) == (none, False)
    BT.save({"version": 2, "validated": {"road": True, "trail": False, "hike": False}, "effort_validated": False}, p)
    assert BT.flags(p) == ({**none, "road": True}, False)
    BT.store_capacity({"passed": True}, p)                     # the 百岳 capacity gate merges into the same file
    assert BT.flags(p) == ({**none, "road": True, "hike_capacity": True}, False)
    BT.save({"validated": {"road": True}}, p)                  # an old-format result validates nothing
    assert BT.flags(p)[0]["road"] is False


# ---- T15: API -----------------------------------------------------------------------

def fake_inputs():
    return {
        "today": "2026-09-30",
        "weight": {"value": W, "source": "測試"},
        "training_conditions": {"altitude_m": 100.0, "temp_c": 15.0, "rh_pct": 60.0},
        "cp": {"sources": [{"id": "activities", "label": "活動", "cp": CP, "w_prime": WP}],
               "default": "activities", "activities": {"w_prime": WP}},
        "tte": {"value": TTE, "source": "預設"},
        "re": {"road": {"median": RE0, "n": 10}, "road_cvi": {"median": 0.0},
               "trail": {"fitted_run": {"median": 0.9, "n": 6}, "itra": {"median": 0.85, "n": 6},
                         "scarf": {"median": 0.87, "n": 6}}},
        "auto_prior": None, "priors": [],
        "riegel": {"k": K, "valid": True, "longest_s": 4 * 3600, "invalid_reasons": []},
        "hiking": {"eph": {"median": 3.0, "n": 8},
                   "days": [{"ep_per_h": x, "days": 1, "gain_m": 800} for x in (2.4, 2.8, 3.0, 3.1, 3.4, 3.8)],
                   "biggest": None},
        "aet": {"aet": 150.0, "source": "測試"},
    }


@pytest.fixture()
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: fake_inputs())
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": []})
    monkeypatch.setattr(RP, "_trail_hr", lambda: None)      # no real dataset: trail total from power
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": False, "hike": False}, False))
    RP._courses.clear()
    app = FastAPI()
    app.include_router(RP.router)
    return TestClient(app)


def test_T15_predict_v1_is_unchanged(client):
    r = client.post("/api/v1/racepower/predict", json={"type": "road", "distance_km": 42.195})
    assert r.status_code == 200
    res = r.json()["result"]
    assert res["time_s"] == approx(10766.75, abs=0.05) and res["power"] == approx(274.330, abs=0.001)
    assert set(r.json()) == {"type", "used", "env", "result", "tasks", "zones", "warnings"}


def test_T15_plan_manual_road_auto_matches_v1(client):
    r = client.post("/api/v1/racepower/plan", json={"type": "road", "distance_km": 42.195, "mode": "auto"})
    assert r.status_code == 200, r.text
    s = r.json()["summary"]
    assert s["time_s"] == approx(10766.75, abs=0.5) and s["total_method"] == "v1"
    assert r.json()["effort"]["badge"] == "推估"


def test_T15_course_upload_then_plan_and_410(client):
    tr = synthetic_track({"len": 12000, "z": lambda x: 300 + (x * 0.06 if x < 6000 else (12000 - x) * 0.06)})
    data = GPX.write_gpx(tr).encode("utf-8")
    up = client.post("/api/v1/racepower/course", files={"file": ("t.gpx", data, "application/gpx+xml")})
    assert up.status_code == 200, up.text
    cid = up.json()["course_id"]
    assert len(up.json()["segments"]) == 2 and len(up.json()["profile"]["km"]) <= 1500
    for mode, extra in (("auto", {}), ("time", {"target_time_s": 5400}), ("power", {"target_power": 250})):
        r = client.post("/api/v1/racepower/plan", json={"type": "trail", "mode": mode, "course": {"course_id": cid},
                                                        "strategy": {"kind": "positive", "amount": 0.02}, **extra})
        assert r.status_code == 200, r.text
        assert all(sg["badge"] == "推估" for sg in r.json()["segments"])
    km = client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": cid, "split": "km"}})
    assert len(km.json()["segments"]) == 12
    gone = client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": "deadbeef"}})
    assert gone.status_code == 410
    bad = client.post("/api/v1/racepower/course", files={"file": ("x.gpx", b"<!DOCTYPE x><gpx/>", "text/xml")})
    assert bad.status_code == 400


def test_T15_hike_plan_and_coros_preview(client):
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("h.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    r = client.post("/api/v1/racepower/plan", json={"type": "baiyue", "course": {"course_id": cid},
                                                    "day_splits_km": [8], "acclimatisation": "partial",
                                                    "start_time": "05:30"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["days"]) == 2 and j["summary"]["acclimatisation"] == "partial"
    assert any("部分適應" in w for w in j["warnings"])
    from backend.api import racepower as RP
    from backend.tests.test_coros_workouts import make_db, run
    db = run(make_db())

    async def _dep():
        yield db
    client.app.dependency_overrides[RP._db] = _dep
    ex = client.post("/api/v1/racepower/export/coros", json={"type": "trail", "course": {"course_id": cid}})
    assert ex.status_code == 200, ex.text
    j = ex.json()
    # trail: lap-button steps with the leg's target (power on runnable legs, HR cap on the steep ones)
    assert j["pushed"] is None and j["mode"] == "lap" and len(j["lines"]) >= 1
    assert all(ln["dur"] == "按圈結束" for ln in j["lines"])
    # (the full push / idempotency is test_race_calculator.py, against a faked COROS)
