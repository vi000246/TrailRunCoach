"""Source chain and the pool's source allow-list (SP-585, backend/research/sources.py): every
activity knows its upstream platform, older rows are filled in once, the allow-list is
replaceable, and a run that came from two sources reaches the pool once. Synthetic DBs only."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from sqlalchemy import create_engine

from backend.db.models import Base
from backend.research import sources as S


# ---------------------------------------------------------------------------- the chain

@pytest.mark.parametrize("source, upstream, chain", [
    ("upload", None, "manual"),
    ("coros", None, "coros-unofficial"),
    ("garmin", None, "garmin-unofficial"),
    ("trainingpeaks", None, "trainingpeaks"),
    ("local", None, "local"),
    (None, None, "local"),
    ("intervals", "GARMIN_CONNECT", "intervals:GARMIN_CONNECT"),
    ("intervals", "garmin_connect", "intervals:GARMIN_CONNECT"),
    ("intervals", "STRAVA", "intervals:STRAVA"),
    ("intervals", "HUAWEI", "intervals:HUAWEI"),
    ("intervals", None, "intervals:UNKNOWN"),
    ("intervals", " ", "intervals:UNKNOWN"),
    ("something-new", None, "something-new"),
])
def test_chain_of_every_source(source, upstream, chain):
    assert S.chain_of(source, upstream) == chain


def _run(coro):
    return asyncio.run(coro)


def _init(path, monkeypatch, times=1):
    import backend.db.database as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        for _ in range(times):
            await db_mod.init_db()
        await db_mod.dispose(path)
    _run(go())


def test_new_rows_record_their_chain_and_intervals_upstream_follows(tmp_path, monkeypatch):
    """The import writes source, intervals.icu's upstream comes a moment later (intervals_client
    sets it after _import_one_file): the chain follows; a chain an importer set itself stays."""
    import backend.db.database as db_mod
    from backend.db.models import Athlete, WorkoutFile
    p = tmp_path / "t.db"
    _init(p, monkeypatch)

    async def go():
        async with db_mod.AsyncSessionLocal() as db:
            db.add(Athlete(id=1, name="a", data_dir="d"))
            await db.flush()
            rows = {
                "up": WorkoutFile(athlete_id=1, file_path="a.fit", file_format="fit", source="upload"),
                "iv": WorkoutFile(athlete_id=1, file_path="b.fit", file_format="fit", source="intervals"),
                "mcp": WorkoutFile(athlete_id=1, file_path="c.fit", file_format="fit", source="coros",
                                   source_chain="coros-mcp"),
            }
            db.add_all(rows.values())
            await db.flush()
            assert rows["iv"].source_chain == "intervals:UNKNOWN"
            rows["iv"].upstream_source = "GARMIN_CONNECT"
            rows["mcp"].upstream_source = "X"               # not intervals: an importer's chain stays
            await db.commit()
        await db_mod.dispose(p)
    _run(go())
    con = sqlite3.connect(p)
    got = dict(con.execute("SELECT file_path, source_chain FROM workout_files").fetchall())
    con.close()
    assert got == {"a.fit": "manual", "b.fit": "intervals:GARMIN_CONNECT", "c.fit": "coros-mcp"}


def test_older_rows_are_filled_in_once_and_a_rerun_changes_nothing(tmp_path, monkeypatch):
    p = tmp_path / "old.db"
    eng = create_engine(f"sqlite:///{p}")
    Base.metadata.create_all(eng)
    eng.dispose()
    con = sqlite3.connect(p)
    con.execute("ALTER TABLE workout_files DROP COLUMN source_chain")       # a DB from before SP-585
    con.execute("INSERT INTO athletes (id, name, data_dir, created_at) VALUES (1, 'a', 'd', '2026-01-01')")
    cases = [("coros", None), ("upload", None), ("intervals", "GARMIN_CONNECT"), ("intervals", None),
             ("trainingpeaks", None), ("garmin", None), ("local", None), ("intervals", "strava")]
    for i, (src, up) in enumerate(cases):
        con.execute("INSERT INTO workout_files (athlete_id, file_path, file_format, source, upstream_source, "
                    "imported_at) VALUES (1, ?, 'fit', ?, ?, '2026-01-01')", (f"f{i}.fit", src, up))
    con.commit()
    con.close()
    _init(p, monkeypatch, times=1)
    con = sqlite3.connect(p)
    got = con.execute("SELECT source, upstream_source, source_chain FROM workout_files ORDER BY id").fetchall()
    assert [g[2] for g in got] == [S.chain_of(s, u) for s, u in cases]
    # a chain changed afterwards (an importer's own) is never overwritten by the next start-up
    con.execute("UPDATE workout_files SET source_chain = 'coros-mcp' WHERE id = 1")
    con.commit()
    assert con.execute(S.backfill_sql()).rowcount == 0
    con.close()
    _init(p, monkeypatch, times=2)
    con = sqlite3.connect(p)
    assert con.execute("SELECT source_chain FROM workout_files WHERE id = 1").fetchone()[0] == "coros-mcp"
    con.close()


def test_backfill_on_a_partial_schema_without_source_columns():
    """An old / partial workout_files without source or upstream_source: read as NULL, never fails."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, source_chain TEXT)")
    con.execute("INSERT INTO workout_files (id) VALUES (1)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(workout_files)")}
    assert con.execute(S.backfill_sql(cols)).rowcount == 1
    assert con.execute("SELECT source_chain FROM workout_files").fetchone()[0] == "local"
    assert con.execute(S.backfill_sql(cols)).rowcount == 0
    con.execute("ALTER TABLE workout_files ADD COLUMN source TEXT")
    con.execute("INSERT INTO workout_files (id, source) VALUES (2, 'intervals')")
    cols = {r[1] for r in con.execute("PRAGMA table_info(workout_files)")}
    con.execute(S.backfill_sql(cols))
    assert con.execute("SELECT source_chain FROM workout_files WHERE id = 2").fetchone()[0] == "intervals:UNKNOWN"


# ---------------------------------------------------------------------------- the allow-list

def test_default_rules_refuse_strava_trainingpeaks_and_unknown_sources():
    pol = S.SourcePolicy()
    for chain in ("strava", "strava-export", "intervals:STRAVA", "trainingpeaks", "intervals:UNKNOWN",
                  "something-new", "", None):
        for layer in ("A", "B"):
            assert not pol.allows(chain, layer, owner=True), (chain, layer)
    assert pol.allows("manual", "B") and pol.allows("intervals:COROS", "B")
    assert pol.allows("intervals:HUAWEI", "B")                    # an upstream not listed: intervals' rule
    assert pol.allows("coros-mcp", "A") and not pol.allows("coros-mcp", "B")
    # the unofficial syncs: only the owner, who is the data subject
    for chain in ("coros-unofficial", "garmin-unofficial", "local"):
        assert pol.allows(chain, "B", owner=True) and not pol.allows(chain, "B", owner=False)
    assert S.RULES_PENDING                                         # §11 Q10 not answered yet


def test_garmin_needs_attribution():
    pol = S.SourcePolicy()
    assert pol.attribution("intervals:GARMIN_CONNECT") == S.GARMIN_ATTRIBUTION
    assert pol.attribution("garmin-unofficial") == S.GARMIN_ATTRIBUTION
    assert pol.attribution("manual", device_name="Garmin Forerunner 965") == S.GARMIN_ATTRIBUTION
    assert pol.attribution("intervals:COROS", device_name="COROS VERTIX 2") is None
    assert pol.attribution("manual") is None


def test_the_rules_are_replaceable(tmp_path):
    f = tmp_path / "rules.json"
    f.write_text(json.dumps({"manual": {"a": True, "b": False}, "intervals:STRAVA": {"a": True, "b": True}}))
    pol = S.load_rules(f)
    assert pol.allows("intervals:STRAVA", "B")                     # the operator's table, whole
    assert not pol.allows("manual", "B") and pol.allows("manual", "A")
    assert not pol.allows("intervals:COROS", "A")                  # not in the file: refused
    back = json.loads(pol.to_json())
    assert back["manual"] == {"a": True, "b": False, "owner_only": False, "attribution": None}
    f.write_text(json.dumps({"manual": {"a": "yes", "b": True}}))
    with pytest.raises(ValueError):
        S.load_rules(f)


# ---------------------------------------------------------------------------- one row per activity

def _r(i, chain, dup=None, fmt="fit", up=None):
    return {"id": i, "duplicate_of": dup, "source_chain": chain, "file_format": fmt, "upstream_source": up}


def test_a_run_from_two_sources_reaches_the_pool_once():
    pol = S.SourcePolicy()
    rows = [_r(1, "intervals:COROS"), _r(2, "manual", dup=1),            # one run, two allowed copies
            _r(3, "manual")]                                             # another run
    kept, refused = S.pick_one_per_activity(rows, pol, "B")
    assert [r["id"] for r in kept] == [1, 3]                             # the canonical copy
    assert not refused


def test_a_refused_canonical_copy_never_hides_an_allowed_one():
    pol = S.SourcePolicy()
    rows = [_r(10, "trainingpeaks"), _r(11, "intervals:COROS", dup=10), _r(12, "manual", dup=10, fmt="gpx")]
    kept, refused = S.pick_one_per_activity(rows, pol, "B")
    assert [r["id"] for r in kept] == [11]                               # the original FIT before a GPX
    assert not refused                                                   # the run went in: nothing lost
    # a Huawei copy (not the original file) ranks after an original
    rows = [_r(20, "trainingpeaks"), _r(21, "intervals:HUAWEI", dup=20, up="HUAWEI"),
            _r(22, "intervals:COROS", dup=20)]
    kept, _ = S.pick_one_per_activity(rows, pol, "B")
    assert [r["id"] for r in kept] == [22]


def test_a_run_only_refused_sources_have_is_left_out_and_counted():
    pol = S.SourcePolicy()
    rows = [_r(1, "trainingpeaks"), _r(2, "intervals:STRAVA", dup=1), _r(3, "manual"),
            _r(4, "manual", fmt="corrupt")]
    kept, refused = S.pick_one_per_activity(rows, pol, "B")
    assert [r["id"] for r in kept] == [3]
    assert refused == {"trainingpeaks": 1, "intervals:STRAVA": 1}
    # the owner's own COROS sync: allowed for him, refused for anyone else
    rows = [_r(5, "coros-unofficial")]
    assert [r["id"] for r in S.pick_one_per_activity(rows, pol, "B", owner=True)[0]] == [5]
    assert S.pick_one_per_activity(rows, pol, "B", owner=False)[1] == {"coros-unofficial": 1}


def test_a_row_without_a_stored_chain_is_derived():
    pol = S.SourcePolicy()
    rows = [{"id": 1, "duplicate_of": None, "source": "intervals", "upstream_source": "STRAVA",
             "file_format": "fit"},
            {"id": 2, "duplicate_of": None, "source": "upload", "file_format": "fit"}]
    kept, refused = S.pick_one_per_activity(rows, pol, "B")
    assert [r["id"] for r in kept] == [2] and refused == {"intervals:STRAVA": 1}


def test_no_api_route_imports_the_research_package():
    """Server-internal only (SP-585/586/587): no router reaches the pool code."""
    import ast
    from pathlib import Path
    api = Path(__file__).resolve().parents[1] / "api"
    for p in api.rglob("*.py"):
        tree = ast.parse(p.read_text("utf-8"))
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.ImportFrom) and n.module:
                mods = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
            elif isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            assert not any(m == "backend.research" or m.startswith("backend.research.") for m in mods), p.name
