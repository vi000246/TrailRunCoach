"""Heat bands for the drift (workout_review.temp_band; 2026-10-02): no refusal
above 25 °C any more — every drift carries its band (< 25 / 25–28 / > 28 °C,
推估 cut-offs) and runs are compared only within a band. Gates still apply:
a pass in heat unlocks (heat inflates the drift: conservative), a fail in
heat is marked 「熱環境，結果可能偏高」. Synthetic data only — never the
user's WKO5 folder, plan or DB (conftest points routes.HOME at a temp folder)."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import base_check as BC
from backend.engine import drift_agg as DA
from backend.engine import plan_prefs as PP
from backend.engine import quality_gate as QG
from backend.engine import workout_review as R
from backend.engine.status import Status
from backend.tests.test_base_check_reentry import _xu_workout
from backend.tests.test_quality_gate import TODAY, _ds, _plan
from backend.tests.test_workout_review import _run


def _temps(ds, temps):
    """ds.activity_temps: {file: °C} per workout in `temps` order (None = no temperature)."""
    ws = sorted(ds.workouts, key=lambda w: w.day)
    ds.activity_temps = {w.entry.file: t for w, t in zip(ws, temps) if t is not None}
    return ws


# ---------------------------------------------------------------------------
# a hot run is kept, with its band
# ---------------------------------------------------------------------------

def test_a_hot_run_is_kept_with_its_band_in_measure_and_drift_series():
    ds = _ds([_run(TODAY - dt.timedelta(days=d), minutes=52) for d in (3, 2, 1)])
    ws = _temps(ds, [22.0, 27.0, 31.0])
    bands = [R.measure(ds, w)["drift"]["temp_band"] for w in ws]
    assert bands == ["cool", "warm", "hot"]
    for w in ws:
        dr = R.measure(ds, w)["drift"]
        assert dr["ok"] and R.basis_drift(dr, "pace")[0] is not None     # the strict tier, not refused
        assert dr["heat"] == (dr["temp_band"] != "cool")
    pts = R.drift_series(ds, TODAY)
    assert [p["band"] for p in pts] == ["cool", "warm", "hot"] and all(p["drift"] is not None for p in pts)


# ---------------------------------------------------------------------------
# within-band aggregation
# ---------------------------------------------------------------------------

def test_pick_band_prefers_the_latest_band_with_two_runs():
    p = lambda b: {"band": b, "drift": 0.03}                              # noqa: E731
    assert DA.pick_band([]) is None
    assert DA.pick_band([p("cool"), p("cool"), p("hot"), p("hot")]) == "hot"
    # the latest band has one run: the band with the most
    assert DA.pick_band([p("cool"), p("cool"), p("cool"), p("hot")]) == "cool"
    assert DA.pick_band([p("none"), p("warm"), p("warm"), p("hot")]) == "warm"


def test_rolling_aggregates_only_within_a_band():
    days = (20, 17, 14, 11, 8, 5, 2)
    temps = [22.0, 30.0, 22.0, 30.0, 22.0, 30.0, 22.0]
    # cool runs barely drift, hot runs drift a lot
    ws = [_run(TODAY - dt.timedelta(days=d), minutes=52, hr=135.0, hr_end=137.0 if t < 25 else 146.0)
          for d, t in zip(days, temps)]
    ds = _ds(ws)
    order = _temps(ds, temps)
    roll = DA.rolling(ds, "pace")
    single = {w.idx: R.basis_drift(R.measure(ds, w)["drift"], "pace", ref=True)[0] for w in order}
    cool = [w.idx for w, t in zip(order, temps) if t < 25]
    hot = [w.idx for w, t in zip(order, temps) if t >= 25]
    last_cool, last_hot = roll[cool[-1]], roll[hot[-1]]
    assert last_cool["band"] == "cool" and last_cool["n"] == len(cool)
    assert last_hot["band"] == "hot" and last_hot["n"] == len(hot)
    assert max(single[i] for i in cool) < min(single[i] for i in hot)
    assert last_cool["mean"] <= max(single[i] for i in cool) + 1e-9          # no hot run mixed in
    assert last_hot["mean"] >= min(single[i] for i in hot) - 1e-9
    # the evaluator: drift_avg(…, band) plots one band; drift(…, …, band) filters the single runs
    from backend.engine.wko5expr.evaluator import Evaluator
    ev = Evaluator(ds, order[0].day, order[-1].day)
    w_hot = next(w for w in order if w.idx == hot[-1])
    assert ev.evaluate('drift_avg("pace", "mean", "hot")', w_hot) == pytest.approx(last_hot["mean"])
    assert np.isnan(ev.evaluate('drift_avg("pace", "mean", "cool")', w_hot))
    assert ev.evaluate('drift_avg("pace")', w_hot) == pytest.approx(last_hot["mean"])   # "same" band
    assert np.isnan(ev.evaluate('drift("pace", "test", "cool")', w_hot))
    assert ev.evaluate('drift("pace", "test", "hot")', w_hot) == pytest.approx(single[hot[-1]])
    assert ev.evaluate('drift("pace")', w_hot) == pytest.approx(single[hot[-1]])        # every band


def test_the_overview_drift_indicator_compares_within_one_band():
    days = (12, 10, 8, 6, 4, 2)
    temps = [22.0, 22.0, 22.0, 30.0, 30.0, 30.0]
    # hot runs drift > 10 % (strict tier): BAD in the cool band, but not in heat
    ws = [_run(TODAY - dt.timedelta(days=d), minutes=52, hr=133.0 if t < 25 else 118.0,
               hr_end=134.0 if t < 25 else 168.0) for d, t in zip(days, temps)]
    for w in ws:
        w.metrics["tss"] = 40.0
    plan = _plan(lthr=165, day="2026-09-01")
    ds = _ds(ws, plan)
    _temps(ds, temps)
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    d = next(i for i in st.indicators if i.id == "drift")
    # the latest runs are hot: the number is the hot band's mean, chipped, the cool band apart
    assert d.extra["band"] == "hot" and d.extra["heat"] and d.extra["chip"] == "🌡 > 28 °C"
    assert d.extra["fair"] == 3 and d.extra["bands"]["cool"]["n"] == 3 and d.extra["bands"]["hot"]["n"] == 3
    assert d.extra["agg"]["mean"] == pytest.approx(d.extra["bands"]["hot"]["agg"]["mean"])
    assert d.extra["agg"]["mean"] > d.extra["bands"]["cool"]["agg"]["mean"]
    assert "🌡 > 28 °C" in d.text and R.HEAT_NOTE in d.why and "< 25 °C 3 次" in d.why
    assert d.extra["agg"]["mean"] > 0.10 and d.extra["test"] == 3
    assert d.level != "bad" and R.HEAT_NOTE in d.verdict and not d.action   # a high drift in heat never warns


def test_the_aet_aggregate_reads_the_cool_band_only():
    ws = [_run(TODAY - dt.timedelta(days=d), minutes=52) for d in (6, 4, 2)]
    ds = _ds(ws)
    order = _temps(ds, [22.0, 27.0, None])
    pts = DA.aet_points(ds, TODAY)
    assert [p["idx"] for p in pts] == [order[0].idx, order[2].idx]           # cool + no temperature


# ---------------------------------------------------------------------------
# the gates
# ---------------------------------------------------------------------------

def test_friel_unlocks_on_a_pass_in_heat_and_marks_a_fail_in_heat():
    plan = _plan(aethr=140, lthr=165)
    flat = _run(TODAY - dt.timedelta(days=5), minutes=80, hr=140.0)
    ds = _ds([flat], plan)
    _temps(ds, [30.0])
    r = QG.friel_check(ds, TODAY, 140.0)
    assert r["state"] == "unlocked" and r["run"]["band"] == "hot" and r["run"]["heat"]
    assert "熱天通過仍算數" in QG.heat_suffix(r["run"], True)
    # a fail in heat: still a fail, reported as possibly heat-inflated
    rising = _run(TODAY - dt.timedelta(days=5), minutes=80, hr=131.0, hr_end=149.0)
    ds = _ds([rising], plan)
    _temps(ds, [27.0])
    r = QG.friel_check(ds, TODAY, 140.0)
    assert r["state"] == "locked" and r["run"]["band"] == "warm" and r["run"]["drift"] >= QG.FRIEL_GOOD
    g = QG.evaluate(ds, plan, TODAY, PP.Prefs(quality_gate="friel_drift"), {}, None)
    assert g["state"] == "locked" and "可能是熱造成的" in g["verdict"] and "🌡 25–28 °C" in g["verdict"]
    # cool: no heat words
    ds = _ds([rising], plan)
    _temps(ds, [22.0])
    g = QG.evaluate(ds, plan, TODAY, PP.Prefs(quality_gate="friel_drift"), {}, None)
    assert g["state"] == "locked" and "熱環境" not in g["verdict"]


def test_xu_90_minute_test_counts_in_heat():
    ds = _ds([_xu_workout(TODAY - dt.timedelta(days=3))])
    _temps(ds, [29.0])
    r = BC.xu_run(ds, ds.workouts[0])
    assert r["ok"] and r["band"] == "hot" and r["heat"]                       # > 25 °C no longer refuses
    assert "熱天通過仍算數" in BC.xu_text(r)
    assert QG.xu_check(ds, TODAY)["state"] == "unlocked"
    bad = _ds([_xu_workout(TODAY - dt.timedelta(days=3), rise=0.12)])
    _temps(bad, [27.0])
    rb = BC.xu_run(bad, bad.workouts[0])
    assert not rb["ok"] and any("可能是熱造成的" in x for x in rb["why"])
    assert not any("°C（> 25 °C）" in x for x in rb["why"])
