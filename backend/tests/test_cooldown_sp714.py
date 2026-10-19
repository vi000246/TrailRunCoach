"""SP-714: the manual recompute buttons cool down per person (the free tier longer than a
subscriber, the owner and admins never), the waits are the admin page's per plan, calibrate's
manual run goes through the job queue, and every signed-in person has a request limit."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from backend import cooldown as CD, site_config as SC, tenancy
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    OWNER_EMAIL, _client, app, browser, c, csrf, env, sign_in, sign_up, started,
)

ENTRY_KEYS = [f"cooldown.{e}" for e in CD.ENTRIES]


@pytest.fixture
def server(tmp_path, monkeypatch):
    from backend import accounts
    from backend.accounts import store, tenants as TN
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    store.migrate_owner()
    now = [1_800_000_000.0]
    monkeypatch.setattr(CD, "clock", lambda: now[0])
    monkeypatch.setattr(store, "now", now, raising=False)       # the test's clock, moved by hand
    return store


def _user(store, plan="free", admin=False):
    from backend.accounts import tenants as TN
    u = store.create_user(email=f"{plan}{store.count_users()}@x.tw")
    if plan != "free":
        store.set_plan(u.id, plan)
    if admin:
        store.set_admin(u.id, True)
    return TN.tenant_of(store.get_user(u.id))


# ---------------------------------------------------------------------------- the waits (site_config)

def test_every_entry_has_a_wait_and_the_free_tier_waits_longer():
    for k in ENTRY_KEYS:
        it = SC.ITEMS[k]
        assert it.group == "cooldown" and it.plan and it.kind == "int"
        free = SC.effective(k, plan="free")
        paid = SC.effective(k, plan="subscriber")
        assert free > paid > 0, k
        assert SC.effective(k, plan="subscriber", owner=True) == free      # the owner: no plan layer (never waits anyway)


def test_the_admin_page_sets_each_plans_wait(server):
    k = "cooldown.calib"
    SC.set_values({}, actor=1, plans={"free": {k: 600}, "subscriber": {k: 30}})
    assert SC.effective(k, plan="free") == 600 and SC.effective(k, plan="subscriber") == 30
    SC.set_values({}, actor=1, plans={"subscriber": {k: None}})            # back to the program's default
    assert SC.effective(k, plan="subscriber") == SC.ITEMS[k].plan_defaults["subscriber"]
    v = SC.view()
    row = next(r for r in v["items"] if r["key"] == k)
    assert row["plan_defaults"] == {"subscriber": 60} and row["group"] == "cooldown"
    assert v["plans"]["free"][k] == 600
    with pytest.raises(SC.ConfigError):
        SC.set_values({}, actor=1, plans={"free": {k: -1}})
    assert SC.ITEMS["ratelimit.user_per_min"].group == "ratelimit"


# ---------------------------------------------------------------------------- the cool-down itself

def test_free_waits_longer_than_subscriber_and_owner_admin_never(server):
    free, paid, adm = _user(server), _user(server, "subscriber"), _user(server, admin=True)
    with tenancy.use(free):
        CD.take("calib")
        with pytest.raises(HTTPException) as ei:
            CD.take("calib")
        d = ei.value.detail
        assert ei.value.status_code == 429 and d["code"] == "COOLDOWN" and d["key"] == "calib"
        assert d["retry_after"] == 1440 * 60 and ei.value.headers["Retry-After"] == str(1440 * 60)
        assert "小時後可以再按" in d["message"]
        assert CD.active() == {"calib": 1440 * 60}
        CD.take("routes")                                               # another button: its own wait
    with tenancy.use(paid):
        CD.take("calib")
        with pytest.raises(HTTPException) as ei:
            CD.take("calib")
        assert ei.value.detail["retry_after"] == 60 * 60
    for t in (tenancy.owner(), adm):
        with tenancy.use(t):
            for _ in range(3):
                CD.take("calib")                                        # never waits
            assert CD.active() == {}
    server.now[0] += 60 * 60 + 1
    with tenancy.use(paid):
        CD.take("calib")                                                # an hour later: again
    with tenancy.use(free):
        with pytest.raises(HTTPException) as ei:
            CD.check("calib")
        assert ei.value.detail["retry_after"] == 1440 * 60 - 3601
        server.now[0] += 1440 * 60
        CD.check("calib")


def test_sub_keys_count_on_their_own_and_ready_never_raises(server):
    free = _user(server)
    with tenancy.use(free):
        CD.take("sync:coros")
        CD.take("sync:tp")
        with pytest.raises(HTTPException):
            CD.take("sync:coros")
        assert not CD.ready("sync:coros") and CD.ready("sync:intervals")
        assert set(CD.active()) == {"sync:coros", "sync:tp"}
        with pytest.raises(ValueError):
            CD.take("nope")


def test_message_in_minutes_and_hours():
    assert CD.message(30) == "1 分鐘後可以再按"
    assert CD.message(25 * 60) == "25 分鐘後可以再按"
    assert CD.message(5 * 3600) == "約 5 小時後可以再按"


# ---------------------------------------------------------------------------- the endpoints

def test_calibrate_is_queued_never_computed_in_the_request(server, monkeypatch):
    from backend import jobs
    from backend.api import calib as API
    from backend.engine import calibrate as CAL

    async def never(*a, **k):
        raise AssertionError("computed in the request")
    monkeypatch.setattr(CAL, "calibrate", never)
    got = []

    class FakeJob:
        state = "queued"
        error = None
        result = None

    def submit(fn, **kw):
        got.append(kw)
        return FakeJob()
    monkeypatch.setattr(jobs, "submit", submit)
    monkeypatch.setattr(jobs, "find", lambda key: None)
    monkeypatch.setattr(jobs, "position", lambda job: 1)
    free = _user(server)
    with tenancy.use(free):
        r = asyncio.run(API.run_now())
        assert r["status"] == "queued" and r["job"]["state"] == "queued"
        assert got == [{"cls": "full", "priority": "user", "label": "calibration (manual)", "key": ("calib", free.id)}]
        with pytest.raises(HTTPException) as ei:                    # done, pressed again: the cool-down
            asyncio.run(API.run_now())
        assert ei.value.detail["code"] == "COOLDOWN"
        # still queued / running: the same job answers, no 429
        monkeypatch.setattr(jobs, "find", lambda key: FakeJob())
        assert asyncio.run(API.run_now())["status"] == "queued" and len(got) == 1


def test_calibrate_job_fits_in_the_jobs_thread(server, monkeypatch):
    """The job body reads the Dataset and runs the fit in its own event loop (jobs inline)."""
    from backend import jobs
    from backend.api import calib as API, wko5views
    from backend.engine import calibrate as CAL
    seen = {}

    async def fake(db, athlete_id=1, ds=None, today=None):
        seen["ds"], seen["job"] = ds, jobs.current()
        return {"written": ["a"], "skipped": {}}
    monkeypatch.setattr(CAL, "calibrate", fake)
    monkeypatch.setattr(wko5views, "_dataset", lambda parity=None, source=None: "DS")
    monkeypatch.setattr(jobs, "_INLINE", True)
    jobs.reset()
    try:
        with tenancy.use(tenancy.owner()):
            job = jobs.submit(API._fit_job(1), cls="full", priority="user", key=("calib-test",))
        assert job.state == "done" and job.result["written"] == ["a"]
        assert seen["ds"] == "DS" and seen["job"] is job
        assert API._job_view(job) == {"state": "done", "position": 0, "written": ["a"]}
    finally:
        jobs.reset()


def test_routes_rebuild_counts_only_a_started_build(server, monkeypatch):
    from backend.api import routes as API
    starts = iter([False, True])
    monkeypatch.setattr(API.BUILDER, "start", lambda *a, **k: next(starts))
    monkeypatch.setattr(API, "_status", lambda: {})
    with tenancy.use(_user(server)):
        assert API.rebuild()["started"] is False                    # busy: not counted
        assert API.rebuild()["started"] is True
        with pytest.raises(HTTPException) as ei:
            API.rebuild()
        assert ei.value.detail["key"] == "routes"


def test_manual_sync_cools_down_per_source(server, monkeypatch):
    from backend.api import sync as API

    async def enabled(*a):
        return False
    monkeypatch.setattr(API.runner, "is_busy", lambda s: False)
    monkeypatch.setattr(API, "_disabled", enabled)
    with tenancy.use(_user(server)):
        asyncio.run(API._sse_sync(None, "coros", 1, None))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(API._sse_sync(None, "coros", 1, None))
        assert ei.value.detail["key"] == "sync:coros" and ei.value.detail["retry_after"] == 30 * 60
        asyncio.run(API._sse_sync(None, "intervals", 1, None))      # another source


def test_check_and_fill_dedup_plan_auto_cool_down(server, monkeypatch):
    from backend.api import plan_auto as PAPI, sync as API
    from backend.sync import check

    async def ready(db, aid):
        return {s: "ready" for s in API.runner.SOURCES}
    monkeypatch.setattr(API.runner, "ready_sources", ready)
    monkeypatch.setattr(check, "start", lambda *a, **k: object())

    class Repo:
        def __init__(self, *a):
            pass

        async def get(self, k):
            return {"missing": [1]}
    monkeypatch.setattr(API, "SettingsRepository", Repo)

    async def rebuild(db, aid):
        return {"ok": True}

    class DB:
        async def commit(self):
            pass
    monkeypatch.setattr(API.dedup, "rebuild", rebuild)

    async def pa_settings(db):
        return {"state": {}}

    async def pa_noop(*a, **k):
        return {}
    monkeypatch.setattr(PAPI.PA, "settings", pa_settings)
    monkeypatch.setattr(PAPI.PA, "_set_state", pa_noop)
    monkeypatch.setattr(PAPI.PA, "run", pa_noop)
    with tenancy.use(_user(server)):
        assert asyncio.run(API.check_start(None, 1, None))["started"]
        with pytest.raises(HTTPException):
            asyncio.run(API.check_start(None, 1, None))
        assert asyncio.run(API.check_fill("coros", None, 1, None))["status"] == "started"
        with pytest.raises(HTTPException) as ei:
            asyncio.run(API.check_fill("coros", None, 1, None))
        assert ei.value.detail["key"] == "check:fill-coros"
        asyncio.run(API.rebuild_dedup(1, DB()))
        with pytest.raises(HTTPException):
            asyncio.run(API.rebuild_dedup(1, DB()))
        asyncio.run(PAPI.run_now(None))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(PAPI.run_now(None))
        assert ei.value.detail["key"] == "plan_auto"


def test_racepower_refresh_falls_back_to_the_cache_and_surface_refresh_cools_down(server, monkeypatch):
    from backend.api import racepower as API
    asked = []
    monkeypatch.setattr(API, "inputs", lambda refresh=False: asked.append(refresh) or {"ok": 1})
    with tenancy.use(_user(server)):
        assert API.get_inputs(refresh=True) == {"ok": 1}
        assert API.get_inputs(refresh=True) == {"ok": 1}            # cooling down: the cache, no error
        assert asked == [True, False]
        from backend.engine import course_surface as CS
        from backend.engine.racepower import surface as SF
        monkeypatch.setattr(API, "_surface_id", lambda cid: cid)
        monkeypatch.setattr(CS, "get", lambda cid: {"profile": {"x": 1}})
        monkeypatch.setattr(API, "_surface_track", lambda cid, ev: None)
        monkeypatch.setattr(SF, "view", lambda cid, row, err=None: {"cid": cid})
        body = API.SurfaceIn(refresh=True)
        API.course_surface("c1", body)
        with pytest.raises(HTTPException) as ei:
            API.course_surface("c1", body)
        assert ei.value.detail["key"] == "surface"
        API.course_surface("c1", API.SurfaceIn())                   # no refresh: the stored one, always


# ---------------------------------------------------------------------------- sign-in mode, end to end

def test_signed_in_cool_down_and_session_list(c):
    sign_up(c, "g-alice", "alice@example.com")
    h = {"X-TRC-CSRF": csrf(c)}
    assert c.post("/api/v1/sync/dedup/rebuild", headers=h).status_code == 200
    r = c.post("/api/v1/sync/dedup/rebuild", headers=h)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "COOLDOWN"
    assert r.json()["detail"]["retry_after"] > 0 and r.headers["retry-after"]
    assert c.get("/api/v1/session").json()["membership"]["cooldowns"]["dedup"] > 0


def test_signed_in_request_limit(app, c):
    from backend import tenancy_mw as MW
    SC.set_values({"ratelimit.user_per_min": 30}, actor=1)
    MW.reset_user_limits()
    alice = browser(app)
    sign_up(alice, "g-alice", "alice@example.com")
    for _ in range(40):
        assert alice.get("/api/v1/static/shell.js").status_code == 200       # static files: not counted
    codes = [alice.get("/api/v1/plan").status_code for _ in range(40)]
    assert 429 in codes and codes.count(429) >= 5
    r = alice.get("/api/v1/plan")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "RATE_LIMITED"
    # the owner (an admin) is never limited
    r = sign_in(c, "g-owner", OWNER_EMAIL)
    assert r.status_code in (302, 303)
    assert all(c.get("/api/v1/plan").status_code == 200 for _ in range(40))


def test_user_limited_rules(server):
    from backend import tenancy_mw as MW

    class U:
        def __init__(self, uid, admin=False):
            self.id, self.is_admin = uid, admin
    MW.reset_user_limits()
    assert MW.user_limited(None, "GET", "/api/v1/plan") is None
    assert all(MW.user_limited(U(1, True), "GET", "/api/v1/plan") is None for _ in range(400))
    heavy = [MW.user_limited(U(7), "POST", "/api/v1/racepower/predict") for _ in range(25)]
    assert heavy[:20] == [None] * 20 and heavy[20] is not None
    assert MW.user_limited(U(7), "GET", "/api/v1/racepower/predict") is None   # a read is not heavy
    MW.reset_user_limits()
