"""
Polar's customer state -> this app's subscription row and the account's plan (pure: the time is
passed in). The rules follow inReachLiveTrack's lib/billing-entitlement.ts, on this app's two plans
(membership.py: free | subscriber, plan_expires_at; past it the role is the free tier again, with
no job needed):

  only this app's products count (the monthly and the yearly one; the Polar organization is shared)
  active / trialing, renewing     subscriber, expires current_period_end + RENEWAL_GRACE_DAYS: the
                                  renewal's webhook moves it on; a missed one ends the plan a few
                                  days late instead of never (the page's 重新整理 / the next visit
                                  asks Polar again)
  cancel_at_period_end, or        subscriber until ends_at (else current_period_end), then free
  canceled with that day ahead
  past_due (a renewal failed;     subscriber until past_due_since + PAST_DUE_GRACE_DAYS (first time
  Polar retries the card)         seen; cleared when it is active again), then free
  nothing live, a row before      free now (revoked, refunded and revoked, or ended)
  nothing live, never a row       the account is left alone (a plan the owner gave by hand)
  the owner                       never touched (the row is still kept)

Both doubts lean towards not taking away what someone paid for: a canceled subscription whose
period hasn't ended still counts; a date that can't be read gives no expiry rather than an early one.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

LIVE = ("active", "trialing", "past_due")
PAST_DUE_GRACE_DAYS = 7          # same as inReachLiveTrack (shared/PAST_DUE_GRACE_DAYS)
RENEWAL_GRACE_DAYS = 3
FIELDS = ("user_id", "customer_id", "subscription_id", "product_id", "interval", "status", "current_period_end",
          "cancel_at_period_end", "ends_at", "past_due_since", "source_ts", "synced_at", "updated_at")


def parse(ts) -> Optional[dt.datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def iso(t: Optional[dt.datetime]) -> Optional[str]:
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat() if t else None


def _end_of(s: dict) -> Optional[dt.datetime]:
    return parse(s.get("ends_at")) or parse(s.get("current_period_end"))


def _counts(s: dict, now: dt.datetime) -> bool:
    if s.get("status") in LIVE:
        return True
    if s.get("status") == "canceled":
        end = _end_of(s)
        return end is not None and end >= now
    return False


def managed(row: Optional[dict], now: dt.datetime) -> bool:
    """A subscription that still runs (or is paid up to a day ahead): the account's plan is Polar's."""
    if not row:
        return False
    st = row.get("status")
    if st in LIVE:
        if row.get("cancel_at_period_end"):
            end = _end_of(row)
            return end is None or end >= now
        return True
    if st == "canceled":
        end = _end_of(row)
        return end is not None and end >= now
    return False


@dataclass(frozen=True)
class Outcome:
    row: Optional[dict]             # the billing_subscriptions row to store (None: nothing to store)
    plan: Optional[tuple]           # (plan, plan_expires_at) to write on the account (None: leave it)


def derive(*, user_id: int, state: dict, products: dict, prev: Optional[dict], is_owner: bool,
           now: dt.datetime, source_ts: str) -> Outcome:
    """`products`: interval -> product id (only these count). `prev`: the stored row or None."""
    by_product = {v: k for k, v in products.items() if v}
    subs = [s for s in (state.get("active_subscriptions") or []) if isinstance(s, dict)]
    ours = [s for s in subs if s.get("product_id") in by_product and _counts(s, now)]
    customer_id = state.get("id") or (prev or {}).get("customer_id")
    if not ours:
        if prev is None:
            return Outcome(None, None)
        row = {**prev, "customer_id": customer_id, "status": "revoked", "cancel_at_period_end": 0,
               "past_due_since": None, "source_ts": source_ts, "updated_at": source_ts}
        return Outcome(row, None if is_owner else ("free", None))
    # the one that runs longest (two at once only by a mistake: a second checkout in another tab)
    sub = max(ours, key=lambda s: (s.get("status") in LIVE, _end_of(s) or now))
    status = sub.get("status")
    cancel = bool(sub.get("cancel_at_period_end"))
    past_due_since = None
    if status == "past_due":
        past_due_since = (prev or {}).get("past_due_since") if (prev or {}).get("status") == "past_due" else None
        past_due_since = past_due_since or iso(now)
    row = {"user_id": int(user_id), "customer_id": customer_id, "subscription_id": sub.get("id"),
           "product_id": sub.get("product_id"), "interval": by_product[sub.get("product_id")], "status": status,
           "current_period_end": iso(parse(sub.get("current_period_end"))), "cancel_at_period_end": int(cancel),
           "ends_at": iso(parse(sub.get("ends_at"))), "past_due_since": past_due_since, "source_ts": source_ts,
           "synced_at": (prev or {}).get("synced_at"), "updated_at": source_ts}
    if is_owner:
        return Outcome(row, None)
    if status == "past_due":
        expires = parse(past_due_since) + dt.timedelta(days=PAST_DUE_GRACE_DAYS)
    elif cancel or status == "canceled":
        expires = _end_of(sub)
    else:
        end = parse(sub.get("current_period_end"))
        expires = end + dt.timedelta(days=RENEWAL_GRACE_DAYS) if end else None
    return Outcome(row, ("subscriber", iso(expires)))


def summary(row: Optional[dict], now: dt.datetime) -> Optional[dict]:
    """What the pages see (never the Polar customer / subscription ids)."""
    if not row:
        return None
    return {"status": row.get("status"), "interval": row.get("interval"),
            "current_period_end": row.get("current_period_end"), "ends_at": row.get("ends_at"),
            "cancel_at_period_end": bool(row.get("cancel_at_period_end")),
            "past_due_since": row.get("past_due_since"),
            "grace_until": iso(parse(row["past_due_since"]) + dt.timedelta(days=PAST_DUE_GRACE_DAYS))
            if row.get("status") == "past_due" and parse(row.get("past_due_since")) else None,
            "managed": managed(row, now)}
