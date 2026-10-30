"""SP-1067: the 介紹頁 keeps its reader — a link to a method or to the sources opens a dialog on the page instead
of the 方法 / 關於 page, and the 方法 page's way in is 關於's 「計算方法與出處」.

- GET /api/v1/intro/methods (public, signed out): every 方法 card's words in the request's language — the
  groups, each card's name, face, calibration and detail (the same detail as /methods shows), and every source
- every link of the 介紹頁 to a method opens the dialog (data-m: a card, @all, @calibrated); its href stays for
  a modified click; the waterfall's layers too (the 「有出處，不是黑盒子」 block and its @sources dialog are gone:
  為什麼信得過 already says it)
- 關於: its contents' 「計算方法與出處」 goes straight to /methods, its own list of methods is gone (the 方法
  page lists every source and the SuperPower port's credit at its end)
Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.api import about as AB
from backend.api import methods as M
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, _clean_env, _client, app, c, env, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
LOCALES = ("zh-TW", "en")
API = "/api/v1/intro/methods"
KEYS = ("@all", "@calibrated")


def _intro() -> str:
    return (STATIC / "intro.html").read_text("utf-8")


# ---------------------------------------------------------------------------- the words

@pytest.mark.parametrize("loc", LOCALES)
def test_the_words_open_signed_out_in_each_language(loc, c):  # noqa: F811
    r = c.get(f"{API}?lang={loc}")
    assert r.status_code == 200, r.text
    assert "public" in r.headers.get("cache-control", "")
    d = r.json()
    assert set(d) == {"groups", "models", "sources", "credit"}
    assert list(d["groups"]) == list(M.GROUPS) and all(d["groups"].values())
    assert [m["id"] for m in d["models"]] == [m.id for m in M.MODELS]
    for m in d["models"]:
        assert set(m) == {"id", "group", "name", "short", "calib", "chips", "calib_text", "html"}, m["id"]
        assert m["group"] in M.GROUPS and m["calib"] == M.BY_ID[m["id"]].calib
        assert m["name"] and m["short"] and m["calib_text"] and 'class="md-chip' in m["chips"]
        assert 'class="md-plain"' in m["html"] and 'class="md-src"' in m["html"]
        assert " id=" not in m["html"] and " id=" not in m["chips"]    # the dialog lives in the 介紹頁's document
    assert d["sources"].count("<li>") == len(AB.SOURCES)
    # nothing in it leads off the 介紹頁 but the literature, each in a new tab
    words = r.text
    hrefs = re.findall(r'href=\\?"([^"\\]+)', words)
    assert hrefs and all(h.startswith("https://") for h in hrefs), [h for h in hrefs if not h.startswith("https://")]
    assert words.count("target=\\\"_blank\\\"") == len(hrefs)
    han = re.search(r"[一-鿿]", d["models"][0]["name"])
    assert bool(han) == (loc == "zh-TW")


def test_the_dialog_says_what_the_methods_page_says(c):  # noqa: F811
    """…but the chip naming the 首頁 waterfall layer / 賽事周期: the reader is on the 介紹頁 already (the owner,
    2026-10-30)."""
    layer = re.compile(r'<span class="md-chip layer">[^<]*</span>')
    for loc in LOCALES:
        page = c.get(f"/methods?lang={loc}", headers=HTML).text
        assert layer.search(page)                                      # the 方法 page keeps them
        for m in c.get(f"{API}?lang={loc}").json()["models"]:
            assert "md-chip layer" not in m["html"], (loc, m["id"])
            assert m["html"] in layer.sub("", page), (loc, m["id"])     # the card's own detail, word for word


def test_the_path_is_public_exactly():
    from backend.accounts import login as LG
    from backend.api import intro as I
    assert API in I.PUBLIC_PATHS and API in LG.PUBLIC_PATHS
    assert not LG.is_public(API + "/x")


# ---------------------------------------------------------------------------- the 介紹頁's links

def test_every_link_to_a_method_or_the_sources_opens_the_dialog():
    html = _intro()
    links = re.findall(r'<a [^>]*href="(?:/methods[^"]*|/about[^"]*)"[^>]*>', html)
    links = [a for a in links if 'href="/about"' not in a]                       # the bar's and footer's 關於
    assert len(links) == 5                     # the four cards, the 賽事周期
    keys = []
    for a in links:
        m = re.search(r'data-m="([^"]+)"', a)
        assert m, a
        keys.append(m.group(1))
    for k in keys:
        assert k in KEYS or k in M.BY_ID, k
    assert set(KEYS) <= set(keys)
    why = html[html.index('id="why"'):html.index("</section>", html.index('id="why"'))]
    assert re.findall(r'data-m="([^"]+)"', why) == ["@all", "zones", "@calibrated", "durability"]
    assert 'id="sources"' not in html and 'data-m="@sources"' not in html
    # the waterfall's layers: the card of each, as a dialog too
    assert "h.dataset.m = METHOD_CARD[key]" in html
    # the dialog itself, and its words in both languages
    assert 'id="m-modal"' in html and 'aria-labelledby="m-title"' in html
    assert re.search(r'fetch\(API \+ "methods\?lang="', html)
    for loc in LOCALES:
        cat = json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))
        for k in ("mp.all.h", "mp.all.lead", "mp.cal.h", "mp.cal.lead", "mp.loading",
                  "mp.error", "mp.foot"):
            assert cat.get(k), (loc, k)
            assert f'"intro.{k}"' in html, k


def test_the_dialog_sends_no_one_to_the_methods_page():
    html = _intro()
    dlg = html[html.index('id="m-modal"'):html.index("</div>\n</div>", html.index('id="m-modal"'))]
    assert "/methods" not in dlg and "/about" not in dlg
    assert 'href="/login"' in dlg                                      # the way it does send them: sign up


# ---------------------------------------------------------------------------- 關於

@pytest.fixture
def owner(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        yield cl


@pytest.mark.parametrize("loc", LOCALES)
def test_the_about_pages_methods_lead_to_the_methods_page(loc, owner):
    r = owner.get(f"/about?lang={loc}", headers=HTML)
    assert r.status_code == 200
    toc = r.text.split('<nav class="ab-toc"', 1)[1].split("</nav>", 1)[0]
    cat = json.loads((STATIC / "i18n" / loc / "about.json").read_text("utf-8"))
    assert f'<a href="/methods">{cat["methods.h"]}</a>' in toc
    assert '<a href="#data">' in toc                                   # the others stay on the page
    assert 'id="methods"' not in r.text and "m-weighted" not in r.text  # the section that listed them is gone
    assert r.text.count('href="/methods"') == 1                        # the chip, nothing else
