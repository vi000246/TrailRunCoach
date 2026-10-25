"""SP-317: the monthly archive of FIT originals past the retention (engine/fit_archive.py) — the
cold summaries are frozen first (SP-340, coldstore.prepare_archive), only those files go, inside the
tenant's own folder, the referenced activities stay, one rebuild per batch, a rerun changes
nothing; the schedule (the 1st, 03:00–05:00, tenants staggered); the settings API (retention,
最近一次清理, 下載我的全部 FIT); the 活動 list shows the archived activities.

Synthetic FIT files only (SP-340's four years of runs), every folder under tmp_path."""
from __future__ import annotations

import datetime as dt
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import jobs, tenancy
from backend.engine import fit_archive as FA
from backend.engine import planning
from backend.engine.wko5expr import chartscope, coldstore, datasource
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.evaluator import Evaluator
from backend.sync import storage
from backend.tests.fit_builder import build_run
from backend.tests.test_cold_summaries_sp340 import CUTOFF, N, TODAY, T, _build, _fit, _start

TZ8 = timezone(timedelta(hours=8))


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    """A user tenant in tmp_path with SP-340's activities in its fit/coros folder."""
    monkeypatch.setattr(storage, "FIT_ROOT", None)
    monkeypatch.setattr(coldstore, "ROOT", None)
    monkeypatch.setattr(FA, "LOG_PATH", None)
    monkeypatch.setattr(planning, "PLAN_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(FA, "_tz", lambda: TZ8)
    t = tenancy.Tenant(id="u7", kind=tenancy.USER, root=tmp_path, shared=tmp_path, caps=tenancy.USER_CAPS)
    root = tmp_path / "fit" / "coros"
    for i in range(N):
        p = root / str(_start(i).year) / f"{i:03d}.fit"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_fit(i))
    rebuilds = []
    monkeypatch.setattr(FA, "_rebuild", lambda: rebuilds.append(1))
    with tenancy.use(t):
        yield {"t": t, "root": root, "home": tmp_path, "rebuilds": rebuilds}


def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in root.rglob("*.fit*")}


def _run(env, ds=None, **kw):
    kw.setdefault("plan", planning.Plan())
    kw.setdefault("tests", [])
    return FA.run(CUTOFF, ds=ds or _build(env["root"]), folder="coros", today=TODAY, years=3, **kw)


# ---------------------------------------------------------------------------- the rule

def test_cutoff_and_retention_choices():
    assert FA.DEFAULT_YEARS == 3 and FA.MIN_YEARS == 2 and FA.CHOICES == (2, 3, 5, 10)
    assert FA.cutoff_for(dt.date(2026, 11, 1), 3) == dt.date(2023, 11, 1)
    assert FA.cutoff_for(dt.date(2028, 2, 29), 3) == dt.date(2025, 2, 28)
    for bad in (1, 0, 4, True, "3", None, 2.0):
        with pytest.raises(ValueError):
            FA.validate_years(bad)
    from backend.settings import repository as R
    assert R.DEFAULTS[FA.RETENTION_KEY] == 3
    R.validate(FA.RETENTION_KEY, 5)
    with pytest.raises(ValueError):
        R.validate(FA.RETENTION_KEY, 1)


# ---------------------------------------------------------------------------- the job

def test_only_files_before_the_cutoff_go_and_the_summaries_keep_the_charts(tenant):
    root = tenant["root"]
    ds = _build(root)
    cut = date_to_day(CUTOFF)
    old = {w.entry.file for w in ds.workouts if w.day < cut}
    keep_before = {k: v for k, v in _files(root).items() if k not in old}
    ctl0 = Evaluator(ds, ds.first_day, T).evaluate("ctl").values.tolist()
    best0 = Evaluator(ds, ds.first_day, T).evaluate("meanmax(runpower)")
    ch = {"kind": "athlete", "id": "pmc", "series": [{"expression": "ctl"}]}
    key0 = [chartscope.fingerprint(ds, chartscope.scope_of(ch, ds, T - d + 1, T)) for d in (90, 365)]

    out = _run(tenant, ds)
    assert out["status"] == "ok" and out["deleted"] == len(old) > 20
    assert out["bytes"] > 0 and out["refused"] == 0 and not out["protected"]
    olds = sorted(w.entry.start.date() for w in ds.workouts if w.day < cut)
    assert (out["first"], out["last"]) == (olds[0].isoformat(), olds[-1].isoformat())
    # 期限內的活動完全不動
    assert _files(root) == keep_before
    after = _build(root)
    assert len(after.cold.archived) == len(old)
    assert Evaluator(after, after.first_day, T).evaluate("ctl").values.tolist() == pytest.approx(ctl0)
    best1 = Evaluator(after, after.first_day, T).evaluate("meanmax(runpower)")
    assert list(best1.ys) == pytest.approx(list(best0.ys))
    # the 90- and 365-day charts keep their keys: nothing hot / warm recomputed
    assert [chartscope.fingerprint(after, chartscope.scope_of(ch, after, T - d + 1, T)) for d in (90, 365)] == key0
    # never fit_objects.deleted: the workout rows stay and the summaries are not dropped
    assert all(r.get("frozen") for m in coldstore.ColdStore(root).months()
               for r in coldstore.ColdStore(root).load(m)["activities"].values())


def test_one_rebuild_per_batch_and_a_rerun_changes_nothing(tenant, monkeypatch):
    root = tenant["root"]
    changed = []
    monkeypatch.setattr(datasource, "files_changed", lambda: changed.append(1))
    first = _run(tenant)
    assert first["deleted"] > 20
    assert changed == [1] and tenant["rebuilds"] == [1]           # one batch, one rebuild
    files = _files(root)
    store = coldstore.ColdStore(root)
    stamps = {m: store.path(m).stat().st_mtime_ns for m in store.months()}
    second = _run(tenant)
    assert (second["status"], second["deleted"], second["bytes"]) == ("ok", 0, 0)
    assert changed == [1] and tenant["rebuilds"] == [1]           # nothing deleted: no rebuild
    assert _files(root) == files
    assert {m: store.path(m).stat().st_mtime_ns for m in store.months()} == stamps
    assert FA.last_run()["deleted"] == 0 and [r["deleted"] for r in FA.log_entries()] == [first["deleted"], 0]


def test_activities_without_a_summary_keep_their_fit(tenant, monkeypatch):
    root = tenant["root"]
    ds = _build(root)
    victim = next(w.entry.file for w in ds.workouts if w.day < date_to_day(CUTOFF))
    real = coldstore._Pass.ensure

    def ensure(self, w, freeze=False):
        return False if w.entry.file == victim else real(self, w, freeze)
    monkeypatch.setattr(coldstore._Pass, "ensure", ensure)
    out = _run(tenant, ds)
    assert out["kept_failed"] == 1 and (root / victim).exists()


def test_nothing_goes_while_a_first_import_is_back_filled(tenant):
    root = tenant["root"]
    ds = _build(root)
    ds._store.first_import = True
    before = _files(root)
    out = _run(tenant, ds)
    assert (out["status"], out["reason"], out["deleted"]) == ("skipped", "backfill", 0)
    assert _files(root) == before and tenant["rebuilds"] == []


def test_paths_outside_the_tenant_folder_and_symlinks_are_refused(tenant, monkeypatch, tmp_path_factory):
    root = tenant["root"]
    other = tmp_path_factory.mktemp("other_tenant") / "fit" / "coros" / "2022"
    other.mkdir(parents=True)
    theirs = other / "x.fit"
    theirs.write_bytes(_fit(1))
    link = root / "2022" / "link.fit"
    link.symlink_to(theirs)
    ds = _build(root)
    monkeypatch.setattr(coldstore, "prepare_archive", lambda cutoff, ds=None: {
        "files": [str(theirs), str(link), str(root / ".." / ".." / ".." / theirs.relative_to(theirs.anchor))],
        "left_out": [], "failed": []})
    out = _run(tenant, ds)
    assert out["deleted"] == 0 and out["refused"] == 3
    assert theirs.exists() and link.is_symlink()


def test_a_dataset_of_another_folder_is_refused(tenant, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "coros"
    elsewhere.mkdir()
    (elsewhere / "a.fit").write_bytes(_fit(0))
    out = FA.run(CUTOFF, ds=_build(elsewhere), folder="coros", today=TODAY, plan=planning.Plan(), tests=[])
    assert out["status"] == "skipped" and (elsewhere / "a.fit").exists()


def test_referenced_activities_are_kept_and_named(tenant):
    """The current LTHR row comes from a test on an old activity's day, and a test session of the
    ongoing plan points at another old activity: both FITs stay (named in the log); the rest go.
    An applied estimate and a test of a plan long over protect nothing."""
    root = tenant["root"]
    ds = _build(root)
    old = [w for w in ds.workouts if w.day < date_to_day(CUTOFF)]
    a, b, c = old[3], old[8], old[12]
    plan = planning.Plan(thresholds=[
        planning.Threshold(date=a.entry.start.date().isoformat(), lthr=168, note="30 分鐘測試"),
        planning.Threshold(date=c.entry.start.date().isoformat(), aethr=150, note="由活動資料自動估算"),
    ])
    tests = [{"uid": "t1", "day": (TODAY - dt.timedelta(days=3)).isoformat(), "state": "done",
              "done_by": {"start": b.entry.start.isoformat(), "index": None}},
             {"uid": "t0", "day": "2022-09-01", "state": "done",
              "done_by": {"start": c.entry.start.isoformat(), "index": None}}]
    out = _run(tenant, ds, plan=plan, tests=tests)
    kept = {p["file"]: p["reason"] for p in out["protected"]}
    assert kept == {a.entry.file: "threshold:lthr", b.entry.file: "plan_test"}
    assert (root / a.entry.file).exists() and (root / b.entry.file).exists()
    assert not (root / c.entry.file).exists()
    assert out["deleted"] == len(old) - 2


def test_tenants_only_lose_their_own_files(tmp_path, monkeypatch):
    """Two tenants, each archived in its own context: B's folder is untouched by A's run."""
    monkeypatch.setattr(storage, "FIT_ROOT", None)
    monkeypatch.setattr(coldstore, "ROOT", None)
    monkeypatch.setattr(FA, "LOG_PATH", None)
    monkeypatch.setattr(FA, "_rebuild", lambda: None)
    ts = {}
    for tid in ("u1", "u2"):
        home = tmp_path / tid
        root = home / "fit" / "coros"
        for i in range(0, N, 4):
            p = root / str(_start(i).year) / f"{i:03d}.fit"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(_fit(i))
        ts[tid] = (tenancy.Tenant(id=tid, kind=tenancy.USER, root=home, shared=home, caps=tenancy.USER_CAPS), root)
    before_b = _files(ts["u2"][1])
    with tenancy.use(ts["u1"][0]):
        out = FA.run(CUTOFF, ds=_build(ts["u1"][1]), folder="coros", today=TODAY, plan=planning.Plan(), tests=[])
        # A's run fed B's Dataset: refused, nothing of B's goes
        out_b = FA.run(CUTOFF, ds=_build(ts["u2"][1]), folder="coros", today=TODAY, plan=planning.Plan(), tests=[])
    assert out["deleted"] > 0 and out_b["status"] == "skipped"
    assert _files(ts["u2"][1]) == before_b
    assert (ts["u1"][0].root / FA.LOG_FILE).exists() and not (ts["u2"][0].root / FA.LOG_FILE).exists()


def test_a_demo_tenant_never_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(FA, "LOG_PATH", tmp_path / "log.json")
    t = tenancy.Tenant(id="demo-x", kind=tenancy.DEMO_SANDBOX, root=tmp_path, shared=tmp_path,
                       caps=tenancy.DEMO_CAPS)
    with tenancy.use(t):
        assert FA.due(datetime(2026, 11, 1, 3, 30, tzinfo=TZ8)) is None
        assert FA.tick(datetime(2026, 11, 1, 3, 30, tzinfo=TZ8), submit=lambda *a, **k: 1 / 0) is None
        out = FA.run(CUTOFF, ds=object(), folder="coros")
    assert out["status"] == "skipped" and not (tmp_path / "log.json").exists()
    owner = tenancy.Tenant(id="owner", kind=tenancy.OWNER, root=tmp_path, shared=tmp_path)
    monkeypatch.setenv(tenancy.ENV_MODE, "demo")                 # the demo instance's own base
    with tenancy.use(owner):
        assert FA.due(datetime(2026, 11, 1, 3, 30, tzinfo=TZ8)) is None


# ---------------------------------------------------------------------------- the schedule

def _at(day, hh, mm=0):
    return datetime(2026, 11, day, hh, mm, tzinfo=TZ8)


def test_the_window_is_the_first_of_the_month_from_three_with_the_tenant_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(FA, "LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(FA, "_tz", lambda: TZ8)
    owner = tenancy.Tenant(id="owner", kind=tenancy.OWNER, root=tmp_path, shared=tmp_path)
    with tenancy.use(owner):
        assert FA.due(_at(1, 2, 59)) is None
        d = FA.due(_at(1, 3, 0))
        assert d == {"month": "2026-11", "years": 3, "cutoff": dt.date(2023, 11, 1), "today": dt.date(2026, 11, 1)}
        assert FA.due(_at(2, 3, 0)) is None and FA.due(datetime(2026, 12, 1, 2, 0, tzinfo=TZ8)) is None
        # UTC now, local 1st: the athlete's zone decides
        assert FA.due(datetime(2026, 10, 31, 19, 30, tzinfo=timezone.utc)) is not None
    slots = {FA.slot_minutes(f"u{i}") for i in range(200)}
    assert min(slots) >= 0 and max(slots) < FA.WINDOW_MIN and len(slots) > 60   # spread over 03:00–05:00
    assert FA.slot_minutes("u5") == FA.slot_minutes("u5") and FA.slot_minutes("owner") == 0
    u = tenancy.Tenant(id="u5", kind=tenancy.USER, root=tmp_path, shared=tmp_path)
    s = FA.slot_minutes("u5")
    with tenancy.use(u):
        assert FA.due(_at(1, 3, 0) + timedelta(minutes=s) - timedelta(seconds=1)) is None or s == 0
        assert FA.due(_at(1, 3, 0) + timedelta(minutes=s)) is not None


def test_the_tick_queues_one_archive_job_a_month_and_retries_a_skipped_one(tmp_path, monkeypatch):
    monkeypatch.setattr(FA, "LOG_PATH", tmp_path / "log.json")
    monkeypatch.setattr(FA, "_tz", lambda: TZ8)
    monkeypatch.setattr(FA, "_years", lambda: 5)
    ran = []
    status = ["skipped"]

    def fake_run(cutoff, **kw):
        ran.append((cutoff, kw["month"], kw["years"]))
        e = {"at": FA._now_iso(), "status": status[0], "deleted": 0}
        FA._record(e, kw["month"])
        return e
    monkeypatch.setattr(FA, "run", fake_run)
    owner = tenancy.Tenant(id="owner", kind=tenancy.OWNER, root=tmp_path, shared=tmp_path)
    jobs._INLINE = True
    try:
        with tenancy.use(owner):
            job = FA.tick(_at(1, 3, 1))
            assert job.priority == jobs.PRIORITIES["archive"] and job.cls == "full"
            assert job.key == ("archive", "owner", "2026-11") and job.tenant == "owner"
            assert ran == [(dt.date(2021, 11, 1), "2026-11", 5)]
            monkeypatch.setattr(FA, "_now_iso", lambda: _at(1, 3, 1).astimezone(timezone.utc).isoformat())
            FA._record({"at": FA._now_iso(), "status": "skipped"}, "2026-11")
            assert FA.tick(_at(1, 3, 30)) is None                    # retried an hour later
            status[0] = "ok"
            assert FA.tick(_at(1, 4, 2)) is not None and len(ran) == 2
            assert FA.tick(_at(1, 5, 30)) is None                    # done this month
            assert FA.next_run(_at(1, 5, 30)) == datetime(2026, 12, 1, 3, 0, tzinfo=TZ8)
    finally:
        jobs._INLINE = False


def test_the_scheduler_loop_checks_every_tenant(monkeypatch):
    import inspect
    from backend.sync import scheduler
    src = inspect.getsource(scheduler.loop)
    assert "fit_archive.tick_async" in src and "_each_tenant" in src


# ---------------------------------------------------------------------------- the settings API

class Api:
    def __init__(self, monkeypatch, home: Path):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.api import archive as A
        from backend.db.database import get_db
        from backend.tests.test_coros_workouts import make_db, run
        self.db = run(make_db(logged_in=False))
        monkeypatch.setenv(tenancy.ENV_HOME, str(home))       # the owner's folder = tmp
        monkeypatch.setattr(FA, "LOG_PATH", None)
        monkeypatch.setattr(FA, "_tz", lambda: TZ8)

        async def fake_db():
            yield self.db
        app = FastAPI()
        app.include_router(A.router)
        app.dependency_overrides[get_db] = fake_db
        self.c = TestClient(app)


def test_settings_api_retention_and_last_cleanup(tmp_path, monkeypatch):
    api = Api(monkeypatch, tmp_path)
    s = api.c.get("/api/v1/archive/status").json()
    assert s["retention_years"] == 3 and s["choices"] == [2, 3, 5, 10] and s["last"] is None
    assert s["next_run"][11:16] == "03:00" and s["next_run"][8:10] == "01"
    r = api.c.put("/api/v1/archive/settings", json={"retention_years": 5})
    assert r.status_code == 200 and r.json()["retention_years"] == 5
    assert api.c.put("/api/v1/archive/settings", json={"retention_years": 1}).status_code == 400
    with tenancy.use(tenancy.owner()):
        FA._record({"at": "2026-11-01T03:02:00+00:00", "status": "ok", "deleted": 171, "bytes": 52428800,
                    "npz_bytes": 0, "first": "2020-03-01", "last": "2023-10-30"}, "2026-11")
        FA._record({"at": "2026-12-01T03:02:00+00:00", "status": "skipped", "reason": "backfill"}, "2026-12")
    last = api.c.get("/api/v1/archive/status").json()["last"]
    assert (last["deleted"], last["first"], last["last"]) == (171, "2020-03-01", "2023-10-30")


def test_the_last_cleanup_date_is_the_athletes_day(tmp_path, monkeypatch):
    """A run on the 1st at 03:00 Taipei is stored in UTC (the 31st, 19:00): the page shows the
    athlete's day — the 1st —, not the UTC date's prefix."""
    api = Api(monkeypatch, tmp_path)
    with tenancy.use(tenancy.owner()):
        FA._record({"at": "2026-10-31T19:00:41+00:00", "status": "ok", "deleted": 171, "bytes": 1,
                    "npz_bytes": 0, "first": "2020-03-01", "last": "2023-10-30"}, "2026-11")
    last = api.c.get("/api/v1/archive/status").json()["last"]
    assert last["at"].startswith("2026-10-31") and last["date_local"] == "2026-11-01"
    page = (Path(__file__).parents[1] / "static" / "settings.html").read_text("utf-8")
    assert "arDate(L.at)" not in page and "L.date_local" in page


def test_download_all_fit_streams_only_this_tenants_files(tmp_path, monkeypatch, tmp_path_factory):
    from backend.files import fitstore
    api = Api(monkeypatch, tmp_path)
    monkeypatch.setattr(storage, "FIT_ROOT", None)
    store = fitstore.LocalDirStore(tmp_path / "fit")
    fits = {}
    for i, folder in ((0, "coros"), (1, "coros"), (2, "tp")):
        raw = build_run(datetime(2024, 5, 3 + i, 7, tzinfo=timezone.utc), seconds=60, hr=140)
        sha = fitstore.sha256_hex(raw)
        store.put(fitstore.object_key(folder, sha), raw)
        fits[sha] = raw
    legacy = tmp_path / "fit" / "coros" / "2021" / "old.fit"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy-fit")
    other = tmp_path_factory.mktemp("someone") / "secret.fit"
    other.write_bytes(b"not yours")
    (tmp_path / "fit" / "coros" / "2021" / "link.fit").symlink_to(other)
    r = api.c.get("/api/v1/archive/fit.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert len(names) == 4 and "coros/2021/old.fit" in names
    got = {z.read(n) for n in names}
    assert got == set(fits.values()) | {b"legacy-fit"}                 # gunzipped; no symlinked file
    assert all(n.startswith(("coros/", "tp/")) for n in names)


def test_the_zip_is_written_as_it_is_sent(tmp_path):
    from backend.api import archive as A
    items = []
    for i in range(5):
        p = tmp_path / f"{i}.fit"
        p.write_bytes(bytes([i]) * 20000)
        items.append((p, f"coros/{i}.fit"))
    chunks = list(A.stream_zip(items))
    assert len([c for c in chunks if c]) >= 5                          # one piece per file, not one blob
    z = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
    assert z.read("coros/3.fit") == bytes([3]) * 20000


def test_the_demo_instance_does_not_mount_the_archive_api():
    from backend.api import archive as A
    from backend.main import build_app

    def routers(app):
        return {id(getattr(r, "original_router", None)) for r in app.routes}
    assert id(A.router) in routers(build_app(demo=False))
    assert id(A.router) not in routers(build_app(demo=True))


# ---------------------------------------------------------------------------- the 活動 list

def test_the_activity_list_shows_archived_activities_from_their_summary(tenant, monkeypatch):
    from backend.api import wko5views as V
    root = tenant["root"]
    ds = _build(root)
    _run(tenant, ds)
    after = _build(root)
    monkeypatch.setattr(V, "_dataset", lambda *a, **k: after)
    out = V.activities_list()["activities"]
    arch = [a for a in out if a.get("archived")]
    assert len(arch) == len(after.cold.archived) > 20
    a0 = arch[-1]
    rec = next(a for a in after.cold.archived if a.entry.file == a0["file"])
    assert a0["index"] is None and a0["excluded"] is None
    assert a0["tss"] == rec.metrics["tss"] and a0["duration"] == rec.metrics["duration"]
    assert a0["key"] and a0["start"] == rec.entry.start.isoformat()
    assert len(out) == len(after.workouts) + len(after.excluded) + len(arch)


# ---------------------------------------------------------------------------- registry / page / i18n

def test_the_log_is_registered():
    from backend import data_registry as R
    e = R.classify(FA.LOG_FILE)
    assert e is not None and e.where == R.ROOT and e.cls == R.DERIVED


def test_the_settings_page_has_the_block_and_its_strings_are_translated():
    page = (Path(__file__).parents[1] / "static" / "settings.html").read_text("utf-8")
    assert 'id="archive"' in page and "/api/v1/archive/fit.zip" in page
    zh = json.loads((Path(__file__).parents[1] / "static" / "i18n" / "zh-TW" / "settings.json").read_text("utf-8"))
    en = json.loads((Path(__file__).parents[1] / "static" / "i18n" / "en" / "settings.json").read_text("utf-8"))
    keys = [k for k in zh if k.startswith("arch.")]
    assert len(keys) >= 8 and all(k in en for k in keys)
    # the ? buttons show their text on a tap too (a phone has no hover title) — SP-750: in the shell's popup
    # for every settings ? (no more ar-note line under the section); the kept ones are
    # part of the last cleanup's sentence, before the next date
    shell = (Path(__file__).parents[1] / "static" / "shell.js").read_text("utf-8")
    assert '", button.q[title]:not([data-tip])"' in shell and 'id="ar-note"' not in page
    assert page.index('"settings.arch.kept"') < page.index('"settings.arch.next"')
    act_zh = json.loads((Path(__file__).parents[1] / "static" / "i18n" / "zh-TW" / "activity.json").read_text("utf-8"))
    assert "archived" in act_zh
