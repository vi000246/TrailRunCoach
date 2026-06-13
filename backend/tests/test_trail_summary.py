import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, WorkoutFile
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    s = Session()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    s.add_all([
        WorkoutFile(athlete_id=1, file_path="/t1.fit", file_format="fit", sport="running",
                    trail_classification="trail", workout_date=date(2025, 6, 1),
                    elevation_gain_m=600, duration_s=7200, total_distance_m=12000),
        WorkoutFile(athlete_id=1, file_path="/t2.fit", file_format="fit", sport="running",
                    trail_classification="trail", workout_date=date(2025, 6, 5),
                    elevation_gain_m=400, duration_s=3600, total_distance_m=8000),
        # road run — excluded
        WorkoutFile(athlete_id=1, file_path="/r.fit", file_format="fit", sport="running",
                    trail_classification="road", workout_date=date(2025, 6, 3),
                    elevation_gain_m=50, duration_s=3600, total_distance_m=10000),
    ])
    await s.commit()
    return s


def test_trail_summary_aggregates_only_trail():
    async def _inner():
        from backend.api.analytics import trail_summary
        s = await _session()
        resp = await trail_summary(athlete_id=1, date_from=date(2025, 1, 1),
                                   date_to=date(2025, 12, 31), db=s)
        assert resp["total_gain_m"] == 1000.0   # 600 + 400, road excluded
        assert resp["activity_count"] == 2
        assert len(resp["recent"]) == 2
        # VAM present on recent entries (m/hr)
        assert all("vam" in r for r in resp["recent"])
    _run(_inner())
