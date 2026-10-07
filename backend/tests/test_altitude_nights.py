"""
睡在高處的紀錄 (SP-259; engine/altitude.py NIGHTS_KEY, api/altitude_nights.py): a night marked on
the 課表 calendar (「這一晚睡在 X m」) counts for the 高度適應提醒 like a night the activities show,
never twice; no record = the old result; the setting is validated, an old DB reads it as empty,
the dialog is filled from a plan event's GPX night. Synthetic only.
"""
import asyncio
import datetime as dt
import json

import pytest

from backend.engine import altitude as AL
from backend.engine import suggestions as SG
from backend.engine.planning import Event

TODAY = dt.date(2026, 10, 5)
D = lambda n: TODAY - dt.timedelta(days=n)                      # noqa: E731
START = TODAY + dt.timedelta(days=6)


def test_a_drive_up_night_counts_once_recorded():
    """松雪樓: drive up on day −3 (no activity), sleep at 3,150 m, climb 合歡北峰 (3,422 m) on −2."""
    alts = {D(2): 3422.0}
    assert AL.exposure(alts, TODAY, START)["nights"] == 0                  # the activities can't see it
    ex = AL.exposure(alts, TODAY, START, {D(3): 3150.0})
    assert ex["nights"] == 1 and ex["manual"] == 1


def test_the_same_night_is_not_counted_twice():
    alts = {D(3): 3100.0, D(2): 3000.0}                                    # a 2-day hike: the night −3 → −2
    assert AL.exposure(alts, TODAY, START)["nights"] == 1
    ex = AL.exposure(alts, TODAY, START, {D(3): 3150.0})                   # and recorded too
    assert ex["nights"] == 1 and ex["manual"] == 0
    ex = AL.exposure(alts, TODAY, START, {D(3): 3150.0, D(2): 3150.0})     # + the next night (only recorded)
    assert ex["nights"] == 2 and ex["manual"] == 1


def test_no_record_gives_the_old_result():
    alts = {D(5): 3100.0, D(4): 3000.0, D(3): 2800.0, D(40): 3200.0, D(200): 3500.0, D(2): 1200.0}
    old = {"nights": 2, "pre_days": 3, "recent": True, "ever": True, "manual": 0}
    assert AL.exposure(alts, TODAY, START) == old
    assert AL.exposure(alts, TODAY, START, None) == old == AL.exposure(alts, TODAY, START, {})
    # and the box row is the same with an empty record
    evs = [Event("yu", "玉山", START.isoformat(), kind="baiyue", days=2)]
    a = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: {"max_m": 3952.0}, lambda: alts)
    b = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: {"max_m": 3952.0}, lambda: alts, lambda: {})
    assert a == b and a[0]["flags"] == ["acc", "recent"]


def test_what_counts():
    # under 2,750 m, tonight, outside the 14 days: not a night
    ex = AL.exposure({}, TODAY, START, {D(1): 2600.0, TODAY: 3150.0, START - dt.timedelta(days=15): 3150.0})
    assert ex["nights"] == 0
    # a night at ≥ 3,000 m: its evening and the next morning are days above 3,000 m (Schneider / Shen)
    ex = AL.exposure({}, TODAY, START, {D(3): 3150.0})
    assert ex["pre_days"] == 2 and ex["recent"] and ex["ever"]
    ex = AL.exposure({}, TODAY, START, {D(3): 2900.0})
    assert ex["nights"] == 1 and ex["pre_days"] == 0 and not ex["ever"]


def test_box_row_reads_the_records_and_says_so():
    evs = [Event("yu", "玉山", START.isoformat(), kind="baiyue", days=2)]
    got = []

    def nights():
        got.append(1)
        return {D(3): 3150.0, D(2): 3150.0}
    rows = SG.altitude_rows(evs, TODAY.isoformat(), lambda e: {"max_m": 3952.0}, lambda: {}, nights)
    r = rows[0]
    assert "acc" in r["flags"] and "近 14 天有 2 晚" in r["reason"] and len(got) == 1
    assert "其中 2 晚來自你在課表日曆記的" in r["help"]
    # an event 15–28 days away doesn't read them
    got.clear()
    far = [Event("yu", "玉山", (TODAY + dt.timedelta(days=20)).isoformat(), kind="baiyue", days=2)]
    SG.altitude_rows(far, TODAY.isoformat(), lambda e: {"max_m": 3952.0}, lambda: {}, nights)
    assert not got


def test_validate_and_edit_the_list():
    ok = [{"day": "2026-10-01", "m": 3150}, {"day": "2026-10-02", "m": 3402}]
    AL.validate_nights(ok)
    for bad in ({"day": "2026-10-01", "m": 3150}, [{"day": "2026-10-01"}], [{"day": "10/1", "m": 3150}],
                [{"day": "2026-10-01", "m": 0}], [{"day": "2026-10-01", "m": 9000}], [{"day": "2026-10-01", "m": 3150.5}],
                [{"day": "2026-10-01", "m": True}], ok + [{"day": "2026-10-01", "m": 2000}],
                [{"day": "2026-10-01", "m": 3150, "note": "頭痛"}]):          # no symptoms (owner 2026-10-06)
        with pytest.raises(ValueError):
            AL.validate_nights(bad)
    assert AL.set_night(ok, "2026-09-30", 2600)[0] == {"day": "2026-09-30", "m": 2600}
    assert AL.set_night(ok, "2026-10-01", 2900)[0] == {"day": "2026-10-01", "m": 2900}         # replaced, one a day
    assert AL.set_night(ok, "2026-10-01", None) == ok[1:]
    assert AL.clean_m("3150.4") == 3150
    for bad in ("", None, "abc", 0, 9999, float("nan")):
        with pytest.raises(ValueError):
            AL.clean_m(bad)
    assert AL.nights_map(ok) == {dt.date(2026, 10, 1): 3150.0, dt.date(2026, 10, 2): 3402.0}
    assert AL.nights_map([{"bad": 1}]) == {} and AL.nights_map(None) == {}


def test_prefill_from_an_event_night():
    ev = Event("yu", "玉山", "2026-09-26", kind="baiyue", days=3)
    alt = lambda e: {"max_m": 3952.0, "nights": [3402, 2600]}             # noqa: E731
    assert AL.event_night([ev], dt.date(2026, 9, 26), alt) == {"m": 3402, "event_id": "yu", "name": "玉山", "night": 1}
    assert AL.event_night([ev], dt.date(2026, 9, 27), alt)["m"] == 2600
    assert AL.event_night([ev], dt.date(2026, 9, 28), alt) is None        # the last day: no night
    assert AL.event_night([ev], dt.date(2026, 9, 25), alt) is None
    assert AL.event_night([ev], dt.date(2026, 9, 26), lambda e: {"max_m": 3952.0}) is None   # nights unknown
    assert AL.event_night([Event("d", "合歡北峰", "2026-09-26", kind="baiyue")], dt.date(2026, 9, 26), alt) is None


# ---- API -----------------------------------------------------------------------------------------

def test_api_put_get_delete(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import altitude_nights as API
    from backend.db.database import get_db
    from backend.engine import localtime
    from backend.engine import planning as PL
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import make_db, run
    db = run(make_db(logged_in=False))

    async def fake_db():
        yield db
    monkeypatch.setattr(localtime, "today_local", lambda *a, **k: TODAY)
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, path=None: PL.Plan(events=[
        Event("yu", "玉山", D(5).isoformat(), kind="baiyue", days=2)])))
    monkeypatch.setattr(AL, "event_altitude", lambda eid, days=1, **k: {"max_m": 3952.0, "nights": [3402]})
    app = FastAPI()
    app.include_router(API.router)
    app.dependency_overrides[get_db] = fake_db
    c = TestClient(app)
    P = "/api/v1/overview/plan/high-nights"
    assert c.get(P).json() == {"nights": []}
    assert c.put(f"{P}/{D(3).isoformat()}", json={"m": 3150}).json() == {"nights": [{"day": D(3).isoformat(), "m": 3150}]}
    assert c.put(f"{P}/{TODAY.isoformat()}", json={"m": "2610"}).status_code == 200        # tonight is fine
    assert c.put(f"{P}/{(TODAY + dt.timedelta(days=1)).isoformat()}", json={"m": 3150}).status_code == 400
    for bad in ({"m": None}, {"m": -5}, {"m": 9000}, {}):
        assert c.put(f"{P}/{D(4).isoformat()}", json=bad).status_code == 400
    assert c.put(f"{P}/2026-13-01", json={"m": 3000}).status_code == 400
    assert run(SettingsRepository(db).get(AL.NIGHTS_KEY)) == [{"day": D(3).isoformat(), "m": 3150},
                                                               {"day": TODAY.isoformat(), "m": 2610}]
    assert c.delete(f"{P}/{D(3).isoformat()}").json() == {"nights": [{"day": TODAY.isoformat(), "m": 2610}]}
    assert c.delete(f"{P}/{D(3).isoformat()}").status_code == 404
    # the dialog's prefill: the 玉山 trip's first night (its camp)
    r = c.get(f"{P}/{D(5).isoformat()}/prefill").json()
    assert r["m"] == 3402 and "玉山" in r["source"] and "第 1 晚" in r["source"]
    assert c.get(f"{P}/{D(4).isoformat()}/prefill").json() == {"m": None, "source": None}


def test_the_demo_may_record_nights_in_its_sandbox():
    from backend import tenancy_mw as MW
    ok = lambda m, p: any(m in ms and rx.match(p) for ms, rx in MW.WRITE_ALLOW)   # noqa: E731
    assert ok("PUT", "/api/v1/overview/plan/high-nights/2026-10-01")
    assert ok("DELETE", "/api/v1/overview/plan/high-nights/2026-10-01")


def test_schedule_page_wires_the_menu_and_the_mark():
    from pathlib import Path
    s = (Path(__file__).resolve().parents[1] / "static" / "schedule.html").read_text("utf-8")
    for el in ('act: "high"', 'act: "unhigh"', "openHigh(", "delHigh(", "high-nights/${day}/prefill", "D.high_nights",
               'data-hn="', ".hn-tag"):
        assert el in s, el
    for loc in ("zh-TW", "en"):
        cat = json.loads((Path(__file__).resolve().parents[1] / "static" / "i18n" / loc / "schedule.json").read_text("utf-8"))
        assert all(cat.get(k) for k in ("ctx.high", "ctx.high_del", "hn.title", "hn.body", "hn.saved", "hn.tag")), loc


# ---- old DB --------------------------------------------------------------------------------------

def _run(c):
    return asyncio.run(c)


def test_an_old_db_reads_no_nights_and_takes_one(tmp_path, monkeypatch):
    """A DB from before SP-259 (no altitude.nights row) goes through init_db() twice as on start-up:
    nothing is lost, the key reads [] (= the old reminder), and a night can be stored."""
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import create_async_engine

    import backend.db.database as db_mod
    from backend.db.models import Base, UserSetting
    from backend.settings.repository import SettingsRepository
    path = tmp_path / "old.db"

    async def make_old():
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("INSERT INTO user_settings (user_id, key, value_json, updated_at) "
                                    "VALUES (1, 'plan.blackouts', '[]', '2026-09-01')"))
        await eng.dispose()
    _run(make_old())
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        await db_mod.init_db()
        await db_mod.init_db()
        async with db_mod.AsyncSessionLocal() as db:
            repo = SettingsRepository(db, 1)
            before = await repo.get(AL.NIGHTS_KEY)
            await repo.set(AL.NIGHTS_KEY, [{"day": "2026-10-01", "m": 3150}])
            with pytest.raises(ValueError):
                await repo.set(AL.NIGHTS_KEY, [{"day": "2026-10-01", "m": "high"}])
            await db.commit()
            kept = {r.key for r in (await db.execute(select(UserSetting))).scalars().all()}
            after = await repo.get(AL.NIGHTS_KEY)
        await db_mod.dispose(path)
        return before, kept, after
    before, kept, after = _run(go())
    assert before == [] and {"plan.blackouts", AL.NIGHTS_KEY} <= kept and after == [{"day": "2026-10-01", "m": 3150}]
    # the read-only loader on that file
    from backend.engine.wko5expr import datasource
    monkeypatch.setattr(datasource, "_db_path", lambda: path)
    assert AL.load_nights() == {dt.date(2026, 10, 1): 3150.0}
