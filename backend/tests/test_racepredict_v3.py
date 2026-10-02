"""
Race prediction v3 (docs/research/unsourced-rules.md §0, A2, A3, A8; 2026-10-02):
the full-effort HR curve x*(T), durability δ shrunk to the 0.05 /h prior on
cleaned windows (+ fuelling split), heat acclimation a = 0 unless the HRC
slope test says otherwise, the separate non-moving time, the athlete's heat β
in the trail HR model, and the trail pass rule (8 % / bootstrap upper bound).
Synthetic data only: no WKO5 folder, no app DB.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from backend.engine import activity_tags as AT
from backend.engine.racepower import backtest as BT
from backend.engine.racepower import heatacc as HA
from backend.engine.racepower import nonmoving as NM
from backend.engine.racepower import trailhr as TH


# ---- x*(T) ------------------------------------------------------------------

def test_xstar_prior_is_the_least_squares_line_through_the_anchors():
    p = TH.xstar_prior()
    L = np.log([0.5, 3.0, 12.0])
    b, a = np.polyfit(L, [1.00, 0.90, 0.85], 1)
    assert p["s"] == pytest.approx(-b) and p["x0"] == pytest.approx(a)
    # falls with duration, near each anchor
    assert TH.xstar_at(p, 0.5) == pytest.approx(0.996, abs=0.005)
    assert TH.xstar_at(p, 3.0) == pytest.approx(0.910, abs=0.005)
    assert TH.xstar_at(p, 12.0) == pytest.approx(0.844, abs=0.005)
    # clamped outside 15 min – 30 h
    assert TH.xstar_at(p, 0.01) == TH.xstar_at(p, 0.25) and TH.xstar_at(p, 99) == TH.xstar_at(p, 30)


def test_fit_xstar_without_samples_is_the_prior():
    f = TH.fit_xstar([])
    assert f["kind"] == "prior" and f["x0"] == pytest.approx(TH.xstar_prior()["x0"]) and f["n"] == 0


def test_fit_xstar_moves_the_level_fast_and_the_slope_slowly():
    prior = TH.xstar_prior()
    # races all 2–3 h at x ≈ 1.15 (an estimated LTHR below the true one): the level follows the data …
    s = [{"T_h": t, "x": 1.15} for t in (2.0, 2.2, 2.5, 2.8, 3.0, 2.4)]
    f = TH.fit_xstar(s)
    assert f["kind"] == "shrunk" and f["n"] == 6
    lvl = TH.xstar_at(f, 2.4)
    w = 6 / (6 + TH.XSTAR["level_weight"])
    assert lvl == pytest.approx(w * 1.15 + (1 - w) * TH.xstar_at(prior, 2.4), abs=0.01)
    # … the slope stays near the literature (the samples span little of ln T)
    assert f["s"] == pytest.approx(prior["s"], abs=0.01)
    # one sample barely moves the level
    one = TH.fit_xstar([{"T_h": 2.0, "x": 1.15}])
    assert TH.xstar_at(prior, 2.0) < TH.xstar_at(one, 2.0) < 1.15
    # samples outside 15 min – 30 h are ignored
    assert TH.fit_xstar([{"T_h": 0.1, "x": 1.3}])["n"] == 0


def test_fit_xstar_learns_a_slope_from_races_of_very_different_lengths():
    true = {"x0": 1.10, "s": 0.10}
    s = [{"T_h": t, "x": TH.xstar_at(true, t)} for t in (0.4, 0.8, 1.5, 3, 6, 10, 14, 20) * 3]
    f = TH.fit_xstar(s)
    assert f["s"] > TH.xstar_prior()["s"] + 0.03


def test_predict_race_solves_the_fixed_point_on_x_star_of_t():
    pts = [{"x": x, "v": 2 + 4 * x, "T_h": 2.0, "eff_km": 10.0} for x in np.linspace(0.8, 1.2, 8)]
    m = TH.fit(pts, 0.05)
    xs = TH.xstar_prior()
    t, x = TH.predict_race(m, 30.0, xs)
    assert x == pytest.approx(TH.xstar_at(xs, t / 3600.0), abs=1e-4)
    assert t == pytest.approx(TH.predict_time(m, 30.0, x), rel=1e-4)
    # a longer course → a lower full-effort HR level
    t2, x2 = TH.predict_race(m, 80.0, xs)
    assert t2 > t and x2 < x
    # effort target and a heat shift lower the level the speed is bought at
    _, xf = TH.predict_race(m, 30.0, xs, f=0.9)
    _, xh = TH.predict_race(m, 30.0, xs, x_shift=0.04)
    assert xf < x and xh < x


def test_auto_full_effort_threshold_follows_the_prior_curve():
    assert TH.auto_max_frac(2.0) == pytest.approx(0.900, abs=0.002)
    assert TH.auto_max_frac(8.0) == pytest.approx(0.834, abs=0.002)
    assert TH.auto_max_frac(None) == TH.TRAILHR["x_default"]
    s = {"hr_avg": 140.0, "above_aet": 0.30, "low_share": 0.2, "moving_s": 8 * 3600.0, "rest_share": 0.02}
    lthr, aet = 165.0, 147.0
    # 140 / 165 = 0.85: below the old 0.90 and the old 2/3 above-AeT share, full effort for an 8 h race
    assert AT.effort_hr(s, lthr, aet)["effort"] != "max"
    assert AT.effort_hr(s, lthr, aet, max_frac=TH.auto_max_frac(8.0))["effort"] == "max"
    assert AT.effort_hr({**s, "rest_share": 0.2}, lthr, aet, max_frac=TH.auto_max_frac(8.0))["effort"] == "hard_with_rests"


# ---- durability δ -------------------------------------------------------------

def test_delta_shrinks_to_the_clark_prior():
    d = TH.shrink_delta([])
    assert d["delta"] == 0.05 and d["n"] == 0 and d["warning"] is None
    d = TH.shrink_delta([0.08, 0.10, 0.12])
    assert d["delta"] == pytest.approx((3 * 0.10 + 3 * 0.05) / 6)
    big = TH.shrink_delta([0.4, 0.5, 0.45, 0.6])
    assert big["delta"] == TH.TRAILHR["delta_max"] and big["warning"]


def _course_run(profile, delta, hours=3.0, hr=150.0, seed=0):
    """A 1-Hz synthetic trail run: grade g(t) from `profile`, speed = the
    grade speed × (1 − δ·(t − 1 h)⁺) × noise, constant HR."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, hours * 3600.0)
    g = np.array([profile(x / 3600.0) for x in t])
    v0 = np.where(g > 0, 2.8 * np.exp(-8 * g), 2.8 * np.exp(3 * g) + 0.3)
    v = v0 * (1 - delta * np.clip(t / 3600.0 - 1.0, 0, None)) * np.exp(0.03 * rng.standard_normal(len(t)))
    d = np.cumsum(v)
    z = 500.0 + np.cumsum(v * g)
    return t, d, z, np.full(len(t), hr), np.ones(len(t), bool)


def test_terrain_matched_delta_recovers_fatigue_on_a_rolling_course():
    rolling = lambda h: 0.10 * math.sin(2 * math.pi * h * 3)          # 20-min hills all run long
    for true in (0.0, 0.08):
        t, d, z, hr, mv = _course_run(rolling, true, seed=4)
        r = TH.within_run_delta(TH.terrain_windows(t, d, z, hr, mv))
        assert r is not None and r["delta"] == pytest.approx(true, abs=0.03) and r["se"] < 0.03


def test_terrain_matched_delta_ignores_a_climb_first_course():
    # up for 1.5 h, down after: no fatigue at all — the old effort-km/HR ratio reads a big decline,
    # the terrain-matched δ finds no shared terrain (or a small value), never the course order
    updown = lambda h: 0.12 if h < 1.5 else -0.12
    t, d, z, hr, mv = _course_run(updown, 0.0, seed=5)
    r = TH.within_run_delta(TH.terrain_windows(t, d, z, hr, mv))
    assert r is None or abs(r["delta"]) < 0.05


def test_pooled_delta_random_effects_and_ci():
    p = TH.pool_deltas([{"delta": 0.05, "se": 0.02}, {"delta": 0.07, "se": 0.02}, {"delta": 0.06, "se": 0.04}])
    assert 0.05 < p["mean"] < 0.07 and p["ci95"][0] < p["mean"] < p["ci95"][1] and p["tau"] == 0.0
    het = TH.pool_deltas([{"delta": -0.3, "se": 0.05}, {"delta": 0.3, "se": 0.05}, {"delta": 0.0, "se": 0.05}])
    assert het["tau"] > 0.2 and het["se"] > 0.1                        # disagreement widens the CI
    s = TH.shrink_delta([0.05, 0.07, 0.06], [0.02, 0.02, 0.04])
    assert s["method"] == "pooled" and s["delta"] == pytest.approx((3 * p["mean"] + 3 * 0.05) / 6)
    assert s["raw_ci95"] == pytest.approx(p["ci95"])
    assert TH.pool_deltas([]) is None


def test_measured_delta_kept_only_when_it_lowers_the_loo_error():
    rng = np.random.default_rng(1)
    pts = []
    for i in range(14):
        x, T = 0.8 + 0.03 * i, 1.0 + 0.25 * i
        v = (2 + 4 * x) * TH.dbar(T, 0.12) * (1 + 0.01 * rng.standard_normal())
        pts.append({"x": x, "v": v, "T_h": T, "eff_km": v * T})
    real = TH.choose_delta(pts, TH.delta_by_fuel([{"delta": 0.12}] * 6))
    assert real["choice"] == "measured" and real["loo_measured"] < real["loo_prior"]
    flat = [{**p, "v": (2 + 4 * p["x"]), "eff_km": (2 + 4 * p["x"]) * p["T_h"]} for p in pts]
    wrong = TH.choose_delta(flat, TH.delta_by_fuel([{"delta": 0.40}] * 6))
    assert wrong["choice"] == "prior" and wrong["delta"] == TH.TRAILHR["delta_prior"]


def test_fuelling_tags_split_delta_only_with_both_groups():
    assert TH.fuel_of(["有補給"]) is True and TH.fuel_of(["沒補給"]) is False and TH.fuel_of(["山徑"]) is None
    assert TH.fuel_of(["unfuelled long run"]) is False
    rows = [{"delta": 0.05, "fuel": True}, {"delta": 0.07, "fuel": True},
            {"delta": 0.14, "fuel": False}, {"delta": 0.16, "fuel": False}]
    r = TH.delta_by_fuel(rows)
    assert r["split"] and r["use"] == r["fuelled"]["delta"] < r["unfuelled"]["delta"]
    assert not TH.delta_by_fuel(rows[:3])["split"]


def test_durability_clean_mask_cuts_the_warm_up_tail_and_restarts():
    from backend.engine.racepower import athlete as A
    t = np.arange(0, 3 * 3600.0)
    kmh = np.full(len(t), 9.0)
    kmh[1000:1060] = 0.0                       # an early stop (adaptive start: < 20 min)
    kmh[5000:5120] = 0.0                       # a 2-min aid station
    kmh[-700:-600] = 0.0                       # return-leg stops near the end
    kmh[-400:-380] = 0.0
    hr = np.full(len(t), 150.0)
    keep, cuts = A.durability_clean_mask(t, kmh, hr)
    assert not keep[:1059 + 60].any() and keep[1200] and cuts["start_s"] == pytest.approx(1059 + 60, abs=2)
    assert not keep[5121:5180].any() and keep[5300]        # 60 s re-acceleration after the stop
    assert not keep[-700:].any() and cuts["tail_s"] > 0


# ---- heat --------------------------------------------------------------------

def test_heat_shift_uses_the_athletes_beta():
    from backend.engine import heat as HT
    assert TH.heat_shift(150.0, 160.0) == pytest.approx(HT.HR_BETA * 30 / 160)
    assert TH.heat_shift(None, 160.0) == 0.0 and TH.heat_shift(150.0, None) == 0.0
    p = TH.heat_adjust(TH.run_point(12.0, 800.0, 2 * 3600.0, 160.0, 160.0), 150.0)
    assert p["x_raw"] == pytest.approx(1.0) and p["x"] == pytest.approx(1.0 - HT.HR_BETA * 30 / 160)


def test_hrc_slope_test_and_default_a_zero():
    d0 = dt.date(2026, 6, 1)
    falling = [{"date": (d0 + dt.timedelta(days=4 * i)).isoformat(), "hrc": 3.0 - 0.1 * i + (0.05 if i % 2 else -0.05)}
               for i in range(12)]
    flat = [{"date": r["date"], "hrc": 2.0 + (0.3 if i % 2 else -0.3)} for i, r in enumerate(falling)]
    assert HA.hrc_slope_test(falling)["supported"]
    assert not HA.hrc_slope_test(flat)["supported"]
    assert not HA.hrc_slope_test(falling[:5])["supported"]               # too few
    assert HA.acclimation_a(None, 0.75)[0] == 0.0
    assert HA.acclimation_a(HA.hrc_slope_test(falling), 0.75)[0] == 0.75


def test_planner_heat_acclimation_does_not_discount_without_evidence():
    from backend.engine.racepower import planner as PL
    h = PL.heat_acclimation({"heat_acclimatisation": {"mode": "acclimatised"}})
    assert h["s"] == 0.9 and h["a"] == 0.0 and not h["a_supported"]
    assert h["scenarios"]["center"] == (0.0, 0.9) and h["scenarios"]["low"][0] == 0.0
    assert "不支持" in h["source"]


# ---- non-moving time ------------------------------------------------------------

def _act(stops):
    t = np.arange(0, 3 * 3600.0)
    mv = np.ones(len(t), bool)
    for a, b in stops:
        mv[a:b] = False
    return NM.run_row(t, mv)


def test_nonmoving_rows_split_long_and_short_stops():
    r = _act([(1000, 1180), (5000, 5020), (8000, 8300)])
    assert r["n_long"] == 2 and r["long_s"] == pytest.approx(480.0) and r["short_s"] == pytest.approx(20.0)
    assert r["elapsed_s"] == pytest.approx(r["moving_s"] + r["stopped_s"])
    # a recording gap (auto-pause) is stopped time
    t = np.concatenate([np.arange(0, 3600.0), np.arange(3900.0, 7200.0)])
    g = NM.run_row(t, np.ones(len(t), bool))
    assert g["long_s"] == pytest.approx(301.0)


def test_nonmoving_predict_user_stations_and_rate():
    prof = NM.profile([_act([(1000, 1180), (8000, 8300)]), _act([(2000, 2240)])])
    assert prof["n"] == 2 and prof["stop_len_s"] == pytest.approx(240.0)
    assert NM.profile([_act([])]) is None                                   # one race is not a profile
    rate = NM.predict(prof, 4 * 3600.0)
    assert rate["method"] == "rate" and rate["long_s"] == pytest.approx(prof["long_per_h_s"] * 4)
    st = NM.predict(prof, 4 * 3600.0, [{"km": 10, "type": "aid"}, {"km": 20, "type": "water"}, {"km": 30, "type": "big"}])
    assert st["method"] == "stations" and st["long_s"] == pytest.approx(2 * 240.0)
    user = NM.predict(prof, 4 * 3600.0, [{"km": 10, "minutes": 3}, {"km": 20, "minutes": 2}])
    assert user["method"] == "user" and user["long_s"] == pytest.approx(300.0)
    assert user["p25_s"] <= user["total_s"] <= user["p75_s"]
    assert NM.predict(None, 3600.0) is None and NM.predict(prof, None) is None


def test_planner_trail_estimate_reports_moving_and_total_apart():
    from backend.engine.racepower import planner as PL
    pts = [{"x": x, "v": 2 + 4 * x, "T_h": 2.0, "eff_km": 10.0} for x in np.linspace(0.8, 1.2, 8)]
    m = TH.fit(pts, 0.05)
    m["xstar"] = TH.fit_xstar([])
    m["nonmoving"] = NM.profile([_act([(1000, 1180)]), _act([(2000, 2240)])])
    e = PL.trail_hr_estimate(m, 30.0, 1500.0, 1.0)
    assert e["time_total_s"] == pytest.approx(e["time_s"] + e["nonmoving"]["total_s"])
    assert e["x_star"] == pytest.approx(TH.xstar_at(m["xstar"], e["time_s"] / 3600.0), abs=1e-3)
    hot = PL.trail_hr_estimate(m, 30.0, 1500.0, 1.0, hadley=160.0, lthr=160.0)
    assert hot["heat_beta"] and hot["time_s"] > e["time_s"] and hot["x"] < hot["x_star"]
    # an older model without the curve still works (single race level)
    old = {k: v for k, v in m.items() if k not in ("xstar", "nonmoving")}
    o = PL.trail_hr_estimate({**old, "x_race": 1.0}, 30.0, 1500.0)
    assert o["time_s"] > 0 and o["nonmoving"] is None and o["time_total_s"] is None


# ---- downhill technicality by grade bin, p50 cap -------------------------------------

def test_trail_technicality_by_grade_bin_and_median_descent_cap():
    from backend.engine.racepower import grade_model as GM
    rng = np.random.default_rng(3)
    s = []
    for i in range(400):
        g = -0.20 + 0.22 * rng.random()
        trail = i % 2 == 0
        slow = 0.75 if (trail and g < -0.15) else 1.0          # steep trail descents are 25 % slower
        re = 1.0 / GM.minetti.grade_factor(g, downhill_floor=GM.DOWNHILL_FLOOR) * slow
        s.append({"g": g, "re": re, "v": 3.0 + 2 * rng.random(), "a": i, "run": 1.0, "trail": trail})
    m = GM.fit_gait_re(s, 1.0)
    steep = m.tech_bins["≤ −15%"]
    assert steep["raw"] < 0.95 and steep["f"] < m.tech_bins["±2%"]["f"]
    w = steep["n"] / (steep["n"] + GM.SHRINK_N)
    assert steep["f"] == pytest.approx(w * steep["raw"] + (1 - w), abs=1e-9)
    tr = m.for_trail("race")
    assert tr.tech_at(-0.18)[1] == "bin" and tr.re(-0.18) < m.re(-0.18)
    assert GM.tech_bin(-0.15) == "−15…−8%" and GM.tech_bin(0.0) == "±2%" and GM.tech_bin(0.05) is None
    # the trail descent cap is the bin's median speed, the road one its p90
    assert tr.v_max(-0.10) < m.v_max(-0.10)


# ---- the pass rule ---------------------------------------------------------------

def test_bootstrap_upper_bound_and_trail_pass_rule():
    assert BT.THRESHOLDS["trail"] == 0.08 and BT.TARGETS["trail"] == 0.06
    assert BT.boot_ub([0.01, 0.02]) is None
    tight = [0.05, -0.04, 0.06, -0.05, 0.05, 0.04, -0.06]
    ub = BT.boot_ub(tight)
    assert ub >= float(np.median(np.abs(tight))) and ub == BT.boot_ub(tight)          # seeded
    assert BT.pass_check(tight, 0.08)["passed"]
    wide = [0.02, -0.03, 0.04, 0.20, -0.25, 0.18, 0.05]
    pc = BT.pass_check(wide, 0.08)
    assert pc["median_abs"] <= 0.08 and not pc["passed"] and pc["ub80"] > pc["ub_limit"]
    assert not BT.pass_check(tight[:4], 0.08)["passed"]                     # n < 5 never passes
    assert BT.pass_check(tight * 2, 0.08)["ub_limit"] == 0.08                # n ≥ 10: no slack
    assert math.isclose(BT.pass_check(tight, 0.08)["ub_limit"], 0.10)
