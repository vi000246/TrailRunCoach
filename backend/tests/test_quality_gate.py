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


def _gate(mode="auto", plan=None, workouts=(), by=None, phase=BASE, weeks=8, settings=None):
    plan = plan or Plan()
    ds = _ds(list(workouts), plan, settings)
    return QG.evaluate(ds, plan, TODAY, PP.Prefs(quality_gate=mode, quality_gate_weeks=weeks), by or GOOD_BY, phase)


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
    # 徐國峰: Zone 3 first — the ladder's first rung is threshold 3×8, not 5×1′
    assert "第 1 步：閾值 3×8 分" in t["verdict"]
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
    # a locked method only keeps Zone 5 closed: Zone 3 still goes on (徐國峰)
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
    assert not QG.week_decision({**spec, "levels": {"intensity": "bad"}}, "specific", "specific")["allow"]


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
    (dict(low_share=0.68), "block", "低強度只有 68%（< 75%）"),
    (dict(low_share=0.9, power_low_share=0.7), "block", "功率 < 80% CP 只有 70%"),
    (dict(ramp=5.6), "sub", "CTL 每週 +5.6（≥ 5，Friel）：本週只排閾值下"),
    (dict(ramp=7.2), "sub", "≥ 5，Friel"),                 # B2: 7 is no longer a block
    (dict(ramp=8.2), "block", "≥ 8，Friel"),
    (dict(step=0.25), "block", "> 20%"),
    (dict(step=0.15), "hold", "10–20%"),
    (dict(tsb=-25.0), "hold", "TSB −25"),
])
def test_guardrails(kw, flag, words):
    g = QG.guard(aet=142.0, **kw)
    assert g[flag] and words in g["verdict"].replace("-", "−")
    assert QG.guard(low_share=0.8, ramp=3, step=0.05, tsb=-10)["rule"] == ""


def test_guardrails_block_the_week_and_say_so():
    by = {**GOOD_BY, "intensity": _ind("watch", extra={"low_share": 0.68})}
    g = _gate(by=by)
    t = QG.indicator(g)
    assert t["level"] == "watch" and t["verdict"] == "本週不排間歇：低強度只有 68%（< 75%）"
    assert t["action"] == "輕鬆跑壓在 AeT 以下，下週再看" or "bpm 以下" in t["action"]
    assert not QG.week_decision(g, "base", "base")["allow"]
    ramp = _gate(by={**GOOD_BY, "fitness": _ind(extra={"ramp_week": 5.6})})
    assert QG.week_decision(ramp, "base", "base")["spec"] is QG.SUB
    assert QG.indicator(ramp)["action"] == "先穩住量"


def test_dose_ladder_zone3_first_then_zone5_only_when_open():
    # 徐國峰: Zone 3 first; Zone 5 reps ≥ 2 min, only while Zone 5 is open
    open_titles = [QG.dose_spec(i, True)[1] for i in range(9)]
    assert open_titles == ["閾值 3×8 分", "閾值 4×8 分", "閾值 3×10 分", "VO2max 5×2 分", "VO2max 4×3 分",
                           "VO2max 5×3 分", "VO2max 4×4 分", "VO2max 4×4 分", "閾值 3×10 分"]
    assert all(s[3] >= 2 for s in QG.Z5)                                  # reps ≥ 2 min
    shut = [QG.dose_spec(i, False)[1] for i in range(3, 7)]
    assert all(t.startswith("閾值") for t in shut)                        # Zone 3 continues
    assert QG.dose_step([]) == {"done": 0, "faded": False, "step": 0}
    # B4: a session that can't be judged (no bouts / CP) repeats the step — no progress
    three = [{"faded": False}] * 3
    d = QG.dose_step(three)
    assert d["step"] == 0 and d["outcome"] == "unknown"
    g = {"state": "none", "guard": {"hold": True}, "dose": {"done": 2, "step": 2}}
    assert QG.week_decision(g, "base", "base")["spec"][1] == "閾值 4×8 分"    # held: repeat the last step
    rec = QG.week_decision({"state": "none", "guard": {}, "dose": {"step": 4}}, "base", "recovery_week")
    assert rec["spec"] is QG.RECOVERY and not rec["advance"]
    # step 3 (3 Zone 3 達標): Zone 5 only with z5 open; else Zone 3, and the step waits
    g3 = {"state": "none", "guard": {}, "dose": {"done": 3, "step": 3}, "z5": {"open": False, "text": "Zone 5：未確認"}}
    d = QG.week_decision(g3, "base", "base")
    assert d["spec"][1].startswith("閾值") and not d["advance"] and "未確認" in d["note"]
    d = QG.week_decision({**g3, "z5": {"open": True}}, "base", "base")
    assert d["spec"] is QG.Z5[0] and d["advance"]


def test_dose_sessions_parse_for_coros_and_the_cap():
    th = {"cp": 250.0, "lthr": 165.0, "aet": 142.0}
    s = QG.session(QG.Z3[0], th, "沒有 AeT 實測：照 80/20 原則每週 1 次間歇，")
    assert s["title"] == "閾值 3×8 分" and s["minutes"] == 15 + 3 * 10 + 10
    assert s["detail"].startswith("沒有 AeT 實測：照 80/20 原則每週 1 次間歇，") and "休 2 分" in s["detail"]
    steps = CW.session_steps(s, CW.Thresholds.of(th))
    rep = steps[1]
    assert rep.sets == 3 and rep.steps[0].seconds == 480 and rep.steps[1].seconds == 120
    assert rep.steps[0].intensity == ("power", 220, 238)                    # 88–95 % CP
    v = QG.session(QG.Z5[0], th)
    st = CW.session_steps(v, CW.Thresholds.of(th))
    assert st[1].sets == 5 and st[1].steps[0].seconds == 120 and st[1].steps[0].intensity == ("power", 265, 280)
    assert "心率" not in QG.session(QG.Z5[3], th, lthr_default=True)["target"]      # WKO5 default LTHR
    assert QG.hard_need("閾值 3×8 分", 600) == pytest.approx(600)
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
    # judged against the Zone 3 rungs (3×8 @ 88–95 % CP, then 4×8): both sessions' first reps
    # are above 0.98 × 88 % CP → 達標 twice → step 2
    d = QG.dose_step(h)
    assert d["step"] == 2 and [x["outcome"] for x in h] == ["met", "met"] and d["outcome"] == "met"


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


def test_unplanned_hard_runs_are_not_ladder_steps_once_the_plan_is_in_use(monkeypatch):
    # real data 2026-10-01: 43 steady runs at ~95 % CP in 8 weeks, none a planned session, were
    # judged against 3×8′ (13 「目標太高」, 18 未適應) and moved the ladder to step 3
    from backend.engine import plan_store as PS
    ds = _ds([_z3_run(TODAY - dt.timedelta(days=4)), _z3_run(TODAY - dt.timedelta(days=2))])
    monkeypatch.setattr(PS, "plan_in_use", lambda db_path=None: True)
    monkeypatch.setattr(PS, "done_plan", lambda db_path=None: {1: {"title": QG.Z3[0][1], "state": "done"}})
    h = QG.dose_history(ds, TODAY)
    assert [x.get("unplanned", False) for x in h] == [True, False]
    d = QG.dose_step(h)
    assert h[0]["outcome"] == "neutral" and h[1]["outcome"] == "met" and d["step"] == 1 and d["done"] == 1


def test_planned_zone3_reps_come_from_the_coros_laps(monkeypatch):
    # interval-prescription.md §B3: a pushed workout has one lap per step — the laps whose
    # duration is the planned rep (±5 s) are the reps; power only when there are no laps
    from backend.engine import interval_reps as IR
    from backend.engine import plan_store as PS
    day = TODAY - dt.timedelta(days=4)
    w = _z3_run(day)
    ds = _ds([w])
    monkeypatch.setattr(PS, "done_plan", lambda db_path=None: {0: {"title": QG.Z3[0][1], "state": "done"}})
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
    # Zone 3 maintenance while Zone 5 is paused (step ≥ 3): neutral, the Z5 step waits
    assert QG.planned_spec(QG.Z3[2][1], 4) == (QG.Z3[2], True)
    assert QG.planned_spec(QG.Z5[1][1], 4) == (QG.Z5[1], False)
    # the old ladder's titles don't count any more
    assert QG.planned_spec("短間歇 5×1 分", 0)[1] is True
    d = QG.dose_step([{"bouts": _b(150, 250, 250, 250, 250), "cp": cp}])
    assert d["step"] == 0 and d["adjust"] == {"power": QG.TARGET_DOWN}
    spec = QG.adjusted_spec(QG.Z3[0], {"power": 0.95})
    assert spec[5] == round(0.88 * 0.95, 3) and spec[1] == QG.Z3[0][1]
    g = {"state": "none", "guard": {}, "dose": {"done": 2, "step": 1, "adjust": {"rest_add": 1}}}
    sp = QG.week_decision(g, "base", "base")["spec"]
    assert sp[4] == QG.Z3[1][4] + 1
    s = QG.session(sp, {"cp": cp})
    assert f"休 {sp[4]} 分鐘" in s["detail"]


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
    cur["quality_gate"] = {"state": "locked", "mode": "weeks", "resolved": "weeks", "verdict": "基礎期第 7 週 / 8 週",
                           "base_start": "2026-08-17", "weeks_need": 8, "levels": {"intensity": "good"},
                           "guard": {}, "dose": {"step": 3, "done": 3, "faded": False}, "aet": {"date": "2026-09-01"},
                           "z5": {"open": False}}
    phases = [{"kind": "base", "start": "2026-08-17", "end": "2026-12-31"}]
    weeks = P.project_weeks(cur, phases, date(2026, 11, 22))
    first_q = {w["start"]: [s["title"] for s in w["sessions"] if s["kind"] == "quality"] for w in weeks}
    # Zone 3 keeps going while the weeks method keeps Zone 5 closed (base week 8, 10/5);
    # from week 9 (10/12) Zone 5 opens and the ladder moves on to 5×2′, 4×3′
    pre = [v[0] for k, v in sorted(first_q.items()) if v and k < "2026-10-12"
           and next(w for w in weeks if w["start"] == k)["mode"] != "recovery_week"]
    assert all(x.startswith("閾值") for x in pre)
    built = [v[0] for k, v in sorted(first_q.items()) if v and k >= "2026-10-12"
             and next(w for w in weeks if w["start"] == k)["mode"] != "recovery_week"]
    assert built[:2] == ["VO2max 5×2 分", "VO2max 4×3 分"]


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


def test_aet_analysis_refuses_short_hot_fast_finish_and_hills():
    t, h, s, p, _ = _aet_series(12.9, main=35)
    assert "< 40 分" in AT.analyze(t, h, s, p)["reason"]
    t, h, s, p, _ = _aet_series(12.9, main=39)
    assert "暖身後只有 39 分鐘" in AT.analyze(t, h, s, p)["reason"]
    t, h, s, p, tp = _aet_series(12.9, temp=28.0)
    assert "25 °C" in AT.analyze(t, h, s, p, tp)["reason"]
    t, h, s, p, _ = _aet_series(12.9, finish=1.12)
    assert "快速結尾" in AT.analyze(t, h, s, p)["reason"]
    t, h, s, p, _ = _aet_series(12.9)
    assert "有坡" in AT.analyze(t, h, s, p, climb_m_per_km=30.0)["reason"]


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
    assert body["note"].startswith(f"AeT 飄移測試 {day.isoformat()}：Pw:HR ")
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
    # (the athlete's ordinary 41–52′ road runs must not offer 「套用這次的 AeT」)
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
    # the heat condition as a temperature (徐國峰; user decision), not 「太陽出來前」
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
    monkeypatch.setattr(DA, "aet_points", lambda ds, today, days=180: [{"hr1": 140, "drift": 0.03, "se": 0.04}])
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
    hot = AT.analyze(*_xu_series(rise=5), None, judge="xu", temp_c=27.0, temp_src="watch")
    assert not hot["ok"] and "25 °C" in hot["reason"]
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
    before = PL.PLAN_PATH.stat().st_mtime if PL.PLAN_PATH.exists() else None
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
    assert (PL.PLAN_PATH.stat().st_mtime if PL.PLAN_PATH.exists() else None) == before     # the real plan untouched
