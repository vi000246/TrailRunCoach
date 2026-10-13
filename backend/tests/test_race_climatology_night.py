"""SP-257 (docs/research/night-and-sleep.md §3 G5 / §4.2 單 4): with no forecast the race-day To is
the climatology's daytime (06:00–17:59) mean; a race that runs outside those hours now gets a single
To over the hours it is actually on course — the daytime hours keep the daytime mean, the others
take the climatology's own hour (its 24-hour profile, the /weather hourly rows). Road / trail: one
window from the start to the predicted finish; 百岳: one per day from the start time. A daytime
race, a plan without a start time and a forecast's To are unchanged. Synthetic rows and the fake
athlete of test_racepower_v2 only; no network."""
import datetime as dt
import json
import re
from pathlib import Path

import pytest

from backend.engine.racepower import env as ENV
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import weather as WX
from backend.tests.test_baiyue_capacity import _synthetic_cap
from backend.tests.test_race_calculator import _upload
from backend.tests.test_racepower_v2 import RE0, client  # noqa: F401  (fixture)

approx = pytest.approx
DAY = "2099-01-10"
D = lambda d, h, m=0: dt.datetime(2099, 1, d, h, m)   # noqa: E731
NIGHT = set(range(0, 6)) | set(range(18, 24))
# the climatology's 24-hour profile: cold nights, warm days
PROFILE = [{"hour": h, "temp_c": 5.0 if h in NIGHT else 20.0, "rh_pct": 95.0 if h in NIGHT else 60.0, "n": 1}
           for h in range(24)]
DAY_T, DAY_RH = 19.6, 70.0        # the page's To: the climatology's daytime mean as /weather gave it


def rows(days=1, profile=PROFILE):
    """The /weather `hourly` rows of the climatology: the profile on the event days and the day after."""
    return WX.climatology_hourly_rows(profile, {dt.date(2099, 1, 10) + dt.timedelta(days=i) for i in range(days)})


# ---- weather.climatology_to --------------------------------------------------------------------

def test_a_daytime_window_keeps_the_daytime_mean():
    assert WX.climatology_to(rows(), [(D(10, 6), D(10, 18))], DAY_T, DAY_RH) is None
    assert WX.climatology_to(rows(2), [(D(10, 8), D(10, 12, 30)), (D(11, 7), D(11, 15))], DAY_T, DAY_RH) is None


def test_a_window_across_midnight_takes_the_night_hours():
    c = WX.climatology_to(rows(), [(D(10, 20), D(11, 4))], DAY_T, DAY_RH)
    assert c["temp_c"] == approx(5.0) and c["rh_pct"] == approx(95.0, abs=0.01)
    assert c["hours"] == approx(8.0) and c["night_h"] == approx(8.0)
    assert c["day_temp_c"] == DAY_T and c["day_rh_pct"] == DAY_RH
    assert c["dew_c"] == approx(ENV.dew_point(c["temp_c"], c["rh_pct"])["dew_c"])


def test_daytime_hours_keep_the_daytime_mean_and_night_hours_the_profile():
    # 14:00–18:00 at the daytime mean (not the profile's 20 °C), 18:00–22:00 at the profile's 5 °C
    c = WX.climatology_to(rows(), [(D(10, 14), D(10, 22))], DAY_T, DAY_RH)
    assert c["temp_c"] == approx((4 * DAY_T + 4 * 5.0) / 8)
    assert c["rh_pct"] == approx((4 * DAY_RH + 4 * 95.0) / 8, abs=0.01)
    assert c["hours"] == approx(8.0) and c["night_h"] == approx(4.0)


def test_the_profile_is_linear_between_hours():
    prof = [{**p, "temp_c": {19: 10.0, 20: 12.0, 21: 14.0}.get(p["hour"], 10.0), "rh_pct": 80.0} for p in PROFILE]
    c = WX.climatology_to(rows(profile=prof), [(D(10, 19), D(10, 21))], DAY_T, DAY_RH)
    assert c["temp_c"] == approx(12.0)


def test_several_days_and_days_past_the_rows_read_the_same_clock_hours():
    # each pre-dawn window: 03–05 at 5 °C, 05–06 from 5 to the profile's 06:00 (20 °C), 06–09 the daytime mean
    one = WX.climatology_to(rows(), [(D(10, 3), D(10, 9))], DAY_T, DAY_RH)
    assert one["temp_c"] == approx((2 * 5.0 + 12.5 + 3 * DAY_T) / 6)
    assert one["night_h"] == approx(3.0) and one["hours"] == approx(6.0)
    many = WX.climatology_to(rows(), [(D(10, 3), D(10, 9)), (D(11, 3), D(11, 9)), (D(14, 3), D(14, 9))],
                             DAY_T, DAY_RH)
    assert many["temp_c"] == approx(one["temp_c"]) and many["rh_pct"] == approx(one["rh_pct"])
    assert many["night_h"] == approx(9.0) and many["hours"] == approx(18.0)
    # 14 Jan is past the rows (10–11 Jan): the profile repeats each day, so its clock hour is read
    late = WX.climatology_to(rows(), [(D(14, 3), D(14, 9))], DAY_T, DAY_RH)
    assert late["temp_c"] == approx(one["temp_c"])


def test_no_rows_no_windows_or_a_missing_hour_change_nothing():
    assert WX.climatology_to([], [(D(10, 20), D(11, 4))], DAY_T, DAY_RH) is None
    assert WX.climatology_to(rows(), [], DAY_T, DAY_RH) is None
    gap = [r for r in rows() if not r["t"].endswith("T03:00")]
    assert WX.climatology_to(gap, [(D(10, 20), D(11, 4))], DAY_T, DAY_RH) is None


# ---- the plan: road / trail ---------------------------------------------------------------------

def _plan(client, kind, **kw):   # noqa: F811
    body = {"type": kind, "course": {"course_id": _upload(client)["course_id"]}, "date": DAY, "start_time": "23:00",
            "env_to": {"altitude_m": 500.0, "temp_c": DAY_T, "rh_pct": DAY_RH}, "hourly": rows(), **kw}
    r = client.post("/api/v1/racepower/plan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _finish(p, start):
    s = p["summary"]
    return start + dt.timedelta(seconds=(s.get("time_total_s") or s["time_s"]) + (s.get("stops_s") or 0.0))


@pytest.mark.parametrize("kind", ["road", "trail"])
def test_a_race_across_midnight_gets_the_night_to(client, kind):   # noqa: F811
    p = _plan(client, kind, start_time="23:30", to_provider="climatology")
    end = _finish(p, D(10, 23, 30))
    assert end.date() == dt.date(2099, 1, 11)                       # finishes after midnight
    want = WX.climatology_to(rows(), [(D(10, 23, 30), end)], DAY_T, DAY_RH)
    c = p["to_clim"]
    assert c["temp_c"] == approx(want["temp_c"], abs=0.05) == approx(5.0)
    assert c["hours"] == approx(want["hours"], abs=0.02) and c["night_h"] == approx(c["hours"])
    assert c["day_temp_c"] == DAY_T
    # the single To of the whole plan (v1, the env chips, M) is the widened one
    assert p["env"]["to"]["temp_c"] == approx(c["temp_c"]) and p["env"]["to"]["rh_pct"] == approx(c["rh_pct"])
    # SP-482: hint objects; the climatology line is the 環境卡's RC29
    assert any(f"{c['temp_c']:.1f} °C" in w["text"] and f"{DAY_T:.1f} °C" in w["text"] and w["id"] == "RC29"
               for w in p["warnings"])
    # exactly the plan that To typed in gives
    typed = _plan(client, kind, start_time="23:30",
                  env_to={"altitude_m": 500.0, "temp_c": c["temp_c"], "rh_pct": c["rh_pct"]})
    assert typed["to_clim"] is None
    assert p["summary"]["time_s"] == approx(typed["summary"]["time_s"])
    assert p["v1"]["result"]["time_s"] == approx(typed["v1"]["result"]["time_s"])
    assert [s["temp_c"] for s in p["segments"]] == approx([s["temp_c"] for s in typed["segments"]])


@pytest.mark.parametrize("kind", ["road", "trail", "baiyue"])
def test_a_daytime_race_is_unchanged(client, kind):   # noqa: F811
    base = _plan(client, kind, start_time="09:00")
    clim = _plan(client, kind, start_time="09:00", to_provider="climatology")
    assert clim["to_clim"] is None and base["to_clim"] is None
    assert clim["env"]["to"]["temp_c"] == DAY_T
    assert clim["summary"]["time_s"] == base["summary"]["time_s"]
    assert [s["t"] for s in clim["segments"]] == [s["t"] for s in base["segments"]]
    assert [s.get("temp_c") for s in clim["segments"]] == [s.get("temp_c") for s in base["segments"]]
    assert clim["warnings"] == base["warnings"]


def test_without_a_start_time_or_with_a_forecast_nothing_changes(client):   # noqa: F811
    for p in (_plan(client, "trail", start_time=None, to_provider="climatology"),
              _plan(client, "trail", to_provider="open_meteo"),
              _plan(client, "trail", to_provider="climatology", hourly=None)):
        assert p["to_clim"] is None and p["env"]["to"]["temp_c"] == DAY_T


def test_hourly_heat_off_uses_the_widened_single_value_on_every_segment(client):   # noqa: F811
    p = _plan(client, "trail", to_provider="climatology", hourly_heat=False)
    assert p["to_clim"]["temp_c"] == approx(5.0)
    assert all(s["heat_src"] == "single" and s["temp_c"] == approx(5.0) for s in p["segments"])
    assert p["fuel"]["water"]["t_mean"] == approx(5.0)              # 補給 reads the same temperature


def test_a_two_day_race_through_the_night_and_into_the_next_day(client):   # noqa: F811
    body = {"type": "trail", "distance_km": 160, "gain_m": 9000, "days": 2, "date": DAY, "start_time": "12:00",
            "course": {"manual": {"km": 160, "gain": 9000, "split": "km"}}, "hourly": rows(2),
            "env_to": {"altitude_m": 500.0, "temp_c": DAY_T, "rh_pct": DAY_RH}, "to_provider": "climatology"}
    r = client.post("/api/v1/racepower/plan", json=body)
    assert r.status_code == 200, r.text
    p = r.json()
    end = _finish(p, D(10, 12))
    assert end > D(11, 6)                                            # a whole night and the next morning
    want = WX.climatology_to(rows(2), [(D(10, 12), end)], DAY_T, DAY_RH)
    c = p["to_clim"]
    assert c["temp_c"] == approx(want["temp_c"], abs=0.05) and 5.0 < c["temp_c"] < DAY_T
    assert c["night_h"] == approx(12.0, abs=0.02) and c["hours"] == approx(want["hours"], abs=0.05)
    assert p["env"]["to"]["temp_c"] == approx(c["temp_c"])


# ---- the plan: 百岳 (one temperature for the trip, lapsed to each segment) -----------------------------

@pytest.fixture()
def cap_client(client, monkeypatch):   # noqa: F811
    from backend.api import racepower as RP
    cap = _synthetic_cap()
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": [], "walk_capacity": cap})
    return client


def test_baiyue_days_starting_before_dawn_cover_the_dark_hours(cap_client):
    kw = {"days": 2, "day_splits_km": [6.0], "start_time": "05:00", "heat_ref_alt_m": 500.0, "hourly": rows(2)}
    base = _plan(cap_client, "baiyue", **kw)
    p = _plan(cap_client, "baiyue", to_provider="climatology", **kw)
    assert p["summary"]["total_method"] == "capacity" and [d["day"] for d in p["days"]] == [1, 2]
    # each day on the trail from 05:00 for its clock time (moving time ÷ the moving ratio)
    wins = [(D(10 + n, 5), D(10 + n, 5) + dt.timedelta(hours=d["clock_h"])) for n, d in enumerate(p["days"])]
    assert all(b > D(10 + n, 6) for n, (_a, b) in enumerate(wins))   # every day runs past dawn
    want = WX.climatology_to(rows(2), wins, DAY_T, DAY_RH)
    c = p["to_clim"]
    assert c["temp_c"] == approx(want["temp_c"], abs=0.05) and c["temp_c"] < DAY_T
    assert c["night_h"] == approx(2.0, abs=0.02)                    # 05:00–06:00 on both days
    assert c["hours"] == approx(sum(d["clock_h"] for d in p["days"]), abs=0.02)
    to = p["env"]["to"]["temp_c"]
    assert to == approx(c["temp_c"])
    # the trip's one temperature, lapsed from the weather point to each segment
    for s in p["segments"]:
        assert s["temp_c"] == approx(ENV.segment_temp(to, 500.0, s["z_mean"]))
    assert all(a["temp_c"] > b["temp_c"] for a, b in zip(base["segments"], p["segments"]))


# ---- the page --------------------------------------------------------------------------------------

def test_the_page_sends_the_provider_and_shows_the_widened_to():
    static = Path(__file__).resolve().parents[1] / "static"
    page = (static / "racepower.html").read_text("utf-8")
    assert "to_provider:" in page and 'id="to-clim"' in page and 'T("env.clim_night"' in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((static / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert {"{t}", "{day}", "{n}", "{h}"} <= set(re.findall(r"\{\w+\}", cat["env.clim_night"]))
