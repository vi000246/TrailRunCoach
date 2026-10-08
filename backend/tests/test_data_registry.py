"""The data registry (backend/data_registry.py, SP-311): every table and tenant file kind has a
class, backup reads it, and the SECRET class never reaches an API response, an export or a sync.
Synthetic folders / DBs in tmp_path only. A built demo tenant is walked in test_demo_smoke.py."""
import asyncio
import json
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from backend import data_registry as R
from backend.db.models import Base

REPO_BACKEND = Path(__file__).resolve().parents[1]

# one path of each kind a tenant folder holds -> its class
SAMPLES = {
    "wko5coach.db": R.PER_TABLE, "wko5coach.db-wal": R.PER_TABLE, "wko5coach.db-shm": R.PER_TABLE,
    "plan.json": R.USER, "racepower_solo_hikes.json": R.USER, "racepower_hike_meta.json": R.USER,
    "racepower_shares/abcdefghijklmnop.json": R.USER,
    "event_gpx/demo-50k.gz": R.IMPORTED, "template_gpx/3.gz": R.IMPORTED,
    "backups/trailruncoach-prerestore-20261007-120000.zip": R.SECRET,
    "backups/.trc-restore-x/restore.db": R.SECRET,
    "engine.json": R.USER, "corrections.json": R.USER, "annotations.json": R.USER, "views/mine.json": R.USER,
    "fit/coros/2026/123_2026-10-01_trailrun.fit": R.IMPORTED, "fit/tp/2025/tp_2025_01_02_99.fit": R.IMPORTED,
    "fit/coros/2026/.124.download": R.IMPORTED,
    "routes/names.json": R.USER, "routes/index.json": R.DERIVED, "routes/manifest.json": R.DERIVED,
    "routes/tracks/2026__demo_0001.fit.json": R.DERIVED, "routes/activity_weather.json": R.DERIVED,
    "routes/weather/25.000_121.000_2026-01-01.json": R.DERIVED,
    "cache/fit/0123456789ab/index.json": R.DERIVED, "cache/fit/0123456789ab/home.json": R.DERIVED,
    "cache/fit/0123456789ab/ch/0123456789abcdef0123.npz": R.DERIVED,
    "cache/fit/0123456789ab/ch/0123.1.2.tmp.npz": R.DERIVED,
    "cache/fit/0123456789ab/estimate.json": R.DERIVED, "cache/fit/0123456789ab/pd_mftp.json": R.DERIVED,
    "cache/fit/0123456789ab/series_workout_review_v23_6413a5d5.json": R.DERIVED,
    "cache/render/ab/abcdef.json": R.DERIVED,
    "achievements_cache.json": R.DERIVED, "channel_peaks.json": R.DERIVED, "workout_curves.json": R.DERIVED,
    "power_source_v1.json": R.DERIVED, "bad_activity_v1.json": R.DERIVED, "tp_tss.json": R.DERIVED,
    "moving_hrtss.json": R.DERIVED, "series_mhr_peak5_v2_19a00de4.json": R.DERIVED,
    "activity_auto_0123abcd.json": R.DERIVED, "racepower_cptests.json": R.DERIVED,
    "racepower_power_source.json": R.DERIVED, "racepower_bad_activity.json": R.DERIVED,
    "racepower_training_env.json": R.DERIVED, "racepower_backtest.json": R.DERIVED,
    "cwa_F-B0053-035.json": R.DERIVED, "cwa_town_elevation.json": R.DERIVED,
    "climatology/25.00_121.00_1000_2026-10-10_1d.json": R.DERIVED,
    "channel_peaks.json.tmp": R.DERIVED, "plan.json.tmp": R.DERIVED,
    "weather.json": R.SECRET, "secret.key": R.SECRET, "tp_client.json": R.SECRET,
    "logs/app.log": R.DERIVED, "logs/app.log.1": R.DERIVED, "research/coros-tl/20261001-120000/x.json": R.DERIVED,
    "fits/123_2025-01-01_run.fit": R.IMPORTED, "base/current": R.DERIVED,
    "sandboxes/0123456789abcdef0123456789abcdef/plan.json": R.DERIVED,
    "meta.json": R.DERIVED, "demo_manifest.json": R.DERIVED, "demo_courses/trail_50k.gpx": R.IMPORTED,
}


def _con_with_schema(path: Path) -> sqlite3.Connection:
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng)
    eng.dispose()
    from backend.engine import event_gpx, race_calc_store
    con = sqlite3.connect(str(path))
    con.execute(event_gpx.DDL)                  # plain-sqlite DDL of two tables (same as the models)
    con.execute(race_calc_store.DDL)
    con.commit()
    return con


# ---------------------------------------------------------------- tables
def test_every_table_of_the_schema_has_a_class_and_no_stale_entry():
    schema = set(Base.metadata.tables)
    registered = {t.name for t in R.TABLES}
    assert schema - registered == set(), "add these tables to backend/data_registry.py TABLES"
    assert registered - schema == set(), "registered tables the schema no longer has"
    assert all(t.cls in R.CLASSES for t in R.TABLES)
    assert len(registered) == len(R.TABLES)


def test_a_db_table_without_a_class_is_reported(tmp_path):
    con = _con_with_schema(tmp_path / "wko5coach.db")
    try:
        assert R.unclassified_tables(con) == []
        con.execute("CREATE TABLE brand_new_feature (id INTEGER PRIMARY KEY)")
        assert R.unclassified_tables(con) == ["brand_new_feature"]
    finally:
        con.close()


def test_legacy_tables_of_an_older_db_are_known_but_not_in_the_schema(tmp_path):
    """workout_metrics / mmp_cache: written at import, never read; an older DB keeps them (no
    destructive migration), a new one no longer has them, and neither is reported unclassified."""
    assert set(R.LEGACY_TABLES) == {"workout_metrics", "mmp_cache"}
    assert not set(R.LEGACY_TABLES) & set(Base.metadata.tables)
    assert not set(R.LEGACY_TABLES) & {t.name for t in R.TABLES} and not set(R.LEGACY_TABLES) & set(R.RETIRED_TABLES)
    con = _con_with_schema(tmp_path / "wko5coach.db")
    try:
        con.execute("CREATE TABLE workout_metrics (id INTEGER PRIMARY KEY, workout_id INTEGER, metric_key TEXT)")
        con.execute("CREATE TABLE mmp_cache (id INTEGER PRIMARY KEY, workout_id INTEGER, duration_s INTEGER)")
        assert R.unclassified_tables(con) == []
    finally:
        con.close()


def test_columns_named_by_the_registry_exist():
    for t in R.TABLES:
        cols = set(Base.metadata.tables[t.name].columns.keys())
        for f in t.user_fields + t.secret_fields + t.deidentify:
            assert f in cols, f"{t.name}.{f}"


def test_workout_files_marks_its_user_fields_and_sync_state_is_secret():
    wf, ss = R.table("workout_files"), R.table("sync_state")
    assert wf.cls == R.IMPORTED and set(wf.user_fields) == {"trail_classification", "classification_overridden"}
    assert ss.cls == R.SECRET
    from backend.settings import secrets
    sealed = {c for t, c in secrets.SEALED_DB_COLUMNS if t == "sync_state"}
    assert sealed <= set(ss.secret_fields)
    assert {"coros_password_sealed", "tp_password_sealed"} <= set(ss.secret_fields)
    # SP-371: the debug API's token hashes are credentials too
    assert [t.name for t in R.of_class(R.SECRET)[0]] == ["sync_state", "debug_tokens"]


# ---------------------------------------------------------------- files
def test_every_kind_of_tenant_file_has_its_class():
    got = {p: (R.classify(p).cls if R.classify(p) else None) for p in SAMPLES}
    assert got == SAMPLES
    for p in ("new_cache.json", "cache/other/x.json", "plan.yaml", "fit", "routesx/index.json"):
        assert R.classify(p) is None, p


def test_a_file_without_a_class_is_reported(tmp_path):
    for rel in SAMPLES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    assert R.unclassified_files(tmp_path) == []
    (tmp_path / "new_cache.json").write_text("{}")
    (tmp_path / "cache" / "other").mkdir(parents=True)
    (tmp_path / "cache" / "other" / "x.json").write_text("{}")
    assert R.unclassified_files(tmp_path) == ["cache/other/x.json", "new_cache.json"]


def test_entries_are_well_formed():
    patterns = [f.pattern for f in R.FILES]
    assert len(patterns) == len(set(patterns))
    for f in R.FILES:
        assert f.cls in R.CLASSES or (f.cls == R.PER_TABLE and f.pattern.startswith("wko5coach.db")), f.pattern
        assert f.where in (R.ROOT, R.BASE, R.SHARED, R.ANYWHERE) and f.scope in (R.TENANT, R.INSTANCE, R.DEMO)
        assert f.backup in (R.ALWAYS, R.OPT_IN, R.NEVER)
        if f.cls == R.SECRET:
            assert f.backup == R.NEVER, f.pattern


def test_the_code_writes_where_the_registry_says(tmp_path, monkeypatch):
    """The paths the modules build for a tenant land on entries of the expected class (a file
    renamed or moved in code without the registry fails here)."""
    from backend import tenancy
    from backend.engine import achievements, event_gpx, planning, routes, user_templates
    from backend.engine.racepower import athlete, backtest, cptest, share, weather
    from backend.engine.wko5expr import config, corrections, customviews, fitcache, render_cache
    from backend.engine.wko5expr import dataset
    from backend.settings import secrets
    from backend.sync import storage
    from backend.demo import sandbox
    from backend import applog
    for mod, attr in ((event_gpx, "ROOT"), (user_templates, "ROOT"), (storage, "FIT_ROOT"), (routes, "HOME"),
                      (athlete, "HIKE_META"), (athlete, "SOLO_HIKES"), (athlete, "TRAINING_ENV_CACHE"),
                      (backtest, "STORE"), (share, "SHARES_DIR"), (weather, "HOME"), (weather, "KEY_PATH"),
                      (achievements, "CACHE_PATH"), (achievements, "ANNOTATIONS_PATH"),
                      (config, "CONFIG_PATH"), (corrections, "CORRECTIONS_PATH"), (customviews, "USER_VIEWS"),
                      (render_cache, "CACHE_DIR"), (dataset, "_CACHE_DIR")):
        monkeypatch.setattr(mod, attr, None)
    monkeypatch.delenv(fitcache.ENV_ROOT, raising=False)
    monkeypatch.delenv("WKO5COACH_ROUTES_DIR", raising=False)
    monkeypatch.setenv(tenancy.ENV_HOME, str(tmp_path))
    t = tenancy.Tenant(id="u1", kind=tenancy.USER, root=tmp_path, shared=tmp_path)
    with tenancy.use(t):
        cases = {
            tenancy.db_path(): R.PER_TABLE,
            planning.plan_path(): R.USER,
            athlete._solo_hikes_path(): R.USER,
            athlete._hike_meta_path(): R.USER,
            share._shares_dir() / "abcdefghijklmnop.json": R.USER,
            event_gpx.file_path("demo-50k"): R.IMPORTED,
            user_templates.gpx_path(3): R.IMPORTED,
            config.config_path(): R.USER,
            corrections.corrections_path(): R.USER,
            achievements.annotations_path(): R.USER,
            customviews.user_views() / "v.json": R.USER,
            storage.source_dir("coros") / "2026" / "1_2026-01-01_run.fit": R.IMPORTED,
            storage.source_dir("tp") / "2026" / "tp_2026_01_01_1.fit": R.IMPORTED,
            routes.RouteStore().names_path: R.USER,
            routes.RouteStore().index_path: R.DERIVED,
            routes.RouteStore()._track_path("2026/a.fit"): R.DERIVED,
            fitcache.root() / "0123456789ab" / fitcache.HOME_MARK: R.DERIVED,
            render_cache.cache_dir() / "ab" / "abc.json": R.DERIVED,
            achievements.cache_path(): R.DERIVED,
            dataset._cache_dir() / "channel_peaks.json": R.DERIVED,
            weather.home() / cptest.CACHE_NAME: R.DERIVED,
            weather.home() / cptest.POWER_CACHE_NAME: R.DERIVED,
            weather.home() / cptest.BAD_CACHE_NAME: R.DERIVED,
            weather.home() / weather.ELEV_CACHE: R.DERIVED,
            weather.clim_cache_path(weather.home(), (25.0, 121.0, 1000.0), datetime(2026, 10, 1).date(), 1):
                R.DERIVED,
            athlete._training_env_cache(): R.DERIVED,
            backtest._store(): R.DERIVED,
            weather.key_path(): R.SECRET,
            tenancy.home_root() / secrets.KEY_FILE.name: R.SECRET,
            applog.log_file() or tenancy.home_root() / "logs" / "app.log": R.DERIVED,
        }
        for p, cls in cases.items():
            rel = Path(p).relative_to(tmp_path).as_posix()
            e = R.classify(rel)
            assert e is not None and e.cls == cls, (rel, e)
    for name in sandbox.PRIVATE_FILES + sandbox.PRIVATE_DIRS:     # what a sandbox copies: private data
        e = R.classify(name if "." in name else name + "/x")
        assert e is not None and e.where == R.ROOT and e.cls in (R.USER, R.IMPORTED), name


# ---------------------------------------------------------------- backup
def _tenant(tmp_path: Path) -> tuple[Path, Path]:
    """A tenant folder holding one file of every registered kind; (db, fit root)."""
    home = tmp_path / "home"
    for rel in SAMPLES:
        if rel.startswith("wko5coach.db") or rel.endswith(".download"):
            continue
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
    con = _con_with_schema(home / "wko5coach.db")
    con.execute("INSERT INTO athletes (id, name, data_dir, created_at) VALUES (1, 'a', 'd', '2026-01-01')")
    con.execute("INSERT INTO sync_state (athlete_id, coros_access_token) VALUES (1, 'enc:v1:sealed')")
    con.commit()
    con.close()
    return home / "wko5coach.db", home / "fit"


def test_backup_reads_the_registry_and_holds_what_it_held_before(tmp_path):
    from backend.engine import backup as B
    assert R.backup_entries(False) == [R.DB] and R.backup_entries(True) == [R.DB, R.FIT]
    assert B.DB_ENTRY == "wko5coach.db" and B.FIT_DIR == "fit"
    db, fit = _tenant(tmp_path)
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)
    plain = B.create_backup(db, tmp_path / "out1", fit_root=fit, include_fit=False, now=now)
    with_fit = B.create_backup(db, tmp_path / "out2", fit_root=fit, include_fit=True, now=now)
    with zipfile.ZipFile(plain["path"]) as z:
        assert set(z.namelist()) == {"manifest.json", "wko5coach.db"}
        assert json.loads(z.read("manifest.json"))["fit"] == {"included": False, "files": 0, "bytes": 0}
    with zipfile.ZipFile(with_fit["path"]) as z:
        assert set(z.namelist()) == {"manifest.json", "wko5coach.db",
                                     "fit/coros/2026/123_2026-10-01_trailrun.fit.gz",
                                     "fit/tp/2025/tp_2025_01_02_99.fit.gz"}
        assert json.loads(z.read("manifest.json"))["fit"] == {"included": True, "files": 2, "bytes": 20}
    # every table rides in the DB snapshot, sync_state included (sealed values, as stored)
    assert set(plain["row_counts"]) == {t.name for t in R.TABLES}


def test_backup_refuses_a_registry_entry_it_cannot_write(tmp_path, monkeypatch):
    from backend.engine import backup as B
    extra = R.File("plan.json", R.USER, R.ROOT, "test", backup=R.ALWAYS)
    monkeypatch.setattr(R, "FILES", R.FILES + (extra,))
    db, fit = _tenant(tmp_path)
    with pytest.raises(B.BackupError):
        B.create_backup(db, tmp_path / "out", fit_root=fit)
    assert not list((tmp_path / "out").glob("*"))          # nothing half-written


# ---------------------------------------------------------------- secret
SECRET_USERS = {      # the modules that may read the credential columns: login, sync, status flags
    "api/auth.py", "api/plan_sessions.py", "api/sync.py", "db/database.py", "db/models.py",
    "settings/secrets.py", "sync/coros_client.py", "sync/runner.py", "sync/session_check.py",
    "sync/tp_client.py", "data_registry.py",
}
SECRET_FILE_USERS = {"secret.key": {"settings/secrets.py"}, "tp_client.json": {"sync/tp_client.py"},
                     "weather.json": {"engine/racepower/weather.py"}}


def _code_words():
    """{module: (identifiers + attribute names, string constants)} of backend/ (no tests /
    scripts), docstrings and comments left out."""
    import ast
    out = {}
    for p in sorted(REPO_BACKEND.rglob("*.py")):
        rel = p.relative_to(REPO_BACKEND).as_posix()
        if rel.startswith(("tests/", "scripts/")):
            continue
        tree = ast.parse(p.read_text("utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        names, strings = set(), []
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                names.add(n.id)
            elif isinstance(n, ast.Attribute):
                names.add(n.attr)
            elif isinstance(n, ast.keyword) and n.arg:
                names.add(n.arg)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                strings.append(n.value)
        out[rel] = (names, strings)
    return out


def test_secret_columns_and_files_are_only_used_by_login_and_sync_code():
    """An export, a device sync or any new module reading a credential fails here: review it
    (SECRET never leaves the server), then allow the module only if it does not."""
    cols = set(R.table("sync_state").secret_fields)
    rx = re.compile(r"\b(" + "|".join(cols) + r")\b")
    code = _code_words()
    users = {rel for rel, (names, strings) in code.items() if names & cols or any(rx.search(s) for s in strings)}
    assert users <= SECRET_USERS, f"credential columns read by {sorted(users - SECRET_USERS)}"
    for name, allowed in SECRET_FILE_USERS.items():
        users = {rel for rel, (_, strings) in code.items() if name in strings} - {"data_registry.py"}
        assert users <= allowed, f"{name} read by {sorted(users - allowed)}"


def test_no_api_response_carries_a_secret(tmp_path, monkeypatch):
    """Credentials planted in sync_state (sealed and legacy plaintext) appear in no GET response of
    the routers that read sync_state (the static demo export saves GET responses)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import auth, sync
    from backend.db import database
    from backend.settings import secrets
    db = tmp_path / "wko5coach.db"
    monkeypatch.setattr(database, "DB_PATH", db)
    monkeypatch.setattr(database, "engine", None)
    plain = {c: f"SENTINEL-{c}-{i}" for i, c in enumerate(R.table("sync_state").secret_fields)}
    stored = {c: (secrets.seal(v) if i % 2 == 0 else v) for i, (c, v) in enumerate(plain.items())}
    con = _con_with_schema(db)
    con.execute("INSERT INTO athletes (id, name, data_dir, created_at) VALUES (1, 'a', 'd', '2026-01-01')")
    con.execute(f"INSERT INTO sync_state (athlete_id, coros_email, coros_token_expires, {', '.join(stored)}) "
                f"VALUES (1, 'me@example.com', '2099-01-01 00:00:00', {', '.join('?' * len(stored))})",
                list(stored.values()))
    con.commit()
    con.close()
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(sync.router)
    skip = {"/api/v1/sync/compare", "/api/v1/auth/tp/oauth"}     # neither reads sync_state
    seen = {}
    with TestClient(app, raise_server_exceptions=False) as c:
        try:
            for r in auth.router.routes + sync.router.routes:
                if "GET" not in (getattr(r, "methods", None) or ()) or "{" in r.path or r.path in skip:
                    continue
                resp = c.get(r.path)
                seen[r.path] = resp.status_code
                for v in list(plain.values()) + list(stored.values()):
                    assert v not in resp.text, (r.path, v[:20])
        finally:
            c.portal.call(database.dispose, db)
    ok = {p for p, s in seen.items() if s == 200}
    assert {"/api/v1/auth/coros/status", "/api/v1/auth/tp/status", "/api/v1/sync/status"} <= ok, seen
