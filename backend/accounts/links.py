"""
Linking an account here with another project of ours (SP-329): 疊石 Cairn (inReachLiveTrack).

Both sign in with Google; the same Google account (its `sub`, users.google_sub / identities) is
the same person on both sides. Nothing is linked on its own (SP-571 Q4 B): the user links in
設定 › 帳號 › 已連結的服務 — or is sent there by Cairn the first time it needs to read or push
something — and can unlink there at any time. Each link / unlink is a consents row (action
"link" / "unlink", client "cairn", the scopes granted), so the history is in 下載我的資料.

Service to service, two directions, one shared secret per project (TRC_CAIRN_LINK_SECRET, from
the environment only, never stored in accounts.db or a tenant folder):

  Cairn → here   POST /api/v1/link/cairn/token   {sub}   Authorization: Bearer <shared secret>
                 a linked user's service token (`trc_…`, 30 days, one live per user: a new one
                 replaces the old), carrying LINK_SCOPES_GRANTED; 404 NOT_LINKED for a sub that
                 is not a linked user here (whether the account exists is never said), with the
                 page where the user can link. Then
                 GET /api/v1/link/account   Authorization: Bearer <service token>
                 (api/link.py; the login gate opens only the routes in ROUTES with such a token)
  here → Cairn   cairn_status(sub): GET <TRC_CAIRN_URL>/api/internal/trc/account?sub=…
                 with the same shared secret — Cairn's side of the contract (to be built there).
                 Only for a linked user; nothing kept (no Cairn token is ever stored here)

No call to Cairn is made unless TRC_CAIRN_URL and the secret are set; tests use a fake (TRANSPORT).
"""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from backend.i18n import N_, _
from backend.security import ratelimit as RL

log = logging.getLogger("backend.links")

PREFIX = "/api/v1/link/"
TOKEN_DAYS = 30


@dataclass(frozen=True)
class Client:
    id: str
    name: str                  # a msgid (the product's name, 疊石 Cairn / Cairn)
    secret_env: str
    url_env: str
    scopes: tuple              # what linking grants (the consent text lists them)


CAIRN = Client(
    id="cairn", name=N_("疊石 Cairn"), secret_env="TRC_CAIRN_LINK_SECRET", url_env="TRC_CAIRN_URL",
    # link:participant_ability is never granted by a link: it reads someone else's ability and needs
    # that person's own consent (SP-326), a separate step of its own
    scopes=("link:account", "link:calc", "link:gpx_sync", "link:weather"),
)
CLIENTS: dict[str, Client] = {CAIRN.id: CAIRN}

# the white list: the only routes a service token opens (GET / HEAD), and the scope each needs.
# The login gate (tenancy_mw) refuses a token anywhere else; the route checks it again.
ROUTES: dict[str, str] = {
    "/api/v1/link/account": "link:account",
}
TOKEN_PATH = "/api/v1/link/{client}/token"

# what the user is told they allow (the settings page); one line per granted scope
SCOPE_TEXT = {
    "link:account": N_("讀你的帳號與方案狀態（免費或訂閱、到期日）"),
    "link:calc": N_("讀你存過的賽事計算與配速曲線"),
    "link:gpx_sync": N_("同步 GPX 路線與行程"),
    "link:weather": N_("讀你路線的天氣"),
}

FAIL_BUCKET = RL.Buckets(rate=10, per=600)          # failed authentications per IP
FAIL_BLOCK_S = 600
_BLOCKED: dict = {}

TRANSPORT: Optional[httpx.BaseTransport] = None     # tests: a fake Cairn
TIMEOUT_S = 4.0


def reset_limits() -> None:
    FAIL_BUCKET.reset()
    _BLOCKED.clear()


def failed(ip: str) -> int:
    """Count a failed authentication from `ip`: 401, or 429 once it failed too often."""
    if _BLOCKED.get(ip, 0) > RL.clock():
        return 429
    if not FAIL_BUCKET.take(ip):
        _BLOCKED[ip] = RL.clock() + FAIL_BLOCK_S
        log.warning("link API: an IP blocked for %d min after repeated failed tokens", FAIL_BLOCK_S // 60)
        return 429
    return 401


def client(cid: str) -> Optional[Client]:
    return CLIENTS.get(cid)


def secret(c: Client) -> Optional[str]:
    from backend.settings.env_secrets import secret_env
    return secret_env(c.secret_env)


def base_url(c: Client) -> Optional[str]:
    import os
    v = (os.environ.get(c.url_env) or "").strip().rstrip("/")
    return v if v.startswith("https://") or v.startswith("http://") else None


def secret_ok(c: Client, got: Optional[str]) -> bool:
    want = secret(c)
    return bool(want) and isinstance(got, str) and bool(got) and hmac.compare_digest(
        got.encode("utf-8"), want.encode("utf-8"))


def bearer(headers) -> Optional[str]:
    kind, _, rest = (headers.get("authorization") or "").partition(" ")
    if kind.lower() != "bearer":
        return None
    val = rest.strip()
    return val if 8 <= len(val) <= 300 else None


def service_bearer(headers) -> Optional[str]:
    """A well-formed service token (`trc_…`) from Authorization: Bearer, else None."""
    val = bearer(headers)
    return val if val and val.startswith("trc_") and 20 <= len(val) <= 200 else None


# ---------------------------------------------------------------------------- the user's side

def state(user, c: Client) -> dict:
    """What the settings page shows for one project."""
    from backend.accounts import store
    st = store.link_state(user.id, c.id) or {}
    return {"id": c.id, "name": _(c.name), "linked": bool(st.get("linked")), "at": st.get("at"),
            "available": bool(secret(c)) and bool(user.google_sub),
            "scopes": [{"scope": s, "text": _(SCOPE_TEXT[s])} for s in c.scopes]}


def is_linked(user, c: Client) -> bool:
    from backend.accounts import store
    st = store.link_state(user.id, c.id)
    return bool(st and st["linked"])


def link(user, c: Client) -> None:
    from backend.accounts import store
    if not is_linked(user, c):
        store.record_link(user.id, c.id, True, c.scopes)
        log.info("account linked with %s", c.id)


def unlink(user, c: Client) -> None:
    """Unlinked: a consents row, and every service token of that project deleted (no request it
    made with one is answered from now on)."""
    from backend.accounts import store
    if is_linked(user, c):
        store.record_link(user.id, c.id, False)
        log.info("account unlinked from %s", c.id)


# ---------------------------------------------------------------------------- the project's side

def issue_token(c: Client, sub: str) -> Optional[dict]:
    """A service token for the user whose Google account is `sub`, when that user linked with `c`
    (and is not disabled); None otherwise — whatever the reason, so whether an account exists is
    never told."""
    from backend.accounts import store
    if not isinstance(sub, str) or not sub.strip() or len(sub) > 255:
        return None
    u = store.find_identity("google", sub.strip()) or store.user_by_google_sub(sub.strip())
    if u is None or u.disabled or not is_linked(u, c):
        return None
    granted = (store.link_state(u.id, c.id) or {}).get("scopes") or c.scopes
    scopes = sorted(set(granted) & set(c.scopes))
    token, exp = store.replace_service_token(u.id, c.id, scopes, TOKEN_DAYS)
    return {"token": token, "token_type": "Bearer", "expires_at": exp, "scopes": scopes}


def account_view(user, scopes) -> dict:
    """GET /api/v1/link/account: who (the Google sub, so the caller can check it asked about the
    right person) and the plan. Nothing else of the person's data."""
    from backend import membership as MS
    m = MS.member()
    return {"sub": user.google_sub, "role": m.role, "plan": m.plan,
            "subscribed": m.role in (MS.SUBSCRIBER, MS.OWNER), "plan_expires_at": m.expires_at,
            "scopes": sorted(scopes)}


def cairn_status(user, c: Client = CAIRN) -> Optional[dict]:
    """The person's plan at Cairn (SP-328's shared subscription), asked server to server: only
    for a linked user with a Google account, only when TRC_CAIRN_URL and the secret are set. None
    when not asked or Cairn did not answer (never an error for the page)."""
    url, key = base_url(c), secret(c)
    if not (url and key and user.google_sub and is_linked(user, c)):
        return None
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_S, follow_redirects=False) as cl:
            r = cl.get(f"{url}/api/internal/trc/account", params={"sub": user.google_sub},
                       headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    except httpx.HTTPError as e:
        log.info("Cairn account status: %s", type(e).__name__)
        return None
    if r.status_code != 200:
        log.info("Cairn account status: HTTP %d", r.status_code)
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("sub") != user.google_sub:
        return None                     # an answer about someone else: ignored
    plan = body.get("plan")
    return {"plan": plan if isinstance(plan, str) else None,
            "plan_expires_at": body.get("plan_expires_at") if isinstance(body.get("plan_expires_at"), str) else None}
