"""SP-249: forecast rain → a reminder on the trail and 百岳 calculators, never a
time change (docs/research/wet-muddy-terrain.md §5 #1: any hour's PoP ≥ 50 %
or ≥ 5 mm over the race window). Fakes only, no network."""
import datetime as dt

import pytest

from backend.engine.racepower import weather as WX
from backend.tests.test_race_calculator import _upload
from backend.tests.test_racepower_v2 import client  # noqa: F401  (fixture)

DAY = "2026-10-07"
MSG = "預報有雨：下坡和技術路段可能比預估慢，跌倒風險較高；建議用杖、注意鞋底抓地"


def _om(pop, mm):
    times = [f"{DAY}T{h:02d}:00" for h in range(24)]
    return {"elevation": 300, "hourly": {"time": times, "temperature_2m": [20.0] * 24,
                                         "relative_humidity_2m": [80.0] * 24,
                                         "precipitation": mm, "precipitation_probability": pop}}


def _row(a, b, pop=None, mm=None):
    return {"start": f"{DAY}T{a}", "end": f"{DAY}T{b}", "pop_pct": pop, "mm": mm}


WIN = (dt.datetime(2026, 10, 7, 6), dt.datetime(2026, 10, 7, 12))


# ---- thresholds ------------------------------------------------------------------

def test_any_hour_at_50_percent_alerts():
    r = WX.rain_alert([_row("07:00", "08:00", pop=50, mm=0.2), _row("08:00", "09:00", pop=20, mm=0)], WIN)
    assert r["alert"] and r["max_pop_pct"] == 50 and r["message"] == MSG


def test_5_mm_summed_alerts_even_at_low_probability():
    rows = [_row(f"{h:02d}:00", f"{h + 1:02d}:00", pop=30, mm=1.0) for h in range(6, 11)]
    r = WX.rain_alert(rows, WIN)
    assert r["alert"] and r["total_mm"] == pytest.approx(5.0)


def test_below_both_thresholds_no_alert():
    rows = [_row(f"{h:02d}:00", f"{h + 1:02d}:00", pop=40, mm=0.9) for h in range(6, 11)]
    r = WX.rain_alert(rows, WIN)
    assert r["alert"] is False and r["message"] is None


def test_rain_outside_the_race_window_is_ignored():
    rows = [_row("14:00", "15:00", pop=90, mm=12), _row("08:00", "09:00", pop=10, mm=0)]
    assert WX.rain_alert(rows, WIN)["alert"] is False


def test_no_rain_data_shows_nothing():
    assert WX.rain_alert(None, WIN) is None
    assert WX.rain_alert([], WIN) is None
    assert WX.rain_alert([_row("14:00", "15:00", pop=90)], WIN) is None      # none in the window
    assert WX.rain_alert([_row("07:00", "08:00", pop=90)], None) is None


def test_race_window():
    assert WX.race_window(DAY, "06:30", 1, 3600 * 5) == (dt.datetime(2026, 10, 7, 6, 30), dt.datetime(2026, 10, 7, 11, 30))
    # no start time or a multi-day trip → the whole event days
    assert WX.race_window(DAY, None, 1, 3600) == (dt.datetime(2026, 10, 7), dt.datetime(2026, 10, 8))
    assert WX.race_window(DAY, "05:00", 2, 3600) == (dt.datetime(2026, 10, 7), dt.datetime(2026, 10, 9))
    assert WX.race_window(None, "05:00", 1, 3600) is None


# ---- rows from the providers -------------------------------------------------------

def test_open_meteo_asks_for_rain_and_rows_cover_the_preceding_hour(tmp_path):
    seen = {}

    def get(url, params, timeout):
        seen.update(params)
        return _om([10] * 8 + [60] + [10] * 15, [0.0] * 24)
    r = WX.race_conditions(date=dt.date(2026, 10, 7), lat=25.0, lon=121.5, elevation_m=300,
                           today=dt.date(2026, 10, 6), key=None, get=get, cache_dir=tmp_path, use_cwa=False)
    assert r["provider"] == "open_meteo"
    assert "precipitation" in seen["hourly"] and "precipitation_probability" in seen["hourly"]
    row = next(x for x in r["rain"] if x["end"] == f"{DAY}T08:00")
    assert row == {"start": f"{DAY}T07:00", "end": f"{DAY}T08:00", "pop_pct": 60.0, "mm": 0.0}


def test_open_meteo_without_rain_columns_gives_no_rows(tmp_path):
    def get(url, params, timeout):
        js = _om(None, None)
        js["hourly"].pop("precipitation"), js["hourly"].pop("precipitation_probability")
        return js
    r = WX.race_conditions(date=dt.date(2026, 10, 7), lat=25.0, lon=121.5, elevation_m=300,
                           today=dt.date(2026, 10, 6), key=None, get=get, cache_dir=tmp_path, use_cwa=False)
    assert r["provider"] == "open_meteo" and r["rain"] is None


def test_cwa_pop_spans_are_parsed():
    def pop(h, v, span=3):
        s = dt.datetime.fromisoformat(f"{DAY}T{h:02d}:00:00+08:00")
        return {"StartTime": s.isoformat(), "EndTime": (s + dt.timedelta(hours=span)).isoformat(),
                "ElementValue": [{"ProbabilityOfPrecipitation": v}]}
    doc = {"cwaopendata": {"Dataset": {"Locations": [{"Location": [{
        "LocationName": "北投區", "Geocode": "6301200", "Latitude": "25.13", "Longitude": "121.53",
        "WeatherElement": [{"ElementName": "3小時降雨機率", "Time": [pop(6, "70"), pop(9, "-"), pop(12, "20")]}]}]}]}}}
    loc = WX.parse_cwa(doc)["locations"][0]
    rows = WX.cwa_rain_rows(loc, {dt.date(2026, 10, 7)})
    assert rows == [{"start": f"{DAY}T06:00", "end": f"{DAY}T09:00", "pop_pct": 70.0, "mm": None},
                    {"start": f"{DAY}T12:00", "end": f"{DAY}T15:00", "pop_pct": 20.0, "mm": None}]
    assert WX.rain_alert(rows, WIN)["alert"] is True


# ---- the plan: shown on trail / 百岳, not on road, time unchanged ----------------------

def _rain_rows(pop):
    return [{"start": f"2099-05-01T{h:02d}:00", "end": f"2099-05-01T{h + 1:02d}:00", "pop_pct": pop, "mm": 0.5}
            for h in range(0, 23)]


@pytest.mark.parametrize("kind", ["trail", "baiyue"])
def test_plan_reminder_without_changing_the_time(client, kind):   # noqa: F811
    cid = _upload(client)["course_id"]
    body = {"type": kind, "course": {"course_id": cid}, "date": "2099-05-01", "start_time": "06:00"}
    dry = client.post("/api/v1/racepower/plan", json={**body, "rain": _rain_rows(10)}).json()
    wet = client.post("/api/v1/racepower/plan", json={**body, "rain": _rain_rows(80)}).json()
    none = client.post("/api/v1/racepower/plan", json=body).json()
    assert wet["rain"]["alert"] is True and wet["rain"]["message"] == MSG
    assert dry["rain"]["alert"] is False
    assert none["rain"] is None
    assert wet["summary"]["time_s"] == dry["summary"]["time_s"] == none["summary"]["time_s"]


def test_road_plan_shows_no_reminder(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    p = client.post("/api/v1/racepower/plan", json={"type": "road", "course": {"course_id": cid}, "date": "2099-05-01",
                                                    "start_time": "06:00", "rain": _rain_rows(90)}).json()
    assert p["rain"] is None
