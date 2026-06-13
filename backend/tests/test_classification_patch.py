import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _session_with_workout():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, WorkoutFile
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    s = Session()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    s.add(WorkoutFile(id=10, athlete_id=1, file_path="/r.fit", file_format="fit",
                      sport="running", total_distance_m=10000, elevation_gain_m=30,
                      trail_classification="road"))
    await s.commit()
    return s


def test_patch_sets_classification_and_override():
    async def _inner():
        from backend.api.workouts import update_classification, ClassificationUpdate
        s = await _session_with_workout()
        resp = await update_classification(10, ClassificationUpdate(trail_classification="trail"), s)
        assert resp["trail_classification"] == "trail"
        assert resp["classification_overridden"] is True
    _run(_inner())


def test_patch_invalid_value_400():
    async def _inner():
        from backend.api.workouts import update_classification, ClassificationUpdate
        s = await _session_with_workout()
        with pytest.raises(HTTPException) as exc:
            await update_classification(10, ClassificationUpdate(trail_classification="hiking"), s)
        assert exc.value.status_code == 400
    _run(_inner())


def test_patch_missing_workout_404():
    async def _inner():
        from backend.api.workouts import update_classification, ClassificationUpdate
        s = await _session_with_workout()
        with pytest.raises(HTTPException) as exc:
            await update_classification(999, ClassificationUpdate(trail_classification="trail"), s)
        assert exc.value.status_code == 404
    _run(_inner())
