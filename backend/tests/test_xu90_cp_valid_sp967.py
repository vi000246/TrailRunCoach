"""SP-967 (owner 2026-10-28, Q1 (b): 「CP 有效的定義要全 app 統一」): the 徐國峰 90-minute AeT test's 「75–80 % 實測
CP」 counts a CP that is valid as the power state reads it (power_state.cp_check) — the Stryd PD fit anchored by
all-out efforts in the last 42 days (SP-963) — not only a formal CP test (overview.week_plan → aet_test.cp_tested
(cp_valid=...)). A CP typed by hand is still never a test; without a valid CP the order is unchanged (E pace →
HR → pace → talk test). Synthetic data only."""
from __future__ import annotations

import datetime as dt

from backend.engine import aet_test as AT
from backend.engine.planning import Plan, Threshold
from backend.tests.test_cp_valid_sp965 import week

TODAY = dt.date(2026, 10, 27)


def test_cp_tested_counts_a_valid_cp_without_a_test():
    assert AT.cp_tested(Plan(), TODAY, cp_valid=True)
    assert not AT.cp_tested(Plan(), TODAY) and not AT.cp_tested(Plan(), TODAY, cp_valid=False)
    # a CP typed by hand is the CP in effect: never a test, whatever keeps the fit valid
    manual = Plan(thresholds=[Threshold("2026-10-01", cp=200.0, cp_manual=True)])
    assert not AT.cp_tested(manual, TODAY, cp_valid=True)
    # a CP test inside the fit's 90 days still counts on its own (SP-468)
    assert AT.cp_tested(Plan(), TODAY, cp_test_date="2026-10-01", cp_valid=False)


def _aet(wp):
    (t,) = [x for x in wp["test_suggestions"] if x["kind"] == "aet"]
    return t


def test_the_90_minute_test_uses_75_to_80_percent_of_a_cp_kept_valid_by_efforts(monkeypatch):
    monkeypatch.setattr(AT, "due", lambda *a, **k: True)
    wp, _ = week(monkeypatch, "2026-09-20")             # ① by efforts, no CP test, no race result
    assert wp["thresholds"]["power_state"]["basis"] == "efforts"
    assert wp["thresholds"]["cp_measured"] is True
    t = _aet(wp)
    assert t["protocol"] == "xu90"
    assert "188–200 W（75–80% CP" in t["session"]["target"]                    # 250 W × 0.75–0.80
    assert "用實測 CP 250 W 的 75–80%" in t["session"]["detail"]


def test_without_a_valid_cp_the_order_is_unchanged(monkeypatch):
    from backend.engine import power_state as PST
    wp, _ = week(monkeypatch, "2026-08-01")             # the efforts lapsed on 9/12, no CP test: ②
    assert wp["thresholds"]["power_state"]["state"] == PST.STALE
    assert wp["thresholds"]["cp"] is None and wp["thresholds"]["cp_measured"] is False
    # cp_measured False → xu_target skips the power step (test_xu90_intensity: 「talk」 / 「fit」 / 「pace」)
    assert AT.xu_target({"cp": 250.0, "cp_measured": False})["basis"] != "power"
