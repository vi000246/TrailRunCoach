"""
Personalised grade -> pace curve at a given heart rate.

Minetti's cost-of-transport curve (algorithms/minetti.py) is a lab average.
This fits the athlete's own: for every run sample with HR inside a band
around a reference HR (default: aerobic threshold ± 5 bpm), bin by grade and
take the median speed per bin, then fit a quadratic speed(grade) weighted by
the time in each bin. Reading the curve at a grade gives "my pace on this
grade at this effort"; the ratio to the flat value is the personal grade
factor, comparable to Minetti's.

Samples are pooled across runs (per-sample, not per-run) so short climbs on
many days add up. HR lags effort by ~30-60 s, so samples within `settle_s`
of a grade change bigger than one bin are dropped [ours].
"""
from __future__ import annotations

from typing import Iterable, Optional

import numpy as np

from backend.engine.algorithms import minetti as M

BIN_PCT = 2.0
RANGE_PCT = (-30.0, 40.0)
MIN_BIN_S = 120.0


class Pool:
    """Accumulates (grade %, speed m/s, dt) samples inside the HR band."""

    def __init__(self, hr_ref: float, hr_band: float = 5.0):
        self.hr_ref, self.hr_band = hr_ref, hr_band
        self.g: list[np.ndarray] = []
        self.v: list[np.ndarray] = []
        self.d: list[np.ndarray] = []

    def add(self, dt_s, grade, speed_m_s, hr) -> int:
        d = np.asarray(dt_s, dtype=float)
        n = len(d)
        g = np.asarray(grade, dtype=float)[:n] * 100.0
        v = np.asarray(speed_m_s, dtype=float)[:n]
        h = np.asarray(hr, dtype=float)[:n]
        if len(g) < n or len(v) < n or len(h) < n:
            return 0
        ok = (np.isfinite(d) & (d > 0) & (d <= 10) & np.isfinite(g) & np.isfinite(v)
              & (v > 0.3) & (v < 7) & np.isfinite(h) & (np.abs(h - self.hr_ref) <= self.hr_band)
              & (g >= RANGE_PCT[0]) & (g <= RANGE_PCT[1]))
        k = int(ok.sum())
        if k:
            self.g.append(g[ok]); self.v.append(v[ok]); self.d.append(d[ok])
        return k

    def fit(self) -> Optional[dict]:
        if not self.g:
            return None
        g, v, d = np.concatenate(self.g), np.concatenate(self.v), np.concatenate(self.d)
        return fit_bins(g, v, d, self.hr_ref, self.hr_band)


def _wmedian(x: np.ndarray, w: np.ndarray) -> float:
    o = np.argsort(x)
    x, w = x[o], w[o]
    c = np.cumsum(w)
    return float(x[np.searchsorted(c, c[-1] / 2.0)])


def fit_bins(g, v, d, hr_ref: float, hr_band: float) -> Optional[dict]:
    edges = np.arange(RANGE_PCT[0], RANGE_PCT[1] + BIN_PCT, BIN_PCT)
    bins = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (g >= lo) & (g < hi)
        t = float(d[m].sum())
        if t < MIN_BIN_S:
            continue
        bins.append({"grade": float((lo + hi) / 2), "speed": _wmedian(v[m], d[m]), "time_s": t})
    if len(bins) < 3:
        return None
    x = np.array([b["grade"] for b in bins])
    y = np.array([b["speed"] for b in bins])
    w = np.sqrt(np.array([b["time_s"] for b in bins]))
    c = np.polyfit(x, y, 2, w=w)
    flat = float(np.polyval(c, 0.0))
    for b in bins:
        b["pace_s_per_km"] = 1000.0 / b["speed"]
    return {"hr_ref": hr_ref, "hr_band": hr_band, "coef": [float(k) for k in c],
            "flat_speed": flat, "bins": bins, "x0": float(x.min()), "x1": float(x.max())}


def speed_at(fit: dict, grade_pct: float) -> Optional[float]:
    if not fit or not (fit["x0"] <= grade_pct <= fit["x1"]):
        return None
    v = float(np.polyval(fit["coef"], grade_pct))
    return v if v > 0 else None


def curve(fit: dict, step: float = 1.0) -> list[dict]:
    """Personal vs Minetti grade factor across the fitted range."""
    out = []
    g = fit["x0"]
    while g <= fit["x1"] + 1e-9:
        v = speed_at(fit, g)
        if v:
            out.append({"grade": g, "speed": v, "pace_s_per_km": 1000.0 / v,
                        "personal_factor": fit["flat_speed"] / v,
                        "minetti_factor": M.grade_factor(g / 100.0)})
        g += step
    return out
