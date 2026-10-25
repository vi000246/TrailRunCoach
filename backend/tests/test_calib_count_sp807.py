"""SP-807: the 校正 list says how many of the athlete's own points a fit below min_n found
(「本人資料 8 筆，滿 10 筆才會自己擬合」), not 0 — without replacing the last good fit, and
without touching a manual value. Synthetic data and in-memory SQLite only."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import calibrate as CAL
from backend.i18n import use_locale
from backend.settings.repository import SettingsRepository

TODAY = dt.date(2026, 10, 20)


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


def _item(fit, **kw):
    base = dict(name="t", label="測試值", unit="u", default=1.0, default_src="推估", k=10, min_n=10,
                fit=fit, bounds=(0.0, 3.0))
    return CAL.Item(**{**base, **kw})


@pytest.fixture
def registry(monkeypatch):
    CAL._registry()                 # the real items register on import: load them before the swap
    reg: dict = {}
    monkeypatch.setattr(CAL, "REGISTRY", reg)
    monkeypatch.setattr(CAL, "_load_items", lambda: None)
    return reg


def test_thin_fit_stores_the_count_when_no_fit_stands(registry):
    registry["a"] = _item(lambda ds, today: CAL.count_only(8), name="a")
    registry["b"] = _item(lambda ds, today: CAL.Fit(2.0, 0.1, 8), name="b")     # a value, but n < min_n
    upd, skipped = CAL.run(None, {}, TODAY)
    for n in ("a", "b"):
        assert upd[n] == {"value": None, "se": None, "n": 8, "fitted_at": TODAY.isoformat(), "source": "default"}
        assert skipped[n] == "n=8<10"
    e = CAL.describe("a", upd["a"])
    assert e["value"] == 1.0 and e["source"] == "default" and e["n"] == 8
    assert "本人資料 8 筆，滿 10 筆才會自己擬合" in e["chip"]["tip"]
    with use_locale("en"):
        tip = CAL.describe("a", upd["a"])["chip"]["tip"]
    assert "8 of your own data points; it fits itself once there are 10" in tip


def test_count_never_replaces_the_last_good_fit_or_a_manual_value(registry):
    registry["f"] = _item(lambda ds, today: CAL.count_only(4), name="f")
    registry["u"] = _item(lambda ds, today: CAL.count_only(4), name="u")
    fitted = {"value": 1.4, "se": 0.1, "n": 12, "personal": 1.7, "w": 0.55, "source": "fitted",
              "fitted_at": "2026-09-01"}
    manual = {"value": 0.8, "se": None, "n": 0, "source": "user", "fitted_at": "2026-09-01"}
    upd, skipped = CAL.run(None, {"f": fitted, "u": manual}, TODAY)
    assert upd == {} and skipped == {"f": "n=4<10", "u": "user"}
    assert CAL.describe("f", fitted)["chip"]["text"] == "本人 n=12"


def test_same_count_is_not_rewritten_and_manual_only_items_are_left_out(registry):
    registry["a"] = _item(lambda ds, today: CAL.count_only(8), name="a")
    registry["m"] = _item(lambda ds, today: None, name="m", manual_only=True)
    prev = {"value": None, "se": None, "n": 8, "fitted_at": "2026-10-13", "source": "default"}
    upd, _skipped = CAL.run(None, {"a": prev}, TODAY)
    assert upd == {}
    upd, _skipped = CAL.run(None, {"a": {**prev, "n": 6}}, TODAY)
    assert upd["a"]["n"] == 8 and "m" not in upd


def test_calibrate_stores_the_count_and_the_list_shows_it(registry):
    registry["a"] = _item(lambda ds, today: CAL.count_only(8), name="a")
    registry["g"] = _item(lambda ds, today: CAL.Fit(2.0, 0.1, 10), name="g")

    async def go():
        s = await _session()
        repo = SettingsRepository(s, 1)
        r = await CAL.calibrate(s, 1, ds=object(), today=TODAY)
        assert r["written"] == ["g"]                                          # a count is not a fit
        got = await repo.get("athlete.calib.a")
        assert got["source"] == "default" and got["value"] is None and got["n"] == 8
        from backend.api import calib as API
        a = CAL.describe("a", got)                                            # what GET /api/v1/calib lists
        assert a["value"] == 1.0 and a["chip"]["text"] == "預設（推估）" and "本人資料 8 筆" in a["chip"]["tip"]
        # 手動指定 then 改回自動 still works on top of a stored count
        await API.set_manual("a", API.Manual(value=2.5), db=s)
        assert (await repo.get("athlete.calib.a"))["n"] == 8
    _run(go())


# ---- every registered item's fit carries the count below its min_n -------------------------

def test_registered_fits_carry_the_count_below_min_n(monkeypatch):
    from backend.engine import drift_agg as DA
    from backend.engine import drift_calib as DC
    from backend.engine import heat_calib as HC
    from backend.engine import interval_calib as IC
    assert DC._p95_fit([800.0] * 7) == CAL.count_only(7)
    assert HC._med_fit([60.0] * 4, HC.RH_MIN_N) == CAL.count_only(4)
    assert HC.fit_beta_rows([{"v": 3.0, "mv": 1, "hour": 7, "hadley": 100 + i} for i in range(5)]) == CAL.count_only(5)
    assert IC.fit_tol_rows([("2026-09-01", 250.0), ("2026-09-20", 255.0)], TODAY) == CAL.count_only(1)
    rows = [{"hr": 140.0, "x": 3.0, "temp_c": 20.0 + i} for i in range(6)]
    monkeypatch.setattr(DA, "_beta_rows", lambda ds, today, days=DA.BETA_DAYS: (rows, "pace"))
    assert DA._calib_fit(None, TODAY) == CAL.count_only(6)


def test_easy_margin_carries_the_count_below_min_points(monkeypatch):
    from types import SimpleNamespace
    from backend.engine import drift_agg as DA
    from backend.engine import threshold_calib as TCAL
    from backend.engine.algorithms import threshold_estimate as TE
    monkeypatch.setattr(DA, "aet_points", lambda ds, today: [])
    monkeypatch.setattr(TE, "aet_aggregate", lambda pts, lthr=None: SimpleNamespace(se=None, n=3))
    assert TCAL.fit_easy_margin(SimpleNamespace(plan=None), TODAY) == CAL.count_only(3)
