"""SP-384: accounts.db, user tenants, the owner migrated in place as user 1, the owner's own
resources, the daily scheduler walking every tenant, and a brand-new user tenant.

Signing in is SP-58: these tests bind a request to a tenant with a small test wrapper (what
the sign-in middleware will do), so they test the data side only."""
from __future__ import annotations

import asyncio
import datetime as dt
from datetime import datetime, timedelta, timezone

import pytest

from backend import tenancy


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A server root in tmp: accounts.db, users/<id>/; the owner's folder is the faked home."""
    from backend import accounts
    from backend.accounts import store, tenants as TN
    root = tmp_path / "server"
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(root))
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    return root


# ---------------------------------------------------------------------------- accounts.db

def test_owner_is_user_1_in_place_and_idempotent(server):
    from backend.accounts import store
    o = store.migrate_owner()
    assert o.id == 1 and o.is_owner and o.folder() == tenancy.home_root().resolve()
    assert store.migrate_owner() == o                          # a second run changes nothing
    assert store.count_users() == 1
    assert not any((tenancy.home_root() / "users").glob("*"))   # nothing moved, no folder made


def test_a_user_signing_up_first_never_takes_id_1(server):
    from backend.accounts import store
    u = store.create_user(email="a@x.tw", google_sub="g-a")
    assert u.id == 2 and u.tenant_dir == "users/2" and u.folder() == server / "users" / "2"
    o = store.migrate_owner()
    assert o.id == 1 and o.is_owner
    with pytest.raises(ValueError):
        store.create_user(role=store.OWNER)


def test_sessions_store_only_the_hash_and_expire(server):
    from backend.accounts import store
    u = store.create_user(email="a@x.tw", google_sub="g-a")
    tok = store.create_session(u.id)
    assert store.session_user(tok).id == u.id
    raw = store.path().read_bytes()
    assert tok.encode() not in raw                              # a stolen accounts.db opens nothing
    later = datetime.now(timezone.utc) + timedelta(days=store.SESSION_DAYS + 1)
    assert store.session_user(tok, now=later) is None           # expired (and removed)
    assert store.session_user(tok) is None
    tok2 = store.create_session(u.id)
    store.set_disabled(u.id, True, "abuse")
    assert store.session_user(tok2) is None                     # disabled: signed out everywhere
    with pytest.raises(ValueError):
        store.set_disabled(1, True)


def test_identities_shares_and_waitlist(server):
    from backend.accounts import store
    u = store.create_user(email="a@x.tw", google_sub="g-a")
    assert store.find_identity("google", "g-a").id == u.id
    assert store.user_by_google_sub("g-a").id == u.id
    with pytest.raises(Exception):
        store.create_user(email="b@x.tw", google_sub="g-a")     # one Google account, one user
    store.add_share("abc123", u.id)
    assert store.share_owner("abc123").id == u.id
    store.remove_share("abc123")
    assert store.share_owner("abc123") is None
    assert store.add_waitlist("Waiting@X.tw") and not store.add_waitlist("waiting@x.tw")
    assert [w["email"] for w in store.waitlist()] == ["waiting@x.tw"]


def test_tenant_of_a_user_and_of_the_owner(server):
    from backend.accounts import store, tenants as TN
    o = store.migrate_owner()
    u = store.create_user(email="a@x.tw")
    assert TN.tenant_of(o) == tenancy.owner()                   # the very same tenant as before
    t = TN.tenant_of(u)
    assert t.id == "u2" and t.kind == tenancy.USER and t.root == t.shared == server / "users" / "2"
    assert t.wko5_dir is None and not (tenancy.OWNER_ONLY_CAPS & t.caps)
    assert tenancy.OWNER_ONLY_CAPS <= tenancy.owner().caps


# ---------------------------------------------------------------------------- the app per tenant

class _Bind:
    """What SP-58's sign-in middleware will do: the request's tenant (here from a test header)."""

    def __init__(self, app, tenants: dict):
        self.app, self.tenants = app, tenants

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        name = dict(scope.get("headers") or []).get(b"x-test-tenant", b"").decode()
        t = self.tenants.get(name)
        if t is None:
            return await self.app(scope, receive, send)
        from backend.accounts import tenants as TN
        await TN.ensure_ready(t)
        tok = tenancy.set_current(t)
        try:
            await self.app(scope, receive, send)
        finally:
            tenancy.reset(tok)


@pytest.fixture
def client(server, monkeypatch, tmp_path):
    from backend.accounts import store, tenants as TN
    from backend.api import plan as plan_api
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    for v in ("WKO5_ATHLETE_DIR", "WKO5COACH_ATHLETE_DIR", "WKO5COACH_MODE"):
        monkeypatch.delenv(v, raising=False)
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()
    owner = store.migrate_owner()
    a, b = store.create_user(email="a@x.tw"), store.create_user(email="b@x.tw")
    tenants = {"owner": TN.tenant_of(owner), "A": TN.tenant_of(a), "B": TN.tenant_of(b)}
    from fastapi.testclient import TestClient
    from backend.main import build_app
    app = build_app(demo=False)
    with TestClient(_Bind(app, tenants), raise_server_exceptions=False) as c:
        c.tenants = tenants
        yield c
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()


def _as(name):
    return {"X-Test-Tenant": name}


def test_a_users_data_is_invisible_to_b(client):
    ev = {"name": "A 的比賽", "date": "2027-03-01", "priority": "A", "kind": "trail"}
    r = client.put("/api/v1/plan/events", json=ev, headers=_as("A"))
    assert r.status_code == 200, r.text
    r = client.put("/api/v1/plan/thresholds", json={"lthr": 171}, headers=_as("A"))
    assert r.status_code < 500
    assert [e["name"] for e in client.get("/api/v1/plan", headers=_as("A")).json()["events"]] == ["A 的比賽"]
    for who in ("B", "owner"):
        got = client.get("/api/v1/plan", headers=_as(who)).json()
        assert got["events"] == [], who
    # each in its own folder and DB
    ta, tb = client.tenants["A"], client.tenants["B"]
    assert (ta.root / "plan.json").exists() and not (tb.root / "plan.json").exists()
    assert (ta.root / "wko5coach.db").exists() and (tb.root / "wko5coach.db").exists()
    assert "A 的比賽" not in (tenancy.home_root() / "plan.json").read_text("utf-8") \
        if (tenancy.home_root() / "plan.json").exists() else True


def test_a_custom_session_stays_with_its_user(client):
    body = {"day": (dt.date.today() + dt.timedelta(days=3)).isoformat(), "kind": "easy", "title": "A 的課",
            "minutes": 40}
    r = client.post("/api/v1/overview/plan/sessions", json=body, headers=_as("A"))
    assert r.status_code == 200, r.text

    def titles(who):
        return [s.get("title") for s in client.get("/api/v1/overview/plan/sessions",
                                                    headers=_as(who)).json().get("sessions", [])]
    assert "A 的課" in titles("A")
    assert "A 的課" not in titles("B") and "A 的課" not in titles("owner")


def test_the_calculator_cache_is_per_user(client, monkeypatch):
    from backend.api import racepower as RP
    from backend.engine.racepower import athlete as ATH
    monkeypatch.setattr(ATH, "derive", lambda ds, today: {"who": tenancy.current().id})
    monkeypatch.setattr(RP, "_cache", RP.TenantSlots())
    assert client.get("/api/v1/racepower/inputs", headers=_as("A")).json() == {"who": "u2"}
    assert client.get("/api/v1/racepower/inputs", headers=_as("B")).json() == {"who": "u3"}
    assert client.get("/api/v1/racepower/inputs", headers=_as("A")).json() == {"who": "u2"}


def test_the_owner_answers_the_same_signed_in_as_before(client):
    """Migration diff (§5 step 3): the owner as user 1 reads exactly what the single-user app did."""
    ev = {"name": "站主的比賽", "date": "2027-05-01", "priority": "A", "kind": "trail"}
    assert client.put("/api/v1/plan/events", json=ev).status_code == 200      # single-user (no tenant set)
    paths = ["/api/v1/plan", "/api/v1/plan/profile", "/api/v1/overview/pmc", "/api/v1/overview/plan/sessions",
             "/api/v1/wko5/dataset/status", "/api/v1/region"]
    for p in paths:
        before = client.get(p)
        after = client.get(p, headers=_as("owner"))
        assert before.status_code == after.status_code, p
        a, b = before.json(), after.json()
        if p == "/api/v1/overview/plan/sessions" and isinstance(a, dict):
            for d in (a, b):
                d.pop("stale", None)
        assert a == b, p


def test_a_user_never_reads_the_owners_own_resources(client, monkeypatch, tmp_path):
    from backend.api import plan as plan_api
    from backend.engine.racepower import weather as WX
    from backend.engine.wko5expr.datasource import wko5_available
    from backend.settings import paths
    from backend.sync import tp_client
    wko5 = tmp_path / "WKO5"
    wko5.mkdir()
    (wko5 / "Owner.wko5athlete").write_bytes(b"x")
    monkeypatch.setenv("CWA_API_KEY", "CWA-OWNER-KEY-123")
    monkeypatch.setenv("TP_CLIENT_ID", "id")
    monkeypatch.setenv("TP_CLIENT_SECRET", "secret")
    owner, a = client.tenants["owner"], client.tenants["A"]
    with tenancy.use(owner):
        assert wko5_available(wko5) and paths.for_tenant(wko5) == wko5
        assert WX.load_key() == "CWA-OWNER-KEY-123"
        assert tp_client.load_client_creds() == ("id", "secret")
    with tenancy.use(a):
        assert not wko5_available(wko5)                        # the owner's WKO5 folder: invisible
        assert paths.for_tenant(wko5) != wko5 and paths.athlete_dir() == paths.no_wko5_dir()
        assert WX.load_key() is None                           # the CWA key: the owner's
        assert tp_client.load_client_creds() is None           # TP's WKO5 client: the owner's
        with pytest.raises(PermissionError):
            WX.save_key("x" * 20)
    monkeypatch.setattr(plan_api, "ATHLETE_DIR", wko5)
    monkeypatch.setattr(plan_api, "_wko5_athlete", lambda: object() if tenancy.is_owner() else None)
    r = client.post("/api/v1/racepower/weather/key", json={"key": "y" * 20}, headers=_as("A"))
    assert r.status_code == 403
    assert client.get("/api/v1/racepower/weather/key", headers=_as("A")).json()["configured"] is False


def test_the_owners_cached_wko5_settings_never_reach_a_user(client, monkeypatch):
    from backend.api import plan as plan_api
    calls = []

    def ath():
        calls.append(tenancy.current().id)
        return None
    monkeypatch.setattr(plan_api, "_wko5_athlete", ath)
    plan_api._wko5_settings.cache_clear()
    with tenancy.use(client.tenants["owner"]):
        plan_api._wko5_settings()
        plan_api._wko5_settings()
    with tenancy.use(client.tenants["A"]):
        plan_api._wko5_settings()
    assert calls == ["owner", "u2"]                        # the owner's is cached, the user's is not


def test_the_server_key_comes_from_the_env_in_multi_user_mode(monkeypatch, server):
    from backend import accounts
    from backend.settings import secrets as S
    monkeypatch.delenv("WKO5COACH_SECRET_KEY", raising=False)
    monkeypatch.setenv(accounts.ENV_ENABLED, "1")
    S.reset_cache()
    try:
        with pytest.raises(S.ServerKeyRequired):
            S.require_server_key()
        with pytest.raises(S.ServerKeyRequired):
            S.seal("x")
        from cryptography.fernet import Fernet
        monkeypatch.setenv("WKO5COACH_SECRET_KEY", Fernet.generate_key().decode())
        S.reset_cache()
        S.require_server_key()
        assert S.unseal(S.seal("pw")) == "pw"
        assert not S.KEY_FILE.exists()                     # no key file in the owner's (a tenant's) folder
    finally:
        S.reset_cache()


# ---------------------------------------------------------------------------- the daily scheduler

def test_the_daily_sync_runs_for_every_tenant_at_its_own_time(server, monkeypatch):
    from backend import accounts
    from backend.accounts import store, tenants as TN
    from backend.db import database
    from backend.settings.repository import SettingsRepository
    from backend.sync import runner, scheduler
    monkeypatch.setenv(accounts.ENV_ENABLED, "1")
    store.migrate_owner()
    a, b, c = (store.create_user(email=f"{n}@x.tw") for n in "abc")
    for u in (a, b, c):
        store.set_plan(u.id, "subscriber")          # their own time; the free tier syncs at night (SP-338)
    ts = {t.id: t for t in TN.sync_tenants()}
    assert set(ts) == {"owner", "u2", "u3", "u4"}

    async def auto_plan(db, athlete_id):
        return ["coros"], {}
    monkeypatch.setattr(runner, "auto_plan", auto_plan)
    times = {"owner": "06:00", "u2": "06:30", "u3": "11:00"}         # u4 never set one: no daily sync
    started = []

    async def go():
        for tid, t in ts.items():
            with tenancy.use(t):
                await TN.ensure_ready(t)
                async with database.AsyncSessionLocal() as db:
                    repo = SettingsRepository(db, 1)
                    await repo.set("athlete.timezone", "Asia/Taipei")
                    if tid in times:
                        await repo.set("sync.schedule.daily_time", times[tid])
                    await db.commit()
        tpe = timezone(timedelta(hours=8))
        start = lambda src, aid, trig, f: started.append((tenancy.current().id, src, trig)) or object()
        at_8 = await scheduler.tick_all(now=datetime(2026, 10, 12, 8, 0, tzinfo=tpe), start=start)
        again = await scheduler.tick_all(now=datetime(2026, 10, 12, 9, 0, tzinfo=tpe), start=start)
        at_noon = await scheduler.tick_all(now=datetime(2026, 10, 12, 12, 0, tzinfo=tpe), start=start)
        return at_8, again, at_noon
    at_8, again, at_noon = asyncio.run(go())
    assert at_8 == {"owner": ["coros"], "u2": ["coros"], "u3": [], "u4": []}
    assert again == {"owner": [], "u2": [], "u3": [], "u4": []}          # once a day each
    assert at_noon["u3"] == ["coros"]
    assert started == [("owner", "coros", "schedule"), ("u2", "coros", "schedule"), ("u3", "coros", "schedule")]
    # each tenant's mark is in its own DB
    for tid in ("owner", "u2", "u3"):
        assert (ts[tid].root / "wko5coach.db").exists()


def test_a_users_sync_time_is_staggered_but_stable():
    from backend.sync import scheduler
    assert scheduler.offset_minutes("owner") == 0
    offs = {scheduler.offset_minutes(f"u{i}") for i in range(2, 60)}
    assert offs <= set(range(scheduler.STAGGER_MIN)) and len(offs) > 10
    assert scheduler.offset_minutes("u7") == scheduler.offset_minutes("u7")
    tpe = timezone(timedelta(hours=8))
    assert not scheduler._due(datetime(2026, 10, 12, 6, 10, tzinfo=tpe), "06:00", 15)
    assert scheduler._due(datetime(2026, 10, 12, 6, 15, tzinfo=tpe), "06:00", 15)


def test_single_user_mode_schedules_the_owner_only(server, monkeypatch):
    from backend import accounts
    from backend.accounts import store, tenants as TN
    monkeypatch.delenv(accounts.ENV_ENABLED, raising=False)
    store.create_user(email="a@x.tw")
    assert [t.id for t in TN.sync_tenants()] == ["owner"]


def test_one_users_sync_does_not_make_anothers_busy(server):
    from backend.sync import runner
    a = tenancy.Tenant(id="u2", kind=tenancy.USER, root=server / "users" / "2", shared=server / "users" / "2")
    b = tenancy.Tenant(id="u3", kind=tenancy.USER, root=server / "users" / "3", shared=server / "users" / "3")
    with tenancy.use(a), runner.hold("coros"):
        assert runner.is_busy("coros")
        with tenancy.use(b):
            assert not runner.is_busy("coros")
            with runner.hold("coros"):
                pass
        with tenancy.use(tenancy.owner()):
            assert not runner.is_busy("coros")


# ---------------------------------------------------------------------------- a brand-new user

def test_a_new_user_with_no_activity_opens_every_page(client):
    from backend.tests.test_new_user_smoke import _get_routes
    bad = []
    for path in _get_routes():
        if path.startswith("/api/v1/debug") or path.startswith("/share"):
            continue
        r = client.get(path, headers=_as("B"))
        if r.status_code >= 500:
            bad.append((path, r.status_code))
    assert bad == []


def test_the_registry_knows_accounts_db_and_the_user_folders(client, server):
    from backend import data_registry as R
    client.get("/api/v1/plan", headers=_as("A"))
    assert R.unclassified_files(server) == []                   # accounts.db (+ WAL), users/
    assert R.unclassified_files(client.tenants["A"].root) == []  # a user's own folder
    assert R.classify("accounts.db").cls == R.SECRET and R.classify("users/2/plan.json").scope == R.INSTANCE
    assert R.classify("users/2/wko5coach.db").backup == R.NEVER  # never in the owner's backup
