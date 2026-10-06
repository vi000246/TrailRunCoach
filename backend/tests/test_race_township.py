"""SP-234: CWA 鄉鎮天氣預報 in the race-day weather chain — the nearest 鄉鎮公所,
temperature moved from its height to the race height (−0.65 °C / 100 m, RH
kept). Owner decision 2026-10-06: after Open-Meteo, as its fallback, until a
station comparison exists — chain 登山 (5 km) → Open-Meteo → 鄉鎮 → climatology
→ manual. CWA, the elevation API and Open-Meteo are all fakes: no network."""
import datetime as dt

import pytest

from backend.engine.racepower import weather as WX
from backend.engine.racepower.env import dew_point

approx = pytest.approx
TODAY = dt.date(2026, 10, 6)
RACE = dt.date(2026, 10, 7)
# a 郊山 trail far from any 登山預報 point: 鄉鎮公所 ~4 km away at 120 m, race start at 620 m
RACE_LAT, RACE_LON = 25.10, 121.55
TOWN_LAT, TOWN_LON = 25.13, 121.53


def _town_doc(day: str, *, hourly=True) -> dict:
    """A 鄉鎮預報 fileapi document in the format of CWA's product sheet
    (Locations → LocationsName / Location → LocationName, Geocode, Latitude,
    Longitude, WeatherElement → ElementName, Time → DataTime | StartTime/EndTime)."""
    def el(name, key, rows):
        return {"ElementName": name, "Time": rows}

    def at(h, key, v):
        return {"DataTime": f"{day}T{h:02d}:00:00+08:00", "ElementValue": [{key: v}]}

    def blk(h, key, v):
        end = dt.datetime.fromisoformat(f"{day}T{h:02d}:00:00+08:00") + dt.timedelta(hours=12)
        return {"StartTime": f"{day}T{h:02d}:00:00+08:00", "EndTime": end.isoformat(), "ElementValue": [{key: v}]}
    if hourly:
        wx = [el("溫度", "Temperature", [at(h, "Temperature", "20") for h in range(0, 24)]),
              el("露點溫度", "DewPoint", [at(h, "DewPoint", "15") for h in range(0, 24)]),
              el("相對濕度", "RelativeHumidity", [at(h, "RelativeHumidity", "80") for h in range(0, 24)])]
    else:
        wx = [el("平均溫度", "Temperature", [blk(6, "Temperature", "22"), blk(18, "Temperature", "18")]),
              el("平均相對濕度", "RelativeHumidity", [blk(6, "RelativeHumidity", "75"), blk(18, "RelativeHumidity", "85")])]
    return {"cwaopendata": {"Sent": f"{day}T05:30:00+08:00", "Dataset": {"Locations": [{
        "LocationsName": "臺灣", "Location": [
            {"LocationName": "北投區", "Geocode": "6301200", "Latitude": str(TOWN_LAT), "Longitude": str(TOWN_LON),
             "WeatherElement": wx},
            {"LocationName": "恆春鎮", "Geocode": "10013010", "Latitude": "22.00", "Longitude": "120.74",
             "WeatherElement": wx},
        ]}]}}}


def _mountain_doc() -> dict:
    # one 登山 point far from the race (玉山): the mountain step finds nothing within 5 km
    return {"cwaopendata": {"Dataset": {"Locations": {"Location": [
        {"LocationName": "玉山", "Latitude": "23.47", "Longitude": "120.957",
         "ParameterSet": {"Parameter": {"ParameterName": "id", "ParameterValue": "D080"}}, "WeatherElement": []}]}}}}


class Fake:
    def __init__(self, *, town_hourly=True, elev=(120.0, 620.0), om_fails=True):
        # om_fails (default): the Open-Meteo forecast and archive fail, so the 鄉鎮 fallback is reached
        self.calls = []
        self.town_hourly, self.elev, self.om_fails = town_hourly, elev, om_fails

    def __call__(self, url, params, timeout):
        self.calls.append((url, dict(params)))
        if "opendataapi" in url:
            ds = url.rsplit("/", 1)[-1]
            if ds.startswith("F-B0053"):
                return _mountain_doc()
            if ds == WX.CWA_TOWN_HOURLY:
                return _town_doc(RACE.isoformat(), hourly=self.town_hourly)
            return _town_doc(RACE.isoformat(), hourly=False)
        if url == WX.OM_ELEVATION:
            n = len(params["latitude"].split(","))
            return {"elevation": list(self.elev[:n])}
        if self.om_fails:
            raise RuntimeError("HTTP 500")
        times = [f"{RACE.isoformat()}T{h:02d}:00" for h in range(24)]
        return {"elevation": 600, "hourly": {"time": times, "temperature_2m": [10.0] * 24,
                                            "relative_humidity_2m": [60.0] * 24}}

    def urls(self):
        return [u for u, _p in self.calls]


def _run(tmp_path, get, **kw):
    args = dict(date=RACE, lat=RACE_LAT, lon=RACE_LON, elevation_m=620.0, name=None, today=TODAY,
                key="CWA-TESTKEY-123", get=get, cache_dir=tmp_path)
    args.update(kw)
    return WX.race_conditions(**args)


def test_dataset_ids_are_the_whole_taiwan_township_products():
    # data.gov.tw 9307 / 9308: 鄉鎮天氣預報-台灣未來3天 / 未來1週
    assert (WX.CWA_TOWN_HOURLY, WX.CWA_TOWN_WEEKLY) == ("F-D0047-089", "F-D0047-091")


def test_parse_reads_the_township_format_with_geocode():
    doc = WX.parse_cwa(_town_doc("2026-10-07"))
    bt = doc["locations"][0]
    assert bt["name"] == "北投區" and bt["id"] == "6301200" and bt["lat"] == TOWN_LAT
    assert len(bt["hourly"]) == 24 and bt["hourly"][6]["temp"] == 20 and bt["hourly"][6]["rh"] == 80


def test_with_a_key_and_open_meteo_answering_open_meteo_is_used(tmp_path):
    g = Fake(om_fails=False)
    r = _run(tmp_path, g)
    assert r["provider"] == "open_meteo"
    assert [t["provider"] for t in r["tried"]][-1] == "open_meteo"
    assert not any(t["provider"].startswith("cwa_town") for t in r["tried"])
    assert not any(u.endswith(WX.CWA_TOWN_HOURLY) or u == WX.OM_ELEVATION for u in g.urls())


def test_open_meteo_failing_falls_back_to_the_township_forecast_with_lapse(tmp_path):
    g = Fake()
    r = _run(tmp_path, g)
    assert r["provider"] == "cwa_town_hourly"
    d = r["detail"]
    assert d["cwa_location"] == "北投區" and d["town"] is True and d["distance_km"] < 5
    # the race elevation was given: only the township point is looked up
    assert d["town_elevation_m"] == 120 and d["target_elevation_m"] == 620 and d["lapse_m"] == 500
    assert d["offset_c"] == approx(-3.25)
    v = r["values"]
    assert v["temp_c"] == approx(20 - 3.25) and v["rh_pct"] == 80          # RH unchanged
    assert v["dew_c"] == approx(dew_point(20 - 3.25, 80)["dew_c"])
    assert v["altitude_m"] == 620
    # hourly rows for per-segment heat, moved the same way
    assert r["hourly"] and all(h["temp_c"] == approx(16.75) and h["rh_pct"] == approx(80) for h in r["hourly"])
    provs = [(t["provider"], t["ok"]) for t in r["tried"]]
    assert provs[0][0] == "cwa_hourly" and not provs[0][1]          # no 登山 point within 5 km
    # the 「嘗試」 order: 登山 ✕ → Open-Meteo ✕ → 鄉鎮 ✓
    assert provs.index(("open_meteo", False)) < provs.index(("cwa_town_hourly", True))
    assert any(u == WX.OM_FORECAST for u in g.urls())


def test_without_a_race_elevation_both_heights_come_from_one_elevation_call(tmp_path):
    g = Fake(elev=(120.0, 900.0))
    r = _run(tmp_path, g, elevation_m=None)
    assert r["detail"]["target_elevation_m"] == 900 and r["values"]["altitude_m"] == 900
    assert r["values"]["temp_c"] == approx(20 - 0.0065 * 780)
    ev = [p for u, p in g.calls if u == WX.OM_ELEVATION]
    assert len(ev) == 1 and len(ev[0]["latitude"].split(",")) == 2


def test_downloads_and_heights_are_cached(tmp_path):
    g = Fake()

    def cwa_or_height():
        return [u for u in g.urls() if "opendataapi" in u or u == WX.OM_ELEVATION]
    _run(tmp_path, g)
    n = len(cwa_or_height())
    r = _run(tmp_path, g)
    assert r["provider"] == "cwa_town_hourly" and r["detail"]["cache"] == "hit"
    # the second run: mountain + township files from the cache (< 3 h), height from the cache
    assert len(cwa_or_height()) == n
    assert (tmp_path / f"cwa_{WX.CWA_TOWN_HOURLY}.json").exists() and (tmp_path / WX.ELEV_CACHE).exists()


def test_beyond_three_days_the_weekly_blocks_are_used(tmp_path):
    g = Fake()
    r = _run(tmp_path, g, today=RACE - dt.timedelta(days=4))
    assert r["provider"] == "cwa_town_weekly"
    assert r["values"]["temp_c"] == approx(22 - 3.25) and r["values"]["rh_pct"] == 75
    assert r["hourly"] is None                       # 12-hour blocks: one heat value
    reasons = {t["provider"]: t.get("reason") for t in r["tried"]}
    assert reasons["cwa_town_hourly"]


def test_no_key_falls_through_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(WX, "load_key", lambda *a, **k: None)
    g = Fake()
    r = _run(tmp_path, g, key=None)
    assert r["provider"] == "manual"
    town = next(t for t in r["tried"] if t["provider"] == "cwa_town")
    assert not town["ok"] and "授權碼" in town["reason"]
    assert not any("opendataapi" in u or u == WX.OM_ELEVATION for u in g.urls())


def test_no_township_point_near_falls_through(tmp_path):
    g = Fake()
    r = _run(tmp_path, g, lat=35.36, lon=138.73)         # 富士山: no 鄉鎮 within 20 km
    assert r["provider"] == "manual"
    t = next(t for t in r["tried"] if t["provider"] == "cwa_town_hourly")
    assert not t["ok"] and "鄉鎮預報點" in t["reason"]


def test_offline_falls_through_with_the_error(tmp_path):
    def offline(url, params, timeout):
        raise RuntimeError("ConnectError: offline")
    r = _run(tmp_path, offline)
    assert r["provider"] == "manual"
    town = [t for t in r["tried"] if t["provider"].startswith("cwa_town")]
    assert town and not any(t["ok"] for t in town) and all("offline" in t["reason"] for t in town)


def test_no_height_for_the_point_falls_through(tmp_path):
    g = Fake(elev=(None, None))
    r = _run(tmp_path, g)
    assert r["provider"] == "manual"
    t = next(t for t in r["tried"] if t["provider"] == "cwa_town_hourly")
    assert not t["ok"] and "海拔" in t["reason"]


def test_a_mountain_point_within_5_km_still_wins(tmp_path):
    # a 登山 point at the race itself (same element format as the township file)
    near = _town_doc(RACE.isoformat())
    near["cwaopendata"]["Dataset"]["Locations"][0]["Location"][0].update(
        LocationName="七星山", Latitude=str(RACE_LAT), Longitude=str(RACE_LON))
    g = Fake()

    def get(url, params, timeout):
        if url.endswith(WX.CWA_HOURLY):
            return near
        return g(url, params, timeout)
    r = _run(tmp_path, get)
    assert r["provider"] == "cwa_hourly" and r["detail"]["cwa_location"] == "七星山"
    assert not any(u.endswith(WX.CWA_TOWN_HOURLY) for u in g.urls())


def test_without_cwa_the_township_step_is_skipped(tmp_path):
    g = Fake()
    r = _run(tmp_path, g, use_cwa=False)
    assert r["provider"] == "manual"
    assert {"provider": "cwa_town", "ok": False, "reason": "未使用"} in r["tried"]
