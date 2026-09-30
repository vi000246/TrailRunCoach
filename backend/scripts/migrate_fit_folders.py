"""
Move synced FIT files into per-source folders and update the DB paths.

    ~/.wko5coach/fits/<athlete>/<year>/…      (old COROS)  -> ~/.wko5coach/fit/coros/<year>/…
    ~/.wko5coach/fit/athlete_<n>/<year>/…     (old TP)     -> ~/.wko5coach/fit/tp/<year>/…

    python -m backend.scripts.migrate_fit_folders            # dry run (default)
    python -m backend.scripts.migrate_fit_folders --apply

Idempotent: rows already inside their source folder are left alone; a file
whose target already exists with the same size is treated as moved. Only
files the DB knows about are moved; anything else found in the legacy
folders is counted and left in place. Empty legacy folders are removed on
--apply.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import WorkoutFile
from backend.sync import storage

LEGACY_DIRS = {
    "coros": lambda: [Path.home() / ".wko5coach" / "fits"],
    "tp": lambda: sorted(p for p in storage.FIT_ROOT.glob("athlete_*") if p.is_dir()),
}


def _year(wf: WorkoutFile, old: Path) -> str:
    if wf.workout_date:
        return str(wf.workout_date.year)
    return old.parent.name if old.parent.name.isdigit() else "unknown"


async def migrate(db: AsyncSession, apply: bool = False, legacy_dirs: Optional[dict] = None) -> dict:
    legacy_dirs = legacy_dirs or {k: f() for k, f in LEGACY_DIRS.items()}
    out = {src: {"moved": 0, "already": 0, "missing": 0, "conflict": 0, "untracked": 0}
           for src in storage.SOURCES}
    rows = (await db.execute(select(WorkoutFile).where(
        WorkoutFile.source.in_(tuple(storage.SOURCES.values()))))).scalars().all()
    tracked = set()
    for wf in rows:
        src = storage.DB_TO_FOLDER[wf.source]
        old = Path(wf.file_path)
        tracked.add(str(old.resolve()) if old.exists() else str(old))
        root = storage.source_dir(src).resolve()
        if old.exists() and root in old.resolve().parents:
            out[src]["already"] += 1
            continue
        target = storage.source_dir(src) / _year(wf, old) / old.name
        if not old.exists():
            if target.exists():          # moved earlier, DB not updated
                if apply:
                    wf.file_path = str(target)
                out[src]["already"] += 1
            else:
                out[src]["missing"] += 1
            continue
        if target.exists() and target.stat().st_size != old.stat().st_size:
            out[src]["conflict"] += 1
            continue
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                old.unlink()
            else:
                shutil.move(str(old), str(target))
            wf.file_path = str(target)
        out[src]["moved"] += 1
    for src, dirs in legacy_dirs.items():
        for d in dirs:
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if p.is_file() and str(p.resolve()) not in tracked:
                    out[src]["untracked"] += 1
            if apply:
                for sub in sorted((x for x in d.rglob("*") if x.is_dir()), reverse=True):
                    if not any(sub.iterdir()):
                        sub.rmdir()
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()
    # athletes whose data_dir was a (now removed) legacy TP folder
    from backend.db.models import Athlete
    legacy_tp = {str(p.resolve()) for p in legacy_dirs.get("tp", [])}
    out["athlete_dirs_updated"] = 0
    for a in (await db.execute(select(Athlete))).scalars():
        if a.data_dir and (str(Path(a.data_dir).resolve()) in legacy_tp
                           or (Path(a.data_dir).parent == storage.FIT_ROOT and Path(a.data_dir).name.startswith("athlete_"))):
            out["athlete_dirs_updated"] += 1
            if apply:
                a.data_dir = str(storage.FIT_ROOT)
    if apply:
        await db.commit()
    else:
        await db.rollback()
    out["applied"] = apply
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    async def go():
        from backend.db.database import AsyncSessionLocal, init_db
        await init_db()
        async with AsyncSessionLocal() as db:
            return await migrate(db, apply=args.apply)

    print(asyncio.run(go()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
