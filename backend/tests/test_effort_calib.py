"""功率與比賽判定 per athlete (generalize-athlete plan B5): full-effort trail
minimum and long-rest share (engine/effort_calib.py), target basis auto for
a runner without Stryd, the COROS LTHR as prior without hard runs. Synthetic."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from backend.engine import calibrate as CAL
from backend.engine import effort_calib as EC

TODAY = dt.date(2026, 10, 1)


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)


def _ds(n, km=21.0, mins=180.0, rest=0.03):
    from backend.engine.wko5expr.dataset import date_to_day
    ws = [SimpleNamespace(idx=i, sport="run", sport_type="trail running", tags=["runningtrail"],
                          day=date_to_day(TODAY - dt.timedelta(days=30 * i + 1)),
                          metrics={"distance": km + i}, entry=SimpleNamespace(start=None, file=f"f{i}"))
          for i in range(n)]
    return SimpleNamespace(workouts=ws), {w.idx: {"moving_s": (mins + 10 * w.idx) * 60, "rest_share": rest + 0.01 * w.idx}
                                          for w in ws}


@pytest.fixture
def races(monkeypatch):
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import athlete as A

    def make(n, **kw):
        ds, stats = _ds(n, **kw)
        monkeypatch.setattr(AT, "load", lambda *a, **k: [{"x": 1}])
        monkeypatch.setattr(AT, "user_of", lambda w, rows=None: {"effort": "max"})
        monkeypatch.setattr(AT, "user_effort", lambda u: (u or {}).get("effort"))
        monkeypatch.setattr(A, "maximal_stats", lambda ds_, w: stats[w.idx])
        return ds
    return make


def test_defaults_without_full_efforts():
    assert EC.trail_min_km() == 10.0 and EC.trail_min_s() == 90 * 60.0 and EC.rest_max() == 0.10


def test_fits_need_three_races(races):
    item = CAL._registry()["trail_max_min_km"]
    assert CAL.shrink(item, item.fit(races(2), TODAY)) is None
    e = CAL.shrink(item, item.fit(races(6, km=25.0), TODAY))
    assert e is not None and 10.0 < e["value"] < 26.0                 # p10 ≈ 25.5, shrunk toward 10 (k 3)
    r = CAL._registry()["effort_rest_max"]
    e = CAL.shrink(r, r.fit(races(6, rest=0.02), TODAY))             # rest 2–7 %: p90 × 1.5 ≈ 9.75 %
    assert e is not None and 0.05 < e["value"] < 0.11


def test_rules_read_the_value_in_effect(monkeypatch):
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import maximal as MX
    vals = {"trail_max_min_km": 25.0, "trail_max_min_min": 150.0, "effort_rest_max": 0.05}
    monkeypatch.setattr(CAL, "value", lambda name, user_id=1: vals[name])
    r = MX.trail_maximal({"km": 21.0, "moving_s": 160 * 60, "hr_avg": 160, "above_aet": 0.9}, 170, 150, "某某越野賽")
    assert r["ok"] is False and "≥ 25" in " ".join(c.get("text", "") for c in r["checks"])
    o = AT.effort_hr({"hr_avg": 165, "above_aet": 0.9, "moving_s": 3600, "elapsed_s": 4000, "rest_share": 0.08},
                     170, 150)
    assert o["effort"] == "hard_with_rests" and "5%" in o["reason"]


def test_target_basis_auto_without_stryd(monkeypatch):
    from backend.engine import target_policy as TP
    from backend.engine import planning as PL
    easy = {"kind": "easy", "title": "輕鬆跑"}
    th = {"cp": 250, "aet": 150, "lthr": 170}
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, path=None: PL.Plan(profile={"power_source": "stryd"})))
    assert TP.target_policy(easy, None, th)["basis"] == "power"
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, path=None: PL.Plan(profile={"power_source": "none"})))
    p = TP.target_policy(easy, None, th)
    assert p["basis"] == "hr" and "Stryd" in p["fallback"]
    from backend.engine import plan_prefs as PP
    assert TP.target_policy(easy, PP.Prefs(target_basis="power"), th)["basis"] == "power"   # an explicit choice wins


def test_coros_lthr_is_the_prior_without_hard_runs(tmp_path, monkeypatch):
    from backend.engine.wko5expr import datasource
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.test_fit_dataset_prereqs import _db, _fits
    root, _ = _fits(tmp_path, 1)
    db = _db(tmp_path, [], settings=[("2026-09-01", None, 66.0, 168, None, None)])
    monkeypatch.setattr(datasource, "_db_path", lambda: db)
    ds = FitFolderDataset(root, config=EngineConfig(parity=True), today=dt.date(2026, 9, 30))
    from backend.engine.wko5expr.dataset import date_to_day
    assert ds.setting("runthr", date_to_day(dt.date(2026, 9, 20))) == pytest.approx(168.0)
    assert ds.setting_label("runthr").startswith("來自手錶")
