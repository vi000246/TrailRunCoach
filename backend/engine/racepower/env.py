"""
Environment multiplier M — SuperPower Calculator `v4 Calcs` rows 204–238
(docs/research/superpower-calculator.md §1.1).

    P_to = P_from · M,   M = 1 − (A_from − A_to) − (H_to − H_from)/100

A = altitude factor from barometric pressure (torr), H = Hadley heat penalty
(%) from air temperature + dew point in °F. Pure functions, no I/O.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

# Magnus coefficients as the workbook uses them
MAG_A, MAG_B, MAG_C, MAG_D = 6.1121, 18.678, 257.14, 234.5

DEFAULT_ALTITUDE_M = 200.0
DEFAULT_TEMP_C = 12.0
DEFAULT_RH_PCT = 70.0


def pressure_torr(altitude_m: float, temp_c: float) -> float:
    """Barometric formula as in the workbook (air temperature at the site)."""
    tk = temp_c + 273.15
    expo = (9.80665 * 0.0289644) / (8.31432 * -0.0065)
    return 101325.0 * (tk / (tk - 0.0065 * altitude_m)) ** expo * 0.00750062


def altitude_factor(torr: float) -> float:
    """Fraction of sea-level sustainable power at this pressure."""
    p = torr
    return (-174.1448622 + 1.0899959 * p - 1.5119e-3 * p ** 2 + 0.72674e-6 * p ** 3) / 100.0


def dew_point(temp_c: float, rh_pct: float) -> dict:
    """Magnus dew point: returns ita (the workbook's intermediate), °C and °F."""
    rh = max(float(rh_pct), 0.01)
    ita = math.log(rh / 100.0 * math.exp((MAG_B - temp_c / MAG_D) * (temp_c / (MAG_C + temp_c))))
    dew_c = MAG_C * ita / (MAG_B - ita)
    return {"ita": ita, "dew_c": dew_c, "dew_f": dew_c * 1.8 + 32.0}


def rh_from_dew_point(temp_c: float, dew_c: float) -> float:
    """Exact inverse of `dew_point` — for feeds that give a dew point but no RH."""
    ita = MAG_B * dew_c / (MAG_C + dew_c)
    sat = (MAG_B - temp_c / MAG_D) * (temp_c / (MAG_C + temp_c))
    return max(0.0, min(100.0, 100.0 * math.exp(ita - sat)))


def heat_penalty_pct(temp_c: float, rh_pct: float) -> float:
    """Hadley: x = air °F + dew °F; penalty (%) only when x > 100 (D3: clamp ≥ 0)."""
    x = dew_point(temp_c, rh_pct)["dew_f"] + temp_c * 1.8 + 32.0
    if x <= 100.0:
        return 0.0
    return max(0.0, 0.001341 * x * x - 0.249517 * x + 11.699986)


@dataclass
class Conditions:
    altitude_m: Optional[float] = None
    temp_c: Optional[float] = None
    rh_pct: Optional[float] = None

    @classmethod
    def of(cls, d) -> "Conditions":
        if d is None:
            return cls()
        if isinstance(d, Conditions):
            return d
        return cls(d.get("altitude_m"), d.get("temp_c"), d.get("rh_pct"))


def resolve(frm, to) -> tuple[Conditions, Conditions]:
    """Workbook defaults: a blank field copies the other side; both blank →
    200 m / 12 °C / 70 %."""
    a, b = Conditions.of(frm), Conditions.of(to)
    defaults = {"altitude_m": DEFAULT_ALTITUDE_M, "temp_c": DEFAULT_TEMP_C, "rh_pct": DEFAULT_RH_PCT}
    fa, fb = {}, {}
    for k, dv in defaults.items():
        va, vb = getattr(a, k), getattr(b, k)
        if va is None and vb is None:
            va = vb = dv
        fa[k] = vb if va is None else va
        fb[k] = va if vb is None else vb
    return Conditions(**fa), Conditions(**fb)


def side(c: Conditions) -> dict:
    torr = pressure_torr(c.altitude_m, c.temp_c)
    dp = dew_point(c.temp_c, c.rh_pct)
    x = dp["dew_f"] + c.temp_c * 1.8 + 32.0
    return {**asdict(c), "torr": torr, "altitude_factor": altitude_factor(torr),
            "dew_c": dp["dew_c"], "dew_f": dp["dew_f"], "ita": dp["ita"],
            "heat_index_sum_f": x, "heat_applies": x > 100.0,
            "heat_penalty_pct": heat_penalty_pct(c.temp_c, c.rh_pct)}


def multiplier(frm=None, to=None) -> dict:
    """M from `frm` (where the power numbers were measured) to `to` (race day),
    with every intermediate for display."""
    a, b = resolve(frm, to)
    sa, sb = side(a), side(b)
    alt_pct = -(sa["altitude_factor"] - sb["altitude_factor"])
    heat_pct = -(sb["heat_penalty_pct"] - sa["heat_penalty_pct"]) / 100.0
    pct = alt_pct + heat_pct
    return {"from": sa, "to": sb, "altitude_pct": alt_pct, "heat_pct": heat_pct,
            "pct": pct, "M": 1.0 + pct}
