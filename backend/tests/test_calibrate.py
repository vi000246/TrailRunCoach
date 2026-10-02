"""Per-athlete calibration foundation (engine/calibrate.py, api/calib.py,
settings/repository.py athlete.calib.*; generalize-athlete plan B1).
Synthetic data and in-memory SQLite only."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import calibrate as CAL
from backend.engine import drift_agg as DA
from backend.settings.repository import SettingsRepository, UnknownSetting
from backend.tests.calib_fixtures import assert_self_consistent, linear_rows

TODAY = dt.date(2026, 10, 1)


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


def _item(fit, **kw):
    base = dict(name="t_item", label="測試值", unit="u", default=1.0, default_src="推估", k=10, min_n=5,
                fit=fit, bounds=(0.0, 3.0))
    return CAL.Item(**{**base, **kw})


@pytest.fixture
def registry(monkeypatch):
    reg = dict(CAL._registry())
    monkeypatch.setattr(CAL, "REGISTRY", reg)
    monkeypatch.setattr(CAL, "_load_items", lambda: None)
    return reg


# ---- the maths ---------------------------------------------------------------

def test_shrink_weights_and_bounds():
    it = _item(None)
    assert CAL.shrink(it, None) is None
    assert CAL.shrink(it, CAL.Fit(2.0, 0.2, 4)) is None                  # n < min_n: nothing written
    e = CAL.shrink(it, CAL.Fit(2.0, 0.2, 10))                             # w = 10 / 20
    assert e["w"] == 0.5 and e["value"] == pytest.approx(1.5) and e["se"] == pytest.approx(0.1)
    assert e["source"] == "fitted" and e["personal"] == 2.0
    assert CAL.shrink(it, CAL.Fit(50.0, 1.0, 1000))["value"] == 3.0      # clipped


def test_resolve_and_chip_texts():
    it = _item(None, default_is_literature=True)
    d = CAL.resolve(it, None)
    assert d["value"] == 1.0 and d["source"] == "default"
    assert CAL.chip(it, d)["text"] == "預設（文獻）"
    assert CAL.chip(_item(None), d)["text"] == "預設（推估）"
    f = CAL.resolve(it, {"value": 1.4, "se": 0.1, "n": 12, "w": 0.55, "personal": 1.7, "source": "fitted"})
    c = CAL.chip(it, f)
    assert c["text"] == "本人 n=12" and "1.70" in c["tip"] and "55%" in c["tip"]
    u = CAL.resolve(it, {"value": 0.8, "n": 0, "source": "user"})
    assert u["value"] == 0.8 and CAL.chip(it, u)["text"] == "手動"


def test_run_keeps_manual_and_skips_thin_data(registry):
    registry["a"] = _item(lambda ds, today: CAL.Fit(2.0, 0.1, 20), name="a")
    registry["b"] = _item(lambda ds, today: CAL.Fit(2.0, 0.1, 2), name="b")
    registry["c"] = _item(lambda ds, today: 1 / 0, name="c")
    registry["d"] = _item(lambda ds, today: CAL.Fit(2.0, 0.1, 20), name="d")
    upd, skipped = CAL.run(None, {"d": {"value": 0.5, "source": "user"}}, TODAY)
    assert set(upd) >= {"a"} and upd["a"]["fitted_at"] == TODAY.isoformat()
    assert skipped["b"].startswith("n=2") and skipped["c"].startswith("error") and skipped["d"] == "user"


def test_settings_store_and_calibrate(registry):
    registry["a"] = _item(lambda ds, today: CAL.Fit(2.0, 0.1, 10), name="a")

    async def go():
        s = await _session()
        repo = SettingsRepository(s, 1)
        assert await repo.get("athlete.calib.a") is None
        with pytest.raises(UnknownSetting):
            await repo.get("athlete.calib.nope")
        with pytest.raises(ValueError):
            await repo.set("athlete.calib.a", {"value": "x", "source": "fitted"})
        with pytest.raises(ValueError):
            await repo.set("athlete.calib.a", {"value": None, "source": "user"})
        r = await CAL.calibrate(s, 1, ds=object(), today=TODAY)
        assert "a" in r["written"]
        got = await repo.get("athlete.calib.a")
        assert got["source"] == "fitted" and got["value"] == pytest.approx(1.5)
        await repo.set("athlete.calib.a", {"value": 0.7, "source": "user"})
        await CAL.calibrate(s, 1, ds=object(), today=TODAY)
        assert (await repo.get("athlete.calib.a"))["value"] == 0.7      # a fit never overwrites a manual value
    _run(go())


def test_after_sync_needs_new_activities():
    assert CAL._after_sync("coros", {"status": "ok", "downloaded": 0}) is None
    assert CAL._after_sync("coros", {"status": "error", "downloaded": 3}) is None


# ---- the first item: the AeT heat β (engine/drift_agg.py) -----------------------

def test_aet_heat_beta_is_registered_like_drift_agg():
    it = CAL._registry()["aet_heat_beta"]
    assert (it.default, it.k, it.min_n, it.bounds) == (DA.BETA_DEFAULT, DA.BETA_K, DA.BETA_MIN_N, DA.BETA_BOUNDS)
    # the stored value equals drift_agg's own shrinkage for the same fit
    fit = {"personal": 0.6, "se": 0.2, "n": 24}
    e = CAL.shrink(it, CAL.Fit(0.6, 0.2, 24))
    assert e["value"] == pytest.approx(DA.shrink_beta(fit)["beta"])


def test_aet_heat_beta_self_consistent(monkeypatch):
    """Template (calib_fixtures.py): runs that follow the default 1.0 bpm/°C
    fit back to it within ±1 SE."""
    rows = linear_rows(40, 1.0, x_sd=4.0, noise_sd=2.5, seed=7)
    monkeypatch.setattr(DA, "_beta_rows", lambda ds, today, days=DA.BETA_DAYS: (rows, "pace"))
    e = assert_self_consistent(CAL._registry()["aet_heat_beta"], None, DA.BETA_DEFAULT, se=0.15)
    assert e["n"] == 40


def test_heat_beta_manual_value_wins(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: {"value": 0.4, "source": "user"})
    b = DA.heat_beta(None, TODAY)
    assert b["beta"] == 0.4 and b["src"] == "user" and "手動" in DA.beta_text(b)
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: {"value": 0.4, "source": "fitted"})
    assert DA.heat_beta(None, TODAY)["src"] == "default"                 # a stored fit is display only


def test_calib_api(monkeypatch, registry):
    registry["a"] = _item(lambda ds, today: CAL.Fit(2.0, 0.1, 10), name="a")
    from backend.api import calib as API

    async def go():
        s = await _session()
        lst = await API.list_calibration(db=s)
        a = next(x for x in lst["items"] if x["name"] == "a")
        assert a["source"] == "default" and a["chip"]["text"] == "預設（推估）"
        r = await API.set_manual("a", API.Manual(value=2.5), db=s)
        assert r["source"] == "user" and r["value"] == 2.5
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            await API.set_manual("a", API.Manual(value=9.0), db=s)        # outside the bounds
        with pytest.raises(HTTPException):
            await API.set_manual("zzz", API.Manual(value=1.0), db=s)
        r = await API.clear_manual("a", db=s)
        assert r["source"] == "default"
    _run(go())
