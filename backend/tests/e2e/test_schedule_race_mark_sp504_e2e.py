"""SP-504: a B / C race day on the 課表, in a real browser on the demo athlete (its B race 貓空越野 17K).

The day holds no generated 「B 賽事：…」 card any more; the race is a mark on the day (「B 賽：貓空越野 17K」,
like the A race's phase label) in the week, month and list views, in Chinese and English, and its
predicted load is in the week's 計畫.

Run: ./e2e.sh -k sp504 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SCHEDULE = _server.PAGES["schedule"]
RACE = "貓空越野 17K"


def _ready(page, q=""):
    open_page(page, SCHEDULE + q)
    page.wait_for_selector("#cal .cal, #cal .agenda", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _race_day(page) -> str:
    """The demo B race's day (the calendar of the next 8 weeks)."""
    r = page.evaluate("""async () => { const t = D.today, e = addDays(t, 60);
      const b = await (await fetch(`${PAPI}/calendar?start=${t}&end=${e}`)).json();
      return (b.races || []).find((x) => x.priority === 'B'); }""")
    assert r and r["name"] == RACE, r
    return r["day"]


def _show(page, day: str, view: str):
    page.evaluate("([d, v]) => { S.view = v; S.anchor = d; S.sel = d; load(); }", [day, view])
    page.wait_for_function("(d) => (D.races || []).some((r) => r.day === d)", arg=day, timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _no_race_card(page, day: str):
    assert page.evaluate("(d) => D.sessions.filter((s) => s.day === d && s.kind === 'race' && s.origin === 'auto')",
                         day) == []


@pytest.mark.parametrize("view", ["week", "month"])
def test_the_b_race_day_is_a_mark_not_a_card(app, view):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    day = _race_day(page)
    _show(page, day, view)
    _no_race_card(page, day)
    mark = page.locator(f'.cell[data-day="{day}"] .rcm')
    expect(mark).to_have_text(f"B 賽：{RACE}")
    assert "以賽代訓" in mark.get_attribute("title")
    # not a card: nothing to drag, nothing on the day's chips but what the user put there
    assert page.locator(f'.cell[data-day="{day}"] .chip[data-uid]').count() == 0
    assert page.locator(f'.cell[data-day="{day}"] .rest-mv').count() == 0        # nor a rest day to move
    # the week's 計畫 counts the race's predicted load (the mark's TSS, counted)
    r = page.evaluate("(d) => D.races.find((x) => x.day === d)", day)
    assert r["counted"] and r["tss"] > 0


def test_the_list_view_and_english(app):
    page, _ = app
    page.set_viewport_size({"width": 390, "height": 844})       # the narrow (list) view
    _ready(page, "?lang=zh-TW")
    day = _race_day(page)
    _show(page, day, "week")
    expect(page.locator(f'.ag-day[data-day="{day}"] .rcm')).to_have_text(f"B 賽：{RACE}")
    assert page.locator(f'.ag-day[data-day="{day}"] .rest, .ag-day[data-day="{day}"] .rest-mv').count() == 0
    page.set_viewport_size({"width": 1400, "height": 900})
    _ready(page, "?lang=en")
    _show(page, day, "week")
    mark = page.locator(f'.cell[data-day="{day}"] .rcm')
    expect(mark).to_have_text(f"B race: {RACE}")
    assert "nothing is scheduled that day" in mark.get_attribute("title")
