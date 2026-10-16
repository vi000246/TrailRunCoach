"""SP-514 in a real browser on the demo athlete in an ordinary 基礎期 week (_server.base_phase): after 稍後提醒 on every
test row of the box, the overview's 測試 card and 5 區 card say 「<day> 再提醒你」 (English too) and no longer point to
the box — at once and after a reload; 7 days later (the stored snoozes moved back in time) the tests are in the box
again and both cards link there as before (SP-483).

Run: ./e2e.sh -k sp514 (see conftest.py)."""
from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e.test_suggestions_snooze_sp507_e2e import GET, TEST_TYPES, _ctx, age_snooze  # noqa: E402

pytestmark = pytest.mark.e2e

CARDS_JS = r"""() => {
  const t = document.querySelector('#ind .ic[data-ind="testing"]'), z = document.querySelector("#z5");
  const vd = t && t.querySelector(".vd");
  return { card: vd ? vd.textContent.replace(/\s+/g, " ").trim() : null,
           cardToBox: t ? t.querySelectorAll("[data-open-sugg]").length : null,
           z5: [...z.querySelectorAll(".test-later")].map((e) => e.textContent.trim()),
           z5ToBox: z.querySelectorAll("[data-open-sugg]").length };
}"""


@pytest.fixture(scope="module")
def base_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-sp514")
    base = _server.build_base(root)
    _server.base_phase(base)
    srv = _server.Server(base, root / "uvicorn.log")
    yield srv
    srv.stop()


def _open(page, q=""):
    page.goto("/api/v1/overview/page" + q)
    page.wait_for_selector("#z5 .ru-tile")
    page.locator('#z5 .ru-tile[data-z="5"]').click()          # SP-678: the AeT test is on the 5 區 tile
    page.wait_for_selector('#ind .ic[data-ind="testing"]')
    page.wait_for_load_state("networkidle")


def test_held_tests_say_when_they_are_back(e2e_browser, base_server):
    ctx = _ctx(e2e_browser, base_server)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _open(page)
        page.wait_for_selector(".sugbox .sg-item", timeout=30_000)
        tests = [r for r in page.evaluate(GET)["suggestions"] if r["type"] in TEST_TYPES]
        assert tests, "the demo week has no test in the box"
        before = page.evaluate(CARDS_JS)
        assert before["cardToBox"] == 1 and before["z5ToBox"] >= 1 and before["z5"] == [], before    # SP-483

        if page.locator(".sugbox.collapsed").count():
            page.locator(".sugbox .sg-head").click()
        for r in tests:
            item = page.locator(f'.sugbox .sg-item[data-id="{r["id"]}"]')
            item.locator('[data-act="snooze"]').click()
            item.wait_for(state="detached")
        until = (dt.date.today() + dt.timedelta(days=7))
        day = f"{until.month}/{until.day}"
        want = f"建議做的測試 {day} 再提醒你"
        page.wait_for_function("() => document.querySelectorAll('#z5 .test-later').length > 0")
        now = page.evaluate(CARDS_JS)
        assert now["card"] == want and now["cardToBox"] == 0, now
        assert now["z5"] == [want] and now["z5ToBox"] == 0, now

        # after a reload (stored on the server), and in English
        _open(page)
        page.wait_for_function("() => document.querySelectorAll('#z5 .test-later').length > 0")
        assert page.evaluate(CARDS_JS) == now
        _open(page, "?lang=en")
        page.wait_for_function("() => document.querySelectorAll('#z5 .test-later').length > 0")
        en = page.evaluate(CARDS_JS)
        want_en = f"I'll remind you of the suggested test on {day}"
        assert en["card"] == want_en and en["z5"] == [want_en] and en["cardToBox"] == en["z5ToBox"] == 0, en
        page.goto("/api/v1/overview/page?lang=zh-TW")            # the cookie back to Chinese

        # 7 days later: back in the box, the cards link there again (SP-483)
        for r in tests:
            age_snooze(base_server.base, r["id"], 7)
        _open(page)
        page.wait_for_selector(".sugbox .sg-item", timeout=30_000)
        page.wait_for_function("() => document.querySelectorAll('#z5 [data-open-sugg]').length > 0")
        back = page.evaluate(CARDS_JS)
        assert back["cardToBox"] == 1 and back["z5ToBox"] >= 1 and back["z5"] == [], back
        assert errors == []
    finally:
        ctx.close()
