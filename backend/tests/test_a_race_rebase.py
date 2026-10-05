"""
SP-116 A 賽後重新打底 (base_check.a_race_rebase; quality_gate.z3_gate / evaluate; base_check.z5_status):
after an A race — not a B / C race — the Zone 3 and Zone 5 gates lock again until a confirmation
dated after the race's 恢復期 + 轉換期, by the 間歇門檻 method; the AeT-test suggestion says so.
Synthetic plans / runs only.
"""
import datetime as dt
from datetime import date
from unittest import mock

import pytest

from backend.engine import base_check as BC
from backend.engine import planning as P
from backend.engine import quality_gate as QG
from backend.tests.test_quality_gate import TODAY, _ds, _gate

RACE_DAY = "2026-08-01"          # 7-day 恢復期 8/2–8/8, 3-week 轉換期 8/9–8/29, base from 8/30


@pytest.fixture(autouse=True)
def _three_week_transition(monkeypatch):
    monkeypatch.setattr(P, "transition_weeks_setting", lambda user_id=1: 3)


def _plan(pri="A", day=RACE_DAY, aet=None):
    plan = P.Plan()
    plan.events.append(P.Event(id="r1", name="草嶺古道越野", date=day, priority=pri, distance_km=21, climbing_m=900))
    if aet:
        plan.thresholds.append(P.Threshold(aet[0], lthr=160, aethr=150))
    return plan


def test_rebase_starts_after_the_recovery_and_the_transition():
    r = BC.a_race_rebase(_plan(), TODAY)
    assert r["from"] == "2026-08-30" and r["race_end"] == "2026-08-01" and r["event_id"] == "r1"
    assert "重新打底" in r["text"] and "徐國峰" in r["src"] and "教練級" in r["src"]
    # inside the 恢復期 already (the gates lock from the race on); before the race nothing
    assert BC.a_race_rebase(_plan(), date(2026, 8, 5))["from"] == "2026-08-30"
    assert BC.a_race_rebase(_plan(), date(2026, 7, 30)) is None
    # B / C races never
    assert BC.a_race_rebase(_plan("B"), TODAY) is None and BC.a_race_rebase(_plan("C"), TODAY) is None


def test_no_rebuild_when_the_next_build_follows_without_a_base_phase():
    plan = _plan()
    # the next A race's 專項期 starts right after the 恢復期 (no 轉換期, no 基礎期)
    plan.events.append(P.Event(id="r2", name="下一場", date="2026-10-17", priority="A", distance_km=21))
    assert BC.a_race_rebase(plan, date(2026, 8, 20)) is None


def test_zone3_locks_after_an_a_race_not_after_a_b_race():
    g = _gate(plan=_plan())
    z3 = g["z3"]
    assert not z3["open"] and z3["rebase"] and "重新打底" in z3["reason"] and "徐國峰" in z3["src"]
    assert g["rebase"]["from"] == "2026-08-30"
    assert QG.week_decision(g, "base", "base")["spec"] is None            # easy, long and strides only
    # the projection doesn't open it by counting weeks
    assert not QG.z3_open_on(z3, date(2026, 11, 30), g["monday"])
    b = _gate(plan=_plan("B"))
    assert b["z3"]["open"] and b["z3"]["path"] == "weeks" and b["rebase"] is None
    assert _gate(mode="none", plan=_plan())["z3"]["open"]                 # no gate: nothing to re-lock


def test_zone3_reopens_on_a_90_min_test_after_the_rebuild_date():
    def xu(day):
        return [{"date": day, "ok": True, "drift": 0.06, "why": [], "hr10": 130, "hr90": 138, "chip": ""}]
    with mock.patch.object(BC, "xu_runs", lambda ds, today, days=BC.LOOKBACK_DAYS: xu("2026-07-20")):
        assert not _gate(plan=_plan())["z3"]["open"]                      # before the race: doesn't count
    with mock.patch.object(BC, "xu_runs", lambda ds, today, days=BC.LOOKBACK_DAYS: xu("2026-08-20")):
        assert not _gate(plan=_plan())["z3"]["open"]                      # in the 轉換期: before the rebuild
    with mock.patch.object(BC, "xu_runs", lambda ds, today, days=BC.LOOKBACK_DAYS: xu("2026-09-06")):
        g = _gate(plan=_plan())
    assert g["z3"]["open"] and g["z3"]["path"] == "xu90"
    assert QG.week_decision(g, "base", "base")["spec"] is not None


def test_zone5_needs_a_confirmation_after_the_rebuild():
    rb = BC.a_race_rebase(_plan(), TODAY)
    ds = _ds([])
    old = BC.z5_status(ds, TODAY, "auto", None, {"aet_ua_gap": "2026-07-01"}, None, [], rb)
    assert old["state"] == "unconfirmed" and not old["open"] and "重新打底" in old["reason"] and old["rebase"]
    new = BC.z5_status(ds, TODAY, "auto", None, {"aet_ua_gap": "2026-09-10"}, None, [], rb)
    assert new["state"] != "unconfirmed" and new["since"] == "2026-09-10"
    # without a rebuild the old confirmation stands
    assert BC.z5_status(ds, TODAY, "auto", None, {"aet_ua_gap": "2026-07-01"}, None, [])["since"] == "2026-07-01"


def test_the_test_suggestion_reason_and_its_method():
    g = _gate(plan=_plan())
    r = g["aet_test_reason"]
    assert r["code"] == "rebase" and r["from"] == "2026-08-30" and "90 分鐘飄移測試" in r["text"]
    assert "（或你選的方法）" in r["text"] and "教練級" in r["text"]
    xu = _gate(mode="xu_drift", plan=_plan())
    assert "用賽後的 E 配速" in xu["aet_test_reason"]["text"]
    # inside the 轉換期 it isn't suggested yet
    rb = BC.a_race_rebase(_plan(), date(2026, 8, 20))
    assert QG.rebase_reason(date(2026, 8, 20), rb, {"open": False, "rebase": True}, {}) is None
    # Zone 3 open again and no Zone 5 confirmed before the race: nothing left to re-test
    assert QG.rebase_reason(TODAY, rb, {"open": True, "rebase": True}, {"aet_paths": {}}) is None
    # Zone 5 confirmed before the race and not again yet: an AeT test
    z5 = {"aet_paths": {"aet_ua_gap": "2026-07-01"}}
    assert "AeT 測試" in QG.rebase_reason(TODAY, rb, {"open": True, "rebase": True}, z5)["text"]


def test_a_drift_method_must_be_run_after_the_rebuild():
    r = {"state": "unlocked", "via": "xu_drift", "run": {"date": "2026-07-20"}}
    rb = {"from": "2026-08-30"}
    assert not QG._rebased(r, {}, None, rb)
    assert QG._rebased({**r, "run": {"date": "2026-09-01"}}, {}, None, rb)
    assert QG._rebased({"via": "ua_gap"}, {"date": "2026-09-02"}, None, rb)
    assert QG._rebased({"via": "weeks"}, {}, "2026-08-30", rb) and not QG._rebased({"via": "weeks"}, {}, "2026-07-01", rb)
    assert "90 分鐘飄移測試" in QG.rebase_action("xu_drift", rb) and "輕鬆跑、長跑和加速跑" in QG.rebase_action("auto", rb)


@pytest.mark.parametrize("mode,proto", [("auto", "xu90"), ("xu_drift", "xu90"), ("friel_drift", "friel")])
def test_week_plan_suggests_the_rebuild_test(mode, proto):
    """The floating box's test row (week_plan test_suggestions): the method's own test, the reason
    names the rebuild; no interval this week. A B race: no such row, the interval stays."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_long_spike import _history

    def week(pri):
        plan = _plan(pri)
        plan.thresholds.append(P.Threshold("2026-08-25", lthr=165, cp=250.0))     # CP fresh: no CP test
        ds = _history(45)                                   # runs too short to read as an AeT test
        ds.plan = plan
        prefs = PP.Prefs(quality_gate=mode)
        return O.week_plan(ds, Status(ds, plan, TODAY, prefs=prefs).compute(), TODAY, prefs=prefs)
    wp = week("A")
    (t,) = wp["test_suggestions"]
    assert t["kind"] == "aet" and t["protocol"] == proto and t["reason"].startswith("A 賽「草嶺古道越野」後重新打底")
    assert not any(s["kind"] == "quality" for s in wp["sessions"])
    wb = week("B")
    assert not any("重新打底" in x["reason"] for x in wb["test_suggestions"])
