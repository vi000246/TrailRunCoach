"""
GET /api/v1/session — who / what this page is for (auth-and-demo plan §3.6);
the shell (static/shell.js) reads it first. Later sign-in keeps the shape:

    {"mode": "demo", "caps": ["plan.write", "upload.gpx"],
     "demo": {"sandbox": true, "expires_at": "…", "base": "2026-09-28"},
     "user": null, "csrf": "<token>"}

It also issues the CSRF cookie (trc_csrf, readable by the page): writes send
it back as the X-TRC-CSRF header (double submit, backend/tenancy_mw.py).

Sign-in mode (SP-58, backend/accounts/login.py): public (the sign-in page reads it too);
signed out it says only {"mode": "signed_out", "caps": [], "user": null, "membership": null,
"login": {"required": true, "signed_in": false}}; signed in, mode "owner" | "user" and
"user": {"id", "name", "email", "role"}.

POST /api/v1/demo/reset — the demo's 「重設示範」: delete the visitor's sandbox;
the next write starts a fresh one from the demo base.
"""
from __future__ import annotations

import datetime as dt
import os
import secrets as _secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend import tenancy
from backend.tenancy_mw import CSRF_COOKIE, cookie, cookie_attrs

router = APIRouter(tags=["session"])


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _login_view() -> tuple[Optional[dict], Optional[object]]:
    """Sign-in mode (SP-58): ({required, signed_in}, the user); (None, None) when sign-in is off."""
    from backend.accounts import login as LG
    if tenancy.demo_mode() or not LG.required():
        return None, None
    u = LG.current_user()
    return {"required": True, "signed_in": u is not None}, u


def session_info(had_cookie: bool = False) -> dict:
    """{mode, caps, demo?, user}: also inlined into every page (i18n/pages.render_page).
    Sign-in mode adds `login` {required, signed_in}; signed out: mode "signed_out", no caps,
    no membership (nothing of the owner's tenant leaks to a page the gate let through);
    signed in: mode "owner" | "user" and user {id, name, email, role}."""
    login, u = _login_view()
    if login is not None and u is None:
        return {"mode": "signed_out", "caps": [], "user": None, "membership": None, "login": login}
    t = tenancy.current()
    body: dict = {"mode": "demo" if tenancy.demo_mode() else "owner",
                  "caps": sorted(t.caps), "user": None}
    if login is not None:
        body["mode"] = "owner" if u.is_owner else "user"
        body["user"] = {"id": u.id, "name": u.display_name, "email": u.email, "role": u.role}
        body["login"] = login
    # SP-330: role, plan, scopes (what the pages may show), paused data sources, the quotas left
    try:
        from backend import membership as MS
        body["membership"] = MS.session_view(t)
    except Exception:                      # noqa: BLE001 — the page still loads; the API still checks
        body["membership"] = None
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
