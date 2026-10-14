"""SP-507 R1 in a real browser on the demo athlete in an ordinary 基礎期 week (_server.base_phase): a test the box's
稍後提醒 holds is gone from the box, but the 課表's right-click 排入測試 ▸ still lists it on a day it fits, and 排入
there puts the test on the calendar (the owner: 「一樣要能排入測試」).

Run: ./e2e.sh -k sp507 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e.test_suggestions_snooze_sp507_e2e import GET, TEST_TYPES, _ctx, _open  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def base_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-sp507r1")
    base = _server.build_base(root)
    _server.base_phase(base)
    srv = _server.Server(base, root / "uvicorn.log")
    yield srv
    srv.stop()


def _days(r: dict) -> list[str]:
    return [o["day"] for o in (r.get("options") or []) + [o for t in r.get("tests") or [] for o in t.get("options") or []]]


def test_a_held_test_is_still_offered_by_the_schedule(e2e_browser, base_server):
    ctx = _ctx(e2e_browser, base_server)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _open(page)
        rows = page.evaluate(GET)["suggestions"]
        row = next((r for r in rows if r["type"] in TEST_TYPES and _days(r)), None)
        assert row, "the demo week has no test with a day in the box"
        sid, day = row["id"], _days(row)[0]
        page.locator(f'.sugbox .sg-item[data-id="{sid}"] [data-act="snooze"]').click()
        page.locator(f'.sugbox .sg-item[data-id="{sid}"]').wait_for(state="detached")
        got = page.evaluate(GET)
        assert sid not in [r["id"] for r in got["suggestions"]] and [r["id"] for r in got["snoozed_tests"]] == [sid]

        page.goto(_server.PAGES["schedule"])
        page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)
        page.wait_for_load_state("networkidle")
        for _ in range(3):                                       # the day may be in the next month / week
            if page.locator(f'#cal [data-day="{day}"]').count():
                break
            page.click("#next")
            page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)
        page.wait_for_function("() => performance.now() > 800")   # not the long-press guard's right-click
        # right-click the day → 排入測試 ▸ (in one go: any scroll closes the menu)
        label = page.evaluate("""(day) => { const c = document.querySelector(`#cal [data-day="${day}"]`), r = c.getBoundingClientRect();
          c.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 3, clientY: r.top + 3 }));
          const b = document.querySelector('.menu.ctx [data-ctx="tests"]');
          const t = b ? [b.textContent, b.className] : null; if (b) b.click(); return t; }""", day)
        assert label, "排入測試 ▸ is not in the day's menu"
        assert "muted" not in label[1], label                    # the day fits (a held 基線測試 counts too)
        opt = page.locator(f'#tp .tp-opt[data-id="{sid}"]')
        expect(opt).to_have_count(1)
        assert "off" not in (opt.get_attribute("class") or "")
        opt.locator("[data-tp-go]").click()
        expect(page.locator("#tp")).not_to_have_attribute("open", "", timeout=30_000)
        sessions = page.evaluate("() => fetch('/api/v1/overview/plan/sessions').then((r) => r.json())")
        ss = sessions if isinstance(sessions, list) else sessions.get("sessions", [])
        assert any(s.get("day") == day and s.get("kind") == "test" and s.get("state") == "active" for s in ss), day
        assert errors == []
    finally:
        ctx.close()
