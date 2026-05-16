import re
import struct
from dataclasses import dataclass, field
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
_CHANNEL_MARKER = b"\xb4\x06"


@dataclass
class Wko4Metadata:
    start_time: Optional[datetime]
    sport: str
    source_file: str
    duration_s: Optional[float] = field(default=None)
    total_distance_m: Optional[float] = field(default=None)


def extract_wko4_metrics(path: str) -> dict:
    """
    Extract total_distance_m and duration_s from WKO4 binary channels.
    Strategy: scan all occurrences of 'elapseddistance' and 'elapsedtime' binary channels,
    try multiple record-size interpretations, keep the first plausible value.
    Returns {"total_distance_m": float|None, "duration_s": float|None}.
    """
    result: dict = {"total_distance_m": None, "duration_s": None}
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return result

    def _scan_channel(field_name: bytes, min_val: float, max_val: float) -> Optional[float]:
        """Find all occurrences of field channel, probe multiple record sizes, return best value."""
        marker = field_name + _CHANNEL_MARKER
        pos = 0
        while True:
            idx = data.find(marker, pos)
            if idx < 0:
                break
            base = idx + len(marker)
            if base + 2 > len(data):
                break
            count = struct.unpack_from("<H", data, base)[0]
            if 1 <= count <= 100_000:
                # Try record sizes that could plausibly fit in the remaining file data
                for rec_size in (10, 8, 6, 4):
                    last_offset = base + 2 + (count - 1) * rec_size
                    # For 10-byte records: 2-byte timestamp prefix then 8-byte float64
                    # For 8-byte records: direct float64
                    float_offset = last_offset + (2 if rec_size >= 10 else 0)
                    float_size = min(rec_size, 8)
                    if float_offset + float_size > len(data):
                        continue
                    try:
                        if float_size == 8:
                            val = struct.unpack_from("<d", data, float_offset)[0]
                        else:
                            val = struct.unpack_from("<f", data, float_offset)[0]
                    except struct.error:
                        continue
                    if min_val < val < max_val:
                        return float(val)
            pos = idx + len(marker)
        return None

    result["total_distance_m"] = _scan_channel(b"elapseddistance", 100.0, 200_000.0)
    result["duration_s"] = _scan_channel(b"elapsedtime", 60.0, 86_400.0)
    return result


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
            metrics = extract_wko4_metrics(path)
            return Wko4Metadata(
                start_time=start_time, sport=sport, source_file=path,
                duration_s=metrics["duration_s"],
                total_distance_m=metrics["total_distance_m"],
            )
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

    metrics = extract_wko4_metrics(path)
    return Wko4Metadata(
        start_time=start_time,
        sport=sport,
        source_file=path,
        duration_s=metrics["duration_s"],
        total_distance_m=metrics["total_distance_m"],
    )
