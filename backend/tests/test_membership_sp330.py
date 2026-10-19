"""SP-330: plans, roles and scopes — one table decides; the API checks scopes (never plan names),
the free tier's quotas count and reset, expiry downgrades without deleting data, data sources can
be switched off for the server, cross-project calls need their scope."""
from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend import membership as MS, tenancy

UTC = dt.timezone.utc


@pytest.fixture
def server(tmp_path, monkeypatch):
    from backend import accounts
    from backend.accounts import store, tenants as TN
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    store.migrate_owner()
    return store


def _user(store, plan="free", expires=None):
    from backend.accounts import tenants as TN
    u = store.create_user(email=f"{plan}{store.count_users()}@x.tw")
    if plan != "free":
        store.set_plan(u.id, plan, expires)
    return TN.tenant_of(store.get_user(u.id))


# ---------------------------------------------------------------------------- the tables

def test_every_scope_is_in_the_doc_and_every_role_uses_known_scopes():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "plans" / "membership.md").read_text("utf-8")
    for s in MS.SCOPES:
        assert f"`{s}`" in doc, s
    for role, sc in MS.ROLE_SCOPES.items():
        assert sc <= set(MS.SCOPES), role
    assert MS.ROLE_SCOPES[MS.USER] < MS.ROLE_SCOPES[MS.SUBSCRIBER] < MS.ROLE_SCOPES[MS.OWNER]
    assert "sync:tp_web" not in MS.ROLE_SCOPES[MS.SUBSCRIBER]          # owner 2026-10-12
    assert not ({"coach:read_students", "group:calc_participants"} & set().union(
        *(MS.ROLE_SCOPES[r] for r in MS.ACTIVE_ROLES)))                 # second wave: nobody yet


def test_roles_from_the_account(server):
    free, paid = _user(server), _user(server, "subscriber")
    assert MS.member(tenancy.owner()).role == MS.OWNER and MS.has("sync:tp_web", tenancy.owner())
    assert MS.member(free).role == MS.USER and not MS.has("charts:365d", free)
    assert MS.member(paid).role == MS.SUBSCRIBER and MS.has("calc:backtest", paid)
    for t in (free, paid):
        assert MS.has("safety", t) and MS.has("plan:edit", t)                # never paywalled
        assert not MS.has("wko5", t) and not MS.has("sync:tp_web", t)


def test_expiry_downgrades_without_touching_data(server):
    future = (dt.datetime.now(UTC) + dt.timedelta(days=30)).isoformat()
    t = _user(server, "subscriber", future)
    assert MS.member(t).role == MS.SUBSCRIBER
    server.set_plan(int(t.id[1:]), "subscriber", (dt.datetime.now(UTC) - dt.timedelta(days=1)).isoformat())
    assert MS.member(t).role == MS.USER                                 # expired: the free tier
    assert not MS.has("charts:full", t) and MS.has("plan:view", t)
    # the data stays: the user's folder and files are untouched by the downgrade
    t.root.mkdir(parents=True, exist_ok=True)
    (t.root / "plan.json").write_text("{}", "utf-8")
    server.set_plan(int(t.id[1:]), "free")
    assert (t.root / "plan.json").exists()


# ---------------------------------------------------------------------------- API checks

def test_require_scope_says_why_and_offers_the_upgrade(server):
    free = _user(server)
    dep = MS.require_scope("calc:backtest")
    with tenancy.use(free):
        with pytest.raises(HTTPException) as ei:
            dep()
    d = ei.value.detail
    assert ei.value.status_code == 403 and d["code"] == "SCOPE_REQUIRED" and d["upgrade"] is True
    with tenancy.use(tenancy.owner()):
        dep()
    with pytest.raises(ValueError):
        MS.require_scope("no:such")


def test_chart_ranges_need_their_scope(server):
    assert MS.chart_scope(30) == "charts:90d" and MS.chart_scope(200) == "charts:365d"
    assert MS.chart_scope(900) == "charts:full"
    free = _user(server)
    with tenancy.use(free):
        MS.require(MS.chart_scope(60))
        with pytest.raises(HTTPException):
            MS.require(MS.chart_scope(200))


def test_free_calculator_quota_counts_and_resets_monthly(server):
    free = _user(server)
    oct_ = dt.datetime(2026, 10, 12, 4, tzinfo=UTC)
    with tenancy.use(free):
        for i in range(10):
            assert MS.consume("calc", now=oct_)["used"] == i + 1
        with pytest.raises(HTTPException) as ei:
            MS.consume("calc", now=oct_)
        assert ei.value.status_code == 429 and ei.value.detail["code"] == "QUOTA_EXCEEDED"
        assert MS.usage("calc", now=oct_)["used"] == 10                 # the refused one is not counted
        assert MS.consume("calc", now=dt.datetime(2026, 11, 1, 1, tzinfo=UTC))["used"] == 1   # a new month
    paid = _user(server, "subscriber")
    with tenancy.use(paid):
        for _ in range(30):
            MS.consume("calc", now=oct_)                               # unlimited
        assert MS.consume("backtest", now=oct_)["used"] == 1
        MS.consume("backtest", now=oct_)
        with pytest.raises(HTTPException):
            MS.consume("backtest", now=oct_)                           # 2 a month
    with tenancy.use(tenancy.owner()):
        for _ in range(30):
            MS.consume("backtest", now=oct_)                           # the owner: nothing counted


def test_free_charts_count_dashboards_once_a_day(server):
    free = _user(server)
    day = dt.datetime(2026, 10, 12, 2, tzinfo=UTC)
    with tenancy.use(free):
        for _ in range(3):
            MS.consume("charts", item="overview/0", now=day)          # the same dashboard: once
        for d in range(1, 5):
            MS.consume("charts", item=f"overview/{d}", now=day)
        assert MS.usage("charts", now=day)["used"] == 5
        MS.consume("charts", item="overview/0", now=day)              # seen today: still fine
        with pytest.raises(HTTPException):
            MS.consume("charts", item="overview/9", now=day)
        # Taiwan midnight: a new day
        assert MS.consume("charts", item="overview/9", now=dt.datetime(2026, 10, 12, 16, 1, tzinfo=UTC))["used"] == 1


# ---------------------------------------------------------------------------- data sources

def test_a_data_source_switched_off_for_the_server(server, tmp_path):
    from backend.db.models import SyncState
    from backend.settings.repository import SettingsRepository
    from backend.sync import runner
    from backend.tests.test_sync_e2e import make_session
    free = _user(server)
    assert MS.has("sync:intervals", free)
    MS.set_source("sync:intervals", False)
    assert not MS.has("sync:intervals", free) and "sync:intervals" in MS.sources_off()
    MS.set_source("sync:intervals", True)
    assert MS.has("sync:intervals", free)
    MS.set_source("sync:coros_credential", False)                    # also the SP-385 switch

    async def go():
        with tenancy.use(free):
            s = await make_session(tmp_path)
            s.add(SyncState(athlete_id=1, coros_access_token="t", tp_access_token="t"))
            rp = SettingsRepository(s, 1)
            await rp.set("sync.coros.enabled", True)
            await rp.set("sync.trainingpeaks.enabled", True)
            await s.commit()
            return await runner.ready_sources(s, 1)
    ready = asyncio.run(go())
    assert ready == {"coros": "site_paused", "garmin": "not_logged_in",       # Garmin: SP-91
                     "tp": "not_allowed",     # TP: the owner's only
                     "intervals": "not_logged_in"}                             # intervals.icu: SP-346
    MS.set_source("sync:coros_credential", True)
    from backend.sync import coros_limits as CL
    assert CL.enabled()


# ---------------------------------------------------------------------------- realtime vs nightly

def test_the_free_tier_replans_after_the_nightly_sync_only(server, monkeypatch):
    from backend.engine import plan_auto
    free, paid = _user(server), _user(server, "subscriber")

    async def fake(trigger):
        return None
    monkeypatch.setattr(plan_auto, "run_safe", fake)

    async def go(t, trig):
        with tenancy.use(t):
            task = plan_auto._after_sync("coros", {"status": "ok", "downloaded": 1, "trigger": trig})
            if task is not None:
                await task
            return task is not None
    assert asyncio.run(go(free, "open")) is False
    assert asyncio.run(go(free, "schedule")) is True
    assert asyncio.run(go(paid, "open")) is True


# ---------------------------------------------------------------------------- cross-project

def test_cross_project_calls_need_their_link_scope(server):
    u = server.get_user(int(_user(server, "subscriber").id[1:]))
    tok = server.create_service_token(u.id, "inreachlivetrack", ["link:calc", "link:gpx_sync"])
    assert MS.check_service_token(tok, "link:calc").id == u.id
    with pytest.raises(HTTPException) as ei:
        MS.check_service_token(tok, "link:participant_ability")
    assert ei.value.status_code == 403 and ei.value.detail["code"] == "SCOPE_REQUIRED"
    for bad in (None, "trc_nope"):
        with pytest.raises(HTTPException) as ei:
            MS.check_service_token(bad, "link:calc")
        assert ei.value.status_code == 401
    server.revoke_service_token(tok)
    with pytest.raises(HTTPException):
        MS.check_service_token(tok, "link:calc")
    old = server.create_service_token(u.id, "inreachlivetrack", ["link:calc"], days=1)
    later = dt.datetime.now(UTC) + dt.timedelta(days=2)
    assert server.service_token(old, now=later) is None                 # expired


# ---------------------------------------------------------------------------- the pages

def test_the_session_tells_the_page_what_it_may_show(server):
    free = _user(server)
    with tenancy.use(free):
        MS.consume("calc")
        v = MS.session_view()
    assert v["role"] == "user" and "calc:use" in v["scopes"] and "charts:365d" not in v["scopes"]
    assert v["quotas"]["calc"]["used"] == 1 and v["quotas"]["calc"]["limit"] == 10
    o = MS.session_view(tenancy.owner())
    assert o["role"] == "owner" and o["quotas"] == {} and "admin" in o["scopes"]


def test_the_owners_single_user_app_is_unchanged():
    """No accounts.db, no multi-user mode: the owner has every scope and nothing is counted."""
    assert MS.member().role == MS.OWNER
    for s in ("charts:full", "calc:backtest", "sync:tp_web", "plan:auto_realtime"):
        assert MS.has(s)
    assert MS.consume("calc") == {"used": 0, "limit": None}


# ---------------------------------------------------------------------------- through the API

@pytest.fixture
def client(server, monkeypatch):
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.accounts import tenants as TN
    app = build_app(demo=False)
    who = {"free": _user(server), "paid": _user(server, "subscriber")}

    async def bind(scope, receive, send):
        name = dict(scope.get("headers") or []).get(b"x-test-tenant", b"").decode()
        t = who.get(name)
        if scope["type"] == "http" and t is not None:
            await TN.ensure_ready(t)
            tok = tenancy.set_current(t)
            try:
                return await app(scope, receive, send)
            finally:
                tenancy.reset(tok)
        return await app(scope, receive, send)
    with TestClient(bind, raise_server_exceptions=False) as c:
        yield c


def test_the_api_checks_scopes_and_quotas(client, monkeypatch):
    from backend.api import racepower as RP
    from backend.engine.racepower import backtest as BT
    monkeypatch.setattr(BT, "start_background", lambda ds: True)
    monkeypatch.setattr(RP, "make_plan", lambda body: {"ok": True})
    F, P = {"X-Test-Tenant": "free"}, {"X-Test-Tenant": "paid"}
    r = client.post("/api/v1/racepower/backtest/run", headers=F)
    assert r.status_code == 403 and r.json()["detail"]["scope"] == "calc:backtest"
    assert client.post("/api/v1/racepower/backtest/run", headers=P).status_code == 200
    # SP-713: one route a day counts once — 11 different (typed) routes
    codes = [client.post("/api/v1/racepower/plan", json={"distance_km": 10 + i}, headers=F).status_code
             for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429             # the free tier's 10 a month
    assert client.post("/api/v1/racepower/plan", json={"distance_km": 10}, headers=F).status_code == 200   # seen today
    assert client.post("/api/v1/racepower/plan", json={"distance_km": 10}, headers=P).status_code == 200
    s = client.get("/api/v1/session", headers=F).json()["membership"]
    assert s["role"] == "user" and "calc:backtest" not in s["scopes"]
    assert client.get("/api/v1/session").json()["membership"]["role"] == "owner"


def test_opening_a_page_syncs_only_for_subscribers(client):
    from backend.db import database
    from backend.settings.repository import SettingsRepository
    F = {"X-Test-Tenant": "free"}
    r = client.post("/api/v1/sync/auto", headers=F).json()
    # auto-on-open off by default for a new tenant, or the plan's nightly rule: never a start
    assert r["started"] == []
