"""SP-987: the share page's 「賽前」 tile at phone width — the value box keeps the short amount
(「約 6 g/kg」 / "about 6 g/kg") on one line, the bracket's words (「正常高碳水」 / "a normal high-carb
day") go to the small line under it; the half-marathon example is a share without the weight, so the
tile shows the amount, not grams. The 10–12 g/kg tile (no bracket) stays as it was.

Run: ./e2e.sh -k sp987 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

TILE = """(k) => {
  const t = [...document.querySelectorAll('.tile')].find((x) => x.querySelector('.k').textContent.includes(k));
  if (!t) return null;
  const v = t.querySelector('.v'), lh = parseFloat(getComputedStyle(v).lineHeight) || parseFloat(getComputedStyle(v).fontSize) * 1.3;
  return { v: v.textContent, s: t.querySelector('.s').textContent, lines: Math.round(v.getBoundingClientRect().height / lh) };
}"""


def _tile(page, sid, loc, key, size=(390, 844)):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.goto(f"/share/{sid}?lang={loc}", wait_until="load")
    page.wait_for_selector(".tile")
    return page.evaluate(TILE, key)


@pytest.mark.parametrize("loc,key,amount,why", [
    ("en", "Before the race", "about 6 g/kg", "a normal high-carb day"),
    ("zh-TW", "賽前", "約 6 g/kg", "正常高碳水"),
])
def test_the_prerace_tile_keeps_the_short_amount_in_the_value_box_on_a_phone(app, loc, key, amount, why):
    page, _w = app
    t = _tile(page, "example-road-half", loc, key)
    assert t, "no 賽前 tile"
    assert t["v"] == amount, t
    assert t["lines"] == 1, t
    assert why in t["s"] and "(" not in t["v"] and "（" not in t["v"], t


def test_the_desktop_tile_and_the_10_12_tile_are_unchanged(app):
    page, _w = app
    t = _tile(page, "example-road-half", "en", "Before the race", size=(1400, 900))
    assert t["v"] == "about 6 g/kg" and t["lines"] == 1, t
    t = _tile(page, "example-trail-50k", "en", "Before the race")
    assert t["v"] == "10–12 g/kg" and "a normal" not in t["s"], t
