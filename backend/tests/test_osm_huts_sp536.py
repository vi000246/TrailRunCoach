"""
SP-536: the built-in list of Taiwan's huts and camps from OpenStreetMap (backend/data/osm_huts_tw/),
matched along an uploaded GPX as day-end candidates.

  * the list is its own public file: format, ODbL attribution, the camp_site height filter, a README;
  * a hut / camp within 150 m of the track is a candidate on every pass (src "osm"), never a day end
    by itself, never an aid-station suggestion; a GPX camp waypoint at the same place wins;
  * the build script: Overpass elements → the document (no network here: fake elevations);
  * the calculator page names the source and shows "© OpenStreetMap contributors".

Synthetic tracks and lists, plus the shipped list itself; no network.
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from backend.engine import event_gpx as EG
from backend.engine.racepower import course as CO
from backend.engine.racepower import fuel as FU
from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import huts as HU
from backend.scripts import build_osm_huts as B

ROOT = Path(__file__).resolve().parents[2]
M_PER_DEG = math.pi / 180 * CO.EARTH_R


def _path(points, step_m=20.0, ele0=2500.0):
    """A track through the (lat, lon) corners, a point every step_m."""
    lat, lon = [], []
    for (a, b), (c, d) in zip(points, points[1:]):
        dist = float(CO.haversine(a, b, c, d))
        n = max(1, int(dist / step_m))
        for i in range(n):
            lat.append(a + (c - a) * i / n)
            lon.append(b + (d - b) * i / n)
    lat.append(points[-1][0])
    lon.append(points[-1][1])
    return lat, lon, [ele0 + 0.02 * i for i in range(len(lat))]


def _east(lat0, m):
    return m / M_PER_DEG / math.cos(math.radians(lat0))


def _doc(*huts):
    return {"format": HU.FORMAT, "attribution": "© OpenStreetMap contributors", "license": "ODbL-1.0",
            "license_url": "https://www.openstreetmap.org/copyright",
            "huts": [{"osm": f"node/{i + 1}", "kind": k, "name": nm, "lat": la, "lon": lo, "ele_m": 2800,
                      "ele_src": "dem"} for i, (nm, k, la, lo) in enumerate(huts)]}


@pytest.fixture
def fake_list(monkeypatch):
    def use(*huts):
        doc = _doc(*huts)
        monkeypatch.setattr(HU, "load", lambda path=None: doc)
        return doc
    return use


# ---- the shipped list -----------------------------------------------------------------------

def test_the_list_is_its_own_public_file_with_its_licence():
    doc = json.loads(HU.DATA.read_text("utf-8"))
    assert doc["format"] == HU.FORMAT
    assert doc["attribution"] == "© OpenStreetMap contributors"
    assert doc["license"] == "ODbL-1.0" and doc["license_url"] == "https://www.openstreetmap.org/copyright"
    hs = doc["huts"]
    assert doc["count"] == {k: sum(1 for h in hs if h["kind"] == k) for k in B.KINDS}
    assert doc["count"]["alpine_hut"] > 50 and doc["count"]["camp_site"] > 50
    s, w, n, e = doc["query"]["bbox"]
    for h in hs:
        assert set(h) <= {"osm", "kind", "name", "name_en", "lat", "lon", "ele_m", "ele_src"}
        assert h["kind"] in B.KINDS and h["osm"].split("/")[0] in ("node", "way", "relation")
        assert s <= h["lat"] <= n and w <= h["lon"] <= e
        if h["kind"] == "camp_site":                    # most OSM camp sites are car camps on the plains
            assert h["ele_m"] >= doc["filter"]["camp_site_min_ele_m"] == B.CAMP_MIN_ELE_M
    assert len({h["osm"] for h in hs}) == len(hs)
    readme = (HU.DATA.parent / "README.md").read_text("utf-8")
    for must in ("ODbL", "© OpenStreetMap contributors", "openstreetmap.org/copyright", "trc-osm-huts/1",
                 "1500", "build_osm_huts", "TRC_OVERPASS_URL"):
        assert must in readme


def test_the_real_list_finds_the_huts_on_the_jiaming_lake_trail():
    # 向陽山屋 → 嘉明湖避難山屋 (both in the shipped list), a straight line a bit past each end
    lat, lon, ele = _path([(23.2560, 120.9790), (23.26269, 120.98438), (23.28397, 120.99697), (23.2900, 121.0050)])
    c = CO.build_course(GPX.Track(lat, lon, ele, None, [], "jiaming"))
    osm = [w for w in c["wpts"] if w.get("src") == "osm"]
    names = [w["name"] for w in osm]
    assert "向陽山屋" in names and "嘉明湖避難山屋" in names
    assert all(w["camp"] and w["kind"] for w in osm)
    assert c["osm"]["text"] == "© OpenStreetMap contributors"


# ---- along the track --------------------------------------------------------------------------

def test_within_150_m_is_a_candidate_on_every_pass(fake_list):
    lat0, lon0 = 24.0, 121.0
    fake_list(("近的山屋", "alpine_hut", lat0 + 5000 / M_PER_DEG, lon0 + _east(lat0, 120)),
              ("遠的營地", "camp_site", lat0 + 7000 / M_PER_DEG, lon0 + _east(lat0, 200)))
    top = (lat0 + 10000 / M_PER_DEG, lon0)
    lat, lon, ele = _path([(lat0, lon0), top, (lat0, lon0)])          # out and back
    c = CO.build_course(GPX.Track(lat, lon, ele, None, [], "oab"))
    osm = [w for w in c["wpts"] if w.get("src") == "osm"]
    assert [w["name"] for w in osm] == ["近的山屋", "近的山屋"]       # 200 m off: not a candidate
    assert [round(w["km"]) for w in osm] == [5, 15]
    assert [(w["pass"], w["passes"]) for w in osm] == [(1, 2), (2, 2)]
    assert osm[0]["kind"] == "alpine_hut" and osm[0]["osm"] == "node/1"
    # candidates only: never aid-station suggestions
    assert FU.stops_from_wpts(c["wpts"], c["totals"]["km"]) == []


def test_a_gpx_camp_waypoint_at_the_same_place_wins(fake_list):
    lat0, lon0 = 24.0, 121.0
    fake_list(("OSM 名字", "alpine_hut", lat0 + 6000 / M_PER_DEG, lon0 + _east(lat0, 60)),
              ("另一間", "wilderness_hut", lat0 + 12000 / M_PER_DEG, lon0))
    lat, lon, ele = _path([(lat0, lon0), (lat0 + 15000 / M_PER_DEG, lon0)])
    w = [{"name": "我的山屋", "lat": lat0 + 6050 / M_PER_DEG, "lon": lon0}]
    c = CO.build_course(GPX.Track(lat, lon, ele, None, w, "t"))
    assert [(x["name"], x.get("src")) for x in c["wpts"]] == [("我的山屋", None), ("另一間", "osm")]
    # no OSM hut near: no attribution, nothing added
    fake_list()
    c = CO.build_course(GPX.Track(lat, lon, ele, None, w, "t"))
    assert c["osm"] is None and [x["name"] for x in c["wpts"]] == ["我的山屋"]
    assert CO.build_course(GPX.Track(lat, lon, ele, None, [], "t"), osm_huts=False)["wpts"] == []


def test_a_missing_or_foreign_list_is_no_list(tmp_path):
    assert HU.load(tmp_path / "none.json")["huts"] == []
    (tmp_path / "x.json").write_text('{"format": "other/9", "huts": [{"lat": 1}]}')
    assert HU.load(tmp_path / "x.json")["huts"] == []


def test_the_event_gpx_keeps_osm_passes_as_candidates_not_day_ends(fake_list, tmp_path, monkeypatch):
    monkeypatch.setattr(EG, "_default_db", lambda: tmp_path / "app.db")
    monkeypatch.setattr(EG, "ROOT", tmp_path / "gpx")
    EG._memo.clear()
    lat0, lon0 = 24.0, 121.0
    fake_list(("山屋", "alpine_hut", lat0 + 8000 / M_PER_DEG, lon0))
    lat, lon, ele = _path([(lat0, lon0), (lat0 + 20000 / M_PER_DEG, lon0)])
    EG.save("trip", GPX.write_gpx(GPX.Track(lat, lon, ele, None, [], "t")).encode(), "t.gpx")
    assert EG.get("trip")["camp_km"] == [pytest.approx(8.0, abs=0.05)]
    s = EG.day_stats("trip", 2)
    assert s["split_source"] == "equal" and s["candidates_km"] == [pytest.approx(8.0, abs=0.05)]


def test_the_box_keeps_the_cost_down(fake_list, monkeypatch):
    lat0, lon0 = 24.0, 121.0
    fake_list(*[(f"h{i}", "alpine_hut", 22.0 + i * 0.001, 120.5) for i in range(2000)])
    lat, lon, _ = _path([(lat0, lon0), (lat0 + 5000 / M_PER_DEG, lon0)])
    calls = []
    real = CO.haversine
    monkeypatch.setattr(CO, "haversine", lambda *a: calls.append(1) or real(*a))
    assert HU.along(np.array(lat), np.array(lon), np.arange(len(lat)) * 20.0) == []
    assert calls == []                                   # every hut outside the track's box: none measured


# ---- the build script -------------------------------------------------------------------------

def test_the_build_script_filters_camps_by_height_and_keeps_huts():
    els = [
        {"type": "node", "id": 3, "lat": 23.5, "lon": 121.0, "tags": {"tourism": "camp_site", "name": "高山營地"}},
        {"type": "node", "id": 4, "lat": 23.6, "lon": 121.1, "tags": {"tourism": "camp_site", "name": "平地露營區"}},
        {"type": "way", "id": 9, "center": {"lat": 24.1, "lon": 121.2},
         "tags": {"tourism": "alpine_hut", "name": "某山屋", "name:en": "Some Hut", "ele": "3,100 m"}},
        {"type": "node", "id": 5, "lat": 23.7, "lon": 121.3, "tags": {"tourism": "wilderness_hut"}},
        {"type": "node", "id": 6, "lat": 22.0, "lon": 118.5, "tags": {"tourism": "alpine_hut", "name": "金門"}},
        {"type": "node", "id": 7, "lat": 23.8, "lon": 121.0, "tags": {"tourism": "viewpoint", "name": "觀景台"}},
        {"type": "node", "id": 3, "lat": 23.5, "lon": 121.0, "tags": {"tourism": "camp_site", "name": "高山營地"}},
    ]
    asked = []

    def elev(points):
        asked.extend(points)
        return [{(23.5, 121.0): 2400.0, (23.6, 121.1): 300.0}.get(p) for p in points]
    doc = B.build(els, elev, osm_timestamp="2026-10-14T05:33:48Z", source="test")
    assert (23.5, 121.0) in asked and (24.1, 121.2) not in asked          # the ele tag is used when there
    by = {h["osm"]: h for h in doc["huts"]}
    assert list(by) == ["node/3", "node/5", "way/9"]
    assert by["node/3"]["ele_m"] == 2400 and by["node/3"]["ele_src"] == "dem"
    assert by["way/9"] == {"osm": "way/9", "kind": "alpine_hut", "name": "某山屋", "lat": 24.1, "lon": 121.2,
                           "name_en": "Some Hut", "ele_m": 3100, "ele_src": "osm"}
    assert by["node/5"]["name"] is None and by["node/5"]["ele_m"] is None   # a hut with no height is kept
    assert doc["dropped"] == {"camp_site_low": 1, "camp_site_no_ele": 0}
    assert doc["count"] == {"alpine_hut": 1, "wilderness_hut": 1, "camp_site": 1}
    assert doc["format"] == HU.FORMAT and doc["license"] == "ODbL-1.0"
    # one hut per line, and it reads back the same
    text = B.dumps(doc)
    assert json.loads(text) == doc and text.count('{"osm": ') == 3


def test_the_query_uses_a_bbox_and_the_url_is_configurable(monkeypatch):
    q = B.overpass_query()
    assert "area" not in q and "(21.85,119.95,25.35,122.05)" in q
    assert "alpine_hut|wilderness_hut|camp_site" in q and "out center" in q
    sent = {}

    def fake_fetch(url, query, headers=None, timeout=0):
        sent.update(url=url, headers=headers)
        return {"osm3s": {"timestamp_osm_base": "t"}, "elements": []}
    monkeypatch.setattr(B, "fetch_overpass", fake_fetch)
    monkeypatch.setenv("TRC_OVERPASS_URL", "https://overpass.example/api/interpreter")
    monkeypatch.setenv("TRC_OVERPASS_CF_CLIENT_ID", "id")
    monkeypatch.setenv("TRC_OVERPASS_CF_CLIENT_SECRET", "sec")
    B.main(["--dry-run"])
    assert sent == {"url": "https://overpass.example/api/interpreter",
                    "headers": {"CF-Access-Client-Id": "id", "CF-Access-Client-Secret": "sec"}}


# ---- the page ---------------------------------------------------------------------------------

def test_the_calculator_names_the_source_and_credits_osm():
    page = (ROOT / "backend/static/racepower.html").read_text("utf-8")
    assert "© OpenStreetMap contributors" in page and "https://www.openstreetmap.org/copyright" in page
    assert 'w.src === "osm"' in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / f"backend/static/i18n/{loc}/racepower.json").read_text("utf-8"))
        for k in ("split.osm_hut", "split.osm_camp", "split.osm_noname", "split.osm_note"):
            assert cat[k]
    zh = json.loads((ROOT / "backend/static/i18n/zh-TW/racepower.json").read_text("utf-8"))
    assert zh["split.osm_hut"] == "OSM 山屋"
