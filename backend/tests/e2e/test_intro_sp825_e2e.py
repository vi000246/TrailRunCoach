"""SP-825: the 介紹頁's 給教練 in a real browser — on a desktop the text on the left and the small 教練看板 on the
right, the whole section within one screen; at phone width nothing scrolls sideways and every student's row stays
inside its card, light or dark, Trig or 原本; the card's colours follow the palette.

Run: ./e2e.sh -k sp825 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/intro.html"


def _open(page, loc="zh-TW", size=(1400, 900), theme="trig-cream"):
    """theme: 設定 › 外觀 — trig-cream (a light system's default), trig-badge, classic, classic-dark (SP-974)."""
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme="dark" if theme == "classic-dark" else "light", reduced_motion="reduce")
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    page.evaluate("t => window.TrcTheme.set(t)", theme)
    page.locator("#coach").scroll_into_view_if_needed()


def _chip(page):
    return page.evaluate("() => { const s = getComputedStyle(document.querySelector('#coach .cbx-chip.bad'));"
                         " return [s.color, s.backgroundColor]; }")


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_text_left_board_right_in_one_screen(app, loc):
    page, _w = app
    _open(page, loc=loc)
    box = page.locator("#coach").bounding_box()
    assert box["height"] <= 900, box
    txt, card = page.locator("#coach .text").bounding_box(), page.locator("#coach .cbx").bounding_box()
    assert card["x"] > txt["x"] + txt["width"] - 1
    assert page.locator("#coach .text li").count() == 4
    assert page.locator("#coach .cbx-row:not(.head)").count() == 4
    assert page.locator("#coach .cbx-row.head").is_visible()
    sub = page.locator("#coach .cbx-sub").text_content()
    assert ("虛構" in sub) if loc == "zh-TW" else ("Fictional" in sub)
    # a row on one line: its four cells side by side
    cells = page.locator("#coach .cbx-row:not(.head)").first.locator(":scope > span")
    ys = {round(cells.nth(i).bounding_box()["y"]) for i in range(4)}
    assert len(ys) == 1, ys


def test_the_card_follows_the_palette(e2e_browser, e2e_server):
    seen = []
    for theme in ("classic", "classic-dark", "trig-cream", "trig-badge"):
        ctx = e2e_browser.new_context(base_url=e2e_server.url)
        try:
            page = ctx.new_page()
            _open(page, theme=theme)
            panel = page.evaluate("() => getComputedStyle(document.querySelector('#coach .cbx')).backgroundColor")
            seen.append((panel, *_chip(page)))
        finally:
            ctx.close()
    assert len({s[0] for s in seen}) == 4, seen          # the card's ground: each palette its own
    assert len({s[1] for s in seen}) >= 3, seen          # the red chip's ink follows it too
    assert all(s[1] != s[2] for s in seen)


@pytest.mark.parametrize("loc,theme", [("zh-TW", "classic-dark"), ("en", "trig-cream"), ("zh-TW", "trig-badge"),
                                       ("en", "classic")])
def test_phone_width_has_no_sideways_scroll(e2e_browser, e2e_server, loc, theme):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True)
    try:
        page = ctx.new_page()
        _open(page, loc=loc, size=(390, 844), theme=theme)
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        card = page.locator("#coach .cbx").bounding_box()
        assert card["x"] >= 0 and card["x"] + card["width"] <= 390
        assert not page.locator("#coach .cbx-row.head").is_visible()
        # three students on a phone (the fourth is the desktop's), closer to one screen
        assert not page.locator("#coach .cbx-row.wide-only").is_visible()
        rows = "#coach .cbx-row:not(.head):not(.wide-only)"
        assert page.locator(rows).count() == 3
        els = page.locator(", ".join(f"{rows} {c}" for c in (".cbx-name", ".cbx-race", ".cbx-chip", ".cbx-num",
                                                             ".cbx-small")))
        assert els.count() > 12
        for i in range(els.count()):
            b = els.nth(i).bounding_box()
            assert b["x"] >= card["x"] and b["x"] + b["width"] <= card["x"] + card["width"] + 0.5, i
        # the labels the hidden header row carried come back inside the cells
        assert page.locator(f"{rows} .cbx-k").first.is_visible()
        assert page.locator("#coach").bounding_box()["height"] < 1100
    finally:
        ctx.close()
