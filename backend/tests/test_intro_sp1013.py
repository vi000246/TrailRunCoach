"""SP-1013: the 介紹頁's two try buttons (the hero's and the one under the race calculator, data-try-big) are a
card, the user's pick of ten mocks (6, 真實剖面): a tag, the play ring + title + the example's numbers, and along
the bottom the example's own elevation profile with its stops ringed — drawn by the page from the example API
(profile, stops, a 百岳 trek's days), so it follows 越野／百岳／路跑. The API adds the stops: km and a type
code, no name (numbers only, like the rest of it)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.api import intro as I
from backend.engine.racepower import fuel
from backend.engine.racepower import share as SH

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "static"


def _page() -> str:
    return (STATIC / "intro.html").read_text(encoding="utf-8")


def test_the_example_api_carries_the_stops_as_km_and_type_only():
    for kind in ("trail", "baiyue", "road"):
        d = I.trimmed(kind)
        snap = SH.example(SH.example_id(kind))
        want = sorted(x["km"] for x in snap["inputs"]["stops"])
        assert [x["km"] for x in d["stops"]] == want, kind
        assert all(set(x) == {"km", "type"} and x["type"] in fuel.STOP_TYPES for x in d["stops"]), kind
    trail = I.trimmed("trail")["stops"]
    assert [x["km"] for x in trail] == [12.0, 25.0, 38.0] and [x["type"] for x in trail] == ["aid", "big", "aid"]


def test_stops_keep_nothing_but_a_finite_km_and_a_known_type():
    got = I._stops([{"km": 30, "type": "big", "name": "某山屋"}, {"km": 5.123, "type": "??"},
                    {"km": float("nan")}, {"km": True}, "x", {"type": "aid"}])
    assert got == [{"km": 5.12, "type": fuel.STOP_DEFAULT}, {"km": 30, "type": "big"}]
    assert I._stops(None) == [] and I._stops({"km": 1}) == []


def test_both_try_buttons_are_the_card_and_still_a_link():
    html = _page()
    cards = re.findall(r'<a class="trybtn" data-try data-try-big href="/share/example-trail-50k">.*?</a>', html, re.S)
    assert len(cards) == 2
    for a in cards:
        assert 'data-try-tag data-i18n="intro.try.tag"' in a
        assert 'class="play" aria-hidden="true"' in a and "data-try-h" in a and "data-try-sub" in a
        svg = re.search(r"<svg [^>]*data-try-prof[^>]*>", a).group(0)
        assert 'aria-hidden="true"' in svg and 'focusable="false"' in svg and 'viewBox="0 0 440 70"' in svg
    script = html[html.index("function tryProf"):html.index("// 越野／百岳／路跑")]
    # drawn from the example API's data, nothing typed in: the profile, the stops, the legs' days
    assert "d.profile" in script and "d.stops" in script and ".day" in script
    assert not re.search(r"\b(12|25|38)(\.0)?\b", script)
    # colours from the theme tokens only (the four 外觀 choices)
    css = html[html.index(".trybtn {"):html.index(".try-end")]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css)


def test_the_tag_reads_in_both_languages():
    for loc in ("zh-TW", "en"):
        d = json.loads((STATIC / "i18n" / loc / "intro.json").read_text(encoding="utf-8"))
        assert d["try.tag"] and d["try.tag_baiyue"] and d["try.tag"] != d["try.tag_baiyue"], loc
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "intro.json").read_text(encoding="utf-8"))
    assert zh["try.tag"] == "示範賽事・免註冊"
