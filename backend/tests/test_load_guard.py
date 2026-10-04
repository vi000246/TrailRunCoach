"""SP-63 load-progression guardrails (engine/load_guard.py) and where they apply:
relative CTL ramp lines, the startup seed / skip, the running-time volume step
against max(the week before, 4-week mean), the planner's weekly CTL goal, adapt
rule E and B2B on the block line, and walks / hikes scoring hrTSS on the run
LTHR over moving time. Synthetic data only."""
import datetime as dt
from datetime import datetime, timezone

import numpy as np
import pytest

from backend.engine import adapt as A
from backend.engine import b2b as B2B
from backend.engine import load_guard as LG
from backend.engine import quality_gate as QG

# ---------------------------------------------------------------------------
# the lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ctl, watch, block, term", [
    (0.0, 3.0, 5.0, "floor"),
    (20.0, 3.0, 5.0, "floor"),          # 10 % = 2 < 3, 15 % = 3 < 5
    (40.0, 4.0, 6.0, "pct"),
    (60.0, 6.0, 9.0, "pct"),
    (66.0, 6.6, 9.9, "pct"),
    (80.0, 8.0, 10.0, "cap"),           # 15 % = 12 → Friel's ceiling 10
    (120.0, 12.0, 10.0, "cap"),         # high CTL: the cap is below the watch share — block wins
])
def test_relative_lines(ctl, watch, block, term):
    assert LG.watch_line(ctl) == pytest.approx(watch)
    assert LG.block_line(ctl) == pytest.approx(block)
    assert LG.block_term(ctl) == term


def test_level_and_missing_values():
    assert LG.ramp_level(None, 60.0) is None
    assert LG.ramp_level(5.9, 60.0) is None
    assert LG.ramp_level(6.0, 60.0) == LG.WATCH
    assert LG.ramp_level(9.0, 60.0) == LG.BLOCK
    assert LG.ramp_level(10.0, 200.0) == LG.BLOCK            # the cap holds at any CTL
    assert LG.ramp_level(3.0, None) == LG.WATCH               # no CTL₋₇: the floors 3 / 5
    assert LG.ramp_level(5.0, None) == LG.BLOCK


def test_old_five_eight_no_longer_fire_at_high_ctl():
    # the same +7 a week: a block at CTL 40 (6.0), only a watch at CTL 60, nothing at CTL 80
    assert LG.ramp_level(7.0, 40.0) == LG.BLOCK
    assert LG.ramp_level(7.0, 60.0) == LG.WATCH
    assert LG.ramp_level(7.0, 80.0) is None


# ---------------------------------------------------------------------------
# startup seed
# ---------------------------------------------------------------------------

def _pmc(x, c=42.0):
    v, out = 0.0, []
    for xv in x:
        v = v + (xv - v) / c
        out.append(v)
    return np.array(out)


def test_daily_load_inverts_the_pmc():
    rng = np.random.default_rng(1)
    x = np.concatenate([np.zeros(10), rng.uniform(0, 150, 200)])
    assert LG.daily_load(_pmc(x), 42.0) == pytest.approx(x, abs=1e-6)


def test_steady_training_has_no_startup_ramp_after_the_seed():
    x = np.concatenate([np.zeros(5), np.full(120, 60.0)])
    ctl = _pmc(x)
    for day in (5 + 10, 5 + 27):
        g = LG.guard_ramp(ctl, 0, day, 42.0)
        assert g["startup"] and g["level"] is None
    for n in (28, 42, 70):
        g = LG.guard_ramp(ctl, 0, 5 + n, 42.0)
        assert not g["startup"] and g["ramp"] == pytest.approx(0.0, abs=1e-9) and g["level"] is None
        raw = ctl[5 + n] - ctl[5 + n - 7]
        if n == 42:
            assert raw / ctl[5 + n - 7] > 0.10              # unseeded: the PMC filling up looks like a ramp


def test_a_real_spike_after_startup_still_fires():
    x = np.concatenate([np.full(70, 50.0), np.full(7, 150.0)])     # a 3× week after 10 steady weeks
    g = LG.guard_ramp(_pmc(x), 0, 76, 42.0)
    assert not g["startup"] and g["level"] == LG.BLOCK
    assert g["ctl_prev"] == pytest.approx(50.0) and g["ramp"] > LG.block_line(50.0)


def test_no_data_and_out_of_range():
    assert LG.guard_ramp(np.zeros(30), 0, 29, 42.0)["startup"]
    assert LG.guard_ramp(np.zeros(30), 100, 10, 42.0)["ramp"] is None


# ---------------------------------------------------------------------------
# weekly volume step
# ---------------------------------------------------------------------------

def test_step_base_is_the_larger_of_last_week_and_the_four_week_mean():
    assert LG.step_base([5, 5, 5, 3.25]) == pytest.approx(4.5625)   # after a recovery week: the mean
    assert LG.step_base([4, 4, 4, 6]) == pytest.approx(6.0)         # after a big week: that week
    assert LG.step_base([0, 0, 0, 0]) is None


def test_back_to_normal_after_a_recovery_week_is_not_a_spike():
    # the app's own 3:1: 5 h, 5 h, 5 h, recovery 65 % (3.25 h), back to 5 h
    step, base = LG.volume_step(5.0, [5, 5, 5, 3.25])
    assert base == pytest.approx(4.5625) and step == pytest.approx(5.0 / 4.5625 - 1) and step < LG.STEP_HOLD
    old = 5.0 / 3.25 - 1                                             # the old last-week rule: +54 % → block
    assert old > LG.STEP_BLOCK


def test_a_genuine_volume_spike_still_blocks():
    step, _ = LG.volume_step(6.5, [5, 5, 5, 5])
    assert step > LG.STEP_BLOCK
    step, _ = LG.volume_step(5.6, [5, 5, 5, 5])
    assert LG.STEP_HOLD < step <= LG.STEP_BLOCK


# ---------------------------------------------------------------------------
# the planner's weekly CTL goal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind, ctl, goal", [
    ("base", 20.0, 2.0), ("base", 60.0, 3.0), ("base", 100.0, 5.0),
    ("specific", 20.0, 2.5), ("specific", 57.0, 3.99), ("specific", 100.0, 7.0),
    ("taper", 60.0, None),
])
def test_ramp_goal(kind, ctl, goal):
    g = LG.ramp_goal(kind, ctl)
    assert g == (None if goal is None else pytest.approx(goal))


def test_week_hours_uses_the_relative_goal():
    from backend.engine import projection as P
    lo, _, _ = P.week_hours("base", [10.0] * 6, [False] * 6, 20.0, 50.0, 42.0, None)
    hi, _, why = P.week_hours("base", [10.0] * 6, [False] * 6, 100.0, 50.0, 42.0, None)
    assert lo == pytest.approx(10.0) and "+2.0" not in why[0]       # need < 4-week mean → the mean
    assert "CTL 100 每週 +5.0" in why[0]


# ---------------------------------------------------------------------------
# consumers: quality gate, adapt rule E (block line), B2B
# ---------------------------------------------------------------------------

def test_guard_reads_the_base():
    assert QG.guard(ramp=7.0, ramp_base=80.0)["rule"] == ""
    assert QG.guard(ramp=7.0, ramp_base=60.0)["sub"]
    assert QG.guard(ramp=7.0, ramp_base=40.0)["block"]


def _adapt(**load):
    from backend.tests.test_adapt import ctx, g, ids, week
    gw = week([g("quality", "quality", "2026-10-01", 60), g("easy1", "easy", "2026-10-02", 50)])
    out, adj, _ = A.adapt(gw, [], ctx(load=load))
    return "quality" in ids(out), [a for a in adj if a["rule"] == "fatigue"]


def test_adapt_rule_e_maps_to_the_block_line_not_the_watch_line():
    # CTL₋₇ 60: watch 6, block 9 — +7 (≥ the 7 of before 2026-10-01, under the old 8) is only a watch
    kept, adj = _adapt(ramp=7.0, ramp_base=60.0)
    assert kept and not adj
    kept, adj = _adapt(ramp=9.2, ramp_base=60.0)
    assert not kept and any(a["action"] == "removed" and "CTL 60 的 15%" in a["reason"] for a in adj)
    kept, adj = _adapt(ramp=9.2, ramp_base=80.0)                     # block line capped at 10
    assert kept and not adj


def test_b2b_blocks_on_the_block_line():
    def ctx(ramp, base):
        return B2B.week_context(kind="specific", mode="specific", monday=dt.date(2026, 10, 5),
                                event={"start": "2026-12-05", "days": 2, "hours": 20.0, "name": "x"},
                                last_recovery=True, state={"last": None, "count": 0}, tsb=-5.0,
                                ramp=ramp, ramp_base=base)
    assert not any("CTL 每週" in b for b in ctx(7.0, 60.0)["blocked"])
    assert any("CTL 每週" in b for b in ctx(9.5, 60.0)["blocked"])
    info = {"due": True}
    assert B2B.tsb_exempt(info, -25.0, ramp=7.0, ramp_base=60.0)
    assert B2B.tsb_exempt(info, -25.0, ramp=9.5, ramp_base=60.0) is None


# ---------------------------------------------------------------------------
# walks / hikes: the run LTHR, moving time; strength stays 0; parity untouched
# ---------------------------------------------------------------------------

UTC = timezone.utc
TODAY = dt.date(2026, 10, 2)


@pytest.fixture
def no_plan_lthr(monkeypatch, tmp_path):
    from backend.engine import planning
    from backend.engine import thresholds as TH
    from backend.engine.racepower import athlete as RA
    from backend.engine.racepower import weather as WX
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: planning.Plan()))
    monkeypatch.setattr(WX, "HOME", tmp_path / "home")
    monkeypatch.setattr(RA, "pd_model", lambda *a, **k: None)
    monkeypatch.setattr(TH, "estimate", lambda ds, day, cp_of=None: {})


def _ds(tmp_path, config):
    from backend.engine.wko5expr.fitdataset import FitFolderDataset
    from backend.tests.fit_builder import build_run
    d = tmp_path / "fit" / "coros" / "2026"
    d.mkdir(parents=True, exist_ok=True)
    stop = [1.2] * 1800 + [0.0] * 1800                       # 30 min walking, 30 min stopped (HR still on)
    files = {
        "run": build_run(datetime(2026, 9, 1, 8, tzinfo=UTC), seconds=1800, hr=150),
        "trail": build_run(datetime(2026, 9, 2, 8, tzinfo=UTC), hr=150, sub_sport=3,
                           speeds_m_s=[2.5] * 1800 + [0.0] * 600),
        "hike": build_run(datetime(2026, 9, 3, 8, tzinfo=UTC), hr=120, sport=17, speeds_m_s=stop),
        "strength": build_run(datetime(2026, 9, 4, 8, tzinfo=UTC), seconds=1800, hr=130, sport=10,
                              sub_sport=20, speed_m_s=0.0),
    }
    for k, b in files.items():
        (d / f"{k}.fit").write_bytes(b)
    ds = FitFolderDataset(tmp_path / "fit" / "coros", config=config, today=TODAY, classifications={},
                          athlete_settings=[{"effective_date": "2026-08-01", "lthr": 160.0}],
                          estimate_thresholds=True, tz=UTC)
    return {w.entry.file.split("/")[-1][:-4]: w for w in ds.workouts}, ds


def test_hike_scores_on_the_run_lthr_over_moving_time(tmp_path, no_plan_lthr):
    from backend.engine.wko5expr.config import EngineConfig
    w, ds = _ds(tmp_path, EngineConfig(parity=False, hr_tss_moving_only=False))
    assert ds.setting("runthr", w["hike"].day) == 160.0 and ds.sport_setting("thr", w["hike"]) is None
    hike = w["hike"].metrics
    assert hike["tss"] is not None and hike["hrtss_moving"] == hike["tss"]
    assert hike["tss"] < hike["hrtss"]                       # the stopped half is not charged
    # the knob is off: the trail run keeps recorded-time hrTSS (unchanged by SP-63)
    assert "hrtss_moving" not in w["trail"].metrics
    assert w["trail"].metrics["tss"] == pytest.approx(w["trail"].metrics["hrtss"])
    assert w["strength"].metrics["tss"] is None              # strength stays 0 TSS
    assert ds.aethr(w["hike"]) is None                       # the 80/20 share does not see hikes


def test_moving_knob_on_still_covers_runs(tmp_path, no_plan_lthr):
    from backend.engine.wko5expr.config import EngineConfig
    w, _ = _ds(tmp_path, EngineConfig(parity=False, hr_tss_moving_only=True))
    assert w["trail"].metrics["hrtss_moving"] < w["trail"].metrics["hrtss"]
    assert w["hike"].metrics["hrtss_moving"] == w["hike"].metrics["tss"]


def test_parity_mode_is_untouched(tmp_path, no_plan_lthr):
    from backend.engine.wko5expr.config import EngineConfig
    w, ds = _ds(tmp_path, EngineConfig(parity=True))
    assert ds.hr_lthr(w["hike"]) is None and not ds.moving_hrtss_on(w["hike"])
    assert w["hike"].metrics["tss"] is None
    assert "hrtss_moving" not in w["run"].metrics


# ---------------------------------------------------------------------------
# status: the fitness / volume indicators carry the guardrail values
# ---------------------------------------------------------------------------

def _status(sessions, today=dt.date(2026, 9, 30)):
    """sessions: [(date, sport, hours, tss)] → a Status on a FakeDataset (no channels)."""
    from backend.engine.planning import Plan
    from backend.engine.status import Status
    from backend.tests.wko5_fakes import FakeDataset, FakeWorkout
    ws = [FakeWorkout(start=dt.datetime.combine(d, dt.time(7)), sport=sp, sport_type=sp,
                      metrics={"tss": tss, "duration": h * 3600, "movingduration": h * 3600})
          for d, sp, h, tss in sessions]
    ds = FakeDataset(ws, today)
    ds.plan = Plan()
    return Status(ds, ds.plan, today)


def _weekly(today, weeks, run_h, extra=()):
    """`weeks` complete weeks before this one: run_h[i] hours of running a week (3 runs), oldest first."""
    mon = today - dt.timedelta(days=today.weekday())
    out = []
    for i, h in enumerate(run_h):
        s = mon - dt.timedelta(weeks=weeks - i)
        out += [(s + dt.timedelta(days=k), "run", h / 3, 60.0 * h / 3) for k in (1, 3, 5)]
    return out + list(extra)


def test_status_startup_has_no_guardrail_ramp():
    today = dt.date(2026, 9, 30)
    st = _status([(today - dt.timedelta(days=d), "run", 1.0, 80.0) for d in range(1, 20)], today)
    f = st.i_fitness()
    assert f.extra["ramp_startup"] and f.extra["ramp_week"] is None and "起算期" in f.verdict
    assert f.value is not None                               # the PMC CTL is still shown


def test_status_volume_step_is_running_time_against_the_larger_base():
    today = dt.date(2026, 9, 30)
    # 4 normal weeks, a recovery week (65 %), back to normal last week
    st = _status(_weekly(today, 6, [5, 5, 5, 5, 3.25, 5]), today)
    v = st.i_volume()
    assert v.extra["run_base"] == pytest.approx(4.5625) and v.extra["step"] < LG.STEP_HOLD
    assert v.level == "good"
    # a big 百岳 weekend last week: all-sport hours jump, running did not
    hike = [(today - dt.timedelta(days=today.weekday() + 2), "walk", 9.0, 0.0)]
    st = _status(_weekly(today, 6, [5, 5, 5, 5, 5, 5], hike), today)
    v = st.i_volume()
    assert v.extra["last_week"] == pytest.approx(14.0) and v.extra["step"] == pytest.approx(0.0)
    # a genuine running spike
    st = _status(_weekly(today, 6, [5, 5, 5, 5, 5, 6.5]), today)
    v = st.i_volume()
    assert v.extra["step"] == pytest.approx(0.3) and v.level == "bad" and "跑步時間" in v.verdict
