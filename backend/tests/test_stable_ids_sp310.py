"""SP-310: a stable row id (`uid`, a UUID) and `updated_at` on the tables the user edits —
activity_tags, injury_events, workout_templates_user, race_calc, plan_change_log
(docs/research/local-first-sync.md §5.1, §11 ③; plan_sessions has had its `uid` all along).

The migration (backend/db/stable_ids.py, run by database._migrate_schema on every tenant DB)
on empty tables and on rows from before SP-310, new rows and edits writing `updated_at`, the
ids never repeating, every tenant DB migrated, and the API carrying `uid` next to the integer
ids it has always used. tmp SQLite only."""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import re
import sqlite3
import time

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db import stable_ids as SI
from backend.tests.test_accounts_sp384 import server  # noqa: F401  (fixture)
from backend.tests.test_injuries import api  # noqa: F401  (fixture)
from backend.tests.test_racepower_v2 import client  # noqa: F401  (fixture)

HEX32 = re.compile(r"^[0-9a-f]{32}$")
OLD = "2020-01-01 00:00:00.000000"          # an updated_at long before any test runs

# The five tables as main had them before SP-310 (2026-10-12, e2d15190: create_all's DDL; race_calc
# may also have engine/race_calc_store's own, which has the same columns)
PRE_SP310 = {
    "activity_tags": (
        "CREATE TABLE activity_tags (id INTEGER NOT NULL, athlete_id INTEGER NOT NULL, start_local VARCHAR(16) "
        "NOT NULL, source VARCHAR(10), file VARCHAR(300), workout_id INTEGER, distance_km DOUBLE, label "
        "VARCHAR(200), activity_type VARCHAR(20), activity_type_overridden BOOLEAN NOT NULL, effort VARCHAR(20), "
        "effort_overridden BOOLEAN NOT NULL, note TEXT, exclusion VARCHAR(10), name VARCHAR(200), tags_json TEXT, "
        "pain INTEGER, pain_area VARCHAR(40), pain_score INTEGER, injury_id INTEGER, updated_at DATETIME NOT NULL, "
        "PRIMARY KEY (id), UNIQUE (athlete_id, start_local))"),
    "injury_events": (
        "CREATE TABLE injury_events (id INTEGER NOT NULL, athlete_id INTEGER NOT NULL, area VARCHAR(40) NOT NULL, "
        "side VARCHAR(8), kind VARCHAR(10) NOT NULL, severity VARCHAR(10) NOT NULL, pain_max INTEGER, onset_date "
        "VARCHAR(10) NOT NULL, onset_key VARCHAR(16), onset_file VARCHAR(300), status VARCHAR(10) NOT NULL, "
        "resolved_date VARCHAR(10), days_missed INTEGER, pause_quality BOOLEAN NOT NULL, recurrence_of INTEGER, "
        "note TEXT, category VARCHAR(10) DEFAULT 'injury' NOT NULL, illness VARCHAR(10), condition VARCHAR(20), "
        "walkrun_from VARCHAR(10), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id))"),
    "workout_templates_user": (
        "CREATE TABLE workout_templates_user (id INTEGER NOT NULL, name VARCHAR(80) NOT NULL, cats_json TEXT NOT "
        "NULL, target_basis VARCHAR(8), steps_json TEXT NOT NULL, note TEXT, copied_from VARCHAR(40), gpx_filename "
        "VARCHAR(200), gpx_sha1 VARCHAR(40), gpx_km FLOAT, gpx_gain_m FLOAT, gpx_loss_m FLOAT, gpx_profile_json "
        "TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (id))"),
    "race_calc": (
        "CREATE TABLE race_calc (id INTEGER PRIMARY KEY, event_id VARCHAR(64) NOT NULL UNIQUE, inputs_json TEXT, "
        "result_json TEXT, saved_at DATETIME)"),
    "plan_change_log": (
        "CREATE TABLE plan_change_log (id INTEGER NOT NULL, athlete_id INTEGER NOT NULL, created_at DATETIME NOT "
        "NULL, \"trigger\" VARCHAR(40) NOT NULL, status VARCHAR(12) NOT NULL, summary TEXT NOT NULL, items_json "
        "TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, big_json TEXT, fingerprint "
        "VARCHAR(64), push_json TEXT, notice_uid VARCHAR(40), ref_id INTEGER, PRIMARY KEY (id))"),
}
TAG_KEYS = ("2026-09-01T07:00", "2026-09-03T06:30", "2026-09-06T05:45")


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


@pytest.fixture
def taipei():
    """The process in UTC+8, as the owner's server: race_calc.saved_at is local time."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Taipei"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def _old_db(path, rows: bool = True) -> None:
    """A tenant DB from before SP-310: the five tables without uid (race_calc and plan_change_log
    also without updated_at), with a few rows each unless `rows` is False."""
    con = sqlite3.connect(path)
    for ddl in PRE_SP310.values():
        con.execute(ddl)
    if rows:
        for i, k in enumerate(TAG_KEYS):
            con.execute("INSERT INTO activity_tags (athlete_id, start_local, activity_type_overridden, "
                        "effort_overridden, note, updated_at) VALUES (1, ?, 0, 0, ?, ?)",
                        (k, f"tag {i}", f"2026-09-0{i + 1} 08:00:00.000000"))
        for i in range(3):
            con.execute("INSERT INTO injury_events (athlete_id, area, kind, severity, onset_date, status, "
                        "pause_quality, note, created_at, updated_at) VALUES (1, 'knee', 'overuse', 'mild', ?, "
                        "'active', 0, ?, '2026-08-01 00:00:00.000000', ?)",
                        (f"2026-08-0{i + 1}", f"injury {i}", f"2026-08-0{i + 1} 09:00:00.000000"))
        for i in range(2):
            con.execute("INSERT INTO workout_templates_user (name, cats_json, steps_json, created_at, updated_at) "
                        "VALUES (?, '[]', '{}', '2026-07-01 00:00:00.000000', '2026-07-02 00:00:00.000000')",
                        (f"tpl {i}",))
        for i in range(2):
            con.execute("INSERT INTO race_calc (event_id, inputs_json, saved_at) VALUES (?, '{}', ?)",
                        (f"ev{i}", f"2026-09-1{i} 10:00:00"))
        con.execute("INSERT INTO race_calc (event_id, inputs_json, saved_at) VALUES ('ev-never', '{}', NULL)")
        for i in range(3):
            con.execute("INSERT INTO plan_change_log (athlete_id, created_at, \"trigger\", status, summary, "
                        "items_json, before_json, after_json) VALUES (1, ?, 'manual', 'applied', ?, '[]', '[]', "
                        "'[]')", (f"2026-10-0{i + 1} 01:02:03.000000", f"run {i}"))
    con.commit()
    con.close()


def _init(path, monkeypatch, times: int = 1) -> None:
    """init_db() on `path` `times` times, as the app's start-up (main.lifespan) does."""
    import backend.db.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        for _ in range(times):
            await db_mod.init_db()
        await db_mod.dispose(path)
    _run(go())


def _rows(path, table: str, cols: str = "uid, updated_at") -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(f"SELECT {cols} FROM {table} ORDER BY id").fetchall()
    finally:
        con.close()


def _uid_index(path, table: str) -> bool:
    """A UNIQUE index on (uid) alone."""
    con = sqlite3.connect(path)
    try:
        for _seq, name, unique, *_ in con.execute(f"PRAGMA index_list({table})").fetchall():
            cols = [r[2] for r in con.execute(f"PRAGMA index_info({name})").fetchall()]
            if unique and cols == ["uid"]:
                return True
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------- the schema

def test_a_new_db_has_uid_and_updated_at_on_every_user_table(tmp_path, monkeypatch):
    p = tmp_path / "new.db"
    _init(p, monkeypatch)
    con = sqlite3.connect(p)
    for t in SI.TABLES:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        assert {"uid", "updated_at"} <= cols, t
        assert _uid_index(p, t), t
    # plan_sessions: its own uid since the stored plan began, unique per athlete, never NULL
    ps = {r[1]: r for r in con.execute("PRAGMA table_info(plan_sessions)")}
    assert ps["uid"][3] == 1 and "updated_at" in ps
    con.close()
    assert set(SI.TABLES) == {"activity_tags", "injury_events", "workout_templates_user", "race_calc",
                              "plan_change_log"}


def test_empty_tables_from_before_sp310_migrate(tmp_path, monkeypatch):
    p = tmp_path / "empty.db"
    _old_db(p, rows=False)
    _init(p, monkeypatch, times=2)                       # twice: idempotent, as every start-up
    con = sqlite3.connect(p)
    for t in SI.TABLES:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        assert {"uid", "updated_at"} <= cols, t
        assert _uid_index(p, t), t
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    con.close()


def test_existing_rows_get_distinct_uids_and_updated_at_and_a_rerun_changes_nothing(tmp_path, monkeypatch,
                                                                                    taipei):
    p = tmp_path / "old.db"
    _old_db(p)
    _init(p, monkeypatch)
    first = {t: _rows(p, t) for t in SI.TABLES}
    for t, rows in first.items():
        uids = [u for u, _ in rows]
        assert rows and all(u and HEX32.match(u) for u in uids), t
        assert len(set(uids)) == len(uids), t
        assert all(ts for _, ts in rows), t                      # every row has an updated_at
        assert _uid_index(p, t), t
    # a tag's uid comes from its key (athlete + start minute): the same activity, the same id
    assert [u for u, _ in first["activity_tags"]] == [SI.tag_uid(1, k) for k in TAG_KEYS]
    # updated_at: kept where it was there; the creation time of a log row; race_calc's local
    # saved_at in UTC; a row never saved gets the migration's time
    assert [ts for _, ts in first["activity_tags"]] == ["2026-09-01 08:00:00.000000", "2026-09-02 08:00:00.000000",
                                                         "2026-09-03 08:00:00.000000"]
    assert [ts for _, ts in first["injury_events"]][0] == "2026-08-01 09:00:00.000000"
    assert [ts for _, ts in first["plan_change_log"]] == [f"2026-10-0{i} 01:02:03.000000" for i in (1, 2, 3)]
    rc = first["race_calc"]
    assert [ts for _, ts in rc[:2]] == ["2026-09-10 02:00:00.000000", "2026-09-11 02:00:00.000000"]
    assert rc[2][1][:4] >= "2026"
    # nothing else changed
    assert [n for (n,) in _rows(p, "activity_tags", "note")] == ["tag 0", "tag 1", "tag 2"]
    assert [s for (s,) in _rows(p, "plan_change_log", "summary")] == ["run 0", "run 1", "run 2"]
    _init(p, monkeypatch, times=2)
    assert {t: _rows(p, t) for t in SI.TABLES} == first          # idempotent: the same ids, the same times


def test_a_uid_column_without_its_index_is_repaired_not_fatal(tmp_path):
    """A uid column made outside the migration (activity_tags.upsert / race_calc_store on a table the
    start-up has not migrated yet) may hold NULLs; a copied row may even repeat a uid. The start-up
    must not fail on CREATE UNIQUE INDEX: the repeats get a new id, the first keeps its own."""
    p = tmp_path / "half.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE injury_events (id INTEGER PRIMARY KEY, note TEXT, uid TEXT, created_at DATETIME, "
                "updated_at DATETIME)")
    con.executemany("INSERT INTO injury_events (note, uid, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    [("a", "x" * 32, "2026-01-01", "2026-01-01"), ("b", "x" * 32, "2026-01-02", "2026-01-02"),
                     ("c", None, "2026-01-03", "2026-01-03")])
    con.commit()
    con.close()
    eng = create_async_engine(f"sqlite+aiosqlite:///{p}")

    async def go():
        async with eng.begin() as conn:
            await SI.migrate(conn)
            await SI.migrate(conn)
        await eng.dispose()
    _run(go())
    uids = [u for u, _ in _rows(p, "injury_events")]
    assert uids[0] == "x" * 32 and len(set(uids)) == 3 and all(uids)
    assert _uid_index(p, "injury_events")


def test_partial_test_schemas_still_migrate(tmp_path):
    """A table with only some columns (older test schemas: no athlete_id / updated_at) is migrated,
    a missing table is left to create_all."""
    p = tmp_path / "partial.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE activity_tags (id INTEGER PRIMARY KEY, start_local TEXT, note TEXT)")
    con.execute("INSERT INTO activity_tags (start_local, note) VALUES ('2025-01-01T08:00', 'keep')")
    con.commit()
    con.close()
    eng = create_async_engine(f"sqlite+aiosqlite:///{p}")

    async def go():
        async with eng.begin() as conn:
            await SI.migrate(conn)
        await eng.dispose()
    _run(go())
    (uid, ts), = _rows(p, "activity_tags")
    assert uid == SI.tag_uid(1, "2025-01-01T08:00") and ts
    assert {r[0] for r in sqlite3.connect(p).execute("SELECT name FROM sqlite_master WHERE type='table'")} \
        == {"activity_tags"}


# ---------------------------------------------------------------------------- every tenant

def test_every_tenant_db_is_migrated(server, tmp_path, monkeypatch):     # noqa: F811
    """The owner's DB at start-up (main.lifespan → init_db) and each user's on first use
    (accounts/tenants.ensure_ready → init_db): each DB file gets its own ids."""
    from backend import tenancy
    from backend.accounts import store, tenants as TN
    from backend.db import database
    # the owner's folder of its own: the session's fake home may already hold another test's DB
    monkeypatch.setenv(tenancy.ENV_HOME, str(tmp_path / "owner"))
    owner = tenancy.owner()
    owner.root.mkdir(parents=True, exist_ok=True)
    _old_db(owner.root / "wko5coach.db")
    users = [TN.tenant_of(store.create_user(email=f"{n}@x.tw")) for n in ("a", "b")]
    for t in users:
        t.root.mkdir(parents=True, exist_ok=True)
        _old_db(t.root / "wko5coach.db")

    async def go():
        with tenancy.use(owner):
            await database.init_db()
        for t in users:
            await TN.ensure_ready(t)
        for t in (owner, *users):
            await database.dispose(t.root / "wko5coach.db")
    _run(go())
    seen = []
    for t in (owner, *users):
        p = t.root / "wko5coach.db"
        for table in SI.TABLES:
            rows = _rows(p, table)
            assert rows and all(u and HEX32.match(u) for u, _ in rows), (t.id, table)
            assert _uid_index(p, table), (t.id, table)
        seen.append({u for u, _ in _rows(p, "injury_events")})
    assert not (seen[0] & seen[1]) and not (seen[1] & seen[2])   # random ids: no two DBs share one


# ---------------------------------------------------------------------------- writes

def _session(tmp_path):
    from backend.db.models import Base
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")

    async def setup():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    _run(setup())
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def _age(path, table: str) -> None:
    con = sqlite3.connect(path)
    con.execute(f"UPDATE {table} SET updated_at = ?", (OLD,))
    con.commit()
    con.close()


def _now_ish(ts) -> bool:
    """Written by this test run (UTC), not the aged value."""
    s = ts if isinstance(ts, str) else ts.strftime("%Y-%m-%d %H:%M:%S.%f")
    return s > OLD and s[:10] >= (dt.datetime.utcnow() - dt.timedelta(days=1)).strftime("%Y-%m-%d")


def test_activity_tag_new_and_edited_rows(tmp_path):
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    from backend.db.models import ActivityTag
    eng, S = _session(tmp_path)
    p = tmp_path / "app.db"

    async def go():
        async with S() as s:
            row = await save_activity_tag(s, ActivityUpdate(note="x"), start_local="2026-10-01T07:00")
            uid = row.uid
        _age(p, "activity_tags")
        async with S() as s:
            await save_activity_tag(s, ActivityUpdate(note="y"), start_local="2026-10-01T07:00")
        (u2, t2), = _rows(p, "activity_tags")
        _age(p, "activity_tags")
        async with S() as s:                              # a bulk update (api/injuries.delete_injury's)
            await s.execute(update(ActivityTag).where(ActivityTag.id > 0).values(injury_id=None))
            await s.commit()
        (u3, t3), = _rows(p, "activity_tags")
        return uid, u2, t2, u3, t3
    uid, u2, t2, u3, t3 = _run(go())
    _run(eng.dispose())
    assert uid == SI.tag_uid(1, "2026-10-01T07:00") and uid == u2 == u3
    assert _now_ish(t2) and _now_ish(t3)


def test_activity_tag_upsert_on_a_table_from_before_sp310(tmp_path):
    """engine/activity_tags.upsert (seed script, tests) on a DB the start-up has not migrated yet."""
    from backend.engine import activity_tags as AT
    p = tmp_path / "old.db"
    _old_db(p)
    AT.upsert(p, start_local="2026-10-02T07:00", note="new")
    rows = {r["start_local"]: r for r in AT.load(p)}
    assert rows["2026-10-02T07:00"]["uid"] == SI.tag_uid(1, "2026-10-02T07:00")
    assert rows[TAG_KEYS[0]]["uid"] is None              # the old rows: filled by the next start-up
    assert rows[TAG_KEYS[0]]["note"] == "tag 0"


def test_injury_new_and_edited_rows_through_the_api(api, tmp_path):     # noqa: F811
    p = tmp_path / "app.db"
    r = api.post("/api/v1/app/injuries", json={"area": "knee", "onset_date": "2026-08-01"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert HEX32.match(e["uid"]) and isinstance(e["id"], int)
    (uid, ts), = _rows(p, "injury_events")
    assert uid == e["uid"] and _now_ish(ts)
    _age(p, "injury_events")
    r = api.patch(f"/api/v1/app/injuries/{e['id']}", json={"note": "改"})       # the integer id, as before
    assert r.status_code == 200 and r.json()["uid"] == e["uid"]
    (uid2, ts2), = _rows(p, "injury_events")
    assert uid2 == e["uid"] and _now_ish(ts2)
    lst = api.get("/api/v1/app/injuries").json()["injuries"]
    assert [(x["id"], x["uid"]) for x in lst] == [(e["id"], e["uid"])]


def test_template_new_and_edited_rows(tmp_path):
    from backend.engine import user_templates as UT
    eng, S = _session(tmp_path)
    p = tmp_path / "app.db"
    steps = {"items": [{"kind": "work", "dur": {"type": "time", "value": 600}, "target": {"type": "none"},
                        "note": ""}]}

    async def go():
        async with S() as s:
            t = await UT.create(s, {"name": "爬坡", "cats": ["trail"], "steps": steps})
        _age(p, "workout_templates_user")
        async with S() as s:
            t2 = await UT.update(s, t["id"], {"name": "爬坡二"})
            listed = await UT.list_all(s)
        return t, t2, listed
    t, t2, listed = _run(go())
    _run(eng.dispose())
    assert HEX32.match(t["uid"]) and t2["uid"] == t["uid"] and listed[0]["uid"] == t["uid"]
    (uid, ts), = _rows(p, "workout_templates_user")
    assert uid == t["uid"] and _now_ish(ts)


def test_change_log_new_rows_and_a_status_change(tmp_path):
    from backend.db.models import PlanChangeLog
    from backend.engine import plan_auto as PA
    eng, S = _session(tmp_path)
    p = tmp_path / "app.db"

    async def go():
        async with S() as s:
            r = await PA._add_entry(s, trigger="manual", status="pending", summary="測試")
            rid = r.id
        _age(p, "plan_change_log")
        async with S() as s:
            r = await s.get(PlanChangeLog, rid)
            r.status = "rejected"                       # as plan_auto.reject does
            await s.commit()
            return await PA.entries(s)
    log = _run(go())
    _run(eng.dispose())
    (uid, ts), = _rows(p, "plan_change_log")
    assert HEX32.match(uid) and _now_ish(ts)
    assert [(x["id"], x["uid"]) for x in log] == [(1, uid)]


def test_race_calc_new_and_edited_rows(tmp_path):
    from backend.engine import race_calc_store as RC
    p = tmp_path / "app.db"
    a = RC.save("ev1", {"x": 1}, None, p)
    assert HEX32.match(a["uid"]) and a["updated_at"]
    _age(p, "race_calc")
    b = RC.save("ev1", {"x": 2}, {"r": 1}, p)
    assert b["uid"] == a["uid"] and b["updated_at"] > OLD and _now_ish(b["updated_at"])
    assert RC.get("ev1", p)["uid"] == a["uid"]
    assert RC.save("ev2", {}, None, p)["uid"] != a["uid"]


def test_race_calc_on_a_table_from_before_sp310(tmp_path):
    """race_calc_store writes with plain sqlite (no init_db): an old table gets the columns, an old
    row its uid on its next save."""
    from backend.engine import race_calc_store as RC
    p = tmp_path / "old.db"
    _old_db(p)
    assert RC.get("ev0", p)["uid"] is None                  # readable before the migration
    r = RC.save("ev0", {"x": 1}, None, p)
    assert HEX32.match(r["uid"]) and _now_ish(r["updated_at"])
    assert HEX32.match(RC.save("ev9", {}, None, p)["uid"])


def test_saved_api_carries_the_uid(client, monkeypatch, tmp_path):     # noqa: F811
    from backend.api import racepower as RP
    from backend.engine import race_calc_store as RC
    monkeypatch.setattr(RC, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(RP, "_event", lambda eid: object())
    r = client.put("/api/v1/racepower/saved/race1", json={"inputs": {"type": "trail"}})
    assert r.status_code == 200 and HEX32.match(r.json()["uid"]) and r.json()["saved_at"]
    got = client.get("/api/v1/racepower/saved/race1").json()["saved"]
    assert got["uid"] == r.json()["uid"] and got["updated_at"]


# ---------------------------------------------------------------------------- ids never repeat

def test_ids_never_repeat(tmp_path):
    from sqlalchemy.exc import IntegrityError
    from backend.db.models import ActivityTag, InjuryEvent, PlanChangeLog, WorkoutTemplateUser
    eng, S = _session(tmp_path)
    p = tmp_path / "app.db"
    n = 300
    day0 = dt.datetime(2026, 1, 1, 6, 0)

    async def go():
        async with S() as s:                            # one flush each: the executemany path
            s.add_all([ActivityTag(start_local=(day0 + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M"),
                                   activity_type_overridden=False, effort_overridden=False) for i in range(n)])
            s.add_all([InjuryEvent(onset_date="2026-01-01") for _ in range(n)])
            s.add_all([WorkoutTemplateUser(name=f"t{i}", steps_json="{}") for i in range(n)])
            s.add_all([PlanChangeLog(athlete_id=1, trigger="t", status="applied", summary="s") for _ in range(n)])
            await s.commit()
        async with S() as s:                            # the unique index refuses a repeated id
            dup = (await s.execute(select(InjuryEvent.uid).limit(1))).scalar_one()
            s.add(InjuryEvent(onset_date="2026-01-02", uid=dup))
            with pytest.raises(IntegrityError):
                await s.commit()
    _run(go())
    _run(eng.dispose())
    for t in ("activity_tags", "injury_events", "workout_templates_user", "plan_change_log"):
        uids = [u for u, _ in _rows(p, t)]
        assert len(uids) == n and len(set(uids)) == n and all(HEX32.match(u) for u in uids), t
    assert len({SI.new_uid() for _ in range(10_000)}) == 10_000


def test_two_copies_of_a_db_tag_the_same_activity_with_the_same_uid(tmp_path):
    """Two devices (DB copies) marking the same activity offline: the same uid, so a later sync
    (SP-312) merges them into one row instead of meeting the (athlete, start minute) key twice."""
    from backend.engine import activity_tags as AT
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    AT.upsert(a, start_local="2026-10-05T06:00", note="phone")
    AT.upsert(b, start_local="2026-10-05T06:00", effort="easy")
    assert AT.load(a)[0]["uid"] == AT.load(b)[0]["uid"] == SI.tag_uid(1, "2026-10-05T06:00")
    assert SI.tag_uid(1, "2026-10-05T06:00") != SI.tag_uid(2, "2026-10-05T06:00")


# ---------------------------------------------------------------------------- the API

def test_a_saved_activity_tag_carries_its_uid(tmp_path):
    # The PATCH /workouts/{id}/activity JSON (_tag_json) went with SP-48; the uid
    # reaches the pages through GET /activities (the next test).
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    eng, S = _session(tmp_path)

    async def go():
        async with S() as s:
            return await save_activity_tag(s, ActivityUpdate(effort="easy"), start_local="2026-10-03T07:00")
    row = _run(go())
    _run(eng.dispose())
    assert row.uid == SI.tag_uid(1, "2026-10-03T07:00") and row.start_local == "2026-10-03T07:00"


def test_activity_json_and_the_activities_list_carry_the_tag_uid(tmp_path, monkeypatch):
    import backend.db.database as D
    from backend.api import wko5views as V
    from backend.db.models import Base
    from backend.engine import activity_tags as AT
    from backend.engine import planning
    from backend.tests.test_activity_edit import _fit_ds
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    w = next(w for w in ds.workouts if w.entry.file == "2025/0.fit")

    async def go():
        eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(D, "AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
        after = await V.patch_activity(w.idx, {"note": "x"})        # PATCH by index, as before
        lst = V.activities_list()
        await eng.dispose()
        return after, lst
    after, lst = _run(go())
    want = SI.tag_uid(1, AT.key_of(w.entry.start))
    assert after["tag_uid"] == want and after["key"] == AT.key_of(w.entry.start)
    acts = {a["file"]: a for a in lst["activities"]}
    assert acts["2025/0.fit"]["tag_uid"] == want
    assert acts["2025/1.fit"]["tag_uid"] is None                  # no stored mark: no row, no id
