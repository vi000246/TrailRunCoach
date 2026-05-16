"""
FIT file parser for Coros and Garmin devices.

Primary: python-fitparse (strict FIT standard compliance).
Fallback: fitdecode with ErrorHandling.IGNORE — handles Coros 'other' sport FIT files
that use a non-standard uint32 field size of 1 byte instead of 4 bytes. This is a
systematic Coros firmware bug affecting all running workouts saved as 'other' type.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np


@dataclass
class RawWorkout:
    """Parsed workout data before metric computation."""

    source_file: str
    sport: str  # "cycling", "running", "swimming", etc.
    start_time: Optional[datetime]
    device: Optional[str]

    # Time-series channels (uniform 1s samples after resampling)
    time_s: np.ndarray = field(default_factory=lambda: np.array([]))
    power_w: np.ndarray = field(default_factory=lambda: np.array([]))
    heart_rate_bpm: np.ndarray = field(default_factory=lambda: np.array([]))
    cadence_rpm: np.ndarray = field(default_factory=lambda: np.array([]))
    distance_m: np.ndarray = field(default_factory=lambda: np.array([]))
    altitude_m: np.ndarray = field(default_factory=lambda: np.array([]))
    speed_ms: np.ndarray = field(default_factory=lambda: np.array([]))

    # Lap summaries (from FIT lap messages)
    laps: list[dict] = field(default_factory=list)

    # Session summary (from FIT session message)
    session: dict = field(default_factory=dict)

    @property
    def has_power(self) -> bool:
        return len(self.power_w) > 0 and np.any(self.power_w > 0)

    @property
    def has_hr(self) -> bool:
        return len(self.heart_rate_bpm) > 0 and np.any(self.heart_rate_bpm > 0)

    @property
    def has_cadence(self) -> bool:
        return len(self.cadence_rpm) > 0 and np.any(self.cadence_rpm > 0)

    @property
    def duration_s(self) -> float:
        if len(self.time_s) == 0:
            return 0.0
        return float(self.time_s[-1] - self.time_s[0])

    @property
    def total_distance_m(self) -> float:
        if len(self.distance_m) > 0:
            return float(np.nanmax(self.distance_m))
        if "total_distance" in self.session:
            return float(self.session["total_distance"])
        return 0.0


def parse_fit(path: str) -> RawWorkout:
    """
    Parse a .fit file and return a RawWorkout.

    Tries fitparse first; falls back to fitdecode (lenient) for Coros 'other'
    sport files that have a non-standard uint32 field size.

    Raises:
        FileNotFoundError: if path does not exist
        ValueError: if file cannot be parsed by either library
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"FIT file not found: {path}")

    try:
        return _parse_fit_fitparse(path)
    except Exception:
        pass

    try:
        return _parse_fit_fitdecode(path)
    except Exception as e:
        raise ValueError(f"Cannot parse FIT file (tried fitparse + fitdecode): {e}")


def _parse_fit_fitparse(path: str) -> RawWorkout:
    import fitparse

    fit = fitparse.FitFile(path)

    records: list[dict] = []
    laps: list[dict] = []
    session: dict = {}
    device_name: Optional[str] = None
    sport = "unknown"
    start_time: Optional[datetime] = None

    for msg in fit.get_messages():
        name = msg.name

        if name == "record":
            rec = _extract_record(msg)
            if rec:
                records.append(rec)

        elif name == "lap":
            laps.append(_extract_lap(msg))

        elif name == "session":
            session = _extract_session(msg)
            if "sport" in session:
                sport = _normalize_sport(str(session["sport"]))
            if "start_time" in session and session["start_time"]:
                start_time = session["start_time"]

        elif name == "device_info":
            device_name = _extract_device_name(msg)

        elif name == "file_id":
            for f in msg.fields:
                if f.name == "time_created" and f.value:
                    start_time = start_time or f.value

    if not records:
        return RawWorkout(
            source_file=path,
            sport=sport,
            start_time=start_time,
            device=device_name,
            session=session,
            laps=laps,
        )

    return _build_raw_workout(
        records=records,
        source_file=path,
        sport=sport,
        start_time=start_time,
        device=device_name,
        laps=laps,
        session=session,
    )


def _parse_fit_fitdecode(path: str) -> RawWorkout:
    """Lenient fallback parser for Coros non-standard FIT files."""
    import fitdecode

    records: list[dict] = []
    laps: list[dict] = []
    session: dict = {}
    device_name: Optional[str] = None
    sport = "unknown"
    start_time: Optional[datetime] = None

    def _get(frame, field_name):
        try:
            if frame.has_field(field_name):
                return frame.get_value(field_name)
        except Exception:
            pass
        return None

    with fitdecode.FitReader(path, error_handling=fitdecode.ErrorHandling.IGNORE) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            name = frame.name

            if name == "record":
                ts = _get(frame, "timestamp")
                if ts is None:
                    continue
                rec = {"timestamp": ts}
                for field_name in ("power", "heart_rate", "cadence", "distance", "altitude", "speed"):
                    v = _get(frame, field_name)
                    if v is not None:
                        rec[field_name] = float(v)
                records.append(rec)

            elif name == "lap":
                lap = {}
                for fd in frame:
                    if fd.value is not None:
                        lap[fd.name] = fd.value
                laps.append(lap)

            elif name == "session":
                for fd in frame:
                    if fd.value is not None:
                        session[fd.name] = fd.value
                if "sport" in session:
                    sport = _normalize_sport(str(session["sport"]))
                if "start_time" in session and session["start_time"]:
                    start_time = session["start_time"]

            elif name == "sport":
                sp = _get(frame, "sport")
                if sp and sport == "unknown":
                    sport = _normalize_sport(str(sp))

            elif name == "device_info":
                mfr = _get(frame, "manufacturer") or ""
                prod = _get(frame, "product_name") or _get(frame, "product") or ""
                if mfr or prod:
                    device_name = device_name or f"{mfr} {prod}".strip()

            elif name == "file_id":
                tc = _get(frame, "time_created")
                if tc:
                    start_time = start_time or tc

    if not records:
        return RawWorkout(
            source_file=path,
            sport=sport,
            start_time=start_time,
            device=device_name,
            session=session,
            laps=laps,
        )

    return _build_raw_workout(
        records=records,
        source_file=path,
        sport=sport,
        start_time=start_time,
        device=device_name,
        laps=laps,
        session=session,
    )


def _extract_record(msg) -> Optional[dict]:
    rec = {}
    for f in msg.fields:
        if f.value is None:
            continue
        if f.name == "timestamp":
            rec["timestamp"] = f.value
        elif f.name == "power":
            rec["power"] = float(f.value)
        elif f.name == "heart_rate":
            rec["heart_rate"] = float(f.value)
        elif f.name == "cadence":
            rec["cadence"] = float(f.value)
        elif f.name == "distance":
            rec["distance"] = float(f.value)
        elif f.name == "altitude":
            rec["altitude"] = float(f.value)
        elif f.name == "speed":
            rec["speed"] = float(f.value)
    return rec if "timestamp" in rec else None


def _extract_lap(msg) -> dict:
    lap = {}
    for f in msg.fields:
        if f.value is None:
            continue
        lap[f.name] = f.value
    return lap


def _extract_session(msg) -> dict:
    session = {}
    for f in msg.fields:
        if f.value is None:
            continue
        session[f.name] = f.value
    return session


def _extract_device_name(msg) -> Optional[str]:
    info = {}
    for f in msg.fields:
        if f.value is not None:
            info[f.name] = str(f.value)
    manufacturer = info.get("manufacturer", "")
    product = info.get("product_name", info.get("product", ""))
    if manufacturer or product:
        return f"{manufacturer} {product}".strip()
    return None


def _normalize_sport(sport: str) -> str:
    sport_lower = sport.lower()
    if any(x in sport_lower for x in ("cycl", "bike", "virt")):
        return "cycling"
    if any(x in sport_lower for x in ("run", "trail")):
        return "running"
    if "swim" in sport_lower:
        return "swimming"
    if "walk" in sport_lower:
        return "walking"
    return sport_lower


def _build_raw_workout(
    records: list[dict],
    source_file: str,
    sport: str,
    start_time: Optional[datetime],
    device: Optional[str],
    laps: list[dict],
    session: dict,
) -> RawWorkout:
    """Resample records to uniform 1s grid."""
    if not records:
        return RawWorkout(source_file=source_file, sport=sport,
                          start_time=start_time, device=device)

    # Convert timestamps to elapsed seconds
    t0 = records[0]["timestamp"]
    def to_seconds(ts) -> float:
        if hasattr(ts, "timestamp"):
            t0_sec = t0.timestamp() if hasattr(t0, "timestamp") else float(t0)
            return ts.timestamp() - t0_sec
        return float(ts) - float(t0)

    raw_time = np.array([to_seconds(r["timestamp"]) for r in records])
    n = len(raw_time)

    def _channel(key: str) -> np.ndarray:
        return np.array([r.get(key, np.nan) for r in records], dtype=np.float64)

    power = _channel("power")
    hr = _channel("heart_rate")
    cadence = _channel("cadence")
    distance = _channel("distance")
    altitude = _channel("altitude")
    speed = _channel("speed")

    # Resample to 1s grid
    total_s = int(np.ceil(raw_time[-1]))
    uniform_time = np.arange(0, total_s + 1, dtype=np.float64)

    def resample(values: np.ndarray) -> np.ndarray:
        valid = ~np.isnan(values)
        if not np.any(valid):
            return np.zeros(len(uniform_time))
        return np.interp(uniform_time, raw_time[valid], values[valid])

    if start_time is None and hasattr(records[0]["timestamp"], "replace"):
        start_time = records[0]["timestamp"]

    return RawWorkout(
        source_file=source_file,
        sport=sport,
        start_time=start_time,
        device=device,
        time_s=uniform_time,
        power_w=resample(power),
        heart_rate_bpm=resample(hr),
        cadence_rpm=resample(cadence),
        distance_m=resample(distance),
        altitude_m=resample(altitude),
        speed_ms=resample(speed),
        laps=laps,
        session=session,
    )
