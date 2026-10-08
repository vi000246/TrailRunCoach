"""
Debug API for AI agents (SP-371, docs/debug-api.md): the server's own view of an activity, a
plan range, a day, the thresholds, the sync and the athlete's settings — read-only, over HTTPS,
with a dedicated token (backend/debug_auth.py: `X-TRC-Debug-Token`, or `Authorization: Bearer`).

  GET /api/v1/debug/activity?date= | id= | label= [&streams=hr,power,…&every=10][&gps=1]   read:activity
                                       (+ read:plan for the full plan row, + read:gps for gps=1)
  GET /api/v1/debug/plan?from=&to=                                                         read:plan
  GET /api/v1/debug/day?date=                                                  read:plan + read:activity
  GET /api/v1/debug/thresholds?date=                                       read:plan or read:activity
  GET /api/v1/debug/sync[?lines=200]                                                       read:sync
  GET /api/v1/debug/export/config                                                      export:config

  (web, 設定 › 進階 › Debug API — the page's own session, never a debug token; writes need a
  same-origin request: Sec-Fetch-Site / Origin)
  GET    /api/v1/settings/debug-api                      state, tokens, the last 100 calls, failures
  PUT    /api/v1/settings/debug-api                      {enabled}
  POST   /api/v1/settings/debug-api/tokens               {pin, name, scopes, days} → the token, once
  DELETE /api/v1/settings/debug-api/tokens/{id}          revoke one
  POST   /api/v1/settings/debug-api/tokens/revoke-all

Read-only for real: the plan views never call plan_sessions.sessions() (it takes the writer lock,
reconciles and records snapshots); they compute the same thing in memory — plan_store.match_only
for the matches, plan_store.reconcile_with_adapt for 「what a page load would change」 — and only
the debug tables (debug_tokens / debug_audit / debug_auth_failures) are written. Never a COROS /
TP call. Bad input is a 400 (never a 500); an unexpected error is audited and answered as a
JSON 500 with the exception's type only (AuditedRoute).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi import Path as Path_
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from backend import debug_auth as DA
from backend.db.database import get_db
from backend.engine import debug_view as DV
from backend.i18n import _

log = logging.getLogger("backend.api.debug")

MAX_PLAN_DAYS = 120
DAY_MIN, DAY_MAX = dt.date(1970, 1, 1), dt.date(2100, 12, 31)
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


async def _audit_late(request: Request, status: int) -> None:
    """Audit an error raised after the gate let the call in (request.state.debug_ctx), on a
    session of its own (the request's may be closed)."""
    ctx = getattr(request.state, "debug_ctx", None)
    if ctx is None:
        return
    gen = request.app.dependency_overrides.get(get_db, get_db)()
    try:
        db = await gen.__anext__()
        await DA.audit(db, ctx, status)
    except Exception:                       # noqa: BLE001 — the answer must still go out
        log.warning("debug API: could not audit a %s", status)
    finally:
        await gen.aclose()


class AuditedRoute(APIRoute):
    """A debug route: a validation error → 400 {code: BAD_REQUEST}; any other exception → audited,
    logged, a JSON 500 {code: INTERNAL, error: <type>} (no traceback, no message)."""

    def get_route_handler(self):
        inner = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await inner(request)
            except HTTPException:
                raise
            except RequestValidationError as e:
                await _audit_late(request, 400)
                errs = [{"loc": list(x.get("loc") or ()), "msg": str(x.get("msg") or "")} for x in e.errors()]
                return JSONResponse({"detail": {"code": "BAD_REQUEST", "errors": errs}}, status_code=400)
            except Exception as e:          # noqa: BLE001
                log.exception("debug API: %s failed", request.url.path)
                await _audit_late(request, 500)
                return JSONResponse({"detail": {"code": "INTERNAL", "error": type(e).__name__}}, status_code=500)
        return handler


def same_origin(request: Request) -> None:
    """The settings writes (L1): a cross-site request is refused. Browsers send Sec-Fetch-Site;
    without it, an Origin must match the Host. A non-browser client (neither header) passes —
    it has no cookies a forged request could ride on."""
    sfs = (request.headers.get("sec-fetch-site") or "").lower()
    if sfs:
        if sfs not in ("same-origin", "none"):
            raise HTTPException(403, {"code": "CROSS_SITE"})
        return
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if origin == "null" or urlparse(origin).netloc.lower() != host.split(",")[0].strip().lower():
        raise HTTPException(403, {"code": "CROSS_SITE"})


router = APIRouter(prefix="/api/v1/debug", tags=["debug"], include_in_schema=False, route_class=AuditedRoute)
admin_router = APIRouter(prefix="/api/v1/settings/debug-api", tags=["settings"], include_in_schema=False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dataset():
    """The chart pages' Dataset (tests replace this)."""
    from backend.api.wko5views import _dataset as ds
    return ds()


async def _bad(ctx: DA.Ctx, db: AsyncSession, status: int, msg: str) -> HTTPException:
    await DA.audit(db, ctx, status)
    return HTTPException(status, msg)


def parse_day(v: Optional[str], name: str) -> Optional[dt.date]:
    """Strict YYYY-MM-DD within 1970–2100; None for empty; ValueError otherwise."""
    if v in (None, ""):
        return None
    s = str(v)
    if not _ISO_DAY.fullmatch(s):
        raise ValueError(f"{name} must be YYYY-MM-DD")
    try:
        d = dt.date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"{name} must be a real date (YYYY-MM-DD)")
    if not DAY_MIN <= d <= DAY_MAX:
        raise ValueError(f"{name} must be between {DAY_MIN} and {DAY_MAX}")
    return d


async def finish(ctx: DA.Ctx, db: AsyncSession, body: dict, ds=None) -> Response:
    """The answer: `meta` + body, made plain JSON, scrubbed (GPS unless ?gps=1, credentials),
    audited with its size."""
    meta = {"app_version": DV.git_sha(), "schema_version": DV.SCHEMA_VERSION, "endpoint": ctx.path,
            "query": DA.masked_query(ctx.query), "caller": ctx.name,
            "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "data_generation": DV.data_generation(ds), "cache_versions": DV.cache_versions(),
            "elapsed_ms": int((time.monotonic() - ctx.started) * 1000)}
    out = DV.scrub(DV.jsonable({"meta": meta, **body}), ctx.gps)
    data = json.dumps(out, ensure_ascii=False).encode("utf-8")
    await DA.audit(db, ctx, 200, len(data))
    return Response(data, media_type="application/json",
                    headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


async def _dataset_or_none():
    try:
        return await run_in_threadpool(_dataset), None
    except Exception as e:                  # noqa: BLE001 — the answer says so instead
        return None, type(e).__name__


async def _today(ds) -> dt.date:
    """The plan's today (plan_sessions._today: the athlete's date, at least the server's); without a
    dataset, the athlete's local date (engine/localtime.today_local)."""
    if ds is not None:
        try:
            from backend.api import plan_sessions as PSA
            return dt.date.fromisoformat(PSA._today(await PSA._inputs(None)))
        except Exception:                   # noqa: BLE001
            pass
    from backend.engine.localtime import today_local
    return today_local()


# ---------------------------------------------------------------------------
# the plan, computed without writing (what the 課表 page would show)
# ---------------------------------------------------------------------------

async def plan_base(db: AsyncSession) -> dict:
    """The generator inputs and the stored rows with a page load's matches applied in memory."""
    from backend.api import plan_sessions as PSA
    from backend.engine import plan_store as PS
    inp = await PSA._inputs(db)
    every = await PS.load(db)
    matched, match_changes = PS.match_only(every, inp)
    return {"inp": inp, "stored": every, "matched": matched, "match_changes": match_changes,
            "today": PSA._today(inp)}


async def plan_view(db: AsyncSession, a: dt.date, b: dt.date, base: Optional[dict] = None) -> dict:
    """The stored plan in [a, b] (≤ MAX_PLAN_DAYS) as the 課表 calendar shows it
    (plan_sessions.calendar → _decorate: planned vs actual, compliance)."""
    from backend.api import plan_sessions as PSA
    if (b - a).days > MAX_PLAN_DAYS:
        raise ValueError(f"at most {MAX_PLAN_DAYS} days")
    base = base or await plan_base(db)
    inp = base["inp"]
    extras = await run_in_threadpool(PSA._range_extras, a.isoformat(), b.isoformat())
    acts = extras.get("activities") or []
    visible = [copy.deepcopy(s) for s in base["matched"] if s["state"] not in ("deleted", "superseded")]
    tph = float(((inp["cur"].get("target") or {}).get("tss_per_hour")) or 50.0)
    rates = PSA.tss_rates(extras.get("tph"), visible, tph)
    ss = PSA._decorate(visible, acts, rates, a.isoformat(), b.isoformat())
    return {**base, "sessions": ss, "activities": acts, "rates": rates, "phases": extras.get("phases")}


def _would_change(stored: list[dict], inp: dict, a: str, b: str) -> dict:
    """What the next page load / sync would change in [a, b] (reconcile + adapt), computed only."""
    from backend.engine import plan_store as PS
    adj: list = []
    try:
        _new, changes = PS.reconcile_with_adapt(copy.deepcopy(stored), copy.deepcopy(inp), adjustments=adj)
    except Exception as e:                  # noqa: BLE001
        return {"error": type(e).__name__}
    inr = lambda x: a <= str(x.get("day") or "") <= b      # noqa: E731
    return {"changes": [c for c in changes if inr(c)], "adapt": [x for x in adj if inr(x) or not x.get("day")],
            "why": _("reconcile 規則（engine/reconcile.py）＋自動調整（engine/adapt.py）；只算不寫")}


def _pair_why(s: dict) -> str:
    vs, comp = s.get("vs") or {}, s.get("compliance") or {}
    parts = []
    if vs.get("match_label"):
        parts.append(_("配對方式：{m}", m=vs["match_label"]))
    if comp.get("label"):
        parts.append(_("完成度：{label}", label=comp["label"]))
    if comp.get("duration_pct") is not None:
        parts.append(_("時間 {p}%", p=comp["duration_pct"]))
    if comp.get("tss_pct") is not None:
        parts.append(_("TSS {p}%", p=comp["tss_pct"]))
    if vs.get("text") or vs.get("short_text"):
        parts.append(vs.get("text") or vs.get("short_text"))
    parts.append(_("等級：|實際 ÷ 計畫 − 1| ≤ 20% 符合、≤ 50% 有點偏離（TrainingPeaks）；強度見 plan_match.compare"))
    return "；".join(parts)


SESSION_FIELDS = ("uid", "day", "week_start", "kind", "title", "minutes", "target", "detail", "terrain",
                  "distance_km", "climb_m", "tss", "tss_est", "gen_key", "origin", "edited", "state", "note",
                  "variant_key", "rung_key", "provisional", "protocol", "family", "steps")


def _session_row(s: dict, today: str) -> dict:
    from backend.engine import compliance as C
    out = {k: s.get(k) for k in SESSION_FIELDS if k in s}
    d = s.get("done_by") if s.get("state") == "done" and isinstance(s.get("done_by"), dict) else None
    out["activity"] = None if d is None else {k: d.get(k) for k in (
        "index", "start", "date", "category", "category_label", "moving_s", "tss", "hard_s", "session", "match")}
    out["vs"] = s.get("vs")
    out["compliance"] = s.get("compliance")
    out["status"] = C.status_of(s, s.get("compliance"), today)
    out["why"] = _pair_why(s) if d is not None else (
        _("沒做：那天沒有對應的活動（reconcile 規則：過了那天、資料也涵蓋那天）") if s.get("state") == "missed"
        else _("還沒配對到活動"))
    return out


def _unmatched_reason(a: dict, sessions: list[dict], unlinked: set) -> str:
    from backend.engine import plan_match as PM
    if a.get("index") in unlinked:
        return _("使用者解除配對過，不會再自動配對（可以手動配）")
    same = [s for s in sessions if s.get("day") == a.get("date")]
    if not same:
        return _("那天沒有排課（輕鬆跑不會被拉去配別天的課）")
    why = []
    for s in same:
        d = s.get("done_by") if s.get("state") == "done" and isinstance(s.get("done_by"), dict) else None
        if d is not None and d.get("index") != a.get("index"):
            why.append(_("「{t}」已配給另一筆活動 #{i}", t=s.get("title"), i=d.get("index")))
        elif not PM.can_match(s, a):
            why.append(_("「{t}」（{k}）跟這筆活動的類型不符", t=s.get("title"), k=s.get("kind")))
    return "；".join(why) or _("同一天的課都已配對")


def _pairing(pv: dict, a: str, b: str) -> dict:
    today = pv["today"]
    rows = [_session_row(s, today) for s in pv["sessions"] if a <= (s.get("day") or "") <= b]
    used = {r["activity"]["index"] for r in rows if r.get("activity")}
    unlinked = set(pv["inp"].get("unlinked") or ())
    acts = [x for x in pv["activities"] if a <= (x.get("date") or "") <= b]
    loose = [{"index": x.get("index"), "date": x.get("date"), "start": x.get("start"),
              "category": x.get("category"), "moving_s": x.get("moving_s"), "tss": x.get("tss"),
              "session": x.get("session"), "why": _unmatched_reason(x, pv["sessions"], unlinked)}
             for x in acts if x.get("index") not in used]
    return {"sessions": rows, "unmatched_activities": loose, "today": today}


async def _activity_pairs(db: AsyncSession, days: list[dt.date]) -> dict:
    """{index: (session row or None, unmatched entry or None)} for the activities of `days`: one
    plan view over the span when it is ≤ MAX_PLAN_DAYS, else one per activity day (M4)."""
    base = await plan_base(db)
    spans = [(min(days), max(days))] if (max(days) - min(days)).days <= MAX_PLAN_DAYS \
        else [(d, d) for d in sorted(set(days))]
    out: dict = {}
    for a, b in spans:
        pr = _pairing(await plan_view(db, a, b, base), a.isoformat(), b.isoformat())
        for r in pr["sessions"]:
            if r.get("activity"):
                out[r["activity"]["index"]] = (r, None)
        for x in pr["unmatched_activities"]:
            out.setdefault(x["index"], (None, x))
    return out


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.get("/activity")
async def activity(date: Optional[str] = None, id: Optional[int] = Query(None, ge=1, le=2 ** 62),
                   label: Optional[str] = Query(None, max_length=100), streams: Optional[str] = Query(None, max_length=80),
                   every: int = Query(10, ge=1, le=600),
                   ctx: DA.Ctx = Depends(DA.need("read:activity")), db: AsyncSession = Depends(get_db)):
    if sum(x not in (None, "") for x in (date, id, label)) != 1:
        raise await _bad(ctx, db, 400, "give exactly one of date=YYYY-MM-DD, id=<workout_files id>, label=")
    try:
        day = parse_day(date, "date")
    except ValueError as e:
        raise await _bad(ctx, db, 400, str(e))
    file_name = None
    if id is not None:
        from backend.db.models import WorkoutFile
        fp = (await db.execute(select(WorkoutFile.file_path).where(WorkoutFile.id == id))).scalar_one_or_none()
        if fp is None:
            raise await _bad(ctx, db, 404, "no workout_files row with that id")
        file_name = Path(str(fp).replace("\\", "/")).name
    ds, err = await _dataset_or_none()
    if ds is None:
        return await finish(ctx, db, {"activities": [], "error": err,
                                      "why": _("資料集讀不到（還沒同步，或正在重建）")})
    ws = await run_in_threadpool(DV.find_workouts, ds, day, file_name, label)
    names = [n for n in (streams or "").split(",") if n.strip()]
    truncated = None
    if (names or ctx.gps) and len(ws) > DV.MAX_STREAM_ACTIVITIES:
        truncated = {"found": len(ws), "returned": DV.MAX_STREAM_ACTIVITIES,
                     "why": f"streams / gps: at most {DV.MAX_STREAM_ACTIVITIES} activities per call"}
        ws = ws[:DV.MAX_STREAM_ACTIVITIES]
    out = []
    for w in ws:
        try:
            out.append(await run_in_threadpool(DV.activity_detail, ds, w, names, every, ctx.gps))
        except Exception as e:              # noqa: BLE001 — one bad file never hides the others
            out.append({"index": w.idx, "error": type(e).__name__})
    if out:
        from backend.engine import overview as O
        full = "read:plan" in ctx.scopes
        try:
            pairs = await _activity_pairs(db, [O.wdate(w) for w in ws])
            for d in out:
                s, loose = pairs.get(d["index"], (None, None))
                why = s["why"] if s else (loose or {}).get("why") or _("沒有配對")
                if full:
                    d["plan"] = {"session": s, "matched": s is not None, "why": why,
                                 "source": _("plan_match（課表頁同一套規則；只算不寫）")}
                else:                       # read:activity only: which session and its level, no plan data
                    d["plan"] = {"matched": s is not None, "uid": (s or {}).get("uid"),
                                 "level": ((s or {}).get("compliance") or {}).get("level"),
                                 "why": _("要看配到的那一堂課的內容，token 需要 read:plan")}
        except Exception as e:              # noqa: BLE001
            for d in out:
                d["plan"] = {"error": type(e).__name__}
    body = {"activities": out, "count": len(out), "lookup": {"date": date, "id": id, "label": label}}
    if truncated:
        body["truncated"] = truncated
    return await finish(ctx, db, body, ds)


@router.get("/plan")
async def plan_range(start: Optional[str] = Query(None, alias="from"), end: Optional[str] = Query(None, alias="to"),
                     ctx: DA.Ctx = Depends(DA.need("read:plan")), db: AsyncSession = Depends(get_db)):
    from backend.api import plan_sessions as PSA
    from backend.engine import plan_store as PS
    from backend.engine import reconcile as R
    from backend.sync import workout_targets as WT
    try:
        a, b = parse_day(start, "from"), parse_day(end, "to")
    except ValueError as e:
        raise await _bad(ctx, db, 400, str(e))
    try:
        inp = await PSA._inputs(db)
    except Exception as e:                  # noqa: BLE001
        return await finish(ctx, db, {"error": type(e).__name__, "why": _("課表的輸入算不出來（資料集讀不到？）")})
    today = PSA._today(inp)
    if a is None:
        a = dt.date.fromisoformat(R.monday_of(today))
    if b is None:
        b = a + dt.timedelta(days=6)
    if b < a or (b - a).days > MAX_PLAN_DAYS:
        raise await _bad(ctx, db, 400, f"to must be on or after from, at most {MAX_PLAN_DAYS} days")
    A, B = a.isoformat(), b.isoformat()
    every = await PS.load(db)
    matched, _mc = PS.match_only(every, inp)
    prov = await WT.active(db)
    rows = await prov.all_rows(db)
    inr = lambda s: A <= (s.get("day") or "") <= B            # noqa: E731
    stored = []
    for s in sorted((x for x in matched if inr(x)), key=lambda s: (s.get("day") or "", s["uid"])):
        v = PSA._view(s, inp, rows, today, prov)
        orig = next((x for x in every if x["uid"] == s["uid"]), None)
        v["stored_state"] = (orig or {}).get("state")
        v["deleted"] = s["state"] in ("deleted", "superseded")
        stored.append(v)
    gen = [w for w in PS.gen_weeks(inp)
           if w["start"] <= B and (dt.date.fromisoformat(w["start"]) + dt.timedelta(days=6)).isoformat() >= A]
    from backend.engine import plan_auto as PA
    from backend.settings.repository import AUTO_KEYS, SettingsRepository
    from backend.db.models import PlanChangeLog
    repo = SettingsRepository(db)
    auto = {k: await repo.get(k) for k in AUTO_KEYS}
    auto["plan.auto.state"] = await repo.get("plan.auto.state")
    pend = await PA.pending(db)
    t0 = dt.datetime.combine(a, dt.time.min)
    t1 = dt.datetime.combine(b + dt.timedelta(days=1), dt.time.min)
    chlog = (await db.execute(select(PlanChangeLog).where(PlanChangeLog.created_at >= t0,
                                                          PlanChangeLog.created_at < t1)
                              .order_by(PlanChangeLog.id.desc()).limit(100))).scalars().all()
    cur = inp.get("cur") or {}
    weeks = [{k: w.get(k) for k in ("start", "phase", "mode", "mode_label", "hours", "tss", "provisional", "why",
                                     "notes", "quality_gate")}
             for w in inp.get("weeks") or [] if w.get("start") and w["start"] <= B
             and (dt.date.fromisoformat(w["start"]) + dt.timedelta(days=6)).isoformat() >= A]
    body = {
        "range": {"from": A, "to": B, "today": today},
        "stored": {"sessions": stored, "source": _("plan_sessions 資料表（含刪除／取代的標記）"),
                   "push": {"provider": prov.id, "rows_in_range": [
                       {"key": k, **prov.row_view(r)} for k, r in rows.items()
                       if any(s["uid"] == k for s in stored)]},
                   "why": _("配對（match_only）只在記憶體裡套用，沒有寫回資料表")},
        "generator": {"weeks": gen, "source": "overview.week_plan + projection.project_weeks",
                      "why": _("產生器這次會排的課（gen_weeks）")},
        "would_change": _would_change(every, inp, A, B),
        "auto": {"settings": auto, "pending": PA.entry_dict(pend) if pend is not None else None,
                 "log": [PA.entry_dict(r) for r in chlog], "source": "plan_change_log + user_settings plan.auto.*"},
        "gates": {"this_week": {"start": (cur.get("week") or {}).get("start"), "mode": cur.get("mode"),
                                "mode_label": cur.get("mode_label"), "why": cur.get("why"),
                                "quality_gate": cur.get("quality_gate"), "notes": cur.get("notes")},
                  "weeks": weeks, "plan_notes": PSA._plan_notes(inp, A, B),
                  "zone": inp.get("zone"), "tests": inp.get("tests"),
                  "source": "engine/quality_gate.py（Zone 3／Zone 5）、load_guard（guard）"},
        "thresholds": inp.get("thresholds"),
        "phase": inp.get("phase"), "horizon_end": inp.get("horizon_end"),
    }
    ds, _err = await _dataset_or_none()
    return await finish(ctx, db, body, ds)


@router.get("/day")
async def day_view(date: Optional[str] = None,
                   ctx: DA.Ctx = Depends(DA.need("read:plan", "read:activity")), db: AsyncSession = Depends(get_db)):
    try:
        d = parse_day(date, "date")
    except ValueError as e:
        raise await _bad(ctx, db, 400, str(e))
    if d is None:
        raise await _bad(ctx, db, 400, "date=YYYY-MM-DD is required")
    try:
        pv = await plan_view(db, d, d)
    except Exception as e:                  # noqa: BLE001
        return await finish(ctx, db, {"date": d.isoformat(), "error": type(e).__name__,
                                      "why": _("課表的輸入算不出來（資料集讀不到？）")})
    D = d.isoformat()
    pr = _pairing(pv, D, D)
    body = {"date": D, "today": pv["today"], **pr,
            "would_change": _would_change(pv["stored"], pv["inp"], D, D),
            "source": _("課表頁（plan_sessions.calendar）同一套：plan_match.compare + compliance；只算不寫"),
            "why": _("每一組：配對方式（同一天／同一週／手動）、完成度等級和原因；沒配到的活動附原因")}
    ds, _err = await _dataset_or_none()
    return await finish(ctx, db, body, ds)


@router.get("/thresholds")
async def thresholds(date: Optional[str] = None,
                     ctx: DA.Ctx = Depends(DA.need_any("read:plan", "read:activity")),
                     db: AsyncSession = Depends(get_db)):
    try:
        d = parse_day(date, "date")
    except ValueError as e:
        raise await _bad(ctx, db, 400, str(e))
    from backend.engine.planning import Plan
    from backend.settings.repository import SettingsRepository
    plan = await run_in_threadpool(Plan.load)
    repo = SettingsRepository(db)
    prof = await repo.get("athlete.coros_profile")
    hist = await repo.get("athlete.coros_profile_history")
    races = await repo.get("athlete.race_results")
    ds, err = await _dataset_or_none()
    if d is None:
        d = await _today(ds)
    planner = None
    if ds is not None:
        try:
            from backend.api import plan_sessions as PSA
            planner = (await PSA._inputs(None)).get("thresholds")
        except Exception:                   # noqa: BLE001
            planner = None
    body = await run_in_threadpool(DV.thresholds_view, plan, d, ds, prof, hist, planner)
    body["e_pace_source"] = {"race_results": races, "why": _("E 配速從最新一筆確認過的路跑成績算（engine/e_pace.py）")}
    if err:
        body["dataset_error"] = err
    return await finish(ctx, db, body, ds)


@router.get("/sync")
async def sync_state(lines: int = Query(200, ge=1, le=2000), ctx: DA.Ctx = Depends(DA.need("read:sync")),
                     db: AsyncSession = Depends(get_db)):
    from backend.db.models import SyncState
    from backend.db.current import current_athlete_id
    from backend.settings.repository import SettingsRepository
    repo = SettingsRepository(db)
    keys = ("sync.primary_source", "sync.coros.enabled", "sync.trainingpeaks.enabled", "sync.coros.last_result",
            "sync.coros.last_ok", "sync.trainingpeaks.last_result", "sync.trainingpeaks.last_ok",
            "sync.coros.rpe_backfill", "sync.schedule.daily_time", "sync.schedule.last_run",
            "sync.auto_on_open.enabled", "sync.auto_on_open.hours", "charts.data_source")
    st = {k: await repo.get(k) for k in keys}
    row = (await db.execute(select(SyncState.last_sync_at, SyncState.last_sync_cursor, SyncState.coros_last_sync_at,
                                   SyncState.coros_token_expires, SyncState.tp_token_expires)
                            .where(SyncState.athlete_id == current_athlete_id()))).first()
    cursors = None if row is None else {
        "tp_last_sync_at": row[0], "tp_cursor": row[1], "coros_last_sync_at": row[2],
        "coros_login_expires": row[3], "tp_login_expires": row[4]}
    fails = []
    for src in ("coros", "trainingpeaks"):
        r = st.get(f"sync.{src}.last_result") or {}
        n = r.get("errors")
        n = len(n) if isinstance(n, list) else int(n or 0) if isinstance(n, (int, float)) else 0
        if n or r.get("error"):
            # runner.py: `errors` = how many activities failed (a count), `error` = the run-level failure
            fails.append({"source": src, "at": r.get("at"), "status": r.get("status"), "error_count": n,
                          "error": r.get("error")})
    body = {"settings": st, "state": cursors, "failures": fails,
            "log": await run_in_threadpool(DV.log_tail, lines),
            "source": _("user_settings sync.*（每次同步的結果）＋ sync_state 的時間和 cursor（不含任何 token）")}
    return await finish(ctx, db, body)


@router.get("/export/config")
async def export_config(ctx: DA.Ctx = Depends(DA.need("export:config")), db: AsyncSession = Depends(get_db)):
    from backend.db.models import ActivityTag, EventGpx, InjuryEvent, UserSetting, WorkoutFile
    from backend.engine.planning import Plan
    from backend.settings.repository import CALIB_PREFIX, SettingsRepository
    repo = SettingsRepository(db)
    settings = await repo.all()
    for r in (await db.execute(select(UserSetting).where(UserSetting.user_id == repo.user_id,
                                                         UserSetting.key.like(CALIB_PREFIX + "%")))).scalars():
        settings[r.key] = json.loads(r.value_json)
    plan = await run_in_threadpool(Plan.load)

    def cols(r, skip=()):
        return {c.name: getattr(r, c.name) for c in r.__table__.columns if c.name not in skip}
    injuries = [cols(r) for r in (await db.execute(select(InjuryEvent).order_by(InjuryEvent.onset_date)))
                .scalars()]
    tags = [cols(r, ("id", "file", "workout_id")) for r in (await db.execute(
        select(ActivityTag).order_by(ActivityTag.start_local))).scalars()]
    over = [{"file": Path(str(fp).replace("\\", "/")).name, "date": d, "terrain": tc}
            for fp, d, tc in (await db.execute(select(WorkoutFile.file_path, WorkoutFile.workout_date,
                                                      WorkoutFile.trail_classification)
                                               .where(WorkoutFile.classification_overridden.is_(True)))).all()]
    gpx = set((await db.execute(select(EventGpx.event_id))).scalars().all())
    body = DV.export_config(settings, plan, injuries, tags, over, gpx)
    return await finish(ctx, db, body)


# ---------------------------------------------------------------------------
# settings page (the web session; owner only — not mounted in the demo)
# ---------------------------------------------------------------------------

async def _state(db: AsyncSession) -> dict:
    return {"enabled": await DA.enabled(db), "pin_configured": DA.pin_configured(), "pin_env": DA.PIN_ENV,
            "pin_min_len": DA.PIN_MIN_LEN, "pin_locked_s": DA.pin_locked_for(), "pin": DA.pin_stats(),
            "scopes": list(DA.SCOPES), "default_scopes": list(DA.DEFAULT_SCOPES), "days": list(DA.DAYS),
            "default_days": DA.DEFAULT_DAYS, "tokens": await DA.list_tokens(db),
            "audit": await DA.audit_rows(db), "failures": await DA.failure_rows(db), "dropped": DA.dropped(),
            "rate_per_min": 60}


@admin_router.get("")
async def admin_state(db: AsyncSession = Depends(get_db)):
    return await _state(db)


@admin_router.put("", dependencies=[Depends(same_origin)])
async def admin_set(body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.settings.repository import SettingsRepository
    on = body.get("enabled")
    if not isinstance(on, bool):
        raise HTTPException(400, "enabled must be true/false")
    if on and not DA.pin_configured():
        raise HTTPException(400, {"code": "NO_PIN", "message": _("伺服器沒有設定 PIN（環境變數 {env}），不能開啟",
                                                                 env=DA.PIN_ENV)})
    await SettingsRepository(db).set(DA.ENABLED_KEY, on)
    await db.commit()
    return await _state(db)


@admin_router.post("/tokens", dependencies=[Depends(same_origin)])
async def admin_create(request: Request, body: dict = Body(...), db: AsyncSession = Depends(get_db)):
    from backend.security import ratelimit as RL
    try:
        DA.check_pin(body.get("pin"), RL.client_ip(request.scope))
    except DA.PinError as e:
        if e.code == "no_pin":
            raise HTTPException(400, {"code": "NO_PIN", "message": _("伺服器沒有設定 PIN，不能產生 token")})
        if e.code == "locked":
            raise HTTPException(429, {"code": "PIN_LOCKED", "retry_s": e.retry_s,
                                      "message": _("PIN 錯太多次，{m} 分鐘後再試", m=max(1, (e.retry_s + 59) // 60))},
                                headers={"Retry-After": str(e.retry_s)})
        raise HTTPException(403, {"code": "PIN_WRONG", "message": _("PIN 不對")})
    try:
        token, view = await DA.create_token(db, body.get("name"), body.get("scopes"), body.get("days"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"token": token, **view}


@admin_router.delete("/tokens/{token_id}", dependencies=[Depends(same_origin)])
async def admin_revoke(token_id: int = Path_(ge=1, le=2 ** 62), db: AsyncSession = Depends(get_db)):
    n = await DA.revoke(db, token_id)
    return {"revoked": n, **(await _state(db))}


@admin_router.post("/tokens/revoke-all", dependencies=[Depends(same_origin)])
async def admin_revoke_all(db: AsyncSession = Depends(get_db)):
    n = await DA.revoke(db, None)
    return {"revoked": n, **(await _state(db))}
