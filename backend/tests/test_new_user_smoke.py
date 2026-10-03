"""A brand-new runner without the original single-user setup (generalize-athlete plan B0):
COROS only, no Stryd, outside Taiwan, no WKO5 folder, nothing entered on the
settings page. Every page and every parameterless data route must answer
without a server error.

Synthetic FITs in the tests' temp FIT root; the app DB and plan.json live
under the faked home (_guard.py) — never ~/WKO5 or ~/.wko5coach. Network
calls are refused (a connection attempt raises), so a route that needs the
internet must degrade, not crash."""
from __future__ import annotations

import datetime as dt
import socket
from datetime import datetime, timezone

import pytest

from backend.tests.fit_builder import build_run

# routes that leave the app by design (OAuth redirects to TrainingPeaks) or
# stream server-sent events
SKIP = {"/api/v1/auth/tp/oauth", "/api/v1/auth/tp/callback"}


def _runs(root, today: dt.date):
    """20 COROS runs over the last ~8 weeks: HR only (no power meter), a few
    with watch-estimated power, a few trail runs with climbing."""
    d = root / "coros" / str(today.year)
    d.mkdir(parents=True, exist_ok=True)
    for i in range(20):
        day = today - dt.timedelta(days=2 + i * 3)
        start = datetime(day.year, day.month, day.day, 6, tzinfo=timezone.utc)
        kw = dict(start=start, seconds=1800 + 60 * (i % 5), hr=135 + i % 10, speed_m_s=2.8 + 0.05 * (i % 4))
        if i % 4 == 1:
            kw["power"] = 230                                   # watch power, no Stryd fields
        if i % 5 == 2:
            kw.update(sub_sport=3, climb_m_per_s=0.15, speed_m_s=2.0)   # trail
        (d / f"run_{i:02d}.fit").write_bytes(build_run(**kw))


@pytest.fixture
def client(monkeypatch, _fit_root_in_tmp, tmp_path):
    for v in ("WKO5_ATHLETE_DIR", "WKO5COACH_ATHLETE_DIR", "WKO5COACH_MODE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")

    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)   # the event loop's self-pipe
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)

    from backend.db.database import db_path
    from backend.engine.wko5expr import datasource
    from backend.api import plan as plan_api, wko5views
    # the app's own DB (under the fake home) is the settings store, as for a real user
    monkeypatch.setattr(datasource, "_db_path", lambda: db_path())
    # no WKO5 folder anywhere
    missing = tmp_path / "WKO5"
    monkeypatch.setattr(plan_api, "ATHLETE_DIR", missing)
    monkeypatch.setattr(wko5views, "ATHLETE_DIR", missing)
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()
    wko5views._dataset_cfg.cache_clear()
    _runs(_fit_root_in_tmp, dt.date.today())

    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    wko5views._dataset_cfg.cache_clear()
    plan_api._wko5_settings.cache_clear()
    plan_api._wko5_profile.cache_clear()


def _all_routes():
    """Every route, also inside included routers (newer FastAPI keeps them
    wrapped: `original_router`, whose routes carry the full path)."""
    from backend.main import app
    stack, out = list(app.routes), []
    while stack:
        r = stack.pop(0)
        sub = getattr(r, "original_router", None)
        if sub is not None:
            stack.extend(sub.routes)
        else:
            out.append(r)
    return out


def _get_routes():
    out = []
    for r in _all_routes():
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", None) or set()
        if "GET" not in methods or "{" in path or path in SKIP or not path.startswith(("/api/", "/share")):
            continue
        out.append(path)
    return sorted(set(out))


def test_new_user_every_page_and_data_route(client):
    from backend.engine.wko5expr.datasource import current_source
    assert current_source() == "coros"            # no WKO5 folder: charts read the 資料來源 (COROS by default)
    r = client.post("/api/v1/athletes/bootstrap")
    assert r.status_code == 200, r.text
    athletes = client.get("/api/v1/athletes").json()
    assert len(athletes) == 1                       # an empty athlete to hang settings on

    failed = []
    routes = _get_routes()
    assert len(routes) > 60
    for path in routes:
        resp = client.get(path)
        if resp.status_code >= 500:
            failed.append(f"{path}: {resp.status_code} {resp.text[:300]}")
    assert not failed, "\n".join(failed)


def test_new_user_activity_routes(client):
    """The per-activity pages (the routes with an index) for a synced run."""
    acts = client.get("/api/v1/wko5/workouts").json()
    rows = acts if isinstance(acts, list) else acts.get("workouts") or acts.get("rows") or []
    assert rows, acts
    assert len(rows) == 20
    idx = rows[0]["index"]
    failed, tried = [], 0
    for r in _all_routes():
        path = getattr(r, "path", "")
        if "GET" not in (getattr(r, "methods", None) or set()):
            continue
        if not path.startswith("/api/v1/wko5/workouts/") or path.count("{") != 1:
            continue
        url = path.replace("{idx}", str(idx)).replace("{i}", str(idx))
        if "{" in url:
            continue
        resp = client.get(url)
        tried += 1
        if resp.status_code >= 500:
            failed.append(f"{url}: {resp.status_code} {resp.text[:300]}")
    assert tried >= 3
    assert not failed, "\n".join(failed)


def test_new_user_every_chart(client):
    """Every chart of every view (圖表分析): athlete charts over the range,
    workout charts for one synced run."""
    idx = client.get("/api/v1/wko5/workouts").json()[0]["index"]
    views = client.get("/api/v1/wko5/views").json()
    failed, n = [], 0
    for v in views:
        if v.get("error"):
            continue
        for d in v["dashboards"]:
            for c in d["charts"]:
                url = f"/api/v1/wko5/views/{v['name']}/dashboards/{d['index']}/charts/{c['index']}"
                resp = client.get(url, params={"workout": idx})
                n += 1
                if resp.status_code >= 500:
                    failed.append(f"{v['name']} / {d['title']} / {c['title']}: {resp.status_code} {resp.text[:200]}")
    assert n > 20
    assert not failed, "\n".join(failed[:30])


def test_new_user_parity_off_without_wko5(client):
    from backend.engine.wko5expr.config import EngineConfig
    assert EngineConfig.load().parity is False
