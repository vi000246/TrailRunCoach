"""
Running effectiveness (RE = speed m/s ÷ power W/kg), the workbook's CVI hill
adjustment (task 18, §1.3), the personal trail RE (§3.1) and the per-activity
Stryd metrics of task 3.

(This module is `backend.engine.racepower.re`; it never needs the stdlib `re`.)
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional, Sequence

import numpy as np

FT_PER_M = 3.28084
MI_PER_KM = 0.621371

CVI_CATEGORIES = [      # (label, lower bound inclusive) — index = category number
    ("下坡 Downhill", -25),
    ("平路 Flat", 0),
    ("微丘 Slightly Hilly", 26),
    ("小丘 Somewhat Hilly", 36),
    ("中丘 Moderately Hilly", 51),
    ("多丘 Very Hilly", 76),
    ("極多丘 Extremely Hilly", 101),
]


def re_value(speed_ms: float, power_w: float, weight_kg: float) -> Optional[float]:
    if not power_w or not weight_kg or speed_ms is None:
        return None
    return speed_ms / (power_w / weight_kg)


def cvi(climbing_m: Optional[float], distance_km: Optional[float]) -> Optional[float]:
    """Climbing ft per mile, clamped to [−25, 101]."""
    if not distance_km or climbing_m is None:
        return None
    v = (climbing_m * FT_PER_M) / (distance_km * MI_PER_KM)
    return max(-25.0, min(101.0, v))


def cvi_category(v: float) -> int:
    r = round(max(-25.0, min(101.0, v)))
    if r < 0:
        return 0
    idx = 1
    for i, (_, lo) in enumerate(CVI_CATEGORIES):
        if i >= 1 and r >= lo:
            idx = i
    return idx


def cvi_adjust(prior_cvi: float, target_cvi: float) -> float:
    """+0.01 RE per category the training was hillier than the race."""
    return (cvi_category(prior_cvi) - cvi_category(target_cvi)) * 0.01


def effort_km(distance_km: float, gain_m: float, divisor: float) -> float:
    return distance_km + (gain_m or 0.0) / divisor


def trail_re(distance_km: float, gain_m: float, moving_s: float, avg_power: float,
             weight_kg: float, divisor: float) -> Optional[float]:
    """RE on the effort distance: (D_eff m / moving s) / (P / W)."""
    if not moving_s or not avg_power:
        return None
    return re_value(effort_km(distance_km, gain_m, divisor) * 1000.0 / moving_s, avg_power, weight_kg)


def summary(values: Sequence[float], weights: Optional[Sequence[float]] = None) -> Optional[dict]:
    """Median (weighted if weights given), IQR and n."""
    v = [x for x in values if x is not None and math.isfinite(x)]
    if not v:
        return None
    if weights is None:
        q1, q3 = np.percentile(v, [25, 75])
        return {"median": float(median(v)), "q1": float(q1), "q3": float(q3), "n": len(v)}
    pairs = sorted((x, w) for x, w in zip(values, weights) if x is not None and math.isfinite(x))
    xs = np.array([p[0] for p in pairs])
    ws = np.array([p[1] for p in pairs], dtype=float)
    cw = np.cumsum(ws) / ws.sum()

    def q(f):
        return float(xs[np.searchsorted(cw, f)])
    return {"median": q(0.5), "q1": q(0.25), "q3": q(0.75), "n": len(xs)}


def activity_metrics(t, power, speed_kmh, weight_kg, air=None, form=None, lss=None,
                     min_speed_kmh: float = 3.0) -> Optional[dict]:
    """Task 3 from one activity's sample arrays (numpy, NaN = no data): moving
    average power / speed, RE, LSS/kg, air-power % (Pa) and form-power %.
    Moving = power > 0 and speed above `min_speed_kmh`, time-weighted by the
    sample interval (gaps > 60 s ignored)."""
    if t is None or power is None or speed_kmh is None or not weight_kg:
        return None
    t = np.asarray(t, float)
    p = np.asarray(power, float)
    s = np.asarray(speed_kmh, float)
    n = min(len(t), len(p), len(s))
    if n < 60:
        return None
    t, p, s = t[:n], p[:n], s[:n]
    dt = np.diff(t, prepend=t[0])
    dt[~np.isfinite(dt)] = 0
    dt[(dt < 0) | (dt > 60)] = 0
    m = np.isfinite(p) & (p > 0) & np.isfinite(s) & (s > min_speed_kmh) & (dt > 0)
    wsum = float(dt[m].sum())
    if wsum < 300:
        return None
    avg_p = float((p[m] * dt[m]).sum() / wsum)
    avg_s = float((s[m] * dt[m]).sum() / wsum) / 3.6

    def share(ch):
        if ch is None:
            return None
        c = np.asarray(ch, float)[:n]
        mm = m & np.isfinite(c)
        if not mm.any():
            return None
        return float((c[mm] * dt[mm]).sum() / dt[mm].sum())
    a, f, l = share(air), share(form), share(lss)
    return {"moving_s": wsum, "avg_power": avg_p, "avg_speed_ms": avg_s,
            "re": re_value(avg_s, avg_p, weight_kg),
            "air_pct": None if a is None else 100.0 * a / avg_p,
            "form_pct": None if f is None else 100.0 * f / avg_p,
            "lss_kg": None if l is None else l / weight_kg}
