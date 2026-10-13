"""
SP-87: the 基礎期's weekly 穩定地形爬升課 (engine/base_climb.py) — one placed easy run of a 越野跑
athlete's base week becomes a 長爬坡有氧 of the same time (steady ground: stairs, 階梯步道, a
treadmill incline; HR ≤ the easy-run cap — an easy session); road athletes, the 專項期 and the
re-entry weeks get none. Synthetic.
"""
import datetime as dt
from datetime import date

from backend.engine import base_climb as BC
from backend.engine import workout_steps as WS
from backend.engine import workout_templates as WT

MON = date(2026, 10, 5)


def d(i, mon=MON):
    return (mon + dt.timedelta(days=i)).isoformat()


def _week(mon=MON, easy=(50, 60, 45)):
    """A placed week: Tue interval, Sat long, easy Wed / Thu / Sun."""
    ss = [{"id": "long", "kind": "long", "day": d(5, mon), "minutes": 150, "tss": 120.0, "title": "LSD",
           "target": "心率 ≤ 145"},
          {"id": "quality", "kind": "quality", "day": d(1, mon), "minutes": 70, "tss": 70.0, "title": "閾值 2×20 分"}]
    for i, (m, day) in enumerate(zip(easy, (2, 3, 6))):
        ss.append({"id": f"easy{i + 1}", "kind": "easy", "day": d(day, mon), "minutes": m, "tss": round(m * 0.9, 1),
                   "title": "輕鬆跑", "target": "心率 ≤ 145", "terrain": "road"})
    ss.append({"id": "strength1", "kind": "strength", "day": d(2, mon), "minutes": 35, "tss": 20.0, "title": "肌力"})
    return ss


def test_week_context_rules():
    on = dict(kind="base", mode="base", monday=MON, road=False)
    assert BC.week_context(**on)["active"]
    assert BC.week_context(**{**on, "mode": "recovery_week"})["active"]          # an easy session: kept
    assert not BC.week_context(**{**on, "road": True})["active"]                 # 路跑: none
    assert not BC.week_context(**{**on, "mode": "reentry"})["active"]
    for kind in ("specific", "taper", "transition", "rebuild", "recovery"):
        assert not BC.week_context(**{**on, "kind": kind, "mode": kind})["active"], kind
    # 跟腱 (SP-270: no hill / climb sessions while it is open)
    heel = {"avoid": {"climb"}, "notes": ["跟腱進行中"], "flat_long": False}
    c = BC.week_context(**on, injury=heel)
    assert not c["active"] and "跟腱" in c["why"]


def test_one_easy_run_becomes_a_long_aerobic_climb_of_the_same_time():
    # SP-442: an A race without a clear descent (10 m/km) keeps the walking-down structure below (no A
    # race ahead now runs down: test_downhill_into_climbs_sp442.py)
    from backend.engine.planning import Event
    flat = Event("e1", "平緩越野", "2027-03-27", kind="race", priority="A", distance_km=30, climbing_m=300,
                 est_hours=4.0)
    info = BC.week_context(kind="base", mode="base", monday=MON, road=False, events=[flat])
    ss, notes = _week(), []
    before = [(x["id"], x["day"], x["minutes"]) for x in ss]
    total_tss = sum(x["tss"] for x in ss)
    BC.apply(ss, info, aet=145.0, notes=notes)
    (c,) = [x for x in ss if x["id"] == BC.ID]
    # the longest easy run (Thu 60′), same day, same minutes, still an easy session
    assert c["day"] == d(3) and c["minutes"] == 60 and c["kind"] == "easy"
    assert [(x["day"], x["minutes"]) for x in ss] == [(dd, m) for _i, dd, m in before]
    assert sum(x["tss"] for x in ss) == total_tss                   # same time, same easy HR
    assert c["title"] == "長爬坡有氧 60′（穩定地形）" and c["terrain"] == "trail"
    assert "階梯步道" in c["detail"] and "跑步機" in c["detail"] and "145" in c["detail"]
    # SP-62's 長爬坡有氧 structure: warm-up, the climb at HR ≤ the easy cap, cool-down — no RPE
    st = c["steps"]
    assert st["origin"] == "template:lib:long_climb"
    items = WS.normalize(st)["items"]
    assert [x["kind"] for x in items] == ["warm", "work", "cool"]
    assert items[1]["target"] == WT.AET and items[1]["dur"]["value"] == (60 - BC.WARM - BC.COOL) * 60
    assert WT.session_role(c) is None and WS.rpe_role(items) is None
    # the climb: the uphill minutes at BC.CLIMB_M_PER_H (推估)
    assert c["climb_m"] == round((60 - BC.WARM - BC.COOL) * BC.CLIMB_M_PER_H / 60.0, -1)
    assert info["planned"] == [{"day": d(3), "minutes": 60, "climb_m": c["climb_m"]}]
    assert notes and notes[0]["src"] == "base_climb" and "長爬坡有氧" in notes[0]["text"]
    # one a week
    BC.apply(ss, info, aet=145.0)
    assert sum(1 for x in ss if x["id"] == BC.ID) == 1


def test_which_easy_run_and_when_none_fits():
    # done / heat / another hook session (技術地形) are never taken; the next longest is
    ss = _week()
    ss[3]["done"] = True                                                # Thu 60′ done
    ss[2]["heat"] = True                                                # Wed 50′ the heat session
    BC.apply(ss, BC.week_context(kind="base", mode="base", monday=MON, road=False), aet=145.0)
    assert next(x for x in ss if x["id"] == BC.ID)["day"] == d(6)      # Sun 45′
    ss = _week()
    ss[3]["id"] = "tech"
    BC.apply(ss, BC.week_context(kind="base", mode="base", monday=MON, road=False), aet=145.0)
    assert next(x for x in ss if x["id"] == BC.ID)["day"] == d(2)      # Wed 50′
    # every easy run shorter than MIN_MINUTES: none, the note says why
    ss, notes = _week(easy=(30, 35, 25)), []
    info = BC.week_context(kind="base", mode="base", monday=MON, road=False)
    BC.apply(ss, info, aet=145.0, notes=notes)
    assert not any(x["id"] == BC.ID for x in ss) and info["planned"] == []
    assert notes and str(BC.MIN_MINUTES) in notes[0]["text"]
    # inactive: untouched
    ss = _week()
    BC.apply(ss, BC.week_context(kind="specific", mode="specific", monday=MON, road=False), aet=145.0)
    assert not any(x["id"] == BC.ID for x in ss)


def test_week_plan_and_projection_trail_base_weeks_only():
    """A trail athlete in the 基礎期: one 長爬坡有氧 in this week and every projected base week (same
    rule in week_plan and the projection); the 專項期 and road athletes none."""
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
    wp = O.week_plan(ds, st, TODAY, sport="trail")
    assert wp["phase"] == "base" and wp["base_climb"]["active"]
    left = [s for s in wp["sessions"] if s["id"] == BC.ID]
    assert len(left) == 1 and left[0]["kind"] == "easy" and left[0]["title"].startswith("長爬坡有氧")
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 8))
    base = [w for w in weeks if w["phase"] == "base" and w["mode"] != "reentry"]
    assert base
    for w in base:
        n = sum(1 for s in w["sessions"] if s["id"] == BC.ID)
        assert n == 1, w["start"]
        assert w["base_climb"]["planned"]
    road = O.week_plan(ds, st, TODAY, sport="road")
    assert not road["base_climb"]["active"] and not any(s["id"] == BC.ID for s in road["sessions"])
    for w in PJ.project_weeks(road, _phases(plan, TODAY), date(2026, 11, 8)):
        assert not any(s["id"] == BC.ID for s in w["sessions"]) and "base_climb" not in w
    # a trail A race 12/5: this week is 專項期 — none
    plan.events = [Event("e1", "越野賽", "2026-12-05", kind="race", priority="A", distance_km=30, climbing_m=2000,
                         est_hours=5.0)]
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY, sport="trail")
    assert wp["phase"] == "specific" and not any(s["id"] == BC.ID for s in wp["sessions"])


def test_a_climb_already_behind_this_week_blocks_a_second_one(monkeypatch):
    """SP-87: once this week's stored 長爬坡有氧 is behind (done, missed, or today's with a run logged)
    the generator — which counts the done run as an easy run — does not turn the next easy run into
    a second one (reconcile then dropped an easy row: the SP-74 known limit)."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine import plan_store as PS
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    from backend.tests.test_quality_gate import TODAY
    assert not BC.week_context(kind="base", mode="base", monday=MON, behind=d(1))["active"]
    assert "已" in BC.week_context(kind="base", mode="base", monday=MON, behind=d(1))["why"]
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = []
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    monday = TODAY - dt.timedelta(days=TODAY.weekday())
    monkeypatch.setattr(PS, "hook_days", lambda m, db_path=None: {BC.ID: (monday + dt.timedelta(days=1)).isoformat()})
    wp = O.week_plan(ds, st, TODAY, sport="trail")
    assert not wp["base_climb"]["active"] and not any(s["id"] == BC.ID for s in wp["sessions"])
    # a stored one later this week (not behind): generated as usual
    monkeypatch.setattr(PS, "hook_days", lambda m, db_path=None: {BC.ID: (monday + dt.timedelta(days=6)).isoformat()})
    wp = O.week_plan(ds, st, TODAY, sport="trail")
    assert sum(1 for s in wp["sessions"] if s["id"] == BC.ID) == 1


def test_hook_days_reads_this_weeks_stored_hook_rows(tmp_path):
    import sqlite3
    from sqlalchemy import create_engine
    from backend.db.models import Base
    from backend.engine import plan_store as PS
    db = tmp_path / "w.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(db)
    for uid, gk, ws, day, state, kind in (("a", "climb_base", "2026-10-05", "2026-10-07", "done", "easy"),
                                          ("b", "downhill", "2026-10-05", "2026-10-09", "active", "easy"),
                                          ("c", "tech", "2026-10-05", "2026-10-08", "deleted", "quality"),
                                          ("d", "easy1", "2026-10-05", "2026-10-06", "done", "easy"),
                                          ("e", "climb_base", "2026-10-12", "2026-10-14", "active", "easy")):
        con.execute("INSERT INTO plan_sessions (athlete_id, uid, week_start, day, kind, title, minutes, origin, edited,"
                    " provisional, state, gen_key, updated_at) VALUES (1, ?, ?, ?, ?, 't', 50, 'auto', 0, 0, ?, ?,"
                    " '2026-10-05')", (uid, ws, day, kind, state, gk))
    con.commit()
    con.close()
    # SP-442: no separate 下坡課 any more — an old stored downhill row is no hook
    assert PS.hook_days(MON, db) == {"climb_base": "2026-10-07"}
    assert PS.hook_days(MON, tmp_path / "missing.db") == {}


def test_the_schedule_swaps_a_long_run_for_a_long_climb_of_the_same_time(monkeypatch):
    """SP-87 (owner: 「我想要爬山，就把 LSD 改成同強度的穩定地形爬坡課表」): PATCH /sessions/{uid}
    {"swap_to": "climb"} on a long run — the same day, minutes, kind (still the week's long run) and
    TSS, now 「長爬坡有氧 N′（穩定地形）」 with its structure and climb; the user's edit (re-planning keeps
    it, COROS gets the new steps). Only a long run; one shorter than BC.MIN_MINUTES is refused."""
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        lng = next(s for s in ss if s["kind"] == "long" and s["day"] == "2026-10-04")
        r = e.c.patch(f"{API}/sessions/{lng['uid']}", json={"swap_to": "climb"})
        assert r.status_code == 200, r.text
        s = r.json()
        assert (s["kind"], s["day"], s["minutes"], s["tss"]) == ("long", "2026-10-04", lng["minutes"], lng["tss"])
        assert s["title"] == f"長爬坡有氧 {lng['minutes']}′（穩定地形）" and s["edited"] is True
        assert s["terrain"] == "trail" and s["climb_m"] == BC.climb_of(lng["minutes"] - BC.WARM - BC.COOL)
        assert "階梯步道" in s["detail"] and s["steps"]["items"][1]["kind"] == "work"
        assert s["coros"] is not None
        assert e.c.get(f"{API}/reconcile").json()["changes"] == []          # the user's edit: kept
        # only a long run
        q = next(x for x in ss if x["kind"] == "quality")
        r = e.c.patch(f"{API}/sessions/{q['uid']}", json={"swap_to": "climb"})
        assert r.status_code == 400 and "長跑" in r.json()["detail"]
        assert e.c.patch(f"{API}/sessions/{lng['uid']}", json={"swap_to": "rocket"}).status_code == 400
        short = e.c.patch(f"{API}/sessions/{lng['uid']}", json={"minutes": 30}).json()
        r = e.c.patch(f"{API}/sessions/{short['uid']}", json={"swap_to": "climb"})
        assert r.status_code == 400 and str(BC.MIN_MINUTES) in r.json()["detail"]
