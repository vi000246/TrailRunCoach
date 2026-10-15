"""SP-530: the demo's planned interval run (「恢復週 fartlek 4×1 分」) is matched to its 課表 session.

The small build (tests, e2e) quartered every step of the linked runs (≥ 30 s), so the fartlek's
4×1′ became 4×30″: 98 s at ≥ 95 % CP against the 144 s the generator's week-wide match asks for
(quality_gate.hard_need: 60 % of 4×1′), not matched, classified an easy run. Now the small
interval run keeps its warm-up, reps and rests as planned and only the cool-down is quartered
(the user's answer to Q1, 2026-10-15). Synthetic only: tmp_path, never ~/.wko5coach."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from backend.demo import linked as L
from backend.engine import quality_gate as QG
from backend.engine import workout_steps as WS

CP = 260.0


def _fartlek_steps() -> dict:
    s = QG.session(QG.RECOVERY, {"cp": CP, "lthr": 170, "aet": 150}, hours=6.0)
    return WS.derive({**s, "kind": "quality"})


def _hard_s(segs) -> int:
    return sum(x.dur_s for x in segs if x.frac >= 0.95)


def test_small_interval_run_keeps_warmup_reps_and_rests():
    full = L.segments(_fartlek_steps(), CP)
    small = L.segments(_fartlek_steps(), CP, small=True, keep_main=True)
    assert [x.dur_s for x in full] == [900, 60, 120, 60, 120, 60, 120, 60, 120, 600]
    # only the cool-down is quartered
    assert [x.dur_s for x in small] == [900, 60, 120, 60, 120, 60, 120, 60, 120, 150]
    assert [x.frac for x in small] == [x.frac for x in full]
    need = QG.hard_need("恢復週 fartlek 4×1 分", 600)
    assert need == pytest.approx(144)
    assert _hard_s(small) == 240 >= need


def test_small_other_runs_still_quartered():
    # the trail run (and keep_main off) keeps the old short form
    small = L.segments(_fartlek_steps(), CP, small=True)
    assert [x.dur_s for x in small] == [225, 30, 30, 30, 30, 30, 30, 30, 30, 150]


def test_small_demo_build_matches_the_interval_run(tmp_path):
    """The e2e build (small, anchor today): the linked interval run is matched to the 課表
    session it was run from and is not classified an easy run."""
    from backend.demo import build as B
    base = B.build(tmp_path, anchor=dt.date.today(), weeks=8, small=True, warm=False, switch=False)
    m = json.loads((base / "demo_manifest.json").read_text("utf-8"))
    iv = next(a for a in m["activities"] if a.get("kind") == "interval_plan")
    assert iv.get("linked"), iv
    assert iv["linked"]["title"] == iv["session"]["title"], iv
