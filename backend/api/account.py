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

The account's own data (SP-386; accounts/export.py, accounts/deletion.py), signed in only:
  GET  /api/v1/account/me          who, whether the account can be deleted here (never the owner),
                                   whether this session confirmed who it is at Google in the last
                                   REAUTH_TTL_S, the cross-user analysis consent (None: the owner)
  GET  /api/v1/account/export.zip  下載我的資料: the user's own data (USER + IMPORTED of the data
                                   registry, never a credential), streamed
  GET  /api/v1/account/reauth      「用 Google 確認身分」 before a deletion: a Google sign-in like
                                   /account/login (purpose "reauth", the user's id kept with the
                                   state); its callback only marks THIS session (sessions.reauth_at)
                                   when Google's account is the signed-in user's own — no new
                                   session, nobody signed in — and goes back to the settings page
                                   with ?reauth=ok | mismatch | failed. Not for the owner
  POST /api/v1/account/delete      {confirm: true}: 刪除帳號 now — 403 OWNER_PROTECTED for the owner,
                                   403 REAUTH_REQUIRED without a confirmation in the last
                                   REAUTH_TTL_S, 409 SYNC_BUSY while a sync runs; then the session
                                   cookie cleared and {next: /login?deleted=1}
  POST /api/v1/account/analysis    {analysis: false | true}: withdraw (or give again) the cross-user
                                   analysis consent bundled with the terms — one more consents row
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
from backend.accounts import deletion as DEL, export as EX
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
    return _to_google(request, LG.safe_next(next))


def _to_google(request: Request, next_url: str, purpose: str = "signin", user: Optional[store.User] = None):
    """Off to Google: a state, nonce and PKCE verifier kept server side, the browser bound by the
    trc_oauth cookie. purpose "reauth": `user` confirming who they are (SP-386)."""
    if not LG.START_LIMIT.take(_client_key(request)):
        log.info("sign-in start refused: rate limited")
        return _to_login("rate_limited") if purpose == "signin" else _back_from_reauth("rate_limited")
    state, nonce, verifier = (_secrets.token_urlsafe(32), _secrets.token_urlsafe(32), _secrets.token_urlsafe(64))
    # one binding per browser (two sign-in tabs both come back to it)
    binding = _get(request, LG.OAUTH_COOKIE) or ""
    if not (40 <= len(binding) <= 64 and binding.replace("-", "").replace("_", "").isalnum()):
        binding = _secrets.token_urlsafe(32)
    redirect = google.redirect_uri()
    store.put_oauth_state(state, binding, "google", nonce, verifier, redirect, next_url, LG.STATE_TTL_S,
                          purpose=purpose, user_id=user.id if user is not None else None)
    hint = user.email if user is not None else None
    resp = RedirectResponse(google.authorize_url(state, nonce, verifier, redirect, login_hint=hint), status_code=302)
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
    reauth = row.get("purpose") == "reauth"
    try:
        ident = await google.identity(code, row["code_verifier"], row["redirect_uri"], row["nonce"])
    except google.LoginFailed as e:
        log.warning("sign-in refused: %s", e.reason)
        return _back_from_reauth("failed") if reauth else _to_login("failed")
    if reauth:
        resp = await run_in_threadpool(_reauth_done, request, row, ident)
        _clear(resp, LG.OAUTH_COOKIE)
        return resp
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


# ---------------------------------------------------------------------------- the account's own data (SP-386)

REAUTH_TTL_S = 10 * 60             # a Google confirmation counts this long for a deletion
SETTINGS_URL = "/api/v1/app/settings"
DELETED_NEXT = "/login?deleted=1"


def _signed_in() -> store.User:
    u = LG.current_user()
    if u is None:                       # the gate answers 401 first; never reached signed out
        raise UserError(N_("請先登入"), code="LOGIN_REQUIRED", status=401)
    return u


def _owner_refused(u: store.User) -> None:
    if u.is_owner:
        raise UserError(N_("站主帳號不能從網頁刪除"), code="OWNER_PROTECTED", status=403)


def _reauth_ok(request: Request) -> bool:
    """This session confirmed who it is at Google within REAUTH_TTL_S."""
    import datetime as _dt
    at = store.reauth_at(_get(request, LG.SESSION_COOKIE))
    if at is None:
        return False
    age = (_dt.datetime.now(_dt.timezone.utc) - at).total_seconds()
    return 0 <= age < REAUTH_TTL_S


def _back_from_reauth(outcome: str) -> RedirectResponse:
    r = RedirectResponse(f"{SETTINGS_URL}?reauth={outcome}#account", status_code=303)
    r.headers["Cache-Control"] = "no-store"
    return r


def _reauth_done(request: Request, row: dict, ident: google.Identity) -> RedirectResponse:
    """Back from Google with a confirmation: only the signed-in user who asked for it, with their
    own Google account, marks this session. Never a new session, never a sign-in."""
    u = LG.current_user()
    if u is None or u.is_owner or u.id != row.get("user_id"):
        outcome = "failed"
    else:
        same = store.find_identity(ident.provider, ident.subject)
        if same is None or same.id != u.id:
            outcome = "mismatch"
        else:
            outcome = "ok" if store.mark_reauth(_get(request, LG.SESSION_COOKIE), u.id) else "failed"
    log.info("deletion confirmation at Google: %s", outcome)
    return _back_from_reauth(outcome)


@router.get("/api/v1/account/me")
def account_me(request: Request):
    _on()
    u = _signed_in()
    return JSONResponse({"id": u.id, "name": u.display_name, "email": u.email, "role": u.role,
                         "created_at": u.created_at, "can_delete": not u.is_owner,
                         "reauth_ok": (not u.is_owner) and _reauth_ok(request), "reauth_ttl_s": REAUTH_TTL_S,
                         "analysis": store.analysis_consent(u.id), "export_url": "/api/v1/account/export.zip"},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/v1/account/export.zip")
def account_export():
    from fastapi.responses import StreamingResponse
    from backend import membership as MS, tenancy
    _on()
    u = _signed_in()
    MS.require("export:data")
    exp = EX.prepare(u, tenancy.current())
    log.info("data export: %d files, %d tables", exp.manifest["files"]["count"], len(exp.manifest["tables"]))
    name = f"trailruncoach-my-data-{exp.manifest['created_at'][:10]}.zip"
    return StreamingResponse(EX.stream(exp), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"',
                                      "Cache-Control": "private, no-store"})


@router.get("/api/v1/account/reauth")
def account_reauth(request: Request):
    _on()
    u = _signed_in()
    _owner_refused(u)
    return _to_google(request, SETTINGS_URL, purpose="reauth", user=u)


class DeleteIn(BaseModel):
    confirm: bool = False


@router.post("/api/v1/account/delete")
async def account_delete(body: DeleteIn, request: Request):
    from backend.sync import runner
    _on()
    u = _signed_in()
    _owner_refused(u)
    if not await run_in_threadpool(_reauth_ok, request):
        raise UserError(N_("請先用 Google 再確認一次身分"), code="REAUTH_REQUIRED", status=403)
    if not body.confirm:
        raise UserError(N_("請先確認要刪除帳號"), code="CONFIRM_REQUIRED", status=400)
    try:
        out = await DEL.delete_account(u)
    except runner.SyncBusy:
        raise UserError(N_("同步正在進行，等它結束後再刪除帳號"), code="SYNC_BUSY", status=409) from None
    resp = JSONResponse({"ok": True, "next": DELETED_NEXT, "disconnected": out["disconnected"],
                         "revoked": out["revoked"], "revoke_failed": out["revoke_failed"]})
    resp.headers["Cache-Control"] = "no-store"
    _clear(resp, LG.SESSION_COOKIE)
    return resp


class AnalysisIn(BaseModel):
    analysis: Optional[bool] = None


@router.post("/api/v1/account/analysis")
def account_analysis(body: AnalysisIn):
    _on()
    u = _signed_in()
    now = store.analysis_consent(u.id)
    if now is None or body.analysis is None:
        raise UserError(N_("這個帳號沒有同意紀錄可以變更"), code="NO_CONSENT", status=409 if now is None else 400)
    if body.analysis != now:
        store.record_analysis(u.id, body.analysis)
        log.info("cross-user analysis consent %s", "given again" if body.analysis else "withdrawn")
    return JSONResponse({"analysis": store.analysis_consent(u.id)}, headers={"Cache-Control": "no-store"})


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
