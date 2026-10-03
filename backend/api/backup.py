"""
備份 API (settings page). The file work is in engine/backup.py; this module
keeps the settings (settings/repository.py backup.*), runs the daily automatic
backup (sync/scheduler.py loop -> auto_tick) and does the in-process reload
after a restore.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.engine import backup as B
from backend.i18n import _
from backend.settings.repository import SettingsRepository

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/backup", tags=["backup"])
UPLOAD_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_UPLOAD = 8 << 30


# paths are looked up at call time so tests can point them at tmp_path
def _db_path() -> Path:
    from backend.db import database
    return database.db_path()


def _local_dir() -> Path:
    """Pre-restore copies and uploads in progress (next to the DB, never the cloud folder)."""
    return _db_path().parent / "backups"


def _fit_root() -> Path:
    from backend.sync import storage
    return storage.fit_root()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(e: Exception) -> HTTPException:
    return HTTPException(409 if isinstance(e, B.Busy) else 400, str(e))


async def status(repo: SettingsRepository) -> dict:
    d = await repo.get("backup.dir")
    last_ok, last = await repo.get("backup.last_ok"), await repo.get("backup.last_result")
    backups = await asyncio.to_thread(B.list_backups, Path(d)) if d else []
    return {"dir": d, "auto": await repo.get("backup.auto"),
            "include_fit": await repo.get("backup.include_fit"),
            "last_result": last, "last_ok": last_ok,
            "auto_due": bool(d) and B.auto_due((last_ok or {}).get("at"), (last or {}).get("at")),
            "backups": backups, "keep": {"daily": B.KEEP_DAILY, "weekly": B.KEEP_WEEKLY},
            "cloud": await asyncio.to_thread(B.detect_cloud_folders)}


async def run_backup(db: AsyncSession, trigger: str, athlete_id: int = 1) -> dict:
    """One backup into the configured folder + retention; the outcome is saved
    as backup.last_result (and backup.last_ok when it worked)."""
    repo = SettingsRepository(db, athlete_id)
    at = _now().isoformat()
    result = {"at": at, "trigger": trigger}
    try:
        d = await repo.get("backup.dir")
        if not d:
            raise B.BackupError("還沒選備份資料夾")
        folder = await asyncio.to_thread(B.check_folder, d)
        made = await asyncio.to_thread(
            B.create_backup, _db_path(), folder, fit_root=_fit_root(),
            include_fit=await repo.get("backup.include_fit"))
        pruned = await asyncio.to_thread(B.prune, folder)
        result.update(status="ok", name=made["name"], size=made["size"],
                      fit_files=made["fit_files"], pruned=len(pruned))
        await repo.set("backup.last_ok", {"at": at, "name": made["name"], "size": made["size"]})
    except B.Busy:
        raise
    except Exception as e:                       # every failure is shown on the page
        msg = str(e) if isinstance(e, B.BackupError) else f"{type(e).__name__}: {e}"
        result.update(status="failed", error=msg[:300])
        log.warning("backup (%s) failed: %s", trigger, msg)
    await repo.set("backup.last_result", result)
    await db.commit()
    return result


async def auto_tick(session_factory: Callable, now: Optional[datetime] = None, athlete_id: int = 1):
    """Daily automatic backup (app start + every scheduler minute): only with a
    folder set, backup.auto on, and > 24 h since the last good backup."""
    async with session_factory() as db:
        repo = SettingsRepository(db, athlete_id)
        if not await repo.get("backup.dir") or not await repo.get("backup.auto"):
            return None
        last_ok, last = await repo.get("backup.last_ok"), await repo.get("backup.last_result")
        if not B.auto_due((last_ok or {}).get("at"), (last or {}).get("at"), now):
            return None
        try:
            return await run_backup(db, "auto", athlete_id)
        except B.Busy:
            return None


# ------------------------------------------------------------------ endpoints
@router.get("/status")
async def get_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    return await status(SettingsRepository(db, athlete_id))


class BackupSettingsBody(BaseModel):
    dir: Optional[str] = None
    auto: Optional[bool] = None
    include_fit: Optional[bool] = None


@router.put("/settings")
async def put_settings(body: BackupSettingsBody, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    repo = SettingsRepository(db, athlete_id)
    try:
        if "dir" in body.model_fields_set:
            path = None
            if body.dir:
                path = str(await asyncio.to_thread(B.check_folder, body.dir))
            await repo.set("backup.dir", path)
        if body.auto is not None:
            await repo.set("backup.auto", body.auto)
        if body.include_fit is not None:
            await repo.set("backup.include_fit", body.include_fit)
    except (B.BackupError, ValueError) as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return await status(repo)


@router.post("/run")
async def post_run(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    try:
        r = await run_backup(db, "manual", athlete_id)
    except B.Busy as e:
        raise _err(e)
    return {**r, "status_all": await status(SettingsRepository(db, athlete_id))}


def _staging() -> Path:
    d = _local_dir() / "staging"
    d.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 86400
    for p in d.glob("*.bin"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass
    return d


@router.post("/upload")
async def post_upload(request: Request):
    """A backup file the user picked in the browser (raw body). Returns an id
    for /inspect and /restore; uploads are dropped after a day."""
    uid = uuid.uuid4().hex
    dst = _staging() / f"{uid}.bin"
    size = 0
    with open(dst, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD:
                f.close()
                dst.unlink()
                raise HTTPException(413, "檔案太大")
            f.write(chunk)
    if not size:
        dst.unlink()
        raise HTTPException(400, _("檔案是空的"))
    if B.is_legacy_encrypted(dst):
        dst.unlink()
        raise HTTPException(400, B.LEGACY_ENCRYPTED)
    return {"upload_id": uid, "size": size}


class SourceBody(BaseModel):
    name: Optional[str] = None           # a backup in the configured folder
    upload_id: Optional[str] = None      # or one uploaded via /upload


async def _source(body: SourceBody, repo: SettingsRepository) -> Path:
    if body.upload_id:
        if not UPLOAD_RE.match(body.upload_id):
            raise HTTPException(400, "upload_id 不正確")
        p = _local_dir() / "staging" / f"{body.upload_id}.bin"
    elif body.name:
        d = await repo.get("backup.dir")
        if d and body.name.endswith(".zip.enc"):
            raise HTTPException(400, B.LEGACY_ENCRYPTED)
        if not d or not B.NAME_RE.match(body.name):
            raise HTTPException(400, "只能還原備份資料夾裡由這個功能建立的檔案")
        p = Path(d) / body.name
    else:
        raise HTTPException(400, "請選擇備份檔")
    if not p.is_file():
        raise HTTPException(404, _("找不到備份檔"))
    return p


@router.post("/inspect")
async def post_inspect(body: SourceBody, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    p = await _source(body, SettingsRepository(db, athlete_id))
    work = _local_dir()
    work.mkdir(parents=True, exist_ok=True)
    try:
        info = await asyncio.to_thread(B.inspect, p, work)
    except B.BackupError as e:
        raise _err(e)
    return info


async def after_restore() -> None:
    """Drop pooled connections (they'd keep the old pages cached), upgrade an
    older schema, clear in-memory caches that read the DB."""
    from backend.db import database
    await database.dispose()
    await database.init_db()
    try:
        from backend.engine import activity_tags
        activity_tags._memo.clear()
        activity_tags._rec_memo.clear()
    except Exception:
        pass


@router.post("/restore")
async def post_restore(body: SourceBody, athlete_id: int = 1):
    from backend.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        p = await _source(body, SettingsRepository(db, athlete_id))
    try:
        r = await asyncio.to_thread(B.restore, p, _db_path(), local_dir=_local_dir(),
                                    fit_root=_fit_root())
    except B.BackupError as e:
        raise _err(e)
    await after_restore()
    if body.upload_id:
        try:
            p.unlink()
        except OSError:
            pass
    return {**r, "restart_recommended": True}
