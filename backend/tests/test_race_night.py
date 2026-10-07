"""SP-254: segments run in the dark marked 夜間 (sunrise / sunset from /weather or NOAA's formula),
an optional night slowdown (default 0 %, 5 / 10 / 15 %, 推估) only on the dark part, and the
headlamp / warmth hint merged into the one attention line with 冷風 (SP-305). Fakes only."""
import datetime as dt

import pytest

from backend.engine import race_feasibility as F
from backend.engine.racepower import cold as CD
from backend.engine.racepower import night as NI
from backend.engine.racepower import weather as WX
from backend.tests.test_race_calculator import _upload
from backend.tests.test_race_cold_wind import _rows, _wc_temp
from backend.tests.test_racepower_v2 import client  # noqa: F401  (fixture)

DAY = "2099-01-10"
SUN = [{"date": "2099-01-10", "sunrise": "2099-01-10T06:30", "sunset": "2099-01-10T17:30"},
       {"date": "2099-01-11", "sunrise": "2099-01-11T06:30", "sunset": "2099-01-11T17:30"}]
D = lambda d, h, m=0: dt.datetime(2099, 1, d, h, m)   # noqa: E731


# ---- sunrise / sunset -------------------------------------------------------------------------

def _min(t: dt.datetime) -> int:
    return t.hour * 60 + t.minute


@pytest.mark.parametrize("date, rise, sset", [
    # Taipei (25.04 N, 121.51 E); api.sunrise-sunset.org (read 2026-10-07), UTC+8:
    # 2026-06-21 05:03:35 / 18:47:50, 2026-12-21 06:33:10 / 17:10:32
    (dt.date(2026, 6, 21), 5 * 60 + 4, 18 * 60 + 48),
    (dt.date(2026, 12, 21), 6 * 60 + 33, 17 * 60 + 11),
])
def test_noaa_formula_within_a_few_minutes(date, rise, sset):
    r, s = WX.sun_times(25.04, 121.51, date)
    assert abs(_min(r) - rise) <= 4 and abs(_min(s) - sset) <= 4


def test_sun_rows_prefer_open_meteo_and_cover_the_next_day():
    js = {"utc_offset_seconds": 28800, "daily": {"time": ["2026-10-07"], "sunrise": ["2026-10-07T05:51"],
                                                  "sunset": ["2026-10-07T17:34"]}}
    rows = WX.sun_rows(25.0, 121.5, {dt.date(2026, 10, 7)}, js)
    assert rows[0] == {"date": "2026-10-07", "sunrise": "2026-10-07T05:51", "sunset": "2026-10-07T17:34",
                       "src": "open_meteo"}
    assert rows[1]["date"] == "2026-10-08" and rows[1]["src"] == "noaa"
    assert WX.sun_rows(None, 121.5, {dt.date(2026, 10, 7)}) is None


def test_open_meteo_asks_for_sunrise_and_sunset(tmp_path):
    seen = {}

    def get(url, params, timeout):
        seen.update(params)
        times = [f"2026-10-07T{h:02d}:00" for h in range(24)]
        return {"elevation": 300, "utc_offset_seconds": 28800,
                "hourly": {"time": times, "temperature_2m": [20.0] * 24, "relative_humidity_2m": [80.0] * 24},
                "daily": {"time": ["2026-10-07", "2026-10-08"], "sunrise": ["2026-10-07T05:51", "2026-10-08T05:52"],
                          "sunset": ["2026-10-07T17:34", "2026-10-08T17:33"]}}
    r = WX.race_conditions(date=dt.date(2026, 10, 7), lat=25.0, lon=121.5, elevation_m=300,
                           today=dt.date(2026, 10, 6), key=None, get=get, cache_dir=tmp_path, use_cwa=False)
    assert seen["daily"] == "sunrise,sunset"
    assert [x["sunset"] for x in r["sun"]] == ["2026-10-07T17:34", "2026-10-08T17:33"]


# ---- dark spans -------------------------------------------------------------------------------

def test_dark_spans_and_shares():
    sp = NI.dark_spans(SUN)
    assert sp[0] == (dt.datetime.min, D(10, 6, 30)) and sp[1] == (D(10, 17, 30), D(11, 6, 30))
    assert sp[-1][0] == D(11, 17, 30)
    assert NI.dark_share(D(10, 17, 0), D(10, 18, 0), sp) == pytest.approx(0.5)
    assert NI.dark_share(D(10, 4, 0), D(10, 5, 0), sp) == 1.0          # pre-dawn start
    assert NI.dark_share(D(10, 10, 0), D(10, 11, 0), sp) == 0.0
    assert NI.dark_spans(None) == [] and NI.dark_spans([{"date": "x"}]) == []


# ---- the plan ---------------------------------------------------------------------------------

def _plan(client, kind, **kw):   # noqa: F811
    cid = _upload(client)["course_id"]
    body = {"type": kind, "course": {"course_id": cid}, "date": DAY, "start_time": "16:30", **kw}
    r = client.post("/api/v1/racepower/plan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("kind", ["road", "trail", "baiyue"])
def test_zero_percent_changes_nothing(client, kind):   # noqa: F811
    base = _plan(client, kind)
    night = _plan(client, kind, sun=SUN)
    zero = _plan(client, kind, sun=SUN, night_slow_pct=0)
    for p in (night, zero):
        assert p["summary"]["time_s"] == base["summary"]["time_s"]
        assert [s["t"] for s in p["segments"]] == [s["t"] for s in base["segments"]]
        assert [s["eta"] for s in p["segments"]] == [s["eta"] for s in base["segments"]]
        assert p["summary"].get("finish_eta") == base["summary"].get("finish_eta")
    assert night["night"]["applied"] is False and night["night"]["slow_pct"] == 0


@pytest.mark.parametrize("kind", ["trail", "baiyue"])
def test_ten_percent_only_lengthens_the_dark_part(client, kind):   # noqa: F811
    base = _plan(client, kind, sun=SUN)
    slow = _plan(client, kind, sun=SUN, night_slow_pct=10)
    assert any(s["night"] for s in base["segments"]) and not all(s["night"] for s in base["segments"])
    for a, b in zip(base["segments"], slow["segments"]):
        if b["dark_share"] == 0:
            assert b["t"] == pytest.approx(a["t"])
        else:
            # the shares settle by fixed point (cumulative times within NI.TOL_S)
            assert b["t"] == pytest.approx(a["t"] * (1 + 0.10 * b["dark_share"]), abs=NI.TOL_S)
            assert b["t"] > a["t"]
    added = sum(s["t"] for s in slow["segments"]) - sum(s["t"] for s in base["segments"])
    assert slow["night"]["applied"] is True and slow["night"]["added_s"] == pytest.approx(added)
    assert slow["summary"]["time_s"] == pytest.approx(base["summary"]["time_s"] + added)
    assert slow["segments"][-1]["eta"] != base["segments"][-1]["eta"]
    assert slow["segments"][-1]["cum_s"] == pytest.approx(slow["summary"]["time_s"])
    rows = slow["chart_rows"]
    assert [r["night"] for r in rows] == [s["night"] for s in slow["segments"]]


def test_target_time_mode_is_not_slowed(client):   # noqa: F811
    p = _plan(client, "trail", sun=SUN, night_slow_pct=15, mode="time", target_time_s=2 * 3600)
    assert p["night"]["applied"] is False and p["summary"]["time_s"] == pytest.approx(2 * 3600, abs=2)


def test_without_a_start_time_nothing_is_marked(client):   # noqa: F811
    p = _plan(client, "trail", sun=SUN, start_time=None, night_slow_pct=10)
    assert p["night"] is None and all(r["night"] is None for r in p["chart_rows"])


def test_gpx_course_without_weather_uses_the_formula_at_its_start(client):   # noqa: F811
    p = _plan(client, "trail", start_time="18:30")                 # the test GPX starts at 24 N, 121 E
    assert p["night"] is not None and all(s["night"] for s in p["segments"])
    assert p["night"]["sun"][0]["src"] == "noaa"


# ---- the hint: merged with 冷風, trail / 百岳 only --------------------------------------------------

def test_night_hint_on_trail_not_on_road(client):   # noqa: F811
    t = _plan(client, "trail", sun=SUN)
    r = _plan(client, "road", sun=SUN, start_time="17:00")
    assert t["attention"]["kinds"] == ["night"]
    assert t["attention"]["line"].startswith("夜間：km ") and "頭燈（備用電池）" in t["attention"]["line"]
    assert any("Brager 2020 的 35.9 % 含疲勞" in d for d in t["attention"]["details"])
    assert any(s["night"] for s in r["segments"]) and r["attention"] is None


def test_night_and_cold_wind_are_one_line(client):   # noqa: F811
    p = _plan(client, "trail", sun=SUN, wind=_rows(_wc_temp(-12.0, 30.0), 30.0))
    a = p["attention"]
    assert a["kinds"][:2] == ["night", "cold_wind"] and a["line"].startswith("夜間＋冷風")
    assert a["line"].count("保暖層") == 1 and a["line"].count("→") == 1


def test_attention_merges_all_three_once():
    night = {"n": 2, "ranges": [{"start_km": 10.0, "end_km": 14.0, "from": "17:35", "to": "18:40"}],
             "applied": False}
    cold = {"alert": True, "level": "cold", "min_wc_c": -12.0, "ranges": [
        {"start_km": 12.0, "end_km": 14.0, "from": "18:00", "to": "18:40", "min_wc_c": -12.0, "max_gust_kmh": None}]}
    hypo = {"alert": True, "min_temp_c": -3.0, "wet": True, "windy": True, "min_wc_c": -12.0}
    a = CD.attention(cold, hypo, night)
    assert a["kinds"] == ["night", "cold_wind", "hypothermia"]
    assert a["line"].startswith("夜間＋冷風＋失溫風險：")
    for g in ("頭燈（備用電池）", "防水防風外套", "保暖層", "手套帽子", "熱食熱飲"):
        assert a["line"].count(g) == 1
    assert "防風外套、" not in a["line"].replace("防水防風外套", "")


# ---- the race card's cutoff follows a saved slowdown -------------------------------------------

def test_calc_night_from_saved_inputs():
    assert F.calc_night({}) == {}
    assert F.calc_night({"form": {"nightslow": "0", "start": "05:00"}}) == {}
    assert F.calc_night({"form": {"nightslow": "10"}}) == {}                      # no start time
    got = F.calc_night({"form": {"nightslow": "10", "start": "22:00"}, "wx": {"sun": SUN}})
    assert got == {"start_time": "22:00", "night_slow_pct": 10.0, "sun": SUN}


def test_trail_finish_passes_the_night_slowdown(monkeypatch):
    seen = {}
    monkeypatch.setattr(F, "calc_inputs", lambda e: {"form": {"nightslow": "10", "start": "22:00"}})

    def plan(body):
        seen["body"] = body
        return {"summary": {"total_method": "trail_hr", "time_s": 6.0 * 3600, "nonmoving": None}}

    class Ev:
        id, date, cutoff_hours = "e1", "2099-01-10", 8.0
    F.trail_finish(Ev(), {"totals": {"km": 30.0, "gain_m": 2000.0, "loss_m": 1900.0}, "days": [],
                          "filename": "race.gpx"}, plan_fn=plan)
    b = seen["body"]
    assert b.start_time == "22:00" and b.night_slow_pct == 10.0
