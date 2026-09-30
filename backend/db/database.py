from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from backend.db.models import Base

DB_PATH = Path.home() / ".wko5coach" / "wko5coach.db"
DB_PATH.parent.mkdir(exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _migrate_schema():
    """Add columns introduced after the initial schema without dropping data."""
    new_cols = [
        ("workout_files", "coros_activity_id", "TEXT"),
        ("workout_files", "coros_sport_type", "INTEGER"),
        ("workout_files", "elevation_gain_m", "REAL"),
        ("workout_files", "trail_classification", "TEXT"),
        ("workout_files", "classification_overridden", "INTEGER"),
        ("workout_files", "start_time_utc", "DATETIME"),
        ("workout_files", "duplicate_of", "INTEGER"),
        ("sync_state", "coros_access_token", "TEXT"),
        ("sync_state", "tp_web_cookie", "TEXT"),
        ("sync_state", "coros_token_expires", "DATETIME"),
        ("sync_state", "coros_last_sync_at", "DATETIME"),
        ("sync_state", "coros_email", "TEXT"),
        ("sync_state", "coros_base_url", "TEXT"),
        ("sync_state", "coros_user_id", "TEXT"),
        ("athlete_settings", "threshold_pace_s_per_km", "REAL"),
        ("athlete_settings", "run_ftp_w", "REAL"),
        ("athlete_settings", "initial_ctl_run", "REAL"),
        ("athlete_settings", "initial_atl_run", "REAL"),
        ("athlete_settings", "ai_provider", "TEXT"),
        ("athlete_settings", "ai_api_key",  "TEXT"),
        ("athlete_settings", "ai_model",    "TEXT"),
    ]
    async with engine.begin() as conn:
        for table, col, col_type in new_cols:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if col not in existing:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                )


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_schema()
