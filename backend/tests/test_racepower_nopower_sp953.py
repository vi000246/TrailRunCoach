"""SP-953: the race calculator and the race feasibility follow the plan's one power rule
(engine/power_state.py; docs/research/no-power-meter-audit.md 「拍板的規則」, SP-950 問答 Q1-8 / Q2-2 (a)):
power only in ① (a Stryd and a CP test or race in the last 42 days). In ② (CP stale) and ③ (no power meter)

  * trail: the trail HR pace model gives the whole-race time directly (it used to need the power model's v1
    first), the segments split by the grade-adjusted pace; no HR model → the race-results estimate
  * road: the race-results estimate (Riegel) is the main route
  * no power, % CP, W/kg, CP, RE, power targets anywhere — plan, chart rows, segment targets, watch export,
    share, the zone bar's fallback; the page says which model and why
  * the race feasibility's W/kg only in ① (it read the legacy power_meter that saving 功率來源 clears)

Synthetic data only: a fake calculator context, synthetic GPX tracks and race results."""
from __future__ import annotations

import datetime as dt

import pytest

from backend.engine import power_state as PST
from backend.engine import race_results as RR
from backend.engine.racepower import calc as CALC
from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import race_estimate as RX
from backend.engine.racepower import share as SH
from backend.engine.racepower import watch_export as WE
from backend.engine.racepower import zonebar as ZB
from backend.tests.test_racepower_v2 import RE0, fake_inputs, synthetic_track

approx = pytest.approx
TODAY = dt.date(2026, 10, 5)
TEN_K = RR.make(10.0, 45 * 60, "2026-05-01", source="survey")               # 10 K 45:00
HR_MODEL = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9}

POWER = PST.state(TODAY, {"power_source": "stryd"}, last_cp_test="2026-09-20", cp=280.0)              # ①
STALE = PST.state(TODAY, {"power_source": "stryd"}, last_cp_test="2026-07-01", cp=280.0)              # ②
NONE = PST.state(TODAY, {"power_source": "none"}, last_cp_test="2026-09-20", cp=280.0)                # ③


def _inputs():
    d = fake_inputs()
    d["aet"] = {"aet": 150.0, "lthr": 170.0, "source": "測試"}
    return d


class Ctx:
    """calc.Context on a synthetic athlete: the v2 fixture's inputs, a trail HR pace model, race results."""

    def __init__(self, ps, th=HR_MODEL, results=(TEN_K,), tracks=None):
        self.ps, self.th, self.results, self.tracks = ps, th, list(results), tracks or {}

    def inputs(self):
        return _inputs()

    def grade_models(self):
        return {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]), "moving_rows": []}

    def flags(self):
        return {"road": False, "trail": False, "hike": False}, False

    def heat_status(self, date):
        return {}

    def hrc_test(self):
        return None

    def trail_hr(self):
        return self.th

    def hr_basis(self):
        return None

    def body(self):
        return None

    def event(self, eid):
        raise CALC.CalcError(404, "no event")

    def event_track(self, eid):
        return None

    def event_splits(self, row, days):
        return []

    def event_meta(self, row):
        return None

    def course_track(self, cid):
        return self.tracks.get(cid)

    def power_state(self):
        return self.ps

    def race_results(self):
        return self.results

    def today(self):
        return TODAY

    def survey_hours(self):
        return None


def _hill():
    """12 km out and back over a 600 m hill: a flat start, a steep climb and the descent."""
    return synthetic_track({"len": 12000, "z": lambda x: 200 + (0 if x < 2000 else (x - 2000) * 0.15 if x < 6000
                                                                else max(0.0, 600 - (x - 6000) * 0.12))})


def _trail_plan(ps, th=HR_MODEL, results=(TEN_K,), **kw):
    ctx = Ctx(ps, th, results, {"c1": _hill()})
    body = CALC.PlanIn(type="trail", course=CALC.CourseRef(course_id="c1"), **kw)
    return CALC.make_plan(ctx, body)


def _no_power(p: dict) -> None:
    """No power, % CP, W/kg or CP anywhere a page, the watch or a share reads."""
    s = p["summary"]
    assert p["power_off"] is True and s["power_off"] is True
    assert s["power"] is None and s["pct_cp"] is None and s["w_per_kg"] is None and s["power_train"] is None
    assert "cp" not in p["used"] and "re" not in p["used"] and p["zones"] == []
    for x in p["segments"]:
        assert x["power"] is None and x["pct_cp"] is None and x["zone"] is None
    for r in p["chart_rows"]:
        assert r["power"] is None and r["power_band"] is None and r["power_ref"] is None and r["basis"] != "power"
    for t in p.get("seg_targets") or []:
        assert t["basis"] != "power" and "W" not in t["text"] and "CP" not in t["text"]
    ex = WE.steps_for(p, p["chart_rows"])
    assert all(lg["target"]["type"] != "power" for lg in ex["legs"])


# ---- 驗收 1: trail → the HR model, road → the race results; the page says which -------------------------

def test_trail_without_power_uses_the_hr_model_directly_split_by_grade_adjusted_pace():
    for ps in (NONE, STALE):
        p = _trail_plan(ps)
        s = p["summary"]
        _no_power(p)
        assert s["total_method"] == "trail_hr" and p["model"]["kind"] == "trail_hr"
        th = s["trail_hr"]
        # the whole race = the HR model's time over the course's environment factor (low course: M ≈ 1)
        assert s["time_s"] == approx(th["time_s"], rel=0.05)
        assert sum(x["t"] for x in p["segments"]) == approx(s["time_s"], abs=1.0)
        assert p["segments"][-1]["cum_s"] == approx(s["time_s"], abs=1.0)
        # split by the grade-adjusted pace: the steep climb is slower per km than the flat start, the GAP even
        flat = next(x for x in p["segments"] if abs(x["grade"]) < 0.02)
        steep = next(x for x in p["segments"] if x["grade"] > 0.08)
        assert steep["pace_s_per_km"] > 1.5 * flat["pace_s_per_km"]
        assert steep["gap_pace_s_per_km"] == approx(flat["gap_pace_s_per_km"], rel=0.02)
        assert p["effort"]["basis"] == "hr" and p["effort"]["f"] == 1.0
        assert any(w["id"] == "RC7" and "心率配速模型" in w["text"] for w in CALC.respond(p)["warnings"])
    # the page's line says which model and why power is not used
    stale = _trail_plan(STALE)
    assert "越野心率配速模型" in stale["model"]["line"] and "CP 已 96 天沒測" in stale["model"]["line"]
    assert "沒有功率計" in _trail_plan(NONE)["model"]["line"]


def test_trail_without_power_and_without_an_hr_model_uses_the_race_results():
    p = _trail_plan(NONE, th=None)
    _no_power(p)
    s = p["summary"]
    est = RX.estimate("trail", s["km"], s["gain_m"], [TEN_K], TODAY, power_state=NONE)
    assert s["total_method"] == "race_estimate" and s["time_s"] == approx(est["time_s"] / s["M"], rel=1e-6)
    assert "還沒有越野心率配速模型" in p["model"]["line"]
    assert p["effort"]["basis"] == "race"


def test_road_without_power_is_the_race_results_estimate():
    ctx = Ctx(NONE)
    p = CALC.make_plan(ctx, CALC.PlanIn(type="road", distance_km=21.0975, course=CALC.CourseRef(manual={"km": 21.0975})))
    _no_power(p)
    s = p["summary"]
    t = RX.riegel_time(10.0, 2700, 21.0975)
    assert s["total_method"] == "race_estimate" and s["time_s"] == approx(t / s["M"], abs=1.0)   # the estimate rounds to s
    assert p["model"]["kind"] == "race_estimate" and "比賽成績推估" in p["model"]["line"]
    assert len({round(x["pace_s_per_km"], 3) for x in p["segments"]}) == 1          # manual course: even split
    txt = " ".join(w["text"] for w in CALC.respond(p)["warnings"])
    assert "Riegel" in txt and "有 CP 之後" not in txt
    # 95 % effort: 5 % slower
    p95 = CALC.make_plan(ctx, CALC.PlanIn(type="road", distance_km=21.0975, effort_target=0.95,
                                          course=CALC.CourseRef(manual={"km": 21.0975})))
    assert p95["summary"]["time_s"] == approx(s["time_s"] / 0.95, rel=1e-6)


def test_no_model_and_no_race_result_says_what_to_enter():
    with pytest.raises(CALC.CalcError) as ex:
        CALC.make_plan(Ctx(NONE, results=()), CALC.PlanIn(type="road", distance_km=10.0))
    assert ex.value.status == 400 and "跑步經驗" in ex.value.detail


def test_power_in_effect_keeps_the_power_model():
    """① (and a context without a power state: the intro page's runner, older callers) — as before."""
    for ps in (POWER, None):
        ctx = Ctx(ps)
        p = CALC.make_plan(ctx, CALC.PlanIn(type="road", distance_km=21.0975))
        assert not p.get("power_off") and p["summary"]["power"] > 0 and p["summary"]["pct_cp"] > 0
        assert p["used"]["cp"]["value"] == 300.0 and p["summary"]["total_method"] == "v1"
    p = _trail_plan(POWER)
    assert p["summary"]["total_method"] == "trail_hr" and p["summary"]["power"] > 0
    assert any(r["power"] for r in p["chart_rows"])


def test_goal_modes_without_power():
    base = _trail_plan(NONE)
    t = base["summary"]["time_s"]
    g = _trail_plan(NONE, mode="time", target_time_s=t / 1.10)
    assert g["summary"]["time_s"] == approx(t / 1.10, rel=1e-6) and g["goal"]["level"] == "too_fast"
    assert g["goal"]["model_time_s"] == approx(t, rel=1e-3) and g["goal"]["model_power"] is None
    _no_power(g)
    # a body still asking for a power goal (a saved page) is the model's prediction
    pw = _trail_plan(NONE, mode="power", target_power=250)
    assert pw["summary"]["mode"] == "auto" and pw["summary"]["time_s"] == approx(t, rel=1e-6)


# ---- 驗收 2: no power in the calculator, the segment table, the watch export, the share page ----------------

def test_watch_export_share_zone_bar_and_fuel_have_no_power():
    p = _trail_plan(NONE, stops=[CALC.StopIn(km=6.0, type="aid")])
    for mode in ("lap", "distance"):
        ex = WE.steps_for(p, p["chart_rows"], mode=mode, stops=[{"km": 6.0, "type": "aid"}])
        assert all(it["target"]["type"] != "power" for it in ex["doc"]["items"])
    # the race session's load: the legs at the HR model's race HR ÷ LTHR, no CP
    th = {"cp": (p["used"].get("cp") or {}).get("value"), "lthr": 170.0}
    hr, src = WE.race_hr(p, th)
    ti = WE.tss_info(ex["legs"], th, hr, src)
    assert th["cp"] is None and hr and ti["tss"] > 0 and not ti["fallback"]
    snap = SH.snapshot(p, title="t", include_weight=True)
    assert snap["power_off"] is True and snap["cp"] is None and "w_per_kg" not in snap["summary"]
    assert "power" not in snap["summary"] and all("power" not in x for x in snap["segments"])
    assert all("power" not in r and "power_band" not in r for r in snap["chart_rows"])
    assert snap["model"]["kind"] == "trail_hr"
    # the zone bar: a trail plan has the HR model's race HR; a road estimate none — and never the power zones
    assert p["hr_bar"]["hr"] and p["hr_bar"]["power_off"] is True
    road = CALC.make_plan(Ctx(NONE), CALC.PlanIn(type="road", distance_km=10.0))
    assert road["hr_bar"]["hr"] is None and road["hr_bar"]["reason"] == ZB.NO_HR_OFF_ROAD
    # 補給: the heart-rate method (Keytel) where the HR model predicts the race HR, never 功率法
    assert "power" not in p["fuel"]["methods"] and p["fuel"]["power_share"] == 0.0
    assert all("功率法" not in str(w) for w in p["fuel"]["warnings"])


def test_estimate_texts_follow_the_power_state():
    one = RX.estimate("road", 21.1, 0.0, [TEN_K], TODAY)
    assert one["title"] == RX.TITLE and "有 CP 之後" in one["text"]                  # ① without a CP: as before
    st = RX.estimate("road", 21.1, 0.0, [TEN_K], TODAY, power_state=STALE)
    assert "CP 過期" in st["title"] and "有 CP 之後" not in st["text"] and "做一次 CP 測試" in st["text"]
    no = RX.estimate("trail", 21.0, 1200.0, [TEN_K], TODAY, power_state=NONE)
    assert "沒有功率計" in no["title"] and "CP" not in no["text"]


def test_api_plan_estimate_and_goal_basis_follow_the_power_state(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    from backend.engine.racepower import backtest as BT
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: _inputs())
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": []})
    monkeypatch.setattr(RP, "_trail_hr", lambda: HR_MODEL)
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": False, "hike": False}, False))
    monkeypatch.setattr(RR, "load", lambda user_id=1: [TEN_K])
    monkeypatch.setattr("backend.engine.race_feasibility.survey_hours", lambda exp=None, load=True: None)
    monkeypatch.setattr(RP, "power_state", lambda: NONE)
    app = FastAPI()
    app.include_router(RP.router)
    c = TestClient(app)
    assert c.get("/api/v1/racepower/power-state").json()["state"] == "none"
    r = c.post("/api/v1/racepower/plan", json={"type": "road", "distance_km": 10.0})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["power_off"] and p["summary"]["total_method"] == "race_estimate" and p["summary"]["power"] is None
    # /estimate: a CP on file does not bring the power model back in ③
    e = c.post("/api/v1/racepower/estimate", json={"type": "road", "distance_km": 10.0}).json()
    assert e["available"] and "沒有功率計" in e["title"]
    # the goal: pace / time only (a stored 目標依據 = 功率 is ignored), ② too
    monkeypatch.setattr(RP, "_goal_settings", lambda: ("power", True, False))
    monkeypatch.setattr(RP, "power_state", lambda: STALE)
    assert c.get("/api/v1/racepower/goal-basis").json()["basis"] == "hr"


# ---- race_refs / 賽事可行性 -------------------------------------------------------------------------------

def test_race_refs_calculator_hours_take_the_same_route(monkeypatch):
    from backend.api import racepower as RP
    from backend.engine.panels import race_refs as RF
    from backend.tests.test_race_feasibility import ev
    e = ev(distance_km=30.0, climbing_m=1500.0, est_hours=5.0)
    course = {"totals": {"km": 30.0, "gain_m": 1500.0, "loss_m": 1500.0}, "days": [{"km": 30.0, "gain_m": 1500.0,
                                                                                   "loss_m": 1500.0}]}
    seen = {}

    def plan(body):
        seen["body"] = body
        return {"summary": {"time_s": 5.5 * 3600, "total_method": "trail_hr"}}
    assert RF.nopower_hours(e, course, "trail", plan_fn=plan) == [5.5]
    assert seen["body"].type == "trail" and seen["body"].course.manual["km"] == 30.0
    assert RF.nopower_hours(e, course, "trail", plan_fn=lambda b: (_ for _ in ()).throw(ValueError("x"))) is None
    # ② / ③: calculator_hours never asks the power model's /predict
    monkeypatch.setattr(RP, "power_state", lambda: NONE)
    monkeypatch.setattr(RP, "predict", lambda body: pytest.fail("the power model was asked"))
    monkeypatch.setattr(RF, "nopower_hours", lambda e, c, k: [4.0])
    assert RF.calculator_hours(e, course) == [4.0]


def test_trail_finish_takes_the_race_estimate_and_says_why_it_failed(monkeypatch):
    from backend.engine import race_feasibility as F
    from backend.tests.test_race_feasibility import TODAY as T0, ev, hist, line
    monkeypatch.setattr(F, "calc_inputs", lambda e: {})
    course = {"totals": {"km": 30.0, "gain_m": 2000.0, "loss_m": 1900.0}, "days": [], "filename": None,
              "descent_assumed": True}
    out = F.trail_finish(ev(cutoff_hours=8.0), course, plan_fn=lambda b: {
        "summary": {"total_method": "race_estimate", "time_s": 6.0 * 3600, "nonmoving": None}})
    assert out["moving_h"] == 6.0 and out["method"] == "race_estimate"
    e = ev(cutoff_hours=8.0)
    c = next(c for c in F.assess(e, line(e, [5.0]), T0, hist(km=40.0, climb=2000.0), finish=out)["checks"]
             if c["id"] == "cutoff")
    assert c["time_method"] == "race_estimate" and "比賽成績推估" in c["text"] and c["moving_h"] == 6.0

    def boom(body):
        raise CALC.CalcError(400, "沒有 CP：請手動輸入")
    out = F.trail_finish(ev(cutoff_hours=8.0), course, plan_fn=boom)
    assert out["moving_h"] is None and out["why"] == "沒有 CP：請手動輸入"
    c = next(c for c in F.assess(e, line(e, [5.0]), T0, hist(km=40.0, climb=2000.0),
                                 finish={**out, "fallback": "plan"})["checks"] if c["id"] == "cutoff")
    # the wrong 「沒有越野心率配速模型」 is gone when the model was not what failed
    assert "沒有越野心率配速模型" not in c["text"] and "算不出這場的移動時間（沒有 CP：請手動輸入）" in c["text"]


# ---- 驗收 3: a Stryd runner with power in effect sees the W/kg check ----------------------------------------

def test_feasibility_w_per_kg_for_a_stryd_profile_only_while_power_is_in_effect():
    from backend.engine import race_feasibility as F
    from backend.engine.planning import Plan, Threshold, Weight
    from backend.tests.test_race_feasibility import TODAY as T0, hist, line, trip
    plan = Plan(events=[], thresholds=[Threshold(date="2026-09-25", cp=260.0)], weights=[Weight("2026-01-01", 65.0)])
    plan.profile = {"power_source": "stryd", "power_meter": None}       # as api/plan.put_profile stores it
    e = trip(distance_km=10, climbing_m=2000, est_hours=5.0)
    _climb, power = F.baiyue_inputs(plan, None, T0, e)
    assert power == {"cp": 260.0, "kg": 65.0}
    r = F.assess(e, line(e), T0, hist(km=40.0, climb=3000.0), power=power)
    chk = next(c for c in r["checks"] if c["id"] == "power")
    assert chk["w_per_kg"] == approx(4.0) and "W/kg" in chk["text"]
    plan.profile = {"power_source": "none"}                              # ③
    assert F.baiyue_inputs(plan, None, T0, e)[1] is None
    plan.profile = {"power_source": "stryd"}
    plan.thresholds = [Threshold(date="2026-06-01", cp=260.0)]           # ②: stale
    assert F.baiyue_inputs(plan, None, T0, e)[1] is None
