"""SP-913 in a real browser on the demo athlete: the 活動列表's sticky 比較 bar (picked A) is never under the floating
「建議」 box (static/suggestions.js) or 「問 AI」 button (static/askai.js) — they go above it (shell.js
data-float-clear → --float-clear) — on a phone (390 px) and a desktop, stuck at the bottom or scrolled to the end;
cancelling puts them back where they were, and a page without such a bar (總覽) is not touched.

Run: ./e2e.sh -k sp913 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

GEOM = """() => {
  const R = (e) => { const r = e.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height}; };
  const bar = document.querySelector('#cmpbar');
  const floats = [...document.querySelectorAll('.sugbox:not([hidden]), .askai-fab:not([hidden])')].filter((e) => e.getBoundingClientRect().height > 0);
  // every visible thing in the bar is the topmost element at its centre (nothing floats over it)
  const covered = bar && !bar.hidden ? [...bar.querySelectorAll('a, button, b, .meta')].filter((e) => {
    const r = e.getBoundingClientRect(); if (!r.height) return false;
    const pts = [[r.left + 2, r.top + r.height / 2], [r.right - 2, r.top + r.height / 2], [(r.left + r.right) / 2, r.top + r.height / 2]];
    return pts.some(([x, y]) => { const t = document.elementFromPoint(x, y); return t && !bar.contains(t); });
  }).map((e) => e.textContent.trim()) : [];
  return {bar: bar && !bar.hidden ? R(bar) : null, floats: floats.map((e) => ({cls: e.className, ...R(e)})), covered,
          clear: document.documentElement.style.getPropertyValue('--float-clear'), vh: innerHeight};
}"""


def _overlaps(a, b) -> bool:
    return a["left"] < b["right"] and b["left"] < a["right"] and a["top"] < b["bottom"] and b["top"] < a["bottom"]


def _open(page, w: int, h: int):
    page.set_viewport_size({"width": w, "height": h})
    page.add_init_script("localStorage.setItem('suggestions.collapsed', '1')")
    open_page(page, _server.PAGES["activity"])
    page.wait_for_function("() => typeof S !== 'undefined' && S.acts && S.acts.length > 5", timeout=60_000)
    expect(page.locator(".askai-fab")).to_be_visible(timeout=30_000)
    page.wait_for_timeout(400)                       # the 建議 box arrives after its GET


def _pick_a(page):
    page.evaluate("() => pickCompare(S.acts[0].index)")
    expect(page.locator("#cmpbar")).to_be_visible()
    page.wait_for_timeout(200)


def _no_cover(g):
    assert g["bar"], g
    for f in g["floats"]:
        assert not _overlaps(f, g["bar"]), g
    assert not g["covered"], g


@pytest.mark.parametrize("w,h", [(390, 844), (1400, 900)])
def test_the_compare_bar_is_never_under_the_floating_buttons(app, w, h):
    page, _ = app
    _open(page, w, h)
    before = page.evaluate(GEOM)
    assert before["floats"], "no floating 問 AI / 建議 on the activity page"
    _pick_a(page)
    _no_cover(page.evaluate(GEOM))                    # stuck at the bottom of the screen
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(300)
    _no_cover(page.evaluate(GEOM))                    # scrolled to the end: the bar in its own place
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    _no_cover(page.evaluate(GEOM))
    # cancel: the floating buttons go back where they were
    page.locator("#cmp-cancel").click()
    expect(page.locator("#cmpbar")).to_be_hidden()
    page.wait_for_timeout(300)
    after = page.evaluate(GEOM)
    assert [round(f["top"]) for f in after["floats"]] == [round(f["top"]) for f in before["floats"]], (before, after)


def test_a_phone_lifts_both_above_the_bar_and_another_page_is_untouched(app):
    page, _ = app
    _open(page, 390, 844)
    _pick_a(page)
    g = page.evaluate(GEOM)
    _no_cover(g)
    # on a phone the bar spans the width: the box and the button sit above it
    for f in g["floats"]:
        assert f["bottom"] <= g["bar"]["top"], g
    assert g["clear"] and g["clear"] != "0px", g
    open_page(page, _server.PAGES["overview"])
    expect(page.locator(".askai-fab")).to_be_visible(timeout=30_000)
    page.wait_for_timeout(400)
    o = page.evaluate(GEOM)
    assert o["clear"] in ("", "0px"), o
    fab = next(f for f in o["floats"] if "askai-fab" in f["cls"])
    assert fab["bottom"] >= o["vh"] - 66 - 60, o         # still just above the tab bar / the 建議 box
