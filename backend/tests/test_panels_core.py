"""Pure panel logic — backend/engine/panels/{fatigue,loadfocus,recommend}.py."""
import datetime as dt
import math

import pytest

from backend.engine.panels import fatigue as F
from backend.engine.panels import loadfocus as LF
from backend.engine.panels.recommend import recommend


# ---- fatigue ----------------------------------------------------------------

def test_pmc_is_linear_wko5_form():
    ctl, atl = F.pmc([100.0, 0.0], ctl_days=42, atl_days=7)
    assert ctl[0] == pytest.approx(100 / 42)
    assert atl[0] == pytest.approx(100 / 7)
    assert atl[1] == pytest.approx(100 / 7 * (1 - 1 / 7))


@pytest.mark.parametrize("r,key", [(160, "overreaching"), (150, "overreaching"),
                                   (149.9, "optimized"), (100, "optimized"),
                                   (80, "maintaining"), (79.9, "recovery"), (0, "recovery")])
def test_band_edges(r, key):
    assert F.band(r)["key"] == key


def test_ratio_needs_positive_ctl():
    assert F.ratio(10, 0) is None
    assert F.ratio(12, 10) == pytest.approx(120)


# ---- load focus -------------------------------------------------------------

def test_split_by_power_matches_tss_integrand():
    ftp = 300.0
    p = [200.0] * 600 + [300.0] * 600 + [400.0] * 600
    b = LF.split(p, [1.0] * len(p), ftp, LF.POWER_EDGES)
    assert b["low"] == pytest.approx((200 / 300) ** 2 * 600 / 36)
    assert b["high"] == pytest.approx(600 / 36)
    assert b["anaerobic"] == pytest.approx((400 / 300) ** 2 * 600 / 36)
    # one hour at FTP would be 100
    assert LF.split([ftp] * 3600, [1.0] * 3600, ftp, LF.POWER_EDGES)["high"] == pytest.approx(100)


def test_split_ignores_breaks_and_gaps():
    b = LF.split([0.0, float("nan"), 50.0, 300.0], [1, 1, 1, 120], 300.0, LF.POWER_EDGES)
    assert sum(b.values()) == 0.0


def test_scale_to_workout_tss():
    s = LF.scale_to({"low": 30.0, "high": 10.0, "anaerobic": 0.0}, 80.0)
    assert s["low"] == pytest.approx(60) and s["high"] == pytest.approx(20)


def test_summarize_states_against_base_targets():
    s = LF.summarize({"low": 50.0, "high": 40.0, "anaerobic": 10.0}, "base")
    by = {b["key"]: b for b in s["buckets"]}
    assert by["low"]["state"] == "short" and by["low"]["pct"] == pytest.approx(50)
    assert by["high"]["state"] == "over"
    assert by["anaerobic"]["state"] == "ok"
    assert by["low"]["short_by"] == pytest.approx(10.0)


def test_summarize_unknown_phase_uses_base():
    assert LF.summarize({"low": 1.0}, "event")["phase"] == "base"


# ---- recommend --------------------------------------------------------------

def _focus(low, high, ana, phase="base"):
    return LF.summarize({"low": low, "high": high, "anaerobic": ana}, phase)


def test_overreaching_means_rest_even_with_gaps():
    r = recommend(F.band(155), _focus(10, 80, 10), "base")
    assert r["key"] == "rest"


def test_taper_phase_wins_over_focus():
    assert recommend(F.band(110), _focus(10, 80, 10), "taper")["key"] == "taper"


def test_largest_shortfall_is_recommended():
    # base: anaerobic ok (0-10), high 5% (short), low 90% (over)
    r = recommend(F.band(110), _focus(90, 5, 5), "base")
    assert r["key"] == "high"
    assert any("高強度有氧" in x for x in r["reasons"])


def test_all_in_range_grows_volume_when_band_low():
    assert recommend(F.band(90), _focus(70, 22, 8), "base")["key"] == "grow"
    assert recommend(F.band(120), _focus(70, 22, 8), "base")["key"] == "low"
