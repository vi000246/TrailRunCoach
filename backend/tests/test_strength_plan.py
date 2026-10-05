"""
SP-119: the strength session by phase before a 越野賽 / 百岳 A race (engine/strength_plan.py) —
解剖適應 in the 轉換期 / first 基礎期 weeks, 最大肌力 in the rest of the 基礎期, 維持 in the 專項期
(no step-down for a 百岳 week with ME, SP-114); a road A race or none keeps the old session;
the SP-86 stop still removes it in the 減量期. Synthetic.
"""
import datetime as dt
from datetime import date

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.engine import strength_plan as STP
from backend.engine.planning import Event
from backend.engine.status import Status
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30

OLD = "肌力（下肢單腳＋核心）"


def _ev(day, kind="race", eid="e1", name="越野賽"):
    return Event(eid, name, day, kind=kind, priority="A", distance_km=30, climbing_m=2000, est_hours=5.0)


def _week(events, prefs=None):
    ds = _history(TODAY)
    plan = _plan_with("2027-06-01", 1, TODAY)
    plan.events = list(events)
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return plan, O.week_plan(ds, st, TODAY, prefs=prefs)


def _strength(ss):
    return [s for s in ss if s["kind"] == "strength" and not s.get("done")]


def _ph(*rows):
    return [{"kind": k, "start": s, "end": e} for k, s, e in rows]


def test_stage_by_phase_and_weeks_into_the_base():
    ph = _ph(("transition", "2026-09-07", "2026-09-20"), ("base", "2026-09-21", "2026-12-31"))
    assert STP.stage("transition", ph, date(2026, 9, 14)) == "aa"
    # a 基礎期 right after a 轉換期 counts from the 轉換期: 3 weeks of AA in all
    assert STP.stage("base", ph, date(2026, 9, 21)) == "aa"
    assert STP.stage("base", ph, date(2026, 9, 28)) == "max"
    ph2 = _ph(("recovery", "2026-09-14", "2026-09-20"), ("base", "2026-09-21", "2026-12-31"))
    assert [STP.stage("base", ph2, date(2026, 9, 21) + dt.timedelta(weeks=i)) for i in range(4)] == \
        ["aa", "aa", "aa", "max"]
    assert STP.stage("specific", ph2, date(2026, 12, 7)) == "maint"
    assert STP.stage("recovery", ph2, date(2026, 9, 14)) is None


def test_only_a_trail_or_baiyue_a_race_changes_it():
    ph = _ph(("base", "2026-01-01", "2026-12-31"))
    mon = date(2026, 9, 28)
    assert STP.week_context([_ev("2027-03-01")], ph, mon, "base")["stage"] == "max"
    assert STP.week_context([_ev("2027-03-01", "baiyue")], ph, mon, "base")["active"]
    assert not STP.week_context([_ev("2027-03-01", "road")], ph, mon, "base")["active"]
    assert not STP.week_context([], ph, mon, "base")["active"]
    # the NEXT A race decides: a road A race before the trail one keeps the old session
    assert not STP.week_context([_ev("2026-11-01", "road"), _ev("2027-03-01", eid="e2")], ph, mon, "base")["active"]
    b = Event("b", "B 賽", "2026-11-01", kind="road", priority="B")
    assert STP.week_context([b, _ev("2027-03-01")], ph, mon, "base")["active"]
    s = STP.session({"active": False})
    assert s["title"] == OLD and s["minutes"] == 35 and s["source"] is None


def test_baiyue_week_with_me_drops_the_step_down():
    ctx = {"active": True, "stage": "maint", "race_kind": "baiyue", "race": "嘉明湖"}
    me = [{"id": "me", "kind": "strength", "title": "肌耐力（ME）"}]
    with_me, without = STP.session(ctx, me), STP.session(ctx, [{"id": "quality", "title": "VO2max 間歇 5×4 分上坡"}])
    assert "離心下階" not in with_me["title"] and "ME" in with_me["detail"]
    assert "高踏階" in with_me["title"] and "引體向上" in with_me["title"]
    assert "離心下階" in without["title"]
    assert with_me["minutes"] == without["minutes"] == 25
    # a 越野賽 week keeps the step-down even with an ME-titled session; Friel's 「肌耐力」 Zone 3 isn't ME
    assert "離心下階" in STP.session({**ctx, "race_kind": "race"}, me)["title"]
    assert not STP.has_me([{"id": "quality", "title": "Friel Base 2：2×20 分 Zone 3 肌耐力"}])


def test_texts_cite_bompa_and_ua_and_mark_the_dosage():
    for st in ("aa", "max", "maint"):
        s = STP.session({"active": True, "stage": st, "race_kind": "race", "race": "x"})
        assert "Bompa" in s["source"] and "Uphill Athlete" in s["source"] and "教練級" in s["source"]
        assert "推估" in s["detail"]
    aa = STP.session({"active": True, "stage": "aa", "race_kind": "race", "race": "x"})
    for move in ("離心下階", "分腿蹲", "高踏階", "引體向上", "農夫走路", "棒式"):
        assert move in aa["detail"]
    assert "12–15 下" in aa["detail"] and "留 1–2 下" in aa["detail"]
    mx = STP.session({"active": True, "stage": "max", "race_kind": "race", "race": "x"})
    assert "3–6 下" in mx["detail"] and "5 → 10 % 體重" in mx["detail"] and "不取代真的下坡" in mx["detail"]


def test_week_plan_trail_transition_is_the_aa_circuit():
    # an A race on 9/05 (recovery to 9/12, 轉換期 9/13–10/03) and the next trail A race in March
    _plan, wp = _week([_ev("2026-09-05", name="前一場"), _ev("2027-03-06", eid="e2")])
    assert wp["phase"] == "transition"
    ss = _strength(wp["sessions"])
    assert len(ss) == 2 and all(s["title"] == "肌力（基礎循環 6 站）" for s in ss)
    assert all("Bompa" in s["source"] for s in ss)
    assert wp["a_races"][0]["name"] == "越野賽" and wp["a_races"][0]["kind"] == "race"


def test_week_plan_trail_base_is_max_strength_and_projection_follows():
    # trail A race 1/02: 專項期 from 10/24
    plan, wp = _week([_ev("2027-01-02")])
    assert wp["phase"] == "base"
    ss = _strength(wp["sessions"])
    assert ss and all(s["title"] == "肌力（分腿蹲＋離心下階＋引體向上）" and s["minutes"] == 35 for s in ss)
    # the projection (events from week_plan's a_races): 最大肌力 → 專項期 維持, 25 min
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 22))
    by = {w["start"]: w for w in weeks}
    assert _strength(by["2026-10-05"]["sessions"])[0]["title"] == "肌力（分腿蹲＋離心下階＋引體向上）"
    sp = _strength(by["2026-10-26"]["sessions"])
    assert by["2026-10-26"]["phase"] == "specific" and sp
    assert sp[0]["title"] == "肌力維持（高踏階＋離心下階＋引體向上）" and sp[0]["minutes"] == 25
    assert sp[0]["tss"] < _strength(by["2026-10-05"]["sessions"])[0]["tss"]


def test_week_plan_specific_is_maintenance_and_taper_stops_it():
    # trail A race 11/28: 專項期 9/19–11/13, 減量期 from 11/14
    plan, wp = _week([_ev("2026-11-28")])
    assert wp["phase"] == "specific"
    ss = _strength(wp["sessions"])
    assert ss and all(s["title"] == "肌力維持（高踏階＋離心下階＋引體向上）" for s in ss)
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 29))
    by = {w["start"]: w for w in weeks}
    assert not _strength(by["2026-11-16"]["sessions"]) and not _strength(by["2026-11-23"]["sessions"])  # SP-86


def test_week_plan_baiyue_specific_without_me_keeps_the_step_down():
    _plan, wp = _week([_ev("2026-11-28", "baiyue", name="嘉明湖")])
    assert wp["phase"] == "specific"
    ss = _strength(wp["sessions"])
    assert ss and all(s["title"] == "肌力維持（高踏階＋離心下階＋引體向上）" for s in ss)


def test_road_a_race_keeps_the_old_session_with_preferences_too():
    _plan, wp = _week([_ev("2026-11-28", "road", name="馬拉松")])
    assert all(s["title"] == OLD and s["minutes"] == 35 for s in _strength(wp["sessions"]))
    # 課表偏好 每週肌力 3 copies the week's session (trail: the phase's)
    _plan, wp = _week([_ev("2027-03-06")], prefs=PP.Prefs(strength=3))
    ss = _strength(wp["sessions"])
    assert len(ss) == 3 and all(s["title"] == "肌力（分腿蹲＋離心下階＋引體向上）" for s in ss)
