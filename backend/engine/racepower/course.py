"""
Course segmentation — docs/research/racepower-v2.md §6.5 (GPX) and §4 step 2
(manual). Pure functions on numpy arrays.

    Track → haversine distance → 10 m resample → median + Gaussian smoothing
    → 3 m hysteresis gain/loss → Douglas–Peucker → classify → merge short
    segments → split long flats per km → per-segment stats + warnings.

Status per step (§3A): Douglas–Peucker is an established algorithm (Douglas
& Peucker 1973, Cartographica 10(2):112–122; the reference is cited from
memory) and is 已驗證 by its property test (V-DP). The smoothing / hysteresis
recipe is our own (推估) and 待驗證 against barometric gain (V-SM checks the
synthetic case). The class thresholds (±2 %, ±15 %) are our own; the 15 % /
28 % walk labels follow Giovanelli et al. 2016 (J Appl Physiol 120:370–375,
walking cheaper at all angles other than 9.4°) and Ortiz, Giovanelli & Kram
2017 (Eur J Appl Physiol 117:1869–1876, 30° incline) — 已驗證 conversion
(9.4° = 16.6 %, 15.8° = 28.3 %), labels only (V-CL).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

EARTH_R = 6371008.8            # mean Earth radius, m
DUP_M = 0.5
JUMP_MS = 60.0 / 3.6
STEP_M = 10.0
DEFAULT_SIGMA_M = 50.0
HYST_M = 3.0
DEFAULT_EPS_M = 10.0
FLAT_PCT = 2.0
STEEP_PCT = 15.0
RUN_WALK_PCT = 15.0
WALK_PCT = 28.0
FLAT_SPLIT_M = 3000.0
PROFILE_MAX = 1500
CAMP_WORDS = ("營地", "山屋", "營", "camp", "hut", "山莊", "避難")

CLASSES = {
    "steep_down": "陡下", "down": "下坡", "flat": "平", "up": "上坡", "steep_up": "陡上",
}


# ---- geometry -----------------------------------------------------------------

def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres (vectorised)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.sqrt(np.minimum(1.0, a)))


def _fill(vals: Sequence[Optional[float]]) -> np.ndarray:
    a = np.array([np.nan if v is None else float(v) for v in vals], float)
    ok = np.isfinite(a)
    if not ok.any():
        return a
    idx = np.arange(len(a))
    return np.interp(idx, idx[ok], a[ok])


def track_distance(track) -> dict:
    """Cumulative horizontal distance along a Track, dropping duplicate
    points (< 0.5 m) and GPS jumps (implied speed > 60 km/h, only when the
    track has times). With `track.dist` (device distance of the athlete's
    own activity) that distance is used instead, keeping points where it
    advances ≥ 0.5 m. Returns {"d", "z", "t", "keep"} (numpy, kept points)."""
    lat = np.asarray(track.lat, float)
    lon = np.asarray(track.lon, float)
    z = _fill(track.ele)
    tt = None if not track.time else _fill(track.time)
    dev = getattr(track, "dist", None)
    if dev is not None:
        dv = np.asarray(dev, float)
        keep = [0]
        for i in range(1, len(dv)):
            if dv[i] - dv[keep[-1]] >= DUP_M:
                keep.append(i)
        k = np.array(keep)
        return {"d": dv[k] - dv[0], "z": z[k], "t": None if tt is None else tt[k], "keep": k}
    keep = [0]
    d = [0.0]
    for i in range(1, len(lat)):
        j = keep[-1]
        step = float(haversine(lat[j], lon[j], lat[i], lon[i]))
        if step < DUP_M:
            continue
        if tt is not None:
            dt_ = tt[i] - tt[j]
            if dt_ > 0 and step / dt_ > JUMP_MS:
                continue
        keep.append(i)
        d.append(d[-1] + step)
    k = np.array(keep)
    return {"d": np.array(d), "z": z[k], "t": None if tt is None else tt[k], "keep": k}


def resample(dist: np.ndarray, ele: np.ndarray, step_m: float = STEP_M) -> tuple[np.ndarray, np.ndarray]:
    """Elevation every `step_m` along the distance (linear interpolation)."""
    total = float(dist[-1])
    xs = np.arange(0.0, total, step_m)
    if len(xs) == 0 or xs[-1] < total:
        xs = np.append(xs, total)
    return xs, np.interp(xs, dist, ele)


def _median5(z: np.ndarray) -> np.ndarray:
    if len(z) < 5:
        return z.copy()
    p = np.pad(z, 2, mode="edge")
    w = np.lib.stride_tricks.sliding_window_view(p, 5)
    return np.median(w, axis=1)


def smooth(z: np.ndarray, step_m: float = STEP_M, sigma_m: float = DEFAULT_SIGMA_M) -> np.ndarray:
    """5-point running median (spikes), then a distance-domain Gaussian with
    σ = sigma_m (§6.5 step 4, 推估)."""
    m = _median5(np.asarray(z, float))
    s = sigma_m / step_m
    if s <= 0.3 or len(m) < 3:
        return m
    r = int(math.ceil(3 * s))
    x = np.arange(-r, r + 1)
    ker = np.exp(-0.5 * (x / s) ** 2)
    ker /= ker.sum()
    pad = min(r, len(m) - 1)
    p = np.pad(m, pad, mode="reflect")
    if pad < r:
        p = np.pad(p, r - pad, mode="edge")
    return np.convolve(p, ker, mode="valid")


def gain_loss(z: Sequence[float], hyst_m: float = HYST_M) -> tuple[float, float]:
    """Climb and descent with a hysteresis band: a trend only counts once it
    reverses by more than `hyst_m` (the last extreme is kept)."""
    z = list(map(float, z))
    if len(z) < 2:
        return 0.0, 0.0
    gain = loss = 0.0
    anchor = peak = z[0]
    trend = 0
    for v in z[1:]:
        if trend == 0:
            if v - anchor >= hyst_m:
                trend, peak = 1, v
            elif anchor - v >= hyst_m:
                trend, peak = -1, v
        elif trend == 1:
            if v > peak:
                peak = v
            elif peak - v >= hyst_m:
                gain += peak - anchor
                anchor, peak, trend = peak, v, -1
        else:
            if v < peak:
                peak = v
            elif v - peak >= hyst_m:
                loss += anchor - peak
                anchor, peak, trend = peak, v, 1
    if trend == 1:
        gain += peak - anchor
    elif trend == -1:
        loss += anchor - peak
    return gain, loss


def douglas_peucker(xs: Sequence[float], ys: Sequence[float], eps_m: float = DEFAULT_EPS_M) -> list[int]:
    """Douglas & Peucker 1973: indices of the points kept so that every
    original point lies within eps_m (vertically) of the polyline. 已驗證 by
    property (V-DP)."""
    x = np.asarray(xs, float)
    y = np.asarray(ys, float)
    n = len(x)
    if n <= 2:
        return list(range(n))
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        xa, xb, ya, yb = x[a], x[b], y[a], y[b]
        seg = slice(a + 1, b)
        if xb == xa:
            dev = np.abs(y[seg] - ya)
        else:
            dev = np.abs(y[seg] - (ya + (yb - ya) * (x[seg] - xa) / (xb - xa)))
        i = int(np.argmax(dev))
        if dev[i] > eps_m:
            m = a + 1 + i
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return [int(i) for i in np.nonzero(keep)[0]]


def classify(grade: float, flat_pct: float = FLAT_PCT) -> str:
    """陡下 ≤ −15 %, 下坡 −15…−flat, 平 |g| ≤ flat (default 2 %), 上坡, 陡上 ≥ +15 %."""
    g = grade * 100.0
    if g <= -STEEP_PCT:
        return "steep_down"
    if g < -flat_pct:
        return "down"
    if g <= flat_pct:
        return "flat"
    if g < STEEP_PCT:
        return "up"
    return "steep_up"


def walk_label(grade: float) -> Optional[str]:
    """≥ 28 % 建議快走, ≥ 15 % 走跑皆可 (labels only, not used for time)."""
    g = grade * 100.0
    if g >= WALK_PCT:
        return "建議快走"
    if g >= RUN_WALK_PCT:
        return "走跑皆可"
    return None


# ---- segmentation --------------------------------------------------------------

def _grade(xs, zs, a, b) -> float:
    dx = xs[b] - xs[a]
    return 0.0 if dx <= 0 else (zs[b] - zs[a]) / dx


def merge(bounds: list[int], xs: np.ndarray, zs: np.ndarray, min_len_m: float,
          flat_pct: float = FLAT_PCT) -> list[int]:
    """Merge adjacent same-class segments, then fold every segment shorter
    than min_len_m into the neighbour whose grade is closer; repeat until no
    short segment is left (§6.5 step 7). `bounds` = boundary indices."""
    b = list(bounds)

    def same_class_pass(b):
        out = [b[0]]
        for i in range(1, len(b) - 1):
            left = classify(_grade(xs, zs, out[-1], b[i]), flat_pct)
            right = classify(_grade(xs, zs, b[i], b[i + 1]), flat_pct)
            if left != right:
                out.append(b[i])
        out.append(b[-1])
        return out

    b = same_class_pass(b)
    while len(b) > 2:
        lens = [xs[b[i + 1]] - xs[b[i]] for i in range(len(b) - 1)]
        i = int(np.argmin(lens))
        if lens[i] >= min_len_m:
            break
        g = _grade(xs, zs, b[i], b[i + 1])
        if i == 0:
            drop = 1
        elif i == len(lens) - 1:
            drop = len(b) - 2
        else:
            gl = _grade(xs, zs, b[i - 1], b[i])
            gr = _grade(xs, zs, b[i + 1], b[i + 2])
            drop = i if abs(gl - g) <= abs(gr - g) else i + 1
        del b[drop]
        b = same_class_pass(b)
    return b


def split_long_flats(bounds: list[int], xs: np.ndarray, zs: np.ndarray, flat_pct: float = FLAT_PCT,
                     max_m: float = FLAT_SPLIT_M) -> list[int]:
    out = [bounds[0]]
    for a, c in zip(bounds, bounds[1:]):
        if classify(_grade(xs, zs, a, c), flat_pct) == "flat" and xs[c] - xs[a] > max_m:
            k = 1
            while xs[a] + 1000.0 * k < xs[c] - 200.0:
                out.append(int(np.searchsorted(xs, xs[a] + 1000.0 * k)))
                k += 1
        out.append(c)
    return sorted(set(out))


def km_bounds(xs: np.ndarray, every_m: float = 1000.0) -> list[int]:
    """Boundaries every `every_m` (per-km split); the last piece may be short."""
    out = [0]
    k = 1
    while k * every_m < xs[-1] - 1e-6:
        out.append(int(np.searchsorted(xs, k * every_m)))
        k += 1
    out.append(len(xs) - 1)
    return sorted(set(out))


def _max_grade(xs, zs, a, b, win_m: float = 100.0) -> float:
    if xs[b] - xs[a] <= win_m:
        return _grade(xs, zs, a, b)
    x = xs[a:b + 1]
    z = zs[a:b + 1]
    j = np.searchsorted(x, x + win_m)
    ok = j < len(x)
    if not ok.any():
        return _grade(xs, zs, a, b)
    i = np.nonzero(ok)[0]
    g = (z[j[ok]] - z[i]) / (x[j[ok]] - x[i])
    return float(g[np.argmax(np.abs(g))])


def segment_rows(bounds: list[int], xs: np.ndarray, zs: np.ndarray, flat_pct: float = FLAT_PCT,
                 climbs: Optional[list] = None) -> list[dict]:
    rows = []
    for n, (a, b) in enumerate(zip(bounds, bounds[1:]), 1):
        g = _grade(xs, zs, a, b)
        gn, ls = gain_loss(zs[a:b + 1], HYST_M)
        seg_x, seg_z = xs[a:b + 1], zs[a:b + 1]
        zmean = float(np.trapezoid(seg_z, seg_x) / (seg_x[-1] - seg_x[0])) if seg_x[-1] > seg_x[0] else float(seg_z[0])
        mid = 0.5 * (xs[a] + xs[b])
        cno = None
        for c in climbs or []:
            if c["start_m"] <= mid <= c["end_m"]:
                cno = c["no"]
                break
        cls = classify(g, flat_pct)
        rows.append({
            "i": n, "start_m": float(xs[a]), "end_m": float(xs[b]),
            "start_km": float(xs[a]) / 1000.0, "end_km": float(xs[b]) / 1000.0,
            "dist_m": float(xs[b] - xs[a]), "gain_m": gn, "loss_m": ls,
            "grade": g, "max_grade": _max_grade(xs, zs, a, b),
            "z_start": float(zs[a]), "z_end": float(zs[b]), "z_mean": zmean, "z_max": float(seg_z.max()),
            "cls": cls, "cls_label": CLASSES[cls], "walk": walk_label(g), "climb_no": cno,
        })
    return rows


def _climbs(xs, zs) -> list[dict]:
    from backend.engine.algorithms.climbs import detect_climbs
    try:
        cs = detect_climbs(list(xs), list(xs / 1000.0), list(zs))
    except Exception:                        # noqa: BLE001
        return []
    return [{"no": i + 1, "start_m": float(xs[c.start_index]), "end_m": float(xs[c.end_index]),
             "gain_m": c.gain_m, "grade": c.grade} for i, c in enumerate(cs)]


def _scale_to_gain(zs: np.ndarray, target: float) -> tuple[np.ndarray, float]:
    """Scale elevation deviations about the mean so the hysteresis gain equals
    `target` (the official climb; Stryd notes GPS noise inflates gain)."""
    zbar = float(zs.mean())
    base = zs - zbar

    def gain_at(c):
        return gain_loss(zbar + c * base)[0]
    g1 = gain_at(1.0)
    if g1 <= 0 or target <= 0:
        return zs, 1.0
    c = target / g1
    for _ in range(30):
        gc = gain_at(c)
        if abs(gc - target) < 0.01:
            break
        c *= target / gc if gc > 0 else 1.0
    return zbar + c * base, c


def build_course(track, *, sigma_m: float = DEFAULT_SIGMA_M, eps_m: float = DEFAULT_EPS_M,
                 min_len_m: Optional[float] = None, flat_pct: float = FLAT_PCT, split: str = "grade",
                 official_gain_m: Optional[float] = None) -> dict:
    """Track → course: totals, segments, a thinned profile, climbs, waypoints
    (with their km) and data-quality warnings (§6.5). split: "grade" (DP +
    merge), "km" (every 1000 m) or "none" (one segment)."""
    td = track_distance(track)
    if len(td["d"]) < 2 or td["d"][-1] < 50:
        raise ValueError("軌跡太短")
    xs, raw = resample(td["d"], td["z"], STEP_M)
    zs = smooth(raw, STEP_M, sigma_m)
    raw_gain = gain_loss(raw, HYST_M)[0]
    warnings = []
    scale = 1.0
    if official_gain_m:
        g0 = gain_loss(zs, HYST_M)[0]
        if g0 > 0 and abs(g0 - official_gain_m) / official_gain_m > 0.15:
            warnings.append(f"官方爬升 {official_gain_m:.0f} m 與 GPX 平滑後 {g0:.0f} m 相差超過 15 %")
        zs, scale = _scale_to_gain(zs, official_gain_m)
    gain, loss = gain_loss(zs, HYST_M)
    total = float(xs[-1])
    if min_len_m is None:
        min_len_m = max(200.0, 0.01 * total)
    if split == "none":
        bounds = [0, len(xs) - 1]
    elif split == "km":
        bounds = km_bounds(xs)
    else:
        bounds = douglas_peucker(xs, zs, eps_m)
        bounds = merge(bounds, xs, zs, min_len_m, flat_pct)
        bounds = split_long_flats(bounds, xs, zs, flat_pct)
    climbs = _climbs(xs, zs)
    segs = segment_rows(bounds, xs, zs, flat_pct, climbs)
    if raw_gain > 0 and abs(raw_gain - gain) / raw_gain > 0.25 and not official_gain_m:
        warnings.append(f"平滑前後爬升相差 {abs(raw_gain - gain) / raw_gain:.0%}（原始 {raw_gain:.0f} m → {gain:.0f} m），海拔雜訊大")
    spacing = float(np.median(np.diff(td["d"]))) if len(td["d"]) > 1 else 0.0
    if spacing > 50:
        warnings.append(f"點距中位數 {spacing:.0f} m：像是路線規劃工具畫的線，坡度會被平均掉")
    step = max(1, int(math.ceil(len(xs) / PROFILE_MAX)))
    idx = list(range(0, len(xs), step))
    if idx[-1] != len(xs) - 1:
        idx.append(len(xs) - 1)
    lat = np.asarray(track.lat, float)[td["keep"]]
    lon = np.asarray(track.lon, float)[td["keep"]]
    wpts = []
    for w in getattr(track, "wpts", []) or []:
        dd = haversine(lat, lon, w["lat"], w["lon"])
        j = int(np.argmin(dd))
        if dd[j] > 500:
            continue
        name = w.get("name") or ""
        wpts.append({"name": name, "km": float(td["d"][j]) / 1000.0,
                     "camp": any(k in name.lower() for k in CAMP_WORDS)})
    return {
        "totals": {"km": total / 1000.0, "gain_m": gain, "loss_m": loss, "raw_gain_m": raw_gain,
                   "z_min": float(zs.min()), "z_max": float(zs.max()), "z_start": float(zs[0]),
                   "points": len(track.lat), "spacing_m": spacing, "segments": len(segs),
                   "gain_scale": scale},
        "segments": segs,
        "profile": {"km": [float(xs[i]) / 1000.0 for i in idx], "z": [round(float(zs[i]), 1) for i in idx]},
        "climbs": climbs,
        "wpts": wpts,
        "warnings": warnings,
        "opts": {"sigma_m": sigma_m, "eps_m": eps_m, "min_len_m": min_len_m, "flat_pct": flat_pct,
                 "split": split, "official_gain_m": official_gain_m},
        "source": "gpx",
    }


def manual_course(km: float, gain_m: float = 0.0, loss_m: Optional[float] = None,
                  split: str = "none", altitude_m: Optional[float] = None) -> dict:
    """A course without a profile (§4 step 2): one segment, or one per km with
    the climb spread evenly (no grade information — the net grade only)."""
    loss = gain_m if loss_m is None else loss_m
    total = km * 1000.0
    if split == "km" and total > 1000.0:
        edges = [i * 1000.0 for i in range(int(total // 1000.0) + 1)]
        if total - edges[-1] > 1.0:
            edges.append(total)
    else:
        edges = [0.0, total]
    g = (gain_m - loss) / total if total > 0 else 0.0
    segs = []
    for n, (a, b) in enumerate(zip(edges, edges[1:]), 1):
        f = (b - a) / total
        cls = classify(g)
        segs.append({"i": n, "start_m": a, "end_m": b, "start_km": a / 1000.0, "end_km": b / 1000.0,
                     "dist_m": b - a, "gain_m": gain_m * f, "loss_m": loss * f, "grade": g, "max_grade": g,
                     "z_start": altitude_m, "z_end": altitude_m, "z_mean": altitude_m, "z_max": altitude_m,
                     "cls": cls, "cls_label": CLASSES[cls], "walk": None, "climb_no": None})
    return {"totals": {"km": km, "gain_m": gain_m, "loss_m": loss, "segments": len(segs)},
            "segments": segs, "profile": None, "climbs": [], "wpts": [], "warnings": [],
            "opts": {"split": split}, "source": "manual"}


def cut_at(segments: list[dict], cuts_km: Sequence[float]) -> list[dict]:
    """Split segments at the given km positions (multi-day boundaries clicked
    on the profile); each piece keeps its parent's grade and class and gets
    `day` = 1 + the number of cuts before it."""
    cuts = sorted(c * 1000.0 for c in cuts_km or [])
    out = []
    for s in segments:
        pieces = [s["start_m"]] + [c for c in cuts if s["start_m"] + 1 < c < s["end_m"] - 1] + [s["end_m"]]
        for a, b in zip(pieces, pieces[1:]):
            f = (b - a) / s["dist_m"] if s["dist_m"] else 1.0
            p = {**s, "start_m": a, "end_m": b, "start_km": a / 1000.0, "end_km": b / 1000.0,
                 "dist_m": b - a, "gain_m": s["gain_m"] * f, "loss_m": s["loss_m"] * f}
            p["day"] = 1 + sum(1 for c in cuts if c <= a + 1)
            out.append(p)
    for n, p in enumerate(out, 1):
        p["i"] = n
    return out
