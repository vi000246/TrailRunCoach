"""SP-997 AI 問答 from more than one source, in an order (backend/ai/providers.py, config.sources).

驗收 (the backend part):
  1. the owner sets up a local model (an OpenAI-compatible endpoint, e.g. Qwen on the Mac), the
     Anthropic key and a Gemini key, and their order; a question tries them in that order and a
     source that can't be reached, refuses its key or answers nothing usable hands over to the next;
  2. the Gemini key is sealed like the Anthropic one (SP-883): only its last 4 shown, never in a log;
     each source has a 「測試」 that bills nothing.
Also: with only the Anthropic key nothing changes (SP-785's call, byte for byte); a member's chain is
their own key only and they can't set the owner's sources; a local answer is not counted against the
daily limit and still answers past it; the Grafana line names the provider.

No real model anywhere: every POST / GET goes to a fake that routes by URL (conftest._no_live_ai
refuses otherwise). The keys are fake sentinels; the local endpoint is a made-up host never dialled."""
from __future__ import annotations

import json
import logging

import pytest

from backend.ai import config as C
from backend.ai import providers as P
from backend.ai import qa as Q
from backend.tests.test_ai_qa_sp785 import FAKE_KEY, GOOD, _key, owner  # noqa: F401 — fixtures
from backend.tests.test_ai_own_key_sp883 import MEMBER_KEY, _member, site  # noqa: F401 — fixtures
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    OWNER_EMAIL, app, browser, csrf, env, sign_in, sign_up, started)

GEMINI_KEY = "AIza" + "example-SP997-gemini-key-xxxxxxxxGEMI"     # fake, split so scanners see it is not a key
LOCAL_URL = "http://studio-mac.example:11434"
LOCAL_BASE = LOCAL_URL + "/v1"
LOCAL_MODEL = "qwen2.5:32b"
Q_STATUS = {"question": "我現在的訓練狀態如何？"}
TEXT = json.dumps(GOOD, ensure_ascii=False)


class Net:
    """Every source's endpoint, faked. `say[kind]`: (status, body) or an exception to raise."""

    def __init__(self):
        self.calls: list = []
        self.say = {
            "anthropic": (200, {"content": [{"type": "text", "text": TEXT}],
                                "usage": {"input_tokens": 900, "output_tokens": 120}}),
            "gemini": (200, {"candidates": [{"content": {"role": "model", "parts": [
                {"text": "thinking about it", "thought": True}, {"text": TEXT}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 800, "candidatesTokenCount": 100, "thoughtsTokenCount": 50}}),
            "local": (200, {"choices": [{"message": {"role": "assistant",
                                                     "content": "<think>先看規則 {S1}</think>\n" + TEXT}}],
                            "usage": {"prompt_tokens": 950, "completion_tokens": 130}}),
        }
        self.models = {"anthropic": (200, {"data": []}), "gemini": (200, {"name": "models/x"}),
                       "local": (200, {"object": "list", "data": [{"id": LOCAL_MODEL}, {"id": "qwen2.5:7b"}]})}

    @staticmethod
    def kind(url: str) -> str:
        if url.startswith("https://api.anthropic.com/"):
            return "anthropic"
        if url.startswith(P.GEMINI_BASE):
            return "gemini"
        assert url.startswith(LOCAL_BASE), url
        return "local"

    @staticmethod
    def _answer(v):
        if isinstance(v, Exception):
            raise v
        status, body = v
        return status, body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()

    def post(self, url, headers, body, timeout):
        k = self.kind(url)
        self.calls.append({"kind": k, "url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        return self._answer(self.say[k])

    def get(self, url, headers, timeout):
        k = self.kind(url)
        self.calls.append({"kind": k, "url": url, "headers": headers, "timeout": timeout, "get": True})
        return self._answer(self.models[k])

    def kinds(self) -> list:
        return [c["kind"] for c in self.calls]


@pytest.fixture
def net(monkeypatch) -> Net:
    n = Net()
    monkeypatch.setattr(Q, "_post", n.post)
    monkeypatch.setattr(Q, "_get", n.get)
    return n


def _all_three(cl):
    _key(cl)
    r = cl.put("/api/v1/ai/settings", json={"gemini_key": GEMINI_KEY,
                                            "local": {"url": LOCAL_URL, "model": LOCAL_MODEL}})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------------------------ 驗收 1

def test_the_local_model_is_tried_first_and_answers_without_being_counted(owner, net):
    s = _all_three(owner)
    assert [x["kind"] for x in s["sources"]] == ["local", "anthropic", "gemini"]       # the default order
    assert s["sources"][0] == {"kind": "local", "ready": True, "url": LOCAL_BASE, "model": LOCAL_MODEL, "masked": None}
    r = owner.post("/api/v1/ai/ask", json=Q_STATUS)
    assert r.status_code == 200, r.text
    a = r.json()
    assert net.kinds() == ["local"] and a["provider"] == "local" and a["model"] == LOCAL_MODEL
    assert a["tried"] == [] and [p["type"] for p in a["parts"]] == ["rule", "inference", "inference"]
    assert a["used_today"] == 0                                         # free: not counted
    # an OpenAI-compatible request: the system prompt first, then the question with the context
    c = net.calls[0]
    assert c["url"] == LOCAL_BASE + "/chat/completions" and c["body"]["model"] == LOCAL_MODEL
    assert c["body"]["messages"][0]["role"] == "system" and "inference" in c["body"]["messages"][0]["content"]
    assert c["body"]["messages"][-1]["content"].endswith("QUESTION:\n我現在的訓練狀態如何？")
    assert c["body"]["stream"] is False and "authorization" not in c["headers"]
    assert c["timeout"] == (P.LOCAL_CONNECT_S, P.LOCAL_READ_S)            # asleep → fails fast


@pytest.mark.parametrize("failure, code", [
    (ConnectionRefusedError("the Mac is asleep"), "AI_UNREACHABLE"),
    (TimeoutError("timed out"), "AI_UNREACHABLE"),
    ((404, {"error": "model 'qwen2.5:32b' not found"}), "AI_NO_MODEL"),
    ((500, {"error": "llama runner process has terminated"}), "AI_UPSTREAM"),
    ((200, b"<html>not the API</html>"), "AI_UPSTREAM"),
    ((200, {"choices": [{"message": {"content": "<think>…</think>   "}}]}), "AI_UPSTREAM"),   # nothing usable
])
def test_a_local_model_that_fails_hands_over_to_the_next(owner, net, failure, code):
    _all_three(owner)
    net.say["local"] = failure
    a = owner.post("/api/v1/ai/ask", json=Q_STATUS).json()
    assert net.kinds() == ["local", "anthropic"] and a["provider"] == "anthropic"
    assert a["tried"] == [{"kind": "local", "model": LOCAL_MODEL, "code": code}]
    assert a["used_today"] == 1 and a["model"] == C.DEFAULT_MODEL
    assert net.calls[1]["headers"]["x-api-key"] == FAKE_KEY


def test_the_order_is_the_owner_s_and_a_refused_key_moves_on(owner, net):
    _all_three(owner)
    r = owner.put("/api/v1/ai/settings", json={"order": ["gemini", "local"]})
    assert [x["kind"] for x in r.json()["sources"]] == ["gemini", "local", "anthropic"]   # the rest go last
    net.say["gemini"] = (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                         "details": [{"reason": "API_KEY_INVALID"}]}})
    net.say["local"] = ConnectionRefusedError()
    a = owner.post("/api/v1/ai/ask", json=Q_STATUS).json()
    assert net.kinds() == ["gemini", "local", "anthropic"] and a["provider"] == "anthropic"
    assert [t["code"] for t in a["tried"]] == ["AI_KEY_INVALID", "AI_UNREACHABLE"]
    for bad in (["gemini", "gemini"], ["openai"], "local", [1]):
        assert owner.put("/api/v1/ai/settings", json={"order": bad}).json()["detail"]["code"] == "BAD_ORDER"


def test_every_source_failing_answers_the_last_error_and_counts_nothing(owner, net):
    _all_three(owner)
    net.say["local"] = ConnectionRefusedError()
    net.say["anthropic"] = (400, {"error": {"message": "Your credit balance is too low"}})
    net.say["gemini"] = (429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
    r = owner.post("/api/v1/ai/ask", json=Q_STATUS)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "AI_BUSY"
    assert net.kinds() == ["local", "anthropic", "gemini"]
    assert owner.get("/api/v1/ai/settings").json()["used_today"] == 0


def test_gemini_is_called_with_its_own_rest_api(owner, net):
    owner.put("/api/v1/ai/settings", json={"gemini_key": GEMINI_KEY})
    a = owner.post("/api/v1/ai/ask", json=Q_STATUS).json()
    assert a["provider"] == "gemini" and a["model"] == C.DEFAULT_GEMINI_MODEL and a["used_today"] == 1
    assert [p["type"] for p in a["parts"]] == ["rule", "inference", "inference"]     # the thought part is left out
    c = net.calls[0]
    assert c["url"] == f"{P.GEMINI_BASE}/models/{C.DEFAULT_GEMINI_MODEL}:generateContent"
    assert c["headers"]["x-goog-api-key"] == GEMINI_KEY and GEMINI_KEY not in c["url"]
    b = c["body"]
    assert "inference" in b["systemInstruction"]["parts"][0]["text"]
    assert b["contents"][-1]["role"] == "user" and b["contents"][-1]["parts"][0]["text"].endswith("訓練狀態如何？")
    assert b["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_turns_are_user_and_model(owner, net, monkeypatch):
    monkeypatch.setenv(C.ENV_GEMINI_KEY, GEMINI_KEY)
    monkeypatch.setenv(C.ENV_GEMINI_MODEL, "gemini-3-flash")
    hist = [{"q": "上一題", "a": "上一個回答"}]
    owner.post("/api/v1/ai/ask", json={**Q_STATUS, "history": hist})
    b = net.calls[0]
    assert b["url"].endswith("/models/gemini-3-flash:generateContent")
    assert [x["role"] for x in b["body"]["contents"]] == ["user", "model", "user"]


def test_with_only_the_anthropic_key_the_call_is_as_before(owner, net):
    _key(owner)
    a = owner.post("/api/v1/ai/ask", json=Q_STATUS).json()
    assert net.kinds() == ["anthropic"] and a["provider"] == "anthropic" and a["tried"] == []
    c = net.calls[0]
    assert c["url"] == Q.API_URL and c["timeout"] == Q.TIMEOUT_S
    assert set(c["body"]) == {"model", "max_tokens", "system", "messages"} and c["body"]["max_tokens"] == Q.MAX_TOKENS
    s = owner.get("/api/v1/ai/settings").json()["sources"]
    assert [(x["kind"], x["ready"]) for x in s] == [("local", False), ("anthropic", True), ("gemini", False)]


def test_past_the_daily_limit_only_the_local_model_answers(owner, net, monkeypatch):
    monkeypatch.setenv(C.ENV_LIMIT, "1")
    _all_three(owner)
    owner.put("/api/v1/ai/settings", json={"order": ["anthropic", "local", "gemini"]})
    assert owner.post("/api/v1/ai/ask", json=Q_STATUS).json()["provider"] == "anthropic"    # 1 of 1
    a = owner.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"}).json()
    assert a["provider"] == "local" and a["used_today"] == 1
    net.say["local"] = ConnectionRefusedError()
    r = owner.post("/api/v1/ai/ask", json={"question": "下週要怎麼練？"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "AI_UNREACHABLE"   # never the paid ones
    assert net.kinds() == ["anthropic", "local", "local"]
    owner.put("/api/v1/ai/settings", json={"local": None})
    r = owner.post("/api/v1/ai/ask", json={"question": "下週要怎麼練？"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "AI_DAILY_LIMIT"


@pytest.mark.parametrize("url, base", [
    ("http://192.168.1.20:11434", "http://192.168.1.20:11434/v1"),
    ("http://studio-mac.tail1234.ts.net:1234/v1/", "http://studio-mac.tail1234.ts.net:1234/v1"),
    ("https://mac.example/llm/v1/chat/completions", "https://mac.example/llm/v1"),
    ("http://[fd7a:115c::5]:11434", "http://[fd7a:115c::5]:11434/v1"),
])
def test_the_local_url_is_normalised(url, base):
    assert P.local_base(url) == base


@pytest.mark.parametrize("local", [
    {"url": "ftp://mac:11434", "model": LOCAL_MODEL},
    {"url": "http://user:pw@mac:11434", "model": LOCAL_MODEL},
    {"url": "http://mac:99999", "model": LOCAL_MODEL},
    {"url": "http://mac:11434/v1?x=1", "model": LOCAL_MODEL},
    {"url": "mac:11434", "model": LOCAL_MODEL},
    {"url": LOCAL_URL, "model": ""},
    {"url": LOCAL_URL, "model": "qwen 3"},
    {"url": LOCAL_URL},
    "http://mac:11434",
])
def test_a_bad_local_endpoint_is_refused(owner, local):
    r = owner.put("/api/v1/ai/settings", json={"local": local})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "BAD_LOCAL"
    assert owner.get("/api/v1/ai/settings").json()["sources"][0]["ready"] is False


def test_a_local_endpoint_may_have_a_key_sealed_and_sent_as_bearer(owner, net, caplog):
    from backend import applog
    caplog.set_level(logging.DEBUG)
    local_key = "lm-" + "example-SP997-local-endpoint-key-LOCL"
    r = owner.put("/api/v1/ai/settings", json={"local": {"url": LOCAL_URL, "model": LOCAL_MODEL, "api_key": local_key}})
    assert r.status_code == 200 and local_key not in r.text and r.json()["sources"][0]["masked"] == "…LOCL"
    raw = (owner.home / C.FILE_NAME).read_text("utf-8")
    assert local_key not in raw and json.loads(raw)["local_key"].startswith("enc:v1:")
    owner.post("/api/v1/ai/ask", json=Q_STATUS)
    assert net.calls[0]["headers"]["authorization"] == "Bearer " + local_key
    assert local_key not in "\n".join(rec.getMessage() for rec in caplog.records)
    assert local_key not in applog.redact(f"x {local_key}")
    # changing the model keeps the key; clearing the endpoint drops both
    owner.put("/api/v1/ai/settings", json={"local": {"url": LOCAL_URL, "model": "qwen2.5:7b"}})
    assert "local_key" in json.loads((owner.home / C.FILE_NAME).read_text("utf-8"))
    owner.put("/api/v1/ai/settings", json={"local": None})
    d = json.loads((owner.home / C.FILE_NAME).read_text("utf-8"))
    assert "local" not in d and "local_key" not in d


# ------------------------------------------------------------------------------------ 驗收 2

def test_the_gemini_key_is_sealed_masked_and_never_logged(owner, net, caplog):
    from backend import applog
    from backend.settings import secrets
    caplog.set_level(logging.DEBUG)
    r = _all_three(owner)
    assert GEMINI_KEY not in json.dumps(r)
    g = [x for x in r["sources"] if x["kind"] == "gemini"][0]
    assert g == {"kind": "gemini", "ready": True, "source": "saved", "masked": "…GEMI", "model": C.DEFAULT_GEMINI_MODEL}
    raw = (owner.home / C.FILE_NAME).read_text("utf-8")
    assert GEMINI_KEY not in raw and json.loads(raw)["gemini_key"].startswith("enc:v1:")
    net.say["local"] = ConnectionRefusedError()
    net.say["anthropic"] = (401, {})
    owner.post("/api/v1/ai/ask", json=Q_STATUS)
    assert net.kinds() == ["local", "anthropic", "gemini"]
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert GEMINI_KEY not in logged and FAKE_KEY not in logged and "訓練狀態" not in logged
    assert GEMINI_KEY not in applog.redact(f"oops {GEMINI_KEY} x")
    assert secrets.ciphertext_exists()
    assert GEMINI_KEY not in repr(C.sources())                         # a Source's repr leaves the key out
    owner.put("/api/v1/ai/settings", json={"gemini_key": ""})
    assert "gemini_key" not in json.loads((owner.home / C.FILE_NAME).read_text("utf-8"))
    assert owner.put("/api/v1/ai/settings", json={"gemini_key": "short"}).json()["detail"]["code"] == "BAD_KEY"


def test_an_env_gemini_key_wins_and_is_redacted(owner, net, monkeypatch):
    from backend import applog
    monkeypatch.setenv(C.ENV_GEMINI_KEY, GEMINI_KEY)
    g = owner.get("/api/v1/ai/settings").json()["sources"][2]
    assert g["source"] == "env" and g["masked"] == "…GEMI"
    assert GEMINI_KEY not in applog.redact(f"x {GEMINI_KEY}")


def test_each_source_has_a_test_that_bills_nothing(owner, net):
    _all_three(owner)
    for kind in ("local", "anthropic", "gemini"):
        r = owner.post("/api/v1/ai/settings/test", json={"kind": kind})
        assert r.status_code == 200 and r.json() == {"ok": True}, (kind, r.text)
    assert all(c.get("get") for c in net.calls)                         # GETs only: no tokens
    assert [c["url"] for c in net.calls] == [LOCAL_BASE + "/models", Q.API_MODELS,
                                             f"{P.GEMINI_BASE}/models/{C.DEFAULT_GEMINI_MODEL}"]
    assert net.calls[0]["timeout"] == (P.LOCAL_CONNECT_S, P.LOCAL_TEST_READ_S)
    assert owner.post("/api/v1/ai/settings/test").json() == {"ok": True}      # no kind: the Anthropic key
    assert net.calls[-1]["url"] == Q.API_MODELS
    assert owner.get("/api/v1/ai/settings").json()["used_today"] == 0


@pytest.mark.parametrize("kind, answer, code", [
    ("local", ConnectionRefusedError(), "AI_UNREACHABLE"),
    ("local", (200, {"data": [{"id": "llama3"}]}), "AI_NO_MODEL"),        # reachable, no such model
    ("local", (401, {}), "AI_KEY_INVALID"),
    ("gemini", (400, {"error": {"message": "API key not valid. Please pass a valid API key."}}), "AI_KEY_INVALID"),
    ("gemini", (404, {}), "AI_NO_MODEL"),
    ("gemini", (429, {}), "AI_BUSY"),
])
def test_a_source_s_test_says_what_is_wrong(owner, net, kind, answer, code):
    _all_three(owner)
    net.models[kind] = answer
    r = owner.post("/api/v1/ai/settings/test", json={"kind": kind})
    assert r.json()["detail"]["code"] == code


def test_testing_a_source_not_set_up_says_no_key(owner, net):
    _key(owner)
    for kind, code in (("gemini", "AI_NO_KEY"), ("local", "AI_NO_KEY"), ("openai", "BAD_REQUEST")):
        assert owner.post("/api/v1/ai/settings/test", json={"kind": kind}).json()["detail"]["code"] == code
    assert not net.calls


# ------------------------------------------------------------------------------------ members

MEMBER_GEMINI = "AIza" + "example-SP997-member-gemini-xxxxxxxMGEM"


def _owner_with_everything(site, monkeypatch):
    """The owner (signed in) with a local model, an env Gemini key and an env Anthropic key."""
    owner_cl = browser(site)
    sign_in(owner_cl, "g-owner", OWNER_EMAIL)
    owner_cl.h = {"X-TRC-CSRF": csrf(owner_cl)}
    monkeypatch.setenv(C.ENV_KEY, FAKE_KEY)
    monkeypatch.setenv(C.ENV_GEMINI_KEY, GEMINI_KEY)
    r = owner_cl.put("/api/v1/ai/settings", json={"local": {"url": LOCAL_URL, "model": LOCAL_MODEL}}, headers=owner_cl.h)
    assert r.status_code == 200, r.text
    return owner_cl


def test_a_member_cannot_set_the_local_model_or_the_order_and_never_gets_the_owner_s(site, net, monkeypatch):
    _owner_with_everything(site, monkeypatch)
    mem = _member(site)
    mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h)
    for body in ({"local": {"url": LOCAL_URL, "model": LOCAL_MODEL}}, {"order": ["anthropic"]},
                 {"gemini_key": MEMBER_GEMINI, "local": None}):          # nothing written, the Gemini key either
        r = mem.put("/api/v1/ai/settings", json=body, headers=mem.h)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "AI_OWNER_ONLY"
    s = mem.get("/api/v1/ai/settings", headers=mem.h).json()["sources"]
    assert s == [{"kind": "anthropic", "ready": True, "source": "saved", "masked": "…MINE", "model": C.DEFAULT_MODEL},
                 {"kind": "gemini", "ready": False, "source": "none", "masked": None, "model": C.DEFAULT_GEMINI_MODEL}]
    a = mem.post("/api/v1/ai/ask", json=Q_STATUS, headers=mem.h).json()
    assert a["provider"] == "anthropic" and net.kinds() == ["anthropic"]               # not the owner's local model
    assert net.calls[0]["headers"]["x-api-key"] == MEMBER_KEY
    assert mem.post("/api/v1/ai/settings/test", json={"kind": "local"}, headers=mem.h).json()["detail"]["code"] == "AI_NO_KEY"
    assert mem.post("/api/v1/ai/settings/test", json={"kind": "gemini"}, headers=mem.h).json()["detail"]["code"] == "AI_NO_KEY"


def test_a_member_s_own_gemini_key_is_sealed_masked_and_second_to_their_claude_key(site, net, monkeypatch, caplog):
    """SP-997 Q2-2: a member may fill their own Gemini key — sealed in their own folder, last 4 shown,
    never logged; their order is the keys they filled (Claude, then Gemini)."""
    from backend import applog
    from backend.tests.test_ai_own_key_sp883 import _files
    caplog.set_level(logging.DEBUG)
    _owner_with_everything(site, monkeypatch)
    mem = _member(site)
    assert mem.get("/api/v1/session").json()["ai"] == {"on": True, "key": False, "own": True}
    r = mem.put("/api/v1/ai/settings", json={"gemini_key": MEMBER_GEMINI}, headers=mem.h)
    assert r.status_code == 200 and MEMBER_GEMINI not in r.text
    assert r.json()["sources"][1] == {"kind": "gemini", "ready": True, "source": "saved", "masked": "…MGEM",
                                      "model": C.DEFAULT_GEMINI_MODEL}
    assert mem.get("/api/v1/session").json()["ai"] == {"on": True, "key": True, "own": True}   # Gemini alone is enough
    [f] = _files(C.FILE_NAME)
    raw = f.read_text("utf-8")
    assert MEMBER_GEMINI not in raw and json.loads(raw)["gemini_key"].startswith("enc:v1:")
    # Gemini alone: their key, never the owner's env Gemini key; counted under their name
    a = mem.post("/api/v1/ai/ask", json=Q_STATUS, headers=mem.h).json()
    assert a["provider"] == "gemini" and a["used_today"] == 1
    assert net.calls[-1]["headers"]["x-goog-api-key"] == MEMBER_GEMINI
    # with their Claude key too: Claude first, Gemini when Claude fails
    mem.put("/api/v1/ai/settings", json={"api_key": MEMBER_KEY}, headers=mem.h)
    net.calls.clear()
    net.say["anthropic"] = (400, {"error": {"message": "Your credit balance is too low"}})
    a = mem.post("/api/v1/ai/ask", json={"question": "最近哪裡不足？"}, headers=mem.h).json()
    assert net.kinds() == ["anthropic", "gemini"] and a["provider"] == "gemini"
    assert a["tried"] == [{"kind": "anthropic", "model": C.DEFAULT_MODEL, "code": "AI_NO_CREDIT"}]
    assert mem.post("/api/v1/ai/settings/test", json={"kind": "gemini"}, headers=mem.h).json() == {"ok": True}
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert MEMBER_GEMINI not in logged and MEMBER_KEY not in logged
    # cleared: gone from their file
    mem.put("/api/v1/ai/settings", json={"gemini_key": ""}, headers=mem.h)
    assert "gemini_key" not in json.loads(f.read_text("utf-8"))


def test_a_member_s_gemini_key_is_in_their_request_s_log_mask_only(site, monkeypatch):
    import contextvars
    from backend import applog
    from backend.settings import env_secrets
    seen = []
    n = Net()

    def model(url, headers, body, timeout):           # runs inside the member's request
        seen.append(applog.redact(f"upstream said key={MEMBER_GEMINI}"))
        return n.post(url, headers, body, timeout)
    monkeypatch.setattr(Q, "_post", model)
    mem = _member(site)
    mem.put("/api/v1/ai/settings", json={"gemini_key": MEMBER_GEMINI}, headers=mem.h)
    assert mem.post("/api/v1/ai/ask", json=Q_STATUS, headers=mem.h).status_code == 200
    assert len(seen) == 1 and MEMBER_GEMINI not in seen[0]
    assert MEMBER_GEMINI not in contextvars.Context().run(env_secrets.values)       # not carried over


# ------------------------------------------------------------------------------------ Grafana

def test_the_grafana_line_names_the_provider_and_a_local_call_costs_nothing(owner, net, monkeypatch, tmp_path):
    from backend import ops_daily
    from backend.ai import spend as S
    where = tmp_path / "logs" / "ops"
    monkeypatch.setattr(ops_daily, "folder", lambda: where)
    _all_three(owner)
    net.say["anthropic"] = (401, {})
    owner.put("/api/v1/ai/settings", json={"order": ["anthropic", "local", "gemini"]})
    owner.post("/api/v1/ai/ask", json=Q_STATUS)
    lines = (where / S.FILE_NAME).read_text("utf-8").splitlines()
    assert [ln.split(",")[1] for ln in lines] == ["provider=anthropic", "provider=local"]
    assert "status=error" in lines[0] and 'error_message="AI_KEY_INVALID"' in lines[0]
    assert f"model={LOCAL_MODEL}" in lines[1] and "cost_usd=0.000000" in lines[1]
    assert "input_tokens=950i,output_tokens=130i" in lines[1]
    assert S.line("gemini-2.5-flash", "success", 800, 150, 10, provider="google").startswith(
        "api_call,provider=google,model=gemini-2.5-flash,")
