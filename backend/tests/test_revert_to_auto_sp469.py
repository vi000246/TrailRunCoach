"""
SP-469 還原成自動: an auto session the user edited was kept by 依實際進度重排 (reconcile rule 3),
and a deleted one left a tombstone that blocked the day for good — there was no way back to the
automatic plan. Now:
  A  POST /sessions/{uid}/revert takes an edited auto session, or a deleted one (its tombstone,
     offered as `deleted_auto` on the 課表 page), back to what the generator plans now; the watch
     follows like after an edit, or the session shows 需更新;
  C  依實際進度重排 with reset_edited (「也重置我改過的課」) does it for every edited / deleted auto
     session at once. Sessions the user added (custom) and done ones are never touched; without
     the box ticked it behaves as before.
Inputs are built by hand (test_plan_store.Env: today 9/30, week 9/28–10/4 + 10/5 week + 10/12
week); COROS is the scripted FakeHub. No WKO5 data.
"""
from datetime import date

import pytest

from backend.engine import plan_store as PS
from backend.sync import coros_workouts as CW
from backend.tests.test_coros_workouts import run
from backend.tests.test_plan_store import API, Env, inputs, next_week, rec

TODAY = "2026-09-30"


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def _sessions(e):
    return e.c.get(f"{API}/sessions").json()


def _by_uid(e):
    return {s["uid"]: s for s in _sessions(e)["sessions"]}


def _on(e, day, kind):
    return next(s for s in _sessions(e)["sessions"] if s["day"] == day and s["kind"] == kind)


# ---------------------------------------------------------------------------
# A: an edited auto session
# ---------------------------------------------------------------------------

def test_edited_auto_session_reverts_to_what_the_generator_plans_now(monkeypatch):
    with Env(monkeypatch) as e:
        long = _on(e, "2026-10-04", "long")
        assert "revertible" not in long                                    # untouched: nothing to revert
        r = e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 90, "day": "2026-10-03", "title": "我的長跑"})
        assert r.status_code == 200 and r.json()["edited"] is True
        edited = _by_uid(e)[long["uid"]]
        assert edited["revertible"] is True and edited["day"] == "2026-10-03"
        e.c.post(f"{API}/reconcile")                                       # 依實際進度重排 keeps it, as before
        assert _by_uid(e)[long["uid"]]["minutes"] == 90

        r = e.c.post(f"{API}/sessions/{long['uid']}/revert")
        assert r.status_code == 200, r.text
        out = r.json()
        assert (out["day"], out["minutes"], out["title"], out["edited"]) == ("2026-10-04", 120, "LSD（山路）", False)
        assert out["was"] == "edited"
        assert out["change"]["reverted"] is True and out["change"]["action"] == "changed"
        assert out["change"]["before"]["minutes"] == 90 and out["change"]["before"]["day"] == "2026-10-03"
        now = _by_uid(e)[long["uid"]]                                      # same session, the generator's again
        assert (now["day"], now["minutes"], now["edited"]) == ("2026-10-04", 120, False)
        assert "revertible" not in now
        # and from now on the automatic re-plan changes it again (it's not the user's any more)
        e.inp["cur"]["sessions"][-1] = {**e.inp["cur"]["sessions"][-1], "minutes": 100}
        e.c.post(f"{API}/reconcile")
        assert _by_uid(e)[long["uid"]]["minutes"] == 100


def test_revert_changes_only_that_session(monkeypatch):
    with Env(monkeypatch) as e:
        ss = _sessions(e)["sessions"]
        long = next(s for s in ss if s["day"] == "2026-10-04")
        q = next(s for s in ss if s["kind"] == "quality" and s["day"] == "2026-10-01")
        e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 90})
        e.c.patch(f"{API}/sessions/{q['uid']}", json={"minutes": 40})
        # the generator moved on meanwhile (another easy minutes): only the reverted one follows now
        e.inp["cur"]["sessions"][3] = {**e.inp["cur"]["sessions"][3], "minutes": 30}
        easy = _on(e, "2026-10-02", "easy")
        e.c.post(f"{API}/sessions/{long['uid']}/revert")
        by = _by_uid(e)
        assert by[long["uid"]]["minutes"] == 120 and by[q["uid"]]["minutes"] == 40 and by[q["uid"]]["edited"]
        assert by[easy["uid"]]["minutes"] == easy["minutes"]              # left for 依實際進度重排


def test_revert_resends_a_pushed_session_to_the_watch(monkeypatch):
    with Env(monkeypatch) as e:
        long = _on(e, "2026-10-04", "long")
        assert e.c.post(f"{API}/push-coros?scope=week").status_code == 200
        e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 90})   # the watch follows (SP-358)
        assert _by_uid(e)[long["uid"]]["coros"]["status"] == "pushed"
        r = e.c.post(f"{API}/sessions/{long['uid']}/revert").json()
        assert r["coros"]["status"] == "ok"
        assert _by_uid(e)[long["uid"]]["coros"]["status"] == "pushed"     # the 120-min version is on the watch
        assert sorted(x["happenDay"] for x in e.fake.entities) == [20261001, 20261002, 20261004]


def test_reverted_session_is_marked_for_a_new_push_when_the_watch_did_not_follow(monkeypatch):
    with Env(monkeypatch) as e:
        long = _on(e, "2026-10-04", "long")
        e.c.post(f"{API}/push-coros?scope=week")
        e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 90})
        # reverted in the store while the watch couldn't follow (expired login, a failed push)
        out = run(PS.revert(e.db, long["uid"], e.inp, TODAY))
        assert out["minutes"] == 120
        assert _by_uid(e)[long["uid"]]["coros"]["status"] == "outdated"   # 需更新: the watch has the 90-min one


# ---------------------------------------------------------------------------
# A: a deleted auto session (tombstone)
# ---------------------------------------------------------------------------

def test_deleted_auto_session_comes_back_on_its_day(monkeypatch):
    with Env(monkeypatch) as e:
        easy = _on(e, "2026-10-02", "easy")
        assert e.c.delete(f"{API}/sessions/{easy['uid']}").json()["state"] == "deleted"
        e.c.post(f"{API}/reconcile")                                       # the tombstone blocks it, as before
        body = _sessions(e)
        assert not [s for s in body["sessions"] if s["day"] == "2026-10-02" and s["kind"] == "easy"]
        assert [(d["uid"], d["day"], d["kind"]) for d in body["deleted_auto"]] == [(easy["uid"], "2026-10-02", "easy")]

        r = e.c.post(f"{API}/sessions/{easy['uid']}/revert")
        assert r.status_code == 200, r.text
        out = r.json()
        assert (out["state"], out["day"], out["minutes"], out["edited"], out["was"]) == ("active", "2026-10-02", 45, False, "deleted")
        assert out["change"]["action"] == "added" and out["change"]["reverted"]
        body = _sessions(e)
        assert any(s["uid"] == easy["uid"] and s["day"] == "2026-10-02" for s in body["sessions"])
        assert body["deleted_auto"] == []


def test_deleted_then_pushed_session_goes_back_on_the_watch(monkeypatch):
    with Env(monkeypatch) as e:
        easy = _on(e, "2026-10-02", "easy")
        e.c.post(f"{API}/push-coros?scope=week")
        e.c.delete(f"{API}/sessions/{easy['uid']}")
        assert 20261002 not in [x["happenDay"] for x in e.fake.entities]
        r = e.c.post(f"{API}/sessions/{easy['uid']}/revert").json()
        assert r["coros"]["status"] == "ok"                                # 自動推送 on: in the window, pushed
        assert 20261002 in [x["happenDay"] for x in e.fake.entities]


def test_deleted_auto_with_a_custom_session_on_its_day(monkeypatch):
    # 正式站 10/16: the auto easy run deleted, a session of the user's own added that day. The
    # user's own stays (C never deletes it); the auto one comes back but not onto the user's day
    # (reconcile rule 4: another free day of the week). Deleting the custom one first gives the day back.
    with Env(monkeypatch) as e:
        easy = _on(e, "2026-10-02", "easy")
        e.c.delete(f"{API}/sessions/{easy['uid']}")
        mine = e.c.post(f"{API}/sessions", json={"day": "2026-10-02", "kind": "easy", "minutes": 30, "title": "我的輕鬆跑"}).json()
        out = e.c.post(f"{API}/sessions/{easy['uid']}/revert").json()
        assert out["state"] == "active" and out["day"] != "2026-10-02"
        assert _by_uid(e)[mine["uid"]]["day"] == "2026-10-02"
        # the other way round: delete the user's own, revert → the auto easy run is back on 10/2
        e.c.delete(f"{API}/sessions/{mine['uid']}")
        e.c.patch(f"{API}/sessions/{easy['uid']}", json={"minutes": 20})   # (edited again, then reverted)
        out = e.c.post(f"{API}/sessions/{easy['uid']}/revert").json()
        assert (out["day"], out["minutes"]) == ("2026-10-02", 45)


def test_what_cannot_be_reverted(monkeypatch):
    with Env(monkeypatch) as e:
        ss = _sessions(e)["sessions"]
        mine = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "easy", "minutes": 30}).json()
        assert e.c.post(f"{API}/sessions/{mine['uid']}/revert").status_code == 400      # the user's own
        untouched = next(s for s in ss if s["day"] == "2026-10-04")
        assert e.c.post(f"{API}/sessions/{untouched['uid']}/revert").status_code == 400  # nothing to revert
        done = next(s for s in run(PS.load(e.db)) if s["state"] == "done")
        assert e.c.post(f"{API}/sessions/{done['uid']}/revert").status_code == 400       # done (9/29)
        assert e.c.post(f"{API}/sessions/nope/revert").status_code == 400
        # moved to another week: the tombstone stays (the session lives on as the user's own there)
        q = next(s for s in ss if s["day"] == "2026-10-01")
        e.c.patch(f"{API}/sessions/{q['uid']}", json={"day": "2026-10-07"})
        tomb = next(s for s in run(PS.load(e.db)) if s["state"] == "deleted" and s.get("note") == PS.MOVED_NOTE)
        assert e.c.post(f"{API}/sessions/{tomb['uid']}/revert").status_code == 400
        assert _sessions(e)["deleted_auto"] == []


def test_revertible_rules():
    new, _ = rec([], inputs(weeks=[next_week()]))
    by = {(s["week_start"], s.get("gen_key")): s for s in new}
    long = by[("2026-09-28", "long")]
    assert not PS.revertible(long, new, TODAY)                              # not edited
    assert PS.revertible({**long, "edited": True}, new, TODAY)
    assert not PS.revertible({**long, "edited": True}, new, "2026-10-05")   # a past day
    assert not PS.revertible({**long, "edited": True, "ext_key": "racecalc:1"}, new, TODAY)
    assert not PS.revertible({**long, "origin": "custom", "gen_key": None, "edited": True}, new, TODAY)
    tomb = {**long, "state": "deleted"}
    rest = [s for s in new if s is not long]
    assert PS.revertible(tomb, rest + [tomb], TODAY)
    assert not PS.revertible({**tomb, "note": PS.USER_DELETED}, rest, TODAY)
    from backend.engine.specific_phase import SRC_SIM
    sim = {**long, "uid": "sim", "origin": "custom", "gen_key": None, "title": "賽事模擬｜合歡山", "source": SRC_SIM,
           "edited": True}
    assert not PS.revertible(tomb, rest + [tomb, sim], TODAY)               # an accepted 賽事模擬 took the long day
    assert not PS.revertible(tomb, new + [tomb], TODAY)                     # the week has its long run already
    # two tombstones of one gen_key: one comes back
    t2 = {**tomb, "uid": "t2"}
    out, uids = PS.reset_rows(rest + [tomb, t2], TODAY)
    assert uids == [tomb["uid"]]
    assert [s["uid"] for s in PS.deleted_revertible(rest + [tomb, t2], TODAY)] == [tomb["uid"]]


# ---------------------------------------------------------------------------
# C: 依實際進度重排 with 「也重置我改過的課」
# ---------------------------------------------------------------------------

def test_reconcile_reset_edited_takes_every_edited_and_deleted_auto_session_back(monkeypatch):
    with Env(monkeypatch) as e:
        ss = _sessions(e)["sessions"]
        long = next(s for s in ss if s["day"] == "2026-10-04")
        q = next(s for s in ss if s["day"] == "2026-10-06")
        easy = _on(e, "2026-10-02", "easy")
        e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 90, "day": "2026-10-03"})
        e.c.patch(f"{API}/sessions/{q['uid']}", json={"title": "我的強度課"})
        e.c.delete(f"{API}/sessions/{easy['uid']}")
        mine = e.c.post(f"{API}/sessions", json={"day": "2026-10-08", "kind": "hike", "minutes": 180, "title": "合歡山"}).json()
        done = next(s for s in run(PS.load(e.db)) if s["state"] == "done")

        # not ticked: exactly as before (nothing to change), and the box says how many it would reset
        p = e.c.get(f"{API}/reconcile").json()
        assert p["changes"] == [] and p["resettable"] == 3 and p["reset_edited"] is False
        e.c.post(f"{API}/reconcile", json={})
        by = _by_uid(e)
        assert by[long["uid"]]["minutes"] == 90 and by[q["uid"]]["title"] == "我的強度課" and easy["uid"] not in by

        # ticked: the preview lists the three, tagged
        p = e.c.get(f"{API}/reconcile", params={"reset_edited": True}).json()
        rv = {c["uid"]: c for c in p["changes"] if c.get("reverted")}
        assert set(rv) == {long["uid"], q["uid"], easy["uid"]}
        assert rv[easy["uid"]]["action"] == "added" and rv[long["uid"]]["action"] == "changed"
        assert mine["uid"] not in {c["uid"] for c in p["changes"]}
        assert _by_uid(e)[long["uid"]]["minutes"] == 90                    # a preview saves nothing

        r = e.c.post(f"{API}/reconcile", json={"reset_edited": True})
        assert r.status_code == 200 and r.json()["reset_edited"] is True
        by = _by_uid(e)
        assert (by[long["uid"]]["day"], by[long["uid"]]["minutes"], by[long["uid"]]["edited"]) == ("2026-10-04", 120, False)
        assert by[q["uid"]]["title"] == "閾值 3×10 分" or by[q["uid"]]["title"].startswith("有氧間歇")
        assert not by[q["uid"]]["edited"]
        assert by[easy["uid"]]["day"] == "2026-10-02" and by[easy["uid"]]["state"] == "active"
        # the user's own session and the done one are untouched
        assert (by[mine["uid"]]["title"], by[mine["uid"]]["day"], by[mine["uid"]]["origin"]) == ("合歡山", "2026-10-08", "custom")
        after = next(s for s in run(PS.load(e.db)) if s["uid"] == done["uid"])
        assert after["state"] == "done" and after["done_by"]["index"] == done["done_by"]["index"]
        assert e.c.get(f"{API}/reconcile").json()["resettable"] == 0


def test_reconcile_reset_keeps_an_edited_past_session(monkeypatch):
    # a session still open on a past day (the data doesn't cover it yet) is history, not reset
    with Env(monkeypatch) as e:
        q = next(s for s in _sessions(e)["sessions"] if s["day"] == "2026-10-01")
        e.c.patch(f"{API}/sessions/{q['uid']}", json={"minutes": 40})
        e.inp = inputs(today="2026-10-02", weeks=e.inp["weeks"], horizon="2026-10-18")
        monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 2))
        e.c.post(f"{API}/reconcile", json={"reset_edited": True})
        row = next(s for s in run(PS.load(e.db)) if s["uid"] == q["uid"])
        assert row["minutes"] == 40 and row["edited"]
