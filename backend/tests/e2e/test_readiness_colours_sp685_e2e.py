"""SP-685 in a real browser on the demo athlete: the 賽前準備 card's 賽前第幾週 row has its own background (not the
item rows', the group rows' or the card's) and the 減量檢查 card is cyan, not purple — on the light and the dark
theme. The static side: backend/tests/test_readiness_colors_sp685.py. Run: ./e2e.sh -k sp685 (see conftest.py)."""
from __future__ import annotations

import urllib.parse

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

pytestmark = pytest.mark.e2e

PERIOD = urllib.parse.quote("周期化訓練")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_readiness_colours_both_themes(e2e_browser, e2e_server, scheme):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 1400, "height": 1200}, locale="zh-TW",
                                  timezone_id="Asia/Taipei", color_scheme=scheme)
    ctx.set_default_timeout(60_000)
    page = ctx.new_page()
    try:
        page.goto(f"/api/v1/app/viewer?view={PERIOD}&dash=2")
        page.wait_for_selector(".rd table.rd-cal")
        bg = page.evaluate("""() => {
          const c = (el) => getComputedStyle(el).backgroundColor;
          const head = document.querySelector('.rd-cal thead th.lab'), row = document.querySelector('.rd-cal tbody tr:not(.grp) td.lab');
          const grp = document.querySelector('.rd-cal tbody tr.grp td');
          return {head: c(head), row: c(row), grp: c(grp), panel: c(document.querySelector('.card:has(.rd)'))};
        }""")
        assert bg["head"] not in (bg["row"], bg["grp"], bg["panel"], "rgba(0, 0, 0, 0)"), bg
        page.goto(f"/api/v1/app/viewer?view={PERIOD}&dash=3")
        page.wait_for_selector(".rd .rd-count .big")
        rgb = page.evaluate("() => getComputedStyle(document.querySelector('.rd .rd-count .big')).color")
        r, g, b = [int(x) for x in rgb[rgb.index("(") + 1:rgb.index(")")].split(",")[:3]]
        assert b > r and g > r + 60, rgb                    # cyan (purple #7c3aed: red over green)
    finally:
        ctx.close()
