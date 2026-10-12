"""
Shared HTTP plumbing for the sync clients.

Every outgoing request goes through `client()`, so tests can swap the
transport for an `httpx.MockTransport` (no real network) with
`use_transport(...)`. Production code never sets a transport.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

import httpx

_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None


def _is_coros(request: httpx.Request) -> bool:
    from backend.sync import coros_limits as CL
    host = request.url.host or ""
    return host == CL.HOST_SUFFIX or host.endswith("." + CL.HOST_SUFFIX)


async def _coros_before(request: httpx.Request) -> None:
    """Requests to *.coros.com go through the server-wide COROS limits (sync/coros_limits.py,
    SP-385): the switch / pauses and the rate limit before sending…"""
    if _is_coros(request):
        from backend.sync import coros_limits as CL
        await CL.before_request(request.url.host)


async def _coros_after(response: httpx.Response) -> None:
    """…and the 429 count after (raises CorosBlocked: no retry loop)."""
    if _is_coros(response.request):
        from backend.sync import coros_limits as CL
        CL.after_response(response.status_code, login=response.request.url.path.endswith("/account/login"))


def client(**kw) -> httpx.AsyncClient:
    if _TRANSPORT is not None:
        kw.setdefault("transport", _TRANSPORT)
    hooks = dict(kw.pop("event_hooks", None) or {})
    hooks["request"] = [_coros_before, *hooks.get("request", [])]
    hooks["response"] = [_coros_after, *hooks.get("response", [])]
    return httpx.AsyncClient(event_hooks=hooks, **kw)


@contextmanager
def use_transport(transport: httpx.AsyncBaseTransport) -> Iterator[None]:
    global _TRANSPORT
    prev, _TRANSPORT = _TRANSPORT, transport
    try:
        yield
    finally:
        _TRANSPORT = prev


def as_utc(t: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands back naive datetimes; everything we store is UTC."""
    if t is None:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
