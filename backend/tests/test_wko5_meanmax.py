"""WKO5 mean-max (backend/engine/algorithms/wko5_meanmax.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms.wko5_meanmax import duration_grid, meanmax_time


def _t(n):
    return [float(i) for i in range(1, n + 1)]


def test_duration_grid_is_geometric_plus_fixed_points():
    g = duration_grid(3600)
    assert g[:4] == [1, 2, 3, 4]              # round(1.05^i)
    for fixed in (5, 60, 300, 1200, 3600):
        assert fixed in g
    assert max(g) <= 3600
    assert duration_grid(30)[-1] <= 30


def test_best_window_finds_the_hard_segment():
    v = [100.0] * 100 + [400.0] * 60 + [100.0] * 100
    _, best = meanmax_time(_t(260), v, durations=[60, 120])
    assert best[0] == pytest.approx(400.0)
    assert best[1] == pytest.approx((400 * 60 + 100 * 60) / 120)


def test_window_is_continuous_and_prorated_on_irregular_samples():
    # one 10 s sample at 300 W, then 1 s samples at 100 W
    t = [10.0] + [10.0 + i for i in range(1, 11)]
    v = [300.0] + [100.0] * 10
    _, best = meanmax_time(t, v, durations=[10])
    assert best[0] == pytest.approx(300.0)     # the whole 10 s sample
    _, best15 = meanmax_time(t, v, durations=[15])
    assert best15[0] == pytest.approx((300 * 10 + 100 * 5) / 15)


def test_mostly_valid_window_divides_by_valid_time_only():
    v = [200.0] * 100
    v[50] = None                                # 59/60 valid = 98.3% > 98%
    _, best = meanmax_time(_t(100), v, durations=[60])
    assert best[0] == pytest.approx(200.0)


def test_gappy_window_divides_by_total_time():
    v = [200.0] * 30 + [None] * 30 + [200.0] * 30   # only 50% valid in any 60 s window
    _, best = meanmax_time(_t(90), v, durations=[60])
    assert best[0] == pytest.approx(200.0 * 30 / 60)


def test_duration_longer_than_the_workout_gives_nothing():
    _, best = meanmax_time(_t(10), [200.0] * 10, durations=[600])
    assert best[0] is None
