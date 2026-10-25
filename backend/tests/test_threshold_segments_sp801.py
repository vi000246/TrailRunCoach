"""SP-801: 閾值段 (engine/threshold_segments.py) and the Zone 3 by power OR heart rate
(interval_reps.z3_on / z3_bouts) on synthetic data."""
import datetime as dt

import numpy as np

from backend.engine import interval_reps as IR
from backend.engine import threshold_segments as TS
from backend.engine import workout_review as R
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

CP, LTHR = 200.0, 160.0


def _flat(minutes_blocks):
    """[(minutes, power, hr), …] at 1 Hz on the flat: t, power, hr, elev, dist (10 km/h)."""
    p, h = [], []
    for mins, pw, hr in minutes_blocks:
        p += [pw] * int(mins * 60)
        h += [hr] * int(mins * 60)
    t = np.arange(len(p), dtype=float)
    return t, np.array(p, float), np.array(h, float), np.full(len(t), 100.0), t * 10 / 3.6


def test_power_only_segment_on_a_flat_run():
    # 10' easy, 15' at 92 % CP with an easy HR (power alone is Zone 3), 10' easy
    t, p, h, e, d = _flat([(10, 120, 130), (15, 184, 140), (10, 120, 130)])
    r = TS.segments_of(t, p, h, e, d, CP, LTHR, "road")
    assert len(r["segs"]) == 1
    sg = r["segs"][0]
    assert sg["by"] == "power" and sg["basis"] == "power"
    assert sg["duration_s"] == np.float64(sg["duration_s"]) and abs(sg["duration_s"] - 900) <= 15
    assert abs(sg["pct_cp"] - 0.92) < 0.02
    assert sg["fade"] is not None and abs(sg["fade"]) < 0.01      # even power
    assert sg["drift"] is not None and abs(sg["drift"]) < 0.01    # constant HR
    assert sg["eff"] == sg["power"] / sg["hr"]


def test_hr_only_segment_skips_the_first_3_minutes():
    # power stays easy, HR at 105 % LTHR for 16' → Zone 3 by HR from minute 3 of the stretch on (13')
    t, p, h, e, d = _flat([(10, 120, 130), (16, 120, 168), (10, 120, 130)])
    r = TS.segments_of(t, p, h, e, d, CP, LTHR, "road")
    assert len(r["segs"]) == 1
    sg = r["segs"][0]
    assert sg["by"] == "hr"
    assert abs(sg["duration_s"] - 13 * 60) <= 20
    assert abs(sg["start_s"] - 13 * 60) <= 20


def test_under_10_minutes_is_not_a_segment():
    t, p, h, e, d = _flat([(10, 120, 130), (9, 190, 140), (10, 120, 130)])
    assert TS.segments_of(t, p, h, e, d, CP, LTHR, "road")["segs"] == []


def test_a_short_dip_does_not_split_a_segment_a_long_one_does():
    short = _flat([(10, 120, 130), (6, 190, 140), (20 / 60, 100, 140), (6, 190, 140), (10, 120, 130)])
    assert len(TS.segments_of(*short, CP, LTHR, "road")["segs"]) == 1
    long_ = _flat([(10, 120, 130), (6, 190, 140), (1, 100, 140), (6, 190, 140), (10, 120, 130)])
    assert TS.segments_of(*long_, CP, LTHR, "road")["segs"] == []          # two 6' pieces, each < 10'


def test_downhill_high_hr_is_not_threshold():
    # 20' down a 10 % slope with HR at 110 % LTHR: not Zone 3 (the 10/18 descent)
    n = 40 * 60
    t = np.arange(n, dtype=float)
    d = t * 8 / 3.6
    e = np.where(t < 600, 1000.0, 1000.0 - (t - 600) * (8 / 3.6) * 0.10)
    e = np.where(t >= 1800, e[1800], e)
    p = np.full(n, 60.0)
    h = np.where((t >= 600) & (t < 1800), 176.0, 130.0)
    grade = np.gradient(e) / np.maximum(np.gradient(d), 1e-6)
    r = TS.segments_of(t, p, h, e, d, CP, LTHR, "hike", grade)
    assert r["segs"] == []


def test_a_hike_climb_is_read_on_vam_not_power():
    # 20' up a 30 % slope at 3 km/h: HR 106 % LTHR, Stryd shows 40 W
    n = 40 * 60
    t = np.arange(n, dtype=float)
    v = np.where((t >= 600) & (t < 1800), 3 / 3.6, 4 / 3.6)
    d = np.cumsum(v)
    climb = (t >= 600) & (t < 1800)
    e = 1000.0 + np.cumsum(np.where(climb, v * 0.30, 0.0))
    p = np.full(n, 40.0)
    h = np.where(climb, 170.0, 125.0)
    grade = np.where(climb, 0.30, 0.0)
    r = TS.segments_of(t, p, h, e, d, CP, LTHR, "hike", grade)
    assert len(r["segs"]) == 1
    sg = r["segs"][0]
    assert sg["basis"] == "vam" and sg["power"] is None and sg["drift"] is None
    assert abs(sg["vam"] - 3000 * 0.30) < 60                      # 900 m/h
    assert abs(sg["grade"] - 0.30) < 0.03
    assert sg["eff"] == sg["vam"] / sg["hr"]


def test_cp_hint_on_a_long_segment_at_or_over_95_percent_cp():
    t, p, h, e, d = _flat([(10, 120, 130), (25, 196, 165), (10, 120, 130)])
    sg = TS.segments_of(t, p, h, e, d, CP, LTHR, "road")["segs"][0]
    assert TS.cp_hint(sg)
    t, p, h, e, d = _flat([(10, 120, 130), (15, 196, 165), (10, 120, 130)])
    assert not TS.cp_hint(TS.segments_of(t, p, h, e, d, CP, LTHR, "road")["segs"][0])     # < 20 min


def test_z3_bouts_add_hr_stretches_band_bouts_keep_power_only():
    # 2 × 6' with HR at 104 % LTHR and power at 75 % CP: band_bouts finds none, z3_bouts (≥ 2.5 min) finds both
    t, p, h, e, d = _flat([(10, 120, 130), (6, 150, 166), (4, 110, 130), (6, 150, 166), (10, 120, 130)])
    assert IR.band_bouts(t, p, CP, IR.Z3_FLOOR, IR.Z3_MIN_S) == []
    zb = IR.z3_bouts(t, p, h, CP, LTHR)
    assert len(zb) == 2 and all(b["by"] == "hr" for b in zb)


def _workout(day, blocks):
    t, p, h, e, d = _flat(blocks)
    ch = {"elapsedtime": list(t), "heartrate": list(h), "speed": [10.0] * len(t), "power": list(p),
          "elapseddistance": list(d / 1000.0)}
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7)), sport="run", tags=["running"], sport_type="running",
                       channels=ch, metrics={"duration": float(len(t)), "movingduration": float(len(t)),
                                             "distance": float(d[-1] / 1000.0), "climbing": 0.0})


def test_review_sections_list_trace_and_trend():
    today = dt.date(2026, 10, 18)
    blocks = [(10, 150, 130), (20, 190, 150), (10, 150, 130)]       # 20' at 95 % CP
    ws = [_workout(today - dt.timedelta(days=k), blocks) for k in (21, 14, 7)] + [_workout(today, blocks)]
    ds = FakeDataset(ws, today, settings={"runthr": 160.0, "runftp": CP})
    w = ds.workouts[-1]
    lst = R.review(ds, w, "threshold_list")
    assert lst["empty"] is None and len(lst["chip_rows"]) >= 1
    assert lst["chip_rows"][0]["label"] == "第 1 段"
    assert "3 區" in lst["subtitle"]
    tr = R.review(ds, w, "threshold_trace")
    kinds = [s["data"]["kind"] for s in tr["series"]]
    assert kinds.count("vline") == 2 and "points" in kinds
    trend = R.review(ds, w, "threshold_trend")
    assert trend["empty"] is None
    pts = {s["name"]: s["data"]["points"] for s in trend["series"] if s["data"]["kind"] == "points"}
    assert len(pts["之前的閾值段"]) == 3 and len(pts["這次"]) == 1


def test_review_lists_a_hike_climb_on_vam():
    """A hike (sport "hike", as synced) gets its segments too — the 10/18 北插天山 case."""
    today = dt.date(2026, 10, 18)
    n = 60 * 60
    t = np.arange(n, dtype=float)
    climb = (t >= 1200) & (t < 2700)                                  # 25' up at 30 %
    v = np.where(climb, 3.0, 4.0)                                     # km/h
    dist_km = np.cumsum(v / 3600.0)
    elev = 900.0 + np.cumsum(np.where(climb, v / 3.6 * 0.30, 0.0))
    ch = {"elapsedtime": list(t), "heartrate": list(np.where(climb, 172.0, 125.0)), "speed": list(v),
          "power": [45.0] * n, "elapseddistance": list(dist_km), "elevation": list(elev)}
    w = FakeWorkout(start=dt.datetime.combine(today, dt.time(7)), sport="hike", tags=["hiking"], sport_type="hiking",
                    channels=ch, metrics={"duration": float(n), "movingduration": float(n),
                                          "distance": float(dist_km[-1]), "climbing": float(elev[-1] - 900.0)})
    ds = FakeDataset([w], today, settings={"runthr": 160.0, "runftp": CP})
    r = R.review(ds, ds.workouts[0], "threshold_list")
    assert r["empty"] is None and len(r["chip_rows"]) == 1
    chips = " ".join(r["chip_rows"][0]["chips"])
    assert "VAM" in chips and " W" not in chips                      # a hike: no power shown
    tr = R.review(ds, ds.workouts[0], "threshold_trace")
    assert all(s["name"] != "功率（30 秒）" for s in tr["series"])


def test_review_threshold_empty_on_an_easy_run():
    today = dt.date(2026, 10, 18)
    ds = FakeDataset([_workout(today, [(45, 140, 130)])], today, settings={"runthr": 160.0, "runftp": CP})
    r = R.review(ds, ds.workouts[0], "threshold_list")
    assert r["empty"] and "10 分鐘" in r["empty"]


def test_quality_gate_keeps_the_power_only_zone_3():
    """The dose / ladder count calls find_reps without hr_z3 (SP-801 is display only)."""
    import inspect
    from backend.engine import quality_gate as QG
    src = inspect.getsource(QG)
    assert "find_reps(" in src and "hr_z3" not in src
    assert inspect.signature(IR.find_reps).parameters["hr_z3"].default is False
