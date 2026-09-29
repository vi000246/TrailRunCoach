"""Climb detection and repeated-route clustering."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms.climbs import detect_climbs
from backend.engine.algorithms.routes import cells, cluster_routes, jaccard


def _profile(segments, step_m=10.0, step_s=5.0):
    """segments: [(horizontal_m, grade)] -> (t, dist_km, elev) sampled every step_m."""
    t, d, e = [0.0], [0.0], [100.0]
    for horiz, grade in segments:
        for _ in range(int(horiz / step_m)):
            t.append(t[-1] + step_s)
            d.append(d[-1] + step_m / 1000.0)
            e.append(e[-1] + step_m * grade)
    return t, d, e


def test_single_steady_climb_is_found_and_measured():
    t, d, e = _profile([(500, 0.0), (2000, 0.10), (500, 0.0)])
    climbs = detect_climbs(t, d, e)
    assert len(climbs) == 1
    c = climbs[0]
    assert c.gain_m == pytest.approx(200.0, abs=1)
    assert c.grade == pytest.approx(0.10, abs=0.005)
    assert c.distance_km == pytest.approx(2.0, abs=0.02)
    # 200 steps of 5 s = 1000 s for 200 m -> 720 m/h
    assert c.vam_m_per_h == pytest.approx(720.0, rel=0.02)


def test_small_dip_inside_a_climb_is_absorbed():
    t, d, e = _profile([(1000, 0.10), (50, -0.10), (1000, 0.10)])   # 5 m dip mid-climb
    climbs = detect_climbs(t, d, e)
    assert len(climbs) == 1
    assert climbs[0].gain_m == pytest.approx(195.0, abs=2)


def test_a_real_descent_splits_two_climbs():
    t, d, e = _profile([(1000, 0.10), (1000, -0.10), (1000, 0.10)])  # 100 m down between
    assert len(detect_climbs(t, d, e)) == 2


def test_climb_starts_at_the_low_point_after_a_descent():
    t, d, e = _profile([(500, -0.05), (1500, 0.10)])
    c = detect_climbs(t, d, e)[0]
    assert c.start_elev_m == pytest.approx(75.0, abs=1)                  # 100 - 25
    assert c.gain_m == pytest.approx(150.0, abs=1)


def test_short_or_gentle_rises_are_ignored():
    t, d, e = _profile([(500, 0.10)])                 # only 50 m of gain
    assert detect_climbs(t, d, e) == []
    t, d, e = _profile([(5000, 0.02)])                # 100 m but 2% grade
    assert detect_climbs(t, d, e) == []


def test_heart_rate_cost_per_100m():
    t, d, e = _profile([(2000, 0.10)])
    hr = [150.0] * len(t)
    c = detect_climbs(t, d, e, hr)[0]
    assert c.avg_hr == pytest.approx(150.0)
    # 1000 s at 150 bpm = 2500 beats for 200 m -> 1250 per 100 m
    assert c.hr_per_100m == pytest.approx(1250.0, rel=0.02)


def test_gaps_in_the_streams_are_bridged():
    t, d, e = _profile([(2000, 0.10)])
    e[50] = None
    d[51] = None
    assert len(detect_climbs(t, d, e)) == 1


# --- routes ----------------------------------------------------------------

def _track(lat0, lon0, n=200, dlat=0.00005, dlon=0.0):
    return [lat0 + i * dlat for i in range(n)], [lon0 + i * dlon for i in range(n)]


def test_same_track_is_the_same_route():
    lat, lon = _track(25.18, 121.55)
    assert jaccard(cells(lat, lon), cells(lat, lon)) == 1.0


def test_small_gps_noise_still_matches():
    lat, lon = _track(25.18, 121.55)
    noisy = [a + 0.00002 for a in lat]
    assert jaccard(cells(lat, lon), cells(noisy, lon)) > 0.6


def test_different_places_do_not_match():
    a = cells(*_track(25.18, 121.55))
    b = cells(*_track(24.83, 121.53))
    assert jaccard(a, b) == 0.0


def test_clustering_groups_repeats_and_keeps_order():
    a1 = cells(*_track(25.18, 121.55))
    a2 = cells([x + 0.00002 for x in _track(25.18, 121.55)[0]], _track(25.18, 121.55)[1])
    b = cells(*_track(24.83, 121.53))
    routes = cluster_routes([("mon", a1), ("tue", b), ("wed", a2)])
    assert len(routes) == 2
    assert routes[0].members == ["mon", "wed"]
    assert routes[1].members == ["tue"]


def test_empty_tracks_are_skipped():
    assert cluster_routes([("x", frozenset())]) == []
