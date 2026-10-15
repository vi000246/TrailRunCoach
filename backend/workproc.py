"""
A queued job's computation in a process of its own (SP-394 第 5 條).

The server is one Python process: a background job that computes in Python (the race back-test,
a whole recompute of the activity classification) holds the GIL most of the time, and every
request then waits for it in turns. Measured (backend/scripts/perf_sp394.py, 2026-10-14, the
52-week demo athlete): during a back-test the 課表 page's calendar went from ~0.1 s to ~1.0–1.8 s
(p50 ×10–19) and the overview status ×13–21; during a whole classification ×2.8–4.7. SP-338's
rule was to split a worker process when background work doubles cheap requests.

So those computations run here, in a fresh spawned process per job: the queue (backend/jobs.py)
still decides when — its classes, places and priorities are unchanged — and the job's thread
only waits on a pipe (no GIL held), relaying the progress the worker sends. The worker reads the
same folders (same environment, the caller's tenant), builds its Dataset from the disk caches the
server's build left (wko5views.worker_dataset: no back-fill, no warm-up started there), writes
what it computes where the server reads it (the back-test's file, the series rows) or returns it
(pickled), and exits — its memory goes with it.

    out = workproc.run("backend.api.activity_auto:compute_in_worker", key, files, recorded,
                       progress=lambda values: ...)

`target` is "module:function", called as function(*args, progress=send) in the worker; send(*v)
calls the caller's `progress(*v)` in the waiting thread. An exception in the worker is raised here
as WorkerError("<Type>: <message>"); a worker that died without an answer (killed by the OOM
killer) too.

Cost (推估 from the local measurement × 8.5): a spawn + importing the app ≈ 0.5 s locally (~4 s on
the NAS) and ~110 MB, then the worker's own Dataset (warm load from the index: ~0.2–1 s locally)
while the job runs — at most one worker per class that uses it (`backtest` 1, `full` 1). So only
long jobs go here: the back-test, and classification steps of `full` size (activity_auto.FULL_FROM
activities or more); a one-activity classification after a sync stays in the server.

WKO5COACH_PROCESS_JOBS=0 runs everything in the server's process as before (the tests do: their
fixtures patch module attributes a spawned process would not see). A worker never starts workers
of its own.
"""
from __future__ import annotations

import importlib
import logging
import os
import threading
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

ENV = "WKO5COACH_PROCESS_JOBS"
# the worker's CPU priority below the server's (os.nice): on the NAS's 4 cores a busy worker
# would otherwise take a core the requests want; its job only takes longer when they compete
WORKER_NICE = 10
_MISSING = object()


class WorkerError(RuntimeError):
    """The worker raised (its message: "<Type>: <message>") or died without an answer."""


def enabled() -> bool:
    """Long background computations in a worker process (default on; WKO5COACH_PROCESS_JOBS=0 off)."""
    return os.environ.get(ENV, "1").strip().lower() not in ("0", "false", "no", "off", "")


def run(target: str, *args: Any, progress: Optional[Callable] = None, tenant=None) -> Any:
    """`target`(*args, progress=send) in a fresh spawned process in `tenant` (default: the
    caller's); its return value, or WorkerError. Blocks the calling thread (a job's) on a pipe."""
    import multiprocessing as mp
    from backend import tenancy
    t = tenant if tenant is not None else tenancy.current()
    ctx = mp.get_context("spawn")
    rx, tx = ctx.Pipe(duplex=False)
    # not a daemon: a worker may start the FIT parse pool (a daemon process cannot have children);
    # it exits by itself when the server is gone (_watch_parent)
    p = ctx.Process(target=_main, args=(target, args, t, tx, os.getpid()),
                    name=f"work-{target.rsplit(':', 1)[-1]}", daemon=False)
    p.start()
    tx.close()
    result, err = _MISSING, None
    try:
        while True:
            if rx.poll(0.5):
                try:
                    kind, val = rx.recv()
                except EOFError:
                    break
                if kind == "progress":
                    if progress is not None:
                        try:
                            progress(*val)
                        except Exception:            # noqa: BLE001 — progress never fails the job
                            log.warning("workproc: a progress callback failed", exc_info=True)
                elif kind == "result":
                    result = val
                    break
                else:
                    err = val
                    break
            elif not p.is_alive() and not rx.poll(0):
                break
    finally:
        rx.close()
        p.join(30)
        if p.is_alive():
            p.kill()
            p.join(5)
    if err is not None:
        raise WorkerError(err)
    if result is _MISSING:
        raise WorkerError(f"worker {target} exited without an answer (exit code {p.exitcode})")
    return result


def _watch_parent(ppid: int) -> None:
    import time
    while True:
        time.sleep(2.0)
        if os.getppid() != ppid:
            os._exit(3)                          # the server is gone: nobody waits for the answer


def _main(target: str, args: tuple, tenant, tx, ppid: int) -> None:
    """The worker process."""
    os.environ[ENV] = "0"                        # never workers of its own
    os.environ["WKO5COACH_NO_WARMUP"] = "1"
    try:
        os.nice(WORKER_NICE)
    except (AttributeError, OSError):            # no nice (Windows) / not allowed: as it is
        pass
    threading.Thread(target=_watch_parent, args=(ppid,), daemon=True).start()
    try:
        from backend import tenancy
        mod, _, name = target.partition(":")
        fn = getattr(importlib.import_module(mod), name)

        def send(*v) -> None:
            tx.send(("progress", v))
        with tenancy.use(tenant):
            out = fn(*args, progress=send)
        tx.send(("result", out))
    except BaseException as e:                   # noqa: BLE001 — answered to the server
        try:
            tx.send(("error", f"{type(e).__name__}: {e}"[:500]))
        except Exception:                        # noqa: BLE001
            pass
    finally:
        tx.close()
