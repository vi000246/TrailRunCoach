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


def _heat_eff(h_pct: float, s: Optional[float], a: float) -> float:
    """heat-acclimation.md §4.3: H_eff = H·(1 − a·S) (engine/heat.py
    effective_heat; 自組). S None = the v1 penalty unchanged."""
    if s is None:
        return h_pct
    s = min(1.0, max(0.0, float(s)))
    return max(0.0, h_pct * (1.0 - a * s))


A_RECOVER = 0.75                 # engine/heat.A_RECOVER (Racinais et al. 2015 MSSE, 77 % in 2 weeks; 自組 use)


def multiplier(frm=None, to=None, heat_s=None, a: float = A_RECOVER) -> dict:
    """M from `frm` (where the power numbers were measured) to `to` (race day),
    with every intermediate for display. `heat_s` = (S_from, S_to), the heat
    acclimation index on each side (heat-acclimation.md §4.3): each side's
    Hadley penalty becomes H·(1 − a·S). None (or S = 0) = v1 exactly."""
    a_, b = resolve(frm, to)
    sa, sb = side(a_), side(b)
    s_from, s_to = heat_s if heat_s is not None else (None, None)
    hf, ht = _heat_eff(sa["heat_penalty_pct"], s_from, a), _heat_eff(sb["heat_penalty_pct"], s_to, a)
    if heat_s is not None:
        sa["heat_penalty_eff_pct"], sb["heat_penalty_eff_pct"] = hf, ht
    alt_pct = -(sa["altitude_factor"] - sb["altitude_factor"])
    heat_pct = -(ht - hf) / 100.0
    pct = alt_pct + heat_pct
    return {"from": sa, "to": sb, "altitude_pct": alt_pct, "heat_pct": heat_pct,
            "pct": pct, "M": 1.0 + pct}


LAPSE_K_PER_M = 0.0065           # ICAO standard atmosphere (pressure_torr uses the same)


def segment_temp(t0: float, z0: Optional[float], z: Optional[float]) -> float:
    """baiyue-from-running.md §8.1: t0 − 0.0065·(z − z0) (standard lapse
    rate; t0 is the temperature at z0)."""
    if z0 is None or z is None:
        return t0
    return t0 - LAPSE_K_PER_M * (z - z0)


def heat_term(temp_c: float, rh_pct: float, status: Optional[dict]) -> dict:
    """§8.1 interface: the per-segment walking / running speed multiplier
    H = 1 − s·Hadley%/100, s = status["scale"] (the share of the
    unacclimatised penalty that remains; None → 1, v1 behaviour)."""
    pen = heat_penalty_pct(temp_c, rh_pct)
    s = 1.0 if not status or status.get("scale") is None else min(1.0, max(0.0, float(status["scale"])))
    return {"H": 1.0 - s * pen / 100.0, "penalty_pct": pen, "penalty_eff_pct": s * pen, "s": s,
            "badge": (status or {}).get("badge")}


# ---- v2: per-segment altitude (docs/research/racepower-v2.md F14, §8) --------

WEHRLIN_START_M = 300.0
WEHRLIN_PER_1000M = 0.063
ACCLIMATISATION = ("acclimatised", "partial", "unacclimatised")


def altitude_factor_linear(alt_m: float) -> float:
    """Fraction of sea-level VO2max at `alt_m`, unacclimatised.

    Source: Wehrlin & Hallén 2006, Eur J Appl Physiol 96:404–412 — VO2max falls
    linearly by 6.3 % per 1000 m from about 300 m (acute exposure, 300–2800 m).
    Status: 已驗證 up to 2800 m (V-F14: 1300 m → 0.937); above 2800 m it is
    extrapolation and the page labels the segment 推估."""
    return 1.0 - WEHRLIN_PER_1000M * max(0.0, float(alt_m) - WEHRLIN_START_M) / 1000.0


BASSETT_MAX_M = 4000.0
BASSETT_TRUSTED_M = 3000.0


def bassett_pct(alt_m: float, acclimatized: bool) -> float:
    """Bassett, Kyle, Passfield, Broker & Burke 1999 (MSSE 31:1665–1676), x in
    km, % of sea-level aerobic power: acclimatised −1.12x² − 1.90x + 99.9,
    unacclimatised (1–7 days) 0.178x³ − 1.43x² − 4.07x + 100. The rounded
    coefficients as TrainingPeaks (Rytlewski 2024) and Simmons 2014 print them
    — 已驗證 second-hand (racepower-v2.md §3C.3; V-F14b: 2000 m → 91.62 /
    87.564). Clamped to 0–4000 m; above 3000 m the page says 推估. Used as a
    cross-check of the acclimatised curve, not in the multiplier."""
    x = max(0.0, min(BASSETT_MAX_M, float(alt_m))) / 1000.0
    if acclimatized:
        return -1.12 * x * x - 1.90 * x + 99.9
    return 0.178 * x ** 3 - 1.43 * x * x - 4.07 * x + 100.0


def _alt_factor(alt_m: float, temp_c: float, mode: str) -> float:
    curve = altitude_factor(pressure_torr(alt_m, temp_c))
    if mode == "acclimatised":
        return curve
    lin = altitude_factor_linear(alt_m)
    if mode == "unacclimatised":
        return lin
    return 0.5 * (curve + lin)


def segment_factors(zs, frm, to, mode: str = "acclimatised", heat=None, heat_s=None,
                    a: float = A_RECOVER) -> list[float]:
    """Per-segment environment multiplier Mᵢ (F14): the v1 formula
    M = 1 − (A_from − A_to) − (H_to − H_from)/100 with the race-day altitude
    replaced by each segment's mean elevation zᵢ.

    Heat: one value (the race-day To side) unless `heat` gives one
    (temp_c, rh_pct) per segment — the conditions at the hour that segment is
    run. H is the same Hadley penalty (`heat_penalty_pct`, the SuperPower
    workbook's `v4 Calcs` formula, docs/research/superpower-calculator.md
    §1.1); only its input changes. The altitude term keeps the To temperature,
    so a heat list equal to the To conditions gives exactly the single-heat
    Mᵢ. Mapping forecast hours to segments is our own composition (自組); the
    page labels it 推估.

    mode: "acclimatised" = this module's pressure polynomial (v1; the same
    coefficients as the SuperPower workbook and GoldenCheetah's aPower, which
    credits Péronnet, Thibault & Cousineau 1991 — that attribution is single-
    source, 待驗證; numerically it stays within 1 point of Bassett et al. 1999's
    acclimatised curve over 0–4000 m, see `bassett_pct`), "unacclimatised" =
    Wehrlin linear (已驗證 ≤ 2800 m), "partial" = the midpoint of the two — our
    own choice with no quantitative study behind it (自組, labelled 推估).
    With every zᵢ equal to the race altitude and mode "acclimatised" each Mᵢ is
    exactly v1's single M (T14).

    heat_s = (S_from, S_to): heat acclimation on each side, every Hᵢ and H_from
    become H·(1 − a·S) (heat-acclimation.md §4.3, 自組). None = unchanged."""
    fa, b = resolve(frm, to)
    sa, sb = side(fa), side(b)
    a_from = _alt_factor(fa.altitude_m, fa.temp_c, mode)
    zs = list(zs)
    if heat is None:
        hs = [sb["heat_penalty_pct"]] * len(zs)
    else:
        if len(heat) != len(zs):
            raise ValueError("heat needs one (temp_c, rh_pct) per segment")
        hs = [heat_penalty_pct(t, rh) for t, rh in heat]
    s_from, s_to = heat_s if heat_s is not None else (None, None)
    h_from = _heat_eff(sa["heat_penalty_pct"], s_from, a)
    return [1.0 - (a_from - _alt_factor(z, b.temp_c, mode)) - (_heat_eff(h, s_to, a) - h_from) / 100.0
            for z, h in zip(zs, hs)]
