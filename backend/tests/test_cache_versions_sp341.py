"""SP-341: caches that never noticed a code change get a code version (channel_peaks /
workout_curves / the WKO5 power-source and bad-file caches, the race-power power-source and
bad-file caches, mmp_cache); pmc_cache is dropped from every tenant DB; the achievements cache
drops activities whose file is gone and is written atomically. Synthetic files in tmp_path only."""
import asyncio
import datetime as dt
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.engine import codehash as CH
from backend.engine.wko5expr import dataset as D
from backend.tests.fit_builder import build_run

T0 = datetime(2026, 9, 1, 7, tzinfo=timezone.utc)
PROFILE = [400] * 180 + [300] * 720 + [220] * 1200          # a mean-max curve the CP fit can use


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- dataset.py per-workout caches
class _Stub:
    """What Dataset._cached_per_workout / _cached_power_source read of a dataset."""

    def __init__(self, folder: Path, files):
        self.dir = folder
        self.workouts = [SimpleNamespace(idx=i, entry=SimpleNamespace(file=f)) for i, f in enumerate(files)]
        self.calls = 0

    def _corr_sig(self, file, channel=None):
        return ""

    def wko4(self, idx):
        return object()

    def _compute_power_source(self, w):
        self.calls += 1
        return "stryd"


@pytest.fixture
def ds_files(tmp_path, monkeypatch):
    folder = tmp_path / "wko5"
    folder.mkdir()
    for f in ("a.wko4", "b.wko4"):
        (folder / f).write_bytes(b"x")
    monkeypatch.setattr(D, "_CACHE_DIR", tmp_path / "home")
    return folder


def test_per_workout_cache_recomputes_when_its_code_version_changes(ds_files, monkeypatch):
    calls = []

    def compute(w, f):
        calls.append(w.entry.file)
        return 1.0
    out = D.Dataset._cached_per_workout(_Stub(ds_files, ["a.wko4", "b.wko4"]), "channel_peaks", "power", compute)
    assert out == {"a.wko4": 1.0, "b.wko4": 1.0} and len(calls) == 2
    path = D._cache_dir() / "channel_peaks.json"
    assert json.loads(path.read_text("utf-8"))["v"] == D.per_workout_code("channel_peaks")
    D.Dataset._cached_per_workout(_Stub(ds_files, ["a.wko4", "b.wko4"]), "channel_peaks", "power", compute)
    assert len(calls) == 2                                      # same code: read from disk
    monkeypatch.setattr(D, "PER_WORKOUT_V", D.PER_WORKOUT_V + 1)   # the algorithm changed
    D.Dataset._cached_per_workout(_Stub(ds_files, ["a.wko4", "b.wko4"]), "channel_peaks", "power", compute)
    assert len(calls) == 4


def test_an_unversioned_cache_file_from_before_is_recomputed(ds_files):
    path = D._cache_dir() / "workout_curves.json"
    path.parent.mkdir(parents=True)
    stamp = D._file_stamp(ds_files / "a.wko4")
    path.write_text(json.dumps({"files": {"a.wko4|power": stamp + ["", [[1], [9]]]}}), "utf-8")
    calls = []
    out = D.Dataset._cached_per_workout(_Stub(ds_files, ["a.wko4"]), "workout_curves", "power",
                                        lambda w, f: calls.append(1) or [[1], [5]])
    assert calls == [1] and out == {"a.wko4": [[1], [5]]}


def test_wko5_power_source_cache_is_versioned(ds_files, monkeypatch):
    def once():
        s = _Stub(ds_files, ["a.wko4"])
        assert D.Dataset._cached_power_source(s, s.workouts[0]) == "stryd"
        D.Dataset._flush_power_sources(s)
        return s.calls
    assert once() == 1 and once() == 0
    monkeypatch.setattr(D, "PER_WORKOUT_V", D.PER_WORKOUT_V + 1)
    assert once() == 1


def test_the_versions_follow_the_algorithm_not_unrelated_code(monkeypatch):
    from backend.engine.wko5expr.corrections import CorrectionStore
    from backend.files.wko4_file import read_wko4
    from backend.engine import bad_activity as BA
    parts = {n: CH.closure([r, read_wko4, CorrectionStore.apply]) for n, r in (
        ("channel_peaks", D.Dataset.channel_peaks), ("workout_curves", D.Dataset._workout_curves),
        ("power_source", D.Dataset._compute_power_source), ("bad_activity", BA.features))}
    assert "backend.engine.algorithms.wko5_meanmax.meanmax_time" in parts["workout_curves"]
    assert "backend.engine.power_source.classify" in parts["power_source"]
    assert "backend.engine.bad_activity.features" in parts["bad_activity"]
    assert "backend.engine.wko5expr.corrections.CorrectionStore.apply" in parts["channel_peaks"]
    for p in parts.values():                                  # no path plumbing in the hash
        assert not any(k.startswith(("backend.tenancy", "backend.demo")) for k in p)
    names = ("channel_peaks", "workout_curves", "power_source", "bad_activity")
    before = {n: D.per_workout_code(n) for n in names}
    assert len(set(before.values())) == 4
    monkeypatch.setattr(D.Dataset, "channel_peaks", lambda self, channel="power": {})   # a new algorithm
    assert D.per_workout_code("channel_peaks") != before["channel_peaks"]
    assert D.per_workout_code("workout_curves") == before["workout_curves"]


# ---------------------------------------------------------------- racepower/cptest.py file caches
@pytest.fixture
def fit_home(tmp_path):
    root = tmp_path / "home" / "fit" / "coros" / "2026"
    root.mkdir(parents=True)
    (root / "1_2026-09-01_run.fit").write_bytes(build_run(T0, seconds=600, power=210, stryd=True))
    (root / "2_2026-09-02_run.fit").write_bytes(build_run(T0 + dt.timedelta(days=1), seconds=600, power=300))
    return tmp_path / "home", ["coros/2026/1_2026-09-01_run.fit", "coros/2026/2_2026-09-02_run.fit"]


def test_racepower_power_source_cache_is_versioned(fit_home, monkeypatch):
    from backend.engine.racepower import cptest as T
    home, paths = fit_home
    calls = []
    real = T._classify_file
    monkeypatch.setattr(T, "_classify_file", lambda *a, **k: calls.append(1) or real(*a, **k))
    first = T.power_sources(home, paths)
    assert len(calls) == 2 and sorted(first.values()) == ["stryd", "watch"]
    doc = json.loads((home / T.POWER_CACHE_NAME).read_text("utf-8"))
    assert doc["v"] == T.power_cache_code() and set(doc["files"]) == set(paths)
    assert T.power_sources(home, paths) == first and len(calls) == 2
    monkeypatch.setattr(T, "FILE_CACHE_V", T.FILE_CACHE_V + 1)
    assert T.power_sources(home, paths) == first and len(calls) == 4


def test_racepower_bad_activity_cache_is_versioned_and_drops_the_old_form(fit_home, monkeypatch):
    from backend.engine.racepower import cptest as T
    home, paths = fit_home
    # the pre-SP-341 flat form {path: [size, mtime, start, group, features]}: not trusted
    (home / T.BAD_CACHE_NAME).write_text(json.dumps({p: [0, 0, None, None, None] for p in paths}), "utf-8")
    calls = []
    real = T._bad_entry
    monkeypatch.setattr(T, "_bad_entry", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert T.bad_files(home, paths, enabled=True, tags=[]) == {}
    assert len(calls) == 2
    assert json.loads((home / T.BAD_CACHE_NAME).read_text("utf-8"))["v"] == T.bad_cache_code()
    T.bad_files(home, paths, enabled=True, tags=[])
    assert len(calls) == 2
    monkeypatch.setattr(T, "FILE_CACHE_V", T.FILE_CACHE_V + 1)
    T.bad_files(home, paths, enabled=True, tags=[])
    assert len(calls) == 4


def test_racepower_cache_write_is_atomic(fit_home, monkeypatch):
    from backend.engine.racepower import cptest as T
    home, paths = fit_home
    T.power_sources(home, paths[:1])
    p = home / T.POWER_CACHE_NAME
    before = p.read_text("utf-8")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(T.os, "replace", boom)
    T.power_sources(home, paths)                              # a new entry: the write fails
    assert p.read_text("utf-8") == before and json.loads(before)["files"]
    assert not list(home.glob("*.tmp"))


# ---------------------------------------------------------------- mmp_cache
async def _mmp_session(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.db.models import Athlete, Base, MmpCache, WorkoutFile
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = async_sessionmaker(eng, expire_on_commit=False)()
    s.add(Athlete(id=1, name="t", data_dir=str(tmp_path)))
    f1 = tmp_path / "1_2026-09-01_run.fit"
    f1.write_bytes(build_run(T0, power=PROFILE))
    s.add(WorkoutFile(id=1, athlete_id=1, file_path=str(f1), file_format="fit", sport="running",
                      workout_date=dt.date(2026, 9, 1)))
    s.add(WorkoutFile(id=2, athlete_id=1, file_path=str(tmp_path / "gone.fit"), file_format="fit",
                      sport="running", workout_date=dt.date(2026, 9, 2)))
    await s.flush()
    for wid in (1, 2):                                        # rows from before the version column
        for dur in (180, 720, 1200):
            s.add(MmpCache(workout_id=wid, channel="power", duration_s=dur, value=999.0, version=None))
    await s.commit()
    return s, eng, f1


def test_mmp_rows_of_another_code_version_are_recomputed(tmp_path, monkeypatch):
    from sqlalchemy import select
    from backend.db.models import MmpCache
    from backend.engine.algorithms.metrics import compute_run_ftp_from_mmp
    from backend.engine.algorithms.mmp import compute_mmp
    from backend.files import file_service as FS
    from backend.files.fit_reader import parse_fit

    async def go():
        s, eng, f1 = await _mmp_session(tmp_path)
        raw = parse_fit(str(f1))
        want = {d: v for d, v in compute_mmp(raw.power_w, raw.time_s).items() if v > 0}
        ftp = await FS.get_run_ftp(s, 1, dt.date(2026, 9, 10))
        rows = (await s.execute(select(MmpCache))).scalars().all()
        assert {r.workout_id for r in rows} == {1}                       # the unreadable file's rows dropped
        assert {r.version for r in rows} == {FS.mmp_version()}
        assert {r.duration_s: r.value for r in rows} == want             # not the stale 999 W
        assert ftp == compute_run_ftp_from_mmp(want) and ftp < 999
        old = FS.mmp_version()
        monkeypatch.setattr(FS, "MMP_CACHE_V", FS.MMP_CACHE_V + 1)       # the algorithm changed
        assert FS.mmp_version() != old
        assert await FS.get_run_ftp(s, 1, dt.date(2026, 9, 10)) == ftp
        rows = (await s.execute(select(MmpCache))).scalars().all()
        assert {r.version for r in rows} == {FS.mmp_version()} and len(rows) == len(want)
        await s.close()
        await eng.dispose()
    run(go())


def test_mmp_version_follows_the_mean_max_code():
    from backend.files import file_service as FS
    parts = CH.closure([FS.compute_mmp, FS.parse_fit])
    assert "backend.engine.algorithms.mmp.compute_mmp" in parts
    assert FS.mmp_version() == FS.mmp_version() and FS.mmp_version().startswith(f"{FS.MMP_CACHE_V}:")


def test_import_writes_mmp_rows_with_the_version(tmp_path):
    from sqlalchemy import select
    from backend.db.models import MmpCache
    from backend.files import file_service as FS
    from backend.tests.test_sync_e2e import make_session

    async def go():
        s = await make_session(tmp_path)
        f = tmp_path / "1_2026-09-01_run.fit"
        f.write_bytes(build_run(T0, power=PROFILE))
        wf = await FS._import_one_file(s, 1, f, source="coros")
        await s.commit()
        rows = (await s.execute(select(MmpCache).where(MmpCache.workout_id == wf.id))).scalars().all()
        assert rows and {r.version for r in rows} == {FS.mmp_version()}
        await s.close()
    run(go())


# ---------------------------------------------------------------- pmc_cache
OLD_PMC = ("CREATE TABLE pmc_cache (id INTEGER PRIMARY KEY, athlete_id INTEGER, date DATE, ctl FLOAT, "
           "atl FLOAT, tsb FLOAT, ramp_rate FLOAT, tss FLOAT, UNIQUE (athlete_id, date))")


def _init(monkeypatch, path: Path) -> set:
    from sqlalchemy import text
    from backend.db import database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        await db_mod.init_db()
        await db_mod.init_db()                                # idempotent, as every start-up
        async with db_mod.get_engine().begin() as conn:
            names = {r[0] for r in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            cols = {r[1] for r in await conn.execute(text("PRAGMA table_info(mmp_cache)"))}
        await db_mod.dispose(path)
        return names, cols
    return run(go())


def test_pmc_cache_is_dropped_from_an_old_tenant_db(tmp_path, monkeypatch):
    from backend.db.models import Base
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute(OLD_PMC)
    con.execute("INSERT INTO pmc_cache (athlete_id, date, ctl) VALUES (1, '2026-01-01', 50)")
    con.execute("CREATE TABLE mmp_cache (id INTEGER PRIMARY KEY, workout_id INTEGER, channel VARCHAR(30), "
                "duration_s INTEGER, value FLOAT)")                     # before the version column
    con.execute("INSERT INTO mmp_cache (workout_id, channel, duration_s, value) VALUES (1, 'power', 60, 300)")
    con.commit()
    con.close()
    names, cols = _init(monkeypatch, path)
    assert "pmc_cache" not in names and "pmc_cache" not in Base.metadata.tables
    assert "version" in cols
    con = sqlite3.connect(str(path))
    try:
        assert con.execute("SELECT value, version FROM mmp_cache").fetchall() == [(300.0, None)]   # kept, stale
    finally:
        con.close()


def test_migration_works_on_dbs_without_pmc_cache(tmp_path, monkeypatch):
    names, cols = _init(monkeypatch, tmp_path / "new.db")                      # a new tenant
    assert "pmc_cache" not in names and "version" in cols and "workout_files" in names
    part = tmp_path / "partial.db"                                             # a partial schema
    con = sqlite3.connect(str(part))
    con.execute("CREATE TABLE user_settings (id INTEGER PRIMARY KEY, user_id INT, key TEXT, value_json TEXT, "
                "updated_at TEXT)")
    con.commit()
    con.close()
    names, _ = _init(monkeypatch, part)
    assert "pmc_cache" not in names and "user_settings" in names


# ---------------------------------------------------------------- achievements cache
SUMMARY = {"moving_s": 3600.0, "elapsed_s": 4000.0, "top_m": 900.0, "peaks": [], "best_climb": None,
           "efd_km": 12.0, "avg_hr": 140, "days": [], "footprint": [[1, 2]]}


def _ach_ds(folder: Path, files):
    ws = [SimpleNamespace(idx=i, tags=["runningtrail"], sport="run", sport_type="trail",
                          entry=SimpleNamespace(file=f, start=datetime(2026, 9, 1 + i, 7)),
                          metrics={"distance": 10.0, "climbing": 500.0, "descending": 500.0})
          for i, f in enumerate(files)]
    return SimpleNamespace(dir=folder, workouts=ws, wko4=lambda idx: object())


@pytest.fixture
def ach(tmp_path, monkeypatch):
    from backend.engine import achievements as A
    monkeypatch.setattr(A, "CACHE_PATH", tmp_path / "home" / "achievements_cache.json")
    monkeypatch.setattr(A, "_summarise", lambda w, f, peaks: dict(SUMMARY))
    folder = tmp_path / "fit"
    folder.mkdir()
    for f in ("a.fit", "b.fit"):
        (folder / f).write_bytes(b"x")
    return A, folder


def test_achievements_cache_drops_activities_that_no_longer_exist(ach, tmp_path):
    A, folder = ach
    assert len(A.build_achievements(_ach_ds(folder, ["a.fit", "b.fit"]), peaks=[])) == 2
    cache = json.loads(A.cache_path().read_text("utf-8"))
    assert set(cache) == {"a.fit", "b.fit"} and cache["a.fit"]["path"] == str(folder / "a.fit")
    # another data source's entries: one whose file exists stays, one whose file is gone goes;
    # older entries without a path: kept (and given one) when the file is here, else dropped
    other = tmp_path / "wko5"
    other.mkdir()
    (other / "x.wko4").write_bytes(b"x")
    cache.update({"x.wko4": {"stamp": [1], "summary": None, "path": str(other / "x.wko4")},
                  "y.wko4": {"stamp": [1], "summary": None, "path": str(other / "y.wko4")},
                  "old.fit": {"stamp": [1], "summary": None}})
    del cache["a.fit"]["path"]
    A.cache_path().write_text(json.dumps(cache), "utf-8")
    (folder / "b.fit").unlink()                                 # the activity was deleted
    recs = A.build_achievements(_ach_ds(folder, ["a.fit"]), peaks=[])
    assert [r.id for r in recs] == ["a.fit"]
    cache = json.loads(A.cache_path().read_text("utf-8"))
    assert set(cache) == {"a.fit", "x.wko4"} and cache["a.fit"]["path"] == str(folder / "a.fit")
    (folder / "a.fit").unlink()
    assert A.baiyue_summits(_ach_ds(folder, []), peaks=[]) == {}
    assert set(json.loads(A.cache_path().read_text("utf-8"))) == {"x.wko4"}


def test_achievements_cache_write_is_atomic(ach, monkeypatch):
    A, folder = ach
    A.build_achievements(_ach_ds(folder, ["a.fit"]), peaks=[])
    p = A.cache_path()
    before = p.read_text("utf-8")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(A.os, "replace", boom)
    A._save_cache({"b.fit": {"stamp": [2], "summary": None}})
    assert p.read_text("utf-8") == before                       # the old cache, whole
    assert not [x for x in p.parent.iterdir() if x != p]        # no temporary file left

    def killed(*a, **k):                                          # interrupted (not an OSError)
        raise KeyboardInterrupt
    monkeypatch.setattr(A.os, "replace", killed)
    with pytest.raises(KeyboardInterrupt):
        A._save_cache({"b.fit": {"stamp": [2], "summary": None}})
    assert json.loads(p.read_text("utf-8")) == json.loads(before)
    assert not [x for x in p.parent.iterdir() if x != p]


def test_the_versions_are_the_same_in_every_process():
    """A version that followed the hash seed would drop every cache on each restart / worker."""
    import os
    import subprocess
    import sys
    code = ("from backend.engine.wko5expr import dataset as D; from backend.engine.racepower import cptest as T;"
            "from backend.files import file_service as FS;"
            "print([D.per_workout_code(n) for n in ('channel_peaks', 'workout_curves', 'power_source', "
            "'bad_activity')], T.power_cache_code(), T.bad_cache_code(), FS.mmp_version())")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    outs = {subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("1", "2")}
    assert len(outs) == 1


# ---------------------------------------------------------------- the data registry (SP-311)
def test_these_caches_are_registered_with_their_version():
    from backend import data_registry as R
    for name in ("channel_peaks.json", "workout_curves.json", "power_source_v1.json", "bad_activity_v1.json",
                 "racepower_power_source.json", "racepower_bad_activity.json", "achievements_cache.json"):
        e = R.classify(name)
        assert e.cls == R.DERIVED and "SP-341" in e.invalidated_by, name
    assert "SP-341" in R.table("mmp_cache").invalidated_by
    assert R.table("pmc_cache") is None and "pmc_cache" in R.RETIRED_TABLES
