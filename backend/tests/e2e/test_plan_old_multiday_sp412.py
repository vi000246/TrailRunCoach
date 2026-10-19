"""SP-412 in the browser: editing the demo's 「示範百岳 3 日縱走」 (a 3-day 百岳 saved without its
per-day numbers, like an event from before SP-114) — the day rows are optional and a hint asks for
them; ticking 會用登山杖 and 儲存 saves it (SP-691: in 賽事管理). Filling one cell makes every day required again, and
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


SAVE = "#ed-save"


# SP-691: the event editor moved from the season plan page to 賽事管理; the plan's 編輯 link opens it
# there (?event=<id>&edit=1), the same form and the same rules
def _edit_old_trip(page):
    open_page(page, f'{_server.PAGES["races"]}?event={EID}&edit=1')
    expect(page.locator("#ed")).to_be_visible()
    expect(page.locator("#dayplanwrap")).to_be_visible()


def _new_trip(page):
    open_page(page, _server.PAGES["races"])
    page.locator("#add").click()
    expect(page.locator("#ed")).to_be_visible()


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
    page.locator("#ed-adv").evaluate("d => { d.open = true; }")      # 會用登山杖 is under 進階
    page.locator("#evform [name=poles]").check()
    page.locator(SAVE).click()
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
    page.locator(SAVE).click()                                  # the browser stops it: no PUT
    assert page.locator(f"{ROWS}:invalid").count() == 5
    cell.fill("")
    assert page.locator(f"{ROWS}[required]").count() == 0
    assert f.locator("[name=distance_km]").input_value() == km0         # the trip's own total is back


def test_a_new_multi_day_trip_still_requires_every_day(app):
    page, _ = app
    _new_trip(page)
    f = page.locator("#evform")
    f.locator("[name=kind]").select_option("baiyue")
    f.locator("[name=days]").fill("3")
    expect(page.locator("#dayplanwrap")).to_be_visible()
    expect(page.locator("#dayplanold")).to_be_hidden()
    assert page.locator(f"{ROWS}[required]").count() == 6


# ---- SP-412 follow-up (decision 2026-10-13): the old trip's totals are editable ----

def _totals(page):
    f = page.locator("#evform")
    return f.locator("[name=distance_km]"), f.locator("[name=climbing_m]")


def _locked(loc) -> bool:
    return loc.evaluate("x => x.readOnly")


def test_an_old_trips_totals_can_be_edited_and_saved(app):
    page, _ = app
    before = _event(page)
    _edit_old_trip(page)
    km, gain = _totals(page)
    assert not _locked(km) and not _locked(gain)
    km.fill("41.5")
    gain.fill("4100")
    page.locator(SAVE).click()
    expect(page.locator("text=已儲存")).to_be_visible()
    after = _event(page)
    assert (after["distance_km"], after["climbing_m"]) == (41.5, 4100)
    assert after["day_plan_missing"] is True and not after["day_plan"]
    # put the demo trip's own totals back (one server for the whole run), through the same form
    _edit_old_trip(page)
    km.fill(str(before["distance_km"]))
    gain.fill(str(before["climbing_m"]))
    page.locator(SAVE).click()
    expect(page.locator("text=已儲存").first).to_be_visible()
    page.wait_for_function(f"""async () => {{
        const r = await fetch('/api/v1/plan'); const e = (await r.json()).events.find((x) => x.id === '{EID}');
        return e.distance_km === {before['distance_km']} && e.climbing_m === {before['climbing_m']}; }}""")


def test_filling_a_day_locks_the_totals_and_emptying_it_brings_back_the_typed_ones(app):
    page, _ = app
    _edit_old_trip(page)
    km, gain = _totals(page)
    km.fill("44")
    gain.fill("4200")
    cell = page.locator(f'{ROWS}[data-d="0"][data-k="km"]')
    cell.fill("12")
    assert _locked(km) and _locked(gain)                               # the sums of the days now
    assert km.input_value() == "12"
    cell.fill("")
    assert not _locked(km) and not _locked(gain)
    assert (km.input_value(), gain.input_value()) == ("44", "4200")    # what was typed, not the saved ones


def test_a_new_multi_day_trip_keeps_its_totals_read_only(app):
    page, _ = app
    _new_trip(page)
    f = page.locator("#evform")
    f.locator("[name=kind]").select_option("baiyue")
    f.locator("[name=days]").fill("3")
    expect(page.locator("#dayplanwrap")).to_be_visible()
    km, gain = _totals(page)
    assert _locked(km) and _locked(gain)


def test_a_trip_with_its_day_numbers_keeps_its_totals_read_only(app):
    page, _ = app
    days = [{"km": 12.0, "gain_m": 1500, "loss_m": None}, {"km": 14.0, "gain_m": 1300, "loss_m": None},
            {"km": 12.0, "gain_m": 1000, "loss_m": None}]

    def patch(route):                                                  # the demo trip as if it had them
        r = route.fetch()
        body = r.json()
        for e in body["events"]:
            if e["id"] == EID:
                e.update(day_plan=days, day_plan_missing=False)
        route.fulfill(response=r, json=body)

    page.route("**/api/v1/plan", patch)
    _edit_old_trip(page)
    expect(page.locator("#dayplanold")).to_be_hidden()
    km, gain = _totals(page)
    assert _locked(km) and _locked(gain)
    assert page.locator(f"{ROWS}[required]").count() == 6
