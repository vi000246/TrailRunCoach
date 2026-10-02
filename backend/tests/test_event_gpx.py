"""
The GPX stored with a season-plan event (engine/event_gpx.py), its API (api/plan.py),
the race calculator reusing it (api/racepower.py) and the コース定数 reference lines'
multi-day number (engine/panels/race_refs.py). A tiny synthetic GPX; tmp DB and folder.
"""
import datetime as dt
import gzip
import math

import pytest

from backend.engine import event_gpx as EG
from backend.engine.algorithms.chart_metrics import course_constant
from backend.engine.panels import race_refs as RR
from backend.engine.planning import Event, Plan

TODAY = dt.date(2026, 10, 2)


def synth_gpx(legs, step_m=20.0, camps=()) -> bytes:
    """A straight north-going track; legs = [(km, climb m)] (negative = descent),
    camps = km positions of 「營地」 waypoints."""
    lat0, lon0, z = 24.0, 121.0, 1000.0
    pts, d = [(lat0, z)], 0.0
    for km, dz in legs:
        n = int(round(km * 1000 / step_m))
        for _ in range(n):
            d += step_m
            z += dz / n
            pts.append((lat0 + d / 111195.0, z))
    w = "".join(f'<wpt lat="{lat0 + k * 1000 / 111195.0:.7f}" lon="{lon0}"><name>營地{i}</name></wpt>'
                for i, k in enumerate(camps, 1))
    tp = "".join(f'<trkpt lat="{la:.7f}" lon="{lon0}"><ele>{e:.1f}</ele></trkpt>' for la, e in pts)
    return (f'<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">{w}'
            f'<trk><name>synthetic</name><trkseg>{tp}</trkseg></trk></gpx>').encode("utf-8")


# up 1000 m over 6 km, down 400 m over 4 km, up 200 m over 2 km: 12 km ↑1200 ↓400
ONE = synth_gpx([(6, 1000), (4, -400), (2, 200)])


@pytest.fixture
def store(tmp_path, monkeypatch):
    db, root = tmp_path / "app.db", tmp_path / "gpx"
    monkeypatch.setattr(EG, "_default_db", lambda: db)
    monkeypatch.setattr(EG, "ROOT", root)
    EG._memo.clear()
    return db, root


def test_save_stores_gzip_and_the_totals(store):
    db, root = store
    row = EG.save("ev1", ONE, "route.gpx")
    f = root / "ev1.gz"
    assert f.exists() and gzip.decompress(f.read_bytes()) == ONE
    assert row["bytes_gz"] < row["bytes_raw"] / 3                     # small on disk
    assert row["km"] == pytest.approx(12.0, abs=0.1)
    assert row["gain_m"] == pytest.approx(1200, rel=0.05) and row["loss_m"] == pytest.approx(400, rel=0.08)
    assert EG.read_bytes("ev1") == ONE and EG.get("ev1")["filename"] == "route.gpx"
    # replace: one row, the new file
    two = synth_gpx([(5, 500)])
    EG.save("ev1", two, "new.gpx")
    assert list(EG.all_rows()) == ["ev1"] and EG.get("ev1")["km"] == pytest.approx(5.0, abs=0.1)
    assert EG.read_bytes("ev1") == two
    assert EG.delete("ev1") and not f.exists() and EG.get("ev1") is None and not EG.delete("ev1")


def test_bad_file_and_bad_id_write_nothing(store):
    db, root = store
    with pytest.raises(EG.EventGpxError):
        EG.save("ev1", b"<kml></kml>", "x.gpx")
    with pytest.raises(EG.EventGpxError):
        EG.save("../x", ONE, "x.gpx")
    assert not (root / "ev1.gz").exists() and EG.get("ev1") is None


def test_day_stats_splits_stored_then_camps_then_equal(store):
    trip = synth_gpx([(8, 1200), (8, -1200)], camps=[8.0])
    EG.save("h", trip, "trip.gpx")
    s = EG.day_stats("h", 2)
    assert s["split_source"] == "camp" and s["splits_km"] == [pytest.approx(8.0, abs=0.05)]
    d1, d2 = s["days"]
    assert d1["gain_m"] == pytest.approx(1200, rel=0.05) and d1["loss_m"] < 30
    assert d2["loss_m"] == pytest.approx(1200, rel=0.05) and d2["gain_m"] < 30
    assert sum(d["km"] for d in s["days"]) == pytest.approx(s["totals"]["km"])
    EG.set_splits("h", [4.0])
    assert EG.day_stats("h", 2)["split_source"] == "stored" and EG.day_stats("h", 2)["days"][0]["km"] == pytest.approx(4.0, abs=0.05)
    eq = EG.day_stats("h", 4)                                          # 3 cuts needed: neither fits → equal
    assert eq["split_source"] == "equal" and all(d["km"] == pytest.approx(4.0, abs=0.05) for d in eq["days"])
    assert EG.day_stats("h", 1)["split_source"] == "single" and EG.day_stats("missing", 2) is None


# ---- コース定数 reference lines ----------------------------------------------------

def test_single_day_race_uses_the_gpx_descent(store):
    EG.save("a", ONE, "a.gpx")
    a = Event("a", "大霸", "2026-11-28", kind="race", priority="A", distance_km=40, climbing_m=2400)
    r = RR.course_constant_refs(Plan(events=[a]), TODAY, lambda e, c: [3.0])
    ln = r["lines"][0]
    t = EG.day_stats("a", 1)["totals"]
    assert not ln["descent_assumed"] and ln["gpx"] == "a.gpx" and ln["km"] == pytest.approx(t["km"], abs=0.05)
    assert ln["cc"] == pytest.approx(round(course_constant(3.0, t["km"], t["gain_m"], t["loss_m"]), 1))
    assert "multi" not in ln


def test_multi_day_is_one_trip_number_with_the_per_day_breakdown():
    hike = Event("h", "南湖大山", "2026-12-10", kind="baiyue", priority="A", days=3, distance_km=36, climbing_m=3000)
    res = RR.apply({"series": [{"name": "p", "data": {"kind": "points", "points": []}}], "description": "x"},
                   Plan(events=[hike]), TODAY, lambda e, c: [5.0, 8.0, 4.0])
    ln = res["race_ref"]["target"]
    per = [course_constant(h, 12, 1000, 1000) for h in (5.0, 8.0, 4.0)]
    assert ln["multi"] and ln["days"] == 3
    assert ln["cc"] == pytest.approx(round(sum(per), 1), abs=0.15)                   # trip = Σ days (linear)
    assert ln["cc"] == pytest.approx(round(course_constant(17.0, 36, 3000, 3000), 1))  # = the whole-route constant
    assert ln["day_mean"] == pytest.approx(round(ln["cc"] / 3, 1)) and ln["hardest_day"] == 2
    assert ln["taiyokudo"] == math.ceil(ln["cc"] / 10)
    # one line: the per-day average (the single-day target); the trip and its days in the hover
    refs = [s for s in res["series"] if s.get("role") == "race_ref" and s["data"]["kind"] == "hline"]
    assert len(refs) == 1 and refs[0]["name"].startswith("A 南湖大山 每天")
    trip = refs[0]
    assert "第 2 天" in trip["tip"] and "（最難）" in trip["tip"] and "體力度" in trip["tip"]
    assert "信州" in trip["tip"] and "推估" in res["description"]


def test_multi_day_plan_estimate_is_spread_by_km_effort():
    days = [{"day": 1, "km": 10, "gain_m": 1000, "loss_m": 0}, {"day": 2, "km": 10, "gain_m": 0, "loss_m": 1000}]
    hs = RR.spread(9.0, days)
    assert hs == pytest.approx([6.0, 3.0]) and sum(hs) == pytest.approx(9.0)
    stage = Event("s", "分站賽", "2026-12-01", kind="race", priority="B", days=2, distance_km=60, climbing_m=3000,
                  est_hours=10)
    ln = RR.race_line(stage, None, "x")
    assert ln["multi"] and ln["hours"] == pytest.approx(10) and "推估" in ln["time_source"]


def test_taiyokudo_matches_the_nagano_examples():
    # 裏銀座 113.1 → 10, 表銀座 89.2 → 9, 燕→常念 62.0 → 7, 槍ヶ岳 70.3 → 8, 19.8 → 2
    assert [RR.taiyokudo(x) for x in (113.1, 89.2, 62.0, 70.3, 19.8, 8.6)] == [10, 9, 7, 8, 2, 1]


# ---- API ------------------------------------------------------------------------------

@pytest.fixture
def api(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as PA
    from backend.api import racepower as RP
    from backend.engine import planning as P
    held = {"plan": Plan(events=[Event("h", "南湖大山", "2026-12-10", kind="baiyue", days=2, distance_km=16,
                                       climbing_m=1200)])}
    monkeypatch.setattr(P.Plan, "load", classmethod(lambda cls, path=None: held["plan"]))
    monkeypatch.setattr(P.Plan, "save", lambda self, path=None: None)
    monkeypatch.setattr(PA, "_notify", lambda thresholds: None)
    RP._courses.clear()
    app = FastAPI()
    app.include_router(PA.router)
    app.include_router(RP.router)
    return TestClient(app)


def test_api_upload_reuse_after_restart_splits_delete(api):
    from backend.api import racepower as RP
    trip = synth_gpx([(8, 1200), (8, -1200)], camps=[8.0])
    assert api.get("/api/v1/plan/events/h/gpx").status_code == 404
    assert api.post("/api/v1/plan/events/nope/gpx", files={"file": ("t.gpx", trip)}).status_code == 404
    assert api.post("/api/v1/plan/events/h/gpx", files={"file": ("t.gpx", b"<kml/>")}).status_code == 400
    r = api.post("/api/v1/plan/events/h/gpx", files={"file": ("t.gpx", trip)})
    assert r.status_code == 200 and r.json()["gpx"]["filename"] == "t.gpx" and len(r.json()["days"]["days"]) == 2
    assert api.get("/api/v1/plan/events/h/gpx/file").content == trip
    # the race calculator loads it without an upload, with the camp split for the 2-day trip
    c = api.post("/api/v1/racepower/course/event/h", json={}).json()
    assert c["event_id"] == "h" and c["day_splits_km"] == [pytest.approx(8.0, abs=0.05)]
    # a server restart empties the course cache: a plan naming the event reloads it (no 410)
    RP._courses.clear()
    body = RP.PlanIn(type="baiyue", course=RP.CourseRef(course_id=c["course_id"], event_id="h"))
    assert RP._resolve_course(body)["totals"]["km"] == pytest.approx(16.0, abs=0.1)
    RP._courses.clear()
    with pytest.raises(Exception) as ei:
        RP._resolve_course(RP.PlanIn(type="baiyue", course=RP.CourseRef(course_id=c["course_id"])))
    assert getattr(ei.value, "status_code", None) == 410
    r = api.put("/api/v1/plan/events/h/gpx/splits", json={"day_splits_km": [5.0, 99]})
    assert r.json()["gpx"]["day_splits"] == [5.0] and r.json()["days"]["split_source"] == "stored"
    ev = api.get("/api/v1/plan/events/h/gpx").json()
    assert ev["days"]["days"][0]["km"] == pytest.approx(5.0, abs=0.05)
    assert api.delete("/api/v1/plan/events/h/gpx").status_code == 200
    assert api.post("/api/v1/racepower/course/event/h", json={}).status_code == 404
    assert api.delete("/api/v1/plan/events/h/gpx").status_code == 404


def test_deleting_the_event_deletes_its_gpx(api, store):
    db, root = store
    api.post("/api/v1/plan/events/h/gpx", files={"file": ("t.gpx", ONE)})
    assert (root / "h.gz").exists()
    assert api.delete("/api/v1/plan/events/h").status_code == 200
    assert not (root / "h.gz").exists() and EG.get("h") is None
