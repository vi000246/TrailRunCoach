"""
Which data the charts read (settings key `charts.data_source`: synced | wko5 |
coros | tp) — synchronous helpers for the Dataset factory in api/wko5views.py,
which is not async. "synced" = the COROS and TP folders merged, the
主要資料來源's file per activity (backend/sync/primary.py).

    source = current_source()                 # "synced" by default ("wko5" without an app DB)
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

SOURCES = ("synced", "wko5", "coros", "tp")
# "synced" = both synced folders merged, one file per activity from the 主要資料來源
# (backend/sync/primary.py); the default once the app has a DB. Without one
# (tests, a bare checkout) the default stays "wko5".
DEFAULT_SOURCE = "synced"
FIT_SOURCES = ("synced", "coros", "tp")


def _db_path() -> Optional[Path]:
    try:
        from backend.db.database import DB_PATH
        return Path(DB_PATH)
    except Exception:
        return None


def read_setting(key: str, default=None, user_id: int = 1):
    """Synchronous, read-only lookup in the settings store (user_settings);
    `default` when the DB, table or row is missing."""
    db = _db_path()
    if db is None or not db.exists():
        return default
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value_json FROM user_settings WHERE user_id=? AND key=?",
                              (user_id, key)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return default
    if not row:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def current_source(user_id: int = 1) -> str:
    db = _db_path()
    default = DEFAULT_SOURCE if db is not None and db.exists() else "wko5"
    v = read_setting("charts.data_source", default, user_id)
    return v if v in SOURCES else default


def primary_info(user_id: int = 1) -> tuple[str, dict]:
    """(主要資料來源 setting — "auto" | a source —, {db source: last sync
    status}) from the settings store, read-only; ("auto", {}) without a DB."""
    from backend.sync import primary as P
    setting = P.normalize(read_setting(P.SETTING_KEY, None, user_id))
    status = {s: P.last_status(read_setting(f"sync.{s}.last_result", None, user_id)) for s in P.DB_SOURCES}
    return setting, status


def athlete_tz(user_id: int = 1):
    """athlete.timezone setting -> WKO5COACH_TZ -> system zone (as the sync uses)."""
    from backend.settings.repository import resolve_tz
    return resolve_tz(read_setting("athlete.timezone", None, user_id))


def db_stamp() -> str:
    """What a FIT dataset reads from the app DB besides the files: the trail
    classification (incl. user overrides), the duplicate links and the
    athlete_settings rows (fitdataset.py). A change there must rebuild the
    cached Dataset even when no FIT file changed. Read-only; "" without a DB."""
    db = _db_path()
    if db is None or not db.exists():
        return ""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            a = con.execute("SELECT count(*), sum(trail_classification='trail'), sum(trail_classification='road'), "
                            "sum(classification_overridden), sum(duplicate_of IS NOT NULL), "
                            "sum(coalesce(duplicate_of, 0)) FROM workout_files").fetchone()
            try:
                b = con.execute("SELECT count(*), max(effective_date), sum(coalesce(weight_kg, 0)), "
                                "sum(coalesce(run_ftp_w, 0)), sum(coalesce(threshold_pace_s_per_km, 0)) "
                                "FROM athlete_settings").fetchone()
            except sqlite3.Error:
                b = ()
        finally:
            con.close()
    except sqlite3.Error:
        return ""
    return ",".join("" if v is None else str(v) for v in (*a, *b))


def source_stamp(source: str, wko5_dir: Path) -> str:
    """Changes when the source's files change, or power.accept_watch_power
    (it changes power TSS, so the Dataset must be rebuilt), or which bad
    activity files are left out (activities.exclude_bad and the per-activity
    keep / exclude overrides, engine/bad_activity.py)."""
    from backend.engine.power_source import read_setting as accept_watch
    from backend.engine import bad_activity as BA
    return (f"{_files_stamp(source, wko5_dir)}|pw:{int(accept_watch())}"
            f"|ex:{int(BA.read_setting())}:{BA.overrides_stamp()}")


def _files_stamp(source: str, wko5_dir: Path) -> str:
    if source == "synced":
        # both folders + the primary setting and the last sync statuses
        # (自動's pick reads them; its other input, the newest activity, is in
        # the folders' stamps)
        setting, status = primary_info()
        st = ",".join(f"{k}={v}" for k, v in sorted(status.items()))
        return f"synced[{setting};{st}]|{_files_stamp('coros', wko5_dir)}|{_files_stamp('tp', wko5_dir)}"
    if source in ("coros", "tp"):
        from backend.sync import storage
        root = storage.source_dir(source)
        if not root.exists():
            return f"{source}:empty"
        files = [p for p in root.rglob("*.fit*") if p.is_file() and not p.is_symlink()]
        latest = max((p.stat().st_mtime_ns for p in files), default=0)
        # a rename (migrate_coros_sport_names) keeps count and mtimes: the
        # names are in the stamp too, so the Dataset doesn't keep stale paths
        import hashlib
        names = hashlib.sha1("\n".join(sorted(str(p.relative_to(root)) for p in files)).encode()).hexdigest()[:12]
        return f"{source}:{len(files)}:{latest}:{names}:{db_stamp()}"
    try:
        return "wko5:" + ";".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
                                  for p in sorted(Path(wko5_dir).glob("*.wko5athlete")))
    except OSError:
        return "wko5:"
