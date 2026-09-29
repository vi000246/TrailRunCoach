"""Parity: decoded .wko4 channels must reproduce WKO5's own stored min/max.

Runs against a real WKO5 athlete folder (set WKO5_ATHLETE_DIR); skipped otherwise.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from pathlib import Path

import pytest

from backend.files.wko4_file import read_wko4, workout_average, window_average

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete"))
VERIFIED = ("heartrate", "speed", "cadence", "elevation", "latitude",
            "longitude", "temperature", "power")

files = sorted(ATHLETE_DIR.rglob("*.wko4")) if ATHLETE_DIR.exists() else []
pytestmark = pytest.mark.golden


@pytest.mark.skipif(not files, reason="no WKO5 athlete folder available")
def test_channel_min_max_match_wko5_stored_stats():
    checked = 0
    for p in files[:300]:
        w = read_wko4(p)
        if not w.ranges:
            continue
        whole = w.ranges[0]
        for ch in VERIFIED:
            st, c = whole.stats.get(ch), w.channels.get(ch)
            cached = whole.cache.get(f"calculateMinMaxAvgMetrics|all|{ch}")
            if not st or not c or not cached or cached.rsplit("|", 1)[-1] != str(c.raw_hash):
                continue  # stats absent or stale (computed from older samples)
            vals = [v for v in c.values if v is not None]
            assert min(vals) == pytest.approx(st["min"]), (p.name, ch)
            assert max(vals) == pytest.approx(st["max"]), (p.name, ch)
            if st["avg"] is not None:
                assert workout_average(w, ch, whole.start_s or 0.0) == \
                    pytest.approx(st["avg"], rel=1e-9), (p.name, ch)
            checked += 1
    assert checked > 50


@pytest.mark.skipif(not files, reason="no WKO5 athlete folder available")
def test_peak_range_average_matches_wko5():
    checked = 0
    for p in files[:300]:
        w = read_wko4(p)
        for r in w.ranges[1:]:
            for ch, st in r.stats.items():
                if ch not in VERIFIED or st["avg"] is None or r.start_s is None:
                    continue
                c = w.channels.get(ch)
                key = next((k for k in r.cache if k.startswith("calculateMinMaxAvgMetrics|")
                            and k.endswith(f"|{ch}")), None)
                if not c or not key or r.cache[key].rsplit("|", 1)[-1] != str(c.raw_hash):
                    continue
                assert window_average(w, ch, r.start_s, r.end_s) == \
                    pytest.approx(st["avg"], rel=1e-9), (p.name, r.name, ch)
                checked += 1
    assert checked > 50
