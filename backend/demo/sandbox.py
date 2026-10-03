"""
Demo instance storage (docs/plans/auth-and-demo.plan.md §3.1, §3.3).

    <demo-root>/                 = $WKO5COACH_HOME of the demo process
      base/current               one line: the base folder in use, e.g. 2026-09-28
      base/<name>/               a full demo tenant (wko5coach.db, plan.json, fit/, cache/ ...)
      sandboxes/<sha256(cookie)[:32]>/   one visitor: wko5coach.db, plan.json, meta.json ...

A visitor gets a sandbox on the first write (copy-on-write): the base DB is
copied with SQLite's backup API, plus the small private JSON files. Reads
without a sandbox go to the base. A sandbox lives 24 h from creation (never
extended), and is reset when the base it was copied from is no longer current.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from backend import tenancy

COOKIE = "trc_demo"
TTL_S = 24 * 3600
MAX_SANDBOXES = 300
MAX_TOTAL_BYTES = 1024 ** 3
# the private files a sandbox copies from its base (§2.3); the rest is read from the base
PRIVATE_FILES = ("plan.json", "racepower_hike_meta.json", "racepower_solo_hikes.json")
PRIVATE_DIRS = ("event_gpx",)
_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_HEX = re.compile(r"^[0-9a-f]{32}$")

now: Callable[[], float] = time.time          # tests replace (fake clock)
_LOCK = threading.Lock()


class DemoNotReady(RuntimeError):
    pass


def demo_root() -> Path:
    return tenancy.home_root()


def base_root() -> Path:
    return demo_root() / "base"


def sandboxes_root() -> Path:
    return demo_root() / "sandboxes"


def current_base_name() -> str:
    try:
        name = (base_root() / "current").read_text("utf-8").strip()
    except OSError:
        raise DemoNotReady(f"no demo data: {base_root() / 'current'} is missing (python -m backend.demo.build)")
    if not _NAME.match(name):
        raise DemoNotReady(f"bad base name in {base_root() / 'current'}")
    return name


def current_base_dir() -> Path:
    return base_root() / current_base_name()


def set_current_base(name: str) -> None:
    """Atomically switch the base new visitors read (the weekly rebuild)."""
    if not _NAME.match(name) or not (base_root() / name).is_dir():
        raise ValueError(f"no base folder {name!r}")
    tmp = base_root() / "current.tmp"
    tmp.write_text(name + "\n", "utf-8")
    os.replace(tmp, base_root() / "current")


# ---------------------------------------------------------------------------
# sandboxes
# ---------------------------------------------------------------------------

def sandbox_id(cookie: str) -> str:
    return hashlib.sha256(cookie.encode("utf-8")).hexdigest()[:32]


def sandbox_dir(sid: str) -> Path:
    if not _HEX.match(sid):
        raise ValueError("bad sandbox id")
    return sandboxes_root() / sid


def read_meta(d: Path) -> Optional[dict]:
    try:
        m = json.loads((d / "meta.json").read_text("utf-8"))
        return m if isinstance(m, dict) else None
    except (OSError, ValueError):
        return None


def _write_meta(d: Path, meta: dict) -> None:
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta), "utf-8")
    os.replace(tmp, d / "meta.json")


def tenant_for(sid: str, base_dir: Path) -> tenancy.Tenant:
    return tenancy.Tenant(id=f"demo-{sid}", kind=tenancy.DEMO_SANDBOX, root=sandbox_dir(sid),
                          shared=base_dir, caps=tenancy.DEMO_CAPS)


def valid(meta: Optional[dict], base: str) -> bool:
    return bool(meta) and meta.get("base") == base and now() < float(meta.get("created", 0)) + TTL_S


def lookup(cookie: Optional[str]) -> Optional[tuple[tenancy.Tenant, dict]]:
    """The visitor's live sandbox (tenant, meta), or None: no cookie, no such
    sandbox, expired, or copied from an older base (then deleted)."""
    if not cookie or len(cookie) > 200:
        return None
    sid = sandbox_id(cookie)
    d = sandbox_dir(sid)
    meta = read_meta(d)
    if meta is None:
        return None
    base = current_base_name()
    if not valid(meta, base):
        delete(sid)
        return None
    return tenant_for(sid, base_root() / base), meta


def create(cookie: str) -> tuple[tenancy.Tenant, dict]:
    """A new sandbox for `cookie`: the base's DB (SQLite backup API) and private files."""
    base = current_base_name()
    bdir = base_root() / base
    sid = sandbox_id(cookie)
    d = sandbox_dir(sid)
    with _LOCK:
        enforce_limits(reserve=1)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        tmp = sandboxes_root() / f".{sid}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        src = sqlite3.connect(f"file:{(bdir / 'wko5coach.db').as_posix()}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(str(tmp / "wko5coach.db"))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        for name in PRIVATE_FILES:
            if (bdir / name).is_file():
                shutil.copy2(bdir / name, tmp / name)
        for name in PRIVATE_DIRS:
            if (bdir / name).is_dir():
                shutil.copytree(bdir / name, tmp / name)
        meta = {"created": now(), "last_seen": now(), "base": base, "writes": 0}
        _write_meta(tmp, meta)
        os.replace(tmp, d)
    return tenant_for(sid, bdir), meta


def touch_write(t: tenancy.Tenant) -> dict:
    """Count a write (the per-sandbox limits read it)."""
    meta = read_meta(t.root) or {}
    meta["writes"] = int(meta.get("writes", 0)) + 1
    meta["last_seen"] = now()
    try:
        _write_meta(t.root, meta)
    except OSError:
        pass
    return meta


def delete(sid: str) -> None:
    d = sandbox_dir(sid)
    try:
        from backend.db import database
        database.forget(d / "wko5coach.db")
    except Exception:                 # noqa: BLE001
        pass
    shutil.rmtree(d, ignore_errors=True)


def expires_at(meta: dict) -> float:
    return float(meta.get("created", 0)) + TTL_S


def _dir_bytes(d: Path) -> int:
    n = 0
    for p in d.rglob("*"):
        try:
            if p.is_file():
                n += p.stat().st_size
        except OSError:
            pass
    return n


def list_sandboxes() -> list[tuple[str, Path, Optional[dict]]]:
    root = sandboxes_root()
    if not root.is_dir():
        return []
    return [(p.name, p, read_meta(p)) for p in root.iterdir() if p.is_dir() and _HEX.match(p.name)]


def janitor() -> dict:
    """Delete expired sandboxes and those of an old base; drop leftovers of
    interrupted creations; then enforce the totals. Also prunes old bases
    (keeps the current one and one before it)."""
    try:
        base = current_base_name()
    except DemoNotReady:
        return {"deleted": 0}
    n = 0
    for sid, d, meta in list_sandboxes():
        if not valid(meta, base):
            delete(sid)
            n += 1
    root = sandboxes_root()
    if root.is_dir():
        for p in root.iterdir():
            if p.name.startswith(".") and p.name.endswith(".tmp") and now() - p.stat().st_mtime > 600:
                shutil.rmtree(p, ignore_errors=True)
    with _LOCK:
        n += enforce_limits()
    old = sorted(p for p in base_root().iterdir() if p.is_dir() and p.name != base)
    for p in old[:-1]:
        shutil.rmtree(p, ignore_errors=True)
    return {"deleted": n}


def enforce_limits(reserve: int = 0) -> int:
    """Keep ≤ MAX_SANDBOXES (minus `reserve` slots) and ≤ MAX_TOTAL_BYTES: the oldest go first."""
    boxes = [(float((m or {}).get("created", 0)), sid, d) for sid, d, m in list_sandboxes()]
    boxes.sort()
    n = 0
    while boxes and len(boxes) > MAX_SANDBOXES - reserve:
        _c, sid, _d = boxes.pop(0)
        delete(sid)
        n += 1
    if boxes:
        sizes = {sid: _dir_bytes(d) for _c, sid, d in boxes}
        total = sum(sizes.values())
        while boxes and total > MAX_TOTAL_BYTES:
            _c, sid, _d = boxes.pop(0)
            total -= sizes[sid]
            delete(sid)
            n += 1
    return n
