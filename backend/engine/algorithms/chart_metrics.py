"""
Plain-Python reference implementations of the metrics behind the charts added
from docs/research/competitor-charts.md §4 (views/training.json, views/workout.json).

The charts themselves are WKO5-style expressions evaluated by
backend/engine/wko5expr/evaluator.py. This module re-implements each formula
from its source, independently of the expression engine, so the tests can check
that the expression and the published definition agree (on the source's own
worked examples, and on real activities). Sources, exact formulas and the check
results are recorded in docs/research/competitor-charts.md §7.

Metrics and sources
-------------------
* Form% = TSB / CTL. Friel, "Managing training using TSB" (joefrieltraining.com)
  gives the zones on absolute TSB; intervals.icu applies the same numbers as a
  percentage of CTL (forum.intervals.icu/t/form-as-a-percentage-of-fitness/869).
  Here TSB is yesterday's CTL − ATL (TrainingPeaks), so the denominator is
  yesterday's CTL too.
* Load ratio = ATL / CTL (7- and 42-day exponentially weighted loads). Bands
  0.8 / 1.3 / 1.5 are Runalyze's A:C ratio bands, which cite Gabbett 2016 (BJSM
  50:273) — Gabbett's ratio is rolling means 7:28, so the bands are borrowed.
* Polarization Index. Treff et al. 2019, Front Physiol 10:707, Eq. 1
  log10(Z1/Z2 x Z3 x 100) with zone shares as fractions; Eq. 2 when Z2 = 0:
  log10(Z1/0.01 x (Z3 - 0.01) x 100); Z3 = 0 -> 0; Z3 > Z1 -> not valid.
* コース定数 (course constant). 山本正嘉, as published by 山と溪谷社
  (yamakei-online.com/yama-ya/detail.php?id=363): 1.8 x 行動時間 h + 0.3 x
  距離 km + 10.0 x 登り累積標高差 km + 0.6 x 下り累積標高差 km. The source uses
  the reference course time; the charts use the actual moving time.
* ITRA km-effort = km + D+ m / 100, categories XXS < 25, XS 25-44, S 45-74,
  M 75-114, L 115-154, XL 155-209, XXL >= 210 (ITRA, as quoted by trailia.run;
  itra.run itself only renders with JavaScript).
* Up/downhill rate (m/h): vertical metres over time on graded samples. No
  formula to verify beyond the arithmetic; the grade and HR cuts are ours.
* Downhill impact load: OUR OWN COMPOSITE, no published formula (Stryd LBSS and
  Garmin Running Tolerance are proprietary). Per downhill sample:
      horizontal km x grade weight x speed weight
  grade weight = 1 + 0.54 x min(|grade| / tan 9 deg, 1): Gottschall & Kram 2005
  (J Biomech 38:445) measured normal impact force peaks +54% at -9 deg at
  3 m/s; linear from level (0%) to -9 deg is our interpolation, and steeper
  than -9 deg is held at +54% (no data).
  speed weight = Fz(v) / Fz(3 m/s), Fz(v) = 1.2 + (2.5 - 1.2)/(6 - 1.5) x
  (v - 1.5) BW, v clamped to 1.5-6 m/s: Keller et al. 1996 (Clin Biomech
  11:253), vertical GRF rising linearly from 1.2 BW at 1.5 m/s to about
  2.5 BW at 6 m/s. 3 m/s is the speed of the Gottschall & Kram data, so 1 unit
  = one level km at 3 m/s. Samples less steep than -3% are not counted (the
  smallest grade in Gottschall & Kram is -3 deg = -5.2%; below -3% the grade
  is mostly barometer / GPS noise).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

# ---- Form%, load ratio -------------------------------------------------------
FORM_ZONES = ((-0.30, "high_risk"), (-0.10, "optimal"), (0.05, "grey"), (0.25, "fresh"))  # upper bounds
LOAD_RATIO_BANDS = (0.8, 1.3, 1.5)


def ewma_loads(daily: Sequence[float], const: float) -> list[float]:
    """TrainingPeaks / WKO5 exponential load: v += (x - v) / const each day."""
    v, out = 0.0, []
    for x in daily:
        v += (float(x) - v) / const
        out.append(v)
    return out


def form_pct(ctl_yesterday: float, atl_yesterday: float) -> Optional[float]:
    """TSB / CTL as a fraction, TSB = yesterday's CTL - ATL."""
    if not ctl_yesterday:
        return None
    return (ctl_yesterday - atl_yesterday) / ctl_yesterday


def form_zone(f: float) -> str:
    for hi, name in FORM_ZONES:
        if f < hi:
            return name
    return "transition"


# ---- Treff Polarization Index ----------------------------------------------------
def polarization_index(z1: float, z2: float, z3: float) -> Optional[float]:
    """Treff et al. 2019. Shares as fractions (0-1). None = not valid (Z3 > Z1)."""
    if z3 > z1:
        return None
    if z3 <= 0:
        return 0.0
    if z2 > 0:
        return math.log10(z1 / z2 * z3 * 100)
    if z3 <= 0.01:
        return 0.0          # Eq. 2 is undefined here; treated like Z3 = 0 (ours)
    return math.log10(z1 / 0.01 * (z3 - 0.01) * 100)


# ---- route difficulty -----------------------------------------------------------
ITRA_CATEGORIES = ((25, "XXS"), (45, "XS"), (75, "S"), (115, "M"), (155, "L"), (210, "XL"))


def course_constant(hours: float, km: float, climb_m: float, descent_m: float) -> float:
    return 1.8 * hours + 0.3 * km + 10.0 * climb_m / 1000 + 0.6 * descent_m / 1000


def km_effort(km: float, climb_m: float) -> float:
    return km + climb_m / 100


def itra_category(ep: float) -> str:
    for hi, name in ITRA_CATEGORIES:
        if ep < hi:
            return name
    return "XXL"


# ---- up / downhill rate ---------------------------------------------------------
CLIMB_GRADE = 0.05        # rise/run
MOVING_KMH = 0.5
MIN_CLIMB_S = 600


def climb_rate(grade, delev, dt, speed_kmh, hr=None, lthr=None, uphill=True) -> Optional[float]:
    """Vertical m/h over samples steeper than +/-5% while moving. Uphill drops
    samples with HR >= LTHR (samples without HR are kept). None below 10 min."""
    g, de, t, v = (np.asarray(a, dtype=float) for a in (grade, delev, dt, speed_kmh))
    with np.errstate(invalid="ignore"):
        sel = (g >= CLIMB_GRADE) if uphill else (g <= -CLIMB_GRADE)
        sel &= v > MOVING_KMH
        if uphill and hr is not None and lthr is not None:
            h = np.asarray(hr, dtype=float)
            sel &= (h < lthr) | np.isnan(h)
    sel &= ~np.isnan(t)
    secs = float(np.sum(t[sel]))
    if secs < MIN_CLIMB_S:
        return None
    m = float(np.nansum(de[sel]))
    return (m if uphill else -m) / secs * 3600


# ---- downhill impact load (our composite) ---------------------------------------
DH_MIN_GRADE = -0.03
GK_GRADE = math.tan(math.radians(9))      # Gottschall & Kram's steepest wedge
GK_IMPACT_GAIN = 0.54                     # +54% normal impact peak at -9 deg
KELLER_V = (1.5, 6.0)                     # m/s
KELLER_FZ = (1.2, 2.5)                    # BW
REF_SPEED = 3.0                           # m/s, Gottschall & Kram's test speed


def keller_fz(v_ms: float) -> float:
    v = min(max(v_ms, KELLER_V[0]), KELLER_V[1])
    return KELLER_FZ[0] + (KELLER_FZ[1] - KELLER_FZ[0]) / (KELLER_V[1] - KELLER_V[0]) * (v - KELLER_V[0])


def downhill_weight(grade: float, speed_kmh: float) -> float:
    """Impact weight of one downhill km (1 = level at 3 m/s)."""
    gw = 1 + GK_IMPACT_GAIN * min(max(-grade / GK_GRADE, 0.0), 1.0)
    return gw * keller_fz(speed_kmh / 3.6) / keller_fz(REF_SPEED)


def downhill_load(grade, ddist_km, speed_kmh) -> float:
    """Sum of horizontal downhill km x weight over samples steeper than -3%."""
    total = 0.0
    for g, d, v in zip(grade, ddist_km, speed_kmh):
        if any(x is None or (isinstance(x, float) and math.isnan(x)) for x in (g, d, v)):
            continue
        if g < DH_MIN_GRADE and v > 0:
            total += d * downhill_weight(g, v)
    return total


# The same per-workout load as a sample expression (views/training.json uses it
# verbatim; the overview card evaluates it too). Constants: tan 9 deg = 0.158384,
# (2.5 - 1.2)/(6 - 1.5) = 0.288889, Fz(3 m/s) = 1.633333.
DOWNHILL_EXPR = ("sum(if(rgrade < -0.03 and speed > 0, delta(elapseddistance) * "
                 "(1 + 0.54 * clamp(-rgrade / 0.158384, 0, 1)) * "
                 "(1.2 + 0.288889 * (clamp(speed / 3.6, 1.5, 6) - 1.5)) / 1.633333))")


def acute_chronic(daily: Sequence[float], acute: int = 7, chronic: int = 28) -> Optional[float]:
    """Mean of the last `acute` days over the mean of the last `chronic` days
    (the list ends today; missing days are 0)."""
    x = list(daily)[-chronic:]
    x = [0.0] * (chronic - len(x)) + x
    c = sum(x) / chronic
    if c <= 0:
        return None
    return (sum(x[-acute:]) / acute) / c
