"""
Rename COROS sync files whose sport word is wrong and fix their DB rows.

The old COROS code map had 100 = "cycling", so every run was saved as
`<labelId>_<date>_cycling.fit` (and trail runs / strength as `_other`). The
sport word now comes from the FIT session (sync/coros_sport.py):

    480707326343414059_2026-09-30_cycling.fit -> 480707326343414059_2026-09-30_run.fit
    480470392861917592_2026-09-20_other.fit   -> 480470392861917592_2026-09-20_trail_run.fit

    python -m backend.scripts.migrate_coros_sport_names            # dry run (default)
    python -m backend.scripts.migrate_coros_sport_names --apply

Confined to ~/.wko5coach/fit/coros/ (storage.confined; symlinks refused).
Idempotent: a file already named from its FIT is left alone, so a second run
changes nothing. Rows are matched by their exact old file_path; a row's
`sport` is set from the FIT too when it disagrees (it normally already
matches — file_service reads it from the FIT). A target that already exists
with other content is a conflict and left in place.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import WorkoutFile
from backend.files.fit_reader import _normalize_sport
from backend.sync import storage
from backend.sync.coros_sport import NAME_RE, fit_session_sport, renamed


async def migrate(db: AsyncSession, apply: bool = False) -> dict:
    root = storage.source_dir("coros")
    out = {"renamed": 0, "already": 0, "conflict": 0, "skipped": 0, "rows_updated": 0,
           "sport_fixed": 0, "changes": []}
    rows = (await db.execute(select(WorkoutFile).where(WorkoutFile.source == "coros"))).scalars().all()
    by_path: dict[str, list[WorkoutFile]] = {}
    for wf in rows:
        by_path.setdefault(str(Path(wf.file_path)), []).append(wf)
        try:
            by_path.setdefault(str(Path(wf.file_path).resolve()), []).append(wf)
        except OSError:
            pass
    files = sorted(p for p in root.rglob("*.fit") if p.is_file()) if root.exists() else []
    for p in files:
        try:
            p = storage.confined(p, "coros")
        except storage.PathNotConfined:
            out["skipped"] += 1
            continue
        if not NAME_RE.match(p.name):
            out["skipped"] += 1
            continue
        wfs = list({id(w): w for w in by_path.get(str(p), [])}.values())
        code = next((w.coros_sport_type for w in wfs if w.coros_sport_type is not None), None)
        new = renamed(p, code)
        if new is not None:
            new = storage.confined(new, "coros")
        if new is None:
            out["already"] += 1
        elif new.exists() and new.read_bytes() != p.read_bytes():
            out["conflict"] += 1
            continue
        else:
            out["renamed"] += 1
            out["changes"].append([p.name, new.name])
            if apply:
                if new.exists():
                    p.unlink()               # same bytes already there
                else:
                    p.rename(new)
            for wf in wfs:
                out["rows_updated"] += 1
                if apply:
                    wf.file_path = str(new)
        sport, _ =fit_session_sport(new if (new is not None and apply) else p)
        if sport:
            want = _normalize_sport(sport)
            for wf in wfs:
                if wf.sport != want:
                    out["sport_fixed"] += 1
                    if apply:
                        wf.sport = want
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
