"""SP-553: a long course's weather at several points along it — one per 10 km, at most 6 — each
section's rows read at the clock the plan reaches that section (heat, then the rain, 冷風 and 失溫
reminders), moved to the segment's height from the section point's. The call count has a cap
(CWA files shared by the site, Open-Meteo once per point and memoised, no climatology for the
points). The weather card links to the same route in 疊石 Cairn when the event's note holds its
link. Fakes only, no network."""
import datetime as dt

import pytest

from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import weather as WX
from backend.tests.test_race_calculator import _upload
from backend.tests.test_racepower_v2 import client, synthetic_track  # noqa: F401  (fixture)

approx = pytest.approx
DAY = "2099-01-10"
KM_DEG = 1 / 111.195                       # synthetic_track runs north: 1 km ≈ this many degrees


def _flat(km):
    return synthetic_track({"len": km * 1000, "z": lambda x: 500.0 + 0.01 * x})


# ---- the points -------------------------------------------------------------------------------

def test_one_point_per_10_km_in_the_middle_of_each_section():
    tr = _flat(40)
    secs = WX.route_sections(tr.lat, tr.lon, tr.ele)
    assert [(s["from_km"], s["to_km"]) for s in secs] == [(0, 10), (10, 20), (20, 30), (30, 40)]
    assert [s["km"] for s in secs] == [approx(5, abs=0.02), approx(15, abs=0.02), approx(25, abs=0.02),
                                       approx(35, abs=0.02)]
    assert secs[2]["elevation_m"] == approx(500 + 0.01 * 25000, abs=1)
    assert secs[1]["lat"] == approx(24.0 + 15 * KM_DEG, abs=1e-3)


def test_at_most_six_points_and_none_on_a_short_course():
    tr = _flat(150)
    secs = WX.route_sections(tr.lat, tr.lon, tr.ele)
    assert len(secs) == WX.ROUTE_MAX_POINTS == 6 and secs[-1]["to_km"] == approx(150, abs=0.1)
    short = _flat(6)
    assert WX.route_sections(short.lat, short.lon, short.ele) == []


def _mountain_doc(points):
    """A CWA 登山三天 document with one location per (name, lat, temp)."""
    def at(h, key, v):
        return {"DataTime": f"{DAY}T{h:02d}:00:00+08:00", "ElementValue": [{key: v}]}
    locs = []
    for name, lat, temp in points:
        locs.append({"LocationName": name, "Latitude": str(lat), "Longitude": "121.0",
                     "ParameterSet": {"Parameter": {"ParameterName": "id", "ParameterValue": name}},
                     "WeatherElement": [
                         {"ElementName": "溫度", "Time": [at(h, "Temperature", str(temp)) for h in range(24)]},
                         {"ElementName": "相對濕度", "Time": [at(h, "RelativeHumidity", "80") for h in range(24)]}]})
    return {"cwaopendata": {"Dataset": {"Locations": {"Location": locs}}}}


def test_each_point_gets_its_own_forecast_with_a_call_cap(tmp_path):
    tr = _flat(40)
    secs = WX.route_sections(tr.lat, tr.lon, tr.ele)
    calls = []
    doc = _mountain_doc([("甲山", secs[0]["lat"], 12), ("乙山", secs[2]["lat"], 3)])

    def get(url, params, timeout):
        calls.append(url)
        if url.endswith(WX.CWA_HOURLY):
            return doc
        if "opendataapi" in url:
            raise RuntimeError("HTTP 500")                  # the 鄉鎮 / weekly files: not needed here
        if url == WX.OM_FORECAST:
            times = [f"{DAY}T{h:02d}:00" for h in range(24)]
            return {"elevation": 600, "hourly": {"time": times, "temperature_2m": [18.0] * 24,
                                                "relative_humidity_2m": [70.0] * 24}}
        raise AssertionError(f"no climatology for the points: {url}")
    pts = WX.route_conditions(secs, date=dt.date(2099, 1, 10), today=dt.date(2099, 1, 9), key="k" * 12,
                              get=get, cache_dir=tmp_path)
    assert [p["provider"] for p in pts] == ["cwa_hourly", "open_meteo", "cwa_hourly", "open_meteo"]
    assert pts[0]["detail"]["cwa_location"] == "甲山" and pts[2]["detail"]["cwa_location"] == "乙山"
    assert pts[0]["hourly"][9]["temp_c"] == 12 and pts[2]["hourly"][9]["temp_c"] == 3
    assert pts[1]["elevation_m"] == approx(secs[1]["elevation_m"])     # the height the rows refer to
    # the cap: the 3-day file once for every point (shared), Open-Meteo once per point that needs it
    assert calls.count(WX.CWA_URL.format(id=WX.CWA_HOURLY)) == 1
    assert calls.count(WX.OM_FORECAST) == 2
    assert not any(u == WX.OM_ARCHIVE for u in calls)


# ---- the weather endpoint -----------------------------------------------------------------------

def _upload_track(client, tr):   # noqa: F811
    up = client.post("/api/v1/racepower/course",
                     files={"file": ("long.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")})
    assert up.status_code == 200, up.text
    return up.json()["course_id"]


def _fake_conditions(seen, provider="open_meteo"):
    def fake(**kw):
        seen.append(kw)
        return {"provider": provider, "label": "x", "values": {"altitude_m": kw.get("elevation_m"), "temp_c": 10.0,
                                                               "rh_pct": 80.0, "dew_c": 6.7},
                "tried": [], "location": {}, "detail": {}, "hourly": [{"t": f"{DAY}T09:00", "temp_c": 10.0,
                                                                        "rh_pct": 80.0, "dew_c": 6.7}],
                "rain": None, "wind": None, "sun": None}
    return fake


def test_the_endpoint_adds_the_points_of_the_uploaded_course(client, monkeypatch):   # noqa: F811
    cid = _upload_track(client, _flat(150))
    seen = []
    monkeypatch.setattr(WX, "race_conditions", _fake_conditions(seen))
    r = client.get("/api/v1/racepower/weather", params={"date": DAY, "lat": 24.0, "lon": 121.0, "course_id": cid})
    assert r.status_code == 200, r.text
    js = r.json()
    assert len(js["points"]) == 6 and js["points"][3]["from_km"] == approx(75, abs=0.5)
    assert len(seen) == 1 + 6                                  # the race's own point + at most 6
    assert all(kw["forecast_only"] and kw["name"] is None for kw in seen[1:])
    assert js["cairn"] is None


def test_no_points_for_a_far_off_date(client, monkeypatch):   # noqa: F811
    cid = _upload_track(client, _flat(40))
    seen = []
    monkeypatch.setattr(WX, "race_conditions", _fake_conditions(seen, "climatology"))
    js = client.get("/api/v1/racepower/weather", params={"date": DAY, "lat": 24.0, "lon": 121.0,
                                                         "course_id": cid}).json()
    assert js["points"] is None and len(seen) == 1


def test_the_cairn_link_comes_from_the_event_note(client, monkeypatch):   # noqa: F811
    from backend.api import racepower as RP
    notes = {"e1": "行程在 https://map.yichlin.com/?gpx=r_7Kp2&share=AbCdEf123456 看", "e2": "沒有連結"}
    monkeypatch.setattr(RP, "_event", lambda eid: type("E", (), {"id": eid, "note": notes[eid]})())
    monkeypatch.setattr(WX, "race_conditions", _fake_conditions([], "manual"))
    q = {"date": DAY, "lat": 24.0, "lon": 121.0}
    js = client.get("/api/v1/racepower/weather", params={**q, "link_event": "e1"}).json()
    assert js["cairn"] == {"url": "https://map.yichlin.com/?gpx=r_7Kp2&share=AbCdEf123456", "from": "note"}
    assert client.get("/api/v1/racepower/weather", params={**q, "link_event": "e2"}).json()["cairn"] is None


def test_cairn_links_are_rebuilt_from_their_ids_only():
    assert WX.cairn_url("https://map.yichlin.com/?gpx=abc") == "https://map.yichlin.com/?gpx=abc"
    assert WX.cairn_url("HTTPS://MAP.YICHLIN.COM/?share=Tok12345&gpx=x1&evil=1") == \
        "https://map.yichlin.com/?gpx=x1&share=Tok12345"
    assert WX.cairn_url("https://map.yichlin.com.evil.io/?gpx=abc") is None
    assert WX.cairn_url("https://map.yichlin.com/?gpx=<script>") is None
    assert WX.cairn_url(None) is None and WX.cairn_url("https://map.yichlin.com/") is None


# ---- the plan: each segment reads its own section ----------------------------------------------

def _hours(temp, rh=60.0, day=DAY):
    return [{"t": f"{day}T{h:02d}:00", "temp_c": temp, "rh_pct": rh} for h in range(24)]


def _wind(temp, wind, day=DAY):
    return [{"t": f"{day}T{h:02d}:00", "temp_c": temp, "wind_kmh": wind, "gust_kmh": None} for h in range(24)]


def _plan(client, cid, **kw):   # noqa: F811
    body = {"type": "trail", "course": {"course_id": cid}, "date": DAY, "start_time": "09:00", **kw}
    r = client.post("/api/v1/racepower/plan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _mid(s):
    return (s["start_km"] + s["end_km"]) / 2


def test_heat_per_segment_from_its_own_section(client):   # noqa: F811
    cid = _upload(client)["course_id"]                         # 12 km, 500 → 860 → 560 → 660 m
    pts = [{"i": 0, "from_km": 0, "to_km": 6, "elevation_m": 500, "hourly": _hours(28.0)},
           {"i": 1, "from_km": 6, "to_km": 12, "elevation_m": 660, "hourly": _hours(6.0)}]
    p = _plan(client, cid, hourly=_hours(28.0), heat_ref_alt_m=500, weather_points=pts)
    one = _plan(client, cid, hourly=_hours(28.0), heat_ref_alt_m=500)
    for s in p["segments"]:
        if _mid(s) < 6:
            assert s["wx_point"] == 0
            assert s["temp_c"] == approx(28.0 - 0.0065 * (s["z_mean"] - 500), abs=0.05)
        else:
            assert s["wx_point"] == 1
            assert s["temp_c"] == approx(6.0 - 0.0065 * (s["z_mean"] - 660), abs=0.05)
    assert all(s["wx_point"] is None for s in one["segments"])
    # the cold half is not slowed by heat: the race is faster than with the one warm point
    assert p["summary"]["time_s"] < one["summary"]["time_s"]


def test_cold_wind_and_hypothermia_per_section(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    pts = [{"i": 0, "from_km": 0, "to_km": 6, "elevation_m": 500, "wind": _wind(12.0, 10.0)},
           {"i": 1, "from_km": 6, "to_km": 12, "elevation_m": 660, "wind": _wind(-8.0, 40.0)}]
    calm = _wind(12.0, 10.0)
    p = _plan(client, cid, wind=calm, heat_ref_alt_m=500, weather_points=pts)
    one = _plan(client, cid, wind=calm, heat_ref_alt_m=500)
    cw = p["cold_wind"]
    assert cw["alert"] is True and cw["ranges"] and all(r["end_km"] > 6 for r in cw["ranges"])
    assert min(r["start_km"] for r in cw["ranges"]) >= 4.0
    assert p["hypothermia"]["alert"] is True and p["hypothermia"]["min_temp_c"] < -5
    assert one["cold_wind"]["alert"] is False and (one["hypothermia"] or {}).get("alert") is not True
    assert p["summary"]["time_s"] == one["summary"]["time_s"]          # reminders never change the time


def test_rain_counts_only_the_hours_each_section_is_run(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    base = _plan(client, cid)
    # the clock the plan reaches km 6 (its first segment past the middle)
    t0 = dt.datetime.fromisoformat(f"{DAY}T09:00")
    later = next(s for s in base["segments"] if _mid(s) >= 6)
    at6 = t0 + dt.timedelta(seconds=later["cum_s"] - later["t"])

    def rows(pop, a, b):
        out, t = [], a
        while t < b:
            out.append({"start": t.strftime("%Y-%m-%dT%H:%M"), "end": (t + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                        "pop_pct": pop, "mm": None})
            t += dt.timedelta(hours=1)
        return out
    day0, day1 = t0.replace(hour=0), t0.replace(hour=23)
    # section 0 rains only once the runner has left it; section 1 is dry
    wet0 = rows(10.0, day0, at6.replace(minute=0)) + rows(90.0, at6.replace(minute=0) + dt.timedelta(hours=1), day1)
    pts = [{"i": 0, "from_km": 0, "to_km": 6, "elevation_m": 500, "rain": wet0},
           {"i": 1, "from_km": 6, "to_km": 12, "elevation_m": 660, "rain": rows(10.0, day0, day1)}]
    p = _plan(client, cid, weather_points=pts)
    assert p["rain"]["alert"] is False and p["rain"]["points"] == 2
    # the same rain where the runner is → the reminder
    pts[1]["rain"] = rows(90.0, day0, day1)
    assert _plan(client, cid, weather_points=pts)["rain"]["alert"] is True
