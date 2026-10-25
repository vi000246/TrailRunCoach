"""SP-821: while a coach views a student (代看, SP-791) the 課表 page doesn't send them to 設定 to log the
student in to COROS — 設定 shows a viewing coach 生理數據 only. The status line says the student logs in
themselves; a push that failed on the student's expired login says the same, with no link. The student's
own page is unchanged.

The e2e server runs in owner mode (no 代看): the page is made to believe it the way
test_coach_view_sp791_e2e.py does, and the calendar's `coros` is set from here (page.route) so the
state doesn't depend on the machine. Run: ./e2e.sh -k sp821 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

ON = {"plan": True, "training": True}


def _viewing(page, perms=ON):
    caps = (["plan.write", "push", "upload.gpx"] if perms["plan"] else []) + (["thresholds.write"] if perms["training"] else [])
    extra = json.dumps({"viewing": {"student_id": 30, "name": "林小跑", "perms": perms, "back": "/api/v1/app/settings#coach"},
                        "caps": sorted(caps)}, ensure_ascii=False)
    page.add_init_script(f"""(() => {{ let v; Object.defineProperty(window, "TRC_SESSION", {{ configurable: true,
      get() {{ return v; }}, set(x) {{ v = {{ ...x, login: {{ required: true, signed_in: true }}, ...{extra} }}; }} }}); }})()""")


def _coros(page, authenticated: bool):
    def handle(route):
        r = route.fetch()
        body = r.json()
        body["coros"] = {**(body.get("coros") or {}), "authenticated": authenticated}
        route.fulfill(response=r, json=body)
    page.route("**/api/v1/overview/plan/calendar*", handle)


def _open(page, suffix=""):
    open_page(page, _server.PAGES["schedule"] + suffix)
    page.wait_for_load_state("networkidle")
    expect(page.locator("#sync-status")).not_to_be_empty()


def test_viewing_a_student_without_coros_has_no_login_link_but_says_the_student_logs_in(app):
    page, _ = app
    _viewing(page)
    _coros(page, False)
    _open(page)
    expect(page.locator("#login-link")).to_be_hidden()
    expect(page.locator("#sync-status")).to_contain_text("學員的 COROS 未登入，要學員自己登入後才能推送到手錶")
    expect(page.locator('.sync a[href*="settings#sync"]:visible')).to_have_count(0)


def test_a_push_that_failed_on_the_students_login_has_no_link_while_viewing(app):
    page, _ = app
    _viewing(page)
    _coros(page, True)
    _open(page)
    page.evaluate("""() => errMsg("推送", { auth: true, message: "x" })""")
    m = page.locator("#sync-msg")
    expect(m).to_contain_text("推送失敗：COROS 未登入或登入過期 · 要學員自己到設定頁重新登入")
    expect(m.locator("a")).to_have_count(0)


def test_viewing_in_english(app):
    page, _ = app
    _viewing(page)
    _coros(page, False)
    _open(page, "?lang=en")
    expect(page.locator("#sync-status")).to_contain_text("The student isn't signed in to COROS")
    expect(page.locator("#login-link")).to_be_hidden()


def test_the_students_own_page_still_links_to_settings(app):
    page, _ = app
    _coros(page, False)
    _open(page)
    link = page.locator("#login-link")
    expect(link).to_be_visible()
    expect(link).to_have_text("COROS 未登入 · 到設定頁登入 →")
    expect(link).to_have_attribute("href", "/api/v1/app/settings#sync")
    expect(page.locator("#sync-status")).to_contain_text("登入 COROS 後才能把課表送到手錶")
    page.evaluate("""() => errMsg("推送", { auth: true, message: "x" })""")
    expect(page.locator('#sync-msg a[href="/api/v1/app/settings#sync"]')).to_have_text("到設定頁重新登入 →")
