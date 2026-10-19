"""SP-693 on the demo athlete: one 路線 page, two tabs — 我跑過的 (the segments found in the activities)
and 上傳的 (the uploaded GPX) — the old 課表 › 路線 URL landing on 上傳的, and 「用這條排課」 on 我跑過的
opening the 課表 editor with that segment's session.

The page rules are covered by backend/tests/test_routes_page_sp693.py.

Run: ./e2e.sh -k routes_page_sp693 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

RAN = _server.PAGES["routes"]
UPLOADED = _server.PAGES["routelib"]


def test_tabs_and_the_old_url(app):
    page, _ = app
    open_page(page, "/api/v1/overview/plan/routes/page")
    expect(page).to_have_url(re.compile(r"/api/v1/routes/uploaded/page$"))
    expect(page.locator('.rtabs a[aria-current="page"]')).to_contain_text("上傳的")
    expect(page.locator("#rl-mine")).to_have_count(0)
    page.locator(".rtabs a", has_text="我跑過的").click()
    page.wait_for_url(re.compile(r"/api/v1/routes/page"))
    expect(page.locator('.rtabs a[aria-current="page"]')).to_contain_text("我跑過的")
    expect(page.locator("#list .row").first).to_be_visible(timeout=120_000)


def test_plan_a_session_on_a_segment_run_before(app):
    page, _ = app
    open_page(page, RAN)
    row = page.locator("#list .row").first
    expect(row).to_be_visible(timeout=120_000)
    rid = row.get_attribute("data-id")
    row.click()
    btn = page.locator("#gen-btn")
    expect(btn).to_have_text(re.compile("用這條排課"))
    btn.click()
    gen = page.locator("#gen")
    expect(gen).to_be_visible()
    expect(gen.locator("ol li").first).to_be_visible(timeout=60_000)
    gen.locator("#gen-go").click()
    page.wait_for_url(re.compile(r"/schedule/page\?.*route=seg"), timeout=60_000)
    assert rid in page.url.replace("%3A", ":")
