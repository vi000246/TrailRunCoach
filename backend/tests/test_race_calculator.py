"""
賽事計算機 (the race calculator page): saved inputs per event (engine/race_calc_store.py,
/saved/{id}), the main chart's per-segment targets (seg_targets.chart_rows), the watch
export (racepower/watch_export.py → the workout provider, COROS faked) and the race-day
weather falling back to the event GPX's start. Synthetic data only; no network
(Open-Meteo and COROS are faked).
"""
import math

import httpx
import pytest

from backend.engine import race_calc_store as RC
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import seg_targets as ST
from backend.engine.racepower import watch_export as WE
from backend.tests.test_racepower_v2 import client, fake_inputs, synthetic_track  # noqa: F401  (fixture)

approx = pytest.approx


# ---- saved inputs + result ------------------------------------------------------

def test_store_round_trip_update_and_delete(tmp_path):
    db = tmp_path / "app.db"
    assert RC.get("ev1", db) is None                                   # no DB file yet
    r = RC.save("ev1", {"form": {"dist": "21.1"}, "trip": "solo"}, {"summary": {"time_s": 7200}}, db)
    assert r["inputs"]["trip"] == "solo" and r["result"]["summary"]["time_s"] == 7200 and r["saved_at"]
    # inputs only: the stored result stays
    r2 = RC.save("ev1", {"form": {"dist": "42.2"}}, None, db)
    assert r2["inputs"]["form"]["dist"] == "42.2" and r2["result"]["summary"]["time_s"] == 7200
    assert RC.get("ev2", db) is None
    assert RC.delete("ev1", db) and RC.get("ev1", db) is None and not RC.delete("ev1", db)
    with pytest.raises(RC.RaceCalcError):
        RC.save("../x", {}, None, db)
    with pytest.raises(RC.RaceCalcError):
        RC.save("ev1", {"x": "a" * (RC.MAX_BYTES + 1)}, None, db)


def test_model_creates_the_same_table(tmp_path):
    """The ORM model and the store's DDL agree: create_all makes race_calc, the store writes it."""
    import sqlite3

    from sqlalchemy import create_engine

    from backend.db.models import Base
    p = tmp_path / "orm.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{p}"))
    cols = {r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(race_calc)")}
    assert {"event_id", "inputs_json", "result_json", "saved_at"} <= cols
    RC.save("e1", {"a": 1}, {"b": 2}, p)
    assert RC.get("e1", p)["result"] == {"b": 2}


def test_saved_api(client, monkeypatch, tmp_path):   # noqa: F811
    from backend.api import racepower as RP
    monkeypatch.setattr(RC, "_default_db", lambda: tmp_path / "app.db")
    ev = type("E", (), {"id": "race1", "name": "測試越野", "date": "2099-05-01", "days": 1})()
    def _event(eid):
        if eid != "race1":
            raise RP.HTTPException(404, "no event")
        return ev
    monkeypatch.setattr(RP, "_event", _event)
    assert client.get("/api/v1/racepower/saved/race1").json() == {"saved": None}
    r = client.put("/api/v1/racepower/saved/race1", json={"inputs": {"type": "trail"}, "result": {"summary": {}}})
    assert r.status_code == 200 and r.json()["saved_at"]
    got = client.get("/api/v1/racepower/saved/race1").json()["saved"]
    assert got["inputs"] == {"type": "trail"} and got["result"] == {"summary": {}}
    assert client.put("/api/v1/racepower/saved/nope", json={"inputs": {}}).status_code == 404
    assert client.delete("/api/v1/racepower/saved/race1").json() == {"deleted": True}


# ---- the main chart's rows ---------------------------------------------------------

def _hilly_gpx():
    # 2 km flat, 3 km at 12 % (walked), 3 km at -10 %, 2 km at 5 %, 2 km flat
    def z(x):
        if x < 2000:
            return 500.0
        if x < 5000:
            return 500 + (x - 2000) * 0.12
        if x < 8000:
            return 860 - (x - 5000) * 0.10
        if x < 10000:
            return 560 + (x - 8000) * 0.05
        return 660.0
    return GPX.write_gpx(synthetic_track({"len": 12000, "z": z})).encode()


def _upload(client):   # noqa: F811
    up = client.post("/api/v1/racepower/course", files={"file": ("t.gpx", _hilly_gpx(), "application/gpx+xml")})
    assert up.status_code == 200, up.text
    return up.json()


def test_course_reports_its_start(client):   # noqa: F811
    c = _upload(client)
    assert c["start"]["lat"] == approx(24.0) and c["start"]["lon"] == approx(121.0) and c["start"]["z"] == approx(500, abs=5)


def test_chart_rows_trail_power_only_where_valid(client):   # noqa: F811
    cid = _upload(client)["course_id"]
    p = client.post("/api/v1/racepower/plan", json={"type": "trail", "course": {"course_id": cid}}).json()
    rows = p["chart_rows"]
    assert len(rows) == len(p["segments"]) and rows[0]["start_km"] == 0
    for r in rows:
        assert r["pace_s_per_km"] > 0 and r["t"] > 0
        g = r["grade"]
        if g > 0.08 or g <= -0.03:
            assert r["power"] is None                       # steep / descent: Stryd not a target
        else:
            assert r["power"] and r["power_band"][0] < r["power"] < r["power_band"][1]
        if g <= -0.03:
            assert r["hr_cap"] is None and r["basis"] == "safe"
        else:
            assert r["hr_cap"]                              # LTHR / AeT cap
    steep = [r for r in rows if r["grade"] > 0.08]
    assert steep and all(r["walk"] and r["basis"] == "hr" for r in steep)


def test_chart_rows_road_and_hike():
    seg = lambda i, a, b, g, **kw: {"i": i, "start_km": a, "end_km": b, "dist_m": (b - a) * 1000, "grade": g,
                                    "t": (b - a) * 300, "gain_m": 0, "loss_m": 0, **kw}
    road = {"type": "road", "used": {"cp": {"value": 300}}, "summary": {"time_s": 3000},
            "segments": [seg(1, 0, 5, 0.0, power=280, pace_s_per_km=240), seg(2, 5, 10, -0.04, power=250, pace_s_per_km=230)]}
    rows = ST.chart_rows(road, aet=150, lthr=170)
    assert [r["power"] for r in rows] == [280, 250] and all(r["hr_cap"] == 170 for r in rows)
    assert all(r["basis"] == "power" for r in rows)
    long_road = {**road, "summary": {"time_s": 4 * 3600}}
    assert ST.chart_rows(long_road, aet=150, lthr=170)[0]["hr_cap"] == 150          # > 3 h: AeT
    hike = {"type": "baiyue", "used": {}, "summary": {"hr_cap": 145, "time_s": 20000},
            "segments": [seg(1, 0, 4, 0.15, speed_kmh=2.0), seg(2, 4, 8, -0.15, speed_kmh=3.0)]}
    ST.plan_targets(hike, aet=145)
    hr = ST.chart_rows(hike, aet=145)
    assert hr[0]["power"] is None and hr[0]["hr_cap"] == 145 and hr[0]["pace_s_per_km"] == approx(1800)
    assert hr[1]["hr_cap"] is None


# ---- watch export ------------------------------------------------------------------

def _rows(n, km=1.0, grade=0.0, basis="power", power=250.0):
    return [{"start_km": i * km, "end_km": (i + 1) * km, "dist_m": km * 1000, "t": km * 300, "grade": grade,
             "gain_m": max(0, grade) * km * 1000, "loss_m": max(0, -grade) * km * 1000,
             "basis": basis, "power": power if basis == "power" else None, "hr_cap": 160.0} for i in range(n)]


def test_lap_steps_end_at_aid_stations_and_name_the_landmark():
    rows = _rows(6, grade=0.06) + [dict(r, start_km=r["start_km"] + 6, end_km=r["end_km"] + 6) for r in _rows(4, grade=-0.10, basis="safe")]
    out = WE.steps_for({"type": "trail"}, rows, stops=[{"km": 3.0, "type": "aid", "name": ""}, {"km": 8.0, "type": "water", "name": "CP2"}])
    assert out["mode"] == "lap"
    legs = out["legs"]
    ends = [round(lg["end_km"], 2) for lg in legs]
    assert 3.0 in ends and 8.0 in ends and ends[-1] == 10.0
    assert legs[0]["name"].startswith("→ 補給站 1 · 約 15 分") and "爬 180 m" in legs[0]["name"]
    assert any(lg["name"].startswith("→ CP2") for lg in legs)
    assert legs[-1]["name"].startswith("→ 終點")
    climb = legs[0]
    assert climb["basis"] == "power" and climb["target"]["type"] == "power"
    assert any(lg["basis"] == "safe" and lg["target"] == {"type": "none"} for lg in legs)
    assert all(it["dur"]["type"] == "open" and it["dur"]["est"] > 0 for it in out["doc"]["items"])
    from backend.engine import workout_steps as WS
    WS.normalize(out["doc"])                                      # the editor format accepts it


def test_road_distance_steps_merge_to_the_limit():
    out = WE.steps_for({"type": "road"}, _rows(120, km=0.35))
    assert out["mode"] == "distance" and len(out["legs"]) <= WE.COROS_MAX
    assert out["merged"] == 120 - len(out["legs"]) and any("合併" in n for n in out["notes"])
    assert sum(it["dur"]["value"] for it in out["doc"]["items"]) == approx(42000, abs=60)
    lap = WE.steps_for({"type": "trail"}, _rows(80, km=0.5, grade=0.0))
    assert len(lap["legs"]) <= WE.LAP_MAX


def _app_with_db(client, db):   # noqa: F811
    from backend.api import racepower as RP

    async def _dep():
        yield db
    client.app.dependency_overrides[RP._db] = _dep


def test_export_preview_then_push_is_idempotent_per_event(client, monkeypatch):   # noqa: F811
    from backend.api import racepower as RP
    from backend.sync import coros_workouts as CW
    from backend.sync import http
    from backend.tests.test_coros_workouts import FakeHub, make_db, run
    db = run(make_db())
    _app_with_db(client, db)
    ev = type("E", (), {"id": "race1", "name": "測試越野", "date": "2099-05-01", "days": 1})()
    monkeypatch.setattr(RP, "_event", lambda eid: ev)
    cid = _upload(client)["course_id"]
    body = {"type": "trail", "course": {"course_id": cid}, "date": "2099-05-01", "event_id": "race1",
            "stops": [{"km": 5.0, "type": "aid"}]}
    pv = client.post("/api/v1/racepower/export/coros", json=body)
    assert pv.status_code == 200, pv.text
    j = pv.json()
    assert j["mode"] == "lap" and j["scheduled"] and j["pushed"] is None and j["previous"] is None
    assert j["name"].startswith("TRC 賽事 測試越野") and len(j["legs"]) <= WE.LAP_MAX
    assert any("補給站" in lg["name"] for lg in j["legs"])
    fake = FakeHub()
    with http.use_transport(httpx.MockTransport(fake)):
        r1 = client.post("/api/v1/racepower/export/coros", json={**body, "push": True}).json()
        assert r1["pushed"]["status"] == "pushed" and r1["pushed"]["scheduled"]
        assert [e["happenDay"] for e in fake.entities] == [20990501] and fake.adds() == 1
        ex = list(fake.live().values())[0]["exercises"]
        assert all(e["targetType"] == CW.TARGET_OPEN for e in ex)
        r2 = client.post("/api/v1/racepower/export/coros", json={**body, "push": True}).json()
        assert r2["pushed"]["changed"] is False and fake.adds() == 1          # same plan: nothing re-sent
        r3 = client.post("/api/v1/racepower/export/coros", json={**body, "push": True, "step_mode": "distance"}).json()
        assert r3["pushed"]["status"] == "updated" and len(fake.live()) == 1 and len(fake.entities) == 1
        assert r3["previous"]["coros_program_id"]
    # the race workout is not the week plan's: the plan's push never sees it as stale
    assert run(CW.all_rows(db)) == {}


def test_export_without_a_future_date_goes_to_the_library(client, monkeypatch):   # noqa: F811
    from backend.sync import http
    from backend.tests.test_coros_workouts import FakeHub, make_db, run
    db = run(make_db())
    _app_with_db(client, db)
    body = {"type": "road", "distance_km": 10, "mode": "auto", "date": "2000-01-01", "push": True}
    fake = FakeHub()
    with http.use_transport(httpx.MockTransport(fake)):
        r = client.post("/api/v1/racepower/export/coros", json=body)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["mode"] == "distance" and not j["scheduled"] and j["pushed"]["scheduled"] is False
        assert fake.entities == [] and fake.adds() == 1
        again = client.post("/api/v1/racepower/export/coros", json={**body, "date": None}).json()
        assert again["pushed"]["changed"] is False and len(fake.live()) == 1


# ---- race-day weather location ----------------------------------------------------------

def test_weather_falls_back_to_the_event_gpx_start(client, monkeypatch, tmp_path):   # noqa: F811
    from backend.api import racepower as RP
    from backend.engine import event_gpx as EG
    from backend.engine.racepower import weather as WX
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    EG.save("race1", _hilly_gpx(), "t.gpx")
    ev = type("E", (), {"id": "race1", "name": "某某越野", "date": "2099-05-01", "days": 1})()
    monkeypatch.setattr(RP, "_event", lambda eid: ev)
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"provider": "manual", "label": "x", "values": None, "tried": [], "location": {}, "hourly": None}
    monkeypatch.setattr(WX, "race_conditions", fake)                   # never Open-Meteo
    r = client.get("/api/v1/racepower/weather", params={"event_id": "race1"})
    assert r.status_code == 200, r.text
    assert seen["lat"] == approx(24.0) and seen["lon"] == approx(121.0) and seen["elevation_m"] == approx(500, abs=1)
    assert seen["date"].isoformat() == "2099-05-01"
    assert math.isfinite(seen["lat"])
