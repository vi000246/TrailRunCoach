"""
In-process abuse limits (auth-and-demo.plan.md §3.7). One uvicorn worker, so
plain dicts are enough; nothing is stored on disk and no IP is logged.

    b = Buckets(rate=60, per=60, burst=60)    # 60 / minute
    b.take(key)                               # False when empty
    client_ip(scope)                          # CF-Connecting-IP only from a trusted proxy
"""
from __future__ import annotations

import ipaddress
import os
import threading
import time
from typing import Callable, Optional

clock: Callable[[], float] = time.monotonic       # tests replace


class Buckets:
    """Token bucket per key: `rate` tokens per `per` seconds, at most `burst`."""

    def __init__(self, rate: float, per: float, burst: Optional[float] = None, max_keys: int = 50_000):
        self.rate = float(rate) / float(per)
        self.burst = float(burst if burst is not None else rate)
        self.max_keys = max_keys
        self._b: dict = {}
        self._lock = threading.Lock()

    def take(self, key: str, n: float = 1.0) -> bool:
        t = clock()
        with self._lock:
            tokens, last = self._b.get(key, (self.burst, t))
            tokens = min(self.burst, tokens + (t - last) * self.rate)
            ok = tokens >= n
            if ok:
                tokens -= n
            self._b[key] = (tokens, t)
            if len(self._b) > self.max_keys:          # forget the oldest half (full buckets mostly)
                for k in sorted(self._b, key=lambda k: self._b[k][1])[: self.max_keys // 2]:
                    self._b.pop(k, None)
            return ok

    def reset(self) -> None:
        with self._lock:
            self._b.clear()


DEFAULT_TRUSTED = "127.0.0.1/32,::1/128"


def trusted_networks() -> list:
    raw = os.environ.get("WKO5COACH_TRUSTED_PROXIES", DEFAULT_TRUSTED)
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return out


def _header(scope, name: bytes) -> Optional[str]:
    for k, v in scope.get("headers") or []:
        if k.lower() == name:
            return v.decode("latin-1").strip()
    return None


def client_ip(scope) -> str:
    """The visitor's IP: CF-Connecting-IP (else the first X-Forwarded-For)
    only when the direct peer is a trusted proxy (cloudflared, the Docker
    bridge, the hosting platform's proxy); otherwise the peer itself."""
    peer = (scope.get("client") or ("", 0))[0] or ""
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer or "unknown"
    if any(addr in n for n in trusted_networks()):
        fwd = _header(scope, b"cf-connecting-ip")
        if not fwd:
            xff = _header(scope, b"x-forwarded-for")
            fwd = xff.split(",")[0].strip() if xff else None
        if fwd:
            try:
                return str(ipaddress.ip_address(fwd))
            except ValueError:
                pass
    return str(addr)


class Gate:
    """At most `n` concurrent heavy computations; a caller waits up to
    `wait_s` for a slot (then the request gets 503)."""

    def __init__(self, n: int = 3, wait_s: float = 10.0):
        self.n = n
        self.wait_s = wait_s
        self._sem = None

    def _get(self):
        import asyncio
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.n)
        return self._sem

    async def acquire(self) -> bool:
        import asyncio
        try:
            await asyncio.wait_for(self._get().acquire(), self.wait_s)
            return True
        except asyncio.TimeoutError:
            return False

    def release(self) -> None:
        self._get().release()
