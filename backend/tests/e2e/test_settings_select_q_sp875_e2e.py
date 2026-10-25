"""SP-875: on a phone (390 px) the settings page's 課表心率區間 / 課表功率區間 selects keep their ? on the
same line (it wrapped onto a line of its own after SP-750); on a desktop the ? still sits right after the
select. Real Chromium on the demo athlete.

Run: ./e2e.sh -k sp875 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _browser, _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SELECTS = ["hr-model", "pz-model"]


def _boxes(page, sid: str) -> tuple[dict, dict]:
    sel = page.locator(f"#{sid}")
    q = page.locator(f"#{sid} >> xpath=ancestor::label[1]/following-sibling::button[contains(@class,'q')][1]")
    return sel.bounding_box(), q.bounding_box()


def _open(e2e_browser, e2e_server, viewport: dict, locale: str, mobile: bool):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport=viewport, is_mobile=mobile, has_touch=mobile,
                                  locale=locale, timezone_id="Asia/Taipei")
    _browser.blank_tiles(ctx, e2e_server.url)
    page = ctx.new_page()
    open_page(page, _server.PAGES["settings"] + ("?lang=en" if locale == "en" else "?lang=zh-TW"))
    page.click("#tab-body")
    for sid in SELECTS:
        page.wait_for_function(f"document.getElementById('{sid}').options.length > 0", timeout=60_000)
    return ctx, page


@pytest.mark.parametrize("locale", ["zh-TW", "en"])
def test_phone_width_select_and_its_q_on_one_line(e2e_browser, e2e_server, locale):
    ctx, page = _open(e2e_browser, e2e_server, {"width": 390, "height": 844}, locale, True)
    try:
        w = page.evaluate("innerWidth")
        for sid in SELECTS:
            s, q = _boxes(page, sid)
            assert s and q, sid
            # the same line: the ? is vertically inside the select's box, to its right, on screen
            assert s["y"] <= q["y"] + q["height"] / 2 <= s["y"] + s["height"], (sid, s, q)
            assert q["x"] >= s["x"] + s["width"] and q["x"] + q["width"] <= w, (sid, s, q, w)
    finally:
        ctx.close()


def test_desktop_q_right_after_the_select(e2e_browser, e2e_server):
    ctx, page = _open(e2e_browser, e2e_server, {"width": 1400, "height": 900}, "zh-TW", False)
    try:
        for sid in SELECTS:
            s, q = _boxes(page, sid)
            assert s["y"] <= q["y"] + q["height"] / 2 <= s["y"] + s["height"], (sid, s, q)
            assert 0 <= q["x"] - (s["x"] + s["width"]) <= 20, (sid, s, q)          # not pushed to the row's end
    finally:
        ctx.close()
