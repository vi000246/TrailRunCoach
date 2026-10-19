"""
SP-700 — the race calculator's GPX split is readable by default:

1. the default shortest segment is max(500 m, 3 % of the course) and the grade split has at most
   MAX_SEGMENTS segments (past that the shortest segment grows); a shortest segment set in the
   advanced options is kept as given (it can still split finer);
2. a power lock (by segment number) that no longer points at the same ground after a new split is
   dropped, with a warning and the dropped numbers in the plan's summary;
3. no 「控制、安全」 text: descents have no target chip, the watch step is named 下坡.
"""
from __future__ import annotations

import math

from pytest import approx

from backend.engine.racepower import course as CO
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import planner as PL
from backend.engine.racepower import watch_export as WE
from backend.tests.test_racepower_export import client  # noqa: F401  (fixture)
from backend.tests.test_racepower_v2 import synthetic_track


def _bumpy(km=24.0):
    """A mountain route with many short ups and downs (the 北插天山 kind)."""
    return synthetic_track({"len": km * 1000, "z": lambda x: 600 + 400 * math.sin(x / 6000.0)
                            + 35 * math.sin(x / 260.0) + 20 * math.sin(x / 110.0)})


def test_default_shortest_segment_is_500_m_or_3_percent():
    assert CO.default_min_len(10_000) == approx(500.0)
    assert CO.default_min_len(24_000) == approx(720.0)
    assert CO.default_min_len(100_000) == approx(3000.0)


def test_default_split_is_capped_and_the_advanced_option_still_splits_finer():
    tr = _bumpy()
    fine = CO.build_course(tr, min_len_m=200.0)                    # the old default length, set by hand
    c = CO.build_course(tr)
    assert len(fine["segments"]) > CO.MAX_SEGMENTS                 # the route really is bumpy
    assert len(c["segments"]) <= CO.MAX_SEGMENTS
    assert c["opts"]["min_len_m"] >= CO.default_min_len(c["totals"]["km"] * 1000) - 1e-6
    assert sum(s["dist_m"] for s in c["segments"]) == approx(c["totals"]["km"] * 1000, abs=1)
    assert fine["opts"]["min_len_m"] == 200.0                      # kept as given, no cap


def test_lock_map_drops_locks_whose_segment_moved():
    segs = [{"start_km": 0.0}, {"start_km": 1.2}, {"start_km": 2.5}]
    keep, dropped = PL.lock_map([
        {"seg": 2, "power": 240.0, "km": 1.2, "n": 3},          # same place → kept
        {"seg": 3, "power": 230.0, "km": 2.1, "n": 3},          # segment 3 starts elsewhere now → dropped
        {"seg": 1, "power": 250.0, "n": 5},                     # set on a 5-segment split → dropped
        {"seg": 9, "power": 250.0},                             # no such segment → dropped
        {"seg": 1, "power": 0.0},                               # no power: not a lock
    ], segs)
    assert keep == {1: 240.0} and dropped == [3, 1, 9]
    assert PL.lock_map([{"seg": 1, "power": 250.0}], segs) == ({0: 250.0}, [])     # no place: as before


def test_plan_drops_a_stale_lock_and_says_so(client):  # noqa: F811
    tr = synthetic_track({"len": 16000, "z": lambda x: 300 + (x * 0.1 if x < 8000 else (16000 - x) * 0.1)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    body = {"type": "trail", "mode": "power", "target_power": 230, "course": {"course_id": cid}}
    p = client.post("/api/v1/racepower/plan", json=body).json()
    seg2 = p["segments"][1]
    ok = client.post("/api/v1/racepower/plan", json={**body, "locks": [
        {"seg": 2, "power": 200, "km": seg2["start_km"], "n": len(p["segments"])}]}).json()
    assert ok["summary"]["locks_dropped"] == [] and ok["segments"][1]["power"] == approx(200, abs=1)
    stale = client.post("/api/v1/racepower/plan", json={**body, "locks": [
        {"seg": 2, "power": 200, "km": seg2["start_km"] + 3.0}]}).json()
    assert stale["summary"]["locks_dropped"] == [2]
    assert any("功率鎖定" in str(w) for w in stale["warnings"])
    assert stale["segments"][1]["power"] == approx(p["segments"][1]["power"], rel=1e-6)


def test_descent_watch_step_is_named_just_downhill():
    rows = [{"start_km": i * 1.0, "end_km": (i + 1) * 1.0, "dist_m": 1000.0, "t": 300.0, "grade": -0.10,
             "gain_m": 0.0, "loss_m": 100.0, "basis": "safe", "power": None, "hr_cap": 160.0} for i in range(3)]
    for mode in ("lap", "distance"):
        legs = WE.steps_for({"type": "trail"}, rows, mode=mode)["legs"]
        assert legs and all(lg["name"].endswith(" 下坡") and "安全" not in lg["name"] for lg in legs)
