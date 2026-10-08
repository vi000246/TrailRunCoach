"""
課表標記 (SP-318): which sessions the automatic run changed, which are the user's own and
which nobody touched — one of three states per session, shown on the 課表 page and the 總覽
(both read GET /overview/plan/calendar, api/plan_sessions._sessions_body):

  auto  changed by the automatic run (engine/plan_auto.py): an adapt rule (engine/adapt.py:
        missed_easy / missed_quality / missed_long / overhard / rpe_hard / fatigue), a CP change
        (`cp`, power targets re-zoned) or a re-plan from the latest data (`plan`: the generator's
        new output, no rule) — with the rule, its reason and before → after
  user  the user's own: `edited` (an edit, a drag, a 交換, a session they added, a 復原 — 復原
        pins the restored session as theirs, `restored`). Automation never changes these
        (reconcile rule 3); the user's edit wins over an earlier auto change
  none  untouched

Derived at read time from plan_change_log (`applied` rows; `restore` rows for the 復原 flag): no
second copy is stored, so the marker can never disagree with the 自動調整紀錄. An undone row is
no longer `applied`, so 復原 takes its marker away.

How long a marker stays (推估, kept short for SP-305's 「few hints」):
  * only on sessions still ahead — active, today or later; done / missed / past sessions carry
    none (their own done / missed look says what happened);
  * only changes from the last MARK_DAYS (7) days: the adapt rules act on the current week, so
    after a week a change is simply the plan; older ones stay in the change log;
  * net change only: before = the first `before` of the chain of changes in that window, after =
    the session now. A change undone by a later run (moved, then moved back) shows nothing; a
    re-plan that only moved the TSS (the generator's rate) is no marker either. A CP change is
    always one (the watts on the watch changed even when no text did).
The marker is never pushed to COROS (plan_store.push_dict does not carry it): the watch gets the
session as it is now, which is the marker's `after`.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from sqlalchemy import select

from backend.db.models import PlanChangeLog

MARK_DAYS = 7               # 推估: a change is news for a week (the adapt rules act on the current week)
# the fields a marker compares (plan_auto.CMP without tss: TSS follows minutes / the generator's rate)
SHOWN = ("day", "kind", "title", "minutes", "target", "detail", "terrain", "protocol")
CP_RULE = "cp"
PLAN_RULE = "plan"          # a change with no adapt rule: the generator re-planned from the latest data
NOTICE_KIND = "notice"


def _norm(v):
    return None if v in (None, "") else v


def _none() -> dict:
    return {"state": "none"}


def of(marks: dict, uid: str) -> dict:
    """The session's marker ({"state": "none"} when there is none)."""
    return marks.get(uid) or _none()


def _user(s: dict, restored: set) -> dict:
    out = {"state": "user"}
    if s["uid"] in restored:
        out["restored"] = True
    return out


def _auto(s: dict, chain: list[tuple[dict, dict]]) -> Optional[dict]:
    """The auto marker from the session's chain of (entry, item), oldest first; None = no net change."""
    chain = [(e, i) for e, i in chain if i.get("action") == "changed" or (i.get("action") == "added" and i.get("rule"))]
    if not chain:
        return None
    orig: dict = {}
    added = False
    for _e, i in chain:
        if i.get("action") == "added":
            added = True
            continue
        for k, v in (i.get("before") or {}).items():
            if k in SHOWN:
                orig.setdefault(k, v)
    diff = [{"key": k, "before": orig[k], "after": s.get(k)} for k in SHOWN
            if k in orig and _norm(orig[k]) != _norm(s.get(k))]
    e, last = chain[-1]
    rule = last.get("rule") or PLAN_RULE
    if not diff and not added and rule != CP_RULE:
        return None
    out = {"state": "auto", "rule": rule, "rules": list(dict.fromkeys(i.get("rule") or PLAN_RULE for _e, i in chain)),
           "reason": last.get("reason") or "", "diff": diff, "entry": e["id"], "at": e.get("at")}
    if added:
        out["added"] = True
    return out


def marks(sessions: list[dict], entries: list[dict], today: str) -> dict:
    """uid -> marker for the sessions that have one (auto / user); `entries`: change-log rows
    as {id, status, at, items, after_uids}, any order (sorted by id here). See the module doc."""
    entries = sorted(entries, key=lambda e: e["id"])
    chains: dict = {}
    restored: set = set()
    for e in entries:
        if e["status"] == "applied":
            for i in e.get("items") or []:
                if i.get("uid"):
                    chains.setdefault(i["uid"], []).append((e, i))
        elif e["status"] == "restore":
            restored |= set(e.get("after_uids") or [])
    out = {}
    for s in sessions:
        if s.get("kind") == NOTICE_KIND or s.get("state") != "active" or (s.get("day") or "") < today:
            continue
        if s.get("edited"):
            out[s["uid"]] = _user(s, restored)
            continue
        m = _auto(s, chains.get(s["uid"]) or [])
        if m is not None:
            out[s["uid"]] = m
    return out


def _j(x, d):
    try:
        return json.loads(x) if x else d
    except ValueError:
        return d


async def load(db, sessions: list[dict], today: str, now: Optional[dt.datetime] = None) -> dict:
    """marks() over the change-log rows of the last MARK_DAYS days (applied + restore)."""
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)    # created_at is naive UTC
    since = now - dt.timedelta(days=MARK_DAYS)
    res = await db.execute(select(PlanChangeLog).where(PlanChangeLog.created_at >= since,
                                                       PlanChangeLog.status.in_(("applied", "restore")))
                           .order_by(PlanChangeLog.id))
    entries = [{"id": r.id, "status": r.status, "at": r.created_at.isoformat() + "Z" if r.created_at else None,
                "items": _j(r.items_json, []),
                "after_uids": [x.get("uid") for x in _j(r.after_json, []) if isinstance(x, dict)]}
               for r in res.scalars().all()]
    return marks(sessions, entries, today)
