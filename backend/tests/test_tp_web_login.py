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
