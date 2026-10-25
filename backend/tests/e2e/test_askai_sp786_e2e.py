"""SP-786 「問 AI」 in a real browser on the demo athlete (owner mode, no API key): one button on the page;
opening it after a click on a card shows 「正在問：<that card>」 with its 3–5 questions, ✕ asks about the whole
page; a preset question is sent with that card's summary and the answer's parts are tagged App 規則 / AI 推論;
a ? popup ends with 「還不懂？問 AI」; 設定 › 進階設定 › AI 問答 switches it off (no button after a reload).

The model is never called: POST /api/v1/ai/ask is answered by the test (page.route), which records the body.
Run: ./e2e.sh -k sp786 (see conftest.py)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.e2e

ANSWER = {"parts": [{"type": "rule", "text": "近 4 週低強度佔 72%，App 的規則要 ≥ 80%。", "refs": ["S1"]},
                    {"type": "inference", "text": "輕鬆跑可能跑太快。", "refs": []}],
          "sources": [{"id": "S1", "kind": "status", "title": "低強度比例", "source": "Seiler 2010", "url": None}],
          "medical": False, "cached": False, "used_today": 1, "daily_limit": 30, "model": "claude-sonnet-5-5",
          "sent": {"in_view": "x", "kind": "card", "rules": 12, "weeks": 8, "todo": 3}}


def _mock_ask(page):
    sent = []

    def handle(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps(ANSWER, ensure_ascii=False))
    page.route("**/api/v1/ai/ask", handle)
    return sent


def test_one_button_the_card_in_view_presets_and_tagged_parts(app):
    page, _ = app
    sent = _mock_ask(page)
    page.goto("/api/v1/overview/page")
    fab = page.locator(".askai-fab")
    expect(fab).to_have_count(1)
    expect(fab).to_be_visible(timeout=30_000)
    # a click on a card makes it the one asked about
    card = page.locator("main section, main .card").filter(has=page.locator("h2")).first
    title = card.locator("h2").first.inner_text().split("\n")[0].strip().rstrip("?").strip()
    card.locator("h2").first.click()
    fab.click()
    dlg = page.locator(".askai-dlg")
    expect(dlg).to_be_visible()
    expect(dlg.locator("[data-askai-ctx-t]")).to_contain_text(title[:6])
    n = dlg.locator("[data-askai-presets] button").count()
    assert 3 <= n <= 5, n
    dlg.locator("[data-askai-presets] button").first.click()
    expect(dlg.locator(".askai-part.rule .askai-tag")).to_have_text("App 規則")
    expect(dlg.locator(".askai-part.inference .askai-tag")).to_have_text("AI 推論")
    expect(dlg.locator(".askai-src")).to_contain_text("Seiler 2010")
    assert sent and sent[0]["focus"]["page"] == "home" and sent[0]["focus"]["title"]
    assert "latlng" not in json.dumps(sent[0])
    # ✕ → the whole page, its own questions
    dlg.locator("[data-askai-page]").click()
    expect(dlg.locator("[data-askai-ctx-t]")).to_contain_text("整頁")
    expect(dlg.locator("[data-askai-presets] button")).to_have_count(4)
    dlg.locator("[data-askai-in]").fill("我現在的訓練狀態如何？")
    dlg.locator("[data-askai-send]").click()
    expect(dlg.locator(".askai-a")).to_have_count(1)            # the log was cleared when the focus changed
    assert sent[-1]["focus"]["kind"] == "page" and sent[-1]["question"] == "我現在的訓練狀態如何？"
    # no key on the e2e server: the dialog says where to set one
    expect(dlg.locator("[data-askai-nokey]")).to_be_visible()
    dlg.locator("[data-askai-close]").click()
    expect(fab).to_be_visible()


def test_a_question_mark_popup_ends_with_ask_ai(app):
    page, _ = app
    sent = _mock_ask(page)
    page.goto("/api/v1/app/settings#general")
    page.locator(".askai-fab").wait_for(timeout=30_000)
    page.locator("#general h2 button.q").first.click()
    pop = page.locator(".an-qpop")
    expect(pop.locator(".askai-row .askai-link")).to_have_text("還不懂？問 AI")
    pop.locator(".askai-link").click()
    dlg = page.locator(".askai-dlg")
    expect(dlg).to_be_visible()
    expect(dlg.locator("[data-askai-presets] button").first).to_have_text("這段說明是什麼意思？")
    dlg.locator("[data-askai-presets] button").first.click()
    expect(dlg.locator(".askai-a")).to_have_count(1)
    assert sent[0]["focus"]["kind"] == "hint" and "W/kg" in sent[0]["focus"]["summary"]


def test_the_settings_switch_turns_the_button_off(app):
    page, _ = app
    page.goto("/api/v1/app/settings#askai")
    sec = page.locator("#askai")
    expect(sec).to_be_visible(timeout=30_000)
    expect(page.locator("#ai-key-state")).to_have_text("未設定")
    box = page.locator("#ai-on")
    expect(box).to_be_checked()
    try:
        box.uncheck()
        page.wait_for_function("() => !document.getElementById('ai-on').checked")
        page.wait_for_timeout(500)
        page.reload()
        page.locator("#askai").wait_for(state="visible", timeout=30_000)
        expect(page.locator(".askai-fab")).to_have_count(0)
    finally:
        page.locator("#ai-on").check()
        page.wait_for_timeout(500)
