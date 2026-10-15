"""
匯入匯出檔 (SP-349): a platform's whole-account export — a ZIP — becomes activities of the current
tenant, to fill in the history a sync cannot reach (intervals.icu: COROS 3 months / Garmin 1 year;
Polar's API: 30 days; COROS MCP: 50 files a day).

What it takes (推估 from the public descriptions, no real export at hand — the report of SP-349
has the sources):
  * Garmin 「Export Your Data」: <id>.zip -> DI_CONNECT/DI-Connect-Uploaded-Files/*.zip (nested
    ZIPs, e.g. UploadedFiles_0-_Part1.zip) holding every upload in its original format: .fit,
    .tcx, .gpx (and .txt). The rest of the export (DI-Connect-Fitness JSON, wellness, user data)
    is no activity file: skipped and listed.
  * a ZIP of FIT / TCX / GPX files the user packed (SP-407), a TrainingPeaks workout-file export
    zipped (.fit / .fit.gz / .tcx / .gpx: "the same format as it was uploaded"), COROS's bulk
    export (FIT or TCX), Polar Flow's per-session exports zipped (TCX / GPX / FIT).
  * not (yet): Polar's account download (JSON training sessions) and Apple Health's export.zip
    (export.xml + workout-routes/*.gpx without heart rate or sport): recognised, skipped with
    their own reason, so the page can say so.

The flow:
  1. create(): one import per tenant at a time, ≤ MAX_ARCHIVE_BYTES. The page sends the file in
     CHUNK_BYTES pieces (append(): PUT at an offset — far below a proxy's ~100 MB request cap, and
     an upload cut off resumes at `received`, also after a reload with the same file).
  2. the last piece queues the work in the server's job queue (backend/jobs.py, class `single`,
     priority `sync_hot`) in parts of ≤ PART_BYTES / PART_FILES activity files, so a big export
     never holds a place for long; each part runs in a worker process (backend/workproc.py: FIT
     parsing holds the GIL) and the next part is queued when it ends.
  3. a part: the first one lists the archive (scan(): manifest.json; nested ZIPs copied to
     nested/<k>.zip under our own names), then each activity file goes through SP-323's import
     (sync/upload.import_one: hash de-dup, TCX / GPX -> FIT, the 資料來源's folder, dedup.resolve
     against the synced runs — only the current tenant's DB and folder), its result appended to
     items.jsonl. A bad file fails alone; a FIT that is no activity (monitoring, a course) is
     skipped and listed.
  4. after the last part: the steps after a sync, once (runner.after_import on the server's loop).
     The archive itself is deleted (only the results stay) unless files failed for a reason a
     retry may cure.

Retry (`retry()`): the work stopped (the server restarted, a worker died — the status says
`interrupted`) or files failed with `error`: the next parts take what has no result yet; what was
imported is never imported twice (results by entry, and the hash de-dup).

Untrusted archive (a ZIP bomb, path tricks): nothing is ever extracted under an entry's name (no
path traversal: the entry names are only shown), the entry count is read from the end-of-central-
directory record before the ZIP is opened (≤ MAX_ENTRIES), nested ZIPs at most MAX_DEPTH deep,
every read is capped while it inflates (an activity file at upload.max_bytes, a nested ZIP at
MAX_NESTED_BYTES) and all inflated bytes together at MAX_INFLATE_BYTES; a file that is not an
activity file is never inflated.

Files (per tenant, private: data_registry `imports/**`): imports/<id>/{state.json, archive.zip,
nested/<k>.zip, manifest.json, items.jsonl}. Counts and sizes only in the logs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import struct
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from backend.sync import upload as UP

log = logging.getLogger(__name__)

ROOT: Optional[Path] = None             # tests: a fixed folder; None = <tenant root>/imports
EXTS = (".zip",)
MAX_ARCHIVE_BYTES = 4 * 1024 ** 3       # one upload (Garmin's export of many years: ~1–3 GB, 推估)
CHUNK_BYTES = 8 * 1024 * 1024           # one PUT
MAX_DEPTH = 2                           # ZIPs inside the archive, and ZIPs inside those
MAX_ENTRIES = 200_000                   # entries of one ZIP (read before it is opened)
MAX_ACTIVITY_FILES = 30_000             # activity files of one import
MAX_NESTED_BYTES = MAX_ARCHIVE_BYTES    # one nested ZIP, inflated
MAX_INFLATE_BYTES = 12 * 1024 ** 3      # everything inflated by one import together
# one queue job's share: ~2 s per MB of FIT here, the NAS ~8.5× slower (SP-323) -> ~1 min there
PART_BYTES = 4 * 1024 * 1024
PART_FILES = 100
KEEP_UNFINISHED_DAYS = 7                # an upload never finished: dropped by the next create()
LIST_MAX = 500                          # names kept per skipped reason (the rest as a count)
READ_STEP = 1024 * 1024

# states
UPLOADING, QUEUED, RUNNING, DONE, FAILED, INTERRUPTED = (
    "uploading", "queued", "running", "done", "failed", "interrupted")
# kinds (what the archive looks like)
GARMIN, POLAR, APPLE_HEALTH, GENERIC = "garmin", "polar", "apple_health", "generic"
# skipped / failed reasons of this module (the rest: sync/upload.py's)
NOT_ACTIVITY_FILE, UNSUPPORTED, POLAR_JSON, APPLE_ROUTE, TOO_DEEP, TOO_MANY = (
    "not_activity_file", "unsupported_format", "polar_json", "apple_health", "too_deep", "too_many_files")
NOT_ZIP, BOMB, TOO_MANY_ENTRIES = "not_zip", "too_large_inflated", "too_many_entries"
RETRYABLE = {UP.ERROR}
# activity formats this import cannot read (listed as such, not as 「不是活動檔」)
UNSUPPORTED_EXTS = (".pwx", ".kml", ".kmz", ".srm", ".hrm", ".csv", ".slf", ".sml", ".xml")

_LOCK = threading.RLock()
_LIVE: dict = {}                        # (tenant id, import id) -> the latest queued / running job
_LOOPS: dict = {}                       # tenant id -> the server's event loop (after_import runs there)


class ArchiveError(Exception):
    def __init__(self, reason: str, **params: Any):
        super().__init__(reason)
        self.reason = reason
        self.params = params


# ---------------------------------------------------------------------------- the folder

def root() -> Path:
    if ROOT is not None:
        return Path(ROOT)
    from backend import tenancy
    return tenancy.current().root / "imports"


def _dir(imp_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{16}", imp_id or ""):
        raise ArchiveError("not_found")
    return root() / imp_id


def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(p: Path, obj) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
    os.replace(tmp, p)


def _state(d: Path) -> Optional[dict]:
    return _read_json(d / "state.json")


def _save(d: Path, st: dict) -> None:
    _write_json(d / "state.json", st)


def _imports() -> list[Path]:
    r = root()
    if not r.is_dir():
        return []
    out = [p for p in r.iterdir() if p.is_dir() and (p / "state.json").is_file() and not (p / "cancel").exists()]
    return sorted(out, key=lambda p: (_state(p) or {}).get("created", 0))


def _tid() -> str:
    from backend import tenancy
    try:
        return tenancy.current().id
    except Exception:                       # noqa: BLE001
        return "default"


def _live(imp_id: str):
    j = _LIVE.get((_tid(), imp_id))
    return j if j is not None and j.is_alive() else None


def is_archive_name(name: str) -> bool:
    return (name or "").strip().lower().endswith(EXTS)


def limits() -> dict:
    return {"max_archive_bytes": MAX_ARCHIVE_BYTES, "chunk_bytes": CHUNK_BYTES, "exts": list(EXTS),
            "max_activity_files": MAX_ACTIVITY_FILES}


# ---------------------------------------------------------------------------- the upload

def create(name: str, size: int, athlete_id: int) -> dict:
    """A new import (or the unfinished upload of the same file, to resume). ArchiveError:
    not_zip, too_large, empty, busy (another import is queued / running)."""
    name = _clean(name)
    if not is_archive_name(name):
        raise ArchiveError(NOT_ZIP)
    if size <= 0:
        raise ArchiveError(UP.EMPTY)
    if size > MAX_ARCHIVE_BYTES:
        raise ArchiveError(UP.TOO_LARGE)
    with _LOCK:
        now = time.time()
        for d in _imports():
            st = _state(d) or {}
            if st.get("state") in (QUEUED, RUNNING) and _live(d.name) is not None:
                raise ArchiveError("busy")
            if st.get("state") == UPLOADING and st.get("name") == name and st.get("size") == size \
                    and now - st.get("created", 0) < KEEP_UNFINISHED_DAYS * 86400:
                return status(d.name)
        for d in _imports():                # one import per tenant: the older ones go
            shutil.rmtree(d, ignore_errors=True)
        imp_id = secrets.token_hex(8)
        d = root() / imp_id
        d.mkdir(parents=True)
        (d / "archive.zip").write_bytes(b"")
        _save(d, {"id": imp_id, "name": name, "size": int(size), "received": 0, "state": UPLOADING,
                  "created": now, "athlete_id": int(athlete_id), "kind": None, "error": None})
    log.info("export import created: %d bytes", size)
    return status(imp_id)


def append(imp_id: str, offset: int, data: bytes) -> dict:
    """One piece of the upload at `offset` (= what was received so far). The status, with
    `start` true once the file is complete and is a ZIP (the caller queues the work: start()).
    ArchiveError: not_found, offset (params: received), too_large, not_zip."""
    d = _dir(imp_id)
    with _LOCK:
        st = _state(d)
        if st is None:
            raise ArchiveError("not_found")
        if st["state"] != UPLOADING:
            raise ArchiveError("offset", received=st["received"])
        if offset != st["received"]:
            raise ArchiveError("offset", received=st["received"])
        if len(data) > CHUNK_BYTES or st["received"] + len(data) > st["size"]:
            raise ArchiveError(UP.TOO_LARGE)
        with open(d / "archive.zip", "r+b") as f:
            f.seek(offset)
            f.write(data)
            f.truncate()
        st["received"] = offset + len(data)
        if st["received"] == st["size"]:
            ok = zipfile.is_zipfile(d / "archive.zip")
            st["state"] = QUEUED if ok else FAILED
            st["error"] = None if ok else NOT_ZIP
            if not ok:
                _drop_data(d)
        _save(d, st)
        ready = st["state"] == QUEUED
    out = status(imp_id)
    out["start"] = ready                    # complete and a ZIP: the caller queues the work (start())
    return out


def _drop_data(d: Path) -> None:
    """The archive and its nested ZIPs go; the results stay."""
    for p in (d / "archive.zip",):
        try:
            p.unlink()
        except OSError:
            pass
    shutil.rmtree(d / "nested", ignore_errors=True)


def delete(imp_id: str) -> bool:
    """Forget an import (a queued / running one stops after its current file)."""
    d = _dir(imp_id)
    if not d.exists():
        return False
    with _LOCK:
        (d / "cancel").touch()
        if _live(imp_id) is None:
            shutil.rmtree(d, ignore_errors=True)
    return True


# ---------------------------------------------------------------------------- the status

def _items(d: Path) -> list[dict]:
    """The per-file results (items.jsonl; a line cut short by a crash is ignored)."""
    out = []
    try:
        with open(d / "items.jsonl", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _repair(d: Path) -> None:
    """A result line cut short (the server died while writing it) goes, so the next one starts on
    a line of its own."""
    p = d / "items.jsonl"
    try:
        with open(p, "rb+") as f:
            data = f.read()
            if data and not data.endswith(b"\n"):
                f.truncate(data.rfind(b"\n") + 1)
    except OSError:
        pass


def _counts(items: list[dict], manifest: Optional[dict]) -> dict:
    c = {k: sum(1 for i in items if i.get("status") == k) for k in (UP.ADDED, UP.DUPLICATE, UP.FAILED, UP.SKIPPED)}
    if manifest:
        c[UP.SKIPPED] += sum(v["n"] for v in manifest.get("skipped", {}).values())
    return c


def status(imp_id: Optional[str] = None, items: bool = True) -> Optional[dict]:
    """The import (the tenant's latest when no id): its state, progress and results.
    `interrupted`: queued / running on disk but no job in this server (restarted, a worker died)."""
    if imp_id is None:
        ds = _imports()
        if not ds:
            return None
        d = ds[-1]
        imp_id = d.name
    else:
        d = _dir(imp_id)
    st = _state(d)
    if st is None:
        return None
    job = _live(imp_id)
    state = st["state"]
    if state in (QUEUED, RUNNING) and job is None:
        state = INTERRUPTED
    elif job is not None:
        state = RUNNING if job.state == "running" else QUEUED
    man = _read_json(d / "manifest.json")
    its = _items(d)
    out = {"id": imp_id, "name": st["name"], "size": st["size"], "received": st["received"], "state": state,
           "kind": (man or {}).get("kind") or st.get("kind"), "error": st.get("error"),
           "total": len(man["entries"]) if man else None, "done": len(its), "finished": st.get("finished"),
           "position": None, "limits": limits(), **_counts(its, man)}
    if job is not None and state == QUEUED:
        from backend import jobs
        out["position"] = jobs.position(job)
    out["retryable"] = sum(1 for i in its if i.get("status") == UP.FAILED and i.get("reason") in RETRYABLE)
    out["can_retry"] = state == INTERRUPTED or (state == DONE and out["retryable"] > 0
                                               and (d / "archive.zip").exists())
    if items and state not in (QUEUED, RUNNING):
        out["items"] = [{k: i.get(k) for k in ("name", "status", "reason", "date", "sport", "of")}
                        for i in its if i.get("status") != UP.ADDED][:3 * LIST_MAX]
        out["skipped_files"] = (man or {}).get("skipped", {})
    return out


def latest() -> Optional[dict]:
    return status(None)


# ---------------------------------------------------------------------------- the archive

def _clean(name: str) -> str:
    """An entry / file name to show: forward slashes, no drive, no leading '/', no '..' parts."""
    n = str(name or "").replace("\\", "/")
    n = re.sub(r"^[A-Za-z]:", "", n)
    parts = [p for p in n.split("/") if p not in ("", ".", "..")]
    return "/".join(parts)[-300:]


def entry_count(path: Path) -> Optional[int]:
    """The number of entries a ZIP says it holds (end of central directory, zip64 too), read
    before zipfile loads the whole directory into memory. None: no record found."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            back = min(size, 65536 + 22)
            f.seek(size - back)
            tail = f.read(back)
            i = tail.rfind(b"PK\x05\x06")
            if i < 0:
                return None
            total = struct.unpack("<H", tail[i + 10:i + 12])[0]
            if total != 0xFFFF:
                return total
            j = tail.rfind(b"PK\x06\x07", 0, i)            # zip64 end of central directory locator
            if j < 0:
                return None
            off = struct.unpack("<Q", tail[j + 8:j + 16])[0]
            f.seek(off)
            rec = f.read(56)
            if rec[:4] != b"PK\x06\x06":
                return None
            return struct.unpack("<Q", rec[32:40])[0]
    except (OSError, struct.error):
        return None


def _open(path: Path) -> zipfile.ZipFile:
    n = entry_count(path)
    if n is None:
        raise ArchiveError(NOT_ZIP)
    if n > MAX_ENTRIES:
        raise ArchiveError(TOO_MANY_ENTRIES, n=MAX_ENTRIES)
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        raise ArchiveError(NOT_ZIP)


def read_capped(zf: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int, out=None) -> Optional[bytes]:
    """The entry's bytes inflated, at most `cap` (None past it: never inflated further); into
    `out` (a file) instead when given (then b""). zipfile / zlib errors raise ArchiveError."""
    if info.file_size > cap:
        return None
    buf, n = [], 0
    try:
        with zf.open(info) as f:
            while True:
                chunk = f.read(READ_STEP)
                if not chunk:
                    break
                n += len(chunk)
                if n > cap:
                    return None
                if out is not None:
                    out.write(chunk)
                else:
                    buf.append(chunk)
    except OSError as e:
        if out is not None and getattr(e, "errno", None) is not None:
            raise                                   # our own disk (full): not the archive's fault
        raise ArchiveError(UP.UNREADABLE) from e
    except Exception as e:                          # noqa: BLE001 — BadZipFile, a CRC error, zlib.error, encrypted
        raise ArchiveError(UP.UNREADABLE) from e
    return b"" if out is not None else b"".join(buf)


def _kind_of(names: list[str]) -> str:
    low = [n.lower() for n in names]
    if any(n.startswith("di_connect/") or "/di_connect/" in n for n in low):
        return GARMIN
    if any(n.endswith("export.xml") and "apple_health_export" in n for n in low):
        return APPLE_HEALTH
    if any(re.search(r"(^|/)training-session-[^/]*\.json$", n) for n in low):
        return POLAR
    return GENERIC


def _ignored(name: str) -> bool:
    """macOS / Windows metadata a zip tool adds: never listed."""
    n = name.lower()
    base = n.rsplit("/", 1)[-1]
    return n.startswith("__macosx/") or "/__macosx/" in n or base.startswith("._") or base in (
        ".ds_store", "thumbs.db", "desktop.ini")


def scan(d: Path) -> dict:
    """List the archive (manifest.json): the activity files in order, the nested ZIPs copied out
    under our own names, what is skipped (by reason, with names). ArchiveError for an archive
    that is no ZIP or holds too many entries."""
    nested = d / "nested"
    nested.mkdir(exist_ok=True)
    entries: list[list] = []                 # [container, member, shown name, declared size]
    skipped: dict = {}
    inflated = 0
    kind = GENERIC

    def skip(reason: str, name: str) -> None:
        s = skipped.setdefault(reason, {"n": 0, "names": []})
        s["n"] += 1
        if len(s["names"]) < LIST_MAX:
            s["names"].append(name)

    queue = [(0, d / "archive.zip", "", 0)]        # (container, path, shown prefix, depth)
    k = 0
    while queue:
        cont, path, prefix, depth = queue.pop(0)
        try:
            zf = _open(path)
        except ArchiveError as e:
            if cont == 0:
                raise
            skip(e.reason, prefix[:-3])
            continue
        with zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if cont == 0:
                kind = _kind_of([i.filename for i in infos])
            for info in infos:
                if _ignored(info.filename):
                    continue
                shown = (prefix + _clean(info.filename))[-300:]
                low = info.filename.lower()
                if low.endswith(".zip"):
                    if depth + 1 > MAX_DEPTH:
                        skip(TOO_DEEP, shown)
                        continue
                    k += 1
                    target = nested / f"{k}.zip"
                    cap = min(MAX_NESTED_BYTES, MAX_INFLATE_BYTES - inflated)
                    try:
                        with open(target, "wb") as out:
                            got = read_capped(zf, info, cap, out=out)
                    except ArchiveError as e:
                        target.unlink(missing_ok=True)
                        skip(e.reason, shown)
                        continue
                    if got is None:
                        target.unlink(missing_ok=True)
                        skip(BOMB, shown)
                        continue
                    inflated += target.stat().st_size
                    queue.append((k, target, shown + " › ", depth + 1))
                elif kind == APPLE_HEALTH and re.search(r"(^|/)workout-routes/", low):
                    skip(APPLE_ROUTE, shown)              # a route without heart rate or sport: later
                elif UP.is_fit_name(low):
                    if len(entries) >= MAX_ACTIVITY_FILES:
                        skip(TOO_MANY, shown)
                    else:
                        entries.append([cont, info.filename, shown, info.file_size])
                elif re.search(r"(^|/)training-session-[^/]*\.json$", low):
                    skip(POLAR_JSON, shown)
                elif low.endswith(UNSUPPORTED_EXTS) and kind != APPLE_HEALTH:
                    skip(UNSUPPORTED, shown)
                else:
                    skip(NOT_ACTIVITY_FILE, shown)
    man = {"kind": kind, "entries": entries, "skipped": skipped, "inflated": inflated}
    _write_json(d / "manifest.json", man)
    log.info("export import listed: %d activity files, %d skipped, kind %s", len(entries),
             sum(v["n"] for v in skipped.values()), kind)
    return man


# ---------------------------------------------------------------------------- the work

async def process(db, d: Path, athlete_id: int, part_bytes: Optional[int] = None,
                  part_files: Optional[int] = None) -> dict:
    """One part: the next activity files without a result (≤ part_bytes / part_files), each
    through sync/upload.import_one into the current tenant; results appended to items.jsonl.
    {"more": files still to do, "added": added in this part, "stopped": cancelled}."""
    man = _read_json(d / "manifest.json")
    if man is None:
        man = await asyncio.to_thread(scan, d)
    _repair(d)
    done = {i.get("i") for i in _items(d)}
    pending = [i for i in range(len(man["entries"])) if i not in done]
    if not pending:
        return {"more": 0, "added": 0, "stopped": False}
    part_bytes = PART_BYTES if part_bytes is None else part_bytes
    part_files = PART_FILES if part_files is None else part_files
    folder = await UP._folder(db, athlete_id)
    await db.commit()
    inflated = int(man.get("inflated") or 0)
    zips: dict = {}
    added = taken = size = 0
    stopped = False
    try:
        with open(d / "items.jsonl", "a", encoding="utf-8") as out:
            for i in pending:
                if (d / "cancel").exists():
                    stopped = True
                    break
                if taken >= part_files or (taken and size >= part_bytes):
                    break
                cont, member, shown, _declared = man["entries"][i]
                cap = UP.max_bytes(member)
                item = None
                try:
                    zf = zips.get(cont)
                    if zf is None:
                        zf = zips[cont] = _open(d / "archive.zip" if cont == 0 else d / "nested" / f"{cont}.zip")
                    if inflated >= MAX_INFLATE_BYTES:
                        raise ArchiveError(BOMB)
                    data = await asyncio.to_thread(read_capped, zf, zf.getinfo(member),
                                                   min(cap, MAX_INFLATE_BYTES - inflated))
                except (ArchiveError, KeyError) as e:
                    reason = e.reason if isinstance(e, ArchiveError) else UP.UNREADABLE
                    item = {"name": shown, "status": UP.FAILED, "reason": reason, "date": None, "sport": None,
                            "of": None}
                    data = b""
                if item is None:
                    if data is None:
                        item = {"name": shown, "status": UP.FAILED, "reason": UP.TOO_LARGE, "date": None,
                                "sport": None, "of": None}
                    else:
                        inflated += len(data)
                        size += len(data)
                        item = await UP.import_one(db, athlete_id, UP.Incoming(shown, data, len(data)), folder)
                        if item["status"] == UP.FAILED and item["reason"] == UP.NOT_ACTIVITY:
                            item["status"] = UP.SKIPPED            # a monitoring FIT, a course: no activity
                taken += 1
                added += int(item["status"] == UP.ADDED)
                out.write(json.dumps({"i": i, **item}, ensure_ascii=False) + "\n")
                out.flush()
    finally:
        for zf in zips.values():
            zf.close()
    man["inflated"] = inflated
    _write_json(d / "manifest.json", man)
    return {"more": max(0, len(pending) - taken), "added": added, "stopped": stopped}


def run_part(imp_id: str, progress=None) -> dict:
    """One part in its own event loop and DB engine (a worker process, or the job's thread when
    worker processes are off): the import's tenant is the caller's."""
    d = _dir(imp_id)
    st = _state(d)
    if st is None:
        return {"more": 0, "added": 0, "stopped": True}

    async def go():
        from sqlalchemy import event
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.db import database
        eng = create_async_engine(database.url_for(database.db_path()))
        event.listen(eng.sync_engine, "connect", database._on_connect)
        try:
            async with async_sessionmaker(eng, expire_on_commit=False)() as db:
                return await process(db, d, int(st.get("athlete_id") or 1))
        finally:
            await eng.dispose()
    return asyncio.run(go())


# ---------------------------------------------------------------------------- the queue

def start(imp_id: str, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
    """Queue the import's next part (the request's loop: the steps after the import run there)."""
    if loop is not None:
        _LOOPS[_tid()] = loop
    d = _dir(imp_id)
    with _LOCK:
        st = _state(d)
        if st is None or st["state"] not in (QUEUED, RUNNING, INTERRUPTED, DONE):
            return
        st["state"] = QUEUED
        st["error"] = None
        st.setdefault("parts", 0)
        _save(d, st)
    _submit(imp_id)


def _submit(imp_id: str) -> None:
    from backend import jobs
    tid = _tid()
    d = _dir(imp_id)
    st = _state(d) or {}
    n = int(st.get("parts") or 0)
    job = jobs.submit(lambda job: _part_job(imp_id), cls="single", priority="sync_hot",
                      label="export import", key=("export-import", tid, imp_id, n))
    _LIVE[(tid, imp_id)] = job


def _part_job(imp_id: str) -> None:
    from backend import workproc
    from backend.engine.wko5expr.datasource import files_changed
    d = _dir(imp_id)
    with _LOCK:
        st = _state(d)
        if st is None or (d / "cancel").exists():
            shutil.rmtree(d, ignore_errors=True)
            return
        st["state"] = RUNNING
        _save(d, st)
    t0 = time.monotonic()
    try:
        if workproc.enabled():
            out = workproc.run("backend.sync.archive:run_part", imp_id)
        else:
            out = run_part(imp_id)
    except Exception as e:                  # noqa: BLE001 — recorded; 重試 continues
        reason = e.reason if isinstance(e, ArchiveError) else type(e).__name__
        if isinstance(e, workproc.WorkerError) and str(e).startswith("ArchiveError:"):
            reason = str(e).split(":", 1)[1].strip()
        log.warning("export import part failed: %s", reason)
        with _LOCK:
            st = _state(d)
            if st is not None:
                hopeless = reason in (NOT_ZIP, TOO_MANY_ENTRIES)
                st["state"] = FAILED if hopeless else INTERRUPTED
                st["error"] = reason
                _save(d, st)
                if hopeless:
                    _drop_data(d)
        return
    finally:
        files_changed()                     # the worker stored FITs: the next stamp rescans
    log.info("export import part: %d added, %d left, %.1f s", out.get("added", 0), out.get("more", 0),
             time.monotonic() - t0)
    if out.get("stopped") or (d / "cancel").exists():
        shutil.rmtree(d, ignore_errors=True)
        return
    with _LOCK:
        st = _state(d)
        if st is None:
            return
        st["parts"] = int(st.get("parts") or 0) + 1
        if out.get("more"):
            st["state"] = QUEUED
            _save(d, st)
            _submit(imp_id)                 # under the lock: the status never sees no job between parts
            return
        st["state"] = DONE
        st["finished"] = time.time()
        _save(d, st)
    _finish(imp_id)


def _finish(imp_id: str) -> None:
    """The last part ended: the archive goes (unless a retry may still need it), and the steps
    after a sync run once on the server's loop."""
    d = _dir(imp_id)
    its = _items(d)
    added = sum(1 for i in its if i.get("status") == UP.ADDED)
    failed = sum(1 for i in its if i.get("status") == UP.FAILED)
    if not any(i.get("status") == UP.FAILED and i.get("reason") in RETRYABLE for i in its):
        _drop_data(d)
    st = _state(d) or {}
    log.info("export import done: %d added of %d files", added, len(its))
    if added <= 0:
        return
    loop = _LOOPS.get(_tid())
    if loop is None or loop.is_closed():
        log.warning("export import: no server loop for the post-import steps")
        return
    from backend import tenancy
    t = tenancy.current()
    aid = int(st.get("athlete_id") or 1)

    async def after():
        from backend.db.database import AsyncSessionLocal
        from backend.sync import runner
        with tenancy.use(t):
            async with AsyncSessionLocal() as db:
                await runner.after_import(db, UP.SOURCE, {"status": "partial" if failed else "ok", "downloaded": added,
                                                          "trigger": "upload"}, aid)
    try:
        asyncio.run_coroutine_threadsafe(after(), loop)
    except RuntimeError:
        log.warning("export import: the server loop is gone")


def retry(imp_id: str, loop: Optional[asyncio.AbstractEventLoop] = None) -> dict:
    """Go on where the work stopped, and try again the files that failed with a reason a retry
    may cure. ArchiveError: not_found, busy, nothing (no archive left / nothing to retry)."""
    d = _dir(imp_id)
    s = status(imp_id, items=False)
    if s is None:
        raise ArchiveError("not_found")
    if s["state"] in (QUEUED, RUNNING):
        raise ArchiveError("busy")
    if not s["can_retry"] or not (d / "archive.zip").exists():
        raise ArchiveError("nothing")
    with _LOCK:
        keep = [i for i in _items(d) if not (i.get("status") == UP.FAILED and i.get("reason") in RETRYABLE)]
        tmp = d / "items.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for i in keep:
                f.write(json.dumps(i, ensure_ascii=False) + "\n")
        os.replace(tmp, d / "items.jsonl")
    start(imp_id, loop)
    return status(imp_id)
