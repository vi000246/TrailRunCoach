"""
Linking an account here with another project of ours (SP-329): 疊石 Cairn (inReachLiveTrack).

Both sign in with Google; the same Google account (its `sub`, users.google_sub / identities) is
the same person on both sides. Nothing is linked on its own (SP-571 Q4 B): the user links in
設定 › 帳號 › 已連結的服務 — or is sent there by Cairn the first time it needs to read or push
something — and can unlink there at any time. Each link / unlink is a consents row (action
"link" / "unlink", client "cairn", the scopes granted), so the history is in 下載我的資料.

Service to service, two directions, one shared secret per direction (SP-329 Q1-1, owner 2026-10-19:
a leaked one opens one way only) — from the environment only, never stored in accounts.db or a
tenant folder: TRC_CAIRN_LINK_SECRET for Cairn's calls here, TRC_CAIRN_OUT_SECRET for this app's
calls to Cairn (out_secret; while it is not set, the first one with a warning — the deployments
before the split):

  Cairn → here   POST /api/v1/link/cairn/token   {sub}   Authorization: Bearer <shared secret>
                 a linked user's service token (`trc_…`, 30 days, one live per user: a new one
                 replaces the old), carrying LINK_SCOPES_GRANTED; 404 NOT_LINKED for a sub that
                 is not a linked user here (whether the account exists is never said), with the
                 page where the user can link. Then
                 GET /api/v1/link/account   Authorization: Bearer <service token>
                 (api/link.py; the login gate opens only the routes in ROUTES with such a token)
  here → Cairn   cairn_status(sub): GET <TRC_CAIRN_URL>/api/internal/trc/account?sub=…
                 with the outbound secret — Cairn's side of the contract (to be built there).
                 Only for a linked user; nothing kept (no Cairn token is ever stored here)

Each feature beyond the account asks on its own (FEATURES, SP-654): GET /api/v1/link/cairn/curves
(link:calc, link_calc.py) answers only once the person switched 「允許 Cairn 讀取」 on in 設定; the
race page's 「推到 Cairn」 (link:gpx_sync, cairn_push.py, SP-655) pushes only once it is on.

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
    scopes: tuple              # what the project may ask for: linking grants these but FEATURES,
                               # which the person allows one by one (the settings page lists them),
                               # and NOT_YET (no route yet: asked when it ships)
    out_secret_env: str = ""   # this app's calls to the project (SP-329 Q1-1); secret_env: its calls here


CAIRN = Client(
    id="cairn", name=N_("疊石 Cairn"), secret_env="TRC_CAIRN_LINK_SECRET", url_env="TRC_CAIRN_URL",
    out_secret_env="TRC_CAIRN_OUT_SECRET",
    # link:participant_ability is never granted by a link: it reads someone else's ability and needs
    # that person's own consent (SP-326), a separate step of its own
    scopes=("link:account", "link:calc", "link:gpx_sync", "link:weather"),
)
CLIENTS: dict[str, Client] = {CAIRN.id: CAIRN}

# the white list: the only routes a service token opens (GET / HEAD), and the scope each needs.
# The login gate (tenancy_mw) refuses a token anywhere else; the route checks it again.
ROUTES: dict[str, str] = {
    "/api/v1/link/account": "link:account",
    "/api/v1/link/cairn/curves": "link:calc",            # SP-654 (SP-570 TRC-4): link_calc.py
}

# features that ask on their own (SP-329 Q1-3: each feature asks again when it launches, SP-571 Q4
# B): linking does not allow them; the person switches each on (or off again) in 設定 › 已連結的
# 服務, a consents row "allow" / "disallow" each, and linking again asks again. A service token
# still carries the scope (what the project may ask for); the login gate checks the switch on
# every request (403 CONSENT_REQUIRED, with the page where the person can allow it). `plan`: the
# plan the feature needs on top (SP-570 Q1-5; the route answers 402 PLAN_REQUIRED without it).
# link:gpx_sync (SP-655, cairn_push.py): the push of a race to Cairn — here the person pushes, from
# the race page; the push dialog asks for the same consent when it is still off. link:weather joins
# here when its route ships.
FEATURES: dict[str, dict] = {
    "link:calc": {"plan": "subscriber"},
    "link:gpx_sync": {"plan": "subscriber"},
}
# what the project may ask for but no route serves yet (SP-329 Q1-3, owner 2026-10-19: each feature
# asks when it launches): not granted by linking, not offered as a switch, never in a token; it moves
# to FEATURES with its route. A link recorded before keeps its consents row, the scope is dropped.
NOT_YET = frozenset({"link:weather"})
TOKEN_PATH = "/api/v1/link/{client}/token"

# what the user is told they allow (the settings page); one line per granted scope
SCOPE_TEXT = {
    "link:account": N_("讀你的帳號與方案狀態（免費或訂閱、到期日）"),
    "link:calc": N_("讀你存過的賽事計算與配速曲線"),
    "link:gpx_sync": N_("把你的賽事路線、過夜點與腳程推到 Cairn（建立或更新行程）"),
    "link:weather": N_("讀你路線的天氣"),
}
# SP-666 (設定 › Cairn): which way each scope goes — Cairn reads from here, or this app writes to
# Cairn — and in plain words what it lets Cairn do (the tab's line under each switch)
SCOPE_KIND = {"link:account": "read", "link:calc": "read", "link:gpx_sync": "write", "link:weather": "read"}
SCOPE_DETAIL = {
    "link:account": N_("Cairn 會讀：你在這裡是免費還是訂閱、到期日。不讀信箱或其他個資。"),
    "link:calc": N_("Cairn 會讀：你對同一份 GPX 存過的賽事計算與配速曲線，用你的體能推估各點的時刻。"
                    "不讀活動、心率或其他健康資料。"),
    "link:gpx_sync": N_("會寫到 Cairn：你在賽事頁按「推到 Cairn」時，那場的路線、過夜點與腳程曲線，"
                        "建立或更新 Cairn 上的一條行程。不寫時刻，也不改你在 Cairn 自己建的行程。"),
    "link:weather": N_("Cairn 會讀：你路線的天氣（功能還沒上線）。"),
}
# Cairn's pushes a day by its plan (its trc_sync_per_day: SP-657, v2/server/src/lib/limits.ts at
# 7c65153; None = no limit). A hint only — Cairn decides (429 CAIRN_QUOTA); a plan not here: no number
CAIRN_PUSH_PER_DAY = {"guest": 0, "free": 0, "basic": 20, "pro": 100, "owner": None}

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
    """The secret the project calls here with (the token exchange)."""
    from backend.settings.env_secrets import secret_env
    return secret_env(c.secret_env)


_WARNED: set = set()


def out_secret(c: Client) -> Optional[str]:
    """The secret this app calls the project with (SP-329 Q1-1: its own, out_secret_env). Not set:
    the inbound one, with a warning once — so a deployment from before the split keeps working
    until both sides set the second secret."""
    from backend.settings.env_secrets import secret_env
    own = secret_env(c.out_secret_env) if c.out_secret_env else None
    if own:
        return own
    shared = secret(c)
    if shared and c.id not in _WARNED:
        _WARNED.add(c.id)
        log.warning("%s: %s not set — calling %s with the inbound secret; set a second one (SP-329)",
                    c.id, c.out_secret_env or "an outbound secret", c.id)
    return shared


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
    linked = bool(st.get("linked"))
    return {"id": c.id, "name": _(c.name), "linked": linked, "at": st.get("at"),
            "available": bool(secret(c)) and bool(user.google_sub),
            "scopes": [_scope_view(s) for s in link_scopes(c)],
            "features": [{**_scope_view(s),
                          "allowed": linked and store.link_scope_allowed(user.id, c.id, s),
                          "plan": FEATURES[s].get("plan")} for s in features(c)]}


def _scope_view(s: str) -> dict:
    return {"scope": s, "text": _(SCOPE_TEXT[s]), "kind": SCOPE_KIND.get(s, "read"),
            "detail": _(SCOPE_DETAIL[s]) if s in SCOPE_DETAIL else ""}


def plan_here() -> dict:
    """The person's plan here, for Cairn's tab (the features need a paid plan: SP-570 Q1-5)."""
    from backend import membership as MS
    m = MS.member()
    return {"role": m.role, "plan_ok": m.role in (MS.SUBSCRIBER, MS.OWNER), "upgrade": m.role == MS.USER}


def link_scopes(c: Client) -> tuple:
    """What linking itself allows (the features asked on their own and those not out yet left out)."""
    return tuple(s for s in c.scopes if s not in FEATURES and s not in NOT_YET)


def features(c: Client) -> tuple:
    return tuple(s for s in c.scopes if s in FEATURES)


def allowed(user, c: Client, scope: str) -> bool:
    """`scope` usable now: linked, and for a feature that asks on its own, switched on since."""
    from backend.accounts import store
    if not is_linked(user, c):
        return False
    if scope in FEATURES:
        return scope in c.scopes and store.link_scope_allowed(user.id, c.id, scope)
    return True


def set_feature(user, c: Client, scope: str, on: bool) -> None:
    """The person switches one feature on / off (a consents row; only a change is recorded)."""
    from backend.accounts import store
    if scope not in features(c):
        raise ValueError(f"not a feature of {c.id}: {scope}")
    if store.link_scope_allowed(user.id, c.id, scope) != on:
        store.record_link_scope(user.id, c.id, scope, on)
        log.info("%s %s for %s", scope, "allowed" if on else "switched off", c.id)


def link_url(c: Client, scope: Optional[str] = None) -> str:
    """Where the person links (設定 › 帳號, the project's row highlighted) or, with `scope`, allows
    that feature."""
    from urllib.parse import quote
    from backend.accounts import login as LG
    path = f"/api/v1/app/settings?link={c.id}" + (f"&allow={quote(scope, safe='')}" if scope else "") + "#links"
    return (LG._env(LG.ENV_PUBLIC_URL) or "").rstrip("/") + path


def is_linked(user, c: Client) -> bool:
    from backend.accounts import store
    st = store.link_state(user.id, c.id)
    return bool(st and st["linked"])


def link(user, c: Client) -> None:
    from backend.accounts import store
    if not is_linked(user, c):
        store.record_link(user.id, c.id, True, link_scopes(c))
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
    granted = (store.link_state(u.id, c.id) or {}).get("scopes") or link_scopes(c)
    # the features too: what the project may ask for; whether it may now is checked per request.
    # A scope not out yet is never in a token, even when an older link recorded it (SP-329 Q1-3)
    scopes = sorted(((set(granted) | set(features(c))) & set(c.scopes)) - NOT_YET)
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
    url, key = base_url(c), out_secret(c)
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
    out = {"plan": plan if isinstance(plan, str) else None,
           "plan_expires_at": body.get("plan_expires_at") if isinstance(body.get("plan_expires_at"), str) else None}
    if out["plan"] in CAIRN_PUSH_PER_DAY:
        out["push_per_day"] = CAIRN_PUSH_PER_DAY[out["plan"]]
    # SP-666 Q1-1: Cairn tells its own limit and today's count (trc_sync_per_day — null: no limit — and
    # trc_sync_used_today, the fields asked for in the Cairn ticket); then they win over the table above
    lim, used = body.get("trc_sync_per_day", _ABSENT), body.get("trc_sync_used_today")
    if lim is None or (isinstance(lim, int) and not isinstance(lim, bool) and lim >= 0):
        out["push_per_day"] = lim
        if lim is not None and isinstance(used, int) and not isinstance(used, bool) and used >= 0:
            out["push_used_today"] = used
            out["push_left_today"] = max(0, lim - used)
    return out


_ABSENT = object()
