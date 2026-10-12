"""SP-394 (SP-338 follow-ups, first part): the automatic classification, the overview status and
the warm-up's low-priority steps go through the server's job queue (backend/jobs.py) — its
priorities and its places — and the monthly archive (SP-317) has its entry there; after a deploy
only the tenants active in the last 7 days (signed in or synced) are warmed up ahead, behind the
owner, newest first; another tenant's first overview after a restart shows the previous status
marked 更新中 while the new one is computed in the queue, and the page swaps it in by itself.

Synthetic only: fake Status objects, SimpleNamespace Datasets, synthetic FIT folders
(fit_builder), a tmp server root."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
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
    """A queued job that runs until released: (job, release)."""
    release, started = threading.Event(), threading.Event()

    def body(job):
        started.set()
        assert release.wait(10)
    j = jobs.submit(body, **kw)
    return j, release, started


def _user(tmp_path, tid):
    return tenancy.Tenant(id=tid, kind=tenancy.USER, root=tmp_path / tid, shared=tmp_path / tid)


# ---------------------------------------------------------------------------- the queue itself

def test_call_waits_for_its_job_and_runs_at_once_inside_a_job():
    me = threading.get_ident()
    seen = []

    def fn():
        j = jobs.current()
        seen.append((threading.get_ident() != me, j.cls, j.priority, j.label))
        return 42
    assert jobs.current() is None
    assert jobs.call(fn, cls="single", priority="user", label="x") == 42
    assert seen == [(True, "single", jobs.PRIORITIES["user"], "x")]       # in the queue's own thread

    class Boom(RuntimeError):
        pass

    def bad():
        raise Boom("y")
    with pytest.raises(Boom):                                             # the caller gets fn's own error
        jobs.call(bad)

    # inside a `full` job (the only full place): another full call runs in that job's thread,
    # it does not queue behind itself; a thread the job starts is not "inside" it
    inner = []

    def outer(job):
        t = threading.Thread(target=lambda: inner.append(("thread", jobs.current())))
        t.start()
        t.join(5)
        return jobs.call(lambda: inner.append(("call", jobs.current() is job)) or "ok", cls="full")
    o = jobs.submit(outer, cls="full", priority="warm")
    o.join(5)
    assert o.state == "done" and o.result == "ok"
    assert inner == [("thread", None), ("call", True)]


def test_a_job_someone_waits_for_may_take_the_tenants_second_single_place():
    a1, rel_a1, st_a1 = _hold(cls="single", priority="warm", tenant="A")
    assert st_a1.wait(5)
    a2, rel_a2, st_a2 = _hold(cls="single", priority="warm", tenant="A")
    au, rel_au, st_au = _hold(cls="single", priority="user", tenant="A")
    assert st_au.wait(5)                                 # the request's job did not wait for A's background one
    assert a2.state == "queued"                          # background work: still one place per tenant
    for r in (rel_a1, rel_a2, rel_au):
        r.set()
    for j in (a1, a2, au):
        j.join(5)
    assert all(j.state == "done" for j in (a1, a2, au))


def test_promote_moves_a_queued_job_ahead():
    blocker, release, started = _hold(cls="full", priority="warm")
    assert started.wait(5)
    cold = jobs.submit(lambda job: None, cls="full", priority="cold")
    warm = jobs.submit(lambda job: None, cls="full", priority="warm")
    assert [jobs.position(j) for j in (warm, cold)] == [1, 2]
    jobs.promote(cold, "user")
    assert [jobs.position(j) for j in (cold, warm)] == [1, 2]
    jobs.promote(cold, "archive")                        # never demoted
    assert jobs.position(cold) == 1
    release.set()
    for j in (blocker, cold, warm):
        j.join(5)


def test_the_monthly_archive_has_its_entry_and_goes_last():
    """SP-317 is not built yet: its job enters here, at the lowest priority (03:00–05:00)."""
    blocker, release, started = _hold(cls="full", priority="user")
    assert started.wait(5)
    order = []
    arch = jobs.submit_archive(lambda job: order.append("archive"), tenant="u2")
    cold = jobs.submit(lambda job: order.append("cold"), cls="full", priority="cold", tenant="u3")
    warm = jobs.submit(lambda job: order.append("warm"), cls="full", priority="warm", tenant="u4")
    assert arch.cls == "full" and arch.priority == jobs.PRIORITIES["archive"] == max(jobs.PRIORITIES.values())
    assert [jobs.position(j) for j in (warm, cold, arch)] == [1, 2, 3]
    release.set()
    for j in (blocker, arch, cold, warm):
        j.join(5)
    assert order == ["warm", "cold", "archive"]


# ---------------------------------------------------------------------------- the classification

@pytest.fixture
def auto(monkeypatch):
    from backend.api import activity_auto as AA
    from backend.engine import activity_tags as AT
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(AT, "load_recorded", lambda *a, **k: [])
    return AA


def _fit_ds(tmp_path, n):
    from backend.tests.test_activity_auto import _fit_ds as make
    return make(tmp_path, n=n)


def test_the_classification_is_a_queue_job_full_for_everything_single_for_a_few(tmp_path, auto, monkeypatch):
    AA = auto
    monkeypatch.setattr(AA, "FULL_FROM", 3)
    ds = _fit_ds(tmp_path, 4)
    job = AA.job_for(ds)                                 # the warm-up's call: background
    q = job.thread
    assert isinstance(q, jobs.Job) and q.cls == "full" and q.priority == jobs.PRIORITIES["warm"]
    assert AA.wait(ds)["state"] == "ready"
    ds2 = _fit_ds(tmp_path, 5)                           # a sync adds one activity
    job2 = AA.job_for(ds2)
    assert job2.thread.cls == "single"
    assert AA.wait(ds2)["state"] == "ready"
    # a code change recomputes everything: `full` again, whatever is stored
    monkeypatch.setattr(AA, "_code_sig", lambda: "other code")
    assert AA.Job(_fit_ds(tmp_path, 5), "sig-x", []).cls == "full"


def test_the_list_page_promotes_the_warm_ups_classification_and_waits_for_a_place(tmp_path, auto, monkeypatch):
    AA = auto
    s1, rel1, st1 = _hold(cls="single", priority="warm", tenant="x1")
    s2, rel2, st2 = _hold(cls="single", priority="warm", tenant="x2")
    assert st1.wait(5) and st2.wait(5)                   # both single places taken
    ds = _fit_ds(tmp_path, 2)
    job = AA.job_for(ds)
    assert job.thread.state == "queued" and job.thread.priority == jobs.PRIORITIES["warm"]
    t = time.monotonic()
    snap = AA.status(ds)                                 # GET /activities/auto: someone is looking
    assert time.monotonic() - t < 2 and snap["state"] == "computing" and snap["n_done"] == 0
    assert job.thread.priority == jobs.PRIORITIES["user"] and AA.job_for(ds) is job
    rel1.set()
    assert AA.wait(ds)["state"] == "ready"
    rel2.set()
    s1.join(5)
    s2.join(5)


# ---------------------------------------------------------------------------- the overview status

class FakeStatus:
    """Stands in for engine.status.Status: records where it computed, can be held at a gate."""
    runs: list = []
    gate = None
    fail = False

    def __init__(self, ds, today, prefs):
        self.ds, self.today = ds, today

    def compute(self):
        j = jobs.current()
        FakeStatus.runs.append((tenancy.current().id, None if j is None else (j.cls, j.priority)))
        if FakeStatus.gate is not None:
            assert FakeStatus.gate.wait(10)
        if FakeStatus.fail:
            raise RuntimeError("status failed")
        return self

    def to_dict(self):
        return {"today": self.today.isoformat(), "who": getattr(self.ds, "who", None), "gen": self.ds.gen,
                "indicators": [], "actions": []}


@pytest.fixture
def ov(monkeypatch, tmp_path):
    from backend.api import overview as OV
    monkeypatch.setattr(FakeStatus, "runs", [])
    monkeypatch.setattr(FakeStatus, "gate", None)
    monkeypatch.setattr(FakeStatus, "fail", False)
    monkeypatch.setattr(OV, "Status", FakeStatus)
    monkeypatch.setattr(OV, "_status_cache", {})
    monkeypatch.setattr(OV, "_status_last", OrderedDict())
    monkeypatch.setattr(OV, "_status_refreshing", {})
    monkeypatch.setattr(OV, "_status_failed", {})
    yield OV
    _wait(lambda: not OV._status_refreshing, 3)


DAY = dt.date(2026, 10, 12)


def _ds(gen, who="u2"):
    return SimpleNamespace(today=20000.0, gen=gen, who=who)


def test_the_status_is_computed_as_a_single_job_and_inline_inside_a_job(ov, tmp_path):
    OV = ov
    with tenancy.use(_user(tmp_path, "u2")):
        OV._status(_ds(1), DAY)
        assert FakeStatus.runs == [("u2", ("single", jobs.PRIORITIES["user"]))]
        # the warm-up computes it inside its own `full` job: no second place, no wait for itself
        w = jobs.submit(lambda job: OV._status(_ds(2), DAY), cls="full", priority="sync_hot")
    w.join(5)
    assert w.state == "done" and FakeStatus.runs[-1] == ("u2", ("full", jobs.PRIORITIES["sync_hot"]))


def test_the_status_waits_for_a_place_in_the_queue(ov, tmp_path):
    OV = ov
    s1, rel1, st1 = _hold(cls="single", priority="warm", tenant="x1")
    s2, rel2, st2 = _hold(cls="single", priority="warm", tenant="x2")
    assert st1.wait(5) and st2.wait(5)
    got = []
    t = _user(tmp_path, "u2")

    def ask():
        with tenancy.use(t):
            got.append(OV._status(_ds(1), DAY))
    th = threading.Thread(target=ask)
    th.start()
    time.sleep(0.2)
    assert not got and FakeStatus.runs == []                 # both places busy: it waits its turn
    rel1.set()
    th.join(5)
    assert got and FakeStatus.runs == [("u2", ("single", jobs.PRIORITIES["user"]))]
    rel2.set()


def test_after_a_restart_the_overview_shows_the_previous_status_marked_updating(ov, tmp_path):
    OV = ov
    with tenancy.use(_user(tmp_path, "u2")):
        first = OV.status_answer(_ds(1), DAY)
        assert first["gen"] == 1 and "stale" not in first
        assert OV._last_path().exists()                      # kept on disk: it survives a restart
        OV._status_cache.clear()                             # a restart / deploy: memory is empty
        OV._status_last.clear()
        FakeStatus.gate = threading.Event()
        ds2 = _ds(2)                                         # a new Dataset after the restart
        t0 = time.monotonic()
        v = OV.status_answer(ds2, DAY)
        assert time.monotonic() - t0 < 1
        assert v["gen"] == 1 and v["stale"]["reason"] == "data" and v["stale"]["age_s"] >= 0 and v["stale"]["since"]
        assert _wait(lambda: len(FakeStatus.runs) == 2)      # computed in the background…
        assert FakeStatus.runs[-1] == ("u2", ("single", jobs.PRIORITIES["user"]))    # …as a queue job
        assert OV.status_updating() is True
        again = OV.status_answer(ds2, DAY)                   # asked again meanwhile: no second computation
        assert again["stale"] and len(FakeStatus.runs) == 2
        FakeStatus.gate.set()
        assert _wait(lambda: not OV.status_updating())
        fresh = OV.status_answer(ds2, DAY)
        assert fresh["gen"] == 2 and "stale" not in fresh and len(FakeStatus.runs) == 2


def test_a_new_day_is_stale_too_a_settings_change_a_demo_or_another_tenant_never(ov, tmp_path, monkeypatch):
    OV = ov
    u2 = _user(tmp_path, "u2")
    ds1 = _ds(1)
    with tenancy.use(u2):
        OV.status_answer(ds1, DAY)
        v = OV.status_answer(ds1, DAY + dt.timedelta(days=1))
        assert v["stale"]["reason"] == "day"
        assert _wait(lambda: not OV.status_updating())
    # another tenant never sees u2's
    with tenancy.use(_user(tmp_path, "u3")):
        v = OV.status_answer(_ds(5, who="u3"), DAY)
        assert "stale" not in v and v["who"] == "u3"
    # the user's own change (課表偏好 here): they expect its effect, computed in the request
    from backend.engine import plan_prefs as PP
    real = PP.load()
    monkeypatch.setattr(PP, "load", lambda *a, **k: dataclasses.replace(real, weekly_hours=(real.weekly_hours or 0) + 1.5))
    with tenancy.use(u2):
        v = OV.status_answer(_ds(3), DAY + dt.timedelta(days=1))
        assert "stale" not in v and v["gen"] == 3
    # one tenant per demo visitor: never kept, never served stale
    demo = dataclasses.replace(u2, id="demo-x", kind=tenancy.DEMO_SANDBOX, root=tmp_path / "demo-x",
                               shared=tmp_path / "demo-x")
    with tenancy.use(demo):
        OV.status_answer(_ds(7), DAY)
        v = OV.status_answer(_ds(8), DAY)
        assert "stale" not in v and v["gen"] == 8 and "demo-x" not in OV._status_last
        assert not OV._last_path().exists()


def test_a_failed_background_status_is_computed_in_the_request_next_time(ov, tmp_path):
    OV = ov
    with tenancy.use(_user(tmp_path, "u2")):
        OV.status_answer(_ds(1), DAY)
        FakeStatus.fail = True
        ds2 = _ds(2)
        assert OV.status_answer(ds2, DAY)["stale"]
        assert _wait(lambda: not OV.status_updating())
        with pytest.raises(RuntimeError):                    # the page reloads: this time it computes (and reports)
            OV.status_answer(ds2, DAY)


def test_the_status_endpoints(ov, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import build_app
    OV = ov
    monkeypatch.setattr(OV, "_last_path", lambda: tmp_path / "owner_status.json")
    cur = {"ds": _ds(1, who="owner")}
    monkeypatch.setattr(OV, "_dataset", lambda: cur["ds"])
    monkeypatch.setattr(OV.O, "day_to_date", lambda d: DAY)
    with TestClient(build_app(demo=False)) as c:
        r = c.get("/api/v1/overview/status")
        assert r.status_code == 200 and r.json()["gen"] == 1 and "stale" not in r.json()
        cur["ds"] = _ds(2, who="owner")                      # a sync / a restart: a new Dataset
        OV._status_cache.clear()
        FakeStatus.gate = threading.Event()
        r = c.get("/api/v1/overview/status")
        assert r.json()["gen"] == 1 and r.json()["stale"]["reason"] == "data"
        assert c.get("/api/v1/overview/status/fresh").json() == {"updating": True}
        FakeStatus.gate.set()
        assert _wait(lambda: not c.get("/api/v1/overview/status/fresh").json()["updating"])
        r = c.get("/api/v1/overview/status")
        assert r.json()["gen"] == 2 and "stale" not in r.json()


def test_the_overview_page_marks_a_stale_status_and_polls_for_the_new_one():
    static = Path(__file__).resolve().parents[1] / "static"
    page = (static / "overview.html").read_text("utf-8")
    assert 'id="stale-badge"' in page and "/status/fresh" in page and "s.stale" in page
    assert 'tO("stale.updating")' in page and "setTimeout(" in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((static / "i18n" / loc / "overview.json").read_text("utf-8"))
        for k in ("stale.updating", "stale.tip", "stale.reload", "stale.reload_tip", "stale.wait"):
            assert cat.get(k), (loc, k)
    assert json.loads((static / "i18n" / "zh-TW" / "overview.json").read_text("utf-8"))["stale.updating"] == "更新中"


def test_the_last_status_file_is_registered():
    from backend import data_registry as R
    assert R.classify("cache/overview_status.json").cls == R.DERIVED


# ---------------------------------------------------------------------------- the warm-up's steps

def test_the_warm_ups_low_priority_steps_are_queue_jobs_at_warm(monkeypatch):
    from backend.api import activity_auto as AA
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.api import racepower as RP
    from backend.api import wko5views
    from backend.engine import calibrate as CAL
    from backend.engine import plan_auto
    seen = []

    def where(name):
        j = jobs.current()
        seen.append((name, None if j is None else (j.cls, j.priority)))
    ds = SimpleNamespace(today=20000.0)
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    monkeypatch.setattr(wko5views, "_WARM", {})
    monkeypatch.setattr(wko5views, "_dataset", lambda: where("dataset") or ds)
    monkeypatch.setattr(wko5views, "_warm_chart_code", lambda d: None)
    monkeypatch.setattr(OV, "_status", lambda d, today: where("status"))
    monkeypatch.setattr(PSA, "_compute_inputs", lambda: where("inputs"))
    monkeypatch.setattr(plan_auto, "busy", lambda: False)
    monkeypatch.setattr(CAL, "_registry", lambda: {})
    monkeypatch.setattr(AA, "job_for", lambda d: where("classify"))
    monkeypatch.setattr(RP, "_grade_models", lambda: where("grade"))
    monkeypatch.setattr(RP, "_climb_cadence", lambda: where("cadence"))
    t = wko5views.warm_up("startup")
    t.join(10)
    low = wko5views._warm_slot().get("low")
    low and low.join(10)
    hot, warm = ("full", jobs.PRIORITIES["deploy_hot"]), ("full", jobs.PRIORITIES["warm"])
    assert seen == [("dataset", hot), ("status", hot), ("inputs", hot),
                    ("dataset", warm),                    # the never-fitted calibration's step
                    ("classify", None),                   # queues its own job (activity_auto); waits outside
                    ("grade", warm), ("cadence", warm)]    # the race charts (owner): one more step


# ---------------------------------------------------------------------------- after a deploy

@pytest.fixture
def server(tmp_path, monkeypatch):
    from backend import accounts
    from backend.accounts import store, tenants as TN
    root = tmp_path / "server"
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(root))
    monkeypatch.setenv(accounts.ENV_ENABLED, "1")
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    return root


def _people(now):
    """The owner and six users: a signed in 2 days ago; b signed in a month ago but its session was
    used yesterday; c synced 3 days ago; d signed in 8 days ago; e only failed to sync yesterday;
    f was disabled after signing in today."""
    from backend.accounts import store
    from backend.sync import events
    store.migrate_owner()
    u = {n: store.create_user(email=f"{n}@x.tw") for n in "abcdef"}
    month = (now - dt.timedelta(days=30)).isoformat()
    login = {"a": now - dt.timedelta(days=2), "b": now - dt.timedelta(days=30), "c": now - dt.timedelta(days=30),
             "d": now - dt.timedelta(days=8), "e": now - dt.timedelta(days=30), "f": now}
    with store.connect() as con:
        for n, t in login.items():
            con.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (t.isoformat(), u[n].id))
        con.execute("INSERT INTO sessions(token_hash, user_id, created_at, last_seen_at, expires_at) "
                    "VALUES ('h-b', ?, ?, ?, ?)", (u["b"].id, month, (now - dt.timedelta(days=1)).isoformat(),
                                                    (now + dt.timedelta(days=29)).isoformat()))
    store.set_disabled(u["f"].id, True, "test")
    events.log("coros", "sync_ok", tenant=f"u{u['c'].id}", trigger="schedule")
    events.log("coros", "sync_failed", tenant=f"u{u['e'].id}", trigger="schedule")
    lines = events.path().read_text("utf-8").splitlines()     # dated: c's 3 days ago, e's yesterday
    recs = [json.loads(x) for x in lines]
    recs[0]["at"] = (now - dt.timedelta(days=3)).timestamp()
    recs[1]["at"] = (now - dt.timedelta(days=1)).timestamp()
    events.path().write_text("".join(json.dumps(r) + "\n" for r in recs), "utf-8")
    return {n: f"u{x.id}" for n, x in u.items()}


def test_active_tenants_signed_in_or_synced_in_7_days_most_recent_first(server):
    from backend.accounts import tenants as TN
    ids = _people(dt.datetime.now(dt.timezone.utc))
    assert [t.id for t in TN.active_tenants()] == [ids["b"], ids["a"], ids["c"]]


def test_after_a_deploy_only_the_active_tenants_are_warmed_behind_the_owner(server, monkeypatch):
    import asyncio
    from backend.accounts import tenants as TN
    from backend.api import wko5views
    ids = _people(dt.datetime.now(dt.timezone.utc))
    monkeypatch.delenv("WKO5COACH_NO_WARMUP", raising=False)
    monkeypatch.setattr(wko5views, "_WARM", {})
    ready = []

    async def ensure_ready(t):
        ready.append(t.id)
    monkeypatch.setattr(TN, "ensure_ready", ensure_ready)
    release, built = threading.Event(), []

    def dataset():
        built.append(tenancy.current().id)
        assert release.wait(10)
        raise RuntimeError("no data here")                  # the warm-up only logs it
    monkeypatch.setattr(wko5views, "_dataset", dataset)
    try:
        queued = asyncio.run(wko5views.warm_up_startup())
        assert queued == ["owner", ids["b"], ids["a"], ids["c"]]
        assert ready == [ids["b"], ids["a"], ids["c"]]          # idle and disabled users: nothing made ahead
        assert _wait(lambda: built == ["owner"])
        snap = jobs.snapshot()
        assert snap["running"] == [("full", "dataset warm-up (startup)", "owner")]
        assert snap["queued"] == [("full", "dataset warm-up (deploy)", t) for t in (ids["b"], ids["a"], ids["c"])]
    finally:
        release.set()
    assert _wait(lambda: len(built) == 4 and not jobs.snapshot()["running"])
    assert built == ["owner", ids["b"], ids["a"], ids["c"]]       # one at a time, in that order


def test_single_user_mode_and_the_demo_warm_the_owner_only(server, monkeypatch):
    import asyncio
    from backend import accounts
    from backend.api import wko5views
    monkeypatch.setattr(wko5views, "warm_up", lambda reason="startup": None)
    _people(dt.datetime.now(dt.timezone.utc))
    assert asyncio.run(wko5views.warm_up_startup(demo=True)) == ["owner"]
    monkeypatch.delenv(accounts.ENV_ENABLED, raising=False)
    assert asyncio.run(wko5views.warm_up_startup()) == ["owner"]
