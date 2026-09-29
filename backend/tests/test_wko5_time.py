"""WKO5 time & distance metrics (backend/engine/algorithms/wko5_time.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.engine.algorithms.wko5_time import (
    MOVING_SPEED_KMH, distance_range, duration, moving_duration, pedaling_duration,
)

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete"))


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


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_time_and_distance_match_wko5_stored_values():
    """Golden: movingduration (4213), pedalingduration (4214), distance (4217)."""
    from backend.files.wko5_athlete import read_athlete
    from backend.files.wko4_file import read_wko4
    a = read_athlete(next(ATHLETE_DIR.glob("*.wko5athlete")))
    checked = 0
    for w in a.workouts[::7]:
        m = w.metrics
        p = ATHLETE_DIR / w.file
        if not p.exists():
            continue
        f = read_wko4(p)
        t = f.channels.get("elapsedtime")
        if not t:
            continue
        sport = (w.sport_group or "").lower()
        sport = {"road bike": "bike"}.get(sport, sport)
        vals = lambda name: (f.channels[name].values if name in f.channels else None)
        if 4213 in m and (vals("speed") or vals("elapseddistance")):
            got = moving_duration(t.values, vals("speed"), sport, distance=vals("elapseddistance"))
            assert got == pytest.approx(m[4213], abs=1e-6), (w.file, "moving")
        if 4214 in m and vals("cadence"):
            assert pedaling_duration(t.values, vals("cadence")) == pytest.approx(m[4214], abs=1e-6), \
                (w.file, "pedaling")
        if 4217 in m and vals("elapseddistance"):
            dc = f.channels["elapseddistance"]
            begin, d = distance_range(dc.values, dc.base)
            assert d == pytest.approx(m[4217], rel=1e-6), (w.file, "distance")
            if 4216 in m:
                assert begin == pytest.approx(m[4216], abs=1e-6), (w.file, "begindistance")
        checked += 1
    assert checked >= 50
