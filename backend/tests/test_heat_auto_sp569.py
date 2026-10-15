"""SP-569 (user 2026-10-14): 熱適應 is automatic only — the plan never schedules a heat session (no
熱適應輕鬆跑, no hot bath / sauna `heat_passive`), the 課表偏好 have no 熱適應 options, a heat session
stored before is put back to a plain easy run / removed on the next replan by the plan store's own
reconcile rules (edited, custom and done rows stay), and the race calculator has no 熱適應 choice
(the race-day S is projected from the activities' weather). The race-day heat and the 熱適應 card stay.
Synthetic only (conftest: no real per-activity weather)."""
from __future__ import annotations

import datetime as dt
import importlib
import json
from datetime import date
from pathlib import Path

import pytest

from backend.engine import heat_data as HD
from backend.engine import plan_prefs as PP
from backend.engine import plan_store as PS
from backend.engine import reconcile as R
from backend.engine.planning import Event
from backend.tests.test_plan_store import active, cur_plan, g, inputs, next_week

STATIC = Path(__file__).resolve().parents[1] / "static"
HEAT_WORDS = ("熱適應", "熱水浴", "桑拿")


def _hot_a(start: str) -> Event:
    return Event(id="hot", name="熱天賽", date=start, kind="race", priority="A", distance_km=42.2,
                 est_hours=4.0, heat="hot")


def _heat_free(sessions) -> None:
    for s in sessions:
        assert s["kind"] != "heat_passive", s
        assert not s.get("heat"), s
        assert not any(w in (s.get("title") or "") for w in HEAT_WORDS), s


# ---------------------------------------------------------------------------------- the generator

def test_no_heat_sessions_before_a_hot_a_race():
    """A hot A race 18 days out (its old induction block 21–8 days before covers this week and the
    next) and no heat exposure (S 0): before SP-569 the easy runs became 熱適應輕鬆跑; now the week
    plan and the projected weeks have plain sessions only and no heat block."""
    from backend.engine import overview as O
    from backend.engine import projection as PJ
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _phases, _plan_with
    from backend.tests.test_quality_gate import TODAY        # Wed 9/30
    ds = _history(TODAY)
    plan = _plan_with("2027-06-01", 1, TODAY)
    plan.events = [e for e in plan.events if e.kind != "baiyue"] + [_hot_a("2026-10-18")]
    ds.plan = plan
    assert HD.hot_race(plan.events, TODAY, [])["event"].id == "hot"      # the card still sees the hot race
    st = Status(ds, plan, TODAY, prefs=PP.Prefs()).compute()
    wp = O.week_plan(ds, st, TODAY)
    assert "heat" not in wp
    assert [x for x in wp["sessions"] if x["kind"] in ("easy", "long")]
    _heat_free(wp["sessions"])
    assert not [n for n in wp["notes"] if n.get("src") == "heat"]
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18), events=plan.events)
    assert weeks
    for w in weeks:
        assert "heat" not in w
        _heat_free(w["sessions"])
        assert not [n for n in w.get("notes") or [] if n.get("src") == "heat"]


def test_the_heat_card_keeps_the_race_day_reminder_without_scheduling():
    """status.i_heat: a hot A race within 30 days with a low race-day S is still flagged, but the
    action no longer promises 熱適應課 on the schedule."""
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    from backend.tests.test_quality_gate import TODAY
    ds = _history(TODAY)
    plan = _plan_with("2027-06-01", 1, TODAY)
    plan.events = [_hot_a("2026-10-18")]
    ds.plan = plan
    ind = Status(ds, plan, TODAY, prefs=PP.Prefs()).i_heat()
    assert ind.level == "bad"
    assert "熱天賽" in ind.action and "賽事計算機" in ind.action
    assert "排" not in ind.action and "課表" not in ind.action


def test_hot_race_moved_to_heat_data():
    today = dt.date(2026, 10, 5)
    e = lambda d, **kw: Event(id="x", name="x", date=(today + dt.timedelta(days=d)).isoformat(),
                              **{"priority": "A", "heat": "hot", **kw})
    assert HD.hot_race([e(20)], today, [])["days_to"] == 20
    assert HD.hot_race([e(31)], today, []) is None                         # beyond 30 days
    assert HD.hot_race([e(-1)], today, []) is None                         # already run
    assert HD.hot_race([e(10, priority="C")], today, []) is None           # A / B only
    assert HD.hot_race([e(10, heat="cool")], today, []) is None
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.engine.heat_plan")


# ---------------------------------------------------------------------------------- the preferences

def test_no_heat_preferences():
    from backend.settings import repository as REPO
    names = {f for f in PP.Prefs.__dataclass_fields__}
    assert "heat" not in names and "heat_method" not in names
    assert not [k for k in PP.KEY_FIELDS if k.startswith("plan.prefs.heat")]
    assert not [k for k in REPO.DEFAULTS if k.startswith("plan.prefs.heat")]
    assert not [k for k in REPO.PREF_ENUMS if k.startswith("plan.prefs.heat")]
    # a page still open from before sends them: ignored, not an error; other unknown names still are
    p = PP.from_body({**PP.Prefs().to_dict(), "heat": "off", "heat_method": "sauna"})
    assert p == PP.Prefs()
    with pytest.raises(ValueError, match="unknown preference"):
        PP.from_body({"bogus": 1})
    # a stored old value is simply not read
    assert PP.from_settings({"plan.prefs.heat": "off", "plan.prefs.heat_method": "bath"}) == PP.Prefs()


def test_the_preferences_dialog_has_no_heat_rows():
    s = (STATIC / "schedule.html").read_text("utf-8")
    for el in ('id="pf-heat"', 'id="pf-heatx"', 'id="pf-hm"', 'name="heat_method"', "pf-heat-s", "pfHeatLine",
               "heat_method:", "E.heat.checked"):
        assert el not in s, el
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "schedule.json").read_text("utf-8"))
        assert not [k for k in cat if k.startswith(("pf.heat", "pf.hm."))], loc
        # nothing left that asks the user to record a hot bath / sauna
        assert not [k for k, v in cat.items() if isinstance(v, str) and ("打勾" in v or "tick" in v.lower())
                    and any(w in v for w in ("熱水浴", "桑拿", "bath", "sauna"))], loc


# ---------------------------------------------------------------------------------- stored rows

def _old_week():
    """The week of 10/5 as the generator wrote it before SP-569: easy1 a 熱適應輕鬆跑 with its bath."""
    return next_week(sessions=[
        g("quality", "quality", "閾值 3×10 分", 60, "2026-10-06"),
        g("easy1", "easy", "熱適應輕鬆跑", 60, "2026-10-07"),
        g("heat_passive_easy1", "heat_passive", "跑後熱水浴 40 °C ≤ 40 分", 40, "2026-10-07"),
        g("long", "long", "LSD（山路）（熱適應）", 130, "2026-10-11")])


def test_stored_heat_sessions_go_back_to_plain_on_the_next_replan():
    """Unedited auto rows: the heat run becomes the generator's plain easy run (same uid, a change),
    the long run loses its tag, the bath is removed — reconcile's rule 2, nothing heat-specific."""
    stored, _ = R.reconcile([], PS.gen_weeks(inputs(weeks=[_old_week()])), [], "2026-09-30", "2026-10-11")
    old = active(stored)
    assert old[("2026-10-05", "easy1")]["title"] == "熱適應輕鬆跑"
    assert ("2026-10-05", "heat_passive_easy1") in old
    new, ch = R.reconcile(stored, PS.gen_weeks(inputs(weeks=[next_week()])), [], "2026-09-30", "2026-10-11")
    a = active(new)
    e1 = a[("2026-10-05", "easy1")]
    assert (e1["title"], e1["minutes"], e1["uid"]) == ("輕鬆跑", 50, old[("2026-10-05", "easy1")]["uid"])
    assert a[("2026-10-05", "long")]["title"] == "LSD（山路）"
    assert ("2026-10-05", "heat_passive_easy1") not in a
    _heat_free(a.values())
    acts = {(c["action"], c["title"]) for c in ch}
    assert ("changed", "輕鬆跑") in acts and ("removed", "跑後熱水浴 40 °C ≤ 40 分") in acts


def test_edited_custom_and_done_heat_rows_stay():
    """The user's own rows are never touched: an edited heat run, a hot bath added by hand, and a
    bath already done stay as they are (rule 3; done rows are history)."""
    stored, _ = R.reconcile([], PS.gen_weeks(inputs(weeks=[_old_week()])), [], "2026-09-30", "2026-10-11")
    old = active(stored)
    old[("2026-10-05", "easy1")].update(minutes=70, edited=True)
    custom = {**old[("2026-10-05", "heat_passive_easy1")], "uid": "mine1", "gen_key": None, "origin": "custom",
              "day": "2026-10-09"}
    done = {**old[("2026-10-05", "heat_passive_easy1")], "uid": "done1", "gen_key": "heat_passive_easy2",
            "week_start": "2026-09-28", "day": "2026-09-29", "state": "done"}
    stored += [custom, done]
    new, _ = R.reconcile(stored, PS.gen_weeks(inputs(weeks=[next_week()])), [], "2026-09-30", "2026-10-11")
    by_uid = {s["uid"]: s for s in new}
    e1 = by_uid[old[("2026-10-05", "easy1")]["uid"]]
    assert (e1["state"], e1["title"], e1["minutes"]) == ("active", "熱適應輕鬆跑", 70)
    assert by_uid["mine1"]["state"] == "active" and by_uid["mine1"]["kind"] == "heat_passive"
    assert by_uid["done1"]["state"] == "done"


# ---------------------------------------------------------------------------------- the calculator

def test_the_calculator_has_no_heat_acclimatisation_choice():
    """Like SP-546's 海拔: the 熱適應 row (自動／未適應／部分／已適應／自訂 %, its input and line) is gone;
    every calculation asks for the automatic value, a saved S.heatAccl is ignored."""
    s = (STATIC / "racepower.html").read_text("utf-8")
    for el in ('id="heat-accl"', 'id="heat-s"', 'id="heat-accl-line"', "S.heatAccl", "heatAccl:", "heatAcclLabel",
               "adv.n.heat_accl", '"heat-s"'):
        assert el not in s, el
    assert 'function heatAcclBody() { return { mode: "auto" }; }' in s
    assert "heat_acclimatisation: heatAcclBody()" in s
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert "adv.n.heat_accl" not in cat, loc


def test_auto_mode_projects_the_race_day_s_from_the_activities():
    """planner.heat_acclimation's auto mode (what the page always sends) takes the race-day S the
    server projects (heat_data.status); with no exposure it is unacclimatised."""
    from backend.engine.racepower import planner as PL
    st = HD.status(dt.date(2026, 10, 5), dt.date(2026, 10, 20),
                   acts=[{"date": f"2026-10-0{d}", "hot_min": 60.0} for d in range(1, 5)])
    assert st["s_race"]["center"] > 0
    h = PL.heat_acclimation({"heat_acclimatisation": {"mode": "auto"}, "heat_status": st})
    assert h["mode"] == "auto" and h["s"] == pytest.approx(st["s_race"]["center"])
    h0 = PL.heat_acclimation({"heat_acclimatisation": {"mode": "auto"}, "heat_status": {}})
    assert h0["s"] == 0.0 and "未適應" in h0["source"]
