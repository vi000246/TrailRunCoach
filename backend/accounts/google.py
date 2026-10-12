"""
Google sign-in (OpenID Connect, authorization code flow with PKCE; SP-58).

    configured()                               GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET (env or *_FILE)
    redirect_uri(base)                         $WKO5COACH_PUBLIC_URL/oauth/google/callback (else the
                                               request's own origin: http://localhost:8000 in development)
    authorize_url(state, nonce, verifier, uri) where the browser is sent (scope openid email profile)
    await identity(code, verifier, uri, nonce) -> Identity, after exchanging the code and verifying
                                               the id_token; LoginFailed(reason) otherwise

The client is authlib's httpx one (AsyncOAuth2Client: the code exchange with the PKCE verifier
and client_secret_basic; the authorization URL from the same authlib helpers it uses). The id_token is verified here with joserfc against Google's JWKS:
RS256 only, the signature, iss, aud (and azp when aud lists several), exp, iat, the nonce of
this sign-in, a non-empty sub. The JWKS is cached for an hour and fetched again when a token
names an unknown key (Google rotates them), at most once a minute for that reason.

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

from authlib.integrations.httpx_client._compat import httpx2 as HTTP

from backend.settings.env_secrets import secret_env

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

# authlib's HTTP library: httpx2 when installed (authlib 1.8 prefers it), else httpx. The JWKS fetch
# uses the same one, so one transport (tests: HTTP.MockTransport, a fake Google) serves both
TRANSPORT = None
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


def client_id() -> Optional[str]:
    return secret_env("GOOGLE_CLIENT_ID")


def client_secret() -> Optional[str]:
    return secret_env("GOOGLE_CLIENT_SECRET")


def configured() -> bool:
    return bool(client_id() and client_secret())


def redirect_uri(request_base: str) -> str:
    """The fixed public address when set (behind Cloudflare the app sees http://, so the
    request's own origin would not be the registered https:// URI), else the request's origin."""
    import os
    base = (os.environ.get(ENV_PUBLIC_URL) or "").strip() or request_base
    return base.rstrip("/") + CALLBACK_PATH


def _client(redirect: Optional[str] = None):
    from authlib.integrations.httpx_client import AsyncOAuth2Client
    kw = {"timeout": TIMEOUT_S, "trust_env": False}
    if TRANSPORT is not None:
        kw["transport"] = TRANSPORT
    return AsyncOAuth2Client(client_id=client_id(), client_secret=client_secret(), scope=SCOPE,
                             redirect_uri=redirect, code_challenge_method="S256", **kw)


def authorize_url(state: str, nonce: str, code_verifier: str, redirect: str) -> str:
    """Google's authorization URL for this sign-in (state, nonce, the S256 code challenge). Built
    with authlib's own helpers, the same ones its client uses, without opening an HTTP client."""
    from authlib.oauth2.rfc6749.parameters import prepare_grant_uri
    from authlib.oauth2.rfc7636 import create_s256_code_challenge
    return prepare_grant_uri(AUTHORIZE_URL, client_id=client_id(), response_type="code", redirect_uri=redirect,
                             scope=SCOPE, state=state, code_challenge=create_s256_code_challenge(code_verifier),
                             code_challenge_method="S256", nonce=nonce, prompt="select_account")


# ---------------------------------------------------------------------------- the JWKS

_JWKS: dict = {"set": None, "at": 0.0, "refetched_at": None}
_JWKS_LOCK = threading.Lock()


def forget_keys() -> None:
    with _JWKS_LOCK:
        _JWKS.update(set=None, at=0.0, refetched_at=None)


async def _fetch_jwks():
    from joserfc.jwk import KeySet
    kw = {"timeout": TIMEOUT_S, "trust_env": False}
    if TRANSPORT is not None:
        kw["transport"] = TRANSPORT
    try:
        async with HTTP.AsyncClient(**kw) as c:
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
    if not code:
        raise LoginFailed("no_code")
    try:
        async with _client(redirect) as c:
            token = await c.fetch_token(TOKEN_URL, grant_type="authorization_code", code=code,
                                        code_verifier=code_verifier, redirect_uri=redirect)
    except LoginFailed:
        raise
    except Exception as e:                               # noqa: BLE001 — refused code, network, bad JSON
        raise LoginFailed("token_" + type(e).__name__) from None
    claims = await verify_id_token(token.get("id_token") if isinstance(token, dict) else None, nonce)
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    name = claims.get("name") if isinstance(claims.get("name"), str) else None
    verified = claims.get("email_verified")
    return Identity(provider="google", subject=claims["sub"], email=email,
                    email_verified=verified is True or verified == "true", name=name)
