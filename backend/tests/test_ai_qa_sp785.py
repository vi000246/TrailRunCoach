"""SP-785 AI 問答, the backend (backend/ai, api/ai.py; SP-306 Q1 / Q2).

驗收:
  1. a question about the training status is answered from the app's numbers and rules, each part
     marked app rule (citing the rule's source) or model inference;
  2. only summaries are sent (no track, no coordinates, no sparkline); the API key is sealed at
     rest, masked on screen, never in a log line;
  3. a daily limit and a cache.
Also: owner only (a member, the demo), the switch, no key, a medical question, the model setting.

No real model call: the Messages API POST (qa._post) is a fake that records what was sent
(conftest._no_live_ai refuses it otherwise). The key is a fake sentinel."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from backend.ai import config as C
from backend.ai import context as X
from backend.ai import qa as Q
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    OWNER_EMAIL, app, browser, csrf, env, sign_in, sign_up, started)

FAKE_KEY = "sk-ant-test-SP785-fake-key-0123456789abcdefWXYZ"
STATUS = {
    "today": "2026-10-20",
    "phase": {"kind": "base", "label": "基礎期 第 3 週", "goal": "練有氧引擎：大量低強度、EF 往上",
              "start": "2026-10-06", "end": "2026-11-30"},
    "headline": "基礎期：低強度不夠",
    "indicators": [
        {"id": "intensity", "title": "低強度比例", "level": "watch", "text": "72%", "verdict": "低強度偏少",
         "why": "近 4 週 Zone 1–2 佔 72%（目標 ≥ 80%）", "action": "輕鬆跑壓在 AeT 以下", "source": "Seiler 2010",
         "value": 0.72, "spark": [["2026-10-01", 0.7], ["2026-10-08", 0.71]], "extra": {"secret_detail": 1}},
        {"id": "fitness", "title": "長期訓練負荷", "level": "good", "text": "CTL 52", "verdict": "穩定上升",
         "why": "每週 +2", "action": "", "source": "Banister", "value": 52, "spark": [], "extra": {}},
    ],
    "actions": [{"priority": 1, "title": "多跑輕鬆跑", "detail": "每週加一次 60 分鐘輕鬆跑", "because": ["intensity"],
                 "source": "Uphill Athlete"}],
}
WEEKS = [{"week": "2026-10-13", "hours": 6.5, "km": 48.2, "climb_m": 1800, "load_tss": 410, "sessions": 5, "days": 5}]


class FakeModel:
    """The Messages API: records each request, answers `reply` (a dict → its JSON as the text)."""

    def __init__(self, reply, status: int = 200):
        self.reply, self.status, self.calls = reply, status, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body)})
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply, ensure_ascii=False)
        return self.status, json.dumps({"content": [{"type": "text", "text": text}],
                                        "usage": {"input_tokens": 900, "output_tokens": 120}}).encode()


GOOD = {"parts": [{"type": "rule", "text": "低強度只佔 72%，app 的規則要 ≥ 80%。", "refs": ["S1"]},
                  {"type": "inference", "text": "看起來是輕鬆跑跑太快。", "refs": []},
                  {"type": "rule", "text": "一條沒有出處的「規則」", "refs": ["S99"]}],
        "medical": False}


@pytest.fixture
def owner(monkeypatch, tmp_path):
    """The owner's app (no sign-in) on a temp home; the status and the weekly totals are fixed."""
    from fastapi.testclient import TestClient
    from backend.main import build_app
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.delenv("WKO5COACH_ACCOUNTS", raising=False)
    monkeypatch.setattr(X, "_status", lambda: (json.loads(json.dumps(STATUS)), object()))
    monkeypatch.setattr(X, "_weeks", lambda ds: WEEKS)
    with TestClient(build_app(demo=False)) as cl:
        cl.home = tmp_path / "home"
        yield cl


def _model(monkeypatch, reply=GOOD, status=200) -> FakeModel:
    m = FakeModel(reply, status)
    monkeypatch.setattr(Q, "_post", m)
    return m


def _key(cl, key=FAKE_KEY):
    r = cl.put("/api/v1/ai/settings", json={"api_key": key})
    assert r.status_code == 200, r.text
    return r


# ------------------------------------------------------------------------------------ 驗收 1

def test_a_status_question_is_answered_with_app_rules_and_model_inference_marked(owner, monkeypatch):
    m = _model(monkeypatch)
    _key(owner)
    r = owner.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert [p["type"] for p in a["parts"]] == ["rule", "inference", "inference"]   # S99 was never sent
    assert a["parts"][0]["refs"] == ["S1"] and a["parts"][2]["refs"] == []
    assert a["sources"] == [{"id": "S1", "kind": "status", "title": "低強度比例", "source": "Seiler 2010", "url": None}]
    assert a["cached"] is False and a["medical"] is False and a["model"] == C.DEFAULT_MODEL
    assert a["sent"] == {"in_view": None, "kind": None, "rules": 2, "weeks": 1, "todo": 1}
    # what the model got: the rules with their ids, numbers, judgments and sources; the question
    body = m.calls[0]["body"]
    assert body["model"] == C.DEFAULT_MODEL and "rule" in body["system"] and "inference" in body["system"]
    msg = body["messages"][-1]["content"]
    ctx = json.loads(msg.split("CONTEXT:\n", 1)[1].split("\n\nQUESTION:\n", 1)[0])
    rule = ctx["app"]["rules"][0]
    assert rule == {"title": "低強度比例", "level": "watch", "value": "72%", "judgment": "低強度偏少",
                    "why": "近 4 週 Zone 1–2 佔 72%（目標 ≥ 80%）", "action": "輕鬆跑壓在 AeT 以下",
                    "source": "Seiler 2010", "id": "S1"}
    assert ctx["app"]["todo"][0]["id"] == "S3" and ctx["app"]["weeks"] == WEEKS
    assert msg.endswith("QUESTION:\n我現在的訓練狀態如何？")
    assert m.calls[0]["url"] == Q.API_URL and m.calls[0]["headers"]["x-api-key"] == FAKE_KEY


def test_the_card_in_view_is_sent_with_its_rules_and_manual_sources(owner, monkeypatch):
    m = _model(monkeypatch, {"parts": [{"type": "rule", "text": "TSB −18：負荷平衡偏低。", "refs": ["S4", "S5"]}]})
    _key(owner)
    focus = {"kind": "chart", "page": "charts", "title": "負荷平衡（10/18）", "summary": "TSB −18",
             "facts": [{"label": "TSB", "value": "−18"}],
             "rules": [{"text": "TSB < −10：疲勞累積", "source": "Coggan"}],
             "sources": [{"title": "手冊：負荷平衡", "url": "/manual/charts#form"},
                         {"title": "外部網站", "url": "https://evil.example/x"}]}
    a = owner.post("/api/v1/ai/ask", json={"question": "這張圖在說什麼？", "focus": focus}).json()
    assert a["parts"][0]["type"] == "rule"
    assert [(s["id"], s["kind"], s["url"]) for s in a["sources"]] == [("S4", "card", None), ("S5", "manual", "/manual/charts#form")]
    ctx = json.loads(m.calls[0]["body"]["messages"][-1]["content"].split("CONTEXT:\n", 1)[1].split("\n\nQUESTION", 1)[0])
    assert all("url" not in s for s in ctx["in_view"]["sources"])  # the links go to the dialog, not the model (SP-1023)
    assert a["sent"]["in_view"] == "負荷平衡（10/18）" and a["sent"]["kind"] == "chart"


def test_an_answer_that_is_not_the_json_is_one_inference_part(owner, monkeypatch):
    _model(monkeypatch, "就是一段文字，沒有照格式。")
    _key(owner)
    a = owner.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"}).json()
    assert a["parts"] == [{"type": "inference", "text": "就是一段文字，沒有照格式。", "refs": []}] and a["sources"] == []


def test_the_conversation_carries_the_earlier_turns(owner, monkeypatch):
    m = _model(monkeypatch)
    _key(owner)
    hist = [{"q": "我現在的訓練狀態如何？", "a": "低強度偏少。"}]
    assert owner.post("/api/v1/ai/ask", json={"question": "那要怎麼改？", "history": hist}).status_code == 200
    roles = [x["role"] for x in m.calls[0]["body"]["messages"]]
    assert roles == ["user", "assistant", "user"] and m.calls[0]["body"]["messages"][1]["content"] == "低強度偏少。"


# ------------------------------------------------------------------------------------ 驗收 2

def test_only_summaries_leave_no_track_no_position_no_sparkline(owner, monkeypatch):
    m = _model(monkeypatch)
    _key(owner)
    stream = ", ".join(str(100 + i) for i in range(200))
    focus = {"kind": "card", "page": "activity", "title": "10/18 越野跑 起點 24.123456, 121.654321",
             "summary": f"路線 24.987654 121.123456 心率 {stream}",
             "latlng": [[24.1, 121.2]] * 50, "gps": "x", "track": [1, 2, 3], "streams": {"hr": [1] * 100}}
    owner.post("/api/v1/ai/ask", json={"question": "這次怎麼樣？", "focus": focus})
    sent = json.dumps(m.calls[0]["body"], ensure_ascii=False)
    for bad in ("24.123456", "121.654321", "24.987654", "latlng", '"gps"', '"track"', "streams", "spark",
                "secret_detail", "150, 151, 152"):
        assert bad not in sent, bad
    assert X.COORD_MARK in sent and X.RUN_MARK in sent
    # POST /context answers exactly that, without a model call
    ctx = owner.post("/api/v1/ai/context", json={"focus": focus}).json()
    assert set(ctx["context"]["in_view"]) <= {"kind", "page", "title", "summary", "facts", "rules", "sources"}
    assert len(m.calls) == 1


def test_the_key_is_sealed_masked_and_never_logged(owner, monkeypatch, caplog):
    from backend import applog
    _model(monkeypatch)
    caplog.set_level(logging.DEBUG)
    r = _key(owner)
    assert FAKE_KEY not in r.text and r.json()["key"] == {"source": "saved", "masked": "…WXYZ"}
    raw = (owner.home / C.FILE_NAME).read_text("utf-8")
    assert FAKE_KEY not in raw and json.loads(raw)["api_key"].startswith("enc:v1:")
    g = owner.get("/api/v1/ai/settings")
    assert FAKE_KEY not in g.text and g.json()["key"]["masked"] == "…WXYZ"
    assert "ai" in owner.get("/api/v1/session").json() and FAKE_KEY not in owner.get("/api/v1/session").text
    owner.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"})
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert FAKE_KEY not in logged and "訓練狀態" not in logged          # neither the key nor the question
    assert FAKE_KEY not in applog.redact(f"oops {FAKE_KEY} x")          # a line that had it anyway
    from backend.settings import secrets
    assert secrets.ciphertext_exists()                                 # no new server key over it
    # cleared
    owner.put("/api/v1/ai/settings", json={"api_key": ""})
    assert owner.get("/api/v1/ai/settings").json()["key"] == {"source": "none", "masked": None}
    assert owner.put("/api/v1/ai/settings", json={"api_key": "short"}).status_code == 400


def test_an_env_key_wins_and_is_redacted(owner, monkeypatch, tmp_path):
    from backend import applog
    f = tmp_path / "anthropic_key"
    f.write_text(FAKE_KEY + "\n", "utf-8")
    monkeypatch.setenv(C.ENV_KEY + "_FILE", str(f))                  # a Docker secret
    s = owner.get("/api/v1/ai/settings").json()
    assert s["key"] == {"source": "env", "masked": "…WXYZ"}
    assert FAKE_KEY not in applog.redact(f"x {FAKE_KEY}")


def test_the_old_ai_api_key_column_is_not_read():
    """SP-306 Q1-4: the old athlete_settings.ai_api_key stays retired (init_db empties and drops it,
    SP-48); the new key is only the env one or the sealed file."""
    from backend.db import database
    assert "ai_api_key" in database.RETIRED_COLUMNS["athlete_settings"]
    src = (Path(C.__file__).read_text("utf-8") + Path(Q.__file__).read_text("utf-8")
           + Path(X.__file__).read_text("utf-8"))
    assert "athlete_settings" not in src.replace("athlete_settings.ai_api_key", "")


# ------------------------------------------------------------------------------------ 驗收 3

def test_the_daily_limit_and_the_cache(owner, monkeypatch):
    monkeypatch.setenv(C.ENV_LIMIT, "2")
    m = _model(monkeypatch)
    _key(owner)
    a1 = owner.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}).json()
    a2 = owner.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}).json()
    assert len(m.calls) == 1 and a1["cached"] is False and a2["cached"] is True
    assert a2["parts"] == a1["parts"] and a2["used_today"] == 1 and a2["daily_limit"] == 2
    assert owner.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"}).json()["used_today"] == 2
    r = owner.post("/api/v1/ai/ask", json={"question": "這週為什麼這樣排？"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "AI_DAILY_LIMIT" and len(m.calls) == 2
    # a cached answer is still there past the limit
    assert owner.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"}).json()["cached"] is True
    assert json.loads((owner.home / C.USAGE_NAME).read_text("utf-8"))["n"] == 2
    # past the cache's life the same question is a new call: refused too
    Q.clear_cache()
    assert owner.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"}).status_code == 429


# ------------------------------------------------------------------------------------- the rest

def test_off_no_key_empty_and_the_model_failing(owner, monkeypatch):
    m = _model(monkeypatch)
    ask = lambda q="我現在的訓練狀態如何？": owner.post("/api/v1/ai/ask", json={"question": q})  # noqa: E731
    r = ask()
    assert r.status_code == 409 and r.json()["detail"]["code"] == "AI_NO_KEY"
    _key(owner)
    assert ask("   ").json()["detail"]["code"] == "AI_EMPTY"
    owner.put("/api/v1/ai/settings", json={"enabled": False})
    assert ask().json()["detail"]["code"] == "AI_OFF" and owner.get("/api/v1/session").json()["ai"]["on"] is False
    owner.put("/api/v1/ai/settings", json={"enabled": True})
    for status, code in ((401, "AI_KEY_INVALID"), (529, "AI_BUSY"), (500, "AI_UPSTREAM")):
        m.status = status
        Q.clear_cache()
        r = ask()
        assert r.json()["detail"]["code"] == code, status
    assert m.calls and C.used_today() == 0                             # a failed call is not counted


def test_a_medical_question_is_flagged_whatever_the_model_says(owner, monkeypatch):
    _model(monkeypatch)
    _key(owner)
    assert owner.post("/api/v1/ai/ask", json={"question": "膝蓋外側痛還可以跑長跑嗎？"}).json()["medical"] is True
    assert owner.post("/api/v1/ai/ask", json={"question": "Is my knee pain a problem?"}).json()["medical"] is True
    assert owner.post("/api/v1/ai/ask", json={"question": "這週為什麼這樣排？"}).json()["medical"] is False


def test_the_model_is_a_setting(owner, monkeypatch):
    m = _model(monkeypatch)
    _key(owner)
    monkeypatch.setenv(C.ENV_MODEL, "claude-opus-5-5")
    assert owner.get("/api/v1/ai/settings").json()["model"] == "claude-opus-5-5"
    owner.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"})
    assert m.calls[0]["body"]["model"] == "claude-opus-5-5"
    monkeypatch.setenv(C.ENV_MODEL, "bad model; rm")
    assert C.model() == C.DEFAULT_MODEL


def test_a_member_is_refused_and_the_demo_has_no_route(monkeypatch, tmp_path):
    from backend import tenancy
    from backend.main import build_app
    from backend.tests.test_login_sp58 import _clean_env
    home = Path("/nonexistent")
    for t in (tenancy.Tenant(id="u9", kind=tenancy.USER, root=home, shared=home, caps=tenancy.USER_CAPS),
              tenancy.Tenant(id="demo-x", kind=tenancy.DEMO_SANDBOX, root=home, shared=home, caps=tenancy.DEMO_CAPS)):
        with tenancy.use(t):
            assert not C.allowed() and C.api_key() is None and C.enabled() is False
            with pytest.raises(C.OwnerOnly):
                C.save_key(FAKE_KEY)
            with pytest.raises(Q.AIError) as e:
                Q.ask("我現在的訓練狀態如何？")
            assert e.value.code == "AI_OWNER_ONLY"
            from fastapi import HTTPException
            from backend.api import ai as A
            with pytest.raises(HTTPException) as he:
                A.owner_only()
            assert he.value.status_code == 403
    _clean_env(monkeypatch, tmp_path)
    def paths(a):
        stack, out = list(a.routes), set()
        while stack:
            r = stack.pop()
            sub = getattr(r, "original_router", None)
            stack.extend(sub.routes if sub is not None else [])
            out.add(getattr(r, "path", ""))
        return out
    # not started: no demo data needed to list its routes
    assert not [p for p in paths(build_app(demo=True)) if p.startswith("/api/v1/ai")]
    assert "/api/v1/ai/ask" in paths(build_app(demo=False))


def test_a_signed_in_member_gets_403_and_no_button(app):  # noqa: F811
    member = browser(app, "198.51.100.31")
    sign_up(member, "g-member", "member@example.com")
    h = {"X-TRC-CSRF": csrf(member)}
    for r in (member.get("/api/v1/ai/settings"), member.put("/api/v1/ai/settings", json={"api_key": FAKE_KEY}, headers=h),
              member.post("/api/v1/ai/ask", json={"question": "我現在的訓練狀態如何？"}, headers=h),
              member.post("/api/v1/ai/context", json={}, headers=h)):
        assert r.status_code == 403 and r.json()["detail"]["code"] == "AI_OWNER_ONLY", r.text
    assert "ai" not in member.get("/api/v1/session").json()
    owner = browser(app)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    assert owner.get("/api/v1/ai/settings").status_code == 200
    assert owner.get("/api/v1/session").json()["ai"] == {"on": True, "key": False}


def test_every_ai_route_has_the_owner_check():
    from backend.api import ai as A
    for r in A.router.routes:
        deps = [d.call for d in r.dependant.dependencies]
        assert A.owner_only in deps, r.path


def test_the_prompt_asks_for_taiwan_traditional_chinese_and_no_paths():
    """SP-1023: a local model wrote Simplified Chinese and a manual path into its answer."""
    zh = Q.system_prompt("zh-TW")
    assert "台灣" in zh and "never Simplified Chinese" in zh
    assert "Never write a URL" in zh and "Never write a URL" in Q.system_prompt("en")
    assert "Simplified" not in Q.system_prompt("en")


@pytest.mark.parametrize("lang, want", [("en", "Answer in English"), ("zh-TW", "Answer in Traditional Chinese as written in Taiwan")])
def test_the_answer_language_follows_the_interface_language(owner, monkeypatch, lang, want):
    """SP-1023: the reply is in the language the user's interface is set to (?lang / cookie / account)."""
    m = _model(monkeypatch, {"parts": [{"type": "inference", "text": "ok", "refs": []}]})
    _key(owner)
    owner.post(f"/api/v1/ai/ask?lang={lang}", json={"question": "How am I doing?"})
    assert want in m.calls[0]["body"]["system"]
