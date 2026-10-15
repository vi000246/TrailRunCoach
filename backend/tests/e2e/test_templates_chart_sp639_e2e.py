"""SP-639 on the demo athlete: a hill-repeat session on the demo's 貓空 17K GPX — the step chart's
距離 | 時間 switch, a hill repeat drawn up and back (the tooltip's climb and time), the 坡段表, and
no page-wide horizontal scroll on a phone. SP-640: the route is picked for the session (課表 ›
路線), not kept on the template, so the chart is the 課表 editor's and the 坡段表 the routes page's.

The numbers (Minetti, the repeat walk, the climb thresholds) are covered by
backend/tests/test_user_templates_sp639.py.

Run: ./e2e.sh -k templates_chart_sp639 (see conftest.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402

pytestmark = pytest.mark.e2e

PAGE = _server.PAGES["schedule"]
PAPI = "/api/v1/overview/plan"
GPX = (Path(__file__).resolve().parents[2] / "demo" / "data" / "maokong_17k.gpx").read_bytes()


def st(kind, sec, target=None, note=""):
    return {"kind": kind, "dur": {"type": "time", "value": sec}, "target": target or {"type": "none"}, "note": note}


HILL = {"items": [
    st("warm", 12 * 60, None, "暖身到坡底"),
    {"kind": "repeat", "times": 4, "last_rest": True, "items": [
        st("work", 7 * 60, {"type": "hr", "mode": "pct", "lo": 0.95, "hi": 1.02}, "上坡"),
        st("rest", 7 * 60, None, "走下來")]},
    st("cool", 5 * 60, None, "收操")]}


def _template(page) -> tuple[str, int]:
    """(a hike session on the route, the route's id): the route uploaded on 課表 › 路線, put on the
    structure (POST /steps/route: the climbs to the top), the session saved with it."""
    r = page.request.post(f"{PAPI}/routes/library", multipart={"file": {"name": "maokong_17k.gpx",
                                                                       "mimeType": "application/gpx+xml", "buffer": GPX}})
    assert r.ok, r.text()
    rid = r.json()["id"]
    a = page.request.post(f"{PAPI}/steps/route", data={"kind": "hike", "steps": HILL, "route": {"src": "gpx", "id": rid}})
    assert a.ok, a.text()
    day = page.evaluate("() => new Date(Date.now() + 6 * 864e5).toISOString().slice(0, 10)")
    s = page.request.post(f"{PAPI}/sessions", data={"day": day, "kind": "hike", "title": "SP-639 登山王", "minutes": 90,
                                                    "steps": a.json()["steps"]})
    assert s.ok, s.text()
    return s.json()["uid"], rid


def _open(page, uid):
    page.goto(PAGE)
    # the session's week (a phone shows fewer days): S.anchor on its day, then wait for it
    page.wait_for_function("() => typeof D !== 'undefined' && D && D.sessions", timeout=120_000)
    day = page.evaluate("(u) => fetch('/api/v1/overview/plan/sessions').then((r) => r.json())"
                        ".then((b) => b.sessions.find((s) => s.uid === u).day)", uid)
    page.evaluate("([u, d]) => { if (!findS(u)) { S.anchor = d; S.sel = d; load(); } }", [uid, day])
    page.wait_for_function("(u) => findS(u)", arg=uid, timeout=120_000)
    page.evaluate("(u) => openDlg(findS(u))", uid)
    page.wait_for_selector("#we-svg rect.blk", state="attached", timeout=120_000)
    page.evaluate("document.getElementById('we-box').open = true")


def test_axis_switch_tooltip_and_segment_table(app):
    page, _ = app
    page.goto(PAGE)
    uid, rid = _template(page)
    _open(page, uid)
    sw = page.locator("#we-legend [data-ax]")
    expect(sw).to_have_count(2)
    expect(page.locator('#we-legend [data-ax="dist"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#we-svg text", has_text="km").first).to_be_attached()
    page.click('#we-legend [data-ax="time"]')
    expect(page.locator('#we-legend [data-ax="time"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#we-svg text", has_text="′").first).to_be_attached()
    # kept per viewer
    _open(page, uid)
    expect(page.locator('#we-legend [data-ax="time"]')).to_have_attribute("aria-pressed", "true")
    page.click('#we-legend [data-ax="dist"]')
    # the 2nd climb's tooltip: climb, grade, time, route km
    page.locator("#we-svg rect.blk").nth(3).hover()
    tip = page.locator("#we-tip")
    expect(tip).to_be_visible()
    expect(tip).to_contain_text("爬升")
    expect(tip).to_contain_text("平均坡度")
    expect(tip).to_contain_text("路線")
    # the drawn profile: one area per stretch on the route
    assert page.locator("#we-svg path[fill='var(--we-elev)']").count() >= 1
    # the 坡段表: on the routes page (SP-640)
    page.goto(f"{_server.PAGES['routelib']}#g{rid}")
    crs = page.locator("#rl-det .crs")
    expect(crs).to_be_visible(timeout=60_000)
    assert crs.locator("tr.climb").count() >= 1 and crs.locator("tbody tr").count() > 10


def test_phone_tap_and_no_page_scroll(app, e2e_browser, e2e_server):
    page, _ = app
    page.goto(PAGE)
    uid, _rid = _template(page)
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True, locale="zh-TW", timezone_id="Asia/Taipei")
    try:
        m = ctx.new_page()
        _open(m, uid)
        m.locator("#we-svg rect.blk").nth(1).tap()
        expect(m.locator("#we-tip")).to_be_visible()
        expect(m.locator("#we-tip")).to_contain_text("爬升")
        assert m.evaluate("document.documentElement.scrollWidth") <= 390
    finally:
        ctx.close()
