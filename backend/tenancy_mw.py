"""
Binds each request to its tenant (backend/tenancy.py) and, in the demo
instance (WKO5COACH_MODE=demo), enforces the demo rules (auth-and-demo
plan §3.3–§3.7). Pure ASGI (not BaseHTTPMiddleware): the contextvar it sets
reaches async endpoints and, through AnyIO's context copy, sync ones.

Owner mode: nothing changes (every request is the owner tenant).

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
from http.cookies import SimpleCookie
from typing import Optional

from backend import i18n, tenancy
from backend.i18n import _
from backend.security import ratelimit as RL

MAX_BODY = 6 * 1024 * 1024
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
             r"|steps/check|reconcile|prefs/conflicts|blackouts/preview|equivalence/design|rest-days)"),
    ("DELETE", _P + r"/rest-days/[^/]+"),
    ("PUT DELETE", _P + r"/high-nights/[^/]+"),          # 睡在高處的紀錄 (SP-259)
    # 範本 (SP-36): the visitor's own templates, categories and route GPX, in the sandbox
    ("POST", _P + r"/steps/templates/(user|user/copy|cats)"),
    ("PATCH DELETE", _P + r"/steps/templates/(user|cats)/[^/]+"),
    ("POST DELETE", _P + r"/steps/templates/user/[^/]+/gpx"),
    ("POST", _P + r"/sessions/[^/]+/save-as-template"),
    ("PUT", _P + r"/(prefs|blackouts)"),
    ("PUT", r"/api/v1/plan/(events|phases)"),
    ("DELETE", r"/api/v1/plan/events/[^/]+"),
    ("POST PUT DELETE", r"/api/v1/plan/events/[^/]+/gpx(/splits)?"),
    ("POST", r"/api/v1/racepower/(predict|course|plan|export/csv|hike-meta|solo-hikes)"),
    ("POST", r"/api/v1/racepower/course/event/[^/]+"),
    ("PUT DELETE", r"/api/v1/racepower/saved/[^/]+"),
    ("POST", r"/api/v1/expr/evaluate"),
    ("POST", r"/api/v1/demo/reset"),
]]
# writes that need no sandbox (they change nothing stored)
NO_SANDBOX = re.compile(r"/api/v1/(demo/reset|expr/evaluate|racepower/(predict|course|plan|export/csv)"
                        r"|overview/plan/(steps-preview|steps/check|blackouts/preview|prefs/conflicts))$")
HEAVY = re.compile(r"/api/v1/(racepower/(predict|course|plan)|racepower/course/event/[^/]+|expr/evaluate"
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
    raw = _headers(scope).get("cookie")
    if not raw:
        return None
    try:
        c = SimpleCookie()
        c.load(raw)
    except Exception:          # noqa: BLE001
        return None
    m = c.get(name)
    return m.value if m else None


def cookie_attrs(max_age: Optional[int], http_only: bool = True) -> str:
    """Secure unless WKO5COACH_COOKIE_SECURE=0 (local http); SameSite from
    WKO5COACH_COOKIE_SAMESITE (Lax; None + Partitioned when the page runs in
    an iframe on another site, e.g. a Hugging Face Space)."""
    same = (os.environ.get("WKO5COACH_COOKIE_SAMESITE") or "Lax").strip().capitalize()
    if same not in ("Lax", "Strict", "None"):
        same = "Lax"
    secure = os.environ.get("WKO5COACH_COOKIE_SECURE", "1") != "0" or same == "None"
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


class TenancyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not tenancy.demo_mode():
            await self.app(scope, receive, send)
            return
        # the error texts below are built before RequestContextMiddleware (inside) sets the language
        from backend.request_context import locale_for_scope
        ltok = i18n.set_locale(locale_for_scope(scope)[0])
        try:
            await self._demo(scope, receive, send)
        finally:
            i18n.reset_locale(ltok)

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
