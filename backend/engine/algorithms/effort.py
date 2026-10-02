"""
Equivalent flat distance (EFD) and effort distance.

The simple "effort distance" formulas exist for people who only have two
numbers, distance and ascent. We have per-second grade and speed, so we can
integrate the real cost of transport (Minetti 2002) instead:

    EFD = sum over samples of  cost(grade) * dx  /  cost(0)

which is exactly "how far on the flat would have cost the same energy".

`SIMPLE_FORMULAS` keeps the published summary-level formulas for comparison
and for activities with no elevation stream. Measured against integrated
Minetti over 430 of this athlete's activities (2026-09-29):

    Scarf   km + gain/126     4.0% mean error   (+2.6% bias)  <- best
    ITRA    km + gain/100     6.9%              (+6.9%)
    plain   km               13.8%              (-13.7%)
    Swiss   + loss/150       22.2%             (+22.2%)

Least-squares best fit on the same data: running km + gain/153,
hiking km + gain/111, with the descent term indistinguishable from zero.
Ascent is worth more to a hiker because flat walking is itself cheap.

Caveat: the ground truth is a model, so this says which summary formula best
approximates the best available lab data — not which is physiologically true.
"""
from __future__ import annotations

from typing import Optional, Sequence

from backend.engine.algorithms import minetti as M

# name -> (metres of ascent per equivalent km, metres of descent per km or None)
SIMPLE_FORMULAS: dict[str, tuple[float, Optional[float]]] = {
    "scarf": (126.0, None),          # Scarf's equivalence — best fit to our data
    "itra": (100.0, None),           # ITRA km-effort, used by 健行筆記
    "swiss_lk": (100.0, 150.0),      # Swiss Leistungskilometer
    "fitted_run": (153.0, None),     # the author's fit; in use: divisor_of() -> engine/terrain_calib (per athlete)
    "fitted_hike": (111.0, None),    # ... and hikes
}


def divisor_of(formula: str) -> float:
    """Metres of ascent per effort km: the table's, except fitted_run = the
    athlete's own (engine/terrain_calib.divisor, ITRA 100 until fitted)."""
    if formula == "fitted_run":
        try:
            from backend.engine.terrain_calib import divisor
            return divisor()
        except Exception:                   # noqa: BLE001
            pass
    return SIMPLE_FORMULAS[formula][0]


def effort_distance(distance_km: float, gain_m: float, loss_m: float = 0.0,
                    formula: str = "scarf") -> float:
    """Summary-level effort distance in km. Use when there is no elevation
    stream; prefer `equivalent_flat_distance` when there is."""
    up, down = divisor_of(formula), SIMPLE_FORMULAS[formula][1]
    efd = distance_km + gain_m / up
    if down:
        efd += loss_m / down
    return efd


def equivalent_flat_distance(distance_km: Sequence[Optional[float]],
                             elevation_m: Sequence[Optional[float]],
                             walking: bool = False,
                             downhill_floor: Optional[float] = 0.9,
                             max_step_m: float = 100.0) -> dict:
    """Integrate Minetti cost over a distance/elevation stream.

    Returns km (horizontal), gain, loss, cost_j_per_kg, efd_km and the implied
    ascent divisor. Steps longer than `max_step_m` are skipped as GPS gaps.
    """
    flat = M.FLAT_WALK if walking else M.FLAT_RUN
    cost = horiz = gain = loss = 0.0
    prev_d = prev_e = None
    for d, e in zip(distance_km, elevation_m):
        if d is None or e is None:
            continue
        if prev_d is not None:
            dd = (d - prev_d) * 1000.0
            de = e - prev_e
            if 0 < dd < max_step_m:
                cost += M.grade_factor(de / dd, walking, downhill_floor) * flat * dd
                horiz += dd
                gain += max(de, 0.0)
                loss += max(-de, 0.0)
        prev_d, prev_e = d, e
    efd = cost / flat / 1000.0
    km = horiz / 1000.0
    return {
        "km": km, "gain_m": gain, "loss_m": loss,
        "cost_j_per_kg": cost, "efd_km": efd,
        # metres of ascent that this activity's terrain made worth 1 flat km
        "implied_gain_divisor": (gain / (efd - km)) if gain and efd > km else None,
    }


def energy_kcal(cost_j_per_kg: float, body_mass_kg: float, pack_kg: float = 0.0,
                efficiency: float = 1.0) -> float:
    """Gross energy for the athlete plus what they carried. Minetti's cost is
    already metabolic (not mechanical), so `efficiency` is 1 by default."""
    return cost_j_per_kg * (body_mass_kg + pack_kg) / efficiency / 4184.0
