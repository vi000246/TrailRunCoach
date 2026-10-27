"""SP-963 (owner 2026-10-27, SP-950's child): 「CP 有效」 is not only a formal CP test or a race in 42 days —
the Stryd PD fit anchored by recent all-out efforts counts too: in the last 42 days at least one short
(~3–5 min) and one long (~12–30 min) mean-max of a Stryd run at ≥ 95 % of the PD model in effect that day
(engine/power_state.efforts_on). One definition for today's state, a past day (fresh_on / day_power: the
per-activity analysis, SP-952 / SP-954), base_check.cp_stale and the calculator (power_state.current).
Synthetic data only (wko5_fakes), never the WKO5 folder."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from backend.engine import base_check as BCK
from backend.engine import power_source as PS
from backend.engine import power_state as PST
from backend.engine.algorithms import wko5_pdmodel as PDM
from backend.engine.planning import Plan
from backend.engine.wko5expr.dataset import day_to_date
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 27)
CP = 250.0
# the PD model in effect (WKO5's [FRC, tau1, FTP, tau2, TTE, D]): ≈ 300 W at 5 min, 271 W at 12, 262 W at 20
PARAMS = [15000.0, 15.0, CP, 25.0, 2400.0, -30.0]


def _p(t: float) -> float:
    return PDM.model(PARAMS, t)


def _run(day: dt.date, segs, watch: bool = False, title: str = "") -> FakeWorkout:
    """A 1-Hz run on `day`: segs = [(seconds, W)]; `watch`: the power is the watch's (not Stryd)."""
    pw = np.concatenate([np.full(int(d), float(w)) for d, w in segs])
    n = len(pw)
    ch = {"elapsedtime": np.arange(n, dtype=float).tolist(), "power": pw.tolist(),
          "heartrate": np.full(n, 140.0).tolist()}
    tags = ["watch"] if watch else []
    return FakeWorkout(start=dt.datetime.combine(day, dt.time(7, 0)), sport="run", sport_type="running",
                       channels=ch, title=title, tags=tags, metrics={"duration": float(n)})


def easy(day):            # 40 min at 72 % CP: far below the curve everywhere
    return _run(day, [(2400, 180)])


def short(day, watch=False):  # 4 × 5′ at 98 % of the model's 5-min power, 3′ jogs: a VO2 session
    seg = [(600, 170)]                                    # (its 12-min best ≈ 92 % of the model: short only)
    for _ in range(4):
        seg += [(300, 0.98 * _p(300)), (180, 120)]
    return _run(day, seg + [(300, 160)], watch)


def long(day, watch=False):   # 20 min at 97 % of the model's 20-min power, nothing hard before it
    return _run(day, [(900, 170), (1200, 0.97 * _p(1200)), (600, 160)], watch)


class _DS(FakeDataset):
    """A Dataset that can tell its power source (Stryd, or the watch for runs tagged "watch") and has a
    Stryd PD model in effect (FitFolderDataset.stryd_pd_params) — fitted on `fit_day`."""

    def __init__(self, workouts, plan=None, fit_day=dt.date(2026, 8, 1), params=PARAMS):
        super().__init__(workouts, TODAY, settings={"runthr": 170.0})
        self.plan = plan if plan is not None else Plan(profile={"power_source": "stryd"})
        self.plan_test_sessions = []
        self.fit_day, self.params = fit_day, params
        self.calls = 0

    def power_source(self, w):
        if self.channel(w.idx, "power") is None:
            return PS.NONE
        return PS.WATCH if "watch" in w.tags else PS.STRYD

    def power_ok(self, w):
        return self.power_source(w) == PS.STRYD

    def cp(self, w):
        return CP

    def stryd_pd_params(self, day):
        self.calls += 1
        if day < self.fit_day:
            return None
        return {"date": self.fit_day, "params": list(self.params), "mftp": CP}


def _ago(n: int) -> dt.date:
    return TODAY - dt.timedelta(days=n)


def _state(ds, **kw):
    return PST.state(TODAY, ds.plan.profile, ds=ds, cp=CP, **kw)


# ---- 1. anchored: ① with what anchors it and until when -----------------------------------------

def test_a_short_and_a_long_effort_in_42_days_keep_the_cp_fresh_without_a_cp_test():
    ds = _DS([easy(_ago(30)), long(_ago(20)), easy(_ago(15)), short(_ago(10)), easy(_ago(2))])
    eff = PST.efforts_on(ds, TODAY)
    assert eff["anchored"]
    assert eff["short"]["date"] == _ago(10).isoformat() and 180 <= eff["short"]["seconds"] <= 300
    assert eff["short"]["pct"] >= PST.EFFORT_ON_CURVE
    assert eff["long"]["date"] == _ago(20).isoformat() and 720 <= eff["long"]["seconds"] <= 1800
    assert eff["until"] == (_ago(20) + dt.timedelta(days=42)).isoformat()      # the older one + 42
    st = _state(ds)
    assert st["state"] == PST.POWER and st["power"] and st["fresh"] and st["basis"] == "efforts"
    assert st["last_cp_test"] is None and st["days"] is None          # no CP test, no race
    assert st["valid_until"] == eff["until"] and st["left"] == 22
    assert st["stale_on"] == (_ago(20) + dt.timedelta(days=43)).isoformat()
    # the status line says until when and why, plain (R2): 「CP 250 W 有效到 11/18（10/7 跑出 …，10/17 跑出 …）, the older one first」
    r = st["reason"]
    assert r.startswith("CP 250 W 有效到 11/18（10/7 跑出 ") and "，10/17 跑出 " in r and r.endswith("）")
    assert st["basis_text"] in r and st["basis_text"].startswith("10/7 跑出 ")
    assert f"{eff['short']['watts']} W" in r and f"{eff['long']['watts']} W" in r


def test_one_run_with_both_efforts_is_named_once():
    # a CP-test-like session (3′/12′ style) not recognised as a CP test: both efforts from the same run
    ds = _DS([_run(_ago(5), [(600, 170), (300, 1.02 * _p(300)), (600, 150), (900, 0.99 * _p(900)), (300, 150)])])
    st = _state(ds)
    assert st["state"] == PST.POWER and st["basis"] == "efforts"
    assert st["reason"].startswith("CP 250 W 有效到 12/3（10/22 跑出 ") and st["reason"].count("跑出") == 1
    assert st["valid_until"] == (_ago(5) + dt.timedelta(days=42)).isoformat()


def test_a_cp_test_still_counts_and_is_named():
    ds = _DS([easy(_ago(3))])
    st = _state(ds, last_cp_test="2026-10-17")
    assert st["state"] == PST.POWER and st["basis"] == "test" and st["days"] == 10
    assert st["reason"] == "CP 250 W 有效到 11/28（10/17 做了 CP 測試）"
    assert st["valid_until"] == "2026-11-28" and st["stale_on"] == "2026-11-29" and st["left"] == 32
    race = _state(ds, last_race="2026-10-20")
    assert race["basis"] == "race" and race["reason"] == "CP 250 W 有效到 12/1（10/20 跑了比賽）"


def test_efforts_after_an_old_test_extend_it():
    ds = _DS([long(_ago(8)), short(_ago(6))])
    st = _state(ds, last_cp_test=_ago(40).isoformat())
    assert st["basis"] == "efforts" and st["valid_until"] == (_ago(8) + dt.timedelta(days=42)).isoformat()
    assert st["days"] == 40                       # 「CP 已 N 天沒測」 still counts from the test


# ---- 2. not anchored: ② and the reminder ---------------------------------------------------------

@pytest.mark.parametrize("runs", [
    pytest.param(lambda: [easy(_ago(n)) for n in (40, 30, 20, 10, 3)], id="only-easy-weeks"),
    pytest.param(lambda: [easy(_ago(30)), short(_ago(10)), easy(_ago(3))], id="only-short"),
    pytest.param(lambda: [easy(_ago(30)), long(_ago(10)), easy(_ago(3))], id="only-long"),
    pytest.param(lambda: [long(_ago(60)), short(_ago(50)), easy(_ago(10))], id="older-than-42-days"),
    pytest.param(lambda: [long(_ago(20), watch=True), short(_ago(10), watch=True)], id="watch-power"),
])
def test_without_a_short_and_a_long_effort_in_42_days_it_is_stale(runs):
    ds = _DS(runs())
    eff = PST.efforts_on(ds, TODAY)
    assert eff is not None and not eff["anchored"] and eff["until"] is None
    st = _state(ds)
    assert st["state"] == PST.STALE and not st["power"] and st["basis"] is None and st["valid_until"] is None
    # ② still asks for a CP test — or a hard session / race (SP-963)
    assert st["reason"] == ("還沒有 CP 測試或比賽，先用心率／配速；做一次 CP 測試就切回功率"
                            "，跑一次強度課或比賽也行")
    assert st["suggest"] == "cp"


def test_the_stale_reminder_with_an_old_test_names_its_age_and_the_hard_session_way_back():
    st = _state(_DS([easy(_ago(5))]), last_cp_test=_ago(50).isoformat())
    assert st["reason"] == "CP 已 50 天沒測，先用心率／配速；做一次 CP 測試就切回功率，跑一次強度課或比賽也行"


def test_no_pd_model_no_efforts():
    # a Dataset without a Stryd PD model (WKO5, test doubles) or a day before the first fit: nothing anchors
    ds = _DS([long(_ago(20)), short(_ago(10))], fit_day=TODAY + dt.timedelta(days=1))
    assert PST.efforts_on(ds, TODAY) is None and _state(ds)["state"] == PST.STALE
    plain = FakeDataset([long(_ago(20)), short(_ago(10))], TODAY)
    assert PST.efforts_on(plain, TODAY) is None


def test_a_lower_effort_is_not_on_the_curve():
    ds = _DS([_run(_ago(20), [(1200, 0.90 * _p(1200))]), short(_ago(10))])
    assert not PST.efforts_on(ds, TODAY)["anchored"]
    assert PST.efforts_on(ds, TODAY)["long"] is None


# ---- 3. one definition: a past day, cp_stale, per activity ----------------------------------------

def test_fresh_on_a_past_day_counts_the_efforts_of_that_day():
    ds = _DS([long(_ago(70)), short(_ago(65)), easy(_ago(40)), easy(_ago(5))])
    a = _ago(70) + dt.timedelta(days=42)                       # the long one's last day
    assert PST.fresh_on(_ago(60), (), (), ds=ds)               # both efforts 5–10 days before it
    assert PST.fresh_on(a, (), (), ds=ds)
    assert not PST.fresh_on(a + dt.timedelta(days=1), (), (), ds=ds)
    assert not PST.fresh_on(_ago(68), (), (), ds=ds)           # the short one not run yet
    assert not PST.fresh_on(_ago(60), (), ())                  # without the Dataset: tests / races only
    assert PST.fresh_on(_ago(10), [_ago(30).isoformat()], (), ds=ds)    # a CP test still counts
    # the activity analysis' per-day answer (SP-952): day_power reads the same
    assert PST.day_power(ds, _ago(60)) and not PST.day_power(ds, _ago(5))
    w_old = next(w for w in ds.workouts if day_to_date(w.day) == _ago(40))
    w_new = next(w for w in ds.workouts if day_to_date(w.day) == _ago(5))
    assert PST.activity_power(ds, w_old) and not PST.activity_power(ds, w_new)


def test_cp_stale_and_is_fresh_take_the_efforts_until():
    d = TODAY
    assert BCK.cp_stale(d, None, None) and BCK.cp_stale(d, None, None, None)
    assert not BCK.cp_stale(d, None, None, "2026-11-10")                  # efforts valid to 11/10
    assert not BCK.cp_stale(d, None, None, d.isoformat())                  # its last day
    assert BCK.cp_stale(d, None, None, "2026-10-26")                       # lapsed yesterday
    assert BCK.cp_stale(d, "2026-08-01", None, "2026-10-01")
    assert PST.is_fresh(d, None, None, "2026-11-10") and not PST.is_fresh(d, None, None, "2026-10-26")


def test_current_reads_the_efforts_for_the_api_and_the_calculator():
    # GET /plan/power-state and the calculator (api/racepower.power_state, race_feasibility) read current()
    ds = _DS([long(_ago(20)), short(_ago(10))])
    ps = PST.current(ds, TODAY)
    assert ps["state"] == PST.POWER and ps["basis"] == "efforts" and ps["efforts"]["anchored"]
    assert ps["valid_until"] == (_ago(20) + dt.timedelta(days=42)).isoformat()
    assert "有效到" in ps["reason"] and "跑出" in ps["reason"]
    stale = PST.current(_DS([easy(_ago(20)), easy(_ago(10))]), TODAY)
    assert stale["state"] == PST.STALE and "跑一次強度課或比賽也行" in stale["reason"]


def test_no_meter_ignores_the_efforts():
    ds = _DS([long(_ago(20)), short(_ago(10))], plan=Plan(profile={"power_source": "none"}))
    st = _state(ds)
    assert st["state"] == PST.NONE and st["efforts"] is None


def test_efforts_are_memoised_per_dataset_and_day():
    ds = _DS([long(_ago(20)), short(_ago(10))])
    first = PST.efforts_on(ds, TODAY)
    n = ds.calls
    for w in ds.workouts:                       # activity analysis asks per activity
        PST.day_power(ds, day_to_date(w.day))
    PST.efforts_on(ds, TODAY)
    assert PST.efforts_on(ds, TODAY) is first and ds.calls == n + 2      # one model read per new day
