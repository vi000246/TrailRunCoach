import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from datetime import date


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_interpret_tsb_fresh():
    from backend.engine.algorithms.interpret import interpret_tsb
    r = interpret_tsb(8.0)
    assert r["status"] == "fresh"
    assert r["color"] == "green"
    assert r["label"] == "新鮮"
    assert "新鮮" in r["summary"]


def test_interpret_tsb_overreached():
    from backend.engine.algorithms.interpret import interpret_tsb
    r = interpret_tsb(-30.0)
    assert r["status"] == "overreached"
    assert r["color"] == "red"


def test_interpret_tsb_none():
    from backend.engine.algorithms.interpret import interpret_tsb
    r = interpret_tsb(None)
    assert r["status"] == "unknown"
    assert r["color"] == "gray"


async def _session_with_pmc(tsb):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base, Athlete, PmcCache
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    s = Session()
    s.add(Athlete(id=1, name="a", data_dir="/tmp"))
    await s.flush()
    s.add(PmcCache(athlete_id=1, date=date(2025, 6, 1), ctl=50, atl=45, tsb=tsb))
    await s.commit()
    return s


def test_endpoint_returns_interpretation_for_pmc():
    async def _inner():
        from backend.api.analytics import chart_interpretation
        s = await _session_with_pmc(tsb=8.0)
        resp = await chart_interpretation(chart="pmc", athlete_id=1, db=s)
        assert resp["status"] == "fresh"
        assert resp["summary"]
    _run(_inner())
