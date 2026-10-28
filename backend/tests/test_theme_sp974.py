"""
外觀 (SP-974; docs/spec/ui-theme.spec.md): the Trig palettes (static/theme.css) and the script that
picks one (static/theme.js), written inline first in every page's <head> by backend/i18n/pages.py;
the 設定 › 個人資料 › 外觀 menu; the default (Trig 米白 on a light system, 原本 dark on a dark one);
the chart colours of views/*.json mapped to the Trig palette's same hue. theme.js runs under node.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.i18n import pages as P

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
PAGES = sorted(p.stem for p in STATIC.glob("*.html"))
NAMES = ["trig-cream", "trig-badge", "classic", "classic-dark"]


# ---- every page carries it, before its own colours ---------------------------------------------------

def test_every_page_has_the_theme_first_in_its_head():
    assert len(PAGES) >= 28
    for page in PAGES:
        for loc in ("zh-TW", "en"):
            html = P.render(page, loc, False)
            head = html[html.index("<head"):]
            css, js = head.find('<style id="trc-theme">'), head.find('<script id="trc-theme-js">')
            own = head.find("<style>")                 # the page's own :root
            assert 0 < css < js, (page, loc)
            assert own == -1 or js < own, (page, loc)   # set before the page's colours: no flash
            assert "window.TrcTheme" in head, page


def test_the_visitor_pages_need_no_static_request():
    # /share/* and /share/pace pass a gate that lets only their own path through: inline, not linked
    for page in ("share", "pace", "intro", "login"):
        html = P.render(page, "zh-TW", False)
        assert "/api/v1/static/theme." not in html
        assert ':root[data-palette="trig-cream"]' in html and ':root[data-palette="trig-badge"]' in html


def test_the_inline_copy_has_no_comments_and_no_chinese():
    h = P.theme_head()
    assert "/*" not in h and not re.search(r"[一-鿿]", h)
    # the comment stripper relies on this: no `//` or `/*` inside a string of either file
    for f in ("theme.css", "theme.js"):
        code = re.sub(r"/\*.*?\*/", "", (STATIC / f).read_text("utf-8"), flags=re.S)
        for s in re.findall(r'"[^"\n]*"', code):
            assert "//" not in s and "/*" not in s, (f, s)


def test_the_render_cache_follows_the_theme_files(tmp_path, monkeypatch):
    css = tmp_path / "theme.css"
    css.write_text(":root[data-palette=\"trig-cream\"] { --bg: #000001; }", "utf-8")
    monkeypatch.setattr(P, "THEME_CSS_FILE", css)
    assert "#000001" in P.render("offline", "zh-TW", False)
    css.write_text(":root[data-palette=\"trig-cream\"] { --bg: #000002; }", "utf-8")
    import os
    os.utime(css, ns=(css.stat().st_mtime_ns + 10**9,) * 2)
    assert "#000002" in P.render("offline", "zh-TW", False)


# ---- the palettes ---------------------------------------------------------------------------------------

def _palette(name: str) -> dict:
    css = (STATIC / "theme.css").read_text("utf-8")
    block = re.search(r':root\[data-palette="%s"\] \{(.*?)\}' % name, css, re.S).group(1)
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))


def _lum(h):
    c = [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _cr(a, b):
    la, lb = sorted([_lum(a), _lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("name,accent,bg", [("trig-cream", "#2f6b64", "#f3efe4"), ("trig-badge", "#1f4a52", "#eef2f1")])
def test_the_palettes_are_the_mocks_and_pass_aa(name, accent, bg):
    p = _palette(name)
    assert p["--accent"] == accent and p["--bg"] == bg and p["--gold"] == "#f2c46d"
    # both families' names: --soft / --accent-soft, --ok / --good, --warn / --watch, --err / --bad
    assert p["--soft"] == p["--accent-soft"] and p["--ok"] == p["--good"] and p["--warn"] == p["--watch"]
    assert p["--err"] == p["--bad"] and p["--theme-color"] == accent
    for surf in ("--bg", "--panel"):
        for fg in ("--text", "--muted", "--accent", "--ok", "--warn", "--err"):
            assert _cr(p[fg], p[surf]) >= 4.5, (name, fg, surf)
        for i in range(1, 8):
            assert _cr(p[f"--s{i}"], p["--panel"]) >= 3, (name, i)
    assert _cr(p["--on-accent"], p["--accent"]) >= 4.5 and _cr(p["--accent"], p["--soft"]) >= 4.5


def test_the_original_colours_are_untouched():
    # 「原本」 is each page's own :root: theme.css only ever applies under a data-palette
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "theme.css").read_text("utf-8"), flags=re.S)
    for sel in re.findall(r"([^{}]+)\{", css):
        for one in sel.split(","):
            assert '[data-palette' in one, one.strip()
    # and the pages still define them, light and dark, as before
    for page in ("overview", "activity", "settings", "intro"):
        src = (STATIC / f"{page}.html").read_text("utf-8")
        assert ':root:not([data-theme="light"])' in src and ':root[data-theme="dark"]' in src, page


# ---- theme.js ------------------------------------------------------------------------------------------

def _js(expr: str, cookie: str = "", dark: bool = False):
    js = r"""
const vm = require("vm"), fs = require("fs");
const attrs = {};
const listeners = [];
const root = { getAttribute: (k) => (k in attrs ? attrs[k] : null), setAttribute: (k, v) => { attrs[k] = String(v); },
               removeAttribute: (k) => { delete attrs[k]; } };
const meta = { content: "", setAttribute: (k, v) => { meta[k] = v; } };
const VARS = { "--s2": "#d0632a", "--s3": "#3b6fb6", "--s6": "#6d8f2a", "--theme-color": "#2f6b64", "--muted": "#5a5a50" };
const ctx = { console, ATTRS: attrs, META: meta, EVENTS: [],
  document: { documentElement: root, cookie: process.argv[3], querySelector: () => meta },
  matchMedia: () => ({ matches: process.argv[4] === "1", addEventListener: (e, f) => listeners.push(f) }),
  getComputedStyle: () => ({ getPropertyValue: (k) => VARS[k] || "" }),
  CustomEvent: function (n, o) { this.type = n; this.detail = o.detail; } };
ctx.window = ctx;
ctx.dispatchEvent = (e) => ctx.EVENTS.push(e.type + ":" + e.detail.theme);
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/theme.js", "utf8"), ctx);
vm.runInContext("OUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, cookie, "1" if dark else "0"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


ATTR = "({palette: ATTRS['data-palette'] || null, theme: ATTRS['data-theme'] || null, cur: TrcTheme.current(), saved: TrcTheme.saved()})"


@needs_node
@pytest.mark.parametrize("dark,want", [
    (False, {"palette": "trig-cream", "theme": "light", "cur": "trig-cream", "saved": ""}),   # Trig 米白: the default
    (True, {"palette": None, "theme": None, "cur": "classic", "saved": ""}),                  # dark system: 原本 (its dark)
])
def test_the_default_follows_the_system(dark, want):
    assert _js(ATTR, dark=dark) == want


@needs_node
@pytest.mark.parametrize("cookie,want", [
    ("theme=trig-cream", {"palette": "trig-cream", "theme": "light"}),
    ("lang=en; theme=trig-badge", {"palette": "trig-badge", "theme": "light"}),
    ("theme=classic", {"palette": None, "theme": None}),
    ("theme=classic-dark", {"palette": None, "theme": "dark"}),
    ("theme=nonsense", {"palette": None, "theme": None}),         # unknown → the default (dark system here)
])
@pytest.mark.parametrize("dark", [False, True])
def test_a_choice_wins_over_the_system(cookie, want, dark):
    out = _js(ATTR, cookie=cookie, dark=dark)
    if cookie == "theme=nonsense":
        want = {"palette": None if dark else "trig-cream", "theme": None if dark else "light"}
    assert {"palette": out["palette"], "theme": out["theme"]} == want, (cookie, dark)


@needs_node
def test_set_remembers_in_the_cookie_and_applies():
    out = _js("(() => { const r = [];"
              " for (const n of TrcTheme.NAMES) { TrcTheme.set(n); r.push([n, document.cookie, ATTRS['data-palette'] || null,"
              " ATTRS['data-theme'] || null, META.content]); }"
              " TrcTheme.set(''); r.push(['', document.cookie]); return {r, ev: EVENTS}; })()")
    rows = out["r"]
    assert [x[0] for x in rows[:4]] == NAMES
    for name, cookie, pal, theme, bar in rows[:4]:
        assert cookie.startswith(f"theme={name}; Max-Age=31536000; Path=/; SameSite=Lax")
        assert pal == (name if name.startswith("trig-") else None)
        assert theme == {"trig-cream": "light", "trig-badge": "light", "classic": None, "classic-dark": "dark"}[name]
        assert bar == ("#2f6b64" if pal else "#2563eb")        # the browser bar: the original blue when 原本
    assert rows[4][1].startswith("theme=; Max-Age=0")           # forgotten: the default again
    assert out["ev"][:4] == [f"trc-theme:{n}" for n in NAMES]


@needs_node
def test_is_dark():
    assert _js("TrcTheme.isDark()", cookie="theme=trig-cream", dark=True) is False   # Trig is light on a dark system
    assert _js("TrcTheme.isDark()", cookie="theme=classic-dark") is True
    assert _js("TrcTheme.isDark()", cookie="theme=classic", dark=True) is True
    assert _js("TrcTheme.isDark()", cookie="theme=classic") is False


@needs_node
def test_chart_colours_take_the_trig_palettes_same_hue():
    spec = {"series": [{"color": "#2A78D6"}, {"color": "#eb6834"}, {"color": "#1baf7a"}, {"color": "#e34948"}],
            "rules": [{"color": "#52514e", "name": "x"}], "color": "hr"}
    expr = "TrcTheme.recolor(" + json.dumps(spec) + ")"
    out = _js(expr, cookie="theme=trig-cream")
    assert [s["color"] for s in out["series"]] == ["#3b6fb6", "#d0632a", "#6d8f2a", "#e34948"]   # blue, orange, green; red kept
    assert out["rules"][0]["color"] == "#5a5a50" and out["color"] == "hr"
    # 原本: nothing changes
    assert _js(expr, cookie="theme=classic") == spec
    assert _js(expr, cookie="theme=classic-dark") == spec


# ---- the setting ---------------------------------------------------------------------------------------

def test_the_settings_menu_has_the_four_choices():
    src = (STATIC / "settings.html").read_text("utf-8")
    sec = src[src.index('<section id="appearance">'):]
    sec = sec[:sec.index("</section>")]
    assert re.findall(r'<option value="([\w-]+)"', sec) == NAMES
    assert 'personal: ["general", "appearance", "map", "account"]' in src      # 個人資料, above 地圖
    assert "TrcTheme.set(sel.value)" in src
    for loc, names in (("zh-TW", ["Trig 米白", "Trig 深綠", "原本（跟隨系統）", "原本暗色"]),
                       ("en", ["Trig cream", "Trig deep green", "Original (follow system)", "Original dark"])):
        html = P.render("settings", loc, False)
        sec = html[html.index('<section id="appearance">'):]
        assert re.findall(r'<option value="[\w-]+"[^>]*>([^<]+)</option>', sec[:sec.index("</section>")]) == names
        cat = P.page_catalog(P.namespaces_for("settings"), loc)
        assert "{light}" in cat["settings.look.default"] and "{dark}" in cat["settings.look.default"]


# ---- the 趨勢／日曆 mode switch (Q2: mock D) -------------------------------------------------------------

MODESW_PAGES = ("charts", "schedule", "compliance", "templates")


def _modesw_css(src: str) -> str:
    i = src.index("/* 模式切換 (SP-974 Q2")
    return src[i:src.index("\n}\n", i) + 3]


def test_the_mode_switch_is_one_design_in_theme_tokens():
    blocks = {p: _modesw_css((STATIC / f"{p}.html").read_text("utf-8")) for p in MODESW_PAGES}
    assert len(set(blocks.values())) == 1, "the four pages carry the same block"
    css = blocks["charts"]
    # no colour of its own: no hex, only the page's tokens (and a neutral shadow)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert set(re.findall(r"var\((--[\w-]+)", css)) <= {"--accent", "--m-acc", "--bg", "--line", "--panel", "--text", "--muted"}
    assert ':is(.on, [aria-current="page"]) .mi { background: var(--m-acc); color: var(--panel); }' in css
    assert "@media (max-width: 620px)" in css and "flex: 1 1 0" in css          # a phone: the choices share the width
    for p in MODESW_PAGES:
        src = (STATIC / f"{p}.html").read_text("utf-8")
        assert "#b4530f" not in src and "#8f3f0a" not in src, p                 # the dark orange is gone
        cards = re.findall(r'class="mcard[^"]*"[^>]*>\s*(<span class="mi"><svg\b)?', src)
        assert len(cards) in (2, 3) and all(cards), p                          # every icon in its square
