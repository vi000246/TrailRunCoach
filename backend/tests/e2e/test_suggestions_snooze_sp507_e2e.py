"""建議框「稍後提醒」 (SP-507) in a real browser on the demo athlete in an ordinary 基礎期 week (_server.base_phase, as
test_overview_test_row_sp503_e2e.py): every row has 稍後提醒 next to 不要 (English too); pressing it on a test row
takes the row out of the box — also after a reload — while the overview treats the test as still the box's (no
測試 to-do row, SP-483 / SP-503); 7 days later (the stored snooze moved back in time) the row is in the box again.

Run: ./e2e.sh -k sp507 (see conftest.py)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402

pytestmark = pytest.mark.e2e

TEST_TYPES = ("test", "baseline", "zone_test")
GET = "() => fetch('/api/v1/overview/plan/suggestions').then((r) => r.json())"
TODO_TEST = ("() => [...document.querySelectorAll('#actions > details.al > summary')]"
             ".some((e) => e.textContent.trim().startsWith('測試'))")


@pytest.fixture(scope="module")
def base_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-sp507")
    base = _server.build_base(root)
    _server.base_phase(base)
    srv = _server.Server(base, root / "uvicorn.log")
    yield srv
    srv.stop()


def age_snooze(base, sid: str, days: int) -> None:
    """Move the stored 稍後提醒 of `sid` back `days` days (as if pressed that long ago): the demo's DB file."""
    con = sqlite3.connect(base / "wko5coach.db", timeout=30)
    try:
        (raw,) = con.execute("SELECT value_json FROM user_settings WHERE key = 'plan.suggestions.dismissed'").fetchone()
        dis = json.loads(raw)
        v = dis[sid]
        at = dt.datetime.fromisoformat(v["at"]) - dt.timedelta(days=days)
        until = dt.date.fromisoformat(v["until"]) - dt.timedelta(days=days)
        dis[sid] = {**v, "at": at.isoformat(timespec="seconds"), "until": until.isoformat()}
        con.execute("UPDATE user_settings SET value_json = ? WHERE key = 'plan.suggestions.dismissed'", (json.dumps(dis),))
        con.commit()
    finally:
        con.close()


def _open(page, q=""):
    page.goto("/api/v1/overview/page" + q)
    page.wait_for_selector("#ind .ic, #ind details.ind-more")
    page.wait_for_selector(".sugbox .sg-item", timeout=30_000)
    page.wait_for_load_state("networkidle")
    if page.locator(".sugbox.collapsed").count():
        page.locator(".sugbox .sg-head").click()


def _ctx(e2e_browser, base_server, locale="zh-TW"):
    ctx = e2e_browser.new_context(base_url=base_server.url, viewport={"width": 1400, "height": 900},
                                  locale=locale, timezone_id="Asia/Taipei")
    ctx.set_default_timeout(60_000)
    return ctx


def test_every_row_has_remind_me_later_in_both_languages(e2e_browser, base_server):
    ctx = _ctx(e2e_browser, base_server)
    page = ctx.new_page()
    try:
        for q, word in (("", "稍後提醒"), ("?lang=en", "Remind me later")):
            _open(page, q)
            items = page.locator(".sugbox .sg-item")
            n = items.count()
            assert n >= 2
            for i in range(n):
                it = items.nth(i)
                kind = it.get_attribute("data-id")
                b = it.locator('[data-act="snooze"]')
                if kind.startswith("injury_done:"):
                    assert b.count() == 0
                    continue
                assert b.count() == 1 and b.inner_text().strip() == word, (kind, q)
        page.goto("/api/v1/overview/page?lang=zh-TW")            # the cookie back to Chinese for the next test
    finally:
        ctx.close()


def test_snooze_takes_the_test_out_and_brings_it_back_a_week_later(e2e_browser, base_server):
    ctx = _ctx(e2e_browser, base_server)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _open(page)
        rows = page.evaluate(GET)
        tests = [r for r in rows["suggestions"] if r["type"] in TEST_TYPES]
        assert tests, "the demo week has no test in the box"
        sid = tests[0]["id"]
        item = page.locator(f'.sugbox .sg-item[data-id="{sid}"]')
        item.locator('[data-act="snooze"]').click()
        page.wait_for_selector(f'.sugbox .sg-item[data-id="{sid}"] .sg-ok')       # 「好，10/21 再提醒你」
        item.wait_for(state="detached")
        got = page.evaluate(GET)
        assert sid not in [r["id"] for r in got["suggestions"]]
        assert [s["id"] for s in got["snoozed"]] == [sid] and got["declined"] == []
        # the overview: still the box's test (SP-483: no 測試 to-do row)
        assert not page.evaluate(TODO_TEST)
        # after a reload: still away
        _open(page)
        assert page.locator(f'.sugbox .sg-item[data-id="{sid}"]').count() == 0
        assert not page.evaluate(TODO_TEST)

        # 7 days later (the stored snooze moved back 7 days): in the box again
        age_snooze(base_server.base, sid, 7)
        _open(page)
        page.wait_for_selector(f'.sugbox .sg-item[data-id="{sid}"]')
        assert page.evaluate(GET)["snoozed"] == []
        assert errors == []
    finally:
        ctx.close()
