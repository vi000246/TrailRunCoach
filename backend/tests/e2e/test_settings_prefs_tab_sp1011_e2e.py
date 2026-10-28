"""設定 › 偏好設定 (SP-1011) in a real browser on the demo athlete.

外觀 and 地圖 moved from 個人資料 to their own tab, 偏好設定 (Preferences), right after 個人資料. The old
deep links (#appearance, #map, an id inside them) open the new tab at that block; 個人資料 no longer
shows them; the behaviour is the same (外觀 applies at once, 地圖 saves with its button); English;
at phone width nothing scrolls sideways.

Run: ./e2e.sh -k prefs_tab (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e


def _settings(page, query: str = "", hash_: str = ""):
    open_page(page, _server.PAGES["settings"] + query + hash_)
    page.wait_for_load_state("networkidle")


def _selected(page):
    return page.locator('.tabs [aria-selected="true"]')


def test_the_tab_follows_personal_and_holds_appearance_and_map(app):
    page, _ = app
    _settings(page)
    tabs = page.locator('.tabs [role="tab"]:visible')
    assert tabs.evaluate_all("(bs) => bs.map((b) => b.dataset.tab)")[:2] == ["personal", "prefs"]
    expect(page.locator("#tab-prefs")).to_have_text("偏好設定")
    # 個人資料: no 外觀, no 地圖
    expect(_selected(page)).to_have_attribute("data-tab", "personal")
    expect(page.locator("#general")).to_be_visible()
    expect(page.locator("#appearance")).to_be_hidden()
    expect(page.locator("#map")).to_be_hidden()
    page.locator("#tab-prefs").click()
    expect(_selected(page)).to_have_attribute("data-tab", "prefs")
    expect(page.locator("#appearance")).to_be_visible()
    expect(page.locator("#map")).to_be_visible()
    expect(page.locator("#general")).to_be_hidden()
    assert page.evaluate("location.hash") == "#prefs"


@pytest.mark.parametrize("hash_", ["#appearance", "#map", "#m-base", "#ap-theme"])
def test_old_deep_links_open_the_block_on_the_new_tab(app, hash_):
    page, _ = app
    _settings(page, "", hash_)
    expect(_selected(page)).to_have_attribute("data-tab", "prefs")
    expect(page.locator(hash_)).to_be_visible()
    expect(page.locator(hash_)).to_be_in_viewport()


def test_a_link_query_keeps_the_hash_block(app):
    """?link=<project> (a linked service other than Cairn) does not take the tab: the hash still does."""
    page, _ = app
    _settings(page, "?link=other", "#map")
    expect(_selected(page)).to_have_attribute("data-tab", "prefs")
    expect(page.locator("#map")).to_be_visible()


def test_appearance_applies_at_once_and_the_map_saves_with_its_button(app):
    page, _ = app
    sent = []

    def settings_put(route):
        if route.request.method == "PUT":
            sent.append(json.loads(route.request.post_data))
        route.fallback()
    page.route("**/api/v1/sync/settings", settings_put)
    _settings(page, "", "#prefs")
    page.locator("#ap-theme").select_option("trig-badge")
    expect(page.locator("html")).to_have_attribute("data-palette", "trig-badge")
    expect(page.locator("#ap-msg")).not_to_be_empty()
    assert not sent                                                        # 外觀 saves nothing on the server
    page.locator("#m-base").select_option("osm")
    assert not sent                                                        # 地圖: only with its button
    page.locator("#m-save").click()
    page.wait_for_function("() => document.querySelector('.toast.on')")
    assert sent and sent[-1]["map_basemap"] == "osm"


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_phone_width_does_not_scroll_sideways(app, lang):
    page, _ = app
    page.set_viewport_size({"width": 375, "height": 800})
    _settings(page, "?lang=en" if lang == "en" else "", "#appearance")
    expect(_selected(page)).to_have_attribute("data-tab", "prefs")
    expect(page.locator("#tab-prefs")).to_be_in_viewport()
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], w
    if lang == "en":
        expect(page.locator("#tab-prefs")).to_have_text("Preferences")
        expect(page.locator("#appearance h2")).to_contain_text("Appearance")
        expect(page.locator("#map h2")).to_contain_text("Maps")      # + its `?` (ST5)
