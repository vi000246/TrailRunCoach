"""SP-590: 個人化間歇 — under 個人化區間 (only) an interval at or above threshold takes its level of the
model's optimized intervals: the rep duration and power from the runner's PD model, the reps and
rests from the coaches' suggestions (the seminar's VO2max 3–8 reps / 12–30 min / 1:1; the other
levels common coaching ratios). Threshold → level 0, supra → 1, VO2max → 2, anaerobic → 3–4, sprint
→ 5 (the user 2026-10-15, Q1-1..5). The 課表 text, the structure, the push and the judging follow it;
every other 課表功率區間 gives exactly the sessions it gave before. Synthetic only."""
from __future__ import annotations

import copy
import math

import pytest

from backend.engine import interval_library as IL
from backend.engine import power_zone_plan as PZP
from backend.engine import zones as Z

PARAMS = [15000.0, 20.0, 250.0, 25.0, 2400.0, -30.0]      # FRC J, tau1, FTP, tau2, TTE, D
TTE = 2100.0
CP = 260.0
TH = {"cp": CP, "lthr": 165.0, "aet": 140.0}

pytestmark = pytest.mark.needs_core


def personal_pm(cp: float = CP) -> dict:
    from backend.engine.algorithms.wko5_levels import levels
    lv = levels(PARAMS)
    rows = [[str(i + 1), n, lo / cp, None if hi != hi else hi / cp] for i, ((lo, hi), n) in
            enumerate(zip(lv, PZP.PERSONAL_NAMES))]
    return {"system": "personal", "setting": "personal", "fallback": "", "levels": rows,
            "scale": PARAMS[2] / cp, "pd": {"params": PARAMS, "tte": TTE}}


def pm(system: str) -> dict:
    return {"system": system, "setting": system, "fallback": "", "levels": PZP.static_levels(system)}


# ---------------------------------------------------------------------------
# the core: reps, rests and the level-2 power
# ---------------------------------------------------------------------------

def test_the_plans_reps_and_rests_follow_the_suggestions():
    from backend.engine.algorithms import wko5_levels as L
    from backend.engine.algorithms.wko5_pdmodel import model
    # level 2 (the seminar): 3–8 reps, 12–30 min of work in all, rest 1:1
    for ref in (0, 600, 900, 1500, 3600):
        pr = L.prescription(2, PARAMS, TTE, ref)
        assert 3 <= pr["reps"] <= 8 and pr["rest_s"] == pytest.approx(pr["work_s"], abs=15)
        assert pr["reps"] * pr["work_s"] <= 30 * 60 or pr["reps"] == 3
    d2 = L.target_duration(2, PARAMS, TTE)
    pr = L.prescription(2, PARAMS, TTE, 900)
    assert pr["power"] == pytest.approx(model(PARAMS, d2))                 # Q1-2 (b): the model's, not 1.20 FTP
    assert pr["power"] != pytest.approx(1.2 * PARAMS[2], abs=0.5) or model(PARAMS, d2) == pytest.approx(300, abs=0.5)
    assert L.target_power(2, PARAMS, TTE) == pytest.approx(1.2 * PARAMS[2])   # the expressions keep the formula
    # the rep is the level's duration (rounded for a watch); the others' power as the formula
    for i in range(6):
        pr = L.prescription(i, PARAMS, TTE, 900)
        r_lo, r_hi, _a, _b, ratio = L.PRESCRIPTION[i]
        assert r_lo <= pr["reps"] <= r_hi
        assert pr["work_s"] == pytest.approx(L.target_duration(i, PARAMS, TTE), rel=0.05, abs=1.0)
        if pr["reps"] > 1:
            assert pr["rest_s"] >= ratio * pr["work_s"] * 0.9
        if i != 2:
            assert pr["power"] == pytest.approx(L.target_power(i, PARAMS, TTE))
    assert L.prescription(0, PARAMS, TTE, 900)["reps"] == 1                 # one rep of TTE at FTP
    assert L.prescription(5, PARAMS, TTE, 0)["rest_s"] >= 120
    assert L.prescription(6, PARAMS, TTE, 900) is None and L.prescription(2, None, TTE, 900) is None
    assert L.SEMINAR_LEVELS == (2,)


def test_each_kind_takes_its_level():
    """Q1-3: threshold → 0, supra → 1, VO2max → 2, anaerobic → 3 (longer reps) or 4, sprint → 5;
    below threshold (有氧間歇／巡航 88–95 %) none."""
    p = personal_pm()
    assert PZP.opt_level(0.88, 0.95, 900, p) is None
    assert PZP.opt_level(0.95, 1.01, 600, p) == 0                         # threshold (Palladino 3B)
    assert PZP.opt_level(0.97, 1.00, 420, p) == 0                         # T+
    assert PZP.opt_level(1.01, 1.06, 300, p) == 1                         # supra
    assert PZP.opt_level(1.06, 1.12, 120, p) == 2                         # VO2max
    assert PZP.opt_level(1.20, 1.40, 70, p) == 3 and PZP.opt_level(1.20, 1.40, 40, p) == 4
    assert PZP.opt_level(1.60, 2.00, 10, p) == 5


# ---------------------------------------------------------------------------
# the 課表: only under 個人化區間
# ---------------------------------------------------------------------------

def _all_fits():
    return [IL.fit(rung, cap) for rung in IL.LIBRARY for cap in (None, 45)] + \
        [IL._fit_result(IL.NON_EQUIV[0], "std", None, False, False, "x", rung="x")]


@pytest.mark.parametrize("system", ["palladino", "stryd", "classic", None])
def test_every_other_set_gives_the_same_sessions(system, monkeypatch):
    """Q1-5: the other 課表功率區間 give exactly the session they gave before (the 個人化間歇 path off)."""
    th = {**TH, "power_model": pm(system) if system else None}
    got = [IL.session_for(f, th) for f in _all_fits()]
    monkeypatch.setattr(PZP, "optimized", lambda *a, **k: None)
    want = [IL.session_for(f, th) for f in _all_fits()]
    assert got == want
    for s in got:
        assert not (s.get("variant_adj") or {}).get("opt")


def test_personal_vo2max_takes_the_models_reps():
    from backend.engine.algorithms import wko5_levels as L
    pr = L.prescription(2, PARAMS, TTE, IL.tiz_s(IL.canonical("z5a")))
    s = IL.session_for(IL.fit("z5a", None), {**TH, "power_model": personal_pm()})
    o = s["variant_adj"]["opt"]
    assert (o["level"], o["work_s"], o["reps"], o["rest_s"]) == (2, pr["work_s"], pr["reps"], pr["rest_s"])
    assert s["title"] == f"VO2max 間歇 {pr['reps']}×{IL.fmt_s(pr['work_s'])}"
    w = pr["power"]
    assert f"功率 {w * 0.97:.0f}–{w * 1.03:.0f} W" in s["target"]
    assert "個人化間歇（最大有氧（VO2max））" in s["detail"] and "3–8 趟、合計 12–30 分、工休比 1:1（教練研討會建議）" in s["detail"]
    assert s["variant_reps"] is None
    # the stored session reads back as the re-cut variant (push / structure / judging)
    v = IL.resolve(s["variant_key"], s["variant_reps"], s["variant_adj"])
    assert (v.n, v.work_s, v.rest_s, v.opt) == (pr["reps"], pr["work_s"], pr["rest_s"], 2)
    assert (v.lo * CP, v.hi * CP) == (pytest.approx(w * 0.97, abs=0.1), pytest.approx(w * 1.03, abs=0.1))


def test_personal_threshold_is_one_rep_of_the_models_duration():
    s = IL.session_for(IL.fit("tp", None), {**TH, "power_model": personal_pm()})
    o = s["variant_adj"]["opt"]
    assert o["level"] == 0 and o["reps"] == 1 and o["rest_s"] == 0 and o["work_s"] == 2100     # TTE 35 min
    assert s["title"].endswith("連續 35 分") and "不休息" in s["detail"]
    assert f"約 {PARAMS[2]:.0f} W" in s["detail"]                         # at the model's threshold power


def test_below_threshold_and_the_30_15_stay():
    p = personal_pm()
    a1 = IL.session_for(IL.fit("a1", None), {**TH, "power_model": p})
    assert not (a1.get("variant_adj") or {}).get("opt") and a1["title"] == "有氧間歇 2×15 分"
    x = IL.session_for(IL._fit_result(IL.NON_EQUIV[0], "std", None, False, False, "x", rung="x"),
                       {**TH, "power_model": p})
    assert not (x.get("variant_adj") or {}).get("opt")


def test_without_the_model_personal_is_palladino():
    """Personal fell back (no fit): plan_model is Palladino, nothing is re-cut."""
    fb = {"system": "palladino", "setting": "personal", "fallback": PZP.NO_FIT, "levels": PZP.static_levels("palladino")}
    s = IL.session_for(IL.fit("z5a", None), {**TH, "power_model": fb})
    assert s == IL.session_for(IL.fit("z5a", None), TH)
    p = personal_pm()
    p.pop("pd")
    assert PZP.optimized(1.06, 1.12, 120, 600, p, CP) is None
    assert PZP.optimized(1.06, 1.12, 120, 600, personal_pm(), None) is None


def test_the_days_cap_cuts_reps_down_to_the_levels_minimum():
    p = personal_pm()
    full = IL.session_for(IL.fit("z5d", None), {**TH, "power_model": p})
    f = IL.fit("z5d", 40)
    cut = IL.session_for(f, {**TH, "power_model": p})
    assert cut["variant_adj"]["opt"]["reps"] >= 3
    assert cut["variant_adj"]["opt"]["reps"] <= full["variant_adj"]["opt"]["reps"]
    v = IL.resolve(cut["variant_key"], None, cut["variant_adj"])
    assert IL.total_min(v, f["level"]) <= 40 + 1e-6 or v.n == 3


def test_the_state_machines_tweaks_apply_on_top():
    """A 目標太高 −5 % / rest +1 min tweak (quality_gate) applies on the model's band and rest."""
    p = personal_pm()
    f = IL.fit("z5a", None, adj={"power": 0.95, "rest_add": 1})
    s = IL.session_for(f, {**TH, "power_model": p})
    o = s["variant_adj"]["opt"]
    v = IL.resolve(s["variant_key"], s["variant_reps"], s["variant_adj"])
    assert v.rest_s == o["rest_s"] + 60 and v.lo == pytest.approx(round(o["lo"] * 0.95, 3))
    assert s["variant_adj"]["power"] == 0.95


def test_fewer_reps_of_a_stored_one_keep_its_cut():
    """overview._shorten: fewer reps of a stored 個人化間歇 stay that 個人化間歇."""
    p = personal_pm()
    s = IL.session_for(IL.fit("z5a", None), {**TH, "power_model": p})
    v = IL.with_reps(IL.resolve(s["variant_key"], None, s["variant_adj"]), 2)
    out = IL.session_for({"variant": v, "level": "std", "reps": 2, "equiv": False, "progress": False,
                          "reason": "x", "rung": "z5a"}, {**TH, "power_model": p})
    o = out["variant_adj"]["opt"]
    assert o["reps"] == 2 and o["work_s"] == s["variant_adj"]["opt"]["work_s"] and out["variant_reps"] is None


# ---------------------------------------------------------------------------
# the structure, the push, the judging
# ---------------------------------------------------------------------------

def _personal_session():
    s = IL.session_for(IL.fit("z5a", None), {**TH, "power_model": personal_pm()})
    return {**s, "kind": "quality", "basis": "power"}


def test_the_structure_follows_and_is_not_mapped_again():
    from backend.engine import workout_steps as WS
    s = _personal_session()
    o = s["variant_adj"]["opt"]
    d = WS.derive(s, {**TH, "power_model": personal_pm()})
    works = [r["st"] for r in WS.flat(d["items"]) if r["st"]["kind"] == "work"]
    assert works and all(w["dur"]["value"] == o["work_s"] and w["target"].get("opt") == 2 for w in works)
    c = WS.Ctx(cp=CP, lthr=165.0, basis="power", pzm=personal_pm())
    r = WS.resolve(works[0], c)
    assert (r.lo, r.hi) == (round(o["lo"] * CP), round(o["hi"] * CP))     # the model's band, not a level's
    # the 1:1 rest and the < 2-min anaerobic reps are the prescription's, not flagged as Zone 5 rules
    assert not [i for i in WS.issues(d, c) if "5 區" in i["text"] or "3 區" in i["text"]]
    # the editor keeps the mark when the structure is saved back
    errs: list = []
    assert WS._norm_target(works[0]["target"], errs)["opt"] == 2 and not errs


def test_the_watch_gets_the_models_band():
    from backend.sync import coros_workouts as CW
    s = _personal_session()
    o = s["variant_adj"]["opt"]
    steps = CW.session_steps(s, CW.Thresholds.of({**TH, "power_model": personal_pm()}))
    flat = []
    for x in steps:
        flat += list(x.steps) if isinstance(x, CW.Repeat) else [x]
    work = [x for x in flat if isinstance(x, CW.Step) and x.kind == CW.EX_TRAIN and x.intensity]
    lo, hi = Z.watch_band(o["lo"], o["hi"])
    assert work and all(x.intensity == ("power", round(lo * CP), round(hi * CP)) for x in work)
    assert all(x.seconds == o["work_s"] for x in work)


def test_the_judging_reads_the_same_reps_and_band():
    """interval_eval judges a planned session by the variant it reads back (resolve): the 個人化間歇's
    reps and band, so 達標 and the time in zone follow it (with SP-618)."""
    from backend.engine import quality_gate as QG
    s = _personal_session()
    v = IL.resolve(s["variant_key"], s["variant_reps"], s["variant_adj"])
    spec = QG.variant_tuple(v)
    o = s["variant_adj"]["opt"]
    assert v.n == o["reps"] and v.lo == o["lo"] and v.hi == o["hi"]
    assert spec[2] == o["reps"]


# ---------------------------------------------------------------------------
# the words
# ---------------------------------------------------------------------------

def test_the_words_are_in_both_languages_and_name_no_wko5():
    import json
    from pathlib import Path
    from backend.i18n import use_locale
    root = Path(__file__).resolve().parents[2]
    en = json.loads((root / "backend" / "i18n" / "locales" / "en.json").read_text("utf-8"))
    for m in PZP.OPT_NAMES:
        assert en.get(m), m
    s = _personal_session()
    with use_locale("en"):
        txt = IL._opt_text(IL.resolve(s["variant_key"], None, s["variant_adj"]), personal_pm(), CP, 40)
        rule = PZP.opt_rule(0)
    assert txt.startswith("Personalized intervals (Max aerobic (VO2max)): ") and "today's cap 40 min" in txt
    assert "1–2 reps" in rule and "4:1" in rule
    zh = IL._opt_text(IL.resolve(s["variant_key"], None, s["variant_adj"]), personal_pm(), CP, None)
    for x in (txt, rule, zh, *PZP.OPT_NAMES):
        assert "wko" not in x.lower() and "ilevel" not in x.lower() and "optimized" not in x.lower()


def test_the_public_part_reaches_the_rules_only_through_the_core():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    src = (root / "backend" / "engine" / "power_zone_plan.py").read_text("utf-8")
    assert "0.9625" not in src and "0.1625" not in src and "PRESCRIPTION = " not in src
    assert "from backend.engine.algorithms.wko5_levels import prescription" in src


def test_without_the_core_nothing_is_re_cut(monkeypatch):
    import backend.engine.core_stub as CS
    monkeypatch.setattr(CS, "core_included", lambda: False)
    assert PZP.optimized(1.06, 1.12, 120, 600, personal_pm(), CP) is None


def test_a_switch_marks_the_recut_sessions_with_its_reason():
    from backend.engine import plan_auto as PA
    items = [{"action": "changed", "before": {"title": "VO2max 間歇 5×2 分", "target": "功率 …", "detail": "…",
                                              "minutes": 50}, "uid": "a"},
             {"action": "changed", "before": {"day": "2026-10-16", "target": "x"}, "uid": "b"}]
    assert PA.mark_power_zone(items, "palladino", "personal") == 1
    assert items[0]["rule"] == "power_zone" and "rule" not in items[1]


def test_the_editor_reads_its_family_and_does_not_compare_it_with_the_canonical():
    """The model's band can sit far below Palladino's (model FTP < CP): the session is still a VO2max
    interval (its level), and the equivalence line says why it is not compared."""
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    p = personal_pm(330.0)                                   # CP far above the model's FTP
    s = {**IL.session_for(IL.fit("z5a", None), {**TH, "cp": 330.0, "power_model": p}),
         "kind": "quality", "basis": "power"}
    d = WS.derive(s, {**TH, "cp": 330.0, "power_model": p})
    fam = WT.family_of(d["items"])
    assert fam and fam["id"] == "vo2max"
    eq = WS.equivalence(d, "z5a", WS.Ctx(cp=330.0, basis="power", pzm=p))
    assert eq["ok"] and eq["text"].startswith("個人化間歇：")
    tp = {**IL.session_for(IL.fit("tp", None), {**TH, "cp": 330.0, "power_model": p}), "kind": "quality",
          "basis": "power"}
    assert WT.family_of(WS.derive(tp, {**TH, "cp": 330.0, "power_model": p})["items"])["id"] == "aerobic"
