"""
Backfill `start_time_utc` and the athlete-local `workout_date` for rows
imported before those columns existed, then rebuild the cross-source de-dup.

    python -m backend.scripts.backfill_start_time            # dry run
    python -m backend.scripts.backfill_start_time --apply

FIT start times are UTC; .wko4 start times are local wall-clock (WKO5). The
time zone comes from the user setting `athlete.timezone`, then WKO5COACH_TZ,
then the machine's zone. Rows whose file can't be read are left alone.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def file_start(path: str):
    p = Path(path)
    fmt = p.suffix.lower().lstrip(".")
    if fmt == "fit":
        from backend.files.fit_reader import parse_fit
        return parse_fit(path).start_time, False
    if fmt == "wko4":
        from backend.files.wko4_reader import parse_wko4_metadata
        return parse_wko4_metadata(path).start_time, True
    return None, False


async def backfill(db: AsyncSession, athlete_id: int = 1, apply: bool = False) -> dict:
    from backend.db.models import WorkoutFile
    from backend.files.file_service import utc_and_local
    from backend.settings.repository import SettingsRepository
    from backend.sync import dedup

    tz = await SettingsRepository(db, athlete_id).timezone()
    rows = (await db.execute(select(WorkoutFile).where(
        WorkoutFile.athlete_id == athlete_id, WorkoutFile.start_time_utc.is_(None),
        WorkoutFile.file_format != "corrupt"))).scalars().all()
    fixed, date_changed, unreadable = 0, 0, 0
    for r in rows:
        try:
            start, is_local = file_start(r.file_path)
        except Exception:
            start = None
        if start is None:
            unreadable += 1
            continue
        utc, local_day = utc_and_local(start, tz, is_local)
        if local_day != r.workout_date:
            date_changed += 1
        if apply:
            r.start_time_utc, r.workout_date = utc, local_day
        fixed += 1
    out = {"candidates": len(rows), "fixed": fixed, "date_changed": date_changed,
           "unreadable": unreadable, "applied": apply}
    if apply:
        out["dedup"] = await dedup.rebuild(db, athlete_id)
        await db.commit()
    else:
        await db.rollback()
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--athlete-id", type=int, default=1)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    async def go():
        from backend.db.database import AsyncSessionLocal, init_db
        await init_db()          # adds the new columns if the app hasn't yet
        async with AsyncSessionLocal() as db:
            return await backfill(db, args.athlete_id, args.apply)

    print(asyncio.run(go()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
