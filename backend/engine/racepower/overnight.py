"""
Overnight stops in a recorded multi-day track (SP-534; research SP-522).

A GPX / FIT the athlete (or a friend) actually walked has time stamps: where the track stays put
for hours — or the recording stops and starts again hours later — is a night. The day ends are the
arrivals at those places; the trip's days are one more than the nights.

    stop  = the track stays within RADIUS_M of the point it arrived at for ≥ MIN_STOP_H, or two
            consecutive points are ≥ MIN_STOP_H apart in time (the watch was off overnight)
    cut   = the along-track km of the arrival: the first point within ARRIVE_M of where the hours
            were spent (the night's drift, GPS wobble at camp, goes to the next day)

Both thresholds are our own (推估): SP-522 tried ≥ 6 h on 4 real trips (6 nights): every night
found, day km within 0.3 km, climb within 39 m, the day count right 4/4 — a long lunch or a summit
rest is 1–2 h. A route drawn in a planner has no times (or evenly spaced fake ones): nothing is
found and the caller falls back to the other ways (candidates, equal km).

The result is a proposal: the pages ask before applying it (SP-522 Q1, owner 2026-10-14).
Pure functions on a gpx.Track; numpy only.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from backend.engine.racepower import course as CO

MIN_STOP_H = 6.0          # a stop this long is a night (推估, SP-522)
RADIUS_M = 300.0          # "stays put": within this of the arrival point (camp, hut and its water source)
ARRIVE_M = 100.0          # … the arrival: the first point this close to where the hours were spent
THIN_S = 60.0             # one point a minute is enough to see hours
EDGE_KM = 0.2             # a stop at the very start / end splits nothing (waiting at the trailhead)
CHUNK = 512


def _thin(t: np.ndarray) -> np.ndarray:
    """Indices keeping one point per THIN_S (and every point after a gap)."""
    keep = [0]
    for i in range(1, len(t)):
        if t[i] - t[keep[-1]] >= THIN_S:
            keep.append(i)
    if keep[-1] != len(t) - 1:
        keep.append(len(t) - 1)
    return np.array(keep)


def _stay_end(lat, lon, i: int) -> int:
    """The last index j ≥ i such that every point i..j is within RADIUS_M of point i."""
    n = len(lat)
    j = i + 1
    while j < n:
        k = min(n, j + CHUNK)
        dd = CO.haversine(lat[i], lon[i], lat[j:k], lon[j:k])
        out = np.flatnonzero(dd > RADIUS_M)
        if len(out):
            return j + int(out[0]) - 1
        j = k
    return n - 1


def _arrival(lat, lon, t, i: int, j: int) -> tuple[int, int]:
    """(arrival, departure) inside the stay i..j: the first and last points within ARRIVE_M of where
    the time was spent (the time-weighted mean position) — the window opens up to RADIUS_M before
    the camp, the arrival is at the camp."""
    w = np.diff(t[i:j + 1], append=t[j])
    if w.sum() <= 0:
        return i, j
    clat = float(np.average(lat[i:j + 1], weights=w))
    clon = float(np.average(lon[i:j + 1], weights=w))
    near = np.flatnonzero(CO.haversine(lat[i:j + 1], lon[i:j + 1], clat, clon) <= ARRIVE_M)
    if not len(near):
        return i, j
    return i + int(near[0]), i + int(near[-1])


def find(track, min_h: float = MIN_STOP_H) -> Optional[dict]:
    """{"stops": [{night, km, hours, kind: stay | gap}], "splits_km", "days", "min_stop_h", "radius_m"}
    of a timed track (stops = nights, oldest first; none found → stops [], days 1); None when the
    track has no time stamps."""
    if not getattr(track, "time", None) or all(x is None for x in track.time):
        return None
    td = CO.track_distance(track)
    t, d = td["t"], td["d"]
    if t is None or len(t) < 3 or not np.all(np.isfinite(t)):
        return None
    lat = np.asarray(track.lat, float)[td["keep"]]
    lon = np.asarray(track.lon, float)[td["keep"]]
    k = _thin(t)
    t, d, lat, lon = t[k], d[k], lat[k], lon[k]
    total_km = float(d[-1]) / 1000.0
    need = min_h * 3600.0
    stops = []
    i = 0
    while i < len(t) - 1:
        if t[i + 1] - t[i] >= need:                      # the recording stops and starts again
            stops.append({"i": i, "km": float(d[i]) / 1000.0, "hours": float(t[i + 1] - t[i]) / 3600.0, "kind": "gap"})
            i += 1
            continue
        j = _stay_end(lat, lon, i)
        if t[j] - t[i] >= need:
            a, b = _arrival(lat, lon, t, i, j)
            if b + 1 < len(t) and t[b + 1] - t[b] >= need:   # the watch off right after: the same night
                b += 1
            off = b > a and float(np.max(np.diff(t[a:b + 1]))) >= need     # the watch was off at camp
            stops.append({"i": a, "km": float(d[a]) / 1000.0, "hours": float(t[b] - t[a]) / 3600.0,
                          "kind": "gap" if off else "stay"})
            i = max(j, b) + 1
            continue
        i += 1
    stops = [s for s in stops if EDGE_KM < s["km"] < total_km - EDGE_KM]
    out = []
    for n, s in enumerate(stops, 1):
        out.append({"night": n, "km": round(s["km"], 2), "hours": round(s["hours"], 1), "kind": s["kind"]})
    return {"stops": out, "splits_km": [s["km"] for s in out], "days": len(out) + 1,
            "min_stop_h": min_h, "radius_m": RADIUS_M}
