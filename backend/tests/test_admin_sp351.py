"""SP-351: the admin page — site settings (stored server-wide, logged), data-source switches, the
users (list, disable / enable, plan), the waitlist (let one in), and who may see any of it."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend import membership as MS, site_config as SC, site_settings as SS, tenancy
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    HTML, OWNER_EMAIL, accept, app, browser, c, csrf, env, location, sign_in, sign_up, started)
from backend.tests.test_membership_sp330 import _user, server  # noqa: F401 — fixtures


@pytest.fixture(autouse=True)
def _fresh_site(monkeypatch):
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    monkeypatch.delenv("WKO5COACH_MAX_USERS", raising=False)
    SS.reset_cache()
    yield
    SS.reset_cache()


# ---------------------------------------------------------------------------- an API client per tenant

@pytest.fixture
def client(server, monkeypatch):  # noqa: F811
    """The app with a header picking the request's tenant (as test_membership_sp330's client); no
    header = the owner. Multi-user data on (accounts.enabled)."""
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.accounts import tenants as TN
    app_ = build_app(demo=False)
    who = {"free": _user(server), "paid": _user(server, "subscriber")}

    async def bind(scope, receive, send):
        name = dict(scope.get("headers") or []).get(b"x-test-tenant", b"").decode()
        t = who.get(name)
        if scope["type"] == "http" and t is not None:
            await TN.ensure_ready(t)
            tok = tenancy.set_current(t)
            try:
                return await app_(scope, receive, send)
            finally:
                tenancy.reset(tok)
        return await app_(scope, receive, send)
    with TestClient(bind, raise_server_exceptions=False) as cl:
        cl.who = who
        yield cl


F = {"X-Test-Tenant": "free"}
P = {"X-Test-Tenant": "paid"}
ADMIN_GETS = ("/api/v1/admin/config", "/api/v1/admin/users", "/api/v1/admin/waitlist", "/api/v1/admin/audit")


# ---------------------------------------------------------------------------- who may

def test_only_an_admin_opens_the_page_and_the_api(client):
    for h in (F, P):
        r = client.get("/admin", headers=h)
        assert r.status_code == 403 and 'data-page="admin"' in r.text          # the page says so itself
        for path in ADMIN_GETS:
            r = client.get(path, headers=h)
            assert r.status_code == 403 and r.json()["detail"]["code"] == "SCOPE_REQUIRED", path
        assert client.put("/api/v1/admin/config", json={"values": {"members.max_users": 5}},
                          headers=h).status_code == 403
        assert client.put("/api/v1/admin/sources", json={"scope": "sync:intervals", "on": False},
                          headers=h).status_code == 403
        assert client.put("/api/v1/admin/users/2", json={"disabled": True}, headers=h).status_code == 403
        assert client.post("/api/v1/admin/waitlist/1/admit", headers=h).status_code == 403
    assert SC.get("members.max_users") == 100 and MS.sources_off() == set()
    # the owner is the admin
    assert client.get("/admin").status_code == 200
    for path in ADMIN_GETS:
        assert client.get(path).status_code == 200, path
    assert {"admin:config", "admin:users"} <= set(client.get("/api/v1/session").json()["membership"]["scopes"])
    assert not {"admin:config", "admin:users"} & set(client.get("/api/v1/session", headers=F).json()["membership"]["scopes"])


def test_signed_in_a_user_is_refused_and_the_owner_is_let_in(c, app):  # noqa: F811
    """Sign-in mode, through the real gate: a signed-in user gets 403 on the page and the API."""
    sign_up(c, "g-alice", "alice@example.com")
    assert c.get("/admin", headers=HTML).status_code == 403
    assert c.get("/api/v1/admin/config").status_code == 403
    assert c.put("/api/v1/admin/config", json={"values": {"members.max_users": 5}},
                 headers={"X-TRC-CSRF": csrf(c)}).status_code == 403
    owner = browser(app)
    assert location(sign_in(owner, "g-owner", OWNER_EMAIL)) == "/"
    assert owner.get("/admin", headers=HTML).status_code == 200
    assert owner.get("/api/v1/admin/config").status_code == 200
    s = owner.get("/api/v1/session").json()
    assert {"admin:config", "admin:users"} <= set(s["membership"]["scopes"])


def test_the_demo_instance_has_no_admin_page(monkeypatch, tmp_path):
    from backend.tests import demo_fixtures as DF
    root = tmp_path / "demo-root"
    DF.demo_env(monkeypatch, root)
    DF.make_base(root)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        assert cl.get("/admin").status_code == 404
        assert cl.get("/api/v1/admin/config").status_code == 404
        assert cl.get("/api/v1/static/admin.html").status_code == 404


def test_the_owners_app_without_sign_in_is_unchanged(monkeypatch, tmp_path):
    """Owner mode: nothing new is asked and the menu is as before (the link shows in sign-in mode only);
    the cap still comes from the environment when the page set none."""
    js = (__import__("pathlib").Path(__file__).resolve().parents[1] / "static" / "shell.js").read_text("utf-8")
    assert "const ADMIN = !!SESSION.login && !DEMO" in js
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "7")
    from backend.accounts import login as LG
    assert LG.max_users() == 7 and SC.source("members.max_users") == "env"
    assert MS.member().role == MS.OWNER and MS.has("admin:config") and MS.has("admin:users")


# ---------------------------------------------------------------------------- site settings

def test_settings_are_site_wide_logged_and_the_page_wins_over_the_environment(client, monkeypatch):
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "50")
    v = client.get("/api/v1/admin/config").json()
    cap = next(i for i in v["items"] if i["key"] == "members.max_users")
    assert cap["value"] == 50 and cap["source"] == "env" and cap["env"] == "TRC_MAX_USERS"
    r = client.put("/api/v1/admin/config", json={"values": {"members.max_users": 500, "sync.night_start": "02:30"}})
    assert r.status_code == 200, r.text
    from backend.accounts import login as LG
    assert LG.max_users() == 500 and SC.source("members.max_users") == "site"
    assert SS.get("members.max_users") == 500                      # in site.json, not a tenant's settings
    # a tenant's own settings DB never got it
    con = sqlite3.connect(client.who["free"].root / "wko5coach.db") if (client.who["free"].root / "wko5coach.db").exists() else None
    if con is not None:
        assert not con.execute("SELECT 1 FROM user_settings WHERE key LIKE 'members.%'").fetchone()
    from backend.sync import scheduler
    assert scheduler.night_slot("u9")[0] == "02:30"
    # a bad value changes nothing
    r = client.put("/api/v1/admin/config", json={"values": {"members.max_users": 0, "sync.night_start": "03:00"}})
    assert r.status_code == 400 and r.json()["detail"] == {"code": "OUT_OF_RANGE", "key": "members.max_users"}
    assert SC.get("sync.night_start") == "02:30"
    # cleared: back to the environment
    client.put("/api/v1/admin/config", json={"values": {"members.max_users": None}})
    assert LG.max_users() == 50
    rows = client.get("/api/v1/admin/audit").json()["rows"]
    got = [(r["action"], r["target"], r["old"], r["new"], r["by"]) for r in rows]
    assert ("config", "members.max_users", None, 500, 1) in got
    assert ("config", "members.max_users", 500, None, 1) in got
    assert ("config", "sync.night_start", None, "02:30", 1) in got
    assert all(r["at"] for r in rows)


def test_quotas_follow_the_admins_limits(client):
    free = client.who["free"]
    client.put("/api/v1/admin/config", json={"values": {"quota.free.calc": 2}})
    with tenancy.use(free):
        MS.consume("calc")
        MS.consume("calc")
        with pytest.raises(Exception) as e:
            MS.consume("calc")
    assert e.value.status_code == 429 and e.value.detail["limit"] == 2


def test_a_new_users_first_back_fill_follows_the_history_range(client, tmp_path, monkeypatch):
    from backend.sync import coros_client as CC, garmin_client as GC, intervals_client as IC
    from backend.tests.test_sync_e2e import make_session
    today = dt.date.today()
    free = client.who["free"]
    with tenancy.use(free):
        assert SC.first_sync_day(dt.date(2020, 1, 1)) == dt.date(2020, 1, 1)          # 0 = everything
    assert client.put("/api/v1/admin/config", json={"values": {"sync.history_days": 30}}).status_code == 200

    async def first_event():
        monkeypatch.setattr(CC, "_get_token_and_base", lambda db, aid: _async(("tok", "https://x", "u1")))
        s = await make_session(tmp_path)
        gen = CC.sync_workouts(s, 1)
        ev = await gen.__anext__()
        await gen.aclose()
        monkeypatch.setattr(GC, "_session", lambda db, aid: _async((object(), SimpleNamespace(garmin_last_sync_at=None))))
        g = GC.sync_workouts(s, 1)
        gev = await g.__anext__()
        await g.aclose()
        await s.close()
        return ev, gev
    with tenancy.use(free):
        ev, gev = asyncio.run(first_event())
        assert ev["status"] == "started" and ev["since"] == (today - dt.timedelta(days=30)).strftime("%Y%m%d")
        assert gev["since"] == (today - dt.timedelta(days=30)).isoformat()
        assert IC.new_backfill(today)["oldest"] == (today - dt.timedelta(days=30)).isoformat()
    # a plan's own range wins for that plan; the owner is never limited by a plan
    client.put("/api/v1/admin/config", json={"plans": {"subscriber": {"sync.history_days": 365}}})
    with tenancy.use(client.who["paid"]):
        assert SC.first_sync_day(dt.date(2020, 1, 1), today) == today - dt.timedelta(days=365)
    with tenancy.use(free):
        assert SC.first_sync_day(dt.date(2020, 1, 1), today) == today - dt.timedelta(days=30)
    with tenancy.use(tenancy.owner()):
        assert SC.first_sync_day(dt.date(2020, 1, 1), today) == today - dt.timedelta(days=30)   # the site's


async def _async(v):
    return v


def test_personal_then_plan_then_site_then_env_then_default(server, monkeypatch):  # noqa: F811
    from backend.engine import fit_archive as FA
    key = "archive.retention_years"
    assert SC.effective(key) == 3 and SC.source(key) == "default"
    SC.set_values({key: 5}, actor=1)
    assert SC.effective(key) == 5 and SC.effective(key, plan="free") == 5
    SC.set_values({}, actor=1, plans={"free": {key: 2}})
    assert SC.effective(key, plan="free") == 2                     # the plan's default beats the site's
    assert SC.effective(key, plan="subscriber") == 5
    assert SC.effective(key, personal=10, plan="free") == 10       # the person's own beats both
    assert SC.effective(key, personal=7, plan="free") == 2         # an invalid own value is ignored
    assert SC.effective(key, plan="free", owner=True) == 5         # the owner skips the plan layer
    # through the archive's own reading, per tenant
    free, paid = _user(server), _user(server, "subscriber")
    import backend.engine.wko5expr.datasource as DS
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, *a: d)
    with tenancy.use(free):
        assert FA._years() == 2 and FA.default_years() == 2
    with tenancy.use(paid):
        assert FA._years() == 5
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, *a: 10 if k == key else d)
    with tenancy.use(free):
        assert FA._years() == 10
    # site value: the page, else the environment, else the default
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "40")
    assert SC.get("members.max_users") == 40
    SC.set_values({"members.max_users": 60}, actor=1)
    assert SC.get("members.max_users") == 60
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "nonsense")
    SC.set_values({"members.max_users": None}, actor=1)
    assert SC.get("members.max_users") == 100 and SC.source("members.max_users") == "default"


def test_the_site_default_region_is_the_last_fallback(server, tmp_path):  # noqa: F811
    from backend.engine import region as RG
    assert RG.detect(tmp_path / "nothing", None) == ("intl", "預設")
    SC.set_values({"defaults.region": "tw"}, actor=1)
    assert RG.detect(tmp_path / "nothing", None) == ("tw", "預設")


def test_the_notice_reaches_every_page_in_its_language(client):
    client.put("/api/v1/admin/config", json={"values": {"notice.zh": "週六凌晨維護", "notice.en": "Maintenance on Saturday"}})
    assert client.get("/api/v1/session", headers=F).json()["notice"] == "週六凌晨維護"
    assert client.get("/api/v1/session?lang=en", headers=F).json()["notice"] == "Maintenance on Saturday"
    client.put("/api/v1/admin/config", json={"values": {"notice.zh": "", "notice.en": ""}})
    assert "notice" not in client.get("/api/v1/session", headers=F).json()


# ---------------------------------------------------------------------------- data sources

def test_a_source_switched_off_pauses_the_scheduler_and_the_settings_page(client, tmp_path):
    from backend.db.models import SyncState
    from backend.settings.repository import SettingsRepository
    from backend.sync import runner
    from backend.tests.test_sync_e2e import make_session
    r = client.put("/api/v1/admin/sources", json={"scope": "sync:coros_credential", "on": False})
    assert r.status_code == 200
    assert next(s for s in r.json()["sources"] if s["scope"] == "sync:coros_credential")["on"] is False
    free = client.who["free"]

    async def ready():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1, coros_access_token="t"))
        await SettingsRepository(s, 1).set("sync.coros.enabled", True)
        await s.commit()
        out = await runner.ready_sources(s, 1)
        await s.close()
        return out
    with tenancy.use(free):
        assert asyncio.run(ready())["coros"] == "site_paused"            # the scheduler skips it
    sess = client.get("/api/v1/session", headers=F).json()["membership"]
    assert "sync:coros_credential" in sess["paused_sources"]             # 設定頁: 暫停中
    from backend.sync import coros_limits as CL
    assert not CL.enabled() and CL.status()["enabled"] is False
    rows = client.get("/api/v1/admin/audit").json()["rows"]
    assert rows[0]["action"] == "source" and rows[0]["target"] == "sync:coros_credential"
    assert (rows[0]["old"], rows[0]["new"]) == (True, False)
    client.put("/api/v1/admin/sources", json={"scope": "sync:coros_credential", "on": True})
    assert CL.enabled() and "sync:coros_credential" not in MS.sources_off()
    assert client.put("/api/v1/admin/sources", json={"scope": "sync:tp_web", "on": False}).status_code == 400


# ---------------------------------------------------------------------------- users

def _plant_sync_state(t, sentinel: str):
    db = t.root / "wko5coach.db"
    con = sqlite3.connect(db)
    con.execute("DELETE FROM sync_state")
    con.execute("INSERT INTO sync_state (athlete_id, coros_access_token, coros_email, coros_last_sync_at, "
                "garmin_last_sync_at) VALUES (1, ?, 'runner@example.com', '2026-10-10 06:00:00', "
                "'2026-10-12 07:30:00')", (sentinel,))
    con.commit()
    con.close()


def test_the_user_list_shows_plan_sync_and_size_but_no_training_data(client):
    free = client.who["free"]
    assert client.get("/api/v1/session", headers=F).status_code == 200      # its folder and DB
    _plant_sync_state(free, "SENTINEL-TOKEN-351")
    (free.root / "fit").mkdir(exist_ok=True)
    (free.root / "fit" / "a.fit").write_bytes(b"x" * 5000)
    from backend.sync import garmin_limits as GL
    GL.pause("GARMIN_RATE_LIMITED", tenant_id=free.id, status=429)
    r = client.get("/api/v1/admin/users")
    assert r.status_code == 200
    assert "SENTINEL-TOKEN-351" not in r.text and "runner@example.com" not in r.text
    d = r.json()
    assert d["accounts"] and d["count"] == 3 and d["max_users"] == 100
    me = next(u for u in d["users"] if u["id"] == int(free.id[1:]))
    assert me["plan"] == "free" and me["role"] == "user" and me["created_at"]
    assert me["last_sync_at"].startswith("2026-10-12")
    assert any(p["code"] == "GARMIN_PAUSED" for p in me["problems"])
    assert me["bytes"] >= 5000
    paid = next(u for u in d["users"] if u["id"] == int(client.who["paid"].id[1:]))
    assert paid["plan"] == "subscriber" and paid["role"] == "subscriber"


def test_disable_enable_and_plan_are_logged_with_old_and_new(client):
    from backend.accounts import store
    uid = int(client.who["free"].id[1:])
    r = client.put(f"/api/v1/admin/users/{uid}", json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled_at"]
    assert store.get_user(uid).disabled
    client.put(f"/api/v1/admin/users/{uid}", json={"disabled": False})
    assert not store.get_user(uid).disabled
    r = client.put(f"/api/v1/admin/users/{uid}", json={"plan": "subscriber", "plan_expires_at": "2027-01-01T00:00:00+00:00"})
    assert r.json()["plan"] == "subscriber" and r.json()["role"] == "subscriber"
    assert client.put("/api/v1/admin/users/1", json={"disabled": True}).json()["detail"]["code"] == "CANNOT_DISABLE_SELF"
    assert client.put(f"/api/v1/admin/users/{uid}", json={"plan": "gold"}).status_code == 400
    rows = client.get("/api/v1/admin/audit").json()["rows"]
    got = [(r["action"], r["target"], r["old"], r["new"]) for r in rows]
    assert ("user.disabled", uid, False, True) in got and ("user.disabled", uid, True, False) in got
    assert ("user.plan", uid, {"plan": "free", "expires_at": None},
            {"plan": "subscriber", "expires_at": "2027-01-01T00:00:00+00:00"}) in got
    # the log names people by id only
    text = SC.audit_path().read_text("utf-8")
    assert "@" not in text
    for line in text.splitlines():
        assert set(json.loads(line)) == {"at", "by", "action", "target", "old", "new"}


# ---------------------------------------------------------------------------- the waitlist

def test_an_admin_lets_one_waitlisted_person_in(c, app, monkeypatch):  # noqa: F811
    from backend import accounts
    from backend.accounts import store
    owner = browser(app)
    assert location(sign_in(owner, "g-owner", OWNER_EMAIL)) == "/"
    tok = {"X-TRC-CSRF": csrf(owner)}
    assert owner.put("/api/v1/admin/config", json={"values": {"members.max_users": 2}}, headers=tok).status_code == 200
    sign_up(c, "g-alice", "alice@example.com")
    carol = browser(app)
    assert location(sign_in(carol, "g-carol", "carol@example.com")) == "/login?error=full"
    w = owner.get("/api/v1/admin/waitlist").json()
    assert [x["email"] for x in w["waitlist"]] == ["carol@example.com"] and w["count"] == 2 and w["max_users"] == 2
    wid = w["waitlist"][0]["id"]
    r = owner.post(f"/api/v1/admin/waitlist/{wid}/admit", headers=tok)
    assert r.status_code == 200, r.text
    uid = r.json()["user_id"]
    assert store.count_users() == 3                                    # past the cap: the admin's choice
    assert (accounts.server_root() / "users" / str(uid) / "wko5coach.db").exists()     # the tenant now
    assert owner.post(f"/api/v1/admin/waitlist/{wid}/admit", headers=tok).status_code == 409
    # carol signs in with the same Google e-mail: the terms, then her account
    assert location(sign_in(carol, "g-carol", "carol@example.com")) == "/signup"
    assert accept(carol).json()["ok"] is True
    assert store.find_identity("google", "g-carol").id == uid
    s = carol.get("/api/v1/session").json()
    assert s["mode"] == "user" and s["user"]["id"] == uid
    # someone else still waits
    dave = browser(app)
    assert location(sign_in(dave, "g-dave", "dave@example.com")) == "/login?error=full"
    rows = owner.get("/api/v1/admin/audit").json()["rows"]
    assert rows[0]["action"] == "waitlist.admit" and rows[0]["target"] == wid and rows[0]["new"] == {"user": uid}
