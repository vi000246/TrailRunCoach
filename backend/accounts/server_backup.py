"""
The whole server's daily backup in multi-user mode (SP-387 第 7 條, after SP-384; SP-355's per-tenant
backup is the building block).

The owner's own backup (設定 › 備份) holds only the owner's tenant — on purpose, accounts.db and
users/ are other people's data (data_registry: scope INSTANCE, backup never). This one is the
operator's: one folder per run under WKO5COACH_SERVER_BACKUP_DIR (a NAS share, never inside the
server root):

    <dir>/server-YYYYMMDD-HHMMSS/
        accounts.db                 a consistent snapshot (sqlite online backup)
        site.json                   the server-wide settings (switches, limits)
        users/<id>/trailruncoach-backup-….zip   each user tenant, made by engine/backup.py
                                    (DB without credentials, the user's files; FIT originals
                                    only with WKO5COACH_SERVER_BACKUP_FIT=1)
        manifest.json               when, how many users, sizes, failures

A user whose backup fails is listed in the manifest and the run goes on. The last KEEP runs are
kept (only folders named like ours are ever deleted). Run daily from the scheduler (owner's loop),
or by hand: `python -m backend.accounts.server_backup`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
ENV_DIR = "WKO5COACH_SERVER_BACKUP_DIR"
ENV_FIT = "WKO5COACH_SERVER_BACKUP_FIT"
KEEP = 7
EVERY_S = 23 * 3600
NAME_RE = re.compile(r"^server-\d{8}-\d{6}$")
STATE_KEY = "backup.server"


def dest_dir() -> Optional[Path]:
    v = os.environ.get(ENV_DIR, "").strip()
    return Path(v) if v else None


def run(dest: Optional[Path] = None, now: Optional[datetime] = None) -> dict:
    """One server backup. Returns the manifest."""
    from backend import accounts, site_settings as SS
    from backend.accounts import store, tenants as TN
    from backend.engine import backup as B
    dest = Path(dest) if dest is not None else dest_dir()
    if dest is None:
        raise RuntimeError(f"{ENV_DIR} is not set")
    root = accounts.server_root().resolve()
    if str(dest.resolve()).startswith(str(root) + os.sep) or dest.resolve() == root:
        raise RuntimeError("the server backup folder must be outside the server root")
    now = (now or datetime.now(timezone.utc)).astimezone()
    out = dest / f"server-{now.strftime('%Y%m%d-%H%M%S')}"
    tmp = dest / f".{out.name}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    t0 = time.monotonic()
    man: dict = {"created_at": now.isoformat(timespec="seconds"), "users": 0, "failed": [], "files": {}}
    try:
        store.init()
        B.snapshot_db(store.path(), tmp / "accounts.db")
        man["files"]["accounts.db"] = (tmp / "accounts.db").stat().st_size
        if SS.path().exists():
            shutil.copy2(SS.path(), tmp / "site.json")
        include_fit = os.environ.get(ENV_FIT, "") in ("1", "true", "yes")
        for u in store.users(include_disabled=True):
            if u.is_owner:
                continue                         # the owner's tenant has its own backup (設定 › 備份)
            t = TN.tenant_of(u)
            db = t.root / "wko5coach.db"
            if not db.exists():
                continue                         # never used: nothing to keep
            try:
                r = B.create_backup(db, tmp / "users" / str(u.id), fit_root=t.shared / "fit",
                                    include_fit=include_fit, roots=B.tenant_roots(t.root),
                                    work_dir=tmp / ".work" / str(u.id), tenant=t.id, now=now)
                man["users"] += 1
                man["files"][f"users/{u.id}"] = r["size"]
            except Exception as e:               # noqa: BLE001 — the others still go
                man["failed"].append({"user": u.id, "error": type(e).__name__})
                log.warning("server backup of user %s failed: %s", u.id, type(e).__name__)
        shutil.rmtree(tmp / ".work", ignore_errors=True)
        man["seconds"] = round(time.monotonic() - t0, 1)
        (tmp / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    prune(dest)
    SS.update({STATE_KEY: {"at": man["created_at"], "users": man["users"], "failed": len(man["failed"]),
                           "path": out.name}})
    return man


def prune(dest: Path, keep: int = KEEP) -> list[str]:
    runs = sorted(p for p in Path(dest).iterdir() if p.is_dir() and NAME_RE.match(p.name))
    gone = []
    for p in runs[:-keep] if keep > 0 else runs:
        shutil.rmtree(p, ignore_errors=True)
        gone.append(p.name)
    return gone


def due(now: Optional[float] = None) -> bool:
    """Multi-user mode, a folder set, and no run in the last EVERY_S."""
    from backend import accounts, site_settings as SS
    if not accounts.enabled() or dest_dir() is None:
        return False
    last = (SS.get(STATE_KEY) or {}).get("at")
    if not last:
        return True
    try:
        t = datetime.fromisoformat(last).timestamp()
    except ValueError:
        return True
    return (now or time.time()) - t >= EVERY_S


async def tick() -> Optional[dict]:
    """The scheduler's daily call (in a thread: zipping is blocking work)."""
    if not due():
        return None
    import asyncio
    return await asyncio.to_thread(run)


if __name__ == "__main__":                       # pragma: no cover — by hand on the NAS
    print(json.dumps(run(), ensure_ascii=False, indent=1))
