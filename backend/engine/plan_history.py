"""
每週課表存檔 (SP-71): what the plan held when a week began, and how the week went.

Why (docs/research/plan-backtest-feasibility.md §1.3, §5.1): whether training by the app's plan
works can only be judged from records kept from now on, and two of them were missing —
  * 介入的紀錄: plan_sessions is rewritten by every reconcile (docs/spec/plan-auto.spec.md, rule 2:
    an unedited automatic session is regenerated), so it cannot say what was planned at the time;
  * 遵從度: the 課表統計 page computes planned vs done on the fly from those same rows, nothing is
    kept per week.

One row per week (db/models.PlanWeekSnapshot), written by record():
  plan    plan_summary() — frozen the first time the app sees the week (a page that shows the
          plan, or an automatic run after a sync). A week first seen after it ended is still
          stored (the stored rows of a past week are no longer regenerated) and marked `late`:
          it is what the plan ended as, not what it started as.
  result  result_summary() — once the week is over: sessions due / done / as planned / missed, TSS
          and hours planned vs done, the 強度課 alone, and how far the plan moved from the
          snapshot (drift). The same numbers as the 課表統計 page (engine/compliance.dashboard).
          It is refreshed while a late sync or a hand pairing can still change it, and frozen
          (`final`) FINAL_DAYS after the week ended.

Small on purpose (owner 2026-10-05: a summary, not the session objects; the NAS container has
3 GB): per session the day, kind, minutes, TSS and a short title — no target / detail / steps
text — about 1 kB a week, and record() reads only the rows of the last BACKFILL_WEEKS weeks.

What these records can and cannot answer (plan-backtest-feasibility.md §5.3): how closely this
athlete followed the plan and how the tests / races moved meanwhile — not whether another plan
would have done better.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Callable, Optional

from sqlalchemy import select

from backend.db.current import current_athlete_id
from backend.db.models import PlanWeekSnapshot

log = logging.getLogger(__name__)

VERSION = 1
# 推估: a run synced late or paired by hand still changes a finished week; two weeks on nothing
# does any more (the plan's activity window, api/plan_sessions._compute_inputs, reaches 4 weeks back)
FINAL_DAYS = 14
# a week with stored sessions and no row yet gets one this far back (推估: the app was not opened
# for a while; older weeks would be a guess at what was planned)
BACKFILL_WEEKS = 8
TITLE_MAX = 40
PLAN_STATES = ("active", "done", "missed")      # as api/plan_sessions._week_rows: what the week planned
SKIP_KINDS = ("notice",)                        # plan_store.NOT_LOAD: the 課表待確認 reminder is not training
QUALITY_KINDS = ("quality",)
HISTORY_MAX = 104                               # GET /history: at most two years of weeks


def monday_of(day: str) -> str:
    d = dt.date.fromisoformat(day[:10])
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def week_end(week_start: str) -> str:
    return (dt.date.fromisoformat(week_start) + dt.timedelta(days=6)).isoformat()


def _in_week(s: dict, ws: str) -> bool:
    return bool(s.get("day")) and ws <= s["day"] <= week_end(ws)


def planned(sessions: list[dict], ws: str) -> list[dict]:
    """The sessions a week planned: active + done + missed (deleted / superseded rows and the
    課表待確認 reminder left out), by day."""
    out = [s for s in sessions if _in_week(s, ws) and s.get("state") in PLAN_STATES
           and s.get("kind") not in SKIP_KINDS]
    return sorted(out, key=lambda s: (s["day"], s.get("kind") == "strength", s.get("uid") or ""))


def _entry(s: dict, ws: str, est: Callable[[dict], float], family: Optional[Callable[[dict], Optional[str]]]) -> dict:
    """One session of the snapshot, short keys: u uid · d weekday (0 = Monday) · k kind ·
    m minutes · t TSS · n title; r the ladder step, f a 強度課's family, o c = the user's own /
    e = an automatic one the user edited, st its state when not open any more (a snapshot
    taken mid-week or late)."""
    e = {"u": s.get("uid"), "d": (dt.date.fromisoformat(s["day"]) - dt.date.fromisoformat(ws)).days,
         "k": s.get("kind"), "m": int(s.get("minutes") or 0), "t": round(float(est(s) or 0.0), 1),
         "n": str(s.get("title") or "")[:TITLE_MAX]}
    if s.get("rung_key"):
        e["r"] = s["rung_key"]
    fam = family(s) if family is not None and s.get("kind") in QUALITY_KINDS else None
    if fam:
        e["f"] = fam
    if s.get("origin") == "custom":
        e["o"] = "c"
    elif s.get("edited"):
        e["o"] = "e"
    if s.get("state") != "active":
        e["st"] = s.get("state")
    return e


def plan_summary(sessions: list[dict], ws: str, today: str, est: Optional[Callable[[dict], float]] = None,
                 ctx: Optional[dict] = None, family: Optional[Callable[[dict], Optional[str]]] = None) -> dict:
    """The compact record of what week `ws` planned, from the stored sessions (plan_store dicts).
    `est(s)`: a session's planned TSS (api/plan_sessions.est_tss; default its stored tss);
    `ctx`: what only the running week knows — phase, mode, the thresholds in effect, the week's
    target, the CTL at its start (a week stored late has none: nothing is guessed);
    `family(s)`: a 強度課's family id. Hours leave the strength sessions out, like week_plan()."""
    est = est or (lambda s: float(s.get("tss") or 0.0))
    ss = planned(sessions, ws)
    rows = [_entry(s, ws, est, family) for s in ss]
    out = {"v": VERSION, "taken": today, "late": today > week_end(ws), "n": len(rows),
           "hours": round(sum(e["m"] for e in rows if e["k"] != "strength") / 60.0, 2),
           "tss": round(sum(e["t"] for e in rows), 1)}
    for k, v in (ctx or {}).items():
        if v is not None:
            out[k] = v
    out["s"] = rows
    return out


def week_ctx(inp: dict, ws: str) -> dict:
    """plan_summary's `ctx` for the running week from the plan inputs (api/plan_sessions.
    _compute_inputs); {} for any other week — its phase and thresholds at the time are not known."""
    cur = inp.get("cur") or {}
    if (cur.get("week") or {}).get("start") != ws:
        return {}
    th = inp.get("thresholds") or cur.get("thresholds") or {}
    tgt, load = cur.get("target") or {}, cur.get("load") or {}

    def num(v, nd=1):
        try:
            return None if v is None else round(float(v), nd)
        except (TypeError, ValueError):
            return None
    thr = {k: num(th.get(k), 0) for k in ("cp", "lthr", "aet") if num(th.get(k), 0) is not None}
    target = {k: num(tgt.get(k), 2 if k == "hours" else 1) for k in ("hours", "tss") if num(tgt.get(k)) is not None}
    return {"phase": cur.get("phase"), "mode": cur.get("mode"), "thr": thr or None, "target": target or None,
            "ctl": num(load.get("ctl_week_start"))}


def drift(plan: dict, ws: str, every: list[dict]) -> dict:
    """How far the plan moved from the snapshot by the end of the week: sessions of the snapshot
    that changed (another day, kind or length), that were removed (deleted / replaced), and
    planned sessions that were not in it (a missed session placed again counts as added)."""
    by = {s.get("uid"): s for s in every}
    d0 = dt.date.fromisoformat(ws)
    changed = removed = 0
    for e in plan.get("s") or []:
        s = by.get(e.get("u"))
        if s is None or s.get("state") not in PLAN_STATES:
            removed += 1
        elif (s.get("day") != (d0 + dt.timedelta(days=int(e.get("d") or 0))).isoformat()
              or s.get("kind") != e.get("k") or int(s.get("minutes") or 0) != int(e.get("m") or 0)):
            changed += 1
    had = {e.get("u") for e in plan.get("s") or []}
    added = sum(1 for s in planned(every, ws) if s.get("uid") not in had)
    return {"changed": changed, "removed": removed, "added": added}


def result_summary(dash: dict, plan: dict, ws: str, every: list[dict], today: str) -> dict:
    """How week `ws` went, from compliance.dashboard() of exactly that week (`dash`): the same
    counts and totals as the 課表統計 page. done_tss / done_hours are every activity of the week,
    planned or not (None when the activities of that week were not at hand); quality = the
    強度課 alone (due, completed, ok = done as planned); drift vs the snapshot."""
    t = dash.get("totals") or {}
    wk = next((w for w in dash.get("weeks") or [] if w.get("start") == ws), None)
    q = [k for k in dash.get("by_kind") or [] if k.get("kind") in QUALITY_KINDS]
    out = {"v": VERSION, "at": today,
           **{k: t.get(k) for k in ("due", "completed", "partial", "off_plan", "missed", "open", "rate", "ok_rate",
                                    "planned_tss", "actual_tss", "tss_pct", "planned_hours", "actual_hours")},
           "ok": t.get("done"),
           "done_tss": None if wk is None else wk.get("done_tss"),
           "done_hours": None if wk is None else wk.get("done_hours"),
           "quality": {"due": sum(k.get("due") or 0 for k in q), "completed": sum(k.get("completed") or 0 for k in q),
                       "ok": sum(k.get("done") or 0 for k in q)},
           "drift": drift(plan, ws, every)}
    return out


def is_final(ws: str, today: str) -> bool:
    return (dt.date.fromisoformat(today) - dt.date.fromisoformat(week_end(ws))).days >= FINAL_DAYS


def _loads(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _dumps(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def view(r: PlanWeekSnapshot) -> dict:
    return {"week_start": r.week_start, "week_end": week_end(r.week_start), "final": bool(r.final),
            "plan": _loads(r.plan_json) or {}, "result": _loads(r.result_json)}


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

async def _rows(db, since: Optional[str] = None, limit: Optional[int] = None) -> list[PlanWeekSnapshot]:
    q = select(PlanWeekSnapshot).where(PlanWeekSnapshot.athlete_id == current_athlete_id())
    if since:
        q = q.where(PlanWeekSnapshot.week_start >= since)
    q = q.order_by(PlanWeekSnapshot.week_start.desc())
    if limit:
        q = q.limit(limit)
    return list((await db.execute(q)).scalars().all())


async def history(db, limit: int = 26) -> list[dict]:
    """The stored weeks, newest first."""
    return [view(r) for r in await _rows(db, limit=max(1, min(HISTORY_MAX, int(limit))))]


def _week_result(ws: str, plan: dict, every: list[dict], inp: dict, today: str, rates: dict) -> dict:
    """compliance.dashboard of week `ws` alone, with the page's own helpers (api/plan_sessions:
    the same TSS estimates, planned vs actual and off-plan verdicts as the 課表統計 page)."""
    from backend.api import plan_sessions as API
    from backend.engine import compliance as C
    we = week_end(ws)
    acts_all = inp.get("activities") or []
    covered = (inp.get("activities_since") or "9999") <= ws     # the activity window reaches this week
    acts = [a for a in acts_all if ws <= str(a.get("date") or "")[:10] <= we]
    ss = API._decorate([dict(s) for s in every if _in_week(s, ws)], acts_all, rates, ws, we)
    weeks = API._week_rows(ws, we, ss, acts, [], [], rates, today) if covered else []
    dash = C.dashboard(ss, weeks, today, ws, we)
    return result_summary(dash, plan, ws, every, today)


async def record(db, inp: dict, every: Optional[list[dict]] = None, today: Optional[str] = None) -> dict:
    """Store what is due: the plan snapshot of every week that has stored sessions and no row yet
    (from BACKFILL_WEEKS back up to the running week), and the result of every finished week
    that is not final. Idempotent and cheap (one small query when nothing is due); the caller
    holds the plan writer lock (api/plan_sessions._wlock). `every`: the stored sessions
    (plan_store.load) when the caller has them. Returns {"added": [...], "updated": [...]}."""
    from backend.api import plan_sessions as API
    from backend.engine import plan_store as PS
    today = today or API._today(inp)
    mon = monday_of(today)
    since = (dt.date.fromisoformat(mon) - dt.timedelta(weeks=BACKFILL_WEEKS)).isoformat()
    have = {r.week_start: r for r in await _rows(db, since=since)}
    if every is None:
        every = await PS.load(db)
    weeks = sorted({monday_of(s["day"]) for s in every if s.get("day") and s.get("state") in PLAN_STATES
                    and s.get("kind") not in SKIP_KINDS})
    new = [w for w in weeks if since <= w <= mon and w not in have]
    open_ = [w for w, r in have.items() if w < mon and not r.final] + [w for w in new if w < mon]
    if not new and not open_:
        return {"added": [], "updated": []}
    cur = inp.get("cur") or {}
    tph = float(((cur.get("target") or {}).get("tss_per_hour")) or 50.0)
    rates = API.tss_rates(cur.get("tss_per_category"), every, tph)
    th = inp.get("thresholds") or {}

    def family(s: dict) -> Optional[str]:
        try:
            from backend.engine import workout_templates as WTPL
            return (WTPL.session_family(s, th) or {}).get("id")
        except Exception:                   # noqa: BLE001 — the family is a nicety
            return None

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    aid = current_athlete_id()
    for w in new:
        plan = plan_summary(every, w, today, lambda s: API.est_tss(s, rates), week_ctx(inp, w), family)
        r = PlanWeekSnapshot(athlete_id=aid, week_start=w, plan_json=_dumps(plan), final=False,
                             created_at=now, updated_at=now)
        db.add(r)
        have[w] = r
    updated = []
    for w in sorted(set(open_)):
        r = have[w]
        res = _week_result(w, _loads(r.plan_json) or {}, every, inp, today, rates)
        old = _loads(r.result_json)
        fin = is_final(w, today)
        # `at` moves only with the numbers: an unchanged week is not rewritten on every page load
        if old is None or {**old, "at": None} != {**res, "at": None}:
            r.result_json = _dumps(res)
            r.updated_at = now
            updated.append(w)
        if fin and not r.final:
            r.final = True
            r.updated_at = now
            if w not in updated:
                updated.append(w)
    if new or updated:
        await db.commit()
    return {"added": new, "updated": updated}


async def record_safe(db, inp: dict, every: Optional[list[dict]] = None) -> None:
    """record() that never breaks the page or the automatic run it rides on."""
    try:
        await record(db, inp, every)
    except Exception as e:                  # noqa: BLE001
        log.warning("plan history not recorded: %s", type(e).__name__)
        try:
            await db.rollback()
        except Exception:                   # noqa: BLE001
            pass
