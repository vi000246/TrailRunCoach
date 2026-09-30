"""
Estimate lactate-threshold heart rate (LTHR) from ordinary runs.

WKO5 has no such estimate — "Threshold Heart Rate" is a hand-entered setting
(WKO5.exe 5.0.587: only the input-validation strings exist, and none of the
182 expression functions derives it). Running power gives us a way in: the
PD model's CP / mFTP is estimated automatically, and heart rate at a
threshold-level effort is LTHR.

Per run (resampled to 1 s):
  * Friel field test: the best 30-minute power window; LTHR ≈ mean HR of its
    last 20 minutes (Friel, "Quick Guide to Setting Zones"). The window only
    counts when its mean power is ≥ 95% of CP — otherwise the run wasn't a
    threshold effort and the HR would underestimate LTHR.
  * HR at CP: mean HR while 30-s power stays within 97–103% of CP, after a
    10-minute warm-up, when that adds up to ≥ 10 minutes. A cross-check.

The athlete-level estimate is the median over qualifying runs in a window
(default 90 days), so one hot day or a flaky strap doesn't move it.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

import numpy as np

FRIEL_WINDOW_S = 1800
FRIEL_TAIL_S = 1200
MIN_EFFORT_OF_CP = 0.95
CP_BAND = (0.97, 1.03)
WARMUP_S = 600
MIN_AT_CP_S = 600
MIN_RUNS = 3


@dataclass
class RunThreshold:
    friel_hr: Optional[float]      # mean HR, last 20 min of the best 30-min power window
    p30: Optional[float]           # that window's mean power
    hr_at_cp: Optional[float]
    s_at_cp: int                   # seconds spent at 97–103% CP (after warm-up)

    def qualifies(self, cp: float) -> bool:
        return self.friel_hr is not None and self.p30 is not None and self.p30 >= MIN_EFFORT_OF_CP * cp


def _grid(t, hr, power):
    """HR and power on one shared 1-s grid (None where either is missing)."""
    t = np.asarray(t, dtype=float)
    h = np.asarray([np.nan if v is None else v for v in hr], dtype=float)
    p = np.asarray([np.nan if v is None else v for v in power], dtype=float)
    h[h <= 0] = np.nan
    ok = ~np.isnan(t) & ~np.isnan(h) & ~np.isnan(p)
    if ok.sum() < 2:
        return None, None
    grid = np.arange(t[ok][0], t[ok][-1] + 1.0)
    return np.interp(grid, t[ok], h[ok]), np.interp(grid, t[ok], p[ok])


def _best(x: np.ndarray, n: int) -> tuple[Optional[float], Optional[int]]:
    if x is None or len(x) < n:
        return None, None
    c = np.convolve(x, np.ones(n) / n, "valid")
    i = int(np.argmax(c))
    return float(c[i]), i


def run_threshold(t: Sequence[float], hr: Sequence[Optional[float]],
                  power: Optional[Sequence[Optional[float]]], cp: Optional[float]) -> RunThreshold:
    """Friel and HR-at-CP numbers for one run. HR and power are resampled onto
    one shared 1-s grid, so windows line up in time."""
    empty = RunThreshold(None, None, None, 0)
    if power is None or cp is None or cp <= 0:
        return empty
    h1, p1 = _grid(t, hr, power)
    if h1 is None:
        return empty
    p30, i = _best(p1, FRIEL_WINDOW_S)
    friel = None if i is None else float(h1[i + FRIEL_WINDOW_S - FRIEL_TAIL_S:i + FRIEL_WINDOW_S].mean())
    smooth = np.convolve(p1, np.ones(30) / 30, "same")
    band = (smooth >= CP_BAND[0] * cp) & (smooth <= CP_BAND[1] * cp)
    band[:WARMUP_S] = False
    s = int(band.sum())
    return RunThreshold(friel, p30, float(h1[band].mean()) if s >= MIN_AT_CP_S else None, s)


@dataclass
class LthrEstimate:
    value: Optional[int]
    n: int                         # qualifying runs
    hr_at_cp: Optional[int]        # cross-check, median over runs with ≥10 min at CP
    n_at_cp: int
    spread: Optional[tuple[int, int]]   # inter-quartile range of the Friel values
    reason: str


def estimate_lthr(runs: Iterable[RunThreshold], cp: float, min_runs: int = MIN_RUNS) -> LthrEstimate:
    runs = list(runs)
    friel = sorted(r.friel_hr for r in runs if r.qualifies(cp))
    at_cp = [r.hr_at_cp for r in runs if r.hr_at_cp is not None]
    hcp = round(statistics.median(at_cp)) if at_cp else None
    if len(friel) < min_runs:
        return LthrEstimate(None, len(friel), hcp, len(at_cp), None,
                            f"只有 {len(friel)} 次跑步有 30 分鐘 ≥ {MIN_EFFORT_OF_CP:.0%} CP 的強度（需要 ≥ {min_runs} 次）")
    q = statistics.quantiles(friel, n=4) if len(friel) >= 4 else [friel[0], 0, friel[-1]]
    return LthrEstimate(round(statistics.median(friel)), len(friel), hcp, len(at_cp),
                        (round(q[0]), round(q[-1])),
                        f"{len(friel)} 次跑步的最佳 30 分鐘（≥ {MIN_EFFORT_OF_CP:.0%} CP）後 20 分鐘平均心率的中位數")


# ---------------------------------------------------------------------------
# aerobic threshold (AeT)
#
# Uphill Athlete's AeT drift test: after a warm-up, hold a steady effort for
# ~60 min; if Pa:HR / Pw:HR drifts < 5% between the halves, that HR is at or
# below AeT, and AeT is the HR where the drift reaches ~5%. Ordinary steady
# runs are small, unplanned versions of that test: each gives (first-half HR,
# drift). Across many runs drift rises with HR, so a straight line through
# them crosses 5% at the AeT estimate.
#
# Power is the output measure (Pw:HR) rather than pace, so hills matter less.
# ---------------------------------------------------------------------------

AET_DRIFT = 0.05
AET_MIN_S = 2700                # run must be ≥ 45 min
AET_MAX_POWER_CV = 0.15         # "steady": coefficient of variation of 30-s power
AET_MAX_OF_CP = 0.90            # only easy/steady runs; harder ones aren't AeT tests
AET_MIN_RUNS = 6


@dataclass
class DriftPoint:
    hr1: float        # mean HR, first half (after warm-up)
    drift: float      # (r1 − r2) / r1, r = power / HR
    power: float      # mean power over the analysed part


def steady_drift(t, hr, power, cp: Optional[float] = None) -> Optional[DriftPoint]:
    """Pw:HR decoupling of one steady run, or None when the run isn't a fair test."""
    if power is None:
        return None
    h1, p1 = _grid(t, hr, power)
    if h1 is None or len(h1) < AET_MIN_S:
        return None
    h, p = h1[WARMUP_S:], p1[WARMUP_S:]
    p30 = np.convolve(p, np.ones(30) / 30, "valid")
    if p30.mean() <= 0 or p30.std() / p30.mean() > AET_MAX_POWER_CV:
        return None
    if cp and p.mean() > AET_MAX_OF_CP * cp:
        return None
    half = len(h) // 2
    r1 = p[:half].mean() / h[:half].mean()
    r2 = p[half:].mean() / h[half:].mean()
    return DriftPoint(float(h[:half].mean()), float((r1 - r2) / r1), float(p.mean()))


@dataclass
class AetEstimate:
    value: Optional[int]
    n: int
    slope_per_10bpm: Optional[float]     # drift change per +10 bpm
    below: Optional[int]                 # highest first-half HR among runs with drift < 5%
    reason: str


def estimate_aet(points: Iterable[DriftPoint], lthr: Optional[float] = None,
                 min_runs: int = AET_MIN_RUNS) -> AetEstimate:
    pts = [p for p in points if abs(p.drift) < 0.30]          # |drift| ≥ 30% = broken data
    # a low-drift run above LTHR says nothing about AeT (heat, strap, downhill)
    below = [p.hr1 for p in pts if p.drift < AET_DRIFT and (lthr is None or p.hr1 < lthr - 3)]
    hi_ok = round(max(below)) if below else None
    if len(pts) < min_runs:
        return AetEstimate(None, len(pts), None, hi_ok,
                           f"只有 {len(pts)} 次 ≥ 45 分鐘的穩定輕鬆跑（需要 ≥ {min_runs} 次）")
    x = np.array([p.hr1 for p in pts])
    y = np.array([p.drift for p in pts])
    if x.std() < 3:
        return AetEstimate(None, len(pts), None, hi_ok, "這些跑步的心率都差不多，看不出飄移隨心率的變化")
    slope, icpt = np.polyfit(x, y, 1)
    if slope <= 0:
        return AetEstimate(None, len(pts), float(slope * 10), hi_ok,
                           "心率越高飄移沒有跟著變大，資料不足以找出轉折點")
    aet = (AET_DRIFT - icpt) / slope
    lo, hi = x.min() - 5, x.max() + 5                     # don't extrapolate far
    if lthr:
        hi = min(hi, lthr - 3)
    if not lo <= aet <= hi:
        return AetEstimate(None, len(pts), float(slope * 10), hi_ok,
                           f"推算值 {aet:.0f} 超出資料範圍（{x.min():.0f}–{x.max():.0f} bpm），先不採用")
    return AetEstimate(round(aet), len(pts), float(slope * 10), hi_ok,
                       f"{len(pts)} 次穩定輕鬆跑：心率每 +10 bpm 飄移 +{slope * 1000:.1f}%，在 {aet:.0f} bpm 達到 5%")
