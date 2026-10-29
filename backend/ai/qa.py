"""One AI 問答 question (SP-785): the context (context.py), the Messages API call, the answer.

    ask(question, focus, history) -> {parts, sources, medical, cached, used_today, daily_limit, model, sent}

* The prompt (SYSTEM) holds the model to the app's own numbers and rules: it explains them, it
  does not invent training advice, workouts or numbers, and it answers in JSON
  {"parts": [{"type": "rule" | "inference", "text", "refs": ["S1"]}], "medical": bool}.
* parse_answer checks it: a "rule" part must cite a ref that was sent, else it is shown as the
  model's inference; text that is not that JSON is one inference part. The dialog marks each part
  (app rule ↔ model inference) and lists the cited sources.
* A medical question (medical.is_medical, or the model saying so) gets `medical: true`: the
  dialog's fixed see-a-doctor line, whatever the model wrote.
* The cache: the same question on the same context (the same data, the same card) within
  CACHE_TTL_S answers from memory — not counted. Each model call is counted (config.count_one);
  past config.daily_limit() → AIError AI_DAILY_LIMIT (429).
* The call: POST API_URL with the standard library's HTTPS (no SDK, like accounts/mailer.py);
  tests replace `_post`; nothing in the tests reaches the network. The log gets the status, the
  time and the token counts — never the key, the question or the answer.
* Each call (not a cached answer) also leaves its tokens and USD for the shared Grafana「API 使用量」
  (spend.record, SP-882) — a file Telegraf reads; it never fails the call. A member's call on their
  own key (SP-883) is purpose `ask_own_key`: the dashboard tells the site's spend from theirs.
* A member's key (SP-883): the same ask, their own key, their own count; calls are one at a time
  per person (not one for the whole site). 「測試」 on the settings page is check_key: GET API_MODELS
  (lists the models; no tokens, nothing billed), at most config.KEY_TESTS_PER_DAY a day. A key the
  API refuses for its credit balance answers AI_NO_CREDIT.
* More than one source (SP-997, providers.py): the owner's local model (OpenAI-compatible, e.g. Qwen
  on the Mac), the Anthropic key, a Gemini key — tried in config.order(); a source that fails hands
  over to the next. The answer says which one answered (`provider`, `model`) and which failed before
  (`tried`). A local model costs nothing: its answers are not counted against the daily limit, and
  past the limit the local model still answers (the paid sources are left out).
"""
from __future__ import annotations

import hashlib
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Any, Optional
from urllib.parse import urlsplit

from backend.ai import config as C
from backend.ai import context as X
from backend.ai import providers as P
from backend.ai.medical import is_medical
from backend.ai.providers import AIError  # noqa: F401 — qa.AIError, as before SP-997

log = logging.getLogger("backend.ai")

API_URL = P.ANTHROPIC_URL
API_MODELS = P.ANTHROPIC_MODELS           # the key test (SP-883): nothing billed
API_VERSION = P.ANTHROPIC_VERSION
TIMEOUT_S = P.TIMEOUT_S
MAX_TOKENS = P.MAX_TOKENS
CACHE_TTL_S = 6 * 3600
CACHE_MAX = 128
MAX_PARTS = 12
TEST_TIMEOUT_S = P.TEST_TIMEOUT_S
PURPOSE_OWN_KEY = "ask_own_key"           # spend.record: a member's call on their own key (SP-883)

_cache: OrderedDict = OrderedDict()       # key -> (at, answer)
_cache_lock = threading.Lock()
_call_locks: dict = {}                    # tenant id -> lock: one model call at a time per person (the count stays exact)
_call_locks_lock = threading.Lock()


def _call_lock() -> threading.Lock:
    from backend import tenancy
    tid = tenancy.current().id
    with _call_locks_lock:
        lk = _call_locks.get(tid)
        if lk is None:
            lk = _call_locks[tid] = threading.Lock()
        return lk

LANG = {"en": "English", "zh-TW": "Traditional Chinese (Taiwan)"}

# {lang} and {brand} (the product's name in the answer's language, SP-1003) filled in by system_prompt()
SYSTEM = """You are the explainer inside {brand}, a trail-running training app; asked who or what you are, say you are {brand}'s explainer. The user is the athlete.
You answer questions about their training status, what is lacking, a chart, a planned session or a race-calculator result.

Rules you must follow:
1. Use only the facts in the CONTEXT: "app" is the app's own summary (status rules with their judgment, number, why, action and source; the to-do list; weekly totals), "in_view" is the card, chart, session or result the user is looking at. Never invent numbers, workouts, paces, zones or training advice that the context does not contain. If the context does not answer the question, say so and name the page or card that would.
2. The app's rules and judgments are the authority. Explain them; do not contradict, replace or extend them with advice of your own.
3. Every citable item has an id (S1, S2 ...). A part that restates an app rule, judgment or number is type "rule" and lists the ids it rests on in "refs". Your own interpretation, connection or summary is type "inference" (refs may be empty). Keep inference short.
4. Anything about pain, injury, illness or other medical symptoms: do not diagnose or treat; tell the user to see a doctor or physiotherapist and set "medical": true.
5. Answer in {lang}, plainly, at most about 180 words in total.

Reply with JSON only, no other text:
{"parts": [{"type": "rule", "text": "...", "refs": ["S1"]}, {"type": "inference", "text": "...", "refs": []}], "medical": false}"""


def system_prompt(locale: str) -> str:
    """SYSTEM for one answer: its language, and the product's name as the user sees it (pages.brand)."""
    from backend.i18n.pages import brand
    loc = locale if locale in LANG else "zh-TW"
    return SYSTEM.replace("{lang}", LANG[loc]).replace("{brand}", brand(loc))


# ------------------------------------------------------------------------------------- the call

def _timeouts(timeout) -> tuple:
    """(connect, read): a pair as is (a local model, SP-997), else the one number for both."""
    return tuple(timeout) if isinstance(timeout, (tuple, list)) else (timeout, timeout)


def _reach(url: str, connect_s: float) -> None:
    """Fail within connect_s when the host can't be reached (the owner's Mac asleep or off), before
    the long read timeout a local model's answer needs (urllib has one timeout for both)."""
    p = urlsplit(url)
    port = p.port or (443 if p.scheme == "https" else 80)
    socket.create_connection((p.hostname, port), timeout=connect_s).close()


def _post(url: str, headers: dict, body: bytes, timeout) -> tuple:
    """(status, response body) of one HTTP(S) POST (tests replace this). `timeout`: seconds, or a
    (connect, read) pair."""
    connect_s, read_s = _timeouts(timeout)
    if connect_s < read_s:
        _reach(url, connect_s)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=read_s) as r:           # noqa: S310 — the API's, or the owner's own endpoint
            return r.status, r.read(1_000_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096) if e.fp else b""


def _get(url: str, headers: dict, timeout) -> tuple:
    """(status, response body) of one HTTP(S) GET — the key test (tests replace this)."""
    connect_s, read_s = _timeouts(timeout)
    if connect_s < read_s:
        _reach(url, connect_s)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=read_s) as r:           # noqa: S310 — the API's, or the owner's own endpoint
            return r.status, r.read(100_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096) if e.fp else b""


def _messages(question: str, ctx: dict, history: list) -> list:
    msgs = []
    for h in history:
        msgs.append({"role": "user", "content": h["q"]})
        msgs.append({"role": "assistant", "content": h["a"] or "-"})
    msgs.append({"role": "user", "content": "CONTEXT:\n" + json.dumps(ctx, ensure_ascii=False)
                 + "\n\nQUESTION:\n" + question})
    return msgs


def call_model(key: str, model: str, system: str, messages: list, purpose: str = "ask") -> str:
    """One Anthropic call (SP-785's single source): the text. Each call's tokens and cost go to the
    Grafana usage (SP-882) — providers.run does both, for any source."""
    text, _src, _tried = P.run([P.Source("anthropic", model, key, purpose=purpose)], system, messages, _post)
    return text


# ------------------------------------------------------------------------------------ the answer

def _json_in(text: str) -> Optional[dict]:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{"):] if "{" in s else s
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        d = json.loads(s[a:b + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def parse_answer(text: str, refs: list) -> dict:
    """{parts: [{type: rule | inference, text, refs}], sources: the refs cited, medical}."""
    known = {r["id"]: r for r in refs}
    d = _json_in(text)
    parts = []
    if d is not None and isinstance(d.get("parts"), list):
        for p in d["parts"][:MAX_PARTS]:
            if not isinstance(p, dict) or not str(p.get("text") or "").strip():
                continue
            cited = [r for r in (p.get("refs") or []) if isinstance(r, str) and r in known] \
                if isinstance(p.get("refs"), list) else []
            cited = list(dict.fromkeys(cited))
            kind = "rule" if p.get("type") == "rule" and cited else "inference"
            parts.append({"type": kind, "text": str(p["text"]).strip()[:2000], "refs": cited})
    if not parts:
        t = (text or "").strip()
        parts = [{"type": "inference", "text": t[:4000], "refs": []}] if t else []
    used = list(dict.fromkeys(r for p in parts for r in p["refs"]))
    return {"parts": parts, "sources": [known[r] for r in used],
            "medical": bool(d.get("medical")) if d is not None else False}


# --------------------------------------------------------------------------------------- one ask

def _cache_key(model: str, locale: str, question: str, ctx: dict, history: list) -> str:
    from backend import tenancy
    raw = json.dumps([tenancy.current().id, model, locale, question, ctx, history], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cached(key: str) -> Optional[dict]:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        if time.time() - hit[0] > CACHE_TTL_S:
            _cache.pop(key, None)
            return None
        _cache.move_to_end(key)
        return hit[1]


def _store(key: str, ans: dict) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), ans)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def build_context(focus: Any) -> tuple[dict, list]:
    """(the context the model would get, its refs) — also POST /api/v1/ai/context (what is sent)."""
    return X.number_refs(X.server_summary(), X.clean_focus(focus))


def ask(question: Any, focus: Any = None, history: Any = None, locale: str = "zh-TW") -> dict:
    if not C.allowed():
        raise AIError("AI_OWNER_ONLY", 403)
    if not C.enabled():
        raise AIError("AI_OFF", 409)
    q = X.clean_text(question, X.MAX["question"])
    if not q:
        raise AIError("AI_EMPTY", 400)
    chain = C.sources()
    if not chain:
        raise AIError("AI_NO_KEY", 409)
    hist = X.clean_history(history)
    ctx, refs = build_context(focus)
    ck = _cache_key("|".join(s.label for s in chain), locale, q, ctx, hist)
    medical_q = is_medical(q)
    hit = _cached(ck)
    if hit is not None:
        return {**hit, "cached": True, "used_today": C.used_today(), "daily_limit": C.daily_limit()}
    with _call_lock():
        if C.used_today() >= C.daily_limit():          # past the limit only the free (local) source is left
            chain = [s for s in chain if s.kind == "local"]
            if not chain:
                raise AIError("AI_DAILY_LIMIT", 429)
        text, src, tried = P.run(chain, system_prompt(locale), _messages(q, ctx, hist), _post)
        used = C.used_today() if src.kind == "local" else C.count_one()
    ans = parse_answer(text, refs)
    ans = {**ans, "medical": ans["medical"] or medical_q, "model": src.model, "provider": src.kind,
           "tried": tried, "sent": X.sent_view(ctx)}
    _store(ck, ans)
    return {**ans, "cached": False, "used_today": used, "daily_limit": C.daily_limit()}


def check_key(kind: Optional[str] = None) -> dict:
    """「測試」 on the settings page (SP-883): is that source usable? (`kind`: local | anthropic |
    gemini, SP-997; default the Anthropic key, as before.) No tokens, nothing billed, not counted
    against the daily questions (providers.check). At most config.KEY_TESTS_PER_DAY a day per person
    (else AI_TEST_LIMIT 429, no call). {ok: true} or AIError (AI_NO_KEY, AI_KEY_INVALID, AI_NO_CREDIT,
    AI_NO_MODEL, AI_UNREACHABLE, AI_BUSY, AI_UPSTREAM). Logs the status and time only."""
    if not C.allowed():
        raise AIError("AI_OWNER_ONLY", 403)
    kind = kind or "anthropic"
    if kind not in P.KINDS:
        raise AIError("BAD_REQUEST", 400)
    src = next((s for s in C.sources() if s.kind == kind), None)
    if src is None:
        raise AIError("AI_NO_KEY", 409)
    if not C.count_key_test():
        raise AIError("AI_TEST_LIMIT", 429)
    return P.check(src, _get)
