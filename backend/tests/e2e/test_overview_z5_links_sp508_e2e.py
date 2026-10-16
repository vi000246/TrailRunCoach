"""5 區卡的測試連結 (SP-508) in a real browser on the demo athlete in an ordinary 基礎期 week (_server.base_phase, as
test_overview_test_row_sp503_e2e.py): while the suggestion box has the test, the 5 區 card's test links go to the box
(SP-483, R15); after 「不要」 (✕ where a row has no 不要) on every test row of the box, the card's test links are its
own 「安排課表」 again (the 課表 page's ?test= link) — at once and after a reload.

Run: ./e2e.sh -k sp508 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402

pytestmark = pytest.mark.e2e

TEST_TYPES = ("test", "baseline", "zone_test")

Z5_JS = r"""() => {
  const z = document.querySelector("#z5");
  return { toBox: z.querySelectorAll("[data-open-sugg]").length,
           tests: [...z.querySelectorAll("a.rd-gr, a.rd-sched")].filter((a) => /[?&]test=/.test(a.getAttribute("href"))).length,
           text: z.textContent.replace(/\s+/g, " ") };
}"""


@pytest.fixture(scope="module")
def base_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-sp508")
    base = _server.build_base(root)
    _server.base_phase(base)
    srv = _server.Server(base, root / "uvicorn.log")
    yield srv
    srv.stop()


def _open(page):
    page.goto("/api/v1/overview/page")
    page.wait_for_selector("#z5 .ru-tile")
    page.locator('#z5 .ru-tile[data-z="5"]').click()          # SP-678: the AeT test is on the 5 區 tile
    page.wait_for_selector(".sugbox .sg-item", timeout=30_000)
    page.wait_for_load_state("networkidle")


def test_declined_tests_give_the_5_zone_card_its_own_links(e2e_browser, base_server):
    ctx = e2e_browser.new_context(base_url=base_server.url, viewport={"width": 1400, "height": 900},
                                  locale="zh-TW", timezone_id="Asia/Taipei")
    ctx.set_default_timeout(60_000)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _open(page)
        rows = page.evaluate("() => fetch('/api/v1/overview/plan/suggestions').then((r) => r.json())")
        tests = [r for r in rows["suggestions"] if r["type"] in TEST_TYPES]
        assert tests, "the demo week has no test in the box: " + str([r["id"] for r in rows["suggestions"]])

        # 1. the box has the test: SP-483 — the card's test links go to the box
        before = page.evaluate(Z5_JS)
        assert before["toBox"] >= 1 and before["tests"] == 0, before

        # 2. 「不要」 (✕ when the row has no 不要) on every test row of the box
        if page.locator(".sugbox.collapsed").count():
            page.locator(".sugbox .sg-head").click()
        for r in tests:
            item = page.locator(f'.sugbox .sg-item[data-id="{r["id"]}"]')
            btn = item.locator('[data-act="decline"]')
            (btn if btn.count() else item.locator('[data-act="dismiss"]')).click()
            item.wait_for(state="detached")
        page.wait_for_function("() => document.querySelectorAll('#z5 [data-open-sugg]').length === 0")
        after = page.evaluate(Z5_JS)
        assert after["toBox"] == 0 and after["tests"] >= 1 and "建議框" not in after["text"], after

        # 3. stored on the server: the same after a reload
        page.reload()
        page.wait_for_selector("#z5 .ru-tile")
        page.locator('#z5 .ru-tile[data-z="5"]').click()
        page.wait_for_load_state("networkidle")
        page.wait_for_function("() => document.querySelectorAll('#z5 [data-open-sugg]').length === 0")
        again = page.evaluate(Z5_JS)
        assert again["toBox"] == 0 and again["tests"] == after["tests"], again
        assert errors == []
    finally:
        ctx.close()
