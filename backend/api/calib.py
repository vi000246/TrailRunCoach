"""
Per-athlete calibration (engine/calibrate.py): the 設定 page's 進階設定 →
「自動估算的參數」 list, its 手動指定 / 改回自動, and a fit on demand.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.engine.localtime import today_local
from backend.db.current import current_athlete_id
from backend.db.database import get_db
from backend.engine import calibrate as CAL
from backend.i18n import _
from backend.settings.repository import SettingsRepository

router = APIRouter(prefix="/api/v1/calib", tags=["calibration"])


def _item(name: str) -> CAL.Item:
    item = CAL._registry().get(name)
    if item is None:
        raise HTTPException(404, "UNKNOWN_CALIBRATION")
    return item


def _replan(name: str) -> None:
    """A value the plan reads (the Zone 3 unlock rule, SP-295): the plan / status caches key on
    it (advanced_params.z3_rule_stamp); a background plan_auto run re-plans the stored sessions."""
    from backend.engine import advanced_params as AP
    if name in AP.Z3_RULE.values():
        from backend.engine import plan_auto as PA
        PA.after_settings()
    if name in AP.PACK_ITEMS:
        # SP-740: the 預設背負 feeds the calculator and its walking model — their caches go
        try:
            from backend.api import racepower as RP
            with RP._lock:
                RP._cache.pop("inputs", None)
                RP._cache.pop("grade", None)
        except Exception:                   # noqa: BLE001
            pass


async def _check_pair(repo: SettingsRepository, name: str, value) -> None:
    """Cross-item checks (SP-295: 重新上鎖天數 ≥ 最長幾天不跑) against the other item's value in effect:
    its stored manual value, else its default. `value` None = 改回自動 (the item's default)."""
    from backend.engine import advanced_params as AP
    if name not in AP.Z3_RULE.values():
        return
    cur = await _z3_values(repo)
    msg = AP.z3_pair_error(name, value, cur.__getitem__)
    if msg:
        raise HTTPException(400, msg)


async def _z3_values(repo: SettingsRepository) -> dict:
    """{item name: value in effect} of the Zone 3 unlock items: the stored manual value, else the default."""
    from backend.engine import advanced_params as AP
    reg = CAL._registry()
    cur = {}
    for n in AP.Z3_RULE.values():
        stored = await repo.get(CAL.key(n)) or {}
        cur[n] = stored.get("value") if stored.get("value") is not None else reg[n].default
    return cur


@router.get("")
async def list_calibration(db: AsyncSession = Depends(get_db)):
    """Every item; a stored Zone 3 pair that breaks 重新上鎖天數 ≥ 最長幾天不跑 (saved before that check, SP-295
    review) carries `warn` on both rows — shown next to them, the values are not changed."""
    from backend.engine import advanced_params as AP
    repo = SettingsRepository(db, current_athlete_id())
    items = [CAL.describe(n, await repo.get(CAL.key(n))) for n in sorted(CAL._registry())]
    items = [AP.pack_live_describe(it) for it in items]           # SP-740: 預設背負 in effect
    cur = await _z3_values(repo)
    gap, relock = AP.Z3_RULE["gap"], AP.Z3_RULE["relock"]
    warn = AP.z3_pair_error(relock, cur[relock], cur.__getitem__)
    if warn:
        for it in items:
            if it.get("name") in (gap, relock):
                it["warn"] = warn
    return {"items": items}


class Manual(BaseModel):
    value: float


@router.put("/{name}")
async def set_manual(name: str, body: Manual, db: AsyncSession = Depends(get_db)):
    """手動指定: stored with source = user, never overwritten by a fit."""
    item = _item(name)
    fmt = "{:.0f}" if item.integer else "{:g}"
    if item.bounds and not item.bounds[0] <= body.value <= item.bounds[1]:
        raise HTTPException(400, _("{label} 要在 {lo}–{hi} {unit} 之間", label=_(item.label), unit=_(item.unit),
                                   lo=fmt.format(item.bounds[0]), hi=fmt.format(item.bounds[1])))
    if item.integer and body.value != int(body.value):
        raise HTTPException(400, _("{label} 要是整數", label=_(item.label)))
    repo = SettingsRepository(db, current_athlete_id())
    await _check_pair(repo, name, body.value)
    prev = await repo.get(CAL.key(name)) or {}
    await repo.set(CAL.key(name), {"value": body.value, "se": None, "n": int(prev.get("n") or 0),
                                   "fitted_at": today_local().isoformat(), "source": "user"})
    await db.commit()
    CAL.forget_reads()
    _replan(name)
    return CAL.describe(name, await repo.get(CAL.key(name)))


@router.delete("/{name}")
async def clear_manual(name: str, db: AsyncSession = Depends(get_db)):
    """改回自動: the manual value goes; the next fit (or the default) applies."""
    _item(name)
    repo = SettingsRepository(db, current_athlete_id())
    await _check_pair(repo, name, None)
    await repo.set(CAL.key(name), None)
    await db.commit()
    CAL.forget_reads()
    _replan(name)
    return CAL.describe(name, None)


def _job_key() -> tuple:
    from backend import tenancy
    return ("calib", tenancy.current().id)


def _fit_job(athlete_id: int):
    """The queued fit (SP-714): its own event loop and DB engine in the job's thread (as
    wko5views._calibrate_never_fitted), the Dataset read inside the job (it holds a place)."""
    import asyncio

    def body(job):
        from backend.api.wko5views import _dataset
        ds = _dataset(False)

        async def fit():
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
            from backend.db.database import DATABASE_URL
            eng = create_async_engine(DATABASE_URL)
            try:
                async with async_sessionmaker(eng, expire_on_commit=False)() as db:
                    return await CAL.calibrate(db, athlete_id, ds=ds)
            finally:
                await eng.dispose()
        return asyncio.run(fit())
    return body


def _job_view(job) -> dict:
    out = {"state": job.state, "position": 0}
    if job.state == "queued":
        from backend import jobs
        out["position"] = jobs.position(job)
    if job.state == "done" and isinstance(job.result, dict):
        out["written"] = job.result.get("written") or []
    if job.state == "failed":
        out["error"] = job.error
    return out


@router.post("/run")
async def run_now():
    """Fit every item now (otherwise it runs after each sync that imports an activity): queued in
    the server's job queue (SP-714: `full`, someone waits — `user`; one per tenant), never computed
    in the request. Answers at once {status: queued, job}; GET /run tells how it went. A cool-down
    per person (cooldown `calib`, 429 COOLDOWN); a run already queued / running is the same job."""
    from backend import cooldown, jobs
    key = _job_key()
    cur = jobs.find(key)
    if cur is not None and cur.state in ("queued", "running"):
        return {"status": "queued", "job": _job_view(cur)}
    cooldown.take("calib")
    job = jobs.submit(_fit_job(current_athlete_id()), cls="full", priority="user", label="calibration (manual)",
                      key=key)
    return {"status": "queued", "job": _job_view(job)}


@router.get("/run")
async def run_status(db: AsyncSession = Depends(get_db)):
    """The last manual fit of this tenant: {job: {state queued | running | done | failed, position,
    written} | null, items} — the page polls it after 「現在重新估算」."""
    from backend import jobs
    from backend.engine import advanced_params as AP
    job = jobs.find(_job_key())
    repo = SettingsRepository(db, current_athlete_id())
    items = [CAL.describe(n, await repo.get(CAL.key(n))) for n in sorted(CAL._registry())]
    items = [AP.pack_live_describe(it) for it in items]           # SP-740: 預設背負 in effect
    return {"job": _job_view(job) if job is not None else None, "items": items}
