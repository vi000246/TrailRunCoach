"""
Which data the charts read (settings key `charts.data_source`: wko5 | coros |
tp) — synchronous helpers for the Dataset factory in api/wko5views.py, which
is not async.

    source = current_source()                 # "wko5" by default
    stamp  = source_stamp(source, ATHLETE_DIR)
    ds     = dataset_for_source(source, ATHLETE_DIR, config)   (fitdataset.py)

`source_stamp` changes whenever the source's files change (a sync added or
removed FITs, WKO5 rewrote its index), so an lru_cache keyed on it rebuilds.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

SOURCES = ("wko5", "coros", "tp")


def _db_path() -> Optional[Path]:
    try:
        from backend.db.database import DB_PATH
        return Path(DB_PATH)
    except Exception:
        return None


def current_source(user_id: int = 1) -> str:
    db = _db_path()
    if db is None or not db.exists():
        return "wko5"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value_json FROM user_settings WHERE user_id=? AND key=?",
                              (user_id, "charts.data_source")).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return "wko5"
    try:
        v = json.loads(row[0]) if row else "wko5"
    except ValueError:
        return "wko5"
    return v if v in SOURCES else "wko5"


def source_stamp(source: str, wko5_dir: Path) -> str:
    if source in ("coros", "tp"):
        from backend.sync import storage
        root = storage.source_dir(source)
        if not root.exists():
            return f"{source}:empty"
        files = [p for p in root.rglob("*.fit*") if p.is_file() and not p.is_symlink()]
        latest = max((p.stat().st_mtime_ns for p in files), default=0)
        return f"{source}:{len(files)}:{latest}"
    try:
        return "wko5:" + ";".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
                                  for p in sorted(Path(wko5_dir).glob("*.wko5athlete")))
    except OSError:
        return "wko5:"
