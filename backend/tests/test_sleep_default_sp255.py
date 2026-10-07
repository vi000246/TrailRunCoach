"""
SP-255: a new sleep point's minutes by the race's predicted hours (docs/research/night-and-sleep.md
§2.4, §4.2 單 2; owner OK 2026-10-06): < 36 h 20, 36–60 h 30 (20–30, about 1–1.5 h in all), > 60 h 90;
the blank row keeps 90 (saved pages unchanged); a sleep point arriving outside 00:00–05:00 gets the
「多數人在凌晨小睡」 line (Kishi 2024).
"""
from __future__ import annotations

from backend.engine.racepower import fuel as FU


def _plan(hours, km=100.0):
    """A plan of one even segment per 10 km over `hours` of moving time."""
    t_seg = hours * 3600.0 / 10
    segs = [{"i": i, "start_km": km * i / 10, "end_km": km * (i + 1) / 10, "t": t_seg, "cum_s": t_seg * (i + 1)}
            for i in range(10)]
    return {"summary": {"time_s": hours * 3600.0, "km": km}, "segments": segs}


def test_default_minutes_by_the_race_hours():
    assert [FU.sleep_default_min(h) for h in (10, 35.9, 36, 59.9, 60, 100)] == [20, 20, 30, 30, 90, 90]
    assert FU.sleep_default_min(None) is None and FU.sleep_default_min(0) is None
    # the blank row is still 90: pages saved before SP-255 keep their sleep time
    assert FU.STOP_TYPES["sleep"]["minutes"] == 90.0


def test_the_hours_are_moving_plus_the_other_stops_not_the_sleep_itself():
    stops = [{"km": 20.0, "minutes": 60.0, "type": "aid"}, {"km": 50.0, "minutes": 600.0, "type": "sleep"}]
    s = FU.sleep_info(_plan(35.5), stops, None)
    assert s["hours"] == 36.5 and s["default_min"] == 30       # 35.5 h moving + 1 h at the aid station
    assert FU.sleep_info(_plan(35.5), [], None)["default_min"] == 20
    assert FU.sleep_info({"summary": {}}, stops, None) is None


def test_the_36_60_group_says_about_1_to_1_5_h_in_all_only_with_sleep_points():
    sleep = [{"km": 50.0, "minutes": 30.0, "type": "sleep"}]
    assert "1–1.5" in FU.sleep_info(_plan(40), sleep, None)["total_hint"]
    assert FU.sleep_info(_plan(40), [], None)["total_hint"] is None
    assert FU.sleep_info(_plan(20), sleep, None)["total_hint"] is None
    assert FU.sleep_info(_plan(70), sleep, None)["total_hint"] is None


def test_a_sleep_point_outside_the_early_hours_gets_the_line():
    # 40 h over 100 km from 06:00: km 50 at 20 h → 02:00 (+1) — inside 00:00–05:00, no line
    night = [{"km": 50.0, "minutes": 30.0, "type": "sleep"}]
    s = FU.sleep_info(_plan(40), night, "06:00")
    assert s["off_night"] == [] and s["note"] is None
    # km 30 at 12 h → 18:00, after a 60-min aid stop at km 20 → 19:00
    day = [{"km": 20.0, "minutes": 60.0, "type": "aid"}, {"km": 30.0, "minutes": 30.0, "type": "sleep"}] + night
    s = FU.sleep_info(_plan(40), day, "06:00")
    assert s["off_night"] == [{"km": 30.0, "eta": "19:00"}]
    assert "第 30 km 預估 19:00 到" in s["note"] and "Kishi 2024" in s["note"]
    # no start time: no ETA, no line
    assert FU.sleep_info(_plan(40), day, None)["note"] is None
