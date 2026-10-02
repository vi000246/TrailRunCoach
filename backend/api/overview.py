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
    # the 課表偏好 (CP-test protocol, 間歇門檻 for status.i_gate) and the stored
    # test sessions (done_by) change the testing / gate indicators too
    prefs = PP.load()
    tests = tuple((s["uid"], s["state"], (s.get("done_by") or {}).get("index"), s.get("protocol"))
                  for s in test_sessions())
    key = (id(ds), today, _plan_stamp(), prefs.stamp(), tests)
    with _lock:
        hit = _status_cache.get(key)
    if hit is None:
        hit = Status(ds, today=today, prefs=prefs).compute()
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
    from backend.engine import b2b as B2B
    from backend.engine import blackouts as BL
    from backend.engine import plan_prefs as PP
    # same 課表偏好, 不排課日期 and accepted B2B weekends as the 課表 page
    return O.week_plan(ds, _status(ds, today), today, prefs=PP.load(), blackouts=BL.load(),
                       b2b_accepted=B2B.load_accepted())


@router.get("/z5")
def z5_card():
    """The 「5 區（最大攝氧量間歇）狀態」 card: the status gate's Zone 5 state (the
    object week_plan decides with) as a checklist (quality_gate.z5_card)."""
    from backend.engine import quality_gate as QG
    ds = _dataset()
    today = O.day_to_date(ds.today)
    st = _status(ds, today)
    gate = next((i.extra for i in st.indicators if i.id == "gate"), None) or {}
    return {"today": today.isoformat(), **QG.z5_card(gate, today), "history_href": _z5_chart_href()}


@router.get("/b2b")
def b2b_card():
    """The 「連續兩天長天（B2B）」 card (engine/b2b.card): the target event, this
    week's B2B state, the planned B2B weekends (the stored-plan inputs: this
    week + the projection), each done B2B with its day-2-vs-day-1 reading and
    the trend across the block."""
    from backend.engine import b2b as B2B
    ds = _dataset()
    today = O.day_to_date(ds.today)
    st = _status(ds, today)
    cur, weeks = None, []
    try:
        from backend.api.plan_sessions import _compute_inputs
        inp = _compute_inputs()
        cur, weeks = inp.get("cur"), inp.get("weeks") or []
    except Exception:                          # noqa: BLE001 — the card still shows what was done
        pass
    aet_now = ((cur or {}).get("thresholds") or {}).get("aet")

    def aet_of(day: dt.date):
        try:
            from backend.engine.racepower.athlete import thresholds_as_of
            return thresholds_as_of(ds, day).get("aet") or aet_now
        except Exception:                      # noqa: BLE001
            return aet_now
    return B2B.card(ds, today, st.plan.events, st.phase, cur, weeks, aet_of)


@router.get("/loaded-carry")
def loaded_carry_card():
    """The 「負重訓練」 card (engine/loaded_carry.card): the trip and its pack,
    this week's stage and loaded sessions, the planned ones (this week + the
    projection), every loaded session done with ΔHR@VAM / 負重效率, the
    per-stage trend, and the recent activities with their recorded pack."""
    from backend.engine import loaded_carry as LC
    ds = _dataset()
    today = O.day_to_date(ds.today)
    st = _status(ds, today)
    cur, weeks = None, []
    try:
        from backend.api.plan_sessions import _compute_inputs
        inp = _compute_inputs()
        cur, weeks = inp.get("cur"), inp.get("weeks") or []
    except Exception:                          # noqa: BLE001 — the card still shows what was done
        pass
    aet_now = ((cur or {}).get("thresholds") or {}).get("aet")

    def aet_of(day: dt.date):
        try:
            from backend.engine.racepower.athlete import thresholds_as_of
            return thresholds_as_of(ds, day).get("aet") or aet_now
        except Exception:                      # noqa: BLE001
            return aet_now
    weight = st.plan.weight_on(today) if st.plan is not None else None
    return LC.card(ds, today, st.plan.events, st.phase, cur, weeks, aet_of, weight)


def _z5_chart_href() -> str:
    """The viewer deep link of the first z5gate panel in the custom views (the 基礎期 chart)."""
    from urllib.parse import urlencode
    from backend.engine.wko5expr.customviews import load_custom_views
    try:
        for name, v in load_custom_views().items():
            for di, d in enumerate(v.get("dashboards") or []):
                for ci, c in enumerate(d.get("charts") or []):
                    if c.get("kind") == "z5gate":
                        return "/api/v1/wko5/viewer?" + urlencode({"view": name, "dash": di, "chart": ci})
    except Exception:                          # noqa: BLE001 — a broken view file: plain viewer link
        pass
    return "/api/v1/wko5/viewer"


@router.get("/page", include_in_schema=False)
def page():
    return FileResponse(STATIC / "overview.html")
