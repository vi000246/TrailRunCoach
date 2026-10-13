"""SP-386: 下載我的資料 (the zip of the user's own data, read off the data registry), 刪除帳號
(after a fresh Google sign-in: the platform credentials first, then the tenant folder and every
accounts.db row), the owner's protection, and withdrawing the cross-user analysis consent.

The sign-in side is SP-58's fake Google (test_login_sp58.py): nothing reaches the network.
Sign-in mode only — the owner's app (no sign-in settings) and the demo answer 404 as before."""
from __future__ import annotations

import gzip
import io
import json
import sqlite3
import zipfile

import pytest

from backend import data_registry as R
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, _clean_env, _client, app, browser, c, csrf, env, location, new_terms, sign_in, sign_up,
    started,
)

TOKEN = "coros-token-SP386-must-not-leak"
SEALED = "gAAAAA-sealed-password-SP386"
CAL_TOKEN = "ics-feed-token-SP386"
DEBUG_HASH = "debug-token-hash-SP386"
SECRET_FILE = b"secret-file-SP386"
DERIVED_FILE = b"derived-cache-SP386"
SETTINGS_URL = "/api/v1/wko5/settings"


# ---------------------------------------------------------------------------- helpers

def _uid(sub: str) -> int:
    from backend.accounts import store
    u = store.find_identity("google", sub)
    assert u is not None
    return u.id


def _folder(uid: int):
    from backend.accounts import store
    return store.get_user(uid).folder()


def _sample(pattern: str) -> str:
    """A path the registry entry `pattern` classifies (relative to the tenant folder)."""
    return pattern.replace("**/", "").replace("**", "s1/sample.dat").replace("*", "sample")


def _export_entries() -> list:
    """The registry's file entries a user's export holds: the tenant's own USER / IMPORTED files."""
    return [f for f in R.FILES if f.cls in (R.USER, R.IMPORTED) and f.scope == R.TENANT and f is not R.DB
            and f.where in (R.ROOT, R.BASE, R.SHARED)]


def _fill(uid: int) -> dict:
    """A user tenant holding one file of every exported registry entry, a secret / derived file,
    platform credentials, an ICS link token and a debug token. Returns {rel: bytes} of the files
    that must be in the export."""
    folder = _folder(uid)
    want = {}
    for f in _export_entries():
        rel = _sample(f.pattern)
        assert R.classify(rel) == f, (f.pattern, rel, R.classify(rel))
        data = gzip.compress(b"FIT " + f.pattern.encode()) if f is R.FIT else ("sample " + f.pattern).encode()
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        want[rel] = data
    for rel, data in (("weather.json", SECRET_FILE), ("backups/pre.zip", SECRET_FILE),
                      ("cache/fit/x.npz", DERIVED_FILE), ("archive_log.json", DERIVED_FILE)):
        assert R.classify(rel).cls in (R.SECRET, R.DERIVED), rel
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    con = sqlite3.connect(str(folder / "wko5coach.db"))
    try:
        con.execute("INSERT INTO sync_state(athlete_id, coros_access_token, coros_password_sealed, coros_email, "
                    "garmin_token_sealed) VALUES (1, ?, ?, 'runner@coros.example', ?)", (TOKEN, SEALED, SEALED))
        con.execute("INSERT INTO user_settings(user_id, key, value_json, updated_at) "
                    "VALUES (1, 'plan.calendar', ?, '2026-10-13')", (json.dumps({"token": CAL_TOKEN, "origin": None}),))
        con.execute("INSERT INTO user_settings(user_id, key, value_json, updated_at) "
                    "VALUES (1, 'athlete.region', '\"tw\"', '2026-10-13')")
        con.execute("INSERT INTO debug_tokens(tenant_id, name, prefix, token_hash, scopes_json, created_at, "
                    "expires_at, ips_json) VALUES (?, 'agent', 'trcd_ab', ?, '[]', '2026-10-13', '2026-10-20', '[]')",
                    (f"u{uid}", DEBUG_HASH))
        con.commit()
    finally:
        con.close()
    return want


def _unzipped(content: bytes) -> tuple[zipfile.ZipFile, bytes]:
    z = zipfile.ZipFile(io.BytesIO(content))
    assert z.testzip() is None
    return z, b"\n".join(z.read(n) for n in z.namelist())


def _reauth(cl, sub: str, email: str):
    """「用 Google 確認身分」: start, pick the account at Google, come back."""
    from backend.accounts import google as G
    r = cl.get("/api/v1/account/reauth")
    assert r.status_code in (302, 303), r.text
    assert location(r).startswith(G.AUTHORIZE_URL)
    return cl.get(cl.fake.authorize(location(r), sub=sub, email=email))


def _delete(cl, **body):
    return cl.post("/api/v1/account/delete", json={"confirm": True, **body}, headers={"X-TRC-CSRF": csrf(cl)})


def _rows_of(uid: int, sub: str, emails: tuple) -> dict:
    """Every accounts.db row that still names this person (by id, Google sub or e-mail)."""
    from backend.accounts import store
    con = sqlite3.connect(str(store.path()))
    try:
        out = {}
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            cols = {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}
            conds, args = [], []
            if "user_id" in cols:
                conds.append("user_id = ?"); args.append(uid)
            if t == "users":
                conds.append("id = ?"); args.append(uid)
            for col in ("google_sub", "subject"):
                if col in cols:
                    conds.append(f"{col} = ?"); args.append(sub)
            if "email" in cols:
                conds.append("lower(email) IN (%s)" % ",".join("?" * len(emails))); args += list(emails)
            if conds:
                n = con.execute(f'SELECT count(*) FROM "{t}" WHERE ' + " OR ".join(conds), args).fetchone()[0]
                if n:
                    out[t] = n
        return out
    finally:
        con.close()


# ---------------------------------------------------------------------------- 下載我的資料

def test_the_export_holds_every_user_and_imported_item_and_no_secret(c):
    sign_up(c, "g-a", "alice@example.com", name="Alice")
    uid = _uid("g-a")
    assert c.get("/api/v1/plan").status_code == 200                     # the tenant's folder and DB exist
    want = _fill(uid)
    r = c.get("/api/v1/account/export.zip")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"] and "no-store" in r.headers["cache-control"]
    z, raw = _unzipped(r.content)
    names = set(z.namelist())
    # every USER / IMPORTED file of the registry, as stored, at its path in the tenant folder
    for rel, data in want.items():
        assert f"files/{rel}" in names, rel
        assert z.read(f"files/{rel}") == data
    # every USER / IMPORTED table of the DB, as JSON rows; no SECRET or DERIVED table
    con = sqlite3.connect(str(_folder(uid) / "wko5coach.db"))
    present = {n for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    for t in R.TABLES:
        entry = f"tables/{t.name}.json"
        if t.cls in (R.USER, R.IMPORTED) and t.name in present:
            assert entry in names, t.name
            assert isinstance(json.loads(z.read(entry)), list)
        else:
            assert entry not in names, t.name
    settings = {row["key"]: row for row in json.loads(z.read("tables/user_settings.json"))}
    assert "athlete.region" in settings and "plan.calendar" not in settings
    # nothing secret anywhere in the zip (credentials, the ICS link token, a debug token's hash, the
    # SECRET files) and no DERIVED cache
    for needle in (TOKEN.encode(), SEALED.encode(), CAL_TOKEN.encode(), DEBUG_HASH.encode(), SECRET_FILE,
                   DERIVED_FILE, b"runner@coros.example"):
        assert needle not in raw, needle
    assert not any(n.startswith(("files/weather.json", "files/backups/", "files/cache/", "files/archive_log"))
                   for n in names)
    # the account itself and its consent history (SP-326 §7.1); never a session or a Google sub
    acct = json.loads(z.read("account.json"))
    assert acct["email"] == "alice@example.com" and acct["display_name"] == "Alice"
    assert [x["terms_version"] for x in acct["consents"]] and all("documents" in x for x in acct["consents"])
    assert b"g-a" not in z.read("account.json")
    man = json.loads(z.read("manifest.json"))
    assert man["files"]["count"] == len(want) and set(man["tables"]) >= {"user_settings", "athletes"}
    assert set(man["left_out"]) == {R.DERIVED, R.SECRET}
    assert "README.txt" in names


def test_the_export_is_only_the_signed_in_users_own(c, app):
    sign_up(c, "g-a", "alice@example.com")
    assert c.get("/api/v1/plan").status_code == 200
    _fill(_uid("g-a"))
    b = browser(app, ip="203.0.113.20")
    sign_up(b, "g-b", "bob@example.com")
    r = b.get("/api/v1/account/export.zip")
    assert r.status_code == 200
    z, raw = _unzipped(r.content)
    assert not any(n.startswith("files/") for n in z.namelist())        # Bob has no files yet
    assert b"alice" not in raw and json.loads(z.read("account.json"))["email"] == "bob@example.com"


# ---------------------------------------------------------------------------- 刪除帳號

def test_deleting_needs_a_fresh_google_sign_in_of_the_same_account(c, monkeypatch):
    from backend.api import account as A
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    r = _delete(c)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "REAUTH_REQUIRED"
    assert c.get("/api/v1/account/me").json()["reauth_ok"] is False
    # another Google account at Google's chooser: refused, nothing marked
    r = _reauth(c, "g-someone-else", "eve@example.com")
    assert location(r) == SETTINGS_URL + "?reauth=mismatch#account"
    assert _delete(c).status_code == 403
    # the same account: marked on this session only
    r = _reauth(c, "g-a", "alice@example.com")
    assert location(r) == SETTINGS_URL + "?reauth=ok#account"
    assert c.get("/api/v1/account/me").json()["reauth_ok"] is True
    # the confirmation must be ticked
    r = c.post("/api/v1/account/delete", json={"confirm": False}, headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "CONFIRM_REQUIRED"
    # a write without the CSRF header never reaches it
    assert c.post("/api/v1/account/delete", json={"confirm": True}).status_code == 403
    # a confirmation older than REAUTH_TTL_S no longer counts
    monkeypatch.setattr(A, "REAUTH_TTL_S", 0)
    r = _delete(c)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "REAUTH_REQUIRED"
    assert _folder(uid).exists() and _rows_of(uid, "g-a", ("alice@example.com",))


def test_a_reauth_never_signs_anyone_in(c):
    """The confirmation is a sign-in at Google, but it only marks the session that asked for it:
    signed out it is refused (the gate), and its callback never starts a session."""
    r = c.get("/api/v1/account/reauth")
    assert r.status_code == 401
    sign_up(c, "g-a", "alice@example.com")
    before = c.cookies.get("__Host-trc_session")
    r = _reauth(c, "g-a", "alice@example.com")
    assert not any(line.startswith("__Host-trc_session=") for line in r.headers.get_list("set-cookie"))
    assert c.cookies.get("__Host-trc_session") == before


def test_deleting_an_account_leaves_nothing_behind(c, app, monkeypatch):
    from backend.accounts import deletion as D, store
    sign_up(c, "g-a", "Alice@Example.com")
    uid = _uid("g-a")
    assert c.get("/api/v1/plan").status_code == 200
    _fill(uid)
    folder = _folder(uid)
    other = browser(app, ip="203.0.113.30")                 # the same person signed in elsewhere
    assert location(sign_in(other, "g-a", "alice@example.com")) == "/"
    assert other.get("/api/v1/plan").status_code == 200
    # everything accounts.db can hold for a person
    store.add_share("share-a", uid)
    store.add_waitlist("alice@example.com")
    store.usage_add(uid, "calc", "2026-10", 10, item="x")
    store.create_service_token(uid, "inreach", ["link:read"])
    store.put_signup_ticket("tkt", "reconsent", "google", "g-a", "alice@example.com", "Alice", uid, "/", 600)
    # the platforms are disconnected before their credentials and the folder go
    order = []

    async def revoke(source, creds):
        order.append(("revoke", source, creds.get("coros_access_token")))
    monkeypatch.setitem(D.REVOKE, "coros", revoke)
    real_rmtree = D.shutil.rmtree

    def rmtree(p, *a, **kw):
        con = sqlite3.connect(str(folder / "wko5coach.db"))
        st = con.execute("SELECT coros_access_token, coros_password_sealed, garmin_token_sealed, coros_email "
                         "FROM sync_state").fetchone()
        con.close()
        order.append(("rmtree", st))
        return real_rmtree(p, *a, **kw)
    monkeypatch.setattr(D.shutil, "rmtree", rmtree)

    assert _reauth(c, "g-a", "alice@example.com").status_code == 303
    r = _delete(c)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["next"] == "/login?deleted=1"
    assert body["disconnected"] == ["coros", "garmin"] and body["revoked"] == ["coros"]
    assert any(line.startswith("__Host-trc_session=;") for line in r.headers.get_list("set-cookie"))
    assert order == [("revoke", "coros", TOKEN), ("rmtree", (None, None, None, None))]
    # no folder, no row, no session — in this browser or the other one
    assert not folder.exists()
    assert _rows_of(uid, "g-a", ("alice@example.com",)) == {}
    for cl in (c, other):
        assert cl.get("/api/v1/plan").status_code == 401
        assert cl.get("/api/v1/account/me").status_code == 401
        assert cl.get("/api/v1/session").json()["user"] is None
    assert store.get_user(uid) is None and store.find_identity("google", "g-a") is None
    assert store.share_owner("share-a") is None
    # the same Google account coming back is a new person (the terms again), never the old data
    assert location(sign_in(browser(app, ip="203.0.113.40"), "g-a", "alice@example.com")) == "/signup"


def test_a_failed_platform_revoke_does_not_stop_the_deletion(c, monkeypatch):
    from backend.accounts import deletion as D
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    assert c.get("/api/v1/plan").status_code == 200
    _fill(uid)

    async def broken(source, creds):
        raise RuntimeError("platform down")
    monkeypatch.setitem(D.REVOKE, "garmin", broken)
    _reauth(c, "g-a", "alice@example.com")
    r = _delete(c)
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == [] and r.json()["revoke_failed"] == ["garmin"]
    assert not _folder_path_exists(uid)


def _folder_path_exists(uid: int) -> bool:
    from backend import accounts
    return (accounts.users_dir() / str(uid)).exists()


def test_a_running_sync_holds_the_deletion_off(c):
    from backend.sync import runner
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    assert c.get("/api/v1/plan").status_code == 200
    _reauth(c, "g-a", "alice@example.com")
    runner._BUSY.add(f"u{uid}:coros")
    try:
        r = _delete(c)
    finally:
        runner._BUSY.discard(f"u{uid}:coros")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "SYNC_BUSY"
    assert _folder(uid).exists() and c.get("/api/v1/plan").status_code == 200     # nothing happened
    assert _delete(c).status_code == 200                                          # once it is done


# ---------------------------------------------------------------------------- the owner

def test_the_owner_cannot_be_deleted_from_the_web(c):
    import asyncio
    from backend.accounts import deletion as D, store
    assert location(sign_in(c, "g-owner", OWNER_EMAIL)) == "/"
    me = c.get("/api/v1/account/me").json()
    assert me["role"] == "owner" and me["can_delete"] is False and me["analysis"] is None
    assert c.get("/api/v1/account/reauth").status_code == 403
    r = _delete(c)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "OWNER_PROTECTED"
    with pytest.raises(ValueError):
        store.delete_user(1)
    with pytest.raises(ValueError):
        asyncio.run(D.delete_account(store.get_user(1)))
    assert store.get_user(1) is not None and c.get("/api/v1/plan").status_code == 200


def test_owner_mode_has_none_of_it(monkeypatch, tmp_path):
    """No sign-in settings: the owner's app as before — every route here answers 404, and the
    settings page's account block stays hidden (it shows only when /account/me answers)."""
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        for m, p in (("GET", "/api/v1/account/me"), ("GET", "/api/v1/account/export.zip"),
                     ("GET", "/api/v1/account/reauth"), ("POST", "/api/v1/account/delete"),
                     ("POST", "/api/v1/account/analysis")):
            assert cl.request(m, p, json={}).status_code == 404, p
        page = cl.get(SETTINGS_URL, headers=HTML).text
        assert '<section id="account" hidden>' in page


def test_the_cli_deletes_an_account_the_owner_names(c, capsys):
    """An account whose deletion stopped half way (or a request by e-mail) is finished from the
    server: `python -m backend.accounts.deletion <id>` — never user 1."""
    from backend.accounts import deletion as D, store
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    assert c.get("/api/v1/plan").status_code == 200
    store.set_disabled(uid, True, "deleting")
    assert D.main([str(uid)]) == 0
    assert store.get_user(uid) is None and not _folder_path_exists(uid)
    assert D.main(["1"]) == 2 and store.get_user(1) is not None
    assert "owner" in capsys.readouterr().err


# ---------------------------------------------------------------------------- 撤回跨使用者分析的同意

def test_withdrawing_the_analysis_consent_is_recorded_and_kept(c, monkeypatch):
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    assert c.get("/api/v1/account/me").json()["analysis"] is True
    r = c.post("/api/v1/account/analysis", json={"analysis": False}, headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 200 and r.json()["analysis"] is False
    rows = store.consents(uid)
    assert [(x["action"], x["analysis"]) for x in rows] == [("terms", 1), ("analysis", 0)]
    assert rows[1]["terms_version"] == rows[0]["terms_version"]          # the terms stay accepted
    assert c.get("/api/v1/plan").status_code == 200
    # new terms accepted later: the withdrawal stays (the bundled consent is never taken back silently)
    new_terms(monkeypatch)
    info = c.get("/api/v1/account/signup").json()
    r = c.post("/api/v1/account/signup", json={"accept": True, "version": info["terms"]["version"],
                                               "digest": info["terms"]["digest"]}, headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 200, r.text
    assert store.analysis_consent(uid) is False
    assert c.get("/api/v1/account/me").json()["analysis"] is False
    # the history is in the export
    z, _raw = _unzipped(c.get("/api/v1/account/export.zip").content)
    acts = [(x["action"], x["analysis"]) for x in json.loads(z.read("account.json"))["consents"]]
    assert acts == [("terms", 1), ("analysis", 0), ("terms", 0)]
    # and it can be given again
    r = c.post("/api/v1/account/analysis", json={"analysis": True}, headers={"X-TRC-CSRF": csrf(c)})
    assert r.json()["analysis"] is True and store.analysis_consent(uid) is True


def test_accounts_db_migrates_an_older_file(tmp_path, monkeypatch):
    """A server whose accounts.db was made before SP-386 gets the new columns on start (no
    rows lost): sessions.reauth_at, oauth_states.purpose / user_id, consents.action."""
    from backend.accounts import store
    p = tmp_path / "accounts.db"
    con = sqlite3.connect(str(p))
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL DEFAULT 'user',
            google_sub TEXT UNIQUE, email TEXT, email_verified INTEGER NOT NULL DEFAULT 0, display_name TEXT,
            tenant_dir TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_login_at TEXT, disabled_at TEXT,
            disabled_reason TEXT, plan TEXT NOT NULL DEFAULT 'free', plan_expires_at TEXT);
        CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE consents (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            terms_version TEXT NOT NULL, documents TEXT NOT NULL, analysis INTEGER NOT NULL DEFAULT 1,
            accepted_at TEXT NOT NULL);
        CREATE TABLE oauth_states (state_hash TEXT PRIMARY KEY, binding_hash TEXT NOT NULL, provider TEXT NOT NULL,
            nonce TEXT NOT NULL, code_verifier TEXT NOT NULL, redirect_uri TEXT NOT NULL, next_url TEXT NOT NULL,
            expires_at TEXT NOT NULL);
        INSERT INTO users(id, role, created_at, tenant_dir) VALUES (2, 'user', '2026-10-01', 'users/2');
        INSERT INTO consents(user_id, terms_version, documents, accepted_at) VALUES (2, 'v1', '{}', '2026-10-01');
    """)
    con.commit()
    con.close()
    monkeypatch.setattr(store, "DB_PATH", p)
    monkeypatch.setattr(store, "_READY", set())
    store.init()
    assert store.consents(2)[0]["action"] == "terms" and store.analysis_consent(2) is True
    tok = store.create_session(2)
    assert store.reauth_at(tok) is None and store.mark_reauth(tok, 2) and store.reauth_at(tok) is not None
    store.put_oauth_state("s", "b", "google", "n", "v", "r", "/", 60, purpose="reauth", user_id=2)
    assert store.pop_oauth_state("s")["purpose"] == "reauth"
