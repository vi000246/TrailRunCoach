"""drift v2 (docs/research/drift-algorithm.md): the return-leg city tail as the
cool-down, the trailing idle cut, the VI / walk / halves-power gate on the
window's moving samples, the standard error and the multi-run aggregation.
Synthetic series only — never the WKO5 folder or the app DB."""
import datetime as dt
import re

import numpy as np
import pytest

from backend.engine import drift_agg as DA
from backend.engine import workout_review as R
from backend.engine.algorithms import threshold_estimate as TE
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout


def _t(minutes):
    return np.arange(0, minutes * 60 + 1, 1.0)


def _steady(minutes, hr0=140.0, hr1=147.0, kmh=10.0, watts=200.0):
    """Steady 10 km/h, power 200 W, HR rising linearly — no warm-up stops."""
    t = _t(minutes)
    hr = hr0 + (hr1 - hr0) * t / t[-1]
    return t, hr, np.full(len(t), kmh), np.full(len(t), watts)


def _stop(v, p, a_s, dur_s):
    v[int(a_s):int(a_s + dur_s)] = 0.0
    p[int(a_s):int(a_s + dur_s)] = 0.0


# ---------------------------------------------------------------------------
# the end of the window
# ---------------------------------------------------------------------------

def test_the_return_leg_city_tail_is_the_cool_down():
    t, hr, v, p = _steady(70)
    _stop(v, p, 45 * 60, 20)                 # an isolated mid-run stop: 14.7 min before the tail, not in it
    for a in (60 * 60, 63 * 60, 66 * 60 + 30):
        _stop(v, p, a, 30)                   # three crossings ≤ 6 min apart in the last 12 min
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    tail = r["tail"]
    assert tail["stops"] == 3 and tail["first_stop_s"] == pytest.approx(3600.0)
    assert tail["excluded_s"] == pytest.approx(600.0, abs=1.5)
    assert r["end_s"] == pytest.approx(3600.0)
    # measured: 10:00 → 60:00 minus the 20-s mid-run stop
    assert r["ok"] and r["measured_s"] == pytest.approx(50 * 60 - 20, abs=2)
    ex = R.excluded_text(r)
    assert "回程市區段 10:00" in ex and "當緩和" in ex
    # the card says so
    assert R.tail_text(r).startswith("回程市區段 10:00")


def test_the_tail_cluster_only_reaches_12_minutes_back_and_never_before_minute_20():
    t, hr, v, p = _steady(70)
    _stop(v, p, 70 * 60 - 14 * 60, 20)       # 14 min before the end: outside the 12-min reach
    _stop(v, p, 70 * 60 - 10 * 60, 20)       # 10 min before: the cluster starts here
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["tail"]["stops"] == 1 and r["tail"]["first_stop_s"] == pytest.approx(60 * 60)
    # a gap > 6 min breaks the cluster: only the last stop counts
    t, hr, v, p = _steady(70)
    _stop(v, p, 59 * 60, 20)
    _stop(v, p, 67 * 60, 20)                 # 7:40 after the previous one ended
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["tail"]["stops"] == 1 and r["tail"]["first_stop_s"] == pytest.approx(67 * 60)
    # a short run: the last 12 min start before minute 20 → no stop there is a cool-down
    tt = _t(28)
    end, rec = R.steady_end(tt, np.where(tt == 19 * 60, 0.0, 10.0), 28 * 60 + 1)
    assert rec is None and end == 28 * 60 + 1
    # no stops at all: nothing excluded
    t, hr, v, p = _steady(60)
    r = R.drift_of(t, hr, v, power=p)
    assert r["tail"] is None and R.excluded_text(r) is None


def test_trailing_idle_is_cut_and_not_counted_as_stopped():
    t, hr, v, p = _steady(65)
    v[t > 60 * 60] = 0.0                     # the watch kept running 5 min after the run
    p[t > 60 * 60] = 0.0
    hr[t > 60 * 60] = 100.0
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    # without the cut 5 min standing in 55 min would be > 5 % stopped
    assert r["ok"] and r["idle_s"] == pytest.approx(300.0, abs=1.5)
    assert r["end_s"] == pytest.approx(3600.0, abs=1.0) and r["tail"] is None
    assert "結尾靜止 5:00" in R.excluded_text(r)
    # < 2 min of trailing standing is not "idle": it is the last stop, the tail rule takes it
    t, hr, v, p = _steady(61)
    v[t > 60 * 60] = 0.0
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["idle_s"] == 0.0 and r["tail"]["stops"] == 1 and r["ok"]
    end, idle = R.trailing_idle(t, v)
    assert idle == 0.0 and end > 61 * 60


# ---------------------------------------------------------------------------
# the stability gate
# ---------------------------------------------------------------------------

def test_vi_gate_on_the_moving_samples():
    t, hr, v, p = _steady(60)
    p = np.where((t // 60) % 2 == 0, 140.0, 260.0)          # 1-min on / off: not a steady run
    r = R.drift_of(t, hr, v, power=p, cp=400.0)
    assert r["vi"] > R.DRIFT_MAX_VI and not r["ok"] and r["tier"] is None and "VI" in r["reason"]
    assert r["cv30"] is not None and r["cv30_w1"] is not None      # the old number, information only
    # steady power with a little noise: VI ≈ 1.00, accepted
    rng = np.random.default_rng(1)
    t, hr, v, p = _steady(60)
    p = 200.0 + rng.normal(0, 8, len(t))
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["ok"] and r["vi"] < 1.01
    # stops (< 5 %): the old CV counts their 0 W, VI on the moving samples doesn't
    t, hr, v, p = _steady(60)
    for a in (15, 25, 35, 45):
        _stop(v, p, a * 60, 30)
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["ok"] and r["vi"] == pytest.approx(1.0, abs=1e-6)
    assert r["cv30"] > 0.10 > r["cv30_w1"]


def test_power_vi_matches_np_over_ap():
    rng = np.random.default_rng(3)
    gp = 200.0 + rng.normal(0, 20, 3000)
    vi, cv = R.power_vi(gp, np.ones(3000, dtype=bool))
    r30 = np.convolve(gp, np.ones(30) / 30, "valid")
    assert vi == pytest.approx(np.mean(r30 ** 4) ** 0.25 / gp.mean())
    # DRIFT §4.2: VI ≈ 1 + 1.5·CV²
    assert vi == pytest.approx(1 + 1.5 * cv ** 2, abs=0.002)


def test_a_run_walk_is_refused_by_the_walk_rule():
    t, hr, v, p = _steady(60)
    v[(t >= 30 * 60) & (t < 34 * 60)] = 6.0                 # 4 min at 60 % of the median speed
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert not r["ok"] and r["tier"] is None and "跑走" in r["reason"]
    assert r["walk_max_s"] >= 180 and r["walks"]
    # 2 min slow: listed, not refused
    t, hr, v, p = _steady(60)
    v[(t >= 30 * 60) & (t < 32 * 60)] = 6.0
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["ok"] and 100 <= r["walk_max_s"] < 180


def test_halves_power_difference_over_5_percent_is_refused():
    t, hr, v, p = _steady(60)
    p = np.where(t < 35 * 60, 200.0, 188.0)                  # second half 6 % lower (the 08-31 case)
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert not r["ok"] and "後半功率比前半低 6%" in r["reason"]
    assert r["halves_diff"] == pytest.approx(-0.06, abs=1e-6) and r["halves_basis"] == "power"
    p = np.where(t < 35 * 60, 200.0, 196.0)                  # −2 %: fine
    r = R.drift_of(t, hr, v, power=p, cp=300.0)
    assert r["ok"] and r["halves_diff"] == pytest.approx(-0.02, abs=1e-6)
    # no power: the pace is held to the same rule
    t, hr, v, _ = _steady(60)
    v = np.where(t < 35 * 60, 10.0, 9.3)
    r = R.drift_of(t, hr, v)
    assert not r["ok"] and "後半速度" in r["reason"] and r["halves_basis"] == "pace"


def test_ramps_stay_in_and_the_ramp_free_value_is_shown():
    t, hr, v, p = _steady(60, hr0=140.0, hr1=140.0)
    dist = np.cumsum(v / 3600.0)
    elev = np.zeros(len(t))
    hr = hr.copy()
    for a in (40 * 60, 47 * 60):                             # two 60-s ramps (+5 %) in the second half
        seg = (t >= a) & (t < a + 60)
        elev[seg] = np.linspace(0, 0.05 * 10 / 3.6 * 60, seg.sum())
        elev[t >= a + 60] = elev[seg][-1]
        after = (t >= a) & (t < a + 180)
        hr[after] += 6.0                                     # HR up on and after the ramp, power flat
        elev[t >= a + 60] = 0.0                              # and back down at once (keeps it simple)
    r = R.drift_of(t, hr, v, power=p, cp=300.0, dist=dist, elev=elev)
    assert r["ok"]                                           # not excluded (the user's decision)
    rp = r["ramps"]
    assert rp["n1"] == 0 and rp["n2"] >= 2
    assert abs(rp["pw_drift"]) < 0.001 and r["pw_drift"] > 0.005     # the ramps' HR rise is the drift
    txt = R.ramps_text(r, power=True)
    assert "前半 0 段" in txt and "去坡道 0.0%" in txt


# ---------------------------------------------------------------------------
# precision
# ---------------------------------------------------------------------------

def _ar1(n, rho, sd, rng):
    e = rng.normal(0, sd * np.sqrt(1 - rho ** 2), n)
    x = np.empty(n)
    x[0] = rng.normal(0, sd)
    for i in range(1, n):
        x[i] = rho * x[i - 1] + e[i]
    return x


def test_the_regression_matches_the_halves_on_a_linear_rise_and_has_a_standard_error():
    n = 2400
    gh = 140.0 + 7.0 * np.arange(n) / n
    gx = np.full(n, 200.0)
    wg = np.ones(n, dtype=bool)
    stopped = np.zeros(n, dtype=bool)
    halves = R._halves_drift(gh, gx, np.ones(n), wg)
    r = R.drift_regression(gh, gx, wg, stopped, halves[2])
    # c·T/2 ÷ HR₂ is the halves drift for a linear rise at fixed output (DRIFT §3.1)
    assert r["drift_eq"] == pytest.approx(halves[0], rel=1e-3) and r["se"] < 1e-6
    # with autocorrelated noise (ρ₁ 0.99) the SE is pp-sized and tracks the run-to-run scatter
    rng = np.random.default_rng(7)
    est, ses = [], []
    for _ in range(30):
        h = gh + _ar1(n, 0.99, 3.0, rng)
        hv = R._halves_drift(h, gx, np.ones(n), wg)
        rr = R.drift_regression(h, gx, wg, stopped, hv[2])
        est.append(hv[0])
        ses.append(rr["se"])
    assert 0.003 < np.median(ses) < 0.06
    assert 0.4 < np.std(est) / np.median(ses) < 2.5


def test_drift_of_reports_the_se_and_flags_a_noisy_run(monkeypatch):
    rng = np.random.default_rng(11)
    t, hr, v, p = _steady(52)
    quiet_hr, noisy_hr = hr + _ar1(len(t), 0.95, 0.5, rng), hr + _ar1(len(t), 0.999, 8.0, rng)
    quiet = R.drift_of(t, quiet_hr, v, power=p, cp=300.0)
    noisy = R.drift_of(t, noisy_hr, v, power=p, cp=300.0)
    assert quiet["drift_se"] is not None and quiet["pw_drift_se"] is not None
    assert noisy["pw_drift_se"] > 10 * quiet["pw_drift_se"] and noisy["pw_drift_se"] > 0.02
    assert quiet["noisy"] is False and noisy["noisy"] is (noisy["pw_drift_se"] > R.DRIFT_NOISY_SE)
    monkeypatch.setattr(R, "DRIFT_NOISY_SE", 0.02)
    assert R.drift_of(t, noisy_hr, v, power=p, cp=300.0)["noisy"] is True
    assert R.se_text(0.042) == "±4.2 pp"


def test_the_card_shows_the_se_the_exclusions_and_the_stability():
    t, hr, v, p = _steady(70)
    for a in (60 * 60, 63 * 60, 66 * 60 + 30):
        _stop(v, p, a, 30)
    day = dt.date(2026, 9, 28)
    w = FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                    sport_type="running",
                    channels={"elapsedtime": list(t), "heartrate": list(hr), "speed": list(v), "power": list(p),
                              "elapseddistance": list(np.cumsum(v / 3600.0))},
                    metrics={"duration": 70 * 60.0, "movingduration": 70 * 60.0, "distance": 11.0, "climbing": 20.0})
    ds = FakeDataset([w], day, settings={"runthr": 160.0, "runftp": 250.0})
    for basis, name in (("pace", "心率飄移（配速）"), ("power", "心率飄移（功率）")):
        rows = {s["name"]: s["data"] for s in R.review(ds, ds.workouts[0], "aerobic", basis=basis)["series"]}
        # plain value (owner 2026-10-02); this run's ± only in the ?'s last 方法 line
        assert "pp" not in rows[name]["value"] and "±" not in rows[name]["value"]
        assert re.search(r"方法：.*這次誤差約 ±\d+\.\d 個百分點", rows[name]["tip"].split("\n")[-1])
        assert "回程市區段 10:00" in rows["已排除"]["value"] and "當緩和" in rows["已排除"]["value"]
        assert "VI" in rows["穩定度"]["value"] and "舊規則 30 秒變異" in rows["穩定度"]["value"]


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def test_aggregate_the_last_six_inverse_variance_weighted():
    pts = [{"drift": d, "se": 0.04, "date": f"2026-09-{i + 1:02d}"} for i, d in
           enumerate([0.30, 0.30, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06])]
    a = DA.aggregate(pts)
    assert a["n"] == 6 and a["first"] == "2026-09-03"            # the two old outliers are out
    assert a["mean"] == pytest.approx(0.035)
    assert a["se_iv"] == pytest.approx(0.04 / np.sqrt(6))
    assert a["se"] == pytest.approx(max(a["se_iv"], a["se_emp"]))
    # a precise run weighs more
    b = DA.aggregate([{"drift": 0.0, "se": 0.01}, {"drift": 0.06, "se": 0.06}])
    assert b["mean"] == pytest.approx(0.06 / 37, rel=1e-6)
    # one run: its own SE; a run without an SE gets the single-run default
    assert DA.aggregate([{"drift": 0.02, "se": 0.03}])["se"] == pytest.approx(0.03)
    assert DA.aggregate([{"drift": 0.02, "se": None}])["se"] == pytest.approx(DA.SE_DEFAULT)
    assert DA.aggregate([{"drift": None}]) is None
    assert DA.text(a).endswith("（6 次平均）") and "±" in DA.text(a)


def _road(day, minutes=52, hr=135.0, hr_end=None, power=200.0):
    t = _t(minutes)
    h = np.linspace(hr, hr_end if hr_end is not None else hr + 3.0, len(t))
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running",
                       channels={"elapsedtime": list(t), "heartrate": list(h), "speed": [10.0] * len(t),
                                 "power": [power] * len(t), "elapseddistance": list(t * 10.0 / 3600.0)},
                       metrics={"duration": minutes * 60.0, "movingduration": minutes * 60.0,
                                "distance": minutes / 6.0, "climbing": 20.0})


def test_rolling_aggregate_for_the_season_charts():
    today = dt.date(2026, 9, 30)
    ws = [_road(today - dt.timedelta(days=d)) for d in (20, 15, 10, 5, 1)]
    ds = FakeDataset(ws, today, settings={"runthr": 160.0, "runftp": 250.0})
    roll = DA.rolling(ds, "pace")
    order = sorted(ds.workouts, key=lambda w: w.day)
    assert order[0].idx not in roll                              # one run: no mean ± SE yet
    last = roll[order[-1].idx]
    assert last["n"] == 5 and last["se"] > 0
    single = [R.basis_drift(R.measure(ds, w)["drift"], "pace", ref=True)[0] for w in order]
    assert min(single) - 1e-9 <= last["mean"] <= max(single) + 1e-9
    # the evaluator's drift_avg() plots it
    from backend.engine.wko5expr.evaluator import Evaluator
    ev = Evaluator(ds, order[0].day, order[-1].day)
    lo = ev.evaluate('drift_avg("pace", "lo")', order[-1])
    mid = ev.evaluate('drift_avg("pace")', order[-1])
    assert mid == pytest.approx(last["mean"]) and lo == pytest.approx(last["mean"] - last["se"])
    assert np.isnan(ev.evaluate('drift_avg("pace")', order[0]))


def test_aet_aggregate_with_se_and_shift():
    rng = np.random.default_rng(5)
    # drift = 5 % at 145 bpm, +0.5 pp per bpm; 12 runs between 130 and 155 bpm
    hr = np.linspace(130, 155, 12)
    drift = 0.05 + 0.005 * (hr - 145) + rng.normal(0, 0.004, 12)
    a = TE.aet_aggregate([(h, d, 0.004) for h, d in zip(hr, drift)])
    assert a.value == pytest.approx(145, abs=2) and a.se is not None
    assert a.valid and a.se <= TE.AET_MAX_SE_BPM and abs(a.shift_bpm) <= TE.AET_SHIFT_BPM
    # the single-run noise (±5 pp): the SE is far above 3 bpm → 需要測試
    noisy = TE.aet_aggregate([(h, d + rng.normal(0, 0.05), 0.05) for h, d in zip(hr, drift)])
    assert not noisy.valid and "需要測試" in noisy.reason
    # the last 6 runs (in date order) all drift 6 pp more than before: AeT moved down → 需要測試
    shifted = list(zip(hr, drift, [0.004] * 12))
    order = list(range(12))
    rng.shuffle(order)
    pts = [(hr[i], drift[i] + (0.06 if k >= 6 else 0.0), 0.004) for k, i in enumerate(order)]
    s = TE.aet_aggregate(pts)
    assert s.shift_bpm is not None and s.shift_bpm < -TE.AET_SHIFT_BPM and not s.valid
    # too few points
    few = TE.aet_aggregate(shifted[:4])
    assert not few.valid and few.value is None and "需要 ≥ 6 次" in few.reason
