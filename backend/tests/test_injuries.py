"""傷病紀錄 (engine/injuries.py, engine/injury_exposure.py, api/injuries.py;
docs/plans/injury-tracking.plan.md). Synthetic data and tmp SQLite only —
never the WKO5 folder, the app DB or ~/.wko5coach."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import random
import sqlite3
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import activity_tags as AT
from backend.engine import injuries as INJ
from backend.engine import injury_exposure as IE
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WKO5COACH_MODE", raising=False)
    INJ._memo.clear()
    yield
    INJ._memo.clear()


def ev(id, onset, area="knee", severity="moderate", status="active", resolved=None, kind="overuse", **kw):
    return {"id": id, "area": area, "side": kw.get("side"), "kind": kind, "severity": severity,
            "onset_date": onset, "onset_key": kw.get("onset_key"), "status": status, "resolved_date": resolved,
            "recurrence_of": kw.get("recurrence_of"), "pause_quality": kw.get("pause_quality", False),
            "days_missed": None, "pain_max": None, "note": None, "onset_file": None}


# ---------------------------------------------------------------------------
# store and migration
# ---------------------------------------------------------------------------

def test_default_db_is_blocked_in_tests():
    assert INJ.load_events() == []


def test_old_activity_tags_table_loads_and_upsert_adds_pain_columns(tmp_path):
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE activity_tags (id INTEGER PRIMARY KEY, athlete_id INTEGER, start_local TEXT, "
                "source TEXT, file TEXT, workout_id INTEGER, distance_km REAL, label TEXT, activity_type TEXT, "
                "activity_type_overridden BOOLEAN, effort TEXT, effort_overridden BOOLEAN, note TEXT, "
                "updated_at DATETIME, UNIQUE(athlete_id, start_local))")
    con.execute("INSERT INTO activity_tags (athlete_id, start_local, note) VALUES (1, '2025-01-01T08:00', 'x')")
    con.commit()
    con.close()
    r = AT.load(db)[0]
    assert r["pain"] is None and r["pain_area"] is None and r["note"] == "x"      # missing columns read as None
    AT.upsert(db, start_local="2025-01-02T08:00", pain=2, pain_area="knee")
    rows = {x["start_local"]: x for x in AT.load(db)}
    assert rows["2025-01-02T08:00"]["pain"] == 2 and rows["2025-01-02T08:00"]["pain_area"] == "knee"
    assert rows["2025-01-01T08:00"]["note"] == "x"                               # old data kept
    with pytest.raises(ValueError):
        AT.upsert(db, start_local="2025-01-02T08:00", pain=7)
    AT.upsert(db, start_local="2025-01-02T08:00", pain=0)                        # 沒痛 clears the area
    assert {x["start_local"]: x for x in AT.load(db)}["2025-01-02T08:00"]["pain_area"] is None


def test_migration_adds_pain_columns_and_creates_injury_table():
    async def _inner():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        import backend.db.database as db_mod
        from backend.db.models import Base
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE activity_tags (id INTEGER PRIMARY KEY, start_local TEXT, note TEXT)"))
            await conn.execute(text("INSERT INTO activity_tags (start_local, note) VALUES ('2025-01-01T08:00', 'keep')"))
            await conn.run_sync(Base.metadata.create_all)        # init_db: missing tables only
        orig = db_mod.engine
        db_mod.engine = engine
        try:
            await db_mod._migrate_schema()
            await db_mod._migrate_schema()                       # idempotent
            async with engine.begin() as conn:
                cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(activity_tags)"))).fetchall()}
                icols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(injury_events)"))).fetchall()}
                note = (await conn.execute(text("SELECT note FROM activity_tags"))).scalar()
        finally:
            db_mod.engine = orig
        assert {"pain", "pain_area", "injury_id"} <= cols and note == "keep"
        assert {"area", "severity", "onset_date", "status", "resolved_date", "pause_quality"} <= icols
    _run(_inner())


# ---------------------------------------------------------------------------
# attach(): the one-tap mark → events
# ---------------------------------------------------------------------------

D = date(2026, 9, 10)


def test_sore_never_creates_an_event():
    r = INJ.attach(1, "knee", None, D, "2026-09-10T07:00", None, None, [])
    assert r == {"injury_id": None, "create": None, "update": None, "delete": None}


def test_pain_creates_a_draft_and_stop_is_moderate():
    r = INJ.attach(2, "knee", "right", D, "2026-09-10T07:00", "a.fit", None, [])
    assert r["create"]["status"] == "draft" and r["create"]["severity"] == "mild" and r["create"]["side"] == "right"
    assert r["create"]["onset_date"] == "2026-09-10" and r["create"]["onset_key"] == "2026-09-10T07:00"
    r = INJ.attach(3, None, None, D, "k", None, None, [])
    assert r["create"]["area"] == INJ.UNKNOWN and r["create"]["severity"] == "moderate"


def test_same_area_within_28_days_joins_29_days_opens_new():
    evs = [ev(5, "2026-08-13")]                                  # 28 days before
    assert INJ.attach(2, "knee", None, D, "k", None, None, evs)["injury_id"] == 5
    evs = [ev(5, "2026-08-12")]                                  # 29 days
    r = INJ.attach(2, "knee", None, D, "k", None, None, evs)
    assert r["injury_id"] is None and r["create"] is not None
    evs = [ev(5, "2026-09-01", area="hip")]                      # another area
    assert INJ.attach(2, "knee", None, D, "k", None, None, evs)["create"]["area"] == "knee"
    assert INJ.attach(2, None, None, D, "k", None, None, evs)["injury_id"] == 5     # no area: any open one


def test_resolved_is_not_joined_and_a_return_is_a_recurrence():
    evs = [ev(5, "2026-08-01", status="resolved", resolved="2026-08-20")]
    r = INJ.attach(2, "knee", None, D, "k", None, None, evs)
    assert r["injury_id"] is None and r["create"]["recurrence_of"] == 5
    assert "21 天" in r["create"]["note"] and "同部位" in r["create"]["note"]
    evs = [ev(5, "2026-05-01", status="resolved", resolved="2026-06-01")]          # > 42 days: noted, not a recurrence
    r = INJ.attach(2, "knee", None, D, "k", None, None, evs)
    assert r["create"]["recurrence_of"] is None and "同部位" in r["create"]["note"]


def test_back_to_none_deletes_the_orphan_draft_only():
    evs = [ev(7, "2026-09-10", status="draft", onset_key="k")]
    assert INJ.attach(None, None, None, D, "k", None, 7, evs, {7: 1})["delete"] == 7
    assert INJ.attach(0, None, None, D, "k", None, 7, evs, {7: 1})["delete"] == 7
    assert INJ.attach(None, None, None, D, "k", None, 7, evs, {7: 2})["delete"] is None   # another activity uses it
    evs = [ev(7, "2026-09-10", status="active", onset_key="k")]
    assert INJ.attach(None, None, None, D, "k", None, 7, evs, {7: 1})["delete"] is None   # a real event stays


def test_own_draft_is_updated_not_duplicated():
    evs = [ev(7, "2026-09-10", area=INJ.UNKNOWN, status="draft", severity="mild", onset_key="k")]
    r = INJ.attach(3, "ankle", "left", D, "k", None, 7, evs, {7: 1})
    assert r["injury_id"] == 7 and r["create"] is None
    assert r["update"] == {"id": 7, "fields": {"area": "ankle", "side": "left", "severity": "moderate"}}


def test_custom_areas_validate():
    assert INJ.validate_pain(2, "足弓") is None and INJ.norm_area("足弓") == "足弓"
    assert INJ.norm_area("膝") == "knee"                         # a built-in label typed as text
    assert INJ.validate_pain(2, "x" * 13) == "INVALID_AREA"
    assert INJ.validate_pain(9) == "INVALID_PAIN" and INJ.validate_pain(True) == "INVALID_PAIN"


def test_days_off_auto_subtracts_usual_rest():
    onset = date(2026, 9, 1)
    before = [(onset - dt.timedelta(days=k), 50, None) for k in range(1, 29) if k % 2 == 0]   # runs every other day
    runs = before + [(date(2026, 9, 12), 15, None), (date(2026, 9, 13), 40, 2), (date(2026, 9, 15), 40, 1)]
    e = ev(1, "2026-09-01")
    # 13 days between the onset and the first ≥ 20-min run with pain ≤ 1 (9/15), half of them usual rest days
    assert INJ.days_off_auto(e, runs, date(2026, 9, 20)) == round(13 - 0.5 * 13)


# ---------------------------------------------------------------------------
# the pain mark through the API helpers (tmp SQLite)
# ---------------------------------------------------------------------------

def _tmp_session(tmp_path):
    from backend.db.models import Base
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")

    async def setup():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    _run(setup())
    return eng, async_sessionmaker(eng, expire_on_commit=False)


def test_save_pain_mark_creates_joins_and_drops_drafts(tmp_path):
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    from backend.db.models import ActivityTag, InjuryEvent
    from sqlalchemy import select
    eng, S = _tmp_session(tmp_path)

    async def _inner():
        async with S() as s:
            await save_activity_tag(s, ActivityUpdate(pain=2, pain_area="knee", pain_side="right"),
                                    start_local="2026-09-01T07:00", file="a.fit")
            await save_activity_tag(s, ActivityUpdate(pain=3, pain_area="knee"), start_local="2026-09-08T07:00")
            await save_activity_tag(s, ActivityUpdate(pain=1, pain_area="knee"), start_local="2026-09-09T07:00")
            evs = (await s.execute(select(InjuryEvent))).scalars().all()
            tags = {t.start_local: t for t in (await s.execute(select(ActivityTag))).scalars().all()}
            assert len(evs) == 1 and evs[0].status == "draft" and evs[0].side == "right"
            assert tags["2026-09-01T07:00"].injury_id == evs[0].id == tags["2026-09-08T07:00"].injury_id
            assert tags["2026-09-09T07:00"].injury_id is None                      # 痠 never attaches
            # a mistap on another day: its own draft, then cleared again → gone
            await save_activity_tag(s, ActivityUpdate(pain=2, pain_area="hip"), start_local="2026-09-10T07:00")
            assert len((await s.execute(select(InjuryEvent))).scalars().all()) == 2
            await save_activity_tag(s, ActivityUpdate(pain=None), start_local="2026-09-10T07:00")
            assert len((await s.execute(select(InjuryEvent))).scalars().all()) == 1
            # a custom area joins the picker
            await save_activity_tag(s, ActivityUpdate(pain=2, pain_area="足弓"), start_local="2026-09-11T07:00")
            from backend.settings.repository import SettingsRepository
            assert await SettingsRepository(s).get(INJ.SETTING_AREAS) == ["足弓"]
            with pytest.raises(Exception):
                await save_activity_tag(s, ActivityUpdate(pain=5), start_local="2026-09-12T07:00")
    _run(_inner())
    _run(eng.dispose())


# ---------------------------------------------------------------------------
# the injuries API (a minimal app: tmp DB, fake dataset; never the real lifespan)
# ---------------------------------------------------------------------------

@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import injuries as IA
    from backend.api import wko5views as WV
    from backend.db.database import get_db
    eng, S = _tmp_session(tmp_path)

    async def _db():
        async with S() as s:
            yield s
    monkeypatch.setattr(AT, "_default_db", lambda: tmp_path / "app.db")
    ds = FakeDataset(_runs(date(2025, 1, 1), date(2026, 9, 30), seed=3), date(2026, 9, 30))
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: ds)
    app = FastAPI()
    app.include_router(IA.router)
    app.dependency_overrides[get_db] = _db
    IA._an_memo.clear()
    yield TestClient(app)
    _run(eng.dispose())


def test_api_crud_resolve_and_errors(api):
    r = api.post("/api/v1/wko5/injuries", json={"area": "knee", "side": "right", "severity": "moderate",
                                                 "onset_date": "2026-08-01"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["status"] == "active" and e["label"] == "右膝" and e["open"]
    assert api.post("/api/v1/wko5/injuries", json={"area": "knee", "onset_date": "2099-01-01"}).status_code == 400
    assert api.post("/api/v1/wko5/injuries", json={"area": "knee", "severity": "x", "onset_date": "2026-08-01"}).status_code == 400
    r = api.patch(f"/api/v1/wko5/injuries/{e['id']}", json={"resolved_date": "2026-07-01"})
    assert r.status_code == 400 and r.json()["detail"] == "RESOLVED_BEFORE_ONSET"
    r = api.post(f"/api/v1/wko5/injuries/{e['id']}/resolve", json={"date": "2026-08-20"})
    assert r.json()["status"] == "resolved" and r.json()["resolved_date"] == "2026-08-20"
    r = api.post("/api/v1/wko5/injuries", json={"area": "knee", "onset_date": "2026-09-10"})
    assert r.json()["recurrence_of"] == e["id"]                                      # 21 days later, same area
    lst = api.get("/api/v1/wko5/injuries").json()["injuries"]
    assert [x["id"] for x in lst] == [r.json()["id"], e["id"]]
    assert api.delete(f"/api/v1/wko5/injuries/{e['id']}").status_code == 200
    assert api.get(f"/api/v1/wko5/injuries/{e['id']}/days").status_code == 404
    m = api.get("/api/v1/wko5/injuries/meta").json()
    assert len([a for a in m["areas"] if not a["custom"]]) == 9 and not m["settings"]["pattern_alerts"]
    assert m["settings"]["reentry_step_up"] is True
    assert api.post("/api/v1/wko5/injuries/areas", json={"label": "足弓"}).status_code == 200
    assert any(a["key"] == "足弓" and a["custom"] for a in api.get("/api/v1/wko5/injuries/meta").json()["areas"])
    api.delete("/api/v1/wko5/injuries/areas/足弓")
    assert not any(a["custom"] for a in api.get("/api/v1/wko5/injuries/meta").json()["areas"])


def test_pattern_alerts_cannot_be_enabled_below_five(api):
    api.post("/api/v1/wko5/injuries", json={"area": "knee", "onset_date": "2026-06-01", "resolved_date": "2026-06-10"})
    r = api.put("/api/v1/wko5/injuries/settings", json={"pattern_alerts": True})
    assert r.status_code == 400 and "5 次" in r.json()["detail"]
    assert api.put("/api/v1/wko5/injuries/settings", json={"reentry_step_up": False}).json()["reentry_step_up"] is False


def test_api_analysis_and_timeline(api):
    api.post("/api/v1/wko5/injuries", json={"area": "knee", "onset_date": "2026-06-01", "resolved_date": "2026-06-10"})
    a = api.get("/api/v1/wko5/injuries/analysis").json()
    assert a["tier"] == 1 and a["n"] == 1
    t = api.get("/api/v1/wko5/injuries/timeline?begin=2026-01-01&end=2026-09-30").json()
    assert t["bands"][0]["area"] == "knee" and t["weeks"] and t["weeks"][0]["week"] <= "2026-01-01"
    d = api.get(f"/api/v1/wko5/injuries/{t['bands'][0]['id']}/days").json()["days"]
    assert len(d) == 22 and d[-1]["onset"]


def test_demo_mode_hides_everything(api, monkeypatch):
    monkeypatch.setenv("WKO5COACH_MODE", "demo")
    for path in ("", "/meta", "/analysis", "/timeline", "/page"):
        assert api.get(f"/api/v1/wko5/injuries{path}").status_code == 404
    assert api.post("/api/v1/wko5/injuries", json={"area": "knee", "onset_date": "2026-06-01"}).status_code == 404
    assert INJ.load_events() == []
    from backend.api.workouts import ActivityUpdate, save_activity_tag
    from fastapi import HTTPException

    async def _inner():
        eng = create_async_engine("sqlite+aiosqlite:///:memory:")
        from backend.db.models import Base
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(eng)() as s:
            with pytest.raises(HTTPException) as ex:
                await save_activity_tag(s, ActivityUpdate(pain=2), start_local="2026-09-01T07:00")
            assert ex.value.status_code == 404
        await eng.dispose()
    _run(_inner())


# ---------------------------------------------------------------------------
# exposure and the case-crossover (synthetic 2–5 years of runs)
# ---------------------------------------------------------------------------

def _runs(a: date, b: date, seed: int = 1, boost: tuple = (), boost_f: float = 1.4, minutes: float = 50.0):
    """4 runs a week (Mon / Wed / Fri / Sun), ±10 % noise; days inside a
    `boost` range [lo, hi] run `boost_f` × longer."""
    rnd = random.Random(seed)
    out = []
    d = a
    while d <= b:
        if d.weekday() in (0, 2, 4, 6):
            m = minutes * (1 + rnd.uniform(-0.1, 0.1))
            if any(lo <= d <= hi for lo, hi in boost):
                m *= boost_f
            out.append(FakeWorkout(start=dt.datetime(d.year, d.month, d.day, 7, 0), sport="run",
                                   metrics={"duration": m * 60, "distance": m / 6.0, "tss": m * 0.9,
                                            "climbing": m * 3, "descending": m * 3}))
        d += dt.timedelta(days=1)
    return out


def _daily(ds, today):
    return IE.build_daily(ds, today, tags=[], recorded=[], pack_meta={}, detail=lambda ds, w: None)


def test_daily_series_and_windows():
    today = date(2026, 3, 31)
    ds = FakeDataset(_runs(date(2026, 1, 1), today), today)
    D = _daily(ds, today)
    e = D.i(date(2026, 3, 1))
    ctl = IE._pmc(D.tss, 42)
    w = IE.window(D, e, ctl)
    assert w["_runs"] == 12 and 9.0 < w["run_h"] < 11.0
    assert w["steep_h"] is None and w["impact"] is None and w["low_share"] is None   # no samples: unknown, not 0
    assert abs(w["step0"]) < 25 and w["acwr"] is not None
    assert IE.window(D, 10, ctl)["step0"] is None                                       # not enough history


def test_detail_metrics_need_coverage():
    today = date(2026, 3, 31)
    ds = FakeDataset(_runs(date(2026, 1, 1), today), today)
    D = IE.build_daily(ds, today, tags=[], recorded=[], pack_meta={},
                       detail=lambda ds, w: {"mov_s": 3000, "steep_s": 300.0, "cad": 85.0, "impact": None,
                                             "low_s": 2400.0, "high_s": 100.0})
    w = IE.window(D, D.i(date(2026, 3, 1)), IE._pmc(D.tss, 42), cad_median=85.0)
    assert w["steep_h"] == pytest.approx(12 * 300 / 3600.0) and w["impact"] is None
    assert w["low_share"] == pytest.approx(100 * 2400 / 2500) and w["cad_rel"] == pytest.approx(0.0)


def test_step_needs_a_normal_base_week():
    today = date(2026, 3, 31)
    ws = [w for w in _runs(date(2026, 1, 1), today) if not date(2026, 2, 16) <= w.start.date() <= date(2026, 2, 22)]
    ds = FakeDataset(ws, today)
    D = _daily(ds, today)
    # W0 = 2/23–3/1, W1 = 2/16–2/22 (empty: < 60 % of the 4-week mean) → no step
    assert IE.window(D, D.i(date(2026, 3, 2)), IE._pmc(D.tss, 42))["step0"] is None


ONSETS = [date(2024, 3, 4), date(2024, 6, 3), date(2024, 9, 2), date(2024, 12, 2), date(2025, 3, 3), date(2025, 6, 2)]


def _boosted(onsets=ONSETS):
    today = date(2025, 12, 31)
    boost = tuple((o - dt.timedelta(days=7), o - dt.timedelta(days=1)) for o in onsets)
    ds = FakeDataset(_runs(date(2023, 6, 1), today, boost=boost), today)
    evs = [ev(i + 1, o.isoformat(), status="resolved", resolved=(o + dt.timedelta(days=5)).isoformat())
           for i, o in enumerate(onsets)]
    return ds, evs, today


def test_case_crossover_finds_the_planted_step():
    ds, evs, today = _boosted()
    an = IE.analysis(ds, evs, today, D=_daily(ds, today))
    assert an["n"] == 6 and an["tier"] == 2 and an["controls"] >= 20
    m = {r["key"]: r for r in an["metrics"]}
    assert all(c["pct"] >= 90 for c in m["step0"]["cases"])
    assert m["step0"]["median_pct"] >= 90 and m["step0"]["k_above"] == 6
    assert "p" not in m["step0"] and "or" not in m["step0"]                       # tier 2: no p / OR
    assert any("第" in t and "百分位" in t for t in m["step0"]["texts"])


def test_control_windows_exclude_onsets_injuries_and_low_volume():
    ds, evs, today = _boosted()
    D = _daily(ds, today)
    ends = IE.control_ends(D, evs, today)
    for e in ends:
        day = D.d0 + dt.timedelta(days=e)
        for x in evs:
            o = date.fromisoformat(x["onset_date"])
            assert abs((day - o).days) > IE.NEAR_ONSET
            assert not (o <= day <= date.fromisoformat(x["resolved_date"]) + dt.timedelta(days=IE.AFTER_RESOLVED))
    # a training gap: windows with < 3 runs are not controls
    ws = [w for w in ds.workouts]
    gap = FakeDataset([FakeWorkout(start=w.entry.start, sport="run", metrics=dict(w.metrics)) for w in ws
                       if not date(2025, 9, 1) <= w.entry.start.date() <= date(2025, 10, 15)], today)
    D2 = _daily(gap, today)
    for e in IE.control_ends(D2, evs, today):
        day = D2.d0 + dt.timedelta(days=e)
        assert not (date(2025, 9, 22) <= day <= date(2025, 10, 15))


def test_random_events_land_near_the_middle():
    today = date(2025, 12, 31)
    ds = FakeDataset(_runs(date(2023, 1, 1), today, seed=9), today)
    rnd = random.Random(4)
    onsets = sorted({date(2023, 3, 1) + dt.timedelta(days=rnd.randrange(0, 950)) for _ in range(30)})
    keep = []
    for o in onsets:                                          # ≥ 80 days apart, so controls remain
        if not keep or (o - keep[-1]).days >= 80:
            keep.append(o)
    evs = [ev(i + 1, o.isoformat(), status="resolved", resolved=(o + dt.timedelta(days=3)).isoformat())
           for i, o in enumerate(keep)]
    an = IE.analysis(ds, evs, today, D=_daily(ds, today))
    pts = [c["pct"] for c in next(r for r in an["metrics"] if r["key"] == "run_h")["cases"] if c["pct"] is not None]
    assert len(pts) >= 8 and 25 <= sum(pts) / len(pts) <= 75


def _tier_case(n):
    today = date(2025, 12, 31)
    onsets = [date(2021, 3, 1) + dt.timedelta(days=90 * k) for k in range(n)]
    ds = FakeDataset(_runs(date(2020, 12, 1), today, seed=n), today)
    evs = [ev(i + 1, o.isoformat(), status="resolved", resolved=(o + dt.timedelta(days=3)).isoformat())
           for i, o in enumerate(onsets)]
    return IE.analysis(ds, evs, today, D=_daily(ds, today))


@pytest.mark.parametrize("n, tier", [(0, 0), (3, 1), (7, 2), (20, 3)])
def test_honesty_tiers(n, tier):
    an = _tier_case(n)
    assert an["tier"] == tier and an["n"] == n
    blob = json.dumps(an, ensure_ascii=False)
    for word in ("造成", "導致", "風險"):
        assert word not in blob
    for r in an["metrics"]:
        if tier < 3:
            assert not ({"p", "or", "ci", "q"} & set(r))                          # no statistic keys at all
        if tier < 2:
            assert "median_pct" not in r and "k_above" not in r
    if tier == 0:
        assert an["metrics"] == []
    if tier == 3:
        assert any("or" in r and "ci" in r and r.get("exploratory") for r in an["metrics"])


def test_severity_filter_and_exclusions():
    ds, evs, today = _boosted()
    evs[0]["severity"] = "mild"                                 # mild injuries ARE analysed (owner decision)
    evs[1]["status"] = "draft"
    evs[2]["kind"] = "acute"
    evs[3]["recurrence_of"] = 1
    D = _daily(ds, today)
    an = IE.analysis(ds, evs, today, D=D)
    assert an["n"] == 3 and {x["id"] for x in an["excluded"]} == {2, 3, 4}
    assert any(c["id"] == 1 for c in an["metrics"][0]["cases"])
    an2 = IE.analysis(ds, evs, today, D=D, severities=("moderate", "severe"))
    assert an2["n"] == 2 and an2["acute"][0]["id"] == 3


def test_season_option_uses_nearby_controls():
    ds, evs, today = _boosted()
    an = IE.analysis(ds, evs, today, D=_daily(ds, today), season=True)
    assert an["season"] and an["n"] == 6


def test_pattern_alert_needs_five_and_never_acwr():
    ds, evs, today = _boosted()
    D = _daily(ds, today)
    an = IE.analysis(ds, evs, today, D=D)
    hot = {"run_h": 99.0, "acwr": 9.0, "monotony": 99.0, "step0": 999.0, "step1": 999.0}
    ctrl = {"run_h": [1.0] * 30, "acwr": [1.0] * 30, "monotony": [1.0] * 30, "step0": [0.0] * 30, "step1": [0.0] * 30}
    keys = {a["metric"] for a in IE.pattern_alerts(an, hot, ctrl, today)}
    assert "acwr" not in keys and "monotony" not in keys and "step0" not in keys
    few = {**an, "n": 4}
    assert IE.pattern_alerts(few, hot, ctrl, today) == []


# ---------------------------------------------------------------------------
# the planner: pause, notes, re-entry, suggestions
# ---------------------------------------------------------------------------

def test_pause_quality_blocks_until_resolved():
    from backend.engine import quality_gate as QG
    evs = [ev(12, "2026-09-20", pause_quality=True)]
    why = INJ.pause_reason(evs, date(2026, 9, 25))
    assert why and "#12" in why
    g = QG.guard(injury=why)
    assert g["block"] and g["rule"] == "injury"
    gate = {"guard": g, "levels": {}, "dose": {"done": 0, "step": 0}}
    assert QG.week_decision(gate, "base", "base", first=True)["allow"] is False
    assert QG.week_decision(gate, "specific", "specific", first=False)["allow"] is False
    evs[0].update(status="resolved", resolved_date="2026-09-24")
    assert INJ.pause_reason(evs, date(2026, 9, 25)) is None
    assert QG.guard(injury=None)["rule"] == ""


def test_week_notes():
    evs = [ev(1, "2026-09-24", side="right")]
    n = INJ.week_notes(evs, date(2026, 9, 28), date(2026, 9, 28))
    assert n and n[0]["src"] == "injury" and "右膝進行中（第 5 天" in n[0]["text"]


def test_injury_layoff_steps_up_the_reentry_block():
    from backend.engine import reentry as RE
    last, ret = date(2026, 9, 1), date(2026, 9, 12)                 # 10 days off
    plain = RE.plan(last, ret, prev_hours=5.0)
    inj = RE.plan(last, ret, prev_hours=5.0, injury=ev(12, "2026-09-02", side="right"), step_up=True)
    off = RE.plan(last, ret, prev_hours=5.0, injury=ev(12, "2026-09-02", side="right"), step_up=False)
    assert plain["category"] == "6-13" and off["category"] == "6-13"
    assert inj["category"] == "14-28" and inj["stepped_up"] and inj["days"] == 10 and inj["days_effective"] == 14
    assert (date.fromisoformat(inj["end"]) - ret).days == 14 and inj["drift_check"]
    assert inj["fvdot"] == plain["fvdot"]                              # fitness loss of the real break
    assert "傷停 10 天（右膝，傷病紀錄 #12）" in inj["text"] and "推估" in inj["text"]
    assert "傷停" in off["text"] and not off["stepped_up"]


def test_reentry_find_uses_overlapping_events():
    from backend.engine import reentry as RE
    today = date(2026, 9, 30)
    ws = [w for w in _runs(date(2026, 6, 1), today) if not date(2026, 9, 5) <= w.start.date() <= date(2026, 9, 16)]
    ds = FakeDataset(ws, today)
    p = RE.find(ds, today, injuries=[ev(3, "2026-09-04", status="resolved", resolved="2026-09-15")], step_up=True)
    assert p["injury"]["id"] == 3 and p["stepped_up"]
    assert RE.find(ds, today, injuries=[], step_up=True)["injury"] is None


def test_suggestion_rows():
    from backend.engine import suggestions as SG
    evs = [ev(4, "2026-09-25", severity="severe")]
    rows = SG.injury_rows(evs, "2026-09-28", set(), None, [])
    assert rows[0]["type"] == "injury_rest" and rows[0]["pick"] == "confirm"
    assert rows[0]["start"] == "2026-09-28" and rows[0]["end"] == "2026-10-04"
    blocked = {(date(2026, 9, 28) + dt.timedelta(days=i)).isoformat() for i in range(7)}
    assert SG.injury_rows(evs, "2026-09-28", blocked, None, []) == []
    assert SG.injury_rows([ev(4, "2026-09-25", severity="moderate")], "2026-09-28", set(), None, []) == []
    rp = {"return": "2026-09-20", "end": "2026-10-04"}
    marks = [{"date": "2026-09-26", "pain": 2, "area": "knee"}]
    hold = SG.injury_rows([], "2026-09-28", set(), rp, marks)
    assert hold[0]["type"] == "injury_hold" and hold[0]["pick"] is None and "Silbernagel 2007" in hold[0]["help"]


def test_silbernagel_numbers_are_the_papers():
    assert INJ.SILBERNAGEL["during_max"] == 5 and INJ.SILBERNAGEL["after_max"] == 5
    assert "隔天早上" in INJ.SILBERNAGEL["text"] and "推估" in INJ.SILBERNAGEL["text"]


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------

def test_share_snapshot_never_carries_injury_or_pain():
    from backend.engine.racepower import share as SH
    plan = {"type": "road", "summary": {"time_s": 3600, "injury_note": "膝"}, "segments": [{"i": 0, "pain": 3}],
            "fuel": {"warnings": [], "pain_flag": True, "daily": [{"day": 1, "injury": {"id": 1}}]},
            "injuries": [{"id": 1}], "used": {}}
    snap = SH.snapshot(plan, title="t")
    blob = json.dumps(snap, ensure_ascii=False)
    assert "injur" not in blob and "pain" not in blob and snap["summary"]["time_s"] == 3600


def test_ai_context_has_no_injuries(tmp_path, monkeypatch):
    from backend.db.models import Athlete, Base, InjuryEvent
    from backend.engine.ai.context import build_context
    monkeypatch.setattr(AT, "_default_db", lambda: tmp_path / "ai.db")
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ai.db'}")

    async def _inner():
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(eng, expire_on_commit=False)() as s:
            s.add(Athlete(id=1, name="a", data_dir="/tmp"))
            s.add(InjuryEvent(athlete_id=1, area="knee", severity="severe", onset_date="2026-09-01", status="active",
                              note="右膝外側 SECRET"))
            await s.commit()
            ctx = await build_context(s, 1)
        assert "SECRET" not in ctx and "傷" not in ctx and "膝" not in ctx
        await eng.dispose()
    _run(_inner())
