"""SP-625: any two activities compared — the three ways in (活動列表's compare button, 圖表分析's
「比較兩次」, the 路線 page's header link), the race filter, and the different-course charts on the demo
athlete (its runs share no route, so the point-by-point view is covered by the backend tests).

Run: ./e2e.sh -k activity_compare (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import chart_drawn, open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/app/activity-compare/page"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _two(page) -> list[int]:
    r = page.request.get("/api/v1/app/activity-compare/activities")
    assert r.ok
    rows = [a for a in r.json() if "run" in (a["sport_type"] or "") and (a["distance"] or 0) > 3]
    assert len(rows) >= 2, "the e2e athlete has fewer than two runs"
    return [rows[0]["index"], rows[1]["index"]]


def _drawn(page):
    for sel in ("#splits", "#ov", "#grades"):
        chart_drawn(page, sel)
    expect(page.locator("#sum tbody tr")).not_to_have_count(0)


def test_compare_page_draws_the_different_course_charts(app):
    page, _ = app
    open_page(page, PAGE)
    a, b = _two(page)
    open_page(page, f"{PAGE}?a={a}&b={b}")
    _drawn(page)
    # the x axis switches: share completed -> distance -> time
    for v in ("dist", "time", "pct"):
        page.click(f'#xmode button[data-v="{v}"]')
        expect(page.locator(f'#xmode button[data-v="{v}"]')).to_have_class(re.compile(r"\bon\b"))
        chart_drawn(page, "#ov")
    page.click('#omode button[data-v="hr"]')
    chart_drawn(page, "#ov")
    rows = page.locator("#sum tbody td:first-child").all_inner_texts()
    assert "時間" in rows and "平均配速" in rows and "爬升" in rows
    expect(page.locator("#grtab tbody tr")).to_have_count(3)


def test_race_filter_keeps_only_races(app):
    page, _ = app
    open_page(page, PAGE)
    # the list is loaded after the page: count it once it is there (+ 1: 「選一個活動…」), not the bare placeholder
    every = page.request.get("/api/v1/app/activity-compare/activities").json()
    expect(page.locator("#sa option")).to_have_count(len(every) + 1)
    page.check("#race")
    races = [a for a in page.request.get("/api/v1/app/activity-compare/activities?race=1").json()]
    assert len(races) < len(every)
    expect(page.locator("#sa option")).to_have_count(len(races) + 1)


def test_activity_list_compare_buttons_open_the_page(app):
    page, _ = app
    open_page(page, _server.PAGES["activity"])
    btn = page.locator("button[data-cmp]")
    expect(btn.first).to_be_visible(timeout=60_000)
    first, second = btn.nth(0).get_attribute("data-cmp"), btn.nth(1).get_attribute("data-cmp")
    btn.nth(0).click()
    expect(page.locator("#cmpbar")).to_be_visible()
    expect(page.locator("button[data-cmp]").nth(0)).to_have_attribute("aria-pressed", "true")
    page.locator("button[data-cmp]").nth(1).click()
    page.wait_for_url(re.compile(rf"activity-compare/page\?a={first}&b={second}"))
    _drawn(page)


def test_viewer_tick_two_then_compare(app):
    page, _ = app
    open_page(page, _server.PAGES["viewer"])
    page.wait_for_selector("#acts .act", state="attached", timeout=60_000)
    page.locator("#chip-acts").click()
    acts = page.locator("#acts .act:not(.excl)")
    expect(acts.first).to_be_visible(timeout=60_000)
    page.click("#cmp-mode")
    acts.nth(0).click()
    acts.nth(1).click()
    go = page.locator("#cmp-go")
    expect(go).to_be_visible()
    a, b = acts.nth(0).get_attribute("data-i"), acts.nth(1).get_attribute("data-i")
    assert go.get_attribute("href").endswith(f"?a={a}&b={b}")
    go.click()
    _drawn(page)


def test_routes_page_links_to_the_compare_page(app):
    page, _ = app
    open_page(page, _server.PAGES["routes"])
    page.click("a.btnlink[href*='activity-compare']")
    page.wait_for_url(re.compile("activity-compare/page"))
    expect(page.locator("#sa")).to_be_visible()


def test_english_page_has_no_chinese_chrome(app):
    page, _ = app
    open_page(page, PAGE)
    a, b = _two(page)
    open_page(page, f"{PAGE}?a={a}&b={b}&lang=en")
    _drawn(page)
    texts = page.evaluate("""() => [...document.querySelectorAll('main h1, main h2, main th, main td:first-child, main .note, main button, main label')]
                                   .map((e) => e.innerText)""")
    bad = [t for t in texts if CJK.search(t)]
    assert not bad, bad
