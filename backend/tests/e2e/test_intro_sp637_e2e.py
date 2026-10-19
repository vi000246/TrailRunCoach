"""SP-637: the 介紹頁's interactive parts in a real browser — the small calculator answers from the server and
follows its sliders, the elevation profile shows a leg on hover (desktop) and on a tap (phone), 越野／百岳／路跑
switch the words, the calculator and the profile, motion stops with 「減少動態效果」, no sideways scroll at
phone width, no Stryd anywhere. R1: the hero's 課表 calendar opens a day's sessions and structure; 新增一堂課
picks a template and shows its structure. Opened by its file name like test_intro_sp622_e2e (the e2e server's "/" is
not the introduction).

Run: ./e2e.sh -k sp637 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/intro.html"
TIME = re.compile(r"^\d{1,2}:\d{2}$")


def _open(page, loc="zh-TW", size=(1400, 900), scheme="light", motion="no-preference"):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme=scheme, reduced_motion=motion)
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/v1/" in r.url and "/static/" not in r.url else None)
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    page.wait_for_function("() => /^\\d{1,2}:\\d{2}$/.test(document.getElementById('calc-time').textContent)")
    return asked


@pytest.mark.parametrize("size", [(390, 844), (1400, 900)])
def test_the_calculator_answers_from_the_server_and_follows_the_sliders(app, size):
    page, _w = app
    asked = _open(page, size=size)
    first = page.locator("#calc-time").text_content()
    assert TIME.match(first) and "km" in page.locator("#calc-km-v").text_content()
    km = page.locator("#calc-km")
    km.evaluate("(el) => { el.value = 80; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    page.wait_for_function("(t) => document.getElementById('calc-time').textContent !== t", arg=first)
    later = page.locator("#calc-time").text_content()
    mins = lambda t: int(t.split(":")[0]) * 60 + int(t.split(":")[1])  # noqa: E731
    assert TIME.match(later) and mins(later) > mins(first)              # 80 km takes longer than the 50K
    assert page.locator("#calc-km-v").text_content().startswith("80")
    # only the intro API: never the calculator's own private routes
    assert asked and all("/api/v1/intro/" in u for u in asked), asked
    assert any("/api/v1/intro/estimate?" in u and "km=80" in u for u in asked)
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= size[0], f"sideways scroll: {sw} > {size[0]}"


def test_the_profile_shows_a_leg_on_hover(app):
    page, _w = app
    _open(page)
    svg = page.locator("#prof-svg")
    svg.scroll_into_view_if_needed()
    page.wait_for_function("() => document.querySelectorAll('#prof-svg rect.band').length > 3")
    box = svg.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] / 2)
    leg = page.locator("#prof-leg")
    page.wait_for_function("() => document.querySelector('#prof-leg .h') !== null")
    text = leg.inner_text()
    assert re.search(r"第 \d+ 段", text) and "/km" in text and "碳水" in text and "心率" in text, text


def test_the_profile_shows_a_leg_on_a_tap_on_a_phone(e2e_browser, e2e_server):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True, locale="en-US")
    try:
        page = ctx.new_page()
        _open(page, loc="en", size=(390, 844))
        svg = page.locator("#prof-svg")
        svg.scroll_into_view_if_needed()
        page.wait_for_function("() => document.querySelectorAll('#prof-svg rect.band').length > 3")
        box = svg.bounding_box()
        page.touchscreen.tap(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
        page.wait_for_function("() => document.querySelector('#prof-leg .h') !== null")
        text = page.locator("#prof-leg").inner_text()
        assert re.search(r"Leg \d+", text) and "/km" in text and "carbs" in text, text
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
    finally:
        ctx.close()


def test_switching_kinds_changes_words_calculator_and_profile(app):
    page, _w = app
    _open(page)
    h1 = page.locator("#hero-h").text_content()
    seen = {}
    for kind in ("baiyue", "road", "trail"):
        page.locator(f'.hero button[data-kind="{kind}"]').click()
        page.wait_for_function("(k) => document.querySelector(`#profile button[data-kind='${k}']`).getAttribute('aria-pressed') === 'true'",
                               arg=kind)
        page.wait_for_function("(m) => document.getElementById('calc-km').max === m",
                               arg={"baiyue": "80", "road": "42", "trail": "100"}[kind])
        page.wait_for_function("() => /^\\d{1,2}:\\d{2}$/.test(document.getElementById('calc-time').textContent)")
        seen[kind] = (page.locator("#hero-h").text_content(), page.locator(".prof-title").text_content(),
                      page.locator("#calc-time").text_content(), page.locator("#prof-end").text_content())
    assert seen["trail"][0] == h1
    assert len({v[0] for v in seen.values()}) == 3 and len({v[1] for v in seen.values()}) == 3
    assert "百岳" in seen["baiyue"][0] and "路跑" in seen["road"][0]
    assert len({v[3] for v in seen.values()}) == 3                      # three courses


def test_reduced_motion_shows_everything_at_once(app):
    page, _w = app
    _open(page, motion="reduce")
    assert page.evaluate("() => document.documentElement.classList.contains('anim')") is False
    assert page.locator("#profile").evaluate("(el) => getComputedStyle(el).opacity") == "1"
    page.wait_for_function("() => document.querySelector('[data-stat=legs]').textContent !== '–'")
    assert int(page.locator("[data-stat=legs]").text_content()) > 3          # the final number, no count-up


def test_with_motion_the_sections_fade_in_and_the_numbers_count_up(app):
    page, _w = app
    _open(page)
    assert page.evaluate("() => document.documentElement.classList.contains('anim')") is True
    page.locator(".stats").scroll_into_view_if_needed()
    page.wait_for_function("() => document.getElementById('profile').classList.contains('in')")
    page.wait_for_function("() => Number(document.querySelector('[data-stat=legs]').textContent) > 3")


@pytest.mark.parametrize("size", [(390, 844), (1400, 900)])
def test_the_hero_calendar_shows_a_day_and_its_structure(app, size):
    # R1: the hero is the 課表 calendar — four weeks, the days after the horizon hatched; a day opens its
    # sessions with their structure bars
    page, _w = app
    asked = _open(page, size=size)
    page.wait_for_function("() => document.querySelectorAll('#cal-weeks .cal-wk').length === 4")
    assert page.locator("#cal-weeks .cal-day.later").count() > 0
    assert page.locator(".hero #calc").count() == 0
    # opened on a structured day already: bars and the steps in words
    assert page.locator("#cal-detail svg.bars rect").count() > 2
    first = page.locator("#cal-detail").text_content()
    page.locator(".cal-day[data-w='1'][data-i='5']").click()                  # 賽前 2 週 週六: the long run
    page.wait_for_function("(t) => document.getElementById('cal-detail').textContent !== t", arg=first)
    assert page.locator(".cal-day[data-w='1'][data-i='5']").get_attribute("aria-pressed") == "true"
    assert "205′" in page.locator("#cal-detail").text_content()
    page.locator(".cal-day.later").first.click(force=True)                    # not a button: nothing happens
    assert page.locator(".cal-day[data-w='1'][data-i='5']").get_attribute("aria-pressed") == "true"
    assert any("/api/v1/intro/plan" in u for u in asked) and all("/api/v1/intro/" in u for u in asked), asked
    assert page.evaluate("() => document.documentElement.scrollWidth") <= size[0]


@pytest.mark.parametrize("size", [(390, 844), (1400, 900)])
def test_adding_a_session_picks_a_template_and_shows_its_structure(app, size):
    # R1: 新增一堂課 → 選範本 → 看結構
    page, _w = app
    _open(page, size=size)
    page.locator("#tpl").scroll_into_view_if_needed()
    page.wait_for_function("() => document.querySelectorAll('.tpl-row').length === 3")
    view = page.locator("#tpl-view").text_content()
    assert page.locator("#tpl-view svg.bars.big rect").count() >= 3
    page.locator("#tpl-cats button[data-cat='quality']").click()
    page.wait_for_function("(t) => document.getElementById('tpl-view').textContent !== t", arg=view)
    rows = page.locator(".tpl-row")
    assert rows.count() == 3 and rows.nth(0).get_attribute("aria-pressed") == "true"
    rows.nth(2).click()
    assert rows.nth(2).get_attribute("aria-pressed") == "true"
    text = page.locator("#tpl-view").text_content()
    assert "16" in text and re.search(r"約 \d+ 分鐘", text)                   # 30-30 ×16, its length
    assert page.locator("#tpl-view svg.bars.big rect").count() > 30           # 16 fast + 16 easy bouts
    assert page.evaluate("() => document.documentElement.scrollWidth") <= size[0]


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_no_stryd_anywhere(app, loc):
    page, _w = app
    _open(page, loc=loc)
    # everything a reader can see, every FAQ answer open (the inlined catalog has the app's other words)
    page.evaluate("() => document.querySelectorAll('details').forEach((d) => { d.open = true; })")
    text = page.locator("body").inner_text().lower()
    attrs = page.evaluate("() => [...document.querySelectorAll('[alt],[title],[aria-label]')]"
                          ".map((e) => [e.alt, e.title, e.getAttribute('aria-label')].join(' ')).join(' ')").lower()
    assert "stryd" not in text and "stryd" not in attrs
    assert ("沒有功率計可以用嗎？" if loc == "zh-TW" else "no power meter") in text
