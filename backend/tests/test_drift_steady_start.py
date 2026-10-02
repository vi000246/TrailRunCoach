"""drift_of's adaptive start (workout_review.steady_start, 推估): a city
section with crossings before the steady path moves the window's start to
60 s after the last stop that begins in the first 20 min — never earlier
than the 10-min warm-up, never at the cost of a tier. Ramps and strides are
NOT masked (user decision 2026-10-01: HR after a ramp may not have recovered,
so masking would hide a real effect). Synthetic series only — never the
user's DB or plan."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import workout_review as R


def _t(minutes):
    return np.arange(0, minutes * 60 + 1, 1.0)


def _steady(minutes, power=200.0):
    """10 km/h, HR 140 → 147 after a 10′ warm-up (HR 110, 8 km/h), constant power."""
    t = _t(minutes)
    after = t >= 600
    hr = np.where(after, 140.0 + 7.0 * (t - 600) / max(1.0, t[-1] - 600), 110.0)
    v = np.where(after, 10.0, 8.0)
    p = np.full(len(t), power)
    return t, hr, v, p


def _stop(t, v, p, at_s, dur_s, surge_s=30, surge_w=320.0):
    """Stand still for dur_s (speed 0, power 0), then re-accelerate (surge_s at surge_w)."""
    s = (t >= at_s) & (t < at_s + dur_s)
    v[s], p[s] = 0.0, 0.0
    p[(t >= at_s + dur_s) & (t < at_s + dur_s + surge_s)] = surge_w


def _cv30(p, start):
    q = p[int(start):]
    m = np.convolve(q, np.ones(30) / 30, "valid")
    return m.std() / m.mean()


# ---------------------------------------------------------------------------
# steady_start / drift_of: the urban start
# ---------------------------------------------------------------------------

def test_a_city_start_with_crossings_in_10_to_20_min_moves_the_start():
    t, hr, v, p = _steady(70)
    for at in (180, 420, 660, 780, 900, 1020, 1140):      # 3′, 7′ (in the warm-up), then 11′ … 19′
        _stop(t, v, p, at, 40)
    # the fixed 10′ start would see 5 × 40 s standing (200 s of 3600 s = 5.6 % > 5 %): refused today
    fixed = R.drift_of(t, hr, v, p, cp=300.0)
    start, rec = R.steady_start(t, hr, v)
    assert start == pytest.approx(1140 + 39 + 60) and rec["stops"] == 7 and not rec["fallback"]
    assert rec["stopped_s"] == pytest.approx(7 * 40) and rec["last_stop_s"] == pytest.approx(1179)
    assert rec["shifted_s"] == pytest.approx(start - 600)
    assert fixed["warmup_s"] == pytest.approx(start)
    # measured from 20:39: 49.35 min of steady running → the strict tier, both bases
    assert fixed["ok"] and fixed["pw_ok"] and fixed["tier"] == "test"
    assert fixed["measured_s"] == pytest.approx(70 * 60 - start, abs=2)
    assert fixed["v1"] == pytest.approx(10.0) and fixed["p1"] == pytest.approx(200.0)
    # the halves start at the new start: HR at 20:39 on the 140 → 147 ramp
    assert fixed["hr1"] > 140.0 + 7.0 * (start - 600) / (70 * 60 - 600)
    txt = R.excluded_text(fixed)
    assert "前段路口停等 7 次" in txt and "4:40" in txt and "20:39" in txt and "推估" in txt
    assert R.start_text(fixed) == "前 20:39 不算"


def test_stops_inside_the_warm_up_change_nothing():
    t, hr, v, p = _steady(55)
    _stop(t, v, p, 200, 30)
    _stop(t, v, p, 480, 30)                               # ends 8:29 → +60 s = 9:29 < 10:00
    r = R.drift_of(t, hr, v, p, cp=300.0)
    assert r["warmup_s"] == 600 and r["start_shift"] is None and r["ok"]
    assert R.excluded_text(r) is None and R.start_text(r) == "前 10 分鐘不算"


def test_a_stop_after_20_min_is_a_mid_run_stop_not_a_city_start():
    t, hr, v, p = _steady(60)
    _stop(t, v, p, 1500, 60)                              # 25′: counts toward the 5 % rule
    r = R.drift_of(t, hr, v, p, cp=300.0)
    assert r["warmup_s"] == 600 and r["start_shift"] is None
    assert r["ok"] and r["measured_s"] == pytest.approx(50 * 60 - 60, abs=2)
    _stop(t, v, p, 1700, 120)                             # + 2′ more: 180 s of 3000 s = 6 % > 5 %
    r = R.drift_of(t, hr, v, p, cp=300.0)
    assert not r["ok"] and "中途停了 3:00" in r["reason"]


@pytest.mark.parametrize("minutes, tier", [(50, "ref"), (60, "test")])
def test_the_later_start_never_costs_a_tier(minutes, tier):
    """A stop ending at 19:29 → start 20:29; on a 50′ run that leaves 29.5′
    (< 30, no tier) where the fixed start has 39.5′ (參考), on a 60′ run
    39.5′ (參考) where the fixed start has 49.5′ (嚴格): keep the 10′ start,
    the stop counts toward the 5 % rule."""
    t, hr, v, p = _steady(minutes)
    _stop(t, v, p, 1140, 30)
    start, rec = R.steady_start(t, hr, v)
    assert start == 600 and rec["fallback"] and rec["shifted_s"] == 0
    r = R.drift_of(t, hr, v, p, cp=300.0)
    assert r["tier"] == tier and r["warmup_s"] == 600 and r["start_shift"]["fallback"]
    assert r["measured_s"] == pytest.approx((minutes - 10) * 60 - 30, abs=2)
    assert "仍從第 10 分鐘起算" in R.excluded_text(r) and R.start_text(r) == "前 10 分鐘不算"


def test_a_recording_gap_is_not_a_stop():
    t, hr, v, p = _steady(60)
    keep = ~((t > 900) & (t < 960))                       # 1-min gap at 15′, no samples at all
    r = R.drift_of(t[keep], hr[keep], v[keep], p[keep], cp=300.0)
    assert r["warmup_s"] == 600 and r["start_shift"] is None


# ---------------------------------------------------------------------------
# ramps and strides stay in (no masking)
# ---------------------------------------------------------------------------

def test_two_ramps_stay_in_the_window_and_the_halves():
    t, hr, v, p = _steady(60)
    for at in (1500, 2700):                               # one ramp in each half, 45 s at +40 % power
        s = (t >= at) & (t < at + 45)
        p[s] = 280.0
        v[s] = 8.5
        hr[(t >= at) & (t < at + 120)] += 6.0             # HR up and still recovering after the top
    r = R.drift_of(t, hr, v, p, cp=300.0)
    assert r["ok"] and r["warmup_s"] == 600 and r["start_shift"] is None
    assert r["measured_s"] == pytest.approx(50 * 60, abs=2)          # nothing masked
    full = R._halves_drift(hr, p, R._dt(t), (t >= 600) & (t > 0))
    assert r["pw_drift"] == pytest.approx(full[0], abs=1e-12)        # ramps' power and HR are in
    assert r["p1"] > 200.0 and r["p2"] > 200.0


def test_a_few_strides_stay_in_and_many_are_refused_as_unsteady():
    t, hr, v, p = _steady(60)
    for at in (1200, 1800, 2400, 3000):                    # 4 × 15 s at 280 W
        p[(t >= at) & (t < at + 15)] = 280.0
    r = R.drift_of(t, hr, v, p, cp=300.0)
    assert r["ok"] and r["measured_s"] == pytest.approx(50 * 60, abs=2)
    assert (r["p1"] + r["p2"]) / 2 > 200.0                 # the strides' power counts
    t, hr, v, p = _steady(60)
    for at in range(700, 3600, 120):                       # 30 s at 400 W every 2 min
        p[(t >= at) & (t < at + 30)] = 400.0
    assert _cv30(p, 600) > R.AET_MAX_POWER_CV
    r = R.drift_of(t, hr, v, p, cp=500.0)
    assert not r["ok"] and r["tier"] is None and "功率起伏大" in r["reason"]


# ---------------------------------------------------------------------------
# still refused
# ---------------------------------------------------------------------------

def _city_then(minutes, walk=False):
    t, hr, v, p = _steady(minutes)
    for at in (180, 660, 900):
        _stop(t, v, p, at, 25)
    if walk:                                               # run 4′ / walk 2′ after the city section
        for at in range(1200, minutes * 60, 360):
            s = (t >= at + 240) & (t < at + 360)
            v[s], p[s] = 5.0, 100.0
    return t, hr, v, p


def test_a_genuinely_unsteady_run_after_a_city_start_is_still_refused():
    t, hr, v, p = _city_then(70, walk=True)
    r = R.drift_of(t, hr, v, p, cp=300.0)
    assert r["warmup_s"] == pytest.approx(900 + 24 + 60) and r["start_shift"]["stops"] == 3
    assert not r["ok"] and not r["ref_ok"] and r["tier"] is None and "功率起伏大" in r["reason"]
    assert R.basis_drift(r, "pace", ref=True)[0] is None


def test_a_hot_run_after_a_city_start_is_kept_with_its_band():
    # heat bands: no refusal above 25 °C any more — the same result, tagged
    t, hr, v, p = _city_then(70)
    cool = R.drift_of(t, hr, v, p, cp=300.0)
    assert cool["ok"] and cool["start_shift"]["stops"] == 3
    r = R.drift_of(t, hr, v, p, cp=300.0, temp_c=28.0, temp_src="watch")
    assert r["ok"] and r["pw_ok"] and r["tier"] == "test" and r["drift"] == cool["drift"]
    assert r["temp_band"] == "warm" and r["heat"]
    assert r["warmup_s"] == cool["warmup_s"]


# ---------------------------------------------------------------------------
# the card
# ---------------------------------------------------------------------------

def _fake_run(day, t, hr, v, p):
    from backend.tests.wko5_fakes import FakeWorkout
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": list(v), "power": list(p),
          "elapseddistance": list(np.cumsum(v) / 3600.0)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": t[-1], "movingduration": t[-1],
                                "distance": float(np.sum(v) / 3600.0), "climbing": 20.0})


def _card_text(card):
    return {s["name"]: s["data"]["value"] for s in card["series"] if s["data"]["kind"] == "value"}


def test_the_card_says_what_the_adaptive_start_left_out():
    from backend.tests.wko5_fakes import FakeDataset
    today = dt.date(2026, 9, 30)
    t, hr, v, p = _city_then(70)
    ds = FakeDataset([_fake_run(today, t, hr, v, p)], today, settings={"runthr": 165.0, "runftp": 300.0})
    ds.activity_temps = {}
    text = _card_text(R.review(ds, ds.workouts[0], "aerobic"))
    assert "前 16:24 不算" in text.get("Pa:HR 飄移", ""), text
    assert text["已排除"].startswith("前段路口停等 3 次") and "推估" in text["已排除"]
    power = _card_text(R.review(ds, ds.workouts[0], "aerobic", basis="power"))
    assert "前 16:24 不算" in power["Pw:HR 飄移"]
    # a plain run has no 已排除 row and the old wording
    t, hr, v, p = _steady(60)
    ds = FakeDataset([_fake_run(today, t, hr, v, p)], today, settings={"runthr": 165.0, "runftp": 300.0})
    ds.activity_temps = {}
    text = _card_text(R.review(ds, ds.workouts[0], "aerobic"))
    assert "已排除" not in text and "前 10 分鐘不算" in text["Pa:HR 飄移"]
