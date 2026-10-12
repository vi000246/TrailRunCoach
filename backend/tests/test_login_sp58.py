"""SP-58: Google sign-in (OIDC), sign-up with the terms, server-side sessions, the sign-in gate.

The Google side is a fake (FakeGoogle below, served through a MockTransport of authlib's HTTP library): it checks the
PKCE verifier against the challenge of the authorization request, the client's credentials and
the redirect URI like Google does, and signs the id_token with an RSA key of its own (a fake
JWKS). Nothing here reaches the network, the real ~/.wko5coach or a real Google account.

The safety gate (the owner must never be locked out by deploying this): sign-in is required only
with the multi-user switch (WKO5COACH_ACCOUNTS=1) AND a Google client (GOOGLE_CLIENT_ID +
GOOGLE_CLIENT_SECRET); otherwise every request is the owner, exactly as before. The demo
instance never asks."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from backend import tenancy

CLIENT_ID = "test-client.apps.googleusercontent.example"
CLIENT_SECRET = "test-client-secret-not-real"
OWNER_EMAIL = "owner@example.com"
HTML = {"Accept": "text/html,application/xhtml+xml"}


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
        assert set(q["scope"].split()) == {"openid", "email", "profile"}
        assert q["code_challenge_method"] == "S256" and q["code_challenge"] and q["nonce"] and q["state"]
        code = "4/" + secrets.token_urlsafe(16)
        self.codes[code] = {"challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"], "nonce": q["nonce"],
                            "sub": sub, "email": email, "email_verified": email_verified, "name": name,
                            "claims": claims or {}, "sign_with": sign_with}
        cb = urlsplit(q["redirect_uri"])
        return f"{cb.path}?{urlencode({'state': q['state'], 'code': code, 'scope': q['scope']})}"

    def handle(self, request):
        from backend.accounts import google as G
        R = G.HTTP.Response
        url = str(request.url)
        if url == G.JWKS_URL:
            self.jwks_calls += 1
            return R(200, json=self.jwks())
        if url == G.TOKEN_URL and request.method == "POST":
            self.token_calls += 1
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            auth = request.headers.get("authorization", "")
            want = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if auth != want and not (form.get("client_id") == CLIENT_ID and form.get("client_secret") == CLIENT_SECRET):
                return R(401, json={"error": "invalid_client"})
            entry = self.codes.pop(form.get("code", ""), None)
            if entry is None or form.get("grant_type") != "authorization_code":
                return R(400, json={"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not verifier or challenge != entry["challenge"]:
                return R(400, json={"error": "invalid_grant", "error_description": "code_verifier"})
            if form.get("redirect_uri") != entry["redirect_uri"]:
                return R(400, json={"error": "redirect_uri_mismatch"})
            from joserfc import jwt
            now = int(time.time())
            claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "azp": CLIENT_ID, "sub": entry["sub"],
                      "email": entry["email"], "email_verified": entry["email_verified"], "name": entry["name"],
                      "iat": now, "exp": now + 3600, "nonce": entry["nonce"]}
            claims.update(entry["claims"])
            claims = {k: v for k, v in claims.items() if v is not None}
            key = entry["sign_with"] or self.key
            id_token = jwt.encode({"alg": "RS256", "kid": self.kid}, claims, key)
            return R(200, json={"access_token": "ya29.fake-" + secrets.token_hex(8), "expires_in": 3599,
                                             "token_type": "Bearer", "scope": "openid email profile",
                                             "id_token": id_token})
        return R(404, json={"error": "not_found"})


# ---------------------------------------------------------------------------- fixtures

@pytest.fixture
def env(monkeypatch, tmp_path):
    """Multi-user mode with a (fake) Google client; the owner's Google account named by e-mail."""
    from backend import accounts
    from backend.accounts import google as G, login as LG, store, tenants as TN
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "owner"))       # the owner's folder, this test's own
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    monkeypatch.setenv(accounts.ENV_ENABLED, "1")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv(LG.ENV_OWNER_EMAIL, OWNER_EMAIL)
    for v in (LG.ENV_OWNER_SUB, LG.ENV_MAX_USERS, LG.ENV_PUBLIC_URL, "WKO5COACH_MODE", "WKO5_ATHLETE_DIR",
              "WKO5COACH_ATHLETE_DIR", "WKO5COACH_COOKIE_SECURE", "WKO5COACH_COOKIE_SAMESITE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    from backend.api import plan as plan_api
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()
    LG.reset_limits()
    fake = FakeGoogle()
    monkeypatch.setattr(G, "TRANSPORT", G.HTTP.MockTransport(fake.handle))
    G.forget_keys()
    yield fake
    LG.reset_limits()
    G.forget_keys()
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()


def _client(app, ip: str = "203.0.113.10"):
    from fastapi.testclient import TestClient
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False,
                      follow_redirects=False, client=(ip, 40000))


@pytest.fixture
def app(env):
    """The app started once (lifespan: the owner's DB, accounts.db with user 1), with its first
    browser `c` open for the whole test (one event loop for the app's DB connections)."""
    from backend.main import build_app
    a = build_app(demo=False)
    a.fake = env
    with _client(a) as main:
        main.fake, main.trc_app = env, a
        a.main = main
        yield a


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


def sign_up(cl, sub: str, email: str, nxt: str = "/", name: str = "跑者") -> dict:
    from backend.accounts import terms
    r = sign_in(cl, sub, email, nxt, name=name)
    assert r.status_code == 303 and r.headers["location"] == "/signup", (r.status_code, r.headers.get("location"))
    r = cl.post("/api/v1/account/signup", json={"accept": True, "version": terms.VERSION},
                headers={"X-TRC-CSRF": csrf(cl)})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    return r.json()


def location(r) -> str:
    return r.headers.get("location", "")


def set_cookie_lines(r) -> list[str]:
    return r.headers.get_list("set-cookie")


# ---------------------------------------------------------------------------- the safety gate

@pytest.mark.parametrize("on", [
    {},                                                           # today's self-hosted app
    {"GOOGLE_CLIENT_ID": CLIENT_ID, "GOOGLE_CLIENT_SECRET": CLIENT_SECRET},      # Google only
    {"WKO5COACH_ACCOUNTS": "1"},                                  # multi-user, no Google client yet
    {"WKO5COACH_ACCOUNTS": "1", "GOOGLE_CLIENT_ID": CLIENT_ID},  # half a Google client
])
def test_without_both_switches_nothing_changes(on, monkeypatch, tmp_path):
    """The deploy can't lock the owner out: sign-in is required only with the multi-user switch
    AND a whole Google client. Otherwise every request is the owner as before: no 401, no CSRF
    header needed, no sign-in pages."""
    from backend import accounts
    from backend.accounts import login as LG, store, tenants as TN
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "owner"))
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    for v in ("WKO5COACH_ACCOUNTS", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_ID_FILE",
              "GOOGLE_CLIENT_SECRET_FILE", "WKO5COACH_MODE", LG.ENV_OWNER_EMAIL, LG.ENV_OWNER_SUB):
        monkeypatch.delenv(v, raising=False)
    for k, v in on.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    assert LG.required() is False
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        assert cl.get("/api/v1/plan").status_code == 200
        ev = {"name": "沒登入也能改", "date": "2027-03-01", "priority": "A", "kind": "trail"}
        assert cl.put("/api/v1/plan/events", json=ev).status_code == 200          # no CSRF header, as before
        assert cl.get("/api/v1/overview/page", headers=HTML).status_code == 200
        s = cl.get("/api/v1/session").json()
        assert s["mode"] == "owner" and s["user"] is None and "login" not in s
        for p in ("/login", "/signup", "/api/v1/account/login", "/oauth/google/callback", "/api/v1/account/signup"):
            assert cl.get(p).status_code == 404, p
        assert cl.get("/healthz").status_code == 200


def test_login_required_needs_both_switches(monkeypatch):
    from backend.accounts import login as LG
    for v in ("WKO5COACH_ACCOUNTS", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_ID_FILE",
              "GOOGLE_CLIENT_SECRET_FILE", "WKO5COACH_MODE"):
        monkeypatch.delenv(v, raising=False)
    assert not LG.required()
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    assert not LG.required()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    assert not LG.required()
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    assert LG.required()
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "0")
    assert not LG.required()
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    monkeypatch.setenv("WKO5COACH_MODE", "demo")                 # the demo instance: never
    assert not LG.required()


def test_the_demo_instance_never_asks_to_sign_in(monkeypatch, tmp_path):
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    monkeypatch.setenv("WKO5COACH_ACCOUNTS", "1")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        s = cl.get("/api/v1/session").json()
        assert s["mode"] == "demo" and "login" not in s
        assert cl.get("/api/v1/plan").status_code == 200
        assert cl.get("/api/v1/overview/page", headers=HTML).status_code == 200
        assert cl.get("/login").status_code == 404 and cl.get("/api/v1/account/login").status_code == 404


def test_sign_in_mode_refuses_to_start_without_the_owners_google_account(env, monkeypatch):
    """No way to tell which Google account is the owner's (user 1 not linked yet, no
    WKO5COACH_OWNER_EMAIL / _GOOGLE_SUB): the app stops at start-up with the reason, instead of
    coming up with the owner's data unreachable."""
    from backend.accounts import login as LG, store
    from backend.main import build_app
    monkeypatch.delenv(LG.ENV_OWNER_EMAIL, raising=False)
    with pytest.raises(LG.LoginConfigError):
        with _client(build_app(demo=False)):
            pass
    store.migrate_owner()
    assert store.claim_owner_google("g-owner", OWNER_EMAIL)
    with _client(build_app(demo=False)) as cl:               # linked once: the env is no longer needed
        assert cl.get("/api/v1/plan").status_code == 401


# ---------------------------------------------------------------------------- the gate

def test_signed_out_only_the_public_paths_open(c):
    r = c.get("/api/v1/plan")
    assert r.status_code == 401 and r.json()["code"] == "LOGIN_REQUIRED"
    r = c.get("/api/v1/overview/page", headers=HTML)
    assert r.status_code in (302, 303) and location(r) == "/login?next=%2Fapi%2Fv1%2Foverview%2Fpage"
    r = c.get("/api/v1/plan/page?event=x", headers=HTML)
    assert location(r) == "/login?next=%2Fapi%2Fv1%2Fplan%2Fpage%3Fevent%3Dx"
    assert location(c.get("/", headers=HTML)) == "/login?next=%2F"
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


# ---------------------------------------------------------------------------- signing up

def test_first_google_sign_in_asks_for_the_terms_then_makes_the_account(c, app):
    from backend import accounts
    from backend.accounts import store, terms
    r = sign_in(c, "g-alice", "alice@example.com", nxt="/api/v1/plan/page", name="Alice")
    assert r.status_code == 303 and location(r) == "/signup"
    assert "trc_session" not in c.cookies
    assert store.find_identity("google", "g-alice") is None and store.count_users() == 1   # nothing before consent
    info = c.get("/api/v1/account/signup").json()
    assert info["kind"] == "signup" and info["email"] == "alice@example.com" and info["name"] == "Alice"
    docs = info["terms"]["documents"]
    assert [d["id"] for d in docs] == ["terms", "privacy", "health", "third_party"]
    assert info["terms"]["version"] == terms.VERSION
    assert all(d["draft"] and "SP-60" in d["body"] and d["title"] for d in docs)
    assert "跨使用者" in next(d for d in docs if d["id"] == "privacy")["body"]     # bundled with the terms
    tok = csrf(c)
    r = c.post("/api/v1/account/signup", json={"accept": False, "version": terms.VERSION}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
    r = c.post("/api/v1/account/signup", json={"accept": True, "version": terms.VERSION})
    assert r.status_code == 403 and r.json()["code"] == "CSRF"
    before = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    r = c.post("/api/v1/account/signup", json={"accept": True, "version": terms.VERSION}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 200 and r.json() == {"ok": True, "next": "/api/v1/plan/page"}
    assert c.cookies.get("trc_session") and not c.cookies.get("trc_signup")
    u = store.find_identity("google", "g-alice")
    assert u.id == 2 and u.role == "user" and u.email == "alice@example.com" and u.google_sub == "g-alice"
    rows = store.consents(u.id)
    assert len(rows) == 1 and rows[0]["terms_version"] == terms.VERSION and rows[0]["analysis"] == 1
    assert dt.datetime.fromisoformat(rows[0]["accepted_at"]) >= before
    assert set(json.loads(rows[0]["documents"])) == {"terms", "privacy", "health", "third_party"}
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
    r = c.post("/api/v1/account/signup", json={"accept": True, "version": terms.VERSION}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 404


def test_new_terms_ask_for_consent_again(c, app, monkeypatch):
    from backend.accounts import store, terms
    sign_up(c, "g-bob", "bob@example.com")
    first = terms.VERSION
    assert c.post("/api/v1/account/logout", headers={"X-TRC-CSRF": csrf(c)}).status_code == 200
    monkeypatch.setattr(terms, "VERSION", "2099-01-01")
    r = sign_in(c, "g-bob", "bob@example.com")
    assert location(r) == "/signup" and "trc_session" not in c.cookies
    assert c.get("/api/v1/account/signup").json()["kind"] == "reconsent"
    tok = csrf(c)
    r = c.post("/api/v1/account/signup", json={"accept": True, "version": first}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TERMS_CHANGED"
    r = c.post("/api/v1/account/signup", json={"accept": True, "version": "2099-01-01"}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 200 and r.json()["ok"]
    u = store.find_identity("google", "g-bob")
    assert [x["terms_version"] for x in store.consents(u.id)] == [first, "2099-01-01"]
    assert store.count_users() == 2                       # the same account, not a new one
    assert c.get("/api/v1/plan").status_code == 200


def test_unverified_google_email_is_refused(c):
    from backend.accounts import store
    r = sign_in(c, "g-nv", "nv@example.com", verified=False)
    assert location(r) == "/login?error=unverified"
    assert "trc_session" not in c.cookies and "trc_signup" not in c.cookies
    assert store.count_users() == 1 and store.waitlist() == []


# ---------------------------------------------------------------------------- the owner

def _owner_event():
    from backend.engine.planning import Plan
    with tenancy.use(tenancy.owner()):
        p = Plan.load()
        p.upsert_event({"name": "站主的比賽", "date": "2027-05-01", "priority": "A", "kind": "trail"})
        p.save()


def test_the_owner_signs_in_as_user_1_and_sees_the_old_data(c, app):
    from backend.accounts import store
    _owner_event()
    r = sign_in(c, "g-owner", OWNER_EMAIL, nxt="/api/v1/plan/page")
    assert r.status_code == 303 and location(r) == "/api/v1/plan/page"       # no terms for the owner
    assert [e["name"] for e in c.get("/api/v1/plan").json()["events"]] == ["站主的比賽"]
    s = c.get("/api/v1/session").json()
    assert s["mode"] == "owner" and s["user"]["id"] == 1 and "wko5" in s["caps"]
    assert store.get_user(1).google_sub == "g-owner"
    # another Google account with the owner's e-mail (a re-created address) is someone new now
    other = browser(app)
    r = sign_in(other, "g-other", OWNER_EMAIL)
    assert location(r) == "/signup"
    assert store.get_user(1).google_sub == "g-owner"


def test_the_owner_pinned_by_google_sub(env, app, monkeypatch):
    from backend.accounts import login as LG, store
    monkeypatch.delenv(LG.ENV_OWNER_EMAIL, raising=False)
    monkeypatch.setenv(LG.ENV_OWNER_SUB, "g-owner-sub")
    _owner_event()
    imp = browser(app)
    assert location(sign_in(imp, "g-impostor", OWNER_EMAIL)) == "/signup"     # the e-mail alone is not enough
    own = browser(app)
    assert location(sign_in(own, "g-owner-sub", "renamed@example.com")) == "/"
    assert [e["name"] for e in own.get("/api/v1/plan").json()["events"]] == ["站主的比賽"]
    assert store.get_user(1).google_sub == "g-owner-sub"


def test_an_unverified_owner_email_never_takes_user_1(c):
    from backend.accounts import store
    assert location(sign_in(c, "g-x", OWNER_EMAIL, verified=False)) == "/login?error=unverified"
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
        cl.cookies.delete("trc_oauth")
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
    assert not any(line.startswith("trc_session=") and "Max-Age=0" not in line for line in set_cookie_lines(r))
    assert not any(line.startswith("trc_signup=") and "Max-Age=0" not in line for line in set_cookie_lines(r))
    assert store.find_identity("google", "g-t") is None
    assert c.get("/api/v1/plan").status_code == 401


def test_a_state_older_than_10_minutes_is_refused(c, monkeypatch):
    from backend.accounts import store
    cb = c.fake.authorize(start(c), "g-t", "t@example.com")
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=11)
    monkeypatch.setattr(store, "_now", lambda: later.replace(microsecond=0))
    assert location(c.get(cb)) == "/login?error=expired"


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


# ---------------------------------------------------------------------------- sessions, CSRF

def test_the_session_cookie_and_its_row(c):
    from backend.accounts import store
    r = sign_in(c, "g-owner", OWNER_EMAIL)
    line = next(x for x in set_cookie_lines(r) if x.startswith("trc_session="))
    for attr in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", f"Max-Age={30 * 86400}"):
        assert attr in line, line
    token = c.cookies["trc_session"]
    raw = store.path().read_bytes()
    assert token.encode() not in raw                                       # only its sha256 is stored
    with store.connect() as con:
        hashes = [x[0] for x in con.execute("SELECT token_hash FROM sessions")]
    assert hashes == [hashlib.sha256(token.encode()).hexdigest()]


def test_logout_ends_the_session(c):
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    token = c.cookies["trc_session"]
    assert c.get("/api/v1/plan").status_code == 200
    r = c.post("/api/v1/account/logout")                                   # no CSRF header: refused
    assert r.status_code == 403 and store.session_user(token) is not None
    r = c.post("/api/v1/account/logout", headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 200 and any(x.startswith("trc_session=;") and "Max-Age=0" in x for x in set_cookie_lines(r))
    assert store.session_user(token) is None                               # the row is gone
    c.cookies.set("trc_session", token, domain="testserver")               # the old cookie replayed
    assert c.get("/api/v1/plan").status_code == 401


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
    assert any(x.startswith("trc_session=") and f"Max-Age={30 * 86400}" in x for x in set_cookie_lines(r))
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(days=45))
    assert c.get("/api/v1/plan").status_code == 200                        # still alive (expires day 59)
    assert idle.get("/api/v1/plan").status_code == 401                     # unused for 30 days: gone


def test_each_sign_in_gets_a_new_token(c):
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    t1 = c.cookies["trc_session"]
    sign_in(c, "g-alice", "alice@example.com")
    t2 = c.cookies["trc_session"]
    assert t1 != t2 and store.session_user(t1) is None and store.session_user(t2).google_sub == "g-alice"


def test_signing_in_again_over_an_old_session_keeps_the_new_one(c, monkeypatch):
    """The gate's own cookie (clearing a dead one, renewing a slid one) never overrides the
    session cookie the sign-in itself just set — also when the old one dies or slides on the
    callback itself."""
    from backend.accounts import store
    t0 = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(store, "_now", lambda: t0)
    sign_up(c, "g-alice", "alice@example.com")
    old = c.cookies["trc_session"]
    # the old session slides on the callback (started at 55 min, back at 61 min)
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(minutes=55))
    cb = c.fake.authorize(start(c), "g-alice", "alice@example.com")
    monkeypatch.setattr(store, "_now", lambda: t0 + dt.timedelta(minutes=61))
    r = c.get(cb)
    lines = [x for x in set_cookie_lines(r) if x.startswith("trc_session=")]
    assert len(lines) == 1 and old not in lines[0] and "Max-Age=0" not in lines[0], lines
    assert c.cookies["trc_session"] != old and c.get("/api/v1/plan").status_code == 200
    # the old session ends (signed out elsewhere) between the start and the callback
    second = c.cookies["trc_session"]
    cb = c.fake.authorize(start(c), "g-alice", "alice@example.com")
    store.delete_session(second)
    r = c.get(cb)
    lines = [x for x in set_cookie_lines(r) if x.startswith("trc_session=")]
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
    # the callback has its own limit (forged codes hammered at it)
    for _ in range(10):
        assert location(a.get("/oauth/google/callback?state=x&code=y")) == "/login?error=expired"
    assert location(a.get("/oauth/google/callback?state=x&code=y")) == "/login?error=rate_limited"


def test_when_full_new_people_go_to_the_waitlist(c, app, monkeypatch):
    from backend import accounts
    from backend.accounts import login as LG, store
    monkeypatch.setenv(LG.ENV_MAX_USERS, "2")                              # the owner + one
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
    monkeypatch.setenv(LG.ENV_MAX_USERS, "3")
    dave = browser(app)
    assert location(sign_in(dave, "g-dave", "dave@example.com")) == "/signup"
    monkeypatch.setenv(LG.ENV_MAX_USERS, "2")
    from backend.accounts import terms
    r = dave.post("/api/v1/account/signup", json={"accept": True, "version": terms.VERSION},
                  headers={"X-TRC-CSRF": csrf(dave)})
    assert r.status_code == 200 and r.json() == {"ok": False, "next": "/login?error=full"}
    assert store.find_identity("google", "g-dave") is None and "trc_session" not in dave.cookies
    assert "dave@example.com" in [w["email"] for w in store.waitlist()]


def test_a_disabled_account_cannot_sign_in(c):
    from backend.accounts import store
    sign_up(c, "g-alice", "alice@example.com")
    uid = store.find_identity("google", "g-alice").id
    store.set_disabled(uid, True, "test")
    assert c.get("/api/v1/plan").status_code == 401                        # signed out everywhere
    r = sign_in(c, "g-alice", "alice@example.com")
    assert location(r) == "/login?error=disabled" and "trc_session" not in c.cookies


# ---------------------------------------------------------------------------- redirects, logs

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


def test_tokens_emails_and_cookies_never_reach_the_log(c, app, caplog):
    from backend import applog
    caplog.set_level(logging.DEBUG)
    sign_up(c, "g-alice", "alice@example.com", name="Alice Wonder")
    _tampered(browser(app), "nonce")
    _tampered(browser(app), "signature")
    secrets_seen = [c.cookies["trc_session"], c.cookies["trc_csrf"], "alice@example.com", "Alice Wonder",
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
    for code in ("expired", "failed", "unverified", "disabled", "full", "rate_limited", "cancelled"):
        assert f"error.{code}" in zh
