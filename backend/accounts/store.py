"""
accounts.db — the server's account tables, apart from every tenant's own DB (auth-and-demo
plan §4.3; SP-384). Plain sqlite3: a few small tables read once per request (the session),
written on sign-in / sign-up.

  users       id, role (owner | user), google_sub (unique), email, display_name, tenant_dir,
              created_at, last_login_at, disabled_at / disabled_reason
              (plan / plan_expires_at: the membership, SP-330; admin: an admin besides the owner,
              granted on the admin page, SP-351 R2 — set_admin never removes the last one)
  identities  (provider, subject) unique; v1 only "google"
  sessions    sha256 of the cookie token only (a stolen DB opens no session), expiry sliding;
              reauth_at: when this session's user signed in at Google again to confirm a
              deletion (SP-386), on this session only
  shares      public /share/<id> -> the user whose tenant holds it
  waitlist    e-mails waiting for a place once sign-up is capped (SP-58)
  usage       the free tier's counters per user / quota / period (SP-330); usage_items: what
              was counted, when a distinct item counts once a period (a chart dashboard a day)
  api_tokens  service tokens of another project (疊石 Cairn / inReachLiveTrack, SP-329, client
              "cairn"): sha256 only, the link:* scopes they carry, 30 days, one live per user and
              project, deleted when the user unlinks; and the weight API's tokens (SP-472, scope weight:write,
              client "weight"): their own row is in the tenant DB, this one only names the user
  consents    each acceptance of the terms (SP-58): user, terms version, the sha256 of every
              document shown, the cross-user analysis bundled with them, when; append-only:
              action "terms" (the terms ticked) or "analysis" (the analysis consent withdrawn or
              given again in 設定, SP-386 — the terms version and documents copied from the last
              row). The analysis state is the last such row's. Action "link" / "unlink" (SP-329): the
              user let another project (`client`, e.g. "cairn") use their account here, or took it
              back in 設定; `documents` holds {"scopes": [...]} granted then. The terms and analysis
              readers skip these rows
  oauth_states  a Google sign-in in progress (SP-58): sha256 of the state, sha256 of the
              browser's binding cookie, the nonce, the PKCE verifier, where to go back; 10 minutes,
              taken once. purpose "reauth" + user_id: a signed-in user confirming a deletion (SP-386)
  signup_tickets  signed in at Google, waiting at the terms (SP-58): sha256 of the cookie, the
              Google account (sub, e-mail, name) or the user asked to accept new terms; 30 minutes,
              taken once
  billing_subscriptions  the user's Polar subscription as last applied (SP-328, backend/billing):
              Polar's customer and subscription ids, product / interval, status, period end,
              cancel flag, when a renewal first failed, the time of the state applied (newer only)
  billing_events  each Polar webhook-id applied once (SP-328): type, user, its time, applied or not;
              no payload (it holds the e-mail); kept EVENTS_KEEP_DAYS
  research_links  the research pool's B layer (SP-586, backend/research/pool.py): user -> the random
              donor id and the donor's day 0 (the pool holds days relative to it, never a date). Only
              withdrawal and retention read it; no foreign key on purpose: deleting the account
              withdraws first (accounts/deletion.py), so the pool rows are found before the link goes
  research_keys   the pool's secret keys (the HMAC key of the activity keys), never next to the pool
  test_login_links  a test account's one-time sign-in links (SP-611, accounts/test_accounts.py): sha256 of
              the token only, the user, who made it, when it ends; taken once

Test accounts (SP-611): users.is_test = 1. An admin makes them on the admin page (test_meta
{"origin": "admin", ...}: no e-mail, no Google account, signed in only through a one-time link)
or marks an account someone signed up with (origin "marked"). They are left out of the member cap
(count_users, create_user's cap), of users() unless asked for (the scheduler, the deploy warm-up,
the background sync, the research pool, the server backup), and accept no terms.

delete_user (SP-386) removes every row that names a person: the user row (identities, sessions,
shares, usage, api_tokens, consents go with it, ON DELETE CASCADE), their sign-ins in progress and
sign-up tickets, and their e-mail on the waitlist. Never the owner.

Every function opens its own connection (short; WAL + busy_timeout), so it is safe from the
event loop thread, the thread pool and the scheduler alike. User 1 is the owner, migrated in
place (migrate_owner): its tenant_dir is the existing ~/.wko5coach, nothing is moved.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets as _secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from backend import accounts

OWNER, USER = "owner", "user"
ROLES = (OWNER, USER)
SESSION_DAYS = 30
SESSION_MAX_DAYS = 90          # however much it is used, a session ends this long after the sign-in (SP-58)
SCHEMA_VERSION = 1
_LOCK = threading.Lock()
DB_PATH: Optional[Path] = None        # fixed file (tests); None = accounts.accounts_db_path()

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL DEFAULT 'user',
    google_sub TEXT UNIQUE,
    email TEXT,
    email_verified INTEGER NOT NULL DEFAULT 0,
    display_name TEXT,
    tenant_dir TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    disabled_at TEXT,
    disabled_reason TEXT,
    plan TEXT NOT NULL DEFAULT 'free',
    plan_expires_at TEXT,
    is_test INTEGER NOT NULL DEFAULT 0,
    test_meta TEXT
);
CREATE TABLE IF NOT EXISTS identities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (provider, subject)
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    reauth_at TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS shares (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'racepower',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    period TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, key, period)
);
CREATE TABLE IF NOT EXISTS usage_items (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    period TEXT NOT NULL,
    item TEXT NOT NULL,
    PRIMARY KEY (user_id, key, period, item)
);
CREATE TABLE IF NOT EXISTS api_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client TEXT NOT NULL,
    scopes TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS waitlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    invited_at TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS consents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    terms_version TEXT NOT NULL,
    documents TEXT NOT NULL,
    analysis INTEGER NOT NULL DEFAULT 1,
    accepted_at TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'terms',
    client TEXT
);
CREATE INDEX IF NOT EXISTS consents_user ON consents(user_id);
CREATE TABLE IF NOT EXISTS oauth_states (
    state_hash TEXT PRIMARY KEY,
    binding_hash TEXT NOT NULL,
    provider TEXT NOT NULL,
    nonce TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    next_url TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    purpose TEXT NOT NULL DEFAULT 'signin',
    user_id INTEGER
);
CREATE TABLE IF NOT EXISTS signup_tickets (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    email TEXT,
    display_name TEXT,
    user_id INTEGER,
    next_url TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_subscriptions (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    customer_id TEXT,
    subscription_id TEXT,
    product_id TEXT,
    interval TEXT,
    status TEXT,
    current_period_end TEXT,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    ends_at TEXT,
    past_due_since TEXT,
    source_ts TEXT,
    synced_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS billing_events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    user_id INTEGER,
    event_ts TEXT NOT NULL,
    received_at TEXT NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS research_links (
    user_id INTEGER PRIMARY KEY,
    donor_id TEXT NOT NULL UNIQUE,
    day0 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_keys (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_login_links (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class User:
    id: int
    role: str
    google_sub: Optional[str]
    email: Optional[str]
    display_name: Optional[str]
    tenant_dir: str
    created_at: str
    last_login_at: Optional[str]
    disabled_at: Optional[str]
    plan: str = "free"
    plan_expires_at: Optional[str] = None
    is_test: bool = False             # SP-611: a test account (module docstring)

    @property
    def is_owner(self) -> bool:
        return self.role == OWNER

    @property
    def disabled(self) -> bool:
        return bool(self.disabled_at)

    # SP-351 R2: an admin besides the owner (users.admin); the owner is always one
    admin: bool = False

    @property
    def is_admin(self) -> bool:
        return self.is_owner or bool(self.admin)

    def folder(self) -> Path:
        """The tenant folder: tenant_dir as stored (absolute for the owner), or relative to the
        server root (users/<id>), so the server root can move. An empty one is refused: it would
        make the server root itself (accounts.db, everyone's folders) this user's tenant."""
        if not (self.tenant_dir or "").strip():
            raise ValueError(f"user {self.id} has no tenant folder")
        p = Path(self.tenant_dir)
        return p if p.is_absolute() else accounts.server_root() / p


def path() -> Path:
    return Path(DB_PATH) if DB_PATH is not None else accounts.accounts_db_path()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _iso(t: dt.datetime) -> str:
    return t.isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=30000")
        yield con
        con.commit()
    finally:
        con.close()


_READY: set = set()


def init() -> None:
    """Create the tables (idempotent; once per file per process)."""
    key = str(path())
    if key in _READY and path().exists():
        return
    with _LOCK, connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(_DDL)
        _add_columns(con)
        con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    _READY.add(key)


# columns added after a table was first made: CREATE TABLE IF NOT EXISTS leaves an older file's
# table as it was, so they are added here (ALTER TABLE ADD COLUMN; existing rows take the default)
_ADDED_COLUMNS = (
    ("sessions", "reauth_at", "TEXT"),                                   # SP-386
    ("users", "admin", "INTEGER NOT NULL DEFAULT 0"),                    # SP-351 R2
    ("oauth_states", "purpose", "TEXT NOT NULL DEFAULT 'signin'"),       # SP-386
    ("oauth_states", "user_id", "INTEGER"),                              # SP-386
    ("consents", "action", "TEXT NOT NULL DEFAULT 'terms'"),             # SP-386
    ("consents", "client", "TEXT"),                                      # SP-329
    ("users", "is_test", "INTEGER NOT NULL DEFAULT 0"),                  # SP-611
    ("users", "test_meta", "TEXT"),                                      # SP-611
)


def _add_columns(con: sqlite3.Connection) -> None:
    for table, col, decl in _ADDED_COLUMNS:
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _user(row: Optional[sqlite3.Row]) -> Optional[User]:
    if row is None:
        return None
    admin = bool(row["admin"]) if "admin" in row.keys() else False      # SP-351 R2
    return User(admin=admin, id=row["id"], role=row["role"], google_sub=row["google_sub"], email=row["email"],
                display_name=row["display_name"], tenant_dir=row["tenant_dir"], created_at=row["created_at"],
                last_login_at=row["last_login_at"], disabled_at=row["disabled_at"], plan=row["plan"],
                plan_expires_at=row["plan_expires_at"], is_test=bool(row["is_test"]))


# ---------------------------------------------------------------------------- users

def migrate_owner(owner_dir: Optional[Path] = None) -> User:
    """User 1 = the owner, pointing at the existing folder in place (§5): no file moves.
    Idempotent: an existing user 1 is returned as it is. The owner backs ~/.wko5coach up first
    (docs/deploy/accounts.md)."""
    init()
    from backend import tenancy
    d = Path(owner_dir) if owner_dir is not None else tenancy.home_root()
    with _LOCK, connect() as con:
        row = con.execute("SELECT * FROM users WHERE id = 1").fetchone()
        if row is not None:
            if row["role"] != OWNER:
                raise ValueError("user 1 exists and is not the owner")
            return _user(row)
        con.execute("INSERT INTO users(id, role, tenant_dir, created_at, display_name) "
                    "VALUES (1, ?, ?, ?, 'owner')", (OWNER, str(d.resolve()), _iso(_now())))
        return _user(con.execute("SELECT * FROM users WHERE id = 1").fetchone())


def create_user(email: Optional[str] = None, google_sub: Optional[str] = None,
                display_name: Optional[str] = None, role: str = USER, email_verified: bool = False,
                cap: Optional[int] = None) -> Optional[User]:
    """A new user and its tenant folder name (users/<id>; the folder itself is made by
    tenants.ensure_ready on first use). The owner is user 1 (migrate_owner), never made here.
    `cap`: None (made) when the server already holds that many users — counted in the same
    write transaction as the insert, so two sign-ups can't both take the last place (SP-58)."""
    if role not in ROLES or role == OWNER:
        raise ValueError(f"role must be {USER!r} (the owner is user 1, migrate_owner)")
    init()
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if cap is not None and con.execute("SELECT count(*) FROM users WHERE is_test = 0").fetchone()[0] >= cap:
            return None
        if con.execute("SELECT 1 FROM users WHERE id = 1").fetchone() is None:
            # keep id 1 for the owner even when a user signs up before the migration ran
            if con.execute("SELECT 1 FROM sqlite_sequence WHERE name = 'users'").fetchone() is None:
                con.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('users', 1)")
        cur = con.execute("INSERT INTO users(role, google_sub, email, email_verified, display_name, created_at) "
                          "VALUES (?, ?, ?, ?, ?, ?)",
                          (role, google_sub, email, int(bool(email_verified)), display_name, _iso(_now())))
        uid = int(cur.lastrowid)
        con.execute("UPDATE users SET tenant_dir = ? WHERE id = ?", (f"users/{uid}", uid))
        if google_sub:
            con.execute("INSERT INTO identities(user_id, provider, subject, email, created_at) VALUES (?, ?, ?, ?, ?)",
                        (uid, "google", google_sub, email, _iso(_now())))
        return _user(con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def get_user(uid: int) -> Optional[User]:
    init()
    with connect() as con:
        return _user(con.execute("SELECT * FROM users WHERE id = ?", (int(uid),)).fetchone())


def user_by_google_sub(sub: str) -> Optional[User]:
    init()
    with connect() as con:
        return _user(con.execute("SELECT * FROM users WHERE google_sub = ?", (sub,)).fetchone())


def users(include_disabled: bool = False, include_test: bool = False) -> list[User]:
    """The users, by id. Test accounts (SP-611) only with include_test: everything that walks the
    users (the scheduler, the warm-up, the research pool, the backup) leaves them out."""
    init()
    where = [w for w, on in (("disabled_at IS NULL", not include_disabled), ("is_test = 0", not include_test)) if on]
    with connect() as con:
        q = "SELECT * FROM users" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
        return [_user(r) for r in con.execute(q)]


def count_users(role: Optional[str] = None, include_test: bool = False) -> int:
    """How many users (the member cap's count: never a test account, SP-611)."""
    init()
    test = "" if include_test else " AND is_test = 0"
    with connect() as con:
        if role:
            return con.execute("SELECT count(*) FROM users WHERE role = ?" + test, (role,)).fetchone()[0]
        return con.execute("SELECT count(*) FROM users WHERE 1 = 1" + test).fetchone()[0]


# ---------------------------------------------------------------------------- test accounts (SP-611)

def create_test_user(display_name: str, meta: dict) -> User:
    """A test account an admin makes: no e-mail, no Google account, never counted by the cap."""
    import json
    init()
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM users WHERE id = 1").fetchone() is None:
            if con.execute("SELECT 1 FROM sqlite_sequence WHERE name = 'users'").fetchone() is None:
                con.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('users', 1)")
        cur = con.execute("INSERT INTO users(role, display_name, created_at, is_test, test_meta) VALUES (?, ?, ?, 1, ?)",
                          (USER, display_name, _iso(_now()), json.dumps(meta, sort_keys=True)))
        uid = int(cur.lastrowid)
        con.execute("UPDATE users SET tenant_dir = ? WHERE id = ?", (f"users/{uid}", uid))
        return _user(con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def test_meta(uid: int) -> dict:
    import json
    init()
    with connect() as con:
        r = con.execute("SELECT test_meta FROM users WHERE id = ?", (int(uid),)).fetchone()
    try:
        return json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        return {}


def update_test_meta(uid: int, **changes) -> dict:
    """Merge `changes` into a user's test_meta (read and written in one transaction)."""
    import json
    init()
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        r = con.execute("SELECT test_meta FROM users WHERE id = ?", (int(uid),)).fetchone()
        if r is None:
            return {}
        try:
            meta = json.loads(r[0]) if r[0] else {}
        except ValueError:
            meta = {}
        meta.update(changes)
        con.execute("UPDATE users SET test_meta = ? WHERE id = ?", (json.dumps(meta, sort_keys=True), int(uid)))
        return meta


def set_test(uid: int, on: bool, meta: Optional[dict] = None) -> None:
    """Mark an account a test account (or not). Never the owner; its sign-in links go when unmarked."""
    import json
    if int(uid) == 1:
        raise ValueError("the owner is never a test account")
    init()
    with _LOCK, connect() as con:
        con.execute("UPDATE users SET is_test = ?, test_meta = ? WHERE id = ? AND role != ?",
                    (int(bool(on)), json.dumps(meta, sort_keys=True) if on and meta is not None else None,
                     int(uid), OWNER))
        if not on:
            con.execute("DELETE FROM test_login_links WHERE user_id = ?", (int(uid),))


def put_test_login(token: str, uid: int, created_by: Optional[int], ttl_s: int) -> str:
    """A one-time sign-in link's token kept by its sha256 (expired ones dropped here); returns when
    it ends (ISO)."""
    init()
    now = _now()
    exp = _iso(now + dt.timedelta(seconds=ttl_s))
    with _LOCK, connect() as con:
        con.execute("DELETE FROM test_login_links WHERE expires_at <= ?", (_iso(now),))
        con.execute("INSERT INTO test_login_links(token_hash, user_id, created_by, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)", (_hash(token), int(uid), created_by, _iso(now), exp))
    return exp


def peek_test_login(token: Optional[str]) -> Optional[User]:
    """The user a live link opens (not taken; None: unknown, expired, not a test account)."""
    if not token:
        return None
    init()
    with connect() as con:
        r = con.execute("SELECT l.expires_at AS l_exp, u.* FROM test_login_links l JOIN users u ON u.id = l.user_id "
                        "WHERE l.token_hash = ?", (_hash(token),)).fetchone()
    if r is None or dt.datetime.fromisoformat(r["l_exp"]) <= _now() or not r["is_test"]:
        return None
    return _user(r)


def take_test_login(token: Optional[str]) -> Optional[User]:
    """Take a link once (deleted whether still valid or not): its user when the link was live and
    the user is still a test account; None otherwise."""
    if not token:
        return None
    init()
    h = _hash(token)
    with _LOCK, connect() as con:
        r = con.execute("SELECT l.expires_at AS l_exp, u.* FROM test_login_links l JOIN users u ON u.id = l.user_id "
                        "WHERE l.token_hash = ?", (h,)).fetchone()
        if r is None or con.execute("DELETE FROM test_login_links WHERE token_hash = ?", (h,)).rowcount != 1:
            return None
    if dt.datetime.fromisoformat(r["l_exp"]) <= _now() or not r["is_test"]:
        return None
    return _user(r)


def count_test_logins(uid: int) -> int:
    init()
    with connect() as con:
        return int(con.execute("SELECT count(*) FROM test_login_links WHERE user_id = ? AND expires_at > ?",
                               (int(uid), _iso(_now()))).fetchone()[0])


def set_disabled(uid: int, disabled: bool, reason: Optional[str] = None) -> None:
    """Disable (sessions dropped: signed out everywhere) or enable a user. Never the owner."""
    if int(uid) == 1 and disabled:
        raise ValueError("the owner cannot be disabled")
    init()
    with _LOCK, connect() as con:
        con.execute("UPDATE users SET disabled_at = ?, disabled_reason = ? WHERE id = ?",
                    (_iso(_now()) if disabled else None, reason if disabled else None, int(uid)))
        if disabled:
            con.execute("DELETE FROM sessions WHERE user_id = ?", (int(uid),))


class LastAdmin(ValueError):
    """Taking this admin away would leave the site without one (SP-351 R2)."""


def _other_admins(con: sqlite3.Connection, uid: int) -> int:
    # a test account (SP-611) is never the admin the site falls back on
    return con.execute("SELECT count(*) FROM users WHERE id != ? AND disabled_at IS NULL AND is_test = 0 "
                       "AND (role = ? OR admin = 1)", (int(uid), OWNER)).fetchone()[0]


def set_admin(uid: int, on: bool) -> None:
    """Make a user an admin, or take it back (SP-351 R2). The owner is always an admin
    (ValueError); taking the last admin away raises LastAdmin — counted in the same write
    transaction, so two admins can't take each other's away at once."""
    init()
    uid = int(uid)
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT role, admin FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            raise KeyError(uid)
        if row["role"] == OWNER:
            if not on:
                raise ValueError("the owner is always an admin")
            return
        if not on and row["admin"] and _other_admins(con, uid) == 0:
            raise LastAdmin(uid)
        con.execute("UPDATE users SET admin = ? WHERE id = ?", (int(bool(on)), uid))


def is_last_admin(uid: int) -> bool:
    """This user is an admin and nobody else (enabled) is (deleting them is refused, SP-351 R2)."""
    init()
    with connect() as con:
        row = con.execute("SELECT role, admin FROM users WHERE id = ?", (int(uid),)).fetchone()
        if row is None or not (row["role"] == OWNER or row["admin"]):
            return False
        return _other_admins(con, int(uid)) == 0


def claim_owner_google(sub: str, email: Optional[str] = None) -> bool:
    """Link a Google account to user 1, the owner, only while user 1 has none (SP-58): one
    conditional UPDATE, so two sign-ins racing can't both take it and a later account never
    replaces the first. False when user 1 is missing, already linked, or the sub is someone's."""
    init()
    try:
        with _LOCK, connect() as con:
            cur = con.execute("UPDATE users SET google_sub = ?, email = coalesce(?, email), email_verified = 1 "
                              "WHERE id = 1 AND role = ? AND google_sub IS NULL", (sub, email, OWNER))
            if cur.rowcount != 1:
                return False
            con.execute("INSERT INTO identities(user_id, provider, subject, email, created_at) "
                        "VALUES (1, 'google', ?, ?, ?)", (sub, email, _iso(_now())))
            return True
    except sqlite3.IntegrityError:          # the sub belongs to another user already
        return False


def find_identity(provider: str, subject: str) -> Optional[User]:
    init()
    with connect() as con:
        row = con.execute("SELECT u.* FROM identities i JOIN users u ON u.id = i.user_id "
                          "WHERE i.provider = ? AND i.subject = ?", (provider, subject)).fetchone()
        return _user(row)


# ---------------------------------------------------------------------------- sessions

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(uid: int, days: int = SESSION_DAYS) -> str:
    """A new session for a user: the cookie value (only its sha256 is stored)."""
    init()
    token = _secrets.token_urlsafe(32)
    now = _now()
    with _LOCK, connect() as con:
        con.execute("INSERT INTO sessions(token_hash, user_id, created_at, last_seen_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)", (_hash(token), int(uid), _iso(now), _iso(now),
                                               _iso(now + dt.timedelta(days=days))))
        con.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(now), int(uid)))
    return token


def session_user(token: Optional[str], now: Optional[dt.datetime] = None) -> Optional[User]:
    """The user of a live session (not expired, user not disabled); slides the expiry at most
    once an hour. None otherwise."""
    found = session_lookup(token, now)
    return found[0] if found else None


def session_lookup(token: Optional[str], now: Optional[dt.datetime] = None) -> Optional[tuple]:
    """(user, renew) of a live session — renew: the cookie's new Max-Age in seconds when the
    expiry was just moved ahead (at most once an hour, SESSION_DAYS from now but never past
    SESSION_MAX_DAYS after the sign-in), else None. None for no / an expired or too old (deleted
    here) / a disabled user's session."""
    if not token:
        return None
    init()
    now = now or _now()
    h = _hash(token)
    with connect() as con:
        row = con.execute("SELECT s.expires_at, s.last_seen_at, s.created_at AS s_created, u.* FROM sessions s "
                          "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (h,)).fetchone()
        if row is None:
            return None
        end = dt.datetime.fromisoformat(row["s_created"]) + dt.timedelta(days=SESSION_MAX_DAYS)
        if dt.datetime.fromisoformat(row["expires_at"]) <= now or end <= now:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))
            return None
        u = _user(row)
        if u.disabled:
            return None
        renew = None
        if now - dt.datetime.fromisoformat(row["last_seen_at"]) >= dt.timedelta(hours=1):
            until = min(now + dt.timedelta(days=SESSION_DAYS), end)
            con.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                        (_iso(now), _iso(until), h))
            renew = int((until - now).total_seconds())
        return u, renew


def last_seen() -> dict[int, str]:
    """user id -> the newest last_seen_at of its sessions (ISO): a session slides at most once an
    hour while it is used, so a user who stays signed in shows up here, not in last_login_at."""
    init()
    with connect() as con:
        return {int(r[0]): r[1] for r in con.execute("SELECT user_id, MAX(last_seen_at) FROM sessions GROUP BY user_id")
                if r[1]}


def mark_reauth(token: Optional[str], uid: int) -> bool:
    """This session's user just signed in at Google again (SP-386): noted on this session only.
    False when the cookie is not a session of `uid`."""
    if not token:
        return False
    init()
    with _LOCK, connect() as con:
        cur = con.execute("UPDATE sessions SET reauth_at = ? WHERE token_hash = ? AND user_id = ?",
                          (_iso(_now()), _hash(token), int(uid)))
        return cur.rowcount == 1


def reauth_at(token: Optional[str]) -> Optional[dt.datetime]:
    """When this session's user last confirmed who they are at Google (None: never)."""
    if not token:
        return None
    init()
    with connect() as con:
        r = con.execute("SELECT reauth_at FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
    return dt.datetime.fromisoformat(r[0]) if r and r[0] else None


def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    init()
    with _LOCK, connect() as con:
        con.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


# ---------------------------------------------------------------------------- shares, waitlist

def add_share(share_id: str, uid: int, kind: str = "racepower") -> None:
    init()
    with _LOCK, connect() as con:
        con.execute("INSERT OR REPLACE INTO shares(id, user_id, kind, created_at) VALUES (?, ?, ?, ?)",
                    (share_id, int(uid), kind, _iso(_now())))


def share_owner(share_id: str) -> Optional[User]:
    init()
    with connect() as con:
        return _user(con.execute("SELECT u.* FROM shares s JOIN users u ON u.id = s.user_id WHERE s.id = ?",
                                 (share_id,)).fetchone())


def remove_share(share_id: str) -> None:
    init()
    with _LOCK, connect() as con:
        con.execute("DELETE FROM shares WHERE id = ?", (share_id,))


def add_waitlist(email: str, note: Optional[str] = None) -> bool:
    """True when added (False: already waiting)."""
    init()
    with _LOCK, connect() as con:
        cur = con.execute("INSERT OR IGNORE INTO waitlist(email, created_at, note) VALUES (?, ?, ?)",
                          (email.strip().lower(), _iso(_now()), note))
        return cur.rowcount > 0


def waitlist() -> list[dict]:
    init()
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM waitlist ORDER BY id")]


def admit_waitlisted(wid: int) -> Optional[User]:
    """An admin lets one waitlisted person in (SP-351): their account is made now, past the cap,
    with the e-mail Google had verified when they were put on the list and no Google account
    linked yet; the row is marked invited_at. They sign in with that e-mail (link_admitted_google)
    and accept the terms like anyone new. None when the row is gone or already let in; the
    existing user when that e-mail already has an account."""
    init()
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM waitlist WHERE id = ?", (int(wid),)).fetchone()
        if row is None or row["invited_at"]:
            return None
        email = row["email"].strip().lower()
        con.execute("UPDATE waitlist SET invited_at = ? WHERE id = ?", (_iso(_now()), int(wid)))
        had = con.execute("SELECT * FROM users WHERE lower(email) = ? ORDER BY id LIMIT 1", (email,)).fetchone()
        if had is not None:
            return _user(had)
        if con.execute("SELECT 1 FROM users WHERE id = 1").fetchone() is None:
            if con.execute("SELECT 1 FROM sqlite_sequence WHERE name = 'users'").fetchone() is None:
                con.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('users', 1)")
        cur = con.execute("INSERT INTO users(role, email, email_verified, created_at) VALUES (?, ?, 1, ?)",
                          (USER, email, _iso(_now())))
        uid = int(cur.lastrowid)
        con.execute("UPDATE users SET tenant_dir = ? WHERE id = ?", (f"users/{uid}", uid))
        return _user(con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def link_admitted_google(sub: str, email: Optional[str]) -> Optional[User]:
    """The first sign-in of a person an admin let in (admit_waitlisted): their verified Google
    e-mail matches an account that has no Google account yet (never the owner) -> linked, one
    conditional UPDATE (two sign-ins racing can't both take it). None otherwise."""
    if not email:
        return None
    init()
    try:
        with _LOCK, connect() as con:
            row = con.execute("SELECT id FROM users WHERE lower(email) = ? AND google_sub IS NULL AND role = ? "
                              "AND id != 1 ORDER BY id LIMIT 1", (email.strip().lower(), USER)).fetchone()
            if row is None:
                return None
            uid = int(row[0])
            cur = con.execute("UPDATE users SET google_sub = ? WHERE id = ? AND google_sub IS NULL", (sub, uid))
            if cur.rowcount != 1:
                return None
            con.execute("INSERT INTO identities(user_id, provider, subject, email, created_at) "
                        "VALUES (?, 'google', ?, ?, ?)", (uid, sub, email, _iso(_now())))
            return _user(con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())
    except sqlite3.IntegrityError:          # the sub belongs to another user already
        return None


# ---------------------------------------------------------------------------- consents (SP-58)

# the rows that say which terms were accepted and the analysis state (a link row says neither)
_TERMS_ROWS = "action IN ('terms', 'analysis')"

def record_consent(uid: int, version: str, documents: dict, analysis: bool = True) -> None:
    """One acceptance of the terms: the version, {document id: sha256 of the text shown}, and the
    cross-user analysis that is bundled with them (not a separate tick, SP-58)."""
    import json
    init()
    with _LOCK, connect() as con:
        con.execute("INSERT INTO consents(user_id, terms_version, documents, analysis, accepted_at, action) "
                    "VALUES (?, ?, ?, ?, ?, 'terms')",
                    (int(uid), version, json.dumps(documents, sort_keys=True), int(bool(analysis)), _iso(_now())))


def analysis_consent(uid: int) -> Optional[bool]:
    """The cross-user analysis consent as the last consents row has it (None: no row — the owner)."""
    init()
    with connect() as con:
        r = con.execute(f"SELECT analysis FROM consents WHERE user_id = ? AND {_TERMS_ROWS} "
                        "ORDER BY id DESC LIMIT 1",
                        (int(uid),)).fetchone()
    return None if r is None else bool(r[0])


def record_analysis(uid: int, analysis: bool) -> None:
    """The analysis consent withdrawn (False) or given again (True) in 設定 (SP-386; SP-326 §7.1):
    one more row, action "analysis", the last row's terms version and documents copied (the terms
    stay accepted). ValueError for a user who never accepted the terms (the owner)."""
    init()
    with _LOCK, connect() as con:
        last = con.execute(f"SELECT terms_version, documents FROM consents WHERE user_id = ? AND {_TERMS_ROWS} "
                           "ORDER BY id DESC LIMIT 1",
                           (int(uid),)).fetchone()
        if last is None:
            raise ValueError(f"user {uid} has accepted no terms")
        con.execute("INSERT INTO consents(user_id, terms_version, documents, analysis, accepted_at, action) "
                    "VALUES (?, ?, ?, ?, ?, 'analysis')",
                    (int(uid), last[0], last[1], int(bool(analysis)), _iso(_now())))


# the research pool's layers (SP-326 Q1-2; SP-586). The B layer (de-identified detail) needs its own
# tick box, never pre-ticked: a consents row action "research_b" whose `analysis` is the state. SP-58
# writes it (the sign-up's second tick box, the 設定 switch) — not built yet, so nobody has one and
# the B export takes nobody. The A layer (k-gated aggregates, opt-out) follows the analysis consent
# above.
RESEARCH_B = "research_b"


def research_b_consent(uid: int) -> bool:
    """True only when the user's last research_b row grants it; no row = no consent. Never a test
    account (SP-611)."""
    init()
    with connect() as con:
        if con.execute("SELECT 1 FROM users WHERE id = ? AND is_test = 1", (int(uid),)).fetchone():
            return False
        r = con.execute("SELECT analysis FROM consents WHERE user_id = ? AND action = ? ORDER BY id DESC LIMIT 1",
                        (int(uid), RESEARCH_B)).fetchone()
    return bool(r and r[0])


def research_a_allowed(user: "User") -> bool:
    """The A layer: informed, opt-out (privacy policy + the 設定 switch). The analysis consent's
    last row; the owner (no row) is the operator and his own data subject. Never a test account
    (SP-611: made-up data)."""
    if user.is_test:
        return False
    a = analysis_consent(user.id)
    return bool(a) if a is not None else user.is_owner


def research_link(uid: int) -> Optional[dict]:
    init()
    with connect() as con:
        r = con.execute("SELECT * FROM research_links WHERE user_id = ?", (int(uid),)).fetchone()
    return dict(r) if r else None


def research_link_create(uid: int, donor_id: str, day0: dt.date) -> dict:
    """The user's link, made once: an existing one is returned unchanged."""
    init()
    with _LOCK, connect() as con:
        con.execute("INSERT OR IGNORE INTO research_links(user_id, donor_id, day0, created_at) VALUES (?, ?, ?, ?)",
                    (int(uid), donor_id, day0.isoformat(), _iso(_now())))
        return dict(con.execute("SELECT * FROM research_links WHERE user_id = ?", (int(uid),)).fetchone())


def research_links() -> list[dict]:
    init()
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM research_links ORDER BY user_id")]


def research_link_delete(uid: int) -> int:
    init()
    with _LOCK, connect() as con:
        return con.execute("DELETE FROM research_links WHERE user_id = ?", (int(uid),)).rowcount


def research_key(name: str) -> bytes:
    """A secret of the pool (made once, 32 random bytes)."""
    init()
    with _LOCK, connect() as con:
        con.execute("INSERT OR IGNORE INTO research_keys(name, value, created_at) VALUES (?, ?, ?)",
                    (name, _secrets.token_hex(32), _iso(_now())))
        return bytes.fromhex(con.execute("SELECT value FROM research_keys WHERE name = ?", (name,)).fetchone()[0])


def consent_version(uid: int) -> Optional[str]:
    """The terms version the user accepted last (None: never)."""
    init()
    with connect() as con:
        r = con.execute(f"SELECT terms_version FROM consents WHERE user_id = ? AND {_TERMS_ROWS} "
                        "ORDER BY id DESC LIMIT 1",
                        (int(uid),)).fetchone()
        return r[0] if r else None


def record_link(uid: int, client: str, linked: bool, scopes: tuple = ()) -> None:
    """Another project (`client`) allowed to use this account (linked, with `scopes`) or no longer
    (SP-329): one more consents row, action "link" / "unlink"; the last terms version and analysis
    state copied so the row reads alike (the owner, who accepts no terms: an empty version). An
    unlink also deletes every service token of that client for this user, in the same transaction."""
    import json
    init()
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        last = con.execute(f"SELECT terms_version, analysis FROM consents WHERE user_id = ? AND {_TERMS_ROWS} "
                           "ORDER BY id DESC LIMIT 1", (int(uid),)).fetchone()
        con.execute("INSERT INTO consents(user_id, terms_version, documents, analysis, accepted_at, action, client) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(uid), last[0] if last else "", json.dumps({"scopes": sorted(set(scopes)) if linked else []}),
                     int(last[1]) if last else 0, _iso(_now()), "link" if linked else "unlink", client))
        if not linked:
            con.execute("DELETE FROM api_tokens WHERE user_id = ? AND client = ?", (int(uid), client))


def link_state(uid: int, client: str) -> Optional[dict]:
    """The last link / unlink row for `client`: {linked, at, scopes}; None = never asked."""
    import json
    init()
    with connect() as con:
        r = con.execute("SELECT action, accepted_at, documents FROM consents WHERE user_id = ? AND client = ? "
                        "AND action IN ('link', 'unlink') ORDER BY id DESC LIMIT 1", (int(uid), client)).fetchone()
    if r is None:
        return None
    try:
        scopes = tuple(json.loads(r["documents"] or "{}").get("scopes") or ())
    except (ValueError, AttributeError):
        scopes = ()
    return {"linked": r["action"] == "link", "at": r["accepted_at"], "scopes": scopes}


def consents(uid: int) -> list[dict]:
    init()
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM consents WHERE user_id = ? ORDER BY id", (int(uid),))]


# ---------------------------------------------------------------------------- sign-in in progress (SP-58)

def put_oauth_state(state: str, binding: str, provider: str, nonce: str, code_verifier: str, redirect_uri: str,
                    next_url: str, ttl_s: int, purpose: str = "signin", user_id: Optional[int] = None) -> None:
    """A sign-in started: kept by the sha256 of its state (the state itself travels in URLs) and
    bound to the browser by the sha256 of its binding cookie. Expired rows are dropped here.
    purpose "reauth" (SP-386): the signed-in user `user_id` confirming who they are."""
    init()
    now = _now()
    with _LOCK, connect() as con:
        con.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (_iso(now),))
        con.execute("INSERT INTO oauth_states(state_hash, binding_hash, provider, nonce, code_verifier, redirect_uri, "
                    "next_url, expires_at, purpose, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_hash(state), _hash(binding), provider, nonce, code_verifier, redirect_uri, next_url,
                     _iso(now + dt.timedelta(seconds=ttl_s)), purpose, user_id))


def pop_oauth_state(state: str) -> Optional[dict]:
    """Take a sign-in's state once (deleted whether it is still valid or not); None when unknown
    or expired."""
    if not state:
        return None
    init()
    h = _hash(state)
    with _LOCK, connect() as con:
        row = con.execute("SELECT * FROM oauth_states WHERE state_hash = ?", (h,)).fetchone()
        # taken once: only the request whose DELETE removed the row may use it
        if row is None or con.execute("DELETE FROM oauth_states WHERE state_hash = ?", (h,)).rowcount != 1:
            return None
    out = dict(row)
    if dt.datetime.fromisoformat(out["expires_at"]) <= _now():
        return None
    return out


def binding_matches(row: dict, binding: Optional[str]) -> bool:
    """The callback came back to the browser that started the sign-in (its binding cookie)."""
    return bool(binding) and _secrets.compare_digest(_hash(binding), row.get("binding_hash") or "")


def put_signup_ticket(token: str, kind: str, provider: str, subject: str, email: Optional[str],
                      display_name: Optional[str], user_id: Optional[int], next_url: str, ttl_s: int) -> None:
    """Signed in at Google, now at the terms: kind signup (a new person) or reconsent (user_id)."""
    init()
    now = _now()
    with _LOCK, connect() as con:
        con.execute("DELETE FROM signup_tickets WHERE expires_at <= ?", (_iso(now),))
        con.execute("INSERT INTO signup_tickets(token_hash, kind, provider, subject, email, display_name, user_id, "
                    "next_url, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_hash(token), kind, provider, subject, email, display_name, user_id, next_url,
                     _iso(now + dt.timedelta(seconds=ttl_s))))


def signup_ticket(token: Optional[str]) -> Optional[dict]:
    """A live sign-up ticket (not taken)."""
    if not token:
        return None
    init()
    with connect() as con:
        row = con.execute("SELECT * FROM signup_tickets WHERE token_hash = ?", (_hash(token),)).fetchone()
    if row is None or dt.datetime.fromisoformat(row["expires_at"]) <= _now():
        return None
    return dict(row)


def take_signup_ticket(token: Optional[str]) -> Optional[dict]:
    """The ticket, deleted (single use); None when unknown or expired."""
    if not token:
        return None
    init()
    h = _hash(token)
    with _LOCK, connect() as con:
        row = con.execute("SELECT * FROM signup_tickets WHERE token_hash = ?", (h,)).fetchone()
        if row is None or con.execute("DELETE FROM signup_tickets WHERE token_hash = ?", (h,)).rowcount != 1:
            return None
    out = dict(row)
    return None if dt.datetime.fromisoformat(out["expires_at"]) <= _now() else out


# ---------------------------------------------------------------------------- plans, usage (SP-330)

def set_plan(uid: int, plan: str, expires_at: Optional[str] = None) -> None:
    """free | subscriber, with the time it ends (None = open-ended). Payment (SP-328) calls it."""
    if plan not in ("free", "subscriber"):
        raise ValueError("plan must be free or subscriber")
    init()
    with _LOCK, connect() as con:
        con.execute("UPDATE users SET plan = ?, plan_expires_at = ? WHERE id = ?", (plan, expires_at, int(uid)))


def usage_get(uid: int, key: str, period: str) -> int:
    init()
    with connect() as con:
        r = con.execute("SELECT used FROM usage WHERE user_id = ? AND key = ? AND period = ?",
                        (int(uid), key, period)).fetchone()
        return int(r[0]) if r else 0


def usage_add(uid: int, key: str, period: str, limit: int, item: Optional[str] = None) -> Optional[int]:
    """Count one use (a distinct `item` once per period); the new count, or None when it would
    pass `limit` (nothing counted then). Counters of older periods are left as history."""
    init()
    with _LOCK, connect() as con:
        if item is not None and con.execute(
                "SELECT 1 FROM usage_items WHERE user_id = ? AND key = ? AND period = ? AND item = ?",
                (int(uid), key, period, item)).fetchone():
            return usage_get_con(con, uid, key, period)
        used = usage_get_con(con, uid, key, period)
        if used >= limit:
            return None
        con.execute("INSERT INTO usage(user_id, key, period, used) VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(user_id, key, period) DO UPDATE SET used = used + 1", (int(uid), key, period))
        if item is not None:
            con.execute("INSERT OR IGNORE INTO usage_items(user_id, key, period, item) VALUES (?, ?, ?, ?)",
                        (int(uid), key, period, item))
        return used + 1


def usage_get_con(con, uid: int, key: str, period: str) -> int:
    r = con.execute("SELECT used FROM usage WHERE user_id = ? AND key = ? AND period = ?",
                    (int(uid), key, period)).fetchone()
    return int(r[0]) if r else 0


def create_service_token(uid: int, client: str, scopes: list, days: Optional[int] = 365) -> str:
    """A token another project uses to call this API as `uid` with `scopes` (link:*)."""
    init()
    token = "trc_" + _secrets.token_urlsafe(32)
    now = _now()
    with _LOCK, connect() as con:
        con.execute("INSERT INTO api_tokens(token_hash, user_id, client, scopes, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (_hash(token), int(uid), client, " ".join(sorted(set(scopes))), _iso(now),
                     _iso(now + dt.timedelta(days=days)) if days else None))
    return token


def service_token(token: str, now: Optional[dt.datetime] = None) -> Optional[tuple]:
    """(user, scopes) of a live service token, else None."""
    got = service_token_full(token, now)
    return None if got is None else got[:2]


def service_token_full(token: str, now: Optional[dt.datetime] = None) -> Optional[tuple]:
    """(user, scopes, client) of a live service token, else None (unknown, expired, user disabled)."""
    init()
    now = now or _now()
    with connect() as con:
        row = con.execute("SELECT t.scopes AS t_scopes, t.expires_at AS t_exp, t.client AS t_client, u.* "
                          "FROM api_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?",
                          (_hash(token),)).fetchone()
    if row is None:
        return None
    if row["t_exp"] and dt.datetime.fromisoformat(row["t_exp"]) <= now:
        return None
    u = _user(row)
    if u.disabled:
        return None
    return u, frozenset((row["t_scopes"] or "").split()), row["t_client"]


def replace_service_token(uid: int, client: str, scopes: list, days: int) -> tuple[str, str]:
    """A new token of `client` for `uid` (SP-329), every older one of that client for that user
    deleted in the same transaction (one live token per user and project). (token, expires_at)."""
    init()
    token = "trc_" + _secrets.token_urlsafe(32)
    now = _now()
    exp = _iso(now + dt.timedelta(days=days))
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM api_tokens WHERE user_id = ? AND client = ?", (int(uid), client))
        con.execute("INSERT INTO api_tokens(token_hash, user_id, client, scopes, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (_hash(token), int(uid), client, " ".join(sorted(set(scopes))), _iso(now), exp))
    return token, exp


def count_service_tokens(uid: int, client: str) -> int:
    init()
    with connect() as con:
        return int(con.execute("SELECT count(*) FROM api_tokens WHERE user_id = ? AND client = ?",
                               (int(uid), client)).fetchone()[0])


def revoke_service_token(token: str) -> None:
    init()
    with _LOCK, connect() as con:
        con.execute("DELETE FROM api_tokens WHERE token_hash = ?", (_hash(token),))


def register_token_hash(uid: int, client: str, scopes: list, token_hash: str) -> None:
    """A token made and kept elsewhere (the weight API's, SP-472: its row lives in the user's own
    tenant DB): only its sha256, so the login gate can find whose it is. No expiry."""
    init()
    with _LOCK, connect() as con:
        con.execute("INSERT OR REPLACE INTO api_tokens(token_hash, user_id, client, scopes, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, NULL)",
                    (token_hash, int(uid), client, " ".join(sorted(set(scopes))), _iso(_now())))


def revoke_token_hashes(uid: int, hashes: list) -> None:
    init()
    with _LOCK, connect() as con:
        con.executemany("DELETE FROM api_tokens WHERE token_hash = ? AND user_id = ?",
                        [(h, int(uid)) for h in hashes])


# ---------------------------------------------------------------------------- subscriptions (SP-328)

EVENTS_KEEP_DAYS = 400


def billing_row(uid: int) -> Optional[dict]:
    init()
    with connect() as con:
        r = con.execute("SELECT * FROM billing_subscriptions WHERE user_id = ?", (int(uid),)).fetchone()
    return dict(r) if r else None


def billing_event_seen(event_id: str) -> bool:
    init()
    with connect() as con:
        return con.execute("SELECT 1 FROM billing_events WHERE id = ?", (event_id,)).fetchone() is not None


def billing_apply(uid: Optional[int], source_ts: str, compute, event_id: Optional[str] = None,
                  event_type: Optional[str] = None, now: Optional[dt.datetime] = None) -> dict:
    """Apply one Polar customer state in one transaction (backend/billing/service.py):
    `compute(prev_row, user) -> entitlement.Outcome`. A webhook id already recorded changes nothing
    ({"duplicate": True}); a state older than the one applied last is recorded, not applied
    ({"stale": True}); the event row is written with the change, so a failure leaves no trace and
    Polar's retry is applied. {"applied", "plan", "expires_at"}."""
    init()
    now = now or _now()
    out: dict = {"applied": 0}
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if event_id and con.execute("SELECT 1 FROM billing_events WHERE id = ?", (event_id,)).fetchone():
            return {"applied": 0, "duplicate": True}
        user = _user(con.execute("SELECT * FROM users WHERE id = ?", (int(uid),)).fetchone()) if uid else None
        if user is not None:
            r = con.execute("SELECT * FROM billing_subscriptions WHERE user_id = ?", (user.id,)).fetchone()
            prev = dict(r) if r else None
            if prev and prev.get("source_ts") and source_ts < prev["source_ts"]:
                out["stale"] = True
            else:
                res = compute(prev, user)
                if res.row is not None:
                    from backend.billing.entitlement import FIELDS
                    row = {k: res.row.get(k) for k in FIELDS}
                    row["user_id"] = user.id
                    cols = ", ".join(FIELDS)
                    con.execute(f"INSERT INTO billing_subscriptions({cols}) VALUES ({', '.join('?' * len(FIELDS))}) "
                                f"ON CONFLICT(user_id) DO UPDATE SET "
                                + ", ".join(f"{k} = excluded.{k}" for k in FIELDS if k != "user_id"),
                                tuple(row[k] for k in FIELDS))
                    out["applied"] = 1
                if res.plan is not None:
                    con.execute("UPDATE users SET plan = ?, plan_expires_at = ? WHERE id = ?",
                                (res.plan[0], res.plan[1], user.id))
                    out["applied"] = 1
                    out["plan"], out["expires_at"] = res.plan
        if event_id:
            con.execute("INSERT INTO billing_events(id, type, user_id, event_ts, received_at, applied) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (event_id, event_type or "unknown", user.id if user else None, source_ts, _iso(now),
                         out["applied"]))
            con.execute("DELETE FROM billing_events WHERE received_at < ?",
                        (_iso(now - dt.timedelta(days=EVENTS_KEEP_DAYS)),))
    return out


def billing_mark_synced(uid: int, at: str) -> None:
    init()
    with _LOCK, connect() as con:
        con.execute("UPDATE billing_subscriptions SET synced_at = ? WHERE user_id = ?", (at, int(uid)))


# ---------------------------------------------------------------------------- deleting an account (SP-386)

_USER_TABLES = ("identities", "sessions", "shares", "usage", "usage_items", "api_tokens", "consents",
                "billing_subscriptions", "test_login_links")


def delete_user(uid: int) -> dict:
    """Every row that names this person, in one transaction: the user row (identities, sessions,
    shares, usage, usage_items, api_tokens, consents and billing_subscriptions cascade; the
    billing_events of theirs keep only the webhook id, for the duplicate check), the sign-ins in progress and
    sign-up tickets that are theirs (by user id or Google sub) and their e-mail on the waitlist.
    {table: rows deleted}. ValueError for the owner (user 1) — never deleted here."""
    init()
    uid = int(uid)
    with _LOCK, connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if row is None:
            return {}
        if uid == 1 or row["role"] == OWNER:
            raise ValueError("the owner cannot be deleted")
        subs = {row["google_sub"]} | {r[0] for r in con.execute("SELECT subject FROM identities WHERE user_id = ?",
                                                                (uid,))}
        subs.discard(None)
        emails = {row["email"]} | {r[0] for r in con.execute("SELECT email FROM identities WHERE user_id = ?", (uid,))}
        emails = {e.strip().lower() for e in emails if e}
        out = {}
        for t in _USER_TABLES:
            out[t] = con.execute(f"SELECT count(*) FROM {t} WHERE user_id = ?", (uid,)).fetchone()[0]
        q = lambda xs: ",".join("?" * len(xs))                      # noqa: E731
        out["signup_tickets"] = con.execute(
            f"DELETE FROM signup_tickets WHERE user_id = ? OR subject IN ({q(subs)})", (uid, *subs)).rowcount
        out["oauth_states"] = con.execute("DELETE FROM oauth_states WHERE user_id = ?", (uid,)).rowcount
        out["waitlist"] = con.execute(f"DELETE FROM waitlist WHERE lower(email) IN ({q(emails)})",
                                      tuple(emails)).rowcount if emails else 0
        out["billing_events"] = con.execute("UPDATE billing_events SET user_id = NULL WHERE user_id = ?",
                                            (uid,)).rowcount
        # the research pool's link (SP-586): deletion.py withdrew the pool rows already
        out["research_links"] = con.execute("DELETE FROM research_links WHERE user_id = ?", (uid,)).rowcount
        out["users"] = con.execute("DELETE FROM users WHERE id = ?", (uid,)).rowcount
        left = [t for t in (*_USER_TABLES, "billing_events")
                if con.execute(f"SELECT 1 FROM {t} WHERE user_id = ? LIMIT 1", (uid,)).fetchone()]
        if left:                                                    # foreign keys off: never half done
            raise RuntimeError(f"rows left after deleting user {uid}: {left}")
        return {k: v for k, v in out.items() if v}
