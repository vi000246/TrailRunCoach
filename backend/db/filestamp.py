"""
The change stamp of an SQLite file, for the read-only memos that re-read the
app DB only when it changed (activity_tags.load, injuries.load_events,
plan_store._plan_rows / test_sessions, api/injuries).

The app DB runs in WAL mode (db/database.py): a commit is appended to
<db>-wal, and the main file's mtime / size change only at a checkpoint
(every ~1000 pages, or when the last connection closes; the app keeps its
pool open). A memo keyed on the main file alone kept serving rows from
before the latest commits. The -wal file's (mtime, size) changes on every
commit, a checkpoint that restarts it rewrites it (new mtime), and the main
file changes then too, so the pair of stamps sees every commit.
"""
from __future__ import annotations

import os
from typing import Optional


def db_stamp(p) -> Optional[tuple]:
    """(main mtime_ns, size, -wal mtime_ns, -wal size); None when the DB file
    is missing / unreadable. The -wal part is (0, 0) when there is none."""
    try:
        st = os.stat(p)
    except OSError:
        return None
    try:
        w = os.stat(str(p) + "-wal")
        wal = (w.st_mtime_ns, w.st_size)
    except OSError:
        wal = (0, 0)
    return (st.st_mtime_ns, st.st_size, *wal)
