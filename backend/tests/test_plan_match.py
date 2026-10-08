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
    PM.assign(out, [act(9, "2026-10-01", 58, hard_s=60, tss=30)], "2026-10-02", {})    # TSS −38 %: ≠
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
    e = sess("e", "2026-10-01", state="done", done_by=act(11, "2026-10-01", 45, hard_s=1200, tss=60))  # TSS +67 %
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
    a = act(31, "2026-10-01", 58, hard_s=30, tss=20)          # TSS far off: still ≠ (SP-370)
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


# ---------------------------------------------------------------------------
# 完成度等級 (SP-216): intensity graded like time / TSS, not pass / fail
# ---------------------------------------------------------------------------

def _q(done_by, kind="quality", minutes=60, title="閾值 3×10 分"):
    return sess("q", "2026-10-01", kind, minutes, title, state="done", done_by=done_by)


def _ses(typ, z3=None, eq=None):
    return {"type": typ, "type_label": "", "z3_s": z3, "t_vo2_eq_s": eq}


def test_99_percent_of_the_intensity_is_done_not_off_plan():
    # the threshold-time rule (no classifier row): 9.9 of 10 min
    q = _q(act(20, "2026-10-01", 60, hard_s=594, tss=48))
    vs = PM.compare(q)
    assert not vs["off_plan"] and not vs["short"] and vs["actual"] == "hard" and vs["intensity_pct"] == 99
    comp = C.with_plan_check(C.session_compliance(q, 48.0), vs)
    assert comp["level"] == "green" and comp["intensity_pct"] == 99 and C.status_of(q, comp, "2026-10-02") == "done"
    # the classifier read it 「輕鬆跑」 (Zone 3 9.9 min < its 10-min bar): still done
    q = _q({**act(21, "2026-10-01", 60, tss=48), "session": _ses("easy", z3=594, eq=60)})
    vs = PM.compare(q)
    assert not vs["off_plan"] and vs["intensity_pct"] == 99 and vs["actual"] == "hard"


def test_intensity_short_is_partial_with_its_own_label_and_headline():
    q = _q({**act(22, "2026-10-01", 60, tss=48), "session": _ses("easy", z3=420)})       # 7 of 10 min
    vs = PM.compare(q)
    assert vs["short"] and not vs["off_plan"] and vs["grade"] == "short" and vs["intensity_pct"] == 70
    assert vs["short_text"] == "強度不足：只做到這堂課的 70%（Zone 3 7／10 分）"
    comp = C.with_plan_check(C.session_compliance(q, 48.0), vs)
    assert comp["level"] == "yellow" and comp["label"] == "強度不足" and comp["short"] and not comp.get("off_plan")
    assert comp["pct"] == 70 and comp["tss_pct"] == 100                   # the number that explains the colour
    assert C.status_of(q, comp, "2026-10-02") == "partial"
    row = C.session_row({**q, "compliance": comp, "tss_est": 48.0}, "2026-10-02")
    assert row["status"] == "partial" and row["off_text"] == vs["short_text"]
    # an equivalent T@VO2max dose counts too: 3 of 4 min = 75 %
    q = _q({**act(23, "2026-10-01", 60, tss=48), "session": _ses("easy", z3=60, eq=180)}, title="VO2max 5×3 分")
    vs = PM.compare(q)
    assert vs["short"] and vs["intensity_pct"] == 75 and "等效 T@VO2max 3.0／4 分" in vs["short_text"]


def test_under_half_the_intensity_is_still_off_plan():
    q = _q({**act(24, "2026-10-01", 60, tss=30), "session": _ses("easy", z3=240)})       # 40 %, TSS −38 %
    vs = PM.compare(q)
    assert vs["off_plan"] and not vs["short"] and vs["actual"] == "easy" and vs["intensity_pct"] == 40
    assert vs["text"] == "沒照課表：排強度課，實際跑輕鬆"
    comp = C.with_plan_check(C.session_compliance(q, 48.0), vs)
    assert comp["label"] == "沒照課表" and C.status_of(q, comp, "2026-10-02") == "off_plan"


def test_a_classified_hard_run_is_done_even_without_its_numbers():
    q = _q({**act(25, "2026-10-01", 60, tss=48), "session": _ses("quality")})
    vs = PM.compare(q)
    assert not vs["off_plan"] and not vs["short"] and vs["intensity_pct"] == 100


def test_easy_run_slightly_hard_is_partial_far_too_hard_is_off_plan():
    e = sess("e", "2026-10-01", state="done",
             done_by={**act(26, "2026-10-01", 45, tss=36), "session": _ses("quality", z3=720)})   # 1.2× the bar
    vs = PM.compare(e)
    assert vs["short"] and not vs["off_plan"] and vs["grade"] == "over" and vs["intensity_pct"] is None
    assert vs["short_text"] == "輕鬆跑偏強（Zone 3 12／10 分）"
    comp = C.with_plan_check(C.session_compliance(e, 36.0), vs)
    assert comp["label"] == "強度偏高" and comp["level"] == "yellow" and comp["pct"] == 100
    e = sess("e", "2026-10-01", state="done",
             done_by={**act(27, "2026-10-01", 45, tss=50), "session": _ses("hard_long", z3=1000)})  # 1.67×, TSS +39 %
    assert PM.compare(e)["off_plan"] and PM.compare(e)["text"] == "沒照課表：排輕鬆跑，實際跑強度"
    # a CP test on an easy day: ran hard, never 「偏強」
    e = sess("e", "2026-10-01", state="done",
             done_by={**act(28, "2026-10-01", 45, tss=50), "session": _ses("test_cp")})
    assert PM.compare(e)["off_plan"]


def test_intensity_short_still_matches_its_session():
    s = sess("q", "2026-10-01", "quality", 60, "閾值 3×10 分")
    short = {**act(29, "2026-10-01", 60), "session": _ses("easy", z3=420)}
    easy = {**act(30, "2026-10-01", 60), "session": _ses("easy", z3=60)}
    assert PM.cost(s, short) < PM.cost(s, easy)
    assert PM.cost(s, short) == PM.cost(s, {**act(31, "2026-10-01", 60), "session": _ses("quality", z3=900)})


def test_session_row_carries_the_doses(monkeypatch):
    from backend.engine import overview as O
    from backend.engine import workout_review as WR
    monkeypatch.setattr(WR, "classify", lambda ds, w: {"type": "easy", "type_label": "輕鬆跑", "stimulus": None,
                                                        "icon": "x", "moderate": False,
                                                        "stim": {"z3_s": 594.04, "t_vo2_eq_s": 61.26}})
    assert O.session_of(None, None) == {"type": "easy", "type_label": "輕鬆跑", "stimulus": None, "icon": "x",
                                        "moderate": False, "z3_s": 594.0, "t_vo2_eq_s": 61.3}
    monkeypatch.setattr(WR, "classify", lambda ds, w: {"type": "strength", "stim": None})
    assert O.session_of(None, None)["z3_s"] is None


# ---------------------------------------------------------------------------
# SP-370: 沒照課表 only when it really wasn't the plan. A walking session takes a hike / walk;
# time and TSS both within ±20 % -> an intensity reversal or another foot sport is ◐, not ≠
# ---------------------------------------------------------------------------

LABEL = {"road": "路跑", "trail": "越野跑", "hike": "登山健行", "walk": "走路", "bike": "騎車"}


def _done(kind, minutes, tss, a_min, a_tss, cat="road", title=None, gen_key=None, session=None, hard_s=0.0):
    a = {**act(40, "2026-10-05", a_min, cat=cat, tss=a_tss, hard_s=hard_s), "category_label": LABEL.get(cat, cat),
         "match": "day"}
    if session:
        a["session"] = session
    return sess("s", "2026-10-05", kind, minutes, title, state="done", gen_key=gen_key, done_by=a, tss=tss)


def _verdict(s):
    """What every consumer shows: calendar vs + compliance, the dashboard status."""
    vs = PM.compare(s)
    comp = C.with_plan_check(C.session_compliance(s), vs)
    return vs, comp, C.status_of(s, comp, "2026-10-08")


STEEP = "陡坡健走 13%（模擬負重 3.4 kg）"


@pytest.mark.parametrize("cat", ["hike", "walk", "trail"])
def test_steep_hill_walk_done_as_a_hike_is_done_not_off_plan(cat):
    # the 10/5 shape: the planner's 陡坡健走 (kind easy, steep_hill.py) walked — time 86 %, TSS 97 %
    s = _done("easy", 50, 40.0, 43, 38.8, cat=cat, title=STEEP, gen_key="steep")
    vs, comp, st = _verdict(s)
    assert not vs["off_plan"] and not vs["wrong_sport"] and not comp["wrong_type"]
    assert comp["level"] == "green" and st == "done"
    # a stored row the user retitled / that lost its gen_key: the title still says walking
    vs, comp, st = _verdict(_done("easy", 50, 40.0, 43, 38.8, cat=cat, title=STEEP))
    assert st == "done"


def test_steep_hill_walk_matches_the_hike_of_its_day_and_still_refuses_a_bike():
    out = [sess("w", "2026-10-05", "easy", 50, STEEP, gen_key="steep")]
    PM.assign(out, [{**act(41, "2026-10-05", 43, cat="walk", tss=38.8), "category_label": "走路"}], "2026-10-06", {})
    assert out[0]["state"] == "done" and PM.sport_ok(out[0], out[0]["done_by"])
    vs, comp, st = _verdict(_done("easy", 50, 40.0, 43, 38.8, cat="bike", title=STEEP, gen_key="steep"))
    assert vs["off_plan"] and comp["level"] == "red" and st == "off_plan"


def test_lsd_run_as_intensity_with_time_and_tss_on_plan_is_partial():
    # the live 10/5 shape: LSD 50′ TSS 66, run 43′ TSS 65 that the classifier calls intensity
    s = _done("long", 50, 66.0, 43, 65.0, session=_ses("hard_long", z3=1000))            # 1.67× the bar
    vs, comp, st = _verdict(s)
    assert not vs["off_plan"] and vs["short"] and vs["soft"] == "intensity"
    assert vs["short_text"] == "時間和負荷都對，但跑成強度課（Zone 3 17／10 分）"
    assert comp["level"] == "yellow" and comp["label"] == "跑成強度課" and not comp.get("off_plan")
    assert st == "partial"


@pytest.mark.parametrize("cat,status", [("trail", "done"), ("hike", "done"), ("walk", "partial")])
def test_lsd_on_another_foot_sport_with_time_and_tss_on_plan(cat, status):
    # a trail run / hike already counts as a long day (KIND_OK); a walk is another foot sport: ◐
    s = _done("long", 50, 66.0, 43, 65.0, cat=cat)
    vs, comp, st = _verdict(s)
    assert not vs["off_plan"] and st == status
    if status == "partial":
        assert vs["soft"] == "sport" and comp["wrong_type"] and comp["level"] == "yellow"
        assert vs["short_text"] == "時間和負荷都對，但項目不同：排LSD，實際走路"
        assert comp["label"] == "項目不同" and comp["off_text"] == vs["short_text"]


def test_easy_run_as_intensity_partial_when_time_and_tss_green_off_plan_when_tss_off():
    hard = _ses("hard_long", z3=1000)
    vs, comp, st = _verdict(_done("easy", 45, 36.0, 45, 40.0, session=hard))             # TSS +11 %
    assert st == "partial" and vs["soft"] == "intensity" and "跑成強度課" in vs["short_text"]
    vs, comp, st = _verdict(_done("easy", 45, 36.0, 45, 46.8, session=hard))             # TSS +30 %
    assert st == "off_plan" and vs["off_plan"] and vs["text"] == "沒照課表：排輕鬆跑，實際跑強度"
    assert comp["label"] == "沒照課表"
    # time off > 20 % keeps it ≠ too
    vs, comp, st = _verdict(_done("easy", 45, 36.0, 33, 36.0, session=hard))             # time −27 %
    assert st == "off_plan"


def test_quality_run_easy_with_time_and_tss_on_plan_is_partial():
    s = _done("quality", 60, 48.0, 58, 50.0, title="閾值 3×10 分", session=_ses("easy", z3=240))   # 40 %
    vs, comp, st = _verdict(s)
    assert st == "partial" and vs["soft"] == "intensity" and vs["short_text"] == "時間和負荷都對，但跑成輕鬆（Zone 3 4／10 分）"
    assert comp["label"] == "跑成輕鬆"


def test_softening_needs_both_time_and_tss_measured():
    # owner 2026-10-08: a 60-min easy run vs a 60-min walk with no TSS -> ≠ (only time is known)
    s = _done("easy", 60, 48.0, 60, 48.0, cat="walk")
    s["done_by"]["tss"] = None
    vs, comp, st = _verdict(s)
    assert vs["off_plan"] and comp["level"] == "red" and comp["time_level"] is None and st == "off_plan"
    # the same walk with its TSS on plan -> ◐ 項目不同
    vs, comp, st = _verdict(_done("easy", 60, 48.0, 60, 48.0, cat="walk"))
    assert vs["soft"] == "sport" and comp["level"] == "yellow" and st == "partial"
    # a planned TSS of 0 is not measured either: the reversal stays ≠
    s = _done("easy", 45, 0.0, 45, 36.0, session=_ses("hard_long", z3=1000))
    vs, comp, st = _verdict(s)
    assert vs["off_plan"] and st == "off_plan"
    # … but with the page's estimate (api/plan_sessions.est_tss) it is measured: ◐
    vs = PM.compare(s, 36.0)
    comp = C.with_plan_check(C.session_compliance(s, 36.0), vs)
    assert vs["soft"] == "intensity" and C.status_of(s, comp, "2026-10-08") == "partial"


def test_activity_review_card_uses_the_calendars_planned_tss(monkeypatch):
    """The stored row has no TSS (0): the calendar grades on its estimate (minutes × the easy rate),
    the workout review's 課表 card must too — both ◐ 跑成強度課, never ◐ on one and ≠ on the other."""
    from types import SimpleNamespace

    from backend.engine import overview as O
    from backend.engine import workout_review as WR
    s = _done("easy", 60, 0.0, 60, 50.0, session=_ses("hard_long", z3=1000))
    tph = {"easy": 50.0}                                       # the dataset's easy TSS / h
    cal = plan_sessions._decorate([dict(s)], [], plan_sessions.tss_rates(tph, [s]), s["day"], s["day"])[0]
    assert cal["tss_est"] == 50.0 and cal["compliance"]["label"] == "跑成強度課"
    assert C.status_of(cal, cal["compliance"], "2026-10-08") == "partial"
    monkeypatch.setattr(PS, "done_session", lambda idx, db_path=None: dict(s))
    monkeypatch.setattr(PS, "rate_rows", lambda db_path=None: [s])
    monkeypatch.setattr(O, "_tss_per_hour", lambda ds, today, *a, **k: tph)

    def no_row(w, ds):
        raise RuntimeError("no dataset")
    monkeypatch.setattr(O, "activity_row", no_row)
    card = WR._plan_card(SimpleNamespace(today=20000.0), SimpleNamespace(idx=40, metrics={}))
    assert card["sub"] == cal["compliance"]["label"] == "跑成強度課"
    assert card["level"] == WR.PLAN_LEVEL[cal["compliance"]["level"]] == "warn"
    assert "TSS：50 ／ 計畫 50（100%）" in card["tip"]
    # the stored TSS alone (no estimate) would have left the TSS unmeasured: ≠ 沒照課表
    assert C.with_plan_check(C.session_compliance(s), PM.compare(s))["label"] == "沒照課表"


def test_bike_for_a_run_stays_off_plan_even_with_time_and_tss_on_plan():
    vs, comp, st = _verdict(_done("easy", 45, 36.0, 45, 36.0, cat="bike"))
    assert vs["off_plan"] and vs["wrong_sport"] and vs["text"] == "沒照課表：排輕鬆跑，實際騎車"
    assert comp["level"] == "red" and comp["wrong_type"] and st == "off_plan"
    # a foot sport with time off > 20 %: still ≠
    vs, comp, st = _verdict(_done("easy", 45, 36.0, 30, 36.0, cat="walk"))
    assert vs["off_plan"] and st == "off_plan" and comp["level"] == "red"
