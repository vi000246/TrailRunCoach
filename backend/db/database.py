"""The app DB: one SQLite file per tenant (backend/tenancy.py, auth-and-demo
plan §2.2) — the owner's ~/.wko5coach/wko5coach.db as before, a demo
sandbox's own copy, later each user's. Engines are pooled per file (LRU).

    db_path()             the current tenant's DB file
    AsyncSessionLocal()   a session on it (call it like the old sessionmaker)
    get_engine()          its engine
"""
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from backend.db.models import Base
from backend import tenancy

# a fixed DB file (scripts / tests); None = the current tenant's (tenancy.db_path)
DB_PATH: Optional[Path] = None
# a fixed engine (tests); None = the pooled engine of db_path()
engine = None

_POOL_MAX = 32
_POOL: "OrderedDict[str, tuple]" = OrderedDict()      # path -> (engine, sessionmaker)
_POOL_LOCK = threading.Lock()


def db_path() -> Path:
    return Path(DB_PATH) if DB_PATH is not None else tenancy.db_path()


def url_for(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _on_connect(dbapi_con, _record) -> None:
    """WAL lets readers run beside the long sync writers (Dataset warm-up,
    plan_auto), and busy_timeout waits instead of failing after 5 s: the
    scheduler / backup ticks died with "database is locked" (OperationalError)."""
    cur = dbapi_con.cursor()
    try:
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA journal_mode=WAL")
    finally:
        cur.close()


def _entry(path: Optional[Path] = None) -> tuple:
    p = Path(path) if path is not None else db_path()
    key = str(p)
    with _POOL_LOCK:
        hit = _POOL.get(key)
        if hit is not None:
            _POOL.move_to_end(key)
            return hit
        p.parent.mkdir(parents=True, exist_ok=True)
        eng = create_async_engine(url_for(p), echo=False)
        event.listen(eng.sync_engine, "connect", _on_connect)
        hit = _POOL[key] = (eng, async_sessionmaker(eng, expire_on_commit=False))
        while len(_POOL) > _POOL_MAX:
            _k, (old, _m) = _POOL.popitem(last=False)
            try:                                   # sync dispose: drops the pooled connections
                old.sync_engine.dispose()
            except Exception:                      # noqa: BLE001
                pass
    return hit


def get_engine(path: Optional[Path] = None):
    if path is None and engine is not None:
        return engine
    return _entry(path)[0]


def AsyncSessionLocal(path: Optional[Path] = None) -> AsyncSession:     # noqa: N802 — the old sessionmaker's name
    """A session on the current tenant's DB (or `path`)."""
    return _entry(path)[1]()


async def dispose(path: Optional[Path] = None) -> None:
    """Close the pooled connections of a DB file (backup restore, a sandbox
    deleted) and forget its engine."""
    key = str(Path(path) if path is not None else db_path())
    with _POOL_LOCK:
        hit = _POOL.pop(key, None)
    if hit is not None:
        await hit[0].dispose()


def forget(path: Path) -> None:
    """Synchronous dispose (a demo sandbox deleted by the janitor thread)."""
    with _POOL_LOCK:
        hit = _POOL.pop(str(Path(path)), None)
    if hit is not None:
        try:
            hit[0].sync_engine.dispose()
        except Exception:                          # noqa: BLE001
            pass


def __getattr__(name):
    """Back-compat for readers of the old module constants."""
    if name == "DATABASE_URL":
        return url_for(db_path())
    raise AttributeError(name)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _migrate_schema():
    """Add columns introduced after the initial schema without dropping data.
    New TABLES (e.g. activity_tags, 2026-10-01) need no entry here: init_db's
    create_all creates any missing table and leaves existing ones alone. Retired
    tables (data_registry.RETIRED_TABLES) are dropped; legacy ones
    (data_registry.LEGACY_TABLES, e.g. workout_metrics) are left as they are.
    The stable ids of the user-edited tables (uid, updated_at; SP-310): db/stable_ids.migrate."""
    new_cols = [
        ("workout_files", "coros_activity_id", "TEXT"),
        ("workout_files", "coros_sport_type", "INTEGER"),
        ("workout_files", "elevation_gain_m", "REAL"),
        ("workout_files", "trail_classification", "TEXT"),
        ("workout_files", "classification_overridden", "INTEGER"),
        ("workout_files", "start_time_utc", "DATETIME"),
        ("workout_files", "duplicate_of", "INTEGER"),
        ("workout_files", "rpe", "REAL"),
        ("workout_files", "feel", "INTEGER"),
        ("workout_files", "coros_training_load", "REAL"),      # COROS list trainingLoad (SP-38)
        ("workout_files", "coros_feel", "INTEGER"),            # COROS post-run self-rating 0–5 (SP-231)
        ("workout_files", "rpe_source", "TEXT"),               # "coros" when rpe came from it (SP-231)
        ("sync_state", "coros_access_token", "TEXT"),
        ("sync_state", "tp_web_cookie", "TEXT"),
        ("sync_state", "coros_token_expires", "DATETIME"),
        ("sync_state", "coros_last_sync_at", "DATETIME"),
        ("sync_state", "coros_email", "TEXT"),
        ("sync_state", "coros_base_url", "TEXT"),
        ("sync_state", "coros_user_id", "TEXT"),
        ("sync_state", "coros_password_sealed", "TEXT"),
        ("sync_state", "tp_username", "TEXT"),
        ("sync_state", "tp_password_sealed", "TEXT"),
        ("sync_state", "garmin_token_sealed", "TEXT"),             # Garmin trial (SP-91)
        ("sync_state", "garmin_token_expires", "DATETIME"),
        ("sync_state", "garmin_email", "TEXT"),
        ("sync_state", "garmin_password_sealed", "TEXT"),
        ("sync_state", "garmin_last_sync_at", "DATETIME"),
        ("workout_files", "garmin_activity_id", "TEXT"),
        ("athlete_settings", "threshold_pace_s_per_km", "REAL"),
        ("athlete_settings", "run_ftp_w", "REAL"),
        ("athlete_settings", "initial_ctl_run", "REAL"),
        ("athlete_settings", "initial_atl_run", "REAL"),
        ("plan_sessions", "terrain", "TEXT"),
        ("plan_sessions", "distance_km", "REAL"),
        ("plan_sessions", "climb_m", "REAL"),
        ("plan_sessions", "protocol", "TEXT"),
        ("plan_sessions", "variant_key", "TEXT"),
        ("plan_sessions", "rung_key", "TEXT"),
        ("plan_sessions", "equiv", "INTEGER"),
        ("plan_sessions", "swap", "TEXT"),
        ("plan_sessions", "swap_reason", "TEXT"),
        ("plan_sessions", "variant_reps", "INTEGER"),
        ("plan_sessions", "variant_blocks", "TEXT"),
        ("plan_sessions", "variant_adj", "TEXT"),
        ("plan_sessions", "target_basis", "TEXT"),
        ("plan_sessions", "steps", "TEXT"),
        ("plan_sessions", "ext_key", "TEXT"),          # 賽事計算機匯出至課表 (plan_store.upsert_external)
        ("plan_sessions", "ext_sig", "TEXT"),
        ("plan_sessions", "family", "TEXT"),           # 強度課 有氧間歇 / VO2max 間歇 / 速度 (SP-79)
        ("activity_tags", "exclusion", "TEXT"),
        ("activity_tags", "name", "TEXT"),
        ("activity_tags", "tags_json", "TEXT"),
        ("activity_tags", "pain", "INTEGER"),          # 傷病紀錄 (engine/injuries.py)
        ("activity_tags", "pain_area", "TEXT"),
        ("activity_tags", "injury_id", "INTEGER"),
        ("activity_tags", "pain_score", "INTEGER"),    # 0–10 跑的時候最痛幾分 (SP-271)
        ("injury_events", "category", "TEXT DEFAULT 'injury'"),   # 生病 in the 傷病紀錄 (SP-117)
        ("injury_events", "condition", "TEXT"),                   # 傷別 (SP-269); NULL = not chosen
        ("injury_events", "walkrun_from", "TEXT"),                # 「可以開始走跑」 (SP-272)
        ("injury_events", "illness", "TEXT"),
        ("coros_plan_push", "provider", "TEXT DEFAULT 'coros'"),   # sync/workout_targets
    ]
    from backend import data_registry
    async with get_engine().begin() as conn:
        # tables no code uses any more (data_registry.RETIRED_TABLES, e.g. pmc_cache, SP-341);
        # IF EXISTS: a DB that never had one (a newer DB, a test schema) is fine
        for table in data_registry.RETIRED_TABLES:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        for table, col, col_type in new_cols:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            # no columns = no such table (a partial schema, e.g. a test DB): create_all owns it
            if existing and col not in existing:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                )
        # the Garmin activity id is looked up once per listed activity (SP-91): indexed like the
        # model's index=True on a new DB; IF NOT EXISTS keeps it idempotent
        cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(workout_files)"))).fetchall()}
        if "garmin_activity_id" in cols:
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_workout_files_garmin_activity_id "
                                    "ON workout_files (garmin_activity_id)"))
        # SP-310: a stable uid (UUID) and updated_at on the tables the user edits, ids for the rows
        # from before, the unique index (db/stable_ids.py)
        from backend.db import stable_ids
        await stable_ids.migrate(conn)
        # settings that no longer exist (settings/repository.py RETIRED_KEYS), e.g. the
        # sealed backup-encryption key: deleted, not left behind in the DB
        from backend.settings.repository import RETIRED_KEYS
        has_settings = (await conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_settings'"))).first()
        if has_settings:
            await _migrate_race_result(conn)
        for key in RETIRED_KEYS if has_settings else ():
            await conn.execute(text("DELETE FROM user_settings WHERE key = :k"), {"k": key})
    # the AI coach's stored API key (SP-48): its own transaction, then the WAL is truncated
    await _clear_retired_columns()


# columns an older DB may still hold that no code reads any more and whose values must not stay
# behind: the AI coach's provider / API key / model on athlete_settings (the model lost them with
# the AI coach, 2026-10-04; owner 2026-10-07, SP-48)
RETIRED_COLUMNS: dict = {"athlete_settings": ("ai_provider", "ai_api_key", "ai_model")}
DROP_COLUMN_SQLITE = (3, 35, 0)          # the first SQLite with ALTER TABLE … DROP COLUMN


async def _clear_retired_columns() -> None:
    """RETIRED_COLUMNS out of the current tenant's DB (init_db: the owner's at start-up, a user
    tenant's on its first use, a restored backup). The values are set to NULL with secure_delete
    on, so the old bytes are zeroed on the page instead of left in a freed cell; then, where the
    SQLite has DROP COLUMN, the columns are dropped too (the table then matches a new DB's). When
    anything changed, the WAL is checkpointed and truncated: older page images there can still
    hold the value. Idempotent: a new DB, or a second run, only reads PRAGMA table_info. Logs
    column names and counts, never a value."""
    import logging
    import sqlite3
    from sqlalchemy.exc import OperationalError
    log = logging.getLogger(__name__)
    done = []
    async with get_engine().begin() as conn:
        for table, retired in RETIRED_COLUMNS.items():
            have = {r[1] for r in (await conn.execute(text(f'PRAGMA table_info("{table}")'))).fetchall()}
            cols = [c for c in retired if c in have]
            if not cols:
                continue
            # the pooled connection's own setting (0 / 1 / 2 = FAST), put back afterwards
            prev = {1: "ON", 2: "FAST"}.get((await conn.execute(text("PRAGMA secure_delete"))).scalar(), "OFF")
            await conn.execute(text("PRAGMA secure_delete=ON"))
            try:
                n = (await conn.execute(text(
                    f'UPDATE "{table}" SET ' + ", ".join(f'"{c}" = NULL' for c in cols)
                    + " WHERE " + " OR ".join(f'"{c}" IS NOT NULL' for c in cols)))).rowcount
                if n:
                    done.append(f"{table}: {n} rows cleared")
                if sqlite3.sqlite_version_info >= DROP_COLUMN_SQLITE:
                    for c in cols:
                        try:
                            await conn.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{c}"'))
                            done.append(f"{table}.{c} dropped")
                        except OperationalError as e:        # e.g. an index on it: stays, emptied
                            log.warning("retired column %s.%s kept (values cleared): %s", table, c,
                                        type(e.orig).__name__ if e.orig is not None else type(e).__name__)
            finally:
                await conn.execute(text(f"PRAGMA secure_delete={prev}"))
    if done:
        async with get_engine().connect() as conn:
            await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
        log.info("retired columns: %s", "; ".join(done))


async def _migrate_race_result(conn) -> None:
    """SP-276's single E-pace race (athlete.race_result) → the shared list athlete.race_results
    (SP-290; engine/e_pace.migrate_legacy), per user, before RETIRED_KEYS deletes the old key.
    Idempotent: a race already in the list is not added again; a bad value is dropped."""
    import datetime as _dt
    import json as _json
    from backend.engine import e_pace as EP
    from backend.engine import race_results as RR
    olds = (await conn.execute(text("SELECT user_id, value_json FROM user_settings WHERE key = :k"),
                               {"k": EP.LEGACY_KEY})).fetchall()
    for uid, vj in olds:
        try:
            old = _json.loads(vj)
        except ValueError:
            continue
        cur = (await conn.execute(text("SELECT value_json FROM user_settings WHERE user_id = :u AND key = :k"),
                                  {"u": uid, "k": RR.KEY})).first()
        try:
            rows = _json.loads(cur[0]) if cur else []
        except ValueError:
            rows = []
        new = EP.migrate_legacy(old, rows)
        if new == RR.normalise(rows):
            continue                                    # already there (or nothing usable)
        params = {"u": uid, "k": RR.KEY, "v": _json.dumps(new), "t": _dt.datetime.utcnow()}
        if cur is None:
            await conn.execute(text("INSERT INTO user_settings (user_id, key, value_json, updated_at) "
                                    "VALUES (:u, :k, :v, :t)"), params)
        else:
            await conn.execute(text("UPDATE user_settings SET value_json = :v, updated_at = :t "
                                    "WHERE user_id = :u AND key = :k"), params)


async def init_db():
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_schema()
