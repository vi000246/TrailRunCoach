"""SP-643 on the demo athlete: the template filters show their levels — 類型 with a visible
heading, its 類別 right under it (›), 主課強度 a dropdown filter — the same in 插入範本 (the
structure editor) and on the 範本 page; no sideways scroll on a phone; the English labels.

Run: ./e2e.sh -k template_menu_sp643 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAGE = _server.PAGES["templates"]


def _menu(page, lang=None):
    page.add_init_script("localStorage.removeItem('templates.target_type')")
    open_page(page, PAGE + (f"?lang={lang}" if lang else ""))
    expect(page.locator("#tm-items li button").first).to_be_visible(timeout=120_000)
    page.click("#tm-new")
    page.click("#we-tplbtn")
    pop = page.locator("#we-pop")
    expect(pop.locator("button[data-cat]").first).to_be_visible()
    return pop


def test_insert_menu_levels(app):
    page, _ = app
    pop = _menu(page)
    f = pop.locator(".we-tf")
    # 類型: a visible heading; no 類別 row for 輕鬆跑
    expect(f.locator(".we-tf-top > .we-tf-h")).to_have_text("類型")
    pop.locator("button[data-cat='easy']").click()
    expect(pop.locator(".we-tf-sub")).to_have_count(0)
    # 強度課: 類別 right under 類型, marked as the next level
    pop.locator("button[data-cat='quality']").click()
    sub = pop.locator(".we-tf-sub")
    expect(sub).to_be_visible()
    expect(sub.locator(".we-tf-h")).to_have_text("› 類別")
    assert f.evaluate("e => [...e.children].map(c => c.className)") == ["we-tf-top", "we-tf-seg", "we-tf-sub"]
    sub.locator("button[data-sub='speed']").click()
    expect(pop.locator("button[data-sub='speed']")).to_have_attribute("aria-pressed", "true")
    # 主課強度: a dropdown (not a third row of chips), on the 類型 line; it filters the list
    expect(pop.locator("button[data-tt]")).to_have_count(0)
    sel = pop.locator(".we-tf-top select[data-tt]")
    expect(sel).to_be_visible()
    rows = pop.locator("button.t")
    n_all = rows.count()
    opts = sel.locator("option").evaluate_all("os => os.map(o => o.value)")
    assert opts[0] == "all" and len(opts) > 2
    narrowed = False
    for v in opts[1:]:
        sel.select_option(v)
        expect(pop.locator("select[data-tt]")).to_have_value(v)
        if rows.count() < n_all:
            narrowed = True
    assert narrowed, "主課強度 narrows the list"
    # kept for the 範本 page too (the shared key)
    assert page.evaluate("localStorage.getItem('templates.target_type')") == opts[-1]
    pop.locator("select[data-tt]").select_option("all")
    # still open after the dropdown, and the 類別 kept
    expect(pop).to_be_visible()
    expect(pop.locator("button[data-sub='speed']")).to_have_attribute("aria-pressed", "true")


def test_templates_page_same_filters(app):
    page, _ = app
    page.add_init_script("localStorage.removeItem('templates.target_type')")
    open_page(page, PAGE)
    lib = page.locator("#tm-items button[data-key]")
    expect(lib.first).to_be_visible(timeout=120_000)
    f = page.locator("#tm-filt .we-tf")
    expect(f.locator(".we-tf-top > .we-tf-h")).to_have_text("類型")
    expect(page.locator("#tm-filt .we-tf-sub")).to_have_count(0)        # 全部: no 類別
    page.click("#tm-filt button[data-cat='quality']")
    sub = page.locator("#tm-filt .we-tf-sub")
    expect(sub.locator(".we-tf-h")).to_have_text("› 類別")
    first = sub.locator("button[data-sub]").first
    expect(first).to_have_attribute("aria-pressed", "true")             # one is always picked
    n1 = lib.count()
    sub.locator("button[data-sub]").nth(1).click()
    expect(sub.locator("button[data-sub]").nth(1)).to_have_attribute("aria-pressed", "true")
    n2 = lib.count()
    assert n1 > 0 and n2 > 0
    expect(page.locator("#tm-filt select[data-tt]")).to_be_visible()
    expect(page.locator("#tm-filt button[data-tt]")).to_have_count(0)


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_phone_no_sideways_scroll(app, lang):
    page, _ = app
    page.set_viewport_size({"width": 360, "height": 780})
    pop = _menu(page, lang)
    pop.locator("button[data-cat='quality']").click()
    expect(pop.locator(".we-tf-sub")).to_be_visible()
    # the menu and everything in it inside the screen (the page itself is not this ticket's: in
    # English the 範本 page is already 1 px wider than a 360 px phone without the menu)
    box = pop.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 360
    assert pop.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    wide = pop.evaluate("e => [...e.querySelectorAll('*')].filter(c => c.getBoundingClientRect().right > innerWidth + 0.5).map(c => c.outerHTML.slice(0, 80))")
    assert wide == []
    if lang == "en":
        expect(pop.locator(".we-tf-top > .we-tf-h").first).to_have_text("Type")
        expect(pop.locator(".we-tf-sub .we-tf-h")).to_have_text("› Category")
        expect(pop.locator(".we-tf-tt .we-tf-h")).to_have_text("Main-set intensity")
