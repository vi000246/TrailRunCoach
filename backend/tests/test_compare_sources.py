"""backend/scripts/compare_sources.py — pairing and diff logic."""
import datetime as dt

import pytest

from backend.scripts.compare_sources import Act, diff, normalized_power, pair

T0 = dt.datetime(2026, 9, 1, 22, 30)


def act(src, key, secs, **vals):
    return Act(src, key, T0 + dt.timedelta(seconds=secs), values=vals)


def test_pair_nearest_within_window():
    a = [act("coros", "c1", 0), act("coros", "c2", 3600)]
    b = [act("tp", "t1", 30), act("tp", "t2", 3600 + 200), act("tp", "t3", 5)]
    pairs, only_a, only_b = pair(a, b, 120)
    assert [(x.key, y.key) for x, y, _ in pairs] == [("c1", "t3")]
    assert [x.key for x in only_a] == ["c2"]
    assert sorted(y.key for y in only_b) == ["t1", "t2"]


def test_diff_flags_beyond_tolerance():
    d = diff(act("a", "a", 0, distance_km=10.0, gain_m=500.0, avg_hr=None),
             act("b", "b", 0, distance_km=10.1, gain_m=600.0, avg_hr=150.0))
    assert d["distance_km"]["flag"] is False           # 1% < 2%
    assert d["gain_m"]["flag"] is True                 # 16.7% > 10%
    assert d["gain_m"]["delta"] == pytest.approx(100)
    assert d["avg_hr"]["delta"] is None


def test_normalized_power_constant():
    assert normalized_power([250.0] * 600) == pytest.approx(250.0)
    assert normalized_power([0.0] * 600) is None
