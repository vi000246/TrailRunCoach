"""SP-669: the planner's weekly CTL goal follows Palladino's 2–5 % for runners (SP-664 Q3 = B).

* load_guard.GOAL: 專項期 7 % → 5 %, 基礎期 5 % → 4 %; the point floors (2.5 / 2) stay; the guard
  lines (watch / block) do not move.
* overview.SRC_RAMP no longer files 7 % under Palladino.
* 專項期 stays 8 weeks (SP-664 Q2 = A) and says where 8 comes from: planning.py and the 周期 page.
* Manual texts that quoted +5 % / +7 % now say +4 % / +5 %.
"""
import json
import re
from pathlib import Path

import pytest

from backend.engine import load_guard as LG
from backend.engine import overview as OV
from backend.engine import planning as P

ROOT = Path(__file__).resolve().parents[2]
SPECIFIC_SOURCES = ("Uphill Athlete", "Pfitzinger", "Canova", "Daniels", "江晏慶")


# ---- acceptance 1: the goal ----------------------------------------------------

def test_goal_shares_are_palladino_upper_end():
    assert LG.GOAL["specific"] == (0.05, 2.5)
    assert LG.GOAL["base"] == (0.04, 2.0)
    assert LG.GOAL["maintain"] == (0.0, 0.0)


@pytest.mark.parametrize("kind, ctl, goal", [
    ("specific", 100.0, 5.0), ("specific", 80.0, 4.0), ("specific", 40.0, 2.5),   # 5 % × 40 = 2 < floor 2.5
    ("base", 100.0, 4.0), ("base", 60.0, 2.4), ("base", 40.0, 2.0),               # 4 % × 40 = 1.6 < floor 2
])
def test_ramp_goal_uses_the_new_shares_and_keeps_the_floors(kind, ctl, goal):
    assert LG.ramp_goal(kind, ctl) == pytest.approx(goal)


def test_guard_lines_unchanged():
    assert (LG.WATCH_PCT, LG.WATCH_MIN) == (0.10, 3.0)
    assert (LG.BLOCK_PCT, LG.BLOCK_MIN, LG.BLOCK_MAX) == (0.15, 5.0, 10.0)
    assert LG.watch_line(60.0) == pytest.approx(6.0) and LG.block_line(60.0) == pytest.approx(9.0)


# ---- acceptance 2: sources -------------------------------------------------------

def test_src_ramp_quotes_the_new_goal_and_does_not_file_7_under_palladino():
    s = OV.SRC_RAMP
    assert "7%" not in s and "7 %" not in s
    assert "長期負荷的 4%" in s and "長期負荷的 5%" in s
    assert "Palladino" in s and "2～5%" in s


def test_specific_weeks_stays_8_with_its_sources_in_planning():
    assert P.SPECIFIC_WEEKS == 8
    src = (ROOT / "backend/engine/planning.py").read_text(encoding="utf-8")
    line = next(x for x in src.splitlines() if x.startswith("SPECIFIC_WEEKS ="))
    block = src[src.index(line) - 900: src.index(line) + len(line)]
    for name in SPECIFIC_SOURCES:
        assert name in block, name
    assert "8＋2" in block or "8 + 2" in block


@pytest.mark.parametrize("lang", ["zh-TW", "en"])
def test_period_page_names_the_specific_weeks_sources(lang):
    cat = json.loads((ROOT / f"backend/static/i18n/{lang}/plan.json").read_text(encoding="utf-8"))
    s = cat["tl.specific_src"]
    assert "{n}" in s
    for name in SPECIFIC_SOURCES:
        if lang == "en" and name == "江晏慶":
            assert "Chiang" in s or "江晏慶" in s
            continue
        assert name in s, name
    html = (ROOT / "backend/static/plan.html").read_text(encoding="utf-8")
    assert 't("plan.tl.specific_src"' in html


@pytest.mark.parametrize("path", [
    "backend/manual/zh-TW/schedule.md", "backend/manual/en/schedule.md",
    "backend/manual/zh-TW/glossary.md", "backend/manual/en/glossary.md",
])
def test_manual_quotes_the_new_weekly_goal(path):
    txt = (ROOT / path).read_text(encoding="utf-8")
    assert not re.search(r"\+7 %", txt), path
    assert "+4 %" in txt and "+5 %" in txt, path
