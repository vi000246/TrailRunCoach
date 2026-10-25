"""SP-737: under 儲備心率 (and 最大心率) a step written in % LTHR — a template's, or a Friel zone — resolves to the
same place in the same zone of the 課表心率區間 (hr_profile.frac_hr, as the template's own `hrp` already did since
SP-493), not × LTHR: Z2's middle in % LTHR is Z2's middle there. The LTHR model is unchanged (× LTHR).
Synthetic thresholds only."""
from __future__ import annotations

import pytest

from backend.engine import hr_profile as HP
from backend.engine import workout_steps as WS
from backend.engine import workout_templates as WT
from backend.tests.test_auto_zones_sp493 import HRZ, LTHR


def _ctx(model: str) -> WS.Ctx:
    return WS.Ctx.of({"cp": 260.0, "lthr": LTHR, "aet": 140.0, "hr_model": HRZ[model]}, "hr")


def _bpm(c, lo, hi):
    r = WS.resolve({"kind": "work", "target": WT.hr(lo, hi) if hasattr(WT, "hr") else
                    {"type": "hr", "mode": "pct", "lo": lo, "hi": hi}}, c)
    return r.lo, r.hi


@pytest.mark.parametrize("model", ["hrr", "hrmax"])
def test_a_percent_lthr_step_lands_in_the_same_place_of_the_same_zone(model):
    c = _ctx(model)
    lo, hi = _bpm(c, 0.85, 0.89)
    assert (lo, hi) == (round(HP.frac_hr(0.85, HRZ[model], LTHR)), round(HP.frac_hr(0.89, HRZ[model], LTHR)))
    # COROS LTHR model edges: Z2 = 80–90 % LTHR, so 85 % is Z2's middle → the middle of this model's Z2
    edges = HP.DEFAULT_RATIOS["lthr"]
    z = WS.hr_model_zones(c)
    z2 = next(x for x in z if x[0] == HP.ZONE_IDS[1])
    mid = HP.frac_hr((edges[0] + edges[1]) / 2, HRZ[model], LTHR)
    assert mid == pytest.approx((z2[2] + z2[3]) / 2, abs=1.0)
    assert lo != round(0.85 * LTHR) or hi != round(0.89 * LTHR)        # not × LTHR any more


def test_the_lthr_model_is_still_times_lthr():
    c = _ctx("lthr")
    assert _bpm(c, 0.85, 0.89) == (round(0.85 * LTHR), round(0.89 * LTHR))


def test_a_step_saved_with_a_friel_zone_id_keeps_resolving_as_lthr_percent():
    # SP-30 (test_hr_profile): a legacy Friel id stays × LTHR; only a written % LTHR moves
    c = _ctx("hrr")
    r = WS.resolve({"kind": "work", "target": {"type": "hr", "mode": "zone", "zone": "3"}}, c)
    lo, hi = WS.zone_span("hr", "3", None)
    assert (r.lo, r.hi) == (round(lo * LTHR), round(hi * LTHR))
