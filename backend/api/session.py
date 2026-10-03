"""
GET /api/v1/session — who / what this page is for (auth-and-demo plan §3.6);
the shell (static/shell.js) reads it first. Later sign-in keeps the shape:

    {"mode": "demo", "caps": ["plan.write", "upload.gpx"],
     "demo": {"sandbox": true, "expires_at": "…", "base": "2026-09-28"},
     "user": null, "csrf": "<token>"}

It also issues the CSRF cookie (trc_csrf, readable by the page): writes send
it back as the X-TRC-CSRF header (double submit, backend/tenancy_mw.py).

POST /api/v1/demo/reset — the demo's 「重設示範」: delete the visitor's sandbox;
the next write starts a fresh one from the demo base.
"""
from __future__ import annotations

import datetime as dt
import os
import secrets as _secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend import tenancy
from backend.tenancy_mw import CSRF_COOKIE, cookie, cookie_attrs

router = APIRouter(tags=["session"])


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def session_info(had_cookie: bool = False) -> dict:
    """{mode, caps, demo?, user}: also inlined into every page (i18n/pages.render_page)."""
    t = tenancy.current()
    body: dict = {"mode": "demo" if tenancy.demo_mode() else "owner",
                  "caps": sorted(t.caps), "user": None}
    if tenancy.demo_mode():
        from backend.demo import sandbox as SB
        demo: dict = {"sandbox": t.kind == tenancy.DEMO_SANDBOX, "base": None, "expires_at": None,
                      "reset": had_cookie and t.kind != tenancy.DEMO_SANDBOX}
        try:
            demo["base"] = SB.current_base_name()
        except SB.DemoNotReady:
            pass
        if t.kind == tenancy.DEMO_SANDBOX:
            meta = SB.read_meta(t.root) or {}
            demo["expires_at"] = _iso(SB.expires_at(meta))
        demo["cta_url"] = os.environ.get("WKO5COACH_DEMO_CTA_URL") or None
        body["demo"] = demo
    return body


@router.get("/api/v1/session")
def get_session(request: Request):
    from backend.demo.sandbox import COOKIE
    body = session_info(bool(cookie(request.scope, COOKIE)))
    tok = cookie(request.scope, CSRF_COOKIE) or _secrets.token_urlsafe(24)
    body["csrf"] = tok
    resp = JSONResponse(body, headers={"Cache-Control": "no-store"})
    resp.headers.append("set-cookie", f"{CSRF_COOKIE}={tok}; {cookie_attrs(30 * 86400, http_only=False)}")
    return resp


@router.post("/api/v1/demo/reset")
def demo_reset(request: Request):
    if not tenancy.demo_mode():
        raise HTTPException(404, "not found")
    from backend.demo import sandbox as SB
    t = tenancy.current()
    if t.kind == tenancy.DEMO_SANDBOX:
        SB.delete(t.root.name)
    resp = JSONResponse({"ok": True, "sandbox": False})
    resp.headers.append("set-cookie", f"{SB.COOKIE}=; {cookie_attrs(0)}")
    return resp
