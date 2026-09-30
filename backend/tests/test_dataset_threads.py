"""cached_series / flush_series are shared by concurrent chart requests."""
import threading
import types

from backend.engine.wko5expr import dataset as D


def test_cached_series_is_thread_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "_CACHE_DIR", tmp_path)
    f = tmp_path / "a.wko4"
    f.write_bytes(b"x")
    ds = D.Dataset.__new__(D.Dataset)          # just the cache machinery
    ds.dir = tmp_path
    ds._series, ds._series_dirty, ds._series_lock = {}, set(), threading.Lock()
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
    assert len(list(tmp_path.glob("series_*.json"))) == 7
