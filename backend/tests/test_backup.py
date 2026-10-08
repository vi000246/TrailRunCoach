"""Backups (engine/backup.py, api/backup.py). Synthetic DBs / folders in tmp_path only."""
import asyncio
import gzip
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.engine import backup as B


def make_db(path: Path, rows: int = 3) -> Path:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT)")
    con.execute("CREATE TABLE user_settings (id INTEGER PRIMARY KEY, user_id INTEGER, key TEXT, "
                "value_json TEXT, updated_at DATETIME, UNIQUE (user_id, key))")
    con.executemany("INSERT INTO workout_files (file_path) VALUES (?)", [(f"f{i}.fit",) for i in range(rows)])
    con.commit()
    con.close()
    return path


def count(db: Path, table: str = "workout_files") -> int:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def test_create_backup_zip_manifest_and_counts(tmp_path):
    db = make_db(tmp_path / "app.db")
    out = tmp_path / "cloud" / "TrailRunCoach Backups"
    r = B.create_backup(db, out, now=datetime(2026, 10, 2, 6, 30, tzinfo=timezone.utc))
    assert B.NAME_RE.match(r["name"]) and r["name"].endswith(".zip") and "encrypted" not in r
    assert r["row_counts"]["workout_files"] == 3
    files = [p.name for p in out.iterdir()]
    assert files == [r["name"]]                       # no .partial left behind
    with zipfile.ZipFile(out / r["name"]) as z:
        m = json.loads(z.read(B.MANIFEST))
        assert set(z.namelist()) == {B.MANIFEST, B.DB_ENTRY}
    assert m["app"] == B.APP and m["format"] == B.FORMAT and m["app_version"]
    assert m["schema_version"].startswith("2t-") and m["fit"]["included"] is False
    assert m["row_counts"] == {"workout_files": 3, "user_settings": 0}


def test_backup_while_another_connection_writes(tmp_path):
    db = make_db(tmp_path / "app.db")
    writer = sqlite3.connect(str(db))
    writer.execute("INSERT INTO workout_files (file_path) VALUES ('uncommitted')")   # open write txn
    try:
        r = B.create_backup(db, tmp_path / "out")
    finally:
        writer.rollback()
        writer.close()
    assert r["row_counts"]["workout_files"] == 3      # a consistent snapshot without the open write


def _legacy_encrypted(path: Path) -> Path:
    """An older version's encrypted backup: its header, then opaque bytes."""
    path.write_bytes(B.LEGACY_MAGIC + bytes([15, 8, 1]) + b"s" * 23 + b"\x00\x00\x00\x10" + b"x" * 16)
    return path


def test_no_encryption_left_and_legacy_encrypted_backups_are_rejected(tmp_path):
    # backups are never encrypted (owner 2026-10-02): no key / password API in the engine
    for gone in ("new_key", "derive_key", "encrypt_file", "decrypt_file", "SCRYPT", "is_encrypted"):
        assert not hasattr(B, gone), gone
    assert not B.NAME_RE.match("trailruncoach-backup-20260101-000000.zip.enc")
    live = make_db(tmp_path / "live.db")
    old = _legacy_encrypted(tmp_path / "trailruncoach-backup-20260101-000000.zip.enc")
    assert B.is_legacy_encrypted(old)
    with pytest.raises(B.BackupError, match="加密備份.*不能還原"):
        B.inspect(old, tmp_path)
    with pytest.raises(B.BackupError, match="不再支援加密"):
        B.restore(old, live, local_dir=tmp_path / "local")
    assert count(live) == 3 and not list((tmp_path / "local").glob("trailruncoach-prerestore-*"))
    # an old .zip.enc in the folder is neither listed nor pruned
    assert B.list_backups(tmp_path) == [] and B.prune(tmp_path) == [] and old.exists()


def test_fit_originals_gzip_and_restore_only_missing(tmp_path):
    db = make_db(tmp_path / "app.db")
    fit = tmp_path / "fit"
    (fit / "coros" / "2026").mkdir(parents=True)
    (fit / "coros" / "2026" / "a.fit").write_bytes(b"FIT-A" * 100)
    (fit / "tp" / "2025").mkdir(parents=True)
    (fit / "tp" / "2025" / "b.fit").write_bytes(b"FIT-B")
    (fit / "notes.txt").write_text("not a source folder")      # outside coros/tp: skipped
    r = B.create_backup(db, tmp_path / "out", fit_root=fit, include_fit=True)
    assert r["fit_files"] == 2
    with zipfile.ZipFile(r["path"]) as z:
        assert gzip.decompress(z.read("fit/coros/2026/a.fit.gz")) == b"FIT-A" * 100
        assert z.getinfo("fit/coros/2026/a.fit.gz").compress_type == zipfile.ZIP_STORED
    # new machine: a.fit exists with other content (kept), b.fit missing (restored)
    fit2 = tmp_path / "fit2"
    (fit2 / "coros" / "2026").mkdir(parents=True)
    (fit2 / "coros" / "2026" / "a.fit").write_bytes(b"LOCAL")
    live = make_db(tmp_path / "live.db", rows=1)
    res = B.restore(r["path"], live, local_dir=tmp_path / "local", fit_root=fit2)
    assert res["fit_restored"] == 1
    assert (fit2 / "coros" / "2026" / "a.fit").read_bytes() == b"LOCAL"
    assert (fit2 / "tp" / "2025" / "b.fit").read_bytes() == b"FIT-B"


def test_restore_swaps_db_keeps_backup_settings_and_saves_current(tmp_path):
    db = make_db(tmp_path / "app.db", rows=3)
    con = sqlite3.connect(str(db))
    con.execute("INSERT INTO user_settings (user_id, key, value_json) VALUES (1, 'backup.dir', '\"/old/machine\"')")
    con.execute("INSERT INTO user_settings (user_id, key, value_json) VALUES (1, 'athlete.timezone', '\"Asia/Taipei\"')")
    con.commit()
    con.close()
    r = B.create_backup(db, tmp_path / "out")
    # the live DB moves on: more rows, this machine's own folder
    con = sqlite3.connect(str(db))
    con.executemany("INSERT INTO workout_files (file_path) VALUES (?)", [("new1",), ("new2",)])
    con.execute("UPDATE user_settings SET value_json = '\"/this/machine\"' WHERE key = 'backup.dir'")
    con.commit()
    con.close()
    holder = sqlite3.connect(str(db))                 # the app keeps a connection open
    try:
        res = B.restore(r["path"], db, local_dir=tmp_path / "local")
        assert holder.execute("SELECT COUNT(*) FROM workout_files").fetchone()[0] == 3
    finally:
        holder.close()
    assert count(db) == 3
    con = sqlite3.connect(str(db))
    vals = dict(con.execute("SELECT key, value_json FROM user_settings").fetchall())
    con.close()
    assert vals["backup.dir"] == '"/this/machine"' and vals["athlete.timezone"] == '"Asia/Taipei"'
    pre = Path(res["pre_restore"])
    assert pre.parent == tmp_path / "local" and B.PRE_RE.match(pre.name)
    assert B.inspect(pre, tmp_path)["row_counts"]["workout_files"] == 5


def test_restore_rejects_bad_files(tmp_path):
    live = make_db(tmp_path / "live.db")
    local = tmp_path / "local"
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"not a zip")
    with pytest.raises(B.BackupError, match="zip"):
        B.restore(junk, live, local_dir=local)
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr(B.MANIFEST, json.dumps({"app": "SomethingElse", "format": 1}))
        z.writestr(B.DB_ENTRY, b"")
    with pytest.raises(B.BackupError, match="TrailRunCoach"):
        B.restore(other, live, local_dir=local)
    # a tampered DB fails the checksum
    good = B.create_backup(make_db(tmp_path / "src.db"), tmp_path / "out")["path"]
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(good) as zi, zipfile.ZipFile(bad, "w") as zo:
        zo.writestr(B.MANIFEST, zi.read(B.MANIFEST))
        raw = bytearray(zi.read(B.DB_ENTRY))
        raw[100:108] = bytes(b ^ 0xFF for b in raw[100:108])
        zo.writestr(B.DB_ENTRY, bytes(raw))
    with pytest.raises(B.BackupError, match="檢查碼"):
        B.restore(bad, live, local_dir=local)
    assert count(live) == 3                           # untouched
    assert not list(local.glob("trailruncoach-prerestore-*"))   # nothing was swapped


def test_retention_7_daily_4_weekly():
    base = datetime(2026, 10, 2, 6, 0)
    stamps = [base - timedelta(days=d) for d in range(60)] + [base - timedelta(hours=3)]
    keep = retention = B.retention(stamps)
    days = sorted({t.date() for t in keep}, reverse=True)
    assert days[:7] == [(base - timedelta(days=d)).date() for d in range(7)]
    assert base in keep and base - timedelta(hours=3) not in keep   # newest of the day only
    weeks = {tuple(t.isocalendar())[:2] for t in retention}
    assert len(weeks) == 4 and len(keep) <= 11


def test_prune_only_touches_our_files(tmp_path):
    base = datetime(2026, 10, 2, 6, 0)
    names = [f"trailruncoach-backup-{base - timedelta(days=d):%Y%m%d-%H%M%S}.zip" for d in range(40)]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    others = ["notes.zip", "trailruncoach-backup-latest.zip", "trailruncoach-backup-20200101-000000.zip.bak",
              "Trailruncoach-backup-20200101-000000.zip", "trailruncoach-prerestore-20200101-000000.zip"]
    for n in others:
        (tmp_path / n).write_bytes(b"keep me")
    (tmp_path / "trailruncoach-backup-20190101-000000.zip").mkdir()     # a folder with our name: ignored
    gone = B.prune(tmp_path)
    left = {p.name for p in tmp_path.iterdir()}
    assert set(others) <= left and "trailruncoach-backup-20190101-000000.zip" in left
    assert set(gone) == set(names) - left and len(set(names) & left) <= 11
    assert names[0] in left


def test_auto_due():
    now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    iso = lambda h: (now - timedelta(hours=h)).isoformat()
    assert B.auto_due(None, None, now)
    assert not B.auto_due(iso(5), iso(5), now)
    assert B.auto_due(iso(25), iso(25), now)
    assert not B.auto_due(iso(30), iso(0.5), now)      # failed half an hour ago: wait
    assert B.auto_due(iso(30), iso(2), now)


def test_detect_cloud_folders_windows_and_mac(tmp_path):
    prof = tmp_path / "win"
    (prof / "iCloudDrive").mkdir(parents=True)
    od = prof / "OneDrive - Personal"
    od.mkdir()
    (prof / "Dropbox").mkdir()
    env = {"USERPROFILE": str(prof), "OneDrive": str(od)}
    found = B.detect_cloud_folders(env=env, home=prof, platform="win32", drives=())
    ids = [f["id"] for f in found]
    assert ids == ["icloud", "onedrive", "dropbox"]
    assert found[1]["path"] == str(od / B.SUGGESTED_SUBDIR)
    assert not (od / B.SUGGESTED_SUBDIR).exists()       # detection never creates anything

    mac = tmp_path / "mac"
    (mac / "Library" / "Mobile Documents" / "com~apple~CloudDocs").mkdir(parents=True)
    (mac / "Library" / "CloudStorage" / "GoogleDrive-me@example.com" / "My Drive").mkdir(parents=True)
    found = B.detect_cloud_folders(env={}, home=mac, platform="darwin")
    assert [f["id"] for f in found] == ["icloud", "gdrive"]


def test_check_folder(tmp_path):
    assert B.check_folder(str(tmp_path / "new")) == tmp_path / "new"
    with pytest.raises(B.BackupError):
        B.check_folder("relative/path")
    with pytest.raises(B.BackupError):
        B.check_folder(str(tmp_path / "missing" / "deeper"))
    (tmp_path / "f").write_text("x")
    with pytest.raises(B.BackupError):
        B.check_folder(str(tmp_path / "f"))


# ------------------------------------------------------------------ user files + secrets (SP-355)
from backend import data_registry as R  # noqa: E402

SRC_SECRETS = {"plain": "SENTINEL-TOKEN-SRC-legacy-plaintext", "pw": "SENTINEL-PW-SRC",
               "hash": "SENTINEL-HASH-SRC-0123456789abcdef0123456789abcdef0123456789abcde",
               "email": "src@example.com"}
SECRET_FILES = {"secret.key": b"SENTINEL-KEYFILE", "weather.json": b'{"key": "SENTINEL-CWA"}',
                "tp_client.json": b'{"secret": "SENTINEL-TPCLIENT"}',
                "backups/trailruncoach-prerestore-20261001-000000.zip": b"SENTINEL-PRERESTORE",
                "cache/render/ab/abc.json": b"SENTINEL-DERIVED", "routes/index.json": b"SENTINEL-ROUTE-INDEX"}


def _sample(pattern: str) -> str:
    """A path a registry pattern matches: `**` -> one sub folder + file, `*` -> a name."""
    return pattern.replace("**", "sub/f.json").replace("*", "x")


def app_tenant(home: Path, *, secrets: dict, files: dict, tz: str = "Asia/Taipei", wal: bool = False) -> Path:
    """A tenant folder with the app schema, a login in sync_state (a legacy plaintext token and a
    sealed password), a debug token, a setting marker and the given files; returns the DB path."""
    from sqlalchemy import create_engine
    from backend.db.models import Base
    from backend.settings import secrets as S
    home.mkdir(parents=True, exist_ok=True)
    db = home / "wko5coach.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(str(db))
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.execute("INSERT INTO athletes (id, name, data_dir, created_at) VALUES (1, 'a', 'd', '2026-01-01')")
    if secrets:
        con.execute("INSERT INTO sync_state (athlete_id, coros_email, coros_access_token, coros_password_sealed, "
                    "tp_access_token, last_sync_cursor) VALUES (1, ?, ?, ?, ?, ?)",
                    (secrets["email"], secrets["plain"], S.seal(secrets["pw"]), secrets["plain"] + "-tp",
                     secrets["email"] + "-cursor"))
        con.execute("INSERT INTO debug_tokens (tenant_id, name, prefix, token_hash, scopes_json, created_at, "
                    "expires_at, ips_json) VALUES ('owner', 'mine', 'p', ?, '[]', '2026-10-01', '2099-01-01', '[]')",
                    (secrets["hash"],))
    con.execute("INSERT INTO user_settings (user_id, key, value_json, updated_at) VALUES "
                "(1, 'athlete.timezone', ?, '2026-10-01')", (json.dumps(tz),))
    con.commit()
    con.close()
    for rel, data in files.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return db


def _rows(db: Path, sql: str) -> list:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _secret_state(db: Path) -> dict:
    """{table: [row as a dict]} of the SECRET tables (column order may differ after a restore)."""
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return {t.name: sorted((dict(r) for r in con.execute(f"SELECT * FROM {t.name}")), key=repr)
                for t in R.of_class(R.SECRET)[0]}
    finally:
        con.close()


def _user_files() -> dict:
    """One file for every registry entry a backup always holds (besides the DB)."""
    out = {}
    for f in R.backup_entries(False):
        if f is not R.DB:
            out[_sample(f.pattern)] = f"user {f.pattern}".encode()
    return out


def test_user_files_are_backed_up_from_the_registry(tmp_path):
    """Every file the registry marks for a backup goes in under files/ (registry-driven: no list
    here); secret, derived and half-written files do not."""
    files = _user_files()
    assert {"plan.json", "routes/names.json", "views/sub/f.json", "event_gpx/sub/f.json"} <= set(files)
    junk = {"views/half.json.tmp": b"partial", "plan.json.tmp": b"partial"}
    db = app_tenant(tmp_path / "home", secrets=SRC_SECRETS, files={**files, **SECRET_FILES, **junk})
    r = B.create_backup(db, tmp_path / "out")
    with zipfile.ZipFile(r["path"]) as z:
        names = set(z.namelist())
        m = json.loads(z.read(B.MANIFEST))
        for rel, data in files.items():
            assert z.read(f"{B.FILES_DIR}/{rel}") == data, rel
    assert names == {B.MANIFEST, B.DB_ENTRY} | {f"{B.FILES_DIR}/{rel}" for rel in files}
    assert m["format"] == 2 and m["files"]["count"] == len(files)
    assert m["files"]["bytes"] == sum(len(v) for v in files.values())
    assert set(m["secrets_removed"]) == {t.name for t in R.of_class(R.SECRET)[0]}


def test_a_new_user_file_in_the_registry_is_backed_up_without_touching_backup_py(tmp_path, monkeypatch):
    """Adding a USER entry with backup=ALWAYS to the registry is all it takes."""
    new = R.File("new_feature/**", R.USER, R.BASE, "a file kind added later", backup=R.ALWAYS)
    monkeypatch.setattr(R, "FILES", (new,) + R.FILES)
    monkeypatch.setitem(R._RX, new.pattern, R._regex(new.pattern))
    db = app_tenant(tmp_path / "home", secrets={}, files={"new_feature/a/b.json": b"NEW"})
    r = B.create_backup(db, tmp_path / "out")
    with zipfile.ZipFile(r["path"]) as z:
        assert z.read(f"{B.FILES_DIR}/new_feature/a/b.json") == b"NEW"
    live = app_tenant(tmp_path / "live", secrets={}, files={})
    assert B.restore(r["path"], live, local_dir=tmp_path / "live" / "backups")["files_restored"] == 1
    assert (tmp_path / "live" / "new_feature" / "a" / "b.json").read_bytes() == b"NEW"


def test_no_secret_is_in_the_backup(tmp_path):
    """Scan the archive (raw and every entry decompressed, the DB pages included) for the
    tokens, the sealed and plaintext passwords, the debug token hash and the secret files."""
    from backend.settings import secrets as S
    db = app_tenant(tmp_path / "home", secrets=SRC_SECRETS, files={**_user_files(), **SECRET_FILES}, wal=True)
    sealed = _rows(db, "SELECT coros_password_sealed FROM sync_state")[0][0]
    assert sealed.startswith(S.PREFIX)
    r = B.create_backup(db, tmp_path / "out")
    assert r["row_counts"]["sync_state"] == 0 and r["row_counts"]["debug_tokens"] == 0
    blobs = [Path(r["path"]).read_bytes()]
    with zipfile.ZipFile(r["path"]) as z:
        for n in z.namelist():
            data = z.read(n)
            blobs.append(data)
            if n.endswith(".gz"):
                blobs.append(gzip.decompress(data))
    sentinels = [v.encode() for v in SRC_SECRETS.values()] + [sealed.encode(), b"SENTINEL-"]
    for blob in blobs:
        for s in sentinels:
            assert s not in blob, s
    # the live DB is untouched
    assert _rows(db, "SELECT coros_email FROM sync_state") == [(SRC_SECRETS["email"],)]


def test_round_trip_puts_user_files_back_and_keeps_this_machines_login(tmp_path):
    files = _user_files()
    src = app_tenant(tmp_path / "src", secrets=SRC_SECRETS, files=files, tz="Asia/Taipei")
    r = B.create_backup(src, tmp_path / "out")
    # another machine: its own login, debug token and older versions of some files, plus a view
    # the backup does not have
    mine = {"plain": "LOCAL-TOKEN", "pw": "LOCAL-PW", "hash": "LOCAL-HASH", "email": "me@here.example"}
    home = tmp_path / "home"
    live = app_tenant(home, secrets=mine, tz="Europe/Paris",
                      files={"plan.json": b"LOCAL PLAN", "views/local_only.json": b"LOCAL VIEW"})
    before = _secret_state(live)
    res = B.restore(r["path"], live, local_dir=home / "backups")
    assert res["files_restored"] == len(files)
    for rel, data in files.items():
        assert (home / rel).read_bytes() == data, rel
    assert (home / "views" / "local_only.json").read_bytes() == b"LOCAL VIEW"   # not in the backup: kept
    assert _rows(live, "SELECT value_json FROM user_settings WHERE key = 'athlete.timezone'") == [('"Asia/Taipei"',)]
    after = _secret_state(live)
    assert after == before                                # this machine's login, cursor and tokens
    assert not list(home.rglob("*.tmp"))
    # the pre-restore copy holds this machine's files as they were (no secrets either)
    with zipfile.ZipFile(res["pre_restore"]) as z:
        assert z.read(f"{B.FILES_DIR}/plan.json") == b"LOCAL PLAN"
        assert b"LOCAL-TOKEN" not in z.read(B.DB_ENTRY)
    info = B.inspect(r["path"], tmp_path)
    assert info["files"]["count"] == len(files)


def test_restore_keeps_local_secrets_when_the_backup_schema_is_older(tmp_path):
    """A backup whose sync_state lacks newer columns (or the debug_tokens table) still gets this
    machine's rows back in full; init_db then finds nothing left to add."""
    src = app_tenant(tmp_path / "src", secrets={}, files={})
    con = sqlite3.connect(str(src))
    con.execute("DROP TABLE debug_tokens")
    con.execute("ALTER TABLE sync_state DROP COLUMN tp_password_sealed")
    con.commit()
    con.close()
    r = B.create_backup(src, tmp_path / "out")
    mine = {"plain": "LOCAL-TOKEN", "pw": "LOCAL-PW", "hash": "LOCAL-HASH", "email": "me@here.example"}
    live = app_tenant(tmp_path / "home", secrets=mine, files={})
    con = sqlite3.connect(str(live))
    con.execute("UPDATE sync_state SET tp_password_sealed = 'enc:v1:local'")
    con.commit()
    con.close()
    before = _secret_state(live)
    B.restore(r["path"], live, local_dir=tmp_path / "home" / "backups")
    assert _secret_state(live) == before


def _old_format_backup(path: Path, db: Path) -> Path:
    """A backup as made before SP-355 (format 1): the whole DB, sync_state included; no files/."""
    con = sqlite3.connect(str(db))
    try:
        schema, counts = B.schema_info(con)
    finally:
        con.close()
    m = {"app": B.APP, "format": 1, "app_version": "0.9", "schema_version": schema,
         "created_at": "2026-10-01T00:00:00+00:00", "row_counts": counts, "db_sha256": B._sha256(db),
         "fit": {"included": False, "files": 0, "bytes": 0}}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(db, B.DB_ENTRY)
        z.writestr(B.MANIFEST, json.dumps(m))
    return path


def test_an_old_backup_still_restores_and_its_login_is_not_taken(tmp_path):
    src = app_tenant(tmp_path / "src", secrets=SRC_SECRETS, files={}, tz="Asia/Taipei")
    old = _old_format_backup(tmp_path / "trailruncoach-backup-20261001-000000.zip", src)
    assert B.inspect(old, tmp_path)["files"] is None
    # a machine that is logged in: keeps its login; its files stay as they are
    mine = {"plain": "LOCAL-TOKEN", "pw": "LOCAL-PW", "hash": "LOCAL-HASH", "email": "me@here.example"}
    home = tmp_path / "home"
    live = app_tenant(home, secrets=mine, tz="Europe/Paris", files={"plan.json": b"LOCAL PLAN"})
    before = _secret_state(live)
    res = B.restore(old, live, local_dir=home / "backups")
    assert res["files_restored"] == 0
    assert _rows(live, "SELECT value_json FROM user_settings WHERE key = 'athlete.timezone'") == [('"Asia/Taipei"',)]
    assert _secret_state(live) == before
    assert (home / "plan.json").read_bytes() == b"LOCAL PLAN"
    # a new machine with no login: the old backup's credentials are not carried over either
    fresh = app_tenant(tmp_path / "fresh", secrets={}, files={})
    B.restore(old, fresh, local_dir=tmp_path / "fresh" / "backups")
    assert _rows(fresh, "SELECT COUNT(*) FROM sync_state") == [(0,)]
    assert _rows(fresh, "SELECT COUNT(*) FROM debug_tokens") == [(0,)]
    assert _rows(fresh, "SELECT COUNT(*) FROM athletes") == [(1,)]


def test_restore_only_writes_registered_user_files_inside_the_tenant(tmp_path):
    db = app_tenant(tmp_path / "src", secrets={}, files={})
    m = {"app": B.APP, "format": B.FORMAT, "created_at": "2026-10-08T00:00:00+00:00",
         "db_sha256": B._sha256(db), "fit": {"included": False}, "files": {"count": 1, "bytes": 2}}
    bad = tmp_path / "crafted.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.write(db, B.DB_ENTRY)
        z.writestr(B.MANIFEST, json.dumps(m))
        z.writestr(f"{B.FILES_DIR}/plan.json", b"OK")
        for rel in ("secret.key", "weather.json", "tp_client.json", "backups/x.zip", "cache/render/a/b.json",
                    "routes/index.json", "../escape.json", "views/../../escape2.json", "/abs.json",
                    "views\\..\\..\\escape3.json", "wko5coach.db", "fit/coros/2026/a.fit", "logs/app.log"):
            z.writestr(f"{B.FILES_DIR}/{rel}", b"EVIL")
    home = tmp_path / "home"
    live = app_tenant(home, secrets={}, files={})
    res = B.restore(bad, live, local_dir=home / "backups")
    assert res["files_restored"] == 1 and (home / "plan.json").read_bytes() == b"OK"
    for p in tmp_path.rglob("*"):
        if p.is_file() and p.suffix != ".zip":
            assert p.read_bytes() != b"EVIL", p


# ------------------------------------------------------------------ API layer
def _app_db(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.db.models import Base
    path = tmp_path / "wko5coach.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    async def init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    return path, engine, async_sessionmaker(engine, expire_on_commit=False), init


def test_run_backup_auto_tick_and_settings(tmp_path, monkeypatch):
    from backend.api import backup as A
    from backend.settings.repository import SettingsRepository
    path, engine, factory, init = _app_db(tmp_path)
    monkeypatch.setattr(A, "_db_path", lambda: path)
    monkeypatch.setattr(A, "_fit_root", lambda: tmp_path / "fit")
    monkeypatch.setattr(A, "_roots", lambda: B.tenant_roots(tmp_path / "tenant"))
    monkeypatch.setattr(B, "detect_cloud_folders", lambda: [])
    out = tmp_path / "cloud" / "Backups"
    (tmp_path / "cloud").mkdir()
    (tmp_path / "tenant").mkdir()
    (tmp_path / "tenant" / "plan.json").write_text('{"events": []}')

    async def go():
        await init()
        assert await A.auto_tick(factory) is None                # no folder yet
        async with factory() as db:
            s = await A.put_settings(A.BackupSettingsBody(dir=str(out)), 1, db)
            assert s["dir"] == str(out) and s["auto"] is True and s["auto_due"]
        r = await A.auto_tick(factory)
        assert r["status"] == "ok" and r["trigger"] == "auto"
        assert await A.auto_tick(factory) is None                # < 24 h
        async with factory() as db:
            s = await A.status(SettingsRepository(db, 1))
            assert s["last_ok"]["name"] == r["name"] and len(s["backups"]) == 1
            assert "encrypted" not in s
            r2 = await A.run_backup(db, "manual")
            assert r2["status"] == "ok" and r2["name"].endswith(".zip") and "encrypted" not in r2
            info = await A.post_inspect(A.SourceBody(name=r2["name"]), 1, db)
            assert info["row_counts"]["user_settings"] >= 1 and "needs_password" not in info
            assert info["files"]["count"] == 1 and r2["user_files"] == 1       # the tenant's plan.json
            # an old encrypted backup in the folder: a clear 400, not 「只能還原…」
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as ei:
                await A._source(A.SourceBody(name="trailruncoach-backup-20260101-000000.zip.enc"),
                                SettingsRepository(db, 1))
            assert ei.value.status_code == 400 and "加密" in ei.value.detail
            # a missing folder is reported as a failed attempt, not an exception
            await SettingsRepository(db, 1).set("backup.dir", str(tmp_path / "gone" / "x"))
            r3 = await A.run_backup(db, "manual")
            assert r3["status"] == "failed" and r3["error"]
            assert (await SettingsRepository(db, 1).get("backup.last_ok"))["name"] == r2["name"]
            with pytest.raises(HTTPException):
                await A.put_settings(A.BackupSettingsBody(dir="not/absolute"), 1, db)
            with pytest.raises(HTTPException):
                await A._source(A.SourceBody(name="../wko5coach.db"), SettingsRepository(db, 1))
        await engine.dispose()
    asyncio.run(go())


def test_repository_validates_backup_keys(tmp_path):
    from backend.settings.repository import validate
    validate("backup.dir", None)
    validate("backup.dir", str(tmp_path))
    with pytest.raises(ValueError):
        validate("backup.dir", "relative")
    with pytest.raises(ValueError):
        validate("backup.auto", "yes")



def test_no_password_endpoints_and_upload_rejects_legacy_encrypted(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from backend.api import backup as A
    assert not hasattr(A, "put_password") and not hasattr(A, "delete_password")
    assert not any("/password" in getattr(r, "path", "") for r in A.router.routes)
    assert "password" not in A.SourceBody.model_fields
    monkeypatch.setattr(A, "_local_dir", lambda: tmp_path / "local")

    class Req:
        def __init__(self, data):
            self.data = data

        async def stream(self):
            yield self.data
    raw = _legacy_encrypted(tmp_path / "old.zip.enc").read_bytes()

    async def go():
        with pytest.raises(HTTPException) as ei:
            await A.post_upload(Req(raw))
        assert ei.value.status_code == 400 and "不再支援加密" in ei.value.detail
        assert not list((tmp_path / "local" / "staging").glob("*.bin"))     # not kept
        ok = await A.post_upload(Req(b"PK\x03\x04 a zip"))
        assert set(ok) == {"upload_id", "size"}
    asyncio.run(go())


def test_retired_backup_encryption_setting_is_deleted_on_start(tmp_path, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from backend.db import database
    from backend.settings.repository import DEFAULTS, RETIRED_KEYS, SettingsRepository, UnknownSetting
    assert "backup.encryption" in RETIRED_KEYS and "backup.encryption" not in DEFAULTS
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wko5coach.db'}")
    monkeypatch.setattr(database, "engine", engine)

    async def go():
        await database.init_db()
        async with engine.begin() as conn:
            await conn.execute(text("INSERT INTO user_settings (user_id, key, value_json, updated_at) VALUES "
                                    "(1, 'backup.encryption', '{\"key\": \"enc:v1:sealed\"}', '2026-10-01'), "
                                    "(1, 'backup.dir', '\"/x\"', '2026-10-01')"))
        await database.init_db()
        async with engine.begin() as conn:
            keys = [r[0] for r in await conn.execute(text("SELECT key FROM user_settings"))]
        assert keys == ["backup.dir"]
        from sqlalchemy.ext.asyncio import async_sessionmaker
        async with async_sessionmaker(engine)() as db:
            with pytest.raises(UnknownSetting):
                await SettingsRepository(db, 1).get("backup.encryption")
        await engine.dispose()
    asyncio.run(go())
