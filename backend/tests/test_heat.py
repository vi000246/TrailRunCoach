"""
Heat acclimation — docs/research/heat-acclimation.md §6.1 fixed values, each
recomputed independently with a flat loop (no call into the function under
test for the expected value).
"""
from __future__ import annotations

import datetime as dt
import math

from pytest import approx

from backend.engine import heat as HT
from backend.engine.racepower import env as ENV

D0 = dt.date(2026, 6, 1)


def loop_s(n_on: int, n_off: int = 0, r: float = 0.025, s: float = 0.0) -> float:
    k = 1 - 0.3 ** (1 / 5)
    for _ in range(n_on):
        s = s + k * (1 - s)
    for _ in range(n_off):
        s = s * (1 - r)
    return s


def test_k_in_calibration():
    assert HT.K_IN == approx(0.2140, abs=1e-4)
    assert HT.K_IN == approx(1 - 0.3 ** 0.2)


def test_build_up_table():
    # the doc's table; its 7-day 0.82 is 0.8147 recomputed (a rounding slip in the doc)
    for n, exp in ((3, 0.51), (5, 0.700), (6, 0.76), (7, 0.815), (10, 0.910), (14, 0.966)):
        doses = {D0 + dt.timedelta(days=i): 1.0 for i in range(n)}
        s = HT.status_series(doses, D0, D0 + dt.timedelta(days=n - 1))[-1][1]
        assert s == approx(loop_s(n), abs=1e-12)
        assert s == approx(exp, abs=0.005)


def test_decay_rules_after_ten_days():
    doses = {D0 + dt.timedelta(days=i): 1.0 for i in range(10)}
    end = D0 + dt.timedelta(days=23)
    s_exp = HT.status_series(doses, D0, end)[-1][1]
    assert s_exp == approx(loop_s(10, 14), abs=1e-12) and s_exp == approx(0.638, abs=0.001)
    s_day = HT.status_series(doses, D0, end, rule="day_loss")[-1][1]
    # independent: 10 exposure days − 14 × 0.5 = 3 days → 1 − (1 − k)^3
    assert s_day == approx(1 - (0.3 ** 0.2) ** 3, abs=1e-9) and s_day == approx(0.514, abs=0.001)
    for r, exp in ((0.023, 0.66), (0.026, 0.63)):
        assert HT.status_series(doses, D0, end, decay=r)[-1][1] == approx(exp, abs=0.006)


def test_effective_heat_worked_example_and_bounds():
    assert HT.effective_heat(7.15, 0.5, 0.75) == approx(7.15 * (1 - 0.375)) == approx(4.47, abs=0.005)
    assert HT.effective_heat(7.15, 0.9, 0.75) == approx(2.32, abs=0.01)
    assert HT.effective_heat(7.15, 0.5, 0.35) == approx(5.90, abs=0.01)
    assert HT.effective_heat(7.15, 1.0, 1.0) == 0.0
    assert HT.A_RECOVER == ENV.A_RECOVER


def test_hadley_worked_values():
    assert ENV.heat_penalty_pct(30, 70) == approx(6.25, abs=0.01)
    assert HT.hadley_sum(30, 70) == approx(161, abs=0.5)
    assert HT.hadley_sum(28, 75) == approx(156, abs=0.5) and HT.race_is_hot(28, 75)
    assert HT.hadley_sum(25, 75) == approx(145, abs=0.5) and not HT.race_is_hot(25, 75)


def test_minute_weight_and_dose():
    assert HT.minute_weight(129) == 0 and HT.minute_weight(140) == approx(0.5) and HT.minute_weight(155) == 1
    assert HT.day_dose(30) == approx(0.5) and HT.day_dose(90) == 1.0
    assert HT.day_dose(0, passive_done=True) == 1.0 and HT.day_dose(30, True) == 1.0
    d = HT.doses_from([{"date": "2026-06-01", "hot_min": 20}, {"date": "2026-06-01", "hot_min": 25}],
                      passive_dates=["2026-06-03"])
    assert d[dt.date(2026, 6, 1)] == approx(0.75) and d[dt.date(2026, 6, 3)] == 1.0


def test_s_zero_leaves_m_bit_identical():
    frm = {"altitude_m": 100, "temp_c": 25, "rh_pct": 75}
    to = {"altitude_m": 500, "temp_c": 32, "rh_pct": 65}
    assert ENV.multiplier(frm, to, heat_s=(0.0, 0.0))["M"] == ENV.multiplier(frm, to)["M"]
    zs = [100, 800, 1500]
    assert ENV.segment_factors(zs, frm, to, "acclimatised", heat_s=(0.0, 0.0)) == ENV.segment_factors(zs, frm, to)


def test_both_sides_of_m_are_scaled():
    frm = {"altitude_m": 100, "temp_c": 30, "rh_pct": 70}
    to = {"altitude_m": 100, "temp_c": 32, "rh_pct": 65}
    hf, ht = ENV.heat_penalty_pct(30, 70), ENV.heat_penalty_pct(32, 65)
    m = ENV.multiplier(frm, to, heat_s=(0.4, 0.9), a=0.75)
    alt = -(ENV.altitude_factor(ENV.pressure_torr(100, 30)) - ENV.altitude_factor(ENV.pressure_torr(100, 32)))
    assert m["M"] == approx(1 + alt - (ht * (1 - 0.675) - hf * (1 - 0.3)) / 100)
    assert m["to"]["heat_penalty_eff_pct"] == approx(ht * (1 - 0.675))


def test_project_three_scenarios():
    pj = HT.project(0.5, D0, D0 + dt.timedelta(days=10))
    assert pj["center"] == approx(0.5 * 0.975 ** 10)
    assert pj["high"] == approx(0.5 * 0.977 ** 10)
    # "1 day lost per 2 days off": S 0.5 ≈ 2.87 exposure days, minus 5 → 0
    n = max(0.0, math.log(0.5) / math.log(0.3 ** 0.2) - 5)
    assert pj["low"] == approx(1 - (0.3 ** 0.2) ** n)
    pj2 = HT.project(0.9, D0, D0 + dt.timedelta(days=4))
    n2 = math.log(0.1) / math.log(0.3 ** 0.2) - 2
    assert pj2["low"] == approx(1 - (0.3 ** 0.2) ** n2)
    planned = {D0 + dt.timedelta(days=i): 1.0 for i in range(1, 6)}
    assert HT.project(0.0, D0, D0 + dt.timedelta(days=5), planned)["center"] == approx(0.70, abs=0.001)


def test_levels_and_current():
    assert HT.level(0.8)["id"] == "acclimatised" and HT.level(0.5)["id"] == "partial" and HT.level(0.1)["id"] == "none"
    acts = [{"date": (D0 + dt.timedelta(days=i)).isoformat(), "hot_min": 70} for i in range(5)]
    c = HT.current(acts, D0 + dt.timedelta(days=4), history_days=30)
    assert c["s"] == approx(0.70, abs=0.001) and c["days_14"] == 5 and c["since_last_d"] == 0


def test_hr_cost_against_the_cool_line():
    segs = [{"date": D0 + dt.timedelta(days=i), "p": 180 + 5 * (i % 4), "hr": 130 + 0.5 * (5 * (i % 4)), "hadley": 110}
            for i in range(10)]
    segs.append({"date": D0 + dt.timedelta(days=12), "p": 190, "hr": 145, "hadley": 160})
    r = HT.hr_cost(segs)
    # cool line HR = 40 + 0.5·P → 135 at 190 W; ΔHR 10 over (160 − 120)/10 = 4 → 2.5 bpm per 10 units
    assert r["rows"][0]["d_hr"] == approx(10.0, abs=1e-6) and r["rows"][0]["hrc"] == approx(2.5, abs=1e-6)


def test_activity_exposure_weights_moving_minutes_per_hour():
    import numpy as np
    from backend.engine import route_weather as RW

    class Tr:
        start = "2026-07-01T10:00:00"
        t = np.arange(0, 5401, 60.0)
        cols = {"cm": np.arange(0, 5401, 60.0)}
    days = [{"hourly": {"time": ["2026-07-01T10:00", "2026-07-01T11:00", "2026-07-01T12:00"],
                        "temperature_2m": [32.0, 25.0, 20.0], "relative_humidity_2m": [65.0, 75.0, 60.0]}}]
    e = RW.activity_exposure(Tr, days)
    w11 = HT.minute_weight(HT.hadley_sum(25.0, 75.0))
    # 10:00 row covers 09:30–10:30 → 30 min (Hadley 166 → 1); 11:00 row 10:30–11:30 → 60 min × w11
    assert e["moving_min"] == approx(90.0) and e["hot_min"] == approx(30.0 + 60.0 * w11, abs=0.1)
    assert e["date"] == "2026-07-01"


def test_planner_heat_acclimation_resolves_modes():
    from backend.engine.racepower import planner as PL
    assert PL.heat_acclimation({}) is None
    h = PL.heat_acclimation({"heat_acclimatisation": {"mode": "partial"}})
    assert h["s"] == 0.5 and h["a"] == HT.A_RECOVER
    auto = PL.heat_acclimation({"heat_acclimatisation": {"mode": "auto"},
                                "heat_status": {"s_race": {"center": 0.72, "low": 0.6, "high": 0.75}, "s_from": 0.3}})
    assert auto["s"] == 0.72 and auto["s_from"] == 0.3 and auto["scenarios"]["low"] == (0.35, 0.6)
    c = PL.heat_acclimation({"heat_acclimatisation": {"mode": "custom", "s": 1.4}})
    assert c["s"] == 1.0
