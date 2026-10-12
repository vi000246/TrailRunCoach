"""SP-338: one queue for heavy background work — priorities, how many run at once (a whole
Dataset 1, single jobs 2, back-tests 1 for the server), tenants taking turns, the back-test's
place in line, CPU seconds per tenant; plus the 5-minute merge of automatic syncs, the spread of
everyone's 06:00 sync and the free tier's night, the sweep of old series_* files, the Dataset
cache by memory budget."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend import jobs, tenancy


@pytest.fixture(autouse=True)
def clean():
    jobs.reset()
    yield
    jobs.reset()


def _t(i):
    from pathlib import Path
    return tenancy.Tenant(id=f"u{i}", kind=tenancy.USER, root=Path(f"/nonexistent/u{i}"), shared=Path(f"/nonexistent/u{i}"))


def _gate():
    """A job body that waits for the test to release it, recording its order and tenant."""
    order, release = [], threading.Event()

    def body(name):
        def f(job):
            order.append((name, tenancy.current().id))
            assert release.wait(10)
        return f
    return order, release, body


def _wait(cond, s=5.0):
    end = time.monotonic() + s
    while not cond() and time.monotonic() < end:
        time.sleep(0.005)
    assert cond()


def test_class_caps_full_1_single_2_backtest_1():
    order, release, body = _gate()
    js = [jobs.submit(body(f"f{i}"), cls="full", priority="warm") for i in range(3)]
    js += [jobs.submit(body(f"s{i}"), cls="single", priority="warm", tenant=f"t{i}") for i in range(4)]
    js += [jobs.submit(body(f"b{i}"), cls="backtest", priority="user", tenant=f"t{i}") for i in range(2)]
    _wait(lambda: len(order) == 4)
    snap = jobs.snapshot()
    assert sorted(c for c, _l, _t in snap["running"]) == ["backtest", "full", "single", "single"]
    assert jobs.position(js[-1]) == 1                    # the second back-test is next in line
    release.set()
    for j in js:
        j.join(5)
    assert all(j.state == "done" for j in js) and len(order) == 9


def test_priority_then_fifo():
    order, release, body = _gate()
    first = jobs.submit(body("running"), cls="full", priority="cold")
    _wait(lambda: order)
    cold = jobs.submit(body("cold"), cls="full", priority="cold")
    hot = jobs.submit(body("sync_hot"), cls="full", priority="sync_hot")
    user = jobs.submit(body("user"), cls="full", priority="user")
    assert [jobs.position(j) for j in (user, hot, cold)] == [1, 2, 3]
    release.set()
    for j in (first, cold, hot, user):
        j.join(5)
    assert [n for n, _t in order] == ["running", "user", "sync_hot", "cold"]


def test_tenants_take_turns_within_a_priority():
    """A queued 10 jobs, B 1: B's does not wait for all of A's (SP-338 第 9 條)."""
    order, release, body = _gate()
    blocker = jobs.submit(body("x"), cls="full", priority="warm", tenant="zz")
    _wait(lambda: order)
    a = [jobs.submit(body(f"a{i}"), cls="full", priority="warm", tenant="A") for i in range(10)]
    b = jobs.submit(body("b"), cls="full", priority="warm", tenant="B")
    assert jobs.position(b) <= 2
    release.set()
    for j in [blocker, *a, b]:
        j.join(5)
    names = [n for n, _t in order]
    assert names.index("b") <= 2


def test_one_single_place_per_tenant():
    order, release, body = _gate()
    a1 = jobs.submit(body("a1"), cls="single", priority="warm", tenant="A")
    a2 = jobs.submit(body("a2"), cls="single", priority="warm", tenant="A")
    b1 = jobs.submit(body("b1"), cls="single", priority="warm", tenant="B")
    _wait(lambda: len(order) == 2)
    assert {n for n, _t in order} == {"a1", "b1"} and a2.state == "queued"
    release.set()
    for j in (a1, a2, b1):
        j.join(5)


def test_the_same_key_is_queued_once_and_jobs_run_in_their_tenant():
    order, release, body = _gate()
    blocker = jobs.submit(body("x"), cls="full", priority="warm")
    _wait(lambda: order)
    with tenancy.use(_t(7)):
        j1 = jobs.submit(body("w"), cls="full", priority="cold", key=("warm", "u7"))
        j2 = jobs.submit(body("w2"), cls="full", priority="user", key=("warm", "u7"))
    assert j1 is j2 and j1.priority == jobs.PRIORITIES["user"]          # promoted, not duplicated
    release.set()
    blocker.join(5)
    j1.join(5)
    assert ("w", "u7") in order and not any(n == "w2" for n, _ in order)


def test_cpu_seconds_are_recorded_per_tenant():
    def spin(job):
        end = time.thread_time() + 0.05
        while time.thread_time() < end:
            pass
    with tenancy.use(_t(3)):
        j = jobs.submit(spin, cls="single", priority="warm")
        j.join(5)
        assert j.cpu_s >= 0.04 and jobs.cpu_today() >= 0.04
    assert jobs.cpu_today("u4") == 0.0


def test_a_failing_job_is_recorded_and_the_next_starts():
    def boom(job):
        raise RuntimeError("x")
    j = jobs.submit(boom, cls="full", priority="warm")
    k = jobs.submit(lambda job: 42, cls="full", priority="warm")
    j.join(5)
    k.join(5)
    assert j.state == "failed" and "RuntimeError" in j.error and k.state == "done" and k.result == 42


# ---------------------------------------------------------------------------- the back-test

def test_one_back_test_for_the_server_the_second_waits_with_its_place(monkeypatch):
    from backend.engine.racepower import backtest as BT
    monkeypatch.setattr(BT, "_states", {})
    release = threading.Event()
    ran = []

    def fake_backtest(ds, today, prog):
        ran.append(tenancy.current().id)
        prog(1, 2, {"label": "x"})
        assert release.wait(10)
        return {"ok": True}
    monkeypatch.setattr(BT, "backtest", fake_backtest)
    monkeypatch.setattr(BT, "save", lambda r: None)
    with tenancy.use(_t(1)):
        assert BT.start_background(lambda: None) is True
        assert BT.start_background(lambda: None) is False        # one per tenant
    _wait(lambda: ran)
    with tenancy.use(_t(2)):
        assert BT.start_background(lambda: None) is True
        st = BT.state()
        assert st["queued"] is True and st["position"] == 1 and st["running"] is False
    with tenancy.use(_t(1)):
        st = BT.state()
        assert st["running"] is True and st["progress"]["n"] == 2
    release.set()
    _wait(lambda: len(ran) == 2)
    assert ran == ["u1", "u2"]


# ---------------------------------------------------------------------------- syncs

def test_automatic_syncs_within_5_minutes_merge(monkeypatch):
    import asyncio
    from backend.sync import runner
    started = []

    async def fake_run_once(source, athlete_id, trigger, session_factory=None):
        started.append(trigger)
    monkeypatch.setattr(runner, "run_once", fake_run_once)

    async def go():
        a = runner.start_background("coros", trigger="open")
        b = runner.start_background("coros", trigger="open")          # 1 s later: merged
        c = runner.start_background("coros", trigger="manual")        # the button is never merged
        await asyncio.sleep(0)
        return a, b, c
    a, b, c = asyncio.run(go())
    assert a is not None and b is None and c is not None


def test_ten_tenants_at_06_00_start_spread_out_and_the_free_tier_at_night():
    from backend.sync import scheduler
    offs = sorted(scheduler.offset_minutes(f"u{i}") for i in range(2, 12))
    assert len(set(offs)) >= 6 and max(offs) - min(offs) >= 10          # spread over ~30 min
    nights = sorted(scheduler.night_slot(f"u{i}")[1] for i in range(2, 102))
    assert nights[0] < 30 and nights[-1] > 200                           # 01:00 … ~05:00
    assert scheduler.night_slot("u5") == scheduler.night_slot("u5")


# ---------------------------------------------------------------------------- caches

def test_old_series_files_go_after_a_warm_up(tmp_path, monkeypatch):
    from backend.engine.wko5expr import dataset as D
    monkeypatch.setattr(D, "SERIES_SEEN", set())
    cur, old, fresh = (tmp_path / f"series_{n}.json" for n in ("thr_v6_aa", "thr_v5_bb", "new_cc"))
    for p in (cur, old, fresh):
        p.write_text("{}", "utf-8")
    week3 = time.time() - 21 * 86400
    os.utime(old, (week3, week3))
    os.utime(cur, (week3, week3))
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "series_x.json").write_text("{}", "utf-8")
    os.utime(other / "series_x.json", (week3, week3))
    D.series_seen(cur)                                    # read this process: the current version
    assert D.sweep_series() == 1
    assert cur.exists() and fresh.exists() and not old.exists()
    assert (other / "series_x.json").exists()             # a folder nobody read from: untouched


def test_the_dataset_cache_keeps_to_its_memory_budget(monkeypatch):
    from backend.api import wko5views as WV
    monkeypatch.setenv("WKO5COACH_DATASET_MEM_MB", "500")
    sizes = {"a": 1000, "b": 1000, "c": 300}                       # ≈ 230, 230, 104 MB

    def build(cfg, source, stamp, shared):
        return SimpleNamespace(workouts=[0] * sizes[shared])
    c = WV._DatasetCache(build)
    c("{}", "coros", "1", "a")
    c("{}", "coros", "1", "b")
    assert len(c.entries()) == 2                                   # 460 MB ≤ 500
    c("{}", "coros", "1", "c")
    assert [k[3] for k in c.entries()] == ["b", "c"]               # the least recently used went
    monkeypatch.setenv("WKO5COACH_DATASET_MEM_MB", "100")
    c("{}", "coros", "1", "a")
    assert [k[3] for k in c.entries()] == ["a"]                    # the newest always stays
