"""
SP-566: a timed recording adds no distance while it is stopped (racepower/course.track_distance):
GPS drift at camp or at a rest no longer makes a walked multi-day record long. A stop = ≥ 10 min
within 50 m and 20 m of elevation, its points adding up to ≤ 0.3 m/s (推估). Tracks that never stop,
drawn routes (no times) and the athlete's own activities (device distance) keep their distance.
Synthetic tracks only.
"""
import math
import random

import numpy as np
import pytest

from backend.engine.racepower import course as CO
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import overnight as OV

M = math.pi / 180 * CO.EARTH_R          # m per degree of latitude
LAT0, LON0 = 24.3, 121.3


class Rec:
    """A northward recording: walk(km) at 3 km/h, a point every step_s; stay(hours, wobble_m,
    every_s) wanders around one place (GPS drift); climb / pace do real moves inside a small place."""

    def __init__(self, step_s=1.0, seed=1):
        self.rnd = random.Random(seed)
        self.step_s = step_s
        self.lat, self.lon, self.ele, self.t = [], [], [], []
        self.x = self.y = 0.0
        self.z = 2000.0
        self.now = 0.0
        self.put()

    def put(self, dx=0.0, dy=0.0):
        self.lat.append(LAT0 + (self.y + dy) / M)
        self.lon.append(LON0 + (self.x + dx) / M / math.cos(math.radians(LAT0)))
        self.ele.append(self.z)
        self.t.append(self.now)

    def walk(self, km, kmh=3.0):
        v = kmh / 3.6
        for _ in range(int(km * 1000 / (v * self.step_s))):
            self.y += v * self.step_s
            self.z += 0.05 * v * self.step_s
            self.now += self.step_s
            self.put()
        return self

    def stay(self, hours, wobble_m=4.0, every_s=None):
        every = every_s or self.step_s
        ox = oy = 0.0
        for _ in range(int(hours * 3600 / every)):
            self.now += every
            # a slow random walk around the spot (GPS drift), pulled back to it
            ox = 0.8 * ox + self.rnd.uniform(-wobble_m, wobble_m)
            oy = 0.8 * oy + self.rnd.uniform(-wobble_m, wobble_m)
            self.put(ox, oy)
        return self

    def track(self):
        return GPX.Track(list(self.lat), list(self.lon), list(self.ele), list(self.t), [], "rec")


def plain(track):
    """The distance before SP-566: every kept step counted."""
    old = CO.STILL_S
    CO.STILL_S = 1e12
    try:
        return CO.track_distance(track)
    finally:
        CO.STILL_S = old


def test_a_night_at_camp_adds_no_drift():
    # SP-534's case: a point every 5 min overnight, ± 4 m of wobble — it used to add ~0.7 km a night
    r = Rec(step_s=30.0).walk(10).stay(13, wobble_m=4.0, every_s=300).walk(9)
    tr = r.track()
    before, after = plain(tr)["d"][-1], CO.track_distance(tr)["d"][-1]
    assert before - 19000 > 300                         # the drift the old sum kept
    assert after == pytest.approx(19000, abs=60)        # a stop adds at most its straight line


def test_a_rest_recorded_every_second_adds_no_drift():
    # a 30 min rest at 1 s recording, drifting ~0.16 m/s (the owner's rests: 0.1–0.25 m/s)
    tr = Rec(step_s=1.0).walk(3).stay(0.5, wobble_m=0.4).walk(3).track()
    before, after = plain(tr)["d"][-1], CO.track_distance(tr)["d"][-1]
    assert before - 6000 > 200
    assert after == pytest.approx(6000, abs=60)


def test_short_stops_and_moving_tracks_keep_their_distance():
    # stops of 3 and 7 min (with the 1 min walk out of 50 m, still < 10 min): the same sum, point for point
    tr = Rec(step_s=1.0).walk(2).stay(0.05, wobble_m=0.4).walk(2).stay(0.12, wobble_m=0.4).walk(1).track()
    assert np.array_equal(CO.track_distance(tr)["d"], plain(tr)["d"])
    tr = Rec(step_s=5.0).walk(15, kmh=2.0).track()
    assert np.array_equal(CO.track_distance(tr)["d"], plain(tr)["d"])


def test_a_drawn_route_without_times_is_untouched():
    r = Rec(step_s=30.0).walk(5).stay(2, wobble_m=4.0, every_s=60).walk(5)
    tr = r.track()
    tr.time = None
    td = CO.track_distance(tr)
    # no times, no stops: the wobble stays in (the old behaviour), nothing is dropped
    assert td["t"] is None and td["d"][-1] > 10000 + 300


def test_the_athletes_own_activity_keeps_the_device_distance():
    # single-day activities from the watch use its own distance (backtest.track_of): unchanged
    r = Rec(step_s=1.0).walk(2).stay(0.5, wobble_m=0.4).walk(2)
    tr = r.track()
    tr.dist = [i * 0.9 for i in range(len(tr.lat))]
    td = CO.track_distance(tr)
    assert td["d"][-1] == pytest.approx(0.9 * (len(tr.lat) - 1))


def test_a_switchback_climb_inside_50_m_is_still_walked():
    # 15 min zig-zagging up a steep face: never 50 m from the start, but 60 m higher — real walking
    r = Rec(step_s=1.0).walk(1)
    for s in range(900):
        r.now += 1
        r.z += 60 / 900
        r.x = 20 * math.sin(s / 40)                    # back and forth across the slope
        r.put()
    r.walk(1)
    tr = r.track()
    assert CO.track_distance(tr)["d"][-1] == pytest.approx(plain(tr)["d"][-1])


def test_pacing_a_small_place_is_not_drift():
    # 15 min of laps round a 30 m circle at 1 m/s: within 50 m and level, but far above the rest speed
    r = Rec(step_s=1.0).walk(1)
    cx, cy = r.x, r.y
    for s in range(900):
        r.now += 1
        a = s / 15.0                                   # 1 m/s on a 15 m radius
        r.x, r.y = cx + 15 * math.sin(a), cy + 15 * (1 - math.cos(a))
        r.put()
    r.x, r.y = cx, cy
    r.walk(1)
    tr = r.track()
    assert CO.track_distance(tr)["d"][-1] == pytest.approx(plain(tr)["d"][-1])


def test_the_nights_land_where_the_walking_says():
    # SP-534 on top: the 2nd night's km is what was walked, not walked + the 1st night's drift
    r = Rec(step_s=30.0).walk(10).stay(13, wobble_m=4.0, every_s=300).walk(9).stay(12, every_s=300).walk(8)
    got = OV.find(r.track())
    assert got["days"] == 3
    assert got["splits_km"] == [pytest.approx(10.0, abs=0.15), pytest.approx(19.0, abs=0.15)]


def test_the_course_profile_still_works_with_a_flat_stretch():
    # the distance stays flat through a stop: the course is built on it as before
    tr = Rec(step_s=30.0).walk(6).stay(10, wobble_m=4.0, every_s=300).walk(6).track()
    c = CO.build_course(tr)
    assert c["totals"]["km"] == pytest.approx(12.0, abs=0.08)
    assert np.all(np.diff(c["profile"]["km"]) > 0)
