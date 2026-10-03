"""
Back-test v2 (2026-09-30): intensity classes, the CP lower bound and the
two-anchor sustainable power, CP-test detection, the HR-filtered hike
windows, gait-aware RE(g), the Tobler EP/h fallback, the k source, and an
independent recomputation of the classification on 3 real activities.
"""
from __future__ import annotations

import math

import numpy as np
from pytest import approx

from backend.engine.racepower import backtest as BT
from backend.engine.racepower import cptest as T
from backend.engine.racepower import difficulty as DF
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import hike as HK
from backend.engine.racepower import hikehr as HH
from backend.engine.racepower import intensity as I


# ---- intensity -------------------------------------------------------------------

def _stats(hr, p=None, n=3600, cad=None):
    t = np.arange(n, dtype=float)
    hr_a = np.full(n, float(hr)) if np.isscalar(hr) else np.asarray(hr, float)
    p_a = None if p is None else (np.full(n, float(p)) if np.isscalar(p) else np.asarray(p, float))
    return I.stats(t, hr_a, p_a, np.full(n, 10.0), None if cad is None else np.full(n, float(cad)))


def test_intensity_constants_are_the_cited_zone_boundaries():
    k = I.INTENSITY
    assert k["aet_frac_lthr"] == 0.89 and k["race_frac_lthr"] == 0.95          # Friel Z2 top, Z4 bottom
    assert (k["power_low"], k["power_high"]) == (0.80, 0.95)                   # Palladino 3-zone
    from backend.engine import zones
    assert zones.PALLADINO_3ZONE == {"low": k["power_low"], "high": k["power_high"]}
    assert next(z for z in zones.FRIEL_HR if z[0] == "4")[2] == k["race_frac_lthr"]


def test_classify_easy_steady_race():
    lthr, aet = 160.0, 142.0
    assert I.classify(_stats(125), lthr, aet)["cls"] == "easy"
    assert I.classify(_stats(150), lthr, aet)["cls"] == "steady"
    assert I.classify(_stats(158), lthr, aet)["cls"] == "race"                 # ≥ 0.95·160 = 152
    assert I.classify(_stats(120), lthr, aet, is_race=True)["cls"] == "race"   # a plan race
    sh = I.classify(_stats(125), lthr, aet)["shares"]
    assert sh["low"] == approx(1.0) and sh["high"] == approx(0.0)


def test_classify_hr_high_but_power_low_is_a_conflict_not_a_race():
    # HR says race, but 150 W for 1 h against a CP of 250 is ~60 % of what is sustainable
    c = I.classify(_stats(158, 150.0), 160.0, 142.0, cp=250.0, cp_is_floor=True)
    assert c["cls"] == "steady" and c.get("conflict")
    ok = I.classify(_stats(158, 245.0), 160.0, 142.0, cp=250.0, cp_is_floor=True)
    assert ok["cls"] == "race"
    # a CP known only as a floor never promotes an HR-easy run to race
    assert I.classify(_stats(125, 260.0), 160.0, 142.0, cp=250.0, cp_is_floor=True)["cls"] == "easy"


def test_classify_drift_breaks_the_tie_near_aet():
    n = 3600
    hr = np.full(n, 141.0)
    p = np.concatenate([np.full(n // 2, 200.0), np.full(n // 2, 170.0)])      # same HR, power fades → Pw:HR drift 15 %
    c = I.classify(I.stats(np.arange(n, dtype=float), hr, p, np.full(n, 10.0)), 160.0, 142.0)
    assert c["drift"] > 0.05 and c["cls"] == "steady"
    flat = I.classify(I.stats(np.arange(n, dtype=float), hr, np.full(n, 185.0), np.full(n, 10.0)), 160.0, 142.0)
    assert abs(flat["drift"]) < 1e-9 and flat["cls"] == "easy"


def test_classify_power_only_and_walk_share():
    c = I.classify(I.stats(np.arange(3600.0), None, np.full(3600, 150.0), np.full(3600, 10.0)), 160.0, 142.0, cp=250.0)
    assert c["cls"] == "easy" and c["basis"] == "power"
    assert _stats(125, cad=50)["walk_share"] == approx(1.0) and _stats(125, cad=85)["walk_share"] == approx(0.0)


# ---- lower bound, two anchors ------------------------------------------------------

def test_cp_lower_bound_is_the_F1_algebra():
    lb = DF.cp_lower_bound([(8397.0, 178.8, "half"), (600.0, 400.0, "short")], 7300.0, 1884.0, -0.07)
    assert lb["cp_min"] == approx(178.8 * (1884.0 / 8397.0) ** -0.07)
    assert lb["who"] == "half" and all(r["t_s"] >= 1200 for r in lb["rows"])  # < 20 min never binds
    assert DF.p_sus(8397.0, lb["cp_min"], 7300.0, 1884.0, -0.07) == approx(178.8)


def test_two_anchor_p_sus():
    ftp, tte, cp2, w = 196.0, 1871.0, 204.0, 13100.0
    assert DF.p_sus(600.0, ftp, w, tte, -0.07, cp2=cp2) == approx(cp2 + w / 600.0)        # F2 on the test pair
    assert DF.p_sus(tte, ftp, w, tte, -0.07, cp2=cp2) == approx(ftp)                      # F1 anchored at mFTP
    assert DF.p_sus(7200.0, ftp, w, tte, -0.07, cp2=cp2) == approx(ftp * (7200 / tte) ** -0.07)
    b = min(DF.SHORT_MAX_S, tte / 2)
    assert DF.p_sus(b, ftp, w, tte, -0.07, cp2=cp2) == approx(cp2 + w / b)                # continuous at b
    assert DF.t_lim(DF.p_sus(3000.0, ftp, w, tte, -0.07, cp2=cp2), ftp, w, tte, -0.07, cp2=cp2) == approx(3000.0, rel=1e-6)
    # the bound on the anchor ignores the bridge when the pair is separate
    lb = DF.cp_lower_bound([(1300.0, 230.0), (5400.0, 183.0)], w, tte, -0.07, cp2=cp2, min_s=tte)
    assert lb["t_s"] == 5400.0


def test_effort_band_from_the_data_spread_and_the_inconsistency_flag():
    e = DF.effort(180.0, 8000.0, 190.0, 7000.0, 1900.0, -0.07, cp_spread=[174.0, 211.0], lower_bound=198.0)
    assert e["band_cp"] == [174.0, 211.0] and e["inconsistent"]
    lo_f = 180.0 / DF.p_sus(8000.0, 211.0, 7000.0, 1900.0, -0.07 + DF.K_UNCERTAINTY)   # beyond TTE a flatter k is kinder
    assert min(e["band"]) == approx(lo_f, rel=1e-6)
    assert not DF.effort(180.0, 8000.0, 200.0, 7000.0, 1900.0, -0.07, lower_bound=198.0)["inconsistent"]


# ---- CP tests ---------------------------------------------------------------------

def _laps(p3, hr3, p12, hr12):
    return [{"t": 540.0, "p": 168.0, "hr_max": 138}, {"t": 180.0, "p": p3, "hr_max": hr3},
            {"t": 960.0, "p": 115.0, "hr_max": 150}, {"t": 720.0, "p": p12, "hr_max": hr12},
            {"t": 511.0, "p": 105.0, "hr_max": 165}]


def test_cptest_detects_a_non_maximal_3min_and_falls_back_to_one_bout():
    t = T.detect(_laps(234.0, 152, 238.0, 177))       # the synthetic 範例跑者 test (fixtures/make_cp_test_fixture.py)
    s, l = t["bouts"]
    assert not s["maximal"] and l["maximal"] and "低 25 bpm" in s["why"]
    e = T.estimate(t, 70.0, "male")
    assert e["method"] == "single_bout" and e["w_prime"] == 13100.0            # Ruiz-Alias 2025 men
    assert e["cp"] == approx(238.0 - 13100.0 / 720.0)
    assert e["cp_range"] == [approx(238.0 - 17100.0 / 720.0), approx(238.0 - 9100.0 / 720.0)]


def test_cptest_two_point_when_both_bouts_are_maximal():
    t = T.detect(_laps(300.0, 170, 260.0, 172))
    e = T.estimate(t, 68.0)
    assert e["method"] == "two_point"
    assert e["cp"] == approx((260 * 720 - 300 * 180) / 540) and e["w_prime"] == approx((300 - e["cp"]) * 180)
    assert T.detect([{"t": 3000.0, "p": 180.0}, {"t": 600.0, "p": 150.0}]) is None      # not a test


def test_workout_review_cp_test_windows_never_overlap():
    from backend.engine import workout_review as WR
    warm, rest = np.full(600, 130.0), np.full(1000, 110.0)
    twelve = np.concatenate([np.full(660, 220.0), np.full(60, 245.0)])          # a strong last minute
    p = np.concatenate([warm, np.full(180, 217.0), rest, twelve, np.full(400, 120.0)])
    r = WR.cp_test(np.arange(len(p), dtype=float), p)
    assert r["p3"] == approx(217.0) and r["method"] == "1pt_prior"             # not the 3′ inside the 12′ bout
    assert r["cp"] == approx(r["p12"] - 13100.0 / 720.0)
    assert WR.looks_like_cp_test(r, 196.0)                                     # still a test session
    p2 = np.concatenate([warm, np.full(180, 300.0), rest, np.full(720, 240.0), np.full(400, 120.0)])
    r2 = WR.cp_test(np.arange(len(p2), dtype=float), p2)
    assert r2["method"] == "2pt" and r2["cp"] == approx((240 * 720 - 300 * 180) / 540)


# ---- hike HR windows ---------------------------------------------------------------

def _win(k, g=0.2, v=0.6, hr=150.0, z=1000.0):
    return {"k": k, "g": g, "v": v, "hr": hr, "z": z}


def test_hike_filter_needs_three_consecutive_windows_at_or_above_aet():
    rows = [_win(0), _win(1), _win(3), _win(4), _win(5), _win(6, g=0.02), _win(7, hr=120), _win(8, v=0.2)]
    out = HH.filter_windows(rows, aet=140.0)
    assert [w["k"] for w in out] == [3, 4, 5]          # 0–1 too short; 6 is flat; 7 below AeT; 8 too slow
    assert out[0]["vam"] == approx(0.6 * 0.2 * 3600)
    fast = HH.filter_windows([_win(i, v=4.0) for i in range(4)], aet=140.0)
    assert fast == []                                   # 2880 m/h: above the vertical-km record → noise


def test_hike_altitude_factor_recovers_a_known_decline():
    rng = np.random.default_rng(1)
    wins = []
    for i in range(200):
        z = 500 + 15 * i
        vam = 700 * math.exp(math.log(1 - 0.063) * z / 1000) * (1 + 0.01 * rng.standard_normal())
        wins.append({"g": 0.2, "hr": 150.0, "z": z, "vam": vam, "v": vam / 0.2 / 3600})
    a = HH.altitude_factor(wins, 140.0, 160.0)
    assert a["enough"] and a["pct_per_km"] == approx(-6.3, abs=0.3)


def test_hike_fatigue_is_the_hr_shift_at_the_same_vam():
    wins = [{"trip": 1, "day": 1, "vam": 400 + 20 * i, "hr": 130 + 0.05 * (400 + 20 * i)} for i in range(8)]
    wins += [{"trip": 1, "day": 2, "vam": 400 + 20 * i, "hr": 135 + 0.05 * (400 + 20 * i)} for i in range(8)]
    f = HH.fatigue(wins)
    assert f["trips"] == 1 and f["days"][0]["hr_shift_bpm"] == approx(5.0, abs=1e-6)


# ---- gait-aware RE, technicality, Tobler EP/h -----------------------------------------

def test_gait_re_uses_the_walking_bins_where_the_athlete_walks():
    s = []
    for i in range(60):
        s.append({"g": 0.0, "re": 1.0, "v": 3.0, "a": 1, "run": 1.0, "trail": True})
        s.append({"g": 0.20, "re": 0.40, "v": 1.0, "a": 1, "run": 0.1, "trail": True})   # walked
        s.append({"g": 0.20, "re": 0.30, "v": 1.2, "a": 2, "run": 0.9, "trail": False})  # one runner's bin
    g = GM.fit_gait_re([x for x in s if not (x["g"] == 0.2 and x["run"] > 0.5)], 1.0)
    assert g.walked(0.20) and not g.walked(0.0)
    assert g.re(0.20) == approx(g.walk.re(0.20)) and g.walk.re(0.20) > g.run.re(0.20)
    assert GM.re_prior(0.2, 1.0, walking=True) == approx(3.6 / GM.minetti.cost_of_transport(0.2, walking=True))


def test_trail_technicality_applies_on_flats_and_descents_only():
    s = [{"g": 0.0, "re": 1.0, "v": 3.0, "a": 1, "run": 1.0, "trail": False} for _ in range(60)]
    s += [{"g": 0.0, "re": 0.9, "v": 2.7, "a": 2, "run": 1.0, "trail": True} for _ in range(60)]
    g = GM.fit_gait_re(s, 1.0, {1: "easy", 2: "race"})
    f, which = g.for_trail("race").tech_factor()
    assert which == "race" and f == approx(0.9 / g.run.re(0.0), rel=1e-6)
    tg = g.for_trail("race")
    # 2026-10-02: on trail the grade bin's factor (shrunk n/(n+30) to 1) is used where the bin has windows
    fb = g.tech_bins["±2%"]
    assert fb["f"] == approx((60 * f + 30) / 90, rel=1e-6) and tg.tech_at(0.0) == (fb["f"], "bin")
    assert tg.re(0.0) == approx(g.re(0.0) * fb["f"]) and tg.re(0.10) == approx(g.re(0.10))
    # no window in a bin → the single per-class factor
    assert tg.tech_at(-0.10) == (f, "race")


def test_tobler_eph_fallback():
    assert HK.tobler_eph(10.0, 0.0) == approx(GM.tobler_kmh(0.0))              # flat: EP = km → 5.04 km/h
    up = HK.tobler_eph(10.0, 1000.0, 1000.0)
    h = 5.0 / GM.tobler_kmh(0.2) + 5.0 / GM.tobler_kmh(-0.2)
    assert up == approx(20.0 / h)


# ---- k source ------------------------------------------------------------------------

def test_k_uses_the_case_distance_and_only_race_priors():
    inp = {"riegel": {"valid": False}, "auto_prior": {"km": 10.0, "time_s": 45 * 60}}
    k10, src = BT._k_of(inp, 10000.0)
    k42, _ = BT._k_of(inp, 42195.0)
    assert "查表" in src and k10 != k42                         # the table row of THIS distance
    assert BT._k_of({"riegel": {}, "auto_prior": None}, 42195.0) == (-0.07, "預設 −0.07")
