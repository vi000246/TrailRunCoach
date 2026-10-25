"""
TCX / GPX -> FIT (SP-349): an activity exported as a track file becomes a FIT file, so it goes the
way every other activity goes — the hash-named object (files/fitstore.py), the chart Dataset, the
活動列表 and the de-duplication (sync/dedup.py) only know FIT.

Where these come from: Garmin's 「Export Your Data」 keeps each upload in its original format (FIT,
TCX or GPX, inside DI_CONNECT/DI-Connect-Uploaded-Files/*.zip), TrainingPeaks exports a workout
file in the format it was uploaded in, Strava's bulk export is GPX, Polar Flow exports one session
as TCX / GPX, COROS's bulk export as FIT or TCX.

What is kept: per point the time, position, altitude, distance, heart rate, cadence, speed and
power (TCX: Trackpoint with the ActivityExtension TPX Speed / Watts / RunCadence; GPX: trkpt with
Garmin's TrackPointExtension hr / cad and a `power` element), and the sport (TCX Activity/@Sport,
GPX trk/type, words or Strava's numbers). Missing distance is summed from the positions (great
circle), missing speed is taken from the distance. A file without timestamps (a GPX route / course)
is not an activity. The FIT written is a function of the input only (same file -> same bytes ->
the same hash), file_id manufacturer "development" (255): never taken for a device's original.

SP-833 adds two more:
  * TrainingPeaks .pwx (PWX 1.0, what WKO and TrainingPeaks write): workout/sportType, workout/time
    (the start) and per `sample` the timeoffset (s), hr, spd (m/s), pwr, cad, dist (m), lat, lon,
    alt (m). A start without a zone is local time — the athlete's zone (`local_tz`), as GoldenCheetah
    reads it (推估: no real file at hand).
  * Polar's account download (「下載你的資料」): one training-session-*.json per session, its
    exercises' `samples` (heartRate, speed km/h, cadence, altitude, distance — {dateTime, value}) and
    `recordedRoute` ({dateTime, latitude, longitude, altitude}); times are local with the exercise's
    `timezoneOffset` (minutes; without one, the athlete's zone). Several exercises (a multisport
    session) are one activity in the sport of the first. Read from the public descriptions of
    exportVersion 1.x (推估: no real file at hand).
Apple Health's export goes through files/apple_health.py, which writes its FIT with fit_of().

Untrusted input: a DOCTYPE / ENTITY declaration is refused before parsing (no entity expansion,
no external entities), the caller caps the size (sync/upload.py MAX_TRACK_BYTES), and at most
MAX_POINTS points are read.

    to_fit(data, "tcx" | "gpx" | "pwx" | "polar", local_tz=None) -> FIT bytes   TrackError otherwise
    fit_of(columns, sport, start=, end=, distance=) -> FIT bytes    (the points already read)
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone, tzinfo
from typing import Optional
from xml.etree import ElementTree as ET

import numpy as np

from backend.demo.fitwrite import (ENUM, SINT32, UINT8, UINT16, UINT32, _data, _definition, _ts, _u,
                                   _wrap)

KINDS = ("tcx", "gpx", "pwx", "polar")
XML_KINDS = ("tcx", "gpx", "pwx")
POLAR_NAME = re.compile(r"(^|/)training-session-[^/]*\.json(\.gz)?$")   # Polar's account download
MAX_POINTS = 400_000                 # ≈ 4.6 days at 1 s: more than any activity
DEVELOPMENT = 255                    # FIT manufacturer: development (not a device's own file)
# FIT sport / sub_sport
RUNNING, CYCLING, SWIMMING, WALKING, HIKING, GENERIC = 1, 2, 5, 11, 17, 0
SUB_GENERIC, SUB_TRAIL = 0, 3
_SPORT_WORDS = (("trail", (RUNNING, SUB_TRAIL)), ("run", (RUNNING, SUB_GENERIC)),
                ("bik", (CYCLING, SUB_GENERIC)), ("cycl", (CYCLING, SUB_GENERIC)), ("ride", (CYCLING, SUB_GENERIC)),
                ("swim", (SWIMMING, SUB_GENERIC)), ("hik", (HIKING, SUB_GENERIC)), ("walk", (WALKING, SUB_GENERIC)))
# Strava's GPX <type> numbers (its bulk export): 1 ride, 9 run, 10 hike, 11 walk
_STRAVA_TYPES = {"1": (CYCLING, SUB_GENERIC), "9": (RUNNING, SUB_GENERIC), "10": (HIKING, SUB_GENERIC),
                 "11": (WALKING, SUB_GENERIC)}
ASCENT_STEP_M = 3.0                  # total ascent: climbs counted past this hysteresis (GPS / baro jitter)

NOT_TRACK, DECLARATION, NO_TIME = "unreadable", "unreadable", "not_activity"


class TrackError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def kind_of(name: str) -> Optional[str]:
    """"tcx" / "gpx" / "pwx" for a track file name (also gzipped), "polar" for a Polar
    training-session JSON, else None."""
    n = (name or "").strip().lower().replace("\\", "/")
    if POLAR_NAME.search(n):
        return "polar"
    if n.endswith(".gz"):
        n = n[:-3]
    for k in XML_KINDS:
        if n.endswith("." + k):
            return k
    return None


# ---------------------------------------------------------------------------- reading

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, name: str):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, *path: str) -> Optional[str]:
    for p in path:
        if el is None:
            return None
        el = _child(el, p)
    return el.text.strip() if el is not None and el.text else None


def _float(s) -> float:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return math.nan
    return v if math.isfinite(v) else math.nan


_TIME = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:?\d\d)?$")


def _time(s: Optional[str], naive: Optional[tzinfo] = None, offset_min: Optional[float] = None) -> Optional[float]:
    """ISO 8601 -> UTC epoch seconds. No zone: `offset_min` minutes east of UTC when given, else
    the zone `naive` (local time), else UTC (as GPX and TCX specify)."""
    if not s or not isinstance(s, str):
        return None
    m = _TIME.match(s.strip())
    if not m:
        return None
    base, frac, zone = m.groups()
    try:
        t = datetime.fromisoformat(base)
    except ValueError:
        return None
    extra = float(frac) if frac else 0.0
    if zone and zone != "Z":
        z = zone.replace(":", "")
        sign = 1 if z[0] == "+" else -1
        off = sign * (int(z[1:3]) * 3600 + int(z[3:5]) * 60)
    elif zone == "Z":
        off = 0
    elif offset_min is not None:
        off = int(round(offset_min * 60))
    elif naive is not None:
        try:
            return t.replace(tzinfo=naive).timestamp() + extra
        except (OverflowError, ValueError):
            return None
    else:
        off = 0
    return t.replace(tzinfo=timezone.utc).timestamp() - off + extra


def _sport(word: Optional[str]) -> tuple:
    w = (word or "").strip().lower()
    if not w:
        return GENERIC, SUB_GENERIC
    if w in _STRAVA_TYPES:
        return _STRAVA_TYPES[w]
    for key, sp in _SPORT_WORDS:
        if key in w:
            return sp
    return GENERIC, SUB_GENERIC


def _ext_value(el, *names: str) -> float:
    """The first descendant of `el` named one of `names` (an extension's value, any namespace)."""
    if el is None:
        return math.nan
    for d in el.iter():
        if _local(d.tag) in names and d.text and d.text.strip():
            return _float(d.text)
    return math.nan


def _points_tcx(root) -> tuple[list, tuple]:
    acts = _child(root, "Activities")
    act = _child(acts, "Activity") if acts is not None else None
    if act is None:
        return [], (GENERIC, SUB_GENERIC)
    sport = _sport(act.get("Sport"))
    pts = []
    for tp in act.iter():
        if _local(tp.tag) != "Trackpoint":
            continue
        t = _time(_text(tp, "Time"))
        if t is None:
            continue
        pos = _child(tp, "Position")
        ext = _child(tp, "Extensions")
        cad = _float(_text(tp, "Cadence"))
        if math.isnan(cad):
            cad = _ext_value(ext, "RunCadence")
        pts.append((t, _float(_text(pos, "LatitudeDegrees")), _float(_text(pos, "LongitudeDegrees")),
                    _float(_text(tp, "AltitudeMeters")), _float(_text(tp, "DistanceMeters")),
                    _float(_text(tp, "HeartRateBpm", "Value")), cad, _ext_value(ext, "Speed"),
                    _ext_value(ext, "Watts")))
        if len(pts) > MAX_POINTS:
            break
    return pts, sport


def _points_gpx(root) -> tuple[list, tuple]:
    trk = _child(root, "trk")
    if trk is None:
        return [], (GENERIC, SUB_GENERIC)
    sport = _sport(_text(trk, "type"))
    pts = []
    for seg in trk:
        if _local(seg.tag) != "trkseg":
            continue
        for p in seg:
            if _local(p.tag) != "trkpt":
                continue
            t = _time(_text(p, "time"))
            if t is None:
                continue
            ext = _child(p, "extensions")
            pts.append((t, _float(p.get("lat")), _float(p.get("lon")), _float(_text(p, "ele")), math.nan,
                        _ext_value(ext, "hr", "heartrate"), _ext_value(ext, "cad", "cadence"),
                        _ext_value(ext, "speed"), _ext_value(ext, "power", "PowerInWatts")))
            if len(pts) > MAX_POINTS:
                break
    return pts, sport


def _points_pwx(root, local_tz: Optional[tzinfo]) -> tuple[list, tuple]:
    wo = _child(root, "workout")
    if wo is None:
        return [], (GENERIC, SUB_GENERIC)
    sport = _sport(_text(wo, "sportType"))
    t0 = _time(_text(wo, "time"), naive=local_tz)
    if t0 is None:
        return [], sport
    pts = []
    for s in wo:
        if _local(s.tag) != "sample":
            continue
        off = _float(_text(s, "timeoffset"))
        if math.isnan(off) or off < 0:
            continue
        pts.append((t0 + off, _float(_text(s, "lat")), _float(_text(s, "lon")), _float(_text(s, "alt")),
                    _float(_text(s, "dist")), _float(_text(s, "hr")), _float(_text(s, "cad")),
                    _float(_text(s, "spd")), _float(_text(s, "pwr"))))
        if len(pts) > MAX_POINTS:
            break
    return pts, sport


# Polar's samples: key -> (column, scale) — speed in km/h
_POLAR_SAMPLES = {"altitude": (3, 1.0), "distance": (4, 1.0), "heartRate": (5, 1.0), "cadence": (6, 1.0),
                  "speed": (7, 1 / 3.6), "power": (8, 1.0)}


def _points_polar(obj, local_tz: Optional[tzinfo]) -> tuple[list, tuple]:
    exs = obj.get("exercises") if isinstance(obj, dict) else None
    if not isinstance(exs, list) or not exs:
        return [], (GENERIC, SUB_GENERIC)
    first = exs[0] if isinstance(exs[0], dict) else {}
    sport = _sport(first.get("sport") or obj.get("sport"))
    rows: dict = {}                                       # time -> the 9 columns

    def row(t: float) -> list:
        r = rows.get(t)
        if r is None:
            if len(rows) > MAX_POINTS:
                raise _Full()
            r = rows[t] = [t] + [math.nan] * 8
        return r

    try:
        for ex in exs:
            if not isinstance(ex, dict):
                continue
            off = ex.get("timezoneOffset", ex.get("timeZoneOffset", obj.get("timeZoneOffset")))
            off = off if isinstance(off, (int, float)) and not isinstance(off, bool) else None
            samples = ex.get("samples") if isinstance(ex.get("samples"), dict) else {}
            for key, (col, scale) in _POLAR_SAMPLES.items():
                for s in samples.get(key) or ():
                    if not isinstance(s, dict):
                        continue
                    t = _time(s.get("dateTime"), naive=local_tz, offset_min=off)
                    v = _float(s.get("value"))
                    if t is not None and not math.isnan(v):
                        row(t)[col] = v * scale
            route = samples.get("recordedRoute") or ex.get("recordedRoute") or ()
            for p in route if isinstance(route, list) else ():
                if not isinstance(p, dict):
                    continue
                t = _time(p.get("dateTime"), naive=local_tz, offset_min=off)
                if t is None:
                    continue
                r = row(t)
                r[1], r[2] = _float(p.get("latitude")), _float(p.get("longitude"))
                if math.isnan(r[3]):
                    r[3] = _float(p.get("altitude"))
    except _Full:
        pass
    return [tuple(r) for r in rows.values()], sport


class _Full(Exception):
    pass


def _haversine(lat1, lon1, lat2, lon2) -> np.ndarray:
    r = 6371008.8
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _ascent(alt: np.ndarray) -> Optional[float]:
    a = alt[np.isfinite(alt)]
    if len(a) < 2:
        return None
    total, low = 0.0, a[0]
    high = a[0]
    for v in a[1:]:
        if v > high:
            high = v
        if v < low or high - v >= ASCENT_STEP_M:
            if high - low >= ASCENT_STEP_M:
                total += high - low
            low = high = v
    if high - low >= ASCENT_STEP_M:
        total += high - low
    return total


_ROOTS = {"tcx": "TrainingCenterDatabase", "gpx": "gpx", "pwx": "pwx"}


def parse(data: bytes, kind: str, local_tz: Optional[tzinfo] = None):
    """(points as columns: t, lat, lon, alt, dist, hr, cad, speed, power — numpy arrays sorted by
    time, one point per second at most), (sport, sub_sport). TrackError otherwise. `local_tz`:
    the zone of a time without one in a PWX / Polar file (the athlete's)."""
    if kind not in KINDS:
        raise TrackError(NOT_TRACK)
    if kind == "polar":
        if data.lstrip()[:1] != b"{":
            raise TrackError(NOT_TRACK)
        try:
            obj = json.loads(data)
        except (ValueError, RecursionError):
            raise TrackError(NOT_TRACK)
        pts, sport = _points_polar(obj, local_tz)
        return columns(pts, sport)
    head = data.lstrip()[:5]
    if head[:1] != b"<":
        raise TrackError(NOT_TRACK)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise TrackError(DECLARATION)            # never expand entities of an uploaded file
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise TrackError(NOT_TRACK)
    if _local(root.tag) != _ROOTS[kind]:
        raise TrackError(NOT_TRACK)
    if kind == "pwx":
        pts, sport = _points_pwx(root, local_tz)
    else:
        pts, sport = (_points_tcx if kind == "tcx" else _points_gpx)(root)
    return columns(pts, sport)


def columns(pts: list, sport: tuple):
    """Points (t, lat, lon, alt, dist, hr, cad, speed, power tuples, any order) -> parse()'s
    answer: sorted, one per whole second, distance filled from the positions and never
    decreasing, speed from the distance when there is none. TrackError(not_activity) for fewer
    than two timed points."""
    if len(pts) < 2:
        raise TrackError(NO_TIME)                # a route / course, or an empty track
    a = np.array(pts, dtype=float)
    a = a[np.argsort(a[:, 0], kind="stable")]
    sec = np.floor(a[:, 0])
    keep = np.concatenate([[True], np.diff(sec) > 0])   # FIT timestamps are whole seconds
    a = a[keep]
    a[:, 0] = np.floor(a[:, 0])
    if len(a) < 2:
        raise TrackError(NO_TIME)
    t, lat, lon, alt, dist, hr, cad, spd, pw = (a[:, i] for i in range(9))
    has_pos = np.isfinite(lat) & np.isfinite(lon)
    if not np.isfinite(dist).any() and has_pos.sum() >= 2:
        la, lo = lat[has_pos], lon[has_pos]
        step = np.concatenate([[0.0], _haversine(la[:-1], lo[:-1], la[1:], lo[1:])])
        d = np.full(len(t), np.nan)
        d[has_pos] = np.cumsum(step)
        dist = d
    if np.isfinite(dist).any():
        # forward fill, never decreasing
        idx = np.where(np.isfinite(dist), np.arange(len(dist)), 0)
        np.maximum.accumulate(idx, out=idx)
        filled = dist[idx]
        filled[~np.isfinite(filled)] = 0.0
        dist = np.maximum.accumulate(filled)
    if not np.isfinite(spd).any() and np.isfinite(dist).any():
        dt = np.diff(t)
        v = np.diff(dist) / np.where(dt > 0, dt, 1.0)
        spd = np.concatenate([[v[0] if len(v) else 0.0], v])
    return (t, lat, lon, alt, dist, hr, cad, spd, pw), sport


# ---------------------------------------------------------------------------- writing

_INVALID_S32 = 0x7FFFFFFF


def _semi(deg: np.ndarray) -> np.ndarray:
    v = np.asarray(deg, dtype=float) * (2 ** 31 / 180.0)
    out = np.where(np.isfinite(v), np.clip(np.rint(v), -(2 ** 31) + 1, 2 ** 31 - 2), _INVALID_S32)
    return out.astype(np.int64)


def to_fit(data: bytes, kind: str, local_tz: Optional[tzinfo] = None) -> bytes:
    """The FIT of a TCX / GPX / PWX / Polar JSON activity; TrackError(reason) when it is none."""
    cols, sport = parse(data, kind, local_tz)
    return fit_of(cols, sport)


def fit_of(cols, sport: tuple, start: Optional[float] = None, end: Optional[float] = None,
           distance: Optional[float] = None) -> bytes:
    """parse()'s columns as a FIT. `start` / `end` (UTC epoch s) and `distance` (m): the session's
    own, when the source knows them better than its points (Apple Health's workout)."""
    t, lat, lon, alt, dist, hr, cad, spd, pw = cols
    (sport, sub) = sport
    from backend.demo.fitwrite import FIT_EPOCH
    epoch = FIT_EPOCH.timestamp()
    ts = (t - epoch).astype(np.int64)
    if ts[0] <= 0:
        raise TrackError(NO_TIME)
    n = len(ts)
    t0, end_ts = int(ts[0]), int(ts[-1])
    if start is not None and 0 < int(start - epoch) <= t0:
        t0 = int(start - epoch)
    if end is not None and int(end - epoch) >= end_ts:
        end_ts = int(end - epoch)
    end = end_ts
    body = b""
    fid = [(0, ENUM), (1, UINT16), (2, UINT16), (4, UINT32)]
    body += _definition(0, 0, fid) + _data(0, fid, [4, DEVELOPMENT, 0, t0])
    ev = [(253, UINT32), (0, ENUM), (1, ENUM)]
    body += _definition(7, 21, ev) + _data(7, ev, [t0, 0, 0])
    # no distance on any point but the session's (an indoor Apple Health workout): the records carry
    # none, so a reader takes the session's total instead of 0 m
    point_dist = distance is None or np.isfinite(dist).any()
    fields = [(253, UINT32), (0, SINT32), (1, SINT32), (2, UINT16), (3, UINT8), (4, UINT8)] + (
        [(5, UINT32)] if point_dist else []) + [(6, UINT16)]
    dtype = [("h", "u1"), ("ts", "<u4"), ("lat", "<i4"), ("lon", "<i4"), ("alt", "<u2"), ("hr", "u1"),
             ("cad", "u1")] + ([("dist", "<u4")] if point_dist else []) + [("spd", "<u2")]
    power = np.isfinite(pw).any()
    if power:
        fields.append((7, UINT16))
        dtype.append(("pw", "<u2"))
    body += _definition(1, 20, fields)
    rec = np.zeros(n, dtype=np.dtype(dtype))
    rec["h"] = 1
    rec["ts"] = ts
    rec["lat"] = _semi(lat)
    rec["lon"] = _semi(lon)
    rec["alt"] = _u((alt + 500.0) * 5.0, 0, 65534, 0xFFFF, np.uint16)
    rec["hr"] = _u(hr, 1, 254, 0xFF, np.uint8)
    rec["cad"] = _u(cad, 0, 254, 0xFF, np.uint8)
    if point_dist:
        rec["dist"] = _u(dist * 100.0, 0, 2 ** 32 - 2, 2 ** 32 - 1, np.uint32)
    rec["spd"] = _u(spd * 1000.0, 0, 65534, 0xFFFF, np.uint16)
    if power:
        rec["pw"] = _u(pw, 0, 65534, 0xFFFF, np.uint16)
    body += rec.tobytes()
    body += _data(7, ev, [end, 0, 4])
    elapsed = (end - t0) * 1000
    total_d = float(dist[-1]) if np.isfinite(dist).any() else math.nan
    if distance is not None and math.isfinite(distance) and distance >= 0 and not total_d > 0:
        total_d = float(distance)
    asc = _ascent(alt)
    hr_f = hr[np.isfinite(hr)]
    ses = [(253, UINT32), (2, UINT32), (5, ENUM), (6, ENUM), (7, UINT32), (8, UINT32), (9, UINT32),
           (22, UINT16), (16, UINT8), (17, UINT8)]
    body += _definition(2, 18, ses) + _data(2, ses, [
        end, t0, sport, sub, elapsed, elapsed,
        int(round(total_d * 100)) if math.isfinite(total_d) else 0xFFFFFFFF,
        int(round(asc)) if asc is not None else 0xFFFF,
        int(round(hr_f.mean())) if len(hr_f) else 0xFF, int(round(hr_f.max())) if len(hr_f) else 0xFF])
    act = [(253, UINT32), (0, UINT32), (1, UINT16), (2, ENUM), (3, ENUM), (4, ENUM)]
    body += _definition(8, 34, act) + _data(8, act, [end, elapsed, 1, 0, 26, 1])
    return _wrap(body)
