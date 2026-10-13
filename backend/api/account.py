"""
Signing in with Google, the sign-up terms, signing out (SP-58; backend/accounts/login.py has the
rules). Every route answers 404 unless sign-in is on (login.required()); the demo instance never
mounts this router.

  GET  /login                    the sign-in page (signed in already: back to ?next)
  GET  /signup                   the terms page (the same page, its sign-up view)
  GET  /api/v1/account/login     start: a state, nonce and PKCE verifier kept server side
                                 (accounts.db oauth_states, 10 min, once), the browser bound by
                                 the trc_oauth cookie, then off to Google. 10 per client in 15 min
  GET  /oauth/google/callback    back from Google: the state (once, this browser's), then — only
                                 then counted, 10 per client in 15 min — the code exchanged with
                                 the verifier, the id_token verified, login.resolve(): a session,
                                 the terms, or /login?error=<why>
  GET  /api/v1/account/signup    the terms to accept (with their digest) and who: the trc_signup
                                 ticket, or a signed-in user the new terms stopped
  POST /api/v1/account/signup    {accept: true, version, digest[, next]}: the consent for the very
                                 texts shown (and the account, for a new person), then a session.
                                 CSRF-checked like every write
  POST /api/v1/account/logout    the session row deleted, the cookie cleared
  GET  /legal                    the legal documents (SP-60): a list; public, signed in or not
  GET  /legal/<id>               one of them (terms.DOCUMENTS: terms, privacy, health, third_party),
                                 rendered on the server — the privacy policy's address for Google's
                                 OAuth consent screen (SP-387). Only these exact paths are public
                                 (login.PUBLIC_PATHS); an unknown id signed out is the gate's 401

Cookies are named by login.cookie_name (__Host- over https). /login?error= codes the page
explains: expired, failed, cancelled, unverified, disabled, full, rate_limited, setup.
"""
from __future__ import annotations

import html
import logging
import secrets as _secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.accounts import google, login as LG, store, terms
from backend.i18n import N_, UserError, _
from backend.i18n.pages import render_page
from backend.security import ratelimit as RL
from backend.tenancy_mw import cookie

router = APIRouter(tags=["account"], include_in_schema=False)
log = logging.getLogger("backend.signin")       # not "…account": applog would mask "account: <word>"


def _on() -> None:
    if not LG.required():
        raise HTTPException(404, "not found")


def _set(resp, base: str, value: str, max_age: int) -> None:
    resp.headers.append("set-cookie", f"{LG.cookie_name(base)}={value}; {LG.cookie_attrs(max_age)}")


def _clear(resp, base: str) -> None:
    resp.headers.append("set-cookie", f"{LG.cookie_name(base)}=; {LG.cookie_attrs(0)}")


def _get(request: Request, base: str) -> Optional[str]:
    return cookie(request.scope, LG.cookie_name(base))


def _to_login(error: str) -> RedirectResponse:
    r = RedirectResponse(f"/login?error={error}", status_code=303)
    r.headers["Cache-Control"] = "no-store"
    return r


def _start_session(request: Request, resp, user: store.User) -> None:
    """A new token for this sign-in; the browser's previous session (if any) ends."""
    old = _get(request, LG.SESSION_COOKIE)
    if old:
        store.delete_session(old)
    _set(resp, LG.SESSION_COOKIE, store.create_session(user.id), LG.SESSION_MAX_AGE)


def _client_key(request: Request) -> str:
    return RL.limit_key(RL.client_ip(request.scope))


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
    if not LG.START_LIMIT.take(_client_key(request)):
        log.info("sign-in start refused: rate limited")
        return _to_login("rate_limited")
    state, nonce, verifier = (_secrets.token_urlsafe(32), _secrets.token_urlsafe(32), _secrets.token_urlsafe(64))
    # one binding per browser (two sign-in tabs both come back to it)
    binding = _get(request, LG.OAUTH_COOKIE) or ""
    if not (40 <= len(binding) <= 64 and binding.replace("-", "").replace("_", "").isalnum()):
        binding = _secrets.token_urlsafe(32)
    redirect = google.redirect_uri()
    store.put_oauth_state(state, binding, "google", nonce, verifier, redirect, LG.safe_next(next), LG.STATE_TTL_S)
    resp = RedirectResponse(google.authorize_url(state, nonce, verifier, redirect), status_code=302)
    resp.headers["Cache-Control"] = "no-store"
    _set(resp, LG.OAUTH_COOKIE, binding, LG.STATE_TTL_S)
    return resp


@router.get("/oauth/google/callback")
async def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    _on()
    row = await run_in_threadpool(store.pop_oauth_state, state)
    if error:
        return _to_login("cancelled")
    if row is None:                       # made up, used already or expired: costs no one's quota
        return _to_login("expired")
    if not store.binding_matches(row, _get(request, LG.OAUTH_COOKIE)):
        log.warning("sign-in refused: the callback came to another browser than the one that started it")
        return _to_login("failed")
    if not LG.CALLBACK_LIMIT.take(_client_key(request)):
        log.info("sign-in callback refused: rate limited")
        return _to_login("rate_limited")
    try:
        ident = await google.identity(code, row["code_verifier"], row["redirect_uri"], row["nonce"])
    except google.LoginFailed as e:
        log.warning("sign-in refused: %s", e.reason)
        return _to_login("failed")
    out = await run_in_threadpool(LG.resolve, ident, row["next_url"])
    if out.kind == "refused":
        return _to_login(out.reason or "failed")
    if out.kind == "terms":
        resp = RedirectResponse("/signup", status_code=303)
        _set(resp, LG.SIGNUP_COOKIE, out.ticket, LG.TICKET_TTL_S)
    else:
        resp = RedirectResponse(LG.safe_next(row["next_url"]), status_code=303)
        await run_in_threadpool(_start_session, request, resp, out.user)
    resp.headers["Cache-Control"] = "no-store"
    _clear(resp, LG.OAUTH_COOKIE)
    return resp


def _reconsent_user() -> Optional[store.User]:
    """Signed in, but the terms changed since this user accepted them (the gate sent them here)."""
    u = LG.current_user()
    return u if u is not None and not LG.consent_current(u) else None


@router.get("/api/v1/account/signup")
def signup_info(request: Request):
    _on()
    t = store.signup_ticket(_get(request, LG.SIGNUP_COOKIE))
    if t is not None:
        who = {"kind": t["kind"], "name": t.get("display_name"), "email": t.get("email")}
    elif (u := _reconsent_user()) is not None:
        who = {"kind": "reconsent", "name": u.display_name, "email": u.email}
    else:
        raise UserError(N_("登入逾時，請重新登入"), code="SIGNUP_EXPIRED", status=404)
    return JSONResponse({**who, "terms": terms.public()}, headers={"Cache-Control": "no-store"})


class SignupIn(BaseModel):
    accept: bool = False
    version: str = ""
    digest: str = ""              # the sha256 of the set of texts the page showed (terms.public())
    next: Optional[str] = None    # signed in already (new terms): where to go back to


@router.post("/api/v1/account/signup")
def signup(body: SignupIn, request: Request):
    _on()
    token = _get(request, LG.SIGNUP_COOKIE)
    ticketed = store.signup_ticket(token) is not None
    signed_in = None if ticketed else _reconsent_user()
    if not ticketed and signed_in is None:
        raise UserError(N_("登入逾時，請重新登入"), code="SIGNUP_EXPIRED", status=404)
    if not body.accept:
        raise UserError(N_("請先勾選同意條款"), code="CONSENT_REQUIRED", status=400)
    documents = terms.accepted(body.version, body.digest)
    if documents is None:
        raise UserError(N_("條款剛更新了，請重新閱讀後再同意"), code="TERMS_CHANGED", status=409)
    if signed_in is not None:
        LG.accept_terms_signed_in(signed_in, documents)
        resp = JSONResponse({"ok": True, "next": LG.safe_next(body.next)})
        resp.headers["Cache-Control"] = "no-store"
        return resp
    ticket = store.take_signup_ticket(token)
    if ticket is None:
        raise UserError(N_("登入逾時，請重新登入"), code="SIGNUP_EXPIRED", status=404)
    out = LG.accept_terms(ticket, documents)
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
    store.delete_session(_get(request, LG.SESSION_COOKIE))
    resp = JSONResponse({"ok": True, "next": "/login?signed_out=1"})
    _clear(resp, LG.SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------- the legal documents (SP-60)

_E = html.escape


def _doc_meta(d: terms.Doc) -> str:
    m = terms.meta(d)
    return _E(_("版本 {version}・生效日 {effective}・最後更新 {updated}", version=m["version"],
                effective=m["effective"], updated=m["updated"]), quote=False)


def _legal(title: str, main: str):
    fill = {
        "title": _E(f"{title} · TrailRunCoach", quote=False),
        # a draft is not for search engines; the lawyer's version is
        "robots": '<meta name="robots" content="noindex">' if terms.DRAFT else "",
        "draft": "1" if terms.DRAFT else "0",          # <body data-draft>: the banner (CSS, no script)
        "main": main,
    }
    # private: the page inlines the session (a signed-in user's name), like every other page
    return render_page("legal", headers={"Cache-Control": "private, no-store"}, fill=fill)


@router.get("/legal")
def legal_index():
    _on()
    items = "".join(
        f'<li><a href="{terms.URL_PREFIX}{d.id}">{_E(_(d.title), quote=False)}</a>'
        f'<span class="meta">{_doc_meta(d)}</span></li>' for d in terms.DOCUMENTS)
    h = _("法律文件")
    return _legal(h, f'<h1>{_E(h, quote=False)}</h1><ul class="doc-list">{items}</ul>')


@router.get("/legal/{doc_id}")
def legal_doc(doc_id: str):
    _on()
    d = terms.by_id(doc_id)
    if d is None:
        raise HTTPException(404, "not found")
    title = _E(_(d.title), quote=False)
    main = (f'<h1>{title}</h1><p class="meta">{_doc_meta(d)}</p>'
            f'<article class="doc" lang="zh-Hant">{terms.to_html(terms.body(d))}</article>')
    return _legal(_(d.title), main)
