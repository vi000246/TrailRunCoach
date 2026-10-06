"""
賽事計算機: walk or run from grade × speed (engine/racepower/runwalk.py, SP-226;
docs/research/run-walk-threshold.md §3.1, §5.1, §5.3).
"""
from __future__ import annotations

import math

import pytest

from backend.engine.racepower import fuel as FU
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import runwalk as RW
from backend.engine.racepower import seg_targets as ST
from backend.tests.test_racepower_export import client  # noqa: F401  (fixture)
from backend.tests.test_racepower_v2 import synthetic_track

approx = pytest.approx


def g_of(deg: float) -> float:
    return math.tan(math.radians(deg))


@pytest.mark.parametrize("deg,p,e", [(0, 1.95, 2.14), (5, 1.78, 1.99), (10, 1.62, 1.78), (15, 1.47, 1.51)])
def test_brill_kram_2021_eight_numbers(deg, p, e):
    """The four grades' preferred and energetically optimal speeds (m/s, belt), ≤ 0.05 m/s."""
    assert RW.pts(g_of(deg)) == approx(p, abs=0.05)
    assert RW.eots(g_of(deg)) == approx(e, abs=0.05)
    assert RW.pts(g_of(deg)) <= RW.eots(g_of(deg))


def test_finiel_2026_and_ortiz_2017():
    """Finiel: the preferred transition at 7.2° is 1.76 m/s; Ortiz: walk and run cost the same at 0.8 m/s on 30°."""
    assert RW.pts(g_of(7.2)) == approx(1.76, abs=0.05)
    assert RW.pts(g_of(30)) == approx(0.80, abs=0.05) and RW.eots(g_of(30)) == approx(0.80, abs=0.05)
    # beyond 30°: the same vertical speed (0.4 m/s = 1,440 m/h)
    assert RW.pts(g_of(40)) * math.sin(math.radians(40)) == approx(0.40, abs=1e-9)


def test_the_curves_fall_with_the_grade():
    gs = [x / 100 for x in range(3, 120)]
    for a, b in zip(gs, gs[1:]):
        assert RW.pts(b) <= RW.pts(a) + 1e-12 and RW.eots(b) <= RW.eots(a) + 1e-12


@pytest.mark.parametrize("vam,pts_pct,eots_pct", [(500, 8.0, 7.0), (700, 11.0, 10.0), (900, 15.0, 13.5), (1400, 28.0, 26.5)])
def test_research_table_climbing_speed_to_grade(vam, pts_pct, eots_pct):
    """§3.2: at this climbing rate, walking pays from about this grade on (± 1 %)."""
    gs = [x / 1000 for x in range(30, 600)]
    walk_from = next(g for g in gs if RW.gait(g, vam / 3600 / g) == "walk")
    either_from = next(g for g in gs if RW.gait(g, vam / 3600 / g) != "run")
    assert walk_from * 100 == approx(pts_pct, abs=1.0)
    assert either_from * 100 == approx(eots_pct, abs=1.0)


def test_three_answers_and_only_on_climbs():
    g = g_of(10)
    h = lambda belt: RW.horizontal(g, belt)            # noqa: E731
    assert RW.gait(g, h(1.50)) == "walk"
    assert RW.gait(g, h(1.70)) == "either"
    assert RW.gait(g, h(1.90)) == "run"
    # 15–30°: the band narrows to nothing at 30°
    assert RW.gait(g_of(30), RW.horizontal(g_of(30), 0.79)) == "walk"
    assert RW.gait(g_of(30), RW.horizontal(g_of(30), 0.81)) == "run"
    assert RW.gait(0.029, 0.5) is None and RW.gait(-0.10, 0.5) is None and RW.gait(0.0, 0.5) is None
    assert RW.gait(0.10, None) is None and RW.gait(0.10, 0.0) is None and RW.gait(None, 1.0) is None
    assert RW.belt_speed(0.2, RW.horizontal(0.2, 1.3)) == approx(1.3)


def test_shift_moves_both_curves():
    g = g_of(10)
    assert RW.pts(g, 0.2) == approx(RW.pts(g) + 0.2) and RW.eots(g, -0.2) == approx(RW.eots(g) - 0.2)
    v = RW.horizontal(g, 1.70)
    assert RW.gait(g, v) == "either" and RW.gait(g, v, shift=0.2) == "walk" and RW.gait(g, v, shift=-0.2) == "run"
    assert RW.pts(g_of(60), -5.0) == RW.MIN_SPEED


def test_labels():
    assert [RW.label(x) for x in ("walk", "either", "run", None)] == ["走", "走跑皆可", "跑", None]
    assert [RW.walk_label(x) for x in ("walk", "either", "run", None)] == ["走", "走跑皆可", None, None]


def seg(g, **kw):
    return {"i": 1, "grade": g, "t": 1200.0, "gain_m": max(0.0, g) * 2000, "loss_m": 0.0,
            "power": 250.0, "pace_s_per_km": 600.0, "dist_m": 2000.0, **kw}


def test_steep_climb_run_keeps_the_hr_target_and_says_run():
    """> 8 % run by the function: 陡坡（跑）, HR cap + VAM, no power target (SP-226)."""
    segs = [seg(0.12, i=1, gait="run"), seg(0.12, i=2, gait="walk"), seg(0.12, i=3, gait="either"),
            seg(0.05, i=4, gait="walk"), seg(0.05, i=5, gait="either"), seg(0.05, i=6, gait="run")]
    plan = {"type": "trail", "segments": segs, "used": {"cp": {"value": 300.0}}, "summary": {"time_s": 7200.0}}
    t = ST.plan_targets(plan, aet=145.0, lthr=165.0)
    assert [x["label"] for x in t] == ["陡坡（跑）", "陡坡（走）", "陡坡（走跑皆可）", "爬坡（走）", "可跑的爬坡", "可跑的爬坡"]
    assert [x["kind"] for x in t] == ["steep_climb"] * 4 + ["run_climb"] * 2
    run = t[0]
    assert run["basis"] == "hr" and run["chips"][0]["text"] == "心率 ≤ 165" and "VAM" in run["text"] and "W" not in run["text"]
    assert t[4]["basis"] == "power"
    rows = ST.chart_rows(plan, aet=145.0, lthr=165.0)
    assert [r["walk"] for r in rows] == [False, True, False, True, False, False]
    assert [r["gait"] for r in rows] == ["run", "walk", "either", "walk", "either", "run"]
    assert rows[0]["power"] is None and rows[0]["power_ref"] == 250.0 and rows[0]["label"] == "陡坡（跑）"


def test_no_gait_keeps_the_grade_rule():
    """百岳 and manual courses carry no gait: a steep climb is walked, as before."""
    plan = {"type": "baiyue", "segments": [seg(0.12, i=1), seg(0.05, i=2)], "summary": {"hr_cap": 140.0}}
    t = ST.plan_targets(plan, aet=140.0)
    assert [x["label"] for x in t] == ["陡坡（走）", "可跑的爬坡"]
    assert [r["walk"] for r in ST.chart_rows(plan, aet=140.0)] == [True, False]


def test_fuel_walking_cost_only_when_walked():
    base = {"grade": 0.12, "power": 255.0, "t": 900.0, "dist_m": 1500.0}
    e = FU.run_energy([dict(base, gait="walk"), dict(base, gait="either"), dict(base, gait="run"),
                       dict(base, grade=0.06, gait="either"), dict(base, grade=0.06, gait="walk")], 65.0, 2.0)
    assert [x["method"] for x in e] == ["minetti_walk", "minetti", "minetti", "power", "minetti_walk"]
    assert e[0]["kcal"] < e[1]["kcal"]                 # walking costs less on the same climb (Minetti 2002)


def _plan(client, monkeypatch=None, gait=None):   # noqa: F811
    tr = synthetic_track({"len": 16000, "z": lambda x: 300 + (x * 0.14 if x < 8000 else (16000 - x) * 0.14)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    if gait is not None:
        monkeypatch.setattr(RW, "gait", lambda g, v, shift=0.0: gait if g >= RW.MIN_GRADE else None)
    return client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": cid}}).json()


def test_plan_labels_follow_the_predicted_speed(client):   # noqa: F811
    p = _plan(client)
    climbs = [s for s in p["segments"] if s["grade"] >= 0.03]
    assert climbs
    for s in climbs:
        assert s["gait"] == RW.gait(s["grade"], s["speed_ms"])
        assert s["walk"] == RW.walk_label(s["gait"])
        assert (s["walk"] in s["notes"]) if s["walk"] else not any(n in ("走", "走跑皆可") for n in s["notes"])
    assert all(s["gait"] is None for s in p["segments"] if s["grade"] < 0.03)


def test_the_labels_never_change_the_time(client, monkeypatch):   # noqa: F811
    """SP-226 changes labels, targets and food only: the predicted times are the same whatever the gait."""
    walk = _plan(client, monkeypatch, "walk")
    run = _plan(client, monkeypatch, "run")
    assert walk["summary"]["time_s"] == approx(run["summary"]["time_s"], rel=1e-12)
    assert [s["t"] for s in walk["segments"]] == approx([s["t"] for s in run["segments"]], rel=1e-12)
    assert {s["gait"] for s in walk["segments"] if s["grade"] >= 0.03} == {"walk"}
    # the food does follow the gait: walking the climbs costs less
    assert walk["fuel"]["kcal"] < run["fuel"]["kcal"]
    assert walk["fuel"]["methods"].get("minetti_walk") and not run["fuel"]["methods"].get("minetti_walk")
