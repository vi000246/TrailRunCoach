"""SP-320 ④: in-memory cache keys carry the tenant and a per-object dataset generation
(never id(ds) alone, never the day alone); the per-athlete calibration runs at most once a
week after a sync, while 「重新校正」 always runs. Synthetic data only."""
from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import tenancy
from backend.engine import calibrate as CAL
from backend.settings.repository import SettingsRepository


class _DS:
    pass


def _tenant(tid: str) -> tenancy.Tenant:
    return tenancy.Tenant(id=tid, kind=tenancy.USER, root=Path("/nonexistent") / tid,
                          shared=Path("/nonexistent") / tid, caps=frozenset())


# ---- generation / ds_key ------------------------------------------------------------

def test_generation_is_stable_per_object_and_never_reused():
    a = _DS()
    ga = tenancy.generation(a)
    assert tenancy.generation(a) == ga
    seen = {ga}
    for _ in range(50):              # freed objects' ids get reused; generations never do
        g = tenancy.generation(_DS())
        assert g not in seen
        seen.add(g)


def test_two_tenants_never_share_a_key():
    ds = _DS()
    with tenancy.use(_tenant("u1")):
        k1 = tenancy.ds_key(ds)
    with tenancy.use(_tenant("u2")):
        k2 = tenancy.ds_key(ds)
    assert k1 != k2 and k1[0] == "u1" and k2[0] == "u2"


def test_hrc_test_cache_is_per_tenant(monkeypatch):
    """The HRC test used to be cached on the day alone: a second tenant read the first's."""
    from backend.api import racepower as RP
    from backend.engine import heat_data as HD
    from backend.engine.racepower import heatacc as HA
    RP._cache.pop("hrc_test", None)
    calls = []
    dss = {"u1": _DS(), "u2": _DS()}
    monkeypatch.setattr(RP, "_dataset", lambda: dss[tenancy.current().id])
    monkeypatch.setattr(HD, "exposures", lambda: ([], {}))
    monkeypatch.setattr(HA, "hrc_slope_test", lambda rows: (calls.append(tenancy.current().id)
                                                             or {"who": tenancy.current().id}))
    with tenancy.use(_tenant("u1")):
        assert RP._hrc_test() == {"who": "u1"}
        assert RP._hrc_test() == {"who": "u1"}          # cached
    with tenancy.use(_tenant("u2")):
        assert RP._hrc_test() == {"who": "u2"}
    assert calls == ["u1", "u2"]
    RP._cache.pop("hrc_test", None)


def test_plan_sessions_key_has_the_tenant():
    import inspect
    from backend.api import plan_sessions as PS
    src = inspect.getsource(PS)
    assert "id(ds)" not in src and "_tenancy.ds_key(ds)" in src


def test_no_api_cache_keys_on_bare_id_ds():
    import inspect
    from backend.api import injuries, overview, plan_sessions, racepower
    for m in (injuries, overview, plan_sessions, racepower):
        assert "id(ds)" not in inspect.getsource(m), m.__name__


# ---- weekly calibration ---------------------------------------------------------------

def test_due():
    now = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc)
    assert CAL.due(None, now) and CAL.due("", now) and CAL.due("garbage", now)
    assert not CAL.due((now - dt.timedelta(days=6, hours=23)).isoformat(), now)
    assert CAL.due((now - dt.timedelta(days=7)).isoformat(), now)
    assert not CAL.due("2026-10-05T00:00:00", now)          # naive = UTC


async def _factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def test_sync_run_skipped_within_a_week_manual_forces(monkeypatch):
    runs = []

    async def fake_calibrate(db, athlete_id=1, ds=None, today=None):
        runs.append(athlete_id)
        await SettingsRepository(db, athlete_id).set(CAL.LAST_RUN_KEY, dt.datetime.now(dt.timezone.utc).isoformat())
        await db.commit()
        return {"written": []}
    monkeypatch.setattr(CAL, "calibrate", fake_calibrate)

    async def go():
        f = await _factory()
        monkeypatch.setattr(CAL, "SESSION_FACTORY", f)
        r1 = await CAL.run_safe(1)                       # never ran: runs
        r2 = await CAL.run_safe(1)                       # within 7 days: skipped
        r3 = await CAL.run_safe(1, force=True)           # manual: runs
        async with f() as db:                            # 8 days ago: due again
            old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).isoformat()
            await SettingsRepository(db, 1).set(CAL.LAST_RUN_KEY, old)
            await db.commit()
        r4 = await CAL.run_safe(1)
        return r1, r2, r3, r4
    r1, r2, r3, r4 = asyncio.run(go())
    assert r1 == {"written": []} and r2["status"] == "skipped" and r3 == {"written": []}
    assert r4 == {"written": []} and runs == [1, 1, 1]


def test_calibrate_stores_the_last_run(monkeypatch):
    monkeypatch.setattr(CAL, "run", lambda ds, stored, today: ({}, {}))

    async def go():
        f = await _factory()
        async with f() as db:
            await CAL.calibrate(db, 1, ds=object(), today=dt.date(2026, 10, 7))
            return await SettingsRepository(db, 1).get(CAL.LAST_RUN_KEY)
    last = asyncio.run(go())
    assert last and not CAL.due(last)
