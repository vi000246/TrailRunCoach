"""
賽事計算機: CSV export of the plan, and the per-segment, time-of-day heat
correction (weather hourly rows → segment ETA → Hadley penalty → Mᵢ, iterated
to a fixed point).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from urllib.parse import unquote

import pytest

from backend.engine.racepower import course as CO
from backend.engine.racepower import env as ENV
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import planner as PL
from backend.engine.racepower import weather as WX
from backend.tests.test_racepower_v2 import RE0, fake_inputs, fake_v1, synthetic_track

approx = pytest.approx
DATE = "2026-10-04"
START = dt.datetime(2026, 10, 4, 6, 0)


def rows_from(fn, hours=range(0, 24), date=DATE):
    """Synthetic hourly forecast: fn(hour) → (temp_c, rh_pct)."""
    out = []
    for h in hours:
        t, rh = fn(h)
        out.append({"t": f"{date}T{h:02d}:00", "temp_c": t, "rh_pct": rh})
    return out


def interp_temp(fc, when):
    """Independent linear interpolation of the forecast temperature."""
    pts = [(dt.datetime.fromisoformat(r["t"]), r["temp_c"]) for r in fc]
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if t0 <= when <= t1:
            return a + (b - a) * (when - t0).total_seconds() / (t1 - t0).total_seconds()
    raise AssertionError("outside")


def hot_later(h):
    # cool until 07:00, ramps to 32 °C by 08:00 and stays there
    return (10.0 if h <= 7 else 32.0), 70.0


def plan_road(**opts):
    """A marathon (≈ 3 h at M = 1): long enough to cross several forecast hours."""
    v1 = fake_v1("road", 42.195)
    return PL.plan_run(v1=v1, course=CO.manual_course(42.195, 0.0, 0.0, "km"), grade_re=GM.GradeRE(RE0),
                       opts={"mode": "auto", **opts}, validated={}, effort_validated=False)


# ---- env / weather primitives -------------------------------------------------------

def test_segment_factors_heat_list():
    frm = {"altitude_m": 100.0, "temp_c": 15.0, "rh_pct": 60.0}
    to = {"altitude_m": 1200.0, "temp_c": 20.0, "rh_pct": 70.0}
    zs = [300.0, 900.0, 1500.0]
    base = ENV.segment_factors(zs, frm, to)
    same = ENV.segment_factors(zs, frm, to, heat=[(20.0, 70.0)] * 3)
    assert same == approx(base, abs=1e-12)
    hot = ENV.segment_factors(zs, frm, to, heat=[(20.0, 70.0), (30.0, 70.0), (34.0, 60.0)])
    h_to = ENV.heat_penalty_pct(20.0, 70.0)
    assert hot[0] == approx(base[0], abs=1e-12)
    assert hot[1] == approx(base[1] - (ENV.heat_penalty_pct(30.0, 70.0) - h_to) / 100.0, abs=1e-12)
    assert hot[2] == approx(base[2] - (ENV.heat_penalty_pct(34.0, 60.0) - h_to) / 100.0, abs=1e-12)
    with pytest.raises(ValueError):
        ENV.segment_factors(zs, frm, to, heat=[(20.0, 70.0)])


def test_hourly_at_interpolates_and_stops_at_the_edges():
    fc = [WX._hour_row(f"{DATE}T{h:02d}:00+08:00", 10.0 + h, 60.0, None) for h in (6, 7, 9)]
    assert fc[0]["t"] == f"{DATE}T06:00" and fc[0]["dew_c"] == approx(ENV.dew_point(16.0, 60.0)["dew_c"])
    mid = WX.hourly_at(fc, dt.datetime(2026, 10, 4, 6, 30))
    assert mid["temp_c"] == approx(16.5)
    # 3-hourly gap (CWA day 2/3): still linear
    assert WX.hourly_at(fc, dt.datetime(2026, 10, 4, 8, 0))["temp_c"] == approx(18.0)
    assert WX.hourly_at(fc, dt.datetime(2026, 10, 4, 5, 0))["temp_c"] == approx(16.0)       # ≤ 1.5 h before
    assert WX.hourly_at(fc, dt.datetime(2026, 10, 4, 4, 0)) is None
    assert WX.hourly_at(fc, dt.datetime(2026, 10, 4, 11, 0)) is None
    # RH is rebuilt from the interpolated temperature and dew point
    assert mid["rh_pct"] == approx(ENV.rh_from_dew_point(mid["temp_c"], mid["dew_c"]))


def test_weather_returns_hourly_rows_from_open_meteo_only(tmp_path):
    def fake_get(url, params, timeout):
        lo = dt.date.fromisoformat(params["start_date"])
        n = (dt.date.fromisoformat(params["end_date"]) - lo).days + 1
        times = [f"{lo + dt.timedelta(days=d)}T{h:02d}:00" for d in range(n) for h in range(24)]
        return {"elevation": 500.0, "hourly": {"time": times, "temperature_2m": [20.0 + i % 24 * 0.5 for i in range(len(times))],
                                               "relative_humidity_2m": [70.0] * len(times),
                                               "dew_point_2m": [15.0] * len(times)}}
    today = dt.date(2026, 9, 30)
    r = WX.race_conditions(date=dt.date(2026, 10, 4), lat=23.5, lon=121.0, elevation_m=500.0, today=today,
                           key=None, get=fake_get, cache_dir=tmp_path, use_cwa=False)
    assert r["provider"] == "open_meteo"
    # the event day plus the day after (a race past midnight); daytime mean unchanged
    assert r["hourly"][0]["t"] == "2026-10-04T00:00" and r["hourly"][-1]["t"] == "2026-10-05T23:00"
    assert len(r["hourly"]) == 48 and r["values"]["temp_c"] == approx(20.0 + 0.5 * 11.5)
    far = WX.race_conditions(date=dt.date(2026, 11, 30), lat=23.5, lon=121.0, elevation_m=500.0, today=today,
                             key=None, get=fake_get, cache_dir=tmp_path, use_cwa=False)
    assert far["provider"] == "climatology" and far["hourly"] is None


# ---- per-segment heat in the planner ------------------------------------------------------

def test_synthetic_forecast_gives_the_expected_per_segment_M_and_converges():
    fc = rows_from(hot_later)
    p = plan_road(date=DATE, start_time="06:00", hourly=fc)
    h = p["summary"]["heat"]
    assert h["mode"] == "hourly" and h["badge"] == "推估"
    assert h["converged"] and 2 <= h["passes"] <= PL.HEAT_MAX_PASSES and h["delta_s"] < 1.0
    hot_pen = ENV.heat_penalty_pct(32.0, 70.0)
    assert hot_pen > 5.0                                    # a real penalty at 32 °C / 70 %
    early = late = 0
    for s in p["segments"]:
        when = START + dt.timedelta(seconds=s["cum_s"] - 0.5 * s["t"])
        temp = interp_temp(fc, when)
        rh = ENV.rh_from_dew_point(temp, ENV.dew_point(temp, 70.0)["dew_c"])
        # the environment of fake_v1: From = To = 100 m / 12 °C / 70 % → only heat moves M
        want = 1.0 - (ENV.heat_penalty_pct(temp, rh) - ENV.heat_penalty_pct(12.0, 70.0)) / 100.0
        assert s["M"] == approx(want, abs=5e-4), (s["i"], s["heat_clock"])
        assert s["heat_src"] == "hourly" and s["temp_c"] == approx(temp, abs=0.02)
        # away from the ramp's ends by a minute (the fixed point is within 1 s)
        if when < START.replace(hour=6, minute=59):
            early += 1
            assert s["M"] == 1.0 and s["heat_pct"] == 0.0
        if when > START.replace(hour=8, minute=1):
            late += 1
            assert s["M"] == approx(1.0 - hot_pen / 100.0, abs=1e-9)
    assert early and late
    # the chart rows sit on the course at the clock the plan reaches them
    hp = p["heat_profile"]
    assert hp[0]["clock"] == "06:00" and hp[0]["km"] == approx(0.0)
    assert all(a["km"] < b["km"] for a, b in zip(hp, hp[1:])) and hp[-1]["km"] <= 42.195 + 1e-9
    single = plan_road(date=DATE, start_time="06:00")
    assert p["summary"]["time_s"] > single["summary"]["time_s"] + 60
    assert single["summary"]["heat"]["mode"] == "single"


def test_heat_fixed_point_is_self_consistent():
    """Recompute Mᵢ from the returned ETAs and re-solve: the time moves < 1 s."""
    fc = rows_from(lambda h: (8.0 + 2.5 * h, 75.0))      # warms all morning
    p = plan_road(date=DATE, start_time="07:00", hourly=fc)
    assert p["summary"]["heat"]["converged"]
    start = dt.datetime(2026, 10, 4, 7, 0)
    heat = []
    for s in p["segments"]:
        c = WX.hourly_at(WX._in_window([WX._hour_row(r["t"], r["temp_c"], r["rh_pct"], None) for r in fc],
                                       {dt.date(2026, 10, 4)}), start + dt.timedelta(seconds=s["cum_s"] - 0.5 * s["t"]))
        heat.append((c["temp_c"], c["rh_pct"]))
    ms =ENV.segment_factors([100.0] * len(heat), {"altitude_m": 100.0, "temp_c": 12.0, "rh_pct": 70.0},
                             {"altitude_m": 100.0, "temp_c": 12.0, "rh_pct": 70.0}, "acclimatised", heat)
    assert [s["M"] for s in p["segments"]] == approx(ms, abs=2e-4)
    # a later start runs in warmer hours: the ETAs pick different forecast hours
    late = plan_road(date=DATE, start_time="10:00", hourly=fc)
    assert late["summary"]["time_s"] > p["summary"]["time_s"]
    assert late["segments"][0]["temp_c"] > p["segments"][0]["temp_c"] + 5


def test_flat_forecast_equals_the_single_M_result():
    flat = rows_from(lambda h: (12.0, 70.0))               # = fake_v1's To conditions
    a = plan_road(date=DATE, start_time="06:00")
    b = plan_road(date=DATE, start_time="06:00", hourly=flat)
    assert b["summary"]["heat"]["mode"] == "hourly" and b["summary"]["heat"]["passes"] == 1
    assert b["summary"]["time_s"] == approx(a["summary"]["time_s"], abs=1e-6)
    assert b["summary"]["power"] == approx(a["summary"]["power"], abs=1e-6)
    assert [s["M"] for s in b["segments"]] == approx([s["M"] for s in a["segments"]], abs=1e-12)
    # GPX course (per-segment altitude), both modes
    tr = synthetic_track({"len": 12000, "z": lambda x: 200 + (x * 0.05 if x < 6000 else (12000 - x) * 0.05)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 12.0, c["totals"]["gain_m"])
    for mode, extra in (("auto", {}), ("time", {"target_time_s": 5400})):
        kw = dict(v1=v1, course=c, grade_re=GM.GradeRE(RE0), validated={}, effort_validated=False)
        g1 = PL.plan_run(opts={"mode": mode, "date": DATE, "start_time": "06:00", **extra}, **kw)
        g2 = PL.plan_run(opts={"mode": mode, "date": DATE, "start_time": "06:00", "hourly": flat, **extra}, **kw)
        assert g2["summary"]["time_s"] == approx(g1["summary"]["time_s"], abs=1e-6)
        assert [s["M"] for s in g2["segments"]] == approx([s["M"] for s in g1["segments"]], abs=1e-12)


def test_no_hourly_or_no_start_time_falls_back_and_says_so():
    none = plan_road(date=DATE, start_time="06:00")
    assert none["summary"]["heat"]["mode"] == "single" and none["heat_profile"] is None
    assert any("單一溫度 12.0 °C" in w and "沒有逐時預報" in w for w in none["warnings"])
    assert all(s["heat_src"] == "single" and s["temp_c"] == 12.0 for s in none["segments"])
    nostart = plan_road(date=DATE, hourly=rows_from(hot_later))
    assert nostart["summary"]["heat"]["mode"] == "single"
    assert any("起跑時間" in w for w in nostart["warnings"])
    off = plan_road(date=DATE, start_time="06:00", hourly=rows_from(hot_later), hourly_heat=False)
    assert off["summary"]["heat"]["reason"] == "逐時熱修正已關閉"
    assert not any("單一溫度" in w for w in off["warnings"])
    # a forecast for another day covers nothing
    other = plan_road(date=DATE, start_time="06:00", hourly=rows_from(hot_later, date="2026-10-10"))
    assert other["summary"]["heat"]["mode"] == "single" and "涵蓋" in other["summary"]["heat"]["reason"]
    # the forecast ends mid-race: those segments use the single value
    part = plan_road(date=DATE, start_time="06:00", hourly=rows_from(hot_later, hours=range(0, 7)))
    assert part["summary"]["heat"]["outside"] > 0
    assert any("超出逐時預報範圍" in w for w in part["warnings"])


# ---- API: /plan with hourly, /export/csv ---------------------------------------------------

@pytest.fixture()
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    from backend.engine.racepower import backtest as BT
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: fake_inputs())
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": []})
    monkeypatch.setattr(RP, "_trail_hr", lambda: None)      # no real dataset: trail total from power
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": False, "hike": False}, False))
    RP._courses.clear()
    app = FastAPI()
    app.include_router(RP.router)
    return TestClient(app)


def read_csv(r):
    raw = r.content
    assert raw.startswith(b"\xef\xbb\xbf")                    # UTF-8 BOM for Excel
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    i = rows.index([])
    return rows[:i], rows[i + 1], rows[i + 2:]


def test_csv_export_matches_the_plan(client):
    body = {"type": "road", "distance_km": 21.1, "mode": "auto", "date": DATE, "start_time": "06:00",
            "stops": [{"km": 10, "minutes": 2}], "course": {"manual": {"km": 21.1, "split": "km"}},
            "hourly": rows_from(hot_later), "name": "台北 半馬"}
    plan = client.post("/api/v1/racepower/plan", json=body).json()
    r = client.post("/api/v1/racepower/export/csv", json=body)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    fname = unquote(r.headers["x-filename"])
    assert fname == f"賽事計算機_台北_半馬_{DATE}.csv" and "filename*=UTF-8''" in r.headers["content-disposition"]
    head, cols, rows = read_csv(r)
    kv = {x[0]: x[1:] for x in head if x}
    assert kv["路線"] == ["台北 半馬"] and kv["模式"][0].startswith("通通幫我算")
    assert kv["CP W"] == ["300", "活動"] and kv["TTE s"][0] == "3000" and kv["Riegel k"][0] == "-0.07"
    assert kv["策略"] == ["均速"] and kv["熱修正"][0].startswith("逐段") and kv["熱修正"][1] == "推估"
    assert kv["距離 km"] == ["21.1"] and "計算時間" in kv and kv["補給站"] == ["10 km 2 分"]
    assert cols[:4] == ["段", "天", "起點 km", "終點 km"] and "溫度 °C" in cols and "ETA（含補給）" in cols
    segs, total = rows[:-1], rows[-1]
    assert len(segs) == len(plan["segments"]) == 22 and total[0] == "合計"
    c = {k: cols.index(k) for k in cols}
    for row, s in zip(segs, plan["segments"]):
        assert int(row[c["段"]]) == s["i"]
        assert float(row[c["目標功率 W"]]) == round(s["power"])
        assert float(row[c["M"]]) == approx(s["M"], abs=1e-4)
        assert float(row[c["溫度 °C"]]) == approx(s["temp_c"], abs=0.05)
        assert row[c["ETA（含補給）"]] == s["eta"] and row[c["標記"]] == "推估"
    # the last ETA includes the aid stop: the finish clock
    assert segs[-1][c["ETA（含補給）"]] == plan["summary"]["finish_eta"] == total[c["ETA（含補給）"]]
    h, m, s_ = (int(x) for x in total[c["分段時間"]].split(":"))
    assert h * 3600 + m * 60 + s_ == round(plan["summary"]["time_s"])


def test_csv_export_gpx_and_hike(client):
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    tr.name = "玉山 測試"
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("h.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    t = client.post("/api/v1/racepower/export/csv", json={"type": "trail", "course": {"course_id": cid}})
    assert t.status_code == 200, t.text
    # named after the GPX track and (no race date given) the day computed
    assert unquote(t.headers["x-filename"]) == f"賽事計算機_玉山_測試_{dt.date.today().isoformat()}.csv"
    head, cols, rows = read_csv(t)
    assert len(rows) - 1 == len(client.post("/api/v1/racepower/plan",
                                            json={"type": "trail", "course": {"course_id": cid}}).json()["segments"])
    hk = client.post("/api/v1/racepower/export/csv", json={"type": "baiyue", "course": {"course_id": cid},
                                                           "day_splits_km": [8], "start_time": "05:30"})
    assert hk.status_code == 200, hk.text
    head, cols, rows = read_csv(hk)
    kv = {x[0]: x[1:] for x in head if x}
    assert kv["類型"] == ["百岳"] and "總移動時間" in kv and kv["海拔適應"] == ["未適應"]
    c = cols.index("天")
    assert {r[c] for r in rows[:-1]} == {"1", "2"}


def test_csv_names_the_trail_even_strategy_even_effort():
    """SP-224: on trail the 「even」 strategy is even effort (the pace follows the grade); road keeps 均速."""
    from backend.engine.racepower import csvplan as CSV
    at = dt.datetime(2026, 10, 6, 8, 0)
    for kind, label in (("trail", "均勻努力"), ("road", "均速")):
        plan = {"type": kind, "summary": {"strategy": "even", "km": 10.0}, "used": {}}
        rows = {r[0]: r[1:] for r in CSV.header_rows(plan, name="x", date=None, computed_at=at) if r}
        assert rows["策略"] == [label]
    plan = {"type": "trail", "summary": {"strategy": "positive", "strategy_amount": 0.03, "km": 10.0}, "used": {}}
    rows = {r[0]: r[1:] for r in CSV.header_rows(plan, name="x", date=None, computed_at=at) if r}
    assert rows["策略"] == ["前快後慢 3.0%"]
