"""/api/v1/scan on the per-source sync layout, and FitFolderDataset's time zone."""
import datetime as dt
import json
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from backend.db.models import WorkoutFile
from backend.engine.wko5expr import datasource as DS
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.files.file_service import scan_and_import
from backend.tests.fit_builder import build_run
from backend.tests.test_sync_e2e import make_session, run

T = datetime(2026, 9, 1, 22, 30, tzinfo=timezone.utc)


def _tree(root):
    c = root / "coros" / "2026"
    t = root / "tp" / "2026"
    c.mkdir(parents=True)
    t.mkdir(parents=True)
    (c / "480567774272323786_2026-09-02_run.fit").write_bytes(build_run(T, seconds=300))
    (t / "tp_2026_09_02_3933231656.fit").write_bytes(build_run(T + dt.timedelta(seconds=20), seconds=300))
    return c, t


def test_scan_walks_per_source_folders_tags_source_and_is_idempotent(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        _tree(_fit_root_in_tmp)
        r = await scan_and_import(s, 1, str(_fit_root_in_tmp))
        assert r["new"] == 2 and r["new_by_source"] == {"coros": 1, "trainingpeaks": 1}
        rows = {w.source: w for w in (await s.execute(select(WorkoutFile))).scalars()}
        assert rows["coros"].coros_activity_id == "480567774272323786"
        assert rows["trainingpeaks"].tp_workout_id == 3933231656
        # same activity from two sources: one canonical, one duplicate
        assert sum(w.duplicate_of is not None for w in rows.values()) == 1
        again = await scan_and_import(s, 1, str(_fit_root_in_tmp))
        assert again["new"] == 0 and again["skipped"] == 2
    run(go())


def test_scan_skips_files_the_sync_already_recorded(tmp_path, _fit_root_in_tmp):
    async def go():
        s = await make_session(tmp_path)
        c, t = _tree(_fit_root_in_tmp)
        # the sync recorded the COROS activity (different path spelling) and the TP workout id
        s.add(WorkoutFile(athlete_id=1, file_path=str(c / ".." / "2026" / "480567774272323786_2026-09-02_run.fit"),
                          file_format="fit", source="coros", coros_activity_id="480567774272323786"))
        s.add(WorkoutFile(athlete_id=1, file_path="elsewhere.fit", file_format="fit",
                          source="trainingpeaks", tp_workout_id=3933231656))
        await s.commit()
        r = await scan_and_import(s, 1, str(_fit_root_in_tmp))
        assert r["new"] == 0 and r["skipped"] == 2
    run(go())


def test_provider_id_dedup_is_per_athlete(tmp_path, _fit_root_in_tmp):
    """Another athlete's copy of the same COROS / TP activity doesn't hide it."""
    async def go():
        s = await make_session(tmp_path)
        _tree(_fit_root_in_tmp)
        s.add(WorkoutFile(athlete_id=2, file_path="other-coros.fit", file_format="fit",
                          source="coros", coros_activity_id="480567774272323786"))
        s.add(WorkoutFile(athlete_id=2, file_path="other-tp.fit", file_format="fit",
                          source="trainingpeaks", tp_workout_id=3933231656))
        await s.commit()
        r = await scan_and_import(s, 1, str(_fit_root_in_tmp))
        assert r["new"] == 2
    run(go())


def test_sync_ids_accepts_folder_and_db_source_names():
    from pathlib import Path
    from backend.files.file_service import _sync_ids
    tp = Path("tp_2026_09_02_3933231656.fit")
    assert _sync_ids("trainingpeaks", tp) == _sync_ids("tp", tp) == {"tp_workout_id": 3933231656}
    assert _sync_ids("coros", Path("480567774272323786_2026-09-02_run.fit")) == \
        {"coros_activity_id": "480567774272323786"}
    assert _sync_ids("local", tp) == {}


def test_scan_classic_year_layout_still_local(tmp_path):
    async def go():
        s = await make_session(tmp_path)
        d = tmp_path / "wko" / "2026"
        d.mkdir(parents=True)
        (d / "a.fit").write_bytes(build_run(T, seconds=120))
        r = await scan_and_import(s, 1, str(tmp_path / "wko"))
        assert r["new_by_source"] == {"local": 1}
    run(go())


def _settings_db(tmp_path, tz):
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE user_settings (id INTEGER PRIMARY KEY, user_id INT, key TEXT, value_json TEXT, updated_at TEXT)")
    con.execute("INSERT INTO user_settings (user_id, key, value_json) VALUES (1, 'athlete.timezone', ?)",
                (json.dumps(tz),))
    con.commit(); con.close()
    return db


def _one_fit(tmp_path):
    d = tmp_path / "fit" / "2026"
    d.mkdir(parents=True)
    (d / "a.fit").write_bytes(build_run(T, seconds=120))
    return tmp_path / "fit"


def test_fit_dataset_uses_the_athlete_timezone_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("WKO5COACH_TZ", "Europe/London")          # setting must win over env
    db = _settings_db(tmp_path, "Asia/Taipei")
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    ds = FitFolderDataset(_one_fit(tmp_path), config=EngineConfig(parity=True), today=dt.date(2026, 9, 30))
    assert str(ds.tz) == "Asia/Taipei"
    assert ds.workouts[0].entry.start == datetime(2026, 9, 2, 6, 30)


def test_fit_dataset_timezone_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WKO5COACH_TZ", "Europe/London")          # BST in September: UTC+1
    ds = FitFolderDataset(_one_fit(tmp_path), config=EngineConfig(parity=True), today=dt.date(2026, 9, 30))
    assert ds.workouts[0].entry.start == datetime(2026, 9, 1, 23, 30)


def test_fit_dataset_explicit_tz(tmp_path):
    ds = FitFolderDataset(_one_fit(tmp_path), config=EngineConfig(parity=True), today=dt.date(2026, 9, 30),
                          tz=ZoneInfo("America/New_York"))
    assert ds.workouts[0].entry.start == datetime(2026, 9, 1, 18, 30)
