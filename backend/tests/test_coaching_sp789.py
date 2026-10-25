"""SP-789: a coach and their students — the invite link and the coach code, the student's yes, the
student withdrawing (the coach's very next request is refused), the coach letting go, the admin taking
the coach away; consents rows and 下載我的資料; deleting an account; and above all that a coach reaches
a student only through an active link (accounts/coaching.student_of), whatever id a request names.

Every person is a made-up test account (accounts_fixtures.many, SP-611) signed in through its one-time
link, in sign-in mode (test_login_sp58's app): nothing here reads real data."""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from backend import site_config as SC, site_settings as SS
from backend.tests import accounts_fixtures as AF
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, started)

_IP = iter(f"198.51.100.{i}" for i in range(40, 250))


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
    return cl.post(url, json=body or {}, headers=cl.tok)


def _coach(app, user):
    from backend.accounts import coaching
    assert coaching.set_coach(user.id, True, by=1)
    return _in(app, user)


def _token(cl) -> str:
    r = _post(cl, "/api/v1/coach/invite")
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.endswith("#coach") and "/api/v1/app/settings?coach_token=" in url
    return parse_qs(urlsplit(url).query)["coach_token"][0]


def _join(student_cl, token):
    return _post(student_cl, "/api/v1/account/coach/invite/accept", {"token": token})


def _student(coach_cl, sid):
    return coach_cl.get(f"/api/v1/coach/students/{sid}")


def _code(r) -> str:
    return r.json()["detail"]["code"]


def _consents(uid: int, client="coach") -> list:
    from backend.accounts import store
    return [(c["action"], json.loads(c["documents"])) for c in store.consents(uid) if c.get("client") == client]


# ---------------------------------------------------------------------------- the acceptance criteria

def test_accepted_the_coach_lists_the_student_and_a_withdrawal_stops_the_next_request(app):
    """驗收 1: the student accepts → in the coach's list, readable; withdraws → the coach's very next
    request about them is 403, and the list no longer has them."""
    coach_u, stu_u = AF.many(2)
    coach, stu = _coach(app, coach_u), _in(app, stu_u)
    tok = _token(coach)
    peek = _post(stu, "/api/v1/account/coach/invite", {"token": tok})
    assert peek.status_code == 200 and peek.json() == {"coach": {"name": coach_u.display_name}}
    assert _student(coach, stu_u.id).status_code == 403            # looking is not saying yes
    r = _join(stu, tok)
    assert r.status_code == 200, r.text
    mine = r.json()["coaches"]
    assert [(m["name"], m["status"], m["initiated_by"]) for m in mine] == [(coach_u.display_name, "active", "coach")]
    home = coach.get("/api/v1/coach").json()
    assert [(s["student_id"], s["status"]) for s in home["students"]] == [(stu_u.id, "active")]
    one = _student(coach, stu_u.id)
    assert one.status_code == 200 and one.json()["name"] == stu_u.display_name
    # the student withdraws: the coach's next request is refused, nothing cached
    end = _post(stu, f"/api/v1/account/coach/{mine[0]['link_id']}/end")
    assert end.status_code == 200 and end.json()["coaches"] == []
    gone = _student(coach, stu_u.id)
    assert gone.status_code == 403 and _code(gone) == "NOT_YOUR_STUDENT"
    assert coach.get("/api/v1/coach").json()["students"] == []


def test_the_accept_and_the_withdrawal_are_in_the_students_download(app):
    """驗收 2: 下載我的資料 has the coach accepted and ended (consents) and the link itself."""
    coach_u, stu_u = AF.many(2)
    coach, stu = _coach(app, coach_u), _in(app, stu_u)
    link = _join(stu, _token(coach)).json()["coaches"][0]["link_id"]
    _post(stu, f"/api/v1/account/coach/{link}/end")
    r = stu.get("/api/v1/account/export.zip")
    assert r.status_code == 200, r.text
    acct = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("account.json"))
    rows = [(x["action"], x.get("client"), x["documents"].get("coach_name"), x["documents"].get("by"))
            for x in acct["consents"] if x.get("client") == "coach"]
    assert rows == [("coach_accept", "coach", coach_u.display_name, None),
                    ("coach_end", "coach", coach_u.display_name, "student")]
    mine = acct["coaching"]["my_coaches"]
    assert [(m["name"], m["status"], m["ended_by"]) for m in mine] == [(coach_u.display_name, "ended", "student")]
    assert acct["coaching"]["coach"] is None and acct["coaching"]["my_students"] == []
    raw = zipfile.ZipFile(io.BytesIO(r.content)).read("account.json").decode()
    assert "coach_token" not in raw and (coach_u.email or "@") not in raw
    # the coach's own download lists the student by name, and their code
    cz = zipfile.ZipFile(io.BytesIO(coach.get("/api/v1/account/export.zip").content))
    cacct = json.loads(cz.read("account.json"))
    assert cacct["coaching"]["coach"]["code"] == coach.get("/api/v1/coach").json()["code"]
    assert [m["name"] for m in cacct["coaching"]["my_students"]] == [stu_u.display_name]


# ---------------------------------------------------------------------------- the coach code (the student asks)

def test_a_code_request_waits_for_the_coach_and_reads_nothing_until_accepted(app):
    from backend.accounts import store
    coach_u, stu_u = AF.many(2)
    coach, stu = _coach(app, coach_u), _in(app, stu_u)
    code = coach.get("/api/v1/coach").json()["code"]
    look = _post(stu, "/api/v1/account/coach/code", {"code": code.lower()[:4] + "-" + code.lower()[4:]})
    assert look.status_code == 200 and look.json()["coach"]["name"] == coach_u.display_name
    r = _post(stu, "/api/v1/account/coach/request", {"code": code})
    assert r.status_code == 200 and r.json()["coaches"][0]["status"] == "pending"
    assert _student(coach, stu_u.id).status_code == 403              # pending: nothing to read
    assert _consents(stu_u.id) == []                                  # nothing granted yet
    req = coach.get("/api/v1/coach").json()["students"][0]
    assert (req["status"], req["initiated_by"]) == ("pending", "student")
    ok = _post(coach, f"/api/v1/coach/requests/{req['link_id']}/accept")
    assert ok.status_code == 200 and ok.json()["students"][0]["status"] == "active"
    assert _student(coach, stu_u.id).status_code == 200
    acts = _consents(stu_u.id)
    assert [a for a, _d in acts] == ["coach_accept"] and acts[0][1]["initiated_by"] == "student" and acts[0][1]["asked_at"]
    # the coach lets the student go: 403 from the next request, a consents row by the coach
    assert _post(coach, f"/api/v1/coach/links/{req['link_id']}/end").status_code == 200
    assert _student(coach, stu_u.id).status_code == 403
    assert [(a, d.get("by")) for a, d in _consents(stu_u.id)] == [("coach_accept", None), ("coach_end", "coach")]
    assert store.coach_link(req["link_id"])["ended_by"] == "coach"


def test_a_declined_or_cancelled_request_never_opens_anything(app):
    coach_u, s1, s2 = AF.many(3)
    coach, a, b = _coach(app, coach_u), _in(app, s1), _in(app, s2)
    code = coach.get("/api/v1/coach").json()["code"]
    la = _post(a, "/api/v1/account/coach/request", {"code": code}).json()["coaches"][0]["link_id"]
    lb = _post(b, "/api/v1/account/coach/request", {"code": code}).json()["coaches"][0]["link_id"]
    assert _post(coach, f"/api/v1/coach/links/{la}/end").status_code == 200       # declined
    assert _post(b, f"/api/v1/account/coach/{lb}/end").status_code == 200         # cancelled by the student
    for sid, lid in ((s1.id, la), (s2.id, lb)):
        assert _student(coach, sid).status_code == 403
        r = _post(coach, f"/api/v1/coach/requests/{lid}/accept")
        assert r.status_code == 409 and _code(r) == "NO_REQUEST"
    assert _consents(s1.id) == [] and _consents(s2.id) == []


def test_guessing_codes_is_limited(app):
    stu = _in(app, AF.many(1)[0])
    seen = [_post(stu, "/api/v1/account/coach/code", {"code": f"ZZZZZZZ{c}"}).status_code for c in "ABCDEFGHJKLM"]
    assert seen[:10] == [404] * 10 and seen[10:] == [429, 429]


# ---------------------------------------------------------------------------- nobody else's students

def test_a_coach_reaches_only_their_active_students_whatever_the_id(app):
    """越權: another coach's student, someone with no coach, a pending one, an ended one, an id that is
    no account, the coach's own id, a non-number — every one 403 with the same code, nothing told."""
    from backend.accounts import coaching, store
    ca, cb, s1, s2, s3, s4, s5 = AF.many(7)
    A, B = _coach(app, ca), _coach(app, cb)
    _join(_in(app, s1), _token(A))
    _join(_in(app, s2), _token(B))
    _post(_in(app, s3), "/api/v1/account/coach/request", {"code": A.get("/api/v1/coach").json()["code"]})
    st4 = _in(app, s4)
    l4 = _join(st4, _token(A)).json()["coaches"][0]["link_id"]
    _post(st4, f"/api/v1/account/coach/{l4}/end")
    assert _student(A, s1.id).status_code == 200
    for sid in (s2.id, s3.id, s4.id, s5.id, ca.id, cb.id, 1, 999999, 2 ** 62, 2 ** 70, 0, -3, "abc", "1.5"):
        r = _student(A, sid)
        assert r.status_code == 403 and _code(r) == "NOT_YOUR_STUDENT", (sid, r.status_code, r.text)
    # B's view never shows A's students, and the other way round
    assert [s["student_id"] for s in B.get("/api/v1/coach").json()["students"]] == [s2.id]
    assert {s["student_id"] for s in A.get("/api/v1/coach").json()["students"]} == {s1.id, s3.id}
    # the gate function itself, for SP-790 / SP-791
    with pytest.raises(coaching.NotYourStudent):
        coaching.student_of(store.get_user(ca.id), s2.id)
    assert coaching.student_of(store.get_user(ca.id), str(s1.id)).id == s1.id


def test_someone_who_is_not_a_coach_gets_nothing(app):
    coach_u, stu_u, other_u = AF.many(3)
    coach, stu, other = _coach(app, coach_u), _in(app, stu_u), _in(app, other_u)
    _join(stu, _token(coach))
    for cl in (stu, other):
        for r in (cl.get("/api/v1/coach"), _student(cl, stu_u.id), _post(cl, "/api/v1/coach/invite"),
                  _post(cl, "/api/v1/coach/code")):
            assert r.status_code == 403 and _code(r) == "NOT_A_COACH", r.text


def test_nobody_ends_or_accepts_a_link_that_is_not_theirs(app):
    ca, cb, s1, s2 = AF.many(4)
    A, B, st1, st2 = _coach(app, ca), _coach(app, cb), _in(app, s1), _in(app, s2)
    l1 = _join(st1, _token(A)).json()["coaches"][0]["link_id"]
    l2 = _post(st2, "/api/v1/account/coach/request", {"code": A.get("/api/v1/coach").json()["code"]}).json()["coaches"][0]["link_id"]
    assert _code(_post(st2, f"/api/v1/account/coach/{l1}/end")) == "NO_LINK"     # another student's link
    assert _code(_post(B, f"/api/v1/coach/links/{l1}/end")) == "NO_LINK"         # another coach's student
    assert _code(_post(B, f"/api/v1/coach/requests/{l2}/accept")) == "NO_REQUEST"
    assert _code(_post(st1, f"/api/v1/account/coach/{l1 + 1000}/end")) == "NO_LINK"
    assert _student(A, s1.id).status_code == 200 and _student(B, s2.id).status_code == 403


def test_signed_out_gets_nothing(app):
    out = browser(app, ip=next(_IP))
    assert out.get("/api/v1/coach").status_code == 401
    assert out.get("/api/v1/account/coach").status_code == 401
    assert out.get("/api/v1/coach/students/2").status_code == 401


def test_owner_mode_and_the_demo_have_no_coach_routes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.tests.test_login_sp58 import _clean_env
    _clean_env(monkeypatch, tmp_path)
    with TestClient(build_app(demo=False)) as cl:                     # owner mode: every request the owner
        assert cl.get("/api/v1/account/coach").status_code == 404
        assert cl.get("/api/v1/coach").status_code == 404
    demo = build_app(demo=True)
    assert not [r for r in demo.routes if "/coach" in getattr(r, "path", "")]


# ---------------------------------------------------------------------------- one coach, the invite link

def test_one_coach_per_student_in_the_first_version(app):
    ca, cb, s = AF.many(3)
    A, B, st = _coach(app, ca), _coach(app, cb), _in(app, s)
    code_b = B.get("/api/v1/coach").json()["code"]
    lid = _post(st, "/api/v1/account/coach/request", {"code": code_b}).json()["coaches"][0]["link_id"]
    r = _join(st, _token(A))                                    # a request waiting: no second coach
    assert r.status_code == 409 and _code(r) == "REQUEST_PENDING"
    _post(st, f"/api/v1/account/coach/{lid}/end")
    assert _join(st, _token(A)).status_code == 200
    r = _post(st, "/api/v1/account/coach/request", {"code": code_b})
    assert r.status_code == 409 and _code(r) == "HAS_COACH"
    assert _code(_join(st, _token(B))) == "HAS_COACH"


def test_a_request_accepted_after_the_student_found_another_coach_is_refused(app):
    from backend.accounts import store
    ca, cb, s = AF.many(3)
    A, B, st = _coach(app, ca), _coach(app, cb), _in(app, s)
    lid = _post(st, "/api/v1/account/coach/request", {"code": B.get("/api/v1/coach").json()["code"]}).json()["coaches"][0]["link_id"]
    # meanwhile (another tab) the student took A's invite: a race the transaction settles
    store.coach_end(lid, "student", s.id)
    assert _join(st, _token(A)).status_code == 200
    with sqlite3.connect(str(store.path())) as con:                 # the old request back to pending by hand
        con.execute("UPDATE coach_links SET status = 'pending', ended_at = NULL, ended_by = NULL WHERE id = ?", (lid,))
    r = _post(B, f"/api/v1/coach/requests/{lid}/accept")
    assert r.status_code == 409 and _code(r) == "HAS_COACH"
    assert _student(B, s.id).status_code == 403


def test_nobody_coaches_themselves(app):
    cu = AF.many(1)[0]
    me = _coach(app, cu)
    tok = _token(me)
    assert _code(_post(me, "/api/v1/account/coach/invite", {"token": tok})) == "SELF"
    assert _code(_join(me, tok)) == "SELF"
    assert _code(_post(me, "/api/v1/account/coach/request", {"code": me.get("/api/v1/coach").json()["code"]})) == "SELF"


def test_a_new_or_stopped_invite_link_ends_the_old_one(app, monkeypatch):
    from backend.accounts import store
    cu, s1, s2, s3 = AF.many(4)
    coach = _coach(app, cu)
    old = _token(coach)
    new = _token(coach)
    st1 = _in(app, s1)
    r = _join(st1, old)
    assert r.status_code == 404 and _code(r) == "INVITE_INVALID"
    assert _join(st1, new).status_code == 200
    assert _join(_in(app, s2), new).status_code == 200               # one link, many students
    assert coach.delete("/api/v1/coach/invite", headers=coach.tok).json()["invite"] is None
    assert _code(_join(_in(app, s3), new)) == "INVITE_INVALID"
    assert _code(_post(st1, "/api/v1/account/coach/invite", {"token": "x" * 500})) == "INVITE_INVALID"
    # an expired one
    tok = _token(coach)
    with sqlite3.connect(str(store.path())) as con:
        con.execute("UPDATE coach_invites SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert _code(_join(_in(app, s3), tok)) == "INVITE_INVALID"
    # only its hash is kept
    with sqlite3.connect(str(store.path())) as con:
        assert tok not in json.dumps(con.execute("SELECT * FROM coach_invites").fetchall())


def test_a_new_code_ends_the_old_one(app):
    cu, s = AF.many(2)
    coach, st = _coach(app, cu), _in(app, s)
    old = coach.get("/api/v1/coach").json()["code"]
    new = _post(coach, "/api/v1/coach/code").json()["code"]
    assert new != old
    assert _post(st, "/api/v1/account/coach/code", {"code": old}).status_code == 404
    assert _post(st, "/api/v1/account/coach/code", {"code": new}).status_code == 200


# ---------------------------------------------------------------------------- the admin, disabled accounts

def test_the_admin_taking_the_coach_away_ends_every_link(app):
    from backend.accounts import store
    cu, s1, s2 = AF.many(3)
    coach, st1, st2 = _coach(app, cu), _in(app, s1), _in(app, s2)
    _join(st1, _token(coach))
    _post(st2, "/api/v1/account/coach/request", {"code": coach.get("/api/v1/coach").json()["code"]})
    owner = browser(app, ip=next(_IP))
    assert location(sign_in(owner, "g-owner", OWNER_EMAIL)) == "/"
    r = owner.put(f"/api/v1/admin/users/{cu.id}/coach", json={"coach": False}, headers={"X-TRC-CSRF": csrf(owner)})
    assert r.status_code == 200 and r.json()["coach"] is None
    assert _student(coach, s1.id).status_code == 403                 # NOT_A_COACH first
    assert st1.get("/api/v1/account/coach").json()["coaches"] == []
    assert st2.get("/api/v1/account/coach").json()["coaches"] == []
    assert [(a, d.get("by")) for a, d in _consents(s1.id)] == [("coach_accept", None), ("coach_end", "admin")]
    assert _consents(s2.id) == []
    assert store.coach_invite_of(cu.id) is None
    # made a coach again: nothing comes back by itself
    assert owner.put(f"/api/v1/admin/users/{cu.id}/coach", json={"coach": True},
                     headers={"X-TRC-CSRF": csrf(owner)}).json()["coach"]["used"] == 0
    assert _student(coach, s1.id).status_code == 403
    rows = [(r["action"], r["target"], r["old"], r["new"]) for r in owner.get("/api/v1/admin/audit").json()["rows"]]
    assert ("user.coach", cu.id, True, False) in rows and ("user.coach", cu.id, False, True) in rows


def test_only_an_admin_makes_coaches(app):
    cu, s = AF.many(2)
    st = _in(app, s)
    r = st.put(f"/api/v1/admin/users/{cu.id}/coach", json={"coach": True}, headers=st.tok)
    assert r.status_code == 403
    from backend.accounts import store
    assert store.coach_profile(cu.id) is None


def test_a_disabled_student_or_coach_is_not_readable(app):
    from backend.accounts import coaching, store
    cu, s = AF.many(2)
    coach, st = _coach(app, cu), _in(app, s)
    _join(st, _token(coach))
    store.set_disabled(s.id, True)
    assert _student(coach, s.id).status_code == 403
    store.set_disabled(s.id, False)
    assert _student(coach, s.id).status_code == 200
    store.set_disabled(cu.id, True)
    with pytest.raises(coaching.NotYourStudent):
        coaching.student_of(store.get_user(cu.id), s.id)


# ---------------------------------------------------------------------------- the interface for SP-790 / SP-791

def test_in_student_opens_the_students_tenant_only_after_the_check(app):
    import asyncio
    from backend import tenancy
    from backend.accounts import coaching, store
    cu, s, other = AF.many(3)
    coach = _coach(app, cu)
    _join(_in(app, s), _token(coach))
    me = store.get_user(cu.id)

    async def go(sid):
        async with coaching.in_student(me, sid) as t:
            return t.id, tenancy.current().id
    assert asyncio.run(go(s.id)) == (f"u{s.id}", f"u{s.id}")
    with pytest.raises(coaching.NotYourStudent):
        asyncio.run(go(other.id))
    assert coaching.allows(me, s.id, "plan:edit") is False           # SP-792: no switch yet


def test_the_coach_api_never_opens_a_tenant_by_an_id_itself():
    """Static guard: api/coach.py reaches a student only through coaching (student_of / in_student)."""
    src = (Path(__file__).resolve().parents[1] / "api" / "coach.py").read_text("utf-8")
    for bad in ("tenant_of(", "get_user(", "tenancy.use(", "set_current("):
        assert bad not in src, bad


# ---------------------------------------------------------------------------- deleting an account, the table

def test_deleting_a_coach_or_a_student_leaves_no_coaching_row(app):
    from backend.accounts import store
    cu, s1, s2 = AF.many(3)
    coach, st1, st2 = _coach(app, cu), _in(app, s1), _in(app, s2)
    _join(st1, _token(coach))
    _post(st2, "/api/v1/account/coach/request", {"code": coach.get("/api/v1/coach").json()["code"]})
    out = store.delete_user(cu.id)
    assert out["coach_links"] == 2 and out["coach_invites"] == 1 and out["coach_profiles"] == 1
    with sqlite3.connect(str(store.path())) as con:
        assert con.execute("SELECT count(*) FROM coach_links").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM coach_profiles WHERE user_id = ?", (cu.id,)).fetchone()[0] == 0
    # the student's history stays, without the deleted coach's name
    acts = _consents(s1.id)
    assert [a for a, _d in acts] == ["coach_accept"] and acts[0][1]["coach_name"] is None
    assert st1.get("/api/v1/account/coach").json()["coaches"] == []
    # a deleted student: their links go with them
    c2 = AF.many(1)[0]
    coach2 = _coach(app, c2)
    assert _join(st2, _token(coach2)).status_code == 200
    assert store.delete_user(s2.id)["coach_links"] == 1
    assert coach2.get("/api/v1/coach").json()["students"] == []


def test_the_tables_are_made_on_an_older_accounts_db(tmp_path, monkeypatch):
    """CREATE TABLE IF NOT EXISTS on start: a file from before SP-789 gets the three tables, twice is
    harmless, the existing tables untouched."""
    from backend.accounts import store
    p = tmp_path / "accounts.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL DEFAULT 'user', "
                "google_sub TEXT UNIQUE, email TEXT, email_verified INTEGER NOT NULL DEFAULT 0, display_name TEXT, "
                "tenant_dir TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_login_at TEXT, disabled_at TEXT, "
                "disabled_reason TEXT, plan TEXT NOT NULL DEFAULT 'free', plan_expires_at TEXT)")
    con.execute("INSERT INTO users(id, role, tenant_dir, created_at) VALUES (1, 'owner', '/x', '2026-01-01')")
    con.commit()
    con.close()
    monkeypatch.setattr(store, "DB_PATH", p)
    monkeypatch.setattr(store, "_READY", set())
    store.init()
    store._READY.clear()
    store.init()
    con = sqlite3.connect(str(p))
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert {"coach_profiles", "coach_links", "coach_invites", "coach_links_live"} <= names
    assert con.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    con.close()
