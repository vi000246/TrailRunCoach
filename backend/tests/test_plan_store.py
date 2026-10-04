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
            g("long", "long", "LSD（山路）", 120, "2026-10-04"),
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
                g("long", "long", "LSD（山路）", 130, "2026-10-11")]}


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
    # 減量期: one interval by the two-track pick (SP-31) — Zone 5 not open here, so Zone 3's 節奏 2×8′
    assert [s["title"] for s in taper["sessions"] if s["kind"] == "quality"] == ["節奏 2×8 分"]
    # 專項期 without a confirmed aerobic base: the Zone 3 ladder, not the 5×4′ hill set (台灣教練)
    assert weeks[6]["phase"] == "specific" and (weeks[6]["mode"] == "recovery_week" or any(
        s["kind"] == "quality" and s["title"].startswith("閾值") for s in weeks[6]["sessions"]))


def test_projection_sessions_placed_like_week_plan():
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 10, 25))
    assert weeks[0]["mode"] == "recovery_week"                      # history already built 3 weeks
    assert not [s for s in weeks[0]["sessions"] if s["kind"] == "long"]
    # base recovery week: the short Palladino fartlek instead of intervals (engine/quality_gate.py)
    assert [s["title"] for s in weeks[0]["sessions"] if s["kind"] == "quality"] == ["恢復週 fartlek 4×1 分"]
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
                             g("long", "long", "LSD（山路）", 100, "2026-10-04")])
    new2, ch = rec(new, inputs(cur=cur))
    b = active(new2)
    assert b[("2026-09-28", "easy1")]["minutes"] == 30 and b[("2026-09-28", "easy1")]["day"] == "2026-10-02"
    assert b[("2026-09-28", "long")]["minutes"] == 100
    changed = [c for c in ch if c["action"] == "changed"]
    assert [c["title"] for c in changed] == ["LSD（山路）"] and changed[0]["before"] == {"minutes": 120}
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
        g("long", "long", "LSD（山路）", 120, "2026-10-04")])
    new2, ch = rec(new, inputs(today="2026-10-02", cur=cur))
    missed = [s for s in new2 if s["state"] == "missed"]
    assert [s["gen_key"] for s in missed] == ["quality"] and missed[0]["day"] == "2026-10-01"
    assert active(new2)[("2026-09-28", "quality")]["day"] == "2026-10-03"      # recalculated, new row
    assert active(new2)[("2026-09-28", "quality")]["uid"] != missed[0]["uid"]
    acts = {(c["action"], c["day"]) for c in ch}
    assert ("missed", "2026-10-01") in acts and ("added", "2026-10-03") in acts


def _two_days_later_quality_moved():
    return cur_plan(today="2026-10-02", sessions=[
        g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
        g("quality", "quality", "閾值 3×10 分", 60, "2026-10-03"),
        g("strength1", "strength", "肌力", 35, "2026-10-02"),
        g("easy1", "easy", "輕鬆跑", 45, "2026-10-02"),
        g("long", "long", "LSD（山路）", 120, "2026-10-04")])


def test_late_sync_turns_missed_into_done():
    new, _ = rec([], inputs())
    new2, _ = rec(new, inputs(today="2026-10-02", cur=_two_days_later_quality_moved()))
    assert [s["gen_key"] for s in new2 if s["state"] == "missed"] == ["quality"]
    # the 10/1 workout syncs a day late: the generator now reports quality done
    act = {"index": 13, "date": "2026-10-01", "category": "road", "category_label": "路跑"}
    cur = cur_plan(today="2026-10-02", sessions=[
        g("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True, done_by=ACT_929),
        g("quality", "quality", "閾值 3×10 分", 60, "2026-10-01", done=True, done_by=act),
        g("strength1", "strength", "肌力", 35, "2026-10-02"),
        g("easy1", "easy", "輕鬆跑", 45, "2026-10-02"),
        g("long", "long", "LSD（山路）", 120, "2026-10-04")])
    new3, ch = rec(new2, inputs(today="2026-10-02", cur=cur, acts=[ACT_929, act]))
    q = [s for s in new3 if s.get("gen_key") == "quality" and s["state"] in ("active", "done", "missed")]
    assert [(s["state"], s["day"]) for s in q] == [("done", "2026-10-01")]     # no leftover re-placed row
    assert not [c for c in ch if c["action"] == "added"]
    assert {c["action"] for c in ch} == {"done", "removed"}


def test_not_missed_before_the_data_covers_the_day():
    new, _ = rec([], inputs())
    inp = inputs(today="2026-10-02", cur=_two_days_later_quality_moved())
    new2, ch = R.reconcile(new, PS.gen_weeks(inp), inp["activities"], inp["today"], inp["horizon_end"],
                           covered="2026-09-30")                          # last sync covers up to 9/30
    q1 = next(s for s in new2 if s.get("gen_key") == "quality" and s["day"] == "2026-10-01")
    assert q1["state"] == "active" and not [c for c in ch if c["action"] == "missed"]
    # a past, still-open session is never pushed (and so never re-scheduled)
    with pytest.raises(CW.Unsupported, match="已過"):
        CW.session_workout(PS.push_dict(q1), TH, "2026-10-02")


def test_projection_from_a_recovery_week_still_has_quality():
    cur = cur_plan(mode="recovery_week", hours=3.0, sessions=[g("easy1", "easy", "輕鬆跑", 45, "2026-10-01")])
    cur["history"] = [{"start": "x", "hours": h, "tss": 0} for h in (4.0, 3.0, 4.0, 3.0, 4.0, 3.0, 4.0, 3.0)]
    weeks = P.project_weeks(cur, PHASES, date(2026, 10, 11))
    assert weeks[0]["mode"] == "base" and any(s["kind"] == "quality" for s in weeks[0]["sessions"])


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
            g("long", "long", "LSD（山路）", 140, "2026-10-18")]}], horizon="2026-10-18")
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


def test_api_swap_a_variant_is_a_user_edit_and_pushes_its_steps(monkeypatch):
    # interval-prescription.md §C5.4: the 換一個 drawer / editor templates; a swap is the user's
    # edit (reconcile rule 3: auto-replan never overrides it) and COROS gets the variant's steps
    with Env(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        q = next(s for s in ss if s["kind"] == "quality")
        tpl = e.c.get(f"{API}/variants", params={"day": q["day"]}).json()
        assert any(r["key"] == "v4a" for g in tpl["templates"]["groups"] for r in g["rows"])
        r = e.c.patch(f"{API}/sessions/{q['uid']}", json={"variant_key": "v3c"})
        assert r.status_code == 200
        s = r.json()
        assert s["edited"] and s["variant_key"] == "v3c" and s["swap"] == "user" and s["title"] == "VO2max 6×2:30"
        d = e.c.get(f"{API}/variants", params={"uid": q["uid"]}).json()
        assert d["drawer"]["rung"] == "z5c" and d["drawer"]["current_key"] == "v3c"
        e.c.post(f"{API}/reconcile")
        again = next(x for x in e.c.get(f"{API}/sessions").json()["sessions"] if x["uid"] == q["uid"])
        assert again["variant_key"] == "v3c" and again["title"] == "VO2max 6×2:30"
        assert e.c.patch(f"{API}/sessions/{q['uid']}", json={"variant_key": "zz"}).status_code == 400
        e.c.post(f"{API}/push-coros?scope=day&day={q['day']}")
        prog = next(p for p in e.fake.programs.values() if "6×2:30" in p["name"])
        names = [x["name"] for x in prog["exercises"]]
        assert "輕鬆跑暖身" in names and names.count("走路或極慢跑") == 5


def test_api_tests_are_suggested_and_put_in_by_the_user(monkeypatch):
    from backend.engine import aet_test as AT
    with Env(monkeypatch) as e:
        a = AT.session({"cp": 250.0, "lthr": 165.0}, 140.0, 190.0, 50, "ua40")
        e.inp["cur"]["test_suggestions"] = [{"kind": "aet", "protocol": "ua40", "title": a["title"], "minutes": a["minutes"],
                                             "reason": "6 週內沒有可判讀的跑步", "replaces_long": False,
                                             "session": {k: a.get(k) for k in ("kind", "title", "minutes", "target",
                                                                               "detail", "source", "protocol")}}]
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        hard = {s["day"] for s in ss if s["kind"] in ("long", "quality", "test")}
        r = e.c.get(f"{API}/test-suggestions").json()["suggestions"]
        (sg,) = r
        assert sg["label"].startswith("建議做一次 AeT 測試（6 週內沒有可判讀的跑步）— 要排在哪一天？")
        days = [d["day"] for d in sg["days"]]
        assert days and all(dt.date.fromisoformat(d).weekday() < 5 for d in days)          # aet_test_days = weekday
        for d in days:                                                                   # ≥ 1 easy day from hard ones
            x = dt.date.fromisoformat(d)
            assert not {(x + dt.timedelta(days=k)).isoformat() for k in (-1, 0, 1)} & hard
        assert e.c.post(f"{API}/test-suggestions/schedule", json={"kind": "aet", "day": "2020-01-01"}).status_code == 400
        s = e.c.post(f"{API}/test-suggestions/schedule", json={"kind": "aet", "day": days[0]}).json()
        assert s["kind"] == "test" and s["edited"] and s["origin"] == "custom" and AT.is_aet_session(s)
        assert e.c.get(f"{API}/test-suggestions").json()["suggestions"] == []           # done: no more nagging
        cal_t = e.c.get(f"{API}/test-templates").json()
        assert [t["protocol"] for t in cal_t["cp"]] == ["quick", "standard", "race"]
        assert [t["protocol"] for t in cal_t["aet"]] == list(AT.PROTOCOLS)


def test_api_xu90_test_added_on_a_long_day_replaces_the_long_run(monkeypatch):
    """SP-39 (owner 2026-10-04): 「安排課表」 on the 90-min test opens the 課表 dialog, which saves
    through POST /sessions — it takes that day's long run like 排入測試 does; other AeT tests don't."""
    from backend.engine import aet_test as AT
    with Env(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        long = next(s for s in ss if s["kind"] == "long" and s["day"] == "2026-10-04")
        ua = AT.session({"cp": 250.0, "lthr": 165.0}, 140.0, 190.0, 50, "ua60")
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-04", "kind": "test", "title": ua["title"],
                                              "minutes": ua["minutes"], "protocol": "aet"})
        assert r.status_code == 200
        live = e.c.get(f"{API}/sessions").json()["sessions"]
        assert any(s["uid"] == long["uid"] for s in live)                     # UA 60′: the long run stays
        e.c.delete(f"{API}/sessions/{r.json()['uid']}")
        xu = AT.session({"cp": 250.0, "lthr": 165.0}, 140.0, 190.0, 150, "xu90")
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-04", "kind": "test", "title": xu["title"],
                                              "minutes": xu["minutes"], "protocol": "aet"})
        assert r.status_code == 200 and AT.is_xu(r.json())
        live = e.c.get(f"{API}/sessions").json()["sessions"]
        assert not any(s["kind"] == "long" and s["day"] == "2026-10-04" for s in live)
        assert [s["uid"] for s in live if s["day"] == "2026-10-04" and s["kind"] == "test"] == [r.json()["uid"]]
        # the template-library row (排入測試's template path) is the same test
        from backend.api.plan_sessions import _test_rows
        assert any(s["kind"] == "long" and s["day"] == "2026-10-18" for s in live)
        t = _test_rows()["lib:xu_e_drift"]
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-18", "kind": "test", "title": t.get("title") or t["label"],
                                              "minutes": 90, "protocol": "aet"})
        assert r.status_code == 200
        live = e.c.get(f"{API}/sessions").json()["sessions"]
        assert not any(s["kind"] == "long" and s["day"] == "2026-10-18" for s in live)


def _b2b_sg(week="2026-09-28"):
    return {"id": f"b2b:{week}", "type": "b2b", "week": week, "title": "建議這週做一次 B2B", "reason": "上週是恢復週",
            "minutes": [150, 100], "long_day": "2026-10-04", "event": "嘉明湖", "event_days": 3,
            "event_kind": "baiyue", "weeks_out": 9, "help": "（推估）", "src": ""}


def test_api_suggestion_box_b2b_accept_cancel_and_dismiss(monkeypatch):
    """The floating box (engine/suggestions.py): a B2B is only suggested; 排入 stores the two
    days as the user's sessions + the accepted entry; deleting a day cancels it (declined for
    the week); 不要 / ✕ persist server-side; zone updates are information."""
    from backend.engine import plan_prefs as PP
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs())       # never the real settings
    with Env(monkeypatch) as e:
        e.inp["cur"]["b2b_suggestion"] = _b2b_sg()
        e.inp["zone"] = {"suggestions": [], "events": [
            {"id": "test_applied", "kind": "zone_update", "field": "cp", "date": "2026-09-25", "value": 250.0,
             "text": "CP 250 W（測試 2026-09-25）：從 2026-09-25 起區間已重算"}]}
        e.c.get(f"{API}/sessions")
        r = e.c.get(f"{API}/suggestions").json()
        by = {s["id"]: s for s in r["suggestions"]}
        b = by["b2b:2026-09-28"]
        assert b["pick"] == "pair" and b["options"] and all(o["day"] >= "2026-09-30" for o in b["options"])
        assert b["options"][0]["day"] == "2026-10-03" and b["options"][0]["end"] == "2026-10-04"   # weekend first
        assert by["zone_update:cp:2026-09-25"]["pick"] is None
        assert e.c.post(f"{API}/suggestions/accept", json={"id": b["id"], "day": "2026-09-01"}).status_code == 400
        out = e.c.post(f"{API}/suggestions/accept", json={"id": b["id"], "day": "2026-10-03"}).json()
        d1, d2 = out["sessions"]
        assert (d1["day"], d2["day"]) == ("2026-10-03", "2026-10-04") and d1["origin"] == d2["origin"] == "custom"
        assert d1["kind"] == d2["kind"] == "long" and d1["title"].startswith("B2B 第 1 天") and d2["minutes"] == 100
        assert out["accepted"]["uids"] == [d1["uid"], d2["uid"]]
        from backend.engine import b2b as B2B
        from backend.settings.repository import SettingsRepository
        acc = run(SettingsRepository(e.db).get(B2B.ACCEPTED_KEY))
        assert acc[0]["days"] == ["2026-10-03", "2026-10-04"] and acc[0]["minutes"] == [150, 100]
        ids = {s["id"] for s in e.c.get(f"{API}/suggestions").json()["suggestions"]}
        assert "b2b:2026-09-28" not in ids                                       # accepted: gone
        # the auto-replan keeps them (user sessions)
        e.c.post(f"{API}/reconcile")
        live = {s["uid"] for s in e.c.get(f"{API}/sessions").json()["sessions"]}
        assert {d1["uid"], d2["uid"]} <= live
        # deleting day 2 cancels the B2B: day 1 goes too, declined for this week
        assert e.c.delete(f"{API}/sessions/{d2['uid']}").json()["b2b_cancelled"]
        live = {s["uid"] for s in e.c.get(f"{API}/sessions").json()["sessions"]}
        assert not {d1["uid"], d2["uid"]} & live
        assert run(SettingsRepository(e.db).get(B2B.ACCEPTED_KEY)) == []
        dis = run(SettingsRepository(e.db).get("plan.suggestions.dismissed"))
        assert dis["b2b:2026-09-28"]["action"] == "declined"
        assert "b2b:2026-09-28" not in {s["id"] for s in e.c.get(f"{API}/suggestions").json()["suggestions"]}
        # ✕ on the zone update: persisted, not shown again
        assert e.c.post(f"{API}/suggestions/dismiss", json={"id": "zone_update:cp:2026-09-25"}).status_code == 200
        assert e.c.get(f"{API}/suggestions").json()["suggestions"] == []
        assert e.c.post(f"{API}/suggestions/dismiss", json={"id": "x", "action": "nope"}).status_code == 400
        # the update no longer computed: its dismissal is pruned (a new one would show again)
        e.inp["zone"] = {"suggestions": [], "events": []}
        e.c.get(f"{API}/suggestions")
        dis = run(SettingsRepository(e.db).get("plan.suggestions.dismissed"))
        assert "zone_update:cp:2026-09-25" not in dis and "b2b:2026-09-28" in dis     # this week's B2B stays


def test_api_suggestion_box_tests_and_zone_retests(monkeypatch):
    from backend.engine import aet_test as AT
    from backend.engine import plan_prefs as PP
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs(aet_test_protocol="ua40"))
    with Env(monkeypatch) as e:
        a = AT.session({"cp": 250.0, "lthr": 165.0}, 140.0, 190.0, 50, "ua40")
        e.inp["cur"]["test_suggestions"] = [{"kind": "cp", "protocol": "quick", "title": "CP 測試", "minutes": 40,
                                             "reason": "門檻過期", "replaces_long": False,
                                             "session": {"kind": "test", "title": "CP 測試 3 分＋12 分",
                                                         "minutes": 40, "protocol": "quick"}}]
        e.inp["zone"] = {"events": [], "suggestions": [
            {"id": "cool_season", "kind": "test_suggestion", "tests": ["tt30", "aet"], "title": "天氣轉涼了",
             "text": "清晨 < 25 °C", "earliest": None, "detected": "2026-09-27", "conditions": ["x：y"],
             "caveat": "手腕心率", "estimate": True, "source": "s", "evidence": {}}]}
        e.c.get(f"{API}/sessions")
        by = {s["id"]: s for s in e.c.get(f"{API}/suggestions").json()["suggestions"]}
        t = by["test:cp:2026-09-28"]
        assert t["pick"] == "day" and t["options"]
        z = by["zone:cool_season"]
        assert z["pick"] == "test_day" and [x["key"] for x in z["tests"]] == ["aet"]      # tt30: described only
        assert "30 分鐘獨跑測試" in z["help"] and "推估" in z["help"]
        assert z["tests"][0]["session"]["kind"] == "test" and "40" in z["tests"][0]["label"]   # 課表偏好 UA 40
        day = z["tests"][0]["options"][0]["day"]
        assert e.c.post(f"{API}/suggestions/accept", json={"id": "zone:cool_season", "day": day}).status_code == 400
        s = e.c.post(f"{API}/suggestions/accept", json={"id": "zone:cool_season", "day": day, "test": "aet"}).json()
        assert s["sessions"][0]["kind"] == "test" and AT.is_aet_session(s["sessions"][0])
        # 不要 on the CP test: hidden for this week, also from the 課表 page's own list
        e.c.post(f"{API}/suggestions/dismiss", json={"id": "test:cp:2026-09-28", "action": "declined"})
        assert e.c.get(f"{API}/suggestions").json()["suggestions"] == []
        assert e.c.get(f"{API}/test-suggestions").json()["suggestions"] == []
        assert a["minutes"] > 0


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
            g("long", "long", "LSD（山路）", 120, "2026-10-04")])
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


def test_new_week_page_load_closes_last_week(monkeypatch):
    """Next week already exists (projected); opening the page on Monday still
    reconciles, because last week's sessions are left open."""
    with Env(monkeypatch) as e:
        e.c.get(f"{API}/sessions")
        monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 5))
        cur = cur_plan(today="2026-10-05", sessions=[])
        cur["week"] = {"start": "2026-10-05", "end": "2026-10-11", "today": "2026-10-05", "days_left": 7}
        cur["sessions"] = next_week()["sessions"]
        e.inp = inputs(today="2026-10-05", cur=cur, weeks=[], horizon="2026-10-11")
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        last = [s for s in ss if s["week_start"] == "2026-09-28"]
        assert last and all(s["state"] in ("missed", "done") for s in last)


def test_concurrent_first_loads_do_not_duplicate(monkeypatch):
    import asyncio as aio
    from backend.api import plan_sessions
    with Env(monkeypatch) as e:
        async def both():
            return await aio.gather(plan_sessions.sessions(db=e.db), plan_sessions.sessions(db=e.db))
        run(both())
        rows = run(e.db.execute(select(PlanSession))).scalars().all()
        keys = [(r.week_start, r.gen_key) for r in rows if r.state == "active"]
        assert len(keys) == len(set(keys))


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


# ---------------------------------------------------------------------------
# bars + PMC projection follow the stored plan
# ---------------------------------------------------------------------------

def test_plan_summary_math():
    ss = [{"state": "done", "day": "2026-09-29", "kind": "easy", "minutes": 40, "tss": 32.0},
          {"state": "active", "day": "2026-10-01", "kind": "quality", "minutes": 60, "tss": 48.0},
          {"state": "active", "day": "2026-10-02", "kind": "strength", "minutes": 35, "tss": 10.0},
          {"state": "missed", "day": "2026-09-30", "kind": "easy", "minutes": 45, "tss": 36.0},
          {"state": "deleted", "day": "2026-10-03", "kind": "long", "minutes": 120, "tss": 96.0},
          {"state": "active", "day": "2026-10-06", "kind": "easy", "minutes": 50, "tss": 40.0}]
    s = PS.plan_summary(ss, "2026-09-28", "2026-09-30", 30.0, 32.0, 42.0, 7.0, "2026-10-11")
    assert s["hours"] == pytest.approx(100 / 60) and s["tss"] == pytest.approx(90.0)   # no strength time, no missed/deleted
    days = [r["date"] for r in s["projection"]]
    assert days[0] == "2026-10-01" and days[-1] == "2026-10-11"
    assert {r["date"]: r["tss"] for r in s["projection"]}["2026-10-06"] == 40.0
    c = 30.0
    for x in (48.0, 10.0, 0.0, 0.0):                         # 10/1 .. 10/4
        c += (x - c) / 42.0
    assert s["ctl_end"] == pytest.approx(c)


def test_edit_moves_the_bars_and_projection(monkeypatch):
    with Env(monkeypatch) as e:
        body = e.c.get(f"{API}/sessions").json()
        s0 = body["summary"]
        long = next(s for s in body["sessions"] if s["day"] == "2026-10-04")
        e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 60})
        e.c.delete(f"{API}/sessions/{next(s for s in body['sessions'] if s['day'] == '2026-10-01')['uid']}")
        s1 = e.c.get(f"{API}/sessions").json()["summary"]
        assert s1["hours"] == pytest.approx(s0["hours"] - 1.0 - 1.0)       # long 120→60, quality 60 gone
        p0 = {r["date"]: r["tss"] for r in s0["projection"]}
        p1 = {r["date"]: r["tss"] for r in s1["projection"]}
        assert p0["2026-10-01"] > 0 and p1["2026-10-01"] == 0
        assert s1["ctl_end"] < s0["ctl_end"]


# ---------------------------------------------------------------------------
# spec-sync regressions
# ---------------------------------------------------------------------------

def _test_week(gate):
    """This week: a CP test in place of the quality session (week_plan's test_due)."""
    cur = cur_plan(sessions=[
        g("test", "test", "CP 測試 3 分 + 12 分", 60, "2026-10-01"),
        g("strength1", "strength", "肌力（下肢單腳＋核心）", 35, "2026-10-02"),
        g("easy1", "easy", "輕鬆跑", 45, "2026-10-02"),
        g("long", "long", "LSD（山路）", 120, "2026-10-04")])
    cur["quality_gate"] = gate
    return cur


def _quality_by_week(weeks):
    return {w["start"]: [s["title"] for s in w["sessions"] if s["kind"] == "quality"]
            for w in weeks if w["mode"] != "recovery_week"}


def test_projection_gate_per_week_cp_test_and_drift_gate_do_not_leak():
    from backend.engine import quality_gate as QG
    good = {"intensity": "good", "drift": "good"}

    def split(weeks):
        q = _quality_by_week(weeks)
        aet = {w["start"] for w in weeks if any(s["id"] == "test_aet" for s in w["sessions"])}
        base = [w["start"] for w in weeks if w["phase"] == "base" and w["mode"] != "recovery_week"
                and w["start"] not in aet]
        spec = [w["start"] for w in weeks if w["phase"] == "specific" and w["mode"] != "recovery_week"]
        return q, base, spec
    # the old {levels, streak_ok} shape: the unsourced drift streak no longer
    # blocks — no method, the guardrails and the dose table step forward per week
    # (this week's CP test is not a step and doesn't leak)
    weeks = P.project_weeks(_test_week({"levels": good, "streak_ok": False}), PHASES, date(2027, 3, 1))
    q, base, spec = split(weeks)
    from backend.engine import interval_library as IL
    dose = [s[1] for s in QG.LADDER] + [IL.title(v) for v in IL.ALL.values()]
    assert base and all(q[d] and q[d][0] in dose for d in base)
    # 台灣教練: Zone 3 first; without a confirmed base (no z5) Zone 3 keeps going — each rung's
    # standard session (no cap: the full-length one, engine/interval_library.fit)
    assert [q[d][0] for d in base][:2] == [IL.title(IL.canonical("z3a")), IL.title(IL.canonical("z3b"))]
    assert all(q[d][0].startswith("閾值") for d in base), {d: q[d] for d in base}
    # 專項期 with Zone 5 not confirmed: the Zone 3 ladder carries on instead of the 5×4′ hill set
    assert spec and all(q[d] and q[d][0].startswith("閾值") for d in spec), {d: q[d] for d in spec}
    # Zone 5 open but Zone 3 not steady yet (0 of 3 達標): Zone 3 first (two tracks, SP-31) — the base
    # weeks' A rungs are over 10 % of a ~5 h week, so their 巡航版 3×6′ / 3×8′ / 2×12′ (still counted);
    # three of them make Zone 3 steady, and the 專項期 then takes its Zone 5 track (trail: the 5×4′
    # hill set) on its turn and keeps Zone 3 (A4 1×30′) on the other
    open5 = {"levels": good, "state": "none", "mode": "auto", "resolved": "none", "guard": {},
             "dose": {"step": 0, "done": 0, "faded": False}, "z5": {"open": True, "state": "confirmed"},
             "ratio": {"z3": 1, "z5": 1, "why": "A 賽 10 km 路跑"}}
    q5, base5, spec5 = split(P.project_weeks(_test_week(open5), PHASES, date(2027, 3, 1)))
    assert [q5[d][0] for d in base5][:3] == ["閾值 3×6 分", "閾值 3×8 分", "閾值 2×12 分"]
    assert len(spec5) == 2 and sorted(q5[d][0].startswith("閾值") for d in spec5) == [False, True]
    assert any(q5[d] == ["爬坡間歇 5×4 分"] for d in spec5)
    # a locked method (data there, criterion not met): Zone 3 still goes on, never Zone 5
    locked = {"state": "locked", "mode": "ua_gap", "resolved": "ua_gap", "verdict": "差距 16%", "levels": good,
              "guard": {}, "dose": {"step": 0, "done": 0, "faded": False}, "z5": {"open": False}}
    q, base, spec = split(P.project_weeks(_test_week(locked), PHASES, date(2027, 3, 1)))
    assert base and all(q[d] and q[d][0].startswith("閾值") for d in base)
    assert spec and all(q[d] and q[d][0].startswith("閾值") for d in spec)
    # intensity bad (SP-31): the low-intensity share keeps Zone 5 out but no longer stops Zone 3 —
    # base and specific weeks keep their Zone 3 session, never a Zone 5 one
    weeks = P.project_weeks(_test_week({"levels": {"intensity": "bad", "drift": "good"}, "streak_ok": True}),
                            PHASES, date(2027, 3, 1))
    q = {s: v for s, v in _quality_by_week(weeks).items()
         if next(w for w in weeks if w["start"] == s)["phase"] in ("base", "specific")}
    assert q and all(v and v[0].startswith("閾值") for v in q.values()), q
    # drift bad still stops the 專項期
    weeks = P.project_weeks(_test_week({"levels": {"intensity": "good", "drift": "bad"}, "streak_ok": True}),
                            PHASES, date(2027, 3, 1))
    assert all(v == [] for s, v in _quality_by_week(weeks).items()
               if next(w for w in weeks if w["start"] == s)["phase"] == "specific")


def test_projection_uses_the_athletes_atl_constant(monkeypatch):
    from backend.engine import overview as O
    seen = []
    real = O.project

    def spy(ctl, atl, planned, cc, ac):
        seen.append((cc, ac))
        return real(ctl, atl, planned, cc, ac)
    monkeypatch.setattr(O, "project", spy)
    P.project_weeks(cur_plan(), PHASES, date(2026, 10, 11), 40.0, 10.0)
    assert seen and all(x == (40.0, 10.0) for x in seen)


def test_projection_survives_a_session_without_a_day(monkeypatch):
    real = P.week_sessions

    def with_unplaced(*a, **kw):
        ss = real(*a, **kw)
        ss.append({**ss[-1], "id": "strength9", "kind": "strength", "day": None})
        return ss
    monkeypatch.setattr(P, "week_sessions", with_unplaced)
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 10, 14))
    assert weeks and all(s["day"] for w in weeks for s in w["sessions"])


def test_past_week_scope_is_a_400(monkeypatch):
    with Env(monkeypatch) as e:
        q = "scope=week&day=2026-09-21"                                 # Mon 9/21 – Sun 9/27, today 9/30
        r = e.c.get(f"{API}/push-coros/preview?{q}")
        assert r.status_code == 400 and "已經過去" in r.json()["detail"]
        assert e.c.post(f"{API}/push-coros?{q}").status_code == 400
        assert e.c.delete(f"{API}/push-coros?{q}").status_code == 400
        assert e.c.get(f"{API}/push-coros/preview?scope=week&day=2026-09-28").status_code == 200


# ---------------------------------------------------------------------------
# 賽事計算機「匯出至課表」 (plan_store.upsert_external, SP-43)
# ---------------------------------------------------------------------------

RACE_STEPS = {"v": 1, "origin": "user", "items": [
    {"id": "r1", "kind": "work", "dur": {"type": "open", "est": 3600}, "target": {"type": "hr", "mode": "abs", "lo": 136, "hi": 160},
     "note": "→ 補給站 1 · 約 1:00"},
    {"id": "r2", "kind": "work", "dur": {"type": "open", "est": 1800}, "target": {"type": "none"}, "note": "→ 終點"}]}
KEY = "racecalc:ev1"


def race_data(day="2026-10-10", minutes=90, title="賽事 測試越野"):
    return {"day": day, "title": title, "minutes": minutes, "tss": 120.0, "detail": "每段直到按下計圈", "target": "",
            "terrain": "trail", "distance_km": 21.0, "climb_m": 1200, "steps": RACE_STEPS, "source": "賽事計算機匯出"}


def race_week(with_race=True):
    ss = [g("quality", "quality", "閾值 3×10 分", 60, "2026-10-06"), g("easy1", "easy", "輕鬆跑", 50, "2026-10-07")]
    if with_race:
        ss.append({**g("race", "race", "比賽", 0, "2026-10-10"), "tss": 0.0})
    return next_week(ss)


def races(ss, ws="2026-10-05"):
    return [s for s in ss if s["kind"] == "race" and s["state"] == "active" and s["week_start"] == ws]


def test_export_claims_the_generators_race_and_reexport_overwrites():
    db = run(make_db())
    inp = inputs(weeks=[race_week()])
    run(PS.plan_reconcile(db, inp, apply=True))
    gen = races(run(PS.load(db)))[0]
    pv = run(PS.upsert_external(db, KEY, race_data(), "2026-09-30", write=False))
    assert pv["action"] == "claim" and pv["previous"] is None
    assert races(run(PS.load(db)))[0]["minutes"] == 0                     # the preview wrote nothing
    w = run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))
    assert w["action"] == "claim"
    rs = races(run(PS.load(db)))
    assert len(rs) == 1 and rs[0]["uid"] == gen["uid"] and rs[0]["ext_key"] == KEY and rs[0]["edited"]
    assert rs[0]["minutes"] == 90 and rs[0]["steps"]["items"][0]["note"].startswith("→ 補給站")
    # reconcile keeps it where it is, adds no second race, moves nothing else onto race day
    run(PS.plan_reconcile(db, inp, apply=True))
    ss = run(PS.load(db))
    rs = races(ss)
    assert len(rs) == 1 and rs[0]["day"] == "2026-10-10" and rs[0]["minutes"] == 90
    assert not [s for s in ss if s["state"] == "active" and s["day"] == "2026-10-10" and s["kind"] != "race"]
    # the same export: unchanged (updated_at stays); another one: the same row, updated_at moves
    row = lambda: run(db.execute(select(PlanSession).where(PlanSession.uid == gen["uid"]))).scalar_one()   # noqa: E731
    t0 = row().updated_at
    assert run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))["action"] == "unchanged"
    assert row().updated_at == t0
    up = run(PS.upsert_external(db, KEY, race_data(minutes=100), "2026-09-30"))
    assert up["action"] == "update" and up["previous"]["user_edited"] is False
    assert row().updated_at > t0 and len(races(run(PS.load(db)))) == 1


def test_export_warns_about_an_edit_on_the_schedule_and_comes_back_after_delete():
    db = run(make_db())
    run(PS.plan_reconcile(db, inputs(weeks=[race_week()]), apply=True))
    s = run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))["session"]
    e = run(PS.edit(db, s["uid"], {"kind": "race", "minutes": 80}, "2026-09-30"))     # the 課表 dialog sends its kind
    assert e["kind"] == "race" and e["ext_key"] == KEY
    pv = run(PS.upsert_external(db, KEY, race_data(), "2026-09-30", write=False))
    assert pv["action"] == "update" and pv["previous"]["user_edited"] is True
    run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))
    assert run(PS.upsert_external(db, KEY, race_data(), "2026-09-30", write=False))["previous"]["user_edited"] is False
    # deleted on the 課表 (the claimed generator row: a tombstone) → exporting again restores it
    assert run(PS.delete(db, s["uid"]))["state"] == "deleted"
    r = run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))
    assert r["action"] == "restore" and len(races(run(PS.load(db)))) == 1


def test_export_without_a_generated_race_blocks_the_later_one():
    db = run(make_db())
    run(PS.plan_reconcile(db, inputs(weeks=[race_week(with_race=False)]), apply=True))
    r = run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))
    assert r["action"] == "add" and r["session"]["origin"] == "custom"
    # the season plan later puts its own race in that week: not added beside the export
    run(PS.plan_reconcile(db, inputs(weeks=[race_week()]), apply=True))
    rs = races(run(PS.load(db)))
    assert [x["uid"] for x in rs] == [r["session"]["uid"]]
    # the race moved (the event's date changed): the row follows
    m = run(PS.upsert_external(db, KEY, race_data(day="2026-10-17"), "2026-09-30"))
    assert m["action"] == "update" and m["session"]["week_start"] == "2026-10-12"
    with pytest.raises(PS.PlanError):
        run(PS.upsert_external(db, KEY, race_data(day="2026-09-01"), "2026-09-30"))   # past day


def test_race_is_not_added_by_hand_and_counts_in_the_week():
    db = run(make_db())
    run(PS.plan_reconcile(db, inputs(weeks=[race_week()]), apply=True))
    with pytest.raises(PS.PlanError):
        run(PS.add(db, {"day": "2026-10-09", "kind": "race", "title": "比賽"}, "2026-09-30"))
    easy = next(s for s in run(PS.load(db)) if s.get("gen_key") == "easy1" and s["week_start"] == "2026-10-05")
    with pytest.raises(PS.PlanError):
        run(PS.edit(db, easy["uid"], {"kind": "race"}, "2026-09-30"))
    before = PS.plan_summary(run(PS.load(db)), "2026-10-05", "2026-09-30", 30.0, 32.0, 42.0, 7.0)["tss"]
    run(PS.upsert_external(db, KEY, race_data(), "2026-09-30"))
    after = PS.plan_summary(run(PS.load(db)), "2026-10-05", "2026-09-30", 30.0, 32.0, 42.0, 7.0)
    assert after["tss"] == pytest.approx(before + 120.0)


def test_api_push_sends_the_exported_race_and_replaces_the_old_calculator_workout(monkeypatch):
    with Env(monkeypatch) as e:
        e.inp["weeks"][0] = race_week()
        e.c.get(f"{API}/sessions")
        # a workout the calculator pushed straight to the watch before (library only)
        old = run(CW.push_workout(e.db, {"id": "ev1", "key": KEY, "kind": "race_plan", "title": "賽事 測試越野",
                                         "steps": RACE_STEPS, "day": None, "detail": ""}, TH, "2026-09-30"))
        assert old["status"] == "pushed" and len(e.fake.live()) == 1
        s = run(PS.upsert_external(e.db, KEY, race_data(), "2026-09-30"))["session"]
        pv = e.c.get(f"{API}/push-coros/preview", params={"scope": "week", "day": "2026-10-10"}).json()
        race = next(x for x in pv["sessions"] if x["uid"] == s["uid"])
        assert race["coros"]["status"] == "not_pushed" and pv["calc_to_replace"] == 1
        r = e.c.post(f"{API}/push-coros", params={"scope": "week", "day": "2026-10-10"})
        assert r.status_code == 200, r.text
        res = r.json()
        assert next(x for x in res["sessions"] if x["id"] == s["uid"])["status"] == "pushed"
        assert any(x["status"] == "removed" for x in res["removed"])
        assert old["coros_program_id"] not in e.fake.live()
        names = [p["name"] for p in e.fake.live().values()]
        assert sum("賽事 測試越野" in n for n in names) == 1
        assert {x.session_key for x in e.pushed()} >= {s["uid"]} and KEY not in {x.session_key for x in e.pushed()}
        # the generator's own race (no steps) is still not pushed
    with pytest.raises(CW.Unsupported):
        CW.session_steps({"kind": "race", "minutes": 0}, CW.Thresholds.of(TH))
    assert len(CW.session_steps({"kind": "race", "minutes": 90, "steps": RACE_STEPS}, CW.Thresholds.of(TH))) == 2
