"""教練看板 (SP-826) in a real browser: a student whose race or 課表 just changed (`refreshing`: the summary
is being computed again) says so under their name, in Chinese and in English; the others are as before.
GET /api/v1/coach/board answers from here (test_coach_board_sp790_e2e's made-up students).

Run: ./e2e.sh -k coach_board (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e.test_coach_board_sp790_e2e import _open, board  # noqa: E402

pytestmark = pytest.mark.e2e


def _refreshing() -> dict:
    b = board()
    b["students"][1]["refreshing"] = True                # 林小跑 just changed their schedule
    return b


def test_a_student_being_updated_says_so(app):
    page, _ = app
    _open(page, _refreshing())
    rows = page.locator("table.cb tbody tr")
    expect(rows.nth(1).locator(".cb-refreshing")).to_have_text("賽事或課表剛改過，更新中…")
    expect(page.locator(".cb-refreshing")).to_have_count(1)
    expect(rows.nth(0)).to_contain_text("最近活動")


def test_in_english(app):
    page, _ = app
    _open(page, _refreshing(), lang="?lang=en")
    expect(page.locator(".cb-refreshing")).to_have_text("Race or schedule just changed; updating…")
