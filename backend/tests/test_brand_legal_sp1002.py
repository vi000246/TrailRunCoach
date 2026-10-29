"""SP-1002: the legal pages' frame and the about page's trademark notice name the product Trig
(SP-971) — 「Trig 基石」 in Chinese, 「Trig」 in English — through the one name key.

The four documents themselves (backend/legal/*.md) are not touched here: a changed text needs a new
terms.VERSION and every user's consent again (terms.py, test_terms_digests_are_pinned_to_the_version),
and that is the owner's call (asked on SP-1002). Synthetic only: no WKO5 folder, no ~/.wko5coach."""
from __future__ import annotations

import re

from backend.accounts import terms
from backend.i18n import pages as P
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, _clean_env, _client, app, c, env, started)

LOGO = '<img src="/api/v1/static/icons/trig-logo-small.svg" alt=""'
OLD = "TrailRunCoach"


def _head(text: str) -> str:
    return text[text.index('<div class="top">'):text.index('<div class="draft"')]


def test_the_legal_pages_header_shows_the_badge_and_the_name(c):
    for path in ("/legal", "/legal/terms", "/legal/privacy"):
        for loc, name, home in (("zh-TW", "Trig 基石", "回到 Trig 基石"), ("en", "Trig", "Back to Trig")):
            r = c.get(f"{path}?lang={loc}", headers=HTML)
            assert r.status_code == 200, (path, loc, r.status_code)
            head = _head(r.text)
            assert LOGO in head and f'<span data-i18n="shell.brand">{name}</span>' in head, (path, loc)
            assert f'<a href="/" data-i18n="legal.home">{home}</a>' in head, (path, loc)
            assert "<svg" not in head and OLD not in head, (path, loc)        # the old calendar square is gone
            title = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S).group(1)
            assert title.endswith(" · " + name) and OLD not in title, (path, loc, title)


def test_the_about_pages_trademark_notice_names_trig():
    zh = P.page_catalog(("about",), "zh-TW")["about.trademarks.others"]
    en = P.page_catalog(("about",), "en")["about.trademarks.others"]
    assert "Trig 基石與上述公司沒有任何關係" in zh and OLD not in zh
    assert "Trig is not affiliated with" in en and OLD not in en


def test_the_documents_and_their_version_are_left_as_they_were():
    # renaming inside the texts waits for the owner (a new VERSION asks everyone to accept again)
    assert terms.VERSION == "2026-10-15-draft"
    assert terms.digests() == terms.PINNED[terms.VERSION]
