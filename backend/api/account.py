"""
Signing in with Google, the sign-up terms, signing out (SP-58; backend/accounts/login.py has the
rules). Every route answers 404 unless sign-in is on (login.required()); the demo instance never
mounts this router.

  GET  /login                    the sign-in page (signed in already: back to ?next)
  GET  /signup                   the terms page (the same page, its sign-up view)
  GET  /api/v1/account/login     start: a state, nonce and PKCE verifier kept server side
                                 (accounts.db oauth_states, 10 min, once), the browser bound by
                                 the trc_oauth cookie, then off to Google. 10 per IP in 15 min
  GET  /oauth/google/callback    back from Google: the state (once, this browser's), the code
                                 exchanged with the verifier, the id_token verified, then
                                 login.resolve(): a session, the terms, or /login?error=<why>.
                                 10 per IP in 15 min
  GET  /api/v1/account/signup    the terms to accept and who is signing up (the trc_signup ticket)
  POST /api/v1/account/signup    {accept: true, version}: the account (a new person) and the
                                 consent, then a session. CSRF-checked like every write
  POST /api/v1/account/logout    the session row deleted, the cookie cleared

/login?error= codes the page explains: expired, failed, cancelled, unverified, disabled, full,
rate_limited.
"""
from __future__ import annotations

import logging
import secrets as _secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from backend.accounts import google, login as LG, store, terms
from backend.i18n import N_, UserError
from backend.i18n.pages import render_page
from backend.security import ratelimit as RL
from backend.tenancy_mw import cookie, cookie_attrs

router = APIRouter(tags=["account"], include_in_schema=False)
log = logging.getLogger("backend.signin")       # not "…account": applog would mask "account: <word>"


def _on() -> None:
    if not LG.required():
        raise HTTPException(404, "not found")


def _set(resp, name: str, value: str, max_age: int) -> None:
    resp.headers.append("set-cookie", f"{name}={value}; {cookie_attrs(max_age, same_site='Lax')}")


def _clear(resp, name: str) -> None:
    resp.headers.append("set-cookie", f"{name}=; {cookie_attrs(0, same_site='Lax')}")


def _to_login(error: str) -> RedirectResponse:
    r = RedirectResponse(f"/login?error={error}", status_code=303)
    r.headers["Cache-Control"] = "no-store"
    return r


def _start_session(request: Request, resp, user: store.User) -> None:
    """A new token for this sign-in; the browser's previous session (if any) ends."""
    old = cookie(request.scope, LG.SESSION_COOKIE)
    if old:
        store.delete_session(old)
    _set(resp, LG.SESSION_COOKIE, store.create_session(user.id), LG.SESSION_MAX_AGE)


@router.get("/login")
def login_page(request: Request, next: Optional[str] = None):  # noqa: A002 — the query name
    _on()
    if LG.current_user() is not None:
        return RedirectResponse(LG.safe_next(next), status_code=303)
    return render_page("login", headers={"Cache-Control": "no-store"})


@router.get("/signup")
def signup_page():
    _on()
    return render_page("login", headers={"Cache-Control": "no-store"})


@router.get("/api/v1/account/login")
def start(request: Request, next: Optional[str] = None, provider: str = "google"):  # noqa: A002
    _on()
    if provider != "google":
        raise HTTPException(404, "unknown provider")
    if not LG.START_LIMIT.take(RL.client_ip(request.scope)):
        log.info("sign-in start refused: rate limited")
        return _to_login("rate_limited")
    state, nonce, verifier = (_secrets.token_urlsafe(32), _secrets.token_urlsafe(32), _secrets.token_urlsafe(64))
    # one binding per browser (two sign-in tabs both come back to it)
    binding = cookie(request.scope, LG.OAUTH_COOKIE) or ""
    if not (40 <= len(binding) <= 64 and binding.replace("-", "").replace("_", "").isalnum()):
        binding = _secrets.token_urlsafe(32)
    redirect = google.redirect_uri(str(request.base_url))
    store.put_oauth_state(state, binding, "google", nonce, verifier, redirect, LG.safe_next(next), LG.STATE_TTL_S)
    resp = RedirectResponse(google.authorize_url(state, nonce, verifier, redirect), status_code=302)
    resp.headers["Cache-Control"] = "no-store"
    _set(resp, LG.OAUTH_COOKIE, binding, LG.STATE_TTL_S)
    return resp


@router.get("/oauth/google/callback")
async def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    _on()
    if not LG.CALLBACK_LIMIT.take(RL.client_ip(request.scope)):
        log.info("sign-in callback refused: rate limited")
        return _to_login("rate_limited")
    row = store.pop_oauth_state(state)
    if error:
        return _to_login("cancelled")
    if row is None:
        return _to_login("expired")
    if not store.binding_matches(row, cookie(request.scope, LG.OAUTH_COOKIE)):
        log.warning("sign-in refused: the callback came to another browser than the one that started it")
        return _to_login("failed")
    try:
        ident = await google.identity(code, row["code_verifier"], row["redirect_uri"], row["nonce"])
    except google.LoginFailed as e:
        log.warning("sign-in refused: %s", e.reason)
        return _to_login("failed")
    out = LG.resolve(ident, row["next_url"])
    if out.kind == "refused":
        return _to_login(out.reason or "failed")
    if out.kind == "terms":
        resp = RedirectResponse("/signup", status_code=303)
        _set(resp, LG.SIGNUP_COOKIE, out.ticket, LG.TICKET_TTL_S)
    else:
        resp = RedirectResponse(LG.safe_next(row["next_url"]), status_code=303)
        _start_session(request, resp, out.user)
    resp.headers["Cache-Control"] = "no-store"
    _clear(resp, LG.OAUTH_COOKIE)
    return resp


@router.get("/api/v1/account/signup")
def signup_info(request: Request):
    _on()
    t = store.signup_ticket(cookie(request.scope, LG.SIGNUP_COOKIE))
    if t is None:
        raise UserError(N_("登入逾時，請重新登入"), code="SIGNUP_EXPIRED", status=404)
    return JSONResponse({"kind": t["kind"], "name": t.get("display_name"), "email": t.get("email"),
                         "terms": terms.public()}, headers={"Cache-Control": "no-store"})


class SignupIn(BaseModel):
    accept: bool = False
    version: str = ""


@router.post("/api/v1/account/signup")
def signup(body: SignupIn, request: Request):
    _on()
    token = cookie(request.scope, LG.SIGNUP_COOKIE)
    if store.signup_ticket(token) is None:
        raise UserError(N_("登入逾時，請重新登入"), code="SIGNUP_EXPIRED", status=404)
    if not body.accept:
        raise UserError(N_("請先勾選同意條款"), code="CONSENT_REQUIRED", status=400)
    if body.version != terms.VERSION:
        raise UserError(N_("條款剛更新了，請重新閱讀後再同意"), code="TERMS_CHANGED", status=409)
    ticket = store.take_signup_ticket(token)
    if ticket is None:
        raise UserError(N_("登入逾時，請重新登入"), code="SIGNUP_EXPIRED", status=404)
    out = LG.accept_terms(ticket)
    if out.kind != "session":
        resp = JSONResponse({"ok": False, "next": f"/login?error={out.reason or 'failed'}"})
    else:
        resp = JSONResponse({"ok": True, "next": LG.safe_next(ticket["next_url"])})
        _start_session(request, resp, out.user)
    resp.headers["Cache-Control"] = "no-store"
    _clear(resp, LG.SIGNUP_COOKIE)
    return resp


@router.post("/api/v1/account/logout")
def logout(request: Request):
    _on()
    store.delete_session(cookie(request.scope, LG.SESSION_COOKIE))
    resp = JSONResponse({"ok": True, "next": "/login?signed_out=1"})
    _clear(resp, LG.SESSION_COOKIE)
    return resp
