"""
The subscription's flows (api/billing.py calls these; the package docstring has the design):
checkout, the webhook, sync (ask Polar), the portal, cancelling before an account is deleted.
Polar failures come back as UserError(code, status 502) with a message for the user; the log lines
name the user id and the upstream status only (never a token, an e-mail or a payload).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Optional

from backend import billing as B
from backend.billing import entitlement as E, polar as P, signature as S
from backend.i18n import N_, UserError

log = logging.getLogger(__name__)

SYNC_MIN_INTERVAL_S = 30          # one Polar call per user per 30 s, however often 重新整理 is pressed
STALE_AFTER_S = 24 * 3600         # the plans page asks again when the last answer is older


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def not_open() -> UserError:
    return UserError(N_("訂閱還沒開放"), code="BILLING_NOT_OPEN", status=503)


def _upstream(what: str, r: P.Result, uid: Optional[int]) -> UserError:
    log.warning("polar %s failed: user=%s status=%s", what, uid, r.status)
    return UserError(N_("付款服務暫時沒有回應，請稍後再試"), code="BILLING_UPSTREAM", status=502)


def _compute(state: dict, cfg: B.Config, now: dt.datetime, source_ts: str):
    def f(prev, user):
        return E.derive(user_id=user.id, state=state, products=cfg.products, prev=prev, is_owner=user.is_owner,
                        now=now, source_ts=source_ts)
    return f


def view(user, now: Optional[dt.datetime] = None) -> dict:
    """GET /api/v1/billing: open or not, the prices, this user's plan and subscription."""
    from backend.accounts import store
    from backend import membership as MS
    now = now or _now()
    cfg = B.config()
    u = store.get_user(user.id) or user
    role = MS.role_of_user(u, now)
    return {"open": B.available() and cfg.ready, "env": cfg.env if cfg.ready else None,
            "prices": {i: {"amount": B.PRICES[i], "currency": B.CURRENCY} for i in B.INTERVALS},
            "role": role, "plan_expires_at": u.plan_expires_at if role == MS.SUBSCRIBER else None,
            "subscription": E.summary(store.billing_row(u.id), now)}


async def checkout(user, interval: str, base_url: str) -> str:
    from backend.accounts import store
    cfg = B.config()
    if not (B.available() and cfg.ready):
        raise not_open()
    if interval not in B.INTERVALS:
        raise UserError(N_("請選月繳或年繳"), code="BAD_INTERVAL", status=400)
    if user.is_owner:
        raise UserError(N_("站主不需要訂閱"), code="OWNER", status=400)
    if E.managed(store.billing_row(user.id), _now()):
        raise UserError(N_("你已經有訂閱中的方案；要換方案或取消請按「管理訂閱」"), code="SUBSCRIPTION_EXISTS",
                        status=409)
    r = await P.create_checkout(cfg, cfg.products[interval], B.external_id(user.id), user.email,
                                success_url=f"{base_url}/plans?billing=ok&checkout_id={{CHECKOUT_ID}}",
                                return_url=f"{base_url}/plans?billing=cancel",
                                metadata={"trc_user_id": str(user.id), "interval": interval})
    if not r.ok:
        raise _upstream("checkout", r, user.id)
    log.info("checkout opened: user=%s interval=%s", user.id, interval)
    return r.data


async def sync(user, force: bool = False, now: Optional[dt.datetime] = None) -> dict:
    """Ask Polar for this user's state and apply it. Never downgrades without an answer: a failed
    call changes nothing (UserError 502). 404 = never bought: an empty state."""
    from backend.accounts import store
    now = now or _now()
    cfg = B.config()
    if not (B.available() and cfg.ready):
        raise not_open()
    prev = store.billing_row(user.id)
    synced = E.parse((prev or {}).get("synced_at"))
    if not force and synced and (now - synced).total_seconds() < SYNC_MIN_INTERVAL_S:
        return view(user, now)
    r = await P.customer_state(cfg, B.external_id(user.id))
    if r.ok and isinstance(r.data, dict):
        state = r.data
    elif r.not_found:
        state = {"active_subscriptions": []}
    else:
        raise _upstream("sync", r, user.id)
    at = E.iso(now)
    store.billing_apply(user.id, at, _compute(state, cfg, now, at), now=now)
    if store.billing_row(user.id) is not None:
        store.billing_mark_synced(user.id, at)
    return view(user, now)


async def maybe_sync(user, now: Optional[dt.datetime] = None) -> None:
    """The plans page: a subscription last checked over a day ago is asked again (a missed webhook).
    Quietly: a failure is logged only."""
    from backend.accounts import store
    now = now or _now()
    row = store.billing_row(user.id)
    if row is None or not B.config().ready or not B.available():
        return
    synced = E.parse(row.get("synced_at")) or E.parse(row.get("source_ts"))
    if synced and (now - synced).total_seconds() < STALE_AFTER_S:
        return
    try:
        await sync(user, now=now)
    except UserError:
        pass


async def portal(user, base_url: str) -> str:
    from backend.accounts import store
    cfg = B.config()
    if not (B.available() and cfg.ready):
        raise not_open()
    row = store.billing_row(user.id)
    if not row or not row.get("customer_id"):
        await sync(user, force=True)
        row = store.billing_row(user.id)
    if not row or not row.get("customer_id"):
        raise UserError(N_("你還沒有訂閱，沒有可以管理的項目"), code="NO_SUBSCRIPTION", status=409)
    r = await P.customer_portal(cfg, row["customer_id"], f"{base_url}/plans?billing=portal")
    if not r.ok:
        raise _upstream("portal", r, user.id)
    return r.data


def handle_webhook(body: bytes, headers, now: Optional[dt.datetime] = None) -> tuple[int, dict]:
    """(status, answer). 404 while not configured; 401 for a bad / missing / old signature (nothing
    happens); 200 for anything signed (a duplicate, another type, another app's customer, a body
    that can't be read — a retry wouldn't change it); a failure applying it propagates (500: Polar
    retries, and since the event row is written with the change, the retry is applied)."""
    from backend.accounts import store
    cfg = B.config()
    if not (B.available() and cfg.webhook_secret):
        return 404, {"detail": "not enabled"}
    msg_id, ts = headers.get("webhook-id"), headers.get("webhook-timestamp")
    if not S.verify(cfg.webhook_secret, body, msg_id, ts, headers.get("webhook-signature"),
                    now=now.timestamp() if now else None):
        return 401, {"detail": "bad signature"}
    try:
        payload = json.loads(body)
    except ValueError:
        return 200, {"handled": 0}
    if not isinstance(payload, dict):
        return 200, {"handled": 0}
    if store.billing_event_seen(msg_id):
        return 200, {"handled": 0, "duplicate": True}
    etype = str(payload.get("type") or "unknown")[:64]
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    uid = B.user_id_of_external(data.get("external_id")) if etype == "customer.state_changed" else None
    source_ts = E.iso(dt.datetime.fromtimestamp(int(ts), dt.timezone.utc))
    when = now or _now()
    out = store.billing_apply(uid, source_ts, _compute(data, cfg, when, source_ts), event_id=msg_id,
                              event_type=etype, now=when)
    if out.get("duplicate"):
        return 200, {"handled": 0, "duplicate": True}
    if uid is not None:
        log.info("polar webhook: %s user=%s applied=%s%s", etype, uid, out.get("applied"),
                 " stale" if out.get("stale") else "")
    return 200, {"handled": int(bool(out.get("applied"))), **({"stale": True} if out.get("stale") else {})}


async def cancel_for_deletion(user) -> Optional[str]:
    """Before an account is deleted (SP-386: stop the charges first). Nothing to do: None. A live
    subscription: Polar is told to stop renewing it; "canceled" when it did. When Polar can't be
    reached the deletion must not go on (the card would still be charged for a deleted account):
    UserError 502 / 409."""
    from backend.accounts import store
    row = store.billing_row(user.id)
    if not E.managed(row, _now()) or row.get("cancel_at_period_end") or row.get("status") == "canceled":
        return None
    cfg = B.config()
    if not cfg.token or not row.get("subscription_id"):
        log.warning("account deletion: user=%s has a live subscription and billing isn't configured", user.id)
        raise UserError(N_("你的訂閱還在扣款，目前無法自動取消；請先在「方案與訂閱」按「管理訂閱」取消後再刪除帳號"),
                        code="SUBSCRIPTION_ACTIVE", status=409)
    r = await P.cancel_at_period_end(cfg, row["subscription_id"])
    if not r.ok and not r.not_found:
        raise _upstream("cancel", r, user.id)
    log.info("account deletion: subscription of user=%s set to end", user.id)
    return "canceled"
