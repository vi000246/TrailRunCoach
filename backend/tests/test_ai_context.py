import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _run(c):
    return asyncio.get_event_loop().run_until_complete(c)


def test_context_includes_zones_and_prompt_has_knowledge():
    async def _i():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from backend.db.models import Base, Athlete, AthleteSettings
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(engine, expire_on_commit=False)
        s = S()
        s.add(Athlete(id=1, name="a", data_dir="/tmp"))
        await s.flush()
        s.add(AthleteSettings(athlete_id=1, effective_date=date(2025, 1, 1),
                              run_ftp_w=192, lthr=182))
        await s.commit()

        from backend.engine.ai.context import build_context, SYSTEM_PROMPT
        ctx = await build_context(s, 1)
        # context surfaces zone boundaries
        assert "功率區間" in ctx
        assert "Threshold" in ctx
        assert "心率區間" in ctx
        # system prompt carries knowledge + prescription instruction
        assert "Palladino" in SYSTEM_PROMPT
        assert "間歇" in SYSTEM_PROMPT
        assert "處方" in SYSTEM_PROMPT
    _run(_i())
