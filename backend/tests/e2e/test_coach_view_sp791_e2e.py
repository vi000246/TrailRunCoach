"""代看 (SP-791) and the coach's edits (SP-792) in a real browser on the demo athlete.

The e2e server runs in owner mode: 代看 never happens there. The page is made to believe it as
test_settings_coach_tab_sp789_e2e.py does — the inlined TRC_SESSION gets `login`, `viewing` and the view's
caps — and the coach routes answer from here (page.route), shaped like api/viewing.py's. The server-side
rules themselves are test_coach_view_sp791.py's / test_coach_edits_sp792.py's.

Run: ./e2e.sh -k coach_view (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

OFF = {"plan": False, "training": False}
ON = {"plan": True, "training": True}


def _session(page, extra: dict):
    js = json.dumps(extra, ensure_ascii=False)
    page.add_init_script(f"""(() => {{ let v; Object.defineProperty(window, "TRC_SESSION", {{ configurable: true,
      get() {{ return v; }}, set(x) {{ v = {{ ...x, login: {{ required: true, signed_in: true }}, ...{js} }}; }} }}); }})()""")


def _viewing(page, perms=OFF, name="林小跑"):
    caps = (["plan.write", "push", "upload.gpx"] if perms["plan"] else []) + (["thresholds.write"] if perms["training"] else [])
    _session(page, {"viewing": {"student_id": 30, "name": name, "perms": perms, "back": "/api/v1/app/settings#coach"},
                    "caps": sorted(caps)})


def _open(page, key, suffix=""):
    open_page(page, _server.PAGES[key] + suffix)
    page.wait_for_load_state("networkidle")


def test_the_banner_says_whose_data_and_back_to_my_own_ends_it(app):
    page, _ = app
    _viewing(page)
    stopped = []
    page.route("**/api/v1/coach/view/stop", lambda r: (stopped.append(r.request.method),
                                                         r.fulfill(json={"go": "/api/v1/app/settings#coach"})))
    _open(page, "overview")
    bar = page.locator(".an-view")
    expect(bar).to_contain_text("正在看 林小跑 的資料")
    expect(bar).to_contain_text("唯讀")
    expect(page.locator('.appnav [href="/api/v1/app/injuries/page"]')).to_have_count(0)      # the injury log: closed
    bar.locator("button").click()
    page.wait_for_url("**/api/v1/app/settings*")
    assert stopped == ["POST"]


def test_the_banner_names_what_the_student_allows(app):
    page, _ = app
    _viewing(page, ON)
    _open(page, "schedule")
    expect(page.locator(".an-view")).to_contain_text("可以改：課表、賽事、不排課日、門檻與訓練設定")


def test_settings_shows_only_the_training_tab_locked_without_the_switch(app):
    page, _ = app
    _viewing(page, OFF)
    _open(page, "settings", "#sync")
    expect(page.locator('.tabs [aria-selected="true"]')).to_have_attribute("data-tab", "body")
    for tab in ("personal", "data", "advanced", "cairn", "coach"):
        expect(page.locator(f"#tab-{tab}")).to_be_hidden()
    expect(page.locator("#thresholds")).to_be_visible()
    expect(page.locator("#power")).to_be_hidden()
    expect(page.locator("#sync")).to_be_hidden()
    for sel in ("#hr-max-save", "#thr-save", "#thr-add", "#pmc-save", "#pz-model"):
        expect(page.locator(sel)).to_have_attribute("data-cap-locked", "1")


def test_with_the_training_switch_the_settings_are_editable(app):
    page, _ = app
    _viewing(page, ON)
    _open(page, "settings")
    for sel in ("#hr-max-save", "#thr-save", "#thr-add", "#pmc-save", "#pz-model"):
        expect(page.locator(sel)).not_to_have_attribute("data-cap-locked", "1")


def test_a_refused_write_says_why(app):
    page, _ = app
    _viewing(page, OFF)
    _open(page, "overview")
    page.route("**/api/v1/plan/events", lambda r: r.fulfill(status=403, json={"code": "COACH_PERM_OFF", "detail": {
        "code": "COACH_PERM_OFF", "message": "學員關掉了「改課表、賽事與不排課日」，教練現在不能改", "params": {}}}))
    page.evaluate("""() => fetch("/api/v1/plan/events", { method: "PUT", headers: { "Content-Type": "application/json" },
                                                           body: "{}" })""")
    expect(page.locator("#an-toast")).to_contain_text("學員關掉了「改課表、賽事與不排課日」")


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_phone_width_does_not_scroll_sideways(app, lang):
    page, _ = app
    page.set_viewport_size({"width": 375, "height": 800})
    _viewing(page, ON, name="Mountain Goat Chen")
    _open(page, "schedule", "?lang=en" if lang == "en" else "")
    expect(page.locator(".an-view")).to_be_visible()
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], w
    if lang == "en":
        expect(page.locator(".an-view")).to_contain_text("Viewing Mountain Goat Chen's data")
        expect(page.locator(".an-view button")).to_have_text("Back to my own")


# ---------------------------------------------------------------------------- the student's side, the coach's button

SEE = "你的訓練紀錄、圖表、課表與賽事"
LABELS = {"plan": "改課表、賽事與不排課日", "training": "改門檻與訓練設定（LTHR、CP、區間）"}
ACTIVE = {"link_id": 7, "name": "王教練", "status": "active", "initiated_by": "coach",
          "since": "2026-10-20T04:00:00+00:00", "can_see": SEE, "perms": ON}
EDITS = [{"at": "2026-10-20T06:03:00+00:00", "coach": "王教練", "action": "session_edit",
          "params": {"day": "2026-10-22", "title": "輕鬆跑", "fields": "day,minutes"}},
         {"at": "2026-10-20T06:00:00+00:00", "coach": "王教練", "action": "view_start", "params": {}}]


def _student(page, mine):
    _session(page, {})
    page.route("**/api/v1/account/me", lambda r: r.fulfill(json={"email": "runner@example.com", "name": "跑者",
                                                                   "analysis": True, "can_delete": True, "reauth_ok": False}))
    page.route("**/api/v1/account/links", lambda r: r.fulfill(json={"clients": []}))
    page.route("**/api/v1/account/coach", lambda r: r.fulfill(json=mine))
    page.route("**/api/v1/account/coach/edits*", lambda r: r.fulfill(json={"edits": EDITS}))


def test_the_student_switches_what_the_coach_may_change_and_reads_the_log(app):
    page, _ = app
    sent = []
    _student(page, {"max": 1, "is_coach": False, "perm_labels": LABELS, "coaches": [ACTIVE]})
    after = {"max": 1, "is_coach": False, "perm_labels": LABELS, "coaches": [{**ACTIVE, "perms": {"plan": False, "training": True}}]}
    page.route("**/api/v1/account/coach/7/perms", lambda r: (sent.append(json.loads(r.request.post_data)),
                                                             r.fulfill(json=after)))
    _open(page, "settings", "#coach")
    boxes = page.locator("#coach-mine input[data-perm]")
    expect(boxes).to_have_count(2)
    expect(page.locator("#coach-mine")).to_contain_text("改課表、賽事與不排課日")
    page.locator('#coach-mine input[data-perm="plan"]').uncheck()
    expect(page.locator("#coach-msg")).to_contain_text("已關掉「改課表、賽事與不排課日」")
    assert sent == [{"plan": False}]
    expect(page.locator('#coach-mine input[data-perm="plan"]')).not_to_be_checked()
    log = page.locator("#coach-edits")
    expect(log).to_contain_text("王教練")
    expect(log).to_contain_text("修改一堂課（2026-10-22、輕鬆跑、改了日期、時間）")
    expect(log).to_contain_text("開始查看你的資料")


def test_the_coach_opens_a_students_data_from_their_list(app):
    page, _ = app
    coach = {"is_coach": True, "code": "K7MP3QXA", "invite": None, "max_students": 4, "used": 1, "full": False,
             "students": [{"link_id": 11, "student_id": 30, "name": "陳山羊", "status": "active", "initiated_by": "coach",
                           "since": "2026-10-18T05:00:00+00:00", "disabled": False}]}
    _student(page, {"max": 1, "is_coach": True, "perm_labels": LABELS, "coaches": []})
    page.route(r"**/api/v1/coach", lambda r: r.fulfill(json=coach))
    started = []
    page.route("**/api/v1/coach/view/30", lambda r: (started.append(r.request.method),
                                                       r.fulfill(json={"go": "/api/v1/overview/page"})))
    _open(page, "settings", "#coach")
    page.locator("#coach-students button.primary", has_text="查看資料").click()
    page.wait_for_url("**/api/v1/overview/page")
    assert started == ["POST"]


def test_the_schedule_says_the_coach_changed_it(app):
    page, _ = app
    _session(page, {"coach_edit": {"coach": "王教練", "at": "2026-10-20T06:03:00+00:00", "action": "session_edit"}})
    _open(page, "schedule")
    note = page.locator(".an-vnote")
    expect(note).to_contain_text("教練 王教練 於")
    expect(note).to_contain_text("修改了你的課表或訓練設定")
    expect(note.locator('a[href="/api/v1/app/settings#coach"]')).to_be_visible()
    note.locator("a[role=button]").click()
    expect(note).to_have_count(0)
