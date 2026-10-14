"""SP-515 in a real browser on the demo athlete in an ordinary 基礎期 week (_server.base_phase): 「不要」 on a test row of
the box holds that test in later weeks — the stored 「不要」 is moved to last week's id (as if pressed a week ago) and
this week's row of the same test stays out of the box, while the overview gives its 測試 to-do row back (SP-503).
The settings page lists it under 「不再建議的測試」 (English too); 恢復 brings it back to the box.

Run: ./e2e.sh -k sp515 (see conftest.py)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e.test_suggestions_snooze_sp507_e2e import GET, TEST_TYPES, TODO_TEST, _ctx, _open  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def base_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e-sp515")
    base = _server.build_base(root)
    _server.base_phase(base)
    srv = _server.Server(base, root / "uvicorn.log")
    yield srv
    srv.stop()


def to_last_week(base, sid: str) -> str:
    """The stored 「不要」 of `sid` (…:<monday>) as if pressed a week earlier, on last week's id. Returns that id."""
    con = sqlite3.connect(base / "wko5coach.db", timeout=30)
    try:
        (raw,) = con.execute("SELECT value_json FROM user_settings WHERE key = 'plan.suggestions.dismissed'").fetchone()
        dis = json.loads(raw)
        v = dis.pop(sid)
        head, mon = sid.rsplit(":", 1)
        prev = (dt.date.fromisoformat(mon) - dt.timedelta(days=7)).isoformat()
        at = dt.datetime.fromisoformat(v["at"]) - dt.timedelta(days=7)
        old = f"{head}:{prev}"
        dis[old] = {**v, "at": at.isoformat(timespec="seconds"), "week": prev}
        con.execute("UPDATE user_settings SET value_json = ? WHERE key = 'plan.suggestions.dismissed'", (json.dumps(dis),))
        con.commit()
        return old
    finally:
        con.close()


def test_no_holds_the_test_next_week_and_settings_restores_it(e2e_browser, base_server):
    ctx = _ctx(e2e_browser, base_server)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _open(page)
        rows = page.evaluate(GET)["suggestions"]
        row = next((r for r in rows if r["type"] in ("test", "baseline")), None)
        assert row, "the demo week has no CP / AeT test row in the box: " + str([r["id"] for r in rows])
        sid, kind = row["id"], row["id"].split(":")[1]
        no = page.locator(f'.sugbox .sg-item[data-id="{sid}"] [data-act="decline"]')
        assert "設定" in (no.get_attribute("title") or "")                    # says where to bring it back
        # 「不要」 on every test row (✕ where a row has none), so the overview's 測試 to-do row is back (SP-503)
        tests = [r for r in rows if r["type"] in TEST_TYPES]
        for r in tests:
            item = page.locator(f'.sugbox .sg-item[data-id="{r["id"]}"]')
            btn = item.locator('[data-act="decline"]')
            (btn if btn.count() else item.locator('[data-act="dismiss"]')).click()
            item.wait_for(state="detached")
        page.wait_for_function(TODO_TEST)

        # a week later (the 「不要」 of the weekly test rows on last week's ids): this week's rows stay out
        weekly = [r["id"] for r in tests if r["type"] in ("test", "baseline")]
        for x in weekly:
            to_last_week(base_server.base, x)
        _open(page)
        got = page.evaluate(GET)
        assert not set(weekly) & {r["id"] for r in got["suggestions"]}
        assert set(weekly) <= {d["id"] for d in got["declined"]}                   # SP-503: still the overview's
        page.wait_for_function(TODO_TEST)

        # the settings page: 不再建議的測試, 恢復
        page.goto(_server.PAGES["settings"])
        box = page.locator("#sugg-declined")
        expect(box).to_be_visible()
        names = {"cp": "CP 測試", "aet": "AeT 測試"}
        kinds = sorted({x.split(":")[1] for x in weekly})
        expect(box.locator("li")).to_have_count(len(kinds))
        expect(box.locator("li").first).to_contain_text(names[kinds[0]])
        page.goto(_server.PAGES["settings"] + "?lang=en")
        expect(page.locator("#sugg-declined h3")).to_have_text("Tests no longer suggested")
        expect(page.locator("#sugg-declined li button").first).to_have_text("Restore")
        page.goto(_server.PAGES["settings"] + "?lang=zh-TW")
        page.locator(f'#sugg-declined li button[data-restore="test:{kind}"]').click()
        expect(box.locator("li")).to_have_count(len(kinds) - 1)
        left = page.evaluate("() => fetch('/api/v1/overview/plan/suggestions/declined-tests').then((r) => r.json())")
        assert [t["kind"] for t in left["tests"]] == [k for k in kinds if k != kind]
        assert sid in [r["id"] for r in page.evaluate(GET)["suggestions"]]       # back in the box
        assert errors == []
    finally:
        ctx.close()
