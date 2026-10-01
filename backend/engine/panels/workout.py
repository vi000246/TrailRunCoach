"""
Single-workout analyses that are tables or derived curves rather than one
WKO5 expression.

grade_bins
    Time, distance, pace, power, HR and cadence per grade bucket. Grade is the
    rolling grade (fraction) the evaluator exposes as `rgrade`, or any
    sample-aligned grade array. Averages are time-weighted; pace is
    moving-time / distance within the bucket. Samples with dt > 30 s (pauses,
    gaps) are dropped.

durability
    "Does my efficiency hold as the day goes on?" A rolling ratio
    (grade-adjusted speed or power) / heart rate over `window_s`, expressed
    as % of the baseline window (the first full window after `warmup_s`),
    plotted against cumulative work (kJ) or elapsed time. A falling line is
    cardiac drift / decoupling developing inside the session; a flat line near
    100 % is durability. [coach practice: Uphill Athlete / Maunder et al. 2021
    "durability"], exact windows [ours].

time_in_zones
    Seconds per zone from sample-aligned values and lower edges.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

# grade bucket lower edges in % (the open-ended first/last buckets catch the rest)
GRADE_EDGES = (-30, -20, -10, -5, -2, 2, 5, 10, 15, 20, 30)
MAX_DT = 30.0


def _arr(x, n) -> Optional[np.ndarray]:
    if x is None:
        return None
    a = np.asarray(x, dtype=float)
    return a[:n] if len(a) >= n else np.concatenate([a, np.full(n - len(a), np.nan)])


def _label(lo: Optional[float], hi: Optional[float]) -> str:
    if lo is None:
        return f"< {hi:g}%"
    if hi is None:
        return f"≥ {lo:g}%"
    return f"{lo:g} ~ {hi:g}%"


def _wavg(v: Optional[np.ndarray], w: np.ndarray, m: np.ndarray) -> Optional[float]:
    if v is None:
        return None
    ok = m & np.isfinite(v) & (v > 0)
    tw = w[ok].sum()
    return float((v[ok] * w[ok]).sum() / tw) if tw > 0 else None


def grade_bins(dt_s, grade, distance_km=None, power=None, hr=None, cadence=None,
               edges: Sequence[float] = GRADE_EDGES, elev_m=None) -> list[dict]:
    """Rows ordered from steepest downhill to steepest uphill; empty buckets dropped.
    distance_km is cumulative (km); grade is a fraction (0.1 = 10%). With
    `elev_m` (sample-aligned, m) each row also has `vam`: the net vertical
    rate inside the bucket, m/h (negative on descents)."""
    d = np.asarray(dt_s, dtype=float)
    n = len(d)
    g = _arr(grade, n) * 100.0
    dist = _arr(distance_km, n)
    step = np.diff(dist, prepend=dist[0]) if dist is not None else None
    el = _arr(elev_m, n)
    rise = np.diff(el, prepend=el[0]) if el is not None else None
    ok = np.isfinite(d) & (d > 0) & (d <= MAX_DT) & np.isfinite(g)
    p, h, c = _arr(power, n), _arr(hr, n), _arr(cadence, n)
    bounds = [None, *edges, None]
    rows = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        m = ok.copy()
        if lo is not None:
            m &= g >= lo
        if hi is not None:
            m &= g < hi
        t = float(d[m].sum())
        if t <= 0:
            continue
        km = None
        if step is not None:
            s = step[m]
            km = float(s[np.isfinite(s) & (s >= 0) & (s < 1)].sum())
        vam = None
        if rise is not None:
            r = rise[m]
            r_ok = np.isfinite(r) & (np.abs(r) < 50)       # a > 50 m step is a GPS / file jump
            if r_ok.any():
                vam = float(r[r_ok].sum() / t * 3600.0)
        rows.append({
            "vam": vam,
            "label": _label(lo, hi), "lo": lo, "hi": hi,
            "time_s": t,
            "distance_km": km,
            "pace_s_per_km": (t / km) if km and km > 0.01 else None,
            "avg_grade": float((g[m] * d[m]).sum() / t),
            "power": _wavg(p, d, m), "hr": _wavg(h, d, m), "cadence": _wavg(c, d, m),
        })
    total = sum(r["time_s"] for r in rows) or 1.0
    for r in rows:
        r["time_pct"] = r["time_s"] / total * 100.0
    return rows


def rolling_mean(t, v, window_s: float) -> np.ndarray:
    """Time-based trailing mean (NaN samples ignored); NaN until the window is full."""
    t = np.asarray(t, dtype=float)
    v = np.asarray(v, dtype=float)
    ok = np.isfinite(v) & np.isfinite(t)
    vv = np.where(ok, v, 0.0)
    cv = np.concatenate([[0.0], np.cumsum(vv)])
    cn = np.concatenate([[0.0], np.cumsum(ok.astype(float))])
    out = np.full(len(t), np.nan)
    j = 0
    for i in range(len(t)):
        if not np.isfinite(t[i]):
            continue
        while j < i and (not np.isfinite(t[j]) or t[i] - t[j] >= window_s):
            j += 1
        if t[i] - t[0] < window_s - 1e-9:
            continue
        cnt = cn[i + 1] - cn[j]
        if cnt > 0:
            out[i] = (cv[i + 1] - cv[j]) / cnt
    return out


def durability(t, output, hr, x=None, window_s: float = 600.0, warmup_s: float = 600.0,
               min_hr: float = 60.0, max_points: int = 400) -> Optional[dict]:
    """Rolling output/HR as % of the first full window after warm-up.
    `output` = grade-adjusted speed (m/s) or power (W); `x` = what to plot
    against (cumulative kJ or elapsed s; defaults to elapsed hours)."""
    t = np.asarray(t, dtype=float)
    o = np.asarray(output, dtype=float)
    h = np.asarray(hr, dtype=float)
    n = min(len(t), len(o), len(h))
    t, o, h = t[:n], o[:n], h[:n]
    if n == 0 or not np.isfinite(t).any():
        return None
    o = np.where(np.isfinite(o) & (o > 0), o, np.nan)
    h = np.where(np.isfinite(h) & (h >= min_hr), h, np.nan)
    ro, rh = rolling_mean(t, o, window_s), rolling_mean(t, h, window_s)
    ratio = ro / rh
    t0 = t[np.isfinite(t)][0]
    base_i = np.where(np.isfinite(ratio) & (t - t0 >= warmup_s + window_s - 1e-9))[0]
    if len(base_i) == 0:
        return None
    base = float(ratio[base_i[0]])
    pct = ratio / base * 100.0
    xs = (t - t0) / 3600.0 if x is None else np.asarray(x, dtype=float)[:n]
    keep = np.where(np.isfinite(pct) & np.isfinite(xs) & (t - t0 >= warmup_s + window_s - 1e-9))[0]
    stepn = max(1, int(math.ceil(len(keep) / max_points)))
    pts = [[float(xs[i]), float(pct[i])] for i in keep[::stepn]]
    last = float(pct[keep[-1]]) if len(keep) else None
    return {"baseline": base, "points": pts, "end_pct": last,
            "decoupling_pct": (100.0 - last) if last is not None else None}


def time_in_zones(values, dt_s, lower_edges: Sequence[float], labels: Sequence[str]) -> list[dict]:
    """lower_edges[i] is the bottom of zone i (ascending); the last zone is open."""
    v = np.asarray(values, dtype=float)
    d = np.asarray(dt_s, dtype=float)
    n = min(len(v), len(d))
    v, d = v[:n], d[:n]
    ok = np.isfinite(v) & np.isfinite(d) & (d > 0) & (d <= MAX_DT)
    out = []
    for i, (lo, lab) in enumerate(zip(lower_edges, labels)):
        hi = lower_edges[i + 1] if i + 1 < len(lower_edges) else None
        m = ok & (v >= lo) & ((v < hi) if hi is not None else True)
        out.append({"label": lab, "lo": lo, "hi": hi, "time_s": float(d[m].sum())})
    total = sum(z["time_s"] for z in out) or 1.0
    for z in out:
        z["pct"] = z["time_s"] / total * 100.0
    return out


def cumulative_kj(power, dt_s) -> np.ndarray:
    p = np.asarray(power, dtype=float)
    d = np.asarray(dt_s, dtype=float)
    w = np.where(np.isfinite(p) & np.isfinite(d) & (d > 0) & (d <= MAX_DT), p * d, 0.0)
    return np.cumsum(w) / 1000.0
