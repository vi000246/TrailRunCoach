"""The 90-minute test's output check (SP-275; lthr-low-confidence-testing.md §6.1 第 3 點): a
scheduled test must hold its pace (power when there is power) — minutes 80–90 not > 5 % slower
than 10–20 — and no longer needs Zone 1; a passive long run keeps Zone 1. Synthetic data only."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import aet_test as AT
from backend.engine import base_check as BC
from backend.i18n import use_locale
from backend.tests.test_quality_gate import TODAY, _ds
from backend.tests.wko5_fakes import FakeWorkout

XU_TITLE = AT.PROTOCOLS["xu90"]["title"]


def _series(minutes=95, hr0=128.0, rise=0.06, slow=0.0, power=None, pdrop=0.0, speed=True):
    """HR rises `rise` from minute 10 to 90; speed 10 km/h, `slow` slower (pace) from minute 20 to 80
    (linear), power `power` W dropping `pdrop` the same way."""
    t = np.arange(minutes * 60 + 1, dtype=float)
    ramp = np.clip((t - 1200) / 3600, 0, 1)
    hr = hr0 * (1 + rise * np.clip((t - 600) / 4800, 0, 1))
    v = 10.0 / (1 + slow * ramp) if speed else None
    p = None if power is None else power * (1 - pdrop * ramp)
    return t, hr, v, p


def _wk(title="", **k):
    t, hr, v, p = _series(**k)
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "elapseddistance": list(t * 10 / 3600)}
    if v is not None:
        ch["speed"] = list(v)
    if p is not None:
        ch["power"] = list(p)
    return FakeWorkout(start=dt.datetime.combine(TODAY - dt.timedelta(days=3), dt.time(6)), sport="run",
                       tags=["running"], sport_type="running", channels=ch, title=title,
                       metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                "distance": len(t) / 360.0, "climbing": 10.0})


def _xu(title="", **k):
    ds = _ds([_wk(title, **k)])
    return BC.xu_run(ds, ds.workouts[0])


# ---- the check itself ------------------------------------------------------------------------

def test_output_hold_pace_power_and_none():
    t, _h, v, _p = _series(slow=0.06)
    h = BC.output_hold(t, v)
    assert h["basis"] == "pace" and h["drop"] == pytest.approx(0.06, abs=0.002) and not h["ok"]
    t, _h, v, _p = _series(slow=0.03)
    assert BC.output_hold(t, v)["ok"]
    # power first: held power, slower pace (e.g. a headwind) → held
    t, _h, v, p = _series(slow=0.08, power=200.0, pdrop=0.02)
    h = BC.output_hold(t, v, p)
    assert h["basis"] == "power" and h["ok"] and h["drop"] == pytest.approx(0.02, abs=0.002)
    t, _h, v, p = _series(power=200.0, pdrop=0.07)
    assert not BC.output_hold(t, v, p)["ok"]
    t, _h, _v, _p = _series(speed=False)
    assert BC.output_hold(t, None, None) == {"basis": None, "drop": None, "ok": None}


# ---- xu_run (the base-check card) --------------------------------------------------------------

def test_scheduled_test_slowed_more_than_5_percent_is_refused():
    r = _xu(XU_TITLE, slow=0.06)
    assert r["scheduled"] and not r["ok"]
    assert any(x.startswith("後段放慢了 6.0%：飄移會偏小，下次配速固定") for x in r["why"])


def test_scheduled_test_slowed_less_than_5_percent_counts():
    r = _xu(XU_TITLE, slow=0.03)
    assert r["scheduled"] and r["ok"] and r["hold"]["basis"] == "pace"


def test_scheduled_test_with_power_uses_power():
    r = _xu(XU_TITLE, slow=0.08, power=200.0, pdrop=0.01)
    assert r["ok"] and r["hold"]["basis"] == "power"
    r = _xu(XU_TITLE, power=200.0, pdrop=0.07)
    assert not r["ok"] and any("後段放慢了 7.0%" in x for x in r["why"])


def test_scheduled_test_counts_above_the_easy_cap():
    # HR ~150–159 is above AeT (0.89 × 160 = 142): a test run at the E pace still counts
    r = _xu(XU_TITLE, hr0=150.0)
    assert r["scheduled"] and r["ok"]


def test_a_passive_long_run_still_needs_zone_1():
    r = _xu("", hr0=150.0)
    assert not r["scheduled"] and not r["ok"] and any("1 區" in x for x in r["why"])
    assert _xu("", slow=0.08)["ok"]                         # passive: no pace check (owner: scheduled only)


def test_no_speed_no_power_only_hr_and_says_so():
    r = _xu(XU_TITLE, speed=False)
    assert r["ok"] and r["hold"]["basis"] is None
    assert "沒辦法確認配速有沒有維持" in BC.xu_text(r)


# ---- analyze_xu (the test's review card) ---------------------------------------------------------

def test_analyze_xu_refuses_a_slowdown():
    t, h, v, p = _series(slow=0.06)
    r = AT.analyze(t, h, v, None, judge="xu")
    assert not r["ok"] and r["reason"].startswith("後段放慢了 6.0%")
    t, h, v, p = _series(slow=0.03)
    assert AT.analyze(t, h, v, None, judge="xu")["ok"]
    t, h, v, p = _series(slow=0.08, power=200.0)
    r = AT.analyze(t, h, v, p, judge="xu")
    assert r["ok"] and r["hold"]["basis"] == "power"


def test_analyze_xu_without_speed_or_power_says_so():
    t, h, _v, _p = _series(speed=False)
    r = AT.analyze(t, h, None, None, judge="xu")
    assert r["ok"] and "沒辦法確認配速有沒有維持" in AT.lines(r)[-1]


def test_english():
    with use_locale("en"):
        t, h, v, _p = _series(slow=0.06)
        r = AT.analyze(t, h, v, None, judge="xu")
        assert r["reason"] == "You slowed down 6.0% in the late part: the drift reads too small; next time hold the pace"
        t, h, _v, _p = _series(speed=False)
        assert AT.lines(AT.analyze(t, h, None, None, judge="xu"))[-1] == \
            "Can't tell whether the pace was held (no speed or power): heart rate only"
