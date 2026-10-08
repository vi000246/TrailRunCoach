"""
「負荷」 end condition (SP-38): the TSS ↔ COROS TL conversion (engine/coros_tl.py), its
per-athlete refit (shrinkage, model choice, time-ordered backtest, recency, threshold cut),
the closed loop on run load steps, the step type in engine/workout_steps.py, the provider
capabilities, the COROS payload (targetType 6) and its fallback, the fingerprint, the
sync storing the list's trainingLoad, and the 「按圈」 rename. Synthetic data only.
"""
import asyncio
import datetime as dt
import json
import math
import random
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from backend.engine import coros_tl as TL
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW
from backend.sync import workout_targets as WT

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 4)


def run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@pytest.fixture(autouse=True)
def _default_model(monkeypatch):
    """No stored fit read from any DB: the defaults unless a test says otherwise."""
    monkeypatch.setattr(TL, "current", lambda user_id=1: TL.Model.of())


# ---------------------------------------------------------------------------
# conversion
# ---------------------------------------------------------------------------

def test_defaults_per_source_and_inverse():
    m = TL.Model.of()
    p = m.tl(100, "power")
    assert p["group"] == "power" and p["tl"] == pytest.approx(TL.POWER_A * 100 ** TL.POWER_K, abs=0.05)
    assert p["err"] == pytest.approx(p["tl"] * TL.DEFAULT_ERR, abs=0.1) and not p["fitted"]
    h = m.tl(100, "hr", 0.9)
    hours = 100 / (100 * 0.81)
    assert h["group"] == "hr" and h["tl"] == pytest.approx(hours * (TL.HR_C0 + TL.HR_C1 * 0.9 + TL.HR_C2 * 0.81), abs=0.05)
    assert m.tl(100)["group"] == "linear" and m.tl(100)["tl"] == pytest.approx(137.0)
    for basis, f in (("power", None), ("hr", 0.8), ("hr", 0.95), (None, None)):
        for tss in (12.5, 60, 180):
            assert m.tss(m.tl(tss, basis, f)["tl"], basis, f) == pytest.approx(tss, abs=0.2)
    assert m.tss(0) == 0 and m.tl(0, "power")["tl"] == 0
    assert TL.convert(50, "power")["tl"] == m.tl(50, "power")["tl"]
    assert TL.inverse(TL.convert(50, "hr", 0.85)["tl"], "hr", 0.85) == pytest.approx(50, abs=0.2)


def test_if_is_clamped_to_the_fitted_range():
    m = TL.Model.of()
    rate = lambda tss, f: m.tl(tss, "hr", f)["tl"] / (tss / (100 * f * f))     # TL per hour
    lo, hi = TL.HR_IF_RANGE
    assert rate(80, 0.40) == pytest.approx(rate(80, lo), rel=1e-3)
    assert rate(80, 1.30) == pytest.approx(rate(80, hi), rel=1e-3)
    assert rate(80, 0.80) < rate(80, hi)
    # a rate ≤ 0 inside the clamp (a bad fit) never goes negative: the default stands in …
    bad = {"groups": {"hr": {"family": "D", "params": {"c0": -500, "c1": 100, "c2": 0, "if_lo": 0.5, "if_hi": 1.0},
                             "n": 100, "w": 1.0}}}
    v = TL.Model.of(bad).tl(60, "hr", 0.8)
    assert v["tl"] == TL.Model.of().tl(60, "hr", 0.8)["tl"] > 0
    # … and when no D can answer at all, the linear model
    m = TL.Model.of()
    m.groups["hr"] = TL.GroupModel("hr")
    assert TL._family_tl("D", {"c0": -500, "c1": 0, "c2": 0}, 60, 0.8) is None
    import unittest.mock as um
    with um.patch.object(TL, "DEFAULTS", {**TL.DEFAULTS, "hr": ("D", {"c0": -500, "c1": 0, "c2": 0})}):
        v = m.tl(60, "hr", 0.8)
    assert v["group"] == "linear" and v["tl"] == pytest.approx(TL.LINEAR_A * 60)


def test_load_factor_scales_the_tss_before_converting():
    store = {"sessions": {f"u{i}": {"day": "2026-09-01", "steps": [{"i": 1, "n": 3, "tss": 50, "tl": 60}],
                                    "actual": [60.0]} for i in range(10)}}
    lf = TL.load_factor(store)
    assert lf["n"] == 10 and lf["ratio"] == pytest.approx(1.2)
    assert lf["factor"] == pytest.approx(math.exp(10 / 15 * math.log(1.2)), abs=1e-4)      # shrunk toward 1
    m = TL.Model.of(None, store)
    assert m.tl(60, "power")["tl"] == pytest.approx(TL.Model.of().tl(60 / lf["factor"], "power")["tl"], abs=0.05)
    assert m.tss(m.tl(60, "power")["tl"], "power") == pytest.approx(60, abs=0.2)
    assert TL.load_factor(None) == {"factor": 1.0, "n": 0, "k": TL.LOAD_K, "ratio": None}


# ---------------------------------------------------------------------------
# refit
# ---------------------------------------------------------------------------

def _rows(n, tl_of, start=TODAY - dt.timedelta(days=200), src="power", ftp=250.0, lthr=165.0, seed=1, noise=0.03):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        tss = rnd.uniform(30, 160)
        f = rnd.uniform(0.65, 0.95)
        hours = tss / (100 * f * f)
        tl = tl_of(tss, f, hours) * (1 + rnd.uniform(-noise, noise))
        out.append({"date": (start + dt.timedelta(days=int(i * 200 / max(1, n)))).isoformat(), "tl": tl,
                    "tss": tss, "tss_source": src, "if": f, "hrtss": tss, "hrif": f, "hours": hours,
                    "ftp": ftp, "lthr": lthr})
    return out


def test_group_samples_and_filters():
    rows = [{"date": "2026-09-01", "tl": 100, "tss": 80, "tss_source": "power", "if": 0.8, "hrtss": 70, "hrif": 0.78,
             "hours": 1.2, "ftp": 250, "lthr": 165},
            {"date": "2026-09-02", "tl": 50, "tss": 40, "tss_source": "hrtss", "hrtss": 40, "hrif": 0.7, "hours": 0.8},
            {"date": "2026-09-03", "tl": 0, "tss": 40, "tss_source": "power", "hours": 1.0},          # no TL
            {"date": "2026-09-04", "tl": 30, "tss": 40, "tss_source": "power", "hours": 0.1},         # 6 min
            {"date": "2026-09-05", "tl": 900, "tss": 600, "tss_source": "hrtss", "hours": 30.0}]      # multi-day
    g = TL.group_samples(rows)
    assert [len(g[k]) for k in TL.GROUPS] == [1, 2, 1]
    assert g["power"][0]["thr"] == 250 and g["hr"][0]["x"] == 70 and g["linear"][0]["x"] == 40


def test_threshold_change_and_recency():
    s = [{"date": f"2026-0{m}-01", "thr": t, "x": 1, "y": 1} for m, t in ((3, 230), (4, 232), (5, 260), (6, 262), (7, None))]
    kept = TL.since_threshold_change(s)
    assert [d["date"] for d in kept] == ["2026-05-01", "2026-06-01", "2026-07-01"]
    w = TL.recency([{"date": (TODAY - dt.timedelta(days=d)).isoformat()} for d in (0, 120, 240)], TODAY)
    assert [round(x["wt"], 3) for x in w] == [1.0, 0.5, 0.25]


def test_refit_small_n_uses_low_parameter_models_and_shrinks():
    # a quadratic-in-IF truth on few activities: only A / C may be chosen (D would overfit)
    rows = _rows(20, lambda tss, f, h: h * (-300 + 900 * f - 400 * f * f), src="hrtss")
    new, rep = TL.refit(rows, None, TODAY)
    e = new["groups"]["hr"]
    assert rep["hr"]["n"] == 20 and e["family"] in TL.SMALL_FAMILIES
    assert e["w"] == pytest.approx(20 / (20 + TL.SHRINK_K), abs=1e-4)
    assert e["backtest"] and e["backtest"]["n"] >= TL.BACKTEST_MIN_N and e["loo"]["n"] == 20
    gm = TL.Model.of(new).groups["hr"]
    # the conversion is the blend: w·individual + (1 − w)·default
    own = TL._family_tl(e["family"], e["params"], 80, 0.8)
    dflt = TL._family_tl("D", TL.DEFAULTS["hr"][1], 80, 0.8)
    assert gm.tl(80, 0.8) == pytest.approx(e["w"] * own + (1 - e["w"]) * dflt)


def test_refit_large_n_can_choose_the_quadratic_and_learns_the_athlete():
    truth = lambda tss, f, h: h * (-300 + 900 * f - 400 * f * f)
    rows = _rows(150, truth, src="hrtss", noise=0.01)
    new, rep = TL.refit(rows, None, TODAY)
    e = new["groups"]["hr"]
    assert e["family"] == "D" and e["n"] == 150
    assert e["params"]["c1"] == pytest.approx(900, rel=0.1)
    assert e["params"]["if_lo"] >= 0.65 and e["params"]["if_hi"] <= 0.95
    # with w = 150 / 180 the athlete's own relation dominates the default's
    m = TL.Model.of(new)
    want = truth(80, 0.8, 80 / 64)
    assert abs(m.tl(80, "hr", 0.8)["tl"] - want) < abs(TL.Model.of().tl(80, "hr", 0.8)["tl"] - want)


def test_refit_too_few_samples_keeps_the_stored_fit():
    stored = {"groups": {"power": {"family": "A", "params": {"a": 1.5}, "n": 40, "w": 0.57}}}
    new, rep = TL.refit(_rows(2, lambda tss, f, h: tss), stored, TODAY)
    assert new["groups"]["power"] == stored["groups"]["power"] and rep["power"]["kept"]


def test_backtest_gate_keeps_a_better_stored_fit():
    # the stored fit matches the recent activities; older ones (before the holdout) follow
    # another relation, so a new fit trained on them is worse on the last 30 days: kept
    old = _rows(60, lambda tss, f, h: 2.0 * tss, start=TODAY - dt.timedelta(days=150), seed=2)
    old = [r for r in old if r["date"] < (TODAY - dt.timedelta(days=40)).isoformat()]
    recent = _rows(12, lambda tss, f, h: 1.0 * tss, start=TODAY - dt.timedelta(days=25), seed=3)
    for i, r in enumerate(recent):
        r["date"] = (TODAY - dt.timedelta(days=25 - 2 * i)).isoformat()
    stored = {"groups": {"power": {"family": "A", "params": {"a": 1.0 / 1.0}, "n": 300, "w": 0.999}}}
    # a = 1.0 in the stored blend (w ≈ 1): the recent truth
    new, rep = TL.refit(old + recent, stored, TODAY)
    e = new["groups"]["power"]
    assert e["params"] == {"a": 1.0} and e["last_check"]["candidate"]["mae"] > e["last_check"]["candidate"]["current_mae"]
    assert rep["power"]["kept"].startswith("the stored fit")
    # nothing stored: the same data is accepted (the defaults are only a prior)
    new2, rep2 = TL.refit(old + recent, None, TODAY)
    # nothing stored: the candidate still has to beat the defaults on the same holdout (it doesn't here)
    assert rep2["power"]["kept"] and "power" not in new2["groups"]
    assert rep2["power"]["backtest_result"]["n"] == len(recent)
    assert TL.validate(new) is None and TL.validate(new2) is None


def test_describe_for_the_settings_page():
    new, _ = TL.refit(_rows(40, lambda tss, f, h: 1.3 * tss), None, TODAY)
    d = TL.describe(new, None)
    g = {r["group"]: r for r in d["groups"]}
    assert g["power"]["fitted"] and g["power"]["n"] == 40 and g["power"]["backtest"]
    assert not g["linear"]["fitted"] and g["linear"]["text"] == TL.MODEL_TEXT["A"] and d["badge"] == "推估"
    with pytest.raises(ValueError):
        TL.validate({"groups": {"power": {"family": "Z", "params": {}}}})


# ---------------------------------------------------------------------------
# closed loop
# ---------------------------------------------------------------------------

def test_factor_does_not_oscillate_once_applied():
    """Steps pushed under a factor of 1.2 that then run exactly as planned keep the factor
    (≈ 1.2 after shrinkage), they don't pull it back toward 1."""
    m12 = TL.Model.of()
    m12.factor = 1.2
    store = None
    for i in range(20):
        tl = m12.tl(50, "power")["tl"]
        store = TL.record_push(store, f"u{i}", "2026-09-01",
                               [{"i": 1, "n": 3, "tss": 50, "tl": tl, "basis": "power", "if": 0.9, "f": 1.2}])
    sessions = [{"uid": f"u{i}", "state": "done"} for i in range(20)]
    raw = TL.Model.of()                                            # the uncorrected model
    with_raw, _ = TL.refresh_load(store, sessions, lambda s, r: [50.0],
                                  lambda st: raw.tss(st["tl"], st["basis"], st["if"]))
    without_raw, _ = TL.refresh_load(store, sessions, lambda s, r: [50.0])
    want = math.exp(20 / 25 * math.log(1.2))
    assert TL.load_factor(with_raw)["factor"] == pytest.approx(want, abs=0.01)
    assert TL.load_factor(without_raw)["factor"] == pytest.approx(want, abs=1e-3)
    assert TL.load_factor(with_raw)["ratio"] == pytest.approx(1.2, abs=0.01)


def test_tl_backfill_alone_triggers_the_refit(monkeypatch):
    from backend.engine import calibrate
    started = []

    async def fake_run(athlete_id=1):
        started.append(athlete_id)
    monkeypatch.setattr(calibrate, "run_safe", fake_run)

    async def go():
        assert calibrate._after_sync("coros", {"status": "ok", "downloaded": 0}) is None
        t = calibrate._after_sync("coros", {"status": "ok", "downloaded": 0, "tl_filled": 3})
        await t
    run(go())
    assert started == [1]


def test_window_share_and_step_actual():
    t = list(range(0, 1200))
    x = [150.0] * 300 + [300.0] * 600 + [150.0] * 300       # a harder middle lap
    laps = [{"start_s": 0, "duration_s": 300}, {"start_s": 300, "duration_s": 600}, {"start_s": 900, "duration_s": 300}]
    share = TL.window_share(t, x, 300, 900)
    assert share == pytest.approx(600 * 4 / (600 * 4 + 600), abs=0.01)
    assert TL.step_actual(laps, t, x, 100.0, 1, 3) == pytest.approx(100 * share, abs=0.2)
    assert TL.step_actual(laps, t, x, 100.0, 1, 4) is None                  # laps ≠ pushed steps
    assert TL.window_share([0], [1], 0, 1) is None


def test_record_refresh_and_idempotence():
    st = [{"i": 1, "n": 3, "tss": 50, "tl": 64}]
    s1 = TL.record_push(None, "u1", "2026-10-05", st)
    assert s1["sessions"]["u1"]["actual"] is None
    sessions = [{"uid": "u1", "state": "done", "done_by": {"index": 0}}]
    s2, ch = TL.refresh_load(s1, sessions, lambda s, rec: [55.0])
    assert ch and s2["sessions"]["u1"]["actual"] == [55.0]
    # a re-push after the run never replaces the sample; a second refresh changes nothing
    assert TL.record_push(s2, "u1", "2026-10-05", [{"i": 1, "n": 3, "tss": 70, "tl": 90}]) == s2
    assert TL.refresh_load(s2, sessions, lambda s, rec: [99.0]) == (s2, False)
    # not done / the laps don't line up → no sample
    assert TL.refresh_load(s1, [{"uid": "u1", "state": "active"}], lambda s, r: [1.0])[1] is False
    assert TL.refresh_load(s1, sessions, lambda s, r: None)[1] is False
    assert TL.load_factor(s2)["ratio"] == pytest.approx(1.1)
    TL.validate_load(s2)


# ---------------------------------------------------------------------------
# the step type
# ---------------------------------------------------------------------------

def _doc(*items):
    return {"items": list(items)}


LOAD_HR = {"id": "L", "kind": "work", "dur": {"type": "load", "value": 75},
           "target": {"type": "hr", "mode": "abs", "lo": 140, "hi": 146}}


def test_normalize_load_main_set_only():
    d = WS.normalize(_doc(LOAD_HR))
    assert d["items"][0]["dur"] == {"type": "load", "value": 75}
    assert WS.normalize(_doc({**LOAD_HR, "dur": {"type": "load", "value": "12.34"}}))["items"][0]["dur"]["value"] == 12.3
    with pytest.raises(WS.StepsError) as e:
        WS.normalize(_doc({**LOAD_HR, "kind": "warm"}))
    assert "「負荷」只能用在主課" in e.value.errors
    with pytest.raises(WS.StepsError) as e:
        WS.normalize(_doc({**LOAD_HR, "dur": {"type": "load", "value": 900}}))
    assert any("負荷（TSS）" in x for x in e.value.errors)
    with pytest.raises(WS.StepsError) as e:
        WS.normalize(_doc({**LOAD_HR, "dur": {"type": "lap"}}))
    assert e.value.errors[0] == "時長類型要是 時間／距離／直到按下計圈／負荷"


def test_load_step_time_tss_view_and_preview():
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0)
    d = WS.normalize(_doc({"id": "w", "kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "none"}},
                          {**LOAD_HR, "target": {"type": "power", "mode": "abs", "lo": 255, "hi": 285}}))
    f = 270 / 300
    t = WS.totals(d, c)
    assert t["est"] and "「負荷」段" in t["est_note"]
    assert t["sec"] == round(600 + 75 * 3600 / (f * f * 100))
    assert t["tss"] == pytest.approx(600 * 0.65 ** 2 * 100 / 3600 + 75, abs=0.1)
    v = WS.view(d, c)
    o = next(x for x in v["order"] if x["id"] == "L")
    want = TL.Model.of().tl(75, "power", f)
    assert o["load"]["tss"] == 75 and o["load"]["tl"] == round(want["tl"]) and v["resolved"]["L"]["load"] == o["load"]
    assert WS.fmt_dur(d["items"][1]["dur"]) == "負荷 75 TSS"
    line = v["watch"]["lines"][1]
    assert line["dur"] == f"負荷 {round(want['tl'])} TL" and line["target"] == "功率 255–285 W"
    assert any("「負荷」段" in x for x in v["watch"]["lost"])
    assert WS.load_records(d, c) == [{"i": 1, "n": 2, "tss": 75, "tl": round(want["tl"]), "basis": "power",
                                      "if": round(f, 4), "f": 1.0}]


def test_issue_when_the_provider_has_no_load_end_condition():
    d = WS.normalize(_doc(LOAD_HR))
    c = WS.Ctx(cp=300.0, lthr=170.0, end_conditions=("time", "open"), provider_label="Garmin")
    assert any(i["level"] == "warn" and "Garmin沒有「負荷」結束條件" in i["text"] for i in WS.issues(d, c))
    c2 = WS.Ctx(cp=300.0, lthr=170.0, end_conditions=WT.get("coros").capabilities.end_conditions)
    assert not any("結束條件" in i["text"] for i in WS.issues(d, c2))


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

def test_capabilities_declare_end_conditions():
    caps = {p["id"]: p["capabilities"] for p in WT.available()}
    assert caps["coros"]["end_conditions"] == ["time", "distance", "open", "load"] and caps["coros"]["load_unit"] == "TL"
    assert caps["coros"]["end_labels"]["open"] == "直到按下計圈" and caps["coros"]["end_labels"]["load"] == "負荷"
    assert "load" not in caps["garmin"]["end_conditions"] and caps["garmin"]["end_labels"]["open"] == "直到按下 Lap 鍵"
    assert caps["intervals"]["end_conditions"] == ["time"] and caps["garmin"]["load_unit"] is None


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_editor_dropdown_follows_the_provider():
    js = r"""
const vm = require("vm"), fs = require("fs"), w = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { window: w, document: {} });
const E = w.WorkoutEditor.endOptions, caps = JSON.parse(process.argv[2]);
const ctx = (id) => ({ provider: { label: caps[id].label, capabilities: caps[id].capabilities }, load_kinds: ["work"] });
const vals = (h) => [...h.matchAll(/value="([^"]+)"/g)].map((m) => m[1]);
console.log(JSON.stringify({
  coros_work: vals(E(ctx("coros"), { kind: "work", dur: { type: "time", value: 60 } })),
  coros_warm: vals(E(ctx("coros"), { kind: "warm", dur: { type: "time", value: 60 } })),
  garmin_load: E(ctx("garmin"), { kind: "work", dur: { type: "load", value: 40 } }),
  none: vals(E({}, { kind: "work", dur: { type: "open" } })),
}));
"""
    caps = {p["id"]: p for p in WT.available()}
    r = subprocess.run([NODE, "-e", js, str(ROOT / "static" / "workout_editor.js"), json.dumps(caps)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert got["coros_work"] == ["time", "distance", "open", "load"]
    assert got["coros_warm"] == ["time", "distance", "open"]                 # 「負荷」 only on the main set
    assert "直到按下 Lap 鍵" in got["garmin_load"] and 'value="load" selected' in got["garmin_load"] \
        and "不支援" in got["garmin_load"] and "distance" not in got["garmin_load"]
    assert got["none"] == ["time", "distance", "open"]


# ---------------------------------------------------------------------------
# the COROS payload
# ---------------------------------------------------------------------------

def test_load_exercise_payload_shape():
    """The shape read back from a Training Hub workout with a 「TL 100」 end condition."""
    th = CW.Thresholds(lthr=160.0)
    st = CW.Step(CW.EX_TRAIN, 3400, ("hr", 140, 146), "主課", load_tss=75.0, load_tl=100.0)
    ex = CW.build_program("TRC x", [st], th)["exercises"][0]
    assert {k: ex[k] for k in ("exerciseType", "targetType", "targetValue", "intensityType", "intensityValue",
                               "intensityValueExtend", "hrType")} == \
        {"exerciseType": 2, "targetType": 6, "targetValue": 100, "intensityType": 2, "intensityValue": 140,
         "intensityValueExtend": 146, "hrType": 3}
    assert ex["name"] == "主課" and CW.build_program("x", [st], th)["estimatedTime"] == 3400


def _session(steps, **kw):
    return {"id": "s1", "key": "u1", "kind": "quality", "title": "負荷課", "minutes": 60, "day": "2026-10-06",
            "steps": steps, **kw}


TH = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}
LOAD_DOC = _doc({"id": "w", "kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "none"}},
                LOAD_HR)
PLAIN_DOC = _doc({"id": "w", "kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "none"}},
                 {"id": "m", "kind": "work", "dur": {"type": "time", "value": 1800},
                  "target": {"type": "hr", "mode": "abs", "lo": 140, "hi": 146}})


def test_session_push_sends_targettype_6_and_records_load_steps():
    spec = CW.session_workout(_session(LOAD_DOC), TH)
    ex = spec.payload["exercises"][1]
    f = WS.hr_to_p(143 / 170)
    want = round(TL.Model.of().tl(75, "hr", f)["tl"])
    assert ex["targetType"] == 6 and ex["targetValue"] == want and ex["intensityType"] == 2
    assert spec.load_steps == [{"i": 1, "n": 2, "tss": 75, "tl": want, "basis": "hr", "if": round(f, 4), "f": 1.0}]


def test_fallback_without_the_code_and_on_other_providers(monkeypatch):
    garmin = WT.get("garmin").build_payload(_session(LOAD_DOC), TH)
    steps = [s for s in garmin["workoutSegments"][0]["workoutSteps"]]
    assert all(s["endCondition"]["conditionTypeKey"] in ("time", "lap.button") for s in steps)
    assert steps[1]["endCondition"]["conditionTypeKey"] == "time" and steps[1]["endConditionValue"] > 0
    monkeypatch.setattr(CW, "COROS_TARGET_TYPE_LOAD", None)
    spec = CW.session_workout(_session(LOAD_DOC), TH)
    ex = spec.payload["exercises"][1]
    f = WS.hr_to_p(143 / 170)
    assert ex["targetType"] == 2 and ex["targetValue"] == round(75 * 3600 / (f * f * 100))
    assert "負荷 75 TSS（約" in ex["name"] and spec.load_steps == []


def test_fingerprint_moves_only_with_a_load_steps_sent_value(monkeypatch):
    fp = lambda doc: CW.session_workout(_session(doc), TH).fingerprint
    plain0, load0 = fp(PLAIN_DOC), fp(LOAD_DOC)
    other = TL.Model.of({"groups": {"hr": {"family": "A", "params": {"a": 3.0}, "n": 500, "w": 0.95},
                                    "linear": {"family": "A", "params": {"a": 3.0}, "n": 500, "w": 0.95}}})
    monkeypatch.setattr(TL, "current", lambda user_id=1: other)
    assert fp(PLAIN_DOC) == plain0                     # a refit never marks plain sessions 需更新
    assert fp(LOAD_DOC) != load0                       # the sent TL moved


def test_a_small_refit_keeps_the_sent_tl(monkeypatch):
    """SP-38 (owner 2026-10-04): a refit moving a load step's TL by < 3 keeps the TL last pushed
    (the closed-loop record) — no 需更新, no re-push; ≥ 3 sends the new one."""
    s = _session(LOAD_DOC)
    pushed = CW.session_workout(s, TH)                     # pushed with the default model
    want = pushed.load_steps[0]["tl"]
    store = {"sessions": {"u1": {"day": s["day"], "steps": pushed.load_steps, "actual": None}}}
    monkeypatch.setattr(TL, "_read", lambda key, user_id=1: store if key == TL.LOAD_KEY else None)

    class Shifted:
        def __init__(self, d):
            self.d, self.factor = d, 1.0

        def tl(self, tss, group, f):
            return {**TL.Model.of().tl(tss, group, f), "tl": want + self.d}
    for d, same in ((1, True), (-2, True), (3, False), (-3, False)):
        monkeypatch.setattr(TL, "current", lambda user_id=1, d=d: Shifted(d))
        spec = CW.session_workout(s, TH)
        ex = spec.payload["exercises"][1]
        assert (spec.fingerprint == pushed.fingerprint) is same, d
        assert ex["targetValue"] == (want if same else want + d)
        assert spec.load_steps[0]["tl"] == ex["targetValue"]
    # a different planned TSS is a new step: the record doesn't hold it back
    other = json.loads(json.dumps(LOAD_DOC))
    for it in other["items"]:
        if it.get("dur", {}).get("type") == "load":
            it["dur"]["value"] += 1
    monkeypatch.setattr(TL, "current", lambda user_id=1: Shifted(1))
    assert CW.session_workout(_session(other), TH).payload["exercises"][1]["targetValue"] == want + 1


def test_push_records_the_load_steps_for_the_closed_loop():
    from backend.settings.repository import SettingsRepository
    from backend.sync import http
    from backend.tests.test_coros_workouts import FakeHub, make_db
    db = run(make_db())
    fake = FakeHub()
    s = {**_session(LOAD_DOC), "key": "uid-1", "week_start": "2026-10-05"}
    with http.use_transport(httpx.MockTransport(fake)):
        res = run(CW.push_sessions(db, [s], TH, "2026-10-04"))
    assert res["sessions"][0]["status"] == "pushed"
    prog = next(iter(fake.live().values()))
    assert prog["exercises"][1]["targetType"] == 6
    store = run(SettingsRepository(db).get(TL.LOAD_KEY))
    assert store["sessions"]["uid-1"]["steps"][0]["tss"] == 75 and store["sessions"]["uid-1"]["actual"] is None


# ---------------------------------------------------------------------------
# sync: the list's trainingLoad is stored
# ---------------------------------------------------------------------------

def test_list_training_load():
    from backend.sync.coros_client import list_training_load
    assert list_training_load({"trainingLoad": 87}) == 87.0
    assert list_training_load({"trainingLoad": "12.5"}) == 12.5
    for v in (None, 0, -3, "x", True, float("nan")):
        assert list_training_load({"trainingLoad": v}) is None


def test_sync_stores_and_backfills_training_load(tmp_path, _fit_root_in_tmp):
    from backend.db.models import WorkoutFile
    from backend.sync import coros_client, http
    from backend.tests.fit_builder import build_run
    from backend.tests.test_sync_e2e import FakeCoros, collect, make_session

    start = dt.datetime(2026, 9, 1, 22, 30, tzinfo=dt.timezone.utc)
    act = lambda label, s, **kw: {"labelId": label, "date": int(s.strftime("%Y%m%d")), "sportType": 100,
                                  "fitUrl": f"https://files.example/fit/{label}", "_bytes": build_run(s), **kw}

    async def go():
        s = await make_session(tmp_path)
        fake = FakeCoros([act("111", start, trainingLoad=64), act("222", start + dt.timedelta(days=1))])
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            await collect(coros_client.sync_workouts(s, 1))
            rows = {r.coros_activity_id: r for r in (await s.execute(select(WorkoutFile))).scalars()}
            assert rows["111"].coros_training_load == 64 and rows["222"].coros_training_load is None
            n_calls = len(fake.list_params)
            fake.activities[1]["trainingLoad"] = 41          # COROS lists it now: filled, no download
            ev = await collect(coros_client.sync_workouts(s, 1, since="2026-08-30"))
            assert next(e for e in ev if e["status"] == "complete")["tl_filled"] == 1
            rows = {r.coros_activity_id: r for r in (await s.execute(select(WorkoutFile))).scalars()}
            assert rows["222"].coros_training_load == 41 and len(fake.list_params) == n_calls + 1
    run(go())


# ---------------------------------------------------------------------------
# 「按圈」 → 「直到按下計圈」
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", ["static/racepower.html", "static/i18n/zh-TW/racepower.json", "api/racepower.py",
                                 "engine/racepower/watch_export.py", "engine/workout_templates.py",
                                 "engine/workout_steps.py", "static/workout_editor.js", "demo/static_shim.js"])
def test_lap_wording_renamed(rel):
    txt = (ROOT / rel).read_text(encoding="utf-8")
    assert "按圈" not in txt.replace("直到按下計圈", "").replace("按下計圈", "")


def test_template_and_export_wording():
    src = (ROOT / "engine" / "workout_templates.py").read_text(encoding="utf-8")
    assert "走路到呼吸恢復（≥ 2′），直到按下計圈" in src and "第 10′ 按下計圈" in src
    zh = json.loads((ROOT / "static" / "i18n" / "zh-TW" / "racepower.json").read_text(encoding="utf-8"))
    assert zh["coros.lap"] == "直到按下計圈" and "「直到按下計圈」" in zh["coros.tip"]


# ---------------------------------------------------------------------------
# after a sync: refit from the stored TL + the Dataset, closed-loop samples
# ---------------------------------------------------------------------------

class _W:
    def __init__(self, idx, label, day, m):
        from types import SimpleNamespace
        from backend.engine.wko5expr.dataset import date_to_day
        self.idx, self.day, self.metrics, self.sport = idx, date_to_day(day), m, "run"
        self.entry = SimpleNamespace(file=f"/fits/{label}_{day.isoformat()}_run.fit")


class _DS:
    def __init__(self, ws, laps=None, ch=None):
        self.workouts, self._laps, self._ch, self.memo = ws, laps or {}, ch or {}, {}

    def sport_setting(self, kind, w):
        return 165.0

    def laps(self, idx):
        return self._laps.get(idx, [])

    def channel(self, idx, name):
        import numpy as np
        return np.array(self._ch[(idx, name)], dtype=float) if (idx, name) in self._ch else None


def test_refit_and_store_after_sync():
    from backend.db.models import WorkoutFile
    from backend.engine import plan_store as PS
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import make_db
    db = run(make_db())
    rnd = random.Random(5)
    ws = []
    for i in range(45):
        day = TODAY - dt.timedelta(days=90 - 2 * i)
        tss = rnd.uniform(40, 150)
        lab = str(9000 + i)
        db.add(WorkoutFile(athlete_id=1, file_path=f"/fits/{lab}.fit", file_format="fit", coros_activity_id=lab,
                           coros_training_load=1.25 * tss))
        ws.append(_W(i, lab, day, {"tss": tss, "tss_source": "power", "if": 0.8, "duration": tss / 64 * 3600,
                                   "ftp_used": 250.0}))
    # a pushed load step that was run: 3 laps = the 3 pushed steps, the middle one the load step
    ws.append(_W(45, "x", TODAY, {"tss": 100.0, "tss_source": "power", "duration": 1200.0}))
    laps = {45: [{"start_s": 0, "duration_s": 300}, {"start_s": 300, "duration_s": 600}, {"start_s": 900, "duration_s": 300}]}
    ch = {(45, "power"): [150.0] * 300 + [300.0] * 600 + [150.0] * 300, (45, "deltatime"): [0.0] + [1.0] * 1199}
    run(db.commit())
    repo = SettingsRepository(db)
    run(repo.set(TL.LOAD_KEY, TL.record_push(None, "uid-9", TODAY.isoformat(), [{"i": 1, "n": 3, "tss": 60, "tl": 70, "basis": "power", "f": 1.0}])))
    run(db.commit())
    sess = {"uid": "uid-9", "state": "done", "done_by": {"index": 45}}

    async def load(db_, athlete_id=1, *a, **k):
        return [sess]
    import unittest.mock as um
    with um.patch.object(PS, "load", load):
        out = run(TL.refit_and_store(db, 1, _DS(ws, laps, ch), TODAY))
    assert out["samples"] == 45 and out["report"]["power"]["n"] == 45
    stored = run(repo.get(TL.KEY))
    assert stored["groups"]["power"]["n"] == 45 and stored["groups"]["power"]["w"] == pytest.approx(45 / 75, abs=1e-3)
    act = run(repo.get(TL.LOAD_KEY))["sessions"]["uid-9"]["actual"]
    assert act and act[0] == pytest.approx(100 * 2400 / 3000, abs=1.0)          # 80 TSS in the load lap
    # the sample is measured against the uncorrected (just refit) model's TSS for the TL sent
    raw = TL.Model.of(stored).tss(70, "power")
    assert run(repo.get(TL.LOAD_KEY))["sessions"]["uid-9"]["raw"] == [raw]
    assert out["load"]["n"] == 1 and out["load"]["ratio"] == pytest.approx(act[0] / raw, abs=0.01)


def test_user_template_round_trips_a_load_step():
    from backend.engine import user_templates as UT
    from backend.tests.test_coros_workouts import make_db
    db = run(make_db())
    cat = next(iter(UT.BUILTIN))
    t = run(UT.create(db, {"name": "負荷主課", "cats": [cat], "steps": LOAD_DOC}))
    got = run(UT.get(db, t["id"]))
    assert got["steps"]["items"][1]["dur"] == {"type": "load", "value": 75}
