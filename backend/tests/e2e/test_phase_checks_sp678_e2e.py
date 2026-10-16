"""SP-678 in a real browser on the demo athlete: the 基礎期 page opens with the 基礎檢查 card (three check cards,
the intensity donut and 8 stacked weeks, the weekly-increase line) and the 強度課表解鎖條件 card (two tiles; a
click switches the zone; 「什麼時候會重新上鎖」 folded until clicked); the 轉換期 page has the 恢復檢查 card
(here without a recent A race: its explanation); at 390 px nothing scrolls sideways.

The API side: backend/tests/test_phase_checks_sp678.py. Run: ./e2e.sh -k sp678."""
from __future__ import annotations

import urllib.parse

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.e2e

VIEW = urllib.parse.quote("周期化訓練")
TRANSITION = f"/api/v1/app/viewer?view={VIEW}&dash=0"
BASE = f"/api/v1/app/viewer?view={VIEW}&dash=1"
SOURCE_WORDS = ("Koop", "Uphill", "Friel", "Seiler", "推估", "研討會")


def _base(page):
    page.goto(BASE)
    page.wait_for_selector(".rd .rd-chk")
    page.wait_for_selector(".rd .ru-tile")
    return page.locator(".card").nth(0), page.locator(".card").filter(has=page.locator(".ru-tile")).first


def test_base_cards_and_the_zone_tiles(app):
    page, _ = app
    card, un = _base(page)
    expect(card.locator(".rd-chk")).to_have_count(3)
    expect(card.locator(".rd-sb > div")).to_have_count(8)
    expect(card.locator("svg.rd-rf")).to_have_count(1)
    expect(un.locator(".ru-tile")).to_have_count(2)
    sel = un.locator(".ru-tile.sel").get_attribute("data-z")
    other = "3" if sel == "5" else "5"
    un.locator(f'.ru-tile[data-z="{other}"]').click()
    expect(un.locator(".ru-tile.sel")).to_have_attribute("data-z", other)
    un.locator('.ru-tile[data-z="5"]').click()
    det = un.locator("details.ru-re")
    expect(det).to_have_count(1)
    assert det.get_attribute("open") is None                     # folded by default
    det.locator("summary").click()
    assert det.get_attribute("open") is not None
    text = card.inner_text() + un.inner_text()
    assert not any(w in text for w in SOURCE_WORDS)
    page.goto(TRANSITION)
    page.wait_for_selector(".rd")
    expect(page.locator(".card").first.locator(".rd")).to_have_count(1)


def test_phone_no_sideways_scroll(e2e_browser, e2e_server):
    for url in (BASE, TRANSITION):
        ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 900},
                                      locale="zh-TW", timezone_id="Asia/Taipei")
        ctx.set_default_timeout(60_000)
        page = ctx.new_page()
        try:
            page.goto(url)
            page.wait_for_selector(".rd")
            wide = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert wide <= 0, (url, wide)
        finally:
            ctx.close()


def test_english(e2e_browser, e2e_server):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 1400, "height": 900}, locale="en",
                                  timezone_id="Asia/Taipei", extra_http_headers={"Accept-Language": "en"})
    ctx.set_default_timeout(60_000)
    page = ctx.new_page()
    try:
        page.goto(BASE + "&lang=en")
        page.wait_for_selector(".rd .ru-tile")
        text = page.locator(".rd").first.inner_text() + page.locator(".ru-tiles").inner_text()
        assert "Zone 3" in page.locator(".ru-tiles").inner_text()
        assert not any("一" <= ch <= "鿿" for ch in text), text[:300]
    finally:
        ctx.close()


def test_overview_has_the_same_unlock_card(app):
    """The 總覽's 3 區／5 區 card is the 強度課表解鎖條件 card too (no z5flow): two tiles, re-locking folded."""
    page, _ = app
    page.goto("/api/v1/overview/page")
    page.wait_for_selector("#z5 .ru-tile")
    z = page.locator("#z5")
    expect(z.locator(".ru-tile")).to_have_count(2)
    expect(z.locator(".zf, .zf-st")).to_have_count(0)
    z.locator('.ru-tile[data-z="5"]').click()
    expect(z.locator(".ru-tile.sel")).to_have_attribute("data-z", "5")
    det = z.locator("details.ru-re")
    assert det.get_attribute("open") is None
    det.locator("summary").click()
    assert det.get_attribute("open") is not None
    assert not any(w in z.inner_text() for w in SOURCE_WORDS)


def test_overview_phone_no_sideways_scroll(e2e_browser, e2e_server):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 900},
                                  locale="zh-TW", timezone_id="Asia/Taipei")
    ctx.set_default_timeout(60_000)
    page = ctx.new_page()
    try:
        page.goto("/api/v1/overview/page")
        page.wait_for_selector("#z5 .ru-tile")
        page.locator('#z5 .ru-tile[data-z="5"]').click()
        page.locator("#z5 details.ru-re summary").click()
        wide = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert wide <= 0, wide
    finally:
        ctx.close()
