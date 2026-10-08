"""
SP-369 (with SP-367's answer): two ? helps on the race calculator, and the behaviour they describe.

* 「以訓練條件功率輸入（會再乘上環境係數 M）」: the target power (W, or %CP × CP) is taken as a
  training-conditions number and multiplied by the race's environment factor M (the
  distance-weighted mean of the segments' M) before the finish time is computed
  (engine/racepower/planner.py, mode "power").
* 「effort km 公式」: in 目標功率 mode it moves the finish time; in 目標時間 mode the finish time is the
  target and it moves the power needed; with no trail heart-rate model the model prediction uses it
  too. (With the trail HR model the time keeps that model's own divisor: trailhr.effort_divisor.)

API through test_racepower_export's client (fake inputs, no trail HR model); the page and both
catalogs read from the repo. No WKO5 folder, no DB.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from backend.tests.test_racepower_export import client  # noqa: F401  (fixture)

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "racepower.html"
HOT = {"altitude_m": 100.0, "temp_c": 32.0, "rh_pct": 75.0}


def plan(c, **body):
    r = c.post("/api/v1/racepower/plan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_training_power_is_multiplied_by_M(client):  # noqa: F811
    base = {"type": "road", "distance_km": 10, "course": {"manual": {"km": 10, "split": "km"}},
            "mode": "power", "env_to": HOT}
    raw = plan(client, **base, target_power=250)
    tr = plan(client, **base, target_power=250, power_is_training=True)
    m = tr["summary"]["M"]
    assert m < 0.99                                            # a hot race day: M below 1
    assert raw["summary"]["power"] == pytest.approx(250, abs=0.5)
    assert tr["summary"]["power"] == pytest.approx(250 * m, abs=0.5)
    assert tr["summary"]["time_s"] > raw["summary"]["time_s"]
    # %CP the same way (CP 300 in the fake inputs)
    pct = plan(client, **base, target_pct_cp=0.8, power_is_training=True)
    assert pct["summary"]["power"] == pytest.approx(240 * pct["summary"]["M"], abs=0.5)


def test_effort_formula_moves_the_time_in_power_mode_and_the_power_in_time_mode(client):  # noqa: F811
    trail = {"type": "trail", "distance_km": 20, "gain_m": 1200,
             "course": {"manual": {"km": 20, "gain": 1200, "split": "km"}}}
    pw = {f: plan(client, **trail, mode="power", target_power=250, effort_formula=f)["summary"]
          for f in ("fitted_run", "itra")}
    assert abs(pw["fitted_run"]["time_s"] - pw["itra"]["time_s"]) > 60
    tm = {f: plan(client, **trail, mode="time", target_time_s=3 * 3600, effort_formula=f)["summary"]
          for f in ("fitted_run", "itra")}
    assert tm["fitted_run"]["time_s"] == pytest.approx(tm["itra"]["time_s"], abs=1.0)
    assert abs(tm["fitted_run"]["power"] - tm["itra"]["power"]) > 2
    # no trail HR model here: the model prediction uses the formula as well
    au = {f: plan(client, **trail, mode="auto", effort_formula=f)["summary"] for f in ("fitted_run", "itra")}
    assert abs(au["fitted_run"]["time_s"] - au["itra"]["time_s"]) > 60


def test_the_trail_hr_model_keeps_its_own_divisor():
    """The 模型預測 total (trail HR model) never sees effort_formula."""
    from backend.engine.racepower import trailhr as TH
    src = inspect.getsource(TH.effort_divisor)
    assert 'divisor_of("fitted_run")' in src
    assert "effort_formula" not in inspect.getsource(TH)


# ---- the page --------------------------------------------------------------------------

def _after(src, anchor, n=700):
    i = src.index(anchor)
    return src[i:i + n]


@pytest.mark.parametrize("anchor,key", [('<input type="checkbox" id="ptrain">', "ptrain.tip"),
                                        ('<select id="formula"', "formula.tip")])
def test_help_next_to_the_control(anchor, key):
    src = PAGE.read_text("utf-8")
    near = _after(src, anchor, 1200)
    assert re.search(r'<span class="help[^"]*" tabindex="0">\?<span class="tip" data-i18n="racepower\.' + re.escape(key), near)
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "racepower.json").read_text("utf-8"))
    assert zh.get(key) and en.get(key)
    assert not re.search(r"[一-鿿]", en[key])


def test_help_texts_say_what_the_code_does():
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "racepower.json").read_text("utf-8"))
    assert "環境係數 M" in zh["ptrain.tip"] and "依距離平均" in zh["ptrain.tip"] and "不勾" in zh["ptrain.tip"]
    assert "environment factor M" in en["ptrain.tip"]
    f = zh["formula.tip"]
    # the mode buttons as a road / trail user sees them (racepower.goal.pace / .power / .model)
    for label in (zh["goal.model"], zh["goal.power"], zh["goal.pace"]):
        assert f"「{label}」" in f, label
    assert "擬合" in f and "改到完賽時間" in f and "目標時間" not in f
    assert "GPX" in f and "分段模型" in f and "不影響" in f            # the validated segment model (v2_primary)
    e = en["formula.tip"]
    for label in (en["goal.model"], en["goal.power"], en["goal.pace"]):
        assert f"“{label}”" in e, label
    assert "GPX" in e and "segment model" in e
    assert zh.get("ptrain.label") and en.get("ptrain.label")


def test_effort_formula_does_nothing_on_the_validated_segment_model(client, monkeypatch):  # noqa: F811
    """A GPX course with the trail back-test passed and no HR estimate (planner v2_primary): every mode
    solves on the segment model, so the formula moves neither the time nor the power."""
    from backend.engine.racepower import backtest as BT
    from backend.engine.racepower import gpx as GPX
    from backend.tests.test_racepower_v2 import synthetic_track
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": True, "hike": False}, False))
    tr = synthetic_track({"len": 16000, "z": lambda x: 300 + (x * 0.08 if x < 8000 else (16000 - x) * 0.08)})
    cid = client.post("/api/v1/racepower/course",
                      files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")}).json()["course_id"]
    base = {"type": "trail", "course": {"course_id": cid}}
    for mode, extra in (("power", {"target_power": 250}), ("time", {"target_time_s": 2.5 * 3600}), ("auto", {})):
        s = {f: plan(client, **base, mode=mode, effort_formula=f, **extra)["summary"] for f in ("fitted_run", "itra")}
        assert s["fitted_run"]["time_s"] == pytest.approx(s["itra"]["time_s"], abs=1.0), mode
        assert s["fitted_run"]["power"] == pytest.approx(s["itra"]["power"], abs=0.5), mode
