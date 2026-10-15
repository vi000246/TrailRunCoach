"""The research pool's B layer (SP-586, backend/research/pool.py): only people with the B consent,
random donor ids, no GPS track / FIT / account in the pool, grid cells of >= 5 people, untrimmed
1-km splits with their export year, coarse summaries, withdrawal and retention. Synthetic tenants in
tmp_path with hand-made tracks (the prototype's way, test_deid_prototype_sp326.py), one test through
the demo athlete's generated FITs — never a real tenant."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine

from backend.db.models import Base
from backend.research import deid as D
from backend.research import pool as P
from backend.research import sources as S

TODAY = dt.date(2026, 10, 15)
HOME = (24.1000, 120.7000)
M_LAT = 1 / 111_320.0


@pytest.fixture
def server(tmp_path, monkeypatch):
    from backend import accounts
    from backend.accounts import store, tenants as TN
    root = tmp_path / "server"
    monkeypatch.setenv(accounts.ENV_SERVER_ROOT, str(root))
    monkeypatch.setattr(store, "_READY", set())
    monkeypatch.setattr(TN, "_READY", set())
    monkeypatch.setattr(TN, "_READY_LOCK", None)
    monkeypatch.setattr(P, "_last_tick", None)
    monkeypatch.delenv("TRC_RESEARCH_POOL", raising=False)
    return root


def _track(points, start, *, step_m=3.0, speed=3.0, chain="manual", is_race=False, climb=0.02):
    pts = np.asarray(points, dtype=float)
    seg = D.haversine_m(pts[:-1, 0], pts[:-1, 1], pts[1:, 0], pts[1:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    d = np.arange(0.0, cum[-1], step_m)
    n = len(d)
    return D.Track(t=d / speed, lat=np.interp(d, cum, pts[:, 0]), lon=np.interp(d, cum, pts[:, 1]), dist=d,
                   speed=np.full(n, speed), ele=100 + climb * d, hr=np.full(n, 140.0), power=np.full(n, 200.0),
                   cadence=np.full(n, 170.0), temp=np.full(n, 22.0), start_utc=start, chain=chain, is_race=is_race)


def _north(origin, km):
    return [origin, (origin[0] + km * 1000 * M_LAT, origin[1])]


def _utc(d: dt.date, h=22):
    return dt.datetime(d.year, d.month, d.day, h, 0, tzinfo=dt.timezone.utc)


TRACKS: dict = {}


def _loader(td, row, chain):
    tr = TRACKS.get((td.user_id, row["id"]))
    if tr is not None:
        tr.chain = chain
        tr.is_race = dt.date.fromisoformat(row["workout_date"]) in td.race_days
    return tr


def _tenant(folder: Path, acts: list, *, sex="female", birth_year=1990, lthr=170.0, injuries=()):
    """acts: [(id, date, source, upstream, duplicate_of)]; the rows' tracks go into TRACKS."""
    folder.mkdir(parents=True, exist_ok=True)
    plan = {"profile": {"sex": sex, "birth_year": birth_year}, "thresholds": [{"date": "2020-01-01", "lthr": lthr}],
            "weights": [{"date": "2020-01-01", "kg": 55.0}]}
    (folder / "plan.json").write_text(json.dumps(plan))
    db = folder / "wko5coach.db"
    eng = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO athletes (id, name, data_dir, created_at) VALUES (1, 'secret-name', 'd', '2026-01-01')")
    for i, day, src, up, dup in acts:
        con.execute("INSERT INTO workout_files (id, athlete_id, file_path, file_format, workout_date, start_time_utc, "
                    "duplicate_of, sport, source, upstream_source, source_chain, coros_activity_id, imported_at) "
                    "VALUES (?, 1, ?, 'fit', ?, ?, ?, 'running', ?, ?, ?, ?, '2026-01-01')",
                    (i, f"fit/coros/2026/SECRETFILE{i}_{day:%Y%m%d}.fit", day.isoformat(),
                     _utc(day).replace(tzinfo=None).isoformat(" "), dup, src, up, S.chain_of(src, up), f"plat{i}"))
    for a, b in injuries:
        con.execute("INSERT INTO injury_events (athlete_id, area, kind, severity, onset_date, status, resolved_date, "
                    "pause_quality, note, category, created_at, updated_at) VALUES (1, 'knee', 'overuse', 'severe', ?, "
                    "'resolved', ?, 0, 'secret note', 'injury', '2026-01-01', '2026-01-01')",
                    (a.isoformat(), b.isoformat()))
    con.commit()
    con.close()


def _user(email, sub):
    from backend.accounts import store
    return store.create_user(email=email, google_sub=sub)


def _consent(uid, *, a=True, b=None):
    from backend.accounts import store
    store.record_consent(uid, "v1", {"terms": "x"}, analysis=a)
    if b is not None:
        _research_b(uid, b)


def _research_b(uid, granted: bool):
    """The B tick box's row (SP-58 writes it; not built yet, so the tests insert it)."""
    from backend.accounts import store
    with store.connect() as con:
        con.execute("INSERT INTO consents(user_id, terms_version, documents, analysis, accepted_at, action) "
                    "VALUES (?, 'v1', '{}', ?, '2026-10-01T00:00:00', 'research_b')", (uid, int(granted)))


def _export(path, **kw):
    return P.export(path=path, today=TODAY, rng=np.random.default_rng(7), loader=_loader, force=True, **kw)


def _one_runner(server, email="b@x.tw", sub="g-b", n=3, b=True):
    u = _user(email, sub)
    _consent(u.id, b=b)
    days = [TODAY - dt.timedelta(days=30 - 3 * i) for i in range(n)]
    _tenant(u.folder(), [(i + 1, d, "upload", None, None) for i, d in enumerate(days)])
    for i, d in enumerate(days):
        TRACKS[(u.id, i + 1)] = _track(_north(HOME, 6 + i), _utc(d))
    return u


def _q(path, sql, *a):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, a).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------- who is in it

def test_the_export_is_off_unless_the_operator_turns_it_on(server, tmp_path):
    with pytest.raises(P.PoolDisabled):
        P.export(path=tmp_path / "pool.db")
    assert not (tmp_path / "pool.db").exists()
    from backend.scripts import research_pool as CLI
    assert CLI.main(["export"]) == 2
    assert CLI.main(["export", "--k", "3"]) == 2                     # below 5: refused


def test_only_people_with_the_b_consent_are_exported(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    a_only = _one_runner(server, "a@x.tw", "g-a", b=None)            # the analysis consent, no B row
    gone = _one_runner(server, "w@x.tw", "g-w", b=True)
    _research_b(gone.id, False)                                     # ticked, then unticked
    yes = _one_runner(server, "b@x.tw", "g-b", b=True)
    res = _export(pool)
    from backend.accounts import store
    assert res.donors == 1 and res.summaries == 3
    assert store.research_link(yes.id) and not store.research_link(a_only.id) and not store.research_link(gone.id)
    donors = {r[0] for r in _q(pool, "SELECT donor_id FROM summaries UNION SELECT donor_id FROM daily")}
    assert donors == {store.research_link(yes.id)["donor_id"]}


def test_nobody_is_exported_while_no_b_consent_row_exists(server, tmp_path):
    """Today's situation: SP-58 has no B tick box yet, so no research_b row exists."""
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    for i in range(3):
        _one_runner(server, f"u{i}@x.tw", f"g-{i}", b=None)
    res = _export(pool)
    assert res.donors == 0
    for t in ("summaries", "splits", "daily"):
        assert _q(pool, f"SELECT count(*) FROM {t}") == [(0,)]


def test_under_18_and_incomplete_profiles_are_left_out(server, tmp_path):
    TRACKS.clear()
    u = _user("kid@x.tw", "g-kid")
    _consent(u.id, b=True)
    _tenant(u.folder(), [(1, TODAY - dt.timedelta(days=3), "upload", None, None)], birth_year=TODAY.year - 16)
    TRACKS[(u.id, 1)] = _track(_north(HOME, 5), _utc(TODAY - dt.timedelta(days=3)))
    v = _user("nosex@x.tw", "g-ns")
    _consent(v.id, b=True)
    _tenant(v.folder(), [(1, TODAY - dt.timedelta(days=3), "upload", None, None)], sex=None)
    res = _export(tmp_path / "pool.db")
    assert res.donors == 0 and res.skipped["under_18"] == 1 and res.skipped["sex"] == 1


# ---------------------------------------------------------------------------- what is in it

ALLOWED_COLUMNS = {
    "summaries": set(P.SUMMARY_COLS), "splits": set(P.SPLIT_COLS), "daily": set(P.DAILY_COLS),
    "grid": {"cell_lat", "cell_lon", "n_people", "n_samples", "median_speed_ms"}, "meta": {"key", "value"},
}


def test_random_donor_id_and_no_account_gps_or_fit_in_the_pool(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    u = _one_runner(server, "Alice@Example.com", "g-alice-123")
    _export(pool)
    from backend.accounts import store
    link = store.research_link(u.id)
    assert re.fullmatch(r"[0-9a-f]{32}", link["donor_id"])
    # the schema: only the listed columns — no lat / lon / time / file / account column anywhere
    con = sqlite3.connect(pool)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == set(ALLOWED_COLUMNS)
    for t in tables:
        assert {r[1] for r in con.execute(f"PRAGMA table_info({t})")} == ALLOWED_COLUMNS[t], t
    dump = "\n".join(con.iterdump())
    con.close()
    for secret in ("alice", "g-alice-123", "SECRETFILE", "plat1", "secret-name", "secret note", str(u.folder()),
                   "24.1", "120.7", "2026-09", "2026-10-0", "55.0"):
        assert secret.lower() not in dump.lower(), secret
    # no blob at all (no FIT copy): every value is a number or a short text
    assert "X'" not in dump
    # the donor id is not derived from the account
    import hashlib
    for v in (str(u.id), "Alice@Example.com", "alice@example.com", "g-alice-123"):
        assert hashlib.sha256(v.encode()).hexdigest()[:32] != link["donor_id"]
    # the splits carry no donor id: they are not in any row next to it
    assert link["donor_id"] in dump
    assert all(len(r) == len(P.SPLIT_COLS) for r in _q(pool, "SELECT * FROM splits"))


def test_splits_are_untrimmed_and_carry_the_export_year(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    u = _user("loop@x.tw", "g-loop")
    _consent(u.id, b=True)
    days = [TODAY - dt.timedelta(days=d) for d in (40, 30, 20, 10)]
    _tenant(u.folder(), [(i + 1, d, "upload", None, None) for i, d in enumerate(days)])
    # a 6-km out-and-back from home, four times in 90 days: home is a frequent place, but the splits
    # are not cut (Q2-1) — the trimming would have left nothing
    for i, d in enumerate(days):
        far = (HOME[0] + 3000 * M_LAT, HOME[1])
        TRACKS[(u.id, i + 1)] = _track([HOME, far, HOME], _utc(d))
    _export(pool)
    rows = _q(pool, "SELECT export_year, sex, age_band FROM splits")
    assert len(rows) >= 4 * 5
    assert {r[0] for r in rows} == {TODAY.year}
    assert {(r[1], r[2]) for r in rows} == {("female", "30-39")}


def test_summaries_are_coarse(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    u = _user("c@x.tw", "g-c")
    _consent(u.id, b=True)
    d1, d2 = TODAY - dt.timedelta(days=20), TODAY - dt.timedelta(days=10)
    _tenant(u.folder(), [(1, d1, "upload", None, None), (2, d2, "upload", None, None)])
    TRACKS[(u.id, 1)] = _track(_north(HOME, 12.73), _utc(d1), speed=2.71)       # 12.7 km, 1:18:17
    TRACKS[(u.id, 2)] = _track(_north(HOME, 21.4), _utc(d2), speed=3.3, climb=0.06)
    from backend.accounts import store
    with sqlite3.connect(u.folder() / "wko5coach.db") as con:            # the user marked #2 a race
        con.execute("INSERT INTO activity_tags (athlete_id, start_local, activity_type, activity_type_overridden, "
                    "effort_overridden, updated_at) VALUES (1, ?, 'race', 1, 0, '2026-10-01')",
                    (f"{d2.isoformat()}T06:00",))
    _export(pool)
    rows = dict((r[0], r[1:]) for r in _q(pool, "SELECT is_race, distance_km, duration_min, ascent_m FROM summaries"))
    dist, mins, asc = rows[0]
    assert dist == 12.0 and mins % 5 == 0 and mins == 75 and asc % 50 == 0
    dist, mins, asc = rows[1]
    assert dist == 20.0 and mins % 5 == 0 and asc % 500 == 0
    assert store.research_link(u.id)


def test_a_run_from_two_sources_is_one_summary_and_refused_sources_never_enter(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    u = _user("d@x.tw", "g-d")
    _consent(u.id, b=True)
    d1, d2, d3 = (TODAY - dt.timedelta(days=x) for x in (20, 15, 10))
    _tenant(u.folder(), [(1, d1, "trainingpeaks", None, None), (2, d1, "intervals", "COROS", 1),   # one run
                         (3, d2, "intervals", "STRAVA", None),                                       # refused
                         (4, d3, "intervals", "GARMIN_CONNECT", None)])
    for i, d in ((1, d1), (2, d1), (3, d2), (4, d3)):
        TRACKS[(u.id, i)] = _track(_north(HOME, 5), _utc(d))
    res = _export(pool)
    srcs = sorted(r[0] for r in _q(pool, "SELECT source FROM summaries"))
    assert srcs == ["intervals:COROS", "intervals:GARMIN_CONNECT"]
    assert res.refused == {"intervals:STRAVA": 1}
    meta = dict(_q(pool, "SELECT key, value FROM meta"))
    assert json.loads(meta["attributions"]) == [S.GARMIN_ATTRIBUTION]
    assert json.loads(meta["rules_pending"]) is True
    # a second run adds nothing (the activity keys are in the pool already)
    res2 = _export(pool)
    assert res2.summaries == 0 and res2.splits == 0
    assert len(_q(pool, "SELECT * FROM summaries")) == 2


def test_grid_cells_need_5_distinct_people(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    far = (HOME[0] + 0.2, HOME[1])                                   # a trail 22 km away from everyone's home
    users = []
    for i in range(5):
        u = _user(f"g{i}@x.tw", f"g-g{i}")
        _consent(u.id, b=None)                                       # the A layer is enough for the grid
        d = TODAY - dt.timedelta(days=5 + i)
        _tenant(u.folder(), [(1, d, "upload", None, None)])
        start = (far[0] - 4000 * M_LAT, far[1] + 0.001 * i)
        TRACKS[(u.id, 1)] = _track([start, (far[0] + 4000 * M_LAT, far[1])], _utc(d))
        users.append(u)
    res = _export(pool)
    cells = _q(pool, "SELECT n_people FROM grid")
    assert cells and all(n >= 5 for (n,) in cells)
    assert res.grid_held > 0                                         # the starts: one person each
    # four people: no cell at all
    from backend.accounts import store
    store.record_analysis(users[0].id, False)                        # one opts out of the A layer
    res = _export(pool)
    assert _q(pool, "SELECT count(*) FROM grid") == [(0,)] and res.grid_cells == 0


# ---------------------------------------------------------------------------- daily load

def test_daily_rows_every_day_and_injury_breaks_of_7_days(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    u = _user("e@x.tw", "g-e")
    _consent(u.id, b=True)
    d1, d2 = TODAY - dt.timedelta(days=40), TODAY - dt.timedelta(days=10)
    _tenant(u.folder(), [(1, d1, "upload", None, None), (2, d2, "upload", None, None)],
            injuries=[(d1 + dt.timedelta(days=5), d1 + dt.timedelta(days=15))])
    TRACKS[(u.id, 1)] = _track(_north(HOME, 5), _utc(d1))
    TRACKS[(u.id, 2)] = _track(_north(HOME, 5), _utc(d2))
    _export(pool)
    rows = _q(pool, "SELECT rel_day, tss, injury_off FROM daily ORDER BY rel_day")
    assert len(rows) == 31 and rows[-1][0] - rows[0][0] == 30
    assert sum(r[2] for r in rows) == 10                              # day 5 .. 14 (resolved the 15th)
    assert rows[0][1] > 0 and rows[1][1] == 0


# ---------------------------------------------------------------------------- withdrawal, retention

def test_withdrawal_deletes_the_linked_rows_within_the_hour_and_keeps_splits(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    u = _one_runner(server)
    other = _one_runner(server, "o@x.tw", "g-o")
    _export(pool)
    n_splits = _q(pool, "SELECT count(*) FROM splits")[0][0]
    from backend.accounts import store
    did = store.research_link(u.id)["donor_id"]
    _research_b(u.id, False)                                          # unticked in 設定 (SP-58)
    assert P.tick(path=pool, now=1000.0)["withdrawn"] == 1            # the scheduler's hourly pass
    assert _q(pool, "SELECT count(*) FROM summaries WHERE donor_id = ?", did) == [(0,)]
    assert _q(pool, "SELECT count(*) FROM daily WHERE donor_id = ?", did) == [(0,)]
    assert store.research_link(u.id) is None and store.research_link(other.id)
    assert _q(pool, "SELECT count(*) FROM splits")[0][0] == n_splits   # no id: kept (§7.3)
    assert P.tick(path=pool, now=1001.0) is None                       # once an hour
    assert P.TICK_EVERY_S <= 24 * 3600


def test_deleting_the_account_withdraws_first(server, tmp_path, monkeypatch):
    TRACKS.clear()
    from backend import research
    from backend.accounts import deletion, store
    pool = research.pool_db_path()
    u = _one_runner(server)
    _export(pool)
    did = store.research_link(u.id)["donor_id"]
    assert _q(pool, "SELECT count(*) FROM summaries WHERE donor_id = ?", did) != [(0,)]
    asyncio.run(deletion.delete_account(store.get_user(u.id), job_wait_s=0))
    assert _q(pool, "SELECT count(*) FROM summaries WHERE donor_id = ?", did) == [(0,)]
    assert _q(pool, "SELECT count(*) FROM daily WHERE donor_id = ?", did) == [(0,)]
    assert store.research_links() == []


def test_retention_deletes_old_rows_and_whole_export_years(server, tmp_path):
    TRACKS.clear()
    pool = tmp_path / "pool.db"
    u = _one_runner(server)
    _export(pool)
    with sqlite3.connect(pool) as con:
        con.execute("UPDATE splits SET export_year = ? WHERE rowid IN (SELECT rowid FROM splits LIMIT 2)",
                    (TODAY.year - 4,))
    out = P.expire(TODAY, path=pool)
    assert out["splits"] == 2 and out["summaries"] == 0
    out = P.expire(TODAY + dt.timedelta(days=4 * 366), path=pool)     # everything is older than 3 years then
    assert out["summaries"] == 3 and _q(pool, "SELECT count(*) FROM daily") == [(0,)]
    assert _q(pool, "SELECT count(*) FROM splits") == [(0,)]
    assert u


# ---------------------------------------------------------------------------- the real reader

def test_the_tenant_reader_through_the_demo_athletes_fits(server, tmp_path):
    """The default loader: FIT objects in the tenant's fit/, decoded by the app's own decoder."""
    from backend.demo import season as SE
    from backend.demo.generate import build_activity
    from backend.files import fitstore
    TRACKS.clear()
    u = _user("demo@x.tw", "g-demo")
    _consent(u.id, b=True)
    seed, anchor = 20261002, dt.date(2026, 10, 3)
    season = SE.schedule(seed, anchor, 2)
    acts = [p for p in season.activities if p.kind != "baiyue"][:3]
    store_ = fitstore.LocalDirStore(u.folder() / "fit")
    rows = []
    for i, p in enumerate(acts, start=1):
        raw, _info = build_activity(seed, i, p, season, True, {})
        key = fitstore.object_key("coros", fitstore.sha256_hex(raw))
        store_.put(key, raw)
        rows.append((i, p.date, key))
    _tenant(u.folder(), [(i, d, "upload", None, None) for i, d, _k in rows])
    with sqlite3.connect(u.folder() / "wko5coach.db") as con:
        for i, _d, key in rows:
            con.execute("UPDATE workout_files SET file_path = ? WHERE id = ?", (key, i))
    pool = tmp_path / "pool.db"
    res = P.export(path=pool, today=TODAY, rng=np.random.default_rng(1), force=True)
    assert res.donors == 1 and res.summaries == len(rows) and res.splits > 0
    dump = "\n".join(sqlite3.connect(pool).iterdump())
    for _i, _d, key in rows:
        assert fitstore.sha_of_key(key) not in dump
