"""
課表 page additions: deleting expired (past, never done) sessions — single and
「刪除所有過期未完成」, never regenerated / restored, pushed copies removed through
the workout-sync provider (mocked COROS) —, the 休息日 context-menu action
(a one-day blackout of kind rest that keeps the week's volume), and the
課表達成率 dashboard (engine/compliance.dashboard + GET /compliance).
Synthetic data only: hand-built inputs (test_plan_store.Env), in-memory DB.
"""
import json
from datetime import date

import pytest

from backend.db.models import PlanChangeLog
from backend.engine import blackouts as BL
from backend.engine import compliance as C
from backend.engine import plan_auto as PA
from backend.engine import plan_store as PS
from backend.sync import coros_workouts as CW
from backend.sync import workout_targets as WT
from backend.tests.test_blackouts import BEnv
from backend.tests.test_coros_workouts import run
from backend.tests.test_plan_store import ACT_929, API, Env, cur_plan, g, inputs


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def _two_days_on(e, monkeypatch):
    """Today 10/2: the 10/1 quality wasn't done; the generator re-places it on 10/3."""
    e.c.get(f"{API}/sessions")                      # the week generated on 9/30
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 2))
    cur = cur_plan(today="2026-10-02", sessions=[
        g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
        g("quality", "quality", "閾值 3×10 分", 60, "2026-10-03"),
        g("strength1", "strength", "肌力", 35, "2026-10-02"),
        g("easy1", "easy", "輕鬆跑", 45, "2026-10-02"),
        g("long", "long", "長時間輕鬆（山路）", 120, "2026-10-04")])
    e.inp = inputs(today="2026-10-02", cur=cur, weeks=e.inp["weeks"], horizon="2026-10-18")
    assert e.c.post(f"{API}/reconcile").status_code == 200


def _missed(e):
    return [s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["state"] == "missed"]


# ---------------------------------------------------------------------------
# expired sessions
# ---------------------------------------------------------------------------

def test_delete_one_expired_session_removes_it_from_coros_and_it_stays_gone(monkeypatch):
    with Env(monkeypatch) as e:
        e.c.post(f"{API}/push-coros?scope=week")
        assert 20261001 in {x["happenDay"] for x in e.fake.entities}
        _two_days_on(e, monkeypatch)
        miss = _missed(e)
        assert [s["day"] for s in miss] == ["2026-10-01"]
        cal = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-11").json()
        assert cal["expired_open"] == 1
        r = e.c.delete(f"{API}/sessions/{miss[0]['uid']}").json()
        assert r["expired"] and r["state"] == "deleted"
        assert r["coros"]["status"] == "ok" and r["coros"]["removed"] == 1
        assert 20261001 not in {x["happenDay"] for x in e.fake.entities}
        # reconcile again (a sync, a page load): not back, and the re-placed 10/3 quality is kept
        e.c.post(f"{API}/reconcile")
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        assert not [s for s in ss if s["day"] == "2026-10-01"]
        assert any(s["kind"] == "quality" and s["day"] == "2026-10-03" and s["state"] == "active" for s in ss)
        rows = run(PS.load(e.db))
        tomb = next(s for s in rows if s["uid"] == miss[0]["uid"])
        assert tomb["state"] == "deleted" and tomb["note"] == PS.USER_DELETED
        assert e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-11").json()["expired_open"] == 0


def test_delete_all_expired_and_bad_uids(monkeypatch):
    with Env(monkeypatch) as e:
        _two_days_on(e, monkeypatch)
        live = [s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["state"] == "active"]
        # a future session is not expired: refused, nothing changed
        r = e.c.post(f"{API}/sessions/expired/delete", json={"uids": [live[0]["uid"]]})
        assert r.status_code == 400
        assert len(_missed(e)) == 1
        r = e.c.post(f"{API}/sessions/expired/delete", json={}).json()
        assert r["deleted"] == 1 and r["coros"]["status"] == "none"          # never pushed
        assert _missed(e) == []
        assert e.c.post(f"{API}/sessions/expired/delete", json={}).json()["deleted"] == 0


def test_provider_login_problem_does_not_undo_the_delete(monkeypatch):
    with Env(monkeypatch) as e:
        e.c.post(f"{API}/push-coros?scope=week")
        _two_days_on(e, monkeypatch)

        async def boom(db, keys, **kw):
            raise WT.SyncAuthError("token expired")
        orig = CW.remove_keys
        monkeypatch.setattr(CW, "remove_keys", boom)
        r = e.c.post(f"{API}/sessions/expired/delete", json={}).json()
        assert r["deleted"] == 1 and r["coros"]["status"] == "auth" and r["coros"]["pending"] == 1
        assert _missed(e) == []
        assert 20261001 in {x["happenDay"] for x in e.fake.entities}
        monkeypatch.setattr(CW, "remove_keys", orig)          # logged in again: the next push takes it off
        e.c.post(f"{API}/push-coros?scope=week")
        assert 20261001 not in {x["happenDay"] for x in e.fake.entities}


def test_undo_of_an_auto_run_does_not_restore_a_deleted_expired_session(monkeypatch):
    with Env(monkeypatch) as e:
        _two_days_on(e, monkeypatch)
        miss = _missed(e)[0]
        before = next(s for s in run(PS.load(e.db)) if s["uid"] == miss["uid"])
        e.c.delete(f"{API}/sessions/{miss['uid']}")
        row = PlanChangeLog(athlete_id=1, trigger="sync", status="applied", summary="x",
                            items_json="[]", before_json=json.dumps([{**before, "state": "active"}]),
                            after_json=json.dumps([before]))
        e.db.add(row)
        run(e.db.commit())

        async def no_push(*a, **k):
            return {"status": "off"}
        monkeypatch.setattr(PA, "push_window", no_push)
        run(PA.undo(e.db, row.id))
        tomb = next(s for s in run(PS.load(e.db)) if s["uid"] == miss["uid"])
        assert tomb["state"] == "deleted" and tomb["note"] == PS.USER_DELETED


# ---------------------------------------------------------------------------
# 休息日
# ---------------------------------------------------------------------------

def test_rest_day_is_not_a_lost_day():
    rest = BL.Blackout(id="r", start="2026-10-07", end="2026-10-07", label="休息日", kind=BL.REST)
    trip = BL.Blackout(id="t", start="2026-10-09", end="2026-10-09", label="出差")
    bm = BL.blocked([rest, trip])
    assert BL.lost_days(bm, date(2026, 10, 5)) == [date(2026, 10, 9)]       # only the trip costs volume
    assert rest.to_dict()["kind"] == "rest" and "kind" not in trip.to_dict()
    assert BL.normalize([rest.to_dict()])[0]["kind"] == "rest"
    with pytest.raises(ValueError):
        BL.validate([{**rest.to_dict(), "kind": "holiday"}])


def test_api_rest_day_set_and_undo(monkeypatch):
    with BEnv(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        assert any(s["day"] == "2026-10-03" for s in ss) is False
        r = e.c.post(f"{API}/rest-days", json={"day": "2026-10-03"})
        assert r.status_code == 200
        saved = r.json()["blackouts"]
        assert [(b["start"], b["end"], b.get("kind")) for b in saved] == [("2026-10-03", "2026-10-03", "rest")]
        e.stored_bl = saved
        assert e.c.post(f"{API}/rest-days", json={"day": "2026-10-03"}).status_code == 400   # already
        assert e.c.post(f"{API}/rest-days", json={"day": "2026-09-29"}).status_code == 400   # past
        assert e.c.post(f"{API}/rest-days", json={"day": "x"}).status_code == 400
        # adding a session there is refused like any blocked day
        assert e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "easy"}).status_code == 400
        r = e.c.delete(f"{API}/rest-days/2026-10-03")
        assert r.status_code == 200 and r.json()["blackouts"] == []
        assert e.c.delete(f"{API}/rest-days/2026-10-03").status_code == 404


def test_api_rest_day_moves_the_users_own_session(monkeypatch):
    with BEnv(monkeypatch) as e:
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "easy", "title": "自己的跑", "minutes": 30})
        uid = r.json()["uid"]
        r = e.c.post(f"{API}/rest-days", json={"day": "2026-10-03"})
        assert r.status_code == 200
        e.stored_bl = r.json()["blackouts"]
        s = next(x for x in e.c.get(f"{API}/sessions").json()["sessions"] if x["uid"] == uid)
        assert s["state"] == "active" and s["day"] != "2026-10-03"


def test_api_context_menu_schedule_a_test_from_a_template(monkeypatch):
    from backend.engine import aet_test as AT
    from backend.engine import plan_prefs as PP
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs(aet_test_protocol="ua40"))
    with Env(monkeypatch) as e:
        e.inp["cur"]["test_suggestions"] = [{"kind": "cp", "protocol": "quick", "title": "CP 測試", "minutes": 40,
                                             "reason": "門檻過期", "replaces_long": False,
                                             "session": {"kind": "test", "title": "CP 測試 20 分全力",
                                                         "minutes": 37, "protocol": "quick"}}]
        e.inp["zone"] = {"events": [], "suggestions": [
            {"id": "cool", "kind": "test_suggestion", "tests": ["aet"], "title": "天氣轉涼了", "text": "清晨 < 25 °C",
             "earliest": None, "detected": "2026-09-27", "conditions": [], "caveat": "", "estimate": True,
             "source": "s", "evidence": {}}]}
        e.c.get(f"{API}/sessions")
        by = {s["id"]: s for s in e.c.get(f"{API}/suggestions").json()["suggestions"]}
        ok_day = by["test:cp:2026-09-28"]["options"][0]["day"]
        r = e.c.get(f"{API}/test-options?day={ok_day}").json()
        cp = next(o for o in r["options"] if o["kind"] == "cp")
        assert cp["ok"] and cp["reason"] == ""
        keys = [t["key"] for t in cp["templates"]]
        assert {"lib:stryd_cp_3_12", "lib:pal_test10", "cp_quick"} <= set(keys)
        assert all(not k.startswith("lib:ua_") for k in keys)
        aet = next(o for o in r["options"] if o["kind"] == "aet")
        assert [t["key"] for t in aet["templates"]] == ["lib:xu_e_drift", "lib:ua_aet_drift"] or \
            set(t["key"] for t in aet["templates"]) == {"lib:xu_e_drift", "lib:ua_aet_drift"}
        # the long run's day (10/4) is not offered: greyed with the reason
        bad = next(o for o in e.c.get(f"{API}/test-options?day=2026-10-04").json()["options"] if o["kind"] == "cp")
        assert not bad["ok"] and "長跑" in bad["reason"]
        # a template from another test kind is refused; a CP template goes in with its steps
        assert e.c.post(f"{API}/suggestions/accept", json={"id": "test:cp:2026-09-28", "day": ok_day,
                                                           "template": "lib:ua_aet_drift"}).status_code == 400
        out = e.c.post(f"{API}/suggestions/accept", json={"id": "test:cp:2026-09-28", "day": ok_day,
                                                          "template": "lib:stryd_cp_3_12"}).json()
        s = out["sessions"][0]
        assert s["kind"] == "test" and s["day"] == ok_day and "3′" in s["title"] and s["protocol"] == "standard"
        assert s["steps"] and s["steps"]["items"] and s["minutes"] > 30
        ids = {x["id"] for x in e.c.get(f"{API}/suggestions").json()["suggestions"]}
        assert "test:cp:2026-09-28" not in ids                                   # accepted, like 排入
        # the zone AeT retest with the UA 60′ template
        z = next(x for x in e.c.get(f"{API}/suggestions").json()["suggestions"] if x["id"] == "zone:cool")
        zday = z["tests"][0]["options"][0]["day"]
        out = e.c.post(f"{API}/suggestions/accept", json={"id": "zone:cool", "day": zday, "test": "aet",
                                                          "template": "lib:ua_aet_drift"}).json()
        assert AT.is_aet_session(out["sessions"][0]), out


# ---------------------------------------------------------------------------
# 課表達成率
# ---------------------------------------------------------------------------

def _s(uid, day, kind, state, minutes=60, tss=50.0, comp=None, done_by=None):
    return {"uid": uid, "day": day, "week_start": "2026-09-21" if day < "2026-09-28" else "2026-09-28",
            "kind": kind, "title": kind, "minutes": minutes, "tss": tss, "tss_est": tss, "state": state,
            "compliance": comp, "done_by": done_by}


def _comp(level, pct, off=False, wrong=False):
    return {"level": level, "pct": pct, "duration_pct": pct, "tss_pct": pct, "wrong_type": wrong,
            **({"off_plan": True, "off_text": "沒照課表：排強度課，實際跑輕鬆"} if off else {})}


def _done(moving_s=3600, tss=50):
    return {"index": 1, "moving_s": moving_s, "tss": tss, "category_label": "路跑"}


def test_dashboard_statuses_totals_kinds_and_streak():
    ss = [
        _s("a", "2026-09-22", "easy", "done", comp=_comp("green", 98), done_by=_done(3500, 49)),
        _s("b", "2026-09-24", "quality", "done", comp=_comp("yellow", 70, off=True), done_by=_done(3600, 35)),
        _s("c", "2026-09-27", "long", "missed", minutes=120, tss=100),
        _s("d", "2026-09-29", "easy", "done", comp=_comp("yellow", 70), done_by=_done(2500, 35)),
        _s("e", "2026-09-29", "notice", "active"),                         # never counted
        _s("f", "2026-09-30", "easy", "active"),                           # today, not done yet
        _s("g", "2026-10-03", "long", "active"),                           # future
        _s("h", "2026-09-28", "strength", "active", minutes=30, tss=10),   # past, data not covered
    ]
    weeks = [{"start": "2026-09-21", "end": "2026-09-27", "planned_tss": 200, "done_tss": 84, "planned_hours": 4,
              "done_hours": 2, "compliance": None},
             {"start": "2026-09-28", "end": "2026-10-04", "planned_tss": 250, "done_tss": 35, "planned_hours": 5,
              "done_hours": 0.7, "compliance": None}]
    d = C.dashboard(ss, weeks, "2026-09-30", "2026-09-21", "2026-10-04")
    st = {r["uid"]: r["status"] for r in d["sessions"]}
    assert st == {"a": "done", "b": "off_plan", "c": "missed", "d": "partial", "h": "open"}
    assert [r["uid"] for r in d["sessions"]][:2] == ["d", "h"]              # newest first
    t = d["totals"]
    assert (t["due"], t["completed"], t["done"], t["missed"], t["open"]) == (4, 3, 1, 1, 1)
    assert t["rate"] == 0.75 and t["ok_rate"] == 0.25
    assert t["planned_tss"] == 250 and t["actual_tss"] == 119 and t["tss_pct"] == 48
    w1, w2 = d["weeks"]
    assert (w1["due"], w1["completed"], w1["rate"]) == (3, 2, round(2 / 3, 3))
    assert (w2["due"], w2["completed"], w2["rate"], w2["current"]) == (1, 1, 1.0, True)
    assert w1["planned_tss"] == 200 and w2["done_hours"] == 0.7
    # the running week reached 80 % (counts); last week 67 % breaks it
    assert d["streak"]["weeks"] == 1 and d["streak"]["broken"] is True
    kinds = {k["kind"]: k for k in d["by_kind"]}
    assert list(kinds) == ["easy", "long", "quality", "strength"]
    assert kinds["easy"]["due"] == 2 and kinds["easy"]["completed"] == 2 and kinds["long"]["missed"] == 1
    assert "notice" not in kinds
    miss = next(r for r in d["sessions"] if r["uid"] == "c")
    assert miss["pct"] == 0 and miss["actual_tss"] is None


def test_dashboard_phase_progress():
    ss = [_s("a", "2026-09-22", "easy", "done", comp=_comp("green", 100), done_by=_done()),
          _s("b", "2026-09-24", "easy", "missed"),
          _s("c", "2026-10-03", "long", "active", tss=120)]
    ph = {"kind": "base", "label": "基礎期", "start": "2026-09-21", "end": "2026-10-04"}
    d = C.dashboard(ss, [], "2026-09-28", "2026-09-21", "2026-09-27", phase=ph, phase_sessions=ss)
    p = d["phase"]
    assert (p["days_total"], p["days_done"], p["time_pct"]) == (14, 7, 50)
    assert (p["sessions_total"], p["sessions_left"], p["due"], p["completed"], p["rate"]) == (3, 1, 2, 1, 0.5)
    assert p["planned_tss"] == 220


def test_api_compliance_and_page(monkeypatch):
    from backend.api import plan_sessions
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda a, b: {
        "activities": [x for x in (ACT_929,) if a <= x["date"] <= b],
        "phases": [{"kind": "base", "label": "基礎期", "start": "2026-09-01", "end": "2026-11-15"}]})
    with Env(monkeypatch) as e:
        _two_days_on(e, monkeypatch)
        r = e.c.get(f"{API}/compliance?start=2026-09-21&end=2026-10-04")
        assert r.status_code == 200
        b = r.json()
        st = {x["day"]: x["status"] for x in b["sessions"]}
        assert st["2026-10-01"] == "missed" and st["2026-09-29"] in ("done", "partial", "off_plan")
        assert b["totals"]["due"] >= 2 and b["phase"]["label"] == "基礎期"
        assert [w["start"] for w in b["weeks"]] == ["2026-09-21", "2026-09-28"]
        # 課表統計's period filter: the plan's phases (falls back to the range's phases)
        assert [p["label"] for p in b["plan_phases"]] == ["基礎期"]
        # a deleted expired session leaves the dashboard
        uid = next(x["uid"] for x in b["sessions"] if x["status"] == "missed")
        e.c.delete(f"{API}/sessions/{uid}")
        b2 = e.c.get(f"{API}/compliance?start=2026-09-21&end=2026-10-04").json()
        assert uid not in {x["uid"] for x in b2["sessions"]}
        assert e.c.get(f"{API}/compliance?start=2026-10-04&end=2026-09-21").status_code == 400
        assert e.c.get(f"{API}/compliance?start=2025-01-01&end=2026-10-04").status_code == 400
        p = e.c.get(f"{API}/compliance/page")
        assert p.status_code == 200 and "compliance" in p.text
        # the 日曆 | 課表統計 switch is the viewer's mode cards; the 負荷比 chart sits above the weeks
        assert 'class="modesw"' in p.text and "ra-plot" in p.text and 'id="phases"' in p.text


def test_api_compliance_plan_phases(monkeypatch):
    from backend.api import plan_sessions
    every = [{"kind": "base", "label": "基礎期", "start": "2026-09-01", "end": "2026-10-10"},
             {"kind": "specific", "label": "專項期", "start": "2026-10-11", "end": "2026-12-05"}]
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda a, b: {
        "activities": [], "plan_phases": every,
        "phases": [p for p in every if p["end"] >= a and p["start"] <= b]})
    with Env(monkeypatch) as e:
        b = e.c.get(f"{API}/compliance?start=2026-09-21&end=2026-10-04").json()
        assert [p["kind"] for p in b["phases"]] == ["base"]
        assert [p["kind"] for p in b["plan_phases"]] == ["base", "specific"]
        # a future phase's range: the planned weeks show (no actuals yet)
        f = e.c.get(f"{API}/compliance?start=2026-10-11&end=2026-12-05").json()
        assert [w["start"] for w in f["weeks"]][:2] == ["2026-10-05", "2026-10-12"]
