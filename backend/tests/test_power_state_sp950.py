"""SP-950 (owner 2026-10-27): 功率狀態 — one site-wide rule (engine/power_state.py). Power is in effect only
with a power meter (Stryd; watch power is no power source) AND a fresh CP (a CP test or a race in 42 days):
① power, ② a meter but a stale CP (HR / pace; a CP test keeps being suggested with 「CP 已 N 天沒測…做一次
CP 測試就切回功率」), ③ no meter (no power anywhere in the plan, no CP test, the AeT test no longer held back).
Synthetic data only."""
from __future__ import annotations

import copy
import datetime as dt

import pytest

from backend.engine import athlete_profile as AP
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import power_source as PS
from backend.engine import power_state as PST
from backend.engine import target_policy as TP

D = dt.date(2026, 10, 27)


def _st(source, last_test=None, last_race=None, **kw):
    return PST.state(D, None, source=source, how="設定", last_cp_test=last_test, last_race=last_race, **kw)


# ---- 1. the state ------------------------------------------------------------------------------

def test_three_states_days_and_the_stale_day():
    one = _st("stryd", "2026-10-17", cp=250.0)
    assert one["state"] == PST.POWER and one["code"] == 1 and one["power"] and one["meter"] and one["fresh"]
    assert one["days"] == 10 and one["stale_on"] == "2026-11-29" and one["suggest"] == "cp"   # 10/17 + 43
    two = _st("stryd", "2026-09-07", cp=250.0)
    assert two["state"] == PST.STALE and two["code"] == 2 and not two["power"] and two["meter"]
    assert two["days"] == 50 and two["stale_on"] == "2026-10-20" and two["cp"] == 250.0
    assert two["reason"] == "CP 已 50 天沒測，目前用心率／配速；做一次 CP 測試就切回功率"
    assert two["suggest"] == "cp"
    # a race counts as the all-out effort too (base_check.cp_stale)
    assert _st("stryd", "2026-08-01", "2026-10-20")["state"] == PST.POWER
    # never tested: ② with its own words
    never = _st("stryd")
    assert never["state"] == PST.STALE and never["days"] is None and "做一次 CP 測試就切回功率" in never["reason"]
    three = _st("none", "2026-10-20")
    assert three["state"] == PST.NONE and three["code"] == 3 and not three["power"] and not three["meter"]
    assert three["suggest"] is None and "沒有功率計" in three["reason"]
    # the 42-day edge: day 42 fresh, day 43 stale
    assert _st("stryd", D - dt.timedelta(days=42))["state"] == PST.POWER
    assert _st("stryd", D - dt.timedelta(days=43))["state"] == PST.STALE


def test_watch_power_is_no_power_source_read_time_only():
    # owner 2026-10-27: a stored 手錶推估功率 / a legacy watch meter reads as 沒有功率計; stored values untouched
    prof = {"power_source": "watch"}
    assert AP.profile_power_source(prof) == "none" and prof == {"power_source": "watch"}
    assert PST.equipment(prof)[0] == "none"
    for m in ("coros", "garmin", "other"):
        assert PST.equipment({"power_meter": m})[0] == "none"
    assert PST.equipment({"power_source": "stryd"}) == ("stryd", "設定")
    assert PST.equipment({}) == (None, "預設") and PST.has_meter(None)     # unknown: as before (a meter)
    assert PST.state(D, prof, last_cp_test="2026-10-20")["state"] == PST.NONE
    # 進階 「採用手錶推估功率」 is ignored
    assert PST.accept_watch({"power_source": "stryd"}, True) is False


def test_the_stored_accept_watch_power_is_ignored(monkeypatch):
    monkeypatch.setattr(PS, "read_setting", lambda default=False: True)
    assert PS.effective_accept() is False


def test_fresh_on_a_past_day():
    tests, races = ["2026-06-01"], ["2026-07-20"]
    assert PST.fresh_on("2026-06-20", tests, races)                  # 19 days after a test
    assert PST.fresh_on("2026-08-31", tests, races)                  # 42 days after the race: still fresh
    assert not PST.fresh_on("2026-09-01", tests, races)              # 43 days: stale
    assert not PST.fresh_on("2026-05-01", tests, races)              # nothing before it
    assert PST.fresh_on("2026-09-15", tests + ["2026-09-15"], races)  # the test day itself
    assert not PST.fresh_on("2026-10-27", tests + ["2026-09-01"], races)   # 56 days after the last test
    # only what happened on or before the day counts (a later test does not make an old run fresh)
    assert not PST.fresh_on("2026-08-30", ["2026-09-01"], [])


def test_plan_days():
    from backend.engine.planning import Event, Plan, Threshold
    plan = Plan(thresholds=[Threshold("2026-09-01", cp=250.0), Threshold("2026-09-10", lthr=165.0)],
                events=[Event(id="r", name="10K", date="2026-07-20", kind="road"),
                        Event(id="b", name="百岳", date="2026-08-01", kind="baiyue")])
    assert PST.plan_days(plan) == (["2026-09-01"], ["2026-07-20"])


# ---- 2. target_policy: ② / ③ run nothing by power ------------------------------------------------

TH = {"cp": 260.0, "lthr": 165.0, "aet": 140.0}
TYPES = (({"kind": "easy", "title": "輕鬆跑"}, "power"), ({"kind": "long", "title": "長跑"}, "power"),
         ({"kind": "quality", "title": "閾值 3×10 分"}, "power"), ({"kind": "quality", "title": "上坡 6×2 分"}, "power"),
         ({"kind": "easy", "title": "越野輕鬆跑", "terrain": "trail"}, "hr"), ({"kind": "hike", "title": "越野跑"}, "hr"),
         ({"kind": "long", "title": "長爬坡"}, "none"), ({"kind": "easy", "title": "下坡練習"}, "none"))


@pytest.mark.parametrize("state", [PST.STALE, PST.NONE])
def test_no_session_is_run_by_power_without_power_in_effect(state):
    ps = _st("stryd", "2026-09-07") if state == PST.STALE else _st("none")
    th = {**TH, "power_state": ps}
    for s, one in TYPES:
        assert TP.target_policy(s, None, {**TH, "power_state": _st("stryd", "2026-10-20")})["basis"] == one
        p = TP.target_policy(s, None, th)
        want = "hr" if one == "power" else one              # road easy / long → HR ≤ the easy cap, intervals HR
        assert p["basis"] == want and p["power_state"] == state, s["title"]
        if one == "power":
            assert p["fallback"] == PST.short_reason(ps)
    if state == PST.STALE:
        assert "CP 已 50 天沒測" in TP.target_policy({"kind": "easy", "title": "輕鬆跑"}, None, th)["why"]


def test_a_chosen_power_basis_is_ignored_without_power_and_comes_back():
    q = {"kind": "quality", "title": "閾值 3×10 分", "target_basis": "power"}
    none = {**TH, "power_state": _st("none")}
    p = TP.target_policy(q, None, none)
    assert p["basis"] == "hr" and p["chosen"] == "power" and "沒有功率計" in p["why"]
    assert TP.target_policy({"kind": "easy", "title": "輕鬆跑"}, PP.Prefs(target_basis="power"), none)["basis"] == "hr"
    # the stored choice is kept: with power in effect again it is power
    assert TP.target_policy(q, None, {**TH, "power_state": _st("stryd", "2026-10-20")})["basis"] == "power"
    assert q["target_basis"] == "power"


def test_the_cp_test_keeps_its_bouts_in_stale_but_not_without_a_meter():
    cp_t = {"kind": "test", "title": "CP 測試 20 分全力", "protocol": "quick"}
    stale = {"lthr": 165.0, "aet": 140.0, "cp": None, "power_state": _st("stryd", "2026-09-07")}
    assert TP.target_policy(cp_t, None, stale)["basis"] == "power"      # the test that brings power back
    assert TP.target_policy(cp_t, None, {**stale, "power_state": _st("none")})["basis"] == "hr"


def test_without_a_power_state_the_profile_and_the_stale_flag_decide(monkeypatch):
    monkeypatch.setattr(TP, "_auto_power_ok", lambda: True)
    e = {"kind": "easy", "title": "輕鬆跑"}
    assert TP.target_policy(e, None, TH)["basis"] == "power"
    assert TP.target_policy(e, None, TH, cp_stale=True)["basis"] == "hr"   # SP-950: road easy too
    monkeypatch.setattr(TP, "_auto_power_ok", lambda: False)             # 一般設定: 沒有功率計
    assert TP.target_policy(e, None, TH)["basis"] == "hr"


def test_push_dict_reads_the_power_state():
    from backend.engine import plan_store as PSt
    s = {"uid": "u", "week_start": "2026-10-26", "kind": "easy", "title": "輕鬆跑", "minutes": 45,
         "target": "功率 160–200 W · 心率 ≤ 140 bpm", "state": "active", "day": "2026-10-28"}
    assert PSt.push_dict(s, None, {"power_state": _st("stryd", "2026-10-20")})["basis"] == "power"
    assert PSt.push_dict(s, None, {"power_state": _st("stryd", "2026-09-07")})["basis"] == "hr"
    assert PSt.push_dict(s, None, {"power_state": _st("none")})["basis"] == "hr"


# ---- 3. the week plan ------------------------------------------------------------------------------

def _week(monkeypatch, cp_day, profile, aet_due=False):
    from backend.engine import aet_test as AT
    from backend.engine.status import Status
    from backend.tests.test_aet_weekday import TODAY, _build_week_ds
    ds, plan = _build_week_ds()
    for t in plan.thresholds:
        if t.cp is not None:
            t.date = cp_day
    plan.profile = profile
    if aet_due:
        monkeypatch.setattr(AT, "due", lambda *a, **k: True)
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return O.week_plan(ds, st, TODAY, prefs=PP.Prefs()), st, TODAY


def _watts(wp):
    import re
    return [s["target"] for s in wp["sessions"] if re.search(r"\d\s*W\b", s.get("target") or "")]


def test_week_plan_fresh_cp_has_power(monkeypatch):
    wp, _, _ = _week(monkeypatch, "2026-09-20", {"power_source": "stryd"})
    ps = wp["thresholds"]["power_state"]
    assert ps["state"] == PST.POWER and wp["thresholds"]["cp"] == 250.0


def test_week_plan_stale_cp_reminds_and_suggests_the_cp_test(monkeypatch):
    wp, st, today = _week(monkeypatch, "2026-08-01", {"power_source": "stryd"})
    ps = wp["thresholds"]["power_state"]
    days = (today - dt.date(2026, 8, 1)).days
    assert ps["state"] == PST.STALE and ps["days"] == days and ps["cp"] == 250.0
    # no CP in the plan: no watts in any target, the targets by HR
    assert wp["thresholds"]["cp"] is None and not _watts(wp)
    reminder = f"CP 已 {days} 天沒測，目前用心率／配速；做一次 CP 測試就切回功率"
    assert any(n.get("src") == "power_state" and n["text"] == reminder for n in wp["notes"])
    (cp,) = [t for t in wp["test_suggestions"] if t["kind"] == "cp"]
    assert reminder in cp["reason"]


def test_week_plan_without_a_meter_no_cp_test_and_the_aet_test_is_not_held_back(monkeypatch):
    # the same week with a meter: the stale CP's test is suggested and holds the AeT test back
    wp2, _, _ = _week(monkeypatch, "2026-08-01", {"power_source": "stryd"}, aet_due=True)
    kinds2 = {t["kind"] for t in wp2["test_suggestions"]}
    assert "cp" in kinds2 and "aet" not in kinds2
    wp3, st, _ = _week(monkeypatch, "2026-08-01", {"power_source": "none"}, aet_due=True)
    ps = wp3["thresholds"]["power_state"]
    assert ps["state"] == PST.NONE and wp3["thresholds"]["cp"] is None and not _watts(wp3)
    kinds3 = {t["kind"] for t in wp3["test_suggestions"]}
    assert "cp" not in kinds3 and "aet" in kinds3
    # no test added in its place (owner 2026-10-27), and the testing card leaves the CP out (also the
    # zone events' suggestions: no CP test in their test lists)
    ti = next(i for i in st.indicators if i.id == "testing")
    assert not any("cp" in (s.get("tests") or []) for s in (ti.extra or {}).get("test_suggestions") or [])
    assert (ti.extra or {}).get("cp_due") is False and "CP" not in (ti.why or "").split("；")[0]
    # nothing deleted: the plan's CP row is still there
    assert any(t.cp == 250.0 for t in st.plan.thresholds)


def test_easy_targets_leave_the_power_out(monkeypatch):
    import types
    from backend.engine import base_check as BC
    from backend.engine.status import Status
    from backend.tests.test_aet_weekday import TODAY, _build_week_ds
    seen = []
    monkeypatch.setattr(BC, "easy_targets", lambda ds, today, aet, power=True: seen.append(power))
    for src, want in (("none", False), ("stryd", True)):
        ds, plan = _build_week_ds()
        plan.profile = {"power_source": src}
        ds.config = types.SimpleNamespace(parity=False)               # easy_targets runs outside the parity mode
        st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
        O.week_plan(ds, st, TODAY, prefs=PP.Prefs())
        assert seen[-1] is want, src


def test_zone_event_suggestions_drop_the_cp_test_without_a_meter():
    from types import SimpleNamespace
    from backend.engine import zone_events as ZE
    from backend.engine.planning import Plan
    out = [ZE._suggestion("break", ["cp", "tt30", "aet"], "t", "x", D), ZE._suggestion("cp_low_easy", ["cp"], "t", "x", D)]
    none = SimpleNamespace(plan=Plan(profile={"power_source": "none"}), workouts=[])
    (b,) = ZE.without_cp_tests(out, none, D)
    assert b["tests"] == ["tt30", "aet"] and len(b["conditions"]) == 2
    stryd = SimpleNamespace(plan=Plan(profile={"power_source": "stryd"}), workouts=[])
    assert ZE.without_cp_tests(out, stryd, D) == out


# ---- 4. the threshold estimate in ③ ----------------------------------------------------------------

def test_the_estimate_without_a_meter_suggests_no_test(monkeypatch):
    from backend.engine import power_state as PST
    from backend.engine import thresholds as THR
    from backend.engine.planning import Plan
    from backend.tests.test_lthr_without_power_sp294 import STRYD_CP, TODAY, _DS, _history, fresh_cp_days
    stryd = _DS(_history("trained", scale_of=lambda d: 1.0), src_of=lambda w: PS.STRYD, cp_charts=STRYD_CP,
                plan=Plan(profile={"power_source": "none"}))
    lt = THR.estimate(stryd, TODAY)["lthr"]
    assert lt["value"] is None and lt["no_meter"] and lt["links"] == []
    assert THR.estimate(stryd, TODAY)["cp"] is None
    stryd.plan.profile = {"power_source": "stryd"}                    # a meter again …
    # … SP-952: but no CP test or race in 42 days (②) — the old CP judges nothing, no LTHR from runs
    lt = THR.estimate(stryd, TODAY)["lthr"]
    assert lt["value"] is None and lt.get("stale") and THR.estimate(stryd, TODAY)["cp"] is None
    monkeypatch.setattr(PST, "cp_days", fresh_cp_days)                # CP tests kept current (①): the runs count
    assert THR.estimate(stryd, TODAY)["lthr"]["value"] is not None


# ---- 5. the automatic run: a new power state re-plans and re-sends --------------------------------

def _with_ps(inp, ps):
    inp = copy.deepcopy(inp)
    inp["thresholds"]["power_state"] = ps
    inp["cur"]["thresholds"]["power_state"] = ps
    return inp


def test_a_new_power_state_replans_and_repushes(monkeypatch):
    from backend.engine import plan_auto as PA
    from backend.tests.test_coros_workouts import make_db, run
    from backend.tests.test_plan_auto import Box, Push, base_inputs
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: dt.date(2026, 9, 30))
    one = PST.state(dt.date(2026, 9, 30), None, source="stryd", how="設定", last_cp_test="2026-09-20")
    b = Box(monkeypatch, _with_ps(base_inputs(), one), Push())

    async def go():
        db = await make_db()
        assert (await PA.run(db))["status"] == "applied"
        assert (await PA.settings(db))["state"]["power_state"] == PST.POWER
        q = [s for c in b.push.calls for s in c["sessions"] if s["kind"] == "quality"]
        assert q and q[-1]["basis"] == "power"
        n0 = len(b.push.calls)
        assert (await PA.run(db))["status"] == "noop" and len(b.push.calls) == n0     # nothing new
        # the equipment set to 沒有功率計 (put_profile's run): re-planned and re-sent, by HR
        b.inp = _with_ps(base_inputs(), PST.state(dt.date(2026, 9, 30), None, source="none", how="設定"))
        r = await PA.run(db, trigger="power_source")
        c = r["power_state_change"]
        assert (c["old"], c["new"]) == (PST.POWER, PST.NONE)
        assert len(b.push.calls) > n0
        q = [s for s in b.push.calls[-1]["sessions"] if s["kind"] == "quality"]
        assert q and all(s["basis"] == "hr" for s in q)
        assert (await PA.settings(db))["state"]["power_state"] == PST.NONE
    run(go())


def test_put_profile_starts_the_run_only_when_the_source_changes(monkeypatch, tmp_path):
    from backend.api import plan as API
    from backend.engine import plan_auto as PA
    from backend.engine import planning as PL
    monkeypatch.setattr(PL, "plan_path", lambda: tmp_path / "plan.json")
    monkeypatch.setattr(API, "_app_weight", lambda: None)
    monkeypatch.setattr("backend.api.wko5views.plan_changed", lambda t: None)
    seen = []
    monkeypatch.setattr(PA, "after_power_source", lambda: seen.append("ps"))
    monkeypatch.setattr(PA, "after_thresholds", lambda: seen.append("th"))
    API.put_profile(API.ProfileIn(power_source="stryd"))
    API.put_profile(API.ProfileIn(power_source="stryd", sex="male"))
    API.put_profile(API.ProfileIn(power_source="none", sex="male"))
    assert seen == ["ps", "th", "ps"]


def test_power_state_api_without_data(monkeypatch, tmp_path):
    from backend.api import plan as API
    from backend.engine import planning as PL
    monkeypatch.setattr(PL, "plan_path", lambda: tmp_path / "plan.json")
    PL.Plan(profile={"power_source": "none"}).save()

    def boom():
        raise FileNotFoundError("no data")
    monkeypatch.setattr("backend.api.wko5views._dataset", boom)
    out = API.get_power_state()
    assert out["state"] == PST.NONE and out["equipment"] == "none" and out["suggest"] is None
