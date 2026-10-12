"""
Convert the synced FIT files to gzip objects named by their content hash (SP-308).

    ~/.wko5coach/fit/coros/2025/<labelId>_<date>_<sport>.fit
        -> ~/.wko5coach/fit/coros/objects/<aa>/<sha256>.fit.gz        (files/fitstore.py)
    ~/.wko5coach/fit/tp/2025/tp_<date>_<workoutId>.fit
        -> ~/.wko5coach/fit/tp/objects/<aa>/<sha256>.fit.gz

    python -m backend.scripts.fit_objects_migrate            # dry run (default): counts and sizes only
    python -m backend.scripts.fit_objects_migrate --apply

Before --apply: stop the app (:8000) and back up ~/.wko5coach (auth-and-demo.plan.md §10.3 C2).
Run backend/scripts/migrate_fit_folders.py / migrate_coros_sport_names.py first if they still
have work (they only know `.fit` files).

For each workout_files row of a synced source (coros / trainingpeaks) whose file_path is a
file (not an object key yet):

1. the file must pass storage.confined (inside its source folder, no symlink); others are
   counted as refused and left alone, a missing file as missing;
2. read it (a legacy .fit.gz is gunzipped), sha256 of the FIT bytes, gzip -6 to the object
   key; the object is read back and must equal the FIT bytes before anything else changes;
3. a fit_objects row (size, source, COROS id, start) and workout_files.file_path = the key;
4. the parse caches move with it, so nothing is parsed again and no result changes: the
   FIT dataset cache entry (engine/wko5expr/fitcache.py: index.json entry + npz channels,
   now valid by the content hash) and the race-power per-file caches
   (racepower_cptests.json, racepower_power_source.json, racepower_bad_activity.json);
5. every BATCH rows: the caches are saved, the DB committed, and only then the old files
   (and their old cache entries) deleted.

Re-runnable / resumable: rows already pointing at an object are skipped; an object already
on disk is not written again; an old file left behind by an interrupted run (no row points
at it, its content is a recorded object) is removed. Two rows with the same bytes: the
second keeps its legacy path (workout_files.file_path is unique) and is listed. Files no row
points at are counted (untracked) and left alone. Other per-file caches keyed by the file
name (achievements, channel peaks, route index ...) are rebuilt once by the app.

Prints the counts and the size before / after.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import FitObject, WorkoutFile
from backend.files import fitstore
from backend.sync import storage

BATCH = 100
RACEPOWER_CACHES = ("racepower_cptests.json", "racepower_power_source.json", "racepower_bad_activity.json")


# ---------------------------------------------------------------------------
# the caches that move with a file
# ---------------------------------------------------------------------------

class _Caches:
    """The FIT dataset cache of each source folder (fitcache.FitStore) and the race-power
    per-file JSON caches of the tenant home; entries are copied old -> new before the DB
    commit and the old ones dropped after the old file is gone."""

    def __init__(self, home: Path, cache_base: Optional[Path] = None):
        self.home = Path(home)
        self.cache_base = cache_base
        self.fc: dict = {}
        self.rp: dict = {}
        self.rp_dirty: set = set()
        self.carried = {"fitcache": 0, "racepower": 0}

    def _fc(self, folder: str):
        from backend.engine.wko5expr import fitcache
        st = self.fc.get(folder)
        if st is None:
            st = self.fc[folder] = fitcache.FitStore(storage.source_dir(folder), base=self.cache_base)
        return st

    def _rp(self, name: str) -> Optional[dict]:
        if name not in self.rp:
            try:
                doc = json.loads((self.home / name).read_text("utf-8"))
                self.rp[name] = doc if isinstance(doc, dict) and isinstance(doc.get("files"), dict) else None
            except (OSError, ValueError):
                self.rp[name] = None
        return self.rp[name]

    def carry(self, folder: str, old: Path, old_blob: bytes, key: str) -> None:
        from backend.engine.wko5expr import fitcache
        new = fitstore.default_store().path(key)
        sha = fitstore.sha_of_key(key)
        # FIT dataset cache: index entry + npz
        st = self._fc(folder)
        try:
            old_rel = old.relative_to(st.dir.resolve()).as_posix()
        except ValueError:
            old_rel = None
        new_rel = key.split("/", 1)[1]
        e = st.files.get(old_rel) if old_rel else None
        if e and e.get("parse") == st.v["parse"] and new_rel not in st.files and (
                e.get("stamp") == fitcache.stamp_of(old) or e.get("sha") == hashlib.sha1(old_blob).hexdigest()):
            ne = json.loads(json.dumps(e))
            ne.pop("seen", None)
            nst = new.stat()
            ne["stamp"] = [nst.st_size, nst.st_mtime_ns]
            ne["sha"] = hashlib.sha1(new.read_bytes()).hexdigest()
            ne["sha256"] = sha
            src_npz, dst_npz = st.npz(old_rel), st.npz(new_rel)
            ok = True
            if src_npz.exists():
                try:
                    dst_npz.parent.mkdir(parents=True, exist_ok=True)
                    if not dst_npz.exists():
                        try:
                            os.link(src_npz, dst_npz)
                        except OSError:
                            shutil.copy2(src_npz, dst_npz)
                except OSError:
                    ok = False
            if ok:
                st.files[new_rel] = ne
                st.dirty = True
                self.carried["fitcache"] += 1
        # race-power caches: keys relative to <home>/fit, [size, whole-s mtime] stamps
        old_key = self._fit_rel(old)
        if old_key is None:
            return
        o_st, n_st = old.stat(), new.stat()
        o_stamp = [o_st.st_size, int(o_st.st_mtime)]
        n_stamp = [n_st.st_size, int(n_st.st_mtime)]
        for name in RACEPOWER_CACHES:
            doc = self._rp(name)
            if doc is None:
                continue
            files = doc["files"]
            hit = files.get(old_key)
            if not isinstance(hit, list) or key in files:
                continue
            if name == "racepower_cptests.json":
                if len(hit) == 2 and hit[0] == o_stamp:
                    v = hit[1]
                    if isinstance(v, dict) and "file" in v:
                        v = {**v, "file": new.name}
                    files[key] = [n_stamp, v]
                else:
                    continue
            elif hit[:2] == o_stamp:
                files[key] = n_stamp + hit[2:]
            else:
                continue
            self.rp_dirty.add(name)
            self.carried["racepower"] += 1

    def _fit_rel(self, p: Path) -> Optional[str]:
        """The race-power caches' key: the path relative to the tenant's fit/ folder."""
        root = storage.fit_root()
        for r in (root.resolve(), root):
            try:
                return str(Path(p).relative_to(r))
            except ValueError:
                continue
        return None

    def drop(self, folder: str, old: Path) -> None:
        st = self._fc(folder)
        try:
            old_rel = old.relative_to(st.dir.resolve()).as_posix()
        except ValueError:
            old_rel = None
        if old_rel and old_rel in st.files:
            del st.files[old_rel]
            st.dirty = True
            try:
                st.npz(old_rel).unlink()
            except OSError:
                pass
        old_key = self._fit_rel(old)
        for name in RACEPOWER_CACHES:
            doc = self._rp(name)
            if doc is not None and old_key in doc["files"]:
                del doc["files"][old_key]
                self.rp_dirty.add(name)

    def save(self) -> None:
        for st in self.fc.values():
            st.save()
        for name in sorted(self.rp_dirty):
            p = self.home / name
            tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                tmp.write_text(json.dumps(self.rp[name]), "utf-8")
                os.replace(tmp, p)
            finally:
                tmp.unlink(missing_ok=True)
        self.rp_dirty.clear()


# ---------------------------------------------------------------------------
# the migration
# ---------------------------------------------------------------------------

async def _has_table(db: AsyncSession, name: str) -> bool:
    r = await db.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name})
    return r.first() is not None


def _legacy_files(folder: str) -> list[Path]:
    """The files of a source folder outside objects/ (resolved, confined)."""
    root = storage.source_dir(folder)
    out = []
    if not root.is_dir():
        return out
    objects = (root / fitstore.OBJECTS).resolve()
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        try:
            rp = storage.confined(p, folder)
        except storage.PathNotConfined:
            continue
        if rp == objects or objects in rp.parents:
            continue
        n = rp.name.lower()
        if n.endswith(".fit") or n.endswith(".fit.gz"):
            out.append(rp)
    return out


async def migrate(db: AsyncSession, apply: bool = False, batch: int = BATCH, home: Optional[Path] = None,
                  cache_base: Optional[Path] = None, say: Callable[[str], None] = lambda s: None) -> dict:
    """Dry run (apply False: counts and sizes, nothing written) or the conversion. See the
    module docstring. Returns the counts."""
    if home is None:
        from backend import tenancy
        home = tenancy.shared_path()
    store = fitstore.default_store()
    out = {"applied": apply, "rows": 0, "converted": 0, "already": 0, "missing": 0, "refused": 0,
           "failed": 0, "duplicate_content": 0, "objects_written": 0, "old_files_deleted": 0,
           "leftovers_removed": 0, "untracked": 0, "bytes_before": 0, "bytes_after": 0,
           "duplicates": [], "failures": []}
    if not await _has_table(db, "workout_files"):
        return out                                 # an empty DB: nothing recorded, nothing to convert
    have_table = await _has_table(db, "fit_objects")
    if apply and not have_table:
        raise SystemExit("fit_objects is missing: start the app once (init_db) or run with the app's DB")
    sha_rows: dict = {}
    if have_table:
        sha_rows = {r.sha256: r for r in (await db.execute(select(FitObject))).scalars().all()}
    db_sources = tuple(storage.SOURCES.values())
    rows = (await db.execute(select(WorkoutFile).where(WorkoutFile.source.in_(db_sources))
                             .order_by(WorkoutFile.id))).scalars().all()
    all_paths = {fp for fp in (await db.execute(select(WorkoutFile.file_path))).scalars().all() if fp}
    live_keys = {fp for fp in all_paths if fitstore.is_object_key(fp)}
    caches = _Caches(home, cache_base) if apply else None
    pending: list[tuple[str, Path]] = []
    planned_objects: set = set()

    async def flush():
        if not apply:
            return
        caches.save()                              # the new cache entries first
        await db.commit()                          # then the rows
        for folder, old in pending:                # only then the old files
            try:
                old.unlink()
                out["old_files_deleted"] += 1
            except OSError as e:
                out["failures"].append([str(old), f"old file not deleted: {type(e).__name__}"])
            caches.drop(folder, old)
        caches.save()
        pending.clear()

    for wf in rows:
        out["rows"] += 1
        fp = wf.file_path
        if fitstore.is_object_key(fp):
            out["already"] += 1
            continue
        folder = storage.DB_TO_FOLDER.get(wf.source)
        try:
            old = storage.confined(fp, folder)
        except (storage.PathNotConfined, OSError, ValueError):
            out["refused"] += 1
            continue
        if not old.is_file():
            out["missing"] += 1
            continue
        try:
            blob = old.read_bytes()
            fit = fitstore.gunzip_if(blob)
        except (OSError, EOFError, gzip.BadGzipFile) as e:
            out["failed"] += 1
            out["failures"].append([str(old), type(e).__name__])
            continue
        sha = fitstore.sha256_hex(fit)
        ex = sha_rows.get(sha)
        key = fitstore.object_key(fitstore.folder_for(ex.source) if ex is not None else folder, sha)
        if key in live_keys:
            out["duplicate_content"] += 1
            out["duplicates"].append([str(old), key])
            continue
        exists = store.exists(key) or key in planned_objects
        out["bytes_before"] += len(blob)
        if not exists:
            out["bytes_after"] += len(fitstore.gzip_fit(fit))
        out["converted"] += 1
        live_keys.add(key)
        if not apply:
            planned_objects.add(key)
            continue
        try:
            store.put(key, fit)
            if store.get(key) != fit:              # the round trip, from disk
                raise OSError("object read back differs")
        except (OSError, ValueError) as e:
            out["failed"] += 1
            out["converted"] -= 1
            out["failures"].append([str(old), f"{type(e).__name__}: {e}"])
            live_keys.discard(key)
            continue
        if not exists:
            out["objects_written"] += 1
        if ex is None:
            ex = FitObject(sha256=sha, size=len(fit), source=wf.source, coros_activity_id=wf.coros_activity_id,
                           start_time=wf.start_time_utc, deleted=False)
            db.add(ex)
            sha_rows[sha] = ex
        else:
            ex.deleted = False
            ex.start_time = ex.start_time or wf.start_time_utc
            ex.coros_activity_id = ex.coros_activity_id or wf.coros_activity_id
        caches.carry(folder, old, blob, key)
        wf.file_path = key
        pending.append((folder, old))
        if len(pending) >= batch:
            await flush()
            say(f"  {out['converted']} converted ...")
    await flush()

    # left behind by an interrupted run: an old file nobody points at whose content is a
    # recorded, live object (its row was committed, the file not yet deleted)
    tracked = set()
    for fp in all_paths:
        if not fitstore.is_object_key(fp):
            try:
                tracked.add(Path(fp).resolve())
            except OSError:
                pass
    for folder in storage.SOURCES:
        for p in _legacy_files(folder):
            if p in tracked:
                continue
            try:
                sha = fitstore.sha256_hex(fitstore.gunzip_if(p.read_bytes()))
            except (OSError, EOFError, gzip.BadGzipFile):
                out["untracked"] += 1
                continue
            ex = sha_rows.get(sha)
            key = fitstore.object_key(fitstore.folder_for(ex.source), sha) if ex is not None else None
            if key is not None and key in live_keys and store.exists(key):
                out["leftovers_removed"] += 1
                if apply:
                    caches.drop(folder, p)
                    p.unlink()
            else:
                out["untracked"] += 1
    if apply:
        caches.save()
        out["cache_entries_carried"] = dict(caches.carried)
        from backend.engine.wko5expr.datasource import files_changed
        files_changed()
    else:
        await db.rollback()
    return out


def _report(out: dict) -> str:
    def mb(n):
        return f"{n / 1e6:.1f} MB"
    lines = [
        f"FIT objects migration ({'applied' if out['applied'] else 'dry run: nothing changed'})",
        f"  rows of synced sources: {out['rows']}",
        f"  converted:              {out['converted']}  (objects written: {out['objects_written']}, "
        f"old files deleted: {out['old_files_deleted']})",
        f"  already objects:        {out['already']}",
        f"  missing file:           {out['missing']}",
        f"  refused (outside the source folder / symlink): {out['refused']}",
        f"  same content as another row (left on its path): {out['duplicate_content']}",
        f"  failed:                 {out['failed']}",
        f"  leftovers of an interrupted run removed: {out['leftovers_removed']}",
        f"  files no row points at (left alone): {out['untracked']}",
        f"  size of the converted files: {mb(out['bytes_before'])} -> {mb(out['bytes_after'])}"
        + (f" ({100.0 * out['bytes_after'] / out['bytes_before']:.0f}%)" if out["bytes_before"] else ""),
    ]
    if "cache_entries_carried" in out:
        c = out["cache_entries_carried"]
        lines.append(f"  cache entries carried: FIT dataset {c['fitcache']}, race power {c['racepower']}")
    for f in out["failures"][:20]:
        lines.append(f"  ! {f[0]}: {f[1]}")
    for d in out["duplicates"][:20]:
        lines.append(f"  = {d[0]} has the content of {d[1]}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="convert (default: dry run)")
    ap.add_argument("--batch", type=int, default=BATCH, help="rows per DB commit")
    args = ap.parse_args(argv)

    from backend.db.database import db_path
    if not db_path().is_file():
        print(f"no app DB at {db_path()}: nothing to migrate")
        return 1

    async def go():
        from backend.db.database import AsyncSessionLocal, init_db
        if args.apply:
            await init_db()                      # the fit_objects table (a dry run changes nothing)
        async with AsyncSessionLocal() as db:
            return await migrate(db, apply=args.apply, batch=max(1, args.batch), say=print)

    print(_report(asyncio.run(go())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
