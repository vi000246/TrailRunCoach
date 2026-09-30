"""TP website-login fallback (fake HTTP only; no real account)."""
from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from backend.settings import secrets as S
from backend.sync import http, tp_client
from backend.tests.test_sync_e2e import make_session, run

LOGIN_PAGE = """<html><form action="/login" method="post">
<input name="__RequestVerificationToken" type="hidden" value="csrf123" />
<input name="CaptchaHidden" type="hidden" value="false" />
<input name="CaptchaToken" type="hidden" value="" />
<input name="Attempts" type="hidden" value="0" />
<input name="SelectedMfaMethod" type="hidden" value="" />
<input name="Username" /><input name="Password" type="password" />
</form></html>"""


class FakeTPWeb:
    """grant -> 400 invalid_grant; website login -> cookie; users/v3/token -> token."""

    def __init__(self, outcome="ok", token_ok=True):
        self.outcome, self.token_ok = outcome, token_ok
        self.posted = None
        self.token_calls = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        if host == "oauth.trainingpeaks.com":
            return httpx.Response(400, json={"error": "invalid_grant",
                                             "error_description": "Invalid resource owner password credential."})
        if host == "home.trainingpeaks.com" and path == "/login":
            if req.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            self.posted = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            if self.outcome == "ok":
                return httpx.Response(302, headers={
                    "Location": "https://app.trainingpeaks.com/",
                    "Set-Cookie": "Production_tpAuth=cookieVAL; Domain=.trainingpeaks.com; Path=/; Secure; HttpOnly"})
            if self.outcome == "badpw":
                return httpx.Response(200, text=LOGIN_PAGE.replace(
                    "</form>", '<div class="validation-summary-errors"><ul><li>Invalid username or password</li></ul></div></form>'))
            if self.outcome == "captcha":
                return httpx.Response(200, text=LOGIN_PAGE.replace(
                    'name="CaptchaHidden" type="hidden" value="false"', 'name="CaptchaHidden" type="hidden" value="true"'))
            if self.outcome == "mfa":
                return httpx.Response(302, headers={"Location": "https://home.trainingpeaks.com/login/mfa"})
        if host == "home.trainingpeaks.com" and path == "/login/mfa":
            return httpx.Response(200, text="<p>Enter the verification code we sent to your phone</p>")
        if host == "app.trainingpeaks.com":
            return httpx.Response(200, text="<html>app</html>")
        if host == "tpapi.trainingpeaks.com" and path == "/users/v3/token":
            self.token_calls += 1
            assert "Production_tpAuth=cookieVAL" in req.headers.get("cookie", "")
            if not self.token_ok:
                return httpx.Response(401, json={"success": False})
            return httpx.Response(200, json={"success": True, "token": {
                "access_token": f"web-at-{self.token_calls}", "expires_in": 3600, "token_type": "bearer"}})
        if host == "tpapi.trainingpeaks.com" and path == "/users/v3/user":
            assert req.headers["authorization"].startswith("Bearer web-at-")
            return httpx.Response(200, json={"user": {"userId": 555, "athletes": [
                {"athleteId": 555, "athleteType": "premium"}]}, "accountStatus": {}})
        return httpx.Response(404)


def test_grant_rejected_falls_back_to_website_login(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeTPWeb()
        with http.use_transport(httpx.MockTransport(fake)):
            r = await tp_client.login_password("u@example.com", "pw", s, 1)
        assert r["method"] == "web" and r["tp_athlete_id"] == 555 and r["can_download"]
        assert fake.posted["__RequestVerificationToken"] == "csrf123"
        assert fake.posted["Username"] == "u@example.com"
        row = (await s.execute(text(
            "SELECT tp_access_token, tp_refresh_token, tp_web_cookie FROM sync_state"))).one()
        assert row[0].startswith(S.PREFIX) and row[2].startswith(S.PREFIX)
        assert "cookieVAL" not in row[2] and row[1] is None
        assert S.unseal(row[2]) == "cookieVAL"
    run(go())


def test_expired_web_token_is_renewed_with_the_cookie(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeTPWeb()
        with http.use_transport(httpx.MockTransport(fake)):
            await tp_client.login_password("u@example.com", "pw", s, 1)
            from backend.db.models import SyncState
            st = await s.get(SyncState, 1)
            st.tp_token_expires = datetime.utcnow() - timedelta(minutes=1)
            await s.commit()
            assert await tp_client._get_valid_token(s, 1) == "web-at-2"
    run(go())


def test_rejected_cookie_means_auth_required(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = FakeTPWeb()
        with http.use_transport(httpx.MockTransport(fake)):
            await tp_client.login_password("u@example.com", "pw", s, 1)
            from backend.db.models import SyncState
            st = await s.get(SyncState, 1)
            st.tp_token_expires = datetime.utcnow() - timedelta(minutes=1)
            await s.commit()
            fake.token_ok = False
            assert await tp_client._get_valid_token(s, 1) is None
            ev = [e async for e in tp_client.sync_workouts(s, 1)]
        assert ev[0]["error"] == "TP_AUTH_REQUIRED"
    run(go())


@pytest.mark.parametrize("outcome,code,status", [
    ("badpw", "TP_LOGIN_FAILED", 401),
    ("captcha", "TP_LOGIN_CAPTCHA", 403),
    ("mfa", "TP_LOGIN_MFA", 403),
])
def test_login_page_outcomes_map_to_clear_errors(tmp_path, outcome, code, status):
    from backend.api.auth import LoginRequest, tp_login_password

    async def go():
        s = await make_session(tmp_path)
        with http.use_transport(httpx.MockTransport(FakeTPWeb(outcome))):
            with pytest.raises(HTTPException) as ei:
                await tp_login_password(LoginRequest(username="u@example.com", password="pw"), s)
        assert ei.value.status_code == status
        assert ei.value.detail.startswith(code)
        assert "pw" not in ei.value.detail.replace("password", "")
    run(go())


def test_trace_logs_steps_and_keys_but_no_secrets(tmp_path, caplog):
    import logging

    async def go():
        s = await make_session(tmp_path)
        with caplog.at_level(logging.INFO, logger="backend.sync.tp_client"):
            with http.use_transport(httpx.MockTransport(FakeTPWeb())):
                await tp_client.login_password("u@example.com", "s3cretPW", s, 1)
        out = caplog.text
        for step in ("step=login_page", "step=login_post", "step=token", "step=user", "step=done"):
            assert step in out
        assert "keys=accountStatus,user" in out
        for secret in ("s3cretPW", "u@example.com", "cookieVAL", "web-at-", "csrf123"):
            assert secret not in out
    run(go())


def test_token_step_named_in_error(tmp_path):
    from backend.api.auth import LoginRequest, tp_login_password

    async def go():
        s = await make_session(tmp_path)
        with http.use_transport(httpx.MockTransport(FakeTPWeb(token_ok=False))):
            with pytest.raises(HTTPException) as ei:
                await tp_login_password(LoginRequest(username="u@example.com", password="pw"), s)
        assert ei.value.status_code == 502 and ei.value.detail.startswith("TP_LOGIN_ERROR[token]")
    run(go())


def test_live_shape_numeric_types_and_athlete_id_fallback(tmp_path):
    """users/v3/user without athletes / userId -> id from users/v3/user/athletes."""
    class LiveShape(FakeTPWeb):
        def __call__(self, req):
            if req.url.host == "tpapi.trainingpeaks.com" and req.url.path == "/users/v3/user":
                return httpx.Response(200, json={"user": {"userName": "x", "userType": 1,
                                                          "athleteType": 2, "isPremium": True},
                                                 "accountStatus": {"isLocked": False}})
            if req.url.host == "tpapi.trainingpeaks.com" and req.url.path == "/users/v3/user/athletes":
                return httpx.Response(200, json=[{"athleteId": 4242}])
            return super().__call__(req)

    async def go():
        s = await make_session(tmp_path)
        with http.use_transport(httpx.MockTransport(LiveShape())):
            r = await tp_client.login_password("u@example.com", "pw", s, 1)
        assert r["tp_athlete_id"] == 4242 and r["premium"] and r["can_download"]
        from backend.db.models import Athlete
        assert (await s.get(Athlete, 1)).tp_athlete_id == 4242
    run(go())


def test_extract_accepts_numeric_athlete_type():
    aid, athletes, user_type, premium = tp_client._extract_athlete_id(
        {"user": {"userId": 9, "athletes": [{"athleteId": 9, "athleteType": 2, "isPremium": True}]}})
    assert aid == 9 and premium and user_type == "2"
    assert tp_client._can_download(user_type, False) is False


def test_successful_grant_still_used_first(tmp_path):
    class Grant(FakeTPWeb):
        def __call__(self, req):
            if req.url.host == "oauth.trainingpeaks.com":
                return httpx.Response(200, json={"access_token": "web-at-oauth", "refresh_token": "rt",
                                                 "expires_in": 3600})
            return super().__call__(req)

    async def go():
        s = await make_session(tmp_path)
        fake = Grant()
        with http.use_transport(httpx.MockTransport(fake)):
            r = await tp_client.login_password("u@example.com", "pw", s, 1)
        assert r["method"] == "oauth" and fake.posted is None
    run(go())
