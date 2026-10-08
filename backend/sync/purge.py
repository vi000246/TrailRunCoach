"""
Delete one source's synced files (「刪除這個來源的同步檔案」).

* files: only inside ~/.wko5coach/fit/<source>/ (storage.confined: resolved
  paths, no symlinks, nothing outside); the WKO5 folder, the other source and
  unmigrated legacy folders are never touched — their rows are still removed
  from the DB, the files are reported as "refused"
* DB: the source's WorkoutFile rows with their metrics / MMP cache, then the
  cross-source de-dup is rebuilt so a workout the other source also has
  becomes canonical there
* the source's sync cursor and last result are reset, so a later sync
  re-downloads cleanly
* holds the per-source lock (SyncBusy while a sync runs)
* when the source is the active chart data source, charts fall back to the
  WKO5 folder
* optional [date_from, date_to] (workout dates, inclusive)
Logs counts and sizes only.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import SyncState, WorkoutFile
from backend.settings.repository import SettingsRepository
from backend.sync import dedup, runner, storage

log = logging.getLogger(__name__)


async def delete_source_files(db: AsyncSession, source: str, athlete_id: int = 1,
                              date_from: Optional[dt.date] = None,
                              date_to: Optional[dt.date] = None) -> dict:
    storage.check_source(source)
    db_source = storage.SOURCES[source]
    # the COROS self-rating job gives way (it does not show as busy; SP-362 review #3)
    await runner._make_way(source)
    with runner.hold(source):
        q = select(WorkoutFile).where(WorkoutFile.athlete_id == athlete_id,
                                      WorkoutFile.source == db_source)
        if date_from:
            q = q.where(WorkoutFile.workout_date >= date_from)
        if date_to:
            q = q.where(WorkoutFile.workout_date <= date_to)
        rows = (await db.execute(q)).scalars().all()
        files, size, refused = 0, 0, 0
        for r in rows:
            try:
                p = storage.confined(r.file_path, source)
            except storage.PathNotConfined:
                refused += 1
                continue
            if p.is_file():
                size += p.stat().st_size
                p.unlink()
                files += 1
        whole = date_from is None and date_to is None
        if whole:
            # stray files in the source folder that no row points at
            root = storage.source_dir(source)
            if root.exists():
                for p in sorted(root.rglob("*"), reverse=True):
                    if p.is_symlink():
                        continue
                    if p.is_file():
                        try:
                            rp = storage.confined(p, source)
                        except storage.PathNotConfined:
                            continue
                        size += rp.stat().st_size
                        rp.unlink()
                        files += 1
                    elif p.is_dir() and not any(p.iterdir()):
                        p.rmdir()
        ids = [r.id for r in rows]
        if ids:
            await db.execute(update(WorkoutFile).where(WorkoutFile.duplicate_of.in_(ids))
                             .values(duplicate_of=None))
        for r in rows:
            await db.delete(r)
        await db.flush()

        st = (await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))).scalar_one_or_none()
        if st is not None:
            if source == "coros":
                st.coros_last_sync_at = None
            else:
                st.last_sync_cursor = None
                st.last_sync_at = None
        # the cursor starts over: every activity is listed again, the failed list with it (SP-362)
        from backend.sync import failures as FL
        await FL.clear(db, athlete_id, source)
        from backend.engine.wko5expr.datasource import files_changed
        files_changed()                   # the chart Dataset's files stamp rescans (SP-362)
        repo = SettingsRepository(db, athlete_id)
        await repo.set(f"sync.{runner.SETTING_NAME[source]}.last_result", None)
        await repo.set(f"sync.{runner.SETTING_NAME[source]}.last_ok", None)
        switched = False
        # the charts read the 資料來源: deleting its files sends them to the WKO5
        # folder when there is one (the other source is never used instead)
        from backend.engine.wko5expr.datasource import wko5_available
        from backend.sync import primary as P
        if (await repo.get("charts.data_source") != "wko5" and P.FOLDER[await P.current(db, athlete_id)] == source
                and wko5_available()):
            await repo.set("charts.data_source", "wko5")
            switched = True
        rebuilt = await dedup.rebuild(db, athlete_id)
        await db.commit()
    out = {"source": source, "rows_deleted": len(rows), "files_deleted": files, "bytes_deleted": size,
           "files_refused": refused, "cursor_reset": True, "chart_source_switched_to_wko5": switched,
           "dedup": rebuilt, "date_from": date_from.isoformat() if date_from else None,
           "date_to": date_to.isoformat() if date_to else None}
    log.warning("deleted %s sync data: rows=%d files=%d bytes=%d refused=%d",
                source, len(rows), files, size, refused)
    return out


async def source_stats(db: AsyncSession, source: str, athlete_id: int = 1) -> dict:
    """File count / size on disk plus DB rows and workout date span."""
    from sqlalchemy import func
    db_source = storage.SOURCES[storage.check_source(source)]
    n, d0, d1 = (await db.execute(select(
        func.count(WorkoutFile.id), func.min(WorkoutFile.workout_date), func.max(WorkoutFile.workout_date))
        .where(WorkoutFile.athlete_id == athlete_id, WorkoutFile.source == db_source))).one()
    fs = storage.folder_stats(source)
    return {"folder": f"~/.wko5coach/fit/{source}", "files": fs["files"], "bytes": fs["bytes"],
            "size": storage.human_size(fs["bytes"]), "rows": n,
            "date_from": d0.isoformat() if d0 else None, "date_to": d1.isoformat() if d1 else None}
