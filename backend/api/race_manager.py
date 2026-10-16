"""
賽事管理 (SP-675; engine/race_manager.py, static/races.html).

    GET /api/v1/app/races/page                 the page (?race=e:<event id> | a:<workout idx>)
    GET /api/v1/app/races                      {today, rows, kinds, priorities} every race, newest first
    GET /api/v1/app/races/detail?event=|idx=   one race: the day's data, estimate vs actual, calc_url
    GET /api/v1/app/races/readiness/{eid}      the 賽前完備程度 as of the day before a past A race

Adding / editing / deleting a race is the season plan's own API (PUT /api/v1/plan/events, DELETE
/api/v1/plan/events/{id}) — one list of events for this page and 賽事周期. Two races are overlaid by
GET /api/v1/app/activity-compare (SP-625); Cairn: POST /api/v1/plan/events/{id}/gpx/cairn (a share
link, SP-537) and /api/v1/account/links/cairn/events/{id}[/push] (SP-655).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from backend.engine.localtime import today_local
from backend.i18n.pages import render_page

router = APIRouter(prefix="/api/v1/app/races", tags=["races"])
log = logging.getLogger(__name__)


def _dataset():
    from backend.api.wko5views import _dataset as ds
    return ds()


def _tags() -> list:
    from backend.engine import activity_tags as AT
    return AT.load()


def _calcs(ids) -> dict:
    """{event id: the race calculator's saved row} (engine/race_calc_store.py); a broken row is left out."""
    from backend.engine import race_calc_store as RC
    out = {}
    for eid in ids:
        try:
            row = RC.get(eid)
        except Exception:                   # noqa: BLE001 — one bad row never hides the list
            row = None
        if row:
            out[eid] = row
    return out


def _event_ids(ds) -> list[str]:
    return [e.id for e in getattr(getattr(ds, "plan", None), "events", None) or []]


@router.get("/page", include_in_schema=False)
def page():
    return render_page("races")


@router.get("")
def races():
    from backend.engine import planning as P
    from backend.engine import race_manager as RM
    from backend.engine.race_activities import NOT_RACE_KINDS
    ds = _dataset()
    today = today_local()
    rows = RM.rows(ds, today, tags=_tags(), calcs=_calcs(_event_ids(ds)))
    return {"today": today.isoformat(), "rows": rows,
            "kinds": {k: v for k, v in P.KINDS.items() if k not in NOT_RACE_KINDS},
            "priorities": list(P.PRIORITIES)}


@router.get("/detail")
async def detail(event: Optional[str] = None, idx: Optional[int] = None):
    from backend.engine import race_manager as RM
    if (event is None) == (idx is None):
        raise HTTPException(400, "event or idx")

    def work():
        ds = _dataset()
        return RM.detail(ds, today_local(), tags=_tags(), calcs=_calcs(_event_ids(ds)), eid=event, idx=idx)
    try:
        return await run_in_threadpool(work)
    except KeyError:
        raise HTTPException(404, "no such race")


@router.get("/readiness/{eid}")
async def readiness(eid: str):
    from backend.engine import race_manager as RM

    def work():
        ds = _dataset()
        return RM.readiness(ds.plan, ds, eid, today_local())
    try:
        return await run_in_threadpool(work)
    except KeyError:
        raise HTTPException(404, "no such race")
    except Exception:                       # noqa: BLE001 — the race page shows without it
        log.exception("readiness as of a past race failed")
        return {"available": False, "why": "", "stages": [], "text": ""}
