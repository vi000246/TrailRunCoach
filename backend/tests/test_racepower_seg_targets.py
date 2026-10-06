"""
賽事計算機: per-segment executable targets for trail and 百岳 plans
(engine/racepower/seg_targets.py; vo2max-gate-and-trail-metric.md §2.3).
"""
from __future__ import annotations

import pytest

from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import seg_targets as ST
from backend.tests.test_racepower_export import client, read_csv  # noqa: F401  (fixture)
from backend.tests.test_racepower_v2 import synthetic_track

approx = pytest.approx


def seg(g, **kw):
    return {"i": 1, "grade": g, "t": 1200.0, "gain_m": max(0.0, g) * 2000, "loss_m": max(0.0, -g) * 2000,
            "power": 250.0, "pace_s_per_km": 360.0, "dist_m": 2000.0, **kw}


@pytest.mark.parametrize("g,gait,kind", [(-0.10, None, "descent"), (0.0, None, "flat"), (0.05, None, "run_climb"),
                                         (0.05, "walk", "steep_climb"), (0.05, "either", "run_climb"),
                                         (0.12, None, "steep_climb"), (0.12, "run", "steep_climb")])
def test_kind_by_grade(g, gait, kind):
    assert ST.kind_of(seg(g, gait=gait)) == kind


def test_trail_targets_follow_the_policy():
    segs = [seg(0.05, i=1), seg(0.15, i=2), seg(-0.12, i=3), seg(0.0, i=4)]
    plan = {"type": "trail", "segments": segs, "used": {"cp": {"value": 300.0}},
            "summary": {"time_s": 2 * 3600.0}}
    t = ST.plan_targets(plan, aet=145.0, lthr=165.0)
    assert [x["mark"] for x in t] == ["①", "②", "③", "④"]
    run, steep, down, flat = t
    assert run["basis"] == "power" and run["chips"][0]["text"] == "242–258 W（81%–86% CP）"
    assert any("心率上限 165" in c["text"] for c in run["chips"])                     # ≤ 3 h: LTHR cap
    assert steep["basis"] == "hr" and steep["chips"][0]["text"] == "心率 ≤ 165"
    assert steep["vam"] == approx(300 / 1200 * 3600) and any("VAM 900" in c["text"] for c in steep["chips"])
    assert down["basis"] == "none" and down["text"] == "控制、安全" and down["ref"] == ["配速參考 6:00 /km"]
    assert flat["basis"] == "power" and segs[0]["target"]["n"] == 1
    # a long race caps at AeT
    plan["summary"]["time_s"] = 5 * 3600.0
    assert ST.plan_targets(plan, aet=145.0, lthr=165.0)[1]["hr_cap"] == 145.0
    assert ST.plan_targets({"type": "road", "segments": [seg(0.0)]}) is None


def test_baiyue_targets_are_hr_and_vam_never_pace():
    segs = [seg(0.15, i=1, day=1), seg(-0.15, i=2, day=1), seg(0.0, i=3, day=2)]
    t = ST.plan_targets({"type": "baiyue", "segments": segs, "summary": {"hr_cap": 140.0}}, aet=140.0)
    assert t[0]["chips"][0]["text"] == "心率 ≤ 140" and "VAM" in t[0]["text"]
    assert t[1]["text"] == "控制、安全" and t[2]["basis"] == "hr"
    assert all(not x["ref"] and "配速" not in x["text"] for x in t)


def test_fuel_summary_counts_points_and_keeps_stations():
    s = seg(0.05, i=2)
    plan = {"type": "trail", "fuel": {"schedule": [
        {"seg": 2, "kind": "fuel", "cho_g": 25.0, "action": "約 25 g 碳水"},
        {"seg": 2, "kind": "fuel", "cho_g": 25.0, "action": "約 25 g 碳水"},
        {"seg": 2, "kind": "aid", "action": "補給站「CP1」：補水、吃"},
        {"seg": 3, "kind": "fuel", "cho_g": 25.0, "action": "x"}]}}
    assert ST.fuel_summary(plan, s) == "吃 2 次（每次約 25 g 碳水）；補給站「CP1」：補水、吃"
    assert ST.fuel_summary(plan, seg(0.0, i=9, fuel_action="")) == ""


def test_api_trail_plan_and_csv_carry_the_targets(client):  # noqa: F811
    tr = synthetic_track({"len": 16000, "z": lambda x: 300 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    body = {"type": "trail", "course": {"course_id": cid}, "start_time": "06:00", "stops": [{"km": 8, "type": "aid"}]}
    p = client.post("/api/v1/racepower/plan", json=body).json()
    assert p["seg_targets"] and len(p["seg_targets"]) == len(p["segments"])
    assert {x["kind"] for x in p["seg_targets"]} >= {"steep_climb", "descent"}
    assert any(x["fuel_action"] for x in p["seg_targets"])
    head, cols, rows = read_csv(client.post("/api/v1/racepower/export/csv", json=body))
    ci, ti = cols.index("目標類型"), cols.index("執行目標")
    assert {r[ci] for r in rows[:-1]} >= {"心率", "不設目標"}
    assert any("控制、安全" in r[ti] for r in rows[:-1])
    road = client.post("/api/v1/racepower/plan", json={"type": "road", "distance_km": 10}).json()
    assert road["seg_targets"] is None


# ---- SP-244 「會用登山杖」: a hint on steep climbs and steep descents, never a number -------

def test_pole_hint_only_on_steep_climbs_and_steep_descents():
    def hint(g, gait=None, kind="trail"):
        s = seg(g, gait=gait)
        return ST.pole_hint(s, ST.kind_of(s), kind)
    assert hint(0.15)["key"] == "up" and hint(0.15, "walk")["text"] == "用杖：自覺比較輕鬆，速度差不多"
    assert hint(0.05, "walk")["key"] == "up"                          # a walked 3–8 % climb is steep_climb
    assert hint(0.15, "run") is None                                  # poles are for walking
    assert hint(-0.15)["text"] == "用杖：膝蓋負擔少 12–25 %" and "Schwameder" in hint(-0.20)["src"]
    assert "Giovanelli" in hint(0.15)["src"]
    assert hint(-0.10) is None and hint(0.0) is None and hint(0.05) is None       # gentle / flat / runnable
    assert hint(0.15, kind="road") is None and hint(0.15, kind="baiyue")["key"] == "up"


def test_chart_rows_carry_the_hint_only_when_the_race_is_marked():
    plan = {"type": "trail", "segments": [seg(0.15, i=1), seg(0.0, i=2), seg(-0.20, i=3), seg(-0.05, i=4)],
            "used": {"cp": {"value": 300.0}}, "summary": {"time_s": 2 * 3600.0}}
    ST.plan_targets(plan, aet=145.0, lthr=165.0)
    off = ST.chart_rows(plan, aet=145.0, lthr=165.0)
    on = ST.chart_rows(plan, aet=145.0, lthr=165.0, poles=True)
    assert all(r["pole_hint"] is None for r in off)
    assert [(r["pole_hint"] or {}).get("key") for r in on] == ["up", None, "down", None]
    strip = lambda rows: [{k: v for k, v in r.items() if k != "pole_hint"} for r in rows]   # noqa: E731
    assert strip(on) == strip(off)                                    # pace, power, HR cap unchanged


def test_api_poles_change_no_time_pace_or_hr(client):  # noqa: F811
    tr = synthetic_track({"len": 12000, "z": lambda x: 300 + (x * 0.2 if x < 6000 else (12000 - x) * 0.2)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    body = {"type": "trail", "course": {"course_id": cid}, "start_time": "06:00"}
    off = client.post("/api/v1/racepower/plan", json=body).json()
    on = client.post("/api/v1/racepower/plan", json={**body, "poles": True}).json()
    assert on["summary"]["time_s"] == off["summary"]["time_s"]
    assert [s["pace_s_per_km"] for s in on["segments"]] == [s["pace_s_per_km"] for s in off["segments"]]
    assert [r["hr_cap"] for r in on["chart_rows"]] == [r["hr_cap"] for r in off["chart_rows"]]
    assert all(r["pole_hint"] is None for r in off["chart_rows"])
    keys = {r["pole_hint"]["key"] for r in on["chart_rows"] if r["pole_hint"]}
    assert keys == {"up", "down"}
    assert all(r["pole_hint"] is None for r in on["chart_rows"] if r["kind"] in ("flat", "run_climb"))


def test_event_keeps_the_poles_choice():
    from backend.engine import planning as P
    plan = P.Plan()
    assert plan.upsert_event({"name": "x", "date": "2026-11-01"}).poles is False
    e = plan.upsert_event({"name": "y", "date": "2026-11-01", "poles": True})
    assert e.poles is True and P.event_json(e, e.start)["poles"] is True
