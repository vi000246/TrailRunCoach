"""
Per-request context: the one middleware that decides who/what a request is
for and puts it in contextvars for the engine. Today: the language
(backend/i18n). The tenant context of auth-and-demo.plan.md §2 goes here too.

Language, first that applies (docs/plans/i18n.plan.md §2.7):
  1. ?lang=xx            — also remembered in the `lang` cookie (shared links can pick a language)
  2. cookie `lang`       — the shell's 中文／EN switch writes it
  3. the account setting — ACCOUNT_LOCALE hook (no accounts yet: returns None)
  4. Accept-Language     — by q value, the first supported; a browser that asks
                           only for unsupported languages gets en
  5. the instance default (TRC_DEFAULT_LOCALE, zh-TW unless set)

Responses carry Content-Language and `Vary: Cookie, Accept-Language`, so a
reverse proxy never serves one language's page to another.
"""
from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Callable, Optional
from urllib.parse import parse_qs

from backend import i18n

COOKIE = "lang"
COOKIE_MAX_AGE = 365 * 24 * 3600

# (scope) -> locale tag or None. Accounts (auth-and-demo phase 2) plug in here.
ACCOUNT_LOCALE: Callable[[dict], Optional[str]] = lambda scope: None


def parse_accept_language(header: Optional[str]) -> list[tuple[str, float]]:
    """'en-GB,en;q=0.8,zh;q=0.5' -> [(tag, q)] by q (stable for equal q), q=0 dropped."""
    out = []
    for i, part in enumerate((header or "").split(",")):
        bits = part.strip().split(";")
        tag = bits[0].strip()
        if not tag:
            continue
        q = 1.0
        for b in bits[1:]:
            b = b.strip()
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        if q > 0:
            out.append((tag, q, i))
    out.sort(key=lambda t: (-t[1], t[2]))
    return [(t, q) for t, q, _i in out]


def from_accept_language(header: Optional[str]) -> Optional[str]:
    """The first supported language the browser asks for; en when it only
    asks for unsupported ones; None when it asks for nothing (or only *)."""
    tags = [t for t, _q in parse_accept_language(header)]
    for t in tags:
        loc = i18n.normalize(t)
        if loc:
            return loc
    return i18n.FALLBACK_FOREIGN if any(t != "*" for t in tags) else None


def resolve(query_lang: Optional[str] = None, cookie_lang: Optional[str] = None,
            account_lang: Optional[str] = None, accept_language: Optional[str] = None) -> tuple[str, str]:
    """(locale, where it came from: query | cookie | account | header | default)."""
    if query_lang:
        return i18n.normalize(query_lang) or i18n.FALLBACK_FOREIGN, "query"
    loc = i18n.normalize(cookie_lang)
    if loc:
        return loc, "cookie"
    loc = i18n.normalize(account_lang)
    if loc:
        return loc, "account"
    loc = from_accept_language(accept_language)
    if loc:
        return loc, "header"
    return i18n.instance_default(), "default"


def _headers(scope) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers") or []:
        name = k.decode("latin-1").lower()
        val = v.decode("latin-1")
        out[name] = f"{out[name]}; {val}" if name == "cookie" and name in out else val
    return out


def _cookie(header: Optional[str], name: str) -> Optional[str]:
    if not header:
        return None
    try:
        c = SimpleCookie()
        c.load(header)
    except Exception:          # noqa: BLE001 — a malformed cookie header is just no cookie
        return None
    m = c.get(name)
    return m.value if m else None


def locale_for_scope(scope) -> tuple[str, str, dict]:
    h = _headers(scope)
    qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    query_lang = (qs.get("lang") or [None])[0]
    try:
        account = ACCOUNT_LOCALE(scope)
    except Exception:          # noqa: BLE001
        account = None
    loc, src = resolve(query_lang, _cookie(h.get("cookie"), COOKIE), account, h.get("accept-language"))
    debug = (qs.get("i18n_debug") or ["0"])[0] not in ("", "0", "false")
    return loc, src, {"debug": debug}


class RequestContextMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware): the contextvars it sets are seen by
    async endpoints and, through the threadpool's context copy, by sync ones."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        loc, src, extra = locale_for_scope(scope)
        tok = i18n.set_locale(loc)
        dtok = i18n.set_debug(extra["debug"])
        scope.setdefault("state", {})["locale"] = loc

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])]
                names = {k.lower() for k, _v in headers}
                if b"content-language" not in names:
                    headers.append((b"content-language", loc.encode()))
                vary = [v for k, v in headers if k.lower() == b"vary"]
                want = ["Cookie", "Accept-Language"]
                if vary:
                    have = {p.strip().lower() for v in vary for p in v.decode("latin-1").split(",")}
                    add = [w for w in want if w.lower() not in have]
                    if add:
                        headers = [(k, v) for k, v in headers if k.lower() != b"vary"]
                        headers.append((b"vary", (", ".join([vary[0].decode("latin-1")] + add)).encode()))
                else:
                    headers.append((b"vary", b"Cookie, Accept-Language"))
                if src == "query":
                    headers.append((b"set-cookie",
                                    f"{COOKIE}={loc}; Max-Age={COOKIE_MAX_AGE}; Path=/; SameSite=Lax".encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            i18n._debug.reset(dtok)
            i18n.reset_locale(tok)
