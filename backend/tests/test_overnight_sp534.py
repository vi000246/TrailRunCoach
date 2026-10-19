"""
SP-534: the nights of a recorded multi-day track (racepower/overnight.py) — a stop ≥ 6 h (推估) or
a recording gap ≥ 6 h is a night; its arrival km is a day end; the pages are only given the
proposal (they ask before applying it, SP-522 Q1). Synthetic timed tracks only.
"""
import math
import random

import pytest

from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import overnight as OV

M = math.pi / 180 * 6371008.8


def walk(plan, step_s=30.0, speed_kmh=3.0, seed=1, lat0=24.3, lon0=121.3):
    """plan = [("walk", km) | ("stay", hours) | ("off", hours)]; northward at speed_kmh, a point every
    step_s; a stay wobbles a little (GPS at camp), an "off" records nothing (the watch was off). The
    wobble adds no distance (SP-566: a stop counts only its straight line)."""
    rnd = random.Random(seed)
    lat, lon, ele, t = [], [], [], []
    x, now = 0.0, 0.0

    def put(dx=0.0, dy=0.0):
        lat.append(lat0 + (x + dy) / M)
        lon.append(lon0 + dx / M / math.cos(math.radians(lat0)))
        ele.append(2000.0 + 0.05 * x)
        t.append(now)
    put()
    for kind, v in plan:
        if kind == "walk":
            n = int(v * 1000 / (speed_kmh / 3.6 * step_s))
            for _ in range(n):
                x += speed_kmh / 3.6 * step_s
                now += step_s
                put()
        elif kind == "stay":
            # a watch at rest records seldom (smart recording): a point every 5 min, ± 4 m of GPS wobble
            for _ in range(int(v * 3600 / 300)):
                now += 300
                put(rnd.uniform(-4, 4), rnd.uniform(-4, 4))
        else:
            now += v * 3600
            put()
    return GPX.Track(lat, lon, ele, t, [], "trip")


THREE_DAYS = [("walk", 6), ("stay", 1.5), ("walk", 4), ("stay", 13), ("walk", 9), ("stay", 1),
              ("walk", 3), ("stay", 12), ("walk", 14)]


def test_two_nights_three_days():
    r = OV.find(walk(THREE_DAYS))
    assert r["days"] == 3 and [s["kind"] for s in r["stops"]] == ["stay", "stay"]
    # the 2nd: 22 km walked — the 1st night's GPS wobble (~0.7 km) no longer adds to it (SP-566)
    assert r["splits_km"] == [pytest.approx(10.0, abs=0.15), pytest.approx(22.0, abs=0.15)]
    assert r["stops"][0]["hours"] == pytest.approx(13, abs=0.2) and r["min_stop_h"] == 6.0
    # the lunch (1.5 h) and the summit rest (1 h) are not nights


def test_the_watch_off_overnight_is_a_night():
    r = OV.find(walk([("walk", 8), ("off", 11), ("walk", 7)]))
    assert r["days"] == 2 and r["stops"][0]["kind"] == "gap"
    assert r["splits_km"] == [pytest.approx(8.0, abs=0.15)]


def test_nothing_to_find():
    assert OV.find(walk([("walk", 10), ("stay", 2), ("walk", 10)]))["stops"] == []      # a day trip
    no_time = walk([("walk", 5)])
    no_time.time = None
    assert OV.find(no_time) is None                                              # a drawn route
    # waiting 7 h at the trailhead before starting splits nothing
    assert OV.find(walk([("stay", 7), ("walk", 10)]))["stops"] == []


def test_a_gpx_file_round_trip_keeps_the_nights():
    tr = walk(THREE_DAYS, step_s=60.0)
    back = GPX.parse(GPX.write_gpx(tr).encode())
    # the file rounds the positions: a stop's edge may move by a point (a few m, SP-566)
    assert OV.find(back)["splits_km"] == [pytest.approx(x, abs=0.03) for x in OV.find(tr)["splits_km"]]


# ---- the APIs hand the proposal over, never apply it ----------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    from backend.engine import event_gpx as EG
    db, root = tmp_path / "app.db", tmp_path / "gpx"
    monkeypatch.setattr(EG, "_default_db", lambda: db)
    monkeypatch.setattr(EG, "ROOT", root)
    EG._memo.clear()
    return db, root


def test_the_plan_upload_proposes_and_stores_nothing(store, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as A
    from backend.engine import event_gpx as EG
    from backend.engine import planning as P
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    A.put_event(A.EventIn(id="trip", name="三天", date="2027-01-10", kind="baiyue", days=3, gpx_pending=True))
    app = FastAPI()
    app.include_router(A.router)
    api = TestClient(app)
    data = GPX.write_gpx(walk(THREE_DAYS, step_s=60.0)).encode()
    r = api.post("/api/v1/plan/events/trip/gpx", files={"file": ("trip.gpx", data)}).json()
    assert r["overnights"]["days"] == 3 and len(r["overnights"]["splits_km"]) == 2
    assert r["days"]["split_source"] == "equal" and not EG.get("trip")["day_splits"]     # asked, not applied
    # the page's 「套用」: the day ends stored as the user's
    r = api.put("/api/v1/plan/events/trip/gpx/splits", json={"day_splits_km": r["overnights"]["splits_km"]}).json()
    assert r["days"]["split_source"] == "stored"
    km = [d["km"] for d in r["days"]["days"]]
    assert km[0] == pytest.approx(10, abs=0.2) and 12 < km[1] < 13.5 and 14 < km[2] < 15.5


def test_the_calculator_upload_proposes(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    from backend.tests.test_racepower_v2 import BT, GM, RE0, fake_inputs
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: fake_inputs())
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": []})
    monkeypatch.setattr(RP, "_trail_hr", lambda: None)
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": False, "hike": False}, False))
    app = FastAPI()
    app.include_router(RP.router)
    c = TestClient(app)
    data = GPX.write_gpx(walk(THREE_DAYS, step_s=60.0)).encode()
    up = c.post("/api/v1/racepower/course", files={"file": ("trip.gpx", data)}).json()
    assert up["overnights"]["days"] == 3
    drawn = GPX.Track([24.0, 24.01, 24.02], [121.0] * 3, [100.0, 150.0, 120.0])
    up = c.post("/api/v1/racepower/course", files={"file": ("r.gpx", GPX.write_gpx(drawn).encode())}).json()
    assert up["overnights"] is None


def test_the_pages_ask_before_applying():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "static"
    rp = (root / "racepower.html").read_text("utf-8")
    assert 'id="overnight-ask"' in rp and "overnight.apply" in rp and "overnight.no" in rp
    plan = (root / "races.html").read_text("utf-8")      # SP-691: the event's GPX is uploaded in 賽事管理
    assert "askOvernights" in plan and "confirm(" in plan
