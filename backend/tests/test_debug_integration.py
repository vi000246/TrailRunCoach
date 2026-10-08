"""
The debug API on the real app (SP-371): synthetic COROS FIT files (one with a GPS track) in the
tests' temp FIT root, the app DB and plan.json in a temp tenant folder, no network. /debug/activity
reads the activity from the Dataset — numbers, the thresholds used and their source, the review —
pairs it against the plan, gives streams on request and positions only with ?gps=1; every endpoint
leaves every table but the debug ones unchanged.
"""
from __future__ import annotations

import datetime as dt
import socket
import sqlite3
from datetime import datetime, timezone

import numpy as np
import pytest

from backend import debug_auth as DA
from backend.tests.debug_fixtures import ADMIN, ALL_SCOPES, DBG, TEST_PIN, bearer
from backend.tests.fit_builder import build_run

DAY = dt.date.today() - dt.timedelta(days=2)


def _gps_run(day: dt.date, minutes: int = 43) -> bytes:
    from backend.demo.fitwrite import encode_activity
    n = minutes * 60
    speed = np.full(n, 2.8)
    dist = np.cumsum(speed) - speed[0]
    i = np.arange(n)
    return encode_activity(start=datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc),
                           lat=25.03 + i * 1e-5, lon=121.56 + i * 1e-5, alt=20 + 5 * np.sin(i / 300.0),
                           dist=dist, speed=speed, hr=np.full(n, 142.0), cadence=np.full(n, 86.0), power=None,
                           temp=np.full(n, 24.0), stryd=False,
                           laps=[{"start_s": 0, "duration_s": n, "distance_m": float(dist[-1]), "avg_power": 0}])


@pytest.fixture
def client(monkeypatch, _fit_root_in_tmp, tmp_path):
    for v in ("WKO5_ATHLETE_DIR", "WKO5COACH_ATHLETE_DIR", "WKO5COACH_MODE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(DA.PIN_ENV, TEST_PIN)
    DA.reset_limits()
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    from backend.db.database import db_path
    from backend.engine.wko5expr import datasource
    from backend.api import plan as plan_api, wko5views
    monkeypatch.setattr(datasource, "_db_path", lambda: db_path())
    missing = tmp_path / "WKO5"
    monkeypatch.setattr(plan_api, "ATHLETE_DIR", missing)
    monkeypatch.setattr(wko5views, "ATHLETE_DIR", missing)
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()
    wko5views._dataset_cfg.cache_clear()
    d = _fit_root_in_tmp / "coros" / str(DAY.year)
    d.mkdir(parents=True, exist_ok=True)
    (d / "gps_run.fit").write_bytes(_gps_run(DAY))
    for k in range(3):
        day = DAY - dt.timedelta(days=3 + 2 * k)
        (d / f"run_{k}.fit").write_bytes(build_run(datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc),
                                                   seconds=1800, hr=138 + k, speed_m_s=2.9))
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=False), raise_server_exceptions=False) as c:
        yield c
    wko5views._dataset_cfg.cache_clear()
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()
    DA.reset_limits()


def _tables(path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                 if not r[0].startswith(("sqlite_", "debug_"))]
        return {n: con.execute(f'SELECT * FROM "{n}" ORDER BY 1').fetchall() for n in names}
    finally:
        con.close()


def _keys(x, out):
    if isinstance(x, dict):
        for k, v in x.items():
            out.add(str(k).lower())
            _keys(v, out)
    elif isinstance(x, list):
        for v in x:
            _keys(v, out)
    return out


def test_activity_view_on_a_real_fit_dataset_and_every_endpoint_read_only(client, tmp_path):
    from backend.db.database import db_path
    assert client.put(ADMIN, json={"enabled": True}).status_code == 200
    tok = client.post(f"{ADMIN}/tokens", json={"pin": TEST_PIN, "scopes": ALL_SCOPES}).json()["token"]
    h = bearer(tok)
    before = _tables(db_path())
    r = client.get(f"{DBG}/activity?date={DAY.isoformat()}", headers=h)
    assert r.status_code == 200, r.text[:500]
    body = r.json()
    assert body["count"] == 1, body
    a = body["activities"][0]
    assert a["basic"]["date"] == DAY.isoformat() and a["basic"]["source"] == "coros"
    assert a["basic"]["file"] == "gps_run.fit" and a["basic"]["category"] in ("road", "trail")
    assert a["numbers"]["metrics"] and a["numbers"]["row"]["moving_s"] > 2000
    assert "value" in a["thresholds_used"]["lthr"] and a["thresholds_used"]["lthr"]["why"]
    assert a["review"]["classification"]["type"] and "drift" in a["review"]["measure"]
    assert a["plan"]["matched"] is False and a["plan"]["why"]
    assert a["tags"]["activity_type"] is not None or "activity_type" in a["tags"]
    assert body["meta"]["data_generation"]["dataset"] and body["meta"]["cache_versions"]["workout_review"]
    assert not {"lat", "lon", "latitude", "longitude"} & _keys(body, set())
    # streams, without and with positions
    s = client.get(f"{DBG}/activity?date={DAY.isoformat()}&streams=hr,speed,elev&every=60", headers=h).json()
    st = s["activities"][0]["streams"]
    assert st["every_s"] == 60 and 40 <= len(st["hr"]) <= 46 and st["hr"][1] == 142.0
    assert "latitude" not in st
    g = client.get(f"{DBG}/activity?date={DAY.isoformat()}&streams=hr&every=60&gps=1", headers=h).json()
    lat = g["activities"][0]["streams"]["latitude"]
    assert lat and abs(lat[0] - 25.03) < 1e-3
    # by label
    lb = client.get(f"{DBG}/activity?label={DAY.isoformat()}", headers=h).json()
    assert lb["count"] == 1 and lb["activities"][0]["index"] == a["index"]
    assert client.get(f"{DBG}/activity", headers=h).status_code == 400
    # the other endpoints on the same data
    for p in ("/plan", f"/day?date={DAY.isoformat()}", f"/thresholds?date={DAY.isoformat()}", "/sync",
              "/export/config"):
        r = client.get(DBG + p, headers=h)
        assert r.status_code == 200, (p, r.text[:300])
    day = client.get(f"{DBG}/day?date={DAY.isoformat()}", headers=h).json()
    assert [x["index"] for x in day["unmatched_activities"]] == [a["index"]] or day["sessions"]
    th = client.get(f"{DBG}/thresholds?date={DAY.isoformat()}", headers=h).json()
    assert "lthr_info" in th["dataset"] and th["dataset"]["source"] == "coros"
    assert _tables(db_path()) == before
