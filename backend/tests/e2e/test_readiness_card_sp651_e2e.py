"""SP-651 in a real browser on the demo athlete (its 50K's 專項期): the 賽前完備程度 card on top of the
專項期 and 減量期 pages — 本週要做 with its 安排課表 link, three gates, the week calendar; a gate filters the
calendar to its stage, a cell shows its week; at 390 px the calendar becomes week buttons and the page
does not scroll sideways; English. SP-677: two gates and weeks 10 → 3 on the 專項期 card, a phone lists this
week only; the 減量期 page has the countdown and four check cards instead.

The API side: backend/tests/test_readiness_card_sp651.py. Run: ./e2e.sh -k sp651 (see conftest.py)."""
from __future__ import annotations

import urllib.parse

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.e2e

VIEW = urllib.parse.quote("周期化訓練")
BUILD = f"/api/v1/app/viewer?view={VIEW}&dash=2"
TAPER = f"/api/v1/app/viewer?view={VIEW}&dash=3"


def _ctx(e2e_browser, e2e_server, width=1400, locale="zh-TW"):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": width, "height": 900},
                                  locale=locale, timezone_id="Asia/Taipei",
                                  extra_http_headers={"Accept-Language": locale})
    ctx.set_default_timeout(60_000)
    return ctx


def _open(page, url=BUILD):
    page.goto(url)
    page.wait_for_selector(".rd .rd-rail .rd-gate")
    return page.locator(".rd").first


def test_card_gates_filter_and_cell_detail(app):
    page, _ = app
    card = _open(page)
    # the card is the first chart of the page
    first = page.locator(".card").first
    expect(first.locator(".rd")).to_have_count(1)
    expect(card.locator(".rd-gate")).to_have_count(2)
    expect(card.locator("table.rd-cal thead tr.rd-wk th.w")).to_have_count(8)
    here = card.locator(".rd-here")
    if here.count():
        assert "schedule/page?day=" in here.locator("a.rd-sched").get_attribute("href")
    # a gate: only that stage in the calendar
    card.locator(".rd-gate").nth(0).click()
    expect(card.locator(".rd-gate.sel")).to_have_count(1)
    assert card.locator("tbody tr.dim").count() > 0
    assert card.locator('tbody tr[data-stage="volume"].dim').count() == 0
    card.locator(".rd-gate").nth(0).click()
    expect(card.locator("tbody tr.dim")).to_have_count(0)
    # a cell: its week's target and actual
    card.locator("table.rd-cal .rd-c").first.click()
    expect(card.locator(".rd-detail b")).to_have_count(1)
    text = card.inner_text()
    for w in ("Koop", "Uphill", "研討會"):
        assert w not in text
    assert "dash=3" in card.locator(".rd-foot a.rd-go").get_attribute("href")       # → the 減量期 card
    # the 減量期 page: the countdown and four check cards on top, no calendar
    page.goto(TAPER)
    page.wait_for_selector(".rd .rd-chk")
    first = page.locator(".card").first
    expect(first.locator(".rd-chk")).to_have_count(4)
    expect(first.locator(".rd-days span")).to_have_count(11)
    expect(first.locator("table.rd-cal")).to_have_count(0)


def test_phone_this_week_only_without_sideways_scroll(e2e_browser, e2e_server):
    ctx = _ctx(e2e_browser, e2e_server, width=390)
    page = ctx.new_page()
    try:
        card = _open(page)
        expect(card.locator("table.rd-cal")).to_be_hidden()
        expect(card.locator(".rd-mlist")).to_be_visible()
        assert card.locator(".rd-mlist .mr").count() > 0
        wide = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert wide <= 1, wide
        page.goto(TAPER)
        page.wait_for_selector(".rd .rd-chk")
        wide = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert wide <= 1, wide
    finally:
        ctx.close()


def test_english(e2e_browser, e2e_server):
    ctx = _ctx(e2e_browser, e2e_server, locale="en")
    page = ctx.new_page()
    try:
        card = _open(page, BUILD + "&lang=en")
        name = card.locator(".rd-head b").inner_text()          # the race's own name is the user's
        text = card.inner_text().replace(name, "")
        assert "Specific volume" in text or "Weeks to race" in text, text[:400]
        assert not any("一" <= ch <= "鿿" for ch in text), [ch for ch in text if "一" <= ch <= "鿿"][:20]
    finally:
        ctx.close()
