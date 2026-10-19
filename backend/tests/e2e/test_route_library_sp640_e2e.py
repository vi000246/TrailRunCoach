"""SP-640 on the demo athlete: 課表 › 路線 — the uploaded GPX routes and 「我的路段」 on their own page
(the profile with the climbs, the 坡段表, rename), the 範本 page's box pointing there, and the route
picked in the 課表 editor: each hill repeat's climb to the top with the lap button, named
「爬 … m · 約 …′」, the climb changeable; no page-wide sideways scroll on a phone.

The rules (the climb pick, the lap step, the watch payload, tenants) are covered by
backend/tests/test_route_library_sp640.py.

Run: ./e2e.sh -k route_library_sp640 (see conftest.py)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAGE = _server.PAGES["routelib"]
PAPI = "/api/v1/overview/plan"
LAPI = f"{PAPI}/routes/library"
GPX = (Path(__file__).resolve().parents[2] / "demo" / "data" / "maokong_17k.gpx").read_bytes()


def st(kind, dur, target=None, note=""):
    d = {"type": "time", "value": dur} if isinstance(dur, int) else dur
    return {"kind": kind, "dur": d, "target": target or {"type": "none"}, "note": note}


HILL = {"items": [
    st("warm", 12 * 60, None, "暖身到坡底"),
    {"kind": "repeat", "times": 4, "last_rest": True, "items": [
        st("work", 7 * 60, {"type": "hr", "mode": "pct", "lo": 0.95, "hi": 1.02}, "上坡"),
        st("rest", 7 * 60, None, "走下來")]},
    st("cool", 5 * 60, None, "收操")]}


def upload(page, name="maokong_17k.gpx") -> dict:
    r = page.request.post(LAPI, multipart={"file": {"name": name, "mimeType": "application/gpx+xml", "buffer": GPX}})
    assert r.ok, r.text()
    return r.json()


def test_routes_page_profile_table_rename(app):
    page, _ = app
    page.goto(PAGE)
    r = upload(page)
    open_page(page, PAGE)
    btn = page.locator(f'#rl-gpx button[data-g="{r["id"]}"]')
    expect(btn).to_be_visible(timeout=60_000)
    expect(page.locator("#rl-mine")).to_have_count(0)                  # SP-693: listed on 我跑過的 only
    expect(page.locator('.rtabs a[aria-current="page"]')).to_have_attribute("href", PAGE)
    btn.click()
    det = page.locator("#rl-det")
    expect(det.locator("svg.prof")).to_be_visible(timeout=60_000)
    assert det.locator("svg.prof rect.cl").count() >= 1                # the climbs marked on the profile
    assert det.locator("tr.climb").count() >= 1
    expect(det.locator("tr.climb").first).to_contain_text("爬 ")       # the step name a climb gets
    # rename
    det.locator("#rl-name").fill("貓空練習")
    det.locator("#rl-ren").click()
    expect(page.locator("#rl-msg")).to_contain_text("貓空練習")
    expect(btn).to_contain_text("貓空練習")
    assert re.search(r"#g\d+$", page.url)


def test_templates_page_points_to_the_routes(app):
    page, _ = app
    page.goto(PAGE)
    uapi = f"{PAPI}/steps/templates/user"
    t = page.request.post(uapi, data={"name": "SP-640 舊範本", "cats": ["trail"], "steps": HILL}).json()
    r = page.request.post(f"{uapi}/{t['id']}/gpx", multipart={"file": {"name": "old.gpx", "mimeType": "application/gpx+xml",
                                                                    "buffer": GPX}})
    assert r.ok, r.text()
    open_page(page, f"{_server.PAGES['templates']}#{t['id']}")
    box = page.locator("#tm-gpx")
    expect(box).to_contain_text("old.gpx", timeout=60_000)
    expect(box.locator("a")).to_have_attribute("href", re.compile(r"/routes/uploaded/page#g\d+$"))
    expect(page.locator("#we-route")).to_be_hidden()                    # a template: structure only


def _session(page, day: str) -> str:
    r = page.request.post(f"{PAPI}/sessions", data={"day": day, "kind": "hike", "title": "SP-640 反覆爬坡", "minutes": 90,
                                                     "steps": HILL})
    assert r.ok, r.text()
    return r.json()["uid"]


def _open(page, uid):
    open_page(page, _server.PAGES["schedule"])
    page.wait_for_selector("#cal .cal", timeout=60_000)
    # the session's week (a phone shows fewer days): S.anchor on its day, then wait for it
    page.wait_for_function("() => typeof D !== 'undefined' && D && D.sessions", timeout=60_000)
    day = page.evaluate("(u) => fetch('/api/v1/overview/plan/sessions').then((r) => r.json())"
                        ".then((b) => b.sessions.find((s) => s.uid === u).day)", uid)
    page.evaluate("([u, d]) => { if (!findS(u)) { S.anchor = d; S.sel = d; load(); } }", [uid, day])
    page.wait_for_function("(u) => findS(u)", arg=uid, timeout=60_000)
    page.evaluate("(u) => openDlg(findS(u))", uid)
    page.wait_for_selector("#we-svg rect.blk", state="attached", timeout=60_000)


def test_pick_a_route_in_the_editor(app):
    page, _ = app
    page.goto(PAGE)
    r = upload(page)
    day = page.evaluate("() => { const d = new Date(Date.now() + 5 * 864e5); return d.toISOString().slice(0, 10); }")
    uid = _session(page, day)
    _open(page, uid)
    page.click("#we-routebtn")
    pop = page.locator("#we-rpop")
    expect(pop).to_be_visible()
    expect(pop).to_contain_text("我跑過的")                             # SP-693: both tabs' routes listed
    pop.locator(f'button[data-rs="gpx:{r["id"]}"]').click()
    line = page.locator("#we-rline")
    expect(line).to_be_visible()
    expect(line).to_contain_text("Maokong")           # the GPX's own name
    # the climb: lap at the top, named with its climb and time
    note = page.locator("#we-list input.note").nth(1)
    expect(note).to_have_value(re.compile(r"^爬 \d+ m · 約 \d+′$"))
    # the chart on the route: the distance axis, a climb's tooltip
    expect(page.locator('#we-legend [data-ax="dist"]')).to_be_visible()
    page.locator("#we-svg rect.blk").nth(1).hover()
    expect(page.locator("#we-tip")).to_contain_text("爬升")
    # another climb, then none: the repeats stay as they are
    sel = line.locator("select[data-climb]")
    opts = sel.locator("option").evaluate_all("os => os.map(o => o.value)")
    assert opts[-1] == "0" and len(opts) >= 2
    if len(opts) > 2:
        sel.select_option(opts[1])
        expect(line.locator("select[data-climb]")).to_have_value(opts[1])
    line.locator("button[data-rdel]").click()
    expect(line).to_be_hidden()


def test_phone_no_sideways_scroll(app, e2e_browser, e2e_server):
    page, _ = app
    page.goto(PAGE)
    r = upload(page)
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, has_touch=True,
                                  is_mobile=True, locale="zh-TW", timezone_id="Asia/Taipei")
    try:
        m = ctx.new_page()
        open_page(m, f"{PAGE}#g{r['id']}")
        expect(m.locator("#rl-det svg.prof")).to_be_visible(timeout=60_000)
        assert m.evaluate("document.documentElement.scrollWidth") <= 390
        for p in ("schedule", "templates"):
            open_page(m, _server.PAGES[p])
            expect(m.locator(".modesw a.mcard")).to_have_count(3)     # SP-693: 路線 is its own page
            assert m.evaluate("document.documentElement.scrollWidth") <= 390, p
    finally:
        ctx.close()
