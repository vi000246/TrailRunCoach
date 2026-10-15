"""SP-527: the weekly-hours cap's +0.5 h floor is at most +20 % (under 2.5 h a week +0.5 h was more,
and the next week the app's own 20 % line blocked the intervals); B2B → 3 easy days after (was 4).
docs/research/coach-consensus-sp518/A-volume-load.md §2, C-long-specific-b2b.md."""
from __future__ import annotations

import inspect

import pytest

from backend.engine import b2b as B2B
from backend.engine import load_guard as LG
from backend.engine import overview as O
from backend.engine import projection as P


@pytest.mark.parametrize("ref,cap", [(1.0, 1.2), (1.5, 1.8), (2.0, 2.4), (2.4, 2.88), (2.5, 3.0), (3.0, 3.5),
                                     (4.0, 4.5), (5.0, 5.5), (8.0, 8.8), (0.0, 0.5)])
def test_cap(ref, cap):
    assert O.volume_cap(ref) == pytest.approx(cap)


def test_below_2_5_h_never_more_than_the_block_line():
    for r in (0.5, 1.0, 1.7, 2.0, 2.49):
        assert O.volume_cap(r) / r - 1.0 <= LG.STEP_BLOCK + 1e-9
    for r in (2.5, 3.0, 6.0, 12.0):                       # unchanged from max(+10 %, +0.5 h)
        assert O.volume_cap(r) == pytest.approx(max(1.10 * r, r + 0.5))


def test_projection_and_week_plan_use_it():
    h, mode, why = P.week_hours("base", [2.0] * 4, [False] * 4, ctl0=50.0, r=50.0, cc=42.0, days_to_a=None)
    assert h == pytest.approx(2.4) and "最多 +20%" in why[0]
    # the cold-start ramp (資料等級 1) steps by the same cap
    h, _, _ = P.week_hours("base", [1.0] * 4, [False] * 4, ctl0=5.0, r=50.0, cc=42.0, days_to_a=None, floor=1.5)
    assert h == pytest.approx(1.8)
    wp = inspect.getsource(O.week_plan)
    assert "cap = volume_cap(cap_ref)" in wp and "max(1.10 * cap_ref, cap_ref + 0.5)" not in wp
    assert "O.volume_cap(ref)" in inspect.getsource(P.week_hours)
    src = inspect.getsource(O)
    k = src.index("VOLUME_STEP, VOLUME_STEP_MIN_H, VOLUME_STEP_MAX = 0.10, 0.5, 0.20")
    assert "SP-527" in src[k - 900:k] and "STEP_BLOCK" in src[k - 900:k]


def test_b2b_three_easy_days_after():
    assert B2B.POST_EASY_DAYS == 3
    assert "中位數 3 天" in B2B.SRC_POST and "Gaudette" in B2B.SRC_POST


def test_english_cap_lines():
    from backend.i18n import use_locale, _
    with use_locale("en"):
        t = _("但週量上限 = 近 4 週 {base4:.1f} h / 上週 {last:.1f} h 的 +10%（至少 +0.5 h、最多 +20%）→ {cap:.1f} h",
              base4=2.0, last=2.0, cap=2.4)
    assert "at most +20%" in t
