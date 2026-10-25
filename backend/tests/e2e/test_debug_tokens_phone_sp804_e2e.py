"""SP-804: 設定 › 進階設定 › Debug API — on a phone (390 px) the token table's 撤銷 button was cut off at the
right edge (five columns, the 最後使用 cell four lines high, did not fit). Each token is a card there now:
the name with 撤銷 beside it, then 範圍／到期／最後使用 each with its name; nothing wider than the screen.
On a desktop it stays the five-column table. Real Chromium on the demo athlete, the token list mocked
(synthetic tokens; the demo has none).

Run: ./e2e.sh -k sp804 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _browser, _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

STATE = {
    "enabled": True,
    "tokens": [
        {"id": 1, "name": "claude", "prefix": "trcd_uTj3", "scopes": ["read:plan", "write:config"],
         "never_expires": False, "expires_at": "2026-10-27T00:00:00Z", "last_used_at": "2026-10-20T09:57:44Z",
         "last_ip": "127.0.0.1", "new_ip_at": "2026-10-20T09:57:44Z", "active": True, "revoked_at": None,
         "expired": False},
        {"id": 2, "name": "my-laptop-agent", "prefix": "trcd_9xQa",
         "scopes": ["read:activities", "read:plan", "read:thresholds"], "never_expires": True, "expires_at": None,
         "last_used_at": "2026-10-22T03:12:05Z", "last_ip": "203.0.113.45", "new_ip_at": None, "active": True,
         "revoked_at": None, "expired": False},
        {"id": 3, "name": "old", "prefix": "trcd_aa11", "scopes": ["read:plan"], "never_expires": False,
         "expires_at": "2026-10-01T00:00:00Z", "last_used_at": None, "last_ip": None, "new_ip_at": None,
         "active": False, "revoked_at": "2026-10-02T00:00:00Z", "expired": False},
    ],
    "writes": [], "audit": [], "failures": [], "dropped": {},
}


def _open(e2e_browser, e2e_server, viewport: dict, mobile: bool, locale: str = "zh-TW"):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport=viewport, is_mobile=mobile, has_touch=mobile,
                                  locale=locale, timezone_id="Asia/Taipei")
    _browser.blank_tiles(ctx, e2e_server.url)
    ctx.route("**/api/v1/settings/debug-api", lambda route: route.fulfill(json=STATE))
    page = ctx.new_page()
    open_page(page, _server.PAGES["settings"] + ("?lang=en" if locale == "en" else "?lang=zh-TW"))
    page.click("#tab-advanced")
    page.wait_for_selector("#dbg-tokens tr.tok", timeout=60_000)
    page.locator("#debugapi").scroll_into_view_if_needed()
    return ctx, page


@pytest.mark.parametrize("locale", ["zh-TW", "en"])
def test_phone_every_revoke_button_is_on_screen_beside_its_name(e2e_browser, e2e_server, locale):
    ctx, page = _open(e2e_browser, e2e_server, {"width": 390, "height": 844}, True, locale)
    try:
        w = page.evaluate("document.documentElement.clientWidth")
        sec = page.locator("#debugapi").bounding_box()
        btns = page.locator("#dbg-tokens [data-dbg-revoke]")
        assert btns.count() == 2                                    # the two active tokens
        for i in range(btns.count()):
            b = btns.nth(i).bounding_box()
            name = page.locator("#dbg-tokens tr.tok").nth(i).locator("td").first.bounding_box()
            assert b["x"] >= 0 and b["x"] + b["width"] <= min(w, sec["x"] + sec["width"]), (b, w, sec)
            # on the name's line, to its right
            assert b["y"] < name["y"] + name["height"] and name["y"] < b["y"] + b["height"], (b, name)
            assert b["x"] >= name["x"] + name["width"] - 1, (b, name)
        # nothing in the table wider than the screen (no sideways scroll), the header row hidden,
        # each detail cell says what it is
        assert page.evaluate("document.getElementById('dbg-tokens').closest('table').scrollWidth") <= w
        assert page.evaluate("document.documentElement.scrollWidth") <= w
        assert not page.locator("#dbg-tokens tr.hd").is_visible()
        labels = page.evaluate("""[...document.querySelectorAll('#dbg-tokens tr.tok td[data-l]')]
            .map((td) => getComputedStyle(td, '::before').content)""")
        assert len(labels) == 9 and all(c not in ("none", "normal", '""') for c in labels), labels
        # the revoke still works from there: the confirm, then DELETE of that token
        seen = []
        page.route("**/api/v1/settings/debug-api/tokens/*", lambda route: (seen.append(route.request.method),
                                                                          route.fulfill(json=STATE)))
        page.once("dialog", lambda d: d.accept())
        btns.first.click()
        page.wait_for_timeout(300)
        assert seen and seen[0] == "DELETE", seen
    finally:
        ctx.close()


def test_desktop_stays_a_five_column_table(e2e_browser, e2e_server):
    ctx, page = _open(e2e_browser, e2e_server, {"width": 1400, "height": 900}, False)
    try:
        assert page.locator("#dbg-tokens tr.hd").is_visible()
        assert page.evaluate("getComputedStyle(document.querySelector('#dbg-tokens tr.tok')).display") == "table-row"
        assert page.locator("#dbg-tokens tr.hd th").count() == 5
        b = page.locator("#dbg-tokens [data-dbg-revoke]").first.bounding_box()
        used = page.locator("#dbg-tokens tr.tok").first.locator("td").nth(3).bounding_box()
        assert b["x"] >= used["x"] + used["width"] - 1                 # the last column, after 最後使用
    finally:
        ctx.close()
