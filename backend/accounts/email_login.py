"""
Signing up and in with an e-mail address and a password (SP-610; owner 2026-10-15 Q1: e-mail +
password, the name typed at sign-up only for display; 2026-10-20 Q2: Resend, one table in
accounts.db, no 疊石 Cairn link for these accounts until SP-329 settles it).

Only in sign-in mode (login.required()), next to Google's, under the same rules (login.resolve):

  register(email, password, name)   always the same answer — whether the address has an account is
                                    never told. A new address (or an unconfirmed sign-up, taken
                                    over): the password's Argon2id hash stored and a link to
                                    confirm it mailed (24 h). An address with an e-mail sign-in
                                    already: a mail saying so (sign in / reset the password); a
                                    Google account's (or the owner's) address: a mail saying to use
                                    Google. Nothing else changes.
  verify(token, password)           the link from the mail + the password set at sign-up: the
                                    address confirmed (the link taken once), then resolve(). The
                                    password is asked so that a link someone else's sign-up sent to
                                    this inbox can't confirm their password
  login(email, password)            a confirmed address with the right password -> resolve(); not
                                    confirmed yet -> EMAIL_UNVERIFIED and the link mailed again;
                                    anything else -> the same LOGIN_FAILED (an unknown address costs
                                    the same hash check: passwords.DUMMY)
  forgot(email) / reset(token, pw)  a reset link (1 h, taken once); the new password ends every
                                    session of the account and counts as confirming the address
  change_password(uid, cur, new, t) signed in (設定 › 帳號, SP-858): the current password, then the
                                    new one; every other session of the account ends, this one stays
  resolve(row)                      who a confirmed address is, login.resolve's rules: its account
                                    (disabled -> refused; the terms accepted -> a session; else the
                                    terms again); no account yet: the owner not signed in -> "setup";
                                    an account an admin let in from the waitlist with this address
                                    -> taken over, then the terms; the address a Google account's ->
                                    "google_account"; full -> the waitlist ("full"); else the terms
                                    (a signup ticket, provider "email") and the account is made on
                                    consent (accept_terms, the cap checked again in the same
                                    transaction)

Limits (backend/security/ratelimit Window, like Google's sign-in): per client 10 sign-ins, 10 mails
asked for (sign-up + reset), 10 links used, in any 15 minutes; per address 10 sign-ins in 15
minutes and 3 mails an hour (more are dropped without a word: the answer stays the same).
Logged: what happened, never the address, the password, a token or a link.
"""
from __future__ import annotations

import logging
import re
import secrets as _secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from backend.accounts import login as LG, mailer, passwords, store
from backend.i18n import _
from backend.security import ratelimit as RL

log = logging.getLogger("backend.signin")

VERIFY_TTL_S = 24 * 3600
RESET_TTL_S = 3600
EMAIL_MAX = 254
NAME_MAX = 40
TOKEN_MAX = 200

LOGIN_LIMIT = RL.Window(limit=10, per=15 * 60)            # sign-ins per client
LOGIN_ADDRESS_LIMIT = RL.Window(limit=10, per=15 * 60)    # sign-ins per address (many clients, one account)
MAIL_LIMIT = RL.Window(limit=10, per=15 * 60)             # sign-ups + resets asked for, per client
MAIL_ADDRESS_LIMIT = RL.Window(limit=3, per=3600)         # mails to one address
LINK_LIMIT = RL.Window(limit=10, per=15 * 60)             # verify / reset links used, per client

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}$")


def reset_limits() -> None:
    for w in (LOGIN_LIMIT, LOGIN_ADDRESS_LIMIT, MAIL_LIMIT, MAIL_ADDRESS_LIMIT, LINK_LIMIT):
        w.reset()


class Refused(ValueError):
    """A request the e-mail sign-in turns down: `code` for the page (and the API's UserError)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_email(raw) -> Optional[str]:
    """The address lower-cased and trimmed when it looks like one (no spaces or control characters,
    one @, a dot in the domain, at most 254 characters); None otherwise."""
    if not isinstance(raw, str):
        return None
    e = raw.strip().lower()
    if not e or len(e) > EMAIL_MAX or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in e):
        return None
    return e if _EMAIL.match(e) else None


def clean_name(raw) -> Optional[str]:
    """The display name typed at sign-up: no control characters, trimmed, at most NAME_MAX; None when empty."""
    if not isinstance(raw, str):
        return None
    s = "".join(ch for ch in raw if ord(ch) >= 0x20 and ord(ch) != 0x7F).strip()
    return s[:NAME_MAX] or None


def password_problem(password, email: Optional[str] = None) -> None:
    p = passwords.problem(password, email)
    if p:
        raise Refused({"too_short": "PASSWORD_TOO_SHORT", "too_long": "PASSWORD_TOO_LONG"}.get(p, "PASSWORD_WEAK"))


# ---------------------------------------------------------------------------- the mails

def _url(path: str, query: Optional[dict] = None, fragment: str = "") -> str:
    from backend.accounts import google
    base = (google.public_url() or "").rstrip("/")
    q = ("?" + urlencode(query)) if query else ""
    return f"{base}{path}{q}" + (f"#{fragment}" if fragment else "")


def _hello(name: Optional[str]) -> str:
    return _("{name} 你好：", name=name) if name else _("你好：")


def _mail(to: str, kind: str, subject: str, text: str) -> None:
    """Hand a mail over unless this address got MAIL_ADDRESS_LIMIT already (dropped quietly)."""
    if not MAIL_ADDRESS_LIMIT.take(to):
        log.info("mail (%s) dropped: too many to one address", kind)
        return
    mailer.send(mailer.Mail(to=to, subject=subject, text=text, kind=kind))


def _mail_verify(email: str, name: Optional[str], token: str, next_url: str) -> None:
    link = _url("/verify-email", {"next": next_url} if next_url != "/" else None, token)
    _mail(email, "verify", _("確認你的 email · TrailRunCoach"),
          _hello(name) + "\n\n"
          + _("請在 24 小時內打開下面的連結，輸入剛才設定的密碼，完成 TrailRunCoach 的註冊：") + "\n\n"
          + link + "\n\n"
          + _("如果不是你註冊的，請忽略這封信：沒有那組密碼，這個連結不能用。"))


def _mail_exists(email: str, google_account: bool) -> None:
    if google_account:
        body = _("有人（可能是你）想用這個 email 註冊 TrailRunCoach，但這個 email 已經用 Google 帳號註冊過了。"
                 "請在登入頁按「用 Google 帳號登入」：") + "\n\n" + _url("/login")
    else:
        body = (_("有人（可能是你）想用這個 email 註冊 TrailRunCoach，但這個 email 已經有帳號了。") + "\n\n"
                + _("直接登入：{url}", url=_url("/login")) + "\n"
                + _("忘記密碼：{url}", url=_url("/forgot-password")))
    _mail(email, "exists", _("你已經有 TrailRunCoach 帳號"),
          _hello(None) + "\n\n" + body + "\n\n" + _("如果不是你，請忽略這封信，你的帳號沒有任何變更。"))


def _mail_reset(email: str, token: str) -> None:
    _mail(email, "reset", _("重設 TrailRunCoach 密碼"),
          _hello(None) + "\n\n"
          + _("請在 1 小時內打開下面的連結設定新密碼：") + "\n\n" + _url("/reset-password", None, token) + "\n\n"
          + _("設定新密碼後，所有已登入的裝置都會被登出。如果你沒有要求重設密碼，請忽略這封信，密碼不會改變。"))


def _mail_google_only(email: str) -> None:
    _mail(email, "google", _("TrailRunCoach 登入方式"),
          _hello(None) + "\n\n"
          + _("有人（可能是你）想重設這個 email 的 TrailRunCoach 密碼，但這個帳號是用 Google 帳號登入的，沒有密碼。"
              "請在登入頁按「用 Google 帳號登入」：") + "\n\n" + _url("/login") + "\n\n"
          + _("如果不是你，請忽略這封信，你的帳號沒有任何變更。"))


def _google_owned(email: str) -> bool:
    """The address is an account's that signs in another way (Google, or the owner): not one an
    admin let in from the waitlist and nobody has signed in to yet."""
    u = store.user_by_email(email)
    return u is not None and store.admitted_by_email(email) is None and store.email_account_of(u.id) is None


# ---------------------------------------------------------------------------- the steps

def register(email_raw, password, name_raw, next_url: str = "/") -> None:
    """A sign-up (the module docstring). Refused for a malformed address or a weak password —
    never for anything that would tell whether the address has an account."""
    email = normalize_email(email_raw)
    if email is None:
        raise Refused("EMAIL_INVALID")
    password_problem(password, email)
    name = clean_name(name_raw)
    hashed = passwords.hash_password(password)       # hashed whatever happens next: the same time for all
    row = store.email_account(email)
    if row is not None and row["verified_at"]:
        _mail_exists(email, google_account=False)
        log.info("e-mail sign-up: the address has an account already (told by mail)")
        return
    if _google_owned(email):
        _mail_exists(email, google_account=True)
        log.info("e-mail sign-up: the address is a Google account's (told by mail)")
        return
    token = _secrets.token_urlsafe(32)
    if store.put_email_signup(email, hashed, name, token, VERIFY_TTL_S) != "pending":
        _mail_exists(email, google_account=False)
        return
    _mail_verify(email, name, token, LG.safe_next(next_url))
    log.info("e-mail sign-up: a link to confirm the address was mailed")


def verify(token: str, password, next_url: str = "/") -> LG.Outcome:
    """The address-check link with the password set at sign-up. Refused("LINK_INVALID") for an
    unknown, used or expired link, Refused("PASSWORD_WRONG") for the wrong password (the link stays
    usable until it expires)."""
    token = (token or "").strip()[:TOKEN_MAX]
    row = store.email_verify_row(token)
    if row is None:
        raise Refused("LINK_INVALID")
    if not passwords.verify(password, row["password_hash"]):
        log.info("e-mail confirmation refused: wrong password")
        raise Refused("PASSWORD_WRONG")
    row = store.confirm_email(token, row["email"])
    if row is None:
        raise Refused("LINK_INVALID")
    log.info("an e-mail address was confirmed")
    return resolve(row, LG.safe_next(next_url))


@dataclass(frozen=True)
class LoginResult:
    outcome: Optional[LG.Outcome] = None
    unverified: bool = False


def login(email_raw, password, next_url: str = "/") -> LoginResult:
    """Refused("LOGIN_FAILED") for an unknown address or a wrong password, alike."""
    email = normalize_email(email_raw)
    row = store.email_account(email) if email else None
    ok = passwords.verify(password if isinstance(password, str) else "", row["password_hash"] if row else None)
    if not ok or row is None:
        log.info("e-mail sign-in refused: wrong address or password")
        raise Refused("LOGIN_FAILED")
    if not row["verified_at"]:
        token = _secrets.token_urlsafe(32)
        if store.put_email_signup(email, row["password_hash"], row["display_name"], token, VERIFY_TTL_S) == "pending":
            _mail_verify(email, row["display_name"], token, LG.safe_next(next_url))
        log.info("e-mail sign-in: the address is not confirmed yet (the link mailed again)")
        return LoginResult(unverified=True)
    if passwords.needs_rehash(row["password_hash"]):
        store.set_email_password(email, passwords.hash_password(password))
    return LoginResult(outcome=resolve(row, LG.safe_next(next_url)))


def forgot(email_raw) -> None:
    """A reset link mailed when the address has an e-mail sign-in; a "you sign in with Google" mail
    when it is a Google account's; nothing otherwise. The same answer every time."""
    email = normalize_email(email_raw)
    if email is None:
        raise Refused("EMAIL_INVALID")
    token = _secrets.token_urlsafe(32)
    if store.put_email_reset(email, token, RESET_TTL_S):
        _mail_reset(email, token)
        log.info("password reset: a link was mailed")
    elif _google_owned(email):
        _mail_google_only(email)
        log.info("password reset: the address signs in with Google (told by mail)")
    else:
        log.info("password reset asked for an address without an e-mail sign-in")


def reset(token: str, password) -> None:
    """The new password from a reset link (Refused LINK_INVALID / PASSWORD_*)."""
    token = (token or "").strip()[:TOKEN_MAX]
    row = store.email_reset_row(token)
    if row is None:
        raise Refused("LINK_INVALID")
    password_problem(password, row["email"])
    if store.take_email_reset(token, passwords.hash_password(password)) is None:
        raise Refused("LINK_INVALID")
    log.info("a password was reset (the account's sessions ended)")


def change_password(uid: int, current, new, keep_token: Optional[str]) -> int:
    """A signed-in e-mail account changes its password (SP-858): the current password first
    (Refused PASSWORD_WRONG), the new one under the sign-up's rules (PASSWORD_*). The other
    sessions of the account end, this one stays. Refused NO_EMAIL_SIGN_IN for an account without
    an e-mail sign-in (Google's). The number of other sessions ended."""
    row = store.email_account_of(uid)
    if row is None:
        raise Refused("NO_EMAIL_SIGN_IN")
    if not passwords.verify(current if isinstance(current, str) else "", row["password_hash"]):
        log.info("password change refused: wrong current password")
        raise Refused("PASSWORD_WRONG")
    password_problem(new, row["email"])
    ended = store.change_email_password(uid, passwords.hash_password(new), keep_token)
    if ended < 0:
        raise Refused("NO_EMAIL_SIGN_IN")
    log.info("a password was changed (%d other session(s) ended)", ended)
    return ended


# ---------------------------------------------------------------------------- who a confirmed address is

def resolve(row: dict, next_url: str) -> LG.Outcome:
    """login.resolve's rules for a confirmed e-mail sign-in (the module docstring)."""
    email, name = row["email"], row.get("display_name")
    u = store.get_user(row["user_id"]) if row.get("user_id") else None
    if u is not None:
        if u.disabled:
            log.info("sign-in refused: the account is disabled")
            return LG.Outcome("refused", reason="disabled")
        if u.is_owner or u.is_test or store.consent_version(u.id) == LG.terms.VERSION:
            return LG.Outcome("session", user=u)
        return LG.ticket("reconsent", "email", email, email, name, next_url, user_id=u.id)
    if not LG._owner_linked():
        log.info("sign-up refused: the owner has not signed in yet")
        return LG.Outcome("refused", reason="setup")
    let_in = store.link_admitted_email(email)
    if let_in is not None:
        log.info("a person let in from the waitlist confirmed their e-mail sign-in")
        if let_in.disabled:
            return LG.Outcome("refused", reason="disabled")
        return LG.ticket("reconsent", "email", email, email, name, next_url, user_id=let_in.id)
    if _google_owned(email):
        log.info("e-mail sign-up refused: the address is a Google account's")
        return LG.Outcome("refused", reason="google_account")
    if LG.full():
        return LG._waitlist(email)
    return LG.ticket("signup", "email", email, email, name, next_url)


def accept_terms(ticket: dict, documents: dict) -> LG.Outcome:
    """The terms ticked for a new e-mail sign-up (login.accept_terms hands it over): the account
    made from the confirmed row, the cap counted in the same transaction (full meanwhile: the
    waitlist, no account)."""
    email = ticket["subject"]
    if not LG._owner_linked():
        return LG.Outcome("refused", reason="setup")
    row = store.email_account(email)
    if row is None or not row["verified_at"]:
        return LG.Outcome("refused", reason="expired")
    if row["user_id"] is None and _google_owned(email):
        return LG.Outcome("refused", reason="google_account")
    u = store.create_email_user(email, cap=LG.max_users())
    if u is None:
        return LG._waitlist(email)
    if u.disabled:
        return LG.Outcome("refused", reason="disabled")
    log.info("a new account was made after the terms were accepted (e-mail sign-in)")
    LG._accepted(u.id, documents)
    return LG.Outcome("session", user=u)
