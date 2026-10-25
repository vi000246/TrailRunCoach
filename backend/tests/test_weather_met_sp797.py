"""SP-797: Open-Meteo's free API is non-commercial only, so the race-day forecast after CWA comes from
MET Norway's Locationforecast 2.0 (free for commercial use, CC BY 4.0) — 7–9 days out, outside Taiwan,
CWA down; a race further out takes the climatology (still Open-Meteo's archive until the ERA5 download).
MET's terms: an identifying User-Agent (every weather call now sends one), ≤ 20 requests a second,
coordinates ≤ 4 decimals, Expires / If-Modified-Since. No gusts in Taiwan → the 冷風 details say the
reminder rests on the wind speed. Fakes only: no network."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from backend.engine.racepower import cold as CD
from backend.engine.racepower import weather as WX

approx = pytest.approx
TODAY = dt.date(2026, 10, 20)


def _v(x, t):
    return x(t) if callable(x) else x


def met_js(day, temp=20.0, rh=80.0, dew=None, wind=None, gust=None, mm=None, pop=None, *, days=1, tz_h=8,
           alt=None, lat=25.0, lon=121.5, six_hourly_from=None):
    """A Locationforecast 2.0 `complete` answer: local hours 00–23 of `days` days from `day` at UTC+tz_h,
    written in UTC ('…Z') as MET does. Values: a number, None (left out) or fn(local datetime). From the
    local datetime `six_hourly_from` on the steps are 6-hourly and carry next_6_hours only."""
    d0 = dt.date.fromisoformat(str(day)) if not isinstance(day, dt.date) else day
    ts, t = [], dt.datetime(d0.year, d0.month, d0.day)
    end = t + dt.timedelta(days=days)
    while t < end:
        six = six_hourly_from is not None and t >= six_hourly_from
        det = {}
        for k, x in (("air_temperature", temp), ("relative_humidity", rh), ("dew_point_temperature", dew),
                     ("wind_speed", wind), ("wind_speed_of_gust", gust)):
            if _v(x, t) is not None:
                det[k] = _v(x, t)
        data = {"instant": {"details": det}}
        per = {}
        if _v(mm, t) is not None:
            per["precipitation_amount"] = _v(mm, t)
        if _v(pop, t) is not None:
            per["probability_of_precipitation"] = _v(pop, t)
        if per:
            data["next_6_hours" if six else "next_1_hours"] = {"summary": {"symbol_code": "rain"}, "details": per}
        utc = t - dt.timedelta(hours=tz_h)
        ts.append({"time": utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "data": data})
        t += dt.timedelta(hours=6 if six else 1)
    coords = [lon, lat] + ([alt] if alt is not None else [])
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": coords},
            "properties": {"meta": {"updated_at": "2026-10-20T06:00:00Z"}, "timeseries": ts}}


def archive(url, params):
    """The Open-Meteo archive (climatology) answer: 15 °C / 70 % every hour of the asked window."""
    lo = dt.date.fromisoformat(params["start_date"])
    n = (dt.date.fromisoformat(params["end_date"]) - lo).days + 1
    times = [f"{lo + dt.timedelta(days=i)}T{h:02d}:00" for i in range(n) for h in range(24)]
    return {"elevation": 1000.0, "hourly": {"time": times, "temperature_2m": [15.0] * len(times),
                                            "relative_humidity_2m": [70.0] * len(times)}}


@pytest.fixture(autouse=True)
def _fresh_met_cache():
    WX._MET_CACHE.clear()
    yield
    WX._MET_CACHE.clear()


# ---- User-Agent ---------------------------------------------------------------------------------

def test_user_agent_names_the_app_and_the_project_page_not_a_person(monkeypatch):
    monkeypatch.delenv("TRC_USER_AGENT", raising=False)
    from backend import __version__
    ua = WX.user_agent()
    assert ua == f"TrailRunCoach/{__version__} (+https://github.com/vi000246/TrailRunCoach)"
    assert "@" not in ua
    monkeypatch.setenv("TRC_USER_AGENT", "MyCoach/1.0 ops@example.org")
    assert WX.user_agent() == "MyCoach/1.0 ops@example.org"


def test_every_weather_call_sends_the_user_agent(monkeypatch):
    import httpx
    seen = []

    class R:
        status_code, headers = 200, {"Expires": "x"}

        def json(self):
            return {"ok": 1}

    def fake_get(url, params=None, timeout=None, follow_redirects=None, headers=None):
        seen.append(headers)
        return R()
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.delenv("TRC_USER_AGENT", raising=False)
    assert WX._http_get(WX.OM_ARCHIVE, {}, 1.0) == {"ok": 1}
    assert WX._http_fetch(WX.MET_FORECAST, {}, 1.0, {"If-Modified-Since": "y"}) == (200, {"ok": 1}, {"expires": "x"})
    assert all(h["User-Agent"].startswith("TrailRunCoach/") for h in seen)
    assert seen[1]["If-Modified-Since"] == "y"


# ---- parsing --------------------------------------------------------------------------------------

def test_query_has_four_decimals_and_whole_metres():
    assert WX.met_params(23.470123456, 120.957891, 3952.4) == {"lat": "23.4701", "lon": "120.9579", "altitude": 3952}
    assert WX.met_params(25.0, 121.5, None) == {"lat": "25", "lon": "121.5"}


def test_rows_are_local_time_km_per_hour_and_the_following_period():
    js = met_js("2026-10-27", temp=lambda t: 10.0 + t.hour, rh=80.0, dew=7.0, wind=10.0, mm=0.4, pop=30.0)
    rows = WX.met_rows(js, WX.TZ)
    assert rows[0]["t"] == "2026-10-27T00:00" and rows[8]["temp_c"] == 18.0       # 00:00Z → 08:00 UTC+8
    assert rows[8]["wind_kmh"] == approx(36.0) and rows[8]["gust_kmh"] is None
    assert rows[8]["dew_c"] == 7.0 and rows[8]["rain"] == (1, 0.4, 30.0)
    rain = WX.met_rain_rows(rows, {dt.date(2026, 10, 27)})
    # MET's precipitation is of the FOLLOWING hour (Open-Meteo's was of the preceding one)
    assert rain[8] == {"start": "2026-10-27T08:00", "end": "2026-10-27T09:00", "pop_pct": 30.0, "mm": 0.4}
    wind = WX.met_wind_rows(rows, {dt.date(2026, 10, 27)})
    assert wind[8] == {"t": "2026-10-27T08:00", "temp_c": 18.0, "wind_kmh": approx(36.0), "gust_kmh": None,
                       "apparent_c": None}


def test_six_hourly_tail_gives_six_hour_rain_rows_without_double_counting():
    six = dt.datetime(2026, 10, 27, 12)
    js = met_js("2026-10-27", mm=1.0, six_hourly_from=six)
    rows = WX.met_rows(js, WX.TZ)
    rain = WX.met_rain_rows(rows, {dt.date(2026, 10, 27)})
    assert [r["start"][11:] for r in rain[-3:]] == ["11:00", "12:00", "18:00"]
    assert rain[-2]["end"] == "2026-10-27T18:00" and rain[-1]["end"] == "2026-10-28T00:00"
    assert sum(r["mm"] for r in rain) == approx(12 * 1.0 + 2 * 1.0)


def test_the_daytime_mean_needs_every_event_day():
    js = met_js("2026-10-28", temp=lambda t: 20.0 if 6 <= t.hour < 18 else 10.0, rh=70.0)
    rows = WX.met_rows(js, WX.TZ)
    c = WX.met_conditions(rows, {dt.date(2026, 10, 28)}, 900.0)
    assert c["temp_c"] == 20.0 and c["rh_pct"] == 70.0 and c["n"] == 12 and c["grid_elevation_m"] == 900.0
    # the series ends on the 28th: a two-day race gets no half mean
    assert WX.met_conditions(rows, {dt.date(2026, 10, 28), dt.date(2026, 10, 29)}) is None


def test_the_clock_outside_taiwan():
    d = dt.date(2026, 7, 10)
    assert WX.met_zone(23.47, 120.96, d) is WX.TZ
    alps = (45.92, 6.87)                                          # Chamonix: solar zone UTC+0, real UTC+2 in July
    lyon = (45.76, 4.84)
    # a runner living nearby (home cell ~170 km away): their zone, DST right
    assert WX.met_zone(*alps, d, ZoneInfo("Europe/Paris"), lyon).key == "Europe/Paris"
    # the same zone without a home cell is 2 h off the solar one: the solar zone (推估)
    assert WX.met_zone(*alps, d, ZoneInfo("Europe/Paris")).utcoffset(None) == dt.timedelta(0)
    far = WX.met_zone(*alps, d, ZoneInfo("Asia/Taipei"), (25.0, 121.5))     # a runner from Taiwan: solar
    assert far.utcoffset(None) == dt.timedelta(0)
    fuji = (35.36, 138.73)
    assert WX.met_zone(*fuji, d, None).utcoffset(None) == dt.timedelta(hours=9)
    assert WX.met_zone(*fuji, d, ZoneInfo("Asia/Tokyo")).key == "Asia/Tokyo"            # equals the solar zone
    assert WX.met_zone(*fuji, d, ZoneInfo("Asia/Taipei")).utcoffset(None) == dt.timedelta(hours=9)


# ---- the chain -----------------------------------------------------------------------------------

def _race(get, date, tmp_path, **kw):
    args = dict(date=date, lat=23.47, lon=120.957, elevation_m=3952.0, today=TODAY, key=None, get=get,
                cache_dir=tmp_path, use_cwa=False)
    args.update(kw)
    return WX.race_conditions(**args)


def test_seven_to_nine_days_out_comes_from_met_norway(tmp_path):
    seen = []
    race = TODAY + dt.timedelta(days=8)

    def get(url, params, timeout):
        seen.append((url, params))
        if url == WX.MET_FORECAST:
            return met_js(race, temp=5.0, rh=90.0, wind=12.0, mm=0.5, days=2, alt=3952)
        raise AssertionError(url)
    r = _race(get, race, tmp_path)
    assert r["provider"] == "met_norway" and r["label"] == "挪威氣象局預報"
    assert seen == [(WX.MET_FORECAST, {"lat": "23.47", "lon": "120.957", "altitude": 3952})]
    assert r["values"]["temp_c"] == 5.0 and r["detail"]["grid_elevation_m"] == 3952
    assert r["detail"]["attribution"] == "Weather data from MET Norway (CC BY 4.0)"
    assert r["detail"]["gusts"] is False
    assert len(r["hourly"]) == 48 and r["rain"][0]["mm"] == 0.5 and r["wind"][0]["gust_kmh"] is None
    assert all(s["src"] == "noaa" for s in r["sun"])               # MET has no sunrise
    assert not any("open-meteo" in u for u, _p in seen)


def test_ten_to_sixteen_days_out_takes_the_climatology_without_asking_met(tmp_path):
    seen = []

    def get(url, params, timeout):
        seen.append(url)
        if url == WX.OM_ARCHIVE:
            return archive(url, params)
        raise AssertionError(url)
    r = _race(get, TODAY + dt.timedelta(days=12), tmp_path)
    assert r["provider"] == "climatology"
    met = next(t for t in r["tried"] if t["provider"] == "met_norway")
    assert met["ok"] is False and "9" in met["reason"] and "歷史平均" in met["reason"]
    assert WX.MET_FORECAST not in seen


def test_met_down_falls_back_to_the_climatology_then_manual(tmp_path):
    def get(url, params, timeout):
        if url == WX.MET_FORECAST:
            raise RuntimeError("MET Norway HTTP 429")
        if url == WX.OM_ARCHIVE:
            return archive(url, params)
        raise AssertionError(url)
    r = _race(get, TODAY + dt.timedelta(days=3), tmp_path)
    assert r["provider"] == "climatology"
    assert {"provider": "met_norway", "ok": False, "reason": "MET Norway HTTP 429"} in r["tried"]

    def down(url, params, timeout):
        raise RuntimeError("HTTP 500")
    m = _race(down, TODAY + dt.timedelta(days=3), tmp_path / "b")
    assert m["provider"] == "manual" and m["values"] is None and m["sun"]


def test_an_answer_without_the_race_days_falls_through(tmp_path):
    race = TODAY + dt.timedelta(days=9)

    def get(url, params, timeout):
        if url == WX.MET_FORECAST:
            return met_js(TODAY, days=9)                          # ends the day before the race
        return archive(url, params)
    r = _race(get, race, tmp_path)
    assert r["provider"] == "climatology"
    assert {"provider": "met_norway", "ok": False, "reason": "回應沒有這些日期"} in r["tried"]


def test_outside_taiwan_the_rows_and_the_sun_use_the_places_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(WX, "_athlete_clock", lambda: (ZoneInfo("Asia/Taipei"), (25.0, 121.5)))
    race = TODAY + dt.timedelta(days=2)

    def get(url, params, timeout):
        return met_js(race, temp=lambda t: 30.0 if t.hour == 12 else 10.0, rh=60.0, tz_h=9, days=2,
                      lat=35.36, lon=138.73)
    r = _race(get, race, tmp_path, lat=35.36, lon=138.73, elevation_m=None)   # 富士山, UTC+9
    assert r["provider"] == "met_norway"
    assert next(h for h in r["hourly"] if h["temp_c"] == 30.0)["t"] == f"{race}T12:00"
    rise = dt.datetime.fromisoformat(r["sun"][0]["sunrise"])
    assert 5 <= rise.hour <= 6                                     # Fuji in late October ≈ 05:50 JST


# ---- MET's caching rules -------------------------------------------------------------------------

class Fetch:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, params, timeout, headers):
        self.calls.append(dict(headers))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


T0 = 1_800_000_000.0
EXP = "Fri, 15 Jan 2027 08:30:00 GMT"                              # T0 + 30 min (T0 = 2027-01-15 08:00:00 UTC)
LM = "Fri, 15 Jan 2027 07:55:00 GMT"


def test_expires_is_kept_and_if_modified_since_sent_after_it():
    assert WX._http_date(EXP) == T0 + 1800
    p = {"lat": "24", "lon": "121"}
    f = Fetch((200, {"v": 1}, {"expires": EXP, "last-modified": LM}), (304, None, {"expires": "Fri, 15 Jan 2027 09:00:00 GMT"}),
              (200, {"v": 2}, {}))
    assert WX.met_forecast(p, fetch=f, now=T0) == {"v": 1}
    assert WX.met_forecast(p, fetch=f, now=T0 + 1799) == {"v": 1} and len(f.calls) == 1     # before Expires: no call
    assert WX.met_forecast(p, fetch=f, now=T0 + 1801) == {"v": 1}                           # 304: the old body
    assert f.calls[1] == {"If-Modified-Since": LM}
    assert WX.met_forecast(p, fetch=f, now=T0 + 3599) == {"v": 1} and len(f.calls) == 2     # the new Expires
    assert WX.met_forecast(p, fetch=f, now=T0 + 3601) == {"v": 2}
    assert f.calls[2] == {"If-Modified-Since": LM}                                         # still the last one seen


def test_without_expires_the_default_ttl_and_an_old_expires_waits_a_minute():
    p = {"lat": "24", "lon": "122"}
    f = Fetch((200, {"v": 1}, {}), (200, {"v": 2}, {"expires": "Thu, 01 Jan 2026 00:00:00 GMT"}), (200, {"v": 3}, {}))
    WX.met_forecast(p, fetch=f, now=T0)
    assert WX.met_forecast(p, fetch=f, now=T0 + WX.MET_DEFAULT_TTL_S - 1) == {"v": 1}
    assert WX.met_forecast(p, fetch=f, now=T0 + WX.MET_DEFAULT_TTL_S + 1) == {"v": 2}
    assert WX.met_forecast(p, fetch=f, now=T0 + WX.MET_DEFAULT_TTL_S + 30) == {"v": 2}    # clock skew: ≥ 1 min
    assert WX.met_forecast(p, fetch=f, now=T0 + WX.MET_DEFAULT_TTL_S + 62) == {"v": 3}


def test_met_failing_uses_a_recent_answer_else_raises():
    p = {"lat": "24", "lon": "123"}
    f = Fetch((200, {"v": 1}, {"expires": EXP}), (429, None, {}), RuntimeError("timeout"), (503, None, {}))
    WX.met_forecast(p, fetch=f, now=T0)
    assert WX.met_forecast(p, fetch=f, now=T0 + 3600) == {"v": 1}                          # 429: stale is fine
    assert WX.met_forecast(p, fetch=f, now=T0 + 7200) == {"v": 1}                          # timeout: same
    with pytest.raises(RuntimeError, match="MET Norway HTTP 503"):
        WX.met_forecast(p, fetch=f, now=T0 + 1800 + WX.MET_STALE_MAX_S + 1)                 # too old
    with pytest.raises(RuntimeError, match="HTTP 500"):
        WX.met_forecast({"lat": "1", "lon": "2"}, fetch=Fetch((500, None, {})), now=T0)     # nothing to fall back on


def test_requests_are_spaced_twenty_a_second():
    t = [100.0]
    slept = []

    def sleep(s):
        slept.append(s)
        t[0] += s
    WX._MET_NEXT[0] = 0.0
    for _ in range(3):
        WX._met_wait(clock=lambda: t[0], sleep=sleep)
    assert slept == [approx(0.05), approx(0.05)]
    t[0] += 1.0
    assert WX._met_wait(clock=lambda: t[0], sleep=sleep) == 0.0


def test_the_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(WX, "MET_CACHE_MAX", 3)
    for i in range(5):
        WX.met_forecast({"lat": str(i), "lon": "0"}, fetch=Fetch((200, {"v": i}, {})), now=T0)
    assert len(WX._MET_CACHE) == 3
    assert (("lat", "0"), ("lon", "0")) not in WX._MET_CACHE


# ---- 冷風 without gusts ---------------------------------------------------------------------------

def _pts(n=3, start=dt.datetime(2099, 1, 10, 6)):
    return [{"i": i + 1, "start_km": 2.0 * i, "end_km": 2.0 * (i + 1), "z": None, "temp_c": None,
             "begin": start + dt.timedelta(minutes=20 * i), "end": start + dt.timedelta(minutes=20 * (i + 1))}
            for i in range(n)]


def test_cold_wind_details_say_when_there_are_no_gusts():
    rows = [{"t": f"2099-01-10T{h:02d}:00", "temp_c": -15.0, "wind_kmh": 30.0, "gust_kmh": None} for h in range(24)]
    r = CD.cold_wind(_pts(), rows, date="2099-01-10")
    assert r["alert"] and r["level"] == "cold" and r["max_gust_kmh"] is None
    txt = " ".join(CD.attention(r)["details"])
    assert "這份預報沒有陣風，只用平均風速判斷。" in txt and "陣風 ≥ 50" not in txt
    gusty = [{**x, "gust_kmh": 60.0} for x in rows]
    g = CD.cold_wind(_pts(), gusty, date="2099-01-10")
    txt = " ".join(CD.attention(g)["details"])
    assert "陣風 ≥ 50 km/h 也提醒（推估）。" in txt and "沒有陣風" not in txt
