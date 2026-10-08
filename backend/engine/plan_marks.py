"""
課表標記 (SP-318): which sessions the automatic run changed, which are the user's own and
which nobody touched — one of three states per session, shown on the 課表 page and the 總覽
(both read GET /overview/plan/calendar, api/plan_sessions._sessions_body):

  auto  changed by the automatic run (engine/plan_auto.py): an adapt rule (engine/adapt.py:
        missed_easy / missed_quality / missed_long / overhard / rpe_hard / fatigue), a CP change
        (`cp`, power targets re-zoned) or a re-plan from the latest data (`plan`: the generator's
        new output, no rule) — with the rule, its reason and before → after
  user  the user's own: `edited` (an edit, a drag, a 交換, a session they added, a 復原 — 復原
        pins the restored session as theirs, `restored`). Automation never changes what these
        sessions are (reconcile rule 3); the user's edit wins over an earlier auto change. One
        exception, said in the marker (`cp`): a CP change rescales their absolute watts too
        (plan_auto.rescale_sessions), because the watch takes watts, not % CP
  none  untouched

Derived at read time from plan_change_log (`applied` rows; `restore` rows for the 復原 flag): no
second copy is stored, so the marker can never disagree with the 自動調整紀錄. An undone row is
no longer `applied`, so 復原 takes its marker away.

How long / where a marker shows (推估, kept short for SP-305's 「few hints」):
  * only on sessions still ahead — active, today or later; done / missed / past sessions carry
    none (their own done / missed look says what happened);
  * ↻ only inside the push window (today … today + plan.auto.push_days − 1): that is what reaches
    the watch, and it keeps a CP / threshold change from marking every session for a week. ✎ is
    about who owns the session, so it shows everywhere ahead;
  * only changes from the last MARK_DAYS (7) days: the adapt rules act on the current week, so
    after a week a change is simply the plan; older ones stay in the change log;
  * net change only: before = the first `before` of the chain of changes in that window, after =
    the session now. A change undone by a later run (moved, then moved back) shows nothing; an
    item that only moved the TSS (the generator's rate) is left out of the chain altogether, so it
    neither marks nor relabels. A CP change always marks (the watts on the watch changed even when
    no text did). The rule / reason shown is the latest adapt rule whose change is still there
    (else a re-plan's, else the CP change's); a CP change in the same run is added as `cp`.
The marker is never pushed to COROS (plan_store.push_dict does not carry it): the watch gets the
session as it is now, which is the marker's `after`.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Optional

from sqlalchemy import select

from backend.db.models import PlanChangeLog

log = logging.getLogger(__name__)

MARK_DAYS = 7               # 推估: a change is news for a week (the adapt rules act on the current week)
# the fields a marker compares (plan_auto.CMP without tss: TSS follows minutes / the generator's rate)
SHOWN = ("day", "kind", "title", "minutes", "target", "detail", "terrain", "protocol")
CP_RULE = "cp"
PLAN_RULE = "plan"          # a change with no adapt rule: the generator re-planned from the latest data
NOTICE_KIND = "notice"
SAME_RUN_S = 600            # a run's own row and its CP row (_log_cp, after the push) are this close at most


def _norm(v):
    return None if v in (None, "") else v


def _none() -> dict:
    return {"state": "none"}


def of(marks: dict, uid: str) -> dict:
    """The session's marker ({"state": "none"} when there is none)."""
    return marks.get(uid) or _none()


def window_end(today: str, push_days) -> str:
    """The last day of the push window (plan_auto.push_window's `end`)."""
    try:
        n = max(1, int(push_days or 7))
    except (TypeError, ValueError):
        n = 7
    return (dt.date.fromisoformat(today) + dt.timedelta(days=n - 1)).isoformat()


def _rule(i: dict) -> str:
    return i.get("rule") or PLAN_RULE


def _when(e: dict) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(str(e.get("at") or "").rstrip("Z"))
    except ValueError:
        return None


def _same_run(a: dict, b: dict) -> bool:
    ta, tb = _when(a), _when(b)
    return ta is not None and tb is not None and abs((tb - ta).total_seconds()) <= SAME_RUN_S


def _counts(i: dict) -> bool:
    """A chain item that says something: a change of a shown field, a CP re-zone, a rule's addition."""
    if i.get("action") == "added":
        return bool(i.get("rule"))
    if i.get("action") != "changed":
        return False
    return _rule(i) == CP_RULE or any(k in SHOWN for k in (i.get("before") or {}))


def _user(s: dict, restored: set, chain: list[tuple[dict, dict]]) -> dict:
    out = {"state": "user"}
    if s["uid"] in restored:
        out["restored"] = True
    cp = [i for _e, i in chain if i.get("action") == "changed" and _rule(i) == CP_RULE]
    if cp:
        out["cp"] = cp[-1].get("reason") or ""
    return out


def _auto(s: dict, chain: list[tuple[dict, dict]]) -> Optional[dict]:
    """The auto marker from the session's chain of (entry, item), oldest first; None = no net change."""
    chain = [(e, i) for e, i in chain if _counts(i)]
    if not chain:
        return None
    orig: dict = {}            # key -> (value, the (entry, item) it came from)
    added = None
    for e, i in chain:
        if i.get("action") == "added":
            added = (e, i)
            continue
        for k, v in (i.get("before") or {}).items():
            if k not in SHOWN:
                continue
            if k not in orig:
                orig[k] = (v, (e, i))
            elif _rule(i) == CP_RULE and _rule(orig[k][1][1]) != CP_RULE and _same_run(orig[k][1][0], e):
                # the run rescaled the stored plan before diffing, so its own item's before already
                # has the new watts: the CP row's before has the original ones
                orig[k] = (v, (e, i))
    diff = [{"key": k, "before": orig[k][0], "after": s.get(k)} for k in SHOWN
            if k in orig and _norm(orig[k][0]) != _norm(s.get(k))]
    keys = {d["key"] for d in diff}
    rules = list(dict.fromkeys(_rule(i) for _e, i in chain))
    if not diff and added is None and CP_RULE not in rules:
        return None
    hit = [(e, i) for e, i in chain if i.get("action") == "changed" and keys & set(i.get("before") or {})]
    cps = [(e, i) for e, i in chain if _rule(i) == CP_RULE]
    pick = ([x for x in hit if _rule(x[1]) not in (PLAN_RULE, CP_RULE)] or
            [x for x in hit if _rule(x[1]) == PLAN_RULE] or cps or ([added] if added else []) or hit or chain)[-1]
    e, last = pick
    out = {"state": "auto", "rule": _rule(last), "rules": rules, "reason": last.get("reason") or "",
           "diff": diff, "entry": e["id"], "at": e.get("at")}
    if cps and _rule(last) != CP_RULE:
        out["cp"] = cps[-1][1].get("reason") or ""
    if added is not None:
        out["added"] = True
    return out


def marks(sessions: list[dict], entries: list[dict], today: str, window_end: Optional[str] = None) -> dict:
    """uid -> marker for the sessions that have one (auto / user); `entries`: change-log rows
    as {id, status, at, items, after_uids}, any order (sorted by id here); `window_end`: the
    push window's last day (↻ only up to it; None = no limit). See the module doc."""
    entries = sorted(entries, key=lambda e: e["id"])
    chains: dict = {}
    restored: set = set()
    for e in entries:
        if e["status"] == "applied":
            for i in e.get("items") or []:
                if isinstance(i, dict) and i.get("uid"):
                    chains.setdefault(i["uid"], []).append((e, i))
        elif e["status"] == "restore":
            restored |= set(e.get("after_uids") or [])
    out = {}
    for s in sessions:
        if s.get("kind") == NOTICE_KIND or s.get("state") != "active" or (s.get("day") or "") < today:
            continue
        chain = chains.get(s["uid"]) or []
        if s.get("edited"):
            out[s["uid"]] = _user(s, restored, chain)
            continue
        if window_end and (s.get("day") or "") > window_end:
            continue
        m = _auto(s, chain)
        if m is not None:
            out[s["uid"]] = m
    return out


def _j(x, d):
    try:
        v = json.loads(x) if x else d
    except ValueError:
        return d
    return v if isinstance(v, type(d)) else d


async def load(db, sessions: list[dict], today: str, now: Optional[dt.datetime] = None,
               window_end: Optional[str] = None) -> dict:
    """marks() over the change-log rows of the last MARK_DAYS days (applied + restore)."""
    now = now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)    # created_at is naive UTC
    since = now - dt.timedelta(days=MARK_DAYS)
    T = PlanChangeLog
    res = await db.execute(select(T.id, T.status, T.created_at, T.items_json, T.after_json)
                           .where(T.created_at >= since, T.status.in_(("applied", "restore"))).order_by(T.id))
    entries = []
    for rid, status, at, items, after in res.all():
        entries.append({"id": rid, "status": status, "at": at.isoformat() + "Z" if at else None,
                        "items": _j(items, []) if status == "applied" else [],
                        "after_uids": [x.get("uid") for x in _j(after, []) if isinstance(x, dict)]
                        if status == "restore" else []})
    return marks(sessions, entries, today, window_end)


async def load_safe(db, sessions: list[dict], today: str, push_days=None) -> dict:
    """load() for the page: the push window from plan.auto.push_days; any error -> no markers
    (logged), never a failed 課表 / 總覽."""
    try:
        if push_days is None:
            from backend.settings.repository import SettingsRepository
            push_days = await SettingsRepository(db).get("plan.auto.push_days")
        return await load(db, sessions, today, window_end=window_end(today, push_days))
    except Exception as e:                  # noqa: BLE001 — a marker is never worth a broken page
        log.warning("plan marks failed: %s", type(e).__name__, exc_info=True)
        return {}
