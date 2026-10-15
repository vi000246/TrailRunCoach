"""
Binds each request to its tenant (backend/tenancy.py) and, in the demo
instance (WKO5COACH_MODE=demo), enforces the demo rules (auth-and-demo
plan §3.3–§3.7). Pure ASGI (not BaseHTTPMiddleware): the contextvar it sets
reaches async endpoints and, through AnyIO's context copy, sync ones.

Owner mode: nothing changes (every request is the owner tenant).

Sign-in mode (SP-58; backend/accounts/login.py: decided once at start-up and frozen —
WKO5COACH_ACCOUNTS=1 and the whole set of sign-in settings; with none of them it is owner mode
above), per request:
  1. a path with dot segments, an empty segment, a backslash or an encoded slash / backslash /
     dot: 400 (no prefix check is trusted with it)
  2. /share/* (public shares and the 課表 feed): /share/<id>[/data] the tenant of the user
     accounts.db's shares row names (SP-124; a disabled account's: none), anything else under
     /share/ the owner's; a real CORS preflight (Origin + Access-Control-Request-Method) passes
     with no tenant
  3. the session cookie (__Host-trc_session over https) -> its user (accounts.db, sha256 of the
     token only; the expiry slides, the cookie is renewed with it); a dead cookie is cleared
  4. no user and not a public path (login.PUBLIC_PATHS: /login, /signup, /oauth/*/callback,
     /api/v1/session, /api/v1/account/login|signup|logout, /api/v1/static/*, /healthz, and the
     legal documents /legal, /legal/<terms|privacy|health|third_party> — exact paths, SP-60):
     a page (a browser navigation) is sent to /login?next=<path>, anything else gets
     401 {"code": "LOGIN_REQUIRED"}
  5. a write (not GET / HEAD) must carry the CSRF header (the demo's double submit), public or not
     — except POST /api/v1/intervals/webhook (SP-346): intervals.icu's webhook, checked by the
     payload's secret in api/intervals.py, with no tenant (the endpoint opens the athlete's)
     — and POST /api/v1/billing/webhook (SP-328): Polar's webhook, checked by its Standard Webhooks
     signature in billing/service.py, with no tenant (it writes accounts.db only)
     — except POST /api/v1/weight/measurements with a weight token (SP-472, weight_auth.py): the
     token's user from accounts.db, its tenant, no session and no CSRF (no cookie is read: nothing
     for another site to forge); that one path only
     — and a GET / HEAD under /api/v1/debug/ with a debug token (SP-406, debug_auth.py): the
     token's user from accounts.db, the owner only (scope debug:*), the owner's tenant, no session;
     the debug API checks the token against the tenant's own row again. Nothing else opens with it
     — and everything under /api/v1/link/ (SP-329, accounts/links.py: 疊石 Cairn, server to server),
     which never reads the session cookie: POST /api/v1/link/<project>/token with no tenant (the
     endpoint checks the project's shared secret); a GET / HEAD of a route in links.ROUTES with
     `Authorization: Bearer <service token>` whose scopes hold the route's, of a user still linked
     with that project — and for a feature that asks on its own (links.FEATURES, SP-654) switched
     on by the person (403 CONSENT_REQUIRED) —, bound to that user's tenant (the token's, never one
     the caller names);
     anything else there 401 TOKEN_INVALID (429 once the IP failed too often)
  6. a user who hasn't accepted the current terms: pages go to /signup, the rest gets
     403 {"code": "TERMS_REQUIRED"} (the owner accepts none)
  7. tenant: the user's (the owner's own for user 1; a new user's folder and DB on first use),
     signed out the anonymous one (no caps; reading its data raises)
  8. an answer without Cache-Control gets "private, no-store"

Demo mode, per request:
  1. abuse limits: body ≤ 6 MB, a token bucket per client IP (client_ip:
     CF-Connecting-IP / X-Forwarded-For only from a trusted proxy)
  2. tenant: the `trc_demo` cookie's live sandbox, else the shared demo base
  3. a write (not GET / HEAD / OPTIONS) must
       - carry the CSRF header (X-TRC-CSRF == cookie trc_csrf: double submit)
       - match the write allow-list (default deny: 403 {"code": "DEMO_DISABLED"})
       - stay within the per-sandbox write rate and data-volume limits
     and the first one creates the visitor's sandbox (copy-on-write) and sets the cookie
  4. heavy computations (race calculator, chart expressions ...) share a
     small concurrency gate (503 after waiting 10 s)
Every response gets X-Robots-Tag: noindex.
"""
from __future__ import annotations

import json
import os
import re
import secrets as _secrets
from typing import Optional
from urllib.parse import urlencode

from backend import i18n, tenancy
from backend.i18n import _
from backend.security import ratelimit as RL
from starlette.concurrency import run_in_threadpool
from starlette.requests import cookie_parser

MAX_BODY = 6 * 1024 * 1024
WEIGHT_INGEST = "/api/v1/weight/measurements"   # SP-472: the one path a weight token opens (sign-in mode)
DEBUG_PREFIX = "/api/v1/debug/"                 # SP-406: the paths a debug token opens (sign-in mode, GET / HEAD)
INTERVALS_WEBHOOK = "/api/v1/intervals/webhook"  # SP-346: intervals.icu's webhook (its own secret, no session)
BILLING_WEBHOOK = "/api/v1/billing/webhook"      # SP-328: Polar's webhook (its own signature, no session)
LINK_PREFIX = "/api/v1/link/"                   # SP-329: another project's calls (accounts/links.py)
_LINK_TOKEN = re.compile(r"/api/v1/link/[a-z]+/token$")
CSRF_COOKIE = "trc_csrf"
CSRF_HEADER = b"x-trc-csrf"

# (methods, path regex): the only writes the demo accepts (§3.5). Default deny.
_P = "/api/v1/overview/plan"
WRITE_ALLOW: list[tuple[frozenset, re.Pattern]] = [(frozenset(m.split()), re.compile(p + r"$")) for m, p in [
    ("POST", _P + r"/sessions"),
    ("PATCH DELETE", _P + r"/sessions/[^/]+"),
    ("POST DELETE", _P + r"/sessions/[^/]+/link"),
    ("POST", _P + r"/sessions/expired/delete"),
    ("POST", _P + r"/(test-suggestions/schedule|suggestions/dismiss|suggestions/accept|steps-preview|steps/derive"
             r"|steps/check|steps/route|reconcile|prefs/conflicts|blackouts/preview|equivalence/design|rest-days)"),
    ("DELETE", _P + r"/rest-days/[^/]+"),
    # 範本 (SP-36): the visitor's own templates, categories and route GPX, in the sandbox
    ("POST", _P + r"/steps/templates/(user|user/copy|cats)"),
    ("PATCH DELETE", _P + r"/steps/templates/(user|cats)/[^/]+"),
    ("POST DELETE", _P + r"/steps/templates/user/[^/]+/gpx"),
    # 課表 › 路線 (SP-640): the visitor's own routes, in the sandbox
    ("POST", _P + r"/routes/library"),
    ("PATCH DELETE", _P + r"/routes/library/[^/]+"),
    ("POST", _P + r"/sessions/[^/]+/save-as-template"),
    ("PUT", _P + r"/(prefs|blackouts)"),
    ("PUT", r"/api/v1/plan/(events|phases)"),
    ("DELETE", r"/api/v1/plan/events/[^/]+"),
    ("POST PUT DELETE", r"/api/v1/plan/events/[^/]+/gpx(/splits)?"),
    ("POST", r"/api/v1/racepower/(predict|course|plan|hike-meta|solo-hikes)"),
    ("POST", r"/api/v1/racepower/course/event/[^/]+"),
    ("PUT DELETE", r"/api/v1/racepower/saved/[^/]+"),
    ("POST", r"/api/v1/demo/reset"),
]]
# writes that need no sandbox (they change nothing stored)
NO_SANDBOX = re.compile(r"/api/v1/(demo/reset|racepower/(predict|course|plan)"
                        r"|overview/plan/(steps-preview|steps/check|steps/route|blackouts/preview|prefs/conflicts))$")
HEAVY = re.compile(r"/api/v1/(racepower/(predict|course|plan)|racepower/course/event/[^/]+"
                   r"|overview/plan/(equivalence/design|reconcile|steps-preview))$")

# limits (§3.7); module level so tests can shrink them
REQ_BUCKET = RL.Buckets(rate=300, per=60)                # every request, per IP
CREATE_HOUR = RL.Buckets(rate=3, per=3600)              # new sandboxes per IP
CREATE_DAY = RL.Buckets(rate=20, per=86400)
WRITE_MIN = RL.Buckets(rate=60, per=60)                 # per sandbox
HEAVY_IP = RL.Buckets(rate=20, per=60)                  # heavy computations per IP
WRITES_PER_DAY = 2000
MAX_SESSIONS, MAX_EVENTS, MAX_PHASES, MAX_TEXT = 400, 20, 30, 200
TEXT_FIELDS = ("note", "name", "title", "label", "description", "reason", "swap_reason")
HEAVY_GATE = RL.Gate(n=3, wait_s=10.0)


def allowed_write(method: str, path: str) -> bool:
    return any(method in ms and rx.match(path) for ms, rx in WRITE_ALLOW)


def _headers(scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers") or []:
        name = k.decode("latin-1").lower()
        val = v.decode("latin-1")
        out[name] = f"{out[name]}; {val}" if name == "cookie" and name in out else val
    return out


def cookie(scope, name: str) -> Optional[str]:
    """A cookie's value. Starlette's lenient parser (what request.cookies uses): one malformed
    pair in the header never hides the others, as SimpleCookie's all-or-nothing load did (SP-58)."""
    raw = _headers(scope).get("cookie")
    if not raw:
        return None
    try:
        return cookie_parser(raw).get(name)
    except Exception:          # noqa: BLE001
        return None


def cookie_attrs(max_age: Optional[int], http_only: bool = True, same_site: Optional[str] = None,
                 secure: Optional[bool] = None) -> str:
    """Secure unless WKO5COACH_COOKIE_SECURE=0 (local http); SameSite from
    WKO5COACH_COOKIE_SAMESITE (Lax; None + Partitioned when the page runs in
    an iframe on another site, e.g. a Hugging Face Space). `same_site` / `secure` fix
    them whatever the env says (the sign-in cookies: Lax, and Secure over https, SP-58)."""
    same = (same_site or os.environ.get("WKO5COACH_COOKIE_SAMESITE") or "Lax").strip().capitalize()
    if same not in ("Lax", "Strict", "None"):
        same = "Lax"
    if secure is None:
        secure = os.environ.get("WKO5COACH_COOKIE_SECURE", "1") != "0"
    secure = secure or same == "None"
    parts = ["Path=/", f"SameSite={same}"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    if same == "None":
        parts.append("Partitioned")
    return "; ".join(parts)


async def _json_response(send, status: int, body: dict, extra: Optional[list] = None) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8"),
               (b"content-length", str(len(data)).encode()), (b"x-robots-tag", b"noindex"),
               (b"cache-control", b"no-store")] + (extra or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})


def _demo_error(code: str, message: str) -> dict:
    return {"code": code, "detail": {"code": code, "message": message}}


async def _redirect(send, location: str, extra: Optional[list] = None) -> None:
    headers = [(b"location", location.encode("latin-1")), (b"content-length", b"0"),
               (b"cache-control", b"no-store")] + (extra or [])
    await send({"type": "http.response.start", "status": 302, "headers": headers})
    await send({"type": "http.response.body", "body": b""})


def bad_path(scope) -> bool:
    """Sign-in mode: a path the public prefixes could be fooled with — dot segments, an empty
    segment, a backslash, or an encoded slash / backslash (%2f, %5c) — is refused outright (400)
    instead of trusting the router and the prefix checks to agree on what it means (SP-58)."""
    path = scope.get("path", "") or ""
    raw = (scope.get("raw_path") or path.encode("utf-8", "replace")).lower()
    if b"%2f" in raw or b"%5c" in raw or b"%2e" in raw or "\\" in path or "//" in path:
        return True
    segs = path.split("/")[1:]
    return any(s in (".", "..") for s in segs)


def _target(scope) -> str:
    qs = (scope.get("query_string") or b"").decode("latin-1")
    return scope.get("path", "") + ("?" + qs if qs else "")


def _is_preflight(scope) -> bool:
    h = _headers(scope)
    return bool(h.get("origin") and h.get("access-control-request-method"))


def _wants_page(scope) -> bool:
    """A browser opening a page (sent to the sign-in page), not a script's request (401)."""
    h = _headers(scope)
    return ("text/html" in (h.get("accept") or "") or h.get("sec-fetch-dest") == "document"
            or h.get("sec-fetch-mode") == "navigate")


def csrf_ok(scope, cookie_name: str = CSRF_COOKIE) -> bool:
    """Double submit: the X-TRC-CSRF header equals the CSRF cookie (a cross-site form or
    request can send the cookie, never read it into a header)."""
    hdr = _headers(scope).get(CSRF_HEADER.decode())
    ck = cookie(scope, cookie_name)
    # bytes: compare_digest refuses non-ASCII str (a forged header would be a 500, not a 403)
    return bool(hdr and ck and _secrets.compare_digest(hdr.encode("utf-8", "replace"), ck.encode("utf-8", "replace")))


async def _read_body(receive) -> tuple[Optional[bytes], list]:
    """The whole request body (None when over MAX_BODY) and the messages to replay."""
    chunks, msgs, n = [], [], 0
    while True:
        m = await receive()
        msgs.append(m)
        if m["type"] != "http.request":
            break
        b = m.get("body", b"")
        n += len(b)
        if n > MAX_BODY:
            return None, msgs
        chunks.append(b)
        if not m.get("more_body"):
            break
    return b"".join(chunks), msgs


def _long_text(obj, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in TEXT_FIELDS and len(v) > MAX_TEXT:
                return True
            if isinstance(v, (dict, list)) and _long_text(v, depth + 1):
                return True
    elif isinstance(obj, list):
        return any(_long_text(v, depth + 1) for v in obj[:500])
    return False


def _volume_error(method: str, path: str, body: bytes, t: Optional[tenancy.Tenant]) -> Optional[str]:
    """§3.7 data-volume limits of a sandbox; None when fine."""
    ctype_json = body[:1] in (b"{", b"[")
    data = None
    if ctype_json:
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            data = None
    if data is not None and _long_text(data):
        return _("文字欄位最多 {n} 字", n=MAX_TEXT)
    if path == "/api/v1/plan/phases" and isinstance(data, list) and len(data) > MAX_PHASES:
        return _("示範模式最多 {n} 個階段", n=MAX_PHASES)
    if t is None:
        return None
    if path == "/api/v1/plan/events" and method == "PUT":
        with tenancy.use(t):
            from backend.engine.planning import Plan
            plan = Plan.load()
        eid = (data or {}).get("id") if isinstance(data, dict) else None
        if len(plan.events) >= MAX_EVENTS and not any(e.id == eid for e in plan.events):
            return _("示範模式最多 {n} 個賽事", n=MAX_EVENTS)
    if path == "/api/v1/overview/plan/sessions" and method == "POST":
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{(t.root / 'wko5coach.db').as_posix()}?mode=ro", uri=True)
            try:
                n = con.execute("SELECT count(*) FROM plan_sessions").fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error:
            n = 0
        if n >= MAX_SESSIONS:
            return _("示範模式最多 {n} 堂課", n=MAX_SESSIONS)
    return None


_SHARE_PATH = re.compile(r"/share/([A-Za-z0-9_-]{16,64})(?:/data)?")


def _share_tenant(path: str) -> tenancy.Tenant:
    """The tenant a public /share/… request reads (sign-in mode, SP-124): /share/<id> and
    /share/<id>/data by accounts.db's shares row; everything else under /share/ the owner's."""
    m = _SHARE_PATH.fullmatch(path)
    if m is None:
        return tenancy.owner()
    from backend.engine.racepower import share as SH
    if SH.is_example(m.group(1)):          # the 介紹頁's built-in examples (SP-622, SP-637): repo files, no one's tenant
        return tenancy.anonymous()
    from backend.accounts import store, tenants as TN
    u = store.share_owner(m.group(1))
    if u is None or u.is_owner:
        return tenancy.owner()
    if u.disabled:
        return tenancy.anonymous()
    return TN.tenant_of(u)


class TenancyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        demo = tenancy.demo_mode()
        signin = False
        if not demo and kind in ("http", "websocket"):
            from backend.accounts import login as LG
            # the mode is frozen at start-up (main.lifespan); decided here only if that never ran.
            # A half-done configuration raises here too: every request fails, none is the owner's
            signin = (LG.mode() or LG.decide()) == LG.SIGNIN
        if signin and kind == "websocket":            # none exist; never one past the gate
            await send({"type": "websocket.close", "code": 1008})
            return
        if kind != "http" or not (demo or signin):
            await self.app(scope, receive, send)
            return
        # the error texts below are built before RequestContextMiddleware (inside) sets the language
        from backend.request_context import locale_for_scope
        ltok = i18n.set_locale(locale_for_scope(scope)[0])
        try:
            if demo:
                await self._demo(scope, receive, send)
            else:
                await self._signed_in(scope, receive, send)
        finally:
            i18n.reset_locale(ltok)

    async def _signed_in(self, scope, receive, send):
        """Sign-in mode (SP-58): the module docstring's steps."""
        from backend.accounts import login as LG, store, tenants as TN
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if bad_path(scope):
            await _json_response(send, 400, _demo_error("BAD_PATH", "bad path"))
            return
        if path == "/share" or path.startswith("/share/"):
            # public shares and the 課表 feed (SP-124): a share whose row in accounts.db names a user
            # opens that user's tenant (only its racepower_shares/<id>.json is read there); no row —
            # the owner's shares from before, and the 課表 feed — the owner's; a disabled account's
            # share no tenant at all (its snapshot reads as gone)
            try:
                share_tenant = await run_in_threadpool(_share_tenant, path)
            except Exception as e:                 # noqa: BLE001 — accounts.db unreadable: no one's share opens
                import logging
                logging.getLogger(__name__).warning("share lookup failed: %s", type(e).__name__)
                await _json_response(send, 503, _demo_error("UNAVAILABLE", _("暫時無法確認登入狀態，請稍後再試")),
                                     [(b"retry-after", b"10")])
                return
            with tenancy.use(share_tenant):
                await self.app(scope, receive, send)
            return
        if method == "OPTIONS" and _is_preflight(scope):        # a CORS preflight carries no cookie
            with tenancy.use(tenancy.anonymous()):
                await self.app(scope, receive, send)
            return
        if method == "POST" and path == WEIGHT_INGEST:
            from backend import weight_auth as WA
            wtok = WA.token_from_headers(_headers(scope))
            if wtok is not None:                   # SP-472: the weight API's own token, no session
                await self._weight_token(scope, receive, send, wtok)
                return
        if method == "POST" and path in (INTERVALS_WEBHOOK, BILLING_WEBHOOK):
            # SP-346: intervals.icu posts with the payload's secret, never a cookie or the CSRF header;
            # no tenant here — the endpoint checks the secret and opens the tenant the athlete maps to.
            # SP-328: Polar likewise, with its signature (the endpoint writes accounts.db only)
            with tenancy.use(tenancy.anonymous()):
                await self.app(scope, receive, send)
            return
        if method in ("GET", "HEAD") and path.startswith(DEBUG_PREFIX):
            from backend import debug_auth as DA
            dtok = DA.token_from_headers(_headers(scope))
            if dtok is not None:                   # SP-406: the debug API's own token, no session
                await self._debug_token(scope, receive, send, dtok)
                return
        if path.startswith(LINK_PREFIX):
            # SP-329: another project of ours (疊石 Cairn), server to server; never a session or a cookie
            await self._link(scope, receive, send, method, path)
            return
        session_cookie = LG.cookie_name(LG.SESSION_COOKIE)
        token = cookie(scope, session_cookie)
        try:
            found = await run_in_threadpool(store.session_lookup, token) if token else None
        except Exception as e:                     # noqa: BLE001 — accounts.db unreadable: nobody is let in
            import logging
            logging.getLogger(__name__).warning("session lookup failed: %s", type(e).__name__)
            await _json_response(send, 503, _demo_error("UNAVAILABLE", _("暫時無法確認登入狀態，請稍後再試")),
                                 [(b"retry-after", b"10")])
            return
        user = found[0] if found else None
        set_cookies: list[bytes] = []
        if token and found is None:                # expired, signed out elsewhere, or disabled
            set_cookies.append(f"{session_cookie}=; {LG.cookie_attrs(0)}".encode())
        extra = [(b"set-cookie", c) for c in set_cookies]
        public = LG.is_public(path)
        if user is None and not public:
            if method in ("GET", "HEAD") and _wants_page(scope):
                await _redirect(send, "/login?" + urlencode({"next": LG.safe_next(_target(scope))}), extra)
                return
            body = _demo_error("LOGIN_REQUIRED", _("請先登入"))
            body["login"] = "/login"
            await _json_response(send, 401, body, extra)
            return
        if method not in ("GET", "HEAD") and not csrf_ok(scope, LG.cookie_name(CSRF_COOKIE)):
            await _json_response(send, 403, _demo_error("CSRF", _("請重新整理頁面後再試一次")), extra)
            return
        if user is not None and not public and not await run_in_threadpool(LG.consent_current, user):
            # new terms: accepted on /signup before anything else opens (the owner accepts none)
            if method in ("GET", "HEAD") and _wants_page(scope):
                await _redirect(send, "/signup?" + urlencode({"next": LG.safe_next(_target(scope))}), extra)
                return
            body = _demo_error("TERMS_REQUIRED", _("條款已更新，請先閱讀並同意"))
            body["signup"] = "/signup"
            await _json_response(send, 403, body, extra)
            return
        if found is not None and found[1]:          # the expiry slid: so does the cookie
            set_cookies.append(f"{session_cookie}={token}; {LG.cookie_attrs(found[1])}".encode())
        tenant = TN.tenant_of(user) if user is not None else tenancy.anonymous()
        if user is not None:
            await TN.ensure_ready(tenant)

        mine = f"{session_cookie}=".encode()

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                # the route set the session cookie itself (signed in, out): its cookie wins, ours
                # (clearing a dead one, renewing a slid one) would come after it and replace it
                if set_cookies and not any(k.lower() == b"set-cookie" and v.startswith(mine) for k, v in headers):
                    headers.extend((b"set-cookie", c) for c in set_cookies)
                # one person's answers: never kept by a shared cache (the static files say no-cache)
                if not any(k.lower() == b"cache-control" for k, _v in headers):
                    headers.append((b"cache-control", b"private, no-store"))
                message = {**message, "headers": headers}
            await send(message)

        ttok = tenancy.set_current(tenant)
        utok = LG.set_user(user)
        scope.setdefault("state", {})["tenant"] = tenant
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            LG.reset_user(utok)
            tenancy.reset(ttok)

    async def _weight_token(self, scope, receive, send, token: str):
        """Sign-in mode, POST /api/v1/weight/measurements with a weight token (SP-472,
        weight_auth.py): the token's user from accounts.db (scope weight:write), bound to its tenant;
        no session and no CSRF header (nothing here reads a cookie, so another site cannot forge
        it). The endpoint checks the token against the tenant's own row again. 401 unknown, 429 once
        the IP failed too often, 403 TERMS_REQUIRED for a user who has not accepted new terms."""
        from backend import weight_auth as WA
        from backend.accounts import login as LG, store, tenants as TN
        ip = RL.client_ip(scope)
        try:
            got = await run_in_threadpool(store.service_token, token)
        except Exception as e:                     # noqa: BLE001 — accounts.db unreadable
            import logging
            logging.getLogger(__name__).warning("weight token lookup failed: %s", type(e).__name__)
            await _json_response(send, 503, {"detail": {"code": "UNAVAILABLE"}}, [(b"retry-after", b"10")])
            return
        if got is None or WA.SCOPE not in got[1]:
            st = WA.failed(ip)
            await _json_response(send, st, {"detail": {"code": "BLOCKED" if st == 429 else "TOKEN_INVALID"}},
                                 [(b"retry-after", str(WA.FAIL_BLOCK_S).encode())] if st == 429
                                 else [(b"www-authenticate", b"Bearer")])
            return
        user = got[0]
        if not await run_in_threadpool(LG.consent_current, user):
            await _json_response(send, 403, _demo_error("TERMS_REQUIRED", _("條款已更新，請先閱讀並同意")))
            return
        tenant = TN.tenant_of(user)
        await TN.ensure_ready(tenant)
        ttok = tenancy.set_current(tenant)
        utok = LG.set_user(user)
        scope.setdefault("state", {})["tenant"] = tenant
        try:
            await self.app(scope, receive, send)
        finally:
            LG.reset_user(utok)
            tenancy.reset(ttok)

    async def _link(self, scope, receive, send, method: str, path: str):
        """Sign-in mode, /api/v1/link/* (SP-329, accounts/links.py): the module docstring's step 5."""
        from backend.accounts import links as LK, login as LG, store, tenants as TN
        if method == "POST" and _LINK_TOKEN.match(path):
            with tenancy.use(tenancy.anonymous()):             # the endpoint checks the shared secret
                await self.app(scope, receive, send)
            return
        ip = RL.client_ip(scope)

        async def refuse(status: int, code: str, **extra_body):
            st = LK.failed(ip) if status == 401 else status
            if st == 429:
                await _json_response(send, 429, {"detail": {"code": "BLOCKED"}},
                                     [(b"retry-after", str(LK.FAIL_BLOCK_S).encode())])
            else:
                await _json_response(send, st, {"detail": {"code": code, **extra_body}},
                                     [(b"www-authenticate", b"Bearer")] if st == 401 else None)

        need = LK.ROUTES.get(path)
        tok = LK.service_bearer(_headers(scope))
        if need is None or method not in ("GET", "HEAD") or tok is None:
            await refuse(401, "TOKEN_INVALID")
            return
        try:
            got = await run_in_threadpool(store.service_token_full, tok)
        except Exception as e:                     # noqa: BLE001 — accounts.db unreadable
            import logging
            logging.getLogger(__name__).warning("link token lookup failed: %s", type(e).__name__)
            await _json_response(send, 503, {"detail": {"code": "UNAVAILABLE"}}, [(b"retry-after", b"10")])
            return
        if got is None:
            await refuse(401, "TOKEN_INVALID")
            return
        user, granted, client_id = got
        c = LK.client(client_id or "")
        if c is None:                               # a weight / debug token: not for these routes
            await refuse(401, "TOKEN_INVALID")
            return
        if need not in granted:
            await refuse(403, "SCOPE_REQUIRED", scope=need)
            return
        if not await run_in_threadpool(LK.is_linked, user, c):
            await refuse(403, "LINK_REVOKED")
            return
        if need in LK.FEATURES and not await run_in_threadpool(LK.allowed, user, c, need):
            # SP-654: a feature the person allows on its own, switched off (or never on)
            await refuse(403, "CONSENT_REQUIRED", scope=need, text=_(LK.SCOPE_TEXT[need]),
                         link_url=LK.link_url(c, need))
            return
        if not await run_in_threadpool(LG.consent_current, user):
            await refuse(403, "TERMS_REQUIRED")
            return
        tenant = TN.tenant_of(user)
        await TN.ensure_ready(tenant)
        ttok = tenancy.set_current(tenant)
        utok = LG.set_user(user)
        st = scope.setdefault("state", {})
        st["tenant"], st["link_client"], st["link_scopes"] = tenant, c.id, granted

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(k.lower() == b"cache-control" for k, _v in headers):
                    headers.append((b"cache-control", b"private, no-store"))
                message = {**message, "headers": headers}
            await send(message)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            LG.reset_user(utok)
            tenancy.reset(ttok)

    async def _debug_token(self, scope, receive, send, token: str):
        """Sign-in mode, GET / HEAD under /api/v1/debug/ with a debug token (SP-406, debug_auth.py):
        the token's user from accounts.db (scope debug:*) — the owner only —, the owner's tenant;
        no session (no cookie is read). The debug API then checks the token against the tenant's
        own row (expiry, revocation, scopes, enabled). 401 unknown / not the owner's, 429 once the
        IP failed too often."""
        from backend import debug_auth as DA
        from backend.accounts import login as LG, store, tenants as TN
        ip = RL.client_ip(scope)
        try:
            got = await run_in_threadpool(store.service_token, token)
        except Exception as e:                     # noqa: BLE001 — accounts.db unreadable
            import logging
            logging.getLogger(__name__).warning("debug token lookup failed: %s", type(e).__name__)
            await _json_response(send, 503, {"detail": {"code": "UNAVAILABLE"}}, [(b"retry-after", b"10")])
            return
        if got is None or DA.SCOPE_ALL not in got[1] or not (got[0].is_owner or int(got[0].id) == 1):
            st = DA.failed_at_gate(ip)
            await _json_response(send, st, {"detail": {"code": "BLOCKED" if st == 429 else "TOKEN_INVALID"}},
                                 [(b"retry-after", str(DA.FAIL_BLOCK_S).encode())] if st == 429
                                 else [(b"www-authenticate", b"Bearer")])
            return
        user = got[0]
        tenant = TN.tenant_of(user)
        await TN.ensure_ready(tenant)
        ttok = tenancy.set_current(tenant)
        utok = LG.set_user(user)
        scope.setdefault("state", {})["tenant"] = tenant

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(k.lower() == b"cache-control" for k, _v in headers):
                    headers.append((b"cache-control", b"private, no-store"))
                message = {**message, "headers": headers}
            await send(message)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            LG.reset_user(utok)
            tenancy.reset(ttok)

    async def _demo(self, scope, receive, send):
        from backend.demo import sandbox as SB
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        ip = RL.client_ip(scope)
        if not REQ_BUCKET.take(ip):
            await _json_response(send, 429, _demo_error("RATE_LIMITED", _("請求太頻繁，請稍後再試")),
                                 [(b"retry-after", b"30")])
            return
        try:
            cl = int(_headers(scope).get("content-length") or 0)
        except ValueError:
            cl = 0
        if cl > MAX_BODY:
            await _json_response(send, 413, _demo_error("TOO_LARGE", _("上傳的資料太大")))
            return
        try:
            base_dir = SB.current_base_dir()
        except SB.DemoNotReady:
            await _json_response(send, 503, _demo_error("DEMO_NOT_READY", _("示範資料準備中，請稍候")))
            return
        found = SB.lookup(cookie(scope, SB.COOKIE))
        tenant = found[0] if found else tenancy.demo_base(base_dir)
        set_cookies: list[bytes] = []
        if found is None and cookie(scope, SB.COOKIE):
            # expired / old base: forget it (the shell shows 「示範資料已更新」 from /session)
            set_cookies.append(f"{SB.COOKIE}=; {cookie_attrs(0)}".encode())

        write = method not in ("GET", "HEAD", "OPTIONS")
        heavy = False
        if write:
            hdr = _headers(scope).get(CSRF_HEADER.decode())
            ck = cookie(scope, CSRF_COOKIE)
            if not hdr or not ck or not _secrets.compare_digest(hdr, ck):
                await _json_response(send, 403, _demo_error("CSRF", _("請重新整理頁面後再試一次")))
                return
            if not allowed_write(method, path):
                await _json_response(send, 403, _demo_error("DEMO_DISABLED", _("示範模式不提供這個功能")))
                return
            body, msgs = await _read_body(receive)
            if body is None:
                await _json_response(send, 413, _demo_error("TOO_LARGE", _("上傳的資料太大")))
                return
            heavy = bool(HEAVY.match(path))
            if heavy and not HEAVY_IP.take(ip):
                await _json_response(send, 429, _demo_error("RATE_LIMITED", _("計算太頻繁，請稍後再試")),
                                     [(b"retry-after", b"30")])
                return
            if not NO_SANDBOX.match(path):
                if found is None:
                    if not (CREATE_HOUR.take(ip) and CREATE_DAY.take(ip)):
                        await _json_response(send, 429, _demo_error(
                            "RATE_LIMITED", _("建立示範沙盒的次數太多，請稍後再試；現在仍可瀏覽示範資料")))
                        return
                    value = _secrets.token_urlsafe(32)
                    tenant, meta = SB.create(value)
                    found = (tenant, meta)
                    set_cookies.append(f"{SB.COOKIE}={value}; {cookie_attrs(SB.TTL_S)}".encode())
                meta = found[1]
                if int(meta.get("writes", 0)) >= WRITES_PER_DAY or not WRITE_MIN.take(tenant.id):
                    await _json_response(send, 429, _demo_error("RATE_LIMITED", _("修改太頻繁，請稍後再試")))
                    return
                err = _volume_error(method, path, body, tenant)
                if err:
                    await _json_response(send, 400, _demo_error("DEMO_LIMIT", err))
                    return
                SB.touch_write(tenant)
            else:
                err = _volume_error(method, path, body, None)
                if err:
                    await _json_response(send, 400, _demo_error("DEMO_LIMIT", err))
                    return
            pending = list(msgs)

            async def replay():
                if pending:
                    return pending.pop(0)
                return await receive()
            receive_ = replay
        else:
            receive_ = receive

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-robots-tag", b"noindex"))
                for c in set_cookies:
                    headers.append((b"set-cookie", c))
                message = {**message, "headers": headers}
            await send(message)

        if heavy and not await HEAVY_GATE.acquire():
            await _json_response(send, 503, _demo_error("BUSY", _("示範伺服器忙碌中，請稍後再試")),
                                 [(b"retry-after", b"10")])
            return
        tok = tenancy.set_current(tenant)
        scope.setdefault("state", {})["tenant"] = tenant
        try:
            await self.app(scope, receive_, send_wrapper)
        finally:
            tenancy.reset(tok)
            if heavy:
                HEAVY_GATE.release()
