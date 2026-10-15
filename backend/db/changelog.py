"""
Change-log sync of the small user-edited rows — a trial (SP-312; docs/research/local-first-sync.md
§5.1, §9, §11 ⑤). Only `user_settings` (a few keys, below) and `activity_tags`. Off unless the
server sets TRC_CHANGE_SYNC=1; nothing in the app's pages uses it (they keep saving through their
own endpoints).

How it works (§9 item 4: the server is a replica too, and the one that numbers the changes):

  * a device edits offline and queues one change per column: {id, ts, table, row, col, op, value}.
    `row` is the setting key, or the activity tag's stable uid (SP-310, db/stable_ids.tag_uid).
  * POST /api/v1/sync/changes (api/change_sync.py) sends the queue and the cursor of the last pull.
    The server writes each change into the tenant's `changes` table (models.Change) with the next
    `seq`, applies the ones that win to the row itself, and answers with the rows changed after
    the cursor — their CURRENT values, not the raw changes: a device simply replaces its copy of
    each row it gets back and replays what it has not sent yet. So the merge rules live here only
    (a device needs no copy of them) and every device ends with the server's rows.
  * the cursor is (seq, cid): when the server's change at that seq is gone or another one (a backup
    was restored, the log was cut), the answer is `reset` with every synced row (a snapshot), as
    for a new device (no cursor).

Merge rules:

  * one column, two values: the later `ts` wins (last writer wins; ties: the larger device id). A
    device's clock ahead of the server's is clamped to the server's time on arrival, so a fast clock
    cannot win every later edit. The losing change is kept, marked not applied.
  * two columns of one row: both kept (each column has its own winner).
  * `tags` (the free-form tag list) is merged as a set, not as one value: a device sends `add` /
    `remove` of ONE tag, and each tag has its own last writer. Two devices adding a tag each keep
    both (as one LWW list, one of them would be lost). The 登山杖 and 路況 marks, stored as tags too,
    are their own columns `poles` / `surface` (one value each, LWW) and cannot be added as tags.
  * a setting the server validates as a set (課表偏好: plan_prefs.check) refuses a change that would
    make the set invalid (e.g. 長跑日上限 shorter than the weekday cap after two devices each changed
    one): INVALID_COMBINATION, the row keeps the server's values.
  * `start_local` creates a tag row (a tag made offline); it must give the row's own uid, and a
    row's start never changes. Tag rows are never deleted (a cleared mark is NULLs).

Not synced (the trial's limits): the 疼痛 fields (pain, pain_area, pain_score: saving them opens
or joins an injury event, injury_id is an integer id) and workout_id; every user_settings key
outside SETTING_KEYS / SETTING_PREFIXES — the server's bookkeeping (sync.*, *.last_result …),
this machine's (backup.*, debug.*, upstream.*), the 課表訂閱 link token, and the choices whose
endpoint does more than store the value (資料來源 moves files and rebuilds the de-dup, 課表心率區間
keeps a history …).

Writes through the app's own pages (SettingsRepository.set, the activity edit page, activity_tags
.upsert) are logged as the server's changes (device "server", ts = now) while the switch is on, by
an ORM flush hook (`_capture`), so a device pulls them too. Raw SQL writes (a migration, a backup
restore) are not logged: a restore is caught by the cursor check instead.

Backup: `changes` is a USER table inside the DB snapshot (data_registry); a restore brings back the
log of the restored rows, and a device whose cursor is newer gets `reset` (see above).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any, Optional

from sqlalchemy import event, inspect as sa_inspect, select, text
from sqlalchemy.orm import Session

from backend.db import stable_ids
from backend.db.models import ActivityTag, Change, UserSetting
from backend.engine import activity_tags as AT

SCHEMA = 1                      # the change format this server reads and writes
MIN_SCHEMA = 1                  # the oldest device format it still takes (older: refused, "update the app")
ENV = "WKO5COACH_CHANGE_SYNC"   # set as TRC_CHANGE_SYNC (backend/env_names.py); off unless 1 / on / true
SERVER_DEVICE = "server"        # the device id of the app's own writes
SKIP = "changelog_skip"         # Session.info flag: this session's writes are not logged by _capture

SETTINGS, TAGS = "user_settings", "activity_tags"
TABLES = (SETTINGS, TAGS)
SETTING_COL = "value"

# the user_settings keys a device may change: plain choices whose endpoint only stores them
SETTING_KEYS = ("charts.map.basemap", "charts.map.overlays", "ui.hint_level",
                "injury.reentry_step_up", "plan.auto.confirm_big", "plan.auto.rpe_rule")
SETTING_PREFIXES = ("plan.prefs.",)     # 課表偏好: checked as a set (plan_prefs.check)

# activity_tags: one value per column (LWW) …
TAG_SET_COLS = ("activity_type", "effort", "note", "exclusion", "name", "poles", "surface",
                "source", "file", "distance_km", "label")
TAG_KEY_COL = "start_local"             # creates the row; never changes
TAG_LIST_COL = "tags"                   # … and the free-form tags as a set (add / remove one tag)
TAG_COLS = (TAG_KEY_COL,) + TAG_SET_COLS + (TAG_LIST_COL,)
_SPECIAL_TAGS = frozenset(set(AT.POLES.values()) | {AT.POLE_NONE_TAG} | set(AT.SURFACES.values()))
_TEXT_MAX = {"note": 5000, "source": 10, "file": 300, "label": 200}

MAX_VALUE_BYTES = 20_000
_ID = re.compile(r"[A-Za-z0-9_-]{8,36}\Z")
_DEVICE = re.compile(r"[A-Za-z0-9_-]{8,64}\Z")
_UID = re.compile(r"[0-9a-f]{32}\Z")
_START = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}\Z")


def enabled() -> bool:
    return (os.environ.get(ENV) or "").strip().lower() in ("1", "on", "true", "yes")


def syncable_setting(key: Any) -> bool:
    from backend.settings.repository import known
    return isinstance(key, str) and known(key) and (key in SETTING_KEYS or key.startswith(SETTING_PREFIXES))


def setting_keys() -> list[str]:
    """Every user_settings key a device gets (stored or not)."""
    from backend.settings.repository import DEFAULTS
    return [k for k in DEFAULTS if syncable_setting(k)]


def valid_device(device: Any) -> bool:
    return isinstance(device, str) and bool(_DEVICE.match(device)) and device != SERVER_DEVICE


def utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


class Refused(Exception):
    """A change the server will never apply (the device drops it): `code` says why."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_ts(raw: Any, now: dt.datetime) -> dt.datetime:
    """A device's ISO time with an offset ('…Z' too) as naive UTC, clamped to the server's now."""
    if not isinstance(raw, str):
        raise Refused("INVALID_TS")
    try:
        t = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise Refused("INVALID_TS") from None
    if t.tzinfo is None:
        raise Refused("INVALID_TS")
    t = t.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return min(t, now)


# ---------------------------------------------------------------------------- the winner of a column

async def _winner(db, tbl: str, row: str, col: str, item: Optional[str]) -> Optional[tuple]:
    q = select(Change.ts, Change.device_id).where(Change.tbl == tbl, Change.row_id == row, Change.col == col,
                                                  Change.applied.is_(True))
    q = q.where(Change.item == item) if item is not None else q.where(Change.item.is_(None))
    hit = (await db.execute(q.order_by(Change.seq.desc()).limit(1))).first()
    return (hit[0], hit[1]) if hit else None


def newer(a: tuple, b: Optional[tuple]) -> bool:
    """(ts, device) a beats b (None = nothing logged for the column: the row's value from before
    the log, which any change replaces)."""
    return b is None or a > b


# ---------------------------------------------------------------------------- pushing

async def take_write_lock(db) -> None:
    """Start the session's transaction as a writer: the driver opens it on the first write, and
    SQLite takes the write lock then, so every read that follows (the column winners) and the
    writes are one serialized step — the app's own writes (logged by _capture) wait for it, and
    never slip in between a winner read and the write that depends on it. Writes nothing."""
    await db.execute(text("DELETE FROM changes WHERE 0"))


async def push(db, device: str, schema: int, changes: list[dict], now: Optional[dt.datetime] = None,
               athlete_id: Optional[int] = None) -> list[dict]:
    """Log and apply a device's changes, in order. Returns one result per change: {id, seq,
    status: applied | superseded} or {id, status: refused, code}. A change id seen before answers
    as it did then (a retried push). The caller commits."""
    from backend.db.current import current_athlete_id
    now = now or utcnow()
    aid = current_athlete_id() if athlete_id is None else athlete_id
    db.info[SKIP] = True
    out = []
    for ch in changes:
        cid = ch.get("id")
        try:
            if not isinstance(cid, str) or not _ID.match(cid):
                raise Refused("INVALID_ID")
            seen = (await db.execute(select(Change.seq, Change.applied).where(Change.cid == cid))).first()
            if seen:
                out.append({"id": cid, "seq": seen[0], "status": "applied" if seen[1] else "superseded"})
                continue
            out.append(await _one(db, device, schema, ch, now, aid))
        except Refused as e:
            out.append({"id": cid if isinstance(cid, str) else None, "status": "refused", "code": e.code})
    if any(r.get("status") == "applied" and r.get("table") == TAGS for r in out):
        AT._memo.clear()
    for r in out:
        r.pop("table", None)
    return out


async def _one(db, device: str, schema: int, ch: dict, now: dt.datetime, aid: int) -> dict:
    tbl, row, col, op = ch.get("table"), ch.get("row"), ch.get("col"), ch.get("op") or "set"
    value = ch.get("value")
    if tbl not in TABLES:
        raise Refused("UNKNOWN_TABLE")
    if not isinstance(row, str) or not isinstance(col, str):
        raise Refused("INVALID_ROW")
    try:
        raw = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        raise Refused("INVALID_VALUE") from None
    if len(raw.encode()) > MAX_VALUE_BYTES:
        raise Refused("VALUE_TOO_LARGE")
    ts = parse_ts(ch.get("ts"), now)
    if tbl == SETTINGS:
        item, apply = None, await _setting(db, row, col, op, value)
    else:
        item, apply = await _tag(db, row, col, op, value, aid)
    wins = newer((ts, device), await _winner(db, tbl, row, col, item))
    if wins:
        await apply()                           # may still refuse it (INVALID_COMBINATION): not logged
    c = Change(cid=ch["id"], ts=ts, received_at=now, tbl=tbl, row_id=row, col=col, op=op, item=item,
               value_json=raw, device_id=device, schema_version=schema, applied=wins)
    db.add(c)
    await db.flush()
    return {"id": c.cid, "seq": c.seq, "status": "applied" if wins else "superseded", "table": tbl}


async def _setting(db, key: str, col: str, op: str, value: Any):
    from backend.settings.repository import DEFAULT_USER, SettingsRepository, validate
    if col != SETTING_COL or op != "set":
        raise Refused("INVALID_COLUMN")
    if not syncable_setting(key):
        raise Refused("NOT_SYNCED")
    try:
        validate(key, value)
    except (ValueError, TypeError):
        raise Refused("INVALID_VALUE") from None
    repo = SettingsRepository(db, DEFAULT_USER)

    async def apply():                          # only a change that won: an older one is superseded
        if key.startswith("plan.prefs."):
            await _check_prefs(repo, key, value)
        await repo.set(key, value)
    return apply


async def _check_prefs(repo, key: str, value: Any) -> None:
    """課表偏好 are checked as a whole (plan_prefs.check): refuse a value that makes the stored set
    invalid. A set already invalid before (an old DB) does not block a change."""
    from backend.engine import plan_prefs as PP
    cur = {k: v for k, v in (await repo.all()).items() if k.startswith("plan.prefs.")}

    def ok(values: dict) -> bool:
        try:
            PP.check(PP.from_settings(values))
            return True
        except (ValueError, TypeError):
            return False
    if not ok({**cur, key: value}) and ok(cur):
        raise Refused("INVALID_COMBINATION")


async def _tag_row(db, uid: str, aid: int) -> Optional[ActivityTag]:
    return (await db.execute(select(ActivityTag).where(ActivityTag.uid == uid,
                                                       ActivityTag.athlete_id == aid))).scalar_one_or_none()


async def _tag(db, uid: str, col: str, op: str, value: Any, aid: int):
    """(item, apply) of one activity_tags change, after checking it."""
    if not _UID.match(uid):
        raise Refused("INVALID_ROW")
    if col not in TAG_COLS:
        raise Refused("INVALID_COLUMN" if col not in ("pain", "pain_area", "pain_score") else "NOT_SYNCED")
    if (col == TAG_LIST_COL) != (op in ("add", "remove")) or op not in ("set", "add", "remove"):
        raise Refused("INVALID_OP")
    row = await _tag_row(db, uid, aid)
    if col == TAG_KEY_COL:
        if not isinstance(value, str) or not _START.match(value) or stable_ids.tag_uid(aid, value) != uid:
            raise Refused("ROW_KEY_MISMATCH")
        if row is None and (await db.execute(select(ActivityTag.id).where(
                ActivityTag.athlete_id == aid, ActivityTag.start_local == value))).first():
            raise Refused("ROW_KEY_MISMATCH")          # an old row of that start with another uid

        async def create():
            if await _tag_row(db, uid, aid) is None:
                db.add(ActivityTag(uid=uid, athlete_id=aid, start_local=value,
                                   activity_type_overridden=False, effort_overridden=False))
                await db.flush()
        return None, create
    if row is None:
        raise Refused("UNKNOWN_ROW")                   # a new row starts with its start_local change
    if col == TAG_LIST_COL:
        tag = _check_tag(value)

        async def edit():
            cur = AT.tags_of({"tags_json": row.tags_json})
            if op == "add":
                if any(t.lower() == tag.lower() for t in cur):
                    return
                if len(cur) >= AT.TAGS_MAX:
                    return                             # full: the add is logged, the list stays
                new = cur + [tag]
            else:
                new = [t for t in cur if t.lower() != tag.lower()]
            AT.apply_update(row, tags=new)
        return tag.lower(), edit
    _check_value(col, value)

    async def set_col():
        if col in ("source", "file", "distance_km", "label"):
            setattr(row, col, value)
            row.updated_at = utcnow()
        else:
            AT.apply_update(row, **{col: value})
    return None, set_col


def _check_tag(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > AT.TAG_MAX_LEN:
        raise Refused("INVALID_VALUE")
    tag = value.strip()
    if tag in _SPECIAL_TAGS:
        raise Refused("USE_POLES_OR_SURFACE")
    return tag


def _check_value(col: str, value: Any) -> None:
    if col in _TEXT_MAX:
        if value is not None and (not isinstance(value, str) or len(value) > _TEXT_MAX[col]):
            raise Refused("INVALID_VALUE")
        return
    if col == "distance_km":
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not 0 <= value <= 2000):
            raise Refused("INVALID_VALUE")
        return
    err = AT.validate(**{col: value}) if value is not None else None
    if err or (col == "name" and value is not None and not isinstance(value, str)):
        raise Refused("INVALID_VALUE")


# ---------------------------------------------------------------------------- pulling

def tag_values(t: ActivityTag) -> dict:
    """An activity tag row as a device holds it (the synced columns)."""
    tags = AT.tags_of({"tags_json": t.tags_json})
    return {"start_local": t.start_local, "source": t.source, "file": t.file, "distance_km": t.distance_km,
            "label": t.label,
            "activity_type": t.activity_type if t.activity_type_overridden else None,
            "effort": t.effort if t.effort_overridden else None,
            "note": t.note, "exclusion": t.exclusion, "name": t.name,
            "tags": [x for x in tags if x not in _SPECIAL_TAGS],
            "poles": AT.pole_choice(tags), "surface": AT.surface_of(tags)}


async def _row(db, tbl: str, row: str, aid: int) -> dict:
    if tbl == SETTINGS:
        from backend.settings.repository import DEFAULT_USER, SettingsRepository
        return {"table": tbl, "row": row, "values": {SETTING_COL: await SettingsRepository(db, DEFAULT_USER).get(row)}}
    t = await _tag_row(db, row, aid)
    return {"table": tbl, "row": row, "values": tag_values(t) if t is not None else None}


async def snapshot(db, aid: int) -> list[dict]:
    """Every synced row: the syncable settings (stored or default) and the athlete's activity tags."""
    from backend.settings.repository import DEFAULT_USER, SettingsRepository
    repo = SettingsRepository(db, DEFAULT_USER)
    out = [{"table": SETTINGS, "row": k, "values": {SETTING_COL: await repo.get(k)}} for k in setting_keys()]
    tags = (await db.execute(select(ActivityTag).where(ActivityTag.athlete_id == aid,
                                                       ActivityTag.uid.is_not(None))
                             .order_by(ActivityTag.id))).scalars().all()
    return out + [{"table": TAGS, "row": t.uid, "values": tag_values(t)} for t in tags]


async def last_cursor(db) -> dict:
    hit = (await db.execute(select(Change.seq, Change.cid).order_by(Change.seq.desc()).limit(1))).first()
    return {"seq": hit[0], "cid": hit[1]} if hit else {"seq": 0, "cid": None}


async def pull(db, since: Optional[dict], limit: int = 1000, athlete_id: Optional[int] = None) -> dict:
    """The rows changed after the cursor `since` ({seq, cid}; None = a new device: every row, and
    {seq: 0} = the log was empty at the last pull), at most `limit`
    changes' worth: {rows, cursor, more, reset, snapshot}. The cursor is read BEFORE the rows, so
    a row is never older than its cursor (a write in between is sent again next time, which is
    harmless: rows are whole current values)."""
    from backend.db.current import current_athlete_id
    aid = current_athlete_id() if athlete_id is None else athlete_id
    seq0 = int((since or {}).get("seq") or 0)
    reset = False
    if seq0 > 0:
        at = (await db.execute(select(Change.cid).where(Change.seq == seq0))).scalar_one_or_none()
        reset = at is None or at != (since or {}).get("cid")
    if since is None or reset:
        cursor = await last_cursor(db)
        return {"rows": await snapshot(db, aid), "cursor": cursor, "more": False, "reset": reset,
                "snapshot": True}
    page = (await db.execute(select(Change.seq, Change.cid, Change.tbl, Change.row_id, Change.applied)
                             .where(Change.seq > seq0).order_by(Change.seq).limit(limit + 1))).all()
    more = len(page) > limit
    page = page[:limit]
    cursor = {"seq": page[-1][0], "cid": page[-1][1]} if page else {"seq": seq0, "cid": since.get("cid")}
    touched: list = []
    for _s, _c, tbl, row, applied in page:
        if applied and (tbl, row) not in touched:
            touched.append((tbl, row))
    rows = [await _row(db, tbl, row, aid) for tbl, row in touched]
    return {"rows": rows, "cursor": cursor, "more": more, "reset": False, "snapshot": False}


# ---------------------------------------------------------------------------- the app's own writes

def _old(state, attr: str):
    h = state.attrs[attr].history
    if h.deleted:
        return h.deleted[0]
    if h.unchanged:
        return h.unchanged[0]
    return None if state.pending or not state.has_identity else getattr(state.obj(), attr)


def _setting_changes(obj: UserSetting) -> list[dict]:
    if not syncable_setting(obj.key):
        return []
    st = sa_inspect(obj)
    h = st.attrs.value_json.history
    if not h.added or (h.deleted and h.deleted[0] == h.added[0]):
        return []
    try:
        value = json.loads(obj.value_json)
    except (TypeError, ValueError):
        return []
    return [{"tbl": SETTINGS, "row_id": obj.key, "col": SETTING_COL, "op": "set", "item": None, "value": value}]


def _tag_changes(obj: ActivityTag) -> list[dict]:
    st = sa_inspect(obj)
    new = st.pending or not st.has_identity
    uid = obj.uid or stable_ids.tag_uid(obj.athlete_id, obj.start_local)
    old = {} if new else {a: _old(st, a) for a in (
        "start_local", "source", "file", "distance_km", "label", "activity_type", "activity_type_overridden",
        "effort", "effort_overridden", "note", "exclusion", "name", "tags_json")}

    def vals(get) -> dict:
        tags = AT.tags_of({"tags_json": get("tags_json")})
        return {"start_local": get("start_local"), "source": get("source"), "file": get("file"),
                "distance_km": get("distance_km"), "label": get("label"),
                "activity_type": get("activity_type") if get("activity_type_overridden") else None,
                "effort": get("effort") if get("effort_overridden") else None,
                "note": get("note"), "exclusion": get("exclusion"), "name": get("name"),
                "tags": [x for x in tags if x not in _SPECIAL_TAGS],
                "poles": AT.pole_choice(tags), "surface": AT.surface_of(tags)}
    a, b = vals(old.get), vals(lambda k: getattr(obj, k))
    out = []
    base = {"tbl": TAGS, "row_id": uid}
    for col in (TAG_KEY_COL,) + TAG_SET_COLS:
        if a[col] != b[col]:
            out.append({**base, "col": col, "op": "set", "item": None, "value": b[col]})
    was = {t.lower(): t for t in a["tags"]}
    now = {t.lower(): t for t in b["tags"]}
    out += [{**base, "col": TAG_LIST_COL, "op": "add", "item": k, "value": now[k]} for k in now if k not in was]
    out += [{**base, "col": TAG_LIST_COL, "op": "remove", "item": k, "value": was[k]} for k in was if k not in now]
    return out


_HAS_TABLE: dict = {}


def _has_table(session: Session) -> bool:
    conn = session.connection()
    key = id(conn.engine)
    hit = _HAS_TABLE.get(key)
    if hit is None:
        hit = _HAS_TABLE[key] = sa_inspect(conn).has_table("changes")
        if len(_HAS_TABLE) > 1000:
            _HAS_TABLE.clear()
    return hit


@event.listens_for(Session, "before_flush")
def _capture(session: Session, _flush_context, _instances) -> None:
    """While the switch is on: a write of a synced row through the app's own code (not a device's
    push) is logged as the server's change, so the devices pull it."""
    if session.info.get(SKIP) or not enabled():
        return
    found = []
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, UserSetting):
            found += _setting_changes(obj)
        elif isinstance(obj, ActivityTag) and obj.start_local:
            found += _tag_changes(obj)
    if not found or not _has_table(session):
        return
    now = utcnow()
    for c in found:
        session.add(Change(cid=stable_ids.new_uid(), ts=now, received_at=now, tbl=c["tbl"], row_id=c["row_id"],
                           col=c["col"], op=c["op"], item=c["item"],
                           value_json=json.dumps(c["value"], ensure_ascii=False), device_id=SERVER_DEVICE,
                           schema_version=SCHEMA, applied=True))
