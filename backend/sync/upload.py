"""
手動匯入 FIT (SP-323; docs/research/fit-on-device.md §4, §12 單 3): the FIT files a person picks in
the browser — a few files, or a whole folder (`webkitdirectory`) — become activities of the
current tenant. For whoever does not sync from COROS (a Garmin / other platform export) and for
filling in old history.

Each file, in the order sent (one bad file never fails the others):

  1. the name says FIT (.fit / .fit.gz) or a track file (.tcx / .gpx, also gzipped: SP-349, what
     Garmin's and TrainingPeaks' exports hold besides FIT) — anything else is `skipped` (the page
     lists it)
  2. at most MAX_FILE_BYTES (a track file MAX_TRACK_BYTES; also after gunzip), not empty; a track
     file is turned into a FIT here (files/track_fit.py); a FIT header (".FIT" at byte 8)
  3. the same content already an activity (its sha256 names the object, SP-308) -> `duplicate`
     (same_file); nothing parsed, nothing stored: uploading the same folder again is cheap
  4. parsed once (fit_reader.parse_fit_bytes); an activity file (file_id type activity or
     unknown, a start time, records or a session) — else `failed`
  5. stored as the hash-named object in the 資料來源's folder (fitstore.store_fit:
     fit/<coros|garmin|tp>/objects/<aa>/<sha256>.fit.gz) — the chart Dataset reads only that
     folder (wko5expr/datasource.py), so the import shows in the charts, the 活動列表 and the
     overview like a synced activity — and recorded as a workout_files row with source "upload"
     (file_service._import_one_file: date, sport, trail / road, RPE, then sync/dedup.resolve)
  6. the same run already there from another source (dedup.same_activity: start ±2 min, span,
     distance, sport family) -> the row is kept as a copy (`duplicate_of`, counted once, the
     user can split it in the activity editor) and the file is reported `duplicate`
     (same_activity); the 資料來源's copy always wins (dedup.choose_canonical), so a COROS sync
     of the same run later makes the COROS row the counted one — and identical bytes synced
     later are found by their hash and not recorded again (coros_client: same_content)

The 資料來源 switches (PUT /sync/settings): `rehome` moves the imported objects to the new
source's folder, so they stay in the charts.

The endpoints (api/sync.py) check the `upload.fit` capability (tenancy.py; never in the demo:
DEMO_CAPS, and the demo mounts no sync router) and the `import:fit` scope (membership.py), take
at most MAX_FILES files / MAX_REQUEST_BYTES per request — the page sends a big folder in batches
of BATCH_BYTES — and after the last batch run what a sync runs (runner.after_import: Dataset
warm-up, overview status, plan inputs, the automatic plan, the calibration). Counts and sizes
only in the logs (file names can be personal).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import zlib
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import FitObject, WorkoutFile
from backend.files import fitstore

log = logging.getLogger(__name__)

SOURCE = "upload"                       # workout_files.source of an imported file
FIT_EXTS = (".fit", ".fit.gz")
TRACK_EXTS = (".tcx", ".tcx.gz", ".gpx", ".gpx.gz")     # turned into FIT (SP-349, files/track_fit.py)
EXTS = FIT_EXTS + TRACK_EXTS
# FIT files are small: the owner's median is 93 KB, 3 years 118 MB in 641 files (SP-319 §1); a
# multi-day trip recorded every second is the big case (a few MB). 推估 with a wide margin.
MAX_FILE_BYTES = 20 * 1024 * 1024       # one file (and its FIT after gunzip)
# a TCX / GPX holds ~5–10× the bytes of the same FIT (XML per point): a 30-hour race at 1 s ≈ 45 MB
MAX_TRACK_BYTES = 64 * 1024 * 1024
MAX_FILES = 20                          # files per request
MAX_REQUEST_BYTES = 100 * 1024 * 1024   # one request's body (refused above it)
MAX_SELECT = 3000                       # files per pick in the page (≈ 10 years of a heavy user)
# what the page puts in one request: parsing takes ~2 s per MB of FIT here and the NAS is ~8.5×
# slower (推估, 2026-10-12), so ~2 MB keeps a batch well inside a proxy's ~100 s response limit
BATCH_BYTES = 2 * 1024 * 1024

# per file: status added | duplicate | failed | skipped, and why
ADDED, DUPLICATE, FAILED, SKIPPED = "added", "duplicate", "failed", "skipped"
SAME_FILE, SAME_ACTIVITY = "same_file", "same_activity"
NOT_FIT, TOO_LARGE, EMPTY, UNREADABLE, NOT_ACTIVITY, ERROR = (
    "not_fit", "too_large", "empty", "unreadable", "not_activity", "error")


@dataclass(frozen=True)
class Incoming:
    name: str                    # the file name (a folder pick: its path inside the folder)
    data: Optional[bytes]        # None: not read (over MAX_FILE_BYTES)
    size: int


class UploadBusy(RuntimeError):
    """This tenant is importing already (another tab)."""


def limits() -> dict:
    """The caps the page batches by (GET /sync/upload/fit)."""
    return {"max_file_bytes": MAX_FILE_BYTES, "max_track_bytes": MAX_TRACK_BYTES, "max_files": MAX_FILES,
            "max_request_bytes": MAX_REQUEST_BYTES, "batch_bytes": BATCH_BYTES, "max_select": MAX_SELECT,
            "exts": list(EXTS)}


def is_fit_name(name: str) -> bool:
    """A name this import takes: FIT, or a TCX / GPX track file (gzipped too)."""
    n = (name or "").strip().lower()
    return any(n.endswith(e) for e in EXTS)


def is_track_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(n.endswith(e) for e in TRACK_EXTS)


def max_bytes(name: str) -> int:
    """The size cap of one file of this name (and of its content after gunzip)."""
    return MAX_TRACK_BYTES if is_track_name(name) else MAX_FILE_BYTES


_BUSY: set[str] = set()


@contextlib.contextmanager
def claim(tenant_id: str) -> Iterator[None]:
    """One import at a time per tenant (single-threaded asyncio: check-and-add is atomic)."""
    if tenant_id in _BUSY:
        raise UploadBusy(tenant_id)
    _BUSY.add(tenant_id)
    try:
        yield
    finally:
        _BUSY.discard(tenant_id)


# ---------------------------------------------------------------------------- one file's bytes

class _Bad(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _gunzip(blob: bytes, cap: int) -> bytes:
    """`blob` with gzip unpacked, never inflated past `cap` (a gzip bomb); _Bad otherwise."""
    if blob[:2] != fitstore.GZIP_MAGIC:
        return blob
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = d.decompress(blob, cap + 1)
    except zlib.error:
        raise _Bad(NOT_FIT)
    if len(raw) > cap or d.unconsumed_tail:
        raise _Bad(TOO_LARGE)
    if not d.eof:
        raise _Bad(NOT_FIT)                       # a cut / broken gzip
    if not raw:
        raise _Bad(EMPTY)
    return raw


def _track_fit(blob: bytes, name: str) -> bytes:
    """A TCX / GPX file (gzip unpacked, at most MAX_TRACK_BYTES) as FIT bytes; _Bad otherwise
    (CPU: run in a thread)."""
    from backend.files import track_fit
    if not blob:
        raise _Bad(EMPTY)
    raw = _gunzip(blob, MAX_TRACK_BYTES)
    try:
        return track_fit.to_fit(raw, track_fit.kind_of(name))
    except track_fit.TrackError as e:
        raise _Bad(e.reason)


def _fit_bytes(blob: bytes) -> bytes:
    """The FIT bytes of an uploaded file (gzip unpacked, at most MAX_FILE_BYTES); _Bad otherwise."""
    if not blob:
        raise _Bad(EMPTY)
    blob = _gunzip(blob, MAX_FILE_BYTES)
    if len(blob) < 12 or blob[0] not in (12, 14) or blob[8:12] != b".FIT":
        raise _Bad(NOT_FIT)
    return blob


def _file_type(raw: bytes) -> Optional[str]:
    """file_id.type ("activity", "course", "monitoring_b" …) or None when it can't be read: the
    first message, so only the head of the file is decoded."""
    import io
    try:
        import fitparse
        f = fitparse.FitFile(io.BytesIO(raw), check_crc=False)
        for m in f.get_messages("file_id"):
            v = m.get_value("type")
            return None if v is None else str(v)
    except Exception:                       # noqa: BLE001 — a COROS oddity: the full parse decides
        return None
    return None


def _parse(raw: bytes, name: str):
    """The RawWorkout of an activity FIT; _Bad otherwise (CPU: run in a thread)."""
    from backend.files.fit_reader import parse_fit_bytes
    kind = _file_type(raw)
    if kind not in (None, "activity"):
        raise _Bad(NOT_ACTIVITY)
    try:
        w = parse_fit_bytes(raw, source_file=name)
    except Exception:                       # noqa: BLE001 — both parsers refused it
        raise _Bad(UNREADABLE)
    if w.start_time is None or (len(w.time_s) == 0 and not w.session):
        raise _Bad(NOT_ACTIVITY)
    return w


# ---------------------------------------------------------------------------- the DB side

def _keys(sha: str) -> list[str]:
    """Every object key this content can have (one per source folder)."""
    from backend.sync import storage
    return [fitstore.object_key(f, sha) for f in storage.SOURCES]


async def _row_with(db: AsyncSession, athlete_id: int, sha: str) -> Optional[WorkoutFile]:
    """The activity already recorded for this content (any source), if any."""
    return (await db.execute(select(WorkoutFile).where(
        WorkoutFile.athlete_id == athlete_id, WorkoutFile.file_path.in_(_keys(sha))))).scalars().first()


async def _folder(db: AsyncSession, athlete_id: int) -> str:
    """The 資料來源's folder ("coros" / "garmin" / "tp"): the one the charts read."""
    from backend.sync import primary as P
    return P.FOLDER[await P.current(db, athlete_id)]


def _day(wf: Optional[WorkoutFile]) -> Optional[str]:
    return wf.workout_date.isoformat() if wf is not None and wf.workout_date else None


async def _move(db: AsyncSession, wf: WorkoutFile, folder: str, store: fitstore.LocalDirStore) -> bool:
    """Put an imported row's object into `folder` (the new 資料來源's) and point the row at it.
    The old file goes when no other row points at it. False when there is nothing to move."""
    from backend.sync import storage
    old = wf.file_path
    sha = fitstore.sha_of_key(old)
    if sha is None or fitstore.folder_of_key(old) == folder:
        return False
    new = fitstore.object_key(folder, sha)
    store.put(new, store.get(old))
    wf.file_path = new
    obj = (await db.execute(select(FitObject).where(FitObject.sha256 == sha))).scalar_one_or_none()
    if obj is not None:
        obj.source = storage.SOURCES[folder]          # where later stores of this content look (store_fit)
    await db.flush()
    if (await db.execute(select(WorkoutFile.id).where(WorkoutFile.file_path == old))).first() is None:
        try:
            store.delete(old)
        except (OSError, ValueError):
            pass
    return True


async def import_one(db: AsyncSession, athlete_id: int, f: Incoming, folder: str) -> dict:
    """One file -> {name, status, reason, date, sport, of}. Commits its own work (or rolls it
    back); never raises for a bad file."""
    from backend.files import file_service as FS
    out = {"name": f.name, "status": FAILED, "reason": None, "date": None, "sport": None, "of": None}
    if not is_fit_name(f.name):
        return {**out, "status": SKIPPED, "reason": NOT_FIT}
    cap = max_bytes(f.name)
    if f.data is None or f.size > cap or len(f.data) > cap:
        return {**out, "reason": TOO_LARGE}
    try:
        if is_track_name(f.name):
            raw = _fit_bytes(await asyncio.to_thread(_track_fit, f.data, f.name))
        else:
            raw = _fit_bytes(f.data)
    except _Bad as e:
        return {**out, "reason": e.reason}
    sha = fitstore.sha256_hex(raw)
    have = await _row_with(db, athlete_id, sha)
    if have is not None:                          # the very same file: nothing to parse or store
        return {**out, "status": DUPLICATE, "reason": SAME_FILE, "date": _day(have), "sport": have.sport,
                "of": have.source}
    try:
        parsed = await asyncio.to_thread(_parse, raw, f.name)
    except _Bad as e:
        return {**out, "reason": e.reason}
    store = fitstore.default_store()
    stored = None
    try:
        stored = await fitstore.store_fit(db, raw, folder, store=store)
        wf = await FS._import_one_file(db, athlete_id, stored.key, source=SOURCE, parsed=parsed)
        if wf is None:
            raise RuntimeError("not imported")
        if fitstore.folder_of_key(stored.key) != folder:
            # content stored before under another source's folder (its activity deleted since):
            # the charts read only the 資料來源's folder
            await _move(db, wf, folder, store)
        await db.commit()
    except Exception as e:                        # noqa: BLE001 — this file only; the batch goes on
        log.warning("FIT import failed: %s", type(e).__name__)
        await db.rollback()
        if stored is not None and stored.created and await fitstore.live_row(db, stored.key) is None:
            # a file without a row would still be read as an activity by the Dataset
            try:
                store.delete(stored.key)
            except (OSError, ValueError):
                pass
        return {**out, "reason": ERROR}
    res = {**out, "status": ADDED, "date": _day(wf), "sport": wf.sport}
    if wf.duplicate_of is not None:
        canon = await db.get(WorkoutFile, wf.duplicate_of)
        res.update(status=DUPLICATE, reason=SAME_ACTIVITY, of=canon.source if canon is not None else None)
    return res


async def _incoming(u) -> Incoming:
    """An uploaded file (starlette UploadFile: spooled to disk) read up to the cap, one at a time;
    a name that is not FIT is never read."""
    name = getattr(u, "filename", None) or ""
    size = int(getattr(u, "size", None) or 0)
    if not is_fit_name(name):
        return Incoming(name, b"", size)
    cap = max_bytes(name)
    data = await u.read(cap + 1)
    return Incoming(name, data if len(data) <= cap else None, max(size, len(data)))


async def import_files(db: AsyncSession, athlete_id: int, files: Iterable) -> dict:
    """Import every file (in order): Incoming, or uploaded files (UploadFile). {added, duplicate,
    failed, skipped, folder, items: [...]}."""
    folder = await _folder(db, athlete_id)
    await db.commit()                             # the 資料來源 migration, if it wrote one
    items = []
    for f in files:
        if not isinstance(f, Incoming):
            f = await _incoming(f)
        items.append(await import_one(db, athlete_id, f, folder))
    count = {k: sum(1 for i in items if i["status"] == k) for k in (ADDED, DUPLICATE, FAILED, SKIPPED)}
    log.info("FIT import: %s", ", ".join(f"{k} {v}" for k, v in count.items()))
    return {**count, "folder": folder, "items": items}


async def stats(db: AsyncSession, athlete_id: int) -> dict:
    """The imported activities (rows, date span) and the limits, for the page."""
    n, d0, d1 = (await db.execute(select(
        func.count(WorkoutFile.id), func.min(WorkoutFile.workout_date), func.max(WorkoutFile.workout_date))
        .where(WorkoutFile.athlete_id == athlete_id, WorkoutFile.source == SOURCE))).one()
    iso = (lambda d: d if isinstance(d, str) or d is None else d.isoformat())
    return {"rows": int(n or 0), "date_from": iso(d0), "date_to": iso(d1), "limits": limits()}


async def rehome(db: AsyncSession, athlete_id: int) -> dict:
    """After the 資料來源 changed: the imported objects into its folder (flush only; the caller
    commits). {moved, folder}."""
    from backend.engine.wko5expr.datasource import files_changed
    folder = await _folder(db, athlete_id)
    store = fitstore.default_store()
    moved = 0
    rows = (await db.execute(select(WorkoutFile).where(
        WorkoutFile.athlete_id == athlete_id, WorkoutFile.source == SOURCE))).scalars().all()
    for wf in rows:
        if not fitstore.is_object_key(wf.file_path):
            continue
        try:
            moved += int(await _move(db, wf, folder, store))
        except (OSError, ValueError) as e:        # a missing object: left where it is
            log.warning("imported FIT not moved: %s", type(e).__name__)
    if moved:
        files_changed()
        log.info("imported FITs moved to the %s folder: %d", folder, moved)
    return {"moved": moved, "folder": folder}
