"""設定與傷病頁照提示多寡 (SP-488; docs/research/ui-hint-audit.md §7 T7) in a real browser on the demo athlete.

設定 → 生理數據 in the owner's 「都一致」 state — the app-vs-watch card within 5 bpm everywhere, 最大心率 and
LTHR 可信度高, both estimates equal to what is in use, WKO5's settings not its factory default. The demo athlete
has none of that (no COROS account, a 低 max HR), so those four answers are patched (the rest stays real), and
the level by patching GET /profile/hint-level.

What counts as a hint (COUNT_JS; the audit's §1.8 rows ST1–ST16 as they show on the tab): the tab note, every
explanation / status line (.meta, .s), pill, red line, ± badge, 可信度 chip, card (a card is one, its lines are
not counted again), each folded line (details.fold's summary) and the app-vs-watch card's sentences. Not counted:
the values the tab is for — 目前使用 (#thr-now's text and its CP source), the 課表區間 it gives (#hr-model-note),
the 起始負荷 card, the race card's pace, the power switch's state — the tables, form labels, the plain 「怎麼測」
style headings of closed <details>, what is inside a closed <details> and every `?`.
Counted inside those value lines anyway: a chip, a red line or a ± badge (ST7 / ST8 / ST10).

傷病: the cards come from engine/injuries.event_json for three open injuries (one red, one in the walk-run, one
green) with the light the list API would add; the page's other answers are real.

Run: ./e2e.sh -k sp488 (see conftest.py)."""
from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

MAX_HINTS = 8                 # the ticket's 驗收: 生理數據 in the 「都一致」 state ≤ 8
LEVELS = ("full", "standard", "lean")

COUNT_JS = r"""() => {
  const HINT = "#tab-note, .meta, .s, .pill, .err-text, .pm, .conf, .est .card, details.fold > summary, .cmp p, .cmp li";
  const VALUE = "#thr-now, #thr-now > .meta, #hr-model-note, #pmc-card .card, #pmc-card .card *, #u-power-note,"
    + " #race-card .card .k, #race-card .card .v, details:not(.fold) > summary, table *";
  const CHIP = ".conf, .err-text, .pm";
  // inside a closed <details> (its summary aside) is not on the page — Chromium still gives it boxes
  const shut = (el) => { for (let d = el.closest("details"); d; d = d.parentElement && d.parentElement.closest("details")) {
    if (!d.open && !(el.closest("summary") && el.closest("summary").parentElement === d)) return true; } return false; };
  const on = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden" && el.textContent.trim() && !shut(el);
  const isHint = (el) => el.matches(HINT) && (!el.matches(VALUE) || el.matches(CHIP)) && !el.closest("table");
  const roots = ["#tab-note", "#hr", "#thresholds", "#power"].map((s) => document.querySelector(s));
  const out = [];
  for (const root of roots) {
    for (const el of [root, ...root.querySelectorAll("*")]) {
      if (!isHint(el) || !on(el)) continue;
      let a = el.parentElement, nested = false;
      while (a && a !== document.body) { if (isHint(a)) { nested = true; break; } a = a.parentElement; }
      if (!nested) out.push((el.dataset.hintId || el.id || el.className) + ": " + el.textContent.replace(/\s+/g, " ").trim().slice(0, 60));
    }
  }
  return out;
}"""

CMP = {"alert_bpm": 5, "read_day": "2026-10-13", "alert": False,
       "rows": [{"id": "lthr", "name": "LTHR", "app": 168, "app_source": "手動輸入", "watch": 167, "diff": 1, "alert": False,
                 "from_watch": False},
                {"id": "max_hr", "name": "最大心率", "app": 188, "app_source": "你的設定", "watch": 188, "diff": 0,
                 "alert": False, "from_watch": False},
                {"id": "rest_hr", "name": "靜息心率", "app": 52, "app_source": None, "watch": 52, "diff": 0, "alert": False,
                 "from_watch": True}],
       "zones": None, "summary": "兩邊的差距都在 5 bpm 以內，不用改", "fixes": [], "where": "去哪裡改",
       "note": "推到手錶的課表心率目標直接送 bpm。",
       "history": {"rows": [{"day": "2026-09-24", "lthr": 167, "max_hr": 188, "rest_hr": 52, "text": "第一次讀到"}], "n": 1,
                   "last_change": {"day": "2026-09-24", "text": "第一次讀到"}}}


def _patch(page, glob: str, fn) -> None:
    def handle(route):
        if route.request.method != "GET":
            return route.continue_()
        r = route.fetch()
        body = r.json()
        fn(body)
        route.fulfill(response=r, json=body)
    page.route(glob, handle)


def _consistent(page, alert: bool = False, low: bool = False, wko5_default: bool = False) -> None:
    def hr(b):
        b["compare"] = {**CMP, "alert": alert, "summary": "最大心率差超過 5 bpm" if alert else CMP["summary"],
                        "fixes": ["最大心率：把 COROS 的 180 改成 188"] if alert else []}
        b["account"] = {"max_hr": 180 if alert else 188, "rest_hr": 52}
        b["rest_hr"] = {"value": 52, "kind": "coros", "source": "手錶（COROS 帳號）"}

    def chk(b):
        b["lthr"].update(confidence="high", signals=[])
        if low:      # the demo's own 低 max HR with its strong signal and candidate stays
            return
        b["hrmax"].update(confidence="high", signals=[], candidate=None)
        b.update(diagnosis=None, suggestions=[])
        b["warn"]["lthr"]["note"] = ""

    def est(b):
        b["lthr"] = {"value": 168, "n": 4, "hr_at_cp": 167, "n_at_cp": 3, "spread": [165, 170], "reason": "近 180 天 4 次", "days": 180}
        b["aethr"] = {"value": 150, "n": 8, "reason": "近 180 天 8 次", "days": 180, "pm": None, "points": []}

    def thr(b):
        b["wko5_settings"]["runthr"] = {"value": 168, "is_default": wko5_default, "date": "2026-09-24"}
        b["wko5_settings"]["runmhr"] = {"value": 188, "is_default": False, "date": "2026-09-24"}
        if wko5_default:
            b["effective_thresholds"]["lthr"]["wko5_default"] = True

    _patch(page, "**/api/v1/plan/hr-profile", hr)
    _patch(page, "**/api/v1/plan/threshold-check", chk)
    _patch(page, "**/api/v1/plan/threshold-estimate", est)
    _patch(page, "**/api/v1/plan/thresholds", thr)


def _level(page, level: str) -> None:
    _patch(page, "**/api/v1/plan/profile/hint-level", lambda b: b.update(level=level))


def _body_tab(page, level: str):
    open_page(page, _server.PAGES["settings"] + "#body")
    page.wait_for_load_state("networkidle")
    page.wait_for_function("(l) => HL === l && !!TC && !!E && !!HR", arg=level)
    page.wait_for_timeout(300)
    return page.evaluate(COUNT_JS)


@pytest.mark.parametrize("level", LEVELS)
def test_the_physiology_tab_in_the_consistent_state(app, level):
    page, _ = app
    _consistent(page)
    _level(page, level)
    hints = _body_tab(page, level)
    note = page.locator("#tab-note")
    if level == "full":                                       # 完整: everything as before (ST1 ●, cards open)
        expect(note).to_be_visible()
        # SP-662: the app-vs-watch check is one line in every level while every gap is within 5 bpm
        expect(page.locator("#hr-cmp details.fold")).to_be_visible()
        expect(page.locator("#thr-est .card")).to_have_count(2)
        expect(page.locator("#hr-check .card")).to_be_visible()
        expect(page.locator("#thr-more")).to_be_hidden()
        assert len(hints) > MAX_HINTS, hints
        return
    expect(note).to_be_hidden()                               # ST1 ✕
    assert len(hints) <= MAX_HINTS, hints
    # R5: 高 → one line beside the value; ST9: one line; ST11: the estimates (and the WKO5 line) in one fold
    expect(page.locator("#hr-check")).to_be_hidden()
    expect(page.locator("#thr-check")).to_be_hidden()
    expect(page.locator('#hr-conf [data-hint-id="ST7"]')).to_have_text("· 可信度高 ✓")
    expect(page.locator('#thr-conf [data-hint-id="ST8"]')).to_have_text("· 可信度高 ✓")
    expect(page.locator("#hr-cmp details.fold > summary")).to_have_text("跟手錶的設定差距不大（都在 5 bpm 內），不用改 ✓")
    expect(page.locator("#hr-cmp details.fold")).not_to_have_attribute("open", "")
    expect(page.locator("#thr-est")).to_be_hidden()
    expect(page.locator("#thr-more-sum")).to_have_text("自動估算：LTHR 和目前一致 ✓、AeT 和目前一致 ✓")
    expect(page.locator("#thr-wko5")).to_be_hidden()
    page.locator("#thr-more-sum").click()                    # the fold holds both cards and the WKO5 line
    expect(page.locator("#thr-more-est .card")).to_have_count(2)
    expect(page.locator("#thr-more-wko5")).to_contain_text("對照資料夾裡的設定")
    # ST6: 精簡 keeps where the value comes from on hover only
    src = page.locator('#hr-kv [data-hint-id="ST6"]')
    if level == "lean":
        expect(src).to_have_count(0)
        assert page.locator("#hr-kv dd").first.get_attribute("title") == "你的設定 2026-09-24"
    else:
        expect(src).to_have_count(2)


def test_a_gap_or_a_low_confidence_still_opens_its_card(app):
    """Class 1 (ST7 低 with a strong signal, ST9 a gap > 5 bpm): the card, in 精簡 too."""
    page, _ = app
    _consistent(page, alert=True, low=True)
    _level(page, "lean")
    _body_tab(page, "lean")
    card = page.locator('#hr-check .card[data-hint-id="ST7"]')
    expect(card).to_be_visible()
    expect(card).to_have_class("card must")
    expect(card.locator("button[data-tcapply]")).to_be_visible()          # its 套用 stays on the page
    expect(page.locator("#hr-conf")).to_be_empty()
    expect(page.locator("#hr-cmp h3 .pill.warn")).to_be_visible()
    expect(page.locator("#hr-cmp")).to_contain_text("最大心率：把 COROS 的 180 改成 188")
    expect(page.locator("#hr-cmp details.fold")).to_have_count(0)


def test_the_wko5_default_is_one_red_line(app):
    """ST10: 「LTHR 是 WKO5 預設值」 and 「WKO5 出廠值」 were two red lines; now one, in every level."""
    page, _ = app
    _consistent(page, wko5_default=True)
    _level(page, "standard")
    _body_tab(page, "standard")
    reds = page.locator('#thresholds .err-text[data-hint-id="ST10"]')
    expect(reds).to_have_count(1)
    expect(reds).to_be_visible()
    expect(reds).to_contain_text("出廠預設值")
    expect(page.locator("#thr-wko5 .err-text, #thr-more-wko5 .err-text")).to_have_count(0)


def test_switching_the_level_redraws_the_tab(app):
    """Picking another level on 個人資料 redraws the page at once (ST5 ? ↔ text, ST1, ST9); the session server's
    level is put back to automatic afterwards (it is shared with the other e2e tests)."""
    page, _ = app
    _consistent(page)
    open_page(page, _server.PAGES["settings"] + "#personal")
    page.wait_for_load_state("networkidle")
    try:
        expect(page.locator("#map > p.meta").first).to_be_hidden()        # ST5 標準: in the heading's ?
        q = page.locator("#map h2 button.fold-q")
        expect(q).to_be_visible()
        assert "魯地圖" in (q.get_attribute("title") or "")
        page.locator('input[name="hint-level"][value="full"]').check(force=True)
        page.wait_for_function("() => HL === 'full'")
        expect(page.locator("#map > p.meta").first).to_be_visible()
        expect(q).to_be_hidden()
        page.locator('.tabs [data-tab="body"]').click()
        expect(page.locator("#tab-note")).to_be_visible()
        expect(page.locator("#hr-cmp details.fold")).to_be_visible()      # SP-662: one line in 完整 too
        page.locator('.tabs [data-tab="personal"]').click()
        page.locator("#hint-auto").click()                    # back to automatic (標準 for the demo athlete)
        page.wait_for_function("() => HL === 'standard'")
        page.locator('.tabs [data-tab="body"]').click()
        expect(page.locator("#hr-cmp details.fold")).to_be_visible()
        expect(page.locator("#tab-note")).to_be_hidden()
    finally:
        page.request.put("/api/v1/plan/profile/hint-level", data={"level": None})


# ---------------------------------------------------------------------------- 傷病紀錄

def _injuries(page, level: str) -> None:
    from backend.engine import injuries as INJ
    today = dt.date.today()
    rows = [({"id": 1, "area": "shin_calf", "side": "right", "kind": "overuse", "severity": "severe", "status": "active",
              "onset_date": (today - dt.timedelta(days=4)).isoformat()},
             {"color": "red", "label": "紅燈", "reason": "嚴重度選了「重」（停跑）", "phase": "red", "stage": 0, "n": 0, "need": None}),
            ({"id": 2, "area": "achilles", "side": "right", "kind": "overuse", "severity": "moderate", "status": "active",
              "condition": "achilles", "onset_date": (today - dt.timedelta(days=13)).isoformat()},
             {"color": "walkrun", "label": "走跑階段", "reason": "走跑階段第 2 階", "phase": "walkrun", "stage": 1, "n": 1, "need": 3}),
            ({"id": 3, "area": "knee", "side": "left", "kind": "overuse", "severity": "mild", "status": "active",
              "condition": "pfp", "onset_date": (today - dt.timedelta(days=24)).isoformat()},
             {"color": "green", "label": "綠燈", "reason": "還沒有標記疼痛的跑步", "phase": "light", "stage": 0, "n": 0, "need": None})]
    body = {"injuries": [{**INJ.event_json(ev, today), "light": light} for ev, light in rows]}
    page.route("**/api/v1/app/injuries", lambda route: route.fulfill(json=body) if route.request.method == "GET"
               else route.continue_())
    _level(page, level)
    open_page(page, "/api/v1/app/injuries/page")
    page.locator("#cards .card").first.wait_for()
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize("level", LEVELS)
def test_the_red_light_the_walk_run_and_its_button_in_every_level(app, level):
    page, _ = app
    _injuries(page, level)
    lights = page.locator('#cards [data-hint-id="IJ1"]')
    expect(lights).to_have_count(3)
    expect(lights.nth(0)).to_have_text("紅燈")
    expect(lights.nth(1)).to_have_text("走跑階段・走跑第 2 階：這階已完成 1/3 次")
    btn = page.locator('#cards [data-hint-id="IJ2"]')
    expect(btn).to_have_count(1)                              # 「可以開始走跑」 on the red one only
    expect(btn).to_have_text("可以開始走跑")
    expect(btn.locator("xpath=following-sibling::button[1]")).to_have_attribute("data-tip", "紅燈之後：先能走 30 分鐘不痛、走路的樣子正常，再開始走跑交替（Ohio State Wexner 回跑指引，臨床機構）。app 不判斷能不能開始跑。按了之後課表從「走 4 分／跑 1 分」開始；走完 30 分在活動上標「沒痛」或「痠」也一樣。直接跑一次標「沒痛」，就跳過走跑、直接回綠燈。")
    # IJ3: the monitoring rule is one visible line, the study under 「詳細」 (closed)
    mon = page.locator('#cards [data-hint-id="IJ3"]')
    expect(mon).to_have_count(3)
    expect(mon.nth(1).locator(".mon-rule")).to_have_text("疼痛監測：跑時 ≤ 5/10；跑完 ≤ 5/10，隔天早上要退回原本的程度；不能一週比一週痛")
    expect(mon.nth(2).locator(".mon-rule")).to_have_text("疼痛監測：跑時 ≤ 2/10；跑完 60 分鐘內回到原本的程度")
    expect(mon.nth(0).locator(".mon-rule")).to_contain_text("先給醫師看")
    expect(mon.nth(1).locator(".mon-full")).to_be_hidden()
    mon.nth(1).locator(".mon-more").click()
    expect(mon.nth(1).locator(".mon-full")).to_be_visible()
    expect(mon.nth(1).locator(".mon-full")).to_contain_text("Silbernagel 2007")
    expect(page.locator("#ev")).to_be_hidden()               # opening 「詳細」 is not a click on the card


def test_one_disclaimer_and_the_severity_scale_in_the_dialog_only(app):
    page, _ = app
    _injuries(page, "standard")
    text = page.evaluate("() => document.body.innerText + [...document.querySelectorAll('[title], [data-tip]')]"
                         ".filter((e) => !e.closest('dialog')).map((e) => (e.title || '') + (e.dataset.tip || '')).join('\\n')")
    assert text.count("這不是醫療診斷") == 1, text.count("這不是醫療診斷")
    expect(page.locator('#disc[data-hint-id="IJ7"]')).to_be_visible()
    assert "照練" not in text                                   # the scale: not in the header ?, cards or filters
    page.locator("#add").click()
    expect(page.locator("#f-sev")).to_contain_text("輕・照練")
    assert "輕＝照練" in page.locator("#fm .fr", has=page.locator("#f-sev")).locator(".qtip").get_attribute("data-tip")


# ---------------------------------------------------------------------------- R14 首次精靈

@pytest.mark.parametrize("page_id, dialog", [("racepower", False), ("overview", True), ("settings", True)])
def test_the_setup_wizard_interrupts_only_on_overview_and_settings(app, page_id, dialog):
    page, _ = app
    _patch(page, "**/api/v1/plan/profile", lambda b: b["setup"].update(needed=True, missing=["weight"], remind=True))
    open_page(page, _server.PAGES[page_id])
    page.wait_for_load_state("networkidle")
    if dialog:
        expect(page.locator("dialog.sw-dlg")).to_be_visible()
        expect(page.locator("#sw-line")).to_have_count(0)
    else:
        expect(page.locator("dialog.sw-dlg")).to_have_count(0)
        line = page.locator('#sw-line[data-hint-id="GL6"] a')
        expect(line).to_have_text("還缺體重：估算可能不準，到設定填 →")
        assert line.get_attribute("href") == "/api/v1/app/settings#general"
