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
from backend.engine import plan_match as PM
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
    from backend.engine import b2b as B2B
    from backend.engine import blackouts as BL
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import planning
    ds = _dataset()
    today = O.day_to_date(ds.today)
    prefs = PP.load()
    bos = BL.load() if blackouts is None else BL.from_list(blackouts)
    acc = B2B.load_accepted()                    # accepted B2B weekends (the user's own days)
    from backend.engine.wko5expr.datasource import read_setting
    auto_on = read_setting("plan.auto.enabled", True) is not False
    # saving 課表偏好 or 不排課日期, or accepting / cancelling a B2B, regenerates
    key = (id(ds), today, _plan_stamp(), prefs.stamp(), BL.stamp(bos), auto_on, B2B.accepted_stamp(acc))
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    st = _status(ds, today)
    cur = O.week_plan(ds, st, today, prefs=prefs, blackouts=bos, b2b_accepted=acc)
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
                            blackouts=bos, events=st.plan.events, heat_acts=heat_acts, b2b_accepted=acc)
    since = monday - dt.timedelta(weeks=4)
    acts = activity_rows(ds, since, today + dt.timedelta(days=1))
    last_act = max((O.wdate(w) for w in ds.workouts if O.wdate(w) <= today), default=None)
    out = {"cur": cur, "weeks": weeks, "activities": acts, "today": cur["week"]["today"],
           "horizon_end": horizon.isoformat(), "thresholds": cur.get("thresholds") or {},
           "phase": None if ph is None else {"kind": ph.kind, "label": ph.label, "start": ph.start, "end": ph.end},
           "phase_push_end": min(phase_end, today + dt.timedelta(weeks=P.MAX_WEEKS)).isoformat(),
           "max_weeks": P.MAX_WEEKS, "last_activity": last_act.isoformat() if last_act else None,
           "cc": ds.athlete.ctlconstant, "ac": ds.athlete.atlconstant, "prefs": prefs.to_dict(),
           "blackouts": [b.to_dict() for b in bos],
           "days_to_next_a": (st.goals or {}).get("days_to_next_a"),
           # engine/zone_events.py (status 測試 indicator): retests suggested, zones recomputed
           "zone": {"suggestions": list(getattr(st, "test_suggestions", None) or []),
                    "events": next(((i.extra or {}).get("zone_events") or [] for i in st.indicators
                                    if i.id == "testing"), [])},
           "adapt": _adapt_ctx(ds, st, cur, monday, today, auto_on)}
    with _lock:
        while len(_cache) >= 3:                 # the stored plan + a preview or two
            _cache.pop(next(iter(_cache)))
        _cache[key] = out
    return out


def activity_rows(ds, a: dt.date, b: dt.date) -> list[dict]:
    """overview.activity_row for the workouts in [a, b) plus `hard_s`: seconds at /
    above threshold (power ≥ 95 % CP or HR ≥ LTHR, the generator's done rule for
    a quality session) — engine/plan_match.py tells an interval run from an easy one."""
    from backend.engine import overview as O
    ws = O.workouts_between(ds, a, b)
    hard: dict = {}
    runs = [w for w in ws if O.category(w) in ("road", "trail", "hike")]
    if runs:
        try:
            hard = O._hard_seconds(ds, runs, int(O.date_to_day(a)), int(O.date_to_day(b)))
        except Exception:                   # noqa: BLE001 — no intensity verdict, still matched
            hard = {}
    out = []
    for w in ws:
        r = O.activity_row(w)
        if w in runs:
            r["hard_s"] = hard.get(w.idx, 0.0) if hard else None
        out.append(r)
    return out


def _adapt_ctx(ds, st, cur: dict, monday: dt.date, today: dt.date, enabled: bool) -> dict:
    """engine/adapt.py's context: per-run review numbers of this week's runs
    (workout_review.measure, disk-memoised), the actual CTL ramp (status
    fitness indicator), TSB today and the first day sessions can still go on."""
    from backend.engine import overview as O
    reviews: dict = {}
    if enabled:
        try:
            from backend.engine import workout_review as WR
            for w in O.workouts_between(ds, monday, today + dt.timedelta(days=1)):
                if O.category(w) not in ("road", "trail"):
                    continue
                m = WR.measure(ds, w) or {}
                reviews[w.idx] = {k: m.get(k) for k in ("avg_hr", "aet", "over_aet_s", "hr_s", "avg_power", "cp")}
                reviews[w.idx]["tss"] = O._n(w.metrics.get("tss"))
            WR._flush(ds)
        except Exception:                   # noqa: BLE001 — a review failure never breaks the plan
            pass
    ramp = next(((i.extra or {}).get("ramp_week") for i in st.indicators if i.id == "fitness"), None)
    load = cur.get("load") or {}
    done_today = any(a.get("date") == today.isoformat() for a in (cur.get("done") or {}).get("activities") or [])
    return {"enabled": enabled, "reviews": reviews, "ramp": ramp, "tsb": load.get("tsb_today"),
            "first_free": (today + dt.timedelta(days=1 if done_today else 0)).isoformat()}


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
        inp = {**inp, "covered": await _covered(db, inp.get("last_activity")),
               "unlinked": await _unlinked(db, inp.get("activities") or [])}
    return inp


async def _unlinked(db: AsyncSession, acts: list[dict]) -> set:
    """Activity indexes the user unlinked from a session (plan_match: never auto-matched)."""
    from backend.settings.repository import SettingsRepository
    try:
        entries = await SettingsRepository(db).get(PS.UNLINKED_KEY)
    except Exception:                        # noqa: BLE001 — a missing table in an old DB
        entries = []
    return PS.unlinked_indexes(entries or [], acts)


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
    if not await PS.initialized(db, ws):
        await PS.plan_reconcile(db, inp, apply=True)
    elif await PS.has_leftovers(db, ws):
        # a 課表待確認 proposal is waiting (engine/plan_auto.py): don't apply it behind
        # the user's back; the automatic run already applied the done / missed part
        from backend.engine import plan_auto as PA
        if await PA.pending(db) is None:
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
        # a run synced since the last reconcile shows on its session right away
        new, ch = PS.match_only(every, inp)
        if ch:
            await PS.save(db, new)
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


def day_cap(prefs, day: Optional[str]) -> Optional[float]:
    """The time cap of a day (課表偏好): Mon–Fri the weekday cap, Sat / Sun the long-day cap."""
    if prefs is None or not prefs.active or not day:
        return None
    wd = dt.date.fromisoformat(day).weekday()
    c = prefs.long_cap if wd >= 5 else prefs.cap_weekday
    return float(c) if c is not None else None


def _variant_ctx(inp: dict, day: Optional[str]) -> dict:
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    prefs = PP.load()
    gate = (inp.get("cur") or {}).get("quality_gate") or {}
    before = dt.date.fromisoformat(day) if day else dt.date.fromisoformat(_today(inp))
    return {"th": inp.get("thresholds") or {}, "prefs": prefs, "cap": day_cap(prefs, day),
            "history": O.variant_history(before, gate), "gate": gate}


def _with_variant(data: dict, inp: dict, rung: Optional[str], day: Optional[str]) -> dict:
    """A body that names a library variant (`variant_key`, optional `variant_reps`): the
    stored variant fields, built server-side (interval_library.variant_patch). The text
    fields the user typed win; the rest come from the library."""
    from backend.engine import interval_library as IL
    key = data.get("variant_key")
    if not key:
        return data
    ctx = _variant_ctx(inp, day or data.get("day"))
    try:
        vp = IL.variant_patch(key, rung, ctx["th"], ctx["prefs"], ctx["cap"], data.get("variant_reps"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    body = {k: v for k, v in data.items() if k not in ("variant_key", "variant_reps")}
    for k in ("title", "minutes", "target", "detail", "tss"):
        body.setdefault(k, vp[k])
    body["kind"] = "quality"
    body["_variant"] = {k: vp[k] for k in ("variant_key", "rung_key", "equiv", "swap", "swap_reason", "variant_reps",
                                           "variant_blocks", "source")}
    return body


@router.post("/sessions")
async def add_session(data: dict = Body(...), db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    data = _with_variant(data, inp, None, data.get("day"))
    if data.get("steps"):
        data = await _with_steps(data, inp, {})
    try:
        async with _wlock():
            return await PS.add(db, data, _today(inp), blocked=PS.blocked_map(inp))
    except PS.PlanError as e:
        raise _err(e)


@router.patch("/sessions/{uid}")
async def edit_session(uid: str, patch: dict = Body(...), db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    if patch.get("variant_key"):
        cur = next((s for s in await PS.load(db) if s["uid"] == uid), None)
        patch = _with_variant(patch, inp, (cur or {}).get("rung_key"), patch.get("day") or (cur or {}).get("day"))
    if patch.get("steps"):
        cur = next((s for s in await PS.load(db) if s["uid"] == uid), None)
        patch = await _with_steps(patch, inp, cur or {})
    try:
        async with _wlock():
            out = await PS.edit(db, uid, patch, _today(inp), blocked=PS.blocked_map(inp))
    except PS.PlanError as e:
        raise _err(e)
    if patch.get("day") and out.get("day"):
        await _b2b_moved(db, uid, out["day"])         # an accepted B2B day moved: the entry follows
    return out


# ---------------------------------------------------------------------------
# tests: the editor's 測試 templates, and due tests suggested (never scheduled)
#
#   GET  /api/v1/overview/plan/test-templates?day=     CP (quick / standard / race) and AeT
#                                                      (徐國峰 90 / UA 60 / UA 40 / Evoke 60 / Friel)
#   GET  /api/v1/overview/plan/test-suggestions        the due tests + the days that suit them
#   POST /api/v1/overview/plan/test-suggestions/schedule {kind, day}  → a custom test session
# ---------------------------------------------------------------------------

def _test_templates(th: dict, prefs) -> dict:
    from backend.engine import aet_test as AT
    from backend.engine import cp_protocols as CPP
    cp = []
    for p in CPP.PROTOCOLS:
        s = CPP.session_for(p)
        cp.append({"protocol": p, "label": CPP.TABLE[p]["label"], **({k: s.get(k) for k in (
            "title", "minutes", "target", "detail", "source", "tss")} if s else
            {"title": "用比賽當 CP 測試", "minutes": 0, "detail": CPP.NOTE_RACE, "none": True})})
    aet = []
    for p in AT.PROTOCOLS:
        s = AT.session(th or {}, AT.start_hr(None, (th or {}).get("lthr")), AT.start_power((th or {}).get("cp")),
                       None, p, None)
        aet.append({"protocol": p, "label": AT.PROTOCOLS[p]["label"], "tip": AT.protocol_tip(p),
                    **{k: s.get(k) for k in ("title", "minutes", "target", "detail", "source", "tss")},
                    "protocol_stored": s.get("protocol")})
    return {"cp": cp, "aet": aet}


@router.get("/test-templates")
async def test_templates():
    inp = await _inputs()
    from backend.engine import plan_prefs as PP
    return _test_templates(inp.get("thresholds") or {}, PP.load())


SUGGEST_DAYS = 14


def suggestion_days(sg: dict, stored: list[dict], today: str, prefs, blocked: dict) -> list[dict]:
    """The days in the next two weeks that suit a suggested test: an allowed, unblocked
    day with no hard session (long / quality / test) the day before, that day or the day
    after — except 徐國峰's 90-min test, which is the weekend LSD and takes the long
    run's own day. The AeT test keeps to Mon–Fri with 課表偏好 aet_test_days = weekday."""
    hard = {s["day"]: s for s in stored if s["state"] == "active" and s.get("day") and s["kind"] in ("long", "quality", "test")}
    longs = {d for d, s in hard.items() if s["kind"] == "long"}
    out = []
    d0 = dt.date.fromisoformat(today)
    for i in range(SUGGEST_DAYS):
        d = d0 + dt.timedelta(days=i)
        iso = d.isoformat()
        if iso in (blocked or {}) or (prefs is not None and not prefs.allowed(d)):
            continue
        near = [x for x in ((d + dt.timedelta(days=k)).isoformat() for k in (-1, 0, 1)) if x in hard]
        if sg.get("replaces_long"):
            if d.weekday() < 5 and iso not in longs:
                continue
            near = [x for x in near if x != iso or x not in longs]
            near = [x for x in near if hard[x]["kind"] != "long" or x == iso]
            if any(hard[x]["kind"] in ("quality", "test") for x in near):
                continue
            out.append({"day": iso, "note": "取代這天的長跑" if iso in longs else "週末平路 90 分"})
            continue
        if sg["kind"] == "aet" and getattr(prefs, "aet_test_days", "weekday") == "weekday" and d.weekday() >= 5:
            continue
        if near:
            continue
        out.append({"day": iso, "note": ""})
    # 課表偏好 偏好的星期: the preferred weekday(s) first
    pref = prefs.pref_of("aet_test" if sg["kind"] == "aet" else "cp_test") if prefs is not None else ()
    if pref:
        for d in out:
            if dt.date.fromisoformat(d["day"]).weekday() in pref:
                d["note"] = (d["note"] + " · " if d["note"] else "") + "你偏好的日子"
        out.sort(key=lambda d: (dt.date.fromisoformat(d["day"]).weekday() not in pref, d["day"]))
    return out


async def _suggestions(db: AsyncSession, inp: dict, dismissed: Optional[dict] = None) -> list[dict]:
    """This week's due CP / AeT tests with their days. `dismissed` (the floating box's
    「不要」 / ✕, engine/suggestions.py): None = read it; {} = keep every one."""
    from backend.engine import plan_prefs as PP
    stored = await PS.load(db)
    today = _today(inp)
    prefs = PP.load()
    bl = PS.blocked_map(inp)
    if dismissed is None:
        dismissed = await _dismissed(db)
    mon = R.monday_of(today)
    out = []
    for sg in (inp.get("cur") or {}).get("test_suggestions") or []:
        from backend.engine.aet_test import is_aet_session
        # already put in by the athlete (this or a later day): no suggestion
        if any(s["kind"] == "test" and s["state"] in ("active", "done") and (s.get("day") or "") >= R.monday_of(today)
               and (is_aet_session(s) == (sg["kind"] == "aet")) for s in stored):
            continue
        if f"test:{sg['kind']}:{mon}" in dismissed:
            continue
        out.append({**sg, "days": suggestion_days(sg, stored, today, prefs, bl),
                    "label": f"建議做一次 {'AeT' if sg['kind'] == 'aet' else 'CP'} 測試（{sg['reason']}）— 要排在哪一天？"})
    return out


@router.get("/test-suggestions")
async def test_suggestions(db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    return {"suggestions": await _suggestions(db, inp)}


@router.post("/test-suggestions/schedule")
async def schedule_test(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    kind, day = body.get("kind"), body.get("day")
    sg = next((x for x in await _suggestions(db, inp) if x["kind"] == kind), None)
    if sg is None:
        raise HTTPException(400, "現在沒有這個測試的建議")
    return await _schedule_test(db, inp, sg, day)


async def _schedule_test(db: AsyncSession, inp: dict, sg: dict, day: Optional[str]) -> dict:
    if day not in {d["day"] for d in sg["days"]}:
        raise HTTPException(400, "這天不適合排測試（太靠近長跑或強度課、不是可練日，或在不排課日期內）")
    data = {**{k: v for k, v in sg["session"].items() if v is not None}, "day": day, "kind": "test"}
    try:
        async with _wlock():
            if sg.get("replaces_long"):
                for s in await PS.load(db):
                    if s["state"] == "active" and s["kind"] == "long" and s.get("day") == day:
                        await PS.delete(db, s["uid"])            # 徐國峰's test is that day's LSD
            return await PS.add(db, data, _today(inp), blocked=PS.blocked_map(inp))
    except PS.PlanError as e:
        raise _err(e)


# ---------------------------------------------------------------------------
# the floating suggestion box (static/suggestions.js; engine/suggestions.py)
#
#   GET  /api/v1/overview/plan/suggestions                 every active, not dismissed suggestion
#   POST /api/v1/overview/plan/suggestions/accept  {id, day, test?}   排入
#   POST /api/v1/overview/plan/suggestions/dismiss {id, action: declined | dismissed}   不要 / ✕
# ---------------------------------------------------------------------------

async def _dismissed(db: AsyncSession) -> dict:
    from backend.engine import suggestions as SG
    from backend.settings.repository import SettingsRepository
    v = await SettingsRepository(db).get(SG.KEY)
    return v if isinstance(v, dict) else {}


async def _save_setting(db: AsyncSession, key: str, value) -> None:
    from backend.settings.repository import SettingsRepository
    await SettingsRepository(db).set(key, value)
    await db.commit()


async def _accepted(db: AsyncSession) -> list[dict]:
    from backend.engine import b2b as B2B
    from backend.settings.repository import SettingsRepository
    v = await SettingsRepository(db).get(B2B.ACCEPTED_KEY)
    return [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []


def _busy_days(stored: list[dict], week: str) -> set:
    """Days of a week a B2B can't take: a done session, or one of the user's own
    (custom / edited) long, hard or hike sessions — the generator moves its own."""
    end = (dt.date.fromisoformat(week) + dt.timedelta(days=6)).isoformat()
    return {s["day"] for s in stored if s.get("day") and week <= s["day"] <= end and (
        s["state"] == "done" or (s["state"] == "active" and (s["origin"] == "custom" or s["edited"])
                                 and s["kind"] in ("long", "quality", "test", "hike")))}


async def _all_suggestions(db: AsyncSession, inp: dict) -> list[dict]:
    """Every suggestion computed now (dismissed ones included)."""
    from backend.engine import aet_test as AT
    from backend.engine import b2b as B2B
    from backend.engine import plan_prefs as PP
    from backend.engine import suggestions as SG
    stored = await PS.load(db)
    today = _today(inp)
    prefs = PP.load()
    bl = PS.blocked_map(inp)
    act = (inp.get("adapt") or {}).get("first_free") or today
    first = dt.date.fromisoformat(max(act, today))
    PR = prefs if prefs.active else None

    def pairs(sg: dict) -> list[dict]:
        return B2B.pair_options(dt.date.fromisoformat(sg["week"]), first, sg["minutes"], set(bl),
                                PR.allowed if PR is not None else None, PR.cap_weekday if PR is not None else None,
                                _busy_days(stored, sg["week"]), sg.get("long_day"))

    rows = SG.b2b_rows(inp, today, pairs)
    tests = await _suggestions(db, inp, dismissed={})
    rows += SG.test_rows(tests, R.monday_of(today))
    tpl = _test_templates(inp.get("thresholds") or {}, prefs)
    aet_p = AT.resolve_protocol(prefs.aet_test_protocol, prefs.cap_weekday, prefs.long_cap)

    def scheduled(kind: str, since: str) -> bool:
        return any(s["kind"] == "test" and s["state"] in ("active", "done") and (s.get("day") or "") >= since
                   and AT.is_aet_session(s) == (kind == "aet") for s in stored)

    def days_for(kind: str, earliest: Optional[str]) -> list[dict]:
        sg = {"kind": kind, "replaces_long": kind == "aet" and aet_p == "xu90"}
        return [d for d in suggestion_days(sg, stored, today, prefs, bl) if not earliest or d["day"] >= earliest]

    rows += SG.zone_rows(inp.get("zone") or {}, {t["kind"] for t in tests}, scheduled, days_for)
    # the session a zone retest would put in (課表偏好 CP / AeT 測試方式)
    for r in rows:
        for t in r.get("tests") or []:
            src = next((x for x in tpl["aet"] if x["protocol"] == aet_p), None) if t["key"] == "aet" else \
                next((x for x in tpl["cp"] if x["protocol"] == prefs.cp_test_protocol and not x.get("none")),
                     next((x for x in tpl["cp"] if not x.get("none")), None))
            if src:
                t["session"] = {"kind": "test", "title": src["title"], "minutes": src["minutes"],
                                "target": src.get("target"), "detail": src.get("detail"), "source": src.get("source"),
                                "tss": src.get("tss"),
                                "protocol": src.get("protocol_stored") if t["key"] == "aet" else src["protocol"]}
                t["label"] = f"{t['label']}：{src['title']}（{src['minutes']} 分）"
            t["replaces_long"] = t["key"] == "aet" and aet_p == "xu90"
    return rows


@router.get("/suggestions")
async def suggestions(db: AsyncSession = Depends(get_db)):
    from backend.engine import suggestions as SG
    inp = await _inputs(db)
    rows = await _all_suggestions(db, inp)
    dis = await _dismissed(db)
    kept = SG.prune(dis, rows, _today(inp))
    if kept != dis:
        await _save_setting(db, SG.KEY, kept)
    vis = SG.visible(rows, kept)
    return {"today": _today(inp), "suggestions": vis, "count": len(vis)}


@router.post("/suggestions/dismiss")
async def dismiss_suggestion(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import suggestions as SG
    sid, action = str(body.get("id") or ""), body.get("action") or "dismissed"
    if action not in ("declined", "dismissed"):
        raise HTTPException(400, "action must be declined or dismissed")
    if not sid:
        raise HTTPException(400, "id required")
    inp = await _inputs(db)
    week = sid.split(":")[-1] if sid.startswith(("b2b:", "test:")) else None
    await _save_setting(db, SG.KEY, SG.record(await _dismissed(db), sid, action, dt.datetime.now(), week))
    return {"id": sid, "action": action, "today": _today(inp)}


@router.post("/suggestions/accept")
async def accept_suggestion(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import suggestions as SG
    sid, day = str(body.get("id") or ""), body.get("day")
    inp = await _inputs(db)
    rows = await _all_suggestions(db, inp)
    sg = next((r for r in SG.visible(rows, await _dismissed(db)) if r["id"] == sid), None)
    if sg is None:
        raise HTTPException(400, "現在沒有這個建議（可能已經排入或關掉了）")
    if sg["type"] == "b2b":
        out = await _accept_b2b(db, inp, sg, day)
    elif sg["type"] == "test":
        tests = await _suggestions(db, inp, dismissed={})
        t = next((x for x in tests if x["kind"] == sg["kind"]), None)
        out = {"sessions": [await _schedule_test(db, inp, t, day)]}
    elif sg["type"] == "zone_test":
        t = next((x for x in sg.get("tests") or [] if x["key"] == body.get("test")), None)
        if t is None or not t.get("session"):
            raise HTTPException(400, "要選一個測試")
        out = {"sessions": [await _schedule_test(db, inp, {"days": [{"day": o["day"]} for o in t["options"]],
                                                           "session": t["session"],
                                                           "replaces_long": t.get("replaces_long")}, day)]}
    else:
        raise HTTPException(400, "這個建議沒有可以排的東西")
    week = sid.split(":")[-1] if sid.startswith(("b2b:", "test:")) else None
    await _save_setting(db, SG.KEY, SG.record(await _dismissed(db), sid, "accepted", dt.datetime.now(), week))
    return {"id": sid, **out}


def _b2b_generated(inp: dict, week: str) -> list[dict]:
    """The generator's accepted B2B days of `week` (long / long2, pinned, decorated, with
    the 負重訓練 pack) — what gets stored as the user's sessions."""
    from backend.engine import b2b as B2B
    for w in [inp.get("cur") or {}] + list(inp.get("weeks") or []):
        ws = (w.get("week") or {}).get("start") or w.get("start")
        if ws == week and (w.get("b2b") or {}).get("accepted"):
            return sorted((s for s in w.get("sessions") or [] if s.get("id") in ("long",) + B2B.FOLLOWERS),
                          key=lambda s: s["id"])
    return []


def _b2b_fallback(inp: dict, sg: dict, days: list[str]) -> list[dict]:
    """The two days from the suggestion alone (the generator didn't plan the week)."""
    from backend.engine import b2b as B2B
    rate = float(((inp.get("cur") or {}).get("tss_per_category") or {}).get("trail") or 55.0) / 60.0
    long_s = {"id": "long", "kind": "long", "title": "長時間輕鬆（山路）", "minutes": sg["minutes"][0],
              "tss": round(rate * sg["minutes"][0], 1), "detail": "", "target": "", "source": "", "terrain": None}
    ss = [long_s] + B2B.followers(long_s, {"minutes": sg["minutes"]})
    B2B.decorate(ss, {"event": {"name": sg.get("event"), "days": sg.get("event_days") or 2,
                                "kind": sg.get("event_kind")}, "weeks_out": sg.get("weeks_out")},
                 (inp.get("thresholds") or {}).get("aet"))
    for s, d in zip(ss, days):
        s["day"] = d
    return ss


async def _accept_b2b(db: AsyncSession, inp: dict, sg: dict, day: Optional[str]) -> dict:
    from backend.engine import b2b as B2B
    from backend.engine import plan_auto as PA
    opt = next((o for o in sg.get("options") or [] if o["day"] == day), None)
    if opt is None:
        raise HTTPException(400, "這兩天不適合排 B2B（不是連續的可練日、在不排課日期內，或已經有你自己的課）")
    days = [opt["day"], opt["end"]]
    acc = [e for e in await _accepted(db) if e.get("week") != sg["week"]]
    entry = {"week": sg["week"], "days": days, "minutes": list(sg["minutes"]),
             "at": dt.datetime.now().isoformat(timespec="seconds"), "uids": []}
    await _save_setting(db, B2B.ACCEPTED_KEY, acc + [entry])
    inp2 = await _inputs(db)                        # the generator plans the week around the two days
    gen = _b2b_generated(inp2, sg["week"]) or _b2b_fallback(inp2, sg, days)
    added = []
    try:
        async with _wlock():
            for s, d in zip(gen, days):
                data = {k: s.get(k) for k in ("kind", "title", "minutes", "target", "detail", "source", "tss",
                                              "terrain") if s.get(k) is not None}
                added.append(await PS.add(db, {**data, "day": d}, _today(inp2), blocked=PS.blocked_map(inp2)))
            # the week's long run is B2B day 1 now: the user's edited copy of it goes too
            end = (dt.date.fromisoformat(sg["week"]) + dt.timedelta(days=6)).isoformat()
            for s in await PS.load(db):
                if (s["state"] == "active" and s.get("gen_key") == "long" and s.get("edited")
                        and s.get("day") and sg["week"] <= s["day"] <= end):
                    await PS.delete(db, s["uid"])
            entry["uids"] = [a["uid"] for a in added]
            await _save_setting(db, B2B.ACCEPTED_KEY, acc + [entry])
            if await PA.pending(db) is None:        # a 課表待確認 proposal is waiting: don't apply it here
                await PS.plan_reconcile(db, inp2, apply=True)
    except PS.PlanError as e:
        await _save_setting(db, B2B.ACCEPTED_KEY, acc)
        raise _err(e)
    return {"sessions": added, "accepted": entry}


async def _b2b_cancelled(db: AsyncSession, uid: str) -> Optional[dict]:
    """A deleted B2B day cancels the accepted B2B: the other day goes too, the week is
    regenerated, and the suggestion stays declined for that week."""
    from backend.engine import b2b as B2B
    from backend.engine import suggestions as SG
    acc = await _accepted(db)
    hit = next((e for e in acc if uid in (e.get("uids") or [])), None)
    if hit is None:
        return None
    for other in hit.get("uids") or []:
        if other != uid:
            try:
                await PS.delete(db, other)
            except PS.PlanError:
                pass
    await _save_setting(db, B2B.ACCEPTED_KEY, [e for e in acc if e is not hit])
    await _save_setting(db, SG.KEY, SG.record(await _dismissed(db), f"b2b:{hit['week']}", "declined",
                                              dt.datetime.now(), hit["week"]))
    return hit


async def _b2b_moved(db: AsyncSession, uid: str, day: str) -> None:
    """A B2B day moved by the user: the accepted entry follows (the generator plans around it)."""
    from backend.engine import b2b as B2B
    acc = await _accepted(db)
    for e in acc:
        if uid in (e.get("uids") or []):
            i = e["uids"].index(uid)
            days = list(e["days"])
            if i < len(days):
                days[i] = day
                e["days"] = days
                await _save_setting(db, B2B.ACCEPTED_KEY, acc)
            return


@router.post("/steps-preview")
async def steps_preview(body: dict = Body(...)):
    """The editor's 「目標用：自動／心率／功率」 preview: each step with its resolved target
    (engine/target_policy.py → sync/coros_workouts steps), nothing stored or sent."""
    from backend.engine import plan_prefs as PP
    from backend.engine import target_policy as TP
    inp = await _inputs()
    th = inp.get("thresholds") or {}
    s = {k: body.get(k) for k in ("kind", "title", "minutes", "target", "detail", "source", "terrain", "protocol",
                                  "variant_key", "variant_reps", "variant_blocks", "variant_adj", "heat")}
    tb = body.get("target_basis")
    s["target_basis"] = tb if tb in ("hr", "power") else None
    pol = TP.target_policy(s, PP.load(), th)
    s["basis"] = pol["basis"]
    try:
        lines = CW.step_lines(CW.session_steps({**s, "minutes": int(s.get("minutes") or 0)}, CW.Thresholds.of(th)))
    except CW.Unsupported as e:
        lines = [f"不推送：{e}"]
    return {"policy": pol, "lines": lines, "label": f"目標用：{TP.LABEL[pol['basis']]}（{pol['why']}）"}


# ---------------------------------------------------------------------------
# the structured editor (engine/workout_steps.py; docs/plans/workout-editor.plan.md)
#
#   POST /api/v1/overview/plan/steps/derive          {uid?, session fields} -> the stored
#        structure, or one derived from the kind / variant / text (nothing stored) + context
#        (thresholds, zones with today's numbers, 目標用 policy, the day's cap)
#   POST /api/v1/overview/plan/steps/check           {session fields, steps} -> resolved
#        targets, run order for the chart, totals / TSS 估, issues, the watch preview
#   GET  /api/v1/overview/plan/steps/templates       插入範本: library main sets, tests, strides
#   GET  /api/v1/overview/plan/sessions/{uid}/coros-preview   「推到手錶會長這樣」
#   PATCH /sessions/{uid} {steps, steps_force?}: errors → 422 {"errors": [...]} unless forced
# ---------------------------------------------------------------------------

STEP_FIELDS = ("kind", "title", "minutes", "target", "detail", "source", "terrain", "protocol", "day",
               "variant_key", "variant_reps", "variant_blocks", "variant_adj", "rung_key", "heat", "target_basis",
               "climb_per_km", "distance_km", "climb_m")
_tp_cache: dict = {}


def _speeds() -> dict:
    """The athlete's easy road speed and trail EP speed (engine/equivalence.py, the same
    model as 同負荷換算) for the time of a distance step; {} when unavailable (tests
    replace this)."""
    try:
        m = (_equivalence() or {}).get("model") or {}
        tr = (m.get("modes") or {}).get("trail") or {}
        return {"v_easy": m.get("v_flat_kmh"), "v_easy_src": m.get("v_flat_source") or "",
                "ep_kmh": tr.get("ep_kmh")}
    except Exception:                       # noqa: BLE001 — speeds are optional
        return {}


def _climb_per_km(s: dict) -> Optional[float]:
    for k in ("climb_per_km",):
        try:
            if s.get(k) is not None:
                return float(s[k])
        except (TypeError, ValueError):
            pass
    try:
        if s.get("distance_km") and s.get("climb_m") is not None:
            return float(s["climb_m"]) / float(s["distance_km"])
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return None


def _tpace() -> Optional[float]:
    """Threshold pace in s/km (thresholds.estimate_tpace: 推估), cached per dataset day; None
    when it can't be estimated (tests replace this)."""
    try:
        from backend.api.overview import _dataset
        from backend.engine import overview as O
        from backend.engine import thresholds as T
        ds = _dataset()
        today = O.day_to_date(ds.today)
        key = (id(ds), today)
        if key not in _tp_cache:
            v = (T.estimate_tpace(ds, today) or {}).get("value")
            _tp_cache.clear()
            _tp_cache[key] = float(v) * 60.0 if v else None
        return _tp_cache[key]
    except Exception:                       # noqa: BLE001 — pace is optional
        return None


def _session_of(body: dict, stored: Optional[dict]) -> dict:
    s = dict(stored or {})
    for k in STEP_FIELDS:
        if k in body and body[k] is not None:
            s[k] = body[k]
    if body.get("target_basis") in ("auto", "", None) and "target_basis" in body:
        s["target_basis"] = None
    s["minutes"] = int(s.get("minutes") or 0)
    return s


async def _steps_env(s: dict, inp: dict) -> dict:
    from backend.engine import interval_library as IL
    from backend.engine import plan_prefs as PP
    from backend.engine import target_policy as TP
    from backend.engine import workout_steps as WS
    prefs = PP.load()
    th = dict(inp.get("thresholds") or {})
    th["tpace"] = await run_in_threadpool(_tpace)
    pol = TP.target_policy(s, prefs, th)
    sp = dict(await run_in_threadpool(_speeds))
    # 越野跑 (kind hike) and trail sessions: effort distance with the session's climb
    sp["terrain"] = "trail" if s.get("kind") == "hike" or s.get("terrain") in ("trail", "hike") else "road"
    sp["climb_per_km"] = _climb_per_km(s)
    c = WS.Ctx.of(th, pol["basis"], bool(pol.get("hr_cap")), sp)
    rung = None
    if s.get("kind") == "quality":
        rung = s.get("rung_key") or getattr(IL.get(s.get("variant_key")), "rung", None)
    cap = day_cap(prefs, s.get("day")) if s.get("kind") not in ("test",) else None
    return {"ctx": c, "th": th, "policy": pol, "cap": cap, "cap_mode": getattr(prefs, "cap_mode", "soft"),
            "rung": rung if rung in IL.LIBRARY else None}


def _context(env: dict) -> dict:
    from backend.engine import target_policy as TP
    from backend.engine import workout_steps as WS
    th, pol = env["th"], env["policy"]
    return {"thresholds": {k: th.get(k) for k in ("cp", "lthr", "aet", "tpace", "cp_source", "lthr_source", "aet_source")},
            "zones": WS.zones_table(env["ctx"]), "policy": pol,
            "basis_label": f"目標用：{TP.LABEL[pol['basis']]}（{pol['why']}）",
            "cap": env["cap"], "cap_mode": env["cap_mode"], "rung": env["rung"],
            "kinds": WS.KIND_LABEL, "types": WS.TYPE_LABEL,
            "rules": {"z5_min_rep_s": WS.Z5_MIN_REP_S, "z3_min_rep_s": WS.Z3_MIN_REP_S,
                      "z5_max_rest_s": WS.Z5_MAX_REST_S, "coros_max_steps": WS.COROS_MAX_STEPS}}


@router.post("/steps/derive")
async def steps_derive(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import workout_steps as WS
    inp = await _inputs()
    stored = next((x for x in await PS.load(db) if x["uid"] == body.get("uid")), None) if body.get("uid") else None
    s = _session_of(body, stored)
    env = await _steps_env(s, inp)
    if stored and stored.get("steps") and not body.get("rederive"):
        return {"steps": stored["steps"], "derived": False, "context": _context(env)}
    d = WS.derive(s, env["th"])
    return {"steps": d, "derived": True, "context": _context(env),
            "reason": "" if d else "這種課不推到手錶，或標題看不出結構（用「＋ 步驟」自己排）"}


def _norm_or_400(steps):
    from backend.engine import workout_steps as WS
    try:
        return WS.normalize(steps)
    except WS.StepsError as e:
        raise HTTPException(400, {"errors": e.errors})


@router.post("/steps/check")
async def steps_check(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import workout_steps as WS
    inp = await _inputs()
    stored = next((x for x in await PS.load(db) if x["uid"] == body.get("uid")), None) if body.get("uid") else None
    s = _session_of(body, stored)
    st = _norm_or_400(body.get("steps"))
    env = await _steps_env(s, inp)
    return {**WS.view(st, env["ctx"], env["cap"], env["cap_mode"], env["rung"]),
            "basis_label": _context(env)["basis_label"], "policy": env["policy"]}


@router.get("/steps/templates")
async def steps_templates():
    from backend.engine import workout_steps as WS
    return WS.templates()


@router.get("/sessions/{uid}/coros-preview")
async def coros_preview(uid: str, db: AsyncSession = Depends(get_db)):
    """「推到手錶會長這樣」 for a stored session: what COROS would get (nothing is sent)."""
    from backend.engine import workout_steps as WS
    inp = await _inputs()
    s = next((x for x in await PS.load(db) if x["uid"] == uid), None)
    if s is None:
        raise HTTPException(404, "找不到這堂課")
    env = await _steps_env(s, inp)
    st = s.get("steps") or WS.derive(s, env["th"])
    if not st:
        return {"uid": uid, "pushed": False, "reason": "這種課不推到手錶"}
    return {"uid": uid, "pushed": True, "derived": not s.get("steps"), "name": CW.workout_name(s) if s.get("day") else "",
            **WS.watch_preview(WS.normalize(st), env["ctx"], overview=s.get("detail") or "")}


def _same_shape(a: dict, b: dict) -> bool:
    def strip(items):
        out = []
        for x in items:
            y = {k: v for k, v in x.items() if k != "id"}
            if "items" in y:
                y["items"] = strip(y["items"])
            out.append(y)
        return out
    return strip(a.get("items") or []) == strip(b.get("items") or [])


async def _with_steps(patch: dict, inp: dict, cur: dict) -> dict:
    """A body that saves a structure: validated, the training rules checked (errors
    refused unless steps_force), and the minutes / target / TSS taken from it unless
    the body sets them. The origin stays template:<key> only while the structure is
    the session's own library variant as is."""
    from backend.engine import interval_library as IL
    from backend.engine import workout_steps as WS
    st = _norm_or_400(patch["steps"])
    s = _session_of(patch, cur)
    env = await _steps_env(s, inp)
    v = WS.view(st, env["ctx"], env["cap"], env["cap_mode"], env["rung"])
    errs = [i["text"] for i in v["issues"] if i["level"] == "err"]
    if errs and not patch.get("steps_force"):
        raise HTTPException(422, {"errors": errs})
    var = IL.resolve(s.get("variant_key"), s.get("variant_reps"), s.get("variant_adj")) if s.get("variant_key") else None
    tpl = WS.from_variant(var, s.get("variant_blocks") or "std", s.get("target") or "") if var else None
    st["origin"] = f"template:{var.key}" if tpl and _same_shape(st, tpl) else "user"
    out = {k: v_ for k, v_ in patch.items() if k != "steps_force"}
    out["steps"] = st
    out.setdefault("minutes", max(1, round(v["totals"]["sec"] / 60.0)))
    if "target" not in patch and v["summary"]:
        out["target"] = v["summary"]
    out.setdefault("tss", v["totals"]["tss"])
    return out


@router.get("/variants")
async def variants(uid: Optional[str] = None, day: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """The swap drawer for a stored interval session (uid) and the editor's templates
    (interval_library.drawer / templates; interval-prescription.md §C5.4)."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    inp = await _inputs()
    s = next((x for x in await PS.load(db) if x["uid"] == uid), None) if uid else None
    day = day or (s or {}).get("day")
    ctx = _variant_ctx(inp, day)
    cp = (ctx["th"] or {}).get("cp")
    gate = ctx["gate"]
    dec = QG.week_decision(gate, "base", "base") if gate.get("state") else {"spec": None}
    rung_now = (dec.get("spec") or (None,))[0]
    out = {"day": day, "cap": ctx["cap"], "cp": cp,
           "templates": IL.templates(cp, ctx["cap"], ctx["prefs"], ctx["history"], rung_now)}
    rung = (s.get("rung_key") or getattr(IL.get(s.get("variant_key")), "rung", None)) if s and s.get("variant_key") \
        else None
    if rung in IL.LIBRARY:
        out["drawer"] = IL.drawer(rung, cp, ctx["cap"], ctx["prefs"], ctx["history"], s.get("variant_key"))
        out["session"] = {k: s.get(k) for k in ("uid", "title", "variant_key", "rung_key", "equiv", "swap", "swap_reason")}
    return out


@router.delete("/sessions/{uid}")
async def delete_session(uid: str, db: AsyncSession = Depends(get_db)):
    try:
        async with _wlock():
            out = await PS.delete(db, uid)
            # deleting either day of an accepted B2B cancels it (both days; declined for that week)
            if await _b2b_cancelled(db, uid) is not None:
                out["b2b_cancelled"] = True
    except PS.PlanError as e:
        raise HTTPException(404, str(e))
    if out.get("b2b_cancelled"):
        from backend.engine import plan_auto as PA
        inp = await _inputs(db)                       # the week without the B2B
        async with _wlock():
            if await PA.pending(db) is None:
                await PS.plan_reconcile(db, inp, apply=True)
    return out


@router.post("/sessions/{uid}/link")
async def link_session(uid: str, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """{index}: the user pairs this session with an activity (engine/plan_match.py)."""
    from backend.settings.repository import SettingsRepository
    inp = await _inputs(db)
    a = next((x for x in inp.get("activities") or [] if x.get("index") == body.get("index")), None)
    if a is None:
        raise HTTPException(404, "找不到這筆活動")
    try:
        async with _wlock():
            out = await PS.link(db, uid, a, _today(inp))
            repo = SettingsRepository(db)
            left = [e for e in (await repo.get(PS.UNLINKED_KEY) or []) if e.get("start") != a.get("start")]
            await repo.set(PS.UNLINKED_KEY, left)
            await db.commit()
    except PS.PlanError as e:
        raise HTTPException(400, str(e))
    return out


@router.delete("/sessions/{uid}/link")
async def unlink_session(uid: str, db: AsyncSession = Depends(get_db)):
    """Undo the pairing; the activity becomes an unplanned run (not auto-matched again)."""
    from backend.settings.repository import SettingsRepository
    inp = await _inputs(db)
    try:
        async with _wlock():
            out, a = await PS.unlink(db, uid, _today(inp))
            if a and a.get("start"):
                repo = SettingsRepository(db)
                ents = [e for e in (await repo.get(PS.UNLINKED_KEY) or []) if e.get("start") != a["start"]]
                await repo.set(PS.UNLINKED_KEY, (ents + [{"start": a["start"], "index": a.get("index")}])[-200:])
                await db.commit()
    except PS.PlanError as e:
        raise HTTPException(400, str(e))
    return out


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
    from backend.engine import aet_test as AT
    # aet_options: the AeT 測試方式 hover texts (duration, terrain, what is held, judging, source)
    return {"prefs": p.to_dict(), "defaults": PP.Prefs().to_dict(), "active": p.active,
            # 偏好的星期 vs the default rules (shown when the prefs are saved; 照我的偏好 = pref_keep)
            "day_conflicts": PP.day_conflicts(p),
            "gate_options": QG.option_texts(),
            "aet_options": {k: {"label": "自動（標準：徐國峰 90 分；備案 UA 40 分）" if k == "auto"
                                else AT.PROTOCOLS[k]["label"], "tip": AT.protocol_tip(k)}
                            for k in AT.PROTOCOL_CHOICES}}


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
    inp = {**(await _inputs(blackouts=cand)), "covered": saved.get("covered"), "unlinked": saved.get("unlinked")}
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
    acts = activity_rows(ds, a, b + dt.timedelta(days=1))
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
    out["notice"] = 0.0                # 課表待確認 reminder (engine/plan_auto.py): not training
    return out


def est_tss(s: dict, rates: dict) -> float:
    if s.get("kind") in PS.NOT_LOAD:
        return 0.0
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
        ss = [s for s in sessions if s.get("day") and a <= s["day"] <= b and s["state"] in ("active", "done", "missed")
              and s["kind"] not in PS.NOT_LOAD]
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


def _fresh_done_by(s: dict, acts_by: dict) -> dict:
    """A done session's stored activity, completed with today's fields (hard_s for
    rows matched before it existed); the match kind stays the stored one."""
    d = s.get("done_by")
    if s.get("state") != "done" or not isinstance(d, dict):
        return s
    a = acts_by.get(d.get("index"))
    if a is None or a.get("start") != d.get("start"):
        return s
    return {**s, "done_by": {**d, **a, "match": d.get("match")}}


def _link_options(ss: list[dict], every: list[dict], acts: list[dict], today: str) -> None:
    """`link_options` (activity indexes, closest first) on the sessions the user may
    pair by hand: open or missed up to today, or done (re-link); same week, ± 1 day."""
    used = {s["done_by"].get("index") for s in every
            if s["state"] == "done" and isinstance(s.get("done_by"), dict)}
    for s in ss:
        if s["state"] not in ("active", "missed", "done") or (s.get("day") or "9999") > today:
            continue
        mine = (s.get("done_by") or {}).get("index") if s["state"] == "done" else None
        opts = [a for a in PM.candidates(s, acts, used - {mine}) if a.get("index") != mine
                and R.monday_of(a["date"]) == s["week_start"]]
        s["link_options"] = [a["index"] for a in opts]


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
    acts_by = {x.get("index"): x for x in extras["activities"]}
    from backend.engine import activity_key as AK
    AK.rebase_done_by(every, extras["activities"])         # done_by -> this source's indexes, by start
    for s in every:
        if s.get("day") and start <= s["day"] <= end:
            est = est_tss(s, rates)
            s = _fresh_done_by(s, acts_by)
            vs = PM.compare(s)
            ss.append({**s, "tss_est": round(est, 1), "vs": vs,
                       "compliance": C.with_plan_check(C.session_compliance(s, est), vs)})
    _link_options(ss, every, extras["activities"], body["today"])
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
            "test_suggestions": await _suggestions(db, inp),
            "test_templates": _test_templates(inp["thresholds"] or {}, None),
            "coros": await _coros_state(db, every)}


@router.get("/schedule/page", include_in_schema=False)
def schedule_page():
    from fastapi.responses import FileResponse
    from backend.api.overview import STATIC
    return FileResponse(STATIC / "schedule.html")
