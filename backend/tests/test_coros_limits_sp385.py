"""SP-385: COROS with many users — a server-wide rate limit and sync places, no retry when COROS
pushes back (429 / a blocked login), a server-wide switch, a breaker, the disclaimer before
connecting, and an event log without anything personal. COROS is a mock transport."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from backend import tenancy


@pytest.fixture(autouse=True)
def site(tmp_path, monkeypatch):
    from backend import site_settings as SS
    from backend.sync import coros_limits as CL, events as EV
    monkeypatch.setattr(SS, "PATH", tmp_path / "site.json")
    monkeypatch.setattr(EV, "PATH", tmp_path / "sync_events.jsonl")
    SS.reset_cache()
    CL.reset()
    yield SS
    CL.reset()
    SS.reset_cache()


def _tenant(tmp_path, i):
    d = tmp_path / f"u{i}"
    return tenancy.Tenant(id=f"u{i}", kind=tenancy.USER, root=d, shared=d)


def _coros(handler):
    from backend.sync import http
    return http.use_transport(httpx.MockTransport(handler))


# ---------------------------------------------------------------------------- 1. rate limit, places

def test_ten_tenants_at_once_stay_under_the_server_rate(tmp_path, site):
    from backend.sync import http
    site.update({"coros.limits": "on", "coros.rate_per_s": 10, "coros.rate_per_min": 6000})
    stamps = []

    def handler(req):
        stamps.append(time.monotonic())
        return httpx.Response(200, json={"result": "0000"})

    async def one(i):
        with tenancy.use(_tenant(tmp_path, i)):
            async with http.client() as c:
                for _ in range(3):
                    await c.get("https://teamapi.coros.com/activity/query")

    async def go():
        t0 = time.monotonic()
        await asyncio.gather(*(one(i) for i in range(10)))
        return t0
    with _coros(handler):
        t0 = asyncio.run(go())
    assert len(stamps) == 30
    # burst 10 at once, then 10 / s: 30 requests need ≥ 2 s; no 1-s window holds more than 10 + 10
    assert stamps[-1] - t0 >= 1.8
    for s0 in stamps:
        assert sum(1 for s in stamps if s0 <= s < s0 + 1.0) <= 20


def test_other_hosts_are_not_limited(tmp_path, site):
    from backend.sync import http
    site.update({"coros.limits": "on", "coros.rate_per_s": 1, "coros.rate_per_min": 1})
    n = []

    async def go():
        async with http.client() as c:
            for _ in range(5):
                await c.get("https://example.com/x")
    with _coros(lambda req: n.append(1) or httpx.Response(200)):
        t0 = time.monotonic()
        asyncio.run(go())
    assert len(n) == 5 and time.monotonic() - t0 < 1.0


def test_at_most_max_concurrent_accounts_sync_at_once(tmp_path, site):
    from backend.sync import coros_limits as CL
    site.update({"coros.limits": "on", "coros.max_concurrent": 2})
    peak = []

    async def sync(i):
        with tenancy.use(_tenant(tmp_path, i)):
            async with CL.sync_slot():
                peak.append(CL.active_syncs())
                await asyncio.sleep(0.02)

    async def go():
        await asyncio.gather(*(sync(i) for i in range(10)))
    asyncio.run(go())
    assert len(peak) == 10 and max(peak) <= 2


def test_the_owner_alone_is_not_slowed(site):
    from backend.sync import coros_limits as CL
    assert CL.limits_on() is False                    # single-user mode, nothing set: old speed


# ---------------------------------------------------------------------------- 2. no retry when blocked

def test_a_429_at_login_tries_no_other_region_and_pauses_the_account(tmp_path, site):
    from backend.sync import coros_client
    from backend.sync import coros_limits as CL
    calls = []

    def handler(req):
        calls.append(req.url.host)
        return httpx.Response(429)

    async def go():
        with tenancy.use(_tenant(tmp_path, 2)):
            with pytest.raises(CL.CorosBlocked) as ei:
                await coros_client._login("a@x.tw", "pw", None, 1)
            return ei.value.code
    with _coros(handler):
        code = asyncio.run(go())
    assert code == "COROS_RATE_LIMITED" and len(calls) == 1          # not the next region
    assert CL.paused("u2")["reason"].startswith("HTTP 429")
    with tenancy.use(_tenant(tmp_path, 2)):
        with pytest.raises(CL.CorosBlocked) as ei:
            CL.check_allowed()
        assert ei.value.code == "COROS_PAUSED"
        assert CL.status()["state"] == "paused"
    with tenancy.use(_tenant(tmp_path, 3)):
        CL.check_allowed()                             # another account is not paused


def test_a_paused_account_waits_for_its_next_scheduled_sync(tmp_path, site):
    from backend.sync import coros_limits as CL
    t = _tenant(tmp_path, 2)
    with tenancy.use(t):
        CL.pause("HTTP 429")

        async def manual():
            async with CL.sync_slot(scheduled=False):
                pass

        async def scheduled():
            async with CL.sync_slot(scheduled=True):
                return CL.paused()
        with pytest.raises(CL.CorosBlocked):
            asyncio.run(manual())
        assert asyncio.run(scheduled()) is None        # the schedule clears the pause and tries again


def test_the_runner_reports_a_paused_account_in_plain_codes(tmp_path, site):
    from backend.sync import coros_limits as CL, runner
    from backend.tests.test_sync_e2e import make_session
    t = _tenant(tmp_path, 2)

    async def go():
        with tenancy.use(t):
            CL.pause("HTTP 429")
            s = await make_session(tmp_path)
            return [e async for e in runner.stream(s, "coros", 1, trigger="manual")]
    evs = asyncio.run(go())
    assert evs == [{"status": "error", "error": "COROS_PAUSED", "source": "coros", "detail": evs[0]["detail"]}]


def test_many_blocked_answers_pause_the_whole_server(tmp_path, site):
    from backend.sync import coros_limits as CL
    with tenancy.use(_tenant(tmp_path, 2)):
        for i in range(CL.ERROR_MIN_CALLS):
            try:
                CL.after_response(429 if i % 3 == 0 else 200)
            except CL.CorosBlocked:
                pass
    with tenancy.use(_tenant(tmp_path, 9)):           # a fresh account is held too
        with pytest.raises(CL.CorosBlocked) as ei:
            CL.check_allowed()
        assert ei.value.code == "COROS_COOLDOWN"
        assert CL.status()["state"] == "cooldown"


def test_a_403_outside_login_is_an_expired_token_not_a_block(tmp_path, site):
    from backend.sync import coros_limits as CL
    with tenancy.use(_tenant(tmp_path, 2)):
        CL.after_response(403)                         # coros_client's re-login handles it
        assert CL.paused() is None


# ---------------------------------------------------------------------------- 3. switch, 4. breaker

def test_the_server_switch_off_skips_coros_and_keeps_the_data(tmp_path, site):
    from backend.db.models import SyncState
    from backend.settings.repository import SettingsRepository
    from backend.sync import coros_limits as CL, runner
    from backend.tests.test_sync_e2e import make_session
    CL.set_enabled(False, by="owner")

    async def go():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1, coros_access_token="t"))
        await SettingsRepository(s, 1).set("sync.coros.enabled", True)
        await s.commit()
        ready = await runner.ready_sources(s, 1)
        evs = [e async for e in runner.stream(s, "coros", 1, trigger="schedule")]
        return ready, evs
    ready, evs = asyncio.run(go())
    assert ready["coros"] == "site_paused"
    assert evs[0]["error"] == "COROS_DISABLED"
    assert CL.status()["state"] == "disabled"
    CL.set_enabled(True)
    assert CL.status()["state"] == "ok"


def test_three_accounts_refused_at_login_in_a_day_trip_the_breaker(tmp_path, site):
    from backend.sync import coros_limits as CL
    for i in (2, 3, 2):
        assert CL.login_blocked(f"u{i}") is False                 # two different accounts so far
    assert CL.enabled()
    assert CL.login_blocked("u4") is True
    assert not CL.enabled() and CL.status()["tripped"]["accounts"] == 3
    CL.reset()                                          # a restart does not turn it back on
    assert not CL.enabled()
    CL.set_enabled(True, by="owner")                    # only the owner does
    assert CL.enabled() and CL.status()["tripped"] is None


def test_old_refusals_fall_out_of_the_breakers_window(tmp_path, site, monkeypatch):
    from backend.sync import coros_limits as CL
    now = [1_000_000.0]
    monkeypatch.setattr(CL, "wall", lambda: now[0])
    CL.login_blocked("u2")
    CL.login_blocked("u3")
    now[0] += CL.BREAKER_WINDOW_S + 1
    assert CL.login_blocked("u4") is False and CL.enabled()


# ---------------------------------------------------------------------------- 5. disclaimer, API

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    from fastapi.testclient import TestClient
    from backend.main import build_app
    app = build_app(demo=False)
    user = _tenant(tmp_path, 2)

    async def bind(scope, receive, send):
        name = dict(scope.get("headers") or []).get(b"x-test-tenant", b"").decode()
        if scope["type"] == "http" and name == "user":
            from backend.accounts import tenants as TN
            await TN.ensure_ready(user)
            tok = tenancy.set_current(user)
            try:
                return await app(scope, receive, send)
            finally:
                tenancy.reset(tok)
        return await app(scope, receive, send)
    with TestClient(bind, raise_server_exceptions=False) as c:
        yield c


def test_no_coros_connection_without_the_disclaimer(client):
    from backend.sync import coros_limits as CL
    U = {"X-Test-Tenant": "user"}
    d = client.get("/api/v1/auth/coros/disclaimer", headers=U).json()
    assert d["version"] == CL.DISCLAIMER_VERSION and "非公開介面" in d["text"] and d["consented"] is False
    r = client.post("/api/v1/auth/coros/login", json={"email": "a@x.tw", "password": "pw"}, headers=U)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "COROS_CONSENT_REQUIRED"
    # ticked: kept with its version and time (the login itself then fails: no network in tests)
    r = client.post("/api/v1/auth/coros/login", headers=U,
                    json={"email": "a@x.tw", "password": "pw", "consent_version": CL.DISCLAIMER_VERSION})
    assert r.status_code != 400
    d = client.get("/api/v1/auth/coros/disclaimer", headers=U).json()
    assert d["consented"] is True and d["consented_at"]


def test_the_owners_existing_connection_needs_no_new_tick(client):
    import asyncio as _a
    from backend.db import database
    from backend.db.models import SyncState

    async def connect():
        async with database.AsyncSessionLocal() as db:
            db.add(SyncState(athlete_id=1, coros_email="owner@x.tw", coros_access_token="t"))
            await db.commit()
    _a.run(connect())
    r = client.post("/api/v1/auth/coros/login", json={"email": "owner@x.tw", "password": "pw"})
    assert r.status_code != 400


def test_only_the_owner_flips_the_switch(client):
    U = {"X-Test-Tenant": "user"}
    assert client.put("/api/v1/auth/coros/site", json={"enabled": False}, headers=U).status_code == 403
    r = client.put("/api/v1/auth/coros/site", json={"enabled": False})
    assert r.status_code == 200 and r.json()["state"] == "disabled"
    st = client.get("/api/v1/auth/coros/site", headers=U).json()
    assert st["state"] == "disabled" and "events" not in st              # the log is the owner's
    r = client.post("/api/v1/auth/coros/login", headers=U, json={"email": "a@x.tw", "password": "pw",
                                                                  "consent_version": st["disclaimer_version"]})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "COROS_DISABLED"
    assert client.put("/api/v1/auth/coros/site", json={"enabled": True}).json()["state"] == "ok"
    assert any(e["event"] == "disabled" for e in client.get("/api/v1/auth/coros/site").json()["events"])


# ---------------------------------------------------------------------------- 6. event log

def test_the_event_log_has_results_and_blocks_but_nothing_personal(tmp_path, site):
    from backend.sync import coros_client, coros_limits as CL, events as EV

    def handler(req):
        if req.url.path.endswith("/account/login"):
            return httpx.Response(429)
        return httpx.Response(200)

    async def go():
        with tenancy.use(_tenant(tmp_path, 5)):
            with pytest.raises(CL.CorosBlocked):
                await coros_client._login("secret@x.tw", "hunter2", None, 1)
    with _coros(handler):
        asyncio.run(go())
    EV.log("coros", "login_ok", tenant="u6", email="leak@x.tw", token="abc")
    raw = EV.PATH.read_text("utf-8")
    rows = [json.loads(x) for x in raw.splitlines()]
    assert {"http_429", "account_paused", "login_ok"} <= {r["event"] for r in rows}
    assert all(r.get("tenant", "u5") in ("u5", "u6") for r in rows)
    for bad in ("secret@x.tw", "hunter2", "leak@x.tw", "abc"):
        assert bad not in raw
