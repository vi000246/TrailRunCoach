"""
Capacity back-test fixes of 2026-10-01: thresholds never applied to earlier
dates, stricter capacity samples (maximal.py), plan-event matching and the
HR-based capacity (hrcap.py). Every formula is recomputed independently here.
Plans are temporary objects, never the user's plan file.
"""
from __future__ import annotations

import datetime as dt
import math
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import planning as P
from backend.engine.racepower import backtest as BT
from backend.engine.racepower import difficulty as DF
from backend.engine.racepower import hrcap as HC
from backend.engine.racepower import maximal as MX
from backend.engine.wko5expr.dataset import Dataset, date_to_day


# ---------------------------------------------------------------------------
# threshold leak
# ---------------------------------------------------------------------------

def _ns(plan, wko5_thr=160.0, mftp=175.6):
    ns = SimpleNamespace(plan=plan, mftp_run=mftp,
                         athlete=SimpleNamespace(setting_on=lambda name, day: {"runthr": wko5_thr,
                                                                               "runftp": 250.0}.get(name)))
    ns.setting = lambda name, day: Dataset.setting(ns, name, day)
    ns.sport_setting = lambda kind, w: Dataset.sport_setting(ns, kind, w)
    return ns


def _w(day: dt.date):
    return SimpleNamespace(day=date_to_day(day), sport="run", entry=SimpleNamespace(ftp=None))


def test_plan_test_never_applies_to_earlier_dates():
    plan = P.Plan(thresholds=[P.Threshold("2026-09-30", lthr=155.0, cp=204.0)])
    ns = _ns(plan)
    past, same, later = dt.date(2025, 12, 21), dt.date(2026, 9, 30), dt.date(2026, 10, 1)
    # past: WKO5's dated setting / current mFTP, never the later plan row
    assert Dataset.setting(ns, "runthr", date_to_day(past)) == 160.0
    assert Dataset.cp(ns, _w(past)) == pytest.approx(175.6)
    assert Dataset.aethr(ns, _w(past)) == pytest.approx(0.89 * 160.0)
    # on / after the test day: the plan row (today's values unchanged)
    for d in (same, later):
        assert Dataset.setting(ns, "runthr", date_to_day(d)) == 155.0
        assert Dataset.cp(ns, _w(d)) == 204.0
        assert Dataset.aethr(ns, _w(d)) == pytest.approx(0.89 * 155.0)


def test_future_row_is_not_in_effect_yet():
    plan = P.Plan(thresholds=[P.Threshold("2026-03-01", lthr=150.0), P.Threshold("2026-12-01", lthr=170.0)])
    assert plan.threshold_on("lthr", dt.date(2026, 10, 1)) == 150.0
    assert plan.threshold_on("lthr", dt.date(2026, 12, 1)) == 170.0
    assert plan.threshold_on("lthr", dt.date(2026, 2, 1)) is None


# ---------------------------------------------------------------------------
# maximal.py
# ---------------------------------------------------------------------------

def test_race_category_within_ten_percent():
    assert MX.race_category(5.4) == "5k" and MX.race_category(4.55) == "5k"     # 5000 × 0.91
    assert MX.race_category(5.6) is None                                       # +12 %
    assert MX.race_category(20.544) == "half"                                  # −2.6 %
    assert MX.race_category(38.5) == "marathon" and MX.race_category(12.0) is None


def test_peak_hr_and_observed_hrmax():
    hist = np.zeros(181)
    hist[150 - 40] = 600
    hist[180 - 40] = 20          # 20 s at 180
    hist[178 - 40] = 15          # 15 s at 178 → 35 s at ≥ 178
    assert MX.peak_hr(hist.tolist()) == 178.0
    # strap spike 216 does not move the median of the top five
    assert MX.hrmax_observed([216, 188, 185, 185, 183, 170]) == 185.0


def _road(km=5.0, q4=160.0, peak=180.0, split=1.0):
    return {"km": km, "q4_hr": q4, "peak_hr": peak, "split": split}


def test_road_maximal_rules():
    lthr, hrmax = 155.0, 185.0
    assert MX.road_maximal(_road(), lthr, hrmax)["ok"]
    # each rule alone rejects
    assert not MX.road_maximal(_road(km=6.0), lthr, hrmax)["ok"]
    assert not MX.road_maximal(_road(q4=154.0), lthr, hrmax)["ok"]               # < 1.00 × LTHR
    assert not MX.road_maximal(_road(peak=174.0), lthr, hrmax)["ok"]             # < 185 − 10
    assert not MX.road_maximal(_road(split=0.97), lthr, hrmax)["ok"]             # positive split
    # half marathon: 0.95 × LTHR, no HRmax rule (an HM does not reach HRmax)
    hm = MX.road_maximal(_road(km=21.0, q4=0.95 * lthr, peak=165.0), lthr, hrmax)
    assert hm["ok"] and {c["id"] for c in hm["checks"]} == {"distance", "q4_hr", "split"}
    assert not MX.road_maximal(_road(km=21.0, q4=0.95 * lthr - 0.5, peak=165.0), lthr, hrmax)["ok"]
    assert MX.road_maximal(_road(km=42.0, q4=0.90 * lthr, peak=160.0), lthr, hrmax)["ok"]
    # monotonicity: a hot 5 km at 152 W after 184 W was held for 141 min is not maximal
    hot = MX.road_maximal({**_road(peak=182.0), "p_avg": 152.0, "longer_p": 184.0}, lthr, hrmax)
    assert not hot["ok"] and [c["id"] for c in hot["checks"] if not c["ok"]] == ["monotone"]
    assert MX.road_maximal({**_road(), "p_avg": 190.0, "longer_p": 184.0}, lthr, hrmax)["ok"]


def test_trail_race_like_rules():
    lthr, aet = 155.0, 138.0
    s = {"km": 14.4, "moving_s": 232 * 60.0, "hr_avg": 152.0, "above_aet": 0.84}
    assert MX.trail_maximal(s, lthr, aet)["ok"]
    assert not MX.trail_maximal({**s, "km": 9.9}, lthr, aet)["ok"]
    assert not MX.trail_maximal({**s, "moving_s": 89 * 60.0}, lthr, aet)["ok"]
    assert not MX.trail_maximal({**s, "hr_avg": 0.90 * lthr - 0.1}, lthr, aet)["ok"]
    assert not MX.trail_maximal({**s, "above_aet": 0.66}, lthr, aet)["ok"]
    # a positive split is never a reason (no split rule for trail)
    assert "split" not in {c["id"] for c in MX.trail_maximal(s, lthr, aet)["checks"]}
    # a race word in the title: only km / time apply
    low = {**s, "hr_avg": 120.0, "above_aet": 0.2}
    assert MX.trail_maximal(low, lthr, aet, title="東眼山越野賽")["ok"]
    assert not MX.trail_maximal(low, lthr, aet, title="Trail Running")["ok"]


def test_match_events_by_date_kind_distance():
    ev = [SimpleNamespace(id="a", name="台北馬 2025 半馬", date="2025-12-21", kind="road", priority="A",
                          distance_km=21.0975),
          SimpleNamespace(id="b", name="越野", date="2025-11-02", kind="race", priority="B", distance_km=None),
          SimpleNamespace(id="c", name="百岳", date="2025-11-02", kind="baiyue", priority="A", distance_km=10.0)]
    runs = [{"idx": 1, "date": "2025-12-21", "km": 2.0, "trail": False},       # warm-up jog
            {"idx": 2, "date": "2025-12-21", "km": 20.544, "trail": False},
            {"idx": 3, "date": "2025-12-21", "km": 21.0, "trail": True},        # wrong kind
            {"idx": 4, "date": "2025-11-02", "km": 14.4, "trail": True},
            {"idx": 5, "date": "2025-11-02", "km": 4.0, "trail": True}]
    m = MX.match_events(ev, runs)
    assert set(m) == {2, 4}
    assert m[2]["priority"] == "A" and m[2]["km_err"] == pytest.approx(1.0 - 20.544 / 21.0975)   # |error|
    assert m[2]["km_ok"] and m[4]["km_err"] is None                           # no distance: longest run
    # a run far from the event distance is not the race
    assert MX.match_events(ev[:1], [{"idx": 9, "date": "2025-12-21", "km": 5.0, "trail": False}]) == {}


# ---------------------------------------------------------------------------
# hrcap.py
# ---------------------------------------------------------------------------

def _windows(hr, p, n=10, t0=700.0, g=0.0, run=1.0, v=3.0):
    return [{"g": g, "v": v, "p": p, "hr_lag": hr, "run": run, "t": t0 + 30.0 * i} for i in range(n)]


def test_run_point_filters():
    ws = _windows(140.0, 180.0) + _windows(170.0, 300.0, g=0.05) + _windows(170.0, 300.0, t0=100.0, n=5) \
        + _windows(170.0, 300.0, t0=3700.0, n=5) + _windows(170.0, 300.0, run=0.2)
    q = HC.run_point(ws)
    assert q["n"] == 10 and q["hr"] == pytest.approx(140.0) and q["p"] == pytest.approx(180.0)
    assert HC.run_point(_windows(140.0, 180.0, n=4)) is None


def test_hr_capacity_recovers_a_known_line():
    rng = np.random.default_rng(3)
    hrs = np.linspace(125, 150, 12)
    ps = 2.4 * hrs - 170.0 + rng.normal(0, 2.0, len(hrs))
    pts = [{"hr": float(h), "p": float(p)} for h, p in zip(hrs, ps)]
    c = HC.capacity(pts, 155.0, boot=200)
    b, a = np.polyfit(hrs, ps, 1)                          # independent fit
    assert c["fit"]["b"] == pytest.approx(b) and c["fit"]["a"] == pytest.approx(a)
    assert c["p_lthr"] == pytest.approx(a + b * 155.0)
    assert abs(c["p_lthr"] - (2.4 * 155 - 170)) < 6.0
    assert c["valid"] and c["range"][0] < c["p_lthr"] < c["range"][1]
    assert c["extrap_bpm"] == pytest.approx(5.0)
    few = HC.capacity(pts[:5], 155.0)
    assert not few["valid"] and any("< 8" in r for r in few["reasons"])
    flat = HC.capacity([{"hr": float(h), "p": 175.0 + (3.0 if i % 2 else -3.0)} for i, h in enumerate(hrs)], 155.0)
    assert not flat["valid"] and any("R²" in r for r in flat["reasons"])
    narrow = HC.capacity([{"hr": 140.0 + i, "p": 200.0 + i} for i in range(10)], 155.0)
    assert not narrow["valid"] and any("心率範圍" in r for r in narrow["reasons"])


def test_intensity_distribution_palladino_zones():
    runs = [{"p_avg": p, "moving_s": 3600.0} for p in (140.0, 150.0, 170.0, 185.0, 200.0)]
    d = HC.intensity_distribution(runs, 200.0)
    # independent: 0.70, 0.75 low; 0.85, 0.925 mid; 1.00 high
    assert [b["runs"] for b in d["bands"]] == [2, 2, 1]
    assert d["median"] == pytest.approx(0.85)
    assert sum(b["share_time"] for b in d["bands"]) == pytest.approx(1.0)


def test_hr_psus_matches_the_formulas():
    cap = {"tte": 1900.0, "k": -0.07}
    hc = {"p_lthr": 190.0}
    wp = 13100.0
    ps = BT.hr_psus(hc, 720.0, cap, wp)
    # tt30: b = min(1200, 1800/2) = 900 ≥ 720 → F2: P + W′/t
    assert ps["tt30"] == pytest.approx(190.0 + wp / 720.0)
    # tte 1900: b = 950 → F2 too
    assert ps["tte"] == pytest.approx(190.0 + wp / 720.0)
    t = 8484.0
    ps = BT.hr_psus(hc, t, cap, wp)
    assert ps["tt30"] == pytest.approx(190.0 * (t / 1800.0) ** -0.07)
    assert ps["tte"] == pytest.approx(190.0 * (t / 1900.0) ** -0.07)
    assert BT.hr_psus({"p_lthr": None}, t, cap, wp) == {}


def test_combined_rule_is_a_second_lower_bound_only_when_few():
    cap = {"tte": 1900.0, "k": -0.07}
    ctx = {"hrcap": {"p_lthr": 200.0, "valid": True}, "w_prime_prior": 13100.0, "n_max": 1}
    def low(t):
        return 170.0
    r = BT._hr_eval(ctx, cap, 8484.0, 184.0, low)
    want = 200.0 * (8484.0 / 1900.0) ** -0.07
    assert r["hr"]["p_sus_comb"] == pytest.approx(max(170.0, want))
    r = BT._hr_eval({**ctx, "n_max": BT.FEW_MAXIMAL}, cap, 8484.0, 184.0, low)
    assert r["hr"]["p_sus_comb"] == pytest.approx(170.0)
    assert r["err_p_comb"] == pytest.approx(170.0 / 184.0 - 1.0)
    # an invalid HR fit is reported but never used as a bound
    r = BT._hr_eval({**ctx, "hrcap": {"p_lthr": 200.0, "valid": False}}, cap, 8484.0, 184.0, low)
    assert r["hr"]["p_sus_comb"] == pytest.approx(170.0) and r["err_p_hr_tte"] == pytest.approx(want / 184.0 - 1.0)


def test_summary_reports_hr_columns():
    rows = [{"category": "road", "f": 1.0, "err_c": -0.1, "err_p": 0.05, "err_p_hr_tte": -0.02,
             "err_c_hr_tte": 0.01, "err_p_comb": 0.05, "cap_kind": "plan_race"},
            {"category": "test", "f": 1.0, "err_p": -0.04, "err_p_hr_tt30": 0.03, "cap_kind": "cp_test"}]
    s = BT.summarise_capacity(rows, [])
    assert s["categories"]["road"]["kinds"] == {"plan_race": 1}
    assert s["categories"]["road"]["hr"]["hr_tte"]["power"]["bias"] == pytest.approx(-0.02)
    assert s["categories"]["road"]["hr"]["combined"]["time"]["bias"] == pytest.approx(-0.1)   # falls back to err_c
    assert s["tests"]["hr"]["hr_tt30"]["power"]["bias"] == pytest.approx(0.03)
