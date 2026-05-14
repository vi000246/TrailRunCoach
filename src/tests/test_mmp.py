"""
MMP algorithm validation tests.

These tests verify the implementation against known good values.
Run after tuning the algorithm against WKO5 output.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from mmp import compute_mmp, mmp_at


def test_mmp_constant_power():
    """With constant power, MMP at all durations == that power."""
    power = np.full(3600, 200.0)
    time = np.arange(3600, dtype=float)
    result = compute_mmp(power, time)
    assert result[1] == pytest.approx(200.0, rel=0.01)
    assert result[300] == pytest.approx(200.0, rel=0.01)
    assert result[3600] == pytest.approx(200.0, rel=0.01)


def test_mmp_single_sprint():
    """5-second sprint at 800W embedded in 60min at 150W."""
    power = np.full(3600, 150.0)
    power[1800:1805] = 800.0
    time = np.arange(3600, dtype=float)
    result = compute_mmp(power, time)

    # 5s MMP should include the sprint
    expected_5s = (800 * 5) / 5  # pure sprint window
    assert result[5] == pytest.approx(expected_5s, rel=0.01)

    # 300s MMP: best 300-second window containing the sprint
    # sprint adds (800-150)*5 = 3250 W extra over 300s
    expected_300 = 150 + (800 - 150) * 5 / 300
    assert result[300] == pytest.approx(expected_300, rel=0.01)


def test_mmp_shorter_than_duration():
    """Durations longer than ride are excluded (value 0)."""
    power = np.full(60, 200.0)
    time = np.arange(60, dtype=float)
    result = compute_mmp(power, time, durations=[30, 60, 120, 300])
    assert result[30] == pytest.approx(200.0, rel=0.01)
    assert result[60] == pytest.approx(200.0, rel=0.01)
    # 120s and 300s windows don't fit
    assert result.get(120, 0.0) == 0.0 or result[120] == pytest.approx(0.0)


def test_mmp_monotone_with_physiological_data():
    """
    MMP is non-increasing when power decays with effort duration.

    For a signal that strictly decreases over time (maximal effort model),
    the best k-sample window is always the first k samples, so
    MMP[d1] >= MMP[d2] for all d1 < d2.
    """
    # Simulate exponentially decaying max-effort power (physiological curve shape)
    t = np.arange(3600, dtype=float)
    power = 500.0 * np.exp(-t / 1200.0)  # 500W at 0s, decaying with 20min tau
    time = t.copy()
    result = compute_mmp(power, time)
    durations = sorted(result.keys())
    for i in range(len(durations) - 1):
        assert result[durations[i]] >= result[durations[i + 1]] - 1e-6, (
            f"MMP[{durations[i]}]={result[durations[i]]:.4f} < "
            f"MMP[{durations[i+1]}]={result[durations[i+1]]:.4f}"
        )


def test_mmp_interpolation():
    """mmp_at interpolates correctly between breakpoints."""
    curve = {5: 600.0, 10: 500.0}
    val = mmp_at(curve, 7)
    expected = 600 + (500 - 600) * (7 - 5) / (10 - 5)
    assert val == pytest.approx(expected, rel=0.001)


def test_mmp_empty_power():
    """Empty input returns zeros."""
    result = compute_mmp(np.array([]), np.array([]))
    for v in result.values():
        assert v == 0.0
