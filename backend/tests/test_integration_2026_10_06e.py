"""Integration 2026-10-06e: cross-branch checks.

- workout_review's measure cache: SP-264 and SP-265 / SP-266 each bumped it on their own
  branch (v20 / v22); the merge has one new key above both, defined once, and the other
  cache keys those branches bumped stay unique.
- A DB from before every branch merged here (no activity_tags.pain_score, no
  injury_events.condition / walkrun_from, no experience / race-result settings) upgrades
  on start-up, twice, keeps its rows and takes the new values.
- Rule D in two tiers (SP-301) + the self-rating rule D′ (SP-231) + the pain lights
  (SP-271, applied by the generator) + a cold-start week (SP-288): one adjustment per
  session at most, nothing put back that the light took out, stable through reconcile.
- The easy run's planned TSS rate (SP-302) for a cold-start runner (SP-288) without
  history: the 推估 rate, from the LTHR prior's cap (SP-289) when there is one.
- The activity list carries the race-derived 有杖 (SP-300), 路況 (SP-250), the rain
  (SP-299) and the pain fields incl. pain_score (SP-271) together; the viewer's list
  carries the activity kind (SP-263) for the same activity.

tmp / in-memory DBs and synthetic numbers only.
"""
import asyncio
import copy
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 6)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- cache keys -------------------------------------------------------------------

def _sources():
    return [p for p in sorted(ROOT.rglob("*.py")) if "tests" not in p.relative_to(ROOT).parts]


def test_workout_review_cache_key_is_the_single_final_value():
    from backend.engine import workout_review as WR
    m = re.fullmatch(r"workout_review_v(\d+)", WR.CACHE_KEY)
    # above SP-264's v20 and SP-266's v22: no entry written by either branch alone is read
    assert m and int(m.group(1)) >= 23
    hits = {}
    for p in _sources():
        for k in re.findall(r"[\"'](workout_review_v\d+)[\"']", p.read_text(encoding="utf-8")):
            hits.setdefault(k, set()).add(p.relative_to(ROOT).as_posix())
    assert hits == {WR.CACHE_KEY: {"engine/workout_review.py"}}


def test_the_bumped_cache_keys_are_unique_and_above_main():
    from backend.engine import thresholds as TH
    from backend.engine import threshold_confidence as TC
    from backend.engine.racepower import athlete as RA

    def ver(k, name):
        m = re.fullmatch(rf"{name}_v(\d+)", k)
        assert m, k
        return int(m.group(1))
    assert ver(TH.MHR_KEY, "mhr_peak5") >= 2 and ver(TC.RUN_KEY, "thr_conf_run") >= 2
    assert ver(RA.HRMAX_PEAK_KEY, "racepower_hrmax_peak") >= 1
    # every versioned cache-key constant in the code base: no two modules share one value
    seen: dict = {}
    pat = re.compile(r"^[A-Z_]*KEY\s*=\s*[\"']([a-z0-9_]+_v\d+)[\"']", re.M)
    for p in _sources():
        for k in pat.findall(p.read_text(encoding="utf-8")):
            seen.setdefault(k, []).append(p.relative_to(ROOT).as_posix())
    assert {k: v for k, v in seen.items() if len(v) > 1} == {}


# ---- an existing DB -----------------------------------------------------------------

def test_an_existing_db_upgrades_with_every_new_column_and_setting_twice(tmp_path, monkeypatch):
    import backend.db.database as db_mod
    from backend.db.models import Base
    from backend.engine import e_pace as EP
    from backend.engine import experience as EX
    from backend.engine import injuries as INJ
    from backend.engine import race_results as RR
    from backend.settings.repository import SettingsRepository

    path = tmp_path / "old.db"
    k = "2026-09-20T07:00"
    WITH, WET = AT.POLES["with"], AT.SURFACES["wet"]

    async def make_old():
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # the schema before SP-269 / SP-271 / SP-272
            await conn.execute(text("ALTER TABLE activity_tags DROP COLUMN pain_score"))
            for col in ("condition", "walkrun_from"):
                await conn.execute(text(f"ALTER TABLE injury_events DROP COLUMN {col}"))
            await conn.execute(text("INSERT INTO athletes (id, name, data_dir, created_at) "
                                    "VALUES (1, 'me', 'x', '2026-01-01')"))
            await conn.execute(text(
                "INSERT INTO injury_events (id, athlete_id, area, kind, severity, onset_date, status, pause_quality, "
                "category, created_at, updated_at) VALUES (5, 1, 'knee', 'overuse', 'mild', '2026-09-18', 'open', 0, "
                "'injury', '2026-09-18', '2026-09-18')"))
            await conn.execute(text(
                "INSERT INTO activity_tags (athlete_id, start_local, file, activity_type_overridden, effort_overridden, "
                "tags_json, pain, pain_area, injury_id, updated_at) VALUES (1, :k, '2026/0.fit', 0, 0, :t, 2, 'knee', 5, "
                "'2026-09-20 00:00:00')"), {"k": k, "t": json.dumps(["冬訓", WITH, WET], ensure_ascii=False)})
            await conn.execute(text("INSERT INTO user_settings (user_id, key, value_json, updated_at) "
                                    "VALUES (1, 'athlete.setup.done', 'true', '2026-09-01')"))
            # SP-276's single E-pace race, before the shared list (moved into it once, owner 2026-10-06)
            await conn.execute(text("INSERT INTO user_settings (user_id, key, value_json, updated_at) "
                                    "VALUES (1, 'athlete.race_result', :v, '2026-09-01')"),
                               {"v": json.dumps({"distance_m": 10000, "time_s": 2700, "date": "2026-05-01",
                                                 "source": "manual"})})
        await eng.dispose()

    _run(make_old())
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    monkeypatch.setattr(db_mod, "engine", None)

    async def go():
        await db_mod.init_db()
        await db_mod.init_db()                                           # idempotent, as every start-up
        async with db_mod.AsyncSessionLocal() as db:
            repo = SettingsRepository(db, 1)
            before = (await repo.get(EX.KEY), await repo.get(RR.KEY), await repo.get("athlete.setup.done"))
            await repo.set(EX.KEY, EX.answer({"runs_per_week": 3}))
            await repo.set(RR.KEY, RR.with_survey(await repo.get(RR.KEY),
                                                  RR.make(21.0975, 6000, "2026-04-01", source="survey")))
            await db.commit()
            after = await repo.get(EX.KEY), await repo.get(RR.KEY)
        await db_mod.dispose(path)
        return before, after

    before, after = _run(go())
    # the old single race moved into the shared list exactly once (two start-ups), the old key gone
    assert before == (None, [RR.make(10, 2700, "2026-05-01", source="manual")], True)
    assert EP.LEGACY_KEY != RR.KEY
    assert after[0]["runs_per_week"] == 3 and [r["source"] for r in after[1]] == ["manual", "survey"]
    assert EP.pick(after[1])["time_s"] == 2700
    con = sqlite3.connect(path)
    assert not con.execute("SELECT 1 FROM user_settings WHERE key = 'athlete.race_result'").fetchall()
    tg = {r[1] for r in con.execute("PRAGMA table_info(activity_tags)")}
    ie = {r[1] for r in con.execute("PRAGMA table_info(injury_events)")}
    con.close()
    assert set(AT.LATE_COLS) <= tg and "pain_score" in tg
    assert {"condition", "walkrun_from", "category", "illness"} <= ie
    # the old rows: kept, the new columns empty
    ev = INJ.load_events(path)
    assert [(e["id"], e["area"], e["condition"], e["walkrun_from"]) for e in ev] == [(5, "knee", None, None)]
    r = AT.load(path)[0]
    assert (r["pain"], r["pain_area"], r["injury_id"], r["pain_score"]) == (2, "knee", 5, None)
    assert AT.poles_of(r["tags"]) == "with" and AT.surface_of(r["tags"]) == "wet"
    # the upgraded DB takes the new values next to the old marks
    AT.upsert(path, start_local=k, pain=2, pain_area="knee", pain_score=4)
    r = AT.load(path)[0]
    assert r["pain_score"] == 4 and AT.poles_of(r["tags"]) == "with" and AT.surface_of(r["tags"]) == "wet"
    # a third start-up on the upgraded DB changes nothing
    monkeypatch.setattr(db_mod, "engine", None)

    async def again():
        await db_mod.init_db()
        await db_mod.dispose(path)
    _run(again())
    assert AT.load(path)[0]["pain_score"] == 4 and INJ.load_events(path)[0]["condition"] is None


# ---- rule D (SP-301) × D′ (SP-231) × pain lights (SP-271) × cold start (SP-288) ----------

def _adapt_mods():
    from backend.engine import adapt as A
    from backend.engine import overview as O
    from backend.engine import plan_store as PS
    from backend.tests.test_adapt import ctx, g, ids, week
    from backend.tests.test_adapt_rpe import EASY, rated
    return A, O, PS, ctx, g, ids, week, EASY, rated


Z3 = {7: {"session_type": "quality", "stimulus": "z3", "tss": 50}}
LIGHT = {"yellow": {"color": "yellow", "label": "膝蓋", "reason": "上次跑步 4 分"},
         "red": {"color": "red", "label": "膝蓋", "reason": "上次跑步 6 分"}}


def _gen(color=None):
    """Mon 09-28 … Sun 10-04: the easy run done Wed 09-30 (activity 7), the quality Thu 10-01
    (< 48 h), easy Sat, long Sun; under a pain light as week_plan applies it (light_apply)."""
    A, O, PS, ctx, g, ids, week, EASY, rated = _adapt_mods()
    ss = [g("easy0", "easy", "2026-09-30", 45, tss=36, done=True, done_by=EASY),
          g("quality", "quality", "2026-10-01", 60, tss=70, title="閾值 3×10 分"),
          g("easy1", "easy", "2026-10-03", 40, tss=32),
          g("long", "long", "2026-10-04", 120, tss=100),
          g("strength", "strength", "2026-10-02", 30, tss=12)]
    if color:
        notes: list = []
        ss = O.light_apply(ss, LIGHT[color], dt.date(2026, 10, 1), 0.0, 0.0, notes)
        assert any(n["src"] == "injury_light" for n in notes)
    return ss


def _cold():
    """A cold-start week (SP-288): three 30-min easy runs a day apart, no long run, no intervals."""
    A, O, PS, ctx, g, ids, week, EASY, rated = _adapt_mods()
    return [g("easy0", "easy", "2026-09-30", 30, tss=27.5, done=True, done_by=EASY),
            g("easy1", "easy", "2026-10-02", 30, tss=27.5), g("easy2", "easy", "2026-10-04", 30, tss=27.5)]


def _changes(adj):
    return [a for a in adj if a["action"] != "note"]


def test_rule_d_and_the_self_rating_adjust_the_same_quality_once():
    A, O, PS, ctx, g, ids, week, EASY, rated = _adapt_mods()
    c = ctx(today="2026-10-01", first_free="2026-10-01", reviews=Z3, rpe=rated(5))
    out, adj, notes = A.adapt(week(_gen()), [], c)
    a = _changes(adj)
    assert len(a) == 1 and a[0]["rule"] == A.OVERHARD_RULE and ids(out)["quality"]["day"] == "2026-10-02"
    assert list(notes) == [7]


@pytest.mark.parametrize("color", ["yellow", "red"])
def test_under_a_pain_light_rule_d_and_d_prime_have_nothing_to_move(color):
    A, O, PS, ctx, g, ids, week, EASY, rated = _adapt_mods()
    gen = _gen(color)
    assert not [s for s in gen if s["kind"] in A.HARD]                  # the light took the quality
    c = ctx(today="2026-10-01", first_free="2026-10-01", reviews=Z3, rpe=rated(5))
    out, adj, notes = A.adapt(week(gen), [], c)
    assert _changes(adj) == [] and out == week(gen)                     # one cut (the light's), not two
    assert notes == {7: "輕鬆跑跑成強度課（閾值課）：48 小時內沒有強度課，課表不用動"}
    assert [a["rule"] for a in adj] == [A.OVERHARD_RULE]                # one note, D′ says nothing more
    if color == "red":                                                  # nothing put back
        assert {s["id"] for s in out[0]["sessions"]} == {"easy0", "strength"}
    else:
        conv = next(s for s in out[0]["sessions"] if s.get("detail", "").startswith("疼痛黃燈"))
        assert conv["kind"] == "easy" and conv["day"] == "2026-10-01"


def test_a_cold_start_week_has_no_hard_session_for_rule_d_or_d_prime():
    A, O, PS, ctx, g, ids, week, EASY, rated = _adapt_mods()
    for rv in (Z3, {7: {"avg_power": 260, "cp": 300, "tss": 40}}):      # 太強 / 偏強
        out, adj, notes = A.adapt(week(_cold()), [], ctx(today="2026-10-01", first_free="2026-10-01",
                                                         reviews=rv, rpe=rated(5)))
        assert _changes(adj) == [] and out == week(_cold()) and list(notes) == [7]


def _inp(gen, reviews, rpe):
    from backend.tests.test_adapt_rpe import EASY
    return {"cur": {"week": {"start": "2026-09-28", "end": "2026-10-04", "today": "2026-10-01", "days_left": 4},
                    "mode": "base", "load": {}, "sessions": copy.deepcopy(gen), "done": {"activities": [EASY]}},
            "weeks": [], "activities": [EASY], "today": "2026-10-01", "horizon_end": "2026-10-04",
            "thresholds": {"cp": 300.0, "lthr": 170.0, "aet": 150.0},
            "adapt": {"enabled": True, "reviews": reviews, "first_free": "2026-10-01", "rpe": rpe}}


@pytest.mark.parametrize("case", ["plain", "yellow", "red", "cold"])
def test_stable_through_reconcile(case):
    A, O, PS, ctx, g, ids, week, EASY, rated = _adapt_mods()
    gen = _cold() if case == "cold" else _gen(None if case == "plain" else case)
    inp = _inp(gen, Z3, rated(5))
    new, ch = PS.reconcile_with_adapt([], inp, adjustments=[])
    act = [s for s in new if s["state"] == "active"]
    q = [s for s in act if s["kind"] in A.HARD]
    if case == "plain":
        assert [s["day"] for s in q] == ["2026-10-02"]
    else:
        assert q == []
    done = next(s for s in new if s.get("gen_key") == "easy0")
    assert done["note"].startswith("輕鬆跑跑成強度課（閾值課）")
    again, ch2 = PS.reconcile_with_adapt(new, copy.deepcopy(inp))
    assert ch2 == []
    third, ch3 = PS.reconcile_with_adapt(again, copy.deepcopy(inp))
    assert ch3 == [] and [s["day"] for s in third if s["state"] == "active"] == \
        [s["day"] for s in again if s["state"] == "active"]


# ---- the easy rate (SP-302) for a cold-start runner (SP-288 / SP-289) --------------------

@pytest.fixture
def cold_week(monkeypatch):
    from backend.engine import experience as EX
    from backend.engine import hr_profile as HP
    from backend.engine import race_results as RR
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    monkeypatch.setattr(HP, "plan_model", lambda user_id=1: "lthr")
    monkeypatch.setattr(EX, "load", lambda user_id=1: None)
    monkeypatch.setattr(RR, "load", lambda user_id=1: [])
    from backend.engine.planning import Plan
    from backend.tests.test_hr_prior import _ds, _week
    return lambda profile=None: _week(_ds(Plan(profile=profile or {})))


def _easy_runs(wp):
    return [s for s in wp["sessions"] if s["kind"] == "easy" and s["minutes"]]


def test_cold_start_without_any_threshold_prices_easy_runs_at_the_estimated_default(cold_week):
    from backend.engine import overview as O
    wp = cold_week()
    assert wp["cold_start"] and wp["cold_start"]["week"] == 1
    info, tph = wp["easy_tss"], wp["tss_per_category"]
    # SP-302 decision (2026-10-06): < 3 easy runs → IF 0.80 (64 TSS / h, 推估), thresholds or not
    assert info["n"] == 0 and info["estimated"] and info["source"] == O.SRC_EASY_TSS_EST
    assert tph["easy"] == pytest.approx(64.0)
    runs = _easy_runs(wp)
    assert len(runs) == 3 and all(s["tss"] == pytest.approx(s["minutes"] / 60 * tph["easy"]) for s in runs)
    srcs = [n.get("src") for n in wp["notes"]]
    assert "easy_tss" in srcs and "cold_start" in srcs


def test_cold_start_with_the_lthr_prior_prices_easy_runs_at_the_estimate_too(cold_week):
    from backend.engine import overview as O
    wp = cold_week({"birth_year": TODAY.year - 40})
    th = wp["thresholds"]
    assert th["lthr_prior"] and th["lthr"] == 162.0 and wp["cold_start"]
    info, tph = wp["easy_tss"], wp["tss_per_category"]
    assert info["estimated"] and info["source"] == O.SRC_EASY_TSS_EST
    assert tph["easy"] == pytest.approx(64.0)                  # IF 0.80, not the prior cap ÷ LTHR
    assert all(s["tss"] == pytest.approx(s["minutes"] / 60 * tph["easy"]) for s in _easy_runs(wp))
    # the long-run / category rates stay the defaults (no history)
    assert tph["road"] == O.TSS_PER_HOUR_DEFAULT["road"]


# ---- the activity list: poles × surface × rain × pain × kind ------------------------------

def test_activity_list_carries_every_mark_and_the_viewer_list_the_kind(tmp_path, monkeypatch):
    from backend.api import wko5views as V
    from backend.engine import injuries as INJ
    from backend.engine import planning
    from backend.engine import route_weather as RW
    from backend.engine import routes as R
    from backend.engine import sport_map as SM
    from backend.engine.planning import Event, Plan
    from backend.tests.test_activity_edit import _fit_ds
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: cls()))
    monkeypatch.setattr(INJ, "demo_mode", lambda: False)
    db = tmp_path / "tags.db"
    monkeypatch.setattr(AT, "_default_db", lambda: db)
    ds = _fit_ds(tmp_path)
    monkeypatch.setattr(V, "_dataset", lambda parity=None, source=None: ds)
    run = next(w for w in ds.workouts if w.entry.file == "2025/0.fit")      # 5.4 km road run
    ds.plan = Plan(events=[Event(id="e1", name="河濱賽", date=run.entry.start.date().isoformat(), kind="road",
                                 distance_km=5.4, poles=True)])
    home = tmp_path / "routes"
    home.mkdir()
    monkeypatch.setattr(R, "HOME", home)
    (home / RW.ACTIVITY_WX_FILE).write_text(json.dumps({"version": RW.ACTIVITY_WX_VERSION, "activities": {
        "2025/0.fit": {"temp_c": 22.0, "rain_mm": 3.2, "start": run.entry.start.isoformat(timespec="seconds")}}}),
        "utf-8")
    AT.upsert(db, start_local=AT.key_of(run.entry.start), file="2025/0.fit", surface="wet",
              pain=2, pain_area="knee", pain_score=3)

    lst = V.activities_list()
    a = {x["file"]: x for x in lst["activities"]}["2025/0.fit"]
    assert (a["poles"], a["poles_race"], a["poles_user"]) == ("with", "河濱賽", None)      # SP-300
    assert a["surface"] == "wet" and a["tags"] == [AT.SURFACES["wet"]]                    # SP-250
    assert a["rain_mm"] == 3.2 and AT.rain_hint(a["rain_mm"], a["surface"]) is None      # SP-299: marked
    assert (a["pain"], a["pain_area"], a["pain_score"]) == (2, "knee", 3)                 # SP-271
    assert lst["pole_none_tag"] == AT.POLE_NONE_TAG and lst["surface_tags"] == AT.SURFACES
    # the viewer's list: the same activity with its platform-neutral kind (SP-263)
    rows = V.workouts(begin="2025-12-01", end="2025-12-31", sports=None, parity=None)
    w0 = next(x for x in rows if x["file"] == "2025/0.fit")
    assert w0["kind"] == SM.kind_of(run) == "road"
    only_road = V.workouts(begin="2025-12-01", end="2025-12-31", sports="road", parity=None)
    assert [x["file"] for x in only_road if x["index"] is not None] == ["2025/0.fit"]
    # the editor's own GET agrees with the list
    g = V.get_activity(run.idx)
    assert (g["poles"], g["poles_race"], g["surface"]) == ("with", "河濱賽", "wet")
