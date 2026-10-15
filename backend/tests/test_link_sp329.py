"""SP-329: one Google account on both sides — linking with 疊石 Cairn (accounts/links.py, api/link.py).

The same Google account (its `sub`) is the same person here and at Cairn; nothing is linked until
the person links in 設定 (a consents row), and unlinking there deletes the project's service tokens.
Cairn trades its shared secret and a sub for a linked user's service token, which opens only the
white-listed /api/v1/link/ routes, by scope, on that user's own tenant.

The sign-in side is SP-58's fake Google (test_login_sp58.py); Cairn is a fake too (httpx
MockTransport): nothing here reaches the network or the real Cairn."""
from __future__ import annotations

import json
import zipfile
import io

import httpx
import pytest

from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, _clean_env, _client, app, browser, c, csrf, env, location, new_terms, sign_in, sign_up,
    started,
)

SECRET = "cairn-shared-secret-SP329-not-real"
CAIRN_URL = "https://cairn.example.test"
TOKEN_URL = "/api/v1/link/cairn/token"
ACCOUNT_URL = "/api/v1/link/account"
LINKS_URL = "/api/v1/account/links"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    from backend.accounts import links as LK
    LK.reset_limits()
    monkeypatch.delenv("TRC_CAIRN_LINK_SECRET", raising=False)
    monkeypatch.delenv("TRC_CAIRN_LINK_SECRET_FILE", raising=False)
    monkeypatch.delenv("TRC_CAIRN_URL", raising=False)
    yield
    LK.reset_limits()


@pytest.fixture
def cairn(monkeypatch):
    """The shared secret set and a fake Cairn answering its internal account route."""
    from backend.accounts import links as LK
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    monkeypatch.setenv("TRC_CAIRN_URL", CAIRN_URL)
    fake = FakeCairn()
    monkeypatch.setattr(LK, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


class FakeCairn:
    """Cairn's side of the contract: GET /api/internal/trc/account?sub= with the shared secret."""

    def __init__(self):
        self.calls: list = []
        self.plans: dict = {}
        self.lie_about_sub = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.headers.get("authorization") != f"Bearer {SECRET}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if request.url.path != "/api/internal/trc/account":
            return httpx.Response(404)
        sub = request.url.params.get("sub")
        if sub not in self.plans:
            return httpx.Response(404, json={"error": "not_linked"})
        return httpx.Response(200, json={"sub": "someone-else" if self.lie_about_sub else sub,
                                         "plan": self.plans[sub], "plan_expires_at": None})


# ---------------------------------------------------------------------------- helpers

def _uid(sub: str) -> int:
    from backend.accounts import store
    u = store.find_identity("google", sub)
    assert u is not None
    return u.id


def _link(cl, linked: bool = True):
    return cl.post(f"{LINKS_URL}/cairn", json={"linked": linked}, headers={"X-TRC-CSRF": csrf(cl)})


def _server(app, ip: str = "198.51.100.7"):
    """Cairn's server: a client with no cookie jar of a browser."""
    return _client(app, ip)


def _token(srv, sub: str, secret: str = SECRET):
    return srv.post(TOKEN_URL, json={"sub": sub}, headers={"Authorization": f"Bearer {secret}"})


def _read(srv, tok: str, path: str = ACCOUNT_URL, **kw):
    return srv.get(path, headers={"Authorization": f"Bearer {tok}", **kw.pop("headers", {})}, **kw)


# ---------------------------------------------------------------------------- the same person, with consent

def test_the_same_google_account_is_the_same_person_once_linked(c, app, cairn):
    sign_up(c, "g-a", "alice@example.com")
    with _server(app) as srv:
        # not linked yet: Cairn is told only where the person can link (no account, or not linked: alike)
        r = _token(srv, "g-a")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "NOT_LINKED"
        assert r.json()["detail"]["link_url"].endswith("/api/v1/app/settings?link=cairn#links")
        assert _token(srv, "g-nobody").json() == r.json()
        r = _link(c)
        assert r.status_code == 200 and r.json()["linked"] is True, r.text
        r = _token(srv, "g-a")
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["token"].startswith("trc_") and got["token_type"] == "Bearer"
        assert set(got["scopes"]) == {"link:account", "link:calc", "link:gpx_sync", "link:weather"}
        assert "link:participant_ability" not in got["scopes"]          # never by a link: SP-326
        r = _read(srv, got["token"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sub"] == "g-a" and body["plan"] == "free" and body["subscribed"] is False
        assert "email" not in body and "no-store" in r.headers["cache-control"]


def test_the_subscription_shows_through_the_link(c, app, cairn):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    store.set_plan(_uid("g-a"), "subscriber", "2099-01-01T00:00:00+00:00")
    with _server(app) as srv:
        body = _read(srv, _token(srv, "g-a").json()["token"]).json()
    assert body["subscribed"] is True and body["role"] == "subscriber"
    assert body["plan_expires_at"].startswith("2099-01-01")


def test_linking_and_unlinking_are_consent_rows_and_unlinking_ends_the_tokens(c, app, cairn):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    version = store.consent_version(uid)
    _link(c)
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        assert _read(srv, tok).status_code == 200
        r = _link(c, False)
        assert r.status_code == 200 and r.json()["linked"] is False
        assert store.count_service_tokens(uid, "cairn") == 0
        r = _read(srv, tok)
        assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_INVALID"
        assert _token(srv, "g-a").status_code == 404                   # no new one either
    rows = store.consents(uid)
    assert [(x["action"], x["client"]) for x in rows] == [("terms", None), ("link", "cairn"), ("unlink", "cairn")]
    # link:calc is not granted by the link: it asks on its own (SP-654, links.FEATURES)
    # link:calc (SP-654) and link:gpx_sync (SP-655) ask on their own: not granted by the link
    assert json.loads(rows[1]["documents"])["scopes"] == ["link:account", "link:weather"]
    # the terms and the analysis consent read as before (a link row says neither)
    assert store.consent_version(uid) == version and store.analysis_consent(uid) is True
    assert c.get("/api/v1/plan").status_code == 200
    # the history goes into 下載我的資料
    z = zipfile.ZipFile(io.BytesIO(c.get("/api/v1/account/export.zip").content))
    acts = [(x["action"], x.get("client")) for x in json.loads(z.read("account.json"))["consents"]]
    assert acts == [("terms", None), ("link", "cairn"), ("unlink", "cairn")]


def test_a_new_token_replaces_the_old_one(c, app, cairn):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    with _server(app) as srv:
        old = _token(srv, "g-a").json()["token"]
        new = _token(srv, "g-a").json()["token"]
        assert old != new and store.count_service_tokens(_uid("g-a"), "cairn") == 1
        assert _read(srv, old).status_code == 401 and _read(srv, new).status_code == 200


def test_the_owner_can_link_too_and_still_accepts_no_terms(c, app, cairn):
    from backend.accounts import store
    assert location(sign_in(c, "g-owner", OWNER_EMAIL)) == "/"
    assert _link(c).json()["linked"] is True
    assert store.analysis_consent(1) is None and store.consent_version(1) is None
    with _server(app) as srv:
        body = _read(srv, _token(srv, "g-owner").json()["token"]).json()
    assert body["sub"] == "g-owner" and body["role"] == "owner"


def test_linking_needs_the_project_to_be_configured(c, app):
    sign_up(c, "g-a", "alice@example.com")
    r = _link(c)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "LINK_UNAVAILABLE"
    row = c.get(LINKS_URL).json()["clients"][0]
    assert row["id"] == "cairn" and row["available"] is False and row["linked"] is False
    assert c.post(f"{LINKS_URL}/cairn", json={"linked": True}).status_code == 403       # CSRF like every write
    assert c.post(f"{LINKS_URL}/nope", json={"linked": True}, headers={"X-TRC-CSRF": csrf(c)}).status_code == 404


# ---------------------------------------------------------------------------- the internal API is authenticated

def test_the_token_exchange_needs_the_shared_secret(c, app, monkeypatch):
    sign_up(c, "g-a", "alice@example.com")
    with _server(app) as srv:
        assert _token(srv, "g-a").status_code == 401                    # no secret set: nobody gets in
        monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
        _link(c)
        r = _token(srv, "g-a", secret="wrong-secret-xxxxxxxx")
        assert r.status_code == 401 and r.json()["detail"]["code"] == "SECRET_INVALID"
        assert srv.post(TOKEN_URL, json={"sub": "g-a"}).status_code == 401
        assert _token(srv, "g-a").status_code == 200


def test_repeated_failures_block_the_ip(c, app, cairn):
    with _server(app, "198.51.100.66") as srv:
        codes = [_token(srv, "g-a", secret="wrong-secret-xxxxxxxx").status_code for _ in range(12)]
        assert codes[0] == 401 and codes[-1] == 429
        # the right secret is checked first: the block never stops Cairn itself (one IP for many callers)
        assert _token(srv, "g-a").status_code == 404
        codes = [_read(srv, "trc_" + "x" * 40).status_code for _ in range(2)]
        assert codes == [429, 429]


def test_a_service_token_opens_only_the_white_listed_routes(c, app, cairn):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        for path in ("/api/v1/plan", "/api/v1/account/me", "/api/v1/account/export.zip", LINKS_URL):
            r = _read(srv, tok, path)
            assert r.status_code == 401 and r.json()["detail"]["code"] == "LOGIN_REQUIRED", path
        r = _read(srv, tok, "/api/v1/link/nope")
        assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_INVALID"
        r = srv.post(ACCOUNT_URL, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401
        # another kind of token (the weight API's) never opens a link route
        wtok = store.create_service_token(_uid("g-a"), "weight", ["weight:write", "link:account"])
        assert _read(srv, wtok).status_code == 401
        # a link route never takes the session cookie instead of a token
        assert c.get(ACCOUNT_URL).status_code == 401


def test_a_token_without_the_routes_scope_is_refused(c, app, cairn):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    tok = store.create_service_token(_uid("g-a"), "cairn", ["link:calc"])
    with _server(app) as srv:
        r = _read(srv, tok)
    assert r.status_code == 403 and r.json()["detail"] == {"code": "SCOPE_REQUIRED", "scope": "link:account"}


def test_a_token_of_a_user_who_did_not_link_is_refused(c, app, cairn):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    tok = store.create_service_token(_uid("g-a"), "cairn", ["link:account"])
    with _server(app) as srv:
        r = _read(srv, tok)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "LINK_REVOKED"


def test_a_token_reads_only_its_own_users_tenant(c, app, cairn):
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    b = browser(app, "203.0.113.20")
    sign_up(b, "g-b", "bob@example.com")
    _link(b)
    with _server(app) as srv:
        tok_a = _token(srv, "g-a").json()["token"]
        assert _read(srv, tok_a).json()["sub"] == "g-a"
        # B's session cookie sent along changes nothing: the token's user, never the cookie's
        from backend.accounts import login as LG
        cookie = b.cookies.get(LG.cookie_name(LG.SESSION_COOKIE))
        r = _read(srv, tok_a, headers={"Cookie": f"{LG.cookie_name(LG.SESSION_COOKIE)}={cookie}"})
        assert r.json()["sub"] == "g-a"
        assert _read(srv, _token(srv, "g-b").json()["token"]).json()["sub"] == "g-b"


def test_a_disabled_user_or_new_terms_stop_the_token(c, app, cairn, monkeypatch):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        new_terms(monkeypatch)
        r = _read(srv, tok)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "TERMS_REQUIRED"
        store.set_disabled(_uid("g-a"), True, "test")
        assert _read(srv, tok).status_code == 401
        assert _token(srv, "g-a").status_code == 404


def test_deleting_the_account_deletes_its_links(c, app, cairn):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    _link(c)
    with _server(app) as srv:
        _token(srv, "g-a")
    out = store.delete_user(uid)
    assert out["api_tokens"] == 1 and out["consents"] == 2


# ---------------------------------------------------------------------------- the other way: Cairn's plan

def test_cairns_plan_is_asked_only_for_a_linked_user(c, app, cairn):
    sign_up(c, "g-a", "alice@example.com")
    cairn.plans["g-a"] = "pro"
    row = c.get(LINKS_URL).json()["clients"][0]
    assert row["linked"] is False and "remote" not in row and cairn.calls == []
    _link(c)
    row = c.get(LINKS_URL).json()["clients"][0]
    assert row["linked"] is True and row["remote"] == {"plan": "pro", "plan_expires_at": None,
                                                        "push_per_day": 100}   # SP-666: Cairn's pushes a day
    assert len(cairn.calls) == 1
    req = cairn.calls[0]
    assert req.url.host == "cairn.example.test" and req.url.params["sub"] == "g-a"
    assert req.headers["authorization"] == f"Bearer {SECRET}"
    # an answer about someone else is ignored; Cairn down is no error for the page
    cairn.lie_about_sub = True
    assert c.get(LINKS_URL).json()["clients"][0]["remote"] is None
    cairn.plans.clear()
    assert c.get(LINKS_URL).json()["clients"][0]["remote"] is None


def test_cairn_is_never_called_without_its_address(c, app, cairn, monkeypatch):
    from backend.accounts import links as LK, store
    monkeypatch.delenv("TRC_CAIRN_URL")
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    assert LK.cairn_status(store.get_user(_uid("g-a"))) is None and cairn.calls == []


# ---------------------------------------------------------------------------- not in the owner's app

def test_owner_mode_has_none_of_it(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TRC_CAIRN_LINK_SECRET", SECRET)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        for m, p in (("GET", LINKS_URL), ("POST", f"{LINKS_URL}/cairn"), ("POST", TOKEN_URL), ("GET", ACCOUNT_URL)):
            r = cl.request(m, p, json={"linked": True, "sub": "x"}, headers={"Authorization": f"Bearer {SECRET}"})
            assert r.status_code == 404, (m, p, r.status_code)


def test_the_demo_never_mounts_it():
    from backend.main import build_app
    paths = {getattr(r, "path", "") for r in build_app(demo=True).routes}
    assert not any(p.startswith("/api/v1/link/") or p.startswith(LINKS_URL) for p in paths)


def test_the_settings_page_has_the_linked_services_block(c, app, cairn):
    sign_up(c, "g-a", "alice@example.com")
    page = c.get("/api/v1/app/settings", headers=HTML).text
    assert 'id="acct-links"' in page and 'data-i18n="settings.acct.links_title"' in page
