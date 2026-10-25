"""SP-770 in a real browser on the demo athlete: the 傷病紀錄 timeline's right-hand axis name 「長期負荷」 is drawn
whole — its box ends inside the SVG — on the desktop and the phone width (a fixed 34 px right margin cut it to
「長期負」). Run: ./e2e.sh -k sp770 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

BOX_JS = """() => {
  const svg = document.querySelector('#tl svg');
  const label = [...svg.querySelectorAll('text')].find((t) => t.textContent === '長期負荷');
  const b = label.getBBox();
  return {right: b.x + b.width, width: +svg.getAttribute('width'), text: label.getComputedTextLength(), chars: label.getNumberOfChars()};
}"""


@pytest.mark.parametrize("width", [1400, 390])
def test_ctl_axis_name_not_cut(e2e_browser, e2e_server, width):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": width, "height": 900}, locale="zh-TW",
                                  timezone_id="Asia/Taipei")
    ctx.set_default_timeout(60_000)
    page = ctx.new_page()
    try:
        open_page(page, "/api/v1/app/injuries/page")
        page.wait_for_selector("#tl svg")
        box = page.evaluate(BOX_JS)
        assert box["chars"] == 4, box
        assert box["right"] <= box["width"], box          # the whole label inside the SVG, not clipped at its edge
    finally:
        ctx.close()
