"""The 介紹頁 keeps its reader: every page it links to (操作手冊、關於、方案與價格、法律文件) opens over it, framed
with ?embed=1, instead of taking the reader away; only the sign-in leaves. A whole page covers the screen
(.modal.full); a method's words stay a dialog in the middle.

- each linked page has an ?embed=1 form without its own top bar and footer (the shell, or its own header)
- the page without ?embed=1 is unchanged
Synthetic data only."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, app, c, env, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
LOCALES = ("zh-TW", "en")
SHELL = 'src="/api/v1/static/shell.js"'


def _intro() -> str:
    return (STATIC / "intro.html").read_text("utf-8")


def test_every_link_to_another_page_opens_the_page_dialog():
    html = _intro()
    body = html[html.index("<body>"):html.index('<div class="modal')]
    for a in re.findall(r'<a [^>]*href="/[^"]*"[^>]*>', body):
        href = re.search(r'href="([^"]*)"', a).group(1)
        if href == "/" or href.startswith("/login") or "data-try" in a:       # this page, the sign-in, the example
            continue
        assert " data-man" in a or " data-m=" in a, a
    for href in ("/manual", "/about", "/pricing", "/legal/terms", "/legal/privacy", "/legal/health",
                 "/legal/third_party"):
        assert re.search(rf'<a [^>]*href="{href}" data-man', body), href
    # whole pages cover the screen, a method's words stay a dialog
    assert '<div class="modal full" id="man-modal"' in html and '<div class="modal full" id="ex-modal"' in html
    assert '<div class="modal" id="m-modal"' in html
    assert ".modal.full .dlg { width: 100%; height: 100%;" in html
    # inside the frame: another of these pages stays framed, the sign-in leaves, the rest a new tab
    assert 'doc.addEventListener("click", frameClick, true)' in html
    assert "LEAVE.test(u.pathname)) location.assign" in html and 'window.open(u.href, "_blank", "noopener")' in html


@pytest.mark.parametrize("loc", LOCALES)
def test_the_embedded_pages_have_no_bar_or_footer(loc, c):  # noqa: F811
    r = c.get(f"/about?embed=1&lang={loc}", headers=HTML)
    assert r.status_code == 200 and SHELL not in r.text and 'data-embed="1"' in r.text
    plain = c.get(f"/about?lang={loc}", headers=HTML).text
    assert SHELL in plain and 'data-embed="1"' not in plain and "<!--fill:" not in plain
    r = c.get(f"/pricing?embed=1&lang={loc}", headers=HTML)
    assert r.status_code == 200 and '<body data-embed="1">' in r.text
    plain = c.get(f"/pricing?lang={loc}", headers=HTML).text
    assert "<body>" in plain and "<!--fill:" not in plain


def test_the_legal_documents_embed_too(c):  # noqa: F811
    r = c.get("/legal/terms?embed=1", headers=HTML)
    if r.status_code == 404:                       # the owner's app has no /legal
        pytest.skip("no /legal in this mode")
    assert r.status_code == 200 and ' data-embed="1">' in r.text
    plain = c.get("/legal/terms", headers=HTML).text
    assert "data-embed" not in plain.split("<body", 1)[1].split(">", 1)[0] and "<!--fill:" not in plain
    page = (STATIC / "legal.html").read_text("utf-8")
    assert "body[data-embed] .top, body[data-embed] footer { display: none; }" in page
