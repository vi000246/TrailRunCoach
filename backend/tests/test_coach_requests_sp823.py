"""SP-823: a coach learns that students are waiting for a yes without opening 設定 › 教練 — the session
(inlined into every page, GET /api/v1/session) carries `coach_requests` {n, at} while students who typed
the coach's code wait; the shell draws the line (test_coach_requests_sp823_e2e.py). In-app only: nothing
is sent anywhere.

Every person is a made-up test account (accounts_fixtures.many) in sign-in mode; nothing real is read."""
from __future__ import annotations

import pytest

from backend import site_config as SC, site_settings as SS
from backend.tests import accounts_fixtures as AF
from backend.tests.test_login_sp58 import app, browser, c, csrf, env, started  # noqa: F401 — fixtures

_IP = iter(f"198.51.100.{i}" for i in range(10, 250))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from backend.accounts import coaching
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    SS.reset_cache()
    coaching.reset_limits()
    yield
    SS.reset_cache()
    coaching.reset_limits()


def _in(app, user):
    cl = AF.signed_in(app, user, ip=next(_IP))
    cl.tok = {"X-TRC-CSRF": csrf(cl)}
    cl.user = user
    return cl


def _post(cl, url, body=None):
    r = cl.post(url, json=body or {}, headers=cl.tok)
    assert r.status_code == 200, r.text
    return r.json()


def _coach(app, user):
    from backend.accounts import coaching
    assert coaching.set_coach(user.id, True, by=1)
    return _in(app, user)


def _asks(stu, code) -> int:
    return _post(stu, "/api/v1/account/coach/request", {"code": code})["coaches"][0]["link_id"]


def _line(cl):
    return cl.get("/api/v1/session").json().get("coach_requests")


def test_the_coach_reads_the_waiting_students_until_they_are_answered(app):
    coach_u, s1, s2, s3 = AF.many(4)
    coach, a, b, d = _coach(app, coach_u), _in(app, s1), _in(app, s2), _in(app, s3)
    code = coach.get("/api/v1/coach").json()["code"]
    assert _line(coach) is None                                         # nobody waits: no line
    la = _asks(a, code)
    first = _line(coach)
    assert first["n"] == 1 and first["at"]
    lb, ld = _asks(b, code), _asks(d, code)
    three = _line(coach)
    assert three["n"] == 3 and three["at"] >= first["at"]              # the newest request's time
    for stu in (a, b, d):
        assert "coach_requests" not in stu.get("/api/v1/session").json()  # the students: nothing
    _post(coach, f"/api/v1/coach/requests/{la}/accept")                # accepted
    assert _line(coach)["n"] == 2
    _post(coach, f"/api/v1/coach/links/{lb}/end")                      # declined
    assert _line(coach)["n"] == 1
    _post(d, f"/api/v1/account/coach/{ld}/end")                        # cancelled by the student
    assert _line(coach) is None


def test_the_page_itself_carries_it(app):
    coach_u, s1 = AF.many(2)
    coach, a = _coach(app, coach_u), _in(app, s1)
    _asks(a, coach.get("/api/v1/coach").json()["code"])
    html = coach.get("/api/v1/overview/page", headers={"Accept": "text/html"}).text
    assert '"coach_requests"' in html


def test_an_invite_link_waits_for_nobody(app):
    from urllib.parse import parse_qs, urlsplit
    coach_u, s1 = AF.many(2)
    coach, a = _coach(app, coach_u), _in(app, s1)
    url = _post(coach, "/api/v1/coach/invite")["url"]
    _post(a, "/api/v1/account/coach/invite/accept", {"token": parse_qs(urlsplit(url).query)["coach_token"][0]})
    assert _line(coach) is None                                         # the student said yes: active at once


def test_not_a_coach_a_disabled_student_or_viewing_has_no_line(app):
    from backend.accounts import coaching, store
    coach_u, s1, s2 = AF.many(3)
    coach, a = _coach(app, coach_u), _in(app, s1)
    _asks(a, coach.get("/api/v1/coach").json()["code"])
    store.set_disabled(s1.id, True)                                     # a disabled account: not counted
    assert _line(coach) is None
    store.set_disabled(s1.id, False)
    assert _line(coach)["n"] == 1
    # while the coach views an active student, the session is the view's: no line
    store.coach_accept_invite(coach_u.id, s2.id, 1, 4)
    _post(coach, f"/api/v1/coach/view/{s2.id}")
    s = coach.get("/api/v1/session").json()
    assert s["viewing"]["student_id"] == s2.id and "coach_requests" not in s
    _post(coach, "/api/v1/coach/view/stop")
    assert _line(coach)["n"] == 1
    # no longer a coach: the requests end with it, no line
    assert coaching.set_coach(coach_u.id, False, by=1)
    assert _line(coach) is None
