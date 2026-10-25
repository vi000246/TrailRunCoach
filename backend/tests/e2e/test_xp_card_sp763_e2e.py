"""The 疊石 Cairn card (SP-763, static/xp_card.js) in a real browser on the demo athlete (owner mode).

賽事管理 › 新增賽事: no card for a 1-day race; 2 days or more → the card under 「從 Cairn 匯入」, its
button opening Cairn in a new tab (from=trc). 「不用了」 hides it, and it stays hidden when the editor
opens again (kept on the server); 設定 › 提示多寡 shows it again. At phone width nothing scrolls sideways.

Run: ./e2e.sh -k xp_card (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e


def _reset(page):
    page.evaluate("""() => fetch("/api/v1/app/xp/cairn", { method: "PUT", headers: { "Content-Type": "application/json" },
                                                         body: JSON.stringify({ off: false }) })""")


def _add(page, days: int):
    page.locator("#add").click()
    expect(page.locator("#ed")).to_be_visible()
    page.fill("#ed-days", str(days))
    page.dispatch_event("#ed-days", "input")


def test_the_card_only_for_multi_day_and_no_thanks_is_remembered(app):
    page, _ = app
    open_page(page, _server.PAGES["races"])
    _reset(page)
    page.reload(wait_until="load")
    card = page.locator("#xp-cairn-ed")
    _add(page, 1)
    expect(card).to_be_hidden()
    page.fill("#ed-days", "3")
    page.dispatch_event("#ed-days", "input")
    expect(card).to_be_visible()
    expect(card).to_contain_text("把這趟排進疊石 Cairn")
    go = card.locator("a.xp-go")
    expect(go).to_have_text("打開疊石 Cairn ↗")
    assert go.get_attribute("target") == "_blank" and go.get_attribute("href").endswith("/?from=trc")
    # 「不用了」: gone, and still gone when the editor opens again after a reload
    with page.expect_response("**/api/v1/app/xp/cairn/dismiss"):
        card.locator("button.xp-no").click()
    expect(card).to_be_hidden()
    page.reload(wait_until="load")
    _add(page, 3)
    expect(card).to_be_hidden()
    # 設定 › 提示多寡: 「重新顯示」
    open_page(page, _server.PAGES["settings"])
    row = page.locator("#xp-cairn-row")
    expect(row).to_be_visible()
    expect(row).to_contain_text("已關閉")
    row.locator("#xp-cairn-btn").click()
    expect(row).to_contain_text("顯示中")
    open_page(page, _server.PAGES["races"])
    _add(page, 2)
    expect(card).to_be_visible()


def test_phone_width_and_english(app):
    page, _ = app
    page.set_viewport_size({"width": 390, "height": 844})
    open_page(page, _server.PAGES["races"] + "?lang=en")
    _reset(page)
    page.reload(wait_until="load")
    _add(page, 3)
    card = page.locator("#xp-cairn-ed")
    expect(card).to_contain_text("Plan this trip in Cairn")
    expect(card.locator("a.xp-go")).to_have_text("Open Cairn ↗")
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], w
