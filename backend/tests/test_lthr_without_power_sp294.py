"""
SP-294 (research): can a runner without a power meter get an LTHR? And what does a runner with
only COROS watch-estimated power (no Stryd) get for CP / LTHR? Reproduces the current behaviour
on synthetic runs — never the user's data, the app DB or ~/.wko5coach (cptest.curves, which
pd_model reads from WX.home(), is stubbed out). Findings: docs/research/lthr-without-power.md.

The synthetic athlete: LTHR 170 bpm (the HR of a 30-min threshold block), HRmax ≈ 186, Stryd CP
250 W; COROS wrist power reads WATCH_SCALE × the Stryd value (the owner's COROS history fitted
~1.8 × the plan CP — fitdataset._estimate_settings; 1.3 here keeps the numbers readable).

The two prototype HR-only estimates at the bottom (`_tp_peak`, `_friel_speed`) are NOT app code:
they measure what a CP-free route would give on the same runs.
"""
from __future__ import annotations

import datetime as dt
import statistics
from types import SimpleNamespace

import numpy as np
import pytest

from backend.engine import hr_profile as HP
from backend.engine import power_source as PS
from backend.engine import thresholds as TH
from backend.engine import threshold_confidence as TC
from backend.engine.algorithms.wko5_meanmax import meanmax_time
from backend.engine.planning import Plan
from backend.engine.racepower import athlete as A
from backend.engine.racepower import cptest as CT
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 1)
TRUE_LTHR = 170
STRYD_CP = 250.0
WATCH_SCALE = 1.3

# (seconds, target HR, km/h, Stryd-scale W)
EASY = [(600, 135, 9.5, 190), (3000, 146, 10.5, 200)]
TEMPO = [(900, 140, 10.0, 195), (1800, TRUE_LTHR, 13.0, 252), (600, 140, 9.5, 190)]
HARD = [(900, 140, 10.0, 195), (10, 160, 22.0, 600), (180, 135, 8.0, 170), (60, 172, 18.0, 400),
        (180, 140, 8.0, 170), (180, 180, 15.0, 320), (240, 145, 8.0, 170), (300, 184, 14.5, 295),
        (240, 150, 8.0, 170), (720, 186, 13.5, 265), (600, 140, 9.0, 185)]


def _run(start: dt.datetime, segs, seed: int, power_scale=None) -> FakeWorkout:
    """1-Hz HR (first-order lag, τ 30 s, ±1 bpm noise), speed, distance; power only with a scale."""
    rng = np.random.default_rng(seed)
    tgt = np.concatenate([np.full(d, h, float) for d, h, _, _ in segs])
    kmh = np.concatenate([np.full(d, v, float) for d, _, v, _ in segs]) + rng.normal(0, 0.2, len(tgt))
    pw = np.concatenate([np.full(d, p, float) for d, _, _, p in segs])
    hr = np.empty(len(tgt))
    h = 100.0
    for i, x in enumerate(tgt):
        h += (x - h) / 30.0
        hr[i] = h
    hr = np.round(hr + rng.normal(0, 1.0, len(tgt)))
    ch = {"elapsedtime": np.arange(1, len(tgt) + 1, dtype=float).tolist(), "heartrate": hr.tolist(),
          "speed": kmh.tolist(), "distance": np.cumsum(kmh / 3600.0).tolist()}
    if power_scale is not None:
        ch["power"] = (pw * power_scale + rng.normal(0, 3.0, len(tgt))).tolist()
    return FakeWorkout(start=start, sport="run", sport_type="running", channels=ch,
                       metrics={"duration": float(len(tgt))})


def _history(kind: str, days: int = 120, scale_of=lambda day: None) -> list[FakeWorkout]:
    """A run every 2nd day up to TODAY. `kind` trained: ~80 % easy — easy ×3, tempo, easy ×3, hard
    (≈ 8 tempo and 8 hard sessions in 120 days); easy: easy runs only. `scale_of(date)`: the power
    scale of that day's run (None = no power)."""
    cycle = [EASY, EASY, EASY, TEMPO, EASY, EASY, EASY, HARD] if kind == "trained" else [EASY]
    out = []
    for i, back in enumerate(range(days, -1, -2)):
        day = TODAY - dt.timedelta(days=back)
        out.append(_run(dt.datetime.combine(day, dt.time(6)), cycle[i % len(cycle)], seed=i,
                        power_scale=scale_of(day)))
    return out


class _DS(FakeDataset):
    """FakeDataset + what thresholds.estimate / cp_as_of / pd_model read. `src_of(w)`: stryd /
    watch for a run with power (FitFolderDataset.power_source); `cp_charts`: Dataset.cp — on a COROS
    source the plan test, else run_ftp_w, else the Stryd-only PD fit (None here: no Stryd fit)."""

    def __init__(self, workouts, src_of=lambda w: PS.WATCH, accept_watch=False, cp_charts=None, plan=None):
        super().__init__(workouts, TODAY, settings={})
        self.plan = plan or Plan()
        self.accept_watch_power = accept_watch
        self._src_of, self._cp, self._curves = src_of, cp_charts, {}

    def cached_series(self, key, w, compute, extra=None):
        return compute()

    def flush_series(self):
        return None

    def cp(self, w):
        return self._cp

    def power_source(self, w):
        return PS.NONE if self.channel(w.idx, "power") is None else self._src_of(w)

    def power_ok(self, w):
        return PS.usable(self.power_source(w), self.accept_watch_power)

    def curve_cache(self, expr):
        return {}

    def workout_curve(self, idx, channel):
        if (idx, channel) not in self._curves:
            v = self.channel(idx, channel)
            self._curves[(idx, channel)] = None if v is None else tuple(
                list(x) for x in meanmax_time(list(self.channel(idx, "elapsedtime")), list(v)))
        return self._curves[(idx, channel)]


def fresh_cp_days(ds=None):
    """SP-952: these synthetic Stryd runners keep their CP current — a CP test every 30 days over the
    whole history (power_state.cp_days), so every run is judged by power as these tests assume."""
    return [(TODAY - dt.timedelta(days=d)).isoformat() for d in range(0, 400, 30)], []


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    from backend.engine import power_state as PST
    monkeypatch.setattr(PST, "cp_days", fresh_cp_days)          # SP-952: the CP fresh on every run's day
    monkeypatch.setattr(CT, "curves", lambda *a, **k: [])       # never the synced FIT folder
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)  # no COROS account HR settings
    A._cp_memo.clear()
    A._est_memo.clear()
    yield
    A._cp_memo.clear()
    A._est_memo.clear()


# ---- prototypes of a CP-free route (research only, not app code) -------------------

def _summaries(ds):
    out = []
    for w in ds.workouts:
        t, hr, v = (ds.channel(w.idx, k) for k in ("elapsedtime", "heartrate", "speed"))
        s = TC.run_summary(t, hr, speed_kmh=v)
        tt = TC.tt30_result(t, hr, None, v)
        out.append({"hr20": s and s["hr20"], "hr60": s and s["hr60"], "friel_speed": tt and tt["lthr"]})
    return out


def _tp_peak(ds):
    """TrainingPeaks' threshold notification as a value: max over runs of the best 60-min mean HR
    or 0.95 × the best 20-min mean HR (threshold_confidence signal 4 uses it to say 「too low」)."""
    vals = [v for s in _summaries(ds) for v in (s["hr60"], s["hr20"] and 0.95 * s["hr20"]) if v]
    return max(vals) if vals else None


def _friel_speed(ds, top=3):
    """Friel's 30-min field test read from ordinary runs by SPEED instead of power: per run the
    fastest 30-min window's minutes 10–30 mean HR (threshold_confidence.tt30_result without power),
    the median of the `top` highest. No intensity gate — CP is what gated it."""
    vals = sorted((s["friel_speed"] for s in _summaries(ds) if s["friel_speed"]), reverse=True)
    return statistics.median(vals[:top]) if len(vals) >= top else None


# ---- 1. HR only: no LTHR estimate, no AeT estimate --------------------------------

@pytest.mark.parametrize("cp_of", ["dataset", "as_of"])
def test_hr_only_runs_give_no_lthr_and_no_aet_estimate(cp_of):
    ds = _DS(_history("trained"))
    assert ds.power_source(ds.workouts[0]) == PS.NONE
    assert A.cp_as_of(ds, TODAY) is None                       # no plan CP, no power to fit
    est = TH.estimate(ds, TODAY, cp_of=None if cp_of == "dataset" else (lambda d: A.cp_as_of(ds, d)))
    assert est["cp"] is None
    # 8 tempo blocks at exactly LTHR, and still no value: without a CP no run says 「hard enough」.
    # SP-573: it says why and points at the 30-minute test (no longer a bare null)
    lt = est["lthr"]
    if cp_of == "dataset":
        # SP-950: no power in the data and no 功率來源 set → 沒有功率計 (③): it says so and suggests no
        # test (owner 2026-10-27: no 30-min test in its place)
        assert lt["value"] is None and lt["no_cp"] and lt["no_meter"] and lt["reason"] == TH.NO_METER_REASON
        assert lt["links"] == []
    else:
        assert lt["value"] is None and lt["no_cp"] and lt["reason"] == TH.NO_CP_REASON
        assert "Stryd" in lt["reason"] and "CP 測試" in lt["reason"] and "30 分鐘 LTHR 測試" in lt["reason"]
    if cp_of == "dataset":
        # SP-952 (owner 2026-10-27): without a power meter the AeT comes from Pa:HR drift (pace), not power
        assert est["aet_basis"] == "pace" and est["aethr"]["n"] > 0
    else:
        assert est["aethr"]["value"] is None and est["aethr"]["n"] == 0   # the load-time grid: power only


# ---- 2. what fills the gap today: the SP-289 prior 0.90 × max HR ---------------------

def test_hr_only_prior_comes_from_the_runs_max_hr_unless_easy_only():
    trained = _DS(_history("trained"), plan=Plan(profile={"birth_year": TODAY.year - 40}))
    m = HP.max_hr(trained, TODAY)
    assert m["kind"] == "estimate" and 183 <= m["value"] <= 187   # the hard sessions' 12-min block
    p = HP.lthr_prior(trained, TODAY)
    assert 165 <= p["value"] <= 168                                 # truth 170: 2–5 bpm low
    easy = _DS(_history("easy"), plan=Plan(profile={"birth_year": TODAY.year - 40}))
    m = HP.max_hr(easy, TODAY)
    # SP-294 (owner 2026-10-14): an easy-only runner's 「max HR」 was the top of his easy runs (≤ 150) and
    # beat the age formula; now the age formula, the runs' value said beside it
    assert HP.age_max_hr(40) == 180.0
    assert m["kind"] == "age" and m["value"] == 180.0 and m["runs_value"] <= 150
    assert "180" in m["source"] and f"{m['runs_value']:.0f}" in m["source"] and "推估" in m["source"]
    p = HP.lthr_prior(easy, TODAY)
    assert p["value"] == 162.0                                      # was ≤ 135; truth 170


# ---- 3. a CP-free route on the same runs ------------------------------------------

def test_cp_free_routes_are_right_only_when_there_were_hard_runs():
    trained, easy = _DS(_history("trained")), _DS(_history("easy"))
    # Friel by speed: the fastest 30 min of every run; with no intensity gate the 3 highest are the
    # interval sessions (fast reps + recoveries, minutes 10–30 above LTHR): 175, +5 bpm
    assert TRUE_LTHR <= _friel_speed(trained) <= TRUE_LTHR + 7
    # TrainingPeaks' peak rule: 0.95 × the interval session's best 20-min HR (178) ≈ 169
    assert TRUE_LTHR - 4 <= _tp_peak(trained) <= TRUE_LTHR + 2
    # an easy-only history: both are the easy runs' HR (146 / 144) — a lower bound, not an LTHR
    assert _friel_speed(easy) <= 148 and _tp_peak(easy) <= 148


# ---- 4. COROS wrist power, no Stryd -----------------------------------------------

def _watch_only():
    return _DS(_history("trained", scale_of=lambda d: WATCH_SCALE), src_of=lambda w: PS.WATCH)


def test_watch_power_only_gives_no_cp_and_no_lthr_estimate():
    # SP-950 (owner 2026-10-27): watch power never counts — SP-573's wrist-power CP (pd_model any_power)
    # is gone, so a watch-power-only history has no CP and no LTHR from ordinary runs, on both paths
    ds = _watch_only()
    assert not ds.power_ok(ds.workouts[0])
    assert A.cp_as_of(ds, TODAY) is None
    est = TH.estimate(ds, TODAY, cp_of=lambda d: A.cp_as_of(ds, d))
    assert est["cp"] is None and est["lthr"]["value"] is None
    est0 = TH.estimate(ds, TODAY)                                 # ③ (detected: no Stryd)
    assert est0["cp"] is None and est0["lthr"]["value"] is None and est0["lthr"]["no_meter"]


def test_watch_power_only_no_cp_for_the_charts_or_the_plan_even_with_watch_power_accepted():
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    ws = [SimpleNamespace(sport="run", day=float(d), idx=i) for i, d in enumerate(range(100, 160, 3))]
    for accept in (False, True):
        stub = SimpleNamespace(workouts=ws, cold=None, power_source=lambda w: PS.WATCH,
                               power_ok=lambda w, a=accept: a)
        # the 課表 / charts CP fit counts Stryd runs only, whatever the setting says
        assert FitFolderDataset.stryd_cp_fit(stub, TODAY) is None


# ---- 5. a history that switches from wrist power to a Stryd ---------------------------

def test_wrist_runs_in_a_stryd_window_are_judged_against_the_stryd_cp():
    switch = TODAY - dt.timedelta(days=40)
    ws = _history("trained", scale_of=lambda d: WATCH_SCALE if d < switch else 1.0)
    src = {}
    for fw in ws:
        src[fw.start] = PS.WATCH if fw.start.date() < switch else PS.STRYD
    ds = _DS(ws, src_of=lambda w: src[w.entry.start])
    cp = A.cp_as_of(ds, TODAY)
    assert cp is not None and abs(cp - STRYD_CP) <= 0.12 * STRYD_CP   # the window has Stryd: Stryd-only fit
    est = TH.estimate(ds, TODAY, cp_of=lambda d: A.cp_as_of(ds, d))
    lt = est["lthr"]
    # SP-572: the wrist runs of the 90-day window used to be judged against the Stryd CP too (their
    # easy 200 W × 1.3 = 260 W cleared 95 % of it, so easy-run HR counted: 146). Now only the runs
    # whose power is the CP's kind (usable power, as in cp_as_of's fit) say 「hard enough」
    assert abs(lt["value"] - TRUE_LTHR) <= 2 and lt["days"] == 90
    # the same value as an estimate on the Stryd runs alone
    stryd_only = _DS([fw for fw in ws if fw.start.date() >= switch], src_of=lambda w: PS.STRYD)
    ok = TH.estimate(stryd_only, TODAY, cp_of=lambda d: cp)
    assert abs(ok["lthr"]["value"] - TRUE_LTHR) <= 2
    assert lt["n"] == ok["lthr"]["n"]
    # the default path (Dataset.cp, the threshold-estimate API) judges the same way
    est0 = TH.estimate(_DS(ws, src_of=lambda w: src[w.entry.start], cp_charts=cp), TODAY)
    assert abs(est0["lthr"]["value"] - TRUE_LTHR) <= 2


def test_with_watch_power_accepted_the_wrist_runs_still_count():
    """power.accept_watch_power on: the user takes wrist power as usable, cp_as_of fits both
    together, so the LTHR estimate judges both as before (SP-572 leaves this case alone)."""
    switch = TODAY - dt.timedelta(days=40)
    ws = _history("trained", scale_of=lambda d: WATCH_SCALE if d < switch else 1.0)
    src = {fw.start: PS.WATCH if fw.start.date() < switch else PS.STRYD for fw in ws}
    ds = _DS(ws, src_of=lambda w: src[w.entry.start], accept_watch=True)
    est = TH.estimate(ds, TODAY, cp_of=lambda d: A.cp_as_of(ds, d))
    stryd_only = _DS([fw for fw in ws if fw.start.date() >= switch], src_of=lambda w: PS.STRYD)
    ok = TH.estimate(stryd_only, TODAY, cp_of=lambda d: est["cp"])
    assert est["lthr"]["n"] > ok["lthr"]["n"]                         # the wrist runs are judged too


def test_a_window_without_usable_power_judges_no_wrist_run():
    """Stryd runs only before the CP's 90-day window. SP-950 (owner 2026-10-27): watch power never
    counts — the CP is the last Stryd fit (cp_as_of's 30 days back), never the wrist-power fit, and no
    wrist run is judged against it."""
    switch = TODAY - dt.timedelta(days=100)
    ws = _history("trained", days=170, scale_of=lambda d: 1.0 if d < switch else WATCH_SCALE)
    src = {fw.start: PS.STRYD if fw.start.date() < switch else PS.WATCH for fw in ws}
    ds = _DS(ws, src_of=lambda w: src[w.entry.start])
    est = TH.estimate(ds, TODAY, cp_of=lambda d: A.cp_as_of(ds, d))
    assert est["cp"] is None or est["cp"] < 1.15 * STRYD_CP          # not the wrist-power fit (> 1.15 × CP)
    from backend.engine.wko5expr.dataset import date_to_day
    judged = TH._judged_against_cp(ds, ds.workouts, int(date_to_day(TODAY)))
    assert not any(judged(w) for w in ds.workouts if src[w.entry.start] == PS.WATCH)


def test_the_estimate_cache_follows_the_rule():
    """The as-of estimate cache (fitdataset.estimate_code: estimate.json, the grid) is keyed on the
    code it reaches: the rule and what it reads, so a change to them recomputes the estimates."""
    from backend.engine import codehash as C
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    est = C.closure([FitFolderDataset._estimate_settings, FitFolderDataset._estimate_cp],
                    context=[FitFolderDataset])
    for name in ("backend.engine.thresholds._judged_against_cp", "backend.engine.racepower.athlete.power_ok",
                 "backend.engine.racepower.athlete.power_source", "backend.engine.racepower.athlete:CP_WINDOW_DAYS"):
        assert name in est, name


# ---- 6. what already reacts to a too-low prior: threshold_confidence ---------------------

@pytest.mark.parametrize("temp,level,priority", [(None, "hint", "low"), ("cool", "strong", None)])
def test_a_prior_below_the_easy_runs_hr_asks_for_the_tests(temp, level, priority):
    # the easy-only runner of test 2 without a birth year: prior 0.90 × 147 = 132, easy runs at ~146 for 60 min
    runs = [{"date": f"2026-09-{d:02d}", "hr20": 146.0, "hr60": 144.0, "s60": 150.0, "s120": 149.0,
             "avg_hr": 142.0, "dur_s": 3600.0, "cp_band": [], "temp": temp} for d in (5, 12, 19)]
    r = TC.assess({"value": 132.0, "source_kind": "prior"}, {"value": 147.0, "source_kind": "estimate"},
                  {"value": None}, TODAY, runs, {})
    assert r["lthr"]["confidence"] == "low"
    sig = {s["id"]: s for s in r["lthr"]["signals"]}
    # TrainingPeaks' rule (signal 4): 0.95 × 146 = 139 > 132 → 「LTHR 可能設低了」, but it never moves the value
    assert sig["long_effort"]["level"] == level and sig["long_effort"]["direction"] == "up"
    (s,) = r["suggestions"]
    assert "tt30" in s["tests"] and s.get("priority") == priority


# ---- 7. SP-294: the runs' max HR gives way to the age formula only for an easy-only history ----------

def _easy_plus(fw):
    return _history("easy") + [fw]


def test_without_a_birth_year_the_runs_value_stays():
    m = HP.max_hr(_DS(_history("easy")), TODAY)
    assert m["kind"] == "estimate" and m["value"] <= 150             # nothing to compare with: as before
    tc = TC.mhr_info(_DS(_history("easy")), TODAY, None)
    assert tc["source_kind"] == "estimate"                           # a low source (threshold_confidence)


def test_a_gap_within_10_bpm_keeps_the_runs_value():
    # the easy runs top out around 147: a 75-year-old's formula 156 is within 10 bpm → the runs' value
    ds = _DS(_history("easy"), plan=Plan(profile={"birth_year": TODAY.year - 75}))
    m = HP.max_hr(ds, TODAY)
    assert HP.age_max_hr(75) == 156.0 and m["kind"] == "estimate" and 156.0 - m["value"] <= 10


@pytest.mark.parametrize("how", ["race_title", "test_title", "race_event", "power", "hr_age", "hr_plan"])
def test_one_hard_effort_keeps_the_runs_value(how):
    """A hard run in the 365 days keeps the runs' value even > 10 bpm under the age formula."""
    from backend.engine.planning import Event, Threshold
    day = TODAY - dt.timedelta(days=30)
    start = dt.datetime.combine(day, dt.time(7))
    plan = Plan(profile={"birth_year": TODAY.year - 40})
    if how in ("race_title", "test_title", "race_event"):
        title = {"race_title": "台北 10K 路跑賽", "test_title": "LTHR 30 分鐘測試", "race_event": "早上跑步"}[how]
        fw = _run(start, EASY, seed=99)
        fw.title = title
        if how == "race_event":
            plan.events.append(Event(id="r1", name="秋季路跑", date=day.isoformat(), kind="road"))
        ds = _DS(_easy_plus(fw), plan=plan)
    elif how == "power":
        # Stryd power: 5 min at 1.1 × CP — the HR of this rep (160) stays under the age route's LTHR 162
        fw = _run(start, [(900, 140, 10.0, 195), (300, 160, 14.0, 275), (600, 140, 9.5, 190)], seed=98,
                  power_scale=1.0)
        ds = _DS(_easy_plus(fw), src_of=lambda w: PS.STRYD, cp_charts=STRYD_CP, plan=plan)
    elif how == "hr_age":
        # no power, no LTHR anywhere: 5 min at ≥ 0.90 × the age formula (162) is hard by the age route's yardstick
        fw = _run(start, [(900, 140, 10.0, 195), (420, 166, 14.0, 275), (600, 140, 9.5, 190)], seed=97)
        ds = _DS(_easy_plus(fw), plan=plan)
    else:
        # a tested LTHR of 160 in the plan (the easy runs' 146 is 91 % of it): 30 min at ≥ 95 % of it is hard
        plan.thresholds.append(Threshold(date="2026-06-01", lthr=160.0, lthr_method="friel30"))
        fw = _run(start, [(600, 135, 9.5, 190), (1900, 157, 12.0, 240), (600, 135, 9.5, 190)], seed=96)
        ds = _DS(_easy_plus(fw), plan=plan)
    est = TH.estimate_mhr(ds, TODAY)["value"]
    assert est < 170                                                  # > 10 under the formula's 180
    hard = TH.hard_efforts(ds, TODAY, HP.LTHR_OF_MHR * 180.0)
    assert hard and hard[0][0] == day.isoformat(), how
    m = HP.max_hr(ds, TODAY)
    assert m["kind"] == "estimate" and m["value"] == est


def test_easy_only_power_runs_are_not_hard():
    plan = Plan(profile={"birth_year": TODAY.year - 40})
    ds = _DS(_history("easy", scale_of=lambda d: 1.0), src_of=lambda w: PS.STRYD, cp_charts=STRYD_CP, plan=plan)
    assert TH.hard_efforts(ds, TODAY, 162.0) == []                    # 200 W = 80 % CP
    assert HP.max_hr(ds, TODAY)["kind"] == "age"


def test_a_hard_run_older_than_365_days_does_not_count():
    old = dt.datetime.combine(TODAY - dt.timedelta(days=400), dt.time(7))
    fw = _run(old, EASY, seed=95)
    fw.title = "台北馬拉松"
    ds = _DS(_easy_plus(fw), plan=Plan(profile={"birth_year": TODAY.year - 40}))
    assert TH.hard_efforts(ds, TODAY, 162.0) == [] and HP.max_hr(ds, TODAY)["kind"] == "age"


def test_the_age_formula_max_hr_is_low_and_easy_runs_are_no_spike_evidence():
    # threshold_confidence on the easy-only runner: the age formula is a low source; the easy runs far
    # under it are why it is used, not a sign it is a spike (no 「sustained」 signal, no candidate to apply)
    runs = [{"date": f"2026-09-{d:02d}", "s10": 148.0, "s60": 147.0, "s120": 146.0, "hr20": 146.0, "hr60": 144.0,
             "avg_hr": 142.0, "dur_s": 3600.0, "cp_band": [], "temp": "cool"} for d in (5, 12, 19)]
    r = TC.assess({"value": 162.0, "source_kind": "prior"}, {"value": 180.0, "source_kind": "age"},
                  {"value": None}, TODAY, runs, {})
    h = r["hrmax"]
    assert h["confidence"] == "low" and [s["id"] for s in h["signals"]] == ["source"]
    assert any("hrmax" in s["tests"] for s in r["suggestions"])       # the max-HR test is still suggested
    # the same runs under a watch value 34 bpm above them: the spike check stays
    r = TC.assess({"value": 162.0, "source_kind": "prior"}, {"value": 180.0, "source_kind": "watch"},
                  {"value": None}, TODAY, runs, {})
    assert any(s["id"] == "sustained" for s in r["hrmax"]["signals"])


# ---- 8. SP-573: no CP says why; the suggestion and the load-time estimate agree -----------

def test_without_a_cp_the_estimate_says_why_and_links_the_lthr_test():
    from backend.i18n import use_locale
    # a Stryd runner (功率來源 set) without any CP yet — SP-950: a runner with no power meter (③) gets
    # no_meter_lthr instead (no test suggested; test 1)
    ds = _DS(_history("trained"), plan=Plan(profile={"power_source": "stryd"}))
    lt = TH.estimate(ds, TODAY)["lthr"]
    # the link of threshold_confidence's test suggestion (「安排課表：LTHR 測試…」, the Friel 30-min template)
    assert lt["links"] == [TC.schedule_link("tt30")]
    assert lt["links"][0]["test"] == "tt30" and "friel_lthr30" in lt["links"][0]["href"]
    assert "LTHR 測試" in lt["links"][0]["label"]
    with use_locale("en"):
        en = TH.estimate(ds, TODAY)["lthr"]
    assert en["value"] is None and en["reason"].startswith("No CP (no Stryd power and no CP test)")
    assert "30-minute LTHR test" in en["reason"] and en["links"][0]["label"] == "Schedule: LTHR test (30-min solo run)"
    assert en["links"][0]["href"] == lt["links"][0]["href"]


def test_hr_only_does_not_refit_a_cp_for_the_suggestion(monkeypatch):
    """No power at all: cp_as_of has nothing to fit, so the default path does not call it (no PD
    refits per run date for nothing) and says why straight away."""
    ds = _DS(_history("trained"))
    monkeypatch.setattr(A, "cp_as_of", lambda *a, **k: pytest.fail("cp_as_of called"))
    assert TH.estimate(ds, TODAY)["lthr"]["no_cp"]


def test_a_stryd_cp_keeps_the_default_path(monkeypatch):
    """Dataset.cp has a value (a Stryd fit / a plan test): the default path is what it was."""
    ds = _DS(_history("trained", scale_of=lambda d: 1.0), src_of=lambda w: PS.STRYD, cp_charts=STRYD_CP)
    monkeypatch.setattr(A, "cp_as_of", lambda *a, **k: pytest.fail("cp_as_of called"))
    est = TH.estimate(ds, TODAY)
    assert est["cp"] == STRYD_CP and abs(est["lthr"]["value"] - TRUE_LTHR) <= 2


# SP-950: the app never accepts watch power any more (power_source.effective_accept) — only the stored-off case
@pytest.mark.parametrize("accept", [False])
def test_the_threshold_estimate_api_gives_the_load_time_value_for_watch_power_only(monkeypatch, accept):
    """GET /plan/threshold-estimate (設定 › 閾值's 「從活動估計」) for a watch-power-only runner: the value
    fitdataset._estimate_settings writes into runthr on a grid day — thresholds.estimate(ds, day,
    cp_of=cp_as_of) — not an empty card while the 課表 already uses that estimate."""
    from backend.api import plan as PL
    ds = _DS(_history("trained", scale_of=lambda d: WATCH_SCALE), src_of=lambda w: PS.WATCH, accept_watch=accept)
    at_load = TH.estimate(ds, TODAY, cp_of=lambda d: A.cp_as_of(ds, d))["lthr"]["value"]
    # SP-950 (owner 2026-10-27): no wrist-power CP without accepted watch power, so no value at load
    # either — the card and the load agree (no value) instead of agreeing on a wrist-power value
    assert (at_load is None) if not accept else abs(at_load - TRUE_LTHR) <= 2
    A._cp_memo.clear()
    monkeypatch.setattr(PL, "_estimate_dataset", lambda: ds)
    monkeypatch.setattr(PL, "_estimate_pending", lambda ds: None)
    monkeypatch.setattr(PL, "today_local", lambda: TODAY)
    got = PL.threshold_estimate()
    # SP-950: both empty — the card says 沒有功率計 (watch power is no meter; no test suggested)
    assert got["lthr"]["value"] is None and at_load is None and got["lthr"].get("no_meter")


# ---- 9. SP-574: 「接受手錶功率」's help says what the setting really changes ----------------------

def test_the_accept_watch_power_help_matches_the_cp_behaviour():
    """The 課表 / charts CP stays Stryd-only with the setting on (test 4 above: stryd_cp_fit), while
    the race-power model, power load and the LTHR estimate's CP (test 5: cp_as_of fits both) read wrist
    power; the help (設定 › 功率 › 進階設定, zh + en) and repository.py's comment say so."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    cat = {loc: json.loads((root / "backend/static/i18n" / loc / "settings.json").read_text("utf-8"))
           for loc in ("zh-TW", "en")}
    zh, en = cat["zh-TW"]["power.watch_help"], cat["en"]["power.watch_help"]
    for s in ("賽事功率的模型", "功率負荷", "估 LTHR", "課表和圖表的 CP 不會因此改變", "只用 Stryd 功率擬合",
              "用手錶功率做的 CP 測試"):
        assert s in zh, s
    for s in ("race power model", "Power load", "LTHR", "The CP of the plan and the charts does not change",
              "Stryd power only", "CP test done with watch power"):
        assert s in en, s
    html = (root / "backend/static/settings.html").read_text("utf-8")
    # SP-955 (owner 2026-10-27, SP-950's 後來的修正): watch power is no power source — the control and its help
    # left the settings page (the stored value is ignored, power_state.accept_watch); the catalog text stays
    assert 'data-i18n="settings.power.watch"' not in html and "title:settings.power.watch_help" not in html
    assert 'id="u-watch"' not in html
    repo = (root / "backend/settings/repository.py").read_text("utf-8")
    assert "SP-574" in repo and "NOT the 課表 / charts CP" in repo and "enter a CP test made with wrist power" in repo
    # the behaviour the text describes
    switch = TODAY - dt.timedelta(days=40)
    ws = _history("trained", scale_of=lambda d: WATCH_SCALE if d < switch else 1.0)
    src = {fw.start: PS.WATCH if fw.start.date() < switch else PS.STRYD for fw in ws}
    on = _DS(ws, src_of=lambda w: src[w.entry.start], accept_watch=True)
    off = _DS(ws, src_of=lambda w: src[w.entry.start])
    assert A.cp_as_of(on, TODAY) > A.cp_as_of(off, TODAY)            # the LTHR estimate's CP reads wrist power
