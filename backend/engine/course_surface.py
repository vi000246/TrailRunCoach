"""
A course's surface profile (SP-543, engine/racepower/surface.py), stored per course file.

The key is the course file's sha1 — the race calculator's course_id and event_gpx.sha1 — so
the profile of an event's GPX is looked up once, whether it came from the calculator's upload
or the plan page, and the user's corrections stay with that course:

  * table course_surface in the app DB (one row per course file; models.CourseSurface):
    profile_json    the OSM lookup: {"v", "segs": [{"a", "b" (km), "c" (class)}], "km", "ways", "at"}
    overrides_json  the user's marks {segment index: dirt / made / unsure}
    manual_json     a typed-in split {dirt, made, unsure} (whole %), or NULL
    updated_at      the last change (the server's local time)

A failed lookup is not stored (the next visit tries again). Sync sqlite like
race_calc_store.py (same DDL as the model, so the table exists whether or not init_db ran).
Tests pass their own db_path (conftest points the default at nothing).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Optional

from backend.i18n import _

TABLE = "course_surface"
DDL = (f"CREATE TABLE IF NOT EXISTS {TABLE} (id INTEGER PRIMARY KEY, sha1 VARCHAR(40) NOT NULL UNIQUE, "
       "profile_json TEXT, overrides_json TEXT, manual_json TEXT, updated_at DATETIME)")
MAX_BYTES = 2_000_000
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


class CourseSurfaceError(ValueError):
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


def check_id(sha1: str) -> str:
    if not isinstance(sha1, str) or not _SHA1.match(sha1):
        raise CourseSurfaceError(_("路線 id 格式不對"))
    return sha1


def _loads(s):
    try:
        return json.loads(s) if s else None
    except (TypeError, ValueError):
        return None


def get(sha1: str, db_path=None) -> Optional[dict]:
    """{sha1, profile, overrides, manual, updated_at}, None when nothing is stored."""
    check_id(sha1)
    p = _db(db_path)
    if p is None or not p.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            r = con.execute(f"SELECT sha1, profile_json, overrides_json, manual_json, updated_at FROM {TABLE} "
                            "WHERE sha1=?", (sha1,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:                   # no table yet
        return None
    if not r:
        return None
    return {"sha1": r[0], "profile": _loads(r[1]), "overrides": _loads(r[2]) or {}, "manual": _loads(r[3]),
            "updated_at": r[4]}


def _dump(x) -> Optional[str]:
    return None if x is None else json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def save(sha1: str, *, profile: Optional[dict] = None, overrides: Optional[dict] = None,
         manual=False, db_path=None) -> dict:
    """Upsert. profile: a new lookup (the marks and the typed-in split of the old one are
    dropped: the segments changed); overrides / manual (False = keep, None = clear): the user's."""
    check_id(sha1)
    p = _db(db_path)
    if p is None:
        raise CourseSurfaceError(_("沒有資料庫"))
    old = get(sha1, p) or {}
    if profile is not None:
        row = {"profile": profile, "overrides": {}, "manual": None}
    else:
        if not old.get("profile"):
            raise CourseSurfaceError(_("這條路線還沒有路面資料"))
        row = {"profile": old["profile"], "overrides": old.get("overrides") or {}, "manual": old.get("manual")}
    if overrides is not None:
        row["overrides"] = overrides
    if manual is not False:
        row["manual"] = manual
    vals = (_dump(row["profile"]), _dump(row["overrides"]), _dump(row["manual"]))
    if sum(len(v or "") for v in vals) > MAX_BYTES:
        raise CourseSurfaceError(_("要儲存的資料太大"))
    now = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    try:
        con.execute(DDL)
        con.execute(f"INSERT INTO {TABLE} (sha1, profile_json, overrides_json, manual_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(sha1) DO UPDATE SET profile_json=excluded.profile_json, "
                    "overrides_json=excluded.overrides_json, manual_json=excluded.manual_json, "
                    "updated_at=excluded.updated_at", (sha1, *vals, now))
        con.commit()
    finally:
        con.close()
    return get(sha1, p)


def delete(sha1: str, db_path=None) -> bool:
    check_id(sha1)
    p = _db(db_path)
    if p is None or not p.exists():
        return False
    try:
        con = sqlite3.connect(str(p))
        try:
            n = con.execute(f"DELETE FROM {TABLE} WHERE sha1=?", (sha1,)).rowcount
            con.commit()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return n > 0
