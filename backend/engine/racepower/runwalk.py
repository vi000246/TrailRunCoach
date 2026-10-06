"""
Walk or run on a climb: one answer from grade × speed — docs/research/
run-walk-threshold.md (SP-197) §3.1, §5.1, §5.3; SP-226.

Every uphill grade has a transition speed: slower than it, walking costs less;
faster, running does; the steeper the grade, the lower that speed (Brill &
Kram 2021; Finiel 2026; Ortiz 2017). Two curves give three answers (the owner's
decision, 2026-10-05):

    speed < PTS(g)            "walk"     走
    PTS(g) ≤ speed < EOTS(g)  "either"   走跑皆可 (people prefer running, walking is a bit cheaper)
    speed ≥ EOTS(g)           "run"      跑

    PTS   preferred (self-selected) transition speed
    EOTS  energetically optimal transition speed (walk and run cost the same)

The curves (§5.1), as treadmill belt speed (m/s along the slope):

    0–15°    PTS = 1.945 − 0.032 × angle (°): the straight line through Brill &
             Kram 2021's four measured PTS (1.95 / 1.78 / 1.62 / 1.47 m/s at
             0 / 5 / 10 / 15°), ≤ 0.005 m/s off at each (推估, the fit).
             EOTS: their measured 2.14 / 1.99 / 1.78 / 1.51 m/s, linearly
             interpolated (推估 between the points).
    15–30°   both straight to Ortiz 2017's 0.80 m/s at 30° (walking and running
             cost the same there); nothing measured in between (推估).
    > 30°    a constant vertical speed of 0.40 m/s (1,440 m/h, = 0.80 m/s at
             30°): Ortiz's single point carried on (推估).

Brill & Kram measured ten high-level male trail runners, fresh, on a treadmill,
no poles: applying it to everyone is 推估 (the owner's decision: the default
curve is used for everyone and the page says 預設值; SP-228 shifts it with the
athlete's own windows). `shift` (m/s) moves both curves by the same amount.

Only climbs: grade ≥ 3 % (seg_targets' runnable-climb lower bound, 推估);
flats and descents get None. The app's speeds are horizontal (map distance ÷
time); the belt speed is that × √(1 + g²).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from backend.i18n import N_, _

MIN_GRADE = 0.03                       # 推估: only climbs (seg_targets.RUN_CLIMB[0])
BK_DEG = (0.0, 5.0, 10.0, 15.0)        # Brill & Kram 2021 (J Exp Biol 224:jeb233056), 已驗證 full text
BK_PTS = (1.95, 1.78, 1.62, 1.47)      # preferred transition speed, m/s (belt)
BK_EOTS = (2.14, 1.99, 1.78, 1.51)     # energetically optimal transition speed, m/s (belt)
PTS_FLAT, PTS_PER_DEG = 1.945, 0.032   # 推估: the straight line through BK_PTS (§3.1)
ORTIZ_DEG, ORTIZ_V = 30.0, 0.80        # Ortiz, Giovanelli & Kram 2017 (Eur J Appl Physiol): 30°, costs equal at 0.8 m/s (摘要)
STEEP_VERT_MS = ORTIZ_V * math.sin(math.radians(ORTIZ_DEG))   # 0.40 m/s vertical above 30° (推估)
MIN_SPEED = 0.10                       # 推估: a shifted curve never goes below this (m/s)

GAITS = ("walk", "either", "run")
LABEL = {"walk": N_("走"), "either": N_("走跑皆可"), "run": N_("跑")}


def angle_deg(grade: float) -> float:
    return math.degrees(math.atan(grade))


def _steep(deg: float, at15: float) -> float:
    """15–30°: straight from the 15° value to Ortiz's 0.8 m/s; beyond, 0.4 m/s vertical."""
    if deg <= ORTIZ_DEG:
        return at15 + (ORTIZ_V - at15) * (deg - BK_DEG[-1]) / (ORTIZ_DEG - BK_DEG[-1])
    return STEEP_VERT_MS / math.sin(math.radians(deg))


def pts(grade: float, shift: float = 0.0) -> float:
    """Preferred walk–run transition speed at `grade` (m/s along the slope)."""
    deg = max(0.0, angle_deg(grade))
    if deg <= BK_DEG[-1]:
        v = PTS_FLAT - PTS_PER_DEG * deg
    else:
        v = _steep(deg, PTS_FLAT - PTS_PER_DEG * BK_DEG[-1])
    return max(MIN_SPEED, v + shift)


def eots(grade: float, shift: float = 0.0) -> float:
    """Energetically optimal walk–run transition speed at `grade` (m/s along the slope)."""
    deg = max(0.0, angle_deg(grade))
    if deg <= BK_DEG[-1]:
        v = float(np.interp(deg, BK_DEG, BK_EOTS))
    else:
        v = _steep(deg, BK_EOTS[-1])
    return max(MIN_SPEED, v + shift)


def belt_speed(grade: float, speed_ms: float) -> float:
    """Horizontal (map) speed → speed along the slope."""
    return speed_ms * math.sqrt(1.0 + grade * grade)


def horizontal(grade: float, belt_ms: float) -> float:
    """Speed along the slope → horizontal (map) speed."""
    return belt_ms / math.sqrt(1.0 + grade * grade)


def gait(grade: Optional[float], speed_ms: Optional[float], shift: float = 0.0) -> Optional[str]:
    """"walk" / "either" / "run" for a climb of `grade` (rise ÷ run) at the
    horizontal `speed_ms`; None below 3 % or without a speed."""
    if grade is None or speed_ms is None or grade < MIN_GRADE or not speed_ms > 0 or not math.isfinite(speed_ms):
        return None
    v = belt_speed(grade, speed_ms)
    if v < pts(grade, shift):
        return "walk"
    if v < eots(grade, shift):
        return "either"
    return "run"


def label(g: Optional[str]) -> Optional[str]:
    """The short label of a gait (走 / 走跑皆可 / 跑), None for None."""
    return _(LABEL[g]) if g in LABEL else None


def walk_label(g: Optional[str]) -> Optional[str]:
    """The segment's `walk` field: the label when the climb is walked or
    either (走 / 走跑皆可), None when it is run or not a climb."""
    return label(g) if g in ("walk", "either") else None
