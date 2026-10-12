"""
Server-wide settings and state (SP-385; read by SP-330's data-source scopes, edited later by
SP-351's admin pages): one small JSON file in the server root, apart from every tenant.

    site_settings.get("coros.enabled", True)
    site_settings.update({"coros.enabled": False, "coros.tripped": {...}})

<server-root>/site.json (accounts.server_root(): the owner's folder unless
WKO5COACH_SERVER_ROOT says otherwise). Dotted keys, flat. Written atomically (tmp + replace);
read through an mtime cache, so a change made by another process (a CLI) is seen at once.
Nothing personal in it: tenants appear by their id (u<n>) only.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

PATH: Optional[Path] = None          # fixed file (tests); None = <server-root>/site.json
_LOCK = threading.RLock()
_CACHE: dict = {"key": None, "data": {}}


def path() -> Path:
    if PATH is not None:
        return Path(PATH)
    from backend import accounts
    return accounts.server_root() / "site.json"


def _read() -> dict:
    p = path()
    try:
        st = p.stat()
    except OSError:
        return {}
    key = (str(p), st.st_mtime_ns, st.st_size)
    if _CACHE["key"] == key:
        return _CACHE["data"]
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    _CACHE.update(key=key, data=data)
    return data


def all() -> dict:                       # noqa: A001 — the module's own namespace
    with _LOCK:
        return dict(_read())


def get(key: str, default: Any = None) -> Any:
    with _LOCK:
        return _read().get(key, default)


def update(values: dict) -> dict:
    """Set (value) or remove (None) keys; returns the new settings."""
    with _LOCK:
        data = dict(_read())
        for k, v in values.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
        os.replace(tmp, p)
        _CACHE.update(key=None, data={})
        return dict(_read())


def reset_cache() -> None:
    with _LOCK:
        _CACHE.update(key=None, data={})
