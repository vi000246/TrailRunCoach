"""SP-517: the 總覽 「本週」 list shows the week's races and 百岳 trips as a mark on the day — written as on the
課表's day cell (SP-504 / R1: 「B 賽：X」, a trip 「C 行程：X」), not a card — in a real browser on the demo athlete.

The demo's own week has no race (its B race 貓空越野 17K is ~10 days out, its C trip 「百岳 3 日（訓練行程）」 two
weeks back), so the list is first checked as is (no mark, as before), then pointed at those weeks: PLAN.week (the
/weekplan answer) is moved there and the done activities taken from that week's /calendar answer, and loadPlan()
redraws the list from the real /calendar of that week (its `races`, sessions and tagged activities).

Run: ./e2e.sh -k sp517 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

OVERVIEW = _server.PAGES["overview"]
TRIP = "百岳 3 日（訓練行程）"
RACE = "貓空越野 17K"

# move the 「本週」 list to the week of `day` (see the module doc): the real /calendar of that week
MOVE_JS = """async (day) => {
  const ws = (() => { const d = new Date(day + "T00:00:00"); d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    return isoOf(d); })(), we = addDays(ws, 6);
  const cal = await (await fetch(`${PAPI}/calendar?start=${ws}&end=${we}`)).json();
  PLAN.week = { ...PLAN.week, start: ws, end: we };
  PLAN.done = { ...PLAN.done, activities: cal.activities.map(({ race, ...a }) => a) };
  await loadPlan();
  return ws; }"""


def _ready(page, q=""):
    open_page(page, OVERVIEW + q)
    page.wait_for_selector("#wk-days .dc", timeout=60_000)
    page.wait_for_function("() => typeof SP !== 'undefined' && SP && SP.races", timeout=60_000)


def _race(page, pred: str) -> dict:
    return page.evaluate(f"""async () => {{ const t = SP.today;
      const b = await (await fetch(`${{PAPI}}/calendar?start=${{addDays(t, -60)}}&end=${{addDays(t, 60)}}`)).json();
      return (b.races || []).find((x) => {pred}); }}""")


def _day(page, iso: str):
    return page.locator("#wk-days .dc").nth(page.evaluate("([a, b]) => Math.round((new Date(b) - new Date(a)) / 864e5)",
                                                         [page.evaluate("() => PLAN.week.start"), iso]))


def test_a_week_without_a_race_is_as_before(app):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    assert page.evaluate("() => SP.races") == []
    assert page.locator("#wk-days .rcm").count() == 0
    assert page.locator("#wk-days .sx.race").count() == 0
    assert page.locator("#wk-days .dc").count() == 7


def test_the_b_race_day_has_the_mark_not_a_card(app):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    r = _race(page, "x.priority === 'B'")
    assert r and r["name"] == RACE and r["kind"] == "race", r
    page.evaluate(MOVE_JS, r["day"])
    day = _day(page, r["day"])
    mark = day.locator(".rcm")
    expect(mark).to_have_text(f"B 賽：{RACE}")
    tip = mark.get_attribute("data-tip")
    assert tip.startswith(f"B 賽「{RACE}」：以賽代訓") and "預估負荷" in tip, tip
    assert day.locator(".sx").count() == 0 and day.locator(".rest").count() == 0     # not a card, no 休息
    assert page.locator("#wk-days .rcm").count() == 1                                   # only that day


def test_the_trip_days_and_their_activities_say_trip(app):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    r = _race(page, "x.kind === 'baiyue' && x.priority === 'C'")
    assert r and r["name"] == TRIP and r["start"] < r["end"], r
    page.evaluate(MOVE_JS, r["start"])
    marks = page.locator("#wk-days .rcm")
    assert marks.count() == 3                                                            # each day of the trip
    for i in range(3):
        expect(marks.nth(i)).to_have_text(f"C 行程：{TRIP}")
        assert marks.nth(i).get_attribute("data-tip").startswith(f"C 行程「{TRIP}」：當訓練走")
    acts = page.locator("#wk-days .sx.act.race")
    assert acts.count() >= 1
    for i in range(acts.count()):
        expect(acts.nth(i).locator(".tl")).to_have_text(f"行程：{TRIP}")
        # the trip, not 「課表外：…」 (an activity outside the plan)
        assert acts.nth(i).get_attribute("data-tip").startswith(f"行程（C 行程「{TRIP}」）：配到賽季計畫的行程")
    assert "比賽" not in page.locator("#wk-days").inner_text()


def test_english(app):
    page, _ = app
    _ready(page, "?lang=en")
    b = _race(page, "x.priority === 'B'")
    page.evaluate(MOVE_JS, b["day"])
    expect(_day(page, b["day"]).locator(".rcm")).to_have_text(f"B race: {RACE}")
    t = _race(page, "x.kind === 'baiyue' && x.priority === 'C'")
    page.evaluate(MOVE_JS, t["start"])
    expect(page.locator("#wk-days .rcm").first).to_have_text(f"C trip: {TRIP}")
    assert "nothing is scheduled that day" in page.locator("#wk-days .rcm").first.get_attribute("data-tip")
    expect(page.locator("#wk-days .sx.act.race .tl").first).to_have_text(f"Trip: {TRIP}")
