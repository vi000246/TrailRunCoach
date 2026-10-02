"""
Steady-climb Pw:HR on trail runs (圖表分析 → 我的訓練 → 能力, reference only).

docs/research/aerobic-base-readiness.md and vo2max-gate-and-trail-metric.md
§2: on a trail run, Stryd power is only a usable load measure on runnable
3–8 % grades (van Rassel 2026: fixed Stryd power ≈ fixed metabolic load on
0–8 %; Gravina-Cognetti 2025: power–VO2 correlation falls to ρ 0.69 at +7 %
and power uncouples from VO2 downhill). So the trail run's own aerobic
efficiency is measured only there, segment by segment:

  1. a 1-s grid; grade = Δelevation over ±50 m of distance (a 100 m
     baseline, the window racepower's grade model uses);
  2. a second qualifies when grade is +3 … +8 % (Stryd's validated range),
     the athlete is moving (> 1.6 km/h, WKO5's threshold) and running:
     10-s mean cadence ≥ 65 strides/min = 130 spm (workout_review.RUN_CADENCE)
     — so descents, grades > 8 % and hiking are out;
  3. runs of qualifying seconds, joined across breaks ≤ 10 s (a step over a
     root, a GPS jitter; 推估), lasting ≥ 10 min, are the segments;
  4. the first 2 min of each are dropped as HR lag (Hunt τ ≈ 60 s, so 2τ;
     the 2 min is 推估);
  5. the rest must have power and HR on ≥ 90 % of its seconds and steady
     power: VI = NP30 / AP ≤ 1.04 (the drift v2 rule, workout_review.
     DRIFT_MAX_VI, 推估 calibrated on the athlete's runs);
  6. per segment: average power, average HR, Pw:HR = W / bpm, duration and
     grade (net gain / distance over the measured part).

Pure functions on arrays; the Dataset side is panels/climb_pwhr.py.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

GRADE_MIN = 0.03                # Stryd validated range (van Rassel 2026: 0–8 %); below 3 % is flat
GRADE_MAX = 0.08
GRADE_HALF_M = 50.0             # ±50 m distance baseline for the grade
MIN_SEG_S = 600.0               # ≥ 10 min continuous climbing
HR_LAG_S = 120.0                # 推估: 2 × Hunt τ ≈ 60 s
JOIN_GAP_S = 10.0               # 推估: breaks this short do not end a segment
RUN_CADENCE = 65.0              # strides/min (130 spm), workout_review.RUN_CADENCE
CADENCE_SMOOTH_S = 10
MOVING_KMH = 1.6                # WKO5's moving threshold (workout_review.STOP_KMH)
MAX_VI = 1.04                   # workout_review.DRIFT_MAX_VI (drift v2, 推估 calibrated)
MIN_COVER = 0.9                 # share of measured seconds with power / HR
MAX_GAP_S = 30.0                # a sample gap longer than this is no data (workout_review.MAX_DT)

REASONS = {
    "no_cadence": "沒有步頻，分不出跑和走",
    "no_power": "沒有功率",
    "no_hr": "沒有心率",
    "short": "連續爬坡不到 10 分鐘",
    "coverage": "功率或心率資料不足 90%",
    "vi": "功率起伏大（VI > 1.04）",
}


def _arr(a, n: int) -> np.ndarray:
    if a is None:
        return np.full(n, np.nan)
    a = np.asarray([np.nan if v is None else v for v in a], dtype=float)
    if len(a) >= n:
        return a[:n]
    return np.concatenate([a, np.full(n - len(a), np.nan)])


def _grid(t: np.ndarray, x: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """x linearly on the 1-s grid; NaN outside the samples and across gaps > 30 s."""
    ok = np.isfinite(t) & np.isfinite(x)
    if ok.sum() < 2:
        return np.full(len(grid), np.nan)
    tt, xx = t[ok], x[ok]
    y = np.interp(grid, tt, xx)
    j = np.clip(np.searchsorted(tt, grid), 1, len(tt) - 1)
    y[(tt[j] - tt[j - 1]) > MAX_GAP_S] = np.nan
    y[(grid < tt[0]) | (grid > tt[-1])] = np.nan
    return y


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[(a, b)) of each unbroken run of True."""
    e = np.diff(np.concatenate([[0], np.asarray(mask, dtype=int), [0]]))
    return list(zip(np.flatnonzero(e == 1).tolist(), np.flatnonzero(e == -1).tolist()))


def _nanmean_window(x: np.ndarray, w: int) -> np.ndarray:
    """Trailing mean over w seconds, ignoring NaN; NaN when the window has none."""
    ok = np.isfinite(x)
    c = np.concatenate([[0.0], np.cumsum(np.where(ok, x, 0.0))])
    k = np.concatenate([[0], np.cumsum(ok)])
    i = np.arange(len(x))
    lo = np.maximum(0, i - w + 1)
    num, den = c[i + 1] - c[lo], k[i + 1] - k[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / np.maximum(den, 1), np.nan)


def grade_1s(dist_m: np.ndarray, elev: np.ndarray, half_m: float = GRADE_HALF_M) -> np.ndarray:
    """Grade at each second: (z(d + h) − z(d − h)) / 2h along distance; NaN
    where the ±h window leaves the recorded distance or elevation is missing."""
    n = len(dist_m)
    out = np.full(n, np.nan)
    ok = np.isfinite(dist_m) & np.isfinite(elev)
    if ok.sum() < 2:
        return out
    d = np.maximum.accumulate(np.where(np.isfinite(dist_m), dist_m, -np.inf))
    dd, zz = d[ok], elev[ok]
    # np.interp needs increasing x: keep the first sample of each distance
    keep = np.concatenate([[True], np.diff(dd) > 0])
    dd, zz = dd[keep], zz[keep]
    if len(dd) < 2:
        return out
    a, b = d - half_m, d + half_m
    inside = np.isfinite(d) & (a >= dd[0]) & (b <= dd[-1])
    out[inside] = (np.interp(b[inside], dd, zz) - np.interp(a[inside], dd, zz)) / (2 * half_m)
    return out


def power_vi(p: np.ndarray) -> tuple[Optional[float], Optional[float]]:
    """(VI, CV30) over one unbroken stretch of 1-s power (workout_review.power_vi):
    30-s rolling means; NP = their 4th-power mean ^ ¼; VI = NP / AP."""
    ok = np.isfinite(p)
    rs = [np.convolve(p[a:b], np.ones(30) / 30, "valid") for a, b in _runs(ok) if b - a >= 30]
    if not rs:
        return None, None
    r = np.concatenate(rs)
    ap = float(np.mean(p[ok]))
    if ap <= 0 or r.mean() <= 0:
        return None, None
    return float(np.mean(r ** 4) ** 0.25 / ap), float(r.std() / r.mean())


def extract_segments(t, dist_m, elev, power, hr, cadence, speed_kmh=None, *,
                     grade_min: float = GRADE_MIN, grade_max: float = GRADE_MAX,
                     min_seg_s: float = MIN_SEG_S, hr_lag_s: float = HR_LAG_S,
                     join_gap_s: float = JOIN_GAP_S, run_cadence: float = RUN_CADENCE,
                     max_vi: float = MAX_VI) -> dict:
    """Steady climbing segments of one activity (module docstring, steps 1–6).

    t: elapsed seconds; dist_m: cumulative distance (m); elev: elevation (m);
    power (W), hr (bpm), cadence (strides/min), speed_kmh (optional; from the
    distance when missing) — all sample-aligned, None = no channel.

    Returns {"segments": [...], "rejected": [{"reason", "start_s", "duration_s"}],
    "reason": why there is nothing at all (or None), "longest_s": the longest
    qualifying climb, kept or not}. A segment:
    start_s / end_s (elapsed, the whole climb), measured_s (after the HR lag),
    avg_power, avg_hr, pwhr (W/bpm), grade, gain_m, dist_m, vi, cv30, cadence_spm."""
    t = np.asarray([np.nan if v is None else v for v in t], dtype=float)
    n = len(t)
    out = {"segments": [], "rejected": [], "reason": None}
    if n < 10 or np.isfinite(t).sum() < 10:
        out["reason"] = "short"
        return out
    if cadence is None or not np.isfinite(_arr(cadence, n)).any():
        out["reason"] = "no_cadence"
        return out
    if power is None or not (np.nan_to_num(_arr(power, n)) > 0).any():
        out["reason"] = "no_power"
        return out
    if hr is None or not (np.nan_to_num(_arr(hr, n)) > 0).any():
        out["reason"] = "no_hr"
        return out
    t0, t1 = np.nanmin(t), np.nanmax(t)
    grid = np.arange(t0, t1 + 1.0)
    g = {k: _grid(t, _arr(v, n), grid) for k, v in
         (("d", dist_m), ("z", elev), ("p", power), ("hr", hr), ("cad", cadence))}
    if speed_kmh is not None and np.isfinite(_arr(speed_kmh, n)).any():
        v = _grid(t, _arr(speed_kmh, n), grid)
    else:
        v = np.gradient(np.nan_to_num(g["d"])) * 3.6
    for k in ("p", "hr", "cad"):
        g[k][g[k] <= 0] = np.nan
    grade = grade_1s(g["d"], g["z"])
    cad = _nanmean_window(g["cad"], CADENCE_SMOOTH_S)
    with np.errstate(invalid="ignore"):
        ok = (grade >= grade_min) & (grade <= grade_max) & (cad >= run_cadence) & (v > MOVING_KMH)
    # join runs across short breaks
    runs = _runs(ok)
    joined: list[list[int]] = []
    for a, b in runs:
        if joined and a - joined[-1][1] <= join_gap_s:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    out["longest_s"] = float(max((b - a for a, b in joined), default=0))
    lag = int(round(hr_lag_s))
    for a, b in joined:
        dur = float(b - a)
        if dur < min_seg_s:
            if dur >= min_seg_s / 2:
                out["rejected"].append({"reason": "short", "start_s": float(grid[a] - t0), "duration_s": dur})
            continue
        m0 = a + lag
        p, h = g["p"][m0:b], g["hr"][m0:b]
        meas = b - m0
        if np.isfinite(p).sum() < MIN_COVER * meas or np.isfinite(h).sum() < MIN_COVER * meas:
            out["rejected"].append({"reason": "coverage", "start_s": float(grid[a] - t0), "duration_s": dur})
            continue
        vi, cv = power_vi(p)
        if vi is None or vi > max_vi:
            out["rejected"].append({"reason": "vi", "start_s": float(grid[a] - t0), "duration_s": dur,
                                    "vi": None if vi is None else round(vi, 3)})
            continue
        ap, ah = float(np.nanmean(p)), float(np.nanmean(h))
        dd = g["d"][b - 1] - g["d"][m0]
        dz = g["z"][b - 1] - g["z"][m0]
        gr = float(dz / dd) if np.isfinite(dd) and dd > 0 and np.isfinite(dz) else float(np.nanmean(grade[m0:b]))
        out["segments"].append({
            "start_s": float(grid[a] - t0), "end_s": float(grid[b - 1] - t0 + 1), "duration_s": dur,
            "measured_s": float(meas), "avg_power": round(ap, 1), "avg_hr": round(ah, 1),
            "pwhr": round(ap / ah, 4), "grade": round(gr, 4),
            "gain_m": None if not np.isfinite(dz) else round(float(dz), 1),
            "dist_m": None if not np.isfinite(dd) else round(float(dd), 0),
            "vi": round(vi, 3), "cv30": None if cv is None else round(cv, 3),
            "cadence_spm": round(float(np.nanmean(g["cad"][m0:b])) * 2, 0),
        })
    return out


def rolling_median(points: list[tuple[float, float]], window_days: float = 56.0) -> list[Optional[float]]:
    """For each (day, value) in day order: the median of the values in
    (day − window, day] — the 8-week rolling median."""
    out = []
    for d, _ in points:
        vals = [v for dd, v in points if d - window_days < dd <= d and v is not None]
        out.append(float(np.median(vals)) if vals else None)
    return out
