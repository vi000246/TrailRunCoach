"""SP-826: a coached student who changes a race, the phases or the 課表 gets their board summary marked
stale (the board says 更新中) and its computation queued at once — through the server's job queue, never
in the request (backend/coach_board.mark_stale, called by tenancy_mw after a 2xx write that
coach_board.changes_plan). A change made while the job runs is not lost: the job keeps the newer mark and
the scheduler's minute queues it again.

Made-up test accounts (accounts_fixtures.many, SP-611) in sign-in mode; nothing reads real data."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from backend import tenancy
from backend.tests import accounts_fixtures as AF
from backend.tests.test_coach_board_sp790 import _DS, _fresh, _put, _summary, _w  # noqa: F401 — the autouse fixture
from backend.tests.test_coach_edits_sp792 import plan_env  # noqa: F401 — fixture
from backend.tests.test_coach_view_sp791 import ON, _pair, _routes, _view
from backend.tests.test_login_sp58 import app, browser, c, csrf, env, started  # noqa: F401 — fixtures

API = "/api/v1/overview/plan"
EVENT = {"name": "新排的 50K", "date": "2027-03-14", "priority": "A", "kind": "trail"}

# every write that marks the summary (spelled out: widening or narrowing coach_board.PLAN_WRITES without
# changing this fails the walk below — and a new plan route has to be put on one side or the other).
# SP-972: PATCH /prefs (SP-955 設定 › 訓練依據: 目標依據 / 可跑坡目標 / 測試方式, fields PUT /prefs used to
# write) marks it too — a new 目標依據 re-plans the upcoming 課表 (plan_auto.after_thresholds) without
# rebuilding the Dataset, unlike the thresholds / zones SP-826 left out
CHANGES = {tuple(x.split()) for x in """
PUT /api/v1/plan/events|DELETE /api/v1/plan/events/x|POST /api/v1/plan/events/x/gpx|DELETE /api/v1/plan/events/x/gpx
PUT /api/v1/plan/events/x/gpx/splits|PUT /api/v1/plan/events/x/gpx/group|POST /api/v1/plan/events/x/gpx/cairn
PUT /api/v1/plan/phases
POST /api/v1/overview/plan/sessions|PATCH /api/v1/overview/plan/sessions/x|DELETE /api/v1/overview/plan/sessions/x
POST /api/v1/overview/plan/sessions/swap|POST /api/v1/overview/plan/sessions/expired/delete
POST /api/v1/overview/plan/sessions/x/revert|POST /api/v1/overview/plan/sessions/x/link
DELETE /api/v1/overview/plan/sessions/x/link
POST /api/v1/overview/plan/test-suggestions/schedule|POST /api/v1/overview/plan/suggestions/accept
POST /api/v1/overview/plan/reconcile|PUT /api/v1/overview/plan/prefs|PUT /api/v1/overview/plan/blackouts
PATCH /api/v1/overview/plan/prefs
POST /api/v1/overview/plan/rest-days|POST /api/v1/overview/plan/rest-days/move|DELETE /api/v1/overview/plan/rest-days/x
POST /api/v1/overview/plan/auto/run|POST /api/v1/overview/plan/auto/proposal/x/approve
POST /api/v1/overview/plan/auto/log/x/undo
""".replace("\n", "|").split("|") if x.strip()}


@pytest.fixture
def queued(monkeypatch):
    """The summary's jobs.submit captured: (key, priority, fn) — it runs only when a test runs it (every
    other job goes to the real queue: the Dataset build waits for its own)."""
    from backend import jobs
    out = []
    real = jobs.submit

    def submit(fn, **kw):
        if (kw.get("key") or ("",))[0] != "coach-summary":
            return real(fn, **kw)
        out.append((kw["key"], kw["priority"], fn))
    monkeypatch.setattr(jobs, "submit", submit)
    return out


def _coached(app):
    coach, stu, link = _pair(app, ON)
    return coach, stu, link


def _row(coach, sid):
    r = coach.get(f"/api/v1/coach/students/{sid}/summary")
    assert r.status_code == 200, r.text
    return r.json()


def _run(user, queued):
    """The queued job, as the queue would run it: in the student's tenant."""
    (_key, _prio, fn), = queued
    queued.clear()
    with tenancy.use(AF.tenant(user)):
        fn(None)


# ---------------------------------------------------------------------------- 驗收 1, 2: marked, queued, recomputed

def test_a_race_change_marks_the_summary_and_the_job_brings_the_new_race(app, queued):
    from backend import coach_board as CB
    coach, stu, _link = _coached(app)
    _put(stu.user, _summary(race_in=40))
    assert _row(coach, stu.user.id)["refreshing"] is False
    r = stu.put("/api/v1/plan/events", json=EVENT, headers=stu.tok)
    assert r.status_code == 200, r.text
    assert [(k, p) for k, p, _f in queued] == [(("coach-summary", f"u{stu.user.id}"), "warm")]
    with tenancy.use(AF.tenant(stu.user)):
        assert CB.read()["stale_at"] and CB.due() == "changed"
    row = _row(coach, stu.user.id)
    assert row["refreshing"] is True and row["next_race"]["name"] == "合歡山越野 50K"     # the old one until computed
    _run(stu.user, queued)
    row = _row(coach, stu.user.id)
    assert row["refreshing"] is False and row["next_race"]["name"] == "新排的 50K"
    with tenancy.use(AF.tenant(stu.user)):
        assert "stale_at" not in CB.read() and CB.due() is None


def test_a_schedule_change_marks_it_and_the_plan_completion_follows(app, queued, plan_env):
    """A 課表 edit (a session moved to another day) — the summary's plan part is read again by the job."""
    from backend import coach_board as CB
    coach, stu, _link = _coached(app)
    _put(stu.user, _summary())
    ss = stu.get(f"{API}/sessions")
    assert ss.status_code == 200, ss.text
    queued.clear()                                     # (reading the 課表 marks nothing)
    s = next(x for x in ss.json()["sessions"] if x["state"] == "active")
    r = stu.patch(f"{API}/sessions/{s['uid']}", json={"minutes": 45}, headers=stu.tok)
    assert r.status_code == 200, r.text
    assert len(queued) == 1 and _row(coach, stu.user.id)["refreshing"] is True
    _run(stu.user, queued)
    row = _row(coach, stu.user.id)
    assert row["refreshing"] is False
    with tenancy.use(AF.tenant(stu.user)):
        assert CB.read()["reason"] == "changed"


def test_the_request_computes_nothing(app, queued, monkeypatch):
    from backend import coach_board as CB
    from backend.api import overview as OV, wko5views as WV
    _coach, stu, _link = _coached(app)
    _put(stu.user, _summary())

    def boom(*a, **k):
        raise AssertionError("computed in the request")
    for mod, name in ((WV, "_dataset"), (OV, "_status"), (CB, "build"), (CB, "refresh_now")):
        monkeypatch.setattr(mod, name, boom)
    r = stu.put("/api/v1/plan/events", json=EVENT, headers=stu.tok)
    assert r.status_code == 200, r.text
    assert len(queued) == 1


# ---------------------------------------------------------------------------- 驗收 3: a change while it runs

def test_a_change_while_the_job_runs_is_computed_once_more(app, queued, monkeypatch):
    from backend import coach_board as CB
    from backend.api import wko5views as WV
    _coach, stu, _link = _coached(app)
    _put(stu.user, _summary())
    today = dt.date.today()
    monkeypatch.setattr(CB, "_status_of", lambda ds, today, compute: None)
    t = AF.tenant(stu.user)
    with tenancy.use(t):
        assert CB.mark_stale() is True
        queued.clear()

        def changed_meanwhile(*a, **k):                # the student saves again while the job builds
            assert CB.mark_stale() is True
            return _DS(today, [_w(0, today, 8, 100, 2400, 40)])
        monkeypatch.setattr(WV, "_dataset", changed_meanwhile)
        CB.refresh_now("changed")
        assert CB.read()["stale_at"] and CB.due() == "changed"          # the newer change still counts
    queued.clear()
    out = asyncio.run(CB.tick(submit=lambda fn, **kw: queued.append((kw["key"], kw["priority"], fn))))
    assert out == [(t.id, "changed")] and queued[0][1] == "warm"
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: _DS(today, []))
    _run(stu.user, queued)
    with tenancy.use(t):
        assert "stale_at" not in CB.read() and CB.due() is None


def test_after_a_warm_up_the_mark_goes_too(app, queued, monkeypatch):
    """The warm-up's summary (a sync) reads the plan as it is now: it clears a mark made before it started."""
    from backend import coach_board as CB
    _coach, stu, _link = _coached(app)
    _put(stu.user, _summary())
    monkeypatch.setattr(CB, "_status_of", lambda ds, today, compute: None)
    with tenancy.use(AF.tenant(stu.user)):
        CB.mark_stale()
        assert CB.after_warm_up(_DS(dt.date.today(), []), "sync-coros") is True
        assert "stale_at" not in CB.read()


# ---------------------------------------------------------------------------- 驗收 4: only what should

def test_no_coach_a_failed_write_or_a_preview_marks_nothing(app, queued):
    from backend import coach_board as CB
    from backend.tests.test_coaching_sp789 import _in
    _coach, stu, _link = _coached(app)
    lone_u = AF.many(1)[0]
    lone = _in(app, lone_u)
    assert lone.put("/api/v1/plan/events", json=EVENT, headers=lone.tok).status_code == 200
    assert queued == []
    with tenancy.use(AF.tenant(lone_u)):
        assert CB.read() is None
    _put(stu.user, _summary())
    bad = stu.put("/api/v1/plan/events", json={**EVENT, "date": "not a day"}, headers=stu.tok)
    assert bad.status_code >= 400
    assert stu.delete("/api/v1/plan/events/nope", headers=stu.tok).status_code == 404
    assert queued == []
    with tenancy.use(AF.tenant(stu.user)):
        assert "stale_at" not in CB.read()
    for method, path in (("POST", f"{API}/sessions/suggest"), ("POST", f"{API}/steps-preview"),
                         ("POST", f"{API}/blackouts/preview"), ("GET", "/api/v1/plan/events"),
                         ("POST", f"{API}/sessions/x/save-as-template"), ("POST", f"{API}/push-coros"),
                         ("PUT", "/api/v1/plan/thresholds")):
        assert CB.changes_plan(method, path) is False, (method, path)


def test_every_plan_write_route_is_on_one_side(app):
    """The app's write routes under the season plan and the 課表: exactly CHANGES mark the summary."""
    from backend import coach_board as CB
    writes = [(m, p) for m, p in _routes(app) if m in ("POST", "PUT", "PATCH", "DELETE")
              and (p.startswith("/api/v1/plan/") or p.startswith(API + "/"))]
    assert CHANGES <= set(writes), CHANGES - set(writes)
    assert {(m, p) for m, p in writes if CB.changes_plan(m, p)} == CHANGES


# ---------------------------------------------------------------------------- 驗收 5: the coach's change too

def test_a_coach_changing_the_students_race_marks_the_students_summary(app, queued):
    from backend import coach_board as CB
    coach, stu, _link = _coached(app)
    _put(stu.user, _summary())
    _put(coach.user, _summary())                       # the coach's own (not coached): never touched
    _view(coach, stu.user.id)
    r = coach.put("/api/v1/plan/events", json=EVENT, headers=coach.tok)
    assert r.status_code == 200, r.text
    assert [k for k, _p, _f in queued] == [("coach-summary", f"u{stu.user.id}")]
    with tenancy.use(AF.tenant(stu.user)):
        assert CB.read()["stale_at"]
    with tenancy.use(AF.tenant(coach.user)):
        assert "stale_at" not in CB.read()
