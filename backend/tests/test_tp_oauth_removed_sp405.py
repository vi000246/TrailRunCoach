"""SP-405: the TrainingPeaks authorization-code flow had no `state` (OAuth / login CSRF):
GET /api/v1/auth/tp/callback?code=<anyone's code> bound that TP account to whoever was
signed in. No page used it (TP connects with the username / password login), so the
redirect and the callback are gone, with the code exchange behind them.

Fake HTTP only, never a real account; the OAuth client credentials are the obviously
fake ones of the `tp_creds` fixture."""
import pytest
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.sync import http, tp_client
from backend.tests.test_sync_e2e import make_session
from backend.tests.test_tp_web_login import FakeTPWeb

GONE = ("/api/v1/auth/tp/oauth", "/api/v1/auth/tp/callback")
BACKEND = Path(__file__).resolve().parents[1]


def _routes(app):
    stack, out = list(app.routes), []
    while stack:
        r = stack.pop(0)
        sub = getattr(r, "original_router", None)
        if sub is not None:
            stack.extend(sub.routes)
        else:
            out.append(r)
    return out


class Recorder:
    """Answers nothing useful; records every outgoing request."""

    def __init__(self):
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(str(req.url))
        return httpx.Response(200, json={"access_token": "attacker-at", "expires_in": 3600})


def test_the_tp_oauth_redirect_and_callback_are_not_in_the_route_table(monkeypatch):
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    from backend.main import build_app
    routes = {(m, getattr(r, "path", "")) for r in _routes(build_app(demo=False))
              for m in (getattr(r, "methods", None) or {""})}
    paths = {p for _m, p in routes}
    assert not set(GONE) & paths, sorted(set(GONE) & paths)
    assert ("POST", "/api/v1/auth/tp/login") in routes          # the TP login that pages use stays


def test_a_foreign_code_on_the_old_callback_binds_nothing(tp_creds):
    """Even with TP OAuth client credentials configured, the old addresses answer 404 and
    nothing is sent to TrainingPeaks."""
    from backend.api import auth
    app = FastAPI()
    app.include_router(auth.router)
    rec = Recorder()
    with http.use_transport(httpx.MockTransport(rec)), TestClient(app) as c:
        for path in GONE:
            for method in ("GET", "POST"):
                r = c.request(method, path, params={"code": "attacker-code", "state": "x"},
                              follow_redirects=False)
                assert r.status_code in (404, 405), (method, path, r.status_code)
    assert rec.calls == []


@pytest.mark.needs_core
def test_no_authorization_code_exchange_is_left_in_the_backend():
    assert not hasattr(tp_client, "get_auth_url")
    assert not hasattr(tp_client, "exchange_code")
    # no TrainingPeaks code talks the authorization-code grant any more (Google sign-in does,
    # with its own state, SP-58 — not TP's)
    found = []
    for p in BACKEND.rglob("*.py"):
        src = p.read_text("utf-8")
        if "tests" not in p.parts and "trainingpeaks" in src.lower() \
                and ("authorization_code" in src or "OAuth/Authorize" in src):
            found.append(str(p.relative_to(BACKEND)))
    assert found == []


def test_the_tp_password_login_route_still_connects(tmp_path):
    from backend.api import auth
    from backend.db.database import get_db
    app = FastAPI()
    app.include_router(auth.router)
    sessions = []

    async def db():
        s = await make_session(tmp_path)
        sessions.append(s)
        yield s
    app.dependency_overrides[get_db] = db
    seen = {}

    async def stored():
        s = sessions[-1]
        seen["row"] = (await s.execute(text("SELECT tp_access_token, tp_web_cookie FROM sync_state"))).one()

    with http.use_transport(httpx.MockTransport(FakeTPWeb())), TestClient(app) as c:
        r = c.post("/api/v1/auth/tp/login", json={"username": "u@example.com", "password": "pw"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["authenticated"] is True and body["method"] == "web"
        assert body["tp_athlete_id"] == 555 and body["password_saved"] is False
        c.portal.call(stored)
    assert all(v for v in seen["row"])
