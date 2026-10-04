"""
engine/workout_steps.py: the structured steps model. Golden: every kind of session
the plan makes, derived into steps and pushed from them, gives exactly the COROS
steps (and payload) of the old text path (sync/coros_workouts.session_steps).
No DB, no WKO5 folder, no network.
"""
import copy

import pytest

from backend.engine import aet_test as AT
from backend.engine import cp_protocols as CPP
from backend.engine import interval_library as IL
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW

FULL = {"cp": 250.0, "lthr": 168.0, "aet": 150.0}
CP_ONLY = {"cp": 250.0}


def _sessions():
    out = []
    for v in list(IL.ALL.values()):
        for lv in IL.LEVELS:
            f = {"variant": v, "level": lv, "reps": None, "equiv": True, "reason": "t", "rung": v.rung}
            s = IL.session_for(f, FULL)
            out.append({**s, "day": "2026-10-07"})
    # a reduced variant (fewer reps) and a tweaked one
    out.append({"kind": "quality", "title": "x", "minutes": 50, "variant_key": "v3a", "variant_reps": 4,
                "variant_blocks": "min", "variant_adj": {"rest_add": 1}, "target": ""})
    out += [
        {"kind": "quality", "title": "閾值 3×10 分", "minutes": 70, "detail": "暖身 15 分、休 3 分、緩和 10 分",
         "target": "功率 220–235 W · 心率 150–160 bpm"},
        {"kind": "quality", "title": "閾值 4×8 分", "minutes": 60, "detail": "休 2 分", "target": "心率 150–160 bpm"},
        {"kind": "quality", "title": "爬坡 6×3 分", "minutes": 55, "detail": "95–101% CP 休 2 分", "target": ""},
        {"kind": "long", "title": "LSD", "minutes": 120},
        {"kind": "mountain", "title": "山路", "minutes": 180},
        {"kind": "hike", "title": "健行", "minutes": 240},
        {"kind": "easy", "title": "輕鬆跑", "minutes": 45},
        {"kind": "easy", "title": "輕鬆跑＋熱適應", "minutes": 50},
        {"kind": "easy", "title": "輕鬆跑＋6×20 秒衝刺", "minutes": 50},
        {"kind": "notice", "title": "課表待確認", "minutes": 1},
    ]
    for p in ("quick", "standard"):
        out.append(CPP.session_for(p))
    out.append({"kind": "test", "title": "CP 測試 3 分 + 12 分", "minutes": 60, "detail": "暖身 20 分", "target": "休 25 分"})
    for p in AT.PROTOCOLS:
        out.append(AT.session(FULL, AT.start_hr(None, FULL["lthr"]), AT.start_power(FULL["cp"]), None, p, None))
    return out


SESSIONS = _sessions()


@pytest.mark.parametrize("th", [FULL, CP_ONLY], ids=["full", "cp_only"])
@pytest.mark.parametrize("basis", ["power", "hr", "none"])
def test_golden_derived_steps_push_like_the_text(th, basis):
    t = CW.Thresholds.of(th)
    n = 0
    for s in SESSIONS:
        s = {**s, "basis": basis}
        legacy = CW.session_steps(s, t)
        d = WS.derive(s, th)
        assert d is not None, s.get("title")
        got = WS.steps_to_coros(WS.normalize(d), WS.Ctx.of(th, basis))
        assert got == legacy, (s.get("title"), s.get("variant_key"), basis)
        # the push path: the same session with the steps stored
        if s["kind"] != "notice":
            pushed = CW.session_steps({**s, "steps": d}, t)
            assert pushed == legacy
            a = CW.build_program("TRC x", legacy, t)
            b = CW.build_program("TRC x", pushed, t)
            assert a == b
        n += 1
    assert n > 90


def test_derive_none_for_not_pushed():
    for k in ("race", "rest", "strength", "heat_passive"):
        assert WS.derive({"kind": k, "minutes": 40}) is None
    assert WS.derive({"kind": "quality", "title": "隨便跑", "minutes": 40}) is None


def test_variant_derives_a_repeat_with_no_rest_after_the_last():
    v = IL.get("v1a")
    d = WS.from_variant(v, "std")
    reps = [x for x in d["items"] if x["kind"] == "repeat" and x["times"] == 5]
    assert reps and reps[0]["last_rest"] is False
    work = reps[0]["items"][0]
    assert work["target"] == {"type": "auto", "intent": "band", "lo": 1.06, "hi": 1.12, "cls": "Z5"}
    assert d["origin"] == "template:v1a"


def test_normalize_round_trip_and_errors():
    d = WS.derive({"kind": "quality", "title": "x", "minutes": 50, "variant_key": "t2a"}, FULL)
    n1 = WS.normalize(d)
    assert WS.normalize(copy.deepcopy(n1)) == n1
    import json
    assert WS.normalize(json.dumps(n1)) == n1
    with pytest.raises(WS.StepsError) as e:
        WS.normalize({"items": [{"kind": "jump", "dur": {"type": "time", "value": 60}}]})
    assert "步驟類型" in str(e.value)
    with pytest.raises(WS.StepsError):
        WS.normalize({"items": []})
    with pytest.raises(WS.StepsError):
        WS.normalize({"items": [{"kind": "repeat", "times": 3, "items": [
            {"kind": "repeat", "times": 2, "items": [
                {"kind": "repeat", "times": 2, "items": [{"kind": "work", "dur": {"type": "time", "value": 60}}]}]}]}]})
    # duplicate ids are renumbered
    n = WS.normalize({"items": [{"id": "a", "kind": "work", "dur": {"type": "time", "value": 60}},
                                {"id": "a", "kind": "rest", "dur": {"type": "time", "value": 60}}]})
    assert n["items"][0]["id"] != n["items"][1]["id"]


def _one(target, kind="work", sec=300):
    return {"v": 1, "origin": "user", "items": [{"id": "a", "kind": kind, "dur": {"type": "time", "value": sec},
                                                   "target": target, "note": ""}]}


def test_resolve_overrides():
    c = WS.Ctx.of({**FULL, "tpace": 280}, "hr")
    r = WS.resolve(_one({"type": "power", "mode": "pct", "lo": 1.06, "hi": 1.12})["items"][0], c)
    assert (r.type, r.lo, r.hi, r.auto) == ("power", 265, 280, False)
    assert "106–112% CP" in r.sub
    r = WS.resolve(_one({"type": "power", "mode": "zone", "zone": "3B"})["items"][0], c)
    assert (r.lo, r.hi) == (round(0.95 * 250), round(1.01 * 250))
    r = WS.resolve(_one({"type": "hr", "mode": "zone", "zone": "aet"})["items"][0], c)
    assert (r.type, r.hi) == ("hr", 150)
    r = WS.resolve(_one({"type": "hr", "mode": "abs", "lo": 150, "hi": 160})["items"][0], c)
    assert (r.lo, r.hi, r.intensity) == (150, 160, ("hr", 150, 160))
    # hand-entered HR above 110 % LTHR is taken as entered (SP-33); lo > hi is still an error
    lt = WS.Ctx.of({**FULL, "lthr": 155}, "hr")
    for tg in ({"type": "hr", "mode": "abs", "lo": 165, "hi": 175}, {"type": "hr", "mode": "pct", "lo": 1.08, "hi": 1.15}):
        d = WS.normalize(_one(tg))
        assert not WS.resolve(d["items"][0], lt).err
        assert not [i for i in WS.issues(d, lt) if i["level"] == "err"]
    r = WS.resolve(_one({"type": "hr", "mode": "abs", "lo": 175, "hi": 165})["items"][0], lt)
    assert r.err == "下限比上限高"
    # hand-entered power outside 40–200 % CP is taken as entered too; lo > hi still errors
    for tg in ({"type": "power", "mode": "abs", "lo": 60, "hi": 80}, {"type": "power", "mode": "abs", "lo": 520, "hi": 560},
               {"type": "power", "mode": "pct", "lo": 2.1, "hi": 2.4}):
        d = WS.normalize(_one(tg))
        assert not WS.resolve(d["items"][0], c).err
        assert not [i for i in WS.issues(d, c) if i["level"] == "err"]
    r = WS.resolve(_one({"type": "power", "mode": "abs", "lo": 300, "hi": 280})["items"][0], c)
    assert r.err == "下限比上限高"
    r = WS.resolve(_one({"type": "pace", "mode": "zone", "zone": "4"})["items"][0], c)
    assert r.type == "pace" and r.intensity == ("pace", round(r.lo), round(r.hi)) and not r.warn
    assert r.text == "4:40–4:57 /km"


def test_pace_targets_push_as_intensity_type_3_seconds_per_km():
    # verified on a COROS watch 2026-10-02: value 270 / extend 285, displayUnit 1 → 4'30"–4'45"/km
    c = WS.Ctx.of({**FULL, "tpace": 280}, "hr")
    st = _one({"type": "pace", "mode": "abs", "lo": 285, "hi": 270})
    prog = CW.build_program("TRC p", WS.steps_to_coros(WS.normalize(st), c), CW.Thresholds.of(FULL))
    ex = prog["exercises"][0]
    assert (ex["intensityType"], ex["intensityValue"], ex["intensityValueExtend"], ex["intensityDisplayUnit"]) == (3, 270, 285, 1)
    assert ex["name"] == "第 1 趟 5 分"                       # no 「配速 …」 name workaround any more
    pv = WS.watch_preview(WS.normalize(st), c)
    assert pv["lines"][0]["target"] == "配速 4:30–4:45 /km" and not any("配速" in x for x in pv["lost"])
    # pct × threshold pace (a Daniels T template) reaches the payload too, through session_steps
    s = {"kind": "quality", "title": "T", "minutes": 30, "day": "2026-10-07",
         "steps": _one({"type": "pace", "mode": "pct", "lo": 0.99, "hi": 1.01})}
    steps = CW.session_steps(s, CW.Thresholds.of({**FULL, "tpace": 280}))
    assert steps[0].intensity == ("pace", round(0.99 * 280), round(1.01 * 280))
    assert CW.step_lines(steps)[0].endswith("配速 4:37–4:43 /km")
    # without a threshold pace: no target (never a made-up number)
    assert CW.session_steps(s, CW.Thresholds.of(FULL))[0].intensity is None


def test_no_threshold_pace_warns_on_pct_and_zone_pace_steps_only():
    c = WS.Ctx.of(FULL, "hr")                                        # no tpace
    for tg in ({"type": "pace", "mode": "pct", "lo": 0.99, "hi": 1.01}, {"type": "pace", "mode": "zone", "zone": "4"}):
        r = WS.resolve(_one(tg)["items"][0], c)
        assert r.type == "none" and not r.err and r.need == "tpace" and r.warn == WS.NO_TPACE
        st = WS.normalize(_one(tg))
        assert WS.needs_tpace(st)
        assert WS.NO_TPACE in WS.watch_preview(st, c)["lost"]
        assert any(i["level"] == "warn" and i["text"] == WS.NO_TPACE for i in WS.issues(st, c))
    ab = WS.normalize(_one({"type": "pace", "mode": "abs", "lo": 270, "hi": 285}))
    assert not WS.needs_tpace(ab) and WS.resolve(ab["items"][0], c).need == ""
    assert WS.NO_TPACE not in WS.watch_preview(ab, c)["lost"]
    # the template list flags the Daniels / Canova / Billat rows (their pace is × threshold pace)
    T = WS.templates()
    rows = [r for g in T["groups"] for r in g["rows"]]
    flagged = {r["key"] for r in rows if r["needs_tpace"]}
    assert flagged and T["no_tpace_text"] == WS.NO_TPACE
    assert all(not r["needs_tpace"] for r in rows if r["key"] in ("strides", "hill_sprints"))


def test_push_preview_note_and_the_link_to_threshold_pace():
    from backend.api import plan_sessions as API
    steps = _one({"type": "pace", "mode": "pct", "lo": 0.99, "hi": 1.01})
    assert API.pace_note({"steps": steps}, FULL) == WS.NO_TPACE
    assert API.pace_note({"steps": steps}, {**FULL, "tpace": 280}) is None
    assert API.pace_note({"steps": _one({"type": "pace", "mode": "abs", "lo": 270, "hi": 285})}, FULL) is None
    assert API.pace_note({}, FULL) is None
    link = API.tpace_link()
    assert link.startswith("/api/v1/static/wko5_viewer.html?view=") and "chart=" in link
    # no CP: a power override is an error; an auto band falls back to HR with the reason
    nc = WS.Ctx.of({"lthr": 168, "aet": 150}, "power")
    r = WS.resolve(_one({"type": "power", "mode": "pct", "lo": 1.0, "hi": 1.05})["items"][0], nc)
    assert r.err == "選了功率卻沒有 CP"
    r = WS.resolve(_one({"type": "auto", "intent": "band", "lo": 1.06, "hi": 1.12, "cls": "Z5"})["items"][0], nc)
    assert r.type == "hr" and "沒有 CP" in r.warn


def test_basis_switches_auto_steps_only():
    d = WS.normalize(WS.derive({"kind": "quality", "title": "x", "minutes": 50, "variant_key": "v1a"}, FULL))
    work = WS.flat(d["items"])
    w = next(x["st"] for x in work if x["st"]["kind"] == "work" and x["st"]["target"].get("intent") == "band")
    assert WS.resolve(w, WS.Ctx.of(FULL, "power")).type == "power"
    assert WS.resolve(w, WS.Ctx.of(FULL, "hr")).type == "hr"
    w["target"] = {"type": "power", "mode": "pct", "lo": 1.0, "hi": 1.05}       # override one step
    assert WS.resolve(w, WS.Ctx.of(FULL, "hr")).type == "power"


def test_issues_z5_rep_cap_and_coros():
    c = WS.Ctx.of(FULL, "power")
    d = WS.normalize({"items": [
        {"kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "auto", "intent": "easy"}},
        {"kind": "repeat", "times": 6, "items": [
            {"kind": "work", "dur": {"type": "time", "value": 90},
             "target": {"type": "auto", "intent": "band", "lo": 1.06, "hi": 1.12, "cls": "Z5"}},
            {"kind": "rest", "dur": {"type": "time", "value": 240}, "target": {"type": "none"}}]},
        {"kind": "cool", "dur": {"type": "open"}, "target": {"type": "auto", "intent": "easy"}}]})
    iss = WS.issues(d, c, cap=40, cap_mode="hard", rung="z5a")
    texts = [i["text"] for i in iss if i["level"] == "err"]
    assert any("5 區每趟至少 2 分鐘" in t for t in texts)
    assert any("超過這天上限 40 分" in t for t in texts)
    assert any("Buchheit" in i["text"] for i in iss if i["level"] == "warn")
    assert any("直到按下計圈" in i["text"] for i in iss if i["level"] == "info")
    assert any("不等效" in i["text"] for i in iss if i["level"] == "info")
    soft = WS.issues(d, c, cap=40, cap_mode="soft")
    assert any(i["level"] == "warn" and "軟上限" in i["text"] for i in soft)


def test_totals_and_tss():
    c = WS.Ctx.of(FULL, "power")
    d = WS.normalize(_one({"type": "power", "mode": "pct", "lo": 1.0, "hi": 1.0}, sec=3600))
    t = WS.totals(d, c)
    assert t["sec"] == 3600 and t["tss"] == pytest.approx(100.0)


def test_repeat_group_and_unrolled_push():
    c = WS.Ctx.of(FULL, "power")
    work = {"kind": "work", "dur": {"type": "time", "value": 180},
            "target": {"type": "power", "mode": "pct", "lo": 1.05, "hi": 1.1}}
    rest = {"kind": "rest", "dur": {"type": "time", "value": 120}, "target": {"type": "none"}}
    g = WS.steps_to_coros(WS.normalize({"items": [{"kind": "repeat", "times": 4, "items": [work, rest]}]}), c)
    assert len(g) == 1 and isinstance(g[0], CW.Repeat) and g[0].sets == 4
    u = WS.steps_to_coros(WS.normalize({"items": [{"kind": "repeat", "times": 4, "last_rest": False,
                                                   "items": [work, rest]}]}), c)
    assert len(u) == 7 and [s.name for s in u if s.kind == CW.EX_TRAIN][:2] == ["第 1 趟 3 分", "第 2 趟 3 分"]
    nested = WS.normalize({"items": [{"kind": "repeat", "times": 2, "items": [
        {"kind": "repeat", "times": 3, "items": [work, rest]}, rest]}]})
    out = WS.steps_to_coros(nested, c)
    assert [type(x).__name__ for x in out] == ["Repeat", "Step", "Repeat", "Step"]
    w = WS.watch_preview(nested, c)
    assert any("攤平" in x for x in w["lost"])
    assert [l["key"] for l in w["limits"]] == ["watts", "one", "ramp"]
    assert w["limits"][0]["hit"]


def test_distance_step_payload():
    c = WS.Ctx.of(FULL, "hr")
    d = WS.normalize({"items": [{"kind": "work", "dur": {"type": "distance", "value": 1000},
                                 "target": {"type": "auto", "intent": "easy"}}]})
    prog = CW.build_program("x", WS.steps_to_coros(d, c), CW.Thresholds.of(FULL))
    ex = prog["exercises"][0]
    assert ex["targetType"] == 5 and ex["targetValue"] == 100000


def test_variant_from_steps_equivalence():
    canon = WS.normalize(WS.template_steps("v1a"))
    eq = WS.equivalence(canon, "z5a")
    assert eq["ok"], eq["why"]
    # 6×2′ instead of 5×2′ = +20 % time in zone: not equivalent
    d = copy.deepcopy(canon)
    for it in d["items"]:
        if it["kind"] == "repeat" and it["times"] == 5:
            it["times"] = 6
    eq = WS.equivalence(d, "z5a")
    assert not eq["ok"] and any("15%" in w for w in eq["why"])
    # HR-only: class estimated (推估) through Friel ↔ Palladino: 5b (103–106 % LTHR) = Zone 5,
    # 5a (100–103 %) = Zone 4 supra-threshold (Palladino everywhere, owner 2026-10-02)
    def hr_reps(lo, hi):
        return WS.variant_from_steps(WS.normalize({"items": [
            {"kind": "repeat", "times": 5, "items": [
                {"kind": "work", "dur": {"type": "time", "value": 120}, "target": {"type": "hr", "mode": "pct", "lo": lo, "hi": hi}},
                {"kind": "rest", "dur": {"type": "time", "value": 120}, "target": {"type": "none"}}]}]}), "z5a")
    v = hr_reps(1.03, 1.06)
    assert v.cls == "Z5" and v.src_kind == "推估"
    assert hr_reps(1.0, 1.04).cls == "Z4"


def test_load_main_set_counts_on_the_ladder():
    """SP-38 (owner 2026-10-04): a main set ended by 「負荷」 counts toward the ladder through
    its estimated time — TSS ÷ (IF² × 100) at the band's middle."""
    canon = WS.normalize(WS.template_steps("v1a"))

    def as_load(scale):
        d = copy.deepcopy(canon)
        for row in WS.flat(d["items"]):
            st = row["st"]
            if st["kind"] == "work" and st["dur"]["type"] == "time":
                lo, hi, _e = WS._work_band(st, None)
                f = (lo + hi) / 2
                st["dur"] = {"type": "load", "value": round(st["dur"]["value"] * scale * f * f * 100 / 3600, 1)}
        return WS.normalize(d)
    d = as_load(1.0)
    v = WS.variant_from_steps(d, "z5a")
    assert v is not None and v.src_kind == "推估" and abs(v.works[0] - 120) <= 2
    eq = WS.equivalence(d, "z5a")
    assert eq["ok"], eq["why"]
    assert "負荷" in eq["text"]
    from backend.engine import quality_gate as QG                # the dose history counts it
    h = {"steps": {**d, "origin": "user"}, "rung_key": "z5a"}
    assert QG.track_spec("z5", 0)[0] == "z5a"
    assert QG.steps_spec(h, 0, "z5")[2] is True and h["steps_estimated"]
    eq = WS.equivalence(as_load(1.5), "z5a")                     # 1.5× the time in zone: not the rung
    assert not eq["ok"] and any("15%" in w for w in eq["why"])


def test_rescale_abs_power():
    d = WS.normalize({"items": [
        {"kind": "work", "dur": {"type": "time", "value": 600}, "target": {"type": "power", "mode": "abs", "lo": 200, "hi": 210}},
        {"kind": "work", "dur": {"type": "time", "value": 600}, "target": {"type": "power", "mode": "pct", "lo": 0.9, "hi": 0.95}}]})
    r = WS.rescale_abs_power(d, 200, 210)
    assert r["items"][0]["target"]["lo"] == 210 and r["items"][0]["target"]["hi"] == 220
    assert r["items"][1]["target"] == d["items"][1]["target"]
    assert d["items"][0]["target"]["lo"] == 200                   # not mutated
    assert WS.rescale_abs_power(r["items"] and {"items": [d["items"][1]]}, 200, 210) == {"items": [d["items"][1]]}


def test_templates_and_zones():
    gs = WS.templates()["groups"]
    keys = {r["key"] for g in gs for r in g["rows"]}
    assert {"v1a", "t1a", "x3015", "cp_quick", "strides"} <= keys
    for g in gs:
        for r in g["rows"]:
            WS.normalize({"items": r["items"]})
    z = WS.zones_table(WS.Ctx.of({**FULL, "tpace": 280}))
    assert z["hr"][0]["id"] == "aet" and z["power"][0]["text"].endswith("W")


def test_steps_text_summary():
    d = WS.normalize(WS.template_steps("t2a"))
    assert WS.steps_text(d, WS.Ctx.of(FULL, "power")) == "功率 225–238 W（90–95% CP）"
