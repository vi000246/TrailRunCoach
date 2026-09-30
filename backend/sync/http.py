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


def client(**kw) -> httpx.AsyncClient:
    if _TRANSPORT is not None:
        kw.setdefault("transport", _TRANSPORT)
    return httpx.AsyncClient(**kw)


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
