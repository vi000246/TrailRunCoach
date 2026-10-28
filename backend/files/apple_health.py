"""
Apple Health's export (「輸出所有健康資料」, export.zip) -> one FIT per workout (SP-833).

The ZIP holds apple_health_export/export.xml — every sample the phone ever stored, often over
1 GB — and apple_health_export/workout-routes/route_*.gpx (a workout's GPS track: time, position,
altitude, speed; no heart rate, no sport). What a workout needs is spread over both:

  * <Workout workoutActivityType="HKWorkoutActivityTypeRunning" startDate="2024-05-01 06:30:00 +0800"
    endDate=… duration=… totalDistance="5.1" totalDistanceUnit="km"> (newer exports: the distance
    in a <WorkoutStatistics type="HKQuantityTypeIdentifierDistance…" sum= unit=> child) and its
    <WorkoutRoute><FileReference path="/workout-routes/route_….gpx"/></WorkoutRoute>
  * <Record type="HKQuantityTypeIdentifierHeartRate" startDate=… value="142"/>, anywhere in the file
    (not inside the workout; in practice before the workouts)
  * SP-912: <Record type="HKQuantityTypeIdentifierRunningPower" unit="W" …/> (the watch's running
    power) and <Record type="HKQuantityTypeIdentifierStepCount" startDate=… endDate=… value="152"/>
    (steps over a span: the cadence, as strides per minute like a watch's FIT, from spans of at most
    MAX_STEP_SPAN_S). Both kept with their source (sourceName): a workout takes the ones of its own
    source when it has any (the watch's steps, not the phone's counted again), else all.
  * SP-960: <Record type="HKQuantityTypeIdentifierCyclingPower" unit="W" …/> and
    <Record type="HKQuantityTypeIdentifierCyclingCadence" unit="count/min" …/> (iOS 17+: a ride's
    power and cadence, the cadence in revolutions per minute as a bike FIT has it), each at the middle
    of its span, kept as the running ones are; a workout with some of them takes them over the running
    power and the step cadence.
  * SP-961: <Record type="HKQuantityTypeIdentifierRunningSpeed" unit="m/s" …/> (the watch's measured
    running speed), at the middle of its span, kept the same way: an indoor workout's FIT (no route)
    carries it on its points, and each point's distance from it, scaled to the workout's total (Q1
    (b): per-km splits, the same total as the 「健身」app). Without speed: no per-point distance.

So export.xml is read once, streamed (scan_export: expat on pieces of the ZIP entry, never the
whole file in memory, never a tree): the workouts are kept (small), every heart-rate sample goes to
a scratch file as (time, bpm) pairs; after the pass the samples inside a workout's span are given
to that workout. Memory: the workouts and the samples that fall inside them. One workout's FIT is
written later (to_fit) from its samples and its route GPX, when the import reaches it — so a big
export is still worked in parts.

The pass itself can be done in parts and survives a restart (SP-911): between two children of
<HealthData> it writes down where it is (a checkpoint: the byte position, the workouts so far, the
length of the heart-rate scratch file, and the file's header — the prolog up to the first child),
and a later pass over the same export.xml inflates the bytes before that position again (fast) but
parses from there on: the header, then the rest of the file, to a new parser.

Untrusted input: expat with no entity declarations (one refuses the file — Apple's internal DTD
declares elements and attributes only), no external entities, no parameter entities; at most
MAX_WORKOUTS workouts, MAX_HR_SAMPLES heart-rate samples and MAX_MORE_SAMPLES power / cadence / step / speed samples
(past them: dropped, counted).
Formats from Apple's export as described publicly (推估: no real export at hand).
"""
from __future__ import annotations

import calendar
import json
import math
import os
import re
import time
from array import array
from pathlib import Path
from typing import Callable, Optional
from xml.parsers import expat

import numpy as np

from backend.files import track_fit as TF

READ_STEP = 1024 * 1024
MAX_WORKOUTS = 30_000                   # = archive.MAX_ACTIVITY_FILES
MAX_HR_SAMPLES = 50_000_000             # ≈ 10 years at one every 6 s; 10 bytes each on disk
HR_GAP_S = 30                           # a route point further than this from a heart-rate sample: none
HR_TYPE = "HKQuantityTypeIdentifierHeartRate"
# SP-912: running power and cadence (from the step count)
POWER_TYPE = "HKQuantityTypeIdentifierRunningPower"
STEP_TYPE = "HKQuantityTypeIdentifierStepCount"
# SP-960: a ride's power and cadence (iOS 17+)
CYCLE_POWER_TYPE = "HKQuantityTypeIdentifierCyclingPower"
CYCLE_CAD_TYPE = "HKQuantityTypeIdentifierCyclingCadence"
# SP-961: an indoor run's measured speed
SPEED_TYPE = "HKQuantityTypeIdentifierRunningSpeed"
POWER, STEPS, CYCLE_POWER, CYCLE_CAD, SPEED = 0, 1, 2, 3, 4     # the channel of a sample in the scratch file
MAX_MORE_SAMPLES = 30_000_000           # power, cadence, step and speed samples together; 18 bytes each on disk
MAX_STEP_SPAN_S = 600                   # a step sample over a longer span says nothing of the cadence
MAX_POWER_W = 3000
MAX_STEPS_PER_MIN = 300
MAX_RPM = 250                           # a bike's cadence (FIT: one byte)
MAX_SPEED_MS = 15.0                     # 54 km/h: past it, not running
_SPEEDS = {"m/s": 1.0, "km/hr": 1 / 3.6, "km/h": 1 / 3.6, "mi/hr": 0.44704, "mi/h": 0.44704, "mph": 0.44704}
MAX_SOURCES = 255                       # source names told apart (more: one "other")
_UNITS = {"km": 1000.0, "m": 1.0, "mi": 1609.344, "yd": 0.9144, "ft": 0.3048, "cm": 0.01}
_PREFIX = "HKWorkoutActivityType"
# a pass in parts (SP-911)
CHECK_BYTES = 128 * 1024 * 1024         # a checkpoint every this much of export.xml (a restart loses less)
MAX_HEADER = 4 * 1024 * 1024            # the prolog kept to go on from (Apple's DTD: a few kB)
FORMAT = "4"                            # of the checkpoint and the scratch files: another one starts over
CHECKPOINT, HEADER, PAIRS, MORE = "checkpoint.json", "header.xml", "hr.bin", "more.bin"
_PAIR = np.dtype([("t", "<f8"), ("v", "<u2")])
# power: t, 0, watts; steps: t (start), span s, strides per minute; a ride's power / cadence: t
# (start), span s, watts / revolutions per minute; speed: t (start), span s, m/s; c: the channel,
# s: the source
_MORE = np.dtype([("t", "<f8"), ("e", "<f4"), ("v", "<f4"), ("c", "u1"), ("s", "u1")])


class AppleError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Refused(Exception):
    pass


class _Paused(Exception):
    def __init__(self, at: int):
        super().__init__(at)
        self.at = at


def apple_time(s: Optional[str]) -> Optional[float]:
    """'2024-05-01 06:30:00 +0800' -> UTC epoch seconds (None otherwise). Hand-parsed: there are
    millions of them."""
    if not s or len(s) < 25:
        return None
    try:
        y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        h, mi, se = int(s[11:13]), int(s[14:16]), int(s[17:19])
        z = s[20:25]
        off = (int(z[1:3]) * 3600 + int(z[3:5]) * 60) * (1 if z[0] == "+" else -1)
        if z[0] not in "+-" or not (1 <= mo <= 12 and 1 <= d <= 31 and h < 24 and mi < 60 and se < 61):
            return None
    except (ValueError, IndexError):
        return None
    return float(calendar.timegm((y, mo, d, h, mi, se)) - off)


def _num(s) -> float:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


def _meters(value, unit) -> Optional[float]:
    v = _num(value)
    k = _UNITS.get((unit or "").strip().lower())
    if math.isnan(v) or k is None or v < 0:
        return None
    return v * k


def sport_of(activity_type: Optional[str]) -> tuple:
    """HKWorkoutActivityTypeRunning -> FIT (sport, sub_sport) by the words (track_fit's table)."""
    a = (activity_type or "")
    if a.startswith(_PREFIX):
        a = a[len(_PREFIX):]
    return TF._sport(re.sub(r"(?<!^)(?=[A-Z])", " ", a))


def _load_checkpoint(scratch: Path, key: str) -> Optional[dict]:
    """The checkpoint of an earlier pass over this very export.xml (`key`), with its header and
    its heart-rate pairs at least as long as it says; None otherwise (start over)."""
    try:
        ck = json.loads((scratch / CHECKPOINT).read_text("utf-8"))
        header = (scratch / HEADER).read_bytes()
        size = (scratch / PAIRS).stat().st_size
        size_more = (scratch / MORE).stat().st_size
    except (OSError, ValueError):
        return None
    if not isinstance(ck, dict) or ck.get("key") != f"{FORMAT}|{key}" or ck.get("header") != len(header):
        return None
    try:
        pos, pairs, hr, dropped = int(ck["pos"]), int(ck["pairs"]), int(ck["hr"]), int(ck["dropped"])
        more, n_more = int(ck["more"]), int(ck["more_n"])
        sources = [str(s) for s in ck["sources"]]
        workouts = [dict(w) for w in ck["workouts"]]
    except (KeyError, TypeError, ValueError):
        return None
    if pos < len(header) or pairs > size or pairs % _PAIR.itemsize or min(hr, dropped, n_more) < 0 \
            or more > size_more or more % _MORE.itemsize or len(sources) > MAX_SOURCES:
        return None
    return {"pos": pos, "pairs": pairs, "hr": hr, "dropped": dropped, "workouts": workouts, "header": header,
            "more": more, "more_n": n_more, "sources": sources}


def clear(scratch: Path) -> None:
    """Forget a pass over export.xml (its checkpoint, header and scratch samples)."""
    for name in (CHECKPOINT, HEADER, PAIRS, MORE, CHECKPOINT + ".tmp"):
        try:
            (scratch / name).unlink()
        except OSError:
            pass


def _replace(tmp: Path, dst: Path) -> None:
    """os.replace, a few times on Windows where a virus scanner may still hold the file just read."""
    for n in range(20):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            if os.name != "nt" or n == 19:
                raise
            time.sleep(0.05)


def unwrap(index: int, fed: int) -> int:
    """expat's byte index of the current event, put right when the build counts it in 32 bits (a
    `long` on Windows; export.xml can pass 4 GB): the event is always within the last few MB of
    the `fed` bytes given to the parser, so the one index ≡ `index` mod 2³² just below `fed`."""
    return fed - ((fed - index) % (1 << 32))


_HEAD = re.compile(rb'^\{"key": ("(?:[^"\\]|\\.)*"), "pos": (\d+)[,}]')


def reading(scratch: Path) -> Optional[tuple[int, str]]:
    """(how far a pass over export.xml got in bytes, the key it was given) from its checkpoint;
    None: none. SP-957: asked on every poll of the import page while export.xml is read, so only
    the head of the file is read — save() writes "key" and "pos" first; the workouts so far (up
    to MAX_WORKOUTS of them) come after."""
    try:
        with open(scratch / CHECKPOINT, "rb") as f:
            m = _HEAD.match(f.read(64 * 1024))
        if m is None:
            return None
        key = json.loads(m.group(1))
    except (OSError, ValueError):
        return None
    if not isinstance(key, str) or not key.startswith(FORMAT + "|"):
        return None
    return int(m.group(2)), key[len(FORMAT) + 1:]


def progress(scratch: Path) -> Optional[int]:
    """How far a pass over export.xml got (bytes), from its checkpoint; None: none."""
    got = reading(scratch)
    return None if got is None else got[0]


def scan_export(read: Callable[[int], bytes], scratch: Path, base: str = "", key: Optional[str] = None,
                budget: Optional[int] = None) -> dict:
    """Stream export.xml (`read(n)` gives the next ≤ n bytes from its start, b"" at its end) ->
    {"workouts": [{start, end, type, distance_m, route, source, hr: [[t, bpm], …], power: [[t, W], …],
    cad: [[t0, t1, strides/min], …], rpm: [[t, rev/min], …] (a ride's, SP-960), speed: [[t, m/s], …]
    (SP-961)}] by start, "hr_samples": n, "more_samples": n, "dropped": n}.
    `route`: the ZIP member of its GPX (`base` + the FileReference path), or None. `scratch`: a
    folder for the samples (removed after). AppleError(unreadable) for no XML / no HealthData / a
    declared entity.

    Resumable (SP-911) when `key` names this export.xml (it changes when the file does): every
    CHECK_BYTES, between two children of <HealthData>, the pass is written down in `scratch` (where
    it is in the file, the workouts so far, how long the heart-rate pairs are) and a later call with
    the same key goes on from there — the bytes before it are only inflated again, not parsed. Past
    `budget` bytes read in this call it stops at the next such point: {"paused": True, "pos": n}."""
    scratch.mkdir(parents=True, exist_ok=True)
    pairs = scratch / PAIRS
    ck = _load_checkpoint(scratch, key) if key is not None else None
    if ck is None:
        clear(scratch)
    workouts: list[dict] = ck["workouts"] if ck else []
    cur: list = [None]
    seen = {"root": None, "hr": ck["hr"] if ck else 0, "dropped": ck["dropped"] if ck else 0,
            "depth": 0, "fed": 0, "more": ck["more_n"] if ck else 0}
    sources: dict = {s: i for i, s in enumerate(ck["sources"])} if ck else {}
    pos0 = ck["pos"] if ck else 0                        # where this call starts in the file
    lead = len(ck["header"]) if ck else 0                # parser input before the file's pos0: the header
    head: list = [bytearray() if key is not None and not ck else None]   # the file's first bytes, to the header
    header: list = [ck["header"] if ck else None]
    next_check = [pos0 + CHECK_BYTES]
    buf = array("d")
    vals = array("H")
    m_t, m_e, m_v, m_c, m_s = array("d"), array("f"), array("f"), array("B"), array("B")

    def opened(path: Path, length: Optional[int]):
        if length is None:
            return open(path, "wb")
        f = open(path, "r+b")
        f.truncate(length)                                # samples written after the checkpoint: read again
        f.seek(0, 2)
        return f
    out = opened(pairs, ck["pairs"] if ck else None)
    out_more = opened(scratch / MORE, ck["more"] if ck else None)

    def flush() -> None:
        if buf:
            a = np.empty(len(buf), dtype=_PAIR)
            a["t"] = np.frombuffer(buf, dtype="<f8")
            a["v"] = np.frombuffer(vals, dtype="<u2")
            out.write(a.tobytes())
            del buf[:]
            del vals[:]
        if m_t:
            a = np.empty(len(m_t), dtype=_MORE)
            a["t"] = np.frombuffer(m_t, dtype="<f8")
            a["e"] = np.frombuffer(m_e, dtype="<f4")
            a["v"] = np.frombuffer(m_v, dtype="<f4")
            a["c"] = np.frombuffer(m_c, dtype="u1")
            a["s"] = np.frombuffer(m_s, dtype="u1")
            out_more.write(a.tobytes())
            for x in (m_t, m_e, m_v, m_c, m_s):
                del x[:]

    def source(attrs) -> int:
        name = (attrs.get("sourceName") or "")[:80]
        i = sources.get(name)
        if i is None:
            if len(sources) >= MAX_SOURCES - 1:
                return MAX_SOURCES - 1                       # "other": never a workout's own
            i = sources[name] = len(sources)
        return i

    def more(t: float, span: float, v: float, channel: int, attrs) -> None:
        if seen["more"] >= MAX_MORE_SAMPLES:
            seen["dropped"] += 1
            return
        m_t.append(t)
        m_e.append(span)
        m_v.append(v)
        m_c.append(channel)
        m_s.append(source(attrs))
        seen["more"] += 1
        if len(m_t) >= 262_144:
            flush()

    def where() -> int:
        """The file position of the current event."""
        return unwrap(p.CurrentByteIndex, seen["fed"]) - lead + pos0

    def save(at: int) -> None:
        flush()
        for f in (out, out_more):
            f.flush()
            os.fsync(f.fileno())
        tmp = scratch / (CHECKPOINT + ".tmp")
        # "key" and "pos" first: reading() takes them from the head of the file (SP-957)
        tmp.write_text(json.dumps({"key": f"{FORMAT}|{key}", "pos": at, "pairs": out.tell(), "hr": seen["hr"],
                                   "more": out_more.tell(), "more_n": seen["more"],
                                   "sources": sorted(sources, key=sources.get),
                                   "dropped": seen["dropped"], "header": len(header[0]),
                                   "workouts": workouts}), "utf-8")
        _replace(tmp, scratch / CHECKPOINT)

    def start(name, attrs):
        if seen["root"] is None:
            seen["root"] = name
            if name != "HealthData":
                raise _Refused()
        seen["depth"] += 1
        if seen["depth"] == 2:                           # a child of <HealthData>: a point to go on from
            if header[0] is None and head[0] is not None:
                at = where()
                header[0] = bytes(head[0][:at])
                head[0] = None
                if key is not None:
                    (scratch / HEADER).write_bytes(header[0])
            if key is not None and header[0] is not None:
                at = where()
                if budget is not None and at - pos0 >= budget:
                    save(at)
                    raise _Paused(at)
                if at >= next_check[0]:
                    save(at)
                    next_check[0] = at + CHECK_BYTES
        if name == "Record":
            kind = attrs.get("type")
            if kind == POWER_TYPE:
                t = apple_time(attrs.get("startDate"))
                v = _num(attrs.get("value"))
                if t is not None and 0 <= v <= MAX_POWER_W and (attrs.get("unit") or "W") == "W":
                    more(t, 0.0, v, POWER, attrs)
                return
            if kind == STEP_TYPE:
                t, e = apple_time(attrs.get("startDate")), apple_time(attrs.get("endDate"))
                v = _num(attrs.get("value"))
                if t is None or e is None or not 0 < e - t <= MAX_STEP_SPAN_S or not v >= 0:
                    return
                rate = v / (e - t) * 60.0                       # steps per minute
                if rate <= MAX_STEPS_PER_MIN:
                    more(t, e - t, rate / 2.0, STEPS, attrs)     # strides per minute, a watch FIT's cadence
                return
            if kind == CYCLE_POWER_TYPE or kind == CYCLE_CAD_TYPE:              # SP-960
                t, e = apple_time(attrs.get("startDate")), apple_time(attrs.get("endDate"))
                v = _num(attrs.get("value"))
                if t is None:
                    return
                e = t if e is None or e < t else e
                unit = attrs.get("unit")
                if kind == CYCLE_POWER_TYPE:
                    ok, channel = 0 <= v <= MAX_POWER_W and (unit or "W") == "W", CYCLE_POWER
                else:
                    ok, channel = 0 <= v <= MAX_RPM and (unit or "count/min") in ("count/min", "rpm"), CYCLE_CAD
                if ok and e - t <= MAX_STEP_SPAN_S:
                    more(t, e - t, v, channel, attrs)
                return
            if kind == SPEED_TYPE:                                             # SP-961
                t, e = apple_time(attrs.get("startDate")), apple_time(attrs.get("endDate"))
                k = _SPEEDS.get((attrs.get("unit") or "m/s").strip().lower())
                if t is None or k is None:
                    return
                e = t if e is None or e < t else e
                v = _num(attrs.get("value")) * k
                if 0 <= v <= MAX_SPEED_MS and e - t <= MAX_STEP_SPAN_S:
                    more(t, e - t, v, SPEED, attrs)
                return
            if kind != HR_TYPE:
                return
            t = apple_time(attrs.get("startDate"))
            v = _num(attrs.get("value"))
            if t is None or not 20 <= v <= 250:
                return
            if seen["hr"] >= MAX_HR_SAMPLES:
                seen["dropped"] += 1
                return
            buf.append(t)
            vals.append(int(round(v)))
            seen["hr"] += 1
            if len(buf) >= 262_144:
                flush()
        elif name == "Workout":
            s, e = apple_time(attrs.get("startDate")), apple_time(attrs.get("endDate"))
            if s is None or e is None or e < s or len(workouts) >= MAX_WORKOUTS:
                cur[0] = None
                if s is not None and len(workouts) >= MAX_WORKOUTS:
                    seen["dropped"] += 1
                return
            cur[0] = {"start": s, "end": e, "type": attrs.get("workoutActivityType") or "",
                      "distance_m": _meters(attrs.get("totalDistance"), attrs.get("totalDistanceUnit")),
                      "route": None, "source": (attrs.get("sourceName") or "")[:80]}
        elif cur[0] is not None:
            if name == "FileReference" and cur[0]["route"] is None:
                path = (attrs.get("path") or "").replace("\\", "/").lstrip("/")
                if path and ".." not in path.split("/") and path.lower().endswith(".gpx"):
                    cur[0]["route"] = base + path
            elif name == "WorkoutStatistics" and cur[0]["distance_m"] is None \
                    and (attrs.get("type") or "").startswith("HKQuantityTypeIdentifierDistance"):
                cur[0]["distance_m"] = _meters(attrs.get("sum"), attrs.get("unit"))

    def end(name):
        seen["depth"] -= 1
        if name == "Workout" and cur[0] is not None:
            workouts.append(cur[0])
            cur[0] = None

    def refuse(*_a):
        raise _Refused()

    p = expat.ParserCreate()
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    p.EntityDeclHandler = refuse                          # no entity is ever declared, so none expands
    p.UnparsedEntityDeclHandler = refuse
    p.ExternalEntityRefHandler = lambda *_a: 1           # never fetched (the parameter entities are off)
    p.StartElementHandler = start
    p.EndElementHandler = end

    def feed(chunk: bytes) -> None:
        seen["fed"] += len(chunk)
        if head[0] is not None:
            if len(head[0]) + len(chunk) <= MAX_HEADER:
                head[0] += chunk
            else:
                head[0] = None                               # no header in sight: this pass cannot be resumed
        p.Parse(chunk, False)

    refused = False
    try:
        left = pos0
        while left > 0:                                      # what an earlier pass read: inflated, not parsed
            skipped = read(min(READ_STEP, left))
            if not skipped:
                raise _Refused()
            left -= len(skipped)
        if ck:
            feed(ck["header"])
        while True:
            chunk = read(READ_STEP)
            if not chunk:
                break
            feed(chunk)
        p.Parse(b"", True)
        flush()
    except _Paused as e:
        return {"paused": True, "pos": e.at}
    except (_Refused, expat.ExpatError):
        refused = True
    finally:
        out.close()
        out_more.close()
    if refused or seen["root"] is None:
        clear(scratch)
        raise AppleError(TF.NOT_TRACK)
    workouts.sort(key=lambda w: (w["start"], w["end"]))
    for w in workouts:
        w["hr"], w["power"], w["cad"], w["rpm"], w["speed"] = [], [], [], [], []
    _give_samples(pairs, workouts)
    _give_more(scratch / MORE, workouts, sources)
    clear(scratch)
    return {"workouts": workouts, "hr_samples": seen["hr"], "more_samples": seen["more"], "dropped": seen["dropped"]}


def _give_samples(pairs: Path, workouts: list[dict]) -> None:
    """Each heart-rate sample inside a workout's span goes to it (the latest-starting one of
    overlapping workouts), read from the scratch file in pieces."""
    if not workouts:
        return
    starts = np.array([w["start"] for w in workouts])
    ends = np.array([w["end"] for w in workouts])
    dt = _PAIR
    got_i, got_t, got_v = [], [], []
    with open(pairs, "rb") as f:
        while True:
            raw = f.read(dt.itemsize * 1_000_000)
            if not raw:
                break
            a = np.frombuffer(raw[:len(raw) - len(raw) % dt.itemsize], dtype=dt)
            idx = np.searchsorted(starts, a["t"], side="right") - 1
            ok = idx >= 0
            ok[ok] &= a["t"][ok] <= ends[idx[ok]]
            got_i.append(idx[ok])
            got_t.append(a["t"][ok])
            got_v.append(a["v"][ok])
    if not got_i:
        return
    i, t, v = np.concatenate(got_i), np.concatenate(got_t), np.concatenate(got_v)
    order = np.lexsort((t, i))
    i, t, v = i[order], t[order], v[order]
    cuts = np.flatnonzero(np.diff(i)) + 1
    for part_i, part_t, part_v in zip(np.split(i, cuts), np.split(t, cuts), np.split(v, cuts)):
        if len(part_i):
            workouts[int(part_i[0])]["hr"] = [[float(a), int(b)] for a, b in zip(part_t, part_v)]


def _give_more(path: Path, workouts: list[dict], sources: dict) -> None:
    """Each power / cadence / step sample whose middle is inside a workout's span goes to it (the
    latest-starting one of overlapping workouts); of a workout's samples of one kind, only those of
    its own source when it has any (the watch that recorded it — the phone's steps over the same
    minutes are not the cadence twice). SP-960: a ride's power, when the workout has some, is its
    power instead of the running one; a ride's cadence ("rpm": [[t, rev/min], …], at the middle of
    each span) instead of the steps'. SP-961: the running speed ("speed": [[t, m/s], …], at the
    middle of each span). From the scratch file in pieces."""
    if not workouts:
        return
    starts = np.array([w["start"] for w in workouts])
    ends = np.array([w["end"] for w in workouts])
    got = []
    with open(path, "rb") as f:
        while True:
            raw = f.read(_MORE.itemsize * 1_000_000)
            if not raw:
                break
            a = np.frombuffer(raw[:len(raw) - len(raw) % _MORE.itemsize], dtype=_MORE)
            mid = a["t"] + a["e"] / 2.0
            idx = np.searchsorted(starts, mid, side="right") - 1
            ok = idx >= 0
            ok[ok] &= mid[ok] <= ends[idx[ok]]
            if ok.any():
                got.append((idx[ok], a[ok]))
    if not got:
        return
    i = np.concatenate([g[0] for g in got])
    a = np.concatenate([g[1] for g in got])
    order = np.lexsort((a["t"], i))
    i, a = i[order], a[order]
    cuts = np.flatnonzero(np.diff(i)) + 1
    for part_i, part in zip(np.split(i, cuts), np.split(a, cuts)):
        if not len(part_i):
            continue
        w = workouts[int(part_i[0])]
        own = sources.get(w.get("source") or "", -1)
        got = {}
        for channel in (POWER, STEPS, CYCLE_POWER, CYCLE_CAD, SPEED):
            s = part[part["c"] == channel]
            if (s["s"] == own).any():
                s = s[s["s"] == own]
            got[channel] = s
        s = got[CYCLE_POWER] if len(got[CYCLE_POWER]) else got[POWER]
        w["power"] = [[float(t) + float(e) / 2.0, round(float(v), 1)] for t, e, v in zip(s["t"], s["e"], s["v"])]
        s = got[SPEED]
        w["speed"] = [[float(t) + float(e) / 2.0, round(float(v), 3)] for t, e, v in zip(s["t"], s["e"], s["v"])]
        if len(got[CYCLE_CAD]):
            s = got[CYCLE_CAD]
            w["rpm"] = [[float(t) + float(e) / 2.0, round(float(v), 1)] for t, e, v in zip(s["t"], s["e"], s["v"])]
        else:
            s = got[STEPS]
            w["cad"] = [[float(t), float(t) + float(e), round(float(v), 2)]
                        for t, e, v in zip(s["t"], s["e"], s["v"])]


def _nearest(samples: np.ndarray, tt: np.ndarray) -> np.ndarray:
    """The value ([t, value] rows by t) of the sample nearest each time, NaN past HR_GAP_S."""
    if not len(samples) or not len(tt):
        return np.full(len(tt), math.nan)
    j = np.clip(np.searchsorted(samples[:, 0], tt), 0, len(samples) - 1)
    k = np.clip(j - 1, 0, len(samples) - 1)
    near = np.where(np.abs(samples[k, 0] - tt) <= np.abs(samples[j, 0] - tt), k, j)
    return np.where(np.abs(samples[near, 0] - tt) <= HR_GAP_S, samples[near, 1], math.nan)


def _covering(spans: np.ndarray, tt: np.ndarray) -> np.ndarray:
    """The value ([t0, t1, value] rows by t0) of the latest-starting span holding each time, NaN
    when none does."""
    if not len(spans) or not len(tt):
        return np.full(len(tt), math.nan)
    j = np.searchsorted(spans[:, 0], tt, side="right") - 1
    ok = j >= 0
    jj = np.clip(j, 0, len(spans) - 1)
    return np.where(ok & (tt <= spans[jj, 1]), spans[jj, 2], math.nan)


def _sorted(rows, width: int) -> np.ndarray:
    a = np.array(rows or [], dtype=float).reshape(-1, width)
    return a[np.argsort(a[:, 0], kind="stable")] if len(a) else a


def _indoor_distance(tt: np.ndarray, ms: np.ndarray, total: Optional[float]) -> np.ndarray:
    """SP-961 Q1 (b): an indoor workout's distance at each time tt (sorted), from its measured speed
    ms (m/s, NaN where no sample is near: 0 there — a pause, no signal), by the trapezoid rule over
    the whole seconds a FIT keeps (the first point of each second; the others NaN, columns() drops
    them), then scaled so the last is the workout's total: per-km splits, and the total the
    「健身」app shows. Without that total the integral as it is. All NaN when there is no speed (or
    it sums to nothing): no distance is made up then (SP-833)."""
    out = np.full(len(tt), math.nan)
    if not len(tt) or not np.isfinite(ms).any():
        return out
    sec = np.floor(tt)
    first = np.concatenate([[True], np.diff(sec) > 0])
    t, v = sec[first], np.nan_to_num(ms[first], nan=0.0)
    d = np.concatenate([[0.0], np.cumsum((v[1:] + v[:-1]) / 2.0 * np.diff(t))])
    if not d[-1] > 0:
        return out
    if total is not None and math.isfinite(total) and total > 0:
        d = d * (float(total) / d[-1])
    out[first] = d
    return out


def to_fit(workout: dict, gpx: Optional[bytes]) -> bytes:
    """One workout (scan_export's) and its route GPX (or None) as a FIT: the route's points with
    the nearest heart-rate and power samples and the cadence of the step span holding them (SP-912)
    — of a ride, the nearest cadence sample (SP-960); without a route, the times of those samples
    alone with the nearest measured speed (SP-961) and, when there is some, each point's distance
    from it, scaled to the workout's distance (_indoor_distance); with no speed no per-point
    distance (none is made up) and the workout's distance on the session. The session spans the
    workout's start and end. TrackError when there is nothing to write."""
    start, end = float(workout["start"]), float(workout["end"])
    hr = _sorted(workout.get("hr"), 2)
    power = _sorted(workout.get("power"), 2)
    cad = _sorted(workout.get("cad"), 3)
    rpm = _sorted(workout.get("rpm"), 2)
    speed = _sorted(workout.get("speed"), 2)

    def cadence(tt: np.ndarray) -> np.ndarray:
        return _nearest(rpm, tt) if len(rpm) else _covering(cad, tt)
    pts: list = []
    if gpx:
        try:
            (t, lat, lon, alt, dist, _hr, gcad, spd, gpw), _sp = TF.parse(gpx, "gpx")
        except TF.TrackError:
            t = None
        if t is not None:
            keep = np.flatnonzero((t >= start - 60) & (t <= end + 60))
            tt = t[keep]
            beat, watts, steps = _nearest(hr, tt), _nearest(power, tt), cadence(tt)
            watts = np.where(np.isfinite(gpw[keep]), gpw[keep], watts)       # the route's own first
            steps = np.where(np.isfinite(gcad[keep]), gcad[keep], steps)
            pts = [(t[k], lat[k], lon[k], alt[k], dist[k], beat[n], steps[n], spd[k], watts[n])
                   for n, k in enumerate(keep)]
    if not pts:
        tt = np.unique(np.concatenate([hr[:, 0], power[:, 0], cad[:, 0], rpm[:, 0], speed[:, 0]]))
        tt = tt[(tt >= start) & (tt <= end)]
        beat, watts, steps, ms = _nearest(hr, tt), _nearest(power, tt), cadence(tt), _nearest(speed, tt)
        far = _indoor_distance(tt, ms, workout.get("distance_m"))
        pts = [(float(a), math.nan, math.nan, math.nan, float(d), float(b), float(c), float(v), float(w))
               for a, d, b, c, v, w in zip(tt, far, beat, steps, ms, watts)]
        if len(pts) < 2:
            pts += [(start,) + (math.nan,) * 8, (end,) + (math.nan,) * 8]
    cols, sport = TF.columns(pts, sport_of(workout.get("type")))
    return TF.fit_of(cols, sport, start=start, end=end, distance=workout.get("distance_m"))


def name_of(workout: dict) -> str:
    """What the import lists for a workout: 'Apple Health › 2024-05-01 06:30 Running' (UTC)."""
    import datetime as _dt
    t = _dt.datetime.fromtimestamp(float(workout["start"]), _dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    a = workout.get("type") or ""
    a = a[len(_PREFIX):] if a.startswith(_PREFIX) else a
    return f"Apple Health › {t} UTC {a or 'Workout'}"[:300]
