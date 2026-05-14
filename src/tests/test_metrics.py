"""
Metrics calculation tests.

Verified against Coggan/Allen "Training and Racing with a Power Meter" formulas.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from metrics import (
    normalized_power,
    intensity_factor,
    training_stress_score,
    average_power,
    compute_all_metrics,
)


def test_np_constant_power():
    """NP of constant power == that power."""
    power = np.full(3600, 200.0)
    result = normalized_power(power)
    assert result == pytest.approx(200.0, rel=0.01)


def test_np_higher_than_average():
    """NP is always >= average power for variable efforts."""
    np.random.seed(1)
    power = np.abs(np.random.normal(200, 80, 3600))
    avg = float(np.mean(power))
    np_val = normalized_power(power)
    assert np_val >= avg - 1.0  # NP >= avg (NP can only be >= avg due to 4th power)


def test_np_interval_workout():
    """
    Classic 4x8min at 300W with 4min recovery at 100W.
    NP should be significantly higher than avg power.
    """
    power = []
    # 4 intervals: 8min on, 4min off
    for _ in range(4):
        power.extend([300.0] * 480)
        power.extend([100.0] * 240)
    power = np.array(power)
    avg = float(np.mean(power))
    np_val = normalized_power(power)
    assert np_val > avg
    assert np_val > 220  # expect ~230W-ish NP vs ~220W avg


def test_tss_one_hour_at_ftp():
    """1 hour exactly at FTP = TSS 100."""
    ftp = 250.0
    tss = training_stress_score(3600, ftp, ftp)
    assert tss == pytest.approx(100.0, rel=0.01)


def test_tss_half_intensity():
    """0.5 IF for 1 hour = TSS 25 (0.5² × 100)."""
    ftp = 250.0
    half_ftp = 125.0
    tss = training_stress_score(3600, half_ftp, ftp)
    assert tss == pytest.approx(25.0, rel=0.01)


def test_intensity_factor_at_ftp():
    """IF at FTP == 1.0."""
    assert intensity_factor(250.0, 250.0) == pytest.approx(1.0)


def test_average_power_excludes_zeros():
    """avg_power skips zeros (stopped segments)."""
    power = np.array([0.0, 0.0, 200.0, 200.0, 200.0])
    avg = average_power(power)
    assert avg == pytest.approx(200.0, rel=0.01)


def test_compute_all_metrics_no_ftp():
    """Without FTP, TSS and IF are None."""
    power = np.full(1800, 200.0)
    result = compute_all_metrics(power=power, ftp_w=None, duration_s=1800)
    assert result["tss"] is None
    assert result["intensity_factor"] is None
    assert result["normalized_power_w"] == pytest.approx(200.0, rel=0.01)
