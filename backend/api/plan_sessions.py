"""
Stored, editable plan + COROS push (engine/plan_store.py, engine/reconcile.py,
engine/projection.py, sync/coros_workouts.py).

  GET    /api/v1/overview/plan/sessions?start=&end=   stored sessions + COROS status
  POST   /api/v1/overview/plan/sessions               add a custom session
  PATCH  /api/v1/overview/plan/sessions/{uid}         edit (day / kind / minutes / title / target / detail)
  DELETE /api/v1/overview/plan/sessions/{uid}
  GET    /api/v1/overview/plan/reconcile              preview: added / changed / removed per day
  POST   /api/v1/overview/plan/reconcile              apply
  GET    /api/v1/overview/plan/push-coros/preview?scope=day|week|phase&day=
  POST   /api/v1/overview/plan/push-coros?scope=&day= reconcile, then push the range
  DELETE /api/v1/overview/plan/push-coros?scope=&day= remove what was pushed in the range
"""
from __future__ import annotations

import asyncio
import datetime as dt
import threading
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.db.database import get_db
from backend.engine import plan_store as PS
from backend.engine import projection as P
from backend.engine import reconcile as R
from backend.sync import coros_workouts as CW

router = APIRouter(prefix="/api/v1/overview/plan", tags=["overview"])

SCOPES = ("day", "week", "phase")
_lock = threading.Lock()
_cache: dict = {}


def _compute_inputs() -> dict:
    """week_plan() for this week, the projection to the horizon, activities
    of the last few weeks (for done / missed) and the current phase."""
    from backend.api.overview import _dataset, _plan_stamp, _status
    from backend.engine import overview as O
    from backend.engine import planning
    ds = _dataset()
    today = O.day_to_date(ds.today)
    key = (id(ds), today, _plan_stamp())
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    st = _status(ds, today)
    cur = O.week_plan(ds, st, today)
    monday = dt.date.fromisoformat(cur["week"]["start"])
    cap = monday + dt.timedelta(weeks=P.MAX_WEEKS, days=6)
    ph = st.phase
    phase_end = dt.date.fromisoformat(ph.end) if ph else cap
    horizon = min(cap, max(phase_end, monday + dt.timedelta(days=13)))
    phases = [{"kind": p.kind, "start": p.start, "end": p.end}
              for p in planning.phases(st.plan, today - dt.timedelta(days=400), today + dt.timedelta(days=400))]
    weeks = P.project_weeks(cur, phases, horizon, ds.athlete.ctlconstant)
    since = monday - dt.timedelta(weeks=4)
    acts = [O.activity_row(w) for w in O.workouts_between(ds, since, today + dt.timedelta(days=1))]
    last_act = max((O.wdate(w) for w in ds.workouts if O.wdate(w) <= today), default=None)
    out = {"cur": cur, "weeks": weeks, "activities": acts, "today": cur["week"]["today"],
           "horizon_end": horizon.isoformat(), "thresholds": cur.get("thresholds") or {},
           "phase": None if ph is None else {"kind": ph.kind, "label": ph.label, "start": ph.start, "end": ph.end},
           "phase_push_end": min(phase_end, today + dt.timedelta(weeks=P.MAX_WEEKS)).isoformat(),
           "max_weeks": P.MAX_WEEKS, "last_activity": last_act.isoformat() if last_act else None,
           "cc": ds.athlete.ctlconstant, "ac": ds.athlete.atlconstant}
    with _lock:
        _cache.clear()
        _cache[key] = out
    return out


async def _covered(db: AsyncSession, last_activity: Optional[str]) -> Optional[str]:
    """Last day the synced data covers: the latest activity's day, or the day
    before the latest successful sync, whichever is later."""
    from sqlalchemy import select
    from backend.db.models import SyncState
    days = [last_activity] if last_activity else []
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == 1))).scalar_one_or_none()
    for t in (getattr(st, "coros_last_sync_at", None), getattr(st, "last_sync_at", None)):
        if t is not None:
            days.append((t.date() - dt.timedelta(days=1)).isoformat())
    return max(days) if days else None


async def _inputs(db: Optional[AsyncSession] = None) -> dict:
    inp = await run_in_threadpool(_compute_inputs)
    if db is not None:
        inp = {**inp, "covered": await _covered(db, inp.get("last_activity"))}
    return inp


# one writer at a time: two tabs / a preview racing a push must not generate
# the same week twice (fresh uids each time) and push the duplicates
_locks: dict = {}


def _wlock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()          # one lock per event loop (tests run several)
    lk = _locks.get(id(loop))
    if lk is None:
        lk = _locks[id(loop)] = asyncio.Lock()
    return lk


def _today(inp: dict) -> str:
    return max(inp["today"], CW.real_today().isoformat())


async def _ensure(db: AsyncSession, inp: dict) -> None:
    """First visit of a week, or last week's sessions still open: reconcile, so the
    week is generated into the table and leftovers are marked done / missed."""
    ws = inp["cur"]["week"]["start"]
    if not await PS.initialized(db, ws) or await PS.has_leftovers(db, ws):
        await PS.plan_reconcile(db, inp, apply=True)


def _range(scope: str, day: Optional[str], inp: dict) -> tuple[str, str]:
    today = _today(inp)
    if scope not in SCOPES:
        raise HTTPException(400, f"scope must be one of {SCOPES}")
    d = day or today
    try:
        dt.date.fromisoformat(d)
    except ValueError:
        raise HTTPException(400, f"bad day {d!r}")
    if scope == "day":
        return d, d
    if scope == "week":
        a = R.monday_of(d)
        b = (dt.date.fromisoformat(a) + dt.timedelta(days=6)).isoformat()
        return max(a, today), b
    return today, inp["phase_push_end"]


def _view(s: dict, inp: dict, rows: dict, today: str) -> dict:
    v = dict(s)
    if s["state"] == "active":
        v["coros"] = CW.status_of(PS.push_dict(s), inp["thresholds"], rows.get(s["uid"]), today)
    elif s["uid"] in rows:
        v["coros"] = {"status": "pushed_" + s["state"], **CW._row_view(rows[s["uid"]])}
    return v


def _meta(inp: dict) -> dict:
    return {"today": _today(inp), "week": inp["cur"]["week"], "horizon_end": inp["horizon_end"],
            "phase": inp["phase"], "phase_push_end": inp["phase_push_end"], "max_weeks": inp["max_weeks"],
            "weeks": [{k: w[k] for k in ("start", "phase", "mode", "mode_label", "hours", "tss", "provisional")}
                      for w in inp["weeks"]]}


@router.get("/sessions")
async def sessions(start: Optional[str] = None, end: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    async with _wlock():
        await _ensure(db, inp)
        every = await PS.load(db)
    ss = [s for s in every if not (start or end) or (s.get("day") and (not start or s["day"] >= start)
                                                     and (not end or s["day"] <= end))]
    today = _today(inp)
    rows = await CW.all_rows(db)
    return {**_meta(inp), "summary": _summary(every, inp),
            "sessions": [_view(s, inp, rows, today) for s in ss if s["state"] != "deleted"
                         and s["state"] != "superseded"]}


def _summary(every: list[dict], inp: dict) -> dict:
    """Bars + PMC projection from the stored plan (edits included)."""
    cur = inp["cur"]
    load = cur.get("load") or {}
    return PS.plan_summary(every, cur["week"]["start"], inp["today"], float(load.get("ctl_today") or 0.0),
                           float(load.get("atl_today") or 0.0), float(inp.get("cc") or 42.0),
                           float(inp.get("ac") or 7.0), inp.get("horizon_end"))


def _err(e: Exception):
    return HTTPException(400, str(e))


@router.post("/sessions")
async def add_session(data: dict = Body(...), db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    try:
        async with _wlock():
            return await PS.add(db, data, _today(inp))
    except PS.PlanError as e:
        raise _err(e)


@router.patch("/sessions/{uid}")
async def edit_session(uid: str, patch: dict = Body(...), db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    try:
        async with _wlock():
            return await PS.edit(db, uid, patch, _today(inp))
    except PS.PlanError as e:
        raise _err(e)


@router.delete("/sessions/{uid}")
async def delete_session(uid: str, db: AsyncSession = Depends(get_db)):
    try:
        async with _wlock():
            return await PS.delete(db, uid)
    except PS.PlanError as e:
        raise HTTPException(404, str(e))


@router.get("/reconcile")
async def reconcile_preview(db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    async with _wlock():
        await _ensure(db, inp)
        _, changes = await PS.plan_reconcile(db, inp, apply=False)
    return {**_meta(inp), "changes": changes, "by_day": R.by_day(changes)}


@router.post("/reconcile")
async def reconcile_apply(db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    async with _wlock():
        await _ensure(db, inp)
        _, changes = await PS.plan_reconcile(db, inp, apply=True)
    return {**_meta(inp), "changes": changes, "by_day": R.by_day(changes)}


def _in_range(ss: list[dict], a: str, b: str) -> list[dict]:
    return [s for s in ss if s["state"] == "active" and s.get("day") and a <= s["day"] <= b]


@router.get("/push-coros/preview")
async def push_preview(scope: str = "week", day: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    a, b = _range(scope, day, inp)
    today = _today(inp)
    async with _wlock():
        await _ensure(db, inp)
        new, changes = await PS.plan_reconcile(db, inp, apply=False)
    rows = await CW.all_rows(db)
    todo = [_view(s, inp, rows, today) for s in _in_range(new, a, b)]
    pushable = [s for s in todo if s["coros"]["status"] not in ("skipped", "done")]
    will = [s for s in pushable if s["coros"]["status"] != "pushed"]
    missed = [s for s in new if s["state"] == "missed" and s["uid"] in rows]
    return {**_meta(inp), "scope": scope, "start": a, "end": b, "sessions": todo,
            "count": len(pushable), "to_send": len(will), "unchanged": len(pushable) - len(will),
            "skipped": [s for s in todo if s["coros"]["status"] == "skipped"],
            "missed_to_remove": len(missed), "changes": changes, "by_day": R.by_day(changes)}


def _auth(e: CW.CorosAuthError):
    return HTTPException(401, {"error": "COROS_AUTH_REQUIRED", "detail": str(e), "hint": "到設定頁重新登入 COROS"})


@router.post("/push-coros")
async def push(scope: str = "week", day: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    a, b = _range(scope, day, inp)
    today = _today(inp)
    async with _wlock():
        await _ensure(db, inp)
        new, changes = await PS.plan_reconcile(db, inp, apply=True)
        live = {s["uid"] for s in new if s["state"] in ("active", "done", "missed")}
        rows = await CW.all_rows(db)
        # pushed sessions gone from the plan (deleted / superseded / regenerated away);
        # past-day ones stay, see push_sessions. Missed ones are removed separately.
        stale = [k for k in rows if k not in live]
        missed = [s["uid"] for s in new if s["state"] == "missed" and s["uid"] in rows]
        try:
            res = await CW.push_sessions(db, [PS.push_dict(s) for s in _in_range(new, a, b)], inp["thresholds"],
                                         today, stale_keys=stale, missed_keys=missed)
        except CW.CorosAuthError as e:
            raise _auth(e)
    return {"scope": scope, "start": a, "end": b, "changes": changes, **res}


@router.delete("/push-coros")
async def unpush(scope: str = "week", day: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    a, b = _range(scope, day, inp)
    async with _wlock():
        rows = await CW.all_rows(db)
        keys = [k for k, r in rows.items() if r.day and a <= r.day <= b]
        try:
            return {"scope": scope, "start": a, "end": b, "removed": await CW.remove_keys(db, keys)}
        except CW.CorosAuthError as e:
            raise _auth(e)
