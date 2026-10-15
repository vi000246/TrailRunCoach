"""
SP-543: the race calculator's 土徑比 from OpenStreetMap (engine/racepower/surface.py), stored per
course file (engine/course_surface.py), its API (api/racepower.py /surface/{course_id}).

No real network: a fake Overpass server on 127.0.0.1 (the HTTP path) or a fake post function.
"""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from backend.engine import course_surface as CS
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


# ---- the Overpass lookup over HTTP (a fake server on 127.0.0.1) -------------------------------

class _Fake(BaseHTTPRequestHandler):
    seen: list = []
    fail_at = None

    def do_POST(self):            # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        q = urllib.parse.parse_qs(body)["data"][0]
        _Fake.seen.append(q)
        if _Fake.fail_at is not None and len(_Fake.seen) >= _Fake.fail_at:
            self.send_response(504)
            self.end_headers()
            return
        out = json.dumps({"elements": WAYS}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server():
    _Fake.seen, _Fake.fail_at = [], None
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/api/interpreter"
    srv.shutdown()


def test_lookup_over_http_is_chunked_and_splits_the_course(fake_server):
    prof = SF.analyse(_track(), url=fake_server)
    assert len(_Fake.seen) == 4                     # 501 points → 85 sampled → 4 chunks of ≤ 25 (overlapping by 1)
    assert all(q.startswith("[out:json][timeout:10];way[highway](around:60,") and q.endswith("out geom;")
               for q in _Fake.seen)
    e = SF.effective(prof)
    assert [s["c"] for s in prof["segs"]] == ["dirt", "unknown", "paved", "unknown"]
    assert close(e["pct"], {"dirt": 40, "made": 20, "unsure": 40})
    assert prof["km"] == pytest.approx(10.0, abs=0.05) and prof["ways"] == 4


def test_one_failed_chunk_fails_the_whole_lookup(fake_server):
    _Fake.fail_at = 3
    with pytest.raises(SF.SurfaceError) as ei:
        SF.analyse(_track(), url=fake_server)
    assert ei.value.code == "failed" and "504" in str(ei.value)
    assert len(_Fake.seen) == 3                     # stops at the failure


def test_no_server_set_means_no_lookup():
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


def test_api_looks_up_once_stores_and_keeps_the_users_marks(api, monkeypatch):
    calls = []

    def fake_post(url, query, timeout):
        calls.append(url)
        return WAYS
    monkeypatch.setattr(SF, "post_query", fake_post)
    monkeypatch.setenv(SF.URL_ENV, "http://overpass.test/api/interpreter")
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


def test_api_without_a_server_or_with_a_failure_says_unknown_and_stores_nothing(api, monkeypatch):
    cid = _upload(api)
    v = api.post(f"/api/v1/racepower/surface/{cid}", json={}).json()
    assert v["status"] == "unknown" and v["reason"] == "off" and "TRC_OVERPASS_URL" in v["message"]

    def down(url, query, timeout):
        raise SF.SurfaceError("failed", "URLError")
    monkeypatch.setattr(SF, "post_query", down)
    monkeypatch.setenv(SF.URL_ENV, "http://overpass.test/api/interpreter")
    v = api.post(f"/api/v1/racepower/surface/{cid}", json={}).json()
    assert v["status"] == "unknown" and v["reason"] == "failed" and CS.get(cid) is None
    assert api.put(f"/api/v1/racepower/surface/{cid}", json={"overrides": {"0": "dirt"}}).status_code == 404
    assert api.post("/api/v1/racepower/surface/nothex", json={}).status_code == 400
    # a course id the server no longer has (and no event): unknown, not an error
    v = api.post("/api/v1/racepower/surface/" + "a" * 40, json={}).json()
    assert v["status"] == "unknown" and v["reason"] == "course"
