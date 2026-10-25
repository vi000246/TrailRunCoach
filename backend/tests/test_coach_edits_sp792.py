"""SP-792: a coach viewing a student (SP-791) changes their 課表, races, 不排課日 and training settings
(LTHR, CP, zones) — only the routes of the student's two switches (課表／賽事／不排課日, 門檻與訓練設定), each
switched off by the student in 設定 › 教練 from the very next request; every change is a line the student
reads (who, when, what) and the schedule shows the latest; a session the coach changes goes to the watch
exactly as when the student changes it (COROS is the scripted FakeHub of test_coros_workouts).

Every person is a made-up test account (accounts_fixtures.many) in sign-in mode; nothing real is read."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from backend.tests import accounts_fixtures as AF
from backend.tests.test_coach_view_sp791 import (  # noqa: F401 — fixtures and the route lists
    NO_SESSION, ON, OFF, WRITES_ANY, WRITES_PLAN, WRITES_TRAINING, _code, _fresh, _pair, _post, _race, _races,
    _routes, _view)
from backend.tests.test_login_sp58 import app, browser, c, csrf, env, started  # noqa: F401 — fixtures

API = "/api/v1/overview/plan"
EVENT = {"name": "教練排的比賽", "date": "2027-06-12", "priority": "A", "kind": "trail"}


def _edits(cl):
    r = cl.get("/api/v1/account/coach/edits")
    assert r.status_code == 200, r.text
    return r.json()["edits"]


def _perms(stu, link_id, **sw):
    return stu.put(f"/api/v1/account/coach/{link_id}/perms", json=sw, headers=stu.tok)


# ---------------------------------------------------------------------------- the acceptance criteria

def test_the_coach_changes_a_race_and_the_student_sees_it_and_who_did(app):
    """驗收 1: the coach, viewing, adds a race — the student signed in sees it, the session carries the
    line 「教練 X 於 … 修改」 and the log says who, when and what."""
    coach, stu, _link = _pair(app, ON)
    _race(stu.user, "學員的比賽")
    _view(coach, stu.user.id)
    r = coach.put("/api/v1/plan/events", json=EVENT, headers=coach.tok)
    assert r.status_code == 200, r.text
    _post(coach, "/api/v1/coach/view/stop")
    assert sorted(_races(stu)) == sorted(["學員的比賽", "教練排的比賽"])
    line = stu.get("/api/v1/session").json()["coach_edit"]
    assert line["coach"] == coach.user.display_name and line["action"] == "event_save" and line["at"]
    e = _edits(stu)
    assert [x["action"] for x in e] == ["view_end", "event_save", "view_start"]
    assert e[1]["coach"] == coach.user.display_name
    assert e[1]["params"]["name"] == "教練排的比賽" and e[1]["params"]["date"] == "2027-06-12"
    assert "coach_edit" not in coach.get("/api/v1/session").json()        # the coach's own: nothing


def test_switching_off_stops_the_coach_from_the_next_request(app):
    """驗收 2: the student turns 「改課表、賽事與不排課日」 off — the coach's next change of a race is refused
    (COACH_PERM_OFF, saying which switch) while the training settings stay open; then that one off too;
    each change of the switches is a consents row; nothing refused is in the log."""
    from backend.accounts import store
    coach, stu, link = _pair(app)                                  # the switches never set: both on
    _view(coach, stu.user.id)
    r = _perms(stu, link["id"], plan=False)
    assert r.status_code == 200, r.text
    me = r.json()["coaches"][0]
    assert me["perms"] == {"plan": False, "training": True, "injury": False}      # SP-822's switch: off
    assert set(r.json()["perm_labels"]) == {"plan", "training", "injury"}
    w = coach.put("/api/v1/plan/events", json=EVENT, headers=coach.tok)
    assert w.status_code == 403 and _code(w) == "COACH_PERM_OFF"
    assert "改課表、賽事與不排課日" in w.json()["detail"]["message"]
    assert coach.get("/api/v1/session").json()["caps"] == ["thresholds.write"]
    th = [{"date": "2026-10-01", "lthr": 168, "cp": 255}]
    ok = coach.put("/api/v1/plan/thresholds", json=th, headers=coach.tok)
    assert ok.status_code == 200, ok.text
    _perms(stu, link["id"], training=False)
    no = coach.put("/api/v1/plan/thresholds", json=th, headers=coach.tok)
    assert no.status_code == 403 and _code(no) == "COACH_PERM_OFF"
    assert coach.get("/api/v1/session").json()["caps"] == []
    _perms(stu, link["id"], plan=True)
    assert coach.put("/api/v1/plan/events", json=EVENT, headers=coach.tok).status_code == 200
    rows = [c for c in store.consents(stu.user.id) if c.get("action") == "coach_perms"]
    assert len(rows) == 3
    assert [x["action"] for x in _edits(stu)] == ["event_save", "thresholds", "view_start"]
    thr = _edits(stu)[1]["params"]
    assert thr["lthr"] == 168 and thr["cp"] == 255 and thr["date"] == "2026-10-01"


def test_only_the_student_sets_the_switches(app):
    coach, stu, link = _pair(app, ON)
    other, = AF.many(1)
    from backend.tests.test_coach_view_sp791 import _in
    oth = _in(app, other)
    assert _code(_perms(oth, link["id"], plan=False)) == "NO_LINK"           # not their link
    assert _code(_perms(coach, link["id"], plan=False)) == "NO_LINK"         # the coach is not the student
    for bad in ({}, {"plan": "no"}, {"push": True}, {"plan": 1}):
        r = stu.put(f"/api/v1/account/coach/{link['id']}/perms", json=bad, headers=stu.tok)
        assert r.status_code == 400 and _code(r) == "BAD_PERM", bad
    _view(coach, stu.user.id)                                                 # viewing: /account/ is closed
    r = _perms(coach, link["id"], plan=False)
    assert r.status_code == 403 and _code(r) == "COACH_VIEW_READONLY"
    assert stu.get("/api/v1/account/coach").json()["coaches"][0]["perms"] == ON


def test_with_both_switches_on_only_their_routes_open(app):
    """The walk of SP-791 with both switches on: the switches' routes and WRITES_ANY get past the gate,
    every other write is still COACH_VIEW_READONLY."""
    coach, stu, _link = _pair(app, ON)
    _view(coach, stu.user.id)
    bad = []
    for m, p in _routes(app):
        if m == "GET" or (m, p) in NO_SESSION or (m, p) in {("POST", "/api/v1/account/logout"),
                                                             ("POST", "/api/v1/coach/view/stop")}:
            continue
        r = coach.request(m, p, json={}, headers=coach.tok)
        code = _code(r)
        if (m, p) in WRITES_ANY | WRITES_PLAN | WRITES_TRAINING:
            if r.status_code == 403 and str(code).startswith("COACH_"):
                bad.append((m, p, r.status_code, code))
        elif not (r.status_code == 403 and code == "COACH_VIEW_READONLY"):
            bad.append((m, p, r.status_code, code))
    assert bad == []


def test_a_refused_or_failed_change_is_not_in_the_log(app):
    coach, stu, _link = _pair(app, ON)
    _view(coach, stu.user.id)
    r = coach.put("/api/v1/plan/events", json={"name": "x"}, headers=coach.tok)          # 422: no date
    assert r.status_code == 422
    assert coach.put("/api/v1/plan/profile", json={"weight": 50}, headers=coach.tok).status_code == 403
    assert [x["action"] for x in _edits(stu)] == ["view_start"]


def test_the_student_reads_the_switches_and_their_words(app):
    coach, stu, _link = _pair(app)
    mine = stu.get("/api/v1/account/coach").json()
    assert mine["coaches"][0]["perms"] == ON                       # never set: the defaults (SP-56 Q3)
    assert mine["perm_labels"]["training"] == "改門檻與訓練設定（LTHR、CP、區間）"
    assert coach.get(f"/api/v1/coach/students/{stu.user.id}").json()["perms"] == ON


# ---------------------------------------------------------------------------- the watch (影響大)

BASE = "https://teameuapi.coros.com"


@pytest.fixture
def plan_env(monkeypatch):
    """The 課表's inputs fixed (test_plan_store's week) and COROS's real today pinned, like test_watch_sync_sp358."""
    from backend.api import plan_sessions
    from backend.sync import coros_workouts as CW
    from backend.tests.test_plan_store import g, inputs, next_week
    inp = inputs(weeks=[next_week(), {**next_week(provisional=True), "start": "2026-10-12", "sessions": [
        g("long", "long", "LSD（山路）", 140, "2026-10-18")]}], horizon="2026-10-18")
    monkeypatch.setattr(plan_sessions, "_compute_inputs", lambda *a, **k: inp)
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    return inp


def _coros_login(user):
    """The student's COROS sign-in in their own tenant's DB (the FakeHub's token), as after 設定 › 資料同步."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from backend.db.models import SyncState
    from backend.settings.secrets import seal
    t = AF.tenant(user)
    eng = create_engine(f"sqlite:///{t.root / 'wko5coach.db'}")
    try:
        with Session(eng) as s:
            s.merge(SyncState(athlete_id=1, coros_access_token=seal("tok"), coros_base_url=BASE, coros_user_id="u1",
                              coros_token_expires=datetime.now(timezone.utc) + timedelta(hours=5)))
            s.commit()
    finally:
        eng.dispose()


def _days(fake):
    return sorted(e["happenDay"] for e in fake.entities)


def _drag(app, by_coach: bool):
    """The student pushes this week to the watch, then the Friday easy run is dragged to Saturday — by the
    student themselves or by their coach viewing them. What the watch has before and after, the answer's
    COROS status, the programs left on the watch."""
    from backend.sync import http
    from backend.tests.test_coros_workouts import FakeHub
    coach, stu, _link = _pair(app, ON)
    _coros_login(stu.user)
    fake = FakeHub()
    with http.use_transport(httpx.MockTransport(fake)):
        ss = stu.get(f"{API}/sessions")
        assert ss.status_code == 200, ss.text
        r = stu.post(f"{API}/push-coros?scope=week", headers=stu.tok)
        assert r.status_code == 200, r.text
        before = _days(fake)
        easy = next(s for s in ss.json()["sessions"] if s["day"] == "2026-10-02" and s["state"] == "active")
        actor = stu
        if by_coach:
            _view(coach, stu.user.id)
            actor = coach
        r = actor.patch(f"{API}/sessions/{easy['uid']}", json={"day": "2026-10-03"}, headers=actor.tok)
        assert r.status_code == 200, r.text
        after = _days(fake)
        calls = [p for _m, p in fake.calls]
    return {"before": before, "after": after, "status": r.json()["coros"]["status"], "live": len(fake.live()),
            "calls": calls, "student": stu, "coach": coach}


def test_a_session_the_coach_moves_goes_to_the_watch_as_when_the_student_moves_it(app, plan_env):
    """影響大: the coach's drag moves the watch's copy exactly as the student's own drag does — off Friday,
    on Saturday, the old program deleted, the same COROS calls in the same order; the student's log says so."""
    own = _drag(app, by_coach=False)
    coached = _drag(app, by_coach=True)
    assert own["before"] == coached["before"] == [20261001, 20261002, 20261004]
    assert own["after"] == coached["after"] == [20261001, 20261003, 20261004]
    assert own["status"] == coached["status"] == "ok"
    assert own["live"] == coached["live"] == 3
    assert own["calls"] == coached["calls"]
    e = _edits(coached["student"])
    assert e[0]["action"] == "session_edit" and e[0]["params"].get("fields") == "day"
    assert e[0]["params"].get("day") == "2026-10-03"


def test_a_coach_edit_with_the_switch_off_never_reaches_the_watch(app, plan_env):
    from backend.sync import http
    from backend.tests.test_coros_workouts import FakeHub
    coach, stu, link = _pair(app, ON)
    _coros_login(stu.user)
    fake = FakeHub()
    with http.use_transport(httpx.MockTransport(fake)):
        ss = stu.get(f"{API}/sessions").json()["sessions"]
        assert stu.post(f"{API}/push-coros?scope=week", headers=stu.tok).status_code == 200
        n = len(fake.calls)
        _perms(stu, link["id"], plan=False)
        _view(coach, stu.user.id)
        easy = next(s for s in ss if s["day"] == "2026-10-02" and s["state"] == "active")
        r = coach.patch(f"{API}/sessions/{easy['uid']}", json={"day": "2026-10-03"}, headers=coach.tok)
        assert r.status_code == 403 and _code(r) == "COACH_PERM_OFF"
        assert coach.post(f"{API}/push-coros?scope=week", headers=coach.tok).status_code == 403
        assert len(fake.calls) == n and _days(fake) == [20261001, 20261002, 20261004]
