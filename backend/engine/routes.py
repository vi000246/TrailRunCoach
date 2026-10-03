"""
Repeated segments and routes, found automatically in the athlete's GPS
history — progress on the same climb / the same loop, effort by effort.

Two tiers, both on disk under ~/.wko5coach/routes/:

  tracks/<file>.json   one compact track per activity: raw samples picked at
                       >= 25 m spacing, with cumulative sums of moving time,
                       HR·dt, power·dt, temperature·dt and hrTSS carried at each
                       kept sample, plus the activity's climbs and descents.
                       Stamped [size, mtime, ALGO_VERSION]; only new or changed
                       files are parsed again.
  index.json           the segments and routes, each with its efforts and
                       their metrics. Rebuilt incrementally from the tracks.
  names.json           the athlete's renames, keyed by segment / route id.

Because every kept point IS a raw sample and the cumulative sums are taken
over raw samples, an effort's metrics between two kept points are an exact
slice of the raw data — the same numbers climbs.py's `_measure` gives for the
same sample range (see docs/spec/route-progress.spec.md, Metrics).

Detection (docs/spec/route-progress.spec.md, Detection):
  candidates  = every climb and every descent (climbs.detect_climbs; descents
                on the negated elevation) + every same-direction stretch >= 500 m
                two activities share (route_match.common_runs)
  segments    = candidates clustered oldest first: a candidate that overlaps
                an existing segment of the same kind and direction (mutual
                overlap >= 0.8 at 30 m) is not a new segment
  efforts     = every traversal of a segment in every activity of the same
                sport family (route_match.find_efforts)
  routes      = activities whose whole tracks overlap each other >= 0.8, start
                and end within 200 m, same direction
"""
from __future__ import annotations

import datetime as dt
import hashlib
import heapq
import json
import math
import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np

from backend.engine.algorithms import route_match as RM
from backend.engine.algorithms.climbs import detect_climbs
from backend.engine.algorithms.routes import cells as coarse_cells
from backend.engine.algorithms.wko5_hr import HR_LEVELS
from backend.engine.algorithms.wko5_time import MOVING_SPEED_KMH

ALGO_VERSION = 3          # tier A (tracks): 2: elevation gaps filled from the previous sample
                          # 3: per-interval peaks (max HR, max 30 s power)
INDEX_VERSION = 4         # tier B (index.json): 3 = ALGO_VERSION 3's index;
                          # 4: routes clustered by shared length share (start / direction free),
                          #    canonical common part, partials / sub-routes, stretches merged
# WKO5COACH_ROUTES_DIR: another store root (a second server on the same
# machine must not share the live index — two versions would rebuild it in turn)
HOME = None      # fixed folder (tests); None = $WKO5COACH_ROUTES_DIR, else <tenant shared>/routes


def home() -> Path:
    if HOME is not None:
        return Path(HOME)
    v = os.getenv("WKO5COACH_ROUTES_DIR")
    if v:
        return Path(v)
    from backend import tenancy
    return tenancy.shared_path("routes")
P30_S = 30.0                     # max-power window
P30_MIN_COVER = 0.8              # share of the window with power samples
PEAKS_PATH = Path(__file__).resolve().parents[1] / "data" / "baiyue.json"

MIN_STRETCH_M = 500.0
ROUTE_END_TOL_M = 200.0
ROUTE_MIN_OVERLAP = 0.8
PEAK_NAME_RADIUS_M = 1000.0
MAX_MOVING_GAP_S = 60.0          # same rule as achievements.moving_mask
MIN_PAIR_CELLS = 5               # shared 100 m cells before two tracks are compared
STRETCH_NMS_OVERLAP = 0.5        # a stretch this much on a kept segment is not listed
FAMILIES = {"run": "foot", "walk": "foot", "other": "foot", "road bike": "bike",
            "mountain bike": "bike", "bike": "bike", "swim": None, "strength": None}
KIND_ZH = {"climb": "爬坡", "descent": "下坡", "stretch": "路段", "route": "路線"}


def family_of(sport: str, sport_type: str = "") -> Optional[str]:
    if "swim" in sport_type or "treadmill" in sport_type or "indoor" in sport_type:
        return None
    if "cycl" in sport_type or "bike" in sport:
        return "bike"
    return FAMILIES.get(sport, "foot")


def moving_threshold(sport: str) -> float:
    return MOVING_SPEED_KMH.get("walk" if sport == "walk" else ("bike" if "bike" in sport else sport), 0.0)


# ---------------------------------------------------------------------------
# tier A: the compact track
# ---------------------------------------------------------------------------

def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fill(a: list) -> list:
    first = next((v for v in a if v is not None), None)
    if first is None:
        return a
    out, last = [], first
    for v in a:
        if v is not None:
            last = v
        out.append(last)
    return out


def _hr_rate(h: float, lthr: float) -> float:
    for frac, per_hour in HR_LEVELS:
        if h >= frac * lthr:
            return per_hour / 3600.0
    return 0.0


def trailing_power(t: list, power: list, window_s: float = P30_S) -> list:
    """Trailing mean power ending at each raw sample e: over the samples
    s(e)+1 .. e, where s(e) is the latest sample with t <= t_e − window_s
    (the shortest sample-aligned span of >= window_s). Mean = Σp·dt ÷ Σdt over
    the samples with power (dt = t − previous valid t); None when there is no
    such s(e) or power covers < 80 % of the span."""
    n = len(t)
    cp = [0.0] * n                # Σ p·dt over samples 0..i with power
    cs = [0.0] * n                # Σ dt of those
    tv = [None] * n               # t of each sample, carried over gaps for the pointer
    acc_p = acc_s = 0.0
    prev = None
    for i in range(n):
        ti = t[i]
        if ti is not None:
            if prev is not None and ti > prev and power[i] is not None:
                dt_ = ti - prev
                acc_p += power[i] * dt_
                acc_s += dt_
            prev = ti
        cp[i], cs[i], tv[i] = acc_p, acc_s, ti
    out: list = [None] * n
    s = -1                        # latest sample with t <= t_e − window
    valid = [i for i in range(n) if tv[i] is not None]
    k = -1                        # position in `valid` of s
    for e in valid:
        lim = tv[e] - window_s
        while k + 1 < len(valid) and tv[valid[k + 1]] <= lim:
            k += 1
        if k < 0:
            continue
        s = valid[k]
        span = tv[e] - tv[s]
        dp, ds = cp[e] - cp[s], cs[e] - cs[s]
        if ds > 0 and ds >= P30_MIN_COVER * span:
            out[e] = dp / ds
    return out


def interval_peaks(t: list, hr: list, p30: Optional[list], idx: list[int],
                   window_s: float = P30_S) -> dict:
    """Per kept point k, peaks over raw samples idx[k-1]+1 .. idx[k] (k = 0:
    0 .. idx[0]) — the same slice the cumulative sums use, so an effort
    i0..i1 takes the max over kept points i0+1 .. i1, exactly the max over
    its raw samples idx[i0]+1 .. idx[i1].

      mhr   max HR over the interval
      mp30  max trailing 30 s power (`trailing_power`) ending in the interval
      p30k, p30h  for an effort STARTING at kept point k, whose windows must
            begin at or after raw sample a = idx[k]: the first valid end is
            e* = the first sample with t >= t_a + 30 s. p30k = the kept point
            whose interval holds e*, p30h = the max from e* to that interval's
            end. An effort's max 30 s power = max(p30h[i0], mp30[p30k[i0]+1 .. i1]).
    """
    n_k = len(idx)
    mhr: list = [None] * n_k
    mp30: list = [None] * n_k
    lo = 0
    for k, r in enumerate(idx):
        best_h = best_p = None
        for i in range(lo, r + 1):
            h = hr[i]
            if h is not None and (best_h is None or h > best_h):
                best_h = h
            if p30 is not None:
                p = p30[i]
                if p is not None and (best_p is None or p > best_p):
                    best_p = p
        mhr[k], mp30[k] = best_h, best_p
        lo = r + 1
    p30k: list = [None] * n_k
    p30h: list = [None] * n_k
    if p30 is not None:
        n = len(t)
        e = 0
        kk = 0
        for k, a in enumerate(idx):
            ta = t[a]
            if ta is None:
                continue
            e = max(e, a + 1)
            while e < n and (t[e] is None or t[e] < ta + window_s):
                e += 1
            if e >= n:
                break
            while kk < n_k and idx[kk] < e:
                kk += 1
            if kk >= n_k:
                break
            best = None
            for i in range(e, idx[kk] + 1):
                p = p30[i]
                if p is not None and (best is None or p > best):
                    best = p
            p30k[k], p30h[k] = kk, best
    return {"mhr": mhr, "mp30": mp30, "p30k": p30k, "p30h": p30h}


def extract_track(meta: dict, t, lat, lon, dist_km=None, elev=None, hr=None, power=None,
                  speed=None, temp=None, lthr: Optional[float] = None,
                  step_m: float = RM.STEP_M) -> Optional[dict]:
    """The compact track of one activity from its raw, sample-aligned channels
    (None = no data). meta: file, start (iso), sport, sport_type.

    Cumulative sums at kept sample k cover raw samples 1..k with
    dt_k = t_k - t_(previous valid):
      cm    moving seconds (speed > the sport's threshold and dt <= 60 s —
            achievements.moving_mask)
      chr   Σ hr·dt over moving samples with HR;   chrs  Σ dt of those
      cpw   Σ power·dt over moving samples with power; cpws
      ctm   Σ temperature·dt over samples with temperature; ctms
      ctss  Σ hrTSS rate·dt over samples with HR (wko5_hr.hr_tss, WKO5 form)
    and per-interval peaks (`interval_peaks`): max HR, max 30 s power.
    """
    n = len(t)
    t = [_num(v) for v in t]
    lat = [_num(v) for v in lat]
    lon = [_num(v) for v in lon]
    if sum(1 for a, b in zip(lat, lon) if RM.valid_fix(a, b)) < 10:
        return None
    col = lambda a: [None] * n if a is None else [_num(v) for v in a] + [None] * max(0, n - len(a))
    dist_km, elev, hr, power, speed, temp = (col(x) for x in (dist_km, elev, hr, power, speed, temp))
    # an elevation gap takes the last valid value before it (the first valid
    # one at the very start), so a segment endpoint on a gap still has a gain
    elev = _fill(elev)

    # distance: the device's, else cumulative GPS
    if all(v is None for v in dist_km):
        acc, prev, dist_km = 0.0, None, []
        for a, b in zip(lat, lon):
            if RM.valid_fix(a, b):
                if prev is not None:
                    acc += RM.haversine_m(prev[0], prev[1], a, b)
                prev = (a, b)
            dist_km.append(acc / 1000.0)
    # speed (km/h) from distance when the device has none
    if all(v is None for v in speed):
        speed, pt, pd = [], None, None
        for ti, di in zip(t, dist_km):
            s = None
            if ti is not None and di is not None and pt is not None and ti > pt:
                s = (di - pd) / (ti - pt) * 3600.0
            if ti is not None and di is not None:
                pt, pd = ti, di
            speed.append(s)

    thr = moving_threshold(meta.get("sport", ""))
    cm = np.zeros(n); chr_ = np.zeros(n); chrs = np.zeros(n); cpw = np.zeros(n); cpws = np.zeros(n)
    ctm = np.zeros(n); ctms = np.zeros(n); ctss = np.zeros(n)
    moving = [False] * n
    acc = [0.0] * 8
    prev_t = 0.0
    first = True
    for i in range(n):
        ti = t[i]
        if ti is not None:
            dt_ = ti - prev_t
            gap = dt_ if not first else ti
            prev_t, first = ti, False
            s = speed[i]
            mv = s is not None and s > thr and gap <= MAX_MOVING_GAP_S
            moving[i] = mv
            if dt_ > 0:
                h, p, tc = hr[i], power[i], temp[i]
                if mv:
                    acc[0] += dt_
                    if h is not None:
                        acc[1] += h * dt_; acc[2] += dt_
                    if p is not None:
                        acc[3] += p * dt_; acc[4] += dt_
                if tc is not None:
                    acc[5] += tc * dt_; acc[6] += dt_
                if h is not None and lthr:
                    acc[7] += _hr_rate(h, lthr) * dt_
        cm[i], chr_[i], chrs[i], cpw[i], cpws[i], ctm[i], ctms[i], ctss[i] = acc

    # climbs and descents on the smoothed elevation, moving time only
    climbs, descents = [], []
    if any(v is not None for v in elev):
        climbs = detect_climbs(t, dist_km, elev, hr, moving=moving)
        neg = [None if v is None else -v for v in elev]
        descents = detect_climbs(t, dist_km, neg, hr, moving=moving)
    keep = set()
    for c in climbs + descents:
        keep.update((c.start_index, c.end_index))
    idx = RM.resample_indices(lat, lon, step_m, keep)
    if len(idx) < 3:
        return None
    pos = {r: k for k, r in enumerate(idx)}

    def ranges(cs):
        out = []
        for c in cs:
            a, b = pos.get(c.start_index), pos.get(c.end_index)
            if a is not None and b is not None and b > a:
                out.append([a, b])
        return out

    def pick(a, nd):
        return [None if a[i] is None else round(a[i], nd) for i in idx]

    def pickf(a, nd):
        return [round(float(a[i]), nd) for i in idx]

    has_pw = any(v is not None for v in power)
    pk = interval_peaks(t, hr, trailing_power(t, power) if has_pw else None, idx)
    rnd = lambda a, nd: [None if v is None else round(v, nd) for v in a]

    return {
        "v": ALGO_VERSION, **meta,
        "raw_n": n, "idx": idx,
        "lat": [round(lat[i], 6) for i in idx], "lon": [round(lon[i], 6) for i in idx],
        "t": pick(t, 1), "d": pick(dist_km, 4), "e": pick(elev, 1),
        "cm": pickf(cm, 1), "chr": pickf(chr_, 1), "chrs": pickf(chrs, 1),
        "cpw": pickf(cpw, 1), "cpws": pickf(cpws, 1), "ctm": pickf(ctm, 1), "ctms": pickf(ctms, 1),
        "ctss": pickf(ctss, 4),
        # power peaks at 6 decimals: rounding them to 1 and the effort's value
        # to 0 again turned e.g. 246.54 W into 246.5, then 246 (half to even) — a double rounding
        "mhr": rnd(pk["mhr"], 1), "mp30": rnd(pk["mp30"], 6), "p30k": pk["p30k"], "p30h": rnd(pk["p30h"], 6),
        "moving_total": round(float(cm[-1]), 1), "hrtss_total": round(float(ctss[-1]), 4) if lthr else None,
        "elapsed_total": next((v for v in reversed(t) if v is not None), None),
        "lthr": lthr, "has_hr": bool(chrs[-1] > 0), "has_power": bool(cpws[-1] > 0),
        "has_temp": bool(ctms[-1] > 0),
        "climbs": ranges(climbs), "descents": ranges(descents),
    }


class Track:
    """A compact track in memory: numpy columns + helpers."""

    def __init__(self, d: dict):
        self.raw = d
        self.file: str = d["file"]
        self.start: str = d["start"]
        self.sport: str = d.get("sport", "")
        self.sport_type: str = d.get("sport_type", "")
        self.family = family_of(self.sport, self.sport_type)
        self.lat = np.asarray(d["lat"], dtype=float)
        self.lon = np.asarray(d["lon"], dtype=float)
        f = lambda k: np.asarray([np.nan if v is None else v for v in d[k]], dtype=float)
        self.t, self.d, self.e = f("t"), f("d"), f("e")
        self.cols = {k: np.asarray(d[k], dtype=float) for k in
                     ("cm", "chr", "chrs", "cpw", "cpws", "ctm", "ctms", "ctss")}
        n = len(self.lat)
        self.peaks = {k: f(k) if k in d else np.full(n, np.nan) for k in ("mhr", "mp30", "p30h")}
        self.p30k = [None if v is None else int(v) for v in d.get("p30k", [None] * n)]
        self.cells = coarse_cells(d["lat"], d["lon"])

    def __len__(self):
        return len(self.lat)

    def xy(self, lat0: float, lon0: float) -> np.ndarray:
        return RM.project(self.lat, self.lon, lat0, lon0)


# ---------------------------------------------------------------------------
# effort metrics
# ---------------------------------------------------------------------------

def effort_metrics(tr: Track, i0: int, i1: int, direction: str) -> dict:
    """Metrics of the stretch between kept points i0 and i1 (raw samples
    idx[i0] .. idx[i1]); definitions in the spec's Metrics table."""
    c = tr.cols
    diff = lambda k: float(c[k][i1] - c[k][i0])
    elapsed = _f(tr.t[i1] - tr.t[i0])
    moving = diff("cm")
    dist = _f(tr.d[i1] - tr.d[i0])
    gain = _f(tr.e[i1] - tr.e[i0])
    hr_s, pw_s, tm_s = diff("chrs"), diff("cpws"), diff("ctms")
    avg_hr = diff("chr") / hr_s if hr_s > 0 else None
    avg_pw = diff("cpw") / pw_s if pw_s > 0 else None
    temp = diff("ctm") / tm_s if tm_s > 0 else None
    vam = gain / moving * 3600.0 if (gain is not None and moving > 0) else None
    up = direction == "up"
    rate = None if vam is None else (vam if up else -vam)
    ok_rate = rate is not None and rate > 0
    hrtss = diff("ctss") if tr.raw.get("lthr") else None
    total = tr.raw.get("hrtss_total")
    return {
        "elapsed_s": _r(elapsed, 0), "moving_s": _r(moving, 0), "dist_km": _r(dist, 3),
        "gain_m": _r(gain, 1),
        "pace_min_km": _r(moving / 60.0 / dist, 2) if (dist and dist > 0 and moving > 0) else None,
        "vam": _r(vam, 0) if up else None,
        "descent_rate": _r(-vam, 0) if (vam is not None and direction == "down") else None,
        "avg_hr": _r(avg_hr, 1), "avg_power": _r(avg_pw, 1),
        "hr_per_100m": _r(diff("chr") / 60.0 / gain * 100.0, 0)
        if (up and avg_hr is not None and gain and gain > 0) else None,
        "hr_vam": _r(avg_hr / rate * 1000.0, 1) if (up and avg_hr and ok_rate) else None,
        "power_vam": _r(avg_pw / rate * 1000.0, 1) if (up and avg_pw and ok_rate) else None,
        # below 0.5 the moving mask (speed > 1 mph) dropped much of a slow climb:
        # moving-time VAM / pace overstate the effort — the page flags it
        "moving_share": _r(moving / elapsed, 3) if elapsed else None,
        "hrtss": _r(hrtss, 2),
        "hrtss_share": _r(hrtss / total, 4) if (hrtss is not None and total) else None,
        "temp_c": _r(temp, 1),
        **effort_peaks(tr, i0, i1),
    }


def _nanmax(a: np.ndarray) -> Optional[float]:
    a = a[np.isfinite(a)]
    return float(a.max()) if len(a) else None


def effort_peaks(tr: Track, i0: int, i1: int) -> dict:
    """Max HR over raw samples idx[i0]+1 .. idx[i1] (all samples with HR,
    moving or not); max 30 s power over the trailing windows lying wholly in
    that range (`interval_peaks`)."""
    pk = tr.peaks
    max_hr = _nanmax(pk["mhr"][i0 + 1:i1 + 1])
    p30 = None
    k = tr.p30k[i0] if i0 < len(tr.p30k) else None
    if k is not None and k <= i1:
        p30 = _nanmax(np.concatenate(([pk["p30h"][i0]], pk["mp30"][k + 1:i1 + 1])))
    return {"max_hr": _r(max_hr, 0), "max_p30": _r(p30, 0)}


def _f(v) -> Optional[float]:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(v, nd):
    v = _f(v)
    return None if v is None else round(v, nd)


# ---------------------------------------------------------------------------
# segments / routes
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    id: str
    kind: str                      # climb / descent / stretch / route
    family: str
    ref_file: str
    ref_i0: int
    ref_i1: int
    lat: list
    lon: list
    length_m: float
    gain_m: float                  # net, reference effort
    created: str
    efforts: list = field(default_factory=list)   # [{file, i0, i1, overlap, frechet_m}]
    derived: bool = False          # a merged chain of stretches (`_merge_chains`), rebuilt every time
    merged_from: list = field(default_factory=list)

    @property
    def direction(self) -> str:
        if self.kind == "climb":
            return "up"
        if self.kind == "descent":
            return "down"
        return "up" if self.gain_m >= 50 else "down" if self.gain_m <= -50 else "flat"

    def xy(self, lat0=None, lon0=None) -> np.ndarray:
        lat0 = self.lat[0] if lat0 is None else lat0
        lon0 = self.lon[0] if lon0 is None else lon0
        return RM.project(self.lat, self.lon, lat0, lon0)

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_json(cls, d: dict) -> "Segment":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def seg_id(kind: str, file: str, raw_i0: int, raw_i1: int) -> str:
    h = hashlib.sha1(f"{kind}|{file}|{raw_i0}|{raw_i1}".encode()).hexdigest()[:10]
    return ("r" if kind == "route" else "s") + h


def _new_segment(kind: str, tr: Track, i0: int, i1: int) -> Segment:
    lat = [float(x) for x in tr.lat[i0:i1 + 1]]
    lon = [float(x) for x in tr.lon[i0:i1 + 1]]
    xy = RM.project(lat, lon, lat[0], lon[0])
    gain = _f(tr.e[i1] - tr.e[i0]) or 0.0
    return Segment(id=seg_id(kind, tr.file, tr.raw["idx"][i0], tr.raw["idx"][i1]), kind=kind,
                   family=tr.family, ref_file=tr.file, ref_i0=i0, ref_i1=i1, lat=lat, lon=lon,
                   length_m=round(float(RM.path_length(xy)[-1]), 1), gain_m=round(gain, 1),
                   created=tr.start)


def match_segment(seg: Segment, tr: Track) -> list[dict]:
    """Efforts of seg in tr."""
    if tr.family != seg.family:
        return []
    ref = seg.xy()
    trk = tr.xy(seg.lat[0], seg.lon[0])
    out = []
    for i0, i1, ov in RM.find_efforts(trk, ref):
        fr = RM.discrete_frechet(trk[i0:i1 + 1], ref)
        out.append({"file": tr.file, "i0": int(i0), "i1": int(i1), "overlap": round(ov, 3),
                    "frechet_m": round(fr, 1)})
    return out


# ---------------------------------------------------------------------------
# the index
# ---------------------------------------------------------------------------

class RouteStore:
    """Paths are injectable so tests never touch ~/.wko5coach."""

    def __init__(self, root: Optional[Path] = None):
        self._root = Path(root) if root else None     # None: the tenant's routes folder, per call

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else home()

    tracks_dir = property(lambda self: self.root / "tracks")
    index_path = property(lambda self: self.root / "index.json")
    names_path = property(lambda self: self.root / "names.json")
    manifest_path = property(lambda self: self.root / "manifest.json")
    weather_dir = property(lambda self: self.root / "weather")

    # tier A
    def _track_path(self, file: str) -> Path:
        safe = file.replace("/", "__").replace("\\", "__")
        return self.tracks_dir / f"{safe}.json"

    def load_manifest(self) -> dict:
        return _read(self.manifest_path) or {}

    def save_manifest(self, m: dict) -> None:
        _write(self.manifest_path, m)

    def load_track(self, file: str) -> Optional[dict]:
        return _read(self._track_path(file))

    def save_track(self, file: str, d: dict) -> None:
        _write(self._track_path(file), d)

    def drop_track(self, file: str) -> None:
        try:
            self._track_path(file).unlink()
        except OSError:
            pass

    # tier B
    def load_index(self) -> Optional[dict]:
        d = _read(self.index_path)
        return d if d and d.get("version") == INDEX_VERSION else None

    def save_index(self, d: dict) -> None:
        _write(self.index_path, d)

    def names(self) -> dict:
        return _read(self.names_path) or {}

    def set_name(self, sid: str, name: str) -> None:
        n = self.names()
        if name:
            n[sid] = name
        else:
            n.pop(sid, None)
        _write(self.names_path, n, indent=1)


def _read(p: Path):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def _write(p: Path, d, indent=None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":")),
                   "utf-8")
    for attempt in range(5):
        try:
            tmp.replace(p)
            return
        except PermissionError:      # Windows: a reader / scanner holds the target for a moment
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------

Progress = Callable[[str, int, int], None]


def _noop(*_):
    pass


def candidates_of(tr: Track) -> list[tuple[str, int, int]]:
    return [("climb", a, b) for a, b in tr.raw.get("climbs", [])] + \
           [("descent", a, b) for a, b in tr.raw.get("descents", [])]


PIECE_PAD = 10          # kept points (~250 m) an uncovered piece reaches into covered track


def interval_overlap(la: np.ndarray, a0: int, a1: int, b0: int, b1: int) -> float:
    """Mutual overlap of two index ranges of ONE track, by path length:
    shared length / the longer range's length. >= 0.8 means each range is
    >= 80 % inside the other — the overlap-ratio test, on a single track."""
    lo, hi = max(a0, b0), min(a1, b1)
    if hi <= lo:
        return 0.0
    shared = la[hi] - la[lo]
    longer = max(la[a1] - la[a0], la[b1] - la[b0])
    return float(shared / longer) if longer > 0 else 0.0


class _State:
    """Working set of a detection pass (see `detect`)."""

    def __init__(self, tracks: dict[str, Track]):
        self.tracks = tracks
        self.segs: list[Segment] = []
        self.by_id: dict[str, Segment] = {}
        self.seg_cells: dict[tuple, list[Segment]] = {}      # 1 km cell of the start -> segments
        self.done: set[str] = set()                          # tracks already processed
        self.cell_index: dict = {}                           # 100 m cell -> processed tracks
        self.on: dict[str, list] = {}                        # file -> [(i0, i1, seg id)]
        self.la: dict[str, np.ndarray] = {}

    def length(self, tr: Track) -> np.ndarray:
        la = self.la.get(tr.file)
        if la is None:
            la = self.la[tr.file] = RM.path_length(tr.xy(tr.lat[0], tr.lon[0]))
        return la

    def add_segment(self, s: Segment) -> None:
        self.segs.append(s)
        self.by_id[s.id] = s
        self.seg_cells.setdefault((int(s.lat[0] // 0.01), int(s.lon[0] // 0.01)), []).append(s)
        for e in s.efforts:
            self.on.setdefault(e["file"], []).append((e["i0"], e["i1"], s.id))

    def mark_done(self, tr: Track) -> None:
        self.done.add(tr.file)
        for c in tr.cells:
            self.cell_index.setdefault(c, set()).add(tr.file)

    def segments_near(self, tr: Track) -> list[Segment]:
        keys = {(int(a // 0.01), int(b // 0.01)) for a, b in zip(tr.lat, tr.lon)}
        out, seen = [], set()
        # sorted: set order changes between processes (hash seed), and the
        # order segments are matched in decides which one covers a candidate
        for ky, kx in sorted(keys):
            for k in ((ky + dy, kx + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                for s in self.seg_cells.get(k, ()):
                    if s.id not in seen:
                        seen.add(s.id)
                        out.append(s)
        return out

    def add_efforts(self, s: Segment, tr: Track) -> None:
        for e in match_segment(s, tr):
            s.efforts.append(e)
            self.on.setdefault(tr.file, []).append((e["i0"], e["i1"], s.id))

    def covered_by(self, tr: Track, i0: int, i1: int) -> Optional[Segment]:
        """The segment one of whose efforts in tr covers [i0, i1] (mutual >= 0.8)."""
        la = self.length(tr)
        for e0, e1, sid in self.on.get(tr.file, ()):
            if interval_overlap(la, i0, i1, e0, e1) >= RM.MIN_OVERLAP:
                return self.by_id.get(sid)
        return None

    def new_segment(self, kind: str, tr: Track, i0: int, i1: int) -> Optional[Segment]:
        la = self.length(tr)
        if la[i1] - la[i0] < (200.0 if kind != "stretch" else MIN_STRETCH_M * 0.9):
            return None
        s = _new_segment(kind, tr, i0, i1)
        if s.id in self.by_id:
            return None
        self.add_segment(s)
        # every track processed so far (later tracks are matched when processed)
        key = (int(s.lat[0] // 0.001), int(s.lon[0] // 0.001))
        near = set()
        for a in (key[0] - 1, key[0], key[0] + 1):
            for b in (key[1] - 1, key[1], key[1] + 1):
                near |= self.cell_index.get((a, b), set())
        for f in sorted(near, key=lambda f: (self.tracks[f].start, f)):
            self.add_efforts(s, self.tracks[f])
        return s

    def uncovered_pieces(self, tr: Track) -> list[tuple[int, int]]:
        """Parts of tr >= 500 m not inside an effort of a known segment,
        padded ~250 m into the covered part so a stretch can start there."""
        n = len(tr)
        la = self.length(tr)
        cov = np.zeros(n, dtype=bool)
        for e0, e1, _ in self.on.get(tr.file, ()):
            cov[e0:e1 + 1] = True
        out, i = [], 0
        while i < n:
            if cov[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and not cov[j + 1]:
                j += 1
            a, b = max(0, i - PIECE_PAD), min(n - 1, j + PIECE_PAD)
            if la[b] - la[a] >= MIN_STRETCH_M:
                out.append((a, b))
            i = j + 1
        return out


def detect(tracks: dict[str, Track], prev: Optional[dict] = None, new_files: Optional[set] = None,
           progress: Progress = _noop, old_routes: Optional[list] = None,
           pair_cache: Optional[dict] = None) -> dict:
    """Segments and routes over `tracks`, one track at a time, oldest first.

    For each track:
      a. match it against every known segment near it (its efforts);
      b. each of its climbs / descents not already covered by one of those
         efforts (mutual >= 0.8 along the track) becomes a new segment;
      c. the parts of it not covered by any effort (>= 500 m, padded 250 m)
         are compared with every processed track sharing >= 5 cells with
         them; each same-direction common stretch >= 500 m, placed on the
         OLDER of the two tracks, becomes a new segment unless an effort on
         that track already covers it.
    A new segment is matched at once against every processed track; later
    tracks meet it in step a. So an incremental build (prev + new_files: the
    old tracks count as processed, their efforts kept) finds what a full
    build does, and existing ids and references never change.
    Routes are clustered over all tracks every time (`cluster_routes`); ids
    and references come from prev's routes, or `old_routes` (a full rebuild /
    an index of an older version).
    """
    ordered = sorted(tracks.values(), key=lambda t: t.start)
    st = _State(tracks)
    routes: list[dict] = list(old_routes or [])
    if prev:
        for d in prev.get("segments", []) + prev.get("pending", []):
            if d.get("derived"):           # merged chains are rebuilt by _finish, never references
                continue
            s = Segment.from_json(d)
            if s.ref_file not in tracks:
                continue
            s.efforts = [{k: e[k] for k in ("file", "i0", "i1", "overlap", "frechet_m")}
                         for e in s.efforts if e["file"] in tracks and e["file"] not in new_files]
            st.add_segment(s)
        routes = [r for r in prev.get("routes", []) + prev.get("pending_routes", [])
                  if r["ref_file"] in tracks]
        for t in ordered:
            if t.file not in new_files:
                st.mark_done(t)
        todo = [t for t in ordered if t.file in new_files]
    else:
        todo = ordered

    total = len(todo)
    for n, tr in enumerate(todo):
        progress("找爬坡與重疊路段", n, total)
        # a. known segments
        for s in st.segments_near(tr):
            st.add_efforts(s, tr)
        st.mark_done(tr)
        # b. its climbs and descents
        for kind, a, b in candidates_of(tr):
            hit = st.covered_by(tr, a, b)
            if hit is None:
                st.new_segment(kind, tr, a, b)
            elif hit.kind == "stretch" and kind in ("climb", "descent") and hit.ref_file == tr.file:
                hit.kind = kind            # the same stretch, now known to be a climb
        # c. stretches shared with processed tracks
        for p0, p1 in st.uncovered_pieces(tr):
            piece_cells = coarse_cells(tr.raw["lat"][p0:p1 + 1], tr.raw["lon"][p0:p1 + 1])
            counts: dict[str, int] = {}
            for c in piece_cells:
                for f in st.cell_index.get(c, ()):
                    counts[f] = counts.get(f, 0) + 1
            xa = tr.xy(tr.lat[p0], tr.lon[p0])[p0:p1 + 1]
            for f, k in sorted(counts.items(), key=lambda fk: (tracks[fk[0]].start, fk[0])):
                o = tracks[f]
                if k < MIN_PAIR_CELLS or f == tr.file or o.family != tr.family:
                    continue
                xb = o.xy(tr.lat[p0], tr.lon[p0])
                for i0, i1, j0, j1 in RM.common_runs(xa, xb, min_len=MIN_STRETCH_M):
                    i0, i1 = i0 + p0, i1 + p0
                    ref, r0, r1 = (o, j0, j1) if o.start <= tr.start else (tr, i0, i1)
                    if st.covered_by(ref, r0, r1) is None:
                        st.new_segment("stretch", ref, r0, r1)
    segs = st.segs
    for s in segs:
        s.efforts.sort(key=lambda e: (tracks[e["file"]].start, e["i0"]))

    # routes: clustered again over ALL tracks on every build (cheap next to the
    # segments), ids and references carried over from the previous index
    progress("找重複路線", 0, 1)
    routes = cluster_routes(tracks, routes, progress=progress, pair_cache=pair_cache)

    # 4. keep what repeats; drop stretches that add nothing
    return _finish(segs, routes, tracks)


# ---------------------------------------------------------------------------
# routes: whole activities clustered by the length share they have in common
# ---------------------------------------------------------------------------

ROUTE_SAME = ROUTE_MIN_OVERLAP   # mutual length share in each other's 30 m buffer: the same route
ROUTE_PART = 0.8                 # 推估 one-way share: a run lying this much on a longer route is part of it
ROUTE_PART_LEN = 0.9             # 推估 ... and is at most 0.9 x its length (two variants of one
                                 #      length sharing 80 % are siblings, not one inside the other)
ROUTE_COMMON = 0.5               # 推估 canonical path = the reference's stretch more than half the members run
ROUTE_DIR = 0.8                  # share of on-path points that follow the canonical path in order
ROUTE_MAX_POINTS = 400           # canonical path thinned for the index / map


_CELL = 0.001                    # the 100 m cells of algorithms/routes.py


def _cell_codes(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    return np.floor(lat / _CELL).astype(np.int64) * 1_000_000 + np.floor(lon / _CELL).astype(np.int64)


class _RouteGeo:
    """Per-track geometry for one clustering pass, cached:

      even   points every 25 m along the track, as lat / lon (the local
             projection is linear in lat / lon, so interpolating lat / lon is
             the same as resampling the projected path: `RM.resample_even`)
      halo   the 100 m cells of those points and their 8 neighbours. A point
             within 30 m of the track lies within 30 + 12.5 m of one of its
             even points, so inside the halo: the share of a's even points in
             b's halo bounds a's share in b's 30 m buffer from above, and a
             pair whose bound is short of the threshold is never measured."""

    def __init__(self, tracks: dict[str, Track]):
        self.tracks = tracks
        self._xy: dict[tuple, np.ndarray] = {}
        self._even: dict[str, tuple] = {}
        self._halo: dict[str, np.ndarray] = {}
        self.memo: dict = {}

    def xy(self, f: str, origin: tuple) -> np.ndarray:
        k = (f, origin)
        v = self._xy.get(k)
        if v is None:
            tr = self.tracks[f]
            v = self._xy[k] = RM.project(tr.lat, tr.lon, origin[0], origin[1])
        return v

    def origin(self, f: str) -> tuple:
        tr = self.tracks[f]
        return float(tr.lat[0]), float(tr.lon[0])

    def even(self, f: str) -> tuple:
        """(lat, lon, cell codes) of the even points."""
        v = self._even.get(f)
        if v is None:
            tr = self.tracks[f]
            cum = RM.path_length(self.xy(f, self.origin(f)))
            L = float(cum[-1]) if len(cum) else 0.0
            s = np.append(np.arange(0.0, L, RM.STEP_M), L) if L > 0 else np.zeros(1)
            la, lo = np.interp(s, cum, tr.lat), np.interp(s, cum, tr.lon)
            v = self._even[f] = (la, lo, _cell_codes(la, lo))
        return v

    def even_xy(self, f: str, origin: tuple) -> np.ndarray:
        la, lo, _ = self.even(f)
        return RM.project(la, lo, origin[0], origin[1])

    def halo(self, f: str) -> np.ndarray:
        v = self._halo.get(f)
        if v is None:
            c = np.unique(self.even(f)[2])
            v = self._halo[f] = np.unique(np.concatenate(
                [c + dy * 1_000_000 + dx for dy in (-1, 0, 1) for dx in (-1, 0, 1)]))
        return v

    def bound(self, a: str, b: str) -> float:
        """Upper bound of a's share in b's 30 m buffer."""
        codes = self.even(a)[2]
        return float(np.isin(codes, self.halo(b), assume_unique=False).mean())

    def cover(self, a: str, b: str, origin: tuple) -> float:
        """a's length share inside b's 30 m buffer (= RM.cover_share)."""
        return RM.overlap_ratio(self.even_xy(a, origin), self.xy(b, origin))


def route_pairs(tracks: dict[str, Track], geo: Optional[_RouteGeo] = None,
                need: float = ROUTE_MIN_OVERLAP, cache: Optional[dict] = None) -> dict:
    """{(a, b): (share of a in b's buffer, share of b in a's buffer)} for every
    pair of same-family tracks that can reach `need` both ways (a before b in
    start order); the halo bound (`_RouteGeo`) skips the rest unmeasured.
    Direction- and start-free: the share is of length within 30 m of the
    other track's polyline. `cache` ({(a, b): shares or None}, kept by the
    Builder between builds, entries of changed files dropped) makes an
    incremental build measure only the pairs of its new tracks."""
    geo = geo or _RouteGeo(tracks)
    cache = {} if cache is None else cache
    order = sorted(tracks, key=lambda f: (tracks[f].start, f))
    pos = {f: k for k, f in enumerate(order)}
    cell_index: dict = {}
    for f in order:
        for c in tracks[f].cells:
            cell_index.setdefault(c, []).append(f)
    out = {}
    for a in order:
        ta = tracks[a]
        if not ta.cells or ta.family is None:
            continue
        counts: dict[str, int] = {}
        for c in ta.cells:
            for b in cell_index[c]:
                if pos[b] > pos[a]:
                    counts[b] = counts.get(b, 0) + 1
        oa = geo.origin(a)
        for b in sorted(counts, key=pos.get):
            tb = tracks[b]
            if tb.family != ta.family:
                continue
            key = (a, b)
            if key in cache:
                v = cache[key]
            else:
                v = None
                if geo.bound(a, b) >= need and geo.bound(b, a) >= need:
                    ca = geo.cover(a, b, oa)
                    if ca >= need:
                        v = (ca, geo.cover(b, a, oa))
                cache[key] = v
            if v is not None:
                out[key] = v
    return out


def _canonical(ref: Track, members: list[str], geo: _RouteGeo) -> dict:
    """The maximal common part of a route: the reference's kept points from
    the first to the last place at least ROUTE_COMMON of the members pass
    (within 30 m). Different car parks, a warm-up loop of one run or a detour
    at the end trim away; the shared path stays whole."""
    o = geo.origin(ref.file)
    xr = geo.xy(ref.file, o)
    la = RM.path_length(xr)
    even = RM.resample_even(xr)
    s_even = np.linspace(0.0, float(la[-1]), len(even)) if len(even) > 1 else np.zeros(len(even))
    cnt = np.ones(len(even))
    for f in members:
        if f != ref.file:
            cnt += RM.dist_to_path(even, geo.xy(f, o)) <= RM.TOL_M
    share = cnt / max(1, len(members))
    common = np.flatnonzero(share > ROUTE_COMMON)
    if len(common) == 0:
        i0, i1 = 0, len(ref) - 1
    else:
        s0, s1 = s_even[common[0]], s_even[common[-1]]
        i0 = int(max(0, np.searchsorted(la, s0, side="right") - 1))
        i1 = int(min(len(ref) - 1, np.searchsorted(la, s1, side="left")))
        if i1 <= i0:
            i0, i1 = 0, len(ref) - 1
    step = max(1, (i1 - i0 + 1) // ROUTE_MAX_POINTS)
    ks = list(range(i0, i1 + 1, step))
    if ks[-1] != i1:
        ks.append(i1)
    return {"ref_i0": i0, "ref_i1": i1, "lat": [float(ref.lat[k]) for k in ks],
            "lon": [float(ref.lon[k]) for k in ks], "length_m": round(float(la[i1] - la[i0]), 1),
            "common_share": round(float(share.mean()), 3)}


def _direction(canon_xy: np.ndarray, pts: np.ndarray) -> str:
    """same / reversed / mixed: the member's on-path points followed in order
    along the canonical path (`route_match.direction_shares`). An out-and-back
    fits both ways and counts as the same direction."""
    f, r = RM.direction_shares(canon_xy, pts)
    if f >= ROUTE_DIR and f >= r:
        return "same"
    if r >= ROUTE_DIR:
        return "reversed"
    return "mixed"


def cluster_routes(tracks: dict[str, Track], prev_routes: Optional[list] = None,
                   progress: Progress = _noop, pair_cache: Optional[dict] = None) -> list[dict]:
    """Routes over all tracks (docs/spec/route-progress.spec.md, Routes).

    1. pairs: the length share of each track inside the other's 30 m buffer
       (`route_pairs`); two runs are the same route when both shares are
       >= 0.8 — whatever the start point and direction;
    2. clusters, leader style: the track with the most unassigned same-route
       neighbours (ties: higher summed share, older, file) takes all of them;
       every member is within the threshold of its leader, so long chains of
       slightly different runs cannot drift into one route;
    3. the reference is the previous index's reference when it is in the
       cluster (the id and the comparison axis stay), else the leader; the
       canonical path is the reference's maximal common part (`_canonical`);
    4. each member's direction (same / reversed / mixed) on that path;
    5. a cluster lying >= 0.8 inside a longer route is linked to it: with one
       run it is that route's partial, with more a sub-route (`parent`).
    Returns route dicts; single runs not part of any route are kept as
    pending routes (members = 1)."""
    geo = _RouteGeo(tracks)
    pairs = route_pairs(tracks, geo, cache=pair_cache)
    nbr: dict[str, dict[str, float]] = {f: {} for f in tracks}
    for (a, b), (ca, cb) in pairs.items():
        m = min(ca, cb)
        if m >= ROUTE_SAME:
            nbr[a][b] = m
            nbr[b][a] = m
    left = set(f for f in tracks if tracks[f].family is not None)

    def score(f):
        nb = [g for g in nbr[f] if g in left]
        return (-len(nb), -round(sum(nbr[f][g] for g in nb), 6), tracks[f].start, f)

    # lazy greedy: a score only gets worse as tracks are taken, so a popped
    # entry whose fresh score equals its stored one is the true best
    heap = [score(f) for f in left]
    heapq.heapify(heap)
    clusters: list[list[str]] = []
    while heap:
        item = heapq.heappop(heap)
        f = item[3]
        if f not in left:
            continue
        cur = score(f)
        if cur != item:
            heapq.heappush(heap, cur)
            continue
        group = [f] + sorted((g for g in nbr[f] if g in left), key=lambda g: (tracks[g].start, g))
        left.difference_update(group)
        clusters.append(group)
    progress("找重複路線", 1, 3)

    prev_routes = sorted(prev_routes or [], key=lambda r: (-len(r.get("members", [])), r["id"]))
    out = _route_dicts(clusters, tracks, geo, prev_routes)
    # clusters whose canonical paths are the same route are one route: a
    # leader with its own spur can leave out a run that matches the common part
    for _ in range(4):
        merged = _merge_same_canonical(out)
        if merged is None:
            break
        out = _route_dicts(merged, tracks, geo, prev_routes)
    progress("找重複路線", 2, 3)
    _link_parts(out, tracks)
    progress("找重複路線", 3, 3)
    return out


class _Shape:
    """lat / lon arrays of a canonical path, for _RouteGeo."""

    def __init__(self, lat, lon):
        self.lat = np.asarray(lat, dtype=float)
        self.lon = np.asarray(lon, dtype=float)


def _boxes_touch(a: tuple, b: tuple, pad: float = 0.0005) -> bool:
    return not (a[1] < b[0] - pad or a[0] > b[1] + pad or a[3] < b[2] - pad or a[2] > b[3] + pad)


def _box(r: dict) -> tuple:
    return min(r["lat"]), max(r["lat"]), min(r["lon"]), max(r["lon"])


def _merge_same_canonical(out: list[dict]) -> Optional[list[list[str]]]:
    """Member lists after merging every cluster into a larger one whose
    canonical path it matches (mutual length share >= 0.8); None when none
    does. The larger cluster's reference stays first (it stays the reference)."""
    shapes = {k: _Shape(r["lat"], r["lon"]) for k, r in enumerate(out)}
    g = _RouteGeo(shapes)
    order = sorted(range(len(out)), key=lambda k: (-len(out[k]["members"]), out[k]["created"], out[k]["id"]))
    boxes = [_box(r) for r in out]
    into: dict[int, int] = {}
    for i, a in enumerate(order):
        if a in into:
            continue
        ra = out[a]
        for b in order[i + 1:]:
            if b in into:
                continue
            rb = out[b]
            if rb["family"] != ra["family"] or not _boxes_touch(boxes[a], boxes[b]):
                continue
            if g.bound(a, b) < ROUTE_SAME or g.bound(b, a) < ROUTE_SAME:
                continue
            o = g.origin(a)
            if g.cover(a, b, o) >= ROUTE_SAME and g.cover(b, a, o) >= ROUTE_SAME:
                into[b] = a
    if not into:
        return None
    groups = []
    for k in order:
        if k in into:
            continue
        r = out[k]
        mem = [r["ref_file"]] + [f for f in r["members"] if f != r["ref_file"]]
        for b in order:
            if into.get(b) == k:
                mem += out[b]["members"]
        groups.append(mem)
    return groups


def _link_parts(out: list[dict], tracks: dict[str, Track]) -> None:
    """A route lying >= ROUTE_PART inside a longer repeated route (while that
    one is not >= ROUTE_SAME inside it — then they would be the same route)
    gets `parent`; a single run there is that route's partial."""
    shapes = {k: _Shape(r["lat"], r["lon"]) for k, r in enumerate(out)}
    g = _RouteGeo(shapes)
    boxes = [_box(r) for r in out]
    hosts = sorted((k for k, r in enumerate(out) if len(r["members"]) >= 2), key=lambda k: -out[k]["length_m"])
    for k, r in enumerate(out):
        best, best_key = None, None
        for p in hosts:
            q = out[p]
            if p == k or q["family"] != r["family"] or r["length_m"] > ROUTE_PART_LEN * q["length_m"]:
                continue
            if not _boxes_touch(boxes[k], boxes[p]) or g.bound(k, p) < ROUTE_PART:
                continue
            o = g.origin(k)
            share = g.cover(k, p, o)
            if share < ROUTE_PART or g.cover(p, k, o) >= ROUTE_SAME:
                continue
            key = (share, len(q["members"]), q["id"])
            if best_key is None or key > best_key:
                best, best_key = p, key
        if best is not None:
            r["parent"] = out[best]["id"]
            r["parent_share"] = round(best_key[0], 3)
    by_id = {r["id"]: r for r in out}
    for r in out:
        if r["parent"] and len(r["members"]) == 1:
            by_id[r["parent"]]["partials"].append({"file": r["members"][0], "share": r["parent_share"]})
    for r in out:
        r["partials"].sort(key=lambda p: (tracks[p["file"]].start, p["file"]))


def _route_dicts(clusters: list[list[str]], tracks: dict[str, Track], geo: _RouteGeo,
                 prev_routes: list[dict]) -> list[dict]:
    """Route dicts of member lists: ids and references from the previous
    index (its largest route first), else the cluster's first track."""
    where = {f: k for k, g in enumerate(clusters) for f in g}
    ref_of: dict[int, tuple[str, str]] = {}
    aliases: dict[str, int] = {}
    for r in prev_routes:
        k = where.get(r["ref_file"])
        if k is None:
            continue
        if k not in ref_of:
            ref_of[k] = (r["id"], r["ref_file"])
        elif len(r.get("members", [])) >= 2:      # a listed route merged into another: its link resolves
            aliases[r["id"]] = k
        for a in r.get("aliases", []):
            aliases.setdefault(a, k)
    out: list[dict] = []
    for k, group in enumerate(clusters):
        if k in ref_of:
            rid, ref_file = ref_of[k]
        else:
            ref_file = group[0]
            rid = seg_id("route", ref_file, 0, tracks[ref_file].raw["idx"][-1])
        ref = tracks[ref_file]
        members = sorted(group, key=lambda f: (tracks[f].start, f))
        ck = (ref_file, tuple(members))
        if ck not in geo.memo:                # the merge passes rebuild unchanged clusters
            canon = _canonical(ref, members, geo)
            o = (canon["lat"][0], canon["lon"][0])
            cxy = RM.project(canon["lat"], canon["lon"], *o)
            dirs = {f: ("same" if f == ref_file else _direction(cxy, geo.xy(f, o))) for f in members}
            geo.memo[ck] = (canon, dirs)
        canon, dirs = geo.memo[ck]
        out.append({"id": rid, "kind": "route", "family": ref.family, "ref_file": ref_file,
                    **canon,
                    "gain_m": round(float(ref.raw.get("climbing") or 0.0), 0),   # WKO5's climbing of the reference
                    "created": tracks[members[0]].start, "members": members, "dirs": dirs,
                    "partials": [], "parent": None,
                    "aliases": sorted(a for a, kk in aliases.items() if kk == k)})
    return out


STRETCH_ON_ROUTE = 0.8     # 推估 a stretch this much (length share) inside a route's canonical path ...
STRETCH_ROUTE_ACTS = 0.8   # 推估 ... with this share of its activities being that route's runs is the route
CHAIN_GAP_M = 250.0        # 推估 two stretches this close along a shared activity continue each other
CHAIN_JACCARD = 0.6        # 推估 activities in common (Jaccard) for two stretches to be one corridor
CHAIN_PIECE_ACTS = 0.8     # 推估 a piece with this share of its activities on the merged stretch is dropped


def _route_family(r: dict, routes: list[dict]) -> set:
    """Runs of a route, its partials and its sub-routes' runs."""
    out = set(r["members"]) | {p["file"] for p in r.get("partials", [])}
    for q in routes:
        if q.get("parent") == r["id"]:
            out |= set(q["members"])
    return out


def _route_holding(s: Segment, sxy: np.ndarray, acts: frozenset, kept_routes: list[dict],
                   routes: list[dict]) -> Optional[dict]:
    """The repeated route a stretch belongs to: the stretch lies >= 0.8 inside
    its canonical path and >= 0.8 of the stretch's activities are its runs
    (or partials / sub-route runs). Best = the most covering route."""
    best, best_key = None, None
    for r in kept_routes:
        if r["family"] != s.family:
            continue
        if max(r["lat"]) < min(s.lat) - 0.001 or min(r["lat"]) > max(s.lat) + 0.001 or \
                max(r["lon"]) < min(s.lon) - 0.001 or min(r["lon"]) > max(s.lon) + 0.001:
            continue
        fam = _route_family(r, routes)
        share_acts = len(acts & fam) / max(1, len(acts))
        if share_acts < STRETCH_ROUTE_ACTS:
            continue
        b = RM.project(r["lat"], r["lon"], s.lat[0], s.lon[0])
        cov = RM.cover_share(sxy, b)
        if cov >= STRETCH_ON_ROUTE:
            key = (share_acts, cov, len(r["members"]), r["id"])
            if best_key is None or key > best_key:
                best, best_key = r, key
    return best


def _merge_chains(stretches: list[Segment], acts: dict, tracks: dict[str, Track], drop: set) -> list[Segment]:
    """Chains of stretches -> their maximal common part (docs/spec, What is listed).

    Two stretches are links of one chain when they share >= 0.6 of their
    activities (Jaccard) and, on the oldest activity doing both, one's effort
    overlaps the other's or starts within 250 m of its end. For each chain of
    >= 2: the host is the activity doing the most links (ties: older, file);
    the links' efforts on the host merged where they touch give the longest
    run covering the most links; that run becomes a stretch (`derived`,
    matched against every activity of the chain). Each link whose activities
    are >= 0.8 on it, and whose path lies on it, is dropped. The merged
    stretch is kept only when it replaces >= 2 links — never one more item.
    Derived stretches are recomputed by every build (never references), so an
    incremental build makes the same ones as a full build."""
    if len(stretches) < 2 or not tracks:
        return []
    stretches = sorted(stretches, key=lambda s: s.id)
    eff: dict[str, dict[str, list]] = {}
    for s in stretches:
        d = eff[s.id] = {}
        for e in s.efforts:
            d.setdefault(e["file"], []).append((e["i0"], e["i1"]))
    la_cache: dict[str, np.ndarray] = {}

    def la(f):
        v = la_cache.get(f)
        if v is None:
            tr = tracks[f]
            v = la_cache[f] = RM.path_length(tr.xy(tr.lat[0], tr.lon[0]))
        return v

    def touch(f, a, b):
        L = la(f)
        lo, hi = (a, b) if a[0] <= b[0] else (b, a)
        return b[0] <= a[1] and a[0] <= b[1] or (L[hi[0]] - L[lo[1]] <= CHAIN_GAP_M)

    box = {s.id: (min(s.lat), max(s.lat), min(s.lon), max(s.lon)) for s in stretches}
    parent = {s.id: s.id for s in stretches}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pad = 0.003                                    # ~300 m: CHAIN_GAP_M plus slack
    for i, s in enumerate(stretches):
        for t in stretches[i + 1:]:
            if s.family != t.family:
                continue
            bs, bt = box[s.id], box[t.id]
            if bs[1] < bt[0] - pad or bs[0] > bt[1] + pad or bs[3] < bt[2] - pad or bs[2] > bt[3] + pad:
                continue
            common = acts[s.id] & acts[t.id]
            if not common or len(common) / len(acts[s.id] | acts[t.id]) < CHAIN_JACCARD:
                continue
            f = min((f for f in common if f in tracks), key=lambda f: (tracks[f].start, f), default=None)
            if f is None:
                continue
            if any(touch(f, a, b) for a in eff[s.id][f] for b in eff[t.id][f]):
                parent[find(s.id)] = find(t.id)
    groups: dict[str, list[Segment]] = {}
    for s in stretches:
        groups.setdefault(find(s.id), []).append(s)
    out = []
    for g in sorted(groups.values(), key=lambda g: min(s.id for s in g)):
        if len(g) < 2:
            continue
        files = sorted({f for s in g for f in acts[s.id] if f in tracks}, key=lambda f: (tracks[f].start, f))
        host = max(files, key=lambda f: (sum(1 for s in g if f in acts[s.id]), -files.index(f)))
        ivs = sorted((iv[0], iv[1], s.id) for s in g for iv in eff[s.id].get(host, []))
        runs: list[list] = []                          # [i0, i1, {ids}]
        for i0, i1, sid in ivs:
            if runs and touch(host, (runs[-1][0], runs[-1][1]), (i0, i1)):
                runs[-1][1] = max(runs[-1][1], i1)
                runs[-1][2].add(sid)
            else:
                runs.append([i0, i1, {sid}])
        L = la(host)
        i0, i1, ids = max(runs, key=lambda r: (len(r[2]), L[r[1]] - L[r[0]], -r[0]))
        if len(ids) < 2:
            continue
        m = _new_segment("stretch", tracks[host], i0, i1)
        if m.id in acts:                               # the run is one of the links itself
            continue
        m.derived = True
        for f in files:
            m.efforts.extend(match_segment(m, tracks[f]))
        m.efforts.sort(key=lambda e: (tracks[e["file"]].start, e["i0"]))
        m_acts = frozenset(e["file"] for e in m.efforts)
        if len(m_acts) < 2:
            continue
        mxy = m.xy()
        gone = []
        for s in g:
            if s.id not in ids:
                continue
            if len(acts[s.id] & m_acts) < CHAIN_PIECE_ACTS * len(acts[s.id]):
                continue
            if RM.overlap_ratio(s.xy(m.lat[0], m.lon[0]), mxy) < RM.MIN_OVERLAP:
                continue
            gone.append(s.id)
        if len(gone) < 2:
            continue
        drop.update(gone)
        m.merged_from = sorted(gone)
        out.append(m)
    return out


def _finish(segs: list[Segment], routes: list[dict], tracks: dict[str, Track]) -> dict:
    acts = {s.id: frozenset(e["file"] for e in s.efforts) for s in segs}
    keep = [s for s in segs if len(acts[s.id]) >= 2]
    pending = [s for s in segs if len(acts[s.id]) < 2]
    # a stretch is redundant when a segment containing it was done by exactly
    # the same activities — it would show the same efforts, only shorter
    drop = set()
    folded: set = set()                    # stretches that are a route (`_route_holding`)
    kept_routes = [r for r in routes if len(r["members"]) >= 2]
    same_acts: dict[frozenset, list[Segment]] = {}
    for s in keep:
        same_acts.setdefault(acts[s.id], []).append(s)
    for s in keep:
        a = s.xy()
        for o in same_acts[acts[s.id]]:
            if o is s or o.id in drop or o.family != s.family:
                continue
            # a climb / descent is only made redundant by a longer one of its kind;
            # a stretch by any longer segment
            if s.kind != "stretch" and o.kind != s.kind:
                continue
            if (o.kind == "stretch" or o.kind == s.kind) and \
                    (o.length_m < s.length_m or (o.length_m == s.length_m and o.id > s.id)):
                continue
            b = o.xy(s.lat[0], s.lon[0])
            if RM.overlap_ratio(a, b) >= RM.MIN_OVERLAP:
                drop.add(s.id)
                break
    # the other way round: a longer variant whose activities (>= 0.8) all do
    # a part of it that more activities do — the same hill or path with its
    # ends detected a little differently — is listed once, as that common part
    for o in sorted(keep, key=lambda s: (-s.length_m, s.id)):
        if o.id in drop:
            continue
        for s in keep:
            if s is o or s.id in drop or s.family != o.family or s.length_m >= o.length_m:
                continue
            if (s.kind == "stretch") != (o.kind == "stretch") or s.kind != "stretch" and s.kind != o.kind:
                continue
            if len(acts[s.id]) <= len(acts[o.id]) or \
                    len(acts[o.id] & acts[s.id]) < CHAIN_PIECE_ACTS * len(acts[o.id]):
                continue
            if RM.overlap_ratio(s.xy(), o.xy(s.lat[0], s.lon[0])) >= RM.MIN_OVERLAP:
                drop.add(o.id)
                break
    for s in keep:
        if s.id in drop or s.kind != "stretch":
            continue
        a = s.xy()
        # a stretch on a route, done mostly by that route's runs, is that route
        r = _route_holding(s, a, acts[s.id], kept_routes, routes)
        if r is not None:
            drop.add(s.id)
            folded.add(s.id)
            covered = r.setdefault("stretches", [])
            if s.id not in covered:
                covered.append(s.id)
    # overlapping stretches: common runs of different pairs start and end at
    # different junctions, so one busy path yields many overlapping pieces.
    # Keep climbs / descents, then stretches by activities x length (ties: id);
    # a stretch lying >= 50 % on an already kept segment is dropped.
    # A stretch folded into a route takes its place in that order as before and
    # suppresses the lower pieces around it (its path is listed — as the
    # route), without being listed itself.
    kept: list[Segment] = [s for s in keep if s.kind != "stretch" and s.id not in drop]
    order = sorted((s for s in keep if s.kind == "stretch" and (s.id not in drop or s.id in folded)),
                   key=lambda s: (-len(acts[s.id]) * s.length_m, s.id))
    suppressed = set()
    boxes = {}

    def box(s):
        b = boxes.get(s.id)
        if b is None:
            b = boxes[s.id] = (min(s.lat), max(s.lat), min(s.lon), max(s.lon))
        return b

    for s in order:
        a = None
        la0, la1, lo0, lo1 = box(s)
        for k in kept:
            if k.family != s.family:
                continue
            ka0, ka1, ko0, ko1 = box(k)
            if ka1 < la0 - 0.001 or ka0 > la1 + 0.001 or ko1 < lo0 - 0.001 or ko0 > lo1 + 0.001:
                continue
            if a is None:
                a = s.xy()
            if RM.overlap_ratio(a, k.xy(s.lat[0], s.lon[0])) >= STRETCH_NMS_OVERLAP:
                drop.add(s.id)
                suppressed.add(s.id)
                break
        if s.id in folded:
            if s.id not in suppressed:
                kept.append(s)
        elif s.id not in drop:
            kept.append(s)
    # pieces of one corridor: common runs of different pairs end at different
    # junctions, so one path walked by the same activities comes out as a
    # chain of listed stretches; the chain's maximal common part replaces them
    derived = _merge_chains([s for s in keep if s.kind == "stretch" and s.id not in drop], acts, tracks, drop)
    for s in derived:
        acts[s.id] = frozenset(e["file"] for e in s.efforts)
        r = _route_holding(s, s.xy(), acts[s.id], kept_routes, routes)
        if r is not None:                      # the merged corridor is a route already
            r.setdefault("stretches", []).append(s.id)
            continue
        keep.append(s)
    return {
        "version": INDEX_VERSION,
        "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "segments": [s.to_json() for s in keep if s.id not in drop],
        # singles and redundant stretches stay as references, so an incremental
        # build that adds their second effort finds what a full build would
        "pending": [s.to_json() for s in pending] + [s.to_json() for s in keep if s.id in drop and not s.derived],
        "routes": kept_routes,
        "pending_routes": [r for r in routes if len(r["members"]) < 2],
    }


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------

_PEAKS: Optional[list] = None


def peaks() -> list[dict]:
    global _PEAKS
    if _PEAKS is None:
        try:
            data = json.loads(PEAKS_PATH.read_text("utf-8"))
            _PEAKS = [p for p in data.get("peaks", []) if p.get("lat") is not None]
        except (OSError, ValueError):
            _PEAKS = []
    return _PEAKS


def nearest_peak(lat: float, lon: float, radius_m: float = PEAK_NAME_RADIUS_M) -> Optional[dict]:
    best, bd = None, radius_m
    for p in peaks():
        if abs(p["lat"] - lat) > 0.02 or abs(p["lon"] - lon) > 0.02:
            continue
        d = RM.haversine_m(lat, lon, p["lat"], p["lon"])
        if d <= bd:
            best, bd = p, d
    return best


def auto_name(kind: str, lat: list, lon: list, length_m: float, gain_m: float,
              elev: Optional[list] = None) -> str:
    """Nearest 百岳 / 小百岳 within 1 km of the landmark end (a climb's top, a
    descent's start, a route's highest point), else a descriptive label."""
    km = f"{length_m / 1000:.1f} km"
    if kind == "climb":
        pk = nearest_peak(lat[-1], lon[-1]) or nearest_peak(lat[0], lon[0])
        return f"{pk['name']} 爬坡 {km} ↑{round(abs(gain_m))} m" if pk else f"爬坡 {km} ↑{round(abs(gain_m))} m"
    if kind == "descent":
        pk = nearest_peak(lat[0], lon[0]) or nearest_peak(lat[-1], lon[-1])
        return f"{pk['name']} 下坡 {km} ↓{round(abs(gain_m))} m" if pk else f"下坡 {km} ↓{round(abs(gain_m))} m"
    if kind == "route":
        pk = None
        if elev:
            finite = [(v, i) for i, v in enumerate(elev) if v is not None]
            if finite:
                i = max(finite)[1]
                pk = nearest_peak(lat[i], lon[i])
        pk = pk or nearest_peak(lat[0], lon[0])
        g = f" ↑{round(gain_m)} m" if gain_m >= 50 else ""
        return f"{pk['name']} 路線 {km}{g}" if pk else f"路線 {km}{g}"
    pk = nearest_peak(lat[0], lon[0]) or nearest_peak(lat[-1], lon[-1])
    g = f" ↑{round(gain_m)} m" if gain_m >= 50 else f" ↓{round(-gain_m)} m" if gain_m <= -50 else ""
    return f"{pk['name']} 路段 {km}{g}" if pk else f"路段 {km}{g}"


# ---------------------------------------------------------------------------
# the builder: background, with progress
# ---------------------------------------------------------------------------

def _stamp(p: Path) -> list:
    st = p.stat()
    return [st.st_size, int(st.st_mtime), ALGO_VERSION]


class Builder:
    """One background build at a time. `status` is what the page polls."""

    def __init__(self, store: RouteStore, weather_get: Optional[Callable] = None,
                 activity_weather: bool = False):
        """weather_get: the archive client (route_weather / weather._http_get);
        None = no historical weather (tests, offline use). activity_weather:
        also the per-activity heat exposure (route_weather.fill_activities)
        for the heat-acclimation index — the app's builder turns it on."""
        self.store = store
        self.weather_get = weather_get
        self.activity_weather = activity_weather
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.status = {"state": "idle", "phase": None, "done": 0, "total": 0,
                       "started_at": None, "finished_at": None, "error": None}
        self._tracks: dict[str, Track] = {}
        self._pairs: dict = {}            # route_pairs cache, by file pair

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, source: Callable, full: bool = False) -> bool:
        """source() -> (workouts, reader); see `build`. False if already running."""
        with self.lock:
            if self.running():
                return False
            self.status.update(state="building", phase="準備", done=0, total=0, error=None,
                               started_at=dt.datetime.now().isoformat(timespec="seconds"),
                               finished_at=None)
            self.thread = threading.Thread(target=self._run, args=(source, full), daemon=True,
                                           name="route-builder")
            self.thread.start()
            return True

    def _progress(self, phase: str, done: int, total: int) -> None:
        self.status.update(phase=phase, done=done, total=total)

    def _run(self, source, full):
        try:
            workouts, reader = source()
            self.build(workouts, reader, full=full)
            self.status.update(state="done", phase=None,
                               finished_at=dt.datetime.now().isoformat(timespec="seconds"))
        except Exception as e:     # noqa: BLE001 — surfaced on the page
            self.status.update(state="error", error=f"{type(e).__name__}: {e}",
                               finished_at=dt.datetime.now().isoformat(timespec="seconds"))
            traceback.print_exc()

    def stale(self, workouts: list[tuple[str, Path, dict]]) -> bool:
        """True when a workout file is new, changed or gone since the last build."""
        m = self.store.load_manifest()
        files = m.get("files", {})
        cur = {}
        for f, p, _meta in workouts:
            try:
                cur[f] = _stamp(p)
            except OSError:
                continue
        return cur != files or self.store.load_index() is None

    def build(self, workouts: list[tuple[str, Path, dict]], reader: Callable, full: bool = False) -> dict:
        """workouts: [(file, path, meta)] where meta has start, sport,
        sport_type (+ lthr); reader(file, path, meta) -> compact track dict or
        None (no GPS). Tier A is refreshed for new / changed files, then the
        index is rebuilt (full) or updated (incremental)."""
        man = self.store.load_manifest()
        stamps: dict = man.get("files", {})
        has_track: dict = man.get("gps", {})
        prev = None if full else self.store.load_index()
        changed: set[str] = set()
        cur_files = set()
        total = len(workouts)
        for n, (f, p, meta) in enumerate(workouts):
            self._progress("讀取軌跡", n, total)
            try:
                st = _stamp(p)
            except OSError:
                continue
            cur_files.add(f)
            if stamps.get(f) == st and (f in has_track):
                continue
            d = reader(f, p, meta)
            stamps[f] = st
            has_track[f] = d is not None
            if d is None:
                self.store.drop_track(f)
            else:
                self.store.save_track(f, d)
            changed.add(f)
            if n % 50 == 0:        # resumable: a crash keeps what was parsed
                self.store.save_manifest({"files": stamps, "gps": has_track})
        for f in list(stamps):
            if f not in cur_files:
                stamps.pop(f, None)
                has_track.pop(f, None)
                self.store.drop_track(f)
                changed.add(f)
        self.store.save_manifest({"files": stamps, "gps": has_track})

        self._progress("載入軌跡", 0, 1)
        tracks = {}
        for f in sorted(cur_files):
            if not has_track.get(f):
                continue
            d = self._tracks.get(f)
            if d is None or f in changed:
                raw = self.store.load_track(f)
                if raw is None or raw.get("v") != ALGO_VERSION:
                    continue
                d = Track(raw)
            tracks[f] = d
        self._tracks = tracks
        if prev is not None and not changed:
            if self.weather_get is not None and (_weather_retry(prev) or
                                                 (self.activity_weather and _activity_weather_missing(self.store))):
                self._weather(prev, tracks)
            return prev
        new_files = set(tracks) if prev is None else {f for f in changed if f in tracks} | \
            {f for f in changed if f not in tracks}
        # the previous index of ANY version: after a version bump prev is None,
        # and carrying ids over is what keeps renames and links
        old = _read(self.store.index_path) if (full or prev is None) else None
        old_routes = (old.get("routes", []) + old.get("pending_routes", [])) if old else None
        self._pairs = {k: v for k, v in self._pairs.items() if k[0] not in changed and k[1] not in changed}
        idx = detect(tracks, prev=prev, new_files=new_files, progress=self._progress, old_routes=old_routes,
                     pair_cache=self._pairs)
        if old:
            carry_over(old, idx, self.store)
        self._progress("計算每次成績", 0, 1)
        enrich(idx, tracks)
        self.store.save_index(idx)
        if self.weather_get is not None:
            self._weather(idx, tracks)
        return idx

    def _weather(self, idx: dict, tracks: dict) -> None:
        """Historical weather on every effort, after the index is saved: the
        page already has the efforts while this runs, and any failure leaves
        them without weather instead of failing the build."""
        from backend.engine import route_weather as RW
        self._progress("歷史天氣", 0, 1)
        try:
            RW.fill(idx, tracks, self.store.weather_dir, self.weather_get, progress=self._progress)
        except Exception as e:     # noqa: BLE001 — weather is context, never a build failure
            idx["weather"] = {"error": f"{type(e).__name__}: {e}"[:200]}
            traceback.print_exc()
        self.store.save_index(idx)
        if not self.activity_weather or (idx.get("weather") or {}).get("failed"):
            return                 # an offline archive is not asked again for the activities
        try:
            # per-activity heat exposure for the heat-acclimation index (engine/heat.py)
            RW.fill_activities(tracks, self.store.root, self.weather_get, progress=self._progress)
        except Exception:          # noqa: BLE001
            traceback.print_exc()

    def tracks(self) -> dict[str, Track]:
        return self._tracks

    def track(self, file: str) -> Optional[Track]:
        tr = self._tracks.get(file)
        if tr is None:
            raw = self.store.load_track(file)
            if raw is None:
                return None
            tr = self._tracks[file] = Track(raw)
        return tr


def _weather_retry(idx: dict) -> bool:
    from backend.engine import route_weather as RW
    return RW.retry_wanted(idx)


def _activity_weather_missing(store) -> bool:
    from backend.engine import route_weather as RW
    return not RW.load_activity_weather(store.root)


def enrich(idx: dict, tracks: dict[str, Track]) -> None:
    """Metrics on every effort and the auto name on every segment / route,
    stored in the index so serving it needs no tracks."""
    for s in idx["segments"]:
        seg = Segment.from_json(s)
        for e in s["efforts"]:
            tr = tracks[e["file"]]
            e.update(start=tr.start, sport=tr.sport, sport_type=tr.sport_type,
                     raw_i0=tr.raw["idx"][e["i0"]], raw_i1=tr.raw["idx"][e["i1"]],
                     **effort_metrics(tr, e["i0"], e["i1"], seg.direction))
        s["direction"] = seg.direction
        s["auto_name"] = auto_name(s["kind"], s["lat"], s["lon"], s["length_m"], s["gain_m"])
    for r in idx["routes"]:
        # a whole route's net gain is ~0 on a loop: no VAM, climbing is WKO5's
        direction = "flat"
        r["direction"] = direction
        r["efforts"] = []
        dirs = r.get("dirs") or {}
        for f in r["members"]:
            tr = tracks[f]
            r["efforts"].append({"file": f, "i0": 0, "i1": len(tr) - 1, "start": tr.start,
                                 "sport": tr.sport, "sport_type": tr.sport_type,
                                 "raw_i0": tr.raw["idx"][0], "raw_i1": tr.raw["idx"][-1],
                                 "dir": dirs.get(f, "same"),
                                 **effort_metrics(tr, 0, len(tr) - 1, direction),
                                 "climbing_m": _r(tr.raw.get("climbing"), 0)})
        for p in r.get("partials", []):
            tr = tracks.get(p["file"])
            if tr is None:
                continue
            m = effort_metrics(tr, 0, len(tr) - 1, direction)
            p.update(start=tr.start, sport=tr.sport, sport_type=tr.sport_type,
                     dist_km=m["dist_km"], moving_s=m["moving_s"])
        ref = tracks[r["ref_file"]]
        elev = [None if np.isnan(v) else float(v) for v in ref.e]
        r["auto_name"] = auto_name("route", [float(x) for x in ref.lat], [float(x) for x in ref.lon],
                                   r["length_m"], r["gain_m"], elev)


def carry_over(old: dict, new: dict, store: RouteStore) -> None:
    """After a full rebuild: a new segment that is the same path as an old one
    takes the old id, so renames and links survive."""
    names = store.names()
    taken = {s["id"] for s in new["segments"]} | {r["id"] for r in new["routes"]}
    olds = old.get("segments", []) + old.get("routes", [])
    for s in new["segments"] + new["routes"]:
        if s["id"] in {o["id"] for o in olds}:
            continue
        for o in olds:
            if o["kind"] != s["kind"] or o["id"] in taken:
                continue
            a = RM.project(o["lat"], o["lon"], o["lat"][0], o["lon"][0])
            b = RM.project(s["lat"], s["lon"], o["lat"][0], o["lon"][0])
            if np.hypot(*(a[0] - b[0])) <= RM.END_TOL_M and np.hypot(*(a[-1] - b[-1])) <= RM.END_TOL_M \
                    and RM.mutual_overlap(a, b) >= RM.MIN_OVERLAP:
                taken.discard(s["id"])
                s["id"] = o["id"]
                taken.add(o["id"])
                break
    _ = names   # names are keyed by id; carrying the id carries the name


# ---------------------------------------------------------------------------
# reading a .wko4 into a compact track
# ---------------------------------------------------------------------------

def read_track(file: str, path: Path, meta: dict, corrections=None, parsed=None) -> Optional[dict]:
    """Parse one .wko4 (without Dataset's parse cache) into a compact track.
    `parsed`: an already-parsed file with the same channels (a FIT dataset's
    Wko4File, fitdataset.wko4) — how routes work without a WKO5 folder."""
    from backend.files.wko4_file import read_wko4
    fam = family_of(meta.get("sport", ""), meta.get("sport_type", ""))
    if fam is None:
        return None
    f = parsed if parsed is not None else read_wko4(path)
    if f is None:
        return None
    ch = f.channels
    t, lat, lon = ch.get("elapsedtime"), ch.get("latitude"), ch.get("longitude")
    if not t or not lat or not lon:
        return None
    tv = t.values

    def vals(name):
        c = ch.get(name)
        if not c:
            return None
        v = c.values
        if corrections is not None and name in ("heartrate", "power", "speed"):
            v = corrections.apply(file, name, tv, v)
        return v

    elev = vals("_elevation") or vals("elevation")
    return extract_track({"file": file, "start": meta["start"], "sport": meta.get("sport", ""),
                          "sport_type": meta.get("sport_type", ""), "climbing": meta.get("climbing")},
                         tv, lat.values, lon.values, vals("elapseddistance"), elev,
                         vals("heartrate"), vals("power"), vals("speed"), vals("temperature"),
                         lthr=meta.get("lthr"))
