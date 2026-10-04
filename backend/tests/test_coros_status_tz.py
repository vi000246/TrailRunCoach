import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(c):
    return asyncio.run(c)


async def _session(expires):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, SyncState
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    S = async_sessionmaker(engine, expire_on_commit=False)
    s = S()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    s.add(SyncState(athlete_id=1, coros_access_token="tok", coros_email="x@y.z",
                    coros_token_expires=expires))
    await s.commit()
    return s


def test_status_handles_naive_expiry_without_crashing():
    """SQLite stores naive datetimes; status must not crash comparing to aware now."""
    async def _i():
        from backend.api.auth import coros_auth_status
        # naive future expiry (no tzinfo) — the real-world stored shape
        naive_future = datetime.utcnow() + timedelta(hours=10)
        s = await _session(naive_future)
        resp = await coros_auth_status(athlete_id=1, db=s)
        assert resp["authenticated"] is True  # not expired
    _run(_i())


def test_status_naive_past_expiry_is_unauthenticated():
    async def _i():
        from backend.api.auth import coros_auth_status
        naive_past = datetime.utcnow() - timedelta(hours=10)
        s = await _session(naive_past)
        resp = await coros_auth_status(athlete_id=1, db=s)
        assert resp["authenticated"] is False  # expired
    _run(_i())
