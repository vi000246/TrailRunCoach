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
from backend.engine.wko5expr.dataset import date_to_day

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
# PMC start values (SP-68) and the startup window
# ---------------------------------------------------------------------------

def _pmc(x, c=42.0):
    v, out = 0.0, []
    for xv in x:
        v = v + (xv - v) / c
        out.append(v)
    return np.array(out)


def _started(x, today, manual=None, c=42.0, s=0):
    st = LG.pmc_start(x, s, today, manual)
    return LG.pmc_series(x, c, s, st, "ctl"), st


def test_start_order_manual_then_auto_then_zero():
    x = np.concatenate([np.zeros(5), np.full(40, 60.0), np.full(40, 30.0)])
    st = LG.pmc_start(x, 100, 100 + 84)
    assert st["source"] == LG.AUTO and st["day"] == 105                       # the first day with TSS
    assert st["ctl"] == st["atl"] == pytest.approx(60.0)                      # mean of its first 28 days
    assert st["auto"] == {"day": 105, "seed": pytest.approx(60.0), "days": 28}
    early = LG.pmc_start(x, 100, 100 + 14)                                    # only days ≤ today count
    assert early["ctl"] == pytest.approx(60.0) and early["auto"]["days"] == 10
    man = {"date": "2026-09-01", "ctl": 55.0, "atl": 40.0}
    md = int(date_to_day(dt.date(2026, 9, 1)))
    st = LG.pmc_start(x, md - 50, md + 3, man)
    assert (st["source"], st["day"], st["ctl"], st["atl"]) == (LG.MANUAL, md, 55.0, 40.0)
    assert st["auto"]["seed"] > 0                                             # still shown on the settings card
    assert LG.pmc_start(x, md - 50, md - 1, man)["source"] == LG.AUTO          # dated after today: ignored
    assert LG.pmc_start(np.zeros(30), 0, 29, None)["source"] == LG.NONE
    assert LG.pmc_start(np.zeros(30), md - 29, md, man)["source"] == LG.MANUAL  # a manual start needs no TSS


@pytest.mark.parametrize("v, ok", [
    ({"date": "2026-09-01", "ctl": 55, "atl": 40}, True),
    ({"date": "2026-09-01", "ctl": 0, "atl": 0}, True),
    ({"date": "2026-09-31", "ctl": 55, "atl": 40}, False),
    ({"date": "2026-09-01", "ctl": -1, "atl": 40}, False),
    ({"date": "2026-09-01", "ctl": 55, "atl": 400}, False),
    ({"date": "2026-09-01", "ctl": "x", "atl": 40}, False),
    (None, False), ([], False),
])
def test_parse_manual(v, ok):
    assert (LG.parse_manual(v) is not None) == ok


def test_recur_without_a_start_is_wko5_tl():
    x = np.concatenate([np.zeros(10), np.random.default_rng(1).uniform(0, 150, 200)])
    assert LG.recur(x, 42.0, 0, None, 0.0) == pytest.approx(_pmc(x))
    # a start before the series decays over the rest days in between
    assert LG.recur(np.zeros(3), 42.0, 10, 5, 42.0)[0] == pytest.approx(42.0 * (41 / 42) ** 6)


def test_steady_training_has_no_startup_ramp_after_the_seed():
    x = np.concatenate([np.zeros(5), np.full(120, 60.0)])
    ctl, st = _started(x, 124)
    assert ctl[5:] == pytest.approx(60.0)                                     # no filling-up
    for day in (5 + 10, 5 + 27):
        g = LG.guard_ramp(ctl, 0, day, st)
        assert g["startup"] and g["level"] is None and g["source"] == LG.AUTO
    for n in (28, 42, 70):
        g = LG.guard_ramp(ctl, 0, 5 + n, st)
        assert not g["startup"] and g["ramp"] == pytest.approx(0.0, abs=1e-9) and g["level"] is None
        raw = _pmc(x)
        if n == 42:
            assert (raw[5 + n] - raw[5 + n - 7]) / raw[5 + n - 7] > 0.10      # from 0: the PMC filling up looks like a ramp


def test_a_real_spike_after_startup_still_fires():
    x = np.concatenate([np.full(70, 50.0), np.full(7, 150.0)])     # a 3× week after 10 steady weeks
    ctl, st = _started(x, 76)
    g = LG.guard_ramp(ctl, 0, 76, st)
    assert not g["startup"] and g["level"] == LG.BLOCK
    assert g["ctl_prev"] == pytest.approx(50.0) and g["ramp"] > LG.block_line(50.0)


def test_manual_start_skips_only_its_first_week():
    md = int(date_to_day(dt.date(2026, 9, 1)))
    s = md - 20
    x = np.full(60, 50.0)
    man = {"date": "2026-09-01", "ctl": 50.0, "atl": 50.0}
    ctl, st = _started(x, md + 30, man, s=s)
    ctl0, _ = _started(x, md + 30, None, s=s)
    assert ctl[19] == pytest.approx(ctl0[19]) and ctl[20] == pytest.approx(50.0)   # the seed, then its value
    g = LG.guard_ramp(ctl, s, md + 6, st)
    assert g["startup"] and g["source"] == LG.MANUAL and g["day_n"] == 6
    g = LG.guard_ramp(ctl, s, md + 7, st)
    assert not g["startup"] and g["ramp"] == pytest.approx(0.0, abs=1e-9)


def test_no_data_and_out_of_range():
    st = LG.pmc_start(np.zeros(30), 0, 29)
    assert LG.guard_ramp(np.zeros(30), 0, 29, st)["startup"]
    assert LG.guard_ramp(np.zeros(30), 100, 10, st)["ramp"] is None
    assert LG.guard_ramp(np.zeros(30), 0, 29, None)["startup"]


# ---- the PMC builtins read the same start (charts = status = week_plan / adapt TSB) ----

def _ev_ds(days=70, tss=60.0):
    from backend.tests.wko5_fakes import FakeDataset, FakeWorkout
    today = dt.date(2026, 9, 29)
    ws = [FakeWorkout(datetime(2026, 9, 29, 8) - dt.timedelta(days=i), metrics={"tss": tss}) for i in range(days)]
    return FakeDataset(ws, today)


def test_builtins_start_from_the_seed_tl_stays_wko5(monkeypatch):
    from backend.engine.wko5expr.evaluator import Evaluator
    monkeypatch.setattr(LG, "manual_start", lambda user_id=1: None)
    ds = _ev_ds()
    ev = Evaluator(ds, ds.today - 60, ds.today)
    ctl, atl, tsb = ev.evaluate("ctl"), ev.evaluate("atl"), ev.evaluate("tsb")
    raw = ev.evaluate("tl(tss, ctlconstant)")
    d0 = ds.first_day
    assert ctl.at(d0) == pytest.approx(60.0) and atl.at(d0) == pytest.approx(60.0)
    assert tsb.at(ds.today) == pytest.approx(0.0, abs=1e-9)                   # steady: no false −TSB
    assert raw.at(d0) == pytest.approx(60.0 / 42)                             # tl() unchanged
    c2, a2, st = ev.pmc()
    assert st["source"] == LG.AUTO and c2.at(ds.today) == pytest.approx(ctl.at(ds.today))


def test_builtins_take_the_manual_start(monkeypatch):
    from backend.engine.wko5expr.evaluator import Evaluator
    monkeypatch.setattr(LG, "manual_start", lambda user_id=1: {"date": "2026-09-01", "ctl": 30.0, "atl": 80.0})
    ds = _ev_ds()
    ev = Evaluator(ds, ds.today - 60, ds.today)
    md = int(date_to_day(dt.date(2026, 9, 1)))
    ctl, atl = ev.evaluate("ctl"), ev.evaluate("atl")
    assert ctl.at(md) == pytest.approx(30.0 + (60.0 - 30.0) / 42)
    assert atl.at(md) == pytest.approx(80.0 + (60.0 - 80.0) / 7)
    assert ctl.at(md - 1) == pytest.approx(60.0)                              # before its date: the seed
    # a sport-filtered evaluator is not the whole-athlete PMC: the automatic seed
    ev_run = Evaluator(ds, ds.today - 60, ds.today, sports={"run"})
    assert ev_run.evaluate("ctl").at(md) == pytest.approx(60.0)


def test_new_user_no_false_recovery_week_in_the_startup_window(monkeypatch):
    """SP-63 Q3: week_plan's TSB < −30 → recovery week (and so load.tsb_today for adapt rule
    E) reads the started PMC. 2.5 weeks of steady data: TSB ≈ −12, not a recovery week; the
    same weeks from a cold 0 start (set by hand) give TSB −40 and the recovery week."""
    from backend.tests import test_b2b as TB
    today = dt.date(2026, 8, 19)
    monkeypatch.setattr(LG, "manual_start", lambda user_id=1: None)
    _, _, st, wp = TB._week(today=today, last_week="b2b", tph=1.2, b2b_tph=1.2)
    assert wp["load"]["tsb_today"] > -20 and wp["mode"] != "recovery_week"
    assert not any("TSB" in w for w in wp["why"])
    form = next(i for i in st.indicators if i.id == "form")
    assert form.value > -30                                                   # status 狀況 TSB agrees
    monkeypatch.setattr(LG, "manual_start", lambda user_id=1: {"date": "2026-08-03", "ctl": 0.0, "atl": 0.0})
    _, _, _, cold = TB._week(today=today, last_week="b2b", tph=1.2, b2b_tph=1.2)
    assert cold["load"]["tsb_today"] < -30 and cold["mode"] == "recovery_week"

def test_settings_key_validates():
    from backend.settings.repository import DEFAULTS, validate
    assert DEFAULTS[LG.PMC_START_KEY] is None
    validate(LG.PMC_START_KEY, None)
    validate(LG.PMC_START_KEY, {"date": "2026-09-01", "ctl": 55.0, "atl": 40.0})
    with pytest.raises(ValueError):
        validate(LG.PMC_START_KEY, {"date": "2026-09-01", "ctl": 500.0, "atl": 40.0})


def test_pmc_start_api(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api import plan as API
    from backend.db.database import get_db
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import make_db, run
    db = run(make_db(logged_in=False))

    async def fake_db():
        yield db
    stored = {}
    monkeypatch.setattr(LG, "manual_start", lambda user_id=1: LG.parse_manual(stored.get("v")))
    monkeypatch.setattr(API, "_overview_dataset", lambda: _ev_ds())
    monkeypatch.setattr(API, "today_local", lambda *a, **k: dt.date(2026, 9, 29))
    app = FastAPI()
    app.include_router(API.router)
    app.dependency_overrides[get_db] = fake_db
    c = TestClient(app)
    r = c.get("/api/v1/plan/pmc-start").json()
    assert r["source"] == LG.AUTO and r["effective"]["ctl"] == 60.0 and r["manual"] is None
    assert r["auto"] == {"date": "2026-07-22", "seed": 60.0, "days": 28, "final": True}
    assert r["now"]["tsb"] == pytest.approx(0.0, abs=0.05)
    for bad in ({"date": "2026-09-01", "ctl": 400, "atl": 40}, {"date": "2026-09-01", "ctl": 50},
                {"date": "2026-10-01", "ctl": 50, "atl": 40}):                    # after today
        assert c.put("/api/v1/plan/pmc-start", json=bad).status_code == 400
    assert c.put("/api/v1/plan/pmc-start", json={"date": "2026-09-01", "ctl": 30.04, "atl": 80}).status_code == 200
    stored["v"] = run(SettingsRepository(db).get(LG.PMC_START_KEY))
    assert stored["v"] == {"date": "2026-09-01", "ctl": 30.0, "atl": 80.0}
    r = c.get("/api/v1/plan/pmc-start").json()
    assert r["source"] == LG.MANUAL and r["effective"] == {"date": "2026-09-01", "ctl": 30.0, "atl": 80.0}
    assert r["auto"]["seed"] == 60.0                                          # still shown
    assert c.put("/api/v1/plan/pmc-start", json={"clear": True}).status_code == 200
    assert run(SettingsRepository(db).get(LG.PMC_START_KEY)) is None

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


def test_strength_is_zero_tss_even_with_a_plan_lthr(tmp_path, no_plan_lthr, monkeypatch):
    """SP-63 (owner 2026-10-04): a dated plan LTHR used to reach strength through
    setting("otherthr") and give it hrTSS; own formulas now keep it at 0. Parity unchanged."""
    from backend.engine import planning
    from backend.engine.wko5expr.config import EngineConfig
    plan = planning.Plan(thresholds=[planning.Threshold(date="2026-08-15", lthr=165.0)])
    monkeypatch.setattr(planning.Plan, "load", classmethod(lambda cls, *a, **k: plan))
    w, ds = _ds(tmp_path, EngineConfig(parity=False))
    assert ds.sport_setting("thr", w["strength"]) == 165.0       # the plan row still reaches otherthr
    assert ds.hr_lthr(w["strength"]) is None
    assert w["strength"].metrics["tss"] is None and w["strength"].metrics.get("tss_source") is None
    assert w["run"].metrics["tss"] is not None                    # runs keep their hrTSS
    w, ds = _ds(tmp_path, EngineConfig(parity=True))
    assert ds.hr_lthr(w["strength"]) == ds.sport_setting("thr", w["strength"])   # parity: WKO5's own rule


def test_parity_mode_is_untouched(tmp_path, no_plan_lthr):
    from backend.engine.wko5expr.config import EngineConfig
    w, ds = _ds(tmp_path, EngineConfig(parity=True))
    assert ds.hr_lthr(w["hike"]) is None and not ds.moving_hrtss_on(w["hike"])
    assert w["hike"].metrics["tss"] is None
    assert "hrtss_moving" not in w["run"].metrics


# ---------------------------------------------------------------------------
# status: the fitness / volume indicators carry the guardrail values
# ---------------------------------------------------------------------------

def _status(sessions, today=dt.date(2026, 9, 30), blackouts=(), prefs=None):
    """sessions: [(date, sport, hours, tss)] → a Status on a FakeDataset (no channels)."""
    from backend.engine.planning import Plan
    from backend.engine.status import Status
    from backend.tests.wko5_fakes import FakeDataset, FakeWorkout
    ws = [FakeWorkout(start=dt.datetime.combine(d, dt.time(7)), sport=sp, sport_type=sp,
                      metrics={"tss": tss, "duration": h * 3600, "movingduration": h * 3600})
          for d, sp, h, tss in sessions]
    ds = FakeDataset(ws, today)
    ds.plan = Plan()
    return Status(ds, ds.plan, today, blackouts=blackouts, prefs=prefs)


def _weekly(today, weeks, run_h, extra=()):
    """`weeks` complete weeks before this one: run_h[i] hours of running a week (3 runs), oldest first."""
    mon = today - dt.timedelta(days=today.weekday())
    out = []
    for i, h in enumerate(run_h):
        s = mon - dt.timedelta(weeks=weeks - i)
        out += [(s + dt.timedelta(days=k), "run", h / 3, 60.0 * h / 3) for k in (1, 3, 5)]
    return out + list(extra)


def _week(mon, days_h):
    """[(date, "run", hours, tss)] for runs on weekday offsets {day: hours} of the week at `mon`."""
    return [(mon + dt.timedelta(days=k), "run", h, 60.0 * h) for k, h in days_h.items()]


def test_short_break_exempts_the_next_weeks_volume_step():
    """SP-63 (owner 2026-10-04): 3–5 days without a run in the week before (no re-entry block)
    pulled the base down; coming back to normal last week is not a spike."""
    today = dt.date(2026, 9, 30)
    mon = today - dt.timedelta(days=today.weekday())
    normal = {1: 5 / 3, 3: 5 / 3, 5: 5 / 3}
    weeks = [normal] * 4

    def status(prev):
        ss = []
        for i, w in enumerate(weeks + [prev, normal]):
            ss += _week(mon - dt.timedelta(weeks=6 - i), w)
        return _status(ss, today)
    v = status({1: 0.75, 5: 0.75}).i_volume()                     # Tue + Sat: Wed–Fri off (3 days)
    assert v.extra["step"] > LG.STEP_BLOCK and v.extra["step_exempt"]
    assert v.level == "good" and "停跑 3 天" in v.verdict
    g = QG.guard(step=v.extra["step"], step_exempt=v.extra["step_exempt"])
    assert not g["block"] and not g["hold"] and "停跑 3 天" in g["step_note"]
    # 2 days off is routine rest: still a spike
    v = status({1: 0.5, 3: 0.5, 5: 0.5}).i_volume()
    assert v.extra["step"] > LG.STEP_BLOCK and not v.extra["step_exempt"] and v.level == "bad"
    # ≥ 6 days is a re-entry block (reentry.py), not this exemption
    v = status({5: 1.5}).i_volume()
    assert not v.extra["step_exempt"]
    assert QG.guard(step=0.3)["block"] and not QG.guard(step=0.3)["step_note"]


def test_planned_break_is_not_exempt_from_the_volume_step():
    """SP-63 (owner 2026-10-05): a 3–5-day gap on the user's own 不排課日期 or 休息日 is a chosen
    rest — the step check still runs; a partly planned gap is exempt only when its unplanned
    part alone is ≥ 3 days."""
    from backend.engine import blackouts as BL
    today = dt.date(2026, 9, 30)
    mon = today - dt.timedelta(days=today.weekday())
    normal = {1: 5 / 3, 3: 5 / 3, 5: 5 / 3}
    prev_mon = mon - dt.timedelta(weeks=2)

    def status(prev, bos):
        ss = []
        for i, w in enumerate([normal] * 4 + [prev, normal]):
            ss += _week(mon - dt.timedelta(weeks=6 - i), w)
        return _status(ss, today, blackouts=bos)

    def rng(a, b, kind=""):
        return BL.Blackout(id=f"b{a}{kind}", start=(prev_mon + dt.timedelta(days=a)).isoformat(),
                           end=(prev_mon + dt.timedelta(days=b)).isoformat(), kind=kind)
    three = {1: 0.75, 5: 0.75}                                    # Wed–Fri off (3 days)
    v = status(three, (rng(2, 4),)).i_volume()                    # all three are 不排課日期
    assert v.extra["step"] > LG.STEP_BLOCK and not v.extra["step_exempt"] and v.level == "bad"
    v = status(three, (rng(3, 3, "rest"),)).i_volume()            # one 休息日: 2 unplanned left
    assert not v.extra["step_exempt"] and v.level == "bad"
    five = {0: 0.75, 6: 0.75}                                     # Tue–Sat off (5 days)
    v = status(five, (rng(1, 2),)).i_volume()                     # 2 planned, 3 unplanned: exempt
    assert v.extra["step_exempt"] and v.level == "good"
    assert "非計畫停跑 3 天" in v.verdict and "另 2 天是自己排的休息" in v.verdict
    v = status(three, (rng(10, 12),)).i_volume()                  # a blackout elsewhere: no effect
    assert v.extra["step_exempt"] and "非計畫停跑 3 天" in v.verdict and "另" not in v.verdict


def test_unticked_training_days_are_a_planned_rest():
    """SP-63 (owner 2026-10-05): weekdays not ticked as 可練日 in 課表偏好 count as planned rest,
    like 不排課日期 / 休息日 — a Fri–Sun-only runner's weekly Mon–Thu gap is not an exemption."""
    from backend.engine import plan_prefs as PP
    today = dt.date(2026, 9, 30)
    mon = today - dt.timedelta(days=today.weekday())
    normal = {4: 5 / 3, 5: 5 / 3, 6: 5 / 3}                       # Fri–Sun only
    low = {4: 0.3, 5: 0.3, 6: 0.3}

    def status(prefs):
        ss = []
        for i, w in enumerate([normal] * 4 + [low, normal]):
            ss += _week(mon - dt.timedelta(weeks=6 - i), w)
        return _status(ss, today, prefs=prefs)
    weekend = PP.Prefs(days=(False,) * 4 + (True,) * 3)
    v = status(weekend).i_volume()
    assert v.extra["step"] > LG.STEP_BLOCK and not v.extra["step_exempt"] and v.level == "bad"
    # every day ticked: the same Mon–Thu gap is 4 unplanned days → exempt (unchanged)
    v = status(PP.Prefs()).i_volume()
    assert v.extra["step_exempt"] and v.level == "good" and "非計畫停跑 4 天" in v.verdict
    # Mon–Wed unticked, Thu ticked: 1 unplanned day left → not exempt
    v = status(PP.Prefs(days=(False,) * 3 + (True,) * 4)).i_volume()
    assert not v.extra["step_exempt"]
    # Mon unticked only: 3 unplanned → exempt, the note names the planned day
    v = status(PP.Prefs(days=(False,) + (True,) * 6)).i_volume()
    assert v.extra["step_exempt"] and "非計畫停跑 3 天" in v.verdict and "另 1 天是自己排的休息" in v.verdict
    assert "沒勾的可練日" in v.verdict


def test_short_break_note_reaches_the_week_plan():
    from backend.engine import overview as O
    from backend.engine import plan_prefs as PP
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    from backend.tests.test_quality_gate import TODAY as T
    ds = _history(T)
    ds.plan = _plan_with("2027-06-05", 1, T)
    st = Status(ds, ds.plan, T, prefs=PP.Prefs()).compute()
    g = next(i for i in st.indicators if i.id == "gate")
    assert g.extra["guard"]["step_note"] == ""
    assert not [n for n in O.week_plan(ds, st, T)["notes"] if n.get("src") == "volume"]
    g.extra["guard"]["step_note"] = "前一週停跑 4 天：這週不算增幅"
    wp = O.week_plan(ds, st, T)
    assert wp["phase"] == "base"
    assert {"level": "info", "src": "volume", "text": "前一週停跑 4 天：這週不算增幅"} in wp["notes"]


def test_short_break_window():
    assert LG.short_break([0, 4, 5], 0, 6) == (1, 3, 0)           # 3 days off inside the week
    assert LG.short_break([0, 4, 8], 0, 6) == (5, 7, 0)           # the latest one
    assert LG.short_break([0, 3, 8], 0, 6) == (4, 7, 0)           # 4 days, reaching into the next week
    assert LG.short_break([0, 3], 0, 6) is None                   # 2 days: routine rest
    assert LG.short_break([0, 7], 0, 6) is None                   # 6 days: re-entry block
    assert LG.short_break([0, 4], 10, 16) is None                 # not in the week before
    # owner 2026-10-05: the user's own 不排課日期／休息日 are not an unplanned break
    assert LG.short_break([0, 4], 0, 6, planned=[1, 2, 3]) is None        # fully planned
    assert LG.short_break([0, 4], 0, 6, planned=[2]) is None              # 2 unplanned left
    assert LG.short_break([0, 6], 0, 6, planned=[1, 2]) == (1, 5, 2)      # 5 days, 3 unplanned: exempt
    assert LG.short_break([0, 6], 0, 6, planned=[1, 2, 3]) is None        # 5 days, 2 unplanned
    assert LG.short_break([0, 4, 8], 0, 6, planned=[5, 6, 7]) == (1, 3, 0)  # the latest unplanned one


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
