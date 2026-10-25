"""設定 › 教練 (SP-789, SP-793) in a real browser on the demo athlete.

The e2e server runs in owner mode: there the coach tab never shows. Sign-in mode is made in the page
as test_settings_cairn_tab_sp666_e2e.py does (the inlined TRC_SESSION gets `login`), and the coach
routes answer from here (page.route), shaped like api/coach.py's. A coach's invite link
(?coach_token=…#coach) lands on the tab, the token leaves the address bar, nothing is accepted until
接受 is pressed; a full coach's refusal is shown; a coach sees their students; at phone width nothing
scrolls sideways, in Chinese and in English.

Run: ./e2e.sh -k coach_tab (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SIGNED_IN = """() => { let v; Object.defineProperty(window, "TRC_SESSION", { configurable: true,
  get() { return v; }, set(x) { v = { ...x, login: { required: true, signed_in: true } }; } }); }"""
ME = {"email": "runner@example.com", "name": "跑者", "analysis": True, "can_delete": True, "reauth_ok": False}
SEE = "你的訓練紀錄、圖表、課表與賽事（唯讀）"
ACTIVE = {"link_id": 7, "name": "王教練", "status": "active", "initiated_by": "coach",
          "since": "2026-10-20T04:00:00+00:00", "can_see": SEE}
COACH = {"is_coach": True, "code": "K7MP3QXA", "invite": None, "max_students": 4, "used": 1, "full": False,
         "students": [{"link_id": 12, "student_id": 31, "name": "林小跑", "status": "pending", "initiated_by": "student",
                       "since": "2026-10-20T05:00:00+00:00", "disabled": False},
                      {"link_id": 11, "student_id": 30, "name": "陳山羊", "status": "active", "initiated_by": "coach",
                       "since": "2026-10-18T05:00:00+00:00", "disabled": False}]}


def _signed_in(page, mine=None, is_coach=False):
    page.add_init_script(f"({SIGNED_IN})()")
    page.route("**/api/v1/account/me", lambda r: r.fulfill(json=ME))
    page.route("**/api/v1/account/links", lambda r: r.fulfill(json={"clients": []}))
    page.route("**/api/v1/account/coach", lambda r: r.fulfill(json={"max": 1, "is_coach": is_coach, "coaches": mine or []}))


def _settings(page, query: str = "", hash_: str = ""):
    open_page(page, _server.PAGES["settings"] + query + hash_)
    page.wait_for_load_state("networkidle")


def test_owner_mode_has_no_coach_tab(app):
    page, _ = app
    _settings(page, "?coach_token=abc", "#coach")
    expect(page.locator("#tab-coach")).to_be_hidden()
    expect(page.locator("#coach")).to_be_hidden()
    expect(page.locator('.tabs [aria-selected="true"]')).to_have_attribute("data-tab", "personal")


def test_an_invite_link_asks_and_only_the_accept_makes_it(app):
    page, _ = app
    _signed_in(page)
    sent = []
    page.route("**/api/v1/account/coach/invite", lambda r: (sent.append(("peek", json.loads(r.request.post_data))),
                                                           r.fulfill(json={"coach": {"name": "王教練"}})))
    page.route("**/api/v1/account/coach/invite/accept",
               lambda r: (sent.append(("accept", json.loads(r.request.post_data))),
                          r.fulfill(json={"max": 1, "is_coach": False, "coaches": [ACTIVE]})))
    _settings(page, "?coach_token=tok-abc123", "#coach")
    expect(page.locator('.tabs [aria-selected="true"]')).to_have_attribute("data-tab", "coach")
    ask = page.locator("#coach-ask")
    expect(ask).to_contain_text("王教練 邀請你成為他的學員")
    assert "coach_token" not in page.url                        # out of the address bar
    assert sent == [("peek", {"token": "tok-abc123"})]          # looking is not accepting
    ask.locator("button.primary").click()
    expect(ask).to_be_hidden()
    expect(page.locator("#coach-mine")).to_contain_text("王教練")
    expect(page.locator("#coach-mine")).to_contain_text(SEE)
    expect(page.locator("#coach-find")).to_be_hidden()          # one coach at a time
    assert sent[1] == ("accept", {"token": "tok-abc123"})


def test_a_full_coach_is_refused_with_the_reason(app):
    page, watch = app
    _signed_in(page)
    page.route("**/api/v1/account/coach/invite", lambda r: r.fulfill(json={"coach": {"name": "王教練"}}))
    page.route("**/api/v1/account/coach/invite/accept", lambda r: r.fulfill(status=409, json={"detail": {
        "code": "COACH_FULL", "message": "這位教練的學員已滿（4 位），請教練先解除一位學員，或請管理員調高上限", "params": {"n": 4}}}))
    _settings(page, "?coach_token=tok-full", "#coach")
    page.locator("#coach-ask button.primary").click()
    expect(page.locator("#coach-msg")).to_contain_text("學員已滿（4 位）")
    expect(page.locator("#coach-ask button.primary")).to_be_enabled()      # still there to try later


def test_a_coach_sees_their_students_and_accepts_a_request(app):
    page, _ = app
    _signed_in(page, is_coach=True)
    accepted = []
    after = {**COACH, "used": 2, "students": [{**COACH["students"][0], "status": "active"}, COACH["students"][1]]}
    page.route(r"**/api/v1/coach", lambda r: r.fulfill(json=COACH))
    page.route("**/api/v1/coach/requests/12/accept", lambda r: (accepted.append(1), r.fulfill(json=after)))
    _settings(page, "", "#coach")
    expect(page.locator("#coach-side")).to_be_visible()
    expect(page.locator("#coach-side-head")).to_contain_text("學員 1／4 位")
    expect(page.locator("#coach-side-head code")).to_have_text("K7MP3QXA")
    rows = page.locator("#coach-students .coach-row")
    expect(rows).to_have_count(2)
    expect(rows.nth(0)).to_contain_text("等你確認")
    rows.nth(0).locator("button.primary").click()
    expect(page.locator("#coach-side-head")).to_contain_text("學員 2／4 位")
    assert accepted == [1]


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_phone_width_does_not_scroll_sideways(app, lang):
    page, _ = app
    page.set_viewport_size({"width": 375, "height": 800})
    _signed_in(page, mine=[ACTIVE], is_coach=True)
    page.route(r"**/api/v1/coach", lambda r: r.fulfill(json=COACH))
    _settings(page, "?lang=en" if lang == "en" else "", "#coach")
    expect(page.locator('.tabs [aria-selected="true"]')).to_have_attribute("data-tab", "coach")
    expect(page.locator("#coach-students .coach-row")).to_have_count(2)
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], w
    if lang == "en":
        expect(page.locator("#tab-coach")).to_have_text("Coach")
        expect(page.locator("#coach-side-head")).to_contain_text("Students 1/4")
