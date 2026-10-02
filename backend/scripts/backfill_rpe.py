"""Backfill workout_files.rpe / feel from the FIT files already imported.

The watch's post-workout self-rating (FIT session workout_rpe / workout_feel,
engine/activity_tags.recorded_from_session) is stored at import since
2026-10-02; this fills the rows imported before. Only FIT rows with neither
value are read; a file without the fields stays NULL. Idempotent.

On this athlete's data (read-only scan, 2026-10-02): the COROS APEX 2 Pro
FITs carry no RPE / feel; the Garmin fenix 7 FITs in the COROS folder
(2023-12 … 2025-03) do, 32 with a value.

Usage:
    python -m backend.scripts.backfill_rpe --dry-run      # read-only: what would change
    python -m backend.scripts.backfill_rpe                # write (stop the app first)
    python -m backend.scripts.backfill_rpe --db PATH
"""
from __future__ import annotations

import argparse
import gzip
import sqlite3
from pathlib import Path
from typing import Optional

from backend.engine.activity_tags import recorded_from_session


def session_of(path: Path) -> Optional[dict]:
    """The session message's fields of one FIT (fitdecode, errors ignored);
    None when unreadable or missing."""
    import fitdecode
    p = Path(path)
    try:
        src = gzip.open(p, "rb") if p.suffix == ".gz" else open(p, "rb")
    except OSError:
        return None
    try:
        with src, fitdecode.FitReader(src, error_handling=fitdecode.ErrorHandling.IGNORE) as fr:
            for fm in fr:
                if isinstance(fm, fitdecode.FitDataMessage) and fm.name == "session":
                    return {f.name: f.value for f in fm.fields}
    except Exception:                           # noqa: BLE001 — a broken file is skipped
        return None
    return None


def plan(con: sqlite3.Connection) -> list[dict]:
    """[{id, file_path, rpe, feel}] for every FIT row without a value whose
    file has one."""
    have = {r[1] for r in con.execute("PRAGMA table_info(workout_files)")}
    if not {"rpe", "feel"} <= have:
        raise SystemExit("workout_files has no rpe / feel columns yet: start the app once (init_db migrates)")
    out = []
    for i, fp in con.execute("SELECT id, file_path FROM workout_files WHERE file_format='fit' "
                             "AND rpe IS NULL AND feel IS NULL").fetchall():
        if not fp or not Path(fp).exists():
            continue
        rpe, feel = recorded_from_session(session_of(Path(fp)))
        if rpe is not None or feel is not None:
            out.append({"id": i, "file_path": fp, "rpe": rpe, "feel": feel})
    return out


def apply(con: sqlite3.Connection, items: list[dict]) -> int:
    for it in items:
        con.execute("UPDATE workout_files SET rpe=?, feel=? WHERE id=?", (it["rpe"], it["feel"], it["id"]))
    con.commit()
    return len(items)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.db is None:
        from backend.db.database import DB_PATH
        db = Path(DB_PATH)
    else:
        db = Path(a.db)
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) if a.dry_run else sqlite3.connect(db)
    try:
        items = plan(con)
        for it in items:
            print(f"{Path(it['file_path']).name}: RPE {it['rpe']}, feel {it['feel']}")
        if a.dry_run:
            print(f"dry run: {len(items)} rows would be updated")
        else:
            print(f"updated {apply(con, items)} rows")
    finally:
        con.close()


if __name__ == "__main__":
    main()
