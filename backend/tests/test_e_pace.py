"""E pace from a race result (SP-276; engine/e_pace.py). Synthetic data only — never the
WKO5 folder, the app DB or ~/.wko5coach."""
import datetime as dt

import pytest

from backend.engine import e_pace as EP
from backend.engine.planning import Event, Plan
from backend.i18n import use_locale
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 6)


def _s(p: str) -> int:
    m, s = p.split(":")
    return int(m) * 60 + int(s)


# ---- VDOT ----------------------------------------------------------------------------------

def test_10k_45_matches_the_vdot_table_within_5_s():
    vd = EP.vdot(10000, 45 * 60)
    assert vd == pytest.approx(45.3, abs=0.1)
    fast, slow = EP.e_range(vd)
    # Daniels' table (4th ed. via sport-calculator.com), VDOT 45: E 5:34–6:08 /km
    assert abs(fast - _s("5:34")) <= 5 and abs(slow - _s("6:08")) <= 5


@pytest.mark.parametrize("vd, fast, slow", [(30, "7:39", "8:23"), (40, "6:07", "6:44"), (50, "5:07", "5:38"),
                                            (60, "4:25", "4:52"), (70, "3:54", "4:18")])
def test_e_range_follows_the_daniels_table(vd, fast, slow):
    f, s = EP.e_range(vd)
    assert abs(f - _s(fast)) <= 2 and abs(s - _s(slow)) <= 2


def test_race_equivalents_give_the_same_vdot():
    # Daniels' race-equivalent table, VDOT 50: 5 K 19:57, 10 K 41:21, half 1:31:35
    for d, t in ((5000, 19 * 60 + 57), (10000, 41 * 60 + 21), (21097.5, 91 * 60 + 35)):
        assert EP.vdot(d, t) == pytest.approx(50.0, abs=0.3)


def test_bad_inputs():
    assert EP.vdot(0, 100) is None and EP.vdot(10000, 0) is None
    assert EP.parse({"distance_m": 10000, "time_s": 600, "date": "2026-09-01"}) is None     # 1:00 /km
    assert EP.parse({"distance_m": 800, "time_s": 150, "date": "2026-09-01"}) is None       # too short
    assert EP.parse({"distance_m": 10000, "time_s": 2700, "date": "nope"}) is None
    assert EP.parse({"distance_m": 10000, "time_s": 2700, "date": "2026-09-01", "source": "x"})["source"] == "manual"


# ---- 180 days ------------------------------------------------------------------------------

def test_older_than_180_days_is_stale():
    fresh = EP.of_race({"distance_m": 10000, "time_s": 2700, "date": (TODAY - dt.timedelta(days=180)).isoformat()}, TODAY)
    old = EP.of_race({"distance_m": 10000, "time_s": 2700, "date": (TODAY - dt.timedelta(days=181)).isoformat()}, TODAY)
    assert not fresh["stale"] and old["stale"] and old["age_days"] == 181
    assert "舊了" in EP.label(old) and "舊了" not in EP.label(fresh)
    assert EP.label(fresh) == "E 配速 5:32–6:06 /km（10 K 45:00，VDOT 45.3）"
    with use_locale("en"):
        assert EP.label(fresh) == "E pace 5:32–6:06 /km (10 K in 45:00, VDOT 45.3)"
        assert "old (over 180 days, estimate)" in EP.label(old)


# ---- candidates: confirm first, no trail races ---------------------------------------------

def _w(day, title, km, secs, climb=0.0, tags=None):
    return FakeWorkout(dt.datetime.combine(day, dt.time(7)), title=title, tags=tags or [],
                       metrics={"distance": km, "duration": secs, "climbing": climb})


def _ds(ws, plan=None):
    ds = FakeDataset(ws, TODAY)
    ds.plan = plan or Plan()
    return ds


def test_road_races_are_offered_trail_races_are_not():
    d = TODAY - dt.timedelta(days=20)
    ws = [_w(d, "台北 10K 路跑賽", 10.0, 2700),
          _w(d - dt.timedelta(days=7), "合歡山越野賽", 21.0, 3 * 3600, climb=1200),              # climb
          _w(d - dt.timedelta(days=14), "Trail race 15K", 15.0, 6000, tags=["runningtrail"]),   # tag
          _w(d - dt.timedelta(days=21), "週末河濱", 10.0, 3300),                                 # not a race
          _w(d - dt.timedelta(days=28), "比賽日", 21.1, 7200)]                                   # plan trail race
    plan = Plan()
    plan.events.append(Event(id="t", name="trail", date=(d - dt.timedelta(days=28)).isoformat(), kind="race"))
    plan.events.append(Event(id="r", name="road", date=(d - dt.timedelta(days=35)).isoformat(), kind="road"))
    ws.append(_w(d - dt.timedelta(days=35), "晨跑", 21.0975, 5700))                            # plan road race
    c = EP.candidates(_ds(ws, plan), TODAY)
    assert [x["title"] for x in c] == ["台北 10K 路跑賽", "晨跑"]
    assert c[0]["vdot"] == pytest.approx(45.3, abs=0.1) and c[0]["source"] == "activity"


def test_an_unconfirmed_candidate_is_not_used(monkeypatch):
    from backend.engine.wko5expr import datasource
    store = {}
    monkeypatch.setattr(datasource, "read_setting", lambda k, d=None, *a: store.get(k, d))
    ds = _ds([_w(TODAY - dt.timedelta(days=5), "10K race", 10.0, 2700)])
    assert EP.candidates(ds, TODAY) and EP.current(TODAY) is None          # offered, not used
    c = EP.candidates(ds, TODAY)[0]
    store[EP.RACE_KEY] = {k: c[k] for k in ("distance_m", "time_s", "date", "source", "title")}   # confirmed
    assert EP.current(TODAY)["vdot"] == c["vdot"]


def test_setting_validation():
    from backend.settings.repository import DEFAULTS, validate
    assert DEFAULTS[EP.RACE_KEY] is None
    validate(EP.RACE_KEY, None)
    validate(EP.RACE_KEY, {"distance_m": 10000, "time_s": 2700, "date": "2026-09-01", "source": "manual"})
    with pytest.raises(ValueError):
        validate(EP.RACE_KEY, {"distance_m": 10000, "time_s": 100, "date": "2026-09-01"})


def test_race_pace_api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import plan as API
    from backend.db.database import get_db
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import make_db, run
    db = run(make_db(logged_in=False))

    async def fake_db():
        yield db
    stored = {}
    monkeypatch.setattr(EP, "stored", lambda user_id=1: EP.parse(stored.get("v")))
    monkeypatch.setattr(API, "_estimate_dataset", lambda: _ds([_w(TODAY - dt.timedelta(days=5), "10K race", 10.0, 2700)]))
    monkeypatch.setattr(API, "today_local", lambda *a, **k: TODAY)
    monkeypatch.setattr(API, "_notify", lambda thresholds: None)
    app = FastAPI()
    app.include_router(API.router)
    app.dependency_overrides[get_db] = fake_db
    c = TestClient(app)
    r = c.get("/api/v1/plan/race-pace").json()
    assert r["current"] is None and len(r["candidates"]) == 1 and r["stale_days"] == 180
    for bad in ({"distance_km": 10, "time_s": 60, "date": "2026-09-01"},
                {"distance_km": 10, "time_s": 2700, "date": "2026-10-07"}):        # after today
        assert c.put("/api/v1/plan/race-pace", json=bad).status_code == 400
    assert c.put("/api/v1/plan/race-pace", json={"distance_km": 10, "time_s": 2700, "date": "2026-09-01"}).status_code == 200
    stored["v"] = run(SettingsRepository(db).get(EP.RACE_KEY))
    assert stored["v"] == {"distance_m": 10000.0, "time_s": 2700.0, "date": "2026-09-01", "source": "manual"}
    r = c.get("/api/v1/plan/race-pace").json()
    assert r["current"]["e_fast"] == 332 and r["label"].startswith("E 配速 5:32–6:06")
    assert c.put("/api/v1/plan/race-pace", json={"clear": True}).status_code == 200
    assert run(SettingsRepository(db).get(EP.RACE_KEY)) is None
