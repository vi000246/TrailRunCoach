"""
Overview API — the one-glance page: training status, what was done by week /
month / year (all sports together), the combined PMC and this week's plan.
"""
from __future__ import annotations

from backend import tenancy as _tenancy
import datetime as dt
import threading
from pathlib import Path
from typing import Optional

from backend.i18n.pages import render_page
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.engine import overview as O
from backend.engine.planning import plan_path
from backend.singleflight import SingleFlight
from backend.engine.status import Status

STATIC = Path(__file__).resolve().parents[1] / "static"

router = APIRouter(prefix="/api/v1/overview", tags=["overview"])

_lock = threading.Lock()
_status_cache: dict = {}          # (tenant id, ...) -> Status, insertion order = LRU
_STATUS_MAX = 64
_STATUS_FLIGHT = SingleFlight()   # one Status.compute per key at a time (SP-362)


def _dataset():
    # same dataset (and engine config / parity mode) as the chart pages
    from backend.api.wko5views import _dataset as ds
    return ds()


def _plan_stamp() -> float:
    try:
        return plan_path().stat().st_mtime
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
    from backend import tenancy
    from backend.engine import hr_profile as HRP          # 課表心率區間 (the week plan's HR targets)
    from backend.engine import load_guard as LG             # 起始 CTL／ATL (SP-68): the PMC starts from it
    pmc0 = LG.manual_start()
    # the Zone 3 unlock rule of 進階設定 (engine/advanced_params.py, SP-295): a change re-evaluates the gate
    from backend.engine import advanced_params as AP
    # the 跑步經驗問卷 (SP-291): the 資料等級 card words its line by the answers
    from backend.engine import experience as EX
    key = (*_tenancy.ds_key(ds), today, _plan_stamp(), prefs.stamp(), tests, HRP.stamp(),
           None if pmc0 is None else tuple(sorted(pmc0.items())), AP.z3_rule_stamp(), EX.stamp())
    with _lock:
        hit = _status_cache.get(key)
        if hit is not None:
            _status_cache[key] = _status_cache.pop(key)      # most recent last
    if hit is not None:
        return hit

    def compute() -> Status:
        with _lock:                            # a flight that just ended stored it
            done = _status_cache.get(key)
        if done is not None:
            return done
        st = Status(ds, today=today, prefs=prefs).compute()
        with _lock:
            # LRU per tenant (demo sandboxes, later users); the owner alone keeps one entry warm
            mine = [k for k in _status_cache if k[0] == key[0]]
            for k in mine:
                _status_cache.pop(k, None)
            _status_cache[key] = st
            while len(_status_cache) > _STATUS_MAX:
                _status_cache.pop(next(iter(_status_cache)))
        return st
    # single flight (SP-362): concurrent callers of this key wait for one computation
    return _STATUS_FLIGHT.do(key, compute)


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
    from backend.engine.panels.race_refs import calculator_hours
    return O.week_plan(ds, _status(ds, today), today, prefs=PP.load(), blackouts=BL.load(),
                       b2b_accepted=B2B.load_accepted(), race_predict=calculator_hours)


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


@router.get("/feasibility")
def feasibility(event_id: Optional[str] = None):
    """「賽事完備程度」 on the season-plan page (engine/race_feasibility.py, SP-105): the upcoming A / B
    races, or the one `event_id`. Only advice; the detail charts are the 專項期 dashboard (chart_href)."""
    from backend.engine import race_feasibility as RF
    from backend.engine.planning import Plan
    from backend.i18n import _
    ds = _dataset()
    today = O.day_to_date(ds.today)
    plan = Plan.load()                 # the season plan's events (ds.plan is empty in parity mode)
    if event_id and not any(e.id == event_id for e in plan.events):
        raise HTTPException(404, "no such event")
    return {"today": today.isoformat(), "races": RF.races(plan, ds, today, event_id=event_id),
            "levels": {k: _(v) for k, v in RF.LEVEL_LABEL.items()}, "chart_href": _specific_chart_href()}


def _specific_chart_href() -> str:
    """The viewer deep link of the 專項期 dashboard (custom views: a dashboard with id "build"),
    where the long days are charted against the race; the plain viewer when there is none."""
    from urllib.parse import urlencode
    from backend.engine.wko5expr.customviews import load_custom_views
    try:
        for name, v in load_custom_views().items():
            for di, d in enumerate(v.get("dashboards") or []):
                if d.get("id") == "build":
                    return "/api/v1/wko5/viewer?" + urlencode({"view": name, "dash": di})
    except Exception:                          # noqa: BLE001 — a broken view file: plain viewer link
        pass
    return "/api/v1/wko5/viewer"


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
    return render_page("overview")
