"""SP-48 (React SPA 去留) leftovers, owner decision 2026-10-07.

* The two routes nobody calls any more are gone: PATCH /api/v1/workouts/{id}/activity (the
  static pages edit an activity through PATCH /api/v1/wko5/workouts/{i}/activity) and the
  POST /api/v1/expr/evaluate stub.
* An older DB's AI-coach columns (athlete_settings.ai_provider / ai_api_key / ai_model; the model
  lost them with the AI coach on 2026-10-04) no longer keep the stored API key: init_db clears
  them in every tenant DB (owner at start-up, a user tenant on its first use, a restored backup).

tmp DBs only. The key is a fake sentinel; only its absence is checked, it is never printed."""
from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine


# ------------------------------------------------------------------------- the routes

def _routes() -> set:
    from backend.tests.test_new_user_smoke import _all_routes
    return {(m, getattr(r, "path", "")) for r in _all_routes() for m in (getattr(r, "methods", None) or ())}


def test_the_spa_only_routes_are_gone_and_the_pages_routes_stay():
    routes = _routes()
    assert ("PATCH", "/api/v1/workouts/{workout_id}/activity") not in routes
    assert not [p for _m, p in routes if p.startswith("/api/v1/expr")]
    assert importlib.util.find_spec("backend.api.expr") is None
    # what the static pages call (activity.html, wko5_viewer.html) is still mounted
    assert ("PATCH", "/api/v1/workouts/{workout_id}/classification") in routes
    assert ("PATCH", "/api/v1/wko5/workouts/{i}/activity") in routes
    assert ("PATCH", "/api/v1/wko5/activities") in routes


def test_no_rule_still_names_the_removed_expr_route():
    """The demo's write allow-list / no-sandbox / heavy rules, the static demo export's compute
    POSTs and perf.log's areas listed the stub; the other entries keep matching."""
    from backend import applog
    from backend import tenancy_mw as MW
    from backend.demo import export_static as X
    gone = "/api/v1/expr/evaluate"
    assert not MW.allowed_write("POST", gone)
    assert not MW.NO_SANDBOX.match(gone) and not MW.HEAVY.match(gone) and not X.COMPUTE_POSTS.match(gone)
    assert applog.perf_area(gone) == "other"
    for p in ("/api/v1/racepower/predict", "/api/v1/racepower/plan"):
        assert MW.allowed_write("POST", p) and MW.NO_SANDBOX.match(p) and MW.HEAVY.match(p)
        assert X.COMPUTE_POSTS.match(p)
    assert MW.NO_SANDBOX.match("/api/v1/demo/reset") and MW.HEAVY.match("/api/v1/racepower/course/event/e1")
    assert applog.perf_area("/api/v1/workouts/{workout_id}/classification") == "chart"


# ------------------------------------------------------------------- the AI-coach columns

FAKE_KEY = "sk-test-SP48-fake-key-0123456789abcdef"
AI_COLS = ("ai_provider", "ai_api_key", "ai_model")
SETTINGS_COLS = ("id INTEGER NOT NULL, athlete_id INTEGER NOT NULL, effective_date DATE NOT NULL, "
                 "ftp_w FLOAT, weight_kg FLOAT, lthr INTEGER, threshold_pace_s_per_km FLOAT, run_ftp_w FLOAT, "
                 "initial_ctl_run FLOAT, initial_atl_run FLOAT")
SETTINGS_KEYS = ("PRIMARY KEY (id), UNIQUE (athlete_id, effective_date), "
                 "FOREIGN KEY(athlete_id) REFERENCES athletes (id)")


def _old_db(path: Path, how: str = "created") -> None:
    """An app DB from while the AI coach existed (2026-06 … 2026-10-04): today's tables, but
    athlete_settings with the ai_* columns and a stored key on two dated rows. "created": the
    table made by create_all with the model's columns; "added": an older table the AI coach's
    _migrate_schema entries extended (ALTER TABLE … ADD COLUMN … TEXT)."""
    from backend.db.models import Base
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng, tables=[t for n, t in Base.metadata.tables.items() if n != "athlete_settings"])
    eng.dispose()
    con = sqlite3.connect(path)
    try:
        if how == "created":
            con.execute(f"CREATE TABLE athlete_settings ({SETTINGS_COLS}, ai_provider VARCHAR, ai_api_key VARCHAR, "
                        f"ai_model VARCHAR, {SETTINGS_KEYS})")
        else:
            con.execute(f"CREATE TABLE athlete_settings ({SETTINGS_COLS}, {SETTINGS_KEYS})")
            for c in AI_COLS:
                con.execute(f"ALTER TABLE athlete_settings ADD COLUMN {c} TEXT")
        con.execute("INSERT INTO athletes (id, name, data_dir, created_at) VALUES (1, 'me', '', '2026-06-01')")
        con.executemany("INSERT INTO athlete_settings (athlete_id, effective_date, weight_kg, run_ftp_w, "
                        "ai_provider, ai_api_key, ai_model) VALUES (1, ?, ?, ?, 'claude', ?, 'claude-x')",
                        [("2026-06-13", 62.0, 280.0, FAKE_KEY), ("2026-08-01", 61.5, 285.0, FAKE_KEY)])
        con.commit()
    finally:
        con.close()


def _settings(path: Path) -> tuple[list, list]:
    con = sqlite3.connect(path)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(athlete_settings)")]
        rows = [dict(zip(cols, r)) for r in con.execute("SELECT * FROM athlete_settings ORDER BY effective_date")]
    finally:
        con.close()
    return cols, rows


def _key_on_disk(path: Path) -> bool:
    """The fake key anywhere in the DB file or its WAL: a cleared value's bytes stay on the page
    (a freed cell) unless overwritten. A bool, so a failure never prints the file."""
    key = FAKE_KEY.encode()
    return any(p.exists() and key in p.read_bytes() for p in (path, path.with_name(path.name + "-wal")))


def _assert_cleared(path: Path, dropped=AI_COLS) -> None:
    cols, rows = _settings(path)
    assert not [c for r in rows for c in AI_COLS if r.get(c) is not None]
    if sqlite3.sqlite_version_info >= (3, 35, 0):          # ALTER TABLE … DROP COLUMN
        assert not set(dropped) & set(cols) and set(AI_COLS) - set(dropped) <= set(cols)
    leaked = _key_on_disk(path)
    assert not leaked, "the fake API key is still in the DB file"


def _init_db(monkeypatch, path: Path) -> None:
    import backend.db.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        try:
            await db_mod.init_db()
            async with db_mod.get_engine().connect() as c:      # the pooled connection is left as it was
                return (await c.execute(text("PRAGMA secure_delete"))).scalar()
        finally:
            await db_mod.dispose(path)
    assert asyncio.run(go()) == 0


@pytest.mark.parametrize("how", ["created", "added"])
def test_init_db_clears_the_ai_columns_of_an_old_db_and_is_idempotent(tmp_path, monkeypatch, how):
    path = tmp_path / "old.db"
    _old_db(path, how)
    assert _key_on_disk(path)                                 # the fixture does hold it
    _init_db(monkeypatch, path)
    _assert_cleared(path)
    _cols, rows = _settings(path)
    # nothing else of the rows changes
    assert [(r["effective_date"], r["weight_kg"], r["run_ftp_w"]) for r in rows] == [
        ("2026-06-13", 62.0, 280.0), ("2026-08-01", 61.5, 285.0)]
    _init_db(monkeypatch, path)                               # every start-up runs it again
    _assert_cleared(path)
    assert _settings(path)[1] == rows


def test_an_sqlite_without_drop_column_still_clears_the_values(tmp_path, monkeypatch):
    """Before SQLite 3.35 the columns stay, emptied; later runs find nothing to clear."""
    import backend.db.database as db_mod
    monkeypatch.setattr(db_mod, "DROP_COLUMN_SQLITE", (99, 0, 0))
    path = tmp_path / "old.db"
    _old_db(path)
    _init_db(monkeypatch, path)
    _assert_cleared(path, dropped=())
    _init_db(monkeypatch, path)
    _assert_cleared(path, dropped=())
    assert [r["weight_kg"] for r in _settings(path)[1]] == [62.0, 61.5]


def test_a_column_that_cannot_be_dropped_is_still_emptied(tmp_path, monkeypatch):
    """An index on a retired column makes DROP COLUMN fail: start-up goes on, the column stays
    with NULLs (the index's copy of the key is zeroed too), the others are dropped."""
    path = tmp_path / "old.db"
    _old_db(path)
    con = sqlite3.connect(path)
    con.execute("CREATE INDEX ix_ai_key ON athlete_settings (ai_api_key)")
    con.commit()
    con.close()
    _init_db(monkeypatch, path)
    _assert_cleared(path, dropped=("ai_provider", "ai_model"))
    _init_db(monkeypatch, path)
    _assert_cleared(path, dropped=("ai_provider", "ai_model"))


def test_init_db_on_a_new_db_and_a_partial_schema(tmp_path, monkeypatch):
    """A DB made after 2026-10-04 never had the columns; a test-style schema may lack the table."""
    import backend.db.database as db_mod
    path = tmp_path / "new.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def new():
        try:
            await db_mod.init_db()
            await db_mod.init_db()
        finally:
            await db_mod.dispose(path)
    asyncio.run(new())
    cols, rows = _settings(path)
    assert "weight_kg" in cols and not set(AI_COLS) & set(cols) and rows == []

    async def partial():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as conn:
            await conn.execute(text("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT)"))
        monkeypatch.setattr(db_mod, "engine", eng)
        try:
            await db_mod._migrate_schema()
            await db_mod._migrate_schema()
        finally:
            await eng.dispose()
    asyncio.run(partial())


def test_every_tenant_db_is_cleared(tmp_path, monkeypatch):
    """SP-384: the owner's DB at start-up (main.lifespan → init_db) and each user tenant's DB on
    its first use (tenants.ensure_ready → init_db): one with an older restored DB, one new."""
    from backend import accounts, tenancy
    from backend.accounts import store, tenants as TN
    from backend.db import database
    monkeypatch.setenv(tenancy.ENV_HOME, str(tmp_path / "owner"))
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    monkeypatch.setattr(database, "DB_PATH", None)
    monkeypatch.setattr(database, "engine", None)
    owner = tenancy.owner()
    a, b = TN.tenant_of(store.create_user(email="a@x.tw")), TN.tenant_of(store.create_user(email="b@x.tw"))
    for t in (owner, a):
        t.root.mkdir(parents=True, exist_ok=True)
        _old_db(t.root / "wko5coach.db")

    async def go():
        try:
            with tenancy.use(owner):
                await database.init_db()
            for t in (a, b):
                await TN.ensure_ready(t)
        finally:
            for t in (owner, a, b):
                await database.dispose(t.root / "wko5coach.db")
    asyncio.run(go())
    for t in (owner, a, b):
        _assert_cleared(t.root / "wko5coach.db")
    assert [r["weight_kg"] for r in _settings(a.root / "wko5coach.db")[1]] == [62.0, 61.5]
