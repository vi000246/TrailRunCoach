"""
SP-740: the 預設背負 is personalised — 進階設定 (手動) > the median of the athlete's own recorded packs of
that kind (單日 / 多日, ≥ MIN_TRIPS) > 4 / 9 kg — and the calculator and the walking-capacity fit (past
hikes without a recorded pack) read the same rule. SP-731 ⑥: the activity list types the 背負 in place
(PATCH /activities pack_kg → the same 「這次背多少」 record). Synthetic data, tmp stores only.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from backend.engine import advanced_params as AP
from backend.engine import calibrate as CAL
from backend.engine.racepower import athlete as A
from backend.engine.racepower import capacity as CAP
from backend.engine.racepower import pack_defaults as PD

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_store(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)
    PD.forget()
    yield
    PD.forget()


def _manual(monkeypatch, **vals):
    monkeypatch.setattr(CAL, "stored_entry",
                        lambda name, user_id=1: {"value": vals[name], "source": "user"} if name in vals else None)


def _hike(i, start, hours, sport_type="hiking"):
    from backend.engine.wko5expr.dataset import date_to_day
    return NS(idx=i, sport="hike" if sport_type != "trail running" else "run", sport_type=sport_type, tags=[],
              day=date_to_day(start.date()),
              metrics={"duration": hours * 3600.0}, entry=NS(file=f"h{i}.fit", start=start))


def _record(i, kg, days=1, sport_type="hiking"):
    hours = 8 if days == 1 else 24 * (days - 1) + 10
    w = _hike(i, dt.datetime(2026, 5, i % 28 + 1, 6, 0), hours, sport_type)
    A.set_hike_meta(w.entry.file, kg, start=w.entry.start, w=w)
    return w


def test_trip_days_by_calendar_day():
    assert A.trip_days(_hike(1, dt.datetime(2026, 5, 1, 6), 8)) == 1
    assert A.trip_days(_hike(1, dt.datetime(2026, 5, 1, 6), 30)) == 2           # 06:00 → next day 12:00
    assert A.trip_days(_hike(1, dt.datetime(2026, 5, 1, 22), 3)) == 2           # crosses midnight
    assert A.trip_days(NS(entry=NS(start=dt.datetime(2026, 5, 1)), metrics={})) == 1


def test_fallback_until_enough_recorded_then_own_median():
    r = PD.resolve(1)
    assert (r["kg"], r["source"], r["n"]) == (4.0, "default", 0)
    assert CAP.pack_default(None, 1) == 4.0 and CAP.pack_default(None, 3) == 9.0
    assert CAP.pack_default_text(None, 1) == "4 kg（單日）"
    _record(1, 5.0)
    _record(2, 7.0)
    assert PD.resolve(1)["source"] == "default" and PD.resolve(1)["n"] == 2       # below MIN_TRIPS (3)
    assert "滿 3 趟" in PD.resolve(1)["note"]
    _record(3, 6.0)
    r = PD.resolve(1)
    assert (r["kg"], r["source"], r["n"]) == (6.0, "own", 3)
    assert r["note"] == "你記過的 3 趟單日，中位數 6 kg"
    assert CAP.pack_default(None, 1) == 6.0
    assert CAP.pack_default_text(None, 1) == "6 kg（你記過的 3 趟單日，中位數）"
    # 多日 is its own kind: still 9 kg with no multi-day record
    assert CAP.pack_default(None, 2) == 9.0 and PD.resolve(2)["source"] == "default"
    for i, kg in ((4, 11.0), (5, 12.0), (6, 14.0), (7, 10.0)):
        _record(i, kg, days=2 + i % 2)
    r = PD.resolve(3)
    assert (r["kg"], r["source"], r["n"]) == (11.5, "own", 4) and r["note"] == "你記過的 4 趟多日，中位數 11.5 kg"
    assert PD.resolve(1)["kg"] == 6.0                                              # 單日 unchanged
    assert "單日 6 kg（你記過的 3 趟，中位數）" in CAP.pack_default_text() and "多日 11.5 kg" in CAP.pack_default_text()


def test_a_run_with_a_vest_never_counts_and_clearing_a_record_counts_again():
    for i, kg in ((1, 2.0), (2, 2.0), (3, 2.0)):
        _record(i, kg, sport_type="trail running")
    assert PD.resolve(1)["source"] == "default" and PD.resolve(1)["n"] == 0
    for i, kg in ((4, 5.0), (5, 5.0), (6, 8.0)):
        _record(i, kg)
    assert PD.resolve(1)["kg"] == 5.0
    A.set_hike_meta("h4.fit", None, start=dt.datetime(2026, 5, 5, 6, 0))
    assert PD.resolve(1)["source"] == "default"                                    # 2 left


def test_manual_setting_wins_and_blank_is_auto(monkeypatch):
    for i, kg in ((1, 5.0), (2, 5.0), (3, 6.0)):
        _record(i, kg)
    _manual(monkeypatch, pack_default_single_kg=3.0)
    r = PD.resolve(1)
    assert (r["kg"], r["source"]) == (3.0, "manual") and CAP.pack_default(None, 1) == 3.0
    assert CAP.pack_default_text(None, 1) == "3 kg（單日，進階設定）"
    assert CAP.pack_default(None, 2) == 9.0                                        # 多日 still automatic
    _manual(monkeypatch, pack_default_multi_kg=12.0)
    assert CAP.pack_default(None, 1) == 5.0 and CAP.pack_default(None, 2) == 12.0   # single back to 自動
    reg = CAL._registry()
    for name, d in (("pack_default_single_kg", 4.0), ("pack_default_multi_kg", 9.0)):
        assert reg[name].manual_only and reg[name].default == d and reg[name].bounds == (0.0, 40.0)


def test_settings_row_shows_the_value_in_effect(monkeypatch):
    row = lambda n: AP.pack_live_describe(CAL.describe(n, None))       # noqa: E731
    r = row("pack_default_single_kg")
    assert r["value"] == 4.0 and r["source"] == "default" and "滿 3 趟" in r["chip"]["tip"]
    for i, kg in ((1, 5.0), (2, 5.0), (3, 6.0)):
        _record(i, kg)
    r = row("pack_default_single_kg")
    assert r["value"] == 5.0 and r["source"] == "fitted" and r["chip"]["text"] == "本人 n=3"
    assert "你記過的 3 趟單日，中位數 5 kg" in r["chip"]["tip"]
    m = AP.pack_live_describe(CAL.describe("pack_default_single_kg", {"value": 2.5, "source": "user"}))
    assert m["value"] == 2.5 and m["source"] == "user"                             # 手動 stays as stored
    assert AP.pack_live_describe({"name": "heat_partial_hadley", "value": 1}) == {"name": "heat_partial_hadley", "value": 1}


def test_calculator_and_fit_read_the_same_default(monkeypatch):
    """The walking-capacity fit (past hikes without a recorded pack) and the calculator's trip without
    a 背負 take the same 預設背負."""
    for i, kg in ((1, 5.0), (2, 7.0), (3, 6.0)):
        _record(i, kg)
    single, multi = _hike(20, dt.datetime(2026, 6, 1, 6), 8), _hike(21, dt.datetime(2026, 6, 8, 6), 30)
    monkeypatch.setattr(A, "grade_samples", lambda *a, **k: [])
    monkeypatch.setattr(A, "thresholds_as_of", lambda *a, **k: {})
    monkeypatch.setattr(A, "hike_hr_windows", lambda ds, hikes, ex=None: (
        [{"trip": 20, "day": 1}, {"trip": 21, "day": 1}, {"trip": 21, "day": 2}], {}))
    monkeypatch.setattr(A, "hike_samples", lambda *a, **k: [])
    monkeypatch.setattr(CAP, "hike_steep_windows", lambda x: [])
    monkeypatch.setattr(CAP, "run_speed_at_aet", lambda *a, **k: {"v": 2.0, "n": 0})
    ds = NS(workouts=[], setting=lambda k, day=None: 60.0)
    x = A.walk_capacity_inputs(ds, dt.date(2026, 7, 1), runs=[], hikes=[single, multi])
    assert x["packs"] == {20: 6.0, 21: 9.0}                       # own single-day median; no multi-day records
    assert x["packs"][20] == CAP.pack_default(None, 1) and x["packs"][21] == CAP.pack_default(None, 2)
    from backend.engine.planning import Event
    assert Event(id="e", name="x", date="2026-08-01", kind="baiyue", days=1).pack == 6.0
    _manual(monkeypatch, pack_default_multi_kg=10.0)
    x = A.walk_capacity_inputs(ds, dt.date(2026, 7, 1), runs=[], hikes=[single, multi])
    assert x["packs"][21] == 10.0 == CAP.pack_default(None, 2)


def test_planner_says_where_the_default_comes_from():
    from backend.tests.test_group_itinerary_sp578 import plan
    p = plan()
    assert p["summary"]["pack_note"]["source"] == "default" and p["summary"]["pack_note"]["short"] == "預設背負"
    for i, kg in ((1, 5.0), (2, 5.0), (3, 6.0)):
        _record(i, kg)
    p = plan()
    s = p["summary"]
    assert s["pack_kg"] == 5.0 and s["pack_src"] == "預設背負 5 kg（你記過的 3 趟單日，中位數）"
    assert s["pack_note"]["short"] == "預設背負：你記過的 3 趟單日，中位數"
    assert plan(pack_kg=12.0)["summary"]["pack_note"] is None
    page = (ROOT / "static" / "racepower.html").read_text(encoding="utf-8")
    assert "s.pack_note?.short" in page and "H.pack_rule" in page
