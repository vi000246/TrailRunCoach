"""SP-955: 設定 › 訓練依據 in a real browser on the demo athlete (Stryd, a CP test 20 days back: ① power in effect,
越野). Layout B: left 「你給的」 (裝備 + 主要訓練項目, 門檻與測試, 手動指定), right 「系統用的」 (現在用什麼練, 我的
區間); a phone stacks them with the result first. ③ (no power meter) is the server's answers patched — the e2e
server is shared, so nothing here changes the stored equipment or sport (their writes are intercepted).

Run: ./e2e.sh -k sp955 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

NONE = {"state": "none", "code": 3, "label": "沒有功率計", "power": False, "meter": False, "equipment": "none",
        "equipment_how": "設定", "fresh": True, "last_cp_test": "2026-10-07", "last_race": None, "anchor": "2026-10-07",
        "days": 20, "stale_on": "2026-11-19", "fresh_days": 42, "cp": 260.0, "cp_date": "2026-10-07", "cp_source": "",
        "suggest": None, "reason": "沒有功率計：課表、模型和門檻都用心率／配速"}


def _basis(page, path="#body"):
    open_page(page, _server.PAGES["settings"] + path)
    page.locator("#now-rows .nr").first.wait_for(timeout=90_000)
    page.wait_for_function("() => !!PST && !!BASIS && !!PREFS && !!PZ && !!HR")


def _no_power(page):
    """③: the power state says no meter; the rows as the server gives them without one (by heart rate)."""
    page.route("**/api/v1/plan/power-state", lambda r: r.fulfill(json=NONE))

    def basis(route):
        r = route.fetch()
        b = r.json()
        b["power_state"] = NONE
        for row in b["rows"]:
            if row["basis"] == "power":
                row.update(basis="hr", basis_label="心率", target="心率 ≤ 150 bpm")
        route.fulfill(response=r, json=b)
    page.route("**/api/v1/overview/plan/basis", basis)


def test_layout_b_power_in_effect(app):
    page, _ = app
    _basis(page)
    expect(page.locator("#tab-body")).to_have_text("訓練依據")
    left, right = page.locator(".basis-l").bounding_box(), page.locator(".basis-r").bounding_box()
    assert left["x"] < right["x"] and abs(left["y"] - right["y"]) < 4          # two columns side by side
    for sel in ("#equip", "#thresholds", "#overrides"):
        assert page.locator(f".basis-l {sel}").count() == 1, sel
    for sel in ("#now", "#physio"):
        assert page.locator(f".basis-r {sel}").count() == 1, sel
    # 裝備: asked once, Stryd / 沒有功率計 — no 使用功率, no 手錶功率, no 30-minute test anywhere
    expect(page.locator('input[name="equip"]')).to_have_count(2)
    expect(page.locator('input[name="equip"][value="stryd"]')).to_be_checked()
    for gone in ("#u-power", "#u-watch", "#p-meter", "#power"):
        expect(page.locator(gone)).to_have_count(0)
    body = page.locator("main").inner_text()
    assert "手錶推估功率" not in body and "30 分鐘測試" not in body and "使用功率" not in body
    # ①: the state line, the power rows, CP tile, CP test, power zone set and power zones all there
    expect(page.locator("#now-status")).to_have_class("ps-box ps-power")
    expect(page.locator("#now-status")).to_contain_text("① 功率生效")
    rows = page.locator("#now-rows .nr")
    names = rows.locator(".nm").all_inner_texts()
    assert any("爬坡重複" in n for n in names) and any("B2B" in n for n in names)          # 越野
    assert not any("馬拉松配速段" in n for n in names)
    expect(page.locator('#now-rows .nr[data-k="z3"] .chip')).to_have_text("功率")
    expect(page.locator("#pv-tiles .pv-tile .k", has_text="CP")).to_have_count(1)
    for sel in ("#b-cpp-row", "#pz-model-row", "#b-climb-row", "#pv-pw"):
        expect(page.locator(sel)).to_be_visible()
    expect(page.locator("#pv-hr .pv-row")).to_have_count(6)
    assert page.locator("#pv-pw .pv-row").count() >= 5
    # 手動指定 all automatic: no （手動）
    expect(page.locator("#overrides .man:visible")).to_have_count(0)


def test_no_power_meter_hides_every_power_setting(app):
    page, _ = app
    _no_power(page)
    _basis(page)
    expect(page.locator('input[name="equip"][value="none"]')).to_be_checked()
    expect(page.locator("#now-status")).to_have_class("ps-box ps-none")
    expect(page.locator("#now-status")).to_contain_text("③ 沒有功率計")
    expect(page.locator("#now-rows .chip", has_text="功率")).to_have_count(0)
    expect(page.locator("#pv-tiles .pv-tile .k", has_text="CP")).to_have_count(0)
    for sel in ("#b-cpp-row", "#pz-model-row", "#b-climb-row", "#pv-pw", "#thr-pz-box", "#how-cp"):
        expect(page.locator(sel)).to_be_hidden()
    assert page.evaluate("() => document.querySelector('#b-basis option[value=power]').disabled")
    expect(page.locator("#thr-now")).not_to_contain_text("260")              # the CP on file is not said in use
    expect(page.locator("#b-aetp")).to_be_visible()                       # the AeT test stays


def test_changing_equipment_writes_the_profile_power_source(app):
    page, _ = app
    sent = []

    def profile(route):
        if route.request.method == "PUT":
            sent.append(json.loads(route.request.post_data))
            return route.fulfill(json={})
        return route.continue_()
    page.route("**/api/v1/plan/profile", profile)
    _basis(page)
    page.locator("#equip label:has(input[value=none])").click()
    page.wait_for_function("() => document.querySelector('#toast').classList.contains('on')")
    assert sent and sent[0]["power_source"] == "none" and "weights" in sent[0]


def test_main_sport_one_setting_two_entries(e2e_browser, e2e_server):
    # its own context with the service worker blocked: the worker the settings page registers would answer the
    # schedule page's GET /app/primary-sport itself, past page.route
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 1400, "height": 900}, locale="zh-TW",
                                  timezone_id="Asia/Taipei", service_workers="block")
    try:
        _sport_two_entries(ctx.new_page())
    finally:
        ctx.close()


def _sport_two_entries(page):
    puts = []

    def settings(route):
        if route.request.method == "PUT":
            puts.append(json.loads(route.request.post_data))
            return route.fulfill(json={})
        return route.continue_()
    page.route("**/api/v1/sync/settings", settings)
    _basis(page)
    page.select_option("#u-sport", "road")
    page.wait_for_function("() => document.querySelector('#toast').classList.contains('on')")
    assert {"primary_sport": "road"} in puts
    # the 課表偏好 entry: first in the dialog, reading the same setting (GET /app/primary-sport)
    page.route("**/api/v1/app/primary-sport", lambda r: r.fulfill(json={
        "setting": "road", "sport": "road", "label": "路跑／馬拉松",
        "suggested": {"sport": "trail", "reason": "A 賽是越野賽"}}))
    open_page(page, _server.PAGES["schedule"])
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)
    page.click("#pf-btn")
    expect(page.locator("#pf")).to_have_attribute("open", "")
    expect(page.locator("#pf-sport")).to_have_value("road")
    expect(page.locator("#pf-sport-why")).to_contain_text("資料建議：越野跑")
    assert page.locator("#pf-sport").bounding_box()["y"] < page.locator("#pf .pf-tabs").bounding_box()["y"]
    for gone in ('input[name="target_basis"]', 'input[name="climb_target"]', "#pf-cptp", "#pf-aetp"):
        expect(page.locator(f"#pf {gone}")).to_have_count(0)


def test_a_phone_puts_the_result_first(e2e_browser, e2e_server):
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 390, "height": 844}, is_mobile=True,
                                  has_touch=True, device_scale_factor=2, locale="zh-TW", timezone_id="Asia/Taipei")
    try:
        page = ctx.new_page()
        _basis(page)
        assert page.locator("#now").bounding_box()["y"] < page.locator("#equip").bounding_box()["y"]
        assert page.locator("#physio").bounding_box()["y"] < page.locator("#equip").bounding_box()["y"]
        assert page.evaluate("() => [innerWidth, document.documentElement.scrollWidth]") == [390, 390]
    finally:
        ctx.close()


def test_advanced_one_type_set_marked_and_reset(app):
    """SP-964: 進階設定 (collapsed under 手動指定) — one row per choosable kind of 越野; set 5 區間歇 to 配速 → the
    card's row says （手動） with 改回自動, which puts it back. The writes are intercepted (shared server): the
    PATCH answers with the choice, the basis answer is the real one with that row as the server gives it."""
    page, _ = app
    chosen, sent = {}, []

    def basis(route):
        r = route.fetch()
        b = r.json()
        if chosen.get("z5") == "pace":
            for row in b["rows"]:
                if row["key"] == "z5":
                    row.update(basis="pace", basis_label="配速", manual=True, own=True, type_key="z5",
                               why="進階設定：5 區間歇用配速", target="配速 5:30–5:50 /km（103–109% 閾值配速）")
            for ty in b["types"]:
                if ty["key"] == "z5":
                    ty["choice"] = "pace"
        route.fulfill(response=r, json=b)
    page.route("**/api/v1/overview/plan/basis", basis)
    _basis(page)
    base = page.evaluate("() => PREFS")

    def prefs(route):
        if route.request.method != "PATCH":
            return route.continue_()
        body = json.loads(route.request.post_data)
        sent.append(body)
        for k, v in (body.get("type_basis") or {}).items():
            if v == "auto":
                chosen.pop(k, None)
            else:
                chosen[k] = v
        route.fulfill(json={"prefs": {**base, "type_basis": dict(chosen)}})
    page.route("**/api/v1/overview/plan/prefs", prefs)
    adv = page.locator("#adv")
    expect(adv).to_be_visible()
    assert page.locator(".basis-l #overrides #adv").count() == 1                 # under 手動指定, left column
    expect(adv.locator("#adv-rows")).to_be_hidden()                             # collapsed
    adv.locator("summary").click()
    keys = page.locator("#adv-rows .adv-row").evaluate_all("rs => rs.map((r) => r.dataset.k)")
    assert keys == ["easy", "z3", "z5", "hill"]                                 # 越野: no long / MP
    expect(page.locator("#adv-z5 option").first).to_have_text("自動（目前：功率）")
    assert page.locator("#adv-z5 option").all_inner_texts()[1:] == ["心率", "配速", "功率"]
    assert page.locator("#adv-easy option").all_inner_texts()[1:] == ["心率", "功率"]      # no pace on easy runs
    expect(page.locator('#now-rows .nr[data-k="z5"] .man')).to_have_count(0)
    page.select_option("#adv-z5", "pace")
    row = page.locator('#now-rows .nr[data-k="z5"]')
    expect(row.locator(".man")).to_have_text("（手動）")
    expect(row.locator(".chip")).to_have_text("配速")
    expect(page.locator('.adv-row[data-k="z5"] .man')).to_be_visible()
    expect(page.locator("#adv-n")).to_have_text("（改了 1 類）")
    expect(page.locator('#now-rows .nr[data-k="z3"] .man')).to_have_count(0)    # the other types stay automatic
    assert sent == [{"type_basis": {"z5": "pace"}}]
    row.locator("button.to-auto").click()
    expect(page.locator('#now-rows .nr[data-k="z5"] .man')).to_have_count(0)
    expect(page.locator('#now-rows .nr[data-k="z5"] .chip')).to_have_text("功率")
    expect(page.locator("#adv-z5")).to_have_value("auto")
    assert sent[-1] == {"type_basis": {"z5": "auto"}}


def test_english(app):
    page, _ = app
    open_page(page, _server.PAGES["settings"] + "?lang=en#body")
    page.locator("#now-rows .nr").first.wait_for(timeout=90_000)
    expect(page.locator("#tab-body")).to_have_text("Training basis")
    expect(page.locator("#now h2")).to_contain_text("What you train by now")
    expect(page.locator("#equip h2")).to_contain_text("Equipment")
    assert not any("一" <= ch <= "鿿" for ch in page.locator("#now-status").inner_text())
