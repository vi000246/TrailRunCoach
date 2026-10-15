"""
SP-437: a session's time, TSS and title come from its structure (engine/session_structure.py). The
generator's cards (minutes / TSS) = the structure the watch gets; the easy steps count at the runner's
own TSS per hour (the owner 2026-10-13, Q1 A); a saved structure sets the time, the TSS and the title's
「N×M」 (Q4 A); the week totals / CTL projection read those numbers and a 減量期 stays a taper.
Synthetic data only.
"""
import asyncio
import copy
from datetime import date

import pytest

from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.engine import quality_gate as QG
from backend.engine import session_structure as SST
from backend.engine import workout_steps as WS
from backend.engine.planning import Event
from backend.engine.status import Status
from backend.sync import coros_workouts as CW
from backend.tests.test_b2b import _history, _phases, _plan_with
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30

TH = {"cp": 260.0, "lthr": 170.0, "aet": 150.0}
RATES = {"easy": 50.0, "easy_trail": 52.0, "road": 55.0, "trail": 60.0, "hike": 45.0}


def _pushed_seconds(steps) -> int:
    return sum(x.seconds if isinstance(x, CW.Step) else x.sets * _pushed_seconds(x.steps) for x in steps)


def _structure_sec(s: dict, th: dict) -> int:
    return WS.totals(SST.structure_of(s, th), WS.session_ctx(s, th))["sec"]


# ---- workout_steps.totals: the easy steps at the runner's own rate ---------------------------------

STEPS = {"items": [
    {"id": "w", "kind": "warm", "dur": {"type": "time", "value": 900}, "target": {"type": "auto", "intent": "easy"}},
    {"id": "r", "kind": "repeat", "times": 3, "last_rest": True, "items": [
        {"id": "x", "kind": "work", "dur": {"type": "time", "value": 600},
         "target": {"type": "auto", "intent": "band", "lo": 0.95, "hi": 1.0, "cls": "Z4"}},
        {"id": "y", "kind": "rest", "dur": {"type": "time", "value": 120}, "target": {"type": "auto", "intent": "open"}}]},
    {"id": "c", "kind": "cool", "dur": {"type": "time", "value": 600}, "target": {"type": "auto", "intent": "easy"}}]}


def test_totals_count_the_easy_steps_at_the_runners_rate():
    generic = WS.totals(STEPS, WS.Ctx.of(TH, "power"))
    own = WS.totals(STEPS, WS.Ctx(**{**vars(WS.Ctx.of(TH, "power")), "easy_tph": 40.0}))
    easy_s = 900 + 3 * 120 + 600
    assert generic["easy_s"] == own["easy_s"] == easy_s and generic["sec"] == own["sec"] == easy_s + 1800
    # the work steps keep IF² × 100 per hour (≈ 97.5 % CP); the rest of the session takes the runner's 40 / h
    assert own["tss_hard"] == generic["tss_hard"] == pytest.approx(1800 * 0.975 ** 2 * 100 / 3600, abs=0.01)
    assert own["tss"] == pytest.approx(own["tss_hard"] + easy_s * 40.0 / 3600, abs=0.06)
    # without a rate (Ctx.easy_tph None) nothing changes: every step at IF² as before
    assert generic["tss"] > generic["tss_hard"] and generic["tss"] != own["tss"]


def test_kind_rate_is_the_generators_category_rate():
    assert SST.kind_rate({"kind": "easy"}, RATES) == 50.0
    assert SST.kind_rate({"kind": "easy", "terrain": "trail"}, RATES) == 52.0
    assert SST.kind_rate({"kind": "long", "title": "LSD（路跑）"}, RATES) == 55.0
    assert SST.kind_rate({"kind": "long", "title": "LSD（山路）"}, RATES) == 60.0
    assert SST.kind_rate({"kind": "hike"}, RATES) == 45.0
    # a quality session's / a test's warm-up, cool-down and rests: the easy-run rate (SP-302)
    assert SST.kind_rate({"kind": "quality"}, RATES) == SST.kind_rate({"kind": "test"}, RATES) == 50.0
    assert SST.kind_rate({"kind": "quality"}, None) == O.easy_est_rate()          # no data: 64 (推估)


def test_an_all_easy_session_keeps_its_card():
    s = {"kind": "easy", "title": "輕鬆跑", "minutes": 45, "tss": 37.80123, "target": "", "detail": ""}
    assert not SST.apply_one(s, TH, RATES, target=False)
    assert (s["minutes"], s["tss"]) == (45, 37.80123)        # the generator's own rate, exactly


# ---- the generator's text intervals: card = structure = watch -------------------------------------

def _qg_recovery():
    return QG.session(QG.RECOVERY, TH)


@pytest.mark.parametrize("name, make, card, real", [
    ("road 2×15", lambda: dict(O.ROAD_SPECIFIC_Q, target=""), 60, 61),             # 15 + 2 × (15 + 3) + 10
    ("road taper full", lambda: dict(O.TAPER_FULL["road"], target=""), 60, 61),     # SP-411's 60 vs 61
    ("road taper cut", lambda: dict(O.TAPER_CUT["road"], target=""), 50, 51),       # SP-411's 50 vs 51
    ("taper cruise 4×3", lambda: dict(O.TAPER_Q, target=""), 45, 49),
    ("trail VO2 5×4", lambda: dict(O.TRAIL_SPECIFIC_Z5, target=""), 60, 65),
    ("recovery fartlek", _qg_recovery, 35, 37),
])
def test_text_interval_minutes_and_tss_follow_the_structure(name, make, card, real):
    """The owner 2026-10-13 (Q3 A): the 1–5 min a text interval's card was short of its structure (the
    rest after the last rep, a rest the text doesn't write) — the card takes the structure's."""
    s = {k: v for k, v in make().items() if k not in ("power_pct", "hr_cls")}
    s.setdefault("id", "quality")
    assert s["minutes"] == card
    assert SST.apply_one(s, TH, RATES)
    assert s["minutes"] == real == _structure_sec(s, TH) // 60
    assert _pushed_seconds(CW.session_steps(s, CW.Thresholds.of(TH))) == real * 60
    f = SST.figures(s, TH, RATES)
    assert s["tss"] == f["tss"] and f["easy_s"] > 0
    assert not SST.apply_one(s, TH, RATES)                                      # idempotent


def test_a_lap_button_structure_keeps_the_card():
    s = {"kind": "easy", "title": "輕鬆跑", "minutes": 40, "tss": 33.0,
         "steps": {"items": [{"id": "a", "kind": "work", "dur": {"type": "open"}, "target": {"type": "auto", "intent": "easy"}}]}}
    assert SST.figures(s, TH, RATES) is None and not SST.apply_one(s, TH, RATES)


# ---- the whole generator: this week, the projected weeks, the suggested tests ---------------------

def _scenario(name):
    if name == "road":
        from backend.tests import test_primary_sport as TPS
        _ds, plan, wp = TPS._wp("road")
        return plan, wp, date(2026, 12, 6)
    ds = _history(TODAY)
    if name == "baiyue":
        plan = _plan_with("2026-12-05", 4, TODAY)
    else:
        plan = _plan_with("2027-06-01", 1, TODAY)
        plan.events = [Event("e1", "越野賽", "2026-11-14" if name == "trail_taper" else "2027-05-01", kind="race",
                             priority="A", distance_km=30, climbing_m=2000, est_hours=5.0)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return plan, O.week_plan(ds, st, TODAY), date(2026, 12, 6)


@pytest.fixture(scope="module", params=["road", "baiyue", "trail_taper", "base"])
def generated(request):
    """(scenario, plan, week_plan, projected weeks, the same weeks without the structure pass)."""
    plan, wp, until = _scenario(request.param)
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), until, events=plan.events)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(SST, "apply", lambda *a, **k: 0)
        mp.setattr(SST, "apply_objs", lambda *a, **k: 0)
        plan0, wp0, _u = _scenario(request.param)
        before = [{"start": wp0["week"]["start"], "mode": wp0["mode"], "hours": wp0["target"]["hours"],
                   "sessions": wp0["sessions"]}] + PJ.project_weeks(wp0, _phases(plan0, TODAY), until, events=plan0.events)
    return request.param, plan, wp, weeks, before


def _runs(ss):
    return [s for s in ss if s.get("kind") in SST.RUN_KINDS and not s.get("done") and s.get("day")]


def test_generated_cards_are_their_structures(generated):
    """Acceptance: every auto session's card time and TSS = the structure pushed to the watch (the SP-411 /
    SP-408 kind of mismatch can't come back) — this week, every projected week and the suggested tests."""
    name, _plan, wp, weeks, _before = generated
    th = {**TH, **{k: v for k, v in wp["thresholds"].items() if v is not None}}
    rates = wp["tss_per_category"]
    sessions = _runs(wp["sessions"]) + [s for w in weeks for s in _runs(w["sessions"])] \
        + [{**t["session"], "id": "test_" + t["kind"]} for t in wp["test_suggestions"]]
    checked = 0
    for s in sessions:
        f = SST.figures(s, th, rates)
        if f is None:
            continue
        checked += 1
        assert s["minutes"] * 60 == pytest.approx(f["sec"], abs=30), (name, s["title"])
        assert s["tss"] == pytest.approx(f["tss"], abs=0.05), (name, s["title"])
        try:
            pushed = _pushed_seconds(CW.session_steps(s, CW.Thresholds.of(th)))
        except CW.Unsupported:
            continue
        assert pushed == f["sec"], (name, s["title"])
    assert checked >= 10
    for t in wp["test_suggestions"]:
        assert t["minutes"] == t["session"]["minutes"]


def test_week_totals_and_projection_read_the_structures(generated):
    """The projected weeks' planned TSS (the CTL that sizes the next week) is the sessions' structure TSS."""
    _name, _plan, _wp, weeks, _before = generated
    for w in weeks:
        assert w["tss"] == pytest.approx(sum(s["tss"] for s in w["sessions"] if s.get("day")), abs=1e-6)


def test_generated_output_is_a_fixed_point(generated):
    """Running the pass again (plan_store.reconcile_with_adapt does, after adapt) changes nothing."""
    _name, _plan, wp, weeks, _before = generated
    th = {**TH, **{k: v for k, v in wp["thresholds"].items() if v is not None}}
    for ss in [wp["sessions"]] + [w["sessions"] for w in weeks]:
        again = copy.deepcopy(ss)
        assert SST.apply(again, th, wp["tss_per_category"]) == 0


def test_taper_and_recovery_weeks_stay_light(generated):
    """Regression: with the structures' numbers a 減量期 week is still ≤ 50 % (≤ 40 % in the race's last
    7 days) of the 6 weeks before it and a recovery week 70 % (SP-512) of the 3 before it; the weekly hours are
    what they were without the pass and each week's scheduled minutes moved by the text intervals' few
    minutes at most (a taper week is not made heavier)."""
    name, _plan, wp, weeks, before = generated
    hist = [float(h["hours"]) for h in wp.get("history") or []] + [float(wp["target"]["hours"])]
    seen = set()
    run_min = lambda w: sum(s["minutes"] for s in w["sessions"] if s["kind"] != "strength" and s.get("day"))
    for w, b in zip(weeks, before[1:]):
        assert (w["start"], w["mode"]) == (b["start"], b["mode"])
        assert w["hours"] == pytest.approx(b["hours"], rel=0.02), (name, w["start"])
        n_q = sum(1 for s in w["sessions"] if s["kind"] in ("quality", "test"))
        assert 0 <= run_min(w) - run_min(b) <= 5 * n_q, (name, w["start"])
        if w["mode"] == "taper":
            assert w["hours"] <= 0.5 * sum(hist[-6:]) / len(hist[-6:]) + 1e-6, (name, w["start"])
            seen.add("taper")
        if w["mode"] == "recovery_week":
            assert w["hours"] == pytest.approx(0.70 * sum(hist[-3:]) / 3, rel=1e-6), (name, w["start"])
            seen.add("recovery")
        hist.append(float(w["hours"]))
    if name == "trail_taper":
        # (SP-668: the road marathon, Sat 12/05, tapers from Wed 11/25 — the projection's last week, 11/23,
        # is the week it starts in, a 專項期 week by its Monday)
        assert "taper" in seen
    assert "recovery" in seen


# ---- the user's own structure (POST / PATCH with steps) -------------------------------------------

def test_retitle_follows_the_structure():
    def doc(*reps, extra=()):
        items = [{"id": "w", "kind": "warm", "dur": {"type": "time", "value": 900}, "target": {"type": "auto", "intent": "easy"}}]
        for i, (n, sec) in enumerate(reps):
            items.append({"id": f"r{i}", "kind": "repeat", "times": n, "items": [
                {"id": f"x{i}", "kind": "work", "dur": {"type": "time", "value": sec}, "target": {"type": "auto", "intent": "open"}},
                {"id": f"y{i}", "kind": "rest", "dur": {"type": "time", "value": 120}, "target": {"type": "auto", "intent": "open"}}]})
        return {"items": items + list(extra)}
    assert SST.retitle("有氧間歇（巡航）3×12 分", doc((4, 600))) == "有氧間歇（巡航）4×10 分"
    assert SST.retitle("有氧間歇（巡航）3×12 分", doc((3, 720))) == "有氧間歇（巡航）3×12 分"
    assert SST.retitle("輕鬆跑＋坡道衝刺 8×10 秒", doc((6, 10))) == "輕鬆跑＋坡道衝刺 6×10 秒"
    # a pyramid / two different blocks: the 「N×M」 can't be read any more → 「（已改）」 once
    pyramid = {"items": [{"id": str(i), "kind": "work", "dur": {"type": "time", "value": v}, "target": {"type": "auto", "intent": "open"}}
                         for i, v in enumerate((60, 120, 180, 120, 60))]}
    assert SST.retitle("VO2max 5×2 分", pyramid) == "VO2max 5×2 分（已改）"
    assert SST.retitle("VO2max 5×2 分（已改）", pyramid) == "VO2max 5×2 分（已改）"
    assert SST.retitle("VO2max 5×2 分", doc((3, 180), (4, 120))) == "VO2max 5×2 分（已改）"
    # no 「N×M」 in the title: nothing to follow
    assert SST.retitle("輕鬆跑", doc((4, 600))) == "輕鬆跑"


def _api(monkeypatch, cur_rates=None):
    from backend.api import plan_sessions as PSAPI
    from backend.engine import target_policy as TP

    async def inputs(*a, **k):
        return {"thresholds": dict(TH), "cur": {"tss_per_category": cur_rates or RATES}}
    monkeypatch.setattr(PSAPI, "_inputs", inputs)
    monkeypatch.setattr(PSAPI, "_tpace", lambda: None)
    monkeypatch.setattr(PSAPI, "_speeds", lambda: {})
    monkeypatch.setattr(PP, "load", lambda *a, **k: PP.Prefs())
    monkeypatch.setattr(TP, "_auto_power_ok", lambda: True)
    return PSAPI


def _rep_doc(n, work_s, rest_s=120):
    return {"origin": "user", "items": [
        {"id": "w", "kind": "warm", "dur": {"type": "time", "value": 900}, "target": {"type": "auto", "intent": "easy"}},
        {"id": "r", "kind": "repeat", "times": n, "items": [
            {"id": "x", "kind": "work", "dur": {"type": "time", "value": work_s},
             "target": {"type": "auto", "intent": "band", "lo": 0.95, "hi": 1.0, "cls": "Z4"}},
            {"id": "y", "kind": "rest", "dur": {"type": "time", "value": rest_s}, "target": {"type": "auto", "intent": "open"}}]},
        {"id": "c", "kind": "cool", "dur": {"type": "time", "value": 600}, "target": {"type": "auto", "intent": "easy"}}]}


def test_saving_a_structure_sets_time_tss_target_and_title(monkeypatch):
    """Acceptance (Q4 A + §1.2): the user changes 3×12 into 4×10 — the stored minutes, TSS, target and the
    title's 「N×M」 are the new structure's; the dialog's old-rate TSS in the body is not kept."""
    PSAPI = _api(monkeypatch)
    cur = {"uid": "u1", "kind": "quality", "title": "有氧間歇（巡航）3×12 分", "minutes": 67, "tss": 72.6,
           "target": "功率 247–260 W", "detail": "休 2 分", "day": "2026-10-06"}
    st = _rep_doc(4, 600)
    body = {"title": cur["title"], "minutes": 67, "tss": 72.6, "target": "功率 247–260 W", "steps": st}
    out = asyncio.run(PSAPI._with_steps(dict(body), {"thresholds": dict(TH), "cur": {"tss_per_category": RATES}}, cur))
    sec = 900 + 4 * (600 + 120) + 600
    assert out["minutes"] == round(sec / 60) == 73
    c = WS.Ctx(**{**vars(WS.Ctx.of(TH, "power")), "easy_tph": RATES["easy"]})
    want = WS.totals(WS.normalize(st), c)["tss"]
    assert out["tss"] == want != 72.6
    assert out["title"] == "有氧間歇（巡航）4×10 分"
    # the user typed a title of their own in the same save: theirs
    mine = asyncio.run(PSAPI._with_steps({**body, "title": "我的節奏跑"}, {"thresholds": dict(TH), "cur": {}}, cur))
    assert mine["title"] == "我的節奏跑"
    # the target: the dialog sends the structure's summary when it changed (schedule.html); without one the
    # server takes the summary itself
    no_t = asyncio.run(PSAPI._with_steps({k: v for k, v in body.items() if k != "target"},
                                         {"thresholds": dict(TH), "cur": {}}, cur))
    assert no_t["target"] == WS.steps_text(WS.normalize(st), WS.session_ctx(cur, TH)) and "W" in no_t["target"]


def test_an_easy_run_edited_keeps_its_own_rate(monkeypatch):
    """An easy run (all easy steps) saved 60 min long: its TSS = 60 × the session's own rate (the
    generator's), not a generic IF² estimate."""
    PSAPI = _api(monkeypatch)
    cur = {"uid": "u2", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "tss": 36.0, "target": "", "detail": "",
           "day": "2026-10-06"}
    st = {"origin": "user", "items": [{"id": "a", "kind": "work", "dur": {"type": "time", "value": 3600},
                                       "target": {"type": "auto", "intent": "easy"}}]}
    out = asyncio.run(PSAPI._with_steps({"title": "輕鬆跑", "minutes": 60, "tss": 99.0, "steps": st},
                                        {"thresholds": dict(TH), "cur": {"tss_per_category": RATES}}, cur))
    assert out["minutes"] == 60 and out["tss"] == pytest.approx(48.0, abs=0.05)       # 36 / 45 × 60
    assert out["title"] == "輕鬆跑"


# ---- Q2 B: the auto sessions' target text = the structure's summary -------------------------------

def test_auto_target_is_the_structures_summary():
    """The owner 2026-10-13 (Q2 B): an easy run's card says what its structure is (the power band and, on a
    power session, its HR cap) instead of the generator's own text."""
    s = {"id": "easy1", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "tss": 37.8, "detail": "",
         "target": "心率 < 150 bpm · 功率 169–208 W · 最近 6 次輕鬆跑：AeT 150 bpm ≈ 5:23/km"}
    assert SST.apply_one(s, TH, RATES)
    assert s["target"] == "功率 169–208 W（65–80% CP） · 心率 ≤ 150 bpm"
    assert (s["minutes"], s["tss"]) == (45, 37.8)
    assert not SST.apply_one(s, TH, RATES)                                      # idempotent


def test_interval_target_keeps_the_hr_its_steps_carry():
    s = next({**x, "id": "quality"} for x in (QG.session(sp, TH) for sp in QG.LADDER) if "心率" in (x.get("target") or ""))
    th = {**TH}
    before = CW.session_steps(dict(s), CW.Thresholds.of(th))
    SST.apply_one(s, th, RATES)
    assert "心率" in s["target"] and CW.session_steps(s, CW.Thresholds.of(th)) == before


def test_a_target_the_structure_reads_is_kept():
    """長爬坡反覆's steps take their note from the target text (workout_steps._climb_items): the summary
    would change what the watch gets, so the session keeps its text."""
    s = {"id": "easy2", "kind": "easy", "title": "長爬坡反覆 3×20 分（9% 坡）", "minutes": 120, "tss": 100.0,
         "target": "心率約輕鬆跑上限 150 bpm（實測 AeT）", "detail": "暖身 15 分、緩和 10 分", "terrain": "trail"}
    was = s["target"]
    SST.apply_one(s, TH, RATES)
    assert s["target"] == was


def test_generated_push_is_unchanged(generated):
    """What the watch gets for every generated session is the same with and without the pass (minutes,
    TSS and target rewritten, nothing re-pushed) — and the target is the summary wherever it can be."""
    name, _plan, wp, weeks, before = generated
    th = {**TH, **{k: v for k, v in wp["thresholds"].items() if v is not None}}
    after = [{"sessions": wp["sessions"]}] + weeks
    n_sum = n_all = 0
    for a, b in zip(after, before):
        bb = {(s.get("id"), s.get("day")): s for s in b["sessions"]}
        for s in _runs(a["sessions"]):
            o = bb.get((s.get("id"), s.get("day")))
            if o is None:
                continue
            got, want = SST._pushed(s, th), SST._pushed(o, th)
            assert got == want, (name, s["title"])
            f = SST.figures(s, th, wp["tss_per_category"])
            if f is not None:
                n_all += 1
                n_sum += s.get("target") == f["summary"]
    assert n_sum >= 0.6 * n_all, (name, n_sum, n_all)


def test_first_run_relabel_is_one_time_and_not_a_big_change():
    """plan_auto: the first run on the structure rule marks its minutes / TSS / target-only items
    (rule 「structure」) and they don't count for 「> 3 sessions changed」; an adapt item keeps its rule."""
    from backend.engine import plan_auto as PA
    days = ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17", "2026-10-18"]
    stored = [{"uid": f"u{i}", "day": d, "kind": "easy", "title": "輕鬆跑", "minutes": 45, "tss": 37.0, "target": "心率 < 150",
               "state": "active", "week_start": "2026-10-12"} for i, d in enumerate(days)]
    new = [dict(s, target="功率 169–208 W（65–80% CP） · 心率 ≤ 150 bpm") for s in stored]
    new[0]["tss"] = 39.0                                       # one TSS up (a quality session's structure TSS)
    items = PA.diff(stored, new, [], [])
    assert len(items) == 5 and not all(PA.is_reduction(i) for i in items)
    args = ("2026-10-13", "2026-10-19", "base", "base", None)
    assert [b["rule"] for b in PA.classify(stored, new, items, *args)] == ["many"]
    assert PA.mark_structure(items) == 5
    assert all(i["rule"] == PA.STRUCTURE_RULE and i["reason"] for i in items)
    assert PA.classify(stored, new, items, *args) == []
    adapt = [{"action": "changed", "uid": "x", "rule": "fatigue", "before": {"minutes": 50}}]
    assert PA.mark_structure(adapt) == 0 and adapt[0]["rule"] == "fatigue"


def test_summary_keeps_the_cards_meaning():
    """The summary must not change what a card's target means: a walking session keeps the uphill cap the
    generator wrote (SP-115: 「心率 ≤ 爬坡上限 X bpm … 或 RPE ≤ 13，先到的為準」, what walk_band pushes), and
    an HR band says plain bpm — no editor 「% LTHR」 note (the band may be a 儲備心率 / 課表心率區間 one)."""
    from backend.engine import hr_profile as HP
    walk = {"value": 147, "pct": 0.75, "source": "max_hr"}
    th = {**TH, "walk_cap": walk}
    s = {"id": "long", "kind": "long", "title": "攻頂日模擬｜大小霸", "minutes": 240, "tss": 180.0, "terrain": "trail",
         "detail": "背 12 kg 的背包", "target": HP.walk_cap_hr(walk, TH["aet"])}
    was = s["target"]
    SST.apply_one(s, th, RATES)
    assert s["target"] == was and s["target"].startswith("心率 ≤ 爬坡上限 147 bpm") and "RPE ≤ 13" in s["target"]
    q = {"id": "quality", "kind": "quality", "title": "有氧間歇（巡航）3×10 分", "minutes": 60, "tss": 70.0,
         "target": "心率 150–160 bpm", "detail": "休 2 分；暖身 15 分、緩和 10 分", "target_basis": "hr"}
    SST.apply_one(q, TH, RATES)
    assert "LTHR" not in q["target"] and q["target"].startswith("心率 ")
