"""engine/hr_quality.py — the shared per-run HR cleaning and flags (SP-265). Synthetic data only."""
import numpy as np
import pytest

from backend.engine import hr_quality as HQ


def _run(n=3600, hr=150.0, kmh=10.0):
    """A steady run; HR wobbles ±2 bpm (a constant would be a flat line)."""
    t = np.arange(n, dtype=float)
    return t, hr + 2.0 * np.sin(t / 9.0), np.full(n, kmh)


def test_a_spike_is_removed():
    t, h, v = _run()
    h[1500:1510] = 195.0                      # +45 bpm in a second, back after 10 s
    g, y = HQ.clean(t, h, speed_kmh=v)
    assert np.isnan(y[1500:1510]).all() and np.isfinite(y[1490:1500]).all() and np.isfinite(y[1510])
    q = HQ.assess(t, h, speed_kmh=v)
    s = HQ.summary(q)
    assert s["spike_n"] == 1 and s["spike_s"] == 10 and s["step_n"] == 0


def test_a_real_three_minute_climb_of_30_bpm_is_kept():
    t, h, v = _run()
    h[:1200] -= 10.0                          # 140 …
    h[1200:1380] = np.linspace(140, 170, 180)  # … +30 bpm over 3 min …
    h[1380:] += 20.0                          # … and it stays at 170
    g, y = HQ.clean(t, h, speed_kmh=v)
    assert np.isfinite(y).all()
    s = HQ.summary(HQ.assess(t, h, speed_kmh=v))
    assert s["spike_n"] == 0 and s["step_n"] == 0 and s["suspect_s"] == 0


def test_a_fast_rise_after_a_stop_is_kept():
    t, h, v = _run()
    v[1200:1260] = 0.0                        # a 1-min stop at a crossing
    h[1200:1260] = np.linspace(150, 118, 60)  # HR falls while standing
    h[1260:1263] = [126, 134, 140]            # phase I: +22 bpm within 3 s on the restart …
    h[1263:] -= 5.0                           # … and it stays (a real level change)
    g, y = HQ.clean(t, h, speed_kmh=v)
    assert np.isfinite(y).all()
    assert HQ.summary(HQ.assess(t, h, speed_kmh=v))["step_n"] == 0


def test_a_moving_step_is_flagged_and_its_high_side_dropped():
    t, h, v = _run()
    h[1800:] += 22.0                          # +22 bpm in a second while running, never back
    q = HQ.assess(t, h, speed_kmh=v)
    s = HQ.summary(q)
    assert s["step_n"] == 1 and s["step_s"] >= HQ.STEP_HOLD_S
    g, y = HQ.clean(t, h, speed_kmh=v)
    assert np.isnan(y[1800:]).all() and np.isfinite(y[:1800]).all()
    # a step down is flagged, nothing is dropped (the lower side is not a peak)
    t, h, v = _run()
    h[1800:] -= 22.0
    assert HQ.summary(HQ.assess(t, h, speed_kmh=v))["step_n"] == 1
    assert np.isfinite(HQ.clean(t, h, speed_kmh=v)[1]).all()
    # without speed a stop can't be seen: no step, the data is kept
    t, h, _v = _run()
    h[1800:] += 22.0
    assert HQ.summary(HQ.assess(t, h))["step_n"] == 0
    assert np.isfinite(HQ.clean(t, h)[1]).all()
    # in the first 10 minutes: not a step
    t, h, v = _run()
    h[300:] += 22.0
    assert HQ.summary(HQ.assess(t, h, speed_kmh=v))["step_n"] == 0


def test_cadence_lock_is_removed_and_flagged():
    t, h, v = _run(1200)
    cad = 170 + 3 * np.sin(t / 7.0)
    h[300:900] = cad[300:900] + 0.5           # HR follows the steps
    g, y = HQ.clean(t, h, cad, speed_kmh=v)
    assert np.isnan(y[330:870]).all() and np.isfinite(y[10])
    s = HQ.summary(HQ.assess(t, h, cad, v))
    assert s["lock_s"] >= 540 and s["lock_n"] == 1
    # HR close to the cadence but flat: a coincidence, not a lock
    assert not HQ.cadence_lock(np.full(600, 172.0), cad[:600]).any()


def test_out_of_range_and_gaps():
    t, h, v = _run(600)
    h[100:105] = 228.0
    g, y = HQ.clean(t, h, speed_kmh=v)
    assert np.isnan(y[100:105]).all()
    # a 20-s gap in the recording is not bridged …
    t2 = np.concatenate([np.arange(0, 100.0), np.arange(120, 400.0)])
    g, y = HQ.clean(t2, np.full(len(t2), 150.0))
    assert np.isnan(y[101:120]).all()


def test_dropout_flat_and_high_start_are_flagged_only():
    t, h, v = _run()
    h[1000:1015] = np.nan                     # the watch kept recording, no HR for 15 s
    h[2000:2070] = 151.0                      # the same value for 70 s while running …
    v = v + 0.5 * np.sin(t / 5.0)             # … while the pace wobbles (a frozen reading)
    h[60:200] = 175.0 + np.sin(t[60:200])     # the first minutes read far above the rest
    q = HQ.assess(t, h, speed_kmh=v)
    s = HQ.summary(q)
    assert s["dropout_n"] == 1 and s["dropout_s"] == 15
    assert s["flat_n"] >= 1 and s["flat_s"] >= 70
    assert s["high_start_s"] >= 30
    # the same flat HR at a perfectly constant speed: nothing says it froze
    assert HQ.summary(HQ.assess(t, h, speed_kmh=np.full(len(t), 10.0)))["flat_s"] == 0
    # never removed by the cleaning
    y = HQ.clean(t, h, speed_kmh=v)[1]
    assert np.isfinite(y[2000:2070]).all() and np.isfinite(y[60:200]).all()


def test_summary_window_and_share():
    t, h, v = _run()
    h[1500:1510] = 195.0
    q = HQ.assess(t, h, speed_kmh=v)
    win = np.zeros(len(q.g), dtype=bool)
    win[1000:2000] = True
    s = HQ.summary(q, win)
    assert s["window_s"] == 1000 and s["suspect_s"] == 10 and s["share"] == pytest.approx(0.01)
    win[:] = False
    win[2000:3000] = True
    assert HQ.summary(q, win)["spike_n"] == 0


def test_held_peak_needs_the_hold():
    y = np.full(100, 150.0)
    y[50:54] = 180.0
    assert HQ.held_peak(y, 5) == 150.0
    assert HQ.held_peak(y, 4) == 180.0
    assert HQ.held_peak(np.full(3, 150.0), 5) is None


def test_the_four_max_hr_rules_use_the_shared_cleaning():
    from backend.engine import session_stimulus as SS, threshold_confidence as TC, thresholds as TH
    from backend.engine.racepower import maximal as MX
    assert SS.cadence_lock is HQ.cadence_lock
    t, h, v = _run()
    h[1500:1510] = 205.0                      # a 10-s spike
    h[2500:] += 30.0                          # a moving step up to the end
    a = TC.clean_hr(t, h, None, v)[1]
    b = HQ.clean(t, h, speed_kmh=v)[1]
    assert np.array_equal(np.isnan(a), np.isnan(b))
    assert TH.peak_sustained_hr(t, h, None, v) == pytest.approx(152.0, abs=0.2)
    assert MX.run_hrmax_peak(t, h, v) == pytest.approx(152.0, abs=1.0)
    st = SS.measure(t, h, None, v, None, None, None, None, "road")
    assert st["hr_peak60"] < 152.0
