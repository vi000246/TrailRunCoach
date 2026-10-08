"""
自動調整課表: after a sync that imported ≥ 1 new activity (sync/runner.py),
reconcile done / missed -> adapt (engine/adapt.py, inside
plan_store.reconcile_with_adapt) -> regenerate the upcoming weeks -> push the
next `plan.auto.push_days` days to COROS. The user only reviews: edited /
custom sessions and 不排課日期 are never changed by this (reconcile rules).

Safety
  * one run at a time: the plan writer lock (api/plan_sessions._wlock), shared
    with the page's edits / pushes; a run that finds the same data stamp as the
    last one does nothing (no change, no push). The lock covers reconcile + save
    only: the push to the watch follows under the push lock
    (api/plan_sessions._plock) from the stored plan as it is then, so the page
    never waits for COROS and an edit made meanwhile wins (SP-362 B3).
  * a push failure never breaks the sync: the run is a separate task, and the
    push error is stored in the change log.
  * big changes are held as a proposal (plan.auto.confirm_big): the stored plan
    only takes the done / missed part, the watch keeps what was pushed, and the
    user approves / rejects on the overview / 課表 page. With
    plan.auto.notify = watch a 1-minute「課表待確認」 workout is pushed too.
    Big (thresholds 推估):
      - a week's planned TSS + > 20 % vs the stored (= last pushed) version
        (reductions are the safe direction and apply on their own)
      - a long / quality / test removed within 14 days before an A race; within those
        14 days also any move / step-down by the self-rating rule (SP-231) or by rule D's
        太強 tier (an easy run that turned into a hard session, SP-301)
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
and one change-log row says 「CP 220 → 226 W：未來 N 堂課的功率目標已更新並重新
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

from backend.db.current import current_athlete_id
from backend.db.models import PlanChangeLog, PlanSession
from backend.i18n import N_, _

log = logging.getLogger(__name__)

# 推估 thresholds for "big" (labelled in the UI hover text too)
BIG_TSS_UP = 0.20           # a week's planned TSS + > 20 %
RACE_GUARD_DAYS = 14        # removing a long / quality / test this close to an A race
MAX_CHANGED = 3             # > 3 non-reducing changes in the push window
BIG_TEXT = {"tss": "單週計畫 TSS 比上次推送 +20% 以上（推估）",
            "race": "A 賽前 14 天內拿掉長跑／強度課／測試（推估）",
            "rpe": "A 賽前 14 天內因為跑後自評延後或降階強度課（推估）",
            "too_hard": N_("A 賽前 14 天內因為輕鬆跑跑成強度課，延後或降階強度課（推估）"),
            "phase": "訓練周期改變（推估）",
            "many": "推送範圍內超過 3 堂課改變、而且不是單純減量（推估）"}
HARD_LONG = ("long", "quality", "test")
RPE_RULE = "rpe_hard"       # engine/adapt.py: the self-rating trigger of rule D (SP-231)
OVERHARD_RULE = "overhard"  # engine/adapt.py rule D: only its 太強 tier changes sessions (SP-301)
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
        after, _n1 = _week_tss(new, ws)          # not `_`: that is the i18n function
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
            # the self-rating rule (SP-231) and rule D's 太強 (SP-301) only move / step down:
            # still the user's call this close to the A race (research §4.3: tired in a taper is common)
            elif i.get("rule") in (RPE_RULE, OVERHARD_RULE) and any(start <= (d or "") <= race for d in
                                                                     (i.get("day"), (i.get("before") or {}).get("day"))):
                out.append({"rule": "rpe" if i["rule"] == RPE_RULE else "too_hard",
                            "text": _("A 賽前 {n} 天內：{reason}", n=RACE_GUARD_DAYS,
                                      reason=i.get("reason") or i.get("title") or "")})
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
        if s.get("steps"):
            # a structure saved in the editor (engine/workout_steps.py): % CP / zone steps follow
            # the new CP by themselves; the absolute watt overrides are rescaled like the text
            from backend.engine import workout_steps as WS
            x["steps"] = WS.rescale_abs_power(s["steps"], old, new)
        if t2 != s.get("target") or d2 != s.get("detail") or x.get("steps") is not s.get("steps") or \
                _has_power(x, new):
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
    if inp.get("rpe_stamp"):
        # this week's self-ratings (SP-231): one read after the activity's import is a change too
        key.append(inp["rpe_stamp"])
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
    # 推課表到手錶 only when the push provider (sync/workout_targets, plan.push.provider)
    # is connected (generalize-athlete S4): push and notify None = auto -> on /
    # "watch" when connected, off / "overview" without. Today only COROS can be
    # connected; the other providers are stubs.
    from backend.sync import runner
    from backend.sync import workout_targets as WT
    prov = await WT.active(db, current_athlete_id())
    connected = prov.enabled and prov.id == "coros" and await runner.logged_in(db, "coros", current_athlete_id())
    out["coros_logged_in"] = connected            # the auto-plan panel's flag (name kept)
    out["push_provider"] = prov.id
    if out["push"] is None:
        out["push"] = connected
    if out["notify"] is None:
        out["notify"] = "watch" if connected else "overview"
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
    r = PlanChangeLog(athlete_id=current_athlete_id(), created_at=dt.datetime.utcnow(), **kw)
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


async def _remove_notice(db, uid: Optional[str], errors: list, remote: Optional[list] = None) -> None:
    """Take a 課表待確認 workout off the store and the watch (explicitly: an
    automatic stale removal would keep a past-day one). `remote` (SP-362 B3): a list the
    uid goes into instead of calling the watch now — the caller holds the writer lock and
    removes it after (_remove_remote, under the push lock)."""
    if not uid:
        return
    res = await db.execute(select(PlanSession).where(PlanSession.uid == uid))
    r = res.scalar_one_or_none()
    if r is not None:
        await db.delete(r)
        await db.commit()
    if remote is not None:
        remote.append(uid)
        return
    await _remove_remote(db, [uid], errors)


async def _remove_remote(db, uids: list, errors: list) -> None:
    """The watch half of _remove_notice."""
    from backend.sync import workout_targets as WT
    for uid in uids:
        try:
            prov = await WT.active(db)
            rows = await prov.rows_by_key(db, [uid])
            if rows:
                await prov.remove_keys(db, [uid])
        except Exception as e:              # noqa: BLE001 — logged, never fatal
            errors.append(f"移除課表待確認失敗：{type(e).__name__}: {e}"[:300])


async def _resolve_pending(db, status: str, errors: list, remote: Optional[list] = None) -> Optional[PlanChangeLog]:
    p = await pending(db)
    if p is not None:
        p.status = status
        await db.commit()
        await _remove_notice(db, p.notice_uid, errors, remote)
    return p


async def _stale_notices(db, today: str, keep: Optional[str], errors: list, remote: Optional[list] = None) -> None:
    res = await db.execute(select(PlanSession).where(PlanSession.kind == NOTICE_KIND))
    for r in res.scalars().all():
        if r.uid != keep and (r.day or "") < today:
            await _remove_notice(db, r.uid, errors, remote)


async def _set_push(db, entry_id: int, push: dict) -> None:
    """Fill in a change-log row's push result once the push (outside the writer lock) is done."""
    r = await db.get(PlanChangeLog, entry_id)
    if r is not None:
        r.push_json = json.dumps(push, ensure_ascii=False, default=str)
        await db.commit()


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------

async def push_window(db, new: list[dict], inp: dict, today: str, days: int,
                      extra_uids: Optional[set] = None, only: Optional[set] = None,
                      window: bool = True) -> dict:
    """Push the active sessions in [today, today + days − 1] (later ones stay in
    the app); remove pushed sessions that left the plan / were missed. Nothing
    is sent when every session in the window is already up to date. Errors are
    returned, never raised. `extra_uids`: sessions after the window that are
    on the watch already and must be re-sent too (a CP change: their watts).
    A session whose copy on the watch sits in the window but which moved out of it
    is re-sent on its new day, so the old copy comes off (SP-358).
    `only` (the user's own change, api/plan_sessions._sync_watch): just these sessions —
    the ones in the window, and the ones already on the watch wherever they are now; the
    stale / blocked / missed clean-up is limited to them too (other copies wait for the run);
    `window` False (自動推送 off): only the copies already on the watch."""
    from backend.api import plan_sessions as API
    from backend.engine import plan_store as PS
    from backend.sync import workout_targets as WT
    end = (dt.date.fromisoformat(today) + dt.timedelta(days=max(1, days) - 1)).isoformat()
    try:
        prov = await WT.active(db)              # setting plan.push.provider (default COROS)
        rows = await prov.all_rows(db)
        bl = PS.blocked_map(inp)
        live = {s["uid"] for s in new if s["state"] in ("active", "done", "missed")}
        stale = [k for k, r in rows.items() if k not in live and (r.day is None or r.day >= today)]
        stale += [s["uid"] for s in API._on_blocked(new, bl, today) if s["uid"] in rows]
        missed = [s["uid"] for s in new if PS.off_watch(s) and s["uid"] in rows]
        if only is not None:
            # the user's own change syncs only what it touched (owner, SP-358 review): other stale /
            # missed copies are left to the automatic run and the manual push
            stale = [k for k in stale if k in only]
            missed = [k for k in missed if k in only]
        todo = [s for s in API._in_range(new, today, end, bl) if s["kind"] != NOTICE_KIND
                and window and (only is None or s["uid"] in only)]
        extra = set(extra_uids or ())
        extra |= set(only) if only is not None else API.copies_in(rows, today, end)
        have = {s["uid"] for s in todo}
        todo += [s for s in API.on_watch(new, rows, today, bl)
                 if s["uid"] in extra and s["uid"] not in have and s["kind"] != NOTICE_KIND]
        th = inp.get("thresholds") or {}
        need = [s for s in todo if prov.status_of(PS.push_dict(s), th, rows.get(s["uid"]), today)["status"]
                in ("not_pushed", "outdated", "failed")]
        if not need and not stale and not missed:
            return {"status": "unchanged", "start": today, "end": end, "sent": 0, "removed": 0}
        res = await prov.push_sessions(db, [PS.push_dict(s) for s in todo], th, today,
                                       stale_keys=stale, missed_keys=missed)
        sent = sum(1 for x in res.get("sessions") or [] if x.get("changed"))
        # a copy that didn't come off the watch is a failure too (SP-358: it used to count as nothing)
        every = (res.get("sessions") or []) + (res.get("removed") or [])
        failed = [x for x in every if x.get("status") == "failed"]
        out = {"status": "partial" if failed else "ok", "start": today, "end": end, "sent": sent,
               "removed": sum(1 for x in res.get("removed") or [] if x.get("status") == "removed"),
               "error": "；".join(str(x.get("error")) for x in failed)[:500] or None}
        # COROS accepted a calendar delete but still listed the entry (coros_workouts._remove_remote):
        # a reminder to check in the COROS app, not a failure
        check = sorted({x["check_day"] for x in every if x.get("check_day")})
        return {**out, "check_days": check} if check else out
    except Exception as e:                  # noqa: BLE001 — the push never breaks the run / the sync
        log.warning("auto plan push failed: %s", type(e).__name__)
        try:
            await db.rollback()
        except Exception:                   # noqa: BLE001
            pass
        return {"status": "failed", "start": today, "end": end, "error": f"{type(e).__name__}: {e}"[:500]}


async def _push_notice(db, s: dict, inp: dict, today: str) -> dict:
    from backend.engine import plan_store as PS
    from backend.sync import workout_targets as WT
    try:
        prov = await WT.active(db)
        res = await prov.push_sessions(db, [PS.push_dict(s)], inp.get("thresholds") or {}, today)
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
    """Log lines for a Zone 3 gate change (SP-31), a Zone 5 state change and a new re-entry
    block (plan rules, not sessions); updates `state` in place (keys z3, z5, z5_track, reentry)."""
    out = []
    cur = inp.get("cur") or {}
    z3 = (cur.get("quality_gate") or {}).get("z3") or {}
    z3key = f"{bool(z3.get('open'))}|{z3.get('path')}" if z3 else None
    if z3 and z3key != state.get("z3"):
        if state.get("z3") is not None or z3.get("open"):
            # the first record of a closed gate says nothing new; opening (or closing again) is logged
            out.append(z3.get("text") or (_("Zone 3：已解鎖") if z3.get("open") else _("Zone 3：未解鎖")))
        state["z3"] = z3key
    z5 = (cur.get("quality_gate") or {}).get("z5") or {}
    key = f"{z5.get('state')}|{z5.get('since')}|{z5.get('path')}" if z5 else None
    if z5 and key != state.get("z5"):
        old = (state.get("z5") or "").split("|")[0]
        from backend.engine.base_check import STATE_LABEL
        out.append(f"Zone 5：{STATE_LABEL.get(old, '—') if old else '—'} → {z5.get('text') or z5.get('label')}")
        state["z5"] = key
    # the Zone 5 track itself (SP-39, quality_gate.z5_track: measured AeT + the soft 「3 區先」):
    # unlocking and re-locking are logged; the first record of a closed track says nothing new
    zt = (cur.get("quality_gate") or {}).get("z5_gate") or {}
    if zt:
        tkey = "open" if zt.get("open") else "closed"
        if tkey != state.get("z5_track") and (state.get("z5_track") is not None or zt.get("open")):
            out.append(zt.get("text") if zt.get("open") else
                       _("Zone 5：重新上鎖（{reason}）", reason=zt.get("reason") or "") if state.get("z5_track") == "open"
                       else zt.get("text") or _("Zone 5：未解鎖"))
        state["z5_track"] = tkey
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
    """The change-log row of a CP change: 「CP 220 → 226 W：未來 N 堂課的功率目標已更新
    並重新推送」, or 「…，待推送」 when plan.auto.push is off (or the run is held / the
    push failed). Per session: 已重新推送 / 待推送 (on the watch with old watts) /
    只在 app (not on the watch yet: it is sent when it enters the push window)."""
    from backend.engine import plan_store as PS
    from backend.sync import workout_targets as WT
    cur = {s["uid"]: s for s in await PS.load(db)}
    prov = WT.get(WT.DEFAULT)
    try:
        prov = await WT.active(db)
        rows = await prov.all_rows(db)
    except Exception:                       # noqa: BLE001
        rows = {}
    marked = []
    for i in items:
        s = cur.get(i["uid"])
        if i["uid"] in rows and s is not None:
            st = prov.status_of(PS.push_dict(s), th, rows[i["uid"]], today)["status"]
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
    and the big-change hold (approve, the page's 立即重算).

    SP-362 B3: the inputs are computed before the writer lock (3–4 s cold on the NAS, as GET
    /sessions does); the writer lock (api/plan_sessions._wlock) covers reconcile, save, the
    change-log row and the run's state only; the watch (COROS, 20–30 s on the NAS) follows under
    the push lock (_plock) from the stored plan as it is then (_push_after). The page's GET and
    the user's edits no longer wait for COROS; an edit saved meanwhile wins (see _plock)."""
    from backend.api import plan_sessions as API
    if not force and not (await settings(db))["enabled"]:
        return {"status": "disabled"}
    inp = await API._inputs(db)
    async with API._wlock():
        out, job = await _run(db, trigger, force, approve_id, inp)
    if job is None:
        return out
    return await _push_after(db, job, out)


async def _run(db, trigger: str, force: bool, approve_id: Optional[int], inp: dict) -> tuple[dict, Optional[dict]]:
    """The part under the writer lock: (result, job) — `job` is what _push_after sends to the
    watch and logs once the lock is released (None: nothing)."""
    from backend.api import plan_sessions as API
    from backend.engine import plan_store as PS
    cfg = await settings(db)
    if not cfg["enabled"] and not force:
        return {"status": "disabled"}, None
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
        return {"status": "noop", "reason": "沒有新的活動"}, None
    errors: list = []
    remote: list = []              # 課表待確認 copies to take off the watch after the lock
    p = await pending(db)
    await _stale_notices(db, today, p.notice_uid if p is not None else None, errors, remote)
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
    held = bool(big and cfg["confirm_big"] and not force)
    job: dict = {"inp": inp, "today": today, "cfg": cfg, "trigger": trigger, "errors": errors, "remote": remote,
                 "held": held, "notice": None, "entry": None, "window": False, "days": days, "items": items,
                 "cp_items": cp_items, "cp": (cp_old, cp_new) if cp_changed else None}

    if held:
        if fp in (state.get("rejected") or []):
            await PS.save(db, history_only(stored, new))
            out = {"status": "rejected_before", "reason": "與你拒絕過的提案相同，略過"}
        elif p is not None and p.fingerprint == fp:
            await PS.save(db, history_only(stored, new))
            out = {"status": "pending", "id": p.id}
        else:
            old = await _resolve_pending(db, "superseded", errors, remote)
            gone = {old.notice_uid} if old is not None else set()
            await PS.save(db, _without(history_only(stored, new), gone))
            notice = None
            if cfg["notify"] == "watch" and cfg["push"]:
                cur = await PS.load(db)
                notice = notice_session(notice_day(cur, inp.get("activities") or [], today), items, big)
                await PS.save(db, cur + [notice])
            before, after = _affected(stored, new, items)
            # written under the lock: a page load must find the proposal waiting (_ensure); the
            # notice's push result is filled in after the push (_push_after)
            e = await _add_entry(db, trigger=trigger, status="pending", summary=summary(items, held=True),
                                 items=items, before=before, after=after, big=big, fingerprint=fp,
                                 push={"status": "pushing" if notice else "held", "errors": errors or None},
                                 notice_uid=notice["uid"] if notice else None)
            job.update(notice=notice, entry=e.id)
            out = {"status": "pending", "id": e.id}
    else:
        resolved = await _resolve_pending(db, "approved" if approve_id else "superseded", errors, remote) \
            if (p is not None or approve_id) else None
        # no proposal is waiting any more: no 課表待確認 stays in the plan
        # (one still on the watch is removed by the push: it left the plan)
        new = [s for s in new if s["kind"] != NOTICE_KIND]
        await PS.save(db, new)
        ref = resolved.id if resolved is not None and approve_id else None
        if items:
            before, after = _affected(stored, new, items)
            e = await _add_entry(db, trigger=trigger, status="applied", summary=summary(items),
                                 items=items, before=before, after=after, big=big or None, fingerprint=fp,
                                 push={"status": "pushing" if cfg["push"] else "off"}, ref_id=ref)
            job["entry"] = e.id
            out = {"status": "applied", "id": e.id}
        job.update(window=bool(cfg["push"]), big=big, fp=fp, ref=ref)
    if cp_new:
        state["cp"] = cp_new
    state["stamp"] = stp
    if not held:
        # the phase baseline moves only once a plan is applied: a held phase
        # change must stay held on the next sync
        state["phase"] = phase
    await _set_state(db, state)
    # 每週課表存檔 (engine/plan_history.py, SP-71): a sync alone, with no page opened, still records the week
    from backend.engine import plan_history as PH
    await PH.record_safe(db, inp)
    return out, job


async def _push_after(db, job: dict, out: dict) -> dict:
    """The watch part of a run, after the writer lock (SP-362 B3): under the push lock, take the
    old 課表待確認 copies off, push the new notice or the window (from the stored plan as it is
    now: an edit saved since this run's save goes out as edited, never overwritten), then fill
    in / write the change-log row and the CP row. Errors are logged, never raised."""
    from backend.api import plan_sessions as API
    from backend.engine import plan_store as PS
    inp, today, errors, cfg = job["inp"], job["today"], job["errors"], job["cfg"]
    push: Optional[dict] = None
    if job["remote"] or job["notice"] is not None or job["window"]:
        async with API._plock():
            try:
                await _remove_remote(db, job["remote"], errors)
                if job["notice"] is not None:
                    push = await _push_notice(db, job["notice"], inp, today)
                elif job["window"]:
                    push = await push_window(db, await PS.load(db), inp, today, job["days"],
                                             {i["uid"] for i in job["cp_items"]})
            except Exception as e:          # noqa: BLE001 — the push never breaks the run / the sync
                log.warning("auto plan push failed: %s", type(e).__name__)
                push = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:500]}
    if job["held"]:
        if job["entry"] is not None:
            await _set_push(db, job["entry"], {**(push or {"status": "held"}), "errors": errors or None})
    else:
        push = push or {"status": "off"}
        if errors:
            push = {**push, "errors": errors}
        if job["entry"] is not None:
            await _set_push(db, job["entry"], push)
            out = {**out, "push": push}
        # a push that only re-sends a CP change is logged by _log_cp (below), not twice
        elif push.get("status") not in ("unchanged", "off") and not job["cp_items"]:
            e = await _add_entry(db, trigger=job["trigger"], status="applied", summary=summary(job["items"]),
                                 items=job["items"], before=[], after=[], big=job["big"] or None,
                                 fingerprint=job["fp"], push=push, ref_id=job["ref"])
            out = {"status": "applied", "id": e.id, "push": push}
        else:
            out = {"status": "noop", "push": push}
    if job["cp"]:
        old, new = job["cp"]
        out["cp_change"] = await _log_cp(db, old, new, job["cp_items"], cfg, out, job["trigger"],
                                         inp.get("thresholds") or {}, today)
    return out


async def reject(db, entry_id: int) -> dict:
    from backend.api import plan_sessions as API
    async with API._wlock():
        r = await db.get(PlanChangeLog, entry_id)
        if r is None or r.status != "pending":
            raise ValueError("找不到待確認的提案")
        errors: list = []
        remote: list = []
        r.status = "rejected"
        await db.commit()
        await _remove_notice(db, r.notice_uid, errors, remote)
        cfg = await settings(db)
        state = dict(cfg["state"] or {})
        state["rejected"] = ((state.get("rejected") or []) + [r.fingerprint])[-REJECTED_KEEP:]
        await _set_state(db, state)
    if remote:
        async with API._plock():                 # the watch after the writer lock (SP-362 B3)
            await _remove_remote(db, remote, errors)
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
                if x.get("note") == PS.USER_DELETED and x.get("state") == "deleted":
                    continue
                if x["origin"] == "auto":
                    x["state"] = "deleted"           # tombstone: not regenerated
                    x["note"] = "復原自動調整"
                else:
                    del by[s["uid"]]
        for b in before:
            x = by.get(b["uid"])
            if x is not None and x.get("state") == "deleted" and x.get("note") == PS.USER_DELETED:
                continue                         # an expired session the user deleted stays deleted
            by[b["uid"]] = {**b, "edited": True if b["state"] == "active" else b.get("edited"),
                            "provisional": False}
        new = list(by.values())
        await PS.save(db, new)
        r.status = "undone"
        await db.commit()
        cfg = await settings(db)
    # the watch after the writer lock, from the store as it is then (SP-362 B3)
    push = {"status": "off"}
    if cfg["push"]:
        inp = await API._inputs(db)
        async with API._plock():
            push = await push_window(db, await PS.load(db), inp, API._today(inp), int(cfg["push_days"] or 7))
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
    from backend import applog
    try:
        async with factory() as db:
            try:
                with applog.timed("auto plan run", trigger=trigger):     # SP-215
                    return await run(db, trigger=trigger)
            except Exception as e:          # noqa: BLE001
                log.warning("auto plan run failed: %s", type(e).__name__, exc_info=True)
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
    """sync/runner.py calls this when a run ends: ≥ 1 new activity, or a COROS self-rating
    stored on an already-imported one (`rpe_filled`, SP-231) -> a background run."""
    r = result or {}
    if r.get("status") not in ("ok", "partial") or (int(r.get("downloaded") or 0) < 1
                                                    and int(r.get("rpe_filled") or 0) < 1):
        return None
    try:
        t = asyncio.get_running_loop().create_task(run_safe(f"sync:{source}"))
    except RuntimeError:
        return None
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


after_sync = _after_sync           # the hook sync/runner.py calls (tests replace it)


def busy() -> bool:
    """An automatic run (a sync's, a threshold / settings change's) is running or queued.
    Safe from another thread (the warm-up thread polls it, SP-362)."""
    return any(not t.done() for t in list(_TASKS))


async def wait_idle(timeout: float = 600.0) -> bool:
    """Wait for this event loop's automatic runs to end (at most `timeout` s): the lower
    priority work after a sync (calibration, engine/calibrate.py) starts after the plan
    (SP-362). True when idle."""
    loop, me = asyncio.get_running_loop(), asyncio.current_task()
    pending = [t for t in list(_TASKS) if not t.done() and t is not me and t.get_loop() is loop]
    if not pending:
        return True
    _done, left = await asyncio.wait(pending, timeout=timeout)
    return not left


def _after_thresholds() -> None:
    """api/plan.py calls this after a threshold edit (apply-cp, the plan page):
    a background run that sees the new CP (cp_of vs state["cp"]) re-zones the
    upcoming power targets and re-pushes them (plan.auto.push permitting).
    From a sync endpoint (a worker thread) the task is started on the app's
    event loop; without one (scripts) nothing happens — the next sync does it."""
    _start_run("cp_change")


def _after_settings() -> None:
    """api/calib.py calls this after a 進階設定 value the plan reads changed (the Zone 3
    unlock rule, SP-295): a background run re-plans with it, like a sync would."""
    _start_run("settings")


def _start_run(trigger: str) -> None:
    def start():
        t = asyncio.get_running_loop().create_task(run_safe(trigger))
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
after_settings = _after_settings         # the hook api/calib.py calls (tests replace it)
