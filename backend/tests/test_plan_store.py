"""
Stored, editable plan: projection past this week, reconcile (done / missed /
regenerate / edited kept), persistence, and the push scopes against a fake
COROS Training Hub. No network, no dataset (inputs are built by hand).
"""
import copy
import datetime as dt
from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.db.models import CorosPlanPush, PlanSession
from backend.engine import plan_store as PS
from backend.engine import projection as P
from backend.engine import reconcile as R
from backend.sync import coros_workouts as CW, http
from backend.tests.test_coros_workouts import FakeHub, make_db, run

TH = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def g(id, kind, title, minutes, day, done=False, done_by=None, target="", detail=""):
    return {"id": id, "kind": kind, "title": title, "minutes": minutes, "target": target, "detail": detail,
            "source": "", "tss": minutes * 0.8, "day": day, "done": done, "done_by": done_by}


ACT_929 = {"index": 11, "date": "2026-09-29", "category": "road", "category_label": "路跑", "moving_s": 2400, "tss": 30}


def cur_plan(today="2026-09-30", sessions=None, mode="base", hours=5.0):
    if sessions is None:
        sessions = [
            g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
            g("quality", "quality", "閾值 3×10 分", 60, "2026-10-01", detail="休 2–3 分鐘；暖身 15 分、緩和 10 分"),
            g("strength1", "strength", "肌力（下肢單腳＋核心）", 35, "2026-10-02"),
            g("easy1", "easy", "輕鬆跑", 45, "2026-10-02"),
            g("long", "long", "長時間輕鬆（山路）", 120, "2026-10-04"),
        ]
    return {"week": {"start": "2026-09-28", "end": "2026-10-04", "today": today, "days_left": 5},
            "phase": "base", "mode": mode, "target": {"hours": hours, "tss": hours * 50, "tss_per_hour": 50.0},
            "history": [{"start": "x", "hours": h, "tss": h * 50} for h in (4.0, 4.2, 4.4, 4.0, 4.3, 4.5, 4.4, 4.6)],
            "load": {"ctl_end": 30.0, "atl_end": 32.0}, "sessions": sessions, "long_weekday": "日",
            "thresholds": dict(TH), "done": {"activities": [ACT_929]}}


def inputs(today="2026-09-30", cur=None, weeks=None, acts=None, horizon="2026-10-11"):
    cur = cur or cur_plan(today)
    return {"cur": cur, "weeks": weeks if weeks is not None else [], "activities": acts if acts is not None else [ACT_929],
            "today": today, "horizon_end": horizon, "thresholds": dict(TH),
            "phase": {"kind": "base", "label": "基礎期", "start": "2026-09-01", "end": "2026-11-15"},
            "phase_push_end": min("2026-11-15", (date.fromisoformat(today) + dt.timedelta(weeks=8)).isoformat()),
            "max_weeks": P.MAX_WEEKS}


def next_week(sessions=None, provisional=False):
    return {"start": "2026-10-05", "phase": "base", "mode": "base", "mode_label": "基礎期", "hours": 5.5,
            "tss": 275, "provisional": provisional, "why": [],
            "sessions": sessions if sessions is not None else [
                g("quality", "quality", "閾值 3×10 分", 60, "2026-10-06"),
                g("easy1", "easy", "輕鬆跑", 50, "2026-10-07"),
                g("long", "long", "長時間輕鬆（山路）", 130, "2026-10-11")]}


def active(ss):
    return {(s["week_start"], s.get("gen_key") or s["uid"]): s for s in ss if s["state"] == "active"}


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------

PHASES = [{"kind": "base", "start": "2026-08-01", "end": "2026-11-08"},
          {"kind": "specific", "start": "2026-11-09", "end": "2026-11-22"},
          {"kind": "taper", "start": "2026-11-23", "end": "2026-12-06"},
          {"kind": "event", "start": "2026-12-07", "end": "2026-12-07"}]


def test_projection_ramp_31_and_cap():
    weeks = P.project_weeks(cur_plan(), PHASES, date(2027, 3, 1))
    assert len(weeks) == P.MAX_WEEKS                               # capped at 8 weeks ahead
    assert weeks[0]["start"] == "2026-10-05" and weeks[-1]["start"] == "2026-11-23"
    assert weeks[0]["provisional"] is False and all(w["provisional"] for w in weeks[1:])
    hist = [h["hours"] for h in cur_plan()["history"]] + [5.0]
    modes = []
    for w in weeks:
        modes.append(w["mode"])
        if w["mode"] in ("base", "specific"):
            ref = max(hist[-1], sum(hist[-4:]) / 4)
            assert w["hours"] <= max(1.10 * ref, ref + 0.5) + 1e-6     # +10 %, at least +0.5 h
        hist.append(w["hours"])
    assert "recovery_week" in modes                                 # 3:1
    i = modes.index("recovery_week", 1)                             # 3 builds, then recovery
    assert modes[i - 3:i] == ["base"] * 3
    assert weeks[i]["hours"] == pytest.approx(0.65 * sum(w["hours"] for w in weeks[i - 3:i]) / 3)
    assert modes[-1] == "taper"
    taper = weeks[-1]
    assert any(s["title"] == "短強度 4×3 分" for s in taper["sessions"])
    assert weeks[6]["phase"] == "specific" and any("爬坡" in s["title"] for s in weeks[6]["sessions"]
                                                   if weeks[6]["mode"] != "recovery_week")


def test_projection_sessions_placed_like_week_plan():
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 10, 25))
    assert weeks[0]["mode"] == "recovery_week"                      # history already built 3 weeks
    assert not [s for s in weeks[0]["sessions"] if s["kind"] in ("long", "quality")]
    w = weeks[1]
    days = {s["id"]: s["day"] for s in w["sessions"]}
    assert days["long"] == "2026-10-18"                             # the usual long weekday (日)
    q = date.fromisoformat(days["quality"])
    assert abs((q - date(2026, 10, 18)).days) >= 2
    assert all(s["day"] != "2026-10-17" for s in w["sessions"] if s["kind"] == "strength")   # not before the long
    main_days = [s["day"] for s in w["sessions"] if s["kind"] != "strength"]
    assert len(main_days) == len(set(main_days))
    total = sum(s["minutes"] for s in w["sessions"] if s["kind"] != "strength")
    assert abs(total - w["hours"] * 60) <= 15
    assert w["sessions"][0]["target"]                                # target text from the thresholds


def test_projection_stops_at_horizon():
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 10, 14))
    assert [w["start"] for w in weeks] == ["2026-10-05", "2026-10-12"]
    assert all(s["day"] <= "2026-10-14" for s in weeks[1]["sessions"])


# ---------------------------------------------------------------------------
# reconcile (pure)
# ---------------------------------------------------------------------------

def rec(stored, inp):
    return R.reconcile(stored, PS.gen_weeks(inp), inp["activities"], inp["today"], inp["horizon_end"])


def test_first_generation_stores_everything():
    new, ch = rec([], inputs(weeks=[next_week()]))
    a = active(new)
    assert set(a) == {("2026-09-28", k) for k in ("quality", "strength1", "easy1", "long")} | \
        {("2026-10-05", k) for k in ("quality", "easy1", "long")}
    done = [s for s in new if s["state"] == "done"]
    assert len(done) == 1 and done[0]["gen_key"] == "easy2" and done[0]["done_by"]["index"] == 11
    assert all(s["origin"] == "auto" and not s["edited"] for s in new)
    assert {c["action"] for c in ch} == {"added"}


def test_unedited_auto_is_regenerated_edited_is_kept():
    new, _ = rec([], inputs())
    a = active(new)
    a[("2026-09-28", "easy1")].update(minutes=30, edited=True)    # the user's edit
    cur = cur_plan(sessions=[g("quality", "quality", "閾值 3×10 分", 60, "2026-10-01",
                               detail="休 2–3 分鐘；暖身 15 分、緩和 10 分"),
                             g("strength1", "strength", "肌力（下肢單腳＋核心）", 35, "2026-10-02"),
                             g("easy1", "easy", "輕鬆跑", 55, "2026-10-03"),
                             g("long", "long", "長時間輕鬆（山路）", 100, "2026-10-04")])
    new2, ch = rec(new, inputs(cur=cur))
    b = active(new2)
    assert b[("2026-09-28", "easy1")]["minutes"] == 30 and b[("2026-09-28", "easy1")]["day"] == "2026-10-02"
    assert b[("2026-09-28", "long")]["minutes"] == 100
    changed = [c for c in ch if c["action"] == "changed"]
    assert [c["title"] for c in changed] == ["長時間輕鬆（山路）"] and changed[0]["before"] == {"minutes": 120}
    assert b[("2026-09-28", "long")]["uid"] == a[("2026-09-28", "long")]["uid"]   # same session, updated


def test_deleted_auto_stays_deleted():
    new, _ = rec([], inputs())
    for s in new:
        if s.get("gen_key") == "quality":
            s["state"] = "deleted"
    new2, ch = rec(new, inputs())
    assert ("2026-09-28", "quality") not in active(new2)
    assert not [c for c in ch if c["action"] == "added"]


def test_missed_and_recalculated():
    new, _ = rec([], inputs())
    # two days later: quality on 10/1 wasn't done; the generator re-placed it on 10/3
    cur = cur_plan(today="2026-10-02", sessions=[
        g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
        g("quality", "quality", "閾值 3×10 分", 60, "2026-10-03"),
        g("strength1", "strength", "肌力", 35, "2026-10-02"),
        g("easy1", "easy", "輕鬆跑", 45, "2026-10-02"),
        g("long", "long", "長時間輕鬆（山路）", 120, "2026-10-04")])
    new2, ch = rec(new, inputs(today="2026-10-02", cur=cur))
    missed = [s for s in new2 if s["state"] == "missed"]
    assert [s["gen_key"] for s in missed] == ["quality"] and missed[0]["day"] == "2026-10-01"
    assert active(new2)[("2026-09-28", "quality")]["day"] == "2026-10-03"      # recalculated, new row
    assert active(new2)[("2026-09-28", "quality")]["uid"] != missed[0]["uid"]
    acts = {(c["action"], c["day"]) for c in ch}
    assert ("missed", "2026-10-01") in acts and ("added", "2026-10-03") in acts


def test_custom_session_done_by_same_day_activity():
    new, _ = rec([], inputs())
    new.append({"uid": "c1", "week_start": "2026-09-28", "gen_key": None, "day": "2026-09-30", "kind": "hike",
                "title": "健行", "minutes": 120, "target": "", "detail": "", "source": "", "tss": 0,
                "origin": "custom", "edited": True, "provisional": False, "state": "active",
                "done_by": None, "note": None})
    act = {"index": 12, "date": "2026-09-30", "category": "hike"}
    new2, ch = rec(new, inputs(today="2026-10-01", acts=[ACT_929, act],
                               cur=cur_plan(today="2026-10-01")))
    c1 = next(s for s in new2 if s["uid"] == "c1")
    assert c1["state"] == "done" and c1["done_by"]["index"] == 12


def test_auto_moved_off_an_edited_day():
    new, _ = rec([], inputs())
    a = active(new)
    a[("2026-09-28", "quality")].update(day="2026-10-04", edited=True)   # user put it on the long day
    new2, ch = rec(new, inputs())
    b = active(new2)
    assert b[("2026-09-28", "quality")]["day"] == "2026-10-04"
    assert b[("2026-09-28", "long")]["day"] != "2026-10-04"
    assert any(c.get("reason", "").startswith("和你安排的課同一天") for c in ch)


def test_edited_hard_session_superseded_in_recovery_week():
    new, _ = rec([], inputs())
    active(new)[("2026-09-28", "quality")].update(minutes=70, edited=True)
    cur = cur_plan(mode="recovery_week", sessions=[g("easy1", "easy", "輕鬆跑", 40, "2026-10-02")])
    new2, ch = rec(new, inputs(cur=cur))
    q = next(s for s in new2 if s.get("gen_key") == "quality")
    assert q["state"] == "superseded"
    assert any(c["action"] == "removed" and "恢復" in c.get("reason", "") for c in ch)
    assert ("2026-09-28", "long") not in active(new2)                  # unedited auto just removed


def test_beyond_horizon_removed():
    new, _ = rec([], inputs(weeks=[next_week()]))
    new2, ch = rec(new, inputs(weeks=[], horizon="2026-10-04"))
    assert not [s for s in new2 if s["week_start"] == "2026-10-05" and s["state"] == "active"]


# ---------------------------------------------------------------------------
# storage + edits
# ---------------------------------------------------------------------------

def test_persistence_and_edits():
    db = run(make_db())
    run(PS.plan_reconcile(db, inputs(weeks=[next_week()]), apply=True))
    ss = run(PS.load(db))
    assert len([s for s in ss if s["state"] == "active"]) == 7
    easy = next(s for s in ss if s.get("gen_key") == "easy1" and s["week_start"] == "2026-09-28")
    e = run(PS.edit(db, easy["uid"], {"minutes": 25, "day": "2026-10-03"}, "2026-09-30"))
    assert e["edited"] and e["minutes"] == 25
    with pytest.raises(PS.PlanError):
        run(PS.edit(db, easy["uid"], {"day": "2026-09-01"}, "2026-09-30"))
    with pytest.raises(PS.PlanError):
        run(PS.edit(db, easy["uid"], {"kind": "yoga"}, "2026-09-30"))
    c = run(PS.add(db, {"day": "2026-10-03", "kind": "hike", "minutes": 180, "title": "合歡山"}, "2026-09-30"))
    assert c["origin"] == "custom"
    run(PS.plan_reconcile(db, inputs(weeks=[next_week()]), apply=True))      # regenerate
    ss = {s["uid"]: s for s in run(PS.load(db))}
    assert ss[easy["uid"]]["minutes"] == 25 and ss[easy["uid"]]["day"] == "2026-10-03"
    assert ss[c["uid"]]["title"] == "合歡山" and ss[c["uid"]]["state"] == "active"
    d = run(PS.delete(db, c["uid"]))
    assert d["state"] == "removed" and c["uid"] not in {s["uid"] for s in run(PS.load(db))}
    long = next(s for s in ss.values() if s.get("gen_key") == "long" and s["week_start"] == "2026-09-28")
    assert run(PS.delete(db, long["uid"]))["state"] == "deleted"
    run(PS.plan_reconcile(db, inputs(weeks=[next_week()]), apply=True))
    assert ("2026-09-28", "long") not in active(run(PS.load(db)))


def test_move_auto_to_another_week_leaves_tombstone():
    db = run(make_db())
    run(PS.plan_reconcile(db, inputs(weeks=[next_week()]), apply=True))
    long = next(s for s in run(PS.load(db)) if s.get("gen_key") == "long" and s["week_start"] == "2026-09-28")
    moved = run(PS.edit(db, long["uid"], {"day": "2026-10-06"}, "2026-09-30"))
    assert moved["week_start"] == "2026-10-05" and moved["origin"] == "custom"
    run(PS.plan_reconcile(db, inputs(weeks=[next_week()]), apply=True))
    ss = run(PS.load(db))
    assert not [s for s in ss if s["week_start"] == "2026-09-28" and s.get("gen_key") == "long"
                and s["state"] == "active"]                              # not regenerated into a duplicate
    assert any(s["uid"] == long["uid"] and s["day"] == "2026-10-06" for s in ss)


# ---------------------------------------------------------------------------
# API + push scopes (mocked COROS)
# ---------------------------------------------------------------------------

class Env:
    def __init__(self, monkeypatch):
        from backend.api import plan_sessions
        from backend.db.database import get_db
        self.db = run(make_db())
        self.fake = FakeHub()
        self.inp = inputs(weeks=[next_week(), {**next_week(provisional=True), "start": "2026-10-12", "sessions": [
            g("long", "long", "長時間輕鬆（山路）", 140, "2026-10-18")]}], horizon="2026-10-18")
        monkeypatch.setattr(plan_sessions, "_compute_inputs", lambda: self.inp)

        async def fake_db():
            yield self.db
        app = FastAPI()
        app.include_router(plan_sessions.router)
        app.dependency_overrides[get_db] = fake_db
        self.c = TestClient(app)

    def __enter__(self):
        self._t = http.use_transport(httpx.MockTransport(self.fake))
        self._t.__enter__()
        return self

    def __exit__(self, *a):
        self._t.__exit__(*a)

    def pushed(self):
        return run(self.db.execute(select(CorosPlanPush))).scalars().all()


API = "/api/v1/overview/plan"


def test_api_sessions_initialize_and_edit(monkeypatch):
    with Env(monkeypatch) as e:
        r = e.c.get(f"{API}/sessions")
        assert r.status_code == 200
        body = r.json()
        ss = body["sessions"]
        assert {s["day"] for s in ss} >= {"2026-10-01", "2026-10-04", "2026-10-06", "2026-10-18"}
        long = next(s for s in ss if s["day"] == "2026-10-04")
        assert long["coros"]["status"] == "not_pushed" and body["max_weeks"] == 8
        r = e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 90})
        assert r.status_code == 200 and r.json()["edited"] is True
        assert e.c.patch(f"{API}/sessions/{long['uid']}", json={"day": "2020-01-01"}).status_code == 400
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "easy", "minutes": 30})
        assert r.status_code == 200 and r.json()["origin"] == "custom"
        assert e.c.delete(f"{API}/sessions/nope").status_code == 404
        prev = e.c.get(f"{API}/reconcile").json()
        assert prev["changes"] == []                                    # nothing new since initialization


def test_push_scopes_and_idempotency(monkeypatch):
    with Env(monkeypatch) as e:
        pv = e.c.get(f"{API}/push-coros/preview?scope=day&day=2026-10-01").json()
        assert (pv["start"], pv["end"], pv["count"]) == ("2026-10-01", "2026-10-01", 1)
        r = e.c.post(f"{API}/push-coros?scope=day&day=2026-10-01")
        assert r.status_code == 200 and [s["status"] for s in r.json()["sessions"]] == ["pushed"]
        assert [x["happenDay"] for x in e.fake.entities] == [20261001]

        pv = e.c.get(f"{API}/push-coros/preview?scope=week").json()
        assert (pv["start"], pv["end"]) == ("2026-09-30", "2026-10-04")
        assert pv["count"] == 3 and pv["to_send"] == 2 and pv["unchanged"] == 1   # strength skipped
        assert [s["kind"] for s in pv["skipped"]] == ["strength"]
        e.c.post(f"{API}/push-coros?scope=week")
        assert sorted(x["happenDay"] for x in e.fake.entities) == [20261001, 20261002, 20261004]

        pv = e.c.get(f"{API}/push-coros/preview?scope=phase").json()
        assert (pv["start"], pv["end"]) == ("2026-09-30", "2026-11-15")
        assert pv["count"] == 7 and pv["to_send"] == 4
        e.c.post(f"{API}/push-coros?scope=phase")
        assert len(e.fake.entities) == 7 and len(e.fake.live()) == 7
        n = len(e.fake.calls)
        r = e.c.post(f"{API}/push-coros?scope=phase")                 # again: nothing sent
        assert all(s["status"] == "pushed" and not s["changed"] for s in r.json()["sessions"]
                   if s["status"] != "skipped")
        adds = [c for c in e.fake.calls[n:] if c[1] != "/training/schedule/query"]
        assert adds == []
        assert len(e.fake.entities) == 7


def test_edit_then_push_replaces_and_delete_cleans_up(monkeypatch):
    with Env(monkeypatch) as e:
        e.c.post(f"{API}/push-coros?scope=week")
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        long = next(s for s in ss if s["day"] == "2026-10-04")
        easy = next(s for s in ss if s["day"] == "2026-10-02" and s["kind"] == "easy")
        e.c.patch(f"{API}/sessions/{long['uid']}", json={"day": "2026-10-03"})
        e.c.delete(f"{API}/sessions/{easy['uid']}")
        st = {s["uid"]: s["coros"]["status"] for s in e.c.get(f"{API}/sessions").json()["sessions"]
              if "coros" in s}
        assert st[long["uid"]] == "outdated"
        r = e.c.post(f"{API}/push-coros?scope=day&day=2026-10-03").json()   # a day scope still cleans up
        assert [s["status"] for s in r["sessions"]] == ["updated"]
        assert [x["status"] for x in r["removed"]] == ["removed"]
        assert sorted(x["happenDay"] for x in e.fake.entities) == [20261001, 20261003]
        assert len(e.fake.live()) == 2


def test_missed_session_is_removed_from_coros(monkeypatch):
    with Env(monkeypatch) as e:
        e.c.post(f"{API}/push-coros?scope=week")
        assert 20261001 in {x["happenDay"] for x in e.fake.entities}
        # two days on: the 10/1 quality wasn't done, the generator re-places it on 10/3
        monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 2))
        cur = cur_plan(today="2026-10-02", sessions=[
            g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
            g("quality", "quality", "閾值 3×10 分", 60, "2026-10-03", detail="休 2–3 分鐘；暖身 15 分、緩和 10 分"),
            g("strength1", "strength", "肌力", 35, "2026-10-02"),
            g("easy1", "easy", "輕鬆跑", 45, "2026-10-02"),
            g("long", "long", "長時間輕鬆（山路）", 120, "2026-10-04")])
        e.inp = inputs(today="2026-10-02", cur=cur, weeks=e.inp["weeks"], horizon="2026-10-18")
        pv = e.c.get(f"{API}/push-coros/preview?scope=week").json()
        assert pv["missed_to_remove"] == 1
        assert any(c["action"] == "missed" and c["day"] == "2026-10-01" for c in pv["changes"])
        r = e.c.post(f"{API}/push-coros?scope=week").json()
        assert [x for x in r["removed"] if x.get("missed")][0]["status"] == "removed"
        days = sorted(x["happenDay"] for x in e.fake.entities)
        assert 20261001 not in days and 20261003 in days
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        assert any(s["state"] == "missed" and s["day"] == "2026-10-01" for s in ss)   # visible as 未完成
        # and a missed entry that COROS shows as executed is left alone
        # (covered by coros_workouts Executed tests)


def test_push_needs_login(monkeypatch):
    with Env(monkeypatch) as e:
        run(e.db.execute(CorosPlanPush.__table__.delete()))
        from backend.db.models import SyncState
        run(e.db.execute(SyncState.__table__.delete()))
        run(e.db.commit())
        r = e.c.post(f"{API}/push-coros?scope=week")
        assert r.status_code == 401
        assert e.c.get(f"{API}/push-coros/preview?scope=nope").status_code == 400


def test_unpush_range(monkeypatch):
    with Env(monkeypatch) as e:
        e.c.post(f"{API}/push-coros?scope=phase")
        r = e.c.delete(f"{API}/push-coros?scope=week")
        assert len(r.json()["removed"]) == 3
        assert sorted(x["happenDay"] for x in e.fake.entities) == [20261006, 20261007, 20261011, 20261018]
