import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _session_with_trail():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, WorkoutFile, WorkoutMetric
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    s = Session()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    # two trail-classified running workouts + one road (must be excluded)
    trail1 = WorkoutFile(id=1, athlete_id=1, file_path="/t1.fit", file_format="fit",
                         sport="running", trail_classification="trail",
                         workout_date=date(2025, 6, 1))
    trail2 = WorkoutFile(id=2, athlete_id=1, file_path="/t2.fit", file_format="fit",
                         sport="running", trail_classification="trail",
                         workout_date=date(2025, 6, 2))
    road = WorkoutFile(id=3, athlete_id=1, file_path="/r.fit", file_format="fit",
                       sport="running", trail_classification="road",
                       workout_date=date(2025, 6, 1))
    s.add_all([trail1, trail2, road])
    await s.flush()
    s.add_all([
        WorkoutMetric(workout_id=1, metric_key="hr_tss", value=80.0),
        WorkoutMetric(workout_id=1, metric_key="r_tss", value=50.0),
        WorkoutMetric(workout_id=2, metric_key="hr_tss", value=90.0),
        WorkoutMetric(workout_id=2, metric_key="r_tss", value=60.0),
        WorkoutMetric(workout_id=3, metric_key="hr_tss", value=200.0),  # road — must be excluded
    ])
    await s.commit()
    return s


def test_trail_load_uses_hr_tss_and_excludes_road():
    async def _inner():
        from backend.api.analytics import trail_load
        s = await _session_with_trail()
        resp = await trail_load(athlete_id=1, date_from=date(2025, 1, 1),
                                date_to=date(2025, 12, 31), db=s)
        series = resp["series"]
        assert series, "expected non-empty trail PMC series"
        # point keys present
        p0 = series[0]
        for k in ("date", "ctl", "atl", "tsb", "hr_tss", "r_tss"):
            assert k in p0
        # the road workout's hr_tss=200 must not inflate any day's load
        day1 = next(p for p in series if p["date"] == "2025-06-01")
        assert day1["hr_tss"] == 80.0  # only the trail workout, road excluded
        assert day1["r_tss"] == 50.0
    _run(_inner())
