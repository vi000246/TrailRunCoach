"""
Debug API authentication (SP-371, backend/debug_auth.py): off → 404; Bearer token only (no cookie);
the PIN from the environment (constant-time, 5 wrong → 15-minute lock, never in the repo); only the
token's hash stored; scopes → 403; expiry / revoke → 401; tenant-bound; rate limits → 429; an audit
row for every call. Synthetic data, in-memory DB (debug_fixtures.debug_env); the two-tenant case
uses two tenant folders in tmp_path.
"""
import ast
import datetime as dt
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update

from backend import debug_auth as DA
from backend.db.models import DebugAudit, DebugToken
from backend.security import ratelimit as RL
from backend.tests.debug_fixtures import (ADMIN, ALL_SCOPES, DBG, ENDPOINTS, TEST_PIN, bearer, debug_env, enable,
                                          make_token)
from backend.tests.test_plan_store import run

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- 404 / 401
def test_off_answers_404_everywhere_then_401_without_a_good_token(monkeypatch, tmp_path):
    monkeypatch.setattr(DA, "FAIL_BUCKET", RL.Buckets(rate=100, per=600))     # no IP block in this test
    with debug_env(monkeypatch, tmp_path) as e:
        tok = make_token(e, ALL_SCOPES)                  # a token made while off: still 404 everywhere
        for p in ENDPOINTS + ("/nope",):
            r = e.c.get(DBG + p, headers=bearer(tok))
            assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, p
        assert enable(e).json()["enabled"] is True
        for p in ENDPOINTS:
            assert e.c.get(DBG + p).status_code == 401, p                                   # no token
            assert e.c.get(DBG + p, headers=bearer("trcd_" + "x" * 43)).status_code == 401, p  # unknown
            assert e.c.get(DBG + p, headers={"Authorization": "Basic abc"}).status_code == 401
        assert e.c.get(DBG + "/sync", headers=bearer(tok)).status_code == 200
        # expired
        run(e.db.execute(update(DebugToken).values(expires_at=dt.datetime.utcnow() - dt.timedelta(minutes=1))))
        run(e.db.commit())
        r = e.c.get(DBG + "/sync", headers=bearer(tok))
        assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_EXPIRED"
        # revoked (one, then all)
        t2, t3 = make_token(e, name="t2"), make_token(e, name="t3")
        ids = {t["name"]: t["id"] for t in e.c.get(ADMIN).json()["tokens"]}
        assert e.c.get(DBG + "/sync", headers=bearer(t2)).status_code == 200
        assert e.c.delete(f"{ADMIN}/tokens/{ids['t2']}").json()["revoked"] == 1
        r = e.c.get(DBG + "/sync", headers=bearer(t2))
        assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_REVOKED"
        assert e.c.get(DBG + "/sync", headers=bearer(t3)).status_code == 200
        assert e.c.post(f"{ADMIN}/tokens/revoke-all").json()["revoked"] == 2      # t3 and the expired one
        assert e.c.get(DBG + "/sync", headers=bearer(t3)).status_code == 401
        # turned off again: 404, not 401
        enable(e, False)
        assert e.c.get(DBG + "/sync", headers=bearer(t3)).status_code == 404


def test_the_web_session_cookie_alone_is_401(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ALL_SCOPES)
        # whatever the browser holds as cookies — even the token itself in a cookie — is never read
        e.c.cookies.set("trc_demo", "x")
        e.c.cookies.set("Authorization", f"Bearer {tok}")
        e.c.cookies.set("token", tok)
        for p in ENDPOINTS:
            assert e.c.get(DBG + p).status_code == 401, p
        assert e.c.get(DBG + "/sync", headers=bearer(tok)).status_code == 200


def test_the_demo_never_answers(monkeypatch, tmp_path):
    # backend.main builds its module-level `app` from WKO5COACH_MODE on first import: import it in
    # owner mode first, so a later `from backend.main import app` is not a demo app
    from backend.main import build_app
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ALL_SCOPES)
        monkeypatch.setenv("WKO5COACH_MODE", "demo")
        assert e.c.get(DBG + "/sync", headers=bearer(tok)).status_code == 404
        monkeypatch.delenv("WKO5COACH_MODE")
    # and the demo app does not mount the routes at all

    def paths(app):
        stack, out = list(app.routes), set()
        while stack:
            r = stack.pop(0)
            sub = getattr(r, "original_router", None)
            if sub is not None:
                stack.extend(sub.routes)
            else:
                out.add(getattr(r, "path", ""))
        return out
    assert not any(p.startswith((DBG, ADMIN)) for p in paths(build_app(demo=True)))
    assert {DBG + "/day", ADMIN} <= paths(build_app(demo=False))


# ---------------------------------------------------------------- scopes / tenants
def test_a_token_without_export_config_gets_403(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e)                                       # the default scopes: no export:config
        assert "export:config" not in DA.DEFAULT_SCOPES
        r = e.c.get(DBG + "/export/config", headers=bearer(tok))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "SCOPE"
        only_sync = make_token(e, ["read:sync"])
        assert e.c.get(DBG + "/sync", headers=bearer(only_sync)).status_code == 200
        for p in ("/activity?date=2026-10-01", "/plan", "/day?date=2026-10-01", "/thresholds", "/export/config"):
            assert e.c.get(DBG + p, headers=bearer(only_sync)).status_code == 403, p
        full = make_token(e, ALL_SCOPES)
        assert e.c.get(DBG + "/export/config", headers=bearer(full)).status_code == 200
        assert e.c.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN, "scopes": ["write:plan"]}).status_code == 400
        assert e.c.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN, "days": 365}).status_code == 400


def _tenant_app(monkeypatch, home: Path, as_user: str | None = None):
    """The real app on a tenant folder (its own DB file); `as_user`: every request runs as a
    user tenant of that id on the same folder. Returns a TestClient context."""
    from fastapi.testclient import TestClient
    from backend import tenancy
    from backend.main import build_app
    monkeypatch.setenv("WKO5COACH_HOME", str(home))
    app = build_app(demo=False)
    if as_user:
        class AsUser:
            def __init__(self, inner):
                self.inner = inner

            async def __call__(self, scope, receive, send):
                tok = tenancy.set_current(tenancy.Tenant(id=as_user, kind=tenancy.USER, root=home, shared=home))
                try:
                    await self.inner(scope, receive, send)
                finally:
                    tenancy.reset(tok)
        app.add_middleware(AsUser)
    return TestClient(app)


def test_tokens_are_bound_to_their_tenant(monkeypatch, tmp_path):
    from backend.api import debug as debug_api
    from backend.db import database
    from backend.engine.planning import Plan
    from backend.tests.debug_fixtures import no_dataset
    monkeypatch.setenv(DA.PIN_ENV, TEST_PIN)
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setattr(debug_api, "_dataset", no_dataset)
    DA.reset_limits()
    homes = {"A": tmp_path / "a", "B": tmp_path / "b"}
    toks = {}
    for name, home in homes.items():
        home.mkdir()
        Plan(profile={"height_cm": 170 if name == "A" else 160}).save(home / "plan.json")
        with _tenant_app(monkeypatch, home) as c:
            assert c.put(ADMIN, json={"enabled": True}).status_code == 200
            r = c.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN, "scopes": ALL_SCOPES, "name": f"tok-{name}"})
            toks[name] = r.json()["token"]
            c.portal.call(database.dispose, home / "wko5coach.db")
    # each token reads its own tenant only
    for name, home in homes.items():
        other = "B" if name == "A" else "A"
        with _tenant_app(monkeypatch, home) as c:
            r = c.get(DBG + "/export/config", headers=bearer(toks[name]))
            assert r.status_code == 200 and r.json()["meta"]["caller"] == f"tok-{name}"
            assert r.json()["blocks"]["profile"]["plan_profile"]["height_cm"] == (170 if name == "A" else 160)
            assert c.get(DBG + "/export/config", headers=bearer(toks[other])).status_code == 401
            c.portal.call(database.dispose, home / "wko5coach.db")
    # a token row of another tenant id in this DB (a copied DB file, a shared one later) is refused too
    with _tenant_app(monkeypatch, homes["A"], as_user="u2") as c:
        r = c.get(DBG + "/export/config", headers=bearer(toks["A"]))
        assert r.status_code == 401 and r.json()["detail"]["code"] == "TOKEN_INVALID"
        c.portal.call(database.dispose, homes["A"] / "wko5coach.db")


# ---------------------------------------------------------------- storage
def test_only_the_hash_of_a_token_is_stored(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        r = e.c.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN, "name": "claude"})
        body = r.json()
        tok = body["token"]
        assert tok.startswith(DA.TOKEN_PREFIX) and len(tok) > 40
        e.c.get(DBG + "/sync", headers=bearer(tok))               # used once: audit + last_used rows too
        listed = e.c.get(ADMIN).json()
        assert tok not in str(listed) and tok[len(DA.TOKEN_PREFIX):] not in str(listed)
        cells = []
        for (name,) in run(e.db.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all():
            for row in run(e.db.execute(text(f'SELECT * FROM "{name}"'))).all():
                cells += [str(v) for v in row]
        blob = "\n".join(cells)
        assert tok not in blob and tok[len(DA.TOKEN_PREFIX):] not in blob
        assert DA.hash_token(tok) in blob                          # the hash is what is kept
        row = run(e.db.execute(select(DebugToken))).scalars().one()
        assert row.token_hash == DA.hash_token(tok) and row.prefix == tok[:len(DA.TOKEN_PREFIX) + 4]


# ---------------------------------------------------------------- PIN
def test_no_pin_in_the_environment_means_no_debug(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path, pin=None) as e:
        st = e.c.get(ADMIN).json()
        assert st["pin_configured"] is False and st["enabled"] is False
        r = enable(e)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "NO_PIN"
        r = e.c.post(f"{ADMIN}/tokens", json={"pin": "anything-at-all"})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "NO_PIN"
        # even with the switch stored on (an old row), nothing answers without a PIN
        from backend.settings.repository import SettingsRepository
        run(SettingsRepository(e.db).set(DA.ENABLED_KEY, True))
        run(e.db.commit())
        assert e.c.get(DBG + "/sync").status_code == 404
    with debug_env(monkeypatch, tmp_path, pin="12345") as e:            # shorter than PIN_MIN_LEN: not set
        assert e.c.get(ADMIN).json()["pin_configured"] is False


def test_a_wrong_pin_gets_no_token_and_five_lock_it_for_15_minutes(monkeypatch, tmp_path):
    now = [1000.0]
    monkeypatch.setattr(RL, "clock", lambda: now[0])
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        for i in range(DA.PIN_MAX_FAILS - 1):
            r = e.c.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN + "x"})
            assert r.status_code == 403 and r.json()["detail"]["code"] == "PIN_WRONG" and "token" not in r.json()
        r = e.c.post(f"{ADMIN}/tokens", json={"pin": "nope"})                  # the 5th
        assert r.status_code == 429 and r.json()["detail"]["code"] == "PIN_LOCKED"
        assert run(e.db.execute(select(func.count(DebugToken.id)))).scalar_one() == 0
        now[0] += DA.PIN_LOCK_S - 5                                          # 14 m 55 s later: the right PIN too
        r = e.c.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN})
        assert r.status_code == 429 and "token" not in r.json()
        assert e.c.get(ADMIN).json()["pin_locked_s"] > 0
        now[0] += 10                                                          # the lock ran out
        assert e.c.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN}).status_code == 200
        assert e.c.get(ADMIN).json()["pin_locked_s"] == 0


def test_pin_compare_is_constant_time_on_hashes():
    src = (REPO / "backend" / "debug_auth.py").read_text("utf-8")
    assert "hmac.compare_digest" in src and "sha256" in src
    assert re.search(r"pin\s*==|==\s*want|want\s*==", src) is None


def test_the_repo_holds_no_pin_value():
    """The PIN comes only from TRC_DEBUG_PIN: no default in the code (os.environ.get / getenv with a
    second argument), and every place that documents the variable leaves the value empty or a
    <placeholder>."""
    for p in (REPO / "backend").rglob("*.py"):
        rel = p.relative_to(REPO).as_posix()
        if rel.startswith("backend/tests/"):
            continue
        tree = ast.parse(p.read_text("utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")) in ("get", "getenv") \
                    and n.args and (isinstance(n.args[0], ast.Name) and n.args[0].id == "PIN_ENV"
                                    or isinstance(n.args[0], ast.Constant) and n.args[0].value == DA.PIN_ENV):
                assert len(n.args) == 1 and not n.keywords, f"{rel}:{n.lineno} reads {DA.PIN_ENV} with a default"
    assign = re.compile(re.escape(DA.PIN_ENV) + r"\s*[=:]\s*([^\s#`'\"]*)")
    files = [REPO / ".env.example", REPO / "Dockerfile", REPO / "docker-compose.yml"]
    files += list((REPO / "docs").rglob("*.md")) + list((REPO / "deploy").rglob("*")) + [REPO / "README.md"]
    seen = False
    for f in files:
        if not f.is_file():
            continue
        try:
            txt = f.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in assign.finditer(txt):
            seen = True
            v = m.group(1)
            assert v == "" or re.fullmatch(r"<[^>]*>?|\$\{?\w+\}?|\.\.\.", v), f"{f.name}: {DA.PIN_ENV}={v!r}"
    assert seen, ".env.example documents TRC_DEBUG_PIN (empty)"


