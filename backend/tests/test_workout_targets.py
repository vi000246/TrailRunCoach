"""
The workout-sync provider interface (backend/sync/workout_targets): registry, the COROS
provider delegating to coros_workouts (mocked — no network), the Garmin / intervals.icu
stubs (offline payload only, every network call refused), the provider setting and the
provider column on coros_plan_push. In-memory DB, synthetic sessions.
"""
import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Athlete, Base, CorosPlanPush
from backend.settings.repository import SettingsRepository, validate
from backend.sync import coros_workouts as CW
from backend.sync import workout_targets as WT

TH = {"cp": 300.0, "lthr": 170.0, "aet": 150.0, "tpace": 280.0}
EASY = {"id": "s1", "key": "u1", "kind": "easy", "title": "輕鬆跑", "minutes": 40, "day": "2026-10-07"}


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _db():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    S = async_sessionmaker(eng, expire_on_commit=False)
    s = S()
    s.add(Athlete(id=1, name="t", data_dir="x"))
    await s.commit()
    return eng, s


def test_registry_one_enabled_provider_and_the_stubs_listed():
    ids = [p["id"] for p in WT.available()]
    assert ids == ["coros", "garmin", "intervals"]
    assert WT.enabled_ids() == ("coros",)
    co = WT.get("coros").describe()
    assert co["enabled"] and "pace" in co["capabilities"]["targets"] and co["capabilities"]["distance_unit"] == "cm"
    assert not WT.get("garmin").enabled and not WT.get("intervals").enabled
    # an unknown / disabled stored value never selects a stub
    assert WT.resolve("garmin").id == "coros" and WT.resolve(None).id == "coros" and WT.resolve("x").id == "coros"


def test_stubs_build_offline_payloads_and_refuse_the_network():
    g = WT.get("garmin").build_payload(EASY, TH)
    assert g["workoutName"].startswith("TRC 輕鬆跑") and g["workoutSegments"][0]["workoutSteps"]
    ev = WT.get("intervals").build_payload(EASY, TH)
    assert ev["category"] == "WORKOUT" and ev["start_date_local"].startswith("2026-10-07")
    with pytest.raises(WT.Unsupported):
        WT.get("garmin").build_payload({**EASY, "kind": "race"}, TH)
    for pid in ("garmin", "intervals"):
        p = WT.get(pid)
        with pytest.raises(WT.ProviderDisabled):
            run(p.push_sessions(None, [EASY], TH, "2026-10-01"))
        with pytest.raises(WT.ProviderDisabled):
            run(p.remove_keys(None, ["u1"]))
        with pytest.raises(WT.ProviderDisabled):
            run(p.list_remote(None))


def test_coros_provider_is_coros_workouts(monkeypatch):
    p = WT.get("coros")
    assert p.build_payload(EASY, TH) == CW.session_workout(EASY, TH).payload
    assert p.status_of(EASY, TH, None, "2026-10-01")["status"] == "not_pushed"
    seen = {}

    async def push(db, sessions, th, today, *, stale_keys=(), missed_keys=()):
        seen["push"] = (sessions, th, today, tuple(stale_keys), tuple(missed_keys))
        return {"sessions": [], "removed": []}

    async def remove(db, keys):
        seen["remove"] = list(keys)
        return []
    monkeypatch.setattr(CW, "push_sessions", push)          # patched at the module: the provider follows
    monkeypatch.setattr(CW, "remove_keys", remove)
    run(p.push_sessions("db", [EASY], TH, "2026-10-01", stale_keys=["a"], missed_keys=["b"]))
    run(p.remove_keys("db", ["u1"]))
    assert seen["push"] == ([EASY], TH, "2026-10-01", ("a",), ("b",)) and seen["remove"] == ["u1"]
    # COROS errors are the interface's errors
    assert issubclass(CW.CorosAuthError, WT.SyncAuthError) and issubclass(CW.CorosError, WT.SyncError)
    assert CW.Unsupported is WT.Unsupported


def test_provider_setting_default_coros_and_only_enabled_ones():
    validate("plan.push.provider", "coros")
    with pytest.raises(ValueError):
        validate("plan.push.provider", "garmin")

    async def go():
        eng, s = await _db()
        try:
            assert (await WT.active(s)).id == "coros"
            await SettingsRepository(s).set("plan.push.provider", "coros")
            await s.commit()
            return (await WT.active(s)).id
        finally:
            await s.close()
            await eng.dispose()
    assert run(go()) == "coros"


def test_push_rows_carry_the_provider_and_old_rows_count_as_coros():
    async def go():
        eng, s = await _db()
        try:
            s.add(CorosPlanPush(athlete_id=1, session_key="new", week_start="2026-10-05", session_id="x"))
            s.add(CorosPlanPush(athlete_id=1, session_key="other", week_start="2026-10-05", session_id="y",
                                provider="garmin"))
            await s.commit()
            # a row from before the column (NULL) is COROS's
            await s.execute(text("INSERT INTO coros_plan_push (athlete_id, session_key, week_start, session_id, "
                                 "status, provider) VALUES (1, 'old', '2026-10-05', 'z', 'pushed', NULL)"))
            await s.commit()
            prov = [r.provider for r in (await s.execute(select(CorosPlanPush).order_by(CorosPlanPush.id))).scalars()]
            rows = await WT.get("coros").all_rows(s)
            by = await WT.get("coros").rows_by_key(s, ["new", "old", "other"])
            return prov, sorted(rows), sorted(by)
        finally:
            await s.close()
            await eng.dispose()
    prov, rows, by = run(go())
    assert prov == ["coros", "garmin", None]
    assert rows == by == ["new", "old"]                      # never another provider's record


def test_additive_migration_fills_old_rows_with_coros():
    # the ALTER the migration runs (db/database.py new_cols), on a table from before the column
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "db" / "database.py").read_text("utf-8")
    assert '("coros_plan_push", "provider", "TEXT DEFAULT \'coros\'")' in src

    async def go():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with eng.begin() as c:
                await c.execute(text("CREATE TABLE coros_plan_push (id INTEGER PRIMARY KEY, session_key TEXT)"))
                await c.execute(text("INSERT INTO coros_plan_push (session_key) VALUES ('a')"))
                await c.execute(text("ALTER TABLE coros_plan_push ADD COLUMN provider TEXT DEFAULT 'coros'"))
                return (await c.execute(text("SELECT provider FROM coros_plan_push"))).scalar()
        finally:
            await eng.dispose()
    assert run(go()) == "coros"
