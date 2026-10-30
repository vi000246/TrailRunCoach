"""SP-1062: what Trig says about 疊石 Cairn never promises everyone a live position. Cairn's rule is to say only
what everyone who signs up gets, and the live track needs a Garmin inReach connected: before you set off the
family gets a link to the route and each day's plan, and 即時 / live comes only with the inReach that makes it so.
Every catalog text and manual line that names Cairn, in both languages (the 介紹頁's own block: SP-1058)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
I18N = ROOT / "backend" / "static" / "i18n"
MANUAL = ROOT / "backend" / "manual"
CAIRN = re.compile(r"疊石|Cairn")


def _cairn_texts():
    for p in sorted(I18N.glob("*/*.json")):
        for k, v in json.loads(p.read_text("utf-8")).items():
            for t in (v.values() if isinstance(v, dict) else (v,)):
                if isinstance(t, str) and CAIRN.search(t):
                    yield f"{p.relative_to(I18N)} {k}", t
    for p in sorted(MANUAL.glob("*/*.md")):
        for i, line in enumerate(p.read_text("utf-8").splitlines(), 1):
            if CAIRN.search(line):
                yield f"{p.relative_to(MANUAL)}:{i}", line


def test_cairn_shares_a_live_track_only_with_an_inreach():
    seen = set()
    for where, t in _cairn_texts():
        seen.add(where.split(" ")[0].split(":")[0])
        assert not re.search(r"即時位置|live (position|location)", t, re.I), where
        for clause in re.split(r"[。；？！.;?!]", t):
            # where you are, live (即時天氣 / live weather layers are another thing)
            if re.search(r"即時(位置|軌跡|追蹤|定位)|\blive (position|location|track|tracking)\b", clause, re.I):
                assert "inReach" in clause, (where, clause)
    # the places that introduce Cairn are all read: the 介紹頁, the about page, the cross-promotion card, the manual
    for f in ("zh-TW/intro.json", "en/intro.json", "zh-TW/about.json", "en/about.json", "zh-TW/common.json",
              "en/common.json", "zh-TW/race.md", "en/race.md"):
        assert f in seen, f
