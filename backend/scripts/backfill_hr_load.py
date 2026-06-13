"""Backfill hrTSS for existing running activities from stored avg HR.

The import pipeline writes per-second hrTSS for new activities, but historical
workouts predate that. This approximates hrTSS from the already-computed
``avg_hr_bpm`` metric (the fallback the SRS/plan anticipated) so the trail PMC
populates without re-parsing FIT files. Idempotent and additive — only writes
``hr_tss`` where it is missing.

Usage:
    python -m backend.scripts.backfill_hr_load
"""
import asyncio

from sqlalchemy import select

from backend.db.models import WorkoutFile, WorkoutMetric, AthleteSettings
from backend.engine.algorithms.metrics import compute_hr_tss


async def backfill_hr_load(session, lthr) -> dict:
    if not lthr:
        return {"updated": 0, "skipped_no_lthr": True}

    # running workouts that have avg_hr_bpm but no hr_tss yet
    rows = (await session.execute(
        select(WorkoutFile.id, WorkoutFile.duration_s)
        .where(WorkoutFile.sport == "running")
    )).all()

    updated = 0
    for wid, duration_s in rows:
        metrics = {
            m.metric_key: m.value
            for m in (await session.execute(
                select(WorkoutMetric).where(WorkoutMetric.workout_id == wid)
            )).scalars().all()
        }
        if "hr_tss" in metrics:
            continue
        avg_hr = metrics.get("avg_hr_bpm")
        if not avg_hr or not duration_s:
            continue
        hr_tss = compute_hr_tss([avg_hr], lthr, duration_s)
        if hr_tss > 0:
            session.add(WorkoutMetric(workout_id=wid, metric_key="hr_tss", value=hr_tss))
            updated += 1

    await session.commit()
    return {"updated": updated}


async def _main() -> None:
    from backend.db.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        settings = (await session.execute(
            select(AthleteSettings).order_by(AthleteSettings.effective_date.desc())
        )).scalars().first()
        lthr = settings.lthr if settings else None
        result = await backfill_hr_load(session, lthr)
    if result.get("skipped_no_lthr"):
        print("Skipped: no LTHR set in athlete settings — cannot approximate hrTSS.")
    else:
        print(f"hrTSS backfill complete: {result['updated']} workouts updated (avg-HR approximation).")


if __name__ == "__main__":
    asyncio.run(_main())
