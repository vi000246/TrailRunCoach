"""SP-790: the coach's board — a read-only summary of each active student, computed ahead and kept
in the student's own tenant (backend/coach_board.py), read through coaching.in_student
(api/coach_board.py): 403 for anyone who is not this coach's active student, whatever the id, and
from the very next request after a withdrawal; opening the board never builds a Dataset.

Every person is a made-up test account (accounts_fixtures.many, SP-611) in sign-in mode
(test_login_sp58's app); the summaries are written by hand or from a stand-in Dataset — nothing
here reads real data."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
from pathlib import Path

import pytest

from backend import site_config as SC, site_settings as SS, tenancy
from backend.tests import accounts_fixtures as AF
from backend.tests.test_coaching_sp789 import _code, _coach, _in, _join, _post, _token
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, started)

_IP = iter(f"198.51.100.{i}" for i in range(10, 40))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from backend import coach_board
    from backend.accounts import coaching
    monkeypatch.setattr(SS, "PATH", None)
    monkeypatch.setattr(SC, "PATH", None)
    monkeypatch.setenv("WKO5COACH_NO_WARMUP", "1")
    SS.reset_cache()
    coaching.reset_limits()
    coach_board._FAILED.clear()
    yield
    SS.reset_cache()
    coaching.reset_limits()
    coach_board._FAILED.clear()


def _today() -> dt.date:
    return dt.date.today()


def _summary(sessions7=3, km7=31.5, ctl=48.2, tsb=-12.4, race_in=40, alerts=(), locale="zh-TW", day=None,
             plan28=(10, 8)) -> dict:
    from backend import coach_board as CB
    t = day or _today()
    return {"v": CB.VERSION, "computed_at": "2026-10-20T03:00:00+00:00", "day": t.isoformat(), "reason": "test",
            "volume": {"d7": {"sessions": sessions7, "moving_s": 15000, "distance_km": km7, "climbing_m": 1200, "tss": 310},
                       "d28": {"sessions": 14, "moving_s": 60000, "distance_km": 120.0, "climbing_m": 4800, "tss": 1250}},
            "load": {"ctl": ctl, "atl": ctl - tsb, "tsb": tsb},
            "next_race": {"name": "合歡山越野 50K", "date": (t + dt.timedelta(days=race_in)).isoformat(), "days": 1,
                          "priority": "A", "kind": "race", "distance_km": 50.0, "climbing_m": 3000},
            "plan": {"d28": {"due": plan28[0], "done": plan28[1], "rate": round(plan28[1] / plan28[0], 3)},
                     "week": {"planned": 5, "done": 2, "start": t.isoformat()}},
            "last_activity": t.isoformat(),
            "alerts": [{"id": i, "level": lv, "title": title} for i, lv, title in alerts],
            "status_counts": {"bad": 0, "watch": 0, "locale": locale}}


def _tenant(user):
    from backend.accounts import store, tenants as TN
    return TN.tenant_of(store.get_user(user.id))


def _put(user, s: dict, last_ok: str | None = "now") -> None:
    """A stored summary (and a sync that worked) in this student's own tenant."""
    from backend import coach_board as CB
    t = AF.tenant(user)
    with tenancy.use(t):
        CB.write(s)
        if last_ok is not None:
            at = dt.datetime.now(dt.timezone.utc).isoformat() if last_ok == "now" else last_ok
            con = sqlite3.connect(str(t.root / "wko5coach.db"))
            try:
                con.execute("INSERT OR REPLACE INTO user_settings(user_id, key, value_json, updated_at) VALUES (1, ?, ?, ?)",
                            ("sync.coros.last_ok", json.dumps({"at": at, "trigger": "test", "downloaded": 1}), at))
                con.commit()
            finally:
                con.close()


def _board(cl):
    return cl.get("/api/v1/coach/board")


def _one(cl, sid):
    return cl.get(f"/api/v1/coach/students/{sid}/summary")


# ---------------------------------------------------------------------------- 驗收 1: one row per student

def test_the_board_shows_each_active_student_with_their_stored_summary(app):
    coach_u, s1, s2, s3, s4 = AF.many(5)
    coach = _coach(app, coach_u)
    tok = _token(coach)
    _join(_in(app, s1), tok)
    _join(_in(app, s2), tok)
    _post(_in(app, s3), "/api/v1/account/coach/request", {"code": coach.get("/api/v1/coach").json()["code"]})  # pending
    _put(s1, _summary())
    _put(s2, _summary(km7=12.0, alerts=(("form", "bad", "疲勞（TSB）"),)))
    _put(s3, _summary())                               # pending: never shown
    _put(s4, _summary())                               # nobody's student
    r = _board(coach)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    rows = body["students"]
    assert [x["student_id"] for x in rows] == [s2.id, s1.id]          # the red alert first
    assert body["used"] == 2 and body["pending"] == 1
    a = {x["student_id"]: x for x in rows}[s1.id]
    assert a["name"] == s1.display_name and a["since"]
    v = a["summary"]["volume"]
    assert (v["d7"]["distance_km"], v["d7"]["sessions"], v["d28"]["distance_km"]) == (31.5, 3, 120.0)
    assert (a["summary"]["load"]["ctl"], a["summary"]["load"]["tsb"]) == (48.2, -12.4)
    assert a["next_race"]["name"] == "合歡山越野 50K" and a["next_race"]["in_days"] == 40
    assert a["summary"]["plan"]["d28"]["rate"] == 0.8
    assert a["last_sync"] and a["alerts"] == []
    assert a["open_url"] == f"/api/v1/coach/view/{s1.id}"          # 代看 (SP-791): the board POSTs it
    b = {x["student_id"]: x for x in rows}[s2.id]
    assert [(x["id"], x["level"], x["text"]) for x in b["alerts"]] == [("form", "bad", "疲勞（TSB）")]
    # no e-mail of anyone, nothing of the pending or unlinked students
    raw = r.text
    for u in (s1, s2, s3, s4):
        assert (u.email or "@@") not in raw
    assert s3.display_name not in raw and s4.display_name not in raw


def test_a_student_without_a_summary_yet_is_listed_with_none(app):
    coach_u, s = AF.many(2)
    coach = _coach(app, coach_u)
    _join(_in(app, s), _token(coach))
    rows = _board(coach).json()["students"]
    assert len(rows) == 1 and rows[0]["summary"] is None and rows[0]["next_race"] is None
    assert [x["id"] for x in rows[0]["alerts"]] == ["never_synced"]


def test_the_alerts_the_board_adds(app):
    """No sync for a week (red after two), no activity in 7 days, under 60 % of the plan done."""
    coach_u, s1, s2 = AF.many(3)
    coach = _coach(app, coach_u)
    tok = _token(coach)
    _join(_in(app, s1), tok)
    _join(_in(app, s2), tok)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=15)).isoformat()
    _put(s1, _summary(sessions7=0, plan28=(10, 5)), last_ok=old)
    _put(s2, _summary(), last_ok=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).isoformat())
    rows = {x["student_id"]: x for x in _board(coach).json()["students"]}
    a = [(x["id"], x["level"]) for x in rows[s1.id]["alerts"]]
    assert a[0] == ("no_sync", "bad") and ("quiet", "watch") in a and ("plan_low", "watch") in a
    assert [(x["id"], x["level"]) for x in rows[s2.id]["alerts"]] == [("no_sync", "watch")]
    assert "15" in rows[s1.id]["alerts"][0]["text"]


def test_the_board_in_english(app):
    """The coach's language: the board's own alerts and the status titles (computed in Chinese, or
    in English and shown to a Chinese page) follow the request."""
    coach_u, s1, s2 = AF.many(3)
    coach = _coach(app, coach_u)
    tok = _token(coach)
    _join(_in(app, s1), tok)
    _join(_in(app, s2), tok)
    from backend.i18n import catalog
    zh_title = next(k for k, v in catalog("en").items() if isinstance(v, str) and k.startswith("熱適應"))
    _put(s1, _summary(alerts=(("heat", "watch", zh_title),), sessions7=0))
    _put(s2, _summary(alerts=(("heat", "watch", catalog("en")[zh_title]),), locale="en"))
    en = {x["student_id"]: x for x in coach.get("/api/v1/coach/board", headers={"Accept-Language": "en"}).json()["students"]}
    assert en[s1.id]["alerts"][0]["text"] == catalog("en")[zh_title]
    assert any(x["id"] == "quiet" and x["text"] == "No activity in the last 7 days" for x in en[s1.id]["alerts"])
    zh = {x["student_id"]: x for x in coach.get("/api/v1/coach/board", headers={"Accept-Language": "zh-TW"}).json()["students"]}
    assert zh[s2.id]["alerts"][0]["text"] == zh_title


# ---------------------------------------------------------------------------- 驗收 2: nothing recomputed on open

def test_opening_the_board_never_builds_a_dataset_or_a_status(app, monkeypatch):
    from backend import coach_board as CB
    from backend.api import overview as OV, wko5views as WV
    coach_u, s1, s2 = AF.many(3)
    coach = _coach(app, coach_u)
    tok = _token(coach)
    _join(_in(app, s1), tok)
    _join(_in(app, s2), tok)
    _put(s1, _summary())

    def boom(*a, **k):
        raise AssertionError("the board computed something")
    for mod, name in ((WV, "_dataset"), (OV, "_status"), (OV, "status_but_hr"), (CB, "build"), (CB, "refresh_now"),
                      (CB, "volume"), (CB, "load")):
        monkeypatch.setattr(mod, name, boom)
    r = _board(coach)
    assert r.status_code == 200, r.text
    assert {x["student_id"]: x["summary"] is not None for x in r.json()["students"]} == {s1.id: True, s2.id: False}
    assert _one(coach, s1.id).status_code == 200


# ---------------------------------------------------------------------------- 越權: 403 whatever the id

def test_one_student_only_through_an_active_link_whatever_the_id(app):
    ca, cb, s1, s2, s3, s4, s5 = AF.many(7)
    A, B = _coach(app, ca), _coach(app, cb)
    _join(_in(app, s1), _token(A))
    _join(_in(app, s2), _token(B))
    _post(_in(app, s3), "/api/v1/account/coach/request", {"code": A.get("/api/v1/coach").json()["code"]})
    st4 = _in(app, s4)
    l4 = _join(st4, _token(A)).json()["coaches"][0]["link_id"]
    _post(st4, f"/api/v1/account/coach/{l4}/end")
    for u in (s1, s2, s3, s4, s5):
        _put(u, _summary())
    ok = _one(A, s1.id)
    assert ok.status_code == 200 and ok.json()["summary"]["volume"]["d7"]["distance_km"] == 31.5
    bodies = set()
    for sid in (s2.id, s3.id, s4.id, s5.id, ca.id, cb.id, 1, 999999, 2 ** 62, 2 ** 70, 0, -3, "abc", "1.5", "1e3"):
        r = _one(A, sid)
        assert r.status_code == 403 and _code(r) == "NOT_YOUR_STUDENT", (sid, r.status_code, r.text)
        bodies.add(r.text)
    assert len(bodies) == 1                                   # the same answer: nothing tells an account exists
    assert [x["student_id"] for x in _board(A).json()["students"]] == [s1.id]
    assert [x["student_id"] for x in _board(B).json()["students"]] == [s2.id]


def test_someone_who_is_not_a_coach_or_signed_out_gets_nothing(app):
    coach_u, stu_u, other_u = AF.many(3)
    coach, stu, other = _coach(app, coach_u), _in(app, stu_u), _in(app, other_u)
    _join(stu, _token(coach))
    _put(stu_u, _summary())
    for cl in (stu, other):
        for r in (_board(cl), _one(cl, stu_u.id)):
            assert r.status_code == 403 and _code(r) == "NOT_A_COACH", r.text
    out = browser(app, ip=next(_IP))
    assert _board(out).status_code == 401 and _one(out, stu_u.id).status_code == 401


def test_owner_mode_and_the_demo_have_no_board(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.tests.test_login_sp58 import _clean_env
    _clean_env(monkeypatch, tmp_path)
    with TestClient(build_app(demo=False)) as cl:
        assert cl.get("/api/v1/coach/board").status_code == 404
        assert cl.get("/api/v1/coach/board/page").status_code == 404
        assert cl.get("/api/v1/coach/students/2/summary").status_code == 404
    demo = build_app(demo=True)
    assert not [r for r in demo.routes if "/coach" in getattr(r, "path", "")]


# ---------------------------------------------------------------------------- 撤回: 403 from the next request

def test_a_withdrawal_or_a_let_go_is_403_from_the_next_request(app):
    from backend.accounts import store
    coach_u, s1, s2 = AF.many(3)
    coach, st1, st2 = _coach(app, coach_u), _in(app, s1), _in(app, s2)
    tok = _token(coach)
    l1 = _join(st1, tok).json()["coaches"][0]["link_id"]
    l2 = _join(st2, tok).json()["coaches"][0]["link_id"]
    _put(s1, _summary())
    _put(s2, _summary())
    assert _one(coach, s1.id).status_code == 200
    assert _post(st1, f"/api/v1/account/coach/{l1}/end").status_code == 200       # the student withdraws
    r = _one(coach, s1.id)
    assert r.status_code == 403 and _code(r) == "NOT_YOUR_STUDENT"
    assert [x["student_id"] for x in _board(coach).json()["students"]] == [s2.id]
    assert _post(coach, f"/api/v1/coach/links/{l2}/end").status_code == 200       # the coach lets go
    assert _one(coach, s2.id).status_code == 403 and _board(coach).json()["students"] == []
    # a disabled student, then the admin taking the coach role away
    s3 = AF.many(1)[0]
    _join(_in(app, s3), _token(coach))
    _put(s3, _summary())
    store.set_disabled(s3.id, True)
    assert _one(coach, s3.id).status_code == 403 and _board(coach).json()["students"] == []
    store.set_disabled(s3.id, False)
    assert _one(coach, s3.id).status_code == 200
    from backend.accounts import coaching
    coaching.set_coach(coach_u.id, False, by=1)
    assert _code(_board(coach)) == "NOT_A_COACH" and _code(_one(coach, s3.id)) == "NOT_A_COACH"


def test_a_withdrawal_between_the_list_and_the_read_skips_the_student(app, monkeypatch):
    """The board lists the links, then reads each through in_student, which checks again."""
    from backend.accounts import coaching as CO, store
    coach_u, s1, s2 = AF.many(3)
    coach = _coach(app, coach_u)
    tok = _token(coach)
    l1 = _join(_in(app, s1), tok).json()["coaches"][0]["link_id"]
    _join(_in(app, s2), tok)
    real = CO.coach_view

    def view_then_withdraw(u):
        v = real(u)
        store.coach_end(l1, "student", s1.id)
        return v
    monkeypatch.setattr(CO, "coach_view", view_then_withdraw)
    rows = _board(coach).json()["students"]
    assert [x["student_id"] for x in rows] == [s2.id]


def test_the_board_api_never_opens_a_tenant_by_an_id_itself():
    """Static guard (as SP-789's for api/coach.py): api/coach_board.py reaches a student only
    through coaching (in_student / student_info)."""
    src = (Path(__file__).resolve().parents[1] / "api" / "coach_board.py").read_text("utf-8")
    for bad in ("tenant_of(", "get_user(", "tenancy.use(", "set_current(", "store."):
        assert bad not in src, bad


def test_the_page_is_there_for_a_signed_in_coach(app):
    coach = _coach(app, AF.many(1)[0])
    r = coach.get("/api/v1/coach/board/page")
    assert r.status_code == 200 and "coach_board" in r.text
    en = coach.get("/api/v1/coach/board/page", headers={"Accept-Language": "en"})
    assert en.status_code == 200 and 'lang="en"' in en.text and "Coach dashboard" in en.text


# ---------------------------------------------------------------------------- computing a summary

class _DS:
    """A stand-in Dataset: workouts and today (the volume part only reads those)."""

    def __init__(self, today: dt.date, ws):
        from backend.engine.wko5expr.dataset import date_to_day
        self.today = date_to_day(today)
        self.workouts = ws
        self.deferred = None


def _w(i, day: dt.date, km, climb, secs, tss, sport="run"):
    from backend.engine.wko5expr.dataset import Workout, date_to_day
    return Workout(idx=i, entry=None, day=date_to_day(day) + 0.3, sport=sport, sport_type="running", tags=[],
                   metrics={"distance": km, "climbing": climb, "movingduration": secs, "duration": secs, "tss": tss})


def test_build_sums_the_last_7_and_28_days(monkeypatch, tmp_path):
    from backend import coach_board as CB
    monkeypatch.setattr(CB, "load", lambda ds, today: {"ctl": 40.0, "atl": 50.0, "tsb": -10.0})
    today = dt.date(2026, 10, 20)
    ws = [_w(0, today, 10, 300, 3600, 60), _w(1, today - dt.timedelta(days=6), 20, 900, 7200, 140),
          _w(2, today - dt.timedelta(days=7), 15, 500, 5400, 90), _w(3, today - dt.timedelta(days=27), 5, 100, 1800, 30),
          _w(4, today - dt.timedelta(days=28), 99, 9999, 9999, 999), _w(5, today + dt.timedelta(days=1), 99, 9, 9, 9)]
    owner = tenancy.Tenant(id="u990", kind=tenancy.USER, root=tmp_path, shared=tmp_path, caps=tenancy.USER_CAPS)
    with tenancy.use(owner):
        s = CB.build(_DS(today, ws), None, reason="test")
    assert s["day"] == "2026-10-20" and s["v"] == CB.VERSION
    assert s["volume"]["d7"] == {"sessions": 2, "moving_s": 10800, "distance_km": 30.0, "climbing_m": 1200, "tss": 200}
    assert s["volume"]["d28"] == {"sessions": 4, "moving_s": 18000, "distance_km": 50.0, "climbing_m": 1800, "tss": 320}
    assert s["load"] == {"ctl": 40.0, "atl": 50.0, "tsb": -10.0}
    assert s["last_activity"] == "2026-10-21" and s["next_race"] is None and s["plan"] is None


def test_a_failing_part_leaves_the_rest(monkeypatch, tmp_path):
    from backend import coach_board as CB

    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr(CB, "load", boom)
    today = dt.date(2026, 10, 20)
    t = tenancy.Tenant(id="u991", kind=tenancy.USER, root=tmp_path, shared=tmp_path, caps=tenancy.USER_CAPS)
    with tenancy.use(t):
        s = CB.build(_DS(today, [_w(0, today, 10, 300, 3600, 60)]), None)
    assert s["load"] is None and s["volume"]["d7"]["sessions"] == 1


def test_the_next_race_the_plan_and_the_status_alerts(app):
    """In a student's own tenant: the season plan's next event (a past one skipped, a running
    multi-day one counted), the stored plan's completion, the status's red / yellow titles."""
    from backend import coach_board as CB
    from backend.engine.planning import Event, Plan
    from backend.engine.status import Indicator
    s = AF.many(1)[0]
    t = AF.tenant(s)
    today = dt.date(2026, 10, 21)                       # a Wednesday
    with tenancy.use(t):
        p = Plan()
        p.events = [Event(id="a", name="舊賽", date="2026-10-01"), Event(id="b", name="B 賽", date="2026-11-20", priority="B"),
                    Event(id="c", name="百岳三天", date="2026-10-20", days=3, kind="baiyue", priority="A")]
        p.save()
        assert CB.next_race(today)["name"] == "百岳三天"
        assert CB.next_race(dt.date(2026, 10, 23))["name"] == "B 賽"
        assert CB.next_race(dt.date(2026, 12, 1)) is None
        con = sqlite3.connect(str(t.root / "wko5coach.db"))
        try:
            rows = [("2026-09-22", "easy", "done"), ("2026-09-23", "easy", "done"),        # 29 days back: outside
                    ("2026-09-24", "long", "done"), ("2026-10-01", "quality", "missed"), ("2026-10-10", "easy", "done"),
                    ("2026-10-12", "notice", "missed"), ("2026-10-15", "easy", "deleted"), ("2026-10-19", "easy", "done"),
                    ("2026-10-20", "easy", "active"), ("2026-10-22", "long", "active"), ("2026-10-25", "easy", "active")]
            for i, (d, k, stt) in enumerate(rows):
                con.execute("INSERT INTO plan_sessions(athlete_id, uid, week_start, day, kind, title, minutes, origin, "
                            "edited, provisional, state, updated_at) "
                            "VALUES (1, ?, ?, ?, ?, 't', 60, 'auto', 0, 0, ?, '2026-09-01')",
                            (f"u{i}", d, d, k, stt))
            con.commit()
        finally:
            con.close()
        pc = CB.plan_completion(today)
        # 28 days before: 09-23 … 10-20 → done 09-23, 09-24, 10-10, 10-19; missed 10-01 (the notice and the deleted
        # one are not sessions); 10-20 still open (active) is not counted
        assert pc["d28"] == {"due": 5, "done": 4, "rate": 0.8}
        assert pc["week"] == {"planned": 4, "done": 1, "start": "2026-10-19"}

        class St:
            indicators = [Indicator("form", "疲勞（TSB）", "bad", "", ""), Indicator("heat", "熱適應", "watch", "", ""),
                          Indicator("fitness", "體能", "good", "", "")]
        alerts, counts = CB.status_alerts(St())
    assert [(a["id"], a["level"]) for a in alerts] == [("form", "bad"), ("heat", "watch")]
    assert counts["bad"] == 1 and counts["watch"] == 1 and counts["locale"] == "zh-TW"


# ---------------------------------------------------------------------------- when it is computed

def test_after_a_warm_up_only_a_coached_student_gets_a_summary(app, monkeypatch):
    from backend import coach_board as CB
    coach_u, s, other = AF.many(3)
    coach = _coach(app, coach_u)
    st = _in(app, s)
    lid = _join(st, _token(coach)).json()["coaches"][0]["link_id"]
    monkeypatch.setattr(CB, "_status_of", lambda ds, today, compute: None)
    ds = _DS(_today(), [_w(0, _today(), 8, 100, 2400, 40)])
    with tenancy.use(AF.tenant(other)):
        assert CB.after_warm_up(ds, "sync-coros") is False and CB.read() is None
    with tenancy.use(AF.tenant(s)):
        assert CB.after_warm_up(ds, "sync-coros") is True
        got = CB.read()
    assert got["reason"] == "warm-up:sync-coros" and got["volume"]["d7"]["distance_km"] == 8.0
    assert _one(coach, s.id).json()["summary"]["volume"]["d7"]["distance_km"] == 8.0
    _post(st, f"/api/v1/account/coach/{lid}/end")
    with tenancy.use(AF.tenant(s)):
        CB.write({**got, "reason": "kept"})
        assert CB.after_warm_up(ds, "sync-coros") is False and CB.read()["reason"] == "kept"


def test_the_warm_up_calls_it_after_the_status(monkeypatch):
    """api/wko5views.warm_up's thread: the Dataset, the status, the plan inputs, then the summary."""
    src = (Path(__file__).resolve().parents[1] / "api" / "wko5views.py").read_text("utf-8")
    body = src[src.index("def warm_up("):src.index("async def warm_up_startup")]
    assert body.index("OV._status(") < body.index("PSA._compute_inputs()") < body.index("coach_board.after_warm_up(ds")


def test_the_tick_queues_a_missing_summary_then_one_a_night(app):
    from backend import coach_board as CB
    coach_u, s1, s2, other = AF.many(4)
    coach = _coach(app, coach_u)
    tok = _token(coach)
    _join(_in(app, s1), tok)
    l2 = _join(st2 := _in(app, s2), tok).json()["coaches"][0]["link_id"]
    AF.tenant(other)
    queued = []

    def submit(fn, **kw):
        queued.append((kw["key"], kw["priority"], fn))
    noon = dt.datetime.now(dt.timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    out = asyncio.run(CB.tick(now=noon, submit=submit))
    assert sorted(out) == sorted([(f"u{s1.id}", "missing"), (f"u{s2.id}", "missing")])
    assert {k for k, _p, _f in queued} == {("coach-summary", f"u{s1.id}"), ("coach-summary", f"u{s2.id}")}
    assert {p for _k, p, _f in queued} == {"warm"}
    # today's summaries: nothing to do; yesterday's: due once the night slot has passed
    local_today = None
    with tenancy.use(AF.tenant(s1)):
        from backend.engine import localtime
        local_today = noon.astimezone(localtime.zone()).date()
        CB.write(_summary(day=local_today))
    with tenancy.use(AF.tenant(s2)):
        CB.write(_summary(day=local_today - dt.timedelta(days=1)))
    queued.clear()
    with tenancy.use(AF.tenant(s2)):
        z = localtime.zone()
    early = dt.datetime.combine(local_today, dt.time(3, 0), tzinfo=z)
    late = dt.datetime.combine(local_today, dt.time(5, 0), tzinfo=z)
    assert asyncio.run(CB.tick(now=early, submit=submit)) == []
    assert asyncio.run(CB.tick(now=late, submit=submit)) == [(f"u{s2.id}", "night")]
    assert queued[-1][1] == "cold"
    # a withdrawn student is not computed any more
    _post(st2, f"/api/v1/account/coach/{l2}/end")
    assert asyncio.run(CB.tick(now=late, submit=submit)) == []


def test_a_failed_job_waits_before_trying_again(app, monkeypatch):
    from backend import coach_board as CB
    coach_u, s = AF.many(2)
    _join(_in(app, s), _token(_coach(app, coach_u)))

    def boom(reason):
        raise RuntimeError("no dataset")
    monkeypatch.setattr(CB, "refresh_now", boom)
    with tenancy.use(AF.tenant(s)):
        assert CB.due() == "missing"
        CB._job("missing")
        assert CB.due() is None                                  # RETRY_S before the next try
        CB._FAILED[f"u{s.id}"] -= CB.RETRY_S + 1
        assert CB.due() == "missing"


def test_refresh_now_on_a_student_with_no_activities_yet(app):
    """The job itself, on the real (empty) Dataset of a new account: zeros, no race, no plan."""
    from backend import coach_board as CB
    coach_u, s = AF.many(2)
    coach = _coach(app, coach_u)
    _join(_in(app, s), _token(coach))
    with tenancy.use(AF.tenant(s)):
        out = CB.refresh_now("missing")
    assert out["volume"]["d7"]["sessions"] == 0 and out["volume"]["d28"]["distance_km"] == 0.0
    assert out["next_race"] is None and out["last_activity"] is None
    row = _one(coach, s.id).json()
    assert row["summary"]["day"] == out["day"]


def test_the_summary_is_a_registered_derived_file():
    from backend import data_registry as DR
    e = DR.classify("cache/coach_summary.json")
    assert e.cls == DR.DERIVED and e.where == DR.ROOT
