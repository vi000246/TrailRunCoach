"""熱適應課 (engine/heat_plan.py, heat-acclimation.md §5.4) and its plumbing."""
from __future__ import annotations

import datetime as dt

import pytest
from pytest import approx

from backend.engine import heat_plan as HP
from backend.engine import plan_prefs as PP
from backend.engine.planning import Event

TODAY = dt.date(2026, 10, 5)                       # a Monday


def week(extra=()):
    days = [TODAY + dt.timedelta(days=i) for i in range(7)]
    ss = [{"id": "long", "kind": "long", "title": "LSD", "minutes": 90, "day": days[5].isoformat(), "tss": 80.0,
           "done": False, "detail": "", "source": ""},
          {"id": "easy1", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": days[1].isoformat(), "tss": 40.0,
           "done": False, "detail": "", "source": ""},
          {"id": "easy2", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": days[3].isoformat(), "tss": 40.0,
           "done": False, "detail": "", "source": ""},
          {"id": "strength1", "kind": "strength", "title": "肌力", "minutes": 35, "day": days[2].isoformat(), "tss": 20.0,
           "done": False}]
    return ss + list(extra)


def ev(days_to, heat="hot", prio="A", kind="race"):
    return Event(id="e1", name="熱天賽", date=(TODAY + dt.timedelta(days=days_to)).isoformat(), kind=kind,
                 priority=prio, heat=heat)


def test_no_hot_race_changes_nothing():
    ss = week()
    before = [dict(s) for s in ss]
    info = HP.apply(ss, events=[ev(20, heat="cool")], today=TODAY, acts=[])
    assert not info["active"] and ss == before
    info = HP.apply(ss, events=[ev(20, prio="C")], today=TODAY, acts=[])
    assert not info["active"] and ss == before
    info = HP.apply(ss, events=[ev(45)], today=TODAY, acts=[])          # beyond 30 days
    assert not info["active"] and ss == before


def test_off_and_already_acclimatised():
    ss = week()
    assert HP.apply(ss, events=[ev(20)], today=TODAY, prefs=PP.Prefs(heat="off"), acts=[])["reason"] == "熱適應課已關閉"
    hot = [{"date": (TODAY - dt.timedelta(days=i)).isoformat(), "hot_min": 90} for i in range(12)]
    info = HP.apply(ss, events=[ev(5)], today=TODAY, acts=hot)      # S ≈ 0.94 decays to ≈ 0.83 by race day
    assert not info["active"] and info["s_race_before"] >= 0.75 and "≥ 75" in info["reason"]


def test_induction_block_tags_the_long_run_and_easy_runs():
    ss = week()
    notes = []
    info = HP.apply(ss, events=[ev(20)], today=TODAY, acts=[], notes=notes, aet=142)
    # race on 10/25: induction 10/04 – 10/17 → every placed easy / long run this week
    assert info["active"] and info["days"] == sorted(s["day"] for s in ss if s["kind"] in ("easy", "long"))
    easy = [s for s in ss if s["kind"] == "easy"]
    assert all(s["heat"] and s["minutes"] == 60 and s["title"] == "熱適應輕鬆跑" for s in easy)
    assert easy[0]["tss"] == approx(40.0 * 60 / 45)
    assert "輕鬆跑上限 142 bpm" in easy[0]["target"]
    assert [s for s in ss if s["id"] == "long"][0]["title"].endswith("（熱適應）")
    assert info["s_race_after"] > info["s_race_before"]
    assert any("熱天" in n["text"] for n in notes)


def test_cap_exemption_note_and_hard_cap_becomes_bath():
    ss = week()
    notes = []
    HP.apply(ss, events=[ev(20)], today=TODAY, acts=[], notes=notes, prefs=PP.Prefs(cap_weekday=50))
    assert any(n["text"] == HP.NOTE_HEAT for n in notes)
    assert all(s["minutes"] == 60 for s in ss if s["kind"] == "easy")
    ss = week()
    HP.apply(ss, events=[ev(20)], today=TODAY, acts=[], prefs=PP.Prefs(cap_weekday=50, cap_mode="hard"))
    easy = [s for s in ss if s["kind"] == "easy"]
    passive = [s for s in ss if s["kind"] == "heat_passive"]
    assert all(s["minutes"] == 40 for s in easy) and len(passive) == len(easy)
    assert passive[0]["tss"] == 0.0 and passive[0]["day"] == easy[0]["day"] and "40 °C" in passive[0]["title"]


def test_methods_and_maintenance_spacing():
    ss = week()
    HP.apply(ss, events=[ev(20)], today=TODAY, acts=[], prefs=PP.Prefs(heat_method="sauna"))
    assert [s["source"] for s in ss if s["kind"] == "heat_passive"][0] == HP.SRC_SAUNA
    ss = week()
    HP.apply(ss, events=[ev(20)], today=TODAY, acts=[], prefs=PP.Prefs(heat_method="mixed"))
    heat = [s for s in ss if s.get("heat") and s["kind"] == "easy"]
    assert heat[0]["title"] == "熱適應輕鬆跑" and "熱水浴" in heat[1]["title"]
    # race 10/13: maintenance window 10/06–10/10, at most one every 4 days (10/08 skipped)
    ss = week()
    info = HP.apply(ss, events=[ev(8)], today=TODAY, acts=[])
    assert info["active"] and info["days"] == ["2026-10-06", "2026-10-10"]


def test_prefs_heat_is_not_shaping_and_round_trips():
    assert not PP.Prefs(heat="off", heat_method="bath").active
    p = PP.from_settings({"plan.prefs.heat": "off", "plan.prefs.heat_method": "sauna"})
    assert p.heat == "off" and p.heat_method == "sauna"
    assert PP.Prefs().settings()["plan.prefs.heat"] == "auto"
    from backend.settings import repository as REPO
    assert REPO.DEFAULTS["plan.prefs.heat"] == "auto" and "mixed" in REPO.PREF_ENUMS["plan.prefs.heat_method"]


def test_coros_heat_run_steps_and_passive_never_pushed():
    from backend.sync import coros_workouts as CW
    th = CW.Thresholds.of({"aet": 142, "lthr": 160, "cp": 200})
    steps = CW.session_steps({"kind": "easy", "title": "熱適應輕鬆跑", "minutes": 60, "heat": True}, th)
    assert [s.kind for s in steps] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_COOLDOWN]
    assert [s.seconds for s in steps] == [600, 2700, 300] and steps[2].name == "走路降溫"
    with pytest.raises(CW.Unsupported, match="被動熱適應不推"):
        CW.session_steps({"kind": "heat_passive", "title": "跑後熱水浴", "minutes": 40}, th)


def test_move_to_keeps_heat_passive_off_the_main_days():
    from backend.engine import blackouts as BL
    wk = [{"kind": "easy", "day": "2026-10-06", "state": "active"},
          {"kind": "heat_passive", "day": "2026-10-07", "state": "active"}]
    s = {"kind": "easy", "day": "2026-10-08", "state": "active"}
    # the bath on 10/07 does not hold that day: the easy run may move there
    assert BL.move_to(s, wk + [s], {"2026-10-08": None}, "2026-10-05") == "2026-10-07"
    hp = wk[1]
    assert BL.move_to(hp, wk, {"2026-10-07": None}, "2026-10-05") in ("2026-10-06", "2026-10-08")


def test_event_heat_field_round_trips():
    from backend.engine import planning as P
    plan = P.Plan()
    e = plan.upsert_event({"name": "x", "date": "2026-11-01", "heat": "hot"})
    assert e.heat == "hot"
    assert plan.upsert_event({"name": "y", "date": "2026-11-01", "heat": "bogus"}).heat == "auto"
    assert P.event_json(e, TODAY)["heat"] == "hot"


def test_event_is_hot_auto_rules():
    from backend.engine import heat_data as HD
    past = [{"date": f"{y}-10-{d:02d}", "hadley": 155} for y in (2024, 2025) for d in (20, 22, 25)]
    assert HD.event_is_hot(ev(20, heat="auto"), past)["hot"]
    assert not HD.event_is_hot(ev(20, heat="auto", kind="baiyue"), past)["hot"]
    assert not HD.event_is_hot(ev(20, heat="auto"), [])["hot"]
