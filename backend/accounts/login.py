"""
Signing in (SP-58; auth-and-demo plan §4, §7): when the app asks, who becomes whom, and the
pieces the sign-in gate (backend/tenancy_mw.py) and the routes (backend/api/account.py) share.

The switch — the owner must never be locked out by a deploy:
    required()   multi-user mode (WKO5COACH_ACCOUNTS=1, never in the demo instance) AND a whole
                 Google client (GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET). Anything less: every
                 request is the owner, exactly as before SP-58 (no 401, no CSRF on writes, the
                 sign-in pages answer 404).
    check_config()  at start-up when required(): which Google account is the owner's must be
                 known (user 1 already linked, or WKO5COACH_OWNER_GOOGLE_SUB / _OWNER_EMAIL),
                 else LoginConfigError stops the app with the reason.

Who becomes whom (resolve(), after Google's id_token checked out and its e-mail is verified):
  1. a known Google account (identities: provider + sub)  -> that user
       disabled                                -> refused ("disabled")
       the owner                               -> signed in (the owner accepts no terms)
       hasn't accepted terms.VERSION           -> the terms again (reconsent ticket)
  2. unknown, and it is the owner's (WKO5COACH_OWNER_GOOGLE_SUB equal; else, without one,
     WKO5COACH_OWNER_EMAIL equal and verified) while user 1 has no Google account yet
       -> linked to user 1 (one conditional UPDATE: store.claim_owner_google) -> signed in.
     Once user 1 is linked, only that sub is the owner; nobody can take user 1 later.
  3. unknown: a new person
       the server is full (users >= WKO5COACH_MAX_USERS, default 100; the owner and disabled
       users count) -> the e-mail goes on the waitlist, no account, no tenant -> refused ("full")
       else -> the terms (signup ticket); the account and its tenant are made only on consent
  An e-mail Google has not verified is refused before any of this ("unverified"): the e-mail is
  never trusted for the owner match, the waitlist or contact.

Sessions: store.create_session (sha256 of the cookie only, 30 days, sliding); a new token at
every sign-in (the browser's previous one is deleted). Limits: 10 sign-in starts and 10
callbacks per IP in any 15 minutes (backend/security/ratelimit.Window).
"""
from __future__ import annotations

import logging
import os
import secrets as _secrets
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from backend import accounts
from backend.accounts import google, store, terms
from backend.security import ratelimit as RL

log = logging.getLogger(__name__)

ENV_OWNER_SUB = "WKO5COACH_OWNER_GOOGLE_SUB"
ENV_OWNER_EMAIL = "WKO5COACH_OWNER_EMAIL"
ENV_MAX_USERS = "WKO5COACH_MAX_USERS"
ENV_PUBLIC_URL = google.ENV_PUBLIC_URL
DEFAULT_MAX_USERS = 100            # SP-58: until SP-351's site settings

SESSION_COOKIE = "trc_session"
OAUTH_COOKIE = "trc_oauth"         # binds a sign-in's state to the browser that started it
SIGNUP_COOKIE = "trc_signup"       # the sign-up ticket between Google and the terms
SESSION_MAX_AGE = store.SESSION_DAYS * 86400
STATE_TTL_S = 10 * 60
TICKET_TTL_S = 30 * 60

START_LIMIT = RL.Window(limit=10, per=15 * 60)        # sign-in starts per IP
CALLBACK_LIMIT = RL.Window(limit=10, per=15 * 60)     # callbacks per IP

# reachable without a session (deploy/commercial.md §4; auth-and-demo plan §7). /share/* is
# public too but read as before (the owner's shares), outside the gate entirely.
PUBLIC_PATHS = frozenset({
    "/login", "/signup", "/api/v1/session", "/healthz",
    "/api/v1/account/login", "/api/v1/account/signup", "/api/v1/account/logout",
})
PUBLIC_PREFIXES = ("/api/v1/static/",)
_OAUTH_CALLBACK = ("/oauth/", "/callback")
# never a place to come back to after signing in (loops)
_NO_RETURN = ("/login", "/signup", "/oauth/", "/api/v1/account/")
NEXT_MAX = 1000


class LoginConfigError(RuntimeError):
    """Sign-in is on but the owner's Google account can't be told apart (start-up check)."""


_USER: ContextVar[Optional[store.User]] = ContextVar("trc_login_user", default=None)


def required() -> bool:
    """Sign-in asked for every request outside the public paths (see the module docstring)."""
    return accounts.enabled() and google.configured()


def current_user() -> Optional[store.User]:
    """The signed-in user of this request (set by the gate), None when signed out / sign-in off."""
    return _USER.get()


def set_user(u: Optional[store.User]):
    return _USER.set(u)


def reset_user(tok) -> None:
    _USER.reset(tok)


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return True
    if path.startswith(_OAUTH_CALLBACK[0]) and path.endswith(_OAUTH_CALLBACK[1]) and path.count("/") == 3:
        return True                    # /oauth/<provider>/callback
    return False


def safe_next(raw: Optional[str]) -> str:
    """Where to go after signing in: a path on this site only (no scheme, no host, no //, no
    backslash or control character, not the sign-in pages themselves); "/" otherwise."""
    if not raw or not isinstance(raw, str) or len(raw) > NEXT_MAX:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "\\" for ch in raw):
        return "/"
    if not raw.startswith("/") or raw.startswith("//"):
        return "/"
    try:
        u = urlsplit(raw)
    except ValueError:
        return "/"
    if u.scheme or u.netloc:
        return "/"
    if any(u.path == p.rstrip("/") or u.path.startswith(p) for p in _NO_RETURN):
        return "/"
    return raw


def max_users() -> int:
    try:
        n = int((os.environ.get(ENV_MAX_USERS) or "").strip() or DEFAULT_MAX_USERS)
    except ValueError:
        n = DEFAULT_MAX_USERS
    return max(1, n)


def _owner_env() -> tuple[Optional[str], Optional[str]]:
    sub = (os.environ.get(ENV_OWNER_SUB) or "").strip() or None
    email = (os.environ.get(ENV_OWNER_EMAIL) or "").strip().lower() or None
    return sub, email


def check_config() -> None:
    """Start-up (main.lifespan) when required(): the owner's Google account must be known."""
    store.migrate_owner()
    o = store.get_user(1)
    if o is not None and o.google_sub:
        return
    sub, email = _owner_env()
    if not (sub or email):
        raise LoginConfigError(
            f"Google sign-in is on (WKO5COACH_ACCOUNTS=1 and a Google client) but the owner's Google account "
            f"is unknown: set {ENV_OWNER_SUB} or {ENV_OWNER_EMAIL} so the owner signs in to user 1 "
            f"(docs/deploy/accounts.md)")



def _is_owner_identity(ident: google.Identity) -> bool:
    sub, email = _owner_env()
    if sub:
        return _secrets.compare_digest(ident.subject.encode(), sub.encode())
    if email and ident.email_verified and ident.email:
        return _secrets.compare_digest(ident.email.strip().lower().encode(), email.encode())
    return False


def full() -> bool:
    return store.count_users() >= max_users()


# ---------------------------------------------------------------------------- outcomes

@dataclass(frozen=True)
class Outcome:
    kind: str                          # "session" | "terms" | "refused"
    user: Optional[store.User] = None
    reason: Optional[str] = None       # refused: disabled | full | unverified
    ticket: Optional[str] = None       # terms: the sign-up cookie's value


def _terms(kind: str, ident: google.Identity, next_url: str, user_id: Optional[int] = None) -> Outcome:
    token = _secrets.token_urlsafe(32)
    store.put_signup_ticket(token, kind, ident.provider, ident.subject, ident.email, ident.name, user_id,
                            next_url, TICKET_TTL_S)
    return Outcome("terms", ticket=token)


def _waitlist(email: Optional[str]) -> Outcome:
    if email:
        store.add_waitlist(email)
    log.info("sign-up refused: the server is full (waitlisted)")
    return Outcome("refused", reason="full")


def resolve(ident: google.Identity, next_url: str) -> Outcome:
    """Who this verified Google account is (the module docstring's rules)."""
    if not ident.email_verified:
        return Outcome("refused", reason="unverified")
    u = store.find_identity(ident.provider, ident.subject)
    if u is None and _is_owner_identity(ident):
        store.migrate_owner()
        if store.claim_owner_google(ident.subject, ident.email):
            log.info("the owner's Google account is now linked to user 1")
            u = store.get_user(1)
        else:
            log.warning("the owner identity in the environment matches, but user 1 is linked to another Google "
                        "account already: signing in as a new user")
    if u is not None:
        if u.disabled:
            log.info("sign-in refused: the account is disabled")
            return Outcome("refused", reason="disabled")
        if u.is_owner or store.consent_version(u.id) == terms.VERSION:
            return Outcome("session", user=u)
        return _terms("reconsent", ident, next_url, user_id=u.id)
    if full():
        return _waitlist(ident.email)
    return _terms("signup", ident, next_url)


def accept_terms(ticket: dict) -> Outcome:
    """The terms ticked on the sign-up page (the ticket already taken, the version checked):
    the account (a new person) and the consent; the cap is checked again — someone may have
    taken the last place meanwhile."""
    if ticket["kind"] == "reconsent":
        u = store.get_user(ticket["user_id"]) if ticket.get("user_id") else None
    else:
        u = store.find_identity(ticket["provider"], ticket["subject"])
        if u is None:
            if full():
                return _waitlist(ticket.get("email"))
            try:
                u = store.create_user(email=ticket.get("email"), google_sub=ticket["subject"],
                                      display_name=ticket.get("display_name"), email_verified=True)
            except Exception:              # noqa: BLE001 — the same account signed up in another tab
                u = store.find_identity(ticket["provider"], ticket["subject"])
            if u is not None:
                log.info("a new account was made after the terms were accepted")
    if u is None:
        return Outcome("refused", reason="expired")
    if u.disabled:
        return Outcome("refused", reason="disabled")
    store.record_consent(u.id, terms.VERSION, terms.digests(), analysis=True)
    return Outcome("session", user=u)


def reset_limits() -> None:
    START_LIMIT.reset()
    CALLBACK_LIMIT.reset()
