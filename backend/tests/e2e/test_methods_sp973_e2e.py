"""SP-973: the 方法 page in a real browser — /methods#<id> opens that card (its detail beside the wall on a
desktop, under the card on a phone), a click opens another, the filters hide the rest, #calibrated sets a
filter; no sideways scroll at phone width, light and dark; the 介紹頁's waterfall layers link their cards.

Run: ./e2e.sh -k sp973 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e


def _open(page, hash_="", loc="zh-TW", size=(1400, 900), scheme="light"):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.emulate_media(color_scheme=scheme, reduced_motion="reduce")
    page.goto(f"/methods?lang={loc}{hash_}", wait_until="load")
    page.wait_for_selector(".md-main.md-js")


def test_a_hash_opens_its_card_beside_the_wall(app):
    page, _w = app
    _open(page, "#durability")
    assert page.locator("#durability").is_visible()
    assert page.locator('.md-card[data-id="durability"]').get_attribute("aria-expanded") == "true"
    assert page.locator(".md-d:visible").count() == 1
    assert page.locator("#md-pane #durability").count() == 1                 # beside the wall
    assert "Clark IE" in page.locator("#durability").text_content()
    # another card: the one detail moves to it, the hash follows
    page.locator('.md-card[data-id="altitude"]').click()
    assert page.locator("#altitude").is_visible() and not page.locator("#durability").is_visible()
    assert page.evaluate("location.hash") == "#altitude"
    assert "Péronnet" in page.locator("#altitude").text_content()


def test_without_a_hash_only_the_hint_and_the_filters_work(app):
    page, _w = app
    _open(page)
    assert page.locator(".md-d:visible").count() == 0 and page.locator(".md-hint").is_visible()
    n = page.locator(".md-card").count()
    page.locator('.md-filters [data-f="race"]').click()
    race = page.locator(".md-card:visible").count()
    assert 0 < race < n
    for g in page.locator(".md-card:visible").evaluate_all("els => els.map(e => e.dataset.g)"):
        assert g == "race"
    page.locator('.md-filters [data-f="all"]').click()
    assert page.locator(".md-card:visible").count() == n


def test_the_calibrated_hash_is_a_filter(app):
    page, _w = app
    _open(page, "#calibrated")
    assert page.locator('.md-filters [data-f="calibrated"]').get_attribute("aria-pressed") == "true"
    cals = page.locator(".md-card:visible").evaluate_all("els => els.map(e => e.dataset.cal)")
    assert cals and "no" not in cals


@pytest.mark.parametrize("loc,scheme", [("zh-TW", "light"), ("en", "dark")])
def test_phone_width_opens_under_the_card_without_sideways_scroll(app, loc, scheme):
    page, _w = app
    _open(page, "#cycle", loc=loc, size=(390, 844), scheme=scheme)
    assert page.locator("#cycle").is_visible()
    after = page.evaluate("document.getElementById('cycle').previousElementSibling.dataset.id")
    assert after == "cycle"                                                  # right under its card
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.locator("#cycle .md-x").click()
    assert not page.locator("#cycle").is_visible()
    page.locator('.md-card[data-id="heat"]').click()
    assert page.locator("#heat").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_the_intro_waterfall_links_each_layer_to_its_card(app):
    page, _w = app
    from backend.api import intro as I
    from backend.api import methods as M
    page.set_viewport_size({"width": 1400, "height": 900})
    page.add_init_script(f"window.TRC_WHY = {json.dumps(I.why())};")
    page.goto("/api/v1/static/intro.html?lang=zh-TW", wait_until="load")
    page.wait_for_function("() => document.querySelectorAll('#wf .wf-row').length > 3")
    hrefs = page.locator("#wf .wf-row .lab a").evaluate_all("els => els.map(e => e.getAttribute('href'))")
    bd = page.evaluate("() => fetch('/api/v1/intro/breakdown').then(r => r.json())")
    want = [f"/methods#{M.LAYER_CARD[x['key']]}" for x in bd["layers"] if x["key"] in M.LAYER_CARD]
    assert hrefs == want and len(want) >= 6
    assert page.locator('#why a[href="/methods#zones"]').text_content().strip()
