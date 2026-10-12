"""
accounts.db — the server's account tables, apart from every tenant's own DB (auth-and-demo
plan §4.3; SP-384). Plain sqlite3: a few small tables read once per request (the session),
written on sign-in / sign-up.

  users       id, role (owner | user), google_sub (unique), email, display_name, tenant_dir,
              created_at, last_login_at, disabled_at / disabled_reason
              (plan / plan_expires_at: the membership, SP-330)
  identities  (provider, subject) unique; v1 only "google"
  sessions    sha256 of the cookie token only (a stolen DB opens no session), expiry sliding
  shares      public /share/<id> -> the user whose tenant holds it
  waitlist    e-mails waiting for a place once sign-up is capped (SP-58)
  usage       the free tier's counters per user / quota / period (SP-330); usage_items: what
              was counted, when a distinct item counts once a period (a chart dashboard a day)
  api_tokens  service tokens of another project (inReachLiveTrack, SP-329): sha256 only, the
              link:* scopes they carry
  consents    each acceptance of the terms (SP-58): user, terms version, the sha256 of every
              document shown, the cross-user analysis bundled with them, when
  oauth_states  a Google sign-in in progress (SP-58): sha256 of the state, sha256 of the
              browser's binding cookie, the nonce, the PKCE verifier, where to go back; 10 minutes,
              taken once
  signup_tickets  signed in at Google, waiting at the terms (SP-58): sha256 of the cookie, the
              Google account (sub, e-mail, name) or the user asked to accept new terms; 30 minutes,
              taken once

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
    plan_expires_at TEXT
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
    expires_at TEXT NOT NULL
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
    accepted_at TEXT NOT NULL
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
    expires_at TEXT NOT NULL
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

    @property
    def is_owner(self) -> bool:
        return self.role == OWNER

    @property
    def disabled(self) -> bool:
        return bool(self.disabled_at)

    def folder(self) -> Path:
        """The tenant folder: tenant_dir as stored (absolute for the owner), or relative to the
        server root (users/<id>), so the server root can move."""
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
        con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    _READY.add(key)


def _user(row: Optional[sqlite3.Row]) -> Optional[User]:
    if row is None:
        return None
    return User(id=row["id"], role=row["role"], google_sub=row["google_sub"], email=row["email"],
                display_name=row["display_name"], tenant_dir=row["tenant_dir"], created_at=row["created_at"],
                last_login_at=row["last_login_at"], disabled_at=row["disabled_at"], plan=row["plan"],
                plan_expires_at=row["plan_expires_at"])


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
                display_name: Optional[str] = None, role: str = USER, email_verified: bool = False) -> User:
    """A new user and its tenant folder name (users/<id>; the folder itself is made by
    tenants.ensure_ready on first use). The owner is user 1 (migrate_owner), never made here."""
    if role not in ROLES or role == OWNER:
        raise ValueError(f"role must be {USER!r} (the owner is user 1, migrate_owner)")
    init()
    with _LOCK, connect() as con:
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


def users(include_disabled: bool = False) -> list[User]:
    init()
    with connect() as con:
        q = "SELECT * FROM users" + ("" if include_disabled else " WHERE disabled_at IS NULL") + " ORDER BY id"
        return [_user(r) for r in con.execute(q)]


def count_users(role: Optional[str] = None) -> int:
    init()
    with connect() as con:
        if role:
            return con.execute("SELECT count(*) FROM users WHERE role = ?", (role,)).fetchone()[0]
        return con.execute("SELECT count(*) FROM users").fetchone()[0]


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


def link_google(uid: int, sub: str, email: Optional[str] = None) -> None:
    """The owner's (or a user's) Google identity, linked once at sign-in (SP-58)."""
    init()
    with _LOCK, connect() as con:
        con.execute("UPDATE users SET google_sub = ?, email = coalesce(?, email) WHERE id = ?", (sub, email, int(uid)))
        con.execute("INSERT OR IGNORE INTO identities(user_id, provider, subject, email, created_at) "
                    "VALUES (?, 'google', ?, ?, ?)", (int(uid), sub, email, _iso(_now())))


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
    """(user, slid) of a live session — slid: the expiry was just moved SESSION_DAYS ahead (at
    most once an hour; the sign-in gate then renews the cookie too). None for no / an expired
    (deleted here) / a disabled user's session."""
    if not token:
        return None
    init()
    now = now or _now()
    h = _hash(token)
    with connect() as con:
        row = con.execute("SELECT s.expires_at, s.last_seen_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                          "WHERE s.token_hash = ?", (h,)).fetchone()
        if row is None:
            return None
        if dt.datetime.fromisoformat(row["expires_at"]) <= now:
            con.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))
            return None
        u = _user(row)
        if u.disabled:
            return None
        slid = now - dt.datetime.fromisoformat(row["last_seen_at"]) >= dt.timedelta(hours=1)
        if slid:
            con.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                        (_iso(now), _iso(now + dt.timedelta(days=SESSION_DAYS)), h))
        return u, slid


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


# ---------------------------------------------------------------------------- consents (SP-58)

def record_consent(uid: int, version: str, documents: dict, analysis: bool = True) -> None:
    """One acceptance of the terms: the version, {document id: sha256 of the text shown}, and the
    cross-user analysis that is bundled with them (not a separate tick, SP-58)."""
    import json
    init()
    with _LOCK, connect() as con:
        con.execute("INSERT INTO consents(user_id, terms_version, documents, analysis, accepted_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (int(uid), version, json.dumps(documents, sort_keys=True), int(bool(analysis)), _iso(_now())))


def consent_version(uid: int) -> Optional[str]:
    """The terms version the user accepted last (None: never)."""
    init()
    with connect() as con:
        r = con.execute("SELECT terms_version FROM consents WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                        (int(uid),)).fetchone()
        return r[0] if r else None


def consents(uid: int) -> list[dict]:
    init()
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM consents WHERE user_id = ? ORDER BY id", (int(uid),))]


# ---------------------------------------------------------------------------- sign-in in progress (SP-58)

def put_oauth_state(state: str, binding: str, provider: str, nonce: str, code_verifier: str, redirect_uri: str,
                    next_url: str, ttl_s: int) -> None:
    """A sign-in started: kept by the sha256 of its state (the state itself travels in URLs) and
    bound to the browser by the sha256 of its binding cookie. Expired rows are dropped here."""
    init()
    now = _now()
    with _LOCK, connect() as con:
        con.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (_iso(now),))
        con.execute("INSERT INTO oauth_states(state_hash, binding_hash, provider, nonce, code_verifier, redirect_uri, "
                    "next_url, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (_hash(state), _hash(binding), provider, nonce, code_verifier, redirect_uri, next_url,
                     _iso(now + dt.timedelta(seconds=ttl_s))))


def pop_oauth_state(state: str) -> Optional[dict]:
    """Take a sign-in's state once (deleted whether it is still valid or not); None when unknown
    or expired."""
    if not state:
        return None
    init()
    h = _hash(state)
    with _LOCK, connect() as con:
        row = con.execute("SELECT * FROM oauth_states WHERE state_hash = ?", (h,)).fetchone()
        if row is None:
            return None
        con.execute("DELETE FROM oauth_states WHERE state_hash = ?", (h,))
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
        if row is None:
            return None
        con.execute("DELETE FROM signup_tickets WHERE token_hash = ?", (h,))
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
    init()
    now = now or _now()
    with connect() as con:
        row = con.execute("SELECT t.scopes AS t_scopes, t.expires_at AS t_exp, u.* FROM api_tokens t "
                          "JOIN users u ON u.id = t.user_id WHERE t.token_hash = ?", (_hash(token),)).fetchone()
    if row is None:
        return None
    if row["t_exp"] and dt.datetime.fromisoformat(row["t_exp"]) <= now:
        return None
    u = _user(row)
    if u.disabled:
        return None
    return u, frozenset((row["t_scopes"] or "").split())


def revoke_service_token(token: str) -> None:
    init()
    with _LOCK, connect() as con:
        con.execute("DELETE FROM api_tokens WHERE token_hash = ?", (_hash(token),))
