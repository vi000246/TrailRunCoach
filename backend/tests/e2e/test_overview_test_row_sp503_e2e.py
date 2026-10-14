"""總覽待辦的「測試」列 (SP-503) in a real browser on the demo athlete in an ordinary 基礎期 week (_server.base_phase,
as test_overview_hints_sp483_e2e.py): while the suggestion box has the test, no 測試 row in 待辦 and the 測試 card
links to the box (SP-483, R15); after 「不要」 (✕ where a row has no 不要) on every test row of the box, the 測試 row is
in 待辦 and the card no longer links to the box — also after a reload (the 「不要」 is stored on the server).

Run: ./e2e.sh -k sp503 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402

pytestmark = pytest.mark.e2e

TEST_TYPES = ("test", "baseline", "zone_test")

STATE_JS = r"""() => {
  const on = (el) => !!el && el.getClientRects().length > 0;
  const todo = [...document.querySelectorAll("#actions > details.al > summary")].filter(on)
    .map((e) => e.textContent.replace(/\s+/g, " ").trim());
  const card = document.querySelector('#ind [data-ind="testing"]');
  return { todo, card: !!card, level: card ? [...card.classList].find((c) => c !== "ic") : null,
           toBox: card ? card.querySelectorAll("[data-open-sugg]").length : 0,
           verdict: card && card.querySelector(".vd") ? card.querySelector(".vd").textContent.trim() : "" };
}"""


@pytest.fixture(scope="module")
def base_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-sp503")
    base = _server.build_base(root)
    _server.base_phase(base)
    srv = _server.Server(base, root / "uvicorn.log")
    yield srv
    srv.stop()


def _open(page):
    page.goto("/api/v1/overview/page")
    page.wait_for_selector("#ind .ic, #ind details.ind-more")
    page.wait_for_selector(".sugbox .sg-item", timeout=30_000)
    page.wait_for_load_state("networkidle")


def test_declined_test_comes_back_to_the_todo(e2e_browser, base_server):
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
        assert rows["declined"] == []

        # 1. the box has the test: SP-483 as it was
        before = page.evaluate(STATE_JS)
        assert before["card"] and before["level"] in ("watch", "bad"), before
        assert not any(t.startswith("測試") for t in before["todo"]), before
        assert before["toBox"] >= 1 and "建議框" in before["verdict"], before

        # 2. 「不要」 (✕ when the row has no 不要) on every test row of the box
        if page.locator(".sugbox.collapsed").count():
            page.locator(".sugbox .sg-head").click()
        for r in tests:
            item = page.locator(f'.sugbox .sg-item[data-id="{r["id"]}"]')
            btn = item.locator('[data-act="decline"]')
            (btn if btn.count() else item.locator('[data-act="dismiss"]')).click()
            item.wait_for(state="detached")
        page.wait_for_function("() => [...document.querySelectorAll('#actions > details.al > summary')]"
                               ".some((e) => e.textContent.trim().startsWith('測試'))")
        after = page.evaluate(STATE_JS)
        assert any(t.startswith("測試") for t in after["todo"]), after
        assert after["toBox"] == 0 and "建議框" not in after["verdict"], after

        # 3. stored on the server: the same after a reload
        page.reload()
        page.wait_for_selector("#ind .ic, #ind details.ind-more")
        page.wait_for_function("() => [...document.querySelectorAll('#actions > details.al > summary')]"
                               ".some((e) => e.textContent.trim().startsWith('測試'))")
        again = page.evaluate(STATE_JS)
        assert again["toBox"] == 0, again
        rows = page.evaluate("() => fetch('/api/v1/overview/plan/suggestions').then((r) => r.json())")
        assert {d["id"] for d in rows["declined"]} >= {r["id"] for r in tests}
        assert errors == []
    finally:
        ctx.close()
