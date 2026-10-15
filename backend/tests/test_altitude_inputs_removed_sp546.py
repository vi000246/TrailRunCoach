"""
SP-546 (owner 2026-10-14, 「高度適應功能有點煩」): nothing about altitude acclimatisation is filled in or
shown on a page any more — the 課表 calendar's 「記錄睡在高處」 (SP-259: menu, ⛺ mark, dialog, the
`high_nights` payload, the /high-nights API) is gone; the race calculator's 海拔 choice is covered by
test_altitude_accl_default.py; the reminder itself by test_altitude.py. Old data stays: the stored
`altitude.nights` setting is neither deleted nor migrated (still validated on write).
"""
import asyncio
import json
from pathlib import Path

import pytest

from backend.engine import altitude as AL

STATIC = Path(__file__).resolve().parents[1] / "static"


def test_schedule_page_has_no_high_night_menu_mark_or_dialog():
    s = (STATIC / "schedule.html").read_text("utf-8")
    for el in ('act: "high"', 'act: "unhigh"', "openHigh(", "delHigh(", "high-nights", "high_nights",
               'data-hn="', ".hn-tag", "hn-m", "睡在高處"):
        assert el not in s, el
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "schedule.json").read_text("utf-8"))
        assert not [k for k in cat if k.startswith(("hn.", "ctx.high"))], loc


def test_no_high_nights_api_route_or_demo_write():
    from backend import main
    from backend import tenancy_mw as MW
    paths = {getattr(r, "path", "") for r in main.app.routes}
    assert not [p for p in paths if "high-nights" in p]
    ok = lambda m, p: any(m in ms and rx.match(p) for ms, rx in MW.WRITE_ALLOW)   # noqa: E731
    assert not ok("PUT", "/api/v1/overview/plan/high-nights/2026-10-01")
    assert not hasattr(AL, "load_nights") and not hasattr(AL, "event_night")


def test_the_calendar_payload_has_no_high_nights():
    import inspect

    from backend.api import plan_sessions as PS
    assert "high_nights" not in inspect.getsource(PS)


def test_old_recorded_nights_stay_in_the_database(tmp_path, monkeypatch):
    """A DB with recorded nights goes through init_db() twice as on start-up: the row is still there,
    unchanged, and a write is still validated (no destructive migration)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    import backend.db.database as db_mod
    from backend.db.models import Base
    from backend.settings.repository import SettingsRepository
    path = tmp_path / "old.db"
    stored = [{"day": "2026-09-20", "m": 3150}]

    async def make_old():
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("INSERT INTO user_settings (user_id, key, value_json, updated_at) "
                                    "VALUES (1, 'altitude.nights', :v, '2026-09-21')"), {"v": json.dumps(stored)})
        await eng.dispose()
    asyncio.run(make_old())
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        await db_mod.init_db()
        await db_mod.init_db()
        async with db_mod.AsyncSessionLocal() as db:
            repo = SettingsRepository(db, 1)
            got = await repo.get(AL.NIGHTS_KEY)
            with pytest.raises(ValueError):
                await repo.set(AL.NIGHTS_KEY, [{"day": "2026-10-01", "m": "high"}])
        await db_mod.dispose(path)
        return got
    assert asyncio.run(go()) == stored
