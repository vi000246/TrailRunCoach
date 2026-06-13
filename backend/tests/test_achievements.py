import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(c):
    return asyncio.get_event_loop().run_until_complete(c)


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, WorkoutFile, WorkoutMetric
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    S = async_sessionmaker(engine, expire_on_commit=False)
    s = S()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    s.add_all([
        WorkoutFile(id=1, athlete_id=1, file_path="/a", file_format="fit", sport="running",
                    trail_classification="trail", workout_date=date(2025, 6, 1),
                    total_distance_m=12000, elevation_gain_m=720, duration_s=7200),
        WorkoutFile(id=2, athlete_id=1, file_path="/b", file_format="fit", sport="running",
                    trail_classification="road", workout_date=date(2025, 6, 5),
                    total_distance_m=8000, elevation_gain_m=40, duration_s=2400),
        # out of window
        WorkoutFile(id=3, athlete_id=1, file_path="/c", file_format="fit", sport="cycling",
                    workout_date=date(2024, 1, 1), total_distance_m=40000, elevation_gain_m=800,
                    duration_s=5400),
    ])
    await s.flush()
    s.add_all([
        WorkoutMetric(workout_id=1, metric_key="tss", value=180.0),
        WorkoutMetric(workout_id=2, metric_key="tss", value=60.0),
        WorkoutMetric(workout_id=3, metric_key="tss", value=300.0),
    ])
    await s.commit()
    return s


def test_achievements_sorted_by_load_within_window():
    async def _i():
        from backend.api.analytics import achievements
        s = await _session()
        resp = await achievements(athlete_id=1, date_from=date(2025, 1, 1),
                                  date_to=date(2025, 12, 31), limit=10, db=s)
        items = resp["items"]
        assert len(items) == 2  # id=3 is out of window
        # highest load (tss 180) first
        assert items[0]["load"] == 180.0
        top = items[0]
        assert top["distance_km"] == 12.0
        assert top["elevation_m"] == 720
        assert top["duration_s"] == 7200
        assert "summary" in top and top["summary"]  # derived description
    _run(_i())


def test_achievements_respects_limit():
    async def _i():
        from backend.api.analytics import achievements
        s = await _session()
        resp = await achievements(athlete_id=1, date_from=date(2025, 1, 1),
                                  date_to=date(2025, 12, 31), limit=1, db=s)
        assert len(resp["items"]) == 1
    _run(_i())
