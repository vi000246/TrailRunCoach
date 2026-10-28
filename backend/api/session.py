"""
GET /api/v1/session — who / what this page is for (auth-and-demo plan §3.6);
the shell (static/shell.js) reads it first. Later sign-in keeps the shape:

    {"mode": "demo", "caps": ["plan.write", "upload.gpx"],
     "demo": {"sandbox": true, "expires_at": "…", "base": "2026-09-28"},
     "user": null, "csrf": "<token>"}

It also issues the CSRF cookie (trc_csrf, readable by the page; __Host-trc_csrf in sign-in mode;
an existing value is echoed only when it looks like one of ours): writes send it back as the
X-TRC-CSRF header (double submit, backend/tenancy_mw.py).

Sign-in mode (SP-58, backend/accounts/login.py): public (the sign-in page reads it too);
signed out it says only {"mode": "signed_out", "caps": [], "user": null, "membership": null,
"login": {"required": true, "signed_in": false}}; signed in, mode "owner" | "user" and
"user": {"id", "name", "email", "role"}.

GET /join — 「用自己的資料算」 (SP-124): on to sign-up, the waitlist or the public demo (public).

POST /api/v1/demo/reset — the demo's 「重設示範」: delete the visitor's sandbox;
the next write starts a fresh one from the demo base.
"""
from __future__ import annotations

import datetime as dt
import os
import re
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
        if u.is_test:
            body["user"]["test"] = True        # SP-611: a test account (the shell's strip says so)
        try:                                   # SP-790: a coach's 更多 has 教練看板 (the API still checks)
            from backend.accounts import coaching
            if coaching.is_coach(u):
                body["user"]["coach"] = True
        except Exception:                      # noqa: BLE001 — no link rather than no page
            pass
        body["login"] = login
        body.update(_coaching_view(u, bool(body["user"].get("coach"))))
    # SP-330: role, plan, scopes (what the pages may show), paused data sources, the quotas left
    try:
        from backend import membership as MS
        body["membership"] = MS.session_view(t)
    except Exception:                      # noqa: BLE001 — the page still loads; the API still checks
        body["membership"] = None
    # SP-714: the buttons cooling down now {key: seconds left} (the shell disables them on load)
    if body["membership"] is not None:
        try:
            from backend import cooldown
            body["membership"]["cooldowns"] = cooldown.active()
        except Exception:                  # noqa: BLE001 — the API still answers 429
            body["membership"]["cooldowns"] = {}
    # AI 問答 (SP-785 / SP-786): the shell's 「問 AI」 button — the owner, or a member with their own key
    # while the server allows it (SP-883, TRC_AI_MEMBER_KEYS) — while it is switched on; never to a
    # coach viewing a student; `key`: a key is set (without one the dialog says where to set it);
    # `own`: a member on their own key (SP-883) — without one the dialog asks for it and sends nothing
    try:
        from backend.ai import config as AIC
        if AIC.allowed() and not body.get("viewing"):
            body["ai"] = {"on": AIC.enabled(), "key": AIC.key_source() != "none"}
            if AIC.is_member():
                body["ai"]["own"] = True
    except Exception:                      # noqa: BLE001 — no button rather than no page
        pass
    # 站內公告 (SP-351): the admin's notice in the page's language (the other one when only it is set)
    try:
        from backend import i18n, site_config as SC
        zh, en = SC.get("notice.zh"), SC.get("notice.en")
        text = (en or zh) if i18n.current_locale() == "en" else (zh or en)
        if text:
            body["notice"] = text
    except Exception:                      # noqa: BLE001 — no notice rather than no page
        pass
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
        # 「用自己的資料算」 (SP-124): the main site's /join (https only), which sends the visitor on to
        # sign-up, the waitlist or nothing — the same entry as the share page's
        demo["cta_url"] = _https_url(os.environ.get("WKO5COACH_DEMO_CTA_URL"))
        body["demo"] = demo
    return body


COACH_EDIT_DAYS = 14


def _coaching_view(u, coach: bool = False) -> dict:
    """SP-791: `viewing` {student_id, name, perms, back} while this session views a student (the shell's
    banner, 回到我自己, the locked controls); SP-792: else `coach_edit` {coach, at, action}, the latest
    change a coach made in this person's data in the last COACH_EDIT_DAYS days (the schedule's line);
    SP-823: and for a coach, `coach_requests` {n, at} — students waiting for them to say yes (the shell's
    line on every page, so the coach doesn't have to open 設定 › 教練 to find out)."""
    out: dict = {}
    try:
        from backend.accounts import coaching as CO, store, viewing as VW
        v = VW.current()
        if v is not None:
            out["viewing"] = {"student_id": v.student.id, "name": CO._name(v.student.display_name, v.student.id),
                              "perms": dict(v.perms), "back": VW.COACH_HOME + "#coach"}
            return out
        if coach:
            try:
                req = CO.pending_requests(u)
                if req:
                    out["coach_requests"] = req
            except Exception:              # noqa: BLE001 — no line rather than no coach_edit
                pass
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=COACH_EDIT_DAYS)).replace(microsecond=0).isoformat()
        for r in store.coach_audit_of(u.id, 20, since):
            if r["action"] not in ("view_start", "view_end"):
                out["coach_edit"] = {"coach": r["coach_name"] or "", "at": r["at"], "action": r["action"]}
                break
    except Exception:                      # noqa: BLE001 — the page still loads; the gate still checks
        pass
    return out


def _https_url(raw: Optional[str]) -> Optional[str]:
    """An https address with no user info and nothing that could break out of an attribute; else None."""
    from urllib.parse import urlsplit
    raw = (raw or "").strip()
    if not raw or any(ord(ch) < 0x21 or ord(ch) == 0x7F or ch in "\"'<>`\\" for ch in raw):
        return None
    try:
        u = urlsplit(raw)
    except ValueError:
        return None
    return raw if u.scheme == "https" and u.hostname and not (u.username or u.password) else None


@router.get("/join", include_in_schema=False)
def join():
    """「用自己的資料算」 (SP-124): a public path (login.PUBLIC_PATHS), the same for every visitor —
    303 to sign-up (/login, then the race calculator), the waitlist (/login?waitlist=1) or, before
    sign-up opens, the public demo (TRC_DEMO_URL), else the 關於 page (/about, SP-124 Q2); 404 in the demo.
    Reads nobody's data and computes nothing."""
    from fastapi.responses import RedirectResponse

    from backend.accounts import login as LG
    j = LG.join_link()
    if j is None:
        raise HTTPException(404, "not found")
    return RedirectResponse(j["url"], status_code=303, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/session")
def get_session(request: Request):
    from backend.accounts import login as LG
    from backend.demo.sandbox import COOKIE
    body = session_info(bool(cookie(request.scope, COOKIE)))
    signin = not tenancy.demo_mode() and LG.required()
    name = LG.cookie_name(CSRF_COOKIE) if signin else CSRF_COOKIE    # __Host-trc_csrf in sign-in mode
    tok = cookie(request.scope, name)
    if not (tok and _CSRF_OK.fullmatch(tok)):        # only a value of our own making is echoed back
        tok = _secrets.token_urlsafe(24)
    body["csrf"] = tok
    resp = JSONResponse(body, headers={"Cache-Control": "no-store"})
    attrs = LG.cookie_attrs(30 * 86400, http_only=False) if signin else cookie_attrs(30 * 86400, http_only=False)
    resp.headers.append("set-cookie", f"{name}={tok}; {attrs}")
    return resp


_CSRF_OK = re.compile(r"[A-Za-z0-9_-]{20,64}")


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
