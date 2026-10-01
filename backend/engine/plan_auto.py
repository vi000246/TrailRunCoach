"""
自動調整課表: after a sync that imported ≥ 1 new activity (sync/runner.py),
reconcile done / missed -> adapt (engine/adapt.py, inside
plan_store.reconcile_with_adapt) -> regenerate the upcoming weeks -> push the
next `plan.auto.push_days` days to COROS. The user only reviews: edited /
custom sessions and 不排課日期 are never changed by this (reconcile rules).

Safety
  * one run at a time: the plan writer lock (api/plan_sessions._wlock), shared
    with the page's edits / pushes; a run that finds the same data stamp as the
    last one does nothing (no change, no push).
  * a push failure never breaks the sync: the run is a separate task, and the
    push error is stored in the change log.
  * big changes are held as a proposal (plan.auto.confirm_big): the stored plan
    only takes the done / missed part, the watch keeps what was pushed, and the
    user approves / rejects on the overview / 課表 page. With
    plan.auto.notify = watch a 1-minute「課表待確認」 workout is pushed too.
    Big (thresholds 自組):
      - a week's planned TSS + > 20 % vs the stored (= last pushed) version
        (reductions are the safe direction and apply on their own)
      - a long / quality / test removed within 14 days before an A race
      - a phase change since the last run
      - > 3 sessions changed in the push window that are not pure reductions
  * every run that changes something writes a plan_change_log row (Traditional
    Chinese reasons) with the affected sessions before / after; 復原 restores the
    before state (pinned as the user's own, so the next run doesn't redo it)
    and re-pushes.

CP change (docs/research/zones-and-thresholds.md §3.2: power targets are %
CP, and COROS running workouts take absolute watts only): a run also starts
when the CP in effect (week_plan thresholds) differs from the one the last run
saw (state["cp"]) — api/plan.py starts one after every threshold edit
(after_thresholds). The upcoming active sessions' watt numbers (target /
detail) are recomputed (rescale_sessions; regenerated sessions get the new
text anyway), the pushed ones go out of date by fingerprint and are re-pushed
through push_window — also those already on the watch beyond the window —
and one change-log row says 「CP 204 → 210 W：未來 N 堂課的功率目標已更新並重新
推送」. With plan.auto.push off the stored plan and the log are updated, the
row says 「…，待推送」 and nothing is sent; each item says 已重新推送 / 待推送 /
只在 app. plan.auto.enabled off: nothing (the next enabled run catches up).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
from typing import Callable, Optional

from sqlalchemy import select

from backend.db.models import PlanChangeLog, PlanSession

log = logging.getLogger(__name__)

# 自組 thresholds for "big" (labelled in the UI hover text too)
BIG_TSS_UP = 0.20           # a week's planned TSS + > 20 %
RACE_GUARD_DAYS = 14        # removing a long / quality / test this close to an A race
MAX_CHANGED = 3             # > 3 non-reducing changes in the push window
BIG_TEXT = {"tss": "單週計畫 TSS 比上次推送 +20% 以上（自組）",
            "race": "A 賽前 14 天內拿掉長跑／強度課／測試（自組）",
            "phase": "訓練周期改變（自組）",
            "many": "推送範圍內超過 3 堂課改變、而且不是單純減量（自組）"}
HARD_LONG = ("long", "quality", "test")
CMP = ("day", "kind", "title", "minutes", "target", "detail", "tss", "terrain", "protocol")
NOTICE_KIND = "notice"
REJECTED_KEEP = 20

SESSION_FACTORY: Optional[Callable] = None      # tests replace; default AsyncSessionLocal
_TASKS: set = set()


# ---------------------------------------------------------------------------
# pure parts
# ---------------------------------------------------------------------------

def _md(day: Optional[str]) -> str:
    if not day:
        return "—"
    d = dt.date.fromisoformat(day)
    return f"{d.month}/{d.day}"


def diff(stored: list[dict], new: list[dict], changes: list[dict], adjustments: list[dict]) -> list[dict]:
    """Per-session differences between the stored plan and the new one
    (notice rows ignored), with reconcile's / adapt's reasons."""
    why = {c["uid"]: c.get("reason") for c in changes if c.get("reason")}
    rule = {c["uid"]: c.get("adapt") for c in changes if c.get("adapt")}
    a = {s["uid"]: s for s in stored if s["kind"] != NOTICE_KIND}
    b = {s["uid"]: s for s in new if s["kind"] != NOTICE_KIND}
    out = []

    def item(action, s, before=None, reason=None):
        it = {"action": action, "uid": s["uid"], "day": s.get("day"), "kind": s.get("kind"),
              "title": s.get("title"), "minutes": s.get("minutes"), "tss": s.get("tss"),
              "reason": reason or why.get(s["uid"]) or "", "rule": rule.get(s["uid"])}
        if before is not None:
            it["before"] = before
        out.append(it)

    for uid, s in b.items():
        o = a.get(uid)
        if o is None:
            if s["state"] == "active":
                item("added", s)
            elif s["state"] == "done":
                item("done", s, reason="已完成")
            continue
        if o["state"] != s["state"]:
            if s["state"] == "done":
                item("done", s, reason="已完成")
            elif s["state"] == "missed":
                item("missed", s, reason=why.get(uid) or "沒有對應的活動")
            elif s["state"] in ("superseded", "deleted"):
                item("removed", s, before={k: o.get(k) for k in CMP})
            else:
                item("changed", s, before={"state": o["state"]})
            continue
        if s["state"] == "active":
            before = {k: o.get(k) for k in CMP if o.get(k) != s.get(k)}
            if before:
                item("changed", s, before=before)
        elif (o.get("note") or "") != (s.get("note") or ""):
            item("note", s, reason=s.get("note") or "")
    for uid, o in a.items():
        if uid not in b and o["state"] == "active":
            item("removed", o, before={k: o.get(k) for k in CMP})
    if out:
        # adaptations with no session of their own (a make-up that never got stored)
        seen = {(i.get("day"), i.get("kind")) for i in out}
        for x in adjustments:
            if x["action"] == "removed" and (x.get("day"), x.get("kind")) not in seen \
                    and not any(i["reason"] == x["reason"] for i in out):
                out.append({"action": "dropped", "uid": None, "day": x.get("day"), "kind": x.get("kind"),
                            "title": x.get("title"), "minutes": None, "tss": None, "reason": x["reason"],
                            "rule": x["rule"]})
    return sorted(out, key=lambda i: (i.get("day") or "9999", i["action"]))


def forward(items: list[dict], today: str) -> list[dict]:
    return [i for i in items if i["action"] in ("added", "removed", "changed") and (i.get("day") or "9999") >= today]


def is_reduction(i: dict) -> bool:
    if i["action"] in ("removed", "dropped"):
        return True
    if i["action"] == "added":
        return not (i.get("tss") or 0) > 0
    b = i.get("before") or {}
    up = lambda k: k in b and (i.get(k) or 0) > (b.get(k) or 0)
    return not up("tss") and not up("minutes")


def _week_tss(ss: list[dict], ws: str) -> tuple[float, int]:
    from backend.engine import plan_store as PS
    we = (dt.date.fromisoformat(ws) + dt.timedelta(days=6)).isoformat()
    xs = [s for s in ss if s.get("day") and ws <= s["day"] <= we and s["state"] in ("active", "done")
          and s["kind"] != NOTICE_KIND]
    return sum(PS.session_tss(s) for s in xs), len(xs)


def classify(stored: list[dict], new: list[dict], items: list[dict], today: str, window_end: str,
             phase: Optional[str], last_phase: Optional[str], days_to_race: Optional[int],
             reentry_weeks: Optional[set] = None) -> list[dict]:
    """Why this run's change is big (empty = apply on its own). Weeks of a
    停訓後恢復期 and the week right after it (`reentry_weeks`) are exempt from
    the TSS rule: Daniels' 50 → 75 → 100 % steps are planned (detraining.md
    §6.5, 推估)."""
    from backend.engine.reconcile import monday_of
    out = []
    fw = forward(items, today)
    weeks = sorted({monday_of(today), monday_of(window_end)})
    for ws in weeks:
        if reentry_weeks and ws in reentry_weeks:
            continue
        before, n0 = _week_tss(stored, ws)
        after, _ = _week_tss(new, ws)
        if n0 and before > 0 and after > before * (1 + BIG_TSS_UP):
            out.append({"rule": "tss", "text": f"{_md(ws)} 那週計畫 TSS {before:.0f} → {after:.0f}"
                                               f"（+{(after / before - 1) * 100:.0f}%，> +{BIG_TSS_UP * 100:.0f}%）"})
    if days_to_race is not None and days_to_race >= 0:
        race = (dt.date.fromisoformat(today) + dt.timedelta(days=days_to_race)).isoformat()
        start = (dt.date.fromisoformat(race) - dt.timedelta(days=RACE_GUARD_DAYS)).isoformat()
        for i in fw:
            gone = i["action"] == "removed" or (i["action"] == "changed" and (i.get("before") or {}).get("kind") in HARD_LONG
                                                and i.get("kind") not in HARD_LONG)
            k = (i.get("before") or {}).get("kind") or i.get("kind")
            if gone and k in HARD_LONG and start <= (i.get("day") or "") <= race:
                out.append({"rule": "race", "text": f"A 賽前 {RACE_GUARD_DAYS} 天內拿掉 {_md(i['day'])} {i['title']}"})
    for i in [x for x in items if x["action"] == "dropped" and x.get("kind") in HARD_LONG]:
        if days_to_race is not None and 0 <= days_to_race <= RACE_GUARD_DAYS + 7:
            out.append({"rule": "race", "text": f"A 賽前取消 {_md(i['day'])} {i['title']}"})
    if last_phase and phase and last_phase != phase:
        out.append({"rule": "phase", "text": f"周期 {last_phase} → {phase}"})
    # a week generated for the first time (nothing stored yet) is not a change
    known = {s.get("week_start") for s in stored if s["kind"] != NOTICE_KIND}
    win = [i for i in fw if (i.get("day") or "9999") <= window_end
           and not (i["action"] == "added" and monday_of(i["day"]) not in known)]
    if len(win) > MAX_CHANGED and not all(is_reduction(i) for i in win):
        out.append({"rule": "many", "text": f"未來 {len(win)} 堂課改變（> {MAX_CHANGED}），不全是減量"})
    return out


def fingerprint(items: list[dict]) -> str:
    key = sorted((i["action"], i.get("day") or "", i.get("kind") or "", i.get("title") or "",
                  int(i.get("minutes") or 0), round(float(i.get("tss") or 0))) for i in items)
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:32]


def summary(items: list[dict], held: bool = False) -> str:
    """One line in Traditional Chinese."""
    fw = [i for i in items if i["action"] in ("added", "removed", "changed", "dropped")]
    hist = [i for i in items if i["action"] in ("done", "missed")]
    parts = []
    if hist:
        nd = sum(1 for i in hist if i["action"] == "done")
        nm = len(hist) - nd
        parts.append("、".join(x for x in (f"完成 {nd} 堂" if nd else "", f"沒跑 {nm} 堂" if nm else "") if x))
    if fw:
        parts.append(f"調整 {len(fw)} 堂" + ("（待你確認）" if held else ""))
    for i in items:
        if i["action"] == "note":
            parts.append(i["reason"])
    return "；".join(p for p in parts if p) or "沒有變更"


# ---------------------------------------------------------------------------
# CP change -> power targets (COROS running workouts take absolute watts only)
# ---------------------------------------------------------------------------

# "180–194 W", "< 163 W", "固定功率 153 W"; not W′ / W/kg / kW
_WATTS = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?:(\s*[–-]\s*)(\d+(?:\.\d+)?))?(\s*W)(?![′'/A-Za-z])")


def cp_of(inp: dict) -> Optional[float]:
    """The CP the plan's power targets use (week_plan()['thresholds']['cp'])."""
    v = (inp.get("thresholds") or {}).get("cp")
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def rescale_watts(text: Optional[str], old: float, new: float) -> Optional[str]:
    """Every absolute watt number in `text` × new / old (the target is a % of CP)."""
    if not text or not old or not new:
        return text

    def one(m):
        a = f"{float(m.group(1)) * new / old:.0f}"
        if m.group(3) is None:
            return f"{a}{m.group(4)}"
        return f"{a}{m.group(2)}{float(m.group(3)) * new / old:.0f}{m.group(4)}"
    return _WATTS.sub(one, text)


def _has_power(s: dict, cp: float) -> bool:
    """The session's COROS workout has a power-target step (watts from % CP)."""
    from backend.engine import plan_store as PS
    from backend.sync import coros_workouts as CW
    try:
        steps = CW.session_steps(PS.push_dict(s), CW.Thresholds(cp=cp))
    except Exception:                       # noqa: BLE001 — not pushable: no watts on the watch
        return False
    flat = []
    for st in steps:
        flat.extend(getattr(st, "steps", None) or [st])
    return any(getattr(x, "intensity", None) and x.intensity[0] == "power" for x in flat)


def rescale_sessions(stored: list[dict], old: float, new: float, today: str) -> tuple[list[dict], list[dict]]:
    """CP old -> new: the upcoming active sessions' watt numbers (target /
    detail) recomputed from % CP. Returns (sessions, items) — one item per
    session whose text or COROS power steps change (the change log rows)."""
    out, items = [], []
    for s in stored:
        if s["state"] != "active" or s["kind"] == NOTICE_KIND or (s.get("day") or "") < today:
            out.append(s)
            continue
        t2, d2 = rescale_watts(s.get("target"), old, new), rescale_watts(s.get("detail"), old, new)
        x = {**s, "target": t2 or "", "detail": d2 or ""}
        if t2 != s.get("target") or d2 != s.get("detail") or _has_power(x, new):
            before = {k: s.get(k) for k in ("target", "detail") if s.get(k) != x.get(k)}
            items.append({"action": "changed", "uid": s["uid"], "day": s.get("day"), "kind": s.get("kind"),
                          "title": s.get("title"), "minutes": s.get("minutes"), "tss": s.get("tss"),
                          "reason": f"CP {old:.0f} → {new:.0f} W：功率目標依 % CP 重算", "rule": "cp",
                          "before": before})
        out.append(x)
    return out, sorted(items, key=lambda i: i.get("day") or "9999")


def cp_summary(old: float, new: float, n: int, pushed: bool) -> str:
    return (f"CP {old:.0f} → {new:.0f} W：未來 {n} 堂課的功率目標已更新" +
            ("並重新推送" if pushed else "，待推送"))


def stamp(inp: dict) -> str:
    acts = inp.get("activities") or []
    key = [inp.get("today"), len(acts), sorted(str(a.get("index")) for a in acts),
           inp.get("last_activity")]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:24]


def notice_day(stored: list[dict], acts: list[dict], today: str) -> str:
    """Today if nothing was done today yet, else the next day with an active session (else tomorrow)."""
    if not any(a.get("date") == today for a in acts):
        return today
    nxt = sorted(s["day"] for s in stored if s["state"] == "active" and s.get("day") and s["day"] > today
                 and s["kind"] not in ("strength", "heat_passive", NOTICE_KIND))
    return nxt[0] if nxt else (dt.date.fromisoformat(today) + dt.timedelta(days=1)).isoformat()


def notice_session(day: str, items: list[dict], big: list[dict]) -> dict:
    from backend.engine import reconcile as R
    fw = forward(items, day) or [i for i in items if i["action"] == "dropped"]
    lines = [f"{_md(i['day'])} {i['title']}：{i['reason'] or i['action']}" for i in fw[:3]]
    detail = "；".join([b["text"] for b in big] + lines + ["到總覽頁按同意／拒絕"])
    return {"uid": R.new_uid(), "week_start": R.monday_of(day), "gen_key": None, "day": day, "kind": NOTICE_KIND,
            "title": "⚠ 課表待確認", "minutes": 1, "target": "", "detail": detail[:200], "source": "plan_auto",
            "tss": 0.0, "origin": "custom", "edited": False, "provisional": False, "state": "active",
            "done_by": None, "note": None, "terrain": None, "distance_km": None, "climb_m": None, "protocol": None}


def history_only(stored: list[dict], new: list[dict]) -> list[dict]:
    """The stored plan with only the done / missed / note part of `new` (a held run)."""
    nb = {s["uid"]: s for s in new}
    out = []
    for s in stored:
        n = nb.get(s["uid"])
        if n is not None and n["state"] in ("done", "missed") and (n["state"] != s["state"] or n.get("note") != s.get("note")):
            out.append(n)
        else:
            out.append(s)
    have = {s["uid"] for s in stored}
    out += [s for s in new if s["uid"] not in have and s["state"] == "done"]
    return out


# ---------------------------------------------------------------------------
# storage helpers
# ---------------------------------------------------------------------------

async def settings(db) -> dict:
    from backend.settings.repository import AUTO_KEYS, SettingsRepository
    repo = SettingsRepository(db)
    out = {k.split(".")[-1]: await repo.get(k) for k in AUTO_KEYS}
    out["state"] = await repo.get("plan.auto.state") or {}
    return out


async def _set_state(db, state: dict) -> None:
    from backend.settings.repository import SettingsRepository
    await SettingsRepository(db).set("plan.auto.state", state)
    await db.commit()


def entry_dict(r: PlanChangeLog) -> dict:
    j = lambda x, d: json.loads(x) if x else d
    return {"id": r.id, "at": r.created_at.isoformat() + "Z" if r.created_at else None, "trigger": r.trigger,
            "status": r.status, "summary": r.summary, "items": j(r.items_json, []), "big": j(r.big_json, None),
            "push": j(r.push_json, None), "notice_uid": r.notice_uid, "ref_id": r.ref_id,
            "can_undo": r.status == "applied" and bool(j(r.before_json, []) or j(r.after_json, []))}


async def _add_entry(db, **kw) -> PlanChangeLog:
    for k in ("items", "before", "after", "big", "push"):
        if k in kw:
            v = kw.pop(k)
            kw[f"{k}_json"] = None if v is None else json.dumps(v, ensure_ascii=False, default=str)
    r = PlanChangeLog(athlete_id=1, created_at=dt.datetime.utcnow(), **kw)
    db.add(r)
    await db.commit()
    return r


async def entries(db, limit: int = 10) -> list[dict]:
    res = await db.execute(select(PlanChangeLog).order_by(PlanChangeLog.id.desc()).limit(limit))
    return [entry_dict(r) for r in res.scalars().all()]


async def pending(db) -> Optional[PlanChangeLog]:
    res = await db.execute(select(PlanChangeLog).where(PlanChangeLog.status == "pending")
                           .order_by(PlanChangeLog.id.desc()))
    return res.scalars().first()


async def _remove_notice(db, uid: Optional[str], errors: list) -> None:
    """Take a 課表待確認 workout off the store and the watch (explicitly: an
    automatic stale removal would keep a past-day one)."""
    if not uid:
        return
    from backend.sync import coros_workouts as CW
    res = await db.execute(select(PlanSession).where(PlanSession.uid == uid))
    r = res.scalar_one_or_none()
    if r is not None:
        await db.delete(r)
        await db.commit()
    try:
        rows = await CW.rows_by_key(db, 1, [uid])
        if rows:
            await CW.remove_keys(db, [uid])
    except Exception as e:                  # noqa: BLE001 — logged, never fatal
        errors.append(f"移除課表待確認失敗：{type(e).__name__}: {e}"[:300])


async def _resolve_pending(db, status: str, errors: list) -> Optional[PlanChangeLog]:
    p = await pending(db)
    if p is not None:
        p.status = status
        await db.commit()
        await _remove_notice(db, p.notice_uid, errors)
    return p


async def _stale_notices(db, today: str, keep: Optional[str], errors: list) -> None:
    res = await db.execute(select(PlanSession).where(PlanSession.kind == NOTICE_KIND))
    for r in res.scalars().all():
        if r.uid != keep and (r.day or "") < today:
            await _remove_notice(db, r.uid, errors)


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------

async def push_window(db, new: list[dict], inp: dict, today: str, days: int,
                      extra_uids: Optional[set] = None) -> dict:
    """Push the active sessions in [today, today + days − 1] (later ones stay in
    the app); remove pushed sessions that left the plan / were missed. Nothing
    is sent when every session in the window is already up to date. Errors are
    returned, never raised. `extra_uids`: sessions after the window that are
    on the watch already and must be re-sent too (a CP change: their watts)."""
    from backend.api import plan_sessions as API
    from backend.engine import plan_store as PS
    from backend.sync import coros_workouts as CW
    end = (dt.date.fromisoformat(today) + dt.timedelta(days=max(1, days) - 1)).isoformat()
    try:
        rows = await CW.all_rows(db)
        bl = PS.blocked_map(inp)
        live = {s["uid"] for s in new if s["state"] in ("active", "done", "missed")}
        stale = [k for k, r in rows.items() if k not in live and (r.day is None or r.day >= today)]
        stale += [s["uid"] for s in API._on_blocked(new, bl, today) if s["uid"] in rows]
        missed = [s["uid"] for s in new if s["state"] == "missed" and s["uid"] in rows]
        todo = [s for s in API._in_range(new, today, end, bl) if s["kind"] != NOTICE_KIND]
        if extra_uids:
            have = {s["uid"] for s in todo}
            todo += [s for s in new if s["uid"] in extra_uids and s["uid"] in rows and s["uid"] not in have
                     and s["state"] == "active" and (s.get("day") or "") >= today and s.get("day") not in bl]
        th = inp.get("thresholds") or {}
        need = [s for s in todo if CW.status_of(PS.push_dict(s), th, rows.get(s["uid"]), today)["status"]
                in ("not_pushed", "outdated", "failed")]
        if not need and not stale and not missed:
            return {"status": "unchanged", "start": today, "end": end, "sent": 0, "removed": 0}
        res = await CW.push_sessions(db, [PS.push_dict(s) for s in todo], th, today,
                                     stale_keys=stale, missed_keys=missed)
        sent = sum(1 for x in res.get("sessions") or [] if x.get("changed"))
        failed = [x for x in res.get("sessions") or [] if x.get("status") == "failed"]
        return {"status": "partial" if failed else "ok", "start": today, "end": end, "sent": sent,
                "removed": sum(1 for x in res.get("removed") or [] if x.get("status") == "removed"),
                "error": "；".join(str(x.get("error")) for x in failed)[:500] or None}
    except Exception as e:                  # noqa: BLE001 — the push never breaks the run / the sync
        log.warning("auto plan push failed: %s", type(e).__name__)
        try:
            await db.rollback()
        except Exception:                   # noqa: BLE001
            pass
        return {"status": "failed", "start": today, "end": end, "error": f"{type(e).__name__}: {e}"[:500]}


async def _push_notice(db, s: dict, inp: dict, today: str) -> dict:
    from backend.engine import plan_store as PS
    from backend.sync import coros_workouts as CW
    try:
        res = await CW.push_sessions(db, [PS.push_dict(s)], inp.get("thresholds") or {}, today)
        st = (res.get("sessions") or [{}])[0]
        return {"status": "ok" if st.get("status") in ("pushed", "updated") else "failed",
                "error": st.get("error"), "notice": True}
    except Exception as e:                  # noqa: BLE001
        try:
            await db.rollback()
        except Exception:                   # noqa: BLE001
            pass
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500], "notice": True}


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def _without(ss: list[dict], uids: set) -> list[dict]:
    return [s for s in ss if s["uid"] not in uids]


def _affected(stored: list[dict], new: list[dict], items: list[dict]) -> tuple[list[dict], list[dict]]:
    uids = {i["uid"] for i in items if i.get("uid") and i["action"] in ("added", "removed", "changed")}
    return ([s for s in stored if s["uid"] in uids], [s for s in new if s["uid"] in uids])


def reentry_weeks(inp: dict) -> set:
    """Week starts in a 停訓後恢復期 (mode reentry) plus the week after it ends."""
    from backend.engine.reconcile import monday_of
    out = {w.get("start") for w in inp.get("weeks") or [] if w.get("mode") == "reentry"}
    rp = (inp.get("cur") or {}).get("reentry")
    if (inp.get("cur") or {}).get("mode") == "reentry":
        out.add(((inp.get("cur") or {}).get("week") or {}).get("start"))
    if rp and rp.get("end"):
        out.add(monday_of(rp["end"]))
        out.add(monday_of((dt.date.fromisoformat(rp["end"]) + dt.timedelta(days=7)).isoformat()))
    return {x for x in out if x}


def state_changes(inp: dict, state: dict) -> list[str]:
    """Log lines for a Zone 5 state change and a new re-entry block (both are
    plan rules, not sessions); updates `state` in place (keys z5, reentry)."""
    out = []
    cur = inp.get("cur") or {}
    z5 = (cur.get("quality_gate") or {}).get("z5") or {}
    key = f"{z5.get('state')}|{z5.get('since')}|{z5.get('path')}" if z5 else None
    if z5 and key != state.get("z5"):
        old = (state.get("z5") or "").split("|")[0]
        from backend.engine.base_check import STATE_LABEL
        out.append(f"Zone 5：{STATE_LABEL.get(old, '—') if old else '—'} → {z5.get('text') or z5.get('label')}")
        state["z5"] = key
    rp = cur.get("reentry")
    rkey = f"{rp['return']}|{rp['days']}" if rp else None
    if rp and rkey != state.get("reentry"):
        out.append(f"恢復期：{rp['text']}" + ("（不排課日期，事前排好）" if rp.get("planned") else "（從活動資料偵測）"))
        state["reentry"] = rkey
    return out


async def _log_states(db, inp: dict, state: dict, trigger: str) -> None:
    for line in state_changes(inp, state):
        await _add_entry(db, trigger=trigger, status="applied", summary=line, items=[], push=None)


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def _log_cp(db, old: float, new: float, items: list[dict], cfg: dict, out: dict, trigger: str,
                  th: dict, today: str) -> dict:
    """The change-log row of a CP change: 「CP 204 → 210 W：未來 N 堂課的功率目標已更新
    並重新推送」, or 「…，待推送」 when plan.auto.push is off (or the run is held / the
    push failed). Per session: 已重新推送 / 待推送 (on the watch with old watts) /
    只在 app (not on the watch yet: it is sent when it enters the push window)."""
    from backend.engine import plan_store as PS
    from backend.sync import coros_workouts as CW
    cur = {s["uid"]: s for s in await PS.load(db)}
    try:
        rows = await CW.all_rows(db)
    except Exception:                       # noqa: BLE001
        rows = {}
    marked = []
    for i in items:
        s = cur.get(i["uid"])
        if i["uid"] in rows and s is not None:
            st = CW.status_of(PS.push_dict(s), th, rows[i["uid"]], today)["status"]
            label = "已重新推送" if st == "pushed" else "待推送"
        else:
            label = "只在 app"
        marked.append({**i, "push": label})
    pending_n = sum(1 for i in marked if i["push"] == "待推送")
    pushed = bool(cfg.get("push")) and out.get("status") in ("applied", "noop") and not pending_n \
        and (out.get("push") or {}).get("status") != "failed"
    push = out.get("push") if cfg.get("push") else {"status": "off"}
    await _add_entry(db, trigger="cp_change" if trigger.startswith("cp_change") else f"cp_change:{trigger}"[:40],
                     status="applied",
                     summary=cp_summary(old, new, len(marked), pushed), items=marked,
                     push={**(push or {"status": "held"}), "pending": pending_n})
    return {"old": old, "new": new, "n": len(marked), "pushed": pushed, "pending": pending_n,
            "status": "已重新推送" if pushed else "待推送"}


async def run(db, trigger: str = "sync", force: bool = False, approve_id: Optional[int] = None) -> dict:
    """One automatic run (see the module doc). `force`: ignore the data stamp
    and the big-change hold (approve, the page's 立即重算)."""
    from backend.api import plan_sessions as API
    async with API._wlock():
        return await _run(db, trigger, force, approve_id)


async def _run(db, trigger: str, force: bool, approve_id: Optional[int]) -> dict:
    from backend.api import plan_sessions as API
    from backend.engine import plan_store as PS
    cfg = await settings(db)
    if not cfg["enabled"] and not force:
        return {"status": "disabled"}
    inp = await API._inputs(db)
    today = API._today(inp)
    state = dict(cfg["state"] or {})
    stp = stamp(inp)
    # a new CP in effect (a test applied, a plan row edited): the power targets are
    # % CP, but COROS running workouts take absolute watts — recompute and re-push
    cp_new, cp_old = cp_of(inp), _f(state.get("cp"))
    cp_changed = bool(cp_new and cp_old and round(cp_new) != round(cp_old))
    if not force and state.get("stamp") == stp and not cp_changed:
        if cp_new and cp_old is None:
            await _set_state(db, {**state, "cp": cp_new})        # the baseline, no event
        return {"status": "noop", "reason": "沒有新的活動"}
    errors: list = []
    p = await pending(db)
    await _stale_notices(db, today, p.notice_uid if p is not None else None, errors)
    stored = await PS.load(db)
    cp_items: list = []
    if cp_changed:
        # the stored plan first, so the regenerated / held plan and the diff start from it
        stored, cp_items = rescale_sessions(stored, cp_old, cp_new, today)
    adj: list = []
    new, changes = PS.reconcile_with_adapt(stored, inp, adjustments=adj)
    items = diff(stored, new, changes, adj)
    days = int(cfg["push_days"] or 7)
    wend = (dt.date.fromisoformat(today) + dt.timedelta(days=days - 1)).isoformat()
    phase = (inp.get("phase") or {}).get("kind")
    big = classify(stored, new, items, today, wend, phase, state.get("phase"), inp.get("days_to_next_a"),
                   reentry_weeks(inp))
    await _log_states(db, inp, state, trigger)
    fw = forward(items, today) + [i for i in items if i["action"] == "dropped"]
    fp = fingerprint(fw) if fw else None
    out: dict = {"status": "noop", "items": items}

    if big and cfg["confirm_big"] and not force:
        if fp in (state.get("rejected") or []):
            await PS.save(db, history_only(stored, new))
            out = {"status": "rejected_before", "reason": "與你拒絕過的提案相同，略過"}
        elif p is not None and p.fingerprint == fp:
            await PS.save(db, history_only(stored, new))
            out = {"status": "pending", "id": p.id}
        else:
            old = await _resolve_pending(db, "superseded", errors)
            gone = {old.notice_uid} if old is not None else set()
            await PS.save(db, _without(history_only(stored, new), gone))
            notice, push = None, None
            if cfg["notify"] == "watch" and cfg["push"]:
                cur = await PS.load(db)
                ns = notice_session(notice_day(cur, inp.get("activities") or [], today), items, big)
                await PS.save(db, cur + [ns])
                notice = ns["uid"]
                push = await _push_notice(db, ns, inp, today)
            before, after = _affected(stored, new, items)
            e = await _add_entry(db, trigger=trigger, status="pending", summary=summary(items, held=True),
                                 items=items, before=before, after=after, big=big, fingerprint=fp,
                                 push={**(push or {"status": "held"}), "errors": errors or None}, notice_uid=notice)
            out = {"status": "pending", "id": e.id}
    else:
        resolved = await _resolve_pending(db, "approved" if approve_id else "superseded", errors) \
            if (p is not None or approve_id) else None
        # no proposal is waiting any more: no 課表待確認 stays in the plan
        # (one still on the watch is removed by the push: it left the plan)
        new = [s for s in new if s["kind"] != NOTICE_KIND]
        await PS.save(db, new)
        push = await push_window(db, new, inp, today, days, {i["uid"] for i in cp_items}) if cfg["push"] \
            else {"status": "off"}
        if errors:
            push = {**push, "errors": errors}
        # a push that only re-sends a CP change is logged by _log_cp (below), not twice
        if items or (push.get("status") not in ("unchanged", "off") and not cp_items):
            before, after = _affected(stored, new, items)
            e = await _add_entry(db, trigger=trigger, status="applied", summary=summary(items),
                                 items=items, before=before, after=after, big=big or None, fingerprint=fp,
                                 push=push, ref_id=resolved.id if resolved is not None and approve_id else None)
            out = {"status": "applied", "id": e.id, "push": push}
        else:
            out = {"status": "noop", "push": push}
    if cp_changed:
        out["cp_change"] = await _log_cp(db, cp_old, cp_new, cp_items, cfg, out, trigger,
                                         inp.get("thresholds") or {}, today)
    if cp_new:
        state["cp"] = cp_new
    state["stamp"] = stp
    if out["status"] in ("applied", "noop"):
        # the phase baseline moves only once a plan is applied: a held phase
        # change must stay held on the next sync
        state["phase"] = phase
    await _set_state(db, state)
    return out


async def reject(db, entry_id: int) -> dict:
    from backend.api import plan_sessions as API
    async with API._wlock():
        r = await db.get(PlanChangeLog, entry_id)
        if r is None or r.status != "pending":
            raise ValueError("找不到待確認的提案")
        errors: list = []
        r.status = "rejected"
        await db.commit()
        await _remove_notice(db, r.notice_uid, errors)
        cfg = await settings(db)
        state = dict(cfg["state"] or {})
        state["rejected"] = ((state.get("rejected") or []) + [r.fingerprint])[-REJECTED_KEEP:]
        await _set_state(db, state)
        await _add_entry(db, trigger="reject", status="rejected", summary="你拒絕了這次的課表調整：維持原本的課表",
                         items=json.loads(r.items_json or "[]"), ref_id=r.id,
                         push={"status": "notice_removed", "errors": errors or None})
        return {"status": "rejected", "id": r.id}


async def approve(db, entry_id: int) -> dict:
    r = await db.get(PlanChangeLog, entry_id)
    if r is None or r.status != "pending":
        raise ValueError("找不到待確認的提案")
    return await run(db, trigger="approve", force=True, approve_id=entry_id)


async def undo(db, entry_id: int) -> dict:
    """Restore the affected sessions as they were before this run (pinned:
    edited = True, so the next run keeps them), tombstone what it added, re-push."""
    from backend.api import plan_sessions as API
    from backend.engine import plan_store as PS
    async with API._wlock():
        r = await db.get(PlanChangeLog, entry_id)
        if r is None or r.status != "applied":
            raise ValueError("這筆紀錄不能復原")
        before = json.loads(r.before_json or "[]")
        after = json.loads(r.after_json or "[]")
        cur = await PS.load(db)
        by = {s["uid"]: s for s in cur}
        for s in after:
            if s["uid"] not in {b["uid"] for b in before} and s["uid"] in by:
                x = by[s["uid"]]
                if x["origin"] == "auto":
                    x["state"] = "deleted"           # tombstone: not regenerated
                    x["note"] = "復原自動調整"
                else:
                    del by[s["uid"]]
        for b in before:
            by[b["uid"]] = {**b, "edited": True if b["state"] == "active" else b.get("edited"),
                            "provisional": False}
        new = list(by.values())
        await PS.save(db, new)
        r.status = "undone"
        await db.commit()
        inp = await API._inputs(db)
        cfg = await settings(db)
        push = await push_window(db, new, inp, API._today(inp), int(cfg["push_days"] or 7)) if cfg["push"] \
            else {"status": "off"}
        n = len({s["uid"] for s in before + after})
        e = await _add_entry(db, trigger="undo", status="restore",
                             summary=f"復原 {n} 堂課到調整前（改為你的課，自動調整不再動它們）",
                             items=json.loads(r.items_json or "[]"), before=after, after=before, ref_id=r.id,
                             push=push)
        return {"status": "undone", "id": e.id, "push": push}


# ---------------------------------------------------------------------------
# the sync hook
# ---------------------------------------------------------------------------

async def run_safe(trigger: str) -> dict:
    """A run in its own DB session; never raises (the sync must not break)."""
    factory = SESSION_FACTORY
    if factory is None:
        from backend.db.database import AsyncSessionLocal as factory
    try:
        async with factory() as db:
            try:
                return await run(db, trigger=trigger)
            except Exception as e:          # noqa: BLE001
                log.warning("auto plan run failed: %s", type(e).__name__)
                try:
                    await db.rollback()
                    await _add_entry(db, trigger=trigger, status="failed",
                                     summary=f"自動調整失敗：{type(e).__name__}", items=[],
                                     push={"status": "failed", "error": f"{type(e).__name__}: {e}"[:500]})
                except Exception:           # noqa: BLE001
                    pass
                return {"status": "failed", "error": type(e).__name__}
    except Exception as e:                  # noqa: BLE001
        log.warning("auto plan session failed: %s", type(e).__name__)
        return {"status": "failed", "error": type(e).__name__}


def _after_sync(source: str, result: dict) -> Optional[asyncio.Task]:
    """sync/runner.py calls this when a run ends: ≥ 1 new activity -> a background run."""
    if (result or {}).get("status") not in ("ok", "partial") or int((result or {}).get("downloaded") or 0) < 1:
        return None
    try:
        t = asyncio.get_running_loop().create_task(run_safe(f"sync:{source}"))
    except RuntimeError:
        return None
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


after_sync = _after_sync           # the hook sync/runner.py calls (tests replace it)


def _after_thresholds() -> None:
    """api/plan.py calls this after a threshold edit (apply-cp, the plan page):
    a background run that sees the new CP (cp_of vs state["cp"]) re-zones the
    upcoming power targets and re-pushes them (plan.auto.push permitting).
    From a sync endpoint (a worker thread) the task is started on the app's
    event loop; without one (scripts) nothing happens — the next sync does it."""
    def start():
        t = asyncio.get_running_loop().create_task(run_safe("cp_change"))
        _TASKS.add(t)
        t.add_done_callback(_TASKS.discard)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            import anyio.from_thread
            anyio.from_thread.run_sync(start)
        except Exception:                   # noqa: BLE001 — no event loop: the next sync picks it up
            pass
        return
    start()


after_thresholds = _after_thresholds     # the hook api/plan.py calls (tests replace it)
