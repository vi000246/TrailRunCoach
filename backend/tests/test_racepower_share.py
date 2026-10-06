"""
賽事計算機 read-only share links (engine/racepower/share.py, /share/<id>):
a frozen, whitelisted snapshot; no weight unless asked; delete and expiry.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from backend.engine.racepower import share as SH
from backend.tests.test_racepower_v2 import W, fake_inputs


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import racepower as RP
    from backend.engine.racepower import backtest as BT
    from backend.engine.racepower import grade_model as GM
    from backend.tests.test_racepower_v2 import RE0
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: fake_inputs())
    monkeypatch.setattr(RP, "_grade_models", lambda: {"grade_re": GM.GradeRE(RE0), "hike_speed": GM.fit_hike_speed([]),
                                                      "moving_rows": []})
    monkeypatch.setattr(RP, "_trail_hr", lambda: None)
    monkeypatch.setattr(BT, "flags", lambda path=None: ({"road": False, "trail": False, "hike": False}, False))
    monkeypatch.setattr(SH, "SHARES_DIR", tmp_path / "shares")
    app = FastAPI()
    app.include_router(RP.router)
    app.include_router(RP.share_router)
    return TestClient(app)


BODY = {"type": "road", "distance_km": 42.195, "course": {"manual": {"km": 42.195, "split": "km"}},
        "start_time": "07:00", "date": "2026-12-20", "stops": [{"km": 21, "type": "water", "name": "半程"}]}


def test_share_round_trip_without_weight(client):
    r = client.post("/api/v1/racepower/share", json={**BODY, "share_title": "台北馬 測試"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert SH.ID_RE.match(sid) and len(sid) >= 20 and r.json()["url"] == f"/share/{sid}"
    page = client.get(f"/share/{sid}")
    assert page.status_code == 200 and "唯讀" in page.text and page.headers["cache-control"] == "no-store"
    d = client.get(f"/share/{sid}/data")
    assert d.status_code == 200 and "noindex" in d.headers["x-robots-tag"]
    snap = d.json()
    assert snap["title"] == "台北馬 測試" and snap["type"] == "road" and snap["weight"] is None
    assert "w_per_kg" not in snap["summary"] and "body" not in snap["fuel"] and "crosscheck" not in snap["fuel"]
    assert "g_day" not in snap["fuel"]["loading"] and snap["fuel"]["loading"]["label"] == "前一天 10–12 g/kg"
    ld = snap["fuel"]["loading"]                                                    # SP-287: share page parts
    assert ld["when"] == "前一天" and ld["amount"] == "10–12 g/kg" and ld["note"] == "總熱量也要跟著多，不只換比例"
    assert snap["inputs"]["stops"] == [{"km": 21.0, "type": "water", "name": "半程", "minutes": 0.0}]
    assert len(snap["segments"]) == 43 and "kcal" in snap["segments"][0]
    # nothing beyond the whitelist (no sources, model inputs, warnings quoting the athlete's records)
    assert set(snap) == {"v", "title", "created", "expires", "type", "inputs", "summary", "effort", "cp", "weight",
                         "segments", "seg_targets", "days", "profile", "fuel"}
    assert "used" not in json.dumps(snap) and "source" not in snap["summary"]
    lst = client.get("/api/v1/racepower/shares").json()["shares"]
    assert [x["id"] for x in lst] == [sid] and lst[0]["weight"] is False
    assert client.delete(f"/api/v1/racepower/shares/{sid}").status_code == 200
    assert client.get(f"/share/{sid}/data").status_code == 404
    assert client.delete(f"/api/v1/racepower/shares/{sid}").status_code == 404


def test_share_with_weight_and_bad_ids(client):
    sid = client.post("/api/v1/racepower/share", json={**BODY, "include_weight": True, "expires_days": 7}).json()["id"]
    snap = client.get(f"/share/{sid}/data").json()
    assert snap["weight"] == W and snap["summary"]["w_per_kg"] > 0 and snap["fuel"]["loading"]["g_day"] == [10 * W, 12 * W]
    assert snap["expires"] and client.post("/api/v1/racepower/share", json={**BODY, "expires_days": 5}).status_code == 400
    for bad in ("..%2F..%2Fplan", "short", "a" * 70):
        assert client.get(f"/share/{bad}/data").status_code in (404, 400)


def test_expired_share_is_gone(client, tmp_path):
    p = {"type": "road", "summary": {"km": 10.0, "time_s": 2400.0}, "segments": [], "fuel": {}}
    old = dt.datetime(2020, 1, 1)
    snap = SH.snapshot(p, title="x", expires_days=7, now=old)
    sid = SH.save(snap)
    assert SH.expired(snap) and client.get(f"/share/{sid}/data").status_code == 410
    assert client.get("/api/v1/racepower/shares").json()["shares"][0]["expired"] is True


def test_baiyue_snapshot_drops_body_and_ree(client):
    r = client.post("/api/v1/racepower/share", json={"type": "baiyue", "distance_km": 20, "gain_m": 1500, "days": 2,
                                                       "course": {"manual": {"km": 20, "gain": 1500}}})
    snap = client.get(f"/share/{r.json()['id']}/data").json()
    assert snap["cp"] is None and all("ree" not in d for d in snap["fuel"]["daily"])
    assert not any("基礎代謝" in w for w in snap["fuel"]["warnings"])


def test_share_snapshot_carries_no_coordinates():
    """SP-41: the course profile and waypoints now carry lat / lon for the map;
    a share link keeps only km / z (an uploaded FIT may start at home)."""
    from backend.engine.racepower import course as CO
    from backend.tests.test_racepower_v2 import synthetic_track
    tr = synthetic_track({"len": 4000, "z": lambda x: 300 + 0.05 * x})
    tr.wpts = [{"name": "CP1", "lat": tr.lat[100], "lon": tr.lon[100]}]
    c = CO.build_course(tr)
    assert c["profile"]["lat"] and c["wpts"][0]["lat"]
    plan = {"type": "trail", "summary": {"km": 4.0}, "profile": c["profile"], "wpts": c["wpts"], "start": c["start"]}
    snap = SH.snapshot(plan, title="t")
    assert set(snap["profile"]) == {"km", "z"} and snap["profile"]["km"] == c["profile"]["km"]
    text = json.dumps(snap)
    assert '"lat"' not in text and '"lon"' not in text and "wpts" not in text
