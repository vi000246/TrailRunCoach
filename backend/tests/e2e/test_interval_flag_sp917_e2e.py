"""SP-917: 圖表分析 › 單次活動判讀 › 間歇 — 「當作間歇判讀」 shows the 間歇 cards without a reload.

The case: a run whose 間歇 page is one card, 「這次不是間歇課」 with 「當作間歇判讀」 (bouts found, no plan). The
mark is in the data fingerprint, so every card's key changes; the cards hidden before (`hide`, the per-rep
cards) were answered with that previous drawing (`stale`, render_cache SP-336), taken off the page, and their
new drawing had no card to go into — the page showed them only after a reload. Now the button's redraw
computes the cards at once ({ stale: false }), and a hidden previous drawing keeps its card as 計算中 until
the new one is in. Both are checked without opening the page again.

SP-850 Q2 = (a): the demo athlete no longer has a run that offers the button (its tempo runs were the ones
judged easy), so the server's side of the case is mocked on a real interval session of the demo — before
the mark: card 0 offers the button and the other cards are hidden; after it, a request asking for the
previous drawing (`stale=1`) gets that hidden drawing marked stale once per card, every other request the
real answer (the session's 間歇 cards). The PATCH of the mark is answered without touching the data.

Run: ./e2e.sh -k interval_flag_sp917 (see conftest.py)."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402
from backend.tests.e2e.test_interval_page_sp513_e2e import INTERVALS, VIEW, _open, _workouts  # noqa: E402

pytestmark = pytest.mark.e2e

FLAG = "當作間歇"
ACTS = "/api/v1/app/activities"


@pytest.fixture
def app(e2e_browser, e2e_server, request):
    """conftest's `app`, with service workers blocked: once the app's service worker (offline_core.js) controls
    the page, its fetches no longer pass page.route, and the mock would miss the redraw."""
    from backend.tests.e2e import _browser
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 1400, "height": 900},
                                  locale="zh-TW", timezone_id="Asia/Taipei", service_workers="block")
    ctx.set_default_timeout(20_000)
    _browser.blank_tiles(ctx, e2e_server.url)
    page = ctx.new_page()
    watch = _browser.Watch(page, e2e_server.url)
    try:
        yield page, watch
        problems = watch.problems()
        if problems:
            pytest.fail(f"{request.node.name}: browser errors\n  " + "\n  ".join(problems[:20]))
    finally:
        ctx.close()


def _session(page) -> dict:
    """A demo interval session whose 間歇 page shows its cards (card 1 not hidden)."""
    for a in _workouts(page):
        if (a.get("plan") or {}).get("kind") != "quality" and "間歇" not in str(a.get("name")):
            continue
        url = f"/api/v1/app/views/{VIEW}/dashboards/{INTERVALS}/charts/{{}}?workout={a['index']}"
        r = page.request.get(url.format(1))
        if r.ok and not r.json().get("hide"):
            return a
    pytest.fail("the demo athlete has no interval session whose 間歇 cards show")


def _scenario(page, a: dict, flagged: bool) -> dict:
    """Mock the server's side of the case for run `a` (module doc); returns the state ({"flagged", "stale"})."""
    st = {"flagged": flagged, "stale": set(), "asked": []}
    offer = {"kind": "flag_interval", "label": "當作間歇判讀", "method": "PATCH", "url": ACTS,
             "body": {"items": [{"key": f"sp917-{a['index']}"}], "add_tags": [FLAG]},
             "done": f"已標記「{FLAG}」，重新判讀中…", "reload": True}
    charts = re.compile(rf".*/dashboards/{INTERVALS}/charts/(\d+)\?(.*&)?workout={a['index']}(&.*)?$")

    def before(route, n: int, stale: bool):
        real = route.fetch().json()
        if n == 0:                                     # the 間歇判讀 card, with the button
            body = {**real, "action": offer}
        else:
            body = {"title": real.get("title"), "hide": True}
        if stale:
            body["stale"] = {"key": f"sp917-{a['index']}-{n}"}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def on_chart(route):
        n = int(charts.match(route.request.url).group(1))
        if not st["flagged"]:
            return before(route, n, False)
        st["asked"].append((n, "stale=1" in route.request.url))
        if "stale=1" in route.request.url and n not in st["stale"]:      # the previous drawing first, once a card
            st["stale"].add(n)
            return before(route, n, True)
        route.continue_()

    def on_ready(route):
        keys = re.search(r"keys=([^&]*)", route.request.url)
        ours = [k for k in (keys.group(1).split(",") if keys else []) if k.startswith("sp917-")]
        if not ours:
            return route.continue_()
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ready": ours, "failed": []}))

    def on_acts(route):
        if route.request.method != "PATCH":
            return route.continue_()
        st["flagged"] = True                          # the mark, without touching the data
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    page.route(charts, on_chart)
    page.route(re.compile(r".*/render/ready\?.*"), on_ready)
    page.route(re.compile(rf".*{re.escape(ACTS)}(\?.*)?$"), on_acts)
    return st


def _settled(page) -> dict:
    """The page as it is once no card is loading or waiting for its new drawing — never opened again."""
    page.wait_for_function("""() => { const cs = document.querySelectorAll('#grid > section.card');
        return cs.length && ![...cs].some((c) => c.querySelector('.loading') || c.classList.contains('is-stale')); }""",
                           timeout=180_000)
    return page.evaluate("""() => ({
      ids: [...document.querySelectorAll('#grid > section.card')].map((c) => c.querySelector('h2')?.firstChild?.textContent?.trim()),
      text: [...document.querySelectorAll('#grid > section.card')].map((c) => c.innerText).join('\\n') })""")


def _assert_intervals(st: dict) -> None:
    assert len(st["ids"]) > 1 and st["ids"][0] == "間歇判讀", st
    assert "當作間歇判讀" not in st["text"], st


def test_the_button_shows_the_interval_cards(app):
    page, _ = app
    a = _session(page)
    st = _scenario(page, a, flagged=False)
    _open(page, a)
    before = _settled(page)
    assert before["ids"] == ["間歇判讀"] and "當作間歇判讀" in before["text"], before
    # the redraw replaces every card: wait for the new ones before reading the page
    page.evaluate("() => document.querySelectorAll('#grid > section.card').forEach((c) => { c.dataset.old = '1'; })")
    page.locator("#grid section.card button.chip.go", has_text="當作間歇判讀").click()
    page.wait_for_function("() => !document.querySelector('#grid > section.card[data-old]')", timeout=60_000)
    assert st["flagged"]
    _assert_intervals(_settled(page))
    # the redraw computes every card now: none asked for its previous drawing (that was the button again)
    assert st["asked"] and not any(s for _n, s in st["asked"]), st["asked"]


def test_a_hidden_previous_drawing_keeps_its_card(app):
    page, _ = app
    a = _session(page)
    st = _scenario(page, a, flagged=True)            # marked: the previous drawings (hidden) come first
    # opened as the viewer always asks (?stale=1): the previous drawings first, the new ones swapped in
    open_page(page, f"{_server.PAGES['viewer']}?view={VIEW}&dash={INTERVALS}&workout={a['index']}")
    _assert_intervals(_settled(page))
    assert len(st["stale"]) > 1, st                   # the hidden drawings were really served
