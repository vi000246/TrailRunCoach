"""
In-process abuse limits (auth-and-demo.plan.md §3.7). One uvicorn worker, so
plain dicts are enough; nothing is stored on disk and no IP is logged.

    b = Buckets(rate=60, per=60, burst=60)    # 60 / minute
    b.take(key)                               # False when empty
    w = Window(limit=10, per=900)             # at most 10 in any 15 minutes (sign-in, SP-58)
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


class Window:
    """At most `limit` events per key within any `per` seconds (a sliding window: the sign-in
    limit, 10 per IP in 15 minutes, SP-58). A refused attempt is not counted, so the key is free
    again `per` seconds after its oldest counted event."""

    def __init__(self, limit: int, per: float, max_keys: int = 50_000):
        self.limit = int(limit)
        self.per = float(per)
        self.max_keys = max_keys
        self._w: dict = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> bool:
        t = clock()
        with self._lock:
            q = [x for x in self._w.get(key, ()) if t - x < self.per]
            ok = len(q) < self.limit
            if ok:
                q.append(t)
            if q:
                self._w[key] = q
            else:
                self._w.pop(key, None)
            if len(self._w) > self.max_keys:          # forget the keys idle the longest
                for k in sorted(self._w, key=lambda k: self._w[k][-1])[: self.max_keys // 2]:
                    self._w.pop(k, None)
            return ok

    def reset(self) -> None:
        with self._lock:
            self._w.clear()


DEFAULT_TRUSTED = "127.0.0.1/32,::1/128"


def trusted_networks() -> list:
    return trusted_networks_of(os.environ.get("WKO5COACH_TRUSTED_PROXIES", DEFAULT_TRUSTED))


def trusted_networks_of(raw: str) -> list:
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
    """The visitor's IP: CF-Connecting-IP (else the address our proxy appended
    to X-Forwarded-For) only when the direct peer is a trusted proxy (cloudflared, the Docker
    bridge, the hosting platform's proxy); otherwise the peer itself."""
    peer = (scope.get("client") or ("", 0))[0] or ""
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer or "unknown"
    nets = trusted_networks()
    if any(addr in n for n in nets):
        fwd = _header(scope, b"cf-connecting-ip")
        if fwd:
            try:
                return str(ipaddress.ip_address(fwd))
            except ValueError:
                pass
        # X-Forwarded-For: the entries a client sent come first; the rightmost
        # address that is not one of our proxies is the one our proxy saw
        xff = _header(scope, b"x-forwarded-for")
        for part in reversed([x.strip() for x in (xff or "").split(",") if x.strip()]):
            try:
                a = ipaddress.ip_address(part)
            except ValueError:
                break
            if not any(a in n for n in nets):
                return str(a)
    return str(addr)


def limit_key(ip: str) -> str:
    """The key a per-client limit counts under: an IPv4 address as it is, an IPv6 one by its /64
    (one subscriber usually holds a whole /64; counting single addresses would let it rotate
    through them, SP-58)."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if a.version == 6:
        if a.ipv4_mapped is not None:
            return str(a.ipv4_mapped)
        return str(ipaddress.ip_network(f"{a}/64", strict=False))
    return str(a)


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
