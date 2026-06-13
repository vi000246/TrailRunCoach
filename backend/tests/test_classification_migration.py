import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_migration_adds_classification_columns():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from backend.db.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        import backend.db.database as db_mod
        orig = db_mod.engine
        db_mod.engine = engine
        try:
            await db_mod._migrate_schema()
            await db_mod._migrate_schema()  # idempotent
            async with engine.begin() as conn:
                result = await conn.execute(text("PRAGMA table_info(workout_files)"))
                cols = {row[1] for row in result.fetchall()}
            assert "trail_classification" in cols
            assert "classification_overridden" in cols
        finally:
            db_mod.engine = orig

    _run(_inner())


def test_orm_has_classification_fields():
    from backend.db.models import WorkoutFile
    w = WorkoutFile(
        athlete_id=1,
        file_path="/x.fit",
        file_format="fit",
        trail_classification="trail",
        classification_overridden=True,
    )
    assert w.trail_classification == "trail"
    assert w.classification_overridden is True
