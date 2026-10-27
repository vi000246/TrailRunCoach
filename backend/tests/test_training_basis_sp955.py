"""SP-955: 設定 › 訓練依據 — 「現在用什麼練」 (engine/training_basis.py, GET /api/v1/overview/plan/basis) and the
課表偏好 fields that moved to the settings page (PATCH /api/v1/overview/plan/prefs: 目標依據, 可跑坡目標, AeT／CP
測試方式, one at a time, the rest of the stored preferences kept).

The rows follow 主要訓練項目 (越野: trail days, hill repeats, long climbs, the steep-hill walk, downhill, B2B; 路跑:
the road long run and the marathon-pace segment); the basis of each is target_policy's under the week plan's power
state (SP-950: ① power, ② stale, ③ no meter). Synthetic thresholds only."""
from __future__ import annotations

from dataclasses import replace

import pytest

from backend.engine import plan_prefs as PP
from backend.engine import power_state as PST
from backend.engine import training_basis as TB

HRZ = {"model": "lthr", "easy": [133, 154], "targets": {"threshold": [161, 166], "vo2": [172, 180], "hill": [161, 170],
                                                         "climb": [150, 166]}}


def _th(state: str, cp=195.0) -> dict:
    ps = PST.state(__import__("datetime").date(2026, 10, 27), None, source="none" if state == "none" else "stryd",
                   how="設定", last_cp_test="2026-10-15" if state == "power" else "2026-08-01", cp=cp)
    assert ps["state"] == state
    return {"cp": cp if state == "power" else None, "lthr": 166.0, "aet": 154.0, "hr_model": HRZ, "power_model": None,
            "power_state": ps, "walk_cap": {"value": 142, "rpe": 13}, "tpace": 408.0}


def _by(rows):
    return {r["key"]: r for r in rows}


def test_the_kinds_follow_the_main_sport():
    trail = [r["key"] for r in TB.rows("trail", _th("power"))]
    road = [r["key"] for r in TB.rows("road", _th("power"), mp_goal_pace_s=340)]
    assert trail == ["easy", "trail_easy", "trail_long", "z3", "z5", "hill", "climb", "walk", "downhill", "b2b"]
    assert road == ["easy", "long", "z3", "z5", "mp"]
    # 越野 has the climbing, 路跑 the marathon-pace segment — never the other's
    assert "mp" not in trail and not {"hill", "climb", "walk", "downhill", "b2b"} & set(road)
    mp = _by(TB.rows("road", _th("power"), mp_goal_pace_s=340))["mp"]
    assert mp["basis"] == "pace" and mp["target"] == "5:40 /km（目標配速）"


def test_power_in_effect_runs_the_flat_sessions_by_power_with_the_hr_cap():
    r = _by(TB.rows("trail", _th("power")))
    assert r["easy"]["basis"] == "power" and "W" in r["easy"]["target"] and "心率 ≤ 154 bpm 當保險" in r["easy"]["target"]
    assert r["z3"]["basis"] == "power" and r["hill"]["basis"] == "power" and "W" in r["z5"]["target"]
    # the trail days stay heart rate; the long climb and downhill have no target
    assert r["trail_easy"]["basis"] == "hr" and r["trail_easy"]["target"] == "心率 ≤ 154 bpm"
    assert r["walk"]["target"] == "心率 ≤ 142 bpm 或 RPE ≤ 13"
    assert r["climb"]["basis"] == "none" and r["downhill"]["basis"] == "none" and r["downhill"]["target"] == "看下降量和技術"
    assert not any(x["manual"] for x in r.values())
    assert r["easy"]["name"] == "路跑輕鬆跑・恢復跑"


@pytest.mark.parametrize("state", ["stale", "none"])
def test_without_power_nothing_is_run_by_power(state):
    rows = TB.rows("road", _th(state), mp_goal_pace_s=None)
    assert all(r["basis"] != "power" for r in rows)
    assert not any("W" in r["target"] for r in rows), [r["target"] for r in rows]
    r = _by(rows)
    assert r["easy"]["basis"] == "hr" and r["easy"]["target"] == "心率 ≤ 154 bpm"
    # SP-951: without power the flat Zone 3 runs by pace (% threshold pace) with the HR cap
    assert r["z3"]["basis"] == "pace" and r["z3"]["target"].startswith("配速 ") and "閾值配速" in r["z3"]["target"]
    assert r["z3"]["target"].endswith("心率 ≤ 166 bpm")
    assert ("做一次 CP 測試就切回功率" in r["z3"]["why"]) == (state == "stale")
    assert r["mp"]["target"] == "照閾值配速推估"


def test_a_chosen_basis_is_marked_manual():
    p = replace(PP.Prefs(), target_basis="hr")
    r = _by(TB.rows("trail", _th("power"), p))
    assert r["z3"]["basis"] == "hr" and r["z3"]["manual"] and r["easy"]["manual"]
    assert not r["climb"]["manual"] and not r["downhill"]["manual"]     # their own rules, never the choice
    # ③: a stored 功率 is ignored (and still says it was chosen)
    p = replace(PP.Prefs(), target_basis="power")
    r = _by(TB.rows("trail", _th("none"), p))
    # SP-951: the flat Zone 3 then runs by pace (not power), still marked as chosen
    assert r["z3"]["basis"] == "pace" and r["z3"]["manual"] and "沒有功率計" in r["z3"]["why"]


def test_summary_phase_week_structure_and_next_test():
    inp = {"today": "2026-10-27", "phase": {"kind": "base", "label": "基礎期", "start": "2026-09-23", "end": "2027-01-10"},
           "cur": {"week": {"start": "2026-10-26"}, "mode_label": "基礎期", "primary_sport": "trail",
                   "sessions": [{"kind": "easy", "day": "2026-10-27"}, {"kind": "easy", "day": "2026-10-29"},
                                {"kind": "quality", "day": "2026-10-28"}, {"kind": "mountain", "day": "2026-11-01"},
                                {"kind": "strength", "day": "2026-10-27"}, {"kind": "strength", "day": "2026-10-30"},
                                {"kind": "rest", "day": "2026-10-31"}],
                   "test_suggestions": [{"title": "CP 測試"}]},
           "weeks": [{"sessions": [{"kind": "test", "title": "AeT 測試", "day": "2026-11-03"}]}]}
    s = TB.summary(inp)
    assert s["phase"]["week"] == 6 and s["phase"]["label"] == "基礎期"          # Monday 9/21 → 10/26: week 6
    assert s["week"] == {"runs": 4, "long": 1, "quality": 1, "strength": 2}
    assert s["next_test"] == {"title": "AeT 測試", "day": "2026-11-03", "suggested": False}
    inp["weeks"] = []
    assert TB.summary(inp)["next_test"] == {"title": "CP 測試", "day": None, "suggested": True}
    inp["cur"]["test_suggestions"] = []
    assert TB.summary(inp)["next_test"] is None


def test_pace_zones_at_the_threshold_pace():
    z = TB.pace_zones(400.0)
    assert [x["id"] for x in z] == ["Z1", "Z2", "Z3", "Z4", "Z5a", "Z5b", "Z5c"]
    assert z[0]["fast"] == pytest.approx(516.0) and z[0]["slow"] is None          # slower than 1.29 × threshold
    assert z[-1]["fast"] is None and z[-1]["slow"] == pytest.approx(360.0)
    assert TB.pace_zones(None) is None


# ---- the API ---------------------------------------------------------------------------------------

@pytest.fixture
def owner(monkeypatch, tmp_path):
    from backend.engine import plan_auto as PA
    from backend.tests.test_pwa_sp314 import _owner_client
    calls = []
    monkeypatch.setattr(PA, "after_thresholds", lambda *a, **k: calls.append("th"))
    with _owner_client(monkeypatch, tmp_path) as cl:
        cl.calls = calls
        yield cl


def test_patch_changes_one_field_and_keeps_the_rest(owner):
    cl = owner
    base = cl.get("/api/v1/overview/plan/prefs").json()["prefs"]
    put = cl.put("/api/v1/overview/plan/prefs", json={**base, "runs": 5, "aet_test_protocol": "ua40"})
    assert put.status_code == 200, put.text
    r = cl.patch("/api/v1/overview/plan/prefs", json={"cp_test_protocol": "standard"})
    assert r.status_code == 200, r.text
    p = cl.get("/api/v1/overview/plan/prefs").json()["prefs"]
    assert p["cp_test_protocol"] == "standard" and p["runs"] == 5 and p["aet_test_protocol"] == "ua40"
    assert cl.calls == []                                   # a test method re-plans nothing by itself
    assert cl.patch("/api/v1/overview/plan/prefs", json={"target_basis": "hr"}).json()["prefs"]["target_basis"] == "hr"
    assert cl.calls == ["th"]                               # a new 目標依據 re-plans / re-sends (after_thresholds)
    assert cl.patch("/api/v1/overview/plan/prefs", json={"target_basis": "hr"}).status_code == 200
    assert cl.calls == ["th"]                               # unchanged: nothing
    assert cl.patch("/api/v1/overview/plan/prefs", json={"climb_target": "effort_pace"}).json()["prefs"]["climb_target"] == "effort_pace"
    assert cl.get("/api/v1/overview/plan/prefs").json()["prefs"]["runs"] == 5


@pytest.mark.parametrize("body", [{"runs": 4}, {}, {"target_basis": "watts"}, {"climb_target": "x"}])
def test_patch_takes_only_the_moved_fields(owner, body):
    assert owner.patch("/api/v1/overview/plan/prefs", json=body).status_code == 400
