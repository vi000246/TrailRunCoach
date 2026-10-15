"""SP-528: estimated constants whose numbers were right but whose sources were wrong — only the
words change (docs/research/coach-consensus-sp518/). The numbers are pinned here; the plan is unchanged."""
from __future__ import annotations

import inspect
from pathlib import Path

from backend.engine import b2b as B2B
from backend.engine import calibrate as CAL
from backend.engine import cold_start as CS
from backend.engine import load_guard as LG
from backend.engine import plan_prefs as PP
from backend.engine import quality_gate as QG
from backend.engine import status as ST
from backend.engine import threshold_calib as TCAL
from backend.engine import workout_review as WR

DOCS = Path(__file__).resolve().parents[2] / "docs" / "research"


def test_numbers_unchanged():
    assert (QG.LOW_SHARE_MIN, ST.LOW_SHARE_GOOD, QG.QUALITY_SHARE_MAX, QG.Z3_SHARE_MAX) == (0.75, 0.75, 0.20, 0.10)
    assert (LG.STEP_HOLD, LG.STEP_BLOCK) == (0.10, 0.20)
    assert B2B.SINGLE_DAY2 == (90, 150) and WR.AET_MARGIN == 3.0 and CS.LONG_SHARE == 0.40
    assert PP._gap(1, 3) == 2                                         # the 48 h rule reads the same gap


def test_low_share_floor_cites_campos_and_the_coaches():
    for txt in (ST.SRC_LOW_SHARE, inspect.getsource(QG).split("LOW_SHARE_MIN = 0.75")[1][:400]):
        assert "Campos 2022" in txt and "80/20" in txt and ("UA" in txt or "Uphill Athlete" in txt)
    assert "Seiler 2010's 80/20" not in inspect.getsource(QG)
    src = inspect.getsource(ST.Status.i_intensity) if hasattr(ST.Status, "i_intensity") else inspect.getsource(ST)
    assert "SRC_LOW_SHARE" in src


def test_quality_budget_corrects_koop():
    note = inspect.getsource(QG).split("QUALITY_SHARE_MAX = 0.20")[1][:700]
    assert "80/20 Endurance" in note and "Campos 2022" in note and "10 % of the HOURS" in note and "20 %" in note


def test_zone3_cap_is_ua_and_tftna():
    note = inspect.getsource(QG).split("Z3_SHARE_MAX = 0.10")[1][:500]
    assert "UA" in note and "TftNA" in note and "side note" in note


def test_hard_day_gap_sources():
    assert all(w in PP.SRC_GAP for w in ("UA", "Friel", "Bompa", "Stanley 2013", "台灣教練"))
    assert "Seiler" not in PP.SRC_GAP


def test_step_hold_is_the_coaches_mode():
    assert "教練眾數" in ST.SRC_VOLUME and "推估（保守）" not in ST.SRC_VOLUME
    k = inspect.getsource(LG).index("STEP_HOLD, STEP_BLOCK = 0.10, 0.20")
    assert "the coaches' mode" in inspect.getsource(LG)[k - 400:k]


def test_single_day2_and_aet_margin_sources():
    assert "vert.run" in inspect.getsource(B2B).split("SINGLE_DAY2 = (90, 150)")[1][:300]
    assert "1.5–2.5 小時輕鬆（vert.run）" in inspect.getsource(B2B)
    item = CAL._registry()[TCAL.MARGIN]
    assert "Lamberts 2009" in item.default_src and "不是教練允許的餘裕" in item.default_src
    assert "不是教練允許的餘裕" in inspect.getsource(WR)


def test_long_share_marked_estimate():
    k = inspect.getsource(CS).index("LONG_SHARE = 0.40")
    assert "推估, no coach source" in inspect.getsource(CS)[k:k + 200]


def test_research_docs_corrected():
    inv = (DOCS / "estimated-constants-inventory.md").read_text("utf-8")
    assert "12 to 16 weeks" in inv and "vert.run 最後 8–10 週 [131]" not in inv
    assert "剩 40–50 %" in (DOCS / "bompa-periodization-strength.md").read_text("utf-8")
    assert "單位是**堂數**" in (DOCS / "periodization-phase-metrics.md").read_text("utf-8")
