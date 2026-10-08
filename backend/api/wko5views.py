"""
WKO5 view reproduction API — renders the user's exported .wko5chart views
straight from a WKO5 athlete folder, for side-by-side verification against
WKO5 itself.

Env:
    WKO5_ATHLETE_DIR  folder containing <Name>.wko5athlete and year/*.wko4
                      (unset: backend/settings/paths.py looks under ~/WKO5)
    WKO5_VIEWS_DIR    folder searched (recursively) for your own exported
                      *.wko5chart views (else the charts.wko5_views_dir
                      setting; unset: no imported WKO5 views)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import weakref
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from backend.engine.localtime import today_local
from backend.i18n.pages import render_page
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from dataclasses import asdict

from backend.engine.wko5expr.chartfixes import FIXES_PATH, apply_fixes, load_fixes
from backend.engine.wko5expr.viewids import ensure_ids
from backend.engine.wko5expr import viewi18n as VI
from backend.engine.wko5expr.config import config_path, MOUNTAIN_PRESET, EngineConfig
from backend.engine.wko5expr.corrections import (
    CorrectionStore, detect_spikes, proposals_to_corrections,
)
from backend.engine.wko5expr.customviews import (
    REPO_VIEWS, user_views, load_custom_views, view_dirs,
)
from backend.engine.wko5expr.dataset import Dataset, date_to_day
from backend.engine.wko5expr import datasource as DSRC
from backend.engine.wko5expr.fitdataset import dataset_for_source
from backend.engine.wko5expr import periods as PD
from backend.engine.wko5expr import basis as BS
from backend.engine.wko5expr import variants as VR
from backend.engine.wko5expr import recentbests as RB
from backend.engine.wko5expr import power_use as PU
from backend.engine.wko5expr.render import render_chart, render_map
from backend.engine.wko5expr.render_cache import CACHE as RENDER_CACHE, chart_key
from backend.engine.wko5expr import chartscope as CS
from backend.files.wko5chart_reader import read_view
from backend.settings.paths import athlete_dir
from backend import tenancy

ROOT = Path(__file__).resolve().parents[2]
ATHLETE_DIR = athlete_dir()     # WKO5_ATHLETE_DIR, else found under ~/WKO5 (settings/paths.py)
VIEWS_SETTING = "charts.wko5_views_dir"


def views_dir() -> Optional[Path]:
    """The folder with the user's own exported WKO5 views (*.wko5chart):
    WKO5_VIEWS_DIR, else the charts.wko5_views_dir setting, else None (no
    imported WKO5 views; the bundled views/*.json still work). Never the repo:
    WKO5 chart packs are the user's own files, not shipped with the app."""
    v = os.getenv("WKO5_VIEWS_DIR")
    if not v:
        try:
            from backend.engine.wko5expr.datasource import read_setting
            v = read_setting(VIEWS_SETTING, None)
        except Exception:               # noqa: BLE001 — no app DB: no imported views
            v = None
    return Path(v).expanduser() if v else None

router = APIRouter(prefix="/api/v1/wko5", tags=["wko5-views"])


_LIVE: "weakref.WeakSet[Dataset]" = weakref.WeakSet()


DATASETS_MAX = 2       # Datasets kept at once (all modes together)


class _DatasetCache:
    """The built Datasets, by (engine config, source, stamp, shared root).

    One Dataset per mode (config, source, shared root): building a new stamp
    of a mode drops the older stamp of that mode, and at most DATASETS_MAX are
    kept overall (least recently used goes). A FIT Dataset holds hundreds of
    MB once the charts have filled its caches; the old lru_cache(maxsize=4)
    kept the last four stamps alive after every sync / settings change, which
    pushed the NAS container to its memory limit. A request that still holds
    an old Dataset finishes with it; it is freed when that request is done.
    Same interface as the lru_cache it replaces (call, cache_clear)."""

    def __init__(self, build):
        self._build = build
        self._lock = threading.Lock()
        self._d: "OrderedDict[tuple, Dataset]" = OrderedDict()
        self.__wrapped__ = build

    def __call__(self, cfg_json: str, source: str = "wko5", stamp: str = "", shared: str = "") -> Dataset:
        key = (cfg_json, source, stamp, shared)
        with self._lock:
            hit = self._d.get(key)
            if hit is not None:
                self._d.move_to_end(key)
                return hit
        ds = self._build(*key)
        with self._lock:
            mode = (cfg_json, source, shared)
            for k in [k for k in self._d if (k[0], k[1], k[3]) == mode and k != key]:
                del self._d[k]
            self._d[key] = ds
            self._d.move_to_end(key)
            while len(self._d) > DATASETS_MAX:
                self._d.popitem(last=False)
        return ds

    def cache_clear(self) -> None:
        with self._lock:
            self._d.clear()

    def entries(self) -> list[tuple]:
        with self._lock:
            return list(self._d)


def _build_dataset(cfg_json: str, source: str = "wko5", stamp: str = "", shared: str = "") -> Dataset:
    """The Dataset for charts.data_source: the WKO5 athlete folder, or the
    COROS / TP FIT folder (FitFolderDataset: thresholds from the plan, the
    app DB's athlete_settings and as-of estimates; WKO5 only when opted in).
    `stamp` = datasource.source_stamp: a sync that adds FITs or WKO5
    rewriting its index gives a new stamp, so a fresh Dataset (_DatasetCache
    then drops the one of the old stamp)."""
    from backend.engine.wko5expr import buildstate
    st = buildstate.get(source)
    st.begin()                      # runs only on a cache miss: a real build
    if source == "wko5":
        st.phase("wko5")
    from backend import applog
    try:
        with applog.timed("dataset build", source=source):        # SP-215: the wait behind every chart
            ds = dataset_for_source(source, ATHLETE_DIR, config=EngineConfig.from_dict(json.loads(cfg_json)))
    except BaseException as e:
        st.fail(e)
        raise
    st.finish()
    _LIVE.add(ds)
    try:                            # stored plan rows find their activities by start (activity_key.py)
        from backend.engine import activity_key as AK
        AK.register_dataset(ds)
    except Exception:               # noqa: BLE001
        pass
    return ds


_dataset_cfg = _DatasetCache(_build_dataset)


def plan_changed(thresholds: bool) -> None:
    """Season plan edited. Event / phase edits only move goal lines, so the
    cached datasets just pick up the new plan; threshold edits change hrTSS
    and zones everywhere, so the datasets are rebuilt."""
    if thresholds:
        _dataset_cfg.cache_clear()
    # events / phases: Dataset.plan reads the tenant's plan.json per request
    # (memoised on its stamp), so nothing to push into the shared Datasets


_FLIGHT: dict = {}                       # build key -> lock (single flight)
_FLIGHT_LOCK = threading.Lock()


def _dataset_key(parity: Optional[bool] = None, source: Optional[str] = None) -> tuple[str, str, str, str]:
    """(engine config, source, files stamp, the tenant's shared root): every
    demo sandbox maps to its base, so they all share one Dataset (§2.4)."""
    cfg = EngineConfig.load()
    if parity is not None and parity != cfg.parity:
        cfg = cfg.replace(parity=parity)
    src = source if source in DSRC.SOURCES else DSRC.current_source()
    return (json.dumps(cfg.to_dict(), sort_keys=True), src, DSRC.source_stamp(src, ATHLETE_DIR),
            str(tenancy.current().shared))


def _dataset(parity: Optional[bool] = None, source: Optional[str] = None) -> Dataset:
    """parity=True reproduces WKO5 exactly (verification mode); False applies
    the athlete's own adjusted formulas from ~/.wko5coach/engine.json.
    `source` (default: the charts.data_source setting): wko5 | coros | tp.
    The chart page, overview (api/overview.py) and race power
    (api/racepower.py) all come through here.

    Single flight: concurrent callers of the same key (the viewer asks for a
    dozen charts at once) wait for ONE build instead of each building its
    own; the build's progress is in buildstate (GET /dataset/status)."""
    # built from the base's data (DB settings, thresholds): a sandbox that
    # triggers the build never puts its own copy into the shared Dataset
    with tenancy.use(tenancy.base_of()):
        return _dataset_in_tenant(parity, source)


def _dataset_in_tenant(parity: Optional[bool], source: Optional[str]) -> Dataset:
    key = _dataset_key(parity, source)
    with _FLIGHT_LOCK:
        lk = _FLIGHT.get(key)
        if lk is None:
            # forget the locks of old stamps (never asked for again)
            for k in [k for k in _FLIGHT if k[1] == key[1] and k != key and not _FLIGHT[k].locked()]:
                del _FLIGHT[k]
            lk = _FLIGHT[key] = threading.Lock()
    with lk:
        return _dataset_cfg(*key)


_WARM = {"thread": None}


def _warm_chart_code(ds) -> None:
    """Each chart's code signature (chartscope.code_signature, SP-336): ~2.5 s local for every
    chart of the bundled views, once per process — here instead of in the first chart page."""
    try:
        for v in _views(ds.config.parity).values():
            for d in v.get("dashboards") or []:
                for ch in d.get("charts") or []:
                    CS.code_signature(ch, ds, glue=(chart, _render, _apply_period))
    except Exception as e:               # noqa: BLE001 — the chart request computes it then
        import logging
        logging.getLogger(__name__).warning("chart code warm-up failed: %s", type(e).__name__)


PLAN_IDLE_WAIT_S = 600.0       # the warm-up's low-priority part waits this long for plan_auto
_PLAN_POLL_S = 0.5
_LOW_LOCK = threading.Lock()


def _wait_plan_idle(limit_s: float = PLAN_IDLE_WAIT_S) -> None:
    """Block (this background thread only) while an automatic plan run is going (SP-362)."""
    import time
    from backend.engine import plan_auto
    end = time.monotonic() + limit_s
    while plan_auto.busy() and time.monotonic() < end:
        time.sleep(_PLAN_POLL_S)


def _calibrate_never_fitted(ds) -> None:
    """每人校正 (engine/calibrate.py): fit what was never fitted (a new install, a new item)
    now instead of waiting for the next sync."""
    from backend.engine import calibrate as CAL
    if any(CAL.stored_entry(n) is None for n, it in CAL._registry().items() if not it.manual_only):
        import asyncio

        async def fit_once():
            # its own engine: this thread runs its own event loop
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
            from backend.db.database import DATABASE_URL
            eng = create_async_engine(DATABASE_URL)
            try:
                async with async_sessionmaker(eng, expire_on_commit=False)() as db:
                    await CAL.calibrate(db, ds=ds)
            finally:
                await eng.dispose()
        asyncio.run(fit_once())


def _low_priority(reason: str) -> None:
    """The warm-up's lowest-priority part, in its own thread (review SP-362 #4): wait for an
    automatic plan run to end, then — on the Dataset read AFTER the wait (a sync may have
    rebuilt it meanwhile) — the never-fitted calibration and the 活動列表's automatic
    classification (started in its own thread, read from disk when nothing changed). A
    warm-up asked for while this waits only marks it to run once more (`low_again`)."""
    import logging
    while True:
        try:
            _wait_plan_idle()
            ds = _dataset()
            _calibrate_never_fitted(ds)
            from backend.api import activity_auto as AA, racepower as RP
            RP.warm_charts(AA.job_for(ds))       # then the race calculator's charts (SP-366; owner only)
        except Exception as e:           # noqa: BLE001 — a page request will show the error
            logging.getLogger(__name__).warning("warm-up low-priority part (%s) failed: %s", reason,
                                                type(e).__name__, exc_info=True)
        with _LOW_LOCK:
            if not _WARM.get("low_again"):
                _WARM["low"] = None
                return
            _WARM["low_again"] = False


def _start_low_priority(reason: str) -> None:
    import contextvars
    with _LOW_LOCK:
        t = _WARM.get("low")
        if t is not None and t.is_alive():
            _WARM["low_again"] = True        # the waiting one runs once more, on the newest Dataset
            return
        _WARM["low_again"] = False
        ctx = contextvars.copy_context()     # the tenant (a Thread does not carry contextvars)
        t = threading.Thread(target=ctx.run, args=(_low_priority, reason), name=f"warmup-low-{reason}",
                             daemon=True)
        _WARM["low"] = t
        t.start()


def warm_up(reason: str = "startup") -> Optional[threading.Thread]:
    """Build what every page load needs in a background thread, so the first page load does
    not wait. In order (SP-362): the active source's Dataset → the overview status → the
    課表's plan inputs (api/plan_sessions._compute_inputs); then, in a thread of its own
    (_low_priority) at the lowest priority and after an automatic plan run (engine/plan_auto.py)
    has ended, the never-fitted 每人校正 items and the 活動列表's automatic classification on the
    Dataset read after that wait. The FIT parsing runs in a process
    pool (fitcache.py); a request arriving meanwhile joins the same computation (single
    flight: the dataset here, the status and the inputs in backend/singleflight.py). None
    when a warm-up is already running or WKO5COACH_NO_WARMUP is set."""
    if os.getenv("WKO5COACH_NO_WARMUP"):
        return None
    t = _WARM["thread"]
    if t is not None and t.is_alive():
        return None

    def run():
        import logging
        import time
        from backend import applog
        t0 = time.perf_counter()
        try:
            ds = _dataset()
            from backend.api import overview as OV
            OV._status(ds, OV.O.day_to_date(ds.today))
            # the 課表 / 總覽 plan inputs: the third thing a page load waits for (SP-362)
            try:
                from backend.api import plan_sessions as PSA
                PSA._compute_inputs()
            except Exception as e:       # noqa: BLE001 — a page request will show the error
                logging.getLogger(__name__).warning("plan inputs warm-up (%s) failed: %s", reason,
                                                    type(e).__name__)
            _warm_chart_code(ds)
            applog.took("dataset warm-up", t0, reason=reason)       # SP-215: what pages wait for
        except Exception as e:           # noqa: BLE001 — a page request will show the error
            logging.getLogger(__name__).warning("dataset warm-up (%s) failed: %s", reason, type(e).__name__,
                                                exc_info=True)
            return
        # lowest priority from here, in its own thread: after the sync's automatic plan run (it
        # computes with the same dataset / status / inputs, now warm). This thread ends, so a
        # warm-up asked for meanwhile is not dropped (review SP-362 #4)
        _start_low_priority(reason)
    import contextvars              # a Thread does not carry the tenant (contextvars) by itself
    ctx = contextvars.copy_context()
    t = threading.Thread(target=ctx.run, args=(run,), name=f"dataset-warmup-{reason}", daemon=True)
    _WARM["thread"] = t
    t.start()
    return t


@router.get("/dataset/status")
async def dataset_status():
    """Progress of the chart Dataset build of the active data source:
    state idle | building | ready | error, phase (解析 FIT, 整理活動, 估算門檻
    ...), n_done / n_total, and `message` (「正在處理第 350／808 筆（解析 FIT）…」).
    Async on purpose: answered on the event loop, never queued behind the
    builds in the thread pool."""
    from backend.engine.wko5expr import buildstate
    src = DSRC.current_source()
    cur = buildstate.get(src).snapshot()
    return {**cur, "sources": buildstate.all_states()}


def _wko5_views_raw() -> dict[str, dict]:
    """Views imported from WKO5 `.wko5chart` binaries, exactly as WKO5 has them
    (from views_dir(); none when no folder is configured)."""
    d = views_dir()
    return _wko5_views_in(str(d)) if d is not None else {}


@lru_cache(maxsize=2)
def _wko5_views_in(folder: str) -> dict[str, dict]:
    out = {}
    root = Path(folder)
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.wko5chart")):
        if ".venv" in p.parts or "node_modules" in p.parts:
            continue
        v = ensure_ids(read_view(p))     # chart ids from the titles: wko5_fixes.json matches on them
        v["source"] = "wko5"
        out[p.stem] = v
    return out


@lru_cache(maxsize=4)
def _wko5_views_fixed(fixes_mtime: float, folder: Optional[str] = None) -> dict[str, dict]:
    """WKO5 views with views/wko5_fixes.json applied (keyed on the file's
    mtime, so editing the fixes and reloading picks them up)."""
    try:
        fixes = load_fixes()
    except (OSError, ValueError) as e:
        print(f"[wko5views] ignoring {FIXES_PATH}: {e}")
        return _wko5_views_raw()
    return apply_fixes(_wko5_views_raw(), fixes)


def _wko5_views(parity: bool = True) -> dict[str, dict]:
    """Parity mode shows WKO5's charts untouched (side-by-side verification);
    otherwise the chart-design fixes are applied."""
    if parity:
        return _wko5_views_raw()
    try:
        mtime = FIXES_PATH.stat().st_mtime
    except OSError:
        return _wko5_views_raw()
    d = views_dir()
    return _wko5_views_fixed(mtime, str(d) if d else None)


def _views(parity: Optional[bool] = None) -> dict[str, dict]:
    """WKO5 views plus the athlete's own JSON views. Custom views are NOT
    cached, so editing a file and reloading the page picks it up."""
    if parity is None:
        parity = EngineConfig.load().parity
    # translations (views/i18n/<locale>.json) go on after the fixes, by chart id; zh-TW: as written
    return VI.translate_views({**_wko5_views(parity), **load_custom_views()})


def _view(name: str, parity: Optional[bool] = None) -> dict:
    v = _views(parity).get(name)
    if v is None:
        raise HTTPException(404, f"view {name!r} not found")
    if v.get("error"):
        raise HTTPException(400, f"{v.get('path')}: {v['error']}")
    return v


MAP_PANEL = "PKMapPanelConfig"


def _panel_kind(c: dict) -> Optional[str]:
    """WKO5's map panel has no series; it is a workout's GPS track ("map").
    A review card (workout_review.py) and a kind "activity" chart
    (panels/activity_charts.py) are workout charts to the viewer."""
    if c.get("kind") in ("review", "activity"):
        return "workout"
    return "map" if c.get("kind") == "other" and c.get("class") == MAP_PANEL else c.get("kind")


def _needs_met() -> dict:
    """Chart `needs` conditions on the athlete's own data (customviews.NEEDS):
    "poles" = ≥ 5 有杖 and ≥ 5 沒杖 marks in the last 365 days on the trail runs
    and hikes the chart uses (SP-243, pole_compare.counts). False when the tag
    store or the data can't be read."""
    from backend.engine import activity_tags as AT
    from backend.engine.panels import pole_compare as PC
    try:
        rows = AT.load()
        # no mark at all and no race marked 「會用登山杖」 (its activities count, SP-300): no need
        # to wait for the Dataset
        from backend.engine.wko5expr.dataset import tenant_plan
        race = any(getattr(e, "poles", False) for e in (tenant_plan().events or []))
        poles = bool(AT.pole_marks_stamp(rows) or race) and PC.counts(_dataset(), rows, today_local())["eligible"]
    except Exception:                          # noqa: BLE001 — no tag store / data: the chart stays hidden
        poles = False
    return {"poles": poles}


@router.get("/views")
def list_views():
    met = None

    def needs(c):
        nonlocal met
        if not c.get("needs"):
            return {}
        met = _needs_met() if met is None else met
        return {"needs": c["needs"], "needs_met": bool(met.get(c["needs"]))}

    return [
        {"name": name, "source": v.get("source", "wko5"), "path": v.get("path"),
         "error": v.get("error"),
         "dashboards": [
            {"index": i, "id": d.get("id"), "title": d["title"], "description": d.get("description"),
             **({"descriptions": d["descriptions"]} if d.get("descriptions") else {}),
             "charts": [{"index": j, "id": c.get("id"), "title": c.get("title"), "kind": _panel_kind(c),
                         "series": len(c.get("series", [])),
                         # 「使用功率」 off (charts.power.enabled): the viewer hides power-only charts
                         # and locks 配速／功率 toggles to pace (power_use.py)
                         "power": PU.chart_needs_power(c), "power_basis": PU.power_basis(c),
                         **({"zoned": c["zoned"]} if c.get("zoned") else {}),
                         # 主要訓練項目 (engine/primary_sport.py): the viewer hides charts whose
                         # `sports` doesn't list the athlete's sport and sorts by `order`
                         **({"sports": c["sports"]} if c.get("sports") else {}),
                         **({"order": c["order"]} if c.get("order") else {}),
                         # `needs` (customviews.NEEDS): the viewer hides the chart unless needs_met
                         **needs(c),
                         **({"view": c.get("view")} if c.get("kind") == "periodzones" else {})}
                        for j, c in enumerate(d["charts"])]}
            for i, d in enumerate(v["dashboards"])]}
        for name, v in _views().items()
    ]


@router.get("/views/dirs")
def custom_view_dirs():
    """Where to put your own view JSON files."""
    return {"dirs": [str(p) for p in view_dirs()],
            "repo": str(REPO_VIEWS), "user": str(user_views()),
            # your exported WKO5 views (*.wko5chart): WKO5_VIEWS_DIR / charts.wko5_views_dir
            "wko5": str(views_dir()) if views_dir() else None}


def _kind_filter(ds: Dataset, sports: Optional[str]):
    """The `sports` query as a workout predicate (engine/sport_map.py, SP-263):
    activity kinds 越野跑 / 路跑 / 登山健行 / 百岳登山 / ...; a WKO5 sport group
    ("run", from an older link) still selects its group. None = everything."""
    from backend.engine import sport_map as SM
    return SM.make_filter(sports, ds)


def _range(ds: Dataset, begin: Optional[str], end: Optional[str]) -> tuple[float, float]:
    e = date_to_day(dt.date.fromisoformat(end)) if end else ds.today
    b = date_to_day(dt.date.fromisoformat(begin)) if begin else e - 365
    return b, e


@router.get("/views/{view}/dashboards/{d}/charts/{c}")
def chart(request: Request, view: str, d: int, c: int, begin: Optional[str] = None,
          end: Optional[str] = None, sports: Optional[str] = None, workout: Optional[int] = None,
          parity: Optional[bool] = None):
    """Athlete charts use begin/end/sports (the RHE); workout charts need `workout`.

    Served from the render cache (render_cache.py): the key covers the chart
    definition, every query parameter (so a future `source` / `period` param
    is part of it automatically), the data the chart reads and its code (SP-336,
    chartscope.py). `?stale=1` (the viewer): on a miss, the chart's previous
    drawing marked `stale` at once while the new one is computed (GET /render/ready)."""
    ds = _dataset(parity)
    v = _view(view, ds.config.parity)
    try:
        ch = v["dashboards"][d]["charts"][c]
    except IndexError:
        raise HTTPException(404, "chart not found")
    b, e = _range(ds, begin, end)
    asked = (b, e)                               # the range as asked, before a period's floor (_slot)
    needs_workout = _panel_kind(ch) in ("workout", "map")
    if needs_workout and (workout is None or not 0 <= workout < len(ds.workouts)):
        raise HTTPException(400, "workout charts need ?workout=<index>")
    if not needs_workout and ch.get("kind") not in ("athlete", "zones", "targets", "z5gate", "periodzones",
                                                    "climbvam", "polecompare"):
        raise HTTPException(400, f"unsupported panel {ch.get('class')}")
    pinfo = winfo = binfo = vinfo = None
    if v.get("source") == "custom" and VR.variant_spec(ch):
        # chart variants (variants.py): ?variant=pct — first, since it picks the axes and series
        ch, vinfo = VR.apply_variant(ch, request.query_params.get("variant"))
    if v.get("source") == "custom" and BS.basis_spec(ch):
        # 配速／功率 (basis.py): ?basis=power — athlete, workout and review charts alike
        ch, binfo = BS.apply_basis(ch, request.query_params.get("basis"))
    if ch.get("kind") == "athlete":
        ch, b, pinfo = _apply_period(ch, b, e, request.query_params.get("period"),
                                     custom=v.get("source") == "custom")
        if v.get("source") == "custom" and RB.window_spec(ch):
            # 近 7／14／28 天新高 (recentbests.py): ?window=14
            ch, winfo = RB.apply_window(ch, request.query_params.get("window"))
    params = {k: val for k, val in request.query_params.items() if k not in ("begin", "end", "parity", "stale")}
    if params.get("sports") and not needs_workout:
        # the activity-type filter reads the user's activity-type marks (百岳跟團 / 爬山) and the plan's
        # 百岳 events (the plan is in the fingerprint, chartscope.py): the marks are in the key too
        from backend.engine import sport_map as SM
        params = {**params, "_kinds": SM.tags_stamp()}
    if ch.get("kind") == "z5gate":
        # the replay follows the 間歇門檻 preference (課表偏好) and the stored test / interval
        # sessions: both in the key so a changed preference isn't served from the cache
        from backend.engine import plan_prefs as PP
        from backend.engine.plan_store import test_sessions
        # …and the stored test sessions, which the progress tracker's status gate reads (overview._status)
        tests = [[s["uid"], s["state"], (s.get("done_by") or {}).get("index"), s.get("protocol")]
                 for s in test_sessions()]
        params = {**params, "_prefs": PP.load().stamp(), "_tests": json.dumps(tests, default=str)}
    if ch.get("kind") in ("zones", "targets", "activity", "periodzones"):
        # HR zones / targets read the COROS account and the 課表心率區間 setting (engine/hr_profile.py)
        from backend.engine import hr_profile as HP
        params = {**params, "_hr": HP.stamp()}
    if ch.get("race_refs") == "course_constant":
        # the events' stored GPX (engine/event_gpx.py) changes the reference lines, not plan.json
        from backend.engine import event_gpx as EG
        params = {**params, "_event_gpx": json.dumps(sorted((k, r.get("sha1"), r.get("day_splits"))
                                                             for k, r in EG.all_rows().items()), default=str)}
    if ch.get("kind") == "climbvam":
        # the route index, the renames and the per-activity weather are inputs too
        from backend.engine.routes import RouteStore
        st = RouteStore()
        stamp = []
        for p in (st.index_path, st.names_path, st.root / "activity_weather.json"):
            try:
                stamp.append(p.stat().st_mtime_ns)
            except OSError:
                stamp.append(None)
        params = {**params, "_routes": json.dumps(stamp)}
    if ch.get("kind") == "polecompare":
        # the 有杖／沒杖 marks live in the tags DB (not in the data fingerprint) and the
        # eligibility counts the last 365 days from today
        from backend.engine import activity_tags as AT
        params = {**params, "_poles": json.dumps(AT.pole_marks_stamp(AT.load())),
                  "_today": today_local().isoformat()}
    # the data source is in the fingerprint too (chartscope: ds.source, each file's stamp); named here as well
    req = {"view": view, "d": d, "c": c, "begin": b, "end": e, "parity": ds.config.parity,
           "source": getattr(ds, "source", None) or "wko5",
           "params": params, "workout_file": ds.workouts[workout].entry.file if needs_workout else None,
           "variant": vinfo["variant"] if vinfo else None}
    # SP-336: only the activities of this chart's range (+ warm-up), today only when it reads it,
    # only the code of its chart type (chartscope.py)
    scope = CS.scope_of(ch, ds, b, e, ds.workouts[workout] if needs_workout else None)
    key = chart_key(ch, req, CS.fingerprint(ds, scope), CS.code_signature(ch, ds, glue=(chart, _render, _apply_period)))

    def compute():
        res = _render(ch, ds, b, e, sports, ds.workouts[workout] if needs_workout else None, params=params)
        if ch.get("race_refs") == "course_constant" and not needs_workout:
            # 目標賽事參考線 (panels/race_refs.py): the plan and the dataset are in the cache key
            import math
            from backend.engine.panels import race_refs as RR
            from backend.engine.planning import Plan
            from backend.files.wko5_athlete import day_to_date
            res = RR.apply(res, getattr(ds, "plan", None) or Plan(), day_to_date(int(math.floor(ds.today))))
        if ch.get("drift_bars") and not needs_workout:
            # 心率飄移 bars (panels/drift_bars.py): each bar's date / duration / temperature on hover
            from backend.engine.panels import drift_bars as DB
            res = DB.apply(res, ds)
        if winfo:
            rb = RB.summarize(res, ds, winfo["window"])      # also drops the gain series
            res = {**res, **winfo, "recent_bests": rb}
        if binfo:
            if needs_workout:
                from backend.engine.workout_review import _has
                res = BS.no_power_note(res, ch, _has(ds.channel(ds.workouts[workout].idx, "power")))
            res = {**res, **binfo}
        if vinfo:
            res = {**res, **vinfo}
        return {**res, **pinfo} if pinfo else res
    # a demo visitor never gets (nor leaves) a previous drawing; the export / scripts don't ask
    demo = tenancy.current().is_demo
    res, stale = RENDER_CACHE.serve(key, compute, slot=None if demo else _slot(view, ch, req, asked, ds),
                                    stale_ok=request.query_params.get("stale") == "1" and not demo)
    return {**res, "stale": stale} if stale else res


def _slot(view: str, ch: dict, req: dict, asked: tuple, ds) -> str:
    """The chart request apart from its data and code (render_cache stale-while-revalidate): the
    view, the chart (its id), the parameters the user chose and the range as asked — 「the N days
    to today」 when it ends today, so the next day still finds yesterday's drawing."""
    import hashlib
    import math
    from backend.i18n import current_locale
    b, e = asked
    rng = ["today", e - b] if e >= math.floor(ds.today) else [b, e]
    body = {"view": view, "chart": ch.get("id") or [req["d"], req["c"]], "kind": ch.get("kind"), "range": rng,
            "params": {k: x for k, x in req["params"].items() if not k.startswith("_")},
            "locale": current_locale(), **{k: req.get(k) for k in ("parity", "source", "workout_file", "variant")}}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


@router.get("/render/ready")
async def render_ready(keys: str = ""):
    """Which of the keys a stale chart named (`stale.key`) can be fetched now: {ready, failed,
    pending}. Async: answered on the event loop, never queued behind the renders."""
    import re
    ks = [k for k in keys.split(",") if re.fullmatch(r"[0-9a-f]{40}", k)][:100]
    return RENDER_CACHE.ready(ks)


def _apply_period(ch: dict, b: float, e: float, asked: Optional[str], custom: bool):
    """Period-total charts (periods.py). Returns (chart, begin, extra JSON):
    the chart re-bucketed when the viewer asked for another period (custom
    views only, and not on week-locked charts), begin moved back to the
    period's look-back floor and to a bucket start, and what the viewer needs
    for the category axis + toggle."""
    default = PD.chart_period(ch)
    if default is None:
        return ch, b, None
    toggle = custom and not PD.period_locked(ch)
    chosen = asked if toggle and asked in PD.PERIODS else default
    note = None
    if custom:
        floor = PD.min_days(ch, chosen)
        if floor and e - b + 1 < floor:
            b = e - floor + 1
            note = {365: "顯示近 12 個月", 730: "顯示近 24 個月", 1825: "顯示近 5 年"}.get(
                floor, f"顯示近 {floor} 天")
        b = PD.bucket_start(b, chosen)       # the first bucket is a whole one
    if chosen != default:
        ch = PD.with_period(ch, chosen)
    return ch, b, {"x_period": chosen, "period_default": default, "period_toggle": toggle,
                   "buckets": PD.buckets(b, e, chosen), "range_note": note}


Z5GATE_MAX_DAYS = 365       # the replay is day by day: at most a year back from the range's end


def z5_progress(ds: Dataset) -> Optional[dict]:
    """Today's 3-step tracker and the 「還缺什麼」 line: quality_gate.z5_card on the
    status gate — the very object the 總覽 card (GET /overview/z5) shows, so the
    chart and the card agree. None when the status can't be computed."""
    import math
    from backend.api.overview import _status
    from backend.engine import quality_gate as QG
    from backend.engine.overview import day_to_date
    try:
        today = day_to_date(int(math.floor(ds.today)))
        st = _status(ds, today)
        gate = next((i.extra for i in st.indicators if i.id == "gate"), None) or {}
        c = QG.z5_card(gate, today)
    except Exception:                          # noqa: BLE001 — the history still draws
        return None
    return {"today": today.isoformat(), **{k: c.get(k) for k in (
        "state", "label", "headline", "steps", "next", "base", "z3", "keep", "reentry", "open", "flow")}}


def z5gate_panel(ch: dict, ds: Dataset, b: float, e: float, prefs=None) -> dict:
    """The 5 區開放流程 panel: quality_gate.z5_history over the selected range
    (capped at a year), with the 間歇門檻 preference the planner uses."""
    import math
    from backend.engine import quality_gate as QG
    from backend.engine.overview import day_to_date
    if prefs is None:
        from backend.engine import plan_prefs as PP
        prefs = PP.load()
    end = min(day_to_date(int(math.floor(e))), day_to_date(int(math.floor(ds.today))))
    begin = max(day_to_date(int(math.floor(b))), end - dt.timedelta(days=Z5GATE_MAX_DAYS))
    plan = getattr(ds, "plan", None)
    if plan is None:
        from backend.engine.planning import Plan
        plan = Plan.load()
    h = QG.z5_history(ds, plan, begin, end, prefs)
    h["progress"] = z5_progress(ds)
    return {"title": ch.get("title"), "description": ch.get("description"), "kind": "z5gate", "z5": h,
            "range_note": f"重播 {begin.isoformat()} 起（最多 1 年）" if begin > day_to_date(int(math.floor(b))) else None}


def _render(ch: dict, ds: Dataset, b: float, e: float, sports: Optional[str], w,
            params: Optional[dict] = None) -> dict:
    if ch.get("kind") == "periodzones":
        # time in zone over a period: zkind / zmodel / zsports / zperiod (/ zbegin, zend) / zgroup
        # come from the query (all part of the render-cache key); the RHE sport filter is not used
        from backend.engine.panels.period_zones import render as render_period_zones
        return render_period_zones(ds, ch, b, e, params or {})
    if ch.get("kind") == "climbvam":
        # steady-climb VAM:HR on trail runs and hikes; ?route=<route id> (part of the render-cache key)
        from backend.engine.panels.climb_vam import render as render_climb_vam
        return render_climb_vam(ds, ch, b, e, params or {})
    if ch.get("kind") == "polecompare":
        # 有杖 vs 沒杖 per grade bin (SP-243); the marks are in the key (chart())
        from backend.engine.panels.pole_compare import render as render_pole_compare
        return render_pole_compare(ds, ch, b, e, params or {}, today=today_local())
    if ch.get("kind") == "review":
        from backend.engine.workout_review import review
        return {**review(ds, w, ch.get("section") or "summary", basis=ch.get("basis_chosen") or "pace"),
                "title": ch.get("title"),
                "description": ch.get("description")}
    if ch.get("kind") == "activity":
        from backend.engine.panels.activity_charts import render as render_activity
        return render_activity(ds, w, ch)
    if ch.get("kind") == "workout":
        return render_chart(ch, ds, b, e, workout=w)
    if _panel_kind(ch) == "map":
        return render_map(ch, ds, w)
    if ch.get("kind") == "z5gate":
        return z5gate_panel(ch, ds, b, e)
    if ch.get("kind") in ("zones", "targets"):
        import math
        from backend.engine.zones import training_targets, zone_table
        end_day = int(math.floor(e))
        base = {"title": ch.get("title"), "description": ch.get("description"), "kind": ch["kind"]}
        if ch["kind"] == "zones":
            # an HR table can be switched in the card (?zsys=, remembered by the viewer)
            from backend.engine.zones import HR_SYSTEMS, SYSTEMS
            system = ch["system"]
            want = (params or {}).get("zsys")
            if want in HR_SYSTEMS and SYSTEMS[system]["unit"] == "bpm":
                system = want
            if system != ch["system"]:
                # the card's title names the model: a switched table shows its own title, not the
                # view's (fixed / translated) one; the view keeps it for the default model
                from backend.i18n import _
                base["title"] = _(SYSTEMS[system]["title"])
            return {**base, "zones": zone_table(ds, system, end_day, ch.get("days", 30))}
        from backend.engine.thresholds import estimate
        est = estimate(ds, today_local()) if not ds.config.parity else {}
        return {**base, "targets": training_targets(
            ds, end_day, (est.get("lthr") or {}).get("value"), (est.get("aethr") or {}).get("value"),
            (est.get("aethr") or {}).get("below"))}
    if ch.get("kind") != "athlete":
        raise HTTPException(400, f"unsupported panel {ch.get('class')}")
    return render_chart(ch, ds, b, e, keep=_kind_filter(ds, sports))


@router.get("/workouts")
def workouts(begin: Optional[str] = None, end: Optional[str] = None, sports: Optional[str] = None,
             parity: Optional[bool] = None):
    """RHE activity list for the selected range / activity kinds (newest
    first; `sports` = kinds, engine/sport_map.py). Each row carries its kind."""
    from backend.engine import activity_tags as AT
    from backend.engine import sport_map as SM
    ds = _dataset(parity)
    b, e = _range(ds, begin, end)
    tag_rows = AT.load()
    keep = SM.make_filter(sports, ds, tag_rows)
    kinds = SM.KindFilter(SM.FILTER_KINDS, ds, tag_rows)
    # the planned session each activity was matched to (engine/plan_match.py): one cached query
    try:
        from backend.engine.plan_store import done_by_index
        plan = done_by_index() if not ds.config.parity else {}
    except Exception:                       # noqa: BLE001 — the list never breaks on the plan
        plan = {}
    out = []
    for w in reversed(ds.workouts):
        if not (b <= w.day < e + 1) or (keep is not None and not keep(w)):
            continue
        m = w.metrics
        out.append({
            "index": w.idx, "start": w.entry.start.isoformat(), "sport": w.sport, "kind": kinds.kind(w),
            # the 活動編輯 page's title (activity_tags.name), else null
            "name": AT.name_of(AT.find(tag_rows, w.entry.start, w.entry.file)) if tag_rows else None,
            "key": AT.key_of(w.entry.start),
            "sport_type": w.sport_type, "file": w.entry.file, "tags": w.tags,
            "duration": m.get("duration"), "distance": m.get("distance"),
            "climbing": m.get("climbing"), "tss": m.get("tss"), "if": m.get("if"),
            "hrtss": m.get("hrtss"), "np": m.get("np"),
            "plan": plan.get(w.idx),        # {kind, label, icon, title, day} or None
            # stryd / watch / none (engine/power_source.py); watch power is
            # 「手錶推估功率（未採用）」 unless power.accept_watch_power
            "power_source": ds.power_source(w) if hasattr(ds, "power_source") else None,
            "power_label": ds.power_label(w) if hasattr(ds, "power_label") else None,
            # the branch Dataset._metrics took: power / rtss / trainingpeaks / hrtss
            "tss_source": m.get("tss_source"),
            # power TSS: the FTP it divided by and where it came from (Dataset.tss_ftp),
            # e.g. 「你的測試 2026-09-30」 / 「推估：Stryd PD 模型 mFTP（…）」 / WKO5 設定
            "ftp_used": m.get("ftp_used"), "ftp_source": m.get("ftp_source"),
        })
    # bad activity files (engine/bad_activity.py) are in no model, but stay in
    # the list, marked 「已排除：…」, without an index (nothing reads them)
    for x in getattr(ds, "excluded", []):
        start = dt.datetime.fromisoformat(x["start"])
        day = date_to_day(start)
        xw = SimpleNamespace(sport=x["sport"], sport_type=x["sport_type"], tags=[],
                             entry=SimpleNamespace(start=start, file=x["file"]))
        if not (b <= day < e + 1) or (keep is not None and not keep(xw)):
            continue
        out.append({"index": None, "start": x["start"], "sport": x["sport"], "kind": kinds.kind(xw),
                    "sport_type": x["sport_type"],
                    "file": x["file"], "tags": [], "duration": x.get("duration"), "distance": x.get("distance"),
                    "climbing": None, "tss": None, "if": None, "hrtss": None, "np": None,
                    "power_source": None, "power_label": None, "tss_source": None,
                    "ftp_used": None, "ftp_source": None,
                    "excluded": _exclusion_json(x)})
    out.sort(key=lambda a: a["start"], reverse=True)
    return out


def _exclusion_json(x: dict) -> dict:
    return {k: x.get(k) for k in ("key", "file", "label", "reason", "rule", "auto", "manual", "override",
                                  "avg_kmh", "distance", "duration", "start", "sport_type")}


@router.get("/exclusions")
def exclusions():
    """Bad activity files of the current source (設定 → 資料校正):
    `excluded` (left out of every model, with the reason) and `kept` (the
    rule flags them, the user said 這筆是正常的). `enabled` =
    activities.exclude_bad."""
    from backend.engine import bad_activity as BA
    ds = _dataset(parity=False)
    return {"enabled": bool(getattr(ds, "exclude_bad", False)), "setting": BA.SETTING_KEY,
            "source": getattr(ds, "source", None) or "wko5",
            "excluded": [_exclusion_json(x) for x in reversed(getattr(ds, "excluded", []))],
            "kept": [_exclusion_json(x) for x in reversed(getattr(ds, "exclusion_kept", []))]}


class ExclusionBody(BaseModel):
    key: str                      # the activity's local start minute (activity_tags key)
    file: Optional[str] = None
    exclusion: Optional[str] = None   # keep | exclude | null (the auto rule)


@router.put("/exclusions")
async def put_exclusion(body: ExclusionBody):
    """Override the bad-file rule of one activity, listed or not: "keep"
    (這筆是正常的，不要排除), "exclude" (手動排除) or null (back to the rule).
    Stored with the activity tags (start minute + file), so it applies
    whatever the data source; the datasets rebuild (source_stamp)."""
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    from backend.db.database import AsyncSessionLocal
    from backend.engine import activity_tags as AT
    try:
        start = dt.datetime.fromisoformat(body.key)
    except ValueError:
        raise HTTPException(400, "INVALID_KEY")
    cur = AT.find(AT.load(), start, body.file)
    key = (cur or {}).get("start_local") or AT.key_of(start)
    async with AsyncSessionLocal() as db:
        await save_activity_tag(db, ActivityUpdate(exclusion=body.exclusion), start_local=key,
                                source=DSRC.current_source(), file=body.file)
    return {"key": key, "exclusion": body.exclusion}


@router.get("/workouts/{i}/review")
def workout_review(i: int, section: Optional[str] = None, parity: Optional[bool] = None,
                   basis: str = "pace"):
    """Single-activity review (backend/engine/workout_review.py): one section's
    card, or all six dashboards' cards plus the classification. `basis`
    (pace / power) picks Pa:HR or Pw:HR on the aerobic card."""
    from backend.engine import workout_review as WR
    ds = _dataset(parity)
    if not 0 <= i < len(ds.workouts):
        raise HTTPException(404, "workout not found")
    w = ds.workouts[i]
    if basis not in BS.BASES:
        raise HTTPException(400, f"basis must be one of {list(BS.BASES)}")
    if section:
        if section not in WR.SECTIONS + WR.EXTRA_SECTIONS:
            raise HTTPException(400, f"section must be one of {list(WR.SECTIONS + WR.EXTRA_SECTIONS)}")
        return WR.review(ds, w, section, basis=basis)
    cards = {s: WR.review(ds, w, s, basis=basis) for s in WR.SECTIONS}
    head = cards["summary"]
    return {"workout": i, "classification": head.get("classification"),
            "suggested_dashboard": head.get("suggested_dashboard"), "sections": cards}


def _activity_json(ds, w) -> dict:
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import athlete as A
    from backend.engine import power_source as PS
    t = A.auto_tags(ds, w)
    src = A.power_source(ds, w)
    kept = next((x for x in getattr(ds, "exclusion_kept", []) if x["file"] == w.entry.file), None)
    title = getattr(w.entry, "title", "") or ""
    return {"workout": w.idx, "key": AT.key_of(w.entry.start), "file": w.entry.file, "label": A.label(w),
            "title_original": title or A.label(w), "title_from": "activity" if title else "label",
            "terrain": _terrain(ds, w.entry.file, A.is_trail(w)),
            "origin": _origin(ds, w),
            "types": AT.TYPES, "efforts": AT.EFFORTS, **t,
            # 登山杖: the user's choice, else 有杖 「依賽事設定」 (SP-300)
            **AT.pole_state(t.get("tags"), AT.race_poles(ds).get(w.idx)),
            # 「要標成濕路嗎？」 applies to 越野跑 / 登山健行 only (SP-299): a type change moves it in / out
            "rain_kind": AT.rain_kind(w, AT.user_of(w)),
            # bad activity files (engine/bad_activity.py): an excluded file is not
            # in ds.workouts; `flagged` = the rule's reason when the user kept it
            "exclusion_state": {"override": t.get("exclusion"), "flagged": kept["reason"] if kept else None,
                                "enabled": bool(getattr(ds, "exclude_bad", False))},
            "power": {"source": src, "used": A.power_ok(ds, w) if src != PS.NONE else False,
                      "label": PS.label(src, bool(getattr(ds, "accept_watch_power", True))),
                      "setting": PS.SETTING_KEY},
            # the pack carried (racepower athlete.activity_pack; racepower_hike_meta.json)
            "pack": _pack_json(w),
            # 疼痛 (engine/injuries.py): the mark, its event and the re-entry prompt; hidden in the demo mode
            "pain_state": _pain_state(ds, w, t)}


def _pain_part(u: Optional[dict]) -> dict:
    """The pain mark of a stored tag (the 活動編輯 list); nothing in the demo mode."""
    from backend.engine import injuries as INJ
    if INJ.demo_mode():
        return {}
    return {"pain": (u or {}).get("pain"), "pain_area": (u or {}).get("pain_area"),
            "injury_id": (u or {}).get("injury_id"), "pain_score": (u or {}).get("pain_score")}


def _pain_state(ds, w, t: dict) -> Optional[dict]:
    from backend.engine import injuries as INJ
    from backend.engine import activity_tags as AT
    if INJ.demo_mode():
        return None
    t = AT.user_of(w) or {}                 # the stored mark (auto_tags may be memoised)
    try:
        today = today_local()
        evs = INJ.load_events()
        ev = next((e for e in evs if e["id"] == t.get("injury_id")), None) if t.get("injury_id") else None
        from backend.engine import reentry as RE
        from backend.engine import workout_review as WR
        day = WR._wdate(w)
        # the event the pain-monitoring text follows (傷別, SP-269): the mark's own, else an injury of the
        # mark's area open that day; none = the general return-to-run rules
        mev = ev or next((e for e in INJ.active_on(evs, day) if not INJ.is_illness(e) and t.get("pain_area")
                          and e.get("area") == t.get("pain_area")), None)
        mon = INJ.monitor(mev)
        out = {"pain": t.get("pain"), "pain_area": t.get("pain_area"), "pain_score": t.get("pain_score"),
               "area_label": INJ.area_label(t.get("pain_area")) if t.get("pain_area") else None,
               "injury": INJ.summary(ev, today), "reentry": None,
               "monitor": mon["text"] + "\n" + mon["disclaimer"] if mev is not None else None}
        # inside a re-entry block (engine/reentry.py): 「記一下有沒有痛」 (plan §4.3)
        if w.sport == "run" and (today - day).days <= 120:
            rp = RE.find(ds, day)
            if rp and RE.in_block(rp, day):
                inj = (rp.get("injury") or {}).get("id")
                rev = mev or next((e for e in evs if inj is not None and e.get("id") == inj), None)
                rm = INJ.monitor(rev)
                out["reentry"] = {"text": rp.get("text"), "monitor": rm["text"] + "\n" + rm["disclaimer"]}
        return out
    except Exception:                       # noqa: BLE001 — never breaks the activity card
        return {"pain": t.get("pain"), "pain_area": t.get("pain_area"), "injury": None, "reentry": None}


def _pack_json(w) -> Optional[dict]:
    try:
        from backend.engine.racepower import athlete as RA
        return RA.activity_pack(w)
    except Exception:                       # noqa: BLE001 — never breaks the activity card
        return None


@router.get("/workouts/{i}/activity")
def get_activity(i: int):
    """Activity tags of one dataset workout (engine/activity_tags.py): the
    effective activity type / effort, the auto values with their reasons,
    the user's overrides and note."""
    ds = _dataset()
    if not 0 <= i < len(ds.workouts):
        raise HTTPException(404, "workout not found")
    return _activity_json(ds, ds.workouts[i])


@router.get("/workouts/{i}/kind")
def get_kind(i: int, parity: Optional[bool] = None):
    """The activity type of one dataset workout as the viewer's filter reads it
    (engine/sport_map.kind_of: the user's 爬山 / 百岳跟團 mark, the trail / road
    classification with its override, the platform code) and `chart_sport`: which
    single-activity charts apply to it (views' "sports" tag; SP-218 — the activity
    itself decides, not 主要訓練項目). 百岳 is not told apart from 登山健行 here
    (both are "trail"), so the GPS summit lookup is skipped."""
    from backend.engine import activity_tags as AT
    from backend.engine import sport_map as SM
    ds = _dataset(parity)
    if not 0 <= i < len(ds.workouts):
        raise HTTPException(404, "workout not found")
    kind = SM.KindFilter(SM.RUN_TYPES, ds, AT.load()).kind(ds.workouts[i])
    return {"workout": i, "kind": kind, "chart_sport": SM.chart_sport(kind)}


@router.get("/workouts/{i}/pain")
def get_pain(i: int):
    """The 疼痛 mark of one dataset workout (the chart page's chip; light: no
    auto tags). 404 in the demo mode (傷病紀錄 hidden)."""
    from backend.engine import injuries as INJ
    if INJ.demo_mode():
        raise HTTPException(404, "Not Found")
    ds = _dataset()
    if not 0 <= i < len(ds.workouts):
        raise HTTPException(404, "workout not found")
    w = ds.workouts[i]
    return {"workout": i, "sport": w.sport, **(_pain_state(ds, w, {}) or {})}


@router.patch("/workouts/{i}/activity")
async def patch_activity(i: int, body: dict):
    """Set / clear the user's activity type, effort or note (a key present
    with null = back to auto). Keyed by the activity's local start minute
    and file, so it applies whatever the data source."""
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    from backend.db.database import AsyncSessionLocal
    from starlette.concurrency import run_in_threadpool
    from backend.engine import activity_tags as AT
    ds = await run_in_threadpool(_dataset)        # never build on the event loop
    if not 0 <= i < len(ds.workouts):
        raise HTTPException(404, "workout not found")
    w = ds.workouts[i]
    from backend.api.workouts import TAG_FIELDS
    if "pack_kg" in body:
        # the pack carried (the 百岳 prediction's per-trip pack): racepower_hike_meta.json, null = cleared
        await run_in_threadpool(_set_pack, w, body.get("pack_kg"))
    tag_keys = {k: v for k, v in body.items() if k in TAG_FIELDS}
    if tag_keys or "pack_kg" not in body:
        upd = ActivityUpdate(**tag_keys)
        from backend.engine.wko5expr import datasource as DSRC
        cur = AT.user_of(w)                     # an existing tag (maybe set from another source ±3 min)
        key = (cur or {}).get("start_local") or AT.key_of(w.entry.start)
        async with AsyncSessionLocal() as db:
            await save_activity_tag(db, upd, start_local=key, source=DSRC.current_source(),
                                    file=w.entry.file, distance_km=w.metrics.get("distance"),
                                    label=f"{w.entry.start:%Y-%m-%d} {w.sport_type}")
    return await run_in_threadpool(_activity_json, ds, w)


def _set_pack(w, kg) -> None:
    from backend.engine.racepower import athlete as A
    try:
        A.set_hike_meta(w.entry.file, None if kg in (None, "") else float(kg), start=w.entry.start)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e) or "背負要在 0–40 kg")
    try:                                     # the race-power walking model reads the packs too
        from backend.api import racepower as RP
        with RP._lock:
            RP._cache.pop("grade", None)
    except Exception:                       # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 活動編輯 page (static/activity.html): every activity with its stored user
# values, the auto values in a second (slower) call, key-based and bulk edits
# ---------------------------------------------------------------------------

ORIGIN_LABELS = {"coros": "COROS", "tp": "TrainingPeaks", "wko5": "WKO5"}


def _origin(ds, w=None, file: Optional[str] = None) -> Optional[str]:
    """Which source an activity's file came from (the 活動編輯 badge): coros /
    tp on a FIT dataset (the 資料來源's folder), wko5 on the WKO5 dataset."""
    if w is not None and hasattr(ds, "file_origin"):
        return ds.file_origin(w)
    src = getattr(ds, "source", None) or "wko5"
    return src if src in ORIGIN_LABELS else None


def _terrain(ds, file: Optional[str], trail: bool) -> dict:
    """Road / trail of one activity and whether it can be changed here: a
    COROS / TP FIT has a workout_files row (PATCH /api/v1/workouts/{id}/
    classification, the existing override); the WKO5 source has none — its
    terrain is the WKO5 workout type."""
    from backend.engine.wko5expr import fitdataset as FD
    rows = getattr(ds, "_classes", None) or {}
    d = getattr(ds, "dir", None)
    r = None
    if rows and d is not None and file:
        r = rows.get(FD._norm(Path(d) / file))
        if r is None:
            cand = rows.get("_by_name", {}).get(Path(str(file)).name) or []
            r = cand[0] if len(cand) == 1 else None
    if r is None:
        return {"value": "trail" if trail else "road", "editable": False, "overridden": False,
                "workout_file_id": None, "why": "WKO5 來源：地形依 WKO5 的活動類型" if not rows else "找不到這個檔案的資料庫紀錄"}
    eff = FD.classification_for(Path(d) / file, rows)
    return {"value": eff or ("trail" if trail else "road"), "editable": True,
            "overridden": bool(r["classification_overridden"]), "workout_file_id": r["id"],
            "stored": r["trail_classification"], "why": None}


@router.get("/activities")
def activities_list():
    """Every activity of the current data source (newest first) with the
    stored user values (activity_tags: type / effort marks, name, tags,
    note, exclusion), the terrain and the power source. Excluded bad files
    are included (index null). The auto values: GET /activities/auto."""
    from backend.engine import activity_tags as AT
    from backend.engine.panels import pole_compare as PC
    from backend.engine.racepower import athlete as A
    ds = _dataset()
    tags = AT.load()
    rec = AT.load_recorded()
    out = []
    # 「這次活動期間下過雨（N mm），要標成濕路嗎？」 (SP-299): the archive rain while it ran
    # (route_weather.activity_rain, activity_weather.json); the page shows the hint only while
    # the 路況 is 未標 and the rain reached rain_hint_mm — a hint, never a mark
    try:
        from backend.engine import route_weather as RW
        rain = RW.rain_by_activity()
    except Exception:                       # noqa: BLE001 — no weather file: no hint
        rain = {}

    def rain_mm(start, file):
        r = rain.find(file, start) if rain else None
        return r["rain_mm"] if r else None

    # 「依賽事設定」 (SP-300): an activity of a race marked 「會用登山杖」 shows 有杖 until the user chooses
    race_pole = AT.race_poles(ds)

    def user_part(u, race=None):
        return {"name": AT.name_of(u), "tags": AT.tags_of(u), **AT.pole_state(AT.tags_of(u), race),
                "surface": AT.surface_of(AT.tags_of(u)),
                "note": (u or {}).get("note"),
                "user_type": AT.user_type(u), "user_effort": AT.user_effort(u),
                "user_exclusion": AT.user_exclusion(u), **_pain_part(u)}

    def rpe_part(start, file):
        # the watch's post-workout RPE / feel (FIT session workout_rpe / workout_feel), read only
        r = AT.recorded_of(rec, start, file) or {}
        from backend.engine import coros_rpe as CR
        # + 「自評：Hard（COROS）」 when the RPE is COROS's post-run rating (SP-231)
        return {"rpe": r.get("rpe"), "feel": r.get("feel"), "self_rating": CR.self_rating(r) if r else None}

    for w in ds.workouts:
        u = AT.find(tags, w.entry.start, w.entry.file)
        m = w.metrics
        title = getattr(w.entry, "title", "") or ""
        out.append({"index": w.idx, "key": AT.key_of(w.entry.start), "start": w.entry.start.isoformat(),
                    "file": w.entry.file, "sport": w.sport, "sport_type": w.sport_type,
                    "title_original": title or A.label(w), "label": A.label(w),
                    "duration": m.get("duration"), "distance": m.get("distance"), "climbing": m.get("climbing"),
                    "tss": m.get("tss"), "trail": A.is_trail(w),
                    "terrain": _terrain(ds, w.entry.file, A.is_trail(w)),
                    "power_label": ds.power_label(w) if hasattr(ds, "power_label") else None,
                    "origin": _origin(ds, w), "excluded": None, **rpe_part(w.entry.start, w.entry.file),
                    # its 有杖 / 沒杖 mark counts toward the comparison (trail runs and hikes, SP-243)
                    "pole_chart": PC.used(w), "rain_mm": rain_mm(w.entry.start, w.entry.file),
                    # the rain hint is for 越野跑 / 登山健行 only (SP-299, owner 2026-10-07)
                    "rain_kind": AT.rain_kind(w, u),
                    **user_part(u, race_pole.get(w.idx))})
    for x in getattr(ds, "excluded", []):
        start = dt.datetime.fromisoformat(x["start"])
        u = AT.find(tags, start, x["file"])
        km = x.get("distance")
        lab = f"{start:%Y-%m-%d} {A.SPORT_ZH.get(x['sport_type'], x['sport_type'])}" + (f" {km:.1f} km" if km else "")
        terr = _terrain(ds, x["file"], x["sport_type"] == "trail running")
        # the rain hint's kind without the user's type (the page applies a type change itself)
        kind_auto = AT.rain_kind_excluded(x["sport_type"], None, terr["value"] == "trail")
        out.append({"index": None, "key": x.get("key") or AT.key_of(start), "start": x["start"], "file": x["file"],
                    "sport": x["sport"], "sport_type": x["sport_type"], "title_original": lab, "label": lab,
                    "duration": x.get("duration"), "distance": km, "climbing": None, "tss": None,
                    "trail": x["sport_type"] == "trail running",
                    "terrain": terr,
                    "power_label": None, "origin": _origin(ds, file=x["file"]),
                    "excluded": _exclusion_json(x), **rpe_part(start, x["file"]), "pole_chart": False,
                    "rain_mm": rain_mm(start, x["file"]), "rain_kind_auto": kind_auto,
                    "rain_kind": AT.rain_kind_excluded(x["sport_type"], u, terr["value"] == "trail"),
                    **user_part(u)})
    out.sort(key=lambda a: a["start"], reverse=True)
    return {"source": getattr(ds, "source", None) or "wko5", "origin_labels": ORIGIN_LABELS,
            "types": AT.TYPES, "efforts": AT.EFFORTS, "pole_tags": AT.POLES, "pole_none_tag": AT.POLE_NONE_TAG,
            "surface_tags": AT.SURFACES,
            # 「再標 N 次就能比較」 beside the 登山杖 choice (SP-243): only the trail runs and
            # hikes the chart uses count
            "pole_compare": PC.counts(ds, tags, today_local()),
            "rain_hint_mm": AT.RAIN_HINT_MM if rain else None,
            "exclude_enabled": bool(getattr(ds, "exclude_bad", False)), "activities": out}


@router.get("/activities/auto")
async def activities_auto():
    """The AUTO activity type / effort (with reasons) of every activity:
    {state computing | ready | error, n_done, n_total, stale, auto: {key:
    values}}. Never waits for the computation (minutes on a cold cache):
    it runs once per Dataset in the background (api/activity_auto.py, kept
    on disk per file), and the page polls while state is computing — `auto`
    holds what is known so far."""
    from starlette.concurrency import run_in_threadpool
    from backend.api import activity_auto as AA

    def work():
        return AA.status(_dataset())
    return await run_in_threadpool(work)


def _averages(ds, w) -> dict:
    """{avg_hr, avg_power} of one workout: a FIT dataset's sample means
    (fitdataset.averages, cached per file); a WKO5 workout's stored fields
    (avg power = NP / vi 4222, avg HR = NP / ef 4247 — power files only)."""
    if hasattr(ds, "averages"):
        try:
            return ds.averages(w)
        except Exception:                   # noqa: BLE001 — a missing cache file: no numbers, not a 500
            return {"avg_hr": None, "avg_power": None}
    m = getattr(w.entry, "metrics", None) or {}
    np_, vi, ef = m.get(4219), m.get(4222), m.get(4247)
    return {"avg_hr": np_ / ef if np_ and ef else None, "avg_power": np_ / vi if np_ and vi else None}


@router.get("/activities/stats")
async def activities_stats():
    """{key: {avg_hr, avg_power}} of every dataset activity (the 活動列表
    columns). Asked after the list: the first call reads every activity's
    samples once; later calls come from the FIT cache."""
    from starlette.concurrency import run_in_threadpool
    from backend.engine import activity_tags as AT

    def work():
        ds = _dataset()
        out = {}
        for w in ds.workouts:
            a = _averages(ds, w)
            out[AT.key_of(w.entry.start)] = {k: None if a.get(k) is None else round(a[k], 1)
                                            for k in ("avg_hr", "avg_power")}
        if hasattr(ds, "save_cache"):
            ds.save_cache()
        return out
    return await run_in_threadpool(work)


class BulkItem(BaseModel):
    key: str
    file: Optional[str] = None


class BulkBody(BaseModel):
    """Edits applied to every item: a field present = set it (null = back to
    auto), absent = unchanged. `add_tags` / `remove_tags` change the tag list
    of each activity; `tags` replaces it; `name` is for single edits."""
    items: list[BulkItem]
    activity_type: Optional[str] = None
    effort: Optional[str] = None
    note: Optional[str] = None
    exclusion: Optional[str] = None
    name: Optional[str] = None
    tags: Optional[list[str]] = None
    add_tags: Optional[list[str]] = None
    remove_tags: Optional[list[str]] = None
    # 疼痛 (engine/injuries.py): bulk 「設為沒痛」 (0) or a single key-based mark
    pain: Optional[int] = None
    pain_area: Optional[str] = None
    pain_side: Optional[str] = None
    pain_score: Optional[int] = None        # SP-271: 0–10 跑的時候最痛幾分 (single edits)
    # 登山杖 (SP-242): "with" / "without" / "none" the user's 未標 (SP-300: keeps a race's
    # 「會用登山杖」 off) / null no choice (engine/activity_tags.POLES, POLE_NONE)
    poles: Optional[str] = None
    # 路況 (SP-250): "dry" / "wet" / null 未標 (engine/activity_tags.SURFACES)
    surface: Optional[str] = None


BULK_MAX = 500


@router.patch("/activities")
async def patch_activities(body: BulkBody):
    """Key-based edit of one or more activities (the 活動編輯 page; an
    excluded file has no dataset index, so it is edited by key). Each item is
    stored under its existing tag key (another source ±3 min) or its own."""
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    from backend.db.database import AsyncSessionLocal
    from backend.engine import activity_tags as AT
    if not body.items:
        raise HTTPException(400, "NO_ITEMS")
    if len(body.items) > BULK_MAX:
        raise HTTPException(400, "TOO_MANY_ITEMS")
    sent = body.model_fields_set
    base = {k: getattr(body, k) for k in ("activity_type", "effort", "note", "exclusion", "name", "tags",
                                          "pain", "pain_area", "pain_side", "pain_score", "poles", "surface")
            if k in sent}
    for k in ("add_tags", "remove_tags"):
        v = getattr(body, k)
        if v is not None and AT.validate(tags=v):
            raise HTTPException(400, "INVALID_TAGS")
    rows = AT.load()
    src = DSRC.current_source()
    saved = []
    async with AsyncSessionLocal() as db:
        for it in body.items:
            try:
                start = dt.datetime.fromisoformat(it.key)
            except ValueError:
                raise HTTPException(400, "INVALID_KEY")
            cur = AT.find(rows, start, it.file)
            key = (cur or {}).get("start_local") or AT.key_of(start)
            patch = dict(base)
            if body.add_tags or body.remove_tags:
                have = patch.get("tags", AT.tags_of(cur))
                drop = {t.strip().lower() for t in body.remove_tags or []}
                patch["tags"] = [t for t in AT.clean_tags(list(have) + list(body.add_tags or []))
                                 if t.lower() not in drop]
            if not patch:
                continue
            await save_activity_tag(db, ActivityUpdate(**patch), start_local=key, source=src, file=it.file)
            saved.append(key)
    return {"saved": saved, "n": len(saved)}


@router.get("/activities/page", include_in_schema=False)
def activities_page():
    return render_page("activity")


@router.get("/sports")
def sports_list():
    """The activity kinds present, with counts, in the filter's order (the
    圖表分析 activity-type filter, engine/sport_map.py, SP-263): 越野跑 / 路跑 /
    登山健行 / 百岳登山 / 騎車 / 肌力 / 走路 / 其他. `sport` = the kind key
    the `sports` query takes."""
    from backend.engine import sport_map as SM
    from backend.i18n import _
    ds = _dataset()
    n = SM.counts(ds.workouts, ds)
    return [{"sport": k, "label": _(label), "count": n[k]} for k, label in SM.FILTER_KINDS.items() if n.get(k)]


@router.get("/athlete")
def athlete_summary(parity: Optional[bool] = None):
    ds = _dataset(parity)
    a = ds.athlete
    return {
        "name": " ".join(x for x in (a.first_name, a.last_name) if x),     # "" without a WKO5 athlete file
        "workouts": len(ds.workouts),
        "first": ds.workouts[0].entry.start.isoformat() if ds.workouts else None,
        "last": ds.workouts[-1].entry.start.isoformat() if ds.workouts else None,
        "ctlconstant": a.ctlconstant, "atlconstant": a.atlconstant,
        "wko5_pmc_snapshot": a.pmc_snapshot,
        "settings": {k: [[d.isoformat(), val] for d, val in v] for k, v in a.settings.items()},
    }


@router.get("/primary-sport")
def primary_sport():
    """主要訓練項目 (engine/primary_sport.py): the setting (auto | trail | road), the sport in
    effect and the suggestion from the data / the A and B races (SP-245; shown next to the control)."""
    from backend.engine import primary_sport as PS
    import math
    from backend.engine.planning import Plan
    from backend.files.wko5_athlete import day_to_date
    ds = _dataset()
    plan = Plan.load()                 # the season plan's events (ds.plan is empty in parity mode)
    return PS.resolve(ds, plan.events, day_to_date(int(math.floor(ds.today))))


# ---------------------------------------------------------------------------
# engine config
# ---------------------------------------------------------------------------

@router.get("/config")
def get_config():
    cfg = EngineConfig.load()
    return {"config": cfg.to_dict(), "path": str(config_path()),
            "mountain_preset": MOUNTAIN_PRESET.to_dict()}


@router.put("/config")
def put_config(body: dict):
    """Persist the engine config. parity=True reproduces WKO5 exactly."""
    cfg = EngineConfig.from_dict({**EngineConfig.load().to_dict(), **body})
    cfg.save()
    _dataset_cfg.cache_clear()
    return {"config": cfg.to_dict()}


# ---------------------------------------------------------------------------
# data corrections — proposed automatically, applied only on approval
# ---------------------------------------------------------------------------

@router.get("/corrections")
def list_corrections():
    store = CorrectionStore()
    return {"applied": [asdict(c) for c in store.items], "path": str(store.path)}


@router.get("/corrections/proposals")
def correction_proposals(channel: str = "power", factor: float = 1.6,
                         percentile: float = 90.0):
    """Detect bad samples. This ONLY proposes; nothing is changed."""
    ds = _dataset(parity=False)
    applied = {(c.file, c.channel, c.t_start, c.t_end) for c in CorrectionStore().items}
    out = [p for p in detect_spikes(ds, channel=channel, factor=factor, percentile=percentile)
           if (p["file"], p["channel"], p["t_start"], p["t_end"]) not in applied]
    return {"proposals": out, "channel": channel, "factor": factor, "percentile": percentile}


class ApproveBody(BaseModel):
    proposals: list[dict]


@router.post("/corrections/approve")
def approve_corrections(body: ApproveBody):
    """Apply exactly the proposals passed in — the approval step."""
    store = CorrectionStore()
    added = store.add(proposals_to_corrections(body.proposals))
    _dataset_cfg.cache_clear()
    return {"added": [asdict(c) for c in added], "total": len(store.items)}


@router.delete("/corrections/{correction_id}")
def undo_correction(correction_id: str):
    store = CorrectionStore()
    if not store.remove(correction_id):
        raise HTTPException(404, "correction not found")
    _dataset_cfg.cache_clear()
    return {"removed": correction_id, "total": len(store.items)}


# ---------------------------------------------------------------------------
# workout samples — one shared index for the route map and synced hover
# ---------------------------------------------------------------------------

@router.get("/workouts/{idx}/samples")
def workout_samples(idx: int, parity: Optional[bool] = None):
    """Per-sample elapsed time (s), distance (km), lat/lng, elevation (m),
    HR, power and grade (%) of one workout, downsampled once. Every array has
    the same length and the same step as the workout charts' points
    (render._downsample), so the viewer maps a chart's x (time or distance)
    and a map position to one sample index. NaN / no GPS -> null."""
    import math
    import numpy as np
    from backend.engine.wko5expr.evaluator import Evaluator
    from backend.engine.wko5expr.render import MAX_POINTS, _f

    ds = _dataset(parity)
    if not 0 <= idx < len(ds.workouts):
        raise HTTPException(404, "workout not found")
    w = ds.workouts[idx]
    t = ds.channel(idx, "elapsedtime")
    n = 0 if t is None else len(t)
    step = max(1, int(math.ceil(n / MAX_POINTS))) if n else 1

    def col(a, nd: int):
        if a is None or len(a) != n:
            return None
        out = [_f(v) for v in a[::step]]
        return [None if v is None else round(v, nd) for v in out]

    grade = None
    if n:
        try:
            g = Evaluator(ds, w.day, w.day).evaluate("rgrade", workout=w)
            if isinstance(g, np.ndarray):
                grade = g * 100.0
        except Exception:   # noqa: BLE001 — grade is optional colouring data
            grade = None
    lat, lng = col(ds.channel(idx, "latitude"), 6), col(ds.channel(idx, "longitude"), 6)
    # the elevation the review cards use (workout_review._samples): the file's WKO5-smoothed
    # _elevation when it has one, else the raw channel — the map's readout matches the climb profile
    elev = ds.channel(idx, "_elevation")
    if elev is None or not np.isfinite(np.asarray(elev, dtype=float)).any():
        elev = ds.channel(idx, "elevation")
    if lat is not None and lng is not None:
        # (0, 0) is a device's "no fix", not a position
        for i, (a, b) in enumerate(zip(lat, lng)):
            if a is None or b is None or (a == 0 and b == 0):
                lat[i] = lng[i] = None
    return {
        "workout": idx, "step": step, "n": len(range(0, n, step)) if n else 0,
        "t": col(t, 1) or [], "d": col(ds.channel(idx, "elapseddistance"), 4),
        "lat": lat, "lng": lng, "elev": col(elev, 1),
        "hr": col(ds.channel(idx, "heartrate"), 0), "power": col(ds.channel(idx, "power"), 0),
        "grade": col(grade, 1),
    }


@router.get("/viewer", include_in_schema=False)
def viewer():
    return render_page("wko5_viewer")


@router.get("/settings", include_in_schema=False)
def settings_page():
    if tenancy.demo_mode():          # the demo's settings are fixed (they shape the shared data)
        raise HTTPException(404, "page not found")
    return render_page("settings")
