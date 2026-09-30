"""
Fatigue band (COROS style) and training monotony / strain (Foster).

Fatigue band
    ratio = ATL / CTL * 100 with the athlete's own time constants (7 d / 42 d
    by default). COROS "Training Load Ratio" bands:

        >= 150   overreaching   過量      high injury / illness risk
        100-149  optimized      最佳化    fitness is being built
        80-99    maintaining    維持
        < 80     recovery       恢復      detraining if it lasts

    The band edges are COROS's published ones [vendor]; the ratio itself is
    the classic acute:chronic workload ratio.

Monotony / strain (Foster 1998, Med Sci Sports Exerc 30:1164)
    monotony = mean(daily load over a week) / sd(daily load over the week)
    strain   = weekly load * monotony
    Rest days count as 0. Foster reported illness clustering at monotony > 2
    together with high strain. sd uses the population form (ddof=0) as Foster
    did; a week with identical loads every day has no defined monotony (None).
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Iterable, Optional, Sequence

BANDS = (  # (lower bound inclusive, key, label, colour)
    (150.0, "overreaching", "過量", "#dc2626"),
    (100.0, "optimized", "最佳化", "#16a34a"),
    (80.0, "maintaining", "維持", "#2563eb"),
    (-math.inf, "recovery", "恢復", "#94a3b8"),
)
MONOTONY_HIGH = 2.0


def pmc(daily_load: Sequence[float], ctl_days: float = 42.0, atl_days: float = 7.0,
        ctl0: float = 0.0, atl0: float = 0.0) -> tuple[list[float], list[float]]:
    """CTL / ATL with the WKO5 / TrainingPeaks exponential form
    x_t = x_{t-1} + (load_t - x_{t-1}) / tc."""
    ctl, atl = [], []
    c, a = ctl0, atl0
    for load in daily_load:
        v = 0.0 if load is None or (isinstance(load, float) and math.isnan(load)) else float(load)
        c += (v - c) / ctl_days
        a += (v - a) / atl_days
        ctl.append(c)
        atl.append(a)
    return ctl, atl


def ratio(atl: Optional[float], ctl: Optional[float]) -> Optional[float]:
    if atl is None or ctl is None or ctl <= 0:
        return None
    return atl / ctl * 100.0


def band(r: Optional[float]) -> Optional[dict]:
    if r is None:
        return None
    for lo, key, label, color in BANDS:
        if r >= lo:
            return {"key": key, "label": label, "color": color, "ratio": r}
    return None  # pragma: no cover


def band_edges() -> list[dict]:
    """Horizontal bands for the chart: [{lo, hi, key, label, color}]."""
    out, hi = [], None
    for lo, key, label, color in BANDS:
        out.append({"lo": None if lo == -math.inf else lo, "hi": hi,
                    "key": key, "label": label, "color": color})
        hi = lo
    return out


def monotony_strain(week: Sequence[float]) -> dict:
    """One week (7 daily loads; missing days = 0)."""
    xs = [0.0 if v is None else float(v) for v in week]
    total = sum(xs)
    n = len(xs) or 1
    mean = total / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / n)
    mono = mean / sd if sd > 1e-9 else None
    return {"load": total, "monotony": mono,
            "strain": total * mono if mono is not None else None}


def weekly_monotony(daily: dict[dt.date, float], begin: dt.date, end: dt.date) -> list[dict]:
    """Monday-based weeks covering [begin, end]; each {week, load, monotony, strain}."""
    start = begin - dt.timedelta(days=begin.weekday())
    out = []
    while start <= end:
        days = [daily.get(start + dt.timedelta(days=i), 0.0) for i in range(7)]
        out.append({"week": start.isoformat(), **monotony_strain(days)})
        start += dt.timedelta(days=7)
    return out


def daily_series(loads: Iterable[tuple[dt.date, float]], begin: dt.date, end: dt.date) -> dict[dt.date, float]:
    """Sum (date, load) pairs into a dense {date: load} over [begin, end]."""
    out = {begin + dt.timedelta(days=i): 0.0 for i in range((end - begin).days + 1)}
    for d, v in loads:
        if d in out and v is not None and not (isinstance(v, float) and math.isnan(v)):
            out[d] += float(v)
    return out
