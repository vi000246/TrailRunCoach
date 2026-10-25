"""
Signing up and in with an e-mail address (SP-610; backend/accounts/email_login.py has the rules).
Like api/account.py: every route answers 404 unless sign-in is on, the demo never mounts it, every
POST is CSRF-checked by the gate (login.PUBLIC_PATHS lets these exact paths through signed out).

  GET  /register, /forgot-password      the sign-in page's other views (static/login.html)
  GET  /verify-email, /reset-password   the pages the mails link to; the token is in the URL's
                                        fragment (never sent to a server, never in a log or a
                                        Referer), the page posts it
  POST /api/v1/account/email/register   {email, password, name?, next?} -> {ok, next: /login?sent=register}
                                        whatever the address (EMAIL_INVALID / PASSWORD_* only for
                                        what was typed); 10 per client in 15 min
  POST /api/v1/account/email/verify     {token, password} -> {ok, next}: the terms (a sign-up ticket
                                        cookie), a session, or {ok: false, next: /login?error=…};
                                        404 LINK_INVALID, 401 PASSWORD_WRONG; 10 per client in 15 min
  POST /api/v1/account/email/login      {email, password, next?} -> as verify; 401 LOGIN_FAILED,
                                        403 EMAIL_UNVERIFIED (the link mailed again); 10 per client
                                        and 10 per address in 15 min
  POST /api/v1/account/email/forgot     {email} -> {ok, next: /login?sent=reset} whatever the address
  POST /api/v1/account/email/reset      {token, password} -> {ok, next: /login?reset=1}; every session
                                        of the account ended; 404 LINK_INVALID
  POST /api/v1/account/email/reauth     {password}, signed in: 刪除帳號's 確認身分 for an e-mail account
                                        (the Google one can't confirm it): this session marked like
                                        /api/v1/account/reauth's callback does; 401 PASSWORD_WRONG
  POST /api/v1/account/email/password   {current, password}, signed in (SP-858, 設定 › 帳號 › 密碼):
                                        the new password; the account's other sessions end, this
                                        one stays -> {ok, signed_out: n}; 401 PASSWORD_WRONG
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.accounts import email_login as EL, login as LG
from backend.api.account import _clear, _client_key, _on, _set, _start_session
from backend.i18n import N_, UserError
from backend.i18n.pages import render_page

router = APIRouter(tags=["account"], include_in_schema=False)

# what the page shows for each refusal (msgids; the page has its own text for the common ones)
_MESSAGES = {
    "EMAIL_INVALID": (N_("請輸入有效的 email"), 400),
    "PASSWORD_TOO_SHORT": (N_("密碼至少要 10 個字元"), 400),
    "PASSWORD_TOO_LONG": (N_("密碼最多 128 個字元"), 400),
    "PASSWORD_WEAK": (N_("這個密碼太容易被猜到，請換一個（不要用 email、常見密碼或同一個字重複）"), 400),
    "PASSWORD_WRONG": (N_("密碼不對：請輸入註冊時設定的密碼"), 401),
    "LINK_INVALID": (N_("這個連結無效、已用過或已過期"), 404),
    "LOGIN_FAILED": (N_("Email 或密碼不正確"), 401),
}
_RATE = (N_("嘗試太多次，請 15 分鐘後再試"), 429)


def _refused(code: str) -> UserError:
    msg, status = _MESSAGES.get(code, (N_("出了點問題，請再試一次"), 400))
    return UserError(msg, code=code, status=status)


def _limited(limit, key: str) -> None:
    if not limit.take(key):
        raise UserError(_RATE[0], code="RATE_LIMITED", status=_RATE[1])


def _json(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _outcome(request: Request, out: LG.Outcome, next_url: str) -> JSONResponse:
    """A resolved sign-in as the page wants it: where to go, with the cookie it needs."""
    if out.kind == "refused":
        return _json({"ok": False, "next": LG.error_url(out.reason or "failed", next_url)})
    if out.kind == "terms":
        resp = _json({"ok": True, "next": "/signup"})
        _set(resp, LG.SIGNUP_COOKIE, out.ticket, LG.TICKET_TTL_S)
        return resp
    resp = _json({"ok": True, "next": LG.safe_next(next_url)})
    _start_session(request, resp, out.user)
    _clear(resp, LG.SIGNUP_COOKIE)
    return resp


@router.get("/register")
@router.get("/forgot-password")
@router.get("/reset-password")
@router.get("/verify-email")
def email_pages(request: Request):
    _on()
    return render_page("login", headers={"Cache-Control": "no-store"})


class RegisterIn(BaseModel):
    email: str = ""
    password: str = ""
    name: Optional[str] = None
    next: Optional[str] = None


@router.post("/api/v1/account/email/register")
def register(body: RegisterIn, request: Request):
    _on()
    _limited(EL.MAIL_LIMIT, _client_key(request))
    try:
        EL.register(body.email, body.password, body.name, LG.safe_next(body.next))
    except EL.Refused as e:
        raise _refused(e.code) from None
    return _json({"ok": True, "next": "/login?sent=register"})


class TokenPasswordIn(BaseModel):
    token: str = ""
    password: str = ""
    next: Optional[str] = None


@router.post("/api/v1/account/email/verify")
def verify(body: TokenPasswordIn, request: Request):
    _on()
    _limited(EL.LINK_LIMIT, _client_key(request))
    try:
        out = EL.verify(body.token, body.password, LG.safe_next(body.next))
    except EL.Refused as e:
        raise _refused(e.code) from None
    return _outcome(request, out, LG.safe_next(body.next))


class LoginIn(BaseModel):
    email: str = ""
    password: str = ""
    next: Optional[str] = None


@router.post("/api/v1/account/email/login")
def login(body: LoginIn, request: Request):
    _on()
    _limited(EL.LOGIN_LIMIT, _client_key(request))
    _limited(EL.LOGIN_ADDRESS_LIMIT, EL.normalize_email(body.email) or "-")
    try:
        res = EL.login(body.email, body.password, LG.safe_next(body.next))
    except EL.Refused as e:
        raise _refused(e.code) from None
    if res.unverified:
        raise UserError(N_("這個 email 還沒驗證：我們重新寄了一封驗證信，請打開信裡的連結"),
                        code="EMAIL_UNVERIFIED", status=403)
    return _outcome(request, res.outcome, LG.safe_next(body.next))


class EmailIn(BaseModel):
    email: str = ""


@router.post("/api/v1/account/email/forgot")
def forgot(body: EmailIn, request: Request):
    _on()
    _limited(EL.MAIL_LIMIT, _client_key(request))
    try:
        EL.forgot(body.email)
    except EL.Refused as e:
        raise _refused(e.code) from None
    return _json({"ok": True, "next": "/login?sent=reset"})


class PasswordIn(BaseModel):
    password: str = ""


@router.post("/api/v1/account/email/reauth")
def reauth(body: PasswordIn, request: Request):
    """刪除帳號前確認身分 for an e-mail account (SP-386's Google confirmation, with the password
    instead): the right password marks THIS session for REAUTH_TTL_S, like the Google callback does.
    Signed in only (the gate answers 401 first); 409 NO_EMAIL_SIGN_IN for an account without one."""
    from backend.accounts import passwords, store
    from backend.api.account import _get, _owner_refused, _signed_in
    _on()
    u = _signed_in()
    _owner_refused(u)
    _limited(EL.LOGIN_LIMIT, _client_key(request))
    row = store.email_account_of(u.id)
    if row is None:
        raise UserError(N_("這個帳號不是用 email 登入的"), code="NO_EMAIL_SIGN_IN", status=409)
    if not passwords.verify(body.password, row["password_hash"]):
        raise UserError(N_("密碼不對，請再試一次"), code="PASSWORD_WRONG", status=401)
    if not store.mark_reauth(_get(request, LG.SESSION_COOKIE), u.id):
        raise _refused("LINK_INVALID")
    return _json({"ok": True})


class ChangePasswordIn(BaseModel):
    current: str = ""
    password: str = ""


@router.post("/api/v1/account/email/password")
def change_password(body: ChangePasswordIn, request: Request):
    """設定 › 帳號 › 密碼 (SP-858): an e-mail account's new password, the current one asked first.
    Every other session of the account ends, this one stays. Signed in only (the gate answers 401
    first); 401 PASSWORD_WRONG, 400 PASSWORD_*, 409 NO_EMAIL_SIGN_IN; the sign-in's limits (10 per
    client and per address in 15 min: the current password can't be guessed here instead)."""
    from backend.accounts import store
    from backend.api.account import _get, _signed_in
    _on()
    u = _signed_in()
    _limited(EL.LOGIN_LIMIT, _client_key(request))
    row = store.email_account_of(u.id)
    if row is None:
        raise UserError(N_("這個帳號不是用 email 登入的"), code="NO_EMAIL_SIGN_IN", status=409)
    _limited(EL.LOGIN_ADDRESS_LIMIT, row["email"])
    try:
        ended = EL.change_password(u.id, body.current, body.password, _get(request, LG.SESSION_COOKIE))
    except EL.Refused as e:
        if e.code == "PASSWORD_WRONG":
            raise UserError(N_("目前的密碼不對，請再試一次"), code="PASSWORD_WRONG", status=401) from None
        raise _refused(e.code) from None
    return _json({"ok": True, "signed_out": ended})


@router.post("/api/v1/account/email/reset")
def reset(body: TokenPasswordIn, request: Request):
    _on()
    _limited(EL.LINK_LIMIT, _client_key(request))
    try:
        EL.reset(body.token, body.password)
    except EL.Refused as e:
        raise _refused(e.code) from None
    return _json({"ok": True, "next": "/login?reset=1"})
