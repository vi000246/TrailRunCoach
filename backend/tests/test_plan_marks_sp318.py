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


def test_chain_shows_first_before_and_current_after_with_the_latest_rule():
    es = [entry(1, [item("a", rule="fatigue", before={"minutes": 60})]),
          entry(2, [item("a", rule="cp", before={"target": "180–194 W"})])]
    m = PMK.of(PMK.marks([s("a", minutes=48, target="184–198 W")], es, TODAY), "a")
    assert m["state"] == "auto" and m["rule"] == "cp" and m["rules"] == ["fatigue", "cp"] and m["entry"] == 2
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


def test_loader_reads_only_recent_applied_and_restore_rows():
    async def go():
        db = await make_db()
        now = dt.datetime(2026, 9, 30, 8, 0)

        def row(id, status, days_ago, uid):
            return PlanChangeLog(id=id, athlete_id=1, created_at=now - dt.timedelta(days=days_ago), trigger="sync",
                                 status=status, summary="x",
                                 items_json=f'[{{"action":"changed","uid":"{uid}","rule":"fatigue","before":{{"minutes":60}}}}]')
        db.add_all([row(1, "applied", 9, "old"), row(2, "applied", 1, "new"), row(3, "pending", 1, "held"),
                    row(4, "rejected", 1, "rej")])
        await db.commit()
        ss = [s(u) for u in ("old", "new", "held", "rej")]
        m = await PMK.load(db, ss, TODAY, now=now)
        assert {u: PMK.of(m, u)["state"] for u in ("old", "new", "held", "rej")} == {
            "old": "none", "new": "auto", "held": "none", "rej": "none"}
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


def test_schedule_and_overview_render_the_same_marker():
    """Both pages read the marker from GET /calendar and draw it with the shared plan_mark.js."""
    js = (STATIC / "plan_mark.js").read_text(encoding="utf-8")
    assert "window.PlanMark" in js
    for page in ("schedule.html", "overview.html"):
        html = (STATIC / page).read_text(encoding="utf-8")
        assert "/api/v1/static/plan_mark.js" in html and "PlanMark." in html, page
