"""TP OAuth with runtime-loaded client credentials (fake values, fake HTTP)."""
import json
import logging
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from backend.db.models import SyncState
from backend.settings import secrets as S
from backend.settings.repository import SettingsRepository
from backend.sync import http, tp_client
from backend.tests.conftest import FAKE_TP_CLIENT
from backend.tests.test_sync_e2e import make_session, run
from backend.tests.test_tp_web_login import LiveShape


class OAuthFake(LiveShape):
    """oauth token endpoint scripted by `grant` / `refresh` status; the rest
    (website login, users/v3/*) from the web fakes."""

    def __init__(self, grant=200, refresh=200, **kw):
        super().__init__(**kw)
        self.grant, self.refresh = grant, refresh
        self.token_bodies, self.token_headers = [], []

    def __call__(self, req):
        if req.url.host == "oauth.trainingpeaks.com":
            body = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            self.token_bodies.append((req.content.decode(), body))
            self.token_headers.append(dict(req.headers))
            status = self.grant if body.get("grant_type") == "password" else self.refresh
            if status != 200:
                return httpx.Response(status, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "web-at-oauth", "refresh_token": "rt-2",
                                             "expires_in": 3600})
        return super().__call__(req)


# ---- credential loading -------------------------------------------------------

def test_creds_from_env(tp_creds):
    assert tp_client.load_client_creds() == FAKE_TP_CLIENT


def test_creds_from_file_and_env_wins(tmp_path, monkeypatch):
    f = tmp_path / "tp_client.json"
    f.write_text(json.dumps({"client_id": "file-id", "client_secret": "file-secret-fake"}), "utf-8")
    monkeypatch.setattr(tp_client, "TP_CLIENT_FILE", f)
    assert tp_client.load_client_creds() == ("file-id", "file-secret-fake")
    monkeypatch.setenv("TP_CLIENT_ID", "env-id")
    monkeypatch.setenv("TP_CLIENT_SECRET", "env-secret-fake")
    assert tp_client.load_client_creds() == ("env-id", "env-secret-fake")


def test_incomplete_or_missing_creds(tmp_path, monkeypatch):
    assert tp_client.load_client_creds() is None
    f = tmp_path / "tp_client.json"
    f.write_text(json.dumps({"client_id": "only-id"}), "utf-8")
    monkeypatch.setattr(tp_client, "TP_CLIENT_FILE", f)
    assert tp_client.load_client_creds() is None


# ---- login ----------------------------------------------------------------------

def test_password_grant_body_matches_wko5(tmp_path, tp_creds):
    async def go():
        s = await make_session(tmp_path)
        fake = OAuthFake()
        with http.use_transport(httpx.MockTransport(fake)):
            r = await tp_client.login_password("u@example.com", "p&w d", s, 1)
        assert r["method"] == "oauth" and fake.posted is None          # no website login
        raw, body = fake.token_bodies[0]
        assert body == {"grant_type": "password", "username": "u@example.com", "password": "p&w d",
                        "scope": "fitness baseactivity users metrics software groundcontrol",
                        "client_id": FAKE_TP_CLIENT[0], "client_secret": FAKE_TP_CLIENT[1]}
        assert "scope=fitness+baseactivity+users+metrics+software+groundcontrol" in raw
        assert fake.token_headers[0]["user-agent"] == "WKO5/PC/5.0.587"
        assert fake.token_headers[0]["content-type"] == "application/x-www-form-urlencoded"
        st = await s.get(SyncState, 1)
        assert st.tp_refresh_token.startswith(S.PREFIX) and S.unseal(st.tp_refresh_token) == "rt-2"
    run(go())


def test_no_secret_means_web_login_only(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = OAuthFake()
        with http.use_transport(httpx.MockTransport(fake)):
            r = await tp_client.login_password("u@example.com", "pw", s, 1)
        assert r["method"] == "web" and fake.token_bodies == []
    run(go())


def test_invalid_client_falls_back_to_web(tmp_path, tp_creds):
    async def go():
        s = await make_session(tmp_path)
        fake = OAuthFake(grant=400)
        with http.use_transport(httpx.MockTransport(fake)):
            r = await tp_client.login_password("u@example.com", "pw", s, 1)
        assert r["method"] == "web" and len(fake.token_bodies) == 1 and fake.posted
    run(go())


def test_setting_can_disable_the_wko5_client(tmp_path, tp_creds):
    async def go():
        s = await make_session(tmp_path)
        await SettingsRepository(s, 1).set("sync.trainingpeaks.use_wko5_client", False)
        fake = OAuthFake()
        with http.use_transport(httpx.MockTransport(fake)):
            r = await tp_client.login_password("u@example.com", "pw", s, 1)
        assert r["method"] == "web" and fake.token_bodies == []
    run(go())


# ---- refresh --------------------------------------------------------------------

async def _expire(s):
    st = await s.get(SyncState, 1)
    st.tp_token_expires = datetime.utcnow() - timedelta(minutes=1)
    await s.commit()


def test_refresh_uses_refresh_token_with_client_creds(tmp_path, tp_creds):
    async def go():
        s = await make_session(tmp_path)
        fake = OAuthFake()
        with http.use_transport(httpx.MockTransport(fake)):
            await tp_client.login_password("u@example.com", "pw", s, 1)
            await _expire(s)
            assert await tp_client._get_valid_token(s, 1) == "web-at-oauth"
        _raw, body = fake.token_bodies[-1]
        assert body == {"grant_type": "refresh_token", "refresh_token": "rt-2",
                        "client_id": FAKE_TP_CLIENT[0], "client_secret": FAKE_TP_CLIENT[1]}
    run(go())


def test_failed_refresh_falls_back_to_cookie_then_auth_required(tmp_path, tp_creds):
    async def go():
        s = await make_session(tmp_path)
        fake = OAuthFake(grant=400, refresh=400)   # web login -> cookie, but keep a refresh token
        with http.use_transport(httpx.MockTransport(fake)):
            await tp_client.login_password("u@example.com", "pw", s, 1)
            st = await s.get(SyncState, 1)
            st.tp_refresh_token = S.seal("stale-rt")
            await s.commit()
            await _expire(s)
            tok = await tp_client._get_valid_token(s, 1)
            assert tok is not None and tok.startswith("web-at-")      # renewed from the cookie
            assert fake.token_bodies[-1][1]["grant_type"] == "refresh_token"
            await _expire(s)
            fake.token_ok = False                                      # cookie rejected too
            assert await tp_client._get_valid_token(s, 1) is None
    run(go())


def test_secret_never_reaches_the_logs(tmp_path, tp_creds, caplog):
    async def go():
        s = await make_session(tmp_path)
        fake = OAuthFake(grant=401, refresh=401)
        with caplog.at_level(logging.DEBUG):
            with http.use_transport(httpx.MockTransport(fake)):
                await tp_client.login_password("u@example.com", "pw", s, 1)
                st = await s.get(SyncState, 1)
                st.tp_refresh_token = S.seal("stale-rt")
                await s.commit()
                await _expire(s)
                await tp_client._get_valid_token(s, 1)
        assert "step=oauth status=401 error=invalid_client" in caplog.text
        assert "step=refresh status=401" in caplog.text
        for secret in (FAKE_TP_CLIENT[1], "stale-rt", "rt-2"):
            assert secret not in caplog.text
    run(go())


def test_sync_settings_expose_opt_in_without_values(tmp_path, tp_creds):
    from backend.api.sync import SyncSettingsBody, get_sync_settings, put_sync_settings

    async def go():
        s = await make_session(tmp_path)
        g = await get_sync_settings(1, s)
        assert g["tp_use_wko5_client"] is None and g["tp_client_credentials_configured"] is True
        assert FAKE_TP_CLIENT[1] not in json.dumps(g)
        r = await put_sync_settings(SyncSettingsBody(tp_use_wko5_client=False), 1, s)
        assert r["tp_use_wko5_client"] is False
    run(go())
