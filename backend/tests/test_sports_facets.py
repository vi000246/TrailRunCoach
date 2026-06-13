import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
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
        WorkoutFile(athlete_id=1, file_path="/r1.fit", file_format="fit", sport="running"),
        WorkoutFile(athlete_id=1, file_path="/r2.fit", file_format="fit", sport="running"),
        WorkoutFile(athlete_id=1, file_path="/b.fit", file_format="fit", sport="cycling"),
    ])
    await s.commit()
    return s


def test_facets_lists_sports_with_counts():
    async def _inner():
        from backend.api.sports import facets
        s = await _session()
        resp = await facets(athlete_id=1, db=s)
        by_key = {x["key"]: x for x in resp["sports"]}
        assert by_key["running"]["count"] == 2
        assert by_key["running"]["label"] == "跑步"
        assert by_key["cycling"]["count"] == 1
    _run(_inner())


def test_facets_empty_for_unknown_athlete():
    async def _inner():
        from backend.api.sports import facets
        s = await _session()
        resp = await facets(athlete_id=999, db=s)
        assert resp["sports"] == []
    _run(_inner())
