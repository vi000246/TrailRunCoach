"""
賽事功率 補給: energy from Stryd power (van Rassel 2026) with Minetti ×
Fletcher / Keytel / Pandolf fallbacks, carbohydrate / water / sodium by event
type, the generic schedule on the predicted splits, aid-station types, the
百岳 daily budget, and the CSV columns (docs/research/fueling-and-energy.md §7.5).
No WKO5 folder, no DB: the API runs on test_racepower_v2's fake inputs.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import types

import pytest

from backend.engine.algorithms import minetti
from backend.engine.racepower import course as CO
from backend.engine.racepower import fuel as FU
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import hike as HK
from backend.tests.test_racepower_export import client, read_csv  # noqa: F401  (fixture)
from backend.tests.test_racepower_v2 import W, synthetic_track

approx = pytest.approx


# ---- energy -----------------------------------------------------------------

def test_power_method_on_the_flat_matches_fletcher_at_re_one():
    """§7.5: RE = 1.0 → kcal/kg/km inside Fletcher 2009's 1.05–1.11."""
    weight, v = 67.0, 3.5
    p = v * weight / 1.0                      # P = v·W / RE
    per = FU.power_kcal(p, 1000.0 / v, 0.0) / weight
    assert 1.05 <= per <= 1.11 and per == approx(1000.0 / (0.221 * 4184.0), rel=1e-9)
    # Stryd's own kcal = kJ (η 24 %) is ~8 % lower
    kj_per = p * (1000.0 / v) / 1000.0 / weight
    assert kj_per == approx(per * 0.221 / 0.24, rel=1e-2) and per / kj_per == approx(1.08, abs=0.01)


def test_eta_changes_above_four_percent_and_power_is_not_used_past_eight():
    assert FU.power_kcal(300, 3600, 0.06) == approx(300 * 3600 / (0.216 * 4184))
    assert FU.power_kcal(300, 3600, 0.03) == approx(300 * 3600 / (0.221 * 4184))
    segs = [{"grade": 0.0, "power": 250.0, "t": 600.0, "dist_m": 2000.0},
            {"grade": 0.12, "power": 260.0, "t": 900.0, "dist_m": 1000.0},
            {"grade": 0.05, "power": 255.0, "t": 600.0, "dist_m": 1500.0, "walk": "走跑皆可"},
            {"grade": -0.10, "power": 180.0, "t": 300.0, "dist_m": 1200.0}]
    e = FU.run_energy(segs, 65.0, 2.0)
    assert [x["method"] for x in e] == ["power", "minetti", "minetti_walk", "minetti"]
    # downhill uses the raw Minetti curve, no 0.9 speed floor
    assert e[3]["kcal"] == approx(1.07 * 67.0 * 1.2 * minetti.cost_of_transport(-0.10) / minetti.FLAT_RUN)
    assert minetti.cost_of_transport(-0.10) / minetti.FLAT_RUN < 0.9


def test_keytel_fixed_input():
    """Keytel 2005 without VO2max (coefficients via Hsieh 2025)."""
    m = 60 * (0.6309 * 150 + 0.1988 * 70 + 0.2017 * 40 - 55.0969) / 4.184
    assert FU.keytel_kcal(150, 60, 70, 40, "male") == approx(m) == approx(882.2, abs=0.1)
    f = 60 * (0.4472 * 150 - 0.1263 * 60 + 0.074 * 40 - 20.4022) / 4.184
    assert FU.keytel_kcal(150, 60, 60, 40, "female") == approx(f)
    # no power on a segment, HR known → Keytel
    e = FU.run_energy([{"grade": 0.0, "power": None, "t": 3600.0, "dist_m": 10000.0}], 70.0, hr_bpm=150, age=40,
                      sex="male")
    assert e[0]["method"] == "keytel" and e[0]["kcal"] == approx(m)


def test_pandolf_segments_and_downhill_estimate():
    s_up = {"grade": 0.10, "t": 3600.0, "dist_m": 2000.0, "pack_kg": 9.0, "gain_m": 200.0, "loss_m": 0.0}
    s_dn = {"grade": -0.10, "t": 3600.0, "dist_m": 3000.0, "pack_kg": 9.0, "gain_m": 0.0, "loss_m": 300.0}
    up, dn = FU.hike_energy([s_up, s_dn], 67.0)
    assert up["kcal"] == approx(HK.pandolf(67.0, 9.0, 2000 / 3600, 10.0) * 3600 / 4184)
    assert up["kcal_yamamoto"] == approx((1.8 + 0.3 * 2 + 10 * 0.2) * 76.0)
    flat = HK.pandolf(67.0, 9.0, 3000 / 3600, 0.0) * 3600 / 4184
    assert 0 < dn["kcal"] < flat                 # cheaper than the flat, never negative


def test_baiyue_daily_doc_example_3600_kcal_and_0_7_kg_food():
    """§5.1: 67 kg + 9 kg, 10 km / +1200 / −800 in 7 h, 175 cm, 40 y, male."""
    kcal = FU.yamamoto_kcal(7, 10, 1200, 800, 76.0)
    assert kcal == approx(28.08 * 76, rel=1e-3)
    d = FU.baiyue_daily([{"day": 1, "moving_h": 7.0, "kcal_pandolf": kcal, "kcal_yamamoto": kcal}], 67.0,
                        {"height_cm": 175.0, "age": 40.0, "sex": "male"})[0]
    assert d["ree"] == approx(1568.75)
    assert d["total"][1] == approx(3600, rel=0.02)
    assert d["food_kg"] == approx(0.7, abs=0.02)
    assert FU.mifflin_ree(60, 165, 30, "female") == approx(600 + 1031.25 - 150 - 161)


# ---- event rules ------------------------------------------------------------------

@pytest.mark.parametrize("kind,hours,km,cls", [
    ("road", 1.0, 10.0, "short"), ("road", 1.6, 21.1, "half"), ("road", 3.4, 42.195, "long"),
    ("trail", 1.6, 15.0, "half"), ("trail", 4.0, 30.0, "long"), ("trail", 9.0, 60.0, "ultra"),
    ("baiyue", 20.0, 40.0, "hike")])
def test_event_class_and_carbs_never_above_90(kind, hours, km, cls):
    assert FU.event_class(kind, hours, km) == cls
    c = FU.carb_target(cls)
    assert c["hi"] <= 90.0 and c["lo"] <= c["hi"]
    if cls == "long":
        assert (c["lo"], c["hi"]) == (60.0, 90.0) and c["mix"]
    if cls == "half":
        assert (c["lo"], c["hi"]) == (30.0, 60.0)
    if cls == "hike":
        assert (c["lo"], c["hi"]) == (30.0, 50.0) and c["badge"] == "推估"


def test_water_and_sodium_move_with_heat_inside_the_source_range():
    cool, hot = FU.water_band("long", 8.0, False), FU.water_band("long", 32.0, False)
    assert cool == [400.0, 600.0] and hot == [600.0, 800.0]
    assert FU.water_band("long", 12.0, True) == [600.0, 800.0]          # a hot segment by heat_pct
    assert FU.water_band("half", 30.0, False) is None                   # half: thirst unless hot (handled above)
    n_cool, n_hot = FU.sodium_band("long", 8.0, False), FU.sodium_band("long", 32.0, False)
    assert 300 <= n_cool[0] < n_hot[0] and n_hot[1] <= 600
    assert FU.sodium_band("hike", 20.0, False) == [200.0, 300.0]
    assert FU.loading(65.0, 3.0, "long")["g_day"] == [650.0, 780.0]
    assert FU.loading(65.0, 1.2, "half")["g_day"] == [390.0, 390.0]
    assert FU.loading(65.0, 20.0, "hike")["kind"] == "normal"


def test_sweat_prior_scales_with_intensity_and_caps_the_water():
    assert FU.sweat_prior(8.0) == approx(1.0) and FU.sweat_prior(30.0) == approx(1.75)
    assert FU.sweat_prior(30.0, 700.0) == approx(1.75)
    assert FU.sweat_prior(30.0, 200.0) == approx(1.75 * 0.4)        # a slow walk: floor 0.4
    # a slow, long effort (≈ 250 kcal/h, hot): water never above the sweat rate, dehydration sane
    segs, cum = [], 0.0
    for i in range(10):
        cum += 3600.0
        segs.append({"i": i + 1, "t": 3600.0, "cum_s": cum, "grade": 0.0, "power": 60.0, "dist_m": 4000.0,
                     "start_km": 4.0 * i, "end_km": 4.0 * (i + 1), "temp_c": 30.0, "heat_pct": 5.0})
    plan = {"type": "trail", "segments": segs, "summary": {"time_s": cum, "km": 40.0}}
    f = FU.plan_fuel(plan, weight=66.0)
    sweat = FU.sweat_prior(30.0, segs[0]["kcal"]) * 1000.0
    assert f["water"]["per_h"][1] <= round(sweat) and f["water"]["dehydration"] < 0.02
    assert all("_wb" not in s and "_sweat" not in s for s in segs)


def test_schedule_skips_steep_descents_and_moves_onto_climbs():
    segs, cum = [], 0.0
    spec = [(1500, 0.0, None), (600, -0.20, None), (1200, 0.0, None), (900, 0.20, "走跑皆可"), (3000, 0.0, None)]
    for i, (t, g, walk) in enumerate(spec, 1):
        cum += t
        segs.append({"i": i, "t": t, "cum_s": cum, "grade": g, "walk": walk, "start_km": i - 1, "end_km": i})
    pts = FU.fuel_points(segs, 1500.0)
    for tau, _ in pts:
        s = segs[FU._seg_at(segs, tau)]
        assert s["grade"] >= FU.STEEP_DOWN_NO_FUEL
    # the second point (≈ 3000 s) moved onto the walked climb starting at 3300 s
    assert any(abs(tau - 3330.0) < 1 for tau, _ in pts)
    # a food station within ±10 min takes the point
    st = FU.fuel_points(segs, 1500.0, station_times=[1700.0])
    assert st[0] == (1700.0, True)


def test_stops_from_waypoints():
    w = [{"name": "CP1 補給站", "km": 10.2}, {"name": "水站 A", "km": 5.0}, {"name": "觀景台", "km": 7.0},
         {"name": "起點", "km": 0.0}, {"name": "Aid 3 (drop bag)", "km": 22.0}, {"name": "醫護站", "km": 15.0},
         {"name": "CP 2", "km": 18.0}, {"name": "終點補給", "km": 30.0}]
    got = FU.stops_from_wpts(w, 30.0)
    assert [(r["km"], r["type"]) for r in got] == [(5.0, "water"), (10.2, "aid"), (15.0, "medical"),
                                                   (18.0, "aid"), (22.0, "self")]


# ---- API: /plan fuel block, stop types, CSV --------------------------------------------

def test_plan_fuel_road_marathon_with_typed_stations(client):  # noqa: F811
    body = {"type": "road", "distance_km": 42.195, "course": {"manual": {"km": 42.195, "split": "km"}},
            "start_time": "07:00", "env_to": {"temp_c": 26, "rh_pct": 75},
            "stops": [{"km": 10, "type": "aid"}, {"km": 20, "type": "water", "name": "20K"},
                      {"km": 25, "type": "medical"}, {"km": 30, "type": "big"}]}
    p = client.post("/api/v1/racepower/plan", json=body).json()
    f = p["fuel"]
    assert f["category"] == "long" and f["methods"] == {"power": approx(f["kcal"])}
    assert sum(s["kcal"] for s in p["segments"]) == approx(f["kcal"])
    assert p["segments"][-1]["cum_kcal"] == approx(f["kcal"])
    lo, hi = f["kcal_band"]
    assert lo == approx(f["kcal"] * 0.9) and hi == approx(f["kcal"] * 1.1)
    assert f["cho"]["per_h"] == [60.0, 90.0] and max(f["cho"]["per_h"]) <= 90
    assert 400 <= f["water"]["per_h"][0] <= f["water"]["per_h"][1] <= 800 and not f["water"]["thirst"]
    assert "體重增加" in f["water"]["caution"]
    assert f["loading"]["g_day"] == [10 * W, 12 * W]
    kinds = {e.get("stop_type") for e in f["schedule"] if e["kind"] == "aid"}
    assert kinds == {"aid", "water", "medical", "big"}
    med = next(e for e in f["schedule"] if e.get("stop_type") == "medical")
    assert "不補給" in med["action"] and med["water_ml"] is None
    water = next(e for e in f["schedule"] if e.get("stop_type") == "water")
    assert "「20K」" in water["action"] and "吃" not in water["action"].split("；")[0]
    # carry legs: from each station with water to the next one (medical skipped)
    legs = f["water"]["legs"]
    assert [(lg["from_km"], lg["to_km"]) for lg in legs] == [(0, 10), (10, 20), (20, 30), (30, approx(42.195))]
    assert all(lg["carry_ml"][0] <= lg["carry_ml"][1] for lg in legs)
    # generic wording, no product names
    acts = " ".join(e["action"] for e in f["schedule"])
    assert "約 30 g 碳水" in acts and "SaltStick" not in acts
    assert any(s["fuel_action"] for s in p["segments"])


def test_plan_fuel_cool_half_is_drink_to_thirst(client):  # noqa: F811
    p = client.post("/api/v1/racepower/plan", json={"type": "road", "distance_km": 21.1, "start_time": "06:00",
                                                    "course": {"manual": {"km": 21.1, "split": "km"}},
                                                    "env_to": {"temp_c": 14, "rh_pct": 70},
                                                    "stops": [{"km": 10, "minutes": 1}]}).json()
    f = p["fuel"]
    assert f["category"] == "half" and f["water"]["thirst"] and f["water"]["per_h"] is None
    assert f["cho"]["per_h"] == [30.0, 60.0] and f["sodium"]["per_h"] == [0.0, 0.0]
    assert any("口渴就喝" in e["action"] for e in f["schedule"])


def test_plan_fuel_trail_gpx_and_station_suggestions(client):  # noqa: F811
    tr = synthetic_track({"len": 24000, "z": lambda x: 300 + (x * 0.1 if x < 9000 else max(0, 900 - (x - 9000) * 0.06))})
    tr.wpts = [{"name": "CP1 補給站", "lat": tr.lat[800], "lon": tr.lon[800]}]
    up = client.post("/api/v1/racepower/course",
                     files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()
    assert up["stop_suggestions"] and up["stop_suggestions"][0]["type"] == "aid"
    assert up["stop_suggestions"][0]["km"] == approx(8.0, abs=0.2)
    p = client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": up["course_id"]},
                                                    "start_time": "06:00", "stops": [{"km": 8, "type": "aid"}]}).json()
    f = p["fuel"]
    assert "minetti" in f["methods"] and f["band_rel"] > 0.10
    assert p["stop_suggestions"] == up["stop_suggestions"]
    fl = f["crosscheck"]["fletcher"]
    assert fl["range"][0] < fl["kcal"] < fl["range"][1]
    for e in f["schedule"]:
        if e["kind"] == "fuel":
            s = next(x for x in p["segments"] if x["i"] == e["seg"])
            assert s["grade"] >= FU.STEEP_DOWN_NO_FUEL


def test_plan_fuel_baiyue_daily_and_csv_columns(client):  # noqa: F811
    tr = synthetic_track({"len": 16000, "z": lambda x: 2600 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("h.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    body = {"type": "baiyue", "course": {"course_id": cid}, "day_splits_km": [8], "start_time": "05:30"}
    p = client.post("/api/v1/racepower/plan", json=body).json()
    f = p["fuel"]
    assert f["category"] == "hike" and f["methods"].keys() == {"pandolf"}
    assert len(f["daily"]) == 2 and all(d["total"][0] <= d["total"][1] for d in f["daily"])
    assert f["body_src"] == {"height_cm": "推估", "age": "推估", "sex": "推估"}      # fake inputs: no profile
    assert any("假設值" in w for w in f["warnings"])
    assert f["kcal_band"][0] <= f["kcal"] <= f["kcal_band"][1] and f["kcal_yamamoto"] > 0
    assert f["cho"]["per_h"] == [30.0, 50.0] and f["loading"]["kind"] == "normal"
    assert sum(1 for e in f["schedule"] if e["kind"] == "start") == 2
    r = client.post("/api/v1/racepower/export/csv", json=body)
    head, cols, rows = read_csv(r)
    kv = {x[0]: x[1:] for x in head if x}
    assert kv["預估熱量 kcal"][0].endswith("kcal") and kv["碳水 g/h"][0] == "30–50 g/h"
    for c in ("熱量 kcal", "累積 kcal", "碳水 g", "水 ml", "鈉 mg", "補給動作"):
        assert c in cols
    ci = cols.index("熱量 kcal")
    assert sum(float(x[ci]) for x in rows[:-1]) == approx(float(rows[-1][ci]), abs=len(rows))
    assert any(x[cols.index("補給動作")] for x in rows[:-1])


def test_csv_road_fuel_header_and_typed_stops(client):  # noqa: F811
    body = {"type": "road", "distance_km": 42.195, "course": {"manual": {"km": 42.195, "split": "km"}},
            "start_time": "07:00", "stops": [{"km": 21, "minutes": 1, "type": "water", "name": "半程"}]}
    head, cols, rows = read_csv(client.post("/api/v1/racepower/export/csv", json=body))
    kv = {x[0]: x[1:] for x in head if x}
    assert kv["補給站"] == ["21 km 1 分 水站 半程"]
    assert kv["碳水 g/h"][0] == "60–90 g/h" and kv["賽前超補"][0] == "前一天 10–12 g/kg"
    assert "ml/h" in kv["水 ml/h"][0]


def test_body_profile_prefers_settings_then_wko5():
    from backend.engine.racepower import athlete as A

    class Root(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)
    ath = types.SimpleNamespace(root=Root({3001: Root({3017: "female", 3033: "1991-11-30T00:00:00"})}),
                                settings={"height": [(dt.date(2020, 1, 1), 1.68)]})
    ds = types.SimpleNamespace(plan=types.SimpleNamespace(profile={"height_cm": 172}), athlete=ath)
    b = A.body_profile(ds, dt.date(2026, 10, 2))
    assert b["height_cm"] == 172 and b["height_cm_src"] == "設定頁"
    assert b["sex"] == "female" and b["age"] == 34 and b["age_src"] == "WKO5 生日"
    empty = A.body_profile(types.SimpleNamespace(plan=types.SimpleNamespace(profile={}), athlete=None), dt.date(2026, 1, 1))
    assert empty["height_cm"] is None and empty["age"] is None


def test_manual_course_has_one_power_segment():
    c = CO.manual_course(10.0)
    assert len(c["segments"]) == 1
    seg = {**c["segments"][0], "power": 250.0, "t": 2700.0}
    assert FU.run_energy([seg], 65.0)[0]["method"] == "power"
