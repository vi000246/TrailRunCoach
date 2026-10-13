"""
Google sign-in (OpenID Connect, authorization code flow with PKCE; SP-58).

    configure(id, secret, public_url)          the client, frozen at start-up (login.decide())
    configured()                               configure() ran
    redirect_uri()                             <public URL>/oauth/google/callback (the registered one)
    authorize_url(state, nonce, verifier, uri) where the browser is sent (scope openid email profile)
    await identity(code, verifier, uri, nonce) -> Identity, after exchanging the code and verifying
                                               the id_token; LoginFailed(reason) otherwise

authlib builds the authorization URL, the PKCE challenge and the token request body; the HTTP
calls are plain httpx (our own client: not authlib's, which picks httpx2 when it happens to be
installed). The code exchange authenticates with client_secret_basic. The id_token is verified
here with joserfc against Google's JWKS: RS256 only, the signature, iss, aud (and azp when aud
lists several), exp, iat, the nonce of this sign-in, a non-empty sub. The JWKS is cached for an
hour and fetched again when a token names an unknown key (Google rotates them), at most once a
minute for that reason.

The state, nonce and PKCE verifier are made and kept by the caller (backend/accounts/login.py,
accounts.db oauth_states): no request.session (itsdangerous is not installed). Nothing here
logs a token, a code or an e-mail; LoginFailed carries a short reason word only.
"""
from __future__ import annotations

import secrets as _secrets
import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPE = "openid email profile"
CALLBACK_PATH = "/oauth/google/callback"
ENV_PUBLIC_URL = "WKO5COACH_PUBLIC_URL"
LEEWAY_S = 60                  # clock skew allowed on exp / iat
JWKS_TTL_S = 3600
JWKS_REFETCH_S = 60            # an unknown key id refetches the JWKS at most this often
TIMEOUT_S = 10.0

TRANSPORT: Optional[httpx.AsyncBaseTransport] = None   # tests: httpx.MockTransport (a fake Google)
clock = time.monotonic                                  # tests replace


class LoginFailed(Exception):
    """The sign-in can't be trusted or didn't complete; `reason` is a short word for the log."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str                 # Google's `sub`: the account key (stable; the e-mail is not)
    email: Optional[str]
    email_verified: bool
    name: Optional[str]


# the client, set once at start-up by login.decide(): a request never reads the environment
_CONF: dict = {"id": None, "secret": None, "public": None}


def configure(cid: Optional[str], secret: Optional[str], public_url: Optional[str]) -> None:
    _CONF.update(id=cid, secret=secret, public=(public_url or "").rstrip("/") or None)


def client_id() -> Optional[str]:
    return _CONF["id"]


def client_secret() -> Optional[str]:
    return _CONF["secret"]


def configured() -> bool:
    return bool(_CONF["id"] and _CONF["secret"] and _CONF["public"])


def redirect_uri() -> str:
    """The registered redirect: the fixed public address (behind Cloudflare the app itself sees
    http:// and an internal host, so the request's own origin is never used)."""
    return (_CONF["public"] or "") + CALLBACK_PATH


def _http() -> httpx.AsyncClient:
    kw = {"timeout": TIMEOUT_S, "trust_env": False, "follow_redirects": False}
    if TRANSPORT is not None:
        kw["transport"] = TRANSPORT
    return httpx.AsyncClient(**kw)


def authorize_url(state: str, nonce: str, code_verifier: str, redirect: str,
                  login_hint: Optional[str] = None) -> str:
    """Google's authorization URL for this sign-in (state, nonce, the S256 code challenge). Built
    with authlib's own helpers, the same ones its client uses, without opening an HTTP client.
    `login_hint`: the account to suggest (a signed-in user confirming who they are, SP-386)."""
    from authlib.oauth2.rfc6749.parameters import prepare_grant_uri
    from authlib.oauth2.rfc7636 import create_s256_code_challenge
    extra = {"login_hint": login_hint} if login_hint else {}
    return prepare_grant_uri(AUTHORIZE_URL, client_id=client_id(), response_type="code", redirect_uri=redirect,
                             scope=SCOPE, state=state, code_challenge=create_s256_code_challenge(code_verifier),
                             code_challenge_method="S256", nonce=nonce, prompt="select_account", **extra)


# ---------------------------------------------------------------------------- the JWKS

_JWKS: dict = {"set": None, "at": 0.0, "refetched_at": None}
_JWKS_LOCK = threading.Lock()


def forget_keys() -> None:
    with _JWKS_LOCK:
        _JWKS.update(set=None, at=0.0, refetched_at=None)


async def _fetch_jwks():
    from joserfc.jwk import KeySet
    try:
        async with _http() as c:
            r = await c.get(JWKS_URL)
    except Exception as e:         # noqa: BLE001 — Google unreachable: the sign-in fails, nothing else
        raise LoginFailed("jwks_" + type(e).__name__) from None
    if r.status_code != 200:
        raise LoginFailed("jwks_unavailable")
    try:
        return KeySet.import_key_set(r.json())
    except Exception:              # noqa: BLE001 — a malformed key set is a failed sign-in
        raise LoginFailed("jwks_invalid") from None


async def _keys(refetch: bool = False):
    now = clock()
    with _JWKS_LOCK:
        ks, at, re_at = _JWKS["set"], _JWKS["at"], _JWKS["refetched_at"]
    if ks is not None and not refetch and now - at < JWKS_TTL_S:
        return ks
    if refetch and ks is not None and re_at is not None and now - re_at < JWKS_REFETCH_S:
        return ks
    ks = await _fetch_jwks()
    with _JWKS_LOCK:
        _JWKS.update(set=ks, at=now)
        if refetch:
            _JWKS["refetched_at"] = now
    return ks


# ---------------------------------------------------------------------------- the id_token

def _check_claims(c: dict, nonce: str, now: int) -> dict:
    cid = client_id()
    if c.get("iss") not in ISSUERS:
        raise LoginFailed("iss")
    aud = c.get("aud")
    if isinstance(aud, list):
        if cid not in aud or (len(aud) > 1 and c.get("azp") != cid):
            raise LoginFailed("aud")
    elif aud != cid:
        raise LoginFailed("aud")
    if "azp" in c and c.get("azp") != cid:
        raise LoginFailed("azp")
    exp, iat = c.get("exp"), c.get("iat")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp + LEEWAY_S <= now:
        raise LoginFailed("exp")
    if not isinstance(iat, (int, float)) or isinstance(iat, bool) or iat > now + LEEWAY_S:
        raise LoginFailed("iat")
    got = c.get("nonce")
    if not nonce or not isinstance(got, str) or not _secrets.compare_digest(got.encode(), nonce.encode()):
        raise LoginFailed("nonce")
    sub = c.get("sub")
    if not isinstance(sub, str) or not sub.strip():
        raise LoginFailed("sub")
    return c


async def verify_id_token(id_token: str, nonce: str, now: Optional[int] = None) -> dict:
    """The id_token's claims once its signature (Google's JWKS, RS256) and claims check out."""
    from joserfc import jwt
    from joserfc.errors import InvalidKeyIdError, JoseError
    if not isinstance(id_token, str) or not id_token:
        raise LoginFailed("no_id_token")
    ks = await _keys()
    try:
        try:
            tok = jwt.decode(id_token, ks, algorithms=["RS256"])
        except InvalidKeyIdError:                       # a key Google added since: fetch the set again
            tok = jwt.decode(id_token, await _keys(refetch=True), algorithms=["RS256"])
    except JoseError:
        raise LoginFailed("signature") from None
    except (ValueError, TypeError):
        raise LoginFailed("malformed") from None
    claims = tok.claims if isinstance(tok.claims, dict) else {}
    return _check_claims(claims, nonce, int(time.time()) if now is None else now)


async def identity(code: str, code_verifier: str, redirect: str, nonce: str) -> Identity:
    """Exchange the code (with this sign-in's PKCE verifier) and verify the id_token."""
    from authlib.oauth2.rfc6749.parameters import prepare_token_request
    if not code or not configured():
        raise LoginFailed("no_code" if not code else "not_configured")
    body = prepare_token_request("authorization_code", code=code, redirect_uri=redirect, code_verifier=code_verifier)
    try:
        async with _http() as c:
            r = await c.post(TOKEN_URL, content=body, auth=(client_id(), client_secret()),
                             headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        token = r.json()
    except Exception as e:                               # noqa: BLE001 — network, not JSON
        raise LoginFailed("token_" + type(e).__name__) from None
    if r.status_code != 200 or not isinstance(token, dict) or "error" in token:
        err = token.get("error") if isinstance(token, dict) else None
        raise LoginFailed("token_" + (err if isinstance(err, str) and err.isidentifier() else str(r.status_code)))
    claims = await verify_id_token(token.get("id_token"), nonce)
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    name = claims.get("name") if isinstance(claims.get("name"), str) else None
    verified = claims.get("email_verified")
    return Identity(provider="google", subject=claims["sub"], email=email,
                    email_verified=verified is True or verified == "true", name=name)
