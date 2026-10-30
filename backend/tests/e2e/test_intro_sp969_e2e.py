"""SP-969: the 介紹頁 in a real browser — the try buttons carry the example's result and open it in a dialog
(its share page in a same-origin frame, covering the screen; × and Esc — also inside the frame — close it, the
focus goes back to the button, 「在新分頁開啟」 links the page itself); the waterfall lays out the server's
breakdown and its total is the example's finish time; 「為什麼不一樣」 shows the server's counts; no sideways
scroll at phone width. Opened by its file name like the other intro e2e tests (the e2e server's "/" is not
the introduction), with the counts the server would put in the page (window.TRC_WHY) set before it runs.

Run: ./e2e.sh -k sp969 (see conftest.py)."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/intro.html"
HMS = re.compile(r"\d{1,2}:\d{2}:\d{2}")


def _open(page, loc="zh-TW", size=(1400, 900), scheme="light"):
    from backend.api import intro as I
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.add_init_script(f"window.TRC_WHY = {json.dumps(I.why())};")
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    page.wait_for_function("() => document.querySelectorAll('#wf .wf-row').length > 3")
    page.wait_for_function("() => /\\d:\\d\\d:\\d\\d/.test(document.querySelector('.hero [data-try-sub]').textContent)")


def test_the_try_button_carries_the_examples_result_and_the_waterfall_adds_up_to_it(app):
    page, _w = app
    _open(page)
    sub = page.locator(".hero [data-try-sub]").text_content()
    ex = page.evaluate("() => fetch('/api/v1/intro/example/trail').then(r => r.json())")
    bd = page.evaluate("() => fetch('/api/v1/intro/breakdown').then(r => r.json())")
    s = ex["summary"]
    t = s["time_s"]
    hms = f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"
    assert f"{s['km']:.1f} km" in sub and hms in sub and f"{s['legs']} 段" in sub, sub
    total = page.locator("#wf .wf-row.sum .t").text_content()
    assert total == hms
    assert page.locator("#wf .wf-row:not(.sum):not(.axis)").count() == len(bd["layers"])
    # 為什麼不一樣: the server's counts
    from backend.api import intro as I
    w = I.why()
    assert page.locator('[data-why="sources"]').text_content() == str(w["sources"])
    assert page.locator('[data-why="calibrated"]').text_content() == str(w["calibrated"])
    assert page.locator('[data-why="zones"]').text_content() == str(w["zone_systems"])
    assert page.locator("#bt").count() == 0          # the back-test line is off the page (user, 2026-10-28)


@pytest.mark.parametrize("loc,slogan,story", [
    ("zh-TW", "備賽交給基石，報備交給疊石", "練夠了沒，你問基石；去哪座山，媽媽問疊石。"),
    ("en", "Trig gets you ready. Cairn gets everyone home.", None)])
def test_the_cairn_block_shows_its_link_only_when_the_server_gives_one(app, loc, slogan, story):
    page, _w = app
    _open(page, loc=loc)
    assert page.locator("#cairn-h").text_content() == slogan
    st = page.locator("#cairn-story")                  # SP-1058: zh only, no blank line in en
    if story:
        assert st.is_visible() and st.text_content() == story
    else:
        assert st.is_hidden()
    assert page.locator("#cairn-link").is_hidden()                       # the static file: no window.TRC_CAIRN
    page.add_init_script("window.TRC_CAIRN = {url: 'https://map.yichlin.com/?from=trc'};")
    _open(page, loc=loc)
    link = page.locator("#cairn-link")
    assert link.is_visible() and link.get_attribute("href") == "https://map.yichlin.com/?from=trc"
    assert link.get_attribute("target") == "_blank"


def test_the_slogan_over_the_hero_stays_when_the_tags_switch_and_is_not_in_english(app):
    # SP-1058: 「備賽，交給基石」 over the h1, the same for 越野跑／百岳／路跑 (the h1 changes); en: no line
    page, _w = app
    _open(page)
    assert page.title() == "Trig 基石 · 備賽，交給基石｜越野跑與百岳的課表、完賽試算"
    slogan = page.locator(".hero .slogan")
    h1s = []
    for kind in ("baiyue", "road", "trail"):
        page.locator(f'.hero .kinds [data-kind="{kind}"]').click()
        assert slogan.is_visible() and slogan.text_content() == "備賽，交給基石"
        h1s.append(page.locator("#hero-h").text_content())
    assert len(set(h1s)) == 3
    _open(page, loc="en")
    assert page.locator(".hero .slogan").is_hidden()
    assert page.title() == "Trig · race prep for trail running and Taiwan's 100 Peaks (Baiyue)"


def _opened(page):
    page.wait_for_function("() => !document.getElementById('ex-modal').hidden")
    page.wait_for_function("() => document.activeElement && document.activeElement.id === 'ex-close'")


def _closed_back_on(page, sel):
    page.wait_for_function("() => document.getElementById('ex-modal').hidden")
    assert page.evaluate("(s) => document.activeElement === document.querySelector(s)", sel)


def test_the_dialog_opens_the_example_and_closes_three_ways(app):
    page, _w = app
    _open(page)
    hero = ".hero a[data-try-big]"
    page.locator(hero).click()
    _opened(page)
    assert "/share/example-trail-50k" in page.locator("#ex-frame").get_attribute("src")
    assert page.locator("#ex-new").get_attribute("href") == "/share/example-trail-50k"
    assert page.locator("#ex-new").get_attribute("target") == "_blank"
    frame = page.frame_locator("#ex-frame")
    frame.locator("#main .tile").first.wait_for(state="visible", timeout=20_000)       # the share page itself
    assert "50K" in page.locator("#ex-title").text_content()
    assert page.evaluate("() => document.querySelector('main').inert") is True
    page.keyboard.press("Escape")
    _closed_back_on(page, hero)
    # ×
    end = "#race .try-end a[data-try-big]"
    page.locator(end).scroll_into_view_if_needed()
    page.locator(end).click()
    _opened(page)
    page.locator("#ex-close").click()
    _closed_back_on(page, end)
    page.locator(hero).focus()
    page.locator(hero).click()
    _opened(page)
    # it covers the screen (a whole page: no background left to click)
    box = page.locator("#ex-modal .dlg").bounding_box()
    vp = page.viewport_size
    assert box["x"] == 0 and box["y"] == 0 and box["width"] == vp["width"] and box["height"] == vp["height"]
    page.locator("#ex-close").click()
    _closed_back_on(page, hero)
    # Esc with the focus inside the frame (the share page)
    page.locator(hero).click()
    _opened(page)
    frame.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Escape")
    _closed_back_on(page, hero)
    assert page.evaluate("() => document.querySelector('main').inert") is False


def test_a_modified_click_is_a_plain_link(app):
    page, _w = app
    _open(page)
    page.locator(".hero a[data-try-big]").click(modifiers=["Meta"])
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.getElementById('ex-modal').hidden") is True


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_phone_width_has_no_sideways_scroll_with_the_waterfall_or_the_dialog(e2e_browser, e2e_server, loc):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True)
    try:
        page = ctx.new_page()
        _open(page, loc=loc, size=(390, 844), scheme="dark")
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        page.locator(".hero a[data-try-big]").tap()
        _opened(page)
        box = page.locator("#ex-modal .dlg").bounding_box()            # SP-1067: the 方法 dialog is a .dlg too
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        page.locator("#ex-close").tap()
        page.wait_for_function("() => document.getElementById('ex-modal').hidden")
    finally:
        ctx.close()
