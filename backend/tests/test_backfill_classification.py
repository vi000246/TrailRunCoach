import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(coro):
    return asyncio.run(coro)


def test_backfill_classifies_and_skips_overridden():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from backend.db.models import Base, Athlete, WorkoutFile
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)

        from backend.scripts.backfill_classification import backfill

        async with Session() as s:
            s.add(Athlete(id=1, name="a", data_dir="/tmp"))
            await s.flush()
            s.add_all([
                WorkoutFile(athlete_id=1, file_path="/trail.fit", file_format="fit",
                            sport="running", total_distance_m=10000, elevation_gain_m=600),
                WorkoutFile(athlete_id=1, file_path="/road.fit", file_format="fit",
                            sport="running", total_distance_m=10000, elevation_gain_m=30),
                WorkoutFile(athlete_id=1, file_path="/bike.fit", file_format="fit",
                            sport="cycling", total_distance_m=40000, elevation_gain_m=800),
                WorkoutFile(athlete_id=1, file_path="/over.fit", file_format="fit",
                            sport="running", total_distance_m=10000, elevation_gain_m=600,
                            trail_classification="road", classification_overridden=True),
            ])
            await s.commit()

            result = await backfill(s)
            # default is already "unknown" → only trail & road rows change; bike stays unknown
            assert result["updated"] == 2   # trail, road
            assert result["skipped"] == 1   # overridden one

            from sqlalchemy import select
            rows = {w.file_path: w.trail_classification
                    for w in (await s.execute(select(WorkoutFile))).scalars().all()}
            assert rows["/trail.fit"] == "trail"
            assert rows["/road.fit"] == "road"
            assert rows["/bike.fit"] == "unknown"
            assert rows["/over.fit"] == "road"  # untouched

            # idempotent: second run updates nothing
            again = await backfill(s)
            assert again["updated"] == 0

    _run(_inner())
