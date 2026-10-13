"""SP-431: the login check's answers (sync/session_check.py) are per tenant. In sign-in mode every
user has their own database and their athlete is id 1 in it, so a cache keyed by (source,
athlete_id) alone showed A's 「COROS 登入已過期」 on B's page, B's fresh login hid A's expired
one, and forgetting one person's answer forgot everyone's. Fake COROS only (never a real account)."""
import asyncio

import httpx
import pytest

from backend import tenancy
from backend.api import auth as AUTH
from backend.sync import coros_limits as CL, http, runner, session_check as SC
from backend.tests.test_coros_login_remember import PASSWORD, US, Coros
from backend.tests.test_sync_e2e import make_session, run


def _t(tmp_path, tid: str) -> tenancy.Tenant:
    d = tmp_path / tid
    d.mkdir(parents=True, exist_ok=True)
    return tenancy.Tenant(id=tid, kind=tenancy.USER, root=d, shared=d, caps=tenancy.USER_CAPS)


async def _login(s, fake):
    with http.use_transport(httpx.MockTransport(fake)):
        await AUTH.coros_login(AUTH.CorosLoginRequest(email="me@example.com", password=PASSWORD, remember=False,
                                                      consent_version=CL.DISCLAIMER_VERSION), s)


async def _two_logged_in(tmp_path):
    """Tenants uA and uB, each with their own database (athlete 1) and a COROS login."""
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")
    out = {}
    for t in (a, b):
        with tenancy.use(t):
            s = await make_session(t.root)
            fake = Coros(home=US)
            await _login(s, fake)
            out[t.id] = (t, s, fake)
    return out


def test_one_persons_expired_login_never_shows_on_anothers_page(tmp_path):
    async def go():
        ten = await _two_logged_in(tmp_path)
        (a, sa, fa), (b, sb, fb) = ten["uA"], ten["uB"]
        for t in (a, b):                                     # the fresh logins' answers ran out
            with tenancy.use(t):
                SC.forget("coros", 1)
        with tenancy.use(a):
            SC.mark_expired("coros", 1)                      # A's sync got COROS's auth-required answer
        with tenancy.use(b), http.use_transport(httpx.MockTransport(fb)):
            assert not SC.is_expired("coros", 1)
            assert await SC.check(sb, "coros", 1) == SC.OK   # B's own login, checked at COROS
            st = await AUTH.coros_auth_status(1, sb)
            assert st["authenticated"] is True and st["status"] == "logged_in"
            assert await runner.logged_in(sb, "coros", 1)    # B's auto-sync still runs
        with tenancy.use(a):
            assert SC.is_expired("coros", 1)                 # A's answer is still A's
            assert await SC.check(sa, "coros", 1) == SC.EXPIRED
            assert not await runner.logged_in(sa, "coros", 1)
    run(go())


def test_another_persons_fresh_login_does_not_hide_an_expired_one(tmp_path):
    async def go():
        ten = await _two_logged_in(tmp_path)
        (a, sa, _), (b, sb, fb) = ten["uA"], ten["uB"]
        with tenancy.use(a):
            SC.mark_expired("coros", 1)
        with tenancy.use(b), http.use_transport(httpx.MockTransport(fb)):
            await _login(sb, fb)                             # B logs in again: B's answer is ok
            assert SC.cached("coros", 1) == SC.OK
        with tenancy.use(a):
            assert SC.cached("coros", 1) == SC.EXPIRED       # A still sees 登入已過期
            assert (await AUTH.coros_auth_status(1, sa))["status"] == "expired"
    run(go())


def test_forgetting_one_persons_answer_keeps_the_others(tmp_path):
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")
    for t in (a, b):
        with tenancy.use(t):
            SC.mark_expired("coros", 1)
            SC.mark_ok("tp", 1)
    with tenancy.use(a):
        SC.forget("coros", 1)                                # A logs out of COROS
        assert SC.cached("coros", 1) is None
        assert SC.cached("tp", 1) == SC.OK
    with tenancy.use(b):
        assert SC.cached("coros", 1) == SC.EXPIRED
        assert SC.cached("tp", 1) == SC.OK
    with tenancy.use(b):
        SC.forget()                                          # every answer of B's
        assert SC.cached("coros", 1) is None and SC.cached("tp", 1) is None
    with tenancy.use(a):
        assert SC.cached("tp", 1) == SC.OK


def test_forget_by_tenant_id_leaves_the_current_tenant_alone(tmp_path):
    """The deletion of an account (accounts/deletion.py) lets go of that tenant's answers by id."""
    a, b = _t(tmp_path, "uA"), _t(tmp_path, "uB")
    for t in (a, b):
        with tenancy.use(t):
            SC.mark_expired("coros", 1)
    with tenancy.use(b):
        SC.forget(tenant_id="uA")
        assert SC.cached("coros", 1) == SC.EXPIRED
    with tenancy.use(a):
        assert SC.cached("coros", 1) is None


def test_the_owners_answers_are_kept_as_before(tmp_path):
    """Owner mode (no tenant set): one person, the same answers as before SP-431."""
    SC.mark_expired("coros", 1)
    assert SC.is_expired("coros", 1)
    with tenancy.use(tenancy.owner()):
        assert SC.is_expired("coros", 1)
    with tenancy.use(_t(tmp_path, "u2")):
        assert not SC.is_expired("coros", 1)
    SC.forget("coros", 1)
    assert SC.cached("coros", 1) is None


def test_one_persons_slow_check_does_not_hold_up_anothers(tmp_path, monkeypatch):
    """The check lock (one live check per source at a time) is per tenant, like the login locks
    of coros_client / tp_client (SP-384): B's page never waits for COROS to answer A's check."""
    async def go():
        ten = await _two_logged_in(tmp_path)
        (a, sa, _), (b, sb, _) = ten["uA"], ten["uB"]
        for t in (a, b):
            with tenancy.use(t):
                SC.forget("coros", 1)
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(db, athlete_id):
            if tenancy.current().id == "uA":
                started.set()
                await release.wait()
            return SC.OK
        monkeypatch.setattr(SC, "_check_coros", slow)

        async def check_as(t, s):
            with tenancy.use(t):
                return await SC.check(s, "coros", 1)
        ta = asyncio.create_task(check_as(a, sa))
        await asyncio.wait_for(started.wait(), 2)           # A's check is waiting for COROS
        try:
            assert await asyncio.wait_for(check_as(b, sb), 1) == SC.OK, "B waited for A's check"
        finally:
            release.set()
        assert await ta == SC.OK
    run(go())


# ---------------------------------------------------------------------------- SP-386 刪除帳號

from backend.tests.test_account_data_sp386 import _delete, _fill, _reauth, _uid  # noqa: E402
from backend.tests.test_login_sp58 import app, c, env, sign_up, started  # noqa: E402,F401 — fixtures


def test_deleting_an_account_lets_go_of_its_login_answers_only(c, tmp_path):
    from backend.accounts import store, tenants as TN
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    assert c.get("/api/v1/plan").status_code == 200
    _fill(uid)
    gone = TN.tenant_of(store.get_user(uid))
    other = _t(tmp_path, "u999")
    for t in (gone, other):
        with tenancy.use(t):
            SC.mark_expired("coros", 1)
    assert _reauth(c, "g-a", "alice@example.com").status_code == 303
    assert _delete(c).status_code == 200
    with tenancy.use(gone):
        assert SC.cached("coros", 1) is None                 # nothing of the deleted account is kept
    with tenancy.use(other):
        assert SC.cached("coros", 1) == SC.EXPIRED           # the other person's answer stays
