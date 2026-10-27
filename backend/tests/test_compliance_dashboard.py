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
        g("long", "long", "LSD（山路）", 120, "2026-10-04")])
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
            **({"off_plan": True, "off_text": "換了課：排強度課，實際跑輕鬆"} if off else {})}


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
        # 天 buckets: one row per day of the range, same planned / done numbers as the weeks
        days = b["days"]
        assert [x["start"] for x in days][0] == "2026-09-21" and len(days) == 14
        assert all(x["start"] == x["end"] for x in days)
        assert len(f["days"]) == 56


def test_dashboard_day_buckets_count_their_sessions():
    ss = [_s("a", "2026-09-22", "easy", "done", comp=_comp("green", 98), done_by=_done(3500, 49)),
          _s("c", "2026-09-22", "long", "missed", minutes=120, tss=100)]
    days = [{"start": d, "end": d, "planned_tss": 150 if d == "2026-09-22" else 0, "done_tss": 49 if d == "2026-09-22" else 0,
             "planned_hours": 3, "done_hours": 1} for d in ("2026-09-21", "2026-09-22")]
    d = C.dashboard(ss, [], "2026-09-30", "2026-09-21", "2026-09-22", day_rows=days)
    d0, d1 = d["days"]
    assert (d0["due"], d0["rate"]) == (0, None)
    assert (d1["due"], d1["completed"], d1["rate"], d1["planned_tss"]) == (2, 1, 0.5, 150)


# ---------------------------------------------------------------------------
# SP-398: neutral words (完成／部分／換了課／跳過), no red on 課表統計
# ---------------------------------------------------------------------------

def _catalog(loc, ns):
    from pathlib import Path
    return json.loads((Path(C.__file__).parents[1] / "static" / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def test_status_words_are_neutral_sp398():
    assert C.LEVEL_LABEL == {"green": "完成", "yellow": "部分", "red": "部分", "missed": "跳過"}
    assert C.session_compliance({"kind": "easy", "state": "missed", "day": "2026-09-29"})["label"] == "跳過"
    vs = {"off_plan": True, "text": "換了課：排強度課，實際跑輕鬆", "intensity_pct": 30}
    assert C.with_plan_check(_comp("green", 100), vs)["label"] == "換了課"
    # one source for the words: the cards' common.legend.status.* (課表統計 reads them too)
    zc, ec = _catalog("zh-TW", "common"), _catalog("en", "common")
    assert [zc["legend.status." + s] for s in ("done", "partial", "off_plan", "missed")] == ["完成", "部分", "換了課", "跳過"]
    assert [ec["legend.status." + s] for s in ("done", "partial", "off_plan", "missed")] == \
        ["Done", "Partly", "Different session", "Skipped"]
    zh = _catalog("zh-TW", "compliance")
    assert not [k for k in zh if k.startswith("status.") and k != "status.open"]
    words = ("沒做", "沒照課表", "走樣", "沒達到", "未完成")
    assert not [(k, w) for k, v in zh.items() for w in words if w in v]


def test_levels_legend_has_one_source_sp398():
    # the four levels are written once — the cards' legend (common.legend.*_tip + levels_note, the
    # thresholds from the payload) — and both 課表's dialog and 課表統計 read it through sesschip.js
    for loc in ("zh-TW", "en"):
        cm = _catalog(loc, "common")
        assert "legend.levels" not in cm and cm["legend.levels_note"] and "{green}" in cm["legend.done_tip"]
        assert "vs.levels_tip" not in _catalog(loc, "schedule")
    from pathlib import Path
    static = Path(C.__file__).parents[1] / "static"
    assert "SessChip.levelsText(" in (static / "schedule.html").read_text("utf-8")
    comp = (static / "compliance.html").read_text("utf-8")
    assert "SessChip.levelsText(" in comp and "/static/sesschip.js" in comp
    assert 'T("levels_note")' in (static / "sesschip.js").read_text("utf-8")


def test_compliance_page_has_no_red_for_skipped_sp398():
    import re
    from pathlib import Path
    html = (Path(C.__file__).parents[1] / "static" / "compliance.html").read_text("utf-8")
    css = {m.group(1): m.group(2) for m in re.finditer(r"\.(st-missed|bg-missed|st-off_plan)\s*\{([^}]*)\}", html)}
    assert "--faint" in css["st-missed"] and "--bad" not in css["st-missed"]
    assert "--faint" in css["bg-missed"] and "--bad" not in css["bg-missed"]
    assert "--watch" in css["st-off_plan"]


# ---------------------------------------------------------------------------
# SP-400: 本週 numbers that only add up, 連續有練, the plan's adjustments this week
# ---------------------------------------------------------------------------

def _wk_done(uid, day, kind="easy", climb=0.0, km=0.0, moving_s=3600, status="green"):
    return _s(uid, day, kind, "done", comp=_comp(status, 100 if status == "green" else 70),
              done_by={"index": hash(uid) % 1000, "moving_s": moving_s, "tss": 50, "category_label": "越野跑",
                       "climbing_m": climb, "distance_km": km})


def _this_week():
    # today Thu 10-01; the week 09-28 … 10-04: 6 sessions, 4 done, 1 skipped, 1 still ahead
    return [_wk_done("a", "2026-09-28", climb=120, km=8.0), _wk_done("b", "2026-09-29", "quality", climb=300, km=10.5),
            _s("c", "2026-09-30", "long", "missed", minutes=120, tss=100),
            _wk_done("d", "2026-09-30", "strength", moving_s=1800),
            _wk_done("e", "2026-10-01", "easy", climb=580, km=12.0, status="yellow"),
            _s("f", "2026-10-03", "long", "active", minutes=150, tss=120)]


def test_dashboard_this_week_adds_up_sp400():
    ss = _this_week()
    d = C.dashboard(ss, [], "2026-10-01", "2026-09-28", "2026-10-04", week_sessions=ss)
    w = d["week"]
    assert (w["start"], w["end"]) == ("2026-09-28", "2026-10-04")
    assert w["completed"] == 4 and w["climb_m"] == 1000 and w["distance_km"] == 30.5
    assert w["actual_hours"] == 3.0                                   # strength left out, like the week's volume
    # one dot per session: done ones by kind and status, the skipped one not drawn, the rest still ahead
    assert [(x["uid"], x["status"]) for x in w["dots"]] == [
        ("a", "done"), ("b", "done"), ("d", "done"), ("e", "partial"), ("f", "upcoming")]
    # the period's totals have the same sums
    assert d["totals"]["climb_m"] == 1000 and d["totals"]["distance_km"] == 30.5


def _stored(uid, day, state, kind="easy"):
    import datetime as _dt
    mon = (_dt.date.fromisoformat(day) - _dt.timedelta(days=_dt.date.fromisoformat(day).weekday())).isoformat()
    return {"uid": uid, "day": day, "week_start": mon, "kind": kind, "state": state}


def test_active_streak_counts_weeks_with_a_session_done_sp400():
    every = [_stored("w1", "2026-09-01", "done"), _stored("w1b", "2026-09-03", "missed"),
             # 09-07 week: nothing planned (a paused / rest week) — does not break it
             _stored("w3", "2026-09-15", "missed"), _stored("w3b", "2026-09-17", "done", "strength"),
             _stored("w4", "2026-09-22", "done"),
             _stored("n", "2026-09-23", "active", "notice"),                  # the 課表待確認 reminder: never counted
             _stored("w5", "2026-10-03", "active")]                           # this week: nothing done yet, still going
    d = C.dashboard([], [], "2026-10-01", "2026-09-28", "2026-10-04", all_sessions=every)
    assert d["active_streak"] == {"weeks": 3, "best": 3}
    # a week with sessions but none done breaks it
    every.insert(0, _stored("w0", "2026-08-25", "done"))
    every.insert(1, _stored("w0x", "2026-08-18", "missed"))
    every.append(_stored("w4x", "2026-09-29", "done"))                          # this week counts once one is done
    d = C.dashboard([], [], "2026-10-01", "2026-09-28", "2026-10-04", all_sessions=every)
    assert d["active_streak"] == {"weeks": 5, "best": 5}
    every.append(_stored("w2x", "2026-09-10", "missed"))                         # 09-07 week: all skipped -> breaks
    d = C.dashboard([], [], "2026-10-01", "2026-09-28", "2026-10-04", all_sessions=every)
    assert d["active_streak"] == {"weeks": 3, "best": 3}


def test_dashboard_lists_this_weeks_auto_adjustments_sp400():
    ss = _this_week()
    marks = {"f": {"state": "auto", "rule": "missed_long", "reason": "長跑延到週六：週三沒跑", "diff": []},
             "e": {"state": "user"},
             "x": {"state": "auto", "rule": "cp", "reason": "CP 變更"}}
    ss = ss + [_s("x", "2026-10-02", "quality", "active")]
    d = C.dashboard(ss, [], "2026-10-01", "2026-09-28", "2026-10-04", week_sessions=ss, marks=marks)
    assert [(a["uid"], a["rule"], a["reason"]) for a in d["adjusted"]] == [("f", "missed_long", "長跑延到週六：週三沒跑")]
    assert d["adjusted"][0]["day"] == "2026-10-03" and d["adjusted"][0]["title"] == "long"
    assert C.dashboard(ss, [], "2026-10-01", "2026-09-28", "2026-10-04", week_sessions=ss)["adjusted"] == []


def test_api_compliance_has_this_week_streak_and_adjustments_sp400(monkeypatch):
    from backend.api import plan_sessions
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda a, b: {
        "activities": [x for x in (ACT_929,) if a <= x["date"] <= b], "phases": []})
    with Env(monkeypatch) as e:
        _two_days_on(e, monkeypatch)
        # a past range: 本週 is still this week (10-02 is a Friday: 09-28 … 10-04)
        b = e.c.get(f"{API}/compliance?start=2026-09-01&end=2026-09-20").json()
        w = b["week"]
        assert (w["start"], w["end"]) == ("2026-09-28", "2026-10-04")
        assert w["completed"] == 1 and [x["uid"] for x in w["dots"] if x["status"] != "upcoming"]
        assert b["active_streak"]["weeks"] == 1
        assert isinstance(b["adjusted"], list)


# ---------------------------------------------------------------------------
# SP-403: 這週亮點 on the dashboard
# ---------------------------------------------------------------------------

def test_dashboard_highlight_sp403():
    ss = _this_week()
    ss[1]["done_by"]["index"] = 77
    ss[1]["title"] = "爬坡間歇"
    week = [{"index": 77, "date": "2026-09-29", "category": "trail", "distance_km": 30.0, "climbing_m": 300,
             "moving_s": 3600}]
    before = [{"index": i, "date": f"2026-08-{i:02d}", "category": "trail", "distance_km": 10.0, "climbing_m": 500,
               "moving_s": 3600} for i in range(1, 13)]
    d = C.dashboard(ss, [], "2026-10-01", "2026-09-28", "2026-10-04", week_sessions=ss, week_acts=week,
                    before_acts=before)
    h = d["highlight"]
    assert (h["kind"], h["what"], h["title"], h["value"]) == ("pr", "distance", "爬坡間歇", 30.0)
    # nothing done, nothing run: no highlight
    assert C.dashboard([], [], "2026-10-01", "2026-09-28", "2026-10-04")["highlight"] is None


def test_api_compliance_highlight_sp403(monkeypatch):
    from backend.api import plan_sessions
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda a, b: {
        "activities": [x for x in (ACT_929,) if a <= x["date"] <= b], "phases": []})
    monkeypatch.setattr(plan_sessions, "_run_history", lambda before: [])
    with Env(monkeypatch) as e:
        _two_days_on(e, monkeypatch)
        b = e.c.get(f"{API}/compliance?start=2026-09-21&end=2026-10-04").json()
        assert b["highlight"] == {"kind": "count", "n": 1}            # no record, no last week: the count
        monkeypatch.setattr(plan_sessions, "_run_history", lambda before: 1 / 0)
        assert e.c.get(f"{API}/compliance?start=2026-09-21&end=2026-10-04").json()["highlight"] is not None


# ---------------------------------------------------------------------------
# SP-401: no pass line, no denominator to fall short of
# ---------------------------------------------------------------------------

def _fn(src, name):
    import re
    m = re.search(r"\n(?:const %s = |function %s\()" % (name, name), src)
    assert m, name
    nxt = re.search(r"\n(?:function |const [A-Za-z]+ = \(|// )", src[m.end():])
    return src[m.start(): m.end() + (nxt.start() if nxt else len(src))]


def test_weekly_chart_counts_sessions_without_a_pass_line_sp401():
    from pathlib import Path
    static = Path(C.__file__).parents[1] / "static"
    html = (static / "compliance.html").read_text("utf-8")
    trend = _fn(html, "renderTrend")
    assert "markLine" not in trend and "threshold" not in trend and '"bar"' in trend and "completed" in trend
    assert "--watch" not in trend
    # the load tile's bar: up to 100 %, no target mark
    assert '<b style="left' not in html and "/ 1.5" not in html
    zh, en = _catalog("zh-TW", "compliance"), _catalog("en", "compliance")
    assert "trend.sub" not in zh and "{pct}" not in zh["trend.tip"] and "黑線" not in zh["kpi.tss_tip"]
    assert set(k for k in zh if k.startswith("trend.")) == set(k for k in en if k.startswith("trend."))


def test_schedule_week_sum_is_done_tss_without_colour_sp401():
    from pathlib import Path
    html = (Path(C.__file__).parents[1] / "static" / "schedule.html").read_text("utf-8")
    comp = _fn(html, "compW")
    # SP-497: the narrow week column says 「完成的訓練負荷」 only (wk.done_col), not the bilingual wk.done_tss
    assert "cp-" not in comp and "w.compliance.pct" not in comp and 'tt("wk.done_col")' in comp
    assert 'tt("wk.plan_tss_tip"' in comp
    i = html.index('<div class="ag-sum')
    ag = html[i:html.index('${days.join("")}', i)]
    assert "完成度" not in ag and "cp-${" not in ag and 'tt("wk.done_tss")' in ag
    for loc in ("zh-TW", "en"):
        sch = _catalog(loc, "schedule")
        assert sch["wk.done_tss"] and sch["wk.done_col"] and "{p}" in sch["wk.plan_tss_tip"]


def test_no_old_completion_words_on_the_card_pages_sp398():
    # after the card line (SP-395…402): the 課表 / 總覽 / 課表統計 pages and sesschip.js say 換了課 / 跳過;
    # the dialog strips plan_match's 「換了課：」 (no 「沒照課表」 heading over a 「換了課：…」 line)
    from pathlib import Path
    static = Path(C.__file__).parents[1] / "static"
    for f in ("schedule.html", "overview.html", "compliance.html", "sesschip.js"):
        src = (static / f).read_text("utf-8")
        assert "沒照課表" not in src and "未完成" not in src, f
    sch = (static / "schedule.html").read_text("utf-8")
    assert 'vs.text.replace(/^換了課：/, "")' in sch
    for loc in ("zh-TW",):
        for ns in ("schedule", "overview", "common", "compliance"):
            cat = _catalog(loc, ns)
            assert not [k for k, v in cat.items() if "沒照課表" in v or "未完成" in v], ns


# ---------------------------------------------------------------------------
# SP-446: switching the period is slow — GET /compliance built the 課表 page's watch-push status
# (PS.push_dict → plan_prefs.load, 33 sqlite reads per active session) for every session of the
# plan, and the page never shows it
# ---------------------------------------------------------------------------

def test_api_compliance_skips_the_watch_push_status_sp446(monkeypatch):
    from backend.api import plan_sessions
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda a, b: {
        "activities": [x for x in (ACT_929,) if a <= x["date"] <= b], "phases": []})
    monkeypatch.setattr(plan_sessions, "_run_history", lambda before: [])
    with Env(monkeypatch) as e:
        _two_days_on(e, monkeypatch)
        view = {s["uid"]: s for s in e.c.get(f"{API}/sessions").json()["sessions"]}
        calls = []
        real = PS.push_dict
        monkeypatch.setattr(PS, "push_dict", lambda s: calls.append(s["uid"]) or real(s))
        b = e.c.get(f"{API}/compliance?start=2026-09-21&end=2026-10-04").json()
        assert calls == []
        # what the page does read from a session's view is still there: the 強度課 family (SP-79)
        q = next(x for x in b["week"]["dots"] if x["kind"] == "quality")
        assert q["family"] == (view[q["uid"]].get("quality_family") or {}).get("id")
        # deleted / superseded rows stay out, as in the 課表 view
        assert {x["uid"] for x in b["week"]["dots"]} <= set(view)


def test_range_extras_can_skip_the_tss_rates_sp446(monkeypatch):
    """The dataset's TSS / h (180 days of runs through the session classifier) is the plan inputs'
    own tss_per_category (week_plan, same dataset and day): 課表統計 reads that, so its extras skip it."""
    from types import SimpleNamespace
    from backend.api import overview as OVA
    from backend.api import plan_sessions as P
    from backend.engine import overview as O
    from backend.engine import planning
    calls = []
    monkeypatch.setattr(OVA, "_dataset", lambda: SimpleNamespace(today=O.date_to_day(date(2026, 10, 2))))
    monkeypatch.setattr(P, "activity_rows", lambda ds, a, b: [])
    monkeypatch.setattr(P, "_status_for", lambda ds, today: SimpleNamespace(plan=None, goals={}))
    monkeypatch.setattr(planning, "phases", lambda plan, lo, hi: [])
    monkeypatch.setattr(O, "_tss_per_hour", lambda ds, today: calls.append(today) or {"road": 61.0})
    assert P._range_extras("2026-09-01", "2026-10-02")["tph"] == {"road": 61.0} and len(calls) == 1
    tok = P._EXTRAS_TPH.set(False)
    try:
        out = P._range_extras("2026-09-01", "2026-10-02")
    finally:
        P._EXTRAS_TPH.reset(tok)
    assert out.get("tph") is None and len(calls) == 1


def test_api_compliance_prices_sessions_with_the_plan_inputs_rates_sp446(monkeypatch):
    from backend.api import plan_sessions
    seen = []

    def extras(a, b, tph=None):
        seen.append(plan_sessions._EXTRAS_TPH.get())
        return {"activities": [x for x in (ACT_929,) if a <= x["date"] <= b], "phases": [],
                **({"tph": tph} if tph else {})}
    real, tphs = plan_sessions.plan_rates, []

    def rates(inp, every, tph=plan_sessions._INPUTS_TPH):
        tphs.append(tph)
        return real(inp, every, tph)
    monkeypatch.setattr(plan_sessions, "_run_history", lambda before: [])
    with Env(monkeypatch) as e:
        _two_days_on(e, monkeypatch)
        monkeypatch.setattr(plan_sessions, "plan_rates", rates)
        # a dataset TSS / h in the extras is not read: the inputs' own rates price the plan
        monkeypatch.setattr(plan_sessions, "_range_extras", lambda a, b: extras(a, b, {"easy": 200.0, "road": 200.0}))
        assert e.c.get(f"{API}/compliance?start=2026-09-28&end=2026-10-18").status_code == 200
    assert seen == [False]
    assert tphs == [plan_sessions._INPUTS_TPH]


def test_period_switch_page_side_sp446():
    """課表統計's period switch (static check): the 負荷比 request runs next to the stats one (not
    after it), an answer for a period no longer picked is dropped, and 本周期 stays within the
    server's 371 days (a longer phase: its last 371 days, not 「載入失敗」)."""
    from pathlib import Path
    html = (Path(C.__file__).parents[1] / "static" / "compliance.html").read_text("utf-8")
    load = html[html.index("\nasync function load("):]
    load = load[: load.index("\n}\n") + 2]
    assert load.index("loadRatio(a, b)") < load.index("await fetch(")
    assert "seq !== LOAD.seq" in load
    rng = _fn(html, "range")
    assert "MAX_DAYS" in rng.split('S.period === "phase"')[1].split("\n")[0]


# ---------------------------------------------------------------------------
# SP-968: one GET /compliance read the same settings again and again — read_setting opens a new
# sqlite connection each time, and the range's activities (activity_rows → classify → the HR
# account / zone model / data source, per activity) asked for the same few keys thousands of times
# over a long range. The whole request now runs inside datasource.settings_memo() (as SP-752's
# 課表輸入): each setting once per request, the threadpool work (the range extras, the history)
# included; the next request reads them again.
# ---------------------------------------------------------------------------

def test_api_compliance_reads_each_setting_once_per_request_sp968(tmp_path, monkeypatch):
    import sqlite3
    from backend.api import plan_sessions
    from backend.engine.wko5expr import datasource as DS
    db = tmp_path / "settings.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE user_settings (user_id INTEGER, key TEXT, value_json TEXT)")
    con.execute("INSERT INTO user_settings VALUES (1, 'plan.hr_zone_model', ?)", (json.dumps("lthr"),))
    con.commit()
    con.close()
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    reads, real = [], DS._setting_row
    monkeypatch.setattr(DS, "_setting_row", lambda path, key, uid: reads.append(key) or real(path, key, uid))
    acts = [ACT_929, {**ACT_929, "index": 12, "date": "2026-09-30"}, {**ACT_929, "index": 13, "date": "2026-10-01"}]
    seen = []

    def extras(a, b):                       # the real one classifies each activity: per-activity reads
        got = [x for x in acts if a <= x["date"] <= b]
        seen.extend(DS.read_setting("plan.hr_zone_model") for _ in got)
        return {"activities": got, "phases": []}

    def history(before):
        seen.append(DS.read_setting("plan.hr_zone_model"))
        return []
    monkeypatch.setattr(plan_sessions, "_range_extras", extras)
    monkeypatch.setattr(plan_sessions, "_run_history", history)
    with Env(monkeypatch) as e:
        _two_days_on(e, monkeypatch)
        for _ in range(2):
            reads.clear()
            seen.clear()
            assert e.c.get(f"{API}/compliance?start=2026-09-21&end=2026-10-04").status_code == 200
            assert seen == ["lthr"] * 4                      # three activities and the history
            assert reads.count("plan.hr_zone_model") == 1    # one DB read for the whole request
            assert [k for k in set(reads) if reads.count(k) > 1] == []
