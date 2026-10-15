"""SP-611: test accounts — an admin makes and deletes them on the admin page, with made-up data on
request; a one-time sign-in link opens one in another browser; they stay out of the member cap,
the user lists the server walks, the research pool, payment and every real sync platform; the
automated tests get a helper that makes many at once (accounts_fixtures)."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

import pytest

from backend import membership as MS, site_config as SC, site_settings as SS, tenancy
from backend.accounts import store, testaccounts as TA
from backend.tests import accounts_fixtures as AF
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    HTML, OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, sign_up, started)
from backend.tests.test_membership_sp330 import _user, server  # noqa: F401 — fixtures


@pytest.fixture(autouse=True)
def _fresh_site(monkeypatch):
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    monkeypatch.delenv("WKO5COACH_MAX_USERS", raising=False)
    SS.reset_cache()
    TA._ACTIVE.clear()
    yield
    SS.reset_cache()
    TA._ACTIVE.clear()


@pytest.fixture
def client(server, monkeypatch):  # noqa: F811
    """The app (multi-user data on, no sign-in): no header = the owner (the admin); X-Test-User: <id>
    = that user's tenant."""
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.accounts import tenants as TN
    app_ = build_app(demo=False)
    who = {"free": _user(server)}

    async def bind(scope, receive, send):
        name = dict(scope.get("headers") or []).get(b"x-test-user", b"").decode()
        t = who.get(name)
        if t is None and name.isdigit():
            u = store.get_user(int(name))
            t = TN.tenant_of(u) if u is not None else None
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


def _as(uid) -> dict:
    return {"X-Test-User": str(uid)}


def _fake_stage(dest, spec, seed, small):
    """Instead of the demo builder's child process: a tenant folder with an empty DB."""
    import sqlite3
    dest.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(dest / "wko5coach.db"))
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT)")
    con.executemany("INSERT INTO workout_files(file_path) VALUES (?)", [(f"x{i}",) for i in range(spec["weeks"])])
    con.commit()
    con.close()


# ---------------------------------------------------------------------------- the admin page

def test_admin_makes_lists_and_deletes_test_accounts_outside_the_cap(client, monkeypatch):
    monkeypatch.setattr(TA, "_stage", _fake_stage)
    real_before = store.count_users()
    r = client.post("/api/v1/admin/test-accounts", json={"name": "測試學員", "count": 3, "mock": None})
    assert r.status_code == 200, r.text
    ids = r.json()["created"]
    assert len(ids) == 3
    rows = {x["id"]: x for x in r.json()["rows"]}
    assert [rows[i]["name"] for i in ids] == ["測試學員 1", "測試學員 2", "測試學員 3"]
    assert all(rows[i]["origin"] == "admin" and rows[i]["mock"] is None for i in ids)
    # marked clearly, and the cap's count is the real members only
    users = client.get("/api/v1/admin/users").json()
    assert {u["id"] for u in users["users"] if u["test"]} == set(ids)
    assert users["count"] == real_before == store.count_users()
    assert store.count_users(include_test=True) == real_before + 3
    SC.set_values({"members.max_users": real_before}, actor=1)
    from backend.accounts import login as LG
    assert LG.full()                                         # full with the real members alone …
    SC.set_values({"members.max_users": real_before + 1}, actor=1)
    assert not LG.full()                                     # … and the 3 test accounts take no place
    assert store.create_user(email="real@example.com", cap=LG.max_users()) is not None
    # one deleted: its folder and rows go, the others stay
    AF.tenant(store.get_user(ids[0]))
    folder = store.get_user(ids[0]).folder()
    assert folder.is_dir()
    r = client.delete(f"/api/v1/admin/test-accounts/{ids[0]}")
    assert r.status_code == 200, r.text
    assert store.get_user(ids[0]) is None and not folder.exists()
    assert {x["id"] for x in r.json()["rows"]} == set(ids[1:])
    # a real member is not deleted from here
    real = store.users()[-1]
    assert not real.is_test
    assert client.delete(f"/api/v1/admin/test-accounts/{real.id}").status_code == 404
    acts = {(a["action"], a["target"]) for a in client.get("/api/v1/admin/audit").json()["rows"]}
    assert {("test.create", i) for i in ids} | {("test.delete", ids[0])} <= acts


def test_only_an_admin_may_touch_test_accounts(client):
    u = TA.create("A", actor=1)
    h = _as(client.who["free"].id[1:])
    assert client.get("/api/v1/admin/test-accounts", headers=h).status_code == 403
    assert client.post("/api/v1/admin/test-accounts", json={"name": "x"}, headers=h).status_code == 403
    assert client.post(f"/api/v1/admin/test-accounts/{u.id}/link", headers=h).status_code == 403
    assert client.delete(f"/api/v1/admin/test-accounts/{u.id}", headers=h).status_code == 403
    assert store.get_user(u.id) is not None


@pytest.mark.parametrize("body,code", [
    ({"name": ""}, "BAD_NAME"), ({"name": "x" * 41}, "BAD_NAME"), ({"name": "x", "count": 21}, "BAD_COUNT"),
    ({"name": "x", "mock": {"weeks": 9}}, "BAD_WEEKS"), ({"name": "x", "mock": {"level": "elite"}}, "BAD_LEVEL")])
def test_what_the_form_may_ask_for(client, body, code):
    r = client.post("/api/v1/admin/test-accounts", json=body)
    assert r.status_code == 400 and r.json()["detail"]["code"] == code
    assert store.count_users(include_test=True) == store.count_users()


def test_made_up_data_is_queued_built_and_shown(client, monkeypatch):
    monkeypatch.setattr(TA, "_stage", _fake_stage)
    r = client.post("/api/v1/admin/test-accounts", json={"name": "教練", "mock": {"weeks": 26, "level": "advanced"}})
    uid = r.json()["created"][0]
    assert TA.wait_idle(30)
    row = next(x for x in client.get("/api/v1/admin/test-accounts").json()["rows"] if x["id"] == uid)
    assert row["mock"]["state"] == "done" and row["mock"]["weeks"] == 26 and row["mock"]["level"] == "advanced"
    assert row["mock"]["activities"] == 26
    folder = store.get_user(uid).folder()
    assert (folder / "wko5coach.db").is_file()
    assert not list(folder.parent.glob(".mock-*"))            # the staging folder is gone


def test_a_build_that_fails_leaves_the_account_without_data(client, monkeypatch):
    def boom(dest, spec, seed, small):
        dest.mkdir(parents=True, exist_ok=True)
        raise RuntimeError("child failed")
    monkeypatch.setattr(TA, "_stage", boom)
    u = TA.create("壞掉", actor=1, mock={"weeks": 8}, start=False)
    TA._ACTIVE.add(u.id)
    out = TA.build_mock(u.id)
    assert out["state"] == "failed"
    assert not u.folder().exists() and not list(u.folder().parent.glob(".mock-*"))
    TA._ACTIVE.discard(u.id)
    # a queued / running state no worker holds (a restart in between) reads as failed
    store.update_test_meta(u.id, mock={"weeks": 8, "level": "beginner", "state": "running"})
    assert TA.mock_state(u.id)["state"] == "failed" and not TA.busy(u.id)


def test_marking_an_account_someone_signed_up_with(client):
    real = store.users()[-1]
    r = client.put(f"/api/v1/admin/users/{real.id}", json={"test": True})
    assert r.status_code == 200 and r.json()["test"] is True
    assert store.get_user(real.id).is_test and TA.view(store.get_user(real.id))["origin"] == "marked"
    # a marked account signs in its own way: no one-time link for it
    r = client.post(f"/api/v1/admin/test-accounts/{real.id}/link")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SIGNIN_OFF"     # owner mode here
    with pytest.raises(TA.Refused, match="NOT_ADMIN_MADE"):
        TA.make_link(store.get_user(real.id), 1)
    assert client.put(f"/api/v1/admin/users/{real.id}", json={"test": False}).json()["test"] is False
    made = TA.create("B", actor=1)
    r = client.put(f"/api/v1/admin/users/{made.id}", json={"test": False})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "ADMIN_MADE"
    r = client.put("/api/v1/admin/users/1", json={"test": True})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CANNOT_MARK_SELF"
    rows = client.get("/api/v1/admin/audit").json()["rows"]
    assert any(x["action"] == "user.test" and x["target"] == real.id for x in rows)


# ---------------------------------------------------------------------------- the made-up data, for real

def test_made_up_data_fills_the_overview_the_plan_and_the_charts(client, monkeypatch):
    """The demo builder's child process, small: 8 weeks of short activities at another level."""
    from backend.engine.wko5expr import datasource
    from backend.db.database import db_path
    from backend.sync import storage
    from backend.engine import routes, event_gpx, race_calc_store, activity_tags
    from backend.engine.racepower import athlete
    from backend.engine.wko5expr import fitcache
    monkeypatch.setattr(datasource, "_db_path", lambda: db_path())
    monkeypatch.setattr(storage, "FIT_ROOT", None)
    monkeypatch.setattr(routes, "HOME", None)
    monkeypatch.setattr(event_gpx, "ROOT", None)
    monkeypatch.setattr(athlete, "HIKE_META", None)
    monkeypatch.delenv(fitcache.ENV_ROOT, raising=False)
    monkeypatch.setattr(event_gpx, "_default_db", lambda: db_path())
    monkeypatch.setattr(race_calc_store, "_default_db", lambda: db_path())
    monkeypatch.setattr(activity_tags, "_default_db", lambda: db_path())
    u = TA.create("進階跑者", actor=1, mock={"weeks": 8, "level": "beginner"}, start=False)
    TA._ACTIVE.add(u.id)
    out = TA.build_mock(u.id, small=True)
    TA._ACTIVE.discard(u.id)
    assert out["state"] == "done", out
    assert out["activities"] >= 10
    h = _as(u.id)
    acts = client.get("/api/v1/app/workouts", headers=h).json()
    rows = acts if isinstance(acts, list) else acts.get("workouts") or []
    assert len(rows) >= 10
    import sqlite3
    con = sqlite3.connect(str(u.folder() / "wko5coach.db"))
    try:
        paths = [r[0] for r in con.execute("SELECT file_path FROM workout_files")]
    finally:
        con.close()
    assert paths and all(p.startswith(str(u.folder())) for p in paths)       # pointed at its own folder
    plan = client.get("/api/v1/plan", headers=h).json()
    assert plan.get("events")                                      # races on the plan
    today = dt.date.today()
    cal = client.get(f"/api/v1/overview/plan/calendar?start={today - dt.timedelta(days=21)}"
                     f"&end={today + dt.timedelta(days=21)}", headers=h)
    assert cal.status_code == 200 and len(cal.text) > 200            # the 課表 around today
    pmc = client.get("/api/v1/overview/pmc", headers=h)
    assert pmc.status_code == 200 and len(pmc.text) > 200            # the training-load chart
    summ = client.get("/api/v1/overview/summary", headers=h)
    assert summ.status_code == 200 and len(summ.text) > 100
    # the owner's own data folder is untouched by any of it
    assert not (tenancy.home_root() / "fit").exists()


def test_levels_change_the_athlete_and_the_weekly_hours(monkeypatch):
    from backend.demo import athlete as A, season as S
    for k in ("CP_W", "CP_HISTORY", "WEIGHT_KG"):
        monkeypatch.setattr(A, k, getattr(A, k))
    monkeypatch.setattr(S, "VOLUME", S.VOLUME)
    anchor = dt.date(2026, 10, 1)
    mid = sum(p.minutes for p in S.schedule(1, anchor, 16).activities)
    A.apply_level("beginner")
    assert A.CP_HISTORY[-1] < 200 and S.VOLUME < 1
    low = sum(p.minutes for p in S.schedule(1, anchor, 16).activities)
    A.apply_level("advanced")
    assert A.CP_HISTORY[-1] > 290
    high = sum(p.minutes for p in S.schedule(1, anchor, 16).activities)
    assert low < mid < high
    with pytest.raises(ValueError):
        A.apply_level("elite")


def test_the_child_build_never_sees_the_sign_in_settings(monkeypatch, tmp_path):
    for k, v in (("WKO5COACH_ACCOUNTS", "1"), ("WKO5COACH_SERVER_ROOT", "/srv"), ("GOOGLE_CLIENT_SECRET", "s"),
                 ("WKO5COACH_OWNER_EMAIL", "o@x"), ("TRC_POLAR_TOKEN", "p"), ("WKO5COACH_PUBLIC_URL", "https://x")):
        monkeypatch.setenv(k, v)
    env = TA.child_env(tmp_path / "t")
    assert not {"WKO5COACH_ACCOUNTS", "WKO5COACH_SERVER_ROOT", "GOOGLE_CLIENT_SECRET", "WKO5COACH_OWNER_EMAIL",
                "TRC_POLAR_TOKEN", "WKO5COACH_PUBLIC_URL"} & set(env)
    assert env["WKO5COACH_HOME"] == str(tmp_path / "t") and env["WKO5COACH_NO_SCHEDULER"] == "1"


# ---------------------------------------------------------------------------- one-time sign-in links

def _make(owner, name="測試學員"):
    r = owner.post("/api/v1/admin/test-accounts", json={"name": name}, headers={"X-TRC-CSRF": csrf(owner)})
    assert r.status_code == 200, r.text
    return r.json()["created"][0]


def _link(owner, uid):
    return owner.post(f"/api/v1/admin/test-accounts/{uid}/link", headers={"X-TRC-CSRF": csrf(owner)})


def _use(cl, token):
    return cl.post("/api/v1/account/test-login", json={"token": token}, headers={"X-TRC-CSRF": csrf(cl)})


def test_a_one_time_link_signs_another_browser_in_as_the_test_account(app, caplog):  # noqa: F811
    caplog.set_level(logging.DEBUG)
    owner = browser(app)
    assert location(sign_in(owner, "g-owner", OWNER_EMAIL)) == "/"
    uid = _make(owner)
    r = _link(owner, uid)
    assert r.status_code == 200, r.text
    url, exp = r.json()["url"], r.json()["expires_at"]
    assert url.startswith("https://testserver/test-login#")
    token = url.split("#", 1)[1]
    left = dt.datetime.fromisoformat(exp) - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(minutes=25) < left <= dt.timedelta(seconds=TA.LINK_TTL_S)
    # the page itself opens signed out (the token never reaches the server in a GET)
    phone = browser(app, "198.51.100.7")
    assert phone.get("/test-login", headers=HTML).status_code == 200
    # the admin's own browser is refused, the link untouched
    r = _use(owner, token)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SIGNED_IN_AS_OTHER"
    assert owner.get("/api/v1/session").json()["user"]["id"] == 1
    # another browser: signed in as the test account, no terms asked
    r = _use(phone, token)
    assert r.status_code == 200 and r.json()["next"] == "/"
    s = phone.get("/api/v1/session").json()
    assert s["user"]["id"] == uid and s["user"]["test"] is True and s["membership"]["test"] is True
    assert phone.get("/api/v1/overview/page", headers=HTML).status_code == 200
    # once only
    other = browser(app, "198.51.100.8")
    r = _use(other, token)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "LINK_INVALID"
    # the token is in no log line, and accounts.db keeps only its hash
    assert not any(token in rec.getMessage() for rec in caplog.records)
    import sqlite3
    con = sqlite3.connect(str(store.path()))
    try:
        dump = "\n".join(con.iterdump())
    finally:
        con.close()
    assert token not in dump
    # nor in the change log
    assert token not in str(SC.audit_rows())


def test_a_link_is_short_lived_and_only_for_test_accounts(app, monkeypatch):  # noqa: F811
    owner = browser(app)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    uid = _make(owner)
    token = _link(owner, uid).json()["url"].split("#", 1)[1]
    later = store._now() + dt.timedelta(seconds=TA.LINK_TTL_S + 1)
    late = browser(app, "198.51.100.9")
    csrf(late)
    with monkeypatch.context() as m:
        m.setattr(store, "_now", lambda: later)
        r = _use(late, token)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "LINK_INVALID"
    # a real member gets no link; a made-up token opens nothing
    alice = browser(app, "198.51.100.10")
    sign_up(alice, "g-alice", "alice@example.com")
    real = store.find_identity("google", "g-alice")
    assert _link(owner, real.id).status_code == 404
    assert _use(browser(app, "198.51.100.11"), "x" * 43).status_code == 404
    # a link made for a test account stops working once it is no longer one … (only marked accounts can be)
    # … and a signed-in member's browser can't be turned into a test account's
    token = _link(owner, uid).json()["url"].split("#", 1)[1]
    assert _use(alice, token).json()["detail"]["code"] == "SIGNED_IN_AS_OTHER"


def test_while_its_data_is_built_no_link_is_made_or_used(app):  # noqa: F811
    owner = browser(app)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    uid = _make(owner)
    token = _link(owner, uid).json()["url"].split("#", 1)[1]
    store.update_test_meta(uid, mock={"weeks": 8, "level": "beginner", "state": "running"})
    TA._ACTIVE.add(uid)
    r = _link(owner, uid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MOCK_RUNNING"
    tester = browser(app, "198.51.100.12")
    r = _use(tester, token)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "MOCK_RUNNING"
    r = owner.delete(f"/api/v1/admin/test-accounts/{uid}", headers={"X-TRC-CSRF": csrf(owner)})
    assert r.status_code == 409
    TA._ACTIVE.discard(uid)
    store.update_test_meta(uid, mock={"weeks": 8, "level": "beginner", "state": "done", "activities": 0})
    assert _use(tester, token).status_code == 200                 # the link was not used up meanwhile


def test_deleting_a_test_account_ends_its_session_and_not_the_admins(app):  # noqa: F811
    owner = browser(app)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    uid = _make(owner)
    tester = AF.signed_in(app, store.get_user(uid), ip="198.51.100.13")
    assert tester.get("/api/v1/account/me").json()["id"] == uid
    spare = AF.link_token(store.get_user(uid))
    r = owner.delete(f"/api/v1/admin/test-accounts/{uid}", headers={"X-TRC-CSRF": csrf(owner)})
    assert r.status_code == 200, r.text
    r = tester.get("/api/v1/account/me")
    assert r.status_code == 401
    assert _use(browser(app, "198.51.100.14"), spare).status_code == 404          # its links went too
    assert owner.get("/api/v1/account/me").json()["id"] == 1
    assert owner.get("/admin", headers=HTML).status_code == 200


def test_signed_in_as_one_test_account_a_link_switches_to_another(app):  # noqa: F811
    a, b = AF.many(2)
    cl = AF.signed_in(app, a, ip="198.51.100.15")
    assert _use(cl, AF.link_token(b)).status_code == 200
    assert cl.get("/api/v1/account/me").json()["id"] == b.id


# ---------------------------------------------------------------------------- left out of everything real

def test_test_accounts_stay_out_of_the_lists_the_server_walks(server, monkeypatch):  # noqa: F811
    from backend.accounts import tenants as TN
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    real = _user(server)
    tests = AF.many(3)
    walked = [u.id for u in store.users()]
    assert not set(walked) & {u.id for u in tests} and int(real.id[1:]) in walked
    assert {t.id for t in TN.sync_tenants()} == {"owner", real.id}
    for u in tests:                                   # used just now, still never warmed or synced
        store.create_session(u.id)
    assert {t.id for t in TN.active_tenants()} <= {real.id}
    assert not {f"u{u.id}" for u in tests} & set(TN.last_visits())
    assert {u.id for u in store.users(include_test=True)} >= {u.id for u in tests}


def test_test_accounts_never_enter_the_research_pool(server):  # noqa: F811
    u = AF.many(1)[0]
    store.record_consent(u.id, "v", {}, analysis=True)
    with store.connect() as con:
        con.execute("INSERT INTO consents(user_id, terms_version, documents, analysis, accepted_at, action) "
                    "VALUES (?, 'v', '{}', 1, '2026-10-15T00:00:00+00:00', ?)", (u.id, store.RESEARCH_B))
    assert not store.research_a_allowed(u) and not store.research_b_consent(u.id)
    from backend.research import a_layer
    seen = []
    a_layer.collect(users=[u], loader=lambda *a, **k: seen.append(a))      # even when handed the account
    assert not seen


def test_test_accounts_connect_no_real_platform_and_sync_nothing(server):  # noqa: F811
    from backend.accounts import tenants as TN
    from backend.sync import runner
    u = AF.many(1)[0]
    t = AF.tenant(u)
    sc = MS.scopes(t)
    assert not sc & MS.TEST_DENIED
    assert {"plan:view", "charts:90d", "import:fit"} <= sc
    store.set_plan(u.id, "subscriber")
    sc = MS.scopes(TN.tenant_of(store.get_user(u.id)))
    assert "charts:full" in sc and not sc & MS.TEST_DENIED      # the paid tier can still be tried
    with tenancy.use(t):
        assert not MS.role_allows("sync:coros_credential") and not MS.role_allows("sync:garmin_trial")
        assert not any(runner._source_allowed(s) for s in runner.SOURCES)


def test_coros_and_garmin_logins_are_refused_for_a_test_account(client):
    u = AF.many(1)[0]
    for path, body in (("/api/v1/auth/coros/login", {"email": "a@b.c", "password": "x", "athlete_id": 1}),
                       ("/api/v1/auth/garmin/login", {"email": "a@b.c", "password": "x", "athlete_id": 1})):
        r = client.post(path, json=body, headers=_as(u.id))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "SCOPE_REQUIRED", (path, r.text)
    from backend.sync import intervals_client as IC
    import unittest.mock as um
    with um.patch.object(IC, "configured", lambda: True):
        r = client.post("/api/v1/auth/intervals/connect", headers=_as(u.id))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "INTERVALS_NOT_ALLOWED", r.text


def test_test_accounts_stay_out_of_payment(app, monkeypatch):  # noqa: F811
    from backend import billing as B
    monkeypatch.setattr(B, "available", lambda: True)
    from backend.billing import service as SV
    called = []

    async def nope(*a, **k):
        called.append(a)
        raise AssertionError("the payment provider was reached")
    monkeypatch.setattr(SV, "checkout", nope)
    monkeypatch.setattr(SV, "portal", nope)
    monkeypatch.setattr(SV, "sync", nope)
    monkeypatch.setattr(SV, "maybe_sync", nope)
    u = AF.many(1)[0]
    cl = AF.signed_in(app, u, ip="198.51.100.16")
    h = {"X-TRC-CSRF": csrf(cl)}
    for path, body in (("/api/v1/billing/checkout", {"interval": "month", "agree": True}),
                       ("/api/v1/billing/portal", {}), ("/api/v1/billing/sync", {})):
        r = cl.post(path, json=body, headers=h)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "TEST_ACCOUNT", (path, r.text)
    monkeypatch.setattr(SV, "view", lambda u: {"plan": u.plan})
    assert cl.get("/api/v1/billing").status_code == 200
    assert not called


def test_a_regular_user_sees_no_test_account(c, app):  # noqa: F811
    AF.many(2)
    sign_up(c, "g-bob", "bob@example.com")
    assert c.get("/api/v1/admin/users").status_code == 403
    assert c.get("/api/v1/admin/test-accounts").status_code == 403
    assert "test" not in c.get("/api/v1/session").json()["user"]


# ---------------------------------------------------------------------------- the helper

def test_the_helper_makes_many_accounts_at_once(server):  # noqa: F811
    users = AF.many(40, prefix="團員")
    assert len({u.id for u in users}) == 40
    assert all(u.is_test and u.display_name.startswith("團員 ") for u in users)
    assert store.count_users(include_test=True) - store.count_users() == 40
    t = AF.tenant(users[-1])
    assert (t.root / "wko5coach.db").is_file()
    with pytest.raises(TA.Refused):
        TA.create_many(TA.MAX_MANY + 1)
