"""SP-753: the overview status after a sync was expensive in itself (2.1 CPU s on the same Dataset, 523
runs): zone_events.run_heat — behind the 測試 indicator and threshold_confidence.lthr_evidence — read
every run's temperature channel from the npz each time, and activity_key.rebase_stored asked the
settings DB (current_source) for every stored session. run_heat now takes watch_temp through the
Dataset's disk-memoised per-file series (the same rows dataset_watch_bias already kept), and the
status computes inside datasource.settings_memo(). Results unchanged; the before / after numbers of
backend/scripts/perf_sp342.py are on the ticket."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import zone_events as ZE
from backend.engine.wko5expr import datasource as DS
from backend.tests.test_zone_events import _ds, _run


class _Series:
    """A Dataset's cached_series / flush_series stand-in shared across 'Datasets' (the disk store)."""

    def __init__(self):
        self.rows, self.flushed = {}, 0

    def bind(self, ds):
        def cached_series(key, w, compute, extra=None):
            k = (key, w.entry.file)
            if k not in self.rows:
                self.rows[k] = compute()
            return self.rows[k]

        def flush_series():
            self.flushed += 1
        ds.cached_series, ds.flush_series = cached_series, flush_series
        return ds


def _setup():
    d = dt.date(2026, 7, 10)
    runs = [_run(i, d + dt.timedelta(days=i)) for i in range(4)]
    acts = [{"file": "0.fit", "date": d.isoformat(), "temp_c": 29.0, "rh_pct": 75.0, "hadley": 156.0}]
    acts += [{"file": f"old{k}.fit", "date": (d - dt.timedelta(days=365 + k)).isoformat(), "temp_c": 29.0,
              "rh_pct": 80.0, "hadley": 150.0 + k} for k in range(-5, 6)]
    return runs, acts, {0: 34.0, 1: 33.7, 2: float("nan"), 3: 31.2}


def test_run_heat_reads_each_runs_temperature_once_across_datasets():
    runs, acts, watch = _setup()
    plain = ZE.run_heat(_ds(runs, watch), runs, acts)
    store = _Series()
    reads = []

    def counting(ds):
        real = ds.channel
        ds.channel = lambda i, n: reads.append((i, n)) or real(i, n)
        return ds
    first = ZE.run_heat(store.bind(counting(_ds(runs, watch))), runs, acts)
    assert first == plain                                    # the same answer as without the store
    n = len(reads)
    assert n > 0 and store.flushed == 1
    again = ZE.run_heat(store.bind(counting(_ds(runs, watch))), runs, acts)   # a new Dataset after a sync
    assert again == plain and len(reads) == n                # no channel read again
    assert {k[0] for k in store.rows} == {ZE.WATCH_TEMP_SERIES}


def test_a_failing_store_falls_back_to_reading_the_channel():
    runs, acts, watch = _setup()
    ds = _ds(runs, watch)

    def broken(*a, **k):
        raise OSError("disk full")
    ds.cached_series, ds.flush_series = broken, broken
    assert ZE.run_heat(ds, runs, acts) == ZE.run_heat(_ds(runs, watch), runs, acts)


def test_the_status_computes_inside_the_settings_memo(monkeypatch):
    from backend.api import overview as OV
    seen = []

    class St:
        def __init__(self, ds, today=None, prefs=None):
            pass

        def compute(self):
            seen.append(DS._SETTINGS_MEMO.get() is not None)
            return SimpleNamespace()
    monkeypatch.setattr(OV, "Status", St)
    monkeypatch.setattr(OV, "_status_key", lambda ds, today: (("t-sp753", object()), None))
    monkeypatch.setattr(OV, "_remember_status", lambda key, st: None)
    from backend import jobs
    monkeypatch.setattr(jobs, "call", lambda fn, **k: fn())
    OV._status(object(), dt.date(2026, 10, 19))
    assert seen == [True] and DS._SETTINGS_MEMO.get() is None


def test_rebase_stored_asks_for_the_source_once_inside_the_memo(monkeypatch):
    from backend.engine import activity_key as AK
    calls = []
    monkeypatch.setattr(DS, "_db_path", lambda: None)            # no DB: "wko5" when the folder is there
    monkeypatch.setattr(DS, "wko5_available", lambda d=None: calls.append(1) or True)
    with DS.settings_memo():
        for _ in range(30):
            AK.rebase_stored([])
    assert len(calls) == 1
