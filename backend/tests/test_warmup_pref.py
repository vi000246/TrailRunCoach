"""
SP-364 課表偏好「每堂課前加熱身」 (plan.prefs.warmup_on / warmup_min, engine/warmup.py): off by default;
on = every generated run session and every inserted template has a warm-up of at least the set
minutes. A session / template that already has one keeps it and only tops it up (a longer one is
never shortened); a plain easy / long run takes its first minutes as the warm-up (its time, TSS and
the week's budget unchanged); strength / rest / notices never get one; the COROS steps include it.
Synthetic data only, COROS mocked (nothing is sent).
"""
import copy
from dataclasses import replace
from datetime import date

import pytest

from backend.engine import aet_test as AT
from backend.engine import cp_protocols as CPP
from backend.engine import interval_library as IL
from backend.engine import plan_prefs as PP
from backend.engine import projection as P
from backend.engine import warmup as WU
from backend.engine import workout_steps as WS
from backend.settings import repository as SR
from backend.sync import coros_workouts as CW

FULL = {"cp": 250.0, "lthr": 168.0, "aet": 150.0}
ON = PP.Prefs(warmup_on=True, warmup_min=20)
ON10 = PP.Prefs(warmup_on=True, warmup_min=10)
RATES = {"easy": 60.0, "road": 70.0, "trail": 55.0}


def lead_s(items) -> int:
    """Seconds of the leading warm-up block (warm steps + a strides repeat right after them)."""
    return WS.lead_warm_s(items)


def struct(s: dict) -> list:
    """The steps a session is pushed with: its stored structure, else derived from its text."""
    return (s.get("steps") or WS.derive(s, FULL))["items"]


def easy(minutes=45, **kw):
    return {"id": "easy1", "kind": "easy", "title": "輕鬆跑", "minutes": minutes, "target": "",
            "detail": "心率不超過輕鬆跑上限", "source": "", "tss": minutes / 60 * 60.0, **kw}


# ---------------------------------------------------------------------------
# the preference
# ---------------------------------------------------------------------------

def test_defaults_off_10_min_not_shaping_and_validated():
    p = PP.Prefs()
    assert p.warmup_on is False and p.warmup_min == 10 and WU.floor_min(p) == 0 and WU.floor_min(None) == 0
    assert WU.floor_min(ON) == 20 and WU.floor_min(PP.Prefs(warmup_min=20)) == 0      # off = no floor
    assert SR.DEFAULTS["plan.prefs.warmup_on"] is False and SR.DEFAULTS["plan.prefs.warmup_min"] == 10
    assert not ON.active                                        # never reshapes the week (NOT_SHAPING)
    assert PP.from_settings(ON.settings()) == ON
    for k, v in ON.settings().items():
        SR.validate(k, v)
    for bad in (4, 31, "10", True):
        with pytest.raises(ValueError):
            SR.validate("plan.prefs.warmup_min", bad)
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.warmup_on", "yes")
    with pytest.raises(ValueError):
        PP.check(replace(ON, warmup_on="yes"))


# ---------------------------------------------------------------------------
# the decorator (engine/warmup.py) on each kind of session
# ---------------------------------------------------------------------------

def test_off_changes_nothing():
    ss = [easy(), {"id": "q", "kind": "quality", "title": "閾值 3×10 分", "minutes": 60, "tss": 70.0,
                   "detail": "休 2 分；暖身 15 分、緩和 10 分", "target": ""}]
    before = copy.deepcopy(ss)
    assert WU.apply(ss, PP.Prefs(), RATES) == 0 and WU.apply(ss, PP.Prefs(warmup_min=25), RATES) == 0
    assert ss == before


def test_easy_run_takes_its_first_minutes_as_the_warm_up():
    s = easy(45)
    assert WU.apply_one(s, ON, RATES)
    assert s["minutes"] == 45 and s["tss"] == pytest.approx(45.0)        # time / TSS / budget unchanged
    assert WU.mark_min(s) == 20 and "含暖身 20 分" in s["detail"]
    d = WS.derive(s, FULL)
    assert d["items"][0]["kind"] == "warm" and d["items"][0]["dur"]["value"] == 1200
    assert sum(x["st"]["dur"]["value"] for x in WS.flat(d["items"])) == 45 * 60
    # idempotent; a later run with another floor rewrites the one mark
    assert not WU.apply_one(s, ON, RATES)
    assert WU.apply_one(s, ON10, RATES) and WU.mark_min(s) == 10 and s["detail"].count("含暖身") == 1


def test_a_short_easy_run_keeps_its_time_and_gets_a_shorter_warm_up():
    """Review #2: never lengthened after the caps — the warm-up shrinks to leave MIN_MAIN easy minutes."""
    s = easy(20)
    WU.apply_one(s, ON, RATES)
    assert WU.MIN_MAIN == 10 and s["minutes"] == 20 and s["tss"] == pytest.approx(20.0)
    assert WU.mark_min(s) == 10
    assert [x["dur"]["value"] for x in WS.derive(s, FULL)["items"]] == [600, 600]
    s = easy(12)                                               # < 5′ left for a warm-up: none
    assert not WU.apply_one(s, ON, RATES) and WU.mark_min(s) is None and s["minutes"] == 12
    # strides: 6 × (20″ + 60″) = 8′ of the 30 are not easy running → 30 − 8 − 10 = 12′ warm-up
    st = {"id": "easy2", "kind": "easy", "title": "輕鬆跑＋6×20 秒衝刺", "minutes": 30, "detail": "", "tss": 30.0}
    WU.apply_one(st, ON, RATES)
    assert st["minutes"] == 30 and WU.mark_min(st) == 12
    pushed = CW.session_steps(st, CW.Thresholds.of(FULL))
    assert pushed[0].seconds == 720 and _secs(pushed) == 30 * 60
    # a marked run shortened again later (an easy run that no longer fits) loses the mark
    st["minutes"] = 15
    assert WU.apply_one(st, ON, RATES) and WU.mark_min(st) is None


def _secs(ss) -> int:
    return sum(x.sets * sum(y.seconds for y in x.steps) if isinstance(x, CW.Repeat) else x.seconds for x in ss)


@pytest.mark.parametrize("minutes,warm", [(25, 900), (12, 120), (10, 0)])
def test_a_marked_run_trimmed_or_edited_later_keeps_its_total(minutes, warm):
    """Review #1: plan_auto's fatigue trim / a user edit keeps 「含暖身 20 分」 with fewer minutes — the
    structure and the push keep the session's time; the warm-up is what is left over 10′ easy."""
    s = easy(40)
    WU.apply_one(s, ON, RATES)
    s["minutes"] = minutes                                     # adapt._set_minutes / the editor
    d = WS.derive(s, FULL)
    assert sum(x["st"]["dur"]["value"] for x in WS.flat(d["items"])) == minutes * 60
    assert (WS.lead_warm_s(d["items"]) or 0) == warm
    pushed = CW.session_steps(s, CW.Thresholds.of(FULL))
    assert _secs(pushed) == minutes * 60
    heat = {"id": "e", "kind": "easy", "title": "熱適應輕鬆跑", "minutes": 25, "detail": "含暖身 20 分", "tss": 1.0,
            "heat": True}
    assert _secs(CW.session_steps(heat, CW.Thresholds.of(FULL))) == 25 * 60


@pytest.mark.parametrize("sess", [
    {"id": "long", "kind": "long", "title": "LSD", "minutes": 120, "detail": "平路", "tss": 120.0},
    {"id": "long", "kind": "long", "title": "長跑＋馬拉松配速 30 分", "minutes": 120, "detail": "", "tss": 130.0},
    {"id": "easy2", "kind": "easy", "title": "輕鬆跑＋6×20 秒衝刺", "minutes": 50, "detail": "", "tss": 50.0},
    {"id": "easy3", "kind": "easy", "title": "熱適應輕鬆跑", "minutes": 60, "detail": "", "tss": 55.0, "heat": True},
    {"id": "hike", "kind": "hike", "title": "健行", "minutes": 180, "detail": "", "tss": 150.0},
])
def test_easy_kinds_warm_up_in_the_steps_and_the_coros_push(sess):
    s = dict(sess)
    WU.apply_one(s, ON, RATES)
    assert s["minutes"] == sess["minutes"]
    d = WS.derive(s, FULL)
    assert d["items"][0]["kind"] == "warm" and lead_s(d["items"]) >= 1200
    assert WS.totals(WS.normalize(d), WS.Ctx.of(FULL))["sec"] == pytest.approx(s["minutes"] * 60, abs=1)
    th = CW.Thresholds.of(FULL)
    pushed = CW.session_steps(s, th)
    assert pushed[0].kind == CW.EX_WARMUP and pushed[0].seconds == 1200
    # golden: the editor's structure pushes exactly the text path's steps
    assert WS.steps_to_coros(WS.normalize(d), WS.Ctx.of(FULL, CW._basis(s))) == pushed
    # …and apart from the warm-up lap it is the old push: same steps, targets and total time
    legacy = CW.session_steps(dict(sess), th)
    secs = _secs
    assert secs(pushed) == secs(legacy) == sess["minutes"] * 60
    # the warm-up lap is easy: the easy-run HR cap (as the heat run's own warm-up and the hike)
    assert pushed[0].intensity == CW.easy_hr(th)
    key = lambda ss: [(x.kind, x.intensity, x.name) if isinstance(x, CW.Step) else (x.sets, x.name)
                      for x in ss if not (isinstance(x, CW.Step) and x.kind == CW.EX_WARMUP)]
    assert key(pushed) == key(legacy)


def test_text_interval_tops_its_warm_up_up_and_keeps_a_longer_one():
    q = {"id": "quality", "kind": "quality", "title": "閾值 3×10 分", "minutes": 60, "tss": 70.0, "target": "",
         "detail": "休 2–3 分鐘；暖身 15 分、緩和 10 分"}
    s = dict(q)
    assert WU.apply_one(s, ON, RATES)
    assert "暖身 20 分" in s["detail"] and s["minutes"] == 65                # the main set is not shortened
    assert s["tss"] == pytest.approx(70.0 + 5 / 60 * RATES["easy"])          # the extra at the easy rate
    pushed = CW.session_steps(s, CW.Thresholds.of(FULL))
    assert pushed[0].kind == CW.EX_WARMUP and pushed[0].seconds == 1200
    assert pushed[1].sets == 3 and pushed[1].steps[0].seconds == 600         # 3×10 untouched
    s = dict(q)
    assert not WU.apply_one(s, ON10, RATES) and s == q                        # 15 ≥ 10: kept as it is


def test_cp_and_aet_tests_get_the_longer_warm_up():
    cp = {**CPP.session_for("quick"), "day": "2026-10-07"}
    assert WU.apply_one(cp, ON, RATES)
    assert cp["minutes"] == 37 + 8 and "暖身 20 分" in cp["detail"]
    st = CW.session_steps(cp, CW.Thresholds.of(FULL))
    assert st[0].kind == CW.EX_WARMUP and st[0].seconds == 1200 and st[1].seconds == 1200     # 20′ all-out kept
    aet = AT.session(FULL, AT.start_hr(None, 168), AT.start_power(250), None, "ua40", None)
    assert WU.apply_one(aet, ON, RATES) and aet["minutes"] == 60
    st = CW.session_steps(aet, CW.Thresholds.of(FULL))
    assert st[0].seconds == 1200 and st[1].seconds == 40 * 60
    # the drift analysis cuts the planned (longer) warm-up, not the protocol's 10′
    assert AT.planned_warm_s(aet) == 1200 and AT.planned_warm_s({"detail": ""}) is None
    std = CPP.session_for("standard")                                         # 15′ warm-up
    assert not WU.apply_one(std, PP.Prefs(warmup_on=True, warmup_min=15), RATES)


def test_library_interval_floor_in_the_blocks_the_cap_and_the_push():
    v = IL.get("t2a")
    assert IL.blocks(v, "std")["warm_min"] == 12
    assert IL.blocks(v, "std", ON)["warm_min"] == 20                          # the city part grows
    assert IL.blocks(v, "std", ON10)["warm_min"] == 12                        # 12 ≥ 10: unchanged
    assert IL.blocks(IL.get("v4a"), "full", PP.Prefs(warmup_on=True, warmup_min=15))["warm_min"] == 20
    f = IL.fit(v.rung, None, (), ON)
    s = IL.session_for(f, FULL, prefs=ON)
    assert s["variant_adj"] == {"warm": 20}
    assert s["minutes"] == round(IL.total_min(f["variant"], f["level"], ON))
    assert "暖身 20 分" in s["detail"]
    # push time: no prefs, the stored adj carries the floor
    th = CW.Thresholds.of(FULL)
    pushed = CW.session_steps(s, th)
    warm = [x for x in pushed if getattr(x, "kind", None) == CW.EX_WARMUP]
    assert sum(x.seconds for x in warm) + sum(r.sets * sum(y.seconds for y in r.steps)
                                             for r in pushed if isinstance(r, CW.Repeat) and "快步跑" in r.name) == 1200
    d = WS.derive(s, FULL)
    assert lead_s(d["items"]) == 1200
    # the user's own swap in the drawer keeps the floor too (variant_patch → the stored variant_adj)
    vp = IL.variant_patch("t2a", None, FULL, ON)
    assert vp["variant_adj"] == {"warm": 20} and "variant_adj" not in IL.variant_patch("t2a", None, FULL, None)
    # the day cap sees the warm-up: a 45′ cap that fits the 12′ warm-up no longer fits 20′
    cap = IL.total_min(f["variant"], "min") + 1
    assert IL.fit(v.rung, cap, (), PP.Prefs())["level"] == "min"
    g = IL.fit(v.rung, cap, (), ON)
    assert IL.total_min(g["variant"], g["level"], ON) <= cap and IL.blocks(g["variant"], g["level"], ON)["warm_min"] >= 20


def test_a_variant_built_without_the_preference_is_topped_up_by_the_decorator():
    f = IL.fit("z3b", None, (), None)
    s = IL.session_for(f, FULL)
    m0, t0 = s["minutes"], s["tss"]
    add = 20 - IL.blocks(f["variant"], f["level"])["warm_min"]
    assert add > 0
    assert WU.apply_one(s, ON, RATES)
    assert s["variant_adj"]["warm"] == 20 and s["minutes"] == m0 + add
    assert s["tss"] == pytest.approx(t0 + add / 60 * 60.0)
    assert lead_s(WS.derive(s, FULL)["items"]) == 1200 and f"暖身 20 分" in s["detail"]
    assert not WU.apply_one(s, ON, RATES)                                     # once


def _tech(lo, hi, kind="hike"):
    ids = WS._Ids("x")
    steps = WS.doc([WS.step(ids, "warm", 600, WS.EASY, "好走的路段暖身"),
                    WS.step(ids, "work", 2400, {"type": "rpe", "lo": lo, "hi": hi}, "技術地形"),
                    WS.step(ids, "cool", 300, WS.EASY, "緩和")], "template:lib:x")
    return {"id": "tech", "kind": kind, "title": "技術地形 55′", "minutes": 55, "tss": 50.0, "steps": steps,
            "detail": "", "target": ""}, steps


def test_structured_sessions_easy_inside_their_time_hard_on_top():
    # an easy (RPE 3–4) generated structure keeps its time: the warm-up comes out of the easy part
    s, steps = _tech(3, 4)
    assert WU.apply_one(s, ON, RATES)
    assert s["minutes"] == 55 and [x["dur"]["value"] for x in s["steps"]["items"]] == [1200, 1800, 300]
    assert steps["items"][0]["dur"]["value"] == 600                          # the input was not mutated
    # an RPE 6–7 one is a quality session: topped up in front, the main part not cut
    s, _ = _tech(6, 7)
    assert WU.apply_one(s, ON, RATES)
    assert s["minutes"] == 65 and [x["dur"]["value"] for x in s["steps"]["items"]] == [1200, 2400, 300]
    # a downhill part (RPE 3–5) gives nothing: its own 10′ warm-up stays, the time too
    s, _ = _tech(3, 5, "easy")
    assert not WU.apply_one(s, ON, RATES) and s["minutes"] == 55


@pytest.mark.parametrize("kind", ["strength", "rest", "race", "notice", "heat_passive"])
def test_strength_rest_race_notice_get_no_warm_up(kind):
    s = {"id": kind, "kind": kind, "title": "x", "minutes": 35, "tss": 10.0, "detail": "d", "target": ""}
    before = dict(s)
    assert not WU.apply_one(s, ON, RATES) and s == before


def test_done_and_walk_run_sessions_are_left_alone():
    s = easy(40, done=True)
    assert not WU.apply_one(s, ON, RATES)
    w = {"id": "walkrun0_1", "kind": "easy", "title": "走跑交替", "minutes": 30, "tss": 20.0, "detail": "",
         "steps": WS.doc([WS.step(WS._Ids(), "work", 1800, WS.EASY)])}
    assert not WU.apply_one(w, ON, RATES)


# ---------------------------------------------------------------------------
# templates (插入範本)
# ---------------------------------------------------------------------------

def test_ensure_warm_tops_up_prepends_and_keeps_longer():
    ids = WS._Ids("e")
    main = [WS.step(ids, "work", 600, WS.OPEN)]
    items, add = WS.ensure_warm(main, 600)
    assert add == 600 and items[0]["kind"] == "warm" and items[0]["dur"]["value"] == 600 and items[1:] == main
    items, add = WS.ensure_warm([WS.step(ids, "warm", 300, WS.EASY)] + main, 600)
    assert add == 300 and items[0]["dur"]["value"] == 600
    long_w = [WS.step(ids, "warm", 1500, WS.EASY)] + main
    items, add = WS.ensure_warm(long_w, 600)
    assert add == 0 and items == long_w
    # a lap-button warm-up is the athlete's own length: left alone
    open_w = [WS.step(ids, "warm", 0, WS.EASY)] + main
    assert WS.ensure_warm(open_w, 600) == (open_w, 0)


def test_templates_full_structures_get_the_warm_up():
    plain = WS.templates()
    on = WS.templates(warm_floor_s=1200)
    rows = {r["key"]: r for g in plain["groups"] for r in g["rows"]}
    rows_on = {r["key"]: r for g in on["groups"] for r in g["rows"]}
    assert rows.keys() == rows_on.keys()
    easy_keys = {r["key"] for g in on["groups"] if g["cat"] == "easy" for r in g["rows"]}
    tot = lambda items: sum(x["st"]["dur"]["value"] for x in WS.flat(items) if x["st"]["dur"]["type"] == "time")
    n = carved = 0
    for k, r in rows_on.items():
        if not r.get("full"):
            continue
        n += 1
        before = WS.lead_warm_s(rows[k]["full"])
        assert r["items"] == rows[k]["items"]                  # 只換主課 keeps the session's own warm-up
        if before is None:                                     # an open / distance warm-up: untouched
            assert r["full"] == rows[k]["full"]
        elif k in easy_keys:
            # an easy-category template keeps its length: the warm-up comes out of its easy part
            assert tot(r["full"]) == tot(rows[k]["full"]) and WS.lead_warm_s(r["full"]) >= before, k
            carved += WS.lead_warm_s(r["full"]) == max(before, 1200)
        else:
            assert WS.lead_warm_s(r["full"]) == max(before, 1200), k
    assert n > 30 and carved >= 5


def test_my_templates_keep_their_length():
    ids = WS._Ids("u")
    mine = {"id": 7, "name": "我的輕鬆跑", "cats": ["easy"], "steps": {"items": [
        WS.step(ids, "work", 2400, WS.EASY, "輕鬆")]}}
    hard = {"id": 8, "name": "我的間歇", "cats": ["quality"], "steps": {"items": [
        WS.rep(ids, 5, [WS.step(ids, "work", 180, {"type": "power", "mode": "pct", "lo": 1.06, "hi": 1.12}),
                        WS.step(ids, "rest", 120, WS.OPEN)], False)]}}
    t = WS.templates(user={"templates": [mine, hard], "cats": []}, warm_floor_s=1200)
    rows = {r["key"]: r for g in t["groups"] for r in g["rows"]}
    assert [x["dur"]["value"] for x in rows["user:7"]["full"]] == [1200, 1200]       # 40′ still
    assert rows["user:8"]["full"] == hard["steps"]["items"]    # no easy step to take it from: as it is


# ---------------------------------------------------------------------------
# the planner: projection and week_plan
# ---------------------------------------------------------------------------

TGT = {"z2": "功率 120–150 W", "long": "心率 < 150 bpm", "threshold": "功率 170–180 W", "supra": "功率 180–190 W"}


def _week(prefs):
    return P.week_sessions(date(2026, 10, 5), "base", "base", 5.0, 50.0, TGT, 6, 60.0, False, True, 17.5, 150.0,
                           None, prefs=prefs, rates=RATES)


def test_projected_week_every_run_has_a_warm_up_and_the_budget_holds():
    off, on = _week(PP.Prefs()), _week(ON)
    runs = [s for s in on if s["kind"] in ("easy", "long", "quality", "test", "hike")]
    assert runs
    for s in runs:
        assert lead_s(struct(s)) >= 1200, s["title"]
    for s in on:
        if s["kind"] == "strength":
            assert "暖身" not in (s.get("detail") or "")
    # the interval's 5 extra minutes come out of the easy runs: the week's total holds (± the easy
    # runs' rounding to 5 min) and the number of easy runs is the same (owner 2026-10-08)
    tot = lambda ss: sum(s["minutes"] for s in ss if s["kind"] != "strength")
    q_on = next(s for s in on if s["kind"] == "quality")
    q_off = next(s for s in off if s["kind"] == "quality")
    assert q_on["minutes"] == q_off["minutes"] + 5
    n_easy = sum(s["kind"] == "easy" for s in on)
    assert n_easy == sum(s["kind"] == "easy" for s in off)
    assert abs(tot(on) - tot(off)) <= 5 * n_easy                # each easy run is rounded to 5 min


@pytest.mark.parametrize("hours", [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0])
@pytest.mark.parametrize("prefs_on", [PP.Prefs(warmup_on=True, warmup_min=30),
                                      PP.Prefs(warmup_on=True, warmup_min=30, cap_weekday=45, runs=None),
                                      PP.Prefs(warmup_on=True, warmup_min=25, cap_weekday=40, cap_mode="hard")])
def test_the_easy_run_count_never_changes(hours, prefs_on):
    """Owner 2026-10-08: no 3×40 → 2×60 — the intervals' extra warm-up minutes come out of the easy runs
    evenly, their number is what it would be without the preference; the weekday cap still holds."""
    off_p = replace(prefs_on, warmup_on=False)
    wk = lambda p: P.week_sessions(date(2026, 10, 5), "base", "base", hours, 50.0, TGT, 6, 60.0, False, True, 17.5,
                                   150.0, None, prefs=p if p.active else None, rates=RATES, warm_prefs=p)
    on, off = wk(prefs_on), wk(off_p)
    assert sum(s["kind"] == "easy" for s in on) == sum(s["kind"] == "easy" for s in off), hours
    if prefs_on.cap_weekday:
        cap = prefs_on.cap_weekday
        for s in on:
            if s["kind"] == "easy":
                assert s["minutes"] <= cap and (WU.mark_min(s) or 0) <= cap - WU.MIN_MAIN


def test_the_week_plan_keeps_its_easy_run_count():
    """The synthetic week where the 5 extra minutes of the interval's warm-up turned 3 × 40′ into 2 × 60′."""
    from backend.engine import overview as O
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY
    from backend.tests.test_recovery_week import LIGHT, _ds, _plan
    ds = _ds({4: LIGHT})
    plan = _plan("2027-03-06")
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    off = O.week_plan(ds, st, TODAY, prefs=PP.Prefs())
    on = O.week_plan(ds, st, TODAY, prefs=ON)
    ids = lambda wp: [s["id"] for s in wp["sessions"]]
    assert ids(on) == ids(off)
    q = lambda wp: next(s for s in wp["sessions"] if s["kind"] == "quality")["minutes"]
    assert q(on) == q(off) + 5


def test_project_weeks_decorates_every_projected_week():
    from backend.tests.test_plan_store import PHASES, cur_plan
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 11, 1), prefs=ON)
    runs = [s for w in weeks for s in w["sessions"] if s["kind"] in ("easy", "long", "quality", "test", "hike")]
    assert runs and all(lead_s(struct(s)) >= 1200 for s in runs)
    plain = P.project_weeks(cur_plan(), PHASES, date(2026, 11, 1), prefs=PP.Prefs())
    assert not any(WU.mark_min(s) for w in plain for s in w["sessions"])


def test_week_plan_sessions_and_suggested_tests_carry_the_warm_up():
    from backend.engine import overview as O
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY
    from backend.tests.test_recovery_week import LIGHT, _ds, _plan
    ds = _ds({4: LIGHT})
    plan = _plan("2027-03-06")
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    off = O.week_plan(ds, st, TODAY, prefs=PP.Prefs())
    on = O.week_plan(ds, st, TODAY, prefs=ON)
    left = [s for s in on["sessions"] if not s["done"] and s["kind"] in O.RUN_KINDS]
    assert left
    for s in left:
        assert lead_s(struct(s)) >= 1200, s["title"]
    # strength untouched; the week's time stays the target (the interval's longer warm-up came out
    # of the easy runs; ± the easy runs' 5-min rounding)
    sk = lambda wp: [(s["title"], s["minutes"], s["detail"]) for s in wp["sessions"] if s["kind"] == "strength"]
    assert sk(on) == sk(off)
    tot = lambda wp: sum(s["minutes"] for s in wp["sessions"] if s["kind"] != "strength")
    assert abs(tot(on) - tot(off)) <= 10
    for t in on.get("test_suggestions") or []:
        assert lead_s(WS.derive({**t["session"], "kind": "test"}, FULL)["items"]) >= 1200


def test_trim_quality_never_cuts_below_the_floor():
    s = {"title": "閾值 3×10 分", "minutes": 75, "tss": 70.0, "detail": "休 2 分；暖身 20 分、緩和 10 分"}
    assert PP.trim_quality(s, 60, warm_floor=20)
    assert "暖身 20 分" in s["detail"] and s["minutes"] <= 60


def test_the_fit_note_names_the_warm_up_when_it_is_what_does_not_fit():
    """Review #4: the floor pushes a rung to 縮量版 / a rung back under 平日上限 — the reason says so."""
    hot = PP.Prefs(warmup_on=True, warmup_min=30)
    found = 0
    for rung in IL.RUNG_ORDER:
        canon = IL.canonical(rung)
        cap = IL.total_min(canon, "min") + 1                   # fits without the preference
        assert "熱身" not in IL.fit(rung, cap, (), PP.Prefs())["reason"]
        f = IL.fit(rung, cap, (), hot)
        if f.get("reduced") or f.get("action") in ("back", "move") or "減成" in f["reason"]:
            assert f["reason"].startswith(f"熱身 30 分＋主課放不進平日上限 {cap:.0f} 分："), (rung, f["reason"])
            found += 1
    assert found


# ---------------------------------------------------------------------------
# reconcile: stable, and back to the original when switched off
# ---------------------------------------------------------------------------

def _gen(prefs):
    from backend.engine import plan_store as PS
    from backend.tests.test_plan_store import PHASES, cur_plan, inputs
    cur = cur_plan()
    WU.apply(cur["sessions"], prefs, RATES)                    # week_plan's last pass
    weeks = P.project_weeks(cur, PHASES, date(2026, 10, 25), prefs=prefs)
    inp = inputs(cur=cur, weeks=weeks, horizon="2026-10-25")
    return inp, PS.gen_weeks(inp)


def _rows(stored):
    keys = ("gen_key", "day", "kind", "title", "minutes", "detail", "target", "variant_adj", "steps", "state")
    return sorted((tuple(str(s.get(k)) for k in keys) for s in stored if s["state"] == "active"))


def test_generating_twice_changes_no_row_and_switching_off_restores_the_plan():
    from backend.engine import reconcile as R
    inp, g_on = _gen(ON)
    on1, _ = R.reconcile([], g_on, inp["activities"], inp["today"], inp["horizon_end"])
    assert any(WU.mark_min(s) for s in on1)
    _, g_on2 = _gen(ON)
    on2, changes = R.reconcile(on1, g_on2, inp["activities"], inp["today"], inp["horizon_end"])
    assert changes == [] and _rows(on2) == _rows(on1)
    # the plan without the preference, then on, then off again: the original rows
    _, g_off = _gen(PP.Prefs())
    off1, _ = R.reconcile([], g_off, inp["activities"], inp["today"], inp["horizon_end"])
    on3, ch_on = R.reconcile(off1, g_on, inp["activities"], inp["today"], inp["horizon_end"])
    assert ch_on and _rows(on3) != _rows(off1)
    back, _ = R.reconcile(on3, g_off, inp["activities"], inp["today"], inp["horizon_end"])
    assert _rows(back) == _rows(off1)
    assert not any(WU.mark_min(s) or "warm" in (s.get("variant_adj") or {}) for s in back if s["state"] == "active")


# ---------------------------------------------------------------------------
# the AeT drift analysis, scheduling a test, the drawer swap
# ---------------------------------------------------------------------------

def test_aet_analysis_cuts_the_scheduled_longer_warm_up(monkeypatch):
    import types

    import numpy as np

    from backend.engine import workout_review as WR
    t = np.arange(0, 3900, 1.0)
    samples = {"t": t, "hr": 135.0 + t / 3900.0 * 4, "speed": np.full_like(t, 3.0), "power": np.full_like(t, 200.0),
               "cadence": None}
    w = types.SimpleNamespace(idx=1, tags=[], metrics={"duration": 3900.0})
    ds = types.SimpleNamespace(channel=lambda i, ch: None)
    sched = {"title": "AeT 飄移測試 40 分", "kind": "test", "protocol": "aet",
             "target": "固定功率 200 W（±3%）", "detail": "暖身 20 分到開始流汗，接著測試 40 分固定功率不要調"}
    monkeypatch.setattr(WR, "_samples", lambda ds_, w_: samples)
    monkeypatch.setattr(WR, "measure", lambda ds_, w_: {})
    monkeypatch.setattr(WR, "activity_temp", lambda ds_, w_, x: (None, None))
    monkeypatch.setattr(WR, "watch_bias_of", lambda ds_: None)
    monkeypatch.setattr(WR, "_title", lambda w_: "")
    monkeypatch.setattr(WR, "scheduled_aet_test", lambda ds_, w_: sched)
    assert AT.analyze_workout(ds, w)["warm_s"] == 1200
    monkeypatch.setattr(WR, "scheduled_aet_test", lambda ds_, w_: {**sched, "detail": "暖身 10 分到開始流汗"})
    assert AT.analyze_workout(ds, w)["warm_s"] == 600


def test_schedule_test_with_the_preference_on(monkeypatch):
    from backend.api import plan_sessions
    from backend.tests.test_plan_store import Env, run
    with Env(monkeypatch) as e:
        monkeypatch.setattr(PP, "load", lambda *a, **k: ON)
        sg = {"days": [{"day": "2026-10-14"}], "session": CPP.session_for("quick")}
        out = run(plan_sessions._schedule_test(e.db, e.inp, sg, "2026-10-14"))
        assert out["minutes"] == 45 and "暖身 20 分" in out["detail"]
        monkeypatch.setattr(PP, "load", lambda *a, **k: PP.Prefs())
        out = run(plan_sessions._schedule_test(e.db, e.inp, {**sg, "days": [{"day": "2026-10-15"}]}, "2026-10-15"))
        assert out["minutes"] == 37 and "暖身 12 分" in out["detail"]


def test_a_drawer_swap_keeps_the_state_machine_tweak(monkeypatch):
    """Review #3: only the warm-up floor follows the swap; rest_add / power stay on the row."""
    import json

    from sqlalchemy import select

    from backend.db.models import PlanSession
    from backend.tests.test_plan_store import API, Env, run
    with Env(monkeypatch) as e:
        monkeypatch.setattr(PP, "load", lambda *a, **k: ON)
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-14", "variant_key": "t2a"})
        assert r.status_code == 200, r.text
        uid = r.json()["uid"]
        row = run(e.db.execute(select(PlanSession).where(PlanSession.uid == uid))).scalars().one()
        assert json.loads(row.variant_adj) == {"warm": 20}
        row.variant_adj = json.dumps({"rest_add": 1, "power": 0.95, "warm": 20})
        run(e.db.commit())
        adj = lambda: json.loads(run(e.db.execute(select(PlanSession.variant_adj).where(PlanSession.uid == uid)))
                                 .scalar_one() or "null")
        assert e.c.patch(f"{API}/sessions/{uid}", json={"variant_key": "t2b"}).status_code == 200
        assert adj() == {"rest_add": 1, "power": 0.95, "warm": 20}
        monkeypatch.setattr(PP, "load", lambda *a, **k: PP.Prefs())       # switched off
        assert e.c.patch(f"{API}/sessions/{uid}", json={"variant_key": "t2a"}).status_code == 200
        assert adj() == {"rest_add": 1, "power": 0.95}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_prefs_and_templates(monkeypatch):
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        r = e.c.put(f"{API}/prefs", json={"warmup_on": True, "warmup_min": 15})
        assert r.status_code == 200 and r.json()["prefs"]["warmup_on"] is True
        got = e.c.get(f"{API}/prefs").json()
        assert got["prefs"]["warmup_min"] == 15 and got["active"] is False
        assert e.c.put(f"{API}/prefs", json={"warmup_on": True, "warmup_min": 40}).status_code == 400
        monkeypatch.setattr(PP, "load", lambda *a, **k: PP.Prefs(warmup_on=True, warmup_min=15))
        rows = [r for g in e.c.get(f"{API}/steps/templates").json()["groups"] for r in g["rows"] if r.get("full")]
        assert rows and all((WS.lead_warm_s(r["full"]) or 900) >= 900 for r in rows)
        # the 測試 dialog's sessions: the quick CP test (12′ warm-up) shown and added with 15′
        tt = e.c.get(f"{API}/test-templates").json()
        quick = next(r for r in tt["cp"] if r["protocol"] == "quick")
        assert quick["minutes"] == 40 and "暖身 15 分" in quick["detail"]
        ua40 = next(r for r in tt["aet"] if r["protocol"] == "ua40")
        assert ua40["minutes"] == 55 and "暖身 15 分" in ua40["detail"]
