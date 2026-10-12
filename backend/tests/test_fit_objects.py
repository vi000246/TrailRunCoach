"""FIT originals as gzip objects named by their sha256 (SP-308, backend/files/fitstore.py):
hash naming and de-dup, the gzip round trip, the store's confinement, the sync writers, the
readers that take both object keys and legacy paths, the FIT dataset cache valid by the
content hash, delete = a mark + a later cleanup, and the one-off migration script.
Synthetic FITs only (fit_builder)."""
import datetime as dt
import gzip
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from backend.db.models import FitObject, WorkoutFile
from backend.engine.wko5expr import fitcache
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.files import fit_to_channels as FTC
from backend.files import fitstore
from backend.files.fit_reader import parse_fit
from backend.scripts import fit_objects_migrate as MIG
from backend.sync import coros_client, http, purge, storage
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import FakeCoros, collect, make_session, run

START = datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)
TODAY = dt.date(2026, 9, 30)


def _fit(i=0, **kw):
    return build_run(START + timedelta(days=i), seconds=600, power=200 + 5 * i, hr=140 + i, speed_m_s=3.0,
                     climb_m_per_s=0.1, **kw)


# ---------------------------------------------------------------------------
# keys, the store
# ---------------------------------------------------------------------------

def test_object_key_is_the_sha256_of_the_fit_bytes():
    raw = _fit()
    sha = fitstore.sha256_hex(raw)
    key = fitstore.object_key("coros", sha)
    assert key == f"coros/objects/{sha[:2]}/{sha}.fit.gz"
    assert fitstore.is_object_key(key) and fitstore.sha_of_key(key) == sha and fitstore.folder_of_key(key) == "coros"
    assert fitstore.object_sha(Path("/x/fit/coros") / key) == sha and fitstore.object_sha(key.split("/", 1)[1]) == sha
    # legacy paths are never keys (absolute, relative, a hash-like name elsewhere)
    for legacy in ("/home/a/.wko5coach/fit/coros/2026/1_2026-09-01_run.fit", "elsewhere.fit",
                   f"coros/2026/{sha}.fit.gz", f"coros/objects/zz/{sha}.fit.gz", "C:\\fit\\a.fit"):
        assert not fitstore.is_object_key(legacy)
    assert fitstore.object_sha(f"coros/objects/{sha[2:4]}/{sha}.fit.gz") is None    # wrong shard
    with pytest.raises(ValueError):
        fitstore.object_key("coros", "abc")


def test_gzip_round_trip_gives_the_same_bytes(tmp_path):
    st = fitstore.LocalDirStore(tmp_path / "fit")
    raw = _fit()
    key = fitstore.object_key("coros", fitstore.sha256_hex(raw))
    assert st.put(key, raw) == key
    on_disk = (tmp_path / "fit" / key).read_bytes()
    assert on_disk[:2] == fitstore.GZIP_MAGIC and len(on_disk) < len(raw)
    assert gzip.decompress(on_disk) == raw and st.get(key) == raw
    assert fitstore.read_bytes(key, st) == raw
    # deterministic (header mtime 0): the same FIT always gives the same stored bytes
    assert fitstore.gzip_fit(raw) == on_disk
    # immutable: a second put of the key writes nothing
    m = (tmp_path / "fit" / key).stat().st_mtime_ns
    st.put(key, raw)
    assert (tmp_path / "fit" / key).stat().st_mtime_ns == m
    # no temporary file left behind; list() sees the object
    assert [o.key for o in st.list()] == [key]
    with st.local_path(key) as p:
        assert p == (tmp_path / "fit" / key).resolve()
    st.delete(key)
    assert not st.exists(key) and list(st.list()) == []


def test_the_store_refuses_keys_outside_it_and_symlinks(tmp_path):
    root = tmp_path / "fit"
    st = fitstore.LocalDirStore(root)
    for bad in ("../x.fit.gz", "/etc/passwd", "coros/../../x", "coros//a", "./coros/a", "C:/x", "coros\\a", ""):
        with pytest.raises(fitstore.PathNotConfined):
            st.path(bad)
    (tmp_path / "elsewhere").mkdir()
    (root / "coros").mkdir(parents=True)
    os.symlink(tmp_path / "elsewhere", root / "coros" / "objects")
    sha = "a" * 64
    with pytest.raises(fitstore.PathNotConfined):
        st.put(fitstore.object_key("coros", sha), b"x")
    assert not any((tmp_path / "elsewhere").iterdir())
    assert not st.exists(fitstore.object_key("coros", sha))


def test_same_content_twice_is_stored_once(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        raw = _fit()
        a = await fitstore.store_fit(s, raw, "coros", coros_activity_id="1")
        b = await fitstore.store_fit(s, raw, "coros", coros_activity_id="2")
        c = await fitstore.store_fit(s, raw, "tp")            # any source: still the one object
        await s.commit()
        assert a.created and not b.created and not c.created
        assert a.key == b.key == c.key and a.key.startswith("coros/objects/")
        files = [p for p in _fit_root_in_tmp.rglob("*") if p.is_file()]
        assert len(files) == 1
        rows = (await s.execute(select(FitObject))).scalars().all()
        assert len(rows) == 1 and rows[0].sha256 == a.sha256 and rows[0].size == len(raw)
        assert rows[0].source == "coros" and rows[0].coros_activity_id == "1" and not rows[0].deleted
        d = await fitstore.store_fit(s, _fit(1), "tp")
        assert d.key.startswith("tp/objects/") and d.created
    run(go())


# ---------------------------------------------------------------------------
# the sync writers
# ---------------------------------------------------------------------------

def _act(label, raw, day=0):
    return {"labelId": label, "date": int((START + timedelta(days=day)).strftime("%Y%m%d")), "sportType": 100,
            "fitUrl": f"https://files.example/fit/{label}", "_bytes": raw}


def test_coros_sync_stores_objects_and_skips_the_same_bytes(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        a, b = _fit(0), _fit(1)
        fake = FakeCoros([_act("111", a), _act("222", b, 1), _act("333", a)])   # 333: 111's bytes again
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", "pw", s, 1)
            ev = await collect(coros_client.sync_workouts(s, 1))
        down = [e for e in ev if e.get("status") == "downloaded"]
        same = [e for e in ev if e.get("status") == "skipped" and e.get("reason") == "same_content"]
        assert {e["activity_id"] for e in down} == {"111", "222"} and [e["activity_id"] for e in same] == ["333"]
        rows = (await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars().all()
        assert [r.coros_activity_id for r in rows] == ["111", "222"]
        assert rows[0].file_path == fitstore.object_key("coros", fitstore.sha256_hex(a))
        assert fitstore.read_bytes(rows[0].file_path) == a
        objs = {o.sha256: o for o in (await s.execute(select(FitObject))).scalars()}
        assert set(objs) == {fitstore.sha256_hex(a), fitstore.sha256_hex(b)}
        o = objs[fitstore.sha256_hex(a)]
        assert o.coros_activity_id == "111" and o.start_time == START.replace(tzinfo=None)
        assert len([p for p in _fit_root_in_tmp.rglob("*") if p.is_file()]) == 2
        assert storage.folder_stats("coros")["files"] == 2
    run(go())


# ---------------------------------------------------------------------------
# readers: object keys and legacy paths
# ---------------------------------------------------------------------------

def test_legacy_paths_and_object_keys_are_both_readable(tmp_path, _fit_root_in_tmp):
    raw = _fit()
    legacy = _fit_root_in_tmp / "coros" / "2026" / "1_2026-09-01_run.fit"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(raw)
    legacy_gz = legacy.with_name("2_2026-09-01_run.fit.gz")
    legacy_gz.write_bytes(gzip.compress(raw))
    st = fitstore.default_store()
    key = st.put(fitstore.object_key("coros", fitstore.sha256_hex(raw)), raw)
    want = parse_fit(str(legacy))
    for src in (str(legacy), str(legacy_gz), key):
        got = parse_fit(src)
        assert got.start_time == want.start_time and got.sport == want.sport
        assert list(got.power_w) == list(want.power_w) and got.session == want.session
        assert fitstore.read_bytes(src) == raw
    assert fitstore.resolve(str(legacy)) == legacy and fitstore.resolve(key) == st.path(key)
    with pytest.raises(FileNotFoundError):
        parse_fit(fitstore.object_key("coros", "b" * 64))


def test_classification_rows_match_object_keys(tmp_path, _fit_root_in_tmp):
    from backend.engine.wko5expr import fitdataset as FD
    raw = _fit()
    key = fitstore.default_store().put(fitstore.object_key("coros", fitstore.sha256_hex(raw)), raw)
    legacy = str(_fit_root_in_tmp / "coros" / "2026" / "x.fit")
    db = tmp_path / "app.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT, trail_classification TEXT, "
                "classification_overridden INTEGER, duplicate_of INTEGER, coros_sport_type INTEGER)")
    con.execute("INSERT INTO workout_files VALUES (1, ?, 'trail', 1, NULL, 102)", (key,))
    con.execute("INSERT INTO workout_files VALUES (2, ?, 'road', 0, NULL, 100)", (legacy,))
    con.commit()
    con.close()
    rows = FD.load_classifications(db)
    path = storage.source_dir("coros") / key.split("/", 1)[1]        # as the Dataset lists it
    assert FD.classification_for(path, rows) == "trail" and FD.platform_for(path, rows)["coros"] == 102
    assert FD.classification_for(Path(legacy), rows) == "road"


def test_the_race_power_scan_lists_objects_by_their_db_date(tmp_path, _fit_root_in_tmp, monkeypatch):
    from backend.engine.racepower import cptest as T
    raw = _fit()
    sha = fitstore.sha256_hex(raw)
    key = fitstore.default_store().put(fitstore.object_key("coros", sha), raw)
    p = fitstore.default_store().path(key)
    info = {sha: {"deleted": False, "date": "2026-09-02"}}
    assert T._file_date(p, info) == dt.date(2026, 9, 2)
    assert T._file_date(p, {sha: {"deleted": True, "date": "2026-09-02"}}) is None      # deleted: left out
    assert T._file_date(p, {}) is None                                                   # unknown: left out
    assert T._file_date(Path("1_2026-09-01_run.fit")) == dt.date(2026, 9, 1)            # legacy names as before
    monkeypatch.setattr(T, "object_info", lambda: info)
    T._LIST_MEMO.clear()
    rows = T._listing(_fit_root_in_tmp)
    assert [(r[1], r[2]) for r in rows] == [(key, dt.date(2026, 9, 2))]


def test_backup_carries_object_entries():
    from backend.engine import backup as B
    sha = "c" * 64
    assert B.FIT_ENTRY_RE.match(f"fit/coros/objects/cc/{sha}.fit.gz.gz")
    assert B.FIT_ENTRY_RE.match("fit/tp/2026/tp_2026_09_02_1.fit.gz")
    assert not B.FIT_ENTRY_RE.match(f"fit/coros/objects/cc/../{sha}.fit.gz.gz")
    assert not B.FIT_ENTRY_RE.match(f"fit/coros/objects/zz/x/{sha}.fit.gz.gz")


# ---------------------------------------------------------------------------
# the FIT dataset cache: valid by the content hash for objects
# ---------------------------------------------------------------------------

def _count_parses(monkeypatch):
    calls = []
    real = FTC.fit_to_channels

    def counting(raw):
        calls.append(1)
        return real(raw)
    monkeypatch.setattr(FTC, "fit_to_channels", counting)
    return calls


def _build(root):
    return FitFolderDataset(root, config=EngineConfig(parity=False, hr_tss_moving_only=True), today=TODAY,
                            classifications={}, athlete_settings=[{"effective_date": "2026-01-01", "weight_kg": 60}],
                            estimate_thresholds=False)


def _values(ds):
    """What the charts read, without the file names (they change with the migration)."""
    return ([(w.entry.start, w.sport, w.sport_type, sorted(w.metrics.items(), key=str)) for w in ds.workouts],
            sorted(ds._power_src.values()), [[c.values for c in ds.wko4(i).channels.values()]
                                            for i in range(len(ds.workouts))])


def test_fitcache_entry_of_an_object_is_valid_by_its_hash(tmp_path, _fit_root_in_tmp, monkeypatch):
    st = fitstore.default_store()
    raw = _fit()
    key = st.put(fitstore.object_key("coros", fitstore.sha256_hex(raw)), raw)
    p = st.path(key)
    root = storage.source_dir("coros")
    calls = _count_parses(monkeypatch)
    first = _build(root)
    assert len(calls) == 1 and len(first.workouts) == 1
    e = fitcache.FitStore(root).files[key.split("/", 1)[1]]
    assert e["sha256"] == fitstore.sha256_hex(raw)
    # a new mtime (a restored / copied folder): still valid, nothing parsed — no content read either
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 5_000_000_000))
    monkeypatch.setattr(fitcache, "content_hash", lambda _p: pytest.fail("no content check for an object"))
    calls.clear()
    again = _build(root)
    assert calls == [] and _values(again) == _values(first)
    # an entry recorded for other content (a hash that is not the name) is not valid
    fs = fitcache.FitStore(root)
    rel = key.split("/", 1)[1]
    assert fs.entry(p) is not None
    fs.files[rel]["sha256"] = "0" * 64
    assert fs.entry(p) is None


def test_a_damaged_object_is_skipped_once_not_reparsed(tmp_path, _fit_root_in_tmp, monkeypatch):
    st = fitstore.default_store()
    good, other = _fit(0), _fit(1)
    key = st.put(fitstore.object_key("coros", fitstore.sha256_hex(good)), good)
    st.path(key).write_bytes(fitstore.gzip_fit(other))          # bytes that are not the name's
    root = storage.source_dir("coros")
    ds = _build(root)
    assert ds.workouts == []
    e = fitcache.FitStore(root).files[key.split("/", 1)[1]]
    assert e["meta"]["error"] == "HashMismatch"
    calls = _count_parses(monkeypatch)
    _build(root)
    assert calls == []


# ---------------------------------------------------------------------------
# delete = a mark; the files go in the cleanup
# ---------------------------------------------------------------------------

async def _import(s, raw, label):
    from backend.files.file_service import _import_one_file
    stored = await fitstore.store_fit(s, raw, "coros", coros_activity_id=label)
    wf = await _import_one_file(s, 1, stored.key, source="coros", coros_activity_id=label)
    await s.commit()
    return wf


def test_delete_marks_objects_and_the_cleanup_removes_the_unused_ones(tmp_path, _fit_root_in_tmp, monkeypatch):
    async def go():
        s = await make_session(tmp_path)
        a, b = _fit(0), _fit(1)
        wa = await _import(s, a, "1")
        await _import(s, b, "2")
        ka = wa.file_path
        st = fitstore.default_store()
        r = await purge.delete_source_files(s, "coros", 1, date_from=wa.workout_date, date_to=wa.workout_date)
        assert r["rows_deleted"] == 1 and r["objects_marked_deleted"] == 1 and r["files_deleted"] == 1
        assert st.exists(ka)                                   # only marked
        objs = {o.sha256: o for o in (await s.execute(select(FitObject))).scalars()}
        assert objs[fitstore.sha256_hex(a)].deleted and not objs[fitstore.sha256_hex(b)].deleted
        # the Dataset leaves the deleted object out at once
        from backend.engine.wko5expr import fitdataset as FD
        monkeypatch.setattr(FD, "_deleted_objects", lambda: frozenset({fitstore.sha256_hex(a)}))
        ds = _build(storage.source_dir("coros"))
        assert len(ds.workouts) == 1 and ds.workouts[0].entry.start.date() == (START + timedelta(days=1)).astimezone(
            ds.tz).date()
        # an orphan (stored, never recorded: a failed sync) — young ones stay
        c = _fit(2)
        kc = st.put(fitstore.object_key("coros", fitstore.sha256_hex(c)), c)
        dry = await purge.cleanup_objects(s, apply=False)
        assert dry["removed"] == 1 and dry["orphans_removed"] == 0 and st.exists(ka)
        out = await purge.cleanup_objects(s)
        assert out["removed"] == 1 and out["kept_live"] == 1 and out["orphans_removed"] == 0
        assert not st.exists(ka) and st.exists(kc)
        assert len((await s.execute(select(FitObject))).scalars().all()) == 2   # the mark stays
        out = await purge.cleanup_objects(s, min_age_s=0)
        assert out["orphans_removed"] == 1 and not st.exists(kc)
        # the same content imported again: the file comes back, the mark is lifted
        again = await fitstore.store_fit(s, a, "coros")
        await s.commit()
        assert again.key == ka and again.created and st.get(ka) == a
        assert not (await s.execute(select(FitObject).where(FitObject.sha256 == again.sha256))).scalar_one().deleted
        # the whole source: rows go, objects are marked, nothing under objects/ is unlinked
        r = await purge.delete_source_files(s, "coros", 1)
        assert r["objects_marked_deleted"] == 1 and st.exists(ka)
        assert (await s.execute(select(FitObject).where(
            FitObject.sha256 == fitstore.sha256_hex(b)))).scalar_one().deleted
    run(go())


def test_cleanup_tick_waits_for_a_running_sync(tmp_path, _fit_root_in_tmp):
    from backend.sync import runner

    class _Factory:
        def __init__(self, s):
            self.s = s

        def __call__(self):
            s = self.s

            class _Ctx:
                async def __aenter__(self):
                    return s

                async def __aexit__(self, *a):
                    return False
            return _Ctx()

    async def go():
        s = await make_session(tmp_path)
        purge._CLEANUP_AT.clear()
        with runner.hold("coros"):
            assert await purge.cleanup_tick(_Factory(s), now=0.0) is None
        assert (await purge.cleanup_tick(_Factory(s), now=0.0)) is not None
        assert await purge.cleanup_tick(_Factory(s), now=10.0) is None                # once a day
        assert await purge.cleanup_tick(_Factory(s), now=purge.CLEANUP_EVERY_S + 1) is not None
        purge._CLEANUP_AT.clear()
    run(go())


# ---------------------------------------------------------------------------
# the migration script
# ---------------------------------------------------------------------------

def _legacy_tree(root: Path) -> dict:
    """Three COROS files and one TP file as the sync wrote them before SP-308."""
    out = {}
    for i, name in enumerate(["480000000000000001_2026-09-01_run.fit", "480000000000000002_2026-09-02_run.fit",
                              "480000000000000003_2026-09-03_run.fit"]):
        p = root / "coros" / "2026" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_fit(i))
        out[name] = p
    p = root / "tp" / "2026" / "tp_2026_09_04_7.fit"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_fit(3))
    out["tp"] = p
    return out


async def _legacy_rows(s, files: dict):
    from backend.files.file_service import _import_one_file
    for name, p in files.items():
        if name == "tp":
            await _import_one_file(s, 1, p, source="trainingpeaks", tp_workout_id=7)
        else:
            await _import_one_file(s, 1, p, source="coros", coros_activity_id=name.split("_")[0])
    await s.commit()


def _tree_state(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_migration_dry_run_apply_rerun(tmp_path, _fit_root_in_tmp, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()

    async def go():
        s = await make_session(tmp_path)
        files = _legacy_tree(_fit_root_in_tmp)
        await _legacy_rows(s, files)
        root = storage.source_dir("coros")
        cold = _build(root)                                    # the FIT dataset cache of the old files
        assert len(cold.workouts) == 3
        cold_values = _values(cold)
        # a race-power cache entry for one old file (as cptest writes it)
        old = files["480000000000000001_2026-09-01_run.fit"]
        old_key = str(old.relative_to(_fit_root_in_tmp))
        stamp = fitcache.stamp_s(old)
        (home / "racepower_cptests.json").write_text(json.dumps(
            {"v": 1, "files": {old_key: [stamp, {"date": "2026-09-01", "file": old.name, "test": None}]}}), "utf-8")
        (home / "racepower_power_source.json").write_text(json.dumps(
            {"v": "x", "files": {old_key: stamp + ["stryd"]}}), "utf-8")
        cache_root = Path(os.environ[fitcache.ENV_ROOT])
        before_fit, before_cache = _tree_state(_fit_root_in_tmp), _tree_state(cache_root)
        before_rows = [(r.id, r.file_path) for r in (await s.execute(select(WorkoutFile))).scalars()]

        # 1. dry run: counts, nothing changes
        dry = await MIG.migrate(s, apply=False, home=home)
        assert dry["converted"] == 4 and dry["already"] == 0 and dry["bytes_before"] > dry["bytes_after"] > 0
        assert _tree_state(_fit_root_in_tmp) == before_fit and _tree_state(cache_root) == before_cache
        assert [(r.id, r.file_path) for r in (await s.execute(select(WorkoutFile))).scalars()] == before_rows
        assert (await s.execute(select(FitObject))).scalars().all() == []

        # 2. apply
        calls = _count_parses(monkeypatch)
        out = await MIG.migrate(s, apply=True, home=home, batch=2)
        assert out["converted"] == 4 and out["objects_written"] == 4 and out["old_files_deleted"] == 4
        assert out["failed"] == 0 and out["cache_entries_carried"]["fitcache"] == 3
        assert out["cache_entries_carried"]["racepower"] == 2
        rows = (await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars().all()
        assert all(fitstore.is_object_key(r.file_path) for r in rows)
        assert [r.file_path.split("/")[0] for r in rows] == ["coros", "coros", "coros", "tp"]
        for r, (name, p) in zip(rows, files.items()):
            assert not p.exists() and fitstore.read_bytes(r.file_path) == before_fit[
                p.relative_to(_fit_root_in_tmp).as_posix()]
        objs = (await s.execute(select(FitObject))).scalars().all()
        assert len(objs) == 4 and {o.source for o in objs} == {"coros", "trainingpeaks"}
        assert all(o.start_time is not None for o in objs)
        # the parse results are the same and nothing was parsed again
        warm = _build(root)
        assert calls == [] and _values(warm) == cold_values
        assert all(fitstore.object_sha(w.entry.file) for w in warm.workouts)
        idx = fitcache.FitStore(root).files
        assert set(idx) == {r.file_path.split("/", 1)[1] for r in rows[:3]}     # old entries gone
        rp = json.loads((home / "racepower_cptests.json").read_text("utf-8"))["files"]
        k1 = rows[0].file_path
        assert list(rp) == [k1] and rp[k1][1]["file"] == Path(k1).name
        assert rp[k1][0] == fitcache.stamp_s(fitstore.default_store().path(k1))
        pw = json.loads((home / "racepower_power_source.json").read_text("utf-8"))["files"]
        assert list(pw) == [k1] and pw[k1][2] == "stryd"

        # 3. a re-run is a no-op
        state = _tree_state(_fit_root_in_tmp)
        again = await MIG.migrate(s, apply=True, home=home)
        assert again["converted"] == 0 and again["already"] == 4 and again["objects_written"] == 0
        assert _tree_state(_fit_root_in_tmp) == state

        # 4. an interrupted run left an old file behind (its row already points at the object)
        leftover = files["480000000000000002_2026-09-02_run.fit"]
        leftover.write_bytes(_fit(1))
        stray = _fit_root_in_tmp / "coros" / "2026" / "notes.fit"
        stray.write_bytes(_fit(9))
        dry = await MIG.migrate(s, apply=False, home=home)
        assert dry["leftovers_removed"] == 1 and dry["untracked"] == 1 and leftover.exists()
        fin = await MIG.migrate(s, apply=True, home=home)
        assert fin["leftovers_removed"] == 1 and not leftover.exists() and stray.exists()
        assert MIG._report(fin).startswith("FIT objects migration (applied)")
    run(go())


def test_migration_leaves_refused_missing_and_duplicate_rows(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        raw = _fit()
        a = _fit_root_in_tmp / "coros" / "2026" / "1_2026-09-01_run.fit"
        a.parent.mkdir(parents=True)
        a.write_bytes(raw)
        b = a.with_name("2_2026-09-01_run.fit.gz")             # a legacy gzip with the same FIT
        b.write_bytes(gzip.compress(raw))
        outside = tmp_path / "WKO5" / "3_2026-09-01_run.fit"
        outside.parent.mkdir(parents=True)
        outside.write_bytes(raw)
        link = _fit_root_in_tmp / "coros" / "2026" / "4_2026-09-01_run.fit"
        os.symlink(outside, link)
        for i, p in enumerate((a, b, outside, link, _fit_root_in_tmp / "coros" / "2026" / "gone.fit")):
            s.add(WorkoutFile(athlete_id=1, file_path=str(p), file_format="fit", source="coros",
                              coros_activity_id=str(i)))
        await s.commit()
        out = await MIG.migrate(s, apply=True, home=tmp_path)
        assert out["converted"] == 1 and out["duplicate_content"] == 1 and out["refused"] == 2
        assert out["missing"] == 1
        rows = (await s.execute(select(WorkoutFile).order_by(WorkoutFile.id))).scalars().all()
        assert fitstore.is_object_key(rows[0].file_path) and rows[1].file_path == str(b)
        assert b.exists() and outside.exists() and link.is_symlink()
        assert parse_fit(rows[1].file_path).start_time == parse_fit(rows[0].file_path).start_time
    run(go())
