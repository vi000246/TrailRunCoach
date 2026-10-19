"""SP-412: an old multi-day trip saved before SP-114 had no per-day numbers, and editing it (say, only
ticking 「會用登山杖」) could not be saved — the page asked for every day's distance and climb, the
server refused without them.

Decision (2026-10-13, option A): an edit goes through when the event ON THE SERVER already lacks its
per-day numbers (Event.day_plan_missing) AND the rows sent are all blank — it is saved as it was,
still hinted. Everything else stays SP-114's rule (test_day_plan.py): rows filled only in part are
refused, a new multi-day trip needs every day, an event that has its per-day numbers cannot lose them.
"""
import json
import re
from pathlib import Path

import pytest

from backend.engine import planning as P

DAYS3 = [{"km": 20.0, "gain_m": 1500, "loss_m": 300}, {"km": 15.0, "gain_m": 1000}, {"km": 30.0, "gain_m": 400, "loss_m": 2600}]
BLANK3 = [{"km": None, "gain_m": None, "loss_m": None}] * 3      # what the page sends with the rows left empty
OLD = {"id": "old", "name": "百岳 3 日", "date": "2027-01-10", "kind": "baiyue", "days": 3,
       "distance_km": 65, "climbing_m": 3500}

STATIC = Path(__file__).resolve().parents[1] / "static"


def old_plan(tmp_path, **ev) -> P.Plan:
    """A plan.json written before SP-114: a 3-day 百岳 without day_plan."""
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"events": [{**OLD, **ev}]}, ensure_ascii=False), "utf-8")
    return P.Plan.load(path)


def edit(**kw) -> dict:
    a = {k: v for k, v in OLD.items()}
    a.update(kw)
    return a


# ---- what goes through ----

@pytest.mark.parametrize("rows", [None, [], BLANK3], ids=["none", "empty", "blank-rows"])
def test_an_old_trip_without_day_numbers_saves_another_field(tmp_path, rows):
    plan = old_plan(tmp_path)
    assert plan.events[0].day_plan_missing
    e = plan.upsert_event(edit(poles=True, day_plan=rows))
    assert e.poles is True and e.day_plan is None
    assert e.distance_km == 65 and e.climbing_m == 3500             # the totals kept as they were
    assert e.day_plan_missing                                       # still hinted: 「請補每天的距離和爬升」
    assert [x.id for x in plan.events] == ["old"]                   # replaced, not added


def test_an_old_trip_can_get_its_day_numbers_on_that_edit(tmp_path):
    e = old_plan(tmp_path).upsert_event(edit(day_plan=DAYS3))
    assert not e.day_plan_missing and e.distance_km == 65.0 and e.climbing_m == 2900


def test_the_api_saves_the_edit_of_an_old_trip(tmp_path, monkeypatch):
    from backend.api import plan as A
    old_plan(tmp_path)
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    r = A.put_event(A.EventIn(**edit(poles=True, day_plan=BLANK3)))
    assert r["event"]["poles"] is True and r["event"]["day_plan_missing"] is True
    saved = json.loads((tmp_path / "plan.json").read_text("utf-8"))["events"]
    assert len(saved) == 1 and saved[0]["poles"] is True and saved[0]["day_plan"] is None


# ---- what is still refused (SP-114) ----

def test_rows_filled_in_part_are_still_refused_on_an_old_trip(tmp_path):
    plan = old_plan(tmp_path)
    with pytest.raises(P.EventError, match="第 2 天"):
        plan.upsert_event(edit(day_plan=[DAYS3[0], {"km": None, "gain_m": None, "loss_m": None}, DAYS3[2]]))
    with pytest.raises(P.EventError, match="第 1 天"):
        plan.upsert_event(edit(day_plan=[{"km": 12.0, "gain_m": None, "loss_m": None}] + BLANK3[1:]))
    with pytest.raises(P.EventError, match="3 天要填 3 列"):
        plan.upsert_event(edit(day_plan=DAYS3[:2]))
    assert plan.events[0].poles is False                            # nothing was saved


@pytest.mark.parametrize("rows", [None, BLANK3], ids=["none", "blank-rows"])
def test_a_new_multi_day_trip_still_needs_every_day(tmp_path, rows):
    plan = old_plan(tmp_path)                                       # an old trip in the plan does not open the door…
    with pytest.raises(P.EventError):
        plan.upsert_event(edit(id=None, name="合歡北西", day_plan=rows))     # …for a new one
    with pytest.raises(P.EventError):
        plan.upsert_event(edit(id="other", name="合歡北西", day_plan=rows))  # nor an id the server does not have
    with pytest.raises(P.EventError):
        P.Plan().upsert_event(edit(day_plan=rows))                  # an empty plan


@pytest.mark.parametrize("rows", [None, [], BLANK3], ids=["none", "empty", "blank-rows"])
def test_a_trip_with_its_day_numbers_cannot_lose_them(tmp_path, rows):
    plan = old_plan(tmp_path)
    plan.upsert_event(edit(day_plan=DAYS3))
    with pytest.raises(P.EventError):
        plan.upsert_event(edit(poles=True, day_plan=rows))
    assert plan.events[0].day_plan and not plan.events[0].poles


def test_a_one_day_event_made_multi_day_needs_every_day(tmp_path):
    # it had no per-day numbers because it did not need them, not because it is old
    plan = old_plan(tmp_path, days=1)
    assert not plan.events[0].day_plan_missing
    with pytest.raises(P.EventError):
        plan.upsert_event(edit(days=3, day_plan=BLANK3))


def test_the_api_still_refuses_a_new_trip_with_blank_rows(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from backend.api import plan as A
    old_plan(tmp_path)
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    with pytest.raises(HTTPException) as x:
        A.put_event(A.EventIn(**edit(id=None, name="合歡北西", day_plan=BLANK3)))
    assert x.value.status_code == 400 and "第 1 天" in x.value.detail


# ---- the page: the rows of such a trip are not required, a hint asks for them ----

def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "plan.json").read_text("utf-8"))


def test_the_page_drops_required_for_an_old_trip_and_hints_instead():
    html = (STATIC / "races.html").read_text("utf-8")   # SP-691: the event editor moved to 賽事管理
    # the edit remembers whether the event lacks its per-day numbers on the server…
    assert re.search(r"dayPlanLenient\s*=\s*!!e\.day_plan_missing", html)
    # …and the rows are required unless it does (and none of them is filled in yet)
    assert "dayPlanRequired()" in html
    assert re.search(r'id="dayplanold"[^>]*data-i18n="plan\.ev\.dayplan_old"', html)
    # a save, 取消 and a new event go back to the SP-114 rule
    assert html.count("dayPlanLenient = false") >= 2


def test_the_hint_is_in_both_languages():
    zh, en = _cat("zh-TW"), _cat("en")
    assert "照原樣存" in zh["ev.dayplan_old"] and "每天" in zh["ev.dayplan_old"]
    assert "save" in en["ev.dayplan_old"].lower() and "each day" in en["ev.dayplan_old"].lower()



# ---- SP-412 follow-up (decision 2026-10-13): an old trip's totals can be edited directly ----
# While the trip has no per-day numbers, its 總距離／總爬升 are the only numbers it has (each day is an
# equal share of them), so they are editable and saved as sent. A new multi-day trip and a trip with
# its per-day numbers keep SP-114's rule: the totals are the sums of the days (read-only on the page).

@pytest.mark.parametrize("rows", [None, [], BLANK3], ids=["none", "empty", "blank-rows"])
def test_an_old_trip_saves_new_totals(tmp_path, rows):
    plan = old_plan(tmp_path)
    e = plan.upsert_event(edit(distance_km=70.5, climbing_m=3800, day_plan=rows))
    assert (e.distance_km, e.climbing_m) == (70.5, 3800)
    assert e.day_plan is None and e.day_plan_missing                # still hinted
    assert [x.id for x in plan.events] == ["old"]


def test_the_api_saves_new_totals_of_an_old_trip(tmp_path, monkeypatch):
    from backend.api import plan as A
    old_plan(tmp_path)
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    r = A.put_event(A.EventIn(**edit(distance_km=70.5, climbing_m=3800, day_plan=BLANK3)))
    assert (r["event"]["distance_km"], r["event"]["climbing_m"]) == (70.5, 3800)
    assert r["event"]["day_plan_missing"] is True
    saved = json.loads((tmp_path / "plan.json").read_text("utf-8"))["events"]
    assert (saved[0]["distance_km"], saved[0]["climbing_m"], saved[0]["day_plan"]) == (70.5, 3800, None)


def test_a_trip_with_day_numbers_keeps_the_sums_whatever_totals_are_sent(tmp_path):
    plan = old_plan(tmp_path)
    e = plan.upsert_event(edit(distance_km=999, climbing_m=9999, day_plan=DAYS3))
    assert (e.distance_km, e.climbing_m) == (65.0, 2900)               # 20 + 15 + 30, 1500 + 1000 + 400
    e = plan.upsert_event(edit(distance_km=1, climbing_m=1, day_plan=DAYS3))
    assert (e.distance_km, e.climbing_m) == (65.0, 2900)


def test_a_new_trip_keeps_the_sums_whatever_totals_are_sent():
    e = P.Plan().upsert_event(edit(id=None, distance_km=999, climbing_m=9999, day_plan=DAYS3))
    assert (e.distance_km, e.climbing_m) == (65.0, 2900)


def test_the_page_unlocks_the_totals_only_for_an_old_trip_with_blank_rows():
    html = (STATIC / "races.html").read_text("utf-8")   # SP-691
    # SP-114 locked them for every multi-day trip: `readOnly = on`
    assert not re.search(r"climbing_m\.readOnly\s*=\s*on\s*;", html)
    # now locked while the rows are required (a new trip, a trip with its numbers, an old trip once a row is filled)
    assert re.search(r"climbing_m\.readOnly\s*=\s*on\s*&&\s*req\b", html)
    # a total typed in is what comes back when the rows are emptied again
    assert re.search(r"dayPlanTotals\[k\]\s*=", html)


def test_the_hint_says_the_totals_can_be_edited_in_both_languages():
    zh, en = _cat("zh-TW"), _cat("en")
    assert "總距離、總爬升也可以直接改" in zh["ev.dayplan_old"]
    assert "edit its total distance and climb" in en["ev.dayplan_old"]
    # SP-114's help is unchanged: every day's distance and climb
    assert "每天的距離和爬升都要填" in zh["ev.dayplan_help"]
