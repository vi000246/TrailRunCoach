"""WKO5 PD model (backend/engine/algorithms/wko5_pdmodel.py).

Synthetic only: the model is DISASSEMBLY-ONLY until compared with WKO5's PD
chart, so these tests pin the fitter's behaviour, not WKO5 parity.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms import wko5_pdmodel as pd

DURATIONS = [1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 420,
             600, 900, 1200, 1500, 1800, 2400, 3000, 3600, 4500, 5400, 7200]
TRUE = [20000.0, 12.0, 250.0, 25.0, 2700.0, -40.0]   # FRC J, tau1, FTP, tau2, TTE, D


def _curve(p=TRUE):
    return [(t, pd.model(p, t)) for t in DURATIONS]


def test_model_shape_is_monotone_decreasing_after_the_sprint():
    ys = [y for _, y in _curve()]
    peak = ys.index(max(ys))
    assert all(a >= b for a, b in zip(ys[peak:], ys[peak + 1:]))


def test_fit_recovers_aerobic_parameters_from_a_model_curve():
    r = pd.fit(_curve())
    assert r is not None and r["valid"]
    assert r["FTP"] == pytest.approx(250.0, rel=0.01)
    assert r["tte"] == pytest.approx(pd.tte_solve(TRUE, 250.0), rel=0.05)
    assert r["phenotype"] in {"All-rounder", "Pursuiter", "Sprinter", "Time-Trialer"}


def test_fit_needs_mmp_out_to_40_minutes():
    short = [(t, y) for t, y in _curve() if t < 2400]
    assert pd.fit(short) is None


def test_vo2max_formula():
    r = pd.fit(_curve())
    assert r["vo2max"] == pytest.approx((r["FRC"] / 589.0 + r["FTP"]) / 84.5 + 0.656)
