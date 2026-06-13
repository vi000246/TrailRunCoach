import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from backend.engine.algorithms.classify import classify_trail


def test_road_run_low_elevation():
    # 10km, 30m gain → 3 m/km → road
    assert classify_trail(sport="running", distance_m=10000, elevation_gain_m=30) == "road"


def test_trail_run_high_climb_rate():
    # 10km, 600m gain → 60 m/km → trail
    assert classify_trail(sport="running", distance_m=10000, elevation_gain_m=600) == "trail"


def test_trail_boundary_at_threshold():
    # exactly 20 m/km → trail (>= threshold)
    assert classify_trail(sport="running", distance_m=10000, elevation_gain_m=200) == "trail"


def test_non_running_is_unknown():
    assert classify_trail(sport="cycling", distance_m=40000, elevation_gain_m=800) == "unknown"


def test_missing_data_unknown():
    assert classify_trail(sport="running", distance_m=None, elevation_gain_m=None) == "unknown"
    assert classify_trail(sport="running", distance_m=0, elevation_gain_m=100) == "unknown"
    assert classify_trail(sport="running", distance_m=10000, elevation_gain_m=None) == "unknown"
