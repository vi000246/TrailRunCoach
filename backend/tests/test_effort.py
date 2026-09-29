"""Equivalent flat distance — backend/engine/algorithms/effort.py."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms.effort import (
    SIMPLE_FORMULAS, effort_distance, energy_kcal, equivalent_flat_distance,
)


def _ramp(km_total, grade, n=1000):
    """Distance/elevation streams for a constant-grade climb."""
    dist = [km_total * i / n for i in range(n + 1)]
    elev = [100.0 + d * 1000.0 * grade for d in dist]
    return dist, elev


def test_flat_course_equals_its_distance():
    d, e = _ramp(10.0, 0.0)
    r = equivalent_flat_distance(d, e)
    assert r["efd_km"] == pytest.approx(10.0, rel=1e-6)
    assert r["gain_m"] == pytest.approx(0.0) and r["loss_m"] == pytest.approx(0.0)


def test_climb_costs_more_than_its_horizontal_distance():
    d, e = _ramp(5.0, 0.15)
    r = equivalent_flat_distance(d, e)
    assert r["km"] == pytest.approx(5.0, rel=1e-6)
    assert r["gain_m"] == pytest.approx(750.0, rel=1e-3)
    assert r["efd_km"] > 10.0                       # a 15% grade more than doubles it


def test_implied_divisor_lands_near_the_published_constants():
    """A moderate climb should imply roughly 'x metres of ascent = 1 flat km',
    in the same range as Scarf (126) and ITRA (100)."""
    d, e = _ramp(5.0, 0.12)
    div = equivalent_flat_distance(d, e)["implied_gain_divisor"]
    assert 90 < div < 200, div


def test_hiking_weights_ascent_more_heavily_than_running():
    d, e = _ramp(5.0, 0.15)
    run = equivalent_flat_distance(d, e, walking=False)["implied_gain_divisor"]
    hike = equivalent_flat_distance(d, e, walking=True)["implied_gain_divisor"]
    assert hike < run                               # lower divisor = ascent counts more


def test_descent_is_capped_not_rewarded():
    d, e = _ramp(5.0, -0.20)
    capped = equivalent_flat_distance(d, e)["efd_km"]
    free = equivalent_flat_distance(d, e, downhill_floor=None)["efd_km"]
    assert free < capped
    assert capped == pytest.approx(5.0 * 0.9, rel=1e-6)


def test_gps_gaps_are_skipped():
    """A jump larger than max_step_m is a teleport, not distance covered."""
    d = [0.0, 0.010, 5.000, 5.010]                  # two 10 m steps, one 5 km jump
    e = [100.0, 101.0, 101.0, 102.0]
    r = equivalent_flat_distance(d, e)
    assert r["km"] == pytest.approx(0.020, rel=1e-6)
    assert r["gain_m"] == pytest.approx(2.0, rel=1e-6)


def test_missing_samples_bridge_rather_than_lose_ground():
    """A dropout in either stream should not discard the ground covered across
    it — the step is measured from the last complete sample to the next one."""
    d = [0.0, None, 0.010, 0.020]
    e = [100.0, 100.0, None, 101.0]
    r = equivalent_flat_distance(d, e)
    assert r["km"] == pytest.approx(0.020, rel=1e-6)
    assert r["gain_m"] == pytest.approx(1.0, rel=1e-6)


def test_simple_formulas():
    assert effort_distance(10.0, 1000.0, formula="itra") == pytest.approx(20.0)
    assert effort_distance(10.0, 1260.0, formula="scarf") == pytest.approx(20.0)
    # Swiss adds a descent term; the others ignore descent
    assert effort_distance(10.0, 0.0, 1500.0, formula="swiss_lk") == pytest.approx(20.0)
    assert effort_distance(10.0, 0.0, 1500.0, formula="scarf") == pytest.approx(10.0)
    assert set(SIMPLE_FORMULAS) >= {"scarf", "itra", "swiss_lk"}


def test_energy_includes_the_pack():
    d, e = _ramp(10.0, 0.10)
    cost = equivalent_flat_distance(d, e, walking=True)["cost_j_per_kg"]
    bare = energy_kcal(cost, 68.0)
    loaded = energy_kcal(cost, 68.0, pack_kg=12.0)
    assert loaded == pytest.approx(bare * 80 / 68, rel=1e-6)
    assert 500 < bare < 4000, bare                  # sane kcal for a 10 km, 1000 m day
