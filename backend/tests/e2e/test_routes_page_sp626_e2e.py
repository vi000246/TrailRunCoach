"""SP-626 on the demo athlete (segments only, no routes): the star is saved on the server and is
back after a reload (another device = another page load), the ★ filter, the pencil's inline
rename (no prompt()), a phone row stays two lines, and the English page.

The routes / 其他路段 split and a route's segment table need routes: covered by
backend/tests/test_routes_page_sp626.py and the review screenshots (synthetic routes).

Run: ./e2e.sh -k routes_page_sp626 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAGE = _server.PAGES["routes"]


def _first_row(page):
    row = page.locator("#list .row").first
    expect(row).to_be_visible(timeout=120_000)
    return row


def test_star_is_saved_on_the_server_and_pinned(app):
    page, _ = app
    page.add_init_script("localStorage.removeItem('routes.page')")
    open_page(page, PAGE)
    row = _first_row(page)
    last = page.locator("#list .row").last
    rid = last.get_attribute("data-id")
    last.locator("button.star").click()
    star = page.locator(f'#list .row[data-id="{rid}"] button.star')
    expect(star).to_have_attribute("aria-pressed", "true")
    # pinned on top, in the 最愛 section
    expect(page.locator("#list .row").first).to_have_attribute("data-id", rid)
    expect(page.locator("#list .sect").first).to_contain_text("最愛")
    assert rid in page.request.get("/api/v1/routes?fav=1").json()["rows"][0]["id"]
    # a fresh load (another device reads the same server file)
    page.reload()
    _first_row(page)
    expect(page.locator(f'#list .row[data-id="{rid}"] button.star')).to_have_attribute("aria-pressed", "true")
    # yellow
    color = page.locator(f'#list .row[data-id="{rid}"] button.star').evaluate("e => getComputedStyle(e).color")
    assert color != page.locator("#list .row").nth(1).locator("button.star").evaluate("e => getComputedStyle(e).color")
    # ★ filter
    page.click("#f-fav")
    expect(page.locator("#list .row")).to_have_count(1)
    page.click("#f-fav")
    page.locator(f'#list .row[data-id="{rid}"] button.star').click()
    expect(page.locator(f'#list .row[data-id="{rid}"] button.star')).to_have_attribute("aria-pressed", "false")


def test_pencil_renames_inline(app):
    page, _ = app
    open_page(page, PAGE)
    row = _first_row(page)
    rid = row.get_attribute("data-id")
    row.locator(".nm").click()
    expect(page.locator("#pen")).to_be_visible()
    page.on("dialog", lambda d: (_ for _ in ()).throw(AssertionError("a dialog opened")))
    page.click("#pen")
    page.fill("#rn-in", "我的測試坡")
    page.press("#rn-in", "Enter")
    expect(page.locator("#dname")).to_have_text("我的測試坡")
    expect(page.locator(f'#list .row[data-id="{rid}"] .nm')).to_contain_text("我的測試坡")
    # empty = back to the automatic name; Esc cancels
    page.click("#pen")
    page.fill("#rn-in", "")
    page.press("#rn-in", "Enter")
    expect(page.locator("#dname")).not_to_have_text("我的測試坡")
    page.click("#pen")
    page.press("#rn-in", "Escape")
    expect(page.locator("#pen")).to_be_visible()


def test_phone_row_is_two_lines(app):
    page, _ = app
    page.set_viewport_size({"width": 390, "height": 844})
    open_page(page, PAGE)
    _first_row(page)
    hs = page.evaluate("""() => [...document.querySelectorAll('#list .row')].map((r) => {
        const nm = r.querySelector('.nm'), sub = r.querySelector('.sub');
        const lh = (e) => parseFloat(getComputedStyle(e).lineHeight) || 20;
        return [nm.getBoundingClientRect().height / lh(nm), sub.getBoundingClientRect().height / lh(sub)]; })""")
    assert hs and all(a < 1.6 and b < 1.6 for a, b in hs), hs


def test_english_page(app):
    page, _ = app
    open_page(page, PAGE + "?lang=en")
    _first_row(page)
    expect(page.locator("h1")).to_have_text("Route progress")
    expect(page.locator("#f-fav")).to_have_text(re.compile("Favourites"))
    cjk = re.compile(r"[㐀-鿿]")
    texts = page.locator(".filters button, #count, .sect, #list .sub").all_inner_texts()
    assert not [t for t in texts if cjk.search(t)], texts
