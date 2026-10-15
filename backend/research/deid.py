"""
The de-identification rules of the research pool (SP-586; docs/research/deidentified-analytics.md
§3, §14; taken over from the prototype backend/scripts/deid_prototype.py with the user's answers to
SP-326 Q2, 2026-10-14):

  * no GPS point and no FIT ever leaves the tenant: positions only as grid cells (~500 m) walked by
    at least K_INTERNAL distinct people (`aggregate_grid`), the cells' input trimmed around the
    start, the end and the frequent places (`keep_mask`)
  * 1-km splits are NOT trimmed (Q2-1: the trimming made whole activities of a runner who loops
    near home vanish, and a split holds no position); they carry no donor id, activity id, time or
    order, only the year they were exported (Q2-2: the retention deletes whole years)
  * summaries: race distance in 5-km, climb in 500-m steps; any other activity in 1-km and 50-m
    steps; the duration in 5-minute steps (Q2-3; races too — finish times are public)
  * no 5-second channel (Q2-4)

Pure functions over in-memory tracks; tenant.py builds the tracks, pool.py writes the rows.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import math
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np

TRIM_BASE_M = 1000.0          # grid input: start / end 1 km ...
TRIM_JITTER_M = 500.0         # ... + uniform 0–500 m, drawn per activity and per end
PLACE_TRIM_M = 2000.0         # frequent places: 2 km + the same jitter
PLACE_RADIUS_M = 300.0        # endpoints within 300 m of each other ...
PLACE_MIN_COUNT = 3           # ... 3 or more ...
PLACE_WINDOW_DAYS = 90        # ... inside 90 days = a frequent place
DAY0_JITTER_DAYS = 27         # day 0 = the first exported activity's date minus uniform 0–27 days
K_INTERNAL = 5                # >= 5 distinct people per cell (SP-326 Q1-7)
K_PUBLIC = 10                 # >= 10 for what other users would see
SPLIT_M = 1000.0
CELL_LAT_DEG = 0.0045         # ~500 m in Taiwan
CELL_LON_DEG = 0.0049
RETENTION_YEARS = 3           # SP-326 Q1-12
MIN_AGE = 18                  # younger people are never exported (see pool.py)
AGE_BANDS = ("18-29", "30-39", "40-49", "50-59", "60+")
SUMMARY_DURATION_MIN = 5      # Q2-3
SUMMARY_DISTANCE_KM = 1.0     # Q2-3, not a race
SUMMARY_ASCENT_M = 50.0
RACE_DISTANCE_KM = 5.0
RACE_ASCENT_M = 500.0
FOOT_SPORTS = ("run", "trail", "hike")


@dataclass
class Track:
    """One activity's per-second channels (NaN = no data). Never written anywhere."""
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    dist: np.ndarray
    speed: np.ndarray
    ele: np.ndarray
    hr: np.ndarray
    power: np.ndarray
    cadence: np.ndarray
    temp: np.ndarray
    start_utc: dt.datetime
    sport: str = "run"
    chain: str = "manual"
    is_race: bool = False
    rpe: Optional[float] = None
    tz_offset_h: float = 8.0

    def local_start(self) -> dt.datetime:
        return self.start_utc + dt.timedelta(hours=self.tz_offset_h)


@dataclass
class Donor:
    """What the rules need of a person. Never written as is: sex and an age band only."""
    sex: str
    age: int
    lthr: float
    cp_w: Optional[float] = None
    weight_kg: Optional[float] = None


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6_371_000.0 * np.arcsin(np.sqrt(np.minimum(1.0, a)))


def _bin(x: float, step: float) -> float:
    return round(math.floor(x / step) * step, 3)


def _r(x, nd=1):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def _nanmean(a) -> float:
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    return float(a.mean()) if len(a) else float("nan")


def season_of(month: int) -> str:
    return {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
            6: "summer", 7: "summer", 8: "summer"}.get(month, "autumn")


def time_of_day(hour: float) -> str:
    if 4 <= hour < 8:
        return "dawn"
    if 8 <= hour < 17:
        return "day"
    if 17 <= hour < 20:
        return "evening"
    return "night"


def age_band(age: int) -> Optional[str]:
    """None under MIN_AGE (never exported)."""
    if age < MIN_AGE:
        return None
    if age >= 60:
        return "60+"
    lo = (age // 10) * 10
    return "18-29" if lo < 30 else f"{lo}-{lo + 9}"


def activity_key(key: bytes, user_id: int, content_id: str) -> str:
    """HMAC (the key lives in accounts.db): a plain hash of a file's sha256 or a platform id could be
    recomputed by anyone who has it."""
    return hmac.new(key, f"{int(user_id)}:{content_id}".encode(), hashlib.sha256).hexdigest()[:32]


# ---------------------------------------------------------------------------- GPS (grid input only)

def frequent_places(endpoints: Iterable[tuple[dt.date, float, float]]) -> list:
    pts = sorted(((d, la, lo) for d, la, lo in endpoints if np.isfinite(la) and np.isfinite(lo)),
                 key=lambda x: x[0])
    places: list[tuple[float, float]] = []
    for d, la, lo in pts:
        near = [(q[1], q[2]) for q in pts
                if 0 <= (q[0] - d).days < PLACE_WINDOW_DAYS and haversine_m(la, lo, q[1], q[2]) <= PLACE_RADIUS_M]
        if len(near) < PLACE_MIN_COUNT:
            continue
        c = (float(np.mean([p[0] for p in near])), float(np.mean([p[1] for p in near])))
        if all(haversine_m(c[0], c[1], p[0], p[1]) > PLACE_RADIUS_M for p in places):
            places.append(c)
    return places


def first_last_fix(tr: Track):
    ok = np.flatnonzero(np.isfinite(tr.lat) & np.isfinite(tr.lon))
    if not len(ok):
        return None, None
    i, j = ok[0], ok[-1]
    return (float(tr.lat[i]), float(tr.lon[i])), (float(tr.lat[j]), float(tr.lon[j]))


def trim_radii(rng: np.random.Generator) -> dict:
    return {"start": TRIM_BASE_M + float(rng.uniform(0, TRIM_JITTER_M)),
            "end": TRIM_BASE_M + float(rng.uniform(0, TRIM_JITTER_M)),
            "place": PLACE_TRIM_M + float(rng.uniform(0, TRIM_JITTER_M))}


def keep_mask(tr: Track, places: list, radii: dict) -> np.ndarray:
    """True where a sample may feed a grid cell: outside the start, end and frequent-place circles."""
    has = np.isfinite(tr.lat) & np.isfinite(tr.lon)
    if not has.any():
        return has
    first, last = first_last_fix(tr)
    keep = has.copy()
    keep &= haversine_m(first[0], first[1], tr.lat, tr.lon) > radii["start"]
    keep &= haversine_m(last[0], last[1], tr.lat, tr.lon) > radii["end"]
    for la, lo in places:
        keep &= haversine_m(la, lo, tr.lat, tr.lon) > radii["place"]
    return keep & has


def cells_of(tr: Track, keep: np.ndarray) -> dict:
    """{cell: [speeds]} of the kept samples — in memory only, a person's cells are never stored."""
    out: dict = {}
    for i in np.flatnonzero(keep):
        c = (int(math.floor(tr.lat[i] / CELL_LAT_DEG)), int(math.floor(tr.lon[i] / CELL_LON_DEG)))
        out.setdefault(c, []).append(float(tr.speed[i]))
    return out


def aggregate_grid(cells_per_person: list[dict], k: int = K_INTERNAL) -> tuple[list[dict], list[dict]]:
    """(cells walked by >= k distinct people: n_people, n_samples, median speed; the cells held back,
    with their head count only — for the report, never written to the pool)."""
    people: dict = {}
    speeds: dict = {}
    for cells in cells_per_person:
        for c, v in cells.items():
            people[c] = people.get(c, 0) + 1
            speeds.setdefault(c, []).extend(v)
    out, held = [], []
    for c, n in sorted(people.items()):
        if n >= k:
            out.append({"cell_lat": c[0], "cell_lon": c[1], "n_people": n, "n_samples": len(speeds[c]),
                        "median_speed_ms": round(float(np.median(speeds[c])), 2)})
        else:
            held.append({"n_people": n})
    return out, held


# ---------------------------------------------------------------------------- rows

def splits(tr: Track, donor: Donor, export_year: int) -> list[dict]:
    """1-km splits of the whole activity (not trimmed, Q2-1). No donor id, activity id, time or
    position; the export year for the retention (Q2-2)."""
    band = age_band(donor.age)
    total_t = float(tr.t[-1]) if len(tr.t) else 0.0
    out = []
    i, b = 0, len(tr.t)
    while i < b:
        j = int(np.searchsorted(tr.dist[i:b], tr.dist[i] + SPLIT_M)) + i
        if j >= b:
            break
        sl = slice(i, j + 1)
        d, e, t = tr.dist[sl], tr.ele[sl], tr.t[sl]
        dt_s = float(t[-1] - t[0])
        if dt_s <= 0:
            i = j
            continue
        grid = np.arange(d[0], d[-1] + 1e-6, 100.0)
        eg = np.interp(grid, d, e) if len(grid) >= 3 and np.isfinite(e).all() else None
        g_sd = float(np.std(np.diff(eg) / 100.0)) if eg is not None else float("nan")
        p = _nanmean(tr.power[sl])
        ele_mean = _nanmean(e)
        out.append({
            "export_year": int(export_year),
            "grade": _r((e[-1] - e[0]) / max(1.0, d[-1] - d[0]), 3),
            "grade_sd": _r(g_sd, 3),
            "ele_band_m": int(_bin(ele_mean, 100.0)) if np.isfinite(ele_mean) else None,
            "speed_ms": _r(float(d[-1] - d[0]) / dt_s, 2),
            "pct_lthr": _r(100.0 * _nanmean(tr.hr[sl]) / donor.lthr, 1),
            "w_per_kg": _r(p / donor.weight_kg, 2) if donor.weight_kg and np.isfinite(p) else None,
            "cadence": _r(_nanmean(tr.cadence[sl]), 0),
            "temp_c": _r(_nanmean(tr.temp[sl]), 0),
            "decile": min(9, int(10 * (0.5 * (t[0] + t[-1])) / total_t)) if total_t > 0 else None,
            "sex": donor.sex,
            "age_band": band,
        })
        i = j
    return out


def _np_power(power: np.ndarray) -> float:
    p = np.nan_to_num(np.asarray(power, dtype=float), nan=0.0)
    if len(p) < 30:
        return float("nan")
    roll = np.convolve(p, np.ones(30) / 30, mode="valid")
    return float(np.mean(roll ** 4) ** 0.25)


def tss(tr: Track, donor: Donor) -> float:
    hours = float(tr.t[-1]) / 3600 if len(tr.t) else 0.0
    if donor.cp_w and np.isfinite(tr.power).sum() > 0.5 * len(tr.t):
        ifac = _np_power(tr.power) / donor.cp_w
        if np.isfinite(ifac):
            return hours * ifac ** 2 * 100
    r = _nanmean(tr.hr) / donor.lthr
    return hours * r ** 2 * 100 if np.isfinite(r) else 0.0


def drift_pct(tr: Track) -> Optional[float]:
    if not len(tr.t) or tr.t[-1] < 20 * 60:
        return None
    h = np.searchsorted(tr.t, tr.t[-1] / 2)
    ef = [_nanmean(tr.speed[s]) / _nanmean(tr.hr[s]) for s in (slice(0, h), slice(h, None))]
    if not all(np.isfinite(x) and x > 0 for x in ef):
        return None
    return round((ef[0] / ef[1] - 1) * 100, 1)


def ascent_m(tr: Track) -> float:
    up = np.diff(tr.ele[np.isfinite(tr.ele)])
    return float(up[up > 0].sum()) if len(up) else 0.0


def summary(tr: Track, donor: Donor, donor_id: str, rel_day: int, act_key: str) -> dict:
    """One B-layer summary (§3.6 table 2, coarsened per Q2-3)."""
    loc = tr.local_start()
    dist_km = float(np.nanmax(tr.dist)) / 1000 if len(tr.dist) else 0.0
    p = _nanmean(tr.power)
    minutes = float(tr.t[-1]) / 60 if len(tr.t) else 0.0
    return {
        "donor_id": donor_id,
        "activity_key": act_key,
        "rel_day": int(rel_day),
        "season": season_of(loc.month),
        "time_of_day": time_of_day(loc.hour + loc.minute / 60),
        "sport": tr.sport,
        "distance_km": _bin(dist_km, RACE_DISTANCE_KM if tr.is_race else SUMMARY_DISTANCE_KM),
        "ascent_m": int(_bin(ascent_m(tr), RACE_ASCENT_M if tr.is_race else SUMMARY_ASCENT_M)),
        "duration_min": int(_bin(minutes, SUMMARY_DURATION_MIN)),
        "pct_lthr": _r(100.0 * _nanmean(tr.hr) / donor.lthr, 1),
        "pct_cp": _r(100.0 * p / donor.cp_w, 1) if donor.cp_w and np.isfinite(p) else None,
        "drift_pct": drift_pct(tr),
        "tss": _r(tss(tr, donor), 0),
        "rpe": tr.rpe,
        "is_race": int(bool(tr.is_race)),
        "source": tr.chain,
    }


def daily_load(donor_id: str, per_day: dict, injury_days: set) -> list[dict]:
    """Every day from the first active day to the last (rest days 0); injury_off: a break of >= 7 days."""
    if not per_day:
        return []
    rows = []
    for d in range(min(per_day), max(per_day) + 1):
        x = per_day.get(d, {"tss": 0.0, "min": 0.0, "asc": 0.0})
        rows.append({"donor_id": donor_id, "rel_day": d, "tss": int(round(x["tss"])), "duration_min": int(x["min"]),
                     "ascent_band_m": int(_bin(x["asc"], 100.0)), "injury_off": int(d in injury_days)})
    return rows


def person_params(tracks: list[Track], donor: Donor) -> Optional[dict]:
    """A layer: one row per person (sex, age band, numbers), aggregated across people and dropped.
    The prototype's three parameters; the research doc §14.5 item 5 would take engine/calibrate.py's
    fitted ones instead (a later step)."""
    band = age_band(donor.age)
    if not tracks or band is None:
        return None
    sp = [s for tr in tracks for s in splits(tr, donor, 0)]
    flat = [s["speed_ms"] for s in sp if s["grade"] is not None and abs(s["grade"]) < 0.02
            and s["pct_lthr"] is not None and s["pct_lthr"] < 85]
    steep = [s["speed_ms"] * s["grade"] * 3600 for s in sp if s["grade"] is not None and s["grade"] > 0.08]
    drifts = [d for d in (drift_pct(tr) for tr in tracks if len(tr.t) and tr.t[-1] >= 3600) if d is not None]

    def med(xs):
        return round(float(np.median(xs)), 2) if xs else None
    return {"sex": donor.sex, "age_band": band, "easy_flat_speed_ms": med(flat),
            "uphill_vam_mh": med(steep), "drift_pct": med(drifts)}
