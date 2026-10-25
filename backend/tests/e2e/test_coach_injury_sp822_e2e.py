"""SP-822 in a real browser on the demo athlete: the student's 「看傷病紀錄」 switch in 設定 › 教練, and a coach
viewing a student who turned it on — 傷病紀錄 back in the menu, its page open but read only (its writes
locked). As test_coach_view_sp791_e2e.py does, the inlined TRC_SESSION is made to say 代看 (the e2e server
runs in owner mode) and the student's coach API answers from here; the server-side rules are
test_coach_injury_sp822.py's.

Run: ./e2e.sh -k coach_injury (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e.test_coach_view_sp791_e2e import (  # noqa: E402
    ACTIVE, LABELS, _open, _session, _student)

pytestmark = pytest.mark.e2e

INJ_LABEL = "看傷病紀錄與每次活動的疼痛（只能看）"
LABELS3 = {**LABELS, "injury": INJ_LABEL}
PERMS = {"plan": True, "training": True, "injury": False}
INJ_PAGE = "/api/v1/app/injuries/page"
MENU_INJ = f'.appnav [href="{INJ_PAGE}"]'


def _viewing(page, perms):
    caps = (["plan.write", "push", "upload.gpx"] if perms.get("plan") else []) \
        + (["thresholds.write"] if perms.get("training") else [])
    _session(page, {"viewing": {"student_id": 30, "name": "林小跑", "perms": perms, "back": "/api/v1/app/settings#coach"},
                    "caps": sorted(caps)})


def test_the_student_turns_on_seeing_the_injury_log(app):
    page, _ = app
    sent = []
    mine = {"max": 1, "is_coach": False, "perm_labels": LABELS3, "coaches": [{**ACTIVE, "perms": PERMS}]}
    after = {**mine, "coaches": [{**ACTIVE, "perms": {**PERMS, "injury": True}}]}
    _student(page, mine)
    page.route("**/api/v1/account/coach/7/perms", lambda r: (sent.append(json.loads(r.request.post_data)),
                                                             r.fulfill(json=after)))
    _open(page, "settings", "#coach")
    expect(page.locator("#coach-mine input[data-perm]")).to_have_count(3)
    box = page.locator('#coach-mine input[data-perm="injury"]')
    expect(box).not_to_be_checked()                                    # off until the student turns it on
    expect(page.locator("#coach-mine")).to_contain_text(INJ_LABEL)
    box.check()
    expect(page.locator("#coach-msg")).to_contain_text(f"已允許教練「{INJ_LABEL}」。教練代看時看得到你的傷病紀錄，但不能改。")
    assert sent == [{"injury": True}]
    expect(page.locator('#coach-mine input[data-perm="injury"]')).to_be_checked()


def test_switched_off_the_message_says_the_coach_no_longer_sees_it(app):
    page, _ = app
    mine = {"max": 1, "is_coach": False, "perm_labels": LABELS3, "coaches": [{**ACTIVE, "perms": {**PERMS, "injury": True}}]}
    _student(page, mine)
    page.route("**/api/v1/account/coach/7/perms",
               lambda r: r.fulfill(json={**mine, "coaches": [{**ACTIVE, "perms": PERMS}]}))
    _open(page, "settings", "#coach")
    page.locator('#coach-mine input[data-perm="injury"]').uncheck()
    expect(page.locator("#coach-msg")).to_contain_text(f"已關掉「{INJ_LABEL}」，教練從現在起看不到你的傷病紀錄。")


def test_viewing_with_the_switch_on_the_injury_log_is_in_the_menu_and_read_only(app):
    page, _ = app
    _viewing(page, {"plan": False, "training": False, "injury": True})
    page.route("**/api/v1/coach/view/stop", lambda r: r.fulfill(json={"go": "/api/v1/app/settings#coach"}))
    _open(page, "overview")
    expect(page.locator(MENU_INJ)).to_have_count(1)
    expect(page.locator(".an-view")).to_contain_text("唯讀")             # the banner: still nothing to change
    page.goto(INJ_PAGE)
    page.wait_for_load_state("networkidle")
    for sel in ("#add", "#set-pat", "#set-up"):
        expect(page.locator(sel)).to_have_attribute("data-cap-locked", "1")


def test_viewing_with_the_switch_off_the_menu_has_no_injury_log(app):
    page, _ = app
    _viewing(page, {"plan": True, "training": True, "injury": False})
    _open(page, "overview")
    expect(page.locator(MENU_INJ)).to_have_count(0)


def test_the_races_add_button_follows_the_plan_switch_not_the_injury_one(app):
    """races.html has an #add too (新增賽事): the injury page's locks stay on the injury page."""
    page, _ = app
    _viewing(page, {"plan": True, "training": False, "injury": False})
    _open(page, "races")
    expect(page.locator("#add")).not_to_have_attribute("data-cap", "injury.write")
