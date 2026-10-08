"""
SP-318 課表標記: every session shows one of three states, derived from plan_change_log
(engine/plan_marks.py, no second copy):

  auto — changed by the automatic run (an adapt rule, a CP change, a re-plan): rule + before → after
  user — the user's own (edited: an edit, a drag, a 交換, a 復原, a session they added)
  none — untouched

Checked here: the three states, the marker agrees with the change log (and with what is pushed
to COROS), 復原 clears the auto marker, the user's edit wins over plan_auto (the existing reconcile
rule) and the 課表 page / 總覽 read the same marker (both use GET /calendar). Synthetic data, an
in-memory DB, COROS never called (push_sessions replaced).
"""
import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.api import plan_sessions as API
from backend.db.models import PlanChangeLog
from backend.engine import plan_auto as PA
from backend.engine import plan_marks as PMK
from backend.engine import plan_store as PS
from backend.tests.test_coros_workouts import make_db, run
from backend.tests.test_plan_auto import Box, Push, base_inputs

STATIC = Path(__file__).resolve().parents[1] / "static"
TODAY = "2026-09-30"
CAL = ("2026-09-28", "2026-10-04")


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    from datetime import date
    from backend.sync import coros_workouts as CW
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    monkeypatch.setattr(API, "_range_extras", lambda a, b: {"activities": [], "phases": []})


# ---------------------------------------------------------------------------
# pure: marks(sessions, entries, today)
# ---------------------------------------------------------------------------

def s(uid, day="2026-10-02", kind="easy", title="輕鬆跑", minutes=45, edited=False, state="active",
      origin="auto", target="", detail=""):
    return {"uid": uid, "day": day, "kind": kind, "title": title, "minutes": minutes, "edited": edited,
            "state": state, "origin": origin, "target": target, "detail": detail, "tss": minutes * 0.8,
            "terrain": None, "protocol": None}


def entry(id, items, status="applied", after_uids=(), at="2026-09-30T06:00:00Z"):
    return {"id": id, "status": status, "at": at, "items": items, "after_uids": list(after_uids)}


def item(uid, action="changed", rule=None, reason="", before=None, day="2026-10-02"):
    it = {"action": action, "uid": uid, "day": day, "kind": "quality", "title": "x", "reason": reason, "rule": rule}
    if before is not None:
        it["before"] = before
    return it


def test_three_states():
    ss = [s("a"), s("b", edited=True), s("c", day="2026-10-03", kind="quality", title="閾值 3×10 分", minutes=60)]
    es = [entry(7, [item("c", rule="missed_quality", reason="閾值 3×10 分延到週六：週四沒跑",
                         before={"day": "2026-10-01"})])]
    m = PMK.marks(ss, es, TODAY)
    assert PMK.of(m, "a")["state"] == "none"
    assert PMK.of(m, "b")["state"] == "user" and not PMK.of(m, "b").get("restored")
    c = PMK.of(m, "c")
    assert c["state"] == "auto" and c["rule"] == "missed_quality" and c["entry"] == 7
    assert c["reason"].startswith("閾值 3×10 分延到週六")
    assert c["diff"] == [{"key": "day", "before": "2026-10-01", "after": "2026-10-03"}]


def test_user_edit_wins_over_an_auto_change():
    # an auto change first, then the user edited the session: it is the user's now
    es = [entry(3, [item("a", rule="fatigue", before={"minutes": 60})])]
    assert PMK.of(PMK.marks([s("a", edited=True)], es, TODAY), "a")["state"] == "user"


def test_restored_session_is_the_users_with_a_restore_flag():
    es = [entry(3, [item("a", before={"minutes": 30})], status="undone"),
          entry(4, [item("a", before={"minutes": 30})], status="restore", after_uids=["a"])]
    m = PMK.marks([s("a", minutes=45, edited=True)], es, TODAY)
    assert PMK.of(m, "a") == {"state": "user", "restored": True}


def test_undone_entries_never_mark():
    es = [entry(3, [item("a", rule="missed_long", before={"day": "2026-10-01"})], status="undone")]
    assert PMK.of(PMK.marks([s("a")], es, TODAY), "a")["state"] == "none"


def test_net_change_reverted_is_no_marker():
    # moved Thu -> Sat by a rule, then the next run put it back: nothing changed in the end
    es = [entry(1, [item("a", rule="overhard", before={"day": "2026-10-02"})]),
          entry(2, [item("a", before={"day": "2026-10-03"})])]
    assert PMK.of(PMK.marks([s("a", day="2026-10-02")], es, TODAY), "a")["state"] == "none"


def test_chain_shows_first_before_and_current_after_with_the_adapt_rule_first():
    es = [entry(1, [item("a", rule="fatigue", before={"minutes": 60})]),
          entry(2, [item("a", rule="cp", reason=CP_REASON, before={"target": "180–194 W"})])]
    m = PMK.of(PMK.marks([s("a", minutes=48, target="184–198 W")], es, TODAY), "a")
    assert m["state"] == "auto" and m["rule"] == "fatigue" and m["rules"] == ["fatigue", "cp"] and m["entry"] == 1
    assert m["cp"] == CP_REASON
    assert {d["key"]: (d["before"], d["after"]) for d in m["diff"]} == {
        "minutes": (60, 48), "target": ("180–194 W", "184–198 W")}


def test_tss_only_regeneration_is_no_marker_but_a_cp_rezone_is():
    es = [entry(1, [item("a", before={"tss": 30.0})]),                       # the generator's rate moved
          entry(2, [item("b", rule="cp", reason="CP 220 → 226 W：功率目標依 % CP 重算", before={})])]
    m = PMK.marks([s("a"), s("b", kind="quality")], es, TODAY)
    assert PMK.of(m, "a")["state"] == "none"
    b = PMK.of(m, "b")
    assert b["state"] == "auto" and b["rule"] == "cp" and b["diff"] == [] and "CP 220" in b["reason"]


def test_added_sessions_marked_only_when_a_rule_added_them():
    es = [entry(1, [item("a", action="added"), item("b", action="added", rule="missed_long",
                                                    reason="長跑延到週六：週四沒跑")])]
    m = PMK.marks([s("a"), s("b", kind="long")], es, TODAY)
    assert PMK.of(m, "a")["state"] == "none"
    b = PMK.of(m, "b")
    assert b["state"] == "auto" and b["added"] is True and b["rule"] == "missed_long"


def test_only_upcoming_active_sessions_carry_a_marker():
    es = [entry(1, [item(u, rule="fatigue", before={"minutes": 60}) for u in ("past", "done", "n")])]
    ss = [s("past", day="2026-09-29"), s("done", state="done"), s("n", kind="notice", origin="custom"),
          s("e", day="2026-09-29", edited=True)]
    m = PMK.marks(ss, es, TODAY)
    assert all(PMK.of(m, u)["state"] == "none" for u in ("past", "done", "n", "e"))


CP_REASON = "CP 220 → 226 W：功率目標依 % CP 重算"


def test_a_tss_only_replan_keeps_the_rule_that_made_the_change():
    # (A) fatigue cut 60 -> 48 min, then a re-plan that only moved the TSS: still 疲勞保護
    es = [entry(1, [item("a", rule="fatigue", reason="本週減量", before={"minutes": 60, "tss": 48.0})]),
          entry(2, [item("a", before={"tss": 38.4})])]
    m = PMK.of(PMK.marks([s("a", minutes=48)], es, TODAY), "a")
    assert m["state"] == "auto" and m["rule"] == "fatigue" and m["rules"] == ["fatigue"]
    assert m["reason"] == "本週減量" and m["entry"] == 1


def test_b_a_cp_mark_survives_a_later_tss_only_item():
    es = [entry(1, [item("a", rule="cp", reason=CP_REASON, before={})]),
          entry(2, [item("a", before={"tss": 30.0})])]
    m = PMK.of(PMK.marks([s("a", kind="quality")], es, TODAY), "a")
    assert m["state"] == "auto" and m["rule"] == "cp" and m["reason"] == CP_REASON


def test_c_fatigue_and_cp_in_one_run_keep_the_minutes_cut_on_fatigue():
    # _log_cp writes its row after the run's own row: the minutes cut is still the fatigue rule's
    es = [entry(1, [item("a", rule="fatigue", reason="本週減量", before={"minutes": 60})], at="2026-09-30T06:00:00Z"),
          entry(2, [item("a", rule="cp", reason=CP_REASON, before={})], at="2026-09-30T06:00:30Z")]
    m = PMK.of(PMK.marks([s("a", minutes=48)], es, TODAY), "a")
    assert m["rule"] == "fatigue" and m["reason"] == "本週減量" and m["entry"] == 1
    assert m["rules"] == ["fatigue", "cp"] and m["cp"] == CP_REASON
    assert m["diff"] == [{"key": "minutes", "before": 60, "after": 48}]


def test_same_run_cp_before_wins_so_before_shows_the_original_watts():
    # the run rescaled the stored plan first, so its own item's before already has the new watts
    es = [entry(1, [item("a", before={"target": "198–211 W"})], at="2026-09-30T06:00:00Z"),
          entry(2, [item("a", rule="cp", reason=CP_REASON, before={"target": "180–192 W"})], at="2026-09-30T06:00:20Z")]
    m = PMK.of(PMK.marks([s("a", kind="quality", target="200–212 W")], es, TODAY), "a")
    assert m["diff"] == [{"key": "target", "before": "180–192 W", "after": "200–212 W"}]
    assert "cp" in m["rules"]


def test_an_edited_session_rescaled_by_cp_says_so():
    # plan_auto.rescale_sessions rescales the watts of the user's own sessions too
    es = [entry(1, [item("a", rule="cp", reason=CP_REASON, before={"target": "180–192 W"})])]
    m = PMK.marks([s("a", edited=True, target="185–197 W"), s("b", edited=True)], es, TODAY)
    assert PMK.of(m, "a") == {"state": "user", "cp": CP_REASON}
    assert PMK.of(m, "b") == {"state": "user"}


def test_auto_marks_only_inside_the_push_window():
    es = [entry(1, [item(u, rule="fatigue", before={"minutes": 60}) for u in ("in", "out")])]
    ss = [s("in", day="2026-10-06"), s("out", day="2026-10-07"), s("mine", day="2026-10-20", edited=True)]
    m = PMK.marks(ss, es, TODAY, window_end="2026-10-06")
    assert PMK.of(m, "in")["state"] == "auto" and PMK.of(m, "out")["state"] == "none"
    assert PMK.of(m, "mine")["state"] == "user"          # ✎ is about who owns it: everywhere ahead
    assert PMK.window_end(TODAY, 7) == "2026-10-06"


def test_loader_reads_only_recent_applied_and_restore_rows():
    async def go():
        db = await make_db()
        now = dt.datetime(2026, 9, 30, 8, 0)

        def row(id, status, days_ago, uid):
            return PlanChangeLog(id=id, athlete_id=1, created_at=now - dt.timedelta(days=days_ago), trigger="sync",
                                 status=status, summary="x",
                                 items_json=f'[{{"action":"changed","uid":"{uid}","rule":"fatigue","before":{{"minutes":60}}}}]')
        db.add_all([row(1, "applied", 9, "old"), row(2, "applied", 1, "new"), row(3, "pending", 1, "held"),
                    row(4, "rejected", 1, "rej"), row(5, "undone", 1, "back"), row(6, "failed", 1, "fail"),
                    row(7, "superseded", 1, "sup")])
        rs = row(8, "restore", 1, "back")
        rs.after_json = '[{"uid": "back", "state": "active"}]'
        db.add(rs)
        await db.commit()
        us = ("old", "new", "held", "rej", "fail", "sup")
        ss = [s(u) for u in us] + [s("back", edited=True)]
        m = await PMK.load(db, ss, TODAY, now=now)
        assert {u: PMK.of(m, u)["state"] for u in us} == {
            "old": "none", "new": "auto", "held": "none", "rej": "none", "fail": "none", "sup": "none"}
        assert PMK.of(m, "back") == {"state": "user", "restored": True}
        # a broken row never breaks the page: no markers instead of an error
        bad = row(9, "applied", 1, "x")
        bad.items_json = "{not json"
        db.add(bad)
        await db.commit()
        assert PMK.of(await PMK.load(db, ss, TODAY, now=now), "new")["state"] == "auto"
    run(go())


# ---------------------------------------------------------------------------
# end to end: the automatic run -> the change log -> GET /calendar (課表 + 總覽) -> COROS
# ---------------------------------------------------------------------------

def _by_gen(ss, key):
    return next(x for x in ss if x.get("gen_key") == key and x["state"] == "active" and x["week_start"] == "2026-09-28")


async def _log(db):
    return (await db.execute(select(PlanChangeLog).order_by(PlanChangeLog.id))).scalars().all()


def _consistent(body, log):
    """Every auto marker points at an applied row of the change log that lists the session."""
    applied = {r.id: PA.entry_dict(r) for r in log if r.status == "applied"}
    for x in body["sessions"]:
        mk = x["mark"]
        if mk["state"] == "auto":
            e = applied[mk["entry"]]
            assert any(i.get("uid") == x["uid"] for i in e["items"])


def test_run_marks_the_changed_session_and_matches_log_and_push(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        body = await API.calendar(*CAL, db=db)
        # the first run only generated the plan: nothing adjusted, nothing the user's
        assert {x["mark"]["state"] for x in body["sessions"]} == {"none"}
        b.inp = base_inputs(new=1, easy_minutes=30)
        r = await PA.run(db)
        assert r["status"] == "applied"
        body = await API.calendar(*CAL, db=db)
        e1 = _by_gen(body["sessions"], "easy1")
        mk = e1["mark"]
        assert mk["state"] == "auto" and mk["rule"] == "plan" and mk["entry"] == r["id"]
        assert mk["diff"] == [{"key": "minutes", "before": 45, "after": 30}]
        others = [x for x in body["sessions"] if x["uid"] != e1["uid"]]
        assert {x["mark"]["state"] for x in others} == {"none"}
        _consistent(body, await _log(db))
        # COROS got the adjusted session, the same as the marker's after; the marker is never pushed
        sent = [x for c in b.push.calls for x in c["sessions"] if x["id"] == e1["uid"]]
        assert sent and sent[-1]["minutes"] == 30
        assert "mark" not in PS.push_dict(e1)
    run(go())


def test_undo_clears_the_auto_marker(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = base_inputs(new=1, easy_minutes=30)
        r = await PA.run(db)
        assert _by_gen((await API.calendar(*CAL, db=db))["sessions"], "easy1")["mark"]["state"] == "auto"
        await PA.undo(db, r["id"])
        body = await API.calendar(*CAL, db=db)
        e1 = _by_gen(body["sessions"], "easy1")
        assert e1["minutes"] == 45
        # no auto marker any more: 復原 pins it as the user's own (automation leaves it alone)
        assert e1["mark"] == {"state": "user", "restored": True}
        assert not [x for x in body["sessions"] if x["mark"]["state"] == "auto"]
        _consistent(body, await _log(db))
        # COROS has the restored session
        sent = [x for c in b.push.calls for x in c["sessions"] if x["id"] == e1["uid"]]
        assert sent[-1]["minutes"] == 45
    run(go())


def test_manual_edit_is_never_overwritten_by_plan_auto(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        e1 = _by_gen(await PS.load(db), "easy1")
        await PS.edit(db, e1["uid"], {"minutes": 50}, TODAY)
        # new data that would make the generator write 30 minutes
        b.inp = base_inputs(new=1, easy_minutes=30)
        r = await PA.run(db)
        mine = _by_gen(await PS.load(db), "easy1")
        assert mine["minutes"] == 50 and mine["edited"] is True
        if r.get("id"):
            items = PA.entry_dict(await db.get(PlanChangeLog, r["id"]))["items"]
            assert not [i for i in items if i.get("uid") == e1["uid"]]
        body = await API.calendar(*CAL, db=db)
        assert _by_gen(body["sessions"], "easy1")["mark"] == {"state": "user"}
        # whatever went to COROS for it is the user's version
        sent = [x for c in b.push.calls for x in c["sessions"] if x["id"] == e1["uid"]]
        assert all(x["minutes"] in (45, 50) for x in sent) and (not sent or sent[-1]["minutes"] == 50)
        # and again with yet another change: still the user's
        b.inp = base_inputs(new=2, easy_minutes=25)
        await PA.run(db)
        assert _by_gen(await PS.load(db), "easy1")["minutes"] == 50
    run(go())


def test_cp_change_end_to_end_marks_auto_and_user_sessions(monkeypatch):
    from backend.tests.test_plan_auto_cp import with_cp
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        q = _by_gen(await PS.load(db), "quality")
        await PS.edit(db, q["uid"], {"target": "功率 285–303 W"}, TODAY)      # the user's own
        b.inp = with_cp(base_inputs(), 330.0)
        r = await PA.run(db, trigger="cp_change")
        assert r["cp_change"]["n"] >= 1
        log = await _log(db)
        cp_row = PA.entry_dict(next(x for x in log if x.trigger == "cp_change"))
        body = await API.calendar(*CAL, db=db)
        mq = _by_gen(body["sessions"], "quality")["mark"]
        assert mq["state"] == "user" and mq["cp"].startswith("CP 300 → 330 W")
        # every other session the CP row lists (upcoming, in the push window, not the user's) reads ↻ CP
        others = {i["uid"] for i in cp_row["items"]} - {q["uid"]}
        seen = 0
        for x in body["sessions"]:
            if x["uid"] in others and x["day"] >= TODAY:
                assert x["mark"]["state"] == "auto" and "cp" in x["mark"]["rules"], x["title"]
                seen += 1
        assert seen
        _consistent(body, log)
    run(go())


def test_a_real_adapt_rule_through_the_run(monkeypatch):
    """rule D′ (self-rating Hard, engine/adapt.py rpe_hard) moves the quality Thu -> Fri."""
    from backend.tests import test_adapt_rpe as T
    monkeypatch.setattr(CW_mod(), "push_sessions", Push().push)
    monkeypatch.setattr(CW_mod(), "remove_keys", Push().remove)
    holder = {}

    def make(feel=None):
        import copy
        from backend.engine import coros_rpe as CR
        from backend.tests.test_plan_store import cur_plan, inputs, next_week
        ss = copy.deepcopy(T.wk()[0]["sessions"])
        ss[0]["title"] = "輕鬆跑"
        inp = inputs(today=TODAY, cur=cur_plan(today=TODAY, sessions=ss), weeks=[next_week()], acts=[T.EASY],
                     horizon="2026-10-11")
        inp["days_to_next_a"] = None
        rr = T.rated(feel) if feel else {}
        inp["adapt"] = {"enabled": True, "reviews": {}, "first_free": "2026-10-01", "rpe": rr}
        inp["rpe_stamp"] = CR.stamp(rr)
        holder["inp"] = inp
    make()
    monkeypatch.setattr(API, "_compute_inputs", lambda *a: holder["inp"])

    async def go():
        db = await make_db()
        await PA.run(db)
        make(feel=4)
        r = await PA.run(db)
        assert r["status"] == "applied"
        body = await API.calendar(*CAL, db=db)
        q = _by_gen(body["sessions"], "quality")
        m = q["mark"]
        assert m["state"] == "auto" and m["rule"] == "rpe_hard" and m["entry"] == r["id"]
        assert "自評 Hard" in m["reason"]
        assert {"key": "day", "before": "2026-10-01", "after": "2026-10-02"} in m["diff"]
        _consistent(body, await _log(db))
        await PA.undo(db, r["id"])
        q = _by_gen((await API.calendar(*CAL, db=db))["sessions"], "quality")
        assert q["day"] == "2026-10-01" and q["mark"] == {"state": "user", "restored": True}
    run(go())


def test_approved_proposal_marks_from_the_applied_row(monkeypatch):
    b = Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        await PA.run(db)
        b.inp = base_inputs(new=1, easy_minutes=200)                 # big: held for approval
        r = await PA.run(db)
        assert r["status"] == "pending"
        body = await API.calendar(*CAL, db=db)
        assert _by_gen(body["sessions"], "easy1")["mark"]["state"] == "none"     # nothing applied yet
        a = await PA.approve(db, r["id"])
        assert a["status"] == "applied"
        m = _by_gen((await API.calendar(*CAL, db=db))["sessions"], "easy1")["mark"]
        assert m["state"] == "auto" and m["entry"] == a["id"] and m["diff"][0]["after"] == 200
        assert (await db.get(PlanChangeLog, a["id"])).ref_id == r["id"]
    run(go())


def test_a_failing_marker_read_never_breaks_the_page(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("bad row")
    monkeypatch.setattr(PMK, "load", boom)

    async def go():
        db = await make_db()
        assert await PMK.load_safe(db, [s("a")], TODAY) == {}
    run(go())


def CW_mod():
    from backend.sync import coros_workouts as CW
    return CW


def test_schedule_and_overview_render_the_same_marker():
    """Both pages read the marker from GET /calendar and draw it with the shared plan_mark.js."""
    js = (STATIC / "plan_mark.js").read_text(encoding="utf-8")
    assert "window.PlanMark" in js
    for page in ("schedule.html", "overview.html"):
        html = (STATIC / page).read_text(encoding="utf-8")
        assert "/api/v1/static/plan_mark.js" in html and "PlanMark." in html, page
