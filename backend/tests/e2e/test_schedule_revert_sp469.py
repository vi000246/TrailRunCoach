"""課表: 還原成自動 (SP-469), in a real browser on the demo athlete.

An auto session the user edited gets 「還原成自動」 in its dialog; a deleted one shows faint on its
day (「↺ 已刪除：…」) and comes back from there; 依實際進度重排 has 「也重置我改過的課」, which
re-fetches the preview with every reverted session tagged. Each test makes its own edit / delete
through the API first (the e2e server is shared by the session's tests).

Run: ./e2e.sh -k revert (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAPI = "/api/v1/overview/plan"


def _ready(page):
    open_page(page, _server.PAGES["schedule"])
    page.wait_for_selector("#cal .cal, #cal .agenda", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _auto(page, skip=()) -> dict:
    """A future, unedited auto session of the generator (not the race / a notice / strength)."""
    s = page.evaluate("""(skip) => D.sessions.find((x) => x.state === 'active' && x.day > D.today && x.origin === 'auto'
      && !x.edited && !['notice', 'race', 'strength', 'heat_passive'].includes(x.kind) && !skip.includes(x.uid))""", list(skip))
    assert s, "no future auto session in the demo plan"
    return s


def _api(page, method, path, body=None):
    return page.evaluate("""async ([m, p, b]) => { const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json' },
      body: b ? JSON.stringify(b) : undefined }); return { status: r.status, body: await r.json() }; }""", [method, path, body])


def test_an_edited_session_reverts_from_its_dialog(app):
    page, _ = app
    _ready(page)
    s = _auto(page)
    r = _api(page, "PATCH", f"{PAPI}/sessions/{s['uid']}", {"minutes": 17})
    assert r["status"] == 200 and r["body"]["edited"], r
    _ready(page)
    page.evaluate("(u) => openDlg(findS(u))", s["uid"])
    btn = page.locator("#sd-revert")
    expect(btn).to_be_visible()
    btn.click()
    expect(page.locator("#cf")).to_contain_text("還原成自動")
    page.locator("#cf-ok").click()
    expect(page.locator("#sync-msg")).to_contain_text("已還原成自動", timeout=30_000)
    page.wait_for_function("(u) => findS(u) && !findS(u).edited", arg=s["uid"], timeout=30_000)   # reloaded
    now = page.evaluate("(u) => findS(u)", s["uid"])
    # the generator's minutes now (on the shared e2e server the stored plan may lag the generator)
    assert now["minutes"] != 17 and not now.get("revertible")


def test_a_deleted_session_comes_back_from_its_day(app):
    page, _ = app
    _ready(page)
    s = _auto(page)
    r = _api(page, "DELETE", f"{PAPI}/sessions/{s['uid']}")
    assert r["status"] == 200 and r["body"]["state"] == "deleted", r
    _ready(page)
    page.evaluate("(d) => { S.anchor = d; S.sel = d; load(); }", s["day"])     # the week of that day
    ghost = page.locator(f'[data-revert="{s["uid"]}"]')
    expect(ghost).to_be_visible(timeout=30_000)
    ghost.click()
    page.locator("#cf-ok").click()
    expect(page.locator("#sync-msg")).to_contain_text("已還原成自動", timeout=30_000)
    expect(page.locator(f'.chip[data-uid="{s["uid"]}"]')).to_be_visible()
    expect(ghost).to_have_count(0)


def test_replan_dialog_offers_to_reset_the_changed_sessions(app):
    page, _ = app
    _ready(page)
    s = _auto(page)
    assert _api(page, "PATCH", f"{PAPI}/sessions/{s['uid']}", {"minutes": 19})["status"] == 200
    _ready(page)
    page.locator("#rec-btn").click()
    box = page.locator("#rc-reset")
    expect(box).to_be_enabled(timeout=30_000)
    expect(page.locator("#cf .rc-reset")).to_contain_text("也重置我改過的課")
    box.check()
    expect(page.locator("#rc-list .rv-tag").first).to_be_visible(timeout=30_000)
    page.locator("#cf-ok").click()
    expect(page.locator("#sync-msg")).to_contain_text("還原成自動", timeout=30_000)
    page.wait_for_function("(u) => findS(u) && !findS(u).edited", arg=s["uid"], timeout=30_000)   # reloaded
    assert page.evaluate("(u) => findS(u)", s["uid"])["minutes"] != 19
