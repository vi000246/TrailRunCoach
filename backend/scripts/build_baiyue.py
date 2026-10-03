"""
Build backend/data/baiyue.json — Taiwan's 百岳 (and 小百岳) with elevation and
coordinates, read by the achievements page (summit detection) and the race-power
page (weather location for a 百岳 target).

Source: a peak list file (e.g. a TypeScript / JSON snapshot of the published
百岳 and 小百岳 lists) with rows `[name, ele_m, lng, lat, flags]` and
flag bits 1 = 百岳, 2 = 小百岳 (8 = 溫泉, 16 = 古道遺跡 are not peaks and are
skipped). The file is only *parsed* as data here — nothing in it is executed.

Usage:
    .venv/Scripts/python.exe -m backend.scripts.build_baiyue path/to/peaks-data.ts
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "baiyue.json"

FLAG_BAIYUE, FLAG_XIAO = 1, 2
ROW = re.compile(r'\[\s*"([^"]+)"\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(\d+)\s*\]')


def parse_rows(text: str) -> list[tuple[str, float, float, float, int]]:
    return [(m[1], float(m[2]), float(m[3]), float(m[4]), int(m[5])) for m in ROW.finditer(text)]


def build(rows) -> dict:
    peaks = []
    for name, ele, lng, lat, flags in rows:
        if not flags & (FLAG_BAIYUE | FLAG_XIAO):
            continue
        # sanity: Taiwan and its islands
        if not (21.5 <= lat <= 26.5 and 118.0 <= lng <= 122.5) or not (0 <= ele < 4000):
            raise ValueError(f"implausible peak row: {name} {ele} {lng} {lat}")
        peaks.append({"name": name, "elevation_m": round(ele), "lat": round(lat, 5), "lon": round(lng, 5),
                      "baiyue": bool(flags & FLAG_BAIYUE), "xiaobaiyue": bool(flags & FLAG_XIAO)})
    baiyue = sorted((p for p in peaks if p["baiyue"]), key=lambda p: (-p["elevation_m"], p["name"]))
    for i, p in enumerate(baiyue, 1):
        p["rank"] = i                 # by elevation (1 = 玉山)
    n = len(baiyue)
    if n != 100:
        raise ValueError(f"expected exactly 100 百岳, found {n}")
    peaks.sort(key=lambda p: (not p["baiyue"], -p["elevation_m"], p["name"]))
    return {
        "source": "peak list rows [name, ele_m, lng, lat, flags] (flags: 1 = 百岳, 2 = 小百岳); "
                  "rank = order by elevation",
        "count": {"baiyue": n, "xiaobaiyue": sum(1 for p in peaks if p["xiaobaiyue"])},
        "peaks": peaks,
    }


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        sys.exit("usage: python -m backend.scripts.build_baiyue path/to/peaks-data.ts")
    src = Path(argv[1])
    data = build(parse_rows(src.read_text("utf-8")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    print(f"wrote {OUT}: {data['count']}")


if __name__ == "__main__":
    main(sys.argv)
