"""SP-687: the 圖表分析 強度 page has no 每週馬拉松配速時間 chart any more (the 專項期 page keeps it), and both
區間時數 cards take the full row, one above the other (static/charts.html: every periodzones card is `wide`)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _view(name):
    return json.loads((ROOT / "views" / f"{name}.json").read_text("utf-8"))


def test_intensity_page_has_no_marathon_pace_chart_but_the_specific_page_does():
    intensity = next(d for d in _view("training")["dashboards"] if d["id"] == "intensity")
    assert [c["id"] for c in intensity["charts"]] == ["zone-hours", "weekly-zone-hours"]
    assert not any("馬拉松配速" in c.get("title", "") for c in intensity["charts"])
    build = next(d for d in _view("periodization")["dashboards"] if d["id"] == "build")
    mp = next(c for c in build["charts"] if c["id"] == "mp-weekly-specific")
    assert mp["title"] == "每週馬拉松配速時間" and mp["sports"] == ["road"]
    en = json.loads((ROOT / "views" / "i18n" / "en.json").read_text("utf-8"))
    assert "mp-weekly" not in json.dumps(en["training"]) and "mp-weekly-specific" in en["periodization"]["charts"]


def test_both_zone_hours_cards_are_full_width():
    src = (ROOT / "backend" / "static" / "charts.html").read_text("utf-8")
    line = next(x for x in src.splitlines() if 'card.classList.add("wide")' in x and "z5gate" in x)
    assert re.search(r'c\.kind === "periodzones"\)', line) and "c.view" not in line
