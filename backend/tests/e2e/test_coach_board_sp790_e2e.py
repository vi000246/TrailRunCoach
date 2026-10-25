"""教練看板 (SP-790) in a real browser.

The e2e server runs in owner mode, where the board's routes are not mounted (api/coach_board.py: 404
without sign-in), so the page is opened by its file name (/api/v1/static/coach_board.html renders it
the same way) and GET /api/v1/coach/board answers from here, shaped like api/coach_board.py's. A row
per student with its volume, load, race, plan and alerts; a student without a summary yet; no students;
someone who is not a coach; at phone width nothing scrolls sideways, in Chinese and in English; the
settings 教練 tab links to the board. All names made up.

Run: ./e2e.sh -k coach_board (see conftest.py)."""
from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAGE = "/api/v1/static/coach_board.html"
TODAY = dt.date.today()
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(d: dt.date) -> str:
    return d.isoformat()


def _vol(km, secs, climb, n, tss):
    return {"sessions": n, "moving_s": secs, "distance_km": km, "climbing_m": climb, "tss": tss}


def board() -> dict:
    """Four made-up students: one with a red alert, one quiet and behind on the plan, one fine, one
    whose first summary is still being computed."""
    def summary(d7, d28, load, plan, last):
        return {"computed_at": (NOW - dt.timedelta(hours=3)).isoformat(), "day": _iso(TODAY), "volume": {"d7": d7, "d28": d28},
                "load": load, "plan": plan, "last_activity": _iso(last)}
    return {"used": 4, "max_students": 4, "pending": 1, "students": [
        {"student_id": 31, "name": "陳山羊", "since": "2026-09-02T05:00:00+00:00", "open_url": None,
         "summary": summary(_vol(68.4, 33900, 3810, 6, 612), _vol(221.0, 109800, 11200, 21, 2104),
                            {"ctl": 71.3, "atl": 104.8, "tsb": -38.6},
                            {"d28": {"due": 18, "done": 17, "rate": 0.944}, "week": {"planned": 6, "done": 5, "start": _iso(TODAY)}},
                            TODAY),
         "next_race": {"name": "示範山徑越野 50K", "date": _iso(TODAY + dt.timedelta(days=26)), "days": 1, "priority": "A",
                       "kind": "race", "distance_km": 50.0, "climbing_m": 3100, "in_days": 26},
         "last_sync": (NOW - dt.timedelta(hours=5)).isoformat(),
         "alerts": [{"id": "form", "level": "bad", "text": "負荷平衡：過度疲勞", "from": "status"},
                    {"id": "ramp", "level": "watch", "text": "長期負荷增幅", "from": "status"}],
         "summary_age_days": 0},
        {"student_id": 30, "name": "林小跑", "since": "2026-10-01T05:00:00+00:00", "open_url": None,
         "summary": summary(_vol(0, 0, 0, 0, 0), _vol(41.2, 16200, 620, 6, 388), {"ctl": 22.4, "atl": 9.1, "tsb": 14.0},
                            {"d28": {"due": 12, "done": 6, "rate": 0.5}, "week": {"planned": 4, "done": 0, "start": _iso(TODAY)}},
                            TODAY - dt.timedelta(days=9)),
         "next_race": {"name": "貓空 17K", "date": _iso(TODAY + dt.timedelta(days=61)), "days": 1, "priority": "B",
                       "kind": "race", "distance_km": 17.0, "climbing_m": 900, "in_days": 61},
         "last_sync": (NOW - dt.timedelta(days=9)).isoformat(),
         "alerts": [{"id": "no_sync", "level": "watch", "text": "9 天沒有同步", "from": "board"},
                    {"id": "quiet", "level": "watch", "text": "近 7 天沒有活動", "from": "board"},
                    {"id": "plan_low", "level": "watch", "text": "近 28 天課表完成 50%", "from": "board"}],
         "summary_age_days": 0},
        {"student_id": 33, "name": "王大衛", "since": "2026-10-10T05:00:00+00:00", "open_url": None,
         "summary": summary(_vol(42.0, 16800, 1450, 4, 330), _vol(160.5, 64800, 5200, 16, 1290),
                            {"ctl": 48.0, "atl": 51.2, "tsb": -3.2},
                            {"d28": {"due": 16, "done": 14, "rate": 0.875}, "week": {"planned": 5, "done": 3, "start": _iso(TODAY)}},
                            TODAY - dt.timedelta(days=1)),
         "next_race": None, "last_sync": (NOW - dt.timedelta(hours=20)).isoformat(), "alerts": [], "summary_age_days": 0},
        {"student_id": 34, "name": "張雲", "since": _iso(TODAY) + "T02:00:00+00:00", "open_url": None, "summary": None,
         "next_race": None, "last_sync": None,
         "alerts": [{"id": "never_synced", "level": "watch", "text": "還沒有同步過", "from": "board"}], "summary_age_days": None},
    ]}


def _open(page, body=None, status=200, lang=""):
    page.route("**/api/v1/coach/board", lambda r: r.fulfill(status=status, json=body if body is not None else board()))
    open_page(page, PAGE + lang)
    page.wait_for_load_state("networkidle")


def test_a_row_per_student_with_what_the_summary_has(app):
    page, _ = app
    _open(page)
    expect(page.locator("#cb-head")).to_contain_text("學員 4／4 位")
    expect(page.locator("#cb-head .cb-pending")).to_contain_text("1 位想加入")
    rows = page.locator("table.cb tbody tr")
    expect(rows).to_have_count(4)
    first = rows.nth(0)
    expect(first).to_contain_text("陳山羊")
    expect(first).to_contain_text("68.4 km")
    expect(first).to_contain_text("長期負荷")
    expect(first).to_contain_text("−39")                     # the load balance, signed
    expect(first).to_contain_text("示範山徑越野 50K")
    expect(first).to_contain_text("還有 26 天")
    expect(first).to_contain_text("94%")
    expect(first.locator(".cb-chip.bad")).to_have_text("負荷平衡：過度疲勞")
    expect(rows.nth(1).locator(".cb-chip.watch")).to_have_count(3)
    expect(rows.nth(3)).to_contain_text("還在算第一份彙總")
    expect(rows.nth(3)).to_contain_text("還沒同步過")
    expect(page.locator("table.cb a.cb-name")).to_have_count(0)   # SP-791 adds the link into a student


def test_no_students_and_not_a_coach(app):
    page, _ = app
    _open(page, {"used": 0, "max_students": 4, "pending": 0, "students": []})
    expect(page.locator("#cb-body")).to_contain_text("還沒有學員")
    expect(page.locator("#cb-body a")).to_have_attribute("href", "/api/v1/app/settings#coach")
    page2 = page.context.new_page()
    page2.route("**/api/v1/coach/board", lambda r: r.fulfill(status=403, json={"detail": {
        "code": "NOT_A_COACH", "message": "只有教練能用這個功能", "params": {}}}))
    open_page(page2, PAGE)
    expect(page2.locator("#cb-body")).to_contain_text("只有教練能看這一頁")


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_phone_width_does_not_scroll_sideways(app, lang):
    page, _ = app
    page.set_viewport_size({"width": 375, "height": 800})
    _open(page, lang="?lang=en" if lang == "en" else "")
    expect(page.locator("table.cb tbody tr")).to_have_count(4)
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], w
    if lang == "en":
        expect(page.locator("h1")).to_have_text("Coach dashboard")
        expect(page.locator("table.cb tbody tr").nth(0)).to_contain_text("in 26 days")
        expect(page.locator("table.cb tbody tr").nth(0)).to_contain_text("Chronic load")


def test_the_settings_coach_tab_links_to_the_board(app):
    from backend.tests.e2e.test_settings_coach_tab_sp789_e2e import COACH, _signed_in
    page, _ = app
    _signed_in(page, is_coach=True)
    page.route(r"**/api/v1/coach", lambda r: r.fulfill(json=COACH))
    open_page(page, _server.PAGES["settings"] + "#coach")
    page.wait_for_load_state("networkidle")
    expect(page.locator("#coach-side a[href='/api/v1/coach/board/page']")).to_be_visible()
