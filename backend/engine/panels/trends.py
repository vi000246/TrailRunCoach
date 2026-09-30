"""
Season trends that need per-workout stream analysis.

efficiency (per workout)
    EF = mean output / mean HR over the moving samples after a warm-up, where
    output is grade-adjusted speed (m/min, from NGP) or power (W).
    Decoupling (Pa:HR, TrainingPeaks / Friel) = (EF first half / EF second
    half - 1) * 100 — positive = HR drifted up relative to output. Under 5 %
    on a long easy run is the usual "aerobically fit for this duration"
    marker [coach practice: Friel, Uphill Athlete].

qualifies_easy
    The trend only means something on comparable sessions: >= 60 min moving,
    easy (IF <= 0.80 or mean HR below the athlete's aerobic threshold / 0.89 *
    LTHR). Hill-heavy runs are fine because output is grade adjusted.

rolling_trend
    Median of the last `n` points, so a single hot day doesn't swing the line.
"""
from __future__ import annotations

from statistics import median
from typing import Optional, Sequence

import numpy as np

MIN_EASY_S = 3600.0
WARMUP_S = 600.0
EASY_IF = 0.80


def efficiency(t, output, hr, warmup_s: float = WARMUP_S, min_hr: float = 60.0) -> Optional[dict]:
    t = np.asarray(t, dtype=float)
    o = np.asarray(output, dtype=float)
    h = np.asarray(hr, dtype=float)
    n = min(len(t), len(o), len(h))
    t, o, h = t[:n], o[:n], h[:n]
    if n == 0:
        return None
    t0 = np.nanmin(t)
    ok = np.isfinite(t) & np.isfinite(o) & (o > 0) & np.isfinite(h) & (h >= min_hr) & (t - t0 >= warmup_s)
    if ok.sum() < 120:
        return None
    tt, oo, hh = t[ok], o[ok], h[ok]
    mid = tt[0] + (tt[-1] - tt[0]) / 2.0
    a, b = tt <= mid, tt > mid
    if a.sum() < 60 or b.sum() < 60:
        return None
    ef = float(oo.mean() / hh.mean())
    ef1 = float(oo[a].mean() / hh[a].mean())
    ef2 = float(oo[b].mean() / hh[b].mean())
    return {"ef": ef, "ef1": ef1, "ef2": ef2, "decoupling_pct": (ef1 / ef2 - 1.0) * 100.0,
            "avg_hr": float(hh.mean()), "duration_s": float(tt[-1] - tt[0] + warmup_s)}


def qualifies_easy(moving_s: Optional[float], iff: Optional[float], avg_hr: Optional[float],
                   aet: Optional[float] = None, lthr: Optional[float] = None) -> bool:
    if not moving_s or moving_s < MIN_EASY_S:
        return False
    if iff is not None and iff > 0:
        return iff <= EASY_IF
    cap = aet or (0.89 * lthr if lthr else None)
    if cap and avg_hr:
        return avg_hr <= cap + 3.0
    return False


def rolling_trend(points: Sequence[tuple[str, float]], n: int = 5) -> list[list]:
    out = []
    for i in range(len(points)):
        window = [v for _, v in points[max(0, i - n + 1): i + 1] if v is not None]
        out.append([points[i][0], median(window) if window else None])
    return out
