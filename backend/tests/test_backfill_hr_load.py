import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_backfill_hr_load_from_avg_hr():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from backend.db.models import Base, Athlete, WorkoutFile, WorkoutMetric
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        from backend.scripts.backfill_hr_load import backfill_hr_load

        async with Session() as s:
            s.add(Athlete(id=1, name="a", data_dir="/tmp"))
            await s.flush()
            # trail workout: 1hr at avg HR = lthr → hrTSS ≈ 100
            s.add(WorkoutFile(id=1, athlete_id=1, file_path="/t.fit", file_format="fit",
                              sport="running", trail_classification="trail",
                              duration_s=3600, total_distance_m=8000,
                              workout_date=date(2025, 6, 1)))
            await s.flush()
            s.add(WorkoutMetric(workout_id=1, metric_key="avg_hr_bpm", value=160.0))
            await s.commit()

            result = await backfill_hr_load(s, lthr=160)
            assert result["updated"] == 1

            hr = (await s.execute(
                select(WorkoutMetric.value)
                .where(WorkoutMetric.workout_id == 1, WorkoutMetric.metric_key == "hr_tss")
            )).scalar()
            assert hr is not None
            assert abs(hr - 100.0) < 5.0

            # idempotent: second run writes nothing new
            again = await backfill_hr_load(s, lthr=160)
            assert again["updated"] == 0

    _run(_inner())


def test_backfill_hr_load_skips_when_no_lthr():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from backend.db.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        from backend.scripts.backfill_hr_load import backfill_hr_load
        async with Session() as s:
            result = await backfill_hr_load(s, lthr=None)
            assert result["updated"] == 0
            assert result.get("skipped_no_lthr")

    _run(_inner())
