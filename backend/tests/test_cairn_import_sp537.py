"""
SP-537: a multi-day trip imported from 疊石 Cairn by its share link (one way, Cairn → here).

  * the link: only Cairn's host, a route id and a share token; anything else is refused before any call;
  * the nights: Cairn's `poi_visits` — an `arrive` on day N with the trip going on (「☾ 在此過夜」), or a
    `depart` on day N + 1 — placed on OUR track (the POI matched to every pass of it);
  * the calculator and the season-plan event apply them at once as the day ends (the days follow);
    each day's km / climb are this app's smoothing, never Cairn's numbers;
  * the token reads Cairn and is never stored or sent back; a dead link / no nights / Cairn down are
    said plainly; the weather card's Cairn link comes from the import.

A fake Cairn on 127.0.0.1 (http.server) answers like Cairn's v2 server (routes/gpx.ts, routes/pois.ts:
{ok, data} envelopes, 404 "no gpx <id>" for a wrong / revoked token); nothing leaves the machine.
"""
import json
import math
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from backend.engine import cairn as CA
from backend.engine import event_gpx as EG
from backend.engine import planning as P
from backend.engine.racepower import course as CO
from backend.engine.racepower import gpx as GPX

ROOT = Path(__file__).resolve().parents[2]
M_PER_DEG = math.pi / 180 * CO.EARTH_R
GID = "5f0c2a8e-1b7d-4c1e-9a2f-0d3e4b5c6a71"
TOKEN = "0123456789abcdef0123456789abcdef"
LAT0, LON0 = 23.20, 120.60          # no OSM hut of the built-in list near (SP-536)


def line_track(km=30.0, step_m=20.0):
    n = int(km * 1000 / step_m)
    xs = [i * step_m for i in range(n + 1)]
    ele = [2000 + 800 * math.sin(math.pi * x / (km * 1000)) + 40 * math.sin(x / 700) for x in xs]
    return GPX.Track([LAT0 + x / M_PER_DEG for x in xs], [LON0] * (n + 1), ele, None, [], "嘉明湖三天")


def at_km(k):
    return LAT0 + k * 1000 / M_PER_DEG


def visit(i, day, kind, dist_m=None):
    return {"id": f"v{i}", "day": day, "eta": "16:00", "kind": kind, "distanceM": dist_m, "isSegment": False,
            "restMinutes": 0, "etaSource": "user"}


def poi(pid, name, km, visits, anchor="coord"):
    return {"id": pid, "gpxId": GID, "name": name, "type": "camp", "lat": at_km(km), "lng": LON0, "sortOrder": 0,
            "createdAt": None, "updatedAt": None, "anchorMode": anchor, "distanceM": None, "visits": visits,
            "description": None, "photoCount": 0, "needsReview": False}


# the trip: trailhead D1 → camp A (night 1, km 9.5) → camp B (night 2, km 21) → end D3
POIS = [
    poi("p0", "登山口", 0.0, [visit(0, 1, "depart", 0)]),
    poi("p1", "向陽山屋", 9.5, [visit(1, 1, "arrive"), visit(2, 2, "depart")]),          # coordinates only
    poi("p2", "水池", 15.0, [visit(3, 2, "pass", 15000)]),
    poi("p3", "嘉明湖避難山屋", 21.0, [visit(4, 2, "arrive", 21040), visit(5, 3, "depart", 21040)], "dist"),
    poi("p4", "終點", 30.0, [visit(6, 3, "arrive", 30000)]),
]


class FakeCairn:
    def __init__(self):
        self.gpx = GPX.write_gpx(line_track()).encode()
        self.pois = POIS
        self.meta = {"id": GID, "name": "嘉明湖三天", "start_date": "2027-03-01"}
        self.token = TOKEN
        self.down = False
        self.seen = []

    def answer(self, path, q):
        self.seen.append((path, q))
        if self.down:
            return 503, "text/plain", b"down"
        if (q.get("share") or [""])[0] != self.token:
            return 404, "application/json", json.dumps({"ok": False, "error": "not_found",
                                                         "message": f"no gpx {GID}"}).encode()
        if path == f"/api/gpx/{GID}":
            return 200, "application/gpx+xml", self.gpx
        if path == f"/api/gpx/{GID}/meta":
            return 200, "application/json", json.dumps({"ok": True, "data": self.meta}).encode()
        if path == "/api/pois" and (q.get("gpxId") or [""])[0] == GID:
            return 200, "application/json", json.dumps({"ok": True, "data": self.pois}).encode()
        return 404, "application/json", b'{"ok": false}'


@pytest.fixture
def cairn(monkeypatch):
    fake = FakeCairn()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            code, ctype, body = fake.answer(u.path, parse_qs(u.query))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fake.origin = f"http://127.0.0.1:{srv.server_address[1]}"
    fake.link = f"{fake.origin}/?gpx={GID}&share={TOKEN}"
    monkeypatch.setenv(CA.ORIGIN_ENV, fake.origin)
    yield fake
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    EG._memo.clear()
    return tmp_path


# ---- the link ------------------------------------------------------------------------------------

def test_only_a_cairn_share_link_is_read(monkeypatch):
    monkeypatch.delenv(CA.ORIGIN_ENV, raising=False)
    assert CA.parse_link(f"https://map.yichlin.com/?gpx={GID}&share={TOKEN}") == (GID, TOKEN)
    assert CA.parse_link(f"行程 HTTPS://MAP.YICHLIN.COM/?share={TOKEN}&gpx={GID}，記得看") == (GID, TOKEN)
    for bad in (f"https://map.yichlin.com.evil.io/?gpx={GID}&share={TOKEN}",
                f"https://evil.io/?gpx={GID}&share={TOKEN}&x=map.yichlin.com", "", "map.yichlin.com",
                f"https://map.yichlin.com/?gpx=<script>&share={TOKEN}"):
        with pytest.raises(CA.CairnError) as e:
            CA.parse_link(bad)
        assert e.value.kind == "link" and "分享連結" in str(e.value)
    with pytest.raises(CA.CairnError) as e:
        CA.parse_link(f"https://map.yichlin.com/?gpx={GID}")              # a link without its token
    assert "share=" in str(e.value)
    assert CA.route_url(GID) == f"https://map.yichlin.com/?gpx={GID}"     # what is kept: no token


# ---- the nights ----------------------------------------------------------------------------------

def test_the_nights_are_the_overnight_visits():
    ns = CA.night_visits(POIS)
    assert [(n["day"], n["poi"]["name"], n["km_cairn"]) for n in ns] == [(1, "向陽山屋", None),
                                                                          (2, "嘉明湖避難山屋", 21.04)]
    # the last day's arrival is not a night; a pass never is; a visit without a day is not placed
    assert CA.night_visits([poi("a", "終點", 30, [visit(0, 1, "depart"), visit(1, 1, "arrive")])]) == []
    assert CA.night_visits([poi("a", "x", 3, [visit(0, None, "arrive"), visit(1, 1, "pass")])]) == []
    # a next-morning start with no arrival marked the night before still ends that day
    only_dep = [poi("a", "登山口", 0, [visit(0, 1, "depart")]), poi("b", "營地", 12, [visit(1, 2, "depart", 12000)]),
                poi("c", "終點", 20, [visit(2, 2, "arrive")])]
    assert [(n["day"], n["poi"]["name"]) for n in CA.night_visits(only_dep)] == [(1, "營地")]


def test_the_nights_go_on_our_track_on_the_right_pass():
    # an out-and-back: the camp at 6 km out is passed again at 14 km on the way back
    n = int(10_000 / 20)
    xs = [i * 20.0 for i in range(n + 1)]
    xs = xs + xs[-2::-1]
    tr = GPX.Track([at_km(x / 1000) for x in xs], [LON0] * len(xs), [2000.0] * len(xs), None, [], "oab")
    camp = poi("c", "營地", 6.0, [visit(0, 1, "arrive"), visit(1, 2, "depart")])
    top = poi("t", "山頂營地", 10.0, [visit(2, 2, "arrive"), visit(3, 3, "depart")])
    end = poi("e", "登山口", 0.0, [visit(4, 3, "arrive")])
    got = CA.place_nights(CA.night_visits([camp, top, end]), tr)
    assert [(g["night"], g["name"], round(g["km"])) for g in got] == [(1, "營地", 6), (2, "山頂營地", 10)]
    # Cairn's own km picks the pass: the camp slept at on the way back (D2 → D3 at 14 km)
    camp2 = poi("c", "營地", 6.0, [visit(0, 2, "arrive", 14000), visit(1, 3, "depart", 14000)])
    first = poi("t", "山頂營地", 10.0, [visit(2, 1, "arrive", 10000), visit(3, 2, "depart", 10000)])
    got = CA.place_nights(CA.night_visits([first, camp2, end]), tr)
    assert [round(g["km"]) for g in got] == [10, 14]


# ---- the race calculator -----------------------------------------------------------------------

@pytest.fixture
def calc_client(monkeypatch):
    """The race calculator's API on fake athlete inputs (as test_gpx_day_splits_sp533's client)."""
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


def test_the_calculator_imports_the_trip_with_its_day_ends(cairn, calc_client):
    r = calc_client.post("/api/v1/racepower/course/cairn", json={"url": cairn.link})
    assert r.status_code == 200, r.text
    j = r.json()
    assert TOKEN not in r.text                                          # never sent back
    assert j["days"] == 3 and j["name"] == "嘉明湖三天"
    assert j["day_splits_km"] == [pytest.approx(9.5, abs=0.05), pytest.approx(21.04, abs=0.05)]
    assert [(n["night"], n["name"]) for n in j["cairn"]["nights"]] == [(1, "向陽山屋"), (2, "嘉明湖避難山屋")]
    assert j["cairn"]["url"] == f"https://map.yichlin.com/?gpx={GID}" and j["cairn"]["notes"] == []
    assert j["overnights"] is None                                       # applied, not asked about
    # Cairn was read with the token, on its three read routes only
    assert {p for p, _q in cairn.seen} == {f"/api/gpx/{GID}", f"/api/gpx/{GID}/meta", "/api/pois"}
    # the plan with those day ends: 3 days, each day's numbers from this app's smoothing
    plan = calc_client.post("/api/v1/racepower/plan", json={"type": "baiyue", "course": {"course_id": j["course_id"]},
                                                           "start_time": "06:00", "days": 3,
                                                           "day_splits_km": j["day_splits_km"]}).json()
    segs = plan["segments"]
    assert sorted({s["day"] for s in segs}) == [1, 2, 3]
    d1 = sum(s["dist_m"] for s in segs if s["day"] == 1) / 1000
    assert d1 == pytest.approx(9.5, abs=0.1)
    c = CO.build_course(GPX.parse(cairn.gpx, "x.gpx"), split="km")
    pieces = CO.cut_at(c["segments"], j["day_splits_km"])
    g1 = sum(p["gain_m"] for p in pieces if p["day"] == 1)
    assert sum(s["gain_m"] for s in segs if s["day"] == 1) == pytest.approx(g1, rel=0.08)


def test_a_dead_link_cairn_down_and_a_bad_link_are_said_plainly(cairn, calc_client):
    bad = cairn.link.replace(TOKEN, "f" * 32)                           # revoked / regenerated
    r = calc_client.post("/api/v1/racepower/course/cairn", json={"url": bad})
    assert r.status_code == 410 and "連結失效" in r.json()["detail"] and "重新複製" in r.json()["detail"]
    n = len(cairn.seen)
    r = calc_client.post("/api/v1/racepower/course/cairn", json={"url": "https://example.com/?gpx=a&share=12345678"})
    assert r.status_code == 400 and "不是疊石 Cairn 的分享連結" in r.json()["detail"]
    assert len(cairn.seen) == n                                          # no call for a link that isn't Cairn's
    cairn.down = True
    r = calc_client.post("/api/v1/racepower/course/cairn", json={"url": cairn.link})
    assert r.status_code == 502 and "稍後再試" in r.json()["detail"]


def test_cairn_unreachable(monkeypatch, calc_client):
    monkeypatch.setenv(CA.ORIGIN_ENV, "http://127.0.0.1:9")             # nothing listens there
    r = calc_client.post("/api/v1/racepower/course/cairn", json={"url": f"http://127.0.0.1:9/?gpx={GID}&share={TOKEN}"})
    assert r.status_code == 502 and "連不上疊石 Cairn" in r.json()["detail"]


def test_a_trip_without_nights_imports_the_gpx_and_says_so(cairn, calc_client):
    cairn.pois = [poi("p0", "登山口", 0.0, [visit(0, 1, "depart")]), poi("p2", "水池", 15.0, [visit(1, 1, "pass")])]
    j = calc_client.post("/api/v1/racepower/course/cairn", json={"url": cairn.link}).json()
    assert j["days"] == 1 and j["day_splits_km"] == [] and j["totals"]["km"] == pytest.approx(30, abs=0.1)
    assert len(j["cairn"]["notes"]) == 1 and "沒有標過夜點" in j["cairn"]["notes"][0] and "☾ 在此過夜" in j["cairn"]["notes"][0]


# ---- the season-plan event ----------------------------------------------------------------------

@pytest.fixture
def plan_client(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as A
    from backend.api import racepower as RP
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    A.put_event(A.EventIn(id="jm", name="嘉明湖", date="2027-03-02", kind="baiyue", days=2, gpx_pending=True))
    app = FastAPI()
    app.include_router(A.router)
    app.include_router(RP.router)
    return TestClient(app)


def test_the_event_takes_the_gpx_the_day_ends_and_the_days(cairn, plan_client, store, monkeypatch):
    r = plan_client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link})
    assert r.status_code == 200, r.text
    j = r.json()
    assert TOKEN not in r.text
    assert j["event_days"] == 3 and j["days"]["split_source"] == "stored"
    assert [round(d["km"], 1) for d in j["days"]["days"]] == [9.5, pytest.approx(11.5, abs=0.1), pytest.approx(9.0, abs=0.1)]
    # the trip's dates differ: said, not changed
    assert any("2027-03-01" in n and "2027-03-02" in n for n in j["cairn"]["notes"])
    e = P.Plan.load().events[0]
    assert e.days == 3 and e.date == "2027-03-02" and len(e.day_plan) == 3
    assert e.distance_km == pytest.approx(30, abs=0.1)                   # the GPX filled the empty totals
    row = EG.get("jm")
    assert row["day_splits"] == j["days"]["splits_km"]
    src = dict(row["source"])
    # SP-535: the nights are kept too, so the day-split preview can offer them back as candidates
    assert [round(x["km"], 1) for x in src.pop("nights")] == [round(k, 1) for k in j["days"]["splits_km"]]
    assert src == {"kind": "cairn", "gpx_id": GID, "url": f"https://map.yichlin.com/?gpx={GID}",
                   "name": "嘉明湖三天"}
    # the token is nowhere on disk
    assert TOKEN not in (store / "app.db").read_bytes().decode("latin-1")
    # the weather card's Cairn link: the imported route (not the note), without the token
    from backend.engine.racepower import weather as WX
    from backend.tests.test_route_weather_sp553 import _fake_conditions
    monkeypatch.setattr(WX, "race_conditions", _fake_conditions([], "manual"))
    js = plan_client.get("/api/v1/racepower/weather", params={"date": "2027-03-02", "lat": 23.2, "lon": 120.6,
                                                              "link_event": "jm"}).json()
    assert js["cairn"] == {"url": f"https://map.yichlin.com/?gpx={GID}", "from": "import"}
    # a plain upload afterwards replaces the GPX and forgets where the old one came from
    up = plan_client.post("/api/v1/plan/events/jm/gpx", files={"file": ("t.gpx", cairn.gpx)})
    assert up.status_code == 200 and EG.get("jm")["source"] is None


def test_an_event_import_that_fails_leaves_the_event_alone(cairn, plan_client):
    cairn.token = "x" * 32
    r = plan_client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link})
    assert r.status_code == 410
    assert EG.get("jm") is None and P.Plan.load().events[0].days == 2
    assert plan_client.post("/api/v1/plan/events/nope/gpx/cairn", json={"url": cairn.link}).status_code == 404


def test_an_older_event_gpx_table_gets_the_source_column(store):
    db = store / "app.db"
    old = EG.DDL.replace(", \nsource_json TEXT", "").replace(", source_json TEXT", "")
    assert "source_json" not in old
    con = sqlite3.connect(db)
    con.execute(old)
    con.execute("INSERT INTO event_gpx (event_id, filename, km, day_splits_json, camp_km_json) "
                "VALUES ('old', 'a.gpx', 12.0, '[5.0]', '[]')")
    con.commit()
    con.close()
    row = EG.get("old")
    assert row["source"] is None and row["day_splits"] == [5.0] and "old" in EG.all_rows()
    EG.save("new", GPX.write_gpx(line_track(km=5)).encode(), "n.gpx", source={"kind": "cairn", "gpx_id": "g1",
                                                                            "url": "u", "share": TOKEN})
    assert EG.get("new")["source"] == {"kind": "cairn", "gpx_id": "g1", "url": "u"}   # unknown keys dropped


# ---- the pages -----------------------------------------------------------------------------------

def test_the_pages_offer_the_import():
    calc = (ROOT / "backend/static/racepower.html").read_text("utf-8")
    assert 'id="cairn-url"' in calc and "/course/cairn" in calc and "/gpx/cairn" in calc
    # SP-691: the event editor (its Cairn link) moved to 賽事管理; SP-692: its Cairn import / push sit in
    # the editor popup (no separate Cairn button on the race any more)
    plan = (ROOT / "backend/static/races.html").read_text("utf-8")
    assert 'id="evcairn"' in plan and 'id="ed-cairn"' in plan and "/gpx/cairn" in plan
    for loc in ("zh-TW", "en"):
        rp = json.loads((ROOT / f"backend/static/i18n/{loc}/racepower.json").read_text("utf-8"))
        pl = json.loads((ROOT / f"backend/static/i18n/{loc}/plan.json").read_text("utf-8"))
        assert all(rp[f"cairn.{k}"] for k in ("label", "go", "done", "night", "how", "empty", "busy", "fail"))
        assert all(pl[f"ev.cairn_{k}"] for k in ("attach", "btn", "again", "use", "prompt", "done", "fail", "night", "day"))
