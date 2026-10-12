"""SP-387 第 7 條: in multi-user mode the server's daily backup holds accounts.db and every user's
tenant (each like SP-355's backup: the DB without credentials, the user's files), outside the
server root, keeping the last runs; the owner's own backup still never holds other people's data."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import zipfile

import pytest

from backend import tenancy


@pytest.fixture
def server(tmp_path, monkeypatch):
    from backend import accounts
    from backend.accounts import store, tenants as TN
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(tmp_path / "server"))
    monkeypatch.setenv(accounts.ENV_ENABLED, "1")
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    store.migrate_owner()
    return tmp_path


def _ready(t):
    from backend.accounts import tenants as TN
    asyncio.run(TN.ensure_ready(t))
    (t.root / "plan.json").write_text('{"events": [{"id": "e1", "name": "x"}]}', "utf-8")


def test_the_server_backup_holds_accounts_and_every_user(server, monkeypatch):
    from backend.accounts import server_backup as SB, store, tenants as TN
    a, b = store.create_user(email="a@x.tw"), store.create_user(email="b@x.tw")
    store.create_user(email="never@x.tw")                      # never used: nothing to keep
    for u in (a, b):
        _ready(TN.tenant_of(u))
    dest = server / "nas-backups"
    man = SB.run(dest)
    runs = [p for p in dest.iterdir() if SB.NAME_RE.match(p.name)]
    assert len(runs) == 1 and man["users"] == 2 and man["failed"] == []
    run = runs[0]
    con = sqlite3.connect(str(run / "accounts.db"))
    assert con.execute("SELECT count(*) FROM users").fetchone()[0] == 4
    con.close()
    for u in (a, b):
        z = next((run / "users" / str(u.id)).glob("trailruncoach-backup-*.zip"))
        with zipfile.ZipFile(z) as zf:
            names = zf.namelist()
            assert "wko5coach.db" in names and "files/plan.json" in names
            assert json.loads(zf.read("manifest.json"))["tenant"] == f"u{u.id}"
    assert json.loads((run / "manifest.json").read_text("utf-8"))["users"] == 2


def test_it_keeps_the_last_runs_and_refuses_a_folder_inside_the_server(server, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from backend import accounts
    from backend.accounts import server_backup as SB
    dest = server / "nas-backups"
    t0 = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
    for i in range(SB.KEEP + 2):
        SB.run(dest, now=t0 + timedelta(days=i))
    assert len([p for p in dest.iterdir() if SB.NAME_RE.match(p.name)]) == SB.KEEP
    (dest / "keep-me").mkdir()
    SB.prune(dest, keep=1)
    assert (dest / "keep-me").exists()                         # only our folders are ever deleted
    with pytest.raises(RuntimeError):
        SB.run(accounts.server_root() / "backups")


def test_it_is_due_daily_only_in_multi_user_mode_with_a_folder(server, monkeypatch):
    from backend import accounts
    from backend.accounts import server_backup as SB
    monkeypatch.delenv(SB.ENV_DIR, raising=False)
    assert SB.due() is False
    monkeypatch.setenv(SB.ENV_DIR, str(server / "nas-backups"))
    assert SB.due() is True
    SB.run()
    assert SB.due() is False
    monkeypatch.delenv(accounts.ENV_ENABLED)
    assert SB.due() is False


def test_the_owners_own_backup_never_holds_other_users(server):
    from backend import data_registry as R
    for rel in ("accounts.db", "users/2/wko5coach.db", "users/2/plan.json", "site.json"):
        f = R.classify(rel)
        assert f is not None and f.backup == R.NEVER, rel
