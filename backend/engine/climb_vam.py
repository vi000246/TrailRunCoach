"""
Steady-climb VAM:HR on trail runs and hikes (圖表分析 → 我的訓練 → 能力, reference only).

The athlete walks most of their climbs (docs/research/vo2max-gate-and-trail-
metric.md §2.2: 26 % of trail time is above +15 %, and no runnable +3…+8 %
climb lasts 10 min), so the climbing efficiency is measured without power:
how many metres an hour each heartbeat buys on a sustained climb, walked or
run. Uphill Athlete's benchmark climb is the same idea — the same climb at the
same HR, faster = fitter (coaching-dashboards-mountain.md §1.1, 教練經驗).

  1. a 1-s grid; grade = Δelevation over ±50 m of distance (a 100 m baseline,
     the window racepower's grade model uses);
  2. a second qualifies when grade ≥ GRADE_MIN and the athlete is moving
     (> 0.3 m/s horizontally, racepower's HIKE_REST_MS: on a 40 % slope a
     400 m/h hike is only ~1 km/h on the map, under WKO5's 1.6 km/h);
  3. runs of qualifying seconds joined across breaks ≤ JOIN_GAP_S (a step, a
     switchback that flattens, a few seconds standing; 推估) and lasting
     ≥ MIN_SEG_S are the climbs — a longer stop ends one;
  4. the first 2 min are dropped as HR lag (Hunt τ ≈ 60 s; 2 min is 推估);
  5. the rest needs HR on ≥ 90 % of its seconds and a net gain;
  6. per climb: VAM = net gain ÷ measured time × 3600 (m/h), average HR,
     VAM:HR = VAM ÷ HR (m/h per bpm), grade, and the share of seconds at a
     running cadence (≥ 65 strides/min = 130 spm, workout_review.RUN_CADENCE)
     — "跑" when ≥ half, else "走"; None without a cadence channel.

GRADE_MIN / MIN_SEG_S / JOIN_GAP_S were picked from the athlete's own data
(推估; read-only count 2026-10-02, 73 trail runs and hikes since 2025-03):
+8 % / 8 min / 30 s gave 93 climbs on 54 activities and 5 repeated routes;
10 min dropped to 54 climbs and 4 routes, 15 min to none on a repeated
route; 10-s joins split most climbs (30 at 8 min). +3 % or +5 % add a few
climbs but mix in near-flat stretches where VAM says little. Almost all of
them are walked (median running share 1 %).
Pure functions on arrays; the Dataset side is panels/climb_vam.py.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

GRADE_MIN = 0.08                # 推估 from the athlete's data (module docstring)
GRADE_HALF_M = 50.0             # ±50 m distance baseline for the grade
MIN_SEG_S = 480.0               # 推估 from the athlete's data: ≥ 8 min sustained climbing
HR_LAG_S = 120.0                # 推估: 2 × Hunt τ ≈ 60 s
JOIN_GAP_S = 30.0               # 推估: breaks this short do not end a climb
RUN_CADENCE = 65.0              # strides/min (130 spm), workout_review.RUN_CADENCE
CADENCE_SMOOTH_S = 10
MOVING_MS = 0.3                 # racepower athlete.HIKE_REST_MS
MIN_COVER = 0.9                 # share of measured seconds with HR
MAX_GRADE = 1.0                 # 推估: a net grade above 100 % (45°) is a data glitch
MAX_GAP_S = 30.0                # a sample gap longer than this is no data (workout_review.MAX_DT)

REASONS = {
    "no_hr": "沒有心率",
    "no_elevation": "沒有高度",
    "short": "連續爬坡不到門檻",
    "coverage": "心率資料不足 90%",
    "glitch": "淨坡度 > 100%（高度或 GPS 異常）",
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


def _med(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    return float(np.median(x)) if len(x) else float("nan")


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
    keep = np.concatenate([[True], np.diff(dd) > 0])      # np.interp needs increasing x
    dd, zz = dd[keep], zz[keep]
    if len(dd) < 2:
        return out
    a, b = d - half_m, d + half_m
    inside = np.isfinite(d) & (a >= dd[0]) & (b <= dd[-1])
    out[inside] = (np.interp(b[inside], dd, zz) - np.interp(a[inside], dd, zz)) / (2 * half_m)
    return out


def extract_climbs(t, dist_m, elev, hr, cadence=None, speed_kmh=None, *,
                   grade_min: float = GRADE_MIN, min_seg_s: float = MIN_SEG_S,
                   hr_lag_s: float = HR_LAG_S, join_gap_s: float = JOIN_GAP_S,
                   run_cadence: float = RUN_CADENCE) -> dict:
    """Sustained climbs of one activity (module docstring, steps 1–6).

    t: elapsed seconds; dist_m: cumulative distance (m); elev: elevation (m);
    hr (bpm), cadence (strides/min, optional), speed_kmh (optional; from the
    distance when missing) — all sample-aligned, None = no channel.

    Returns {"segments": [...], "rejected": [{"reason", "start_s", "duration_s"}],
    "reason": why there is nothing at all (or None), "longest_s": the longest
    qualifying climb, kept or not}. A segment: start_s / end_s (elapsed, the
    whole climb), duration_s, measured_s (after the HR lag), vam (m/h),
    avg_hr, vam_hr (m/h per bpm), grade, gain_m, dist_m, run_share, mode
    ("跑" / "走" / None), cadence_spm."""
    t = np.asarray([np.nan if v is None else v for v in t], dtype=float)
    n = len(t)
    out = {"segments": [], "rejected": [], "reason": None, "longest_s": 0.0}
    if n < 10 or np.isfinite(t).sum() < 10:
        out["reason"] = "short"
        return out
    if hr is None or not (np.nan_to_num(_arr(hr, n)) > 0).any():
        out["reason"] = "no_hr"
        return out
    if elev is None or np.isfinite(_arr(elev, n)).sum() < 10:
        out["reason"] = "no_elevation"
        return out
    t0, t1 = np.nanmin(t), np.nanmax(t)
    grid = np.arange(t0, t1 + 1.0)
    g = {k: _grid(t, _arr(v, n), grid) for k, v in
         (("d", dist_m), ("z", elev), ("hr", hr), ("cad", cadence))}
    if speed_kmh is not None and np.isfinite(_arr(speed_kmh, n)).any():
        v = _grid(t, _arr(speed_kmh, n), grid)
    else:
        v = np.gradient(np.nan_to_num(g["d"])) * 3.6
    g["hr"][g["hr"] <= 0] = np.nan
    has_cad = np.isfinite(g["cad"]).any() and (np.nan_to_num(g["cad"]) > 0).any()
    cad = _nanmean_window(g["cad"], CADENCE_SMOOTH_S) if has_cad else None
    grade = grade_1s(g["d"], g["z"])
    with np.errstate(invalid="ignore"):
        ok = (grade >= grade_min) & (np.nan_to_num(v) > MOVING_MS * 3.6)
    joined: list[list[int]] = []
    for a, b in _runs(ok):
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
        h = g["hr"][m0:b]
        meas = b - m0
        # ends as the median of their 10 s (a missing or spiky sample does not decide the gain)
        dz = _med(g["z"][b - 10:b]) - _med(g["z"][m0:m0 + 10])
        if np.isfinite(h).sum() < MIN_COVER * meas or not np.isfinite(dz) or dz <= 0:
            out["rejected"].append({"reason": "coverage", "start_s": float(grid[a] - t0), "duration_s": dur})
            continue
        ah = float(np.nanmean(h))
        vam = float(dz) / meas * 3600.0
        dd = g["d"][b - 1] - g["d"][m0]
        gr = float(dz / dd) if np.isfinite(dd) and dd > 0 else float(np.nanmean(grade[m0:b]))
        if not (gr <= MAX_GRADE):
            # more height than distance: an elevation or GPS glitch, not a trail
            out["rejected"].append({"reason": "glitch", "start_s": float(grid[a] - t0), "duration_s": dur})
            continue
        rs = None
        if cad is not None:
            c = cad[m0:b]
            if np.isfinite(c).sum() >= MIN_COVER * meas:
                rs = float(np.mean(c[np.isfinite(c)] >= run_cadence))
        out["segments"].append({
            "start_s": float(grid[a] - t0), "end_s": float(grid[b - 1] - t0 + 1), "duration_s": dur,
            "measured_s": float(meas), "vam": round(vam, 0), "avg_hr": round(ah, 1),
            "vam_hr": round(vam / ah, 3), "grade": round(gr, 4), "gain_m": round(float(dz), 1),
            "dist_m": None if not np.isfinite(dd) else round(float(dd), 0),
            "run_share": None if rs is None else round(rs, 2),
            "mode": None if rs is None else ("跑" if rs >= 0.5 else "走"),
            "cadence_spm": None if cad is None else round(float(np.nanmean(g["cad"][m0:b])) * 2, 0),
        })
    return out


def rolling_median(points: list[tuple[float, float]], window_days: float = 56.0) -> list[Optional[float]]:
    """For each (day, value): the median of the values in (day − window, day]
    — the 8-week rolling median."""
    out = []
    for d, _ in points:
        vals = [v for dd, v in points if d - window_days < dd <= d and v is not None]
        out.append(float(np.median(vals)) if vals else None)
    return out
