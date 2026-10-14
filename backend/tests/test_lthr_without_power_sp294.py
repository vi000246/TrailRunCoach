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


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
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
    # 8 tempo blocks at exactly LTHR, and still nothing: without a CP estimate_lthr is not even
    # called (thresholds.estimate: `lt = … if cp_now else None`), so there is no reason text either
    assert est["lthr"] is None
    assert est["aethr"]["value"] is None and est["aethr"]["n"] == 0   # the drift test needs power too


# ---- 2. what fills the gap today: the SP-289 prior 0.90 × max HR ---------------------

def test_hr_only_prior_comes_from_the_runs_max_hr_and_beats_the_age_formula():
    trained = _DS(_history("trained"), plan=Plan(profile={"birth_year": TODAY.year - 40}))
    m = HP.max_hr(trained, TODAY)
    assert m["kind"] == "estimate" and 183 <= m["value"] <= 187   # the hard sessions' 12-min block
    p = HP.lthr_prior(trained, TODAY)
    assert 165 <= p["value"] <= 168                                 # truth 170: 2–5 bpm low
    easy = _DS(_history("easy"), plan=Plan(profile={"birth_year": TODAY.year - 40}))
    m = HP.max_hr(easy, TODAY)
    # an easy-only runner's 「max HR」 is the top of his easy runs — and it wins over the age formula
    assert m["kind"] == "estimate" and m["value"] <= 150
    assert HP.age_max_hr(40) == 180.0
    p = HP.lthr_prior(easy, TODAY)
    assert p["value"] <= 135                                        # 35 bpm below the same physiology's 170


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


def test_watch_power_only_cp_as_of_fits_the_watch_power_and_the_lthr_estimate_works():
    ds = _watch_only()
    assert not ds.power_ok(ds.workouts[0])                     # power.accept_watch_power off (default)
    cp = A.cp_as_of(ds, TODAY)
    # _pd_mftp: no usable (Stryd) power in the window → pd_model(any_power=True) on the wrist power
    assert cp is not None and 1.15 * STRYD_CP <= cp <= 1.45 * STRYD_CP
    est = TH.estimate(ds, TODAY, cp_of=lambda d: A.cp_as_of(ds, d))
    assert abs(est["lthr"]["value"] - TRUE_LTHR) <= 2          # same scale in and out: it works
    # the as-of path (fitdataset._estimate_settings → runthr) has it; Dataset.cp (charts, 課表, the
    # threshold-estimate API's default path) has no Stryd fit → None → no estimate there
    est0 = TH.estimate(ds, TODAY)
    assert est0["cp"] is None and est0["lthr"] is None


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
    # the wrist runs of the 90-day window still enter run_threshold (_per_run reads `power` without
    # power_ok): their easy 200 W × 1.3 = 260 W clears 95 % of the Stryd CP, so easy-run HR counts
    assert lt["value"] < TRUE_LTHR - 15 and lt["days"] == 90
    # with only the Stryd runs the same estimate is right
    stryd_only = _DS([fw for fw in ws if fw.start.date() >= switch], src_of=lambda w: PS.STRYD)
    ok = TH.estimate(stryd_only, TODAY, cp_of=lambda d: cp)
    assert abs(ok["lthr"]["value"] - TRUE_LTHR) <= 2


# ---- 6. what already reacts to a too-low prior: threshold_confidence ---------------------

@pytest.mark.parametrize("temp,level,priority", [(None, "hint", "low"), ("cool", "strong", None)])
def test_a_prior_below_the_easy_runs_hr_asks_for_the_tests(temp, level, priority):
    # the easy-only runner of test 2: prior 0.90 × 147 = 132, easy runs at ~146 for 60 min
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
