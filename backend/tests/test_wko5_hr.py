"""hrTSS / hrIF (backend/engine/algorithms/wko5_hr.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms.wko5_hr import hr_tss


def _secs(n):
    return [float(i) for i in range(1, n + 1)]


def test_one_hour_at_lthr_is_100_tss():
    s, iff = hr_tss(_secs(3600), [160.0] * 3600, 160.0)
    assert s == pytest.approx(100.0)
    assert iff == pytest.approx((0.6 * 100 / 60 ** 1.025) ** 0.5)


def test_levels_are_checked_from_the_top():
    # 1.06 * 160 = 169.6 -> 140 TSS/h ; 0.855 * 160 = 136.8 -> 60 TSS/h
    assert hr_tss(_secs(3600), [170.0] * 3600, 160.0)[0] == pytest.approx(140.0)
    assert hr_tss(_secs(3600), [137.0] * 3600, 160.0)[0] == pytest.approx(60.0)
    assert hr_tss(_secs(3600), [1.0] * 3600, 160.0)[0] == pytest.approx(20.0)


def test_invalid_samples_skip_time_but_keep_the_clock():
    t = _secs(3600)
    hr = [160.0] * 3600
    hr[:1800] = [None] * 1800
    s, _ = hr_tss(t, hr, 160.0)
    assert s == pytest.approx(50.0)        # only the valid half counts


def test_no_lthr_or_data_is_not_computable():
    assert hr_tss(_secs(10), [150.0] * 10, None) == (None, None)
    assert hr_tss([], [], 160.0) == (None, None)
