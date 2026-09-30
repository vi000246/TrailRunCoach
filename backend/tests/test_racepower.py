"""Race-power calculator — docs/research/superpower-calculator.md §1.8 worked
example and the workbook's cached default values. No network, no real data."""
import datetime as dt
import json
import math

import pytest

from backend.engine.racepower import cp as CP
from backend.engine.racepower import env as ENV
from backend.engine.racepower import predict as PR
from backend.engine.racepower import re as RE
from backend.engine.racepower import riegel as R
from backend.engine.racepower import weather as WX

approx = pytest.approx
W, TTE, K, REV = 70.0, 3000.0, -0.07, 1.0
MARATHON = 42195.0


# ---- §1.1 environment ---------------------------------------------------------

def test_env_defaults_match_workbook_cache():
    s = ENV.side(ENV.Conditions(200, 12, 70))
    assert s["torr"] == approx(741.965324, abs=1e-6)
    assert s["altitude_factor"] == approx(0.9911949118, abs=1e-9)
    assert s["ita"] == approx(0.4738293568, abs=1e-9)
    assert s["dew_f"] == approx(44.04739671, abs=1e-7)
    assert s["heat_applies"] is False and s["heat_penalty_pct"] == 0
    m = ENV.multiplier(None, None)
    assert m["M"] == 1.0
    assert m["from"]["altitude_m"] == 200 and m["to"]["temp_c"] == 12 and m["to"]["rh_pct"] == 70


def test_env_blank_side_copies_other():
    a, b = ENV.resolve({"altitude_m": 1000, "temp_c": 20, "rh_pct": 50}, {"temp_c": 30})
    assert (b.altitude_m, b.temp_c, b.rh_pct) == (1000, 30, 50)
    assert ENV.multiplier({"altitude_m": 500, "temp_c": 5, "rh_pct": 40}, {})["M"] == 1.0


def test_env_worked_example():
    m = ENV.multiplier({"altitude_m": 0, "temp_c": 15, "rh_pct": 50},
                       {"altitude_m": 1500, "temp_c": 25, "rh_pct": 70})
    assert m["from"]["torr"] == approx(760.0003, abs=1e-3)
    assert m["to"]["torr"] == approx(638.1478, abs=1e-3)
    assert m["from"]["altitude_factor"] == approx(1.00000017, abs=1e-7)
    assert m["to"]["altitude_factor"] == approx(0.94599896, abs=1e-7)
    assert m["from"]["dew_f"] == approx(40.397, abs=1e-3)
    assert m["to"]["dew_f"] == approx(66.307, abs=1e-3)
    # x = dew °F + air °F = 66.307 + 77 (the spec's "125.3" is a typo; H below needs 143.3)
    assert m["to"]["heat_index_sum_f"] == approx(143.307, abs=1e-3)
    assert m["to"]["heat_penalty_pct"] == approx(3.48248, abs=1e-4)
    assert m["M"] == approx(0.911174, abs=1e-6)


def test_heat_penalty_clamped_and_rh_inverse():
    assert ENV.heat_penalty_pct(-5, 30) == 0
    for t, rh in ((10, 40), (25, 80), (32, 60)):
        dew = ENV.dew_point(t, rh)["dew_c"]
        assert ENV.rh_from_dew_point(t, dew) == approx(rh, abs=1e-6)


# ---- §1.5 CP ------------------------------------------------------------------

def test_cp_two_point_and_rating():
    f = CP.fit_cp([(180, 345), (720, 300)])
    assert f["cp"] == approx(285) and f["w_prime"] == approx(10800)
    r = CP.rwc_rating(f["w_prime"], W, "male", wind=False)
    assert r["j_per_kg"] == approx(154.2857, abs=1e-3)
    assert r["rating"] == "High"


def test_cp_three_point_fit_and_loglog():
    pts = [(180, 400), (360, 360), (1200, 318)]
    f = CP.fit_cp(pts)
    assert f["cp"] == approx(302.429, abs=1e-3)
    assert f["w_prime"] == approx(18991.09, abs=0.01)
    assert f["r2"] == approx(0.999905, abs=1e-6)
    ll = R.fit_loglog([p[0] for p in pts], [p[1] for p in pts])
    assert ll["k"] == approx(-0.118937, abs=1e-6)


def test_cp_validity_checks():
    good = [{"t": 180, "p": 400, "date": "2026-09-01"}, {"t": 1200, "p": 318, "date": "2026-09-10"}]
    assert all(c["ok"] for c in CP.validity(good))
    bad = [{"t": 400, "p": 300, "date": "2026-08-01"}, {"t": 600, "p": 310, "date": "2026-09-10"}]
    failed = {c["id"] for c in CP.validity(bad) if not c["ok"]}
    assert failed == {"short", "long", "span", "falling", "dates"}
    env = CP.validity(bad, envelope=True)
    assert next(c for c in env if c["id"] == "dates")["level"] == "warn"


@pytest.mark.parametrize("wp,weight,sex,wind,expect", [
    (4000, 70, "male", False, "Too Low"), (5000, 70, "male", False, "Low"),
    (8000, 70, "male", False, "Medium"), (11000, 70, "male", False, "Too High"),
    (8000, 60, "female", False, "Too High"), (8000, 70, "male", True, "Medium"),
])
def test_rwc_bands(wp, weight, sex, wind, expect):
    assert CP.rwc_rating(wp, weight, sex, wind)["rating"] == expect


# ---- §1.2 Riegel tasks ---------------------------------------------------------

def test_task8_task7_task12_task14_task15():
    m = ENV.multiplier({"altitude_m": 0, "temp_c": 15, "rh_pct": 50},
                       {"altitude_m": 1500, "temp_c": 25, "rh_pct": 70})["M"]
    p8 = R.power_from_cp(300, 3 * 3600, TTE, K)
    assert p8 / 300 == approx(0.914237, abs=1e-6)
    assert p8 == approx(274.271, abs=1e-3)
    assert p8 * m == approx(249.909, abs=1e-3)
    assert R.cp_from_prior(280, 5400, TTE, K) == approx(291.761, abs=1e-3)
    p14 = PR.power_for_time(MARATHON, 3 * 3600, REV, W)
    assert p14 == approx(273.486, abs=1e-3)
    imp = PR.improvement_needed(MARATHON, 3 * 3600, REV, W, 300, TTE, K)
    assert imp["cp_required"] == approx(299.141, abs=1e-3)
    assert imp["pct_change"] * 100 == approx(-0.286, abs=1e-3)
    assert PR.time_for_power(MARATHON, 250, REV, W) == approx(11814.6, abs=0.05)


def test_task11_converges():
    s = PR.solve_riegel_re(MARATHON, 300, TTE, K, REV, W)
    assert s["time_s"] == approx(10766.75, abs=0.05)
    assert s["power"] == approx(274.330, abs=1e-3)
    # closed form: T^(1+k) = D·W·TTE^k / (RE·CP)
    exact = (MARATHON * W * TTE ** K / (REV * 300)) ** (1 / (1 + K))
    assert s["time_s"] == approx(exact, abs=0.05)


def test_task17_cp_wprime():
    r = PR.cp_wprime_scenario(5000, REV, W, 300, 15000)
    assert r["time_s"] == approx(1116.67, abs=0.01)
    assert r["power"] == approx(313.433, abs=1e-3)


def test_task10_d1_time_consistent_form():
    r = R.power_from_prior_distance(280, 21097.5, 42195, K)
    assert r["exponent"] == approx(-0.0752688, abs=1e-6)
    assert r["power"] == approx(280 * 2 ** (K / (1 + K)))
    assert r["power_workbook"] == approx(280 * 2 ** K)
    # consistent with task 9 applied at the implied time
    t1 = 5400.0
    t2 = t1 * r["time_ratio"]
    assert R.power_from_prior_time(280, t1, t2, K) == approx(r["power"])
    # and distance ∝ t·P
    assert (t2 * r["power"]) / (t1 * 280) == approx(2.0)


def test_scenarios_center_row_is_feasible_boundary():
    rows = PR.scenarios(MARATHON, 10766.75, 300, TTE, K, REV, W)
    mid = next(r for r in rows if r["factor"] == 1.0)
    assert mid["power_needed"] == approx(mid["power_sustainable"], abs=0.01)
    assert mid["cp_required"] == approx(300, abs=0.01)
    assert next(r for r in rows if r["factor"] == 1.1)["feasible"]
    assert not next(r for r in rows if r["factor"] == 0.9)["feasible"]


# ---- §1.3 CVI -------------------------------------------------------------------

def test_cvi_adjust_example():
    assert RE.cvi_adjust(30, 80) == approx(-0.03)
    assert RE.cvi_category(-3) == 0 and RE.cvi_category(25) == 1 and RE.cvi_category(26) == 2
    assert RE.cvi_category(150) == 6
    assert RE.cvi(100, 10) == approx(100 * 3.28084 / 6.21371)


# ---- §1.4 lookup -----------------------------------------------------------------

def test_riegel_table_lookup():
    # marathon target, 10k prior 40:00 -> band 4 (38:21–42:40) -> -0.09
    r = R.table_k(42195, 10000, 40 * 60)
    assert r["k"] == -0.09 and r["lo"] == -0.10 and r["hi"] == -0.08
    # D5: exactly on an upper bound belongs to that band
    assert R.table_k(42195, 10000, 29 * 60 + 50)["k"] == -0.06
    assert R.table_k(42195, 10000, 29 * 60 + 51)["k"] == -0.07
    # 10k target uses the 11-band set: 5k prior 15:00 -> band 2 -> -0.05
    assert R.table_k(10000, 5000, 15 * 60)["k"] == -0.05
    # clamping of the range at the ends
    fast = R.table_k(5000, 5000, 14 * 60)
    assert fast["k"] == -0.04 and fast["hi"] == -0.03
    slow = R.table_k(42195, 42195, 6 * 3600)
    assert slow["k"] == -0.12 and slow["lo"] == -0.12
    # non-standard and ultra
    assert R.table_k(15000, 10000, 3000)["k"] is None
    assert R.table_k(42195, 15000, 3000)["k"] is None
    u = R.table_k(60000, 21097.5, 7200)
    assert u["k"] == -0.12 and u["warning"]


def test_distance_category():
    assert R.distance_category(4850) == "5k" and R.distance_category(4849) is None
    assert R.distance_category(5200) is None
    assert R.distance_category(21097.5) == "half" and R.distance_category(40929.15) == "marathon"


def test_personal_k_cut():
    xs = [1200, 1800, 2400, 3600, 5400, 7200, 10800, 21600]
    ys = [300 * (x / 1200) ** -0.08 for x in xs[:6]] + [150, 60]
    pk = R.personal_k(xs, ys, 1000)
    assert pk["k"] == approx(-0.08, abs=1e-9) and pk["cut_s"] == 10800 and pk["n"] == 6
    raw = R.personal_k(xs, ys, 1000, keep_frac=None)
    assert raw["k"] < -0.2
    assert R.extrapolation_warning(20000, 7200) and not R.extrapolation_warning(9000, 7200)


# ---- §3.1 trail ----------------------------------------------------------------------

def test_trail_re_and_prediction():
    re_t = RE.trail_re(20, 1530, 3 * 3600, 200, 68, 153)       # D_eff 30 km in 3 h
    assert re_t == approx((30000 / 10800) / (200 / 68))
    r = PR.predict_run(distance_km=30, gain_m=1800, cp=250, tte=2400, k=-0.08, re=1.0,
                       weight=68, m=0.97, effort_divisor=153)
    assert r["effort_km"] == approx(30 + 1800 / 153)
    t = r["time_s"]
    # the fixed point holds
    assert t == approx(r["effort_km"] * 1000 / (1.0 * r["power"] / 68), abs=0.1)
    assert r["power"] == approx(250 * 0.97 * (t / 2400) ** -0.08, rel=1e-9)
    assert r["pace_s_per_km"] == approx(t / 30)
    assert r["effort_pace_s_per_km"] == approx(t / r["effort_km"])
    assert r["climb_power_cap"] == approx(1.1 * r["power"])


def test_road_prediction_with_target_and_task17():
    r = PR.predict_run(distance_km=5, cp=300, tte=TTE, k=K, re=REV, weight=W, w_prime=15000,
                       target_time_s=1100)
    assert r["kind"] == "road" and r["effort_pace_s_per_km"] is None
    assert r["task17"]["power"] == approx(313.433, abs=1e-3)
    assert r["target"]["power_required"] == approx(PR.power_for_time(5000, 1100, REV, W))
    assert "task17" not in PR.predict_run(distance_km=21.1, cp=300, tte=TTE, k=K, re=REV, weight=W,
                                          w_prime=15000)


# ---- §3.2 百岳 -------------------------------------------------------------------------

def test_baiyue_prediction():
    plan = PR.split_days(2, 21, 1800, None)
    assert plan == [{"km": 10.5, "gain_m": 900, "loss_m": 900}] * 2
    r = PR.predict_baiyue(day_plan=plan, eph=6.0, weight=68, m=0.9, pack_kg=12, hist_pack_kg=5,
                          aet=140, biggest_day={"ep": 25, "gain_m": 1000, "moving_h": 3.5})
    pf = (68 + 5) / (68 + 12)
    assert r["pack_factor"] == approx(pf)
    d1 = r["days"][0]
    assert d1["ep"] == approx(19.5)
    assert d1["moving_h"] == approx(19.5 / (6 * 0.9 * pf))
    cc = 1.8 * d1["moving_h"] + 0.3 * 10.5 + 10 * 0.9 + 0.6 * 0.9
    assert d1["kcal"] == approx(cc * 80)
    assert d1["water_ml"] == approx([0.7 * cc * 80, 0.8 * cc * 80])
    assert d1["hr_cap"] == 140
    assert any("移動時間" in f for f in d1["flags"])
    assert r["total"]["ep"] == approx(39) and r["total"]["km"] == approx(21)
    t = PR.predict_baiyue(day_plan=plan, eph=6.0, weight=68, target_moving_h=6)
    assert t["target"]["ep_per_h_needed"] == approx(6.5)


# ---- weather parsing ---------------------------------------------------------------------

def _cwa_fixture(day: str) -> dict:
    def el(name, key, vals, hourly=True):
        rows = []
        for h, v in vals:
            if hourly:
                rows.append({"DataTime": f"{day}T{h:02d}:00:00+08:00", "ElementValue": {key: v}})
            else:
                rows.append({"StartTime": f"{day}T{h:02d}:00:00+08:00",
                             "EndTime": f"{day}T{h + 12:02d}:00:00+08:00", "ElementValue": {key: v}})
        return {"ElementName": name, "Time": rows}
    hours = [(h, str(h)) for h in range(0, 24, 3)]
    return {"cwaopendata": {"Sent": f"{day}T05:30:00+08:00", "Dataset": {"Locations": {"Location": [
        {"LocationName": "玉山", "Latitude": "23.47", "Longitude": "120.957",
         "ParameterSet": {"Parameter": {"ParameterName": "id", "ParameterValue": "D080"}},
         "WeatherElement": [
             el("溫度", "Temperature", hours),
             el("露點溫度", "DewPoint", [(h, " ") for h, _ in hours]),
             el("相對濕度", "RelativeHumidity", [(h, "80") for h, _ in hours]),
             el("平均溫度", "Temperature", [(6, "8"), (18, "2")], hourly=False),
             el("平均露點溫度", "DewPoint", [(6, "4"), (18, "0")], hourly=False),
         ]},
        {"LocationName": "雪山", "Latitude": "24.38", "Longitude": "121.23",
         "ParameterSet": {"Parameter": {"ParameterName": "id", "ParameterValue": "D078"}},
         "WeatherElement": []},
    ]}}}}


def test_cwa_parse_match_and_day_conditions():
    doc = WX.parse_cwa(_cwa_fixture("2026-10-07"))
    assert doc["issued"].startswith("2026-10-07")
    yu = doc["locations"][0]
    assert yu["id"] == "D080" and yu["lat"] == 23.47
    assert yu["hourly"][2] == {"t": "2026-10-07T06:00:00+08:00", "temp": 6.0, "dew": None, "rh": 80.0}
    assert yu["blocks"][0]["temp"] == 8.0 and yu["blocks"][0]["dew"] == 4.0
    assert WX.match_location(doc["locations"], "玉山主峰", None, None)["id"] == "D080"
    near = WX.match_location(doc["locations"], "玉山前峰", 23.475, 120.94)
    assert near["match"] == "nearest" and near["distance_km"] < 5
    assert WX.match_location(doc["locations"], "奇萊", 24.1, 121.3) is None
    c = WX.cwa_day_conditions(yu, dt.date(2026, 10, 7))
    assert c["product"] == "hourly"
    assert c["temp_c"] == approx((6 + 9 + 12 + 15) / 4) and c["rh_pct"] == 80
    # a date only the weekly blocks cover would use them; with no RH, RH comes from the dew point
    yu2 = {**yu, "hourly": []}
    c2 = WX.cwa_day_conditions(yu2, dt.date(2026, 10, 7))
    assert c2["product"] == "weekly" and c2["temp_c"] == 8
    assert c2["rh_pct"] == approx(ENV.rh_from_dew_point(8, 4))


def test_fetch_cwa_uses_cache(tmp_path):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return _cwa_fixture("2026-10-07")
    now = dt.datetime(2026, 10, 7, 8, tzinfo=WX.TZ)
    a = WX.fetch_cwa(WX.CWA_HOURLY, "CWA-TESTKEY-123", cache_dir=tmp_path, now=now, get=fake_get)
    assert a["cache"] == "miss" and len(calls) == 1 and calls[0][1]["Authorization"] == "CWA-TESTKEY-123"
    b = WX.fetch_cwa(WX.CWA_HOURLY, "CWA-TESTKEY-123", cache_dir=tmp_path,
                     now=now + dt.timedelta(hours=1), get=fake_get)
    assert b["cache"] == "hit" and len(calls) == 1
    WX.fetch_cwa(WX.CWA_HOURLY, "CWA-TESTKEY-123", cache_dir=tmp_path,
                 now=now + dt.timedelta(hours=4), get=fake_get)
    assert len(calls) == 2
    with pytest.raises(RuntimeError):
        WX.fetch_cwa(WX.CWA_WEEKLY, None, cache_dir=tmp_path, now=now, get=fake_get)


def _om(day: str, temps, rhs, elevation=None):
    times = [f"{day}T{h:02d}:00" for h in range(24)]
    js = {"hourly": {"time": times, "temperature_2m": temps, "relative_humidity_2m": rhs}}
    if elevation is not None:
        js["elevation"] = elevation
    return js


def test_open_meteo_parse_daytime_mean():
    temps = [10.0] * 6 + [20.0] * 12 + [10.0] * 6
    js = _om("2026-10-07", temps, [60.0] * 24, elevation=3000)
    c = WX.parse_open_meteo(js, {dt.date(2026, 10, 7)})
    assert c["temp_c"] == 20 and c["rh_pct"] == 60 and c["n"] == 12 and c["grid_elevation_m"] == 3000
    assert WX.parse_open_meteo(js, {dt.date(2026, 10, 8)}) is None


def test_climatology_lapse_rate():
    y = [{"temp_c": 10.0, "rh_pct": 70.0, "grid_elevation_m": 2500.0},
         {"temp_c": 12.0, "rh_pct": 80.0, "grid_elevation_m": 2500.0}, None]
    c = WX.climatology_from(y, 3500.0)
    assert c["temp_c"] == approx(11.0 - 6.5) and c["rh_pct"] == 75 and c["years"] == 2


def test_race_conditions_chain_falls_through(tmp_path):
    def fake_get(url, params, timeout):
        if "archive" in url:
            lo = dt.date.fromisoformat(params["start_date"])
            days = (dt.date.fromisoformat(params["end_date"]) - lo).days + 1
            times, T, H = [], [], []
            for i in range(days):
                d = lo + dt.timedelta(days=i)
                for h in range(24):
                    times.append(f"{d}T{h:02d}:00")
                    T.append(15.0)
                    H.append(70.0)
            return {"elevation": 3000.0, "hourly": {"time": times, "temperature_2m": T,
                                                    "relative_humidity_2m": H}}
        raise RuntimeError("HTTP 500")
    today = dt.date(2026, 9, 30)
    r = WX.race_conditions(date=today + dt.timedelta(days=7), days=2, lat=23.47, lon=120.957,
                           elevation_m=3952, name="玉山", today=today, key=None, get=fake_get,
                           cache_dir=tmp_path)
    provs = [t["provider"] for t in r["tried"]]
    assert provs[0] == "cwa" and "open_meteo" in provs and r["provider"] == "climatology"
    assert r["values"]["temp_c"] == approx(15 - 6.5 * 0.952)
    assert r["values"]["altitude_m"] == 3952
    # no coordinates and no CWA -> manual
    m = WX.race_conditions(date=today, lat=None, lon=None, today=today, key=None, get=fake_get,
                           cache_dir=tmp_path)
    assert m["provider"] == "manual" and m["values"] is None


def test_activities_conditions_overlap():
    js = _om("2026-09-01", [float(h) for h in range(24)], [50.0] * 24)
    w = [(dt.datetime(2026, 9, 1, 6, 10), dt.datetime(2026, 9, 1, 7, 20))]
    c = WX.activities_conditions(js, w)
    assert c["temp_c"] == approx((6 + 7) / 2) and c["activities"] == 1


def test_key_storage_and_mask(tmp_path, monkeypatch):
    monkeypatch.delenv("CWA_API_KEY", raising=False)
    p = tmp_path / "weather.json"
    assert WX.key_status(p)["configured"] is False
    WX.save_key("CWA-ABCDEF12-3456-7890", p)
    s = WX.key_status(p)
    assert s["configured"] and s["masked"] == "CWA-ABCD…" and "3456" not in s["masked"]
    assert json.loads(p.read_text("utf-8"))["cwa_api_key"] == "CWA-ABCDEF12-3456-7890"


def test_find_peak():
    peaks = [{"name": "玉山", "elevation_m": 3952, "baiyue": True},
             {"name": "玉山北峰", "elevation_m": 3858, "baiyue": True},
             {"name": "雪山", "elevation_m": 3884, "baiyue": True}]
    assert WX.find_peak("玉山主峰", peaks)["name"] == "玉山"
    assert WX.find_peak("玉山北峰單攻", peaks)["name"] == "玉山北峰"
    assert WX.find_peak("嘉明湖", peaks) is None


def test_baiyue_json_has_100_peaks():
    from backend.engine.achievements import load_peaks
    by = load_peaks()
    assert len(by) == 100 and all({"name", "lat", "lon", "elevation_m", "rank"} <= set(p) for p in by)
    assert max(by, key=lambda p: p["elevation_m"])["name"] == "玉山"
    assert len(WX.load_peaks()) > 100


def test_altitude_norm_d2():
    """D2: activity power is expressed at the reference altitude, T / RH cancel."""
    from backend.engine.racepower.athlete import altitude_norm
    ref = {"altitude_m": 15.0, "temp_c": 27.7, "rh_pct": 80.0}
    assert altitude_norm(15.0, ref) == pytest.approx(1.0)
    assert altitude_norm(None, ref) == 1.0
    assert altitude_norm(2000.0, None) == 1.0
    f = altitude_norm(2000.0, ref)
    m = ENV.multiplier({"altitude_m": 15.0, "temp_c": 27.7, "rh_pct": 80.0},
                       {"altitude_m": 2000.0, "temp_c": 27.7, "rh_pct": 80.0})["M"]
    assert f > 1.0 and f == pytest.approx(2.0 - m, abs=1e-12)   # same altitude terms, opposite sign


def test_build_baiyue_parser():
    from backend.scripts.build_baiyue import parse_rows
    rows = parse_rows('export const PEAK_ROWS = [\n  ["玉山",3952,120.95728,23.47,1],\n  ["x",1,121,24,16],\n];')
    assert rows == [("玉山", 3952.0, 120.95728, 23.47, 1), ("x", 1.0, 121.0, 24.0, 16)]
