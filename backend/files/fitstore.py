"""
FIT originals as immutable objects named by their content hash (SP-308;
docs/plans/auth-and-demo.plan.md §10.3 C1, docs/research/local-first-sync.md §5.3 / §9).

Not to be confused with engine/wko5expr/fitcache.FitStore: that is the PARSE cache of a FIT
folder (npz channels + index.json). This module is the STORAGE of the FIT files themselves,
hence the names FitObjectStore / LocalDirStore.

Key layout — relative to the store root, the tenant's shared `fit/` folder
(tenancy.shared_path("fit") = sync/storage.fit_root()):

    <source folder>/objects/<sha256[:2]>/<sha256>.fit.gz        e.g. coros/objects/3f/3f9a…e1.fit.gz

* sha256 of the raw (uncompressed) FIT bytes; the stored file is always gzip (-6, header
  mtime 0, so the same FIT always gives the same bytes). An object never changes: putting a
  key that exists is a no-op.
* the objects stay inside the per-source folder (fit/coros, fit/tp) on purpose: the chart
  Dataset (fitdataset.FitFolderDataset), the race-power scan (racepower/cptest.py), the
  sync folder stats, purge and backup all work per source folder; they see the objects as
  `*.fit.gz` files of that folder without a second code path.
* the same content is stored once (fit_objects.sha256 is the primary key): importing it
  again from any source finds the object and stores nothing. COROS and TP encode the same
  activity differently (different bytes, different hashes), so the cross-source duplicate
  rule stays sync/dedup.py's start-time match.
* `workout_files.file_path` holds the object key (relative). Rows written before SP-308
  hold an absolute path to a `.fit` file; every reader takes both (resolve / read_bytes)
  until backend/scripts/fit_objects_migrate.py has converted them.
* deleting a source's files (sync/purge.py) only marks fit_objects.deleted; the files go
  later in purge.cleanup_objects (only objects no live workout_files row points at).

    store = default_store()              # LocalDirStore(storage.fit_root())
    key = store.put(object_key("coros", sha), fit_bytes)
    raw = read_bytes(row.file_path)      # an object key or a legacy absolute path; gunzipped
"""
from __future__ import annotations

import gzip
import hashlib
import io
import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Iterator, Optional, Protocol, Union

log = logging.getLogger(__name__)

OBJECTS = "objects"
SUFFIX = ".fit.gz"
GZIP_MAGIC = b"\x1f\x8b"
GZIP_LEVEL = 6
KEY_RE = re.compile(r"^([a-z][a-z0-9_-]*)/objects/([0-9a-f]{2})/([0-9a-f]{64})\.fit\.gz$")
NAME_RE = re.compile(r"^([0-9a-f]{64})\.fit\.gz$")

PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------

def sha256_hex(fit: bytes) -> str:
    return hashlib.sha256(fit).hexdigest()


def object_key(folder: str, sha: str) -> str:
    """<folder>/objects/<sha[:2]>/<sha>.fit.gz (folder: "coros" / "tp", storage.SOURCES)."""
    sha = sha.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("not a sha256 hex digest")
    key = f"{folder}/{OBJECTS}/{sha[:2]}/{sha}{SUFFIX}"
    if not KEY_RE.match(key):
        raise ValueError(f"bad folder {folder!r}")
    return key


def is_object_key(s) -> bool:
    """A workout_files.file_path written by SP-308 (a relative object key), not a legacy path."""
    return isinstance(s, str) and KEY_RE.match(s) is not None


def sha_of_key(key: str) -> Optional[str]:
    m = KEY_RE.match(key) if isinstance(key, str) else None
    return m.group(3) if m else None


def folder_of_key(key: str) -> Optional[str]:
    m = KEY_RE.match(key) if isinstance(key, str) else None
    return m.group(1) if m else None


def object_sha(p: PathLike) -> Optional[str]:
    """The sha256 a file / relative path is named by when it is an object
    (…/objects/<aa>/<sha>.fit.gz with aa = the hash's first two digits), else None."""
    parts = str(p).replace("\\", "/").split("/")
    if len(parts) < 3:
        return None
    m = NAME_RE.match(parts[-1])
    if not m or parts[-2] != m.group(1)[:2] or parts[-3] != OBJECTS:
        return None
    return m.group(1)


def gunzip_if(blob: bytes) -> bytes:
    """The FIT bytes of a file that may be gzip (the magic decides, never the name)."""
    return gzip.decompress(blob) if blob[:2] == GZIP_MAGIC else blob


def gzip_fit(fit: bytes) -> bytes:
    """The stored form: gzip -6 with header mtime 0 (the same FIT always gives the same bytes)."""
    return gzip.compress(fit, compresslevel=GZIP_LEVEL, mtime=0)


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

class PathNotConfined(ValueError):
    """A key that is absolute, has `..`, passes a symlink or ends outside the store root."""


@dataclass(frozen=True)
class FitObj:
    key: str
    size: int            # bytes on disk (the gzip)
    stamp: str           # mtime_ns locally (an ETag for a remote store)


class FitObjectStore(Protocol):
    """§10.3's interface (`FitStore` in the plan; renamed: fitcache.FitStore is the parse
    cache). A key is a relative POSIX path inside the tenant's fit/ folder."""

    def put(self, key: str, fit: bytes) -> str: ...            # gzip, store; returns the key
    def get(self, key: str) -> bytes: ...                       # the FIT bytes (gunzipped)
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str = "") -> Iterator[FitObj]: ...
    def local_path(self, key: str) -> ContextManager[Path]: ...  # the stored (gzip) file on disk


class LocalDirStore:
    """FIT objects in a local folder (the tenant's fit/). The safety of sync/storage.confined
    lives here too: a key is relative, has no `.` / `..` part, passes no symlink and ends
    strictly inside the root."""

    def __init__(self, root: PathLike):
        self.root = Path(root)

    # ---- paths ----------------------------------------------------------------
    def path(self, key: str) -> Path:
        """The resolved file of `key` (it may not exist); PathNotConfined otherwise."""
        if not isinstance(key, str) or not key or "\\" in key or ":" in key or key.startswith("/"):
            raise PathNotConfined("a key is a relative POSIX path")
        parts = key.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise PathNotConfined("a key has no empty, '.' or '..' part")
        root = self.root.resolve()
        p = self.root.joinpath(*parts)
        cur = p
        while True:
            if cur.is_symlink():
                raise PathNotConfined("symlinks are not followed")
            if cur.parent == cur or cur.parent == self.root:     # no "..": the walk ends at the root
                break
            cur = cur.parent
        rp = p.resolve()
        if rp == root or root not in rp.parents:
            raise PathNotConfined("key is outside the store")
        return rp

    def key_of(self, p: PathLike) -> Optional[str]:
        """The key of a file inside the root, else None."""
        try:
            return Path(p).resolve().relative_to(self.root.resolve()).as_posix()
        except (OSError, ValueError):
            return None

    # ---- the interface --------------------------------------------------------
    def exists(self, key: str) -> bool:
        try:
            return self.path(key).is_file()
        except PathNotConfined:
            return False

    def put(self, key: str, fit: bytes) -> str:
        """Store `fit` gzip-compressed under `key`. Immutable: a key that exists is left as
        it is (same key = same content). Written to a temporary file, the round trip
        checked (gunzip == fit), fsynced, then renamed into place."""
        p = self.path(key)
        if p.is_file():
            return key
        blob = gzip_fit(fit)
        if gzip.decompress(blob) != fit:                     # never: gzip is lossless
            raise OSError("gzip round trip differs")
        p.parent.mkdir(parents=True, exist_ok=True)
        # no ".fit" in the temporary name: the folder scans (*.fit*) never see a half file
        tmp = p.with_name(f".{p.name.split('.')[0]}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            tmp.unlink(missing_ok=True)
        return key

    def get(self, key: str) -> bytes:
        return gunzip_if(self.path(key).read_bytes())

    def delete(self, key: str) -> None:
        p = self.path(key)
        p.unlink(missing_ok=True)
        try:                                    # an empty shard folder goes too
            p.parent.rmdir()
        except OSError:
            pass

    def list(self, prefix: str = "") -> Iterator[FitObj]:
        """Every stored file under `prefix` (a folder key such as "coros"), symlinks and
        temporary files left out."""
        base = self.path(prefix) if prefix else self.root
        if not base.is_dir():
            return
        root = self.root.resolve()
        stack = [base]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                if e.is_symlink():
                    continue
                if e.is_dir():
                    stack.append(Path(e.path))
                elif e.is_file() and not (e.name.startswith(".") and e.name.endswith(".tmp")):
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    yield FitObj(Path(e.path).resolve().relative_to(root).as_posix(), st.st_size,
                                 str(st.st_mtime_ns))

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        """The stored file (gzip) for a caller that needs a path; a remote store would
        download it to a temporary file here."""
        yield self.path(key)


def default_store() -> LocalDirStore:
    """The current tenant's store: <tenant shared>/fit (sync/storage.fit_root)."""
    from backend.sync import storage
    return LocalDirStore(storage.fit_root())


# ---------------------------------------------------------------------------
# readers: an object key or a legacy path
# ---------------------------------------------------------------------------

def resolve(file_path: PathLike, store: Optional[LocalDirStore] = None) -> Path:
    """The file on disk of a workout_files.file_path: an object key resolves inside the
    store (confined), anything else is a legacy path, as it was."""
    if is_object_key(file_path):
        return (store or default_store()).path(str(file_path))
    return Path(file_path)


def read_bytes(file_path: PathLike, store: Optional[LocalDirStore] = None) -> bytes:
    """The FIT bytes (gunzipped) of an object key or a legacy path (.fit or .fit.gz)."""
    if is_object_key(file_path):
        return (store or default_store()).get(str(file_path))
    return gunzip_if(Path(file_path).read_bytes())


def open_fit(file_path: PathLike, store: Optional[LocalDirStore] = None):
    """A binary file-like of the FIT bytes (fitparse / fitdecode take file-likes)."""
    return io.BytesIO(read_bytes(file_path, store))


def is_gzip_file(p: PathLike) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(2) == GZIP_MAGIC
    except OSError:
        return False


# ---------------------------------------------------------------------------
# the fit_objects table (async, the app DB)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stored:
    key: str
    sha256: str
    created: bool        # False: the content was already stored (nothing written)


def folder_for(source: str) -> str:
    """The store folder of a source: the DB name ("coros" / "trainingpeaks") or the folder name."""
    from backend.sync import storage
    if source in storage.SOURCES:
        return source
    if source in storage.DB_TO_FOLDER:
        return storage.DB_TO_FOLDER[source]
    raise ValueError(f"no FIT object folder for source {source!r}")


async def store_fit(db, fit: bytes, source: str, coros_activity_id: Optional[str] = None,
                    start_time=None, store: Optional[LocalDirStore] = None) -> Stored:
    """Store one imported FIT as an object and record it in fit_objects (flush only: the
    caller commits, or rolls back — the object file then stays, unreferenced, for the next
    try or cleanup_objects). Content already stored (any source) is not stored again: the
    existing object's key is returned, a deleted mark is lifted, a file the cleanup removed
    meanwhile is written back."""
    from sqlalchemy import select
    from backend.db.models import FitObject
    from backend.sync import storage
    store = store or default_store()
    sha = sha256_hex(fit)
    row = (await db.execute(select(FitObject).where(FitObject.sha256 == sha))).scalar_one_or_none()
    if row is not None:
        key = object_key(folder_for(row.source), sha)
        created = not store.exists(key)
        if created:
            store.put(key, fit)
        row.deleted = False
        if coros_activity_id and not row.coros_activity_id:
            row.coros_activity_id = coros_activity_id
        if start_time is not None and row.start_time is None:
            row.start_time = start_time
        await db.flush()
        return Stored(key, sha, created)
    db_source = storage.SOURCES.get(source, source)
    key = object_key(folder_for(source), sha)
    created = not store.exists(key)
    store.put(key, fit)
    db.add(FitObject(sha256=sha, size=len(fit), source=db_source, coros_activity_id=coros_activity_id,
                     start_time=start_time, deleted=False))
    await db.flush()
    return Stored(key, sha, created)


async def note_import(db, key: str, start_time=None, coros_activity_id: Optional[str] = None) -> None:
    """Fill fit_objects.start_time / coros_activity_id of an object once its FIT is parsed."""
    from sqlalchemy import select
    from backend.db.models import FitObject
    sha = sha_of_key(key)
    if sha is None:
        return
    row = (await db.execute(select(FitObject).where(FitObject.sha256 == sha))).scalar_one_or_none()
    if row is None:
        return
    if start_time is not None and row.start_time is None:
        row.start_time = start_time
    if coros_activity_id and not row.coros_activity_id:
        row.coros_activity_id = coros_activity_id


async def live_row(db, key: str):
    """The workout_files row that points at `key`, if any."""
    from sqlalchemy import select
    from backend.db.models import WorkoutFile
    return (await db.execute(select(WorkoutFile).where(WorkoutFile.file_path == key))).scalars().first()


# ---------------------------------------------------------------------------
# the fit_objects table (read-only sqlite: the Dataset / race-power scans)
# ---------------------------------------------------------------------------

_INFO_MEMO: dict = {}
_INFO_LOCK = threading.Lock()


def object_info(db_path: Optional[PathLike]) -> dict:
    """{sha256: {"deleted": bool, "date": "YYYY-MM-DD" | None}} of the app DB's fit_objects
    (date = the athlete-local workout_date of the row that points at the object, else the
    object's UTC start date). Read-only and memoised on the DB file's stamp; {} without a
    DB or the table (a DB from before SP-308, a bare test folder)."""
    import sqlite3
    if db_path is None or not Path(db_path).exists():
        return {}
    from backend.db.filestamp import db_stamp
    st = db_stamp(Path(db_path))
    ck = (str(db_path), st)
    with _INFO_LOCK:
        hit = _INFO_MEMO.get(str(db_path))
    if hit is not None and hit[0] == ck and st is not None:
        return hit[1]
    out: dict = {}
    try:
        con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            have = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fit_objects'").fetchone()
            if have:
                for sha, deleted, start in con.execute("SELECT sha256, deleted, start_time FROM fit_objects"):
                    out[sha] = {"deleted": bool(deleted), "date": str(start)[:10] if start else None}
                for fp, d in con.execute("SELECT file_path, workout_date FROM workout_files "
                                         "WHERE file_path LIKE '%/objects/%'"):
                    sha = sha_of_key(fp)
                    if sha and d:
                        out.setdefault(sha, {"deleted": False, "date": None})["date"] = str(d)[:10]
        finally:
            con.close()
    except sqlite3.Error:
        return hit[1] if hit is not None else {}
    sig = hashlib.sha1(repr(sorted((k, v["deleted"], v["date"]) for k, v in out.items())).encode()).hexdigest()
    with _INFO_LOCK:
        _INFO_MEMO[str(db_path)] = (ck, out)
        _SIG_MEMO[str(db_path)] = sig
    return out


_SIG_MEMO: dict = {}


def object_sig(db_path: Optional[PathLike]) -> str:
    """Changes when object_info's answer does (a deleted mark, a date): for listing memos."""
    if not object_info(db_path):
        return ""
    with _INFO_LOCK:
        return _SIG_MEMO.get(str(db_path), "")


def deleted_shas(db_path: Optional[PathLike]) -> frozenset:
    return frozenset(s for s, v in object_info(db_path).items() if v.get("deleted"))
