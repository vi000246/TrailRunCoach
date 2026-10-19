"""SP-698: a share link opened by someone who isn't signed in showed 「join.title」 and 「join.signup」
instead of the card's title and text. Production sits behind Cloudflare Access, which lets only
/share/* through: the page's i18n.js (/api/v1/static/i18n/i18n.js) answered with Access's login
redirect, so window.I18N never existed and the page's JT() fell back to the bare keys. The share page
now carries i18n.js inline and loads nothing else from /api/v1/static/."""
from __future__ import annotations

import re

import pytest

from backend.i18n import pages as P


def _scripts(html: str) -> list[str]:
    """The script tags' src (an inline script's text left out: i18n.js's own comment names its address)."""
    return re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", re.sub(r"<script>.*?</script>", "", html, flags=re.S))


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_the_share_page_loads_nothing_from_behind_the_gate(loc):
    html = P.render("share", loc, debug=False)
    srcs = _scripts(html)
    assert not [s for s in srcs if not s.startswith("https://")], srcs      # only the CDN (ECharts)
    assert "/api/v1/static/" not in re.sub(r"<script>.*?</script>", "", html, flags=re.S)
    # i18n.js itself is in the page, right after the catalog, before the page's own scripts
    js = P.I18N_JS_FILE.read_text("utf-8")
    assert "window.I18N = {" in js and "window.I18N = {" in html
    assert html.index("window.__I18N__ = ") < html.index("window.I18N = {") < html.index("const JT = ")


@pytest.mark.parametrize("loc,title,signup", [
    ("zh-TW", "想算自己的比賽？", "免費註冊"),
    ("en", "Plan your own race?", "Sign up free"),
])
def test_the_join_card_words_are_in_the_pages_catalog(loc, title, signup):
    html = P.render("share", loc, debug=False)
    boot = re.search(r"window\.__I18N__ = (\{.*?\});</script>", html, re.S).group(1)
    assert f'"share.join.title": "{title}"' in boot
    assert f'"share.join.signup": "{signup}' in boot


def test_the_other_pages_still_load_i18n_js_by_its_address():
    html = P.render("overview", "zh-TW", debug=False)
    assert P.I18N_JS in _scripts(html)
    assert "window.I18N = {" not in html


def test_a_changed_i18n_js_reaches_the_share_page(tmp_path, monkeypatch):
    f = tmp_path / "i18n.js"
    f.write_text("window.I18N = {probe: 1}; // </script> inside", "utf-8")
    monkeypatch.setattr(P, "I18N_JS_FILE", f)
    P._CACHE.clear()
    html = P.render("share", "zh-TW", debug=False)
    assert "window.I18N = {probe: 1}; // <\\/script> inside" in html      # cannot close the tag early
    P._CACHE.clear()
