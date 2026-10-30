"""SP-1073: the 介紹頁's links to the manual's text open the chapter in a dialog on the page, not the manual.

- every 「手冊：…」 / 「看手冊 →」 link (class more, /manual/<chapter>[#<section>]) carries data-man; so do the
  bar's and the footer's 操作手冊 since the page dialog (test_intro_page_dialog): the contents, embedded
- the dialog frames /manual/<chapter>?embed=1 — the chapter alone: no app bar (shell.js), contents, search
  or pager (manual.html's data-embed); a page without ?embed=1 keeps its bar
- the dialog sends no one to the manual: its own button is the sign-up
Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.api import manual as MA
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, app, c, env, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
LOCALES = ("zh-TW", "en")


def _intro() -> str:
    return (STATIC / "intro.html").read_text("utf-8")


def test_every_link_to_the_manuals_text_opens_the_dialog():
    html = _intro()
    links = re.findall(r'<a [^>]*href="/manual[^"]*"[^>]*>', html)
    text = [a for a in links if 'class="more"' in a]
    assert len(text) == 8                      # 課表、名詞表、入門 and the five FAQ answers
    for a in text:
        assert " data-man" in a, a
        cid = re.search(r'href="/manual/([a-z0-9-]+)', a).group(1)
        assert MA.chapter("zh-TW", cid), cid
    nav = [a for a in links if 'class="more"' not in a]
    assert len(nav) == 2 and all(" data-man" in a for a in nav)            # the bar's and footer's 操作手冊
    # the dialog and how it frames the chapter
    assert 'id="man-modal"' in html and '<iframe id="man-frame"' in html and 'aria-labelledby="man-title"' in html
    assert 'u.searchParams.set("embed", "1")' in html and 'closest("a[data-man]")' in html
    dlg = html[html.index('id="man-modal"'):html.index("</div>\n</div>", html.index('id="man-modal"'))]
    assert "/manual" not in dlg and 'href="/login"' in dlg
    for loc in LOCALES:
        cat = json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))
        assert cat.get("man.foot") and cat.get("nav.manual"), loc


@pytest.mark.parametrize("loc", LOCALES)
def test_an_embedded_chapter_is_the_text_alone(loc, c):  # noqa: F811
    shell = 'src="/api/v1/static/shell.js"'
    for cid in ("schedule", "glossary", "start", "faq"):
        r = c.get(f"/manual/{cid}?embed=1&lang={loc}", headers=HTML)
        assert r.status_code == 200, (cid, r.status_code)
        assert shell not in r.text and 'data-embed="1"' in r.text, cid
        for s in MA.chapter(loc, cid).sections:
            assert f'id="{s.anchor}"' in r.text, (cid, s.anchor)
        plain = c.get(f"/manual/{cid}?lang={loc}", headers=HTML).text
        assert shell in plain and 'data-embed="1"' not in plain, cid
    # the contents page too, since the bar's 操作手冊 opens the page dialog
    r = c.get(f"/manual?embed=1&lang={loc}", headers=HTML)
    assert shell not in r.text and 'data-embed="1"' in r.text


def test_the_embedded_page_hides_the_rest_and_keeps_its_links_inside():
    page = (STATIC / "manual.html").read_text("utf-8")
    assert "<!--fill:shell-->" in page and "<!--fill:embed-->" in page
    assert ".m-wrap[data-embed] .m-side, .m-wrap[data-embed] .m-pager, .m-wrap[data-embed] .m-crumb { display: none; }" in page
    js = (STATIC / "manual.js").read_text("utf-8")
    assert "wrap.dataset.embed" in js and 'u.searchParams.set("embed", "1")' in js and 'a.target = "_blank"' in js
