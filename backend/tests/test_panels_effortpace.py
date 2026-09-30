"""backend/engine/panels/effortpace.py — personal grade→pace fit."""
import numpy as np
import pytest

from backend.engine.algorithms import minetti as M
from backend.engine.panels import effortpace as EP


def _minetti_runner(flat=3.0, grades=np.arange(-0.2, 0.3, 0.02), secs=600, hr=145.0):
    """Synthetic athlete running at constant metabolic power: speed = flat / factor."""
    dt, g, v, h = [], [], [], []
    for gr in grades:
        s = flat / M.grade_factor(gr, downhill_floor=None)
        dt += [1.0] * secs; g += [gr] * secs; v += [s] * secs; h += [hr] * secs
    return dt, g, v, h


def test_fit_recovers_flat_speed_and_uphill_slowdown():
    pool = EP.Pool(hr_ref=145)
    assert pool.add(*_minetti_runner()) > 0
    f = pool.fit()
    assert f["flat_speed"] == pytest.approx(3.0, rel=0.08)
    assert EP.speed_at(f, 20) < EP.speed_at(f, 0)
    c = {round(x["grade"]): x for x in EP.curve(f)}
    assert c[10]["personal_factor"] == pytest.approx(c[10]["minetti_factor"], rel=0.15)


def test_hr_band_filters_samples():
    pool = EP.Pool(hr_ref=120, hr_band=5)
    assert pool.add(*_minetti_runner(hr=145)) == 0
    assert pool.fit() is None


def test_needs_three_bins():
    pool = EP.Pool(hr_ref=145)
    pool.add(*_minetti_runner(grades=[0.0, 0.02]))
    assert pool.fit() is None


def test_speed_at_outside_range_is_none():
    pool = EP.Pool(hr_ref=145)
    pool.add(*_minetti_runner())
    assert EP.speed_at(pool.fit(), 45) is None
