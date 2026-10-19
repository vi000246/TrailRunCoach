"""
SP-533: multi-day GPX day splits, the basics.

  * a waypoint is matched to EVERY pass of the track (an out-and-back passes the same hut twice:
    both passes are day-end candidates);
  * camp / hut waypoints are candidates only — never day ends by themselves, whatever their count;
    the trip's days are never rewritten by the split points (the race calculator and the season
    plan's event follow the same rule: the user's points when they make n days, else equal km, 推估);
  * a multi-day event with a GPX (stored, uploaded right after the save, or to come) is saved without
    typing each day.

Synthetic tracks only; tmp DB / folders.
"""
import math

import pytest

from backend.engine import event_gpx as EG
from backend.engine import planning as P
from backend.engine import race_feasibility as F
from backend.engine.panels import race_refs as RR
from backend.engine.racepower import course as CO
from backend.engine.racepower import gpx as GPX

M_PER_DEG = math.pi / 180 * CO.EARTH_R


def out_and_back(out_km=11.0, step_m=20.0, huts=(("排雲山莊", 8.0),), lat0=24.0, lon0=121.0):
    """North `out_km` (climbing 100 m / km), then back south the same way; `huts` = (name, km out)
    waypoints on the line (passed going out and again coming back)."""
    n = int(out_km * 1000 / step_m)
    xs = [i * step_m for i in range(n + 1)]
    xs = xs + xs[-2::-1]
    lat = [lat0 + x / M_PER_DEG for x in xs]
    ele = [1500.0 + 0.1 * x for x in xs]
    wpts = [{"name": nm, "lat": lat0 + k * 1000 / M_PER_DEG, "lon": lon0} for nm, k in huts]
    return GPX.Track(lat, [lon0] * len(lat), ele, None, wpts, "out-and-back")


def line(km=24.0, step_m=20.0, wpts=(), lat0=24.0, lon0=121.0, climb_per_km=60.0):
    n = int(km * 1000 / step_m)
    xs = [i * step_m for i in range(n + 1)]
    w = [{"name": nm, "lat": lat0 + k * 1000 / M_PER_DEG, "lon": lon0 + east_m / M_PER_DEG / math.cos(math.radians(lat0))}
         for nm, k, east_m in wpts]
    return GPX.Track([lat0 + x / M_PER_DEG for x in xs], [lon0] * (n + 1),
                     [1000.0 + climb_per_km * x / 1000 for x in xs], None, w, "line")


# ---- every pass of a waypoint ---------------------------------------------------------

def test_an_out_and_back_hut_is_a_candidate_on_both_passes():
    c = CO.build_course(out_and_back())
    hut = [w for w in c["wpts"] if w["name"] == "排雲山莊"]
    assert [w["km"] for w in hut] == [pytest.approx(8.0, abs=0.05), pytest.approx(14.0, abs=0.05)]
    assert [(w["pass"], w["passes"]) for w in hut] == [(1, 2), (2, 2)]
    assert all(w["camp"] for w in hut)                                # 山莊: a camp-like name → candidate


def test_passes_far_and_near():
    tr = line(wpts=(("遠方山屋", 10.0, 800.0), ("步道旁營地", 12.0, 120.0)))
    c = CO.build_course(tr)
    assert [w["name"] for w in c["wpts"]] == ["步道旁營地"]                 # > 500 m off the track: not on the route
    assert c["wpts"][0]["km"] == pytest.approx(12.0, abs=0.05) and c["wpts"][0]["passes"] == 1
    # the turnaround of an out-and-back is one pass, not two
    c = CO.build_course(out_and_back(huts=(("山頂", 11.0),)))
    assert [w["km"] for w in c["wpts"]] == [pytest.approx(11.0, abs=0.05)]


@pytest.fixture
def store(tmp_path, monkeypatch):
    db, root = tmp_path / "app.db", tmp_path / "gpx"
    monkeypatch.setattr(EG, "_default_db", lambda: db)
    monkeypatch.setattr(EG, "ROOT", root)
    EG._memo.clear()
    return db, root


def test_the_event_keeps_both_passes_as_candidates(store):
    EG.save("yu", GPX.write_gpx(out_and_back()).encode(), "yu.gpx")
    row = EG.get("yu")
    assert row["camp_km"] == [pytest.approx(8.0, abs=0.05), pytest.approx(14.0, abs=0.05)]
    # 3 days, 2 camp passes: they fit — still only candidates (SP-522 Q1): equal km until the user picks
    s = EG.day_stats("yu", 3)
    assert s["split_source"] == "equal" and s["candidates_km"] == row["camp_km"]
    EG.set_splits("yu", row["camp_km"])                               # the user picks both passes
    s = EG.day_stats("yu", 3)
    assert s["split_source"] == "stored"
    assert [round(d["km"], 1) for d in s["days"]] == [8.0, 6.0, 8.0]


# ---- the day count is never rewritten ------------------------------------------------------

def test_day_cuts_fit_the_days_or_go_equal():
    assert CO.day_cuts([8.0, 14.0], 22.0, 3) == ([8.0, 14.0], "stored")
    cuts, src = CO.day_cuts([5.0, 8.0, 14.0], 22.0, 3)               # 3 camps, 3 days: not 4 days
    assert src == "equal" and len(cuts) == 2 and cuts[0] == pytest.approx(22 / 3)
    assert CO.day_cuts([8.0], 22.0, 1) == ([], "single")              # a day trip with a 「營地」 waypoint
    assert CO.day_cuts([0.0, 22.0, 30.0], 22.0, 2)[1] == "equal"      # points off the course don't count


@pytest.fixture()
def client(monkeypatch):
    from backend.tests.test_racepower_v2 import BT, GM, RE0, fake_inputs
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: fake_inputs())
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": []})
    monkeypatch.setattr(RP, "_trail_hr", lambda: None)
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": False, "hike": False}, False))
    RP._courses.clear()
    app = FastAPI()
    app.include_router(RP.router)
    return TestClient(app)


def _texts(j):
    return [w["text"] for w in j["warnings"]]


def test_the_calculator_keeps_the_days_and_says_when_it_splits_equally(client):
    tr = line(km=24.0, wpts=(("營地 A", 6.0, 0.0), ("山屋 B", 12.0, 0.0), ("營地 C", 18.0, 0.0)))
    up = client.post("/api/v1/racepower/course", files={"file": ("t.gpx", GPX.write_gpx(tr).encode(), "application/gpx+xml")})
    cid = up.json()["course_id"]
    camps = [w["km"] for w in up.json()["wpts"] if w["camp"]]
    assert len(camps) == 3
    base = {"type": "baiyue", "course": {"course_id": cid}, "start_time": "06:00"}
    # 3 camp waypoints sent as day ends of a 3-day trip: still 3 days (no silent 4th day), equal km, 推估
    j = client.post("/api/v1/racepower/plan", json={**base, "days": 3, "day_splits_km": camps}).json()
    assert len(j["days"]) == 3 and j["v1"]["result"]["days"].__len__() == 3
    assert any("對不上" in t and "推估" in t for t in _texts(j))
    # no day ends: equal km, said so
    j = client.post("/api/v1/racepower/plan", json={**base, "days": 3}).json()
    assert len(j["days"]) == 3 and any("還沒有分日點" in t for t in _texts(j))
    # two of them picked for 3 days: used, nothing to say
    j = client.post("/api/v1/racepower/plan", json={**base, "days": 3, "day_splits_km": camps[:2]}).json()
    assert len(j["days"]) == 3 and not any("分日點" in t for t in _texts(j))
    km = [d["km"] for d in j["v1"]["result"]["days"]]
    assert km == [pytest.approx(6.0, abs=0.1), pytest.approx(6.0, abs=0.1), pytest.approx(12.0, abs=0.1)]
    # a day trip with a camp-like waypoint (北插天山's 「營地（衫木林）」): one day
    j = client.post("/api/v1/racepower/plan", json={**base, "days": 1}).json()
    assert len(j["days"]) == 1
    # the API before SP-533: points without `days` → one day more than the points
    j = client.post("/api/v1/racepower/plan", json={**base, "day_splits_km": [12.0]}).json()
    assert len(j["days"]) == 2


def test_the_calculator_page_never_applies_camps_by_itself():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "static" / "racepower.html").read_text("utf-8")
    assert "SPLITS = (c.wpts" not in html                             # the old upload: every camp a cut
    assert "renderSplitCands" in html and 'id="split-cands"' in html


def test_the_calculator_and_the_event_agree(store, client, monkeypatch):
    """Same GPX, same 3 days, the same 3 camp waypoints: both cut equal km (no 4th day)."""
    from backend.api import racepower as RP
    tr = line(km=24.0, wpts=(("營地 A", 6.0, 0.0), ("山屋 B", 12.0, 0.0), ("營地 C", 18.0, 0.0)))
    EG.save("t3", GPX.write_gpx(tr).encode(), "t.gpx")
    ev = P.Event("t3", "三天", "2099-01-10", kind="baiyue", days=3)
    monkeypatch.setattr(RP, "_event", lambda eid: ev)
    c = client.post("/api/v1/racepower/course/event/t3", json={}).json()
    assert c["day_splits_km"] == []                                   # no day ends of the user's
    j = client.post("/api/v1/racepower/plan", json={"type": "baiyue", "days": 3, "day_splits_km": c["day_splits_km"],
                                                     "course": {"course_id": c["course_id"], "event_id": "t3"}}).json()
    calc = [round(d["km"], 1) for d in j["v1"]["result"]["days"]]
    event = [round(d["km"], 1) for d in EG.day_stats("t3", 3)["days"]]
    assert calc == event == [8.0, 8.0, 8.0]


# ---- the season plan: a GPX instead of typing each day ---------------------------------

def test_a_trip_with_a_gpx_is_saved_without_its_days():
    e = P.Plan().upsert_event({"name": "大小霸", "date": "2027-01-10", "kind": "baiyue", "days": 3}, gpx=True)
    assert e.day_plan is None and e.day_plan_missing                 # its days: the GPX's (equal until its day ends)
    with pytest.raises(P.EventError, match="第 2 天"):                 # rows filled in part are still checked
        P.Plan().upsert_event({"name": "x", "date": "2027-01-10", "kind": "baiyue", "days": 3, "day_plan": [
            {"km": 20, "gain_m": 1500}, {"km": 15, "gain_m": None}, {"km": 30, "gain_m": 400}]}, gpx=True)
    with pytest.raises(P.EventError):                                  # no GPX: as before (SP-114)
        P.Plan().upsert_event({"name": "x", "date": "2027-01-10", "kind": "baiyue", "days": 3})


def test_the_api_lets_a_gpx_trip_through_and_fills_its_totals(store, tmp_path, monkeypatch):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from backend.api import plan as A
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    blank = [{"km": None, "gain_m": None, "loss_m": None}] * 3
    # the page uploads the GPX right after the save
    r = A.put_event(A.EventIn(id="dxb", name="大小霸", date="2027-01-10", kind="baiyue", days=3, day_plan=blank,
                              gpx_pending=True))
    assert r["event"]["day_plan"] is None and r["event"]["distance_km"] is None
    app = FastAPI()
    app.include_router(A.router)
    api = TestClient(app)
    tr = line(km=24.0, wpts=(("營地 A", 6.0, 0.0), ("山屋 B", 12.0, 0.0)))
    up = api.post("/api/v1/plan/events/dxb/gpx", files={"file": ("t.gpx", GPX.write_gpx(tr).encode())})
    assert up.status_code == 200 and up.json()["days"]["split_source"] == "equal"
    e = P.Plan.load().events[0]
    assert e.distance_km == pytest.approx(24.0, abs=0.1) and e.climbing_m == pytest.approx(1440, rel=0.05)
    # editing it later: the stored GPX lets the blank rows through without the flag
    r = A.put_event(A.EventIn(id="dxb", name="大小霸", date="2027-01-10", kind="baiyue", days=3, day_plan=blank,
                              distance_km=24.0, climbing_m=1440))
    assert r["event"]["day_plan"] is None
    # the user's own totals are never overwritten by a GPX
    A.put_event(A.EventIn(id="dxb", name="大小霸", date="2027-01-10", kind="baiyue", days=3, day_plan=blank,
                          distance_km=30.0, climbing_m=2000))
    api.post("/api/v1/plan/events/dxb/gpx", files={"file": ("t.gpx", GPX.write_gpx(tr).encode())})
    e = P.Plan.load().events[0]
    assert (e.distance_km, e.climbing_m) == (30.0, 2000)
    # a trip without a GPX and without the flag: as before
    with pytest.raises(HTTPException) as x:
        A.put_event(A.EventIn(id="n", name="合歡北西", date="2027-02-10", kind="baiyue", days=2, day_plan=blank[:2]))
    assert x.value.status_code == 400


def test_a_gpx_trip_without_day_ends_is_hinted_as_an_estimate(store):
    tr = line(km=24.0, wpts=(("營地 A", 6.0, 0.0), ("山屋 B", 12.0, 0.0)))
    EG.save("g3", GPX.write_gpx(tr).encode(), "g3.gpx")
    e = P.Plan().upsert_event({"id": "g3", "name": "三天", "date": "2027-01-10", "kind": "baiyue", "days": 3,
                               "est_hours": 21.0}, gpx=True)
    c = RR.course_of(e)
    assert c["split_source"] == "equal" and c["filename"] == "g3.gpx"
    ln = RR.race_line(e, None, "x", c)
    note = F.split_note(ln)
    assert "GPX 還沒有分日點" in note and "推估" in note and "候選" in note
    EG.set_splits("g3", [6.0, 12.0])
    c = RR.course_of(e)
    assert c["split_source"] == "stored" and F.split_note(RR.race_line(e, None, "x", c)) is None


def test_the_plan_page_offers_a_gpx_instead_of_the_rows():
    from pathlib import Path
    # SP-691: the event editor moved from the season plan page to 賽事管理
    html = (Path(__file__).resolve().parents[1] / "static" / "races.html").read_text("utf-8")
    assert 'id="evgpxfile"' in html and 'id="evgpxlater"' in html and "gpx_pending" in html
