"""SP-945 (SP-808 option A, the user 2026-10-21): an RPE target's ≈ % CP (workout_steps.RPE_FRAC) is
rpe_load's five levels (DEFAULT_IF at CR-10 2 / 4 / 5 / 7 / 10) interpolated — one table, not two —
and the editor's chart copy (static/workout_editor.js RPE_F) says the same. No data."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.engine import rpe_load as RL
from backend.engine import workout_steps as WS

ROOT = Path(__file__).resolve().parents[1]


def test_the_five_anchors_are_rpe_loads_levels():
    for k in RL.IDS:
        assert WS.RPE_FRAC[RL.CR10[k]] == RL.DEFAULT_IF[k], k


def test_the_table_is_the_chosen_interpolation():
    assert WS.RPE_FRAC == {1: 0.70, 2: 0.74, 3: 0.78, 4: 0.82, 5: 0.88, 6: 0.92, 7: 0.95, 8: 1.00,
                           9: 1.05, 10: 1.10}
    assert WS.RPE_FRAC[3] == WS.EASY_F               # RPE 3 lands on the app's easy-run anchor
    vals = [WS.RPE_FRAC[r] for r in range(WS.RPE_MIN, WS.RPE_MAX + 1)]
    assert all(b > a for a, b in zip(vals, vals[1:]))   # a higher RPE always counts more


def test_the_table_is_derived_from_default_if(monkeypatch):
    # not a second hand-written table: move an anchor and the RPE targets move with it
    monkeypatch.setitem(RL.DEFAULT_IF, "easy", 0.70)
    t = WS._rpe_frac_table()
    assert t[2] == 0.70 and t[3] == 0.76 and t[1] == 0.64 and t[4] == 0.82


def test_the_editors_copy_matches():
    src = (ROOT / "static" / "workout_editor.js").read_text(encoding="utf-8")
    m = re.search(r"const RPE_F = \[([^\]]*)\]", src)
    assert m, "RPE_F not found in workout_editor.js"
    js = [float(x) for x in m.group(1).split(",")]
    assert js[0] == 0 and len(js) == 11
    assert {r: js[r] for r in range(1, 11)} == pytest.approx(WS.RPE_FRAC)
