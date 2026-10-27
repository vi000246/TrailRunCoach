"""SP-951 (owner 2026-10-27, SP-950 問答 Q1-3): a first-class 配速 basis. Power is in effect only in ①
(a Stryd and a fresh CP); in ② (Stryd, CP stale) and ③ (no Stryd) — or with 配速 picked — the flat Zone 3 /
Zone 5 intervals and tempo run by % threshold pace (threshold pace ÷ % CP, 推估) with the HR as a cap note;
no threshold pace → heart rate (+ RPE) with the reason. Hill repeats → HR + RPE; road easy / long → HR ≤
the easy cap; trail, long climbs, downhill, walking, the MP segment unchanged; ① as before.
Synthetic data only (no WKO5 folder)."""
from __future__ import annotations

import datetime as dt
import re

import pytest

from backend.engine import interval_library as IL
from backend.engine import plan_prefs as PP
from backend.engine import power_state as PST
from backend.engine import quality_gate as QG
from backend.engine import session_structure as SST
from backend.engine import target_policy as TP
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW

D = dt.date(2026, 10, 27)
TPACE = 400.0                                   # 6:40 /km


def _st(source, last_test=None):
    return PST.state(D, None, source=source, how="設定", last_cp_test=last_test)


ONE = _st("stryd", "2026-10-20")
TWO = _st("stryd", "2026-09-07")
THREE = _st("none")
TH = {"cp": 260.0, "lthr": 165.0, "aet": 140.0, "tpace": TPACE}


def th_of(ps, tpace=TPACE):
    """The plan thresholds as week_plan gives them: no CP outside ① (SP-950), "tpace" only when known."""
    out = {"lthr": 165.0, "aet": 140.0, "power_state": ps, "cp": TH["cp"] if ps["state"] == PST.POWER else None}
    if tpace:
        out["tpace"] = tpace
    return out


FLAT = {"kind": "quality", "title": "有氧間歇 3×10 分"}
HILL = {"kind": "quality", "title": "爬坡 6×3 分"}
TYPES = ((FLAT, "power", "pace"), (HILL, "power", "hr"),
         ({"kind": "easy", "title": "輕鬆跑"}, "power", "hr"), ({"kind": "long", "title": "長跑"}, "power", "hr"),
         ({"kind": "easy", "title": "越野輕鬆跑", "terrain": "trail"}, "hr", "hr"),
         ({"kind": "mountain", "title": "山路長天"}, "hr", "hr"), ({"kind": "hike", "title": "越野跑"}, "hr", "hr"),
         ({"kind": "long", "title": "長爬坡"}, "none", "none"), ({"kind": "easy", "title": "下坡練習"}, "none", "none"),
         ({"kind": "easy", "id": "steep", "title": "陡坡健走 14%"}, "hr", "hr"))


# ---- 1. target_policy ------------------------------------------------------------------------------

@pytest.mark.parametrize("ps", [ONE, TWO, THREE], ids=["1", "2", "3"])
def test_the_basis_of_each_session_type_by_power_state(ps):
    for s, one, other in TYPES:
        p = TP.target_policy(s, None, th_of(ps))
        want = one if ps["state"] == PST.POWER else other
        assert p["basis"] == want, (ps["state"], s["title"], p)
        if want == "pace":
            assert p["hr_cap"] and "閾值配速" in p["source"]


def test_the_flat_interval_says_why_it_is_by_pace():
    p3 = TP.target_policy(FLAT, None, th_of(THREE))
    assert p3["fallback"] == "沒有功率計：平路間歇用配速，心率當上限" and p3["why"].startswith("自動：間歇看功率")
    p2 = TP.target_policy(FLAT, None, th_of(TWO))
    assert "CP 已 50 天沒測" in p2["fallback"]


@pytest.mark.parametrize("ps", [TWO, THREE], ids=["2", "3"])
def test_no_threshold_pace_falls_back_to_heart_rate_with_the_reason(ps):
    p = TP.target_policy(FLAT, None, th_of(ps, tpace=None))
    assert p["basis"] == "hr" and "沒有閾值配速：改用心率＋RPE" in p["fallback"]
    # no LTHR either: nothing
    th = {**th_of(ps, tpace=None), "lthr": None, "aet": None}
    assert TP.target_policy(FLAT, None, th)["basis"] == "none"


def test_picking_pace_moves_only_the_flat_intervals():
    pace = PP.Prefs(target_basis="pace")
    one = th_of(ONE)
    assert TP.target_policy(FLAT, pace, one)["basis"] == "pace"            # ① too: the runner's choice
    assert TP.target_policy(FLAT, pace, one)["why"] == "課表偏好：配速"
    assert TP.target_policy(HILL, pace, one)["basis"] == "power"           # a hill: 自動
    assert TP.target_policy({"kind": "easy", "title": "輕鬆跑"}, pace, one)["basis"] == "power"
    own = {**FLAT, "target_basis": "pace"}
    p = TP.target_policy(own, None, one)
    assert p["basis"] == "pace" and p["chosen"] == "pace" and p["why"] == "這次課表你選了配速"
    # picked, but no threshold pace: heart rate, said so
    p = TP.target_policy(FLAT, pace, th_of(ONE, tpace=None))
    assert p["basis"] == "hr" and "沒有閾值配速" in p["why"]


def test_pace_is_a_setting_value():
    assert PP.from_settings({"plan.prefs.target_basis": "pace"}).target_basis == "pace"
    assert TP.pref_basis(PP.Prefs(target_basis="pace")) == "pace"


def test_flat_pace_needs_a_power_state_or_pace_picked():
    assert TP.flat_pace(th_of(THREE)) and TP.flat_pace(th_of(TWO))
    assert not TP.flat_pace(th_of(ONE)) and not TP.flat_pace(th_of(THREE, tpace=None))
    assert not TP.flat_pace({"cp": None, "lthr": 165.0, "tpace": TPACE})        # an old caller: its old text
    assert TP.flat_pace(th_of(ONE), PP.Prefs(target_basis="pace"))


def test_the_pace_band_and_its_words():
    a, b = TP.pace_band(0.88, 0.95, TPACE)
    assert round(a) == 421 and round(b) == 455                    # 400 ÷ 0.95, 400 ÷ 0.88
    assert TP.pace_text(0.88, 0.95, TPACE) == "配速 7:01–7:35 /km（105–114% 閾值配速）"
    assert TP.target_text("配速 7:01–7:35 /km（105–114% 閾值配速） · 心率 ≤ 165 bpm", "pace") == \
        "配速 7:01–7:35 /km（105–114% 閾值配速） · 心率 ≤ 165 bpm"


# ---- 2. one step (workout_steps.resolve) -----------------------------------------------------------

BAND = {"kind": "work", "dur": {"type": "time", "value": 600},
        "target": {"type": "auto", "intent": "band", "lo": 0.88, "hi": 0.95, "cls": "Z3sub"}}


def test_an_auto_band_by_pace_shows_the_prescription_and_pushes_the_watch_band():
    c = WS.Ctx.of(th_of(THREE), "pace", True)
    r = WS.resolve(BAND, c)
    assert r.type == "pace" and r.text == "7:01–7:35 /km"
    assert r.sub.startswith("105–114% 閾值配速 · 心率 ≤ 165 bpm")       # Z3sub: AeT–LTHR, its top the cap
    # the watch: zones.watch_band (upper × 1.05 → faster bound 400 ÷ 0.9975)
    assert r.intensity == ("pace", 401, 455) and "手錶 6:41–7:35 /km" in r.sub
    assert r.frac == pytest.approx(0.915)


def test_an_auto_band_by_pace_falls_back_on_a_hill_or_without_threshold_pace():
    hill = WS.Ctx.of(th_of(THREE), "pace", True, hill=True)
    assert WS.resolve(BAND, hill).type == "hr"
    no = WS.Ctx.of(th_of(THREE, tpace=None), "pace", True)
    r = WS.resolve(BAND, no)
    assert r.type == "hr" and r.warn == "沒有閾值配速：改用心率＋RPE"


def test_hr_without_lthr_goes_to_pace_not_to_an_old_cp():
    c = WS.Ctx.of({"cp": None, "tpace": TPACE}, "hr")
    r = WS.resolve(BAND, c)
    assert r.type == "pace" and r.warn == "沒有 LTHR：改用配速"
    assert WS.resolve(BAND, WS.Ctx.of({"cp": None}, "hr")).type == "none"
    # ① (a CP in effect) keeps the power fallback
    assert WS.resolve(BAND, WS.Ctx.of({"cp": 260.0}, "hr")).type == "power"


def test_a_power_step_without_cp_becomes_pace():
    st = {"kind": "work", "dur": {"type": "time", "value": 300},
          "target": {"type": "power", "mode": "pct", "lo": 1.0, "hi": 1.05}}
    r = WS.resolve(st, WS.Ctx.of(th_of(THREE), "pace"))
    assert r.type == "pace" and not r.err and r.intensity == ("pace", 381, 400) and "% 閾值配速" in r.warn
    # on a hill (or with no threshold pace) it stays the old error
    assert WS.resolve(st, WS.Ctx.of(th_of(THREE), "hr", hill=True)).err
    assert WS.resolve(st, WS.Ctx.of(th_of(THREE, tpace=None), "hr")).err
    # ①: watts as before
    assert WS.resolve(st, WS.Ctx.of(th_of(ONE), "power")).type == "power"


def test_the_mp_segment_is_unchanged():
    s = {"kind": "long", "title": "長跑＋馬拉松配速 30 分", "minutes": 120}
    d = WS.derive(s, th_of(THREE))
    mp = next(x for x in d["items"] if x.get("note") == "馬拉松配速")
    assert mp["target"]["type"] == "pace" and mp["target"]["lo"] == WS.MP_PACE[0]


# ---- 3. the texts: one basis everywhere ------------------------------------------------------------

def _no_power(text):
    return not re.search(r"\d\s*W\b|% CP", text or "")


def _session(v_key, th, prefs=None):
    v = IL.get(v_key)
    f = {"variant": v, "level": "std", "reps": None, "equiv": True, "reason": "t", "rung": v.rung}
    return IL.session_for(f, th, prefs=prefs)


FLAT_KEY = next(k for k, v in IL.ALL.items() if v.terrain != "hill" and v.cls == "Z3sub")
HILL_KEY = next(k for k, v in IL.ALL.items() if v.terrain == "hill")
Z5_SHORT = next(k for k, v in IL.ALL.items() if v.terrain != "hill" and v.cls == "Z5" and min(v.works) < 180)


@pytest.mark.parametrize("ps", [TWO, THREE], ids=["2", "3"])
def test_the_library_writes_pace_with_the_hr_cap(ps):
    th = th_of(ps)
    s = _session(FLAT_KEY, th)
    assert s["target"].startswith("配速 ") and " · 心率 ≤ " in s["target"] and _no_power(s["target"])
    assert "閾值配速" in s["detail"]                       # the main set's band in the 說明 too
    # the card (the structure's summary) says the same
    f = SST.figures(s, {**th, "tpace": TPACE})
    assert f["summary"] == s["target"]
    # a short Zone 5 rep: pace, no HR cap (HR has not settled)
    z5 = _session(Z5_SHORT, th)
    assert z5["target"].startswith("配速 ") and "心率" not in z5["target"]
    # a hill: HR + RPE, no % CP
    h = _session(HILL_KEY, th)
    assert "配速" not in h["target"] and "RPE 8" in h["target"] and _no_power(h["target"])


def test_the_library_in_one_is_unchanged_and_pace_picked_writes_pace():
    s = _session(FLAT_KEY, th_of(ONE))
    assert s["target"].startswith("功率 ") and "W" in s["target"]
    p = _session(FLAT_KEY, th_of(ONE), PP.Prefs(target_basis="pace"))
    assert p["target"].startswith("配速 ")


def test_no_threshold_pace_the_library_writes_hr_and_rpe_without_cp():
    s = _session(FLAT_KEY, th_of(THREE, tpace=None))
    assert "配速" not in s["target"] and "心率" in s["target"] and "RPE 8" in s["target"] and _no_power(s["target"])


def test_the_gate_sessions_write_pace():
    spec = next(x for x in QG.LADDER if x[0] not in ("z3",) and not x[7])         # a flat dose step
    s = QG.session(spec, th_of(THREE))
    assert s["target"].startswith("配速 ") and _no_power(s["target"])
    up = next((x for x in QG.LADDER if x[7]), None)
    if up is not None:
        assert "配速" not in QG.session(up, th_of(THREE))["target"]
    assert QG.session(spec, th_of(ONE))["target"].startswith("功率 ")


def test_the_taper_templates_write_pace():
    from backend.engine import overview as O
    tm = dict(O.TAPER_SHORT)
    t = O.taper_q_target(tm, None, "心率 157–165 bpm", None, "", th_of(THREE))
    assert t == TP.pace_text(0.98, 1.02, TPACE) + " · 心率 ≤ 165 bpm"
    trail = dict(O.TAPER_FULL["trail"])
    assert "配速" not in O.taper_q_target(trail, None, "心率 157–165 bpm", None, "心率 140–165 bpm", th_of(THREE))
    # ①: watts as before
    assert O.taper_q_target(tm, 260.0, "心率 157–165 bpm", None, "", th_of(ONE)).startswith("功率 ")


def test_the_week_plan_session_builders_carry_the_pace_and_the_state():
    th = TP.text_th({**th_of(THREE), "hr_model": None, "power_model": None, "junk": 1})
    assert th["tpace"] == TPACE and th["power_state"]["state"] == PST.NONE and "junk" not in th


# ---- 4. the push: COROS steps ------------------------------------------------------------------------

def _work(steps):
    """The main-set reps (the strides of a warm-up are open, no target)."""
    out = []
    for x in steps:
        out += _work(x.steps) if isinstance(x, CW.Repeat) else \
            ([x] if x.kind == CW.EX_TRAIN and not x.name.startswith("快步跑") else [])
    return out


@pytest.mark.parametrize("ps", [TWO, THREE], ids=["2", "3"])
def test_coros_pushes_the_flat_reps_as_pace(ps):
    th = th_of(ps)
    s = {**_session(FLAT_KEY, th), "day": "2026-10-29", "basis": "pace"}
    steps = CW.session_steps(s, CW.Thresholds.of(th))
    work = _work(steps)
    assert work and all(w.intensity[0] == "pace" for w in work)
    prog = CW.build_program("TRC x", steps, CW.Thresholds.of(th))
    reps = [e for e in prog["exercises"] if e["exerciseType"] == CW.EX_TRAIN and not e["name"].startswith("快步跑")]
    assert reps and all(e["intensityType"] == CW.INT_PACE and e["intensityValue"] < e["intensityValueExtend"]
                        for e in reps)
    assert not any(e["intensityType"] == CW.INT_POWER for e in prog["exercises"])
    # the editor's structure pushes the same
    d = WS.derive(s, th)
    assert WS.steps_to_coros(WS.normalize(d), WS.session_ctx(s, th)) == steps


def test_coros_hill_reps_and_no_threshold_pace_go_by_hr():
    th = th_of(THREE)
    h = {**_session(HILL_KEY, th), "day": "2026-10-29", "basis": "pace"}
    assert all(w.intensity[0] == "hr" for w in _work(CW.session_steps(h, CW.Thresholds.of(th))))
    no = th_of(THREE, tpace=None)
    s = {**_session(FLAT_KEY, no), "day": "2026-10-29", "basis": "pace"}
    assert all(w.intensity[0] == "hr" for w in _work(CW.session_steps(s, CW.Thresholds.of(no))))


def test_push_dict_gives_pace_with_the_threshold_pace_and_hr_without():
    from backend.engine import plan_store as PS
    s = {"uid": "u", "week_start": "2026-10-26", "kind": "quality", "title": "有氧間歇 3×10 分", "minutes": 60,
         "target": "", "state": "active", "day": "2026-10-28"}
    assert PS.push_dict(s, None, th_of(THREE))["basis"] == "pace"
    assert PS.push_dict(s, None, th_of(TWO))["basis"] == "pace"
    assert PS.push_dict(s, None, th_of(THREE, tpace=None))["basis"] == "hr"
    assert PS.push_dict(s, None, th_of(ONE))["basis"] == "power"


def test_the_text_path_quality_session_pushes_pace():
    th = th_of(THREE)
    s = {"kind": "quality", "title": "短強度 2×3 分（平路）", "minutes": 35, "day": "2026-10-29",
         "detail": "賽前 3–5 天最後一次短強度：量減半，98–102% CP，休 2 分慢跑；暖身 15 分、緩和 10 分",
         "target": TP.pace_text(0.98, 1.02, TPACE) + " · 心率 ≤ 165 bpm", "basis": "pace"}
    steps = CW.session_steps(s, CW.Thresholds.of(th))
    assert [w.intensity[0] for w in _work(steps)] == ["pace"]
    d = WS.derive(s, th)
    assert WS.steps_to_coros(WS.normalize(d), WS.session_ctx(s, th)) == steps
    # an old HR-only text keeps its HR (the user's / the old builder's)
    old = {**s, "target": "心率 157–165 bpm"}
    assert [w.intensity[0] for w in _work(CW.session_steps(old, CW.Thresholds.of(th)))] == ["hr"]


def test_golden_pace_derived_steps_push_like_the_text():
    from backend.tests.test_workout_steps import SESSIONS
    for th in ({"cp": None, "lthr": 168.0, "aet": 150.0, "tpace": TPACE}, {"cp": None, "tpace": TPACE}):
        t = CW.Thresholds.of(th)
        for s in SESSIONS:
            s = {**s, "basis": "pace", "day": "2026-10-29"}
            try:
                legacy = CW.session_steps(s, t)
            except CW.Unsupported:
                continue
            d = WS.derive(s, th)
            c = WS.Ctx.of(th, "pace", hill=TP.session_type(s) == "hill")
            assert WS.steps_to_coros(WS.normalize(d), c) == legacy, (s.get("title"), s.get("variant_key"))


# ---- 4b. no % CP / watts anywhere a ②/③ session shows (owner: nothing about power without power) ----

POWER_WORDS = re.compile(r"% CP|\d\s*W\b")


def _shown(s: dict, th: dict) -> list:
    """What a session shows: title, 目標, 說明, 出處, and its structure's step names (the editor)."""
    out = [s.get(k) or "" for k in ("title", "target", "detail", "source")]
    try:
        d = WS.derive(s, th)
    except Exception:                                   # noqa: BLE001
        d = None
    for r in WS.flat((d or {}).get("items") or []):
        out.append(r["st"].get("note") or "")
    return out


def _offending(sessions, th):
    return [(s.get("title"), t) for s in sessions if s.get("kind") != "test"
            for t in _shown(s, th) if POWER_WORDS.search(t)]


NO_POWER_THS = {"3": th_of(THREE), "3-no-tpace": th_of(THREE, tpace=None), "2": th_of(TWO),
                "2-no-tpace": th_of(TWO, tpace=None)}


@pytest.mark.parametrize("name", list(NO_POWER_THS))
def test_no_session_builder_shows_power_without_power(name):
    from backend.engine import overview as O
    th = {**NO_POWER_THS[name], "hr_model": None}
    ss = []
    for v in IL.ALL.values():
        for lv in IL.LEVELS:
            f = {"variant": v, "level": lv, "reps": None, "equiv": True, "reason": "t", "rung": v.rung}
            ss.append(IL.session_for(f, th))
    ss += [QG.session(spec, th) for spec in list(QG.LADDER) + [QG.RECOVERY, QG.SUB]]
    # the 減量期 templates as the taper rules write them (full / cut / short, trail and road)
    for day, monday, road in (("2026-10-06", dt.date(2026, 10, 5), False), ("2026-10-09", dt.date(2026, 10, 5), True),
                              ("2026-10-13", dt.date(2026, 10, 12), False)):
        x = [{"id": "quality", "kind": "quality", "day": day, "minutes": 50, "title": "x", "tss": 50.0,
              "done": False, "detail": "", "source": "", "terrain": None}]
        tc = {"race": "r", "id": "e", "start": "2026-10-17", "taper_start": "2026-10-03", "days": 14,
              "trail": not road, "sore": False, "long_days": 7}
        O.taper_rules(x, tc, monday, road=road, q_target="心率 160–168 bpm", z3_target="心率 150–159 bpm", th=th)
        ss += x
    assert not _offending(ss, th)
    # ① keeps its watts (the same builders, power in effect)
    one = {**th_of(ONE), "hr_model": None}
    f = {"variant": IL.get(FLAT_KEY), "level": "std", "reps": None, "equiv": True, "reason": "t",
         "rung": IL.get(FLAT_KEY).rung}
    assert _offending([IL.session_for(f, one)], one)


@pytest.mark.parametrize("profile,cp_day,tpace", [({"power_source": "none"}, "2026-08-01", TPACE),
                                                  ({"power_source": "none"}, "2026-08-01", None),
                                                  ({"power_source": "stryd"}, "2026-08-01", TPACE)],
                         ids=["3", "3-no-tpace", "2"])
def test_no_week_plan_session_shows_power_without_power(profile, cp_day, tpace):
    wp = _week(profile, cp_day, tpace)
    th = wp["thresholds"]
    assert th["power_state"]["state"] != PST.POWER
    assert not _offending(wp["sessions"], th)


# ---- 5. the week plan (synthetic week: backend/tests/test_aet_weekday) ------------------------------

def _week(profile, cp_day, tpace):
    from backend.engine import overview as O
    from backend.engine.status import Status
    from backend.tests.test_aet_weekday import TODAY, _build_week_ds
    ds, plan = _build_week_ds()
    if cp_day:
        for t in plan.thresholds:
            if t.cp is not None:
                t.date = cp_day
    plan.profile = profile
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return O.week_plan(ds, st, TODAY, prefs=PP.Prefs(), tpace=tpace)


@pytest.mark.parametrize("profile,want", [({"power_source": "none"}, PST.NONE),
                                          ({"power_source": "stryd"}, PST.STALE)], ids=["3", "2"])
def test_week_plan_flat_interval_by_pace_and_nothing_by_power(profile, want):
    wp = _week(profile, "2026-08-01", TPACE)
    assert wp["thresholds"]["power_state"]["state"] == want
    q = [s for s in wp["sessions"] if s["kind"] == "quality"]
    assert q and all(s["target"].startswith("配速 ") and "心率 ≤ " in s["target"] for s in q)
    assert all(_no_power(s["target"]) for s in wp["sessions"])
    easy = [s for s in wp["sessions"] if s["kind"] in ("easy", "long")]
    assert easy and all("心率" in s["target"] and "配速" not in s["target"] for s in easy)


def test_week_plan_power_in_effect_is_unchanged_and_no_threshold_pace_is_hr():
    wp = _week({"power_source": "stryd"}, None, TPACE)
    assert wp["thresholds"]["power_state"]["state"] == PST.POWER
    q = [s for s in wp["sessions"] if s["kind"] == "quality"]
    assert q and all(s["target"].startswith("功率 ") for s in q)
    wp3 = _week({"power_source": "none"}, "2026-08-01", None)
    q3 = [s for s in wp3["sessions"] if s["kind"] == "quality"]
    assert q3 and all("配速" not in s["target"] and "心率" in s["target"] and _no_power(s["target"]) for s in q3)
