"""
百岳 walking capacity — docs/research/baiyue-from-running.md §7.2. Every
formula is checked against an independent recomputation (closed-form
quadratics, hand arithmetic), not against the function under test.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pytest
from pytest import approx

from backend.engine.racepower import capacity as CAP
from backend.engine.racepower import course as CO
from backend.engine.racepower import env as ENV
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import hike as HK
from backend.engine.racepower import planner as PL
from backend.tests.test_racepower_v2 import synthetic_track


def pandolf_v_closed_form(m, w, load, g_pct, eta=1.0):
    """Independent: Pandolf M = 1.5W + 2(W+L)(L/W)² + η(W+L)(1.5V² + 0.35·V·G)
    is a quadratic in V; take its positive root."""
    c = m - 1.5 * w - 2.0 * (w + load) * (load / w) ** 2
    a, b = 1.5 * eta * (w + load), 0.35 * eta * (w + load) * g_pct
    return (-b + math.sqrt(b * b + 4 * a * c)) / (2 * a)


# ---- B1 / B2 / B3 -----------------------------------------------------------

def test_B1_aet_power_worked_example():
    assert CAP.aet_power(68, 2.7) == approx(68 * (1.5 + 3.6 * 2.7))
    assert CAP.aet_power(68, 2.7) == approx(762.96, abs=0.01)
    assert CAP.CR0 == 3.6


@pytest.mark.parametrize("g, load, expect", [(0.20, 2, 1.093), (0.30, 12, 0.709)])
def test_B2_pandolf_inverse_worked_examples(g, load, expect):
    e = 762.96
    v = CAP.prior_speed(g, load, e, 68)
    assert v == approx(pandolf_v_closed_form(e, 68, load, 100 * g), abs=1e-6)
    assert v == approx(expect, abs=5e-4)


def test_B2_doc_table_and_flat_speed_is_a_running_speed():
    e = CAP.aet_power(68, 2.7)
    # the doc's table (km/h): 10 % 5.76 / 5.21, 20 % 3.93 / 3.49, 30 % 2.90 / 2.55
    for g, l2, l12 in ((0.10, 5.76, 5.21), (0.20, 3.93, 3.49), (0.30, 2.90, 2.55)):
        assert CAP.prior_speed(g, 2, e, 68) * 3.6 == approx(l2, abs=0.01)
        assert CAP.prior_speed(g, 12, e, 68) * 3.6 == approx(l12, abs=0.01)
    assert CAP.prior_speed(0.0, 2, e, 68) * 3.6 == approx(9.03, abs=0.01)


def test_B3_pack_ratio_matches_linear_on_steep_grades_and_downhill_is_linear():
    e = CAP.aet_power(68, 2.7)
    r30 = CAP.pack_ratio(0.30, 12, 2, 68, e)
    ind = pandolf_v_closed_form(e, 68, 12, 30) / pandolf_v_closed_form(e, 68, 2, 30)
    assert r30 == approx(ind, abs=1e-6) and r30 == approx(0.880, abs=5e-4)
    lin = (68 + 2) / (68 + 12)
    assert abs(r30 / lin - 1) < 0.01
    assert CAP.pack_ratio(0.05, 12, 2, 68, e) == approx(0.917, abs=5e-4)
    assert CAP.pack_ratio(-0.2, 12, 2, 68, e) == approx(lin)


def test_day_pack_drops_0_7_kg_per_day():
    assert [CAP.day_pack(9.0, n) for n in (1, 2, 3)] == approx([9.0, 8.3, 7.6])


# ---- B7 ---------------------------------------------------------------------

def test_B7_precision_weighting_worked_examples():
    for se, w, post in ((1.0, 0.90, -9.54), (2.0, 0.69, -8.79), (3.0, 0.50, -8.10), (5.0, 0.26, -7.25)):
        s = CAP.shrink(-9.9, se, -6.3, 3.0)
        ind = (-9.9 / se ** 2 + -6.3 / 9.0) / (1 / se ** 2 + 1 / 9.0)
        assert s["post"] == approx(ind) and s["post"] == approx(post, abs=0.01) and s["w"] == approx(w, abs=0.005)
    assert CAP.shrink(-9.9, math.inf)["post"] == CAP.ALT_PRIOR_PCT
    assert CAP.shrink(-9.9, None)["post"] == CAP.ALT_PRIOR_PCT
    assert CAP.shrink(-9.9, 1e-9, -6.3, 3.0)["post"] == approx(-9.9)


def test_alt_tau_from_wehrlin_range_and_coffman():
    sd_ind = (7.5 - 4.6) / 2.847
    assert CAP.ALT_TAU == approx(math.sqrt(sd_ind ** 2 + ((6.3 - 3.8) / 2) ** 2))
    assert CAP.ALT_TAU == approx(1.61, abs=0.01)


def test_trip_fixed_effect_recovers_the_within_trip_slope():
    """Trips differ in pack (level) and the heavy trips are the high ones:
    the pooled slope is confounded, the trip-FE slope recovers −6 %."""
    rng = random.Random(1)
    wins = []
    for trip in range(8):
        z0 = 300 + trip * 450
        level = math.log(700) - 0.04 * trip        # heavier pack on the higher trips
        for i in range(30):
            z = z0 + i * 20
            g = rng.choice([0.15, 0.25, 0.35])
            vam = math.exp(level - 0.06 * z / 1000.0 + rng.gauss(0, 0.01))
            wins.append({"trip": trip, "z": z, "g": g, "v": vam / (g * 3600), "hr": 150, "day": 1, "t": i * 120})
    fe = CAP.alt_slope(wins, lambda w: (CAP._gband(w["g"]), w["trip"]))
    pooled = CAP.alt_slope(wins, lambda w: CAP._gband(w["g"]))
    assert fe["pct_per_km"] == approx(-6.0, abs=0.5)
    assert abs(pooled["pct_per_km"] + 6.0) > 0.5
    se = CAP.cluster_se(wins, lambda w: (CAP._gband(w["g"]), w["trip"]), reps=100)
    assert se is not None and se < 1.0


# ---- HR lag and the first window --------------------------------------------

def test_hr_lag_and_first_window_drop_give_the_steady_walking_hr():
    """Run at 170 bpm, then walk (1 m/s) with HR decaying to 140 (τ 40 s):
    the lagged HR of the kept windows is back at the steady state."""
    t = np.arange(0, 1200, 1.0)
    walk_from = 200.0
    hr = np.where(t < walk_from, 170.0, 140.0 + 30.0 * np.exp(-(t - walk_from) / 40.0))
    v = np.where(t < walk_from, 3.0, 1.0)
    d = np.cumsum(v)
    z = np.where(t < walk_from, 0.0, (d - d[int(walk_from)]) * 0.2)
    rows = GM.windows(t, d, z, None, np.ones_like(t, bool), hr=hr, hr_lag_s=60.0)
    walk = [dict(r, a=1, trail=True, run=0.0) for r in rows if r["g"] >= 0.15]
    assert walk[0]["hr"] > 148                       # the first walked window still carries the run
    kept = CAP.prepare(CAP.runs_of(walk), "trail")
    assert len(kept) == len(walk) - 1
    assert kept[0]["seg_hr"] == approx(140.0, abs=2.0)
    assert all(r["t"] >= 0 for r in rows) and rows[1]["t"] > rows[0]["t"]


def test_v_run_at_aet_picks_flat_running_windows_at_aet():
    gs = [{"g": 0.0, "run": 1.0, "hr_lag": 142 + (i % 5) - 2, "v": 2.0 + 0.01 * (i % 3), "a": 1, "date": "2026-09-01",
           "trail": False} for i in range(40)]
    gs += [{"g": 0.0, "run": 1.0, "hr_lag": 160, "v": 3.0, "a": 1, "date": "2026-09-01", "trail": False}] * 40
    r = CAP.run_speed_at_aet(gs, lambda a: 142.0, "2026-07-01")
    assert r["v"] == approx(2.01, abs=0.011) and not r["fallback"]
    none = CAP.run_speed_at_aet([], lambda a: 142.0, "2026-07-01")
    assert none["v"] is None


# ---- the fitted model ---------------------------------------------------------

def _synthetic_cap(level=0.2, n=40):
    e = CAP.aet_power(68, 2.0)
    rng = random.Random(4)
    trail = []
    for a in range(12):
        for i in range(n):
            g = rng.choice([0.12, 0.2, 0.28, 0.34])
            v = CAP.prior_speed(g, CAP.L_TRAIL, e, 68) * math.exp(level + rng.gauss(0, 0.05))
            trail.append({"a": a, "g": g, "g_raw": g, "v": v, "z": 300, "t": i * 90.0, "k": i, "hr": 140, "hr_lag": 140,
                          "seg": ("trail", a, None, i // 5), "seg_hr": 140, "src": "trail"})
    flat = [{"a": a, "g": gg, "v": 1.3 if gg > -0.05 else 1.6, "trail": True, "run": 0.2}
            for a in range(3) for gg in (0.0, 0.01, -0.02, -0.1, -0.2) for _ in range(10)]
    return CAP.fit_walk_capacity(weight=68, v_run={"v": 2.0, "n": 50, "source": "t"}, trail=trail, hike=[],
                                 flat=flat, hike_down=[], aet_of=lambda a: 140.0, boot_reps=20)


def test_level_term_absorbs_the_personal_offset_and_the_prior_stays_the_shape():
    cap = _synthetic_cap(0.2)
    assert cap.delta["level"]["d"] == approx(0.2 * 480 / 510, abs=0.02)
    e = cap.e_aet
    v = cap.v(0.2, CAP.L_TRAIL, z=300)
    assert v == approx(CAP.prior_speed(0.2, CAP.L_TRAIL, e, 68) * math.exp(cap.d(0.2)), rel=1e-9)
    assert cap.d(0.2) == approx(0.2, abs=0.03)
    # no hike windows → α is the prior
    assert cap.alpha["post"] == CAP.ALT_PRIOR_PCT


def test_descent_is_capped_and_flat_is_never_a_running_speed():
    cap = _synthetic_cap()
    for g in (-0.4, -0.3, -0.2, -0.1, -0.05):
        assert cap.v(g, 9.0) <= cap.c_cap * GM.tobler_kmh(g) / 3.6 + 1e-12
    assert cap.v(0.0, 2.0) * 3.6 < 7.0
    assert cap.v(0.0, 2.0) == approx(min(1.3, CAP.prior_speed(0.0, 2.0, cap.e_aet, 68)), rel=1e-9)
    # uphill never faster than flat
    assert cap.v(0.12, 2.0) <= cap.v(0.0, 2.0) + 1e-12


def test_altitude_factor_is_b7_with_the_posterior():
    cap = _synthetic_cap()
    assert cap.A(300) == 1.0 and cap.A(100) == 1.0
    assert cap.A(3300) == approx(math.exp(-6.3 / 100 * 3.0))
    ratio = ENV.bassett_pct(3300, True) / ENV.bassett_pct(3300, False) / (
        ENV.bassett_pct(300, True) / ENV.bassett_pct(300, False))
    assert cap.A(3300, "acclimatised") == approx(math.exp(-0.189) * ratio)


def test_beta_is_fixed_at_zero_when_not_significant():
    cap = _synthetic_cap()
    assert cap.beta["used"] == 0.0 and not cap.beta["reliable"]
    assert cap.f_day(3) == 1.0


def test_course_eph_differs_from_tobler_and_group_time_quantiles():
    cap = _synthetic_cap()
    e = CAP.course_eph(cap, 16.0, 1200.0, 1200.0, 5.0)
    assert e != approx(HK.tobler_eph(16.0, 1200.0, 1200.0), rel=0.01)
    g = CAP.group_time(28.0, [{"ep_per_h": x} for x in (2.0, 2.5, 3.0, 3.5, 4.0)])
    assert g["p50_s"] == approx(28.0 / 3.0 * 3600) and g["p25_s"] == approx(28.0 / 3.5 * 3600)
    assert CAP.group_time(28.0, [{"ep_per_h": 3.0}] * 2) is None


def test_band_is_log_symmetric_p10_p90():
    b = CAP.band(10000.0, 0.2)
    assert b["p10_s"] == approx(10000 * math.exp(-1.2816 * 0.2)) and b["p90_s"] == approx(10000 * math.exp(1.2816 * 0.2))


# ---- solo suggestion ----------------------------------------------------------

def test_classify_day_group_vs_solo():
    cap = _synthetic_cap(0.0)
    # group: low HR, steady plateau speed across changing grades
    grp = [{"k": i, "g": 0.10 + 0.03 * (i % 6), "v": 0.55, "hr": 120, "z": 500} for i in range(30)]
    # solo: HR ≥ AeT and speed follows the model
    solo = []
    for i in range(30):
        g = 0.10 + 0.03 * (i % 6)
        solo.append({"k": i, "g": g, "v": cap.v(g, 6.0, z=500) * (1 + 0.02 * ((i % 3) - 1)), "hr": 145, "z": 500})
    assert CAP.classify_day(grp, 140, cap, 6.0)["suggest"] == "group"
    assert CAP.classify_day(solo, 140, cap, 6.0)["suggest"] == "solo"
    v = CAP.classifier_validation([{"marked": "solo", "suggest": "solo"}] * 3 + [{"marked": "group", "suggest": "group"}] * 6)
    assert not v["validated"] and v["n_solo"] == 3
    v = CAP.classifier_validation([{"marked": "solo", "suggest": "solo"}] * 5 + [{"marked": "group", "suggest": "group"}] * 5)
    assert v["validated"] and v["agreement"] == 1.0


# ---- heat interface ------------------------------------------------------------

def test_heat_term_without_status_is_v1_hadley():
    h = ENV.heat_term(30.0, 70.0, None)
    assert h["H"] == approx(1 - ENV.heat_penalty_pct(30.0, 70.0) / 100)
    assert h["penalty_pct"] == approx(6.25, abs=0.01)
    half = ENV.heat_term(30.0, 70.0, {"scale": 0.5})
    assert half["H"] == approx(1 - 0.5 * 6.2518 / 100, abs=1e-4)
    assert ENV.segment_temp(20.0, 1000.0, 3000.0) == approx(20.0 - 13.0)


# ---- planner ----------------------------------------------------------------------

def _v1():
    return {"type": "baiyue", "env": {"M": 0.93, "from": {"altitude_m": 100, "temp_c": 20, "rh_pct": 70},
                                      "to": {"altitude_m": 3400, "temp_c": 10, "rh_pct": 70}},
            "used": {"weight": {"value": 68}, "eph": {"value": 3.0}, "aet": {"value": 142}},
            "result": {"pack_factor": 0.9, "pack_kg": 9, "M": 0.93, "time_s": 20000,
                       "days": [{"day": 1, "km": 16, "gain_m": 800, "loss_m": 800, "moving_h": 5.5, "kcal": 3000,
                                 "water_ml": [2100, 2400]}]}, "warnings": []}


def test_plan_hike_uses_the_capacity_model_group_time_band_and_days():
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    c = CO.build_course(tr)
    cap = _synthetic_cap()
    inp = {"hiking": {"days": [{"ep_per_h": x, "days": 1, "solo": False} for x in (2.4, 2.8, 3.0, 3.2, 3.5, 3.9)]}}
    out = PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp=inp,
                       opts={"mode": "auto", "day_splits_km": [8.0]}, validated={}, capacity=cap)
    s = out["summary"]
    assert s["total_method"] == "capacity" and s["badge"] == "推估"
    assert s["pack_by_day"] == approx([9.0, 8.3]) and s["main"] == "group"
    assert s["group_time_s"]["p50"] == approx((16 + c["totals"]["gain_m"] / 100) / 3.1 * 3600, rel=1e-6)
    # independent segment sum: v = cap.v(...) × H (10 °C at 3400 m lapsed to each segment)
    tot = 0.0
    day, h = 0, 0.0
    for sg in CO.cut_at(c["segments"], [8.0]):
        if sg["day"] != day:
            day, h = sg["day"], 0.0
        L = CAP.day_pack(9.0, sg["day"])
        t_c = 10.0 - 0.0065 * (sg["z_mean"] - 3400)
        H = 1 - ENV.heat_penalty_pct(t_c, 70) / 100
        t = sg["dist_m"] / (cap.v(sg["grade"], L, 1.0, sg["z_mean"], h, sg["day"]) * H)
        tot += t
        h += t / 3600
    assert s["time_s"] == approx(tot, rel=1e-9)
    assert s["band"]["sigma"] > cap.sigma_loo                  # 百岳 is wider than the trail σ_LOO
    assert s["band"]["p10_s"] < s["time_s"] < s["band"]["p90_s"]
    assert [d["day"] for d in out["days"]] == [1, 2]
    assert any("±15 %" in w for w in out["warnings"])
    solo = PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp=inp,
                        opts={"mode": "auto", "trip_kind": "solo"}, validated={"hike_capacity": True}, capacity=cap)
    assert solo["summary"]["main"] == "capacity" and solo["summary"]["badge"] is None


def test_plan_hike_without_capacity_is_the_old_path():
    tr = synthetic_track({"len": 8000, "z": lambda x: 1000 + x * 0.1})
    c = CO.build_course(tr)
    out = PL.plan_hike(v1=_v1(), course=c, hike_speed=GM.fit_hike_speed([]), inp={"hiking": {"days": []}},
                       opts={"mode": "auto"}, validated={})
    assert out["summary"]["total_method"] == "v1"


def test_predict_baiyue_eph_uses_capacity_not_tobler(monkeypatch):
    from backend.api import racepower as RP
    cap = _synthetic_cap()
    monkeypatch.setattr(RP, "_grade_models", lambda: {"walk_capacity": cap})
    body = RP.PredictIn(type="baiyue", distance_km=16, gain_m=1200, loss_m=1200, days=2)
    d = {"hiking": {"eph": None, "note": "x", "biggest": None}, "aet": {"aet": 142, "source": "t"}}
    used, warns = {}, []
    RP._predict_baiyue(body, d, 68, {"M": 1.0}, used, warns)
    assert used["eph"]["value"] == approx(CAP.course_eph(cap, 16, 1200, 1200, 5.0))
    assert used["eph"]["value"] != approx(HK.tobler_eph(16, 1200, 1200), rel=0.01)
