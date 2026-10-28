"""
The ground under a course from OpenStreetMap (SP-543; research SP-531): how much of it is
dirt trail, how much is man-made, how much the map cannot tell.

The user's definition of 土徑 (2026-10-14): unstable ground — earth, roots, rock — counts;
anything laid by people does not (asphalt, concrete, laid stones, boardwalks, stone steps,
gravel forest roads). Every stretch is one of three groups:

  dirt    確定土徑   an explicit dirt / ground / rock … surface, sac_scale ≥ mountain_hiking,
                     a very bad smoothness, or (rules v2, SP-735) an untagged path out of town
  made    確定人工   a laid surface, highway=steps, a gravel / forest road, a road
  unsure  不確定     a path / footway without a surface in town, or no way within 50 m of the track

Where the ways come from (SP-727): the trail dictionary on this server (engine/trail_dict_store.py,
the SP-709 dictionary rebuilt monthly from the OSM Taiwan extract), not an Overpass server any more.
The classes are the dictionary's own (engine/trailclass.py, the one copy both sites read); each way
also carries its speed class (trail_dict_store.speed_kind: road / forest_road / steps / trail /
trail_t3), which the calculator's segment split reads (SP-727, trailhr.kind_mult).

How: the track every 20 m (on the course's own ruler); each point takes the class of the nearest
way within 50 m (Cairn's matchAndSegment, as before); equal neighbours are run-length merged into km
segments (a run shorter than 60 m joins the one before it: junction noise) — once for the surface
class (`segs`, what the page shows and the user marks) and once for the speed class (`kinds`).
`classify` is the old per-tag rule (Cairn's classifySurface + SP-531), kept for the cases shared
with Cairn (backend/tests/fixtures/osm_surface_cases.json) and a way without a stored class.

No dictionary on this server ($TRC_TRAIL_DICT_URL unset, or the first index still being built) →
no lookup, the page says 路面未知 and the calculator works without it.

Data © OpenStreetMap contributors, ODbL (ATTRIBUTION, shown next to the numbers).
"""
from __future__ import annotations

import datetime as dt
import math
import os
from typing import Optional, Sequence

import numpy as np

from backend.i18n import _

ATTRIBUTION = "© OpenStreetMap contributors"
VERSION = 2                     # 2: from the trail dictionary, with the speed classes (SP-727)
AROUND_RADIUS_M = 60.0          # the corridor (Cairn)
MATCH_THRESHOLD_M = 50.0        # a point farther than this from every way is unknown (Cairn)
STEP_M = 20.0                   # a label every 20 m along the track
MIN_RUN_M = 60.0                # a shorter run joins the one before it (推估: junction noise)
NO_KIND = "none"                # a stretch without a speed class (no way near, or an `other` way)

CLASSES = ("dirt", "gravel", "paved", "steps", "unknown")
GROUPS = ("dirt", "made", "unsure")
GROUP = {"dirt": "dirt", "gravel": "made", "paved": "made", "steps": "made", "unknown": "unsure"}

# Cairn's lists, plus the laid surfaces of the user's definition (paving_stones, wood = 棧道, …)
PAVED_SURFACES = frozenset({"asphalt", "concrete", "paved", "sett", "cobblestone",
                            "concrete:plates", "concrete:lanes", "paving_stones", "unhewn_cobblestone",
                            "brick", "bricks", "wood", "metal", "metal_grid", "rubber", "tartan",
                            "artificial_turf", "acrylic", "grass_paver"})
GRAVEL_SURFACES = frozenset({"gravel", "fine_gravel", "compacted", "pebblestone", "unpaved"})
DIRT_SURFACES = frozenset({"dirt", "ground", "soil", "earth", "mud", "sand", "grass", "rock", "bare_rock",
                           "woodchips"})
ROAD_HIGHWAYS = frozenset({"motorway", "trunk", "primary", "secondary", "tertiary", "residential",
                           "unclassified", "road", "service", "living_street"})
PATH_HIGHWAYS = frozenset({"path", "footway", "bridleway", "cycleway"})
SAC_DIRT = frozenset({"mountain_hiking", "demanding_mountain_hiking", "alpine_hiking",
                      "demanding_alpine_hiking", "difficult_alpine_hiking"})
SMOOTH_DIRT = frozenset({"very_bad", "horrible", "very_horrible", "impassable"})


class SurfaceError(Exception):
    """The lookup failed: `code` off (no dictionary on this server) / building (its first index is
    being built) / course (no track)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------- classification

def classify(tags: dict) -> str:
    """OSM way tags → dirt / gravel / paved / steps / unknown (Cairn's classifySurface + SP-531)."""
    tags = tags or {}
    hw = tags.get("highway") or ""
    sf = tags.get("surface") or ""
    if sf in PAVED_SURFACES:                     # an explicit surface beats the highway class
        return "paved"
    if hw == "steps":
        return "steps"
    if sf in GRAVEL_SURFACES:
        return "gravel"
    if sf in DIRT_SURFACES:
        return "dirt"
    if (tags.get("sac_scale") or "") in SAC_DIRT:
        return "dirt"
    if hw in ROAD_HIGHWAYS:
        return "paved"
    if (tags.get("smoothness") or "") in SMOOTH_DIRT:
        return "dirt"
    if hw == "track":
        return "gravel"
    return "unknown"                             # path / footway / … without a surface, or not a way


def group_of(cls: str) -> str:
    return GROUP.get(cls, "unsure")


def way_class(w: dict) -> str:
    """A way's surface class: the dictionary's (rules v2), else from its tags."""
    c = w.get("class")
    return c if c in CLASSES else classify(w.get("tags") or {})


# ---------------------------------------------------------------------------- matching

def _xy(lat, lon, lat0: float):
    k = 111320.0
    return np.asarray(lon, float) * k * math.cos(math.radians(lat0)), np.asarray(lat, float) * k


def nearest_ways(lat: Sequence[float], lon: Sequence[float], ways: list[dict],
                 threshold_m: float = MATCH_THRESHOLD_M) -> list[int]:
    """Each point → the index (in `ways`) of its nearest way within threshold_m, else −1 (Cairn's
    matchAndSegment; local flat metres, a grid of the way pieces instead of bboxes)."""
    n = len(lat)
    if n == 0:
        return []
    lat0 = float(np.mean(lat))
    ax, ay, bx, by, wi = [], [], [], [], []
    for k, w in enumerate(ways):
        g = w.get("geometry") or []
        if len(g) < 2:
            continue
        x, y = _xy([p["lat"] for p in g], [p["lon"] for p in g], lat0)
        ax.extend(x[:-1]); ay.extend(y[:-1]); bx.extend(x[1:]); by.extend(y[1:]); wi.extend([k] * (len(g) - 1))
    if not ax:
        return [-1] * n
    AX, AY, BX, BY = (np.asarray(v, float) for v in (ax, ay, bx, by))
    WI = np.asarray(wi, int)
    cell = max(100.0, threshold_m * 2)
    grid: dict[tuple[int, int], list[int]] = {}
    x0 = np.floor((np.minimum(AX, BX) - threshold_m) / cell).astype(int)
    x1 = np.floor((np.maximum(AX, BX) + threshold_m) / cell).astype(int)
    y0 = np.floor((np.minimum(AY, BY) - threshold_m) / cell).astype(int)
    y1 = np.floor((np.maximum(AY, BY) + threshold_m) / cell).astype(int)
    for i in range(len(AX)):
        for gx in range(x0[i], x1[i] + 1):
            for gy in range(y0[i], y1[i] + 1):
                grid.setdefault((gx, gy), []).append(i)
    PX, PY = _xy(lat, lon, lat0)
    out = []
    for px, py in zip(PX, PY):
        cand = grid.get((int(math.floor(px / cell)), int(math.floor(py / cell))))
        if not cand:
            out.append(-1)
            continue
        idx = np.asarray(cand)
        dx, dy = BX[idx] - AX[idx], BY[idx] - AY[idx]
        len2 = dx * dx + dy * dy
        t = np.where(len2 > 0, ((px - AX[idx]) * dx + (py - AY[idx]) * dy) / np.where(len2 > 0, len2, 1.0), 0.0)
        t = np.clip(t, 0.0, 1.0)
        d = np.hypot(px - (AX[idx] + t * dx), py - (AY[idx] + t * dy))
        j = int(np.argmin(d))
        out.append(int(WI[idx[j]]) if d[j] <= threshold_m else -1)
    return out


def label_points(lat: Sequence[float], lon: Sequence[float], ways: list[dict],
                 threshold_m: float = MATCH_THRESHOLD_M) -> list[str]:
    """Each point → the surface class of its nearest way within threshold_m, else unknown."""
    return [way_class(ways[k]) if k >= 0 else "unknown" for k in nearest_ways(lat, lon, ways, threshold_m)]


def kind_labels(near: Sequence[int], ways: list[dict]) -> list[str]:
    """Each point's speed class (trail_dict_store.KINDS) from nearest_ways, NO_KIND without one."""
    return [(ways[k].get("kind") or NO_KIND) if k >= 0 else NO_KIND for k in near]


def run_segments(d_m: Sequence[float], labels: Sequence[str], min_run_m: float = MIN_RUN_M) -> list[dict]:
    """Labels at distances d_m (m, increasing) → [{"a", "b" (km), "c"}]: equal neighbours merged,
    a run shorter than min_run_m joined to the one before it (the first one to the next)."""
    if len(d_m) < 2:
        return []
    runs: list[list] = []                  # [a_m, b_m, class]
    for i in range(1, len(d_m)):
        c = labels[i - 1]
        if runs and runs[-1][2] == c:
            runs[-1][1] = d_m[i]
        else:
            runs.append([d_m[i - 1], d_m[i], c])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for k, r in enumerate(runs):
            if r[1] - r[0] < min_run_m:
                if k > 0:
                    runs[k - 1][1] = r[1]
                else:
                    runs[1][0] = r[0]
                del runs[k]
                changed = True
                break
        merged = []
        for r in runs:
            if merged and merged[-1][2] == r[2]:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        runs = merged
    return [{"a": round(a / 1000.0, 3), "b": round(b / 1000.0, 3), "c": c} for a, b, c in runs]


def resample_track(track) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(d m, lat, lon) every STEP_M along the course's own ruler (course.track_distance), so the
    segments' km match the calculator's profile."""
    from backend.engine.racepower import course as CO
    td = CO.track_distance(track)
    d = np.asarray(td["d"], float)
    keep = td["keep"]
    lat = np.asarray(track.lat, float)[keep]
    lon = np.asarray(track.lon, float)[keep]
    if len(d) < 2 or d[-1] <= 0:
        raise SurfaceError("course", "track too short")
    xs = np.arange(0.0, d[-1], STEP_M)
    if xs[-1] < d[-1]:
        xs = np.append(xs, d[-1])
    return xs, np.interp(xs, d, lat), np.interp(xs, d, lon)


def dictionary():
    """The trail dictionary's index on this server (trail_dict_store.current); SurfaceError off /
    building without one."""
    from backend.engine import trail_dict_store as DS
    idx = DS.current()
    if idx is None:
        if DS.base_url() and DS.building():
            raise SurfaceError("building", "building")
        raise SurfaceError("off", "no dictionary")
    return idx


def lookup(xs, la, lo, index=None) -> dict:
    """Points every STEP_M (xs m, lat, lon) → {"segs", "kinds", "km", "ways"} from the dictionary."""
    idx = index if index is not None else dictionary()
    ways = idx.ways_near(la, lo, AROUND_RADIUS_M)
    near = nearest_ways(la, lo, ways)
    labels = [way_class(ways[k]) if k >= 0 else "unknown" for k in near]
    xl = list(map(float, xs))
    return {"segs": run_segments(xl, labels), "kinds": run_segments(xl, kind_labels(near, ways)),
            "km": round(float(xs[-1]) / 1000.0, 3), "ways": len(ways),
            "dict": idx.version, "rules": idx.rules_version}


def analyse(track, index=None) -> dict:
    """The course's surface profile: {"v", "src": "dict", "dict" (version), "rules" (rules version),
    "segs": [{"a", "b", "c"}], "kinds": [{"a", "b", "c" (speed class)}], "km", "ways", "at"}.
    SurfaceError when there is no dictionary on this server."""
    xs, la, lo = resample_track(track)
    out = lookup(xs, la, lo, index)
    return {"v": VERSION, "src": "dict", **out,
            "at": dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")}


def kind_shares(kinds: Optional[list], a_km: float, b_km: float) -> dict:
    """{speed class: share} of the stretch a_km–b_km from a profile's `kinds` (NO_KIND and the part
    past the profile left out: those keep the multiplier 1)."""
    span = b_km - a_km
    out: dict = {}
    if span <= 0:
        return out
    for s in kinds or []:
        if s["c"] == NO_KIND:
            continue
        o = min(b_km, s["b"]) - max(a_km, s["a"])
        if o > 0:
            out[s["c"]] = out.get(s["c"], 0.0) + o / span
    return out


def ground_of_track(track, index=None) -> Optional[dict]:
    """SP-544's back-test hook (backtest.GROUND_OF): an activity's own surface from the dictionary
    (surface.effective, no marks), None without a dictionary."""
    try:
        return effective(analyse(track, index))
    except SurfaceError:
        return None


# ---------------------------------------------------------------------------- what is shown / used

def pct(dist: dict) -> dict:
    """{group: metres} → whole percentages that add up to 100 (largest remainder)."""
    tot = sum(max(0.0, dist.get(g, 0.0)) for g in GROUPS)
    if tot <= 0:
        return {g: 0 for g in GROUPS}
    raw = {g: max(0.0, dist.get(g, 0.0)) / tot * 100.0 for g in GROUPS}
    out = {g: int(math.floor(v)) for g, v in raw.items()}
    for g in sorted(GROUPS, key=lambda g: raw[g] - out[g], reverse=True)[:100 - sum(out.values())]:
        out[g] += 1
    return out


def clean_manual(m) -> Optional[dict]:
    """A typed-in split {dirt, made, unsure} (whole %, adding up to 100) or None."""
    if not m:
        return None
    try:
        v = {g: int(round(float(m.get(g) or 0))) for g in GROUPS}
    except (TypeError, ValueError, AttributeError):
        raise ValueError(_("比例要是數字"))
    if any(x < 0 or x > 100 for x in v.values()) or sum(v.values()) != 100:
        raise ValueError(_("三個比例要加起來是 100 %"))
    return v


def clean_overrides(o, n: int) -> dict:
    """{segment index: dirt / made / unsure} for the profile's n segments (others dropped)."""
    out = {}
    for k, g in (o or {}).items():
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and g in GROUPS:
            out[str(i)] = g
    return out


def effective(profile: Optional[dict], overrides: Optional[dict] = None, manual: Optional[dict] = None) -> dict:
    """The segments with their group after the user's marks, the OSM split, the split shown
    (the typed-in one when there is one) and the km of each group."""
    segs = []
    dist = {g: 0.0 for g in GROUPS}
    osm = {g: 0.0 for g in GROUPS}
    ov = overrides or {}
    for i, s in enumerate((profile or {}).get("segs") or []):
        g0 = group_of(s["c"])
        g = ov.get(str(i), g0)
        segs.append({**s, "g": g, "user": str(i) in ov})
        osm[g0] += s["b"] - s["a"]
        dist[g] += s["b"] - s["a"]
    p = pct(dist)
    return {"segs": segs, "osm_pct": pct(osm), "marked_pct": p, "pct": manual or p,
            "km": {g: round(v, 3) for g, v in dist.items()}, "manual": manual}


def shares(eff: dict, a_km: float, b_km: float) -> dict:
    """{dirt, made, unsure} fractions of the stretch a_km–b_km. A typed-in split applies
    everywhere (it says nothing about where); a stretch past the profile counts as unsure."""
    if eff.get("manual"):
        return {g: eff["manual"][g] / 100.0 for g in GROUPS}
    span = b_km - a_km
    if span <= 0:
        return {"dirt": 0.0, "made": 0.0, "unsure": 1.0}
    out = {g: 0.0 for g in GROUPS}
    for s in eff.get("segs") or []:
        o = min(b_km, s["b"]) - max(a_km, s["a"])
        if o > 0:
            out[s["g"]] += o / span
    out["unsure"] += max(0.0, 1.0 - sum(out.values()))
    return out


def unknown_message(code: str, detail: str = "") -> str:
    if code == "off":
        from backend.engine import trail_dict_store as DS
        return _("這台還沒有步道字典（{env}）", env=DS.URL_ENV)
    if code == "building":
        return _("步道字典準備中，稍後再試")
    if code == "course":
        return _("路線已過期（伺服器重啟過），請重新上傳 GPX")
    return _("查不到 OSM 路面（{err}）", err=detail or "?")


def view(cid: str, row: Optional[dict], error: Optional[SurfaceError] = None) -> dict:
    """What the page gets: the stored profile with the user's marks (status ok), or why there is
    none (status unknown + reason + message; none = never looked up)."""
    base = {"course_id": cid, "attribution": ATTRIBUTION}
    if row and row.get("profile"):
        prof = row["profile"]
        eff = effective(prof, row.get("overrides"), row.get("manual"))
        return {**base, "status": "ok", "segs": eff["segs"], "pct": eff["pct"], "osm_pct": eff["osm_pct"],
                "marked_pct": eff["marked_pct"], "manual": eff["manual"], "km": prof.get("km"),
                "at": prof.get("at"), "ways": prof.get("ways")}
    if error is not None:
        return {**base, "status": "unknown", "reason": error.code, "message": unknown_message(error.code, str(error))}
    return {**base, "status": "none"}


# ---------------------------------------------------------------------------- SP-544

TECH_ENV = "TRC_TECH_DIRT_ONLY"
FLAT_MAX = 0.02                 # GaitRE.re: the trail technicality applies at g ≤ +2 %


def tech_dirt_only() -> bool:
    """SP-544's switch: the trail technicality only on the dirt share of each segment. Off unless
    $TRC_TECH_DIRT_ONLY is 1 (the back-test decides whether to turn it on)."""
    return (os.environ.get(TECH_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def apply_tech_dirt(segs: list[dict], grade_re, eff: dict) -> Optional[dict]:
    """SP-544: on a trail course the technicality factor (GaitRE.tech_at, flats and descents) is
    kept only on each segment's dirt share; the man-made share runs without it. The unsure share
    keeps it (SP-531 Q4: unsure is not guessed, the technicality stays as it was). Sets
    s["re_mult"] = (1 + share·(f − 1)) / f on the segments at g ≤ +2 % (pacing.course_time
    multiplies the RE by it; GaitRE.re already holds f). Returns what was applied, by the distance
    of those segments: {"dirt", "unsure", "made", "applied" (dirt + unsure) as whole %, "km"}, None
    when the model has no technicality."""
    if not eff or not getattr(grade_re, "trail", False) or not hasattr(grade_re, "tech_at"):
        return None
    tot = {g: 0.0 for g in GROUPS}
    d_all = 0.0
    for s in segs:
        g = s["grade"]
        if g > FLAT_MAX:
            continue
        f = float(grade_re.tech_at(g)[0] or 1.0)
        sh = shares(eff, s["start_km"], s["end_km"])
        on = sh["dirt"] + sh["unsure"]
        if f > 0:
            s["re_mult"] = (1.0 + on * (f - 1.0)) / f
        for k in GROUPS:
            tot[k] += sh[k] * s["dist_m"]
        d_all += s["dist_m"]
    if d_all <= 0:
        return None
    p = pct(tot)
    return {**p, "applied": p["dirt"] + p["unsure"], "km": round(d_all / 1000.0, 2), "manual": bool(eff.get("manual"))}
