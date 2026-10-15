"""Many made-up accounts at once, for the tests of what several people do together (a coach and
their students, a group): SP-611's helper.

    users = many(30)                         # 30 test accounts in accounts.db ("學員 1" … "學員 30")
    t = tenant(users[0])                     # its tenant (folder + DB made), for an in-process client
    cl = signed_in(app, users[0])            # a browser signed in as it (sign-in mode: test_login_sp58's app)

Each one is a real row (users.is_test = 1) through accounts/testaccounts.create_many — the same
path the admin page takes — so the cap, the scheduler and the research pool treat them as they
treat the admin's. No made-up data unless asked for (`mock=`, built by build_mock: slow).
Needs the accounts server root of the test (test_membership_sp330's `server`, or test_login_sp58's
`env`)."""
from __future__ import annotations

import asyncio
from typing import Optional


def many(n: int, prefix: str = "學員", mock: Optional[dict] = None) -> list:
    from backend.accounts import testaccounts as TA
    return TA.create_many(n, prefix, mock=mock, start=False)


def tenant(user):
    """The user's tenant, its folder and DB ready (as the sign-in gate does on the first request)."""
    from backend.accounts import tenants as TN
    t = TN.tenant_of(user)
    asyncio.run(TN.ensure_ready(t))
    return t


def link_token(user) -> str:
    """A one-time sign-in link's token for `user` (what the admin page's button makes)."""
    import secrets
    from backend.accounts import store, testaccounts as TA
    token = secrets.token_urlsafe(32)
    store.put_test_login(token, user.id, None, TA.LINK_TTL_S)
    return token


def signed_in(app, user, ip: str = "203.0.113.10"):
    """A new browser on `app` (sign-in mode) signed in as the test account `user`."""
    from backend.tests.test_login_sp58 import browser, csrf
    cl = browser(app, ip)
    r = cl.post("/api/v1/account/test-login", json={"token": link_token(user)}, headers={"X-TRC-CSRF": csrf(cl)})
    assert r.status_code == 200, r.text
    return cl
