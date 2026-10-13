"""
SP-442 (owner 2026-10-13): no separate 下坡課 — the downhill goes into the climbing sessions.

  * the planner never makes a standalone downhill session (基礎期 or 專項期; was engine/downhill.py);
  * 基礎期: the weekly 長爬坡有氧 (engine/base_climb.py) runs down instead of walking down — a gentle
    −5～−10 % downhill at RPE 3–5, 10′ the base phase's first week then 15′ (the old 基礎期 downhill dose),
    every week, only when the next A race has a clear descent (≥ 20 m/km, the old rule; no A race
    ahead = a trail athlete between races keeps it); the detail says a treadmill / stairs means walking
    down (that week has no downhill); the structure's downhill step is RPE-set (no HR target, SP-409);
  * 專項期: the 長爬坡反覆 keeps its run-down (SP-409), nothing extra; none in the last 2 weeks / taper;
  * a stored, untouched auto downhill session goes at the next re-plan; an edited one stays.
Synthetic athletes, plans and a tiny synthetic GPX (tmp folder); nothing reads the athlete's data.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import base_climb as BC
from backend.engine import projection as P
from backend.engine import target_policy as TP
from backend.engine import workout_steps as WS
from backend.engine import workout_templates as WT
from backend.engine.planning import Event
from backend.tests.test_b2b import _phases
from backend.tests.test_quality_gate import TODAY
from backend.tests.test_recovery_week import LIGHT, _week

RACE = date(2026, 12, 5)                    # Sat: 賽前第 10 週 on 9/28
FAR = date(2027, 3, 27)                     # 25 weeks out: the 基礎期
MON = date(2026, 10, 5)
FULL = {"cp": 250.0, "lthr": 168.0, "aet": 150.0}


def ev(kind="race", climbing_m=2000, start=FAR):
    return Event(id="e1", name="測試越野", date=start.isoformat(), kind=kind, priority="A", distance_km=30,
                 climbing_m=climbing_m, est_hours=5.0)


def bctx(monday=MON, **kw):
    a = dict(kind="base", mode="base", monday=monday, road=False, events=[ev()], phases=None)
    a.update(kw)
    return BC.week_context(**a)


def d(i, mon=MON):
    return (mon + dt.timedelta(days=i)).isoformat()


def _ss(mon=MON, easy=(50, 60, 45)):
    """A placed base week: Tue interval, Sat long, easy Wed / Thu / Sun."""
    ss = [{"id": "long", "kind": "long", "day": d(5, mon), "minutes": 150, "tss": 120.0, "title": "LSD"},
          {"id": "quality", "kind": "quality", "day": d(1, mon), "minutes": 70, "tss": 70.0, "title": "閾值 2×20 分"}]
    for i, (m, day) in enumerate(zip(easy, (2, 3, 6))):
        ss.append({"id": f"easy{i + 1}", "kind": "easy", "day": d(day, mon), "minutes": m, "tss": round(m * 0.9, 1),
                   "title": "輕鬆跑", "target": "心率 ≤ 145", "terrain": "road"})
    return ss


def _downhill_like(s):
    t = s.get("title") or ""
    return s.get("id") == "downhill" or t.startswith("下坡離心") or t.startswith("緩下坡")


# ---------------------------------------------------------------------- no standalone downhill session

def test_no_standalone_downhill_session_in_the_specific_phase():
    """賽前第 10 週 (a trail race with a clear descent): week_plan and every projected week have no
    downhill session and no 「downhill」 week rule any more."""
    ds, plan, wp = _week(RACE.isoformat(), {4: LIGHT})
    assert wp["phase"] == "specific"
    assert "downhill" not in wp
    assert not any(_downhill_like(s) for s in wp["sessions"])
    weeks = P.project_weeks(wp, _phases(plan, TODAY), RACE, events=plan.events)
    assert any(w["phase"] == "specific" for w in weeks)
    for w in weeks:
        assert "downhill" not in w, w["start"]
        assert not any(_downhill_like(s) for s in w["sessions"]), w["start"]


def test_no_standalone_downhill_session_in_the_base_phase():
    """A trail athlete's 基礎期 (an A race 25 weeks out): no 「緩下坡」 session in any week (was: every even
    ISO week); the week's 長爬坡有氧 runs down instead, every week."""
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    ds = _history(TODAY)
    plan = _plan_with(FAR.isoformat(), 1, TODAY)
    plan.events = [Event("e1", "春季越野", FAR.isoformat(), kind="race", priority="A", distance_km=50,
                         climbing_m=3000, est_hours=8.0)]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY, sport="trail")
    assert wp["phase"] == "base" and "downhill" not in wp
    assert not any(_downhill_like(s) for s in wp["sessions"])
    assert wp["base_climb"]["run_down"]
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 12, 20), events=plan.events)
    base = [w for w in weeks if w["phase"] == "base" and w["mode"] != "reentry"]
    assert len(base) >= 4
    for w in base:
        assert not any(_downhill_like(s) for s in w["sessions"]), w["start"]
        (c,) = [s for s in w["sessions"] if s["id"] == BC.ID]
        assert w["base_climb"]["run_down"], w["start"]
        items = WS.normalize(c["steps"])["items"]
        assert items[-1]["target"]["type"] == "rpe", w["start"]           # every week, not every other one


# ---------------------------------------------------------------------- 基礎期: the 長爬坡有氧 runs down

def test_base_climb_runs_down_when_the_next_a_race_has_a_clear_descent():
    c = bctx()
    assert c["active"] and c["run_down"] and c["down_work"] == BC.DOWN_WORK == 15
    nxt = bctx(MON + dt.timedelta(weeks=1))
    assert nxt["active"] and nxt["run_down"]                                  # every week
    # the base phase's first week: 10′, then 15′ (the old 基礎期 downhill dose)
    ph = [{"kind": "base", "start": MON.isoformat(), "end": (MON + dt.timedelta(weeks=10)).isoformat()}]
    first = bctx(phases=ph)
    assert first["run_down"] and first["down_first"] and first["down_work"] == BC.DOWN_FIRST_WORK == 10
    later = bctx(MON + dt.timedelta(weeks=1), phases=ph)
    assert not later["down_first"] and later["down_work"] == 15
    # a race without a clear descent (10 m/km) or a road race: the climb stays, walking down
    flat = bctx(events=[ev(climbing_m=300)])
    assert flat["active"] and not flat["run_down"] and "不明顯" in flat["down_why"]
    road_race = bctx(events=[ev(kind="road", climbing_m=0)])
    assert road_race["active"] and not road_race["run_down"]
    # no A race ahead (a trail athlete between races): kept, as the old base-phase downhill dose
    free = bctx(events=[])
    assert free["active"] and free["run_down"]
    # 賽前 2 週內: walking down
    near = bctx(events=[ev(start=MON + dt.timedelta(days=12))])
    assert not near["run_down"] and "2 週" in near["down_why"]
    # an injury rule avoiding downhill only: walking down (膝前痛／髂脛束 avoid the climb too: no session)
    knee = {"avoid": {"downhill"}, "notes": ["膝前痛進行中"], "flat_long": False}
    hurt = bctx(injury=knee)
    assert hurt["active"] and not hurt["run_down"] and "膝前痛" in hurt["down_why"]


def test_run_down_structure_text_and_same_time():
    info = bctx()
    ss, notes = _ss(), []
    before = [(x["id"], x["day"], x["minutes"]) for x in ss]
    total_tss = sum(x["tss"] for x in ss)
    BC.apply(ss, info, aet=145.0, notes=notes)
    (c,) = [x for x in ss if x["id"] == BC.ID]
    # the longest easy run (Thu 60′), the same day, minutes and TSS: the week's total is unchanged
    assert c["day"] == d(3) and c["minutes"] == 60 and c["kind"] == "easy"
    assert [(x["day"], x["minutes"]) for x in ss] == [(dd, m) for _i, dd, m in before]
    assert sum(x["tss"] for x in ss) == total_tss
    # 下坡 stays out of the title: target_policy keeps it a long climb (no HR / power on the watch)
    assert c["title"] == "長爬坡有氧 60′（跑下山 15′）" and "下坡" not in c["title"]
    assert TP.session_type(c) == "climb"
    assert "−5～−10%" in c["detail"] and "RPE 3–5" in c["detail"]
    assert "在跑步機或樓梯就走下來，這週不算下坡" in c["detail"]
    items = WS.normalize(c["steps"])["items"]
    assert [x["kind"] for x in items] == ["warm", "work", "work"]
    assert items[0]["dur"]["value"] == BC.WARM * 60
    assert items[1]["target"] == WT.AET and items[1]["dur"]["value"] == (60 - BC.WARM - 15) * 60
    down = items[2]
    assert down["dur"]["value"] == 15 * 60
    assert down["target"]["type"] == "rpe" and (down["target"]["lo"], down["target"]["hi"]) == (3, 5)
    assert down["target"].get("down") == 150 and "下坡用跑的" in (down.get("note") or "")
    assert WT.session_role(c) == "easy"                                      # RPE 3–5: still an easy session
    assert c["climb_m"] == BC.climb_of(60 - BC.WARM - 15)
    assert info["planned"] == [{"day": d(3), "minutes": 60, "climb_m": c["climb_m"], "down_min": 15}]
    assert notes and notes[0]["src"] == "base_climb" and "跑下山" in notes[0]["text"]


def test_run_down_push_has_no_hr_target_on_the_descent():
    """The watch: 暖身 → 上坡 → 下坡 (RPE: no HR / power target, its name says the RPE); the whole
    session's time; the structure's TSS stays close to the card (the RPE gives the descent an IF)."""
    from backend.sync import coros_workouts as CW
    info = bctx()
    ss = _ss()
    BC.apply(ss, info, aet=150.0)
    c = next(x for x in ss if x["id"] == BC.ID)
    st = CW.session_steps(c, CW.Thresholds.of(FULL))
    assert len(st) == 3 and all(isinstance(x, CW.Step) for x in st)
    assert st[0].kind == CW.EX_WARMUP
    assert sum(x.seconds for x in st) == 60 * 60
    assert st[2].seconds == 15 * 60 and st[2].intensity is None and "RPE" in st[2].name
    t = WS.totals(WS.normalize(c["steps"]), WS.session_ctx(c, FULL))
    assert t["sec"] == 60 * 60
    assert t["tss"] == pytest.approx(c["tss"], rel=0.15)


def test_walking_down_keeps_the_old_structure():
    """A race without a clear descent: the 長爬坡有氧 as before (warm-up, climb, walking down)."""
    info = bctx(events=[ev(climbing_m=300)])
    ss = _ss()
    BC.apply(ss, info, aet=145.0)
    c = next(x for x in ss if x["id"] == BC.ID)
    assert c["title"] == "長爬坡有氧 60′（穩定地形）"
    items = WS.normalize(c["steps"])["items"]
    assert [x["kind"] for x in items] == ["warm", "work", "cool"]
    assert items[1]["dur"]["value"] == (60 - BC.WARM - BC.COOL) * 60
    assert "下坡用走的" in c["detail"]
    assert info["planned"][0].get("down_min") is None


def test_run_down_fits_a_short_run_and_never_lands_within_14_days_of_the_race():
    # a 45′ run: the climb keeps ≥ MIN_WORK, the descent shrinks to 15′ … ≥ 10′
    info = bctx()
    ss = _ss(easy=(45, 40, 40))
    BC.apply(ss, info, aet=145.0)
    c = next(x for x in ss if x["id"] == BC.ID)
    items = WS.normalize(c["steps"])["items"]
    assert c["minutes"] == 45 and items[1]["dur"]["value"] >= BC.MIN_WORK * 60
    assert items[2]["dur"]["value"] == (45 - BC.WARM - BC.MIN_WORK) * 60 == 15 * 60
    ss = _ss(easy=(40, 35, 30))
    BC.apply(ss, bctx(), aet=145.0)
    c = next(x for x in ss if x["id"] == BC.ID)
    assert WS.normalize(c["steps"])["items"][2]["dur"]["value"] == 10 * 60
    # the race 16 days after Monday (賽前第 3 週): Thursday is 13 days out — walking down, the note says why
    race = MON + dt.timedelta(days=16)
    info = bctx(events=[ev(start=race)])
    assert info["run_down"]
    ss, notes = _ss(), []
    BC.apply(ss, info, aet=145.0, notes=notes)
    c = next(x for x in ss if x["id"] == BC.ID)
    assert c["day"] == d(3) and c["title"].endswith("（穩定地形）")
    assert any("14 天" in n["text"] for n in notes)


def test_walks_down_in_the_real_taper_on_post_race_days_and_after_a_b2b():
    """The old downhill day rules, now on the 長爬坡有氧's day: walking down in the race's real 減量期
    (planning.taper_start), on a race's post-race days and on the easy days after a B2B."""
    for extra, word in (({"taper_start": d(3)}, "減量期"), ({"post_race": [d(3)]}, "賽後")):
        ss, notes = _ss(), []
        BC.apply(ss, {**bctx(), **extra}, aet=145.0, notes=notes)
        c = next(x for x in ss if x["id"] == BC.ID)
        assert c["day"] == d(3) and c["title"].endswith("（穩定地形）"), extra
        assert any(word in n["text"] for n in notes), extra
    ss, notes = _ss(), []
    BC.apply(ss, bctx(), aet=145.0, notes=notes, b2b={"post": {"until": d(4)}})
    assert next(x for x in ss if x["id"] == BC.ID)["title"].endswith("（穩定地形）")
    assert any("連續長天" in n["text"] for n in notes)


def test_post_race_days_of_a_b_race_with_a_big_downhill(monkeypatch):
    """A B race with a big downhill for the athlete on the Thursday before the week: its easy / flat
    days reach into the week (engine/post_race.py) — the week's post_race days (walking down there)."""
    from backend.engine import planning as PL
    monkeypatch.setattr(PL, "RACE_DOWNHILL_OF", lambda e: 30.0)
    monkeypatch.setattr(PL, "ATHLETE_DOWNHILL_OF", lambda d: 10.0)
    b = Event(id="b1", name="B 越野", date=(MON - dt.timedelta(days=4)).isoformat(), kind="race", priority="B",
              distance_km=21, climbing_m=1500, est_hours=3.5)
    info = bctx(events=[ev(), b])
    rp = PL.recovery_plan(b, b=True)
    assert rp["downhill"]["big"]
    last = max(rp["days"], PL.DOWNHILL_FLAT_DAYS)
    assert info["post_race"] == [(b.end + dt.timedelta(days=k)).isoformat() for k in range(1, last + 1)
                                 if b.end + dt.timedelta(days=k) >= MON]
    assert info["post_race"]


# ---------------------------------------------------------------------- 專項期: the 長爬坡反覆's own descent

@pytest.fixture
def store(tmp_path, monkeypatch):
    from backend.engine import event_gpx as EG
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    EG._memo.clear()
    return tmp_path


def test_specific_phase_climb_repeats_keep_their_descent_none_in_the_last_2_weeks(store):
    """A built athlete 6 weeks before an A trail race with a GPX: the 長爬坡反覆 still runs down (its
    derived structure, SP-409), no extra downhill session, and no 長爬坡反覆 in the last 14 days / the
    taper (specific_phase: 賽前第 10–3 週 only, never ≤ 7 days out; overview.taper_rules: none in the
    taper)."""
    from backend.engine import event_gpx as EG
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    from backend.tests.test_specific_phase import GPX, _ev
    EG.save("e1", GPX, "hehuan.gpx")
    race = date(2026, 11, 7)
    ds = _history(TODAY)
    plan = _plan_with("2026-12-05", 1, TODAY)
    plan.events = [_ev()]
    ds.plan = plan
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    weeks = [{"start": wp["week"]["start"], "phase": wp["phase"], "sessions": wp["sessions"]}] + \
        P.project_weeks(wp, _phases(plan, TODAY), race, events=plan.events)
    climbs = [s for w in weeks for s in w["sessions"] if s["id"] == "climb"]
    assert climbs
    for s in climbs:
        assert (race - date.fromisoformat(s["day"])).days >= 14, s["day"]
        items = WS.derive(s, FULL)["items"]
        (rep,) = [x for x in items if x["kind"] == "repeat"]
        assert "下坡" in rep["items"][1]["note"] and rep["items"][1]["target"]["type"] == "rpe"
    for w in weeks:
        assert not any(_downhill_like(s) for s in w["sessions"]), w["start"]
    assert not any(s["id"] == "climb" for w in weeks if w["phase"] == "taper" for s in w["sessions"])


# ---------------------------------------------------------------------- the stored plan

def _row(uid, gen_key, day, title, edited=False, minutes=50):
    return {"uid": uid, "week_start": "2026-10-12", "gen_key": gen_key, "day": day, "kind": "easy", "title": title,
            "minutes": minutes, "target": "", "detail": "", "source": "", "tss": 40.0, "terrain": "trail",
            "origin": "auto", "edited": edited, "provisional": False, "state": "active", "done_by": None,
            "note": None, "steps": None}


def _gen(i, day, minutes=50):
    return {"id": i, "kind": "easy", "title": "輕鬆跑", "minutes": minutes, "day": day, "tss": 40.0, "target": "",
            "detail": "", "source": "", "terrain": "road"}


def test_reconcile_drops_an_untouched_stored_downhill_session_and_keeps_an_edited_one():
    from backend.engine import reconcile as R
    stored = [_row("a", "easy1", "2026-10-13", "輕鬆跑"), _row("b", "downhill", "2026-10-15", "緩下坡（下坡 15′）")]
    gen = [{"start": "2026-10-12", "mode": "base", "provisional": False,
            "sessions": [_gen("easy1", "2026-10-13"), _gen("easy2", "2026-10-15")]}]
    out, changes = R.reconcile(stored, gen, [], "2026-10-12")
    live = [s for s in out if s["state"] == "active"]
    assert not any(s["gen_key"] == "downhill" for s in live)
    assert any(c["action"] == "removed" and c["uid"] == "b" for c in changes)
    assert any(s["gen_key"] == "easy2" and s["day"] == "2026-10-15" for s in live)
    # the user's edited one stays as it is
    stored = [_row("a", "easy1", "2026-10-13", "輕鬆跑"),
              _row("b", "downhill", "2026-10-15", "下坡離心（下坡 20′）", edited=True)]
    out, changes = R.reconcile(stored, gen, [], "2026-10-12")
    kept = next(s for s in out if s["uid"] == "b")
    assert kept["state"] == "active" and kept["title"] == "下坡離心（下坡 20′）" and kept["day"] == "2026-10-15"
    assert not any(c["uid"] == "b" for c in changes)
