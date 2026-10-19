"""SP-722: the within-activity durability δ (trailhr step 8) — synthetic runs only (no WKO5
folder, no ~/.wko5coach)."""
import datetime as dt
import math
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine.racepower import athlete as A
from backend.engine.racepower import trailhr as TH


def _run(hours, delta, form=1.0, hr_slope=0.0, hr_coef=0.0, seed=0):
    """A 1-Hz synthetic trail run on 20-min rolling hills: speed = the grade speed × the day's
    form × exp(hr_coef·(HR − 150)) × (1 − δ·(t − 1 h)⁺) × noise; HR = 150 + hr_slope·hours."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, hours * 3600.0)
    h = t / 3600.0
    g = 0.10 * np.sin(2 * math.pi * h * 3 + seed)
    hr = 150.0 + hr_slope * h + rng.standard_normal(len(t))
    v0 = np.where(g > 0, 2.8 * np.exp(-8 * g), 2.8 * np.exp(3 * g) + 0.3)
    v = v0 * form * np.exp(hr_coef * (hr - 150.0)) * (1 - delta * np.clip(h - 1.0, 0, None)) \
        * np.exp(0.03 * rng.standard_normal(len(t)))
    d = np.cumsum(v)
    z = 500.0 + np.cumsum(v * g)
    return {"wins": TH.inside_windows(t, d, z, hr, np.ones(len(t), bool)), "T_h": hours, "idx": seed,
            "date": f"2026-01-{seed + 1:02d}"}


LENGTHS = (1.0, 1.2, 1.6, 1.8, 2.2, 2.6, 3.0, 3.5)


@pytest.mark.parametrize("true", [0.0, 0.06])
def test_inside_delta_recovers_the_fatigue_whatever_the_days_form(true):
    acts = [_run(T, true, form=0.85 + 0.04 * i, seed=i) for i, T in enumerate(LENGTHS)]
    r = TH.inside_delta(acts)
    assert r is not None and r["raw"] == pytest.approx(true, abs=0.015)
    assert r["se"] < 0.02
    # runs under 1.5 h shape the grade curve only; the slope comes from the runs ≥ 1.5 h
    assert r["n"] == sum(T >= 1.5 for T in LENGTHS) and r["n_curve"] == len(LENGTHS)
    assert {x["idx"] for x in r["runs"]} == {i for i, T in enumerate(LENGTHS) if T >= 1.5}


def test_long_runs_weigh_more_in_the_pooled_slope():
    acts = [_run(T, 0.05, seed=i) for i, T in enumerate(LENGTHS)]
    lever = {x["T_h"]: x["lever"] for x in TH.inside_delta(acts)["runs"]}
    assert sum(lever.values()) == pytest.approx(1.0)
    assert lever[3.5] > 5 * lever[1.6]                       # 2.5 h after T0 vs 0.6 h


def test_the_slope_is_at_the_same_hr_like_the_models_delta():
    # HR drifts up 8 bpm/h and buys speed (1 %/bpm): without the HR term the run would look faster
    # late; the model's δ is the loss at the same HR, so it must come out at the true 5 %/h
    acts = [_run(T, 0.05, hr_slope=8.0, hr_coef=0.01, seed=i) for i, T in enumerate(LENGTHS)]
    r = TH.inside_delta(acts)
    assert r["raw"] == pytest.approx(0.05, abs=0.015) and r["hr_coef"] == pytest.approx(0.01, abs=0.004)


def test_warm_up_windows_are_skipped_and_short_runs_give_no_slope():
    a = _run(1.2, 0.0, seed=1)
    assert min(w[2] for w in a["wins"]) < TH.INSIDE["warmup_h"]          # cached with the warm-up …
    assert TH.inside_delta([a]) is None                                   # … no run ≥ 1.5 h → no slope
    assert TH.grade_curve([a]) and TH.inside_delta([]) is None


def test_inside_shrinks_to_the_prior_by_precision():
    p, sd = TH.TRAILHR["delta_prior"], TH.INSIDE["prior_sd"]
    none = TH.shrink_inside(None)
    assert none["delta"] == p and none["n"] == 0 and none["w"] == 0.0 and none["label"] == "推估"
    s = TH.shrink_inside({"raw": 0.0, "se": 0.02, "n": 10})
    assert s["w"] == pytest.approx(0.5) and s["delta"] == pytest.approx(0.5 * p) and s["k"] == pytest.approx(1.0)
    noisy = TH.shrink_inside({"raw": 0.0, "se": 0.06, "n": 10})
    assert noisy["w"] == pytest.approx(sd ** 2 / (sd ** 2 + 0.06 ** 2)) and noisy["delta"] > 0.045
    sure = TH.shrink_inside({"raw": 0.02, "se": 0.002, "n": 30})
    assert sure["delta"] == pytest.approx(0.02, abs=0.001)
    assert TH.shrink_inside({"raw": -0.2, "se": 0.001, "n": 30})["delta"] == 0.0      # clamped
    assert TH.shrink_inside({"raw": 0.5, "se": 0.001, "n": 30})["delta"] == TH.TRAILHR["delta_max"]


# ---- own pace only (SP-722 acceptance 2) --------------------------------------

def _w(file="2026/a.fit", sport_type="trail running", tags=("runningtrail",)):
    return SimpleNamespace(entry=SimpleNamespace(file=file, start=dt.datetime(2026, 5, 1, 7, 0)),
                           sport_type=sport_type, tags=list(tags), sport="run")


def test_group_hikes_are_not_own_paced(tmp_path):
    from backend.engine.activity_key import ByStartSet
    none = ByStartSet()
    tag = lambda t: [{"file": "2026/a.fit", "start_local": "2026-05-01T07:00", "activity_type": t,   # noqa: E731
                      "activity_type_overridden": True}]
    assert A.own_paced(_w(), [], none)
    assert A.own_paced(_w(), tag("race"), none) and A.own_paced(_w(), tag("training"), none)
    assert not A.own_paced(_w(), tag("baiyue_group"), none)
    assert not A.own_paced(_w(), tag("hike"), none)
    assert not A.own_paced(_w(sport_type="hiking", tags=()), [], none)
    solo = ByStartSet(["2026/a.fit"], {})
    assert A.own_paced(_w(), tag("hike"), solo) and A.own_paced(_w(sport_type="hiking", tags=()), [], solo)


# ---- the LOO gate on the runs δ acts on ----------------------------------------

def _pts(delta_true, lengths):
    out = []
    for i, T in enumerate(lengths):
        x = 0.8 + 0.02 * i
        v = (2 + 4 * x) * TH.dbar(T, delta_true)
        out.append({"x": x, "v": v, "T_h": T, "eff_km": v * T})
    return out


def test_gate_scores_only_the_runs_of_at_least_1_5_h():
    # many short runs (δ does not touch them) and 5 long ones that need the real δ 0.10
    pts = _pts(0.10, [0.8, 0.9, 1.0, 1.1, 0.7, 0.85, 0.95, 1.05, 2.0, 2.5, 3.0, 3.5, 4.0])
    good = TH.choose_delta(pts, {"use": 0.10, "all": {"n": 0}, "n_evidence": 5})
    assert good["choice"] == "measured" and good["loo_min_h"] == 1.5
    bad = TH.choose_delta(pts, {"use": 0.0, "all": {"n": 0}, "n_evidence": 5})
    assert bad["choice"] == "prior" and bad["delta"] == TH.TRAILHR["delta_prior"]
    assert bad["loo_measured"] > bad["loo_prior"]


def test_gate_keeps_the_prior_with_fewer_than_four_long_runs():
    pts = _pts(0.10, [0.8, 0.9, 1.0, 1.1, 0.7, 2.0, 3.0, 4.0])
    r = TH.choose_delta(pts, {"use": 0.10, "all": {"n": 0}, "n_evidence": 3})
    assert r["choice"] == "prior" and r["too_few_long"] == 3


# ---- trail_hr_model wiring -------------------------------------------------------

def test_trail_hr_model_uses_the_shrunk_inside_delta(monkeypatch, tmp_path):
    monkeypatch.setattr(A, "SOLO_HIKES", str(tmp_path / "none.json"))
    pts = _pts(0.03, [0.8, 0.9, 1.0, 1.1, 1.6, 2.0 - 1e-6, 1.7, 1.9])   # none ≥ 2 h: no long-run rows
    acts = [_run(T, 0.03, form=0.9 + 0.03 * i, seed=i) for i, T in enumerate(LENGTHS)]
    monkeypatch.setattr(A, "trail_hr_points", lambda ds, runs, exclude=None, heat=True: list(pts) if heat else [])
    monkeypatch.setattr(A, "inside_acts", lambda ds, today, exclude=None, tags=None: acts)
    ds = SimpleNamespace(workouts=[], flush_series=lambda: None)
    m = A.trail_hr_model(ds, dt.date(2026, 10, 8), race_idx=set(), tags=[])
    ins = m["delta_info"]["inside"]
    assert ins["n"] == 6 and ins["raw"] == pytest.approx(0.03, abs=0.015) and 0.03 < ins["delta"] < 0.05
    assert m["delta_info"]["n_evidence"] == 6 and m["delta_info"]["use"] == ins["delta"]
    assert m["delta_info"]["gate"]["loo_min_h"] == 1.5
    assert m["delta"] == m["delta_info"]["gate"]["delta"]


def test_rc7_names_the_personal_value_or_why_the_prior():
    from backend.engine.racepower import planner as PL
    base = {"delta": 0.039, "delta_measured": {"choice": "measured", "inside": {"n": 28, "raw": -0.006}}}
    assert "28 筆自己步調" in PL.durability_text(base) and "3.9%" in PL.durability_text(base)
    prior = {"delta": 0.05, "delta_measured": {"choice": "prior", "inside": {"n": 28, "raw": -0.006}}}
    assert "先驗" in PL.durability_text(prior) and "+0.6%" in PL.durability_text(prior)
    assert "還沒有" in PL.durability_text({"delta": 0.05, "delta_measured": {"inside": {"n": 0}}})
    assert "−5.0%" in PL.durability_text({"delta": 0.05})
