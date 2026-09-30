"""WKO5 _elevation smoothing and climbing (backend/engine/algorithms/wko5_elevation.py)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.engine.algorithms.wko5_elevation import (
    climbing, descending, elevation_change, smooth_elevation,
)

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\WKO5\Athlete"))


def _t(n):
    return [float(i) for i in range(1, n + 1)]


def test_smoothing_keeps_the_first_and_last_valid_values():
    e = [100.0, 101.0, 99.0, 103.0, 102.0, 105.0]
    out = smooth_elevation(_t(6), e)
    assert out[0] == pytest.approx(e[0])
    assert out[-1] == pytest.approx(e[-1])


def test_smoothing_removes_gps_noise_so_climbing_drops():
    e = [100.0 + (1.5 if i % 2 else -1.5) for i in range(60)]   # pure jitter
    assert climbing(e) > 80
    assert climbing(smooth_elevation(_t(60), e)) < 5


def test_climbing_and_descending_ignore_gaps():
    e = [100.0, None, 110.0, 105.0, None, 115.0]
    assert climbing(e) == pytest.approx(20.0)
    assert descending(e) == pytest.approx(5.0)
    assert elevation_change(e) == pytest.approx(15.0)


def test_short_or_empty_series_pass_through():
    assert smooth_elevation([1.0, 2.0], [10.0, 20.0]) == [10.0, 20.0]
    assert smooth_elevation(_t(5), [None] * 5) == [None] * 5


@pytest.mark.golden
@pytest.mark.skipif(not ATHLETE_DIR.exists(), reason="no WKO5 athlete folder")
def test_elevation_smoothing_matches_wko5_channel():
    """Golden: our _elevation == WKO5's stored _elevation channel."""
    from backend.files.wko4_file import read_wko4
    files = sorted(ATHLETE_DIR.rglob("*.wko4"))
    checked = 0
    for p in files[::9]:
        w = read_wko4(p)
        e, t, we = (w.channels.get("elevation"), w.channels.get("elapsedtime"),
                    w.channels.get("_elevation"))
        if not e or not t or not we:
            continue
        ours = smooth_elevation(t.values, e.values)
        for a, b in zip(ours, we.values):
            if a is None or b is None:
                assert a is None and b is None, p.name
            else:
                assert a == pytest.approx(b, abs=1e-9), p.name
        checked += 1
        if checked >= 25:
            break
    assert checked >= 10
