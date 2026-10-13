"""
Signing in (SP-58; auth-and-demo plan §4, §7): when the app asks, who becomes whom, and the
pieces the sign-in gate (backend/tenancy_mw.py) and the routes (backend/api/account.py) share.

The mode — decided ONCE at start-up (decide(), main.lifespan) and frozen for the life of the
process: no request reads the environment again, so nothing that changes or fails later (an
unreadable secret file, an edited env) can turn one request into the owner's app.
    owner    no sign-in setting at all (WKO5COACH_ACCOUNTS off, or on with nothing else: SP-384's
             multi-user data without sign-in yet): every request is the owner, exactly as before
             SP-58 (no 401, no CSRF on writes, the sign-in pages answer 404)
    signin   WKO5COACH_ACCOUNTS=1 and the whole set: GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET
             (env or *_FILE), WKO5COACH_PUBLIC_URL (https; http only for localhost), with https
             an explicit WKO5COACH_TRUSTED_PROXIES (the client IP the limits count), and the
             owner's Google account (user 1 linked already, or WKO5COACH_OWNER_GOOGLE_SUB /
             _OWNER_EMAIL)
    demo     the demo instance: never asks
  Anything in between — a sign-in setting present but the set incomplete, a secret file that
  can't be read, WKO5COACH_ACCOUNTS neither on nor off — raises LoginConfigError: the app does
  not start, the message names what is missing. /healthz reports the mode.

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
       user 1 not linked yet -> refused ("setup": 網站還在設定中) — a typo in the owner's
       e-mail never turns the owner into a new, empty account
       the server is full (users >= WKO5COACH_MAX_USERS, default 100; the owner and disabled
       users count) -> the e-mail goes on the waitlist, no account, no tenant -> refused ("full")
       else -> the terms (signup ticket); the account and its tenant are made only on consent
  An e-mail Google has not verified is refused before any of this ("unverified"): the e-mail is
  never trusted for the owner match, the waitlist or contact.

New terms (terms.VERSION) stop a signed-in user too: the gate answers 403 TERMS_REQUIRED / sends
the pages to /signup until accepted (consent_current(), cached a few minutes per user).

Sessions: store.create_session (sha256 of the cookie only, 30 days sliding, 90 days at most); a
new token at every sign-in (the browser's previous one is deleted). Cookies: __Host-trc_session
/ _oauth / _signup / _csrf over https (Secure, Path=/, no Domain; SameSite=Lax); the plain names
only for http://localhost development. Limits: 10 sign-in starts per client in any 15 minutes,
and 10 callbacks that got past the state check (forged callbacks use no one's quota); an IPv6
client counts by its /64.
"""
from __future__ import annotations

import logging
import os
import secrets as _secrets
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
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
ENV_PROXIES = "WKO5COACH_TRUSTED_PROXIES"
DEFAULT_MAX_USERS = 100            # SP-58: until SP-351's site settings
# the settings that only mean something with sign-in: any of them set asks for the whole set
SIGNIN_ENV = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_ID_FILE", "GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET_FILE",
              ENV_OWNER_SUB, ENV_OWNER_EMAIL, ENV_PUBLIC_URL)
_ON = frozenset({"1", "true", "yes", "on"})
_OFF = frozenset({"", "0", "false", "no", "off"})
SIGNIN, OWNER_MODE, DEMO_MODE = "signin", "owner", "demo"

SESSION_COOKIE = "trc_session"
OAUTH_COOKIE = "trc_oauth"         # binds a sign-in's state to the browser that started it
SIGNUP_COOKIE = "trc_signup"       # the sign-up ticket between Google and the terms
SESSION_MAX_AGE = store.SESSION_DAYS * 86400
STATE_TTL_S = 10 * 60
TICKET_TTL_S = 30 * 60
CONSENT_TTL_S = 300                # a user's accepted terms version, cached this long

START_LIMIT = RL.Window(limit=10, per=15 * 60)        # sign-in starts per client
CALLBACK_LIMIT = RL.Window(limit=10, per=15 * 60)     # callbacks past the state check, per client

# reachable without a session (deploy/commercial.md §4; auth-and-demo plan §7). /share/* is
# public too but read as before (the owner's shares), outside the gate entirely.
# The legal documents (SP-60) too, each by its exact path: /legal and /legal/<id> of terms.DOCUMENTS
# (the privacy policy's address goes on Google's OAuth consent screen, SP-387) — nothing next to them.
PUBLIC_PATHS = frozenset({
    "/login", "/signup", "/api/v1/session", "/healthz",
    "/api/v1/account/login", "/api/v1/account/signup", "/api/v1/account/logout",
    "/legal",
    # the installable app (SP-314, api/pwa.py): no one's data in either; the browser fetches the
    # manifest without cookies, and the worker before anyone signs in
    "/sw.js", "/manifest.webmanifest",
} | {terms.URL_PREFIX + d.id for d in terms.DOCUMENTS})
PUBLIC_PREFIXES = ("/api/v1/static/",)
_OAUTH_CALLBACK = ("/oauth/", "/callback")
# never a place to come back to after signing in (loops)
_NO_RETURN = ("/login", "/signup", "/oauth/", "/api/v1/account/")
NEXT_MAX = 1000


class LoginConfigError(RuntimeError):
    """The sign-in settings are incomplete or unreadable (start-up refuses, with the reason)."""


# ---------------------------------------------------------------------------- the mode (frozen)

_MODE: Optional[str] = None
_SECURE = True                     # sign-in cookies Secure (and __Host-)
_OWNER: tuple = (None, None)       # (sub, e-mail) of the owner's Google account, from the env
_MODE_LOCK = threading.Lock()


def mode() -> Optional[str]:
    """signin | owner | demo once decided; None before start-up."""
    return _MODE


def required() -> bool:
    """Sign-in asked for every request outside the public paths (the frozen mode)."""
    return _MODE == SIGNIN


def reset_mode() -> None:
    """Tests only: forget the decision (each test starts its own app)."""
    global _MODE, _SECURE, _OWNER
    with _MODE_LOCK:
        _MODE, _SECURE, _OWNER = None, True, (None, None)
        google.configure(None, None, None)
        _CONSENT.clear()


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _secret(name: str) -> str:
    """NAME, else the file NAME_FILE names; missing, empty or unreadable: LoginConfigError."""
    v = _env(name)
    if v:
        return v
    f = _env(name + "_FILE")
    if f:
        try:
            v = Path(f).read_text("utf-8").strip()
        except OSError:
            raise LoginConfigError(f"{name}_FILE is set but can't be read") from None
        if not v:
            raise LoginConfigError(f"{name}_FILE is empty")
        return v
    raise LoginConfigError(f"{name} (or {name}_FILE) is not set")


def _public_url() -> str:
    raw = _env(ENV_PUBLIC_URL)
    if not raw:
        raise LoginConfigError(f"{ENV_PUBLIC_URL} is not set (the address the OAuth redirect comes back to)")
    try:
        u = urlsplit(raw)
    except ValueError:
        raise LoginConfigError(f"{ENV_PUBLIC_URL} is not a URL") from None
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme == "https" or (u.scheme == "http" and local):
        if u.hostname and u.path in ("", "/") and not u.query and not u.fragment and not u.username:
            return raw.rstrip("/")
        raise LoginConfigError(f"{ENV_PUBLIC_URL} must be just the origin (https://host[:port]), no path or query")
    raise LoginConfigError(f"{ENV_PUBLIC_URL} must be https (http only for localhost development)")


def _decide() -> tuple:
    from backend import tenancy
    if tenancy.demo_mode():
        return DEMO_MODE, True, (None, None)
    raw = _env(accounts.ENV_ENABLED).lower()
    if raw not in _ON | _OFF:
        raise LoginConfigError(f"{accounts.ENV_ENABLED} must be 1 (on) or 0 / empty (off)")
    present = [n for n in SIGNIN_ENV if _env(n)]
    if raw in _OFF:
        if present:
            raise LoginConfigError(f"sign-in settings are set ({', '.join(present)}) but {accounts.ENV_ENABLED} "
                                   f"is off: set {accounts.ENV_ENABLED}=1, or remove them")
        return OWNER_MODE, True, (None, None)
    if not present:
        return OWNER_MODE, True, (None, None)        # multi-user data only (SP-384): no sign-in yet
    cid, secret, public = _secret("GOOGLE_CLIENT_ID"), _secret("GOOGLE_CLIENT_SECRET"), _public_url()
    https = public.startswith("https://")
    if https:
        nets = {str(n) for n in RL.trusted_networks()}
        default = {str(n) for n in RL.trusted_networks_of(RL.DEFAULT_TRUSTED)}
        if not _env(ENV_PROXIES) or not nets or nets == default:
            raise LoginConfigError(f"{ENV_PROXIES} must name the proxy in front of the app (the cloudflared "
                                   f"container's address, /32): the sign-in limits count the client IP it reports")
    sub, email = _env(ENV_OWNER_SUB) or None, _env(ENV_OWNER_EMAIL).lower() or None
    store.migrate_owner()
    o = store.get_user(1)
    if not (o is not None and o.google_sub):
        if not (sub or email):
            raise LoginConfigError(f"the owner's Google account is unknown: set {ENV_OWNER_SUB} or "
                                   f"{ENV_OWNER_EMAIL} so the owner signs in to user 1 (docs/deploy/accounts.md)")
        if email and not sub:
            log.warning("the owner's Google account is named by %s only: prefer %s (Google's account id; an "
                        "e-mail address can change hands) until the owner has signed in once",
                        ENV_OWNER_EMAIL, ENV_OWNER_SUB)
    google.configure(cid, secret, public)
    secure = https or os.environ.get("WKO5COACH_COOKIE_SECURE", "1") != "0"
    return SIGNIN, secure, (sub, email)



def decide() -> str:
    """Decide the mode once (main.lifespan; the gate on its first request if the lifespan never
    ran) and freeze it. LoginConfigError for a half-done configuration (nothing is frozen then)."""
    global _MODE, _SECURE, _OWNER
    with _MODE_LOCK:
        if _MODE is None:
            _MODE, _SECURE, _OWNER = _decide()
        return _MODE


# ---------------------------------------------------------------------------- cookies

def cookie_name(base: str) -> str:
    """The sign-in cookies' names: __Host-<base> when Secure (always over https)."""
    return f"__Host-{base}" if _SECURE else base


def cookie_attrs(max_age: Optional[int], http_only: bool = True) -> str:
    from backend.tenancy_mw import cookie_attrs as attrs
    return attrs(max_age, http_only=http_only, same_site="Lax", secure=_SECURE)


# ---------------------------------------------------------------------------- the request's user

_USER: ContextVar[Optional[store.User]] = ContextVar("trc_login_user", default=None)


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
        n = int(_env(ENV_MAX_USERS) or DEFAULT_MAX_USERS)
    except ValueError:
        n = DEFAULT_MAX_USERS
    return max(1, n)


def full() -> bool:
    return store.count_users() >= max_users()


# ---------------------------------------------------------------------------- the terms a user accepted

_CONSENT: dict = {}                # user id -> (accepted version, monotonic time read)


def consent_current(u: store.User) -> bool:
    """The user has accepted terms.VERSION (the owner accepts none). Cached CONSENT_TTL_S."""
    if u.is_owner:
        return True
    now = time.monotonic()
    hit = _CONSENT.get(u.id)
    if hit is None or now - hit[1] > CONSENT_TTL_S:
        hit = (store.consent_version(u.id), now)
        if len(_CONSENT) > 20_000:
            _CONSENT.clear()
        _CONSENT[u.id] = hit
    return hit[0] == terms.VERSION


def _accepted(uid: int, documents: dict) -> None:
    store.record_consent(uid, terms.VERSION, documents, analysis=True)
    _CONSENT[uid] = (terms.VERSION, time.monotonic())


# ---------------------------------------------------------------------------- outcomes

@dataclass(frozen=True)
class Outcome:
    kind: str                          # "session" | "terms" | "refused"
    user: Optional[store.User] = None
    reason: Optional[str] = None       # refused: disabled | full | unverified | setup | expired
    ticket: Optional[str] = None       # terms: the sign-up cookie's value


def _is_owner_identity(ident: google.Identity) -> bool:
    sub, email = _OWNER
    if sub:
        return _secrets.compare_digest(ident.subject.encode(), sub.encode())
    if email and ident.email_verified and ident.email:
        return _secrets.compare_digest(ident.email.strip().lower().encode(), email.encode())
    return False


def _owner_linked() -> bool:
    o = store.get_user(1)
    return o is not None and bool(o.google_sub)


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
    if not _owner_linked():
        log.info("sign-up refused: the owner has not signed in yet")
        return Outcome("refused", reason="setup")
    if full():
        return _waitlist(ident.email)
    return _terms("signup", ident, next_url)


def accept_terms(ticket: dict, documents: dict) -> Outcome:
    """The terms ticked on the sign-up page (the ticket already taken; `documents` the digests of
    the texts the page showed): the account (a new person) and the consent. The cap is checked
    again, in the same transaction as the insert — someone may have taken the last place."""
    if ticket["kind"] == "reconsent":
        u = store.get_user(ticket["user_id"]) if ticket.get("user_id") else None
    else:
        u = store.find_identity(ticket["provider"], ticket["subject"])
        if u is None:
            if not _owner_linked():
                return Outcome("refused", reason="setup")
            try:
                u = store.create_user(email=ticket.get("email"), google_sub=ticket["subject"],
                                      display_name=ticket.get("display_name"), email_verified=True,
                                      cap=max_users())
            except Exception:              # noqa: BLE001 — the same account signed up in another tab
                u = store.find_identity(ticket["provider"], ticket["subject"])
            if u is None and store.find_identity(ticket["provider"], ticket["subject"]) is None:
                return _waitlist(ticket.get("email"))
            if u is not None:
                log.info("a new account was made after the terms were accepted")
    if u is None:
        return Outcome("refused", reason="expired")
    if u.disabled:
        return Outcome("refused", reason="disabled")
    _accepted(u.id, documents)
    return Outcome("session", user=u)


def accept_terms_signed_in(u: store.User, documents: dict) -> Outcome:
    """New terms accepted by a user who is signed in already (the gate sent them to /signup)."""
    if u.disabled:
        return Outcome("refused", reason="disabled")
    _accepted(u.id, documents)
    return Outcome("session", user=u)


def reset_limits() -> None:
    START_LIMIT.reset()
    CALLBACK_LIMIT.reset()
