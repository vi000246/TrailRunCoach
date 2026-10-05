"""Memory regressions: a Dataset that nothing references any more is freed
(its per-instance method caches go with it), and a locked DB keeps the last
db_stamp instead of flipping the Dataset cache key."""
import gc
import sqlite3
import weakref

from backend.engine.wko5expr import datasource
from backend.engine.wko5expr.dataset import Dataset, instance_lru


class _Holder:
    calls = 0

    @instance_lru(2)
    def f(self, x):
        type(self).calls += 1
        return [x] * 1000


def test_instance_lru_caches_per_instance_and_evicts():
    a, b = _Holder(), _Holder()
    _Holder.calls = 0
    assert a.f(1) is a.f(1)
    assert _Holder.calls == 1
    b.f(1)
    assert _Holder.calls == 2                  # not shared across instances
    a.f(2), a.f(3)                             # maxsize 2: 1 evicted
    a.f(1)
    assert _Holder.calls == 5


def test_cached_method_does_not_keep_instance_alive():
    h = _Holder()
    h.f(1)
    ref = weakref.ref(h)
    del h
    gc.collect()
    assert ref() is None


def test_dataset_methods_use_instance_caches():
    for name in ("_workout_curves", "curve_cache", "wko4"):
        assert not hasattr(getattr(Dataset, name), "cache_clear"), name


def test_db_stamp_keeps_last_good_value_when_locked(tmp_path, monkeypatch):
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE workout_files (trail_classification, classification_overridden, duplicate_of)")
    con.execute("INSERT INTO workout_files VALUES ('trail', 0, NULL)")
    con.commit()
    con.close()
    monkeypatch.setattr(datasource, "_db_path", lambda: db)
    good = datasource.db_stamp()
    assert good

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(datasource.sqlite3, "connect", locked)
    assert datasource.db_stamp() == good
