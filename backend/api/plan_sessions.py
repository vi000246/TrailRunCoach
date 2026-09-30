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
import functools
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


def _compute_inputs(blackouts: Optional[list] = None) -> dict:
    """week_plan() for this week, the projection to the horizon, activities
    of the last few weeks (for done / missed) and the current phase.
    `blackouts`: a candidate 不排課日期 list (preview before saving); None = the stored one."""
    from backend.api.overview import _dataset, _plan_stamp, _status
    from backend.engine import blackouts as BL
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import planning
    ds = _dataset()
    today = O.day_to_date(ds.today)
    prefs = PP.load()
    bos = BL.load() if blackouts is None else BL.from_list(blackouts)
    # saving 課表偏好 or 不排課日期 regenerates
    key = (id(ds), today, _plan_stamp(), prefs.stamp(), BL.stamp(bos))
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    st = _status(ds, today)
    cur = O.week_plan(ds, st, today, prefs=prefs, blackouts=bos)
    monday = dt.date.fromisoformat(cur["week"]["start"])
    cap = monday + dt.timedelta(weeks=P.MAX_WEEKS, days=6)
    ph = st.phase
    phase_end = dt.date.fromisoformat(ph.end) if ph else cap
    horizon = min(cap, max(phase_end, monday + dt.timedelta(days=13)))
    phases = [{"kind": p.kind, "start": p.start, "end": p.end}
              for p in planning.phases(st.plan, today - dt.timedelta(days=400), today + dt.timedelta(days=400))]
    try:
        from backend.engine import heat_data as HD
        heat_acts = HD.exposures()[0]
    except Exception:                       # noqa: BLE001
        heat_acts = []
    weeks = P.project_weeks(cur, phases, horizon, ds.athlete.ctlconstant, ds.athlete.atlconstant, prefs=prefs,
                            blackouts=bos, events=st.plan.events, heat_acts=heat_acts)
    since = monday - dt.timedelta(weeks=4)
    acts = [O.activity_row(w) for w in O.workouts_between(ds, since, today + dt.timedelta(days=1))]
    last_act = max((O.wdate(w) for w in ds.workouts if O.wdate(w) <= today), default=None)
    out = {"cur": cur, "weeks": weeks, "activities": acts, "today": cur["week"]["today"],
           "horizon_end": horizon.isoformat(), "thresholds": cur.get("thresholds") or {},
           "phase": None if ph is None else {"kind": ph.kind, "label": ph.label, "start": ph.start, "end": ph.end},
           "phase_push_end": min(phase_end, today + dt.timedelta(weeks=P.MAX_WEEKS)).isoformat(),
           "max_weeks": P.MAX_WEEKS, "last_activity": last_act.isoformat() if last_act else None,
           "cc": ds.athlete.ctlconstant, "ac": ds.athlete.atlconstant, "prefs": prefs.to_dict(),
           "blackouts": [b.to_dict() for b in bos]}
    with _lock:
        while len(_cache) >= 3:                 # the stored plan + a preview or two
            _cache.pop(next(iter(_cache)))
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


async def _inputs(db: Optional[AsyncSession] = None, blackouts: Optional[list] = None) -> dict:
    if blackouts is None:
        inp = await run_in_threadpool(_compute_inputs)
    else:
        inp = await run_in_threadpool(functools.partial(_compute_inputs, blackouts))
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
        if b < today:
            # max(a, today) > b would be an empty range: preview / push / unpush
            # would silently do nothing
            raise HTTPException(400, f"{a}–{b} 這週已經過去（今天 {today}），不能推送或移除")
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
            "blackouts": inp.get("blackouts") or [],
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
            return await PS.add(db, data, _today(inp), blocked=PS.blocked_map(inp))
    except PS.PlanError as e:
        raise _err(e)


@router.patch("/sessions/{uid}")
async def edit_session(uid: str, patch: dict = Body(...), db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    try:
        async with _wlock():
            return await PS.edit(db, uid, patch, _today(inp), blocked=PS.blocked_map(inp))
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


def _decisions(body: Optional[dict]) -> dict:
    """{uid: move | delete} for edited sessions on a 不排課日期 (reconcile rule 6)."""
    d = (body or {}).get("decisions") or {}
    if not isinstance(d, dict) or any(v not in ("move", "delete") for v in d.values()):
        raise HTTPException(400, "decisions must be {uid: move | delete}")
    return {str(k): v for k, v in d.items()}


@router.post("/reconcile")
async def reconcile_apply(body: Optional[dict] = Body(None), db: AsyncSession = Depends(get_db)):
    dec = _decisions(body)
    inp = await _inputs(db)
    async with _wlock():
        await _ensure(db, inp)
        _, changes = await PS.plan_reconcile(db, inp, apply=True, decisions=dec)
    return {**_meta(inp), "changes": changes, "by_day": R.by_day(changes)}


def _in_range(ss: list[dict], a: str, b: str, blocked: Optional[dict] = None) -> list[dict]:
    """Active sessions in [a, b]; never one on a 不排課日期 (an edited session left
    there until the user decides isn't pushed, and a pushed copy is removed)."""
    return [s for s in ss if s["state"] == "active" and s.get("day") and a <= s["day"] <= b
            and s["day"] not in (blocked or {})]


@router.get("/push-coros/preview")
async def push_preview(scope: str = "week", day: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    a, b = _range(scope, day, inp)
    today = _today(inp)
    async with _wlock():
        await _ensure(db, inp)
        new, changes = await PS.plan_reconcile(db, inp, apply=False)
    rows = await CW.all_rows(db)
    bl = PS.blocked_map(inp)
    todo = [_view(s, inp, rows, today) for s in _in_range(new, a, b, bl)]
    pushable = [s for s in todo if s["coros"]["status"] not in ("skipped", "done")]
    will = [s for s in pushable if s["coros"]["status"] != "pushed"]
    missed = [s for s in new if s["state"] == "missed" and s["uid"] in rows]
    on_bl = [s for s in _on_blocked(new, bl, today) if s["uid"] in rows]
    return {**_meta(inp), "scope": scope, "start": a, "end": b, "sessions": todo,
            "count": len(pushable), "to_send": len(will), "unchanged": len(pushable) - len(will),
            "skipped": [s for s in todo if s["coros"]["status"] == "skipped"],
            "missed_to_remove": len(missed), "blackout_to_remove": len(on_bl),
            "changes": changes, "by_day": R.by_day(changes)}


def _on_blocked(ss: list[dict], blocked: dict, today: str) -> list[dict]:
    return [s for s in ss if s["state"] == "active" and s.get("day") and s["day"] >= today and s["day"] in blocked]


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
        bl = PS.blocked_map(inp)
        # an edited session still on a 不排課日期 (no decision yet) comes off COROS
        stale += [s["uid"] for s in _on_blocked(new, bl, today) if s["uid"] in rows]
        missed = [s["uid"] for s in new if s["state"] == "missed" and s["uid"] in rows]
        try:
            res = await CW.push_sessions(db, [PS.push_dict(s) for s in _in_range(new, a, b, bl)], inp["thresholds"],
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
# 課表偏好 (engine/plan_prefs.py) and same-load conversion (engine/equivalence.py)
#
#   GET /api/v1/overview/plan/prefs               the stored preferences (+ defaults)
#   PUT /api/v1/overview/plan/prefs               validate + save; the page then opens
#                                                 the reconcile preview (GET /reconcile)
#   GET /api/v1/overview/plan/equivalence         the athlete's time model + LOO backtest
#   POST /api/v1/overview/plan/equivalence/design {mode, minutes, climb_per_km} -> km / gain
# ---------------------------------------------------------------------------

def _prefs_body(p) -> dict:
    from backend.engine import plan_prefs as PP
    from backend.engine import quality_gate as QG
    # gate_options: the 間歇門檻 hover texts (the page adds "usable with your data"
    # from GET /prefs/gate, which needs the dataset)
    return {"prefs": p.to_dict(), "defaults": PP.Prefs().to_dict(), "active": p.active,
            "gate_options": QG.option_texts()}


@router.get("/prefs/gate")
def get_prefs_gate():
    """間歇門檻 availability per mode on the athlete's data (status.i_gate's extra.options)."""
    from backend.api.overview import _dataset, _status
    from backend.engine import overview as O
    ds = _dataset()
    st = _status(ds, O.day_to_date(ds.today))
    g = next((i.extra for i in st.indicators if i.id == "gate"), None) or {}
    return {"options": g.get("options") or {}, "mode": g.get("mode"), "resolved": g.get("resolved"),
            "state": g.get("state"), "verdict": next((i.verdict for i in st.indicators if i.id == "gate"), "")}


@router.get("/prefs")
async def get_prefs(db: AsyncSession = Depends(get_db)):
    from backend.engine import plan_prefs as PP
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db)
    return _prefs_body(PP.from_settings({k: await repo.get(k) for k in PP.KEY_FIELDS}))


@router.put("/prefs")
async def put_prefs(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """The whole preference set (fields of plan_prefs.Prefs; missing = default)."""
    from backend.engine import plan_prefs as PP
    from backend.settings.repository import SettingsRepository
    try:
        p = PP.from_body(body)
        PP.check(p)
        repo = SettingsRepository(db)
        for k, v in p.settings().items():
            await repo.set(k, v)
    except (ValueError, TypeError) as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.commit()
    return _prefs_body(p)


# ---------------------------------------------------------------------------
# 不排課日期 (engine/blackouts.py)
#
#   GET  /api/v1/overview/plan/blackouts           the stored ranges
#   POST /api/v1/overview/plan/blackouts/preview   {blackouts} -> the reconcile preview
#                                                  with that list, nothing saved
#   PUT  /api/v1/overview/plan/blackouts           {blackouts, decisions} -> save, then
#                                                  reconcile with the user's move / delete
#                                                  choices for edited sessions on those days
# ---------------------------------------------------------------------------

def _bl_body(body) -> list[dict]:
    from backend.engine import blackouts as BL
    try:
        return BL.normalize((body or {}).get("blackouts"))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@router.get("/blackouts")
async def get_blackouts(db: AsyncSession = Depends(get_db)):
    from backend.engine import blackouts as BL
    from backend.settings.repository import SettingsRepository
    return {"blackouts": await SettingsRepository(db).get(BL.KEY)}


@router.post("/blackouts/preview")
async def preview_blackouts(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    cand = _bl_body(body)
    saved = await _inputs(db)
    inp = {**(await _inputs(blackouts=cand)), "covered": saved.get("covered")}
    async with _wlock():
        await _ensure(db, saved)                  # first visit: generate with what is stored
        _, changes = await PS.plan_reconcile(db, inp, apply=False)
    return {**_meta(inp), "blackouts": cand, "changes": changes, "by_day": R.by_day(changes)}


@router.put("/blackouts")
async def put_blackouts(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import blackouts as BL
    from backend.settings.repository import SettingsRepository
    cand = _bl_body(body)
    dec = _decisions(body)
    try:
        await SettingsRepository(db).set(BL.KEY, cand)
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.commit()
    inp = await _inputs(db, blackouts=cand)       # = what was just stored (same cache key)
    async with _wlock():
        await _ensure(db, inp)
        _, changes = await PS.plan_reconcile(db, inp, apply=True, decisions=dec)
    return {**_meta(inp), "blackouts": cand, "changes": changes, "by_day": R.by_day(changes)}


_eq_lock = threading.Lock()
_eq_cache: dict = {}


def _equivalence() -> dict:
    from backend.api.overview import _dataset
    from backend.engine import equivalence as E
    from backend.engine import overview as O
    ds = _dataset()
    today = O.day_to_date(ds.today)
    aet = (_compute_inputs().get("thresholds") or {}).get("aet")
    key = (id(ds), today, aet)
    with _eq_lock:
        hit = _eq_cache.get(key)
    if hit is None:
        hit = E.summary(ds, today, aet)
        with _eq_lock:
            _eq_cache.clear()
            _eq_cache[key] = hit
    return hit


@router.get("/equivalence")
async def equivalence():
    return await run_in_threadpool(_equivalence)


@router.post("/equivalence/design")
async def equivalence_design(body: dict = Body(...)):
    from backend.engine import equivalence as E
    mode = body.get("mode")
    if mode not in E.MODES:
        raise HTTPException(400, f"mode must be one of {E.MODES}")
    try:
        minutes = float(body.get("minutes"))
        density = float(body.get("climb_per_km") or 0.0)
    except (TypeError, ValueError):
        raise HTTPException(400, "minutes / climb_per_km must be numbers")
    if not (0 < minutes <= 1440 and 0 <= density <= 300):
        raise HTTPException(400, "minutes 0-1440, climb_per_km 0-300")
    s = await run_in_threadpool(_equivalence)
    m = s["model"]
    model = E.Model(v_flat_kmh=m["v_flat_kmh"], v_flat_source=m["v_flat_source"], road_n=m["road_n"],
                    modes={k: E.ModeModel(**v) for k, v in m["modes"].items()})
    return {**E.design(model, mode, minutes, density), "estimate": s["estimate"].get(mode, False)}


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
    goal_d = ((st.goals.get("targets") or {}).get("climb_per_km") or {}).get("value")
    return {"activities": acts, "phases": phases, "tph": O._tss_per_hour(ds, today), "goal_climb_per_km": goal_d}


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
    out["heat_passive"] = 0.0          # a bath / sauna: no TSS conversion was found (heat-acclimation.md §5.4)
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


def _plan_notes(inp: dict, start: str, end: str) -> list[dict]:
    """The generator's notes for the weeks in view (課表偏好: capped weeks, CP test
    exempt, soft-cap excess; 不排課日期: hours lost, the step after it), each with
    its week start."""
    cur = inp.get("cur") or {}
    weeks = [((cur.get("week") or {}).get("start"), cur.get("notes") or [])] + \
        [(w.get("start"), w.get("notes") or []) for w in inp.get("weeks") or []]
    out = []
    for ws, notes in weeks:
        if not ws:
            continue
        we = (dt.date.fromisoformat(ws) + dt.timedelta(days=6)).isoformat()
        if we < start or ws > end:
            continue
        out += [{"week_start": ws, **n} for n in notes if n.get("src") in ("prefs", "blackout")]
    return out


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
            "prefs": inp.get("prefs"), "goal_climb_per_km": extras.get("goal_climb_per_km"),
            "plan_notes": _plan_notes(inp, start, end),
            "coros": await _coros_state(db, every)}


@router.get("/schedule/page", include_in_schema=False)
def schedule_page():
    from fastapi.responses import FileResponse
    from backend.api.overview import STATIC
    return FileResponse(STATIC / "schedule.html")
