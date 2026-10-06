import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def _run(coro):
    return asyncio.run(coro)


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


def test_an_existing_db_upgrades_for_the_2026_10_06_branches(tmp_path, monkeypatch):
    """Integration 2026-10-06: a DB from before the merge (no plan_week_snapshots table, the
    settings stored then) goes through init_db() as on start-up; nothing is lost and
    SP-71 (the weekly record table), SP-67 (COROS threshold history), SP-211 (age / 「稍後再說」)
    and SP-191 (strength-move prefs) work on it."""
    import copy
    import json
    from sqlalchemy import select
    from backend.db.models import Athlete, Base, PlanWeekSnapshot, UserSetting
    import backend.db.database as db_mod
    from backend.engine import athlete_profile as AP
    from backend.engine import coros_compare as CC
    from backend.engine import hr_profile as HP
    from backend.engine import plan_prefs as PP
    from backend.settings.repository import SettingsRepository
    from backend.sync import coros_client
    from backend.tests.test_hr_profile import ACCOUNT_DATA

    path = tmp_path / "old.db"
    old_acc = {**HP.parse_account(ACCOUNT_DATA), "at": "2026-09-01T01:00:00+00:00"}
    old_acc = json.loads(json.dumps(old_acc))

    async def make_old():
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        tables = [t for n, t in Base.metadata.tables.items() if n != "plan_week_snapshots"]
        async with eng.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
            await conn.execute(text("INSERT INTO athletes (id, name, data_dir, created_at) VALUES (1, 'me', 'x', '2026-01-01')"))
            for k, v in (("athlete.setup.done", True),                  # dismissed the old wizard
                         ("athlete.coros_profile", old_acc),             # read by a sync before SP-67
                         ("plan.prefs.quality_per_week", 1)):
                await conn.execute(text("INSERT INTO user_settings (user_id, key, value_json, updated_at) "
                                        "VALUES (1, :k, :v, '2026-09-01')"), {"k": k, "v": json.dumps(v)})
        await eng.dispose()

    _run(make_old())
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        await db_mod.init_db()
        await db_mod.init_db()                                           # idempotent, as every start-up
        async with db_mod.get_engine().begin() as conn:
            names = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")))}
        async with db_mod.AsyncSessionLocal() as db:
            repo = SettingsRepository(db, 1)
            kept = {r.key for r in (await db.execute(select(UserSetting))).scalars().all()}
            athletes = (await db.execute(select(Athlete))).scalars().all()
            # SP-71: the new table is there and takes a week
            db.add(PlanWeekSnapshot(athlete_id=1, week_start="2026-09-28", plan_json="{}"))
            await db.commit()
            snaps = (await db.execute(select(PlanWeekSnapshot))).scalars().all()
            # SP-67: no history yet; the next sync with a changed account seeds it with the stored one
            before = await repo.get(CC.HISTORY_KEY)
            changed = copy.deepcopy(ACCOUNT_DATA)
            changed["zoneData"].update(lthr=152, maxHr=185)
            await coros_client.store_hr_profile(db, 1, changed)
            await db.commit()
            hist = await repo.get(CC.HISTORY_KEY)
            # SP-211 / SP-191: the new keys read as their defaults next to the old ones
            done, later = await repo.get(AP.SETUP_DONE_KEY), await repo.get(AP.SETUP_LATER_KEY)
            prefs = {k: await repo.get(k) for k in ("plan.prefs.quality_per_week", "plan.prefs.strength_moves",
                                                     "plan.prefs.strength_no_gear")}
        await db_mod.dispose(path)
        return names, kept, athletes, snaps, before, hist, done, later, prefs

    names, kept, athletes, snaps, before, hist, done, later, prefs = _run(go())
    assert "plan_week_snapshots" in names
    assert {"athlete.setup.done", "athlete.coros_profile", "plan.prefs.quality_per_week"} <= kept and len(athletes) == 1
    assert [s.week_start for s in snaps] == ["2026-09-28"]
    assert before is None
    assert [r["at"][:10] for r in hist][0] == "2026-09-01" and hist[-1]["lthr"] == 152 and len(hist) == 2
    # the old 「稍後再說」 (setup.done) no longer silences the wizard while the age is missing
    assert done is True and later is None
    assert AP.setup_missing(70.0, "male", None) == ["age"] and AP.setup_remind(True, later)
    p = PP.from_settings(prefs)
    assert p.quality == 1 and p.strength_moves == () and p.strength_no_gear == ()
