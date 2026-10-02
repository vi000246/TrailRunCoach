"""The session classifier's stimulus (backend/engine/session_stimulus.py) on synthetic arrays."""
import numpy as np
import pytest

from backend.engine import session_stimulus as SS

CP = 200.0


def _blocks(*parts):
    """[(seconds, power, hr, grade), ...] -> t, power, hr, grade, speed (all 1 Hz)."""
    p, h, g = [], [], []
    for n, pw, hr, gr in parts:
        p += [pw] * n
        h += [hr] * n
        g += [gr] * n
    t = np.arange(len(p), dtype=float)
    return t, np.array(p, dtype=float), np.array(h, dtype=float), np.array(g, dtype=float), np.full(len(t), 3.0)


def _reps(n, work_s, pct, rest_s=120, hr=150.0):
    parts = [(900, 0.7 * CP, 130.0, 0.0)]
    for k in range(n):
        parts.append((work_s, pct * CP, hr, 0.0))
        if k < n - 1:
            parts.append((rest_s, 0.5 * CP, 130.0, 0.0))
    parts.append((300, 0.7 * CP, 130.0, 0.0))
    return _blocks(*parts)


def _stim(t, p, h, g, v, cat="road", lthr=160.0, cad=None):
    return SS.measure(t, h, p, v, cad, g, CP, lthr, cat)


def test_five_two_minute_reps_at_110_percent_are_zone5():
    t, p, h, g, v = _reps(5, 120, 1.10)
    st = _stim(t, p, h, g, v)
    # 90 + 4 × 60 s after the on-kinetics cut (Buchheit §3.1.1.2)
    assert st["t_vo2_power_s"] == pytest.approx(270, abs=6)
    assert len(st["vo2_bouts"]) == 5
    assert SS.verdict(st, "road")["stimulus"] == "z5"


def test_four_two_minute_reps_are_short_of_four_minutes():
    t, p, h, g, v = _reps(4, 120, 1.10)
    st = _stim(t, p, h, g, v)
    assert st["t_vo2_power_s"] == pytest.approx(210, abs=6)
    assert SS.verdict(st, "road")["stimulus"] is None          # 2-min reps are < 2.5 min of Zone 3 too


def test_a_long_climb_at_98_percent_cp_is_zone3_not_zone5():
    t, p, h, g, v = _blocks((600, 140.0, 130.0, 0.0), (25 * 60, 0.98 * CP, 165.0, 0.05), (600, 140.0, 130.0, 0.0))
    st = _stim(t, p, h, g, v, cat="trail")
    assert st["t_vo2_power_s"] == 0 and st["vo2_bouts"] == []
    assert st["z3_s"] == pytest.approx(25 * 60, abs=40)
    v_ = SS.verdict(st, "trail", hrpeak=185.0)
    assert v_["stimulus"] == "z3"


def test_the_lower_vo2_band_needs_five_minutes():
    t, p, h, g, v = _blocks((600, 140.0, 130.0, 0.0), (360, 1.045 * CP, 160.0, 0.0), (600, 140.0, 130.0, 0.0))
    assert _stim(t, p, h, g, v)["t_vo2_power_s"] == pytest.approx(360 - 180, abs=6)
    t, p, h, g, v = _blocks((600, 140.0, 130.0, 0.0), (240, 1.045 * CP, 160.0, 0.0), (600, 140.0, 130.0, 0.0))
    assert _stim(t, p, h, g, v)["t_vo2_power_s"] == 0


def test_trail_power_on_steep_grades_is_not_trusted_hr_is():
    # 12' at 120 % CP on a 15 % grade: Stryd is not validated above 8 % (van Rassel 2026)
    t, p, h, g, v = _blocks((600, 140.0, 130.0, 0.0), (720, 1.20 * CP, 182.0, 0.15), (600, 140.0, 130.0, 0.0))
    st = _stim(t, p, h, g, v, cat="trail")
    assert st["t_vo2_power_s"] == 0
    r = SS.verdict(st, "trail", hrpeak=188.0)                  # 0.93 × 188 = 174.8
    assert r["t_vo2_hr_s"] == pytest.approx(720, abs=40)
    assert r["t_vo2_eq_s"] == pytest.approx(720 / SS.HR_FACTOR, abs=30) and r["stimulus"] == "z5"
    # a lower HRpeak setting moves the threshold; 182 < 0.93 × 200
    assert SS.verdict(st, "trail", hrpeak=200.0)["stimulus"] != "z5"


def test_hr_path_skips_downhill_and_stops():
    # HR 182 on a −10 % descent right after a climb (inertia) and while stopped: not counted
    t, p, h, g, v = _blocks((600, 140.0, 130.0, 0.0), (600, 0.0, 182.0, -0.10), (600, 140.0, 130.0, 0.0))
    st = _stim(t, None, h, g, v, cat="trail")
    assert SS.hr_path_s(st, 175.0) == 0
    v2 = v.copy()
    v2[600:1200] = 0.0
    st2 = _stim(t, None, h, np.zeros(len(t)), v2, cat="road")
    assert SS.hr_path_s(st2, 175.0) == 0


def test_hikes_never_get_zone5():
    t, p, h, g, v = _blocks((600, 0.0, 130.0, 0.2), (2400, 0.0, 185.0, 0.2), (600, 0.0, 130.0, 0.2))
    st = _stim(t, None, h, g, v, cat="hike")
    r = SS.verdict(st, "hike", hrpeak=188.0)
    assert r["stimulus"] == "z3" and r["t_vo2_eq_s"] == 0


def test_power_evidence_beats_the_easy_average_hr_rule():
    t, p, h, g, v = _reps(5, 120, 1.10)
    st = _stim(t, p, h, g, v)
    assert SS.verdict(st, "road", easy_hr=True)["stimulus"] == "z5"
    # HR-only evidence does not: the easy average stands
    t, p, h, g, v = _blocks((3000, 0.0, 130.0, 0.0), (600, 0.0, 182.0, 0.0))
    st = _stim(t, None, h, g, v)
    assert SS.verdict(st, "road", hrpeak=188.0)["stimulus"] == "z5"
    assert SS.verdict(st, "road", hrpeak=188.0, easy_hr=True)["stimulus"] is None


def test_cadence_lock_needs_hr_to_follow_cadence():
    n = 600
    cad = 172.0 + 4.0 * np.sin(np.arange(n) / 15.0)            # cadence moving ±4 spm
    locked = SS.cadence_lock(cad.copy(), cad)
    assert locked.mean() > 0.8
    # HR close to the cadence but flat (a coincidence, not a lock): kept
    flat = SS.cadence_lock(np.full(n, 172.0), cad)
    assert not flat.any()
    # a locked stretch is not counted by the HR path
    t = np.arange(n, dtype=float)
    st = SS.measure(t, cad.copy(), None, np.full(n, 3.0), cad, None, CP, 160.0, "road")
    assert SS.hr_path_s(st, 170.0) == 0


def test_hr_peak_drops_the_two_highest():
    assert SS.hr_peak([216, 211, 188, 185, 179]) == 188
    assert SS.hr_peak([180, 175]) == 175
    assert SS.hr_peak([]) is None
