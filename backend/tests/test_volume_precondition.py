"""The stable-volume precondition (base_check.volume_stable, 推估): a 90-min test
or an AeT test only counts when the weekly running time was steady (±15 %) for
the 3 counted weeks before it; the AeT-test suggestion waits until then.
Synthetic data only — never the WKO5 folder, the app DB or ~/.wko5coach."""
import datetime as dt

import pytest

from backend.engine import aet_test as AT
from backend.engine import base_check as BC
from backend.engine import quality_gate as QG
from backend.tests.test_quality_gate import TODAY, _ds


def _weeks(minutes, end=TODAY):
    """weekly() rows for the weeks before `end`'s week, oldest first."""
    m0 = BC.monday(end) - dt.timedelta(weeks=len(minutes))
    return [{"monday": (m0 + dt.timedelta(weeks=i)).isoformat(), "z1_s": m * 60.0, "run_s": m * 60.0,
             "complete": True} for i, m in enumerate(minutes)]


@pytest.fixture
def weekly(monkeypatch):
    rows = {"v": []}
    monkeypatch.setattr(BC, "weekly", lambda ds, today, n: rows["v"][-n:])
    return rows


def test_steady_three_weeks_pass_and_one_big_week_fails(weekly):
    weekly["v"] = _weeks([100, 300, 200, 210, 190])          # only the last 3 weeks count
    v = BC.volume_stable(_ds([]), TODAY)
    assert v["ok"] is True and v["mean_min"] == pytest.approx(200.0) and len(v["weeks"]) == 3
    assert "推估" in v["src"] and "±15%" in v["label"]
    weekly["v"] = _weeks([200, 200, 260])                     # +24 % against the mean of 220
    v = BC.volume_stable(_ds([]), TODAY)
    assert v["ok"] is False and v["worst"] > BC.VOL_TOL and "最大差" in v["value"]


def test_no_running_is_not_a_verdict(weekly):
    weekly["v"] = _weeks([0, 0, 0])
    assert BC.volume_stable(_ds([]), TODAY)["ok"] is None
    weekly["v"] = _weeks([200])                               # fewer than 3 weeks of history
    assert BC.volume_stable(_ds([]), TODAY)["ok"] is None


def test_recovery_weeks_are_skipped(weekly, monkeypatch):
    weekly["v"] = _weeks([200, 210, 120, 190])                # the 120-min week is a 3:1 down week
    down = weekly["v"][2]["monday"]
    monkeypatch.setattr(BC, "_skip_week", lambda ds, mon, brk: mon.isoformat() == down)
    v = BC.volume_stable(_ds([]), TODAY)
    assert v["ok"] is True and [w["min"] for w in v["weeks"]] == [200, 210, 190]


@pytest.fixture
def xu_pass(monkeypatch):
    monkeypatch.setattr(BC, "xu_runs", lambda ds, today, days=182: [
        {"idx": 0, "date": "2026-08-01", "ok": True, "drift": 0.06, "hr10": 128.0, "hr90": 135.7, "why": []}])
    monkeypatch.setattr(BC, "maintenance", lambda ds, today, since, brk=None: {"ok": True, "why": ""})


def test_a_test_after_unsteady_weeks_does_not_confirm(xu_pass, monkeypatch):
    monkeypatch.setattr(BC, "volume_stable", lambda ds, day: {"ok": False, "value": "100 / 200 / 300 分"})
    z = BC.z5_status(_ds([]), TODAY, "auto")
    assert z["state"] == "unconfirmed" and not z["open"] and "週量不穩定" in z["reason"]
    assert z["vol_skipped"] == [{"date": "2026-08-01", "path": "xu90"}]
    monkeypatch.setattr(BC, "volume_stable", lambda ds, day: {"ok": True})
    z = BC.z5_status(_ds([]), TODAY, "auto")
    assert z["state"] == "confirmed" and z["since"] == "2026-08-01"
    monkeypatch.setattr(BC, "volume_stable", lambda ds, day: {"ok": None})     # no data: no block
    assert BC.z5_status(_ds([]), TODAY, "auto")["state"] == "confirmed"


def test_the_latest_test_with_steady_weeks_counts(monkeypatch):
    monkeypatch.setattr(BC, "xu_runs", lambda ds, today, days=182: [
        {"idx": 0, "date": "2026-08-01", "ok": True, "drift": 0.06, "hr10": 128.0, "hr90": 135.7, "why": []},
        {"idx": 1, "date": "2026-09-10", "ok": True, "drift": 0.05, "hr10": 128.0, "hr90": 134.4, "why": []}])
    monkeypatch.setattr(BC, "maintenance", lambda ds, today, since, brk=None: {"ok": True, "why": ""})
    monkeypatch.setattr(BC, "volume_stable", lambda ds, day: {"ok": day.isoformat() == "2026-08-01"})
    z = BC.z5_status(_ds([]), TODAY, "auto")
    assert z["state"] == "confirmed" and z["since"] == "2026-08-01"


def test_the_aet_test_suggestion_waits_for_steady_volume(monkeypatch):
    ds = _ds([])
    ae = {"value": 140.0, "measured": False, "validity": {"valid": False, "reason": "還不夠準"}}
    z5 = {"state": "unconfirmed"}
    monkeypatch.setattr(BC, "volume_stable", lambda ds, day: {"ok": False, "value": "100 / 200 / 300 分"})
    r = QG.aet_test_reason(ds, TODAY, ae, z5)
    assert r["code"] == "no_data" and r["wait"] and "先讓週量穩定 3 週" in r["text"]
    assert not AT.due(TODAY, "base", None, r, None)
    monkeypatch.setattr(BC, "volume_stable", lambda ds, day: {"ok": True, "value": "200 / 200 / 200 分"})
    r = QG.aet_test_reason(ds, TODAY, ae, z5)
    assert not r.get("wait") and r["volume"]["ok"] and AT.due(TODAY, "base", None, r, None)


def test_the_card_shows_the_precondition_while_a_test_is_needed():
    vol = {"ok": False, "label": "前提：週量穩定 3 週（每週在平均 ±15% 內）", "value": "100 / 200 / 300 分",
           "need": "±15%（推估）", "src": BC.SRC_VOL}
    gate = {"z5": {"state": "unconfirmed", "path": None}, "mode": "auto", "aet": {}, "lthr": {}, "dose": {},
            "volume": vol, "options": {}}
    c = QG.z5_card(gate, TODAY)
    assert c["base"]["pre"]["key"] == "volume" and c["base"]["pre"]["ok"] is False
    assert "先讓週量穩定 3 週" in c["next"]["text"]
    done = QG.z5_card({**gate, "z5": {"state": "confirmed", "path": "xu90", "since": "2026-08-01"}}, TODAY)
    assert done["base"]["pre"] is None
