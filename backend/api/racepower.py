"""
Race calculator API — the 賽事計算機 page (docs/research/superpower-calculator.md).

    GET  /inputs        athlete-derived inputs (CP / W′ / TTE sources, RE, k, EP/h, conditions)
    GET  /peaks         百岳 / 小百岳 list (backend/data/baiyue.json)
    GET  /weather       race-day conditions via CWA → Open-Meteo → climatology → manual
    GET  /weather/key   CWA key status (masked);  POST /weather/key  save it
    POST /predict       road / trail / 百岳 prediction; every input overridable
    GET  /page          the HTML page

v2 (docs/research/racepower-v2.md §10.2):
    POST /course        upload .gpx / .fit → course_id + segments + profile
    POST /plan          three modes on a course; segments, effort bar, cross-checks
    GET  /grade-model   personal RE(g), v_max(g), v_h(g)
    GET  /cadence-check climbing cadence vs the 130 spm walk line (SP-230)
    GET  /backtest      stored leave-one-out back-test;  POST /backtest/run  recompute
    POST /export/plan   plan → the race-day session in the 課表 (preview, or push=true writes it)
    GET/PUT/DELETE /saved/{event_id}   the page's inputs + last result per plan event
    GET/POST/PUT /surface/{course_id}  the course's ground from OSM: 確定土徑 / 確定人工 / 不確定 (SP-543)
"""
from __future__ import annotations

from backend import tenancy as _tenancy
import datetime as dt
import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from backend.engine.localtime import today_local
from backend.i18n.pages import render_page
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.engine.planning import plan_path
from backend.singleflight import SingleFlight
from backend.tenant_cache import TenantSlots
from backend import membership as MS
from backend.engine.racepower import calc as CALC
from backend.engine.racepower import weather as WX
# the request bodies live with the computations (engine/racepower/calc.py); kept importable from here
from backend.engine.racepower.calc import (  # noqa: F401
    CourseRef, DayIn, EnvIn, EventCourseIn, ExportIn, HourIn, LockIn, PlanIn, PredictIn, PriorIn, StopIn)

STATIC = Path(__file__).resolve().parents[1] / "static"
router = APIRouter(prefix="/api/v1/racepower", tags=["racepower"])

DEFAULT_K = CALC.DEFAULT_K
INPUTS_TTL_S = 600.0
_lock = threading.Lock()
# named slots ("inputs", "grade", ...) for each tenant (SP-374 第 4 條: was one per slot for the
# whole server, so two people using the calculator pushed each other's results out)
_cache = TenantSlots()
_FLIGHT = SingleFlight()            # the slow per-athlete fits: one computation per key (SP-366)


def _dataset():
    from backend.api.wko5views import _dataset as ds
    return ds()


def _plan_stamp() -> float:
    try:
        return plan_path().stat().st_mtime
    except OSError:
        return 0.0


def inputs(refresh: bool = False) -> dict:
    """derive() for today, memoised per dataset / plan / day for 10 minutes. Computed outside
    the lock (SP-374 第 1 條: derive() takes ~3.4 s cold on the NAS, and it used to hold the one
    server-wide lock, so every other tenant's calculator waited); concurrent callers of the
    same key share one computation (_FLIGHT)."""
    from backend.engine.racepower.athlete import derive
    ds = _dataset()
    today = today_local()
    key = (*_tenancy.ds_key(ds), today, _plan_stamp())

    def cached():
        with _lock:
            hit = _cache.get("inputs")
        if hit and hit[0] == key and time.time() - hit[1] < INPUTS_TTL_S:
            return hit[2]
        return None

    if not refresh:
        got = cached()
        if got is not None:
            return got

    def compute():
        got = None if refresh else cached()
        if got is not None:
            return got
        d = derive(ds, today)
        with _lock:
            _cache["inputs"] = (key, time.time(), d)
        return d
    return _FLIGHT.do(("inputs", key, refresh), compute)


@router.get("/inputs")
def get_inputs(refresh: bool = False):
    """`refresh` skips the cache — at most once per cool-down per person (SP-714, cooldown
    `racepower_inputs`); while it cools down the cached answer comes back, never an error (the
    page asks for it on its own after saving the 百岳單攻 list)."""
    if refresh:
        from backend import cooldown
        if cooldown.ready("racepower_inputs"):
            cooldown.mark("racepower_inputs")
        else:
            refresh = False
    return inputs(refresh)


@router.get("/peaks")
def peaks(q: Optional[str] = None, baiyue_only: bool = False):
    ps = WX.load_peaks()
    if baiyue_only:
        ps = [p for p in ps if p.get("baiyue")]
    if q:
        ps = [p for p in ps if q in p["name"]]
    return {"count": len(ps), "peaks": ps}


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------

def _event(eid: str):
    from backend.engine.planning import Plan
    for e in Plan.load().events:
        if e.id == eid:
            return e
    raise HTTPException(404, f"no event {eid}")


@router.get("/weather")
def weather(date: Optional[str] = None, days: int = 1, event_id: Optional[str] = None,
            peak: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None,
            elevation: Optional[float] = None, cwa: bool = True, course_id: Optional[str] = None,
            link_event: Optional[str] = None):
    name = None
    note = None
    if link_event and not event_id:
        try:                                     # only for the Cairn link (SP-553); the place is the query's
            note = getattr(_event(link_event), "note", None)
        except HTTPException:
            note = None
    if event_id:
        e = _event(event_id)
        note = getattr(e, "note", None)
        date = date or e.date
        days = e.days or days
        pk = WX.find_peak(e.name)
        if pk:
            peak = peak or pk["name"]
        name = e.name
    if peak:
        pk = WX.find_peak(peak)
        if pk is None:
            raise HTTPException(404, f"找不到山名 {peak}")
        name = pk["name"]
        lat = pk["lat"] if lat is None else lat
        lon = pk["lon"] if lon is None else lon
        elevation = pk["elevation_m"] if elevation is None else elevation
    if event_id and (lat is None or lon is None):
        # no peak / coordinates: the start of the event's stored GPX
        got = _event_track(event_id)
        if got is not None and got[1].lat:
            tr = got[1]
            lat, lon = float(tr.lat[0]), float(tr.lon[0])
            z0 = next((z for z in tr.ele if z is not None), None)
            elevation = z0 if elevation is None else elevation
    if not date:
        raise HTTPException(400, "date is required")
    try:
        d = dt.date.fromisoformat(date[:10])
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    r = WX.race_conditions(date=d, days=max(1, days), lat=lat, lon=lon, elevation_m=elevation,
                           name=name, use_cwa=cwa)
    r["peak"] = WX.find_peak(name) if name else None
    r["points"] = _route_points(r, d, max(1, days), course_id, event_id, cwa)
    # SP-553: the same route in 疊石 Cairn — SP-537: the route the event's GPX was imported from, else a
    # link in the event's note
    r["cairn"] = _cairn_link(event_id or link_event, note)
    return r


def _cairn_link(eid: Optional[str], note: Optional[str]) -> Optional[dict]:
    if eid:
        from backend.engine import event_gpx as EG
        try:
            u = EG.cairn_url(EG.get(eid))
        except EG.EventGpxError:
            u = None
        if u:
            return {"url": u, "from": "import"}
    u = WX.cairn_url(note)
    return {"url": u, "from": "note"} if u else None


def _route_points(r: dict, date: dt.date, days: int, course_id: Optional[str], event_id: Optional[str],
                  cwa: bool) -> Optional[list]:
    """SP-553: a long course's weather at up to WX.ROUTE_MAX_POINTS points (one per WX.ROUTE_POINT_KM),
    each section's rows for the planner to read at its own clock. Only when the race's own point got a
    forecast (no points for the climatology: far-off dates keep the one point) and a track is known
    (the uploaded GPX `course_id`, else the event's stored GPX). None otherwise."""
    if r.get("provider") not in WX.FORECAST_PROVIDERS:
        return None
    tr = None
    if course_id:
        tr = LIVE.course_track(course_id)
    if tr is None and event_id:
        got = _event_track(event_id)
        tr = got[1] if got is not None else None
    if tr is None:
        return None
    secs = WX.route_sections(tr.lat, tr.lon, tr.ele)
    if not secs:
        return None
    return WX.route_conditions(secs, date=date, days=days, use_cwa=cwa)


class KeyIn(BaseModel):
    key: str = ""


@router.get("/weather/key")
def key_status():
    return WX.key_status()


@router.post("/weather/key")
def save_key(body: KeyIn):
    if not _tenancy.can("weather.key"):
        raise HTTPException(403, "氣象署授權碼只有站主能設定")
    k = body.key.strip()
    if k and (len(k) < 10 or len(k) > 80 or any(c.isspace() for c in k)):
        raise HTTPException(400, "授權碼格式不對")
    WX.save_key(k)
    return WX.key_status()


# ---------------------------------------------------------------------------
# prediction (engine/racepower/calc.py on the live athlete context)
# ---------------------------------------------------------------------------

class LiveContext:
    """calc.Context on this process's athlete: the Dataset, the season plan and the local
    files, memoised as before (inputs(), _grade_models(), ...)."""

    def inputs(self) -> dict:
        return inputs()

    def grade_models(self) -> dict:
        return _grade_models()

    def flags(self):
        from backend.engine.racepower import backtest as BT
        return BT.flags()

    def heat_status(self, date: Optional[str]) -> dict:
        return heat_status_for(date)

    def hrc_test(self) -> Optional[dict]:
        return _hrc_test()

    def trail_hr(self) -> Optional[dict]:
        return _trail_hr()

    def hr_basis(self) -> Optional[dict]:
        return _hr_basis()

    def body(self) -> Optional[dict]:
        return _body(inputs())

    def event(self, eid: str):
        return _event(eid)

    def event_track(self, eid: str):
        return _event_track(eid)

    def event_splits(self, row: dict, days: int) -> list[float]:
        """The event's own day ends (the user's, when they make `days` days); [] = none yet —
        the plan then cuts equal days and says so (推估, SP-533)."""
        from backend.engine import event_gpx as EG
        cuts, src = EG.split_source(row, days)
        return cuts if src == "stored" else []

    def event_meta(self, row: dict) -> Optional[dict]:
        from backend.engine import event_gpx as EG
        return EG.meta(row)

    def course_track(self, course_id: str):
        with _courses_lock:
            return _courses.get(course_id)


LIVE = LiveContext()


def _calc(fn, *args):
    """A calc.py computation; its CalcError → the same HTTP status and detail."""
    try:
        return fn(*args)
    except CALC.CalcError as e:
        raise HTTPException(e.status, e.detail)


@router.post("/predict")
def predict(body: PredictIn):
    MS.use_calc(body)                        # SP-713: the calculator's count, once a route a day
    return _calc(CALC.predict_response, LIVE, body)


def _predict_baiyue(body: PredictIn, d: dict, weight: float, env: dict, used: dict, warnings: list):
    return _calc(CALC.predict_baiyue, LIVE, body, d, weight, env, used, warnings)


@router.post("/estimate")
def estimate(body: PlanIn):
    """SP-293 用近期比賽成績推完賽時間: without a CP the calculator's model can't run; the shared race
    results (engine/race_results.py — the questionnaire's and SP-276's one list) give a 推估 time:
    road Riegel k −0.07, trail unsourced-rules §0.5.5 (engine/racepower/race_estimate.py). With a CP
    (typed in, the chosen or the default source): {"available": False, "model": True} — /plan's model."""
    from backend.engine import race_results as RR
    from backend.engine.race_feasibility import survey_hours
    from backend.engine.racepower import race_estimate as RX
    MS.use_calc(body)                        # SP-713: the calculator's count, once a route a day
    if RX.has_cp(inputs(), body):
        return {"available": False, "model": True}
    t = _calc(CALC.resolve_course, LIVE, body)["totals"]
    return RX.estimate(body.type, float(t.get("km") or 0.0), float(t.get("gain_m") or 0.0), RR.load(),
                       today_local(), survey_hours())


@router.get("/page", include_in_schema=False)
def page():
    return render_page("racepower")


# ---------------------------------------------------------------------------
# v2: course upload, plan, grade model, back-test, COROS export.
# /predict above is kept unchanged (v1); /plan calls it for the baseline.
# ---------------------------------------------------------------------------

COURSE_CACHE_MAX = 20
_courses: "OrderedDict[str, object]" = OrderedDict()
_courses_lock = threading.Lock()


_py = CALC.py
_course_opts = CALC.course_opts


def _build(track, opts: dict) -> dict:
    return _calc(CALC.build, track, opts)


@router.post("/course")
async def upload_course(file: UploadFile = File(...), sigma_m: Optional[float] = Form(None),
                        eps_m: Optional[float] = Form(None), min_len_m: Optional[float] = Form(None),
                        flat_pct: Optional[float] = Form(None), split: Optional[str] = Form(None),
                        official_gain_m: Optional[float] = Form(None)):
    """Parse a .gpx / .fit once and cache the Track (LRU 20, by content sha1);
    later plans send only the course_id and re-segment with their options."""
    from starlette.concurrency import run_in_threadpool
    from backend.engine.racepower import gpx as GPX
    data = await file.read(GPX.MAX_BYTES + 1)
    try:
        track = await run_in_threadpool(GPX.parse, data, file.filename or "")   # CPU: off the event loop
    except GPX.GpxError as e:
        raise HTTPException(400, str(e))
    cid = hashlib.sha1(data).hexdigest()
    _cache_course(cid, track)
    c = await run_in_threadpool(_build, track, _course_opts({
        "sigma_m": sigma_m, "eps_m": eps_m, "min_len_m": min_len_m, "flat_pct": flat_pct, "split": split,
        "official_gain_m": official_gain_m}))
    from backend.engine.racepower import fuel as FU
    from backend.engine.racepower import overnight as OV
    sug = FU.stops_from_wpts(c.get("wpts") or [], c["totals"]["km"])
    # SP-534: a recorded multi-day track's nights — a proposal the page asks about before applying
    nights = await run_in_threadpool(OV.find, track)
    return _py(CALC.respond({"course_id": cid, "name": track.name or file.filename, **c, "stop_suggestions": sug,
                             "overnights": nights}))


class CairnCourseIn(BaseModel):
    url: str = Field("", max_length=2000)
    split: Optional[str] = None
    sigma_m: Optional[float] = None
    eps_m: Optional[float] = None
    min_len_m: Optional[float] = None
    flat_pct: Optional[float] = None
    official_gain_m: Optional[float] = None


@router.post("/course/cairn")
async def cairn_course(body: CairnCourseIn):
    """SP-537: a course from a 疊石 Cairn share link — the upload's answer for Cairn's GPX, + the nights
    marked in Cairn as the day ends (`day_splits_km`, applied at once; the page can still change them)
    and the days. The token reads Cairn once and is not kept."""
    from starlette.concurrency import run_in_threadpool
    from backend.api.plan import _CAIRN_STATUS, _cairn_out
    from backend.engine import cairn as CA
    from backend.engine.racepower import fuel as FU
    from backend.engine.racepower import gpx as GPX
    try:
        imp = await run_in_threadpool(CA.import_link, body.url)
        track = await run_in_threadpool(GPX.parse, imp["data"], imp["filename"])
    except CA.CairnError as e:
        raise HTTPException(_CAIRN_STATUS.get(e.kind, 400), str(e))
    except GPX.GpxError as e:
        raise HTTPException(502, str(e))
    cid = hashlib.sha1(imp["data"]).hexdigest()
    _cache_course(cid, track)
    c = await run_in_threadpool(_build, track, _course_opts(body.model_dump(exclude={"url"})))
    sug = FU.stops_from_wpts(c.get("wpts") or [], c["totals"]["km"])
    out = _cairn_out(imp, route_km=c["totals"]["km"], ratio_rows=group_ratio_rows())
    return _py(CALC.respond({"course_id": cid, "name": imp["name"], **c, "stop_suggestions": sug,
                             "day_splits_km": imp["splits_km"], "days": imp["days"], "cairn": out,
                             "overnights": None}))


def group_ratio_rows() -> list:
    """SP-576: the past group hikes' moving / elapsed rows (the timetable's stops when Cairn has none);
    [] when the models can't be had (the 80 % default then)."""
    try:
        return _grade_models().get("moving_rows_group") or []
    except Exception:                       # noqa: BLE001
        return []


def _cache_course(cid: str, track) -> None:
    with _courses_lock:
        _courses[cid] = track
        _courses.move_to_end(cid)
        while len(_courses) > COURSE_CACHE_MAX:
            _courses.popitem(last=False)


def _event_track(eid: str):
    """(course_id, Track, row) of the GPX stored with a plan event (engine/event_gpx.py), cached
    like an upload; None when the event has none."""
    from backend.engine import event_gpx as EG
    try:
        got = EG.track(eid)
    except EG.EventGpxError:
        return None
    if got is None:
        return None
    track, row = got
    _cache_course(row["sha1"], track)
    return row["sha1"], track, row


@router.post("/course/event/{eid}")
def event_course(eid: str, body: Optional[EventCourseIn] = None):
    """The course of the GPX stored with a plan event — the race calculator's upload
    response, without uploading again; + `event_id` and the stored day splits."""
    return _calc(CALC.event_course, LIVE, eid, body)


def _grade_models() -> dict:
    """GradeRE + HikeSpeed + the hike moving-ratio rows, memoised like /inputs. One fit per
    key at a time (SP-366): /plan, GET /grade-model and a second tab on a cold cache wait for
    the same computation instead of each running the whole year's fit."""
    ds = _dataset()
    today = today_local()
    key = (*_tenancy.ds_key(ds), today)

    def cached():
        with _lock:
            hit = _cache.get("grade")
            if hit and hit[0] == key and time.time() - hit[1] < INPUTS_TTL_S:
                return hit[2]
        return None

    def compute():
        return cached() or _fit_grade_models(ds, today, key)
    return cached() or _FLIGHT.do(("grade", key), compute)


def _fit_grade_models(ds, today, key) -> dict:
    from backend.engine.achievements import KIND_HIKE, build_achievements
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import backtest as BT
    road = (inputs()["re"]["road"] or {}).get("median")
    gm = A.grade_models(ds, today, re_flat=road)
    # the race-like class's own RE(g) only when the back-test showed it clearly better
    gm["race_model"] = BT.class_model_flag()
    if gm["race_model"]:
        rw = getattr(gm["grade_re"], "runwalk", None)
        gm["grade_re"] = A.grade_models(ds, today, re_flat=road, classes=gm.get("classes"),
                                        only_classes={"race"}, hikes=False)["grade_re"]
        if rw is not None:
            gm["grade_re"].runwalk = rw           # SP-228: the transition shift from every run, not the race class only
    # SP-250: the dry / wet technicality only when the back-test kept it (downhill error not worse)
    gm["surface_ok"] = BT.surface_split_flag()
    if getattr(gm["grade_re"], "surface_split", False) and not gm["surface_ok"]:
        gm["grade_re"] = gm["grade_re"].without_surface_split("backtest")
    # SP-229: the curve by the predicted speed only when the back-test kept it (for the model used)
    if hasattr(gm["grade_re"], "with_speed_gait"):
        gm["grade_re"] = gm["grade_re"].with_speed_gait(BT.speed_gait_flag(class_model=gm["race_model"]))
    solo = A.solo_hikes()
    try:
        # the clock ETA's moving ratio, per trip kind: group hikes rest on the
        # group's schedule, solo ones on the athlete's (baiyue §2.6)
        rows = [(a.id in solo, {"moving_s": a.moving_s, "elapsed_s": a.elapsed_s}) for a in build_achievements(ds)
                if a.kind == KIND_HIKE and not a.days]
        gm["moving_rows"] = [r for s, r in rows if s]
        gm["moving_rows_group"] = [r for s, r in rows if not s]
    except Exception:                       # noqa: BLE001
        gm["moving_rows"], gm["moving_rows_group"] = [], []
    cap = gm.get("walk_capacity")
    hc = (BT.load() or {}).get("hike_capacity") or {}
    if cap is not None and hc.get("sigma_loo"):
        # the back-test's held-out segment log-error SD (segment level: wider
        # than a whole day's, so conservative)
        cap.sigma_loo = float(hc["sigma_loo"])
        cap.sigma_loo_src = f"回測：留一的段時間對數誤差 SD（{hc.get('today')}）"
    with _lock:
        _cache["grade"] = (key, time.time(), gm)
    return gm


@router.get("/grade-model")
def grade_model():
    gm = _grade_models()
    cap = gm.get("walk_capacity")
    return _py({"grade_re": gm["grade_re"].to_json(), "hike_speed": gm["hike_speed"].to_json(),
                "walk_capacity": cap.to_json() if cap is not None else None,
                "hike_hr": gm.get("hike_hr"), "hike_basis": gm.get("hike_basis"), "race_model": gm.get("race_model")})


@router.get("/cadence-check")
def cadence_check():
    """SP-230: the climbing cadence distribution against the 130 spm walk line (report only)."""
    from backend.engine.racepower import runwalk as RW
    secs, n = _climb_cadence()
    return _py({**RW.cadence_check(secs), "n_runs": n})


CHART_WARM_WAIT_S = 600.0      # how long the chart warm-up waits for the classification job


def warm_charts(job=None) -> None:
    """The warm-up's last step (api/wko5views._low_priority; SP-366): the grade-model fit and
    the climbing-cadence scan, so the first page that opens the charts finds them. After the
    activity classification `job` (waited for here, holding no place), as a job of the server's
    queue (backend/jobs.py, SP-394: `full`, `warm` — after every tenant's hot work); owner only —
    the demo and other tenants compute on request. A failure only logs."""
    import logging
    if _tenancy.demo_mode() or _tenancy.current().kind != _tenancy.OWNER:
        return
    wait_for = getattr(job, "wait_for", None)
    if wait_for is not None:
        wait_for("warm", CHART_WARM_WAIT_S)  # the year's activities (SP-394: not the cold back-fill)
    else:
        t = getattr(job, "thread", None)
        if t is not None and t.is_alive():
            t.join(CHART_WARM_WAIT_S)
    try:
        from backend import jobs
        jobs.call(_warm_chart_fits, cls="full", priority="warm", label="race charts warm-up")
    except Exception as e:               # noqa: BLE001 — the page computes on request
        logging.getLogger(__name__).warning("race chart warm-up failed: %s", type(e).__name__)


def _warm_chart_fits() -> None:
    _grade_models()
    _climb_cadence()


def _climb_cadence() -> tuple:
    """(seconds per cadence bin, runs) of the year's climbing, memoised per dataset and day;
    one scan per key for concurrent callers (SP-366). The texts follow each request's locale."""
    from backend.engine.racepower import athlete as A
    ds = _dataset()
    today = today_local()
    key = (*_tenancy.ds_key(ds), today)

    def scan():
        hit = _cache.get("climb_cadence")
        if not (hit and hit[0] == key):
            hit = (key, A.climb_cadence_seconds(ds, today))
            _cache["climb_cadence"] = hit
        return hit
    hit = _cache.get("climb_cadence")
    if not (hit and hit[0] == key):
        hit = _FLIGHT.do(("climb_cadence", key), scan)
    return hit[1]


def heat_status_for(date: Optional[str]) -> dict:
    """engine/heat_data.status for today, projected to `date` (the race day)."""
    from backend.engine import heat_data as HD
    rd = CALC.race_day(date)
    try:
        passive = HD.completed_passive_dates()
    except Exception:                       # noqa: BLE001
        passive = []
    return HD.status(today_local(), rd, passive_dates=passive)


def _hrc_test() -> Optional[dict]:
    """The HRC slope test (racepower/heatacc.py) on the last 84 days' hot
    steady segments, cached per day; None when it cannot be computed (no
    weather file, no dataset) — the calculator then credits no acclimation."""
    from backend.engine import heat as HT
    from backend.engine import heat_data as HD
    from backend.engine.racepower import heatacc as HA
    today = today_local()
    try:
        ds = _dataset()
    except Exception:                       # noqa: BLE001
        ds = None
    # SP-320 ④: the tenant and the dataset in the key, not the day alone (a second tenant
    # read the first one's test; a sync's new activities did not count until tomorrow)
    key = (*(_tenancy.ds_key(ds) if ds is not None else (_tenancy.current().id, None)), today)
    hit = _cache.get("hrc_test")
    if hit and hit[0] == key:
        return hit[1]
    try:
        acts, _meta = HD.exposures()
        rows = HT.hr_cost(HD.steady_segments(ds, today, acts))["rows"] if acts else []
        out = HA.hrc_slope_test(rows)
    except Exception:                       # noqa: BLE001
        out = None
    _cache["hrc_test"] = (key, out)
    return out


@router.get("/heat-status")
def heat_status(date: Optional[str] = None):
    """The heat-acclimation index S (engine/heat.py): today, its last 120
    days, and the race-day projection for `date` (推估)."""
    return _py(heat_status_for(date))


@router.get("/altitude-acclimatisation")
def altitude_acclimatisation(date: Optional[str] = None):
    """百岳的海拔適應 (SP-260; engine/altitude.acclimatisation_default): nights above 2,750 m in the 14
    days before `date` (the activities; SP-546: no records, no choice on the page) → partial |
    unacclimatised."""
    from backend.engine import altitude as AL
    today = today_local()
    try:
        alts = AL.day_altitudes(_dataset(), today)
    except Exception:                       # noqa: BLE001 — no data: no nights
        alts = {}
    return _py(AL.acclimatisation_default(alts, today, CALC.race_day(date)))


class HikeMetaIn(BaseModel):
    file: str
    pack_kg: Optional[float] = None


def _hike_rows() -> list[dict]:
    """Every hiking day with its pack (recorded or default) and the solo /
    group suggestion (capacity.classify_day; baiyue-from-running.md §2.6)."""
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import capacity as CAP
    ds = _dataset()
    today = today_local()
    gm = _grade_models()
    cap = gm.get("walk_capacity")
    meta = A.hike_meta()
    solo = A.solo_hikes()
    hikes = {w.entry.file: w for w in A.hike_workouts(ds, today)}
    days = inputs().get("hiking", {}).get("days") or []
    from backend.engine.wko5expr.dataset import date_to_day
    body_w = ds.setting("weight", date_to_day(today))
    out = []
    for d in days:
        w = hikes.get(d["file"])
        rec = meta.get(d["file"]) or {}
        default = CAP.pack_default(body_w, d.get("days") or 1)
        sug = None
        if w is not None:
            th = A.thresholds_as_of(ds, w.entry.start.date())
            wins = [x for x in A.hike_samples(ds, [w]) if x["day"] == (d.get("day") or 1)]
            L = CAP.day_pack(rec.get("pack_kg", default), d.get("day") or 1)
            sug = CAP.classify_day(wins, th.get("aet"), cap, L)
        out.append({**{k: d.get(k) for k in ("date", "trip", "name", "day", "days", "km", "gain_m", "moving_h",
                                             "ep_per_h", "file", "peaks")},
                    "solo": d["file"] in solo, "pack_kg": rec.get("pack_kg"), "pack_default": default,
                    "suggest": sug})
    marked = [{"marked": "solo" if r["solo"] else "group", "suggest": (r["suggest"] or {}).get("suggest")}
              for r in out]
    return out, CAP.classifier_validation(marked)


@router.get("/hike-meta")
def get_hike_meta():
    rows, val = _hike_rows()
    return _py({"days": rows, "validation": val,
                "note": "「看起來是自己走」只是建議；確認後才寫進自己走的清單"})


@router.post("/hike-meta")
def post_hike_meta(body: HikeMetaIn):
    from backend.engine.racepower import athlete as A
    try:
        trips = A.set_hike_meta(body.file, body.pack_kg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _lock:
        _cache.pop("grade", None)
    return {"trips": trips}


class SoloHikesIn(BaseModel):
    files: list[str] = []


@router.get("/solo-hikes")
def get_solo_hikes():
    """Hikes opted in as solo (the athlete's own pace): only these calibrate
    EP/h, the walking model and the hike back-test."""
    from backend.engine.racepower import athlete as A
    return {"files": sorted(A.solo_hikes()), "note": A.GROUP_HIKE_NOTE}


@router.post("/solo-hikes")
def post_solo_hikes(body: SoloHikesIn):
    from backend.engine.racepower import athlete as A
    s = A.set_solo_hikes(body.files)
    with _lock:
        _cache.pop("inputs", None)
        _cache.pop("grade", None)
    return {"files": sorted(s), "note": A.GROUP_HIKE_NOTE}


def _resolve_course(body: PlanIn) -> dict:
    return _calc(CALC.resolve_course, LIVE, body)


def _v1_for(body: PlanIn, course: dict) -> dict:
    return _calc(CALC.v1_for, LIVE, body, course)


def _trail_hr() -> Optional[dict]:
    """The trail HR pace model as of today (athlete.trail_hr_model), cached
    with the inputs."""
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import athlete as A
    AT.load()
    key = (*_tenancy.ds_key(_dataset()), today_local(), _plan_stamp(), AT._memo.get("stamp"))
    hit = _cache.get("trail_hr")
    if hit and hit[0] == key:
        return hit[1]
    try:
        m = A.trail_hr_model(_dataset())
        if not m.get("n"):
            # the chosen source has no trail history (e.g. a watch account synced only recently):
            # the WKO5 athlete folder has it
            from backend.api.wko5views import _dataset as wds
            m = A.trail_hr_model(wds(source="wko5"))
            m["dataset"] = "wko5"
    except Exception:                       # noqa: BLE001 — the planner falls back to the power total
        import traceback
        traceback.print_exc()
        m = None
    _cache["trail_hr"] = (key, m)
    return m


def _hr_basis() -> Optional[dict]:
    """What the heart-rate zone bar (engine/racepower/zonebar.py) needs beyond LTHR: max /
    rest HR in effect today, the COROS account's zone ratios and the 課表心率區間 model
    (hr_profile), cached per dataset / day / plan / HR settings."""
    from backend.engine import hr_profile as HP
    try:
        ds, day = _dataset(), today_local()
        key = (*_tenancy.ds_key(ds), day, _plan_stamp(), HP.stamp())
        hit = _cache.get("hr_basis")
        if hit and hit[0] == key:
            return hit[1]
        acc = HP.account()
        mx, rs = HP.max_hr(ds, day, acc), HP.rest_hr(ds, day, acc)
        # SP-493: the zones in effect (自動's pick, or the model chosen)
        b = {"model": HP.effective_model(ds, day)["model"], "acc": acc, "mhr": mx.get("value"), "rhr": rs.get("value")}
    except Exception:                       # noqa: BLE001 — the bar keeps its LTHR tables
        import traceback
        traceback.print_exc()
        return None
    _cache["hr_basis"] = (key, b)
    return b


def make_plan(body: PlanIn) -> dict:
    return _calc(CALC.make_plan, LIVE, body)


def _body(inp: dict) -> Optional[dict]:
    """inputs()["body"] (settings profile → the dataset's athlete); a COROS /
    TP dataset has no WKO5 athlete, so missing fields come from the WKO5
    athlete file itself when there is one. No "body" key (old cache, tests)
    = nothing to fill: fuel uses its labelled defaults."""
    b = inp.get("body")
    if b is None or all(b.get(k) is not None for k in ("height_cm", "sex", "age")):
        return b
    b = dict(b)
    try:
        from backend.engine.racepower import athlete as A
        from backend.files.wko5_athlete import read_athlete
        from backend.settings.paths import athlete_dir
        f = next(athlete_dir().glob("*.wko5athlete"), None)
        if f is not None:
            plan = type("P", (), {"profile": {}})()
            w = A.body_profile(type("D", (), {"plan": plan, "athlete": read_athlete(f)})(), today_local())
            for k in ("height_cm", "sex", "age"):
                if b.get(k) is None and w.get(k) is not None:
                    b[k], b[k + "_src"] = w[k], w[k + "_src"]
    except (OSError, ValueError, StopIteration):
        pass
    return b


def _goal_settings() -> tuple[Optional[str], Optional[bool], bool]:
    """(課表偏好 target_basis, 使用功率 stored or None = auto, accept_watch_power)."""
    from backend.engine import plan_prefs as PP
    from backend.engine.wko5expr.datasource import read_setting
    return (PP.load().target_basis, read_setting("charts.power.enabled", None),
            bool(read_setting("power.accept_watch_power", False)))


@router.get("/goal-basis")
async def goal_basis():
    """Which goal the page offers (engine/racepower/goal.py): hr → a target
    pace, power → a target power."""
    from backend.engine import athlete_profile as AP
    from backend.engine.racepower import goal as GOAL
    tb, up, watch = _goal_settings()
    if up is None and tb not in ("hr", "power"):
        from backend.api.sync import _power_source
        up = AP.use_power((await _power_source())[0], watch)
    return GOAL.basis(tb, up)


@router.post("/plan")
def plan(body: PlanIn):
    """One route a day = one use of the calculator (the free tier's monthly count, SP-330 / SP-713:
    the page re-plans after every edit; the same route the same day counts once)."""
    MS.use_calc(body)
    return _py(CALC.respond(make_plan(body)))      # SP-482: warnings as hint objects (= calc.plan)


@router.get("/backtest")
def backtest_result():
    from backend.engine.racepower import backtest as BT
    return {"state": BT.state(), "result": BT.load()}


@router.post("/backtest/run", dependencies=[Depends(MS.require_scope("calc:backtest"))])
def backtest_run():
    """Queue this tenant's back-test: one use of the monthly count — none when one is already
    running or queued (SP-713: {started: False, already: True}, 「已經在算」)."""
    from backend.engine.racepower import backtest as BT
    st = BT.state()
    if st.get("running") or st.get("queued"):
        return {"started": False, "already": True, "message": "已經在算", "state": st}
    got = MS.consume("backtest")             # 429 past the plan's limit
    run = _backtest_runner()                 # None: workers off — start_background's default, as before
    started = BT.start_background(_dataset, run=run) if run is not None else BT.start_background(_dataset)
    if not started:                          # a concurrent click queued it first: this one counts nothing
        MS.refund("backtest", got)
        return {"started": False, "already": True, "message": "已經在算", "state": BT.state()}
    return {"started": started, "state": BT.state()}


def _backtest_runner():
    """The back-test in a worker process (SP-394 第 5 條, backend/workproc.py): it computes in
    Python for minutes (NAS ~160 s) and, in the server, made every page ~10× slower meanwhile.
    The queued job (`backtest`, one for the server) builds / reads this tenant's Dataset first, so
    the worker loads it warm from the disk caches. None (in the server, as before) when workers are
    off (WKO5COACH_PROCESS_JOBS=0, the tests)."""
    from backend import workproc
    if not workproc.enabled():
        return None

    def run(prog) -> None:
        from backend.api import wko5views as WV
        from backend.engine.racepower import backtest as BT
        ds = _dataset()
        key = WV.key_of(ds)
        if key is None:                      # not a kept Dataset: compute it here
            BT.save(BT.backtest(ds, None, prog))
            return
        del ds
        workproc.run("backend.api.racepower:backtest_in_worker", key,
                     progress=lambda i, n, label: prog(i, n, {"label": label}))
    return run


def backtest_in_worker(key: tuple, progress) -> bool:
    """The worker's side (backend/workproc.py): this tenant's back-test on the Dataset of `key`,
    stored where GET /backtest reads it."""
    from backend.api import wko5views as WV
    from backend.engine.racepower import backtest as BT
    ds = WV.worker_dataset(key)
    BT.save(BT.backtest(ds, None, lambda i, n, c: progress(i, n, c.get("label"))))
    return True


def _thresholds(p: dict) -> dict:
    a = inputs().get("aet") or {}
    return {"cp": (p["used"].get("cp") or {}).get("value"), "lthr": a.get("lthr"),
            "aet": (p["used"].get("aet") or {}).get("value") or a.get("aet")}


TERRAIN = {"road": "road", "trail": "trail", "baiyue": "hike"}
EXPORT_SOURCE = "賽事計算機匯出（分段目標推估）"


def race_session(body: ExportIn, p: dict, ev, calib: Optional[dict] = None) -> tuple[dict, dict]:
    """(the 課表 session the export writes, the watch_export legs) for a plan: kind race on
    the event's date, named 「賽事 <name>」, the legs as its steps; minutes = the plan's
    moving time, tss = watch_export.tss_info (推估: the open legs at the calculator's own
    predicted race HR, race_hr) × the post-race correction factor (`calib`, tss_calib.factor).
    ex["tss"]: {raw, factor, n, k, tss, open_h, open_if, open_src, fallback}."""
    from backend.engine.racepower import planner as PL
    from backend.engine.racepower import watch_export as WE
    sm = p["summary"]
    name = body.name or ev.name or p.get("course_name") or f"{sm['km']:.0f} km"
    ex = WE.steps_for(p, p.get("chart_rows") or [], mode=body.step_mode, stops=[x.model_dump() for x in body.stops],
                      day_splits_km=body.day_splits_km if p["type"] == "baiyue" else None)
    badge = "（推估）" if sm.get("badge") else ""
    hint = "每段直到按下計圈" if ex["mode"] == "lap" else "每段依距離"
    detail = f"{hint}；{PL.HINT_30S}；分段目標{badge}" if p["type"] != "baiyue" else f"{hint}；只看心率（≤ AeT）；分段目標{badge}"
    th = _thresholds(p)
    hr, hr_src = WE.race_hr(p, th)
    ti = WE.tss_info(ex["legs"], th, hr, hr_src)
    cf = calib or {"factor": 1.0, "n": 0, "k": None}
    tss = round(min(2000.0, ti["tss"] * cf["factor"]), 1) if ti["tss"] else None
    ex["tss"] = {**ti, "raw": ti["tss"], "tss": tss, "factor": cf["factor"], "n": cf["n"], "k": cf.get("k")}
    if ti["open_h"] > 0:
        # how the legs without a power / HR target were counted (the session note on the 課表)
        detail += (f"；負荷推估：無目標段強度比 {ti['open_if']:.2f}（{ti['open_src']}）" if not ti["fallback"] else
                   f"；負荷推估：沒有心率預測，無目標段用預設強度比 {WE.DEFAULT_IF:.2f}")
    if cf["n"]:
        detail += f"；賽後校正 ×{cf['factor']:.2f}（{cf['n']} 場）"
    km, gain = sm.get("km"), sm.get("gain_m")
    s = {"title": f"賽事 {name}"[:200], "day": str(ev.date)[:10], "steps": ex["doc"], "detail": detail,
         "minutes": max(1, min(1440, round(float(sm.get("time_s") or 0) / 60))),
         "tss": tss, "terrain": TERRAIN.get(p["type"]),
         "distance_km": min(500.0, float(km)) if km else None, "climb_m": min(20000.0, float(gain)) if gain else None,
         "target": "", "source": EXPORT_SOURCE}
    return s, ex


def _blocked() -> dict:
    """ISO day -> label of the 不排課日期 (engine/blackouts.py)."""
    from backend.engine import blackouts as BL
    try:
        return {d: b.label for d, b in BL.blocked(BL.load()).items()}
    except Exception:                       # noqa: BLE001 — no stored blackouts: none
        return {}


async def _db():
    from backend.db.database import get_db
    async for s in get_db():
        yield s


# ---------------------------------------------------------------------------
# read-only share links (engine/racepower/share.py)
#
#   POST   /api/v1/racepower/share          same body as /plan + title / include_weight /
#                                            expires_days → freeze the result, return the link
#   GET    /api/v1/racepower/shares         the athlete's shares
#   DELETE /api/v1/racepower/shares/{id}    remove one
#   GET    /share/{id}        (public)      the read-only page
#   GET    /share/{id}/data   (public)      the frozen snapshot
#
# Only /share/ is meant to be reachable without the site password (the
# Cloudflare tunnel's Basic-auth proxy exempts that prefix); it serves
# nothing but stored snapshots.
# ---------------------------------------------------------------------------

class ShareIn(PlanIn):
    share_title: Optional[str] = Field(None, max_length=80)
    include_weight: bool = False
    expires_days: Optional[int] = None
    include_cairn_curve: bool = False       # SP-653: the 時間曲線 for 疊石 Cairn, only when ticked


@router.post("/share")
def create_share(body: ShareIn):
    from backend.engine.racepower import share as SH
    MS.use_calc(body)                        # SP-713: the calculator's count, once a route a day
    p = _py(make_plan(body))
    req = {"date": body.date, "start_time": body.start_time, "stops": [x.model_dump() for x in body.stops]}
    title = body.share_title or p.get("course_name") or \
        f"{ {'road': '路跑', 'trail': '越野', 'baiyue': '百岳'}.get(body.type, '')} {p['summary']['km']:.1f} km"
    curve = None
    if body.include_cairn_curve:
        from backend.engine.racepower import cairn_curve as CC
        try:
            curve = CC.for_plan(p, body.course.event_id if body.course else None, body.start_time,
                                computed_at=SH._now().isoformat())
        except CC.CurveError as e:
            raise HTTPException(400, str(e))
    try:
        snap = SH.snapshot(p, title=title, include_weight=body.include_weight, expires_days=body.expires_days,
                           request=req, cairn_curve=curve)
        sid = SH.save(snap)
    except SH.ShareError as e:
        raise HTTPException(400, str(e))
    _register_share(sid)
    return {"id": sid, "url": f"/share/{sid}", "title": snap["title"], "created": snap["created"],
            "expires": snap["expires"]}


@router.get("/shares")
def list_shares():
    from backend.engine.racepower import share as SH
    return {"shares": [{**r, "url": f"/share/{r['id']}"} for r in SH.listing()]}


@router.delete("/shares/{sid}")
def delete_share(sid: str):
    from backend.engine.racepower import share as SH
    try:
        if not SH.delete(sid):
            raise HTTPException(404, "找不到這個分享")
    except SH.ShareError as e:
        raise HTTPException(400, str(e))
    _unregister_share(sid)
    return {"deleted": sid}


def _signed_in_user():
    """The signed-in user (sign-in mode), else None (owner mode: the owner's shares need no row)."""
    from backend.accounts import login as LG
    return LG.current_user() if LG.required() else None


def _register_share(sid: str) -> None:
    """SP-124: accounts.db's shares row (id -> the user whose tenant holds it), so the public
    /share/<id> opens the right tenant (tenancy_mw). Without the row the link would not open:
    the snapshot is taken back and the error goes up."""
    u = _signed_in_user()
    if u is None:
        return
    from backend.accounts import store
    from backend.engine.racepower import share as SH
    try:
        store.add_share(sid, u.id)
    except Exception:
        SH.delete(sid)
        raise


def _unregister_share(sid: str) -> None:
    u = _signed_in_user()
    if u is None:
        return
    from backend.accounts import store
    owner = store.share_owner(sid)
    if owner is not None and owner.id == u.id:
        store.remove_share(sid)


share_router = APIRouter(prefix="/share", tags=["share"], include_in_schema=False)
SHARE_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer"}


@share_router.get("/{sid}")
def share_page(sid: str):
    # 「用自己的資料算」 (SP-124): the same link for every visitor (login.join_link); the snapshot is
    # read by the page from /data — nothing is computed here
    import json

    from backend.accounts import login as LG
    join = json.dumps(LG.join_link()).replace("<", "\\u003c")
    return render_page("share", headers=SHARE_HEADERS, fill={"join": f"<script>window.TRC_JOIN = {join};</script>"})


@share_router.get("/{sid}/data")
def share_data(sid: str):
    from fastapi.responses import JSONResponse

    from backend.engine.racepower import share as SH
    from backend import tenancy
    try:
        snap = SH.load(sid)
    except (SH.ShareError, tenancy.NoTenant):   # NoTenant: a disabled account's share (tenancy_mw)
        snap = None
    if snap is None:
        raise HTTPException(404, "這個分享不存在或已刪除")
    if SH.expired(snap):
        raise HTTPException(410, "這個分享已過期")
    return JSONResponse(snap, headers=SHARE_HEADERS)


@router.post("/export/plan")
async def export_plan(body: ExportIn, db=Depends(_db)):
    """「匯出至課表」: the plan as the race-day session of the stored plan (plan_sessions,
    kind race, ext_key racecalc:<event id>), editable on the 課表 page and pushed to the watch
    with the plan's own push. A target event is required (one session per event: exporting
    again overwrites it — plan_store.upsert_external, which also takes over the generator's
    own 比賽 row of that week). push=false: the preview (the steps as the watch gets them, the
    day, whether an earlier export is overwritten and whether it was changed on the 課表 page
    since). push=true: write it; an earlier export changed on the 課表 page is only
    overwritten with overwrite=true (else 409 EDITED)."""
    from starlette.concurrency import run_in_threadpool
    from backend.api.plan_sessions import _wlock
    from backend.engine import plan_store as PS
    from backend.engine import workout_steps as WS
    from backend.engine.racepower import tss_calib as TC
    from backend.engine.racepower import watch_export as WE
    from backend.settings.repository import SettingsRepository
    from backend.sync import coros_workouts as CW
    from backend.sync import workout_targets as WT
    eid = body.event_id or (body.course.event_id if body.course else None)
    if not eid:
        raise HTTPException(400, "先選目標賽事：匯出至課表會放在那場比賽的日子")
    ev = _event(eid)                        # 404 for an event that is not in the plan
    if not ev.date:
        raise HTTPException(400, "這場賽事沒有日期")
    await run_in_threadpool(MS.use_calc, body)     # SP-713: the calculator's count (accounts.db: off the loop)
    p = _py(await run_in_threadpool(make_plan, body))      # reads the Dataset: never on the event loop
    why = WE.multi_day(p, body.start_time, max(int(body.days or 1), int(getattr(ev, "days", None) or 1)))
    if why:
        raise HTTPException(400, why)
    repo = SettingsRepository(db)
    calib, calib_changed = TC.refresh(await repo.get(TC.KEY), await PS.load(db))
    s, ex = race_session(body, p, ev, TC.factor(calib))
    th = _thresholds(p)
    try:
        s["steps"] = WS.normalize(s["steps"])
    except WS.StepsError as e:
        raise HTTPException(400, f"分段轉成課表步驟失敗：{e}")
    # SP-632: the runnable legs as COROS gets them (Effort Pace; over power with 課表偏好「可跑坡目標」)
    pv = WS.watch_preview(s["steps"], WS.Ctx(cp=th["cp"], lthr=th["lthr"], aet=th["aet"],
                                             ep_power=WS.ep_power_of(PS.load_prefs_safe())),
                          CW.workout_name(s), s["detail"])
    key = CW.RACE_KEY_PREFIX + eid
    today = today_local().isoformat()
    blocked = _blocked()
    async with _wlock():
        try:
            r = await PS.upsert_external(db, key, s, today, blocked=blocked, write=False)
            if body.push and (r["previous"] or {}).get("user_edited") and not body.overwrite:
                raise HTTPException(409, {"error": "EDITED", "message": "這堂比賽課在課表上改過，確認後才會覆蓋"})
            if body.push:
                r = await PS.upsert_external(db, key, s, today, blocked=blocked, write=True)
                # the raw estimate of this race, for the factor once it is done (one entry per race)
                new = TC.record(calib, key, ex["tss"]["raw"], s["day"])
                if calib_changed or new != calib:
                    await repo.set(TC.KEY, new)
                    await db.commit()
        except PS.PlanError as e:
            raise HTTPException(400, str(e))
    old_watch = False
    try:                                    # the calculator's old direct push (replaced by the plan's push)
        prov = await WT.active(db)
        old_watch = (await prov.rows_by_key(db, [key])).get(key) is not None
    except Exception:                       # noqa: BLE001 — a preview never fails on the record lookup
        old_watch = False
    ss = r["session"]
    known = body.push or r["previous"] is not None
    return {"day": ss["day"], "title": ss["title"], "minutes": ss["minutes"], "tss": ss["tss"], "tss_info": ex["tss"],
            "mode": ex["mode"], "legs": ex["legs"], "merged": ex["merged"], "limit": ex["limit"],
            "notes": ex["notes"] + pv["lost"], "lines": pv["lines"], "steps": pv["n"],
            "action": r["action"], "previous": r["previous"], "old_watch": old_watch, "written": bool(body.push),
            "uid": ss["uid"] if known else None,
            "plan_url": f"/api/v1/overview/plan/schedule/page?day={ss['day']}" + (f"&uid={ss['uid']}" if known else "")}


# ---------------------------------------------------------------------------
# SP-543: the course's ground from OpenStreetMap (engine/racepower/surface.py), stored per
# course file (engine/course_surface.py): 確定土徑 / 確定人工 / 不確定
# ---------------------------------------------------------------------------

class SurfaceIn(BaseModel):
    event_id: Optional[str] = None          # the event whose stored GPX this is (after a restart)
    refresh: bool = False                   # look it up again (drops the marks)


class SurfaceEditIn(BaseModel):
    overrides: Optional[dict] = None        # {segment index: dirt / made / unsure}
    manual: Optional[dict] = None           # {dirt, made, unsure} whole %, adding up to 100
    clear_manual: bool = False


def _surface_track(cid: str, eid: Optional[str]):
    """The Track of course `cid`: the upload cache, else the event's stored GPX with that sha1."""
    with _courses_lock:
        tr = _courses.get(cid)
    if tr is None and eid:
        got = _event_track(eid)
        if got is not None and got[0] == cid:
            tr = got[1]
    return tr


def _surface_id(cid: str) -> str:
    from backend.engine import course_surface as CS
    try:
        return CS.check_id(cid)
    except CS.CourseSurfaceError as e:
        raise HTTPException(400, str(e))


@router.get("/surface/{cid}")
def get_surface(cid: str):
    """The stored surface of a course (status none: never looked up)."""
    from backend.engine import course_surface as CS
    from backend.engine.racepower import surface as SF
    return SF.view(_surface_id(cid), CS.get(cid))


@router.post("/surface/{cid}")
def course_surface(cid: str, body: Optional[SurfaceIn] = None):
    """The stored surface, or look it up on the Overpass server ($TRC_OVERPASS_URL) and store it.
    A failed lookup is an answer too (status unknown: the page says 路面未知), never an error:
    the calculator works without it."""
    from backend.engine import course_surface as CS
    from backend.engine.racepower import surface as SF
    body = body or SurfaceIn()
    _surface_id(cid)
    row = CS.get(cid)
    if row and row.get("profile") and not body.refresh:
        return SF.view(cid, row)
    if body.refresh:                              # 重查: a cool-down per person (SP-714, 429 COOLDOWN)
        from backend import cooldown
        cooldown.take("surface")
    track = _surface_track(cid, body.event_id)
    if track is None:
        return SF.view(cid, None, SF.SurfaceError("course", "no track"))
    try:
        prof = SF.analyse(track)
    except SF.SurfaceError as e:
        return SF.view(cid, None, e)
    try:
        return SF.view(cid, CS.save(cid, profile=prof))
    except CS.CourseSurfaceError:
        return SF.view(cid, {"profile": prof})          # no DB (a demo / test): shown, not kept


@router.put("/surface/{cid}")
def edit_surface(cid: str, body: SurfaceEditIn):
    """The user's marks on the segments and / or a typed-in split; stored with the course."""
    from backend.engine import course_surface as CS
    from backend.engine.racepower import surface as SF
    _surface_id(cid)
    row = CS.get(cid)
    if not row or not row.get("profile"):
        raise HTTPException(404, "no surface")
    try:
        manual = None if body.clear_manual else (SF.clean_manual(body.manual) if body.manual else False)
        ov = SF.clean_overrides(body.overrides, len(row["profile"].get("segs") or [])) \
            if body.overrides is not None else None
        return SF.view(cid, CS.save(cid, overrides=ov, manual=manual))
    except (ValueError, CS.CourseSurfaceError) as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# saved inputs + result per plan event (engine/race_calc_store.py)
# ---------------------------------------------------------------------------

class SavedIn(BaseModel):
    inputs: dict
    result: Optional[dict] = None


@router.get("/saved/{eid}")
def get_saved(eid: str):
    from backend.engine import race_calc_store as RC
    try:
        return {"saved": RC.get(eid)}
    except RC.RaceCalcError as e:
        raise HTTPException(400, str(e))


@router.get("/saved/{eid}/cairn-curve")
def saved_cairn_curve(eid: str, download: bool = False):
    """SP-653: the 時間曲線 for 疊石 Cairn (cairn-curve v1, docs/spec/cairn-curve.md) of the event's saved
    calculation on its stored GPX. 404 NO_RESULT; 409 NO_GPX / GPX_CHANGED / GPX_MISMATCH / NO_TRACK."""
    from fastapi.responses import JSONResponse
    from backend.engine.racepower import cairn_curve as CC
    try:
        curve = CC.for_saved(eid)
    except CC.CurveError as e:
        raise HTTPException(e.status, {"code": e.code, "message": str(e)})
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="cairn-curve-{eid}.json"'
    return JSONResponse(curve, headers=headers)


@router.put("/saved/{eid}")
def put_saved(eid: str, body: SavedIn):
    from backend.engine import race_calc_store as RC
    _event(eid)                             # 404 for an event that is not in the plan
    try:
        r = RC.save(eid, body.inputs, body.result)
    except RC.RaceCalcError as e:
        raise HTTPException(400, str(e))
    return {"event_id": eid, "saved_at": r["saved_at"] if r else None, "uid": r.get("uid") if r else None}


@router.delete("/saved/{eid}")
def delete_saved(eid: str):
    from backend.engine import race_calc_store as RC
    try:
        return {"deleted": RC.delete(eid)}
    except RC.RaceCalcError as e:
        raise HTTPException(400, str(e))
