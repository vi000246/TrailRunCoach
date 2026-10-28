"""
The trail dictionary on this server (SP-727; the dictionary itself: SP-709, tools/trail-dict/).

The race calculator's ground (racepower/surface.py, SP-543) and the per-run surface evidence of the
trail HR model (athlete.kind_rows) read the OSM ways here instead of asking an Overpass server: the
NAS downloads the dictionary's bundle once per version and keeps a local index of it.

  $TRC_TRAIL_DICT_URL           where the dictionary is published (the R2 domain, e.g.
                                https://trail-dict.example/); unset → no dictionary: the calculator
                                says 路面未知 and the HR model has no surface evidence, as before
  <server root>/trail-dict/     DERIVED, shared by every tenant (public OSM data, no user data):
      <version>.sqlite          the index of one version (FORMAT below)
      current.json              {"version", "rules_version", "built_at", "source_md5", "ways", "format",
                                 "indexed_at", "checked_at"}: which index is in use, when latest.json
                                was last looked at

Refresh (`refresh`): latest.json → when its version is not the one in use, its manifest → the bundle
(trail-dict.ndjson.gz, checked against the manifest's sha256 and size) → a new index, built next to
the old one and switched in by a rename; the old index is removed afterwards. Any failure keeps the
index in use. The scheduler looks once a day (`tick`, queued at `cold`: building the index reads
~820,000 ways); a server without an index looks at its first tick.

The index: table ways (id, line, surface, kind, sac, c = the record's delta-coded coordinates as
packed int32, zlib) and table cells (cell, way) — every 0.01° cell (about 1.1 km × 1.0 km in
Taiwan) a way's segments touch. `Index.ways_near` takes the cells around a set of points.

`kind` is the speed class of the way (SP-727, `speed_kind`): what the calculator's segment split
multiplies by. Changing that rule bumps FORMAT, and a server with an index of another FORMAT builds
it again from the same version.

Data © OpenStreetMap contributors, ODbL (racepower/surface.ATTRIBUTION).
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import zlib
from array import array
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

log = logging.getLogger(__name__)

URL_ENV = "TRC_TRAIL_DICT_URL"
DIRNAME = "trail-dict"
CURRENT = "current.json"
FORMAT = 1                       # the index's schema and speed_kind rule; a change rebuilds the index
CELL_DEG = 0.01                  # index cell (推估: ~1 km, a 20 km course touches a few dozen)
COORD_SCALE = 100_000            # the dictionary's coord_scale (tools/trail-dict/trail_dict.py)
CHECK_EVERY_S = 24 * 3600        # look at latest.json once a day (the dictionary is rebuilt monthly)
TIMEOUT_S = 60.0
MAX_JSON_BYTES = 5_000_000       # latest.json / manifest.json (the manifest lists ~2,000 tiles)
MAX_BUNDLE_BYTES = 300_000_000   # the bundle is ~28 MB (SP-709 trial build)
USER_AGENT = "TrailRunCoach (trail dictionary, SP-727)"
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$")
_REL = re.compile(r"^[0-9A-Za-z._/-]{1,200}$")

# the speed classes (SP-727): what the segment split's surface multipliers are fitted on
KINDS = ("road", "forest_road", "steps", "trail", "trail_t3")
SAC_T3PLUS = frozenset({"demanding_mountain_hiking", "alpine_hiking", "demanding_alpine_hiking",
                        "difficult_alpine_hiking"})


class TrailDictError(Exception):
    pass


# ---------------------------------------------------------------------------- the speed class

def speed_kind(line: str, surface: str, tags: Optional[dict]) -> Optional[str]:
    """The way's speed class (KINDS) from the dictionary's classes (trailclass rules) and kept tags:

      steps        石階 (highway=steps)
      road         a road, or any way with a laid surface (a paved park path runs like a road)
      forest_road  林道 (highway=track, not paved)
      trail        a path / footway / bridleway, sac_scale T1–T2 or none
      trail_t3     the same with sac_scale T3 or above, or Rudy's 艱難路線 (poor visibility, T5+)
      None         anything else (other, a corridor …): not fitted, the multiplier stays 1

    A closed / abandoned way (line "closed") runs like what it was: its highway decides."""
    from backend.engine import trailclass as TC
    tags = tags or {}
    if line == "closed":
        hw = TC.effective_highway(tags)
        if hw in TC.PATH_HIGHWAYS:
            line = "hard" if (tags.get("trail_visibility") in TC.POOR_VISIBILITY
                              or tags.get("sac_scale") in TC.SAC_T5PLUS) else "trail"
        elif hw == "steps":
            line = "steps"
        elif hw == "track":
            line = "forest_road"
        elif hw in TC.ROAD_HIGHWAYS or hw in TC.TOWN_PATH_HIGHWAYS:
            line = "road"
        else:
            return None
    if line == "steps":
        return "steps"
    if line == "road" or surface == "paved":
        return "road"
    if line == "forest_road":
        return "forest_road"
    if line in ("trail", "hard"):
        return "trail_t3" if line == "hard" or (tags.get("sac_scale") or "") in SAC_T3PLUS else "trail"
    return None


# ---------------------------------------------------------------------------- where

def base_url() -> Optional[str]:
    """$TRC_TRAIL_DICT_URL with a trailing slash, None when unset or not http(s)."""
    u = (os.environ.get(URL_ENV) or "").strip()
    if not u or not u.lower().startswith(("http://", "https://")):
        return None
    return u if u.endswith("/") else u + "/"


def _default_root() -> Optional[Path]:
    """<server root>/trail-dict (tests patch this: conftest)."""
    try:
        from backend import accounts
        return accounts.server_root() / DIRNAME
    except Exception:                       # noqa: BLE001
        return None


def _root(root=None) -> Optional[Path]:
    return Path(root) if root is not None else _default_root()


def _read_json(path: Path) -> Optional[dict]:
    try:
        v = json.loads(path.read_text("utf-8"))
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def status(root=None) -> Optional[dict]:
    """current.json (what is in use), None without an index."""
    r = _root(root)
    if r is None:
        return None
    cur = _read_json(r / CURRENT)
    if not cur or not cur.get("version") or not (r / f"{cur['version']}.sqlite").exists():
        return None
    return cur


# ---------------------------------------------------------------------------- building the index

def _cell(lon: float, lat: float) -> int:
    return math.floor(lon / CELL_DEG) * 100_000 + math.floor(lat / CELL_DEG)


def decode(c: Sequence[int]) -> list[tuple[float, float]]:
    """The dictionary's delta-coded coordinates → [(lon, lat)] (trail_dict.decode_coords)."""
    out, x, y = [], 0, 0
    for i in range(0, len(c) - 1, 2):
        x += c[i]
        y += c[i + 1]
        out.append((x / COORD_SCALE, y / COORD_SCALE))
    return out


def way_cells(coords: Sequence[tuple[float, float]]) -> set[int]:
    """Every index cell the bounding box of each segment touches (a superset: never misses one)."""
    out: set[int] = set()
    for (lo1, la1), (lo2, la2) in zip(coords, coords[1:]):
        for cx in range(math.floor(min(lo1, lo2) / CELL_DEG), math.floor(max(lo1, lo2) / CELL_DEG) + 1):
            for cy in range(math.floor(min(la1, la2) / CELL_DEG), math.floor(max(la1, la2) / CELL_DEG) + 1):
                out.add(cx * 100_000 + cy)
    return out


def _pack(c: Sequence[int]) -> bytes:
    return zlib.compress(array("i", c).tobytes(), 6)


def _unpack(b: bytes) -> list[int]:
    a = array("i")
    a.frombytes(zlib.decompress(b))
    return a.tolist()


SCHEMA = ("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)",
          "CREATE TABLE ways (id INTEGER PRIMARY KEY, line TEXT, surface TEXT, kind TEXT, sac TEXT, c BLOB)",
          "CREATE TABLE cells (cell INTEGER NOT NULL, way INTEGER NOT NULL)")


def build_index(records: Iterable[dict], out_path, meta: dict, batch: int = 5000) -> int:
    """Write the index of `records` (bundle lines, parsed) to `out_path` (replaced); returns the
    number of ways. `meta` goes into table meta (version, rules_version, …) with the FORMAT."""
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(str(out_path))
    n = 0
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        for s in SCHEMA:
            con.execute(s)
        wrows, crows = [], []
        for rec in records:
            c = rec.get("c") or []
            if len(c) < 4:
                continue
            coords = decode(c)
            tags = rec.get("tags") or {}
            line, surface = rec.get("line") or "other", rec.get("surface") or "unknown"
            wid = int(rec["id"])
            wrows.append((wid, line, surface, speed_kind(line, surface, tags), tags.get("sac_scale"), _pack(c)))
            crows.extend((cell, wid) for cell in way_cells(coords))
            n += 1
            if len(wrows) >= batch:
                con.executemany("INSERT OR REPLACE INTO ways VALUES (?,?,?,?,?,?)", wrows)
                con.executemany("INSERT INTO cells VALUES (?,?)", crows)
                wrows, crows = [], []
        if wrows:
            con.executemany("INSERT OR REPLACE INTO ways VALUES (?,?,?,?,?,?)", wrows)
            con.executemany("INSERT INTO cells VALUES (?,?)", crows)
        con.execute("CREATE INDEX cells_cell ON cells (cell)")
        m = {**{k: v for k, v in meta.items() if v is not None}, "format": FORMAT, "ways": n}
        con.executemany("INSERT INTO meta VALUES (?,?)", [(k, json.dumps(v)) for k, v in m.items()])
        con.commit()
    finally:
        con.close()
    return n


def bundle_records(path) -> Iterable[dict]:
    """The bundle's lines (trail-dict.ndjson.gz), one way each."""
    with gzip.open(str(path), "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


# ---------------------------------------------------------------------------- reading the index

class Index:
    """One version's index, opened read-only."""

    def __init__(self, path, meta: Optional[dict] = None):
        self.path = Path(path)
        self._local = threading.local()
        m = meta if meta is not None else self._meta()
        self.version = m.get("version")
        self.rules_version = m.get("rules_version")
        self.format = m.get("format")
        self.ways = m.get("ways")

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            self._local.con = con
        return con

    def _meta(self) -> dict:
        try:
            return {k: json.loads(v) for k, v in self._con().execute("SELECT k, v FROM meta")}
        except sqlite3.Error:
            return {}

    def ways_near(self, lat: Sequence[float], lon: Sequence[float], radius_m: float) -> list[dict]:
        """The ways with a cell within `radius_m` of any of the points: [{"id", "line", "class" (the
        dictionary's surface class), "kind", "sac", "geometry": [{"lat", "lon"}]}] (the Overpass shape
        surface.label_points reads)."""
        cells: set[int] = set()
        for la, lo in zip(lat, lon):
            dlat = radius_m / 111_320.0
            dlon = radius_m / (111_320.0 * max(0.1, math.cos(math.radians(la))))
            for cx in range(math.floor((lo - dlon) / CELL_DEG), math.floor((lo + dlon) / CELL_DEG) + 1):
                for cy in range(math.floor((la - dlat) / CELL_DEG), math.floor((la + dlat) / CELL_DEG) + 1):
                    cells.add(cx * 100_000 + cy)
        con = self._con()
        ids: set[int] = set()
        cl = sorted(cells)
        for i in range(0, len(cl), 500):
            part = cl[i:i + 500]
            q = f"SELECT DISTINCT way FROM cells WHERE cell IN ({','.join('?' * len(part))})"
            ids.update(r[0] for r in con.execute(q, part))
        out = []
        il = sorted(ids)
        for i in range(0, len(il), 500):
            part = il[i:i + 500]
            q = f"SELECT id, line, surface, kind, sac, c FROM ways WHERE id IN ({','.join('?' * len(part))})"
            for wid, line, surface, kind, sac, c in con.execute(q, part):
                g = [{"lat": la_, "lon": lo_} for lo_, la_ in decode(_unpack(c))]
                out.append({"type": "way", "id": wid, "line": line, "class": surface, "kind": kind,
                            "sac": sac, "geometry": g})
        return out


_open: dict = {}
_open_lock = threading.Lock()


def current(root=None) -> Optional[Index]:
    """The index in use (status()), or None (no dictionary on this server yet, or of an old FORMAT)."""
    st = status(root)
    if st is None or st.get("format") != FORMAT:
        return None
    path = _root(root) / f"{st['version']}.sqlite"
    key = str(path)
    with _open_lock:
        idx = _open.get(key)
        if idx is None:
            idx = Index(path, {"version": st["version"], "rules_version": st.get("rules_version"),
                               "format": st.get("format"), "ways": st.get("ways")})
            _open.clear()
            _open[key] = idx
    return idx


# ---------------------------------------------------------------------------- refresh

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):    # noqa: D401 — never follow
        return None


def http_get(url: str, dest=None, max_bytes: int = MAX_JSON_BYTES, timeout: float = TIMEOUT_S) -> bytes:
    """GET `url` (no redirects: a login page is not the dictionary) → its bytes, or streamed into
    `dest` (returns b""). TrailDictError on anything else than a 200 under `max_bytes`."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:            # noqa: S310 — the configured https URL
            if getattr(r, "status", 200) != 200:
                raise TrailDictError(f"HTTP {r.status}")
            got = 0
            if dest is None:
                buf = r.read(max_bytes + 1)
                if len(buf) > max_bytes:
                    raise TrailDictError("too big")
                return buf
            with open(dest, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    got += len(chunk)
                    if got > max_bytes:
                        raise TrailDictError("too big")
                    f.write(chunk)
            return b""
    except urllib.error.HTTPError as e:
        raise TrailDictError(f"HTTP {e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise TrailDictError(type(e).__name__)


def _json(raw: bytes, what: str) -> dict:
    try:
        v = json.loads(raw)
    except ValueError:
        raise TrailDictError(f"{what}: not JSON")
    if not isinstance(v, dict):
        raise TrailDictError(f"{what}: not an object")
    return v


def _sha256(path: Path) -> tuple[str, int]:
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


_refresh_lock = threading.Lock()


def refresh(get: Optional[Callable] = None, root=None, url: Optional[str] = None,
            now: Optional[Callable[[], float]] = None) -> dict:
    """Bring the index up to the published latest version. {"status": off / current / updated /
    failed, "version", "error"}. A failure leaves the index in use as it was."""
    url = url or base_url()
    r = _root(root)
    if not url or r is None:
        return {"status": "off"}
    get = get or http_get
    now = now or time.time
    if not _refresh_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        r.mkdir(parents=True, exist_ok=True)
        cur = _read_json(r / CURRENT) or {}
        try:
            latest = _json(get(url + "latest.json"), "latest.json")
            version = str(latest.get("version") or "")
            base = str(latest.get("base") or "")
            if not _VERSION.match(version) or not _REL.match(base) or ".." in base or not base.endswith("/"):
                raise TrailDictError("latest.json: bad version / base")
            have = status(r)
            if have and have.get("version") == version and have.get("format") == FORMAT:
                _write_json(r / CURRENT, {k: v for k, v in have.items() if k != "error"} | {"checked_at": now()})
                return {"status": "current", "version": version}
            man = _json(get(url + base + "manifest.json"), "manifest.json")
            b = man.get("bundle") or {}
            rel = str(b.get("path") or "")
            if not _REL.match(rel) or ".." in rel or not b.get("sha256"):
                raise TrailDictError("manifest.json: no bundle")
            tmp_bundle = r / f".{version}.ndjson.gz.part"
            tmp_index = r / f".{version}.sqlite.part"
            try:
                get(url + base + rel, dest=tmp_bundle, max_bytes=MAX_BUNDLE_BYTES)
                sha, size = _sha256(tmp_bundle)
                if sha != b["sha256"] or (b.get("bytes") and int(b["bytes"]) != size):
                    raise TrailDictError("bundle: sha256 / size differ from the manifest")
                meta = {"version": version, "rules_version": man.get("rules_version"),
                        "built_at": man.get("built_at"), "source_md5": (man.get("source") or {}).get("md5")}
                n = build_index(bundle_records(tmp_bundle), tmp_index, meta)
                if n <= 0:
                    raise TrailDictError("bundle: no ways")
                os.replace(tmp_index, r / f"{version}.sqlite")
            finally:
                for p in (tmp_bundle, tmp_index):
                    try:
                        p.unlink()
                    except OSError:
                        pass
            _write_json(r / CURRENT, {**meta, "ways": n, "format": FORMAT,
                                      "indexed_at": dt.datetime.now().replace(microsecond=0).isoformat(sep=" "),
                                      "checked_at": now()})
            for p in r.glob("*.sqlite"):
                if p.name != f"{version}.sqlite":
                    try:
                        p.unlink()
                    except OSError:
                        pass
            log.info("trail dictionary %s indexed: %d ways", version, n)
            return {"status": "updated", "version": version, "ways": n}
        except (TrailDictError, sqlite3.Error, OSError, ValueError, KeyError, TypeError) as e:
            log.warning("trail dictionary not refreshed: %s", e)
            # the look is recorded either way (due(): the next one in an hour without an index, a day with)
            _write_json(r / CURRENT, {**cur, "checked_at": now(), "error": str(e)[:200]})
            return {"status": "failed", "error": str(e)[:200]}
    finally:
        _refresh_lock.release()


def due(root=None, now: Optional[float] = None) -> bool:
    """Is a look at latest.json due? (a URL is set, and no index yet or the last look is a day old)"""
    if not base_url() or _root(root) is None:
        return False
    st = _read_json(_root(root) / CURRENT) or {}
    if status(root) is None or st.get("format") != FORMAT:
        last = st.get("checked_at") or 0
        return (now if now is not None else time.time()) - float(last) >= 3600.0   # without one: hourly
    return (now if now is not None else time.time()) - float(st.get("checked_at") or 0) >= CHECK_EVERY_S


def tick() -> Optional[object]:
    """From the scheduler's loop: queue a refresh when one is due (the server's one job queue, `cold`:
    the index build reads the whole bundle). Returns the job, if one was queued."""
    if not due():
        return None
    from backend import jobs
    return jobs.submit(lambda job: refresh(), cls="full", priority="cold", label="trail dictionary",
                       key=("trail_dict",))


def building() -> bool:
    """Is a refresh running or queued right now?"""
    if _refresh_lock.locked():
        return True
    try:
        from backend import jobs
        j = jobs.find(("trail_dict",))
        return j is not None and j.state in ("queued", "running")
    except Exception:                       # noqa: BLE001
        return False
