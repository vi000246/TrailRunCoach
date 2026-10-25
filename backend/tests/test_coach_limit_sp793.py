"""SP-793: how many students a coach may have (no payment yet). By default 4 (coaching.DEFAULT_MAX_STUDENTS);
the admin page sets one coach's own. The yes that would take one more — the student accepting an
invite link, the coach accepting a request — is refused with COACH_FULL and says why; checked in the
same write transaction as the change. Pending requests do not count; lowering it removes nobody.

Made-up test accounts only (accounts_fixtures.many), in sign-in mode (test_login_sp58's app)."""
from __future__ import annotations

import pytest

from backend import site_config as SC, site_settings as SS
from backend.tests import accounts_fixtures as AF
from backend.tests.test_coaching_sp789 import _code, _coach, _in, _join, _post, _student, _token
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, started)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from backend.accounts import coaching
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    SS.reset_cache()
    coaching.reset_limits()
    yield
    SS.reset_cache()


def _owner(app):
    owner = browser(app, ip="203.0.113.77")
    assert location(sign_in(owner, "g-owner", OWNER_EMAIL)) == "/"
    owner.tok = {"X-TRC-CSRF": csrf(owner)}
    return owner


def _set(owner, uid, **body):
    return owner.put(f"/api/v1/admin/users/{uid}/coach", json=body, headers=owner.tok)


def test_the_fifth_accept_is_refused_until_the_admin_raises_the_limit(app):
    """驗收: a coach with 4 students — the 5th accept is refused with the reason; the admin page sets
    their limit to 5 — the 5th can accept."""
    from backend.accounts import coaching
    assert coaching.DEFAULT_MAX_STUDENTS == 4
    cu, *studs = AF.many(6)
    coach = _coach(app, cu)
    tok = _token(coach)
    for s in studs[:4]:
        assert _join(_in(app, s), tok).status_code == 200
    home = coach.get("/api/v1/coach").json()
    assert (home["used"], home["max_students"], home["full"]) == (4, 4, True)
    fifth = _in(app, studs[4])
    r = _join(fifth, tok)
    assert r.status_code == 409 and _code(r) == "COACH_FULL"
    msg = r.json()["detail"]["message"]
    assert "4" in msg and "已滿" in msg and "管理員" in msg
    assert fifth.get("/api/v1/account/coach").json()["coaches"] == []      # nothing half made
    assert _student(coach, studs[4].id).status_code == 403
    # the admin page: 5 for this coach
    owner = _owner(app)
    row = owner.get("/api/v1/admin/users").json()
    assert row["coach_default_max"] == 4
    mine = next(u for u in row["users"] if u["id"] == cu.id)
    assert mine["coach"] == {"max_students": 4, "custom": False, "used": 4}
    assert next(u for u in row["users"] if u["id"] == studs[0].id)["coach"] is None
    r = _set(owner, cu.id, max_students=5)
    assert r.status_code == 200 and r.json()["coach"] == {"max_students": 5, "custom": True, "used": 4}
    assert _join(fifth, tok).status_code == 200
    assert _student(coach, studs[4].id).status_code == 200
    assert coach.get("/api/v1/coach").json()["full"] is True


def test_the_limit_holds_when_the_coach_accepts_a_request(app):
    cu, s1, s2 = AF.many(3)
    coach = _coach(app, cu)
    owner = _owner(app)
    assert _set(owner, cu.id, max_students=1).status_code == 200
    code = coach.get("/api/v1/coach").json()["code"]
    for s in (s1, s2):                                   # pending requests do not count
        assert _post(_in(app, s), "/api/v1/account/coach/request", {"code": code}).status_code == 200
    reqs = {r["student_id"]: r["link_id"] for r in coach.get("/api/v1/coach").json()["students"]}
    assert _post(coach, f"/api/v1/coach/requests/{reqs[s1.id]}/accept").status_code == 200
    r = _post(coach, f"/api/v1/coach/requests/{reqs[s2.id]}/accept")
    assert r.status_code == 409 and _code(r) == "COACH_FULL" and "1" in r.json()["detail"]["message"]
    assert _student(coach, s2.id).status_code == 403
    # letting one go makes room
    _post(coach, f"/api/v1/coach/links/{reqs[s1.id]}/end")
    assert _post(coach, f"/api/v1/coach/requests/{reqs[s2.id]}/accept").status_code == 200


def test_lowering_the_limit_removes_nobody_and_back_to_the_default(app):
    cu, s1, s2, s3 = AF.many(4)
    coach = _coach(app, cu)
    tok = _token(coach)
    for s in (s1, s2):
        _join(_in(app, s), tok)
    owner = _owner(app)
    assert _set(owner, cu.id, max_students=1).json()["coach"] == {"max_students": 1, "custom": True, "used": 2}
    assert _student(coach, s1.id).status_code == 200 and _student(coach, s2.id).status_code == 200
    assert _code(_join(_in(app, s3), tok)) == "COACH_FULL"
    assert _set(owner, cu.id, default_max=True).json()["coach"] == {"max_students": 4, "custom": False, "used": 2}
    assert _join(_in(app, s3), tok).status_code == 200
    rows = [(r["action"], r["target"], r["old"], r["new"]) for r in owner.get("/api/v1/admin/audit").json()["rows"]]
    assert ("user.coach_max", cu.id, None, 1) in rows and ("user.coach_max", cu.id, 1, None) in rows


def test_the_admin_page_refuses_odd_limits(app):
    cu, other = AF.many(2)
    _coach(app, cu)
    owner = _owner(app)
    for bad in (-1, 1001):
        r = _set(owner, cu.id, max_students=bad)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "OUT_OF_RANGE"
    r = _set(owner, other.id, max_students=3)                         # not a coach
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_A_COACH"
    st = _in(app, other)
    assert st.put(f"/api/v1/admin/users/{cu.id}/coach", json={"max_students": 9}, headers=st.tok).status_code == 403
    assert _set(owner, cu.id, max_students=0).json()["coach"]["max_students"] == 0      # 0: takes nobody new


def test_many_accepts_at_once_take_only_the_places_there_are(tmp_path, monkeypatch):
    """store.coach_accept_invite counts in the same write transaction as the insert (a fresh accounts.db,
    no HTTP)."""
    import threading
    from backend.accounts import store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(store, "_READY", set())
    coach = store.create_user(email="c@example.com", display_name="C")
    studs = [store.create_user(email=f"s{i}@example.com", display_name=f"S{i}") for i in range(8)]
    store.set_coach(coach.id, True, code="ABCDEFGH")
    store.set_coach_max(coach.id, 3)
    got, refused = [], []

    def go(s):
        try:
            got.append(store.coach_accept_invite(coach.id, s.id, 1, 4)["id"])
        except store.CoachRefused as e:
            refused.append(e.code)
    ts = [threading.Thread(target=go, args=(s,)) for s in studs]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(got) == 3 and refused == ["COACH_FULL"] * 5
