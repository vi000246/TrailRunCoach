"""Browser smoke tests: every page loads without a JS error, its main parts are
drawn and its main buttons work — plus the features merged on 2026-10-04 that
had only backend tests (SP-70): the GPX maps, 抓活動, the watch status glyphs,
the TIS charts, 範本 management, the threshold-confidence card and the
課表訂閱 section.

Run: ./e2e.sh (see conftest.py). The app fixture fails a test on any page
error, console error or 5xx answer of the app."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import chart_drawn, no_loading, open_page  # noqa: E402

pytestmark = pytest.mark.e2e

VIEWER = _server.PAGES["viewer"]


def _json(page, path: str):
    r = page.request.get(path)
    assert r.ok, f"{path}: {r.status}"
    return r.json()


def _viewer_loaded(page, timeout: float = 90_000) -> None:
    """圖表分析: the dashboard's chart cards are there and none is still computing."""
    page.wait_for_function(
        """() => { const cs = document.querySelectorAll('#grid section.card');
                   return cs.length > 0 && ![...cs].some((c) => c.querySelector('.loading')); }""",
        timeout=timeout)


def _workouts(page) -> list[dict]:
    w = _json(page, "/api/v1/wko5/workouts")
    rows = w if isinstance(w, list) else w.get("workouts") or []
    assert rows, "the e2e athlete has no activities"
    return rows


# ---------------------------------------------------------------------------
# every page: loads, its nav is drawn, no error (the app fixture checks errors)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_server.PAGES))
def test_page_loads_clean(app, name):
    page, _ = app
    open_page(page, _server.PAGES[name])
    expect(page.locator(".appnav")).to_be_visible()
    # this page is marked in the nav (pages under 「更多」 also mark the 更多 button)
    assert page.locator(".appnav [aria-current='page']").count() >= 1
    page.wait_for_timeout(2500)                       # the first data and renders report their errors


# ---------------------------------------------------------------------------
# 總覽
# ---------------------------------------------------------------------------

def test_overview(app):
    page, _ = app
    open_page(page, _server.PAGES["overview"])
    no_loading(page, "#kpis")
    chart_drawn(page, "#pmc")
    no_loading(page, "#ind")
    no_loading(page, "#wk-days")
    # 做了什麼: week / month / year and the period arrows
    no_loading(page, "#period")
    for u in ("month", "year", "week"):
        page.click(f'#unit button[data-u="{u}"]')
        no_loading(page, "#period")
    page.click("#prev")
    no_loading(page, "#period")
    page.click("#next")
    page.click("#now")
    no_loading(page, "#period")
    expect(page.locator("#per-label")).not_to_be_empty()
    # 閾值可信度 (SP-64): the demo athlete's max HR (188) is not believable → a line under 測試
    expect(page.locator("#ind")).to_contain_text("可信度")


# ---------------------------------------------------------------------------
# 課表: 日曆, 抓活動, 手錶狀態, 課表統計, 範本
# ---------------------------------------------------------------------------

def _calendar_ready(page) -> None:
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)
    page.wait_for_selector("#cal .chip", timeout=60_000)


def test_schedule_calendar(app):
    page, _ = app
    open_page(page, _server.PAGES["schedule"])
    _calendar_ready(page)
    assert page.locator("#cal .chip[data-uid]").count() > 0, "no sessions on the calendar"
    # month / week, back and forth, today
    page.click('#view-seg button[data-view="week"]')
    _calendar_ready(page)
    page.click('#view-seg button[data-view="month"]')
    _calendar_ready(page)
    page.click("#next")
    page.wait_for_selector("#calwrap:not(.loading)")
    page.click("#prev")
    page.wait_for_selector("#calwrap:not(.loading)")
    page.click("#go-today")
    _calendar_ready(page)
    # a coming session (editable: draggable) opens its dialog with 儲存, 取消 closes it
    live = page.locator('#cal .chip[data-uid][draggable="true"]')
    assert live.count() > 0, "no editable session in the coming days"
    live.first.click()
    expect(page.locator("#sd")).to_have_attribute("open", "")
    expect(page.locator("#sd-save")).to_be_visible()
    expect(page.locator("#sd-we")).not_to_be_empty()
    page.click("#sd-cancel")
    expect(page.locator("#sd")).not_to_have_attribute("open", "")
    # 課表偏好 dialog
    page.click("#pf-btn")
    expect(page.locator("#pf")).to_have_attribute("open", "")
    page.locator("#pf [data-close]").first.click()
    expect(page.locator("#pf")).not_to_have_attribute("open", "")
    # the ⋯ menu opens and closes
    page.click("#more-btn")
    expect(page.locator("#more-menu")).to_be_visible()
    page.keyboard.press("Escape")
    page.mouse.click(5, 300)
    expect(page.locator("#more-menu")).to_be_hidden()


def test_schedule_watch_status_glyphs(app):
    """手錶狀態圖示 (推送狀態): the legend's four states, each an <svg> glyph with its label."""
    page, _ = app
    open_page(page, _server.PAGES["schedule"])
    _calendar_ready(page)
    glyphs = page.locator("#legend .sy")
    expect(glyphs).to_have_count(4)
    for cls in ("sy-ok", "sy-warn", "sy-bad", "sy-none"):
        g = page.locator(f"#legend .sy.{cls}")
        expect(g).to_have_count(1)
        assert g.locator("svg").count() == 1, cls
        assert g.get_attribute("aria-label"), cls
    # a session on the calendar shows its glyph only once it has a push status (pushed / not_pushed …)
    for g in page.locator("#cal .sy").all()[:5]:
        assert g.get_attribute("aria-label")


def test_schedule_pull_button_logged_out(app):
    """No COROS login (the e2e athlete): 抓活動 stays hidden, a link to 設定 instead."""
    page, _ = app
    open_page(page, _server.PAGES["schedule"])
    _calendar_ready(page)
    expect(page.locator("#pull-btn")).to_be_hidden()
    links = page.locator("#pull-login:visible, #login-link:visible")
    expect(links.first).to_be_visible()
    expect(links.first).to_have_attribute("href", "/api/v1/wko5/settings")


def test_schedule_pull_button_runs(app):
    """抓活動 with the 資料來源 logged in: the sync run (SSE) is answered by the test —
    never a real COROS call — and the page reports 「已是最新」 and reloads the calendar."""
    page, _ = app
    page.route("**/api/v1/sync/primary", lambda r: r.fulfill(json={
        "source": "coros", "label": "COROS", "logged_in": True, "login": "ok", "enabled": True, "busy": False}))
    events = [{"status": "range_list", "count": 2}, {"status": "checking"}, {"status": "checking"},
              {"status": "complete"}]
    sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    started = []

    def start(route):
        started.append(route.request.method)
        route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=sse)
    page.route("**/api/v1/sync/coros/start*", start)
    open_page(page, _server.PAGES["schedule"])
    _calendar_ready(page)
    btn = page.locator("#pull-btn")
    expect(btn).to_be_visible()
    expect(btn).to_be_enabled()
    expect(page.locator("#pull-lbl")).to_contain_text("COROS")
    btn.click()
    expect(page.locator("#sync-msg")).to_contain_text("已是最新", timeout=30_000)
    assert started == ["POST"]
    expect(btn).to_be_enabled()
    _calendar_ready(page)


def test_compliance(app):
    """課表統計: KPIs, the load-ratio chart, the period switch."""
    page, _ = app
    open_page(page, _server.PAGES["compliance"])
    no_loading(page, "#kpis")
    chart_drawn(page, "#ra-plot")
    for p in ("12w", "phase", "4w"):
        page.click(f'#period button[data-p="{p}"]')
        no_loading(page, "#kpis")
    page.click('#period button[data-p="custom"]')
    expect(page.locator("#custom")).to_be_visible()
    page.click("#c-go")
    no_loading(page, "#kpis")


def test_templates_manage(app):
    """範本: list, filters, open a built-in one, copy it to 我的範本, delete the copy."""
    page, _ = app
    open_page(page, _server.PAGES["templates"])
    lib = page.locator("#tm-items button[data-key]")
    expect(lib.first).to_be_visible(timeout=30_000)
    n_all = lib.count()
    page.click('#tm-src button[data-src="lib"]')
    expect(page.locator("#tm-items button[data-id]")).to_have_count(0)
    page.click('#tm-src button[data-src="all"]')
    # a category chip narrows the list, 「全部」 brings it back
    cats = page.locator("#tm-cats button[data-cat]")
    if cats.count() > 1:
        cats.nth(1).click()
        cats.first.click()
        expect(lib).to_have_count(n_all)
    page.fill("#tm-q", "zzzz-no-such-template")
    expect(lib).to_have_count(0)
    page.fill("#tm-q", "")
    expect(lib).to_have_count(n_all)
    # a built-in template: read-only, 複製成我的範本
    lib.first.click()
    expect(page.locator("#tm-ed")).to_be_visible()
    expect(page.locator("#tm-copy")).to_be_visible()
    expect(page.locator("#tm-save")).to_be_hidden()
    mine_before = page.locator("#tm-items button[data-id]").count()
    page.click("#tm-copy")
    expect(page.locator("#tm-msg")).to_have_class(re.compile(r"\bok\b"))
    expect(page.locator("#tm-items button[data-id]")).to_have_count(mine_before + 1)
    expect(page.locator("#tm-save")).to_be_visible()
    expect(page.locator("#tm-del")).to_be_visible()
    # delete the copy (two clicks: arm, confirm)
    page.click("#tm-del")
    expect(page.locator("#tm-del")).to_have_class(re.compile(r"\barmed\b"))
    page.click("#tm-del")
    expect(page.locator("#tm-items button[data-id]")).to_have_count(mine_before)
    # 新增範本: an empty form with the name focused
    page.click("#tm-new")
    expect(page.locator("#tm-name")).to_be_focused()
    expect(page.locator("#tm-del")).to_be_hidden()


# ---------------------------------------------------------------------------
# 週期規劃, 賽事計算機
# ---------------------------------------------------------------------------

def test_plan_page(app):
    page, _ = app
    open_page(page, _server.PAGES["plan"])
    expect(page.locator("#events tr").first).to_be_visible(timeout=30_000)
    expect(page.locator("#tl")).not_to_be_empty()
    expect(page.locator("#now")).not_to_be_empty()


def test_racepower_course_map(app):
    """賽事計算機 on the demo's GPX race: the plan is computed and the course map (Leaflet) drawn."""
    page, _ = app
    events = _json(page, "/api/v1/racepower/inputs").get("events") or []
    ids = [e["id"] for e in events]
    eid = next((x for x in ("demo-maokong", "demo-50k") if x in ids), None)
    assert eid, f"no GPX race among the e2e athlete's events: {ids}"
    open_page(page, f"{_server.PAGES['racepower']}?event={eid}")
    expect(page.locator("#event")).to_have_value(eid, timeout=30_000)
    page.click('#mode button[data-m="auto"]')
    page.click("#calc")
    expect(page.locator("#course-map-wrap")).not_to_have_class(re.compile(r"\bhidden\b"), timeout=60_000)
    expect(page.locator("#course-map.leaflet-container")).to_be_visible()
    page.wait_for_function("() => document.querySelectorAll('#course-map .leaflet-overlay-pane canvas, "
                           "#course-map .leaflet-overlay-pane svg path').length > 0", timeout=30_000)
    expect(page.locator("#course-map-legend")).not_to_be_empty()
    # the tab switch
    page.click('#tabs button[data-tab="acc"]')
    page.click('#tabs button[data-tab="calc"]')


# ---------------------------------------------------------------------------
# 設定: 閾值可信度, 課表訂閱
# ---------------------------------------------------------------------------

def test_settings_threshold_confidence(app):
    page, _ = app
    open_page(page, _server.PAGES["settings"])
    card = page.locator("#thr-check")
    expect(card).to_be_visible(timeout=30_000)
    expect(card).to_contain_text("LTHR")
    expect(card.locator(".card .v")).not_to_be_empty()
    # 最大心率 in 心率: the demo athlete's 188 is not believable (a sustained-peak candidate)
    hr = page.locator("#hr-check")
    expect(hr).to_be_visible()
    expect(hr.locator("[data-tcapply]").first).to_be_visible()


def test_settings_calendar_subscription(app):
    """課表訂閱: create the address, the ICS answers, disable it again."""
    page, watch = app
    open_page(page, _server.PAGES["settings"])
    page.locator("#calendar").scroll_into_view_if_needed()
    expect(page.locator("#cal-off")).to_be_visible(timeout=30_000)
    page.click("#cal-create")
    expect(page.locator("#cal-on")).to_be_visible()
    url = page.locator("#cal-https").input_value()
    assert url.startswith(page.url.split("/api/")[0]), url
    assert page.locator("#cal-webcal").input_value().startswith("webcal:")
    ics = page.request.get(url)
    assert ics.ok, ics.status
    assert "BEGIN:VCALENDAR" in ics.text()
    # 複製: the clipboard may be refused headless — the page then selects the text (no error)
    page.locator('[data-cal-copy="cal-https"]').click()
    page.click("#cal-disable")                        # confirm() → accepted by the fixture
    expect(page.locator("#cal-off")).to_be_visible()
    assert page.request.get(url).status in (401, 403, 404, 410)


# ---------------------------------------------------------------------------
# 活動列表, 單次活動 (GPX 地圖), 圖表分析 (TIS)
# ---------------------------------------------------------------------------

def test_activity_list(app):
    page, _ = app
    open_page(page, _server.PAGES["activity"])
    rows = page.locator("#tbody tr[data-k]")
    expect(rows.first).to_be_visible(timeout=60_000)
    assert rows.count() > 5
    page.locator("#tbody button[data-edit]").first.click()
    expect(page.locator("#ed")).to_have_attribute("open", "")
    page.keyboard.press("Escape")
    expect(page.locator("#ed")).not_to_have_attribute("open", "")
    page.fill("#f-q", "zzzz-no-such-activity")
    expect(rows).to_have_count(0)
    page.click("#f-reset")
    expect(rows.first).to_be_visible()
    page.click("#tab-ach")
    expect(page.locator("#ach")).to_be_visible()
    page.click("#tab-acts")
    expect(page.locator("#acts-view")).to_be_visible()


def test_single_activity_with_map(app):
    """單次活動判讀 › 本次重點 of a synced run: every card drawn, the route map is a Leaflet map."""
    page, _ = app
    idx = _workouts(page)[0]["index"]
    open_page(page, f"{VIEWER}?view=單次活動判讀&dash=0&workout={idx}")
    expect(page.locator("#m-workout")).to_have_class(re.compile(r"\bon\b"), timeout=30_000)
    _viewer_loaded(page)
    expect(page.locator("#grid .card .lmap.leaflet-container")).to_be_visible(timeout=30_000)
    page.wait_for_function("() => document.querySelectorAll('#grid .lmap .leaflet-overlay-pane canvas, "
                           "#grid .lmap .leaflet-overlay-pane svg path').length > 0", timeout=30_000)
    # SP-80: the activity chart beside the map; hovering it marks the map and the HR / power chart
    mc = page.locator("#grid .mapwrap .mapchart")
    expect(mc).to_be_visible(timeout=30_000)
    page.click('#grid .maplayout button[data-l="left"]')
    expect(page.locator("#grid .mapwrap")).to_have_class(re.compile(r"\bleft\b"))
    box = mc.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 40)
    expect(page.locator("#grid .lmap .leaflet-tooltip.hovtip")).to_be_visible(timeout=5_000)
    page.click('#grid .maplayout button[data-l="top"]')
    # another dashboard of the same activity (目錄), then back to the trends
    dbs = page.locator("#tree .db")
    if dbs.count() > 1:
        dbs.nth(1).click()
        _viewer_loaded(page)
    page.click("#back")
    expect(page.locator("#m-season")).to_have_class(re.compile(r"\bon\b"))
    _viewer_loaded(page)


def test_tis_charts(app):
    """我的訓練 › 負荷 PMC: the aerobic / anaerobic TIS charts (power charts: shown while 使用功率 is on)."""
    page, _ = app
    use_power = _json(page, "/api/v1/sync/settings").get("use_power") is not False
    open_page(page, f"{VIEWER}?view=我的訓練&dash=0")
    _viewer_loaded(page)
    tis = page.locator("#grid section.card", has=page.locator("h2", has_text="TIS"))
    if not use_power:
        expect(tis).to_have_count(0)
        return
    expect(tis).to_have_count(2)
    for i in range(2):                                # the demo athlete runs with Stryd: both have data
        card = tis.nth(i)
        card.scroll_into_view_if_needed()
        expect(card.locator(".plot canvas").first).to_be_visible()
        expect(card.locator(".nodata")).to_have_count(0)
