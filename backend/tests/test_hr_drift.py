"""Aerobic decoupling (Pa:HR) — backend/engine/algorithms/trail.py.

Convention (TrainingPeaks / Uphill Athlete): ratio = speed / HR, and
decoupling % = (first half - second half) / first half * 100, so POSITIVE
means drift. Uphill Athlete's AeT test passes under 5%.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import numpy as np
import pytest

from backend.engine.algorithms.trail import compute_hr_drift

N = 600
PACE = 1 / 3.0          # s per m == 3 m/s


def _halves(hr1, hr2, pace1=PACE, pace2=PACE):
    hr = np.array([hr1] * (N // 2) + [hr2] * (N // 2), dtype=float)
    gap = np.array([pace1] * (N // 2) + [pace2] * (N // 2), dtype=float)
    return compute_hr_drift(hr, gap)


def test_steady_effort_has_no_decoupling():
    assert _halves(140, 140)["decoupling_pct"] == pytest.approx(0.0)


def test_slowing_at_the_same_heart_rate_is_positive_drift():
    """The classic drift signature: same HR, slower pace."""
    r = _halves(140, 140, PACE, PACE * 1.1)     # 10% slower
    assert r["decoupling_pct"] > 0
    assert r["decoupling_pct"] == pytest.approx(100 * (1 - 1 / 1.1), abs=0.1)


def test_rising_heart_rate_at_the_same_pace_is_positive_drift():
    r = _halves(140, 154)                       # +10% HR
    assert r["decoupling_pct"] > 0
    assert r["decoupling_pct"] == pytest.approx(100 * (1 - 140 / 154), abs=0.1)


def test_negative_split_is_negative_decoupling():
    """Speeding up at the same HR means you got more efficient, not less."""
    assert _halves(140, 140, PACE, PACE * 0.9)["decoupling_pct"] < 0


def test_uphill_athlete_five_percent_threshold():
    """Their AeT test: under 5% means the effort was at or below AeT."""
    assert _halves(140, 140, PACE, PACE * 1.03)["decoupling_pct"] < 5     # pass
    assert _halves(140, 140, PACE, PACE * 1.10)["decoupling_pct"] > 5     # fail


def test_insufficient_or_invalid_data_returns_none():
    assert compute_hr_drift(np.array([140.0] * 30), np.array([PACE] * 30)) is None
    assert compute_hr_drift(np.array([140.0] * N), np.zeros(N)) is None
