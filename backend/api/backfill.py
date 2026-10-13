"""
The back-fill of the older activities a hot-first build left out (SP-394 第 4 條;
docs/research/cache-tiering.md §7.1, §7.4).

A first import — a new user's first COROS / Garmin sync, a big manual FIT import (SP-323) — or a
re-parse after the FIT parse version changed has hundreds of files to parse (the owner's 3 years:
~800 files, ~850 CPU s on the NAS) and then the as-of estimates over all of them (~23 CPU min).
The build parses only the last 90 days (FitFolderDataset hot_first: fitdataset.HOT_FIRST_FROM or
more files to parse), and the race-power reads leave the other files out too (cptest.hold), so
the overview and the 課表 are ready in about the time of a sync, every number of that Dataset
from the same activities. The rest, here:

  1. chunks of CHUNK_FILES files, newest first, each a `full` job (the parse uses the process
     pool; then the same files' race-power reads, cptest.read_ahead, and they are released): the
     warm tier's (to 365 days) at `warm`, the cold tier's at `cold` (backend/jobs.py tier_of) —
     other tenants' hot work (a sync's warm-up, `sync_hot`) goes between two chunks, and within a
     priority the tenants take turns;
  2. then the complete Dataset — `build` at `cold`: one of the class's places at most, so a
     request always finds the other, and never the warm-ups' `full` place for the length of the
     full-history estimates — replaces the hot-first one in the Dataset cache
     (wko5views._DatasetCache.put; a request meanwhile keeps the hot-first one), and a warm-up
     (reason `backfill`, at `warm`) computes the overview status, the plan inputs and the
     activity classification on it.

What a page shows of a hot-first Dataset (owner 2026-10-13: CTL must never be wrong — it waits
for the whole history): a chart or a number is drawn only when every day it reads (chartscope's
scope: the range + 6 time constants of warm-up for CTL / ATL / TSB, the estimates' look-back …;
api/overview.py's table for the status) is inside the hot-first Dataset (`coverage(ds)`: the days
after its newest deferred file) — so CTL / ATL / TSB, a 365-day chart, an estimate wait for the
complete Dataset, while what reads the last 90 days only is shown at once; else `pending(ds, …)`:
{n_done, n_total, message} 「計算中（較舊的活動補算中 n／N）」. The automatic plan waits for the
complete Dataset (`when_complete`).

A chunk that fails leaves its files to the complete build (it parses whatever is still unparsed).
Progress per tenant: `progress()` — GET /api/v1/wko5/dataset/status's `backfill`, shown by
shell.js under the nav. In-process only: after a restart the next build is hot-first again and
starts a new back-fill of what is still unparsed.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# 推估: files per chunk job — ~30 s of parsing on the NAS (a median file 0.64 s, 2 pool workers;
# SP-320 §1.2), so another tenant's hot work waits at most about that long for the `full` place
CHUNK_FILES = 50

_STATE: dict = {}                   # (tenant id, FIT folder) -> Backfill
_LOCK = threading.Lock()


class Backfill:
    """One tenant's back-fill of one FIT folder."""

    def __init__(self, tid: str, folder: Path, today: dt.date):
        self.tid, self.folder, self.today = tid, folder, today
        self.todo: list = []                 # [(date, path)] newest first
        self.seen: set = set()               # every path ever queued here
        self.n_total = 0
        self.n_done = 0
        self.state = "running"               # running | done | error
        self.step: Optional[str] = "parse"   # parse | dataset
        self.store = None                    # the newest hot-first Dataset's FitStore (its index in memory)
        self.keys: set = set()               # the Dataset cache keys built hot first
        self.job = None                      # the jobs.Job queued / running now
        self.waiting: list = []              # when_complete callbacks
        self.started = time.monotonic()
        self.error: Optional[str] = None
        self.lock = threading.Lock()

    def add(self, ds, key: Optional[tuple]) -> None:
        with self.lock:
            new = [x for x in ds.deferred if str(x[1]) not in self.seen]
            self.seen.update(str(p) for _, p in new)
            self.todo = sorted(self.todo + new, key=lambda x: (x[0], str(x[1])), reverse=True)
            self.n_total += len(new)
            self.store = ds._store
            if key is not None:
                self.keys.add(tuple(key))

    def view(self) -> dict:
        with self.lock:
            return {"state": self.state, "step": self.step, "n_done": self.n_done, "n_total": self.n_total,
                    "elapsed_s": round(time.monotonic() - self.started, 1), "error": self.error}


def _today(ds) -> dt.date:
    from backend.engine.wko5expr.dataset import day_to_date
    t = getattr(ds, "today", None)
    return dt.date.today() if t is None else day_to_date(t)


def _tid() -> str:
    from backend import tenancy
    try:
        return tenancy.base_of().id
    except Exception:                        # noqa: BLE001 — a demo instance without its data
        return "default"


def schedule(ds, key: Optional[tuple] = None) -> Optional[Backfill]:
    """After a hot-first build (wko5views._build_dataset, in its tenant): back-fill its deferred
    files; a back-fill of that folder still running takes them in (a sync built hot first again
    meanwhile). `key`: the Dataset cache key it was built for (replaced by the complete one)."""
    if not getattr(ds, "deferred", None):
        return None
    k = (_tid(), str(ds.dir))
    with _LOCK:
        bf = _STATE.get(k)
        start = bf is None or bf.state != "running"
        if start:
            bf = _STATE[k] = Backfill(k[0], Path(ds.dir), _today(ds))
    bf.add(ds, key)
    if start:
        log.info("back-fill of %d older activities started (hot first)", bf.n_total)
        _next(bf)
    return bf


def _next(bf: Backfill) -> None:
    """Queue the next step: a chunk of the newest files left, else the complete Dataset."""
    from backend import jobs
    try:
        with bf.lock:
            part = bf.todo[:CHUNK_FILES]
            bf.step = "parse" if part else "dataset"
        if part:
            tier = jobs.tier_of(part[0][0], bf.today)
            prio = "warm" if tier == "hot" else jobs.tier_priority(tier)
            bf.job = jobs.submit(lambda job, part=part: _chunk(bf, part), cls="full", priority=prio,
                                 label=f"older activities back-fill ({tier})")
        else:
            bf.job = jobs.submit(lambda job: _complete(bf), cls="build", priority="cold",
                                 label="older activities back-fill: the complete dataset")
    except Exception as e:                   # noqa: BLE001 — the next build parses what is left
        _failed(bf, e)


def _failed(bf: Backfill, e: BaseException) -> None:
    log.warning("back-fill of older activities failed: %s", type(e).__name__, exc_info=True)
    with bf.lock:
        bf.state, bf.step, bf.error = "error", None, f"{type(e).__name__}: {e}"[:300]
    _release(None)
    try:                                     # the next request builds (and back-fills) it again,
        from backend.api import wko5views as WV   # instead of 「計算中」 for good
        for key in bf.keys:
            now = WV._restamp(key)
            if getattr(WV._dataset_cfg.peek(now), "deferred", None):
                WV._dataset_cfg.drop(now)
    except Exception:                        # noqa: BLE001
        pass


def _root() -> Path:
    from backend.engine.racepower import weather as WX
    return WX.home() / "fit"


def _release(paths) -> None:
    """The race-power reads see these files again (None: every held one of this tenant)."""
    from backend.engine.racepower import cptest as T
    root = _root()
    if paths is None:
        T.release(root)
        return
    keys = []
    for p in paths:
        try:
            keys.append(Path(p).relative_to(root))
        except ValueError:
            pass
    T.release(root, keys)


def _chunk(bf: Backfill, part: list) -> None:
    from backend.engine.racepower import cptest as T
    paths = [p for _, p in part]
    t0 = time.monotonic()
    try:
        n = bf.store.parse_only(paths)
        T.read_ahead(paths)                  # its race-power reads too: no request reads them later
        log.info("back-fill: %d of %d files parsed in %.1f s", n, len(paths), time.monotonic() - t0)
    except Exception as e:                   # noqa: BLE001 — the complete build parses what is left
        log.warning("back-fill chunk failed: %s", type(e).__name__, exc_info=True)
    finally:
        _release(paths)
        done = {str(p) for p in paths}
        with bf.lock:
            bf.todo = [x for x in bf.todo if str(x[1]) not in done]
            bf.n_done += len(paths)
    _next(bf)


def _rebuild_complete(bf: Backfill) -> None:
    """Each hot-first Dataset still cached, built again complete. A request meanwhile keeps the
    one it has."""
    from backend.api import wko5views as WV
    for key in sorted(bf.keys):
        now = WV._restamp(key)
        cur = WV._dataset_cfg.peek(now)
        if cur is None or not getattr(cur, "deferred", None):
            continue                         # gone (the next request builds it complete) or complete already
        WV._dataset_cfg.put(now, WV._build_dataset(*now, hot_first=False))


def _complete(bf: Backfill) -> None:
    """The complete Dataset in place of each hot-first one still cached (a request keeps the
    hot-first one until then), then a warm-up on it."""
    from backend.api import wko5views as WV
    t0 = time.monotonic()
    try:
        _release(None)                       # every file is read now (a failed chunk's: by this build)
        _rebuild_complete(bf)
    except Exception as e:                   # noqa: BLE001 — reported in progress(); the next build retries
        _failed(bf, e)
        return
    with bf.lock:
        more = bool(bf.todo)                 # a newer hot-first build deferred more meanwhile
    if more:
        _next(bf)
        return
    with bf.lock:
        bf.state, bf.step = "done", None
    _fire(bf)
    log.info("back-fill of %d older activities done; complete dataset in %.1f s", bf.n_total,
             time.monotonic() - t0)
    WV.warm_up("backfill")


def coverage(ds) -> Optional[float]:
    """The first day number from which `ds` holds every activity: the day after its newest
    deferred file (a hot-first Dataset), None when it is complete."""
    deferred = getattr(ds, "deferred", None)
    if not deferred:
        return None
    from backend.engine.wko5expr.dataset import date_to_day
    return float(date_to_day(max(d for d, _ in deferred))) + 1.0


def pending(ds, lo: Optional[float]) -> Optional[dict]:
    """None when what reads the days from `lo` on (a day number; None: the whole history) is
    complete in `ds`; else {n_done, n_total, message} — 「計算中（較舊的活動補算中 n／N）」."""
    cov = coverage(ds)
    if cov is None or (lo is not None and lo >= cov):
        return None
    from backend.i18n import _
    p = progress() or {}
    total = int(p.get("n_total") or len(ds.deferred))
    done = min(int(p.get("n_done") or 0), total)
    return {"n_done": done, "n_total": total,
            "message": _("計算中（較舊的活動補算中 {done}／{total}）", done=done, total=total)}


def pending_back(ds, days: Optional[float]) -> Optional[dict]:
    """pending() of what reads the `days` before the Dataset's today (None: the whole history)."""
    import math
    t = getattr(ds, "today", None)
    if days is None or t is None:
        return pending(ds, None)
    return pending(ds, float(math.floor(t)) - float(days))


def when_complete(ds, fn) -> bool:
    """Call `fn()` once this tenant's back-fill is done, when `ds` is a hot-first Dataset (the
    automatic plan: never with an incomplete CTL). False: `ds` is complete (nothing kept)."""
    if coverage(ds) is None:
        return False
    with _LOCK:
        mine = [bf for (t, _), bf in _STATE.items() if t == _tid() and bf.state == "running"]
    if not mine:
        return False
    with mine[0].lock:
        mine[0].waiting.append(fn)
    return True


def _fire(bf: Backfill) -> None:
    """The when_complete callbacks (the back-fill is done)."""
    with bf.lock:
        due, bf.waiting = bf.waiting, []
    for fn in due:
        try:
            fn()
        except Exception:                    # noqa: BLE001 — a callback never stops the back-fill
            log.warning("back-fill: a when_complete callback failed", exc_info=True)


def build_failed() -> None:
    """A Dataset build of this tenant failed: what a hot-first build held out of the race-power
    reads is released unless a back-fill of this tenant runs (it releases them itself)."""
    tid = _tid()
    with _LOCK:
        busy = any(bf.state == "running" for (t, _), bf in _STATE.items() if t == tid)
    if not busy:
        _release(None)


def progress() -> Optional[dict]:
    """This tenant's back-fill running now: {state, step (parse | dataset), n_done, n_total,
    elapsed_s, error}, else None."""
    tid = _tid()
    with _LOCK:
        mine = [bf for (t, _), bf in _STATE.items() if t == tid and bf.state == "running"]
    return mine[0].view() if mine else None


def reset() -> None:
    """Tests: forget every back-fill (jobs already queued are left alone)."""
    with _LOCK:
        _STATE.clear()
