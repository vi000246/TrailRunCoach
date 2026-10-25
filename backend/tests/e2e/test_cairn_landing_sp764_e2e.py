"""A route brought from 疊石 Cairn (SP-764) in a real browser on the demo athlete (owner mode).

The calculator's ?cairn=<share link>&from=cairn lands on 賽事管理 with 新增賽事 filled from the Cairn trip
(the preview answered here: no Cairn on the network) and a line saying what 「儲存」 does; saving makes the
race, imports the link (answered here too) and opens the calculator on that race with the
「已從疊石 Cairn 帶入」 line — and, with no run or hike recorded (first=1), the line about the estimate.

Run: ./e2e.sh -k cairn_landing (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

LINK = "https://map.yichlin.com/?gpx=5f0c2a8e-1b7d-4c1e-9a2f-0d3e4b5c6a71&share=0123456789abcdef0123456789abcdef"
PREVIEW = {"name": "嘉明湖三天", "start_date": "2027-03-01", "days": 3, "nights": 2, "event_id": None,
           "has_records": False}


def test_a_cairn_link_becomes_a_race_then_the_calculator(app):
    page, _ = app
    page.route("**/api/v1/app/xp/cairn/route", lambda r: r.fulfill(json=PREVIEW))
    page.route("**/api/v1/plan/events/*/gpx/cairn",
               lambda r: r.fulfill(json={"gpx": None, "days": None, "event_days": 3, "event": None, "cairn": {}}))
    resp = page.goto(_server.PAGES["racepower"] + "?" + "from=cairn&cairn=" + LINK.replace("&", "%26"),
                     wait_until="load")
    assert resp is not None and resp.status == 200
    assert "/api/v1/app/races/page" in page.url
    expect(page.locator("#ed")).to_be_visible()
    assert page.evaluate("location.search") == ""                    # a reload won't bring the link again
    expect(page.locator("#ed-name")).to_have_value("嘉明湖三天")
    expect(page.locator("#ed-date")).to_have_value("2027-03-01")
    expect(page.locator("#ed-kind")).to_have_value("baiyue")
    expect(page.locator("#ed-days")).to_have_value("3")
    expect(page.locator("#evcairn")).to_have_value(LINK)
    expect(page.locator("#ci-msg")).to_contain_text("從疊石 Cairn 帶來「嘉明湖三天」（3 天）")
    expect(page.locator("#xp-cairn-ed")).to_be_hidden()              # came from Cairn: no Cairn introduction
    page.locator("#ed-save").click()
    page.wait_for_url("**/api/v1/racepower/page?*", timeout=30_000)
    eid = page.evaluate("new URLSearchParams(location.search).get('event')")
    try:
        assert eid and "from=cairn" in page.url and "first=1" in page.url
        line = page.locator("#from-cairn")
        expect(line).to_be_visible(timeout=60_000)
        expect(line).to_contain_text("已從疊石 Cairn 帶入「嘉明湖三天」")
        expect(line).to_contain_text("還沒有手錶紀錄")
    finally:
        page.evaluate("(id) => fetch('/api/v1/plan/events/' + encodeURIComponent(id), { method: 'DELETE' })", eid)


def test_a_route_already_a_race_opens_the_calculator(app):
    page, _ = app
    page.route("**/api/v1/app/xp/cairn/route", lambda r: r.fulfill(json={**PREVIEW, "event_id": "nope"}))
    page.goto(_server.PAGES["races"] + "?cairn=x&from=cairn", wait_until="load")
    page.wait_for_url("**/api/v1/racepower/page?event=nope&from=cairn", timeout=30_000)
    open_page(page, page.url)
    expect(page.locator("#from-cairn")).to_be_hidden()               # no such race: nothing claimed
