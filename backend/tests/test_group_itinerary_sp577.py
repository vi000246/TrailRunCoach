"""
SP-577 (跟團 G1): the Cairn import reads the timetable (each point's time, stop and who wrote it, on our
km) and asks once whether it is the group's — ticked when the data says so — and keeps the answer
with the trip. A trip without times imports only the route, as before (SP-537).

Signals (SP-575 §1.2): ≥ 2 typed times besides the start on a day = a timetable typed in; the route
recruiting in Cairn (`meta.recruit`) = a weak one; a route made before 2026-08-11 (Cairn wrote every
older time as `user`) can't tell — never ticked by itself. The fake Cairn of test_cairn_import_sp537.
"""
import json
from pathlib import Path

import pytest

from backend.engine import cairn as CA
from backend.engine import event_gpx as EG
from backend.tests.test_cairn_import_sp537 import (GID, TOKEN, calc_client, cairn, line_track, plan_client,  # noqa: F401
                                                   poi, store)
from backend.tests.test_group_itinerary_sp576 import GROUP, tv

ROOT = Path(__file__).resolve().parents[2]


def sig(pois, **meta):
    return CA.signals(CA.schedule_visits(pois), meta)


def test_the_point_times_stops_and_sources_are_read_on_our_km():
    s = CA.schedule_of(GROUP, {"uploaded_at": "2026-09-01 10:00:00"}, line_track())
    p = {x["name"]: x for x in s["points"]}
    assert (p["水池"]["eta"], p["水池"]["rest_min"], p["水池"]["src"], p["水池"]["kind"]) == ("08:30", 20, "user", "pass")
    assert p["水池"]["km"] == pytest.approx(15.0, abs=0.05) and p["終點"]["km"] == pytest.approx(30.0, abs=0.05)
    # Cairn's own km picks the pass (an out-and-back's way back) and is scaled to our total
    oab = [poi("a", "登山口", 0.0, [tv(0, 1, "depart", "06:00")]),
           poi("b", "營地", 6.0, [tv(1, 1, "pass", "08:00", dist_m=6100), tv(2, 1, "pass", "13:00", dist_m=24200)]),
           poi("c", "山頂", 15.0, [tv(3, 1, "pass", "10:30")])]
    s2 = CA.schedule_of(oab, {"distance_m": 30500}, line_track())
    assert [(x["name"], round(x["km"])) for x in s2["points"]] == [("登山口", 0), ("營地", 6), ("山頂", 15)]
    # this line goes one way: the 營地's second time (Cairn km 24.2) has only the 6 km pass here, which
    # would go back along the track — left out and named
    assert s2["skipped"] == ["營地"]


def test_the_signals_and_the_suggestion():
    g = sig(GROUP, uploaded_at="2026-09-01 10:00:00")
    assert g["user_days"] == [1, 2] and g["n"] == 8 and g["n_user"] == 8 and g["suggest"] is True and not g["legacy"]
    # Cairn's own estimates (only the start typed): not a timetable
    est = [poi(x["id"], x["name"], 0, [{**v, "etaSource": "user" if v["kind"] == "depart" else "estimate"}
                                        for v in x["visits"]]) for x in GROUP]
    g2 = sig(est, uploaded_at="2026-09-01")
    assert g2["user_days"] == [] and g2["n_estimate"] == 5 and g2["suggest"] is False
    # recruiting: a weak signal, enough to tick
    assert sig(est, uploaded_at="2026-09-01", recruit={"url": "https://…"})["suggest"] is True
    # before 2026-08-11 every time reads `user`: can't tell, not ticked by itself (recruiting or not)
    old = sig(GROUP, uploaded_at="2026-07-30 08:00:00", recruit={"url": "x"})
    assert old["legacy"] is True and old["suggest"] is False
    assert sig(GROUP)["legacy"] is False                                   # no date: not called legacy


def test_the_import_asks_with_the_suggestion(cairn, plan_client):  # noqa: F811
    url = "/api/v1/plan/events/jm/gpx/cairn"                            # SP-699: 賽事管理's import only
    cairn.pois = GROUP
    cairn.meta = {**cairn.meta, "uploaded_at": "2026-07-01 08:00:00"}
    sc = plan_client.post(url, json={"url": cairn.link}).json()["cairn"]["schedule"]
    assert sc["group"] is False and sc["signals"]["legacy"] is True
    cairn.meta = {**cairn.meta, "uploaded_at": "2026-09-01 08:00:00"}
    sc = plan_client.post(url, json={"url": cairn.link}).json()["cairn"]["schedule"]
    assert sc["group"] is True and sc["signals"]["user_days"] == [1, 2]
    assert [round(p["km"], 1) for p in sc["points"]] == [0.0, 4.0, 9.5, 9.5, 15.0, 21.0, 21.0, 30.0]


def test_a_trip_without_times_imports_only_the_route(cairn, plan_client):  # noqa: F811
    j = plan_client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link}).json()   # SP-537's trip
    assert j["cairn"]["schedule"] is None and j["event_days"] == 3
    assert "schedule" not in (EG.get("jm")["source"] or {})


def test_the_answer_is_kept_with_the_event_and_can_be_changed(cairn, plan_client, store):  # noqa: F811
    cairn.pois = GROUP
    cairn.meta = {**cairn.meta, "uploaded_at": "2026-09-01 08:00:00"}
    r = plan_client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link})
    assert r.status_code == 200 and TOKEN not in r.text
    src = EG.get("jm")["source"]
    assert src["schedule"]["group"] is True and len(src["schedule"]["points"]) == 8
    assert src["schedule"]["points"][4] == {"name": "水池", "day": 2, "eta": "08:30", "rest_min": 20, "src": "user",
                                            "kind": "pass", "km": pytest.approx(15.0, abs=0.05)}
    # the calculator's event course gives it back with the legs and the group's moving time
    c = plan_client.post("/api/v1/racepower/course/event/jm", json={}).json()
    assert c["schedule"]["group"] is True and c["schedule"]["moving_s"] > 0 and len(c["schedule"]["legs"]) == 5
    # the user unticks it: kept
    r = plan_client.put("/api/v1/plan/events/jm/gpx/group", json={"group": False})
    assert r.status_code == 200 and EG.get("jm")["source"]["schedule"]["group"] is False
    assert EG.get("jm")["source"]["gpx_id"] == GID                       # the rest of the source is kept
    assert TOKEN not in (store / "app.db").read_bytes().decode("latin-1")
    assert plan_client.put("/api/v1/plan/events/nope/gpx/group", json={"group": True}).status_code == 404


def test_the_kept_timetable_is_cleaned():
    sc = CA.clean_schedule({"points": [{"name": "a" * 99, "day": 1, "eta": "07:00", "km": 0, "share": TOKEN},
                                       {"name": "b", "day": 1, "eta": "25:00", "km": 2},
                                       {"name": "c", "day": 1, "eta": "09:00", "km": 3, "rest_min": -5, "src": "x"}],
                            "signals": {"n": 3, "suggest": 1, "evil": "<script>"}, "group": "yes", "token": TOKEN})
    assert TOKEN not in json.dumps(sc)
    assert [p["name"] for p in sc["points"]] == ["a" * 60, "c"]
    assert sc["points"][1]["rest_min"] == 0 and sc["points"][1]["src"] == "user"
    assert sc["signals"] == {"n": 3, "recruit": False, "legacy": False, "suggest": True, "user_days": []}
    assert sc["group"] is True
    assert CA.clean_schedule({"points": [{"day": 1, "eta": "07:00", "km": 0}]}) is None


def test_the_page_asks_once_and_keeps_the_answer():
    # SP-699: asked in 賽事管理's editor popup with the Cairn import; 賽事計算機 only says what it uses
    races = (ROOT / "backend/static/races.html").read_text("utf-8")
    assert 'id="ed-grp"' in races and "function renderGroupAsk" in races and "/gpx/group" in races
    calc = (ROOT / "backend/static/racepower.html").read_text("utf-8")
    assert 'id="grp-ask"' not in calc and "function setGroup" not in calc and "/gpx/group" not in calc
    assert 'id="grp-note"' in calc and "function renderGroupNote" in calc and "group: c.schedule || null" in calc
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / f"backend/static/i18n/{loc}/races.json").read_text("utf-8"))
        assert all(cat[f"grp.{k}"] for k in ("ask", "sub", "tip", "sig_user", "sig_estimate", "sig_recruit", "sig_legacy"))
        rp = json.loads((ROOT / f"backend/static/i18n/{loc}/racepower.json").read_text("utf-8"))
        assert all(rp[f"grp.{k}"] for k in ("note_on", "note_off", "manage"))
    # the words are plain: no 「這是團的時刻表（跟團對照）」 any more
    zh = json.loads((ROOT / "backend/static/i18n/zh-TW/races.json").read_text("utf-8"))
    assert "登山社" in zh["grp.ask"] and "跟不跟得上" in zh["grp.ask"] and "時刻表" not in zh["grp.ask"]
