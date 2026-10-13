"""
SP-462: switching the 課表 month (九月 → 十月) took ~5 s on the NAS. The plan inputs were cached;
each GET /calendar still redid two things that do not depend on the month:

1. _range_extras' TSS / h per category (overview._tss_per_hour: the session classifier over every
   run of the last 180 days, ≈ 190 ms on the demo data) — the very numbers the cached plan inputs
   already hold (week_plan's tss_per_category + easy_tss, same dataset, same day).
2. _view → plan_store.push_dict → _basis_for read 課表偏好 (plan_prefs.load: one sqlite connection
   per key, ~33 keys) once per session of the whole plan (≈ 4.5 ms × the sessions).

Now the TSS / h comes from the cached inputs of the same dataset and day (computed as before when
none is cached) and the calendar reads 課表偏好 once per request.
"""
import datetime as dt
from types import SimpleNamespace

import pytest

from backend import tenancy
from backend.api import plan_sessions as PSA
from backend.engine import overview as O
from backend.engine import plan_prefs as PP
from backend.engine import plan_store as PS
from backend.tests.test_plan_store import API, Env
from backend.tests.test_stale_calendar_sp362 import _fresh_state  # noqa: F401 — module state reset

DAY = dt.date(2026, 9, 30)
CAL = f"{API}/calendar?start=2026-09-28&end=2026-11-01"


def _ds():
    ds = SimpleNamespace(today=float(O.date_to_day(DAY)))
    return ds


def test_the_tss_rates_come_from_the_cached_inputs_of_the_same_dataset_and_day(monkeypatch):
    ds = _ds()
    key = (*tenancy.ds_key(ds), DAY, (), 1.0, "prefs")
    tph = {"road": 61.0, "trail": 55.0, "hike": 40.0, "strength": 30.0, "easy": 52.0, "easy_trail": 50.0}
    info = {"n": 12, "estimated": False, "rate": 52.0, "source": "x"}
    PSA._cache[key] = {"cur": {"tss_per_category": tph, "easy_tss": info}}
    monkeypatch.setattr(O, "_tss_per_hour", lambda *a, **k: pytest.fail("recomputed the TSS / h"))
    got = PSA._dataset_tph(ds, DAY)
    assert got == {**tph, "easy_info": info}
    got["road"] = 0.0                                              # a copy: the cached inputs stay
    assert PSA._cache.get(key)["cur"]["tss_per_category"]["road"] == 61.0


def test_another_day_or_dataset_computes_them(monkeypatch):
    ds = _ds()
    PSA._cache[(*tenancy.ds_key(ds), DAY - dt.timedelta(days=1), ())] = {"cur": {"tss_per_category": {"road": 1.0}}}
    PSA._cache[(*tenancy.ds_key(_ds()), DAY, ())] = {"cur": {"tss_per_category": {"road": 2.0}}}  # other dataset
    monkeypatch.setattr(O, "_tss_per_hour", lambda d, t, *a, **k: {"road": 3.0, "computed": (d is ds, t)})
    assert PSA._dataset_tph(ds, DAY) == {"road": 3.0, "computed": (True, DAY)}


def test_the_calendar_reads_the_preferences_once_not_per_session(monkeypatch):
    monkeypatch.setattr(PSA, "_range_extras", lambda a, b: {"activities": [], "phases": []})
    calls = {"n": 0}
    real = PP.load

    def counted(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    with Env(monkeypatch) as e:
        assert e.c.get(CAL).status_code == 200                     # initialises the stored plan
        monkeypatch.setattr(PP, "load", counted)
        r = e.c.get(CAL)
        assert r.status_code == 200
        n_sessions = sum(1 for s in r.json()["sessions"] if s["state"] == "active")
        assert n_sessions >= 5
        assert calls["n"] <= 2, f"課表偏好 read {calls['n']} times for {n_sessions} sessions"


def test_push_dict_takes_the_preferences_it_is_given(monkeypatch):
    s = {"uid": "u1", "week_start": "2026-09-28", "kind": "easy", "title": "輕鬆跑", "minutes": 40,
         "state": "active", "day": "2026-10-01"}
    monkeypatch.setattr(PP, "load", lambda *a, **k: pytest.fail("read the preferences again"))
    prefs = PP.Prefs()
    assert PS.push_dict(s, prefs)["basis"] == PS.push_dict(s, None)["basis"]
