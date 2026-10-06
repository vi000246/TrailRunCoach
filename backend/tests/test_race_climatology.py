"""SP-210: the race-day climatology past the forecast horizon
(backend/engine/racepower/weather.py, fetch_climatology): the month centred
on the race date in each of the last 10 years, the mean of three
reanalyses, a 24-hour profile for per-segment heat, and a disk cache.
Synthetic archive answers only; no network, no real data."""
import datetime as dt
import threading

import pytest

from backend.engine.racepower import env as ENV
from backend.engine.racepower import weather as WX

approx = pytest.approx
TODAY = dt.date(2026, 10, 6)
RACE = dt.date(2026, 12, 20)                 # past the 16-day forecast


def _days(params):
    lo = dt.date.fromisoformat(params["start_date"])
    n = (dt.date.fromisoformat(params["end_date"]) - lo).days + 1
    return [lo + dt.timedelta(days=i) for i in range(n)]


def archive(temp_of=lambda d, h: 10.0 + h, rh=80.0, models=WX.CLIM_MODELS, offsets=None, elevation=None,
            fail_years=(), calls=None):
    """A fake archive: per-model columns (`key_<model>`), model m's temperature
    = temp_of(day, hour) + offsets[m]; it answers at the requested elevation
    unless `elevation` is given; years in `fail_years` raise."""
    offsets = offsets or {m: 0.0 for m in models}
    lock = threading.Lock()

    def get(url, params, timeout):
        assert url == WX.OM_ARCHIVE
        if calls is not None:
            with lock:
                calls.append(dict(params))
        days = _days(params)
        if days[0].year in fail_years or days[-1].year in fail_years:
            raise RuntimeError("HTTP 500")
        times = [f"{d}T{h:02d}:00" for d in days for h in range(24)]
        hourly = {"time": times}
        for m in models:
            hourly[f"temperature_2m_{m}"] = [temp_of(d, h) + offsets[m] for d in days for h in range(24)]
            hourly[f"relative_humidity_2m_{m}"] = [rh] * len(times)
        return {"elevation": params.get("elevation") if elevation is None else elevation, "hourly": hourly}
    return get


def clim(tmp_path, get, date=RACE, days=1, elevation_m=1000.0, **kw):
    return WX.race_conditions(date=date, days=days, lat=23.4712, lon=120.9571, elevation_m=elevation_m,
                              today=TODAY, key=None, get=get, cache_dir=tmp_path, use_cwa=False, **kw)


# ---- the windows ----------------------------------------------------------------------

def test_windows_are_the_month_around_the_race_date_in_each_of_10_years():
    w = WX.clim_windows(RACE, 1, TODAY)
    assert len(w) == WX.CLIM_YEARS == 10
    assert w[0] == (dt.date(2025, 12, 5), dt.date(2026, 1, 4), True)          # 20 Dec ± 15 days
    assert w[-1][0] == dt.date(2016, 12, 5)                                      # 2016 … 2025
    assert all((hi - lo).days + 1 == 31 for lo, hi, _ in w)
    # a 2-day event adds its second day to each window
    assert WX.clim_windows(RACE, 2, TODAY)[0][1] == dt.date(2026, 1, 5)


def test_windows_stop_at_the_archives_newest_day_and_29_feb():
    # a race next autumn: last year's window runs into days the archive does not have yet
    w = WX.clim_windows(dt.date(2027, 10, 1), 1, TODAY)
    lo, hi, whole = w[0]
    assert hi == TODAY - dt.timedelta(days=WX.ARCHIVE_LAG_DAYS) and not whole
    assert all(x[2] for x in w[1:])
    # a window wholly in the future is dropped: a 2028 race has no 2027 window yet
    later = WX.clim_windows(dt.date(2028, 2, 29), 1, TODAY)
    assert len(later) == 9 and later[0][0].year == 2026
    # 29 Feb → 28 Feb in the other years
    leap = WX.clim_windows(dt.date(2024, 2, 29), 1, TODAY)
    assert leap[0][0] == dt.date(2023, 2, 28) - dt.timedelta(days=15)


# ---- three models, the request ----------------------------------------------------------

def test_merged_takes_the_plain_column_else_the_mean_of_the_models():
    h = {"temperature_2m_era5": [10.0, None, 4.0], "temperature_2m_era5_land": [12.0, 8.0, None],
         "temperature_2m_ecmwf_ifs": [14.0, None, None]}
    assert WX.merged(h, "temperature_2m") == [12.0, 8.0, 4.0]                # each hour over the models it has
    assert WX.merged({**h, "temperature_2m": [1.0]}, "temperature_2m") == [1.0]
    assert WX.merged(h, "dew_point_2m") is None


def test_climatology_is_the_mean_of_three_models_at_the_target_elevation(tmp_path):
    calls = []
    get = archive(offsets={"era5": 1.0, "era5_land": -2.0, "ecmwf_ifs": -2.0}, calls=calls)
    r = clim(tmp_path, get, elevation_m=3952.0)
    assert r["provider"] == "climatology"
    assert r["label"] == "近 10 年同月平均（Open-Meteo 歷史資料）"
    # daytime 06–17 of 10 + hour, minus the models' mean offset of 1 °C
    assert r["values"]["temp_c"] == approx(10.0 + 11.5 - 1.0, abs=0.02)
    assert r["values"]["rh_pct"] == approx(80.0)
    assert len(calls) == 10
    p = calls[0]
    assert p["models"] == "era5,era5_land,ecmwf_ifs" and p["elevation"] == 3950.0      # 10 m steps
    assert (p["latitude"], p["longitude"]) == (23.47, 120.96)
    assert set(p["hourly"].split(",")) == {"temperature_2m", "relative_humidity_2m", "dew_point_2m"}
    d = r["detail"]
    assert d["years"] == 10 and d["models"] == list(WX.CLIM_MODELS) and d["window_days"] == 31
    assert d["partial"] is False and d["cache"] == "miss"


def test_a_year_without_one_model_averages_the_others(tmp_path):
    # ECMWF IFS starts in 2017: an answer with two model columns still gives the mean of those two
    get = archive(models=("era5", "era5_land"), offsets={"era5": 2.0, "era5_land": 0.0})
    r = clim(tmp_path, get)
    assert r["values"]["temp_c"] == approx(10.0 + 11.5 + 1.0, abs=0.02)


# ---- the 24-hour profile ------------------------------------------------------------------

def test_profile_gives_hourly_rows_for_the_event_days_and_the_day_after(tmp_path):
    r = clim(tmp_path, archive(), days=2)
    rows = r["hourly"]
    assert len(rows) == 3 * 24
    assert rows[0]["t"] == "2026-12-20T00:00" and rows[-1]["t"] == "2026-12-22T23:00"
    assert rows[5]["temp_c"] == approx(15.0) and rows[14]["temp_c"] == approx(24.0)      # cool dawn, warm afternoon
    assert rows[24 + 14]["temp_c"] == approx(24.0)                                           # the same hours each day
    assert rows[14]["dew_c"] == approx(ENV.dew_point(24.0, 80.0)["dew_c"])
    # the planner reads a clock time from them
    at = WX.hourly_at(rows, dt.datetime(2026, 12, 20, 6, 30))
    assert at["temp_c"] == approx(16.5)


def test_profile_follows_the_daily_cycle_not_one_value():
    # the old climatology gave one daytime value; a 05:00 start now gets the cool hours
    days = {dt.date(2025, 12, 20)}
    js = {"hourly": {"time": [f"2025-12-20T{h:02d}:00" for h in range(24)],
                     "temperature_2m": [5.0 if h < 6 else 15.0 for h in range(24)],
                     "relative_humidity_2m": [90.0] * 24}}
    prof = WX.diurnal_profile([(js, days)])
    assert [p["temp_c"] for p in prof[:6]] == [5.0] * 6 and prof[12]["temp_c"] == 15.0
    # a missing clock hour → no profile (the plan then uses the single value)
    js["hourly"]["temperature_2m"][3] = None
    assert WX.diurnal_profile([(js, days)]) is None


def test_profile_moves_with_the_lapse_offset_and_keeps_rh(tmp_path):
    # an answer from 1000 m for a 2000 m target (a fake that ignores `elevation`): −6.5 °C on every row
    r = clim(tmp_path, archive(elevation=1000.0), elevation_m=2000.0)
    assert r["values"]["temp_c"] == approx(10.0 + 11.5 - 6.5)
    assert r["hourly"][14]["temp_c"] == approx(24.0 - 6.5) and r["hourly"][14]["rh_pct"] == approx(80.0)
    assert r["detail"]["lapse_corrected"] is True


# ---- cache and failures ---------------------------------------------------------------------

def test_cache_answers_the_second_time_without_calls(tmp_path):
    calls = []
    a = clim(tmp_path, archive(calls=calls))
    assert len(calls) == 10 and a["detail"]["cache"] == "miss"
    files = list((tmp_path / "climatology").glob("*.json"))
    assert len(files) == 1 and files[0].name == "23.47_120.96_1000_2026-12-20_1d.json"
    b = clim(tmp_path, archive(calls=calls))
    assert len(calls) == 10 and b["detail"]["cache"] == "hit"
    assert b["values"] == a["values"] and b["hourly"] == a["hourly"]


def test_failed_years_are_left_out_and_not_cached(tmp_path):
    calls = []
    r = clim(tmp_path, archive(fail_years={2021}, calls=calls))
    # 2020-12-05 … 2021-01-04 and 2021-12-05 … 2022-01-04 touch the failing year
    assert r["provider"] == "climatology" and r["detail"]["years"] == 8 and r["detail"]["partial"] is True
    assert not (tmp_path / "climatology").exists() or not list((tmp_path / "climatology").glob("*.json"))
    clim(tmp_path, archive(calls=calls))                     # retried, now complete → cached
    assert len(calls) == 20 and len(list((tmp_path / "climatology").glob("*.json"))) == 1


def test_no_year_answering_falls_back_to_manual(tmp_path):
    def down(url, params, timeout):
        raise RuntimeError("HTTP 503")
    r = clim(tmp_path, down)
    assert r["provider"] == "manual" and r["values"] is None and r["hourly"] is None
    t = r["tried"][-1]
    assert t["provider"] == "climatology" and not t["ok"] and "HTTP 503" in t["reason"]


def test_a_cache_file_of_an_older_version_is_ignored(tmp_path):
    clim(tmp_path, archive())
    f = next((tmp_path / "climatology").glob("*.json"))
    f.write_text('{"version": 1, "result": {"temp_c": 99}}', "utf-8")
    calls = []
    r = clim(tmp_path, archive(calls=calls))
    assert len(calls) == 10 and r["values"]["temp_c"] != 99
