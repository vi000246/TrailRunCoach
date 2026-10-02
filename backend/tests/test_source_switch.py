"""Chart data-source helpers: current_source (sync read of the settings
store), source_stamp, and the two-source comparison."""
import datetime as dt
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from backend.engine.wko5expr import datasource as DS
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.sourcecompare import compare
from backend.tests.fit_builder import build_run


def _db(tmp_path, value):
    db = tmp_path / "w.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE user_settings (id INTEGER PRIMARY KEY, user_id INT, key TEXT, value_json TEXT, updated_at TEXT)")
    if value is not None:
        con.execute("INSERT INTO user_settings (user_id, key, value_json) VALUES (1, 'charts.data_source', ?)",
                    (json.dumps(value),))
    con.commit(); con.close()
    return db


@pytest.mark.parametrize("stored,expect", [(None, "synced"), ("coros", "coros"), ("tp", "tp"), ("wko5", "wko5"),
                                           ("synced", "synced"), ("garmin", "synced")])
def test_current_source(tmp_path, monkeypatch, stored, expect):
    db = _db(tmp_path, stored)
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    assert DS.current_source() == expect


def test_current_source_without_db(monkeypatch, tmp_path):
    monkeypatch.setattr(DS, "_db_path", lambda: tmp_path / "missing.db")
    assert DS.current_source() == "wko5"


def test_source_stamp_changes_with_files(_fit_root_in_tmp, tmp_path):
    s0 = DS.source_stamp("coros", tmp_path)
    d = _fit_root_in_tmp / "coros" / "2026"
    d.mkdir(parents=True)
    (d / "a.fit").write_bytes(b"x")
    s1 = DS.source_stamp("coros", tmp_path)
    assert s0 != s1 and s1.startswith("coros:1:")


def test_compare_coros_vs_tp(_fit_root_in_tmp, tmp_path):
    t = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    for src, off, power in (("coros", 0, 250), ("tp", 30, 262)):
        d = _fit_root_in_tmp / src / "2026"
        d.mkdir(parents=True)
        (d / "a.fit").write_bytes(build_run(t + dt.timedelta(seconds=off), seconds=900, power=power))
    (_fit_root_in_tmp / "coros" / "2026" / "b.fit").write_bytes(
        build_run(t + dt.timedelta(days=1), seconds=600))
    r = compare("coros", "tp", tmp_path / "no-wko5", config=EngineConfig(parity=True))
    assert r["matched"] == 1 and r["only_a"] == 1 and r["only_b"] == 0
    row = r["rows"][0]
    assert row["metrics"]["np"]["a"] == pytest.approx(250, rel=0.01)
    assert "np" in row["flags"] and "duration" not in row["flags"]
