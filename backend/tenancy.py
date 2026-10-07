"""
Tenant context: which data a request (or a background job) belongs to
(docs/plans/auth-and-demo.plan.md §2). Every per-person path and the app DB
come from here, so the demo sandboxes now and the signed-in users later use
one mechanism.

    t = tenancy.current()            # the request's tenant; the owner when nothing is set
    tenancy.private_path("plan.json")    # t.root / ...    (writable, per person)
    tenancy.shared_path("cache")         # t.shared / ...  (FIT, caches, Dataset inputs)
    tenancy.db_path()                    # t.root / "wko5coach.db"
    with tenancy.use(other): ...         # temporarily switch (build a Dataset, janitor)

Kinds:
  owner         the self-hosted app: root = shared = $WKO5COACH_HOME (default
                ~/.wko5coach, as before); the only tenant with a WKO5 folder
  demo_base     the demo instance's shared fake athlete (<demo-root>/base/<name>);
                requests without a sandbox cookie read it
  demo_sandbox  one visitor's copy-on-write sandbox: root = its own folder (DB,
                plan.json ...), shared = the demo base (FIT, caches)
  user          (later: signed-in users, §5)

The tenant is set per request by backend/tenancy_mw.py (a pure ASGI middleware:
contextvars set there reach async endpoints and, through AnyIO's context copy,
sync ones and `asyncio.create_task`). A plain `threading.Thread` does NOT carry
contextvars: start it with `contextvars.copy_context().run` or `with use(t)`.
"""
from __future__ import annotations

import itertools
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

OWNER = "owner"
USER = "user"
DEMO_BASE = "demo_base"
DEMO_SANDBOX = "demo_sandbox"

# what a tenant may do (the backend decides; the shell only follows, §3.5)
ALL_CAPS = frozenset({
    "plan.write", "thresholds.write", "profile.write", "dataset.write", "settings",
    "sync", "connect", "push", "upload.fit", "upload.gpx", "ai", "share",
    "weather.key", "backtest", "backup", "injuries", "wko5", "achievements",
})
DEMO_CAPS = frozenset({"plan.write", "upload.gpx"})

ENV_HOME = "WKO5COACH_HOME"
ENV_MODE = "WKO5COACH_MODE"


@dataclass(frozen=True)
class Tenant:
    id: str                       # "owner" | "u<user_id>" | "demo-base" | "demo-<hash>"
    kind: str                     # OWNER | USER | DEMO_BASE | DEMO_SANDBOX
    root: Path                    # writable: DB, plan.json ...
    shared: Path                  # big / read-mostly: FIT, cache/, dataset caches (== root unless a sandbox)
    caps: frozenset = ALL_CAPS
    wko5_dir: Optional[Path] = None   # owner only (settings/paths.py finds it lazily)

    @property
    def is_demo(self) -> bool:
        return self.kind in (DEMO_BASE, DEMO_SANDBOX)

    def can(self, cap: str) -> bool:
        return cap in self.caps


_CURRENT: ContextVar[Optional[Tenant]] = ContextVar("trc_tenant", default=None)


def demo_mode() -> bool:
    """The demo instance (WKO5COACH_MODE=demo)."""
    return os.environ.get(ENV_MODE, "").strip().lower() == "demo"


def home_root() -> Path:
    """$WKO5COACH_HOME, else ~/.wko5coach (evaluated per call: tests fake home)."""
    v = os.environ.get(ENV_HOME, "").strip()
    return Path(v) if v else Path.home() / ".wko5coach"


def owner() -> Tenant:
    r = home_root()
    return Tenant(id="owner", kind=OWNER, root=r, shared=r, caps=ALL_CAPS)


def demo_base(base_dir: Optional[Path] = None) -> Tenant:
    """The shared demo athlete: <demo-root>/base/<current> (backend/demo/sandbox.py)."""
    if base_dir is None:
        from backend.demo import sandbox as SB
        base_dir = SB.current_base_dir()
    return Tenant(id="demo-base", kind=DEMO_BASE, root=base_dir, shared=base_dir, caps=DEMO_CAPS)


def default() -> Tenant:
    """The tenant when nothing is set: the demo base in the demo instance, else the owner."""
    return demo_base() if demo_mode() else owner()


def current() -> Tenant:
    t = _CURRENT.get()
    return t if t is not None else default()


def set_current(t: Optional[Tenant]):
    """Low level (the middleware): returns the token for reset()."""
    return _CURRENT.set(t)


def reset(token) -> None:
    _CURRENT.reset(token)


@contextmanager
def use(t: Tenant) -> Iterator[Tenant]:
    tok = _CURRENT.set(t)
    try:
        yield t
    finally:
        _CURRENT.reset(tok)


def base_of(t: Optional[Tenant] = None) -> Tenant:
    """The tenant whose data a Dataset is built from: a sandbox's demo base,
    else the tenant itself (the charts never read a sandbox's private copy)."""
    t = t or current()
    if t.kind == DEMO_SANDBOX:
        return Tenant(id="demo-base", kind=DEMO_BASE, root=t.shared, shared=t.shared, caps=DEMO_CAPS)
    return t


_GEN = itertools.count(1)
_GEN_LOCK = threading.Lock()


def generation(ds) -> int:
    """A number unique to this Dataset object for the life of the process (SP-320 ④).
    Replaces id(ds) in in-memory cache keys: CPython reuses a freed object's id, so a
    new Dataset (after a sync, or another tenant's) could match a stale entry."""
    g = getattr(ds, "_trc_generation", None)
    if g is None:
        with _GEN_LOCK:
            g = getattr(ds, "_trc_generation", None)
            if g is None:
                g = next(_GEN)
                try:
                    ds._trc_generation = g
                except AttributeError:          # no instance dict (a test stub): fall back to the id
                    return id(ds)
    return g


def ds_key(ds) -> tuple:
    """(tenant id, dataset generation): the head of every in-memory cache key that holds
    one person's results (SP-320 ④: two tenants never share an entry)."""
    return (current().id, generation(ds))


def private_path(*parts: str) -> Path:
    return current().root.joinpath(*parts)


def shared_path(*parts: str) -> Path:
    return current().shared.joinpath(*parts)


def base_path(*parts: str) -> Path:
    """Settings a sandbox inherits read-only from its base (engine.json,
    corrections.json, views/, annotations.json: they change the shared
    Dataset, so the demo never writes them, §2.3); the tenant's own root otherwise."""
    return base_of(current()).root.joinpath(*parts)


def db_path() -> Path:
    return current().root / "wko5coach.db"


def can(cap: str) -> bool:
    return current().can(cap)
