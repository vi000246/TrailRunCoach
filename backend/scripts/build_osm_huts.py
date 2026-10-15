"""
Build backend/data/osm_huts_tw/huts.json — Taiwan's mountain huts and camps from OpenStreetMap
(SP-536, research SP-522). The race calculator offers the ones within 150 m of an uploaded GPX as
day-end candidates (backend/engine/racepower/huts.py). Format, licence (ODbL 1.0) and the filter:
backend/data/osm_huts_tw/README.md.

    1. Overpass: every node / way / relation tagged tourism = alpine_hut | wilderness_hut | camp_site
       inside BBOX (Taiwan's main island; a bbox, not an area: the self-hosted Overpass runs without
       areas). Ways and relations come with their centre point.
    2. Height: the element's own `ele` tag when it is a number, else the ground height from an
       elevation API (Open-Meteo, Copernicus GLO-90 DEM; ≤ 100 points a call).
    3. Filter: alpine_hut and wilderness_hut are all kept; a camp_site only from CAMP_MIN_ELE_M up
       (most of the 700-odd OSM camp sites in Taiwan are car / glamping camps on the plains and in the
       foothills — see the README for how the threshold was chosen).
    4. Write the list (sorted by OSM id, so a rebuild diffs cleanly) with the attribution, the licence
       and how it was made.

Usage (from the repo root; nothing is read from or written to the app's data folder):
    python -m backend.scripts.build_osm_huts                            # Overpass at TRC_OVERPASS_URL / the public one
    python -m backend.scripts.build_osm_huts --url https://overpass.example/api/interpreter
    python -m backend.scripts.build_osm_huts --from-file overpass.json  # a saved Overpass JSON answer
    python -m backend.scripts.build_osm_huts --dry-run                  # print the counts, write nothing

Environment (read here only, never written anywhere):
    TRC_OVERPASS_URL          the Overpass interpreter (default: PUBLIC_OVERPASS). The owner's self-hosted
                              Taiwan Overpass sits behind Cloudflare Access: give it a service token with
    TRC_OVERPASS_CF_CLIENT_ID / TRC_OVERPASS_CF_CLIENT_SECRET   (sent as CF-Access-Client-Id / -Secret)
    TRC_ELEVATION_URL         the elevation API (default: OPEN_METEO_ELEVATION)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional

OUT = Path(__file__).resolve().parents[1] / "data" / "osm_huts_tw" / "huts.json"
FORMAT = "trc-osm-huts/1"
PUBLIC_OVERPASS = "https://overpass-api.de/api/interpreter"
OPEN_METEO_ELEVATION = "https://api.open-meteo.com/v1/elevation"
BBOX = (21.85, 119.95, 25.35, 122.05)      # south, west, north, east: Taiwan's main island
KINDS = ("alpine_hut", "wilderness_hut", "camp_site")
CAMP_MIN_ELE_M = 1500                       # a camp_site lower than this is left out (README: how chosen)
ELEV_BATCH = 100                            # Open-Meteo: ≤ 100 coordinates a call
ATTRIBUTION = "© OpenStreetMap contributors"
LICENSE = "ODbL-1.0"
LICENSE_URL = "https://www.openstreetmap.org/copyright"
UA = "TrailRunCoach build_osm_huts (SP-536)"


def overpass_query(bbox=BBOX, kinds=KINDS, timeout_s: int = 180) -> str:
    s, w, n, e = bbox
    return (f"[out:json][timeout:{timeout_s}];\n"
            f'nwr["tourism"~"^({"|".join(kinds)})$"]({s},{w},{n},{e});\n'
            "out center tags;")


def _num_ele(v) -> Optional[float]:
    """The OSM `ele` tag as metres: "2850", "2,850 m", "2850.5" → a number; anything else → None."""
    if v is None:
        return None
    m = re.match(r"^\s*(-?\d[\d,]*(?:\.\d+)?)\s*(m|公尺)?\s*$", str(v))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _point(el: dict) -> Optional[tuple[float, float]]:
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    c = el.get("center") or {}
    if "lat" in c and "lon" in c:
        return float(c["lat"]), float(c["lon"])
    return None


def _name(tags: dict) -> Optional[str]:
    for k in ("name", "name:zh-Hant", "name:zh", "name:en"):
        v = (tags.get(k) or "").strip()
        if v:
            return v[:80]
    return None


def candidates(elements: Iterable[dict], bbox=BBOX, kinds=KINDS) -> list[dict]:
    """The Overpass elements of the wanted kinds inside the bbox, as rows without a height yet."""
    s, w, n, e = bbox
    out = []
    seen = set()
    for el in elements:
        tags = el.get("tags") or {}
        kind = tags.get("tourism")
        if kind not in kinds or el.get("type") not in ("node", "way", "relation"):
            continue
        p = _point(el)
        if p is None or not (s <= p[0] <= n and w <= p[1] <= e):
            continue
        oid = f"{el['type']}/{int(el['id'])}"
        if oid in seen:
            continue
        seen.add(oid)
        row = {"osm": oid, "kind": kind, "name": _name(tags), "lat": round(p[0], 5), "lon": round(p[1], 5),
               "ele_tag": _num_ele(tags.get("ele"))}
        en = (tags.get("name:en") or "").strip()
        if en and en != row["name"]:
            row["name_en"] = en[:80]
        out.append(row)
    return out


def build(elements: Iterable[dict], elevations: Callable[[list[tuple[float, float]]], list[Optional[float]]],
          *, osm_timestamp: Optional[str] = None, source: str = "", now: Optional[dt.datetime] = None,
          camp_min_ele_m: float = CAMP_MIN_ELE_M) -> dict:
    """Overpass elements → the huts.json document. `elevations(points)` gives the ground height of the
    points with no usable `ele` tag (None = unknown: such a camp_site is left out, a hut kept)."""
    rows = candidates(elements)
    need = [r for r in rows if r["ele_tag"] is None]
    dem = elevations([(r["lat"], r["lon"]) for r in need]) if need else []
    for r, z in zip(need, dem):
        r["ele_dem"] = z
    huts, dropped = [], {"camp_site_low": 0, "camp_site_no_ele": 0}
    for r in rows:
        tag, z_dem = r.pop("ele_tag"), r.pop("ele_dem", None)
        z = tag if tag is not None else z_dem
        if r["kind"] == "camp_site":
            if z is None:
                dropped["camp_site_no_ele"] += 1
                continue
            if z < camp_min_ele_m:
                dropped["camp_site_low"] += 1
                continue
        r["ele_m"] = None if z is None else int(round(z))
        r["ele_src"] = None if z is None else ("osm" if tag is not None else "dem")
        huts.append(r)
    huts.sort(key=lambda r: (r["osm"].split("/")[0], int(r["osm"].split("/")[1])))
    count = {k: sum(1 for h in huts if h["kind"] == k) for k in KINDS}
    return {
        "format": FORMAT,
        "attribution": ATTRIBUTION,
        "license": LICENSE,
        "license_url": LICENSE_URL,
        "source": "OpenStreetMap via the Overpass API" + (f" ({source})" if source else ""),
        "osm_timestamp": osm_timestamp,
        "generated": (now or dt.datetime.now(dt.timezone.utc)).replace(microsecond=0).isoformat(),
        "query": {"bbox": list(BBOX), "tourism": list(KINDS)},
        "filter": {"camp_site_min_ele_m": camp_min_ele_m, "ele": "OSM ele tag, else the Copernicus GLO-90 DEM "
                   "(Open-Meteo elevation API)"},
        "count": count,
        "dropped": dropped,
        "huts": huts,
    }


# ---- fetching (httpx; only from this script, never by the app) ---------------------------------

def fetch_overpass(url: str, query: str, headers: Optional[dict] = None, timeout: float = 240.0) -> dict:
    import httpx
    h = {"User-Agent": UA, **(headers or {})}
    r = httpx.post(url, data={"data": query}, headers=h, timeout=timeout, follow_redirects=False)
    if r.status_code != 200:
        raise RuntimeError(f"Overpass HTTP {r.status_code}: {r.text[:200]}")
    try:
        js = r.json()
    except ValueError:
        raise RuntimeError(f"Overpass did not answer JSON: {r.text[:200]}")
    if "elements" not in js:
        raise RuntimeError("Overpass answer has no elements")
    return js


def fetch_elevations(url: str, points: list[tuple[float, float]], timeout: float = 30.0,
                     wait_s: float = 65.0, tries: int = 4) -> list[Optional[float]]:
    """Ground heights, ELEV_BATCH points a call. Open-Meteo counts each point as a call (600 a
    minute on the free tier): on HTTP 429 wait a minute and ask again."""
    import time

    import httpx
    out: list[Optional[float]] = []
    for i in range(0, len(points), ELEV_BATCH):
        part = points[i:i + ELEV_BATCH]
        for _ in range(tries):
            r = httpx.get(url, params={"latitude": ",".join(f"{p[0]:.5f}" for p in part),
                                       "longitude": ",".join(f"{p[1]:.5f}" for p in part)},
                          headers={"User-Agent": UA}, timeout=timeout)
            if r.status_code != 429:
                break
            print(f"elevation API: rate limited, waiting {wait_s:.0f} s", file=sys.stderr)
            time.sleep(wait_s)
        if r.status_code != 200:
            raise RuntimeError(f"elevation HTTP {r.status_code}: {r.text[:200]}")
        got = r.json().get("elevation") or []
        for j in range(len(part)):
            z = got[j] if j < len(got) else None
            out.append(float(z) if isinstance(z, (int, float)) and z > -1000 else None)
    return out


def _cf_headers() -> dict:
    cid = (os.environ.get("TRC_OVERPASS_CF_CLIENT_ID") or "").strip()
    sec = (os.environ.get("TRC_OVERPASS_CF_CLIENT_SECRET") or "").strip()
    return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": sec} if cid and sec else {}


def dumps(doc: dict) -> str:
    """The document as JSON with one hut per line (a rebuild diffs line by line)."""
    head = json.dumps({**doc, "huts": "@HUTS@"}, ensure_ascii=False, indent=1)
    rows = ",\n".join("  " + json.dumps(h, ensure_ascii=False, separators=(", ", ": ")) for h in doc["huts"])
    return head.replace('"@HUTS@"', "[\n" + rows + "\n ]") + "\n"


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("TRC_OVERPASS_URL") or PUBLIC_OVERPASS,
                    help="Overpass interpreter URL (env TRC_OVERPASS_URL)")
    ap.add_argument("--from-file", type=Path, help="a saved Overpass JSON answer instead of a query")
    ap.add_argument("--elevation-url", default=os.environ.get("TRC_ELEVATION_URL") or OPEN_METEO_ELEVATION)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--dry-run", action="store_true", help="print the counts, write nothing")
    a = ap.parse_args(argv)
    if a.from_file:
        js = json.loads(a.from_file.read_text("utf-8"))
        src = "a saved answer"
    else:
        js = fetch_overpass(a.url, overpass_query(), _cf_headers())
        src = re.sub(r"^https?://([^/]+).*$", r"\1", a.url)
    ts = (js.get("osm3s") or {}).get("timestamp_osm_base")
    doc = build(js.get("elements") or [], lambda pts: fetch_elevations(a.elevation_url, pts),
                osm_timestamp=ts, source=src)
    print(f"{doc['count']} kept, {doc['dropped']} left out (OSM data of {ts})")
    if a.dry_run:
        return
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp")
    tmp.write_text(dumps(doc), "utf-8")
    os.replace(tmp, a.out)
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main(sys.argv[1:])
