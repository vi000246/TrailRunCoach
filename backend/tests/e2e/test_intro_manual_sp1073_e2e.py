"""SP-1073: the 介紹頁's links to the manual open the chapter in a dialog (no new page): the chapter embedded
(no app bar, contents or pager), at the section the link names, titled with the chapter's own heading; the
glossary's charts still draw; it covers the screen; × and Esc (inside the frame too) close it and the focus goes back
to the link; inside, another chapter stays embedded and anything else opens in a new tab; no sideways scroll
at phone width. Opened by its file name like the other intro e2e tests.

Run: ./e2e.sh -k sp1073 (see conftest.py)."""
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
    page.wait_for_function("() => !document.getElementById('man-modal').hidden")
    assert page.evaluate("() => document.activeElement.id") == "man-close"
    page.frame_locator("#man-frame").locator(".m-head h1").wait_for()
    # the title is the chapter's own heading, once it has loaded
    page.wait_for_function("() => { const d = document.getElementById('man-frame').contentDocument;"
                           " const h = d && d.querySelector('.m-head h1');"
                           " return h && document.getElementById('man-title').textContent === h.textContent; }")


def _at(page, anchor):
    """The frame is at that section: its hash, and the section in the upper half of the frame (a section near
    the chapter's end can't scroll up to the top)."""
    page.wait_for_function("(a) => { const w = document.getElementById('man-frame').contentWindow;"
                           " const s = w && w.document.getElementById(a); if (!s || w.location.hash !== '#' + a) return false;"
                           " const top = s.getBoundingClientRect().top; return top >= -2 && top < w.innerHeight / 2; }", arg=anchor)


def _closed_back_on(page, sel):
    page.wait_for_function("() => document.getElementById('man-modal').hidden")
    assert page.evaluate("(s) => document.activeElement === document.querySelector(s)", sel)


def test_a_manual_link_opens_its_chapter_alone_and_closes_two_ways(app):
    page, _w = app
    _open(page)
    url = page.url
    sel = "#plan a[data-man]"                                           # 手冊：課表怎麼用
    page.locator(sel).scroll_into_view_if_needed()
    page.locator(sel).click()
    _opened(page)
    assert page.url == url                                               # still the 介紹頁
    src = page.locator("#man-frame").get_attribute("src")
    assert src.startswith("/manual/schedule?") and "embed=1" in src and "lang=zh-TW" in src
    frame = page.frame_locator("#man-frame")
    assert frame.locator("script[src*='shell.js']").count() == 0         # no app bar
    for hidden in (".m-side", ".m-pager", ".m-crumb"):
        assert frame.locator(hidden).evaluate("(el) => getComputedStyle(el).display") == "none", hidden
    assert frame.locator(".m-sec").count() > 3
    assert page.evaluate("() => document.querySelector('main').inert") is True
    # Esc with the focus inside the frame
    frame.locator(".m-head h1").click()
    page.keyboard.press("Escape")
    _closed_back_on(page, sel)
    # ×
    page.locator(sel).click()
    _opened(page)
    # it covers the screen (a whole page: no background left to click)
    box = page.locator("#man-modal .dlg").bounding_box()
    vp = page.viewport_size
    assert box["x"] == 0 and box["y"] == 0 and box["width"] == vp["width"] and box["height"] == vp["height"]
    page.locator("#man-close").click()
    _closed_back_on(page, sel)
    assert page.evaluate("() => document.querySelector('main').inert") is False
    assert page.url == url


def test_a_faq_link_lands_on_its_section_and_the_links_inside_stay_inside(app):
    page, _w = app
    _open(page)
    q = '#faq a[href="/manual/faq#estimate"]'
    page.locator(q).evaluate("(a) => a.closest('details').open = true")
    page.locator(q).click()
    _opened(page)
    frame = page.frame_locator("#man-frame")
    _at(page, "estimate")
    # another chapter stays embedded; the 方法 page (not the manual) opens in a new tab
    other = frame.locator('.m-main a[href^="/manual/"]')
    assert other.count() > 0
    for h in other.evaluate_all("(els) => els.map((a) => a.getAttribute('href'))"):
        assert "embed=1" in h, h
    methods = frame.locator('#estimate a[href="/methods"]')
    assert methods.get_attribute("target") == "_blank" and methods.get_attribute("rel") == "noopener"
    # another question of the same chapter: the frame moves to it
    page.keyboard.press("Escape")
    _closed_back_on(page, q)
    q2 = '#faq a[href="/manual/faq#import-export"]'
    page.locator(q2).evaluate("(a) => a.closest('details').open = true")
    page.locator(q2).click()
    page.wait_for_function("() => !document.getElementById('man-modal').hidden")
    _at(page, "import-export")


def test_the_glossary_charts_draw_in_the_dialog(app):
    page, _w = app
    _open(page)
    sel = "#read a[data-man]"                                           # 手冊：名詞表
    page.locator(sel).scroll_into_view_if_needed()
    page.locator(sel).click()
    _opened(page)
    frame = page.frame_locator("#man-frame")
    frame.locator(".m-chart canvas").first.wait_for(state="attached", timeout=20_000)


def test_a_modified_click_is_a_plain_link(app):
    page, _w = app
    _open(page)
    page.locator("#plan a[data-man]").click(modifiers=["Meta"])
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.getElementById('man-modal').hidden") is True


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_phone_width_has_no_sideways_scroll(e2e_browser, e2e_server, loc):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True)
    try:
        page = ctx.new_page()
        _open(page, loc=loc, size=(390, 844), scheme="dark")
        sel = "#start a[data-man]"                                       # 手冊：入門
        page.locator(sel).scroll_into_view_if_needed()
        page.locator(sel).tap()
        _opened(page)
        box = page.locator("#man-modal .dlg").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        inner = page.evaluate("() => { const d = document.getElementById('man-frame').contentDocument.documentElement;"
                              " return [d.scrollWidth, d.clientWidth]; }")
        assert inner[0] <= inner[1] + 1, inner                            # nothing wider than the frame
        page.locator("#man-close").tap()
        page.wait_for_function("() => document.getElementById('man-modal').hidden")
    finally:
        ctx.close()
