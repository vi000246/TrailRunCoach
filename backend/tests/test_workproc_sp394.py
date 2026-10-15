"""SP-394 第 5 條: the back-test and a whole classification step compute in a worker process
(backend/workproc.py) — measured, they made cheap requests 2–13× slower in the server's process
(backend/scripts/perf_sp394.py). The queue still decides when; the job's thread waits on a pipe.

The runner itself is tested with real spawned processes (backend/tests/workproc_targets.py: no
data); the classification / back-test wiring with the runner replaced (a spawned process would not
see the fixtures' tmp folders). Synthetic only."""
from __future__ import annotations

import time

import pytest

from backend import jobs, tenancy, workproc


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(workproc.ENV, "1")


# ---------------------------------------------------------------------------- the runner

def test_a_worker_returns_its_result_relays_progress_and_runs_in_the_callers_tenant(on, tmp_path):
    import os
    t = tenancy.Tenant(id="u7", kind=tenancy.USER, root=tmp_path, shared=tmp_path)
    seen = []
    with tenancy.use(t):
        out = workproc.run("backend.tests.workproc_targets:echo", {"a": [1, 2]}, progress=lambda i, n: seen.append((i, n)))
    assert out["x"] == {"a": [1, 2]} and out["tenant"] == "u7"
    assert out["pid"] != os.getpid()                     # another process
    assert out["nested"] == "0"                          # a worker never starts workers
    assert out["nice"] == min(19, os.nice(0) + workproc.WORKER_NICE)   # below the server's CPU priority
    assert seen == [(0, 3), (1, 3), (2, 3)]


def test_a_workers_exception_and_a_dead_worker_raise_here(on):
    with pytest.raises(workproc.WorkerError, match="ValueError: bad input"):
        workproc.run("backend.tests.workproc_targets:boom")
    with pytest.raises(workproc.WorkerError, match="exit code 7"):
        workproc.run("backend.tests.workproc_targets:die")


def test_workers_are_on_by_default_and_off_with_the_env(monkeypatch):
    monkeypatch.delenv(workproc.ENV, raising=False)
    assert workproc.enabled()
    for v in ("0", "false", "off"):
        monkeypatch.setenv(workproc.ENV, v)
        assert not workproc.enabled()


# ---------------------------------------------------------------------------- the classification

@pytest.fixture
def auto(monkeypatch):
    from backend.api import activity_auto as AA
    from backend.engine import activity_tags as AT
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(AT, "load_recorded", lambda *a, **k: [])
    jobs.reset()
    yield AA
    jobs.reset()


def _fit_ds(tmp_path, n):
    from backend.tests.test_activity_auto import _fit_ds as make
    return make(tmp_path, n=n)


def _fake_worker(monkeypatch, ds, calls):
    """workproc.run replaced: the worker's side run here on `ds` (what the worker would rebuild)."""
    from backend.api import wko5views as WV
    monkeypatch.setattr(WV, "key_of", lambda d: ("cfg", "coros", "stamp", "root") if d is ds else None)
    monkeypatch.setattr(WV, "worker_dataset", lambda key: ds)

    def run(target, *args, progress=None, tenant=None):
        calls.append((target, args))
        mod, _, name = target.partition(":")
        import importlib
        return getattr(importlib.import_module(mod), name)(*args, progress=progress)
    monkeypatch.setattr(workproc, "run", run)


def test_a_whole_classification_step_computes_in_a_worker_with_the_same_values(tmp_path, auto, monkeypatch, on):
    AA = auto
    monkeypatch.setattr(AA, "FULL_FROM", 3)
    ds = _fit_ds(tmp_path / "a", 4)
    monkeypatch.setenv(workproc.ENV, "0")
    here = AA.wait(ds)                                   # in this process, as before
    assert here["state"] == "ready"
    ds2 = _fit_ds(tmp_path / "b", 4)
    calls: list = []
    _fake_worker(monkeypatch, ds2, calls)
    monkeypatch.setenv(workproc.ENV, "1")
    snap = AA.wait(ds2)
    assert snap["state"] == "ready" and snap["n_done"] == snap["n_total"] == 4
    assert [c[0] for c in calls] == ["backend.api.activity_auto:compute_in_worker"]
    key, files, recorded = calls[0][1]
    assert key == ("cfg", "coros", "stamp", "root") and sorted(files) == sorted(w.entry.file for w in ds2.workouts)
    assert snap["auto"] == here["auto"]                  # the same values, by activity


def test_a_small_step_a_dataset_not_kept_and_workers_off_stay_in_the_server(tmp_path, auto, monkeypatch, on):
    AA = auto
    calls: list = []
    ds = _fit_ds(tmp_path / "a", 4)
    _fake_worker(monkeypatch, ds, calls)
    monkeypatch.setattr(AA, "FULL_FROM", 5)              # 4 activities: a `single` step
    assert AA.wait(ds)["state"] == "ready" and calls == []
    monkeypatch.setattr(AA, "FULL_FROM", 3)
    other = _fit_ds(tmp_path / "b", 4)                   # not the kept Dataset (key_of → None)
    assert AA.wait(other)["state"] == "ready" and calls == []
    monkeypatch.setenv(workproc.ENV, "0")
    third = _fit_ds(tmp_path / "c", 4)
    _fake_worker(monkeypatch, third, calls)
    assert AA.wait(third)["state"] == "ready" and calls == []


def test_what_the_worker_left_out_is_computed_here(tmp_path, auto, monkeypatch, on):
    AA = auto
    monkeypatch.setattr(AA, "FULL_FROM", 3)
    ds = _fit_ds(tmp_path, 4)
    calls: list = []
    _fake_worker(monkeypatch, ds, calls)
    real = AA.compute_in_worker

    def partial(key, files, recorded, progress):        # the worker's Dataset lost one file
        return real(key, files[1:], recorded, progress)
    monkeypatch.setattr(AA, "compute_in_worker", partial)
    snap = AA.wait(ds)
    assert snap["state"] == "ready" and len(snap["auto"]) == 4 and len(calls) == 1


def test_a_failed_worker_keeps_what_it_sent_and_the_rest_is_computed_here(tmp_path, auto, monkeypatch, on):
    AA = auto
    monkeypatch.setattr(AA, "FULL_FROM", 3)
    ds = _fit_ds(tmp_path, 4)
    from backend.api import wko5views as WV
    monkeypatch.setattr(WV, "key_of", lambda d: ("k",) if d is ds else None)
    files = sorted(w.entry.file for w in ds.workouts)
    here = []
    real = AA.compute_blocking

    def counting(d, progress=None, recorded=None, only=None, context=None):
        here.append(set(only or ()))
        return real(d, progress=progress, recorded=recorded, only=only, context=context)
    monkeypatch.setattr(AA, "compute_blocking", counting)
    idx = {w.entry.file: w.idx for w in ds.workouts}

    def run(target, *args, progress=None, tenant=None):     # sends one activity's values, then dies
        progress({files[0]: {"activity_type": None, "activity_type_reason": "", "effort": None,
                             "effort_reason": "w"}})
        raise workproc.WorkerError("worker exited without an answer (exit code -9)")
    monkeypatch.setattr(workproc, "run", run)
    snap = AA.wait(ds)
    assert snap["state"] == "ready" and len(snap["auto"]) == 4
    assert here[-1] == {idx[f] for f in files[1:]}       # only what the worker had not sent


# ---------------------------------------------------------------------------- the back-test

def test_the_back_test_runs_in_a_worker_on_the_kept_datasets_key(monkeypatch, on):
    from backend.api import racepower as RP
    from backend.api import wko5views as WV
    from backend.engine.racepower import backtest as BT
    ds = object()
    monkeypatch.setattr(RP, "_dataset", lambda: ds)
    monkeypatch.setattr(WV, "key_of", lambda d: ("cfg", "coros", "s", "r") if d is ds else None)
    calls, progress = [], []

    def run(target, *args, progress=None, tenant=None):
        calls.append((target, args))
        progress(1, 2, "case A")
        return True
    monkeypatch.setattr(workproc, "run", run)
    runner = RP._backtest_runner()
    runner(lambda i, n, c: progress.append((i, n, c["label"])))
    assert calls == [("backend.api.racepower:backtest_in_worker", (("cfg", "coros", "s", "r"),))]
    assert progress == [(1, 2, "case A")]
    # workers off (the tests, WKO5COACH_PROCESS_JOBS=0): computed in the server as before
    monkeypatch.setenv(workproc.ENV, "0")
    assert RP._backtest_runner() is None
    # the queued job uses the runner it is given
    seen = []
    jobs.reset()
    monkeypatch.setattr(BT, "_states", {})
    assert BT.start_background(lambda: ds, run=lambda prog: seen.append("worker"))
    end = time.monotonic() + 5
    while not seen and time.monotonic() < end:
        time.sleep(0.01)
    assert seen == ["worker"]
    jobs.reset()


def test_the_worker_side_saves_the_back_test_where_the_page_reads_it(monkeypatch, tmp_path):
    from backend.api import racepower as RP
    from backend.api import wko5views as WV
    from backend.engine.racepower import backtest as BT
    monkeypatch.setattr(BT, "STORE", tmp_path / "bt.json")
    monkeypatch.setattr(WV, "worker_dataset", lambda key: ("ds", key))

    def fake(ds, today, prog):
        prog(1, 1, {"label": "x"})
        return {"ds": list(ds[1])}
    monkeypatch.setattr(BT, "backtest", fake)
    seen = []
    assert RP.backtest_in_worker(("k",), lambda *a: seen.append(a)) is True
    assert BT.load() == {"ds": ["k"]} and seen == [(1, 1, "x")]
