"""
Track geometry for repeated segments and routes: resampling, point-to-path
distance, the overlap ratio, discrete Fréchet distance, common same-direction
stretches between two tracks, and finding a segment's efforts in a track.

Every distance is in metres on a local equirectangular projection (x = R·Δλ·cos φ0,
y = R·Δφ), centred on the first track of each comparison — accurate to well
under 1 % over the few kilometres a comparison spans.

Methods
-------
* Overlap ratio — the fraction of one polyline's points that lie within `tol`
  metres of the other polyline (point-to-segment distance). Two paths are "the
  same" when the ratio is >= 0.8 in BOTH directions at tol = 30 m; the
  two-way test stops a short path from matching a long one that contains it.
* Discrete Fréchet distance — Eiter & Mannila, "Computing Discrete Fréchet
  Distance", Tech. Report CD-TR 94/64, TU Wien, 1994:
      c(i, j) = max(d(p_i, q_j), min(c(i-1, j), c(i-1, j-1), c(i, j-1)))
  with c(0, 0) = d(p_0, q_0); the distance is c(n-1, m-1). It is order-aware
  (a path walked backwards has a large Fréchet distance) and is reported per
  effort as a match-quality figure. The accept/reject gate is the overlap ratio
  plus the endpoint test, because one GPS spike inflates the Fréchet distance
  of an otherwise perfect match.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

R_EARTH = 6371000.0
STEP_M = 25.0              # resampling step
TOL_M = 30.0               # "on the same path"
END_TOL_M = 60.0           # start / end proximity
MIN_OVERLAP = 0.8
LEN_RANGE = (0.7, 1.4)     # an effort's path length vs the reference's


# ---------------------------------------------------------------------------
# projection / resampling
# ---------------------------------------------------------------------------

def project(lat, lon, lat0: float, lon0: float) -> np.ndarray:
    """(n, 2) metres east / north of (lat0, lon0)."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    k = math.radians(1.0) * R_EARTH
    return np.column_stack(((lon - lon0) * k * math.cos(math.radians(lat0)), (lat - lat0) * k))


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(min(1.0, a)))


def valid_fix(a, b) -> bool:
    """A usable GPS fix: present, finite, and not a device's (0, 0) 'no fix'."""
    if a is None or b is None:
        return False
    try:
        if not (math.isfinite(a) and math.isfinite(b)):
            return False
    except TypeError:
        return False
    return not (a == 0 and b == 0) and -90 <= a <= 90 and -180 <= b <= 180


def resample_indices(lat: Sequence, lon: Sequence, step_m: float = STEP_M,
                     keep: Optional[set] = None) -> list[int]:
    """Raw sample indices spaced >= step_m apart along the GPS path (the first
    and last valid fix always kept), plus every index in `keep` that has a fix.
    Keeping raw samples, not interpolated points, means anything measured
    between two kept points is an exact slice of the raw data."""
    keep = keep or set()
    out: list[int] = []
    last = None
    acc = 0.0
    prev = None
    last_valid = None
    for i, (a, b) in enumerate(zip(lat, lon)):
        if not valid_fix(a, b):
            continue
        if prev is not None:
            acc += haversine_m(prev[0], prev[1], a, b)
        prev = (a, b)
        last_valid = i
        if last is None or acc >= step_m or i in keep:
            out.append(i)
            last = i
            acc = 0.0
    if last_valid is not None and (not out or out[-1] != last_valid):
        out.append(last_valid)
    return out


def path_length(xy: np.ndarray) -> np.ndarray:
    """Cumulative length (m) along an (n, 2) polyline, starting at 0."""
    if len(xy) == 0:
        return np.zeros(0)
    seg = np.hypot(*np.diff(xy, axis=0).T) if len(xy) > 1 else np.zeros(0)
    return np.concatenate(([0.0], np.cumsum(seg)))


# ---------------------------------------------------------------------------
# distances
# ---------------------------------------------------------------------------

def dist_to_path(p: np.ndarray, q: np.ndarray, chunk: int = 256) -> np.ndarray:
    """For each point of p (n, 2), the distance to the polyline q (m, 2)."""
    if len(q) == 0:
        return np.full(len(p), np.inf)
    if len(q) == 1:
        return np.hypot(*(p - q[0]).T)
    a, b = q[:-1], q[1:]
    ab = b - a
    ab2 = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    out = np.empty(len(p))
    for s in range(0, len(p), chunk):
        pp = p[s:s + chunk][:, None, :]                       # (k, 1, 2)
        t = np.clip(((pp - a) * ab).sum(axis=2) / ab2, 0.0, 1.0)  # (k, m-1)
        proj = a + t[..., None] * ab
        out[s:s + chunk] = np.sqrt(((pp - proj) ** 2).sum(axis=2)).min(axis=1)
    return out


def overlap_ratio(p: np.ndarray, q: np.ndarray, tol: float = TOL_M) -> float:
    """Fraction of p's points within tol metres of the polyline q."""
    if len(p) == 0 or len(q) == 0:
        return 0.0
    if len(q) > 2:
        # segments of q with an end inside p's box (+ one segment of slack)
        # are the only ones that can be within tol of p
        lo, hi = p.min(axis=0) - tol - 200.0, p.max(axis=0) + tol + 200.0
        ins = (q[:, 0] >= lo[0]) & (q[:, 0] <= hi[0]) & (q[:, 1] >= lo[1]) & (q[:, 1] <= hi[1])
        if not ins.any():
            return 0.0
        seg = ins[:-1] | ins[1:]
        k = np.flatnonzero(seg)
        if len(k) < len(q) - 1:
            pts = np.unique(np.concatenate((k, k + 1)))
            # keep consecutive pairs only: split where indices jump
            d = np.full(len(p), np.inf)
            runs = np.split(pts, np.flatnonzero(np.diff(pts) > 1) + 1)
            for r in runs:
                d = np.minimum(d, dist_to_path(p, q[r]))
            return float((d <= tol).mean())
    return float((dist_to_path(p, q) <= tol).mean())


def mutual_overlap(p: np.ndarray, q: np.ndarray, tol: float = TOL_M,
                   need: Optional[float] = None) -> float:
    """min(overlap p->q, overlap q->p). With `need`, returns early once the
    first direction already falls short of it."""
    a = overlap_ratio(p, q, tol)
    if need is not None and a < need:
        return a
    return min(a, overlap_ratio(q, p, tol))


def discrete_frechet(p: np.ndarray, q: np.ndarray, max_points: int = 150) -> float:
    """Discrete Fréchet distance (Eiter & Mannila 1994), metres. Curves longer
    than max_points are thinned evenly first (at 25 m spacing the thinning
    shifts the result by at most about half the new spacing)."""
    if len(p) == 0 or len(q) == 0:
        return math.inf
    p, q = _thin(p, max_points), _thin(q, max_points)
    d = np.sqrt(((p[:, None, :] - q[None, :, :]) ** 2).sum(axis=2)).tolist()
    n, m = len(d), len(d[0])
    # row by row: prev = c(i-1, ·), row = c(i, ·); plain floats are much
    # faster than numpy scalar indexing here
    prev = [0.0] * m
    prev[0] = d[0][0]
    for j in range(1, m):
        prev[j] = max(prev[j - 1], d[0][j])
    for i in range(1, n):
        di = d[i]
        row = [0.0] * m
        row[0] = max(prev[0], di[0])
        left = row[0]
        for j in range(1, m):
            best = prev[j]
            if prev[j - 1] < best:
                best = prev[j - 1]
            if left < best:
                best = left
            left = di[j] if di[j] > best else best
            row[j] = left
        prev = row
    return float(prev[m - 1])


def _thin(xy: np.ndarray, k: int) -> np.ndarray:
    if len(xy) <= k:
        return xy
    idx = np.unique(np.linspace(0, len(xy) - 1, k).round().astype(int))
    return xy[idx]


# ---------------------------------------------------------------------------
# spatial hash
# ---------------------------------------------------------------------------

class Grid:
    """Points of one polyline bucketed into square cells of `cell` metres, for
    'which points are within r of here' with r <= cell."""

    def __init__(self, xy: np.ndarray, cell: float = 40.0):
        self.xy, self.cell = xy, cell
        self.buckets: dict[tuple[int, int], list[int]] = {}
        for i, (x, y) in enumerate(xy):
            self.buckets.setdefault((int(x // cell), int(y // cell)), []).append(i)

    def near(self, x: float, y: float, r: float) -> list[int]:
        cx, cy = int(x // self.cell), int(y // self.cell)
        out = []
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for i in self.buckets.get((gx, gy), ()):
                    px, py = self.xy[i]
                    if (px - x) ** 2 + (py - y) ** 2 <= r * r:
                        out.append(i)
        return out


# ---------------------------------------------------------------------------
# common stretches between two tracks
# ---------------------------------------------------------------------------

def common_runs(a: np.ndarray, b: np.ndarray, tol: float = TOL_M, min_len: float = 500.0,
                max_gap: int = 2) -> list[tuple[int, int, int, int]]:
    """Maximal stretches where track a follows track b in the SAME direction.

    Walks a; each point is paired with a point of b within tol, preferring the
    one just ahead of the previous pairing, so b's index keeps increasing. A
    stretch survives up to `max_gap` unpaired points (a GPS wobble) and is kept
    when it is at least min_len metres of a. Returns (a_i0, a_i1, b_j0, b_j1).
    """
    if len(a) < 2 or len(b) < 2:
        return []
    la = path_length(a)
    # only the part of b inside a's bounding box can be near a
    lo, hi = a.min(axis=0) - tol, a.max(axis=0) + tol
    inbox = np.flatnonzero((b[:, 0] >= lo[0]) & (b[:, 0] <= hi[0]) & (b[:, 1] >= lo[1]) & (b[:, 1] <= hi[1]))
    if len(inbox) < 2:
        return []
    bb = b[inbox]
    # which points of b are within tol of each point of a (dense, in row chunks)
    near_rows: list = [None] * len(a)
    tol2 = tol * tol
    for s in range(0, len(a), 512):
        blk = a[s:s + 512]
        d2 = (blk[:, None, 0] - bb[None, :, 0]) ** 2 + (blk[:, None, 1] - bb[None, :, 1]) ** 2
        m = d2 <= tol2
        for k in np.flatnonzero(m.any(axis=1)):
            near_rows[s + k] = inbox[np.flatnonzero(m[k])]
    empty = np.zeros(0, dtype=int)
    runs = []
    cur = None          # [i0, i_last, j0, j_last]
    miss = 0
    for i in range(len(a)):
        near = near_rows[i]
        if near is None:
            if cur is None:
                continue
            near = empty
        x, y = a[i]
        pick = None
        if len(near) and cur is not None:
            jl = cur[3]
            ahead = near[(near >= jl - 1) & (near <= jl + 12)]
            if len(ahead):
                pick = int(ahead[np.argmin(np.abs(ahead - (jl + 1)))])
        if pick is not None:
            cur[1], cur[3] = i, max(cur[3], pick)
            miss = 0
            continue
        if cur is not None:
            miss += 1
            if miss <= max_gap:
                continue
            runs.append(cur)
            cur, miss = None, 0
        if len(near):
            j = int(near[np.argmin(((b[near] - (x, y)) ** 2).sum(axis=1))])
            cur, miss = [i, i, j, j], 0
    if cur is not None:
        runs.append(cur)
    return [tuple(r) for r in runs if la[r[1]] - la[r[0]] >= min_len and r[3] > r[2]]


# ---------------------------------------------------------------------------
# finding a segment's efforts in a track
# ---------------------------------------------------------------------------

def _local_minima(dist: np.ndarray, within: float) -> list[int]:
    """Index of the closest point in each run of consecutive points within `within`."""
    out, i, n = [], 0, len(dist)
    while i < n:
        if dist[i] > within:
            i += 1
            continue
        j = i
        while j + 1 < n and dist[j + 1] <= within:
            j += 1
        out.append(i + int(np.argmin(dist[i:j + 1])))
        i = j + 1
    return out


def find_efforts(track: np.ndarray, ref: np.ndarray, end_tol: float = END_TOL_M,
                 tol: float = TOL_M, min_overlap: float = MIN_OVERLAP,
                 len_range: tuple[float, float] = LEN_RANGE) -> list[tuple[int, int, float]]:
    """Every traversal of the reference path `ref` inside `track`, in the
    reference's direction: (i0, i1, overlap) with track[i0] the point closest
    to the reference start and track[i1] closest to its end. A traversal
    counts when both endpoints are within end_tol, its length is within
    len_range of the reference's, and the overlap ratio is >= min_overlap both
    ways. Several traversals of one track (repeats) are all returned."""
    if len(track) < 2 or len(ref) < 2:
        return []
    L = float(path_length(ref)[-1])
    if L <= 0:
        return []
    lt = path_length(track)
    ds = np.hypot(*(track - ref[0]).T)
    de = np.hypot(*(track - ref[-1]).T)
    starts = _local_minima(ds, end_tol)
    ends = _local_minima(de, end_tol)
    out = []
    after = -1
    for s in starts:
        if s <= after:
            continue
        for e in ends:
            if e <= s:
                continue
            length = lt[e] - lt[s]
            if length < len_range[0] * L:
                continue
            if length > len_range[1] * L:
                break
            sub = track[s:e + 1]
            ov = min(overlap_ratio(sub, ref, tol), overlap_ratio(ref, sub, tol))
            if ov >= min_overlap:
                out.append((s, e, ov))
                after = e
                break
    return out


def along_residual(ref: np.ndarray, pts: np.ndarray, window: int = 12, tol: float = TOL_M
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Follow `pts` in order along `ref`: each point is projected onto the
    part of ref just ahead of the previous projection (`window` segments), so
    the walk only moves forward. When that projection is off the path (a GPS
    gap), the rest of ref ahead is searched, but a jump along ref is accepted
    only if it is no longer than 1.5 x the distance the track itself covered
    since its last on-path point + 100 m. Returns (distance along ref in m,
    non-decreasing; distance from each point to its projection in m). A track
    walked in the opposite direction cannot follow ref and gets large
    residuals — that is the direction test."""
    if len(ref) < 2 or len(pts) == 0:
        return np.zeros(len(pts)), np.full(len(pts), np.inf)
    a, b = ref[:-1], ref[1:]
    ab = b - a
    ab2 = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    cum = path_length(ref)
    lp = path_length(pts)
    out = np.empty(len(pts))
    res = np.empty(len(pts))
    prev_k = 0
    good_s, good_l = 0.0, 0.0

    def best(p, lo, hi):
        aa, abw, ab2w = a[lo:hi], ab[lo:hi], ab2[lo:hi]
        t = np.clip(((p - aa) * abw).sum(axis=1) / ab2w, 0.0, 1.0)
        d2 = ((p - (aa + t[:, None] * abw)) ** 2).sum(axis=1)
        k = int(np.argmin(d2))
        return lo + k, cum[lo + k] + t[k] * math.sqrt(ab2w[k]), math.sqrt(d2[k])

    for n, p in enumerate(pts):
        k, s, r = best(p, prev_k, min(len(a), prev_k + window))
        if r > 2 * tol and prev_k + window < len(a):
            k2, s2, r2 = best(p, prev_k, len(a))
            if r2 <= tol and s2 - good_s <= 1.5 * (lp[n] - good_l) + 100.0:
                k, s, r = k2, s2, r2
        prev_k = k
        out[n], res[n] = s, r
        if r <= tol:
            good_s, good_l = s, lp[n]
    return np.maximum.accumulate(out), res


def along(ref: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Distance along `ref` (m) of each point's in-order projection onto it."""
    return along_residual(ref, pts)[0]
