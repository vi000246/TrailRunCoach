"""
SP-711: the server-side backup (/api/v1/backup/*) is the owner's alone (tenancy.OWNER_ONLY_CAPS
`backup`). In sign-in mode every other user — a member, a test account — gets 403 on every
endpoint, before anything is read or written (a backup folder anywhere on the server, an 8 GB
upload); the owner's app and the owner signed in work as before. A user's own data is backed up by
the whole-server backup (accounts/server_backup.py) and exported with `export:data`.

Also: the settings page shows its 備份 section only with the `backup` cap, and every other
OWNER_ONLY_CAPS resource has its own check (wko5, weather.key, tp.client, ai.owner_key).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend import tenancy
from backend.tests import accounts_fixtures as AF
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures
    OWNER_EMAIL, app, browser, c, csrf, env, sign_in, sign_up, started)

REPO = Path(__file__).resolve().parents[2]
BK = "/api/v1/backup"


def _calls(cl, evil: Path):
    """Every backup endpoint once (the body each one wants)."""
    h = {"X-TRC-CSRF": csrf(cl)}
    return {
        "GET /status": cl.get(f"{BK}/status"),
        "PUT /settings": cl.put(f"{BK}/settings", json={"dir": str(evil), "auto": True}, headers=h),
        "POST /run": cl.post(f"{BK}/run", headers=h),
        "POST /upload": cl.post(f"{BK}/upload", content=b"PK\x03\x04 not really a zip",
                                headers={**h, "Content-Type": "application/octet-stream"}),
        "POST /inspect": cl.post(f"{BK}/inspect", json={"name": "x.zip"}, headers=h),
        "POST /restore": cl.post(f"{BK}/restore", json={"name": "x.zip"}, headers=h),
    }


def _staged(root: Path) -> list:
    return sorted(str(p) for p in root.rglob("staging/*.bin"))


def test_a_member_and_a_test_account_get_403_on_every_backup_endpoint(app, tmp_path):  # noqa: F811
    member = browser(app, "198.51.100.21")
    sign_up(member, "g-member", "member@example.com")
    tester = AF.signed_in(app, AF.many(1)[0], ip="198.51.100.22")
    evil = tmp_path / "owner" / "evil-backups"            # the owner's folder: the worst place to write
    for who, cl in (("member", member), ("test account", tester)):
        for name, r in _calls(cl, evil).items():
            assert r.status_code == 403, (who, name, r.status_code, r.text)
            assert r.json()["detail"]["code"] == "OWNER_ONLY", (who, name, r.text)
    assert not evil.exists()                              # no mkdir, no test file
    assert _staged(tmp_path) == []                        # nothing uploaded was kept


def test_the_owner_signed_in_still_uses_the_backup(app, tmp_path, monkeypatch):  # noqa: F811
    from backend.engine import backup as B
    monkeypatch.setattr(B, "detect_cloud_folders", lambda: [])
    owner = browser(app)
    sign_in(owner, "g-owner", OWNER_EMAIL)
    r = owner.get(f"{BK}/status")
    assert r.status_code == 200, r.text
    folder = tmp_path / "cloud" / "Backups"
    folder.parent.mkdir()
    r = owner.put(f"{BK}/settings", json={"dir": str(folder)}, headers={"X-TRC-CSRF": csrf(owner)})
    assert r.status_code == 200, r.text
    assert r.json()["dir"] == str(folder.resolve())


def test_the_owners_app_without_sign_in_is_unchanged(monkeypatch, tmp_path):
    """The self-hosted app (no sign-in): the owner tenant, every cap — the backup answers."""
    from fastapi.testclient import TestClient
    from backend.engine import backup as B
    from backend.main import build_app
    monkeypatch.setenv("WKO5COACH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WKO5COACH_NO_SCHEDULER", "1")
    monkeypatch.delenv("WKO5COACH_ACCOUNTS", raising=False)
    monkeypatch.setattr(B, "detect_cloud_folders", lambda: [])
    with TestClient(build_app(demo=False)) as cl:
        r = cl.get(f"{BK}/status")
        assert r.status_code == 200, r.text


def test_the_check_is_the_owner_tenant_with_the_backup_cap(monkeypatch):
    """A user tenant made without caps (the dataclass default is ALL_CAPS) is still refused: the
    check wants the owner's tenant, not only the cap."""
    from fastapi import HTTPException
    from backend.api import backup as A
    home = Path("/nonexistent")
    for t in (tenancy.Tenant(id="u9", kind=tenancy.USER, root=home, shared=home),
              tenancy.Tenant(id="u9", kind=tenancy.USER, root=home, shared=home, caps=tenancy.USER_CAPS),
              tenancy.Tenant(id="demo-x", kind=tenancy.DEMO_SANDBOX, root=home, shared=home, caps=tenancy.DEMO_CAPS),
              tenancy.anonymous()):
        with tenancy.use(t), pytest.raises(HTTPException) as e:
            A.owner_only()
        assert e.value.status_code == 403
    with tenancy.use(tenancy.Tenant(id="owner", kind=tenancy.OWNER, root=home, shared=home)):
        A.owner_only()


def test_every_backup_route_carries_the_owner_check():
    """A route added later is covered too: the check sits on the router, not on each endpoint."""
    from backend.api import backup as A
    deps = [d.dependency for d in A.router.dependencies]
    assert A.owner_only in deps
    assert A.router.routes, "no routes?"


# ---------------------------------------------------------------- the settings page

def test_the_settings_page_shows_the_backup_section_only_with_the_cap():
    html = (REPO / "backend" / "static" / "settings.html").read_text(encoding="utf-8")
    assert re.search(r'<section id="backup" hidden>', html), "the section starts hidden (no flash for a member)"
    m = re.search(r"const CAN_BACKUP = ([^;]+);", html)
    assert m and '"backup"' in m.group(1) and "caps" in m.group(1), "shown only when the session has the cap"
    assert re.search(r'\$\("backup"\)\.hidden = !CAN_BACKUP', html)
    assert re.search(r"if \(CAN_BACKUP\) loadBackup\(\)", html), "a member's page never calls the API"
    assert not re.search(r"[;\s]loadBackup\(\); loadArchive", html)


# ---------------------------------------------------------------- the other owner-only caps

def _user_tenant(home: Path):
    return tenancy.Tenant(id="u7", kind=tenancy.USER, root=home, shared=home, caps=tenancy.USER_CAPS)


def test_every_owner_only_cap_has_a_check(tmp_path, monkeypatch):
    """SP-711 (c): each OWNER_ONLY_CAPS resource refuses a user tenant (behaviour, not text)."""
    assert tenancy.OWNER_ONLY_CAPS == frozenset({"wko5", "weather.key", "tp.client", "backup", "ai.owner_key"})
    from backend.engine.racepower import weather as WX
    from backend.settings import paths as P
    from backend.sync import tp_client as TP
    monkeypatch.delenv(tenancy.ENV_MODE, raising=False)
    monkeypatch.setenv("TP_CLIENT_ID", "owners-id")
    monkeypatch.setenv("TP_CLIENT_SECRET", "owners-secret")
    monkeypatch.setenv("CWA_API_KEY", "OWNERS-CWA-KEY-123")
    wk = tmp_path / "WKO5" / "athlete"
    wk.mkdir(parents=True)
    with tenancy.use(_user_tenant(tmp_path / "u7")):
        # wko5: a user never sees the owner's WKO5 folder (settings/paths.athlete_dir / for_tenant)
        assert P.for_tenant(wk) == P.no_wko5_dir()
        assert P.athlete_dir() == P.no_wko5_dir()
        # weather.key: no CWA key to read, and saving one is refused (engine) / 403 (api/racepower)
        assert WX.load_key() is None
        with pytest.raises(PermissionError):
            WX.save_key("SOMEONES-KEY-12345")
        from fastapi import HTTPException
        from backend.api import racepower as RP
        with pytest.raises(HTTPException) as e:
            RP.save_key(RP.KeyIn(key="SOMEONES-KEY-12345"))
        assert e.value.status_code == 403
        # tp.client: the developer client credentials are the owner's only
        assert TP.lookup_client_creds() == (None, "owner only")
    with tenancy.use(tenancy.owner()):
        assert P.for_tenant(wk) == wk
        assert TP.lookup_client_creds()[0] == ("owners-id", "owners-secret")
        assert WX.load_key() == "OWNERS-CWA-KEY-123"
    # ai.owner_key (SP-785, backend/ai/config.py): the owner's AI key — a user tenant reads none and
    # may not save one
    assert not _user_tenant(tmp_path).can("ai.owner_key")
    from backend.ai import config as AIC
    monkeypatch.setenv(AIC.ENV_KEY, "sk-ant-OWNERS-AI-KEY-0123456789")
    with tenancy.use(_user_tenant(tmp_path / "u7")):
        assert AIC.api_key() is None and AIC.key_source() == "none"
        with pytest.raises(PermissionError):
            AIC.save_key("sk-ant-SOMEONES-KEY-0123456789")
    with tenancy.use(tenancy.owner()):
        assert AIC.api_key() == "sk-ant-OWNERS-AI-KEY-0123456789"
