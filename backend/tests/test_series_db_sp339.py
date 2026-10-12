"""SP-339 (SP-320 ⑧): Dataset.cached_series as SQLite rows (engine/wko5expr/seriesdb.py) keyed on
the activity file's content hash, the code the compute reaches (engine/codehash.py) and the row's
other inputs — one sync writes its own rows, not every series_*.json whole; a changed function
recomputes only the keys reaching it; the old JSON files move in once, with the same values; each
tenant has its own store; old rows go after a warm-up (SP-338 第 4 條). Synthetic FIT files only."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path

from backend import tenancy
from backend.engine import codehash as C
from backend.engine.wko5expr import dataset as D
from backend.engine.wko5expr import fitcache, seriesdb
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.files import fitstore
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 9, 30)


def _fit(i: int) -> bytes:
    return build_run(datetime(2026, 9, 1 + i, 8, tzinfo=timezone.utc), seconds=600, power=200 + 10 * i,
                     hr=140 + i, speed_m_s=3.0)


def _folder(base: Path, n: int = 3) -> Path:
    d = base / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    for i in range(n):
        (d / f"{i}.fit").write_bytes(_fit(i))
    return base / "fit" / "coros"


def _build(root: Path) -> FitFolderDataset:
    return FitFolderDataset(root, config=EngineConfig(parity=False), today=TODAY, classifications={},
                            athlete_settings=[{"effective_date": "2026-01-01", "weight_kg": 60}],
                            estimate_thresholds=False)


def _rows(db: Path, where: str = "1", args=()) -> list:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(f"SELECT key, fid, code, sig, file, written, value FROM series WHERE {where} "
                           "ORDER BY key, file", args).fetchall()
    finally:
        con.close()


def _db(ds) -> Path:
    return ds._store.home / seriesdb.FILE_NAME


# ---------------------------------------------------------------------------- read / write

def test_values_are_rows_keyed_on_the_file_content_hash(tmp_path):
    root = _folder(tmp_path)
    a = _build(root)
    calls = []
    for w in a.workouts:
        assert a.cached_series("probe_v1", w, lambda w=w: calls.append(1) or {"f": w.entry.file, "x": 1.5}) \
            == {"f": w.entry.file, "x": 1.5}
    a.flush_series()
    assert len(calls) == 3
    assert not list(a._store.home.glob("series_*.json"))                  # no JSON file any more
    rows = _rows(_db(a))
    assert len(rows) == 3 and {r[0] for r in rows} == {D._safe("probe_v1")}
    for key, fid, code, sig, file, written, value in rows:
        e = a._store.entry(root / file)
        assert fid == "sha1:" + e["sha"]                                   # the bytes' hash, not the stamp
        assert json.loads(value) == {"f": file, "x": 1.5}
    b = _build(root)                                                       # a new Dataset object reads them
    got = [b.cached_series("probe_v1", w, lambda: calls.append(1) or "computed") for w in b.workouts]
    assert len(calls) == 3 and got == [{"f": w.entry.file, "x": 1.5} for w in b.workouts]


def test_a_fit_object_row_is_keyed_on_its_sha256(tmp_path):
    raw = _fit(0)
    sha = fitstore.sha256_hex(raw)
    root = tmp_path / "fit" / "coros"
    p = root / fitstore.object_key("coros", sha).split("/", 1)[1]
    p.parent.mkdir(parents=True)
    p.write_bytes(fitstore.gzip_fit(raw))
    ds = _build(root)
    ds.cached_series("probe_v1", ds.workouts[0], lambda: 7)
    ds.flush_series()
    assert [r[1] for r in _rows(_db(ds))] == ["sha256:" + sha]


def test_one_sync_writes_only_its_own_rows(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    a = _build(root)
    for key in ("k1_v1", "k2_v1"):
        for w in a.workouts:
            a.cached_series(key, w, lambda w=w: {"v": [w.entry.file] * 50})
    a.flush_series()
    before = {(r[0], r[4]): r[5] for r in _rows(_db(a))}
    (root / "2026" / "new.fit").write_bytes(_fit(5))                       # one synced activity
    put = []
    real = seriesdb.SeriesDB.put

    def spy(self, rows, *a, **kw):
        rows = list(rows)
        put.append([(r[0], r[4]) for r in rows])
        return real(self, rows, *a, **kw)
    monkeypatch.setattr(seriesdb.SeriesDB, "put", spy)
    time.sleep(0.01)
    b = _build(root)
    computed = []
    for key in ("k1_v1", "k2_v1"):
        for w in b.workouts:
            b.cached_series(key, w, lambda w=w: computed.append(w.entry.file) or {"v": [w.entry.file] * 50})
    b.flush_series()
    assert computed == ["2026/new.fit", "2026/new.fit"]
    assert put == [[(D._safe("k1_v1"), "2026/new.fit"), (D._safe("k2_v1"), "2026/new.fit")]]
    after = {(r[0], r[4]): r[5] for r in _rows(_db(b))}
    assert {k: after[k] for k in before} == before                        # the other rows were not rewritten


def test_threshold_variants_per_file_are_capped(tmp_path):
    root = _folder(tmp_path, n=1)
    ds = _build(root)
    w = ds.workouts[0]
    for i in range(ds.SERIES_VARIANTS + 2):
        ds.athlete.settings["runthr"] = [(dt.date(2020, 1, 1), 150.0 + i)]
        assert ds.cached_series("probe_v1", w, lambda i=i: i) == i
        ds.flush_series()
        time.sleep(0.002)
    rows = _rows(_db(ds))
    assert len(rows) == ds.SERIES_VARIANTS
    assert sorted(json.loads(r[6]) for r in rows) == [2, 3, 4, 5]          # the newest kept
    again = _build(root)                                                   # a new object, the newest variant
    again.athlete.settings["runthr"] = [(dt.date(2020, 1, 1), 155.0)]
    assert again.cached_series("probe_v1", again.workouts[0], lambda: "again") == 5


def test_readers_and_writers_share_one_store(tmp_path):
    root = _folder(tmp_path, n=3)
    a, b = _build(root), _build(root)
    keys = [f"k{i}_v1" for i in range(6)]
    errors = []

    def val(key, w):
        return {"k": key, "f": w.entry.file}

    def work(ds, n):
        try:
            for i in range(60):
                key = keys[(n + i) % len(keys)]
                for w in ds.workouts:
                    assert ds.cached_series(key, w, lambda key=key, w=w: val(key, w)) == val(key, w)
                ds.flush_series()
        except Exception as e:                       # noqa: BLE001 — reported below
            errors.append(e)

    ts = [threading.Thread(target=work, args=(a if n % 2 else b, n)) for n in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    c = _build(root)
    for key in keys:
        for w in c.workouts:
            assert c.cached_series(key, w, lambda: "computed") == val(key, w)


# ---------------------------------------------------------------------------- code versions

LIB = '''
def helper(x):
    return x + 1


def other(x):
    return x * 2


def unrelated():
    return 0


def run(ds, w, log):
    a = ds.cached_series("fx_a_v1", w, lambda: log.append("a") or helper(1))
    b = ds.cached_series("fx_b_v1", w, lambda: log.append("b") or other(1))
    ds.flush_series()
    return a, b
'''


def _fixture(tmp_path, variant, src):
    d = tmp_path / variant
    d.mkdir()
    p = d / "_sp339_fx.py"
    p.write_text(src)
    spec = importlib.util.spec_from_file_location("backend._sp339_fx", p)
    m = importlib.util.module_from_spec(spec)
    sys.modules["backend._sp339_fx"] = m
    spec.loader.exec_module(m)
    C._ASSIGN.clear()
    C._MEMO.clear()
    C._CALLABLE.clear()
    return m


def test_a_changed_function_recomputes_only_the_keys_reaching_it(tmp_path):
    root = _folder(tmp_path, n=1)
    try:
        log = []
        m = _fixture(tmp_path, "a", LIB)
        ds = _build(root)
        assert m.run(ds, ds.workouts[0], log) == (2, 2) and log == ["a", "b"]
        log.clear()
        for variant, src in (("b", LIB + "\n\ndef added():\n    return 1\n"),       # a new function
                             ("c", LIB.replace("return 0", "return 5"))):            # an unrelated edit
            m = _fixture(tmp_path, variant, src)
            ds = _build(root)
            assert m.run(ds, ds.workouts[0], log) == (2, 2) and log == []
        m = _fixture(tmp_path, "d", LIB.replace("return x + 1", "return x + 10"))
        ds = _build(root)
        assert m.run(ds, ds.workouts[0], log) == (11, 2) and log == ["a"]   # only the key reaching helper
        # no manual _vN bump needed, and the manual constant still forces everything
        log.clear()
        ds = _build(root)
        D_v = D.SERIES_V
        try:
            D.SERIES_V = D_v + 1
            assert m.run(ds, ds.workouts[0], log) == (11, 2) and log == ["a", "b"]
        finally:
            D.SERIES_V = D_v
    finally:
        sys.modules.pop("backend._sp339_fx", None)
        C._CALLABLE.clear()


def test_the_code_hash_follows_what_the_closure_holds():
    """A call-site lambda reaches its work through closure cells: a local `import … as A`, a
    nested function, an object of ours whose methods it calls by name. Its defaults (`w=w`) are
    inputs, not code: two activities give one hash."""
    from backend.engine.racepower import athlete as A
    ws = [types.SimpleNamespace(i=1), types.SimpleNamespace(i=2)]

    def site(w):
        from backend.engine.racepower import athlete as AA
        return lambda w=w: AA._hike_windows(None, w)
    roots, ctx = C.callable_roots(site(ws[0]))
    names = C.closure(roots, ctx)
    assert "backend.engine.racepower.athlete._hike_windows" in names
    assert C.callable_hash(site(ws[0])) == C.callable_hash(site(ws[1]))
    assert C.callable_hash(site(ws[0])) != C.callable_hash(lambda: A._grade_windows(None, None))


def test_row_code_versions_do_not_depend_on_the_hash_seed():
    """A real call site's code version is the same in every process (a restart must not drop
    every row): b2b._windows's lambda over a local `import … as A`."""
    import subprocess
    code = ("import types; from backend.engine import b2b, codehash as C;"
            "from backend.engine.wko5expr.fitdataset import FitFolderDataset as F;"
            "got = []; ds = types.SimpleNamespace(cached_series=lambda k, w, f: got.append(f) or []);"
            "b2b._windows(ds, None); roots, ctx = C.callable_roots(got[0]);"
            "print(C.callable_hash(got[0], context=(F,), extra=('series', 1)),"
            " 'backend.engine.racepower.athlete._hike_windows' in C.closure(roots, [F] + ctx))")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    outs = {subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": "utf-8"}).stdout
            for seed in ("1", "2")}
    assert len(outs) == 1 and next(iter(outs)).split()[1] == "True"


def test_cache_plumbing_is_not_part_of_any_code_hash():
    """Memoising a value does not change it: an edit to cached_series / flush_series must not
    drop every value cached through them."""
    from backend.engine.wko5expr.dataset import Dataset

    def uses_cache(ds, w):
        ds.cached_series("x", w, lambda: 1)
        ds.flush_series()
    names = C.closure([uses_cache], [Dataset, FitFolderDataset])
    assert not [n for n in names if n.split(":")[-1] in C.PLUMBING]
    assert "backend.engine.wko5expr.dataset.Dataset.cached_series" in C.PLUMBING


# ---------------------------------------------------------------------------- the move from JSON

def _old_fit_json(ds, key, values: dict, extra=None, stale: bool = False) -> Path:
    """series_<key>.json as the FIT dataset wrote it before SP-339: {"files": {rel: {sk: value}}},
    sk = json [size, mtime_ns, corrections, thresholds(, extra)] (FitFolderDataset.cached_series)."""
    files = {}
    for w in ds.workouts:
        rel = w.entry.file
        st = fitcache.stamp_of(ds.dir / rel)
        sk = json.dumps(st + [ds._corr_sig(rel), ds._settings_sig(w)] + ([] if extra is None else [extra]))
        files[rel] = {sk: values[rel]}
        if stale:                                                          # the file as it was before
            old = json.dumps([st[0] + 1, st[1]] + [ds._corr_sig(rel), ds._settings_sig(w)])
            files[rel] = {old: "stale", **files[rel]}
    p = ds._store.home / f"series_{D._safe(key)}.json"
    p.write_text(json.dumps({"files": files}), "utf-8")
    return p


def test_old_json_moves_into_rows_once_with_the_same_values(tmp_path):
    root = _folder(tmp_path)
    a = _build(root)
    values = {w.entry.file: {"drift": 3.5 + w.idx, "rows": [[1, 2.5], [3, None]]} for w in a.workouts}
    p = _old_fit_json(a, "workout_review_v23", values, stale=True)
    q = _old_fit_json(a, "tis_v1", {f: i for i, f in enumerate(values)}, extra="win:abc")
    b = _build(root)
    calls = []
    got = {w.entry.file: b.cached_series("workout_review_v23", w, lambda: calls.append(1)) for w in b.workouts}
    assert got == values and calls == [] and not p.exists()               # the same values, the file gone
    assert [b.cached_series("tis_v1", w, lambda: calls.append(1), extra="win:abc") for w in b.workouts] \
        == [0, 1, 2] and calls == [] and not q.exists()
    assert len(_rows(_db(b), "key = ?", (D._safe("workout_review_v23"),))) == 3     # the stale variant left out
    c = _build(root)                                                        # from the rows now
    assert {w.entry.file: c.cached_series("workout_review_v23", w, lambda: calls.append(1))
            for w in c.workouts} == values and calls == []


def test_the_move_is_idempotent_and_a_failed_one_recomputes(tmp_path, monkeypatch):
    root = _folder(tmp_path)
    a = _build(root)
    values = {w.entry.file: w.idx * 10 for w in a.workouts}
    p = _old_fit_json(a, "k_v1", values)
    keep = p.read_bytes()
    b = _build(root)
    assert [b.cached_series("k_v1", w, lambda: "x") for w in b.workouts] == [0, 10, 20] and not p.exists()
    # killed after the rows were written, before the file was deleted: moved again, no error
    p.write_bytes(keep)
    seriesdb.forget_all()
    c = _build(root)
    assert [c.cached_series("k_v1", w, lambda: "x") for w in c.workouts] == [0, 10, 20] and not p.exists()
    assert len(_rows(_db(c))) == 3
    # the store cannot be opened (a full disk, a broken file): the values are computed, the file stays
    p2 = _old_fit_json(c, "other_v1", values)

    def broken(self):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(seriesdb.SeriesDB, "_open", broken)
    seriesdb.forget_all()
    d = _build(root)
    assert [d.cached_series("other_v1", w, lambda w=w: f"computed {w.idx}") for w in d.workouts] \
        == ["computed 0", "computed 1", "computed 2"]
    d.flush_series()
    assert p2.exists()


def test_a_damaged_store_is_rebuilt(tmp_path):
    root = _folder(tmp_path, n=1)
    ds = _build(root)
    ds._store.home.mkdir(parents=True, exist_ok=True)
    _db(ds).write_bytes(b"this is not a database at all" * 100)
    seriesdb.forget_all()
    assert ds.cached_series("k_v1", ds.workouts[0], lambda: 4) == 4
    ds.flush_series()
    assert [json.loads(r[6]) for r in _rows(_db(ds))] == [4]


def test_the_wko5_dataset_moves_its_old_format_too(tmp_path, monkeypatch):
    """The WKO5 Dataset's old series_<key>.json: {"files": {file: [size, mtime, corrections,
    thresholds(, extra), value]}} — one variant per file."""
    monkeypatch.setattr(D, "_CACHE_DIR", tmp_path)
    for n in ("a.wko4", "b.wko4"):
        (tmp_path / n).write_bytes(n.encode())
    ds = D.Dataset.__new__(D.Dataset)
    ds.dir = tmp_path
    ds._series, ds._series_lock = {}, threading.Lock()
    ds._corr_sig = lambda *a: ""
    ds._settings_sig = lambda w: "170,,"
    ws = [types.SimpleNamespace(entry=types.SimpleNamespace(file=n)) for n in ("a.wko4", "b.wko4", "gone.wko4")]
    files = {"a.wko4": D._file_stamp(tmp_path / "a.wko4") + ["", "170,,", {"v": 1}],
             "b.wko4": [1, 2, "", "170,,", "stale"]}
    p = tmp_path / f"series_{D._safe('k_v3')}.json"
    p.write_text(json.dumps({"files": files}), "utf-8")
    assert ds.cached_series("k_v3", ws[0], lambda: "computed") == {"v": 1}
    assert ds.cached_series("k_v3", ws[1], lambda: "computed") == "computed"
    assert ds.cached_series("k_v3", ws[2], lambda: "computed") is None     # no file: as before
    ds.flush_series()
    assert not p.exists() and len(_rows(tmp_path / seriesdb.FILE_NAME)) == 2


# ---------------------------------------------------------------------------- tenants

def test_two_tenants_never_read_each_others_rows(tmp_path, monkeypatch):
    """The same FIT bytes (the same content hash) in two tenants: each has its own store."""
    monkeypatch.delenv(fitcache.ENV_ROOT, raising=False)
    fitcache._HOMES.clear()
    ts = [tenancy.Tenant(id=f"u{i}", kind=tenancy.USER, root=tmp_path / f"u{i}", shared=tmp_path / f"u{i}")
          for i in (1, 2)]
    got, dbs = [], []
    for t in ts:
        with tenancy.use(t):
            root = _folder(t.shared, n=1)
            ds = _build(root)
            got.append(ds.cached_series("probe_v1", ds.workouts[0], lambda t=t: t.id))
            ds.flush_series()
            dbs.append(_db(ds))
    assert got == ["u1", "u2"]
    assert dbs[0] != dbs[1] and all(Path(t.shared) in db.parents for t, db in zip(ts, dbs))
    fitcache._HOMES.clear()


# ---------------------------------------------------------------------------- old rows (SP-338)

def test_old_rows_go_after_a_warm_up(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "SERIES_SEEN", set())
    monkeypatch.setattr(D, "SERIES_ROWS_SEEN", {})
    week3 = time.time() - 21 * 86400
    db = seriesdb.open_db(tmp_path / "a" / seriesdb.FILE_NAME)
    db.put([("k_v2", "f1", "new", "s", "1.fit", 1)])                       # the current version: read
    db.put([("k_v2", "f1", "older", "s", "1.fit", 1)], written=week3)     # an older code version of it
    db.put([("k_v1", "f1", "c", "s", "1.fit", 1)], written=week3)         # a key bumped long ago
    db.put([("x_v1", "f1", "c", "s", "1.fit", 1)])                         # unread, but written lately
    other = seriesdb.open_db(tmp_path / "b" / seriesdb.FILE_NAME)          # nobody read from it
    other.put([("k_v1", "f1", "c", "s", "1.fit", 1)], written=week3)
    left_json = tmp_path / "a" / "series_k_v0_1234abcd.json"               # never moved in: an old version
    left_json.write_text("{}", "utf-8")
    os.utime(left_json, (week3, week3))
    D.series_rows_seen(db.path, "k_v2", "new")
    assert D.sweep_series() == 3                                           # two rows and the file
    assert sorted((r[0], r[2]) for r in _rows(db.path)) == [("k_v2", "new"), ("x_v1", "c")]
    assert len(_rows(other.path)) == 1 and not left_json.exists()


def test_no_series_file_is_written_any_more(tmp_path):
    root = _folder(tmp_path, n=1)
    ds = _build(root)
    with D.batched_flush(ds):
        ds.cached_series("probe_v1", ds.workouts[0], lambda: 1)
    assert [p.name for p in ds._store.home.iterdir() if p.name.startswith("series")] \
        and not list(ds._store.home.glob("series_*.json"))
    shutil.rmtree(ds._store.home)                                          # a cache folder deleted by hand
    e = _build(root)
    assert e.cached_series("probe_v1", e.workouts[0], lambda: "again") == "again"
