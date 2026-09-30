"""
4-week load focus: how the training load of the last 28 days splits into
low aerobic / high aerobic / anaerobic, against target ranges for the current
training phase.

Classification per sample (the same boundaries as WKO5's "Energy System Impact
- Run" chart, which colours each second by power vs 0.85 / 1.05 * mFTP):

    power      P <  0.85 FTP  low aerobic
               P <= 1.05 FTP  high aerobic
               P >  1.05 FTP  anaerobic
    heart rate HR <  0.89 LTHR, <= 1.00 LTHR, > 1.00 LTHR (Friel zone 1-2 /
               3-4 / 5a+ boundaries) when the workout has no power

Load per sample is (x / threshold)^2 * dt / 36 — the TSS integrand, so a
workout's buckets add up to its TSS-like load. When the workout's own TSS is
known the buckets are scaled to it, so the focus chart sums to the same total
as the PMC. Samples under 0.3 * threshold (standing, walking breaks, bad data)
carry no load.

Target ranges per phase are a coaching heuristic [ours], loosely following the
polarised / pyramidal distributions in docs/research/periodization-phase-metrics.md
(base = mostly low aerobic; specific = more high aerobic and some anaerobic):
they are shares of the 4-week load, not of time — load weights intensity, so
80 % of time easy is roughly 60-70 % of load.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

BUCKETS = ("low", "high", "anaerobic")
LABELS = {"low": "低強度有氧", "high": "高強度有氧", "anaerobic": "無氧"}
COLORS = {"low": "#38bdf8", "high": "#f59e0b", "anaerobic": "#d946ef"}

POWER_EDGES = (0.85, 1.05)
HR_EDGES = (0.89, 1.00)
MIN_FRACTION = 0.3

# phase -> bucket -> (lo %, hi %) of the 4-week load
TARGETS = {
    "base":       {"low": (60, 80), "high": (15, 30), "anaerobic": (0, 10)},
    "specific":   {"low": (50, 70), "high": (20, 35), "anaerobic": (5, 15)},
    "taper":      {"low": (50, 70), "high": (20, 35), "anaerobic": (5, 15)},
    "transition": {"low": (70, 100), "high": (0, 25), "anaerobic": (0, 5)},
    "recovery":   {"low": (70, 100), "high": (0, 25), "anaerobic": (0, 5)},
}
DEFAULT_PHASE = "base"


def split(values: Sequence[float], dt_s: Sequence[float], threshold: float,
          edges: tuple[float, float]) -> dict[str, float]:
    """Load per bucket for one workout's samples."""
    out = {b: 0.0 for b in BUCKETS}
    if not threshold or threshold <= 0:
        return out
    x = np.asarray(values, dtype=float) / threshold
    d = np.asarray(dt_s, dtype=float)
    if len(x) != len(d):
        n = min(len(x), len(d))
        x, d = x[:n], d[:n]
    ok = np.isfinite(x) & np.isfinite(d) & (d > 0) & (d < 30) & (x >= MIN_FRACTION)
    x, d = x[ok], d[ok]
    load = x * x * d / 36.0
    lo, hi = edges
    out["low"] = float(load[x < lo].sum())
    out["high"] = float(load[(x >= lo) & (x <= hi)].sum())
    out["anaerobic"] = float(load[x > hi].sum())
    return out


def scale_to(buckets: dict[str, float], total: Optional[float]) -> dict[str, float]:
    s = sum(buckets.values())
    if total is None or not math.isfinite(total) or total <= 0 or s <= 0:
        return dict(buckets)
    return {k: v * total / s for k, v in buckets.items()}


def summarize(buckets: dict[str, float], phase: Optional[str]) -> dict:
    """Totals, shares and the gap to each target range."""
    phase_key = phase if phase in TARGETS else DEFAULT_PHASE
    targets = TARGETS[phase_key]
    total = sum(buckets.get(b, 0.0) for b in BUCKETS)
    rows = []
    for b in BUCKETS:
        v = buckets.get(b, 0.0)
        pct = v / total * 100.0 if total > 0 else None
        lo, hi = targets[b]
        if pct is None:
            state = None
        elif pct < lo:
            state = "short"
        elif pct > hi:
            state = "over"
        else:
            state = "ok"
        rows.append({"key": b, "label": LABELS[b], "color": COLORS[b], "load": v,
                     "pct": pct, "target": [lo, hi], "state": state,
                     # load that would bring this bucket to the bottom of its range
                     "short_by": (lo / 100.0 * total - v) if state == "short" else 0.0})
    return {"phase": phase_key, "total": total, "buckets": rows}
