"""The aerobic-base checks (engine/base_check.py), the Zone 5 lifecycle and the
re-entry block after a break (engine/reentry.py; docs/research/detraining.md). Synthetic data only — never the WKO5 folder, the app DB,
the user's plan or COROS."""
import datetime as dt
from datetime import date

import numpy as np
import pytest

from backend.engine import base_check as BC
from backend.engine import overview as O
from backend.engine import plan_auto as PA
from backend.engine import plan_prefs as PP
from backend.engine import projection as P
from backend.engine import quality_gate as QG
from backend.engine import reentry as RE
from backend.engine.planning import Threshold
from backend.engine.status import Status
from backend.tests.test_plan_store import PHASES, cur_plan
from backend.tests.test_quality_gate import TODAY, _ds, _plan, _run
from backend.tests.wko5_fakes import FakeWorkout


def bo(start, end, label="出國"):
    from backend.engine import blackouts as BL
    return BL.from_list([{"id": f"b{start}", "start": start, "end": end, "label": label}])[0]


# ---------------------------------------------------------------------------
# the re-entry block by break length (Daniels table 9.2)
# ---------------------------------------------------------------------------

def _plan_for(days, cross=False):
    last = date(2026, 9, 1)
    return RE.plan(last, last + dt.timedelta(days=days + 1), cross, prev_hours=5.0, prev_long_min=120.0)


def test_breaks_up_to_5_days_need_no_block():
    for n in range(1, 6):
        assert _plan_for(n) is None                              # back to 100 %, no make-up


@pytest.mark.parametrize("days, cat, segs, z3, drift, reconfirm", [
    (6, "6-13", [0.50, 0.75], 1, False, False),
    (13, "6-13", [0.50, 0.75], 1, False, False),
    (14, "14-28", [0.50, 0.75], 2, True, False),
    (28, "14-28", [0.50, 0.75], 2, True, False),
    (29, "29-56", [0.33, 0.50, 0.75], 2, False, True),
    (56, "29-56", [0.33, 0.50, 0.75], 2, False, True),
    (70, "long", [0.33, 0.50, 0.70, 0.85, 1.00], 2, False, True),
])
def test_block_by_break_length(days, cat, segs, z3, drift, reconfirm):
    p = _plan_for(days)
    assert p["category"] == cat and [s[2] for s in p["segments"]] == segs
    assert p["z3_before_z5"] == z3 and p["drift_check"] is drift and p["reconfirm"] is reconfirm
    ret, end = date.fromisoformat(p["return"]), date.fromisoformat(p["end"])
    if cat != "long":
        assert (end - ret).days == days                          # the block lasts as long as the break
        assert p["quality_from"] == p["end"]                     # no Z3 / Z5 inside it
    else:
        assert (end - ret).days == 15 * 7 and p["restart_base"] and p["cp_retest"]
        assert (date.fromisoformat(p["quality_from"]) - ret).days == 12 * 7      # Z3 from week 13
    assert p["aet_stale"] is (days >= 29)


def test_fvdot_table_interpolation_and_the_42_day_typo():
    assert RE.fvdot(5) == 1.0 and RE.fvdot(14) == pytest.approx(0.973) and RE.fvdot(28) == pytest.approx(0.931)
    assert RE.fvdot(14, cross=True) == pytest.approx(0.986)
    assert RE.fvdot(42, cross=True) == pytest.approx(0.944)      # the page's 0.994 is a typo (未驗證)
    assert RE.fvdot(21) == pytest.approx(0.952) and 0.952 > RE.fvdot(24) > 0.931
    assert RE.fvdot(100) == pytest.approx(0.800)
    assert _plan_for(20)["fvdot"] == pytest.approx(RE.fvdot(20), abs=1e-4)


def test_week_factor_counts_break_days_zero_and_after_the_block_one():
    last = date(2026, 9, 17)                                     # Thu; back Mon 9/28 after 10 days
    p = RE.plan(last, date(2026, 9, 28), prev_hours=5.0)
    assert p["days"] == 10
    # week of 9/28: 5 days at 50 %, 2 at 75 %
    assert RE.week_factor(p, date(2026, 9, 28)) == pytest.approx((5 * 0.5 + 2 * 0.75) / 7)
    # week of 10/5: 3 days at 75 % then 100 %
    assert RE.week_factor(p, date(2026, 10, 5)) == pytest.approx((3 * 0.75 + 4 * 1.0) / 7)
    assert RE.week_factor(p, date(2026, 10, 12)) is None
    assert not RE.quality_ok(p, date(2026, 10, 4)) and RE.quality_ok(p, date(2026, 10, 8))


# ---------------------------------------------------------------------------
# detecting breaks: unplanned (from the runs) and planned (不排課日期)
# ---------------------------------------------------------------------------

def _daily_except(skip_days, n=60, minutes=52, **kw):
    ws = []
    for d in range(1, n):
        if d in skip_days:
            continue
        w = _run(TODAY - dt.timedelta(days=d), minutes=minutes, power=180.0, **kw)
        w.metrics["tss"] = 40.0
        ws.append(w)
    return ws


def test_unplanned_break_from_the_runs_and_the_current_gap():
    ds = _ds(_daily_except(set(range(2, 12))))                  # ran yesterday after 10 days off
    p = RE.find(ds, TODAY)
    assert p["days"] == 10 and not p["planned"] and p["return"] == (TODAY - dt.timedelta(days=1)).isoformat()
    assert p["prev_hours"] == pytest.approx(52 * 7 / 60, rel=0.05)
    # still off: the gap counts as a break returning today
    ds = _ds(_daily_except(set(range(1, 9))))
    p = RE.find(ds, TODAY)
    assert p["ongoing"] and p["return"] == TODAY.isoformat() and p["days"] == 8      # 9/22–9/29 off
    # 4 days off: nothing
    assert RE.find(_ds(_daily_except({2, 3, 4, 5})), TODAY) is None


def test_planned_blackout_ahead_and_one_with_runs_inside():
    ds = _ds(_daily_except(set()))
    p = RE.find(ds, TODAY, [bo("2026-10-05", "2026-10-14")])
    assert p["planned"] and p["return"] == "2026-10-15" and p["days"] == 10
    # a blackout the athlete ran inside (past) is not a break
    assert RE.find(ds, TODAY, [bo("2026-09-10", "2026-09-20")]) is None
    # ahead of the projected weeks
    ps = RE.planned_ahead([bo("2026-10-19", "2026-10-30")], date(2026, 10, 4), 5.0, 120.0)
    assert len(ps) == 1 and ps[0]["days"] == 12 and ps[0]["return"] == "2026-10-31"


# ---------------------------------------------------------------------------
# the generator: week_plan and project_weeks
# ---------------------------------------------------------------------------

def _status_plan(ds):
    plan = _plan(lthr=165, day="2026-09-01")
    plan.thresholds.append(Threshold("2026-09-20", cp=250.0))
    ds.plan = plan
    return plan


def test_week_plan_after_an_unplanned_break_is_a_reentry_block_without_quality():
    ds = _ds(_daily_except(set(range(2, 12))))                  # 10 days off, back yesterday
    plan = _status_plan(ds)
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    assert wp["mode"] == "reentry" and wp["mode_label"] == "停訓後恢復期"
    assert wp["reentry"]["days"] == 10
    f = RE.week_factor(wp["reentry"], date(2026, 9, 28))
    assert wp["target"]["hours"] == pytest.approx(wp["reentry"]["prev_hours"] * f)
    assert not any(s["kind"] in ("quality", "test") for s in wp["sessions"])     # E days only
    assert not any("衝刺" in s["title"] for s in wp["sessions"])
    assert any(n.get("src") == "reentry" for n in wp["notes"])
    g = wp["quality_gate"]
    assert g["z5"]["state"] == "reentry" and not QG.week_decision(g, "base", "base")["allow"]


def test_projection_schedules_the_block_after_a_planned_blackout_not_step_cap():
    bos = [bo("2026-10-05", "2026-10-14")]                     # 10 days: Daniels cat. 2
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 11, 1), blackouts=bos)
    by = {w["start"]: w for w in weeks}
    w12 = by["2026-10-12"]
    assert w12["mode"] == "reentry"                            # 10/15–10/19 50 %, 10/20–24 75 %
    assert not any(s["kind"] in ("quality", "test") for s in w12["sessions"])
    w19 = by["2026-10-19"]
    assert w19["mode"] == "reentry" and not any(s["kind"] in ("quality", "test") for s in w19["sessions"])
    # the old step_cap after a fully blocked week (0.5 h) is gone: the block starts at ~50 % of before
    assert w19["hours"] > 1.0


# ---------------------------------------------------------------------------
# 徐國峰's 90-min test
# ---------------------------------------------------------------------------

def _xu_workout(day, rise=0.06, minutes=95, tags=("running",), climb=10.0):
    t = np.arange(minutes * 60 + 1, dtype=float)
    hr = 128.0 * (1 + rise * np.clip((t - 600) / 4800, 0, 1))
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": [10.0] * len(t), "power": [190.0] * len(t),
          "elapseddistance": list(t * 10 / 3600)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(6)), sport="run", tags=list(tags), sport_type="running",
                       channels=ch, metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                             "distance": len(t) / 360.0, "climbing": climb})


def test_xu_run_minute_10_vs_90_and_its_conditions():
    ds = _ds([_xu_workout(TODAY - dt.timedelta(days=3))])
    r = BC.xu_run(ds, ds.workouts[0])
    assert r["ok"] and r["drift"] == pytest.approx(0.06, abs=0.003) and r["hr10"] == pytest.approx(128, abs=0.5)
    assert "< 10%" in BC.xu_text(r)
    bad = _ds([_xu_workout(TODAY - dt.timedelta(days=3), rise=0.12)])
    rb = BC.xu_run(bad, bad.workouts[0])
    assert not rb["ok"] and any("≥ 10%" in x for x in rb["why"])
    trail = _ds([_xu_workout(TODAY - dt.timedelta(days=3), tags=("running", "runningtrail"))])
    assert not BC.xu_run(trail, trail.workouts[0])["ok"]
    short = _ds([_xu_workout(TODAY - dt.timedelta(days=3), minutes=80)])
    assert BC.xu_run(short, short.workouts[0]) is None                # not a 90-min run


def test_rq_points_are_daniels_intensity_points_and_the_tss_conversion():
    assert BC.rq_points(210 * 60) == pytest.approx(42.0)             # 210 min × 0.2
    assert BC.rq_points(150 * 60) == pytest.approx(30.0)
    assert BC.tss_of_points(30) == pytest.approx(150 / 60 * 0.7 ** 2 * 100)   # ≈ 122 (推估 IF 0.70)


def test_three_signals_are_gone_only_the_measured_aet_tests_remain():
    # SP-39: Zone 5 opens on a measured AeT only (UA gap or Friel); the 90-min test and the
    # plateau / weeks methods open Zone 3 only; the old xu_signals path is gone
    assert not hasattr(BC, "three_signals") and not hasattr(BC, "signal2")
    for m in ("auto", "xu_drift", "plateau", "weeks"):
        assert BC._paths_for(m) == ("aet_ua_gap", "aet_friel_drift")
    assert BC._paths_for("ua_gap") == ("aet_ua_gap",) and BC._paths_for("none") == ()
    assert BC._paths_for("xu_signals") == ()
    assert "xu_signals" not in BC.PATH_LABEL


def test_long_run_late_vs_early():
    t = np.arange(0, 80 * 60 + 1, dtype=float)
    ok = BC.long_late_vs_early(t, np.full(len(t), 135.0), np.full(len(t), 10.0))
    assert ok["hr_rise"] == pytest.approx(0.0) and ok["pace_drop"] == pytest.approx(0.0)
    hr = 130.0 + 15.0 * t / t[-1]
    up = BC.long_late_vs_early(t, hr, np.full(len(t), 10.0))
    assert up["hr_rise"] > BC.LONG_HR_RISE


# ---------------------------------------------------------------------------
# the Zone 5 lifecycle
# ---------------------------------------------------------------------------

AP = {"aet_ua_gap": "2026-08-01"}           # a measured AeT + LTHR within 10 % on 2026-08-01


@pytest.fixture
def xu_confirmed(monkeypatch):
    """A passing 徐國峰 run on 2026-08-01 (not a Zone 5 path any more); maintenance ok."""
    monkeypatch.setattr(BC, "xu_runs", lambda ds, today, days=182: [
        {"idx": 0, "date": "2026-08-01", "ok": True, "drift": 0.06, "hr10": 128.0, "hr90": 135.7, "why": []}])
    monkeypatch.setattr(BC, "maintenance", lambda ds, today, since, brk=None: {"ok": True, "why": ""})
    monkeypatch.setattr(BC, "long_check", lambda ds, today, days=28: {"state": "ok", "why": "穩"})


def test_z5_confirmed_by_a_measured_aet_not_by_the_90_min_test(xu_confirmed):
    # SP-39: the 90-min pass alone doesn't confirm Zone 5
    z = BC.z5_status(_ds([]), TODAY, "auto")
    assert z["state"] == "unconfirmed" and not z["open"] and "實測 AeT" in z["reason"]
    assert BC.z5_status(_ds([]), TODAY, "xu_drift")["state"] == "unconfirmed"
    assert BC.z5_status(_ds([]), TODAY, "weeks", "unlocked")["state"] == "unconfirmed"
    z = BC.z5_status(_ds([]), TODAY, "auto", aet_paths=AP)
    assert z["state"] == "confirmed" and z["open"] and z["path"] == "aet_ua_gap" and z["since"] == "2026-08-01"
    assert "已確認" in z["text"]
    # forced modes only take their own path; none = no gate
    assert BC.z5_status(_ds([]), TODAY, "friel_drift", aet_paths=AP)["state"] == "unconfirmed"
    assert BC.z5_status(_ds([]), TODAY, "none")["open"]


def test_z5_pauses_on_a_maintenance_failure(xu_confirmed, monkeypatch):
    monkeypatch.setattr(BC, "maintenance", lambda ds, today, since, brk=None: {
        "ok": False, "why": "連續 3 週 1 區時間 < 確認時的 2/3"})
    z = BC.z5_status(_ds([]), TODAY, "auto", aet_paths=AP)
    assert z["state"] == "paused" and not z["open"] and "2/3" in z["reason"]
    # Zone 3 continues while Zone 5 is paused
    gate = {"state": "none", "guard": {}, "dose": {"done": 3, "step": 4}, "z5": z}
    d = QG.week_decision(gate, "base", "base")
    assert d["allow"] and d["spec"][1].startswith("閾值")


def test_z1_rule_is_two_thirds_for_three_weeks(monkeypatch):
    lvl = 200 * 60
    rows = [{"monday": (date(2026, 7, 6) + dt.timedelta(weeks=i)).isoformat(), "z1_s": lvl, "run_s": lvl,
             "complete": True} for i in range(4)]
    since = date(2026, 7, 27)
    low = [{"monday": (date(2026, 8, 3) + dt.timedelta(weeks=i)).isoformat(), "z1_s": 120 * 60, "run_s": 120 * 60,
            "complete": True} for i in range(3)]
    # a failing long run no longer pauses Zone 5: only the weekly Z1 rule does
    monkeypatch.setattr(BC, "long_check", lambda ds, today, days=28: {"state": "fail", "why": "後段心率 +8%",
                                                                      "date": "2026-08-15"})
    monkeypatch.setattr(BC, "weekly", lambda ds, today, n: rows + low[:2])
    assert BC.maintenance(_ds([]), date(2026, 8, 20), since)["ok"]          # 2 weeks low: not yet
    monkeypatch.setattr(BC, "weekly", lambda ds, today, n: rows + low)
    m = BC.maintenance(_ds([]), date(2026, 8, 27), since)
    assert not m["ok"] and "Hickson" in m["why"]
    # 140 min ≥ 2/3 × 200 = 133: fine
    ok = [{**r, "z1_s": 140 * 60} for r in low]
    monkeypatch.setattr(BC, "weekly", lambda ds, today, n: rows + ok)
    assert BC.maintenance(_ds([]), date(2026, 8, 27), since)["ok"]


def _brk(days, ret):
    last = date.fromisoformat(ret) - dt.timedelta(days=days + 1)
    return RE.plan(last, date.fromisoformat(ret), prev_hours=5.0)


def test_inside_the_block_no_z3_no_z5(xu_confirmed):
    brk = _brk(10, (TODAY - dt.timedelta(days=2)).isoformat())
    z = BC.z5_status(_ds([]), TODAY, "auto", brk=brk)
    assert z["state"] == "reentry" and not z["open"] and "Daniels" in z["reason"]


def test_after_a_short_break_zone3_first_then_zone5(xu_confirmed):
    brk = _brk(10, "2026-09-05")                                  # block 9/5–9/14
    z = BC.z5_status(_ds([]), TODAY, "auto", None, AP, brk, [])
    assert z["state"] == "paused" and "先完成 1 堂 3 區" in z["reason"]
    z = BC.z5_status(_ds([]), TODAY, "auto", None, AP, brk, ["2026-09-20"])
    assert z["state"] == "confirmed" and z["open"]               # the pre-break confirmation still counts


def test_after_14_to_28_days_two_z3_and_the_drift_check(xu_confirmed, monkeypatch):
    brk = _brk(20, "2026-09-01")                                  # block 9/1–9/20
    z = BC.z5_status(_ds([]), TODAY, "auto", None, AP, brk, ["2026-09-22"])
    assert z["state"] == "paused" and "2 堂" in z["reason"]
    assert z["pause"] == {"kind": "reentry_z3", "done": 1, "need": 2}
    # the post-break long-run drift check (a re-entry rule, kept when xu_signals went)
    monkeypatch.setattr(BC, "long_check", lambda ds, today, days=28: {"state": "fail", "why": "後段心率 +8%"})
    z = BC.z5_status(_ds([]), TODAY, "auto", None, AP, brk, ["2026-09-22", "2026-09-26"])
    assert z["state"] == "paused" and "飄移檢查" in z["reason"] and z["pause"]["kind"] == "drift_check"


def test_a_long_break_invalidates_the_earlier_confirmation(xu_confirmed):
    # conflict fix: the 8-week look-back kept a pre-break pass; ≥ 29 days → only confirmations after it
    brk = _brk(30, "2026-08-25")                                  # block 8/25–9/24
    z = BC.z5_status(_ds([]), TODAY, "auto", None, AP, brk, ["2026-09-26", "2026-09-28"])
    assert z["state"] == "unconfirmed" and "重新做 AeT 測試" in z["reason"] and "Mujika" in z["text"]


def test_a_long_break_makes_the_aet_stale_and_asks_for_a_test(xu_confirmed, monkeypatch):
    from backend.engine import drift_agg as DA
    monkeypatch.setattr(DA, "aet_validity", lambda ds, today, lthr=None: {
        "valid": True, "value": 150.0, "se": 2.0, "n": 8, "shift_bpm": 0.0, "reason": "", "points": 8})
    brk = _brk(30, "2026-08-25")
    monkeypatch.setattr(RE, "find", lambda ds, today, blackouts=(), horizon_days=182: brk)
    g = QG.evaluate(_ds([], _plan(aethr=150, lthr=165, day="2026-08-01")), _plan(aethr=150, lthr=165, day="2026-08-01"),
                    TODAY, PP.Prefs(), None, None)
    assert not g["aet"]["valid"] and g["aet_test_reason"]["code"] == "break"
    assert "Uphill Athlete" in g["aet_test_reason"]["text"] and g["resolved"] == "none"


# ---------------------------------------------------------------------------
# faster at the same HR, the change log
# ---------------------------------------------------------------------------

def test_easy_targets_follow_the_recent_ef():
    ws = [_run(TODAY - dt.timedelta(days=d), minutes=52, hr=135.0, kmh=10.0, power=200.0) for d in (2, 5, 8, 11)]
    ds = _ds(ws)
    et = BC.easy_targets(ds, TODAY, 142.4)
    assert et["n"] == 4 and et["power"] == pytest.approx(200 / 135 * 142.4, rel=0.02)
    assert et["pace_s_km"] == pytest.approx(3600 / (10.0 / 135 * 142.4), rel=0.03) and "推估" in et["text"]
    assert BC.easy_targets(_ds(ws[:2]), TODAY, 142.4) is None


def test_plan_auto_logs_z5_state_and_reentry_changes():
    brk = _brk(10, "2026-09-25")
    inp = {"cur": {"quality_gate": {"z5": {"state": "reentry", "since": None, "path": None,
                                           "text": "Zone 5：恢復期"}}, "reentry": brk}}
    state: dict = {}
    lines = PA.state_changes(inp, state)
    assert any(x.startswith("Zone 5：— → Zone 5：恢復期") for x in lines)
    assert any(x.startswith("恢復期：停跑 10 天") and "活動資料" in x for x in lines)
    assert PA.state_changes(inp, state) == []                        # unchanged: nothing logged
    inp["cur"]["quality_gate"]["z5"] = {"state": "confirmed", "since": "2026-09-30", "path": "xu90",
                                        "text": "Zone 5：已確認（2026-09-30，徐國峰 90 分鐘飄移）"}
    assert PA.state_changes(inp, state) == ["Zone 5：恢復期 → Zone 5：已確認（2026-09-30，徐國峰 90 分鐘飄移）"]
    # the block's planned step-ups are not a "big" change
    weeks = PA.reentry_weeks({"cur": {"reentry": brk, "mode": "reentry", "week": {"start": "2026-09-21"}},
                              "weeks": [{"start": "2026-09-28", "mode": "reentry"}]})
    assert {"2026-09-21", "2026-09-28"} <= weeks
