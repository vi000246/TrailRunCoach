"""SP-542: on the 課表, in a real browser on the demo athlete — the week column's phase pill says why the week is
planned this way: hover (desktop) or tap (phone) shows this week's and a projected week's reasons (the recovery
week's 「the share is 推估」 line included); the English page shows them in English although the plan inputs were
built by a Chinese request first (i18n.relocalize); 提示多寡 精簡 puts them in the week reminders' 「詳細」.

Run: ./e2e.sh -k sp542 (see conftest.py)."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SCHEDULE = _server.PAGES["schedule"]
CJK = re.compile(r"[一-鿿]")


def _ready(page, q=""):
    open_page(page, SCHEDULE + q)
    page.wait_for_selector("#cal .cal, #cal .agenda", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)
    page.add_style_tag(content=".sugbox { display: none !important; }")   # (the floating 建議 box covers the column)


def _rows(page) -> list:
    """The week rows in view that have reasons: this week first, then the projected ones."""
    return page.evaluate("D.week_rows.filter((w) => (w.why || []).length)")


def _pop(page) -> str:
    pop = page.locator(".qtip-pop")
    expect(pop).to_have_count(1)
    return pop.inner_text()


def test_hover_shows_this_and_a_future_weeks_reasons(app):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    rows = _rows(page)
    today = page.evaluate("D.today")
    cur = next(w for w in rows if w["start"] <= today <= w["end"])
    fut = next(w for w in rows if w["start"] > today and w["provisional"])
    assert any("推估" in x for w in rows if w["mode_label"] == "恢復週" for x in w["why"]), rows
    for w in (cur, fut):
        pill = page.locator(f'.wk[data-week="{w["start"]}"] .sum .wkq')
        pill.scroll_into_view_if_needed()
        pill.hover()
        txt = _pop(page)
        assert "這週為什麼這樣排" in txt and all(x in txt for x in w["why"]), (txt, w)
        assert ("暫定：" in txt) == bool(w["provisional"])
        page.mouse.move(5, 5)
        expect(page.locator(".qtip-pop")).to_have_count(0)


def test_english_reasons_are_english(app):
    page, _ = app
    _ready(page, "?lang=zh-TW")                   # the inputs are built (cached) by a Chinese request first
    _ready(page, "?lang=en")
    rows = _rows(page)
    today = page.evaluate("D.today")
    cur = next(w for w in rows if w["start"] <= today <= w["end"])
    assert not [x for x in cur["why"] + [cur["mode_label"]] if CJK.search(x)], cur
    rec = [w for w in rows if w["why"][-1].startswith("No study says how much a recovery week should cut")]
    assert rec, rows
    pill = page.locator(f'.wk[data-week="{cur["start"]}"] .sum .wkq')
    pill.hover()
    txt = _pop(page)
    assert "why this week is planned this way" in txt and not CJK.search(txt), txt


def test_phone_tap_opens_and_closes(app):
    page, _ = app
    page.set_viewport_size({"width": 390, "height": 844})
    _ready(page, "?lang=zh-TW")
    page.locator(".ag-sum .wkq").click()
    txt = _pop(page)
    assert "這週為什麼這樣排" in txt
    page.locator("#cal-label").click()
    expect(page.locator(".qtip-pop")).to_have_count(0)


def test_lean_puts_the_reasons_in_details(app):
    page, ctx = app

    def lean(route):
        r = route.fetch()
        route.fulfill(response=r, body=json.dumps({**r.json(), "level": "lean"}), content_type="application/json")
    page.route("**/api/v1/plan/profile/hint-level", lean)
    _ready(page, "?lang=zh-TW")
    expect(page.locator("#cal .wkq")).to_have_count(0)
    det = page.locator("#pnotes details.pn-detail")
    expect(det).to_have_count(1)
    det.locator("summary").click()
    rows = _rows(page)
    box = det.inner_text()
    assert rows and all(x in box for w in rows for x in w["why"]), box
