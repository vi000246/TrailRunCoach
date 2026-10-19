"""SP-558: the 操作手冊 (api/manual.py, backend/manual/, static/manual*.js, pmc_chart.js).

  - the chapters: Chinese and English with the same chapters and section anchors; every section has
    its 「簡單說」 and a folded 「深入」; every picture a chapter uses is there (and taken by the shot list)
  - /manual opens signed out (sign-in mode), in the owner's app and in the demo; 操作手冊 sits right
    above 設定 in the menu; a signed-out visitor's bar has only the manual and 登入
  - the search (static/manual_search.js under node) finds sections in both languages, and every hit
    points at an anchor the page has
  - offline: the service worker keeps the whole manual — signed in or not — and answers from it
  - every 「看手冊 →」 (data-manual, ManualLink.href, /manual/… links in the pages) names a section that exists
  - the live chart computes what the app computes (engine/load_guard.recur)
Synthetic data only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.api import manual as M
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, _clean_env, _client, app, c, env, location, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
LOCALES = ("zh-TW", "en")


# ---------------------------------------------------------------------------- the chapters

def test_both_languages_have_the_same_chapters_and_anchors():
    zh, en = M.chapters("zh-TW"), M.chapters("en")
    # + SP-559's 名詞表, SP-560's 常見問題; SP-561–564's 分析, 賽事計算機與賽季計畫, 傷病與復跑, 設定 before them
    assert [c.id for c in zh] == [c.id for c in en] == ["start", "schedule", "analysis", "race", "injury", "settings",
                                                        "glossary", "faq"]
    for a, b in zip(zh, en):
        assert [s.anchor for s in a.sections] == [s.anchor for s in b.sections], a.id
        assert a.title and b.title and a.summary and b.summary
    assert M.anchors("zh-TW") == M.anchors("en")


@pytest.mark.parametrize("loc", LOCALES)
def test_every_section_has_its_simple_part_and_a_folded_deep_part(loc):
    for ch in M.chapters(loc):
        for s in ch.sections:
            assert len(s.simple_text) > 40, (loc, ch.id, s.anchor)
            assert s.deep_html and len(s.deep_text) > 80, (loc, ch.id, s.anchor)
        page = M._fill(loc, M.chapters(loc), ch)["main"]
        for s in ch.sections:
            assert f'<section class="m-sec" id="{s.anchor}"' in page
            # folded: a <details> without `open`, reachable as #<anchor>--deep
            assert f'<details class="m-deep" id="{s.anchor}--deep">' in page


def test_every_picture_is_there_in_both_languages_and_comes_from_the_shot_list():
    used = set()
    for loc in LOCALES:
        for ch in M.chapters(loc):
            for name in ch.images:
                assert (M.IMG_DIR / name).is_file(), f"{loc}/{ch.id}: missing picture {name}"
                assert name.endswith(f".{loc}.webp"), f"{loc}/{ch.id}: {name} is not this language's picture"
                used.add(name)
    shots = json.loads((M.ROOT / "shots.json").read_text("utf-8"))["shots"]
    taken = {f"{s['name']}.{loc}.webp" for s in shots for loc in LOCALES}
    assert used <= taken, sorted(used - taken)
    on_disk = {p.name for p in M.IMG_DIR.iterdir() if p.is_file()}
    assert on_disk == used, f"pictures no chapter uses: {sorted(on_disk - used)}"
    zh = {n.replace(".zh-TW.", ".") for n in used if ".zh-TW." in n}
    en = {n.replace(".en.", ".") for n in used if ".en." in n}
    assert zh == en


def test_the_chapters_have_live_parts_drawn_by_the_apps_code():
    sched = M.chapter("zh-TW", "schedule")
    page = M._fill("zh-TW", M.chapters("zh-TW"), sched)["main"]
    assert 'data-chart="pmc"' in page and 'data-legend="1"' in page
    html = (STATIC / "manual.html").read_text("utf-8")
    for js in ("pmc_chart.js", "sesschip.js", "dashicons.js", "manual_search.js", "manual.js", "echarts/5.5.0"):
        assert js in html, js
    ov = (STATIC / "overview.html").read_text("utf-8")
    assert "pmc_chart.js" in ov and "PmcChart.option(" in ov       # the overview draws with the same code


DENY = re.compile(r"WKO|\.md\b|docs/|擁有者|\bowner\b|\bTSS\b|\bCTL\b|\bATL\b|\bTSB\b|\bPMC\b|體能（|疲勞（|狀況（", re.I)


@pytest.mark.parametrize("loc", LOCALES)
def test_the_text_uses_the_apps_own_words(loc):
    for ch in M.chunks(loc):
        bad = DENY.findall(ch["title"] + " " + ch["text"])
        assert not bad, (loc, ch["url"], bad)


def test_the_parser_escapes_and_refuses_what_it_does_not_know():
    assert M.inline("<b>x</b> **y** `z<`") == "&lt;b&gt;x&lt;/b&gt; <strong>y</strong> <code>z&lt;</code>"
    assert M.inline("[a](schedule.md#push) [b](javascript:alert)") == '<a href="/manual/schedule#push">a</a> b'
    with pytest.raises(M.ManualError):
        M.parse("---\nid: x\n---\n## no anchor\n", "zh-TW")
    with pytest.raises(M.ManualError):
        M.parse("---\nid: x\n---\n## A {#a}\n:::deep\nopen\n", "zh-TW")
    with pytest.raises(M.ManualError):
        M.parse("---\nid: x\n---\n## A {#a}\n![x](../img/evil.exe)\n", "zh-TW")


# ---------------------------------------------------------------------------- the pages, public

@pytest.fixture
def owner(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        yield cl


def test_signed_out_the_manual_opens(c):  # noqa: F811
    for p in ("/manual", "/manual/start", "/manual/schedule"):
        r = c.get(p, headers=HTML)
        assert r.status_code == 200, (p, r.status_code, r.text[:200])
        assert '"mode": "signed_out"' in r.text                    # the bar shows the manual and 登入 only
    idx = c.get("/manual/index.json")
    assert idx.status_code == 200 and idx.json()["chunks"]
    img = M.chapters("zh-TW")[0].images[0]
    r = c.get(f"/manual/img/{img}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert c.get("/manual/nope", headers=HTML).status_code == 404
    assert c.get("/manual/img/nope.webp").status_code == 404
    # next to the manual everything stays closed
    assert c.get("/api/v1/plan").status_code == 401
    assert c.get("/manualx", headers=HTML).status_code in (302, 401)


def test_the_manual_in_the_owners_app_and_in_english(owner):
    r = owner.get("/manual/schedule?lang=en", headers=HTML)
    assert r.status_code == 200 and "Sending the schedule to the watch" in r.text and 'lang="en"' in r.text
    r = owner.get("/manual/schedule?lang=zh-TW", headers=HTML)
    assert "課表送到手錶" in r.text and r.headers["cache-control"] == "private, no-cache"
    assert owner.get("/manual/index.json?lang=en").json()["locale"] == "en"


def test_the_demo_has_the_manual(monkeypatch, tmp_path):
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        assert cl.get("/manual/start", headers=HTML).status_code == 200


def test_the_menu_has_the_manual_right_above_settings():
    js = (STATIC / "shell.js").read_text("utf-8")
    ids = re.findall(r'\{ id: "(\w+)"', js)
    assert ids.index("manual") == ids.index("settings") - 1
    more = re.search(r'const MORE = new Set\(\[([^\]]*)\]\)', js).group(1)
    more = re.findall(r'"(\w+)"', more)
    assert more[more.index("manual") + 1] == "settings"           # 關於 (SP-495) may follow 設定
    # signed out: only the manual (and 登入); none of the data the bar otherwise asks for
    assert 'const PUBLIC = SESSION.mode === "signed_out"' in js
    assert "if (DEMO || PUBLIC) return;" in js and "if (!PUBLIC) setTimeout(poll, 250);" in js
    for loc in LOCALES:
        sh = json.loads((STATIC / "i18n" / loc / "shell.json").read_text("utf-8"))
        for k in ("page.manual.name", "page.manual.short", "page.manual.purpose", "manual.see", "account.login"):
            assert sh.get(k), (loc, k)


# ---------------------------------------------------------------------------- the search

def _search(loc: str, queries: list[str]) -> dict:
    index = {"chunks": M.chunks(loc)}
    js = r"""
const S = require(process.argv[1]);
const idx = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = {};
for (const q of idx.queries) out[q] = S.search(idx.index, q, { limit: 8 });
console.log(JSON.stringify(out));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC / "manual_search.js")], input=json.dumps({"index": index, "queries": queries}),
                       capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


ZH = {"推到手錶": "/manual/schedule#push", "閾值": "/manual/start#thresholds", "行事曆 訂閱": "/manual/schedule#calendar",
      "恢復週": ("/manual/schedule#weekly-load", "/manual/glossary#phases"),       # SP-559: the 名詞表 too "Garmin": "/manual/start#connect", "體重": "/manual/start#weight",
      "顏色": "/manual/schedule#colors", "推送手錶": "/manual/schedule#push", "Chronic load": ("/manual/schedule#weekly-load", "/manual/glossary#chronic-load")}
EN = {"push to watch": "/manual/schedule#push", "thresholds": "/manual/start#thresholds", "calendar": "/manual/schedule#calendar",
      "recovery week": "/manual/schedule#weekly-load", "garmin": "/manual/start#connect", "weight": "/manual/start#weight",
      "colours": "/manual/schedule#colors", "first sync": "/manual/start#first-sync"}


@needs_node
@pytest.mark.parametrize("loc,cases", [("zh-TW", ZH), ("en", EN)])
def test_the_search_finds_the_section_in_both_languages(loc, cases):
    out = _search(loc, list(cases) + ["xyzzy 不存在的字"])
    for q, want in cases.items():
        # top 5: SP-559's 名詞表 and SP-560's 常見問題 answer many of these words too
        urls = [h["url"].split("--")[0] for h in out[q][:5]]
        assert any(w in urls for w in (want if isinstance(want, tuple) else (want,))), (q, [h["url"] for h in out[q]])
        assert "<mark>" in "".join(h["snippet"] for h in out[q])
    assert out["xyzzy 不存在的字"] == []


@needs_node
@pytest.mark.parametrize("loc", LOCALES)
def test_every_hit_points_at_an_anchor_the_page_has(loc):
    out = _search(loc, ["課", "the", "手錶", "watch", "COROS", "負荷", "load"])
    pages = {c.id: M._fill(loc, M.chapters(loc), c)["main"] for c in M.chapters(loc)}
    seen = 0
    for hits in out.values():
        for h in hits:
            path, _, frag = h["url"].partition("#")
            page = pages[path.rsplit("/", 1)[-1]]
            if frag:
                assert f'id="{frag}"' in page, h["url"]
            seen += 1
    assert seen > 10


def test_the_search_index_is_the_chunks_with_their_anchors():
    for loc in LOCALES:
        for ch in M.chunks(loc):
            assert ch["url"].startswith("/manual/") and ch["text"] and ch["part"] in ("simple", "deep")
            if ch["anchor"]:
                key = ch["chapter"] + "#" + ch["anchor"]
                assert key in M.anchors(loc)


# ---------------------------------------------------------------------------- offline

@needs_node
def test_offline_the_whole_manual_is_kept_and_answers_signed_out_too():
    from backend.tests.test_offline_pwa import run
    out = run(r"""
const idx = { chapters: [{ url: "/manual/start", images: ["/manual/img/a.zh-TW.webp"] },
                         { url: "/manual/schedule", images: ["/manual/img/b.zh-TW.webp", "https://evil.test/x.webp"] }] };
server.routes["/manual/index.json"] = () => json(idx);
for (const p of ["/manual", "/manual/start", "/manual/schedule"]) server.routes[p] = () => html("<html><head></head><body>" + p + "</body></html>");
for (const p of ["/manual/img/a.zh-TW.webp", "/manual/img/b.zh-TW.webp"]) server.routes[p] = () => new Response("img", { headers: { "content-type": "image/webp" } });
person(null, null);                                    // nobody signed in
const E = mk();
await E.install();
const kept = caches.urls("trc-app-v1").filter((u) => u.startsWith("/manual")).sort();
const cls = ["/manual", "/manual/schedule", "/manual/index.json", "/manual/img/a.zh-TW.webp"].map((p) => O.classify(ORIGIN + p, ORIGIN, true));
server.down = true;
const page = await get(E, nav("/manual/schedule"));
const index = await get(E, req("/manual/index.json"));
const img = await get(E, req("/manual/img/b.zh-TW.webp"));
const s = await E.sync({});                            // signed out: the people's copies go, the manual stays
OUT = { kept, cls: cls.map((c) => c && c.kind), page: await body(page), cache: page.headers.get("x-trc-cache"),
        index: (await index.json()).chapters.length, img: await body(img),
        after: caches.urls("trc-app-v1").filter((u) => u.startsWith("/manual")).length,
        files: O.APP_FILES.filter((f) => /manual|pmc_chart/.test(f)) };
""")
    assert out["kept"] == ["/manual", "/manual/img/a.zh-TW.webp", "/manual/img/b.zh-TW.webp", "/manual/index.json",
                           "/manual/schedule", "/manual/start"]
    assert out["cls"] == ["app", "app", "app", "app"]
    assert out["page"].endswith("/manual/schedule</body></html>") and out["cache"] == "offline"
    assert out["index"] == 2 and out["img"] == "img"
    assert out["after"] == 6
    assert sorted(out["files"]) == ["/api/v1/static/manual.js", "/api/v1/static/manual_charts.js",     # SP-559's charts
                                    "/api/v1/static/manual_search.js", "/api/v1/static/pmc_chart.js"]


def test_the_manual_page_asks_the_worker_to_keep_it():
    js = (STATIC / "manual.js").read_text("utf-8")
    assert 'postMessage({ type: "manual" })' in js
    sw = (STATIC / "sw.js").read_text("utf-8")
    assert 'd.type === "manual"' in sw and "engine.warmManual()" in sw


# ---------------------------------------------------------------------------- ? → 看手冊

_LINK = re.compile(r'data-manual="([^"]+)"|ManualLink\.href\("([^"]+)"\)|href="/manual/([a-z0-9-]+#[a-z0-9-]+)"')


def _links() -> list[tuple[str, str]]:
    out = []
    for p in sorted(STATIC.glob("*.html")) + sorted(STATIC.glob("*.js")):
        for m in _LINK.finditer(p.read_text("utf-8")):
            key = next(g for g in m.groups() if g)
            if "${" not in key and "<" not in key:
                out.append((p.name, key))
    return out


def test_every_see_the_manual_link_names_a_section_that_exists():
    links = _links()
    have = M.anchors("zh-TW")
    bad = [(f, k) for f, k in links if k not in have]
    assert not bad, bad
    files = {f for f, _k in links}
    # the 課表 and 入門 ? (SP-558's part): the schedule page, the settings page, the legend, the wizard
    assert {"schedule.html", "settings.html", "sesschip.js", "setup_wizard.js"} <= files
    keys = {k for _f, k in links}
    assert {"start#thresholds", "start#weight", "start#connect", "schedule#push", "schedule#colors",
            "schedule#replan", "schedule#edit", "schedule#prefs", "start#first-run"} <= keys


@needs_node
def test_the_help_popup_gets_the_link():
    js = r"""
const vm = require("vm"), fs = require("fs");
const made = [];
const el = (tag) => ({ tag, children: [], style: {}, dataset: {}, className: "", textContent: "", href: "", title: "",
  appendChild(c) { this.children.push(c); return c; }, querySelector(s) { return this.children.find((c) => c.className === "an-man-row") || null; },
  addEventListener() {}, contains: () => false, after() {}, remove() {}, prepend() {} });
const ctx = { console, URLSearchParams, location: { pathname: "/x", hash: "" }, innerWidth: 1000, innerHeight: 800,
  matchMedia: () => ({ matches: false }), navigator: {}, addEventListener() {}, setTimeout, clearTimeout, setInterval() {}, MutationObserver: class { observe() {} },
  sessionStorage: { getItem: () => "1", setItem() {} }, localStorage: { getItem: () => "1", setItem() {} },
  fetch: async () => ({ json: async () => ({}) }), TRC_SESSION: { mode: "demo", caps: [] },
  I18N: { locale: "zh-TW", t: (k) => ({ "shell.manual.see": "看手冊 →" }[k] || k), setLocale() {} } };
ctx.window = ctx;
ctx.document = { currentScript: { dataset: {} }, head: { appendChild() {}, querySelector: () => null }, body: { prepend() {}, appendChild() {} },
  createElement: (t) => { const e = el(t); e.setAttribute = () => {}; e.querySelector = (s) => (s === ".an-man" ? null : { addEventListener() {}, appendChild() {} }); e.querySelectorAll = () => []; made.push(e); return e; },
  addEventListener() {}, querySelectorAll: () => [], documentElement: { toggleAttribute() {} }, title: "" };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
const L = ctx.ManualLink;
const pop = el("div"); pop.querySelector = () => null;
L.decorate(pop, { dataset: { manual: "schedule#push" }, contains: () => false });
const row = pop.children[0], a = row && row.children[0];
console.log(JSON.stringify({ href: a && a.href, text: a && a.textContent, pe: pop.style.pointerEvents,
  good: L.href("start#thresholds"), bad: [L.href("../x"), L.href("a#b c"), L.href("")] }));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC / "shell.js")], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["href"] == "/manual/schedule#push" and out["text"] == "看手冊 →" and out["pe"] == "auto"
    assert out["good"] == "/manual/start#thresholds" and out["bad"] == [None, None, None]


# ---------------------------------------------------------------------------- the live chart

@needs_node
def test_the_live_chart_computes_what_the_app_computes():
    from backend.engine import load_guard
    loads = [0, 40, 60, 30, 0, 110, 70] * 6 + [200, 0, 150, 0, 0, 260, 90]
    js = r"""
const P = require(process.argv[1]);
const loads = JSON.parse(process.argv[2]);
const rows = P.simulate(loads.map((l, i) => ({ date: "d" + i, load: l })), { ctl0: 30, atl0: 30 });
console.log(JSON.stringify({ ctl: rows.map((r) => r.ctl), atl: rows.map((r) => r.atl), tsb: rows.map((r) => r.tsb),
  bands: [-0.5, -0.2, 0, 0.1, 0.4].map((f) => P.band(f).key) }));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC / "pmc_chart.js"), json.dumps(loads)], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    ctl = load_guard.recur(loads, 42.0, 0, 0, 30.0)
    atl = load_guard.recur(loads, 7.0, 0, 0, 30.0)
    assert out["ctl"] == pytest.approx(list(ctl), abs=1e-9)
    assert out["atl"] == pytest.approx(list(atl), abs=1e-9)
    # balance = yesterday's chronic − yesterday's acute (engine/wko5expr/evaluator: tsb)
    assert out["tsb"][1:] == pytest.approx([ctl[i - 1] - atl[i - 1] for i in range(1, len(loads))], abs=1e-9)
    assert out["bands"] == ["high_risk", "optimal", "grey", "fresh", "transition"]
