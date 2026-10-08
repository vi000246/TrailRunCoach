"""FitFolderDataset prerequisites for the COROS / TP back-test
(docs/research/unsourced-rules.md §0.10 step 0): trail classification from
the app DB, thresholds / weight not from the WKO5 athlete file, activity tags
matched to FIT workouts. Synthetic FITs and a tmp SQLite DB only — never the
WKO5 folder or the real app DB."""
import datetime as dt
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr import fitdataset as FD
from backend.engine.wko5expr.fitdataset import FitFolderDataset, classification_for, load_classifications, sport_of
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 9, 30)


def _db(tmp_path, rows, settings=()):
    """A minimal app DB: workout_files (+ athlete_settings) rows."""
    p = tmp_path / "app.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT, source TEXT, "
                "trail_classification TEXT, classification_overridden INTEGER, duplicate_of INTEGER)")
    con.execute("CREATE TABLE athlete_settings (id INTEGER PRIMARY KEY, athlete_id INTEGER, effective_date DATE, "
                "ftp_w REAL, weight_kg REAL, lthr INTEGER, threshold_pace_s_per_km REAL, run_ftp_w REAL)")
    con.executemany("INSERT INTO workout_files VALUES (?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO athlete_settings (athlete_id, effective_date, ftp_w, weight_kg, lthr, "
                    "threshold_pace_s_per_km, run_ftp_w) VALUES (1,?,?,?,?,?,?)", settings)
    con.commit()
    con.close()
    return p


def _fits(tmp_path, n=3):
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    paths = []
    for i in range(n):
        p = d / f"{i}_2026-09-0{i + 1}_run.fit"
        p.write_bytes(build_run(datetime(2026, 9, i + 1, 0, 0, tzinfo=timezone.utc), seconds=600))
        paths.append(p)
    return tmp_path / "fit" / "coros", paths


@pytest.fixture
def app_db(monkeypatch):
    """Point the dataset's app-DB lookup at a given path."""
    from backend.engine.wko5expr import datasource

    def use(path):
        monkeypatch.setattr(datasource, "_db_path", lambda: path)
    return use


# ---- item 1: trail classification -----------------------------------------

def test_sport_of_db_classification_wins_over_sub_sport():
    assert sport_of("running", None, "trail") == ("run", "trail running")
    assert sport_of("running", "trail", "road") == ("run", "running")          # user override to road
    assert sport_of("running", "treadmill", "road") == ("run", "indoor running")
    assert sport_of("running", "trail", None) == ("run", "trail running")      # no DB value: sub_sport
    assert sport_of("running", "trail", "unknown") == ("run", "trail running")
    assert sport_of("hiking", None, "trail") == ("walk", "hiking")             # only runs


def test_coros_trail_runs_classified_from_db(tmp_path, app_db):
    root, paths = _fits(tmp_path)
    app_db(_db(tmp_path, [
        (1, str(paths[0]), "coros", "trail", 0, None),       # auto trail
        (2, str(paths[1]), "coros", "road", 0, None),
        (3, str(paths[2]), "coros", "unknown", 0, None),
    ]))
    ds = FitFolderDataset(root, config=EngineConfig(parity=True), today=TODAY, estimate_thresholds=False)
    w0, w1, w2 = ds.workouts
    assert w0.sport_type == "trail running" and "runningtrail" in w0.tags
    assert w1.sport_type == "running" and w1.tags == []
    assert w2.sport_type == "running"                         # unknown + no sub_sport
    from backend.engine.racepower.athlete import is_trail
    assert [is_trail(w) for w in ds.workouts] == [True, False, False]


def test_user_override_and_duplicates(tmp_path):
    a, b, c = tmp_path / "coros" / "a.fit", tmp_path / "tp" / "a.fit", tmp_path / "coros" / "c.fit"
    rows = load_classifications(_db(tmp_path, [
        (1, str(b), "trainingpeaks", "trail", 1, None),      # canonical TP row, user marked trail
        (2, str(a), "coros", "road", 0, 1),                  # COROS duplicate, auto road
        (3, str(c), "coros", "trail", 1, None),              # user override on its own row
    ]))
    assert classification_for(a, rows) == "trail"             # the group's override wins
    assert classification_for(b, rows) == "trail"
    assert classification_for(c, rows) == "trail"
    assert classification_for(tmp_path / "none.fit", rows) is None


def test_override_on_duplicate_reaches_canonical(tmp_path):
    a, b = tmp_path / "coros" / "a.fit", tmp_path / "tp" / "x.fit"
    rows = load_classifications(_db(tmp_path, [
        (1, str(a), "coros", "trail", 0, None),              # canonical, auto trail
        (2, str(b), "trainingpeaks", "road", 1, 1),          # user said road on the TP copy
    ]))
    assert classification_for(a, rows) == "road"


def test_classification_override_changes_the_source_stamp(tmp_path, app_db, _fit_root_in_tmp):
    from backend.engine.wko5expr import datasource as DSRC
    (_fit_root_in_tmp / "coros").mkdir()
    db = _db(tmp_path, [(1, "x.fit", "coros", "road", 0, None)])
    app_db(db)
    s0 = DSRC.source_stamp("coros", tmp_path)
    con = sqlite3.connect(db)
    con.execute("UPDATE workout_files SET trail_classification='trail', classification_overridden=1")
    con.commit()
    con.close()
    assert DSRC.source_stamp("coros", tmp_path) != s0


def test_no_db_falls_back_to_sub_sport_first(tmp_path):
    d = tmp_path / "fit"
    d.mkdir()
    (d / "t.fit").write_bytes(build_run(datetime(2026, 9, 1, tzinfo=timezone.utc), seconds=300, sub_sport=1))
    ds = FitFolderDataset(d, config=EngineConfig(parity=True), today=TODAY)
    assert ds.workouts[0].sport_type == "indoor running"


# ---- item 2: thresholds / weight ------------------------------------------

def test_settings_from_db_not_wko5(tmp_path, app_db):
    root, _ = _fits(tmp_path, 1)
    app_db(_db(tmp_path, [], settings=[("2026-09-30", 200.0, 66.3, 182, None, None),
                                       ("2026-01-01", None, 68.0, None, 300.0, 210.0)]))
    ds = FitFolderDataset(root, config=EngineConfig(parity=True), today=TODAY, estimate_thresholds=False)
    assert ds.settings_from == "app"
    w = ds.workouts[0]                                        # 2026-09-01
    assert ds.setting("weight", w.day) == pytest.approx(68.0)
    assert ds.setting("weight", FD.date_to_day(dt.date(2026, 9, 30))) == pytest.approx(66.3)
    assert ds.sport_setting("ftp", w) == pytest.approx(210.0)
    assert ds.sport_setting("tpace", w) == pytest.approx(5.0)                 # 300 s/km
    # the COROS-profile LTHR 182 (sport unrecorded) is NOT a running threshold; the legacy
    # ftp_w 200 of an older DB is not read at all
    assert ds.sport_setting("thr", w) is None
    assert {x["field"] for x in ds.settings_ignored} == {"lthr"}
    assert ds.setting_label("weight").startswith("athlete_settings")
    assert ds.setting_label("runthr") == "未設定"


def test_athlete_settings_read_from_the_current_schema(tmp_path):
    """A DB made by the current models has no ftp_w column (legacy, unread): the rows still read."""
    from sqlalchemy import create_engine
    from backend.db.models import Base
    p = tmp_path / "new.db"
    eng = create_engine(f"sqlite:///{p}")
    Base.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(p)
    assert "ftp_w" not in {r[1] for r in con.execute("PRAGMA table_info(athlete_settings)")}
    con.execute("INSERT INTO athletes (id, name, data_dir, created_at) VALUES (1, 'a', 'd', '2026-01-01')")
    con.execute("INSERT INTO athlete_settings (athlete_id, effective_date, weight_kg, lthr) "
                "VALUES (1, '2026-09-30', 66.3, 171)")
    con.commit()
    con.close()
    rows = FD.read_athlete_settings(p)
    assert rows and rows[0]["weight_kg"] == pytest.approx(66.3) and rows[0]["lthr"] == 171
    assert "ftp_w" not in rows[0]


def test_plan_threshold_wins(tmp_path, app_db):
    from backend.engine.planning import Plan, Threshold
    root, _ = _fits(tmp_path, 1)
    app_db(_db(tmp_path, [], settings=[("2026-01-01", None, 68.0, None, None, 210.0)]))
    ds = FitFolderDataset(root, config=EngineConfig(parity=True), today=TODAY, estimate_thresholds=False)
    ds.plan = Plan(thresholds=[Threshold(date="2026-08-01", lthr=165.0, cp=230.0)])
    w = ds.workouts[0]
    assert ds.sport_setting("thr", w) == 165.0 and ds.cp(w) == 230.0


def test_wko5_settings_only_when_opted_in(tmp_path, _fit_root_in_tmp, monkeypatch):
    from backend.engine.wko5expr import datasource
    calls = []
    real = FD.FitFolderDataset

    def spy(*a, **k):
        calls.append(k.get("settings_dir"))
        return real(*a, **k, estimate_thresholds=False)
    monkeypatch.setattr(FD, "FitFolderDataset", spy)
    (_fit_root_in_tmp / "coros").mkdir()
    FD.dataset_for_source("coros", tmp_path / "wko5", config=EngineConfig(parity=True), today=TODAY)
    monkeypatch.setattr(datasource, "read_setting",
                        lambda k, d=None, u=1: True if k == FD.WKO5_OPT_IN_KEY else d)
    FD.dataset_for_source("coros", tmp_path / "wko5", config=EngineConfig(parity=True), today=TODAY)
    assert calls == [None, tmp_path / "wko5"]


def test_estimated_thresholds_fill_unset_dates(tmp_path, monkeypatch):
    """No plan / DB value: the as-of estimate sets runthr / runftp from the
    grid day on (never before), and hrTSS is recomputed with it."""
    from backend.engine.racepower import athlete as A
    import backend.engine.thresholds as TH
    d = tmp_path / "fit"
    d.mkdir()
    for i in range(4):
        start = datetime(2026, 6, 1, tzinfo=timezone.utc) + dt.timedelta(days=25 * i)
        (d / f"{i}.fit").write_bytes(build_run(start, seconds=1800, hr=150, power=220))
    monkeypatch.setattr(A, "cp_as_of", lambda ds, day: 240.0)
    monkeypatch.setattr(TH, "estimate", lambda ds, day, cp_of=None: {"lthr": {"value": 160.0}})
    assert FitFolderDataset(d, config=EngineConfig(parity=True), today=TODAY).athlete.settings == {}  # no app DB: auto off
    ds = FitFolderDataset(d, config=EngineConfig(parity=True), today=TODAY, estimate_thresholds=True)
    first = ds.workouts[0].entry.start.date()
    hist = ds.athlete.settings["runthr"]
    est_day = first + dt.timedelta(days=FD.ESTIMATE_STEP_DAYS)
    assert hist[0] == FD.NOT_BEFORE and hist[2][0] == est_day
    # SP-289: no watch LTHR → before the first estimate the 0.90 × max-HR prior (max HR from these
    # runs), never the estimate itself; the switch day is noted on the PMC
    assert hist[1][0] == first and ds.lthr_prior["until"] == est_day.isoformat()
    assert ds.sport_setting("thr", ds.workouts[0]) == ds.lthr_prior["value"] != 160.0
    late = ds.workouts[-1]
    assert ds.sport_setting("thr", late) == 160.0
    assert ds.sport_setting("ftp", late) is None and ds.cp(late) is None   # CP is never filled from the PD refit
    assert late.metrics["hrtss"] is not None and ds.workouts[0].metrics["hrtss"] is not None
    assert ds.setting_label("runthr").startswith("自動估算")


# ---- item 3: activity tags on COROS / TP workouts ---------------------------

def _trail_fits(tmp_path):
    """Two COROS-style runs (no sub_sport): 2025-06-14 07:31 and 2025-08-09
    20:54 Asia/Taipei, the first a trail run by the DB classification."""
    d = tmp_path / "fit" / "coros" / "2025"
    d.mkdir(parents=True)
    a, b = d / "1_2025-06-14_run.fit", d / "2_2025-08-09_run.fit"
    a.write_bytes(build_run(datetime(2025, 6, 13, 23, 31, tzinfo=timezone.utc), seconds=1800, speed_m_s=3.0))
    b.write_bytes(build_run(datetime(2025, 8, 9, 12, 54, tzinfo=timezone.utc), seconds=1680, speed_m_s=3.0))
    return tmp_path / "fit" / "coros", a, b


def test_tags_written_from_wko5_apply_to_coros_workouts(tmp_path, app_db, monkeypatch):
    """A tag stored with a WKO5 file name and its start minute (the seed run
    on the WKO5 source) is found for the COROS FIT of the same activity —
    the file differs, the local start matches within ±3 min."""
    from backend.engine import activity_tags as AT
    monkeypatch.setenv("WKO5COACH_TZ", "Asia/Taipei")
    root, a, b = _trail_fits(tmp_path)
    app_db(_db(tmp_path, [(1, str(a), "coros", "trail", 0, None), (2, str(b), "coros", "road", 0, None)]))
    ds = FitFolderDataset(root, config=EngineConfig(parity=True), today=TODAY, estimate_thresholds=False)
    race, road = ds.workouts
    assert race.entry.start == datetime(2025, 6, 14, 7, 31) and "runningtrail" in race.tags
    tags_db = tmp_path / "tags.db"
    AT.upsert(tags_db, start_local="2025-06-14T07:30", file="2025/Example_2025_06_14_07_30.wko4",
              activity_type="race")
    AT.upsert(tags_db, start_local="2025-08-09T20:54", file="2025/Example_2025_08_09_20_54.wko4",
              activity_type="training", effort="moderate")
    rows = AT.load(tags_db)
    assert AT.user_type(AT.find(rows, race.entry.start, race.entry.file)) == "race"
    u = AT.find(rows, road.entry.start, road.entry.file)
    assert AT.user_effort(u) == "moderate"
    assert AT.find(rows, road.entry.start + dt.timedelta(minutes=10), road.entry.file) is None


def test_seed_matches_coros_races_by_wko5_start(tmp_path, app_db, monkeypatch):
    from backend.engine import activity_tags as AT
    from backend.scripts import seed_activity_tags as SD
    monkeypatch.setenv("WKO5COACH_TZ", "Asia/Taipei")
    root, a, b = _trail_fits(tmp_path)
    app_db(_db(tmp_path, [(1, str(a), "coros", "trail", 0, None), (2, str(b), "coros", "road", 0, None)]))
    ds = FitFolderDataset(root, config=EngineConfig(parity=True), today=TODAY, estimate_thresholds=False)
    assert SD.start_of_file("Example_2025_06_14_07_30.wko4") == datetime(2025, 6, 14, 7, 30)
    items = SD.plan(ds.workouts, [], "coros", SD.EXAMPLE_SEED)
    by = {it["spec"]["date"]: it for it in items if it["found"]}
    race = by["2025-06-14"]
    assert race["how"] == "start" and race["file"] == "2025/1_2025-06-14_run.fit"
    assert race["start_local"] == "2025-06-14T07:31" and race["want"] == {"activity_type": "race"}
    # the road 5 km correction matches by date + distance (5.04 km within ±10 %)
    assert by["2025-08-09"]["file"] == "2025/2_2025-08-09_run.fit"
    # dry run only: nothing written anywhere
    assert AT.load(tmp_path / "app.db") == []


def test_no_db_falls_back_to_sub_sport(tmp_path):
    d = tmp_path / "fit"
    d.mkdir()
    (d / "t.fit").write_bytes(build_run(datetime(2026, 9, 1, tzinfo=timezone.utc), seconds=300, sub_sport=3))
    ds = FitFolderDataset(d, config=EngineConfig(parity=True), today=TODAY)
    assert ds.workouts[0].sport_type == "trail running" and "runningtrail" in ds.workouts[0].tags
