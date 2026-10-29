"""AI 問答 (SP-785; the dialog: static/askai.js, SP-786). The owner with the owner's key (SP-306
Q1-1 C) and, while the server's TRC_AI_MEMBER_KEYS is on, a signed-in member with their own key
(SP-883; backend/ai/config.allowed): every route answers 403 AI_OWNER_ONLY to anyone else (the
member too while the switch is off); the demo never mounts it (main.py owner_only).

    POST /api/v1/ai/ask            {question, focus?, history?} -> backend/ai/qa.ask
    POST /api/v1/ai/context        {focus?} -> {context, refs}: exactly what a question would send (no model call)
    GET  /api/v1/ai/settings       {enabled, key {source, masked}, model, daily_limit, used_today, who,
                                    sources: [{kind, ready, …}] in the order they are tried (SP-997)}
    PUT  /api/v1/ai/settings       {enabled?, api_key?, gemini_key?, local? {url, model} | null, order?}
                                   ('' / null clears); never answers a key. local and order are the
                                   owner's only (SP-997 Q1-1): a member gets 403 AI_OWNER_ONLY; a
                                   member's gemini_key is their own (Q2-2)
    POST /api/v1/ai/settings/test  {kind?} -> {ok: true}: that source is usable (SP-883, SP-997;
                                   default the Anthropic key; nothing billed)

Errors are {detail: {code}}: AI_OWNER_ONLY 403, AI_OFF / AI_NO_KEY 409, AI_EMPTY / BAD_KEY /
BAD_LOCAL / BAD_ORDER / BAD_REQUEST 400, AI_NO_CREDIT 402, AI_DAILY_LIMIT / AI_TEST_LIMIT 429,
AI_BUSY 503, AI_KEY_INVALID / AI_NO_MODEL / AI_UNREACHABLE / AI_UPSTREAM 502 — the dialog words
them (shell catalog askai.err.*), the settings page too (settings.askai.*).
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from backend.ai import config as C
from backend.ai import qa as Q
from backend.api.debug import same_origin

SOURCE_FIELDS = ("gemini_key", "local", "order")     # SP-997
OWNER_FIELDS = ("local", "order")                   # SP-997 Q1-1: the owner's only (a member may set their own gemini_key, Q2-2)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"], include_in_schema=False)


def owner_only() -> None:
    if not C.allowed():
        raise HTTPException(403, {"code": "AI_OWNER_ONLY"})


def _body(body) -> dict:
    return body if isinstance(body, dict) else {}


@router.post("/ask", dependencies=[Depends(owner_only), Depends(same_origin)])
def ask(body: dict = Body(default={})):
    from backend.i18n import current_locale
    b = _body(body)
    try:
        return Q.ask(b.get("question"), b.get("focus"), b.get("history"), locale=current_locale())
    except Q.AIError as e:
        raise HTTPException(e.status, {"code": e.code})


@router.post("/context", dependencies=[Depends(owner_only), Depends(same_origin)])
def context(body: dict = Body(default={})):
    ctx, refs = Q.build_context(_body(body).get("focus"))
    return {"context": ctx, "refs": refs}


@router.get("/settings", dependencies=[Depends(owner_only)])
def get_settings():
    return C.state()


@router.put("/settings", dependencies=[Depends(owner_only), Depends(same_origin)])
def put_settings(body: dict = Body(default={})):
    b = _body(body)
    if C.is_member() and any(k in b for k in OWNER_FIELDS):       # before anything is written
        raise HTTPException(403, {"code": "AI_OWNER_ONLY"})
    if "enabled" in b:
        if not isinstance(b["enabled"], bool):
            raise HTTPException(400, {"code": "BAD_REQUEST"})
        C.set_enabled(b["enabled"])
    if "api_key" in b:
        if not isinstance(b["api_key"], str):
            raise HTTPException(400, {"code": "BAD_KEY"})
        try:
            C.save_key(b["api_key"])
        except ValueError:
            raise HTTPException(400, {"code": "BAD_KEY"})
        Q.clear_cache()
    _put_sources(b)
    return C.state()


def _put_sources(b: dict) -> None:
    """SP-997: the Gemini key (the asker's own), the local model and the order (the owner's)."""
    if not any(k in b for k in SOURCE_FIELDS):
        return
    if "gemini_key" in b:
        if not isinstance(b["gemini_key"], str):
            raise HTTPException(400, {"code": "BAD_KEY"})
        try:
            C.save_gemini_key(b["gemini_key"])
        except ValueError:
            raise HTTPException(400, {"code": "BAD_KEY"})
    if "local" in b:
        v = b["local"]
        if v is not None and not (isinstance(v, dict) and isinstance(v.get("url", ""), str)
                                  and isinstance(v.get("model", ""), str)
                                  and isinstance(v.get("api_key", ""), (str, type(None)))):
            raise HTTPException(400, {"code": "BAD_LOCAL"})
        try:
            C.set_local((v or {}).get("url", ""), (v or {}).get("model", ""), (v or {}).get("api_key"))
        except ValueError:
            raise HTTPException(400, {"code": "BAD_LOCAL"})
    if "order" in b:
        try:
            C.set_order(b["order"])
        except ValueError:
            raise HTTPException(400, {"code": "BAD_ORDER"})
    Q.clear_cache()


@router.post("/settings/test", dependencies=[Depends(owner_only), Depends(same_origin)])
def test_settings(body: dict = Body(default={})):
    """「測試」 (SP-883; SP-997 `kind`): is that source usable? Nothing billed (qa.check_key)."""
    kind = _body(body).get("kind")
    if kind is not None and not isinstance(kind, str):
        raise HTTPException(400, {"code": "BAD_REQUEST"})
    try:
        return Q.check_key(kind)
    except Q.AIError as e:
        raise HTTPException(e.status, {"code": e.code})
