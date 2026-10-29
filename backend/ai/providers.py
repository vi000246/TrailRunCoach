"""Where an AI 問答 answer comes from (SP-997): a list of sources, tried in order.

    Source(kind, model, key=None, url=None, purpose="ask")      kind: local | anthropic | gemini
    run(sources, system, messages, post) -> (text, the source that answered, tried)
    check(source, get) -> {ok: True}                              no tokens, nothing billed

* anthropic — Anthropic's Messages API (SP-785, as before).
* gemini    — Google's Gemini API, its own REST: POST <GEMINI_BASE>/models/<model>:generateContent
              with the key in `x-goog-api-key` (not the OpenAI-compatible beta: the native one takes
              the system prompt as systemInstruction and answers JSON on request).
* local     — any OpenAI-compatible chat endpoint: POST <base>/v1/chat/completions (Ollama, LM Studio,
              llama.cpp's server …) — e.g. Qwen on the owner's Mac. No key unless the server wants one.

The chain: each source is tried in order. Any failure of *that* source moves on to the next — it
can't be reached (the Mac asleep or off), it times out, it refuses the key, it has no credit, it is
busy, it answers something unusable (not the API's JSON; an empty text while another source is
left). When every source fails, the last one's error is raised (tried lists each failure).

The answer is the same JSON for every source (qa.SYSTEM; qa.parse_answer checks it): nothing here
uses tools, prompt caching or streaming — a plain system prompt, the turns and one text back. A
local model's 「thinking」 (Qwen's <think>…</think>) is dropped before the text is parsed.

The HTTPS calls go through the `post` / `get` the caller passes (qa._post / qa._get — the tests'
fakes, conftest._no_live_ai): nothing here opens a socket. A local source's timeout is a
(connect, read) pair: a Mac that is asleep fails in LOCAL_CONNECT_S, a slow answer may take
LOCAL_READ_S. Each call (not a cached answer) leaves its line for the Grafana usage (spend.record,
provider anthropic | google | local; a local call costs 0). The log gets the source, the status and
the time — never a key, the question or the answer; a Source's key is not in its repr.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

from backend.ai import spend

log = logging.getLogger("backend.ai")

KINDS = ("local", "anthropic", "gemini")
SPEND_PROVIDER = {"anthropic": "anthropic", "gemini": "google", "local": "local"}

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS = "https://api.anthropic.com/v1/models?limit=1"     # the key test (SP-883): nothing billed
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

MAX_TOKENS = 1200
GEMINI_MAX_TOKENS = 4096        # Gemini's 「thinking」 is counted inside maxOutputTokens: 1200 can end empty
TIMEOUT_S = 60.0
TEST_TIMEOUT_S = 15.0
LOCAL_CONNECT_S = 3.0           # the Mac asleep / off / not on the network: give up fast, try the next
LOCAL_READ_S = 180.0            # a local model on a laptop writes slower than an API
LOCAL_TEST_READ_S = 15.0

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


class AIError(Exception):
    """A code the API answers ({detail: {code}}) and its HTTP status. `upstream`: the source's own
    status (the log's; 0 for a network error)."""

    def __init__(self, code: str, status: int, upstream: int = 0):
        super().__init__(code)
        self.code, self.status, self.upstream = code, status, upstream


@dataclass(frozen=True)
class Source:
    kind: str
    model: str
    key: Optional[str] = field(default=None, repr=False)
    url: Optional[str] = None          # local: the endpoint's base (…/v1)
    purpose: str = "ask"               # spend.record's purpose (ask | ask_own_key)

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.model}"


Post = Callable[[str, dict, bytes, object], tuple]
Get = Callable[[str, dict, object], tuple]


# ------------------------------------------------------------------------------- the local URL

def local_base(url: str) -> Optional[str]:
    """The base of an OpenAI-compatible endpoint (…/v1), from what the owner typed: http(s) only,
    no user:password in it; `http://mac:11434`, `…/v1`, `…/v1/chat/completions` all give
    `http://mac:11434/v1`. None when it is not such a URL."""
    u = (url or "").strip()
    if not u or len(u) > 300 or any(c.isspace() for c in u):
        return None
    try:
        p = urlsplit(u)
        port = p.port                  # raises on a bad port
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password \
            or p.query or p.fragment:
        return None
    path = p.path.rstrip("/")
    for tail in ("/chat/completions", "/models"):
        if path.endswith(tail):
            path = path[: -len(tail)]
    if not path.endswith("/v1"):
        path += "/v1"
    host = f"[{p.hostname}]" if ":" in p.hostname else p.hostname
    return f"{p.scheme}://{host}{'' if port is None else ':' + str(port)}{path}"


# ------------------------------------------------------------------------------- each provider

def _json(raw: bytes) -> dict:
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        raise AIError("AI_UPSTREAM", 502)
    if not isinstance(d, dict):
        raise AIError("AI_UPSTREAM", 502)
    return d


def _int(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def anthropic_headers(key: str) -> dict:
    return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json",
            "user-agent": "TrailRunCoach-ai"}


def _anthropic_refused(status: int, raw: bytes) -> AIError:
    if status in (401, 403):
        return AIError("AI_KEY_INVALID", 502, status)
    if status in (400, 402) and b"credit balance" in (raw or b"").lower():   # 「credit balance is too low」
        return AIError("AI_NO_CREDIT", 402, status)
    if status in (429, 529):
        return AIError("AI_BUSY", 503, status)
    return AIError("AI_UPSTREAM", 502, status)


def _anthropic(src: Source, system: str, messages: list, post: Post) -> tuple:
    body = json.dumps({"model": src.model, "max_tokens": MAX_TOKENS, "system": system,
                       "messages": messages}).encode()
    status, raw = post(ANTHROPIC_URL, anthropic_headers(src.key or ""), body, TIMEOUT_S)
    if not 200 <= status < 300:
        raise _anthropic_refused(status, raw)
    d = _json(raw)
    try:
        text = "".join(b.get("text") or "" for b in d.get("content") or [] if b.get("type") == "text")
        u = d.get("usage") or {}
    except (AttributeError, TypeError):
        raise AIError("AI_UPSTREAM", 502, status)
    return text, _int(u.get("input_tokens")), _int(u.get("output_tokens"))


def gemini_headers(key: str) -> dict:
    return {"x-goog-api-key": key, "content-type": "application/json", "user-agent": "TrailRunCoach-ai"}


def _gemini_refused(status: int, raw: bytes) -> AIError:
    low = (raw or b"").lower()
    if status in (401, 403) or (status == 400 and (b"api_key_invalid" in low or b"api key not valid" in low)):
        return AIError("AI_KEY_INVALID", 502, status)
    if status == 404:
        return AIError("AI_NO_MODEL", 502, status)
    if status in (429, 503):                       # RESOURCE_EXHAUSTED (the free tier's quota too), UNAVAILABLE
        return AIError("AI_BUSY", 503, status)
    return AIError("AI_UPSTREAM", 502, status)


def _gemini_model(model: str) -> str:
    return quote(model, safe="-._")


def _gemini(src: Source, system: str, messages: list, post: Post) -> tuple:
    contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                for m in messages]
    body = json.dumps({"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                       "generationConfig": {"maxOutputTokens": GEMINI_MAX_TOKENS,
                                            "responseMimeType": "application/json"}}).encode()
    url = f"{GEMINI_BASE}/models/{_gemini_model(src.model)}:generateContent"
    status, raw = post(url, gemini_headers(src.key or ""), body, TIMEOUT_S)
    if not 200 <= status < 300:
        raise _gemini_refused(status, raw)
    d = _json(raw)
    try:
        cands = d.get("candidates") or []
        parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
        text = "".join(p.get("text") or "" for p in parts if isinstance(p, dict) and not p.get("thought"))
        u = d.get("usageMetadata") or {}
    except (AttributeError, TypeError, IndexError):
        raise AIError("AI_UPSTREAM", 502, status)
    return (text, _int(u.get("promptTokenCount")),
            _int(u.get("candidatesTokenCount")) + _int(u.get("thoughtsTokenCount")))


def local_headers(key: Optional[str]) -> dict:
    h = {"content-type": "application/json", "user-agent": "TrailRunCoach-ai"}
    if key:
        h["authorization"] = "Bearer " + key
    return h


def _local_refused(status: int, raw: bytes) -> AIError:
    if status in (401, 403):
        return AIError("AI_KEY_INVALID", 502, status)
    if status == 404:                              # Ollama: 「model … not found」
        return AIError("AI_NO_MODEL", 502, status)
    if status in (429, 503):
        return AIError("AI_BUSY", 503, status)
    return AIError("AI_UPSTREAM", 502, status)


def strip_thinking(text: str) -> str:
    t = _THINK.sub("", text or "")
    if "</think>" in t:                            # an opening tag the server already ate
        t = t.split("</think>", 1)[1]
    return t.strip()


def _local(src: Source, system: str, messages: list, post: Post) -> tuple:
    body = json.dumps({"model": src.model, "messages": [{"role": "system", "content": system}] + messages,
                       "max_tokens": MAX_TOKENS, "stream": False}).encode()
    status, raw = post(f"{src.url}/chat/completions", local_headers(src.key), body,
                       (LOCAL_CONNECT_S, LOCAL_READ_S))
    if not 200 <= status < 300:
        raise _local_refused(status, raw)
    d = _json(raw)
    try:
        msg = (d.get("choices") or [{}])[0].get("message") or {}
        text = strip_thinking(msg.get("content") or "")
        u = d.get("usage") or {}
    except (AttributeError, TypeError, IndexError):
        raise AIError("AI_UPSTREAM", 502, status)
    return text, _int(u.get("prompt_tokens")), _int(u.get("completion_tokens"))


_CALL = {"anthropic": _anthropic, "gemini": _gemini, "local": _local}


# ------------------------------------------------------------------------------------ the chain

def _network(src: Source) -> AIError:
    return AIError("AI_UNREACHABLE" if src.kind == "local" else "AI_UPSTREAM", 502)


def run(sources: list, system: str, messages: list, post: Post) -> tuple:
    """(text, the Source that answered, tried: [{kind, model, code}] of the ones that failed before)."""
    if not sources:
        raise AIError("AI_NO_KEY", 409)
    tried: list = []
    last: Optional[AIError] = None
    for i, src in enumerate(sources):
        t0 = time.monotonic()
        tin = tout = 0
        try:
            try:
                text, tin, tout = _CALL[src.kind](src, system, messages, post)
            except AIError:
                raise
            except Exception as e:             # noqa: BLE001 — a network error: logged by type only
                log.warning("AI %s: %s", src.kind, type(e).__name__)
                raise _network(src)
            if not (text or "").strip() and i < len(sources) - 1:
                raise AIError("AI_UPSTREAM", 502, 200)
        except AIError as e:
            ms = (time.monotonic() - t0) * 1000
            log.warning("AI %s failed: %s (%s, %d ms)%s", src.kind, e.code, e.upstream or "-", ms,
                        " — trying the next source" if i < len(sources) - 1 else "")
            spend.record(src.model, "error", tin, tout, ms, e.code, src.purpose,
                         provider=SPEND_PROVIDER[src.kind])
            tried.append({"kind": src.kind, "model": src.model, "code": e.code})
            last = e
            continue
        ms = (time.monotonic() - t0) * 1000
        log.info("AI %s ok (%d ms, %s in / %s out tokens)", src.kind, ms, tin, tout)
        spend.record(src.model, "success", tin, tout, ms, "", src.purpose, provider=SPEND_PROVIDER[src.kind])
        return text, src, tried
    assert last is not None
    raise last


# ------------------------------------------------------------------------------------- the test

def check(src: Source, get: Get) -> dict:
    """Is this source usable? No tokens, nothing billed: Anthropic lists one model, Gemini reads
    the model's card, a local endpoint lists its models (and must have the one set).
    {ok: True} or AIError (AI_KEY_INVALID, AI_NO_MODEL, AI_UNREACHABLE, AI_BUSY, AI_UPSTREAM)."""
    t0 = time.monotonic()
    try:
        if src.kind == "anthropic":
            status, raw = get(ANTHROPIC_MODELS, anthropic_headers(src.key or ""), TEST_TIMEOUT_S)
            refused = _anthropic_refused
        elif src.kind == "gemini":
            status, raw = get(f"{GEMINI_BASE}/models/{_gemini_model(src.model)}", gemini_headers(src.key or ""),
                              TEST_TIMEOUT_S)
            refused = _gemini_refused
        else:
            status, raw = get(f"{src.url}/models", local_headers(src.key), (LOCAL_CONNECT_S, LOCAL_TEST_READ_S))
            refused = _local_refused
    except Exception as e:                     # noqa: BLE001 — a network error: logged by type only
        log.warning("AI %s test: %s", src.kind, type(e).__name__)
        raise _network(src)
    ms = int((time.monotonic() - t0) * 1000)
    if not 200 <= status < 300:
        e = refused(status, raw)
        log.warning("AI %s test refused: %s (%s, %d ms)", src.kind, e.code, status, ms)
        raise e
    if src.kind == "local":
        ids = {str(m.get("id")) for m in (_json(raw).get("data") or []) if isinstance(m, dict)}
        if src.model not in ids:
            log.warning("AI local test: the endpoint answers but has not got the model set (%d ms)", ms)
            raise AIError("AI_NO_MODEL", 502, status)
    log.info("AI %s test ok (%d ms)", src.kind, ms)
    return {"ok": True}
