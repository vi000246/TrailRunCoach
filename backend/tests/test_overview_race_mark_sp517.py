"""
SP-517: the 總覽 「本週」 list writes the week's races / 百岳 trips as the 課表's day mark (SP-504, R1): the
overview's wk.race_* / wk.trip_* strings say the same as the 課表's race.* / race.trip_* ones, in zh-TW and en, and
overview.html reads the 課表 calendar's `races` (A / B / C) for it. The browser side:
e2e/test_overview_race_mark_sp517_e2e.py.
"""
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
KINDS = ("race", "trip")
KEYS = ("mark", "tip_a", "tip_b", "tip_c", "act", "act_tip")


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text(encoding="utf-8"))


def test_strings_in_both_languages_with_the_same_placeholders():
    zh, en = _cat("zh-TW", "overview"), _cat("en", "overview")
    for k in [f"wk.{kind}_{key}" for kind in KINDS for key in KEYS] + ["wk.mark_load"]:
        assert k in zh and k in en, k
        assert set(re.findall(r"\{(\w+)\}", zh[k])) == set(re.findall(r"\{(\w+)\}", en[k])), k
    for key in KEYS:
        z, e = zh[f"wk.trip_{key}"], en[f"wk.trip_{key}"]
        assert "行程" in z and "比賽" not in z and "賽「" not in z and " 賽：" not in z, z
        assert "trip" in e.lower() and "race" not in e.lower(), e


def test_written_as_on_the_schedule():
    """Same mark, same activity label, same B / C hover and load line as the 課表's (schedule.json)."""
    for loc in ("zh-TW", "en"):
        ov, sc = _cat(loc, "overview"), _cat(loc, "schedule")
        assert ov["wk.race_mark"] == sc["race.mark"] and ov["wk.trip_mark"] == sc["race.trip_mark"]
        assert ov["wk.race_act"] == sc["race.act"] and ov["wk.trip_act"] == sc["race.trip_act"]
        assert ov["wk.race_tip_b"] == sc["race.mark_tip_b"] and ov["wk.race_tip_c"] == sc["race.mark_tip_c"]
        assert ov["wk.trip_tip_b"] == sc["race.trip_tip_b"] and ov["wk.trip_tip_c"] == sc["race.trip_tip_c"]
        assert ov["wk.mark_load"] == sc["race.mark_load"]
    zh = _cat("zh-TW", "overview")
    assert zh["wk.race_mark"].format(p="A", name="X") == "A 賽：X"
    assert zh["wk.trip_mark"].format(p="C", name="大小霸 3 日") == "C 行程：大小霸 3 日"


def test_overview_reads_the_calendar_races_for_every_priority():
    src = (STATIC / "overview.html").read_text(encoding="utf-8")
    assert re.search(r'raceT = \(r, k, p\) => tO\(`wk\.\$\{r && r\.kind === "baiyue" \? "trip" : "race"\}_\$\{k\}`, p\)', src)
    # every race of the day (no priority filter: the A race too), from the 課表 calendar answer (SP)
    assert re.search(r"racesOn = \(iso\) => \(\(SP && SP\.races\) \|\| \[\]\)\.filter\(\(r\) => r\.start <= iso && iso <= ", src)
    # a race day is no rest day: the marks are part of the day's items
    assert "const items = rs.map(raceMarkHtml).concat(" in src
