"""SP-588 (first step): the 個人化間歇 targets — six levels, each a rep duration and a rep power from
the runner's PD model (algorithms/wko5_levels.targets; formulas.md §6.9b). WKO5 computes no reps and
no rests; how the 課表 uses the targets (SP-590, renumbered from SP-588) is test_personal_intervals_sp590.py.
Synthetic only."""
from __future__ import annotations

import math

import pytest

from backend.engine.algorithms import wko5_levels as L
from backend.engine.algorithms.wko5_pdmodel import model

PARAMS = [15000.0, 20.0, 250.0, 25.0, 2400.0, -30.0]      # FRC (J), tau1, FTP, tau2, TTE, D
TTE = 2100.0


def test_the_six_levels_follow_the_formula():
    ln2 = math.log(2)
    want_d = [TTE, 0.9625 * 15000 / (0.0375 * 250), 0.1625 * 15000 / (0.032 * 250),
              5 * ln2 * 20, 3 * ln2 * 20, ln2 * 20]
    got = L.targets(PARAMS, TTE)
    assert [d for d, _p in got] == pytest.approx(want_d)
    assert [p for _d, p in got][:3] == pytest.approx([250.0, 255.0, 300.0])
    for (d, p) in got[3:]:
        assert p == pytest.approx(model(PARAMS, d))
    assert [d for d, _p in got][3:] == sorted([d for d, _p in got][3:], reverse=True)   # 3 > 4 > 5


def test_out_of_range_and_no_model():
    assert math.isnan(L.target_duration(6, PARAMS, TTE)) and math.isnan(L.target_power(-1, PARAMS, TTE))
    assert math.isnan(L.target_duration(0, None, TTE))


def test_the_evaluator_reads_the_same_numbers():
    """targetduration / targetpower(i, meanmax) — a user's own view — and the core share one table."""
    from backend.tests.test_wko5expr_functions import _ds, _ev, ev_with, items, model_curve
    e = _ev(_ds([]))
    c = model_curve()
    f = e._fit(c)
    d = items(ev_with(e, "targetduration({0,1,2,3,4,5}, @c)", c=c))
    p = items(ev_with(e, "targetpower({0,1,2,3,4,5}, @c)", c=c))
    want = L.targets(f["params"], f["tte"])
    assert d == pytest.approx([x for x, _ in want]) and p == pytest.approx([y for _, y in want])


def test_what_the_levels_mean_on_the_cp_model():
    """Why the rest and the number of reps matter (the report's case): on the CP / W′ model a level-1
    rep spends about half of W′ (0.9625 / 0.0375 · W′/CP of the 50 · W′/CP a 1.02 · CP effort lasts),
    a level-2 rep all of it (0.1625 / 0.032 ≈ 5.08 · W′/CP vs 5 · W′/CP to exhaustion at 1.2 · CP)."""
    w, cp = 13000.0, 191.0
    p = [w, 20.0, cp, 25.0, 2400.0, -30.0]
    d1, d2 = L.target_duration(1, p, 1800.0), L.target_duration(2, p, 1800.0)
    assert d1 * 0.02 * cp / w == pytest.approx(0.5133, abs=1e-3)       # share of W′ one rep uses
    assert d2 * 0.20 * cp / w == pytest.approx(1.0156, abs=1e-3)
