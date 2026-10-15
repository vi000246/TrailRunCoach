"""SP-641 on the demo athlete: 課表 › 路線 → 「從這條路線產生課」 — pick a day, see the steps the server
makes from the whole route (one step per climb), open the 課表 editor with them on that day, check the
chart and the watch preview, save it as a session of your own; no page-wide sideways scroll on a phone.

The rules (legs, merging, targets, the watch payload, the automatic plan keeping it) are covered by
backend/tests/test_route_session_sp641.py.

Run: ./e2e.sh -k route_session_sp641 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402
from backend.tests.e2e.test_route_library_sp640_e2e import PAGE, PAPI, upload  # noqa: E402

pytestmark = pytest.mark.e2e

CLIMB = re.compile(r"^爬 \d+ m · 約 \d+′$")


def _day(page, ahead=6):
    return page.evaluate(f"() => {{ const d = new Date(Date.now() + {ahead} * 864e5); "
                         "return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }")


def _panel(page, rid, day):
    page.goto("about:blank")                       # a hash-only change would not load the page
    open_page(page, f"{PAGE}#g{rid}")
    det = page.locator("#rl-det")
    expect(det.locator("svg.prof")).to_be_visible(timeout=60_000)
    det.locator("#rl-gen-btn").click()
    gen = det.locator("#rl-gen")
    expect(gen).to_be_visible()
    gen.locator("#rl-gen-day").fill(day)
    gen.locator("#rl-gen-day").dispatch_event("change")
    expect(gen.locator("ol li").first).to_be_visible(timeout=60_000)
    return gen


def test_make_a_session_from_a_route_and_save_it(app):
    page, _ = app
    page.goto(PAGE)
    r = upload(page)
    day = _day(page)
    gen = _panel(page, r["id"], day)
    items = gen.locator("ol li")
    n = items.count()
    assert 5 <= n <= 25
    expect(items.first).to_contain_text("爬 ")
    expect(gen).to_contain_text("Effort Pace")                          # a runnable climb's target on COROS
    expect(gen).to_contain_text("控制、安全")                            # a descent: no target
    gen.locator("#rl-gen-go").click()
    # the 課表 page's new-session dialog with the route's session on that day
    page.wait_for_url(re.compile(r"/schedule/page\?.*route=gpx"), timeout=60_000)
    dlg = page.locator("#sd")
    expect(dlg).to_be_visible(timeout=60_000)
    expect(page.locator("#sd-sub")).to_contain_text("從路線", timeout=60_000)
    assert page.locator('input[name="kind"][value="hike"]').is_checked()
    assert page.locator("#sd-form [name=day]").input_value() == day
    expect(page.locator("#sd-form [name=title]")).to_have_value(re.compile(r"^路線："))
    notes = page.locator("#we-list input.note")
    expect(notes.first).to_have_value(CLIMB)
    assert notes.count() == n
    expect(page.locator('#we-legend [data-ax="dist"]')).to_be_visible()   # the chart on the route
    page.locator("#we-watch summary").click()
    expect(page.locator("#we-wbody")).to_contain_text("爬 ")
    expect(page.locator("#we-rline")).to_contain_text("Maokong")
    # saved: a session of your own on that day, the route kept for the chart
    page.locator("#sd-save").click()
    expect(dlg).to_be_hidden(timeout=60_000)
    ss = page.evaluate("() => fetch('/api/v1/overview/plan/sessions').then((r) => r.json()).then((b) => b.sessions)")
    mine = [s for s in ss if s["day"] == day and (s.get("title") or "").startswith("路線：")]
    assert len(mine) == 1 and mine[0]["origin"] == "custom" and mine[0]["kind"] == "hike"
    assert mine[0]["steps"]["route"]["src"] == {"kind": "gpx", "id": str(r["id"])}
    assert CLIMB.match(mine[0]["steps"]["items"][0]["note"])


def test_phone_no_sideways_scroll(app, e2e_browser, e2e_server):
    page, _ = app
    page.goto(PAGE)
    r = upload(page)
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True, locale="zh-TW", timezone_id="Asia/Taipei")
    try:
        m = ctx.new_page()
        _panel(m, r["id"], _day(m))
        assert m.evaluate("document.documentElement.scrollWidth") <= 390
        m.locator("#rl-gen-go").click()
        expect(m.locator("#sd-sub")).to_contain_text("從路線", timeout=60_000)
        expect(m.locator("#we-list input.note").first).to_have_value(CLIMB)
        assert m.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        ctx.close()
    assert _server.PAGES["schedule"].endswith("/schedule/page") and PAPI
