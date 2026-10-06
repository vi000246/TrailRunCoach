"""Integration 2026-10-06c: cross-branch checks.

- SP-242 (登山杖 有杖 / 沒杖 / 未標, a free-form tag) and SP-231 (COROS post-run
  self-rating → RPE → effort) stay independent: a pole mark is never an effort,
  a rating never sets or clears the pole mark, and GET /activities returns both.
- SP-240 (≥ 8 h races in the trail HR back-test summary) on SP-239's
  linear-then-floor trail HR model.
- A DB from before SP-231 (no coros_feel / rpe_source) upgrades on start-up.

tmp / in-memory DBs and synthetic numbers only.
"""
import asyncio
import datetime as dt
import math
import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.engine import activity_tags as AT
from backend.engine import coros_rpe as CR

WITH, WITHOUT = AT.POLES["with"], AT.POLES["without"]


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- SP-242 × SP-231 ----------------------------------------------------------------

def _coros_rec(start_local="2025-04-12T10:32", file="x.wko4", feel=4):
    return {"start_local": start_local, "file": file, "rpe": CR.TO_RPE[feel], "feel": None,
            "coros_feel": feel, "source": CR.SOURCE}


def test_effort_from_a_coros_rating_carries_no_pole_mark():
    rec = _coros_rec(feel=4)
    e = AT.effort_from_rpe(rec["rpe"], 0.02, {"effort": "easy"}, rec=rec)
    assert e["basis"] == "rpe" and "COROS" in e["reason"]
    assert not {"poles", "tags"} & set(e)
    # the pole tags are not effort values, and an effort is not a pole value
    assert not set(AT.POLES) & set(AT.EFFORTS) and not set(AT.POLES.values()) & set(AT.EFFORTS.values())
    assert AT.validate(effort="with") == "INVALID_EFFORT"
    assert AT.validate(poles="max") == "INVALID_POLES"


def test_a_pole_mark_is_not_an_effort_and_a_rating_keeps_the_mark(tmp_path):
    db = tmp_path / "t.db"
    k = "2025-04-12T10:32"
    AT.upsert(db, start_local=k, poles="with")
    r = AT.load(db)[0]
    assert r["effort"] is None and not r["effort_overridden"]          # the mark sets no effort
    auto = {"activity_type": "trail", "effort": "hard_with_rests", "effort_reason": "x"}
    m = AT.merge(auto, r)
    assert m["poles"] == "with" and m["effort"] == "hard_with_rests" and not m["effort_overridden"]
    AT.upsert(db, start_local=k, effort="max")                         # an effort keeps the mark
    r = AT.load(db)[0]
    assert AT.poles_of(r["tags"]) == "with" and r["effort"] == "max"
    AT.upsert(db, start_local=k, effort=None)                          # back to auto: mark still there
    r = AT.load(db)[0]
    assert AT.poles_of(r["tags"]) == "with" and r["effort"] is None


def test_capacity_sample_from_a_coros_rating_is_the_same_with_any_pole_mark(monkeypatch):
    """The COROS rating decides the auto effort of a capacity sample; 有杖 / 沒杖 / 未標
    changes nothing but the tag list."""
    from backend.engine.racepower import athlete as A
    from backend.tests.test_activity_tags import _fake_capacity_ds, _hist
    st = {"moving_s": 13900.0, "hr_avg": 152.0, "hist": _hist(152, 13900.0), "hist_lo": 40, "hr_s": 13900.0}
    base = {"start_local": "2025-04-12T10:32", "file": "2025/x.wko4"}

    def sample(row, feel):
        ds, w = _fake_capacity_ds(monkeypatch, st, {"elapsed_s": 14500.0, "rest_share": 0.02})
        rec = [_coros_rec(file="x.wko4", feel=feel)]
        c = A.capacity_samples(ds, [w], tags=[row], recorded=rec)[0]
        return c

    for feel in (2, 4, 5):
        plain = sample(base, feel)
        assert plain["effort"]["basis"] == "rpe" and "COROS" in plain["effort"]["reason"]
        for tag in (WITH, WITHOUT):
            c = sample({**base, "tags_json": f'["{tag}"]'}, feel)
            assert c["effort"] == plain["effort"] and c["ok"] == plain["ok"], (feel, tag)
            assert c["tags"]["poles"] == ("with" if tag == WITH else "without")


@pytest.fixture
def no_plan(monkeypatch):
    from backend.engine import planning
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))


def test_activities_list_returns_poles_and_the_self_rating(tmp_path, no_plan, monkeypatch):
    from backend.api import wko5views as V
    from backend.tests.test_activity_edit import _fit_ds
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    starts = {w.entry.file: w.entry.start for w in ds.workouts}
    starts.update({x["file"]: dt.datetime.fromisoformat(x["start"]) for x in getattr(ds, "excluded", [])})
    run_key = AT.key_of(starts["2025/0.fit"])
    AT.upsert(db, start_local=run_key, file="2025/0.fit", poles="with")
    # the recorded ratings live in workout_files of the same app DB (load_recorded)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE workout_files (id INTEGER PRIMARY KEY, file_path TEXT, start_time_utc DATETIME, "
                "rpe REAL, feel INTEGER, coros_feel INTEGER, rpe_source TEXT)")
    con.execute("INSERT INTO workout_files VALUES (1, '/fit/coros/2025/0.fit', :st, 7.0, NULL, 4, 'coros')",
                {"st": starts["2025/0.fit"].astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat(" ")
                 if starts["2025/0.fit"].tzinfo else starts["2025/0.fit"].isoformat(" ")})
    con.commit()
    con.close()
    AT._rec_memo.clear()
    try:
        acts = {a["file"]: a for a in V.activities_list()["activities"]}
    finally:
        AT._rec_memo.clear()
    run, car = acts["2025/0.fit"], acts["2025/1.fit"]
    assert run["poles"] == "with" and run["tags"] == [WITH]
    assert run["rpe"] == 7.0 and run["self_rating"]["source"] == "coros"
    assert run["self_rating"]["text"] == "自評：Hard（COROS）"
    assert run["user_effort"] is None                                   # neither one is a user effort
    assert car["poles"] is None and car["self_rating"] is None and car["rpe"] is None


# ---- SP-240 × SP-239 ----------------------------------------------------------------

def test_long_race_backtest_summary_on_the_floored_trail_hr_model():
    """Synthetic athlete whose runs follow SP-239's linear-then-floor speed exactly:
    evaluate_trail_hr + summarise_trail_hr (SP-240) on races of 3–24 h."""
    from backend.engine.racepower import backtest as BT
    from backend.engine.racepower import trailhr as TH
    d = 0.05
    v0 = lambda x: 2.0 + 6.0 * x                                        # noqa: E731 effort-km/h

    def pt(T_h, x):
        e = v0(x) * TH.dist_nodecay(T_h, d)                             # E = v₀ · T · D̄(T)
        return {"x": x, "x_raw": x, "v": e / T_h, "T_h": T_h, "eff_km": e}

    train = [pt(T, x) for T, x in ((1.0, 0.80), (1.5, 0.85), (2.0, 0.78), (3.0, 0.82), (4.0, 0.76),
                                   (5.0, 0.80), (6.0, 0.74), (2.5, 0.88))]
    m = TH.fit(train, d)
    assert m["kind"] == "ols" and m["b"] == pytest.approx(6.0, rel=1e-6)
    m["xstar"] = TH.xstar_prior()
    rows = []
    for i, T in enumerate((3.0, 6.0, 8.0, 11.0, 15.0, 24.0)):
        p = pt(T, TH.xstar_at(m["xstar"], T))
        r = BT.evaluate_trail_hr({"trail_pt": p}, {"trail_hr": m})
        rows.append({"category": "trail", "date": f"2026-0{1 + i % 9}-01", "label": f"r{T}",
                     "activity_type": "race", "effort_tag": "max", **r})
    for r in rows:                                                       # given HR = the model: exact
        assert r["err_th_given"] == pytest.approx(0.0, abs=1e-6)
        assert r["err_th_race"] is not None and math.isfinite(r["err_th_race"])
        # no-decay is faster than decayed, and decay never doubles the time any more (SP-239)
        assert r["th"]["t_nodur"] < r["th"]["t_given"] < 1.25 * r["th"]["t_nodur"]
    s = BT.summarise_trail_hr(rows)
    lr = s["long_races"]
    assert lr["n"] == 4 and lr["min_h"] == BT.LONG_RACE_H
    assert lr["race_level"]["median_abs"] == pytest.approx(0.0, abs=1e-3)   # the decay shape fits
    assert lr["race_level_no_durability"]["bias"] < -0.05                    # no decay: too fast
    assert [r["long"] for r in s["race_rows"]] == [False, False, True, True, True, True]


# ---- pre-SP-231 DB --------------------------------------------------------------

def test_a_pre_sp231_db_gets_the_rating_columns_on_start_up(tmp_path, monkeypatch):
    import backend.db.database as db_mod
    from backend.db.models import Base

    path = tmp_path / "old.db"

    async def make_old():
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # drop the SP-231 columns: the schema as it was before the merge
            await conn.execute(text("ALTER TABLE workout_files DROP COLUMN coros_feel"))
            await conn.execute(text("ALTER TABLE workout_files DROP COLUMN rpe_source"))
            await conn.execute(text("INSERT INTO athletes (id, name, data_dir, created_at) "
                                    "VALUES (1, 'me', 'x', '2026-01-01')"))
            await conn.execute(text("INSERT INTO workout_files (athlete_id, file_path, file_format, source, rpe, "
                                    "feel, start_time_utc, imported_at) VALUES (1, '/a/G1.fit', 'fit', 'local', 8.0, 75, "
                                    "'2026-09-29 22:00:00', '2026-09-30 00:00:00')"))
        await eng.dispose()

    _run(make_old())
    cols0 = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(workout_files)")}
    assert not {"coros_feel", "rpe_source"} & cols0
    # before the upgrade the reader still works (NULL, NULL for the missing columns)
    monkeypatch.setattr(AT, "_db_path", lambda p=None: path)
    AT._rec_memo.clear()
    assert [r["source"] for r in AT.load_recorded()] == ["watch"]

    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        await db_mod.init_db()
        await db_mod.init_db()                                           # idempotent, as every start-up
        await db_mod.dispose(path)

    _run(go())
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(workout_files)")}
    assert {"coros_feel", "rpe_source"} <= cols
    assert con.execute("SELECT rpe, feel, coros_feel, rpe_source FROM workout_files").fetchall() == [(8.0, 75, None, None)]
    con.execute("UPDATE workout_files SET coros_feel = 4, rpe_source = 'coros', rpe = 7.0")
    con.commit()
    con.close()
    AT._rec_memo.clear()
    rows = AT.load_recorded()
    AT._rec_memo.clear()
    assert rows[0]["source"] == "coros" and CR.self_rating(rows[0])["text"] == "自評：Hard（COROS）"
