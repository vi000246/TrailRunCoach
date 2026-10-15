"""SP-351 R2 (2026-10-15): on the admin page an admin deletes a user (the account-deletion flow of
SP-386 / SP-326), makes others admins (never the last one away, never the owner's), and sets the
sign-in's Google client (the secret sealed, never sent back; the page before the environment; no
restart; a server whose environment has no client but whose page has one still starts)."""
from __future__ import annotations

import json

import pytest

from backend import site_config as SC, site_settings as SS
from backend.tests.test_account_data_sp386 import TOKEN, _fill, _folder, _rows_of, _uid
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    CLIENT_ID, CLIENT_SECRET, OWNER_EMAIL, _clean_env, _client, _signin_env, accept, app, browser, c, csrf,
    env, location, sign_in, sign_up, start, started)


@pytest.fixture(autouse=True)
def _fresh_site(monkeypatch):
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    SS.reset_cache()
    yield
    SS.reset_cache()


def _owner(app):
    owner = browser(app, ip="203.0.113.50")
    assert location(sign_in(owner, "g-owner", OWNER_EMAIL)) == "/"
    owner.tok = {"X-TRC-CSRF": csrf(owner)}
    return owner


def _audit(cl) -> list:
    return [(r["action"], r["target"], r["old"], r["new"], r["by"]) for r in cl.get("/api/v1/admin/audit").json()["rows"]]


# ---------------------------------------------------------------------------- deleting a user

def test_an_admin_deletes_a_user_through_the_account_deletion_flow(c, app, monkeypatch):
    """The same steps as a person deleting their own account: the subscription stopped first, the
    platforms disconnected, the folder, the research pool and every accounts.db row gone; logged."""
    from backend.accounts import deletion as D, store
    from backend.billing import service as billing
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    assert c.get("/api/v1/plan").status_code == 200
    _fill(uid)
    folder = _folder(uid)
    calls = []

    async def revoke(source, creds):
        calls.append(("revoke", source, creds.get("coros_access_token")))
    monkeypatch.setitem(D.REVOKE, "coros", revoke)
    real_cancel, real_delete = billing.cancel_for_deletion, D.delete_account

    async def cancel(u):
        calls.append(("cancel", u.id))
        return await real_cancel(u)

    async def delete(u, **kw):
        calls.append(("delete_account", u.id))
        return await real_delete(u, **kw)
    monkeypatch.setattr(billing, "cancel_for_deletion", cancel)
    monkeypatch.setattr(D, "delete_account", delete)
    from backend.research import pool
    withdrawn = []
    real_withdraw = pool.withdraw
    monkeypatch.setattr(pool, "withdraw", lambda u: withdrawn.append(u) or real_withdraw(u))

    owner = _owner(app)
    # a user without an admin scope can't; the confirmation is asked for
    assert c.post(f"/api/v1/admin/users/{uid}/delete", json={"confirm": True},
                  headers={"X-TRC-CSRF": csrf(c)}).status_code == 403
    r = owner.post(f"/api/v1/admin/users/{uid}/delete", json={}, headers=owner.tok)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CONFIRM_REQUIRED"
    assert folder.exists() and store.get_user(uid) is not None

    r = owner.post(f"/api/v1/admin/users/{uid}/delete", json={"confirm": True}, headers=owner.tok)
    assert r.status_code == 200, r.text
    assert r.json()["disconnected"] == ["coros", "garmin"] and r.json()["revoked"] == ["coros"]
    assert calls == [("cancel", uid), ("delete_account", uid), ("revoke", "coros", TOKEN)]
    assert withdrawn == [uid]                                         # the research pool (SP-326 / SP-586)
    assert not folder.exists() and store.get_user(uid) is None
    assert _rows_of(uid, "g-a", ("alice@example.com",)) == {}
    assert c.get("/api/v1/plan").status_code == 401                   # signed out everywhere
    row = next(x for x in _audit(owner) if x[0] == "user.delete")
    assert row[1] == uid and row[2] == {"plan": "free", "admin": False, "disabled": False}
    assert row[3]["deleted"] is True and row[4] == 1
    assert "alice" not in SC.audit_path().read_text("utf-8")          # by id only, never the e-mail
    assert owner.post(f"/api/v1/admin/users/{uid}/delete", json={"confirm": True},
                      headers=owner.tok).status_code == 404


def test_the_owner_and_yourself_are_never_deleted_here(c, app):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    store.set_admin(uid, True)
    tok = {"X-TRC-CSRF": csrf(c)}
    r = c.post("/api/v1/admin/users/1/delete", json={"confirm": True}, headers=tok)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "OWNER_PROTECTED"
    r = c.post(f"/api/v1/admin/users/{uid}/delete", json={"confirm": True}, headers=tok)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CANNOT_DELETE_SELF"
    assert store.get_user(1) is not None and store.get_user(uid) is not None


# ---------------------------------------------------------------------------- roles

def test_an_admin_makes_someone_else_an_admin_and_takes_it_back(c, app):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    alice = {"X-TRC-CSRF": csrf(c)}
    assert c.get("/api/v1/admin/users").status_code == 403
    assert c.put(f"/api/v1/admin/users/{uid}/admin", json={"admin": True}, headers=alice).status_code == 403
    owner = _owner(app)
    r = owner.put(f"/api/v1/admin/users/{uid}/admin", json={"admin": True}, headers=owner.tok)
    assert r.status_code == 200 and r.json()["admin"] is True and r.json()["role"] == "user"
    # alice now has the admin page — and nothing of the owner's own (計算模式, TP website login)
    scopes = set(c.get("/api/v1/session").json()["membership"]["scopes"])
    assert {"admin:config", "admin:users"} <= scopes and not {"admin", "sync:tp_web", "wko5"} & scopes
    assert "charts:365d" not in scopes                                # her plan is still free
    assert c.get("/api/v1/admin/users").status_code == 200
    users = {u["id"]: u for u in c.get("/api/v1/admin/users").json()["users"]}
    assert users[1]["admin"] is True and users[uid]["admin"] is True
    assert users[uid]["me"] is True and users[1]["me"] is False        # the page hides delete on your own row
    # she can't take the owner's away
    r = c.put("/api/v1/admin/users/1/admin", json={"admin": False}, headers=alice)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "OWNER_ROLE"
    # the owner takes hers back: the page closes for her
    r = owner.put(f"/api/v1/admin/users/{uid}/admin", json={"admin": False}, headers=owner.tok)
    assert r.status_code == 200 and r.json()["admin"] is False
    assert c.get("/api/v1/admin/users").status_code == 403
    assert not store.get_user(uid).is_admin
    rows = _audit(owner)
    assert ("user.admin", uid, False, True, 1) in rows and ("user.admin", uid, True, False, 1) in rows


def test_the_last_admin_is_never_taken_away(tmp_path, monkeypatch):
    """Without the owner's row (a server where someone signed up before the owner's migration),
    the only admin can't lose it nor be deleted; with another admin, either may go."""
    from backend import accounts
    from backend.accounts import store
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    monkeypatch.setattr(store, "_READY", set())
    a = store.create_user(email="a@x.tw")
    b = store.create_user(email="b@x.tw")
    store.set_admin(a.id, True)
    assert store.is_last_admin(a.id)
    with pytest.raises(store.LastAdmin):
        store.set_admin(a.id, False)
    store.set_admin(b.id, True)
    assert not store.is_last_admin(a.id)
    store.set_admin(a.id, False)
    with pytest.raises(store.LastAdmin):
        store.set_admin(b.id, False)
    store.set_disabled(b.id, True)                     # a disabled admin is no admin to fall back on
    store.set_admin(a.id, True)
    assert store.is_last_admin(a.id)
    store.migrate_owner(tmp_path / "owner")
    with pytest.raises(ValueError):
        store.set_admin(1, False)                      # the owner is always one
    assert store.get_user(1).is_admin and not store.is_last_admin(a.id)


# ---------------------------------------------------------------------------- the Google client

OTHER_ID = "other-client.apps.googleusercontent.example"


def test_the_google_client_secret_is_sealed_and_never_sent_back(c, app):
    from backend.accounts import google as G
    owner = _owner(app)
    sign_up(c, "g-a", "alice@example.com")
    assert c.get("/api/v1/admin/google").status_code == 403
    assert c.put("/api/v1/admin/google", json={"client_id": OTHER_ID}, headers={"X-TRC-CSRF": csrf(c)}).status_code == 403
    v = owner.get("/api/v1/admin/google").json()
    assert v["signin"] is True and v["redirect_uri"] == "https://testserver/oauth/google/callback"
    assert v["client_id"] == {"value": CLIENT_ID, "source": "env", "site": False, "env": True}
    assert v["client_secret"]["set"] is True and v["client_secret"]["source"] == "env"
    assert CLIENT_SECRET not in json.dumps(v)
    secret = "GOCSPX-admin-page-secret-351"
    r = owner.put("/api/v1/admin/google", json={"client_id": CLIENT_ID, "client_secret": secret}, headers=owner.tok)
    assert r.status_code == 200, r.text
    assert secret not in r.text and r.json()["client_secret"] == {
        "set": True, "source": "site", "site": True, "unreadable": False, "env": True}
    assert secret not in owner.get("/api/v1/admin/google").text
    raw = SS.path().read_text("utf-8")
    assert secret not in raw and json.loads(raw)["google.client_secret"].startswith("enc:v1:")
    assert G.client_secret() == secret                                # in force, no restart
    rows = _audit(owner)
    assert ("google.secret", "google", None, None, 1) in rows         # "updated": never the value
    assert secret not in SC.audit_path().read_text("utf-8")
    # the same secret again changes nothing; clearing it is logged as cleared
    owner.put("/api/v1/admin/google", json={"client_secret": secret}, headers=owner.tok)
    assert [x[0] for x in _audit(owner)].count("google.secret") == 1
    owner.put("/api/v1/admin/google", json={"client_secret": None}, headers=owner.tok)
    assert _audit(owner)[0][:4] == ("google.secret_cleared", "google", None, None)
    assert G.client_secret() == CLIENT_SECRET
    bad = owner.put("/api/v1/admin/google", json={"client_id": "has spaces"}, headers=owner.tok)
    assert bad.status_code == 400 and bad.json()["detail"] == {"code": "BAD_VALUE", "key": "google.client_id"}
    assert owner.put("/api/v1/admin/google", json={"other": 1}, headers=owner.tok).status_code == 422


def test_the_page_wins_over_the_environment_and_needs_no_restart(c, app):
    from backend.accounts import google as G
    from urllib.parse import parse_qs, urlsplit
    owner = _owner(app)
    q = lambda: parse_qs(urlsplit(start(browser(app, ip="203.0.113.70"))).query)["client_id"][0]  # noqa: E731
    assert q() == CLIENT_ID
    r = owner.put("/api/v1/admin/google", json={"client_id": OTHER_ID}, headers=owner.tok)
    assert r.status_code == 200 and r.json()["client_id"]["source"] == "site"
    assert G.client_id() == OTHER_ID and q() == OTHER_ID              # the next sign-in, same process
    assert ("google.client_id", "google", None, OTHER_ID, 1) in _audit(owner)
    owner.put("/api/v1/admin/google", json={"client_id": ""}, headers=owner.tok)
    assert G.client_id() == CLIENT_ID and q() == CLIENT_ID            # cleared: the environment again
    # signing in still works end to end with the page's client
    owner.put("/api/v1/admin/google", json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}, headers=owner.tok)
    sign_up(browser(app, ip="203.0.113.71"), "g-b", "bob@example.com")


def _site_client(tmp_path, monkeypatch, cid=CLIENT_ID, secret=CLIENT_SECRET):
    from backend.settings import secrets
    SS.reset_cache()
    secrets.reset_cache()
    vals = {}
    if cid:
        vals["google.client_id"] = cid
    if secret:
        vals["google.client_secret"] = secrets.seal(secret)
    SS.update(vals)


def test_without_a_client_in_the_environment_the_pages_starts_sign_in(monkeypatch, tmp_path, env):
    """The chicken and egg: the page is behind sign-in, so a client set there counts at start-up."""
    from backend.accounts import google as G, login as LG
    _clean_env(monkeypatch, tmp_path)
    _signin_env(monkeypatch)
    monkeypatch.delenv("GOOGLE_CLIENT_ID")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET")
    with pytest.raises(LG.LoginConfigError):                          # neither: the rule as before
        LG.decide()
    _site_client(tmp_path, monkeypatch, secret=None)
    with pytest.raises(LG.LoginConfigError):                          # half a client: still refused
        LG.decide()
    _site_client(tmp_path, monkeypatch)
    from backend.main import build_app
    a = build_app(demo=False)
    a.fake = env
    with _client(a) as cl:
        cl.fake, cl.trc_app = env, a
        assert LG.mode() == "signin" and G.env_client() == (None, None)
        assert G.client_id() == CLIENT_ID and G.configured()
        assert location(sign_in(cl, "g-owner", OWNER_EMAIL)) == "/"
        owner = cl
        tok = {"X-TRC-CSRF": csrf(owner)}
        v = owner.get("/api/v1/admin/google").json()
        assert v["client_id"]["source"] == "site" and v["client_secret"]["source"] == "site"
        assert v["client_id"]["env"] is False and v["client_secret"]["env"] is False
        # nothing to fall back on: the page's client can't be cleared (sign-in would stop for everyone)
        r = owner.put("/api/v1/admin/google", json={"client_secret": None}, headers=tok)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "GOOGLE_INCOMPLETE"
        r = owner.put("/api/v1/admin/google", json={"client_id": None}, headers=tok)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "GOOGLE_INCOMPLETE"
        assert G.configured()


def test_an_unreadable_file_is_passed_over_when_the_page_has_the_part(monkeypatch, tmp_path, env):
    from backend.accounts import login as LG
    _clean_env(monkeypatch, tmp_path)
    _signin_env(monkeypatch)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_FILE", str(tmp_path / "missing"))
    with pytest.raises(LG.LoginConfigError):
        LG.decide()
    _site_client(tmp_path, monkeypatch, cid=None)
    assert LG.decide() == "signin"
