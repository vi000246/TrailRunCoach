"""SP-509: several 「不要」 at the same moment. POST /suggestions/dismiss reads `plan.suggestions.dismissed`, adds
its id and writes the whole dict back; two requests doing that at once used to collide — the first one INSERTs the
setting row, the next one INSERTs it again (UNIQUE user_id + key → HTTP 500), or, once the row exists, writes back
the dict it read before the other one's write (that 「不要」 is lost). Each request here has its own DB session on a
real SQLite file (WAL, as backend/db/database.py), like the app; synthetic data only."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.database import _on_connect
from backend.db.models import Base, UserSetting
from backend.engine import suggestions as SG
from backend.tests.test_plan_store import API, inputs

IDS = ["zone:a", "zone:b", "zone:c", "b2b:2026-09-28", "test:cp:2026-09-28"]


async def _go(tmp_path, monkeypatch, pre: dict | None):
    from backend.api import plan_sessions
    from backend.db.database import get_db
    from backend.engine import plan_prefs as PP

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    event.listen(eng.sync_engine, "connect", _on_connect)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    mk = async_sessionmaker(eng, expire_on_commit=False)
    if pre is not None:
        async with mk() as s:
            s.add(UserSetting(user_id=1, key=SG.KEY, value_json=json.dumps(pre)))
            await s.commit()

    inp = inputs()
    monkeypatch.setattr(plan_sessions, "_compute_inputs", lambda: inp)
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs())
    # every request reads the setting before any of them writes it (the moment the clicks land together)
    read = plan_sessions._dismissed

    async def slow_read(db):
        v = await read(db)
        await asyncio.sleep(0.05)
        return v
    monkeypatch.setattr(plan_sessions, "_dismissed", slow_read)

    async def fake_db():
        async with mk() as s:
            yield s
    app = FastAPI()
    app.include_router(plan_sessions.router)
    app.dependency_overrides[get_db] = fake_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://t") as c:
        rs = await asyncio.gather(*(c.post(f"{API}/suggestions/dismiss", json={"id": i, "action": "declined"})
                                    for i in IDS))
    async with mk() as s:
        row = (await s.execute(select(UserSetting).where(UserSetting.key == SG.KEY))).scalar_one()
        stored = json.loads(row.value_json)
    await eng.dispose()
    return [r.status_code for r in rs], stored


@pytest.mark.parametrize("pre", [None, {"zone:old": {"action": "dismissed", "at": "2026-09-29T08:00:00"}}],
                         ids=["first-dismissal", "setting-exists"])
def test_dismissals_at_the_same_moment_all_count(tmp_path, monkeypatch, pre):
    codes, stored = asyncio.run(_go(tmp_path, monkeypatch, pre))
    assert codes == [200] * len(IDS)
    assert set(stored) == set(IDS) | set(pre or {})
    assert all(stored[i]["action"] == "declined" for i in IDS)
