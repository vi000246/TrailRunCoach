"""
The server's one queue for heavy background work (SP-338).

Before this, the Dataset warm-up, the race back-test and the other rebuilds each started their own
thread: two Datasets built at once (+ the FIT process pool) once took the NAS to ~5 GB and swap.
Now they go through one queue that decides who starts next; work already running is never
interrupted (a queue cannot make a thread give way — see SP-338 第 8 條 on the GIL).

    job = jobs.submit(fn, cls="full", priority="sync_hot", label="dataset warm-up", key=("warm", tid))
    job.join(10); job.state; jobs.position(job)

Priorities (lower first; SP-338 第 1 條):
    user        a person is waiting for it (a page, the back-test button)
    sync_hot    right after a sync: what the next page load needs
    deploy_hot  after a deploy: tenants active in the last 7 days
    warm        background fill of the warm data
    cold        background fill of the cold data
    archive     the monthly archive (SP-317)

Classes and how many run at once (第 2 條): `full` (a whole-Dataset rebuild, a whole recompute) 1,
`single` (one activity, one small job) 2, `backtest` 1 for the whole server, `build` 2 (a whole
Dataset built outside a warm-up: a request's — SP-394 第 4 條, wko5views._dataset — or the last step
of a back-fill of older activities; work nobody waits for takes at most one of them
(BACKGROUND_PLACES), so a request always finds one — 推估 from SP-320 §4.6: 4–6 working sets of
300–500 MB fit the 3 GB container). Within one priority the tenants take turns (第 9 條: round-robin
by who was served least recently), and one tenant holds at most one `single` place at a time for
background work — a job someone waits for (`user`) may take the other one (SP-394: the overview
status of a tenant whose classification runs).

The data's age tiers (SP-394 第 3、4 條, docs/research/cache-tiering.md §7.1): `tier_of` — hot (the
last HOT_DAYS), warm (to WARM_DAYS), cold (older). A version change or a first import computes the
hot tier first at the trigger's priority (`sync_hot` / `deploy_hot`), then the warm and cold tiers
in the background at `warm` / `cold` (`tier_priority`), newest first.

What goes through it (SP-394): the Dataset warm-up (`full`, wko5views.warm_up), its low-priority
steps at `warm` (the never-fitted calibration and the race charts `full`; the activity
classification `single`, or `full` when most activities are recomputed, in age tiers —
api/activity_auto.py), the overview status (`single`, api/overview._status), the Dataset a request
builds (`build`, `user`: wko5views._dataset), the back-fill of the older activities a hot-first
build left out (`full` chunks at `warm` / `cold`, then `build` at `cold`: api/backfill.py), the
back-test (`backtest`), the cold-data sweep queued once the classification's last tier is done
(`full` at `cold`: the summaries of the activities older than WARM_DAYS, their per-second
channels dropped; SP-340, wko5expr/coldstore.schedule_sweep) and the monthly archive
(`submit_archive`, SP-317: engine/fit_archive.tick queues it; its job calls
coldstore.prepare_archive first).

A caller that needs the result waits with `call(fn, ...)`: it queues `fn` and returns its result
(or raises its exception); with a `key`, concurrent callers share one job and its result (the
request path's Dataset build: one per tenant). Called from inside a job (`current()`), `fn` runs at
once in that job's thread — the job already holds a place, and queuing would wait for itself. A
single flight's leader (backend/singleflight.py) may wait in `call`, so a job never joins a flight
whose leader waits for a job of its own class: the status job (`single`) runs only the computation,
never a flight; the warm-up (`full`) joins the status / plan-inputs flights, whose leaders wait at
most for a `single` job. A flight's leader never waits for a `build` place either (`_dataset()`
builds inline there: singleflight.leading()), and a request waiting for one holds no lock a job
needs (wko5views._dataset_in_tenant).

Every job records its tenant and its CPU seconds (第 6 條, thread CPU time) in the perf log and in
`cpu_today()` — the free tier's compute budget (SP-330) reads it once that budget exists — and the
process's resident memory when it ended (`rss_mb`; Linux, SP-394 第 6 條's measurement reads it).
A job with the same `key` as one already queued / running is not queued twice (第 5 條's merging):
the existing job is returned.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import itertools
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Optional

log = logging.getLogger(__name__)

PRIORITIES = {"user": 0, "sync_hot": 1, "deploy_hot": 2, "warm": 3, "cold": 4, "archive": 5}
CAPS = {"full": 1, "single": 2, "backtest": 1, "build": 2}
BACKGROUND_PLACES = {"build": 1}  # places of a class that work nobody waits for may take at once
KEEP_DONE = 200                  # finished jobs kept for status questions

# the data's age tiers (SP-394; cache-tiering.md §7.1 — 90 days is also the charts' default range,
# SP-336)
HOT_DAYS = 90
WARM_DAYS = 365
TIERS = ("hot", "warm", "cold")


def tier_of(day: dt.date, today: dt.date) -> str:
    """hot (at most HOT_DAYS before `today`), warm (at most WARM_DAYS) or cold (older)."""
    age = (today - day).days
    return "hot" if age <= HOT_DAYS else "warm" if age <= WARM_DAYS else "cold"


def tier_priority(tier: str, hot: str = "deploy_hot") -> str:
    """The queue priority of one tier's work: the hot tier at the trigger's (`hot`), the warm
    and cold tiers at `warm` / `cold`."""
    return hot if tier == "hot" else tier


@dataclass(eq=False)
class Job:
    id: int
    fn: Callable
    cls: str
    priority: int
    label: str
    tenant: str
    key: Optional[Hashable]
    ctx: contextvars.Context
    state: str = "queued"             # queued | running | done | failed
    enqueued: float = field(default_factory=time.monotonic)
    started: Optional[float] = None
    finished: Optional[float] = None
    cpu_s: float = 0.0
    error: Optional[str] = None
    result: Any = None
    progress: Optional[dict] = None
    shared: Optional[dict] = None     # call(key=…): the result box its callers share
    waiters: int = 0                  # callers of `shared` still to read it
    rss_mb: Optional[float] = None    # the process's resident memory when the job ended
    _done: threading.Event = field(default_factory=threading.Event)

    def join(self, timeout: Optional[float] = None) -> None:
        self._done.wait(timeout)

    def is_alive(self) -> bool:
        return not self._done.is_set()

    def view(self) -> dict:
        return {"id": self.id, "label": self.label, "state": self.state, "class": self.cls,
                "position": position(self), "progress": self.progress, "error": self.error,
                "cpu_s": round(self.cpu_s, 2),
                "wait_s": round((self.started or time.monotonic()) - self.enqueued, 1)}


_LOCK = threading.Lock()
_IDS = itertools.count(1)
_QUEUED: list[Job] = []
_RUNNING: list[Job] = []
_DONE: list[Job] = []
_SERVED: dict = {}                 # (cls, tenant) -> serve counter (round-robin)
_SERVE_N = itertools.count(1)
_CPU: dict = {}                    # (local day, tenant) -> cpu seconds
_INLINE = False                    # tests: run the dispatch synchronously in the caller's thread
_LOCAL = threading.local()         # .job: the job this thread runs now (current())


def _tenant_id() -> str:
    from backend import tenancy
    try:
        return tenancy.current().id
    except Exception:              # noqa: BLE001 — a demo instance without its data
        return "default"


def submit(fn: Callable, *, cls: str = "single", priority: str = "warm", label: str = "",
           key: Optional[Hashable] = None, tenant: Optional[str] = None, _shared: Optional[dict] = None) -> Job:
    """Queue `fn(job)` (run in the caller's contextvars: its tenant). Returns the job — an
    existing one when a job with the same `key` is still queued or running. `_shared`: call()'s
    result box (a caller merged into a job of call() registers as one more reader)."""
    if cls not in CAPS:
        raise ValueError(f"unknown class {cls}")
    if priority not in PRIORITIES:
        raise ValueError(f"unknown priority {priority}")
    tid = tenant or _tenant_id()
    with _LOCK:
        if key is not None:
            for j in _QUEUED + _RUNNING:
                if j.key == key:
                    if PRIORITIES[priority] < j.priority and j.state == "queued":
                        j.priority = PRIORITIES[priority]       # someone now waits for it
                    if _shared is not None and j.shared is not None:
                        j.waiters += 1
                    return j
        job = Job(id=next(_IDS), fn=fn, cls=cls, priority=PRIORITIES[priority], label=label or getattr(fn, "__name__", "job"),
                  tenant=tid, key=key, ctx=contextvars.copy_context())
        if _shared is not None:
            job.shared, job.waiters = _shared, 1
        _QUEUED.append(job)
    _dispatch()
    return job


def call(fn: Callable[[], Any], *, cls: str = "single", priority: str = "user", label: str = "",
         tenant: Optional[str] = None, key: Optional[Hashable] = None) -> Any:
    """`fn()` through the queue for a caller that waits for it (a request, a single flight's
    leader, a waiting background thread): its result, or its exception raised again here. Inside
    a job already (`current()`), `fn` runs at once in this thread: that job holds a place. The
    result goes to the callers only: the finished job kept for status questions holds neither
    (a Status, a Dataset). With a `key`, a caller arriving while a call of that key is queued or
    running waits for that one and gets its result (or its exception): one computation."""
    if current() is not None:
        return fn()
    box: dict = {}

    def body(job):
        try:
            box["value"] = fn()
        except BaseException as e:         # noqa: BLE001 — raised again in the callers
            box["error"] = e
            raise
    job = submit(body, cls=cls, priority=priority, label=label or getattr(fn, "__name__", "job"), tenant=tenant,
                 key=key, _shared=box)
    with _LOCK:
        mine = job.shared                   # this caller's box, or the one it joined (None: not a call())
    if mine is None:
        job.join()                          # a submit() of that key: wait for it, then compute alone
        return call(fn, cls=cls, priority=priority, label=label, tenant=tenant)
    try:
        job.join()
        if "error" in mine:
            raise mine["error"]
        return mine.get("value")
    finally:
        with _LOCK:
            job.waiters -= 1
            if job.waiters <= 0:
                job.shared = None           # the last reader: the job keeps no result


def current() -> Optional[Job]:
    """The job the calling thread runs (None outside the queue). Per thread: a thread a job
    starts is not inside it (it holds no place)."""
    return getattr(_LOCAL, "job", None)


def promote(job: Job, priority: str) -> None:
    """Someone now waits for a queued job: move it up to `priority` (never down)."""
    if priority not in PRIORITIES:
        raise ValueError(f"unknown priority {priority}")
    with _LOCK:
        if job.state == "queued" and PRIORITIES[priority] < job.priority:
            job.priority = PRIORITIES[priority]


def submit_archive(fn: Callable, *, label: str = "monthly archive", key: Optional[Hashable] = None,
                   tenant: Optional[str] = None) -> Job:
    """The monthly archive's place in the queue (SP-317, engine/fit_archive.tick → run).
    `archive`, the lowest priority — after every waiting job of every other kind, the cold
    fill included; `full` (it rewrites one tenant's cold summaries and moves its old FITs).
    SP-317 runs it in its 03:00–05:00 window: one call per tenant due, `key` = ("archive",
    tenant id, month) so a second call before it ran is the same job. Its `fn` first calls
    wko5expr/coldstore.prepare_archive(cutoff) (SP-340: freezes the cold summaries; idempotent)
    and deletes only the files that returns."""
    return submit(fn, cls="full", priority="archive", label=label, key=key, tenant=tenant)


def _order(jobs: list[Job]) -> list[Job]:
    """The queued jobs of one class in the order they will start: by priority; within one, the
    tenants in turn (the one served least recently first, each tenant's own jobs FIFO) — the
    same choice _pick makes one job at a time, simulated to the end."""
    out: list[Job] = []
    for prio in sorted({j.priority for j in jobs}):
        per: dict = {}
        for j in sorted((j for j in jobs if j.priority == prio), key=lambda j: (j.enqueued, j.id)):
            per.setdefault(j.tenant, []).append(j)
        served = {t: _SERVED.get((q[0].cls, t), 0) for t, q in per.items()}
        tick = max([0, *_SERVED.values()])
        while per:
            t = min(per, key=lambda t: (served[t], per[t][0].enqueued, per[t][0].id))
            out.append(per[t].pop(0))
            tick += 1
            served[t] = tick
            if not per[t]:
                del per[t]
    return out


def _pick() -> list[Job]:
    """(under _LOCK) the jobs to start now."""
    start = []
    user = PRIORITIES["user"]
    for cls, cap in CAPS.items():
        running = [j for j in _RUNNING if j.cls == cls]
        free = cap - len(running)
        if free <= 0:
            continue
        busy_tenants = {j.tenant for j in running} if cls == "single" else set()
        bg_cap = BACKGROUND_PLACES.get(cls)
        bg = sum(1 for j in running if j.priority != user)
        for j in _order([j for j in _QUEUED if j.cls == cls]):
            if free <= 0:
                break
            if cls == "single" and j.tenant in busy_tenants and j.priority != user:
                continue                       # one single place per tenant (someone waiting: the second)
            if bg_cap is not None and j.priority != user:
                if bg >= bg_cap:
                    continue                   # the class's other places stay for requests
                bg += 1
            start.append(j)
            busy_tenants.add(j.tenant)
            free -= 1
    for j in start:
        _QUEUED.remove(j)
        _RUNNING.append(j)
        j.state = "running"
        j.started = time.monotonic()
        _SERVED[(j.cls, j.tenant)] = next(_SERVE_N)
    return start


def _dispatch() -> None:
    with _LOCK:
        start = _pick()
    for j in start:
        if _INLINE:
            _run(j)
        else:
            threading.Thread(target=_run, args=(j,), name=f"job-{j.cls}-{j.id}", daemon=True).start()


def _run(job: Job) -> None:
    t_cpu = time.thread_time()
    outer = getattr(_LOCAL, "job", None)  # _INLINE: the caller's thread may be in a job already
    _LOCAL.job = job
    try:
        job.result = job.ctx.run(job.fn, job)
        job.state = "done"
    except BaseException as e:             # noqa: BLE001 — recorded on the job
        job.state = "failed"
        job.error = f"{type(e).__name__}: {str(e)[:200]}"
        log.warning("job %s (%s) failed: %s", job.label, job.cls, type(e).__name__)
    finally:
        _LOCAL.job = outer
        job.fn = None                      # kept in _DONE: never what fn closed over (a Dataset)
        job.cpu_s = time.thread_time() - t_cpu
        job.finished = time.monotonic()
        job.rss_mb = rss_mb()
        _account(job)
        with _LOCK:
            if job in _RUNNING:
                _RUNNING.remove(job)
            _DONE.append(job)
            del _DONE[:-KEEP_DONE]
        job._done.set()
        _dispatch()


def rss_mb() -> Optional[float]:
    """The process's resident memory now (MB; Linux /proc — the NAS container), else None."""
    try:
        with open("/proc/self/statm", "rb") as f:
            pages = int(f.read().split()[1])
        import os
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1048576, 1)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _account(job: Job) -> None:
    day = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    with _LOCK:
        _CPU[(day, job.tenant)] = _CPU.get((day, job.tenant), 0.0) + job.cpu_s
        for k in [k for k in _CPU if k[0] < day]:
            if len(_CPU) > 5000:
                _CPU.pop(k, None)
    try:
        from backend import applog
        applog.perf("job", job.label, job.finished - (job.started or job.finished), cls=job.cls,
                    tenant=job.tenant, cpu=round(job.cpu_s, 2), wait=round((job.started or 0) - job.enqueued, 1),
                    status=job.state, **({"rss_mb": job.rss_mb} if job.rss_mb is not None else {}))
    except Exception:                      # noqa: BLE001 — logging never fails a job
        pass


def position(job: Job) -> int:
    """0 = running (or finished); n = n jobs of its class start before it."""
    with _LOCK:
        if job.state != "queued":
            return 0
        order = _order([j for j in _QUEUED if j.cls == job.cls])
        return order.index(job) + 1 if job in order else 0


def find(key: Hashable) -> Optional[Job]:
    """The queued / running job with `key`, else the newest finished one."""
    with _LOCK:
        for j in _QUEUED + _RUNNING:
            if j.key == key:
                return j
        for j in reversed(_DONE):
            if j.key == key:
                return j
    return None


def cpu_today(tenant: Optional[str] = None) -> float:
    """CPU seconds of this tenant's jobs today (Taiwan day)."""
    day = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    with _LOCK:
        return _CPU.get((day, tenant or _tenant_id()), 0.0)


def snapshot() -> dict:
    with _LOCK:
        return {"queued": [(j.cls, j.label, j.tenant) for j in _order(_QUEUED)],
                "running": [(j.cls, j.label, j.tenant) for j in _RUNNING]}


def reset() -> None:
    """Tests: forget everything (running threads are left alone)."""
    with _LOCK:
        _QUEUED.clear()
        _RUNNING.clear()
        _DONE.clear()
        _SERVED.clear()
        _CPU.clear()
