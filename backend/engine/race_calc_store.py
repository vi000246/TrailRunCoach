"""
The race calculator's (賽事計算機) inputs and last result, saved per season-plan event.

Choosing an event (or reopening the page with it selected) restores what was
entered and shows the last result without recomputing first — no re-entering.

  * table race_calc in the app DB (one row per event; models.RaceCalc):
    inputs_json  the page's form state (target, course ref, stops, locks, overrides, …)
    result_json  the last /plan response for those inputs
    saved_at     when

Sync sqlite like engine/event_gpx.py (same DDL as the model, so the table exists
whether or not init_db ran). A new table: additive, nothing is altered. Tests pass
their own db_path (conftest points the default at nothing).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Optional

TABLE = "race_calc"
DDL = (f"CREATE TABLE IF NOT EXISTS {TABLE} (id INTEGER PRIMARY KEY, event_id VARCHAR(64) NOT NULL UNIQUE, "
       "inputs_json TEXT, result_json TEXT, saved_at DATETIME)")
MAX_BYTES = 4_000_000            # inputs + result as JSON; a GPX plan's profile is the bulk (≤ 1500 points)
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class RaceCalcError(ValueError):
    pass


def _default_db() -> Optional[Path]:
    """The app DB. Tests patch this (conftest)."""
    try:
        from backend.db.database import DB_PATH
        return Path(DB_PATH)
    except Exception:                       # noqa: BLE001
        return None


def _db(db_path=None) -> Optional[Path]:
    return Path(db_path) if db_path is not None else _default_db()


def _check_id(eid: str) -> str:
    if not isinstance(eid, str) or not _ID.match(eid):
        raise RaceCalcError("賽事 id 格式不對")
    return eid


def _loads(s: Optional[str]):
    try:
        return json.loads(s) if s else None
    except (TypeError, ValueError):
        return None


def get(eid: str, db_path=None) -> Optional[dict]:
    """{event_id, inputs, result, saved_at} of an event, None when nothing is saved."""
    _check_id(eid)
    p = _db(db_path)
    if p is None or not p.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            r = con.execute(f"SELECT event_id, inputs_json, result_json, saved_at FROM {TABLE} WHERE event_id=?",
                            (eid,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:                   # no table yet
        return None
    if not r:
        return None
    return {"event_id": r[0], "inputs": _loads(r[1]), "result": _loads(r[2]), "saved_at": r[3]}


def save(eid: str, inputs: dict, result: Optional[dict] = None, db_path=None) -> dict:
    """Upsert the event's inputs (and result: None keeps the stored one)."""
    _check_id(eid)
    if not isinstance(inputs, dict):
        raise RaceCalcError("inputs 要是物件")
    p = _db(db_path)
    if p is None:
        raise RaceCalcError("沒有資料庫")
    ij = json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))
    rj = json.dumps(result, ensure_ascii=False, separators=(",", ":")) if result is not None else None
    if len(ij) + len(rj or "") > MAX_BYTES:
        raise RaceCalcError("要儲存的資料太大")
    now = dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    try:
        con.execute(DDL)
        if rj is None:
            con.execute(f"INSERT INTO {TABLE} (event_id, inputs_json, saved_at) VALUES (?, ?, ?) "
                        "ON CONFLICT(event_id) DO UPDATE SET inputs_json=excluded.inputs_json, saved_at=excluded.saved_at",
                        (eid, ij, now))
        else:
            con.execute(f"INSERT INTO {TABLE} (event_id, inputs_json, result_json, saved_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(event_id) DO UPDATE SET inputs_json=excluded.inputs_json, "
                        "result_json=excluded.result_json, saved_at=excluded.saved_at", (eid, ij, rj, now))
        con.commit()
    finally:
        con.close()
    return get(eid, p)


def delete(eid: str, db_path=None) -> bool:
    _check_id(eid)
    p = _db(db_path)
    if p is None or not p.exists():
        return False
    try:
        con = sqlite3.connect(str(p))
        try:
            n = con.execute(f"DELETE FROM {TABLE} WHERE event_id=?", (eid,)).rowcount
            con.commit()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return n > 0
