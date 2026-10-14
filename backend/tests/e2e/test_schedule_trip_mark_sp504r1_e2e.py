"""SP-504 R1: a 百岳 trip on the 課表 is a 行程, not a 賽 — in a real browser on the demo athlete (its C trip
「百岳 3 日（訓練行程）」, 3 days, kind baiyue, two weeks before today; its B race 貓空越野 17K stays a 賽).

Each day of the trip has the mark 「C 行程：百岳 3 日（訓練行程）」 (hover: 當訓練走…) in the week, month and list
views, the hike on it is 「行程：…」 (and its summary says so), English 「C trip: …」／「Trip: …」.

Run: ./e2e.sh -k sp504r1 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SCHEDULE = _server.PAGES["schedule"]
TRIP = "百岳 3 日（訓練行程）"
RACE = "貓空越野 17K"


def _ready(page, q=""):
    open_page(page, SCHEDULE + q)
    page.wait_for_selector("#cal .cal, #cal .agenda", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _trip(page) -> dict:
    r = page.evaluate("""async () => { const t = D.today;
      const b = await (await fetch(`${PAPI}/calendar?start=${addDays(t, -60)}&end=${t}`)).json();
      return (b.races || []).find((x) => x.kind === 'baiyue' && x.priority === 'C'); }""")
    assert r and r["name"] == TRIP and r["start"] < r["end"], r
    return r


def _show(page, day: str, view: str):
    page.evaluate("([d, v]) => { S.view = v; S.anchor = d; S.sel = d; load(); }", [day, view])
    page.wait_for_function("(d) => (D.races || []).some((r) => r.start <= d && d <= r.end)", arg=day, timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _days(r: dict) -> list[str]:
    import datetime as dt
    a, b = dt.date.fromisoformat(r["start"]), dt.date.fromisoformat(r["end"])
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


@pytest.mark.parametrize("view", ["week", "month"])
def test_the_trip_days_say_trip_not_race(app, view):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    r = _trip(page)
    _show(page, r["start"], view)
    for d in _days(r):
        mark = page.locator(f'.cell[data-day="{d}"] .rcm')
        expect(mark).to_have_text(f"C 行程：{TRIP}")
        tip = mark.get_attribute("title")
        assert "C 行程「" in tip and "當訓練走" in tip and "賽" not in tip.replace("賽季", ""), tip
    # the hike on the trip's days is the trip: 「行程：…」, not 「比賽：…」 nor 沒排課的活動
    chips = page.locator(".chip.act.race")
    assert chips.count() >= 1
    for i in range(chips.count()):
        expect(chips.nth(i).locator(".c-t")).to_have_text(f"行程：{TRIP}")
        assert chips.nth(i).get_attribute("title").startswith(f"行程（C 行程「{TRIP}」）")
    assert "比賽" not in page.locator("#cal").inner_text()


def test_the_trip_activity_summary_and_the_race_still_a_race(app):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    r = _trip(page)
    _show(page, r["start"], "week")
    page.locator(".chip.act.race").first.click()
    page.wait_for_selector("#ad[open]")
    expect(page.locator("#ad-h")).to_have_text(f"行程：{TRIP}")
    assert "這筆活動是行程本身" in page.locator("#ad-body").inner_text()
    page.keyboard.press("Escape")
    # the B race (kind race) keeps 「B 賽：X」
    b = page.evaluate("""async () => { const t = D.today;
      const b = await (await fetch(`${PAPI}/calendar?start=${t}&end=${addDays(t, 60)}`)).json();
      return (b.races || []).find((x) => x.priority === 'B'); }""")
    assert b["name"] == RACE and b["kind"] == "race"
    _show(page, b["day"], "week")
    expect(page.locator(f'.cell[data-day="{b["day"]}"] .rcm')).to_have_text(f"B 賽：{RACE}")


def test_the_list_view_and_english(app):
    page, _ = app
    page.set_viewport_size({"width": 390, "height": 844})       # the narrow (list) view
    _ready(page, "?lang=zh-TW")
    r = _trip(page)
    _show(page, r["start"], "week")
    expect(page.locator(f'.ag-day[data-day="{r["start"]}"] .rcm')).to_have_text(f"C 行程：{TRIP}")
    page.set_viewport_size({"width": 1400, "height": 900})
    _ready(page, "?lang=en")
    _show(page, r["start"], "week")
    mark = page.locator(f'.cell[data-day="{r["start"]}"] .rcm')
    expect(mark).to_have_text(f"C trip: {TRIP}")
    assert "C trip “" in mark.get_attribute("title") and "race" not in mark.get_attribute("title").lower()
    expect(page.locator(".chip.act.race .c-t").first).to_have_text(f"Trip: {TRIP}")
