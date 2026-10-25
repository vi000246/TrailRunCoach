"""SP-839: the English 課表 page in a real browser on the demo athlete — the month title in the page's
language (「October 2026」; Chinese 「2026 年 10 月」), the cards' titles from the server in English (the
stored zh-TW ones kept as `title_src`), and a generated title sent back from the dialog stored in zh-TW.

Run: ./e2e.sh -k sp839 (see conftest.py)."""
from __future__ import annotations

import datetime as dt
import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAPI = "/api/v1/overview/plan"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _ready(page, lang):
    open_page(page, _server.PAGES["schedule"] + f"?lang={lang}")
    page.wait_for_selector("#cal .cal, #cal .agenda", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def test_month_title_and_card_titles_in_english(app):
    page, _ = app
    _ready(page, "en")
    today = dt.date.fromisoformat(page.evaluate("D.today"))
    expect(page.locator("#cal-label")).to_have_text(today.strftime("%B %Y"))
    titles = page.evaluate("D.sessions.map((s) => [s.title, s.title_src || null])")
    assert titles and not [t for t, _src in titles if CJK.search(t)], titles
    assert [src for _t, src in titles if src and CJK.search(src)], "the stored zh-TW titles are kept as title_src"
    chips = page.evaluate("[...document.querySelectorAll('.chip[data-uid] .c-t')].map((e) => e.textContent)")
    assert chips and not [t for t in chips if CJK.search(t)], chips


def test_month_title_stays_chinese(app):
    page, _ = app
    _ready(page, "zh-TW")
    today = dt.date.fromisoformat(page.evaluate("D.today"))
    expect(page.locator("#cal-label")).to_have_text(f"{today.year} 年 {today.month} 月")


def test_a_shown_title_saved_from_the_dialog_is_stored_in_zh_tw(app):
    page, _ = app
    _ready(page, "en")
    s = page.evaluate("""() => D.sessions.find((x) => x.state === 'active' && x.day > D.today && x.title_src
      && !['notice', 'race'].includes(x.kind))""")
    assert s, "no future session with a translated title in the demo plan"
    page.evaluate("(s) => openDlg(D.sessions.find((x) => x.uid === s.uid))", s)
    expect(page.locator("#sd")).to_be_visible()
    expect(page.locator("#sd-form [name=title]")).to_have_value(s["title"])
    body = page.evaluate("() => weSession()")
    assert body["title"] == s["title_src"]
    page.keyboard.press("Escape")
