"""The demo athlete's generator (backend/demo/, auth-and-demo.plan.md §3.2).
Synthetic only: everything is written to tmp_path — never the WKO5 folder,
the app DB or ~/.wko5coach."""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from backend.demo import season as S
from backend.demo.generate import build_activity, generate, _trip_legs

ANCHOR = dt.date(2026, 10, 3)
SEED = 20261002


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo_small")
    return root, generate(root, seed=SEED, anchor=ANCHOR, small=True)


def test_deterministic(small, tmp_path):
    root, m = small
    again = generate(tmp_path / "a", seed=SEED, anchor=ANCHOR, small=True)
    assert again["files"] == m["files"]
    other = generate(tmp_path / "b", seed=SEED + 1, anchor=ANCHOR, small=True)
    assert other["files"] != m["files"]
    json.dumps(m)                                       # the manifest is JSON-able


def test_manifest_and_showcase(small):
    root, m = small
    assert m["n_activities"] == len(m["activities"]) > 20
    for k in ("interval", "trail_long", "easy", "lsd", "cp_test", "aet_test", "baiyue"):
        assert k in m["showcase"], k
        assert (root / m["showcase"][k]).is_file()
    assert sum(1 for a in m["activities"] if a["showcase"] and a["kind"] == "baiyue") == 3
    show = [a for a in m["activities"] if a["showcase"] and a["kind"] != "baiyue"]
    assert len({a["kind"] for a in show}) == len(show)              # one each
    last = max(dt.date.fromisoformat(a["date"]) for a in m["activities"])
    assert 1 <= (ANCHOR - last).days <= 2
    for a in m["activities"]:
        assert a["start_utc"].endswith("Z") and a["file"].startswith("fit/coros/")
    for name, rel in m["courses"].items():
        txt = (root / rel).read_text("utf-8")
        assert "<trkpt" in txt and "虛構" in txt


def test_plan_json(small):
    root, _m = small
    from backend.engine.planning import Plan
    plan = Plan.load(root / "plan.json")
    names = {e.id for e in plan.events}
    assert {"demo-half", "demo-baiyue", "demo-50k"} <= names
    a50 = next(e for e in plan.events if e.id == "demo-50k")
    assert a50.start > ANCHOR                                       # the A race is still ahead
    ph = sorted(plan.phases, key=lambda p: p.start)
    for p in ph:
        assert p.start <= p.end
    for p, q in zip(ph, ph[1:]):
        assert p.end < q.start                                       # no overlap
    on = [p for p in ph if p.start <= ANCHOR.isoformat() <= p.end]
    assert on and on[0].kind == "specific" and on[0].event_id == "demo-50k"
    cps = [t.cp for t in sorted(plan.thresholds, key=lambda t: t.date)]
    assert cps == [240.0, 255.0, 260.0]
    assert plan.weight_on(ANCHOR) == 62.0 and plan.profile["sex"] == "male"


def test_full_year_schedule_statistics():
    """The year's plan (no files): sessions per week, Stryd share, phases."""
    s = S.schedule(SEED, ANCHOR, 52)
    assert 230 <= len(s.activities) <= 320
    normal = [w for w in s.weeks if w["type"] == "normal"]
    assert len(normal) > 25
    assert all(4 <= w["n"] <= 6 for w in normal), [w for w in normal if not 4 <= w["n"] <= 6]
    rec = [w for w in s.weeks if w["type"] == "recovery"]
    assert rec and all(w["n"] <= 5 for w in rec)
    assert any(w["type"] == "gap" for w in s.weeks)                 # the 感冒 gap
    runs = [a for a in s.activities if a.sport != "hike"]
    assert sum(a.stryd for a in runs) / len(runs) >= 0.85
    assert sum(not a.stryd for a in runs) >= 5                       # some watch-power-only runs
    kinds = Counter(a.kind for a in s.activities)
    assert kinds["baiyue"] >= 3 + 2 * 2 + 3 and kinds["race_half"] == 1
    show = [a for a in s.activities if a.showcase]
    assert all(a.date >= ANCHOR - dt.timedelta(days=21) for a in show)


def test_signals_plausible(small):
    root, m = small
    from backend.files.fit_to_channels import fit_to_channels
    stryd = 0
    for a in m["activities"]:
        ch = fit_to_channels((root / a["file"]).read_bytes())
        hr = np.array([v for v in ch.channels["heartrate"] if v is not None], float)
        assert len(hr) and 90 <= hr.min() and hr.max() <= 195, a["file"]
        assert ch.channels.get("power") and ch.channels.get("latitude") and ch.channels.get("elevation")
        stryd += ch.power_source == "stryd"
        assert ch.power_source == ("stryd" if a["stryd"] else "watch")
        if a["kind"] == "trail_long":
            assert ch.sub_sport == "trail"
        if a["sport"] == "hike":
            assert ch.sport == "hiking"
    assert stryd / len(m["activities"]) >= 0.85


def test_baiyue_days_climb_and_altitude():
    s = S.schedule(SEED, ANCHOR, 52)
    trip = [p for p in s.activities if p.trip == "demo-trip"]
    legs = _trip_legs(SEED, "demo-trip", 3, small=False)
    assert len(trip) == 3 == len(legs)
    for c in legs:
        gain, _loss = c.gain_loss()
        assert gain >= 1000
        assert 2550 <= c.ele.min() and c.ele.max() <= 4000
    for a, b in zip(legs, legs[1:]):
        assert a.ele[-1] == pytest.approx(b.ele[0]) and a.lat[-1] == pytest.approx(b.lat[0])


def test_dataset_builds_and_pmc(small, monkeypatch):
    root, m = small
    monkeypatch.setenv("WKO5COACH_TZ", "Asia/Taipei")
    from backend.engine import overview as O
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.engine import planning
    monkeypatch.setattr(planning, "PLAN_PATH", root / "plan.json")      # the demo's thresholds
    ds = FitFolderDataset(root / "fit" / "coros", config=EngineConfig(parity=False), today=ANCHOR,
                          classifications={}, athlete_settings=[], estimate_thresholds=False)
    assert len(ds.workouts) == m["n_activities"]
    types = Counter(w.sport_type for w in ds.workouts)
    assert types["trail running"] >= 1 and types["hiking"] >= 3
    rows = O.pmc(ds, ANCHOR - dt.timedelta(days=40), ANCHOR)["series"]
    assert any((r["ctl"] or 0) > 0 for r in rows)


def _full(kind: str):
    """One showcase activity at full length (the detectors need the real durations)."""
    s = S.schedule(SEED, ANCHOR, 52)
    i, p = next((i, p) for i, p in enumerate(s.activities, start=1) if p.showcase and p.kind == kind)
    raw, info = build_activity(SEED, i, p, s, False, {})
    from backend.files.fit_to_channels import fit_to_channels
    return fit_to_channels(raw), info


def _arr(ch, name):
    return np.array([np.nan if v is None else v for v in ch.channels[name]], float)


def test_cp_test_two_point():
    from backend.engine import cp_protocols as CPP
    ch, _info = _full("cp_test")
    t = np.asarray(ch.elapsedtime, float)
    bouts = CPP.measure_bouts(t, _arr(ch, "power"), _arr(ch, "heartrate"))
    r = CPP.result(bouts, "standard", lthr=168, sex="male")
    assert r is not None and r["cp"] is not None
    assert 240 <= r["cp"] <= 275, r
    assert bouts["standard"]["long_first"]


def test_aet_test_drift_band():
    from backend.engine import aet_test as AT
    ch, _info = _full("aet_test")
    t = np.asarray(ch.elapsedtime, float)
    r = AT.analyze(t, _arr(ch, "heartrate"), speed=_arr(ch, "speed"), power=_arr(ch, "power"),
                   temp=_arr(ch, "temperature"), warm_s=AT.WARM_STD_S)
    assert r["ok"], r
    assert r["band"] == "at" and 145 <= r["hr1"] <= 155, r          # first-half HR = the AeT


def test_no_owner_strings(small):
    root, m = small
    import os
    me = os.path.basename(os.path.expanduser("~")).lower()     # the machine's user name must not leak
    bad = (me, "users\\", "/users/", "wko5")
    blob = json.dumps(m, ensure_ascii=False).lower()
    for f in root.rglob("*"):
        if f.is_file():
            data = f.read_bytes().lower()
            for b in bad:
                assert b.encode() not in data, (f, b)
    assert me not in blob
