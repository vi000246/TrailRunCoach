"""
In-memory caches with room for each tenant (SP-374 第 4 條).

The caches behind the 課表 inputs and the race calculator used to be one small dict for the
whole server (3 plan inputs, 1 calculator slot): with a few people using the app at once they
pushed each other's results out and every page load computed again. Their keys already carry
the tenant (tenancy.ds_key, SP-320 ④) — this is about room, not correctness.

    _cache = TenantSlots()                  # a dict per tenant; LRU over tenants
    _cache.get("inputs"); _cache["inputs"] = ...          # the current tenant's slots

    _cache = TenantLRU(per_tenant=3)        # key[0] is the tenant id (_inputs_key's layout)
    _cache.get(key); _cache.put(key, value)

How many tenants are kept: WKO5COACH_CACHE_TENANTS (default 32). The total stays bounded by
that × the per-tenant room; a tenant that was not used for a while is the first to go. The
memory budget of SP-338 第 7 條 is the Dataset cache's (api/wko5views.DATASETS_MAX); these
entries are small next to a Dataset.

Not thread-safe by themselves: the callers hold their own lock around them, as before.
"""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any, Hashable, Optional

ENV_TENANTS = "WKO5COACH_CACHE_TENANTS"
DEFAULT_TENANTS = 32


def max_tenants() -> int:
    try:
        return max(1, int(os.environ.get(ENV_TENANTS, "") or DEFAULT_TENANTS))
    except ValueError:
        return DEFAULT_TENANTS


def _tid() -> str:
    from backend import tenancy
    return tenancy.current().id


class TenantSlots:
    """Named slots per tenant: `cache.get("inputs")` reads the current tenant's. Dict-like
    (get / pop / [] / in / clear) so the callers that used one global dict read the same."""

    def __init__(self, tenants: Optional[int] = None):
        self._tenants = tenants
        self._by: "OrderedDict[str, dict]" = OrderedDict()

    def _limit(self) -> int:
        return self._tenants or max_tenants()

    def slots(self, tenant_id: Optional[str] = None) -> dict:
        tid = tenant_id or _tid()
        d = self._by.get(tid)
        if d is None:
            d = self._by[tid] = {}
            while len(self._by) > self._limit():
                self._by.popitem(last=False)
        else:
            self._by.move_to_end(tid)
        return d

    def get(self, name: Hashable, default: Any = None) -> Any:
        return self.slots().get(name, default)

    def pop(self, name: Hashable, default: Any = None) -> Any:
        return self.slots().pop(name, default)

    def __getitem__(self, name: Hashable) -> Any:
        return self.slots()[name]

    def __setitem__(self, name: Hashable, value: Any) -> None:
        self.slots()[name] = value

    def __contains__(self, name: Hashable) -> bool:
        return name in self.slots()

    def clear(self) -> None:
        """Every tenant's slots (tests, a settings change that invalidates everything)."""
        self._by.clear()

    def tenants(self) -> list[str]:
        return list(self._by)


class TenantLRU:
    """Up to `per_tenant` entries for each tenant (key[0] = the tenant id), LRU within a tenant
    and over tenants. Replaces `while len(_cache) >= 3: pop the oldest` for the whole server."""

    def __init__(self, per_tenant: int, tenants: Optional[int] = None):
        self.per_tenant = per_tenant
        self._tenants = tenants
        self._by: "OrderedDict[str, OrderedDict]" = OrderedDict()

    def _limit(self) -> int:
        return self._tenants or max_tenants()

    def get(self, key: tuple, default: Any = None) -> Any:
        d = self._by.get(key[0])
        if d is None or key not in d:
            return default
        self._by.move_to_end(key[0])
        d.move_to_end(key)
        return d[key]

    def put(self, key: tuple, value: Any) -> None:
        d = self._by.get(key[0])
        if d is None:
            d = self._by[key[0]] = OrderedDict()
        self._by.move_to_end(key[0])
        d[key] = value
        d.move_to_end(key)
        while len(d) > self.per_tenant:
            d.popitem(last=False)
        while len(self._by) > self._limit():
            self._by.popitem(last=False)

    def __setitem__(self, key: tuple, value: Any) -> None:
        self.put(key, value)

    def __contains__(self, key: tuple) -> bool:
        d = self._by.get(key[0])
        return d is not None and key in d

    def pop(self, key: tuple, default: Any = None) -> Any:
        d = self._by.get(key[0])
        return default if d is None else d.pop(key, default)

    def clear(self) -> None:
        self._by.clear()

    def __len__(self) -> int:
        return sum(len(d) for d in self._by.values())

    def keys(self) -> list:
        return [k for d in self._by.values() for k in d]
