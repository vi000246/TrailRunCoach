"""
Accounts and user tenants (SP-384; docs/plans/auth-and-demo.plan.md §2.2, §4.3, §5).

    accounts.enabled()          WKO5COACH_ACCOUNTS=1: multi-user mode. With the whole set of sign-in
                                settings too (login.decide(), frozen at start-up) every request
                                needs a session and is bound to its user's tenant (tenancy_mw).
                                Off (the default, today's self-hosted app): every request is the
                                owner, nothing here is read.
    accounts.server_root()      where accounts.db and users/<id>/ live
    store                       accounts.db: users, identities, sessions, shares, waitlist, consents,
                                the sign-ins in progress
    tenants                     user row -> Tenant; ready a new tenant's folder and DB; the
                                tenants the scheduler walks
    login, google, terms        signing in (SP-58): the switch and who becomes whom, the Google
                                OIDC client, the terms a new user accepts

    export, deletion            下載我的資料 and 刪除帳號 (SP-386): the user's own data off the data
                                registry; an account and all its data deleted (never the owner)

Plans and scopes SP-330; the admin pages SP-351.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_ENABLED = "WKO5COACH_ACCOUNTS"
ENV_SERVER_ROOT = "WKO5COACH_SERVER_ROOT"


def enabled() -> bool:
    """Multi-user mode (accounts.db, user tenants, sign-in required). Never in the demo instance."""
    from backend import tenancy
    if tenancy.demo_mode():
        return False
    return os.environ.get(ENV_ENABLED, "").strip().lower() in ("1", "true", "yes", "on")


def server_root() -> Path:
    """$WKO5COACH_SERVER_ROOT, else $WKO5COACH_HOME (the owner's folder, ~/.wko5coach). The
    owner's tenant stays where it is (migrated in place, §5); accounts.db and users/ sit next
    to its files and are classified as the server's own (data_registry: scope INSTANCE, never in
    the owner's backup)."""
    v = os.environ.get(ENV_SERVER_ROOT, "").strip()
    if v:
        return Path(v)
    from backend import tenancy
    return tenancy.home_root()


def accounts_db_path() -> Path:
    return server_root() / "accounts.db"


def users_dir() -> Path:
    return server_root() / "users"
