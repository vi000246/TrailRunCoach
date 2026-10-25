"""
SP-829: the 維持期's 24 weeks (SP-102) count from where training last started again — the first run after
the last gap of ≥ LAYOFF_WEEKS (4, 推估) weeks without a run — not from the first day of data, so a new
user importing years of old data isn't in a 維持期 from day one. Synthetic data only.
"""
import datetime as dt
import sqlite3
from datetime import date

from backend.engine import planning as P


def ev(name, date_, pri="A", **kw):
    return P.Event(id=name, name=name, date=date_, priority=pri, **kw)


def _days(a: date, b: date, every: int = 2) -> list[date]:
    return [a + dt.timedelta(days=i) for i in range(0, (b - a).days + 1, every)]


def _maint(ph):
    return [p for p in ph if p.maintain]


# ---- where training started -----------------------------------------------------------------------

def test_restart_is_the_first_run_after_the_last_layoff():
    assert P.LAYOFF_WEEKS == 4
    old = _days(date(2020, 1, 1), date(2024, 12, 31))
    back = _days(date(2026, 6, 1), date(2026, 10, 20))
    assert P.training_restart(old + back, date(2026, 10, 23)) == (date(2026, 6, 1), True)
    # two layoffs: the later one counts
    mid = _days(date(2025, 3, 1), date(2025, 9, 1))
    assert P.training_restart(old + mid + back, date(2026, 10, 23)) == (date(2026, 6, 1), True)


def test_no_layoff_counts_from_the_first_day():
    runs = _days(date(2025, 1, 6), date(2026, 10, 20), every=7)        # one run a week: never a layoff
    assert P.training_restart(runs, date(2026, 10, 23)) == (date(2025, 1, 6), False)
    assert P.training_restart([], date(2026, 10, 23)) is None


def test_exactly_four_weeks_off_is_a_layoff_three_weeks_is_not():
    a = date(2026, 1, 1)
    # 28 days without a run between the two runs → a layoff
    assert P.training_restart([a, a + dt.timedelta(days=29)], date(2026, 2, 5)) == (a + dt.timedelta(days=29), True)
    # 27 days without a run → not one
    assert P.training_restart([a, a + dt.timedelta(days=28)], date(2026, 2, 5)) == (a, False)


def test_a_layoff_still_going_starts_today():
    runs = _days(date(2025, 1, 1), date(2026, 8, 1))
    assert P.training_restart(runs, date(2026, 10, 23)) == (date(2026, 10, 23), True)
    assert P.training_restart(runs, date(2026, 8, 20)) == (date(2025, 1, 1), False)


# ---- the phases -----------------------------------------------------------------------------------

def test_years_of_old_data_and_a_recent_restart_is_no_maintenance_yet(monkeypatch):
    # the old way (first day of data) would put this user in a 維持期 from day one
    plan = P.Plan()
    old = _days(date(2019, 1, 1), date(2024, 12, 31))
    back = _days(date(2026, 6, 1), date(2026, 10, 20))
    monkeypatch.setattr(P, "SINCE_OF", lambda: P.training_restart(old + back, date(2026, 10, 23)))
    p = P.phase_on(plan, date(2026, 10, 23), transition_weeks=3)
    assert p is not None and p.kind == "base" and not p.maintain
    ph = P.phases(plan, date(2026, 10, 1), date(2027, 6, 1), transition_weeks=3)
    m = _maint(ph)
    assert m and m[0].maintain_from == (date(2026, 6, 1) + dt.timedelta(weeks=24)).isoformat()
    # the old data start alone: in a 維持期 already
    monkeypatch.setattr(P, "SINCE_OF", lambda: date(2019, 1, 1))
    assert P.phase_on(plan, date(2026, 10, 23), transition_weeks=3).maintain


def test_the_note_says_it_counts_from_the_restart():
    ph = P.auto_phases([], date(2026, 1, 1), date(2027, 6, 1), since=date(2026, 6, 1), since_restart=True)
    m = _maint(ph)[0]
    assert m.maintain_from == "2026-11-16"
    assert "連續 4 週以上沒跑" in m.note and "2026-06-01" in m.note and "資料" not in m.note
    # without a layoff: the first day of data, as before
    ph = P.auto_phases([], date(2026, 1, 1), date(2027, 6, 1), since=date(2026, 6, 1))
    assert "訓練資料的第一天" in _maint(ph)[0].note


def test_a_layoff_after_the_a_race_moves_the_start():
    race = ev("race", "2026-01-11", distance_km=42.2, kind="road")
    plain = P.auto_phases([race], date(2025, 6, 1), date(2027, 6, 1))
    m0 = _maint(plain)[0]
    restart = P._d(m0.maintain_from) - dt.timedelta(weeks=20)        # off for a while after its base started
    ph = P.auto_phases([race], date(2025, 6, 1), date(2027, 6, 1), since=restart, since_restart=True)
    m = _maint(ph)[0]
    assert m.maintain_from == (restart + dt.timedelta(weeks=24)).isoformat()
    assert "重新開始跑" in m.note
    # a restart before the base after the race: the race's base still counts
    early = P.auto_phases([race], date(2025, 6, 1), date(2027, 6, 1), since=date(2025, 3, 1), since_restart=True)
    assert _maint(early)[0].maintain_from == m0.maintain_from and "A 賽事恢復後" in _maint(early)[0].note


def test_the_db_reader_uses_run_days_only(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, workout_date TEXT, sport TEXT, duplicate_of INTEGER)")
    rows = [(d.isoformat(), "running", None) for d in _days(date(2020, 1, 1), date(2024, 1, 1))]
    # rides during the layoff don't bridge it; a duplicate of another source doesn't count
    rows += [(d.isoformat(), "cycling", None) for d in _days(date(2024, 1, 2), date(2026, 5, 31), every=5)]
    rows += [("2025-06-01", "running", 1)]
    rows += [(d.isoformat(), "trail running", None) for d in _days(date(2026, 6, 1), date(2026, 10, 20))]
    con.executemany("INSERT INTO workout_files (workout_date, sport, duplicate_of) VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    from backend.engine.wko5expr import datasource as DS
    monkeypatch.setattr(DS, "_db_path", lambda: db)
    P._SINCE_CACHE.clear()
    st = P.training_start_db(date(2026, 10, 23))
    assert st is not None and st[0] == date(2026, 6, 1) and st[1] is True


def test_the_restart_note_is_in_english():
    from backend.i18n import use_locale
    with use_locale("en"):
        ph = P.auto_phases([], date(2026, 1, 1), date(2027, 6, 1), since=date(2026, 6, 1), since_restart=True)
    assert "when running started again after 4 or more weeks without a run" in _maint(ph)[0].note
