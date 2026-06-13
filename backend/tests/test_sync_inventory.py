import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date, datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(c):
    return asyncio.get_event_loop().run_until_complete(c)


async def _session(empty=False):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, WorkoutFile, SyncState
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    S = async_sessionmaker(engine, expire_on_commit=False)
    s = S()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    if not empty:
        s.add_all([
            WorkoutFile(athlete_id=1, file_path="/a", file_format="fit", source="coros",
                        sport="running", workout_date=date(2025, 1, 1)),
            WorkoutFile(athlete_id=1, file_path="/b", file_format="fit", source="local",
                        sport="cycling", workout_date=date(2026, 5, 1)),
        ])
        s.add(SyncState(athlete_id=1, coros_last_sync_at=datetime(2026, 5, 15)))
    await s.commit()
    return s


def test_inventory_groups_by_source_and_sport():
    async def _i():
        from backend.api.sync import sync_inventory
        s = await _session()
        r = await sync_inventory(athlete_id=1, db=s)
        assert r["total"] == 2
        assert r["by_source"]["coros"] == 1 and r["by_source"]["local"] == 1
        assert r["by_sport"]["running"] == 1
        assert r["date_min"] == "2025-01-01" and r["date_max"] == "2026-05-01"
        assert r["last_sync"]["coros"] is not None
        assert r["last_sync"]["tp"] is None
    _run(_i())


def test_inventory_empty_athlete():
    async def _i():
        from backend.api.sync import sync_inventory
        s = await _session(empty=True)
        r = await sync_inventory(athlete_id=1, db=s)
        assert r["total"] == 0
        assert r["date_min"] is None and r["date_max"] is None
        assert r["last_sync"]["coros"] is None
    _run(_i())
