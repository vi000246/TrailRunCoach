"""SP-883 (the owner 2026-10-28): with members' own keys allowed, a signed-in member sees 「問 AI」 even before
saving a key; its dialog then shows a reminder — their own Anthropic key, billed to their own account — with a
link to the key field (outlined there), no questions and nothing can be sent. Once the key is saved the same
dialog asks. With the server's switch off a member sees no button at all.

The e2e server runs in owner mode (no members): the page is made to believe it — the inlined TRC_SESSION gets
`ai: {on, key, own}` — and /api/v1/ai/* answer from here (page.route); nothing reaches a model. The backend's
rules are test_ai_own_key_sp883.py's. Run: ./e2e.sh -k sp883."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

ANSWER = {"parts": [{"type": "rule", "text": "近 4 週低強度佔 72%，App 的規則要 ≥ 80%。", "refs": ["S1"]}],
          "sources": [{"id": "S1", "kind": "status", "title": "低強度比例", "source": "Seiler 2010", "url": None}],
          "medical": False, "cached": False, "used_today": 1, "daily_limit": 30, "model": "claude-sonnet-5-5",
          "sent": {"in_view": None, "kind": None, "rules": 12, "weeks": 8, "todo": 3}}


def _settings(source: str) -> dict:
    return {"enabled": True, "key": {"source": source, "masked": "…MINE" if source == "saved" else None},
            "model": "claude-sonnet-5-5", "daily_limit": 30, "used_today": 0, "who": "member"}


def _member(page, ai, source="none"):
    """The page believes a signed-in member (`ai` as the session would give it; None: the switch off);
    GET /api/v1/ai/settings answers `source`; POST /api/v1/ai/ask is recorded and answered."""
    js = json.dumps({"ai": ai}, ensure_ascii=False)
    page.add_init_script(f"""(() => {{ let v; Object.defineProperty(window, "TRC_SESSION", {{ configurable: true,
      get() {{ return v; }}, set(x) {{ v = {{ ...x, login: {{ required: true, signed_in: true }},
      user: {{ id: 41, name: "陳會員", email: "member@example.com", role: "user" }}, ...{js} }}; }} }}); }})()""")
    page.route("**/api/v1/ai/settings", lambda r: r.fulfill(status=200, content_type="application/json",
                                                             body=json.dumps(_settings(source))))
    sent = []

    def ask(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps(ANSWER, ensure_ascii=False))
    page.route("**/api/v1/ai/ask", ask)
    return sent


def test_a_member_without_a_key_is_asked_for_it_and_can_send_nothing_until_it_is_saved(app):
    page, _ = app
    sent = _member(page, {"on": True, "key": False, "own": True})
    open_page(page, _server.PAGES["overview"])
    fab = page.locator(".askai-fab")
    expect(fab).to_be_visible(timeout=30_000)                    # the button shows before a key
    fab.click()
    dlg = page.locator(".askai-dlg")
    need = dlg.locator("[data-askai-needkey]")
    expect(need).to_be_visible()
    expect(need).to_contain_text("先設定你自己的 API key 才能問")
    expect(need).to_contain_text("費用記在你的 Anthropic 帳號")
    expect(need.locator("a")).to_have_attribute("href", "/api/v1/app/settings#ai-key-row")
    expect(dlg.locator("[data-askai-nokey]")).to_be_hidden()      # the owner's line is not shown to a member
    expect(dlg.locator("[data-askai-presets]")).to_be_hidden()
    expect(dlg.locator("[data-askai-in]")).to_be_disabled()
    expect(dlg.locator("[data-askai-send]")).to_be_disabled()
    page.evaluate("() => window.AskAI.ask('我現在的訓練狀態如何？')")   # even called directly: nothing is sent
    page.wait_for_timeout(300)
    assert sent == []
    # the key saved (the settings page's event, as when it is saved with this dialog open): the same dialog asks
    page.evaluate("() => window.dispatchEvent(new CustomEvent('askai:key', { detail: { has: true } }))")
    expect(need).to_be_hidden()
    expect(dlg.locator("[data-askai-in]")).to_be_enabled()
    dlg.locator("[data-askai-presets] button").first.click()
    expect(dlg.locator(".askai-part.rule .askai-tag")).to_have_text("App 規則")
    assert len(sent) == 1


def test_a_key_saved_elsewhere_is_seen_when_the_dialog_opens(app):
    page, _ = app
    sent = _member(page, {"on": True, "key": False, "own": True}, source="saved")   # saved in another tab
    open_page(page, _server.PAGES["overview"])
    page.locator(".askai-fab").click()
    dlg = page.locator(".askai-dlg")
    expect(dlg.locator("[data-askai-in]")).to_be_enabled()
    expect(dlg.locator("[data-askai-needkey]")).to_be_hidden()
    dlg.locator("[data-askai-in]").fill("最近哪裡不足？")
    dlg.locator("[data-askai-send]").click()
    expect(dlg.locator(".askai-a")).to_have_count(1)
    assert sent and sent[0]["question"] == "最近哪裡不足？"


def test_a_member_with_a_key_asks_at_once(app):
    page, _ = app
    sent = _member(page, {"on": True, "key": True, "own": True}, source="saved")
    open_page(page, _server.PAGES["overview"])
    page.locator(".askai-fab").click()
    dlg = page.locator(".askai-dlg")
    expect(dlg.locator("[data-askai-needkey]")).to_be_hidden()
    expect(dlg.locator("[data-askai-presets] button").first).to_be_visible()
    dlg.locator("[data-askai-presets] button").first.click()
    expect(dlg.locator(".askai-a")).to_have_count(1)
    assert len(sent) == 1


def test_the_link_lands_on_the_key_field_outlined(app):
    page, _ = app
    _member(page, {"on": True, "key": False, "own": True})
    open_page(page, "/api/v1/app/settings#ai-key-row")
    row = page.locator("#ai-key-row")
    expect(row).to_be_visible(timeout=30_000)
    expect(row).to_have_class("row link-asked")
    expect(page.locator("#ai-key")).to_be_focused()
    expect(page.locator("#ai-member-note")).to_be_visible()


def test_with_the_server_switch_off_a_member_sees_no_button(app):
    page, _ = app
    _member(page, None)
    open_page(page, _server.PAGES["overview"])
    page.wait_for_load_state("networkidle")
    expect(page.locator(".askai-fab")).to_have_count(0)
