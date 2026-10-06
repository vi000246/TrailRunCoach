"""
SP-222: the segment ETAs carry the athlete's own late-race fade (engine/racepower/trailhr.py
fade_run / fade_shape / fade_times, planner._fade). The whole-race time stays the HR model's; only
its spread over the segments changes. Synthetic runs only: no WKO5 folder, no app DB.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from pytest import approx

from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.engine.racepower import trailhr as TH
from backend.tests.test_racepower_v2 import RE0, fake_v1, synthetic_track


def _run(profile, mults, hr=150.0, seed=0):
    """A 1-Hz synthetic trail run: grade g(t) from `profile`; speed = the grade speed × the
    multiplier of the moving hour (`mults[hour]`) × noise; constant HR."""
    rng = np.random.default_rng(seed)
    hours = len(mults)
    t = np.arange(0, hours * 3600.0)
    g = np.array([profile(x / 3600.0) for x in t])
    v0 = np.where(g > 0, 2.8 * np.exp(-8 * g), 2.8 * np.exp(3 * g) + 0.3)
    m = np.asarray(mults)[np.minimum((t // 3600).astype(int), hours - 1)]
    v = v0 * m * np.exp(0.03 * rng.standard_normal(len(t)))
    d = np.cumsum(v)
    z = 500.0 + np.cumsum(v * g)
    return t, d, z, np.full(len(t), hr), np.ones(len(t), bool)


def _fade_of(mults, seed=0, profile=None):
    t, d, z, hr, mv = _run(profile or (lambda h: 0.10 * math.sin(2 * math.pi * h * 3)), mults, seed=seed)
    return TH.fade_run(TH.terrain_windows(t, d, z, hr, mv))


def test_fade_run_reads_the_speed_by_hour_at_the_same_grade_and_hr():
    r = _fade_of([1.0, 0.97, 0.92, 0.88], seed=1)
    assert r is not None and [b for b, _c, _s in r["bins"]] == [1, 2, 3]
    for (b, c, s), want in zip(r["bins"], (0.97, 0.92, 0.88)):
        assert math.exp(c) == approx(want, abs=0.02) and 0 < s < 0.02


def test_fade_run_needs_three_hours_and_shared_terrain():
    assert _fade_of([1.0, 0.95]) is None                               # only two hours
    updown = lambda h: 0.12 if h < 1.5 else -0.12                       # climb first, descend after
    r = _fade_of([1.0, 1.0, 1.0], profile=updown)
    assert r is None or all(abs(c) < 0.05 for _b, c, _s in r["bins"])


def _rows(n, betas=(-0.03, -0.08, -0.12), se=0.01):
    return [{"bins": [[i + 1, b, se] for i, b in enumerate(betas)], "n": 300, "hours": 4.0} for _ in range(n)]


def test_fade_shape_needs_enough_own_runs_and_shrinks_toward_even():
    few = TH.fade_shape(_rows(2), n_long=5)
    assert not few["applied"] and few["points"] == [[0.5, 1.0]] and few["n_runs"] == 2 and few["n_long"] == 5
    sh = TH.fade_shape(_rows(3))
    w = 3 / (3 + TH.FADE["k"])
    assert sh["applied"] and sh["shrink"] == approx(w)
    assert [p[0] for p in sh["points"]] == [0.5, 1.5, 2.5, 3.5]
    assert [p[1] for p in sh["points"]][1:] == approx([math.exp(w * b) for b in (-0.03, -0.08, -0.12)])
    # the hours fewer than min_runs runs reach end the shape (held flat after it)
    rows = _rows(3, betas=(-0.03, -0.08)) + [{"bins": [[1, -0.03, 0.01], [2, -0.08, 0.01], [3, -0.5, 0.01]]}]
    sh = TH.fade_shape(rows)
    assert len(sh["points"]) == 3 and TH.fade_mult(sh, [10.0])[0] == approx(sh["points"][-1][1])


def test_fade_times_keep_the_total_earlier_etas_and_slower_late_segments():
    shape = TH.fade_shape(_rows(6))
    ts = [1800.0] * 12                                                  # a 6 h race in 30-min segments
    out = TH.fade_times(ts, shape)
    assert abs(sum(out) - sum(ts)) < 1.0
    assert out[0] < ts[0] and out[-1] > ts[-1]
    cum, cum0 = np.cumsum(out), np.cumsum(ts)
    assert all(a < b for a, b in zip(cum[:-1], cum0[:-1]))              # every aid station earlier
    assert TH.fade_times(ts, TH.fade_shape(_rows(1))) is None and TH.fade_times(ts, None) is None


def _plan(fade, kind="trail"):
    tr = synthetic_track({"len": 30000, "z": lambda x: 200 + 150 * math.sin(x / 2500.0)})
    c = CO.build_course(tr)
    v1 = fake_v1(kind, 30.0, c["totals"]["gain_m"])
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9,
         "fade": fade}
    return PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(RE0), effort_validated=False, opts={"mode": "auto"},
                       validated={"trail": True}, trail_hr=m)


def test_planner_spreads_the_hr_total_by_the_athletes_fade():
    even = _plan(None)
    faded = _plan(TH.fade_shape(_rows(6)))
    se, sf = even["segments"], faded["segments"]
    assert faded["summary"]["time_s"] == approx(even["summary"]["time_s"], abs=1.0)
    assert abs(sum(s["t"] for s in sf) - faded["summary"]["time_s"]) < 1.0
    half = len(se) // 2
    assert sf[half]["cum_s"] < se[half]["cum_s"]                         # the middle station earlier
    assert sf[-1]["t"] > se[-1]["t"] and sf[0]["t"] < se[0]["t"]          # late segments slower
    assert sf[-1]["power"] == approx(se[-1]["power"], rel=1e-6)           # powers stay the allocation's
    assert faded["summary"]["fade"]["applied"] and all(s["badge"] == "推估" for s in sf)
    w = next(w for w in faded["warnings"] if "速度變化" in w)
    assert "6 次" in w and "第 2 到第 4 小時" in w and " / ".join(f"{p[1]:.0%}" for p in TH.fade_shape(_rows(6))["points"][1:]) in w
    assert not any("平均功率只能到" in w for w in faded["warnings"])


def test_planner_keeps_even_segments_without_enough_own_runs():
    even = _plan(None)
    few = _plan(TH.fade_shape(_rows(2), n_long=4))
    assert [s["t"] for s in few["segments"]] == approx([s["t"] for s in even["segments"]])
    assert not few["summary"]["fade"]["applied"]
    assert any("分段維持平均分配" in w and "2 次" in w and "共 4 次" in w for w in few["warnings"])
    assert even["summary"]["fade"] is None


def test_road_plans_do_not_fade():
    tr = synthetic_track({"len": 10000, "z": lambda x: 50.0})
    c = CO.build_course(tr)
    common = dict(v1=fake_v1("road", 10.0, 0.0), course=c, grade_re=GM.GradeRE(RE0), effort_validated=False,
                  opts={"mode": "auto"}, validated={"road": True})
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "n": 9,
         "fade": TH.fade_shape(_rows(6))}
    a, b = PL.plan_run(trail_hr=None, **common), PL.plan_run(trail_hr=m, **common)
    assert [s["t"] for s in a["segments"]] == approx([s["t"] for s in b["segments"]])
    assert b["summary"]["fade"] is None


def test_a_long_run_gives_its_fade_row_with_the_durability(monkeypatch):
    """athlete._trail_durability (cached per run) carries trailhr.fade_run for runs ≥ 3 h, None below."""
    from backend.engine.racepower import athlete as A
    for mults, want in (([1.0, 0.97, 0.93, 0.9], True), ([1.0, 0.97, 0.97], False)):
        hours = len(mults) if want else 2.5
        t, d, z, hr, _mv = _run(lambda h: 0.10 * math.sin(2 * math.pi * h * 3), mults, seed=3)
        n = int(hours * 3600)
        t, d, z, hr = t[:n], d[:n], z[:n], hr[:n] + np.random.default_rng(0).normal(0, 2, n)
        kmh = np.gradient(d, t) * 3.6
        monkeypatch.setattr(A, "activity_arrays", lambda ds, w, a={"t": t, "d": d, "z": z, "hr": hr, "kmh": kmh}: a)
        r = A._trail_durability(None, None)
        assert (r.get("fade") is not None) is want
        if want:
            assert [b for b, _c, _s in r["fade"]["bins"]][:2] == [1, 2]
