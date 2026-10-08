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


def test_a_short_easy_run_keeps_10_minutes_after_its_warm_up():
    s = easy(20)
    WU.apply_one(s, ON, RATES)
    assert WU.MIN_MAIN == 10 and s["minutes"] == 30                           # 20 warm-up + 10 easy
    assert s["tss"] == pytest.approx(20.0 * 30 / 20)                         # scaled with the minutes
    d = WS.derive(s, FULL)
    assert [x["dur"]["value"] for x in d["items"]] == [1200, 600]


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
    secs = lambda ss: sum(x.sets * sum(y.seconds for y in x.steps) if isinstance(x, CW.Repeat) else x.seconds
                          for x in ss)
    assert secs(pushed) == secs(legacy) == sess["minutes"] * 60
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


def test_structured_sessions_extend_their_warm_step():
    ids = WS._Ids("x")
    steps = WS.doc([WS.step(ids, "warm", 600, WS.EASY, "好走的路段暖身"),
                    WS.step(ids, "work", 2400, {"type": "rpe", "lo": 3, "hi": 4}, "技術地形"),
                    WS.step(ids, "cool", 300, WS.EASY, "緩和")], "template:lib:x")
    s = {"id": "tech", "kind": "hike", "title": "技術地形 55′", "minutes": 55, "tss": 50.0, "steps": steps,
         "detail": "", "target": ""}
    assert WU.apply_one(s, ON, RATES)
    assert s["minutes"] == 65 and s["steps"]["items"][0]["dur"]["value"] == 1200
    assert s["steps"]["items"][1]["dur"]["value"] == 2400                    # the main part is not cut
    assert steps["items"][0]["dur"]["value"] == 600                          # the input was not mutated


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
    n = 0
    for k, r in rows_on.items():
        if not r.get("full"):
            continue
        n += 1
        before = WS.lead_warm_s(rows[k]["full"])
        if before is None:                                     # an open / distance warm-up: untouched
            assert r["full"] == rows[k]["full"]
            continue
        assert WS.lead_warm_s(r["full"]) == max(before, 1200), k
        assert r["items"] == rows[k]["items"]                  # 只換主課 keeps the session's own warm-up
    assert n > 30


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
    # runs' rounding to 5 min)
    tot = lambda ss: sum(s["minutes"] for s in ss if s["kind"] != "strength")
    q_on = next(s for s in on if s["kind"] == "quality")
    q_off = next(s for s in off if s["kind"] == "quality")
    assert q_on["minutes"] == q_off["minutes"] + 5
    assert abs(tot(on) - tot(off)) <= 10


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
