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
    ds = FitFolderDataset(root, config=EngineConfig(parity=True), today=TODAY)
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


def test_no_db_falls_back_to_sub_sport(tmp_path):
    d = tmp_path / "fit"
    d.mkdir()
    (d / "t.fit").write_bytes(build_run(datetime(2026, 9, 1, tzinfo=timezone.utc), seconds=300, sub_sport=3))
    ds = FitFolderDataset(d, config=EngineConfig(parity=True), today=TODAY)
    assert ds.workouts[0].sport_type == "trail running" and "runningtrail" in ds.workouts[0].tags
