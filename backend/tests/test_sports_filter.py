import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from sqlalchemy import select, func
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
        WorkoutFile(athlete_id=1, file_path="/run1.fit", file_format="fit", sport="running"),
        WorkoutFile(athlete_id=1, file_path="/run2.fit", file_format="fit", sport="running"),
        WorkoutFile(athlete_id=1, file_path="/bike.fit", file_format="fit", sport="cycling"),
        WorkoutFile(athlete_id=1, file_path="/strength.fit", file_format="fit", sport="strength"),
    ])
    await s.commit()
    return s


def _count(session, sports):
    from backend.api.analytics import _sport_clause
    from backend.db.models import WorkoutFile

    async def _q():
        q = select(func.count(WorkoutFile.id)).where(_sport_clause(sports))
        return (await session.execute(q)).scalar()
    return _run(_q())


def test_sport_clause_none_returns_all():
    s = _run(_session())
    assert _count(s, None) == 4


def test_sport_clause_single_sport():
    s = _run(_session())
    assert _count(s, ["running"]) == 2


def test_sport_clause_multi_sport():
    s = _run(_session())
    assert _count(s, ["running", "cycling"]) == 3


def test_run_load_backward_compatible_default():
    """run_load with no sports arg must still behave as running-only."""
    async def _inner():
        from backend.api.analytics import run_load
        from backend.db.models import WorkoutFile, WorkoutMetric
        from datetime import date
        s = await _session()
        # add tss metrics: running gets tss, cycling gets tss too
        wfs = (await s.execute(select(WorkoutFile))).scalars().all()
        for w in wfs:
            w_date = date(2025, 1, 1)
            w.workout_date = w_date
            s.add(WorkoutMetric(workout_id=w.id, metric_key="tss", value=50.0))
        await s.commit()
        # explicit window covering the 2025-01-01 workouts
        df, dt = date(2024, 1, 1), date(2025, 12, 31)
        # FastAPI passes None when the query param is omitted → running-only default
        resp_default = await run_load(athlete_id=1, date_from=df, date_to=dt, sports=None, db=s)
        resp_all = await run_load(athlete_id=1, date_from=df, date_to=dt,
                                  sports=["running", "cycling", "strength"], db=s)
        # both return a series; the all-sports variant sums more tss on the date
        d = "2025-01-01"
        tss_default = next((p["tss"] for p in resp_default["series"] if p["date"] == d), 0)
        tss_all = next((p["tss"] for p in resp_all["series"] if p["date"] == d), 0)
        assert tss_all > tss_default
    _run(_inner())
