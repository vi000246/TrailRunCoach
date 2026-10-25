"""
Writes through the debug API (SP-802, docs/debug-api.md §「寫入」): an AI agent changes a few of the
athlete's settings on the owner's behalf — a whitelist only, previewed before it is applied, every
write logged with what it replaced and undoable.

  * what (TARGETS):
      threshold  one dated row of plan.json's thresholds (設定 › 門檻): set lthr / aethr / mhr / rhr / cp /
                 note (a None clears the field), or delete the row. A value set here counts as typed by
                 hand: lthr_method / aethr_method / mhr_method = "manual", cp_manual = True.
      event      one event of plan.json (賽事): create (no id) or change fields of one (by id), through
                 planning.Plan.upsert_event (the page's own checks). No delete: an event's GPX goes with
                 it and that cannot be put back.
      prefs      課表偏好 (plan_prefs.Prefs fields), merged into the stored set and checked like the
                 page's save (plan_prefs.from_body + check).
  * preview → apply: `preview` computes the state after the changes on the current one, nothing written;
    it answers the diff (field by field: before → after) and a preview id (PREVIEW_TTL_S, kept in memory
    for that token). `apply` takes the id and writes the previewed state — only while the parts it touches
    are still as previewed (else CHANGED: preview again).
  * the log (LOG_KEY, the newest LOG_KEEP): per write {id, at, by (the token's name), kind, diff, before, after, undone_at}.
    `before` holds the whole touched part as it was (the thresholds, the events or the 課表偏好), so it is
    the backup of that write; `undo` puts the newest not-undone write's `before` back, while those parts
    are still its `after` (else CHANGED), and logs the undo as a write of its own.
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import json
import secrets
import time
from typing import Any, Optional

from backend.i18n import _

TARGETS = ("threshold", "event", "prefs")
THRESHOLD_SET = {"lthr": (80, 230), "aethr": (60, 220), "mhr": (100, 240), "rhr": (25, 120), "cp": (50, 700)}
NOTE_MAX = 200
MAX_CHANGES = 20
PREVIEW_TTL_S = 600
LOG_KEY = "debug.write.log"
LOG_KEEP = 30
EVENT_NO_SET = ("gpx_pending",)          # the page's flag for an upload right after the save

_PREVIEWS: dict = {}                     # preview id -> {tenant, token_id, parts, before, after, diff, changes, exp}


class WriteError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def _bad(msg: str) -> WriteError:
    return WriteError(400, "BAD_CHANGE", msg)


def reset() -> None:
    """Tests: forget the previews."""
    _PREVIEWS.clear()


# ---------------------------------------------------------------------------- the state of each part

PARTS = {"threshold": "thresholds", "event": "events", "prefs": "prefs"}


async def state(db, parts: set) -> dict:
    """{part: JSON-able value} of the parts asked: thresholds / events (plan.json rows, sorted), prefs
    (the plan.prefs.* keys as stored)."""
    from starlette.concurrency import run_in_threadpool

    from backend.engine import planning as P
    out: dict = {}
    if parts & {"thresholds", "events"}:
        plan = await run_in_threadpool(P.Plan.load)
        if "thresholds" in parts:
            out["thresholds"] = sorted((dataclasses.asdict(t) for t in plan.thresholds), key=lambda t: t["date"])
        if "events" in parts:
            out["events"] = sorted((dataclasses.asdict(e) for e in plan.events), key=lambda e: (e["date"], e["id"]))
    if "prefs" in parts:
        from backend.engine import plan_prefs as PP
        from backend.settings.repository import SettingsRepository
        repo = SettingsRepository(db)
        out["prefs"] = {k: await repo.get(k) for k in PP.KEY_FIELDS}
    return out


def fingerprint(st: dict) -> str:
    return hashlib.sha256(json.dumps(st, sort_keys=True, default=str).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------- applying changes in memory

def _num(field: str, v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise _bad(_("{f} 要是數字或 null", f=field))
    lo, hi = THRESHOLD_SET[field]
    if not lo <= float(v) <= hi:
        raise _bad(_("{f} 要在 {lo}–{hi}", f=field, lo=lo, hi=hi))
    return float(v)


def _threshold(rows: list, ch: dict) -> list:
    from backend.engine import planning as P
    date = ch.get("date")
    try:
        P._d(date)
    except (TypeError, ValueError):
        raise _bad(_("門檻要給日期 date（YYYY-MM-DD）"))
    rows = [dict(r) for r in rows]
    i = next((k for k, r in enumerate(rows) if r["date"] == date), None)
    if ch.get("delete") is True:
        if i is None:
            raise _bad(_("沒有 {d} 這一列門檻可以刪", d=date))
        return rows[:i] + rows[i + 1:]
    sets = ch.get("set")
    if not isinstance(sets, dict) or not sets:
        raise _bad(_("門檻的改動要有 set（lthr／aethr／mhr／rhr／cp／note）或 delete: true"))
    bad = set(sets) - set(THRESHOLD_SET) - {"note"}
    if bad:
        raise _bad(_("門檻不能改這些欄位：{f}", f=", ".join(sorted(bad))))
    row = rows[i] if i is not None else dataclasses.asdict(P.Threshold(date=date))
    for f, v in sets.items():
        if f == "note":
            row["note"] = str(v or "")[:NOTE_MAX]
            continue
        row[f] = _num(f, v)
        if f == "cp":
            row["cp_manual"] = True if v is not None else None
            row["cp_method"] = None
            row["wprime"] = None if v is None else row.get("wprime")
        elif f in ("lthr", "aethr", "mhr"):
            row[f + "_method"] = "manual" if v is not None else None
    if not any(row.get(f) is not None for f in P.Threshold.THRESHOLD_FIELDS):
        raise _bad(_("這一列門檻改完會全空：要刪就用 delete: true"))
    if i is None:
        rows.append(row)
    else:
        rows[i] = row
    return sorted(rows, key=lambda r: r["date"])


def _event(rows: list, ch: dict) -> list:
    from backend.api.plan import EventIn, _has_gpx
    from backend.engine import planning as P
    if ch.get("delete"):
        raise _bad(_("賽事不能用 API 刪（GPX 會一起刪掉、復原不回來）：到賽事頁刪"))
    sets = ch.get("set")
    if not isinstance(sets, dict) or not sets:
        raise _bad(_("賽事的改動要有 set（賽事欄位）"))
    if "id" in sets or any(k in sets for k in EVENT_NO_SET):
        raise _bad(_("set 裡不能有 id／gpx_pending：要改哪一場用 id 指定"))
    eid = ch.get("id")
    plan = P.Plan(events=[P.Event(**r) for r in rows])
    if eid is not None:
        cur = next((r for r in rows if r["id"] == eid), None)
        if cur is None:
            raise _bad(_("沒有 id 是 {i} 的賽事", i=eid))
        data = {**{k: v for k, v in cur.items() if k in EventIn.model_fields}, **sets, "id": eid}
    else:
        data = dict(sets)
    try:
        body = EventIn(**data).model_dump()
    except Exception as e:                  # noqa: BLE001 — pydantic's ValidationError: a bad field
        raise _bad(_("賽事欄位不對：{e}", e=str(e).splitlines()[0][:200]))
    body.pop("gpx_pending", None)
    try:
        plan.upsert_event(body, gpx=_has_gpx(eid))
    except (P.EventError, ValueError) as e:
        raise _bad(str(e))
    return sorted((dataclasses.asdict(e) for e in plan.events), key=lambda e: (e["date"], e["id"]))


def _prefs(stored: dict, ch: dict) -> dict:
    from backend.engine import plan_prefs as PP
    sets = ch.get("set")
    if not isinstance(sets, dict) or not sets:
        raise _bad(_("課表偏好的改動要有 set（偏好欄位）"))
    # a field (runs) or its stored key (plan.prefs.runs_per_week): either names the same preference
    sets = {PP.KEY_FIELDS.get(k, k): v for k, v in sets.items()}
    try:
        cur = PP.drop_overlaps(PP.from_settings(stored))[0]
        p = PP.from_body({**cur.to_dict(), **sets})
        PP.check(p)
    except (TypeError, ValueError) as e:
        raise _bad(str(e))
    return p.settings()


def compute(before: dict, changes: list) -> dict:
    """The state after `changes` (in order) on `before`; WriteError on a bad change."""
    after = copy.deepcopy(before)
    for ch in changes:
        t = ch.get("target")
        if t == "threshold":
            after["thresholds"] = _threshold(after["thresholds"], ch)
        elif t == "event":
            after["events"] = _event(after["events"], ch)
        elif t == "prefs":
            after["prefs"] = _prefs(after["prefs"], ch)
    return after


def _rows_diff(part: str, a: list, b: list, key: str) -> list:
    A, B = {r[key]: r for r in a}, {r[key]: r for r in b}
    out = []
    for k in sorted(A.keys() | B.keys(), key=str):
        ra, rb = A.get(k), B.get(k)
        if ra == rb:
            continue
        if ra is None or rb is None:
            out.append({"part": part, "key": k, "field": None, "before": ra, "after": rb})
            continue
        out += [{"part": part, "key": k, "field": f, "before": ra.get(f), "after": rb.get(f)}
                for f in sorted(ra.keys() | rb.keys()) if ra.get(f) != rb.get(f)]
    return out


def diff(before: dict, after: dict) -> list:
    """Field by field: [{part, key (date / event id / settings key), field, before, after}] (field None =
    a whole row added or removed)."""
    out = []
    if "thresholds" in before:
        out += _rows_diff("thresholds", before["thresholds"], after["thresholds"], "date")
    if "events" in before:
        out += _rows_diff("events", before["events"], after["events"], "id")
    if "prefs" in before:
        out += [{"part": "prefs", "key": k, "field": None, "before": before["prefs"].get(k),
                 "after": after["prefs"].get(k)}
                for k in sorted(after["prefs"]) if before["prefs"].get(k) != after["prefs"].get(k)]
    return out


def check_changes(changes: Any) -> set:
    """The parts the changes touch; WriteError when the list is malformed."""
    if not isinstance(changes, list) or not changes:
        raise _bad(_("changes 要是一個非空的列表"))
    if len(changes) > MAX_CHANGES:
        raise _bad(_("一次最多 {n} 個改動", n=MAX_CHANGES))
    parts = set()
    for ch in changes:
        if not isinstance(ch, dict) or ch.get("target") not in TARGETS:
            raise _bad(_("每個改動要有 target：{t}", t="／".join(TARGETS)))
        parts.add(PARTS[ch["target"]])
    return parts


# ---------------------------------------------------------------------------- preview / apply / undo

def _tenant() -> str:
    from backend import tenancy
    return tenancy.current().id


def _sweep() -> None:
    now = time.monotonic()
    for k in [k for k, v in _PREVIEWS.items() if v["exp"] <= now]:
        _PREVIEWS.pop(k, None)


async def preview(db, changes: Any, token_id: Optional[int]) -> dict:
    parts = check_changes(changes)
    before = await state(db, parts)
    after = compute(before, changes)
    d = diff(before, after)
    _sweep()
    pid = None
    if d:
        pid = "pv_" + secrets.token_urlsafe(12)
        _PREVIEWS[pid] = {"tenant": _tenant(), "token_id": token_id, "parts": sorted(parts), "before": before,
                          "after": after, "diff": d, "changes": changes, "exp": time.monotonic() + PREVIEW_TTL_S}
    return {"preview_id": pid, "diff": d, "expires_in_s": PREVIEW_TTL_S if pid else None,
            "nothing_to_change": not d}


async def _write(db, after: dict) -> None:
    """Write the parts of `after`: plan.json (thresholds / events) and the 課表偏好 keys."""
    from starlette.concurrency import run_in_threadpool

    from backend.engine import planning as P
    if "thresholds" in after or "events" in after:
        def save():
            plan = P.Plan.load()
            if "thresholds" in after:
                plan.thresholds = [P.Threshold(**r) for r in after["thresholds"]]
            if "events" in after:
                plan.events = [P.Event(**r) for r in after["events"]]
            plan.save()
        await run_in_threadpool(save)
    if "prefs" in after:
        from backend.settings.repository import SettingsRepository
        repo = SettingsRepository(db)
        for k, v in after["prefs"].items():
            await repo.set(k, v)
    await db.commit()
    from backend.api.plan import _notify
    try:
        _notify("thresholds" in after)      # the charts' datasets, a new CP's re-zoned targets (as the page)
    except Exception:                       # noqa: BLE001 — nothing built yet: nothing to drop
        pass


async def _log(db) -> list:
    from backend.settings.repository import SettingsRepository
    return list(await SettingsRepository(db).get(LOG_KEY) or [])


async def _save_log(db, log: list) -> None:
    from backend.settings.repository import SettingsRepository
    await SettingsRepository(db).set(LOG_KEY, log[-LOG_KEEP:])
    await db.commit()


def _now() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


async def apply(db, preview_id: Any, token_id: Optional[int], token_name: Optional[str]) -> dict:
    pv = _PREVIEWS.get(preview_id) if isinstance(preview_id, str) else None
    if pv is None or pv["exp"] <= time.monotonic() or pv["tenant"] != _tenant() or pv["token_id"] != token_id:
        raise WriteError(404, "NO_PREVIEW", _("沒有這個預覽（過期、已套用，或不是這個 token 做的）：重新預覽"))
    now_state = await state(db, set(pv["parts"]))
    if fingerprint(now_state) != fingerprint(pv["before"]):
        _PREVIEWS.pop(preview_id, None)
        raise WriteError(409, "CHANGED", _("預覽之後資料已經變了：重新預覽"))
    await _write(db, pv["after"])
    _PREVIEWS.pop(preview_id, None)
    log = await _log(db)
    entry = {"id": (log[-1]["id"] + 1) if log else 1, "at": _now(), "by": token_name, "kind": "apply",
             "diff": pv["diff"], "before": pv["before"], "after": pv["after"], "undone_at": None}
    await _save_log(db, log + [entry])
    return {"applied": True, "change_id": entry["id"], "diff": pv["diff"]}


async def undo(db, change_id: Any, token_name: Optional[str]) -> dict:
    """Put back the `before` of write `change_id` (default: the newest not undone)."""
    log = await _log(db)
    todo = [e for e in log if e.get("kind") == "apply" and not e.get("undone_at")]
    if change_id is not None:
        todo = [e for e in todo if e["id"] == change_id]
    if not todo:
        raise WriteError(404, "NOTHING_TO_UNDO", _("沒有可以復原的寫入"))
    e = todo[-1]
    now_state = await state(db, set(e["after"]))
    if fingerprint(now_state) != fingerprint(e["after"]):
        raise WriteError(409, "CHANGED", _("寫入之後資料又被改過，不能自動復原：照 before 自己改回去"))
    await _write(db, e["before"])
    at = _now()
    for x in log:
        if x["id"] == e["id"]:
            x["undone_at"] = at
    log.append({"id": log[-1]["id"] + 1, "at": at, "by": token_name, "kind": "undo", "undoes": e["id"],
                "diff": diff(e["after"], e["before"]), "before": e["after"], "after": e["before"], "undone_at": None})
    await _save_log(db, log)
    return {"undone": e["id"], "diff": log[-1]["diff"]}


async def recent(db, n: int = 10, full: bool = False) -> list:
    """The newest writes, newest first; `full` keeps before / after (the debug API's log), else the diff
    only (the settings page)."""
    out = []
    for e in reversed((await _log(db))[-n:]):
        out.append(e if full else {k: v for k, v in e.items() if k not in ("before", "after")})
    return out
