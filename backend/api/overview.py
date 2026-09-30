"""
Overview API — the one-glance page: training status, what was done by week /
month / year (all sports together), the combined PMC and this week's plan.
"""
from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.engine import overview as O
from backend.engine.planning import PLAN_PATH
from backend.engine.status import Status

STATIC = Path(__file__).resolve().parents[1] / "static"

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])

_lock = threading.Lock()
_status_cache: dict = {}


def _dataset():
    # same dataset (and engine config / parity mode) as the chart pages
    from backend.api.wko5views import _dataset as ds
    return ds()


def _plan_stamp() -> float:
    try:
        return PLAN_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _status(ds, today: dt.date) -> Status:
    """Status is the slow part (it reads samples); memoise per dataset / day / plan edit."""
    from backend.engine import plan_prefs as PP
    from backend.engine.plan_store import test_sessions
    # the CP-test protocol (課表偏好) and the stored test sessions (done_by)
    # change the testing indicator too
    tests = tuple((s["uid"], s["state"], (s.get("done_by") or {}).get("index"), s.get("protocol"))
                  for s in test_sessions())
    key = (id(ds), today, _plan_stamp(), PP.load().cp_test_protocol, tests)
    with _lock:
        hit = _status_cache.get(key)
    if hit is None:
        hit = Status(ds, today=today).compute()
        with _lock:
            _status_cache.clear()
            _status_cache[key] = hit
    return hit


def _day(s: Optional[str], default: dt.date) -> dt.date:
    if not s:
        return default
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        raise HTTPException(400, f"bad date {s!r}")


@router.get("/status")
def status():
    ds = _dataset()
    today = O.day_to_date(ds.today)
    return _status(ds, today).to_dict()


@router.get("/summary")
def summary(unit: str = "week", anchor: Optional[str] = None, n: int = 12):
    if unit not in O.UNITS:
        raise HTTPException(400, f"unit must be one of {O.UNITS}")
    ds = _dataset()
    today = O.day_to_date(ds.today)
    n = max(1, min(n, {"week": 104, "month": 60, "year": 12}[unit]))
    return O.summary(ds, unit, _day(anchor, today), n)


@router.get("/pmc")
def pmc(begin: Optional[str] = None, end: Optional[str] = None):
    ds = _dataset()
    today = O.day_to_date(ds.today)
    e = _day(end, today)
    b = _day(begin, e - dt.timedelta(days=180))
    if b > e:
        raise HTTPException(400, "begin after end")
    return O.pmc(ds, b, e)


@router.get("/weekplan")
def weekplan():
    ds = _dataset()
    today = O.day_to_date(ds.today)
    from backend.engine import blackouts as BL
    from backend.engine import plan_prefs as PP
    # same 課表偏好 and 不排課日期 as the 課表 page
    return O.week_plan(ds, _status(ds, today), today, prefs=PP.load(), blackouts=BL.load())


@router.get("/page", include_in_schema=False)
def page():
    return FileResponse(STATIC / "overview.html")
