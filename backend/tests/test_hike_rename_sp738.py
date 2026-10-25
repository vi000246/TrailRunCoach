"""
SP-738 (owner 2026-10-19): the calculator's 百岳 type reads 登山 (郊山、中級山、百岳 alike; the stored
type / event kind stays `baiyue`), a 登山 trip is 1 day unless the route says more, and a trip without a
pack takes the 預設背負 4 kg (single day) / 9 kg (multi-day).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = (STATIC / "racepower.html").read_text("utf-8")


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def test_type_button_reads_hiking_with_its_help():
    seg = re.search(r'<div class="seg" id="type">(.*?)</div>', PAGE).group(1)
    assert '<button data-t="baiyue" data-i18n="racepower.type.hike">登山</button>' in seg
    assert "百岳" not in seg and "多日登山" not in seg
    assert 'data-i18n="racepower.type.hike_tip">登山：郊山、中級山、百岳都適用' in PAGE
    assert _cat("zh-TW", "racepower")["type.hike"] == "登山"
    assert "郊山、中級山、百岳都適用" in _cat("zh-TW", "racepower")["type.hike_tip"]
    assert _cat("en", "racepower")["type.hike"] == "Hiking"


def test_calculator_texts_say_hiking():
    for key in ("trip.tip", "coros.tip", "nm.tip", "noweight.banner"):
        assert "百岳" not in _cat("zh-TW", "racepower")[key], key
        assert "Baiyue" not in _cat("en", "racepower")[key], key
    assert "百岳步行模型" not in PAGE and "登山步行模型" in PAGE
    assert "體重 × 13 %" not in PAGE and "單日 4 kg、多日 9 kg" in PAGE
    # the share page's type (SP-460: its words are the share catalog's)
    assert 'baiyue: JT("type.baiyue")' in (STATIC / "share.html").read_text("utf-8")
    assert _cat("zh-TW", "share")["type.baiyue"] == "登山" and _cat("en", "share")["type.baiyue"] == "Hiking"


def test_race_manager_kind_reads_hiking_and_old_events_still_load():
    from backend.engine import planning as P
    from backend.api import plan as PA
    assert P.KINDS["baiyue"] == "登山" and PA._kinds()["baiyue"] == "登山"
    races = (STATIC / "races.html").read_text("utf-8")
    assert races.count('<option value="baiyue" data-i18n="races.kind.baiyue">登山</option>') == 2
    assert _cat("zh-TW", "races")["kind.baiyue"] == "登山" and _cat("en", "races")["kind.baiyue"] == "Hiking"
    assert "13 %" not in _cat("zh-TW", "races")["ed.pack_tip"]
    e = P.Event(id="x", name="合歡北峰", date="2026-11-01", kind="baiyue", priority="B")
    assert e.kind == "baiyue" and e.pack == 4.0                 # an old 百岳 event: same kind, single day 4 kg
    e3 = P.Event(id="y", name="南湖", date="2026-11-01", kind="baiyue", priority="A", days=3)
    assert e3.pack == 9.0


def test_trip_days_default_to_one():
    assert re.search(r'id="tripdays"[^>]*value="1"', PAGE)
    assert '$("days").value = 3' not in PAGE and "S.daysInit" not in PAGE


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_route_days_follow_the_route():
    fn = re.search(r"function routeDays\(eid\) \{.*?\n\}", PAGE, re.S).group(0)
    js = fn + """
const out = [];
let SPLITS = [], CAIRN_NIGHTS = null, INP = { events: [{ id: "e3", days: 3 }, { id: "e1", days: 1 }] };
out.push(routeDays(null));                                   // a plain GPX: 1
out.push(routeDays("e1"));                                   // a 1-day event: 1
out.push(routeDays("e3"));                                   // the event's own days
SPLITS = [8.2]; out.push(routeDays("e1"));                   // a GPX split by day: 2
SPLITS = []; CAIRN_NIGHTS = [{ km: 5 }, { km: 12 }]; out.push(routeDays(null));   // Cairn: 2 nights → 3
console.log(JSON.stringify(out));
"""
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [1, 1, 3, 2, 3]


def test_planner_default_pack_single_and_multi_day():
    from backend.tests.test_group_itinerary_sp578 import plan
    p1 = plan()
    assert p1["summary"]["pack_kg"] == 4.0 and p1["summary"]["pack_src"].startswith("預設背負")
    assert "單日" in p1["summary"]["pack_src"]
    p2 = plan(day_splits_km=[6.0])
    assert p2["summary"]["pack_by_day"][0] == 9.0 and "多日" in p2["summary"]["pack_src"]
    assert any("沒填背負" in (w if isinstance(w, str) else w.get("text", "")) for w in p2.get("warnings", []))
    p3 = plan(pack_kg=12.0)
    assert p3["summary"]["pack_kg"] == 12.0 and p3["summary"]["pack_src"] == "手動"
