"""間歇門檻 (engine/quality_gate.py) and the AeT drift test (engine/aet_test.py)
on synthetic data — docs/research/aerobic-base-readiness.md §4–§6.

Never touches the user's plan.json (the apply test runs on a temp plan) and
never talks to COROS (steps and payloads are built locally)."""
import datetime as dt
import types
from datetime import date

import numpy as np
import pytest

from backend.engine import aet_test as AT
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as P
from backend.engine import interval_library as IL_
from backend.engine import load_guard as LG
from backend.engine import quality_gate as QG
from backend.engine.planning import Plan, Threshold
from backend.engine.status import Status
from backend.settings import repository as SR
from backend.sync import coros_workouts as CW
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = date(2026, 9, 30)
BASE = types.SimpleNamespace(kind="base", start="2026-07-01", end="2026-12-31")


def _ds(workouts, plan=None, settings=None):
    ds = FakeDataset(workouts, TODAY, settings=settings or SETTINGS)
    ds.plan = plan or Plan()
    ds.aethr = lambda w: ds.plan.threshold_on("aethr", TODAY) or 0.89 * 160.0
    ds.cp = lambda w: 250.0
    ds.mftp_run = None
    ds.config = types.SimpleNamespace(parity=True)
    return ds


def _ind(level="good", extra=None, value=None):
    return types.SimpleNamespace(level=level, extra=extra or {}, value=value)


GOOD_BY = {"intensity": _ind(extra={"low_share": 0.85, "power_low_share": 0.9}),
           "fitness": _ind(extra={"ramp_week": 2.0}), "volume": _ind(extra={"step": 0.05, "last_week": 5.0}),
           "form": _ind(value=-12.0), "drift": _ind("info"), "efficiency": _ind(extra={"change": 0.01})}


def _plan(aethr=None, lthr=None, day="2026-09-01", note=""):
    p = Plan()
    if aethr or lthr:
        p.thresholds.append(Threshold(day, lthr=lthr, aethr=aethr, note=note))
    return p


def _weeks_ok(n, need=QG.Z3_WEEKS_NEED):
    """Run dates for QG.run_days: 4 runs in each of the last `n` complete weeks, none before —
    the Zone 3 gate's consistency path (SP-31) is met from n ≥ 4."""
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    return sorted(mon - dt.timedelta(weeks=i) + dt.timedelta(days=k) for i in range(1, n + 1) for k in (0, 2, 4, 6))


def _gate(mode="auto", plan=None, workouts=(), by=None, phase=BASE, weeks=8, settings=None, z3_weeks=QG.Z3_WEEKS_NEED):
    """evaluate() on synthetic data; `z3_weeks`: complete weeks in a row of ≥ 3 runs (the Zone 3
    gate's consistency path, SP-31) — default: open."""
    from unittest import mock
    plan = plan or Plan()
    ds = _ds(list(workouts), plan, settings)
    with mock.patch.object(QG, "run_days", lambda ds, today, days=QG.Z3_HISTORY_DAYS: _weeks_ok(z3_weeks)):
        return QG.evaluate(ds, plan, TODAY, PP.Prefs(quality_gate=mode, quality_gate_weeks=weeks), by or GOOD_BY,
                           phase)


# ---------------------------------------------------------------------------
# preferences
# ---------------------------------------------------------------------------

def test_gate_prefs_round_trip_validate_and_never_switch_shaping_on():
    p = PP.from_body({"quality_gate": "friel_drift", "quality_gate_weeks": 10})
    assert p.quality_gate == "friel_drift" and p.quality_gate_weeks == 10
    assert not p.active                       # the gate alone doesn't turn shape() / place() on
    assert PP.from_settings(p.settings()) == p
    assert PP.Prefs().quality_gate == "auto" and PP.Prefs().quality_gate_weeks == 8
    assert SR.DEFAULTS["plan.prefs.quality_gate"] == "auto"
    SR.validate("plan.prefs.quality_gate", "plateau")
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.quality_gate", "legacy_streak")
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.quality_gate_weeks", 20)
    with pytest.raises(ValueError):
        PP.check(PP.Prefs(quality_gate_weeks=1))


# ---------------------------------------------------------------------------
# the method, per mode, with and without a measured AeT
# ---------------------------------------------------------------------------

def test_auto_without_aet_is_no_method_and_the_guardrails():
    g = _gate()
    assert g["resolved"] == "none" and g["state"] == "none" and not g["fallback"]
    t = QG.indicator(g)
    assert t["level"] == "info" and "沒有 AeT 實測：照 80/20 原則每週 1 次間歇" in t["verdict"]
    # 台灣教練: Zone 3 first — the Zone 3 track's first rung is 2×15′ (SP-31), not 5×1′
    assert "3 區第 1 步：有氧間歇 2×15 分" in t["verdict"]
    assert QG.guardrail_mode(g)
    assert QG.week_decision(g, "base", "base")["spec"] is QG.Z3[0]
    assert g["z5"]["state"] == "unconfirmed" and not g["z5"]["open"]


@pytest.fixture
def valid_aet(monkeypatch):
    """B3: the AeT counts as valid (the aggregate's SE ≤ 3 bpm, no shift)."""
    from backend.engine import drift_agg as DA
    monkeypatch.setattr(DA, "aet_validity", lambda ds, today, lthr=None: {
        "valid": True, "value": 150.0, "se": 2.0, "n": 8, "shift_bpm": 0.0, "slope_per_10bpm": 0.05,
        "reason": "8 次聚合估計 AeT 150 ± 2.0 bpm", "points": 8})


def test_auto_with_measured_aet_uses_ua_gap_then_friel(valid_aet):
    open_ = _gate(plan=_plan(aethr=150, lthr=160))          # 160 / 150 − 1 = 6.7 %
    assert open_["resolved"] == "ua_gap+friel_drift" and open_["state"] == "unlocked" and open_["via"] == "ua_gap"
    assert open_["gap"] == pytest.approx(160 / 150 - 1)
    t = QG.indicator(open_)
    assert t["level"] == "good" and "≤ 10%：可以加 Zone 3" in t["verdict"]
    # the measured AeT passing the UA gap confirms the base: Zone 5 opens (dated the AeT row)
    assert open_["z5"]["state"] == "confirmed" and open_["z5"]["path"] == "aet_ua_gap"
    assert QG.week_decision(open_, "base", "base")["spec"] is QG.Z3[0]          # still Zone 3 first
    shut = _gate(plan=_plan(aethr=140, lthr=165))           # 17.9 %
    assert shut["state"] == "locked"
    t = QG.indicator(shut)
    assert t["level"] == "watch" and t["verdict"].startswith("AeT 140 / LTHR 165：差距 18%（> 10%，有氧不足）")
    assert "3 區照排" in t["action"]
    # a locked method only keeps Zone 5 closed: Zone 3 still goes on (台灣教練)
    d = QG.week_decision(shut, "base", "base")
    assert d["allow"] and d["spec"] is QG.Z3[0] and not shut["z5"]["open"]
    # Friel is the second way in: one steady run at AeT (140 ± band), ≥ 70 min, flat drift
    run = _run(TODAY - dt.timedelta(days=3), minutes=80, hr=140.0)
    via = _gate(plan=_plan(aethr=140, lthr=165), workouts=[run])
    assert via["state"] == "unlocked" and via["via"] == "friel_drift"


def test_auto_ignores_a_stale_aet_and_a_default_lthr():
    # B3: no runs → no aggregated estimate → not valid, whatever the row's age
    stale = _gate(plan=_plan(aethr=150, lthr=160, day="2026-09-20"))
    assert stale["resolved"] == "none" and stale["stale_aet"]
    assert "需要測試" in stale["aet"]["validity"]["reason"]
    assert stale["aet_test_reason"]["code"] in ("no_data", "se")
    # AeT measured, LTHR still WKO5's default (runthr dated 1980, nothing in the plan)
    dflt = _gate(plan=_plan(aethr=150))
    assert dflt["lthr"]["default"] and dflt["resolved"] == "none"


def test_ua_gap_forced_without_aet_is_missing_watch_and_falls_back():
    g = _gate("ua_gap")
    assert g["state"] == "missing" and g["fallback"]
    t = QG.indicator(g)
    assert t["level"] == "watch" and "沒有實測 AeT，差距法算不出來" in t["verdict"] and "推估" in t["verdict"]
    assert t["action"] == "先做 AeT 飄移測試，或把間歇門檻改回自動"
    d = QG.week_decision(g, "base", "base")
    assert d["allow"] and d["spec"] is QG.Z3[0]                          # never a permanent lock
    assert QG.guardrail_mode(g)
    assert "缺資料，先照護欄排" in QG.prefix(g)


def test_friel_drift_forced():
    plan = _plan(aethr=140, lthr=165)
    assert _gate("friel_drift", plan)["state"] == "missing"             # no qualifying run
    flat = _run(TODAY - dt.timedelta(days=5), minutes=80, hr=140.0)
    assert _gate("friel_drift", plan, [flat])["state"] == "unlocked"
    drifted = _run(TODAY - dt.timedelta(days=5), minutes=80, hr=133.0, hr_end=150.0)
    g = _gate("friel_drift", plan, [drifted])
    assert g["state"] == "locked" and "飄移都 ≥ 5%" in g["verdict"]
    assert _gate("friel_drift")["state"] == "missing"                    # no measured AeT


def test_xu_drift_of_and_the_mode():
    t = np.arange(0, 95 * 60 + 1, 1.0)
    r = QG.xu_drift_of(t, np.linspace(130, 150, len(t)))
    assert r["hr10"] == pytest.approx(130 + 20 * 600 / 5700, abs=0.3)
    assert r["drift"] == pytest.approx((r["hr90"] - r["hr10"]) / r["hr10"])
    assert QG.xu_drift_of(t[:80 * 60], np.full(80 * 60, 140.0)) is None     # < 90 min
    assert _gate("xu_drift")["state"] == "missing"
    ok = _run(TODAY - dt.timedelta(days=4), minutes=95, hr=140.0)
    assert _gate("xu_drift", workouts=[ok])["state"] == "unlocked"
    hot = _run(TODAY - dt.timedelta(days=4), minutes=95, hr=130.0, hr_end=150.0)
    g = _gate("xu_drift", workouts=[hot])
    assert g["state"] == "locked" and "（≥ 10%）" in g["verdict"]
    assert QG.indicator(g)["action"] == "繼續低強度長跑，下次選 < 25 °C 的日子再測"


def test_plateau_and_weeks():
    by = dict(GOOD_BY)
    g = _gate("plateau", by=by)                        # base since 7/1 = week 14, EF +1 %
    assert g["state"] == "unlocked"
    by["efficiency"] = _ind(extra={"change": 0.03})
    g = _gate("plateau", by=by)
    assert g["state"] == "locked" and QG.indicator(g)["level"] == "info" and "EF 還在進步（+3%）" in g["verdict"]
    by["efficiency"] = _ind("na")
    assert _gate("plateau", by=by)["state"] == "missing"
    young = types.SimpleNamespace(kind="base", start="2026-08-29", end="2026-12-31")   # week 5
    g = _gate("weeks", phase=young)
    assert g["state"] == "locked" and g["verdict"] == "基礎期第 5 週 / 8 週" and QG.indicator(g)["level"] == "info"
    assert _gate("weeks", phase=young, weeks=4)["state"] == "unlocked"


def test_none_mode_and_other_phases():
    g = _gate("none")
    assert g["state"] == "none" and "不設門檻" in QG.indicator(g)["verdict"]
    spec = _gate(phase=types.SimpleNamespace(kind="specific", start="2026-11-01", end="2026-11-30"))
    assert QG.indicator(spec)["level"] == "info"
    assert QG.week_decision({**spec, "levels": {"intensity": "good", "drift": "good"}}, "specific", "specific")["allow"]
    # 專項期: intensity bad keeps Zone 5 out but Zone 3 goes on with a warning (SP-31); drift bad stops both
    d = QG.week_decision({**spec, "levels": {"intensity": "bad"}}, "specific", "specific")
    assert d["allow"] and d["track"] == "z3" and "輕鬆跑心率偏高" in d["warn"]
    assert not QG.week_decision({**spec, "levels": {"drift": "bad"}}, "specific", "specific")["allow"]


def test_options_say_what_the_data_allows():
    o = _gate()["options"]
    assert set(o) == set(QG.MODES)
    assert not o["ua_gap"]["usable"] and o["ua_gap"]["why"] == "沒有實測 AeT"
    assert o["none"]["usable"] and o["weeks"]["usable"]
    tips = QG.option_texts()
    assert "AnT ÷ AeT − 1 ≤ 10%（Uphill Athlete）：用 LTHR 當 AnT、實測 AeT。差距越小代表有氧基礎越好" in tips["ua_gap"]["tip"]
    assert "需要一次 AeT 測試" in tips["ua_gap"]["tip"]
    assert all("來源：" in v["tip"] and "要做的事：" in v["tip"] for v in tips.values())


# ---------------------------------------------------------------------------
# guardrails and the dose
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kw, flag, words", [
    (dict(low_share=0.68), "block", "低強度只有 68%（< 75%，底線）"),
    (dict(low_share=0.9, power_low_share=0.7), "block", "功率 < 80% CP 只有 70%"),
    # SP-63: relative lines — CTL₋₇ 60: watch 6, block 9; CTL₋₇ 80: block capped at 10
    (dict(ramp=6.2, ramp_base=60.0), "sub", "CTL 每週 +6.2（≥ 6.0＝CTL 60 的 10%）：本週只排閾值下"),
    (dict(ramp=8.2, ramp_base=60.0), "sub", "≥ 6.0"),
    (dict(ramp=9.1, ramp_base=60.0), "block", "≥ 9.0＝CTL 60 的 15%"),
    (dict(ramp=9.1, ramp_base=80.0), "sub", "≥ 8.0＝CTL 80 的 10%"),
    (dict(ramp=10.0, ramp_base=80.0), "block", "≥ 10.0＝上限 10"),
    (dict(ramp=5.2, ramp_base=20.0), "block", "≥ 5.0＝下限 5"),
    (dict(ramp=3.1), "sub", "≥ 3.0＝下限 3"),            # no CTL₋₇: the floors
    (dict(step=0.25), "block", "> 20%"),
    (dict(step=0.15), "hold", "10–20%"),
    (dict(tsb=-25.0), "hold", "TSB −25"),
])
def test_guardrails(kw, flag, words):
    g = QG.guard(aet=142.0, **kw)
    assert g[flag] and words in g["verdict"].replace("-", "−")
    assert QG.guard(low_share=0.8, ramp=3, ramp_base=40.0, step=0.05, tsb=-10)["rule"] == ""


def test_guardrails_block_the_week_and_say_so():
    by = {**GOOD_BY, "intensity": _ind("watch", extra={"low_share": 0.68})}
    # SP-39: the share blocks Zone 5 only with a tested AeT in effect
    g = _gate(by=by, plan=_plan(aethr=150, lthr=160, day="2026-09-01"))
    assert g["aet"]["tested"]
    t = QG.indicator(g)
    assert t["level"] == "watch" and t["verdict"] == "低強度只有 68%（< 75%，底線）：本週 5 區先不排，3 區照排"
    assert t["action"] == "輕鬆跑壓在 AeT 以下，下週再看" or "bpm 以下" in t["action"]
    # SP-31 (owner 2026-10-04): the low-intensity share no longer blocks Zone 3 — a warning only;
    # Zone 5 keeps the guardrail when the AeT is tested (SP-39)
    d = QG.week_decision(g, "base", "base")
    assert d["allow"] and d["track"] == "z3" and d["warn"].startswith("輕鬆跑心率偏高：低強度只有 68%")
    both = {**g, "z5": {"open": True}, "dose": {"z3": {"step": 3, "met": 3, "done": 3}, "z5": {"step": 0, "done": 0}}}
    # (2 a week with Zone 5 held: the second session is a 巡航版, not Zone 5)
    assert [it["track"] for it in QG.week_decision(both, "base", "base", n=2)["items"]] == ["z3", "z3"]
    assert [it.get("cruise", False) for it in QG.week_decision(both, "base", "base", n=2, first=False)["items"]] == [
        False, True]
    # an estimated AeT (no plan row, or an applied estimate): the share is noisy — a warning for Zone 5 too
    for plan in (None, _plan(aethr=150, lthr=160, day="2026-09-01", note="AeT 自動估算")):
        ge = _gate(by=by, plan=plan)
        assert not ge["aet"]["tested"] and not ge["guard"]["block"] and "AeT 是估計值" in ge["guard"]["warn"]
        both = {**ge, "z5": {"open": True}, "z3_recent": {"done": 2},
                "dose": {"z3": {"step": 3, "met": 3, "done": 3}, "z5": {"step": 0, "done": 0}}}
        d = QG.week_decision(both, "base", "base", n=2)
        assert [it["track"] for it in d["items"]] == ["z3", "z5"] and "AeT 是估計值" in d["warn"]
    # the other guardrails still block Zone 3 too
    vol = _gate(by={**by, "volume": _ind(extra={"step": 0.3, "last_week": 5.0})})
    d = QG.week_decision(vol, "base", "base")
    assert not d["allow"] and "上週跑步量增 +30%" in d["z3_note"]
    ramp = _gate(by={**GOOD_BY, "fitness": _ind(extra={"ramp_week": 5.6, "ramp_base": 50.0})})
    assert QG.week_decision(ramp, "base", "base")["spec"] is QG.SUB
    assert QG.indicator(ramp)["action"] == "先穩住量"


def test_two_tracks_each_with_its_own_ladder():
    # SP-31: the Zone 3 track (有氧間歇, reps 15–30 min) and the Zone 5 track, each with its own step
    z3 = [QG.z3_spec(i)[1] for i in range(8)]
    assert z3 == ["有氧間歇 2×15 分", "有氧間歇（巡航）3×12 分", "有氧間歇 2×20 分", "有氧間歇 連續 30 分",
                  "有氧間歇 2×20 分", "有氧間歇 連續 30 分", "有氧間歇（巡航）3×7 分", "有氧間歇 2×20 分"]        # then A3 / A4 / T+
    assert all(min(IL_.canonical(s[0]).works) >= 12 * 60 for s in QG.Z3)        # long reps (old: 6–12′)
    assert [(s[5], s[6]) for s in QG.Z3] == [(0.88, 0.95)] * 3 + [(0.88, 0.92)]
    assert [(s[5], s[6]) for s in QG.CRUISE] == [(0.90, 0.95)] * 3                # 巡航版 T1–T3 kept
    z5 = [QG.z5_spec(i)[1] for i in range(7)]
    assert z5 == ["VO2max 間歇 5×2 分", "VO2max 間歇 4×3 分", "VO2max 間歇 5×3 分", "VO2max 間歇 4×4 分",
                  "VO2max 間歇 5×3 分", "VO2max 間歇 4×4 分", "VO2max 間歇 5×3 分"]
    assert all(s[3] >= 2 for s in QG.Z5)                                  # reps ≥ 2 min
    assert [s[4] for s in QG.Z5] == [2, 3, 2.5, 3] and (QG.Z5[3][5], QG.Z5[3][6]) == (1.04, 1.08)
    # the legacy single-ladder reading still resolves
    assert QG.dose_spec(0)[0] == "a1" and QG.dose_spec(4, True)[0] == "z5b" and QG.dose_spec(4, False)[0] == "a3"
    assert QG.dose_step([]) == {"track": "z3", "done": 0, "faded": False, "step": 0, "met": 0}
    # B4: a session that can't be judged (no bouts / CP) repeats the step — no progress
    three = [{"faded": False}] * 3
    d = QG.dose_step(three)
    assert d["step"] == 0 and d["outcome"] == "unknown"
    gate = {"state": "none", "guard": {}, "z3": {"open": True},
            "dose": {"z3": {"step": 1, "met": 1, "done": 1}, "z5": {"step": 0, "done": 0}}}
    held = {**gate, "guard": {"hold": True}}
    assert QG.week_decision(held, "base", "base")["spec"][1] == "有氧間歇 2×15 分"   # held: repeat the last step
    assert not QG.week_decision(held, "base", "base")["advance"]
    rec = QG.week_decision(gate, "base", "recovery_week")
    assert rec["spec"] is QG.RECOVERY and not rec["advance"] and "恢復週" in rec["z3_note"]
    # 3 Zone 3 達標 and Zone 5 not open: Zone 3 keeps stepping, the note says why no Zone 5
    g3 = {**gate, "dose": {"z3": {"step": 3, "met": 3, "done": 3}, "z5": {"step": 0, "done": 0}},
          "z5": {"open": False, "text": "Zone 5：未確認"}}
    d = QG.week_decision(g3, "base", "base")
    assert d["spec"] is QG.Z3[3] and d["advance"] and d["track"] == "z3" and "未確認" in d["note"]
    # SP-39: Zone 5's AeT passed but the soft 「3 區先」 not met (1 of 2 in 6 weeks): still Zone 3 only,
    # the note says what Zone 5 waits for; 2 Zone 3 sessions done → both tracks
    g1 = {**g3, "dose": {"z3": {"step": 1, "met": 1, "done": 1}, "z5": {}}, "z5": {"open": True},
          "z3_recent": {"done": 1, "need": 2}}
    d = QG.week_decision(g1, "base", "base", n=2)
    assert [it["track"] for it in d["items"]] == ["z3", "z3"] and "1/2 堂 3 區" in d["note"]
    g2 = {**g1, "z3_recent": {"done": 2, "need": 2}}
    assert [it["track"] for it in QG.week_decision(g2, "base", "base", n=2)["items"]] == ["z3", "z5"]
    # not tied to 達標: two Zone 3 sessions done at the first rung count
    assert QG.z5_track(g2)["open"] and not QG.z5_track(g1)["open"]


def test_zone3_continues_after_zone5_opens_and_the_week_split():
    gate = {"state": "none", "guard": {}, "z3": {"open": True}, "z5": {"open": True},
            "dose": {"z3": {"step": 3, "met": 3, "done": 3}, "z5": {"step": 1, "done": 1}},
            "ratio": {"z3": 2, "z5": 1, "why": "A 賽越野"}}
    # 2 a week: one Zone 3 + one Zone 5 (each its own rung), not a copy
    d = QG.week_decision(gate, "base", "base", n=2)
    assert [(it["track"], it["spec"][0]) for it in d["items"]] == [("z3", "a4"), ("z5", "z5b")]
    assert d["z3_note"] == "" and all(it["advance"] for it in d["items"])
    # 1 a week: 2:1 for a trail / long race, deterministic by the week (3 consecutive weeks)
    mon = date(2026, 9, 28)
    picks = [QG.week_decision(gate, "base", "base", mon + dt.timedelta(weeks=i))["track"] for i in range(6)]
    assert sorted(picks[:3]) == ["z3", "z3", "z5"] and picks[:3] == picks[3:]
    z5wk = next(mon + dt.timedelta(weeks=i) for i in range(3) if picks[i] == "z5")
    d = QG.week_decision(gate, "base", "base", z5wk)
    assert d["spec"][0] == "z5b" and "本週輪到 5 區" in d["z3_note"] and "2:1" in d["z3_note"]
    # 1:1 for a ≤ 10 km road race
    g11 = {**gate, "ratio": {"z3": 1, "z5": 1, "why": "A 賽 10 km 路跑"}}
    picks = [QG.week_decision(g11, "base", "base", mon + dt.timedelta(weeks=i))["track"] for i in range(4)]
    assert sorted(picks) == ["z3", "z3", "z5", "z5"] and picks[0] != picks[1]
    # the projection's steps carry each track on
    d = QG.week_decision(gate, "base", "base", step={"z3": 5, "z5": 3, "met": 5}, first=False, n=2)
    assert [it["spec"][0] for it in d["items"]] == ["a4", "z5d"]
    # the guardrails apply to both tracks
    blocked = {**gate, "guard": {"block": True, "rule": "volume", "verdict": "上週量增 +30%"}}
    d = QG.week_decision(blocked, "base", "base", n=2)
    assert not d["allow"] and d["items"] == [] and d["z3_note"] == "本週沒排 3 區：上週量增 +30%"


def test_track_ratio_by_the_a_race():
    from backend.engine.planning import Event
    ev = lambda kind, km: Event(id="x", name="x", date="2026-12-01", kind=kind, priority="A", distance_km=km)
    assert (QG.track_ratio([ev("road", 10)], TODAY)["z3"], QG.track_ratio([ev("road", 21.1)], TODAY)["z3"]) == (1, 2)
    assert QG.track_ratio([ev("race", 8)], TODAY)["z3"] == 2                  # trail: 2:1 whatever the distance
    assert QG.track_ratio([], TODAY) == {"z3": 2, "z5": 1, "why": "沒有 A 賽"}


def test_dose_sessions_parse_for_coros_and_the_cap():
    th = {"cp": 250.0, "lthr": 165.0, "aet": 142.0}
    # the legacy text builder (recovery fartlek, Zone 3 HR, adapt's old rows) on a ladder row:
    # no rest after the last rep (§A5.2-3)
    s = QG.session(QG.CRUISE[1], th, "沒有 AeT 實測：照 80/20 原則每週 1 次間歇，")
    assert s["title"] == "有氧間歇（巡航）3×8 分" and s["minutes"] == 15 + 3 * 8 + 2 * 2 + 10
    assert s["detail"].startswith("沒有 AeT 實測：照 80/20 原則每週 1 次間歇，") and "休 2 分" in s["detail"]
    steps = CW.session_steps(s, CW.Thresholds.of(th))
    rep = steps[1]
    assert rep.sets == 3 and rep.steps[0].seconds == 480 and rep.steps[1].seconds == 120
    assert rep.steps[0].intensity == ("power", 225, 238)                    # 90–95 % CP
    v = QG.session(QG.Z5[0], th)
    assert "休 2 分鐘（走路或極慢跑）" in v["detail"]                         # Buchheit: < 2–3 min passive
    st = CW.session_steps(v, CW.Thresholds.of(th))
    assert st[1].sets == 5 and st[1].steps[0].seconds == 120 and st[1].steps[0].intensity == ("power", 265, 280)
    assert "心率" not in QG.session(QG.Z5[3], th, lthr_default=True)["target"]      # WKO5 default LTHR
    assert QG.hard_need("閾值 3×8 分", 600) == pytest.approx(600)
    assert QG.hard_need("VO2max 5×2 分", 600, "v1a") == pytest.approx(360)
    # a library variant goes to COROS as its own steps: blocks, every rep / rest, walk rests untargeted
    from backend.engine import interval_library as IL
    f = IL.fit("z5a", 45)
    ls = IL.session_for(f, th)
    cs = CW.session_steps({**ls, "day": "2026-10-07"}, CW.Thresholds.of(th))
    names = [x.name for x in cs]
    assert names[:3] == ["輕鬆跑暖身", "動態伸展／drill", "快步跑 3×20 秒"] and names[-1] == "緩和"
    work = [x for x in cs if isinstance(x, CW.Step) and x.kind == CW.EX_TRAIN]
    rest = [x for x in cs if isinstance(x, CW.Step) and x.kind == CW.EX_REST]
    assert len(work) == 5 and len(rest) == 4 and all(r.intensity is None and r.name == "走路或極慢跑" for r in rest)
    assert work[0].intensity == ("power", 265, 280)
    total = CW.session_workout({**ls, "day": "2026-10-07"}, th, "2026-10-01").payload["estimatedTime"]
    assert total == ls["minutes"] * 60
    z3 = QG.session(QG.ZONE3, th, hours=6.0)
    assert z3["title"] == "Zone 3 間歇 3×6 分" and "心率 142–165 bpm" in z3["target"]


def _reps_run(day, reps=5, fade=0.0, on=250.0):
    p = [150.0] * 900
    for k in range(reps):
        p += [on * (1 - fade * k / max(1, reps - 1))] * 60 + [150.0] * 120
    p += [150.0] * 900
    t = np.arange(len(p), dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": [140.0] * len(t), "speed": [10.0] * len(t), "power": p,
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"], sport_type="running",
                       channels=ch, metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                             "distance": len(t) / 360.0, "climbing": 10.0})


def test_count_reps_finds_one_minute_reps_and_the_history_counts_them():
    r = _reps_run(TODAY - dt.timedelta(days=5))
    ch = r.channels
    reps = QG.count_reps(np.array(ch["elapsedtime"]), np.array(ch["power"]), 250.0)
    assert len(reps) == 5 and all(40 <= x["duration_s"] <= 70 for x in reps)
    # 260 → 239 W: every rep still ≥ 95 % CP, the last 8 % down = faded
    ds = _ds([r, _reps_run(TODAY - dt.timedelta(days=12), fade=0.08, on=260.0), _run(TODAY - dt.timedelta(days=2))])
    h = QG.dose_history(ds, TODAY)
    assert len(h) == 2 and h[0]["faded"] and not h[1]["faded"] and h[1]["reps"] == 5
    # two tracks (SP-31): ≥ 4 short reps without a plan row are a Zone 5 session — judged against
    # V1 5×2′ @ 106–112 % CP: 1-minute reps at ~104 % don't reach it; the Zone 3 track has none
    assert [x["track"] for x in h] == ["z5", "z5"]
    assert QG.dose_step(h)["done"] == 0
    d = QG.dose_step(h, track="z5")
    assert d["step"] == 0 and d["done"] == 2 and d["outcome"] in ("too_high", "unadapted")
    assert d["note"].startswith("上次 5 區間歇")


def _z3_run(day, reps=3, work_min=8, rest_min=2, on=230.0, title=""):
    """Zone 3 session (interval-prescription.md S0): 12′ easy @ 70 % CP, reps × work @ `on`
    (230 W = 92 % of CP 250) with jog rests @ 64 %, 5′ cool-down. The reps are more than
    half the moving time, so the session median ≈ the rep power."""
    p = [175.0] * 720
    for k in range(reps):
        p += [on] * (work_min * 60)
        if k < reps - 1:
            p += [160.0] * (rest_min * 60)
    p += [160.0] * 300
    t = np.arange(len(p), dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": [150.0] * len(t), "speed": [10.0] * len(t), "power": p,
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"], sport_type="running",
                       channels=ch, title=title,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 10.0})


def test_zone3_reps_are_found_and_not_judged_too_high():
    # bug (b), interval-prescription.md §A5.2-2: count_reps needs ≥ 95 % CP and detect_efforts'
    # threshold max(0.85 CP, 1.12 × median) sits above 92 % reps → no bouts → 「第 1 趟就沒到」
    ds = _ds([_z3_run(TODAY - dt.timedelta(days=4))])
    h = QG.dose_history(ds, TODAY)
    assert len(h) == 1
    assert len(h[0]["bouts"]) == 3 and all(b["power"] == pytest.approx(230.0, abs=1) for b in h[0]["bouts"])
    o = QG.interval_outcome(h[0]["bouts"], QG.Z3[0], 250.0)
    assert o["outcome"] != "too_high" and o["outcome"] == "met"
    assert h[0]["rep_source"] == "z3"
    # with no bouts at all the session can't be judged: unknown, never 「目標太高」
    d = QG.dose_step([{"bouts": [], "cp": 250.0}])
    assert d["outcome"] == "unknown" and d.get("adjust") == {}


def test_a_hard_long_run_is_not_an_interval_session():
    # 90′ with 3×8′ at 92 % CP inside: 高強度長跑 (Zone 3 ≥ 10′, ≥ 75′) — a hard day, not an interval
    from backend.engine import workout_review as WR
    w = _z3_run(TODAY - dt.timedelta(days=4))
    p = [175.0] * 2700 + list(w.channels["power"])
    t = np.arange(len(p), dtype=float)
    w.channels.update({"elapsedtime": list(t), "power": p, "heartrate": [150.0] * len(t), "speed": [10.0] * len(t),
                       "elapseddistance": list(t * 10 / 3600)})
    w.metrics.update({"duration": float(len(t)), "movingduration": float(len(t)), "distance": len(t) / 360.0})
    ds = _ds([w])
    c = WR.classify(ds, ds.workouts[0])
    assert c["type"] == "hard_long" and c["stimulus"] == "z3" and c["type_label"] == "高強度長跑"
    assert QG.dose_history(ds, TODAY) == []
    assert O.session_of(ds, ds.workouts[0])["type"] == "hard_long"
    assert QG.is_z5_variant("v1a") and not QG.is_z5_variant("t1a") and not QG.is_z5_variant(None)


def test_unplanned_hard_runs_are_not_ladder_steps_once_the_plan_is_in_use(monkeypatch):
    # real data 2026-10-01: 43 steady runs at ~95 % CP in 8 weeks, none a planned session, were
    # judged against 3×8′ (13 「目標太高」, 18 未適應) and moved the ladder to step 3
    from backend.engine import plan_store as PS
    ds = _ds([_z3_run(TODAY - dt.timedelta(days=4)), _z3_run(TODAY - dt.timedelta(days=2))])
    monkeypatch.setattr(PS, "plan_in_use", lambda db_path=None: True)
    monkeypatch.setattr(PS, "done_plan", lambda db_path=None: {1: {"title": QG.CRUISE[1][1], "state": "done"}})
    h = QG.dose_history(ds, TODAY)
    assert [x.get("unplanned", False) for x in h] == [True, False]
    d = QG.dose_step(h)
    # the planned 3×8′ (T2, an old Zone 3 rung) counts as a Zone 3 達標 without moving the A rung
    assert h[0]["outcome"] == "neutral" and h[1]["outcome"] == "met" and d["met"] == 1 and d["done"] == 1


def test_planned_zone3_reps_come_from_the_coros_laps(monkeypatch):
    # interval-prescription.md §B3: a pushed workout has one lap per step — the laps whose
    # duration is the planned rep (±5 s) are the reps; power only when there are no laps
    from backend.engine import interval_reps as IR
    from backend.engine import plan_store as PS
    day = TODAY - dt.timedelta(days=4)
    w = _z3_run(day)
    ds = _ds([w])
    monkeypatch.setattr(PS, "done_plan", lambda db_path=None: {0: {"title": QG.CRUISE[1][1], "state": "done"}})
    laps = [{"start_s": 0.0, "duration_s": 720.0, "power": 175.0}]
    t = 720.0
    for k in range(3):
        laps.append({"start_s": t, "duration_s": 481.0, "power": 230.0})
        t += 480
        if k < 2:
            laps.append({"start_s": t, "duration_s": 120.0, "power": 160.0})
            t += 120
    laps.append({"start_s": t, "duration_s": 300.0, "power": 160.0})
    ds.laps = lambda idx: laps
    h = QG.dose_history(ds, TODAY)
    assert h[0]["rep_source"] == "lap" and len(h[0]["bouts"]) == 3
    assert [round(b["start_s"]) for b in h[0]["bouts"]] == [720, 1320, 1920]
    # 1-km auto laps (≈ 6′, the wrong length) are not reps: back to the power pattern
    ds2 = _ds([_z3_run(day)])
    ds2.laps = lambda idx: [{"start_s": 360.0 * i, "duration_s": 360.0, "power": 200.0} for i in range(10)]
    h2 = QG.dose_history(ds2, TODAY)
    assert h2[0]["rep_source"] == "power" and len(h2[0]["bouts"]) == 3
    assert IR.lap_bouts(laps, [480, 480, 480], 0.88, 250.0, 120)[0]["source"] == "lap"
    # the user's real 1-km auto laps are ≈ 8′ (482–485 s) back to back: never a 3×8′ set
    auto = [{"start_s": 483.0 * i, "duration_s": 483.0, "power": 230.0} for i in range(6)]
    assert IR.lap_bouts(auto, [480, 480, 480], 0.88, 250.0, 120) == []


def _b(*ps, at60=None):
    return [{"power": p, "hr_at60": at60} for p in ps]


def test_variant_fields_are_stored_and_regenerated():
    from backend.db.models import PlanSession
    from backend.engine import plan_store as PS
    from backend.engine import reconcile as R
    g = {"id": "quality", "kind": "quality", "title": "閾值 3×8 分", "minutes": 45, "day": "2026-10-07",
         "variant_key": "t2a", "rung_key": "z3b", "equiv": True, "swap": "cap", "swap_reason": "平日上限 45 分 → 標準版",
         "variant_reps": None, "variant_blocks": "std", "variant_adj": {"rest_add": 1}}
    s = R.session_from_gen(g, "2026-10-05", False, "u1")
    assert {k: s[k] for k in ("variant_key", "rung_key", "equiv", "swap", "variant_blocks", "variant_adj")} == \
        {"variant_key": "t2a", "rung_key": "z3b", "equiv": True, "swap": "cap", "variant_blocks": "std",
         "variant_adj": {"rest_add": 1}}
    r = PlanSession(athlete_id=1, uid="u1")
    PS._fill(r, s)
    back = PS.to_dict(r)
    assert back["variant_key"] == "t2a" and back["variant_adj"] == {"rest_add": 1} and back["equiv"] is True
    p = PS.push_dict(back)
    assert p["variant_key"] == "t2a" and p["variant_blocks"] == "std"


def test_bug_a_a_shortened_session_still_moves_the_ladder():
    # bug (a), interval-prescription.md §A5.2-1: under a 45/50-min weekday cap the old
    # trim_quality turned 4×8′ into 「閾值 3×8 分」; dose_step matched the *title* to the first
    # rung (not where the ladder stood) → neutral → the 4×8′ / 3×10′ steps never moved.
    # The stored variant key (and its rung) is what is judged now.
    cp = 250.0
    good = _b(*[240] * 6)
    first = {"bouts": good, "cp": cp, "title": "閾值 2×15 分", "variant_key": "a1a", "rung_key": "a1", "equiv": True}
    trimmed = {"bouts": good, "cp": cp, "title": "閾值 連續 32 分", "variant_key": "a2c", "rung_key": "a2",
               "equiv": True}                              # an equivalent shorter variant of the second rung
    d = QG.dose_step([first, trimmed])
    assert trimmed["outcome"] == "met" and d["step"] == 2 and d["met"] == 2
    # a 縮量版 (TIZ < 85 %, equiv False) is judged for display only: the rung doesn't move …
    reduced = {"bouts": good, "cp": cp, "variant_key": "a2a", "variant_reps": 2, "rung_key": "a2", "equiv": False}
    d = QG.dose_step([first, reduced])
    assert reduced["outcome"] == "met" and reduced.get("counted") is False and d["step"] == 1
    # … and the old title-only rows still work the old way (backward compatible)
    old = {"bouts": good, "cp": cp, "title": QG.Z3[0][1]}
    assert QG.dose_step([old])["step"] == 1


def test_legacy_zone3_rungs_still_resolve_and_count_for_zone5():
    # SP-31: stored sessions of the old Zone 3 rungs (rung_key z3a / z3b / z3c, now the 巡航版 T1–T3)
    # are judged against their own variant: their 達標 count for Zone 5's 「3 區達標」, but they don't
    # move the new Zone 3 track (A1–A4) — and the Zone 5 rows of the old single ladder still step Zone 5
    cp = 250.0
    good = _b(*[240] * 6)
    rows = [{"bouts": good, "cp": cp, "variant_key": k, "rung_key": r, "equiv": True}
            for k, r in (("t1a", "z3a"), ("t2c", "z3b"), ("t3a", "z3c"))]
    rows.append({"bouts": _b(*[275] * 5), "cp": cp, "variant_key": "v1a", "rung_key": "z5a", "equiv": True})
    assert [QG.row_track(h) for h in rows] == ["z3", "z3", "z3", "z5"]
    d = QG.dose_tracks(rows)
    assert d["z3"]["step"] == 0 and d["z3"]["met"] == 3 and [h["outcome"] for h in rows[:3]] == ["met"] * 3
    assert all(h["counted"] is False for h in rows[:3])
    assert d["z5"]["step"] == 1 and rows[3]["outcome"] == "met"
    assert d["step"] == 0 and d["done"] == 4                       # the legacy top-level keys = the Zone 3 track
    # title-only rows of the old rungs resolve too (T2 「閾值 3×8 分」)
    assert QG.spec_by_title("閾值 3×8 分")[0] == "z3b" and QG.row_track({"title": "閾值 3×8 分"}) == "z3"
    # Zone 5 is reachable with the old history: 3 達標 → Zone 5 open → the week takes Zone 5 on its turn
    gate = {"state": "none", "guard": {}, "z3": {"open": True}, "z5": {"open": True}, "dose": d}
    assert [it["track"] for it in QG.week_decision(gate, "base", "base", n=2)["items"]] == ["z3", "z5"]
    # adapt / editors: the cruise rungs keep their library rows and names
    assert IL_.RUNG_NAME["z3b"] == "T2" and IL_.canonical("z3c").key == "t3a" and IL_.track_of("z3a") == "z3"


def test_interval_outcome_state_machine_rows():
    # a 5×1′ spec at 98–101 % CP (the old first rung); floor = 0.98 × 0.98 × 250 = 240 W
    spec = ("d1", "短間歇 5×1 分", 5, 1, 2, 0.98, 1.01, False, "test")
    cp = 250.0
    met = QG.interval_outcome(_b(250, 250, 249, 248, 247), spec, cp)
    assert met["outcome"] == "met"
    last_small = QG.interval_outcome(_b(250, 250, 249, 248, 239), spec, cp)       # −4.4 %: still 達標
    assert last_small["outcome"] == "met" and last_small["first_miss"] == 5
    last_big = QG.interval_outcome(_b(255, 250, 249, 248, 235), spec, cp)         # −7.8 %: 邊界
    assert last_big["outcome"] == "border"
    rep2 = QG.interval_outcome(_b(250, 230, 249, 248, 247), spec, cp)
    assert rep2["outcome"] == "unadapted" and rep2["first_miss"] == 2
    short = QG.interval_outcome(_b(250, 250, 250), spec, cp)
    assert short["outcome"] == "unadapted" and short["done"] == 0.6
    first = QG.interval_outcome(_b(230, 250, 250, 250, 250), spec, cp)
    assert first["outcome"] == "too_high"
    # HR brake: power fine but HR still above AeT 60 s into the rest on most reps
    brake = QG.interval_outcome(_b(250, 250, 250, 250, 250, at60=150), spec, cp, aet=140)
    assert brake["outcome"] == "border"
    assert QG.interval_outcome(_b(250, 250, 250, 250, 250, at60=135), spec, cp, aet=140)["outcome"] == "met"
    assert QG.interval_outcome(_b(250), spec, None)["outcome"] is None


def test_dose_step_replays_outcomes():
    cp = 250.0
    good = {"bouts": _b(*[250] * 6), "cp": cp}
    bad = {"bouts": _b(250, 190, 190, 190), "cp": cp}      # rep 2 below 0.98 × 88 % CP = 216 W
    assert QG.dose_step([good, good])["step"] == 2
    d = QG.dose_step([good, bad])                         # step 1 未適應 once: hold + rest +1
    assert d["step"] == 1 and d["adjust"] == {"rest_add": 1} and d["faded"]
    d = QG.dose_step([good, bad, bad])                    # twice in a row: back one
    assert d["step"] == 0 and d["adjust"] == {}
    # a recovery fartlek done at step 2: neutral, not judged
    fart = {"bouts": _b(248, 248, 248, 248), "cp": cp, "title": QG.RECOVERY[1]}
    d = QG.dose_step([good, {**good, "title": QG.Z3[1][1]}, fart])
    assert d["step"] == 2 and d.get("adjust") == {} and fart["outcome"] == "neutral"
    # a ramp-week SUB (3×8′) when the ladder stands further on: neutral
    sub = {"bouts": _b(190, 190, 190), "cp": cp, "title": QG.SUB[1]}
    assert QG.dose_step([good, sub])["step"] == 1 and sub["outcome"] == "neutral"
    # a 巡航版 (old Zone 3 rung) off the track's rung: neutral position, each track its own step
    assert QG.planned_spec(QG.CRUISE[2][1], 4) == (QG.CRUISE[2], True)
    assert QG.planned_spec(QG.Z3[2][1], 4) == (QG.Z3[2], False)              # A3 = the first maintenance rung
    assert QG.planned_spec(QG.Z5[1][1], 1, "z5") == (QG.Z5[1], False)
    # the old ladder's titles don't count any more
    assert QG.planned_spec("短間歇 5×1 分", 0)[1] is True
    d = QG.dose_step([{"bouts": _b(150, 250, 250, 250, 250), "cp": cp}])
    assert d["step"] == 0 and d["adjust"] == {"power": QG.TARGET_DOWN}
    spec = QG.adjusted_spec(QG.Z3[0], {"power": 0.95})
    assert spec[5] == round(0.88 * 0.95, 3) and spec[1] == QG.Z3[0][1]
    g = {"state": "none", "guard": {}, "dose": {"done": 2, "step": 1, "adjust": {"rest_add": 1}}}
    dec = QG.week_decision(g, "base", "base")
    sp = dec["spec"]
    assert sp[4] == QG.Z3[1][4] + 1 and dec["adjust"] == {"rest_add": 1}
    s = QG.session(sp, {"cp": cp})
    assert f"休 {sp[4]:g} 分鐘" in s["detail"]


# ---------------------------------------------------------------------------
# status + week_plan + projection
# ---------------------------------------------------------------------------

def _daily(n=56, **kw):
    ws = [_run(TODAY - dt.timedelta(days=d), power=180.0, **kw) for d in range(1, n)]
    for w in ws:
        w.metrics["tss"] = 40.0
    return ws


def test_status_i_gate_and_week_plan_schedule_the_dose():
    plan = _plan(lthr=165, day="2026-09-01")
    plan.thresholds.append(Threshold("2026-09-20", cp=250.0))
    ds = _ds(_daily(), plan)
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    by = {i.id: i for i in st.indicators}
    assert by["gate"].level in ("info", "watch") and by["drift"].level in ("info", "na", "bad")
    assert "streak_ok" not in by["drift"].extra
    wp = O.week_plan(ds, st, TODAY)
    g = wp["quality_gate"]
    assert g["mode"] == "auto" and g["resolved"] == "none"
    q = [s for s in wp["sessions"] if s["kind"] == "quality"]
    assert g["allowed"]
    if g["aet_test"]["due"] and wp["mode"] != "recovery_week":
        # B3: no aggregate yet → the AeT test is due; auto = 徐國峰 90 min in place of the long run
        assert any(s["id"] == "test_aet" and s["minutes"] == 90 for s in wp["sessions"])
    if wp["mode"] == "recovery_week":                           # flat weeks = 3 builds → 3:1
        assert [s["title"] for s in q] == ["恢復週 fartlek 4×1 分"]
    else:
        assert [s["title"] for s in q] == ["閾值 3×8 分"] and "80/20" in q[0]["detail"]
    # forced mode with missing data: the same guardrail plan, never locked
    st2 = Status(ds, plan, TODAY, prefs=PP.Prefs(quality_gate="ua_gap")).compute()
    gate = next(i for i in st2.indicators if i.id == "gate")
    assert gate.level == "watch" and gate.extra["fallback"]
    assert any("間歇門檻" in a.title for a in st2.actions)            # 還缺什麼 says why


def test_projection_advances_the_dose_and_evaluates_weeks_per_week():
    from backend.tests.test_plan_store import cur_plan
    cur = cur_plan(sessions=[])
    cur["history"] = [{"start": "x", "hours": 5.0, "tss": 250} for _ in range(8)]    # flat: no 3:1 yet
    cur["quality_gate"] = cur_g = {"state": "locked", "mode": "weeks", "resolved": "weeks", "verdict": "基礎期第 7 週 / 8 週",
                           "base_start": "2026-08-17", "weeks_need": 8, "levels": {"intensity": "good"},
                           "guard": {}, "dose": {"step": 3, "done": 3, "faded": False,
                                                 "z3": {"step": 1, "met": 3, "done": 3}, "z5": {"step": 0, "done": 0}},
                           "aet": {"date": "2026-09-01"}, "z5": {"open": False}, "z3": {"open": True},
                           "ratio": {"z3": 2, "z5": 1, "why": "沒有 A 賽"}}
    phases = [{"kind": "base", "start": "2026-08-17", "end": "2026-12-31"}]
    weeks = P.project_weeks(cur, phases, date(2026, 11, 22))
    first_q = {w["start"]: [s["title"] for s in w["sessions"] if s["kind"] == "quality"] for w in weeks}
    # SP-39: the weeks method opens Zone 3 only — Zone 5 needs a measured AeT, so no Zone 5 week.
    # One rung per Zone 3 week; a rung over 10 % of the week (Daniels) is its 巡航版: A2 3×12′ =
    # 36′ > 30′ of 5.1 h → T2 3×8′; A3 2×20′ = 40′ > 37′ → T3 2×12′; A4 1×30′ fits
    q = [(k, v[0]) for k, v in sorted(first_q.items()) if v
         and next(w for w in weeks if w["start"] == k)["mode"] != "recovery_week"]
    assert all(k >= "2026-10-12" for k, _ in q)
    assert not [x for _, x in q if x.startswith("VO2max")]
    assert [x for _, x in q][:3] == ["有氧間歇（巡航）3×8 分", "有氧間歇（巡航）2×12 分", "有氧間歇 連續 30 分"]
    # the measured-AeT gate passed but no Zone 3 session yet: the projection counts its own Zone 3
    # weeks for the soft 「近 6 週 ≥ 2 堂 3 區」 (z5_track via steps["z3_dates"]) — Zone 5 from then on
    cur = cur_plan(sessions=[])
    cur["history"] = [{"start": "x", "hours": 5.0, "tss": 250} for _ in range(8)]
    cur["quality_gate"] = {**cur_g, "z5": {"open": True}, "z3_recent": {"done": 0, "dates": []},
                           "dose": {"step": 0, "done": 0, "z3": {"step": 0, "met": 0, "done": 0},
                                    "z5": {"step": 0, "done": 0}}}
    weeks = P.project_weeks(cur, phases, date(2026, 11, 22))
    order = [s["title"] for w in weeks if w["mode"] != "recovery_week" for s in w["sessions"] if s["kind"] == "quality"]
    i5 = next(i for i, t in enumerate(order) if t.startswith("VO2max"))
    assert i5 >= QG.Z5_Z3_NEED and not any(t.startswith("VO2max") for t in order[:QG.Z5_Z3_NEED])


# ---------------------------------------------------------------------------
# the AeT drift test
# ---------------------------------------------------------------------------

def _aet_series(rise, warm=10, main=40, cool=0, temp=None, finish=1.0):
    """UA's minimum test by default: 10′ warm-up + 40′ fixed power, no cool-down."""
    n_w, n_m, n_c = warm * 60, main * 60, cool * 60
    hr = [128.0] * n_w + list(np.linspace(143.0, 143.0 + rise, n_m)) + [130.0] * n_c
    pw = [180.0] * n_w + [200.0] * int(n_m * 0.9) + [200.0 * finish] * (n_m - int(n_m * 0.9)) + [140.0] * n_c
    t = np.arange(len(hr), dtype=float)
    sp = np.full(len(t), 10.0)
    return t, np.array(hr), sp, np.array(pw), None if temp is None else np.full(len(t), temp)


@pytest.mark.parametrize("rise, band", [(4.0, "below"), (12.9, "at"), (25.0, "above")])
@pytest.mark.parametrize("main, cool", [(40, 0), (40, 5), (60, 5)])
def test_aet_analysis_bands(rise, band, main, cool):
    # the planned 50-min test (stopped right at 40′, or with a cool-down) and a longer 60′ one
    t, h, s, p, _ = _aet_series(rise, main=main, cool=cool)
    r = AT.analyze(t, h, s, p)
    assert r["ok"] and r["basis"] == "Pw:HR" and r["band"] == band
    assert r["main_s"] == pytest.approx(main * 60, abs=90)          # warm-up and cool-down cut
    if main == 40 and cool == 0:
        assert r["main_s"] == 2400                                  # nothing trimmed at the very end
    assert r["hr1"] == pytest.approx(143.0 + rise / 4, abs=0.6)    # first 20′ vs last 20′ on the 40′
    line = " ".join(AT.lines(r, 142.0))
    assert {"below": "< 3.5%", "at": "3.5–5%", "above": "> 5%"}[band] in line


def test_aet_analysis_refuses_short_fast_finish_and_hills():
    t, h, s, p, _ = _aet_series(12.9, main=35)
    assert "< 40 分" in AT.analyze(t, h, s, p)["reason"]
    t, h, s, p, _ = _aet_series(12.9, main=39)
    assert "暖身後只有 39 分鐘" in AT.analyze(t, h, s, p)["reason"]
    t, h, s, p, _ = _aet_series(12.9, finish=1.12)
    assert "快速結尾" in AT.analyze(t, h, s, p)["reason"]
    t, h, s, p, _ = _aet_series(12.9)
    assert "有坡" in AT.analyze(t, h, s, p, climb_m_per_km=30.0)["reason"]


def test_aet_analysis_in_heat_counts_and_says_so():
    # heat bands: a hot test is not refused. 「at」 in heat counts (the AeT can only be low:
    # conservative); 「above」 in heat says it may be the heat
    t, h, s, p, tp = _aet_series(12.9, temp=33.0)               # watch 33 °C − 3.7 = 29.3: hot
    r = AT.analyze(t, h, s, p, tp)
    assert r["ok"] and r["band"] == "at" and r["temp_band"] == "hot" and r["heat"]
    assert r["temp_src"] == "watch" and r["temp_c"] == pytest.approx(33.0 - 3.7)
    ln = AT.lines(r, 142.0)
    assert "3.5–5%" in ln[0] and "熱環境，結果可能偏高" in ln[-1] and "熱天通過仍算數" in ln[-1]
    up = AT.analyze(*_aet_series(25.0)[:4], temp_c=26.0, temp_src="route_weather")
    assert up["ok"] and up["band"] == "above" and up["temp_band"] == "warm"
    assert "可能是熱造成的" in AT.lines(up)[-1]
    cool = AT.analyze(*_aet_series(12.9)[:4], temp_c=22.0, temp_src="route_weather")
    assert cool["temp_band"] == "cool" and not any("熱環境" in x for x in AT.lines(cool))


def test_aet_analysis_subtracts_the_athletes_own_wrist_bias(monkeypatch):
    # SP-51: the watch's block mean minus the dataset's wrist bias (zone_events.dataset_watch_bias)
    from backend.engine import workout_review as R
    own = {"bias_c": 2.0, "sd_c": 0.5, "n": 12, "src": "dataset", "pairs": 12}
    t, h, s, p, tp = _aet_series(12.9, temp=29.0)
    r = AT.analyze(t, h, s, p, tp, watch_bias=own)               # 29 − 2.0 = 27: warm, not 25.3 cool
    assert r["temp_c"] == pytest.approx(27.0) and r["temp_band"] == "warm"
    assert r["temp_bias"] == {"bias_c": 2.0, "own": True, "pairs": 12}
    assert AT.analyze(t, h, s, p, tp)["temp_c"] == pytest.approx(29.0 - 3.7)     # no bias given: the default
    xt, xh, xv = _xu_series(rise=5)
    xu = AT.analyze(xt, xh, xv, None, np.full(len(xt), 29.0), judge="xu", watch_bias=own)
    assert xu["temp_c"] == pytest.approx(27.0) and xu["temp_bias"]["own"]
    # analyze_workout: no archive temperature → the dataset's bias
    w = _aet_workout(TODAY - dt.timedelta(days=3))
    w.channels["temperature"] = [29.0] * len(w.channels["elapsedtime"])
    ds = _ds([w])
    ds.activity_temps = {}
    monkeypatch.setattr(R, "watch_bias_of", lambda _ds: own)
    r = AT.analyze_workout(ds, ds.workouts[0])
    assert r["ok"] and r["temp_src"] == "watch" and r["temp_c"] == pytest.approx(27.0)


def _aet_workout(day, rise=12.9, title="WKO5 AeT 飄移測試 40 分", main=40):
    t, h, s, p, _ = _aet_series(rise, main=main)
    ch = {"elapsedtime": list(t), "heartrate": list(h), "speed": list(s), "power": list(p),
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"], sport_type="running",
                       title=title, channels=ch,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 5.0})


def test_latest_aet_test_review_card_and_the_apply_button():
    from backend.engine import workout_review as R
    day = TODAY - dt.timedelta(days=3)
    ds = _ds([_aet_workout(day)])
    w = ds.workouts[0]
    assert R.classify(ds, w)["type"] == "test_aet"                  # not test_cp despite 「測試」
    at = AT.latest_aet_test(ds, TODAY)
    assert at["band"] == "at" and at["aethr_suggest"] == round(at["hr1"])
    body = AT.apply_body(at)
    assert body["date"] == day.isoformat() and body["aethr"] == at["aethr_suggest"]
    assert body["note"].startswith(f"AeT 飄移測試 {day.isoformat()}：心率飄移 ")      # plain words, no Pw:HR
    assert at["basis"] == "Pw:HR"                                        # the data field keeps the basis
    card = R.review(ds, w, "summary")                                # the viewer's drawAction hook
    assert card["action"]["body"] == body and card["action"]["label"].startswith("套用這次的 AeT")
    assert card["action"]["url"] == "/api/v1/plan/thresholds/apply-estimate" and card["action"]["method"] == "POST"
    assert "action" in R.review(ds, w, "aerobic")
    # once the plan has an AeT dated on / after the test, no button
    ds.plan.thresholds.append(Threshold(day.isoformat(), aethr=body["aethr"], note=body["note"]))
    assert "action" not in R.review(ds, w, "summary") and AT.applied(ds.plan, at)


def _aet_row(day, idx=0, **kw):
    """A stored AeT-test session (plan_store.test_sessions shape), done by activity `idx`."""
    row = {"uid": "aet1", "day": day.isoformat(), "state": "done", "title": AT.TITLE, "protocol": AT.PROTOCOL,
           "gen_key": "test_aet", "done_by": {"index": idx, "date": day.isoformat()}}
    row.update(kw)
    return row


@pytest.mark.parametrize("row", [
    {},                                                                  # protocol "aet"
    {"protocol": None},                                                  # a row stored before the field: gen_key
    {"protocol": None, "gen_key": None, "title": "AeT 測試（自訂）"},       # a custom session: its title
])
def test_aet_test_is_matched_through_the_plans_done_by(row):
    from backend.engine import workout_review as R
    day = TODAY - dt.timedelta(days=3)
    # title 「飄移測試」 has 測試 but no AeT: without the plan it would be read as a CP test
    ds = _ds([_aet_workout(day, title="週三 飄移測試")])
    w = ds.workouts[0]
    assert R.classify(ds, w)["type"] == "test_cp"
    ds.plan_test_sessions = [_aet_row(day, **row)]
    c = R.classify(ds, w)
    assert c["type"] == "test_aet" and c["test_match"] == "done_by" and c["protocol"] is None
    assert R.scheduled_aet_test(ds, w)["uid"] == "aet1"
    assert R.scheduled_test(ds, w, R.measure(ds, w)) is None            # never the CP path
    assert AT.latest_aet_test(ds, TODAY)["idx"] == w.idx


def test_aet_test_done_by_must_be_this_activity_and_day():
    from backend.engine import workout_review as R
    day = TODAY - dt.timedelta(days=3)
    ds = _ds([_aet_workout(day, title="週三 飄移測試")])
    w = ds.workouts[0]
    for row in (_aet_row(day, idx=5), _aet_row(day, state="active", done_by=None),
                _aet_row(day, done_by={"index": 0, "date": (day - dt.timedelta(days=1)).isoformat()})):
        ds.plan_test_sessions = [row]
        assert R.scheduled_aet_test(ds, w) is None
        assert R.classify(ds, w)["type"] == "test_cp"                    # back to the old rules


def test_aet_test_fallbacks_title_plan_row_then_steady_run():
    from backend.engine import workout_review as R
    day = TODAY - dt.timedelta(days=3)
    ds = _ds([_aet_workout(day)])                                        # 「WKO5 AeT 飄移測試 40 分」, 50′
    assert R.classify(ds, ds.workouts[0])["test_match"] == "title"
    ds = _ds([_aet_workout(day, title="")], plan=_plan(aethr=145.0, day=day.isoformat()))
    c = R.classify(ds, ds.workouts[0])
    assert c["type"] == "test_aet" and c["test_match"] == "threshold"
    # an untitled, unplanned 50′ run is not taken for a test: the steady fallback stays ≥ 55′
    # (ordinary ~45′ road runs must not offer 「套用這次的 AeT」)
    ds = _ds([_aet_workout(day, title="")])
    assert R.classify(ds, ds.workouts[0])["type"] != "test_aet"
    ds = _ds([_aet_workout(day, title="", main=60)])                     # 70′ flat steady run, fair drift
    m = R.measure(ds, ds.workouts[0])
    assert m["drift"]["ok"] and m["moving_s"] >= R.TEST_AET_MIN_S
    c = R.classify(ds, ds.workouts[0], m)
    assert c["type"] == "test_aet" and c["test_match"] == "steady"
    assert R.MATCH_LABEL["steady"].startswith("≥ 55")


def test_a_short_planned_aet_test_is_found_and_refused_with_the_reason():
    day = TODAY - dt.timedelta(days=2)
    ds = _ds([_aet_workout(day, title="", main=25)])                     # 10 + 25 = 35′ on the clock
    assert AT.latest_aet_test(ds, TODAY) is None                         # too short without the plan
    ds.plan_test_sessions = [_aet_row(day)]
    at = AT.latest_aet_test(ds, TODAY)
    assert at is not None and not at["ok"] and "< 40 分" in at["reason"]


def test_the_aet_session_carries_protocol_aet():
    s = AT.session({"cp": 250.0}, 140.0, 190.0)
    assert s["protocol"] == AT.PROTOCOL == "aet" and AT.is_aet_session(s)
    assert AT.is_aet_session({"kind": "aet"}) and AT.is_aet_session({"gen_key": "test_aet"})
    assert not AT.is_aet_session({"kind": "test", "protocol": "quick", "title": "CP 測試 20 分全力"})
    q = P._bq({**s, "kind": "test"})
    assert q["protocol"] == "aet" and q["id"] == "test_aet"              # projection keeps it for the store


def test_aet_test_session_steps_and_payload_without_coros():
    th = {"cp": 250.0, "lthr": 165.0, "aet": 145.0}
    # no weekday cap: the standard 15 + 60 + 5 (the short 50′ one: test_aet_weekday.py)
    s = {**AT.session(th, 140.0, AT.start_power(250.0)), "day": "2026-10-07"}
    assert s["kind"] == "test" and s["id"] == "test_aet" and s["minutes"] == 80
    assert "冷氣房跑步機 2–3%＋電扇（首選），或平路環線" in s["detail"]
    assert "暖身 15 分到開始流汗" in s["detail"] and "測試 60 分固定功率不要調" in s["detail"] and "中途不停" in s["detail"]
    # the heat condition as a temperature (台灣教練; user decision), not 「太陽出來前」
    assert "氣溫 25 °C 以下時開始（熱會讓心率偏高、飄移失真" in s["detail"] and "太陽" not in s["detail"]
    assert "主課第 10 分鐘心率已經比起始高 10 下還在升" in s["detail"] and "evokeendurance.com" in s["source"]
    assert "If you only have 40 minutes" in s["source"]
    steps = CW.session_steps(s, CW.Thresholds.of(th))
    assert [x.kind for x in steps] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_COOLDOWN]
    assert [x.seconds for x in steps] == [900, 3600, 300]
    short = {**AT.session(th, 140.0, AT.start_power(250.0), 50), "day": "2026-10-07"}
    steps_s = CW.session_steps(short, CW.Thresholds.of(th))
    assert [x.kind for x in steps_s] == [CW.EX_WARMUP, CW.EX_TRAIN]        # the cool-down is optional
    assert [x.seconds for x in steps_s] == [600, 2400]
    assert steps[0].intensity[0] == "hr" and steps[0].intensity[2] == 140
    assert steps[1].intensity == ("power", round(187.5 * 0.97), round(187.5 * 1.03)) or \
        steps[1].intensity == ("power", round(188 * 0.97), round(188 * 1.03))
    assert steps[1].name == "固定功率，不要調"
    # the CP test still gets its own steps
    cp = {"id": "test", "kind": "test", "title": "CP 測試 3 分 + 12 分", "minutes": 60, "target": "", "detail": ""}
    assert len(CW.session_steps(cp, CW.Thresholds.of(th))) == 5
    spec = CW.session_workout(s, th, "2026-10-01")                      # payload only, nothing sent
    assert spec.payload["estimatedTime"] == 80 * 60 and "AeT" in spec.name
    assert CW.session_workout(short, th, "2026-10-01").payload["estimatedTime"] == 50 * 60


def test_aet_test_due_only_for_a_reason():
    # B3: no fixed cadence — the test is due when quality_gate.aet_test_reason gives a reason
    why = {"code": "se", "text": "聚合估計還不夠準"}
    assert AT.due(date(2026, 7, 6), "base", "2026-06-29", why, None)
    assert AT.due(date(2026, 7, 13), "base", "2026-06-29", why, None)         # any week, not week 2 / 7 …
    assert not AT.due(date(2026, 7, 6), "base", "2026-06-29", None, None)     # no reason, no test
    assert not AT.due(date(2026, 7, 6), "base", "2026-06-29", "2026-06-20", None)       # the old date arg
    assert not AT.due(date(2026, 7, 6), "base", "2026-06-29", why, "2026-06-25")       # tested < 4 weeks
    assert not AT.due(date(2026, 7, 6), "specific", "2026-06-29", why, None)
    assert AT.start_hr(None, 160.0) == pytest.approx(0.89 * 160 - 5)


def test_aet_test_reasons(valid_aet, monkeypatch):
    from backend.engine import drift_agg as DA
    monkeypatch.setattr(DA, "aet_points", lambda ds, today, days=180, **kw: [{"hr1": 140, "drift": 0.03, "se": 0.04}])
    # valid estimate 150 ± 2, plan AeT 150: nothing to test
    g = _gate(plan=_plan(aethr=150, lthr=165))
    assert g["aet_test_reason"] is None
    # the estimate moved by more than its SE (≈ 3 bpm): confirm with a test
    g = _gate(plan=_plan(aethr=142, lthr=165))
    assert g["aet_test_reason"]["code"] == "moved" and "UA" in g["aet_test_reason"]["text"]
    # a shift in the last 6 points
    monkeypatch.setattr(DA, "aet_validity", lambda ds, today, lthr=None: {
        "valid": False, "value": 150.0, "se": 2.0, "n": 8, "shift_bpm": -6.0, "reason": "最近 6 次一致偏低 6 bpm",
        "points": 8})
    assert _gate(plan=_plan(aethr=150, lthr=165))["aet_test_reason"]["code"] == "shift"


@pytest.mark.parametrize("pref, cap_w, cap_l, want", [
    ("auto", None, None, "xu90"), ("auto", 50, None, "xu90"), ("auto", 50, 80, "ua40"),
    ("ua60", None, None, "ua60"), ("ua60", 50, None, "ua40"), ("evoke60", None, None, "evoke60"),
    ("friel", 50, 60, "friel"), ("xu90", None, 60, "ua40"), ("bogus", None, None, "xu90")])
def test_aet_protocol_resolution(pref, cap_w, cap_l, want):
    # auto = the standard 徐國峰 90 min on the long day; UA 40 when the long-day cap can't fit it
    assert AT.resolve_protocol(pref, cap_w, cap_l) == want


@pytest.mark.parametrize("key", ["xu90", "ua60", "ua40", "evoke60", "friel"])
def test_each_protocol_session_title_steps_and_hover(key):
    th = {"cp": 250.0, "lthr": 165.0, "aet": 145.0}
    s = AT.session(th, 140.0, 190.0, None, key)
    p = AT.PROTOCOLS[key]
    assert s["title"] == p["title"] and AT.protocol_of_title(s["title"]) == key and AT.is_aet_session(s)
    assert s["minutes"] == p["warm"] + p["main"] + p["cool"]
    assert "氣溫 25 °C 以下時開始" in s["detail"]
    steps = CW.session_steps(s, CW.Thresholds.of(th))
    assert steps[0].seconds == p["warm"] * 60 and steps[1].seconds == p["main"] * 60
    if key in ("xu90", "friel"):
        assert steps[1].intensity[0] == "hr"                       # the pace / HR is held, no power range
    else:
        assert steps[1].intensity[0] == "power"
    tip = AT.protocol_tip(key)
    assert f"共 {s['minutes']} 分" in tip and "場地" in tip and "固定" in tip and "判讀" in tip and "來源" in tip
    assert "徐國峰" in AT.protocol_tip("auto") and "UA" in AT.protocol_tip("auto")


def _xu_series(rise, minutes=95, stop_at=None, stop_s=0):
    t = np.arange(minutes * 60 + 1, dtype=float)
    hr = 135.0 + rise * np.clip((t - 600) / 4800, 0, 1)
    v = np.full(len(t), 10.0)
    if stop_at:
        v[stop_at:stop_at + stop_s] = 0.0
    return t, hr, v


def test_xu90_analysis_is_minute_10_vs_minute_90_not_halves():
    t, h, v = _xu_series(rise=135.0 * 0.06)                 # HR 135 → 143.1 at minute 90: 6 %
    r = AT.analyze(t, h, v, None, judge="xu")
    assert r["ok"] and r["band"] == "base_ok" and r["drift"] == pytest.approx(0.06, abs=0.003)
    assert r["hr1"] == pytest.approx(135.0, abs=0.2) and "徐國峰" in " ".join(AT.lines(r))
    bad = AT.analyze(*_xu_series(rise=135.0 * 0.12), None, judge="xu")
    assert bad["ok"] and bad["band"] == "base_not" and "還不夠" in " ".join(AT.lines(bad))
    short = AT.analyze(*_xu_series(rise=5, minutes=80), None, judge="xu")
    assert not short["ok"] and "第 91 分鐘" in short["reason"]
    stop = AT.analyze(*_xu_series(rise=5, stop_at=3000, stop_s=60), None, judge="xu")
    assert not stop["ok"] and "30 秒" in stop["reason"]
    # heat bands: kept in heat; a pass counts, a fail may be the heat
    hot = AT.analyze(*_xu_series(rise=5), None, judge="xu", temp_c=27.0, temp_src="route_weather")
    assert hot["ok"] and hot["band"] == "base_ok" and hot["temp_band"] == "warm" and hot["heat"]
    assert "熱天通過仍算數" in AT.lines(hot)[-1]
    hot_bad = AT.analyze(*_xu_series(rise=135.0 * 0.12), None, judge="xu", temp_c=29.0, temp_src="route_weather")
    assert hot_bad["ok"] and hot_bad["band"] == "base_not" and "可能是熱造成的" in AT.lines(hot_bad)[-1]
    assert AT.band_of(0.04, "evoke") == "at" and AT.band_of(0.06, "evoke") == "above"
    assert AT.band_of(0.04, "friel") == "base_ok" and AT.band_of(0.08, "friel") == "base_mid"


def test_aet_test_protocol_pref_round_trip():
    p = PP.from_body({"aet_test_protocol": "evoke60"})
    assert p.aet_test_protocol == "evoke60" and not p.active
    assert PP.Prefs().aet_test_protocol == "auto" and SR.DEFAULTS["plan.prefs.aet_test_protocol"] == "auto"
    SR.validate("plan.prefs.aet_test_protocol", "xu90")
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.aet_test_protocol", "maf")
    with pytest.raises(ValueError):
        PP.check(PP.Prefs(aet_test_protocol="maf"))


def test_apply_writes_the_test_day_to_a_temp_plan(tmp_path, monkeypatch):
    from backend.api import plan as API
    from backend.engine import planning as PL
    path = tmp_path / "plan.json"
    real_load, real_save = PL.Plan.load.__func__, PL.Plan.save
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, p=None: real_load(cls, path)))
    monkeypatch.setattr(PL.Plan, "save", lambda self, p=None: real_save(self, path))
    monkeypatch.setattr(API, "_notify", lambda thresholds: None)
    before = PL.plan_path().stat().st_mtime if PL.plan_path().exists() else None
    out = API.apply_estimate(API.ApplyEstimate(aethr=146.4, date="2026-09-20",
                                               note="AeT 飄移測試 2026-09-20：Pw:HR 4.2%"))
    assert out["threshold"]["date"] == "2026-09-20" and out["threshold"]["aethr"] == 146
    back = real_load(PL.Plan, path)
    assert [(t.date, t.aethr) for t in back.thresholds] == [("2026-09-20", 146)]
    info = QG.aet_info(back, TODAY)
    assert info["measured"] and info["label"] == "AeT 146（2026-09-20 飄移測試）"
    # auto switches to the gap method once LTHR is measured too — and the AeT is valid (B3)
    back.thresholds.append(Threshold("2026-09-01", lthr=165.0))
    from backend.engine import drift_agg as DA
    monkeypatch.setattr(DA, "aet_validity", lambda ds, today, lthr=None: {
        "valid": True, "value": 146.0, "se": 2.0, "n": 8, "shift_bpm": 0.0, "reason": "", "points": 8})
    g = QG.evaluate(_ds([], back), back, TODAY, PP.Prefs(), GOOD_BY, BASE)
    assert g["resolved"] == "ua_gap+friel_drift" and g["gap"] == pytest.approx(165 / 146 - 1)
    with pytest.raises(Exception):
        API.apply_estimate(API.ApplyEstimate(aethr=146, date="2999-01-01"))
    assert (PL.plan_path().stat().st_mtime if PL.plan_path().exists() else None) == before     # the real plan untouched


def test_ladder_pick_and_the_change_log_follow_the_tracks():
    # SP-31: 推薦第一名 = the next step of the session's own track; the change log notes the Zone 3 gate
    from backend.engine import plan_auto as PA
    from backend.engine import template_recs as TR
    gate = {"state": "none", "guard": {}, "z3": {"open": True}, "z5": {"open": True},
            "dose": {"z3": {"step": 2, "met": 3, "done": 3}, "z5": {"step": 1, "done": 1}}}
    assert QG.rung_now(gate, "z3") == "a3" and QG.rung_now(gate, "z5") == "z5b" and QG.rung_now({}, "z3") is None
    k3, why3 = TR.ladder_pick(None, None, gate=gate, track="z3")
    k5, why5 = TR.ladder_pick(None, None, gate=gate, track="z5")
    assert k3 == "a3a" and "A3" in why3 and k5 == "v2a" and "V2" in why5
    state = {}
    closed = {"cur": {"quality_gate": {"z3": {"open": False, "path": None, "text": "Zone 3：未解鎖（…）"}}}}
    assert PA.state_changes(closed, state) == []                         # the first closed record: nothing new
    opened = {"cur": {"quality_gate": {"z3": {"open": True, "path": "weeks", "text": "Zone 3：已解鎖（連續 4 週規律訓練）"}}}}
    assert PA.state_changes(opened, state) == ["Zone 3：已解鎖（連續 4 週規律訓練）"]
    assert PA.state_changes(opened, state) == []


def test_specific_phase_applies_the_ramp_and_volume_guardrails_to_both_tracks():
    # owner 2026-10-04: no school exempts 專項期 from load-progression caution (Friel ramp 5–8; Nielsen
    # 2014 / Damsted 2019 > 20 %); 減量期 and projected weeks stay exempt (re-checked when they come)
    spec = types.SimpleNamespace(kind="specific", start="2026-09-01", end="2026-12-31")
    both = {"z5": {"open": True, "state": "confirmed"}, "z3_recent": {"done": 2},
            "dose": {"z3": {"step": 3, "met": 3, "done": 3}, "z5": {"step": 1, "done": 1}}}
    ok = {**_gate(phase=spec), **both}
    assert [it["track"] for it in QG.week_decision(ok, "specific", "specific", n=2)["items"]] == ["z3", "z5"]
    ramp8 = {**_gate(phase=spec, by={**GOOD_BY, "fitness": _ind(extra={"ramp_week": LG.block_line(60.0) + 0.2,
                                                                         "ramp_base": 60.0})}), **both}
    d = QG.week_decision(ramp8, "specific", "specific", n=2)
    assert not d["allow"] and not d["items"] and "CTL 60 的 15%" in d["z3_note"] and "本週不排間歇" in d["z3_note"]
    vol = {**_gate(phase=spec, by={**GOOD_BY, "volume": _ind(extra={"step": 0.3, "last_week": 5.0})}), **both}
    d = QG.week_decision(vol, "specific", "specific", n=2)
    assert not d["allow"] and "上週跑步量增 +30%" in d["z3_note"]
    ramp5 = {**_gate(phase=spec, by={**GOOD_BY, "fitness": _ind(extra={"ramp_week": LG.watch_line(60.0) + 0.5,
                                                                         "ramp_base": 60.0})}), **both}
    d = QG.week_decision(ramp5, "specific", "specific", n=2)
    assert d["spec"] is QG.SUB and [it["track"] for it in d["items"]] == ["z3"] and "只排閾值下" in d["note"]
    from backend.engine import overview as O
    q = O.quality_sessions(ramp5, d, "specific", {"cp": 250.0}, {"threshold": "x"}, 6.0, None, [], road=True)
    assert [s["title"] for s in q] == [QG.SUB[1]]
    # 減量期 and a projected week: not blocked by this week's load
    for g in (ramp8, vol):
        assert QG.week_decision(g, "taper", "taper")["allow"]
        assert QG.week_decision(g, "specific", "reentry", n=2)["items"]     # the re-entry block has its own rules
        assert QG.week_decision(g, "specific", "specific", first=False, n=2)["items"]
