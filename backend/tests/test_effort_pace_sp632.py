"""
SP-632: the race calculator's 「這場的平地等效配速」 (the segments' GAP by time) and COROS Effort
Pace targets on runnable ground (−3 … +15 %, not walked, not technical, not high altitude):
racepower/watch_export puts the band on the leg's target (`ep`), workout_steps keeps it, and only
the COROS push sends it (intensityType 8, 推估) — over power only with 課表偏好「可跑坡目標」 =
Effort Pace. Garmin's payload is unchanged.
"""
from __future__ import annotations

import pytest

from backend.engine import plan_prefs as PP
from backend.engine import workout_steps as WS
from backend.engine.racepower import planner as PL
from backend.engine.racepower import watch_export as WE
from backend.sync import coros_workouts as CW
from backend.tests.test_race_calculator import _upload
from backend.tests.test_racepower_v2 import client  # noqa: F401  (fixture)

approx = pytest.approx
TH = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}


def _row(i, grade, *, gap=400.0, t=300.0, basis="hr", power=None, gait=None, walk=False, tech_f=None, z=500.0,
         kind=None):
    return {"start_km": float(i), "end_km": float(i + 1), "dist_m": 1000.0, "t": t, "grade": grade,
            "gain_m": max(0.0, grade) * 1000, "loss_m": max(0.0, -grade) * 1000, "basis": basis, "power": power,
            "hr_cap": 160.0, "gap_pace_s_per_km": gap, "gait": gait, "walk": walk, "tech_f": tech_f, "z_mean": z,
            "kind": kind or ("descent" if grade <= -0.03 else "steep_climb" if grade > 0.08 else
                             "run_climb" if grade >= 0.03 else "flat")}


# ---- 平地等效配速 -------------------------------------------------------------------

def test_flat_pace_is_the_gap_weighted_by_time():
    segs = [{"gap_pace_s_per_km": 300.0, "t": 600.0}, {"gap_pace_s_per_km": 400.0, "t": 1800.0},
            {"gap_pace_s_per_km": None, "t": 900.0}]
    assert PL.flat_pace(segs) == approx((300 * 600 + 400 * 1800) / 2400)
    assert PL.flat_pace([{"gap_pace_s_per_km": None, "t": 100.0}]) is None
    assert PL.flat_pace([]) is None


def test_trail_plan_has_the_flat_pace_and_the_rows_carry_what_the_export_reads(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    p = client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": cid}}).json()
    fp = p["summary"]["flat_pace_s_per_km"]
    segs = p["segments"]
    want = sum(s["gap_pace_s_per_km"] * s["t"] for s in segs) / sum(s["t"] for s in segs)
    assert fp == approx(want)
    rows = p["chart_rows"]
    assert all("gap_pace_s_per_km" in r and "z_mean" in r and "tech_f" in r for r in rows)
    # the technicality only on flats / descents (g ≤ +2 %)
    assert all(r["tech_f"] is None for r in rows if r["grade"] > 0.02)
    road = client.post("/api/v1/racepower/plan", json={"type": "road", "course": {"course_id": cid}}).json()
    assert road["summary"].get("flat_pace_s_per_km") is None


# ---- which ground gets Effort Pace ------------------------------------------------------

@pytest.mark.parametrize("grade,ok", [(-0.0301, False), (-0.03, True), (0.0, True), (0.08, True), (0.15, True),
                                      (0.1501, False), (-0.10, False), (0.25, False)])
def test_ep_grade_window_both_ends_in(grade, ok):
    assert WE.EP_GRADE == (-0.03, 0.15)
    assert WE.ep_ok(_row(0, grade)) is ok


def test_ep_not_on_walked_technical_or_high_ground():
    assert WE.ep_ok(_row(0, 0.05))
    assert not WE.ep_ok(_row(0, 0.05, gait="walk"))
    assert not WE.ep_ok(_row(0, 0.12, walk=True))                      # steep without a gait counts as walked
    assert WE.ep_ok(_row(0, 0.12, gait="run", walk=False))
    assert WE.EP_TECH_MIN == 0.85
    assert WE.ep_ok(_row(0, 0.0, tech_f=0.85)) and not WE.ep_ok(_row(0, 0.0, tech_f=0.849))
    assert WE.EP_ALT_MAX_M == 2800.0
    assert WE.ep_ok(_row(0, 0.05, z=2800.0)) and not WE.ep_ok(_row(0, 0.05, z=2801.0))
    assert not WE.ep_ok(_row(0, 0.05, gap=None))                       # no GAP (road / manual course)


def test_trail_legs_carry_the_ep_band_others_do_not():
    rows = [_row(0, 0.05, gap=400.0), _row(1, 0.05, gap=400.0),                  # runnable climb, HR
            _row(2, 0.25, gap=380.0, gait="walk", walk=True),                     # steep, walked
            _row(3, -0.10, gap=330.0, basis="safe")]                              # descent
    legs = WE.steps_for({"type": "trail"}, rows, mode="distance")["legs"]
    runnable = [lg for lg in legs if lg["end_km"] <= 2.0]
    assert runnable and all(lg["target"]["type"] == "hr" for lg in runnable)
    assert all(lg["target"]["ep"] == [round(400 * (1 - WE.EP_BAND)), round(400 * (1 + WE.EP_BAND))] for lg in runnable)
    assert all("ep" not in lg["target"] for lg in legs if lg["start_km"] >= 2.0)
    out = WE.steps_for({"type": "trail"}, rows)
    assert any(WE.EP_NOTE.split("（")[0] in n for n in out["notes"])
    # road and 百岳 never get it
    assert not any("ep" in lg["target"] for lg in WE.steps_for({"type": "road"}, rows)["legs"])
    assert not any("ep" in lg["target"] for lg in WE.steps_for({"type": "baiyue"}, rows)["legs"])


def test_a_merged_leg_needs_more_than_half_its_time_eligible():
    pc = {"t": 1000.0, "ept": 500.0, "epg": 500.0 * 400}
    assert WE.leg_ep(pc, "trail") is None
    pc = {"t": 1000.0, "ept": 501.0, "epg": 501.0 * 400}
    assert WE.leg_ep(pc, "trail") == [388, 412]
    assert WE.leg_ep(pc, "road") is None


def test_power_leg_keeps_power_and_carries_ep():
    rows = [_row(i, 0.04, basis="power", power=250.0) for i in range(3)]
    legs = WE.steps_for({"type": "trail"}, rows)["legs"]
    assert legs[0]["target"]["type"] == "power" and legs[0]["target"]["ep"]


# ---- stored, then pushed -------------------------------------------------------------

def _race_session(rows, mode=None, **kw):
    doc = WE.steps_for({"type": "trail"}, rows, mode=mode)["doc"]
    return {"id": "r1", "key": "race:x", "kind": "race", "title": "賽事 測試", "day": "2099-05-01",
            "minutes": 60, "steps": WS.normalize(doc), "detail": "", **kw}


def _ints(payload):
    return [(e["intensityType"], e["intensityValue"], e["intensityValueExtend"], e["intensityDisplayUnit"])
            for e in payload["exercises"] if not e.get("isGroup")]


def test_normalize_keeps_a_valid_ep_and_drops_a_bad_one():
    d = {"items": [{"kind": "work", "dur": {"type": "open"}, "target": {"type": "hr", "mode": "abs", "lo": 140,
                                                                            "hi": 160, "ep": [412, 388]}},
                   {"kind": "work", "dur": {"type": "open"}, "target": {"type": "none", "ep": [400, 420]}},
                   {"kind": "work", "dur": {"type": "open"}, "target": {"type": "none", "ep": [50, 420]}},
                   {"kind": "work", "dur": {"type": "open"}, "target": {"type": "none", "ep": "x"}}]}
    it = WS.normalize(d)["items"]
    assert it[0]["target"]["ep"] == [388, 412] and it[1]["target"]["ep"] == [400, 420]
    assert "ep" not in it[2]["target"] and "ep" not in it[3]["target"]


def test_coros_sends_effort_pace_on_runnable_legs_only():
    rows = [_row(0, 0.05), _row(1, 0.05), _row(2, 0.25, gait="walk", walk=True), _row(3, 0.25, gait="walk", walk=True),
            _row(4, -0.10, basis="safe"), _row(5, -0.10, basis="safe")]
    s = _race_session(rows, mode="distance")
    # lap mode: the 5 % and the walked 25 % merge into one climb leg, half eligible — no Effort Pace there
    lap = _ints(CW.session_workout(_race_session(rows), TH, today="2099-01-01").payload)
    assert not any(x[0] == CW.INT_EFFORT_PACE for x in lap)
    ints = _ints(CW.session_workout(s, TH, today="2099-01-01").payload)
    ep = [x for x in ints if x[0] == CW.INT_EFFORT_PACE]
    assert CW.INT_EFFORT_PACE == 8
    assert ep and all(x[1] == 388 and x[2] == 412 and x[3] == CW.PACE_DISPLAY_UNIT for x in ep)
    # steep (walked): the HR cap as before; descent: no target
    assert any(x[0] == CW.INT_HR for x in ints) and any(x[0] == CW.INT_NONE for x in ints)
    # the same session without the ep bands: nothing else changed
    plain = {**s, "steps": {**s["steps"], "items": [{**it, "target": {k: v for k, v in it["target"].items() if k != "ep"}}
                                                    for it in s["steps"]["items"]]}}
    ints0 = _ints(CW.session_workout(plain, TH, today="2099-01-01").payload)
    assert [x for x in ints0 if x[0] == CW.INT_EFFORT_PACE] == []
    assert [a for a, b in zip(ints, ints0) if a[0] != CW.INT_EFFORT_PACE] == \
        [b for a, b in zip(ints, ints0) if a[0] != CW.INT_EFFORT_PACE]


def test_stryd_power_by_default_effort_pace_when_chosen():
    rows = [_row(i, 0.04, basis="power", power=250.0) for i in range(3)]
    s = _race_session(rows)
    ints = _ints(CW.session_workout(s, TH, today="2099-01-01").payload)
    assert ints and all(x[0] == CW.INT_POWER for x in ints)
    ints = _ints(CW.session_workout({**s, "ep_power": True}, TH, today="2099-01-01").payload)
    assert ints and all(x[0] == CW.INT_EFFORT_PACE for x in ints)


def test_garmin_payload_is_unchanged():
    from backend.scripts.garmin_probe import garmin_workout
    rows = [_row(0, 0.05), _row(1, 0.05), _row(2, 0.04, basis="power", power=250.0)]
    s = _race_session(rows, ep_power=True)
    plain = {**s, "steps": {**s["steps"], "items": [{**it, "target": {k: v for k, v in it["target"].items() if k != "ep"}}
                                                    for it in s["steps"]["items"]]}}
    assert garmin_workout(s, TH) == garmin_workout(plain, TH)
    assert garmin_workout(s, TH, power_targets=True) == garmin_workout(plain, TH, power_targets=True)


def test_watch_preview_shows_effort_pace_and_says_it_is_unverified():
    rows = [_row(0, 0.05), _row(1, 0.05)]
    st = _race_session(rows)["steps"]
    pv = WS.watch_preview(st, WS.Ctx(cp=300.0, lthr=170.0, aet=150.0))
    assert any(ln.get("target", "").startswith("Effort Pace 6:28–6:52") for ln in pv["lines"])
    assert any("intensityType 8" in x for x in pv["lost"])
    # power legs: power unless the preference says Effort Pace
    pw = _race_session([_row(i, 0.04, basis="power", power=250.0) for i in range(2)])["steps"]
    assert all(ln["target"].startswith("功率") for ln in WS.watch_preview(pw, WS.Ctx(cp=300.0))["lines"])
    assert all(ln["target"].startswith("Effort Pace")
               for ln in WS.watch_preview(pw, WS.Ctx(cp=300.0, ep_power=True))["lines"])


# ---- 課表偏好「可跑坡目標」 ---------------------------------------------------------------

def test_climb_target_preference():
    assert PP.Prefs().climb_target == "power"
    p = PP.from_body({"climb_target": "effort_pace"})
    assert p.climb_target == "effort_pace" and not p.active            # it reshapes nothing
    PP.check(p)
    with pytest.raises(ValueError):
        PP.check(PP.from_body({"climb_target": "pace"}))
    assert PP.from_settings({"plan.prefs.climb_target": "junk"}).climb_target == "power"     # lenient read
    assert p.settings()["plan.prefs.climb_target"] == "effort_pace"
    assert WS.ep_power_of(p) and not WS.ep_power_of(PP.Prefs()) and not WS.ep_power_of(None)


def test_push_dict_passes_the_preference_on_stored_steps():
    from backend.engine import plan_store as PS
    s = {"uid": "u1", "week_start": "2099-04-27", "kind": "race", "title": "賽事 x", "minutes": 60, "state": "planned",
         "day": "2099-05-01", "steps": {"v": WS.V, "items": []}}
    assert PS.push_dict(s, PP.from_body({"climb_target": "effort_pace"}))["ep_power"] is True
    assert "ep_power" not in PS.push_dict(s, PP.Prefs())
    assert "ep_power" not in PS.push_dict({**s, "steps": None}, PP.from_body({"climb_target": "effort_pace"}))


def test_settings_repository_accepts_the_key():
    from backend.settings.repository import DEFAULTS, PREF_ENUMS
    assert DEFAULTS["plan.prefs.climb_target"] == "power"
    assert PREF_ENUMS["plan.prefs.climb_target"] == ("power", "effort_pace")
