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
    assert B.NAME_RE.match(r["name"]) and not r["encrypted"]
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


def test_encrypted_round_trip_wrong_password_and_truncation(tmp_path):
    db = make_db(tmp_path / "app.db", rows=50)
    k = B.new_key("correct horse")
    r = B.create_backup(db, tmp_path / "out", key=k)
    p = Path(r["path"])
    assert r["name"].endswith(".zip.enc") and B.is_encrypted(p)
    assert b"workout_files" not in p.read_bytes()
    info = B.inspect(p, tmp_path, "correct horse")
    assert info["row_counts"]["workout_files"] == 50
    with pytest.raises(B.BackupError, match="密碼"):
        B.inspect(p, tmp_path, "wrong password")
    with pytest.raises(B.BackupError, match="密碼"):
        B.inspect(p, tmp_path, None)
    cut = tmp_path / "cut.enc"
    cut.write_bytes(p.read_bytes()[:-20])
    with pytest.raises(B.BackupError):
        B.inspect(cut, tmp_path, "correct horse")


def test_multi_chunk_encryption(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "CHUNK", 1000)
    src = tmp_path / "plain.bin"
    src.write_bytes(bytes(range(256)) * 20)            # 5120 bytes = 6 chunks
    salt = b"s" * 16
    key = B.derive_key("pw-pw-pw-pw", salt, n=1 << 10)
    B.encrypt_file(src, tmp_path / "x.enc", key, salt, 1 << 10, 8, 1)
    B.decrypt_file(tmp_path / "x.enc", tmp_path / "back.bin", "pw-pw-pw-pw")
    assert (tmp_path / "back.bin").read_bytes() == src.read_bytes()
    # dropping the last chunk is detected
    raw = (tmp_path / "x.enc").read_bytes()
    head = len(B.MAGIC) + 26
    chunks, i = [], head
    while i < len(raw):
        n = int.from_bytes(raw[i:i + 4], "big")
        chunks.append(raw[i:i + 4 + n])
        i += 4 + n
    (tmp_path / "short.enc").write_bytes(raw[:head] + b"".join(chunks[:-1]))
    with pytest.raises(B.BackupError, match="不完整"):
        B.decrypt_file(tmp_path / "short.enc", tmp_path / "o.bin", "pw-pw-pw-pw")


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
    monkeypatch.setattr(B, "detect_cloud_folders", lambda: [])
    out = tmp_path / "cloud" / "Backups"
    (tmp_path / "cloud").mkdir()

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
            # encryption: the password is not stored, only a sealed key
            s = await A.put_password(A.PasswordBody(password="long enough pw"), 1, db)
            assert s["encrypted"]
            enc = await SettingsRepository(db, 1).get("backup.encryption")
            assert "long enough pw" not in json.dumps(enc) and enc["key"].startswith("enc:v1:")
            r2 = await A.run_backup(db, "manual")
            assert r2["status"] == "ok" and r2["encrypted"]
            info = await A.post_inspect(A.SourceBody(name=r2["name"]), 1, db)
            assert info["needs_password"]
            info = await A.post_inspect(A.SourceBody(name=r2["name"], password="long enough pw"), 1, db)
            assert info["row_counts"]["user_settings"] >= 1
            # a missing folder is reported as a failed attempt, not an exception
            await SettingsRepository(db, 1).set("backup.dir", str(tmp_path / "gone" / "x"))
            r3 = await A.run_backup(db, "manual")
            assert r3["status"] == "failed" and r3["error"]
            assert (await SettingsRepository(db, 1).get("backup.last_ok"))["name"] == r2["name"]
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                await A.put_settings(A.BackupSettingsBody(dir="not/absolute"), 1, db)
            with pytest.raises(HTTPException):
                await A.put_password(A.PasswordBody(password="short"), 1, db)
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
    with pytest.raises(ValueError):
        validate("backup.encryption", {"salt": "00"})
