"""Backfill trail_classification for existing workouts.

Idempotent: re-running only updates rows whose computed classification
differs from the stored value, and never touches manually-overridden rows.

Usage:
    python -m backend.scripts.backfill_classification
"""
import asyncio

from sqlalchemy import select

from backend.db.models import WorkoutFile
from backend.engine.algorithms.classify import classify_trail


async def backfill(session) -> dict:
    rows = (await session.execute(select(WorkoutFile))).scalars().all()
    updated = skipped = 0
    for w in rows:
        if w.classification_overridden:
            skipped += 1
            continue
        new = classify_trail(w.sport, w.total_distance_m, w.elevation_gain_m)
        if new != w.trail_classification:
            w.trail_classification = new
            updated += 1
    await session.commit()
    return {"updated": updated, "skipped": skipped}


async def _main() -> None:
    from backend.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        result = await backfill(session)
    print(f"Backfill complete: {result['updated']} updated, {result['skipped']} skipped (overridden)")


if __name__ == "__main__":
    asyncio.run(_main())
