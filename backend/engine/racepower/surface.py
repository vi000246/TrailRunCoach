"""
The ground under a course from OpenStreetMap (SP-543; research SP-531): how much of it is
dirt trail, how much is man-made, how much the map cannot tell.

The user's definition of 土徑 (2026-10-14): unstable ground — earth, roots, rock — counts;
anything laid by people does not (asphalt, concrete, laid stones, boardwalks, stone steps,
gravel forest roads). OSM's `surface` is missing on most Taiwanese paths (28–40 % tagged on
the SP-531 samples), so every stretch is one of three groups:

  dirt    確定土徑   an explicit dirt / ground / rock … surface, sac_scale ≥ mountain_hiking,
                     or a very bad smoothness
  made    確定人工   a laid surface, highway=steps, a gravel / forest road, a road
  unsure  不確定     a path / footway without a surface (SP-531 Q4: not guessed), or no way
                     within 50 m of the track

How (Cairn's surface analysis, inReachLiveTrack v2/server/src/lib/overpass.ts, copied — its
API is not called): the track's points, sampled down to ≤ 100, are the corridor of one
`way[highway](around:60, …)` Overpass query per 25 points (one chunk at a time: the NAS is a
service host, not a compute host); each track point (every 20 m) takes the class of the
nearest way within 50 m; equal neighbours are run-length merged into km segments (a run
shorter than 60 m joins the one before it: junction noise). The classes are Cairn's five
(classify) with the SP-531 changes; the three groups are GROUP. Test cases shared with Cairn:
backend/tests/fixtures/osm_surface_cases.json.

The Overpass server: $TRC_OVERPASS_URL (the self-hosted Taiwan extract on the NAS, LAN only;
unset → no lookup, the page says 路面未知). All-or-nothing: a failed chunk fails the lookup
(a partly looked-up course would show the missing part as 不確定, a wrong answer).

Data © OpenStreetMap contributors, ODbL (ATTRIBUTION, shown next to the numbers).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional, Sequence

import numpy as np

from backend.i18n import _

URL_ENV = "TRC_OVERPASS_URL"
ATTRIBUTION = "© OpenStreetMap contributors"
VERSION = 1
AROUND_RADIUS_M = 60.0          # the corridor (Cairn)
MATCH_THRESHOLD_M = 50.0        # a point farther than this from every way is unknown (Cairn)
MAX_QUERY_POINTS = 100          # corridor vertices for the whole course (Cairn)
CHUNK_MAX_POINTS = 25           # per Overpass request, overlapping by one (Cairn)
QUERY_TIMEOUT_S = 10            # [timeout:] in the query (Cairn)
CHUNK_TIMEOUT_S = 13.0          # one request (Cairn)
TOTAL_BUDGET_S = 40.0           # the whole lookup (Cairn)
STEP_M = 20.0                   # a label every 20 m along the track
MIN_RUN_M = 60.0                # a shorter run joins the one before it (推估: junction noise)
USER_AGENT = "TrailRunCoach (course surface, SP-543)"

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

_one_at_a_time = threading.Semaphore(1)       # one lookup against the NAS at a time
WAIT_S = 60.0


class SurfaceError(Exception):
    """The lookup failed: `code` off (no server set) / failed (the server) / busy / course."""

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


# ---------------------------------------------------------------------------- the query

def sample_path(pts: Sequence[tuple[float, float]], max_n: int = MAX_QUERY_POINTS) -> list[tuple[float, float]]:
    """Even sub-sample to ≤ max_n (+ the last point), as Cairn's samplePath."""
    if not pts:
        return []
    step = max(1, math.ceil(len(pts) / max_n))
    out = list(pts[::step])
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out


def chunk_points(pts: list, max_n: int = CHUNK_MAX_POINTS) -> list[list]:
    """Chunks of ≤ max_n points; neighbours share one point so the corridor has no gap."""
    if len(pts) <= max_n:
        return [pts]
    out, a = [], 0
    while a + 1 < len(pts):
        out.append(pts[a:min(a + max_n, len(pts))])
        a += max_n - 1
    return out


def build_query(pts: Sequence[tuple[float, float]], radius_m: float = AROUND_RADIUS_M) -> str:
    coords = ",".join(f"{la:.5f},{lo:.5f}" for la, lo in pts)
    return f"[out:json][timeout:{QUERY_TIMEOUT_S}];way[highway](around:{radius_m:.0f},{coords});out geom;"


def server_url() -> Optional[str]:
    u = (os.environ.get(URL_ENV) or "").strip()
    return u or None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):    # noqa: D401 — never follow
        return None


def post_query(url: str, query: str, timeout: float) -> list[dict]:
    """One Overpass request (POST data=…, no redirects: a login page is not an answer) → its
    elements. SurfaceError("failed") on anything else than a JSON answer."""
    body = urllib.parse.urlencode({"data": query}).encode("ascii")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"User-Agent": USER_AGENT,
                                          "Content-Type": "application/x-www-form-urlencoded"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise SurfaceError("failed", f"HTTP {e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise SurfaceError("failed", type(e).__name__)
    try:
        doc = json.loads(raw)
    except ValueError:
        raise SurfaceError("failed", "not JSON")
    if not isinstance(doc, dict) or not isinstance(doc.get("elements"), list):
        raise SurfaceError("failed", "no elements")
    return doc["elements"]


def fetch_ways(pts: Sequence[tuple[float, float]], url: str,
               post: Optional[Callable[[str, str, float], list]] = None) -> list[dict]:
    """The ways around the course (deduplicated by id), one chunk after the other within
    TOTAL_BUDGET_S. All-or-nothing: SurfaceError when any chunk fails."""
    if not url.lower().startswith(("http://", "https://")):
        raise SurfaceError("off", "bad url")
    post = post or post_query
    deadline = time.monotonic() + TOTAL_BUDGET_S
    ways: dict[int, dict] = {}
    for chunk in chunk_points(sample_path(pts)):
        left = deadline - time.monotonic()
        if left < 3.0:
            raise SurfaceError("failed", "time budget")
        for el in post(url, build_query(chunk), min(CHUNK_TIMEOUT_S, left)):
            if isinstance(el, dict) and el.get("type", "way") == "way" and el.get("geometry"):
                ways.setdefault(el.get("id"), el)
    return list(ways.values())


# ---------------------------------------------------------------------------- matching

def _xy(lat, lon, lat0: float):
    k = 111320.0
    return np.asarray(lon, float) * k * math.cos(math.radians(lat0)), np.asarray(lat, float) * k


def label_points(lat: Sequence[float], lon: Sequence[float], ways: list[dict],
                 threshold_m: float = MATCH_THRESHOLD_M) -> list[str]:
    """Each point → the class of its nearest way within threshold_m, else unknown (Cairn's
    matchAndSegment labels; local flat metres, a grid of the way pieces instead of bboxes)."""
    n = len(lat)
    if n == 0:
        return []
    lat0 = float(np.mean(lat))
    ax, ay, bx, by, cl = [], [], [], [], []
    for w in ways:
        g = w.get("geometry") or []
        if len(g) < 2:
            continue
        c = CLASSES.index(classify(w.get("tags") or {}))
        x, y = _xy([p["lat"] for p in g], [p["lon"] for p in g], lat0)
        ax.extend(x[:-1]); ay.extend(y[:-1]); bx.extend(x[1:]); by.extend(y[1:]); cl.extend([c] * (len(g) - 1))
    if not ax:
        return ["unknown"] * n
    AX, AY, BX, BY, CL = (np.asarray(v, float) for v in (ax, ay, bx, by, cl))
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
            out.append("unknown")
            continue
        idx = np.asarray(cand)
        dx, dy = BX[idx] - AX[idx], BY[idx] - AY[idx]
        len2 = dx * dx + dy * dy
        t = np.where(len2 > 0, ((px - AX[idx]) * dx + (py - AY[idx]) * dy) / np.where(len2 > 0, len2, 1.0), 0.0)
        t = np.clip(t, 0.0, 1.0)
        d = np.hypot(px - (AX[idx] + t * dx), py - (AY[idx] + t * dy))
        j = int(np.argmin(d))
        out.append(CLASSES[int(CL[idx[j]])] if d[j] <= threshold_m else "unknown")
    return out


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


def analyse(track, url: Optional[str] = None,
            post: Optional[Callable[[str, str, float], list]] = None) -> dict:
    """The course's surface profile: {"v", "segs": [{"a", "b", "c"}], "km", "ways", "at"}.
    SurfaceError when there is no server, it fails or another lookup holds it for WAIT_S."""
    url = url or server_url()
    if not url:
        raise SurfaceError("off", "no server")
    xs, la, lo = resample_track(track)
    if not _one_at_a_time.acquire(timeout=WAIT_S):
        raise SurfaceError("busy", "busy")
    try:
        ways = fetch_ways(list(zip(la.tolist(), lo.tolist())), url, post)
    finally:
        _one_at_a_time.release()
    labels = label_points(la, lo, ways)
    return {"v": VERSION, "segs": run_segments(xs.tolist(), labels), "km": round(float(xs[-1]) / 1000.0, 3),
            "ways": len(ways), "at": dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")}


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
        return _("這台沒有設定 OSM 路面查詢（{env}）", env=URL_ENV)
    if code == "busy":
        return _("路面查詢忙碌中，稍後再試")
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
