"""
刪除帳號 (SP-386; auth-and-demo plan §4.2): a user's account and every piece of their data, at
once. The web route (api/account.py) asks for a fresh Google sign-in first and never deletes the
owner; `python -m backend.accounts.deletion <user id>` is the server's way (a deletion that stopped
half way, a request that came by e-mail). Neither ever deletes user 1.

In order:
  1. every sync source of the tenant is held (runner.hold: no sync, check or file deletion runs
     until the end; SyncBusy when one is running — nothing has happened then), and the user is
     disabled: every session ends (signed out everywhere), the scheduler and the deploy warm-up
     skip them (store.users() leaves disabled users out)
  2. still holding them:
     a. each connected platform is disconnected at the platform when there is a call for it
        (REVOKE) — today there is none: COROS and Garmin are signed in with the account's
        password through unofficial APIs and TrainingPeaks by its password grant, and no
        revoke / logout endpoint of theirs is used anywhere in the app; intervals.icu
        (DELETE /api/v1/disconnect-app) is still a stub (SP-346) and stores no token. A revoke
        that fails is logged and reported; the deletion goes on
     b. the platform credentials are deleted from sync_state (the registry's secret_fields and
        local_fields: tokens, sealed passwords, the accounts' e-mails and ids), and the Garmin
        login waiting for its MFA code is dropped
     c. the tenant's queued and running background jobs are let finish (at most JOB_WAIT_S), so
        none writes into the folder after it is gone
     d. the tenant folder is deleted (users/<id>/: the DB, FIT, caches, uploads, share links) —
        only a folder that is exactly <server-root>/users/<id>, never a link
  3. every accounts.db row of the person (store.delete_user: user, identities, sessions, shares,
     usage, service tokens, consents, sign-ins in progress, sign-up tickets, the waitlist e-mail)
  4. what the server keeps by tenant id only is let go: a COROS / Garmin pause (site.json), the
     tenant's ready mark and consent cache in this process
A step that fails leaves the user disabled with reason "deleting" — signed out, never half usable
— and the CLI finishes it.

Not done here (SP-386 notes): the subscription (SP-328, not built yet); the server backups
(accounts/server_backup.py keeps the last KEEP daily runs: the user is gone from them after about
a week); log lines that name the tenant id (logs/, rotated); a share link's page is the owner's
only today (tenancy_mw: /share/* reads the owner's tenant).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from backend import accounts, data_registry as R, tenancy

log = logging.getLogger(__name__)

DELETING = "deleting"
JOB_WAIT_S = 60.0
# the sync_state columns that say a platform is connected
CONNECTED_BY = {
    "coros": ("coros_access_token", "coros_password_sealed"),
    "garmin": ("garmin_token_sealed", "garmin_password_sealed"),
    "tp": ("tp_access_token", "tp_refresh_token", "tp_web_cookie", "tp_password_sealed"),
}
# source -> async revoke(source, credentials as stored): disconnect at the platform. Empty: no
# platform the app connects to has a revoke call in use (module docstring, 2a)
REVOKE: dict[str, Callable[[str, dict], Awaitable[None]]] = {}


class DeletionError(RuntimeError):
    pass


def tenant_folder(user) -> Path:
    """The user's tenant folder, only when it is exactly <server-root>/users/<id> (never the owner's,
    the server root, or anything a link points to)."""
    if user.is_owner or int(user.id) == 1:
        raise ValueError("the owner cannot be deleted")
    d = user.folder()
    want = accounts.users_dir() / str(int(user.id))
    if d.is_symlink() or d.resolve() != want.resolve() or d.resolve().parent != accounts.users_dir().resolve():
        raise DeletionError(f"user {user.id}'s tenant folder is not users/{user.id}: not deleted")
    return d


def _credentials(db: Path) -> dict:
    """{column: value} of sync_state's credential columns (row of athlete 1, the tenant's athlete)."""
    if not db.exists():
        return {}
    t = R.table("sync_state")
    con = sqlite3.connect(str(db), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        have = {r[1] for r in con.execute("PRAGMA table_info(sync_state)")}
        if not have:
            return {}
        cols = [c for c in dict.fromkeys(t.secret_fields + t.local_fields) if c in have]
        rows = con.execute("SELECT %s FROM sync_state" % ", ".join(cols)).fetchall() if cols else []
    finally:
        con.close()
    out: dict = {}
    for r in rows:
        for c in cols:
            if r[c] is not None and out.get(c) is None:
                out[c] = r[c]
    return out


def _wipe_credentials(db: Path) -> None:
    """NULL every credential column of sync_state (secure_delete: not left on a free page)."""
    if not db.exists():
        return
    t = R.table("sync_state")
    con = sqlite3.connect(str(db), timeout=30)
    try:
        have = {r[1] for r in con.execute("PRAGMA table_info(sync_state)")}
        cols = [c for c in dict.fromkeys(t.secret_fields + t.local_fields) if c in have]
        if cols:
            con.execute("PRAGMA secure_delete=ON")
            con.execute("UPDATE sync_state SET " + ", ".join(f"{c} = NULL" for c in cols))
            con.commit()
    finally:
        con.close()


def _wait_jobs(tid: str, wait_s: float) -> list[str]:
    """Let the tenant's queued / running jobs finish; the labels of those still alive after wait_s."""
    from backend import jobs
    end = time.monotonic() + wait_s
    for j in jobs.of_tenant(tid):
        j.join(max(0.0, end - time.monotonic()))
    return [j.label for j in jobs.of_tenant(tid) if j.is_alive()]


async def _disconnect() -> dict:
    """2a + 2b: revoke at each connected platform that has a call, then delete the credentials."""
    from backend.db import database
    db = Path(database.db_path())
    creds = _credentials(db)
    connected = [s for s, cols in CONNECTED_BY.items() if any(creds.get(c) for c in cols)]
    revoked, failed = [], []
    for s in connected:
        fn = REVOKE.get(s)
        if fn is None:
            continue
        try:
            await fn(s, {c: v for c, v in creds.items() if c.startswith(s + "_")})
            revoked.append(s)
        except Exception as e:                       # noqa: BLE001 — the user's deletion goes on
            log.warning("account deletion: %s revoke failed (%s); its credentials are deleted here",
                        s, type(e).__name__)
            failed.append(s)
    await asyncio.to_thread(_wipe_credentials, db)
    try:
        from backend.sync import garmin_client
        garmin_client.forget_mfa()
    except Exception:                                # noqa: BLE001 — garminconnect not installed
        pass
    return {"disconnected": connected, "revoked": revoked, "revoke_failed": failed}


def _let_go(tid: str) -> None:
    """4: the COROS / Garmin pause of this tenant id (site.json), if any."""
    try:
        from backend.sync import coros_limits as CL, garmin_limits as GL
        if CL.paused(tid):
            CL.unpause(tid)
        if GL.paused(tid):
            GL.unpause(tid, recovered=False)
    except Exception as e:                           # noqa: BLE001 — bookkeeping by id only
        log.warning("account deletion: the pause of %s was not cleared (%s)", tid, type(e).__name__)


async def delete_account(user, *, job_wait_s: Optional[float] = None) -> dict:
    """Delete `user` (store.User) and all their data — the module docstring's steps. Returns what
    was done: {disconnected, revoked, revoke_failed, rows}. ValueError for the owner;
    runner.SyncBusy when a sync of theirs is running (nothing has happened then)."""
    from backend.accounts import login as LG, store, tenants as TN
    from backend.db import database
    from backend.sync import runner
    folder = tenant_folder(user)                     # refuses the owner before anything happens
    tenant = TN.tenant_of(user)
    with tenancy.use(tenant), contextlib.ExitStack() as held:
        for s in runner.SOURCES:                     # SyncBusy here: nothing has happened yet
            held.enter_context(runner.hold(s))
        await asyncio.to_thread(store.set_disabled, user.id, True, DELETING)
        none = {"disconnected": [], "revoked": [], "revoke_failed": []}
        done = await _disconnect() if folder.exists() else none
        left = await asyncio.to_thread(_wait_jobs, tenant.id, JOB_WAIT_S if job_wait_s is None else job_wait_s)
        if left:
            log.warning("account deletion: %d job(s) of %s still running; deleting anyway", len(left), tenant.id)
        await asyncio.to_thread(database.forget, Path(database.db_path()))     # its pooled connections
        if folder.exists():
            await asyncio.to_thread(shutil.rmtree, folder)
        if folder.exists():
            raise DeletionError(f"{tenant.id}'s folder is still there")
    rows = await asyncio.to_thread(store.delete_user, user.id)
    TN._READY.discard(str(tenant.root))
    LG._CONSENT.pop(user.id, None)
    _let_go(tenant.id)
    log.info("account %s deleted (rows: %s; platforms disconnected: %s)", tenant.id,
             sum(rows.values()), ",".join(done["disconnected"]) or "none")
    return {**done, "rows": rows}


def main(argv: Optional[list] = None) -> int:
    """python -m backend.accounts.deletion <user id>: delete that account now (never user 1)."""
    from backend.accounts import store
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or not argv[0].isdigit():
        print("usage: python -m backend.accounts.deletion <user id>", file=sys.stderr)
        return 2
    u = store.get_user(int(argv[0]))
    if u is None:
        print(f"no user {argv[0]}", file=sys.stderr)
        return 1
    if u.is_owner or u.id == 1:
        print("the owner (user 1) cannot be deleted", file=sys.stderr)
        return 2
    out = asyncio.run(delete_account(u))
    print(f"user {u.id} deleted: platforms disconnected {out['disconnected'] or 'none'}, "
          f"accounts.db rows {sum(out['rows'].values())}")
    return 0


if __name__ == "__main__":                           # pragma: no cover
    sys.exit(main())
