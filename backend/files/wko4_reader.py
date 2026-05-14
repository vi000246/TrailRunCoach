import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

WKO4_MAGIC = b"wko4"
_FILENAME_RE = re.compile(r"_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})\.wko4$")
_SPORT_MAP = {
    b"Walking": "walking",
    b"Cycling": "cycling",
    b"Running": "running",
    b"Swimming": "swimming",
    b"Biking": "cycling",
    b"Strength Training": "strength",
    b"Other": "other",
}


@dataclass
class Wko4Metadata:
    start_time: Optional[datetime]
    sport: str
    source_file: str


def parse_wko4_metadata(path: str) -> Wko4Metadata:
    """
    Extract metadata from .wko4 file.
    Strategy: parse filename for datetime; scan first 256 bytes for sport string.
    Avoids full binary format reversal — channel data comes from TP FIT files.
    """
    p = Path(path)
    start_time = None
    sport = "unknown"

    m = _FILENAME_RE.search(p.name)
    if m:
        yyyy, mm, dd, hh, mn = (int(x) for x in m.groups())
        try:
            start_time = datetime(yyyy, mm, dd, hh, mn)
        except ValueError:
            pass

    try:
        with open(path, "rb") as f:
            header = f.read(256)
        if header[:4] != WKO4_MAGIC:
            return Wko4Metadata(start_time, sport, path)
        for keyword, sport_name in _SPORT_MAP.items():
            if keyword in header:
                sport = sport_name
                break
        if start_time is None:
            date_m = re.search(rb"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", header)
            if date_m:
                try:
                    start_time = datetime.fromisoformat(date_m.group(1).decode())
                except ValueError:
                    pass
    except OSError:
        pass

    return Wko4Metadata(start_time, sport, path)
