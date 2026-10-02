"""Single-activity HR / power charts (panels/activity_charts.py): the brushed-
range stats, time in zones per model, and WKO5's Heart Rate Variation and
Trend. Synthetic data only (no WKO5 folder, no real DB)."""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from backend.engine.panels import activity_charts as A
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

DAY = dt.date(2026, 9, 30)


def _run(t, hr, p=None, v=None, gap_at=None):
    t = np.asarray(t, dtype=float)
    v = np.full(len(t), 10.8) if v is None else np.asarray(v, dtype=float)
    ch = {"elapsedtime": list(t), "heartrate": list(hr), "speed": list(v),
          "elapseddistance": list(np.cumsum(np.diff(t, prepend=t[0]) * v) / 3600.0)}
    if p is not None:
        ch["power"] = list(p)
    return FakeWorkout(start=dt.datetime.combine(DAY, dt.time(7)), sport="run", tags=["running"],
                       sport_type="running", channels=ch,
                       metrics={"duration": float(t[-1]), "movingduration": float(t[-1])})


def _ds(*runs, **settings):
    s = {"runthr": 160.0, "runftp": 250.0}
    s.update(settings)
    return FakeDataset(list(runs), DAY, settings=s)


def _steady(minutes=60, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(0, minutes * 60 + 1, 1.0)
    hr = 130 + 15 * t / t[-1] + rng.normal(0, 2, len(t))
    p = 220 + 30 * np.sin(t / 50.0) + rng.normal(0, 10, len(t))
    v = 10 + 2 * np.sin(t / 300.0)
    v[1000:1060] = 0.5                        # a stop: not moving
    return t, hr, p, v


# ---------------------------------------------------------------------------
# hrpower: running totals → exact stats of a brushed range
# ---------------------------------------------------------------------------

def _direct(t, hr, p, v, a, b):
    """Stats of grid seconds [a, b) computed directly (1-s data, no gaps)."""
    h, pw = hr[a:b], p[a:b]
    r30 = A.rolling(np.asarray(p, float), 30)[a:b]
    r30 = r30[np.isfinite(r30)]
    dist = np.cumsum(np.diff(t, prepend=t[0]) * v) / 3600.0
    mov = float(np.sum(v[a:b] > 1.6))
    km = float(dist[b] - dist[a]) if b < len(dist) else float(dist[-1] - dist[a])
    npw = float(np.mean(r30 ** 4) ** 0.25)
    return {"hr_avg": float(np.mean(h)), "hr_max": float(np.max(np.round(h))), "p_avg": float(np.mean(pw)),
            "np": npw, "moving_s": mov, "km": km}


@pytest.mark.parametrize("points", [5000, 300])
def test_brushed_range_stats_match_a_direct_computation(points):
    t, hr, p, v = _steady()
    ds = _ds(_run(t, hr, p, v))
    res = A.hrpower(ds, ds.workouts[0], points=points)
    assert res["has_hr"] and res["has_power"] and res["empty"] is None
    step = res["step"]
    K = len(res["x"])
    for ka, kb in ((0, K), (K // 4, K // 2), (3, K - 7)):
        s = A.range_stats(res, ka, kb)
        a, b = ka * step, min(kb * step, res["n"])
        d = _direct(t, hr, p, v, a, b)
        assert s["elapsed_s"] == b - a
        assert s["hr_avg"] == pytest.approx(d["hr_avg"], abs=1e-3)
        assert s["p_avg"] == pytest.approx(d["p_avg"], abs=1e-3)
        assert s["np"] == pytest.approx(d["np"], rel=1e-4)
        assert s["moving_s"] == pytest.approx(d["moving_s"])
        assert s["km"] == pytest.approx(d["km"], abs=2e-4)
        assert s["pace_s_per_km"] == pytest.approx(s["moving_s"] / s["km"])
        assert s["pw_hr"] == pytest.approx(s["p_avg"] / s["hr_avg"])
        assert s["ef"] == pytest.approx(s["np"] / s["hr_avg"])
        if step == 1:
            assert s["hr_max"] == pytest.approx(d["hr_max"], abs=0.5)


def test_constant_power_np_and_the_halves_drift():
    t = np.arange(0, 3601, 1.0)
    hr = np.where(t < 1800, 140.0, 154.0)      # HR up 10 % in the second half, power flat
    p = np.full(len(t), 200.0)
    ds = _ds(_run(t, hr, p))
    res = A.hrpower(ds, ds.workouts[0])
    s = A.range_stats(res, 0, len(res["x"]))
    assert s["np"] == pytest.approx(200.0, abs=1e-6) and s["p_avg"] == pytest.approx(200.0)
    e1, e2 = 200 / 140, 200 / 154
    assert s["pw_hr_drift"] == pytest.approx((e1 - e2) / e1, abs=2e-3)
    assert res["ref"]["lthr"] == 160 and res["ref"]["cp"] == 250


def test_no_power_keeps_hr_stats_and_says_nothing_about_power():
    t = np.arange(0, 1201, 1.0)
    ds = _ds(_run(t, np.full(len(t), 150.0)))
    res = A.hrpower(ds, ds.workouts[0])
    assert res["has_hr"] and not res["has_power"] and res["power"] is None
    s = A.range_stats(res, 0, len(res["x"]))
    assert s["hr_avg"] == pytest.approx(150.0) and s["p_avg"] is None and s["np"] is None and s["pw_hr"] is None


def test_a_recording_gap_counts_as_elapsed_but_not_moving():
    t = np.concatenate([np.arange(0, 600, 1.0), np.arange(900, 1500, 1.0)])   # a 5-min pause
    ds = _ds(_run(t, np.full(len(t), 140.0), np.full(len(t), 210.0)))
    res = A.hrpower(ds, ds.workouts[0])
    s = A.range_stats(res, 0, len(res["x"]))
    assert s["elapsed_s"] == 1500
    assert s["moving_s"] == pytest.approx(1199, abs=2)
    assert s["hr_avg"] == pytest.approx(140.0)


# ---------------------------------------------------------------------------
# time in zones
# ---------------------------------------------------------------------------

def _model(res, mid):
    return next(m for m in res["models"] if m["id"] == mid)


def test_friel_zones_count_seconds_at_the_lthr_boundaries():
    # 600 s at each of 130, 140, 150, 158, 163 bpm (LTHR 160: Friel 1 < 136, 2 136–144, 3 144–152, 4 152–160, 5a 160–164.8)
    hr = np.repeat([130.0, 140.0, 150.0, 158.0, 163.0], 600)
    t = np.arange(len(hr), dtype=float)
    ds = _ds(_run(t, hr))
    res = A.zone_times(ds, ds.workouts[0], "hr")
    f = _model(res, "frielhr")
    assert f["available"] and [r["id"] for r in f["rows"]] == ["1", "2", "3", "4", "5a", "5b", "5c"]
    assert [r["seconds"] for r in f["rows"][:5]] == [600.0] * 5
    assert f["rows"][1]["from"] == 136 and f["rows"][1]["to"] == 144
    assert sum(r["share"] for r in f["rows"]) == pytest.approx(1.0)
    assert f["total_s"] == len(hr)
    # Seiler 3 zones on AeT (0.89 × LTHR without a plan) / LTHR
    s3 = _model(res, "seiler3")
    assert s3["available"] and [r["seconds"] for r in s3["rows"]] == [1200.0, 1200.0, 600.0]
    # RQ needs a resting HR the app doesn't have
    rq = _model(res, "rqhrr")
    assert not rq["available"] and "靜息心率" in rq["reason"]
    assert res["default"] == "frielhr" and res["empty"] is None


def test_no_hrmax_model_even_with_an_hrmax_setting():
    # %HRmax zones were dropped (zones-and-thresholds.md §2.1: LT at 60–90 % HRmax, Iannetta 2020);
    # a remembered 「hrmax5」 is not in the list, so the viewer falls back to res["default"]
    hr = np.repeat([110.0, 125.0, 160.0, 185.0], 300)
    t = np.arange(len(hr), dtype=float)
    ds = _ds(_run(t, hr), runmhr=190.0)
    res = A.zone_times(ds, ds.workouts[0], "hr")
    assert [m["id"] for m in res["models"]] == ["frielhr", "classichr", "seiler3", "rqhrr"]
    assert res["default"] == "frielhr"
    assert not hasattr(A.Z, "HRMAX5_ZONES")


def test_power_zone_models_and_ilevels_unavailable_without_a_model(monkeypatch):
    p = np.repeat([150.0, 210.0, 240.0, 260.0, 300.0], 300)     # CP 250
    t = np.arange(len(p), dtype=float)
    ds = _ds(_run(t, np.full(len(p), 150.0), p))
    monkeypatch.setattr(A, "ilevels_for", lambda ds, w: None)
    res = A.zone_times(ds, ds.workouts[0], "power")
    # power zones are Palladino's everywhere (owner 2026-10-02): no Coggan / Stryd sets
    assert [m["id"] for m in res["models"]] == ["ilevels", "palladino", "palladino3"]
    assert res["default"] == "palladino"          # Palladino % CP (zones-and-thresholds.md §3.2)
    il = _model(res, "ilevels")
    assert not il["available"] and "iLevels" in il["reason"]
    p3 = _model(res, "palladino3")  # 80 % / 95 % CP = 200 / 237.5
    assert [r["seconds"] for r in p3["rows"]] == [300.0, 300.0, 900.0]
    # Palladino's table starts at 50 % CP; below that counts as 1A, so every second has a zone
    pal = _model(res, "palladino")
    assert pal["rows"][0]["from"] == 0 and pal["total_s"] == len(p)


def test_ilevels_rows_come_from_the_wko5_levelto_expression(monkeypatch):
    tops = [109.5, 148.6, 172.1, 185.8, 205.3, 266.0, 370.0, 518.9]
    seen = []

    class Ev:
        def __init__(self, *a, **k):
            pass

        def evaluate(self, expr, w=None):
            seen.append(expr)
            return tops[int(expr.rsplit(",", 1)[1].rstrip(")"))]
    import backend.engine.wko5expr.evaluator as E
    monkeypatch.setattr(E, "Evaluator", Ev)
    t = np.arange(0, 600, 1.0)
    ds = _ds(_run(t, np.full(len(t), 150.0), np.full(len(t), 200.0)))
    lv = A.ilevels_for(ds, ds.workouts[0])
    assert [r[0] for r in lv] == ["1", "2", "3", "4a", "4", "5", "6", "7a", "7"]
    assert lv[0][2:] == (0.0, 109.5) and lv[4][2:] == (185.8, 205.3) and lv[-1][2:] == (518.9, None)
    assert seen[0] == "levelto(athleterange(date-89,date,(meanmax(power))),0)"
    m = _model(A.zone_times(ds, ds.workouts[0], "power"), "ilevels")
    assert m["available"] and m["rows"][4]["seconds"] == 600.0       # 200 W is in FTP (185.8–205.3)
    assert "mFTP 196 W" in m["basis_text"]                            # 205.3 / 1.05


# ---------------------------------------------------------------------------
# WKO5 Heart Rate Variation and Trend
# ---------------------------------------------------------------------------

def test_hr_variation_matches_the_expression_engine():
    from backend.engine.wko5expr.evaluator import Evaluator
    t, hr, p, v = _steady(50, seed=3)
    ds = _ds(_run(t, hr, p, v))
    w = ds.workouts[0]
    r = A.hr_variation(t, hr, p, v)
    ev = Evaluator(ds, math.floor(w.day), math.floor(w.day))
    for key, expr in (("avg", "avg(heartrate)"), ("sd", "stddev(heartrate)"),
                      ("slope", "slrm(heartrate)"), ("intercept", "slrb(heartrate)")):
        assert r[key] == pytest.approx(float(ev.evaluate(expr, w)), rel=1e-9), key
    assert r["variability"] == "steady"                  # SD / avg ≈ 0.04
    assert r["trend"] == "increasing"                    # +15 bpm over 50 min = 0.005 bpm/s


def test_hr_variation_classes_and_pwhr_halves():
    t = np.arange(0, 3601, 1.0)
    flat = A.hr_variation(t, np.full(len(t), 140.0) + np.sin(t), np.full(len(t), 200.0))
    assert flat["trend"] == "consistent" and flat["decoupling"] == pytest.approx(0.0, abs=1e-3)
    down = A.hr_variation(t, 160 - t * 0.001, None)
    assert down["trend"] == "declining" and down["decoupling"] is None
    hr = np.where(t <= 1800, 140.0, 150.0)
    r = A.hr_variation(t, hr, np.full(len(t), 210.0))
    e1, e2 = 210 / 140, 210 / 150
    assert r["decoupling"] == pytest.approx((e1 - e2) / e1, abs=1e-9) and r["decoupling_basis"] == "pwhr"
    pa = A.hr_variation(t, hr, None, np.full(len(t), 10.0))
    assert pa["decoupling_basis"] == "pahr" and pa["decoupling"] == pytest.approx((e1 - e2) / e1, abs=1e-9)
    assert A.hr_variation(t, np.where(t % 2 == 0, 70.0, 170.0), None)["variability"] == "mixed"     # CV ≈ .42
    assert A.hr_variation(t, np.where(t % 2 == 0, 30.0, 210.0), None)["variability"] == "variable"  # CV ≈ .75


def test_hrtrend_payload():
    t, hr, p, v = _steady(40)
    ds = _ds(_run(t, hr, p, v))
    res = A.hrtrend(ds, ds.workouts[0])
    assert res["empty"] is None and res["trend_label"] == "上升" and res["variability_label"] == "穩定"
    assert len(res["trend_line"]) == 2 and res["trend_line"][0][0] == 0
    assert res["change_bpm"] == pytest.approx(15.0, abs=1.0)
    assert res["slope_bpm_h"] == pytest.approx(15.0 * 60 / 40, abs=1.5)


# ---------------------------------------------------------------------------
# view wiring
# ---------------------------------------------------------------------------

def test_activity_kind_parses_and_rejects_unknown_charts():
    from backend.engine.wko5expr.customviews import CustomViewError, parse_view
    v = parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [
        {"title": "a", "kind": "activity", "chart": "hrpower"}]}]})
    assert v["dashboards"][0]["charts"][0]["chart"] == "hrpower"
    with pytest.raises(CustomViewError):
        parse_view({"name": "x", "dashboards": [{"title": "d", "charts": [
            {"title": "a", "kind": "activity", "chart": "nope"}]}]})


def test_bundled_view_has_the_charts_and_the_api_renders_them():
    from backend.api.wko5views import _panel_kind, _render
    from backend.engine.wko5expr.customviews import REPO_VIEWS, load_custom_views
    v = load_custom_views([REPO_VIEWS])["單次活動判讀"]
    charts = {c["title"]: (di, c) for di, d in enumerate(v["dashboards"]) for c in d["charts"]}
    assert charts["心率與功率（拖曳選一段看統計）"][0] == 0
    for title, name in (("心率變化與趨勢", "hrtrend"), ("心率區間時間", "hrzones"), ("功率區間時間", "powerzones")):
        di, c = charts[title]
        assert di == 1 and c["chart"] == name and _panel_kind(c) == "workout"
    # the old dual-axis HR + power chart (BPM and WATTS on one plot) is gone from 本次重點
    assert not any({"BPM", "WATTS"} <= {a["id"] for a in c["axes"]} for c in v["dashboards"][0]["charts"])
    t, hr, p, vv = _steady(30)
    ds = _ds(_run(t, hr, p, vv))
    for title in ("心率與功率（拖曳選一段看統計）", "心率變化與趨勢", "心率區間時間"):
        res = _render(charts[title][1], ds, ds.today, ds.today, None, ds.workouts[0])
        assert res["kind"].startswith("act_") and res["title"] == title and not res.get("empty")
