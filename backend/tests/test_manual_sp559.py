"""SP-559: the manual's 名詞表 (backend/manual/<locale>/glossary.md, static/manual_charts.js, /manual/terms.json,
shell.js ManualLink's 「名詞表：…」 row).

  - the glossary covers the training terms on the screens (the glossary.json metric words), Chinese and
    English with the same anchors; every term has its 「簡單說」, a folded 「深入」 and at least one picture
    (a screenshot or a live chart), and the words the screens write for it (:::terms)
  - every live chart is one manual_charts.js draws; where the app has a rule of its own the chart computes
    what the Python side computes (zones, RPE load, grade adjustment); the published formulas (weighted
    power, training load, CP, the anaerobic reserve, drift) are checked against a reference written here
  - /manual/terms.json (public) gives every word with a section that exists
  - the pages' ? popups call ManualLink.decorate, which adds 「名詞表：…」 links for the words a text names
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
GLOSSARY_JSON = Path(__file__).resolve().parents[1] / "i18n" / "glossary.json"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
LOCALES = ("zh-TW", "en")

TERMS = ["training-load", "chronic-load", "acute-load", "load-balance", "ramp", "load-ratio", "weighted-power",
         "intensity", "cp", "mtp", "anaerobic-capacity", "anaerobic-reserve", "training-effect", "lthr", "aet",
         "hrr", "zones", "time-in-zones", "drift", "rpe", "gap", "vam", "effort-distance", "phases"]


def _glossary(loc):
    ch = M.chapter(loc, "glossary")
    assert ch is not None, loc
    return ch


# ---------------------------------------------------------------------------- the chapter

def test_the_glossary_has_every_term_in_both_languages():
    for loc in LOCALES:
        assert [s.anchor for s in _glossary(loc).sections] == TERMS, loc
    assert M.anchors("zh-TW") == M.anchors("en")


@pytest.mark.parametrize("loc", LOCALES)
def test_every_term_has_its_simple_part_a_deep_part_a_picture_and_its_words(loc):
    ch = _glossary(loc)
    page = M._fill(loc, M.chapters(loc), ch)["main"]
    for s in ch.sections:
        assert len(s.simple_text) > 40 and len(s.deep_text) > 80, (loc, s.anchor)
        pics = re.findall(r'class="m-fig"|class="m-chart" data-chart="', s.simple_html + s.deep_html)
        assert pics, f"{loc}#{s.anchor}: no picture"
        assert s.terms, f"{loc}#{s.anchor}: no :::terms"
        assert f'<details class="m-deep" id="{s.anchor}--deep">' in page
        # the deep part names where it comes from
        assert re.search(r"來源|Sources", s.deep_html), (loc, s.anchor)


def test_the_glossary_covers_the_screens_metric_words():
    """The metric words of the translation glossary (what the screens write) are all in the 名詞表."""
    words = {w for s in _glossary("zh-TW").sections for w in s.terms}
    gl = json.loads(GLOSSARY_JSON.read_text("utf-8"))["terms"]
    metric = ["長期訓練負荷", "短期負荷", "負荷平衡", "訓練負荷", "強度比", "加權功率", "模型閾值功率", "有氧訓練效果",
              "無氧訓練效果", "無氧容量", "無氧儲備", "AeT", "LTHR", "臨界功率", "基礎期", "專項期", "減量期", "恢復期",
              "長期負荷週增量", "過度疲勞"]
    on_screen = {z for t in gl for z in t["zh"]}
    for w in metric:
        assert w in on_screen or w in ("長期負荷週增量", "訓練負荷", "無氧訓練效果", "有氧訓練效果"), w
        assert w in words, f"{w} is not in the glossary"
    en_words = {w for s in _glossary("en").sections for w in s.terms}
    for w in ("Chronic load", "Acute load", "Load balance", "Training load", "Intensity", "Weighted power",
              "Modelled threshold power", "Anaerobic capacity", "Anaerobic reserve", "AeT", "LTHR", "CP"):
        assert w in en_words, w


def test_every_live_chart_is_one_the_page_draws():
    names = _charts_js()["names"]
    for loc in LOCALES:
        for s in _glossary(loc).sections:
            for name in re.findall(r'data-chart="([a-z]+)"', s.simple_html + s.deep_html):
                assert name == "pmc" or name in names, (loc, s.anchor, name)
    html = (STATIC / "manual.html").read_text("utf-8")
    assert "manual_charts.js" in html and html.index("pmc_chart.js") < html.index("manual_charts.js") < html.index("manual.js")


def test_the_chart_text_is_in_both_languages():
    js = (STATIC / "manual_charts.js").read_text("utf-8")
    keys = {k for k in re.findall(r't\("(gl\.[a-z0-9_]+)"', js) if not k.endswith("_") and k != "gl.z"}
    keys |= {f"gl.z{i}" for i in range(1, 7)} | {f"gl.model_{m}" for m in ("lthr", "hrr", "hrmax")}
    keys |= {f"gl.rpe_{k}" for k in ("easy", "moderate", "hard", "very_hard", "max")}
    keys |= {f"gl.wbal_{k}" for k in ("empty", "deep", "ok")} | {f"gl.drift_{k}" for k in ("under", "at", "over")}
    for loc in LOCALES:
        cat = json.loads((STATIC / "i18n" / loc / "manual.json").read_text("utf-8"))
        missing = sorted(k for k in keys | {"terms", "chart.ratio"} if not cat.get(k))
        assert not missing, (loc, missing)


def test_the_parser_takes_chart_arguments_and_terms():
    ch = M.parse("---\nid: x\n---\n## A {#a}\n:::terms\n詞一\nWord two\n:::\ntext\n:::chart pmc ratio=1\ncap\n:::\n",
                 "zh-TW")
    s = ch.sections[0]
    assert s.terms == ["詞一", "Word two"]
    assert 'data-chart="pmc" data-args="ratio=1"' in s.simple_html and 'class="m-terms"' in s.simple_html
    with pytest.raises(M.ManualError):
        M.parse("---\nid: x\n---\n## A {#a}\n:::chart pmc a=<b>\n:::\n", "zh-TW")
    with pytest.raises(M.ManualError):
        M.parse("---\nid: x\n---\n## A {#a}\n:::chart nope\n:::\n", "zh-TW")
    with pytest.raises(M.ManualError):
        M.parse("---\nid: x\n---\n:::terms\nx\n:::\n", "zh-TW")          # not in a section


# ---------------------------------------------------------------------------- the charts compute what the app does

def _node(js: str, *args: str) -> dict:
    r = subprocess.run([NODE, "-e", js, *args], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _charts_js() -> dict:
    if NODE is None:
        js = (STATIC / "manual_charts.js").read_text("utf-8")
        return {"names": re.findall(r"DRAW\.([a-z]+) = ", js)}
    return _node("const C = require(process.argv[1]); console.log(JSON.stringify({ names: C.NAMES }));",
                 str(STATIC / "manual_charts.js"))


@needs_node
def test_the_zones_chart_has_the_schedules_zones():
    from backend.engine import hr_profile as HP
    cases = [(168, 190, 52), (171, 197, 47), (150, 181, 60), (183, 205, 41), (145, 188, 70)]
    out = _node(r"""
const C = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify({ ratios: C.RATIOS,
  edges: cases.map(([lthr, mhr, rhr]) => ["lthr", "hrr", "hrmax"].map((m) => C.zoneEdges(m, { lthr, mhr, rhr }))),
  round: [0.5, 1.5, 2.5, 162.5, 163.5, 162.45].map(C.pyRound) }));
""", str(STATIC / "manual_charts.js"), json.dumps(cases))
    assert {k: tuple(v) for k, v in out["ratios"].items()} == {k: tuple(v) for k, v in HP.DEFAULT_RATIOS.items()}
    assert out["round"] == [round(x) for x in (0.5, 1.5, 2.5, 162.5, 163.5, 162.45)]
    for (lthr, mhr, rhr), got in zip(cases, out["edges"]):
        for model, edges in zip(("lthr", "hrr", "hrmax"), got):
            rows = HP.zone_rows(model, lthr=lthr, mhr=mhr, rhr=rhr, acc=None)["rows"]
            assert edges == [int(r[3]) for r in rows[:5]], (model, lthr, mhr, rhr)


@needs_node
def test_the_rpe_chart_has_the_defaults_of_the_rpe_load():
    from backend.engine import rpe_load as R
    out = _node(r"""
const C = require(process.argv[1]);
console.log(JSON.stringify({ rpe: C.RPE, h: C.RPE.map((r) => C.rpePerHour(r[0])), l: C.rpeLoad("hard", 90) }));
""", str(STATIC / "manual_charts.js"))
    assert [(k, n, f) for k, n, f in out["rpe"]] == [(k, R.CR10[k], R.DEFAULT_IF[k]) for k in R.IDS]
    assert out["h"] == pytest.approx([R.DEFAULT_TSS_H[k] for k in R.IDS])
    assert out["l"] == pytest.approx(R.DEFAULT_TSS_H["hard"] * 1.5)


@needs_node
def test_the_grade_chart_is_the_apps_minetti():
    from backend.engine.algorithms import minetti
    grades = [-0.5, -0.3, -0.12, -0.05, 0.0, 0.03, 0.1, 0.2, 0.35, 0.45, 0.6]
    out = _node(r"""
const C = require(process.argv[1]);
const g = JSON.parse(process.argv[2]);
console.log(JSON.stringify({ f: g.map((x) => C.gradeFactor(x)), t: C.trail(480, 0.1) }));
""", str(STATIC / "manual_charts.js"), json.dumps(grades))
    assert out["f"] == pytest.approx([minetti.grade_factor(g) for g in grades], abs=1e-12)
    assert out["t"]["gap"] == pytest.approx(480 / minetti.grade_factor(0.1))
    assert out["t"]["vam"] == pytest.approx(3600 / 480 * 1000 * 0.1)       # 7.5 km/h × 10 % = 750 m/h
    assert out["t"]["ep"] == pytest.approx(2.0)                             # 1 km + 100 m ÷ 100


def _np(p):
    roll = [sum(p[i - 29:i + 1]) / 30 for i in range(29, len(p))]
    return (sum(v ** 4 for v in roll) / len(roll)) ** 0.25


def _wbal(p, cp, w0):
    w, out = w0, []
    for x in p:
        w = w - (x - cp) if x > cp else w + (cp - x) * (w0 - w) / w0
        out.append(w)
    return out


@needs_node
def test_the_power_cp_reserve_and_drift_charts_follow_their_published_formulas():
    out = _node(r"""
const C = require(process.argv[1]);
const p = C.surges(190, 0.3), iv = C.intervals(250, 1.12, 120);
console.log(JSON.stringify({ p, wp: C.weighted(p), avg: C.mean(p), load: C.load(3600, C.weighted(p), 250),
  flat: C.weighted(new Array(600).fill(200)), iv, wb: C.wbal(iv, 250, 15000),
  cp: [C.cpPower(180, 260, 13000), C.tte(286, 260, 13000), C.tte(250, 260, 13000)],
  drift: [C.drift(140, 140), C.drift(140, 146)], bands: [0, 3.4, 3.5, 5, 5.1].map(C.driftBand) }));
""", str(STATIC / "manual_charts.js"))
    p = out["p"]
    assert len(p) == 3600 and out["avg"] == pytest.approx(190)
    assert out["wp"] == pytest.approx(_np(p)) and out["wp"] > out["avg"]
    assert out["flat"] == pytest.approx(200)                                # steady power: weighted = average
    assert out["load"] == pytest.approx((out["wp"] / 250) ** 2 * 100)       # an hour = Intensity² × 100
    assert out["wb"] == pytest.approx(_wbal(out["iv"], 250, 15000))
    assert out["cp"][0] == pytest.approx(260 + 13000 / 180)
    assert out["cp"][1] == pytest.approx(500) and out["cp"][2] is None      # Infinity → null in JSON
    assert out["drift"] == pytest.approx([0, (1 - 140 / 146) * 100])
    assert out["bands"] == ["under", "under", "at", "at", "over"]


# ---------------------------------------------------------------------------- /manual/terms.json

def test_the_words_are_public_and_point_at_sections_that_exist(c):  # noqa: F811
    for loc in LOCALES:
        r = c.get(f"/manual/terms.json?lang={loc}")
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body["locale"] == loc and len(body["terms"]) >= 40
        have = M.anchors(loc)
        for t in body["terms"]:
            assert t["url"].startswith("/manual/") and t["url"][len("/manual/"):] in have, t
        lens = [len(t["term"]) for t in body["terms"]]
        assert lens == sorted(lens, reverse=True)                            # longest first
    zh = {t["url"] for t in M.terms("zh-TW")}
    en = {t["url"] for t in M.terms("en")}
    assert zh == en == {f"/manual/glossary#{a}" for a in TERMS}


# ---------------------------------------------------------------------------- ? → 名詞表

POPUP_PAGES = ("overview.html", "charts.html", "activity.html", "compliance.html", "injuries.html", "templates.html",
               "schedule.html")


def test_every_pages_help_popup_asks_for_the_glossary_links():
    for name in POPUP_PAGES:
        src = (STATIC / name).read_text("utf-8")
        assert "ManualLink?.decorate(" in src, name
        assert "ManualLink?.into(e)" in src, name                          # moving into the popup keeps it
        assert re.search(r'closest\?\.\("\.(qtip-pop|qpop)"\)\) return', src), name   # a click on its link
    sh = (STATIC / "shell.js").read_text("utf-8")
    assert ".qpop:has(.an-man)" in sh and '".qtip-pop, .qpop, .an-qpop"' in sh


def _shell(texts: list[str], terms: list[dict], skip: str | None = None) -> dict:
    js = r"""
const vm = require("vm"), fs = require("fs");
const terms = JSON.parse(process.argv[2]), texts = JSON.parse(process.argv[3]), skip = process.argv[4] || "";
const el = (tag) => ({ tag, children: [], style: {}, dataset: {}, className: "", textContent: "", href: "", title: "", isConnected: true,
  appendChild(c) { this.children.push(c); return c; }, querySelector(s) { return null; },
  addEventListener() {}, contains: () => false, after() {}, remove() {}, prepend() {} });
const fetched = [];
const ctx = { console, URLSearchParams, location: { pathname: "/x", hash: "" }, innerWidth: 1000, innerHeight: 800,
  matchMedia: () => ({ matches: false }), navigator: {}, addEventListener() {}, clearTimeout, setInterval() {}, MutationObserver: class { observe() {} },
  sessionStorage: { getItem: () => "1", setItem() {} }, localStorage: { getItem: () => "1", setItem() {} },
  fetch: async (u) => { fetched.push(u); return u.startsWith("/manual/terms.json") ? { ok: true, json: async () => ({ terms }) } : { ok: true, json: async () => ({}) }; },
  TRC_SESSION: { mode: "demo", caps: [] },
  I18N: { locale: "zh-TW", t: (k) => ({ "shell.manual.glossary": "名詞表：", "shell.manual.see": "看手冊 →" }[k] || k), setLocale() {} } };
ctx.setTimeout = () => 0;
ctx.window = ctx;
ctx.document = { currentScript: { dataset: {} }, head: { appendChild() {}, querySelector: () => null }, body: { prepend() {}, appendChild() {} },
  createElement: (t) => { const e = el(t); e.setAttribute = () => {}; e.querySelector = (s) => (s === ".an-man" ? null : { addEventListener() {}, appendChild() {} }); e.querySelectorAll = () => []; return e; },
  createTextNode: (t) => ({ text: t }),
  addEventListener() {}, querySelectorAll: () => [], documentElement: { toggleAttribute() {} }, title: "" };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => {
  const L = ctx.ManualLink;
  const out = [];
  for (const t of texts) {
    const pop = el("div"); pop.textContent = t;
    L.decorate(pop, { dataset: skip ? { manual: skip } : {}, contains: () => false });
    await L.terms(); await new Promise((r) => setImmediate(r));
    const rows = pop.children.filter((r) => /an-gl-row/.test(r.className));
    out.push(rows.map((r) => ({ lab: r.children[0].textContent, links: r.children.filter((a) => a.tag === "a").map((a) => [a.href, a.textContent]) })));
  }
  console.log(JSON.stringify({ out, fetched }));
})();
"""
    return _node(js, str(STATIC / "shell.js"), json.dumps(terms), json.dumps(texts), skip or "")


@needs_node
def test_a_popup_naming_glossary_words_links_to_them():
    terms = M.terms("zh-TW")
    out = _shell(["近 42 天的長期訓練負荷（Chronic load）、短期負荷（Acute load）、負荷平衡（Load balance）。",
                  "沒有任何名詞的說明。",
                  "LTHR、AeT、CP、無氧容量、模型閾值功率、強度比都用它。",
                  "CPU 和 ABCP 不是 CP。"], terms)
    first, none, many, latin = out["out"]
    assert first[0]["lab"].startswith("名詞表：")
    assert [h for h, _w in first[0]["links"]] == ["/manual/glossary#chronic-load", "/manual/glossary#acute-load",
                                                  "/manual/glossary#load-balance"]   # 長期訓練負荷, not 訓練負荷
    assert none == []
    assert len(many[0]["links"]) == 4                                         # at most four, in the text's order
    assert [w for _h, w in many[0]["links"]] == ["LTHR", "AeT", "CP", "無氧容量"]
    assert [w for _h, w in latin[0]["links"]] == ["CP"]                       # whole words only
    assert out["fetched"].count("/manual/terms.json?lang=zh-TW") == 1         # loaded once per page


@needs_node
def test_english_words_match_as_written_and_a_link_already_there_is_not_repeated():
    out = _shell(["Chronic load and chronic load; high intensity is not the Intensity metric.", "LTHR and HR load."],
                 M.terms("en"), skip="glossary#lthr")
    a, b = out["out"]
    assert [w for _h, w in a[0]["links"]] == ["Chronic load", "Intensity"]   # "intensity" in a sentence is not it
    assert [h for h, _w in b[0]["links"]] == ["/manual/glossary#training-load"]   # LTHR already has its link


def test_the_settings_help_that_is_about_a_term_links_to_it():
    html = (STATIC / "settings.html").read_text("utf-8")
    assert 'data-manual="glossary#rpe"' in html and 'data-manual="glossary#training-load"' in html
