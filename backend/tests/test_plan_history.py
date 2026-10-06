"""
每週課表存檔 (engine/plan_history.py, SP-71): the plan snapshot of a week is frozen when the week
is first seen and survives later edits / regenerations; the result (planned vs done) is stored
once the week is over, refreshed while it can still change and frozen FINAL_DAYS later; a week
first seen after it ended is stored and marked late; a sync alone records too; a failure never
breaks the page. Synthetic data only: hand-built inputs (test_plan_store.Env), in-memory DB.
"""
import json
from datetime import date

import pytest
from sqlalchemy import select

from backend.api import plan_sessions as PSAPI
from backend.db.models import PlanWeekSnapshot
from backend.engine import compliance as C
from backend.engine import plan_auto as PA
from backend.engine import plan_history as PH
from backend.sync import coros_workouts as CW
from backend.tests.test_coros_workouts import make_db, run
from backend.tests.test_plan_auto import Box, Push, base_inputs
from backend.tests.test_plan_store import ACT_929, API, Env, cur_plan, g, inputs


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def _s(uid, day, kind, state="active", minutes=60, tss=50.0, **kw):
    return {"uid": uid, "day": day, "week_start": PH.monday_of(day), "kind": kind, "title": kw.pop("title", kind),
            "minutes": minutes, "tss": tss, "state": state, "origin": "auto", "edited": False, **kw}


# ---------------------------------------------------------------------------
# pure
# ---------------------------------------------------------------------------

def test_plan_summary_is_a_compact_record_of_the_week():
    long_text = "很長的說明" * 200
    ss = [
        _s("a", "2026-09-28", "easy", minutes=45, tss=36.0, target=long_text, detail=long_text, steps={"items": [1] * 50}),
        _s("b", "2026-09-30", "quality", minutes=60, tss=70.0, title="有氧間歇（巡航）3×10 分", rung_key="z3_2"),
        _s("c", "2026-10-02", "strength", minutes=35, tss=10.0),
        _s("d", "2026-10-04", "long", minutes=120, tss=96.0, origin="custom", title="自己排的長跑" * 20),
        _s("e", "2026-09-29", "easy", state="done", minutes=40, tss=30.0, edited=True),
        _s("x", "2026-10-01", "notice", minutes=1, tss=0.0),                 # the 課表待確認 reminder
        _s("y", "2026-10-03", "easy", state="deleted"),                      # a tombstone
        _s("z", "2026-10-05", "easy"),                                       # next week
    ]
    p = PH.plan_summary(ss, "2026-09-28", "2026-09-28", ctx={"phase": "base", "thr": {"cp": 250.0}, "ctl": None},
                        family=lambda s: "aerobic")
    assert [e["u"] for e in p["s"]] == ["a", "e", "b", "c", "d"]             # by day; no notice / tombstone / next week
    assert p["n"] == 5 and p["late"] is False and p["taken"] == "2026-09-28"
    assert p["hours"] == round((45 + 40 + 60 + 120) / 60, 2)                 # strength time left out, like week_plan()
    assert p["tss"] == 242.0
    assert p["phase"] == "base" and p["thr"] == {"cp": 250.0} and "ctl" not in p     # None is not stored
    by = {e["u"]: e for e in p["s"]}
    assert by["a"] == {"u": "a", "d": 0, "k": "easy", "m": 45, "t": 36.0, "n": "easy"}
    assert by["b"]["r"] == "z3_2" and by["b"]["f"] == "aerobic" and by["b"]["d"] == 2
    assert "f" not in by["a"]                                                 # a family only on a 強度課
    assert by["d"]["o"] == "c" and len(by["d"]["n"]) == PH.TITLE_MAX
    assert by["e"]["o"] == "e" and by["e"]["st"] == "done"
    # a summary: none of the session's long texts, about 100 bytes a session
    raw = PH._dumps(p)
    assert "很長的說明" not in raw and "items" not in raw and len(raw.encode()) < 900
    assert PH.plan_summary(ss, "2026-09-28", "2026-10-06")["late"] is True   # first seen after the week ended


def test_drift_counts_what_moved_since_the_snapshot():
    ss = [_s("a", "2026-09-28", "easy", minutes=45), _s("b", "2026-09-30", "quality"),
          _s("c", "2026-10-02", "easy"), _s("d", "2026-10-04", "long", minutes=120)]
    p = PH.plan_summary(ss, "2026-09-28", "2026-09-28")
    end = [_s("a", "2026-09-28", "easy", state="done", minutes=45),          # as planned
           _s("b", "2026-10-01", "quality", state="missed"),                 # another day
           _s("c", "2026-10-02", "easy", state="superseded"),                # replaced
           _s("d", "2026-10-04", "long", minutes=90),                        # shortened
           _s("n", "2026-10-03", "easy")]                                    # placed again by the generator
    assert PH.drift(p, "2026-09-28", end) == {"changed": 2, "removed": 1, "added": 1}
    assert PH.drift(p, "2026-09-28", ss) == {"changed": 0, "removed": 0, "added": 0}
    # a session moved to another week keeps its uid: changed, not removed
    assert PH.drift(p, "2026-09-28", ss[:3] + [_s("d", "2026-10-06", "long", minutes=120)])["changed"] == 1


def test_result_summary_reads_the_dashboard_of_that_week():
    def row(uid, day, kind, state, comp=None, done_by=None, tss=50.0):
        return {**_s(uid, day, kind, state, tss=tss), "tss_est": tss, "compliance": comp, "done_by": done_by}
    green = {"level": "green", "pct": 100, "duration_pct": 100, "tss_pct": 100, "wrong_type": False}
    yellow = {**green, "level": "yellow", "pct": 70}
    ss = [row("a", "2026-09-28", "easy", "done", green, {"index": 1, "moving_s": 3600, "tss": 50}),
          row("b", "2026-09-30", "quality", "done", yellow, {"index": 2, "moving_s": 2500, "tss": 35}),
          row("c", "2026-10-02", "quality", "missed"),
          row("d", "2026-10-04", "long", "missed", tss=100.0)]
    weeks = [{"start": "2026-09-28", "end": "2026-10-04", "planned_tss": 250, "done_tss": 120.0, "planned_hours": 4,
              "done_hours": 2.5, "compliance": None}]
    d = C.dashboard(ss, weeks, "2026-10-06", "2026-09-28", "2026-10-04")
    p = PH.plan_summary(ss, "2026-09-28", "2026-09-28")
    r = PH.result_summary(d, p, "2026-09-28", ss, "2026-10-06")
    assert (r["due"], r["completed"], r["ok"], r["partial"], r["missed"], r["open"]) == (4, 2, 1, 1, 2, 0)
    assert r["rate"] == 0.5 and r["ok_rate"] == 0.25
    assert r["planned_tss"] == 250.0 and r["actual_tss"] == 85.0 and r["tss_pct"] == 34
    assert r["done_tss"] == 120.0 and r["done_hours"] == 2.5                 # every activity, planned or not
    assert r["quality"] == {"due": 2, "completed": 1, "ok": 0}               # 強度課 alone
    assert r["drift"] == {"changed": 0, "removed": 0, "added": 0} and r["at"] == "2026-10-06"
    # the week's activities were not at hand: no made-up totals
    r2 = PH.result_summary(C.dashboard(ss, [], "2026-10-06", "2026-09-28", "2026-10-04"), p, "2026-09-28", ss, "2026-10-06")
    assert r2["done_tss"] is None and r2["done_hours"] is None and r2["due"] == 4


def test_final_after_two_weeks():
    assert not PH.is_final("2026-09-28", "2026-10-17")
    assert PH.is_final("2026-09-28", "2026-10-18")                           # 10/4 + 14 days
    assert PH.week_end("2026-09-28") == "2026-10-04" and PH.monday_of("2026-10-04") == "2026-09-28"


def test_week_ctx_only_for_the_running_week():
    inp = inputs()
    inp["cur"]["load"]["ctl_week_start"] = 31.26
    c = PH.week_ctx(inp, "2026-09-28")
    assert c["phase"] == "base" and c["mode"] == "base" and c["thr"] == {"cp": 300.0, "lthr": 170.0, "aet": 150.0}
    assert c["target"] == {"hours": 5.0, "tss": 250.0} and c["ctl"] == 31.3
    assert PH.week_ctx(inp, "2026-09-21") == {}                              # a past week: nothing is guessed


# ---------------------------------------------------------------------------
# stored: the week through the API
# ---------------------------------------------------------------------------

def _rows(e):
    return {r.week_start: r for r in run(e.db.execute(select(PlanWeekSnapshot))).scalars().all()}


def _week(start, today, sessions):
    cur = cur_plan(today=today, sessions=sessions)
    end = PH.week_end(start)
    cur["week"] = {"start": start, "end": end, "today": today, "days_left": (date.fromisoformat(end) - date.fromisoformat(today)).days + 1}
    return cur


ACT_LONG = {"index": 20, "date": "2026-10-04", "category": "trail", "category_label": "越野", "moving_s": 5400,
            "tss": 80, "start": "2026-10-04T07:00:00"}


def test_snapshot_is_frozen_and_the_result_follows_the_week(monkeypatch):
    with Env(monkeypatch) as e:
        e.inp["activities_since"] = "2026-08-31"
        assert e.c.get(f"{API}/history").json()["weeks"] == []
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        h = e.c.get(f"{API}/history").json()
        assert [w["week_start"] for w in h["weeks"]] == ["2026-09-28"]       # the running week only, not the ones ahead
        w = h["weeks"][0]
        p = w["plan"]
        assert w["week_end"] == "2026-10-04" and w["result"] is None and w["final"] is False
        assert (p["taken"], p["late"], p["n"], p["phase"]) == ("2026-09-30", False, 5, "base")
        assert p["thr"] == {"cp": 300.0, "lthr": 170.0, "aet": 150.0}
        assert sorted((x["d"], x["k"], x["m"]) for x in p["s"]) == [
            (1, "easy", 40), (3, "quality", 60), (4, "easy", 45), (4, "strength", 35), (6, "long", 120)]
        assert next(x for x in p["s"] if x["d"] == 1)["st"] == "done"         # taken on Wednesday: Tuesday was done
        assert p["hours"] == round((40 + 60 + 45 + 120) / 60, 2)
        frozen = _rows(e)["2026-09-28"].plan_json
        assert len(frozen.encode()) < 1200                                    # about 1 kB a week
        stamp = _rows(e)["2026-09-28"].updated_at

        # the user shortens the long run mid-week; the page is loaded again: the snapshot stays
        long = next(s for s in ss if s["day"] == "2026-10-04")
        assert e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 90}).status_code == 200
        e.c.get(f"{API}/sessions")
        e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-11")
        r = _rows(e)["2026-09-28"]
        assert r.plan_json == frozen and r.updated_at == stamp and r.result_json is None

        # Tuesday of the next week: the long run was done, the rest was not
        monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 6))
        e.inp = inputs(today="2026-10-06", acts=[ACT_929, ACT_LONG], horizon="2026-10-18", cur=_week(
            "2026-10-05", "2026-10-06", [g("quality", "quality", "閾值 3×10 分", 60, "2026-10-06"),
                                         g("easy1", "easy", "輕鬆跑", 50, "2026-10-07"),
                                         g("long", "long", "LSD（山路）", 130, "2026-10-11")]))
        e.inp["activities_since"] = "2026-09-07"
        e.c.get(f"{API}/sessions")
        rows = _rows(e)
        assert set(rows) == {"2026-09-28", "2026-10-05"}
        assert rows["2026-09-28"].plan_json == frozen                         # still what the week began with
        res = json.loads(rows["2026-09-28"].result_json)
        assert (res["due"], res["completed"], res["missed"], res["open"]) == (5, 2, 3, 0)
        assert res["rate"] == 0.4 and res["quality"] == {"due": 1, "completed": 0, "ok": 0}
        assert res["done_tss"] == 110.0 and res["done_hours"] == round((2400 + 5400) / 3600, 2)
        assert res["drift"] == {"changed": 1, "removed": 0, "added": 0}       # the long run: 120 → 90
        assert res["at"] == "2026-10-06" and rows["2026-09-28"].final is False
        assert json.loads(rows["2026-10-05"].plan_json)["taken"] == "2026-10-06" and rows["2026-10-05"].result_json is None
        # loading again changes nothing (no rewrite on every page load)
        stamp = rows["2026-09-28"].updated_at
        e.c.get(f"{API}/sessions")
        assert _rows(e)["2026-09-28"].updated_at == stamp

        # a late pairing still moves the result: the missed quality is deleted as expired
        q = next(s for s in e.c.get(f"{API}/sessions").json()["sessions"] if s["day"] == "2026-10-01")
        assert e.c.delete(f"{API}/sessions/{q['uid']}").status_code == 200
        e.c.get(f"{API}/sessions")
        res = json.loads(_rows(e)["2026-09-28"].result_json)
        assert (res["due"], res["missed"]) == (4, 2) and res["drift"]["removed"] == 1

        # two weeks after the week ended it is final: later changes no longer touch it
        monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 19))
        e.inp = inputs(today="2026-10-19", acts=[], horizon="2026-11-01", cur=_week(
            "2026-10-19", "2026-10-19", [g("easy1", "easy", "輕鬆跑", 50, "2026-10-21")]))
        e.inp["activities_since"] = "2026-09-21"
        e.c.get(f"{API}/sessions")
        rows = _rows(e)
        assert rows["2026-09-28"].final is True and json.loads(rows["2026-09-28"].result_json)["due"] == 4
        assert e.c.post(f"{API}/sessions/expired/delete").status_code == 200  # every expired session goes
        e.c.get(f"{API}/sessions")
        rows = _rows(e)
        assert json.loads(rows["2026-09-28"].result_json)["due"] == 4         # frozen
        # the week of 10/12 was never opened: stored late, from what the plan still holds
        late = json.loads(rows["2026-10-12"].plan_json)
        assert late["late"] is True and late["taken"] == "2026-10-19" and "phase" not in late
        assert rows["2026-10-12"].result_json is not None and rows["2026-10-12"].final is False
        # the activity window starts 9/21 now: a week before it has no made-up totals, one inside has
        assert json.loads(rows["2026-10-12"].result_json)["done_tss"] == 0.0
        h = e.c.get(f"{API}/history?limit=2").json()
        assert [w["week_start"] for w in h["weeks"]] == ["2026-10-19", "2026-10-12"]
        assert h["final_days"] == PH.FINAL_DAYS and h["marked_races"] == 0


def test_a_week_outside_the_activity_window_has_no_totals(monkeypatch):
    with Env(monkeypatch) as e:
        e.c.get(f"{API}/sessions")
        monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 6))
        e.inp = inputs(today="2026-10-06", acts=[ACT_929], horizon="2026-10-18", cur=_week(
            "2026-10-05", "2026-10-06", [g("easy1", "easy", "輕鬆跑", 50, "2026-10-07")]))
        e.c.get(f"{API}/sessions")                                            # no activities_since in the inputs
        res = json.loads(_rows(e)["2026-09-28"].result_json)
        assert res["done_tss"] is None and res["done_hours"] is None and res["due"] == 5


def test_an_automatic_run_records_the_week_without_a_page(monkeypatch):
    Box(monkeypatch, base_inputs(), Push())

    async def go():
        db = await make_db()
        assert (await PA.run(db, trigger="sync:coros"))["status"] == "applied"
        rows = (await db.execute(select(PlanWeekSnapshot))).scalars().all()
        assert [r.week_start for r in rows] == ["2026-09-28"]
        assert json.loads(rows[0].plan_json)["n"] == 4
    run(go())


def test_a_failing_record_never_breaks_the_page(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(PH, "record", boom)
    with Env(monkeypatch) as e:
        r = e.c.get(f"{API}/sessions")
        assert r.status_code == 200 and r.json()["sessions"]
        assert _rows(e) == {}


def test_history_counts_the_marked_races(monkeypatch, tmp_path):
    from backend.engine import activity_tags as AT
    db = tmp_path / "tags.db"
    AT.upsert(db, start_local="2026-03-08T06:30", activity_type="race", label="某場越野賽")
    AT.upsert(db, start_local="2026-04-12T07:00", activity_type="training")
    AT.upsert(db, start_local="2026-05-01T07:00", effort="max")
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    AT._memo.clear()
    with Env(monkeypatch) as e:
        assert e.c.get(f"{API}/history").json()["marked_races"] == 1
    assert PSAPI.router is not None
