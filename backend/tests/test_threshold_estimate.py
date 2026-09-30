import numpy as np
import pytest

from backend.engine.algorithms.threshold_estimate import (
    DriftPoint, RunThreshold, estimate_aet, estimate_lthr, run_threshold, steady_drift,
)

CP = 200.0


def synth(minutes_easy=15, minutes_hard=30, hard_power=200, hard_hr=160, easy_hr=130, dt=1.0):
    """Easy running, then a steady threshold block whose HR creeps up 150→170."""
    n_e, n_h = int(minutes_easy * 60 / dt), int(minutes_hard * 60 / dt)
    t = np.arange(n_e + n_h) * dt
    p = np.r_[np.full(n_e, 140.0), np.full(n_h, float(hard_power))]
    hr = np.r_[np.full(n_e, float(easy_hr)), np.linspace(hard_hr - 10, hard_hr + 10, n_h)]
    return t, hr, p


def test_friel_takes_last_20_minutes_of_best_30():
    t, hr, p = synth()
    r = run_threshold(t, hr, p, CP)
    assert r.p30 == pytest.approx(200, abs=0.5)
    # HR rises linearly 150→170 over the block; last 20 of 30 min average = 163.3
    assert r.friel_hr == pytest.approx(163.3, abs=0.3)
    assert r.qualifies(CP)


def test_easy_run_does_not_qualify():
    t, hr, p = synth(hard_power=160)          # 80% CP
    r = run_threshold(t, hr, p, CP)
    assert not r.qualifies(CP)


def test_hr_at_cp_needs_ten_minutes_after_warmup():
    t, hr, p = synth(minutes_hard=8)
    assert run_threshold(t, hr, p, CP).hr_at_cp is None
    t, hr, p = synth(minutes_hard=30)
    r = run_threshold(t, hr, p, CP)
    assert r.s_at_cp >= 1700
    assert r.hr_at_cp == pytest.approx(160, abs=0.5)


def test_irregular_sampling_and_gaps():
    t, hr, p = synth(dt=1.0)
    keep = np.ones(len(t), bool)
    keep[::3] = False                          # smart recording: drop every 3rd sample
    hr = hr.astype(object)
    hr[100:110] = None                         # dropouts
    r = run_threshold(t[keep], hr[keep], p[keep], CP)
    assert r.friel_hr == pytest.approx(163.3, abs=0.5)


def test_no_power_or_cp():
    t, hr, p = synth()
    assert run_threshold(t, hr, None, CP).friel_hr is None
    assert run_threshold(t, hr, p, None).friel_hr is None


def test_estimate_median_and_minimum_runs():
    runs = [RunThreshold(v, 196.0, None, 0) for v in (150, 154, 155, 158, 190)]   # 190 = bad strap
    est = estimate_lthr(runs, CP)
    assert est.value == 155 and est.n == 5            # median ignores the outlier
    few = estimate_lthr(runs[:2], CP)
    assert few.value is None and few.n == 2
    weak = estimate_lthr([RunThreshold(150, 150.0, None, 0)] * 5, CP)   # 75% CP efforts
    assert weak.value is None


def test_palladino_zone_boundaries():
    from backend.engine.zones import PALLADINO_POWER_ZONES, zone_of
    assert zone_of(0.95 * CP, CP) == "3B"
    assert zone_of(1.0099 * CP, CP) == "3B"
    assert zone_of(1.01 * CP, CP) == "4"          # supra-threshold starts above 101%
    assert zone_of(1.10 * CP, CP) == "5"
    assert zone_of(2.0 * CP, CP) == "7"
    assert zone_of(0.3 * CP, CP) == "1A"
    # contiguous, no gaps or overlaps
    for a, b in zip(PALLADINO_POWER_ZONES, PALLADINO_POWER_ZONES[1:]):
        assert a[3] == b[2]


def steady_run(hr_start, drift_bpm, minutes=60, power=150.0):
    t = np.arange(minutes * 60, dtype=float)
    return t, np.linspace(hr_start, hr_start + drift_bpm, len(t)), np.full(len(t), power)


def test_steady_drift_matches_hand_calculation():
    t, hr, p = steady_run(130, 10)
    d = steady_drift(t, hr, p, CP)
    h = hr[600:]
    half = len(h) // 2
    r1, r2 = 150 / h[:half].mean(), 150 / h[half:].mean()
    assert d.drift == pytest.approx((r1 - r2) / r1, rel=1e-6)
    assert d.hr1 == pytest.approx(h[:half].mean())


def test_steady_drift_rejects_unfair_runs():
    t, hr, p = steady_run(130, 5, minutes=30)                 # too short
    assert steady_drift(t, hr, p, CP) is None
    t, hr, p = steady_run(130, 5)
    p = np.where((t // 120) % 2 == 0, 230.0, 110.0)            # 2'/2' intervals: not steady
    assert steady_drift(t, hr, p, CP) is None
    t, hr, p = steady_run(130, 5, power=190)                   # 95% CP: not an easy run
    assert steady_drift(t, hr, p, CP) is None


def test_aet_is_where_drift_line_crosses_5_percent():
    # drift rises 1% per bpm above 120 → crosses 5% at 125... use 0.5%/bpm → 130
    pts = [DriftPoint(hr, 0.005 * (hr - 120), 150) for hr in (118, 122, 126, 130, 134, 138, 142)]
    est = estimate_aet(pts, lthr=160)
    assert est.value == 130 and est.n == 7
    assert est.below == 126                                     # highest HR still < 5%


def test_aet_refuses_to_guess():
    flat = [DriftPoint(hr, 0.02, 150) for hr in (120, 125, 130, 135, 140, 145)]
    assert estimate_aet(flat).value is None                     # no rise with HR
    few = [DriftPoint(130, 0.04, 150)] * 3
    assert estimate_aet(few).value is None
    far = [DriftPoint(hr, 0.001 * (hr - 100), 150) for hr in (120, 124, 128, 132, 136, 140)]
    assert estimate_aet(far).value is None                      # would cross at 150: extrapolation
