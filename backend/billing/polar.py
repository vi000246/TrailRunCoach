"""
The few Polar API calls this app makes (the same calls and wire format as inReachLiveTrack's
lib/polar-client.ts; snake_case on the wire). Each returns `Result`: never raises for an upstream
answer, so a 404 (this customer never bought anything), a 5xx and a dropped connection (status 0)
stay apart — the callers do different things for each.

  create_checkout        POST  /v1/checkouts/                         -> url
  customer_state         GET   /v1/customers/external/{id}/state      -> state (404: never bought)
  customer_portal        POST  /v1/customer-sessions/                  -> url
  cancel_at_period_end   PATCH /v1/subscriptions/{id}                  (an account being deleted)

The token goes in the Authorization header only; nothing here logs a request or a response body.
Tests (and the local fake Polar) set TRANSPORT to an httpx.MockTransport.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

import httpx

from backend.billing import Config

TIMEOUT_S = 15.0
TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


@dataclass
class Result:
    ok: bool
    status: int
    data: Any = None
    message: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @property
    def not_found(self) -> bool:
        return self.status == 404


def _message(body) -> Optional[str]:
    if not isinstance(body, dict):
        return None
    d = body.get("detail")
    if isinstance(d, str):
        return d[:200]
    if isinstance(d, list):
        return "; ".join(str(x.get("msg") if isinstance(x, dict) else x) for x in d)[:200]
    return None


async def _call(cfg: Config, method: str, path: str, json: Optional[dict] = None) -> Result:
    kw = {"timeout": TIMEOUT_S, "trust_env": False, "follow_redirects": False}
    if TRANSPORT is not None:
        kw["transport"] = TRANSPORT
    headers = {"Authorization": f"Bearer {cfg.token}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(**kw) as c:
            r = await c.request(method, cfg.api_base + path, json=json, headers=headers)
    except httpx.HTTPError as e:
        return Result(False, 0, message=type(e).__name__)
    try:
        body = r.json()
    except ValueError:
        body = None
    if r.status_code >= 400:
        return Result(False, r.status_code, message=_message(body))
    if body is None and r.status_code != 204:
        return Result(False, r.status_code, message="not JSON")
    return Result(True, r.status_code, body)


async def create_checkout(cfg: Config, product_id: str, external_customer_id: str, email: Optional[str],
                          success_url: str, return_url: str, metadata: dict) -> Result:
    body = {"products": [product_id], "external_customer_id": external_customer_id,
            "success_url": success_url, "return_url": return_url, "metadata": metadata}
    if email:
        body["customer_email"] = email
    r = await _call(cfg, "POST", "/v1/checkouts/", body)
    if r.ok and not (isinstance(r.data, dict) and r.data.get("url")):
        return Result(False, r.status, message="checkout without url")
    if r.ok:
        r.data = r.data["url"]
    return r


async def customer_state(cfg: Config, external_id: str) -> Result:
    return await _call(cfg, "GET", f"/v1/customers/external/{quote(external_id, safe='')}/state")


async def customer_portal(cfg: Config, customer_id: str, return_url: str) -> Result:
    r = await _call(cfg, "POST", "/v1/customer-sessions/", {"customer_id": customer_id, "return_url": return_url})
    if r.ok and not (isinstance(r.data, dict) and r.data.get("customer_portal_url")):
        return Result(False, r.status, message="session without url")
    if r.ok:
        r.data = r.data["customer_portal_url"]
    return r


async def cancel_at_period_end(cfg: Config, subscription_id: str) -> Result:
    """Stop the renewals (the paid period runs out; no refund here — that is the owner's call in Polar)."""
    return await _call(cfg, "PATCH", f"/v1/subscriptions/{quote(subscription_id, safe='')}",
                       {"cancel_at_period_end": True})
