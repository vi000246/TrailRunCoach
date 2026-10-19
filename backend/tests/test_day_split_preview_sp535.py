"""
SP-535: the day-split preview (static/daysplit.js) — one dialog for the race calculator and the season
plan. Its day sums are the server's own (course.day_cuts + course.cut_at), so what the preview shows is
what is stored after 確定; GET /plan/events/{eid}/gpx/preview hands it the pieces, candidates, the
recorded nights and the trip's hours without changing anything; the pages store only on 確定.
Synthetic tracks; tmp DB / folders; the node tests are skipped without node.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import event_gpx as EG
from backend.engine import planning as P
from backend.engine.racepower import gpx as GPX
from backend.tests.test_gpx_day_splits_sp533 import client, line, out_and_back, store  # noqa: F401
from backend.tests.test_overnight_sp534 import THREE_DAYS, walk

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def ds(expr: str, **data):
    """Evaluate `expr` with static/daysplit.js loaded (DATA = data) under node."""
    js = r"""
const vm = require("vm"), fs = require("fs");
const ctx = { DATA: JSON.parse(process.argv[3]), console };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/daysplit.js", "utf8"), ctx);
vm.runInContext("OUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture
def plan_api(store, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as A
    monkeypatch.setattr(P, "PLAN_PATH", tmp_path / "plan.json")
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    app = FastAPI()
    app.include_router(A.router)
    return A, TestClient(app)


# ---- the data the preview gets (season plan) ------------------------------------------

def test_the_preview_data_changes_nothing(plan_api, monkeypatch):
    A, api = plan_api
    A.put_event(A.EventIn(id="yu", name="玉山", date="2099-01-10", kind="baiyue", days=2, gpx_pending=True))
    EG.save("yu", GPX.write_gpx(out_and_back()).encode(), "yu.gpx")
    monkeypatch.setattr(P, "event_hours", lambda e: 9.0)
    before = EG.get("yu")
    r = api.get("/api/v1/plan/events/yu/gpx/preview").json()
    assert r["days"] == 2 and r["hours"] == 9.0 and r["name"] == "玉山"
    assert r["total_km"] == pytest.approx(22.0, abs=0.1) and len(r["pieces"]) >= 22
    assert len(r["profile"]["km"]) == len(r["profile"]["z"]) > 10
    # SP-533: the hut on an out-and-back is a candidate on both passes
    hut = [c for c in r["candidates"] if c["name"] == "排雲山莊"]
    assert [(round(c["km"]), c["pass"], c["passes"], c["kind"]) for c in hut] == [(8, 1, 2, "camp"), (14, 2, 2, "camp")]
    assert r["stored_km"] == [] and r["split_source"] == "equal" and r["overnights"] is None    # a drawn route
    after = EG.get("yu")
    assert not after["day_splits"] and after == before
    assert api.get("/api/v1/plan/events/nope/gpx/preview").status_code == 404


def test_the_preview_proposes_the_recorded_nights(plan_api, monkeypatch):
    A, api = plan_api
    A.put_event(A.EventIn(id="trip", name="三天", date="2099-01-10", kind="baiyue", days=2, gpx_pending=True))
    EG.save("trip", GPX.write_gpx(walk(THREE_DAYS, step_s=60.0)).encode(), "trip.gpx")
    monkeypatch.setattr(P, "event_hours", lambda e: None)
    r = api.get("/api/v1/plan/events/trip/gpx/preview").json()
    assert r["overnights"]["days"] == 3 and len(r["overnights"]["splits_km"]) == 2
    assert r["hours"] is None and r["stored_km"] == []                   # proposed, never stored by it


# ---- the numbers: the preview = what the server stores -------------------------------------

@needs_node
def test_the_preview_sums_are_the_events_day_stats(plan_api, monkeypatch):
    A, api = plan_api
    A.put_event(A.EventIn(id="t3", name="三天", date="2099-01-10", kind="baiyue", days=3, gpx_pending=True))
    EG.save("t3", GPX.write_gpx(line(km=24.0, climb_per_km=60.0)).encode(), "t.gpx")
    monkeypatch.setattr(P, "event_hours", lambda e: 12.0)
    pv = api.get("/api/v1/plan/events/t3/gpx/preview").json()
    for splits in ([7.3, 15.55], [], [10.0]):          # day ends that fit, none (equal), too few (equal)
        EG.set_splits("t3", splits)
        want = EG.day_stats("t3", 3)
        got = ds("DaySplit.stats({total: DATA.pv.total_km, pieces: DATA.pv.pieces, hours: DATA.pv.hours}, DATA.s, 3)",
                 pv=pv, s=splits)
        assert got["source"] == want["split_source"]
        assert got["cuts"] == pytest.approx(want["splits_km"], abs=1e-6)
        for g, w in zip(got["days"], want["days"]):
            assert g["km"] == pytest.approx(w["km"], abs=1e-3)
            assert g["gain_m"] == pytest.approx(w["gain_m"], abs=0.5) and g["loss_m"] == pytest.approx(w["loss_m"], abs=0.5)
        # the trip's 12 h shared by each day's EP (km + climb / 100)
        assert sum(d["h"] for d in got["days"]) == pytest.approx(12.0)
        eps = [d["km"] + d["gain_m"] / 100 for d in got["days"]]
        assert [d["h"] for d in got["days"]] == pytest.approx([12.0 * e / sum(eps) for e in eps])


@needs_node
def test_the_preview_starts_from_the_calculators_own_days(store, client, monkeypatch):
    """The race calculator hands over its result's segments (with their time): the preview of the
    current day ends shows exactly the result's days."""
    from backend.api import racepower as RP
    EG.save("t3", GPX.write_gpx(line(km=24.0, wpts=(("營地 A", 6.0, 0.0), ("山屋 B", 12.0, 0.0)))).encode(), "t.gpx")
    ev = P.Event("t3", "三天", "2099-01-10", kind="baiyue", days=3)
    monkeypatch.setattr(RP, "_event", lambda eid: ev)
    c = client.post("/api/v1/racepower/course/event/t3", json={}).json()
    j = client.post("/api/v1/racepower/plan", json={"type": "baiyue", "days": 3, "day_splits_km": [6.0, 12.0],
                                                     "course": {"course_id": c["course_id"], "event_id": "t3"}}).json()
    pieces = [[s["start_km"], s["end_km"], s["gain_m"], s["loss_m"], s["t"]] for s in j["segments"]]
    got = ds("DaySplit.stats({total: DATA.km, pieces: DATA.p}, [6.0, 12.0], 3)", km=j["summary"]["km"], p=pieces)
    assert got["source"] == "stored"
    for g, w in zip(got["days"], j["days"]):
        assert (g["km"], g["gain_m"], g["h"]) == (pytest.approx(w["km"]), pytest.approx(w["gain_m"]), pytest.approx(w["moving_h"]))
    # moving a day end moves km, climb and time between the days; the totals stay
    moved = ds("DaySplit.stats({total: DATA.km, pieces: DATA.p}, [8.0, 12.0], 3)", km=j["summary"]["km"], p=pieces)
    assert moved["days"][0]["km"] == pytest.approx(8.0) and moved["days"][1]["km"] == pytest.approx(4.0)
    assert sum(d["h"] for d in moved["days"]) == pytest.approx(sum(d["moving_h"] for d in j["days"]))


@needs_node
def test_no_clues_is_an_equal_split_and_single_days_need_none():
    p = [[0, 10, 500, 0], [10, 20, 0, 500]]
    r = ds("[DaySplit.stats({total: 20, pieces: DATA.p}, [], 2), DaySplit.stats({total: 20, pieces: DATA.p}, [], 1),"
           " DaySplit.stats({total: 20, pieces: DATA.p}, [5, 30, 0, 5.0004], 2)]", p=p)
    assert r[0]["source"] == "equal" and r[0]["cuts"] == [10] and r[0]["days"][0]["h"] is None   # no hours: no time
    assert r[1]["source"] == "single" and r[1]["days"][0]["km"] == 20
    assert r[2]["source"] == "stored" and r[2]["cuts"] == [5]          # out of the course and duplicates dropped


# ---- the pages: one component, stored only on 確定 --------------------------------------

def test_both_pages_use_the_one_preview_and_store_only_on_ok():
    rp = (STATIC / "racepower.html").read_text("utf-8")
    plan = (STATIC / "races.html").read_text("utf-8")   # SP-691: the event editor moved to 賽事管理
    for html in (rp, plan):
        assert '<script src="/api/v1/static/daysplit.js"></script>' in html and "DaySplit.open(" in html
    # the calculator: no more applying a click on the profile / a candidate chip right away
    assert 'id="splitmode"' not in rp and "data-ck=" not in rp and 'id="splitpreview"' in rp
    assert '$("overnight-yes").onclick = () => openSplitPreview(N)' in rp
    f = rp[rp.index("async function openSplitPreview"):]
    f = f[:f.index("\n}\n")]
    assert f.index("if (!r) return;") < f.index("SPLITS = r.splits") < f.index("saveEventSplits()")
    # the season plan: the recorded nights open the preview; the PUTs come after 確定 only
    g = plan[plan.index("async function openSplits"):]
    g = g[:g.index("\n}\n")]
    assert g.index("if (!r) return;") < g.index('call("PUT"')
    assert "askOvernights" in plan and "openSplits(eid, N)" in plan and "data-gpxsplit" in plan


def test_the_preview_strings_in_both_languages():
    import re
    js = (STATIC / "daysplit.js").read_text("utf-8")
    keys = {k for k in re.findall(r'\bT\("([\w.]+)"', js) if not k.endswith((".", "_"))} | {"cand.camp", "src.proposal_night", "src.applied_cairn"}
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
        missing = sorted(k for k in keys if "daysplit." + k not in cat)
        assert not missing, (loc, missing)
    assert not re.search(r"[一-鿿]", re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S))   # no Chinese in the code


# ---- after SP-536 / 537: OpenStreetMap huts and Cairn nights in the same preview ----------------

def test_osm_huts_and_cairn_nights_reach_the_preview(plan_api, monkeypatch):
    import math
    from backend.engine.racepower import huts as HU
    A, api = plan_api
    monkeypatch.setattr(P, "event_hours", lambda e: None)
    h = HU.load()["huts"][0]
    m = math.pi / 180 * 6371008.8
    n = 400                                                   # 8 km north through the hut (at 3 km)
    lat = [h["lat"] + (i * 20 - 3000) / m for i in range(n + 1)]
    tr = GPX.Track(lat, [h["lon"]] * (n + 1), [2000.0 + i for i in range(n + 1)], None, [], "osm")
    A.put_event(A.EventIn(id="osm", name="山屋", date="2099-01-10", kind="baiyue", days=2, gpx_pending=True))
    EG.save("osm", GPX.write_gpx(tr).encode(), "osm.gpx",
            source={"kind": "cairn", "gpx_id": "g1", "url": "u", "nights": [{"night": 1, "km": 5.0, "name": "營地"}]},
            day_splits_km=[5.0])
    r = api.get("/api/v1/plan/events/osm/gpx/preview").json()
    hut = [c for c in r["candidates"] if c.get("src") == "osm"]
    assert hut and hut[0]["kind"] == "hut" and hut[0]["km"] == pytest.approx(3.0, abs=0.05) and hut[0]["osm_kind"]
    assert "OpenStreetMap" in r["osm_attr"]["text"]
    assert r["source"]["nights"] == [{"night": 1, "km": 5.0, "name": "營地"}] and r["stored_km"] == [5.0]
    plan = (STATIC / "races.html").read_text("utf-8")   # SP-691
    assert 'applied: cairn.length ? "cairn" : null' in plan and "osmAttr: pv.osm_attr" in plan
    rp = (STATIC / "racepower.html").read_text("utf-8")
    assert 'kind: "hut", src: "osm"' in rp and 'applied: CAIRN_NIGHTS && CAIRN_NIGHTS.length ? "cairn" : null' in rp
    js = (STATIC / "daysplit.js").read_text("utf-8")
    assert "© OpenStreetMap contributors" in js and "openstreetmap.org/copyright" in js
