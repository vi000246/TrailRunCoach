"""Synthetic courses for the demo athlete: an elevation profile and a GPS
track on a 5 m distance grid. Every track is a generated curve in a fixed
fictional area (athlete.AREAS) — never a real activity's or a real trail's
route; elevations are invented too.

Kinds:
  loop        closed curve (road runs, the half marathon): wraps seamlessly
  out_back    out along a random path and back the same way (trail runs,
              hill repeats, the long climb): start = end, so it wraps too
  point       point to point (a 百岳 day: trailhead -> hut -> ...)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

STEP = 5.0                       # m between grid points
M_PER_DEG_LAT = 111_320.0


@dataclass
class Course:
    kind: str                    # loop | out_back | point
    ele: np.ndarray              # m, per grid point
    lat: np.ndarray
    lon: np.ndarray
    trail: bool = False

    @property
    def length(self) -> float:
        return (len(self.ele) - 1) * STEP

    @property
    def wraps(self) -> bool:
        return self.kind in ("loop", "out_back")

    def grade(self) -> np.ndarray:
        """Rise / run between grid points (len n, last repeated), clipped ±0.45."""
        g = np.diff(self.ele) / STEP
        g = np.append(g, g[-1] if len(g) else 0.0)
        return np.clip(g, -0.45, 0.45)

    def gain_loss(self) -> tuple[float, float]:
        d = np.diff(self.ele)
        return float(d[d > 0].sum()), float(-d[d < 0].sum())


# ---------------------------------------------------------------------------
# elevation
# ---------------------------------------------------------------------------

def wave_profile(rng, length_m: float, gain_m: float, loss_m: float, start_ele: float,
                 up=(0.03, 0.08), down=(0.03, 0.08), n_up: int = 4, n_down: int = 4,
                 first_up: bool = True, rough: float = 1.5) -> np.ndarray:
    """Elevation on the 5 m grid: `gain_m` of climbing in n_up chunks and
    `loss_m` of descent in n_down chunks, each at a random grade from its
    range, the rest flat; total horizontal length = length_m."""
    n = max(2, int(round(length_m / STEP)) + 1)

    def chunks(total, k, grades):
        if total <= 0 or k <= 0:
            return []
        w = rng.dirichlet(np.full(k, 2.0)) * total
        return [(float(h), float(rng.uniform(*grades))) for h in w]

    ups = [(h, g) for h, g in chunks(gain_m, n_up, up)]
    downs = [(-h, g) for h, g in chunks(loss_m, n_down, down)]
    horiz = sum(abs(h) / g for h, g in ups + downs)
    scale = 1.0
    if horiz > length_m * 0.97:               # steeper than asked to fit the length
        scale = horiz / (length_m * 0.97)
    segs = [(h, g * scale) for h, g in ups + downs]
    flat_total = max(0.0, length_m - sum(abs(h) / g for h, g in segs))
    order = list(rng.permutation(len(segs)))
    if first_up and ups:                       # climbs first (a trailhead below)
        order.sort(key=lambda i: (segs[i][0] < 0, rng.random()))
        # interleave: mostly up first, a few downs in between
        ups_i = [i for i in order if segs[i][0] > 0]
        dn_i = [i for i in order if segs[i][0] < 0]
        order = []
        while ups_i or dn_i:
            if ups_i:
                order.append(ups_i.pop(0))
            if dn_i and (not ups_i or rng.random() < 0.35):
                order.append(dn_i.pop(0))
    nf = len(order) + 1
    flats = rng.dirichlet(np.full(nf, 1.5)) * flat_total if flat_total > 0 else np.zeros(nf)
    pieces_x, pieces_dz = [], []
    for k, i in enumerate(order):
        pieces_x.append(flats[k]); pieces_dz.append(0.0)
        h, g = segs[i]
        pieces_x.append(abs(h) / g); pieces_dz.append(h)
    pieces_x.append(flats[-1]); pieces_dz.append(0.0)
    xs = np.concatenate([[0.0], np.cumsum(pieces_x)])
    zs = np.concatenate([[start_ele], start_ele + np.cumsum(pieces_dz)])
    xs = xs * (length_m / xs[-1]) if xs[-1] > 0 else xs
    grid = np.linspace(0.0, length_m, n)
    ele = np.interp(grid, xs, zs)
    # rocks / steps: a little short-scale texture (keeps the totals close)
    if rough > 0:
        tex = np.convolve(rng.normal(0, rough, n), np.ones(5) / 5, mode="same")
        tex[0] = tex[-1] = 0.0
        ele = ele + tex
    # smooth the corners (5-point)
    k = np.ones(7) / 7
    sm = np.convolve(np.pad(ele, 3, mode="edge"), k, mode="valid")
    sm[0], sm[-1] = ele[0], ele[-1]
    return sm


# ---------------------------------------------------------------------------
# tracks
# ---------------------------------------------------------------------------

def _to_latlon(x: np.ndarray, y: np.ndarray, center) -> tuple[np.ndarray, np.ndarray]:
    lat0, lon0 = center[0], center[1]
    lat = lat0 + y / M_PER_DEG_LAT
    lon = lon0 + x / (M_PER_DEG_LAT * math.cos(math.radians(lat0)))
    return lat, lon


def _wander(rng, n: int, turn: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """A smooth random path of n points STEP apart (heading random walk)."""
    h0 = rng.uniform(0, 2 * math.pi)
    dh = np.convolve(rng.normal(0, turn, n), np.ones(25) / 25, mode="same")
    head = h0 + np.cumsum(dh)
    x = np.concatenate([[0.0], np.cumsum(np.cos(head[:-1]) * STEP)])
    y = np.concatenate([[0.0], np.cumsum(np.sin(head[:-1]) * STEP)])
    return x, y


def _loop_xy(rng, length_m: float, n: int) -> tuple[np.ndarray, np.ndarray]:
    """A closed blob r(φ) = R(1 + a sin(2φ+p) + b sin(3φ+q)), resampled by arc length."""
    a, b = rng.uniform(0.1, 0.3), rng.uniform(0.05, 0.15)
    p, q = rng.uniform(0, 2 * math.pi, 2)
    phi = np.linspace(0, 2 * math.pi, 4000)
    r = 1 + a * np.sin(2 * phi + p) + b * np.sin(3 * phi + q)
    x, y = r * np.cos(phi), r * np.sin(phi)
    seg = np.hypot(np.diff(x), np.diff(y))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    R = length_m / s[-1]
    t = np.linspace(0, s[-1], n)
    return (np.interp(t, s, x) - x[0]) * R, (np.interp(t, s, y) - y[0]) * R


def make(rng, kind: str, area, length_m: float, gain_m: float, loss_m: float = None,
         start_ele: float = None, up=(0.03, 0.08), down=(0.03, 0.08), n_seg: int = 4,
         trail: bool = False, offset_m: float = 0.0, rough: float = 1.5) -> Course:
    """A course of `kind` in `area` (athlete.AREAS value: lat, lon, base ele).
    out_back: `gain_m` is the climb to the turnaround; the way back mirrors it."""
    center = (area[0], area[1])
    base = area[2] if start_ele is None else start_ele
    if kind == "out_back":
        half = length_m / 2.0
        ele_h = wave_profile(rng, half, gain_m, loss_m or 0.0, base, up, down, n_seg,
                             max(1, n_seg // 3) if loss_m else 0, rough=rough)
        ele = np.concatenate([ele_h, ele_h[-2::-1]])
        x, y = _wander(rng, len(ele_h), 0.06 if trail else 0.03)
        x = np.concatenate([x, x[-2::-1]])
        y = np.concatenate([y, y[-2::-1]])
    elif kind == "loop":
        g = gain_m
        ele = wave_profile(rng, length_m, g, g if loss_m is None else loss_m, base, up, down,
                           n_seg, n_seg, first_up=False, rough=rough)
        ele[-1] = ele[0]
        x, y = _loop_xy(rng, length_m, len(ele))
    else:
        ele = wave_profile(rng, length_m, gain_m, loss_m or 0.0, base, up, down, n_seg,
                           max(1, n_seg // 2), rough=rough)
        x, y = _wander(rng, len(ele), 0.07)
    # move the start a little inside the area so the same kind of run does not
    # always start on the same spot (fictional anyway)
    if offset_m:
        ang = rng.uniform(0, 2 * math.pi)
        x = x + offset_m * math.cos(ang)
        y = y + offset_m * math.sin(ang)
    lat, lon = _to_latlon(x, y, center)
    return Course(kind=kind, ele=ele, lat=lat, lon=lon, trail=trail)


def gpx(course: Course, name: str, every: int = 4) -> str:
    """A GPX 1.1 track of the course (every `every`-th grid point)."""
    pts = []
    for i in range(0, len(course.ele), every):
        pts.append(f'<trkpt lat="{course.lat[i]:.6f}" lon="{course.lon[i]:.6f}"><ele>{course.ele[i]:.1f}</ele></trkpt>')
    if (len(course.ele) - 1) % every:
        i = len(course.ele) - 1
        pts.append(f'<trkpt lat="{course.lat[i]:.6f}" lon="{course.lon[i]:.6f}"><ele>{course.ele[i]:.1f}</ele></trkpt>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="TrailRunCoach demo (synthetic course)" '
            'xmlns="http://www.topografix.com/GPX/1/1">\n'
            f"<metadata><name>{name}</name><desc>虛構的示範賽道，非真實路線</desc></metadata>\n"
            f"<trk><name>{name}</name><trkseg>\n" + "\n".join(pts) + "\n</trkseg></trk>\n</gpx>\n")


def chain(parts: list[Course]) -> list[Course]:
    """Point-to-point legs moved so each starts where the previous one ended
    (position and elevation): the days of one multi-day trip."""
    out = [parts[0]]
    for p in parts[1:]:
        prev = out[-1]
        out.append(Course(kind=p.kind, ele=p.ele - p.ele[0] + prev.ele[-1],
                          lat=p.lat - p.lat[0] + prev.lat[-1], lon=p.lon - p.lon[0] + prev.lon[-1],
                          trail=p.trail))
    return out


def concat(parts: list[Course]) -> Course:
    """One course of chained legs (a multi-day course for the GPX)."""
    ele = np.concatenate([p.ele if i == 0 else p.ele[1:] for i, p in enumerate(parts)])
    lat = np.concatenate([p.lat if i == 0 else p.lat[1:] for i, p in enumerate(parts)])
    lon = np.concatenate([p.lon if i == 0 else p.lon[1:] for i, p in enumerate(parts)])
    return Course(kind="point", ele=ele, lat=lat, lon=lon, trail=True)
