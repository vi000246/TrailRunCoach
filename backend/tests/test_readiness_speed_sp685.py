"""SP-685: the 專項期 賽前準備 and 減量期 減量檢查 cards were slow — one card classified every activity of ten
weeks and each classification re-read the same settings (課表偏好, the HR account, the data source …), opening
the settings DB about 480 times per card. Inside datasource.settings_memo() each (DB, user, key) is read once;
the two cards (and the cache-key stamp read on every request) compute inside it. Synthetic DB only.

Measured on the 13-week demo athlete (owner mode, desktop, quiet server, render cache cleared = a new day):
專項期 0.53 → 0.14 s, 減量期 0.55 → 0.18 s, a cache hit 0.12 → 0.05 s (scratchpad bench, see the ticket)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest

from backend.engine.wko5expr import datasource as DS


@pytest.fixture
def settings_db(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE user_settings (user_id INTEGER, key TEXT, value_json TEXT)")
    con.execute("INSERT INTO user_settings VALUES (1, 'a', ?)", (json.dumps({"x": [1, 2]}),))
    con.commit()
    con.close()
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    reads = []
    real = DS._setting_row

    def counted(path, key, user_id):
        reads.append(key)
        return real(path, key, user_id)
    monkeypatch.setattr(DS, "_setting_row", counted)
    return db, reads


def test_outside_the_memo_every_call_reads_the_db(settings_db):
    db, reads = settings_db
    assert DS.read_setting("a") == {"x": [1, 2]}
    assert DS.read_setting("a") == {"x": [1, 2]}
    assert DS.read_setting("missing", 7) == 7
    assert reads == ["a", "a", "missing"]


def test_inside_the_memo_each_key_is_read_once_and_copies_are_independent(settings_db):
    db, reads = settings_db
    with DS.settings_memo():
        v1 = DS.read_setting("a")
        v1["x"].append(3)                      # a caller changing its copy does not change the next one
        v2 = DS.read_setting("a")
        assert v2 == {"x": [1, 2]}
        assert DS.read_setting("missing", 7) == 7 and DS.read_setting("missing") is None
        with DS.settings_memo():               # nested: the outer memo
            DS.read_setting("a")
    assert reads == ["a", "missing"]
    DS.read_setting("a")                       # the memo ends with the scope
    assert reads == ["a", "missing", "a"]


def test_the_memo_is_per_db_and_user(settings_db, tmp_path, monkeypatch):
    db, reads = settings_db
    with DS.settings_memo():
        DS.read_setting("a")
        DS.read_setting("a", user_id=2)
        other = tmp_path / "t.db"
        con = sqlite3.connect(other)
        con.execute("CREATE TABLE user_settings (user_id INTEGER, key TEXT, value_json TEXT)")
        con.execute("INSERT INTO user_settings VALUES (1, 'a', '5')")
        con.commit()
        con.close()
        monkeypatch.setattr(DS, "_db_path", lambda: other)
        assert DS.read_setting("a") == 5       # another tenant's DB is not served the first one's value
    assert reads == ["a", "a", "a"]


def test_both_cards_and_the_stamp_compute_inside_the_memo(monkeypatch):
    from backend.engine import race_readiness as RD
    seen = []
    monkeypatch.setattr(RD, "_compute", lambda *a, **k: seen.append(DS._SETTINGS_MEMO.get() is not None) or {})
    monkeypatch.setattr(RD, "_compute_taper", lambda *a, **k: seen.append(DS._SETTINGS_MEMO.get() is not None) or {})
    RD.compute(None, None, dt.date(2026, 10, 19))
    RD.compute_taper(None, None, dt.date(2026, 10, 19))
    from backend.api import wko5views as W
    monkeypatch.setattr(W, "_readiness_panel", lambda *a, **k: seen.append(DS._SETTINGS_MEMO.get() is not None) or {})
    W.readiness_panel({}, None, 0, 0)
    from backend.engine import injuries as INJ
    monkeypatch.setattr(INJ, "load_events", lambda: seen.append(DS._SETTINGS_MEMO.get() is not None) or [])
    W.readiness_stamp()
    assert len(seen) >= 4 and all(seen), seen        # the stamp reads the injuries more than once
    assert DS._SETTINGS_MEMO.get() is None
