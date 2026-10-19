"""
SP-723 — the trail plan's segment split by the athlete's own slope cost (downhill included):

1. the whole-race moving time is unchanged; each segment's share follows effort km × cost(grade),
   the cost curve fitted from the athlete's own runs (within-activity ratios), shrunk to the default;
2. leave-one-run-out (synthetic runs with a known slope cost): the cumulative-time error at the
   segment ends is smaller than the effort-km split and the power model's split.
"""
from __future__ import annotations

import math
import random

import numpy as np
from pytest import approx

from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import pacing as PC
from backend.engine.racepower import planner as PL
from backend.engine.racepower import trailhr as TH
from backend.tests.test_racepower_v2 import RE0, fake_v1, synthetic_track

DIV = 153.0
TRUE = (2.4, 1.4, 1.0, 0.85, 0.5)           # the synthetic athlete's own curve: steeper than the default


def test_slope_cost_is_linear_between_the_bins_and_flat_outside():
    fit = {"cost": [1.6, 1.2, 1.0, 0.9, 0.7]}
    assert TH.slope_cost(fit, 0.0) == approx(1.0)
    assert TH.slope_cost(fit, -0.15) == approx(1.4)
    assert TH.slope_cost(fit, 0.05) == approx(0.95)
    assert TH.slope_cost(fit, -0.5) == approx(1.6) and TH.slope_cost(fit, 0.4) == approx(0.7)
    assert TH.slope_cost(None, 0.0) == approx(TH.SLOPE["default"][2])


def _segs(rnd, n=12):
    out = []
    for _ in range(n):
        g = rnd.uniform(-0.25, 0.25)
        d = rnd.uniform(400, 1500)
        out.append({"grade": g, "dist_m": d, "gain_m": max(0.0, g) * d, "loss_m": max(0.0, -g) * d})
    return out


def _run(rnd, cost=TRUE):
    segs = _segs(rnd)
    pace = rnd.uniform(400, 700)                         # s per effort km
    ts = [(s["dist_m"] / 1000 + s["gain_m"] / DIV) * pace * TH.slope_cost({"cost": cost}, s["grade"])
          * rnd.lognormvariate(0, 0.08) for s in segs]
    return segs, ts


def test_fit_follows_the_athletes_runs_and_few_runs_are_the_default():
    rnd = random.Random(1)
    runs = [_run(rnd) for _ in range(40)]
    rows = [TH.slope_row(s, t, DIV) for s, t in runs]
    fit = TH.fit_slope_cost(rows)
    assert fit["n_runs"] == 40 and fit["applied"]
    # the ratios are relative to each run's mean (only the shape matters for a split): this athlete's
    # curve is steeper than the default, and so is the fit (plenty of minutes: barely shrunk)
    got = fit["cost"][0] / fit["cost"][4]
    assert got > TH.SLOPE["default"][0] / TH.SLOPE["default"][4]
    assert fit["cost"] == approx(fit["own"], rel=0.03)
    assert fit["cost"][0] > fit["cost"][1] > fit["cost"][2] > fit["cost"][3] > fit["cost"][4]
    few = TH.fit_slope_cost(rows[:2])
    assert few["cost"] == list(TH.SLOPE["default"]) and few["n_runs"] == 2


def test_slope_times_keep_the_total_and_move_time_onto_descents():
    segs = [{"grade": 0.2, "dist_m": 1000, "gain_m": 200}, {"grade": -0.2, "dist_m": 1000, "gain_m": 0}]
    ts = [1000.0, 600.0]
    out = TH.slope_times(segs, ts, {"applied": True, "cost": list(TH.SLOPE["default"])}, DIV)
    assert sum(out) == approx(sum(ts))
    e_up, e_dn = 1 + 200 / DIV, 1.0
    assert out[1] / out[0] == approx((e_dn * 1.61) / (e_up * 0.74))
    assert TH.slope_times(segs, ts, None) is None


def _score(segs_act, ts):
    T = sum(segs_act)
    ts = np.asarray(ts) * T / sum(ts)
    return float(np.mean(np.abs(np.cumsum(ts) - np.cumsum(segs_act))) / T)


def test_leave_one_run_out_cumulative_error_smaller_than_now():
    rnd = random.Random(7)
    runs = [_run(rnd) for _ in range(30)]
    model = PC.RunModel(62.0, GM.GradeRE(RE0).re, GM.GradeRE(RE0).v_max)
    new, eff, power = [], [], []
    for i, (segs, ts) in enumerate(runs):
        fit = TH.fit_slope_cost([TH.slope_row(s, t, DIV) for j, (s, t) in enumerate(runs) if j != i])
        new.append(_score(ts, TH.slope_times(segs, ts, fit, DIV)))
        eff.append(_score(ts, [s["dist_m"] / 1000 + s["gain_m"] / DIV for s in segs]))
        res = PC.solve_power_mode(155.0, [dict(s, M=1.0) for s in segs], model)
        power.append(_score(ts, [r["t"] for r in res["rows"]]))
    assert np.mean(new) < np.mean(eff) and np.mean(new) < np.mean(power)


def _plan(slope):
    tr = synthetic_track({"len": 30000, "z": lambda x: 200 + 150 * math.sin(x / 2500.0)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 30.0, c["totals"]["gain_m"])
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9,
         "slope_cost": slope}
    return PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(RE0), effort_validated=False, opts={"mode": "auto"},
                       validated={"trail": True}, trail_hr=m)


def test_planner_keeps_the_total_and_resplits_by_the_slope_cost():
    old = _plan(None)
    fit = {"applied": True, "cost": list(TH.SLOPE["default"]), "n_runs": 12}
    new = _plan(fit)
    so, sn = old["segments"], new["segments"]
    assert new["summary"]["time_s"] == approx(old["summary"]["time_s"], abs=1.0)
    assert sum(s["t"] for s in sn) == approx(new["summary"]["time_s"], abs=1.0)
    assert new["summary"]["slope_split"]["applied"] and old["summary"]["slope_split"] is None
    div = TH.effort_divisor()
    mbar = new["summary"]["M"]
    # × the segment's altitude / heat factor relative to the race (M̄ ÷ Mᵢ)
    raw = [(s["dist_m"] / 1000 + (s["gain_m"] or 0) / div) * TH.slope_cost(fit, s["grade"]) * mbar / s["M"] for s in sn]
    shares = [s["t"] / new["summary"]["time_s"] for s in sn]
    assert shares == approx([x / sum(raw) for x in raw], rel=1e-3)
    down = [i for i, s in enumerate(sn) if s["grade"] < -0.02]
    assert down and sum(sn[i]["t"] for i in down) > sum(so[i]["t"] for i in down)     # descents get more time
    assert any("坡度成本" in w for w in new["warnings"])
