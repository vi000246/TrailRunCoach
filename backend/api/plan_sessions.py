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


# ---------------------------------------------------------------------------
# 課表 page (static/schedule.html): a month / week calendar over the stored plan
#
#   GET /api/v1/overview/plan/calendar?start=&end=   sessions + activities + phases
#                                                    + week summaries + COROS state
#   GET /api/v1/overview/plan/schedule/page          the page
# ---------------------------------------------------------------------------

MAX_CAL_DAYS = 120
KIND_TARGET = {"easy": "z2", "long": "long", "hike": "long", "quality": "threshold"}


def _range_extras(start: str, end: str) -> dict:
    """Completed activities and training phases in [start, end] (reads the dataset;
    tests replace this)."""
    from backend.api.overview import _dataset, _status
    from backend.engine import overview as O
    from backend.engine import planning
    ds = _dataset()
    today = O.day_to_date(ds.today)
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    acts = [O.activity_row(w) for w in O.workouts_between(ds, a, b + dt.timedelta(days=1))]
    st = _status(ds, today)
    lo, hi = min(a, today) - dt.timedelta(days=400), max(b, today) + dt.timedelta(days=400)
    phases = [{"kind": p.kind, "label": p.label, "start": p.start, "end": p.end}
              for p in planning.phases(st.plan, lo, hi) if p.end >= start and p.start <= end]
    return {"activities": acts, "phases": phases, "tph": O._tss_per_hour(ds, today)}


# TSS per hour of a planned session by kind: week_plan() / projection use the
# athlete's median TSS/h per category (road for easy / long, hike, strength) and
# fixed rates for the hard sessions (quality ≈ 70, test 75).
KIND_CATEGORY = {"easy": "road", "long": "road", "hike": "hike", "strength": "strength"}
HARD_RATE = {"quality": 70.0, "test": 75.0}


def tss_rates(tph: Optional[dict], sessions: list[dict], fallback: float = 50.0) -> dict[str, float]:
    """TSS per hour per session kind. The generator's own rate (tss / minutes of
    the unedited auto sessions of that kind, median) wins, so an estimate for an
    edited or added session matches what the plan would have given it."""
    import statistics
    from backend.engine.overview import TSS_PER_HOUR_DEFAULT
    tph = {**TSS_PER_HOUR_DEFAULT, **(tph or {})}
    out = {k: float(tph.get(KIND_CATEGORY.get(k, ""), 0.0) or HARD_RATE.get(k, fallback)) for k in PS.KINDS}
    seen: dict[str, list[float]] = {}
    for s in sessions:
        if s.get("origin") == "auto" and not s.get("edited") and (s.get("minutes") or 0) > 0 and (s.get("tss") or 0) > 0:
            seen.setdefault(s["kind"], []).append(float(s["tss"]) / s["minutes"] * 60.0)
    for k, v in seen.items():
        if k in out:
            out[k] = round(statistics.median(v), 1)
    return out


def est_tss(s: dict, rates: dict) -> float:
    return float(s.get("tss") or 0.0) or (s.get("minutes") or 0) / 60.0 * rates.get(s.get("kind"), 50.0)


def _week_rows(start: str, end: str, sessions: list[dict], acts: list[dict], phases: list[dict],
               proj: list[dict], rates: dict, today: Optional[str] = None) -> list[dict]:
    """Per Monday in range: planned (active + done + missed, strength time excluded
    like week_plan()) vs done (the activities), the phase of the week, and the
    week's 完成度 over the days up to today (engine/compliance.py)."""
    from backend.engine import compliance as C
    by_start = {w["start"]: w for w in proj}
    out = []
    d = dt.date.fromisoformat(R.monday_of(start))
    last = dt.date.fromisoformat(end)
    while d <= last:
        a, b = d.isoformat(), (d + dt.timedelta(days=6)).isoformat()
        ss = [s for s in sessions if s.get("day") and a <= s["day"] <= b and s["state"] in ("active", "done", "missed")]
        mins = sum(s["minutes"] or 0 for s in ss if s["kind"] != "strength")
        tss = sum(est_tss(s, rates) for s in ss)
        aa = [x for x in acts if a <= (x.get("date") or "") <= b]
        mid = (d + dt.timedelta(days=3)).isoformat()
        ph = next((p for p in phases if p["start"] <= mid <= p["end"]), None) \
            or next((p for p in phases if p["start"] <= b and p["end"] >= a), None)
        w = by_start.get(a) or {}
        comp = None
        if today and a <= today:
            upto = min(b, today)
            # today counts once it's done (the day isn't over yet)
            sp = [s for s in ss if s["day"] < today or (s["day"] <= upto and s["state"] == "done")]
            ap = [x for x in aa if x["date"] <= upto]
            comp = C.week_compliance(sum(est_tss(s, rates) for s in sp), sum(float(x.get("tss") or 0) for x in ap),
                                     sum(s["minutes"] or 0 for s in sp if s["kind"] != "strength") / 60.0,
                                     sum(float(x.get("moving_s") or 0) for x in ap) / 3600.0) if sp else None
        out.append({"start": a, "end": b, "compliance": comp,
                    "planned_hours": mins / 60.0, "planned_tss": tss,
                    "done_hours": sum(float(x.get("moving_s") or 0) for x in aa) / 3600.0,
                    "done_tss": sum(float(x.get("tss") or 0) for x in aa),
                    "phase": ph["kind"] if ph else w.get("phase"), "phase_label": ph["label"] if ph else None,
                    "mode_label": w.get("mode_label"), "provisional": bool(w.get("provisional"))})
        d += dt.timedelta(days=7)
    return out


async def _coros_state(db: AsyncSession, views: list[dict]) -> dict:
    from sqlalchemy import select
    from backend.db.models import SyncState
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == 1))).scalar_one_or_none()
    authed = bool(st and st.coros_access_token)
    exp = getattr(st, "coros_token_expires", None) if st else None
    if authed and exp is not None:
        exp = exp if exp.tzinfo else exp.replace(tzinfo=dt.timezone.utc)
        authed = dt.datetime.now(dt.timezone.utc) < exp
    rows = await CW.all_rows(db)
    last = max((r.pushed_at for r in rows.values() if r.pushed_at), default=None)
    n = lambda k: sum(1 for s in views if s["state"] == "active" and (s.get("coros") or {}).get("status") == k)
    return {"authenticated": authed, "last_pushed_at": last.isoformat() if last else None,
            "outdated": n("outdated"), "failed": n("failed"), "pushed": n("pushed") + n("updated"),
            "not_pushed": n("not_pushed")}


@router.get("/calendar")
async def calendar(start: str, end: str, db: AsyncSession = Depends(get_db)):
    """Everything the 課表 calendar needs for [start, end] (≤ 120 days)."""
    try:
        a, b = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
    except ValueError:
        raise HTTPException(400, "start / end must be YYYY-MM-DD")
    if b < a:
        raise HTTPException(400, "end before start")
    if (b - a).days > MAX_CAL_DAYS:
        raise HTTPException(400, f"at most {MAX_CAL_DAYS} days")
    start, end = a.isoformat(), b.isoformat()
    body = await sessions(start=None, end=None, db=db)      # reconciles on first visit, like the week view
    every = body["sessions"]
    inp = await _inputs()
    extras = await run_in_threadpool(_range_extras, start, end)
    tph = float(((inp["cur"].get("target") or {}).get("tss_per_hour")) or 50.0)
    rates = tss_rates(extras.get("tph"), every, tph)
    from backend.engine import compliance as C
    ss = []
    for s in every:
        if s.get("day") and start <= s["day"] <= end:
            est = est_tss(s, rates)
            ss.append({**s, "tss_est": round(est, 1), "compliance": C.session_compliance(s, est)})
    tt = P.target_texts(inp["thresholds"] or {})
    return {**{k: v for k, v in body.items() if k != "sessions"}, "start": start, "end": end,
            "sessions": ss, "activities": extras["activities"], "phases": extras["phases"],
            "week_rows": _week_rows(start, end, ss, extras["activities"], extras["phases"], inp["weeks"], rates,
                                    body["today"]),
            "compliance_levels": C.COMPLIANCE,
            "thresholds": inp["thresholds"], "tss_per_hour": tph, "tss_rates": rates,
            "targets": {k: tt.get(v, "") for k, v in KIND_TARGET.items()},
            "kinds": PS.KINDS, "default_titles": PS.DEFAULT_TITLES,
            "coros": await _coros_state(db, every)}


@router.get("/schedule/page", include_in_schema=False)
def schedule_page():
    from fastapi.responses import FileResponse
    from backend.api.overview import STATIC
    return FileResponse(STATIC / "schedule.html")
