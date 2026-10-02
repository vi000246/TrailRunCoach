"""
賽事功率: per-segment executable targets for trail and 百岳 plans
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


@pytest.mark.parametrize("g,walk,kind", [(-0.10, None, "descent"), (0.0, None, "flat"), (0.05, None, "run_climb"),
                                         (0.05, "走跑皆可", "steep_climb"), (0.12, None, "steep_climb")])
def test_kind_by_grade(g, walk, kind):
    assert ST.kind_of(seg(g, walk=walk)) == kind


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
