"""The login check (sync/session_check.py): the settings page's 已登入 is
validated against COROS / TP (fake HTTP only, never a real account), cached,
flipped by any auth-required answer, renewed with a remembered password.
Never a token or a password in a response or the log."""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from backend.api import auth as AUTH
from backend.db.models import SyncState
from backend.sync import coros_client, http, runner, session_check as SC
from backend.tests.test_coros_login_remember import PASSWORD, US, Coros
from backend.tests.test_sync_e2e import collect, make_session, run

STATIC = Path(__file__).resolve().parents[1] / "static"


def _probes(fake):
    return sum(1 for _, p in fake.calls if p == "/activity/query")


async def _login(s, fake, remember=False):
    with http.use_transport(httpx.MockTransport(fake)):
        await AUTH.coros_login(AUTH.CorosLoginRequest(email="me@example.com", password=PASSWORD,
                                                      remember=remember), s)


def _no_secrets(*objs):
    for o in objs:
        txt = json.dumps(o, ensure_ascii=False)
        assert PASSWORD not in txt and "tok1" not in txt and "tok2" not in txt


def test_a_token_coros_refused_shows_expired_without_a_remembered_password(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake)
        with http.use_transport(httpx.MockTransport(fake)):
            st = await AUTH.coros_auth_status(1, s)
            assert st["authenticated"] is True and st["status"] == "logged_in"   # fresh login: cached ok
            SC.forget()                                      # the cache ran out
            fake.valid = "gone"                              # ~1 day later: COROS no longer takes it
            st = await AUTH.coros_auth_status(1, s)
        assert st["authenticated"] is False and st["expired"] is True and st["status"] == "expired"
        assert st["password_saved"] is False and st["email"] == "me@example.com"
        assert len(fake.logins) == 1                         # nothing logged in automatically
        assert not await runner.logged_in(s, "coros", 1)     # auto-sync skips it
        _no_secrets(st)
    run(go())
    assert PASSWORD not in caplog.text and "tok1" not in caplog.text


def test_the_check_is_cached_so_the_page_never_hammers_coros(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake)
        SC.forget()
        with http.use_transport(httpx.MockTransport(fake)):
            before = _probes(fake)
            for _ in range(5):
                assert (await AUTH.coros_auth_status(1, s))["authenticated"] is True
        assert _probes(fake) - before == 1
    run(go())


def test_a_locally_expired_token_needs_no_call_to_be_expired(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake)
        SC.forget()
        st = (await s.execute(select(SyncState))).scalar_one()
        st.coros_token_expires = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
        n = len(fake.calls)
        with http.use_transport(httpx.MockTransport(fake)):
            r = await AUTH.coros_auth_status(1, s)
        assert r["status"] == "expired" and len(fake.calls) == n
    run(go())


def test_a_remembered_password_renews_the_login_once(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake, remember=True)
        SC.forget()
        fake.valid = "someone-else-logged-in"
        with http.use_transport(httpx.MockTransport(fake)):
            r = await AUTH.coros_auth_status(1, s)
            r2 = await AUTH.coros_auth_status(1, s)
        assert r["authenticated"] is True and r2["authenticated"] is True
        assert len(fake.logins) == 2                         # the login + exactly one automatic
        _no_secrets(r)
    run(go())


def test_a_failed_automatic_relogin_is_expired(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake, remember=True)
        SC.forget()
        fake.valid, fake.login_ok = "gone", False            # password changed on COROS
        with http.use_transport(httpx.MockTransport(fake)):
            r = await AUTH.coros_auth_status(1, s)
        assert r["status"] == "expired" and r["password_saved"] is True
    run(go())


def test_coros_unreachable_keeps_the_login(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        await _login(s, Coros(home=US))
        SC.forget()
        r = await AUTH.coros_auth_status(1, s)               # conftest: every request refused
        assert r["authenticated"] is True and r["check"] == "unknown"
    run(go())


def test_an_auth_error_from_a_sync_marks_it_expired_and_a_login_clears_it(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        await _login(s, fake)
        fake.valid = "gone"
        with http.use_transport(httpx.MockTransport(fake)):
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert ev[-1]["error"] == "COROS_AUTH_REQUIRED"
        n = len(fake.calls)
        with http.use_transport(httpx.MockTransport(fake)):
            assert (await AUTH.coros_auth_status(1, s))["status"] == "expired"
        assert len(fake.calls) == n                          # known from the sync: no check needed
        await _login(s, fake)
        assert (await AUTH.coros_auth_status(1, s))["status"] == "logged_in"
        await AUTH.coros_logout(1, s)
        assert (await AUTH.coros_auth_status(1, s))["status"] == "logged_out"
    run(go())


def test_a_training_hub_auth_error_marks_it_expired(tmp_path):
    from backend.sync import coros_workouts as CW

    class Hub(Coros):
        def __call__(self, request):
            if request.url.path == "/training/program/query":
                return httpx.Response(200, json={"result": "1019", "message": "Access token is invalid"})
            return super().__call__(request)

    async def go():
        s = await make_session(tmp_path)
        fake = Hub(home=US)
        await _login(s, fake)
        with http.use_transport(httpx.MockTransport(fake)):
            hub = await CW.TrainingHub.from_db(s, 1)
            with pytest.raises(CW.CorosAuthError):
                await hub.list_programs()
        assert SC.is_expired("coros", 1)
    run(go())


def test_the_banner_lists_the_expired_login_in_use(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        assert (await AUTH.session_alerts(1, s))["expired"] == []
        fake = Coros(home=US)
        await _login(s, fake)
        SC.mark_expired("coros", 1)
        r = await AUTH.session_alerts(1, s)
        assert [a["source"] for a in r["expired"]] == ["coros"]
        assert r["expired"][0]["message"] == "COROS 登入已過期，重新登入後才能同步／推送"
        assert r["settings_url"].endswith("#sync")
    run(go())


def test_tp_a_refused_token_is_expired(tmp_path, monkeypatch):
    from backend.settings.secrets import seal
    from backend.sync import tp_client

    async def no_refresh(state, db):
        return False
    monkeypatch.setattr(tp_client, "_refresh_token", no_refresh)
    hits = []

    def tp(request):
        hits.append(request.url.path)
        return httpx.Response(401, json={"error": "unauthorized"})

    async def go():
        s = await make_session(tmp_path)
        s.add(SyncState(athlete_id=1, tp_access_token=seal("tp-secret-token"),
                        tp_token_expires=datetime.now(timezone.utc) + timedelta(hours=1)))
        await s.commit()
        with http.use_transport(httpx.MockTransport(tp)):
            r = await AUTH.tp_auth_status(1, s)
            await AUTH.tp_auth_status(1, s)
        assert r["status"] == "expired" and r["authenticated"] is False
        assert hits == ["/users/v3/user"]                    # one check, then the cache
        assert "tp-secret-token" not in json.dumps(r)
    run(go())


def test_pages_load_the_banner_and_the_settings_page_handles_expired():
    for page in ("overview.html", "schedule.html"):
        assert "/api/v1/static/session_banner.js" in (STATIC / page).read_text(encoding="utf-8")
    js = (STATIC / "session_banner.js").read_text(encoding="utf-8")
    assert "/api/v1/auth/session-alerts" in js
    html = (STATIC / "settings.html").read_text(encoding="utf-8")
    assert "登入已過期" in html and "auth.expired" in html
