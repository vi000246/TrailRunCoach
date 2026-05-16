import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_migrate_schema_adds_new_columns():
    """_migrate_schema() must add 3 new columns to athlete_settings idempotently."""
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
            await db_mod._migrate_schema()  # idempotency check
            async with engine.begin() as conn:
                result = await conn.execute(text("PRAGMA table_info(athlete_settings)"))
                cols = {row[1] for row in result.fetchall()}
            assert "threshold_pace_s_per_km" in cols
            assert "initial_ctl_run" in cols
            assert "initial_atl_run" in cols
        finally:
            db_mod.engine = orig

    _run(_inner())


def test_athlete_settings_orm_has_new_fields():
    """AthleteSettings ORM model must have the 3 new fields as attributes."""
    from backend.db.models import AthleteSettings
    s = AthleteSettings(
        athlete_id=1,
        effective_date=date(2026, 1, 1),
        threshold_pace_s_per_km=300.0,
        initial_ctl_run=55.0,
        initial_atl_run=35.0,
    )
    assert s.threshold_pace_s_per_km == 300.0
    assert s.initial_ctl_run == 55.0
    assert s.initial_atl_run == 35.0
