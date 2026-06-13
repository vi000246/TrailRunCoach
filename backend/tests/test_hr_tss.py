import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import numpy as np
import pytest
from backend.engine.algorithms.metrics import compute_hr_tss


def test_hr_tss_at_threshold_one_hour():
    # 1hr exactly at LTHR → ~100 TSS
    hr = np.array([160.0] * 3600)
    lthr = 160
    assert compute_hr_tss(hr, lthr, duration_s=3600) == pytest.approx(100.0, abs=5.0)


def test_hr_tss_below_threshold_is_lower():
    hr_hard = np.array([160.0] * 3600)
    hr_easy = np.array([120.0] * 3600)
    lthr = 160
    assert compute_hr_tss(hr_easy, lthr, 3600) < compute_hr_tss(hr_hard, lthr, 3600)


def test_hr_tss_zero_guard():
    assert compute_hr_tss(None, 160, 3600) == 0.0
    assert compute_hr_tss(np.array([150.0]), 0, 3600) == 0.0
    assert compute_hr_tss(np.array([]), 160, 3600) == 0.0
    assert compute_hr_tss(np.array([150.0]), 160, 0) == 0.0
