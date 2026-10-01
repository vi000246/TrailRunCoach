"""Bad activity files (backend/engine/bad_activity.py): detection rules,
overrides, the Dataset exclusion and the API. Synthetic data only."""
from __future__ import annotations

import numpy as np
import pytest

from backend.engine import bad_activity as B


def _track(speeds_kmh, dt_s: float = 1.0):
    """(t, distance km) for consecutive constant-speed seconds."""
    v = np.asarray(speeds_kmh, float)
    t = np.arange(1, len(v) + 1) * dt_s
    d = np.cumsum(v * dt_s / 3600.0)
    return list(t), list(d)


# ---- limits ---------------------------------------------------------------

def test_world_record_speeds_at_the_record_durations():
    assert B.wr_speed_kmh(43.03) == pytest.approx(400 / 43.03 * 3.6)
    assert B.wr_speed_kmh(7235.0) == pytest.approx(42195 / 7235 * 3.6)        # 21.0 km/h
    assert B.wr_speed_kmh(10.0) == B.wr_speed_kmh(43.03)                       # clamped
    assert B.wr_speed_kmh(30000.0) == B.wr_speed_kmh(7235.0)
    # monotone: longer = slower
    xs = [60, 120, 300, 600, 1200, 3600]
    ys = [B.wr_speed_kmh(x) for x in xs]
    assert ys == sorted(ys, reverse=True)


# ---- the rules ------------------------------------------------------------

def test_car_file_is_flagged_by_average_speed():
    # the TP 2025-12-14 shape: 17 min at ~43 km/h
    t, d = _track([43.0] * 1037)
    f = B.features(t, d)
    v = B.judge(f, "run")
    assert v and v["rule"] == "avg_speed"
    assert v["reason"] == "疑似交通工具／騎車（均速 43 km/h）"
    assert f["distance_km"] == pytest.approx(12.39, abs=0.05)


def test_real_runs_are_not_flagged():
    # an easy run, a 5 K race at 20 km/h, a 3 h trail run
    for speeds in ([10.0] * 3600, [20.0] * 900, [6.0] * 10800):
        t, d = _track(speeds)
        assert B.judge(B.features(t, d), "run") is None


def test_fast_downhill_and_sprints_are_not_flagged():
    # 2 min at 28 km/h on a descent inside a 1 h run; a 30 s sprint at 36 km/h
    speeds = [10.0] * 1500 + [28.0] * 120 + [10.0] * 1500 + [36.0] * 30 + [10.0] * 450
    t, d = _track(speeds)
    assert B.judge(B.features(t, d), "run") is None


def test_gps_spike_is_ignored():
    t, d = _track([10.0] * 1800)
    d = list(d)
    for i in range(900, len(d)):          # a 2 km jump in one second, then normal
        d[i] += 2.0
    f = B.features(t, d)
    assert B.judge(f, "run") is None
    assert f["distance_km"] == pytest.approx(5.0, abs=0.05)                    # the jump is no distance


def test_vehicle_segment_at_the_end_is_flagged_by_a_sustained_window():
    # a 2 h trail run at 6 km/h, then 10 min in a car at 45 km/h: the average
    # (≈ 9 km/h) is fine, the 5-min window is not
    t, d = _track([6.0] * 7200 + [45.0] * 600)
    f = B.features(t, d)
    assert f["avg_kmh"] < B.limit_kmh(f["moving_s"])
    v = B.judge(f, "run")
    assert v and v["rule"] == "window_speed"
    assert "疑似交通工具／騎車" in v["reason"] and "第 1" in v["reason"]
    assert v["window_start_s"] >= 7200 - 60


def test_impossible_power_is_flagged():
    t, d = _track([10.0] * 1200)
    f = B.features(t, d, power=[900.0] * 1200)
    v = B.judge(f, "run", weight_kg=65.0)
    assert v and v["rule"] == "power" and "W/kg" in v["reason"]
    assert B.judge(B.features(t, d, power=[250.0] * 1200), "run", weight_kg=65.0) is None


def test_only_foot_sports_are_judged():
    t, d = _track([43.0] * 1800)
    f = B.features(t, d)
    assert B.judge(f, "bike") is None
    assert B.judge(f, "walk") is not None          # a hike can include running: same limits


def test_paused_recording_does_not_make_speed():
    # 10 km/h, a 30-min pause (no samples), 10 km/h again
    t1, d1 = _track([10.0] * 1200)
    t2 = [x + 1200 + 1800 for x in t1]
    d2 = [x + d1[-1] for x in d1]
    f = B.features(t1 + t2, d1 + d2)
    assert B.judge(f, "run") is None
    assert f["moving_s"] == pytest.approx(2400, abs=2)


# ---- overrides ------------------------------------------------------------

def test_decide_overrides():
    auto = {"reason": "疑似交通工具／騎車（均速 43 km/h）"}
    assert B.decide(auto, None)["label"] == "已排除：疑似交通工具／騎車（均速 43 km/h）"
    assert B.decide(auto, B.KEEP) is None                       # 這筆是正常的，不要排除
    assert B.decide(None, B.EXCLUDE)["label"] == "已排除：手動排除"
    assert B.decide(auto, None, enabled=False) is None          # setting off: no auto exclusion
    assert B.decide(None, B.EXCLUDE, enabled=False)["manual"]   # manual still applies
    assert B.decide(None, None) is None


def test_overrides_stamp_changes_with_the_rows():
    a = B.overrides_stamp([{"start_local": "2025-12-14T10:00", "file": "x.fit", "exclusion": "keep"}])
    b = B.overrides_stamp([{"start_local": "2025-12-14T10:00", "file": "x.fit", "exclusion": "exclude"}])
    assert a and b and a != b
    assert B.overrides_stamp([{"start_local": "2025-12-14T10:00", "exclusion": None}]) == ""
