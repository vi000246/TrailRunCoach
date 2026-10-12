"""
Accounts and user tenants (SP-384; docs/plans/auth-and-demo.plan.md §2.2, §4.3, §5).

    accounts.enabled()          WKO5COACH_ACCOUNTS=1: requests are bound to the signed-in user's
                                tenant (tenancy_mw). Off (the default, today's self-hosted app):
                                every request is the owner, nothing here is read.
    accounts.server_root()      where accounts.db and users/<id>/ live
    store                       accounts.db: users, identities, sessions, shares, waitlist
    tenants                     user row -> Tenant; ready a new tenant's folder and DB; the
                                tenants the scheduler walks

The login flow and the sign-up screen are SP-58; deleting an account SP-386; plans and scopes
SP-330; the admin pages SP-351. This package only holds the data and the tenant mapping.
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
