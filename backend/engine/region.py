"""
地區 (generalize-athlete plan L3 / L4, batch B3): tw | intl.

`tw` keeps everything Taiwan-specific (百岳 achievements and the mountain-name
lookup, 魯地圖 / NLSC basemaps, the CWA forecast, Taiwan presets); `intl`
hides them, shows a 百岳 event as 「多日登山」 and defaults the map to OSM.
The data model is not renamed (old data reads the same).

Order:
  1. athlete.region (進階設定)
  2. the home weather cell (engine/heat_data: the 0.25° cell with the most
     cached weather days) inside Taiwan's bounding box
     (scripts/build_baiyue.py: 21.5–26.5 N, 118–122.5 E)
  3. the detected time zone's browser zone (Asia/Taipei → tw)
  4. intl
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

KEY = "athlete.region"
REGIONS = ("tw", "intl")
TW_BBOX = (21.5, 26.5, 118.0, 122.5)       # lat lo, lat hi, lon lo, lon hi
TW_ZONES = ("Asia/Taipei",)
LABEL = {"tw": "台灣", "intl": "台灣以外"}


def in_taiwan(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    a, b, c, d = TW_BBOX
    return a <= lat <= b and c <= lon <= d


def home_cell(root: Optional[Path] = None) -> Optional[tuple[float, float]]:
    """(lat, lon) of the weather cell with the most cached days; None without a cache."""
    if root is None:
        from backend.engine import routes as R
        root = R.home()
    try:
        files = list((Path(root) / "weather").glob("*.json"))
    except OSError:
        return None
    n: dict = {}
    for p in files:
        parts = p.stem.split("_")
        if len(parts) == 3:
            n[(parts[0], parts[1])] = n.get((parts[0], parts[1]), 0) + 1
    if not n:
        return None
    lat, lon = max(n, key=n.get)
    try:
        return float(lat), float(lon)
    except ValueError:
        return None


def detect(root: Optional[Path] = None, auto_tz: Optional[dict] = None) -> tuple[str, str]:
    """(region, how) from the data only (steps 2–4)."""
    cell = home_cell(root)
    if cell is not None:
        return ("tw" if in_taiwan(*cell) else "intl"), "住家天氣格點"
    br = (auto_tz or {}).get("browser")
    if br:
        return ("tw" if br in TW_ZONES else "intl"), "瀏覽器時區"
    return "intl", "預設"


def region(user_id: int = 1, root: Optional[Path] = None) -> tuple[str, str]:
    """(tw | intl, how it was decided)."""
    from backend.engine.wko5expr.datasource import read_setting
    v = read_setting(KEY, None, user_id)
    if v in REGIONS:
        return v, "設定"
    return detect(root, read_setting("athlete.timezone.auto", None, user_id))


def is_tw(user_id: int = 1) -> bool:
    return region(user_id)[0] == "tw"


def default_basemap(reg: str) -> str:
    return "rudy" if reg == "tw" else "osm"
