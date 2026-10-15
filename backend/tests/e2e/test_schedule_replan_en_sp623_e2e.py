"""SP-623: the 依實際進度重排 dialog in English, in a real browser on the demo athlete — its frame (title,
the change labels, the apply / cancel / close buttons, the 「也重置我改過的課」 box) reads the catalog, so
nothing in it is Chinese; the Chinese page still says 依實際進度重排 and 套用 N 項變動.

Session titles and the reasons the generator gives are the plan's own text (translated on the server), so
the check is on the frame, not on every word in the list.

Run: ./e2e.sh -k sp623 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

PAPI = "/api/v1/overview/plan"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")

# the dialog's own text: everything but the plan's session titles / reasons inside the change list
FRAME = """() => {
  const cf = document.getElementById('cf'), out = [];
  const add = (el) => { const t = (el.textContent || '').trim(); if (t) out.push(t); };
  ['#cf-h', '#cf-ok', '#cf-no', '#cf-body > p', '#cf .rc-reset b', '#cf .rc-reset .meta', '#cf .chg li > b',
   '#cf .chg .add-c, #cf .chg .ch-c, #cf .chg .rm-c'].forEach((s) => cf.querySelectorAll(s).forEach(add));
  cf.querySelectorAll('[aria-label]').forEach((el) => out.push(el.getAttribute('aria-label')));
  return out;
}"""


def _ready(page, lang):
    open_page(page, _server.PAGES["schedule"] + f"?lang={lang}")
    page.wait_for_selector("#cal .cal, #cal .agenda", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _edit_one(page, minutes):
    """An edited auto session, so the reset box is live and the preview has changes to apply."""
    s = page.evaluate("""() => D.sessions.find((x) => x.state === 'active' && x.day > D.today && x.origin === 'auto'
      && !x.edited && !['notice', 'race', 'strength', 'heat_passive'].includes(x.kind))""")
    assert s, "no future auto session in the demo plan"
    r = page.evaluate("""async ([p, b]) => (await fetch(p, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(b) })).status""", [f"{PAPI}/sessions/{s['uid']}", {"minutes": minutes}])
    assert r == 200
    return s["uid"]


def _revert(page, uid):
    """Back to automatic: the e2e server is shared, and the other tests look for unedited auto sessions."""
    page.keyboard.press("Escape")
    r = page.evaluate("async (p) => (await fetch(p, { method: 'POST' })).status", f"{PAPI}/sessions/{uid}/revert")
    assert r == 200


def _open(page):
    page.locator("#rec-btn").click()
    expect(page.locator("#rc-reset")).to_be_enabled(timeout=30_000)
    page.locator("#rc-reset").check()                     # the preview with changes: 「套用 N 項變動」
    expect(page.locator("#cf-ok")).to_be_visible(timeout=30_000)


def test_replan_dialog_frame_is_english(app):
    page, _ = app
    _ready(page, "en")
    uid = _edit_one(page, 23)
    _ready(page, "en")
    _open(page)
    expect(page.locator("#cf-h")).to_have_text("Replan from progress")
    expect(page.locator("#cf-ok")).to_have_text(re.compile(r"^Apply (1 change|\d+ changes)$"))
    expect(page.locator("#cf-no")).to_have_text("Cancel")
    left = [t for t in page.evaluate(FRAME) if CJK.search(t)]
    _revert(page, uid)
    assert not left, left


def test_replan_dialog_stays_chinese_on_the_chinese_page(app):
    page, _ = app
    _ready(page, "zh-TW")
    uid = _edit_one(page, 21)
    _ready(page, "zh-TW")
    _open(page)
    expect(page.locator("#cf-h")).to_have_text("依實際進度重排")
    expect(page.locator("#cf-ok")).to_have_text(re.compile(r"^套用 \d+ 項變動$"))
    expect(page.locator("#cf-no")).to_have_text("取消")
    expect(page.locator("#cf .x")).to_have_attribute("aria-label", "關閉")
    _revert(page, uid)
