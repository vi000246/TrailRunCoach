"""
Stable row ids and `updated_at` on the tables the user edits (SP-310; docs/research/
local-first-sync.md §5.1, §11 ③). Any device sync (SP-312's change log, SP-316's plan rows)
needs a row id that two copies of a DB cannot both hand out: the integer `id` is counted per
DB file, so a row added offline on one device and another added on the server get the same
number. The integer ids stay (every API and link keeps using them); `uid` sits next to them.

    TABLES         the tables with a `uid` and an `updated_at` from here
    new_uid()      a random UUID (version 4) as 32 lowercase hex characters
    tag_uid(a, k)  an activity tag's uid: a UUID (version 5) of the tag's own unique key, the
                   athlete and the activity's local start minute (`start_local`). The same
                   activity marked on two devices gets the same uid, so a sync merges the two
                   marks into one row instead of meeting the (athlete, start minute) key twice.
                   Tag rows are never deleted (a cleared mark is NULLs), so a key never needs a
                   second id. Not the FIT hash (SP-308): most WKO5 activities have no FIT, and a
                   COROS and a TP copy of one run differ in bytes but share the start minute
                   (activity_tags.find)
    migrate(conn)  the columns, the ids, the times and the unique index on an existing DB
                   (database._migrate_schema: every tenant DB at its init_db — the owner's at
                   start-up, a user's at accounts/tenants.ensure_ready)

`uid` is NULL-able in the DB (SQLite's ADD COLUMN cannot add NOT NULL without a constant
default) but always filled: the models' default on insert (models.py), race_calc_store's own
INSERT, and migrate() for the rows from before and any a plain-sqlite writer left without one.
The UNIQUE index ux_<table>_uid keeps the ids apart (NULLs never collide in it).

`updated_at`: naive UTC like every timestamp of the app DB (datetime.utcnow, stored by
SQLAlchemy's DateTime as 'YYYY-MM-DD HH:MM:SS.ffffff'), written on insert and on every ORM or
Core update (the models' default / onupdate) and by race_calc_store's upsert. A row from
before gets the time it was last known to change: its created_at, race_calc's saved_at (the
server's local time, turned into UTC), else the migration's time.

plan_sessions is not here: it has had its own `uid` from the start (engine/reconcile.new_uid:
12 hex characters of a UUID4, NOT NULL, UNIQUE (athlete_id, uid), kept through every
regeneration and used as the COROS push key) and an `updated_at` that plan_store moves itself
(only on a real change, one second apart: the 課表訂閱 feed's SEQUENCE).
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Optional

from sqlalchemy import text

TABLES = ("activity_tags", "injury_events", "workout_templates_user", "race_calc", "plan_change_log")
TAG_NS = uuid.UUID("bc1be371-9870-4041-b8d4-4071efff6a54")     # activity tags' uid namespace (fixed)
STAMP = "%Y-%m-%d %H:%M:%S.%f"                                 # SQLAlchemy's DateTime on SQLite
# where a row from before takes its updated_at when the column is new (else the migration's time)
_SINCE = {"injury_events": "created_at", "workout_templates_user": "created_at", "plan_change_log": "created_at"}


def new_uid() -> str:
    return uuid.uuid4().hex


def tag_uid(athlete_id, start_local) -> str:
    """The uid of the activity tag keyed (athlete_id, start_local); athlete None = 1 (the
    column's default). A tag without a key (never written by the app) gets a random one."""
    if not start_local:
        return new_uid()
    return uuid.uuid5(TAG_NS, f"{athlete_id if athlete_id is not None else 1}|{start_local}").hex


def tag_uid_default(context) -> str:
    """ActivityTag.uid on insert: SQLAlchemy's context-sensitive column default."""
    p = context.get_current_parameters()
    return tag_uid(p.get("athlete_id"), p.get("start_local"))


def index_name(table: str) -> str:
    return f"ux_{table}_uid"


def stamp(t: Optional[dt.datetime] = None) -> str:
    """A naive UTC time as the DB stores DateTime columns (now when None)."""
    return (t or dt.datetime.utcnow()).strftime(STAMP)


async def _cols(conn, table: str) -> set:
    return {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}


async def migrate(conn) -> None:
    """Idempotent: a column, an id, a time or the index already there is left as it is."""
    now = stamp()
    for table in TABLES:
        cols = await _cols(conn, table)
        if not cols:
            continue                    # no such table (a partial test schema): create_all makes it
        if "uid" not in cols:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN uid VARCHAR(32)"))
        if "updated_at" not in cols:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN updated_at DATETIME"))
        await _fill_updated(conn, table, cols, now)
        await _fill_uids(conn, table, cols)
        await conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name(table)} ON {table} (uid)"))


async def _fill_updated(conn, table: str, cols: set, now: str) -> None:
    if table == "race_calc" and "saved_at" in cols:
        rows = (await conn.execute(text(f"SELECT rowid, saved_at FROM {table} WHERE updated_at IS NULL"))).fetchall()
        if rows:
            await conn.execute(text(f"UPDATE {table} SET updated_at = :t WHERE rowid = :r"),
                               [{"t": _local_to_utc(s) or now, "r": r} for r, s in rows])
        return
    since = _SINCE.get(table)
    src = f"COALESCE({since}, :now)" if since in cols else ":now"
    await conn.execute(text(f"UPDATE {table} SET updated_at = {src} WHERE updated_at IS NULL"), {"now": now})


def _local_to_utc(s) -> Optional[str]:
    """race_calc_store's saved_at (the server's local time, 'YYYY-MM-DD HH:MM:SS') in UTC."""
    try:
        t = dt.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.astimezone()              # naive = this machine's local time, as it was written
    return stamp(t.astimezone(dt.timezone.utc).replace(tzinfo=None))


async def _fill_uids(conn, table: str, cols: set) -> None:
    """Every row without a uid gets one; a uid on more than one row (a copied row) stays with the
    first and the others get new ones, so the unique index can always be made."""
    if table == "activity_tags" and "start_local" in cols:
        ath = "athlete_id" if "athlete_id" in cols else "NULL"
        rows = (await conn.execute(text(f"SELECT rowid, {ath}, start_local FROM {table} WHERE uid IS NULL"))).fetchall()
        fill = [{"u": tag_uid(a, k), "r": r} for r, a, k in rows]
    else:
        rows = (await conn.execute(text(f"SELECT rowid FROM {table} WHERE uid IS NULL"))).fetchall()
        fill = [{"u": new_uid(), "r": r} for (r,) in rows]
    if fill:
        await conn.execute(text(f"UPDATE {table} SET uid = :u WHERE rowid = :r"), fill)
    dups = (await conn.execute(text(f"SELECT uid FROM {table} WHERE uid IS NOT NULL GROUP BY uid "
                                    "HAVING COUNT(*) > 1"))).fetchall()
    for (u,) in dups:
        extra = (await conn.execute(text(f"SELECT rowid FROM {table} WHERE uid = :u ORDER BY rowid"),
                                    {"u": u})).fetchall()[1:]
        await conn.execute(text(f"UPDATE {table} SET uid = :u WHERE rowid = :r"),
                           [{"u": new_uid(), "r": r} for (r,) in extra])
