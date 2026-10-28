"""SP-975: the 介紹頁's 賽事周期 in a real browser — the server's season drawn one band per phase, the A / B races
marked, 「今天」 inside the 專項期; the line under it says the phase now and, hovered or tapped, another phase's
length and sources; the section fits one desktop screen; no sideways scroll at phone width, light or dark,
Trig or 原本. Opened by its file name like the other intro e2e tests.

Run: ./e2e.sh -k sp975 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/intro.html"


def _open(page, loc="zh-TW", size=(1400, 900), scheme="light", palette=None):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    if palette:     # what theme.js would set (SP-974)
        page.add_init_script(f"document.documentElement.dataset.palette = '{palette}';"
                             "document.documentElement.dataset.theme = 'light';")
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    page.wait_for_function("() => document.querySelectorAll('#cy-tl .seg').length > 3")


def test_the_season_is_drawn_from_the_server_and_says_what_each_phase_stands_on(app):
    page, _w = app
    _open(page)
    d = page.evaluate("() => fetch('/api/v1/intro/cycle').then(r => r.json())")
    assert page.locator("#cy-tl .seg").count() == len(d["phases"])
    assert page.locator("#cy-tl .ev").count() == len(d["events"])
    assert page.locator("#cy-tl .mini").count() == len(d["b_windows"])
    assert page.locator("#cy-tl .ev.A").count() == 2 and page.locator("#cy-tl .ev.B").count() == 2
    # 今天 inside the 專項期's band
    today = page.locator("#cy-tl .today").bounding_box()
    i = next(i for i, p in enumerate(d["phases"]) if p["start"] <= 0 <= p["end"])
    seg = page.locator("#cy-tl .seg").nth(i).bounding_box()
    assert seg["x"] <= today["x"] <= seg["x"] + seg["width"]
    assert page.locator("#cy-tip").text_content() == "現在：專項期 8 週 · 依據：Uphill Athlete、Pfitzinger、Canova、Daniels、江晏慶"
    taper = next(i for i, p in enumerate(d["phases"]) if p["kind"] == "taper" and p["event"] == "trail50k")
    page.locator("#cy-tl .seg").nth(taper).hover()
    assert page.locator("#cy-tip").text_content().startswith("減量期 10 天 · 依據：Bosquet 2007")
    page.mouse.move(5, 5)
    assert page.locator("#cy-tip").text_content().startswith("現在：專項期")
    rec = next(i for i, p in enumerate(d["phases"]) if p["kind"] == "recovery" and p["event"] == "trail50k")
    page.locator("#cy-tl .seg").nth(rec).click()
    page.mouse.move(5, 5)
    assert page.locator("#cy-tip").text_content().startswith("恢復期 14 天（超馬級）")
    # the section fits one desktop screen, the card beside the text
    box = page.locator("#cycle").bounding_box()
    assert box["height"] <= 900, box
    txt, card = page.locator("#cycle .text").bounding_box(), page.locator("#cycle .cy").bounding_box()
    assert card["x"] > txt["x"] + txt["width"] - 1
    assert page.locator("#cycle a.more").get_attribute("href") == "/methods#cycle"     # SP-973: the 方法 card


def test_english_words(app):
    page, _w = app
    _open(page, loc="en")
    assert page.locator("#cy-tip").text_content().startswith("Now: Build, 8 weeks")
    assert "Base" in page.locator("#cy-legend").text_content()


@pytest.mark.parametrize("loc,scheme,palette", [("zh-TW", "dark", None), ("en", "light", "trig-cream"),
                                                ("zh-TW", "light", "trig-badge")])
def test_phone_width_has_no_sideways_scroll(e2e_browser, e2e_server, loc, scheme, palette):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True)
    try:
        page = ctx.new_page()
        _open(page, loc=loc, size=(390, 844), scheme=scheme, palette=palette)
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        card = page.locator("#cycle .cy").bounding_box()
        assert card["x"] >= 0 and card["x"] + card["width"] <= 390
        for i in range(page.locator("#cy-tl .ev span").count()):
            b = page.locator("#cy-tl .ev span").nth(i).bounding_box()
            assert b["x"] >= card["x"] and b["x"] + b["width"] <= card["x"] + card["width"], i
        assert page.locator("#cycle").bounding_box()["height"] < 1300
        d = page.evaluate("() => fetch('/api/v1/intro/cycle').then(r => r.json())")
        taper = next(i for i, p in enumerate(d["phases"]) if p["kind"] == "taper" and p["event"] == "trail50k")
        before = page.locator("#cy-tip").text_content()
        page.locator("#cy-tl .seg").nth(taper).tap()
        after = page.locator("#cy-tip").text_content()
        assert after and after != before and "10" in after
    finally:
        ctx.close()
