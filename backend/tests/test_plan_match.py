"""
Planned session <-> activity matching (engine/plan_match.py, reconcile rule 1),
planned vs actual (compare / compliance 「沒照課表」) and the manual link / unlink
API. Synthetic sessions and activities only (no dataset, no real DB).
"""
from datetime import date

import pytest

from backend.api import plan_sessions
from backend.engine import compliance as C
from backend.engine import plan_match as PM
from backend.engine import plan_store as PS
from backend.engine import reconcile as R
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, Env, g, run

WS = "2026-09-28"


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 2))


def act(index, day, minutes, cat="road", hard_s=0.0, tss=None, hour=20):
    return {"index": index, "start": f"{day}T{hour:02d}:00:00", "date": day, "category": cat,
            "category_label": {"road": "路跑", "trail": "越野跑", "bike": "騎車", "strength": "肌力"}.get(cat, cat),
            "moving_s": minutes * 60.0, "tss": tss if tss is not None else minutes * 0.9, "hard_s": hard_s}


def sess(uid, day, kind="easy", minutes=45, title=None, state="active", gen_key=None, done_by=None, **kw):
    return {"uid": uid, "week_start": R.monday_of(day), "gen_key": gen_key, "day": day, "kind": kind,
            "title": title or PS.KINDS.get(kind, kind), "minutes": minutes, "target": "", "detail": "",
            "source": "", "tss": minutes * 0.8, "origin": "auto" if gen_key else "custom",
            "edited": not gen_key, "provisional": False, "state": state, "done_by": done_by, "note": None, **kw}


def by_uid(ss):
    return {s["uid"]: s for s in ss}


# ---------------------------------------------------------------------------
# the 10/1 case: the planned easy run gets the run of its own day
# ---------------------------------------------------------------------------

def test_planned_run_takes_its_own_days_run_not_the_generators_week_order():
    a28, a30, a01 = act(800, "2026-09-28", 43), act(801, "2026-09-30", 48, hard_s=700), act(802, "2026-10-01", 48)
    stored = [sess("t", "2026-09-30", "test", 50, "CP 測試 3 分 + 12 分", state="done", gen_key="test",
                   done_by={**a30, "match": "day"}),
              sess("e1", "2026-10-01", gen_key="easy1"),            # pushed to the watch for 10/1
              sess("e2", "2026-10-03", gen_key="easy2")]
    # week_plan takes the week's runs in its own order: easy1 = the first run (9/28)
    gen = [{"start": WS, "mode": "base", "provisional": False, "sessions": [
        g("easy1", "easy", "輕鬆跑", 45, "2026-09-28", done=True, done_by=a28),
        g("test", "test", "CP 測試 3 分 + 12 分", 50, "2026-09-30", done=True, done_by=a30),
        g("easy2", "easy", "輕鬆跑", 45, "2026-10-01", done=True, done_by=a01),
        g("easy3", "easy", "輕鬆跑", 45, "2026-10-03")]}]
    new, ch = R.reconcile(stored, gen, [a28, a30, a01], "2026-10-02", "2026-10-04")
    s = by_uid(new)
    assert s["e1"]["state"] == "done" and s["e1"]["day"] == "2026-10-01" and s["e1"]["done_by"]["index"] == 802
    assert s["e1"]["done_by"]["match"] == "day"
    # the open easy keeps its day (the generator's easy3 lines up with it), nothing is added
    live = [x for x in new if x["state"] in ("active", "done", "missed")]
    assert s["e2"]["state"] == "active" and s["e2"]["day"] == "2026-10-03"
    assert len(live) == 3
    # 9/28 had nothing planned: the run stays a separate, unplanned item
    held = [x["done_by"]["index"] for x in live if x["state"] == "done"]
    assert 800 not in held and len(held) == len(set(held))


def test_one_activity_never_counts_twice_old_rows_are_repaired():
    a30 = act(801, "2026-09-30", 48, hard_s=700)
    stored = [sess("t", "2026-09-30", "test", 50, "CP 測試 3 分 + 12 分", state="done", gen_key="test",
                   done_by=dict(a30)),
              sess("e", "2026-09-30", state="done", gen_key="easy2", done_by=dict(a30))]
    out = [dict(x) for x in stored]
    ch = PM.assign(out, [a30], "2026-10-02", {}, covered="2026-10-01")
    s = by_uid(out)
    assert s["t"]["state"] == "done" and s["e"]["state"] == "missed"         # the test keeps the run
    assert any(c["action"] == "unmatched" and c["uid"] == "e" for c in ch)


def test_two_runs_a_day_each_take_the_closest_session():
    hard, easy = act(1, "2026-10-01", 65, hard_s=900, hour=6), act(2, "2026-10-01", 40, hour=19)
    out = [sess("q", "2026-10-01", "quality", 60, "閾值 3×10 分"), sess("e", "2026-10-01", "easy", 40)]
    PM.assign(out, [hard, easy], "2026-10-02", {})
    s = by_uid(out)
    assert s["q"]["done_by"]["index"] == 1 and s["e"]["done_by"]["index"] == 2


def test_unplanned_run_stays_separate_and_does_not_pull_a_later_easy():
    a = act(5, "2026-09-29", 40)
    out = [sess("e", "2026-10-03", gen_key="easy1")]
    gen_done = {(WS, "easy1"): g("easy1", "easy", "輕鬆跑", 45, "2026-09-29", done=True, done_by=a)}
    PM.assign(out, [a], "2026-10-02", gen_done)
    assert out[0]["state"] == "active" and out[0]["day"] == "2026-10-03"


def test_long_done_a_day_early_follows_the_generator():
    a = act(6, "2026-10-03", 120)
    out = [sess("l", "2026-10-04", "long", 120, gen_key="long")]
    gen_done = {(WS, "long"): g("long", "long", "LSD", 120, "2026-10-03", done=True, done_by=a)}
    PM.assign(out, [a], "2026-10-03", gen_done)
    assert out[0]["state"] == "done" and out[0]["day"] == "2026-10-03" and out[0]["done_by"]["match"] == "plan"


def test_strength_and_bath_rules():
    out = [sess("st", "2026-10-01", "strength", 35), sess("hp", "2026-10-01", "heat_passive", 20),
           sess("e", "2026-10-01")]
    PM.assign(out, [act(7, "2026-10-01", 30, cat="strength")], "2026-10-02", {}, covered="2026-10-01")
    s = by_uid(out)
    assert s["st"]["state"] == "done" and s["e"]["state"] == "missed" and s["hp"]["state"] == "active"


def test_unlinked_activity_is_not_auto_matched():
    out = [sess("e", "2026-10-01")]
    PM.assign(out, [act(8, "2026-10-01", 45)], "2026-10-02", {}, unlinked={8})
    assert out[0]["state"] == "missed" and out[0]["done_by"] is None


# ---------------------------------------------------------------------------
# planned vs actual
# ---------------------------------------------------------------------------

def test_intervals_planned_but_ran_easy_is_linked_and_marked_off_plan():
    out = [sess("q", "2026-10-01", "quality", 60, "閾值 3×10 分")]
    PM.assign(out, [act(9, "2026-10-01", 58, hard_s=60)], "2026-10-02", {})
    q = out[0]
    assert q["state"] == "done" and q["done_by"]["index"] == 9                # still linked
    vs = PM.compare(q)
    assert vs["off_plan"] and vs["planned"] == "hard" and vs["actual"] == "easy"
    assert vs["text"] == "沒照課表：排強度課，實際跑輕鬆" and vs["need_min"] == 10.0
    comp = C.with_plan_check(C.session_compliance(q, 48.0), vs)
    assert comp["label"] == "沒照課表" and comp["level"] in ("yellow", "red") and comp["off_plan"]


def test_easy_as_planned_is_on_plan_and_compliance_unchanged():
    out = [sess("e", "2026-10-01", "easy", 45)]
    PM.assign(out, [act(10, "2026-10-01", 46, tss=36)], "2026-10-02", {})
    vs = PM.compare(out[0])
    assert not vs["off_plan"] and vs["text"] == ""
    comp = C.session_compliance(out[0], 36.0)
    assert C.with_plan_check(comp, vs) == comp and comp["level"] == "green"


def test_easy_planned_but_ran_hard_and_other_sport():
    e = sess("e", "2026-10-01", state="done", done_by=act(11, "2026-10-01", 45, hard_s=1200))
    assert PM.compare(e)["text"] == "沒照課表：排輕鬆跑，實際跑強度"
    b = sess("b", "2026-10-01", state="done", done_by=act(12, "2026-10-01", 60, cat="bike"))
    assert PM.compare(b)["wrong_sport"] and PM.compare(b)["text"] == "沒照課表：排輕鬆跑，實際騎車"
    # no intensity data (no hard_s): no verdict
    n = sess("n", "2026-10-01", "quality", 60, state="done", done_by={**act(13, "2026-10-01", 60), "hard_s": None})
    assert not PM.compare(n)["off_plan"]


# ---------------------------------------------------------------------------
# API: a synced run shows on its session at once; manual link / unlink
# ---------------------------------------------------------------------------

def _week(e, sessions, monkeypatch):
    """The week generated on 9/30 (sessions stored), then it's 10/2."""
    e.inp["cur"]["sessions"] = sessions
    e.inp["activities"] = []
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))
    e.c.get(f"{API}/sessions")
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 2))
    e.inp["cur"]["week"]["today"] = "2026-10-02"
    e.inp["today"] = "2026-10-02"


def test_api_sessions_marks_a_synced_run_done_and_link_unlink(monkeypatch):
    with Env(monkeypatch) as e:
        _week(e, [g("easy1", "easy", "輕鬆跑", 45, "2026-10-01"), g("long", "long", "LSD", 120, "2026-10-04")],
              monkeypatch)
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        easy = next(s for s in ss if s.get("gen_key") == "easy1" and s["day"] == "2026-10-01")
        assert easy["state"] == "active"
        # the run syncs; the generator (cached) hasn't seen it: the page still shows it on the session
        a = act(21, "2026-10-01", 47)
        e.inp["activities"] = [a]
        ss = by_uid(e.c.get(f"{API}/sessions").json()["sessions"])
        assert ss[easy["uid"]]["state"] == "done" and ss[easy["uid"]]["done_by"]["index"] == 21
        # done = not pushed again (plan_auto / push only send active sessions)
        assert not plan_sessions._in_range(list(ss.values()), "2026-10-01", "2026-10-01")
        # unlink: back to open, and the run is never auto-matched again
        r = e.c.delete(f"{API}/sessions/{easy['uid']}/link")
        assert r.status_code == 200 and r.json()["state"] == "missed"
        ss = by_uid(e.c.get(f"{API}/sessions").json()["sessions"])
        assert ss[easy["uid"]]["state"] == "missed"
        e.c.post(f"{API}/reconcile")
        assert by_uid(e.c.get(f"{API}/sessions").json()["sessions"])[easy["uid"]]["state"] == "missed"
        # manual link puts it back (match = manual) and forgets the unlink
        r = e.c.post(f"{API}/sessions/{easy['uid']}/link", json={"index": 21})
        assert r.status_code == 200 and r.json()["done_by"]["match"] == "manual"
        assert e.c.post(f"{API}/sessions/{easy['uid']}/link", json={"index": 99}).status_code == 404
        long = next(s for s in ss.values() if s.get("gen_key") == "long")
        r = e.c.post(f"{API}/sessions/{long['uid']}/link", json={"index": 21})
        assert r.status_code == 400 and "已經配給" in r.json()["detail"]


def test_api_calendar_has_planned_vs_actual_and_link_options(monkeypatch):
    a = act(31, "2026-10-01", 58, hard_s=30)
    extra = act(32, "2026-10-02", 30, hour=7)

    def fake(start, end):
        return {"activities": [x for x in (a, extra) if start <= x["date"] <= end], "phases": []}
    monkeypatch.setattr(plan_sessions, "_range_extras", fake)
    with Env(monkeypatch) as e:
        _week(e, [g("quality", "quality", "閾值 3×10 分", 60, "2026-10-01"),
                  g("easy1", "easy", "輕鬆跑", 45, "2026-10-03")], monkeypatch)
        e.inp["activities"] = [a, extra]
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()
        q = next(s for s in b["sessions"] if s["kind"] == "quality" and s["day"] == "2026-10-01")
        assert q["state"] == "done" and q["vs"]["off_plan"] and q["compliance"]["label"] == "沒照課表"
        assert q["link_options"] == [32]                                     # the 10/2 run, ± 1 day
