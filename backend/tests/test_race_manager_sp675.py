"""SP-675: 賽事管理 — every race, past and future, in one list (filter / sort, the result and the
activity it matched), one race's day (pace / HR / power / climbing, splits, the readiness before it,
the estimate vs the actual), two races overlaid (the activity comparison of SP-625), add / edit /
delete on the same season-plan events as 賽事周期, a way to the race calculator's calculation and
back, and Cairn: a share link imported as the race's GPX, the course pushed (SP-655), with a plain
hint while Cairn is not linked. Fake datasets / tmp files only."""
import datetime as dt
import json
import re
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import activity_compare as AC
from backend.engine import race_manager as RM
from backend.engine.planning import Event, Plan

STATIC = Path(__file__).resolve().parents[1] / "static"
TODAY = dt.date(2026, 10, 16)
D = dt.datetime


def _w(idx, start, km, gain=None, dur=None, title="", sport="run", trail=True):
    return SimpleNamespace(idx=idx, sport=sport, sport_type="trail running" if trail else "running",
                           tags=["runningtrail"] if trail else [],
                           metrics={"distance": km, "climbing": gain, "duration": dur},
                           entry=SimpleNamespace(start=start, file=f"f{idx}.fit", title=title))


def _ds(ws, events):
    return SimpleNamespace(workouts=ws, plan=SimpleNamespace(events=events))


def _series(km=10.0, pace_s=360.0, hr=150.0):
    n = int(km * pace_s) + 1
    t = np.arange(n, dtype=float)
    d = t / pace_s
    return AC.Series(t, d, 100.0 + 50 * np.sin(d), np.full(n, hr), None)


def _events():
    return [Event(id="past", name="北橫 30K", date="2026-09-20", kind="race", priority="B", distance_km=30,
                  climbing_m=1800, est_hours=4.0, result="分組第 5"),
            Event(id="next", name="東眼山 50K", date="2026-12-06", kind="race", priority="A", distance_km=50,
                  climbing_m=3000),
            Event(id="trip", name="雪山", date="2026-08-01", kind="baiyue", priority="A", distance_km=20)]


def _world():
    ws = [_w(0, D(2026, 9, 20, 7), 31.0, 1750, dur=4 * 3600 + 600),
          _w(1, D(2026, 7, 5, 7), 10.0, 400, dur=3600, title="大屯山越野賽"),
          _w(2, D(2026, 6, 1, 7), 8.0, 100, dur=2700)]
    return _ds(ws, _events())


# ---- 1. the list ---------------------------------------------------------------------------

def test_list_has_past_and_future_races_with_result_and_matched_activity():
    rows = RM.rows(_world(), TODAY, tags=[], calcs={})
    by = {r["id"]: r for r in rows}
    assert set(by) == {"e:past", "e:next", "e:trip", "a:1"}  # 百岳 trips are listed (owner 2026-10-16); idx 2 is training
    past, nxt, title = by["e:past"], by["e:next"], by["a:1"]
    assert past["past"] and not nxt["past"]
    assert past["priority"] == "B" and past["distance_km"] == 30 and past["climbing_m"] == 1800
    assert past["status"] == "matched" and [a["idx"] for a in past["activities"]] == [0]
    assert past["finish_s"] == 4 * 3600 + 600 and past["result"] == "分組第 5"
    assert past["compare_idx"] == 0
    assert nxt["status"] == "upcoming" and nxt["finish_s"] is None and nxt["compare_idx"] is None
    assert title["event"] is None and title["source"] == "title" and title["finish_s"] == 3600
    assert title["distance_km"] == 10.0 and title["compare_idx"] == 1
    # newest first
    assert [r["date"] for r in rows] == sorted((r["date"] for r in rows), reverse=True)


def test_list_carries_the_saved_calculation_prediction():
    calcs = {"past": {"result": {"summary": {"time_s": 14000, "stops_s": 400}}, "saved_at": "2026-09-18 21:00:00"}}
    past = next(r for r in RM.rows(_world(), TODAY, tags=[], calcs=calcs) if r["id"] == "e:past")
    assert past["calc"] == {"saved": True, "predicted_s": 14400, "saved_at": "2026-09-18 21:00:00"}
    assert RM.predicted_s({"summary": {"time_total_s": 9000, "time_s": 1}}) == 9000
    assert RM.predicted_s(None) is None and RM.predicted_s({"summary": {}}) is None


def test_api_list_and_the_page_has_filter_and_sort(monkeypatch):
    from backend.api import race_manager as API
    monkeypatch.setattr(API, "_dataset", lambda: _world())
    monkeypatch.setattr(API, "_tags", lambda: [])
    monkeypatch.setattr(API, "_calcs", lambda ids: {})
    monkeypatch.setattr(API, "today_local", lambda: TODAY)
    r = API.races()
    assert r["today"] == TODAY.isoformat() and {x["id"] for x in r["rows"]} == {"e:past", "e:next", "e:trip", "a:1"}
    assert set(r["kinds"]) >= {"race", "road", "other", "baiyue"}
    html = (STATIC / "races.html").read_text("utf-8")
    for el in ('id="f-when"', 'id="f-prio"', 'id="f-kind"', 'id="sort"', 'id="list"'):
        assert el in html, el
    assert "/api/v1/app/races" in html


def test_page_route_is_mounted():
    from backend.tests.test_new_user_smoke import _all_routes
    paths = {getattr(r, "path", "") for r in _all_routes()}
    assert {"/api/v1/app/races/page", "/api/v1/app/races", "/api/v1/app/races/detail",
            "/api/v1/app/races/readiness/{eid}"} <= paths


# ---- 2. one race: the day's data, estimate vs actual -------------------------------------

def test_detail_has_the_days_data_and_estimate_vs_actual():
    calcs = {"past": {"result": {"summary": {"time_total_s": 4 * 3600}}, "saved_at": "2026-09-18 21:00:00"}}
    d = RM.detail(_world(), TODAY, tags=[], calcs=calcs, eid="past", series=lambda idx: _series())
    assert d["row"]["id"] == "e:past"
    act = d["activities"][0]
    assert act["idx"] == 0 and act["summary"]["distance_km"] == 31.0 and act["summary"]["avg_hr"] == 150
    assert act["splits"] and act["splits"][0]["km"] == 1
    ov = act["overlay"]
    assert len(ov["pct"]) == len(ov["elev"]) == len(ov["pace_min_km"]) and ov["elev"][0] is not None
    est = {e["key"]: e for e in d["estimates"]}
    assert est["calc"]["predicted_s"] == 4 * 3600 and est["calc"]["actual_s"] == 4 * 3600 + 600
    assert est["calc"]["diff_s"] == 600
    assert est["own"]["predicted_s"] == 4 * 3600          # 你填的預估 est_hours
    assert d["calc_url"] == "/api/v1/racepower/page?event=past"


def test_detail_of_an_activity_only_race_and_an_upcoming_one():
    d = RM.detail(_world(), TODAY, tags=[], calcs={}, idx=1, series=lambda idx: _series(km=10))
    assert d["row"]["id"] == "a:1" and d["activities"][0]["idx"] == 1 and d["estimates"] == []
    up = RM.detail(_world(), TODAY, tags=[], calcs={}, eid="next", series=lambda idx: _series())
    assert up["activities"] == [] and up["row"]["status"] == "upcoming"
    with pytest.raises(KeyError):
        RM.detail(_world(), TODAY, tags=[], calcs={}, eid="nope", series=lambda idx: _series())


def test_readiness_before_a_past_a_race_or_why_not(monkeypatch):
    from backend.engine import race_readiness as RR
    ev = Event(id="a1", name="A 賽", date="2026-09-20", kind="race", priority="A", distance_km=30)
    plan = SimpleNamespace(events=[ev])
    seen = {}

    def fake(plan_, ds_, today, predict=None):
        seen["today"] = today
        return {"race": {"id": "a1"}, "today": today, "taper_from": today, "text": "",
                "stages": [{"name": "專項量", "unlocked": True, "passed": True, "done": 4, "total": 5, "label": "達標"}]}
    monkeypatch.setattr(RR, "compute", fake)
    r = RM.readiness(plan, None, "a1", TODAY)
    assert seen["today"] == dt.date(2026, 9, 19) and r["available"] and r["stages"][0]["done"] == 4
    assert r["text"]
    ev.priority = "B"
    r = RM.readiness(plan, None, "a1", TODAY)
    assert not r["available"] and r["why"]


# ---- 3. two races overlaid -----------------------------------------------------------------

def test_compare_uses_the_activity_comparison_and_the_page_draws_it():
    html = (STATIC / "races.html").read_text("utf-8")
    assert "/api/v1/app/activity-compare?a=" in html          # the same overlay as SP-625 / the route page
    assert "/api/v1/app/activity-compare/page?a=" in html     # the full comparison one click away
    assert 'id="cmp-go"' in html and "echarts" in html


def test_page_catalog_has_english_for_every_key():
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "races.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "races.json").read_text("utf-8"))
    assert zh and not [k for k in zh if not en.get(k)]
    assert not [k for k, v in en.items() if re.search(r"[一-鿿]", v)]
    html = (STATIC / "races.html").read_text("utf-8")
    used = set(re.findall(r'\bT\("([a-z0-9_.]+)"', html)) | set(re.findall(r'data-i18n="races\.([a-z0-9_.]+)"', html))
    assert not sorted(k for k in used if not k.endswith(".") and k not in zh)


def test_phone_layout_has_no_wide_table():
    html = (STATIC / "races.html").read_text("utf-8")
    assert 'name="viewport"' in html and "@media (max-width: 699px)" in html
    assert "<table" not in html.split("<script>")[0] or "tablewrap" in html


# ---- 4. add / edit / delete: the same events as 賽事周期 ----------------------------------

def test_result_is_kept_when_the_season_page_saves_without_it():
    plan = Plan(events=[])
    ev = plan.upsert_event({"name": "北橫", "date": "2026-09-20", "kind": "race", "priority": "B",
                            "result": "總排 35"})
    assert ev.result == "總排 35"
    again = plan.upsert_event({"id": ev.id, "name": "北橫 30K", "date": "2026-09-20", "kind": "race",
                               "priority": "B", "result": None})        # 賽事周期's editor: no result field
    assert again.result == "總排 35" and again.name == "北橫 30K"
    cleared = plan.upsert_event({"id": ev.id, "name": "北橫", "date": "2026-09-20", "result": ""})
    assert cleared.result == ""


def test_crud_goes_through_the_plan_events_api(tmp_path, monkeypatch):
    from backend.api import plan as PA
    from backend.engine import planning as P
    path = tmp_path / "plan.json"
    monkeypatch.setattr(P, "plan_path", lambda: path)
    monkeypatch.setattr(PA, "_notify", lambda *a: None)
    out = PA.put_event(PA.EventIn(name="新賽事", date="2026-12-06", kind="race", priority="A", result="完賽"))
    eid = out["event"]["id"]
    assert out["event"]["result"] == "完賽" and [e.id for e in P.Plan.load(path).events] == [eid]
    PA.put_event(PA.EventIn(id=eid, name="新賽事 改", date="2026-12-06"))
    assert P.Plan.load(path).events[0].result == "完賽"
    PA.delete_event(eid)
    assert P.Plan.load(path).events == []
    html = (STATIC / "races.html").read_text("utf-8")
    assert "/api/v1/plan/events" in html and 'method: "DELETE"' in html and 'id="ed"' in html


def test_pages_link_each_other():
    races = (STATIC / "races.html").read_text("utf-8")
    plan = (STATIC / "plan.html").read_text("utf-8")
    assert "/api/v1/plan/page" in races and "/api/v1/app/races/page" in plan
    shell = (STATIC / "shell.js").read_text("utf-8")
    assert '"races"' in shell and "/api/v1/app/races/page" in shell
    for loc in ("zh-TW", "en"):
        sh = json.loads((STATIC / "i18n" / loc / "shell.json").read_text("utf-8"))
        flat = sh.get("page", {}).get("races") or {k.split(".", 2)[2]: v for k, v in sh.items()
                                                    if k.startswith("page.races.")}
        assert flat.get("name") and flat.get("short") and flat.get("purpose"), loc


# ---- 5. the race calculator, both ways ---------------------------------------------------

def test_calculator_links_back_and_opens_a_past_events_calculation():
    rp = (STATIC / "racepower.html").read_text("utf-8")
    assert 'id="to-races"' in rp and "/api/v1/app/races/page" in rp
    assert "addPastEvent" in rp                     # ?event=<a past race> still opens its saved calculation
    races = (STATIC / "races.html").read_text("utf-8")
    assert "calc_url" in races


# ---- 6. Cairn: import a GPX, push the course ----------------------------------------------

def test_cairn_buttons_use_the_import_and_push_apis_and_say_link_first():
    html = (STATIC / "races.html").read_text("utf-8")
    assert "/gpx/cairn" in html                                     # SP-537's share-link import
    assert "/api/v1/account/links/cairn/events/" in html            # SP-655's push + status
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "races.json").read_text("utf-8"))
    assert "連結" in zh["cairn.link_first"] and "連結" in zh["cairn.unavailable"]
    assert zh["cairn.import_pick_na"]                                # no "pick from my Cairn trips" API yet


def test_push_status_says_not_linked(monkeypatch):
    from backend.accounts import cairn_push as CP
    from backend.accounts import links as LK
    from backend import membership as MS
    monkeypatch.setattr(CP, "_event", lambda eid: SimpleNamespace(id=eid))
    monkeypatch.setattr(CP, "get", lambda eid: None)
    monkeypatch.setattr(CP, "_curve", lambda eid: {})
    monkeypatch.setattr(LK, "is_linked", lambda u, c: False)
    monkeypatch.setattr(LK, "secret", lambda c: "s")
    monkeypatch.setattr(LK, "base_url", lambda c: "https://cairn.test")
    monkeypatch.setattr(LK, "link_url", lambda c, scope=None: "/link/cairn")
    monkeypatch.setattr(MS, "member", lambda: SimpleNamespace(role=MS.SUBSCRIBER))
    st = CP.status(SimpleNamespace(id=1, google_sub="g"), "past")
    assert st["available"] and not st["linked"] and not st["allowed"] and st["link_url"] == "/link/cairn"
