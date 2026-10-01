"""
Convert a FIT activity into WKO5-equivalent channels — the same per-sample
data WKO5 stores in `.wko4` after importing that FIT.

This lets any FIT source (COROS / Garmin API, TP download, a local file) feed
the chart engine with WKO5-identical data. Rules were derived by diffing the
original FIT embedded in each `.wko4` (field 4049) against WKO5's decoded
channels across ~1060 activities; see backend/tests/test_fit_to_channels.py.

WKO5 sample semantics: a sample labelled t holds its value over (prev t, t].

Time axis (2026-09-29):
  * WKO5 picks one of two labelling models per file, based on the recording
    interval:
      - 1-second recording (>= 95% of record gaps are exactly 1 s):
        a record at time r covers [r, r+1) and is labelled r+1.
        origin = first record's timestamp. Whenever records have a gap
        (next r > previous label), ONE empty sample labelled r is inserted
        to cover the gap (this is how pauses appear).
      - smart / irregular recording: a record at time r covers (prev r, r]
        and is labelled r. origin = session.start_time; samples at t <= 0 are
        dropped (zero width).
  * records sharing a timestamp are merged (later non-null fields win); a
    clock that goes backwards advances by 1 s instead.
  * leading records that define none of the file's data fields are dropped
    and the origin moves to the first data record. A field counts as data
    only if it is non-zero somewhere in the file (an indoor all-zero
    `distance`, or `activity_type=generic`, is not data). Emptiness is judged
    by which fields the record *defines*, not by their values — Garmin
    GPSMAP records define every field with invalid values and are kept.
  * the last label is clipped to session.total_elapsed_time (shifted by the
    trimmed lead-in) when that falls inside the last sample's interval.

Values:
  * units: speed m/s -> km/h (x3.6), distance m -> km (/1000),
    lat/long semicircles -> degrees, cadence += fractional_cadence,
    stance time / vertical oscillation mm or ms -> m or s
  * elapseddistance is rebased so the first sample is 0, and carried
    forward into inserted gap samples
  * speed above MAX_SPEED_KMH is blanked (GPS spikes)
  * values are quantized to WKO5's per-channel storage scale (half away
    from zero)
"""
from __future__ import annotations

import gzip
import io
import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import fitdecode

SEMI = 180.0 / 2 ** 31

# WKO5 channel -> (FIT record fields in priority order, factor, storage scale)
# Storage scale = packed-block field 114 WKO5 uses for that channel; values are
# rounded to 1/scale.
CHANNEL_MAP: dict[str, tuple[tuple[str, ...], float, float]] = {
    "heartrate": (("heart_rate",), 1.0, 1),
    "cadence": (("cadence",), 1.0, 1),
    "speed": (("enhanced_speed", "speed"), 3.6, 1000),
    "elevation": (("enhanced_altitude", "altitude"), 1.0, 10),
    "power": (("power",), 1.0, 1),
    "temperature": (("temperature",), 1.0, 1),
    "latitude": (("position_lat",), SEMI, 1e7),
    "longitude": (("position_long",), SEMI, 1e7),
    "elapseddistance": (("distance",), 0.001, 1e5),
    "stancetime": (("stance_time",), 0.001, 1000),
    "verticaloscillation": (("vertical_oscillation",), 0.001, 10000),
}

# "@" channels: stored by WKO5 as raw float64 (no quantization). Emitted only
# when non-zero somewhere in the file. (FIT field, factor)
AT_CHANNEL_MAP: dict[str, tuple[str, float]] = {
    "@activity_type": ("activity_type", 1.0),
    "@step_length": ("step_length", 1.0),
    "@vertical_ratio": ("vertical_ratio", 1.0),
    "@gps_accuracy": ("gps_accuracy", 1.0),
    "@air_power": ("Air Power", 1.0),          # Stryd / COROS developer fields
    "@effort_pace": ("Effort Pace", 3.6),
    "@form_power": ("Form Power", 1.0),
    "@impact_loading_rate": ("Impact Loading Rate", 1.0),
    "@leg_spring_stiffness": ("Leg Spring Stiffness", 1.0),
}
# FIT profile `activity_type` enum (fitdecode yields names for known values)
ACTIVITY_TYPE = {"generic": 0, "running": 1, "cycling": 2, "transition": 3,
                 "fitness_equipment": 4, "swimming": 5, "walking": 6,
                 "sedentary": 8, "all": 254}

ONE_SECOND_SHARE = 0.95
# WKO5 blanks GPS speed spikes: 151/160 km/h blanked, 110/115 km/h kept in
# the reference set. 40 m/s is the assumed cut-off.
MAX_SPEED_KMH = 144.0
_ZEROISH = (0, 0.0, "generic")


def _quantize(v: float, scale: float) -> float:
    # WKO5 rounds half away from zero (Python's round() is half-to-even).
    x = v * scale
    return (math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)) / scale


@dataclass
class FitChannels:
    start_time: Optional[object]
    sport: Optional[str]
    elapsedtime: list[float]
    channels: dict[str, list[Optional[float]]]
    one_second: bool = False
    sub_sport: Optional[str] = None     # session sub_sport (trail / treadmill / ...), when the device writes one
    stryd_device: bool = False          # a device_info row names a Stryd pod (backend/engine/power_source.py)

    @property
    def power_source(self) -> str:
        """stryd / watch / none (backend/engine/power_source.py)."""
        from backend.engine.power_source import classify
        return classify(self.channels, self.stryd_device)


def _read_messages(raw: bytes, devices: Optional[list] = None):
    records, sessions, events = [], [], []
    with fitdecode.FitReader(io.BytesIO(raw)) as fr:
        for fm in fr:
            if not isinstance(fm, fitdecode.FitDataMessage):
                continue
            if fm.name == "record":
                records.append({f.name: f.value for f in fm.fields})
            elif fm.name == "session":
                sessions.append({f.name: f.value for f in fm.fields})
            elif fm.name == "event":
                events.append({f.name: f.value for f in fm.fields})
            elif fm.name == "device_info" and devices is not None:
                devices.append({f.name: f.value for f in fm.fields
                                if f.name in ("manufacturer", "product_name", "product")})
    return records, sessions, events


def _drop_timer_stopped(records: list[dict], events: list[dict],
                        include_start: bool = False) -> list[dict]:
    """Drop records inside a timer-stopped span, including anything after the
    final stop (devices often write a junk record at session end).
    Span is (stop, start); with include_start it is (stop, start] — for smart
    recording a record at the restart instant covers (prev, start], i.e.
    entirely stopped time."""
    marks = sorted((e["timestamp"], e.get("event_type")) for e in events or []
                   if e.get("event") == "timer" and e.get("timestamp") is not None
                   and e.get("event_type") in ("start", "stop", "stop_all"))
    if not marks:
        return records
    # A stop only opens a stopped span when the next timer event is a start
    # (or there is none). Stop followed by another stop means the device kept
    # recording (Garmin GPSMAP writes repeated stop_all), so it is ignored.
    stopped: list[tuple[object, object]] = []
    for i, (ts, typ) in enumerate(marks):
        if typ == "start":
            continue
        nxt = marks[i + 1] if i + 1 < len(marks) else None
        if nxt is None:
            stopped.append((ts, None))
        elif nxt[1] == "start":
            stopped.append((ts, nxt[0]))
    out = []
    for r in records:
        ts = r.get("timestamp")
        if ts is not None and any(a < ts and (b is None or ts < b or (include_start and ts == b))
                                  for a, b in stopped):
            continue
        out.append(r)
    return out


def _data_fields(records: list[dict]) -> set[str]:
    keys = set()
    for r in records:
        for k, v in r.items():
            if k != "timestamp" and v is not None and v not in _ZEROISH:
                keys.add(k)
    return keys


def _is_one_second(records: list[dict]) -> bool:
    gaps = [(b["timestamp"] - a["timestamp"]).total_seconds()
            for a, b in zip(records, records[1:])]
    return bool(gaps) and sum(1 for g in gaps if g == 1) / len(gaps) >= ONE_SECOND_SHARE


# Experimental, OFF: WKO5 sometimes collapses an interior run of data-less
# records into one empty sample (seen in 1 COROS file and table-tennis files),
# but enabling it breaks as many files as it fixes. Kept for further study.
COLLAPSE_EMPTY_RUNS = False


def _collapse_empty_runs(samples, data):
    """1-second model: a run of consecutive samples carrying no data field
    (after the first data sample) becomes ONE empty sample at the run's end —
    the same representation as a time gap."""
    def blank(r):
        return r is None or not any(k in r for k in data)
    out = []
    seen_data = False
    for smp in samples:
        if blank(smp[1]) and seen_data and out and blank(out[-1][1]):
            out[-1] = (smp[0], None)
            continue
        if not blank(smp[1]):
            seen_data = True
        out.append(smp)
    return out


def build_samples(records: list[dict], session: dict):
    """Return ([(label seconds, record-or-None)], one_second, base_distance_m).
    None = gap sample. base_distance = distance subtracted from the channel."""
    records = [r for r in records if r.get("timestamp") is not None]
    if not records:
        return [], False, 0.0
    one_second = _is_one_second(records)
    data = _data_fields(records)

    shift = 0.0
    k = 0
    while k < len(records) and not any(f in records[k] for f in data):
        k += 1
    if 0 < k < len(records):
        shift = (records[k]["timestamp"] - records[0]["timestamp"]).total_seconds()
        records = records[k:]

    samples: list[tuple[float, Optional[dict]]] = []
    base_distance = 0.0
    if one_second:
        origin = records[0]["timestamp"]
        prev_t, last_label = None, 0.0
        for r in records:
            t = (r["timestamp"] - origin).total_seconds()
            if prev_t is not None and t == prev_t and samples and samples[-1][1] is not None:
                samples[-1][1].update({kk: v for kk, v in r.items() if v is not None})
                continue
            if prev_t is not None and t < prev_t:
                t = prev_t + 1.0
            if t + 1.0 <= last_label:
                continue
            if samples and t > last_label + 1e-9:
                samples.append((t, None))
            samples.append((t + 1.0, dict(r)))
            last_label, prev_t = t + 1.0, t
        if COLLAPSE_EMPTY_RUNS:
            samples = _collapse_empty_runs(samples, data)
        width = 1.0
    else:
        start = session.get("start_time") or records[0]["timestamp"]
        start = start + timedelta(seconds=shift)
        merged: list[dict] = []
        for r in records:
            if merged and merged[-1]["timestamp"] == r["timestamp"]:
                merged[-1].update({kk: v for kk, v in r.items() if v is not None})
            else:
                merged.append(dict(r))
        for r in merged:
            t = (r["timestamp"] - start).total_seconds()
            if t > 0:
                samples.append((t, r))
        width = (samples[-1][0] - samples[-2][0]) if len(samples) > 1 else 1.0
    # Distance rebase: 1-second model -> first *raw* record (before duplicate
    # merging); smart model -> first kept sample (the dropped t=0 record's
    # distance is not used).
    pool = records if one_second else [r for _, r in samples if r is not None]
    base_distance = next((r["distance"] for r in pool if r.get("distance") is not None), 0.0)

    total = session.get("total_elapsed_time")
    if samples and total is not None:
        total -= shift
        last = samples[-1][0]
        if last - width < total < last:
            samples[-1] = (float(total), samples[-1][1])
    return samples, one_second, base_distance


def fit_to_channels(raw: bytes) -> FitChannels:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    devices: list = []
    records, sessions, events = _read_messages(raw, devices)
    fc = channels_from_messages(records, sessions, events)
    from backend.engine.power_source import fit_stryd_device
    fc.stryd_device = fit_stryd_device(devices)
    return fc


def channels_from_messages(records: list[dict], sessions: list[dict],
                           events: Optional[list[dict]] = None) -> FitChannels:
    """Build WKO5 channels from already-decoded FIT record/session/event dicts."""
    session = sessions[0] if sessions else {}
    timed = [r for r in records if r.get("timestamp") is not None]
    records = _drop_timer_stopped(timed, events, include_start=not _is_one_second(timed))
    samples, one_second, base_distance = build_samples(records, session)
    times = [float(t) for t, _ in samples]
    rows = [r for _, r in samples]

    channels: dict[str, list[Optional[float]]] = {}
    present = [r for r in rows if r is not None]
    for ch, (fields, factor, scale) in CHANNEL_MAP.items():
        fld = next((f for f in fields if any(r.get(f) is not None for r in present)), None)
        if fld is None:
            continue
        if ch == "elapseddistance" and not any(r.get(fld) for r in present):
            continue  # all-zero distance: WKO5 drops the channel
        vals: list[Optional[float]] = []
        for r in rows:
            v = None if r is None else r.get(fld)
            if not isinstance(v, (int, float)):
                vals.append(None)
                continue
            if ch == "cadence" and isinstance(r.get("fractional_cadence"), (int, float)):
                v = v + r["fractional_cadence"]
            v = v * factor
            if ch == "speed" and v > MAX_SPEED_KMH:
                v = None  # GPS spike; WKO5 blanks it
            vals.append(v)
        if ch == "elapseddistance":
            # rebase; accumulate only positive increments (a device distance
            # drop/reset is ignored, later increments still count); 0 before
            # the first reading; carried forward into gap samples
            base = base_distance * factor
            out, last, prev_raw = [], None, None
            for r, v in zip(rows, vals):
                if v is not None:
                    if last is None:
                        last = v - base
                    elif v > prev_raw:
                        last += v - prev_raw
                    prev_raw = v
                    out.append(last)
                elif last is None:
                    out.append(0.0)
                else:
                    out.append(last if r is None else None)
            vals = out
        channels[ch] = [None if v is None else _quantize(v, scale) for v in vals]

    if "speed" not in channels and "elapseddistance" in channels:
        # No speed field: WKO5 derives it from distance / time (km/h).
        dist = channels["elapseddistance"]
        spd: list[Optional[float]] = []
        prev_t, prev_d = 0.0, 0.0
        for t, d in zip(times, dist):
            if d is None:
                spd.append(None)
                continue
            dt = t - prev_t
            v = (d - prev_d) / dt * 3600.0 if dt > 0 else None
            spd.append(None if v is None or v > MAX_SPEED_KMH else _quantize(v, 1000))
            prev_t, prev_d = t, d
        channels["speed"] = spd

    for ch, (fld, factor) in AT_CHANNEL_MAP.items():
        vals = []
        for r in rows:
            v = None if r is None else r.get(fld)
            if isinstance(v, str):
                v = ACTIVITY_TYPE.get(v)
            vals.append(float(v) * factor if isinstance(v, (int, float)) else None)
        if any(v for v in vals):
            channels[ch] = vals

    return FitChannels(start_time=records[0].get("timestamp") if records else None,
                       sport=session.get("sport"), elapsedtime=times,
                       channels=channels, one_second=one_second,
                       sub_sport=None if session.get("sub_sport") is None else str(session.get("sub_sport")))
