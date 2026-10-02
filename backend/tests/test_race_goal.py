"""
賽事計算機 goal (engine/racepower/goal.py): the goal kind follows the training
basis (hr → pace, power → power), and a goal is compared with the model's own
prediction on the same course. Synthetic data only (the v2 fixture's athlete).
"""
import pytest

from backend.engine.racepower import goal as GOAL
from backend.tests.test_race_calculator import _upload
from backend.tests.test_racepower_v2 import client  # noqa: F401  (fixture)

approx = pytest.approx


def test_basis_follows_target_basis_then_use_power():
    assert GOAL.basis("hr", True)["basis"] == "hr"
    assert GOAL.basis("power", False)["basis"] == "power"
    assert GOAL.basis("auto", False) == {"basis": "hr", "how": "use_power"}
    assert GOAL.basis("auto", True)["basis"] == "power"
    assert GOAL.basis(None, None)["basis"] == "power"       # unknown = the long-standing default


def test_check_levels():
    assert GOAL.check(3600, 3600, "time")["level"] == "ok"
    g = GOAL.check(3600, 3600 * 1.05, "time")
    assert g["faster"] == approx(0.05) and g["level"] == "fast" and "快 5%" in g["message"]
    assert GOAL.check(3600, 3600 * 1.10, "time")["level"] == "too_fast"
    assert GOAL.check(3600 * 1.2, 3600, "power")["level"] == "slow"
    assert GOAL.check(3600, None, "time")["faster"] is None


def test_goal_basis_endpoint(client, monkeypatch):   # noqa: F811
    from backend.api import racepower as RP
    monkeypatch.setattr(RP, "_goal_settings", lambda: ("auto", False, False))
    assert client.get("/api/v1/racepower/goal-basis").json()["basis"] == "hr"
    monkeypatch.setattr(RP, "_goal_settings", lambda: ("power", False, False))
    assert client.get("/api/v1/racepower/goal-basis").json() == {"basis": "power", "how": "target_basis"}


def test_plan_without_goal_has_no_goal_block(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    p = client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": cid}}).json()
    assert "goal" not in p and p["summary"]["mode"] == "auto"


def test_pace_goal_spreads_by_grade_and_warns_when_too_fast(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    base = client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": cid}}).json()
    t_model, km = base["summary"]["time_s"], base["summary"]["km"]
    fast = t_model / 1.10
    p = client.post("/api/v1/racepower/plan", json={"type": "trail", "mode": "time", "course": {"course_id": cid},
                                                     "target_pace_s_per_km": fast / km}).json()
    g = p["goal"]
    assert p["summary"]["time_s"] == approx(fast, rel=0.01)
    assert g["model_time_s"] == approx(t_model, rel=0.01) and g["faster"] == approx(0.10, abs=0.01)
    assert g["level"] == "too_fast"
    # per-segment paces follow the grade: the steep climb is slower than the flat start
    segs = p["segments"]
    flat = next(s for s in segs if abs(s["grade"]) < 0.02)
    steep = next(s for s in segs if s["grade"] > 0.08)
    assert steep["pace_s_per_km"] > flat["pace_s_per_km"]
    assert sum(r["t"] for r in p["chart_rows"]) == approx(p["summary"]["time_s"], rel=0.01)


def test_power_goal_gives_a_finish_time(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    base = client.post("/api/v1/racepower/plan", json={"type": "road", "course": {"course_id": cid}}).json()
    p = client.post("/api/v1/racepower/plan", json={"type": "road", "mode": "power", "course": {"course_id": cid},
                                                     "target_pct_cp": base["summary"]["pct_cp"] * 0.9}).json()
    g = p["goal"]
    assert g["mode"] == "power" and p["summary"]["time_s"] > base["summary"]["time_s"]
    assert g["faster"] < 0 and g["level"] in ("ok", "slow")
