"""圖表分析 on a COROS / TP source (FitFolderDataset): VAM + hike tags, CP
before the first plan test (Stryd-only PD fit), threshold pace estimate,
Friel pace / Palladino zone tables with their sources, the 課表建議強度 trail
rows and the 4-week weekly-volume growth. Synthetic FITs only — never the
WKO5 folder, the real app DB or ~/.wko5coach."""
import datetime as dt
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from backend.engine.planning import Plan, Threshold
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.dataset import date_to_day
from backend.engine.wko5expr.evaluator import Evaluator
from backend.engine.wko5expr.fitdataset import FitFolderDataset
from backend.tests.fit_builder import build_run

TODAY = dt.date(2026, 9, 30)
UTC = timezone.utc


def _ds(tmp_path, runs, plan=None):
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True)
    for i, kw in enumerate(runs):
        (d / f"{i:03d}.fit").write_bytes(build_run(**kw))
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=EngineConfig(parity=True), today=TODAY,
                          estimate_thresholds=False, tz=UTC)
    ds.plan = plan or Plan()
    return ds


# ---- item 4: VAM and the hike tags --------------------------------------------

def test_vam_and_hike_tags(tmp_path):
    ds = _ds(tmp_path, [
        dict(start=datetime(2026, 9, 1, 0, tzinfo=UTC), seconds=3600, speed_m_s=1.0, climb_m_per_s=0.2, sport=17),
        dict(start=datetime(2026, 9, 2, 0, tzinfo=UTC), seconds=3600, speed_m_s=1.0, climb_m_per_s=0.2, sport=16),
        dict(start=datetime(2026, 9, 3, 0, tzinfo=UTC), seconds=3600, speed_m_s=2.5, climb_m_per_s=0.1,
             sub_sport=3),
    ])
    hike, mount, trail = ds.workouts
    assert hike.sport_type == "hiking" and hike.tags == ["hiking"]
    assert mount.sport_type == "mountaineering" and mount.tags == ["mountaineering"]
    assert trail.tags == ["runningtrail"]
    # WKO5 4224: climbing / duration * 3600
    assert hike.metrics["vam"] == pytest.approx(round(hike.metrics["climbing"] / 3600 * 3600), abs=1)
    assert hike.metrics["vam"] == pytest.approx(720, rel=0.05)
    ev = Evaluator(ds, ds.first_day, ds.last_day)
    hikes = ev.evaluate('if((hastag("hiking") or hastag("mountaineering")) and climbing > 200, vam)')
    assert len([v for v in hikes.values() if v == v]) == 2
    trails = ev.evaluate('if(hastag("runningtrail") and climbing > 200, vam)')
    assert len([v for v in trails.values() if v == v]) == 1


# ---- item 2: CP before the first plan test --------------------------------------

STRYD_DAYS = (1, 4, 7, 10, 13)


def _stryd_runs():
    return [dict(start=datetime(2026, 8, d, 0, tzinfo=UTC), seconds=1800, power=200, stryd=True) for d in STRYD_DAYS] + \
        [dict(start=datetime(2026, 8, 20, 0, tzinfo=UTC), seconds=1800, power=300)]          # watch power


def test_cp_fit_uses_stryd_runs_only(tmp_path, monkeypatch):
    from backend.engine.racepower import athlete as A
    ds = _ds(tmp_path, _stryd_runs())
    ds.accept_watch_power = False
    assert [ds.power_source(w) for w in ds.workouts] == ["stryd"] * 5 + ["watch"]
    seen = []

    def fake_pd(ds_, day, runs, ref_cp, any_power=False):
        seen.append((day, [w.entry.start.day for w in runs], any_power))
        return {"mftp": 210.0, "frc": 15000.0, "n_points": 40}
    monkeypatch.setattr(A, "pd_model", fake_pd)
    ds._estimate_cp(dt.date(2026, 8, 25), dt.date(2026, 9, 24))
    assert seen and all(runs == list(STRYD_DAYS) for _, runs, _ in seen)   # the watch run never enters
    assert all(not any_power for *_, any_power in seen)
    assert ds._cp_est[0]["cp"] == 210.0
    # fewer Stryd runs than CP_FIT_MIN_RUNS in the window: no fit at all
    seen.clear()
    ds._estimate_cp(dt.date(2026, 8, 8), dt.date(2026, 8, 8))
    assert seen == [] and ds._cp_est == []


def test_cp_plan_first_then_fit_then_unset(tmp_path):
    plan = Plan(thresholds=[Threshold(date="2026-09-30", cp=204.0)])
    ds = _ds(tmp_path, _stryd_runs() + [dict(start=datetime(2026, 9, 30, 0, tzinfo=UTC), seconds=1800, power=200,
                                               stryd=True)], plan)
    ds._cp_est = [{"date": dt.date(2026, 8, 15), "cp": 221.5, "frc_j": 14000.0, "runs": 2, "n_points": 40}]
    w1, w20, w30 = ds.workouts[0], ds.workouts[5], ds.workouts[6]
    assert (w1.entry.start.day, w20.entry.start.day, w30.entry.start.day) == (1, 20, 30)
    assert ds.cp(w1) is None                             # before any fit: no CP
    assert ds.cp(w20) == 221.5                           # the fit in effect on its date
    assert ds.cp(w30) == 204.0                           # the plan test wins from its date
    i = ds.cp_info(w20)
    assert i["cp"] == 221.5 and i["date"] == "2026-08-15" and "推估" in i["source"] and "Stryd" in i["source"]
    assert i["wprime"] == 14000.0 and "FRC" in i["wprime_source"]
    assert ds.cp_info(w30)["source"] == "你的測試 2026-09-30"
    assert ds.cp_info(w1)["cp"] is None


def test_palladino_zone_table_counts_runs_before_the_plan_test(tmp_path):
    from backend.engine.zones import zone_table
    plan = Plan(thresholds=[Threshold(date="2026-09-30", cp=200.0)])
    ds = _ds(tmp_path, [dict(start=datetime(2026, 9, 20, 0, tzinfo=UTC), seconds=1800, power=170, stryd=True)], plan)
    end = int(date_to_day(TODAY))
    # the run predates the test and has no CP of its own (no fit): the table
    # counts it against the CP its rows show (it used to read 0 s)
    assert ds.cp(ds.workouts[0]) is None
    z = zone_table(ds, "palladino", end)
    z2 = next(r for r in z["rows"] if r["id"] == "2")                           # 170 W = 85 % CP
    assert z2["seconds"] == pytest.approx(1800, abs=5) and z["threshold"] == 200.0
    assert z["threshold_source"] == "你的測試 2026-09-30"
    # the weekly chart's per-workout `cp` gets the Stryd fit before the test
    ds._cp_est = [{"date": dt.date(2026, 9, 1), "cp": 190.0, "frc_j": 15000.0, "runs": 6, "n_points": 40}]
    ds.memo.clear()
    ev = Evaluator(ds, ds.first_day, ds.last_day)
    assert ev.evaluate("cp", ds.workouts[0]) == 190.0
    w = ev.evaluate("sum(if(runpower >= 0.88*cp and runpower < 1.01*cp, deltatime))", ds.workouts[0])
    assert w == pytest.approx(1800, abs=5)                                      # 170 / 190 = 89 %: Z3


def test_threshold_pace_at_cp_from_stryd_road_runs(tmp_path):
    from backend.engine.thresholds import estimate_tpace
    plan = Plan(thresholds=[Threshold(date="2026-09-01", cp=200.0, lthr=160.0)])
    runs = [dict(start=datetime(2026, 9, d, 0, tzinfo=UTC), seconds=2400, hr=160, power=200, stryd=True,
                 speed_m_s=3.0) for d in (5, 12, 19)]
    ds = _ds(tmp_path, runs, plan)
    est = estimate_tpace(ds, TODAY)
    assert est["method"] == "cp"
    assert est["value"] == pytest.approx(1000 / (200 * 3.0 / 200) / 60, rel=0.02)    # 3 m/s at CP: 5:33 /km
    assert "推估" in est["reason"] and "CP 200 W" in est["reason"]


# ---- item 3: threshold pace --------------------------------------------------------

def test_tpace_run_fastest_20_min_at_lthr():
    from backend.engine.thresholds import _tpace_run
    t = np.arange(1, 3601, dtype=float)
    hr = np.where(t < 1800, 130.0, 160.0)                # easy first half, LTHR second half
    speed = np.where(t < 1800, 2.5, 3.0)                 # m/s
    d = np.cumsum(speed) / 1000.0
    r = _tpace_run(t, hr, d, 160.0)
    assert r["pace"] == pytest.approx(1000 / 3.0 / 60, rel=0.01)      # 5:33 /km
    assert r["hr"] == pytest.approx(160, abs=1)
    assert _tpace_run(t, np.full(3600, 120.0), d, 160.0) is None       # never near LTHR


def test_friel_pace_zones_use_the_estimate_with_its_source(tmp_path):
    from backend.engine.zones import zone_table
    plan = Plan(thresholds=[Threshold(date="2026-01-01", lthr=160.0)])
    runs = [dict(start=datetime(2026, 9, d, 0, tzinfo=UTC), seconds=2400, hr=160, speed_m_s=3.0) for d in (5, 12, 19)]
    ds = _ds(tmp_path, runs, plan)
    z = zone_table(ds, "frielpace", int(date_to_day(TODAY)))
    assert z["threshold"] == pytest.approx(1000 / 3.0 / 60, rel=0.01)
    assert "推估" in z["threshold_source"]
    assert z["total_seconds"] > 0
    # running at threshold pace itself: on the Zone 4 / 5a boundary (1.00)
    near = sum(r["share"] for r in z["rows"] if r["id"] in ("4", "5a"))
    assert near > 0.9


def test_friel_pace_without_enough_runs_says_why(tmp_path):
    from backend.engine.zones import zone_table
    ds = _ds(tmp_path, [dict(start=datetime(2026, 9, 5, 0, tzinfo=UTC), seconds=2400, hr=140)])
    z = zone_table(ds, "frielpace", int(date_to_day(TODAY)))
    assert z["threshold"] is None and z["total_seconds"] == 0
    assert z["no_data_reason"] and z["threshold_source"]


# ---- item 1: trail rows of 課表建議強度 -----------------------------------------------

def test_trail_rows_hr_first_hills_power_first(tmp_path):
    # 2026-10-01: trail long days by HR, hill repeats by power, long climbs are
    # suggestions only, downhill practice by feel (docs/research/vo2max-gate-and-trail-metric.md)
    from backend.engine.zones import training_targets
    plan = Plan(thresholds=[Threshold(date="2026-09-01", cp=200.0, lthr=160.0)])
    ds = _ds(tmp_path, [dict(start=datetime(2026, 9, 5, 0, tzinfo=UTC), seconds=1200)], plan)
    tt = training_targets(ds, int(date_to_day(TODAY)))
    rows = {r["id"]: r for r in tt["rows"]}
    assert rows["trail"]["primary"] == "心率" and rows["trail"]["hr"][1] is not None
    assert rows["trail"]["power"] == [pytest.approx(150), pytest.approx(176)]   # power kept as a reference
    assert rows["hill"]["primary"] == "功率" and all(v is not None for v in rows["hill"]["power"])
    assert rows["climb"]["primary"] == "建議" and "推估" in rows["climb"]["source"]
    assert rows["downhill"]["primary"] == "體感" and rows["downhill"]["power"] == [None, None]
    assert "推估" in rows["hill"]["source"]
    assert "下坡" in tt["terrain_note"] and tt["cp_source"] == "你的測試 2026-09-01"


# ---- item 5: weekly volume growth -----------------------------------------------------

def _chart(title):
    v = json.loads((Path(__file__).resolve().parents[2] / "views" / "periodization.json").read_text("utf-8"))
    return next(c for d in v["dashboards"] for c in d["charts"] if c["title"] == title)


def test_weekly_growth_is_the_4_week_mean_change(tmp_path):
    # Mondays from 2026-06-01: weekly hours 2, 2, 2, 2, 4 (a big week), 0 (nothing), 2
    hours = [2, 2, 2, 2, 4, 0, 2]
    runs = []
    for k, h in enumerate(hours):
        if h:
            runs.append(dict(start=datetime(2026, 6, 1, 0, tzinfo=UTC) + dt.timedelta(days=7 * k),
                             seconds=int(h * 3600)))
    ds = _ds(tmp_path, runs)
    expr = _chart("訓練量週增幅")["series"][0]["expression"]
    b = date_to_day(dt.date(2026, 6, 1))
    r = Evaluator(ds, b, b + 7 * len(hours) - 1).evaluate(expr)
    got = {int(round(d - b)) // 7: v for d, v in ((r.start + i, x) for i, x in enumerate(r.values)) if v == v}
    # week k: (W_k − W_{k−4}) / (W_{k−1} + … + W_{k−4})
    assert got[4] == pytest.approx((4 - 2) / 8, rel=0.01)          # +25 %, not the +100 % week-over-week
    assert got[5] == pytest.approx((0 - 2) / 10, rel=0.01)         # an empty week counts 0 h
    assert got[6] == pytest.approx((2 - 2) / 8, rel=0.01)
    assert 3 not in got                                            # needs 4 earlier weeks
    assert all(not math.isinf(v) for v in got.values())
