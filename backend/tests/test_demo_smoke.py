"""The demo instance end to end: build the synthetic athlete (small mode)
with backend/demo/build.py into tmp_path, start the app in demo mode on it
and open every page and every parameterless GET data route; then the three
things the demo is for (排課表, 賽事計算機, 週期) once each, in a sandbox.

Synthetic only: the build refuses the owner's folders and runs with its own
WKO5COACH_HOME; network calls are refused here."""
from __future__ import annotations

import datetime as dt
import socket

import pytest

from backend.tests import demo_fixtures as F

PAGES = ["/demo", "/api/v1/overview/page", "/api/v1/overview/plan/schedule/page", "/api/v1/wko5/viewer",
         "/api/v1/plan/page", "/api/v1/wko5/activities/page", "/api/v1/routes/page", "/api/v1/racepower/page",
         "/api/v1/overview/plan/compliance/page"]
SKIP = {"/api/v1/session"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-root")
    B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return root


@pytest.fixture
def demo(built, monkeypatch):
    F.demo_env(monkeypatch, built)
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as c:
        yield c, built
    wko5views._dataset_cfg.cache_clear()


def _get_routes(app):
    stack, out = list(app.routes), []
    while stack:
        r = stack.pop(0)
        sub = getattr(r, "original_router", None)
        if sub is not None:
            stack.extend(sub.routes)
            continue
        path = getattr(r, "path", "")
        if "GET" in (getattr(r, "methods", None) or set()) and "{" not in path and path.startswith("/api/") \
                and path not in SKIP:
            out.append(path)
    return sorted(set(out))


def test_build_is_synthetic_and_complete(built):
    from backend.demo import sandbox as SB
    import os
    os.environ["WKO5COACH_HOME"] = str(built)
    try:
        base = SB.current_base_dir()
    finally:
        os.environ.pop("WKO5COACH_HOME", None)
    assert (base / "wko5coach.db").is_file() and (base / "plan.json").is_file()
    assert any((base / "fit" / "coros").rglob("*.fit"))
    assert (base / "demo_manifest.json").is_file()
    import json as _json
    here = {str(built).lower(), _json.dumps(str(built)).strip('"').lower()}     # caches record their own folder
    for p in base.rglob("*.json"):
        t = p.read_text("utf-8", errors="ignore").lower()
        for h in here:
            t = t.replace(h, "")
        t = t.replace(str(built.parent).lower(), "").replace(_json.dumps(str(built.parent)).strip('"').lower(), "")
        assert ".wko5coach" not in t and "\\wko5" not in t, p


def test_every_page_and_data_route(demo):
    c, root = demo
    failed = []
    for url in PAGES:
        r = c.get(url)
        if r.status_code != 200 or "window.TRC_SESSION" not in r.text and url != "/demo":
            failed.append(f"{url}: {r.status_code}")
    for path in _get_routes(c.app):
        r = c.get(path)
        if r.status_code >= 500:
            failed.append(f"{path}: {r.status_code} {r.text[:200]}")
    assert not failed, "\n".join(failed)
    acts = c.get("/api/v1/wko5/workouts").json()
    rows = acts if isinstance(acts, list) else acts.get("workouts") or []
    assert len(rows) >= 10


def test_the_three_demo_flows(demo):
    c, root = demo
    tok = F.csrf(c)
    h = {"X-TRC-CSRF": tok}
    # 週期: add an event, then the phases
    r = c.put("/api/v1/plan/events", json={"name": "我的 B 賽", "date": (dt.date.today() + dt.timedelta(days=60)).isoformat(),
                                           "priority": "B", "kind": "trail"}, headers=h)
    assert r.status_code == 200, r.text
    # 排課表: the week, then add a session
    wk = c.get("/api/v1/overview/plan/sessions?scope=week")
    assert wk.status_code == 200, wk.text
    day = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    r = c.post("/api/v1/overview/plan/sessions", json={"day": day, "kind": "easy", "minutes": 45}, headers=h)
    assert r.status_code < 500, r.text
    # 賽事計算機: predict an event of the plan
    evs = c.get("/api/v1/plan").json()["events"]
    assert evs
    r = c.get("/api/v1/racepower/athlete")
    assert r.status_code < 500, r.text
    # sync / push stay off
    assert c.post("/api/v1/overview/plan/push-coros", headers=h).status_code == 403


def test_every_tenant_file_and_table_has_a_data_class(built):
    """SP-311: after the pages above filled the caches, every file of the demo base and of
    the visitor's sandbox, and every table of their DBs, has an entry in backend/data_registry.py
    (a new file kind / table without one fails here). A sandbox holds private files only."""
    import sqlite3
    from backend import data_registry as R
    assert R.unclassified_files(built) == []                  # the demo root: base/, sandboxes/, logs/
    base = built / "base" / (built / "base" / "current").read_text("utf-8").strip()
    tenants = [base] + sorted(p for p in (built / "sandboxes").glob("*") if p.is_dir())
    for t in tenants:
        assert R.unclassified_files(t) == [], t
        con = sqlite3.connect(f"file:{(t / 'wko5coach.db').as_posix()}?mode=ro", uri=True)
        try:
            assert R.unclassified_tables(con) == [], t
        finally:
            con.close()
    for t in tenants[1:]:
        for p in t.rglob("*"):
            if p.is_file():
                assert R.classify(p.relative_to(t).as_posix()).where in (R.ROOT, R.ANYWHERE), p
