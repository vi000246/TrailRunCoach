"""
「負荷」 entered by RPE (SP-57): the five levels → Borg CR-10, Foster session RPE × the
athlete's factor → TSS (engine/rpe_load.py), its refit with shrinkage on watch-recorded RPE,
the step in engine/workout_steps.py (normalize, timing, view), the push still sending COROS TL,
and the editor's payload (static/workout_editor.js). Synthetic data only.
"""
import datetime as dt
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import coros_tl as TL
from backend.engine import rpe_load as RL
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 5)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    """No stored fit read from any DB: the defaults unless a test says otherwise."""
    monkeypatch.setattr(RL, "current", lambda user_id=1: RL.Model())
    monkeypatch.setattr(TL, "current", lambda user_id=1: TL.Model.of())


def _doc(*items):
    return {"items": list(items)}


def _rpe_step(level="very_hard", minutes=40, **kw):
    return {"id": "L", "kind": "work", "dur": {"type": "load", "value": 1, "rpe": level, "min": minutes},
            "target": {"type": "none"}, **kw}


# ---------------------------------------------------------------------------
# mapping and conversion
# ---------------------------------------------------------------------------

def test_five_levels_map_to_cr10_anchors():
    assert [(k, v) for k, _l, v in RL.LEVELS] == [("easy", 2), ("moderate", 4), ("hard", 5),
                                                    ("very_hard", 7), ("max", 10)]
    assert [RL.LABEL[k] for k in RL.CR10] == ["輕鬆", "稍累", "累", "很累", "極限"]
    assert RL.levels()[3] == {"id": "very_hard", "cr10": 7}


def test_session_rpe_times_the_factor():
    m = RL.Model()
    assert m.tss("very_hard", 40) == pytest.approx(RL.DEFAULT_FACTOR * 7 * 40)
    assert RL.Model(factor=0.25).tss("easy", 60) == pytest.approx(30.0)
    assert m.tss("nope", 40) is None and m.tss("hard", 0) is None and m.tss("hard", None) is None
    assert RL.to_tss("max", 10, RL.Model(factor=0.5)) == 50.0


def test_model_of_stored_value_and_error():
    assert not RL.Model.of(None).fitted and RL.Model.of({"factor": -1}).factor == RL.DEFAULT_FACTOR
    m = RL.Model.of({"factor": 0.4, "n": 10, "w": 0.5, "loo": {"mape": 0.1}})
    assert m.fitted and m.factor == 0.4 and m.err_frac() == pytest.approx(0.5 * 0.1 + 0.5 * RL.DEFAULT_ERR)
    assert RL.Model().err_frac() == RL.DEFAULT_ERR


# ---------------------------------------------------------------------------
# the refit (watch RPE vs actual TSS)
# ---------------------------------------------------------------------------

def _rows(n, ratio=0.5, start=TODAY, thr=300.0):
    out = []
    for i in range(n):
        rpe, h = 3 + i % 5, 0.5 + (i % 3) * 0.5
        out.append({"date": (start - dt.timedelta(days=i)).isoformat(), "rpe": rpe, "hours": h,
                    "tss": ratio * rpe * h * 60, "thr": thr})
    return out


def test_samples_filters():
    rows = [{"date": "2026-10-01", "rpe": 5, "hours": 1.0, "tss": 90},       # r = 0.3: kept
            {"date": "2026-10-01", "rpe": None, "hours": 1.0, "tss": 90},    # no RPE
            {"date": "2026-10-01", "rpe": 5, "hours": 0.1, "tss": 10},       # < 10 min
            {"date": "2026-10-01", "rpe": 5, "hours": 1.0, "tss": 0},        # no TSS
            {"date": "2026-10-01", "rpe": 1, "hours": 1.0, "tss": 200}]      # r = 3.3: mis-rated
    s = RL.samples(rows)
    assert len(s) == 1 and s[0]["r"] == pytest.approx(0.3) and s[0]["sr"] == 300


def test_refit_shrinks_toward_the_default():
    new, rep = RL.refit(_rows(10, ratio=0.5), TODAY)
    w = 10 / (10 + RL.SHRINK_K)
    assert new["n"] == 10 and new["w"] == pytest.approx(w, abs=1e-4) and new["ratio"] == pytest.approx(0.5)
    want = math.exp(w * math.log(0.5) + (1 - w) * math.log(RL.DEFAULT_FACTOR))
    assert new["factor"] == pytest.approx(want, abs=1e-4) and rep["factor"] == new["factor"]
    assert RL.DEFAULT_FACTOR < new["factor"] < 0.5
    # more data: closer to the athlete's own ratio
    big, _ = RL.refit(_rows(90, ratio=0.5), TODAY)
    assert big["factor"] > new["factor"] and big["w"] == pytest.approx(0.9, abs=1e-4)
    # the LOO error is reported (every sample has ratio 0.5: the error is the shrinkage bias)
    assert new["loo"]["n"] == 10 and new["loo"]["mape"] > 0 and new["loo"]["bias"] < 0
    assert big["loo"]["mape"] < new["loo"]["mape"]
    RL.validate(new)


def test_refit_without_rpe_keeps_the_default_and_threshold_cut():
    assert RL.refit([{"date": "2026-10-01", "rpe": None, "hours": 1, "tss": 50}], TODAY)[0] is None
    # an FTP change > 5 %: only the activities after it
    old = _rows(20, ratio=0.2, start=TODAY - dt.timedelta(days=60), thr=250.0)
    new = _rows(5, ratio=0.5, start=TODAY, thr=300.0)
    fit, rep = RL.refit(old + new, TODAY)
    assert fit["n"] == 5 and fit["ratio"] == pytest.approx(0.5)


def test_validate_and_describe():
    RL.validate(None)
    with pytest.raises(ValueError):
        RL.validate({"n": 3})
    d = RL.describe(None)
    assert not d["fitted"] and d["factor"] == RL.DEFAULT_FACTOR and d["err_pct"] == round(RL.DEFAULT_ERR * 100)
    new, _ = RL.refit(_rows(10), TODAY)
    d = RL.describe(new)
    assert d["fitted"] and d["n"] == 10 and d["loo"]["mape"] == new["loo"]["mape"] and len(d["levels"]) == 5


def test_activity_rows_join_the_watch_rpe():
    from types import SimpleNamespace as NS
    from backend.engine.wko5expr.dataset import day_to_date
    day0 = day_to_date(0)
    start = dt.datetime(2026, 10, 1, 7, 0)
    w = NS(entry=NS(start=start, file="a.fit"), day=(dt.date(2026, 10, 1) - day0).days,
           metrics={"tss": 80.0, "movingduration": 3600, "tss_source": "power", "ftp_used": 300.0})
    w2 = NS(entry=NS(start=start + dt.timedelta(days=1), file="b.fit"), day=w.day + 1,
            metrics={"tss": 50.0, "movingduration": 3600})
    ds = NS(workouts=[w, w2], sport_setting=lambda k, x: None)
    from backend.engine import activity_tags as AT
    rec = [{"start_local": AT.key_of(start), "file": "a.fit", "rpe": 6.0, "feel": None}]
    rows = RL.activity_rows(ds, rec)
    assert rows == [{"date": "2026-10-01", "rpe": 6.0, "tss": 80.0, "hours": 1.0, "thr": 300.0}]


# ---------------------------------------------------------------------------
# the step (engine/workout_steps.py)
# ---------------------------------------------------------------------------

def test_normalize_sets_the_tss_from_rpe_and_minutes():
    d = WS.normalize(_doc(_rpe_step("very_hard", 40)))
    assert d["items"][0]["dur"] == {"type": "load", "value": round(0.3 * 7 * 40, 1), "rpe": "very_hard", "min": 40}
    d = WS.normalize(_doc(_rpe_step("hard", 30)), rpe_model=RL.Model(factor=0.5))
    assert d["items"][0]["dur"]["value"] == 75.0
    # a typed TSS step is unchanged
    assert WS.normalize(_doc({**_rpe_step(), "dur": {"type": "load", "value": 75}}))["items"][0]["dur"] == \
        {"type": "load", "value": 75}
    for bad, msg in ((_rpe_step("so-so"), "RPE 要是"), (_rpe_step("hard", 0), "分鐘"),
                     (_rpe_step("max", 360), "超過 500 TSS")):
        with pytest.raises(WS.StepsError) as e:
            WS.normalize(_doc(bad))
        assert any(msg in x for x in e.value.errors), e.value.errors
    with pytest.raises(WS.StepsError) as e:
        WS.normalize(_doc({**_rpe_step(), "kind": "warm"}))
    assert "「負荷」只能用在主課" in e.value.errors


def test_rpe_step_is_timed_by_its_minutes_and_viewed():
    c = WS.Ctx(cp=300.0, lthr=170.0, aet=150.0, rpe=RL.Model())
    d = WS.normalize(_doc(_rpe_step("very_hard", 40)))
    tss = 0.3 * 7 * 40
    st = d["items"][0]
    f = math.sqrt(tss / (100 * 40 / 60))
    assert WS.load_if(st, WS.resolve(st, c)) == pytest.approx(f, abs=1e-4)
    t = WS.totals(d, c)
    assert t["sec"] == 2400 and t["tss"] == pytest.approx(tss, abs=0.1) and "RPE" in t["est_note"]
    v = WS.view(d, c)
    o = v["order"][0]
    want = TL.Model.of().tl(tss, "hr", round(f, 4))
    assert o["load"]["tss"] == tss and o["load"]["tl"] == round(want["tl"]) and o["sec"] == 2400
    assert o["load"]["rpe"] == {"level": "very_hard", "min": 40, "factor": 0.3, "fitted": False,
                                "err_pct": round(RL.DEFAULT_ERR * 100)}
    assert WS.fmt_dur(st["dur"]) == f"負荷 {tss:g} TSS（很累 40 分）"
    assert v["watch"]["lines"][0]["dur"] == f"負荷 {round(want['tl'])} TL"


def test_push_still_sends_coros_tl():
    th = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}
    doc = _doc({"id": "w", "kind": "warm", "dur": {"type": "time", "value": 600}, "target": {"type": "none"}},
               _rpe_step("hard", 30, target={"type": "hr", "mode": "abs", "lo": 140, "hi": 146}))
    s = {"id": "s1", "key": "u1", "kind": "quality", "title": "RPE 負荷", "minutes": 40, "day": "2026-10-06",
         "steps": doc}
    spec = CW.session_workout(s, th)
    ex = spec.payload["exercises"][1]
    tss = 0.3 * 5 * 30
    f = round(math.sqrt(tss / 50.0), 4)
    want = round(TL.Model.of().tl(tss, "hr", f)["tl"])
    assert ex["targetType"] == 6 and ex["targetValue"] == want
    assert spec.load_steps == [{"i": 1, "n": 2, "tss": tss, "tl": want, "basis": "hr", "if": f, "f": 1.0}]


# ---------------------------------------------------------------------------
# the editor (static/workout_editor.js)
# ---------------------------------------------------------------------------

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_editor_payload_by_rpe():
    js = r"""
const vm = require("vm"), fs = require("fs"), w = {};
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), { window: w, document: {} });
const E = w.WorkoutEditor, D = { type: "load", value: 75 };
const toRpe = E.loadDur(D, "lmode", "rpe", 42.4);
const lvl = E.loadDur(toRpe, "lrpe", "max"), mins = E.loadDur(lvl, "lmin", "25");
const ctx = { provider: { label: "COROS", capabilities: { load_unit: "TL" } } };
const r = { load: { tss: 84, tl: 110, err: 33, sec: 2400, rpe: { level: "very_hard", min: 40, factor: 0.3, fitted: false } } };
console.log(JSON.stringify({
  toRpe, lvl, mins,
  badLvl: E.loadDur(toRpe, "lrpe", "meh"), badMin: E.loadDur(toRpe, "lmin", "0"), bigMin: E.loadDur(toRpe, "lmin", "400"),
  back: E.loadDur({ ...mins, value: 50 }, "lmode", "tss"),
  html: E.loadInput(ctx, { kind: "work", dur: { type: "load", value: 84, rpe: "very_hard", min: 40 } }, r),
  plain: E.loadInput(ctx, { kind: "work", dur: { type: "load", value: 75 } }, null),
}));
"""
    r = subprocess.run([NODE, "-e", js, str(ROOT / "static" / "workout_editor.js")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    g = json.loads(r.stdout)
    assert g["toRpe"] == {"type": "load", "value": 75, "rpe": "hard", "min": 42}
    assert g["lvl"]["rpe"] == "max" and g["mins"] == {"type": "load", "value": 75, "rpe": "max", "min": 25}
    assert g["badLvl"] is None and g["badMin"] is None and g["bigMin"] is None
    assert g["back"] == {"type": "load", "value": 50}                     # back to TSS: the computed value
    h = g["html"]
    assert 'data-f="lmode"' in h and 'value="rpe" selected' in h and 'data-f="lrpe"' in h
    assert 'value="very_hard" selected' in h and 'data-f="lmin"' in h and 'value="40"' in h
    assert "≈ 84 TSS ≈ 110 TL" in h and 'data-f="tss"' not in h
    assert all(f'value="{k}"' in h for k in ("easy", "moderate", "hard", "very_hard", "max"))
    assert 'data-f="tss"' in g["plain"] and 'value="tss" selected' in g["plain"]
