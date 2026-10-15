"""課表偏好 in tabs (SP-540), in a real browser on the demo athlete: one panel at a time, the
last tab opened comes back next time (localStorage, and still works when storage throws),
an error opens the tab that has it, and at phone width every tab is on screen.

Run: ./e2e.sh -k sp540 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _browser, _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

TABS = ["days", "content", "strength", "races", "auto"]


def _open(page):
    page.click("#pf-btn")
    expect(page.locator("#pf")).to_have_attribute("open", "")


def _ready(page, lang=None):
    open_page(page, _server.PAGES["schedule"] + (f"?lang={lang}" if lang else ""))
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _shown(page) -> list[str]:
    return [t for t in TABS if page.locator(f"#pf-p-{t}").is_visible()]


def test_tabs_show_one_panel_and_remember_the_last(app):
    page, _ = app
    _ready(page)
    _open(page)
    expect(page.locator("#pf-tab-days")).to_have_attribute("aria-selected", "true")   # first time: 日期與時間
    assert _shown(page) == ["days"]
    expect(page.locator("#pf-days")).to_be_visible()
    page.click("#pf-tab-strength")
    assert _shown(page) == ["strength"]
    expect(page.locator("#pf-st")).to_be_visible()
    expect(page.locator("#pf-mv select[data-mv]").first).to_be_visible()
    # ← → between the tabs
    page.locator("#pf-tab-strength").focus()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#pf-tab-races")).to_be_focused()
    assert _shown(page) == ["races"]
    expect(page.locator("#pf-tpd")).to_be_visible()
    page.locator("#pf [data-close]").first.click()
    assert page.evaluate("localStorage.getItem('schedule.pf.tab')") == '"races"'
    _open(page)                                                                        # comes back on 賽事與測試
    expect(page.locator("#pf-tab-races")).to_have_attribute("aria-selected", "true")
    assert _shown(page) == ["races"]
    page.locator("#pf [data-close]").first.click()
    _ready(page)                                                                       # and after a reload
    _open(page)
    expect(page.locator("#pf-tab-races")).to_have_attribute("aria-selected", "true")


def test_an_error_opens_its_tab(app):
    page, _ = app
    _ready(page)
    _open(page)
    page.click("#pf-tab-days")
    page.fill("#pf-capw", "10")                                                        # 平日單次上限 < 20
    page.click("#pf-tab-auto")
    expect(page.locator("#pf-tab-days")).to_have_class(re.compile(r"\bhas-err\b"))
    expect(page.locator("#pf-err")).to_be_visible()
    page.click("#pf-save")
    expect(page.locator("#pf-tab-days")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#pf-err")).to_contain_text("20–300")
    expect(page.locator("#pf")).to_have_attribute("open", "")                          # nothing saved
    page.locator("#pf [data-close]").first.click()


def test_storage_blocked_still_opens(e2e_browser, e2e_server):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 1400, "height": 900}, locale="zh-TW",
                                  timezone_id="Asia/Taipei")
    try:
        _browser.blank_tiles(ctx, e2e_server.url)
        page = ctx.new_page()
        page.add_init_script("Storage.prototype.setItem = function () { throw new Error('blocked'); };"
                             "Storage.prototype.getItem = function () { throw new Error('blocked'); };")
        _ready(page)
        _open(page)
        expect(page.locator("#pf-tab-days")).to_have_attribute("aria-selected", "true")
        page.click("#pf-tab-content")
        assert _shown(page) == ["content"]
    finally:
        ctx.close()


@pytest.mark.parametrize("locale", ["zh-TW", "en"])
def test_phone_width_every_tab_on_screen(e2e_browser, e2e_server, locale):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, is_mobile=True,
                                  has_touch=True, locale=locale, timezone_id="Asia/Taipei")
    try:
        _browser.blank_tiles(ctx, e2e_server.url)
        page = ctx.new_page()
        _ready(page, "en" if locale == "en" else None)
        page.evaluate("openPrefs()")
        expect(page.locator("#pf")).to_have_attribute("open", "")
        w = page.evaluate("innerWidth")
        for t in TABS:
            box = page.locator(f"#pf-tab-{t}").bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= w, (t, box, w)
        assert page.evaluate("PFF.scrollWidth <= PFF.clientWidth + 1")                  # no sideways scroll
        for t in TABS:
            page.click(f"#pf-tab-{t}")
            assert _shown(page) == [t]
            assert page.evaluate("PFF.scrollWidth <= PFF.clientWidth + 1"), t
    finally:
        ctx.close()
