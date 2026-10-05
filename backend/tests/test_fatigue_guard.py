"""
SP-110 間歇疲勞保險 (quality_gate.dose_step / fatigue_check, interval-adaptation.md §4.3): a 60-s HR
drop ≥ 25 % faster than the same-spec sessions' 8-week median, two same-spec sessions in a row,
with the power missed → 未適應 (no step forward) and the status says why. Synthetic rows only.
"""
import datetime as dt

import numpy as np
import pytest

from backend.engine import quality_gate as QG

CP = 250.0
D0 = dt.date(2026, 8, 3)


def _row(k, drop, key="v1a", power_ok=True, rest_drop=None):
    """Session k (weekly) of variant `key` (z5a: 5×2′ at 106–112 % CP, rest 2′): 5 reps, each rep's
    60-s HR drop `drop`; power_ok False = the last rep 8 % down (邊界 by power)."""
    ps = [280.0] * 5 if power_ok else [280.0] * 4 + [257.0]
    return {"date": (D0 + dt.timedelta(days=7 * k)).isoformat(), "track": "z5", "variant_key": key,
            "rung_key": "z5a", "cp": CP, "bouts": [{"power": p, "hr_drop60": drop, "hr_at60": 120.0} for p in ps]}


def _base(n=5, key="v1a"):
    # 邊界 sessions (the last rep fades 8 %): the step stays at z5a, the baseline grows
    return [_row(k, 30.0, key, power_ok=False) for k in range(n)]


def test_two_fast_sessions_with_the_power_missed_are_unadapted():
    h = _base() + [_row(5, 40.0, power_ok=False), _row(6, 40.0, power_ok=False)]
    d = QG.dose_step(h, None, "z5")
    assert d["outcome"] == "unadapted" and d["step"] == 0
    f = d["fatigue"]
    assert f["drop"] == 40.0 and f["base"] == 30.0 and f["streak"] == 2 and f["n"] == 6
    assert "心率恢復變快但功率沒到，可能累積疲勞" in d["note"] and "連續 2 堂" in f["text"]
    assert h[-1]["fatigue"] and not h[-2].get("fatigue")


def test_one_fast_session_does_not_fire():
    d = QG.dose_step(_base() + [_row(5, 40.0, power_ok=False)], None, "z5")
    assert d["outcome"] == "border" and "fatigue" not in d


def test_power_met_does_not_fire():
    h = _base() + [_row(5, 40.0, power_ok=False), _row(6, 40.0, power_ok=True)]
    d = QG.dose_step(h, None, "z5")
    assert d["outcome"] == "met" and d["step"] == 1 and "fatigue" not in d


def test_under_25_percent_or_under_5_baseline_sessions_does_not_fire():
    h = _base() + [_row(5, 37.0, power_ok=False), _row(6, 37.0, power_ok=False)]      # +23 %
    assert "fatigue" not in QG.dose_step(h, None, "z5")
    h = _base(4) + [_row(4, 40.0, power_ok=False), _row(5, 40.0, power_ok=False)]     # baseline 4 / 5
    assert "fatigue" not in QG.dose_step(h, None, "z5")


def test_other_specs_are_not_mixed_in():
    # v1c = the same 5×2′ / 2′ rest but jogging down: another spec, so v1a has no baseline
    assert QG.spec_key({"variant_key": "v1a"}, QG.Z5[0]) == (120, 120, "walk")
    assert QG.spec_key({"variant_key": "v1c"}, QG.Z5[0]) == (120, 120, "jog_down")
    assert QG.spec_key({}, QG.Z5[0]) == (120, 120, None)
    h = _base(key="v1c") + [_row(5, 40.0, power_ok=False), _row(6, 40.0, power_ok=False)]
    assert "fatigue" not in QG.dose_step(h, None, "z5")
    # a baseline older than 8 weeks doesn't count
    old = [{**r, "date": (D0 - dt.timedelta(days=70)).isoformat()} for r in _base()]
    assert "fatigue" not in QG.dose_step(old + [_row(5, 40.0, power_ok=False), _row(6, 40.0, power_ok=False)],
                                         None, "z5")


def test_hrr_fast_and_session_drop60():
    rows = [{"date": f"2026-08-{d:02d}", "spec_key": (120, 120, "walk"), "drop60": 30.0} for d in range(1, 6)]
    rows += [{"date": "2026-08-07", "spec_key": (180, 180, "jog"), "drop60": 10.0},
             {"date": "2026-08-08", "spec_key": (120, 120, "walk"), "drop60": 37.5}]
    f = QG.hrr_fast(rows, 6)
    assert f["n"] == 5 and f["base"] == 30.0 and f["gain"] == pytest.approx(0.25) and f["fast"]
    assert QG.session_drop60({"bouts": [{"hr_drop60": 20}, {"hr_drop60": 30}, {"hr_drop60": None},
                                        {"hr_drop60": 90}]}, 3) == 25.0
    assert QG.session_drop60({"bouts": [{"hr_drop60": None}]}, 1) is None


def test_bouts_carry_the_60s_drop():
    t = np.arange(600, dtype=float)
    hr = np.where(t < 120, 170.0, 130.0)
    out = QG._with_hr_at60([{"power": 280.0, "duration_s": 120, "start_s": 0}], {"t": t, "hr": hr})
    assert out[0]["hr_at60"] == 130.0 and out[0]["hr_drop60"] == 40.0


def test_status_indicator_says_it():
    d5 = QG.dose_step(_base() + [_row(5, 40.0, power_ok=False), _row(6, 40.0, power_ok=False)], None, "z5")
    gate = {"state": "none", "mode": "none", "mode_label": "不設門檻", "resolved": "none", "kind": "base",
            "guard": {}, "z3": {"open": True}, "dose": {"z3": {"step": 0, "met": 0, "done": 0}, "z5": d5}}
    ind = QG.indicator(gate)
    assert ind["level"] == "watch" and "可能累積疲勞" in ind["verdict"] and "Aubry 2015" in ind["source"]
    assert ind["action"]
