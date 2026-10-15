"""
Linked services (SP-329, accounts/links.py): 疊石 Cairn and this app as one Google account.
Every route answers 404 unless sign-in is on (login.required()); the demo never mounts this router.

The user's side (signed in, a session; writes CSRF-checked like every write):
  GET  /api/v1/account/links             each project: linked or not, since when, what linking
                                         allows; for a linked Cairn its plan there, asked server to
                                         server (links.cairn_status; nothing when not configured)
  POST /api/v1/account/links/<project>   {linked: true | false}: link (a consents row) or unlink (a
                                         consents row, the project's service tokens deleted)

The project's side (server to server, never a cookie; the login gate's /api/v1/link/ step):
  POST /api/v1/link/<project>/token      Authorization: Bearer <the project's shared secret>, {sub}:
                                         a linked user's service token; 401 bad secret (also when
                                         none is set), 404 NOT_LINKED otherwise (with link_url: the
                                         page where the person links), 429 after repeated failures
  GET  /api/v1/link/account              Authorization: Bearer <service token> with link:account:
                                         that user's Google sub and plan (links.account_view)
  GET  /api/v1/link/cairn/curves?gpx_sha256=
                                         link:calc, switched on by the person (SP-654): the saved
                                         calculations of that GPX with their curves (link_calc.py);
                                         403 CONSENT_REQUIRED off, 402 PLAN_REQUIRED free plan,
                                         400 BAD_GPX_SHA256

The user's side also has
  POST /api/v1/account/links/<project>/scopes   {scope, allowed}: one feature of a linked project
                                         switched on / off (links.FEATURES; 409 NOT_LINKED)
  GET  /api/v1/account/links/cairn/pushes   設定 › Cairn (SP-666): the races pushed (cairn_push
                                         table), newest first, with the trip's link in Cairn
  GET  /api/v1/account/links/cairn/events/<event id>
                                         the race page's 「推到 Cairn」 (SP-655, cairn_push.py):
                                         configured, linked, link:gpx_sync on, paid plan, pushed
                                         before (Cairn's trip id, when), ready (a curve to send)
  POST /api/v1/account/links/cairn/events/<event id>/push   {consent?}: create the trip in Cairn,
                                         or update the one pushed before; consent: true switches
                                         link:gpx_sync on first (the dialog's 「同意並推送」)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.accounts import links as LK, login as LG
from backend.i18n import N_, UserError
from backend.security import ratelimit as RL

router = APIRouter(tags=["link"], include_in_schema=False)
log = logging.getLogger("backend.links")

SETTINGS_URL = "/api/v1/app/settings"
NO_STORE = {"Cache-Control": "no-store"}


def _on() -> None:
    if not LG.required():
        raise HTTPException(404, "not found")


def _client(cid: str) -> LK.Client:
    c = LK.client(cid)
    if c is None:
        raise HTTPException(404, "not found")
    return c


def link_url(c: LK.Client) -> str:
    """Where the person links (設定 › 帳號, the project's row highlighted)."""
    return LK.link_url(c)


# ---------------------------------------------------------------------------- the user's side

def _signed_in():
    u = LG.current_user()
    if u is None:
        raise UserError(N_("請先登入"), code="LOGIN_REQUIRED", status=401)
    return u


@router.get("/api/v1/account/links")
async def account_links():
    _on()
    u = _signed_in()
    out = []
    for c in LK.CLIENTS.values():
        row = await run_in_threadpool(LK.state, u, c)
        if c is LK.CAIRN:
            row["plan_here"] = await run_in_threadpool(LK.plan_here)   # 設定 › Cairn's plan line (SP-666)
            if row["linked"]:
                row["remote"] = await run_in_threadpool(LK.cairn_status, u, c)
        out.append(row)
    return JSONResponse({"clients": out}, headers=NO_STORE)


class LinkIn(BaseModel):
    linked: Optional[bool] = None


@router.post("/api/v1/account/links/{cid}")
async def account_link(cid: str, body: LinkIn):
    _on()
    u = _signed_in()
    c = _client(cid)
    if body.linked is None:
        raise UserError(N_("要連結還是解除連結？"), code="BAD_REQUEST", status=400)
    if body.linked:
        if not u.google_sub:
            raise UserError(N_("這個帳號沒有用 Google 登入，不能連結"), code="NO_GOOGLE", status=409)
        if not LK.secret(c):
            raise UserError(N_("這個服務還沒開放連結"), code="LINK_UNAVAILABLE", status=409)
        await run_in_threadpool(LK.link, u, c)
    else:
        await run_in_threadpool(LK.unlink, u, c)
    return JSONResponse(await run_in_threadpool(LK.state, u, c), headers=NO_STORE)


class ScopeIn(BaseModel):
    scope: Optional[str] = None
    allowed: Optional[bool] = None


@router.post("/api/v1/account/links/{cid}/scopes")
async def account_link_scope(cid: str, body: ScopeIn):
    """One feature of a linked project switched on / off (SP-654, links.FEATURES)."""
    _on()
    u = _signed_in()
    c = _client(cid)
    if body.scope not in LK.features(c) or body.allowed is None:
        raise UserError(N_("沒有這個項目"), code="BAD_REQUEST", status=400)
    if not await run_in_threadpool(LK.is_linked, u, c):
        raise UserError(N_("要先連結才能開啟"), code="NOT_LINKED", status=409)
    await run_in_threadpool(LK.set_feature, u, c, body.scope, body.allowed)
    return JSONResponse(await run_in_threadpool(LK.state, u, c), headers=NO_STORE)


# ---------------------------------------------------------------------------- the project's side

class TokenIn(BaseModel):
    sub: Optional[str] = None


@router.post("/api/v1/link/{cid}/token")
async def link_token(cid: str, body: TokenIn, request: Request):
    _on()
    c = LK.client(cid)
    ip = RL.client_ip(request.scope)
    if c is None or not LK.secret_ok(c, LK.bearer(request.headers)):
        st = LK.failed(ip)
        raise HTTPException(st, {"code": "BLOCKED" if st == 429 else "SECRET_INVALID"},
                            headers={"Retry-After": str(LK.FAIL_BLOCK_S)} if st == 429
                            else {"WWW-Authenticate": "Bearer"})
    got = await run_in_threadpool(LK.issue_token, c, body.sub or "")
    if got is None:
        return JSONResponse(status_code=404, content={"detail": {"code": "NOT_LINKED", "link_url": link_url(c)}},
                            headers=NO_STORE)
    log.info("service token issued to %s", c.id)
    return JSONResponse(got, headers=NO_STORE)


def _service_user(request: Request, scope: str):
    """The token again (the gate checked it): its user must be the request's, its client the one
    the gate saw, and the scope there — a request can only ever read the token's own tenant."""
    from backend import membership as MS
    tok = LK.service_bearer(request.headers)
    user = MS.check_service_token(tok, scope)
    me = LG.current_user()
    st = request.scope.get("state") or {}
    c = LK.client(st.get("link_client") or "")
    if me is None or me.id != user.id or c is None:
        raise HTTPException(401, {"code": "TOKEN_INVALID"})
    if not LK.allowed(user, c, scope):          # the gate checked it too (SP-654)
        raise HTTPException(403, {"code": "CONSENT_REQUIRED", "scope": scope, "link_url": LK.link_url(c, scope)})
    return user, st.get("link_scopes") or frozenset()


@router.get("/api/v1/link/account")
async def link_account(request: Request):
    _on()
    user, scopes = await run_in_threadpool(_service_user, request, "link:account")
    return JSONResponse(await run_in_threadpool(LK.account_view, user, scopes), headers=NO_STORE)


@router.get("/api/v1/link/cairn/curves")
async def link_cairn_curves(request: Request, gpx_sha256: Optional[str] = None):
    """SP-654 (SP-570 TRC-4): this person's saved calculations for the GPX whose sha256 Cairn
    holds, each with its cairn-curve (SP-653); link:calc, switched on by the person, a paid plan."""
    from backend.accounts import link_calc as LC
    _on()
    await run_in_threadpool(_service_user, request, "link:calc")
    sha = LC.clean_sha256(gpx_sha256)
    if sha is None:
        raise HTTPException(400, {"code": "BAD_GPX_SHA256"})
    LC.require_plan("link:calc")
    return JSONResponse({"gpx_sha256": sha, "calcs": await run_in_threadpool(LC.saved_curves, sha)},
                        headers=NO_STORE)


# ---------------------------------------------------------------------------- 推到 Cairn (SP-655)

@router.get("/api/v1/account/links/cairn/pushes")
async def cairn_pushes():
    """設定 › Cairn (SP-666): the races pushed, newest first, each with its 「在 Cairn 打開」."""
    from backend.accounts import cairn_push as CP
    _on()
    _signed_in()
    return JSONResponse({"pushes": await run_in_threadpool(CP.history)}, headers=NO_STORE)


@router.get("/api/v1/account/links/cairn/events/{eid}")
async def cairn_push_status(eid: str):
    from backend.accounts import cairn_push as CP
    _on()
    u = _signed_in()
    return JSONResponse(await run_in_threadpool(CP.status, u, eid), headers=NO_STORE)


class PushIn(BaseModel):
    consent: Optional[bool] = None


@router.post("/api/v1/account/links/cairn/events/{eid}/push")
async def cairn_push(eid: str, body: PushIn):
    from backend.accounts import cairn_push as CP
    _on()
    u = _signed_in()
    return JSONResponse(await run_in_threadpool(CP.push, u, eid, bool(body.consent)), headers=NO_STORE)
