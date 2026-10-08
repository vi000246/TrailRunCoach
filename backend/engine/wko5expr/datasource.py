"""
Which data the charts read — synchronous helpers for the Dataset factory in
api/wko5views.py, which is not async. One synced source at a time (資料來源,
backend/sync/primary.py): the charts read that source's folder ("coros" /
"tp") only; `charts.data_source` = "wko5" switches them to the WKO5 folder
(cross-check), any other stored value follows the 資料來源.

    source = current_source()                 # "coros" / "tp" ("wko5" without an app DB)
    stamp  = source_stamp(source, ATHLETE_DIR)
    ds     = dataset_for_source(source, ATHLETE_DIR, config)   (fitdataset.py)

`source_stamp` changes whenever the source's files change (a sync added or
removed FITs, WKO5 rewrote its index), so an lru_cache keyed on it rebuilds.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

SOURCES = ("wko5", "coros", "tp")          # what a Dataset can be built from
FIT_SOURCES = ("coros", "tp")


def _db_path() -> Optional[Path]:
    try:
        from backend.db.database import db_path
        return db_path()
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


def wko5_available(wko5_dir: Optional[Path] = None) -> bool:
    """A WKO5 athlete file (*.wko5athlete) is there. A runner without WKO5
    (COROS / TP only) has none: the charts then read the synced FITs."""
    if wko5_dir is None:
        from backend.settings.paths import athlete_dir
        wko5_dir = athlete_dir()
    try:
        return any(Path(wko5_dir).glob("*.wko5athlete"))
    except OSError:
        return False


def primary_folder(user_id: int = 1) -> str:
    """The 資料來源's folder ("coros" / "tp") from the settings store,
    read-only; COROS until a setting exists (primary.effective)."""
    from backend.sync import primary as P
    return P.folder(read_setting(P.SETTING_KEY, None, user_id))


def current_source(user_id: int = 1, wko5_dir: Optional[Path] = None) -> str:
    """The Dataset the charts read: the 資料來源's folder; "wko5" when
    charts.data_source = wko5 (or without an app DB, the tests' / a bare
    checkout's default) and a WKO5 athlete file exists."""
    db = _db_path()
    if db is None or not db.exists():
        want = "wko5"
    else:
        want = "wko5" if read_setting("charts.data_source", None, user_id) == "wko5" else "source"
    if want == "wko5" and wko5_available(wko5_dir):
        return "wko5"
    return primary_folder(user_id)


def athlete_tz(user_id: int = 1):
    """athlete.timezone setting -> WKO5COACH_TZ -> system zone (as the sync uses)."""
    from backend.settings.repository import resolve_tz
    return resolve_tz(read_setting("athlete.timezone", None, user_id),
                      auto=read_setting("athlete.timezone.auto", None, user_id))


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
        # a locked DB is not a change: "" here flipped the cache key and
        # rebuilt (and kept) another whole Dataset
        return _LAST_DB_STAMP.get(str(db), "")
    s = ",".join("" if v is None else str(v) for v in (*a, *b))
    _LAST_DB_STAMP[str(db)] = s
    return s


_LAST_DB_STAMP: dict[str, str] = {}


def source_stamp(source: str, wko5_dir: Path) -> str:
    """Changes when the source's files change, or power.accept_watch_power
    (it changes power TSS, so the Dataset must be rebuilt), or which bad
    activity files are left out (activities.exclude_bad and the per-activity
    keep / exclude overrides, engine/bad_activity.py)."""
    from backend.engine.power_source import read_setting as accept_watch
    from backend.engine import bad_activity as BA
    return (f"{_files_stamp(source, wko5_dir)}|pw:{int(accept_watch())}"
            f"|ex:{int(BA.read_setting())}:{BA.overrides_stamp()}")


# The FIT folder scan (rglob + a stat per file: 800+ files) ran on every _dataset() call, 8–12
# times per page load (SP-362). Its result is kept FILES_STAMP_TTL_S seconds AND only while the
# folder and its year folders keep their mtimes (a file added, removed or renamed in them
# changes their mtime at once); the in-process writers (files/file_service._import_one_file,
# sync/purge.py) also drop it with files_changed(). What is left — a file rewritten in place
# by another process — shows after at most FILES_STAMP_TTL_S. The DB part (db_stamp) is read
# on every call, as before. Chosen over a pure generation counter: changes made outside the
# app (a copy into the folder, a restore, a script) are still seen.
FILES_STAMP_TTL_S = 5.0
_FILES_MEMO: dict = {}            # root -> (expires monotonic, dirs signature, stamp)
_FILES_LOCK = threading.Lock()


def files_changed() -> None:
    """A FIT was added / removed by this process: the next stamp rescans the folder."""
    with _FILES_LOCK:
        _FILES_MEMO.clear()


def _dirs_sig(root: Path) -> tuple:
    """(name, mtime_ns) of the folder and its sub-folders (fit/<source>/<year>/): cheap."""
    out = [("", root.stat().st_mtime_ns)]
    for p in root.iterdir():
        if p.is_dir() and not p.is_symlink():
            out.append((p.name, p.stat().st_mtime_ns))
    return tuple(sorted(out))


def _scan(source: str, root: Path) -> str:
    files = [p for p in root.rglob("*.fit*") if p.is_file() and not p.is_symlink()]
    latest = max((p.stat().st_mtime_ns for p in files), default=0)
    # a rename (migrate_coros_sport_names) keeps count and mtimes: the
    # names are in the stamp too, so the Dataset doesn't keep stale paths
    import hashlib
    names = hashlib.sha1("\n".join(sorted(str(p.relative_to(root)) for p in files)).encode()).hexdigest()[:12]
    return f"{source}:{len(files)}:{latest}:{names}"


def _scan_cached(source: str, root: Path) -> str:
    try:
        sig = _dirs_sig(root)
    except OSError:
        return _scan(source, root)
    key, now = str(root), time.monotonic()
    with _FILES_LOCK:
        hit = _FILES_MEMO.get(key)
    if hit is not None and hit[0] > now and hit[1] == sig:
        return hit[2]
    stamp = _scan(source, root)
    with _FILES_LOCK:
        _FILES_MEMO[key] = (now + FILES_STAMP_TTL_S, sig, stamp)
    return stamp


def _files_stamp(source: str, wko5_dir: Path) -> str:
    if source in ("coros", "tp"):
        from backend.sync import storage
        root = storage.source_dir(source)
        if not root.exists():
            return f"{source}:empty"
        return f"{_scan_cached(source, root)}:{db_stamp()}"
    try:
        return "wko5:" + ";".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
                                  for p in sorted(Path(wko5_dir).glob("*.wko5athlete")))
    except OSError:
        return "wko5:"
