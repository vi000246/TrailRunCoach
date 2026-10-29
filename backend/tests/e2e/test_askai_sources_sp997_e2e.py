"""SP-997 設定 › 進階設定 › AI 問答 with more than one source (mock A): one card per source in the order they are
tried — the owner's local model (Ollama's qwen2.5:32b), Claude, Gemini — each with its state and 「測試」; the owner
reorders them with ↑↓; a source not set up is a dashed card with its field open. A member has their own Claude and
Gemini cards only (no local model, no ↑↓). The 問 AI dialog says when an earlier source failed (「本機模型連不上，
改用 Claude 回答」, from the answer's `tried`).

GET /api/v1/ai/settings, the order PUT, the 「測試」 POST and /api/v1/ai/ask are answered here (page.route): nothing
reaches a model or the owner's Mac. The backend's rules are test_ai_providers_sp997.py's. Run: ./e2e.sh -k sp997."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

LOCAL = {"kind": "local", "ready": True, "url": "http://studio-mac.example:11434/v1", "model": "qwen2.5:32b", "masked": None}
CLAUDE = {"kind": "anthropic", "ready": True, "source": "saved", "masked": "…OWNR", "model": "claude-sonnet-5-5"}
GEMINI = {"kind": "gemini", "ready": False, "source": "none", "masked": None, "model": "gemini-2.5-flash"}


def owner_settings(sources=(LOCAL, CLAUDE, GEMINI)) -> dict:
    claude = next((s for s in sources if s["kind"] == "anthropic"), CLAUDE)
    return {"enabled": True, "key": {"source": claude["source"], "masked": claude["masked"]}, "model": "claude-sonnet-5-5",
            "daily_limit": 30, "used_today": 5, "who": "owner", "sources": list(sources)}


def _settings(page, body: dict) -> dict:
    """GET answers `body`; a PUT {order} reorders it and answers it (recorded in seen['put'])."""
    seen = {"put": [], "test": []}
    cur = {"s": json.loads(json.dumps(body))}

    def handle(route):
        if route.request.method == "PUT":
            data = json.loads(route.request.post_data or "{}")
            seen["put"].append(data)
            if "order" in data:
                by = {s["kind"]: s for s in cur["s"]["sources"]}
                cur["s"]["sources"] = [by[k] for k in data["order"] if k in by]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(cur["s"], ensure_ascii=False))
    page.route("**/api/v1/ai/settings", handle)
    return seen


def _test_answers(page, answers: dict, seen: dict):
    def handle(route):
        kind = json.loads(route.request.post_data or "{}").get("kind")
        seen["test"].append(kind)
        code = answers.get(kind)
        if code:
            route.fulfill(status=502, content_type="application/json", body=json.dumps({"detail": {"code": code}}))
        else:
            route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
    page.route("**/api/v1/ai/settings/test", handle)


def _cards(page) -> list:
    return page.eval_on_selector_all("#ai-sources > .ai-src:not([hidden])", "els => els.map(e => e.dataset.kind)")


def test_the_owner_sees_one_card_per_source_in_order_and_moves_them(app):
    page, _ = app
    seen = _settings(page, owner_settings())
    open_page(page, "/api/v1/app/settings#askai")
    expect(page.locator("#askai")).to_be_visible(timeout=30_000)
    assert _cards(page) == ["local", "anthropic", "gemini"]
    loc, cla, gem = (page.locator(f"#ai-src-{k}") for k in ("local", "anthropic", "gemini"))
    expect(loc.locator(".ai-src-n")).to_have_text("1")
    expect(page.locator("#ai-loc-state")).to_have_text("qwen2.5:32b · http://studio-mac.example:11434/v1")
    expect(page.locator("#ai-key-state")).to_have_text("已設定（…OWNR），加密存放")
    expect(gem).to_have_class("ai-src off")                                     # not set up: dashed, its field open
    expect(page.locator("#ai-gem-row")).to_be_visible()
    expect(page.locator("#ai-loc-row")).to_be_hidden()                          # set: behind 「編輯」
    expect(page.locator("#ai-order-note")).to_be_visible()
    expect(page.locator("#ai-member-note")).to_be_hidden()
    expect(page.locator("#ai-model")).to_have_text("今天用了 5／30 次（本機模型不算）")
    expect(loc.locator(".ai-up")).to_be_disabled()
    expect(gem.locator(".ai-down")).to_be_disabled()
    # ↓ on the local model: Claude first now
    loc.locator(".ai-down").click()
    expect(cla.locator(".ai-src-n")).to_have_text("1")
    assert seen["put"] == [{"order": ["anthropic", "local", "gemini"]}]
    assert _cards(page) == ["anthropic", "local", "gemini"]


def test_each_card_tests_its_own_source(app):
    page, watch = app
    watch.allow("/api/v1/ai/settings/test")             # the 502 AI_UNREACHABLE answered on purpose below
    watch.allow("502")
    seen = _settings(page, owner_settings())
    _test_answers(page, {"local": "AI_UNREACHABLE"}, seen)
    open_page(page, "/api/v1/app/settings#askai")
    expect(page.locator("#askai")).to_be_visible(timeout=30_000)
    page.locator("#ai-loc-test").click()
    expect(page.locator("#ai-loc-test-msg")).to_have_text("連不上：那台電腦可能關機、睡眠，或網址不對")
    page.locator("#ai-key-test").click()
    expect(page.locator("#ai-key-test-msg")).to_have_text("✓ 可以用：Anthropic 接受這把 key")
    assert seen["test"] == ["local", "anthropic"]
    # 「編輯」 opens the local model's fields, filled in
    page.locator("#ai-loc-edit").click()
    expect(page.locator("#ai-loc-url")).to_have_value("http://studio-mac.example:11434/v1")
    expect(page.locator("#ai-loc-model")).to_have_value("qwen2.5:32b")


def test_a_member_has_their_own_claude_and_gemini_cards_only(app):
    page, _ = app
    member = {"enabled": True, "key": {"source": "none", "masked": None}, "model": "claude-sonnet-5-5",
              "daily_limit": 30, "used_today": 0, "who": "member",
              "sources": [dict(CLAUDE, ready=False, source="none", masked=None),
                          dict(GEMINI, ready=True, source="saved", masked="…MGEM")]}
    seen = _settings(page, member)
    open_page(page, "/api/v1/app/settings#askai")
    expect(page.locator("#askai")).to_be_visible(timeout=30_000)
    assert _cards(page) == ["anthropic", "gemini"]
    expect(page.locator("#ai-member-note")).to_be_visible()
    expect(page.locator("#ai-order-note")).to_be_hidden()
    expect(page.locator("#ai-src-anthropic .ai-src-moves")).to_be_hidden()
    expect(page.locator("#ai-gem-state")).to_have_text("已設定（…MGEM），加密存放")
    expect(page.locator("#ai-model")).to_have_text("今天用了 0／30 次")
    expect(page.locator("#ai-gem-row")).to_be_hidden()                         # set: behind 「換 key」
    page.locator("#ai-gem-change").click()
    page.locator("#ai-gem-key").fill("AIza-example-SP997-e2e-not-a-key-xxxx")
    page.locator("#ai-gem-save").click()
    page.wait_for_function("() => document.getElementById('ai-gem-row').hidden")
    assert seen["put"] == [{"gemini_key": "AIza-example-SP997-e2e-not-a-key-xxxx"}]


ANSWER = {"parts": [{"type": "rule", "text": "近 4 週低強度佔 72%，App 的規則要 ≥ 80%。", "refs": ["S1"]}],
          "sources": [{"id": "S1", "kind": "status", "title": "低強度比例", "source": "Seiler 2010", "url": None}],
          "medical": False, "cached": False, "used_today": 1, "daily_limit": 30, "model": "claude-sonnet-5-5",
          "provider": "anthropic", "tried": [{"kind": "local", "model": "qwen2.5:32b", "code": "AI_UNREACHABLE"}],
          "sent": {"in_view": None, "kind": None, "rules": 12, "weeks": 8, "todo": 3}}


def test_the_dialog_says_the_local_model_could_not_be_reached(app):
    page, _ = app
    page.route("**/api/v1/ai/ask", lambda r: r.fulfill(status=200, content_type="application/json",
                                                       body=json.dumps(ANSWER, ensure_ascii=False)))
    open_page(page, _server.PAGES["overview"])
    page.locator(".askai-fab").click(timeout=30_000)
    dlg = page.locator(".askai-dlg")
    dlg.locator("[data-askai-presets] button").first.click()
    expect(dlg.locator("[data-askai-via]")).to_have_text("本機模型連不上，改用 Claude 回答")
    # an answer from the first source has no such line
    page.unroute("**/api/v1/ai/ask")
    page.route("**/api/v1/ai/ask", lambda r: r.fulfill(status=200, content_type="application/json",
                                                       body=json.dumps({**ANSWER, "provider": "local", "tried": []},
                                                                       ensure_ascii=False)))
    dlg.locator("[data-askai-in]").fill("最近哪裡不足？")
    dlg.locator("[data-askai-send]").click()
    expect(dlg.locator(".askai-a")).to_have_count(2)
    expect(dlg.locator("[data-askai-via]")).to_have_count(1)
