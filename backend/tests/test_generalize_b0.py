"""generalize-athlete plan B0: what broke or misbehaved for a runner without
the original single-user setup — the parity default without WKO5, the empty athlete row,
the hard-coded athlete id and the W′ prior by sex. Synthetic data only."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from types import SimpleNamespace

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import cp_protocols as CPP
from backend.engine.planning import Plan


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def _wko5_folder(tmp_path):
    d = tmp_path / "wko5"
    d.mkdir()
    (d / "Someone.wko5athlete").write_bytes(b"")
    return d


# ---- parity default ------------------------------------------------------------

def test_parity_default_follows_the_wko5_folder(tmp_path, monkeypatch):
    from backend.engine.wko5expr.config import EngineConfig
    cfg = tmp_path / "engine.json"
    monkeypatch.delenv("WKO5_ATHLETE_DIR", raising=False)
    assert EngineConfig.load(cfg).parity is False                   # no WKO5, no file
    cfg.write_text(json.dumps({"use_tp_tss": False}), "utf-8")
    assert EngineConfig.load(cfg).parity is False and EngineConfig.load(cfg).use_tp_tss is False
    monkeypatch.setenv("WKO5_ATHLETE_DIR", str(_wko5_folder(tmp_path)))
    assert EngineConfig.load(cfg).parity is True                    # the original setup: unchanged
    cfg.write_text(json.dumps({"parity": False}), "utf-8")
    assert EngineConfig.load(cfg).parity is False                   # an explicit choice wins


def test_wko5_dataset_without_a_folder_says_so(tmp_path):
    from backend.engine.wko5expr.dataset import Dataset
    from backend.engine.wko5expr.config import EngineConfig
    with pytest.raises(FileNotFoundError):
        Dataset(tmp_path / "none", config=EngineConfig(parity=True))


def test_plan_api_without_wko5(tmp_path, monkeypatch):
    from backend.api import plan as PA
    monkeypatch.setattr(PA, "ATHLETE_DIR", tmp_path / "none")
    PA._wko5_settings.cache_clear()
    PA._wko5_profile.cache_clear()
    try:
        assert PA._wko5_settings()["runthr"] is None and PA._wko5_settings()["mftp"] is None
        assert PA._wko5_profile() == {"weights": [], "height_cm": None, "sex": None}
    finally:
        PA._wko5_settings.cache_clear()
        PA._wko5_profile.cache_clear()


# ---- the athlete row --------------------------------------------------------------

def _session():
    async def mk():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from backend.db.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)()
    return mk


def test_ensure_athlete_and_current_id(monkeypatch):
    from backend.db import current as CUR
    from backend.db.models import Athlete
    from sqlalchemy import select

    async def go():
        s = await _session()()
        assert await CUR.ensure_athlete(s) is True
        await s.commit()
        assert await CUR.ensure_athlete(s) is False                # idempotent
        rows = (await s.execute(select(Athlete))).scalars().all()
        assert [(a.id, a.name) for a in rows] == [(1, "athlete")]
        monkeypatch.setenv("WKO5COACH_ATHLETE_ID", "3")
        assert CUR.current_athlete_id() == 3
        assert await CUR.ensure_athlete(s) is True
        await s.commit()
        assert sorted(a.id for a in (await s.execute(select(Athlete))).scalars().all()) == [1, 3]
    _run(go())
    monkeypatch.setenv("WKO5COACH_ATHLETE_ID", "x")
    assert CUR.current_athlete_id() == 1


def test_startup_creates_the_athlete_row_also_with_wko5(tmp_path, monkeypatch):
    """main._ensure_athlete (was POST /athletes/bootstrap): one row on first start,
    pointing at the WKO5 athlete folder when there is one; idempotent."""
    import backend.db.database as DB
    from backend import main as M
    from backend.db.models import Athlete
    from sqlalchemy import select
    monkeypatch.delenv("WKO5COACH_ATHLETE_ID", raising=False)
    monkeypatch.setenv("WKO5_ATHLETE_DIR", str(_wko5_folder(tmp_path)))

    async def go():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Athlete.metadata.create_all)
        monkeypatch.setattr(DB, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False))
        await M._ensure_athlete()
        await M._ensure_athlete()
        async with DB.AsyncSessionLocal() as s:
            rows = (await s.execute(select(Athlete))).scalars().all()
        assert [(a.id, a.data_dir) for a in rows] == [(1, str(tmp_path / "wko5"))]
        await engine.dispose()
    _run(go())


# ---- W′ prior by sex --------------------------------------------------------------

def _ds(sex=None, wko5_sex=None):
    plan = Plan(profile={"sex": sex} if sex else {})
    root = {3001: {3017: wko5_sex}} if wko5_sex else {}
    return SimpleNamespace(plan=plan, athlete=SimpleNamespace(root=root))


def test_athlete_sex_profile_then_wko5():
    assert CPP.athlete_sex(_ds("female", "male")) == "female"
    assert CPP.athlete_sex(_ds(None, "female")) == "female"
    assert CPP.athlete_sex(_ds()) is None
    assert CPP.athlete_sex(SimpleNamespace()) is None


def test_wprime_prior_by_sex():
    assert CPP.wprime_prior("male")[:2] == (13100.0, 4000.0)
    assert CPP.wprime_prior("female")[:2] == (6400.0, 2200.0)
    w, sd, label = CPP.wprime_prior(None)
    assert (w, sd) == (13100.0, 4000.0) and "未填性別" in label and "推估" in label


def test_battery_uses_the_athletes_sex():
    from backend.engine import interval_eval as IE
    t = np.arange(600.0)
    s = {"t": t, "power": np.full(600, 300.0)}
    male = IE.battery(_ds("male"), None, s, 250.0)
    female = IE.battery(_ds("female"), None, s, 250.0)
    unknown = IE.battery(_ds(), None, s, 250.0)
    assert male["wprime_j"] == unknown["wprime_j"] == 13100.0         # the long-standing default
    assert female["wprime_j"] == 6400.0 and "女" in female["wprime_src"]


def test_cp_test_single_bout_prior_by_sex():
    from backend.engine import workout_review as WR
    # a 12′ bout at 250 W and a later 3′ at 240 W (not all-out): 1pt_prior
    p = np.concatenate([np.full(720, 250.0), np.full(900, 100.0), np.full(180, 240.0)])
    res = WR.cp_test(np.arange(len(p), dtype=float), p)
    assert res["method"] == "1pt_prior" and res["cp"] == pytest.approx(250 - 13100 / 720)
    assert WR.cp_test_for_sex(res, "male") is res and WR.cp_test_for_sex(res, None) is res
    f = WR.cp_test_for_sex(res, "female")
    assert f["cp"] == pytest.approx(250 - 6400 / 720) and f["wprime"] == 6400.0 and "6.4 kJ" in f["note"]


# ---- routes without WKO5: tracks from the synced FITs ------------------------------

def test_routes_read_fit_tracks_without_wko5(tmp_path, monkeypatch):
    from backend.api import routes as RA
    from backend.engine.wko5expr import datasource
    from backend.files.wko4_file import Channel, Wko4File
    (tmp_path / "coros").mkdir()
    (tmp_path / "coros" / "a.fit").write_bytes(b"x")
    n = 900
    ch = {"elapsedtime": Channel("elapsedtime", [float(i) for i in range(n)], 1.0, base=0.0),
          "latitude": Channel("latitude", [46.5 + i * 2e-5 for i in range(n)], 1.0),
          "longitude": Channel("longitude", [8.0 + i * 2e-5 for i in range(n)], 1.0),
          "elapseddistance": Channel("elapseddistance", [i * 0.0028 for i in range(n)], 1.0),
          "heartrate": Channel("heartrate", [140.0] * n, 1.0)}
    wf = Wko4File(path="a.fit", sport="running", start_time="2026-09-01T06:00:00", device=None, weight_kg=None,
                  original_type="fit", original_bytes=None, channels=ch, ranges=[], info=None)
    w = SimpleNamespace(idx=0, sport="run", sport_type="running", metrics={"climbing": 0},
                        entry=SimpleNamespace(file="coros/a.fit", start=dt.datetime(2026, 9, 1, 6)))
    ds = SimpleNamespace(dir=tmp_path, workouts=[w], corrections=None, wko4=lambda i: wf,
                         sport_setting=lambda name, w_: 170.0)
    monkeypatch.setattr(datasource, "wko5_available", lambda d=None: False)
    monkeypatch.setattr(RA, "_ds", lambda: ds)
    rows, reader = RA._source()
    assert [r[0] for r in rows] == ["coros/a.fit"]
    tr = reader(*rows[0])
    assert tr is not None and tr["file"] == "coros/a.fit"
