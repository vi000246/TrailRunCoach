"""
Achievements — a record of every trail run and mountain day, built to be
shown to a hiking group when signing up for a trip.

Each record summarises one activity: what kind of mountain (by summit
altitude), distance, climb, moving time, highest point, which 百岳 were
summited, the biggest climb, and a per-day breakdown for multi-day trips. The
athlete can rename a record, name a repeated route, and enter the 上河 map
standard time to get the "上河時間比" hikers compare with.

Records are expensive (each needs a full .wko4 parse plus climb detection and
the Minetti integral), so they are cached on disk keyed by file size, mtime and
ALGO_VERSION — bump it whenever the computation changes.
"""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from backend.engine.algorithms.climbs import detect_climbs
from backend.engine.algorithms.effort import equivalent_flat_distance
from backend.engine.algorithms.routes import cells, cluster_routes
from backend.engine.algorithms.wko5_time import MOVING_SPEED_KMH

ALGO_VERSION = 2          # 2: moving mask excludes recording gaps; VAM on moving time
CACHE_PATH = None          # fixed files (tests); None = the tenant's
ANNOTATIONS_PATH = None


def cache_path() -> Path:
    from backend import tenancy
    return Path(CACHE_PATH) if CACHE_PATH is not None else tenancy.shared_path("achievements_cache.json")


def annotations_path() -> Path:
    from backend import tenancy
    return Path(ANNOTATIONS_PATH) if ANNOTATIONS_PATH is not None else tenancy.base_path("annotations.json")
PEAKS_PATH = Path(__file__).resolve().parents[1] / "data" / "baiyue.json"

# Mountain class by summit altitude — the grading Taiwanese hikers use.
CLASS_BAIYUE, CLASS_MID, CLASS_LOW = "百岳", "中級山", "郊山"
KIND_TRAIL, KIND_HIKE = "越野跑", "登山健行"
SUMMIT_RADIUS_M = 250.0          # track must pass this close to a peak to count it
MULTIDAY_HOURS = 20.0            # longer than this -> break down by calendar day
MIN_DAY_MOVING_S = 1800.0        # a day counts only if you moved >= 30 min on it
MAX_MOVING_GAP_S = 60.0          # a sample after a longer gap is not "moving"


def mountain_class(top_m: Optional[float]) -> str:
    if top_m is None:
        return CLASS_LOW
    if top_m >= 3000:
        return CLASS_BAIYUE
    if top_m >= 1500:
        return CLASS_MID
    return CLASS_LOW


def activity_kind(w) -> Optional[str]:
    if "runningtrail" in w.tags:
        return KIND_TRAIL
    if w.sport_type in ("hiking", "mountaineering"):
        return KIND_HIKE
    return None


# ---------------------------------------------------------------------------
# peaks
# ---------------------------------------------------------------------------

def load_peaks(path: Path = PEAKS_PATH, baiyue_only: bool = True) -> list[dict]:
    """Peaks with coordinates from baiyue.json (built by
    backend/scripts/build_baiyue.py). Summit detection uses the 百岳 only —
    the page lists them as 已登百岳; the file also carries 小百岳
    (`baiyue: false`) for callers that want them."""
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    return [p for p in data.get("peaks", []) if p.get("lat") is not None and p.get("lon") is not None
            and (not baiyue_only or p.get("baiyue", True))]


def _metres(lat1, lon1, lat2, lon2) -> float:
    """Equirectangular distance — plenty accurate at a few hundred metres."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000.0 * math.hypot(x, y)


def summited_peaks(lat, lon, peaks: list[dict], radius_m: float = SUMMIT_RADIUS_M) -> list[dict]:
    pts = [(a, b) for a, b in zip(lat, lon) if a is not None and b is not None]
    if not pts or not peaks:
        return []
    la0, la1 = min(p[0] for p in pts), max(p[0] for p in pts)
    lo0, lo1 = min(p[1] for p in pts), max(p[1] for p in pts)
    pad = 0.01
    hits = []
    for pk in peaks:
        if not (la0 - pad <= pk["lat"] <= la1 + pad and lo0 - pad <= pk["lon"] <= lo1 + pad):
            continue
        best = min(_metres(a, b, pk["lat"], pk["lon"]) for a, b in pts[::3] or pts)
        if best <= radius_m:
            hits.append({"name": pk["name"], "rank": pk.get("rank"),
                         "elevation_m": pk.get("elevation_m"), "distance_m": round(best)})
    return sorted(hits, key=lambda h: -(h["elevation_m"] or 0))


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

@dataclass
class DaySummary:
    date: str
    distance_km: float
    climbing_m: float
    moving_s: float
    top_m: Optional[float]


@dataclass
class Achievement:
    id: str                       # the .wko4 file, relative to the athlete folder
    start: str
    kind: str                     # 越野跑 / 登山健行
    mclass: str                   # 百岳 / 中級山 / 郊山
    auto_name: str
    distance_km: Optional[float]
    climbing_m: Optional[float]
    descending_m: Optional[float]
    moving_s: Optional[float]
    elapsed_s: Optional[float]
    top_m: Optional[float]
    peaks: list = field(default_factory=list)
    best_climb: Optional[dict] = None
    efd_km: Optional[float] = None
    avg_hr: Optional[float] = None
    days: list = field(default_factory=list)
    footprint: list = field(default_factory=list)   # route cells, for clustering
    route_id: Optional[int] = None
    route_key: Optional[str] = None
    route_count: int = 1


def _summarise(w, f, peaks: list[dict]) -> Optional[dict]:
    ch = f.channels
    t = ch.get("elapsedtime")
    if not t:
        return None
    tv = t.values
    elev = ch.get("_elevation") or ch.get("elevation")
    dist = ch.get("elapseddistance")
    speed = ch.get("speed")
    hr = ch.get("heartrate")
    lat, lon = ch.get("latitude"), ch.get("longitude")
    ev = elev.values if elev else [None] * len(tv)
    dv = dist.values if dist else [None] * len(tv)
    raw_elev = ch.get("elevation")
    top = max((v for v in (raw_elev.values if raw_elev else ev) if v is not None), default=None)

    thr = MOVING_SPEED_KMH.get("walk" if w.sport == "walk" else w.sport, 0.0)
    mask = moving_mask(tv, speed.values if speed else None, thr)
    moving = 0.0
    prev = 0.0
    for i, ti in enumerate(tv):
        if ti is None:
            continue
        dt_, prev = ti - prev, ti
        if mask[i]:
            moving += dt_

    climbs = (detect_climbs(tv, dv, ev, hr.values if hr else None, moving=mask)
              if elev and dist else [])
    best = max(climbs, key=lambda c: c.gain_m, default=None)
    efd = equivalent_flat_distance(dv, ev, walking=(w.sport == "walk")) if elev and dist else None

    hr_avg = None
    if hr:
        num = den = 0.0
        prev = 0.0
        for ti, h in zip(tv, hr.values):
            if ti is None:
                continue
            dt_, prev = ti - prev, ti
            if h is not None and dt_ > 0:
                num += h * dt_
                den += dt_
        hr_avg = num / den if den else None

    elapsed = (tv[-1] - 0.0) if tv and tv[-1] is not None else None
    days = []
    if elapsed and elapsed / 3600 > MULTIDAY_HOURS:
        days = _split_days(w.entry.start, tv, dv, ev, mask)
        # a broken device clock can stretch one day across midnight; only call
        # it multi-day if you actually moved on at least two separate days
        if sum(1 for d in days if d.moving_s >= MIN_DAY_MOVING_S) < 2:
            days = []

    return {
        "moving_s": moving or None,
        "elapsed_s": elapsed,
        "top_m": top,
        "peaks": summited_peaks(lat.values, lon.values, peaks) if lat and lon else [],
        "best_climb": None if best is None else {
            "gain_m": round(best.gain_m), "distance_km": round(best.distance_km, 2),
            "grade": round(best.grade, 3), "vam_m_per_h": round(best.vam_m_per_h),
            "duration_s": round(best.duration_s),
            "avg_hr": round(best.avg_hr) if best.avg_hr else None},
        "efd_km": round(efd["efd_km"], 2) if efd else None,
        "avg_hr": round(hr_avg) if hr_avg else None,
        "days": [asdict(d) for d in days],
        "footprint": sorted(cells(lat.values, lon.values)) if lat and lon else [],
    }


def moving_mask(t, speed, thr: float, max_gap_s: float = MAX_MOVING_GAP_S) -> list[bool]:
    """True where the athlete was genuinely moving: speed above the sport's
    threshold AND not arriving after a recording gap. A watch that stops
    logging overnight produces one sample whose interval spans the whole night;
    counting it as moving is how a 10 km hike showed 25 h of moving time."""
    out, prev = [], None
    for i, ti in enumerate(t):
        if ti is None:
            out.append(False)
            continue
        gap = (ti - prev) if prev is not None else ti
        prev = ti
        s = speed[i] if speed is not None and i < len(speed) else None
        out.append(s is not None and s > thr and gap <= max_gap_s)
    return out


def _split_days(start: dt.datetime, t, d, e, mask) -> list[DaySummary]:
    out: dict[dt.date, dict] = {}
    prev_t, prev_d, prev_e = 0.0, None, None
    for i, ti in enumerate(t):
        if ti is None:
            continue
        day = (start + dt.timedelta(seconds=ti)).date()
        b = out.setdefault(day, {"d": 0.0, "c": 0.0, "m": 0.0, "top": None})
        dt_ = ti - prev_t
        prev_t = ti
        di, ei = d[i], e[i]
        if di is not None and prev_d is not None and di > prev_d:
            b["d"] += di - prev_d
        if ei is not None and prev_e is not None and ei > prev_e:
            b["c"] += ei - prev_e
        if mask[i]:
            b["m"] += dt_
        if ei is not None:
            b["top"] = ei if b["top"] is None else max(b["top"], ei)
        if di is not None:
            prev_d = di
        if ei is not None:
            prev_e = ei
    return [DaySummary(str(k), round(v["d"], 2), round(v["c"]), round(v["m"]),
                       round(v["top"]) if v["top"] is not None else None)
            for k, v in sorted(out.items())]


def _cached_summary(ds, w, peaks: list[dict], cache: dict) -> tuple[Optional[dict], bool]:
    """(the GPS summary of one activity, whether `cache` changed): the cached one while its
    file is unchanged, else summarised again. None without a file / a time channel."""
    p = ds.dir / w.entry.file
    if not p.exists():
        return None, False
    st = p.stat()
    stamp = [st.st_size, int(st.st_mtime), ALGO_VERSION, len(peaks)]
    hit = cache.get(w.entry.file)
    if hit and hit.get("stamp") == stamp:
        return hit.get("summary"), False
    f = ds.wko4(w.idx)
    summary = _summarise(w, f, peaks) if f else None
    cache[w.entry.file] = {"stamp": stamp, "summary": summary}
    return summary, True


def baiyue_summits(ds, peaks: Optional[list[dict]] = None) -> dict[str, list[str]]:
    """{activity file: the 百岳 names its GPS track passed within SUMMIT_RADIUS_M} for the mountain
    days (activity_kind 登山健行) — this page's own detection and cache, read by the 活動類型
    filter's 百岳登山 (sport_map.kind_of, SP-263 owner 2026-10-06). {} on any error."""
    try:
        peaks = load_peaks() if peaks is None else peaks
        cache = _load_cache()
        dirty = False
        out: dict[str, list[str]] = {}
        for w in ds.workouts:
            if activity_kind(w) != KIND_HIKE:
                continue
            s, d = _cached_summary(ds, w, peaks, cache)
            dirty = dirty or d
            if s and s.get("peaks"):
                out[w.entry.file] = [p["name"] for p in s["peaks"]]
        if dirty:
            _save_cache(cache)
        return out
    except Exception:                       # noqa: BLE001 — a dataset without files (tests, demo)
        return {}


def build_achievements(ds, peaks: Optional[list[dict]] = None) -> list[Achievement]:
    """One Achievement per trail run / mountain day, oldest first, with route
    clusters assigned. Uses (and refreshes) the on-disk cache."""
    peaks = load_peaks() if peaks is None else peaks
    cache = _load_cache()
    dirty = False
    out: list[Achievement] = []
    for w in ds.workouts:
        kind = activity_kind(w)
        if kind is None:
            continue
        s, d = _cached_summary(ds, w, peaks, cache)
        dirty = dirty or d
        if not s:
            continue
        m = w.metrics
        top = s["top_m"]
        mc = mountain_class(top)
        out.append(Achievement(
            id=w.entry.file, start=w.entry.start.isoformat(), kind=kind, mclass=mc,
            auto_name=_auto_name(s, mc, kind),
            distance_km=m.get("distance"), climbing_m=m.get("climbing"),
            descending_m=m.get("descending"), moving_s=s["moving_s"],
            elapsed_s=s["elapsed_s"], top_m=top, peaks=s["peaks"],
            best_climb=s["best_climb"], efd_km=s["efd_km"], avg_hr=s["avg_hr"],
            days=s["days"], footprint=s["footprint"]))
    if dirty:
        _save_cache(cache)
    _assign_routes(out)
    return out


def _auto_name(s: dict, mclass: str, kind: str) -> str:
    if s["peaks"]:
        names = [p["name"] for p in s["peaks"]]
        return "、".join(names[:3]) + (f" 等 {len(names)} 座" if len(names) > 3 else "")
    top = s["top_m"]
    return f"{mclass} {round(top)}m" if top else kind


def _assign_routes(records: list[Achievement]) -> None:
    routes = cluster_routes([(r.id, frozenset(tuple(c) for c in r.footprint)) for r in records])
    by_id = {r.id: r for r in records}
    for route in routes:
        for key in route.members:
            rec = by_id[key]
            rec.route_id = route.id
            rec.route_key = route.members[0]       # stable: the first time you did it
            rec.route_count = len(route)


def _load_cache() -> dict:
    try:
        return json.loads(cache_path().read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        cache_path().parent.mkdir(parents=True, exist_ok=True)
        cache_path().write_text(json.dumps(cache), "utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# annotations — names, 上河 time, notes the athlete adds
# ---------------------------------------------------------------------------

class Annotations:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or annotations_path()
        try:
            self.data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {}
        # records keyed by the record id (= the activity file): a ByStartDict
        # (engine/activity_key.py) also finds a name stored under another source's file
        from backend.engine.activity_key import ByStartDict
        self.data["records"] = ByStartDict(self.data.get("records") or {})
        self.data.setdefault("routes", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")

    def record(self, rid: str) -> dict:
        return self.data["records"].get(rid, {})

    def set_record(self, rid: str, **fields) -> dict:
        from backend.engine import activity_key as AK
        key = self.data["records"].key_for(rid)        # the same activity under another file keeps its key
        cur = {**self.record(rid), **{k: v for k, v in fields.items() if v is not None}}
        for k, v in fields.items():
            if v == "" or v is False:
                cur.pop(k, None)
        if not cur.get("start"):
            st = AK.key_of(AK.start_of_file(rid))
            if st:
                cur["start"] = st
        self.data["records"][key] = cur
        self.save()
        return cur

    def route_name(self, key: Optional[str]) -> Optional[str]:
        return self.data["routes"].get(key) if key else None

    def set_route_name(self, key: str, name: str) -> None:
        if name:
            self.data["routes"][key] = name
        else:
            self.data["routes"].pop(key, None)
        self.save()


def to_row(rec: Achievement, ann: Annotations) -> dict:
    """Public, display-ready form of a record, with annotations merged."""
    a = ann.record(rec.id)
    shang_he = a.get("shang_he_min")
    ratio = (rec.moving_s / 60.0 / shang_he) if (shang_he and rec.moving_s) else None
    route_name = ann.route_name(rec.route_key)
    row = asdict(rec)
    row.pop("footprint", None)
    row.update({
        "name": a.get("name") or route_name or rec.auto_name,
        "route_name": route_name,
        "note": a.get("note"),
        "hidden": bool(a.get("hidden")),
        "shang_he_min": shang_he,
        "shang_he_ratio": round(ratio, 2) if ratio else None,
        "flat_pace_min_km": (round(rec.moving_s / 60.0 / rec.efd_km, 2)
                             if rec.moving_s and rec.efd_km else None),
    })
    return row
