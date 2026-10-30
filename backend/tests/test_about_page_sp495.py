"""SP-495: the 關於 page (api/about.py, static/about.html, static/i18n/<locale>/about.json).

  - /about opens signed out (sign-in mode), in the owner's app and in the demo, in Chinese and English
  - it shows the version (backend.__version__) and the build (the commit), the third-party data and
    software with their licences, and the trademark notice; the methods and their sources are the 方法
    page's (SP-1067: the contents' 「計算方法與出處」 goes there, no section of its own here)
  - the two languages have the same keys; every data source has its name and description
  - every source link is an external https address; no internal notes, no doc paths, no old product name
  - the shell's menu reaches it: last in 更多, and in a signed-out visitor's bar next to the manual
Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.api import about as A
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, _clean_env, _client, app, c, env, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
LOCALES = ("zh-TW", "en")


def _cat(loc: str) -> dict:
    """The file's text with the product's name filled in, as the page shows it (SP-971: {brand})."""
    from backend.i18n.pages import brand
    raw = json.loads((STATIC / "i18n" / loc / "about.json").read_text("utf-8"))
    return {k: v.replace("{brand}", brand(loc)) if isinstance(v, str) else v for k, v in raw.items()}


def _text(html: str) -> str:
    """The page's visible words (markup and scripts dropped)."""
    body = html.split("<main", 1)[1].split("</main>", 1)[0]
    return re.sub(r"<[^>]+>", " ", body)


@pytest.fixture
def owner(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        yield cl


# ---------------------------------------------------------------------------- the words

def test_both_languages_have_the_same_keys_and_every_item_its_words():
    zh, en = _cat("zh-TW"), _cat("en")
    assert set(zh) == set(en)
    for loc, cat in (("zh-TW", zh), ("en", en)):
        for did, _links in A.DATA:
            assert cat.get(f"d.{did}.name") and len(cat.get(f"d.{did}.desc", "")) > 15, (loc, did)
        for sec in ("version", "methods", "data", "software", "trademarks"):
            assert cat.get(f"{sec}.h"), (loc, sec)
    # a data source in about.json without a row in api/about.py would never be shown; no methods (SP-1067)
    assert not any(k.startswith("m.") for k in zh)
    assert {k.split(".")[1] for k in zh if k.startswith("d.")} == {d for d, _ in A.DATA}


def test_every_source_is_dated_and_links_only_https():
    """The one list of the literature (the 方法 page shows every one of it, test_methods_sp973)."""
    for sid, s in A.SOURCES.items():
        assert re.search(r"\b(19|20)\d\d\b", s.cite) or sid in ("friel_ramp", "friel_zones", "superpower",
                                                                 "daniels"), sid
        assert s.url is None or s.url.startswith("https://"), sid


def test_methods_are_public_literature_never_the_old_product():
    """SP-498: no WKO5 on screen; the methods are named by their authors."""
    for loc in LOCALES:
        words = json.dumps(_cat(loc), ensure_ascii=False)
        assert not re.search(r"WKO", words), loc
    cites = " ".join(s.cite + " " + (s.url or "") for s in A.SOURCES.values())
    assert "WKO" not in cites.upper()
    for author in ("Coggan", "Banister", "Monod", "Minetti", "Tobler", "Pandolf", "Riegel", "Tanaka", "Foster", "Borg",
                   "Palladino", "Friel", "Daniels"):
        assert author in cites, author


DENY = re.compile(r"WKO|\.md\b|docs/|擁有者|\bowner\b|Obsi[d]ian|yichlin|localhost|127\.0\.0\.1", re.I)


@pytest.mark.parametrize("loc", LOCALES)
def test_the_page_has_no_internal_notes_and_links_only_outside(loc, owner):
    r = owner.get(f"/about?lang={loc}", headers=HTML)
    assert r.status_code == 200
    text = _text(r.text)
    assert not DENY.findall(text), (loc, DENY.findall(text))
    main = r.text.split("<main", 1)[1].split("</main>", 1)[0]
    hrefs = re.findall(r'href="([^"]+)"', main)
    assert hrefs
    for h in hrefs:
        # SP-1067: and the 方法 page — 關於's 「計算方法與出處」 is the way into it
        assert h.startswith("https://") or re.fullmatch(r"#[a-z]+", h) or h == "/methods", h
        assert not DENY.search(h), h
    # every external link opens in a new tab without handing over the page
    assert main.count('target="_blank"') == main.count('rel="noopener"') == len([h for h in hrefs if h.startswith("https")])


# ---------------------------------------------------------------------------- the page

def test_the_page_in_both_languages_with_version_sources_licences_and_trademarks(owner):
    from backend import __version__
    from backend.engine import debug_view as DV
    zh = owner.get("/about?lang=zh-TW", headers=HTML)
    assert zh.status_code == 200 and '<html lang="zh-TW">' in zh.text
    assert zh.headers["cache-control"] == "private, no-cache"
    en = owner.get("/about?lang=en", headers=HTML)
    assert en.status_code == 200 and '<html lang="en">' in en.text
    for r, loc in ((zh, "zh-TW"), (en, "en")):
        cat = _cat(loc)
        t = r.text
        assert f"<code>{__version__}</code>" in t and f"<code>{DV.git_sha()}</code>" in t
        for sec in ("version", "data", "software", "trademarks"):
            assert f'<section class="ab-sec" id="{sec}">' in t
        assert 'id="methods"' not in t and '<a href="/methods">' in t          # SP-1067: the 方法 page
        for key in ("h1", "d.osm.desc", "d.cwa.desc", "d.openmeteo.desc"):
            assert cat[key].split("（")[0][:20] in t, (loc, key)
        assert "https://www.openstreetmap.org/copyright" in t and "ODbL" in t
        assert "Leaflet" in t and "BSD-2-Clause" in t and "Apache ECharts" in t
        assert 'data-page="about"' in t                                          # the shell marks the page
    assert "Intensity Factor® 是 TrainingPeaks, LLC 的註冊商標" in zh.text         # README's notice (SP-391)
    assert "TSS®" in zh.text and "Intensity Factor® are registered trademarks of TrainingPeaks, LLC" in en.text
    assert "沒有任何關係，也沒有獲得它們的授權或背書" in zh.text
    assert "not affiliated with, authorised or endorsed by" in en.text
    # the English page has no Chinese of its own (the catalog is complete)
    assert not re.search(r"[一-鿿]", _text(en.text).replace("TrailRunCoach", ""))


def test_signed_out_the_page_opens(c):  # noqa: F811
    for loc in LOCALES:
        r = c.get(f"/about?lang={loc}", headers=HTML)
        assert r.status_code == 200, (loc, r.status_code, r.text[:200])
        assert '"mode": "signed_out"' in r.text                    # the bar: the manual, 關於 and 登入
    # only /about itself: next to it everything stays closed
    assert c.get("/aboutx", headers=HTML).status_code in (302, 401)
    assert c.get("/about/x", headers=HTML).status_code in (302, 401, 404)
    assert c.get("/api/v1/plan").status_code == 401


def test_the_demo_has_the_page(monkeypatch, tmp_path):
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        assert cl.get("/about", headers=HTML).status_code == 200


# ---------------------------------------------------------------------------- the menu

def test_the_menu_links_to_the_page_last_in_more_and_for_signed_out_visitors():
    js = (STATIC / "shell.js").read_text("utf-8")
    assert re.search(r'\{ id: "about", href: "/about"', js)
    ids = re.findall(r'\{ id: "(\w+)"', js)
    assert ids[-1] == "about"
    more = re.findall(r'"(\w+)"', re.search(r'const MORE = new Set\(\[([^\]]*)\]\)', js).group(1))
    assert more[-1] == "about"
    assert re.findall(r'"(\w+)"', re.search(r'const PUBLIC_PAGES = new Set\(\[([^\]]*)\]\)', js).group(1)) == [
        "manual", "about"]
    # a page for reading: no floating suggestion box (like the manual; the coach board neither, SP-790)
    gate = ('if ((!cur || !PUBLIC_PAGES.has(cur.id)) && me?.dataset.page !== "coach_board"'
            ' && me?.dataset.page !== "methods") {')                 # SP-973: 方法 is for reading too
    assert gate in js
    sg = js.index(gate)
    assert js.index('"/api/v1/static/suggestions.js"') > sg
    for loc in LOCALES:
        sh = json.loads((STATIC / "i18n" / loc / "shell.json").read_text("utf-8"))
        for k in ("page.about.name", "page.about.short", "page.about.purpose"):
            assert sh.get(k), (loc, k)


def test_the_page_is_public_by_its_exact_path():
    from backend.accounts import login as LG
    assert "/about" in LG.PUBLIC_PATHS
    assert not any("/about".startswith(p) for p in LG.PUBLIC_PREFIXES)
