"""SP-642: heart rate on the structure chart's common intensity scale (threshold = 1.0).

  * the unsourced _HR_P table is gone; with too little data the scale comes from Friel's run HR
    and pace zones paired by zone (engine/hr_power_calib.py, marked 推估);
  * with enough steady runs (power + HR) the runner's own line through the threshold — an
    engine/calibrate.py item (stored, refitted weekly, read without refitting);
  * the TSS estimate and the chart read the same conversion;
  * a 儲備心率 step says % HRR next to its bpm.
"""
from __future__ import annotations

import pytest

from backend.engine import calibrate as CAL
from backend.engine import hr_power_calib as HPC
from backend.engine import workout_steps as WS
from backend.engine import zones as Z


@pytest.fixture(autouse=True)
def _nothing_stored(monkeypatch):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: None)


def _fitted(monkeypatch, slope, n=12):
    monkeypatch.setattr(CAL, "stored_entry", lambda name, user_id=1: (
        {"value": slope, "n": n, "w": 0.5, "personal": slope, "source": "fitted"} if name == HPC.NAME else None))


# ---- the unsourced table is gone ----------------------------------------------------------

def test_the_unsourced_table_is_gone():
    assert not hasattr(WS, "_HR_P")
    src = open(WS.__file__, encoding="utf-8").read()
    assert "(.70, .62)" not in src and "(.90, .82)" not in src


# ---- the literature fallback: Friel's zones, paired ------------------------------------------

def test_friel_points_come_from_the_zone_tables():
    # Friel run HR zone lower edges ↔ the same zone's slower pace edge (speed = 1 ÷ pace)
    hr = {z: lo for z, _n, lo, _hi in Z.FRIEL_HR}
    pace = {z: hi for z, _n, _lo, hi in Z.FRIEL_PACE}
    want = sorted((hr[z], 1 / pace[z]) for z in ("2", "3", "4", "5a", "5b", "5c"))
    assert HPC.FRIEL_POINTS == pytest.approx(want)
    assert dict(HPC.FRIEL_POINTS)[1.0] == pytest.approx(1.0)          # threshold = threshold


@pytest.mark.parametrize("f,want", [(0.85, 1 / 1.29), (0.90, 1 / 1.14), (0.95, 1 / 1.06), (1.0, 1.0),
                                    (1.03, 1 / 0.97), (1.06, 1 / 0.90)])
def test_literature_at_the_zone_edges(f, want):
    assert HPC.literature(f) == pytest.approx(want)
    assert WS.hr_to_p(f) == pytest.approx(want)                       # nothing stored: the literature


def test_literature_is_monotone_and_bounded():
    xs = [0.4 + 0.01 * i for i in range(90)]
    ys = [HPC.literature(x) for x in xs]
    assert all(b >= a for a, b in zip(ys, ys[1:]))
    assert min(ys) == pytest.approx(HPC.FLOOR) and max(ys) == pytest.approx(HPC.CEIL)
    assert HPC.FLOOR == 0.50 and HPC.CEIL == Z.Z5_TOP


def test_the_default_says_estimate():
    d = HPC.describe()
    assert d["source"] == "default" and d["estimate"] is True and "推估" in d["text"]
    assert "Friel" in d["text"]
    assert d["points"][0][0] == 0.6 and d["points"][-1][0] == pytest.approx(1.1)
    assert HPC.DEFAULT_SLOPE == pytest.approx((1 - 1 / 1.29) / 0.15, abs=1e-3)
    assert HPC.BOUNDS[0] <= HPC.DEFAULT_SLOPE <= HPC.BOUNDS[1]


# ---- the personal fit ------------------------------------------------------------------------

def _pts(slope, xs):
    return [{"x": x, "y": 1 + slope * (x - 1)} for x in xs]


def test_fit_recovers_the_runners_slope():
    xs = [0.78, 0.80, 0.82, 0.84, 0.85, 0.86, 0.88, 0.90, 0.92]
    f = HPC.fit_points(_pts(1.8, xs))
    assert f.n == 9 and f.value == pytest.approx(1.8)


def test_fit_is_robust_to_an_outlier_and_drops_unusable_points():
    xs = [0.80, 0.82, 0.84, 0.85, 0.86, 0.88, 0.90, 0.92]
    pts = _pts(1.6, xs) + [{"x": 0.83, "y": 0.2},             # power dropout: out of Y_RANGE
                           {"x": 0.995, "y": 0.99},            # too close to the threshold
                           {"x": 1.04, "y": 1.05},             # above threshold
                           {"x": 0.81, "y": 0.95}]             # an optical-HR low: one odd slope
    f = HPC.fit_points(pts)
    assert f.n == 9 and f.value == pytest.approx(1.6, abs=0.02)


def test_fit_is_clipped_to_the_physiological_range():
    xs = [0.80, 0.85, 0.90] * 3
    assert HPC.fit_points(_pts(4.0, xs)).value == HPC.BOUNDS[1]
    assert HPC.fit_points(_pts(0.6, xs)).value == HPC.BOUNDS[0]
    assert HPC.fit_points([]) is None


def test_below_the_minimum_nothing_is_written():
    item = CAL._registry()[HPC.NAME]
    assert item.min_n == HPC.MIN_RUNS == 8
    assert CAL.shrink(item, HPC.fit_points(_pts(1.8, [0.8, 0.82, 0.84, 0.86, 0.88, 0.9, 0.92]))) is None
    e = CAL.shrink(item, HPC.fit_points(_pts(1.8, [0.8, 0.82, 0.84, 0.86, 0.88, 0.9, 0.92, 0.85])))
    assert e["source"] == "fitted" and HPC.DEFAULT_SLOPE < e["value"] < 1.8


def test_the_stored_fit_is_what_the_chart_reads(monkeypatch):
    calls = []
    monkeypatch.setattr(HPC, "fit", lambda *a, **k: calls.append(1))
    _fitted(monkeypatch, 1.8)
    assert WS.hr_to_p(0.90) == pytest.approx(1 + 1.8 * -0.1)
    assert WS.hr_to_p(1.06) == pytest.approx(HPC.literature(1.06))   # above threshold: the literature
    c = WS.Ctx(cp=300.0, lthr=170.0)
    r = WS.resolve({"kind": "work", "target": {"type": "hr", "mode": "abs", "lo": 150, "hi": 156}}, c)
    assert r.frac == pytest.approx(1 + 1.8 * (153 / 170 - 1))
    assert calls == []                                                 # reading never refits
    d = HPC.describe()
    assert d["source"] == "fitted" and not d["estimate"] and "12" in d["text"]


def test_view_carries_the_hr_scale_only_with_hr_steps():
    c = WS.Ctx(cp=300.0, lthr=170.0)
    hr = WS.normalize({"items": [{"kind": "work", "dur": {"type": "time", "value": 600},
                                  "target": {"type": "hr", "mode": "abs", "lo": 150, "hi": 156}}]})
    pw = WS.normalize({"items": [{"kind": "work", "dur": {"type": "time", "value": 600},
                                  "target": {"type": "power", "mode": "pct", "lo": 0.9, "hi": 0.95}}]})
    v = WS.view(hr, c)
    assert v["hr_scale"]["estimate"] is True and v["order"][0]["type"] == "hr"
    assert WS.view(pw, c)["hr_scale"] is None


# ---- TSS follows the same conversion ---------------------------------------------------------

def test_tss_of_an_hr_step_uses_the_conversion(monkeypatch):
    c = WS.Ctx(cp=300.0, lthr=170.0)
    steps = WS.normalize({"items": [{"kind": "work", "dur": {"type": "time", "value": 3600},
                                     "target": {"type": "hr", "mode": "pct", "lo": 0.93, "hi": 0.97}}]})
    f = HPC.literature(0.95)
    assert WS.totals(steps, c)["tss"] == pytest.approx(f * f * 100, abs=0.2)
    _fitted(monkeypatch, 2.0)
    f2 = 1 + 2.0 * (0.95 - 1)
    assert WS.totals(steps, c)["tss"] == pytest.approx(f2 * f2 * 100, abs=0.2)


# ---- 儲備心率: % HRR next to the bpm ----------------------------------------------------------

def test_hrr_step_says_percent_hrr():
    hrz = {"model": "hrr", "rows": [{"id": f"Z{i}", "lo": None, "hi": None} for i in range(1, 7)],
           "mhr": 190.0, "rhr": 50.0}
    c = WS.Ctx(cp=300.0, lthr=170.0, hrz=hrz)
    r = WS.resolve({"kind": "work", "target": {"type": "hr", "mode": "abs", "lo": 148, "hi": 155}}, c)
    # (148 − 50) ÷ 140 = 70 %, (155 − 50) ÷ 140 = 75 %
    assert "70–75% HRR" in r.sub and r.text == "148–155 bpm"
    # the height still comes from bpm ÷ LTHR
    assert r.frac == pytest.approx(HPC.literature(151.5 / 170))
    # the LTHR model: % LTHR as before
    r2 = WS.resolve({"kind": "work", "target": {"type": "hr", "mode": "abs", "lo": 148, "hi": 155}},
                    WS.Ctx(cp=300.0, lthr=170.0))
    assert "% LTHR" in r2.sub and "HRR" not in r2.sub


def test_plan_hr_zones_carry_the_rest_hr():
    from backend.engine import hr_profile as HP
    z = HP.plan_hr_zones(170.0, None, False, 190.0, 50.0, "hrr")
    assert z["model"] == "hrr" and z["rhr"] == 50.0
    assert HP.plan_hr_zones(170.0, None, False, 190.0, 50.0, "lthr")["rhr"] is None


# ---- the data: steady flat stretches, drift and late windows left out --------------------------

def test_run_points_take_steady_flat_stretches_without_drift(monkeypatch):
    import datetime as dt
    from types import SimpleNamespace as NS
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import date_to_day
    today = dt.date(2026, 10, 1)

    def run(idx, days_ago, hr, p, drift=0.02, trail=False, late_hr=None):
        return NS(idx=idx, day=date_to_day(today - dt.timedelta(days=days_ago)), drift=drift, trail=trail,
                  hr=hr, p=p, late_hr=late_hr, entry=NS(start=dt.datetime(2026, 9, 1, 7)))

    runs = [run(1, 5, 150, 240), run(2, 10, 150, 240, drift=0.09), run(3, 15, 150, 240, trail=True),
            run(4, 200, 150, 240), run(5, 20, 140, 220, late_hr=175)]

    def windows(_ds, ws):
        w = ws[0]
        out = []
        for k in range(60):                       # 100 m windows, 25 s each: 0 … 25 min
            t = 600 + k * 25
            out.append({"g": 0.0, "v": 4.0, "p": w.p, "hr": w.hr, "hr_lag": w.hr, "run": 1.0, "k": k, "t": t})
        if w.late_hr:                              # after the first hour: drifted, never read
            out += [{"g": 0.0, "v": 4.0, "p": w.p, "hr": w.late_hr, "hr_lag": w.late_hr, "run": 1.0, "k": 100 + k,
                     "t": 3700 + k * 25} for k in range(60)]
        return out
    monkeypatch.setattr(A, "thresholds_as_of", lambda ds, d: {"lthr": 170.0})
    monkeypatch.setattr(A, "cp_as_of", lambda ds, d: 300.0)
    monkeypatch.setattr(A, "outdoor", lambda w: True)
    monkeypatch.setattr(A, "is_trail", lambda w: w.trail)
    monkeypatch.setattr(A, "power_ok", lambda ds, w: True)
    monkeypatch.setattr(A, "intensity_stats", lambda ds, w: {"drift": w.drift})
    monkeypatch.setattr(A, "grade_samples", windows)
    pts = HPC.run_points(NS(workouts=runs), today)
    assert sorted(p["idx"] for p in pts) == [1, 5]          # drift, trail, outside the window: out
    one = next(p for p in pts if p["idx"] == 1)
    assert one["x"] == pytest.approx(150 / 170) and one["y"] == pytest.approx(240 / 300)
    five = next(p for p in pts if p["idx"] == 5)
    assert five["x"] == pytest.approx(140 / 170)             # the drifted windows after 1 h are not read


def test_below_friel_zone_2_the_chord_goes_on():
    # Zone 1 is open: the line from 85 % LTHR to the threshold, not Zone 2's steeper slope
    assert HPC.literature(0.75) == pytest.approx(1 / 1.29 - 0.10 * HPC.DEFAULT_SLOPE)
    assert HPC.literature(0.60) == HPC.FLOOR
