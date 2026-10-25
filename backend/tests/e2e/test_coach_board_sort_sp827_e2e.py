"""教練看板 (SP-827) in a real browser: the order (警示 — the server's, the default — 賽事日期, 課表完成度) and
「只看有紅燈的」, kept per viewer (localStorage) for the next visit; nobody red says so; at phone width
nothing scrolls sideways; English. GET /api/v1/coach/board answers from here (test_coach_board_sp790_e2e's
made-up students, 王大衛 given a race in 10 days so each order differs).

Run: ./e2e.sh -k coach_board (see conftest.py)."""
from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e._browser import open_page  # noqa: E402
from backend.tests.e2e.test_coach_board_sp790_e2e import PAGE, TODAY, _open, board  # noqa: E402

pytestmark = pytest.mark.e2e


def _board() -> dict:
    b = board()
    b["students"][2]["next_race"] = {"name": "東眼山 25K", "date": (TODAY + dt.timedelta(days=10)).isoformat(), "days": 1,
                                     "priority": "C", "kind": "race", "distance_km": 25.0, "climbing_m": 1500,
                                     "in_days": 10}
    return b


def _names(page) -> list[str]:
    return page.locator("table.cb tbody tr .cb-name").all_inner_texts()


def _sort(page, value: str) -> None:
    page.select_option("#cb-sort", value)


def test_the_three_orders(app):
    page, _ = app
    _open(page, _board())
    expect(page.locator("#cb-sort")).to_have_value("alerts")
    assert _names(page) == ["陳山羊", "林小跑", "王大衛", "張雲"]          # the server's: the red first
    _sort(page, "race")
    assert _names(page) == ["王大衛", "陳山羊", "林小跑", "張雲"]          # 10, 26, 61 days; no race last
    _sort(page, "plan")
    assert _names(page) == ["林小跑", "王大衛", "陳山羊", "張雲"]          # 50 %, 88 %, 94 %; no plan last
    _sort(page, "alerts")
    assert _names(page) == ["陳山羊", "林小跑", "王大衛", "張雲"]


def test_red_only_and_nobody_red(app):
    page, _ = app
    _open(page, _board())
    expect(page.locator("label.cb-toggle")).to_contain_text("只看有紅燈的（1）")
    page.check("#cb-red")
    assert _names(page) == ["陳山羊"]
    page.uncheck("#cb-red")
    expect(page.locator("table.cb tbody tr")).to_have_count(4)
    b = _board()
    b["students"] = [s for s in b["students"] if not any(a["level"] == "bad" for a in s["alerts"])]
    page2 = page.context.new_page()
    _open(page2, b)
    page2.check("#cb-red")
    expect(page2.locator("#cb-body")).to_contain_text("目前沒有學員有紅燈警示")
    expect(page2.locator("table.cb")).to_have_count(0)


def test_the_choice_is_kept_for_the_next_visit(app):
    page, _ = app
    _open(page, _board())
    _sort(page, "plan")
    page.check("#cb-red")
    open_page(page, PAGE)
    page.wait_for_load_state("networkidle")
    expect(page.locator("#cb-sort")).to_have_value("plan")
    expect(page.locator("#cb-red")).to_be_checked()
    assert _names(page) == ["陳山羊"]
    page.uncheck("#cb-red")
    assert _names(page) == ["林小跑", "王大衛", "陳山羊", "張雲"]
    page.evaluate("() => localStorage.setItem('coach_board.view', '{\"sort\": \"bogus\", \"red\": 1}')")
    open_page(page, PAGE)
    page.wait_for_load_state("networkidle")
    expect(page.locator("#cb-sort")).to_have_value("alerts")             # anything else: the default
    expect(page.locator("#cb-red")).not_to_be_checked()
    page.evaluate("() => localStorage.removeItem('coach_board.view')")   # the next test starts from the default


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_phone_width_and_english(app, lang):
    page, _ = app
    page.set_viewport_size({"width": 375, "height": 800})
    _open(page, _board(), lang="?lang=en" if lang == "en" else "")
    page.check("#cb-red")
    expect(page.locator("table.cb tbody tr")).to_have_count(1)
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], w
    if lang == "en":
        expect(page.locator(".cb-tools")).to_contain_text("Sort")
        expect(page.locator("#cb-sort option")).to_have_text(["Most alerts first", "Nearest race first",
                                                               "Lowest completion first"])
        expect(page.locator("label.cb-toggle")).to_contain_text("Red alerts only (1)")
