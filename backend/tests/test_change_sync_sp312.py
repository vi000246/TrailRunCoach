"""
SP-312: the change-log sync trial (backend/db/changelog.py, POST /api/v1/sync/changes) — the
`changes` table and the server's seq, pushing and pulling, two devices editing offline and merging,
the merge rules (one column: last writer wins; two columns: both kept; tags: a set), resuming a pull
by seq, the tenant of the signed-in session, old clients refused, the app's own writes logged, a
restored backup. Synthetic data only: a DB in tmp_path, devices simulated in the test (`Device`).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import change_sync as CS
from backend.db import changelog as CL
from backend.db import stable_ids
from backend.engine import activity_tags as AT
from backend.i18n import UserError

URL = "/api/v1/sync/changes"
START = "2026-10-10T07:00"
UID = stable_ids.tag_uid(1, START)
T0 = dt.datetime.utcnow().replace(microsecond=0) - dt.timedelta(hours=3)


def ts(minutes: float) -> str:
    """A device time `minutes` after T0 (three hours ago), ISO with Z."""
    return (T0 + dt.timedelta(minutes=minutes)).isoformat() + "Z"


class Device:
    """A browser tab / phone: its copy of the server's rows (`confirmed`), the edits it has not sent
    (`pending`, made offline), and the cursor of its last pull. What it shows = confirmed + pending."""

    def __init__(self, cl, name: str, headers: dict | None = None):
        self.c, self.device, self.headers = cl, f"dev-{name}-0001", headers or {}
        self.confirmed: dict = {}
        self.pending: list = []
        self.cursor = None
        self.last = None

    def edit(self, table: str, row: str, col: str, value, at: float, op: str = "set") -> str:
        cid = uuid.uuid4().hex
        self.pending.append({"id": cid, "ts": ts(at), "table": table, "row": row, "col": col, "op": op,
                             "value": value})
        return cid

    def setting(self, key: str, value, at: float) -> str:
        return self.edit(CL.SETTINGS, key, "value", value, at)

    def tag(self, col: str, value, at: float, op: str = "set", row: str = UID) -> str:
        return self.edit(CL.TAGS, row, col, value, at, op)

    def sync(self, limit: int = 1000, schema: int = CL.SCHEMA) -> dict:
        """Send what is pending, then pull until there is no more."""
        body = {"schema": schema, "device": self.device, "since": self.cursor, "changes": self.pending,
                "limit": limit}
        while True:
            r = self.c.post(URL, json=body, headers=self.headers)
            assert r.status_code == 200, r.text
            got = r.json()
            self.take(got)
            if not got["more"]:
                return got
            body = {"schema": schema, "device": self.device, "since": self.cursor, "changes": [], "limit": limit}

    def take(self, got: dict) -> None:
        done = {x["id"] for x in got["results"]}
        self.pending = [p for p in self.pending if p["id"] not in done]
        if got["snapshot"]:
            self.confirmed = {}
        for row in got["rows"]:
            self.confirmed[(row["table"], row["row"])] = row["values"]
        self.cursor = got["cursor"]
        self.last = got

    def view(self) -> dict:
        out = copy.deepcopy(self.confirmed)
        for p in self.pending:
            k = (p["table"], p["row"])
            if p["table"] == CL.SETTINGS:
                out[k] = {"value": p["value"]}
                continue
            row = out.setdefault(k, {c: None for c in CL.TAG_COLS} | {"tags": []})
            if p["op"] == "add" and p["value"].lower() not in [t.lower() for t in row["tags"]]:
                row["tags"] = row["tags"] + [p["value"]]
            elif p["op"] == "remove":
                row["tags"] = [t for t in row["tags"] if t.lower() != p["value"].lower()]
            elif p["op"] == "set":
                row[p["col"]] = p["value"]
        return out

    def get(self, table: str, row: str, col: str = "value"):
        return (self.view().get((table, row)) or {}).get(col)


def changes_rows(db) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT seq, tbl, row_id, col, op, item, value_json, device_id, applied "
                           "FROM changes ORDER BY seq").fetchall()
    finally:
        con.close()


@pytest.fixture
def srv(tmp_path, monkeypatch):
    """The trial switch on, the endpoint on a tenant DB in tmp (made by init_db), with one activity tag
    from before the log (effort 一般, tags 山路 + 有杖) and one stored 課表偏好."""
    from backend.db import database
    from backend.main import _user_error
    db = tmp_path / "wko5coach.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.delenv(CL.ENV, raising=False)
    api = FastAPI()
    api.include_router(CS.router)
    api.add_exception_handler(UserError, _user_error)
    with TestClient(api, raise_server_exceptions=False) as cl:
        cl.portal.call(database.init_db)
        AT.upsert(db, start_local=START, effort="moderate", tags=["山路"], poles="with")    # switch off: not logged
        cl.portal.call(_set, "plan.prefs.long_day", "sat")
        monkeypatch.setenv(CL.ENV, "1")
        cl.db = db
        yield cl
        cl.portal.call(database.dispose, db)
    AT._memo.clear()


async def _set(key, value):
    from backend.db import database
    from backend.settings.repository import SettingsRepository
    async with database.AsyncSessionLocal() as s:
        await SettingsRepository(s).set(key, value)
        await s.commit()


# ---------------------------------------------------------------------------- the table and the switch

def test_init_db_adds_the_changes_table_to_an_existing_db(tmp_path, monkeypatch):
    """遷移: a DB from before SP-312 (no `changes`) gets the table, its index and AUTOINCREMENT (a seq
    is never handed out twice) at init_db; a second run changes nothing."""
    import asyncio
    from backend.db import database
    db = tmp_path / "wko5coach.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(database, "engine", None)

    async def go():
        await database.init_db()
        await database.dispose(db)
    asyncio.run(go())
    con = sqlite3.connect(str(db))
    con.execute("DROP TABLE changes")
    con.execute("INSERT INTO user_settings (user_id, key, value_json, updated_at) VALUES (1, 'ui.hint_level', "
                "'\"lean\"', '2026-10-01')")
    con.commit()
    con.close()
    asyncio.run(go())
    asyncio.run(go())
    con = sqlite3.connect(str(db))
    try:
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'changes'").fetchone()[0]
        assert "AUTOINCREMENT" in sql
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name = 'ix_changes_target'").fetchone()
        assert con.execute("SELECT value_json FROM user_settings WHERE key = 'ui.hint_level'").fetchone() == ('"lean"',)
    finally:
        con.close()
    from backend import data_registry as R
    t = R.table("changes")
    assert t.cls == R.USER and not t.local and t in R.export_entries()[0]


def test_the_switch_is_off_by_default(srv, monkeypatch):
    monkeypatch.delenv(CL.ENV, raising=False)
    r = srv.post(URL, json={"schema": 1, "device": "dev-a-0001", "changes": []})
    assert r.status_code == 404
    srv.portal.call(_set, "plan.prefs.long_day", "sun")         # the app's own write: not logged
    AT.upsert(srv.db, start_local=START, note="x")
    assert changes_rows(srv.db) == []
    from backend import env_names
    environ = {"TRC_CHANGE_SYNC": "1"}                         # the name to set sets the one the code reads
    env_names.apply(environ)
    assert environ == {CL.ENV: "1"}


# ---------------------------------------------------------------------------- push, pull, seq

def test_push_numbers_the_changes_and_pull_sends_the_rows_after_a_cursor(srv):
    a = Device(srv, "a")
    got = a.sync()                                              # a new device: everything
    assert got["snapshot"] and not got["reset"] and got["cursor"] == {"seq": 0, "cid": None}
    assert a.get(CL.SETTINGS, "plan.prefs.long_day") == "sat"
    assert a.get(CL.SETTINGS, "plan.prefs.cap_mode") == "soft"           # a default: sent too
    assert a.get(CL.TAGS, UID, "effort") == "moderate" and a.get(CL.TAGS, UID, "tags") == ["山路"]
    assert a.get(CL.TAGS, UID, "poles") == "with" and a.get(CL.TAGS, UID, "start_local") == START
    assert not any(k[1].startswith(("sync.", "backup.", "debug.")) or k[1] == "plan.calendar" for k in a.confirmed)
    c1 = a.setting("plan.prefs.long_day", "sun", 10)
    c2 = a.tag("note", "雨後泥濘", 11)
    got = a.sync()
    assert [(x["id"], x["seq"], x["status"]) for x in got["results"]] == [(c1, 1, "applied"), (c2, 2, "applied")]
    assert got["cursor"]["seq"] == 2 and not got["snapshot"]
    assert {(r["table"], r["row"]) for r in got["rows"]} == {(CL.SETTINGS, "plan.prefs.long_day"), (CL.TAGS, UID)}
    rows = changes_rows(srv.db)
    assert [(s, t, r, c, d, ap) for s, t, r, c, _o, _i, _v, d, ap in rows] == [
        (1, CL.SETTINGS, "plan.prefs.long_day", "value", "dev-a-0001", 1), (2, CL.TAGS, UID, "note", "dev-a-0001", 1)]
    assert a.sync()["rows"] == []                               # nothing after the cursor
    # the rows themselves were written
    con = sqlite3.connect(str(srv.db))
    assert con.execute("SELECT value_json FROM user_settings WHERE key='plan.prefs.long_day'").fetchone() == ('"sun"',)
    assert con.execute("SELECT note FROM activity_tags WHERE uid=?", (UID,)).fetchone() == ("雨後泥濘",)
    con.close()


def test_a_pull_resumes_from_its_seq_page_by_page(srv):
    """序號續傳: a device pulling in pages (limit) — or cut off after any page — carries on from the
    cursor it kept and ends where one big pull ends."""
    a, b, big = Device(srv, "a"), Device(srv, "b"), Device(srv, "big")
    for d in (a, b, big):
        d.sync()
    for i, (k, v) in enumerate([("plan.prefs.cap_mode", "hard"), ("plan.prefs.runs_per_week", 5),
                                ("ui.hint_level", "lean"), ("plan.prefs.b2b", False)]):
        b.setting(k, v, i)
    b.tag("name", "早上的山", 5)
    b.sync()
    # A: one change's worth per request, and the device "loses" the third answer (no cursor kept)
    pages = []
    body = {"schema": 1, "device": a.device, "since": a.cursor, "changes": [], "limit": 1}
    for i in range(10):
        got = srv.post(URL, json=body).json()
        pages.append(got["cursor"]["seq"])
        if i != 2:
            a.take(got)
        if not got["more"] and i != 2:
            break
        body["since"] = a.cursor
    assert pages == [1, 2, 3, 3, 4, 5]                          # page 3 sent again after the lost answer
    big.sync()
    assert a.view() == big.view() == b.view()
    assert a.get(CL.SETTINGS, "plan.prefs.runs_per_week") == 5 and a.get(CL.TAGS, UID, "name") == "早上的山"


@pytest.mark.parametrize("first", ["a", "b"])
def test_two_devices_edit_offline_and_end_with_the_same_rows(srv, first):
    """Two tabs / devices, both offline, each change things; online again in either order: every
    copy ends the same, and the same whichever came back first."""
    a, b = Device(srv, "a"), Device(srv, "b")
    a.sync()
    b.sync()
    # offline: A changes the long day at minute 30, B at minute 20 (earlier, but B comes back later
    # when first == "a"); different columns of one activity; one tag each; both change the cap mode
    a.setting("plan.prefs.long_day", "sun", 30)
    b.setting("plan.prefs.long_day", "fri", 20)
    a.tag("effort", "easy", 31)
    b.tag("note", "鞋子太滑", 21)
    a.tag("tags", "團練", 32, op="add")
    b.tag("tags", "夜跑", 22, op="add")
    a.setting("plan.prefs.cap_mode", "hard", 33)
    b.setting("plan.prefs.cap_mode", "soft", 40)
    order = (a, b) if first == "a" else (b, a)
    for d in order + order:
        d.sync()
    assert not a.pending and not b.pending
    assert a.view() == b.view()
    fresh = Device(srv, "fresh")
    fresh.sync()
    assert fresh.view() == a.view()
    assert a.get(CL.SETTINGS, "plan.prefs.long_day") == "sun"       # A's is the later edit
    assert a.get(CL.SETTINGS, "plan.prefs.cap_mode") == "soft"      # B's is the later edit
    assert a.get(CL.TAGS, UID, "effort") == "easy" and a.get(CL.TAGS, UID, "note") == "鞋子太滑"
    assert sorted(a.get(CL.TAGS, UID, "tags")) == sorted(["山路", "團練", "夜跑"])   # both tags kept
    assert a.get(CL.TAGS, UID, "poles") == "with"                   # untouched


# ---------------------------------------------------------------------------- merge rules

def test_one_column_the_later_edit_wins_whatever_arrives_last(srv):
    a, b = Device(srv, "a"), Device(srv, "b")
    a.setting("ui.hint_level", "full", 50)
    a.sync()
    b.setting("ui.hint_level", "lean", 45)                          # older, arrives later: superseded
    got = b.sync()
    assert got["results"][0]["status"] == "superseded"
    assert b.get(CL.SETTINGS, "ui.hint_level") == "full"
    # a tie goes to the larger device id; a clock in the future is clamped to the server's now, so it
    # does not win an edit made later
    b.setting("ui.hint_level", "lean", 50)
    assert b.sync()["results"][0]["status"] == "applied"            # "dev-b-…" > "dev-a-…"
    a.edit(CL.SETTINGS, "ui.hint_level", "value", "standard", 0)
    a.pending[-1]["ts"] = "2099-01-01T00:00:00Z"
    a.sync()
    stored = changes_rows(srv.db)[-1]
    assert stored[6] == '"standard"'
    con = sqlite3.connect(str(srv.db))
    when = con.execute("SELECT ts FROM changes ORDER BY seq DESC LIMIT 1").fetchone()[0]
    con.close()
    assert when < "2027"                                             # clamped
    srv.portal.call(_set, "ui.hint_level", "full")                  # the app's own write, later: wins
    b.sync()
    assert b.get(CL.SETTINGS, "ui.hint_level") == "full"


def test_two_columns_of_one_row_are_both_kept(srv):
    a, b = Device(srv, "a"), Device(srv, "b")
    a.tag("activity_type", "race", 10)
    b.tag("exclusion", "keep", 5)
    b.tag("surface", "wet", 6)
    a.sync()
    b.sync()
    a.sync()
    v = a.confirmed[(CL.TAGS, UID)]
    assert (v["activity_type"], v["exclusion"], v["surface"], v["effort"]) == ("race", "keep", "wet", "moderate")
    assert a.view() == b.view()
    con = sqlite3.connect(str(srv.db))
    row = con.execute("SELECT activity_type, activity_type_overridden, exclusion, tags_json FROM activity_tags "
                      "WHERE uid=?", (UID,)).fetchone()
    con.close()
    assert row[:3] == ("race", 1, "keep") and json.loads(row[3]) == ["山路", "有杖", "濕路"]


def test_tags_merge_as_a_set_one_tag_at_a_time(srv):
    """`tags` is a set: each tag has its own last writer. Added on two devices: both kept. Removed on
    one and added again later on the other: the later wins. The 登山杖 / 路況 marks are columns of
    their own (one value, LWW) and cannot be sent as tags."""
    a, b = Device(srv, "a"), Device(srv, "b")
    a.tag("tags", "山路", 10, op="remove")
    b.tag("tags", "山路", 12, op="add")                              # later: stays
    a.tag("tags", "長距離", 13, op="add")
    b.tag("tags", "長距離", 11, op="remove")                         # earlier than A's add: superseded
    a.tag("poles", "without", 14)
    b.tag("poles", "none", 9)                                       # older: A's 沒杖 wins
    refused = b.tag("tags", "有杖", 15, op="add")
    a.sync()
    got = b.sync()
    a.sync()
    assert {x["id"]: x.get("code") for x in got["results"] if x["status"] == "refused"} == {refused: "USE_POLES_OR_SURFACE"}
    assert a.view() == b.view()
    v = a.confirmed[(CL.TAGS, UID)]
    assert sorted(v["tags"]) == sorted(["山路", "長距離"]) and v["poles"] == "without"
    # case-insensitive, like the page's own tag list
    a.tag("tags", "  團練 ", 20, op="add")
    a.tag("tags", "LSD", 21, op="add")
    b.tag("tags", "lsd", 22, op="remove")
    a.sync()
    b.sync()
    a.sync()
    assert a.view() == b.view() and sorted(a.get(CL.TAGS, UID, "tags")) == sorted(["山路", "長距離", "團練"])


def test_a_pref_change_that_breaks_the_set_is_refused(srv):
    """課表偏好 are one set (plan_prefs.check): two devices each change one cap; the one that would make
    the long-day cap shorter than the weekday cap is refused, the row keeps the server's values."""
    a, b = Device(srv, "a"), Device(srv, "b")
    a.sync()
    b.sync()
    a.setting("plan.prefs.cap_long", 60, 10)
    b.setting("plan.prefs.cap_weekday", 90, 11)
    a.sync()
    got = b.sync()
    assert got["results"][0] == {"id": got["results"][0]["id"], "status": "refused", "code": "INVALID_COMBINATION"}
    a.sync()
    assert a.view() == b.view()
    assert (a.get(CL.SETTINGS, "plan.prefs.cap_long"), a.get(CL.SETTINGS, "plan.prefs.cap_weekday")) == (60, None)


def test_a_tag_made_offline_creates_its_row_once(srv):
    """A tag for an activity no tag existed for: the device sends start_local first (the row's key,
    which gives its uid). Two devices tagging the same activity offline meet in one row."""
    start = "2026-10-12T18:30"
    uid = stable_ids.tag_uid(1, start)
    a, b = Device(srv, "a"), Device(srv, "b")
    for d, (col, v, at) in ((a, ("effort", "max", 2)), (b, ("note", "間歇 6×800", 3))):
        d.tag("start_local", start, at - 1, row=uid)
        d.tag(col, v, at, row=uid)
    a.sync()
    b.sync()
    a.sync()
    v = a.confirmed[(CL.TAGS, uid)]
    assert (v["start_local"], v["effort"], v["note"]) == (start, "max", "間歇 6×800")
    assert a.view() == b.view()
    con = sqlite3.connect(str(srv.db))
    assert con.execute("SELECT COUNT(*) FROM activity_tags WHERE start_local=?", (start,)).fetchone() == (1,)
    con.close()
    assert AT.find(AT.load(srv.db), dt.datetime(2026, 10, 12, 18, 30))["effort"] == "max"   # the app reads it


def test_bad_changes_are_refused_one_by_one_and_a_retry_applies_once(srv):
    a = Device(srv, "a")
    ok = a.setting("ui.hint_level", "lean", 1)
    bad = {
        a.edit("plan_sessions", "x", "title", "y", 2): "UNKNOWN_TABLE",
        a.setting("sync.primary_source", "garmin", 2): "NOT_SYNCED",       # moves files: not synced
        a.setting("plan.calendar", None, 2): "NOT_SYNCED",                 # a link token
        a.setting("backup.dir", "/tmp", 2): "NOT_SYNCED",                  # this machine's
        a.setting("no.such.key", 1, 2): "NOT_SYNCED",
        a.setting("plan.prefs.cap_mode", "never", 2): "INVALID_VALUE",
        a.tag("pain", 2, 2): "NOT_SYNCED",                                 # opens an injury event
        a.tag("effort", "very", 2): "INVALID_VALUE",
        a.tag("note", "x", 2, row=stable_ids.tag_uid(1, "2026-01-01T00:00")): "UNKNOWN_ROW",
        a.tag("start_local", "2026-01-01T00:00", 2): "ROW_KEY_MISMATCH",   # not this row's start
        a.tag("tags", ["a", "b"], 2, op="add"): "INVALID_VALUE",
        a.tag("tags", "a", 2): "INVALID_OP",                               # a set, not one list value
        a.setting("ui.hint_level", "full", 2): "INVALID_TS",
    }
    a.pending[-1]["ts"] = "yesterday"
    first = list(a.pending)
    got = a.sync()
    res = {x["id"]: x for x in got["results"]}
    assert res[ok]["status"] == "applied"
    for cid, code in bad.items():
        assert res[cid]["status"] == "refused" and res[cid]["code"] == code, (cid, res[cid])
    assert len(changes_rows(srv.db)) == 1                                  # refused ones are not logged
    # the answer was lost: the device sends the same changes again
    again = srv.post(URL, json={"schema": 1, "device": a.device, "changes": first[:1]}).json()
    assert again["results"] == [{"id": ok, "seq": 1, "status": "applied"}] and len(changes_rows(srv.db)) == 1
    assert srv.post(URL, json={"schema": 1, "device": "server", "changes": []}).status_code == 400


def test_an_older_client_is_refused_and_told_to_update(srv, monkeypatch):
    a = Device(srv, "a")
    a.setting("ui.hint_level", "lean", 1)
    monkeypatch.setattr(CL, "MIN_SCHEMA", 2)
    monkeypatch.setattr(CL, "SCHEMA", 2)
    r = srv.post(URL, json={"schema": 1, "device": a.device, "changes": a.pending})
    assert r.status_code == 426
    d = r.json()["detail"]
    assert d["code"] == "SYNC_CLIENT_TOO_OLD" and d["params"]["min_schema"] == 2 and "更新" in d["message"]
    assert changes_rows(srv.db) == []                                      # nothing taken
    r = srv.post(URL, json={"schema": 3, "device": a.device, "changes": a.pending})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SYNC_SERVER_TOO_OLD"
    r = srv.post(URL, json={"schema": 2, "device": a.device, "changes": a.pending})
    assert r.status_code == 200 and r.json()["results"][0]["status"] == "applied"
    con = sqlite3.connect(str(srv.db))
    assert con.execute("SELECT schema_version FROM changes").fetchall() == [(2,)]
    con.close()


# ---------------------------------------------------------------------------- the app's own writes

def test_the_apps_own_writes_are_logged_and_pulled_while_on(srv):
    """Saving through the app's own pages (SettingsRepository, the activity edit page's apply_update /
    upsert) adds the server's changes; a device pulls them. Keys that are not synced are not logged."""
    a = Device(srv, "a")
    a.sync()
    srv.portal.call(_set, "plan.prefs.long_day", "sun")
    srv.portal.call(_set, "sync.coros.last_result", {"status": "ok"})      # bookkeeping: not logged
    srv.portal.call(_set, "plan.prefs.long_day", "sun")                    # the same value: nothing
    AT.upsert(srv.db, start_local=START, tags=["山路", "團練", "有杖"], surface="dry", effort=None)
    rows = changes_rows(srv.db)
    assert {(t, r, c, o, i, d) for _s, t, r, c, o, i, _v, d, _a in rows} == {
        (CL.SETTINGS, "plan.prefs.long_day", "value", "set", None, "server"),
        (CL.TAGS, UID, "effort", "set", None, "server"),
        (CL.TAGS, UID, "surface", "set", None, "server"),
        (CL.TAGS, UID, "tags", "add", "團練", "server")}
    a.sync()
    v = a.confirmed[(CL.TAGS, UID)]
    assert a.get(CL.SETTINGS, "plan.prefs.long_day") == "sun"
    assert (v["effort"], v["surface"], v["poles"], v["tags"]) == (None, "dry", "with", ["山路", "團練"])
    # an offline edit made before the page's save loses to it; one made after wins
    a.setting("plan.prefs.long_day", "mon", -60)
    a.tag("effort", "easy", 0)
    a.pending[-1]["ts"] = dt.datetime.utcnow().isoformat() + "Z"           # after the page's save
    a.sync()
    assert a.get(CL.SETTINGS, "plan.prefs.long_day") == "sun" and a.get(CL.TAGS, UID, "effort") == "easy"


# ---------------------------------------------------------------------------- backup / restore

def test_a_restored_backup_sends_the_devices_everything_again(tmp_path, monkeypatch):
    """備份: `changes` is in the DB snapshot like the rows it describes. After a restore the log is
    the backup's again: a device whose cursor is newer (or names a change the log no longer has)
    gets `reset` and the restored rows."""
    from backend.db import database
    from backend.engine import backup as B
    from backend.main import _user_error
    from backend.tests.test_backup import app_tenant
    home = tmp_path / "home"
    db = app_tenant(home, secrets={}, files={})
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(database, "engine", None)
    monkeypatch.setenv(CL.ENV, "1")
    api = FastAPI()
    api.include_router(CS.router)
    api.add_exception_handler(UserError, _user_error)
    with TestClient(api, raise_server_exceptions=False) as cl:
        cl.portal.call(database.init_db)
        a, b = Device(cl, "a"), Device(cl, "b")
        a.setting("plan.prefs.long_day", "sun", 1)
        a.sync()
        b.sync()
        kept = dict(b.cursor)
        r = B.create_backup(db, tmp_path / "out")
        a.setting("plan.prefs.long_day", "mon", 2)
        a.setting("ui.hint_level", "lean", 3)
        a.sync()
        assert a.cursor["seq"] == 3
        cl.portal.call(database.dispose, db)
        B.restore(r["path"], db, local_dir=home / "backups")
        cl.portal.call(database.dispose, db)
        got = a.sync()                                                     # cursor 3: not in the log any more
        assert got["reset"] and got["snapshot"] and got["cursor"]["seq"] == 1
        assert a.get(CL.SETTINGS, "plan.prefs.long_day") == "sun" and a.get(CL.SETTINGS, "ui.hint_level") is None
        b.setting("plan.prefs.cap_mode", "hard", 4)                        # seq 2 again, another change
        b.sync()
        assert not b.last["reset"] and b.cursor["seq"] == 2                # B's cursor (seq 1) is still there
        stale = Device(cl, "stale")
        stale.cursor = {"seq": 2, "cid": "not-that-one"}
        assert stale.sync()["reset"]
        assert kept["seq"] == 1
        cl.portal.call(database.dispose, db)


# ---------------------------------------------------------------------------- the tenant: the signed-in session

from backend.tests.test_login_sp58 import (OWNER_EMAIL, app, browser, c, csrf, env, sign_in,  # noqa: E402,F401
                                           sign_up, started)


def test_each_signed_in_user_syncs_only_their_own_rows(app, c, monkeypatch):
    """租戶隔離: the tenant is the session's (sign-in mode, the real middleware). Alice's and Bob's
    changes land in their own DBs; neither pulls the other's; a cursor from another tenant's log is
    not trusted (reset); a write without the CSRF header is refused as everywhere."""
    monkeypatch.setenv(CL.ENV, "1")
    sign_up(c, "g-alice", "alice@example.com")
    bob = browser(app)
    sign_up(bob, "g-bob", "bob@example.com")
    alice_dev = Device(c, "alice", headers={"X-TRC-CSRF": csrf(c)})
    bob_dev = Device(bob, "bob", headers={"X-TRC-CSRF": csrf(bob)})
    alice_dev.setting("plan.prefs.long_day", "sun", 1)
    alice_dev.setting("ui.hint_level", "lean", 2)
    alice_dev.sync()
    bob_dev.setting("plan.prefs.long_day", "mon", 3)
    bob_dev.sync()
    assert alice_dev.cursor["seq"] == 2 and bob_dev.cursor["seq"] == 1           # each log its own seq
    alice_dev.sync()
    assert alice_dev.get(CL.SETTINGS, "plan.prefs.long_day") == "sun"
    fresh = Device(bob, "bob2", headers={"X-TRC-CSRF": csrf(bob)})
    fresh.sync()
    assert fresh.get(CL.SETTINGS, "plan.prefs.long_day") == "mon" and fresh.get(CL.SETTINGS, "ui.hint_level") is None
    # Alice's cursor in Bob's session: Bob's log has no such change -> everything of Bob's, nothing of Alice's
    stray = Device(bob, "bob3", headers={"X-TRC-CSRF": csrf(bob)})
    stray.cursor = dict(alice_dev.cursor)
    got = stray.sync()
    assert got["reset"] and stray.get(CL.SETTINGS, "plan.prefs.long_day") == "mon"
    owner = browser(app)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    od = Device(owner, "owner", headers={"X-TRC-CSRF": csrf(owner)})
    od.sync()
    assert od.get(CL.SETTINGS, "plan.prefs.long_day") == "auto"                 # the default: untouched
    r = c.post(URL, json={"schema": 1, "device": "dev-alice-0001", "changes": []})
    assert r.status_code == 403 and r.json()["code"] == "CSRF"
    assert browser(app).post(URL, json={"schema": 1, "device": "dev-anon-0001"}).status_code == 401
