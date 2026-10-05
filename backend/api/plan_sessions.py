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

from backend.i18n.pages import render_page
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend.db.current import current_athlete_id
from backend.db.database import get_db
from backend.engine import plan_match as PM
from backend.engine import plan_store as PS
from backend.engine import projection as P
from backend.engine import reconcile as R
from backend.engine import workout_templates as WTPL
from backend.sync import coros_workouts as CW
from backend.sync import workout_targets as WT

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
    # 主要訓練項目 (engine/primary_sport.py): switching it regenerates too
    from backend.engine import primary_sport as PSP
    # 課表心率區間 and the COROS account's max / rest HR (engine/hr_profile.py) change the HR targets
    from backend.engine import hr_profile as HRP
    key = (id(ds), today, _plan_stamp(), prefs.stamp(), BL.stamp(bos), auto_on, B2B.accepted_stamp(acc),
           PSP.stored(), HRP.stamp())
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    st = _status(ds, today)
    from backend.engine.panels.race_refs import calculator_hours
    cur = O.week_plan(ds, st, today, prefs=prefs, blackouts=bos, b2b_accepted=acc, race_predict=calculator_hours)
    monday = dt.date.fromisoformat(cur["week"]["start"])
    cap = monday + dt.timedelta(weeks=P.MAX_WEEKS, days=6)
    ph = st.phase
    phase_end = dt.date.fromisoformat(ph.end) if ph else cap
    horizon = min(cap, max(phase_end, monday + dt.timedelta(days=13)))
    phases = [{"kind": p.kind, "start": p.start, "end": p.end, "note": p.note}
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
           "horizon_end": horizon.isoformat(),
           # + threshold pace (s/km, 推估): % / zone pace targets reach the watch (COROS intensityType 3)
           "thresholds": {**(cur.get("thresholds") or {}), "tpace": _tpace(),
                          # SP-64 (engine/threshold_confidence.py): LTHR / HRmax not believable →
                          # the editor badges HR-target templates and sessions
                          "thr_warn": _thr_warn(st)},
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


def _thr_warn(st) -> Optional[dict]:
    """status.i_testing's threshold check (engine/threshold_confidence.warn_of), or None."""
    try:
        from backend.engine import threshold_confidence as TC
        return TC.warn_of(getattr(st, "thr_check", None))
    except Exception:                       # noqa: BLE001 — no badge
        return None


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
        r = O.activity_row(w, ds)          # + `session` (workout_review.classify: type, label, icon)
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
    hard_days: set = set()
    if enabled:
        try:
            from backend.engine import workout_review as WR
            for w in O.workouts_between(ds, monday, today + dt.timedelta(days=1)):
                if O.category(w) not in ("road", "trail", "hike"):
                    continue
                # a hard day done this week, planned or not (Z5 / Z3 / 高強度長跑 / CP test):
                # adapt keeps the remaining hard sessions 48 h away from it
                if O.session_of(ds, w).get("type") in WR.HARD_TYPES:
                    hard_days.add(O.wdate(w).isoformat())
                if O.category(w) == "hike":
                    continue
                m = WR.measure(ds, w) or {}
                reviews[w.idx] = {k: m.get(k) for k in ("avg_hr", "aet", "over_aet_s", "hr_s", "avg_power", "cp")}
                reviews[w.idx]["tss"] = O._n(w.metrics.get("tss"))
            WR._flush(ds)
        except Exception:                   # noqa: BLE001 — a review failure never breaks the plan
            pass
    fx = next(((i.extra or {}) for i in st.indicators if i.id == "fitness"), {})
    ramp, ramp_base = fx.get("ramp_week"), fx.get("ramp_base")
    load = cur.get("load") or {}
    done_today = any(a.get("date") == today.isoformat() for a in (cur.get("done") or {}).get("activities") or [])
    return {"enabled": enabled, "reviews": reviews, "ramp": ramp, "ramp_base": ramp_base, "tsb": load.get("tsb_today"),
            "hard_days": sorted(hard_days),
            "first_free": (today + dt.timedelta(days=1 if done_today else 0)).isoformat()}


async def _covered(db: AsyncSession, last_activity: Optional[str]) -> Optional[str]:
    """Last day the synced data covers: the latest activity's day, or the day
    before the latest successful sync, whichever is later."""
    from sqlalchemy import select
    from backend.db.models import SyncState
    days = [last_activity] if last_activity else []
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == current_athlete_id()))).scalar_one_or_none()
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


def _view(s: dict, inp: dict, rows: dict, today: str, prov=None) -> dict:
    """`coros` keeps its name in the API: the push status at the active provider.
    `quality_family`: a 強度課's 有氧間歇 / VO2max 間歇 / 速度 (workout_templates.session_family: the
    stored `family` the user picked, else read from the steps); `steps_family` = what the steps
    read as (the editor's 「步驟看起來像…」 hint when it differs, SP-79)."""
    prov = prov or WT.get(WT.DEFAULT)
    v = dict(s)
    got = WTPL.steps_family(s, inp["thresholds"])
    v["quality_family"] = WTPL.session_family(s, inp["thresholds"], derived=got)
    v["steps_family"] = got
    if s["state"] == "active":
        v["coros"] = prov.status_of(PS.push_dict(s), inp["thresholds"], rows.get(s["uid"]), today)
        note = pace_note(s, inp["thresholds"])
        if note:
            v["coros"] = {**v["coros"], "pace_note": note, "tpace_link": tpace_link()}
    elif s["uid"] in rows:
        v["coros"] = {"status": "pushed_" + s["state"], **prov.row_view(rows[s["uid"]])}
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
    prov = await WT.active(db)
    rows = await prov.all_rows(db)
    return {**_meta(inp), "summary": _summary(every, inp),
            "sessions": [_view(s, inp, rows, today, prov) for s in ss if s["state"] != "deleted"
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
        await _with_route(db, data["steps"], None)
    try:
        async with _wlock():
            out = await PS.add(db, data, _today(inp), blocked=PS.blocked_map(inp))
            if data.get("kind") == "test" and out.get("day") and _is_xu90(data):
                # 徐國峰's 90′ is that day's LSD — the 「安排課表」 deep link / the 測試 dialog take the
                # same path as 排入測試 (SP-39, owner 2026-10-04); after the add, so a refused day
                # keeps its long run
                await _replace_long(db, out["day"])
            return out
    except PS.PlanError as e:
        raise _err(e)


def _is_xu90(s: dict) -> bool:
    """徐國峰's 90-min test: the 測試 dialog's xu90 protocol (aet_test.is_xu, by title) or the
    template-library row lib:xu_e_drift (排入測試's template path)."""
    from backend.engine import aet_test as AT
    if AT.is_xu(s):
        return True
    row = _test_rows().get("lib:xu_e_drift") or {}
    t = row.get("title") or row.get("label")
    return bool(t) and (s.get("title") or "") == t


async def _replace_long(db: AsyncSession, day: str) -> None:
    """Delete the active long run of `day` (徐國峰's 90-min test is that day's LSD). Caller holds _wlock."""
    for s in await PS.load(db):
        if s["state"] == "active" and s["kind"] == "long" and s.get("day") == day:
            await PS.delete(db, s["uid"])


@router.patch("/sessions/{uid}")
async def edit_session(uid: str, patch: dict = Body(...), db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    if patch.get("variant_key"):
        cur = next((s for s in await PS.load(db) if s["uid"] == uid), None)
        patch = _with_variant(patch, inp, (cur or {}).get("rung_key"), patch.get("day") or (cur or {}).get("day"))
    if patch.get("steps"):
        cur = next((s for s in await PS.load(db) if s["uid"] == uid), None)
        patch = await _with_steps(patch, inp, cur or {})
        await _with_route(db, patch["steps"], (cur or {}).get("steps"))
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


def day_reason(sg: dict, stored: list[dict], today: str, prefs, blocked: dict, day: str,
               earliest: Optional[str] = None) -> str:
    """Why suggestion_days doesn't offer `day` for this test (the same rules, in words)."""
    from backend.i18n import _
    d = dt.date.fromisoformat(day)
    d0 = dt.date.fromisoformat(today)
    if day < today:
        return _("過去的日子")
    if (d - d0).days >= SUGGEST_DAYS:
        return _("超出建議的 {n} 天內", n=SUGGEST_DAYS)
    if earliest and day < earliest:
        return _("要等到 {day} 以後（重測的條件）", day=f"{int(earliest[5:7])}/{int(earliest[8:10])}")
    if day in (blocked or {}):
        return _("不排課日期／休息日")
    if prefs is not None and not prefs.allowed(d):
        return _("不是可練日（課表偏好）")
    hard = {s["day"]: s for s in stored if s["state"] == "active" and s.get("day") and s["kind"] in ("long", "quality", "test")}
    near = [hard[x] for x in ((d + dt.timedelta(days=k)).isoformat() for k in (-1, 0, 1)) if x in hard]
    if sg.get("replaces_long"):
        if d.weekday() < 5 and day not in {x for x, s in hard.items() if s["kind"] == "long"}:
            return _("徐國峰 90 分測試排在週末或長跑那天")
        near = [s for s in near if s["kind"] in ("quality", "test")]
    elif sg.get("kind") == "aet" and getattr(prefs, "aet_test_days", "weekday") == "weekday" and d.weekday() >= 5:
        return _("AeT 測試排在週一到週五（課表偏好）")
    if near:
        return _("前後一天有長跑／強度課／測試：{titles}", titles="、".join(s.get("title") or "" for s in near[:2]))
    return _("這天不適合")


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


# 排入測試 from the 課表 calendar's context menu: the template library's 測試 entries
# (engine/workout_steps.templates, group cat "test") per suggested test kind, the
# template_recs 推薦 first. protocol: how engine/cp_protocols reads the result
# (two-point = standard, one 20′ bout = quick, a 30′ time trial = race; None = 課表偏好's).
TEST_TEMPLATES = {
    "cp": ("lib:stryd_cp_3_12", "lib:stryd_9_3", "cp_standard", "cp_quick", "lib:pal_test20", "lib:pal_test10",
           "lib:pal_test3", "lib:friel_lthr30"),
    "aet": ("lib:xu_e_drift", "lib:ua_aet_drift"),
}
TEMPLATE_PROTOCOL = {"lib:stryd_cp_3_12": "standard", "lib:stryd_9_3": "standard", "cp_standard": "standard",
                     "cp_quick": "quick", "lib:pal_test20": "quick", "lib:friel_lthr30": "race",
                     "lib:xu_e_drift": "aet", "lib:ua_aet_drift": "aet"}


def _test_rows() -> dict:
    from backend.engine import workout_steps as WS
    return {r["key"]: r for g in WS.templates()["groups"] if g.get("cat") == "test" for r in g.get("rows") or []}


async def test_templates_for(kind: str, day: Optional[str], db: AsyncSession) -> list[dict]:
    """[{key, title, minutes, src, url, reason, recommended}] for a test kind (cp / aet), 推薦 first."""
    from backend.engine import template_recs as TR
    rows = _test_rows()
    keys = [k for k in TEST_TEMPLATES.get(kind, ()) if k in rows]
    try:
        recs = (await steps_template_recs(kind="test", day=day, db=db))["cats"].get("test") or []
    except Exception:                         # noqa: BLE001 — the order is only a nicety
        recs = []
    why = {r["key"]: r.get("reason") or "" for r in recs}
    order = [k for k in why if k in keys] + [k for k in keys if k not in why]
    return [{"key": k, "title": rows[k].get("title") or rows[k].get("label"), "minutes": round(TR.row_minutes(rows[k])),
             "src": rows[k].get("src") or "", "url": rows[k].get("url"), "reason": why.get(k, ""),
             "recommended": k in why} for k in order]


@router.get("/test-templates/for")
async def get_test_templates_for(kind: str, day: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    if kind not in TEST_TEMPLATES:
        raise HTTPException(400, "kind must be cp or aet")
    return {"kind": kind, "templates": await test_templates_for(kind, day, db)}


async def _template_session(inp: dict, key: str, kind: str) -> dict:
    """A test session from a template: its title, its structure (the user's steps), the
    minutes / target / TSS computed from them (_with_steps)."""
    if key not in TEST_TEMPLATES.get(kind, ()):
        raise HTTPException(400, "這個範本不是這種測試")
    row = _test_rows().get(key)
    if row is None:
        raise HTTPException(400, "找不到這個範本")
    body = {"kind": "test", "title": row.get("title") or row.get("label"), "source": row.get("src") or "",
            "protocol": TEMPLATE_PROTOCOL.get(key),
            "steps": {"items": row.get("full") or row.get("items") or [], "origin": "user"}, "steps_force": True}
    out = await _with_steps(body, inp, {})
    return {k: v for k, v in out.items() if v is not None}


async def _schedule_test(db: AsyncSession, inp: dict, sg: dict, day: Optional[str],
                         template: Optional[str] = None, kind: Optional[str] = None) -> dict:
    if day not in {d["day"] for d in sg["days"]}:
        raise HTTPException(400, "這天不適合排測試（太靠近長跑或強度課、不是可練日，或在不排課日期內）")
    if template:
        data = {**(await _template_session(inp, template, kind or "cp")), "day": day}
        # 徐國峰's 90′ is that week's long run; another AeT template doesn't replace it
        sg = {**sg, "replaces_long": bool(sg.get("replaces_long")) and template == "lib:xu_e_drift"}
    else:
        data = {**{k: v for k, v in sg["session"].items() if v is not None}, "day": day, "kind": "test"}
    try:
        async with _wlock():
            if sg.get("replaces_long"):
                await _replace_long(db, day)                     # 徐國峰's test is that day's LSD
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

    def sim_opts(sg: dict) -> list[dict]:
        from backend.engine import specific_phase as SP
        return SP.sim_day_options(sg, first, set(bl), PR.allowed if PR is not None else None,
                                  PR.cap_weekday if PR is not None else None, lambda w: _busy_days(stored, w))

    rows += SG.race_sim_rows(inp, sim_opts, stored)          # 賽事模擬 (engine/specific_phase.py)
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
    rows += await run_in_threadpool(_injury_suggestions, inp, today, set(bl), stored)
    return rows


def _injury_suggestions(inp: dict, today: str, blocked: set, stored: list[dict]) -> list[dict]:
    """傷病紀錄 rows of the box (engine/suggestions.injury_rows) and, when the
    user turned it on, 「跟受傷前很像」 (engine/injury_exposure.alerts)."""
    from backend.engine import activity_tags as AT
    from backend.engine import injuries as INJ
    from backend.engine import suggestions as SG
    if INJ.demo_mode():
        return []
    try:
        events = INJ.load_events()
        rows = SG.injury_rows(events, today, blocked, (inp.get("cur") or {}).get("reentry"),
                              INJ.pain_marks(AT.load()))
    except Exception:                       # noqa: BLE001 — the box must still load
        return []
    try:
        from backend.engine.wko5expr.datasource import read_setting
        if events and read_setting(INJ.SETTING_PATTERN, False) is True:
            from backend.api.overview import _dataset
            from backend.engine import injury_exposure as IE
            end = (dt.date.fromisoformat(today) + dt.timedelta(days=7)).isoformat()
            planned = [s for s in stored if s.get("state") == "active" and today <= (s.get("day") or "") < end
                       and s.get("kind") not in ("strength", "rest")]
            rows += IE.alerts(_dataset(), events, dt.date.fromisoformat(today), planned)
    except Exception:                       # noqa: BLE001
        pass
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
    tpl = body.get("template") or None          # 排入測試 ▸ a template (the calendar's context menu)
    inp = await _inputs(db)
    rows = await _all_suggestions(db, inp)
    sg = next((r for r in SG.visible(rows, await _dismissed(db)) if r["id"] == sid), None)
    if sg is None:
        raise HTTPException(400, "現在沒有這個建議（可能已經排入或關掉了）")
    if sg["type"] == "b2b":
        out = await _accept_b2b(db, inp, sg, day)
    elif sg["type"] == "race_sim":
        out = await _accept_race_sim(db, inp, sg, day)
    elif sg["type"] == "test":
        tests = await _suggestions(db, inp, dismissed={})
        t = next((x for x in tests if x["kind"] == sg["kind"]), None)
        out = {"sessions": [await _schedule_test(db, inp, t, day, tpl, sg["kind"])]}
    elif sg["type"] == "zone_test":
        t = next((x for x in sg.get("tests") or [] if x["key"] == body.get("test")), None)
        if t is None or not t.get("session"):
            raise HTTPException(400, "要選一個測試")
        out = {"sessions": [await _schedule_test(db, inp, {"days": [{"day": o["day"]} for o in t["options"]],
                                                           "session": t["session"],
                                                           "replaces_long": t.get("replaces_long")}, day,
                                                 tpl, t["key"])]}
    elif sg["type"] == "injury_rest":
        out = await _accept_injury_rest(db, sg)
    else:
        raise HTTPException(400, "這個建議沒有可以排的東西")
    week = sid.split(":")[-1] if sid.startswith(("b2b:", "test:")) else None
    await _save_setting(db, SG.KEY, SG.record(await _dismissed(db), sid, "accepted", dt.datetime.now(), week))
    return {"id": sid, **out}


@router.get("/test-options")
async def test_options(day: str, db: AsyncSession = Depends(get_db)):
    """排入測試 ▸ on an empty calendar day: the active test suggestions (the floating box's
    CP / AeT / zone retest items), each with ok / the reason this day doesn't suit it, and
    its templates (推薦 first). Accept = POST /suggestions/accept {id, day, test?, template}."""
    from backend.engine import plan_prefs as PP
    from backend.engine import suggestions as SG
    day = _iso_day(day)
    inp = await _inputs(db)
    today = _today(inp)
    stored = await PS.load(db)
    prefs = PP.load()
    bl = PS.blocked_map(inp)
    vis = SG.visible(await _all_suggestions(db, inp), await _dismissed(db))
    due = {t["kind"]: t for t in await _suggestions(db, inp, dismissed={})}
    out = []
    for r in vis:
        if r["type"] == "test":
            ents = [(r["kind"], None, r["title"], r.get("options") or [], bool((due.get(r["kind"]) or {}).get("replaces_long")),
                     None)]
        elif r["type"] == "zone_test":
            ents = [(t["key"], t["key"], t.get("label") or t["key"], t.get("options") or [], bool(t.get("replaces_long")),
                     r.get("earliest")) for t in r.get("tests") or [] if t.get("session")]
        else:
            continue
        for kind, test, label, opts, rl, earliest in ents:
            ok = day in {o["day"] for o in opts}
            out.append({"id": r["id"], "type": r["type"], "kind": kind, "test": test, "label": label, "ok": ok,
                        "reason": "" if ok else day_reason({"kind": kind, "replaces_long": rl}, stored, today, prefs, bl,
                                                           day, earliest),
                        "note": next((o.get("note") or "" for o in opts if o["day"] == day), ""),
                        "templates": await test_templates_for(kind, day, db)})
    return {"day": day, "options": out}


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
    from backend.engine.hr_profile import easy_cap_measured
    rate = float(((inp.get("cur") or {}).get("tss_per_category") or {}).get("trail") or 55.0) / 60.0
    long_s = {"id": "long", "kind": "long", "title": "LSD（山路）", "minutes": sg["minutes"][0],
              "tss": round(rate * sg["minutes"][0], 1), "detail": "", "target": "", "source": "", "terrain": None}
    ss = [long_s] + B2B.followers(long_s, {"minutes": sg["minutes"]})
    B2B.decorate(ss, {"event": {"name": sg.get("event"), "days": sg.get("event_days") or 2,
                                "kind": sg.get("event_kind")}, "weeks_out": sg.get("weeks_out")},
                 (inp.get("thresholds") or {}).get("aet"),
                 aet_measured=easy_cap_measured(inp.get("thresholds")))
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


async def _accept_race_sim(db: AsyncSession, inp: dict, sg: dict, day: Optional[str]) -> dict:
    """賽事模擬 (engine/specific_phase.py): the day (or day pair) becomes the user's
    session(s); the generator's long day (and B2B day 2) of that week is tombstoned —
    the simulation is that week's long day."""
    from backend.engine import plan_auto as PA
    opt = next((o for o in sg.get("options") or [] if o["day"] == day), None)
    if opt is None:
        raise HTTPException(400, "這天不適合排賽事模擬（不是可練日、在不排課日期內，或已經有你自己的課）")
    days = [opt["day"]] + ([opt["end"]] if sg.get("multi") and opt.get("end") else [])
    week = R.monday_of(days[0])
    end = (dt.date.fromisoformat(week) + dt.timedelta(days=6)).isoformat()
    added = []
    try:
        async with _wlock():
            for s, d in zip(sg.get("sessions") or [], days):
                data = {k: v for k, v in s.items() if v is not None}
                added.append(await PS.add(db, {**data, "day": d}, _today(inp), blocked=PS.blocked_map(inp)))
            for s in await PS.load(db):
                if (s["state"] == "active" and s.get("origin") == "auto" and s.get("gen_key") in ("long", "long2")
                        and s.get("day") and week <= s["day"] <= end):
                    await PS.delete(db, s["uid"])
            if await PA.pending(db) is None:
                await PS.plan_reconcile(db, inp, apply=True)
    except PS.PlanError as e:
        raise _err(e)
    return {"sessions": added}


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
#   GET  /api/v1/overview/plan/steps/templates/recs  its 「推薦」 block for one session (template_recs.py)
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


TPACE_CHART = "friel-pace-zones"           # views/periodization.json: Friel pace zones (shows the estimate)


@functools.lru_cache(maxsize=1)
def tpace_link() -> Optional[str]:
    """The viewer deep link to where threshold pace is estimated and shown (the Friel
    pace-zone chart, enlarged); None when the view isn't there."""
    import json
    from pathlib import Path
    from urllib.parse import urlencode
    try:
        p = Path(__file__).resolve().parents[2] / "views" / "periodization.json"
        v = json.loads(p.read_text("utf-8"))
        for di, d in enumerate(v.get("dashboards") or []):
            for ci, c in enumerate(d.get("charts") or []):
                if c.get("id") == TPACE_CHART:
                    return "/api/v1/static/wko5_viewer.html?" + urlencode({"view": v["name"], "dash": di, "chart": ci})
    except (OSError, ValueError, KeyError):
        pass
    return None


def pace_note(s: dict, thresholds: Optional[dict]) -> Optional[str]:
    """The push preview's note for a session whose stored steps have % / zone pace targets
    while there is no threshold pace (those steps reach the watch with no pace target)."""
    from backend.engine import workout_steps as WS
    if (thresholds or {}).get("tpace") or not s.get("steps"):
        return None
    try:
        return WS.no_tpace_text() if WS.needs_tpace(WS.normalize(s["steps"])) else None
    except WS.StepsError:
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
    # the push target's end conditions (sync/workout_targets; 「負荷」 only where it has one)
    prov = _provider()
    c.end_conditions, c.provider_label = tuple(prov.capabilities.end_conditions), prov.label
    rung = None
    if s.get("kind") == "quality":
        rung = s.get("rung_key") or getattr(IL.get(s.get("variant_key")), "rung", None)
    cap = day_cap(prefs, s.get("day")) if s.get("kind") not in ("test",) else None
    return {"ctx": c, "th": th, "policy": pol, "cap": cap, "cap_mode": getattr(prefs, "cap_mode", "soft"),
            "rung": rung if rung in IL.LIBRARY else None, "provider": prov.describe()}


def _provider():
    """The active workout provider (setting plan.push.provider; sync read like the engine's)."""
    from backend.engine.wko5expr.datasource import read_setting
    return WT.resolve(read_setting(WT.SETTING_KEY, WT.DEFAULT))


def _rpe_load_ctx() -> dict:
    from backend.engine import rpe_load as RL
    m = RL.current()
    return {"levels": RL.levels(), "min_range": list(RL.MIN_RANGE), "factor": round(m.factor, 4),
            "fitted": m.fitted, "n": m.n}


def _context(env: dict) -> dict:
    from backend.engine import target_policy as TP
    from backend.engine import workout_steps as WS
    th, pol = env["th"], env["policy"]
    return {"thresholds": {k: th.get(k) for k in ("cp", "lthr", "aet", "tpace", "cp_source", "lthr_source", "aet_source",
                                                  "thr_warn")},
            "tpace_link": tpace_link(),
            "zones": WS.zones_table(env["ctx"]), "policy": pol,
            "basis_label": f"目標用：{TP.LABEL[pol['basis']]}（{pol['why']}）",
            "cap": env["cap"], "cap_mode": env["cap_mode"], "rung": env["rung"],
            "kinds": WS.KIND_LABEL, "types": WS.TYPE_LABEL,
            # the editor's 時長類型 dropdown: the provider's end conditions + labels (SP-38)
            "provider": env.get("provider"), "load_kinds": list(WS.LOAD_KINDS), "load_range": list(WS.LOAD_RANGE),
            # 「負荷」 entered by feel (SP-57, engine/rpe_load.py): the five levels and the factor in effect
            "rpe_load": _rpe_load_ctx(),
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
    out = {**WS.view(st, env["ctx"], env["cap"], env["cap_mode"], env["rung"]),
           "basis_label": _context(env)["basis_label"], "policy": env["policy"]}
    if s.get("kind") == "quality":
        # the family these steps read as (SP-79: the 類型's 「步驟看起來像…」 hint)
        out["family"] = WTPL.family_of(st.get("items") or [], env["th"])
    if st.get("route") or st.get("tpl"):
        # made from a user template with a route GPX: the chart on the route's distance axis, the
        # session's own copy of the profile first (it outlives the template), else the template's
        from backend.engine import user_templates as UT
        prof = st.get("route") or await UT.profile_of(db, st["tpl"])
        out["elev"] = UT.route_elevation(st, env["ctx"], prof) if prof else None
    return out


@router.get("/steps/templates")
async def steps_templates(db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    from backend.engine import workout_steps as WS
    return WS.templates(user={"templates": await UT.list_all(db), "cats": await UT.custom_cats(db)})


# ---------------------------------------------------------------------------
# the user's own templates (engine/user_templates.py; 範本 page static/templates.html, SP-36)
#
#   GET    /steps/templates/user                   {templates, cats (built-in + custom)}
#   POST   /steps/templates/user                   {name, cats, steps, target_basis?, note?}
#   POST   /steps/templates/user/copy              {key}: a built-in 插入範本 row → 我的範本
#   PATCH  /steps/templates/user/{id}              any of name / cats / steps / target_basis / note
#   DELETE /steps/templates/user/{id}
#   POST   /steps/templates/user/{id}/gpx          upload / replace the training route (multipart file)
#   DELETE /steps/templates/user/{id}/gpx
#   GET    /steps/templates/user/{id}/gpx/file     the stored file
#   POST   /steps/templates/cats {label} · PATCH / DELETE /steps/templates/cats/{cid}
#   POST   /sessions/{uid}/save-as-template        {name, cats, steps?}: the session's structure
#          (the body's — the editor's current one —, else the stored, else derived)
# ---------------------------------------------------------------------------

def _tpl_err(e) -> HTTPException:
    return HTTPException(e.status, {"errors": e.errors} if e.status != 404 else e.errors[0])


async def _tpl_call(fn, *a, **kw):
    from backend.engine import user_templates as UT
    try:
        return await fn(*a, **kw)
    except UT.TemplateError as e:
        raise _tpl_err(e)


@router.get("/steps/templates/user")
async def user_templates_list(db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    from backend.engine import workout_templates as WTP
    return {"templates": [{**t, "row": UT.row(t)} for t in await UT.list_all(db)],
            "cats": WTP.cats() + await UT.custom_cats(db),
            "limits": {"name": UT.NAME_MAX, "cat": UT.CAT_MAX, "cats": UT.MAX_CATS, "templates": UT.MAX_TEMPLATES}}


@router.post("/steps/templates/user")
async def user_templates_create(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    return await _tpl_call(UT.create, db, body)


def _builtin_row(key: str) -> Optional[dict]:
    from backend.engine import workout_steps as WS
    for g in WS.templates()["groups"]:
        for r in g["rows"]:
            if r["key"] == key:
                return {**r, "cat": g["cat"]}
    return None


@router.post("/steps/templates/user/copy")
async def user_templates_copy(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """「複製成我的範本」: a built-in row (read-only) becomes the user's own, to change."""
    from backend.engine import user_templates as UT
    from backend.i18n import _
    r = _builtin_row(str(body.get("key") or ""))
    if r is None:
        raise HTTPException(404, _("找不到這個範本"))
    name = str(body.get("name") or "").strip() or r["title"] or r["label"]
    data = {"name": name[:UT.NAME_MAX], "cats": body.get("cats") or [r["cat"]], "steps": {"items": r.get("full") or r["items"]},
            "target_basis": r.get("basis") if r.get("basis") in UT.BASES else None,
            "note": r.get("src") or ""}
    return await _tpl_call(UT.create, db, data, copied_from=r["key"])


@router.patch("/steps/templates/user/{tid}")
async def user_templates_update(tid: int, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    return await _tpl_call(UT.update, db, tid, body)


@router.delete("/steps/templates/user/{tid}")
async def user_templates_delete(tid: int, db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    return await _tpl_call(UT.delete, db, tid)


@router.post("/steps/templates/user/{tid}/gpx")
async def user_templates_gpx(tid: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    from backend.engine.racepower import gpx as GPX
    data = await file.read(GPX.MAX_BYTES + 1)
    await _tpl_call(UT.get, db, tid)                   # 404 before the parse
    try:
        got = await run_in_threadpool(UT.parse_profile, data, file.filename or "")
    except UT.TemplateError as e:
        raise _tpl_err(e)
    return await _tpl_call(UT.save_gpx, db, tid, data, file.filename or "", parsed=got)


@router.delete("/steps/templates/user/{tid}/gpx")
async def user_templates_gpx_delete(tid: int, db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    return await _tpl_call(UT.delete_gpx, db, tid)


@router.get("/steps/templates/user/{tid}/gpx/file")
async def user_templates_gpx_file(tid: int, db: AsyncSession = Depends(get_db)):
    from urllib.parse import quote

    from fastapi.responses import Response

    from backend.engine import user_templates as UT
    from backend.i18n import _
    t = await _tpl_call(UT.get, db, tid)
    data = UT.read_gpx(tid) if t.get("gpx") else None
    if data is None:
        raise HTTPException(404, _("這個範本沒有 GPX"))
    name = t["gpx"].get("filename") or "route.gpx"
    mt = "application/gpx+xml" if not name.lower().endswith(".fit") else "application/octet-stream"
    return Response(content=data, media_type=mt,
                    headers={"Content-Disposition": f"attachment; filename=\"route\"; filename*=UTF-8''{quote(name)}"})


@router.post("/steps/templates/cats")
async def user_template_cat_add(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    return await _tpl_call(UT.add_cat, db, body.get("label"))


@router.patch("/steps/templates/cats/{cid}")
async def user_template_cat_rename(cid: str, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    return await _tpl_call(UT.rename_cat, db, cid, body.get("label"))


@router.delete("/steps/templates/cats/{cid}")
async def user_template_cat_delete(cid: str, db: AsyncSession = Depends(get_db)):
    from backend.engine import user_templates as UT
    return await _tpl_call(UT.delete_cat, db, cid)


@router.post("/sessions/{uid}/save-as-template")
async def save_as_template(uid: str, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    """儲存成範本: the editor's current structure when sent, else the stored one, else derived
    from the session's kind / text; the session's 目標用 goes with it."""
    from backend.engine import user_templates as UT
    from backend.engine import workout_steps as WS
    from backend.i18n import _
    s = next((x for x in await PS.load(db) if x["uid"] == uid), None)
    if s is None:
        raise HTTPException(404, _("找不到這堂課"))
    steps = body.get("steps") or s.get("steps")
    if not steps:
        inp = await _inputs()
        env = await _steps_env(s, inp)
        steps = WS.derive(s, env["th"])
    if not steps:
        raise HTTPException(400, {"errors": [_("這堂課看不出結構，沒辦法存成範本")]})
    data = {"name": body.get("name") or s.get("title") or "", "cats": body.get("cats") or [], "steps": steps,
            "target_basis": body.get("target_basis", s.get("target_basis")), "note": body.get("note")}
    return await _tpl_call(UT.create, db, data)


def _phase_on(inp: dict, day: Optional[str]) -> Optional[str]:
    """The training phase of `day`: its projected week's, else today's."""
    if day:
        for w in inp.get("weeks") or []:
            try:
                ws = dt.date.fromisoformat(w["start"])
            except (KeyError, TypeError, ValueError):
                continue
            if ws <= dt.date.fromisoformat(day) < ws + dt.timedelta(days=7) and w.get("phase"):
                return w["phase"]
    return (inp.get("phase") or {}).get("kind")


@router.get("/steps/templates/recs")
async def steps_template_recs(kind: str = "easy", day: Optional[str] = None, uid: Optional[str] = None,
                              minutes: Optional[float] = None, terrain: Optional[str] = None,
                              db: AsyncSession = Depends(get_db)):
    """插入範本's 「推薦」 block for one session (engine/template_recs.py): per category, the
    3 best templates with a reason, 我的範本 included (`mine`); 強度課's first is the interval
    ladder's next step (the old 間歇範本 ★ 推薦: interval_library.fit for the current rung
    and the day's cap)."""
    from backend.engine import quality_gate as QG
    from backend.engine import template_recs as TR
    from backend.engine import user_templates as UT
    from backend.engine import workout_steps as WS
    inp = await _inputs()
    s = next((x for x in await PS.load(db) if x["uid"] == uid), None) if uid else None
    day = day or (s or {}).get("day")
    ctx = _variant_ctx(inp, day)
    gate = ctx["gate"]
    # two tracks (SP-31): the session's own track picks the ladder (a Zone 5 session → the Zone 5 rung)
    from backend.engine import interval_library as IL
    track = IL.track_of((s or {}).get("rung_key") or getattr(IL.get((s or {}).get("variant_key")), "rung", None))
    rung_now = QG.rung_now(gate, track)
    key, why = TR.ladder_pick(rung_now, ctx["cap"], ctx["history"], ctx["prefs"])
    ter = terrain or (s or {}).get("terrain")
    ter = "trail" if kind == "hike" or ter in ("trail", "hike") else "road"
    # 我的範本 ranked with the built-ins (SP-36), the same menu /steps/templates gives
    tpl = WS.templates(user={"templates": await UT.list_all(db), "cats": await UT.custom_cats(db)})
    return TR.recommend(tpl, kind=kind, cap=ctx["cap"], minutes=minutes, terrain=ter,
                        phase=_phase_on(inp, day), z5_open=bool(QG.z5_track(gate)["open"]) if gate else False,
                        rung=rung_now, ladder_key=key, ladder_reason=why,
                        sport=(inp.get("cur") or {}).get("primary_sport") or "trail")


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


async def _with_route(db: AsyncSession, st: dict, was: Optional[dict]) -> None:
    """A structure made from a user template with a route GPX keeps its own copy of the
    profile (`route`, user_templates.route_copy), so deleting the template or its GPX later
    doesn't take it off the session: the stored copy carried over while the template is the
    same (a re-save from the editor), else taken from the template now."""
    if not st.get("tpl") or st.get("route"):
        return
    if was and was.get("tpl") == st["tpl"] and was.get("route"):
        st["route"] = was["route"]
        return
    from backend.engine import user_templates as UT
    r = UT.route_copy(await UT.profile_of(db, st["tpl"]))
    if r:
        st["route"] = r


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
    track = IL.track_of((s or {}).get("rung_key") or getattr(IL.get((s or {}).get("variant_key")), "rung", None))
    rung_now = QG.rung_now(gate, track)
    out = {"day": day, "cap": ctx["cap"], "cp": cp,
           "templates": IL.templates(cp, ctx["cap"], ctx["prefs"], ctx["history"], rung_now)}
    rung = (s.get("rung_key") or getattr(IL.get(s.get("variant_key")), "rung", None)) if s and s.get("variant_key") \
        else None
    if rung in IL.LIBRARY:
        out["drawer"] = IL.drawer(rung, cp, ctx["cap"], ctx["prefs"], ctx["history"], s.get("variant_key"))
        out["session"] = {k: s.get(k) for k in ("uid", "title", "variant_key", "rung_key", "equiv", "swap", "swap_reason")}
    return out


async def _unpush_expired(db: AsyncSession, deleted: list[dict]) -> dict:
    """Deleted expired sessions still on the watch's calendar come off it (a past-day
    push row is otherwise kept, see push_sessions). Through the active workout-sync
    provider; a login problem doesn't undo the delete, it is reported."""
    prov = await WT.active(db)
    rows = await prov.all_rows(db)
    keys = [s["uid"] for s in deleted if s["uid"] in rows]
    if not keys:
        return {"status": "none", "removed": 0}
    try:
        res = await prov.remove_keys(db, keys)
    except WT.SyncAuthError as e:
        return {"status": "auth", "removed": 0, "pending": len(keys), "error": str(e), "provider_label": prov.label}
    except Exception as e:                   # noqa: BLE001 — the delete itself is done
        return {"status": "failed", "removed": 0, "pending": len(keys), "error": f"{type(e).__name__}: {e}"[:300],
                "provider_label": prov.label}
    n = sum(1 for r in res or [] if (r or {}).get("status") == "removed")
    return {"status": "ok" if n == len(keys) else "partial", "removed": n, "pending": len(keys) - n,
            "provider_label": prov.label}


@router.post("/sessions/expired/delete")
async def delete_expired_sessions(body: Optional[dict] = Body(None), db: AsyncSession = Depends(get_db)):
    """{uids?}: delete the past sessions that weren't done (missed / still open on a past
    day); without uids all of them. Tombstones (never regenerated); pushed copies are
    removed from the provider's calendar."""
    uids = (body or {}).get("uids")
    if uids is not None and (not isinstance(uids, list) or len(uids) > 2000):
        raise HTTPException(400, "uids must be a list")
    inp = await _inputs()
    today = _today(inp)
    try:
        async with _wlock():
            out = await PS.delete_expired(db, today, uids)
    except PS.PlanError as e:
        raise HTTPException(400, str(e))
    coros = await _unpush_expired(db, out)
    return {"deleted": len(out), "uids": [s["uid"] for s in out], "coros": coros}


@router.delete("/sessions/{uid}")
async def delete_session(uid: str, db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    try:
        async with _wlock():
            out = await PS.delete(db, uid, today=_today(inp))
            # deleting either day of an accepted B2B cancels it (both days; declined for that week)
            if not out.get("expired") and await _b2b_cancelled(db, uid) is not None:
                out["b2b_cancelled"] = True
    except PS.PlanError as e:
        raise HTTPException(404, str(e))
    if out.get("expired"):                            # a past session never done: off the watch now
        out["coros"] = await _unpush_expired(db, [out])
        return out
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
    prov = await WT.active(db)
    rows = await prov.all_rows(db)
    bl = PS.blocked_map(inp)
    todo = [_view(s, inp, rows, today, prov) for s in _in_range(new, a, b, bl)]
    pushable = [s for s in todo if s["coros"]["status"] not in ("skipped", "done")]
    will = [s for s in pushable if s["coros"]["status"] != "pushed"]
    missed = [s for s in new if PS.off_watch(s) and s["uid"] in rows]
    on_bl = [s for s in _on_blocked(new, bl, today) if s["uid"] in rows]
    old_calc = await prov.rows_by_key(db, _old_calc_keys(pushable))
    return {**_meta(inp), "scope": scope, "start": a, "end": b, "sessions": todo,
            "count": len(pushable), "to_send": len(will), "unchanged": len(pushable) - len(will),
            "skipped": [s for s in todo if s["coros"]["status"] == "skipped"],
            "missed_to_remove": len(missed), "blackout_to_remove": len(on_bl),
            # the calculator's old direct push of an exported race (removed by this push)
            "calc_to_replace": len(old_calc),
            # sessions whose % / zone pace steps go out with no pace target (no threshold pace)
            "pace_notes": [{"uid": s["uid"], "title": s.get("title"), "day": s.get("day"), "text": s["coros"]["pace_note"]}
                           for s in pushable if s["coros"].get("pace_note")],
            "tpace_link": tpace_link(),
            "changes": changes, "by_day": R.by_day(changes)}


def _old_calc_keys(ss: list[dict]) -> list[str]:
    """The 賽事計算機's exported sessions among `ss` (ext_key racecalc:<event id>): a workout
    the calculator once pushed straight to the watch under that key is replaced by them."""
    return [s["ext_key"] for s in ss if str(s.get("ext_key") or "").startswith(CW.RACE_KEY_PREFIX)]


def _on_blocked(ss: list[dict], blocked: dict, today: str) -> list[dict]:
    return [s for s in ss if s["state"] == "active" and s.get("day") and s["day"] >= today and s["day"] in blocked]


def _auth(e: WT.SyncAuthError, prov=None):
    label = getattr(prov, "label", "COROS")
    code = "COROS_AUTH_REQUIRED" if getattr(prov, "id", "coros") == "coros" else "SYNC_AUTH_REQUIRED"
    return HTTPException(401, {"error": code, "detail": str(e), "hint": f"到設定頁重新登入 {label}"})


@router.post("/push-coros")
async def push(scope: str = "week", day: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    inp = await _inputs(db)
    a, b = _range(scope, day, inp)
    today = _today(inp)
    async with _wlock():
        await _ensure(db, inp)
        new, changes = await PS.plan_reconcile(db, inp, apply=True)
        live = {s["uid"] for s in new if s["state"] in ("active", "done", "missed")}
        prov = await WT.active(db)
        rows = await prov.all_rows(db)
        # pushed sessions gone from the plan (deleted / superseded / regenerated away);
        # past-day ones stay, see push_sessions. Missed ones are removed separately.
        stale = [k for k in rows if k not in live]
        bl = PS.blocked_map(inp)
        # an edited session still on a 不排課日期 (no decision yet) comes off COROS
        stale += [s["uid"] for s in _on_blocked(new, bl, today) if s["uid"] in rows]
        # a race exported from the 賽事計算機 replaces the workout the calculator once pushed
        # straight to the watch (same key, racecalc:<event id>; not in all_rows)
        stale += _old_calc_keys(_in_range(new, a, b, bl))
        missed = [s["uid"] for s in new if PS.off_watch(s) and s["uid"] in rows]
        try:
            res = await prov.push_sessions(db, [PS.push_dict(s) for s in _in_range(new, a, b, bl)], inp["thresholds"],
                                           today, stale_keys=stale, missed_keys=missed)
        except WT.SyncAuthError as e:
            raise _auth(e, prov)
    return {"scope": scope, "start": a, "end": b, "changes": changes, **res}


@router.delete("/push-coros")
async def unpush(scope: str = "week", day: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    inp = await _inputs()
    a, b = _range(scope, day, inp)
    async with _wlock():
        prov = await WT.active(db)
        rows = await prov.all_rows(db)
        keys = [k for k, r in rows.items() if r.day and a <= r.day <= b]
        try:
            return {"scope": scope, "start": a, "end": b, "removed": await prov.remove_keys(db, keys)}
        except WT.SyncAuthError as e:
            raise _auth(e, prov)


# ---------------------------------------------------------------------------
# 課表偏好 (engine/plan_prefs.py) and same-load conversion (engine/equivalence.py)
#
#   GET /api/v1/overview/plan/prefs               the stored preferences (+ defaults)
#   PUT /api/v1/overview/plan/prefs               validate + save; the page then opens
#                                                 the reconcile preview (GET /reconcile)
#   GET /api/v1/overview/plan/equivalence         the athlete's time model + LOO backtest
#   POST /api/v1/overview/plan/equivalence/design {mode, minutes, climb_per_km} -> km / gain
# ---------------------------------------------------------------------------

def _prefs_body(p, dropped=()) -> dict:
    from backend.engine import plan_prefs as PP
    from backend.engine import quality_gate as QG
    # gate_options: the 間歇門檻 hover texts (the page adds "usable with your data"
    # from GET /prefs/gate, which needs the dataset)
    from backend.engine import aet_test as AT
    # aet_options: the AeT 測試方式 hover texts (duration, terrain, what is held, judging, source)
    return {"prefs": p.to_dict(), "defaults": PP.Prefs().to_dict(), "active": p.active,
            # 偏好的星期 vs the default rules (shown when the prefs are saved; 照我的偏好 = pref_keep)
            "day_conflicts": PP.day_conflicts(p),
            # stored 偏好的星期 that shared a weekday with an earlier type (drop_overlaps):
            # removed on load, shown next to their row until the athlete saves
            "pref_dropped": list(dropped),
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
    return _prefs_body(*PP.drop_overlaps(PP.from_settings({k: await repo.get(k) for k in PP.KEY_FIELDS})))


@router.post("/prefs/conflicts")
def post_prefs_conflicts(body: dict = Body(...)):
    """偏好的星期 conflicts of an unsaved preference set (the dialog's live hints); nothing is stored."""
    from backend.engine import plan_prefs as PP
    try:
        p = PP.from_body(body)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    return {"day_conflicts": PP.day_conflicts(p), "overlaps": PP.overlaps(p)}


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


# 休息日 (the 課表 calendar's context menu on an empty day): a one-day 不排課日期 of
# kind "rest" — the planner skips the day and puts the week's volume on its other
# days (engine/blackouts.lost_days). Undo = the same menu.
#   POST   /api/v1/overview/plan/rest-days        {day}
#   DELETE /api/v1/overview/plan/rest-days/{day}
REST_LABEL = "休息日"


async def _save_blackouts(db: AsyncSession, cand: list[dict], dec: Optional[dict] = None) -> dict:
    from backend.engine import blackouts as BL
    from backend.settings.repository import SettingsRepository
    try:
        await SettingsRepository(db).set(BL.KEY, cand)
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.commit()
    inp = await _inputs(db, blackouts=cand)
    async with _wlock():
        await _ensure(db, inp)
        _, changes = await PS.plan_reconcile(db, inp, apply=True, decisions=dec or {})
    return {**_meta(inp), "blackouts": cand, "changes": changes, "by_day": R.by_day(changes)}


def _iso_day(day) -> str:
    try:
        return dt.date.fromisoformat(str(day or "")[:10]).isoformat()
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")


@router.post("/rest-days")
async def add_rest_day(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.engine import blackouts as BL
    from backend.i18n import _
    from backend.settings.repository import SettingsRepository
    day = _iso_day((body or {}).get("day"))
    inp = await _inputs(db)
    if day < _today(inp):
        raise HTTPException(400, _("過去的日子不能設成休息日"))
    cur = list(await SettingsRepository(db).get(BL.KEY) or [])
    if day in BL.blocked(BL.from_list(cur)):
        raise HTTPException(400, _("這天已經是不排課日期或休息日"))
    cand = BL.normalize(cur + [{"start": day, "end": day, "label": REST_LABEL, "kind": BL.REST}])
    # the user chose to rest that day: their own sessions there move to another day of the week
    stored = await PS.load(db)
    dec = {s["uid"]: "move" for s in stored if s.get("day") == day and s["state"] == "active"
           and (s.get("edited") or s.get("origin") != "auto")}
    return await _save_blackouts(db, cand, dec)


@router.delete("/rest-days/{day}")
async def remove_rest_day(day: str, db: AsyncSession = Depends(get_db)):
    from backend.engine import blackouts as BL
    from backend.i18n import _
    from backend.settings.repository import SettingsRepository
    day = _iso_day(day)
    cur = list(await SettingsRepository(db).get(BL.KEY) or [])
    keep = [r for r in cur if not (r.get("kind") == BL.REST and r.get("start") == day)]
    if len(keep) == len(cur):
        raise HTTPException(404, _("這天不是休息日"))
    return await _save_blackouts(db, BL.normalize(keep))


async def _accept_injury_rest(db: AsyncSession, sg: dict) -> dict:
    """「設成不排課」 of an injury_rest suggestion: the days of [start, end] not
    blocked yet become 不排課日期 (label 傷停), then the plan reconciles as a
    PUT /blackouts without decisions (edited sessions are reported, not moved)."""
    from backend.engine import blackouts as BL
    from backend.settings.repository import SettingsRepository
    cur = BL.normalize(await SettingsRepository(db).get(BL.KEY) or [])
    have = set(BL.blocked(BL.from_list(cur)))
    a, b = dt.date.fromisoformat(sg["start"]), dt.date.fromisoformat(sg["end"])
    free = [a + dt.timedelta(days=i) for i in range((b - a).days + 1)
            if (a + dt.timedelta(days=i)).isoformat() not in have]
    added = [{"id": BL.new_id(), "start": lo.isoformat(), "end": hi.isoformat(), "label": "傷停"}
             for lo, hi in BL._runs(free)]
    cand = BL.normalize(cur + added)
    try:
        await SettingsRepository(db).set(BL.KEY, cand)
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
    await db.commit()
    inp = await _inputs(db, blackouts=cand)
    async with _wlock():
        await _ensure(db, inp)
        _, changes = await PS.plan_reconcile(db, inp, apply=True, decisions={})
    return {"sessions": [], "blackouts": added, "changes": changes}


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
    every = [{"kind": p.kind, "label": p.label, "start": p.start, "end": p.end} for p in planning.phases(st.plan, lo, hi)]
    phases = [p for p in every if p["end"] >= start and p["start"] <= end]
    # the plan's phases from a year back on: 課表統計's period filter offers each one (an auto
    # phase's start depends on the window, so a long one is cut at a year back: a stable id)
    since = (today - dt.timedelta(days=MAX_COMPLIANCE_DAYS)).isoformat()
    plan_phases = sorted(({**p, "start": max(p["start"], since)} for p in every if p["end"] >= since),
                         key=lambda p: p["start"])
    goal_d = ((st.goals.get("targets") or {}).get("climb_per_km") or {}).get("value")
    return {"activities": acts, "phases": phases, "plan_phases": plan_phases,
            "tph": O._tss_per_hour(ds, today), "goal_climb_per_km": goal_d}


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


def _day_rows(start: str, end: str, sessions: list[dict], acts: list[dict], rates: dict) -> list[dict]:
    """Per day in [start, end]: planned (same sessions and strength rule as _week_rows,
    future ones included) vs done (the activities) — 課表統計's 天 / 月 buckets."""
    out = []
    d, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while d <= last:
        a = d.isoformat()
        ss = [s for s in sessions if s.get("day") == a and s["state"] in ("active", "done", "missed")
              and s["kind"] not in PS.NOT_LOAD]
        aa = [x for x in acts if (x.get("date") or "")[:10] == a]
        out.append({"start": a, "end": a,
                    "planned_hours": sum(s["minutes"] or 0 for s in ss if s["kind"] != "strength") / 60.0,
                    "planned_tss": sum(est_tss(s, rates) for s in ss),
                    "done_hours": sum(float(x.get("moving_s") or 0) for x in aa) / 3600.0,
                    "done_tss": sum(float(x.get("tss") or 0) for x in aa)})
        d += dt.timedelta(days=1)
    return out


async def _coros_state(db: AsyncSession, views: list[dict]) -> dict:
    from sqlalchemy import select
    from backend.db.models import SyncState
    st = (await db.execute(select(SyncState).where(SyncState.athlete_id == current_athlete_id()))).scalar_one_or_none()
    authed = bool(st and st.coros_access_token)
    exp = getattr(st, "coros_token_expires", None) if st else None
    if authed and exp is not None:
        exp = exp if exp.tzinfo else exp.replace(tzinfo=dt.timezone.utc)
        authed = dt.datetime.now(dt.timezone.utc) < exp
    prov = await WT.active(db)
    rows = await prov.all_rows(db)
    last = max((r.pushed_at for r in rows.values() if r.pushed_at), default=None)
    n = lambda k: sum(1 for s in views if s["state"] == "active" and (s.get("coros") or {}).get("status") == k)
    return {"provider": prov.id, "provider_label": prov.label,
            "authenticated": authed, "last_pushed_at": last.isoformat() if last else None,
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


def _decorate(every: list[dict], acts: list[dict], rates: dict, start: str, end: str) -> list[dict]:
    """The stored sessions in [start, end] with tss_est, planned vs actual (vs) and
    compliance (engine/compliance.py) — the calendar's and the dashboard's shape."""
    from backend.engine import activity_key as AK
    from backend.engine import compliance as C
    acts_by = {x.get("index"): x for x in acts}
    AK.rebase_done_by(every, acts)                  # done_by -> this source's indexes, by start
    out = []
    for s in every:
        if s.get("day") and start <= s["day"] <= end:
            est = est_tss(s, rates)
            s = _fresh_done_by(s, acts_by)
            vs = PM.compare(s)
            out.append({**s, "tss_est": round(est, 1), "vs": vs,
                        "compliance": C.with_plan_check(C.session_compliance(s, est), vs)})
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
    ss = _decorate(every, extras["activities"], rates, start, end)
    _link_options(ss, every, extras["activities"], body["today"])
    tt = P.target_texts(inp["thresholds"] or {})
    return {**{k: v for k, v in body.items() if k != "sessions"}, "start": start, "end": end,
            "sessions": ss, "activities": extras["activities"], "phases": extras["phases"],
            "week_rows": _week_rows(start, end, ss, extras["activities"], extras["phases"], inp["weeks"], rates,
                                    body["today"]),
            "compliance_levels": C.COMPLIANCE,
            "thresholds": inp["thresholds"], "tss_per_hour": tph, "tss_rates": rates,
            "targets": {k: tt.get(v, "") for k, v in KIND_TARGET.items()},
            "kinds": PS.KINDS, "default_titles": PS.DEFAULT_TITLES, "family_titles": PS.FAMILY_TITLES,
            "prefs": inp.get("prefs"), "goal_climb_per_km": extras.get("goal_climb_per_km"),
            "plan_notes": _plan_notes(inp, start, end),
            "test_suggestions": await _suggestions(db, inp),
            "test_templates": _test_templates(inp["thresholds"] or {}, None),
            # 刪除所有過期未完成: how many past sessions were never done (whole plan, not just the range)
            "expired_open": sum(1 for s in every if PS.is_expired_open(s, body["today"])),
            "coros": await _coros_state(db, every)}


@router.get("/schedule/page", include_in_schema=False)
def schedule_page():
    return render_page("schedule")


# ---------------------------------------------------------------------------
# 課表達成率 (engine/compliance.dashboard)
#
#   GET /api/v1/overview/plan/compliance?start=&end=   the due sessions of [start, end]
#       with status and %, totals, weeks (planned vs actual TSS / hours, completion),
#       the streak, per kind, and the current phase's progress
#   GET /api/v1/overview/plan/compliance/page          the page (a tab of 課表)
# ---------------------------------------------------------------------------

MAX_COMPLIANCE_DAYS = 371


@router.get("/compliance")
async def compliance(start: str, end: str, db: AsyncSession = Depends(get_db)):
    from backend.engine import compliance as C
    try:
        a, b = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
    except ValueError:
        raise HTTPException(400, "start / end must be YYYY-MM-DD")
    if b < a:
        raise HTTPException(400, "end before start")
    if (b - a).days > MAX_COMPLIANCE_DAYS:
        raise HTTPException(400, f"at most {MAX_COMPLIANCE_DAYS} days")
    body = await sessions(start=None, end=None, db=db)      # reconciles (done / missed) like the calendar
    every = body["sessions"]
    today = body["today"]
    inp = await _inputs()
    ph = inp.get("phase")
    lo = min(a.isoformat(), ph["start"]) if ph else a.isoformat()
    hi = max(b.isoformat(), ph["end"]) if ph else b.isoformat()
    # activities: the range and the phase so far (the phase may reach into the future)
    extras = await run_in_threadpool(_range_extras, lo, min(hi, max(today, b.isoformat())))
    tph = float(((inp["cur"].get("target") or {}).get("tss_per_hour")) or 50.0)
    rates = tss_rates(extras.get("tph"), every, tph)
    allss = _decorate(every, extras["activities"], rates, lo, hi)
    start, end = a.isoformat(), b.isoformat()
    ss = [s for s in allss if start <= s["day"] <= end]
    acts = [x for x in extras["activities"] if start <= (x.get("date") or "") <= end]
    phases = [p for p in extras["phases"] if p["end"] >= start and p["start"] <= end]
    weeks = _week_rows(start, end, ss, acts, phases, inp["weeks"], rates, today)
    days = _day_rows(start, end, ss, acts, rates)
    out = C.dashboard(ss, weeks, today, start, end, phase=ph, phase_sessions=allss, day_rows=days)
    return {**out, "phases": phases, "current_phase": ph, "kinds": PS.KINDS,
            "plan_phases": extras.get("plan_phases", extras["phases"]),
            "plan_start": min((s["day"] for s in every if s.get("day")), default=None)}


@router.get("/compliance/page", include_in_schema=False)
def compliance_page():
    return render_page("compliance")


@router.get("/templates/page", include_in_schema=False)
def templates_page():
    """範本 (static/templates.html, SP-36): a tab of 課表 — the user's own templates and the library."""
    return render_page("templates")
