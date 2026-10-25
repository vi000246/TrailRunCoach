"""SP-885 「問 AI」 in English (?lang=en) on the demo athlete: the dialog's own words (title, the asking
line, the no-key note, the questions, the input) have no Chinese left; the calculator's result is asked
about as "Prediction" (its own heading is not translated yet); a 課表 session as its title and day, not
the card's long aria-label. The model is never called (no request is sent here).
Run: ./e2e.sh -k sp885 (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e
CJK = re.compile(r"[㐀-鿿]")


def _open(page):
    page.locator(".askai-fab").click()
    dlg = page.locator(".askai-dlg")
    expect(dlg).to_be_visible()
    return dlg


def _chrome_text(dlg) -> str:
    """The dialog's own words: everything but the card's title on the asking line."""
    return dlg.evaluate("""(d) => { const c = d.cloneNode(true); c.querySelector('[data-askai-ctx-t]').remove();
                                   c.querySelector('[data-askai-in]').remove();
                                   return c.innerText + ' ' + d.querySelector('[data-askai-in]').placeholder; }""")


def test_the_dialog_speaks_english_on_a_session_and_the_calculator(app):
    page, _ = app
    open_page(page, _server.PAGES["schedule"] + "?lang=en")
    chip = page.locator("#cal .chip.sc[data-uid]").first
    expect(chip).to_be_visible(timeout=30_000)
    uid = chip.get_attribute("data-uid")
    chip.click()
    page.keyboard.press("Escape")
    dlg = _open(page)
    title = page.evaluate("(uid) => D.sessions.find((x) => x.uid === uid).title", uid)
    ctx = dlg.locator("[data-askai-ctx-t]").inner_text()
    assert ctx.startswith(title + " · ") and "Load" not in ctx, ctx      # not the card's aria-label
    assert not CJK.search(_chrome_text(dlg)), _chrome_text(dlg)
    assert dlg.locator("[data-askai-presets] button").count() == 3

    r = page.request.get("/api/v1/racepower/inputs")
    ids = [e["id"] for e in r.json().get("events") or []]
    eid = next(x for x in ("demo-50k", "demo-maokong") if x in ids)
    open_page(page, f"{_server.PAGES['racepower']}?event={eid}&lang=en")
    expect(page.locator("#event")).to_have_value(eid, timeout=30_000)
    page.click("#calc")
    expect(page.locator("#results .tiles").first).to_be_visible(timeout=90_000)
    page.locator("#results h2").first.click()
    dlg = _open(page)
    expect(dlg.locator("[data-askai-ctx-t]")).to_have_text("Prediction (Race calculator)")
    assert not CJK.search(_chrome_text(dlg)), _chrome_text(dlg)
