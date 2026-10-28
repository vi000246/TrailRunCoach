"""SP-637: the 介紹頁's interactive parts in a real browser — 越野／百岳／路跑 switch the words and (SP-969) the try
buttons' example (SP-969 Q2-3 took the small calculator and the elevation profile off the page), motion stops with 「減少動態效果」, no sideways scroll at
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


def _open(page, loc="zh-TW", size=(1400, 900), scheme="light", motion="no-preference"):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme=scheme, reduced_motion=motion)
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/v1/" in r.url and "/static/" not in r.url else None)
    page.goto(f"{PAGE}?lang={loc}", wait_until="load")
    # SP-969 Q2-3: the small calculator is gone — the page is ready once the try button carries the example
    page.wait_for_function("() => /\\d:\\d\\d:\\d\\d/.test(document.querySelector('.hero [data-try-sub]').textContent)")
    return asked


# SP-969 Q2-3 (the user, 2026-10-28): the small calculator and the elevation profile left the page — their
# tests (the sliders, a leg on hover / on a tap) went with them; the kind switch and the motion stay


def test_switching_kinds_changes_the_words_and_the_try_buttons(app):
    page, _w = app
    asked = _open(page)
    h1 = page.locator("#hero-h").text_content()
    seen = {}
    for kind, sid in (("baiyue", "example-baiyue-3day"), ("road", "example-road-half"), ("trail", "example-trail-50k")):
        before = page.locator(".hero [data-try-sub]").text_content()
        page.locator(f'.hero button[data-kind="{kind}"]').click()
        page.wait_for_function("(s) => document.querySelector('.hero a[data-try-big]').getAttribute('href') === '/share/' + s",
                               arg=sid)
        if kind != "trail":
            page.wait_for_function("(t) => document.querySelector('.hero [data-try-sub]').textContent !== t", arg=before)
        seen[kind] = (page.locator("#hero-h").text_content(), page.locator(".hero [data-try-h]").text_content(),
                      page.locator(".hero [data-try-sub]").text_content())
    assert seen["trail"][0] == h1
    assert len({v[0] for v in seen.values()}) == 3 and len({v[1] for v in seen.values()}) == 3
    assert len({v[2] for v in seen.values()}) == 3                      # three examples, three results
    assert "百岳" in seen["baiyue"][0] and "路跑" in seen["road"][0]
    assert asked and all("/api/v1/intro/" in u for u in asked), asked


def test_reduced_motion_shows_everything_at_once(app):
    page, _w = app
    _open(page, motion="reduce")
    assert page.evaluate("() => document.documentElement.classList.contains('anim')") is False
    assert page.locator("#wf-card").evaluate("(el) => getComputedStyle(el).opacity") == "1"


def test_with_motion_the_sections_fade_in(app):
    page, _w = app
    _open(page)
    assert page.evaluate("() => document.documentElement.classList.contains('anim')") is True
    page.locator("#wf-card").scroll_into_view_if_needed()
    page.wait_for_function("() => document.getElementById('wf-card').classList.contains('in')")


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
    # SP-1013: it opens on 強度課 (an interval session's structure has shape; an easy run is one flat bar)
    assert page.locator("#tpl-cats button[data-cat='quality']").get_attribute("aria-pressed") == "true"
    assert page.locator("#tpl-cats button[data-cat='easy']").get_attribute("aria-pressed") == "false"
    rows = page.locator(".tpl-row")
    assert rows.count() == 3 and rows.nth(0).get_attribute("aria-pressed") == "true"
    assert page.locator("#tpl-view svg.bars.big rect").count() >= 5           # warm-up, the reps, cool-down
    view = page.locator("#tpl-view").text_content()
    page.locator("#tpl-cats button[data-cat='easy']").click()
    page.wait_for_function("(t) => document.getElementById('tpl-view').textContent !== t", arg=view)
    assert rows.count() == 3 and rows.nth(0).get_attribute("aria-pressed") == "true"
    assert page.locator("#tpl-view svg.bars.big rect").count() >= 1
    page.locator("#tpl-cats button[data-cat='quality']").click()
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
