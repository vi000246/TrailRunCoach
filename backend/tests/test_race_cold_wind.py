"""SP-251: wind from the race-day forecast → wind chill per segment and one 冷風 line in the
calculator's attention box (cold.py). Thresholds: wind chill ≤ −10 °C (ECCC, owner 2026-10-06) or
gusts ≥ 50 km/h; ≤ −28 °C → frostbite wording. No wind data → nothing, no error. The predicted
time never changes. Fakes only, no network."""
import datetime as dt

import pytest

from backend.engine.racepower import cold as CD
from backend.engine.racepower import weather as WX
from backend.tests.test_race_calculator import _upload
from backend.tests.test_racepower_v2 import client  # noqa: F401  (fixture)

DAY = "2099-01-10"


def _wc_temp(target: float, wind: float) -> float:
    """The air temperature that gives wind chill `target` at `wind` km/h (bisection)."""
    lo, hi = -60.0, 10.0
    for _ in range(80):
        m = (lo + hi) / 2
        if CD.wind_chill_c(m, wind) > target:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2


def _rows(temp, wind, gust=None, day=DAY):
    return [{"t": f"{day}T{h:02d}:00", "temp_c": temp, "wind_kmh": wind, "gust_kmh": gust}
            for h in range(24)]


def _pts(n=3, start=dt.datetime(2099, 1, 10, 6)):
    return [{"i": i + 1, "start_km": 2.0 * i, "end_km": 2.0 * (i + 1), "z": None, "temp_c": None,
             "begin": start + dt.timedelta(minutes=20 * i), "end": start + dt.timedelta(minutes=20 * (i + 1))}
            for i in range(n)]


# ---- the formula --------------------------------------------------------------------------

def test_nws_wind_chill_matches_the_chart():
    # NWS chart: 0 °F and 15 mph → −19 °F
    assert CD.wind_chill_c((0 - 32) * 5 / 9, 15 * 1.609344) == pytest.approx((-19 - 32) * 5 / 9, abs=0.3)
    # 20 °F, 30 mph → 1 °F
    assert CD.wind_chill_c((20 - 32) * 5 / 9, 30 * 1.609344) == pytest.approx((1 - 32) * 5 / 9, abs=0.3)


def test_wind_chill_only_inside_its_range():
    assert CD.wind_chill_c(10.5, 30) is None          # warmer than 10 °C
    assert CD.wind_chill_c(0.0, 4.8) is None          # wind ≤ 4.8 km/h
    assert CD.wind_chill_c(None, 30) is None and CD.wind_chill_c(0.0, None) is None
    assert CD.wind_chill_c(10.0, 4.9) is not None


# ---- thresholds -----------------------------------------------------------------------------

def test_wind_chill_at_minus_10_alerts_just_above_does_not():
    t = _wc_temp(-10.0, 30.0)
    on = CD.cold_wind(_pts(), _rows(t - 0.01, 30.0), date=DAY)
    off = CD.cold_wind(_pts(), _rows(t + 0.05, 30.0), date=DAY)
    assert on["alert"] is True and on["level"] == "cold" and on["min_wc_c"] <= -10.0
    assert off["alert"] is False and off["level"] is None and off["min_wc_c"] > -10.0


def test_gust_at_50_alerts_even_when_mild():
    on = CD.cold_wind(_pts(), _rows(12.0, 20.0, gust=50.0), date=DAY)
    off = CD.cold_wind(_pts(), _rows(12.0, 20.0, gust=49.9), date=DAY)
    assert on["alert"] is True and on["level"] == "gust" and on["min_wc_c"] is None
    assert off["alert"] is False


def test_frostbite_wording_at_minus_28():
    r = CD.cold_wind(_pts(), _rows(_wc_temp(-28.5, 40.0), 40.0), date=DAY)
    assert r["level"] == "frostbite"
    a = CD.attention(r)
    assert "外露皮膚 10–30 分鐘可能凍傷" in a["line"] and "遮住臉和手" in a["gear"]
    mild = CD.attention(CD.cold_wind(_pts(), _rows(_wc_temp(-15.0, 40.0), 40.0), date=DAY))
    assert "凍傷" not in mild["line"]


def test_the_line_names_km_clock_and_wind_chill():
    r = CD.cold_wind(_pts(), _rows(_wc_temp(-12.0, 30.0), 30.0), date=DAY)
    assert len(r["ranges"]) == 1                       # consecutive segments merged into one range
    rg = r["ranges"][0]
    assert (rg["start_km"], rg["end_km"], rg["from"], rg["to"]) == (0.0, 6.0, "06:00", "07:00")
    a = CD.attention(r)
    assert a["kinds"] == ["cold_wind"]
    assert a["line"].startswith("冷風：km 0.0–6.0（06:00–07:00）風寒 −12 °C")
    assert "防風外套" in a["line"] and len(a["details"]) == 2


def test_no_wind_data_is_none_and_no_line():
    assert CD.cold_wind(_pts(), None) is None
    assert CD.cold_wind(_pts(), []) is None
    assert CD.cold_wind(_pts(), [{"t": f"{DAY}T06:00", "temp_c": 0.0, "wind_kmh": None}]) is None
    far = _rows(-20.0, 60.0, day="2099-01-12")        # rows two days off: no point has wind
    assert CD.cold_wind(_pts(), far) is None
    assert CD.attention(None) is None
    calm = CD.cold_wind(_pts(), _rows(5.0, 10.0), date=DAY)
    assert calm["alert"] is False and CD.attention(calm) is None


def test_temperature_follows_the_segment_height():
    pts = _pts(1)
    pts[0]["z"] = 3500.0
    low = CD.cold_wind(pts, _rows(0.0, 30.0), z_ref=1500.0, date=DAY)
    assert low["min_temp_c"] == pytest.approx(0.0 - 0.0065 * 2000.0)
    assert low["alert"] is True                       # −13 °C at 30 km/h


def test_window_points_without_a_start_time():
    plan = {"type": "baiyue", "segments": [], "summary": {"clock_s": 8 * 3600}}
    pts = CD.points(plan, DAY, None)
    assert pts[0]["begin"] == dt.datetime(2099, 1, 10, 6) and pts[-1]["end"] == dt.datetime(2099, 1, 10, 18)
    rows = _rows(0.0, 10.0)
    rows[14] = {**rows[14], "temp_c": -8.0, "wind_kmh": 40.0}
    r = CD.cold_wind(pts, rows, date=DAY)
    assert r["alert"] and r["per_km"] is False and r["ranges"][0]["start_km"] is None
    assert CD.attention(r)["line"].startswith("冷風：13:00–15:00")


def test_baiyue_days_start_from_the_start_time():
    segs = [{"i": 1, "day": 1, "start_km": 0, "end_km": 5, "t": 3600, "cum_s": 3600},
            {"i": 2, "day": 2, "start_km": 5, "end_km": 9, "t": 1800, "cum_s": 5400}]
    pts = CD.points({"type": "baiyue", "segments": segs, "summary": {"moving_ratio": 0.5}}, DAY, "04:00")
    assert pts[0]["begin"] == dt.datetime(2099, 1, 10, 4) and pts[0]["end"] == dt.datetime(2099, 1, 10, 6)
    assert pts[1]["begin"] == dt.datetime(2099, 1, 11, 4) and pts[1]["end"] == dt.datetime(2099, 1, 11, 5)
    assert CD.clock(pts[1]["begin"], DAY) == "04:00 (+1)"


# ---- rows from the providers ---------------------------------------------------------------

def test_open_meteo_asks_for_wind_and_gives_rows(tmp_path):
    seen = {}

    def get(url, params, timeout):
        seen.update(params)
        times = [f"2026-10-07T{h:02d}:00" for h in range(24)]
        return {"elevation": 3000, "hourly": {"time": times, "temperature_2m": [-2.0] * 24,
                                              "relative_humidity_2m": [80.0] * 24, "wind_speed_10m": [35.0] * 24,
                                              "wind_gusts_10m": [60.0] * 24, "apparent_temperature": [-9.0] * 24}}
    r = WX.race_conditions(date=dt.date(2026, 10, 7), lat=23.47, lon=120.96, elevation_m=3000,
                           today=dt.date(2026, 10, 6), key=None, get=get, cache_dir=tmp_path, use_cwa=False)
    assert r["provider"] == "open_meteo"
    for k in ("wind_speed_10m", "wind_gusts_10m", "apparent_temperature"):
        assert k in seen["hourly"]
    row = next(x for x in r["wind"] if x["t"] == "2026-10-07T08:00")
    assert row == {"t": "2026-10-07T08:00", "temp_c": -2.0, "wind_kmh": 35.0, "gust_kmh": 60.0, "apparent_c": -9.0}


def test_open_meteo_without_wind_gives_no_rows(tmp_path):
    def get(url, params, timeout):
        times = [f"2026-10-07T{h:02d}:00" for h in range(24)]
        return {"elevation": 300, "hourly": {"time": times, "temperature_2m": [20.0] * 24,
                                             "relative_humidity_2m": [80.0] * 24}}
    r = WX.race_conditions(date=dt.date(2026, 10, 7), lat=25.0, lon=121.5, elevation_m=300,
                           today=dt.date(2026, 10, 6), key=None, get=get, cache_dir=tmp_path, use_cwa=False)
    assert r["provider"] == "open_meteo" and r["wind"] is None


def test_cwa_wind_speed_in_metres_per_second():
    """F-B0053 (休閒旅遊 / 登山) 3-day product: 「風速」 rows carry WindSpeed (m/s) and BeaufortScale
    (CWA 產品說明文件, 2024-12-10); the temperature of the same file is attached."""
    def t(h):
        return dt.datetime.fromisoformat(f"2026-10-07T{h:02d}:00:00+08:00").isoformat()
    doc = {"cwaopendata": {"Sent": "2026-10-07T05:30:00+08:00", "Dataset": {"Locations": {"Location": [{
        "LocationName": "玉山", "Latitude": "23.47", "Longitude": "120.96",
        "ParameterSet": {"Parameter": [{"ParameterName": "id", "ParameterValue": "D055"}]},
        "WeatherElement": [
            {"ElementName": "溫度", "Time": [{"DataTime": t(h), "ElementValue": [{"Temperature": str(-h)}]} for h in (0, 3, 6)]},
            {"ElementName": "相對濕度", "Time": [{"DataTime": t(h), "ElementValue": [{"RelativeHumidity": "90"}]} for h in (0, 3, 6)]},
            {"ElementName": "風速", "Time": [{"DataTime": t(h), "ElementValue": [{"WindSpeed": w, "BeaufortScale": "5"}]}
                                           for h, w in ((0, "10"), (3, "-"), (6, "12"))]}]}]}}}}
    loc = WX.parse_cwa(doc)["locations"][0]
    assert loc["wind"] == [{"t": t(0), "ms": 10.0}, {"t": t(6), "ms": 12.0}]
    rows = WX.cwa_wind_rows(loc, {dt.date(2026, 10, 7)})
    assert rows[0] == {"t": "2026-10-07T00:00", "temp_c": 0.0, "wind_kmh": pytest.approx(36.0), "gust_kmh": None,
                       "apparent_c": None}
    assert rows[1]["wind_kmh"] == pytest.approx(43.2) and rows[1]["temp_c"] == -6.0


# ---- the plan: road / trail / 百岳, time unchanged ---------------------------------------------

@pytest.mark.parametrize("kind", ["road", "trail", "baiyue"])
def test_plan_reminder_without_changing_the_time(client, kind):   # noqa: F811
    cid = _upload(client)["course_id"]
    body = {"type": kind, "course": {"course_id": cid}, "date": DAY, "start_time": "06:00"}
    cold = client.post("/api/v1/racepower/plan", json={**body, "wind": _rows(-8.0, 40.0)}).json()
    calm = client.post("/api/v1/racepower/plan", json={**body, "wind": _rows(5.0, 10.0)}).json()
    none = client.post("/api/v1/racepower/plan", json=body).json()
    assert cold["cold_wind"]["alert"] is True and cold["cold_wind"]["per_km"] is True
    # SP-253: −8 °C with wind chill ≤ −5 °C also lists the 失溫 checklist on trail / 百岳, in the same line
    assert cold["attention"]["kinds"] == (["cold_wind"] if kind == "road" else ["cold_wind", "hypothermia"])
    assert "風寒" in cold["attention"]["line"]
    assert calm["cold_wind"]["alert"] is False and calm["attention"] is None
    assert none["cold_wind"] is None and none["attention"] is None
    assert cold["summary"]["time_s"] == calm["summary"]["time_s"] == none["summary"]["time_s"]
    assert [s["t"] for s in cold["segments"]] == [s["t"] for s in none["segments"]]
