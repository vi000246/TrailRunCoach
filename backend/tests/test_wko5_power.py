"""NP / tssduration / power TSS (backend/engine/algorithms/wko5_power.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms.wko5_power import normalized_power, power_tss, rapower


def test_constant_power_np_equals_power_and_counts_seconds():
    t = [float(i) for i in range(1, 601)]           # 10 min at 1 Hz
    np_, dur = normalized_power(t, [200.0] * 600)
    assert np_ == pytest.approx(200.0)
    assert dur == 600


def test_np_weights_hard_efforts_above_average():
    t = [float(i) for i in range(1, 1201)]
    p = [100.0] * 600 + [300.0] * 600                # avg 200
    np_, _ = normalized_power(t, p)
    assert np_ > 200.0


def test_rapower_divides_by_valid_coverage_when_at_least_27s():
    t = [float(i) for i in range(1, 61)]
    v = [100.0] * 60
    v[40:43] = [None, None, None]                     # 3 s gap -> 27 s valid in window
    ra = dict(rapower(t, v))
    assert ra[45.0] == pytest.approx(100.0)          # sum / validcov, not / covered


def test_rapower_skips_seconds_without_valid_samples():
    t = [1.0, 2.0, 3.0]
    assert rapower(t, [None, None, None]) == []
    assert normalized_power(t, [None, None, None]) == (None, 0.0)


def test_power_tss_one_hour_at_ftp_is_100():
    assert power_tss(250.0, 3600.0, 250.0) == pytest.approx(100.0)
    assert power_tss(None, 3600.0, 250.0) is None
    assert power_tss(250.0, 0.0, 250.0) is None
