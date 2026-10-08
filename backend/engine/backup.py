"""
Backups of the app database (and optionally the synced FIT originals).

A backup is one file in a folder the user picks (usually inside a cloud-sync
folder — iCloud Drive / OneDrive / Google Drive / Dropbox; the sync client does
the uploading, the app never logs in anywhere):

    trailruncoach-backup-YYYYMMDD-HHMMSS.zip

Backups are not encrypted (owner 2026-10-02: the option, its stored derived
key and the settings UI were removed). An encrypted backup made by an older
version (`.zip.enc`, starts with LEGACY_MAGIC) is rejected with a clear
message; it is neither listed nor pruned.

The zip holds what the data registry (backend/data_registry.py, SP-311) marks for a
backup — `plan()`:
    manifest.json     app / app_version / format / schema_version / created_at /
                      row_counts / db_sha256 / fit {included, files, bytes} /
                      files {count, bytes} / secrets_removed
    wko5coach.db      (registry DB, always) a consistent snapshot taken with sqlite3's
                      online backup API (safe while the app is writing); every table,
                      but the SECRET ones (sync_state: COROS / TP tokens, sealed
                      passwords; debug_tokens) are emptied and the copy VACUUMed, so
                      no credential — sealed or legacy plaintext — is in a backup (SP-355)
    files/<rel>       (registry USER / uploaded IMPORTED files, always, SP-355) every
                      tenant file whose entry says backup=ALWAYS, at its path relative
                      to its tenant folder (`roots`: ROOT / BASE / SHARED); a new entry
                      is carried without a change here
    fit/<source>/<year>/<file>.gz   (registry FIT, opt-in) only with "include FIT originals"
Secret files (secret.key, weather.json, tp_client.json, backups/) are SECRET entries:
never backup=ALWAYS (test_data_registry.py), so never walked.

Retention (`prune`) only ever looks at files whose name matches NAME_RE exactly,
so nothing else in the folder can be deleted.

Restore: check the manifest -> extract the DB -> sha256
and PRAGMA integrity_check -> back up the current DB and files (local pre-restore
file) -> copy the backup into the live DB with the sqlite3 backup API (works while
the app has the file open; a plain file swap does not on Windows) -> keep this
machine's own backup.* settings and SECRET tables (its login stays; an older backup's
sync_state is not taken either) -> put the backup's files/ back in place (only
registered backup=ALWAYS paths inside the tenant; files the backup does not hold are
left alone) -> restore FIT originals that are missing. Format 1 (before SP-355: no
files/, the SECRET rows as stored) still restores.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from backend import data_registry as R

log = logging.getLogger(__name__)

APP = "TrailRunCoach"
# 2 (SP-355): files/ + the SECRET tables emptied. An older app refuses it (「請先更新 App」)
# instead of restoring it over its own login; format 1 still restores here.
FORMAT = 2
NAME_RE = re.compile(r"^trailruncoach-backup-(\d{8})-(\d{6})\.zip$")
PRE_RE = re.compile(r"^trailruncoach-prerestore-(\d{8})-(\d{6})\.zip$")
MANIFEST = "manifest.json"
DB_ENTRY = R.DB.pattern                       # "wko5coach.db"
FIT_DIR = R.FIT.pattern.split("/")[0]         # "fit"
FIT_ENTRY_RE = re.compile(r"^fit/(coros|tp)/[A-Za-z0-9_.\-]{1,32}/[^/\\:*?\"<>|]{1,200}\.gz$")
FILES_DIR = "files"                           # files/<path relative to its tenant folder>
FILE_CLASSES = (R.USER, R.IMPORTED)           # what a files/ entry may be
FILE_WHERES = (R.ROOT, R.BASE, R.SHARED)
KEEP_DAILY, KEEP_WEEKLY, KEEP_PRERESTORE = 7, 4, 5
AUTO_EVERY = timedelta(hours=24)
RETRY_AFTER_FAIL = timedelta(hours=1)
SUGGESTED_SUBDIR = "TrailRunCoach Backups"
PRESERVED_KEYS = "backup.%"          # this machine's backup settings survive a restore

CHUNK = 1 << 20
# the header of an older version's encrypted backup (.zip.enc) — only to reject it
LEGACY_MAGIC = b"TRCBAK\x00\x01"
LEGACY_ENCRYPTED = ("這是舊版建立的加密備份（.zip.enc），現在的版本不再支援加密備份，不能還原。"
                    "請改選未加密的備份檔（.zip）")

_LOCK = threading.Lock()


class BackupError(RuntimeError):
    """Shown to the user (Traditional Chinese)."""


class Busy(BackupError):
    pass


# ---------------------------------------------------------------- versions
def app_version() -> str:
    from backend import __version__
    return __version__


def schema_info(con: sqlite3.Connection) -> tuple[str, dict[str, int]]:
    """(schema fingerprint, {table: rows}). The fingerprint changes whenever a
    table or column is added; init_db upgrades an older one on start."""
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    cols, counts = [], {}
    for t in tables:
        q = t.replace('"', '""')
        cols += [f"{t}.{r[1]}" for r in con.execute(f'PRAGMA table_info("{q}")')]
        counts[t] = con.execute(f'SELECT COUNT(*) FROM "{q}"').fetchone()[0]
    fp = hashlib.sha256("\n".join(sorted(cols)).encode()).hexdigest()[:12]
    return f"{len(tables)}t-{len(cols)}c-{fp}", counts


def _ro_uri(p: Path) -> str:
    return Path(p).resolve().as_uri() + "?mode=ro"


def integrity_ok(db: Path) -> bool:
    con = sqlite3.connect(_ro_uri(db), uri=True)
    try:
        rows = con.execute("PRAGMA integrity_check").fetchall()
        return rows == [("ok",)]
    except sqlite3.DatabaseError:
        return False
    finally:
        con.close()


def snapshot_db(src: Path, out: Path) -> None:
    """A consistent copy of a (possibly busy) SQLite DB."""
    s = sqlite3.connect(_ro_uri(src), uri=True, timeout=30)
    d = sqlite3.connect(str(out))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def strip_secrets(snap: Path) -> list[str]:
    """Empty the SECRET tables (data_registry: sync_state, debug_tokens) of a snapshot and
    VACUUM it, so no credential — sealed or legacy plaintext — is left on a free page.
    Returns the tables emptied. Only ever called on a copy, never the live DB."""
    con = sqlite3.connect(str(snap))
    try:
        con.execute("PRAGMA journal_mode=DELETE")       # a WAL source makes a WAL copy: no -wal left
        con.execute("PRAGMA secure_delete=ON")
        present = _tables(con)
        gone = [t for t in R.secret_tables() if t in present]
        for t in gone:
            con.execute(f"DELETE FROM {_q(t)}")
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    return gone


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(CHUNK), b""):
            h.update(b)
    return h.hexdigest()


def is_legacy_encrypted(path: Path) -> bool:
    """An older version's encrypted backup (.zip.enc) — no longer restorable."""
    with open(path, "rb") as f:
        return f.read(len(LEGACY_MAGIC)) == LEGACY_MAGIC


# ---------------------------------------------------------------- create
def backup_name(now: datetime) -> str:
    return f"trailruncoach-backup-{now:%Y%m%d-%H%M%S}.zip"


def _fit_files(fit_root: Optional[Path]) -> Iterable[tuple[Path, str]]:
    if not fit_root or not Path(fit_root).is_dir():
        return
    root = Path(fit_root)
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        entry = f"{FIT_DIR}/{rel}.gz"
        if FIT_ENTRY_RE.match(entry):
            yield p, entry


def _file_entry_ok(f) -> bool:
    """A registry file entry this module can carry under files/: a tenant's own USER or
    IMPORTED file in one of its folders (never SECRET / DERIVED / the server's files)."""
    return f.cls in FILE_CLASSES and f.where in FILE_WHERES and f.scope == R.TENANT


def plan(include_fit: bool) -> list:
    """The registry entries a backup holds (data_registry.backup_entries). This module
    writes the DB snapshot, the files/ entries and the FIT originals; an entry it cannot
    write is an error, not silently left out."""
    parts = R.backup_entries(include_fit)
    unknown = [f.pattern for f in parts if f not in (R.DB, R.FIT) and not _file_entry_ok(f)]
    if unknown or R.DB not in parts:
        raise BackupError(f"備份不支援這些資料：{unknown or [R.DB.pattern]}")
    return parts


def file_entries(parts: Optional[list] = None) -> list:
    """The files/ entries of a backup: the registry's backup=ALWAYS files besides the DB."""
    return [f for f in (plan(False) if parts is None else parts) if f not in (R.DB, R.FIT)]


def tenant_roots(root: Path, base: Optional[Path] = None, shared: Optional[Path] = None) -> dict:
    """{where: folder} of a tenant (tenancy.py: private root, the base a sandbox reads,
    shared). The owner's three are one folder ($WKO5COACH_HOME)."""
    root = Path(root)
    return {R.ROOT: root, R.BASE: Path(base) if base else root, R.SHARED: Path(shared) if shared else root}


def _skipped(name: str) -> bool:
    """A half-written file (an atomic write's *.tmp, a hidden partial): not user data."""
    return name.endswith(".tmp") or name.startswith(".")


def _user_files(roots: dict, entries: list) -> Iterable[tuple[Path, str]]:
    """(path, path relative to its tenant folder) of every file of the given entries; a
    file belongs to the entry the registry classifies it under (first match wins)."""
    for f in entries:
        base = Path(roots[f.where])
        if "*" not in f.pattern:
            cands = [base / f.pattern]
        elif "**" not in f.pattern:
            cands = sorted(base.glob(f.pattern))
        else:
            head = f.pattern.split("*", 1)[0]
            start = base / head.rsplit("/", 1)[0] if "/" in head else base
            cands = []
            for dirpath, dirnames, filenames in os.walk(start):     # does not follow links
                dirnames.sort()
                cands += [Path(dirpath, n) for n in sorted(filenames)]
        for p in cands:
            if _skipped(p.name) or p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(base).as_posix()
            if R.classify(rel) == f:
                yield p, rel


def build_zip(db_path: Path, zip_path: Path, *, fit_root: Optional[Path] = None,
              include_fit: bool = False, roots: Optional[dict] = None,
              now: Optional[datetime] = None) -> dict:
    """The plain zip (snapshot without the SECRET rows + the user's files + manifest
    [+ FITs]). `roots`: the tenant's folders (tenant_roots; default: the DB's folder).
    Returns the manifest."""
    now = now or datetime.now(timezone.utc)
    parts = plan(include_fit)
    roots = roots or tenant_roots(Path(db_path).parent)
    with tempfile.TemporaryDirectory(dir=zip_path.parent, prefix=".trc-snap-") as td:
        snap = Path(td) / DB_ENTRY
        snapshot_db(db_path, snap)
        removed = strip_secrets(snap)
        con = sqlite3.connect(str(snap))
        try:
            schema, counts = schema_info(con)
        finally:
            con.close()
        if not integrity_ok(snap):
            raise BackupError("資料庫完整性檢查沒通過，這次沒有建立備份")
        manifest = {"app": APP, "format": FORMAT, "app_version": app_version(),
                    "schema_version": schema, "created_at": now.astimezone(timezone.utc).isoformat(),
                    "row_counts": counts, "db_sha256": _sha256(snap),
                    "fit": {"included": R.FIT in parts, "files": 0, "bytes": 0},
                    "files": {"count": 0, "bytes": 0}, "secrets_removed": removed}
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(snap, DB_ENTRY)
            for p, rel in _user_files(roots, file_entries(parts)):
                data = p.read_bytes()
                z.writestr(zipfile.ZipInfo(f"{FILES_DIR}/{rel}", date_time=_zip_time(p)), data,
                           compress_type=zipfile.ZIP_STORED if rel.endswith(".gz") else zipfile.ZIP_DEFLATED)
                manifest["files"]["count"] += 1
                manifest["files"]["bytes"] += len(data)
            if R.FIT in parts:
                for p, entry in _fit_files(fit_root):
                    z.writestr(zipfile.ZipInfo(entry, date_time=_zip_time(p)),
                               gzip.compress(p.read_bytes(), compresslevel=6, mtime=0),
                               compress_type=zipfile.ZIP_STORED)
                    manifest["fit"]["files"] += 1
                    manifest["fit"]["bytes"] += p.stat().st_size
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


def _zip_time(p: Path) -> tuple:
    t = datetime.fromtimestamp(max(p.stat().st_mtime, 315532800))   # zip can't go before 1980
    return (t.year, t.month, t.day, t.hour, t.minute, t.second)


def create_backup(db_path: Path, dest_dir: Path, *, fit_root: Optional[Path] = None,
                  include_fit: bool = False, roots: Optional[dict] = None,
                  now: Optional[datetime] = None) -> dict:
    """Write one backup file into dest_dir (atomically: a hidden .partial file
    renamed at the end, so a cloud client never uploads half a backup)."""
    if not _LOCK.acquire(blocking=False):
        raise Busy("另一個備份或還原正在進行中")
    try:
        db_path, dest_dir = Path(db_path), Path(dest_dir)
        if not db_path.exists():
            raise BackupError("找不到資料庫檔案")
        dest_dir.mkdir(parents=True, exist_ok=True)
        local = (now or datetime.now(timezone.utc)).astimezone()
        name = backup_name(local)
        final = dest_dir / name
        while final.exists():             # two backups in the same second
            local += timedelta(seconds=1)
            name = backup_name(local)
            final = dest_dir / name
        tmp = dest_dir / f".{name}.partial"
        try:
            manifest = build_zip(db_path, tmp, fit_root=fit_root, include_fit=include_fit, roots=roots,
                                 now=local)
            os.replace(tmp, final)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        return {"name": name, "path": str(final), "size": final.stat().st_size,
                "created_at": manifest["created_at"],
                "row_counts": manifest["row_counts"], "fit_files": manifest["fit"]["files"],
                "user_files": manifest["files"]["count"]}
    finally:
        _LOCK.release()


# ---------------------------------------------------------------- list / prune
def _stamp(m: re.Match) -> datetime:
    return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")


def _ours(folder: Path, pattern: re.Pattern) -> list[tuple[datetime, Path]]:
    out = []
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return out
    for p in entries:
        m = pattern.match(p.name)
        if not m or p.is_symlink() or not p.is_file():
            continue
        try:
            out.append((_stamp(m), p))
        except ValueError:
            continue
    out.sort(key=lambda t: t[0], reverse=True)
    return out


def list_backups(folder: Path) -> list[dict]:
    return [{"name": p.name, "size": p.stat().st_size, "created_local": t.isoformat(timespec="seconds")}
            for t, p in _ours(folder, NAME_RE)]


def retention(stamps: list[datetime], keep_daily: int = KEEP_DAILY,
              keep_weekly: int = KEEP_WEEKLY) -> set[datetime]:
    """Which to keep: the newest of each of the `keep_daily` most recent days
    with a backup, plus the newest of each of the `keep_weekly` most recent
    ISO weeks."""
    keep: set[datetime] = set()
    days: dict = {}
    weeks: dict = {}
    for t in sorted(stamps, reverse=True):
        days.setdefault(t.date(), t)
        weeks.setdefault(tuple(t.isocalendar())[:2], t)
    keep |= set(list(days.values())[:keep_daily])
    keep |= set(list(weeks.values())[:keep_weekly])
    return keep


def prune(folder: Path, keep_daily: int = KEEP_DAILY, keep_weekly: int = KEEP_WEEKLY) -> list[str]:
    """Delete old backups — only files this feature created (NAME_RE)."""
    files = _ours(folder, NAME_RE)
    keep = retention([t for t, _ in files], keep_daily, keep_weekly)
    gone = []
    for t, p in files:
        if t not in keep:
            try:
                p.unlink()
                gone.append(p.name)
            except OSError:
                pass
    return gone


def auto_due(last_ok_at: Optional[str], last_attempt_at: Optional[str],
             now: Optional[datetime] = None) -> bool:
    """Daily automatic backup: more than 24 h since the last good one, and
    not within an hour of a failed attempt."""
    now = now or datetime.now(timezone.utc)

    def parse(s):
        try:
            d = datetime.fromisoformat(s)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    ok, tried = parse(last_ok_at), parse(last_attempt_at)
    if ok is not None and now - ok < AUTO_EVERY:
        return False
    if tried is not None and (ok is None or tried > ok) and now - tried < RETRY_AFTER_FAIL:
        return False
    return True


# ---------------------------------------------------------------- folders
def check_folder(path: str) -> Path:
    """A folder the user typed / picked: absolute, creatable, writable."""
    if not isinstance(path, str) or not path.strip():
        raise BackupError("請選擇備份資料夾")
    p = Path(os.path.expandvars(os.path.expanduser(path.strip())))
    if not p.is_absolute():
        raise BackupError("請輸入完整路徑（例如 C:\\Users\\你\\OneDrive\\TrailRunCoach Backups）")
    if p.exists() and not p.is_dir():
        raise BackupError("這個路徑是檔案，不是資料夾")
    if not p.parent.exists():
        raise BackupError("上一層資料夾不存在")
    try:
        p.mkdir(exist_ok=True)
        probe = p / ".trailruncoach-write-test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise BackupError(f"這個資料夾不能寫入：{e.strerror or e}")
    return p


def _dropbox_paths(env: dict) -> list[Path]:
    out = []
    for base in (env.get("LOCALAPPDATA"), env.get("APPDATA")):
        if not base:
            continue
        info = Path(base) / "Dropbox" / "info.json"
        try:
            data = json.loads(info.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        for acct in ("personal", "business"):
            pth = (data.get(acct) or {}).get("path")
            if pth:
                out.append(Path(pth))
    return out


def detect_cloud_folders(env: Optional[dict] = None, home: Optional[Path] = None,
                         platform: Optional[str] = None, drives: Iterable[str] = ("G", "H", "I")) -> list[dict]:
    """Cloud-sync folders that exist on this machine, as one-click choices:
    [{id, label, root, path}] where path = root / SUGGESTED_SUBDIR. Only looks
    at folders; never logs in or writes anything."""
    env = dict(os.environ) if env is None else env
    home = Path.home() if home is None else Path(home)
    platform = platform or sys.platform
    cands: list[tuple[str, str, Path]] = []
    if platform.startswith("win"):
        prof = Path(env.get("USERPROFILE") or home)
        cands.append(("icloud", "iCloud Drive", prof / "iCloudDrive"))
        for var, label in (("OneDrive", "OneDrive"), ("OneDriveConsumer", "OneDrive"),
                           ("OneDriveCommercial", "OneDrive（公司）")):
            if env.get(var):
                cands.append(("onedrive", label, Path(env[var])))
        cands.append(("onedrive", "OneDrive", prof / "OneDrive"))
        for d in drives:
            for sub in ("My Drive", "我的雲端硬碟"):
                cands.append(("gdrive", "Google 雲端硬碟", Path(f"{d}:\\") / sub))
        cands += [("gdrive", "Google 雲端硬碟", prof / "Google Drive"),
                  ("gdrive", "Google 雲端硬碟", prof / "My Drive")]
        cands += [("dropbox", "Dropbox", p) for p in _dropbox_paths(env)]
        cands.append(("dropbox", "Dropbox", prof / "Dropbox"))
    else:
        if platform == "darwin":
            cands.append(("icloud", "iCloud Drive", home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"))
            cs = home / "Library" / "CloudStorage"
            try:
                subs = sorted(p for p in cs.iterdir() if p.is_dir())
            except OSError:
                subs = []
            for s in subs:
                if s.name.startswith("OneDrive"):
                    cands.append(("onedrive", "OneDrive", s))
                elif s.name.startswith("GoogleDrive"):
                    for sub in ("My Drive", "我的雲端硬碟"):
                        cands.append(("gdrive", "Google 雲端硬碟", s / sub))
                elif s.name.startswith("Dropbox"):
                    cands.append(("dropbox", "Dropbox", s))
        cands += [("onedrive", "OneDrive", home / "OneDrive"),
                  ("gdrive", "Google 雲端硬碟", home / "Google Drive"),
                  ("dropbox", "Dropbox", home / "Dropbox")]
    out, seen = [], set()
    for cid, label, root in cands:
        try:
            if not root.is_dir():
                continue
            key = os.path.normcase(str(root.resolve()))
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": cid, "label": label, "root": str(root), "path": str(root / SUGGESTED_SUBDIR)})
    return out


# ---------------------------------------------------------------- restore
def _safe_fit_target(fit_root: Path, entry: str) -> Optional[Path]:
    if not FIT_ENTRY_RE.match(entry):
        return None
    parts = PurePosixPath(entry[len(FIT_DIR) + 1:-3]).parts
    if any(x in ("", ".", "..") for x in parts):
        return None
    target = Path(fit_root).joinpath(*parts)
    root = Path(fit_root).resolve()
    try:
        if root not in target.resolve().parents:
            return None
    except OSError:
        return None
    return target


def open_backup(path: Path, work: Path) -> dict:
    """Unzip / validate into `work`. Returns {manifest, db, zip}."""
    path, work = Path(path), Path(work)
    if not path.is_file():
        raise BackupError("找不到備份檔")
    work.mkdir(parents=True, exist_ok=True)
    zpath = path
    if is_legacy_encrypted(path):
        raise BackupError(LEGACY_ENCRYPTED)
    if not zipfile.is_zipfile(zpath):
        raise BackupError("不是有效的備份檔（zip 打不開）")
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
        if MANIFEST not in names or DB_ENTRY not in names:
            raise BackupError("備份檔裡缺少 manifest 或資料庫")
        try:
            manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BackupError("manifest 讀不懂，檔案可能損毀")
        if manifest.get("app") != APP:
            raise BackupError("這不是 TrailRunCoach 的備份")
        if not isinstance(manifest.get("format"), int) or manifest["format"] > FORMAT:
            raise BackupError("這個備份是較新版本的 App 建立的，請先更新 App")
        db = work / "restore.db"
        with z.open(DB_ENTRY) as src, open(db, "wb") as dst:
            shutil.copyfileobj(src, dst, CHUNK)
    if manifest.get("db_sha256") and _sha256(db) != manifest["db_sha256"]:
        raise BackupError("資料庫檔案的檢查碼不符，檔案已損毀")
    if not integrity_ok(db):
        raise BackupError("備份裡的資料庫沒通過完整性檢查（integrity_check）")
    con = sqlite3.connect(str(db))
    try:
        schema, counts = schema_info(con)
    finally:
        con.close()
    return {"manifest": manifest, "db": db, "zip": zpath, "schema_version": schema, "row_counts": counts}


def _preserved_rows(con: sqlite3.Connection) -> list[tuple]:
    try:
        return con.execute("SELECT user_id, key, value_json, updated_at FROM user_settings WHERE key LIKE ?",
                           (PRESERVED_KEYS,)).fetchall()
    except sqlite3.Error:
        return []


def _secret_rows(con: sqlite3.Connection) -> dict:
    """This machine's SECRET tables before a restore: {table: (create sql, index sqls,
    [(column, declared type)], rows)}."""
    out = {}
    for t in R.secret_tables():
        try:
            row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
            if not row:
                continue
            idx = [r[0] for r in con.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (t,))]
            cols = [(r[1], r[2]) for r in con.execute(f"PRAGMA table_info({_q(t)})")]
            rows = con.execute(f"SELECT {', '.join(_q(c) for c, _ in cols)} FROM {_q(t)}").fetchall()
        except sqlite3.Error:
            continue
        out[t] = (row[0], idx, cols, rows)
    return out


def _put_back_secrets(con: sqlite3.Connection, keep: dict) -> None:
    """After the backup was copied in: the SECRET tables hold this machine's rows again,
    whatever the backup had (a format-1 backup's sync_state is not taken over). A backup
    with an older schema gets the missing table / columns first (init_db finds them there)."""
    present = _tables(con)
    for t in R.secret_tables():
        if t in keep:
            create, idx, cols, rows = keep[t]
            if t not in present:
                con.execute(create)
                for s in idx:
                    con.execute(s)
            else:
                have = {r[1] for r in con.execute(f"PRAGMA table_info({_q(t)})")}
                for c, typ in cols:
                    if c not in have:
                        con.execute(f"ALTER TABLE {_q(t)} ADD COLUMN {_q(c)} {typ}")
            con.execute(f"DELETE FROM {_q(t)}")
            if rows:
                con.executemany(f"INSERT INTO {_q(t)} ({', '.join(_q(c) for c, _ in cols)}) "
                                f"VALUES ({', '.join('?' * len(cols))})", rows)
        elif t in present:
            con.execute(f"DELETE FROM {_q(t)}")
    con.commit()


def _write_db_into(src: Path, live: Path) -> None:
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(live), timeout=30)
    try:
        keep = _preserved_rows(d)
        secrets_kept = _secret_rows(d)
        s.backup(d)
        if keep:
            try:
                d.execute("DELETE FROM user_settings WHERE key LIKE ?", (PRESERVED_KEYS,))
                d.executemany("INSERT INTO user_settings (user_id, key, value_json, updated_at) "
                              "VALUES (?, ?, ?, ?)", keep)
                d.commit()
            except sqlite3.Error:
                d.rollback()
        try:
            _put_back_secrets(d, secrets_kept)
        except sqlite3.Error as e:          # the restore itself is done; the login may need redoing
            d.rollback()
            log.warning("restore: this machine's %s could not be put back: %s", R.secret_tables(), e)
    finally:
        d.close()
        s.close()


def _safe_file_target(roots: dict, rel: str, entries: list) -> Optional[Path]:
    """Where a files/<rel> entry goes: only a registered backup=ALWAYS path, inside its
    tenant folder; anything else (a secret, a cache, ../, an absolute path) is skipped."""
    if not rel or "\\" in rel or ":" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(x in ("", ".", "..") for x in parts) or _skipped(parts[-1]):
        return None
    e = R.classify(rel)
    if e is None or e not in entries:
        return None
    base = Path(roots[e.where])
    target = base.joinpath(*parts)
    try:
        if base.resolve() not in target.resolve().parents:
            return None
    except OSError:
        return None
    return target


def _restore_files(zpath: Path, roots: dict) -> int:
    """Put the backup's files/ back (overwriting this machine's copy, atomically); a file
    the backup does not hold is left as it is. Returns how many were written."""
    entries = file_entries()
    n = 0
    with zipfile.ZipFile(zpath) as z:
        for zi in z.infolist():
            if zi.is_dir() or not zi.filename.startswith(FILES_DIR + "/"):
                continue
            target = _safe_file_target(roots, zi.filename[len(FILES_DIR) + 1:], entries)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".restore.tmp")
            try:
                with z.open(zi) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, CHUNK)
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
            n += 1
    return n


def restore(path: Path, live_db: Path, *, local_dir: Path, fit_root: Optional[Path] = None,
            roots: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    """Validate the backup, save the current DB and files to local_dir first, then copy
    the backup into the live DB (this machine's SECRET tables kept) and its files/ into
    `roots` (tenant_roots; default: the DB's folder). FIT originals in the backup that
    are missing locally are written into fit_root (existing files are never overwritten)."""
    if not _LOCK.acquire(blocking=False):
        raise Busy("另一個備份或還原正在進行中")
    try:
        live_db, local_dir = Path(live_db), Path(local_dir)
        roots = roots or tenant_roots(live_db.parent)
        local_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local_dir, prefix=".trc-restore-") as td:
            info = open_backup(path, Path(td))
            pre = None
            if live_db.exists():
                local = (now or datetime.now(timezone.utc)).astimezone()
                pre = local_dir / f"trailruncoach-prerestore-{local:%Y%m%d-%H%M%S}.zip"
                build_zip(live_db, pre, roots=roots, now=local)
                for _, old in _ours(local_dir, PRE_RE)[KEEP_PRERESTORE:]:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            _write_db_into(info["db"], live_db)
            try:
                files_restored = _restore_files(info["zip"], roots)
            except OSError as e:
                raise BackupError(f"資料庫已還原，但放回你改過的檔案時出錯（{e.strerror or e}）；"
                                  f"還原前的資料在 {pre or '–'}")
            fit_restored = 0
            if fit_root is not None and info["manifest"].get("fit", {}).get("included"):
                with zipfile.ZipFile(info["zip"]) as z:
                    for entry in z.namelist():
                        target = _safe_fit_target(Path(fit_root), entry)
                        if target is None or target.exists():
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(gzip.decompress(z.read(entry)))
                        fit_restored += 1
        m = info["manifest"]
        return {"created_at": m.get("created_at"), "app_version": m.get("app_version"),
                "schema_version": m.get("schema_version"), "row_counts": info["row_counts"],
                "pre_restore": str(pre) if pre else None, "fit_restored": fit_restored,
                "files_restored": files_restored}
    finally:
        _LOCK.release()


def inspect(path: Path, work: Path) -> dict:
    """Validate without restoring (for the confirmation dialog)."""
    with tempfile.TemporaryDirectory(dir=work, prefix=".trc-inspect-") as td:
        info = open_backup(path, Path(td))
        m = info["manifest"]
        return {"created_at": m.get("created_at"), "app_version": m.get("app_version"),
                "schema_version": m.get("schema_version"), "row_counts": info["row_counts"],
                "fit": m.get("fit"), "files": m.get("files")}
