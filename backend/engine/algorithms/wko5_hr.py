"""
WKO5 hrTSS / hrIF — reconstructed from WKO5.exe 5.0.587 calculateHeartrateTss
(@0x657300) and VERIFIED against WKO5's stored hrTSS (field 4235) and hrIF
(4236) on 1030/1030 workouts with heart rate (2026-09-29).

Per valid HR sample i (invalid samples are skipped entirely):
    dt   = t[i] - t[i-1]   (first sample: t[0] - start)
    rate = TSS/hour of the first level (descending) with hr >= factor * LTHR
    hrTSS += rate / 3600 * dt ;  T += dt
hrTSS is invalid if > 5000 or < 0.
hrIF = sqrt(0.6 * hrTSS / (T/60)^1.025)   (inverse of the rTSS formula)

LTHR is the sport-group threshold setting on the workout day
(runthr / bikethr / otherthr ...).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

# (fraction of LTHR, TSS per hour) — Friel HR levels, constants @0x86cdd0
HR_LEVELS: tuple[tuple[float, float], ...] = (
    (1.06, 140), (1.03, 120), (1.00, 100), (0.94, 80), (0.89, 70), (0.855, 60),
    (0.82, 50), (0.82 * 2 / 3, 40), (0.82 / 3, 30), (0.0, 20),
)


def hr_tss(t: Sequence[Optional[float]], hr: Sequence[Optional[float]], lthr: Optional[float],
           start: float = 0.0, moving: Optional[Sequence[bool]] = None
           ) -> tuple[Optional[float], Optional[float]]:
    """(hrTSS, hrIF); (None, None) when not computable / invalid.

    `moving` is a per-sample mask; when given, stationary samples are skipped
    entirely. WKO5 does NOT do this — it charges every recorded second — which
    inflates multi-day trips where you are camped but your heart rate is still
    up. Pass the mask for a moving-time-only hrTSS.
    """
    if not lthr or not t or hr is None:
        return None, None
    score = total = 0.0
    prev = start
    for i, (ti, h) in enumerate(zip(t, hr)):
        if ti is None:
            continue
        dt, prev = ti - prev, ti
        if h is None:
            continue
        if moving is not None and not (i < len(moving) and moving[i]):
            continue
        rate = 0.0
        for frac, per_hour in HR_LEVELS:
            if h >= frac * lthr:
                rate = per_hour / 3600
                break
        score += rate * dt
        total += dt
    if score > 5000 or score < 0:
        return None, None
    if total <= 0:
        return score, None
    iff = math.sqrt(0.6 * score / (total / 60.0) ** 1.025) if score > 0 else 0.0
    return score, iff
