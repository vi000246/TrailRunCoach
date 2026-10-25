"""SP-917: 圖表分析 › 單次活動判讀 › 間歇 — 「當作間歇判讀」 shows the 間歇 cards without a reload.

On the demo athlete: a run whose 間歇 page is one card, 「這次不是間歇課」 with 「當作間歇判讀」 (bouts found,
no plan). The mark is in the data fingerprint, so every card's key changes; the cards hidden before
(`hide`, the per-rep cards) were answered with that previous drawing (`stale`, render_cache SP-336), taken
off the page, and their new drawing had no card to go into — the page showed them only after a reload.
Now the button's redraw computes the cards at once ({ stale: false }), and a hidden previous drawing keeps
its card as 計算中 until the new one is in. Both are checked without opening the page again. The mark is
taken off afterwards (the e2e server is shared by every test).

Run: ./e2e.sh -k interval_flag_sp917 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402
from backend.tests.e2e.test_interval_page_sp513_e2e import INTERVALS, VIEW, _open, _workouts  # noqa: E402

pytestmark = pytest.mark.e2e

FLAG = "當作間歇"
ACTS = "/api/v1/app/activities"


def _all_hidden(page, url: str) -> bool:
    """Every card of the page after 間歇判讀 is hidden (the page is one card)."""
    c = 1
    while (r := page.request.get(url.format(c))).ok:
        if not r.json().get("hide"):
            return False
        c += 1
    return c > 1


def _offer(page, nth: int = 0) -> tuple[dict, dict]:
    """(the `nth` run whose 間歇判讀 card offers 「當作間歇判讀」 and whose per-rep cards are hidden, that
    action). Each test takes its own run: a marked run's drawings stay in the render cache, so marking it
    again is a hit, never stale."""
    for a in _workouts(page):
        url = f"/api/v1/app/views/{VIEW}/dashboards/{INTERVALS}/charts/{{}}?workout={a['index']}"
        r = page.request.get(url.format(0))
        act = (r.json() if r.ok else {}).get("action") or {}
        if act.get("kind") == "flag_interval" and _all_hidden(page, url):
            if not nth:
                return a, act
            nth -= 1
    pytest.fail("not enough demo runs offer 「當作間歇判讀」")


def _settled(page) -> dict:
    """The page as it is once no card is loading or waiting for its new drawing — never opened again."""
    page.wait_for_function("""() => { const cs = document.querySelectorAll('#grid > section.card');
        return cs.length && ![...cs].some((c) => c.querySelector('.loading') || c.classList.contains('is-stale')); }""",
                           timeout=180_000)
    return page.evaluate("""() => ({
      ids: [...document.querySelectorAll('#grid > section.card')].map((c) => c.querySelector('h2')?.firstChild?.textContent?.trim()),
      text: [...document.querySelectorAll('#grid > section.card')].map((c) => c.innerText).join('\\n') })""")


def _unflag(page, act: dict) -> None:
    body = {"items": act["body"]["items"], "remove_tags": [FLAG]}
    assert page.request.patch(ACTS, data=body).ok


def _assert_intervals(st: dict) -> None:
    assert len(st["ids"]) > 1 and st["ids"][0] == "間歇判讀", st
    assert "這次不是間歇課" not in st["text"] and "當作間歇判讀" not in st["text"], st


def test_the_button_shows_the_interval_cards(app):
    page, _ = app
    a, act = _offer(page)
    _open(page, a)
    st = _settled(page)                       # a previous test's mark taken off: its drawings first
    assert st["ids"] == ["間歇判讀"] and "當作間歇判讀" in st["text"], st
    try:
        # the redraw replaces every card: wait for the new ones before reading the page
        page.evaluate("() => document.querySelectorAll('#grid > section.card').forEach((c) => { c.dataset.old = '1'; })")
        page.locator("#grid section.card button.chip.go", has_text="當作間歇判讀").click()
        page.wait_for_function("() => !document.querySelector('#grid > section.card[data-old]')", timeout=60_000)
        _assert_intervals(_settled(page))
    finally:
        _unflag(page, act)


def test_a_hidden_previous_drawing_keeps_its_card(app):
    page, _ = app
    a, act = _offer(page, 1)                  # not the run the button test marked (its drawings are cached)
    _open(page, a)
    st = _settled(page)                       # the per-rep cards' drawing on record: hidden
    assert st["ids"] == ["間歇判讀"], st
    assert page.request.patch(ACTS, data=act["body"]).ok
    try:
        # opened as the viewer always asks (?stale=1): the previous drawings first, the new ones swapped in
        open_page(page, f"{_server.PAGES['viewer']}?view={VIEW}&dash={INTERVALS}&workout={a['index']}")
        _assert_intervals(_settled(page))
    finally:
        _unflag(page, act)
