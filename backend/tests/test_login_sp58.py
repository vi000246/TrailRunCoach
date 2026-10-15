"""SP-58: Google sign-in (OIDC), sign-up with the terms, server-side sessions, the sign-in gate.

The Google side is a fake (FakeGoogle below, served through httpx.MockTransport): it checks the
PKCE verifier against the challenge of the authorization request, the client's credentials and
the redirect URI like Google does, and signs the id_token with an RSA key of its own (a fake
JWKS). Nothing here reaches the network, the real ~/.wko5coach or a real Google account.

The safety gate (the owner must never be locked out by deploying this, nor the data opened by a
half-done configuration): the mode is decided once at start-up and frozen. Sign-in mode needs
WKO5COACH_ACCOUNTS=1 AND every sign-in setting (Google client, public URL, the owner's account,
the trusted proxy); none of them set = today's owner mode; anything in between refuses to
start. The demo instance never asks."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from backend import tenancy

CLIENT_ID = "test-client.apps.googleusercontent.example"
CLIENT_SECRET = "test-client-secret-not-real"
OWNER_EMAIL = "owner@example.com"
PUBLIC_URL = "https://testserver"
PROXY = "10.9.9.9/32"                      # the (fake) cloudflared container
HTML = {"Accept": "text/html,application/xhtml+xml"}
# sign-in cookies over https: the __Host- prefix (Secure, Path=/, no Domain)
SESSION, SIGNUP, OAUTH, CSRF = "__Host-trc_session", "__Host-trc_signup", "__Host-trc_oauth", "__Host-trc_csrf"


# ---------------------------------------------------------------------------- the fake Google

class FakeGoogle:
    """Authorization server + token endpoint + JWKS, like accounts.google.com answers them."""

    def __init__(self):
        from joserfc.jwk import RSAKey
        self.kid = "fake-k1"
        self.key = RSAKey.generate_key(2048, parameters={"kid": self.kid}, private=True)
        self.codes: dict = {}
        self.token_calls = 0
        self.jwks_calls = 0

    def jwks(self) -> dict:
        return {"keys": [self.key.as_dict(private=False)]}

    def authorize(self, auth_url: str, sub: str, email: str, email_verified: bool = True,
                  name: str = "跑者", claims: dict | None = None, sign_with=None) -> str:
        """The user picks an account at Google: the callback URL Google redirects the browser to."""
        from backend.accounts import google as G
        u = urlsplit(auth_url)
        assert f"{u.scheme}://{u.netloc}{u.path}" == G.AUTHORIZE_URL
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        assert q["client_id"] == CLIENT_ID and q["response_type"] == "code"
        assert q["redirect_uri"] == PUBLIC_URL + "/oauth/google/callback"
        assert set(q["scope"].split()) == {"openid", "email", "profile"}
        assert q["code_challenge_method"] == "S256" and q["code_challenge"] and q["nonce"] and q["state"]
        code = "4/" + secrets.token_urlsafe(16)
        self.codes[code] = {"challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"], "nonce": q["nonce"],
                            "sub": sub, "email": email, "email_verified": email_verified, "name": name,
                            "claims": claims or {}, "sign_with": sign_with}
        cb = urlsplit(q["redirect_uri"])
        return f"{cb.path}?{urlencode({'state': q['state'], 'code': code, 'scope': q['scope']})}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        from backend.accounts import google as G
        url = str(request.url)
        if url == G.JWKS_URL:
            self.jwks_calls += 1
            return httpx.Response(200, json=self.jwks())
        if url == G.TOKEN_URL and request.method == "POST":
            self.token_calls += 1
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            auth = request.headers.get("authorization", "")
            want = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if auth != want and not (form.get("client_id") == CLIENT_ID and form.get("client_secret") == CLIENT_SECRET):
                return httpx.Response(401, json={"error": "invalid_client"})
            entry = self.codes.pop(form.get("code", ""), None)
            if entry is None or form.get("grant_type") != "authorization_code":
                return httpx.Response(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not verifier or challenge != entry["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "code_verifier"})
            if form.get("redirect_uri") != entry["redirect_uri"]:
                return httpx.Response(400, json={"error": "redirect_uri_mismatch"})
            from joserfc import jwt
            now = int(time.time())
            claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "azp": CLIENT_ID, "sub": entry["sub"],
                      "email": entry["email"], "email_verified": entry["email_verified"], "name": entry["name"],
                      "iat": now, "exp": now + 3600, "nonce": entry["nonce"]}
            claims.update(entry["claims"])
            claims = {k: v for k, v in claims.items() if v is not None}
            key = entry["sign_with"] or self.key
            id_token = jwt.encode({"alg": "RS256", "kid": self.kid}, claims, key)
            return httpx.Response(200, json={"access_token": "ya29.fake-" + secrets.token_hex(8), "expires_in": 3599,
                                             "token_type": "Bearer", "scope": "openid email profile",
                                             "id_token": id_token})
        return httpx.Response(404, json={"error": "not_found"})


# ---------------------------------------------------------------------------- fixtures

SIGNIN_ENV = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_ID_FILE", "GOOGLE_CLIENT_SECRET_FILE",
              "WKO5COACH_OWNER_GOOGLE_SUB", "WKO5COACH_OWNER_EMAIL", "WKO5COACH_PUBLIC_URL",
              "WKO5COACH_TRUSTED_PROXIES", "WKO5COACH_ACCOUNTS", "WKO5COACH_MAX_USERS",
              "WKO5COACH_COOKIE_SECURE", "WKO5COACH_COOKIE_SAMESITE", "WKO5COACH_MODE")


def _clean_env(monkeypatch, tmp_path):
    from backend import accounts
    from backend.accounts import store, tenants as TN
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "owner"))       # the owner's folder, this test's own
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    for v in SIGNIN_ENV + ("WKO5_ATHLETE_DIR", "WKO5COACH_ATHLETE_DIR"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)


def _signin_env(monkeypatch):
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("WKO5COACH_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("WKO5COACH_TRUSTED_PROXIES", PROXY)
    monkeypatch.setenv("WKO5COACH_OWNER_EMAIL", OWNER_EMAIL)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Sign-in mode with a (fake) Google client; the owner's Google account named by e-mail."""
    from backend.accounts import google as G, login as LG
    from backend.api import plan as plan_api
    _clean_env(monkeypatch, tmp_path)
    _signin_env(monkeypatch)
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()
    fake = FakeGoogle()
    monkeypatch.setattr(G, "TRANSPORT", httpx.MockTransport(fake.handle))
    G.forget_keys()
    yield fake
    LG.reset_mode()
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()


def _client(app, ip: str = "203.0.113.10"):
    from fastapi.testclient import TestClient
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False,
                      follow_redirects=False, client=(ip, 40000))


@pytest.fixture
def started(env):
    """The app started once (lifespan: the mode decided, accounts.db with user 1), with its first
    browser `.main` open for the whole test (one event loop for the app's DB connections).
    User 1 has no Google account yet."""
    from backend.main import build_app
    a = build_app(demo=False)
    a.fake = env
    with _client(a) as main:
        main.fake, main.trc_app = env, a
        a.main = main
        yield a


@pytest.fixture
def app(started):
    """…with the owner's Google account (g-owner) linked to user 1, as after the owner's first
    sign-in: others may sign up from then on."""
    from backend.accounts import store
    assert store.claim_owner_google("g-owner", OWNER_EMAIL)
    return started


@pytest.fixture
def c(app):
    return app.main


def browser(app, ip: str = "203.0.113.10"):
    """Another browser (its own cookie jar) on the same app."""
    cl = _client(app, ip)
    cl.fake, cl.trc_app = app.fake, app
    return cl


def csrf(cl) -> str:
    r = cl.get("/api/v1/session")
    assert r.status_code == 200, r.text
    return r.json()["csrf"]


def start(cl, nxt: str = "/") -> str:
    """「用 Google 登入」: the Google authorization URL the browser is sent to."""
    from backend.accounts import google as G
    r = cl.get("/api/v1/account/login", params={"next": nxt})
    assert r.status_code in (302, 303), r.text
    url = r.headers["location"]
    assert url.startswith(G.AUTHORIZE_URL), url
    return url


def sign_in(cl, sub: str, email: str, nxt: str = "/", verified: bool = True, name: str = "跑者", **kw):
    """Start, pick the account at Google, come back: the callback's response."""
    cb = cl.fake.authorize(start(cl, nxt), sub=sub, email=email, email_verified=verified, name=name, **kw)
    return cl.get(cb)


def accept(cl, **over):
    """Tick the terms on the sign-up page: what the page posts (the version and digest it was shown)."""
    info = cl.get("/api/v1/account/signup").json()
    body = {"accept": True, "version": info["terms"]["version"], "digest": info["terms"]["digest"], **over}
    return cl.post("/api/v1/account/signup", json=body, headers={"X-TRC-CSRF": csrf(cl)})


def sign_up(cl, sub: str, email: str, nxt: str = "/", name: str = "跑者") -> dict:
    r = sign_in(cl, sub, email, nxt, name=name)
    assert r.status_code == 303 and r.headers["location"] == "/signup", (r.status_code, r.headers.get("location"))
    r = accept(cl)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    return r.json()


def location(r) -> str:
    return r.headers.get("location", "")


def set_cookie_lines(r) -> list[str]:
    return r.headers.get_list("set-cookie")


def new_terms(monkeypatch, version: str = "2099-01-01"):
    """A new terms version (its digests pinned, as the registry wants)."""
    from backend.accounts import terms
    monkeypatch.setattr(terms, "VERSION", version)
    monkeypatch.setitem(terms.PINNED, version, terms.digests())


# ---------------------------------------------------------------------------- the safety gate

@pytest.mark.parametrize("on", [
    {},                                   # today's self-hosted app
    {"WKO5COACH_ACCOUNTS": "1"},          # multi-user data (SP-384), no sign-in settings at all yet
    {"WKO5COACH_ACCOUNTS": "0"},
    {"WKO5COACH_ACCOUNTS": "off", "WKO5COACH_TRUSTED_PROXIES": PROXY, "WKO5COACH_COOKIE_SECURE": "0"},
    # the production NAS before sign-in: the site address is set for the 課表 feed's links
    # (SP-54, api/calendar_feed.py) — a site setting, not a sign-in one (owner 2026-10-13)
    {"WKO5COACH_PUBLIC_URL": "https://coach.example.com", "WKO5COACH_TRUSTED_PROXIES": PROXY},
])
def test_without_sign_in_settings_nothing_changes(on, monkeypatch, tmp_path):
    """No sign-in setting at all: every request is the owner as before (no 401, no CSRF header
    needed, no sign-in pages), and /healthz says so."""
    from backend.accounts import login as LG
    _clean_env(monkeypatch, tmp_path)
    for k, v in on.items():
        monkeypatch.setenv(k, v)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        assert LG.mode() == "owner" and not LG.required()
        assert cl.get("/api/v1/plan").status_code == 200
        ev = {"name": "沒登入也能改", "date": "2027-03-01", "priority": "A", "kind": "trail"}
        assert cl.put("/api/v1/plan/events", json=ev).status_code == 200          # no CSRF header, as before
        assert cl.get("/api/v1/overview/page", headers=HTML).status_code == 200
        s = cl.get("/api/v1/session").json()
        assert s["mode"] == "owner" and s["user"] is None and "login" not in s
        assert "trc_csrf=" in cl.get("/api/v1/session").headers["set-cookie"]     # the owner's cookie name as before
        for p in ("/login", "/signup", "/api/v1/account/login", "/oauth/google/callback", "/api/v1/account/signup"):
            assert cl.get(p).status_code == 404, p
        assert cl.get("/healthz").json() == {"ok": True, "mode": "owner"}


@pytest.mark.parametrize("case", [
    "google_without_accounts", "owner_email_without_accounts",
    "id_without_secret", "secret_file_unreadable", "no_public_url", "public_url_not_https",
    "public_url_with_path", "https_with_default_proxies", "https_with_no_proxy_at_all", "accounts_typo", "no_owner",
])
def test_a_half_done_sign_in_setup_refuses_to_start(case, monkeypatch, tmp_path):
    """Any sign-in setting present but the set incomplete (or the switch unreadable): the app
    stops at start-up with the reason — never quietly the owner's app, never a request that
    happens to be the owner's."""
    from backend.accounts import login as LG
    _clean_env(monkeypatch, tmp_path)
    _signin_env(monkeypatch)
    off = lambda *names: [monkeypatch.delenv(n, raising=False) for n in names]
    if case == "google_without_accounts":
        off("WKO5COACH_ACCOUNTS", "WKO5COACH_PUBLIC_URL", "WKO5COACH_OWNER_EMAIL")
    elif case == "owner_email_without_accounts":
        monkeypatch.setenv("WKO5COACH_ACCOUNTS", "0")
        off("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "WKO5COACH_PUBLIC_URL")
    elif case == "id_without_secret":
        off("GOOGLE_CLIENT_SECRET")
    elif case == "secret_file_unreadable":
        off("GOOGLE_CLIENT_SECRET")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET_FILE", str(tmp_path / "missing-secret"))
    elif case == "no_public_url":
        off("WKO5COACH_PUBLIC_URL")
    elif case == "public_url_not_https":
        monkeypatch.setenv("WKO5COACH_PUBLIC_URL", "http://coach.example.com")
    elif case == "public_url_with_path":
        monkeypatch.setenv("WKO5COACH_PUBLIC_URL", "https://coach.example.com/app")
    elif case == "https_with_default_proxies":
        off("WKO5COACH_TRUSTED_PROXIES")
    elif case == "https_with_no_proxy_at_all":
        monkeypatch.setenv("WKO5COACH_TRUSTED_PROXIES", " , not-an-address")
    elif case == "accounts_typo":
        monkeypatch.setenv("WKO5COACH_ACCOUNTS", "yes please")
    elif case == "no_owner":
        off("WKO5COACH_OWNER_EMAIL")
    with pytest.raises(LG.LoginConfigError):
        LG.decide()
    assert LG.mode() is None                                  # nothing frozen
    from backend.main import build_app
    with pytest.raises(LG.LoginConfigError):
        with _client(build_app(demo=False)):
            pass


def test_the_mode_is_frozen_at_start_up(env, started, monkeypatch, tmp_path):
    """Decided once: a secret file that turns unreadable, or the env changing under a running
    process, never downgrades a request to the owner's app."""
    from backend.accounts import login as LG
    c = started.main
    assert LG.mode() == "signin" and c.get("/healthz").json() == {"ok": True, "mode": "signin"}
    for v in ("WKO5COACH_ACCOUNTS", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "WKO5COACH_PUBLIC_URL"):
        monkeypatch.delenv(v)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_FILE", str(tmp_path / "gone"))
    assert c.get("/api/v1/plan").status_code == 401
    assert browser(started).get("/api/v1/plan").status_code == 401
    assert LG.required()


def test_sign_in_mode_needs_the_owners_google_account_until_it_is_linked(env, monkeypatch):
    from backend.accounts import login as LG, store
    from backend.main import build_app
    monkeypatch.delenv("WKO5COACH_OWNER_EMAIL")
    with pytest.raises(LG.LoginConfigError):
        with _client(build_app(demo=False)):
            pass
    store.migrate_owner()
    assert store.claim_owner_google("g-owner", OWNER_EMAIL)
    with _client(build_app(demo=False)) as cl:               # linked once: the env is no longer needed
        assert cl.get("/api/v1/plan").status_code == 401


def test_an_owner_email_alone_logs_a_hint_to_use_the_sub(env, caplog):
    from backend.accounts import login as LG
    caplog.set_level(logging.WARNING)
    assert LG.decide() == "signin"
    assert "WKO5COACH_OWNER_GOOGLE_SUB" in caplog.text


def test_the_demo_instance_never_asks_to_sign_in(monkeypatch, tmp_path):
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    _signin_env(monkeypatch)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        s = cl.get("/api/v1/session").json()
        assert s["mode"] == "demo" and "login" not in s
        assert "trc_csrf=" in cl.get("/api/v1/session").headers["set-cookie"]     # the demo's cookie name as before
        assert cl.get("/api/v1/plan").status_code == 200
        assert cl.get("/api/v1/overview/page", headers=HTML).status_code == 200
        assert cl.get("/login").status_code == 404 and cl.get("/api/v1/account/login").status_code == 404


# ---------------------------------------------------------------------------- the gate

def test_signed_out_only_the_public_paths_open(c):
    r = c.get("/api/v1/plan")
    assert r.status_code == 401 and r.json()["code"] == "LOGIN_REQUIRED"
    r = c.get("/api/v1/overview/page", headers=HTML)
    assert r.status_code in (302, 303) and location(r) == "/login?next=%2Fapi%2Fv1%2Foverview%2Fpage"
    r = c.get("/api/v1/plan/page?event=x", headers=HTML)
    assert location(r) == "/login?next=%2Fapi%2Fv1%2Fplan%2Fpage%3Fevent%3Dx"
    assert c.get("/", headers=HTML).status_code == 200                 # the 介紹頁 (SP-622)
    r = c.get("/login")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"] and 'id="lg-google"' in r.text
    assert c.get("/signup").status_code == 200
    s = c.get("/api/v1/session").json()
    assert s["mode"] == "signed_out" and s["caps"] == [] and s["user"] is None and s["membership"] is None
    assert s["login"] == {"required": True, "signed_in": False} and s["csrf"]
    assert c.get("/healthz").status_code == 200
    assert c.get("/api/v1/static/shell.js").status_code == 200
    assert c.get("/share/nope").status_code == 200                     # a share page: not behind sign-in
    assert c.get("/share/nope/data").status_code == 404                # (no such share: its own 404)
    assert location(c.get("/oauth/google/callback")).startswith("/login?error=")
    ev = {"name": "x", "date": "2027-03-01", "priority": "A", "kind": "trail"}
    assert c.put("/api/v1/plan/events", json=ev, headers={"X-TRC-CSRF": csrf(c)}).status_code == 401
    assert c.get("/api/v1/nope").status_code == 401                    # unknown paths too


def test_every_private_route_needs_a_session(c, app):
    """Enumerate the app: every route outside the public list answers 401 without a session
    (a new router can't be left open by accident)."""
    import re
    from backend.accounts import login as LG
    tok = csrf(c)
    bad = []
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None) or {"GET"}
        if not path:
            continue
        concrete = re.sub(r"\{[^}]+\}", "x", path)
        if LG.is_public(concrete) or concrete.startswith("/share"):
            continue
        for m in sorted(methods - {"HEAD", "OPTIONS"}):
            resp = c.request(m, concrete, headers={"X-TRC-CSRF": tok})
            if resp.status_code != 401:
                bad.append((m, concrete, resp.status_code))
    assert bad == []


def test_signed_out_requests_get_no_tenant(c, app, monkeypatch):
    """A public path that reached tenant data would read nothing of the owner's: signed out, the
    request's tenant is the anonymous one (no caps, every data path raises)."""
    from backend.accounts import login as LG
    from backend.engine.planning import Plan
    with tenancy.use(tenancy.owner()):
        p = Plan.load()
        p.upsert_event({"name": "站主的比賽", "date": "2027-05-01", "priority": "A", "kind": "trail"})
        p.save()
    monkeypatch.setattr(LG, "PUBLIC_PATHS", LG.PUBLIC_PATHS | {"/api/v1/plan"})      # pretend it were public
    r = c.get("/api/v1/plan")
    assert r.status_code == 500 and "站主的比賽" not in r.text
    nobody = tenancy.anonymous()
    assert nobody.caps == frozenset() and not nobody.can("plan.write")
    for f in (lambda: nobody.root, lambda: nobody.shared, lambda: nobody.wko5_dir):
        with pytest.raises(tenancy.NoTenant):
            f()
    with tenancy.use(nobody):
        for f in (tenancy.db_path, lambda: tenancy.private_path("plan.json"), lambda: tenancy.shared_path("fit")):
            with pytest.raises(tenancy.NoTenant):
                f()


def test_only_a_real_cors_preflight_skips_the_gate(c):
    assert c.options("/api/v1/plan").status_code == 401
    r = c.options("/api/v1/plan", headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "PUT"})
    assert r.status_code == 200 and r.headers.get("access-control-allow-origin") == "http://localhost:8000"


@pytest.mark.parametrize("path", [
    "/api//v1/plan", "/api/v1/static/%2e%2e/plan", "/api/v1/static%2fx", "/api/v1/static/x%5cy", "/share/..%2fapi",
])
def test_odd_paths_are_refused_before_any_prefix_check(c, path):
    r = c.get(path)
    assert r.status_code == 400 and r.json()["code"] == "BAD_PATH", (path, r.status_code)


@pytest.mark.parametrize("path,raw,bad", [
    ("/api/v1/static/../plan", None, True), ("/api/v1/static/./x.js", None, True), ("/login/..", None, True),
    ("/api/v1/static/..", None, True), ("/api/v1/x\\y", None, True), ("/api//v1/plan", None, True),
    ("/api/v1/static/../plan", b"/api/v1/static/%2E%2E/plan", True), ("/api/v1/static/x", b"/api/v1/static%2Fx", True),
    ("/api/v1/static/shell.js", None, False), ("/api/v1/plan/page", None, False), ("/", None, False),
    ("/share/abc..def", None, False), ("/api/v1/static/i18n/i18n.js", None, False),
])
def test_bad_path(path, raw, bad):
    """The same check on the ASGI scope itself (an HTTP client normalizes dot segments before
    sending, a raw one need not)."""
    from backend.tenancy_mw import bad_path
    scope = {"path": path}
    if raw is not None:
        scope["raw_path"] = raw
    assert bad_path(scope) is bad


def test_a_malformed_cookie_never_drops_the_session(c):
    sign_up(c, "g-alice", "alice@example.com")
    token = c.cookies[SESSION]
    c.cookies.clear()
    r = c.get("/api/v1/plan", headers={"Cookie": f'junk; x="unterminated; {SESSION}={token}; =y'})
    assert r.status_code == 200


def test_signed_in_answers_are_not_cached(c):
    sign_up(c, "g-alice", "alice@example.com")
    assert c.get("/api/v1/plan").headers["cache-control"] == "private, no-store"
    assert c.get("/api/v1/static/shell.js").headers["cache-control"] == "no-cache"    # the static files keep theirs


# ---------------------------------------------------------------------------- signing up

def test_first_google_sign_in_asks_for_the_terms_then_makes_the_account(c, app):
    from backend import accounts
    from backend.accounts import store, terms
    r = sign_in(c, "g-alice", "alice@example.com", nxt="/api/v1/plan/page", name="Alice")
    assert r.status_code == 303 and location(r) == "/signup"
    assert SESSION not in c.cookies
    assert store.find_identity("google", "g-alice") is None and store.count_users() == 1   # nothing before consent
    info = c.get("/api/v1/account/signup").json()
    assert info["kind"] == "signup" and info["email"] == "alice@example.com" and info["name"] == "Alice"
    docs = info["terms"]["documents"]
    assert [d["id"] for d in docs] == ["terms", "privacy", "health", "third_party"]
    assert info["terms"]["version"] == terms.VERSION and info["terms"]["digest"] == terms.shown_digest()
    assert all(d["draft"] and "SP-60" in d["body"] and d["title"] for d in docs)
    assert "跨使用者" in next(d for d in docs if d["id"] == "privacy")["body"]     # bundled with the terms
    r = accept(c, accept=False)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
    r = c.post("/api/v1/account/signup", json={"accept": True, "version": terms.VERSION, "digest": terms.shown_digest()})
    assert r.status_code == 403 and r.json()["code"] == "CSRF"
    before = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    r = accept(c)
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/api/v1/plan/page"}
    assert c.cookies.get(SESSION) and not c.cookies.get(SIGNUP)
    u = store.find_identity("google", "g-alice")
    assert u.id == 2 and u.role == "user" and u.email == "alice@example.com" and u.google_sub == "g-alice"
    rows = store.consents(u.id)
    assert len(rows) == 1 and rows[0]["terms_version"] == terms.VERSION and rows[0]["analysis"] == 1
    assert dt.datetime.fromisoformat(rows[0]["accepted_at"]) >= before
    assert json.loads(rows[0]["documents"]) == terms.PINNED[terms.VERSION]          # the texts the page showed
    # its own tenant, made on first use; the owner's data is not there
    plan = c.get("/api/v1/plan")
    assert plan.status_code == 200 and plan.json()["events"] == []
    assert (accounts.server_root() / "users" / "2" / "wko5coach.db").exists()
    s = c.get("/api/v1/session").json()
    assert s["mode"] == "user" and s["user"]["id"] == 2 and s["login"] == {"required": True, "signed_in": True}
    assert "wko5" not in s["caps"] and s["membership"]["role"] == "user"
    # 首次設定: the first-run 精靈 (SP-211 sex / age / weight; SP-290 questionnaire) opens on the next page
    setup = c.get("/api/v1/plan/profile").json()["setup"]
    assert setup["needed"] and setup["remind"] and setup["survey_pending"]
    # the sign-up ticket was single use
    r = c.post("/api/v1/account/signup", json={"accept": True, "version": terms.VERSION, "digest": terms.shown_digest()},
               headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 404


def test_the_consent_must_be_for_the_texts_shown(c):
    from backend.accounts import store
    sign_in(c, "g-alice", "alice@example.com")
    r = accept(c, digest="0" * 64)                          # another set of texts than the page showed
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TERMS_CHANGED"
    assert store.find_identity("google", "g-alice") is None
    assert accept(c).json()["ok"]


def test_terms_digests_are_pinned_to_the_version():
    """A document edited without a new terms.VERSION (and its digests pinned in terms.PINNED)
    fails here: the users who accepted that version agreed to another text."""
    from backend.accounts import terms
    assert terms.digests() == terms.PINNED[terms.VERSION]
    assert set(terms.PINNED[terms.VERSION]) == {d.id for d in terms.DOCUMENTS}


def test_new_terms_ask_for_consent_again_at_the_next_sign_in(c, app, monkeypatch):
    from backend.accounts import store, terms
    sign_up(c, "g-bob", "bob@example.com")
    first = terms.VERSION
    assert c.post("/api/v1/account/logout", headers={"X-TRC-CSRF": csrf(c)}).status_code == 200
    new_terms(monkeypatch)
    r = sign_in(c, "g-bob", "bob@example.com")
    assert location(r) == "/signup" and SESSION not in c.cookies
    assert c.get("/api/v1/account/signup").json()["kind"] == "reconsent"
    r = accept(c, version=first)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TERMS_CHANGED"
    r = accept(c)
    assert r.status_code == 200 and r.json()["ok"]
    u = store.find_identity("google", "g-bob")
    assert [x["terms_version"] for x in store.consents(u.id)] == [first, "2099-01-01"]
    assert store.count_users() == 2                       # the same account, not a new one
    assert c.get("/api/v1/plan").status_code == 200


def test_new_terms_stop_a_signed_in_user_until_accepted(c, app, monkeypatch):
    """Already signed in when the terms change: the API answers 403 TERMS_REQUIRED, the pages
    go to the terms; accepted there (no new sign-in), everything opens again. The owner never."""
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    owner = browser(app)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    new_terms(monkeypatch)
    r = c.get("/api/v1/plan")
    assert r.status_code == 403 and r.json()["code"] == "TERMS_REQUIRED"
    r = c.get("/api/v1/plan/page", headers=HTML)
    assert r.status_code in (302, 303) and location(r) == "/signup?next=%2Fapi%2Fv1%2Fplan%2Fpage"
    assert c.get("/api/v1/session").status_code == 200                  # public paths still answer
    info = c.get("/api/v1/account/signup").json()
    assert info["kind"] == "reconsent" and info["email"] == "alice@example.com"
    r = accept(c, next="/api/v1/plan/page")
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/api/v1/plan/page"}
    assert c.get("/api/v1/plan").status_code == 200
    assert [x["terms_version"] for x in store.consents(2)][-1] == "2099-01-01"
    assert owner.get("/api/v1/plan").status_code == 200                  # the owner accepts no terms


def test_unverified_google_email_is_refused(c):
    from backend.accounts import store
    r = sign_in(c, "g-nv", "nv@example.com", verified=False)
    assert location(r) == "/login?error=unverified"
    assert SESSION not in c.cookies and SIGNUP not in c.cookies
    assert store.count_users() == 1 and store.waitlist() == []


def test_nobody_signs_up_before_the_owner_has_signed_in(started):
    """User 1 not linked yet (the owner's e-mail in the env may have a typo): a new Google
    account is refused (「網站還在設定中」) instead of becoming an empty account."""
    from backend.accounts import store
    b = browser(started)
    assert location(sign_in(b, "g-early", "early@example.com")) == "/login?error=setup"
    assert store.find_identity("google", "g-early") is None and store.count_users() == 1


# ---------------------------------------------------------------------------- the owner

def _owner_event():
    from backend.engine.planning import Plan
    with tenancy.use(tenancy.owner()):
        p = Plan.load()
        p.upsert_event({"name": "站主的比賽", "date": "2027-05-01", "priority": "A", "kind": "trail"})
        p.save()


def test_the_owner_signs_in_as_user_1_and_sees_the_old_data(started):
    from backend.accounts import store
    c = started.main
    _owner_event()
    r = sign_in(c, "g-owner", OWNER_EMAIL, nxt="/api/v1/plan/page")
    assert r.status_code == 303 and location(r) == "/api/v1/plan/page"       # no terms for the owner
    assert [e["name"] for e in c.get("/api/v1/plan").json()["events"]] == ["站主的比賽"]
    s = c.get("/api/v1/session").json()
    assert s["mode"] == "owner" and s["user"]["id"] == 1 and "wko5" in s["caps"]
    assert store.get_user(1).google_sub == "g-owner"
    # another Google account with the owner's e-mail (a re-created address) is someone new now
    other = browser(started)
    r = sign_in(other, "g-other", OWNER_EMAIL)
    assert location(r) == "/signup"
    assert store.get_user(1).google_sub == "g-owner"


def test_the_owner_pinned_by_google_sub(env, monkeypatch):
    from backend.accounts import store
    from backend.main import build_app
    monkeypatch.delenv("WKO5COACH_OWNER_EMAIL")
    monkeypatch.setenv("WKO5COACH_OWNER_GOOGLE_SUB", "g-owner-sub")
    started = build_app(demo=False)
    started.fake = env
    with _client(started):                       # the owner's identity is read at start-up
        pass
    _owner_event()
    imp = browser(started)
    assert location(sign_in(imp, "g-impostor", OWNER_EMAIL)) == "/login?error=setup"   # the e-mail alone: no
    own = browser(started)
    assert location(sign_in(own, "g-owner-sub", "renamed@example.com")) == "/"
    assert [e["name"] for e in own.get("/api/v1/plan").json()["events"]] == ["站主的比賽"]
    assert store.get_user(1).google_sub == "g-owner-sub"


def test_an_unverified_owner_email_never_takes_user_1(started):
    from backend.accounts import store
    assert location(sign_in(started.main, "g-x", OWNER_EMAIL, verified=False)) == "/login?error=unverified"
    assert store.get_user(1).google_sub is None


# ---------------------------------------------------------------------------- the callback's checks

def _tampered(cl, case: str):
    """Run a sign-in with one thing wrong; the callback's response."""
    from joserfc.jwk import RSAKey
    f = cl.fake
    if case == "unknown_state":
        cb = f.authorize(start(cl), "g-t", "t@example.com")
        return cl.get(cb.replace("state=", "state=x"))
    if case == "replayed":
        cb = f.authorize(start(cl), "g-t", "t@example.com")
        assert location(cl.get(cb)) == "/signup"
        cl.cookies.clear()
        return cl.get(cb)
    if case == "other_browser":
        cb = f.authorize(start(cl), "g-t", "t@example.com")
        other = browser(cl.trc_app)
        start(other)                                    # it has a binding cookie of its own
        return other.get(cb)
    if case == "no_binding_cookie":
        cb = f.authorize(start(cl), "g-t", "t@example.com")
        cl.cookies.delete(OAUTH)
        return cl.get(cb)
    if case == "pkce":
        # a code issued for another authorization request (another challenge) injected with this state
        url1, url2 = start(cl), start(cl)
        code1 = parse_qs(urlsplit(f.authorize(url1, "g-t", "t@example.com")).query)["code"][0]
        state2 = parse_qs(urlsplit(url2).query)["state"][0]
        return cl.get("/oauth/google/callback?" + urlencode({"state": state2, "code": code1}))
    if case == "nonce":
        return cl.get(f.authorize(start(cl), "g-t", "t@example.com", claims={"nonce": "not-the-one"}))
    if case == "no_nonce":
        return cl.get(f.authorize(start(cl), "g-t", "t@example.com", claims={"nonce": None}))
    if case == "signature":
        wrong = RSAKey.generate_key(2048, parameters={"kid": f.kid}, private=True)
        return cl.get(f.authorize(start(cl), "g-t", "t@example.com", sign_with=wrong))
    if case == "audience":
        return cl.get(f.authorize(start(cl), "g-t", "t@example.com", claims={"aud": "someone-else", "azp": "someone-else"}))
    if case == "issuer":
        return cl.get(f.authorize(start(cl), "g-t", "t@example.com", claims={"iss": "https://evil.example"}))
    if case == "expired":
        old = int(time.time()) - 7200
        return cl.get(f.authorize(start(cl), "g-t", "t@example.com", claims={"iat": old, "exp": old + 3600}))
    if case == "no_sub":
        return cl.get(f.authorize(start(cl), "g-t", "t@example.com", claims={"sub": None}))
    if case == "google_error":
        url = start(cl)
        state = parse_qs(urlsplit(url).query)["state"][0]
        return cl.get("/oauth/google/callback?" + urlencode({"state": state, "error": "access_denied"}))
    raise AssertionError(case)


@pytest.mark.parametrize("case,error", [
    ("unknown_state", "expired"), ("replayed", "expired"), ("other_browser", "failed"),
    ("no_binding_cookie", "failed"), ("pkce", "failed"), ("nonce", "failed"), ("no_nonce", "failed"),
    ("signature", "failed"), ("audience", "failed"), ("issuer", "failed"), ("expired", "failed"),
    ("no_sub", "failed"), ("google_error", "cancelled"),
])
def test_a_tampered_callback_is_refused(c, case, error):
    from backend.accounts import store
    r = _tampered(c, case)
    assert r.status_code in (302, 303) and location(r) == f"/login?error={error}", (case, location(r))
    assert not any(line.startswith(SESSION + "=") and "Max-Age=0" not in line for line in set_cookie_lines(r))
    assert not any(line.startswith(SIGNUP + "=") and "Max-Age=0" not in line for line in set_cookie_lines(r))
    assert store.find_identity("google", "g-t") is None
    assert c.get("/api/v1/plan").status_code == 401


def test_a_state_older_than_10_minutes_is_refused(c, monkeypatch):
    from backend.accounts import store
    cb = c.fake.authorize(start(c), "g-t", "t@example.com")
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=11)
    monkeypatch.setattr(store, "_now", lambda: later.replace(microsecond=0))
    assert location(c.get(cb)) == "/login?error=expired"


def test_states_and_tickets_are_taken_once(env, started):
    from backend.accounts import store
    store.put_oauth_state("s1", "b1", "google", "n", "v", PUBLIC_URL + "/cb", "/", 600)
    assert store.pop_oauth_state("s1")["nonce"] == "n" and store.pop_oauth_state("s1") is None
    store.put_signup_ticket("t1", "signup", "google", "sub", None, None, None, "/", 600)
    assert store.take_signup_ticket("t1")["subject"] == "sub" and store.take_signup_ticket("t1") is None


def test_id_token_checks(env):
    """google.verify_id_token directly: signature (the JWKS), iss, aud (and azp), exp, iat, nonce."""
    import asyncio
    from joserfc import jwt
    from joserfc.jwk import RSAKey
    from backend.accounts import google as G
    now = int(time.time())
    good = {"iss": "accounts.google.com", "aud": CLIENT_ID, "sub": "g1", "iat": now, "exp": now + 600, "nonce": "n1",
            "email": "a@example.com", "email_verified": True}

    def tok(claims, key=None, alg="RS256", kid=None):
        return jwt.encode({"alg": alg, "kid": kid or env.kid}, {k: v for k, v in claims.items() if v is not None},
                          key or env.key)

    def check(token, nonce="n1"):
        return asyncio.run(G.verify_id_token(token, nonce))
    G.configure(CLIENT_ID, CLIENT_SECRET, PUBLIC_URL)          # what decide() sets at start-up
    assert check(tok(good))["sub"] == "g1"
    assert check(tok({**good, "aud": ["other", CLIENT_ID], "azp": CLIENT_ID}))["sub"] == "g1"
    bad = [
        {**good, "aud": ["other", CLIENT_ID], "azp": "other"}, {**good, "aud": "other"}, {**good, "iss": "evil"},
        {**good, "exp": now - 120}, {**good, "exp": None}, {**good, "iat": now + 3600}, {**good, "nonce": "n2"},
        {**good, "nonce": "非 ASCII"}, {**good, "nonce": None}, {**good, "sub": ""}, {**good, "exp": True},
    ]
    for claims in bad:
        with pytest.raises(G.LoginFailed):
            check(tok(claims))
    with pytest.raises(G.LoginFailed):
        check(tok(good), nonce="")
    with pytest.raises(G.LoginFailed):                     # a key not in Google's JWKS
        check(tok(good, key=RSAKey.generate_key(2048, private=True)))
    from joserfc.jwk import OctKey
    with pytest.raises(G.LoginFailed):                     # alg confusion: HS256 with any key
        check(jwt.encode({"alg": "HS256"}, good, OctKey.generate_key(256)))
    with pytest.raises(G.LoginFailed):
        check("not.a.jwt")
    # Google rotated its key: an unknown kid fetches the JWKS once more
    env.kid, env.key = "fake-k2", RSAKey.generate_key(2048, parameters={"kid": "fake-k2"}, private=True)
    calls = env.jwks_calls
    assert check(tok(good))["sub"] == "g1" and env.jwks_calls == calls + 1


def test_google_is_reached_with_plain_httpx():
    """No private authlib module (its _compat shim picks httpx2 when installed) and not authlib's
    HTTP client: our own httpx client, the one the tests' transport plugs into."""
    import ast
    import inspect
    from backend.accounts import google as G
    mods = set()
    for node in ast.walk(ast.parse(inspect.getsource(G))):
        if isinstance(node, ast.ImportFrom):
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    assert "httpx" in mods and not any("_compat" in m or "httpx_client" in m or m == "httpx2" for m in mods), mods


# ---------------------------------------------------------------------------- sessions, CSRF, cookies

def test_the_session_cookie_and_its_row(c):
    from backend.accounts import store
    r = sign_in(c, "g-owner", OWNER_EMAIL)
    line = next(x for x in set_cookie_lines(r) if x.startswith(SESSION + "="))
    for attr in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", f"Max-Age={30 * 86400}"):
        assert attr in line, line
    assert "Domain" not in line
    token = c.cookies[SESSION]
    raw = store.path().read_bytes()
    assert token.encode() not in raw                                       # only its sha256 is stored
    with store.connect() as con:
        hashes = [x[0] for x in con.execute("SELECT token_hash FROM sessions")]
    assert hashes == [hashlib.sha256(token.encode()).hexdigest()]
    # every sign-in cookie carries the __Host- prefix; the CSRF one is readable by the page
    lines = set_cookie_lines(c.get("/api/v1/session"))
    assert any(x.startswith(CSRF + "=") and "HttpOnly" not in x and "Secure" in x for x in lines)


def test_https_public_url_keeps_cookies_secure_whatever_cookie_secure_says(env, monkeypatch):
    monkeypatch.setenv("WKO5COACH_COOKIE_SECURE", "0")
    from backend.accounts import login as LG
    assert LG.decide() == "signin" and LG.cookie_name("trc_session") == SESSION
    assert "Secure" in LG.cookie_attrs(60)


def test_local_http_development_uses_plain_cookie_names(env, monkeypatch):
    from backend.accounts import login as LG
    monkeypatch.setenv("WKO5COACH_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.delenv("WKO5COACH_TRUSTED_PROXIES")
    monkeypatch.setenv("WKO5COACH_COOKIE_SECURE", "0")
    assert LG.decide() == "signin"
    assert LG.cookie_name("trc_session") == "trc_session" and "Secure" not in LG.cookie_attrs(60)


def test_the_csrf_cookie_is_reissued_unless_it_looks_like_one(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        good = "A" * 32
        assert cl.get("/api/v1/session", headers={"Cookie": f"trc_csrf={good}"}).json()["csrf"] == good
        for bad in ("short", "x" * 200, "has space here..................", 'q"uote' + "a" * 30):
            cl.cookies.clear()
            got = cl.get("/api/v1/session", headers={"Cookie": f"trc_csrf={bad}"}).json()["csrf"]
            assert got != bad and len(got) >= 20


def test_logout_ends_the_session(c):
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    token = c.cookies[SESSION]
    assert c.get("/api/v1/plan").status_code == 200
    r = c.post("/api/v1/account/logout")                                   # no CSRF header: refused
    assert r.status_code == 403 and store.session_user(token) is not None
    r = c.post("/api/v1/account/logout", headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 200 and any(x.startswith(SESSION + "=;") and "Max-Age=0" in x for x in set_cookie_lines(r))
    assert store.session_user(token) is None                               # the row is gone
    c.cookies.clear()
    r = c.get("/api/v1/plan", headers={"Cookie": f"{SESSION}={token}"})   # the old cookie replayed
    assert r.status_code == 401
    assert any(x.startswith(SESSION + "=;") and "Max-Age=0" in x for x in set_cookie_lines(r))   # and cleared


def test_sessions_last_30_days_and_slide_while_used(c, monkeypatch):
    from backend.accounts import store
    t0 = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(store, "_now", lambda: t0)
    sign_up(c, "g-alice", "alice@example.com")
    idle = browser(c.trc_app)
    sign_in(idle, "g-alice", "alice@example.com")
    assert idle.get("/api/v1/plan").status_code == 200
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(days=29))
    r = c.get("/api/v1/plan")                                              # used on day 29: slides
    assert r.status_code == 200
    assert any(x.startswith(SESSION + "=") and f"Max-Age={30 * 86400}" in x for x in set_cookie_lines(r))
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(days=45))
    assert c.get("/api/v1/plan").status_code == 200                        # still alive (expires day 59)
    assert idle.get("/api/v1/plan").status_code == 401                     # unused for 30 days: gone


def test_a_session_ends_90_days_after_sign_in_however_much_it_is_used(c, monkeypatch):
    from backend.accounts import store
    t0 = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(store, "_now", lambda: t0)
    sign_up(c, "g-alice", "alice@example.com")
    for day in (20, 40, 60, 80):
        monkeypatch.setattr(store, "_now", lambda d=day: t0 + dt.timedelta(days=d))
        r = c.get("/api/v1/plan")
        assert r.status_code == 200, day
    line = next(x for x in set_cookie_lines(r) if x.startswith(SESSION + "="))
    assert f"Max-Age={10 * 86400}" in line                                 # day 80: 10 days left, not 30
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(days=89, hours=23))
    assert c.get("/api/v1/plan").status_code == 200
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(days=90, minutes=1))
    assert c.get("/api/v1/plan").status_code == 401


def test_each_sign_in_gets_a_new_token(c):
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    t1 = c.cookies[SESSION]
    sign_in(c, "g-alice", "alice@example.com")
    t2 = c.cookies[SESSION]
    assert t1 != t2 and store.session_user(t1) is None and store.session_user(t2).google_sub == "g-alice"


def test_signing_in_again_over_an_old_session_keeps_the_new_one(c, monkeypatch):
    """The gate's own cookie (clearing a dead one, renewing a slid one) never overrides the
    session cookie the sign-in itself just set — also when the old one dies or slides on the
    callback itself."""
    from backend.accounts import store
    t0 = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(store, "_now", lambda: t0)
    sign_up(c, "g-alice", "alice@example.com")
    old = c.cookies[SESSION]
    # the old session slides on the callback (started at 55 min, back at 61 min)
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(minutes=55))
    cb = c.fake.authorize(start(c), "g-alice", "alice@example.com")
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(minutes=61))
    r = c.get(cb)
    lines = [x for x in set_cookie_lines(r) if x.startswith(SESSION + "=")]
    assert len(lines) == 1 and old not in lines[0] and "Max-Age=0" not in lines[0], lines
    assert c.cookies[SESSION] != old and c.get("/api/v1/plan").status_code == 200
    # the old session ends (signed out elsewhere) between the start and the callback
    second = c.cookies[SESSION]
    cb = c.fake.authorize(start(c), "g-alice", "alice@example.com")
    store.delete_session(second)
    r = c.get(cb)
    lines = [x for x in set_cookie_lines(r) if x.startswith(SESSION + "=")]
    assert len(lines) == 1 and "Max-Age=0" not in lines[0], lines
    assert c.get("/api/v1/plan").status_code == 200


def test_csrf_blocks_cross_site_writes(c):
    sign_up(c, "g-alice", "alice@example.com")
    ev = {"name": "A 的比賽", "date": "2027-03-01", "priority": "A", "kind": "trail"}
    r = c.put("/api/v1/plan/events", json=ev)                             # a cross-site form: cookies, no header
    assert r.status_code == 403 and r.json()["code"] == "CSRF"
    assert c.put("/api/v1/plan/events", json=ev, headers={"X-TRC-CSRF": "forged"}).status_code == 403
    assert c.put("/api/v1/plan/events", json=ev, headers={"X-TRC-CSRF": "fälscht".encode("latin-1")}).status_code == 403   # not a 500
    assert c.get("/api/v1/plan").json()["events"] == []
    assert c.put("/api/v1/plan/events", json=ev, headers={"X-TRC-CSRF": csrf(c)}).status_code == 200
    assert [e["name"] for e in c.get("/api/v1/plan").json()["events"]] == ["A 的比賽"]


def test_two_users_never_see_each_other(c, app):
    sign_up(c, "g-alice", "alice@example.com")
    ev = {"name": "A 的比賽", "date": "2027-03-01", "priority": "A", "kind": "trail"}
    assert c.put("/api/v1/plan/events", json=ev, headers={"X-TRC-CSRF": csrf(c)}).status_code == 200
    b = browser(app)
    sign_up(b, "g-bob", "bob@example.com")
    assert b.get("/api/v1/plan").json()["events"] == []
    o = browser(app)
    sign_in(o, "g-owner", OWNER_EMAIL)
    assert o.get("/api/v1/plan").json()["events"] == []


# ---------------------------------------------------------------------------- limits

def test_the_11th_sign_in_from_one_ip_in_15_minutes_is_refused(app, monkeypatch):
    from backend.security import ratelimit as RL
    clock = [1000.0]
    monkeypatch.setattr(RL, "clock", lambda: clock[0])
    a = browser(app, ip="203.0.113.9")
    for _ in range(10):
        start(a)
    r = a.get("/api/v1/account/login")
    assert r.status_code in (302, 303) and location(r) == "/login?error=rate_limited"
    start(browser(app, ip="198.51.100.4"))                                # another IP is not affected
    clock[0] += 14 * 60
    assert location(a.get("/api/v1/account/login")) == "/login?error=rate_limited"
    clock[0] += 61                                                          # 15 minutes after the first ones
    start(a)


def test_forged_callbacks_use_no_ones_quota(app, monkeypatch):
    """A callback is counted only once its state checks out (this browser's, not expired):
    anyone hammering the callback with made-up states never uses up a real sign-in's quota."""
    from backend.accounts import login as LG
    from backend.security import ratelimit as RL
    a = browser(app, ip="203.0.113.9")
    for _ in range(30):
        assert location(a.get("/oauth/google/callback?state=x&code=y")) == "/login?error=expired"
    assert location(sign_in(a, "g-owner", OWNER_EMAIL)) == "/"
    # the callbacks that reach Google are limited per IP
    monkeypatch.setattr(LG, "START_LIMIT", RL.Window(limit=100, per=900))
    b = browser(app, ip="203.0.113.77")
    for _ in range(10):
        assert location(sign_in(b, "g-owner", OWNER_EMAIL)) == "/"
    assert location(sign_in(b, "g-owner", OWNER_EMAIL)) == "/login?error=rate_limited"


def test_an_ipv6_client_is_limited_by_its_64():
    from backend.security import ratelimit as RL
    assert RL.limit_key("2001:db8:1:2:aaaa::1") == RL.limit_key("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert RL.limit_key("2001:db8:1:3::1") != RL.limit_key("2001:db8:1:2::1")
    assert RL.limit_key("203.0.113.9") == "203.0.113.9" and RL.limit_key("testclient") == "testclient"


def test_when_full_new_people_go_to_the_waitlist(c, app, monkeypatch):
    from backend import accounts
    from backend.accounts import store
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "2")                         # the owner + one
    sign_up(c, "g-alice", "alice@example.com")
    carol = browser(app)
    r = sign_in(carol, "g-carol", "Carol@Example.com")
    assert location(r) == "/login?error=full"
    assert [w["email"] for w in store.waitlist()] == ["carol@example.com"]
    assert store.find_identity("google", "g-carol") is None and store.count_users() == 2
    assert not (accounts.server_root() / "users" / "3").exists()
    # people with an account still sign in
    again = browser(app)
    assert location(sign_in(again, "g-alice", "alice@example.com")) == "/"
    # full between the callback and the consent: waitlisted, no account
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "3")
    dave = browser(app)
    assert location(sign_in(dave, "g-dave", "dave@example.com")) == "/signup"
    monkeypatch.setenv("WKO5COACH_MAX_USERS", "2")
    r = accept(dave)
    assert r.status_code == 200 and r.json() == {"ok": False, "next": "/login?error=full"}
    assert store.find_identity("google", "g-dave") is None and SESSION not in dave.cookies
    assert "dave@example.com" in [w["email"] for w in store.waitlist()]


def test_the_cap_is_checked_in_the_same_transaction_as_the_insert(env, started):
    from backend.accounts import store
    assert store.create_user(email="a@example.com", google_sub="g-a", cap=2) is not None
    assert store.create_user(email="b@example.com", google_sub="g-b", cap=2) is None
    assert store.count_users() == 2


def test_a_disabled_account_cannot_sign_in(c):
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    uid = store.find_identity("google", "g-alice").id
    store.set_disabled(uid, True, "test")
    assert c.get("/api/v1/plan").status_code == 401                        # signed out everywhere
    r = sign_in(c, "g-alice", "alice@example.com")
    assert location(r) == "/login?error=disabled" and SESSION not in c.cookies


# ---------------------------------------------------------------------------- redirects, pages, logs

@pytest.mark.parametrize("raw,want", [
    (None, "/"), ("", "/"), ("/api/v1/plan/page", "/api/v1/plan/page"), ("/api/v1/plan/page?event=a&x=1", "/api/v1/plan/page?event=a&x=1"),
    ("https://evil.example/", "/"), ("http://testserver/x", "/"), ("//evil.example", "/"), ("///evil.example", "/"),
    ("/\\evil.example", "/"), ("\\\\evil.example", "/"), ("/\t/evil.example", "/"), ("/\n/x", "/"),
    ("javascript:alert(1)", "/"), ("evil.example", "/"), (" /x", "/"), ("/login", "/"), ("/login?next=/x", "/"),
    ("/signup", "/"), ("/oauth/google/callback?code=x", "/"), ("/api/v1/account/login", "/"), ("/" + "a" * 3000, "/"),
])
def test_safe_next_is_a_path_on_this_site(raw, want):
    from backend.accounts import login as LG
    assert LG.safe_next(raw) == want


def test_sign_in_never_redirects_off_site(c, app):
    r = sign_in(c, "g-owner", OWNER_EMAIL, nxt="https://evil.example/steal")
    assert location(r) == "/"
    r = c.get("/login", params={"next": "//evil.example"})
    assert r.status_code in (302, 303) and location(r) == "/"             # signed in: back to the app
    b = browser(app)
    sign_up(b, "g-alice", "alice@example.com", nxt="/\\evil.example")
    assert b.get("/api/v1/plan").status_code == 200


def test_the_inlined_session_never_closes_its_script(c):
    """A Google display name is the user's own text: inlined into every page's TRC_SESSION, no
    `<` survives (no </script> break-out)."""
    sign_up(c, "g-alice", "alice@example.com", name="</script><img src=x onerror=alert(1)>")
    html = c.get("/api/v1/plan/page").text
    tag = html[html.index("window.TRC_SESSION"):]
    tag = tag[:tag.index("</script>")]
    assert "<" not in tag and "\\u003c/script" in tag


def test_a_user_folder_is_never_the_server_root():
    from backend.accounts import store
    u = store.User(id=5, role="user", google_sub="g", email=None, display_name=None, tenant_dir="",
                   created_at="", last_login_at=None, disabled_at=None)
    with pytest.raises(ValueError):
        u.folder()


def test_tokens_emails_and_cookies_never_reach_the_log(c, app, caplog):
    from backend import applog
    caplog.set_level(logging.DEBUG)
    sign_up(c, "g-alice", "alice@example.com", name="Alice Wonder")
    _tampered(browser(app), "nonce")
    _tampered(browser(app), "signature")
    secrets_seen = [c.cookies[SESSION], c.cookies[CSRF], "alice@example.com", "Alice Wonder",
                    CLIENT_SECRET, "g-alice"]
    text = caplog.text + "".join(str(r.args) for r in caplog.records)
    for s in secrets_seen:
        assert s not in text, s
    masked = applog.mask_url("/oauth/google/callback?state=abcDEF123&code=4/0AbCdEf")
    assert "abcDEF123" not in masked and "4/0AbCdEf" not in masked


def test_the_login_page_texts_are_in_both_languages():
    from backend.i18n.pages import CATALOG_DIR
    zh = json.loads((CATALOG_DIR / "zh-TW" / "login.json").read_text("utf-8"))
    en = json.loads((CATALOG_DIR / "en" / "login.json").read_text("utf-8"))
    assert set(zh) == set(en)
    for code in ("expired", "failed", "unverified", "disabled", "full", "rate_limited", "cancelled", "setup"):
        assert f"error.{code}" in zh
