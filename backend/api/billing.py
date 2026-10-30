"""
方案與訂閱 (SP-328; backend/billing has the design). Sign-in mode only: every route answers 404 in
the owner's single-user app (nobody to bill); the demo never mounts this router.

  GET  /plans                        the page: the two plans, the checkout, the subscription's state
                                     (back from Polar: ?billing=ok | cancel | portal)
  GET  /pricing                      方案與價格 (SP-846): public, signed in or not — the prices, the plan
                                     cards and the same comparison as /plans (billing/plan_table.py)
  GET  /api/v1/billing              {open, env, prices, role, plan_expires_at, subscription}; a
                                     subscription last checked over a day ago is asked again first
  POST /api/v1/billing/checkout      {interval: month | year, agree: true} -> {url} of Polar's
                                     checkout; 503 BILLING_NOT_OPEN until it is configured, 409
                                     SUBSCRIPTION_EXISTS with a live subscription
  POST /api/v1/billing/sync          ask Polar now (at most once in 30 s) -> the same as GET
  POST /api/v1/billing/portal        -> {url} of Polar's customer portal (card, cancel, receipts)
  POST /api/v1/billing/webhook       Polar's webhook: no session, no CSRF (tenancy_mw lets this one
                                     path through); the Standard Webhooks signature is the only
                                     authority (401 without it, 404 while not configured)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import billing as B
from backend.billing import plan_table as PT, service as SV
from backend.i18n import N_, UserError
from backend.i18n.pages import render_page

router = APIRouter(tags=["billing"], include_in_schema=False)
log = logging.getLogger(__name__)

WEBHOOK_PATH = "/api/v1/billing/webhook"
MAX_BODY = 1024 * 1024
NO_STORE = {"Cache-Control": "private, no-store"}


def _on() -> None:
    if not B.available():
        raise HTTPException(404, "not found")


def _user():
    from backend.accounts import login as LG
    u = LG.current_user()
    if u is None:
        raise UserError(N_("請先登入"), code="LOGIN_REQUIRED", status=401)
    return u


def _paying_user():
    """The signed-in user, never a test account (SP-611: test accounts stay out of payment)."""
    u = _user()
    if u.is_test:
        raise UserError(N_("測試帳號不能訂閱或付款"), code="TEST_ACCOUNT", status=403)
    return u


def _base(request: Request) -> str:
    """The site's public origin (behind the tunnel the app sees an internal host)."""
    from backend.accounts import google
    return google.public_url() or str(request.base_url).rstrip("/")


@router.get("/plans")
def plans_page():
    _on()
    return render_page("plans", headers={"Cache-Control": "private, no-cache"},
                       fill={"plan_rows": PT.rows_html("plans")})


@router.get(PT.URL)
def pricing_page(embed: bool = False):
    """方案與價格 (SP-846): public (login.PUBLIC_PATHS), linked from the 介紹頁; the same table as /plans.
    ?embed=1: the 介紹頁's page dialog frames it without its top bar and footer."""
    _on()
    return PT.pricing_page(embed)


@router.get("/api/v1/billing")
async def billing_view():
    _on()
    u = _user()
    if not u.is_test:                      # a test account never reaches the payment provider (SP-611)
        await SV.maybe_sync(u)
    return JSONResponse(SV.view(u), headers=NO_STORE)


class CheckoutIn(BaseModel):
    interval: str = B.MONTH
    agree: bool = False


@router.post("/api/v1/billing/checkout")
async def billing_checkout(body: CheckoutIn, request: Request):
    _on()
    u = _paying_user()
    if not body.agree:
        raise UserError(N_("請先勾選同意服務條款"), code="TERMS_NOT_AGREED", status=400)
    url = await SV.checkout(u, body.interval, _base(request))
    return JSONResponse({"url": url}, headers=NO_STORE)


@router.post("/api/v1/billing/sync")
async def billing_sync():
    _on()
    return JSONResponse(await SV.sync(_paying_user()), headers=NO_STORE)


@router.post("/api/v1/billing/portal")
async def billing_portal(request: Request):
    _on()
    return JSONResponse({"url": await SV.portal(_paying_user(), _base(request))}, headers=NO_STORE)


@router.post(WEBHOOK_PATH)
async def billing_webhook(request: Request):
    from starlette.concurrency import run_in_threadpool
    _on()
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "too large")
    status, out = await run_in_threadpool(SV.handle_webhook, body, request.headers)
    return JSONResponse(out, status_code=status, headers={"Cache-Control": "no-store"})
