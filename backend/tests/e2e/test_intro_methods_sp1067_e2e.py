"""SP-1067: the 介紹頁's links to a method or to the sources open a dialog on the page (no new page): the card's
words (the 方法 page's detail), every card by group, the calibrated ones or every source; × , the background
and Esc close it and the focus goes back to the link; a modified click still follows the href; the waterfall's
layers open their card too; no sideways scroll at phone width. Opened by its file name like the other intro
e2e tests.

Run: ./e2e.sh -k sp1067 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/intro.html"


def _open(page, loc="zh-TW", size=(1400, 900), scheme="light"):
    from backend.api import intro as I
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.add_init_script(f"window.TRC_WHY = {json.dumps(I.why())};")
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    page.wait_for_function("() => document.querySelectorAll('#wf .wf-row').length > 3")


def _opened(page):
    page.wait_for_function("() => !document.getElementById('m-modal').hidden")
    page.wait_for_function("() => !document.querySelector('#m-body .m-msg')")      # the words are in
    assert page.evaluate("() => document.activeElement.id") == "m-close"


def _closed_back_on(page, sel):
    page.wait_for_function("() => document.getElementById('m-modal').hidden")
    assert page.evaluate("(s) => document.activeElement === document.querySelector(s)", sel)


def test_each_card_opens_its_words_on_the_page_and_closes_three_ways(app):
    page, _w = app
    _open(page)
    url = page.url
    zones = '#why a[data-m="zones"]'
    page.locator(zones).scroll_into_view_if_needed()
    page.locator(zones).click()
    _opened(page)
    assert page.url == url                                                         # still the 介紹頁
    assert page.locator("#m-title").text_content()
    assert page.locator("#m-body .md-src li").count() >= 1                         # its sources
    assert page.evaluate("() => document.querySelector('main').inert") is True
    page.keyboard.press("Escape")
    _closed_back_on(page, zones)
    # every card, by group: one open at a time is the reader's choice
    allm = '#why a[data-m="@all"]'
    page.locator(allm).click()
    _opened(page)
    n = page.locator("#m-body details.m-acc").count()
    assert n == page.evaluate("() => fetch('/api/v1/intro/methods').then(r => r.json()).then(d => d.models.length)")
    assert page.locator("#m-body .m-g").count() == 3
    first = page.locator("#m-body details.m-acc").first
    assert not first.locator(".md-plain").is_visible()
    first.locator("> summary").click()
    assert first.locator(".md-plain").is_visible()
    page.locator("#m-close").click()
    _closed_back_on(page, allm)
    # the calibrated ones
    cal = '#why a[data-m="@calibrated"]'
    page.locator(cal).click()
    _opened(page)
    assert page.locator("#m-body .m-cal li").count() >= 5
    page.mouse.click(5, 5)                                                         # the background
    _closed_back_on(page, cal)
    # the 推估 card: the long-race durability
    est = '#why a[data-m="durability"]'
    page.locator(est).click()
    _opened(page)
    assert "Clark IE" in page.locator("#m-body").text_content()
    page.keyboard.press("Escape")
    _closed_back_on(page, est)
    assert page.evaluate("() => document.querySelector('main').inert") is False
    assert page.url == url


def test_the_cycle_and_the_waterfall_open_the_dialog_too(app):
    page, _w = app
    _open(page)
    url = page.url
    for sel, word in (("#cycle a[data-m]", "Pfitzinger"),
                      ('#wf a[data-m="effort"]', "UTMB")):
        page.locator(sel).scroll_into_view_if_needed()
        page.locator(sel).click()
        _opened(page)
        assert word in page.locator("#m-body").text_content(), sel
        page.keyboard.press("Escape")
        _closed_back_on(page, sel)
    assert page.url == url


def test_a_modified_click_is_a_plain_link(app):
    page, _w = app
    _open(page)
    page.locator('#why a[data-m="zones"]').click(modifiers=["Meta"])
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.getElementById('m-modal').hidden") is True


def test_english(app):
    page, _w = app
    _open(page, loc="en")
    page.locator('#why a[data-m="@all"]').click()
    _opened(page)
    assert page.locator("#m-title").text_content() == "Every model's formula and sources"


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_phone_width_has_no_sideways_scroll_and_the_words_scroll(e2e_browser, e2e_server, loc):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True)
    try:
        page = ctx.new_page()
        _open(page, loc=loc, size=(390, 844), scheme="dark")
        a = '#why a[data-m="@all"]'
        page.locator(a).scroll_into_view_if_needed()
        page.locator(a).tap()
        _opened(page)
        box = page.locator(".m-dlg").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390 and box["y"] + box["height"] <= 844
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        body = page.locator("#m-body")
        assert body.evaluate("(el) => el.scrollHeight > el.clientHeight")              # it scrolls inside
        assert page.locator(".m-dlg .dlg-f a[href='/login']").is_visible()
        page.locator("#m-close").tap()
        page.wait_for_function("() => document.getElementById('m-modal').hidden")
    finally:
        ctx.close()
