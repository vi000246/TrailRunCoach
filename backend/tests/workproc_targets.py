"""Functions test_workproc_sp394.py runs in a worker process (backend/workproc.py): importable
by "module:function", touching no data."""
from __future__ import annotations

import os


def echo(x, progress):
    from backend import tenancy
    for i in range(3):
        progress(i, 3)
    return {"x": x, "tenant": tenancy.current().id, "pid": os.getpid(),
            "nested": os.environ.get("WKO5COACH_PROCESS_JOBS"), "nice": os.nice(0)}


def boom(progress):
    raise ValueError("bad input")


def die(progress):
    os._exit(7)
