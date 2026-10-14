"""cached_series / flush_series are shared by concurrent chart requests."""
import pytest
import threading
import types

from backend.engine.wko5expr import dataset as D


@pytest.mark.needs_core
def test_cached_series_is_thread_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_CACHE_DIR", tmp_path)
    f = tmp_path / "a.wko4"
    f.write_bytes(b"x")
    ds = D.Dataset.__new__(D.Dataset)          # just the cache machinery
    ds.dir = tmp_path
    ds._series, ds._series_lock = {}, threading.Lock()
    ds._corr_sig = lambda *a: ""
    ds._settings_sig = lambda w: ""
    ws = [types.SimpleNamespace(entry=types.SimpleNamespace(file="a.wko4"))]
    errors = []

    def worker(n):
        try:
            for i in range(200):
                ds.cached_series(f"k{(n + i) % 7}", ws[0], lambda: i)
                ds.flush_series()
        except Exception as e:           # the old code raised "Set changed size during iteration"
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    # SP-339: rows of one SQLite store (seriesdb.py), every key written, no JSON file
    import sqlite3
    from backend.engine.wko5expr import seriesdb
    con = sqlite3.connect(str(tmp_path / seriesdb.FILE_NAME))
    try:
        assert con.execute("SELECT COUNT(DISTINCT key) FROM series").fetchone()[0] == 7
    finally:
        con.close()
    assert not list(tmp_path.glob("series_*.json"))
