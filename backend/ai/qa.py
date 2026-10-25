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
  (spend.record, SP-882) — a file Telegraf reads; it never fails the call.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from typing import Any, Optional

from backend.ai import config as C
from backend.ai import context as X
from backend.ai import spend
from backend.ai.medical import is_medical

log = logging.getLogger("backend.ai")

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TIMEOUT_S = 60.0
MAX_TOKENS = 1200
CACHE_TTL_S = 6 * 3600
CACHE_MAX = 128
MAX_PARTS = 12

_cache: OrderedDict = OrderedDict()       # key -> (at, answer)
_cache_lock = threading.Lock()
_call_lock = threading.Lock()             # one model call at a time (the count stays exact)

LANG = {"en": "English", "zh-TW": "Traditional Chinese (Taiwan)"}

SYSTEM = """You are the explainer inside TrailRunCoach, a trail-running training app. The user is the athlete.
You answer questions about their training status, what is lacking, a chart, a planned session or a race-calculator result.

Rules you must follow:
1. Use only the facts in the CONTEXT: "app" is the app's own summary (status rules with their judgment, number, why, action and source; the to-do list; weekly totals), "in_view" is the card, chart, session or result the user is looking at. Never invent numbers, workouts, paces, zones or training advice that the context does not contain. If the context does not answer the question, say so and name the page or card that would.
2. The app's rules and judgments are the authority. Explain them; do not contradict, replace or extend them with advice of your own.
3. Every citable item has an id (S1, S2 ...). A part that restates an app rule, judgment or number is type "rule" and lists the ids it rests on in "refs". Your own interpretation, connection or summary is type "inference" (refs may be empty). Keep inference short.
4. Anything about pain, injury, illness or other medical symptoms: do not diagnose or treat; tell the user to see a doctor or physiotherapist and set "medical": true.
5. Answer in {lang}, plainly, at most about 180 words in total.

Reply with JSON only, no other text:
{"parts": [{"type": "rule", "text": "...", "refs": ["S1"]}, {"type": "inference", "text": "...", "refs": []}], "medical": false}"""


class AIError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code, self.status = code, status


# ------------------------------------------------------------------------------------- the call

def _post(url: str, headers: dict, body: bytes, timeout: float) -> tuple:
    """(status, response body) of one HTTPS POST (tests replace this)."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:          # noqa: S310 — a fixed https URL
            return r.status, r.read(1_000_000)
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
    body = json.dumps({"model": model, "max_tokens": MAX_TOKENS, "system": system,
                       "messages": messages}).encode()
    headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json",
               "user-agent": "TrailRunCoach-ai"}
    t0 = time.monotonic()
    done = {"status": "error", "in": 0, "out": 0, "error": "AI_UPSTREAM"}
    try:
        try:
            status, raw = _post(API_URL, headers, body, TIMEOUT_S)
        except Exception as e:               # noqa: BLE001 — a network error: logged by type only
            log.warning("AI call failed: %s", type(e).__name__)
            raise AIError("AI_UPSTREAM", 502)
        ms = int((time.monotonic() - t0) * 1000)
        if status in (401, 403):
            log.warning("AI call refused (%s, %d ms): the key is not accepted", status, ms)
            raise AIError("AI_KEY_INVALID", 502)
        if status in (429, 529):
            log.warning("AI call busy (%s, %d ms)", status, ms)
            raise AIError("AI_BUSY", 503)
        if not 200 <= status < 300:
            log.warning("AI call failed (%s, %d ms)", status, ms)
            raise AIError("AI_UPSTREAM", 502)
        try:
            d = json.loads(raw)
            text = "".join(b.get("text") or "" for b in d.get("content") or [] if b.get("type") == "text")
            u = d.get("usage") or {}
        except (ValueError, AttributeError, TypeError):
            log.warning("AI call: an answer that is not JSON (%d ms)", ms)
            raise AIError("AI_UPSTREAM", 502)
        log.info("AI call ok (%d ms, %s in / %s out tokens)", ms, u.get("input_tokens"), u.get("output_tokens"))
        done.update(status="success", error="", **{"in": _int(u.get("input_tokens")),
                                                   "out": _int(u.get("output_tokens"))})
        return text
    except AIError as e:
        done["error"] = e.code
        raise
    finally:                                 # SP-882: every call's tokens and cost for the Grafana usage
        spend.record(model, done["status"], done["in"], done["out"],
                     (time.monotonic() - t0) * 1000, done["error"], purpose)


def _int(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


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
    key = C.api_key()
    if not key:
        raise AIError("AI_NO_KEY", 409)
    model = C.model()
    hist = X.clean_history(history)
    ctx, refs = build_context(focus)
    ck = _cache_key(model, locale, q, ctx, hist)
    medical_q = is_medical(q)
    hit = _cached(ck)
    if hit is not None:
        return {**hit, "cached": True, "used_today": C.used_today(), "daily_limit": C.daily_limit()}
    with _call_lock:
        if C.used_today() >= C.daily_limit():
            raise AIError("AI_DAILY_LIMIT", 429)
        text = call_model(key, model, SYSTEM.replace("{lang}", LANG.get(locale, LANG["zh-TW"])),
                          _messages(q, ctx, hist))
        used = C.count_one()
    ans = parse_answer(text, refs)
    ans = {**ans, "medical": ans["medical"] or medical_q, "model": model, "sent": X.sent_view(ctx)}
    _store(ck, ans)
    return {**ans, "cached": False, "used_today": used, "daily_limit": C.daily_limit()}
