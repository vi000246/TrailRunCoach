"""AI 問答 (SP-785; the dialog: static/askai.js, SP-786). The owner with the owner's key (SP-306
Q1-1 C) and, while the server's TRC_AI_MEMBER_KEYS is on, a signed-in member with their own key
(SP-883; backend/ai/config.allowed): every route answers 403 AI_OWNER_ONLY to anyone else (the
member too while the switch is off); the demo never mounts it (main.py owner_only).

    POST /api/v1/ai/ask            {question, focus?, history?} -> backend/ai/qa.ask
    POST /api/v1/ai/context        {focus?} -> {context, refs}: exactly what a question would send (no model call)
    GET  /api/v1/ai/settings       {enabled, key {source, masked}, model, daily_limit, used_today, who}
    PUT  /api/v1/ai/settings       {enabled?, api_key?} ('' clears the saved key); never answers the key
    POST /api/v1/ai/settings/test  {ok: true}: the key in use is accepted (SP-883; nothing billed)

Errors are {detail: {code}}: AI_OWNER_ONLY 403, AI_OFF / AI_NO_KEY 409, AI_EMPTY / BAD_KEY 400,
AI_NO_CREDIT 402, AI_DAILY_LIMIT / AI_TEST_LIMIT 429, AI_BUSY 503, AI_KEY_INVALID / AI_UPSTREAM 502
— the dialog words them (shell catalog askai.err.*), the settings page too (settings.askai.*).
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from backend.ai import config as C
from backend.ai import qa as Q
from backend.api.debug import same_origin

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
    return C.state()


@router.post("/settings/test", dependencies=[Depends(owner_only), Depends(same_origin)])
def test_settings():
    """「測試」 (SP-883): is the key in use accepted? Nothing billed (qa.check_key)."""
    try:
        return Q.check_key()
    except Q.AIError as e:
        raise HTTPException(e.status, {"code": e.code})
