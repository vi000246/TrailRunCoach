"""SP-394 (SP-338 follow-ups, second part — 第 3、4 條): the data's age tiers in the queue (hot ≤ 90
days at the trigger's priority, then warm / cold in the background, newest first), the request path's
Dataset build through the queue (`build`: a cap, one build per tenant, no deadlock with the warm-up
or a single flight), the activity classification in age tiers after a version change, the old series
rows swept only once the back-fill is done, and a first import / a re-parse built hot first (the last
90 days parsed and computed, the older activities back-filled newest first at `warm` / `cold` with
their progress on the page, other tenants' work going on meanwhile).

Synthetic only: fake builds and SimpleNamespace Datasets, synthetic FIT folders (fit_builder), tmp
roots."""
from __future__ import annotations

import datetime as dt
import threading
import time
from types import SimpleNamespace

import pytest

from backend import jobs, tenancy


@pytest.fixture(autouse=True)
def clean():
    jobs.reset()
    yield
    jobs.reset()


def _wait(cond, s=5.0):
    end = time.monotonic() + s
    while not cond() and time.monotonic() < end:
        time.sleep(0.005)
    return cond()


def _hold(**kw):
    """A queued job that runs until released: (job, release, started)."""
    release, started = threading.Event(), threading.Event()

    def body(job):
        started.set()
        assert release.wait(10)
    return jobs.submit(body, **kw), release, started


def _user(tmp_path, tid):
    return tenancy.Tenant(id=tid, kind=tenancy.USER, root=tmp_path / tid, shared=tmp_path / tid)


# ---------------------------------------------------------------------------- the queue

def test_the_age_tiers_and_their_priorities():
    today = dt.date(2026, 10, 12)
    days = {0: "hot", 90: "hot", 91: "warm", 365: "warm", 366: "cold", 3000: "cold"}
    assert {d: jobs.tier_of(today - dt.timedelta(days=d), today) for d in days} == days
    assert (jobs.HOT_DAYS, jobs.WARM_DAYS) == (90, 365)
    assert [jobs.tier_priority(t, "sync_hot") for t in jobs.TIERS] == ["sync_hot", "warm", "cold"]
    assert jobs.tier_priority("hot") == "deploy_hot"


def test_the_build_class_two_places_background_work_takes_one():
    b1, rel1, st1 = _hold(cls="build", priority="cold", tenant="new-user")
    b2, rel2, st2 = _hold(cls="build", priority="cold", tenant="other-new-user")
    assert st1.wait(5)
    time.sleep(0.05)
    assert b2.state == "queued"                          # one place for background work only
    u1, relu1, stu1 = _hold(cls="build", priority="user", tenant="a")
    assert stu1.wait(5)                                  # a request finds the other one at once
    u2, relu2, stu2 = _hold(cls="build", priority="user", tenant="b")
    time.sleep(0.05)
    assert u2.state == "queued" and jobs.CAPS["build"] == 2      # the cap holds
    relu1.set()
    assert stu2.wait(5) and b2.state == "queued"         # the request goes first, background still waits
    for r in (rel1, rel2, relu2):
        r.set()
    for j in (b1, b2, u1, u2):
        j.join(5)
    assert all(j.state == "done" for j in (b1, b2, u1, u2))


def test_call_with_a_key_shares_one_job_and_its_result():
    gate, runs = threading.Event(), []

    def build():
        runs.append(jobs.current().id)
        assert gate.wait(10)
        return object()
    got, errs = [], []

    def ask():
        got.append(jobs.call(build, cls="build", priority="user", key=("dataset", "u2")))
    ts = [threading.Thread(target=ask) for _ in range(5)]
    for t in ts:
        t.start()
    assert _wait(lambda: len(runs) == 1)
    time.sleep(0.05)
    gate.set()
    for t in ts:
        t.join(5)
    assert len(runs) == 1 and len(got) == 5 and all(g is got[0] for g in got)    # one build, one result
    done = jobs.find(("dataset", "u2"))
    assert done.state == "done" and done.shared is None and done.result is None  # nothing kept on the job

    class Bad(RuntimeError):
        pass
    gate.clear()

    def bad():
        assert gate.wait(10)
        raise Bad("x")

    def ask_bad():
        try:
            jobs.call(bad, cls="build", priority="user", key=("dataset", "u3"))
        except Bad as e:
            errs.append(e)
    ts = [threading.Thread(target=ask_bad) for _ in range(3)]
    for t in ts:
        t.start()
    time.sleep(0.1)
    gate.set()
    for t in ts:
        t.join(5)
    assert len(errs) == 3                                # every waiting caller gets the error


# ---------------------------------------------------------------------------- the request path's Dataset

class _Builds:
    """A fake Dataset build behind wko5views._dataset: records where it ran, held at a gate."""

    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()
        self.lock = threading.Lock()
        self.runs: list = []
        self.now = 0
        self.most = 0

    def __call__(self, cfg, source, stamp, shared):
        j = jobs.current()
        with self.lock:
            self.runs.append((shared, None if j is None else (j.cls, j.priority)))
            self.now += 1
            self.most = max(self.most, self.now)
        try:
            assert self.gate.wait(10)
            return SimpleNamespace(shared=shared, workouts=[])
        finally:
            with self.lock:
                self.now -= 1


@pytest.fixture
def views(monkeypatch):
    from backend.api import wko5views as WV
    builds = _Builds()
    monkeypatch.setattr(WV, "DATASETS_MAX", 50)          # the cache's own LRU is not the subject here
    monkeypatch.setattr(WV, "_dataset_cfg", WV._DatasetCache(builds))
    monkeypatch.setattr(WV, "_FLIGHT", {})
    monkeypatch.setattr(WV, "_dataset_key",
                        lambda parity=None, source=None: ("{}", "coros", "s1", str(tenancy.current().shared)))
    return WV, builds


def test_a_request_builds_the_dataset_in_a_build_job_once_per_tenant(views, tmp_path):
    WV, builds = views
    builds.gate.clear()
    out: dict = {}

    def ask(tid):
        with tenancy.use(_user(tmp_path, tid)):
            ds = WV._dataset()
        out.setdefault(tid, []).append(ds)
    ts = [threading.Thread(target=ask, args=(f"u{i}",)) for i in range(10) for _ in range(3)]
    for t in ts:
        t.start()
    assert _wait(lambda: len(builds.runs) == 2)
    time.sleep(0.1)
    assert len(builds.runs) == 2                         # 10 idle tenants at once: two builds at a time
    builds.gate.set()
    for t in ts:
        t.join(10)
    assert builds.most == jobs.CAPS["build"] == 2
    assert len(builds.runs) == 10                        # one build per tenant, however many requests
    assert {r[1] for r in builds.runs} == {("build", jobs.PRIORITIES["user"])}
    for tid, got in out.items():
        assert len(got) == 3 and all(g is got[0] for g in got) and got[0].shared.endswith(tid)
    with tenancy.use(_user(tmp_path, "u3")):
        assert WV._dataset() is out["u3"][0] and len(builds.runs) == 10        # built: no job at all


def test_a_request_while_the_warm_up_builds_the_same_dataset_waits_without_a_place(views, tmp_path):
    WV, builds = views
    builds.gate.clear()
    t = _user(tmp_path, "u2")
    with tenancy.use(t):
        warm = jobs.submit(lambda job: WV._dataset(), cls="full", priority="deploy_hot", label="warm-up")
    assert _wait(lambda: len(builds.runs) == 1)
    # both build places taken by other tenants' requests: the request of u2 must not need one
    o1, rel1, st1 = _hold(cls="build", priority="user", tenant="x1")
    o2, rel2, st2 = _hold(cls="build", priority="user", tenant="x2")
    assert st1.wait(5) and st2.wait(5)
    got = []

    def ask():
        with tenancy.use(t):
            got.append(WV._dataset())
    th = threading.Thread(target=ask)
    th.start()
    time.sleep(0.1)
    builds.gate.set()
    th.join(5)
    warm.join(5)
    assert got and got[0] is warm.result                 # the warm-up's Dataset
    assert builds.runs == [(str(t.shared), ("full", jobs.PRIORITIES["deploy_hot"]))]      # built once, inline
    rel1.set()
    rel2.set()


def test_a_single_flights_leader_builds_inline(views, tmp_path):
    """The warm-up (`full`) joins the status / plan-inputs flights: their leader must never wait
    for a place in the queue (backend/jobs.py's deadlock rule)."""
    from backend.singleflight import SingleFlight
    WV, builds = views
    o1, rel1, st1 = _hold(cls="build", priority="user", tenant="x1")
    o2, rel2, st2 = _hold(cls="build", priority="user", tenant="x2")
    assert st1.wait(5) and st2.wait(5)                   # no build place free
    flight = SingleFlight()
    with tenancy.use(_user(tmp_path, "u2")):
        ds = flight.do("inputs", WV._dataset)
    assert ds.shared.endswith("u2") and builds.runs == [(str(tmp_path / "u2"), None)]
    rel1.set()
    rel2.set()


def test_inside_a_job_the_dataset_is_built_in_that_job(views, tmp_path):
    WV, builds = views
    with tenancy.use(_user(tmp_path, "u2")):
        j = jobs.submit(lambda job: WV._dataset(), cls="single", priority="user")
    j.join(5)
    assert j.state == "done" and builds.runs == [(str(tmp_path / "u2"), ("single", jobs.PRIORITIES["user"]))]


# ---------------------------------------------------------------------------- the classification in tiers

TODAY = dt.date(2026, 10, 12)
AGES = (5, 20, 60, 150, 300, 400, 600, 900)            # 3 hot, 2 warm, 3 cold


def _aged_ds(tmp_path, ages=AGES):
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.corrections import CorrectionStore
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros"
    for i, age in enumerate(ages):
        start = dt.datetime.combine(TODAY - dt.timedelta(days=age), dt.time(1, 0), tzinfo=dt.timezone.utc)
        p = d / str(start.year) / f"a{age:04d}.fit"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(build_run(start, seconds=1500 + 60 * i, speed_m_s=3.0, hr=135 + i))
    return FitFolderDataset(d, config=EngineConfig(parity=False), today=TODAY,
                            corrections=CorrectionStore(tmp_path / "corr.json"), classifications={},
                            athlete_settings=[], estimate_thresholds=False, tz=dt.timezone.utc)


@pytest.fixture
def auto(monkeypatch):
    from backend.api import activity_auto as AA
    from backend.engine import activity_tags as AT
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(AT, "load_recorded", lambda *a, **k: [])
    monkeypatch.setattr(AA, "FULL_FROM", 3)
    return AA


def _tier(name):
    return jobs.tier_of(TODAY - dt.timedelta(days=int(name[1:5])), TODAY)


def _spy_steps(AA, monkeypatch, gate=None):
    """Every compute_blocking call: (its files newest first, the job's class, priority)."""
    real, steps = AA.compute_blocking, []

    def spy(ds, progress=None, recorded=None, only=None, context=None):
        j = jobs.current()
        names = [w.entry.file.split("/")[-1] for w in sorted((w for w in ds.workouts if only is None or w.idx in only),
                                                             key=lambda w: w.entry.start, reverse=True)]
        steps.append((names, j.cls, j.priority))
        if gate is not None and len(steps) == 2:
            assert gate.wait(10)
        return real(ds, progress=progress, recorded=recorded, only=only, context=context)
    monkeypatch.setattr(AA, "compute_blocking", spy)
    return steps


def test_after_a_code_change_the_classification_computes_hot_first_then_warm_and_cold(tmp_path, auto, monkeypatch):
    AA = auto
    ds = _aged_ds(tmp_path)
    assert AA.wait(ds)["state"] == "ready"                  # every value stored for the old code
    real = AA._branch_code()
    monkeypatch.setattr(AA, "_branch_code", lambda: {b: h + "-edited" for b, h in real.items()})   # a deploy
    gate = threading.Event()
    steps = _spy_steps(AA, monkeypatch, gate)
    ds2 = _aged_ds(tmp_path)
    assert AA.to_compute_tiers(ds2, AA.load_cache(ds2)) == {"hot": 3, "warm": 2, "cold": 3}
    job = AA.job_for(ds2, priority="deploy_hot")
    assert job.thread.priority == jobs.PRIORITIES["deploy_hot"] and job.thread.cls == "full"
    # the hot tier done, the warm one computing (held): the hot values are the new ones, kept on
    # disk without the signature (a restart now recomputes the rest), the older ones still the old
    assert _wait(lambda: len(steps) == 2)
    snap = job.snapshot()
    assert snap["state"] == "computing" and snap["tier"] == "warm" and snap["n_done"] == 3 and snap["stale"]
    disk = AA.load_cache(ds2)
    assert disk["sig"] is None and len(disk["files"]) == len(AGES)
    gate.set()
    assert AA.wait(ds2)["state"] == "ready"
    names = [s[0] for s in steps]
    assert names == [["a0005.fit", "a0020.fit", "a0060.fit"], ["a0150.fit", "a0300.fit"],
                     ["a0400.fit", "a0600.fit", "a0900.fit"]]           # newest first, tier by tier
    assert [s[1:] for s in steps] == [("full", jobs.PRIORITIES["deploy_hot"]), ("single", jobs.PRIORITIES["warm"]),
                                      ("full", jobs.PRIORITIES["cold"])]
    assert AA.load_cache(ds2)["sig"] == job.sig and job.snapshot()["stale"] is False
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "static"
    import json
    assert 'r.tier === "warm"' in (static / "activity.html").read_text("utf-8")      # the page says so
    for loc in ("zh-TW", "en"):
        assert "{done}" in json.loads((static / "i18n" / loc / "activity.json").read_text("utf-8"))["auto_progress_older"]
    # the next start (a restart) answers everything from disk
    assert AA.Job(_aged_ds(tmp_path), job.sig, []).state == "ready"


def test_a_few_to_compute_stay_one_job_and_the_list_page_promotes_only_the_hot_tier(tmp_path, auto, monkeypatch):
    AA = auto
    steps = _spy_steps(AA, monkeypatch)
    ds = _aged_ds(tmp_path, ages=(5, 400))                   # 2 < FULL_FROM: one job, as before
    job = AA.job_for(ds, priority="sync_hot")
    assert job.thread.cls == "single" and job.thread.priority == jobs.PRIORITIES["sync_hot"]
    assert AA.wait(ds)["state"] == "ready" and len(steps) == 1
    # a tiered job: the 活動列表 (someone looking) moves the hot tier up, never the cold back-fill
    blocker, release, started = _hold(cls="full", priority="user", tenant="x")
    assert started.wait(5)
    ds2 = _aged_ds(tmp_path / "b")
    job2 = AA.job_for(ds2, priority="warm")
    assert job2.thread.state == "queued" and job2.thread.priority == jobs.PRIORITIES["warm"]
    AA.status(ds2)
    assert job2.thread.priority == jobs.PRIORITIES["user"]
    release.set()
    assert job2.join(10) or job2.snapshot()["state"] == "ready"
    assert [s[2] for s in steps[1:]] == [jobs.PRIORITIES["user"], jobs.PRIORITIES["warm"], jobs.PRIORITIES["cold"]]


def test_a_change_between_tiers_restarts_from_the_keys_and_moves_the_done_callbacks(tmp_path, auto, monkeypatch):
    AA = auto
    ds = _aged_ds(tmp_path)
    gate = threading.Event()
    steps = _spy_steps(AA, monkeypatch, gate)
    job = AA.job_for(ds, priority="deploy_hot")
    done = []
    job.when_done(lambda: done.append("first"), key="sweep")
    job.when_done(lambda: done.append("sweep"), key="sweep")                # one per key: the latest
    assert _wait(lambda: len(steps) == 2)                    # the warm tier computing (held)
    blocker, release, started = _hold(cls="full", priority="user", tenant="x")   # the only full place
    assert started.wait(5)
    gate.set()
    assert _wait(lambda: job.step == "cold" and job.thread.state == "queued")   # the cold tier waits for it
    monkeypatch.setattr(AA, "signature", lambda d, r: "a plan edit")
    job2 = AA.job_for(ds, priority="deploy_hot")
    assert job2 is not job and job.superseded and job.join(1) and done == []
    release.set()
    assert AA.wait(ds)["state"] == "ready" and done == ["sweep"]           # once, after the new job's last tier
    assert job2.computed and len(job2.computed) < len(AGES)                 # the keys kept what was done


# ---------------------------------------------------------------------------- the old version goes last

def test_the_sweep_can_be_held_to_one_tenants_store(tmp_path, monkeypatch):
    import os
    from backend.engine.wko5expr import dataset as D
    from backend.engine.wko5expr import seriesdb
    monkeypatch.setattr(D, "SERIES_SEEN", set())
    monkeypatch.setattr(D, "SERIES_ROWS_SEEN", {})
    week3 = time.time() - 21 * 86400
    a = seriesdb.open_db(tmp_path / "a" / seriesdb.FILE_NAME)
    b = seriesdb.open_db(tmp_path / "b" / seriesdb.FILE_NAME)
    for db in (a, b):
        db.put([("k_v2", "f1", "new", "s", "1.fit", 1)])
        db.put([("k_v2", "f1", "old", "s", "1.fit", 1)], written=week3)
        D.series_rows_seen(db.path, "k_v2", "new")
        old_json = db.path.parent / "series_k_v0_1234abcd.json"
        old_json.write_text("{}", "utf-8")
        os.utime(old_json, (week3, week3))
    assert D.sweep_series(stores=[a.path]) == 2                            # a's old row and old file only
    assert not (tmp_path / "a" / "series_k_v0_1234abcd.json").exists()
    assert (tmp_path / "b" / "series_k_v0_1234abcd.json").exists()
    assert D.sweep_series() == 2                                           # b's, when nothing is held


def test_the_warm_up_sweeps_the_old_series_rows_only_after_the_last_tier(tmp_path, auto, monkeypatch):
    from backend.api import racepower as RP
    from backend.api import wko5views as WV
    from backend.engine import calibrate as CAL
    from backend.engine import plan_auto
    from backend.engine.wko5expr import dataset as D
    AA = auto
    ds = _aged_ds(tmp_path)
    assert AA.wait(ds)["state"] == "ready"
    real = AA._branch_code()
    monkeypatch.setattr(AA, "_branch_code", lambda: {b: h + "-edited" for b, h in real.items()})
    ds2 = _aged_ds(tmp_path)
    order = []
    steps = _spy_steps(AA, monkeypatch)
    monkeypatch.setattr(D, "sweep_series", lambda days=D.SERIES_SWEEP_DAYS, stores=None:
                        order.append(("sweep", [str(p) for p in stores or []], len(steps))) or 0)
    monkeypatch.setattr(WV, "_dataset", lambda: ds2)
    monkeypatch.setattr(plan_auto, "busy", lambda: False)
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(RP, "warm_charts", lambda job=None: order.append(("charts", job.step)))
    WV._warm_slot()["low_again"] = False
    WV._low_priority("startup")
    job = AA.job_for(ds2)
    assert job.join(10)
    assert [s[2] for s in steps] == [jobs.PRIORITIES["deploy_hot"], jobs.PRIORITIES["warm"], jobs.PRIORITIES["cold"]]
    assert order[-1] == ("sweep", [str(ds2._series_home() / "series.db")], 3)    # after the cold tier, this store
    assert [o for o in order if o[0] == "sweep"] == [order[-1]]


def test_the_warm_up_itself_no_longer_sweeps(monkeypatch):
    from backend.api import activity_auto as AA
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.api import racepower as RP
    from backend.api import wko5views as WV
    from backend.engine import plan_auto
    from backend.engine.wko5expr import dataset as D
    swept = []
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    monkeypatch.setattr(WV, "_WARM", {})
    monkeypatch.setattr(WV, "_dataset", lambda: SimpleNamespace(today=20000.0))
    monkeypatch.setattr(WV, "_warm_chart_code", lambda d: None)
    monkeypatch.setattr(OV, "_status", lambda d, today: None)
    monkeypatch.setattr(PSA, "_compute_inputs", lambda: None)
    from backend.engine import calibrate as CAL
    plan_end = time.monotonic() + 0.3
    monkeypatch.setattr(plan_auto, "busy", lambda: time.monotonic() < plan_end)   # a plan run first
    monkeypatch.setattr(WV, "_PLAN_POLL_S", 0.01)
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(D, "sweep_series", lambda *a, **k: swept.append(1) or 0)
    monkeypatch.setattr(AA, "job_for", lambda d, **kw: None)
    monkeypatch.setattr(RP, "warm_charts", lambda job=None: None)
    t = WV.warm_up("startup")
    t.join(10)
    assert t.state == "done" and swept == []
    low = WV._warm_slot().get("low")
    low and low.join(10)
    assert swept == []                                           # no classification job: nothing to wait for


# ---------------------------------------------------------------------------- a first import, hot first

HOT_AGES = (3, 30, 80)
OLD_AGES = (100, 150, 200, 300, 400, 500, 700, 900)          # 4 warm, 4 cold


@pytest.fixture
def first_import(tmp_path, monkeypatch):
    """Tenant u2's first import: 11 COROS files never parsed (dated in their names), the app's
    Dataset cache and builds (no app DB: no estimates), a small HOT_FIRST_FROM / CHUNK_FILES."""
    import json
    from backend.api import backfill
    from backend.api import wko5views as WV
    from backend.engine.racepower import cptest as T
    from backend.engine.wko5expr import datasource as DSRC
    from backend.engine.wko5expr import fitdataset as FD
    from backend.engine.wko5expr.config import EngineConfig
    from backend.settings.paths import for_tenant
    from backend.sync import storage
    from backend.tests.fit_builder import build_run
    monkeypatch.setattr(FD, "HOT_FIRST_FROM", 5)
    monkeypatch.setattr(backfill, "CHUNK_FILES", 3)
    monkeypatch.setattr(backfill, "_STATE", {})
    monkeypatch.setattr(T, "_HELD", {})
    monkeypatch.setattr(WV, "_dataset_cfg", WV._DatasetCache(WV._build_dataset))
    monkeypatch.setattr(WV, "_FLIGHT", {})
    warmed, real_warm_up = [], WV.warm_up
    monkeypatch.setattr(WV, "warm_up", lambda reason="startup": warmed.append((tenancy.current().id, reason)))
    cfg = json.dumps(EngineConfig(parity=False).to_dict(), sort_keys=True)
    monkeypatch.setattr(WV, "_dataset_key", lambda parity=None, source=None: (
        cfg, "coros", DSRC.source_stamp("coros", for_tenant(WV.ATHLETE_DIR)), str(tenancy.current().shared)))
    u2 = _user(tmp_path, "u2")
    monkeypatch.setattr(storage, "FIT_ROOT", tmp_path / "u2" / "fit")      # as in production: the tenant's fit/
    today = dt.date.today()
    with tenancy.use(u2):
        d = storage.source_dir("coros")
        for i, age in enumerate(HOT_AGES + OLD_AGES):
            day = today - dt.timedelta(days=age)
            p = d / str(day.year) / f"run_{day.isoformat()}_{age:04d}.fit"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(build_run(dt.datetime.combine(day, dt.time(1, 0), tzinfo=dt.timezone.utc),
                                    seconds=1500 + 60 * i, speed_m_s=3.0, hr=135 + i))
    return SimpleNamespace(WV=WV, backfill=backfill, T=T, u2=u2, warmed=warmed, today=today, dir=d,
                           root=tmp_path / "u2" / "fit", real_warm_up=real_warm_up)


def _age(path) -> int:
    from pathlib import Path
    return int(Path(str(path)).name.rsplit("_", 1)[-1][:4])


def test_a_first_import_parses_the_last_90_days_first_and_back_fills_the_rest(first_import, monkeypatch):
    from backend.engine.wko5expr import fitcache
    F = first_import
    WV, T, BF = F.WV, F.T, F.backfill
    real_parse, real_build = fitcache.parse_file, WV._build_dataset
    events, chunk_gate, final_gate = [], threading.Event(), threading.Event()

    def parse(path, npz):
        j = jobs.current()
        events.append(("parse", _age(path), None if j is None else (j.cls, j.priority)))
        if sum(1 for e in events if e[0] == "parse") == len(HOT_AGES) + 1:
            assert chunk_gate.wait(10)                   # the first back-fill chunk: held
        return real_parse(path, npz)

    def build(*key, hot_first=None):
        if hot_first is False:
            j = jobs.current()
            events.append(("complete build", (j.cls, j.priority)))
            assert final_gate.wait(10)
        return real_build(*key, hot_first=hot_first)
    monkeypatch.setattr(fitcache, "parse_file", parse)
    monkeypatch.setattr(WV, "_build_dataset", build)
    monkeypatch.setattr(WV, "_dataset_cfg", WV._DatasetCache(build))
    with tenancy.use(F.u2):
        t0 = time.monotonic()
        ds = WV._dataset()                               # the first page load after the import
        took = time.monotonic() - t0
        # the last 90 days only: parsed in the request's build job, the overview / 課表 Dataset ready
        assert sorted(_age(w.entry.file) for w in ds.workouts) == list(HOT_AGES)
        assert [e for e in events if e[0] == "parse"][:3] == [("parse", a, ("build", jobs.PRIORITIES["user"]))
                                                              for a in sorted(HOT_AGES, reverse=True)] or \
            sorted(e[1] for e in events[:3]) == sorted(HOT_AGES)
        assert [_age(p) for _, p in ds.deferred] == list(OLD_AGES)          # newest first
        assert len(T.held(F.root)) == len(OLD_AGES)      # the race-power reads leave them out too
        assert _wait(lambda: len(events) == len(HOT_AGES) + 1)             # the first chunk started
        pr = BF.progress()
        assert pr["state"] == "running" and pr["step"] == "parse" and (pr["n_done"], pr["n_total"]) == (0, 8)
    # meanwhile another tenant synced: its warm-up goes before the next chunk (hot before warm)
    u3 = jobs.submit(lambda job: events.append(("u3 sync warm-up",)), cls="full", priority="sync_hot",
                     tenant="u3", label="dataset warm-up (sync-coros)")
    assert u3.state == "queued"
    chunk_gate.set()
    assert _wait(lambda: any(e[0] == "complete build" for e in events), 10)
    # the complete build holds a `build` place at `cold`: another tenant's request builds at once
    u4, rel4, st4 = _hold(cls="build", priority="user", tenant="u4")
    assert st4.wait(5)
    rel4.set()
    with tenancy.use(F.u2):
        assert WV._dataset() is ds                       # until the complete one is in: the hot-first one
    final_gate.set()
    assert _wait(lambda: F.warmed == [("u2", "backfill")], 10)
    parses = [e for e in events if e[0] == "parse"]
    assert [e[1] for e in parses[3:]] == list(OLD_AGES)  # each file once, the older ones newest first
    assert [e[2] for e in parses[3:]] == [("full", jobs.PRIORITIES["warm"])] * 6 + [("full", jobs.PRIORITIES["cold"])] * 2
    names = [e[0] for e in events]
    assert names.index("u3 sync warm-up") == names.index("parse") + len(HOT_AGES) + 3      # between two chunks
    assert ("complete build", ("build", jobs.PRIORITIES["cold"])) in events
    with tenancy.use(F.u2):
        full = WV._dataset()
        assert full is not ds and sorted(_age(w.entry.file) for w in full.workouts) == sorted(HOT_AGES + OLD_AGES)
        assert not full.deferred and BF.progress() is None
    assert T.held(F.root) == frozenset()
    assert took < 10


def test_the_back_fill_progress_is_on_the_dataset_status_and_the_page_shows_it(first_import, monkeypatch):
    import json
    from pathlib import Path
    from fastapi.testclient import TestClient
    from backend.main import build_app
    F = first_import
    gate = threading.Event()
    real = F.backfill._chunk
    monkeypatch.setattr(F.backfill, "_chunk", lambda bf, part: gate.wait(10) and real(bf, part))
    with tenancy.use(F.u2):
        F.WV._dataset()
    monkeypatch.setattr(F.backfill, "_tid", lambda: "u2")
    with TestClient(build_app(demo=False)) as c:
        bf = c.get("/api/v1/wko5/dataset/status").json()["backfill"]
    assert bf["state"] == "running" and (bf["n_done"], bf["n_total"]) == (0, len(OLD_AGES))
    gate.set()
    assert _wait(lambda: F.warmed, 10)
    static = Path(__file__).resolve().parents[1] / "static"
    shell = (static / "shell.js").read_text("utf-8")
    assert "s.backfill" in shell and 'T("build.backfill"' in shell and 'T("build.backfill_done")' in shell
    for loc in ("zh-TW", "en"):
        cat = json.loads((static / "i18n" / loc / "shell.json").read_text("utf-8"))
        assert "{done}" in cat["build.backfill"] and "{total}" in cat["build.backfill"] and cat["build.backfill_done"]


def test_a_sync_of_a_few_files_is_never_deferred(first_import, monkeypatch):
    """A sync's few new files are parsed at once, whatever their age (HOT_FIRST_FROM)."""
    from backend.engine.wko5expr import fitdataset as FD
    F = first_import
    monkeypatch.setattr(FD, "HOT_FIRST_FROM", 100)
    with tenancy.use(F.u2):
        ds = F.WV._dataset()
    assert len(ds.workouts) == len(HOT_AGES + OLD_AGES) and not ds.deferred and F.backfill.progress() is None


def test_after_the_first_sync_the_overview_and_plan_inputs_come_before_the_back_fill(first_import, monkeypatch):
    """The warm-up after a new user's first sync (runner.after_import): the Dataset of the last 90
    days, then the overview status and the 課表's plan inputs on it — all before the first chunk of
    the back-fill; the older activities after it, at `warm` / `cold`."""
    from backend.api import activity_auto as AA
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.api import racepower as RP
    from backend.engine import calibrate as CAL
    from backend.engine import plan_auto
    from backend.engine.wko5expr import fitcache
    F = first_import
    WV, BF = F.WV, F.backfill
    seen = []
    real_parse = fitcache.parse_file

    def parse(path, npz):
        j = jobs.current()
        seen.append(("parse", _age(path), j.label))
        return real_parse(path, npz)
    monkeypatch.setattr(fitcache, "parse_file", parse)
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    monkeypatch.setattr(WV, "_WARM", {})
    monkeypatch.setattr(WV, "warm_up", lambda reason="startup": F.real_warm_up(reason) if reason.startswith("sync")
                        else F.warmed.append((tenancy.current().id, reason)))
    monkeypatch.setattr(WV, "_warm_chart_code", lambda d: None)
    monkeypatch.setattr(OV, "_status", lambda d, today: seen.append(("status", len(d.workouts), BF.progress()["n_done"])))
    monkeypatch.setattr(PSA, "_compute_inputs", lambda: seen.append(("inputs", len(WV._dataset().workouts))))
    monkeypatch.setattr(plan_auto, "busy", lambda: False)
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(AA, "job_for", lambda d, **kw: seen.append(("classify", len(d.workouts), kw.get("priority"))))
    monkeypatch.setattr(RP, "warm_charts", lambda job=None: None)
    with tenancy.use(F.u2):
        t = WV.warm_up("sync-coros")
    t.join(10)
    assert t.state == "done" and t.priority == jobs.PRIORITIES["sync_hot"]
    assert [x for x in seen if x[0] != "parse"][:2] == [("status", 3, 0), ("inputs", 3)]
    assert {x[2] for x in seen if x[0] == "parse" and x[1] in HOT_AGES} == {"dataset warm-up (sync-coros)"}
    assert _wait(lambda: F.warmed == [("u2", "backfill")], 10)
    first_chunk = next(i for i, x in enumerate(seen) if x[0] == "parse" and x[1] not in HOT_AGES)
    assert seen.index(("inputs", 3)) < first_chunk          # the pages' Dataset, status and inputs first
    assert ("classify", 3, "sync_hot") in seen               # the hot-first Dataset's classification: hot tier first
    low = WV._warm_slot(F.u2.id).get("low")
    low and low.join(10)


def test_a_failed_hot_first_build_releases_what_it_held(first_import, monkeypatch):
    from backend.engine.wko5expr import fitdataset as FD
    F = first_import
    real = FD.FitFolderDataset._apply_elevation_bonus

    def boom(self):
        raise RuntimeError("build failed late")
    monkeypatch.setattr(FD.FitFolderDataset, "_apply_elevation_bonus", boom)
    with tenancy.use(F.u2):
        with pytest.raises(RuntimeError):
            F.WV._dataset()
    assert F.T.held(F.root) == frozenset() and F.backfill.progress() is None
    monkeypatch.setattr(FD.FitFolderDataset, "_apply_elevation_bonus", real)
