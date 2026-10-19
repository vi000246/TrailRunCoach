"""The trail dictionary's formats: tiles, records, manifest, retention (SP-709).

Standard library only, no OSM reader here, so the tests can run it on synthetic ways.
build.py reads the OSM extract and feeds ways in; publish.py uploads what this writes.

One build, two packagings of the same records:

* ``tiles/<z>/<x>/<y>.json.gz`` -- slippy-map tiles (default z13, about 4.5 km wide in Taiwan).
  Each holds the pieces of ways that cross it, so a reader (the inReachLiveTrack Worker) fetches
  only the tiles a route passes. A long way is cut at vertices: a piece keeps every segment that
  touches the tile, so pieces of neighbouring tiles overlap by one segment and leave no gap.
* ``trail-dict.ndjson.gz`` -- every way once, whole, one JSON object per line (the NAS download).
* ``manifest.json`` -- version, source md5 and date, build time, rules version, the tile list.

A record (tile piece or bundle line)::

    {"id": 123, "line": "trail", "surface": "dirt", "risk": ["closed"],
     "routes": [{"id": 9, "name": "...", "network": "...", "ref": "..."}],   # only when a member
     "tags": {"highway": "path", ...},                                     # the KEPT_TAGS present
     "c": [lon0, lat0, dlon1, dlat1, ...]}                                 # ints, x COORD_SCALE, deltas
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from collections import defaultdict
from typing import Iterable, Iterator, Sequence

FORMAT_VERSION = 1
DEFAULT_ZOOM = 13
COORD_SCALE = 100_000            # 1e-5 degree, about 1.1 m
BUCKET_SHIFT = 5                 # tiles are written bucket by bucket (z13 >> 5 = z8) to cap memory
BUNDLE_NAME = "trail-dict.ndjson.gz"
MANIFEST_NAME = "manifest.json"
LATEST_NAME = "latest.json"
VERSIONS_DIR = "v/"
KEEP_VERSIONS = 2
LICENSE = "ODbL 1.0 -- (c) OpenStreetMap contributors, https://www.openstreetmap.org/copyright"

MAX_LAT = 85.05112878


# ---------------------------------------------------------------------------- tile maths

def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Slippy-map tile (x, y) holding the point at zoom z (Web Mercator, as OSM tiles)."""
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) of a tile in degrees."""
    n = 1 << z

    def lat_of(yy: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360.0 - 180.0, lat_of(y + 1), (x + 1) / n * 360.0 - 180.0, lat_of(y)


def segment_tiles(a: tuple[float, float], b: tuple[float, float], z: int) -> Iterator[tuple[int, int]]:
    """Tiles the segment a-b may touch: every tile of its bounding box (a superset, never misses)."""
    xa, ya = lonlat_to_tile(a[0], a[1], z)
    xb, yb = lonlat_to_tile(b[0], b[1], z)
    for x in range(min(xa, xb), max(xa, xb) + 1):
        for y in range(min(ya, yb), max(ya, yb) + 1):
            yield x, y


def split_by_tile(coords: Sequence[tuple[float, float]], z: int) -> dict[tuple[int, int], list[list]]:
    """{tile: [piece, ...]}: each piece is the run of consecutive segments touching that tile."""
    runs: dict[tuple[int, int], list[list[int]]] = defaultdict(list)   # tile -> [[first seg, last seg]]
    for i in range(len(coords) - 1):
        for t in segment_tiles(coords[i], coords[i + 1], z):
            r = runs[t]
            if r and r[-1][1] == i - 1:
                r[-1][1] = i
            else:
                r.append([i, i])
    return {t: [list(coords[a:b + 2]) for a, b in r] for t, r in runs.items()}


# ---------------------------------------------------------------------------- records

def encode_coords(coords: Iterable[tuple[float, float]]) -> list[int]:
    out: list[int] = []
    px = py = 0
    for lon, lat in coords:
        x, y = round(lon * COORD_SCALE), round(lat * COORD_SCALE)
        out += (x - px, y - py)
        px, py = x, y
    return out


def decode_coords(c: Sequence[int]) -> list[tuple[float, float]]:
    out, x, y = [], 0, 0
    for i in range(0, len(c) - 1, 2):
        x += c[i]
        y += c[i + 1]
        out.append((x / COORD_SCALE, y / COORD_SCALE))
    return out


def make_record(way_id: int, cls: dict, tags: dict, coords: Sequence[tuple[float, float]],
                routes: Sequence[dict] | None = None) -> dict:
    rec = {"id": way_id, "line": cls["line"], "surface": cls["surface"], "risk": list(cls["risk"])}
    if routes:
        rec["routes"] = list(routes)
    rec["tags"] = dict(tags)
    rec["c"] = encode_coords(coords)
    return rec


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------- the writer

class DictWriter:
    """Streams ways into the bundle and the tile buckets, then writes tiles and the manifest.

    Memory stays bounded: tile pieces go to one temp file per bucket (a z8 parent tile) and each
    bucket is grouped into tiles on its own at the end.
    """

    def __init__(self, out_dir: str, zoom: int = DEFAULT_ZOOM):
        self.out_dir = out_dir
        self.zoom = zoom
        self.tmp_dir = os.path.join(out_dir, "_buckets")
        os.makedirs(self.tmp_dir, exist_ok=True)
        os.makedirs(os.path.join(out_dir, "tiles"), exist_ok=True)
        self._bundle = gzip.open(os.path.join(out_dir, BUNDLE_NAME), "wt", encoding="utf-8",
                                 compresslevel=9)
        self._buckets: dict[tuple[int, int], object] = {}
        self.counts: dict[str, dict[str, int]] = {"line": defaultdict(int), "surface": defaultdict(int),
                                                  "risk": defaultdict(int)}
        self.ways = 0
        self.pieces = 0
        self.route_members = 0
        self.length_km: dict[str, float] = defaultdict(float)

    def _bucket(self, tx: int, ty: int):
        key = (tx >> BUCKET_SHIFT, ty >> BUCKET_SHIFT)
        f = self._buckets.get(key)
        if f is None:
            f = open(os.path.join(self.tmp_dir, f"{key[0]}_{key[1]}.ndjson"), "w", encoding="utf-8",
                     newline="\n")
            self._buckets[key] = f
        return f

    def add(self, way_id: int, cls: dict, tags: dict, coords: Sequence[tuple[float, float]],
            routes: Sequence[dict] | None = None) -> None:
        if len(coords) < 2:
            return
        self.ways += 1
        self.counts["line"][cls["line"]] += 1
        self.counts["surface"][cls["surface"]] += 1
        for r in cls["risk"]:
            self.counts["risk"][r] += 1
        if routes:
            self.route_members += 1
        self.length_km[cls["line"]] += polyline_km(coords)
        self._bundle.write(dumps(make_record(way_id, cls, tags, coords, routes)) + "\n")
        for (tx, ty), pieces in split_by_tile(coords, self.zoom).items():
            f = self._bucket(tx, ty)
            for p in pieces:
                self.pieces += 1
                f.write(f"{tx} {ty} " + dumps(make_record(way_id, cls, tags, p, routes)) + "\n")

    def finish(self) -> list[dict]:
        """Write the tile files; returns the tile list [{"x", "y", "bytes", "ways"}] (sorted)."""
        self._bundle.close()
        for f in self._buckets.values():
            f.close()
        tiles = []
        for name in sorted(os.listdir(self.tmp_dir)):
            path = os.path.join(self.tmp_dir, name)
            groups: dict[tuple[int, int], list[str]] = defaultdict(list)
            with open(path, encoding="utf-8") as f:
                for line in f:
                    tx, ty, rec = line.rstrip("\n").split(" ", 2)
                    groups[(int(tx), int(ty))].append(rec)
            for (tx, ty), recs in groups.items():
                tiles.append(write_tile(self.out_dir, self.zoom, tx, ty, recs))
            os.remove(path)
        os.rmdir(self.tmp_dir)
        tiles.sort(key=lambda t: (t["x"], t["y"]))
        return tiles


def tile_relpath(z: int, x: int, y: int) -> str:
    return f"tiles/{z}/{x}/{y}.json.gz"


def write_tile(out_dir: str, z: int, x: int, y: int, recs: Sequence[str]) -> dict:
    rel = tile_relpath(z, x, y)
    path = os.path.join(out_dir, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w, s, e, n = tile_bounds(x, y, z)
    head = dumps({"format": FORMAT_VERSION, "z": z, "x": x, "y": y,
                  "bbox": [round(w, 6), round(s, 6), round(e, 6), round(n, 6)],
                  "coord_scale": COORD_SCALE})
    body = head[:-1] + ',"ways":[' + ",".join(recs) + "]}"
    data = gzip.compress(body.encode("utf-8"), compresslevel=9, mtime=0)
    with open(path, "wb") as f:
        f.write(data)
    return {"x": x, "y": y, "bytes": len(data), "ways": len(recs)}


def read_tile(path: str) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def polyline_km(coords: Sequence[tuple[float, float]]) -> float:
    km = 0.0
    for (lo1, la1), (lo2, la2) in zip(coords, coords[1:]):
        p1, p2 = math.radians(la1), math.radians(la2)
        dp, dl = p2 - p1, math.radians(lo2 - lo1)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        km += 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(h)))
    return km


# ---------------------------------------------------------------------------- manifest

def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def version_id(built_at_utc, source_md5: str) -> str:
    """Sortable version name: build time (UTC, to the minute) + the source md5's first 8 chars."""
    return f"{built_at_utc:%Y%m%d-%H%M}-{source_md5[:8]}"


def make_manifest(*, version: str, built_at: str, source: dict, rules_version: int, zoom: int,
                  tiles: Sequence[dict], bundle: dict, stats: dict, build_seconds: float | None = None) -> dict:
    return {
        "format": FORMAT_VERSION,
        "version": version,
        "built_at": built_at,
        "build_seconds": build_seconds,
        "source": source,
        "rules_version": rules_version,
        "zoom": zoom,
        "coord_scale": COORD_SCALE,
        "license": LICENSE,
        "bundle": bundle,
        "tile_count": len(tiles),
        "tile_bytes": sum(t["bytes"] for t in tiles),
        "tile_path": "tiles/{z}/{x}/{y}.json.gz",
        "tiles": [[t["x"], t["y"]] for t in tiles],
        "stats": stats,
    }


def expected_files(manifest: dict) -> list[str]:
    """Every file a complete version holds (relative paths), manifest last."""
    z = manifest["zoom"]
    files = [tile_relpath(z, x, y) for x, y in manifest["tiles"]]
    files.append(manifest["bundle"]["path"])
    files.append(MANIFEST_NAME)
    return files


def latest_pointer(manifest: dict, prefix: str) -> dict:
    v = manifest["version"]
    return {"version": v, "base": f"{prefix}{v}/", "manifest": f"{prefix}{v}/{MANIFEST_NAME}",
            "built_at": manifest["built_at"], "rules_version": manifest["rules_version"],
            "source_md5": manifest["source"].get("md5")}


# ---------------------------------------------------------------------------- retention

def versions_to_delete(versions: Iterable[str], latest: str | None, keep: int = KEEP_VERSIONS) -> list[str]:
    """Old versions to remove: keep the `keep` newest, and never the one `latest` points to."""
    vs = sorted(set(versions), reverse=True)
    kept = set(vs[:keep])
    if latest:
        kept.add(latest)
    return sorted(v for v in vs if v not in kept)
