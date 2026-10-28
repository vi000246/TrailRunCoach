"""
SP-543: the race calculator's 土徑比 from OpenStreetMap (engine/racepower/surface.py), stored per
course file (engine/course_surface.py), its API (api/racepower.py /surface/{course_id}).

SP-727: the ways come from the trail dictionary on this server (engine/trail_dict_store.py), no
Overpass any more: the tests build a small index of the same ways (no network).
"""
import json
from pathlib import Path

import pytest

from backend.engine import course_surface as CS
from backend.engine import trail_dict_store as DS
from backend.engine import trailclass as TC
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import surface as SF

CASES = json.loads((Path(__file__).parent / "fixtures" / "osm_surface_cases.json").read_text("utf-8"))
LAT0, LON0 = 24.0, 121.0
M_PER_DEG = 111195.0


def synth_gpx(km: float = 10.0, step_m: float = 20.0) -> bytes:
    """A straight north-going course of `km` (flat)."""
    n = int(round(km * 1000 / step_m))
    tp = "".join(f'<trkpt lat="{LAT0 + i * step_m / M_PER_DEG:.7f}" lon="{LON0}"><ele>100</ele></trkpt>'
                 for i in range(n + 1))
    return (f'<?xml version="1.0"?><gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<trk><name>synthetic</name><trkseg>{tp}</trkseg></trk></gpx>').encode("utf-8")


def way(wid: int, a_km: float, b_km: float, tags: dict, east_m: float = 8.0) -> dict:
    """A way alongside the course from a_km to b_km, east_m to the east of it."""
    lon = LON0 + east_m / (M_PER_DEG * 0.9135)          # cos(24°)
    pts = [a_km + (b_km - a_km) * i / 10 for i in range(11)]
    return {"type": "way", "id": wid, "tags": tags,
            "geometry": [{"lat": LAT0 + k * 1000 / M_PER_DEG, "lon": lon} for k in pts]}


# 0–4 km dirt path, 4–7 km an untagged footway, 7–9 km a road, 9–10 km nothing near
WAYS = [way(1, 0, 4, {"highway": "path", "surface": "ground"}),
        way(2, 4, 7, {"highway": "footway"}),
        way(3, 7, 9, {"highway": "residential"}),
        way(4, 0, 10, {"highway": "path", "surface": "asphalt"}, east_m=400.0)]     # too far: never matched


def close(p: dict, want: dict) -> bool:
    """Whole percentages within 1 point (a way's end still matches the points 50 m past it)."""
    return set(p) == set(want) and all(abs(p[k] - want[k]) <= 1 for k in want)


def _track():
    return GPX.parse(synth_gpx(), "t.gpx")


# ---- the classification (shared with Cairn) -------------------------------------------------

@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: json.dumps(c["tags"], ensure_ascii=False))
def test_classification_follows_the_shared_cases(case):
    assert SF.classify(case["tags"]) == case["class"], case["why"]
    assert SF.group_of(SF.classify(case["tags"])) == case["group"]


def test_the_groups_are_the_users_definition():
    assert CASES["groups"] == SF.GROUP
    # an untagged path / footway is 不確定, never 土徑 (SP-531 Q4)
    assert SF.group_of(SF.classify({"highway": "footway"})) == "unsure"
    assert SF.group_of(SF.classify({"highway": "steps"})) == "made"


# ---- matching ---------------------------------------------------------------------------------

def test_points_take_the_nearest_way_within_50_m():
    lat = [LAT0 + k * 1000 / M_PER_DEG for k in (1.0, 5.0, 8.0, 9.5)]
    lon = [LON0] * 4
    assert SF.label_points(lat, lon, WAYS) == ["dirt", "unknown", "paved", "unknown"]
    near = [way(9, 0, 2, {"highway": "path", "surface": "concrete"}, east_m=30.0),
            way(8, 0, 2, {"highway": "path", "surface": "dirt"}, east_m=5.0)]
    assert SF.label_points([LAT0 + 1000 / M_PER_DEG], [LON0], near) == ["dirt"]     # the nearer one wins
    assert SF.label_points([LAT0 + 1000 / M_PER_DEG], [LON0], []) == ["unknown"]


def test_runs_shorter_than_60_m_join_their_neighbour():
    d = [0, 20, 40, 60, 80, 100, 120, 140, 160, 180, 200]
    lab = ["dirt"] * 4 + ["paved"] * 2 + ["dirt"] * 5
    assert SF.run_segments(d, lab) == [{"a": 0.0, "b": 0.2, "c": "dirt"}]
    lab = ["dirt"] * 3 + ["paved"] * 4 + ["dirt"] * 4
    assert [s["c"] for s in SF.run_segments(d, lab)] == ["dirt", "paved", "dirt"]


def test_percentages_add_up_to_100_and_the_users_marks_count():
    prof = {"segs": [{"a": 0, "b": 1, "c": "dirt"}, {"a": 1, "b": 2, "c": "unknown"}, {"a": 2, "b": 3, "c": "steps"}]}
    e = SF.effective(prof)
    assert e["pct"] == {"dirt": 34, "made": 33, "unsure": 33} and sum(e["pct"].values()) == 100
    e = SF.effective(prof, {"1": "dirt"})
    assert e["pct"] == {"dirt": 67, "made": 33, "unsure": 0} and e["osm_pct"]["unsure"] == 33
    assert e["segs"][1] == {"a": 1, "b": 2, "c": "unknown", "g": "dirt", "user": True}
    assert SF.shares(e, 0.5, 2.5) == pytest.approx({"dirt": 0.75, "made": 0.25, "unsure": 0.0})
    assert SF.shares(e, 2.5, 4.0)["unsure"] == pytest.approx(2 / 3)         # past the profile: unsure
    m = SF.effective(prof, {}, {"dirt": 50, "made": 20, "unsure": 30})
    assert m["pct"] == {"dirt": 50, "made": 20, "unsure": 30}
    assert SF.shares(m, 0, 1) == {"dirt": 0.5, "made": 0.2, "unsure": 0.3}
    with pytest.raises(ValueError):
        SF.clean_manual({"dirt": 50, "made": 20, "unsure": 20})
    assert SF.clean_overrides({"0": "made", "7": "dirt", "x": "dirt", "1": "lava"}, 3) == {"0": "made"}


# ---- the lookup in the trail dictionary (SP-727; was Overpass over HTTP) ------------------------

def _record(w: dict) -> dict:
    """WAYS as the dictionary carries them: its classes (rules v2, in town: the untagged footway stays
    unknown as before) and the delta-coded coordinates."""
    cls = TC.classify(w["tags"], town=True)
    c, px, py = [], 0, 0
    for g in w["geometry"]:
        x, y = round(g["lon"] * DS.COORD_SCALE), round(g["lat"] * DS.COORD_SCALE)
        c += (x - px, y - py)
        px, py = x, y
    return {"id": w["id"], "line": cls["line"], "surface": cls["surface"], "risk": cls["risk"],
            "tags": TC.kept_tags(w["tags"]), "c": c}


@pytest.fixture
def dictionary(tmp_path, monkeypatch):
    """The server's trail dictionary holding WAYS (its index built as a refresh would)."""
    root = tmp_path / "trail-dict"
    root.mkdir()
    n = DS.build_index([_record(w) for w in WAYS], root / "v1.sqlite", {"version": "v1", "rules_version": 2})
    (root / DS.CURRENT).write_text(json.dumps({"version": "v1", "rules_version": 2, "format": DS.FORMAT, "ways": n}))
    monkeypatch.setattr(DS, "_default_root", lambda: root)
    return root


def test_lookup_in_the_dictionary_splits_the_course(dictionary):
    prof = SF.analyse(_track())
    e = SF.effective(prof)
    assert [s["c"] for s in prof["segs"]] == ["dirt", "unknown", "paved", "unknown"]
    assert close(e["pct"], {"dirt": 40, "made": 20, "unsure": 40})
    assert prof["km"] == pytest.approx(10.0, abs=0.05) and prof["ways"] == 4
    assert prof["src"] == "dict" and prof["dict"] == "v1"


def test_no_dictionary_means_no_lookup():
    with pytest.raises(SF.SurfaceError) as ei:
        SF.analyse(_track())
    assert ei.value.code == "off"


# ---- the API ----------------------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    monkeypatch.setattr(CS, "_default_db", lambda: tmp_path / "app.db")
    RP._courses.clear()
    app = FastAPI()
    app.include_router(RP.router)
    return TestClient(app)


def _upload(api) -> str:
    r = api.post("/api/v1/racepower/course", files={"file": ("t.gpx", synth_gpx())})
    assert r.status_code == 200, r.text
    return r.json()["course_id"]


def test_api_looks_up_once_stores_and_keeps_the_users_marks(api, dictionary, monkeypatch):
    calls = []
    real = SF.lookup

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(SF, "lookup", counted)
    cid = _upload(api)
    assert api.get(f"/api/v1/racepower/surface/{cid}").json()["status"] == "none"
    v = api.post(f"/api/v1/racepower/surface/{cid}", json={}).json()
    assert v["status"] == "ok" and close(v["pct"], {"dirt": 40, "made": 20, "unsure": 40})
    assert v["attribution"] == "© OpenStreetMap contributors"
    n = len(calls)
    assert api.post(f"/api/v1/racepower/surface/{cid}", json={}).json()["pct"] == v["pct"] and len(calls) == n
    # mark the untagged footway (segment 1) as dirt: stored with the course
    unsure = [i for i, s in enumerate(v["segs"]) if s["g"] == "unsure"]
    r = api.put(f"/api/v1/racepower/surface/{cid}", json={"overrides": {str(unsure[0]): "dirt"}})
    assert r.status_code == 200 and close(r.json()["pct"], {"dirt": 70, "made": 20, "unsure": 10})
    assert CS.get(cid)["overrides"] == {str(unsure[0]): "dirt"}
    # a typed-in split wins over the segments; clearing it brings the marked split back
    r = api.put(f"/api/v1/racepower/surface/{cid}", json={"manual": {"dirt": 60, "made": 30, "unsure": 10}})
    assert r.json()["pct"] == {"dirt": 60, "made": 30, "unsure": 10} and abs(r.json()["marked_pct"]["dirt"] - 70) <= 1
    assert api.put(f"/api/v1/racepower/surface/{cid}", json={"manual": {"dirt": 60, "made": 30, "unsure": 30}}) \
        .status_code == 400
    r = api.put(f"/api/v1/racepower/surface/{cid}", json={"clear_manual": True})
    assert r.json()["manual"] is None and abs(r.json()["pct"]["dirt"] - 70) <= 1
    # a server restart empties the course cache: the stored surface is still there
    from backend.api import racepower as RP
    RP._courses.clear()
    assert abs(api.post(f"/api/v1/racepower/surface/{cid}", json={}).json()["pct"]["dirt"] - 70) <= 1
    # looking it up again drops the marks (the segments may differ)
    _upload(api)
    assert api.post(f"/api/v1/racepower/surface/{cid}", json={"refresh": True}).json()["pct"]["dirt"] in (39, 40, 41)


def test_api_without_a_dictionary_or_while_building_says_unknown_and_stores_nothing(api, monkeypatch):
    cid = _upload(api)
    v = api.post(f"/api/v1/racepower/surface/{cid}", json={}).json()
    assert v["status"] == "unknown" and v["reason"] == "off" and "TRC_TRAIL_DICT_URL" in v["message"]
    # the first index still being built (SP-727)
    monkeypatch.setenv(DS.URL_ENV, "https://dict.test/")
    monkeypatch.setattr(DS, "building", lambda: True)
    v = api.post(f"/api/v1/racepower/surface/{cid}", json={}).json()
    assert v["status"] == "unknown" and v["reason"] == "building" and CS.get(cid) is None
    assert api.put(f"/api/v1/racepower/surface/{cid}", json={"overrides": {"0": "dirt"}}).status_code == 404
    assert api.post("/api/v1/racepower/surface/nothex", json={}).status_code == 400
    # a course id the server no longer has (and no event): unknown, not an error
    v = api.post("/api/v1/racepower/surface/" + "a" * 40, json={}).json()
    assert v["status"] == "unknown" and v["reason"] == "course"
