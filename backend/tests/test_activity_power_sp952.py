"""SP-952: activity judging by THAT day's power state (owner 2026-10-27, SP-950 Q1/Q2;
docs/research/no-power-meter-audit.md 「拍板的規則」).

An activity is judged by power only when the runner has a power meter, its power is trusted (Stryd)
and the CP was fresh on its date (power_state.activity_power); otherwise by heart rate / pace —
session type, stimulus, Zone 3 / VO2 time, completion, the interval ladder and the threshold
estimates. Synthetic data only (wko5_fakes), never the WKO5 folder.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import interval_reps as IR
from backend.engine import power_source as PS
from backend.engine import power_state as PST
from backend.engine import quality_gate as QG
from backend.engine import session_stimulus as SS
from backend.engine import workout_review as WR
from backend.engine.planning import Plan, Threshold
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 27)
LTHR = 170.0
CP = 250.0
TPACE = 4.0                       # min/km: threshold speed 15 km/h


def _run(start: dt.datetime, segs, seed: int = 0, power: bool = True, title: str = "") -> FakeWorkout:
    """1-Hz run: segs = [(seconds, HR target, km/h, W)]; HR follows its target with a 30-s lag."""
    rng = np.random.default_rng(seed)
    tgt = np.concatenate([np.full(d, h, float) for d, h, _, _ in segs])
    kmh = np.concatenate([np.full(d, v, float) for d, _, v, _ in segs]) + rng.normal(0, 0.1, len(tgt))
    pw = np.concatenate([np.full(d, p, float) for d, _, _, p in segs]) + rng.normal(0, 2.0, len(tgt))
    hr = np.empty(len(tgt))
    h = 120.0
    for i, x in enumerate(tgt):
        h += (x - h) / 30.0
        hr[i] = h
    ch = {"elapsedtime": np.arange(0, len(tgt), dtype=float).tolist(), "heartrate": np.round(hr).tolist(),
          "speed": kmh.tolist(), "elapseddistance": np.cumsum(kmh / 3600.0).tolist()}
    if power:
        ch["power"] = pw.tolist()
    return FakeWorkout(start=start, sport="run", sport_type="running", channels=ch, title=title,
                       metrics={"duration": float(len(tgt)), "distance": float(np.sum(kmh) / 3600.0),
                                "climbing": 0.0})


class _DS(FakeDataset):
    """A FakeDataset that can tell its power source (Stryd when a run has power) — so the SP-952 rule
    applies — with a plan, laps (`laps`) and no stored test sessions."""

    def __init__(self, workouts, plan: Plan, laps=None, settings=None):
        super().__init__(workouts, TODAY, settings={"runthr": LTHR, "runtpace": TPACE, **(settings or {})})
        self.plan = plan
        self.plan_test_sessions = []
        self._laps = laps or {}

    def power_source(self, w):
        return PS.STRYD if self.channel(w.idx, "power") is not None else PS.NONE

    def power_ok(self, w):
        return self.power_source(w) == PS.STRYD

    def cp(self, w):
        return CP

    def aethr(self, w):
        return 0.89 * LTHR

    def laps(self, idx):
        return self._laps.get(idx, [])


def _plan(source="stryd", test_day="2026-09-01", **kw) -> Plan:
    rows = [Threshold(test_day, cp=CP)] if test_day else []
    return Plan(thresholds=rows, profile={"power_source": source} if source else {}, **kw)


def _at(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(7))


# a threshold session: 15′ warm-up, 3×8′ at 96 % CP / HR → 0.97 LTHR, 2′ rests, 10′ cool-down
THRESHOLD = [(900, 140, 10.0, 190)] + [(480, 0.99 * LTHR, 14.5, 0.96 * CP), (120, 135, 8.0, 160)] * 3 + \
    [(600, 135, 9.5, 180)]


# ---- 1. the rule: by the activity's own date --------------------------------------------------

def test_power_is_judged_only_while_the_cp_was_fresh_on_that_day():
    fresh = _run(_at(dt.date(2026, 9, 20)), THRESHOLD, seed=1)           # 19 days after the CP test
    stale = _run(_at(dt.date(2026, 10, 25)), THRESHOLD, seed=1)          # 54 days after: stale (> 42)
    ds = _DS([fresh, stale], _plan())
    wf, ws = ds.workouts
    assert PST.activity_power(ds, wf) and not PST.activity_power(ds, ws)
    mf, ms = WR.measure(ds, wf), WR.measure(ds, ws)
    assert mf["power_on"] and not ms["power_on"]
    # the CP on file stays (CP-test recognition, display); judging uses it only on the fresh day
    assert mf["cp"] == ms["cp"] == CP and WR.judge_cp(mf) == CP and WR.judge_cp(ms) is None
    assert mf["stim"]["z3_power_s"] > 0 and mf["stim"]["power_valid_share"] > 0.5
    assert ms["stim"]["z3_power_s"] == 0 and ms["stim"]["power_valid_share"] == 0.0
    assert ms["hard_power_s"] is None and mf["hard_power_s"] > 0
    # the stale day still holds its Zone 3 — by heart rate (3×8′ at ≈ 0.99 LTHR)
    assert ms["stim"]["z3_hr_s"] >= SS.Z3_NEED_S and mf["stim"]["z3_hr_s"] == 0.0


def test_a_cp_test_in_the_data_or_a_race_makes_its_own_day_fresh():
    test = _run(_at(dt.date(2026, 10, 20)), THRESHOLD, seed=2, title="CP 測試 3/12")
    after = _run(_at(dt.date(2026, 10, 25)), THRESHOLD, seed=3)
    ds = _DS([test, after], _plan())
    assert all(PST.activity_power(ds, w) for w in ds.workouts)            # the test anchors both
    race = _run(_at(dt.date(2026, 10, 25)), THRESHOLD, seed=3)
    from backend.engine.planning import Event
    ev = Event(id="r1", name="10K", date="2026-10-10", kind="road")
    ds2 = _DS([race], _plan(events=[ev]))
    assert PST.activity_power(ds2, ds2.workouts[0])


def test_no_power_meter_never_judges_by_power():
    fresh = _run(_at(dt.date(2026, 9, 20)), THRESHOLD, seed=1)
    ds = _DS([fresh], _plan(source="none"))
    m = WR.measure(ds, ds.workouts[0])
    assert not PST.activity_power(ds, ds.workouts[0]) and not m["power_on"]
    assert m["stim"]["power_valid_share"] == 0.0 and m["hard_power_s"] is None


def test_a_dataset_that_cannot_tell_keeps_the_old_behaviour():
    fw = _run(_at(dt.date(2026, 10, 25)), THRESHOLD, seed=1)
    ds = FakeDataset([fw], TODAY, settings={"runthr": LTHR})
    ds.plan = Plan()
    ds.cp = lambda w: CP
    assert PST.activity_power(ds, ds.workouts[0])
    assert WR.measure(ds, ds.workouts[0])["power_on"]


# ---- 2. Zone 3 by HR with the planned reps known ---------------------------------------------

def _reps(n, work, rest, hr_rep=0.99 * LTHR, kmh=15.0):
    segs = [(900, 138, 10.0, 0)]
    for i in range(n):
        segs.append((work, hr_rep, kmh, 0))
        if i < n - 1:
            segs.append((rest, 130, 8.0, 0))
    segs.append((600, 132, 9.0, 0))
    return segs


@pytest.mark.parametrize("n,work,rest", [(5, 180, 120), (4, 240, 120)])
def test_hr_reps_of_a_planned_set_count_zone3(n, work, rest):
    fw = _run(_at(dt.date(2026, 10, 25)), _reps(n, work, rest), seed=4, power=False)
    ds = _DS([fw], _plan(source="none"))
    m = WR.measure(ds, ds.workouts[0])
    st = m["stim"]
    assert st["z3_hr_s"] == 0.0                         # the unplanned rule: each stretch minus 3 min
    z = SS.z3_hr_planned(st, work, n, rest)
    assert SS.Z3_NEED_S <= z <= n * work                # the known reps: a Zone 3 session, ≤ the planned work
    spec = ("x", f"{n}×{work // 60}′", n, work / 60.0, rest / 60.0, 0.90, 0.95, False, "")
    np_ = IR.no_power_reps(ds, ds.workouts[0], spec, m)
    assert np_["source"] == "hr" and len(np_["bouts"]) >= n - 1
    o = QG.interval_outcome(np_["bouts"], spec, None, 0.89 * LTHR, basis="hr")
    assert o["outcome"] == "met"


def test_the_classifier_reads_a_planned_hr_set_as_zone3(monkeypatch):
    fw = _run(_at(dt.date(2026, 10, 25)), _reps(4, 240, 120), seed=5, power=False)
    ds = _DS([fw], _plan(source="none"))
    w = ds.workouts[0]
    m = WR.measure(ds, w)
    assert WR.classify(ds, w, m)["stimulus"] is None    # unplanned: 0 s of Zone 3 by HR
    spec = ("x", "4×4′", 4, 4.0, 2.0, 0.90, 0.95, False, "")
    monkeypatch.setattr(WR, "_planned_spec", lambda ds, w, m=None: spec)
    monkeypatch.setattr(WR, "_planned_quality", lambda ds, w: True)
    c = WR.classify(ds, w, m)
    assert c["stimulus"] == "z3" and c["stim"]["z3_s"] >= SS.Z3_NEED_S


# ---- 3. flat road with laps: lap pace against the band -----------------------------------------

def _lap_run(rep_kmh):
    segs = _reps(4, 240, 120, kmh=rep_kmh)
    fw = _run(_at(dt.date(2026, 10, 25)), segs, seed=6, power=False)
    laps, t = [], 900.0
    for i in range(4):
        laps.append({"start_s": t, "duration_s": 240.0, "power": None, "speed": rep_kmh})
        t += 240.0 + 120.0
    return fw, laps


def test_lap_pace_judges_the_reps_and_the_zone3_time():
    fw, laps = _lap_run(15.0)                           # 4:00 /km = the threshold pace (rel 1.0)
    ds = _DS([fw], _plan(source="none"), laps={0: laps})
    w = ds.workouts[0]
    spec = ("x", "4×4′", 4, 4.0, 2.0, 0.90, 0.95, False, "")
    np_ = IR.no_power_reps(ds, w, spec)
    assert np_["source"] == "lap_pace" and len(np_["bouts"]) == 4
    assert all(b["rel"] == pytest.approx(1.0) for b in np_["bouts"])
    assert np_["z3_s"] == pytest.approx(960.0)
    assert QG.interval_outcome(np_["bouts"], spec, None, None, basis="pace")["outcome"] == "met"
    slow, slow_laps = _lap_run(12.0)                    # 5:00 /km: 80 % of the threshold speed
    ds2 = _DS([slow], _plan(source="none"), laps={0: slow_laps})
    np2 = IR.no_power_reps(ds2, ds2.workouts[0], spec)
    o = QG.interval_outcome(np2["bouts"], spec, None, None, basis="pace")
    assert o["outcome"] == "too_high" and "閾值配速" in o["why"]


def test_the_ladder_moves_on_pace_and_hr_sessions_without_power(monkeypatch):
    fw, laps = _lap_run(15.0)
    hr_run = _run(_at(dt.date(2026, 10, 26)), _reps(6, 180, 60), seed=7, power=False)
    ds = _DS([fw, hr_run], _plan(source="none"), laps={0: laps})
    from backend.engine import interval_library as IL
    from backend.engine import plan_store
    v = IL.get("t1b")                                    # 6×3′ / 1′ (Zone 3)
    monkeypatch.setattr(plan_store, "done_plan", lambda: {
        0: {"title": "4×4′", "kind": "quality"}, 1: {"title": IL.title(v), "kind": "quality", "variant_key": "t1b"}})
    monkeypatch.setattr(plan_store, "plan_in_use", lambda: True)
    monkeypatch.setattr(QG, "planned_variant_spec",
                        lambda row: v if row.get("variant_key") else ("x", "4×4′", 4, 4.0, 2.0, 0.90, 0.95, False, ""))
    rows = QG.dose_history(ds, TODAY)
    by = {r["idx"]: r for r in rows}
    assert by[0]["basis"] == "pace" and by[0]["cp"] is None and by[0]["tiz_ratio"] == pytest.approx(1.0)
    assert by[1]["basis"] == "hr" and by[1]["reps"] >= 5 and by[1]["tiz_ratio"] is None
    for r in rows:
        o = QG.interval_outcome(r["bouts"], QG.variant_tuple(v) if r["idx"] == 1 else
                                ("x", "4×4′", 4, 4.0, 2.0, 0.90, 0.95, False, ""), None, 0.89 * LTHR,
                                basis=r["basis"])
        assert o["outcome"] == "met"
    # dose_step judges both without power (before: 「沒有功率或 CP，無法判定」 — the step never moved)
    QG.dose_step(rows, 0.89 * LTHR, "z3")
    assert {r["idx"]: r.get("outcome") for r in rows} == {0: "met", 1: "met"}


def test_zone3_done_marking_uses_the_reps_without_power():
    from backend.engine import overview as O
    hr_run = _run(_at(dt.date(2026, 10, 26)), _reps(6, 180, 60), seed=7, power=False)
    ds = _DS([hr_run], _plan(source="none"))
    w = ds.workouts[0]
    s = SimpleNamespace(variant_key="t1b", variant_reps=None, title="6×3′", kind="quality")
    need = QG.hard_need(s.title, O.HARD_SESSION_S, s.variant_key, s.variant_reps)
    assert O._z3_done_s(ds, w, {w.idx: 0.0}, s) >= need       # the expression's power says 0


# ---- 4. the drift test's intensity gate without power -----------------------------------------

def _steady(hr: float, kmh: float = 11.0, secs: int = 4200):
    t = np.arange(0, secs, dtype=float)
    rng = np.random.default_rng(8)
    return t, hr + rng.normal(0, 1.0, secs), kmh + rng.normal(0, 0.1, secs)


def test_a_steady_tempo_without_power_is_no_aet_test():
    t, h, v = _steady(0.95 * LTHR)
    dr = WR.drift_of(t, h, v, None, None, lthr=LTHR)
    assert not dr["ok"] and "LTHR" in dr["reason"]
    t, h, v = _steady(0.85 * LTHR)
    assert WR.drift_of(t, h, v, None, None, lthr=LTHR)["ok"]
    t, h, v = _steady(0.85 * LTHR, kmh=14.5)                          # 97 % of the threshold speed
    dr = WR.drift_of(t, h, v, None, None, lthr=LTHR, tspeed=15.0)
    assert not dr["ok"] and "閾值配速" in dr["reason"]


def test_a_measured_tempo_run_without_a_meter_is_not_classed_aet_test():
    fw = _run(_at(dt.date(2026, 10, 25)), [(600, 140, 10.0, 0), (3600, 0.95 * LTHR, 13.0, 0)], seed=9, power=False)
    ds = _DS([fw], _plan(source="none"))
    w = ds.workouts[0]
    m = WR.measure(ds, w)
    assert not m["drift"]["ok"]
    assert WR.classify(ds, w, m)["type"] != "test_aet"


# ---- 5. thresholds without power --------------------------------------------------------------

def test_steady_drift_pace_gives_an_aet_point_and_gates_tempo():
    from backend.engine.algorithms.threshold_estimate import steady_drift_pace
    t, h, v = _steady(0.85 * LTHR, secs=3600)
    p = steady_drift_pace(t, h, v, LTHR)
    assert p is not None and abs(p.drift) < 0.05 and p.power == pytest.approx(11.0, abs=0.1)
    t, h, v = _steady(0.95 * LTHR, secs=3600)
    assert steady_drift_pace(t, h, v, LTHR) is None


def test_no_cp_evidence_on_a_stale_day(monkeypatch):
    from backend.engine import threshold_confidence as TC
    fw = _run(_at(dt.date(2026, 10, 25)), THRESHOLD, seed=1)
    stale = _DS([fw], _plan())                                       # CP test 2026-09-01: stale today
    assert TC._cp_now(stale, stale.plan, TODAY) is None
    fresh = _DS([fw], _plan(test_day="2026-10-10"))
    assert TC._cp_now(fresh, fresh.plan, TODAY) == CP
    # the per-run CP-band summary has no power and no CP on a run not judged by power
    s = TC._summary(stale, stale.workouts[0], PST.activity_power(stale, stale.workouts[0]))
    assert s is None or not s.get("cp_band")
