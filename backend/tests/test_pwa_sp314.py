"""
離線唯讀快取與加到主畫面 (SP-314): /sw.js and /manifest.webmanifest (backend/api/pwa.py), the two
public paths they add in sign-in mode (exact match, SP-58's rules), the demo mounting neither,
and the wiring the service worker relies on (what it pre-caches matches what the pages load,
the listed API paths exist, sign-out clears the copy first). The worker's own rules: test_offline_pwa.py.
Synthetic only: no WKO5 folder, no ~/.wko5coach.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.tests.test_login_sp58 import (  # noqa: F401 — the sign-in fixtures (fake Google)
    HTML, _clean_env, _client, app, c, env, started, sign_up)

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")


def _owner_client(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    return _client(build_app(demo=False))


def _core() -> dict:
    """offline_core.js's lists, read by node itself (what the worker really uses)."""
    js = ("const O = require(process.argv[1]); console.log(JSON.stringify({APP_FILES: O.APP_FILES, CDN: O.CDN, "
          "CDN_ON_USE: O.CDN_ON_USE, PAGES: O.PAGES, API: O.API, WARM: O.WARM}));")
    r = subprocess.run([NODE, "-e", js, str(STATIC / "offline_core.js")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ---------------------------------------------------------------------------- the routes

def test_the_worker_and_the_manifest_in_the_owners_app(monkeypatch, tmp_path):
    from backend.api import pwa
    with _owner_client(monkeypatch, tmp_path) as cl:
        r = cl.get("/sw.js")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
        assert r.headers["cache-control"] == "no-cache"
        assert "__TRC_BUILD__" not in r.text and f'const VERSION = "{pwa.build_id()}";' in r.text
        assert 'importScripts("/api/v1/static/offline_core.js?v=" + VERSION)' in r.text
        m = cl.get("/manifest.webmanifest", headers={"Accept-Language": "zh-TW"})
        assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
        man = m.json()
        assert man["name"] == "訓練教練" and man["lang"] == "zh-TW" and man["description"]
        assert man["start_url"] == "/api/v1/overview/page" and man["scope"] == "/" and man["display"] == "standalone"
        assert {i["purpose"] for i in man["icons"]} == {"any", "maskable"}
        for i in man["icons"]:
            got = cl.get(i["src"])
            assert got.status_code == 200 and got.headers["content-type"].startswith(i["type"]), i
            if i["type"] == "image/png":
                w = int.from_bytes(got.content[16:20], "big")
                assert i["sizes"] == f"{w}x{w}", i
        assert cl.get("/api/v1/static/icons/apple-touch-icon.png").status_code == 200
        en = cl.get("/manifest.webmanifest", headers={"Accept-Language": "en"}).json()
        assert en["name"] == "Training Coach" and en["lang"] == "en"
        # the page the worker shows for a page it hasn't kept, in both languages
        assert '<html lang="en">' in cl.get("/api/v1/static/offline.html", headers={"Accept-Language": "en"}).text
        zh = cl.get("/api/v1/static/offline.html", headers={"Accept-Language": "zh-TW"})
        assert zh.status_code == 200 and "這一頁沒有離線資料" in zh.text


def test_signed_out_the_worker_and_manifest_open_and_nothing_else(c):
    """Sign-in mode, no session: the two new public paths answer (no one's data in them),
    exactly those — a longer or shorter path is still behind sign-in."""
    from backend.accounts import login as LG
    assert c.get("/sw.js").status_code == 200
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.json()["start_url"] == "/api/v1/overview/page"
    assert c.get("/api/v1/static/icons/icon-192.png").status_code == 200       # the static prefix, as before
    for p in ("/sw.jsx", "/sw.js/x", "/sw", "/manifest.webmanifest.json", "/manifest", "/api/v1/sw.js", "/x/sw.js"):
        assert not LG.is_public(p), p
        assert c.get(p).status_code == 401, p
    assert c.get("/api/v1/overview/status").status_code == 401                  # the data stays behind sign-in
    r = c.get("/api/v1/overview/page", headers=HTML)
    assert r.status_code in (302, 303) and r.headers["location"].startswith("/login")


def test_signed_in_the_same_files(c):
    sign_up(c, "g-alice", "alice@example.com")
    assert c.get("/sw.js").headers["cache-control"] == "no-cache"
    assert c.get("/manifest.webmanifest").headers["cache-control"] == "no-cache"
    # the person's answers stay private, no-store for any shared cache (the worker keeps its own copy)
    assert c.get("/api/v1/overview/plan/prefs").headers["cache-control"] == "private, no-store"


def test_the_demo_mounts_neither(monkeypatch, tmp_path):
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        assert cl.get("/sw.js").status_code == 404
        assert cl.get("/manifest.webmanifest").status_code == 404
        assert cl.get("/api/v1/overview/page", headers=HTML).status_code == 200          # the demo as before


def test_the_build_id_follows_the_files_contents(tmp_path):
    from backend.api import pwa
    a = tmp_path / "a"
    (a / "i18n").mkdir(parents=True)
    (a / "shell.js").write_text("one", "utf-8")
    (a / "i18n" / "x.json").write_text("{}", "utf-8")
    first = pwa.build_id(a)
    assert first == pwa.build_id(a) and re.fullmatch(r"[0-9a-f]{16}", first)
    b = tmp_path / "b"
    shutil.copytree(a, b)
    assert pwa.build_id(b) == first                        # the same files elsewhere (another start, machine)
    (a / "shell.js").write_text("two", "utf-8")
    assert pwa.build_id(a) != first                        # a deploy that changes a file: a new worker


# ---------------------------------------------------------------------------- the wiring

PAGE_FILES = {"/api/v1/overview/page": "overview.html", "/api/v1/overview/plan/schedule/page": "schedule.html",
              "/api/v1/static/offline.html": "offline.html", "/api/v1/app/activities/page": "activity.html",
              "/api/v1/app/viewer": "charts.html"}              # 單次活動, kept offline since SP-758


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_program_kept_is_what_the_cached_pages_load():
    core = _core()
    assert set(core["PAGES"]) == set(PAGE_FILES)
    app_files = set(core["APP_FILES"])
    for p in app_files:
        assert (STATIC / p.removeprefix("/api/v1/static/")).is_file(), p
    want = set()
    for page, f in PAGE_FILES.items():
        html = (STATIC / f).read_text("utf-8")
        cdns = re.findall(r'<script[^>]*\bsrc="(https://[^"]+)"', html)
        if core["PAGES"][page] == "p2":
            # 單次活動 (SP-758): kept only once opened — its program and libraries are kept as it loads them
            # (any /api/v1/static/ file is; a library only when listed in CDN_ON_USE)
            for cdn in cdns:
                assert cdn in core["CDN"] + core["CDN_ON_USE"], (f, cdn)
            continue
        want |= set(re.findall(r'<script[^>]*\bsrc="(/api/v1/static/[^"?]+)"', html))
        for cdn in cdns:
            assert cdn in core["CDN"], (f, cdn)
    want |= set(re.findall(r'\.src = "(/api/v1/static/[^"]+\.js)"', (STATIC / "shell.js").read_text("utf-8")))
    want.add("/api/v1/static/i18n/i18n.js")                # every rendered page loads it (i18n/pages.py)
    assert want - app_files == set()


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_every_listed_path_is_a_get_route():
    from backend.api import achievements, injuries, overview, plan, plan_sessions, region, sync, wko5views
    # plan: 提示多寡 GET /plan/profile/hint-level (static/hints.js, SP-482); injuries: /injuries/meta (SP-758 P2)
    gets = {r.path for m in (achievements, injuries, overview, plan, plan_sessions, region, sync, wko5views)
            for r in m.router.routes if "GET" in (getattr(r, "methods", None) or set())}
    core = _core()
    for p in list(core["API"]) + [x for x in core["PAGES"] if not x.startswith("/api/v1/static/")]:
        assert p in gets, p
    for tier, urls in core["WARM"].items():
        for u in urls:
            path = u.split("?")[0]
            assert path in core["API"] or path in core["PAGES"], (tier, u)


def test_the_shell_registers_the_worker_and_clears_the_copy_on_sign_out():
    js = (STATIC / "shell.js").read_text("utf-8")
    assert 'register("/sw.js", { scope: "/", updateViaCache: "none" })' in js
    assert "const SW_OK = !DEMO &&" in js                                      # never in the demo
    # sign-out: the copy goes before the session does
    i = js.index('out.addEventListener("click"')
    assert js.index("window.AppOffline.clear()", i) < js.index('"/api/v1/account/logout"', i)
    assert 'k.startsWith("trc-u-") || k === "trc-meta"' in js
    # offline: writes are refused with the reason, never sent
    assert 'code: "OFFLINE"' in js and 'toast(offT("locked"))' in js
    # 課表's 「＋」 and the context menu's 新增 / 刪除 are locked too (they open an empty session to save)
    assert '"[data-add]", \'[data-ctx="add"]\', \'[data-ctx="del"]\'' in js
    assert '["link", { rel: "manifest", href: "/manifest.webmanifest" }]' in js
    sched = (STATIC / "schedule.html").read_text("utf-8")
    assert "const editable = (s) => !isStale() && !(window.AppOffline && AppOffline.active)" in sched
    sw = (STATIC / "sw.js").read_text("utf-8")
    assert 'const VERSION = "__TRC_BUILD__";' in sw and "skipWaiting" in sw and "clients.claim" in sw


def test_the_offline_texts_are_in_both_languages():
    for loc in ("zh-TW", "en"):
        shell = json.loads((STATIC / "i18n" / loc / "shell.json").read_text("utf-8"))
        for k in ("offline.banner", "offline.banner_now", "offline.slow", "offline.locked", "offline.back",
                  "offline.reload", "offline.a2hs", "offline.a2hs_ok", "pwa.description"):
            assert shell.get(k), (loc, k)
        assert "{at}" in shell["offline.banner"] and "{at}" in shell["offline.slow"]
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "offline.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "offline.json").read_text("utf-8"))
    assert set(zh) == set(en) and all(en.values())
