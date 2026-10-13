"""SP-412 in the browser: editing the demo's 「示範百岳 3 日縱走」 (a 3-day 百岳 saved without its
per-day numbers, like an event from before SP-114) — the day rows are optional and a hint asks for
them; ticking 會用登山杖 and 更新 saves it. Filling one cell makes every day required again, and
emptying it puts the trip's own totals back.

Run: ./e2e.sh -k sp412 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

EID = "demo-baiyue"
ROWS = "#dayplanrows input"


def _edit_old_trip(page):
    open_page(page, _server.PAGES["plan"])
    page.locator("#togglepast").click()
    page.locator(f'[data-edit="{EID}"]').click()
    expect(page.locator("#dayplanwrap")).to_be_visible()


def _event(page) -> dict:
    r = page.request.get("/api/v1/plan")
    assert r.ok
    return next(e for e in r.json()["events"] if e["id"] == EID)


def test_an_old_trip_without_day_numbers_is_hinted_and_saves(app):
    page, _ = app
    before = _event(page)
    assert before["day_plan_missing"] and not before["day_plan"]
    _edit_old_trip(page)
    expect(page.locator("#dayplanold")).to_be_visible()
    assert page.locator(f"{ROWS}[required]").count() == 0
    page.locator("#evform [name=poles]").check()
    page.locator("#evsubmit").click()
    expect(page.locator("text=已儲存")).to_be_visible()
    after = _event(page)
    assert after["poles"] is True and after["day_plan_missing"] is True
    assert after["distance_km"] == before["distance_km"] and after["climbing_m"] == before["climbing_m"]


def test_filling_one_cell_makes_every_day_required_again(app):
    page, _ = app
    _edit_old_trip(page)
    f = page.locator("#evform")
    km0 = f.locator("[name=distance_km]").input_value()
    cell = page.locator(f'{ROWS}[data-d="0"][data-k="km"]')
    cell.fill("12")
    assert page.locator(f"{ROWS}[required]").count() == 6               # km and climb of the 3 days
    assert f.locator("[name=distance_km]").input_value() == "12"
    page.locator("#evsubmit").click()                                  # the browser stops it: no PUT
    assert page.locator(f"{ROWS}:invalid").count() == 5
    cell.fill("")
    assert page.locator(f"{ROWS}[required]").count() == 0
    assert f.locator("[name=distance_km]").input_value() == km0         # the trip's own total is back


def test_a_new_multi_day_trip_still_requires_every_day(app):
    page, _ = app
    open_page(page, _server.PAGES["plan"])
    f = page.locator("#evform")
    f.locator("[name=kind]").select_option("baiyue")
    f.locator("[name=days]").fill("3")
    expect(page.locator("#dayplanwrap")).to_be_visible()
    expect(page.locator("#dayplanold")).to_be_hidden()
    assert page.locator(f"{ROWS}[required]").count() == 6
