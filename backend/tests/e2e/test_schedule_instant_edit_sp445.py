"""課表: an edit shows at once (SP-445).

Before: dragging a session to another day (or deleting one, or saving it in the dialog) changed
nothing on screen until the server answered — the PATCH waits for the watch to follow (a COROS
push: 5–10 s on the NAS) — then the whole calendar greyed out (`#calwrap.loading`) while it
reloaded, and only then did the session show on its new day.

Now the session moves / goes away the moment it is sent, locked with ↻, the toast says
「重新排課中」 and the calendar stays usable (no grey-out) while the server saves and the calendar
reloads quietly; afterwards the calendar is the server's. A refused edit puts the calendar back and
says why. The server's answer is held here (Playwright route) to stand in for the slow push.

Run: ./e2e.sh -k instant_edit (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SESS = "**/api/v1/overview/plan/sessions/*"


def _ready(page):
    open_page(page, _server.PAGES["schedule"])
    page.wait_for_selector("#cal .cal", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _pick(page) -> dict:
    """A future active session drawn on the calendar and a free future day of the grid."""
    info = page.evaluate("""() => {
      const busy = new Set(D.sessions.filter((s) => s.state !== 'deleted').map((s) => s.day));
      const s = D.sessions.find((x) => x.state === 'active' && x.day > D.today && !['notice', 'race'].includes(x.kind)
        && document.querySelector(`#cal .chip[data-uid="${x.uid}"][draggable="true"]`));
      const to = [...document.querySelectorAll('#cal .cell.can')].map((c) => c.dataset.day).find((d) => d > D.today && !busy.has(d));
      return { uid: s && s.uid, from: s && s.day, to };
    }""")
    assert info["uid"] and info["to"], info
    return info


def _drag(page, info):
    page.evaluate("""(i) => { const dt = new DataTransfer();
      const chip = document.querySelector(`#cal .chip[data-uid="${i.uid}"]`), cell = document.querySelector(`#cal .cell[data-day="${i.to}"]`);
      chip.dispatchEvent(new DragEvent('dragstart', { bubbles: true, dataTransfer: dt }));
      cell.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }));
      cell.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt }));
      chip.dispatchEvent(new DragEvent('dragend', { bubbles: true, dataTransfer: dt })); }""", info)


def _chip(page, day, uid):
    return page.locator(f'#cal .cell[data-day="{day}"] .chip[data-uid="{uid}"]')


def test_a_drag_moves_the_session_at_once_and_the_server_result_follows(app):
    page, _ = app
    _ready(page)
    info = _pick(page)
    held = []
    page.route(SESS, lambda r: held.append(r) if r.request.method == "PATCH" else r.continue_())
    _drag(page, info)
    # the server has not answered: the session is on its new day already, locked, 「重新排課中」
    moved = _chip(page, info["to"], info["uid"])
    expect(moved).to_be_visible(timeout=1_000)
    assert "pend" in (moved.get_attribute("class") or "")
    expect(_chip(page, info["from"], info["uid"])).to_have_count(0)
    expect(page.locator("#swap-toast")).to_contain_text("重新排課中")
    assert not page.evaluate("() => document.getElementById('calwrap').classList.contains('loading')")
    assert len(held) == 1
    held[0].continue_()
    # saved: the toast says so, the calendar is the server's (the session stays on its new day)
    expect(page.locator("#swap-toast")).to_contain_text("已更新課表", timeout=30_000)
    moved = _chip(page, info["to"], info["uid"])
    expect(moved).to_be_visible()
    assert "pend" not in (moved.get_attribute("class") or "")
    stored = page.request.get(f"/api/v1/overview/plan/calendar?start={info['to']}&end={info['to']}").json()
    assert any(s["uid"] == info["uid"] for s in stored["sessions"])
    page.unroute(SESS)


def test_a_refused_drag_puts_the_session_back_and_says_why(app):
    page, _ = app
    _ready(page)
    info = _pick(page)
    page.route(SESS, lambda r: r.fulfill(status=400, json={"detail": "測試：伺服器拒絕"})
               if r.request.method == "PATCH" else r.continue_())
    _drag(page, info)
    expect(page.locator("#swap-toast")).to_contain_text("課表已還原", timeout=10_000)
    expect(_chip(page, info["from"], info["uid"])).to_be_visible()
    expect(_chip(page, info["to"], info["uid"])).to_have_count(0)
    expect(page.locator("#sync-msg")).to_contain_text("測試：伺服器拒絕")
    page.unroute(SESS)


def test_a_delete_takes_the_session_away_at_once(app):
    page, _ = app
    _ready(page)
    info = _pick(page)
    held = []
    page.route(SESS, lambda r: held.append(r) if r.request.method == "DELETE" else r.continue_())
    # the page treats a right-click within 700 ms of CTX.at (0 = page start) as the long-press's own
    page.wait_for_function("() => performance.now() > 800")
    # right-click → 刪除 in one go (the floating 建議 box may cover the chip, and any scroll closes the menu)
    page.evaluate("""(uid) => { const c = document.querySelector(`#cal .chip[data-uid="${uid}"]`), r = c.getBoundingClientRect();
      c.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 5, clientY: r.top + 5 }));
      document.querySelector('.menu.ctx [data-ctx="del"]').click(); }""", info["uid"])
    page.locator("#cf-ok").click()
    expect(page.locator(f'#cal .chip[data-uid="{info["uid"]}"]')).to_have_count(0, timeout=1_000)
    expect(page.locator("#swap-toast")).to_contain_text("重新排課中")
    assert len(held) == 1
    held[0].continue_()
    expect(page.locator("#swap-toast")).to_contain_text("已更新課表", timeout=30_000)
    expect(page.locator(f'#cal .chip[data-uid="{info["uid"]}"]')).to_have_count(0)
    page.unroute(SESS)
