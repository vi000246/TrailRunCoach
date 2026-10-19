"""SP-752: one 課表輸入 computation (api/plan_sessions._build_inputs) read the settings DB ~1,070 times —
read_setting and current_source for every session and activity it looked at, a new sqlite connection
each. It now computes inside datasource.settings_memo() (SP-685), and current_source is memoised there
too (its two settings and the WKO5 folder's glob). Synthetic DB only; the before / after counts on the
156-week demo athlete are on the ticket."""
from __future__ import annotations

import json
import sqlite3

import pytest

from backend.engine.wko5expr import datasource as DS


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE user_settings (user_id INTEGER, key TEXT, value_json TEXT)")
    con.execute("INSERT INTO user_settings VALUES (1, 'charts.data_source', ?)", (json.dumps("source"),))
    con.commit()
    con.close()
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    reads, globs = [], []
    real = DS._setting_row

    def counted(path, key, user_id):
        reads.append(key)
        return real(path, key, user_id)
    monkeypatch.setattr(DS, "_setting_row", counted)
    monkeypatch.setattr(DS, "wko5_available", lambda d=None: globs.append(d) or False)
    return db, reads, globs


def test_current_source_outside_the_memo_reads_every_time(settings_db):
    db, reads, globs = settings_db
    assert DS.current_source() == DS.current_source() == "coros"
    assert len(reads) == 4                                   # charts.data_source + the 資料來源, twice


def test_current_source_inside_the_memo_is_worked_out_once(settings_db):
    db, reads, globs = settings_db
    with DS.settings_memo():
        for _ in range(50):
            assert DS.current_source() == "coros"
    assert len(reads) == 2
    DS.current_source()                                      # the memo ends with the scope
    assert len(reads) == 4


def test_current_source_memo_is_per_tenant_and_folder(settings_db, monkeypatch):
    db, reads, globs = settings_db
    con = sqlite3.connect(db)
    con.execute("UPDATE user_settings SET value_json=? WHERE key='charts.data_source'", (json.dumps("wko5"),))
    con.commit()
    con.close()
    monkeypatch.setattr(DS, "wko5_available", lambda d=None: globs.append(d) or d is None)
    with DS.settings_memo():
        assert DS.current_source() == "wko5"
        assert DS.current_source(wko5_dir="/elsewhere") == "coros"      # another folder: its own answer
        monkeypatch.setattr(DS, "_tenant_id", lambda: "other")
        DS.current_source()
        assert len(globs) == 3                                         # another tenant: looked at again
        DS.current_source()
        assert len(globs) == 3


def test_the_key_and_the_computation_share_one_read(settings_db, monkeypatch):
    """The cache key (_inputs_key) and the computation read the same settings: once in all."""
    from backend.api import plan_sessions as PSA
    db, reads, globs = settings_db

    def key(blackouts=None):
        k = ("t-sp752", DS.read_setting("charts.data_source"), DS.current_source())
        return k, lambda: {"src": [DS.read_setting("charts.data_source"), DS.current_source()]}
    monkeypatch.setattr(PSA, "_inputs_key", key)
    monkeypatch.setattr(PSA, "_remember", lambda key, out: None)
    PSA._cache.pop(key()[0], None)
    reads.clear()
    with PSA._lock:
        PSA._cache.clear()
    out = PSA._compute_inputs()
    assert out == {"src": ["source", "coros"]}
    assert sorted(reads) == ["charts.data_source", "sync.primary_source"]
    assert DS._SETTINGS_MEMO.get() is None


def test_the_inputs_compute_inside_the_memo(monkeypatch):
    from backend.api import plan_sessions as PSA
    seen = []
    monkeypatch.setattr(PSA, "_build_inputs_in", lambda *a: seen.append(DS._SETTINGS_MEMO.get() is not None) or {})
    PSA._build_inputs(("k",), None, None, None, None, None, True, True, None)
    assert seen == [True] and DS._SETTINGS_MEMO.get() is None
