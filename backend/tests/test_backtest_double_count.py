"""SP-241: the x*(T) / δ double-count check in the trail HR back-test summary
(docs/research/long-race-durability-shape.md §2.4). Report only: the three
set-ups are the existing err_th_race (x* + δ), err_th_race_nodur (x* only)
and err_th_race_median (δ only, fixed x); a verdict needs ≥ 3 races ≥ 6 h."""
import pytest

from backend.engine.racepower import backtest as BT
from backend.engine.racepower import trailhr as TH


def _row(date, hours, e_both, e_xstar, e_delta, activity_type="race"):
    return {"category": "trail", "date": date, "label": f"run {date}", "activity_type": activity_type,
            "effort_tag": "max", "th": {"moving_s": hours * 3600.0},
            "err_th_race": e_both, "err_th_race_nodur": e_xstar, "err_th_race_median": e_delta}


def test_no_long_races_means_no_verdict():
    s = BT.summarise_trail_hr([_row("2026-01-01", 3.9, 0.04, 0.03, 0.06), _row("2026-02-01", 5.9, 0.1, 0.0, 0.2)])
    dc = s["double_count_check"]
    assert dc["n"] == 0 and dc["ready"] is False
    assert dc["min_h"] == 6.0 and dc["min_n"] == 3
    assert dc["xstar_and_delta"]["median_abs"] is None
    assert BT.summarise_trail_hr([])["double_count_check"]["n"] == 0


def test_three_long_races_give_the_three_setups():
    rows = [_row("2026-01-01", 6.0, 0.20, 0.02, 0.10),          # exactly 6 h counts
            _row("2026-02-01", 9.0, 0.30, -0.04, 0.12),
            _row("2026-03-01", 14.0, 0.40, 0.06, 0.20),
            _row("2026-04-01", 12.0, 0.90, 0.90, 0.90, activity_type="training")]   # not a race
    dc = BT.summarise_trail_hr(rows)["double_count_check"]
    assert dc["n"] == 3 and dc["ready"] is True
    assert dc["xstar_and_delta"]["median_abs"] == pytest.approx(0.30)
    assert dc["xstar_only"]["median_abs"] == pytest.approx(0.04)
    assert dc["delta_only"]["median_abs"] == pytest.approx(0.12)


def test_two_long_races_are_not_ready():
    rows = [_row("2026-01-01", 7.0, 0.2, 0.0, 0.1), _row("2026-02-01", 8.0, 0.2, 0.0, 0.1)]
    dc = BT.summarise_trail_hr(rows)["double_count_check"]
    assert dc["n"] == 2 and dc["ready"] is False


def test_model_unchanged_by_sp241():
    # the finding was "no structural double count, too little data": δ and x*(T) both stay
    assert TH.TRAILHR["delta_prior"] == 0.05 and TH.TRAILHR["v_floor"] == 0.80
    assert TH.XSTAR["anchors"] == ((0.5, 1.00), (3.0, 0.90), (12.0, 0.85))
    m = {"kind": "proportional", "c": 10.0, "delta": 0.05}
    both, _ = TH.predict_race(m, 80.0, None)
    xonly, _ = TH.predict_race(m, 80.0, None, delta=0.0)
    assert both > xonly            # δ still applied on top of x*(T)
