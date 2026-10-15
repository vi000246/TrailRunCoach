"""SP-622: the 介紹頁 in a real browser — at phone and desktop width, light and dark, both languages: no
sideways scroll, the app's colours (dark background in dark mode), this language's and scheme's
screenshots loaded, the FAQ opens. The e2e server is the demo instance (its "/" is /demo), so the page is
opened by its file name (/api/v1/static/intro.html: the same page render, without the server's
「先試試看」 choice — the example link it carries by default).

Run: ./e2e.sh -k sp622 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/intro.html"
SIZES = {"mobile": (390, 844), "desktop": (1400, 900)}


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", ["mobile", "desktop"])
@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_intro_fits_and_follows_the_colour_scheme(app, loc, size, scheme):
    page, _watch = app
    w, h = SIZES[size]
    page.set_viewport_size({"width": w, "height": h})
    page.emulate_media(color_scheme=scheme)
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    page.locator("#hero-h").wait_for(state="visible")
    # every screenshot: scrolled into view (lazy), loaded, this language's and scheme's file
    for img in page.locator("img[data-shot]").all():
        img.scroll_into_view_if_needed()
        page.wait_for_function("(el) => el.complete && el.naturalWidth > 0", arg=img.element_handle())
        src = page.evaluate("(el) => el.currentSrc", img.element_handle())
        assert f".{loc}." in src and (src.endswith(".dark.webp") == (scheme == "dark")), src
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= w, f"sideways scroll: {sw} > {w}"
    bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    dark_bg = bg in ("rgb(17, 17, 16)",)
    assert dark_bg == (scheme == "dark"), bg
    assert page.evaluate("() => document.documentElement.lang") == loc
    det = page.locator("#faq details").first
    det.locator("summary").click()
    assert det.evaluate("(el) => el.open") is True
    assert det.locator("a.more").get_attribute("href").startswith("/manual/")
    assert page.locator("[data-try]").first.get_attribute("href") == "/share/example-trail-50k"
