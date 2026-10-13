"""總覽「本週」標題旁 (SP-416): the week's 完成度 % in traffic-light colours (st good / watch / bad, red
past 50 % off, a tooltip with 「紅 > 50%」) is gone. Like the 課表 page's week row (SP-398) and 課表統計's
first screen (SP-400) it only adds up: 「完成 TSS n」 in the heading's neutral meta colour, the planned
TSS in the hover. Static checks of overview.html and the catalogs."""
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "overview.html"


def _wk_cp() -> str:
    m = re.search(r'\$\("wk-cp"\)\.innerHTML = (.*?);\n', PAGE.read_text(encoding="utf-8"))
    assert m, "the 本週 heading's wk-cp line not found"
    return m.group(1)


def test_the_week_heading_shows_done_tss_without_a_colour_or_a_percent():
    js = _wk_cp()
    for gone in ("level", "good", "watch", "bad", "class=\"st", "%", "完成度", "紅"):
        assert gone not in js, gone
    assert 'tO("week.done_tss")' in js and "done_tss" in js
    assert 'tO("week.plan_tss_tip"' in js and "planned_tss" in js
    assert "class=\"meta num\"" in js


def test_no_traffic_light_tooltip_left_on_the_page():
    html = PAGE.read_text(encoding="utf-8")
    assert "紅 > 50%" not in html and "綠 ±20%" not in html


def test_catalog_has_the_words_in_both_languages():
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "overview.json").read_text("utf-8"))
        assert cat.get("week.done_tss") and "{p}" in cat.get("week.plan_tss_tip", ""), loc
    sched = json.loads((STATIC / "i18n" / "zh-TW" / "schedule.json").read_text("utf-8"))
    over = json.loads((STATIC / "i18n" / "zh-TW" / "overview.json").read_text("utf-8"))
    assert over["week.done_tss"] == sched["wk.done_tss"]            # the same words as the 課表 page's week row
    assert over["week.plan_tss_tip"] == sched["wk.plan_tss_tip"]
