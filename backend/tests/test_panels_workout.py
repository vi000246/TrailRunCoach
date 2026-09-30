"""backend/engine/panels/{workout,mountain}.py on synthetic streams."""
import datetime as dt

import numpy as np
import pytest

from backend.engine.panels import mountain as MT
from backend.engine.panels import workout as WK


def test_grade_bins_time_pace_and_averages():
    n = 1200
    dt_s = [1.0] * n
    grade = [0.0] * 600 + [0.12] * 600
    speed = [3.0] * 600 + [1.5] * 600          # m/s
    dist = np.cumsum(np.array(speed) / 1000.0)
    hr = [140.0] * 600 + [160.0] * 600
    rows = WK.grade_bins(dt_s, grade, dist, power=[250.0] * n, hr=hr, cadence=[90.0] * n)
    by = {r["label"]: r for r in rows}
    flat, up = by["-2 ~ 2%"], by["10 ~ 15%"]
    assert flat["time_s"] == 600 and up["time_s"] == 600
    assert flat["pace_s_per_km"] == pytest.approx(1000 / 3.0, rel=0.01)
    assert up["pace_s_per_km"] == pytest.approx(1000 / 1.5, rel=0.01)
    assert up["hr"] == pytest.approx(160) and flat["hr"] == pytest.approx(140)
    assert sum(r["time_pct"] for r in rows) == pytest.approx(100)
    assert len(rows) == 2  # empty buckets dropped


def test_grade_bins_drop_pauses():
    rows = WK.grade_bins([1.0, 300.0], [0.0, 0.0])
    assert rows[0]["time_s"] == 1.0


def test_rolling_mean_waits_for_a_full_window():
    t = np.arange(10.0)
    r = WK.rolling_mean(t, np.arange(10.0), 3.0)
    assert np.isnan(r[1])
    assert r[3] == pytest.approx((1 + 2 + 3) / 3)


def test_durability_flat_when_efficiency_holds():
    t = np.arange(0, 7200.0)
    d = WK.durability(t, np.full(len(t), 3.0), np.full(len(t), 140.0))
    assert d["end_pct"] == pytest.approx(100)
    assert d["decoupling_pct"] == pytest.approx(0, abs=1e-9)


def test_durability_drops_with_cardiac_drift():
    t = np.arange(0, 7200.0)
    hr = 140.0 + 14.0 * t / 7200.0                  # +10% HR by the end
    d = WK.durability(t, np.full(len(t), 3.0), hr)
    assert 88 < d["end_pct"] < 95
    assert d["points"][0][1] == pytest.approx(100, abs=0.5)


def test_durability_needs_enough_data():
    assert WK.durability(np.arange(0, 600.0), np.ones(600), np.full(600, 140.0)) is None


def test_time_in_zones():
    z = WK.time_in_zones([100, 150, 150, 200], [1, 1, 1, 1], [0, 120, 180], ["Z1", "Z2", "Z3"])
    assert [x["time_s"] for x in z] == [1, 2, 1]
    assert z[1]["pct"] == pytest.approx(50)


def test_cumulative_kj():
    assert WK.cumulative_kj([200.0] * 10, [1.0] * 10)[-1] == pytest.approx(2.0)


# ---- mountain ---------------------------------------------------------------

def test_fit_line_and_vam_at():
    f = MT.fit_line([5, 10, 15, 20], [400, 600, 800, 1000])
    assert f["b"] == pytest.approx(40) and f["a"] == pytest.approx(200)
    assert MT.vam_at(f, 12) == pytest.approx(680)
    assert MT.fit_line([1, 2], [1, 2]) is None


def test_vam_vs_grade_windows():
    today = dt.date(2026, 9, 30)
    mk = lambda days, g, v: {"date": today - dt.timedelta(days=days), "grade": g, "vam_m_per_h": v}
    cl = [mk(10, 0.10, 700), mk(20, 0.15, 900), mk(30, 0.20, 1100),
          mk(100, 0.10, 600), mk(120, 0.15, 800), mk(150, 0.20, 1000), mk(400, 0.2, 1)]
    r = MT.vam_vs_grade(cl, today)
    assert len(r["recent"]) == 3 and len(r["earlier"]) == 3
    assert MT.vam_at(r["fit_recent"], 15) - MT.vam_at(r["fit_earlier"], 15) == pytest.approx(100)


def test_downhill_counts_only_steep_descent():
    n = 400
    g = [-0.15] * 200 + [-0.05] * 200
    dist = np.cumsum([0.002] * n)
    elev = 1000 - np.cumsum([0.3] * 200 + [0.1] * 200)
    r = MT.downhill([1.0] * n, g, dist, elev)
    assert r["time_s"] == 199 or r["time_s"] == 200
    assert r["loss_m"] == pytest.approx(0.3 * 199, rel=0.02)


def test_weekly_sum():
    d = dt.date(2026, 9, 2)
    w = MT.weekly_sum([(d, {"time_s": 60.0, "distance_km": 1.0, "loss_m": 100.0})], d, d)
    assert w == [{"week": "2026-08-31", "time_s": 60.0, "distance_km": 1.0, "loss_m": 100.0}]
