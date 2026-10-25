"""SP-846: 方案與價格 in a real browser — at phone and desktop width, light and dark, both languages: no
sideways scroll, the app's colours, the 月繳／年繳 switch changes the subscription's price, the whole
comparison is folded on a phone and open on a desktop, and opens with a tap. The e2e server runs without
sign-in (no /pricing route there), so the page is opened by its file name — the same render, as a signed-out
visitor while the subscription isn't on sale (「即將開放」).

Run: ./e2e.sh -k sp846 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/pricing.html"
SIZES = {"mobile": (390, 844), "desktop": (1400, 900)}


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", ["mobile", "desktop"])
@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_pricing_fits_and_switches_the_price(app, loc, size, scheme):
    page, _watch = app
    w, h = SIZES[size]
    page.set_viewport_size({"width": w, "height": h})
    page.emulate_media(color_scheme=scheme)
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    page.locator("#paid-h").wait_for(state="visible")
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= w, f"sideways scroll: {sw} > {w}"
    bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    assert (bg == "rgb(17, 17, 16)") == (scheme == "dark"), bg
    assert page.evaluate("() => document.documentElement.lang") == loc
    # yearly first; the switch shows the monthly price and back
    year, month = page.locator('[data-iv-show="year"]'), page.locator('[data-iv-show="month"]')
    assert year.is_visible() and not month.is_visible() and "890" in year.inner_text()
    page.locator("#seg-month").click()
    assert month.is_visible() and not year.is_visible() and "99" in month.inner_text()
    assert page.locator("#seg-month").get_attribute("aria-pressed") == "true"
    page.locator("#seg-year").click()
    assert year.is_visible()
    # not on sale: the subscription's button is there and does nothing
    soon = page.locator("#pr-soon")
    assert soon.is_visible() and soon.is_disabled()
    # the whole comparison: folded on a phone, open on a desktop; a tap opens it
    det = page.locator("#pr-all")
    assert det.evaluate("(el) => el.open") is (size == "desktop")
    if size == "mobile":
        det.locator("summary").click()
        assert det.evaluate("(el) => el.open") is True
    assert page.locator("#pr-all tbody tr").count() >= 11
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= w, f"sideways scroll with the table open: {sw} > {w}"
