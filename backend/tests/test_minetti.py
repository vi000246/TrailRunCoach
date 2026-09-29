"""Minetti (2002) gradient energy cost — backend/engine/algorithms/minetti.py."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest

from backend.engine.algorithms import minetti as M


def test_flat_cost_matches_the_published_constants():
    assert M.cost_of_transport(0.0) == pytest.approx(3.6)          # running, J/kg/m
    assert M.cost_of_transport(0.0, walking=True) == pytest.approx(2.5)
    assert M.grade_factor(0.0) == pytest.approx(1.0)


def test_uphill_cost_rises_steeply():
    assert M.cost_of_transport(0.20) == pytest.approx(9.01, abs=0.05)   # 2.5x flat
    assert M.cost_of_transport(0.40) > M.cost_of_transport(0.20) > M.cost_of_transport(0.0)


def test_downhill_has_a_minimum_around_minus_20_percent():
    grades = [-0.45, -0.30, -0.20, -0.10, 0.0]
    costs = [M.cost_of_transport(g) for g in grades]
    assert min(costs) == costs[grades.index(-0.20)]
    assert costs[grades.index(-0.20)] < costs[-1]                  # cheaper than flat


def test_cost_is_clamped_outside_the_validated_range():
    assert M.cost_of_transport(0.9) == M.cost_of_transport(M.VALID_GRADE)
    assert M.cost_of_transport(-0.9) == M.cost_of_transport(-M.VALID_GRADE)


def test_walking_costs_less_per_metre_than_running_at_every_grade():
    """Walking is always the cheaper gait per metre; running buys speed, not
    economy. That is why power-hiking wins on steep ground — the running speed
    advantage collapses there while the energy penalty remains."""
    for g in (-0.4, -0.2, 0.0, 0.1, 0.2, 0.3, 0.45):
        assert M.cost_of_transport(g, walking=True) < M.cost_of_transport(g), g


def test_cost_per_vertical_metre_is_roughly_constant_when_steep():
    """The basis for using vertical speed as the intensity metric on steep
    ground: J per vertical metre flattens out (~45 J/kg/m of ascent)."""
    per_vert = [M.cost_of_transport(g) / g for g in (0.20, 0.30, 0.40)]
    assert max(per_vert) / min(per_vert) < 1.15
    assert all(35 < v < 55 for v in per_vert), per_vert


def test_grade_adjusted_speed_uphill_is_much_faster_than_actual():
    # 2.5 m/s up a 15% grade is worth far more than 2.5 m/s on the flat
    assert M.grade_adjusted_speed(2.5, 0.15) > 2.5 * 1.8


def test_downhill_credit_is_floored():
    """Minetti alone would over-credit descent; the floor caps the bonus."""
    free = M.grade_adjusted_speed(3.0, -0.20, downhill_floor=None)
    capped = M.grade_adjusted_speed(3.0, -0.20)
    assert free < capped                      # Minetti says cheaper; we cap it
    assert capped == pytest.approx(3.0 * 0.9)


def test_metabolic_power_scales_with_speed_and_grade():
    assert M.metabolic_power(3.0, 0.0) == pytest.approx(3.6 * 3.0)
    assert M.metabolic_power(3.0, 0.15) > M.metabolic_power(3.0, 0.0)


def test_acsm_undercounts_steep_running_versus_minetti():
    """The gap that motivates using Minetti for trail: WKO5's ACSM factor
    (0.19v + 0.9vg)/0.19 is linear in grade."""
    for g in (0.20, 0.30):
        acsm = (0.19 + 0.9 * g) / 0.19
        assert M.grade_factor(g) > acsm * 1.15


def test_series_helper_passes_gaps_through():
    out = M.grade_adjusted_series([3.0, None, 3.0], [0.0, 0.1, None])
    assert out[0] == pytest.approx(3.0) and out[1] is None and out[2] is None
