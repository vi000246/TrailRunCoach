"""Render cache for the chart page: keys, disk persistence, size cap,
coalescing of identical in-flight requests, concurrency cap."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import datetime as dt
import threading
import time

from backend.engine.wko5expr import render_cache as RC
from backend.engine.wko5expr.render_cache import RenderCache, chart_key, data_fingerprint
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

CHART = {"title": "t", "series": [{"expression": "tss"}]}
REQ = {"view": "v", "d": 0, "c": 1, "begin": 1.0, "end": 2.0, "parity": False, "params": {}}


def test_key_changes_with_chart_request_and_data():
    k = chart_key(CHART, REQ, "fp")
    assert k == chart_key(dict(CHART), dict(REQ), "fp")
    assert k != chart_key({**CHART, "series": [{"expression": "tss*2"}]}, REQ, "fp")   # fixes / custom view edit
    assert k != chart_key(CHART, {**REQ, "end": 3.0}, "fp")                            # date range
    assert k != chart_key(CHART, {**REQ, "params": {"source": "coros"}}, "fp")         # data source / period
    assert k != chart_key(CHART, {**REQ, "parity": True}, "fp")
    assert k != chart_key(CHART, REQ, "fp2")                                           # data changed


def test_key_changes_with_code_version(monkeypatch):
    k = chart_key(CHART, REQ, "fp")
    assert RC.code_signature() == RC.code_signature()       # fixed for the process
    monkeypatch.setattr(RC, "CACHE_VERSION", RC.CACHE_VERSION + 1)
    monkeypatch.setattr(RC, "_CODE_SIGNATURE", RC._compute_code_signature())
    assert chart_key(CHART, REQ, "fp") != k


def test_code_signature_covers_zones_and_planning():
    names = {p.name for d, pat in RC._ENGINE_GLOBS for p in d.glob(pat)}
    assert {"zones.py", "planning.py", "thresholds.py", "evaluator.py", "render.py"} <= names


def test_fingerprint_follows_workouts_and_today(tmp_path):
    (tmp_path / "A.wko5athlete").write_bytes(b"x")
    def ds(n, today=dt.date(2026, 9, 30)):
        d = FakeDataset([FakeWorkout(dt.datetime(2026, 9, i + 1, 7)) for i in range(n)], today)
        d.dir, d.config = tmp_path, None
        return d
    assert data_fingerprint(ds(2)) == data_fingerprint(ds(2))
    assert data_fingerprint(ds(2)) != data_fingerprint(ds(3))              # sync added a workout
    assert data_fingerprint(ds(2)) != data_fingerprint(ds(2, dt.date(2026, 10, 1)))


def test_fingerprint_follows_athlete_file(tmp_path):
    f = tmp_path / "A.wko5athlete"
    f.write_bytes(b"x")
    d = FakeDataset([], dt.date(2026, 9, 30))
    d.dir, d.config = tmp_path, None
    a = data_fingerprint(d)
    f.write_bytes(b"xy")                                                     # WKO5 rewrote the index
    assert data_fingerprint(d) != a


def test_disk_persistence_and_memory(tmp_path):
    c = RenderCache(tmp_path)
    calls = []
    assert c.get_or_compute("ab" * 20, lambda: calls.append(1) or {"v": 1}) == {"v": 1}
    assert c.get_or_compute("ab" * 20, lambda: calls.append(1) or {"v": 2}) == {"v": 1}
    assert len(calls) == 1
    fresh = RenderCache(tmp_path)                                            # after a restart
    assert fresh.get("ab" * 20) == {"v": 1} and fresh.stats["hit_disk"] == 1


def test_errors_are_not_cached(tmp_path):
    c = RenderCache(tmp_path)
    def boom():
        raise ValueError("x")
    try:
        c.get_or_compute("cd" * 20, boom)
    except ValueError:
        pass
    assert c.get_or_compute("cd" * 20, lambda: {"ok": True}) == {"ok": True}


def test_size_cap_evicts_least_recently_used(tmp_path):
    c = RenderCache(tmp_path, max_bytes=2500)
    for i in range(5):
        c.put(f"{i:02d}" + "e" * 38, {"pad": "x" * 1000})
        time.sleep(0.02)
    c.prune()
    left = sorted(p.stem[:2] for p in tmp_path.glob("*/*.json"))
    assert left == ["03", "04"]


def test_identical_requests_are_coalesced(tmp_path):
    c = RenderCache(tmp_path)
    calls, gate = [], threading.Event()
    def slow():
        calls.append(1)
        gate.wait(5)
        return {"v": 1}
    out = []
    ts = [threading.Thread(target=lambda: out.append(c.get_or_compute("ef" * 20, slow))) for _ in range(4)]
    for t in ts:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in ts:
        t.join(5)
    assert out == [{"v": 1}] * 4 and len(calls) == 1 and c.stats["coalesced"] == 3


def test_concurrent_renders_are_capped(tmp_path):
    c = RenderCache(tmp_path, max_concurrent=2)
    active, peak, lock = [0], [0], threading.Lock()
    def work():
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.1)
        with lock:
            active[0] -= 1
        return {}
    ts = [threading.Thread(target=c.get_or_compute, args=(f"{i:02d}" + "f" * 38, work)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert peak[0] == 2
