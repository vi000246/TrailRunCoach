"""
Body weight from Apple Health (SP-472; rules: engine/weight_sync.py, tokens: backend/weight_auth.py).

  POST   /api/v1/weight/measurements        the iOS Shortcut / Health Auto Export (token only, scope
                                            weight:write): every value stored with its source; nothing
                                            recomputed (no Dataset, no cache touched)
  GET    /api/v1/weight/status              設定頁: tokens, the last measurements, the nightly apply,
                                            the COROS hint
  POST   /api/v1/weight/tokens              make one {name?} → {token (shown once), row}
  DELETE /api/v1/weight/tokens/{id}         revoke one;  POST /tokens/revoke-all: every live one
  GET    /api/v1/weight/coros-hint          總覽 / 設定頁: the COROS account weight differs ≥ 1 kg
  POST   /api/v1/weight/coros/accept        {kg}: the user takes it (a dated entry, source coros)
  POST   /api/v1/weight/coros/dismiss       {kg}: 「不用」 for that value

The nightly apply (`tick`, from sync/scheduler.loop for every tenant): once a local day in
APPLY_AT–APPLY_END (+ a per-tenant slot), when no sync or automatic plan run of the tenant is going,
the days with new measurements go through weight_sync.choose; only when an entry was added or
replaced is plan.json written and the recompute started (api/plan._notify: thresholds, the
課表's power targets). No new measurement: nothing is read but that query.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import time
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import tenancy
from backend import weight_auth as WA
from backend.db.database import get_db
from backend.db.models import AthleteSettings, WeightMeasurement
from backend.engine import weight_sync as WS
from backend.i18n import _
from backend.security import ratelimit as RL
from backend.settings.repository import SettingsRepository

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/weight", tags=["weight"])

INGEST_PATH = "/api/v1/weight/measurements"
MAX_BODY = 1024 * 1024
STATE_KEY = "weight.sync.state"
DISMISS_KEY = "weight.coros.dismissed"
APPLY_AT = dt.time(3, 0)
APPLY_END = dt.time(6, 0)
SLOT_MIN = 60                         # a user's apply starts 0–59 min after APPLY_AT (the owner's at once)
SHOW = 10
CLOCK = lambda: dt.datetime.now(dt.timezone.utc)      # noqa: E731 — tests replace it


def _err(status: int, code: str, headers: Optional[dict] = None, **extra) -> HTTPException:
    return HTTPException(status, {"code": code, **extra}, headers=headers)


def _utc(t: dt.datetime) -> dt.datetime:
    return t.astimezone(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def slot_minutes(tenant_id: str) -> int:
    if tenant_id == "owner":
        return 0
    return int(hashlib.sha256(("weight:" + tenant_id).encode("utf-8")).hexdigest(), 16) % SLOT_MIN


def next_apply(local: dt.datetime, tenant_id: str, done_today: bool) -> dt.datetime:
    start = dt.datetime.combine(local.date(), APPLY_AT, tzinfo=local.tzinfo) + dt.timedelta(minutes=slot_minutes(tenant_id))
    end = dt.datetime.combine(local.date(), APPLY_END, tzinfo=local.tzinfo)
    if not done_today and local < end:
        return max(start, local)
    return start + dt.timedelta(days=1)


# ---------------------------------------------------------------------------- receiving

async def _read_body(request: Request) -> bytes:
    try:
        n = int(request.headers.get("content-length") or 0)
    except ValueError:
        n = 0
    if n > MAX_BODY:
        raise _err(413, "TOO_LARGE")
    chunks, size = [], 0
    async for c in request.stream():
        size += len(c)
        if size > MAX_BODY:
            raise _err(413, "TOO_LARGE")
        chunks.append(c)
    return b"".join(chunks)


async def store(db: AsyncSession, parsed: WS.Parsed, tz: dt.tzinfo, now: Optional[dt.datetime] = None) -> dict:
    """Every value as a row (source, time unique): a new one is `new`; the same sample sent again
    with another value is updated and `new` again (the nightly apply looks at it); an identical
    copy changes nothing. Returns the counts. Never reads plan.json, never clears a cache."""
    now = now or dt.datetime.utcnow()
    added = updated = same = 0
    seen: dict = {}
    for it in parsed.items:
        at = _utc(it.measured_at)
        seen[at] = it                                   # a repeat inside the request: the last wins
    if seen:
        have = {r.measured_at: r for r in (await db.execute(select(WeightMeasurement).where(
            WeightMeasurement.source == parsed.source,
            WeightMeasurement.measured_at.in_(list(seen))))).scalars().all()}
    else:
        have = {}
    for at, it in seen.items():
        day = it.measured_at.astimezone(tz).date()
        r = have.get(at)
        if r is None:
            db.add(WeightMeasurement(measured_at=at, day=day, kg=it.kg, source=parsed.source, device=it.device,
                                     received_at=now, status="new"))
            added += 1
        elif abs(r.kg - it.kg) >= 0.005 or (it.device and it.device != r.device) or r.day != day:
            r.kg, r.day, r.received_at, r.status, r.note, r.applied_at = it.kg, day, now, "new", None, None
            r.device = it.device or r.device
            updated += 1
        else:
            same += 1
    await db.flush()
    return {"added": added, "updated": updated, "unchanged": same}


@router.post("/measurements")
async def receive(request: Request, db: AsyncSession = Depends(get_db)):
    """The weight API (scope weight:write): see the module docstring and weight_auth."""
    t = tenancy.current()
    if tenancy.demo_mode() or t.is_demo:
        raise HTTPException(404, "Not Found")
    ip = RL.client_ip(request.scope)
    tok = WA.token_from_headers(request.headers)
    row = await WA.find(db, tok) if tok else None
    if row is None:
        st = WA.failed(ip)
        if st == 429:
            raise _err(429, "BLOCKED", {"Retry-After": str(WA.FAIL_BLOCK_S)})
        raise _err(401, "TOKEN_MISSING" if tok is None else "TOKEN_INVALID", {"WWW-Authenticate": "Bearer"})
    if not WA.within_limits(t.id, row.id):
        raise _err(429, "RATE_LIMITED", {"Retry-After": "600"})
    raw = await _read_body(request)
    try:
        body = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        raise _err(400, "BAD_JSON")
    repo = SettingsRepository(db)
    tz = await repo.timezone()
    try:
        parsed = WS.parse(body, tz, CLOCK())
    except WS.PayloadError as e:
        raise _err(413 if e.code == "too_many" else 400, e.code.upper(), max_items=WS.MAX_ITEMS)
    counts = await store(db, parsed, tz, _utc(CLOCK()))
    row.last_used_at = _utc(CLOCK())
    await db.commit()
    local = CLOCK().astimezone(tz)
    state = await repo.get(STATE_KEY) or {}
    nxt = next_apply(local, t.id, state.get("last_run") == local.date().isoformat())
    out = {"source": parsed.source, "received": len(parsed.items) + len(parsed.rejected), **counts,
           "rejected": parsed.rejected, "ignored_metrics": parsed.ignored,
           "applies_at": nxt.isoformat(timespec="minutes")}
    if parsed.items:
        last = max(parsed.items, key=lambda i: i.measured_at)
        out["message"] = _("已收到 {n} 筆體重（最新 {kg:.1f} kg），{at} 才會套用", n=len(parsed.items), kg=last.kg,
                           at=nxt.strftime("%m/%d %H:%M"))
        return out
    if parsed.rejected:
        out["message"] = _("沒有收到可用的體重：{why}", why=parsed.rejected[0]["reason"])
        raise HTTPException(422, {"code": "NOTHING_VALID", **out})
    out["message"] = _("這次沒有體重資料")
    return out


# ---------------------------------------------------------------------------- the nightly apply

def _notify() -> None:
    from backend.api.plan import _notify as plan_notify
    plan_notify(True)          # weight feeds W/kg and RE everywhere: the same as a 設定頁 save


async def apply(db: AsyncSession, today: dt.date, now: Optional[dt.datetime] = None) -> dict:
    """The days with `new` measurements (up to `today`) through weight_sync.choose; marks the rows.
    Writes plan.json only when an entry was added or replaced ({"changed": True}); the caller
    starts the recompute after its commit."""
    from backend.engine import planning as P
    now = now or dt.datetime.utcnow()
    new = (await db.execute(select(WeightMeasurement.day).where(
        WeightMeasurement.status == "new", WeightMeasurement.day <= today).distinct())).scalars().all()
    if not new:
        return {"status": "nothing", "changed": False, "decisions": []}
    rows = (await db.execute(select(WeightMeasurement).where(WeightMeasurement.day.in_(new)))).scalars().all()
    last = WS.last_per_day(rows)
    plan = P.Plan.load()
    out, decisions = WS.choose(plan.weights, {d: r.kg for d, r in last.items()},
                               lambda d, kg: P.Weight(d, kg, WS.AUTO))
    by_day = {x["day"]: x for x in decisions}
    for r in rows:
        if last.get(r.day) is not r:
            r.status, r.note = "superseded", None
            continue
        d = by_day[r.day.isoformat()]
        if d["action"] in ("added", "replaced"):
            r.status, r.note, r.applied_at = "applied", d["reason"], now
        else:
            r.status, r.note = "skipped", d["reason"]
    changed = any(x["action"] in ("added", "replaced") for x in decisions)
    if changed:
        plan.weights = out
        plan.save()
    await db.flush()
    return {"status": "applied" if changed else "unchanged", "changed": changed, "decisions": decisions}


def _busy() -> bool:
    """A sync (any source) or an automatic plan run of the current tenant is going: wait a minute,
    so the recompute never starts while a sync is running."""
    from backend.engine import plan_auto as PA
    from backend.sync import runner
    return any(runner.is_busy(s) for s in runner.SOURCES) or PA.busy()


async def tick(session_factory: Optional[Callable] = None, now: Optional[dt.datetime] = None) -> Optional[dict]:
    """The scheduler's check of the current tenant (each minute): the nightly apply when due.
    Returns its result, or None when not due / already done / busy."""
    t = tenancy.current()
    if tenancy.demo_mode() or t.is_demo:
        return None
    if session_factory is None:
        from backend.db.database import AsyncSessionLocal as session_factory
    now = now or dt.datetime.now(dt.timezone.utc)
    async with session_factory() as db:
        repo = SettingsRepository(db)
        local = now.astimezone(await repo.timezone())
        state = dict(await repo.get(STATE_KEY) or {})
        today = local.date().isoformat()
        if state.get("last_run") == today:
            return None
        start = dt.datetime.combine(local.date(), APPLY_AT, tzinfo=local.tzinfo) + dt.timedelta(minutes=slot_minutes(t.id))
        if not (start <= local < dt.datetime.combine(local.date(), APPLY_END, tzinfo=local.tzinfo)):
            return None
        if _busy():
            return None
        t0 = time.monotonic()
        res = await apply(db, local.date(), now=_utc(now))
        state = {"last_run": today, "at": now.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
                 "status": res["status"],
                 "added": sum(1 for x in res["decisions"] if x["action"] in ("added", "replaced")),
                 "skipped": sum(1 for x in res["decisions"] if x["action"] == "skipped")}
        await repo.set(STATE_KEY, state)
        await db.commit()
    if res["changed"]:
        _notify()
        log.info("weight apply: %d entr(ies) written in %.0f ms; recompute started", state["added"],
                 (time.monotonic() - t0) * 1000)
    return res


# ---------------------------------------------------------------------------- 設定頁 / 總覽

async def _coros(db: AsyncSession) -> Optional[tuple]:
    r = (await db.execute(select(AthleteSettings.weight_kg, AthleteSettings.effective_date).where(
        AthleteSettings.weight_kg.isnot(None)).order_by(AthleteSettings.effective_date.desc()).limit(1))).first()
    return (float(r[0]), r[1].isoformat()) if r and r[0] else None


async def _hint(db: AsyncSession) -> Optional[dict]:
    from backend.engine import planning as P
    dismissed = await SettingsRepository(db).get(DISMISS_KEY)
    return WS.coros_hint(P.Plan.load().weights, await _coros(db), dismissed)


def _mview(r: WeightMeasurement, tz) -> dict:
    return {"id": r.id, "kg": r.kg, "day": r.day.isoformat(),
            "measured_at": r.measured_at.replace(tzinfo=dt.timezone.utc).astimezone(tz).isoformat(timespec="minutes"),
            "source": r.source, "device": r.device, "status": r.status, "note": r.note}


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db)):
    from backend.engine import planning as P
    repo = SettingsRepository(db)
    tz = await repo.timezone()
    rows = (await db.execute(select(WeightMeasurement).order_by(WeightMeasurement.measured_at.desc())
                             .limit(SHOW))).scalars().all()
    state = await repo.get(STATE_KEY) or {}
    local = CLOCK().astimezone(tz)
    plan = P.Plan.load()
    return {"endpoint": str(request.base_url).rstrip("/") + INGEST_PATH, "path": INGEST_PATH,
            "tokens": await WA.list_tokens(db), "max_active": WA.MAX_ACTIVE,
            "measurements": [_mview(r, tz) for r in rows],
            "pending": (await db.execute(select(func.count(WeightMeasurement.id)).where(
                WeightMeasurement.status == "new"))).scalar_one(),
            "state": state,
            "next_apply": next_apply(local, tenancy.current().id,
                                     state.get("last_run") == local.date().isoformat()).isoformat(timespec="minutes"),
            "weights": [{"date": w.date, "kg": w.kg, "source": w.source or "manual"}
                        for w in sorted(plan.weights, key=lambda w: w.date)][-SHOW:],
            "rules": {"min_diff_kg": WS.MIN_DIFF_KG, "max_gap_days": WS.MAX_GAP_DAYS,
                      "coros_diff_kg": WS.COROS_DIFF_KG, "apply_at": APPLY_AT.strftime("%H:%M")},
            "coros_hint": await _hint(db)}


class TokenIn(BaseModel):
    name: str = ""


@router.post("/tokens")
async def make_token(body: TokenIn, db: AsyncSession = Depends(get_db)):
    if tenancy.current().is_demo:
        raise HTTPException(404, "Not Found")
    try:
        token, row = await WA.create(db, body.name)
    except ValueError as e:
        raise _err(400, "TOO_MANY", message=str(e))
    return {"token": token, "row": row}


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: int, db: AsyncSession = Depends(get_db)):
    return {"revoked": await WA.revoke(db, token_id)}


@router.post("/tokens/revoke-all")
async def revoke_all(db: AsyncSession = Depends(get_db)):
    return {"revoked": await WA.revoke(db)}


@router.get("/coros-hint")
async def coros_hint(db: AsyncSession = Depends(get_db)):
    return {"hint": await _hint(db)}


class KgIn(BaseModel):
    kg: float


@router.post("/coros/accept")
async def coros_accept(body: KgIn, db: AsyncSession = Depends(get_db)):
    """The user takes the COROS account weight: a dated entry today (source coros — the user's own
    choice, so it counts as manual for the nightly apply), then the usual recompute of a weight
    change, as a 設定頁 save does."""
    from backend.engine import planning as P
    from backend.engine.localtime import today_local
    hint = await _hint(db)
    if hint is None or abs(hint["kg"] - body.kg) >= 0.05:
        raise _err(409, "HINT_CHANGED")
    plan = P.Plan.load()
    day = today_local().isoformat()
    plan.weights = [w for w in plan.weights if w.date[:10] != day] + [P.Weight(day, hint["kg"], "coros")]
    plan.save()
    _notify()
    return {"ok": True, "date": day, "kg": hint["kg"]}


@router.post("/coros/dismiss")
async def coros_dismiss(body: KgIn, db: AsyncSession = Depends(get_db)):
    await SettingsRepository(db).set(DISMISS_KEY, round(float(body.kg), 1))
    await db.commit()
    return {"ok": True}
