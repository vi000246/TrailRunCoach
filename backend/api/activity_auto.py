"""
GET /api/v1/wko5/activities/auto in the background: the AUTO activity type /
effort of every activity (the 活動列表 filters and editor) without holding a
request — or the server — for minutes.

The first computation on a cold cache reads every activity's samples (HR
effort, the run review's test detection, the as-of thresholds), several
minutes on a full COROS history. So:

  * single flight: one computation per Dataset, every request shares it;
  * background: the work runs in a daemon thread (like the dataset warm-up);
    a request returns at once with what is known plus {state, n_done,
    n_total} the page polls;
  * incremental: activities are evaluated in chunks, newest first, and each
    finished chunk is visible to the next poll;
  * on disk: the result is kept per activity file (the FIT dataset cache
    folder, or ~/.wko5coach for WKO5) under a signature of everything it
    reads, so a restart answers from disk at once; after a change (a sync,
    a plan edit) the previous values are served, marked stale, while the
    recomputation runs.

The values are those of racepower.athlete.auto_tags_all (the same rules,
called per chunk: capacity_samples for outdoor runs, the HR effort rule for
the rest).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import threading
import time
import weakref
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CHUNK = 25                 # activities per published step
CACHE_V = 1
_JOBS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# the computation (the same rules as athlete.auto_tags_all)
# ---------------------------------------------------------------------------

def _recorded_stamp(recorded: list) -> tuple:
    return (len(recorded), sum(r.get("rpe") or 0 for r in recorded), sum(r.get("feel") or 0 for r in recorded))


def _other(ds, w, recorded) -> dict:
    """A workout that is not an outdoor run: auto_tags_all's second branch."""
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import athlete as A
    try:
        th = A.thresholds_as_of(ds, w.entry.start.date())
        es = A.effort_stats(ds, w, th)
        eff = AT.effort_hr(es, th.get("lthr"), th.get("aet"))
    except Exception:                       # noqa: BLE001 — one bad file never breaks the list
        es, eff = {}, {"effort": None, "reason": "無法計算"}
    rec = AT.recorded_of(recorded, w.entry.start, w.entry.file)
    eff = AT.effort_from_rpe((rec or {}).get("rpe"), es.get("rest_share"), eff, rec=rec) or eff
    typ, why = AT.auto_type(test=A._test_reason(ds, w), sport=w.sport, sport_type=w.sport_type,
                            title=getattr(w.entry, "title", "") or "", trail=A.is_trail(w),
                            baiyue_event=A.baiyue_on(ds, w.entry.start.date()))
    return {"activity_type": typ, "activity_type_reason": why, "effort": eff["effort"], "effort_reason": eff["reason"]}


def _chunk_values(ds, chunk: list, recorded: list) -> dict[int, dict]:
    from backend.engine.racepower import athlete as A
    runs = [w for w in chunk if A.outdoor(w)]
    out: dict[int, dict] = {}
    if runs:
        tests = {w.idx: t for w in runs if (t := A._test_reason(ds, w))}
        caps = A.capacity_samples(ds, runs, tags=[], tests=tests, recorded=recorded)
        for w in runs:
            tg = caps[w.idx]["tags"]
            out[w.idx] = {"activity_type": tg["activity_type_auto"], "activity_type_reason": tg["activity_type_reason"],
                          "effort": tg["effort_auto"], "effort_reason": tg["effort_reason"]}
    for w in chunk:
        if w.idx not in out:
            out[w.idx] = _other(ds, w, recorded)
    return out


def compute_blocking(ds, progress=None, recorded: Optional[list] = None) -> dict[int, dict]:
    """{idx: auto values} of every workout, newest first in chunks;
    `progress(done, total, {idx: values})` after each chunk. Series writes
    are batched (dataset.batched_flush)."""
    from backend.engine import activity_tags as AT
    from backend.engine.wko5expr.dataset import batched_flush
    recorded = AT.load_recorded() if recorded is None else recorded
    order = sorted(ds.workouts, key=lambda w: w.entry.start, reverse=True)
    out: dict[int, dict] = {}
    with batched_flush(ds):
        for i in range(0, len(order), CHUNK):
            part = _chunk_values(ds, order[i:i + CHUNK], recorded)
            out.update(part)
            if progress is not None:
                progress(min(i + CHUNK, len(order)), len(order), part)
    return out


# ---------------------------------------------------------------------------
# the disk cache
# ---------------------------------------------------------------------------

_CODE: dict = {}


def _code_sig() -> str:
    """The code the values come from (a rule change recomputes)."""
    if not _CODE:
        from backend.engine import activity_tags, thresholds, workout_review
        from backend.engine.racepower import athlete, intensity, maximal, trailhr
        import sys
        h = hashlib.sha1()
        for m in (activity_tags, thresholds, workout_review, athlete, intensity, maximal, trailhr,
                  sys.modules[__name__]):
            try:
                h.update(inspect.getsource(m).encode("utf-8"))
            except (OSError, TypeError):
                h.update(m.__name__.encode())
        _CODE["v"] = f"{CACHE_V}:{h.hexdigest()[:12]}"
    return _CODE["v"]


def _file_stamp(ds, w):
    try:
        from backend.engine.wko5expr.fitcache import stamp_of
        return stamp_of(Path(ds.dir) / w.entry.file)
    except (OSError, TypeError, AttributeError):
        return None


def signature(ds, recorded: list) -> str:
    """Everything the auto values read: the code, every workout (file stamp,
    sport, tags, title, start), the plan (events, tests, thresholds), the
    dated settings and corrections, the watch RPEs and the power settings."""
    h = hashlib.sha1()

    def add(x):
        h.update(repr(x).encode("utf-8"))
        h.update(b"\x00")
    add(_code_sig())
    add(type(ds).__name__)
    add(getattr(ds, "source", None))
    for w in ds.workouts:
        add((w.entry.file, _file_stamp(ds, w), w.sport, w.sport_type, sorted(w.tags or []),
             getattr(w.entry, "title", ""), w.entry.start.isoformat()))
    add(repr(getattr(ds, "plan", None)))
    try:
        add(sorted((k, [(str(d), v) for d, v in vals]) for k, vals in ds.athlete.settings.items()))
    except Exception:                        # noqa: BLE001
        pass
    corr = getattr(ds, "corrections", None)
    add([(c.file, c.channel, c.t_start, c.t_end) for c in (corr.items if corr else [])])
    add(getattr(ds, "accept_watch_power", None))
    add(_recorded_stamp(recorded))
    add(sorted((r.get("start_local"), r.get("file"), r.get("rpe")) for r in recorded))
    return h.hexdigest()


def cache_path(ds) -> Path:
    store = getattr(ds, "_store", None)
    if store is not None and getattr(store, "home", None) is not None:
        return Path(store.home) / "activity_auto.json"
    from backend.engine.wko5expr import dataset as D
    key = hashlib.sha1(os.path.normcase(str(getattr(ds, "dir", ""))).encode()).hexdigest()[:10]
    return D._cache_dir() / f"activity_auto_{key}.json"


def load_cache(ds) -> dict:
    """{"sig": ..., "files": {file: {"stamp": ..., "start": ..., "auto": {...}}}} (empty when unreadable)."""
    try:
        d = json.loads(cache_path(ds).read_text("utf-8"))
        if isinstance(d, dict) and isinstance(d.get("files"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"sig": None, "files": {}}


def save_cache(ds, sig: Optional[str], files: dict) -> None:
    p = cache_path(ds)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"sig": sig, "files": files}, ensure_ascii=False), "utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.warning("activity auto: could not write the cache (%s)", type(e).__name__)


# ---------------------------------------------------------------------------
# the job
# ---------------------------------------------------------------------------

class Job:
    """One background computation of one Dataset's auto values."""

    def __init__(self, ds, sig: str, recorded: list):
        from backend.engine import activity_tags as AT
        self.ds_ref = weakref.ref(ds)
        self.sig = sig
        self.recorded = recorded
        self.state = "computing"
        self.n_total = len(ds.workouts)
        self.n_done = 0
        self.error: Optional[str] = None
        self.started = time.monotonic()
        self.finished: Optional[float] = None
        self.fresh: dict[str, dict] = {}            # key -> values computed by this job
        self.stale: dict[str, dict] = {}            # key -> values from an earlier signature (shown meanwhile)
        self.lock = threading.Lock()
        disk = load_cache(ds)
        by_file = disk.get("files") or {}
        same = disk.get("sig") == sig
        for w in ds.workouts:
            e = by_file.get(w.entry.file)
            if not isinstance(e, dict) or not isinstance(e.get("auto"), dict):
                continue
            k = AT.key_of(w.entry.start)
            (self.fresh if same else self.stale)[k] = e["auto"]
        # by key: two activities starting in the same minute share one
        if same and len(self.fresh) == len({AT.key_of(w.entry.start) for w in ds.workouts}):
            self.state, self.n_done, self.finished = "ready", self.n_total, time.monotonic()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.state != "computing":
            return
        t = threading.Thread(target=self._run, name="activity-auto", daemon=True)
        self.thread = t
        t.start()

    def _run(self) -> None:
        from backend.engine import activity_tags as AT
        ds = self.ds_ref()
        if ds is None:
            return
        keys = {w.idx: AT.key_of(w.entry.start) for w in ds.workouts}

        def progress(done, total, part):
            with self.lock:
                for i, v in part.items():
                    self.fresh[keys[i]] = v
                self.n_done = done
        try:
            out = compute_blocking(ds, progress=progress, recorded=self.recorded)
            files = {}
            for w in ds.workouts:
                files[w.entry.file] = {"stamp": _file_stamp(ds, w), "start": w.entry.start.isoformat(),
                                       "auto": out.get(w.idx)}
            save_cache(ds, self.sig, files)
            with self.lock:
                self.state, self.n_done, self.finished = "ready", self.n_total, time.monotonic()
                self.stale.clear()
        except Exception as e:               # noqa: BLE001 — reported to the page, the next request retries
            log.warning("activity auto failed: %s", e, exc_info=True)
            with self.lock:
                self.state, self.error, self.finished = "error", f"{type(e).__name__}: {e}"[:300], time.monotonic()

    def snapshot(self) -> dict:
        with self.lock:
            auto = {**self.stale, **self.fresh}
            el = (self.finished or time.monotonic()) - self.started
            return {"state": self.state, "n_done": self.n_done, "n_total": self.n_total,
                    "stale": bool(self.stale) and self.state != "ready", "error": self.error,
                    "elapsed_s": round(el, 1), "auto": auto}


def job_for(ds, recorded: Optional[list] = None) -> Job:
    """The Dataset's job (single flight): started on first use, restarted
    when the inputs changed (signature) and the previous one is not running."""
    from backend.engine import activity_tags as AT
    recorded = AT.load_recorded() if recorded is None else recorded
    sig = signature(ds, recorded)
    with _LOCK:
        job = _JOBS.get(ds)
        running = job is not None and job.state == "computing" and job.thread is not None and job.thread.is_alive()
        if job is not None and (job.sig == sig and job.state != "error" or running):
            return job
        job = Job(ds, sig, recorded)
        _JOBS[ds] = job
        job.start()
        return job


def status(ds) -> dict:
    """The endpoint's answer: {state, n_done, n_total, stale, error, elapsed_s, auto: {key: values}}."""
    return job_for(ds).snapshot()


def wait(ds, timeout: float = 600.0) -> dict:
    """Block until the job is done (tests, scripts)."""
    job = job_for(ds)
    t = job.thread
    if t is not None:
        t.join(timeout)
    return job.snapshot()
