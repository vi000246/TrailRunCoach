"""
Energy cost of running and walking on a gradient — Minetti et al. (2002),
J Appl Physiol 93:1039-1046, "Energy cost of walking and running at extreme
uphill and downhill slopes".

    i = gradient (rise / run, dimensionless)
    Cr(i) = 155.4 i^5 - 30.4 i^4 - 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6   (running)
    Cw(i) = 280.5 i^5 - 58.7 i^4 - 76.8 i^3 + 51.9 i^2 + 19.6 i + 2.5   (walking)

Cost is J/kg/m. The fit is valid for i in [-0.45, +0.45] (R^2 = 0.999); it is
extrapolation outside that and this module clamps to the range.

WHY THIS MATTERS HERE: WKO5's grade-adjusted pace uses the ACSM equation
(0.19v + 0.9vg)/0.19, which is linear in grade and was derived for treadmill
walking. Against Minetti it under-counts steep running by roughly 22% at +20%
and 31% at +30%, and it goes negative below about -21% grade. For trail and
vertical work Minetti is the better-grounded model.

CAVEAT ON DOWNHILL: Minetti's curve says steep descent is cheap (its minimum
is near -20%), but runners cannot convert that into speed — braking, footing
and eccentric load dominate. Strava's heart-rate-fitted model caps the downhill
benefit near 10%. `grade_adjusted_speed` therefore applies a floor on the
downhill side, configurable via `downhill_floor`.
"""
from __future__ import annotations

from typing import Optional, Sequence

RUN_COEFFS = (155.4, -30.4, -43.3, 46.3, 19.5, 3.6)
WALK_COEFFS = (280.5, -58.7, -76.8, 51.9, 19.6, 2.5)
VALID_GRADE = 0.45
# Cost on the flat, J/kg/m — the denominator for grade adjustment.
FLAT_RUN = RUN_COEFFS[-1]
FLAT_WALK = WALK_COEFFS[-1]
# Roughly constant cost per vertical metre on steep ground (~20% to 30-35 deg).
J_PER_VERTICAL_M = 45.0


def _poly(c, i: float) -> float:
    return ((((c[0] * i + c[1]) * i + c[2]) * i + c[3]) * i + c[4]) * i + c[5]


def cost_of_transport(grade: float, walking: bool = False) -> float:
    """Metabolic cost in J/kg/m at `grade` (rise/run). Clamped to +/-0.45."""
    i = max(-VALID_GRADE, min(VALID_GRADE, grade))
    return _poly(WALK_COEFFS if walking else RUN_COEFFS, i)


def grade_factor(grade: float, walking: bool = False,
                 downhill_floor: Optional[float] = 0.9) -> float:
    """Cost at `grade` relative to flat — multiply speed by this for a
    grade-adjusted speed. `downhill_floor` caps how much credit a descent gets
    (0.9 = at most a 10% speed bonus, matching Strava's fitted model); None
    uses Minetti unmodified."""
    flat = FLAT_WALK if walking else FLAT_RUN
    f = cost_of_transport(grade, walking) / flat
    if downhill_floor is not None and grade < 0:
        f = max(f, downhill_floor)
    return f


def grade_adjusted_speed(speed: float, grade: float, walking: bool = False,
                         downhill_floor: Optional[float] = 0.9) -> float:
    """Flat-equivalent speed: the speed that would cost the same energy."""
    return speed * grade_factor(grade, walking, downhill_floor)


def metabolic_power(speed_m_s: float, grade: float, walking: bool = False) -> float:
    """W/kg — cost per metre times metres per second."""
    return cost_of_transport(grade, walking) * speed_m_s


def grade_adjusted_series(speed: Sequence[Optional[float]], grade: Sequence[Optional[float]],
                          walking: bool = False,
                          downhill_floor: Optional[float] = 0.9) -> list[Optional[float]]:
    """Per-sample grade-adjusted speed; None where either input is missing."""
    return [None if s is None or g is None else grade_adjusted_speed(s, g, walking, downhill_floor)
            for s, g in zip(speed, grade)]
