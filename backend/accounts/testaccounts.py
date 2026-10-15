"""
測試帳號 (SP-611): accounts an admin makes on the admin page to try the app as several people at
once (a coach and their students, a group) — on the production site, apart from the real members.

What a test account is (store.users.is_test = 1, test_meta):
  origin "admin"   made here: a name only — no e-mail, no Google account, no mail sent. It signs in
                   only through a one-time link an admin makes for it (below)
  origin "marked"  an account someone signed up with (e.g. a Gmail "+" address of the admin's), marked
                   on the admin page; it signs in as it always did, never through a link
  Either way it is left out of the member cap, the user lists the server walks (the scheduler, the
  deploy warm-up, the background sync, the research pool — store.users()), and payment
  (api/billing.py); it accepts no terms (login.consent_current) and connects no real platform
  (membership: no data-source scope, no automatic sync). Only the admin page lists it.

Made-up data (mock): when an account is made the admin may ask for the demo athlete's kind of data
(backend/demo: FIT files, plan, races, thresholds) — WEEKS weeks at one of athlete.LEVELS. One
build at a time, in a background thread: the demo builder's child process writes into
users/.mock-<id>.tmp, which then becomes users/<id> (its DB's file paths pointed there). The
state is in test_meta["mock"]: queued | running | done | failed. While it is queued or running
no sign-in link is made or used and the account is not deleted (the folder is being written).

One-time sign-in links (LINK_TTL_S, taken once): <public url>/test-login#<token>. The token
travels in the URL's fragment, which a browser never sends: no server or proxy log, no Referer,
and a chat app's link preview cannot use it up (the page posts it). accounts.db keeps only its
sha256. A browser signed in as someone who is not a test account (the admin) is refused, link
untouched: the admin's own session is never replaced — open the link in a private window or
another browser. Deleting a test account deletes its sessions and links with it.

create_many(): many test accounts at once, for the automated tests of what several people do
together (backend/tests/accounts_fixtures.py) and the cross-project scripts (SP-616).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import queue
import secrets as _secrets
import shutil
import threading
from pathlib import Path
from typing import Optional

from backend import accounts
from backend.accounts import store

log = logging.getLogger(__name__)

LINK_TTL_S = 30 * 60
WEEKS = (8, 16, 26, 52)
DEFAULT_WEEKS = 16
LEVELS = ("beginner", "intermediate", "advanced")
DEFAULT_LEVEL = "intermediate"
NAME_MAX = 40
MAX_MANY = 200                       # create_many (tests, scripts)
MAX_BATCH = 20                       # one go on the admin page
SEED = 20261002
BUILD_TIMEOUT_S = 3 * 3600          # 52 weeks on the NAS is ~20 min; a hung child is given up long after
ADMIN, MARKED = "admin", "marked"
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
BUSY = (QUEUED, RUNNING)

# the sign-in settings and the server's own places a child build must never see: it runs the app
# in owner mode on its own folder (backend/demo/build.py)
_CHILD_DROP = ("WKO5COACH_ACCOUNTS", "WKO5COACH_SERVER_ROOT", "WKO5COACH_SERVER_BACKUP_DIR",
               "WKO5COACH_PUBLIC_URL", "WKO5COACH_OWNER_GOOGLE_SUB", "WKO5COACH_OWNER_EMAIL",
               "WKO5COACH_TRUSTED_PROXIES", "WKO5COACH_MAX_USERS")
_CHILD_DROP_PREFIX = ("GOOGLE_CLIENT_", "TRC_POLAR_", "TRC_DEBUG", "INTERVALS_")


class Refused(ValueError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


# ---------------------------------------------------------------------------- making and listing

def mock_spec(weeks=None, level=None) -> dict:
    """The made-up data asked for (Refused BAD_WEEKS / BAD_LEVEL)."""
    w = DEFAULT_WEEKS if weeks in (None, "") else weeks
    try:
        w = int(w)
    except (TypeError, ValueError):
        raise Refused("BAD_WEEKS") from None
    if w not in WEEKS:
        raise Refused("BAD_WEEKS")
    lv = level or DEFAULT_LEVEL
    if lv not in LEVELS:
        raise Refused("BAD_LEVEL")
    return {"weeks": w, "level": lv}


def clean_name(name) -> str:
    n = " ".join(str(name or "").split())
    if not n or len(n) > NAME_MAX or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in n):
        raise Refused("BAD_NAME")
    return n


def create(name: str, *, actor: Optional[int], mock: Optional[dict] = None, start: bool = True) -> store.User:
    """A test account (origin admin). With `mock` (mock_spec()) its made-up data is queued."""
    meta = {"origin": ADMIN, "by": actor}
    if mock is not None:
        meta["mock"] = {**mock_spec(mock.get("weeks"), mock.get("level")), "state": QUEUED}
    u = store.create_test_user(clean_name(name), meta)
    if mock is not None and start:
        enqueue(u.id)
    return u


def create_many(n: int, prefix: str = "測試", *, actor: Optional[int] = None, mock: Optional[dict] = None,
                start: bool = True) -> list[store.User]:
    """`n` test accounts named "<prefix> 1" … "<prefix> n" (each a new account)."""
    if not 1 <= int(n) <= MAX_MANY:
        raise Refused("BAD_COUNT")
    return [create(f"{prefix} {i}", actor=actor, mock=mock, start=start) for i in range(1, int(n) + 1)]


def is_admin_made(u: store.User) -> bool:
    return bool(u.is_test) and store.test_meta(u.id).get("origin") == ADMIN


def mock_state(uid: int, meta: Optional[dict] = None) -> Optional[dict]:
    """test_meta["mock"]; a queued / running one this process isn't working on is reported failed
    (the server restarted in between)."""
    meta = store.test_meta(uid) if meta is None else meta
    m = meta.get("mock")
    if not m:
        return None
    m = dict(m)
    if m.get("state") in BUSY and uid not in _ACTIVE:
        m["state"], m["error"] = FAILED, "INTERRUPTED"
    return m


def busy(uid: int) -> bool:
    m = mock_state(uid)
    return bool(m) and m.get("state") in BUSY


def view(u: store.User) -> dict:
    """What the admin page shows of a test account (never a token)."""
    meta = store.test_meta(u.id)
    return {"origin": meta.get("origin"), "mock": mock_state(u.id, meta), "links": store.count_test_logins(u.id)}


def mark(u: store.User, on: bool, actor: Optional[int]) -> None:
    """Mark an account someone signed up with a test account (or not). An account made here stays one."""
    if u.is_owner or u.id == actor:
        raise Refused("CANNOT_MARK_SELF")
    if not on and is_admin_made(u):
        raise Refused("ADMIN_MADE")
    if bool(on) == u.is_test:
        return
    store.set_test(u.id, on, {"origin": MARKED, "by": actor} if on else None)


# ---------------------------------------------------------------------------- one-time sign-in links

def make_link(u: store.User, actor: Optional[int]) -> tuple[str, str]:
    """(token, expires_at) of a new one-time link for an account made here."""
    if not is_admin_made(u):
        raise Refused("NOT_ADMIN_MADE")
    if u.disabled:
        raise Refused("DISABLED", 409)
    if busy(u.id):
        raise Refused("MOCK_RUNNING", 409)
    token = _secrets.token_urlsafe(32)
    return token, store.put_test_login(token, u.id, actor, LINK_TTL_S)


def link_url(token: str, base: str) -> str:
    return base.rstrip("/") + "/test-login#" + token


# ---------------------------------------------------------------------------- deleting

async def delete(u: store.User) -> dict:
    """A test account and all its data (accounts/deletion.py: its sessions and links go with it)."""
    if not u.is_test or u.is_owner:
        raise Refused("NOT_TEST")
    if busy(u.id):
        raise Refused("MOCK_RUNNING", 409)
    from backend.accounts import deletion
    return await deletion.delete_account(u)


# ---------------------------------------------------------------------------- made-up data

_Q: "queue.Queue[int]" = queue.Queue()
_ACTIVE: set = set()                 # ids queued or being built in this process
_WORKER: Optional[threading.Thread] = None
_WORKER_LOCK = threading.Lock()


def enqueue(uid: int) -> None:
    global _WORKER
    _ACTIVE.add(int(uid))
    _Q.put(int(uid))
    with _WORKER_LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_work, name="test-account-mock", daemon=True)
            _WORKER.start()


def wait_idle(timeout: float = 600.0) -> bool:
    """Every queued build done (tests)."""
    import time
    end = time.monotonic() + timeout
    while _ACTIVE and time.monotonic() < end:
        time.sleep(0.05)
    return not _ACTIVE


def _work() -> None:
    while True:
        try:
            uid = _Q.get(timeout=30)
        except queue.Empty:
            return
        try:
            build_mock(uid)
        except Exception as e:                     # noqa: BLE001 — recorded on the account; the next one runs
            log.warning("test account u%s: made-up data failed (%s)", uid, type(e).__name__)
        finally:
            _ACTIVE.discard(uid)
            _Q.task_done()


def child_env(dest: Path) -> dict:
    """The child build's environment: the demo builder's, without the sign-in settings and the
    server's own places (its app runs in owner mode on `dest` alone)."""
    from backend.demo import build as B
    env = B.child_env(dest)
    for k in list(env):
        if k in _CHILD_DROP or k.startswith(_CHILD_DROP_PREFIX):
            env.pop(k, None)
    return env


def _stage(dest: Path, spec: dict, seed: int, small: bool) -> None:
    from backend.demo import build as B
    B.stage_into(dest, seed=seed, anchor=dt.date.today(), weeks=spec["weeks"], level=spec["level"], small=small,
                 env=child_env(dest), timeout_s=BUILD_TIMEOUT_S)


def build_mock(uid: int, small: bool = False) -> Optional[dict]:
    """Build a test account's made-up data now (the worker; tests call it directly). Returns the
    mock state written, None when the account is gone or asked for none."""
    u = store.get_user(uid)
    meta = store.test_meta(uid) if u is not None else {}
    spec = meta.get("mock")
    if u is None or not u.is_test or not spec:
        return None
    from backend.accounts import deletion, tenants as TN
    from backend.db import database
    final = deletion.tenant_folder(u)              # exactly users/<id>, never a link or the owner's
    tmp = accounts.users_dir() / f".mock-{u.id}.tmp"
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    store.update_test_meta(uid, mock={**spec, "state": RUNNING, "started_at": started})
    log.info("test account u%s: building %s weeks of made-up data (%s)", uid, spec["weeks"], spec["level"])
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        _stage(tmp, spec, SEED + int(uid), small)
        if store.get_user(uid) is None:            # deleted meanwhile (it was refused, but be sure)
            shutil.rmtree(tmp, ignore_errors=True)
            return None
        database.forget(final / "wko5coach.db")
        if final.exists():
            shutil.rmtree(final)
        os.replace(tmp, final)
        from backend.demo.build import relocate_paths
        relocate_paths(final)
        TN._READY.discard(str(final))
        n = _activities(final)
        out = {**spec, "state": DONE, "started_at": started, "activities": n,
               "done_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        log.info("test account u%s: made-up data ready (%d activities)", uid, n)
    except Exception as e:                         # noqa: BLE001 — the account stays, without data
        shutil.rmtree(tmp, ignore_errors=True)
        out = {**spec, "state": FAILED, "started_at": started, "error": type(e).__name__}
        log.warning("test account u%s: made-up data failed (%s: %s)", uid, type(e).__name__, str(e)[:300])
    if store.get_user(uid) is not None:
        store.update_test_meta(uid, mock=out)
    return out


def _activities(root: Path) -> int:
    import sqlite3
    db = root / "wko5coach.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return int(con.execute("SELECT count(*) FROM workout_files").fetchone()[0])
        finally:
            con.close()
    except sqlite3.Error:
        return 0
