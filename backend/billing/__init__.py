"""
訂閱付款 (SP-328): Polar, the same payment provider and merchant account as inReachLiveTrack
(SP-321: one payment setup for both; its design is docs/srs/livetrack-subscription-billing.srs.md
there). Polar is the merchant of record: it sells, charges the card, sends the receipts and the
failed-payment / card-update e-mails, and runs the customer portal (change card, cancel, receipts).
This app only

  * opens a checkout for the signed-in user (one product per interval: NT$99 a month, NT$890 a year);
  * takes Polar's webhook (Standard Webhooks signature checked before anything is parsed; each
    webhook-id applied once; `customer.state_changed` carries the customer's whole state, so the
    newest one wins) and writes the account's plan (membership.py, SP-330: plan "subscriber" with
    plan_expires_at; past it the role falls back to the free tier by itself);
  * asks Polar again when the user comes back from the checkout, presses 重新整理, or opens the
    plans page with a subscription last checked over a day ago;
  * opens the customer portal; cancels the subscription before an account is deleted (SP-386).

Settings (environment only, never in a file of the repo or a log line; settings/env_secrets.py):

  TRC_POLAR_TOKEN              Organization Access Token (secret)
  TRC_POLAR_WEBHOOK_SECRET     the webhook endpoint's secret (secret)
  TRC_POLAR_ENV                sandbox (default) | production
  TRC_POLAR_PRODUCT_MONTH      the monthly product's id
  TRC_POLAR_PRODUCT_YEAR       the yearly product's id
  TRC_POLAR_API_BASE           a local fake Polar for development (http://127.0.0.1:<port> only)

Missing any of the first two or both products: the plans page says 尚未開放 and nothing calls Polar.
Only in sign-in mode (login.required()): the owner's own app has nobody to bill.

Shared with inReachLiveTrack: the Polar organization (merchant account, payouts, tax as merchant of
record) and its product catalogue; not shared: the database (each app keeps its own subscription
rows, in its own DB), the webhook endpoint (Polar sends every event to each endpoint registered;
each app counts only its own products) and the customer key: this app's external customer id is
`trc:<user id>` (inReachLiveTrack's are bare UUIDs), so the two never claim each other's customer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from backend.settings.env_secrets import secret_env

ENV_TOKEN = "TRC_POLAR_TOKEN"
ENV_WEBHOOK_SECRET = "TRC_POLAR_WEBHOOK_SECRET"
ENV_ENV = "TRC_POLAR_ENV"
ENV_PRODUCT_MONTH = "TRC_POLAR_PRODUCT_MONTH"
ENV_PRODUCT_YEAR = "TRC_POLAR_PRODUCT_YEAR"
ENV_API_BASE = "TRC_POLAR_API_BASE"

SANDBOX, PRODUCTION = "sandbox", "production"
API_BASE = {SANDBOX: "https://sandbox-api.polar.sh", PRODUCTION: "https://api.polar.sh"}
MONTH, YEAR = "month", "year"
INTERVALS = (MONTH, YEAR)
# SP-321's prices (TWD); what the page shows. Polar's product holds the price actually charged
PRICES = {MONTH: 99, YEAR: 890}
CURRENCY = "TWD"
EXTERNAL_PREFIX = "trc:"


@dataclass(frozen=True)
class Config:
    token: Optional[str]
    webhook_secret: Optional[str]
    env: str
    products: dict            # interval -> product id
    api_base: str

    @property
    def ready(self) -> bool:
        """Checkout, sync and portal can call Polar."""
        return bool(self.token and self.webhook_secret and all(self.products.get(i) for i in INTERVALS))

    def interval_of(self, product_id: Optional[str]) -> Optional[str]:
        for k, v in self.products.items():
            if v and v == product_id:
                return k
        return None


def _local_base(raw: str) -> Optional[str]:
    try:
        u = urlsplit(raw)
    except ValueError:
        return None
    if u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1") and u.path in ("", "/"):
        return raw.rstrip("/")
    return None


def config() -> Config:
    """Read every time (cheap; the tests change the environment). Never logged."""
    env = (os.environ.get(ENV_ENV) or SANDBOX).strip().lower()
    if env not in API_BASE:
        env = SANDBOX
    base = API_BASE[env]
    fake = (os.environ.get(ENV_API_BASE) or "").strip()
    if fake:
        base = _local_base(fake) or base       # anything but a local address is ignored
    return Config(token=secret_env(ENV_TOKEN), webhook_secret=secret_env(ENV_WEBHOOK_SECRET), env=env,
                  products={MONTH: (os.environ.get(ENV_PRODUCT_MONTH) or "").strip() or None,
                            YEAR: (os.environ.get(ENV_PRODUCT_YEAR) or "").strip() or None},
                  api_base=base)


def available() -> bool:
    """Billing applies to this server at all: sign-in mode (accounts/login.py), never the owner's
    single-user app or the demo."""
    from backend.accounts import login
    return login.required()


def open_for_sale() -> bool:
    return available() and config().ready


def external_id(uid: int) -> str:
    return f"{EXTERNAL_PREFIX}{int(uid)}"


def user_id_of_external(ext) -> Optional[int]:
    if isinstance(ext, str) and ext.startswith(EXTERNAL_PREFIX) and ext[len(EXTERNAL_PREFIX):].isdigit():
        return int(ext[len(EXTERNAL_PREFIX):])
    return None
