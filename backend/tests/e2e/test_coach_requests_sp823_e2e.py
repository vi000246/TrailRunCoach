"""SP-823: a coach with students waiting for a yes reads it on every page (the shell's line under the nav,
like SP-792's coach_edit line), not only in 設定 › 教練: closing it hides that batch, a newer request shows
it again; answering the last one in 設定 › 教練 takes it away; never while viewing a student.

The e2e server runs in owner mode (no coaches): the page is made to believe it — the inlined TRC_SESSION
gets `login` and `coach_requests` — and the coach routes answer from here (page.route), shaped like
api/coach.py's. The session's rules are test_coach_requests_sp823.py's. Run: ./e2e.sh -k sp823."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

AT = "2026-10-24T01:00:00+00:00"
LATER = "2026-10-24T03:00:00+00:00"
COACH = {"is_coach": True, "code": "K7MP3QXA", "invite": None, "max_students": 4, "used": 0, "full": False,
         "students": [{"link_id": 12, "student_id": 31, "name": "林小跑", "status": "pending", "initiated_by": "student",
                       "since": AT, "disabled": False}]}


def _session(page, extra: dict):
    js = json.dumps(extra, ensure_ascii=False)
    page.add_init_script(f"""(() => {{ let v; Object.defineProperty(window, "TRC_SESSION", {{ configurable: true,
      get() {{ return v; }}, set(x) {{ v = {{ ...x, login: {{ required: true, signed_in: true }},
      user: {{ id: 40, name: "王教練", email: "coach@example.com", role: "user", coach: true }}, ...{js} }}; }} }}); }})()""")


def _open(page, key, suffix=""):
    open_page(page, _server.PAGES[key] + suffix)
    page.wait_for_load_state("networkidle")


def test_the_coach_reads_it_on_any_page_and_can_close_it(app):
    page, _ = app
    _session(page, {"coach_requests": {"n": 2, "at": AT}})
    _open(page, "overview")
    line = page.locator(".an-creq")
    expect(line).to_contain_text("2 位學員想加入，等你確認")
    expect(line.locator('a[href="/api/v1/app/settings#coach"]')).to_have_text("到設定 › 教練確認 →")
    _open(page, "races")                                     # every page, not only the overview
    expect(page.locator(".an-creq")).to_contain_text("2 位學員想加入")
    close = page.locator(".an-creq a[role=button]")
    expect(close).to_have_text("先關掉")
    close.click()
    expect(page.locator(".an-creq")).to_have_count(0)
    _open(page, "overview")                                  # closed: this batch stays away
    expect(page.locator(".an-creq")).to_have_count(0)


def test_a_newer_request_shows_it_again(app):
    page, _ = app
    page.add_init_script(f"""try {{ localStorage.setItem("appshell.coach_requests.seen", "{AT}"); }} catch (_) {{}}""")
    _session(page, {"coach_requests": {"n": 2, "at": LATER}})
    _open(page, "overview")
    expect(page.locator(".an-creq")).to_contain_text("2 位學員想加入")


def test_nobody_waiting_or_viewing_has_no_line(app):
    page, _ = app
    _session(page, {})
    _open(page, "overview")
    expect(page.locator(".appnav")).to_be_visible()
    expect(page.locator(".an-creq")).to_have_count(0)
    page2 = page.context.new_page()
    _session(page2, {"coach_requests": {"n": 1, "at": AT},
                     "viewing": {"student_id": 30, "name": "陳山羊", "perms": {"plan": False, "training": False},
                                 "back": "/api/v1/app/settings#coach"}, "caps": []})
    _open(page2, "overview")
    expect(page2.locator(".an-view")).to_be_visible()
    expect(page2.locator(".an-creq")).to_have_count(0)


def test_answering_the_last_one_in_settings_takes_it_away(app):
    page, _ = app
    _session(page, {"coach_requests": {"n": 1, "at": AT}})
    page.route("**/api/v1/account/me", lambda r: r.fulfill(json={"email": "coach@example.com", "name": "王教練",
                                                                 "analysis": True, "can_delete": True, "reauth_ok": False}))
    page.route("**/api/v1/account/links", lambda r: r.fulfill(json={"clients": []}))
    page.route("**/api/v1/account/coach", lambda r: r.fulfill(json={"max": 1, "is_coach": True, "coaches": []}))
    page.route(r"**/api/v1/coach", lambda r: r.fulfill(json=COACH))
    after = {**COACH, "used": 1, "students": [{**COACH["students"][0], "status": "active"}]}
    page.route("**/api/v1/coach/requests/12/accept", lambda r: r.fulfill(json=after))
    _open(page, "settings", "#coach")
    expect(page.locator(".an-creq")).to_contain_text("1 位學員想加入")
    page.locator("#coach-students .coach-row button.primary").first.click()
    expect(page.locator(".an-creq")).to_have_count(0)


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_phone_width_does_not_scroll_sideways(app, lang):
    page, _ = app
    page.set_viewport_size({"width": 375, "height": 800})
    _session(page, {"coach_requests": {"n": 1 if lang == "en" else 3, "at": AT}})
    _open(page, "overview", "?lang=en" if lang == "en" else "")
    expect(page.locator(".an-creq")).to_be_visible()
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1], w
    if lang == "en":
        expect(page.locator(".an-creq")).to_contain_text("1 student wants to join — waiting for your yes")
        expect(page.locator(".an-creq a").first).to_have_text("Answer in Settings › Coach →")
