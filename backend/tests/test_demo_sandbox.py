"""Demo mode (auth-and-demo plan §3): tenancy, sandboxes, the write allow-list,
CSRF, limits and the session endpoint. A tiny hand-made demo base in tmp_path
(backend/tests/demo_fixtures.py) — never ~/WKO5 or ~/.wko5coach."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.tests import demo_fixtures as F


@pytest.fixture
def demo(monkeypatch, tmp_path):
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    base = F.make_base(root)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    app = build_app(demo=True)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, root, base


def _client(app_client):
    """A second browser on the same app (own cookie jar)."""
    from fastapi.testclient import TestClient
    return TestClient(app_client.app, raise_server_exceptions=False)


# ---------------------------------------------------------------- tenancy

def test_owner_tenant_by_default(monkeypatch, tmp_path):
    from backend import tenancy
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "h"))
    t = tenancy.current()
    assert t.kind == tenancy.OWNER and t.root == tmp_path / "h" == t.shared
    assert tenancy.db_path() == tmp_path / "h" / "wko5coach.db"


@pytest.mark.parametrize("fn", [
    "backend.db.database:db_path", "backend.engine.planning:plan_path",
    "backend.engine.wko5expr.render_cache:cache_dir", "backend.engine.routes:home",
    "backend.engine.racepower.weather:home", "backend.sync.storage:fit_root",
    "backend.engine.wko5expr.dataset:_cache_dir", "backend.engine.event_gpx:_root",
    "backend.engine.racepower.athlete:_hike_meta_path", "backend.engine.racepower.share:_shares_dir",
    "backend.engine.wko5expr.config:config_path", "backend.engine.wko5expr.corrections:corrections_path",
])
def test_paths_follow_the_tenant(fn, tmp_path, monkeypatch):
    import importlib
    from backend import tenancy
    mod, name = fn.split(":")
    m = importlib.import_module(mod)
    for attr in ("HOME", "ROOT", "FIT_ROOT", "CACHE_DIR", "_CACHE_DIR", "HIKE_META", "SHARES_DIR"):
        if getattr(m, attr, None) is not None:
            monkeypatch.setattr(m, attr, None)          # the conftest's fixed folders off: the tenant decides
    monkeypatch.delenv("WKO5COACH_ROUTES_DIR", raising=False)
    base = tmp_path / "base"
    box = tmp_path / "box"
    sb = tenancy.Tenant(id="demo-x", kind=tenancy.DEMO_SANDBOX, root=box, shared=base, caps=tenancy.DEMO_CAPS)
    with tenancy.use(sb):
        p = Path(getattr(m, name)())
    assert base in p.parents or box in p.parents or p in (base, box)


def test_thread_carries_the_tenant(tmp_path):
    import contextvars
    from backend import tenancy
    t = tenancy.Tenant(id="demo-x", kind=tenancy.DEMO_BASE, root=tmp_path, shared=tmp_path)
    seen = {}
    with tenancy.use(t):
        ctx = contextvars.copy_context()
        th = threading.Thread(target=ctx.run, args=(lambda: seen.setdefault("id", tenancy.current().id),))
        th.start()
        th.join()
    assert seen["id"] == "demo-x"


def test_startup_refuses_the_owner_folder(monkeypatch, tmp_path):
    from backend.demo import instance as DI
    real = tmp_path / "realhome"
    monkeypatch.setenv("WKO5COACH_TEST_REAL_HOME", str(real))
    monkeypatch.setenv("WKO5COACH_MODE", "demo")
    for bad in (real / ".wko5coach", real / ".wko5coach" / "demo", real / "WKO5"):
        monkeypatch.setenv("WKO5COACH_HOME", str(bad))
        with pytest.raises(DI.DemoStartupError):
            DI.check_home()
    monkeypatch.delenv("WKO5COACH_HOME")
    with pytest.raises(DI.DemoStartupError):
        DI.check_home()
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "demo"))
    with pytest.raises(DI.DemoStartupError):          # no demo data built yet
        DI.startup_checks()


def test_demo_has_no_wko5_folder(monkeypatch, tmp_path):
    from backend.settings.paths import athlete_dir
    monkeypatch.setenv("WKO5COACH_MODE", "demo")
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path))
    monkeypatch.setenv("WKO5_ATHLETE_DIR", str(tmp_path / "somewhere"))
    d = athlete_dir()
    assert d.parent == tmp_path and not d.exists()


# ---------------------------------------------------------------- sandboxes

def test_get_without_cookie_creates_nothing(demo):
    c, root, base = demo
    for url in ("/api/v1/session", "/api/v1/plan", "/api/v1/plan/page", "/demo"):
        assert c.get(url).status_code < 500, url
    assert not (root / "sandboxes").exists() or not any((root / "sandboxes").iterdir())
    s = c.get("/api/v1/session").json()
    assert s["mode"] == "demo" and s["demo"]["sandbox"] is False and "plan.write" in s["caps"]
    assert "sync" not in s["caps"]


def _add_event(c, name="我的測試賽"):
    return c.put("/api/v1/plan/events", json={"name": name, "date": "2027-03-01", "priority": "B", "kind": "trail"},
                 headers={"X-TRC-CSRF": F.csrf(c)})


def test_first_write_makes_a_sandbox_and_isolates_visitors(demo):
    c, root, base = demo
    before = F.tree_hash(base)
    r = _add_event(c, "A 的賽事")
    assert r.status_code == 200, r.text
    assert "trc_demo" in c.cookies
    boxes = list((root / "sandboxes").iterdir())
    assert len(boxes) == 1 and (boxes[0] / "wko5coach.db").is_file() and (boxes[0] / "plan.json").is_file()
    assert boxes[0].name != c.cookies["trc_demo"]                     # the folder name hides the cookie
    names_a = [e["name"] for e in c.get("/api/v1/plan").json()["events"]]
    assert "A 的賽事" in names_a
    b = _client(c)
    names_b = [e["name"] for e in b.get("/api/v1/plan").json()["events"]]
    assert "A 的賽事" not in names_b and "示範越野 50K" in names_b
    r = _add_event(b, "B 的賽事")
    assert r.status_code == 200
    assert "B 的賽事" not in [e["name"] for e in c.get("/api/v1/plan").json()["events"]]
    assert F.tree_hash(base) == before                                # the base never changes
    s = c.get("/api/v1/session").json()
    assert s["demo"]["sandbox"] is True and s["demo"]["expires_at"]


def test_reset(demo):
    c, root, base = demo
    assert _add_event(c).status_code == 200
    r = c.post("/api/v1/demo/reset", headers={"X-TRC-CSRF": F.csrf(c)})
    assert r.status_code == 200
    assert not any((root / "sandboxes").iterdir())
    c.cookies.clear()
    assert "我的測試賽" not in [e["name"] for e in c.get("/api/v1/plan").json()["events"]]


def test_expiry_and_janitor(demo, monkeypatch):
    c, root, base = demo
    from backend.demo import sandbox as SB
    t0 = SB.now()
    assert _add_event(c).status_code == 200
    monkeypatch.setattr(SB, "now", lambda: t0 + SB.TTL_S + 5)
    assert SB.janitor()["deleted"] == 1
    assert not any((root / "sandboxes").iterdir())
    assert c.get("/api/v1/session").json()["demo"]["sandbox"] is False


def test_base_switch_resets_sandboxes(demo):
    c, root, base = demo
    assert _add_event(c).status_code == 200
    F.make_base(root, "b2")
    s = c.get("/api/v1/session").json()
    assert s["demo"]["sandbox"] is False and s["demo"]["base"] == "b2"
    assert not any((root / "sandboxes").iterdir())


def test_total_limit_evicts_the_oldest(demo, monkeypatch):
    c, root, base = demo
    from backend.demo import sandbox as SB
    monkeypatch.setattr(SB, "MAX_SANDBOXES", 2)
    tick = [SB.now()]

    def clock():
        tick[0] += 1
        return tick[0]
    monkeypatch.setattr(SB, "now", clock)
    for i in range(3):
        SB.create(f"cookie-{i}")
    left = {m["created"] for _s, _d, m in SB.list_sandboxes()}
    assert len(left) == 2


# ---------------------------------------------------------------- the allow-list

def _all_routes(app):
    stack, out = list(app.routes), []
    while stack:
        r = stack.pop(0)
        sub = getattr(r, "original_router", None)
        if sub is not None:
            stack.extend(sub.routes)
        else:
            out.append(r)
    return out


def test_every_write_route_is_classified(demo):
    """Each non-GET route of the demo app either is on the allow-list or answers
    403 DEMO_DISABLED: a new route nobody classified fails here."""
    c, root, base = demo
    from backend.tenancy_mw import allowed_write
    tok = F.csrf(c)
    bad = []
    for r in _all_routes(c.app):
        for m in (getattr(r, "methods", None) or set()) - {"GET", "HEAD", "OPTIONS"}:
            path = getattr(r, "path", "")
            url = path.replace("{", "").replace("}", "")
            if allowed_write(m, url):
                continue
            resp = c.request(m, url, headers={"X-TRC-CSRF": tok}, json={})
            if resp.status_code != 403 or resp.json().get("code") != "DEMO_DISABLED":
                bad.append(f"{m} {path}: {resp.status_code}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("path", ["/api/v1/sync/auto", "/api/v1/auth/tp/callback",
                                  "/api/v1/backup/list",
                                  "/api/v1/wko5/injuries/meta", "/api/v1/static/compare.html",
                                  "/api/v1/static/settings.html", "/api/v1/wko5/settings", "/share/abc"])
def test_owner_only_routes_are_not_mounted(demo, path):
    c, _r, _b = demo
    assert c.get(path).status_code == 404


def test_csrf_required(demo):
    c, _r, _b = demo
    body = {"name": "x", "date": "2027-01-01"}
    assert c.put("/api/v1/plan/events", json=body).json()["code"] == "CSRF"
    F.csrf(c)
    assert c.put("/api/v1/plan/events", json=body, headers={"X-TRC-CSRF": "wrong"}).status_code == 403


@pytest.mark.parametrize("method,path", [
    ("PUT", "/api/v1/plan/thresholds"), ("PUT", "/api/v1/plan/profile"),
    ("POST", "/api/v1/overview/plan/push-coros"), ("POST", "/api/v1/racepower/share"),
    ("POST", "/api/v1/racepower/export/plan"), ("POST", "/api/v1/racepower/backtest/run"),
    ("POST", "/api/v1/racepower/weather/key"), ("POST", "/api/v1/plan/thresholds/apply-cp"),
])
def test_owner_writes_are_refused(demo, method, path):
    c, _r, _b = demo
    r = c.request(method, path, json={}, headers={"X-TRC-CSRF": F.csrf(c)})
    assert r.status_code == 403 and r.json()["code"] == "DEMO_DISABLED"


def test_volume_and_body_limits(demo, monkeypatch):
    c, root, base = demo
    from backend import tenancy_mw as MW
    tok = F.csrf(c)
    r = c.put("/api/v1/plan/events", json={"name": "x" * 500, "date": "2027-01-01"}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 400 and r.json()["code"] == "DEMO_LIMIT"
    monkeypatch.setattr(MW, "MAX_EVENTS", 1)                    # the base already has one
    r = c.put("/api/v1/plan/events", json={"name": "y", "date": "2027-01-01"}, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 400
    big = b"x" * (MW.MAX_BODY + 10)
    r = c.post("/api/v1/racepower/course", content=big, headers={"X-TRC-CSRF": tok, "Content-Type": "application/octet-stream"})
    assert r.status_code == 413


def test_sandbox_creation_rate_limit(demo, monkeypatch):
    c, _r, _b = demo
    from backend import tenancy_mw as MW
    from backend.security import ratelimit as RL
    monkeypatch.setattr(MW, "CREATE_HOUR", RL.Buckets(rate=1, per=3600))
    assert _add_event(c).status_code == 200
    b = _client(c)
    r = _add_event(b)
    assert r.status_code == 429
    assert b.get("/api/v1/plan").status_code == 200              # still reads the base


def test_responses_are_noindex(demo):
    c, _r, _b = demo
    assert c.get("/api/v1/session").headers.get("x-robots-tag") == "noindex"
    assert "Disallow: /" in c.get("/robots.txt").text


def test_pages_carry_the_session(demo):
    c, _r, _b = demo
    html = c.get("/api/v1/plan/page").text
    assert "window.TRC_SESSION" in html and '"mode": "demo"' in html


# ---------------------------------------------------------------- rate limits

def test_token_bucket(monkeypatch):
    from backend.security import ratelimit as RL
    t = [100.0]
    monkeypatch.setattr(RL, "clock", lambda: t[0])
    b = RL.Buckets(rate=2, per=60)
    assert b.take("a") and b.take("a") and not b.take("a")
    assert b.take("b")
    t[0] += 30
    assert b.take("a") and not b.take("a")


def test_client_ip_trusts_only_the_proxy(monkeypatch):
    from backend.security import ratelimit as RL
    monkeypatch.setenv("WKO5COACH_TRUSTED_PROXIES", "127.0.0.1/32,10.0.0.0/8")
    hdr = [(b"cf-connecting-ip", b"203.0.113.7")]
    assert RL.client_ip({"client": ("127.0.0.1", 1), "headers": hdr}) == "203.0.113.7"
    assert RL.client_ip({"client": ("10.1.2.3", 1), "headers": [(b"x-forwarded-for", b"6.6.6.6, 198.51.100.2, 10.0.0.1")]}) == "198.51.100.2"
    assert RL.client_ip({"client": ("198.51.100.9", 1), "headers": hdr}) == "198.51.100.9"     # untrusted peer: header ignored


def test_owner_session(monkeypatch, tmp_path):
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=False), raise_server_exceptions=False) as c:
        s = c.get("/api/v1/session").json()
    assert s["mode"] == "owner" and "sync" in s["caps"] and s["user"] is None and s["csrf"]


def test_owner_home_and_old_spa_bookmarks(monkeypatch):
    """No React SPA any more (removed 2026-10-04): / is the 總覽 page, the SPA's
    routes with a static page redirect there, the rest 404."""
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=False), raise_server_exceptions=False) as c:
        def loc(path):
            r = c.get(path, follow_redirects=False)
            return r.status_code, r.headers.get("location")
        assert loc("/") == (307, "/api/v1/overview/page")
        assert loc("/overview") == (307, "/api/v1/overview/page")
        assert loc("/activities") == (307, "/api/v1/wko5/activities/page")
        assert loc("/activities/12") == (307, "/api/v1/wko5/activities/page")
        assert loc("/achievements") == (307, "/api/v1/achievements/page")
        assert loc("/sync")[1] == loc("/config")[1] == "/api/v1/wko5/settings"
        for gone in ("/running", "/trail", "/ai"):
            assert c.get(gone).status_code == 404
