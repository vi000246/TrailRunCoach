"""SP-905: the English 課表 page on the demo athlete — the sessions' 目標 / 說明 / 出處 in English (the stored
zh-TW ones kept as `<key>_src`), and the dialog sends a shown 目標 / 說明 back as stored.

Run: ./e2e.sh -k sp905 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _ready(page, lang):
    open_page(page, _server.PAGES["schedule"] + f"?lang={lang}")
    page.wait_for_selector("#cal .cal, #cal .agenda", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def test_target_detail_source_in_english_and_sent_back_as_stored(app):
    page, _ = app
    _ready(page, "en")
    rows = page.evaluate("""() => D.sessions.filter((s) => s.detail_src).map((s) => ({
      uid: s.uid, detail: s.detail, detail_src: s.detail_src, target: s.target, target_src: s.target_src || null,
      source: s.source}))""")
    assert rows, "no session with a translated 說明 in the demo plan"
    # the generated texts read in English; a race's name (the user's) may stay as written
    left = [r["detail"] for r in rows if CJK.search(re.sub(r"示範山徑越野 50K|貓空越野 17K", "", r["detail"]))]
    assert not left, left
    s = page.evaluate("""() => D.sessions.find((x) => x.state === 'active' && x.day > D.today && x.detail_src
      && x.target_src && !['notice', 'race'].includes(x.kind))""")
    assert s, "no future session with a translated 目標 and 說明"
    page.evaluate("(s) => openDlg(D.sessions.find((x) => x.uid === s.uid))", s)
    expect(page.locator("#sd")).to_be_visible()
    expect(page.locator("#sd-form [name=detail]")).to_have_value(s["detail"])
    body = page.evaluate("() => weSession()")
    assert body["detail"] == s["detail_src"] and body["target"] == s["target_src"]
    page.keyboard.press("Escape")
