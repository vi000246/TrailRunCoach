"""GET /activities/auto in the background (api/activity_auto.py): the same
values as athlete.auto_tags_all, one computation per Dataset however many
requests ask, an immediate answer with the progress, and the per-file disk
cache that makes a later open (a restart) instant. Synthetic FIT folders
only (fit_builder), a tmp FIT cache."""
import asyncio
import datetime as dt
import threading
import time
from datetime import timezone

import pytest

from backend.api import activity_auto as AA
from backend.engine import activity_tags as AT
from backend.tests.fit_builder import build_run

T0 = dt.datetime(2025, 12, 13, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


@pytest.fixture(autouse=True)
def _no_recorded(monkeypatch):
    monkeypatch.setattr(AT, "load_recorded", lambda *a, **k: [])


def _fit_ds(tmp_path, n=4):
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.corrections import CorrectionStore
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    d = tmp_path / "fit" / "coros" / "2025"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        p = d / f"{i}.fit"
        if not p.exists():
            p.write_bytes(build_run(T0 + dt.timedelta(days=i), seconds=1500 + 300 * i, speed_m_s=3.0,
                                    hr=135 + 5 * i))
    return FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=False), today=dt.date(2026, 1, 1),
                            corrections=CorrectionStore(tmp_path / "corr.json"), classifications={},
                            athlete_settings=[], estimate_thresholds=False, tz=timezone.utc)


def _keyed(ds, by_idx):
    return {AT.key_of(w.entry.start): by_idx.get(w.idx) for w in ds.workouts}


def test_same_values_as_auto_tags_all(tmp_path, no_plan):
    from backend.engine.racepower import athlete as A
    ds = _fit_ds(tmp_path)
    old = A.auto_tags_all(ds)
    ds2 = _fit_ds(tmp_path)
    new = AA.compute_blocking(ds2, recorded=[])
    assert set(new) == {w.idx for w in ds2.workouts}
    assert _keyed(ds2, new) == _keyed(ds, old)
    assert all(v["effort_reason"] for v in new.values())


def test_progress_reports_every_chunk_newest_first(tmp_path, no_plan, monkeypatch):
    monkeypatch.setattr(AA, "CHUNK", 2)
    ds = _fit_ds(tmp_path, n=5)
    seen = []
    AA.compute_blocking(ds, progress=lambda d, t, part: seen.append((d, t, sorted(part))), recorded=[])
    assert [s[:2] for s in seen] == [(2, 5), (4, 5), (5, 5)]
    newest = max(ds.workouts, key=lambda w: w.entry.start).idx
    assert newest in seen[0][2]


def _slow(monkeypatch, gate: threading.Event, calls: list):
    real = AA._chunk_values

    def slow(ds, chunk, recorded):
        calls.append(len(chunk))
        gate.wait(10)
        return real(ds, chunk, recorded)
    monkeypatch.setattr(AA, "_chunk_values", slow)


def test_concurrent_requests_share_one_background_computation(tmp_path, no_plan, monkeypatch):
    monkeypatch.setattr(AA, "CHUNK", 100)
    ds = _fit_ds(tmp_path)
    gate, calls = threading.Event(), []
    _slow(monkeypatch, gate, calls)
    t = time.monotonic()
    snaps = []
    ths = [threading.Thread(target=lambda: snaps.append(AA.status(ds))) for _ in range(6)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(5)
    assert time.monotonic() - t < 5                     # answered while the work waits
    assert len(snaps) == 6 and all(s["state"] == "computing" and s["n_done"] == 0 for s in snaps)
    assert all(s["n_total"] == len(ds.workouts) for s in snaps)
    gate.set()
    done = AA.wait(ds)
    assert done["state"] == "ready" and done["n_done"] == done["n_total"]
    assert calls == [len(ds.workouts)]                  # one computation for six requests
    assert set(done["auto"]) == {AT.key_of(w.entry.start) for w in ds.workouts}
    assert AA.status(ds)["state"] == "ready" and calls == [len(ds.workouts)]


def test_a_restart_answers_from_the_disk_cache(tmp_path, no_plan, monkeypatch):
    ds = _fit_ds(tmp_path)
    first = AA.wait(ds)
    assert first["state"] == "ready"
    calls = []
    real = AA._chunk_values
    monkeypatch.setattr(AA, "_chunk_values", lambda *a: calls.append(1) or real(*a))
    ds2 = _fit_ds(tmp_path)                             # a rebuilt Dataset (a restart): nothing to compute
    s = AA.status(ds2)
    assert s["state"] == "ready" and calls == [] and s["auto"] == first["auto"]


def test_a_restart_is_ready_with_two_activities_in_the_same_minute(tmp_path, no_plan, monkeypatch):
    d = tmp_path / "fit" / "coros" / "2025"
    d.mkdir(parents=True)
    (d / "x.fit").write_bytes(build_run(T0 + dt.timedelta(seconds=20), seconds=900, speed_m_s=3.0))
    ds = _fit_ds(tmp_path, n=2)                         # 0.fit and x.fit start in the same minute
    assert len({AT.key_of(w.entry.start) for w in ds.workouts}) < len(ds.workouts)
    AA.wait(ds)
    calls = []
    real = AA._chunk_values
    monkeypatch.setattr(AA, "_chunk_values", lambda *a: calls.append(1) or real(*a))
    assert AA.status(_fit_ds(tmp_path, n=2))["state"] == "ready" and calls == []


def test_a_change_recomputes_and_serves_the_old_values_meanwhile(tmp_path, no_plan, monkeypatch):
    ds = _fit_ds(tmp_path, n=3)
    old = AA.wait(ds)["auto"]
    _fit_ds(tmp_path, n=4)                              # a sync adds an activity
    ds2 = _fit_ds(tmp_path, n=4)
    gate, calls = threading.Event(), []
    _slow(monkeypatch, gate, calls)
    s = AA.status(ds2)
    assert s["state"] == "computing" and s["stale"]
    assert {k: s["auto"][k] for k in old} == old       # the known activities keep their values
    gate.set()
    done = AA.wait(ds2)
    assert done["state"] == "ready" and not done["stale"] and len(done["auto"]) == 4


def test_a_failure_is_reported_and_retried(tmp_path, no_plan, monkeypatch):
    ds = _fit_ds(tmp_path, n=2)
    monkeypatch.setattr(AA, "_chunk_values", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    s = AA.wait(ds)
    assert s["state"] == "error" and "boom" in s["error"]
    monkeypatch.undo()
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(AT, "load_recorded", lambda *a, **k: [])
    assert AA.wait(ds)["state"] == "ready"              # the next request starts again


def test_endpoint_never_waits(tmp_path, no_plan, monkeypatch):
    from backend.api import wko5views as V
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    gate, calls = threading.Event(), []
    _slow(monkeypatch, gate, calls)
    t = time.monotonic()
    r = asyncio.new_event_loop().run_until_complete(V.activities_auto())
    assert time.monotonic() - t < 5 and r["state"] == "computing"
    assert {"n_done", "n_total", "auto", "stale"} <= set(r)
    gate.set()
    AA.wait(ds)
    r = asyncio.new_event_loop().run_until_complete(V.activities_auto())
    assert r["state"] == "ready" and len(r["auto"]) == len(ds.workouts)


def test_page_polls_while_computing():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] / "static" / "activity.html").read_text(encoding="utf-8")
    assert 'r.state === "computing"' in page and "setTimeout(" in page and 'T("auto_progress"' in page


def test_batched_flush_writes_once_at_the_end(tmp_path, no_plan):
    from backend.engine.wko5expr.dataset import batched_flush
    ds = _fit_ds(tmp_path, n=2)
    with batched_flush(ds):
        for w in ds.workouts:
            ds.cached_series("t_k_x", w, lambda: {"v": 1})
            ds.flush_series()                           # held: no write inside
        assert not list(ds._store.home.glob("series_t_k_x*.json"))
    assert len(list(ds._store.home.glob("series_t_k_x*.json"))) == 1    # written on leaving
