"""
SP-270 依傷別迴避課型 (engine/injuries.condition_rule / condition_week, overview.condition_apply, the
downhill / technical / steep_hill modules, week_plan, projection): while a 膝前痛 / 髂脛束 event is open
no downhill session, no 技術地形, the trail long run becomes flat; while a 跟腱 event is open no hill
repeats, steep walk or strides (推估); 足底筋膜 / 其他 / no condition change nothing. The rule ends on
the day the event resolves; an avoided session's time stays as easy running (the week's volume holds).
Synthetic data only (docs/research/injury-graded-return.md §4.2, §4.5, §6.1 points 3–4).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict
from datetime import date

import pytest

from backend.engine import downhill as DH
from backend.engine import injuries as INJ
from backend.engine import overview as O
from backend.engine import projection as P
from backend.engine import steep_hill as SH
from backend.engine import technical as TECH
from backend.tests.test_b2b import _phases
from backend.tests.test_injuries import ev
from backend.tests.test_quality_gate import TODAY
from backend.tests.test_recovery_week import LIGHT, _week

MON = TODAY - dt.timedelta(days=TODAY.weekday())               # 2026-09-28
PFP_NOTE = "膝前痛進行中：先不排下坡課（Esculier 2016）"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    INJ._memo.clear()
    yield
    INJ._memo.clear()


def cev(id, onset, condition, area=None, **kw):
    return {**ev(id, onset, area=area or INJ.CONDITION_AREA.get(condition) or "hip", **kw), "condition": condition}


# ---------------------------------------------------------------------------
# the rule
# ---------------------------------------------------------------------------

def test_rule_by_condition():
    r = INJ.condition_rule([cev(1, "2026-09-20", "pfp", side="right")], TODAY)
    assert r["avoid"] == {"downhill", "technical", "climb"} and r["flat_long"]
    assert r["notes"] == ["右膝・" + PFP_NOTE + "；技術地形、長爬坡反覆也先不排，越野長跑改平路"]
    r = INJ.condition_rule([cev(1, "2026-09-20", "itb")], TODAY)
    assert "downhill" in r["avoid"] and r["flat_long"] and "Fredericson 2005" in r["notes"][0]
    r = INJ.condition_rule([cev(1, "2026-09-20", "achilles")], TODAY)
    assert r["avoid"] == {"climb", "steep", "me", "strides"} and not r["flat_long"]
    assert "推估" in r["notes"][0] and "長跑地形不變" in r["notes"][0]
    for c in ("plantar_fascia", "other", None):
        assert INJ.condition_rule([cev(1, "2026-09-20", c)], TODAY) is None
    # an illness never takes a condition rule
    assert INJ.condition_rule([{**cev(1, "2026-09-20", "pfp"), "category": "illness"}], TODAY) is None


def test_rule_ends_the_day_the_event_resolves():
    e = [cev(1, "2026-09-20", "pfp", status="resolved", resolved="2026-09-30")]
    assert INJ.condition_rule(e, date(2026, 9, 29)) is not None
    assert INJ.condition_rule(e, date(2026, 9, 30)) is None                  # 「事件結束的那一天起」
    assert INJ.condition_rule(e, date(2026, 9, 19)) is None                  # before the onset


def test_modules_ask_the_rule():
    knee = INJ.condition_rule([cev(1, "2026-09-20", "pfp")], TODAY)
    heel = INJ.condition_rule([cev(1, "2026-09-20", "achilles")], TODAY)
    d = DH.week_context(kind="specific", mode="specific", monday=MON, events=[], injury=knee)
    assert not d["active"] and PFP_NOTE in d["why"]
    t = TECH.week_context(kind="base", mode="base", monday=MON, road=False, injury=knee)
    assert not t["active"] and PFP_NOTE in t["why"]
    assert TECH.week_context(kind="base", mode="base", monday=MON, road=False, injury=heel).get("why") != t["why"]
    trip = {"id": "t", "name": "百岳", "start": "2026-11-20", "kind": "baiyue", "days": 3, "pack_kg": 12}
    s0 = SH.week_context(kind="specific", mode="specific", monday=date(2026, 9, 28), event=trip, weight=65.0)
    s1 = SH.week_context(kind="specific", mode="specific", monday=date(2026, 9, 28), event=trip, weight=65.0,
                         injury=heel)
    assert s0["active"] and not s1["active"] and any("跟腱進行中" in w for w in s1["why"])
    s2 = SH.week_context(kind="specific", mode="specific", monday=date(2026, 9, 28), event=trip, weight=65.0,
                         injury=knee)
    assert s2["active"] == s0["active"]                                     # 膝: the steep walk stays


# ---------------------------------------------------------------------------
# condition_apply on a week's sessions
# ---------------------------------------------------------------------------

def _s(id, kind, day, minutes, **kw):
    return O.Session(id=id, kind=kind, title=kw.pop("title", id), minutes=minutes, day=day, tss=minutes * 0.8, **kw)


def _week_ss():
    return [_s("easy1", "easy", "2026-09-29", 50, title="輕鬆跑" + O.ROAD_STRIDES[0],
               detail="心率不超過 142" + O.ROAD_STRIDES[1], source=O.SRC_UA + O.ROAD_STRIDES[2]),
            _s("downhill", "easy", "2026-09-30", 50, title="下坡離心（下坡 20′）", terrain="trail", steps={"v": 1}),
            _s("climb", "easy", "2026-10-01", 60, title="長爬坡反覆", terrain="trail"),
            _s("steep", "easy", "2026-10-02", 60, title="陡坡健走", terrain="trail"),
            _s("strength1", "strength", "2026-10-01", 35),
            _s("long", "long", "2026-10-03", 150, title="LSD（山路）", detail="有山路就走山路，陡坡用走的；全程心率壓在…"),
            _s("done", "easy", "2026-09-28", 40, done=True, title="下坡離心", terrain="trail")]


def _run_min(ss):
    return sum(s.minutes for s in ss if s.kind in O.RUN_KINDS)


def test_knee_week():
    notes: list = []
    ss = O.condition_apply(_week_ss(), [cev(1, "2026-09-20", "pfp")], notes)
    by = {s.title: s for s in ss}
    assert not any(s.id in ("downhill", "climb") for s in ss if not s.done)
    assert sum(1 for s in ss if s.title == "輕鬆跑" and s.terrain == "road" and s.steps is None) == 2
    assert next(s for s in ss if s.id == "steep").title == "陡坡健走"           # not avoided for the knee
    lng = next(s for s in ss if s.kind == "long")
    assert lng.terrain == "road" and lng.title == "LSD" and lng.detail.startswith("平路或緩坡")
    assert O.ROAD_STRIDES[0] in next(s for s in ss if s.id == "easy1").title  # strides stay for the knee
    assert _run_min(ss) == _run_min(_week_ss())                              # the week's volume holds
    assert next(s for s in ss if s.id == "done").title == "下坡離心"           # done: never touched
    assert any(PFP_NOTE in n["text"] and n["src"] == "injury_condition" for n in notes)
    assert "輕鬆跑" in by


def test_achilles_week():
    notes: list = []
    ss = O.condition_apply(_week_ss(), [cev(1, "2026-09-20", "achilles")], notes)
    e1 = next(s for s in ss if s.day == "2026-09-29")
    assert e1.title == "輕鬆跑" and "加速跑" not in e1.detail and "strides" not in e1.source
    assert not any(s.id in ("climb", "steep") for s in ss)
    assert next(s for s in ss if s.id == "downhill").title.startswith("下坡離心")   # downhill: not the heel's rule
    lng = next(s for s in ss if s.kind == "long")
    assert lng.title == "LSD（山路）" and lng.terrain is None                      # 長跑地形不變
    assert _run_min(ss) == _run_min(_week_ss())
    assert any("跟腱進行中" in n["text"] and "推估" in n["text"] for n in notes)
    me = [{"id": "me", "kind": "quality", "title": "ME 負重爬坡", "minutes": 70, "day": "2026-10-01", "tss": 60.0,
           "done": False, "climb_m": 600.0}]
    O.condition_apply(me, [cev(1, "2026-09-20", "achilles")])
    assert me[0]["kind"] == "easy" and me[0]["climb_m"] is None and me[0]["minutes"] == 70


def test_plantar_other_and_no_condition_change_nothing():
    for evs in ([cev(1, "2026-09-20", "plantar_fascia")], [cev(1, "2026-09-20", "other")],
                [ev(1, "2026-09-20")], []):
        ss = _week_ss()
        before = [asdict(s) for s in ss]
        notes: list = []
        assert O.condition_apply(ss, evs, notes) is ss
        assert [asdict(s) for s in ss] == before and notes == []


def test_mid_week_resolution_only_touches_the_days_before():
    e = [cev(1, "2026-09-20", "pfp", status="resolved", resolved="2026-10-01")]
    ss = O.condition_apply(_week_ss(), e)
    assert not any(s.id == "downhill" for s in ss)                           # 9/30: still open
    assert any(s.id == "climb" for s in ss)                                  # 10/1: resolved that day
    assert next(s for s in ss if s.kind == "long").terrain is None


def test_tech_lsd_goes_back_to_a_flat_long_run():
    ss = [{"id": "tech", "kind": "hike", "title": "技術地形（LSD）", "minutes": 120, "day": "2026-10-03",
           "tss": 90.0, "done": False, "terrain": "trail", "steps": {"v": 1}}]
    O.condition_apply(ss, [cev(1, "2026-09-20", "itb")])
    assert ss[0]["kind"] == "long" and ss[0]["id"] == "long" and ss[0]["terrain"] == "road" and ss[0]["minutes"] == 120


# ---------------------------------------------------------------------------
# week_plan and the projection
# ---------------------------------------------------------------------------

def _wp(monkeypatch, events):
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    ds, plan, _ = _week("2026-11-28", {4: LIGHT})                       # 賽前第 9 週: a downhill week
    monkeypatch.setattr(INJ, "load_events", lambda *a, **k: list(events))
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    return ds, plan, O.week_plan(ds, st, TODAY)


def _strip(wp):
    return [{k: v for k, v in s.items() if k != "done_by"} for s in wp["sessions"]]


def test_week_plan_knee(monkeypatch):
    _, _, base = _wp(monkeypatch, [])
    assert base["downhill"]["active"] and any(s["id"] == "downhill" for s in base["sessions"])
    _, plan, wp = _wp(monkeypatch, [cev(1, "2026-09-20", "pfp", side="right")])
    assert not wp["downhill"]["active"] and PFP_NOTE in wp["downhill"]["why"]
    assert not any(s["id"] in ("downhill", "climb", "tech") for s in wp["sessions"])
    assert any(PFP_NOTE in n["text"] for n in wp["notes"])
    lng = next(s for s in wp["sessions"] if s["kind"] == "long")
    assert lng["terrain"] == "road"
    tot = lambda w: sum(s["minutes"] for s in w["sessions"] if s["kind"] in O.RUN_KINDS)   # noqa: E731
    assert abs(tot(wp) - tot(base)) <= 5                                  # the week's volume holds
    # the projected weeks: no downhill while the event stays open
    weeks = P.project_weeks(wp, _phases(plan, TODAY), date(2026, 11, 28), events=plan.events)
    assert weeks and not any(s["id"] == "downhill" for w in weeks for s in w["sessions"])


def test_week_plan_no_condition_is_unchanged(monkeypatch):
    _, _, base = _wp(monkeypatch, [])
    for evs in ([ev(1, "2026-09-20", side="right")], [cev(1, "2026-09-20", "plantar_fascia")],
                [cev(1, "2026-09-01", "pfp", status="resolved", resolved="2026-09-29")]):
        _, _, wp = _wp(monkeypatch, evs)
        assert _strip(wp) == _strip(base)
        assert wp["downhill"] == base["downhill"]
