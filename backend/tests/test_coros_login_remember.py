"""COROS login: one login per call, the data-server fallback, 「記住密碼」 and
the automatic re-login (fake HTTP only, never a real account)."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from backend.api import auth as AUTH
from backend.db.models import SyncState
from backend.sync import coros_client, http
from backend.tests.test_sync_e2e import START, _coros_act, collect, make_session, run

PASSWORD = "S3cret-pw-do-not-leak"
EU, US, CN = coros_client.COROS_BASES["eu"], coros_client.COROS_BASES["us"], coros_client.COROS_BASES["cn"]


class Coros:
    """Scripted COROS: the account lives on `home`; tokens are numbered and
    only the latest one is valid (as COROS behaves)."""

    def __init__(self, home=EU, activities=(), probe_ok=True, login_ok=True, data=None):
        self.home, self.activities, self.probe_ok, self.login_ok = home, list(activities), probe_ok, login_ok
        self.data = data or home          # the data server (a Taiwan account: login EU, data US)
        self.logins = []
        self.n = 0
        self.valid = None
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        base = f"{request.url.scheme}://{request.url.host}"
        path = request.url.path
        self.calls.append((base, path))
        assert PASSWORD not in request.url.query.decode() and PASSWORD.encode() not in (request.content or b"")
        if path == "/account/login":
            if base != self.home:
                return httpx.Response(200, json={"result": "1001", "message": "account not in this region"})
            if not self.login_ok:
                return httpx.Response(200, json={"result": "1002", "message": "wrong password"})
            self.n += 1
            self.valid = f"tok{self.n}"
            self.logins.append(base)
            return httpx.Response(200, json={"result": "0000", "data": {"accessToken": self.valid, "userId": 42}})
        if path == "/activity/query":
            if not self.probe_ok and request.url.params.get("size") == "1":
                raise httpx.ReadTimeout("busy loop", request=request)
            if base != self.data or request.headers.get("accessToken") != self.valid:
                return httpx.Response(200, json={"result": "1019", "message": "Access token is invalid"})
            q = dict(request.url.params)
            items = self.activities if q.get("pageNumber") == "1" and q.get("size") != "1" else []
            return httpx.Response(200, json={"result": "0000", "data": {
                "dataList": [{k: v for k, v in a.items() if k != "_bytes"} for a in items]}})
        if path.startswith("/fit/"):
            label = path.split("/")[-1]
            return httpx.Response(200, content=next(a for a in self.activities if a["labelId"] == label)["_bytes"])
        return httpx.Response(404)


async def _state(s):
    return (await s.execute(select(SyncState))).scalar_one()


def test_login_is_made_once_and_finds_the_data_server(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=EU, data=US)                 # a Taiwan account
        with http.use_transport(httpx.MockTransport(fake)):
            info = await coros_client.login("me@example.com", PASSWORD, s, 1)
        assert fake.logins == [EU]                     # never a second login on US / CN
        assert (await _state(s)).coros_base_url == US and info["data_server"] == US
        assert PASSWORD not in json.dumps(info)
    run(go())


def test_probes_timing_out_keep_the_known_data_server(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        with http.use_transport(httpx.MockTransport(Coros(home=CN))):
            await coros_client.login("me@example.com", PASSWORD, s, 1)
        assert (await _state(s)).coros_base_url == CN
        fake = Coros(home=CN, probe_ok=False)          # e.g. a blocked event loop
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", PASSWORD, s, 1)
        assert (await _state(s)).coros_base_url == CN and len(fake.logins) == 1
    run(go())


def test_concurrent_logins_are_refused_not_raced(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        with http.use_transport(httpx.MockTransport(fake)):
            res = await asyncio.gather(coros_client.login("me@example.com", PASSWORD, s, 1),
                                       coros_client.login("me@example.com", PASSWORD, s, 1),
                                       return_exceptions=True)
        assert sum(isinstance(r, coros_client.LoginBusy) for r in res) == 1
        assert len(fake.logins) == 1
        st = await _state(s)
        from backend.settings.secrets import unseal
        assert unseal(st.coros_access_token) == fake.valid           # the stored token is the live one
    run(go())


def test_remember_stores_the_password_sealed_and_logout_deletes_it(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    async def go():
        s = await make_session(tmp_path)
        with http.use_transport(httpx.MockTransport(Coros(home=US))):
            r = await AUTH.coros_login(AUTH.CorosLoginRequest(email="me@example.com", password=PASSWORD,
                                                              remember=True), s)
        assert r["password_saved"] is True and PASSWORD not in json.dumps(r)
        st = await _state(s)
        assert st.coros_password_sealed and PASSWORD not in st.coros_password_sealed
        status = await AUTH.coros_auth_status(1, s)
        assert status["password_saved"] is True and PASSWORD not in json.dumps(status)
        # untick -> deleted at once
        assert (await AUTH.set_remember("coros", AUTH.RememberBody(remember=False), s)) == {"password_saved": False}
        assert (await _state(s)).coros_password_sealed is None
        # login without remember never stores; logout clears a stored one
        with http.use_transport(httpx.MockTransport(Coros(home=US))):
            await AUTH.coros_login(AUTH.CorosLoginRequest(email="me@example.com", password=PASSWORD,
                                                          remember=True), s)
        await AUTH.coros_logout(1, s)
        assert (await _state(s)).coros_password_sealed is None
        with http.use_transport(httpx.MockTransport(Coros(home=US))):
            r = await AUTH.coros_login(AUTH.CorosLoginRequest(email="me@example.com", password=PASSWORD), s)
        assert r["password_saved"] is False and (await _state(s)).coros_password_sealed is None
    run(go())
    assert PASSWORD not in caplog.text


def test_invalid_token_logs_in_once_and_retries_the_listing(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US, activities=[_coros_act("A1", START)])
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", PASSWORD, s, 1)
            await coros_client.save_password(s, 1, PASSWORD)
            fake.valid = "someone-else-logged-in"           # the stored token is now invalid
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert any(e.get("status") == "relogin" for e in ev)
        assert sum(e.get("status") == "downloaded" for e in ev) == 1 and ev[-1]["errors"] == []
        assert len(fake.logins) == 2                          # the first + exactly one automatic
    run(go())
    assert PASSWORD not in caplog.text


def test_expired_token_is_renewed_before_the_sync(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", PASSWORD, s, 1)
            await coros_client.save_password(s, 1, PASSWORD)
            st = await _state(s)
            st.coros_token_expires = datetime.now(timezone.utc) - timedelta(minutes=1)
            await s.commit()
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert ev[-1]["status"] == "complete" and len(fake.logins) == 2
    run(go())


def test_a_failed_relogin_is_reported_not_looped(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", PASSWORD, s, 1)
            await coros_client.save_password(s, 1, PASSWORD)
            fake.valid, fake.login_ok = "gone", False         # the password was changed on COROS
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert ev[-1]["error"] == "COROS_AUTH_REQUIRED"
        # the login (EU no, US yes) + ONE re-login attempt over the 3 regions; no loop
        assert len([c for c in fake.calls if c[1] == "/account/login"]) == 2 + 3
    run(go())


def test_without_a_remembered_password_nothing_logs_in(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        fake = Coros(home=US)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", PASSWORD, s, 1)
            fake.valid = "gone"
            ev = await collect(coros_client.sync_workouts(s, 1))
        assert ev[-1]["error"] == "COROS_AUTH_REQUIRED" and len(fake.logins) == 1
    run(go())


def test_training_hub_call_relogs_once_on_invalid_token(tmp_path):
    from backend.sync import coros_workouts as CW

    class Hub(Coros):
        def __call__(self, request):
            if request.url.path == "/training/program/query":
                if request.headers.get("accessToken") != self.valid:
                    return httpx.Response(200, json={"result": "1019", "message": "Access token is invalid"})
                return httpx.Response(200, json={"result": "0000", "data": {"list": [{"id": "p1"}]}})
            return super().__call__(request)

    async def go():
        s = await make_session(tmp_path)
        fake = Hub(home=US)
        with http.use_transport(httpx.MockTransport(fake)):
            await coros_client.login("me@example.com", PASSWORD, s, 1)
            await coros_client.save_password(s, 1, PASSWORD)
            hub = await CW.TrainingHub.from_db(s, 1)
            fake.valid = "gone"
            assert await hub.list_programs() == [{"id": "p1"}]
            fake.valid = "gone-again"
            with pytest.raises(CW.CorosAuthError):           # only one automatic login per hub
                await hub.list_programs()
        assert len(fake.logins) == 2
    run(go())


def test_tp_remember_and_logout(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        from backend.sync import tp_client
        await tp_client.save_password(s, 1, "u@example.com", PASSWORD)
        st = await _state(s)
        assert st.tp_username == "u@example.com" and PASSWORD not in st.tp_password_sealed
        status = await AUTH.tp_auth_status(1, s)
        assert status["password_saved"] is True and PASSWORD not in json.dumps(status)
        await AUTH.tp_logout(1, s)
        st = await _state(s)
        assert st.tp_password_sealed is None and st.tp_username is None
    run(go())
