"""SP-851 in a real browser on the demo athlete: on a desktop the 課表 leaves the floating 建議 box its room at the
right (static/suggestions.js data-sugbox-dock → --sugbox-w), so the 週統計 column is never under it — open or
collapsed — and the box is still there; 問 AI (askai.js) stacks above it in that room. A phone is unchanged
(the box floats at the bottom as before), and another page (總覽) is not padded.

Run: ./e2e.sh -k sp851 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402

pytestmark = pytest.mark.e2e

GEOM = """() => {
  const box = document.querySelector('.sugbox:not([hidden])');
  const sums = [...document.querySelectorAll('.wk .sum')].map((e) => e.getBoundingClientRect());
  const cal = document.querySelector('.calwrap').getBoundingClientRect();
  const b = box && box.getBoundingClientRect();
  return {box: b && {left: b.left, right: b.right, top: b.top, width: b.width, height: b.height},
          sumRight: Math.max(...sums.map((r) => r.right)), calRight: cal.right, vw: innerWidth,
          docked: document.documentElement.classList.contains('sugbox-docked')};
}"""


def _open(page, w: int, h: int, collapsed: bool, path: str = _server.PAGES["schedule"]):
    page.set_viewport_size({"width": w, "height": h})
    page.add_init_script(f"localStorage.setItem('suggestions.collapsed', '{1 if collapsed else 0}')")
    page.goto(path)
    page.wait_for_selector(".sugbox:not([hidden]) .sg-head", timeout=30_000)
    page.wait_for_timeout(300)


@pytest.mark.parametrize("w,h", [(1280, 800), (1600, 900), (1000, 800)])
@pytest.mark.parametrize("collapsed", [False, True])
def test_the_week_sum_column_is_left_of_the_box(app, w, h, collapsed):
    page, _watch = app
    _open(page, w, h, collapsed)
    page.wait_for_selector(".wk .sum")
    g = page.evaluate(GEOM)
    assert g["docked"] and g["box"] and g["box"]["width"] > 0          # the box is still shown
    assert g["calRight"] <= g["box"]["left"], g                         # nothing of the calendar under it
    assert g["sumRight"] <= g["box"]["left"], g
    if not collapsed:
        assert g["box"]["height"] > 100                                  # open: the list shows


def test_the_box_floats_on_a_phone_and_other_pages_are_not_padded(app):
    page, _watch = app
    docked = "() => document.documentElement.classList.contains('sugbox-docked')"
    _open(page, 390, 844, True)
    assert not page.evaluate(docked)
    _open(page, 1280, 800, False, _server.PAGES["overview"])
    assert not page.evaluate(docked)
