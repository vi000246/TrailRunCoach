"""
Overview API — the one-glance page: training status, what was done by week /
month / year (all sports together), the combined PMC and this week's plan.
"""
from __future__ import annotations

from backend import tenancy as _tenancy
import contextvars
import datetime as dt
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
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
log = logging.getLogger(__name__)

# The status card after new data, a new day or a restart (SP-394, the 課表's SP-362 B4 for the
# overview): GET /status answers this tenant's previous status at once, marked `stale`
# ({reason: data | day, since, age_s}), while the new one is computed in the background — through
# the single flight and the server's job queue (backend/jobs.py), never twice; GET /status/fresh
# says when it is done and the page reloads the card. Only when the user's own settings are the
# same (_SK_SETTINGS on: a change of theirs is computed in the request, they expect its effect)
# and the day did not go back. The previous status is also on disk (_last_path): after a deploy
# the in-memory caches are empty, and a tenant not warmed up ahead (accounts/tenants
# .active_tenants) still sees it at once. Display only: GET /weekplan, /z5, /b2b, the 課表 and
# plan_auto compute fresh (they join the same flight). Demo tenants: never kept, never stale.
_status_last: OrderedDict = OrderedDict()   # tenant id -> {settings, day, at, status} of its newest status
_STATUS_LAST_MAX = 64
_status_refreshing: dict = {}     # tenant id -> the key computed in the background now
_status_failed: dict = {}         # tenant id -> a key whose background computation failed (computed in the request next)
STALE_REASONS = ("data", "day")
# _status_key's layout: (tenant, dataset generation, day, then the user's own settings);
# _SK_HR: the HR stamp (HRP.stamp(), 課表心率區間) among them (status_but_hr, SP-361); last (_SK_LOCALE): the
# request's language, since the status's words are made in it (SP-502)
_SK_DAY, _SK_SETTINGS, _SK_HR, _SK_LOCALE = 2, 3, 6, -1


def _dataset():
    # same dataset (and engine config / parity mode) as the chart pages
    from backend.api.wko5views import _dataset as ds
    return ds()


def _plan_stamp() -> float:
    try:
        return plan_path().stat().st_mtime
    except OSError:
        return 0.0


def _status_key(ds, today: dt.date) -> tuple:
    """(key, prefs): the status's cache key from what it is computed from — (tenant, dataset
    generation, day, then the user's own settings, _SK_SETTINGS on) — and the 課表偏好 read for it."""
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
    # SP-502: the status's words are made in the request's language (_()): an English page was answered
    # with the Chinese status a Chinese page had computed first (and the other way round)
    from backend.i18n import current_locale
    key = (*_tenancy.ds_key(ds), today, _plan_stamp(), prefs.stamp(), tests, HRP.stamp(),
           None if pmc0 is None else tuple(sorted(pmc0.items())), AP.z3_rule_stamp(), EX.stamp(), current_locale())
    return key, prefs


def _status(ds, today: dt.date) -> Status:
    """Status is the slow part (it reads samples); memoise per dataset / day / plan edit.
    Computed as a job of the server's queue (backend/jobs.py, SP-394: `single`, `user` — at
    once inside a job already, e.g. the warm-up's); concurrent callers of a key share one."""
    key, prefs = _status_key(ds, today)
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
        # kept below for this tenant: without the Dataset, which the Dataset cache's budget decides
        # on (SP-394 第 6 條; a test's stand-in may have no detach)
        if hasattr(st, "detach"):
            st = st.detach()
        with _lock:
            # LRU per tenant (demo sandboxes, later users); the owner alone keeps one entry warm
            mine = [k for k in _status_cache if k[0] == key[0]]
            for k in mine:
                _status_cache.pop(k, None)
            _status_cache[key] = st
            while len(_status_cache) > _STATUS_MAX:
                _status_cache.pop(next(iter(_status_cache)))
        _remember_status(key, st)
        return st
    # single flight (SP-362): concurrent callers of this key wait for one computation, a job of
    # the server's queue (SP-394; the job runs only compute(), never a flight: backend/jobs.py)
    from backend import jobs
    return _STATUS_FLIGHT.do(key, lambda: jobs.call(compute, cls="single", priority="user", label="overview status"))


def _settings_sig(key: tuple) -> str:
    """The user's own settings part of a status key, stable across restarts."""
    return hashlib.sha1(repr(key[_SK_SETTINGS:]).encode("utf-8")).hexdigest()[:16]


def _last_path() -> Path:
    """This tenant's previous status on disk (data_registry: cache/overview_status.json)."""
    return _tenancy.private_path("cache", "overview_status.json")


def _remember_status(key: tuple, st: Status) -> None:
    """The tenant's newest status (memory, LRU of _STATUS_LAST_MAX tenants, and disk): what a
    stale answer shows. Never for a demo tenant; a failure here never fails the status."""
    if _tenancy.current().is_demo:
        return
    try:
        rec = {"settings": _settings_sig(key), "day": key[_SK_DAY].isoformat(), "at": time.time(),
               "status": st.to_dict()}
    except Exception as e:                     # noqa: BLE001 — only the stale answer is missed
        log.warning("status not kept for a stale answer: %s", type(e).__name__)
        return
    with _lock:
        _status_last[key[0]] = rec
        _status_last.move_to_end(key[0])
        while len(_status_last) > _STATUS_LAST_MAX:
            _status_last.popitem(last=False)
        _status_failed.pop(key[0], None)       # computed after all: a later miss may be served stale again
    p = _last_path()
    tmp = p.with_name(f"{p.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"v": 1, **rec}, ensure_ascii=False, default=str), "utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.warning("status not written for a stale answer: %s", type(e).__name__)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _previous_status(tid: str) -> Optional[dict]:
    """The tenant's newest kept status: memory, else the disk (after a restart)."""
    with _lock:
        rec = _status_last.get(tid)
    if rec is not None:
        return rec
    try:
        d = json.loads(_last_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(d, dict) and d.get("v") == 1 and isinstance(d.get("status"), dict)):
        return None
    rec = {k: d.get(k) for k in ("settings", "day", "at", "status")}
    with _lock:
        _status_last.setdefault(tid, rec)
    return rec


def _stale_status(key: tuple, ds, today: dt.date) -> Optional[dict]:
    """The previous status of this tenant marked `stale` when only the data or the day changed
    since it was computed, and the new one started in the background (once per key); else None."""
    if _tenancy.current().is_demo:
        return None
    tid = key[0]
    with _lock:
        if _status_failed.get(tid) == key:
            return None
    prev = _previous_status(tid)
    if prev is None or prev.get("settings") != _settings_sig(key) or str(prev.get("day")) > key[_SK_DAY].isoformat():
        return None
    reason = "day" if prev.get("day") != key[_SK_DAY].isoformat() else "data"
    with _lock:
        start = _status_refreshing.get(tid) != key
        if start:
            _status_refreshing[tid] = key
    if start:
        ctx = contextvars.copy_context()       # the tenant, the locale: the request's
        threading.Thread(target=ctx.run, args=(_refresh_status, key, ds, today), name="status-refresh",
                         daemon=True).start()
    at = float(prev.get("at") or 0.0)
    return {**prev["status"], "stale": {"reason": reason, "age_s": max(0, int(time.time() - at)),
                                        "since": dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat()}}


def _refresh_status(key: tuple, ds, today: dt.date) -> None:
    """The background half of a stale answer: this thread only waits — the computation is the
    single flight's, a job of the server's queue (a request asking meanwhile joins it)."""
    try:
        _status(ds, today)
    except Exception as e:                     # noqa: BLE001 — the next GET /status computes it itself
        log.warning("overview status refresh failed: %s", type(e).__name__)
        with _lock:
            _status_failed[key[0]] = key
    finally:
        with _lock:
            if _status_refreshing.get(key[0]) == key:
                del _status_refreshing[key[0]]


def status_answer(ds, today: dt.date) -> dict:
    """GET /status: the status as a dict — or, on a miss after new data / a new day / a restart,
    the previous one marked `stale` while the new one is computed (SP-394). During a first
    import's back-fill the cards that read further back than its Dataset holds wait (history_mask)."""
    key, _prefs = _status_key(ds, today)
    with _lock:
        hit = _status_cache.get(key)
    if hit is None:
        old = _stale_status(key, ds, today)
        if old is not None:
            return history_mask(old, ds)
    return history_mask(_status(ds, today).to_dict(), ds)


# SP-394 第 4 條 (owner 2026-10-13: CTL is never shown wrong): during a first import's back-fill
# (api/backfill.py) the hot-first Dataset holds the last ~90 days only. Each status card is shown
# when every day it reads lies inside it — the days it reads back from today, checked in
# engine/status.py: phase (the plan) 0, data / density / descent 28, strength 35, durability 84.
# Every other card waits for the complete Dataset: fitness / form (CTL / ATL / TSB: 6 × 42 days of
# warm-up, chartscope.WARMUP_TAU), volume (26 weeks), efficiency (181 d), heat (119 d), long
# (365 d), climb (every earlier activity), intensity / drift / gate / testing / level (the
# thresholds of each day: as-of estimates over 305 days, fitdataset.ESTIMATE_LOOKBACK_DAYS) and
# any card not listed here.
STATUS_READS = {"phase": 0, "data": 28, "density": 28, "descent": 28, "strength": 35, "durability": 84}


def history_mask(d: dict, ds) -> dict:
    """`d` (a status dict) with the cards a first import's hot-first Dataset cannot answer yet
    replaced by 「計算中（較舊的活動補算中 n／N）」 (level na, no number, no line) and the to-dos
    they back left out; `history_pending` {n_done, n_total, message}. `d` itself when complete."""
    from backend.api import backfill as BF
    if BF.coverage(ds) is None:
        return d
    from backend.i18n import _
    held, inds = set(), []
    for i in d.get("indicators") or []:
        p = BF.pending_back(ds, STATUS_READS.get(i.get("id")))
        if p is None:
            inds.append(i)
            continue
        held.add(i.get("id"))
        inds.append({**i, "level": "na", "text": _("計算中"), "verdict": p["message"], "why": p["message"],
                     "action": "", "value": None, "spark": [], "extra": {"pending": p}})
    out = {**d, "indicators": inds, "actions": [a for a in d.get("actions") or []
                                                  if not set(a.get("because") or []) & held],
           "history_pending": BF.pending(ds, None)}
    if held:
        out["counts"] = {lvl: sum(1 for i in inds if i.get("level") == lvl) for lvl in (d.get("counts") or {})}
        out["headline"] = f"{(d.get('phase') or {}).get('label') or ''}：{out['history_pending']['message']}".lstrip("：")
    return out


def history_load(w: dict, ds) -> dict:
    """GET /weekplan: during a first import's back-fill its CTL / ATL / TSB (today, Monday, the
    projection to Sunday) are None and `history_pending` says why; the sessions stay."""
    from backend.api import backfill as BF
    p = BF.pending(ds, None) if BF.coverage(ds) is not None else None
    if p is None:
        return w
    return {**w, "load": {k: None for k in (w.get("load") or {})},
            "projection": [{"date": r.get("date")} for r in w.get("projection") or []], "history_pending": p}


def status_updating() -> bool:
    """This tenant's new status is being computed behind a stale answer (GET /status/fresh)."""
    with _lock:
        return _tenancy.current().id in _status_refreshing


def status_peek() -> Optional[Status]:
    """This tenant's newest computed Status, whatever its key (None: none yet). For a stale
    課表 view only (api/plan_sessions._status_for, SP-362 B4): never computes."""
    me = _tenancy.current().id
    with _lock:
        for k in reversed(list(_status_cache)):
            if k[0] == me:
                return _status_cache[k]
    return None


def status_but_hr(ds, today: dt.date) -> Optional[Status]:
    """The cached Status of this key, else one of this tenant whose key differs only by the HR
    stamp (_SK_HR: 課表心率區間 switched) and / or the language (_SK_LOCALE); None when neither is cached — never computes. For
    readers of the plan, its phases and goals only (api/plan_sessions._status_for, SP-361): none
    of them depends on the HR model, the HR targets / zones of the Status do."""
    key, _prefs = _status_key(ds, today)
    with _lock:
        hit = _status_cache.get(key)
        if hit is not None:
            return hit
        for k in reversed(list(_status_cache)):
            # nor the language (_SK_LOCALE): the plan, its phases and goals are the same in every one
            if k[:_SK_HR] == key[:_SK_HR] and k[_SK_HR + 1:_SK_LOCALE] == key[_SK_HR + 1:_SK_LOCALE]:
                return _status_cache[k]
    return None


def _day(s: Optional[str], default: dt.date) -> dt.date:
    if not s:
        return default
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        raise HTTPException(400, f"bad date {s!r}")


@router.get("/status")
def status():
    """The status card (indicators, to-do, KPIs); `stale` when it is the previous one (SP-394)."""
    ds = _dataset()
    today = O.day_to_date(ds.today)
    return status_answer(ds, today)


@router.get("/status/fresh")
async def status_fresh():
    """SP-394: {updating}: the new status behind a stale GET /status is still being computed. The
    overview polls this (it computes nothing; answered on the event loop) and reloads the card after."""
    return {"updating": status_updating()}


@router.get("/summary")
def summary(unit: str = "week", anchor: Optional[str] = None, n: int = 12):
    if unit not in O.UNITS:
        raise HTTPException(400, f"unit must be one of {O.UNITS}")
    ds = _dataset()
    today = O.day_to_date(ds.today)
    n = max(1, min(n, {"week": 104, "month": 60, "year": 12}[unit]))
    out = O.summary(ds, unit, _day(anchor, today), n)
    # SP-394: periods older than a first import's hot-first Dataset holds are not zero: pending
    from backend.api import backfill as BF
    if BF.coverage(ds) is not None and out.get("buckets"):
        p = BF.pending(ds, O.date_to_day(dt.date.fromisoformat(out["buckets"][0]["start"])))
        if p is not None:
            return {**out, "pending": p}
    return out


@router.get("/pmc")
def pmc(begin: Optional[str] = None, end: Optional[str] = None):
    ds = _dataset()
    today = O.day_to_date(ds.today)
    e = _day(end, today)
    b = _day(begin, e - dt.timedelta(days=180))
    if b > e:
        raise HTTPException(400, "begin after end")
    # SP-394: CTL / ATL / TSB read every earlier day (6 × 42 of warm-up): a first import's
    # hot-first Dataset answers `pending` until its back-fill is done
    from backend.api import backfill as BF
    if BF.coverage(ds) is not None:
        from backend.engine.wko5expr.chartscope import WARMUP_TAU
        need = O.date_to_day(b) - 1 - WARMUP_TAU * float(ds.athlete.ctlconstant)
        p = BF.pending(ds, need)
        if p is not None:
            return {"begin": b.isoformat(), "end": e.isoformat(), "pending": p}
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
    st = _status(ds, today)
    w = history_load(O.week_plan(ds, st, today, prefs=PP.load(), blackouts=BL.load(),
                                 b2b_accepted=B2B.load_accepted(), race_predict=calculator_hours), ds)
    # SP-482: each week note with its hint fields (engine/hints.py: id, cls, prio, …; static/hints.js);
    # SP-483: with the overview's §5 conditions (R7 first week of the phase / cold start, R11 heat, R15)
    from backend.engine import hints as H
    try:
        monday = dt.date.fromisoformat(str((w.get("week") or {}).get("start"))[:10])
    except ValueError:
        monday = today - dt.timedelta(days=today.weekday())
    ctx = H.week_context(today, monday, phase_start=getattr(getattr(st, "phase", None), "start", None),
                         cold_week=(w.get("cold_start") or {}).get("week"))
    return {**w, "notes": H.notes(w.get("notes"), ctx)}


@router.get("/z5")
def z5_card():
    """The 「5 區（最大攝氧量間歇）狀態」 card: the status gate's Zone 5 state (the
    object week_plan decides with) as a checklist (quality_gate.z5_card)."""
    from backend.engine import quality_gate as QG
    ds = _dataset()
    today = O.day_to_date(ds.today)
    from backend.api import backfill as BF
    p = BF.pending_back(ds, STATUS_READS.get("gate")) if BF.coverage(ds) is not None else None
    if p is not None:                  # SP-394: the gate reads the thresholds of each day (estimates)
        return {"today": today.isoformat(), "pending": p, "history_href": _z5_chart_href()}
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
    from backend.api import backfill as BF
    p = BF.pending(ds, None) if BF.coverage(ds) is not None else None
    if p is not None:                  # SP-394: CTL and the long-run history: after the back-fill
        return {"today": today.isoformat(), "races": [], "pending": p,
                "levels": {k: _(v) for k, v in RF.LEVEL_LABEL.items()}, "chart_href": _specific_chart_href()}
    out = {"today": today.isoformat(), "races": RF.races(plan, ds, today, event_id=event_id),
           "levels": {k: _(v) for k, v in RF.LEVEL_LABEL.items()}, "chart_href": _specific_chart_href()}
    if not event_id:                   # SP-652: the season plan's 完備程度 row (not the race calculator's)
        out["ready_line"] = _ready_line(plan, ds, today)
    return out


READY_CARD = {"build": "race-readiness", "taper": "race-readiness-taper"}   # views/periodization.json


def _ready_line(plan, ds, today) -> Optional[dict]:
    """「賽前準備：第 n 段〈…〉x/y（…）」 of the next A race's 賽前完備程度 board (race_readiness.line) and the
    link to that card on the 專項期 page (減量期 page once the taper started); None when the board fails."""
    from backend.engine import race_readiness as RD
    try:
        ln = RD.line(RD.compute(plan, ds, today))
    except Exception:                          # noqa: BLE001 — no line: the card keeps its old row
        logging.getLogger(__name__).exception("race readiness line")
        return None
    ln["href"] = _chart_href_by_id(READY_CARD[ln["phase"]])
    return ln


def _chart_href_by_id(cid: str) -> str:
    """The viewer deep link of the custom-view chart with id `cid`; the plain viewer when there is none."""
    from urllib.parse import urlencode
    from backend.engine.wko5expr.customviews import load_custom_views
    try:
        for name, v in load_custom_views().items():
            for di, d in enumerate(v.get("dashboards") or []):
                for ci, c in enumerate(d.get("charts") or []):
                    if c.get("id") == cid:
                        return "/api/v1/app/viewer?" + urlencode({"view": name, "dash": di, "chart": ci})
    except Exception:                          # noqa: BLE001 — a broken view file: plain viewer link
        pass
    return "/api/v1/app/viewer"


def _specific_chart_href() -> str:
    """The viewer deep link of the 專項期 dashboard (custom views: a dashboard with id "build"),
    where the long days are charted against the race; the plain viewer when there is none."""
    from urllib.parse import urlencode
    from backend.engine.wko5expr.customviews import load_custom_views
    try:
        for name, v in load_custom_views().items():
            for di, d in enumerate(v.get("dashboards") or []):
                if d.get("id") == "build":
                    return "/api/v1/app/viewer?" + urlencode({"view": name, "dash": di})
    except Exception:                          # noqa: BLE001 — a broken view file: plain viewer link
        pass
    return "/api/v1/app/viewer"


def _z5_chart_href() -> str:
    """The viewer deep link of the first z5gate panel in the custom views (the 基礎期 chart)."""
    from urllib.parse import urlencode
    from backend.engine.wko5expr.customviews import load_custom_views
    try:
        for name, v in load_custom_views().items():
            for di, d in enumerate(v.get("dashboards") or []):
                for ci, c in enumerate(d.get("charts") or []):
                    if c.get("kind") == "z5gate":
                        return "/api/v1/app/viewer?" + urlencode({"view": name, "dash": di, "chart": ci})
    except Exception:                          # noqa: BLE001 — a broken view file: plain viewer link
        pass
    return "/api/v1/app/viewer"


@router.get("/page", include_in_schema=False)
def page():
    return render_page("overview")
