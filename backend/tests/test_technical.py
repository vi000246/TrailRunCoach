"""
SP-74: 技術地形課 in the generated plan (engine/technical.py) — 基礎期 every other week's LSD as a
low-RPE technical run, 專項期 one session a week as a quality session (RPE 6–7: 48 h spacing, the
week's 20 % budget) or, without room, an easy one (RPE 4–5); road athletes get none. Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import technical as T
from backend.engine import workout_steps as WS
from backend.engine import workout_templates as WT

MON = date(2026, 10, 5)            # ISO week 41 (odd)
EVEN = date(2026, 10, 12)          # ISO week 42


def d(i, mon=MON):
    return (mon + dt.timedelta(days=i)).isoformat()


def test_week_context_rules():
    on = dict(kind="specific", mode="specific", monday=MON, road=False)
    assert T.week_context(**on)["active"]
    assert not T.week_context(**{**on, "road": True})["active"]                    # 路跑: none
    assert not T.week_context(**{**on, "mode": "recovery_week"})["active"]
    assert not T.week_context(**{**on, "mode": "reentry"})["active"]
    assert not T.week_context(**{**on, "kind": "taper", "mode": "taper"})["active"]
    assert not T.week_context(**{**on, "kind": "transition", "mode": "transition"})["active"]
    base = dict(kind="base", mode="base", road=False)
    assert T.week_context(**base, monday=EVEN)["active"]
    assert not T.week_context(**base, monday=MON)["active"]                        # every other week
    assert not T.week_context(**base, monday=EVEN, b2b={"due": True})["active"]


def _week(mon=MON, q_day=1, long_min=150, easy=(50, 60, 50), q_title="閾值 2×20 分"):
    ss = [{"id": "long", "kind": "long", "day": d(5, mon), "minutes": long_min, "tss": 120.0, "title": "LSD"},
          {"id": "quality", "kind": "quality", "day": d(q_day, mon), "minutes": 70, "tss": 70.0, "title": q_title}]
    for i, (m, day) in enumerate(zip(easy, (2, 3, 6))):
        ss.append({"id": f"easy{i + 1}", "kind": "easy", "day": d(day, mon), "minutes": m, "tss": m * 0.9,
                   "title": "輕鬆跑"})
    ss.append({"id": "strength1", "kind": "strength", "day": d(2, mon), "minutes": 35, "tss": 20.0, "title": "肌力"})
    return ss


def test_base_long_becomes_low_rpe_technical_of_the_same_time():
    info = T.week_context(kind="base", mode="base", monday=EVEN, road=False)
    ss, notes = _week(EVEN), []
    T.apply(ss, info, hours=7.0, rates={"trail": 60.0}, notes=notes)
    s = next(x for x in ss if x["id"] == "long")
    assert s["kind"] == "long" and s["minutes"] == 150 and s["day"] == d(5, EVEN)   # same slot, same time
    assert s["title"] == "技術地形 150′（低 RPE 3–4）" and s["terrain"] == "trail" and s["target"] == ""
    assert WS.rpe_role(s["steps"]["items"]) == "easy" and WT.session_role(s) == "easy"
    assert WS.normalize(s["steps"])["items"][1]["target"]["type"] == "rpe"
    assert s["climb_m"] and s["tss"] == pytest.approx(150.0)
    assert info["planned"][0]["replaces"] == "long"
    assert any(n["src"] == "technical" and "隔週" in n["text"] for n in notes)
    # done / already technical: untouched
    done = _week(EVEN)
    done[0]["done"] = True
    T.apply(done, T.week_context(kind="base", mode="base", monday=EVEN, road=False))
    assert done[0]["title"] == "LSD"


def test_specific_quality_role_spaced_and_within_the_budget():
    info = T.week_context(kind="specific", mode="specific", monday=MON, road=False)
    ss, notes = _week(), []
    before = sum(x["minutes"] for x in ss if x["kind"] != "strength")
    T.apply(ss, info, hours=8.0, rates={"trail": 60.0}, notes=notes)
    s = next(x for x in ss if x["id"] == "tech")
    assert s["kind"] == "hike" and WT.session_role(s) == "quality"                 # RPE 6–7
    day = date.fromisoformat(s["day"])
    for h in (date.fromisoformat(d(1)), date.fromisoformat(d(5))):                 # quality Tue, long Sat
        assert abs((day - h).days) >= 2
    # 20 % of 8 h = 96 min − the interval's 40 → 56 → 55′ of RPE 6–7 work
    work = s["steps"]["items"][1]["dur"]["value"] / 60
    assert work == 55 and s["minutes"] == 55 + 25
    assert sum(x["minutes"] for x in ss if x["kind"] != "strength") == before     # other easy runs gave it
    assert all(x["minutes"] >= T.EASY_MIN for x in ss if x["kind"] == "easy")
    assert info["planned"][0]["role"] == "quality" and "強度預算" in notes[0]["text"]
    # a big week: capped at the template's 90′
    big = _week()
    T.apply(big, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=14.0)
    assert next(x for x in big if x["id"] == "tech")["steps"]["items"][1]["dur"]["value"] == 90 * 60


def test_specific_without_budget_or_spacing_is_kept_easy():
    # the intervals fill the 20 %: 20 % of 4 h = 48 − 40 → 8 < SPEC_WORK_MIN
    ss, notes = _week(), []
    info = T.week_context(kind="specific", mode="specific", monday=MON, road=False)
    T.apply(ss, info, hours=4.0, notes=notes)
    s = next(x for x in ss if x["id"] == "tech")
    assert WT.session_role(s) == "easy" and s["title"].endswith("（RPE 4–5）")
    assert "強度預算不夠" in notes[0]["text"] and info["planned"][0]["role"] == "easy"
    # quality on Thu: no easy day ≥ 2 days from it and the Sat long run
    ss, notes = _week(q_day=3), []
    T.apply(ss, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=8.0, notes=notes)
    s = next(x for x in ss if x["id"] == "tech")
    assert WT.session_role(s) == "easy" and "≥ 2 天" in notes[0]["text"]
    # a hard run already done on Wed (hard_done) blocks Wed too
    ss = _week()
    T.apply(ss, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=8.0,
            hard_done=[date.fromisoformat(d(3))])
    s = next(x for x in ss if x["id"] == "tech")
    assert WT.session_role(s) == "easy"


def test_specific_respects_the_weekday_cap():
    from backend.engine import plan_prefs as PP
    # weekday cap 60: Thu's work = 60 − 15 − 10 = 35′ (the budget would allow 55′)
    ss = _week()
    T.apply(ss, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=8.0,
            prefs=PP.Prefs(cap_weekday=60, cap_long=200))
    s = next(x for x in ss if x["id"] == "tech")
    assert s["day"] == d(3) and s["minutes"] == 60 and WT.session_role(s) == "quality"
    # cap 50: < 30′ of work fits → kept easy on the easy run's time, the note says why
    ss, notes = _week(), []
    T.apply(ss, T.week_context(kind="specific", mode="specific", monday=MON, road=False), hours=8.0,
            prefs=PP.Prefs(cap_weekday=50, cap_long=200), notes=notes)
    s = next(x for x in ss if x["id"] == "tech")
    assert WT.session_role(s) == "easy" and "時間上限" in notes[0]["text"]


def test_week_plan_and_projection_trail_vs_road():
    """A trail athlete: 基礎期 even ISO weeks' LSD is technical, odd weeks plain, in week_plan and
    in the projection alike; 專項期 one tech session a week; road athletes none."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import projection as PJ
    from backend.engine.planning import Event
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = []
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY, sport="trail")                    # week of 9/28 = ISO 40 (even)
    lg = next(s for s in wp["sessions"] if s["id"] == "long")
    assert lg["title"].startswith("技術地形") and lg["steps"] and wp["technical"]["planned"]
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8))
    for w in weeks:
        long_s = next((s for s in w["sessions"] if s["id"] == "long"), None)
        if long_s is None:
            continue
        even = date.fromisoformat(w["start"]).isocalendar()[1] % 2 == 0
        assert long_s["title"].startswith("技術地形") == even, w["start"]
    road = O.week_plan(ds, st, TODAY, sport="road")
    assert not road["technical"]["active"] and not any("技術地形" in s["title"] for s in road["sessions"])
    for w in PJ.project_weeks(road, _phases(plan, TODAY), date(2026, 11, 8)):
        assert not any("技術地形" in s["title"] for s in w["sessions"]) and "technical" not in w
    # 專項期 (a trail A race 12/5): one tech session in each 專項期 build week, never two
    plan.events = [Event("e1", "越野賽", "2026-12-05", kind="race", priority="A", distance_km=30, climbing_m=2000,
                         est_hours=5.0)]
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY, sport="trail")
    assert wp["phase"] == "specific"
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8))
    built = [w for w in weeks if w["mode"] == "specific"]
    assert built and all(sum(1 for s in w["sessions"] if s["id"] == "tech") == 1 for w in built)
    for w in built:
        t = next(s for s in w["sessions"] if s["id"] == "tech")
        if WT.session_role(t) == "quality":
            hard = [date.fromisoformat(s["day"]) for s in w["sessions"] if s["kind"] in ("quality", "long")]
            assert all(abs((date.fromisoformat(t["day"]) - h).days) >= 2 for h in hard)
        assert any(n.get("src") == "technical" for n in w["notes"])
    assert any(WT.session_role(next(s for s in w["sessions"] if s["id"] == "tech")) == "quality" for w in built)


# ---- SP-74 follow-up: the user's own RPE ≥ 7 技術地形 session counts in the 20 % ---------------

def _user_row(day, work=60, rpe=(6, 7), uid="u1", origin="custom", edited=False, state="active", kind="hike"):
    b = WT.B()
    items = [b.warm(15, "暖身"), b.t("work", work * 60, WT.rpe(rpe[0], rpe[1], up=300), "技術路段"),
             b.t("cool", 600, WT.OPEN, "收操")]
    return {"uid": uid, "day": day, "state": state, "kind": kind, "title": f"技術地形 {work + 25}′",
            "minutes": work + 25, "origin": origin, "edited": edited, "gen_key": None,
            "steps": WS.doc(items, origin="template:lib:tech_hard")}


def test_user_quality_counts_rpe_7_work_of_the_week_only():
    rows = [_user_row(d(3)), _user_row(d(4), rpe=(4, 5), uid="u2"), _user_row(d(9), uid="u3"),
            _user_row(d(5), work=45, uid="u4", state="done")]
    u = T.user_quality(rows, MON)
    assert [(x["uid"], x["work"]) for x in u] == [("u1", 60.0), ("u4", 45.0)]   # RPE 4–5 / next week: out
    n = T.user_note(u, 8.0, "specific")
    assert n["src"] == "technical" and "105 分" in n["text"] and "不另外排" in n["text"]
    assert "不另外排" not in T.user_note(u, 8.0, "base")["text"]
    # a distance step has no time: the whole session counts (推估)
    b = WT.B()
    r = {**_user_row(d(3)), "minutes": 80,
         "steps": WS.doc([b.d("work", 8000, WT.rpe(6, 7, up=300), "技術路段")], origin="user")}
    assert T.user_work_min(r) == 80.0


def test_user_session_blocks_the_generated_one_in_the_specific_phase():
    ss, notes = _week(), []
    info = T.week_context(kind="specific", mode="specific", monday=MON, road=False)
    T.apply(ss, info, hours=8.0, notes=notes, user=T.user_quality([_user_row(d(3))], MON))
    assert not any(x.get("id") == "tech" for x in ss) and info["planned"] == [] and info["user"]


def _specific_plan(monkeypatch, rows):
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.planning import Event
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    from backend.tests.test_quality_gate import TODAY
    monkeypatch.setattr(T, "load_user", lambda: list(rows))
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = [Event("e1", "越野賽", "2026-12-05", kind="race", priority="A", distance_km=30, climbing_m=2000,
                         est_hours=5.0)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return plan, O.week_plan(ds, st, TODAY, sport="trail")


def _tiz(ss):
    from backend.engine.overview import session_tiz_min
    return sum(session_tiz_min(s) for s in ss if s["kind"] == "quality")


def test_week_plan_and_projection_budget_the_user_session(monkeypatch):
    from backend.engine import projection as PJ
    from backend.engine import quality_gate as QG
    from backend.tests.test_b2b import _phases
    from backend.tests.test_quality_gate import TODAY
    plan, base = _specific_plan(monkeypatch, [])
    assert base["phase"] == "specific"
    week_hours = base["target"]["hours"]
    # this week (9/28) and next (10/5): a user's RPE 6–7 session with 60′ of work each
    rows = [_user_row("2026-10-03", uid="a"), _user_row("2026-10-10", uid="b")]
    plan, wp = _specific_plan(monkeypatch, rows)
    assert not any(s["id"] == "tech" for s in wp["sessions"])                   # theirs is this week's
    assert _tiz(wp["sessions"]) <= QG.QUALITY_SHARE_MAX * week_hours * 60 - 60 + 1e-6
    assert any(n.get("src") == "technical" and "你排的技術地形課" in n["text"] for n in wp["notes"])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18))
    w = next(x for x in weeks if x["start"] == "2026-10-05")
    assert w["mode"] == "specific" and not any(s["id"] == "tech" for s in w["sessions"])
    assert _tiz(w["sessions"]) <= QG.QUALITY_SHARE_MAX * w["hours"] * 60 - 60 + 1e-6
    assert any("你排的技術地形課" in n["text"] for n in w["notes"])
    # the week after has none of the user's: the generated one is back
    w2 = next(x for x in weeks if x["start"] == "2026-10-12")
    assert sum(1 for s in w2["sessions"] if s["id"] == "tech") == 1


def test_user_session_taking_the_whole_budget_leaves_no_interval(monkeypatch):
    from backend.engine import projection as PJ
    from backend.tests.test_b2b import _phases
    from backend.tests.test_quality_gate import TODAY
    rows = [_user_row("2026-10-03", work=300, uid="a"), _user_row("2026-10-10", work=300, uid="b")]
    plan, wp = _specific_plan(monkeypatch, rows)
    assert not any(s["kind"] == "quality" for s in wp["sessions"])
    assert any(n.get("src") == "quality_share" and "這週不排" in n["text"] for n in wp["notes"])
    w = next(x for x in PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18)) if x["start"] == "2026-10-05")
    assert not any(s["kind"] == "quality" for s in w["sessions"])               # no fallback interval either


def test_user_rpe_rows_reads_the_users_own_structured_sessions(tmp_path):
    import json
    import sqlite3
    from sqlalchemy import create_engine
    from backend.db.models import Base
    from backend.engine import plan_store as PS
    db = tmp_path / "w.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()
    steps = json.dumps(_user_row(d(3))["steps"], ensure_ascii=False)
    con = sqlite3.connect(db)
    for uid, origin, edited, state, st in (("c", "custom", 0, "active", steps), ("e", "auto", 1, "done", steps),
                                           ("a", "auto", 0, "active", steps), ("x", "custom", 0, "deleted", steps),
                                           ("n", "custom", 0, "active", None)):
        con.execute("INSERT INTO plan_sessions (athlete_id, uid, week_start, day, kind, title, minutes, origin, edited,"
                    " provisional, state, steps, updated_at) VALUES (1, ?, '2026-10-05', '2026-10-08', 'hike',"
                    " '技術地形 85′', 85, ?, ?, 0, ?, ?, '2026-10-05')", (uid, origin, edited, state, st))
    con.commit()
    con.close()
    got = PS.user_rpe_rows(db)
    assert sorted(r["uid"] for r in got) == ["c", "e"]       # custom / edited, active / done, with steps
    assert [x["uid"] for x in T.user_quality(got, MON)] == ["c", "e"]
    assert T.user_stamp(got) and PS.user_rpe_rows(tmp_path / "missing.db") == []
