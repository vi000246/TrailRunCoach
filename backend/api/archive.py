"""
運動紀錄保留 (SP-317, engine/fit_archive.py) on the settings page (資料同步 tab):

    GET  /api/v1/archive/status     retention (years, choices), the last cleanup, the next run
    PUT  /api/v1/archive/settings   {"retention_years": 2 | 3 | 5 | 10} (a subscription; the free
                                    tier's only choice is 1, SP-713 — a longer one: 403 history:full)
    GET  /api/v1/archive/fit.zip    「下載我的全部 FIT」: every FIT of the current tenant, streamed

The zip is written as it is sent (zipfile on a non-seekable sink, one FIT in memory at a time),
so a large account never sits in memory or on disk. It holds only the current tenant's fit/
folder (files/fitstore.LocalDirStore: symlinks and half-written files left out), each FIT
gunzipped and named by its start (workout_files) when known. The demo never mounts this router
(main.py).
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import sqlite3
import zipfile
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.engine import fit_archive as FA
from backend.settings.repository import SettingsRepository

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/archive", tags=["archive"])


def _tenant():
    from backend import tenancy
    t = tenancy.current()
    if t.is_demo or not t.can("settings"):
        raise HTTPException(403, "not available")
    return t


async def _status(repo: SettingsRepository) -> dict:
    years = await repo.get(FA.RETENTION_KEY)
    nxt = None
    try:
        nxt = FA.next_run().isoformat(timespec="minutes")
    except Exception:                              # noqa: BLE001 — the page shows no date then
        pass
    out = {"retention_years": years, "choices": list(FA.CHOICES), "default": FA.default_years(),
           "min_years": FA.MIN_YEARS, "last": _with_local_date(FA.last_run()), "next_run": nxt}
    # SP-713: the plan's cap — the free tier keeps 1 year (its only choice); a longer retention it
    # had is cut only from the monthly run named in `cap_notice` (told first, SP-317)
    cap = FA.plan_cap()
    if cap is not None:
        out.update(retention_years=cap, choices=list(FA.FREE_CHOICES), default=cap, min_years=cap, cap=cap)
        n = FA.cap_notice()
        if n is not None and n["from"] > dt.datetime.now(FA._tz()).date().isoformat():
            out["cap_notice"] = n
    return out


def _with_local_date(last):
    """The run's day in the athlete's zone (`date_local`): `at` is UTC, so a run on the 1st at
    03:00 Taipei is the 31st's evening there — the page shows this field, never `at`'s prefix."""
    if not last:
        return last
    out = dict(last)
    try:
        out["date_local"] = dt.datetime.fromisoformat(str(last["at"])).astimezone(FA._tz()).date().isoformat()
    except (KeyError, TypeError, ValueError):
        out["date_local"] = None
    return out


@router.get("/status")
async def get_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    _tenant()
    return await _status(SettingsRepository(db, athlete_id))


class ArchiveSettingsBody(BaseModel):
    retention_years: int


@router.put("/settings")
async def put_settings(body: ArchiveSettingsBody, athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
    _tenant()
    repo = SettingsRepository(db, athlete_id)
    cap = FA.plan_cap()
    if cap is not None:                            # SP-713: the free tier keeps 1 year, nothing to store
        if body.retention_years == cap:
            return await _status(repo)
        if body.retention_years in FA.CHOICES:
            from backend import membership as MS
            raise MS.scope_error("history:full")     # 403 with the upgrade hint
        raise HTTPException(400, f"{FA.RETENTION_KEY} must be {cap}")
    try:
        await repo.set(FA.RETENTION_KEY, body.retention_years)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.commit()
    return await _status(repo)


# ---------------------------------------------------------------------------
# 下載我的全部 FIT
# ---------------------------------------------------------------------------

class _Sink(io.RawIOBase):
    """A write-only, non-seekable buffer the zip writes into; `take` hands over what it holds."""

    def __init__(self):
        super().__init__()
        self.buf = bytearray()
        self.n = 0

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self.buf += b
        self.n += len(b)
        return len(b)

    def tell(self) -> int:
        return self.n

    def take(self) -> bytes:
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _starts() -> dict:
    """{workout_files.file_path: start (UTC, naive)} of the current tenant's DB, read only."""
    try:
        from backend.db.database import db_path
        p = db_path()
        if not Path(p).exists():
            return {}
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT file_path, start_time_utc FROM workout_files").fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError):
        return {}
    out = {}
    for fp, st in rows:
        try:
            out[fp] = dt.datetime.fromisoformat(str(st)) if st else None
        except ValueError:
            out[fp] = None
    return out


def export_list() -> list[tuple[Path, str]]:
    """(the stored file, its name in the zip) of every FIT in the current tenant's fit/ folder."""
    from backend.files import fitstore
    from backend.sync import storage
    store = fitstore.LocalDirStore(storage.fit_root())
    starts = _starts()
    out, used = [], set()
    for folder in storage.SOURCES:
        if not storage.source_dir(folder).is_dir():
            continue
        for o in store.list(folder):
            name = o.key.split("/")[-1]
            if not (name.endswith(".fit") or name.endswith(".fit.gz")):
                continue
            sha = fitstore.sha_of_key(o.key)
            st = starts.get(o.key)
            if sha and st is not None:
                arc = f"{folder}/{st:%Y}/{st:%Y-%m-%d_%H%M}Z_{sha[:8]}.fit"
            elif sha:
                arc = f"{folder}/{sha}.fit"
            else:
                arc = o.key[:-3] if o.key.endswith(".gz") else o.key
            while arc in used:
                arc = arc[:-4] + "_1.fit"
            used.add(arc)
            out.append((store.path(o.key), arc))
    return out


def stream_zip(items: list[tuple[Path, str]]) -> Iterator[bytes]:
    """The zip of `items`, chunk by chunk (one FIT in memory at a time)."""
    from backend.files import fitstore
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path, arc in items:
            try:
                raw = fitstore.gunzip_if(Path(path).read_bytes())
            except OSError:
                continue                           # gone meanwhile (an archive, a deletion)
            zf.writestr(arc, raw)
            yield sink.take()
    yield sink.take()


@router.get("/fit.zip")
def download_fit():
    _tenant()
    items = export_list()
    log.info("FIT export: %d files", len(items))
    name = f"fit-{dt.date.today().isoformat()}.zip"
    return StreamingResponse(stream_zip(items), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"',
                                      "Cache-Control": "private, no-store"})
