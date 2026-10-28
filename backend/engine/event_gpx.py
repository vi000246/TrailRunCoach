"""
The GPX stored with a season-plan event (賽季計畫的賽事 / 百岳行程).

Upload once (plan page or the race calculator), then everything that needs the
course reads it from here: the race calculator (POST /racepower/course/event/{id},
no re-upload, survives a server restart), the コース定数 reference lines
(engine/panels/race_refs.py: real km / climb / descent, per day for a multi-day
trip) and anything later (demo).

  * the file: <HOME>/event_gpx/<event id>.gz (the uploaded .gpx / .fit, gzipped:
    GPX is verbose XML, gzip makes it ~5–10× smaller)
  * the row:  table event_gpx in the app DB (one per event; models.EventGpx) with
    the file's name, sha1, sizes, the parsed totals and the day split points

Sync sqlite like engine/activity_tags.py (the engine panels are sync). Tests
pass their own db_path / root.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Optional, Sequence

ROOT = None      # fixed folder (tests); None = the tenant's event_gpx/


def _root() -> Path:
    if ROOT is not None:
        return Path(ROOT)
    from backend import tenancy
    return tenancy.private_path("event_gpx")
TABLE = "event_gpx"
COLS = ("event_id", "filename", "sha1", "bytes_raw", "bytes_gz", "km", "gain_m", "loss_m", "z_min", "z_max",
        "day_splits_json", "camp_km_json", "uploaded_at", "source_json")
DDL = (f"CREATE TABLE IF NOT EXISTS {TABLE} (id INTEGER PRIMARY KEY, event_id VARCHAR(64) NOT NULL UNIQUE, "
       "filename VARCHAR(200), sha1 VARCHAR(40), bytes_raw INTEGER, bytes_gz INTEGER, km FLOAT, gain_m FLOAT, "
       "loss_m FLOAT, z_min FLOAT, z_max FLOAT, day_splits_json TEXT, camp_km_json TEXT, uploaded_at DATETIME, "
       "source_json TEXT)")
ADDED = (("source_json", "TEXT"),)     # columns newer than the table (SP-537); db/database.py adds them too
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class EventGpxError(ValueError):
    pass


def _default_db() -> Optional[Path]:
    """The app DB. Tests patch this (conftest)."""
    try:
        from backend.db.database import db_path
        return db_path()
    except Exception:                       # noqa: BLE001
        return None


def _db(db_path=None) -> Optional[Path]:
    return Path(db_path) if db_path is not None else _default_db()


def _check_id(eid: str) -> str:
    if not isinstance(eid, str) or not _ID.match(eid):
        raise EventGpxError("賽事 id 格式不對")
    return eid


def file_path(eid: str, root: Optional[Path] = None) -> Path:
    return Path(root or _root()) / f"{_check_id(eid)}.gz"


def _row(r: dict) -> dict:
    d = {c: r.get(c) for c in COLS}
    for k in ("day_splits_json", "camp_km_json", "source_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k) or "null")
        except (TypeError, ValueError):
            d[k[:-5]] = None
    return d


def get(eid: str, db_path=None) -> Optional[dict]:
    """The stored row of an event's GPX (None = no GPX)."""
    p = _db(db_path)
    if p is None or not p.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:                                # SELECT *: a table older than a column (ADDED) still reads
            r = con.execute(f"SELECT * FROM {TABLE} WHERE event_id=?", (eid,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:                   # no table yet
        return None
    return _row(dict(r)) if r else None


def all_rows(db_path=None) -> dict[str, dict]:
    p = _db(db_path)
    if p is None or not p.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(f"SELECT * FROM {TABLE}").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return {r["event_id"]: r for r in (_row(dict(x)) for x in rows)}


def _write_row(p: Path, row: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    try:
        con.execute(DDL)
        have = {r[1] for r in con.execute(f"PRAGMA table_info({TABLE})")}
        for c, typ in ADDED:
            if c not in have:
                con.execute(f"ALTER TABLE {TABLE} ADD COLUMN {c} {typ}")
        cols = list(row)
        con.execute(f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                    f"ON CONFLICT(event_id) DO UPDATE SET " + ", ".join(f"{c}=excluded.{c}" for c in cols if c != "event_id"),
                    [row[c] for c in cols])
        con.commit()
    finally:
        con.close()


def parse(data: bytes, filename: str = ""):
    from backend.engine.racepower import gpx as GPX
    try:
        return GPX.parse(data, filename)
    except GPX.GpxError as e:
        raise EventGpxError(str(e))


def _course(track, split: str = "none") -> dict:
    from backend.engine.racepower import course as CO
    try:
        return CO.build_course(track, split=split)
    except ValueError as e:
        raise EventGpxError(str(e))


def save(eid: str, data: bytes, filename: str = "", *, db_path=None, root: Optional[Path] = None,
         day_splits_km: Optional[Sequence[float]] = None, source: Optional[dict] = None) -> dict:
    """Parse (rejects a broken file before anything is written), gzip to the app
    data dir and upsert the row. Replaces any earlier GPX of the event; the day
    splits are reset unless given (a new course = new km positions). source: where the file
    came from when not an upload (SP-537: {"kind": "cairn", "gpx_id", "url", "name"} — never a
    token); a new upload clears it."""
    _check_id(eid)
    p = _db(db_path)
    if p is None:
        raise EventGpxError("沒有資料庫")
    track = parse(data, filename)
    c = _course(track)
    t = c["totals"]
    gz = gzip.compress(data, compresslevel=9, mtime=0)
    f = file_path(eid, root)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(gz)
    os.replace(tmp, f)
    camps = [round(w["km"], 2) for w in c.get("wpts") or [] if w.get("camp")]   # candidates, every pass
    row = {"event_id": eid, "filename": (filename or track.name or "course.gpx")[:200],
           "sha1": hashlib.sha1(data).hexdigest(), "bytes_raw": len(data), "bytes_gz": len(gz),
           "km": float(t["km"]), "gain_m": float(t["gain_m"]), "loss_m": float(t["loss_m"]),
           "z_min": float(t["z_min"]), "z_max": float(t["z_max"]),
           "day_splits_json": json.dumps(clean_splits(day_splits_km, t["km"])) if day_splits_km else None,
           "camp_km_json": json.dumps(camps),
           "uploaded_at": dt.datetime.now().replace(microsecond=0).isoformat(sep=" "),
           "source_json": json.dumps(clean_source(source)) if source else None}
    _write_row(p, row)
    _memo.clear()
    return get(eid, p)


def clean_source(src: Optional[dict]) -> Optional[dict]:
    """Only the known, token-free keys of a source (SP-537)."""
    if not isinstance(src, dict) or src.get("kind") != "cairn":
        return None
    out = {k: str(src[k])[:200] for k in ("kind", "gpx_id", "url", "name") if src.get(k)}
    # SP-538: an import from the person's own Cairn trips (via "account", accounts/cairn_trips.py) keeps the
    # trip's version (「重新同步」 when Cairn's moves) and whether it carries a group's timetable (never pushed back)
    if src.get("via") in ("account", "share"):
        out["via"] = src["via"]
    if src.get("version") not in (None, "") and not isinstance(src.get("version"), bool):
        out["version"] = str(src["version"])[:40]
    if src.get("group") is True:
        out["group"] = True
    # SP-535: the nights marked in Cairn (km on our track), so the day-split preview can offer them back
    # as candidates after the day ends were changed
    nights = []
    for x in src.get("nights") or []:
        try:
            nights.append({"night": int(x["night"]), "km": round(float(x["km"]), 3), "name": str(x.get("name") or "")[:60]})
        except (KeyError, TypeError, ValueError):
            continue
    if nights:
        out["nights"] = nights[:20]
    # SP-577: the timetable read with it and the answer to 「這是團的時刻表」 (read only, never sent back)
    from backend.engine import cairn as CA
    sc = CA.clean_schedule(src.get("schedule"))
    if sc:
        out["schedule"] = sc
    return out if out.get("gpx_id") else None


def set_group(eid: str, group: bool, db_path=None) -> dict:
    """SP-577: the answer to 「這是團的時刻表」 for the Cairn timetable stored with the event."""
    r = get(eid, db_path)
    src = (r or {}).get("source") or {}
    if r is None or not isinstance(src.get("schedule"), dict):
        raise EventGpxError("這場賽事沒有 Cairn 時刻表")
    return _put_source(eid, {**src, "schedule": {**src["schedule"], "group": bool(group)}}, db_path)


def set_source(eid: str, db_path=None, **fields) -> dict:
    """Some keys of a Cairn source changed in place (SP-538: the trip's version after our own push)."""
    r = get(eid, db_path)
    src = (r or {}).get("source") or {}
    if r is None or src.get("kind") != "cairn":
        raise EventGpxError("not a GPX imported from Cairn")      # internal: after_push only
    return _put_source(eid, {**src, **fields}, db_path)


def _put_source(eid: str, src: dict, db_path=None) -> dict:
    p = _db(db_path)
    con = sqlite3.connect(str(p))
    try:
        con.execute(f"UPDATE {TABLE} SET source_json=? WHERE event_id=?", (json.dumps(clean_source(src)), eid))
        con.commit()
    finally:
        con.close()
    _memo.clear()
    return get(eid, p)


def cairn_url(row: Optional[dict]) -> Optional[str]:
    """The Cairn route the event's GPX was imported from (SP-537), None otherwise."""
    src = (row or {}).get("source") or {}
    return src.get("url") if src.get("kind") == "cairn" else None


def clean_splits(splits, km: float) -> list[float]:
    out = sorted({round(float(x), 3) for x in splits or [] if 0 < float(x) < km})
    return out


def set_splits(eid: str, splits: Sequence[float], db_path=None) -> dict:
    """Store the day split points (km) of a multi-day trip ([] = equal km per day)."""
    r = get(eid, db_path)
    if r is None:
        raise EventGpxError("這場賽事沒有 GPX")
    p = _db(db_path)
    con = sqlite3.connect(str(p))
    try:
        con.execute(f"UPDATE {TABLE} SET day_splits_json=? WHERE event_id=?",
                    (json.dumps(clean_splits(splits, r["km"])), eid))
        con.commit()
    finally:
        con.close()
    _memo.clear()
    return get(eid, p)


def delete(eid: str, db_path=None, root: Optional[Path] = None) -> bool:
    """Remove the row and the file; False when the event had no GPX."""
    _check_id(eid)
    had = False
    f = file_path(eid, root)
    if f.exists():
        f.unlink()
        had = True
    p = _db(db_path)
    if p is not None and p.exists():
        try:
            con = sqlite3.connect(str(p))
            try:
                had = con.execute(f"DELETE FROM {TABLE} WHERE event_id=?", (eid,)).rowcount > 0 or had
                con.commit()
            finally:
                con.close()
        except sqlite3.Error:
            pass
    _memo.clear()
    return had


def read_bytes(eid: str, root: Optional[Path] = None) -> Optional[bytes]:
    f = file_path(eid, root)
    try:
        return gzip.decompress(f.read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile):
        return None


_memo: dict = {}


def track(eid: str, row: Optional[dict] = None, *, db_path=None, root: Optional[Path] = None):
    """(Track, row) of the stored file, memoised on the sha1; None when missing."""
    row = row or get(eid, db_path)
    if row is None:
        return None
    key = ("track", eid, row["sha1"])
    if key in _memo:
        return _memo[key], row
    data = read_bytes(eid, root)
    if data is None:
        return None
    t = parse(data, row.get("filename") or "")
    _memo[key] = t
    return t, row


def splits_for(row: dict, days: int) -> list[float]:
    """The day split km of an n-day trip: the stored ones (the user's, when they make n days),
    else equal km per day (推估). SP-533: the GPX's camp / hut waypoints are only candidates
    (camp_km, every pass) — never cut by themselves, even when their count fits."""
    return split_source(row, days)[0]


def split_source(row: dict, days: int) -> tuple[list[float], str]:
    """(splits_for, "stored" | "equal" | "single") — course.day_cuts on the stored points."""
    from backend.engine.racepower import course as CO
    return CO.day_cuts(clean_splits(row.get("day_splits") or [], row["km"]), row["km"], days)


def day_stats(eid: str, days: int = 1, *, db_path=None, root: Optional[Path] = None) -> Optional[dict]:
    """{"totals": {km, gain_m, loss_m}, "days": [{day, km, gain_m, loss_m}], "splits_km", "split_source",
    "filename"} from the stored GPX (the race calculator's smoothing: course.build_course), None = no GPX."""
    got = track(eid, db_path=db_path, root=root)
    if got is None:
        return None
    tr, row = got
    cuts = splits_for(row, days)
    key = ("days", eid, row["sha1"], tuple(cuts))
    if key not in _memo:
        from backend.engine.racepower import course as CO
        c = _km_course(eid, tr, row)
        pieces = CO.cut_at(c["segments"], cuts)
        ds = []
        for n in range(1, len(cuts) + 2):
            ps = [p for p in pieces if p["day"] == n]
            ds.append({"day": n, "km": sum(p["dist_m"] for p in ps) / 1000.0,
                       "gain_m": sum(p["gain_m"] for p in ps), "loss_m": sum(p["loss_m"] for p in ps)})
        t = c["totals"]
        _memo[key] = {"totals": {"km": t["km"], "gain_m": t["gain_m"], "loss_m": t["loss_m"]}, "days": ds}
    src = split_source(row, days)[1]
    return {**_memo[key], "splits_km": cuts, "split_source": src, "filename": row.get("filename"),
            "candidates_km": clean_splits(row.get("camp_km") or [], row["km"])}


def _km_course(eid: str, tr, row: dict) -> dict:
    """The stored track's course in 1 km pieces (a day end inside one is prorated over ≤ 1 km),
    memoised on the sha1: day_stats and the day-split preview share it."""
    key = ("km_course", eid, row["sha1"])
    if key not in _memo:
        _memo[key] = _course(tr, "km")
    return _memo[key]


def preview(eid: str, days: int = 1, *, db_path=None, root: Optional[Path] = None) -> Optional[dict]:
    """What the day-split preview (static/daysplit.js, SP-535) needs to show each day as the day ends
    move, without asking the server again: the 1 km pieces [start km, end km, climb m, descent m]
    (the day sums are day_stats' own: course.cut_at), the profile, the candidates (the camp / hut
    waypoints, every pass — SP-533; the OpenStreetMap huts — SP-536, kind "hut", with osm_attr),
    the stored day ends (SP-537: a Cairn import's nights, `source`) and the recorded nights
    (SP-534, a proposal). None = no GPX."""
    got = track(eid, db_path=db_path, root=root)
    if got is None:
        return None
    tr, row = got
    c = _km_course(eid, tr, row)
    km = float(c["totals"]["km"])
    cands = []
    for w in c.get("wpts") or []:
        if not (w.get("camp") and 0.05 < w["km"] < km - 0.05):
            continue
        x = {"km": round(w["km"], 3), "kind": "camp", "name": w["name"], "pass": w["pass"], "passes": w["passes"]}
        if w.get("src") == "osm":           # SP-536: a hut / camp from the built-in OpenStreetMap list
            x.update(kind="hut", src="osm", osm_kind=w.get("kind"), **({"name_en": w["name_en"]} if w.get("name_en") else {}))
        cands.append(x)
    cuts, src = split_source(row, days)
    return {"total_km": km,
            "pieces": [[round(s["start_km"], 4), round(s["end_km"], 4), round(s["gain_m"], 1), round(s["loss_m"], 1)]
                       for s in c["segments"]],
            "profile": {"km": c["profile"]["km"], "z": c["profile"]["z"]},
            "candidates": cands, "stored_km": clean_splits(row.get("day_splits") or [], km),
            "splits_km": cuts, "split_source": src, "filename": row.get("filename"),
            "osm_attr": c.get("osm"), "source": row.get("source"),     # SP-536 credit; SP-537 Cairn
            "overnights": overnights(eid, db_path=db_path, root=root)}


def overnights(eid: str, *, db_path=None, root: Optional[Path] = None) -> Optional[dict]:
    """The stored track's overnight stops (racepower.overnight.find, SP-534), memoised on the sha1;
    None = no GPX, no time stamps or a broken file."""
    got = track(eid, db_path=db_path, root=root)
    if got is None:
        return None
    tr, row = got
    key = ("nights", eid, row["sha1"])
    if key not in _memo:
        from backend.engine.racepower import overnight as OV
        try:
            _memo[key] = OV.find(tr)
        except Exception:                   # noqa: BLE001 — a proposal only: nothing to propose
            _memo[key] = None
    return _memo[key]


def meta(row: Optional[dict]) -> Optional[dict]:
    """The row as the API returns it."""
    if row is None:
        return None
    return {k: row.get(k) for k in ("event_id", "filename", "bytes_raw", "bytes_gz", "km", "gain_m", "loss_m",
                                     "z_min", "z_max", "day_splits", "camp_km", "uploaded_at", "sha1", "source")}
