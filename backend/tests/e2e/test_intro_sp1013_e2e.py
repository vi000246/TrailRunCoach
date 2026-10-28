"""SP-1013: the 介紹頁's two try buttons as the card the user picked (mock 6, 真實剖面) in a real browser — the
example's elevation profile and its stops drawn from the example API and redrawn for 越野／百岳／路跑 (a 百岳
trek rings its nights, a road race has no stops), still a link that opens the dialog from the keyboard, and in
each of the four 外觀 at phone width nothing spills sideways. Opened by its file name like the other intro e2e
tests; the 外觀 set as the settings page does (TrcTheme.set, like test_intro_sp825_e2e).

Run: ./e2e.sh -k sp1013 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/intro.html"
HERO = ".hero a[data-try-big]"
END = "#race .try-end a[data-try-big]"


def _open(page, loc="zh-TW", size=(1400, 900), theme="trig-cream"):
    """theme: 設定 › 外觀 — trig-cream (a light system's default), trig-badge, classic, classic-dark (SP-974)."""
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme="dark" if theme == "classic-dark" else "light", reduced_motion="reduce")
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    page.evaluate("t => window.TrcTheme.set(t)", theme)
    page.wait_for_function("(s) => document.querySelectorAll(s + ' [data-try-prof] path.line').length === 1", arg=HERO)


def _drawn(page, sel):
    return page.evaluate("""(s) => { const a = document.querySelector(s);
        return { line: a.querySelector('[data-try-prof] path.line').getAttribute('d'),
                 stops: a.querySelectorAll('[data-try-prof] circle.stop').length,
                 tag: a.querySelector('[data-try-tag]').textContent,
                 noprof: a.classList.contains('noprof') }; }""", sel)


def test_the_card_draws_the_examples_profile_and_stops_and_follows_the_kind(app):
    page, _w = app
    _open(page)
    seen = {}
    for kind in ("trail", "baiyue", "road"):
        if kind != "trail":
            before = _drawn(page, HERO)["line"]
            page.locator(f'.hero button[data-kind="{kind}"]').click()
            page.wait_for_function("(a) => document.querySelector(a[0] + ' [data-try-prof] path.line').getAttribute('d') !== a[1]",
                                   arg=[HERO, before])
        d = page.evaluate("(k) => fetch('/api/v1/intro/example/' + k).then(r => r.json())", kind)
        hero, end = _drawn(page, HERO), _drawn(page, END)
        assert hero == end, kind                                   # the two buttons are one card
        assert not hero["noprof"]
        assert len(re.findall(r"[ML]", hero["line"])) == len(d["profile"]), kind
        nights = sum(1 for a, b in zip(d["legs"], d["legs"][1:]) if a["day"] and b["day"] and a["day"] != b["day"])
        assert hero["stops"] == len(d["stops"]) + nights, kind
        seen[kind] = hero
    assert seen["trail"]["stops"] == 3 and seen["baiyue"]["stops"] == 2 and seen["road"]["stops"] == 0
    assert seen["trail"]["tag"] == seen["road"]["tag"] == "示範賽事・免註冊"
    assert seen["baiyue"]["tag"] == "示範行程・免註冊"
    # the road race is flat: its line stays near the bottom (the height keeps a 300 m scale)
    ys = [float(y) for y in re.findall(r"[ML][\d.]+ ([\d.]+)", seen["road"]["line"])]
    assert min(ys) > 45


def test_the_card_is_a_link_the_keyboard_opens(app):
    page, _w = app
    _open(page, loc="en")
    assert _drawn(page, HERO)["tag"] == "Sample race · no sign-up"
    page.locator(HERO).focus()
    page.keyboard.press("Enter")
    page.wait_for_function("() => !document.getElementById('ex-modal').hidden")
    assert "/share/example-trail-50k" in page.locator("#ex-frame").get_attribute("src")
    page.keyboard.press("Escape")
    page.wait_for_function("() => document.getElementById('ex-modal').hidden")
    assert page.evaluate("(s) => document.activeElement === document.querySelector(s)", HERO)
    # the profile is decoration: the link's name is the tag, the title and the numbers
    name = page.locator(HERO).inner_text()
    assert "50K" in name and re.search(r"\d:\d\d:\d\d", name)


@pytest.mark.parametrize("theme", ["trig-cream", "trig-badge", "classic", "classic-dark"])
def test_phone_width_in_each_palette(e2e_browser, e2e_server, theme):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True)
    try:
        page = ctx.new_page()
        _open(page, size=(390, 844), theme=theme)
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        for sel in (HERO, END):
            b = page.locator(sel).bounding_box()
            assert b["x"] >= 0 and b["x"] + b["width"] <= 390, sel
            # the profile runs the card's whole width along its bottom (no other rule boxes it in)
            ln = page.locator(sel + " [data-try-prof] path.line").bounding_box()
            assert ln["width"] >= 0.95 * b["width"] and ln["y"] + ln["height"] <= b["y"] + b["height"], sel
            assert ln["y"] >= page.locator(sel + " [data-try-sub]").bounding_box()["y"], sel
        # the line is the palette's accent, the ground behind the text its panel
        got = page.evaluate("""(s) => { const a = document.querySelector(s), cs = getComputedStyle(document.documentElement);
            const probe = document.createElement('i'); probe.style.color = cs.getPropertyValue('--accent'); document.body.appendChild(probe);
            const accent = getComputedStyle(probe).color; probe.remove();
            return { line: getComputedStyle(a.querySelector('path.line')).stroke, accent }; }""", HERO)
        assert got["line"] == got["accent"], theme
    finally:
        ctx.close()
