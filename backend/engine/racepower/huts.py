"""
Taiwan's mountain huts and camps from OpenStreetMap along a course (SP-536, research SP-522).

The list is backend/data/osm_huts_tw/huts.json (format "trc-osm-huts/1", ODbL 1.0, built by
backend/scripts/build_osm_huts.py — see its README). It is public data kept apart from the users'
data and the coaching logic. A hut or camp within NEAR_M of the track becomes a day-end CANDIDATE of
a multi-day trip, one per time the track goes by (an out-and-back passes a hut twice), exactly like
a GPX's own camp waypoint (course.build_course `wpts`, camp = True) but marked src = "osm" so the
pages can say where it comes from and show "© OpenStreetMap contributors". Never a day end by itself.

Cost: the track's bounding box (+ NEAR_M) picks the few huts that can be near it; only those are
measured against the track points (numpy). The list is read once per process.
"""
from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

DATA = Path(__file__).resolve().parents[2] / "data" / "osm_huts_tw" / "huts.json"
FORMAT = "trc-osm-huts/1"
NEAR_M = 150.0            # SP-522: 5 of the owner's 6 nights had an OSM hut / camp within 150 m
SEP_M = 300.0             # two passes closer than this along the track are one
GPX_SAME_KM = 0.3         # a GPX camp waypoint this close along the track is the same place: the GPX's name wins

_lock = threading.Lock()
_cache: dict = {}


def load(path: Optional[Path] = None) -> dict:
    """The list document ({format, attribution, license, huts: [...]}); an empty list when the file is
    missing or not this format (the candidates are a convenience: nothing breaks without them)."""
    p = Path(path or DATA)
    with _lock:
        if p in _cache:
            return _cache[p]
        try:
            doc = json.loads(p.read_text("utf-8"))
            if doc.get("format") != FORMAT or not isinstance(doc.get("huts"), list):
                raise ValueError("format")
        except (OSError, ValueError):
            doc = {"format": FORMAT, "huts": []}
        _cache[p] = doc
        return doc


def _arrays(doc: dict) -> tuple[np.ndarray, np.ndarray]:
    key = ("arr", id(doc))
    got = _cache.get(key)
    if got is None or got[0] is not doc:          # keeps the doc: an id is only unique while it lives
        hs = doc["huts"]
        got = (doc, np.array([h["lat"] for h in hs], float), np.array([h["lon"] for h in hs], float))
        _cache[key] = got
    return got[1], got[2]


def along(lat: np.ndarray, lon: np.ndarray, d: np.ndarray, *, near_m: float = NEAR_M,
          doc: Optional[dict] = None) -> list[dict]:
    """The huts / camps within `near_m` of the track (points lat / lon, distance along it d in m), one
    row per pass: {name, km, camp: True, src: "osm", kind, osm, ele_m, pass, passes, lat, lon}."""
    from backend.engine.racepower import course as CO
    doc = doc if doc is not None else load()
    if not doc["huts"] or len(lat) < 2:
        return []
    hlat, hlon = _arrays(doc)
    pad_lat = near_m / 111_000.0
    pad_lon = near_m / (111_000.0 * max(0.2, math.cos(math.radians(float(np.mean(lat))))))
    box = ((hlat >= lat.min() - pad_lat) & (hlat <= lat.max() + pad_lat)
           & (hlon >= lon.min() - pad_lon) & (hlon <= lon.max() + pad_lon))
    out = []
    for k in np.flatnonzero(box):
        h = doc["huts"][int(k)]
        dd = CO.haversine(lat, lon, h["lat"], h["lon"])
        near = np.flatnonzero(dd <= near_m)
        if not len(near):
            continue
        runs = np.split(near, np.flatnonzero(np.diff(near) > 1) + 1)
        js: list[int] = []
        for r in runs:
            j = int(r[np.argmin(dd[r])])
            if js and d[j] - d[js[-1]] < SEP_M:
                if dd[j] < dd[js[-1]]:
                    js[-1] = j
                continue
            js.append(j)
        for n, j in enumerate(js, 1):
            out.append({"name": h.get("name") or "", **({"name_en": h["name_en"]} if h.get("name_en") else {}),
                        "km": float(d[j]) / 1000.0, "camp": True, "src": "osm",
                        "kind": h["kind"], "osm": h["osm"], "ele_m": h.get("ele_m"), "pass": n, "passes": len(js),
                        "lat": h["lat"], "lon": h["lon"]})
    return out


def merge(wpts: Sequence[dict], osm: Sequence[dict]) -> list[dict]:
    """The GPX's own waypoints + the OSM passes not already there: an OSM pass within GPX_SAME_KM of a
    GPX camp waypoint's pass is that camp (the user's file names it). Sorted by km."""
    camps = [w["km"] for w in wpts if w.get("camp")]
    keep = [o for o in osm if not any(abs(o["km"] - k) < GPX_SAME_KM for k in camps)]
    return sorted([*wpts, *keep], key=lambda w: w["km"])


def attribution(doc: Optional[dict] = None) -> dict:
    doc = doc if doc is not None else load()
    return {"text": doc.get("attribution") or "© OpenStreetMap contributors",
            "url": doc.get("license_url") or "https://www.openstreetmap.org/copyright",
            "license": doc.get("license") or "ODbL-1.0"}
