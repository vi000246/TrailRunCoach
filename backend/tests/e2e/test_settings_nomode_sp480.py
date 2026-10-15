"""設定: no word about 計算模式 for a user who cannot switch it (SP-480), in a real browser on the
demo athlete.

The e2e server runs in owner mode, so the sign-in mode's session is made in the page: the
inlined TRC_SESSION gets `login` (and, for the user, loses the `admin` scope) through a setter
installed before the page's scripts, and GET /config's can_switch is patched to match. Every
tab's visible text and every visible tooltip is read; a `.mode-note` is checked the moment it
enters the DOM (no flash before GET /config answers).

Run: ./e2e.sh -k nomode (see conftest.py)."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

MODE_WORDS = re.compile(r"我的算式|對照模式|計算模式|WKO5 對照|My formulas|parity mode|Calculation mode", re.I)

# a sign-in mode session made from the real one: `login` added; the user also loses `admin`
FAKE_SESSION = """(admin) => { let v; Object.defineProperty(window, "TRC_SESSION", { configurable: true,
  get() { return v; },
  set(x) { const ms = x && x.membership;
    v = { ...x, login: { required: true, signed_in: true },
          membership: ms ? { ...ms, scopes: (ms.scopes || []).filter((s) => admin || s !== "admin") } : ms }; } }); }"""
# every .mode-note as it enters the DOM: its display at that moment (before any script after it)
FLASH = """window.__flash = [];
new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes) {
  if (n.nodeType !== 1) continue;
  for (const el of [n, ...n.querySelectorAll(".mode-note")]) if (el.matches(".mode-note")) window.__flash.push(getComputedStyle(el).display);
} }).observe(document, { childList: true, subtree: true });"""


def _open(page, *, signin: bool, admin: bool, level: str | None = None):
    if level:       # SP-488: the tab note is 完整's only (ST1) — the level as GET /hint-level would answer it
        def hint_level(route):
            if route.request.method != "GET":
                return route.continue_()
            r = route.fetch()
            route.fulfill(response=r, json={**r.json(), "level": level})
        page.route("**/api/v1/plan/profile/hint-level", hint_level)
    if signin:
        page.add_init_script(f"({FAKE_SESSION})({json.dumps(admin)})")

        def config(route):
            if route.request.method != "GET":
                return route.continue_()
            r = route.fetch()
            body = r.json()
            body["can_switch"] = admin
            route.fulfill(response=r, json=body)
        page.route("**/api/v1/app/config", config)
    page.add_init_script(FLASH)
    open_page(page, _server.PAGES["settings"] + "#advanced")
    # loadMode has run: GET /config answered and the section shows / stays hidden
    page.wait_for_function("(l) => typeof tabNote === 'function' && (!l || HL === l)", arg=level)
    page.wait_for_load_state("networkidle")


def _all_visible_text(page) -> str:
    """Every tab's visible text and visible tooltips (title / aria-label) of the settings page,
    plus the nav (shell.js: the page purposes sit in the 更多 menu / the tooltips)."""
    out = []
    for tab in ("personal", "body", "data", "advanced"):
        page.locator(f'.tabs [data-tab="{tab}"]').click()
        out.append(page.evaluate("""() => {
          const vis = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
          const tips = [...document.querySelectorAll("main [title], main [aria-label]")].filter(vis)
            .map((el) => (el.getAttribute("title") || "") + " " + (el.getAttribute("aria-label") || ""));
          return document.querySelector("main").innerText + "\\n" + tips.join("\\n") + "\\n" + document.getElementById("tab-note").textContent;
        }"""))
    out.append(page.evaluate("() => [...document.querySelectorAll('.appnav [title], .appnav .an-purpose')]"
                             ".map((el) => (el.getAttribute('title') || '') + ' ' + el.textContent).join('\\n')"))
    return "\n".join(out)


def test_a_user_who_cannot_switch_reads_nothing_about_the_modes(app):
    page, _ = app
    _open(page, signin=True, admin=False)
    assert page.evaluate("document.documentElement.classList.contains('nomode')")
    flashes = page.evaluate("window.__flash")
    assert flashes and set(flashes) == {"none"}, flashes                      # hidden from the first paint
    expect(page.locator("#mode")).to_be_hidden()
    for el in page.locator(".mode-note").all():
        expect(el).to_be_hidden()
    help_ = page.evaluate("() => $('thr-q').title")
    assert help_ == page.evaluate("() => t('settings.thr.help')") and not MODE_WORDS.search(help_)
    text = _all_visible_text(page)
    assert not MODE_WORDS.findall(text), MODE_WORDS.findall(text)
    assert page.evaluate("() => t('shell.page.settings.purpose_nomode')") in text


@pytest.mark.parametrize("signin", [True, False], ids=["signin-admin", "owner-app"])
def test_the_admin_and_the_owners_app_keep_every_mode_text(app, signin):
    page, _ = app
    _open(page, signin=signin, admin=True, level="full")
    assert not page.evaluate("document.documentElement.classList.contains('nomode')")
    flashes = page.evaluate("window.__flash")
    assert flashes and "none" not in flashes, flashes
    expect(page.locator("#mode")).to_be_visible()
    # SP-488 (ST15): said once, in 計算模式
    assert page.locator(".mode-note").count() == 1
    expect(page.locator("#mode .mode-note")).to_be_visible()
    expect(page.locator("#mode .mode-note")).to_have_text(page.evaluate("() => t('settings.mode.only_mine')"))
    assert page.evaluate("() => $('thr-q').title") == page.evaluate("() => t('settings.thr.help')")
    expect(page.locator("#tab-note")).to_have_text(page.evaluate("() => t('settings.tabs.advanced_note')"))
    expect(page.locator("#mode-admin")).to_be_visible() if signin else expect(page.locator("#mode-admin")).to_be_hidden()
    text = _all_visible_text(page)
    assert page.evaluate("() => t('shell.page.settings.purpose')") in text
