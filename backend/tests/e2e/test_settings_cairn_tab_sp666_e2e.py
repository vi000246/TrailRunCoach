"""設定 › 疊石 Cairn (SP-666) in a real browser on the demo athlete.

The e2e server runs in owner mode: there the Cairn tab never shows (no account, no link). The
sign-in mode is made in the page as test_settings_nomode_sp480.py does (the inlined TRC_SESSION
gets `login`), and the account routes answer from here (page.route): GET /account/me, GET
/account/links (Cairn's row as api/link.py builds it) and GET /account/links/cairn/pushes. A Cairn
link_url (?link=cairn&allow=link%3Acalc#links) lands on the tab with the asked switch highlighted;
at phone width nothing scrolls sideways, in Chinese and in English.

Run: ./e2e.sh -k cairn_tab (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SIGNED_IN = """() => { let v; Object.defineProperty(window, "TRC_SESSION", { configurable: true,
  get() { return v; }, set(x) { v = { ...x, login: { required: true, signed_in: true } }; } }); }"""

ME = {"email": "runner@example.com", "name": "跑者", "analysis": True, "can_delete": True, "reauth_ok": False}


def _row(linked=True, calc=False, push=True, plan="basic"):
    def item(scope, text, kind, detail, **kw):
        return {"scope": scope, "text": text, "kind": kind, "detail": detail, **kw}
    row = {"id": "cairn", "name": "疊石 Cairn", "linked": linked, "at": "2026-10-15T10:00:00" if linked else None,
           "available": True,
           "scopes": [item("link:account", "讀你的帳號與方案狀態（免費或訂閱、到期日）", "read", "Cairn 會讀：方案。"),
                      item("link:weather", "讀你路線的天氣", "read", "Cairn 會讀：天氣。")],
           "features": [item("link:calc", "讀你存過的賽事計算與配速曲線", "read", "Cairn 會讀：計算。",
                             allowed=linked and calc, plan="subscriber"),
                        item("link:gpx_sync", "把你的賽事路線、過夜點與腳程推到 Cairn（建立或更新行程）", "write",
                             "會寫到 Cairn：行程。", allowed=linked and push, plan="subscriber")],
           "plan_here": {"role": "subscriber", "plan_ok": True, "upgrade": False}}
    if linked:
        row["remote"] = {"plan": plan, "plan_expires_at": None, "push_per_day": 20}
    return row


PUSHES = [
    {"event_id": "race-2", "name": "嘉明湖 三天", "date": "2027-04-02", "gone": False, "trip_id": "trip-2",
     "at": "2026-10-15 21:40:00", "url": "https://map.yichlin.com/?gpx=trip-2"},
    {"event_id": "race-1", "name": "", "date": None, "gone": True, "trip_id": "trip-1",
     "at": "2026-10-14 08:00:00", "url": "https://map.yichlin.com/?gpx=trip-1"},
]


def _signed_in(page, row=None, pushes=PUSHES):
    page.add_init_script(f"({SIGNED_IN})()")
    page.route("**/api/v1/account/me", lambda r: r.fulfill(json=ME))
    page.route("**/api/v1/account/links", lambda r: r.fulfill(json={"clients": [row or _row()]}))
    page.route("**/api/v1/account/links/cairn/pushes", lambda r: r.fulfill(json={"pushes": pushes}))


def _settings(page, query: str = "", hash_: str = ""):
    open_page(page, _server.PAGES["settings"] + query + hash_)
    page.wait_for_load_state("networkidle")


def _no_side_scroll(page):
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], w


def test_owner_mode_has_no_cairn_tab(app):
    page, _ = app
    _settings(page, "?link=cairn&allow=link%3Acalc", "#links")
    expect(page.locator("#tab-cairn")).to_be_hidden()
    expect(page.locator("#cairn")).to_be_hidden()
    expect(page.locator('.tabs [aria-selected="true"]')).to_have_attribute("data-tab", "personal")
    _settings(page, "", "#cairn")
    expect(page.locator('.tabs [aria-selected="true"]')).to_have_attribute("data-tab", "personal")


def test_a_link_url_opens_the_tab_with_the_switch_highlighted(app):
    page, _ = app
    _signed_in(page)
    _settings(page, "?link=cairn&allow=link%3Acalc", "#links")
    expect(page.locator('.tabs [aria-selected="true"]')).to_have_attribute("data-tab", "cairn")
    expect(page.locator("#cairn")).to_be_visible()
    asked = page.locator('#cairn-features [data-link-scope="link:calc"]')
    expect(asked).to_have_class("cairn-item link-asked")
    expect(page.locator("#cairn-ask")).to_be_visible()
    # the account tab no longer has Cairn's row; the other tabs are still there
    page.locator('.tabs [data-tab="personal"]').click()
    expect(page.locator("#acct-links")).to_be_hidden()
    expect(page.locator("#account")).to_be_visible()


def test_the_tab_lists_what_cairn_reads_or_writes_the_plans_and_the_trips(app):
    page, _ = app
    _signed_in(page)
    _settings(page, "", "#cairn")
    items = page.locator("#cairn-features .cairn-item")
    expect(items).to_have_count(4)
    dirs = page.locator("#cairn-features .cairn-dir").all_inner_texts()
    assert dirs.count("Cairn 讀") == 3 and dirs.count("寫到 Cairn") == 1
    # the features are switches; what linking gives is not
    expect(page.locator('#cairn-features [data-link-scope="link:gpx_sync"] input[type=checkbox]')).to_be_checked()
    expect(page.locator('#cairn-features [data-link-scope="link:account"] input')).to_have_count(0)
    expect(page.locator("#cairn-plan")).to_contain_text("輕度會員")
    expect(page.locator("#cairn-plan")).to_contain_text("最多 20 次")
    lis = page.locator("ul.cairn-pushes li")
    expect(lis).to_have_count(2)
    expect(lis.nth(0)).to_contain_text("嘉明湖 三天")
    expect(lis.nth(0).locator("a")).to_have_attribute("href", "https://map.yichlin.com/?gpx=trip-2")
    expect(lis.nth(1)).to_contain_text("刪除")


def test_switching_a_feature_posts_its_scope(app):
    page, _ = app
    _signed_in(page)
    sent = []

    def scopes(route):
        sent.append(json.loads(route.request.post_data))
        route.fulfill(json=_row(calc=True))
    page.route("**/api/v1/account/links/cairn/scopes", scopes)
    _settings(page, "", "#cairn")
    page.locator('#cairn-features [data-link-scope="link:calc"] input').check()
    expect(page.locator("#cairn-msg")).not_to_be_empty()
    assert sent == [{"scope": "link:calc", "allowed": True}]


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_phone_width_does_not_scroll_sideways(app, lang):
    page, _ = app
    page.set_viewport_size({"width": 375, "height": 800})
    _signed_in(page)
    _settings(page, "?link=cairn&allow=link%3Agpx_sync" + ("&lang=en" if lang == "en" else ""), "#links")
    expect(page.locator('.tabs [aria-selected="true"]')).to_have_attribute("data-tab", "cairn")
    _no_side_scroll(page)
    if lang == "en":
        expect(page.locator("#tab-cairn")).to_have_text("Cairn")
        expect(page.locator("#cairn-plan")).to_contain_text("Light member")


def test_not_linked_highlights_the_link_first(app):
    page, _ = app
    _signed_in(page, row=_row(linked=False), pushes=[])
    _settings(page, "?link=cairn&allow=link%3Acalc", "#links")
    expect(page.locator("#cairn-status")).to_have_class("sub link-asked")
    expect(page.locator("#cairn-status button.primary")).to_be_visible()
    expect(page.locator('#cairn-features [data-link-scope="link:calc"] input')).to_be_disabled()
    expect(page.locator("#cairn-pushes")).to_contain_text("還沒有推過")
