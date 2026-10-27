"""設定 → 生理數據 「我的區間」 (SP-662) in a real browser on the demo athlete: the numbers in use as tiles, the
課表心率區間 and 課表功率區間 each as a coloured bar and one tinted row per zone (range and %), in view — not in a
folded menu — and the app-vs-watch check as one sentence while every gap is within 5 bpm.

Run: ./e2e.sh -k sp662 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

CMP = {"alert_bpm": 5, "read_day": "2026-10-13", "alert": False,
       "rows": [{"id": "lthr", "name": "LTHR", "app": 168, "app_source": "手動輸入", "watch": 167, "diff": 1, "alert": False,
                 "from_watch": False}],
       "zones": None, "summary": "兩邊的差距都在 5 bpm 以內，不用改", "fixes": [], "where": "", "note": "推到手錶的課表心率目標直接送 bpm。",
       "history": {"rows": [], "n": 0, "last_change": None}}


def _with_compare(page, alert=False):
    def handle(route):
        if route.request.method != "GET":
            return route.continue_()
        r = route.fetch()
        body = r.json()
        body["compare"] = {**CMP, "alert": alert, "fixes": ["最大心率：把 COROS 的 180 改成 188"] if alert else []}
        route.fulfill(response=r, json=body)
    page.route("**/api/v1/plan/hr-profile", handle)


def _body(page):
    open_page(page, _server.PAGES["settings"] + "#body")
    page.locator("#pv-hr .pv-row").first.wait_for()
    page.locator("#pv-pw .pv-row").first.wait_for()


def test_the_zones_and_numbers_are_on_top_of_the_tab_and_not_folded(app):
    page, _ = app
    _body(page)
    box = page.locator("#physio")
    expect(box).to_be_visible()
    # SP-955: 我的區間 tops the right column (系統用的); the tiles moved to 門檻與測試 on the left
    assert box.bounding_box()["y"] < page.locator("#hr").bounding_box()["y"]
    tiles = page.locator("#thresholds .pv-tile")
    page.wait_for_function("() => document.querySelectorAll('#thresholds .pv-tile').length === 6")
    expect(tiles.nth(0)).to_contain_text("最大心率")
    expect(tiles.nth(1)).to_contain_text("靜息心率")
    expect(tiles.nth(2)).to_contain_text("LTHR")
    expect(tiles.nth(3)).to_contain_text("AeT")
    expect(tiles.nth(4)).to_contain_text("閾值配速")
    expect(tiles.nth(5)).to_contain_text("CP")
    expect(tiles.nth(2).locator(".v")).to_have_text("168bpm")
    # 心率: the six COROS zones, each in view with its bpm range and % LTHR, a tinted background and a colour bar
    hr = box.locator("#pv-hr .pv-row")
    expect(hr).to_have_count(6)
    expect(hr.nth(1)).to_contain_text("Z2")
    expect(hr.nth(1).locator(".r")).to_have_text("134–151")
    expect(hr.nth(1).locator(".p")).to_have_text("80–90%")
    expect(hr.nth(5).locator(".r")).to_have_text("≥ 178")
    expect(box.locator("#pv-hr .pv-bar i")).to_have_count(6)
    bgs = page.evaluate("() => [...document.querySelectorAll('#pv-hr .pv-row')].map((e) => getComputedStyle(e).backgroundColor)")
    assert len(set(bgs)) == 6 and all(b not in ("rgba(0, 0, 0, 0)", "transparent") for b in bgs), bgs
    # 功率: the plan's power zones in watts and % CP
    pw = box.locator("#pv-pw .pv-row")
    assert pw.count() >= 5
    expect(pw.first.locator(".p")).to_contain_text("%")
    expect(box.locator("#pv-pw h3")).to_contain_text("W")
    # nothing of it inside a <details>
    assert page.evaluate("() => !document.querySelector('#thresholds .pv-tiles, #physio .pv-zones').closest('details')")


def test_the_watch_check_is_one_sentence_when_close(app):
    page, _ = app
    _with_compare(page)
    _body(page)
    fold = page.locator("#physio #hr-cmp details.fold")
    expect(fold).to_be_visible()
    expect(fold.locator("> summary")).to_have_text("跟手錶的設定差距不大（都在 5 bpm 內），不用改 ✓")
    expect(fold).not_to_have_attribute("open", "")
    expect(page.locator("#hr-cmp table")).to_be_hidden()


def test_a_gap_keeps_what_to_change_and_folds_the_table(app):
    page, _ = app
    _with_compare(page, alert=True)
    _body(page)
    expect(page.locator("#hr-cmp h3 .pill.warn")).to_be_visible()
    expect(page.locator("#hr-cmp li")).to_have_text("最大心率：把 COROS 的 180 改成 188")
    expect(page.locator("#hr-cmp table").first).to_be_hidden()          # under 「詳細對照」


def test_a_phone_keeps_the_tab_inside_the_screen(app, e2e_browser, e2e_server):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, is_mobile=True,
                                  has_touch=True, device_scale_factor=2, locale="zh-TW", timezone_id="Asia/Taipei")
    try:
        page = ctx.new_page()
        _body(page)
        assert page.evaluate("() => [innerWidth, document.documentElement.scrollWidth]") == [390, 390]
    finally:
        ctx.close()
