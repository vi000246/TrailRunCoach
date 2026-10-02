"""WKO5 time & distance metrics (backend/engine/algorithms/wko5_time.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms.wko5_time import (
    MOVING_SPEED_KMH, distance_range, duration, moving_duration, pedaling_duration,
)


def _t(n):
    return [float(i) for i in range(1, n + 1)]


def test_moving_duration_uses_the_sports_speed_threshold():
    t = _t(10)
    speed = [0.0] * 5 + [6.0] * 5
    assert moving_duration(t, speed, "run") == pytest.approx(5.0)
    # 2 km/h counts as moving for a run (1 mph) but not for a bike (2 mph)
    slow = [2.0] * 10
    assert moving_duration(t, slow, "run") == pytest.approx(10.0)
    assert moving_duration(t, slow, "bike") == pytest.approx(0.0)
    assert MOVING_SPEED_KMH["walk"] == MOVING_SPEED_KMH["run"]


def test_moving_duration_falls_back_to_distance_when_no_speed():
    t = _t(6)
    dist = [0.0, 0.0, 0.1, 0.2, 0.2, 0.3]
    assert moving_duration(t, None, "run", distance=dist) == pytest.approx(3.0)


def test_pedaling_duration_counts_non_zero_cadence():
    assert pedaling_duration(_t(10), [0.0] * 4 + [80.0] * 6) == pytest.approx(6.0)
    assert pedaling_duration(_t(3), [None, None, None]) == 0.0


def test_duration_counts_time_with_any_data_channel():
    t = _t(6)
    ch = {"elapsedtime": t, "heartrate": [None, None, 120.0, 120.0, None, None],
          "power": [None, 100.0, None, None, None, None]}
    assert duration(t, ch) == pytest.approx(3.0)   # samples 2, 3, 4
    assert duration(t, {"elapseddistance": t}) == 0.0   # axis channels don't count


def test_distance_is_measured_from_the_channel_base():
    begin, dist = distance_range([5.0, 6.0, 8.5], base=4.9)
    assert begin == pytest.approx(4.9) and dist == pytest.approx(3.6)
    begin, dist = distance_range([5.0, 6.0, 8.5])          # no base -> first sample
    assert begin == pytest.approx(5.0) and dist == pytest.approx(3.5)
    assert distance_range([None, None]) == (None, None)
