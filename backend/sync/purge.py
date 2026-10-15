"""
Delete one source's synced files (「刪除這個來源的同步檔案」).

* files: only inside ~/.wko5coach/fit/<source>/ (storage.confined: resolved
  paths, no symlinks, nothing outside); the WKO5 folder, the other source and
  unmigrated legacy folders are never touched — their rows are still removed
  from the DB, the files are reported as "refused"
* FIT objects (SP-308, files/fitstore.py: rows whose file_path is an object key) are
  not unlinked here: their fit_objects row is marked `deleted` (the Dataset and the
  race-power scan leave them out at once) and cleanup_objects removes the files later —
  only objects no live workout_files row points at
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

from backend.db.models import FitObject, SyncState, WorkoutFile
from backend.files import fitstore
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
        files, size, refused, marked = 0, 0, 0, 0
        store = fitstore.default_store()
        going = {r.id for r in rows}
        for r in rows:
            if fitstore.is_object_key(r.file_path):
                # a FIT object: marked deleted, the file goes in cleanup_objects (SP-308)
                if fitstore.folder_of_key(r.file_path) != source:
                    refused += 1
                    continue
                n = await mark_deleted(db, r.file_path, keep_row_ids=going)
                if n:
                    try:
                        size += store.path(r.file_path).stat().st_size
                    except (OSError, ValueError):
                        pass
                    files += 1
                    marked += 1
                continue
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
            # stray files in the source folder that no row points at; the FIT objects
            # (objects/) are cleanup_objects' (only the unreferenced ones, later)
            root = storage.source_dir(source)
            objects = (root / fitstore.OBJECTS).resolve()
            if root.exists():
                for p in sorted(root.rglob("*"), reverse=True):
                    if p.is_symlink():
                        continue
                    try:
                        rp0 = p.resolve()
                    except OSError:
                        continue
                    if rp0 == objects or objects in rp0.parents:
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
            elif source == "garmin":
                st.garmin_last_sync_at = None
            elif source == "intervals":
                st.intervals_last_sync_at = None
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
           "files_refused": refused, "objects_marked_deleted": marked, "cursor_reset": True, "chart_source_switched_to_wko5": switched,
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
    return {"folder": f"fit/{source}", "files": fs["files"], "bytes": fs["bytes"],
            "size": storage.human_size(fs["bytes"]), "rows": n,
            "date_from": d0.isoformat() if d0 else None, "date_to": d1.isoformat() if d1 else None}


async def mark_deleted(db: AsyncSession, key: str, keep_row_ids: frozenset | set = frozenset()) -> int:
    """Mark the FIT object `key` deleted (fit_objects.deleted) unless a workout_files row
    other than `keep_row_ids` (the rows being deleted) still points at it. 1 when marked."""
    sha = fitstore.sha_of_key(key)
    if sha is None:
        return 0
    others = (await db.execute(select(WorkoutFile.id).where(WorkoutFile.file_path == key))).scalars().all()
    if any(i not in keep_row_ids for i in others):
        return 0
    row = (await db.execute(select(FitObject).where(FitObject.sha256 == sha))).scalar_one_or_none()
    if row is None:
        # an object stored before its row existed (or a lost row): the mark needs one
        st = fitstore.default_store()
        try:
            size = len(st.get(key))
        except (OSError, ValueError):
            size = 0
        row = FitObject(sha256=sha, size=size, source=storage.SOURCES.get(fitstore.folder_of_key(key), fitstore.folder_of_key(key)))
        db.add(row)
    row.deleted = True
    await db.flush()
    return 1


ORPHAN_MIN_AGE_S = 3600     # an object a running sync just stored is not an orphan yet


async def cleanup_objects(db: AsyncSession, apply: bool = True, min_age_s: float = ORPHAN_MIN_AGE_S,
                          store: Optional["fitstore.LocalDirStore"] = None) -> dict:
    """Remove the files of FIT objects nobody uses (SP-308, files/fitstore.py):

    * fit_objects rows marked `deleted` that no workout_files row points at: the file is
      removed, the row stays (the deleted mark; a later import of the same content stores
      the file again and lifts the mark);
    * orphan object files — no fit_objects row and no workout_files row (a sync that
      stored the object and then failed) — older than `min_age_s`.

    Never touches a legacy (non-object) file or anything a live row points at. `apply`
    False only counts. Returns counts and bytes."""
    import time
    store = store or fitstore.default_store()
    live = {fp for fp in (await db.execute(select(WorkoutFile.file_path))).scalars().all()
            if fitstore.is_object_key(fp)}
    objs = {r.sha256: r for r in (await db.execute(select(FitObject))).scalars().all()}
    out = {"removed": 0, "bytes": 0, "orphans_removed": 0, "kept_live": 0, "applied": apply}
    now = time.time()
    for folder in storage.SOURCES:
        if not storage.source_dir(folder).is_dir():
            continue
        for o in list(store.list(f"{folder}/{fitstore.OBJECTS}")):
            sha = fitstore.sha_of_key(o.key)
            if sha is None:
                continue
            if o.key in live:
                out["kept_live"] += 1
                continue
            row = objs.get(sha)
            if row is not None and not row.deleted:
                continue                      # recorded, not deleted: a row is about to point at it
            if row is None:
                try:
                    if now - store.path(o.key).stat().st_mtime < min_age_s:
                        continue
                except (OSError, ValueError):
                    continue
            if apply:
                store.delete(o.key)
            out["orphans_removed" if row is None else "removed"] += 1
            out["bytes"] += o.size
    if out["removed"] or out["orphans_removed"]:
        log.info("FIT objects cleanup: removed=%d orphans=%d bytes=%d applied=%s",
                 out["removed"], out["orphans_removed"], out["bytes"], apply)
    return out


CLEANUP_EVERY_S = 24 * 3600
_CLEANUP_AT: dict = {}


async def cleanup_tick(session_factory, now: Optional[float] = None) -> Optional[dict]:
    """cleanup_objects at most once a day per tenant DB (the scheduler loop, sync/scheduler.py).
    Skipped while a sync or a deletion holds a source (runner.hold): a sync may be storing
    or reviving an object right now; the next minute tries again."""
    import contextlib
    import time
    from backend.db.database import db_path
    t = time.monotonic() if now is None else now
    key = str(db_path())
    last = _CLEANUP_AT.get(key)
    if last is not None and t - last < CLEANUP_EVERY_S:
        return None
    try:
        with contextlib.ExitStack() as st:
            for src in runner.SOURCES:
                st.enter_context(runner.hold(src))
            async with session_factory() as db:
                res = await cleanup_objects(db)
    except runner.SyncBusy:
        return None
    _CLEANUP_AT[key] = t
    return res
