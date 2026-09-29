"""
WKO5 mean-max — reconstructed from WKO5.exe 5.0.587 (core @0x5c0230,
duration grid @0x4bda20) and VERIFIED bit-exact against WKO5's cached
meanmax(power) curves: 49,188/49,188 points on 359/359 power workouts
(2026-09-29).

Each sample i has an x increment dx[i], a weight w[i] (both = deltatime on
the time axis, first sample measured from 0) and a value v[i] (None =
invalid). For each duration d a continuous window of exactly d x-units slides
over the samples, pro-rating the end samples fractionally; two alignments
are tried per start sample. avg = (sum w*v - trims) / div, where div is the
trimmed valid weight if (trimmed valid / total weight) > 0.98, else the total
weight (invalid samples count as time but add no value).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

EPS = 2.220446049250313e-16
DBL_MIN = 2.2250738585072014e-308
FIXED_DURATIONS = (5, 10, 20, 30, 40, 50, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600,
                   900, 1200, 1800, 2400, 2700, 3600)


def _almost_equal(a: float, b: float, ulp: float = 10.0) -> bool:
    diff = abs(a - b)
    return abs(a + b) * EPS * ulp > diff or diff < DBL_MIN


def _valid(x) -> bool:
    return x is not None and not (isinstance(x, float) and (math.isnan(x) or x >= 1.7976931348623157e308))


def duration_grid(total: float) -> list[int]:
    """Durations WKO5 evaluates for a workout of `total` seconds."""
    s, i = set(), 0
    while True:
        d = int(math.floor(1.05 ** i + 0.5))
        if not total >= d:
            break
        s.add(d)
        i += 1
    s.update(f for f in FIXED_DURATIONS if f <= total)
    return sorted(s)


def meanmax_curve(dx: Sequence[float], w: Sequence[float], v: Sequence[Optional[float]],
                  grid: Sequence[float]) -> list[Optional[float]]:
    """Best average over a window of each duration in `grid`.

    Uses the vectorised path when the samples are well-formed (see
    `_fast_meanmax`) and the literal reconstruction of WKO5's loop otherwise.
    `test_wko5_perf.py` asserts the two agree on real workouts.
    """
    fast = _fast_meanmax(dx, w, v, grid)
    return fast if fast is not None else meanmax_curve_reference(dx, w, v, grid)


def _fast_meanmax(dx: Sequence[float], w: Sequence[float], v: Sequence[Optional[float]],
                  grid: Sequence[float]) -> Optional[list[Optional[float]]]:
    """Vectorised equivalent of `meanmax_curve_reference`, or None when the
    inputs fall outside the cases it provably covers (weights not equal to the
    x increments, or any increment non-finite or <= 0 — the reference's
    accumulate and un-accumulate paths disagree on those, so only it is safe).

    For well-formed samples the reference evaluates exactly two window families
    per duration d, which prefix sums give directly. With X the cumulative end
    position of each sample:

      A(k): window [X[k], X[k] + d] — ends inside sample j, which is trimmed
      B(j): window [X[j+1] - d, X[j+1]] — starts inside sample k, trimmed

    The reference also stops its start-sample loop once the FIRST duration in
    `grid` can no longer slide, and drops a duration once its window runs off
    the end; both bounds are reproduced here so the two agree window for window.
    """
    n = len(v)
    if n == 0 or len(grid) == 0:
        return None
    dxa = np.asarray(dx, dtype=float)
    if dxa.shape != (n,):
        return None
    if w is not dx:
        wa = np.asarray(w, dtype=float)
        if wa.shape != (n,) or not np.array_equal(wa, dxa):
            return None
    if not np.all(np.isfinite(dxa)) or not np.all(dxa > 0.0):
        return None

    va = np.array([x if _valid(x) else np.nan for x in v], dtype=float)
    vok = ~np.isnan(va)
    vfill = np.nan_to_num(va)

    X = np.zeros(n + 1)
    np.cumsum(dxa, out=X[1:])                                  # totw prefix (w == dx)
    SVW = np.zeros(n + 1)
    np.cumsum(np.where(vok, dxa, 0.0), out=SVW[1:])             # valid weight prefix
    SSV = np.zeros(n + 1)
    np.cumsum(np.where(vok, dxa * vfill, 0.0), out=SSV[1:])     # weight*value prefix

    def end_sample(d: float) -> np.ndarray:
        """For every start position X[k], the sample the window of length d ends
        in; n means the window runs past the last sample."""
        return np.searchsorted(X, X + d, side="left") - 1

    def first_at_least(arr: np.ndarray, limit: int) -> int:
        hit = np.nonzero(arr[:n] >= limit)[0]
        return int(hit[0]) if hit.size else n

    # The reference's start-sample loop ends when grid[0]'s window stops sliding.
    k_stop = first_at_least(end_sample(float(grid[0])), n - 1)

    def window_avg(k, j, f_start, f_end):
        totw = X[j + 1] - X[k]
        validw = SVW[j + 1] - SVW[k]
        swv = SSV[j + 1] - SSV[k]
        a2 = np.where(vok[k], dxa[k], 0.0) * f_start
        b2 = np.where(vok[j], dxa[j], 0.0) * f_end
        validw2 = validw - a2 - b2
        div = np.where(validw2 / totw > 0.98, validw2, totw)
        num = (swv - np.where(vok[k], vfill[k] * dxa[k], 0.0) * f_start
                   - np.where(vok[j], vfill[j] * dxa[j], 0.0) * f_end)
        return np.where(div > 0, num / div, 0.0)

    out: list[Optional[float]] = []
    for d in grid:
        d = float(d)
        jd = end_sample(d)
        kd = min(k_stop, first_at_least(jd, n))   # start samples this duration sees
        if kd <= 0:
            out.append(None)
            continue
        best = -np.inf
        ka = np.arange(kd)
        ja = jd[:kd]
        best = max(best, window_avg(ka, ja, 0.0, (X[ja + 1] - X[ka] - d) / dxa[ja]).max())
        lo, hi = int(jd[0]), min(int(jd[kd]), n)
        if hi > lo:
            jb = np.arange(lo, hi)
            kb = np.searchsorted(X, X[jb + 1] - d, side="right") - 1
            best = max(best, window_avg(kb, jb, (X[jb + 1] - X[kb] - d) / dxa[kb], 0.0).max())
        out.append(float(best))
    return out


def meanmax_curve_reference(dx: Sequence[float], w: Sequence[float], v: Sequence[Optional[float]],
                            grid: Sequence[float]) -> list[Optional[float]]:
    """Literal reconstruction of WKO5's loop — the correctness reference."""
    n = len(v)
    G = [dict(d=float(d), acc=0.0, totw=0.0, validw=0.0, swv=0.0, j=-1, best=None) for d in grid]
    if n == 0 or not G:
        return [None] * len(G)
    # phase 1: fill each window from the first sample until it spans d
    for i in range(n):
        any_unfilled = False
        for g in G:
            if g["d"] > g["acc"]:
                wi, vi, xi = w[i], v[i], dx[i]
                if _valid(wi) and wi != 0:
                    if _valid(vi):
                        g["validw"] += wi
                        g["swv"] += wi * vi
                    g["totw"] += wi
                if _valid(xi) and xi != 0:
                    g["acc"] += xi
                if g["acc"] >= g["d"]:
                    g["j"] = i
            if g["j"] == -1:
                any_unfilled = True
        if not any_unfilled:
            break
    # phase 2: slide the window start
    for k in range(n):
        if G[0]["j"] == -1 or G[0]["j"] >= n - 1:
            break
        dxk, wk, vk = dx[k], w[k], v[k]
        vk_ok = _valid(vk)
        for g in G:
            if g["j"] == -1 or g["j"] >= n:
                break
            f_start = 0.0
            while g["j"] < n and _almost_equal(dx[g["j"]], 0.0):
                g["j"] += 1
            if g["j"] >= n:
                break
            j = g["j"]
            dxj, wj, vj = dx[j], w[j], v[j]
            f_end = (g["acc"] - g["d"]) / dxj
            while True:
                vj_ok = _valid(vj)
                a2 = (wk if vk_ok else 0.0) * f_start
                b2 = (wj if vj_ok else 0.0) * f_end
                validw2 = g["validw"] - a2 - b2
                div = g["totw"]
                if validw2 / div > 0.98:
                    div = validw2
                if div > 0:
                    avg = (g["swv"] - (vk if vk_ok else 0.0) * wk * f_start
                           - (vj if vj_ok else 0.0) * wj * f_end) / div
                else:
                    avg = 0.0
                if g["best"] is None or avg > g["best"]:
                    g["best"] = avg
                if f_end == 0.0:
                    g["j"] += 1
                    if g["j"] < n:
                        j = g["j"]
                        dxj, wj, vj = dx[j], w[j], v[j]
                        g["acc"] += dxj
                        g["totw"] += wj
                        if _valid(vj):
                            g["swv"] += vj * wj
                            g["validw"] += wj
                f_end = 0.0
                f_start = (g["acc"] - g["d"]) / dxk if dxk > 0 else 1.0
                if dxk > 0 and g["d"] > g["acc"] - dxk and g["j"] < n:
                    continue
                break
            g["acc"] -= dxk
            g["totw"] -= wk
            if vk_ok:
                g["swv"] -= vk * wk
                g["validw"] -= wk
    return [g["best"] for g in G]


def meanmax_time(t: Sequence[float], v: Sequence[Optional[float]], start: float = 0.0,
                 durations: Optional[Sequence[float]] = None):
    """Workout-level meanmax on the time axis -> (durations, best averages)."""
    dt = [t[i] - (t[i - 1] if i else start) for i in range(len(t))]
    grid = list(durations) if durations is not None else (duration_grid(t[-1]) if t else [])
    return grid, meanmax_curve(dt, dt, v, grid)
