"""
De-identification prototype for the cross-user research pool (SP-326,
docs/research/deidentified-analytics.md §3, §14). A PROTOTYPE: nothing in the
app imports it, it writes no pool table, it reads no tenant data. It turns
activities (the demo athlete's synthetic FITs, or hand-made tracks in the
tests) into the rows the pool would hold, so the rules can be tested and the
storage measured before an implementation ticket.

Layers (the user's answers, 2026-10-13):
  A  per-person parameters, computed in the tenant; leave it only as
     aggregates over >= K_INTERNAL people (>= K_PUBLIC for what other users
     see). Opt-out (privacy policy + settings switch).
  B  activity summaries, 1-km splits, daily load. Separate opt-in tick box,
     never pre-ticked. Splits carry NO donor id, NO activity id, NO order.

What never leaves the tenant: GPS coordinates (only k-gated grid cells),
absolute dates and weekdays, account links (email, Google sub, COROS / TP
ids, file names, platform activity ids), free text, body weight.

    python -m backend.scripts.deid_prototype [--weeks 52] [--json out.json]

prints the storage estimate (§9.1) measured on the demo athlete.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import io
import json
import math
import secrets
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

# ---------------------------------------------------------------------------
# rules (docs/research/deidentified-analytics.md §3.2, §3.3, §3.5, §10)
# ---------------------------------------------------------------------------

TRIM_BASE_M = 1000.0          # start / end: 1 km ...
TRIM_JITTER_M = 500.0         # ... + uniform 0–500 m, drawn per activity (and per end)
PLACE_TRIM_M = 2000.0         # frequent places (home, work, the usual trailhead): 2 km + the same jitter
PLACE_RADIUS_M = 300.0        # endpoints within 300 m of each other ...
PLACE_MIN_COUNT = 3           # ... 3 or more ...
PLACE_WINDOW_DAYS = 90        # ... inside 90 days = a frequent place
DAY0_JITTER_DAYS = 27         # day 0 = the first activity's date minus uniform 0–27 days
K_INTERNAL = 5                # internal analysis: >= 5 distinct people per cell
K_PUBLIC = 10                 # shown to other users: >= 10
SPLIT_M = 1000.0              # 1-km splits (the user's choice; no 5-minute splits)
CELL_LAT_DEG = 0.0045         # ~500 m grid cells in Taiwan (1° lat ≈ 111 km; lon at ~24°N ≈ 102 km)
CELL_LON_DEG = 0.0049
RETENTION_YEARS = 3
AGE_BANDS = ("18-29", "30-39", "40-49", "50-59", "60+")
GARMIN_ATTRIBUTION = "Insights derived in part from Garmin device-sourced data."


@dataclass(frozen=True)
class SourceRule:
    a: bool                   # may feed A-layer aggregates
    b: bool                   # may feed B-layer detail
    owner_only: bool = False  # only the owner's own data (he is the data subject)
    attribution: Optional[str] = None


# §8, §11 Q10. NOT DECIDED by the user yet (2026-10-13): these are the
# document's recommendations, kept as one replaceable table. Unknown sources
# are refused (deny by default).
SOURCE_RULES_PENDING = True
DEFAULT_SOURCE_RULES: dict[str, SourceRule] = {
    "manual": SourceRule(True, True),                     # FIT the user exported and uploaded himself
    "coros-partner": SourceRule(True, True),              # COROS Partner API §7.4: internal use only
    "coros-mcp": SourceRule(True, False),                 # A only until COROS answers in writing
    "coros-unofficial": SourceRule(True, True, owner_only=True),
    "intervals": SourceRule(True, True),
    "intervals:COROS": SourceRule(True, True),
    "intervals:GARMIN_CONNECT": SourceRule(True, True, attribution=GARMIN_ATTRIBUTION),
    "intervals:STRAVA": SourceRule(False, False),
    "trainingpeaks": SourceRule(False, False),
    "strava": SourceRule(False, False),
    "strava-export": SourceRule(False, False),
}


class SourcePolicy:
    """Which source may feed which layer. `rules` replaces the defaults whole."""

    def __init__(self, rules: Optional[dict[str, SourceRule]] = None):
        self.rules = dict(DEFAULT_SOURCE_RULES if rules is None else rules)

    def rule(self, source: str) -> Optional[SourceRule]:
        if source in self.rules:
            return self.rules[source]
        if source.startswith("intervals:") and "intervals" in self.rules:
            return self.rules["intervals"]
        return None

    def allows(self, source: str, layer: str, *, owner: bool = False) -> bool:
        r = self.rule(source)
        if r is None or (r.owner_only and not owner):
            return False
        return r.a if layer == "A" else r.b

    def attribution(self, source: str) -> Optional[str]:
        r = self.rule(source)
        return r.attribution if r else None


# ---------------------------------------------------------------------------
# inputs: what the tenant has (never exported as is)
# ---------------------------------------------------------------------------

@dataclass
class Track:
    """One activity as the per-second cache holds it (NaN = no data)."""
    t: np.ndarray                 # s since start
    lat: np.ndarray               # deg
    lon: np.ndarray
    dist: np.ndarray              # m
    speed: np.ndarray             # m/s
    ele: np.ndarray               # m
    hr: np.ndarray
    power: np.ndarray
    cadence: np.ndarray
    temp: np.ndarray
    start_utc: dt.datetime
    sport: str = "run"            # run | trail | hike
    source: str = "manual"
    is_race: bool = False
    rpe: Optional[float] = None
    # what ties the activity to an account (file name, platform ids, ...): read only for the
    # keyed dedup key, never copied into a row
    account: dict = field(default_factory=dict)

    def local_start(self, tz_offset_h: float) -> dt.datetime:
        return self.start_utc + dt.timedelta(hours=tz_offset_h)


@dataclass
class Donor:
    user_id: str
    sex: str                      # male | female
    age: int
    lthr: float
    cp_w: Optional[float] = None
    weight_kg: Optional[float] = None    # only to turn W into W/kg, never exported
    tz_offset_h: float = 8.0
    consent_a: bool = True        # A: informed, opt-out
    consent_b: bool = False       # B: separate tick box, never pre-ticked
    owner: bool = False
    injuries: list = field(default_factory=list)    # [(first date, last date)] of injury breaks


class LinkTable:
    """Stand-in for accounts.db `research_links` (§3.4): user -> random donor id
    and the donor's day 0. Only withdrawal / retention read it; analysis code
    never sees it, and the pool never holds day 0."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def link(self, user_id: str, first_day: dt.date, rng: np.random.Generator) -> dict:
        if user_id not in self.rows:
            day0 = first_day - dt.timedelta(days=int(rng.integers(0, DAY0_JITTER_DAYS + 1)))
            self.rows[user_id] = {"donor_id": secrets.token_hex(16), "day0": day0}
        return self.rows[user_id]

    def get(self, user_id: str) -> Optional[dict]:
        return self.rows.get(user_id)

    def by_donor(self, donor_id: str) -> Optional[dict]:
        return next((r for r in self.rows.values() if r["donor_id"] == donor_id), None)

    def forget(self, user_id: str) -> None:
        self.rows.pop(user_id, None)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

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
    """4 bands (§3.3): dawn 4–8, day 8–17, evening 17–20, night 20–4."""
    if 4 <= hour < 8:
        return "dawn"
    if 8 <= hour < 17:
        return "day"
    if 17 <= hour < 20:
        return "evening"
    return "night"


def age_band(age: int) -> str:
    if age >= 60:
        return "60+"
    lo = max(18, (age // 10) * 10)
    return "18-29" if lo < 30 else f"{lo}-{lo + 9}"


def dedup_key(pool_key: bytes, source: str, platform_id: str) -> str:
    """HMAC, not a plain hash: a plain hash of a platform id can be recomputed by anyone who
    knows the id (§3.1). `pool_key` lives in accounts.db, never next to the pool."""
    return hmac.new(pool_key, f"{source}:{platform_id}".encode(), hashlib.sha256).hexdigest()[:32]


# ---------------------------------------------------------------------------
# GPS (§3.2)
# ---------------------------------------------------------------------------

def frequent_places(endpoints: Iterable[tuple[dt.date, float, float]], radius_m: float = PLACE_RADIUS_M,
                    min_count: int = PLACE_MIN_COUNT, window_days: int = PLACE_WINDOW_DAYS) -> list:
    """Centres of places where >= min_count starts / ends fall within radius_m of
    each other inside window_days. Computed in the tenant; never exported."""
    pts = [(d, la, lo) for d, la, lo in endpoints if np.isfinite(la) and np.isfinite(lo)]
    pts.sort(key=lambda x: x[0])
    places: list[tuple[float, float]] = []
    for d, la, lo in pts:
        near = [(q[1], q[2]) for q in pts
                if 0 <= (q[0] - d).days < window_days and haversine_m(la, lo, q[1], q[2]) <= radius_m]
        if len(near) < min_count:
            continue
        c = (float(np.mean([p[0] for p in near])), float(np.mean([p[1] for p in near])))
        if all(haversine_m(c[0], c[1], p[0], p[1]) > radius_m for p in places):
            places.append(c)
    return places


def _first_last_fix(tr: Track):
    ok = np.flatnonzero(np.isfinite(tr.lat) & np.isfinite(tr.lon))
    if not len(ok):
        return None, None
    i, j = ok[0], ok[-1]
    return (float(tr.lat[i]), float(tr.lon[i])), (float(tr.lat[j]), float(tr.lon[j]))


def trim_radii(rng: np.random.Generator) -> dict:
    """The random radii of one activity: each end and the frequent places get their own draw."""
    return {"start": TRIM_BASE_M + float(rng.uniform(0, TRIM_JITTER_M)),
            "end": TRIM_BASE_M + float(rng.uniform(0, TRIM_JITTER_M)),
            "place": PLACE_TRIM_M + float(rng.uniform(0, TRIM_JITTER_M))}


def keep_mask(tr: Track, places: list, radii: dict) -> np.ndarray:
    """True where a sample may be used (splits, grid cells). Stronger than §3.2's "cut until
    the track first leaves the circle": every sample inside the start circle, the end circle or
    any frequent-place circle is dropped, wherever it is in the activity (a loop that passes
    home mid-run is cut there too). No GPS at all (treadmill, GPS off): all True — there is no
    location to protect, and the splits never hold one."""
    has = np.isfinite(tr.lat) & np.isfinite(tr.lon)
    if not has.any():
        return np.ones(len(tr.t), dtype=bool)
    first, last = _first_last_fix(tr)
    keep = has.copy()
    keep &= haversine_m(first[0], first[1], tr.lat, tr.lon) > radii["start"]
    keep &= haversine_m(last[0], last[1], tr.lat, tr.lon) > radii["end"]
    for la, lo in places:
        keep &= haversine_m(la, lo, tr.lat, tr.lon) > radii["place"]
    return keep & has


def cells_of(tr: Track, keep: np.ndarray) -> dict:
    """{cell: [speeds]} over the kept samples — the tenant-side input of the grid aggregate."""
    out: dict = {}
    idx = np.flatnonzero(keep & np.isfinite(tr.lat) & np.isfinite(tr.lon))
    for i in idx:
        c = (int(math.floor(tr.lat[i] / CELL_LAT_DEG)), int(math.floor(tr.lon[i] / CELL_LON_DEG)))
        out.setdefault(c, []).append(float(tr.speed[i]))
    return out


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------

def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, stop) of the True runs."""
    m = np.concatenate([[False], mask, [False]]).astype(int)
    d = np.diff(m)
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def splits(tr: Track, keep: np.ndarray, donor: Donor) -> list[dict]:
    """1-km splits of the kept parts. No donor id, no activity id, no time, no position:
    grade, its spread, a 100-m elevation band, speed, % LTHR, W/kg, cadence, air temperature,
    which tenth of the activity it is in, sex and age band (§3.6 table 3)."""
    total_t = float(tr.t[-1]) if len(tr.t) else 0.0
    out = []
    for a, b in _runs(keep):
        i = a
        while i < b:
            target = tr.dist[i] + SPLIT_M
            j = int(np.searchsorted(tr.dist[i:b], target)) + i
            if j >= b:
                break                                   # the remainder (< 1 km) is dropped
            sl = slice(i, j + 1)
            d, e, t = tr.dist[sl], tr.ele[sl], tr.t[sl]
            dt_s = float(t[-1] - t[0])
            if dt_s <= 0:
                i = j
                continue
            # grade spread over 100-m pieces
            grid = np.arange(d[0], d[-1] + 1e-6, 100.0)
            eg = np.interp(grid, d, e) if len(grid) >= 3 else None
            g_sd = float(np.std(np.diff(eg) / 100.0)) if eg is not None else float("nan")
            p = _nanmean(tr.power[sl])
            out.append({
                "grade": _r((e[-1] - e[0]) / max(1.0, d[-1] - d[0]), 3),
                "grade_sd": _r(g_sd, 3),
                "ele_band_m": int(_bin(_nanmean(e), 100.0)),
                "speed_ms": _r(float(d[-1] - d[0]) / dt_s, 2),
                "pct_lthr": _r(100.0 * _nanmean(tr.hr[sl]) / donor.lthr, 1),
                "w_per_kg": _r(p / donor.weight_kg, 2) if donor.weight_kg and np.isfinite(p) else None,
                "cadence": _r(_nanmean(tr.cadence[sl]), 0),
                "temp_c": _r(_nanmean(tr.temp[sl]), 0),
                "decile": min(9, int(10 * (0.5 * (t[0] + t[-1])) / total_t)) if total_t > 0 else None,
                "sex": donor.sex,
                "age_band": age_band(donor.age),
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
    """Power TSS when there is power and a CP; else hrTSS (duration × (HR/LTHR)²)."""
    hours = float(tr.t[-1]) / 3600 if len(tr.t) else 0.0
    if donor.cp_w and np.isfinite(tr.power).sum() > 0.5 * len(tr.t):
        ifac = _np_power(tr.power) / donor.cp_w
        if np.isfinite(ifac):
            return hours * ifac ** 2 * 100
    r = _nanmean(tr.hr) / donor.lthr
    return hours * r ** 2 * 100 if np.isfinite(r) else 0.0


def drift_pct(tr: Track) -> Optional[float]:
    """Pa:HR decoupling: efficiency (speed / HR) of the first half over the second, minus 1 (%)."""
    if not len(tr.t) or tr.t[-1] < 20 * 60:
        return None
    h = np.searchsorted(tr.t, tr.t[-1] / 2)
    ef = [_nanmean(tr.speed[s]) / _nanmean(tr.hr[s]) for s in (slice(0, h), slice(h, None))]
    if not all(np.isfinite(x) and x > 0 for x in ef):
        return None
    return round((ef[0] / ef[1] - 1) * 100, 1)


def summary(tr: Track, donor: Donor, donor_id: str, rel_day: int, act_key: str) -> dict:
    """§3.6 table 2. Races: distance in 5-km bins, climb in 500-m bins, no name, no date."""
    loc = tr.local_start(donor.tz_offset_h)
    dist_km = float(np.nanmax(tr.dist)) / 1000 if len(tr.dist) else 0.0
    up = np.diff(tr.ele[np.isfinite(tr.ele)])
    ascent = float(up[up > 0].sum()) if len(up) else 0.0
    p = _nanmean(tr.power)
    return {
        "donor_id": donor_id,
        "activity_key": act_key,
        "rel_day": rel_day,
        "season": season_of(loc.month),
        "time_of_day": time_of_day(loc.hour + loc.minute / 60),
        "sport": tr.sport,
        "distance_km": _bin(dist_km, 5.0 if tr.is_race else 0.5),
        "ascent_m": int(_bin(ascent, 500.0 if tr.is_race else 50.0)),
        "duration_min": int(float(tr.t[-1]) // 60) if len(tr.t) else 0,
        "pct_lthr": _r(100.0 * _nanmean(tr.hr) / donor.lthr, 1),
        "pct_cp": _r(100.0 * p / donor.cp_w, 1) if donor.cp_w and np.isfinite(p) else None,
        "drift_pct": drift_pct(tr),
        "tss": _r(tss(tr, donor), 0),
        "rpe": tr.rpe,
        "is_race": bool(tr.is_race),
        "source": tr.source,
    }


def daily_load(donor_id: str, per_day: dict, injury_days: set) -> list[dict]:
    """§3.6 table 4: one row for every day from the first active day to the last."""
    if not per_day:
        return []
    lo, hi = min(per_day), max(per_day)
    rows = []
    for d in range(lo, hi + 1):
        x = per_day.get(d, {"tss": 0.0, "min": 0, "asc": 0.0})
        rows.append({"donor_id": donor_id, "rel_day": d, "tss": int(round(x["tss"])), "duration_min": int(x["min"]),
                     "ascent_band_m": int(_bin(x["asc"], 100.0)), "injury_off": d in injury_days})
    return rows


def person_params(tracks: list[Track], donor: Donor) -> Optional[dict]:
    """A layer: one row per person, computed in the tenant from ALL his A-allowed activities
    (not trimmed: no location leaves). No id, no date — only sex, age band and numbers."""
    if not tracks:
        return None
    sp = [s for tr in tracks for s in splits(tr, np.ones(len(tr.t), dtype=bool), donor)]
    flat = [s["speed_ms"] for s in sp if s["grade"] is not None and abs(s["grade"]) < 0.02
            and s["pct_lthr"] is not None and s["pct_lthr"] < 85]
    steep = [s["speed_ms"] * s["grade"] * 3600 for s in sp if s["grade"] is not None and s["grade"] > 0.08]
    drifts = [d for d in (drift_pct(tr) for tr in tracks if tr.t[-1] >= 3600) if d is not None]
    med = lambda xs: round(float(np.median(xs)), 2) if xs else None    # noqa: E731
    return {"sex": donor.sex, "age_band": age_band(donor.age), "easy_flat_speed_ms": med(flat),
            "uphill_vam_mh": med(steep), "drift_pct": med(drifts)}


# ---------------------------------------------------------------------------
# export, withdrawal, retention
# ---------------------------------------------------------------------------

@dataclass
class DonorExport:
    summaries: list = field(default_factory=list)   # B
    splits: list = field(default_factory=list)      # B (shuffled, no ids)
    daily: list = field(default_factory=list)       # B
    params: Optional[dict] = None                   # A (transient: aggregated, then dropped)
    cells: dict = field(default_factory=dict)       # A (transient: tenant-side grid input)
    attributions: set = field(default_factory=set)
    excluded: dict = field(default_factory=dict)    # source -> n activities refused
    kept_m: float = 0.0                             # metres of track kept after trimming (B)
    total_m: float = 0.0
    acts_without_splits: int = 0                    # B activities that gave no split at all


def export_donor(donor: Donor, tracks: list[Track], links: LinkTable, *, pool_key: bytes,
                 policy: Optional[SourcePolicy] = None, rng: Optional[np.random.Generator] = None,
                 trim_splits: bool = True) -> DonorExport:
    """One donor's rows. `trim_splits` False: the splits come from the whole track (they hold no
    position anyway) and the trimming applies to the grid cells only — the doc's §3.2 trims
    both; the prototype measured what that costs (§14)."""
    policy = policy or SourcePolicy()
    rng = rng or np.random.default_rng()
    out = DonorExport()
    a_tracks = [tr for tr in tracks if donor.consent_a and policy.allows(tr.source, "A", owner=donor.owner)]
    b_tracks = [tr for tr in tracks if donor.consent_b and policy.allows(tr.source, "B", owner=donor.owner)]
    for tr in tracks:
        if not policy.allows(tr.source, "A", owner=donor.owner) and not policy.allows(tr.source, "B",
                                                                                      owner=donor.owner):
            out.excluded[tr.source] = out.excluded.get(tr.source, 0) + 1
    # frequent places from every activity the tenant has (any source): detection is tenant-side
    ends = []
    for tr in tracks:
        f, l_ = _first_last_fix(tr)
        d = tr.local_start(donor.tz_offset_h).date()
        if f:
            ends += [(d, *f), (d, *l_)]
    places = frequent_places(ends)
    for tr in a_tracks + b_tracks:
        a = policy.attribution(tr.source)
        if a:
            out.attributions.add(a)
    # A: per-person parameters + grid cells (both leave only as k-gated aggregates)
    out.params = person_params(a_tracks, donor)
    for tr in a_tracks:
        for c, v in cells_of(tr, keep_mask(tr, places, trim_radii(rng))).items():
            out.cells.setdefault(c, []).extend(v)
    if not b_tracks:
        return out
    # B: detail under a random donor id
    first = min(tr.local_start(donor.tz_offset_h).date() for tr in b_tracks)
    link = links.link(donor.user_id, first, rng)
    per_day: dict = {}
    for tr in sorted(b_tracks, key=lambda x: x.start_utc):
        rel = (tr.local_start(donor.tz_offset_h).date() - link["day0"]).days
        pid = tr.account.get("platform_activity_id") or secrets.token_hex(16)
        s = summary(tr, donor, link["donor_id"], rel, dedup_key(pool_key, tr.source, str(pid)))
        out.summaries.append(s)
        keep = keep_mask(tr, places, trim_radii(rng))
        sp = splits(tr, keep if trim_splits else np.ones(len(tr.t), dtype=bool), donor)
        out.splits += sp
        out.acts_without_splits += not sp
        if len(tr.dist) > 1:
            step = np.diff(tr.dist, prepend=tr.dist[0])
            out.total_m += float(np.nansum(step))
            out.kept_m += float(np.nansum(step[keep]))
        x = per_day.setdefault(rel, {"tss": 0.0, "min": 0, "asc": 0.0})
        x["tss"] += s["tss"] or 0.0
        x["min"] += s["duration_min"]
        up = np.diff(tr.ele[np.isfinite(tr.ele)])
        x["asc"] += float(up[up > 0].sum()) if len(up) else 0.0
    injury_days = set()
    for d1, d2 in donor.injuries:
        if (d2 - d1).days + 1 >= 7:                     # only breaks of 7 days or more (§3.5)
            injury_days |= {(d1 - link["day0"]).days + k for k in range((d2 - d1).days + 1)}
    out.daily = daily_load(link["donor_id"], per_day, injury_days)
    order = rng.permutation(len(out.splits))
    out.splits = [out.splits[i] for i in order]         # no order: splits cannot be chained back
    return out


@dataclass
class Pool:
    """In-memory stand-in for research/pool.db (the prototype never creates that table)."""
    summaries: list = field(default_factory=list)
    splits: list = field(default_factory=list)
    daily: list = field(default_factory=list)

    def add(self, ex: DonorExport) -> None:
        seen = {s["activity_key"] for s in self.summaries}
        self.summaries += [s for s in ex.summaries if s["activity_key"] not in seen]
        self.splits += ex.splits
        days = {(r["donor_id"], r["rel_day"]) for r in self.daily}
        self.daily += [r for r in ex.daily if (r["donor_id"], r["rel_day"]) not in days]


def withdraw(pool: Pool, links: LinkTable, user_id: str) -> dict:
    """B withdrawal (§7.2): rows that still map to the person go, and the link row with them;
    the splits (no id) and the aggregates stay — the next recompute leaves him out."""
    link = links.get(user_id)
    if link is None:
        return {"summaries": 0, "daily": 0}
    did = link["donor_id"]
    n_s = sum(1 for s in pool.summaries if s["donor_id"] == did)
    n_d = sum(1 for r in pool.daily if r["donor_id"] == did)
    pool.summaries = [s for s in pool.summaries if s["donor_id"] != did]
    pool.daily = [r for r in pool.daily if r["donor_id"] != did]
    links.forget(user_id)
    return {"summaries": n_s, "daily": n_d}


def expire(pool: Pool, links: LinkTable, today: dt.date, years: int = RETENTION_YEARS) -> int:
    """3-year retention of the rows with a donor id: the link table's day 0 turns a relative
    day back into a date (only this job reads it). Splits carry no day: they cannot be expired
    (see the doc's §14 findings)."""
    cut = today - dt.timedelta(days=round(365.25 * years))
    day0 = {r["donor_id"]: r["day0"] for r in links.rows.values()}

    def old(r):
        d0 = day0.get(r["donor_id"])
        return d0 is not None and d0 + dt.timedelta(days=r["rel_day"]) < cut

    n = sum(map(old, pool.summaries)) + sum(map(old, pool.daily))
    pool.summaries = [s for s in pool.summaries if not old(s)]
    pool.daily = [r for r in pool.daily if not old(r)]
    return n


# ---------------------------------------------------------------------------
# k-gated aggregates (§3.5 "人口特徵的 k 門檻", §10 item 6)
# ---------------------------------------------------------------------------

def k_for(audience: str) -> int:
    return {"internal": K_INTERNAL, "public": K_PUBLIC}[audience]


def _band_label(bands: list[str]) -> str:
    if len(bands) == 1:
        return bands[0]
    lo = bands[0].split("-")[0].rstrip("+")
    hi = bands[-1]
    return f"{lo}+" if hi.endswith("+") else f"{lo}-{hi.split('-')[1]}"


def _merge_bands(rows: list[dict], k: int) -> Optional[list[tuple[str, list[dict]]]]:
    """Adjacent age bands merged until each has >= k people; None when even all of them < k."""
    groups = [[[b], [r for r in rows if r["age_band"] == b]] for b in AGE_BANDS]
    groups = [g for g in groups if g[1]]
    if sum(len(g[1]) for g in groups) < k:
        return None
    while len(groups) > 1:
        small = [i for i, g in enumerate(groups) if len(g[1]) < k]
        if not small:
            break
        i = small[0]
        nb = [j for j in (i - 1, i + 1) if 0 <= j < len(groups)]
        j = min(nb, key=lambda x: (len(groups[x][1]), x))
        a, b = sorted((i, j))
        groups[a] = [groups[a][0] + groups[b][0], groups[a][1] + groups[b][1]]
        del groups[b]
    return [(_band_label(bands), rs) for bands, rs in groups]


def _stats(rows: list[dict], fields: list[str], k: int) -> dict:
    out = {}
    for f in fields:
        xs = [r[f] for r in rows if r.get(f) is not None]
        out[f] = None if len(xs) < k else {"median": round(float(np.median(xs)), 2),
                                           "p25": round(float(np.percentile(xs, 25)), 2),
                                           "p75": round(float(np.percentile(xs, 75)), 2)}
    return out


def aggregate_params(params: list[dict], audience: str = "internal") -> list[dict]:
    """Per-person A-layer rows -> group statistics. Groups are sex × age band, adjacent bands
    merged below k; if either sex cannot reach k, only the all-people row is given (publishing
    "all" next to "male" would give "female" away by subtraction). A field with fewer than k
    values in a group is None."""
    k = k_for(audience)
    rows = [p for p in params if p]
    if len(rows) < k:
        return []
    fields = [f for f in rows[0] if f not in ("sex", "age_band")]
    by_sex = {s: [r for r in rows if r["sex"] == s] for s in sorted({r["sex"] for r in rows})}
    merged = {s: _merge_bands(rs, k) for s, rs in by_sex.items()}
    if any(m is None for m in merged.values()):
        return [{"sex": "all", "age_band": "all", "n": len(rows), **_stats(rows, fields, k)}]
    return [{"sex": s, "age_band": label, "n": len(rs), **_stats(rs, fields, k)}
            for s, groups in merged.items() for label, rs in groups]


def aggregate_grid(cells_per_person: list[dict], audience: str = "internal") -> list[dict]:
    """Grid cells walked by >= k distinct people: count, passes (samples), median speed."""
    k = k_for(audience)
    people: dict = {}
    speeds: dict = {}
    for cells in cells_per_person:
        for c, v in cells.items():
            people[c] = people.get(c, 0) + 1
            speeds.setdefault(c, []).extend(v)
    return [{"cell": list(c), "n_people": n, "n_samples": len(speeds[c]),
             "median_speed_ms": round(float(np.median(speeds[c])), 2)}
            for c, n in sorted(people.items()) if n >= k]


# ---------------------------------------------------------------------------
# the demo athlete as input (synthetic only)
# ---------------------------------------------------------------------------

def track_from_fit(raw: bytes, *, source: str = "manual", account: Optional[dict] = None,
                   sport: Optional[str] = None, is_race: bool = False, rpe: Optional[float] = None) -> Track:
    """A FIT -> Track through the app's own decoder (the same channels as the per-second cache)."""
    from backend.files.fit_to_channels import fit_to_channels
    fc = fit_to_channels(raw)
    n = len(fc.elapsedtime)

    def ch(name, k=1.0):
        v = fc.channels.get(name)
        if v is None:
            return np.full(n, np.nan)
        return np.array([np.nan if x is None else float(x) * k for x in v], dtype=float)

    dist = ch("elapseddistance", 1000.0)
    dist = np.fmax.accumulate(np.nan_to_num(dist, nan=0.0))
    start = fc.start_time
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    if sport is None:
        sport = "hike" if fc.sport == "hiking" else ("trail" if fc.sub_sport == "trail" else "run")
    return Track(t=np.asarray(fc.elapsedtime, dtype=float), lat=ch("latitude"), lon=ch("longitude"), dist=dist,
                 speed=ch("speed", 1 / 3.6), ele=ch("elevation"), hr=ch("heartrate"), power=ch("power"),
                 cadence=ch("cadence"), temp=ch("temperature"), start_utc=start.astimezone(dt.timezone.utc),
                 sport=sport, source=source, is_race=is_race, rpe=rpe, account=dict(account or {}))


DEMO_ACCOUNT = {"email": "demo-runner@example.invalid", "google_sub": "109876543210987654321",
                "coros_user_id": "demo-coros-4711", "tp_username": "demo_tp_runner", "athlete_name": "示範跑者"}


def demo_tracks(seed: int = 20261002, anchor: dt.date = dt.date(2026, 10, 3), weeks: int = 52,
                small: bool = False, source: str = "manual") -> tuple[Donor, list[Track], list[dict]]:
    """The demo athlete (backend/demo, invented numbers) as a donor + his tracks. Each track gets
    synthetic account links (file name, platform id) so the tests can look for them in the rows."""
    from backend.demo import athlete as A
    from backend.demo import season as S
    from backend.demo.generate import _trip_legs, build_activity
    season = S.schedule(seed, anchor, weeks)
    trips = {}
    for p in season.activities:
        if p.kind == "baiyue" and p.trip not in trips:
            n = sum(1 for q in season.activities if q.trip == p.trip)
            trips[p.trip] = _trip_legs(seed, p.trip, max(n, 2), small)
    donor = Donor(user_id=f"demo-user-{seed}", sex=A.SEX, age=A.AGE, lthr=A.LTHR, cp_w=A.CP_W,
                  weight_kg=A.WEIGHT_KG, tz_offset_h=A.TZ_OFFSET_H, consent_b=True)
    tracks, infos = [], []
    for i, p in enumerate(season.activities, start=1):
        raw, info = build_activity(seed, i, p, season, small, trips)
        pid = f"{seed % 100000:05d}{i:06d}"
        acc = {**DEMO_ACCOUNT, "file_path": f"fit/coros/{p.date.year}/{pid}_{p.date:%Y%m%d}.fit",
               "platform_activity_id": pid, "coros_activity_id": pid, "tp_workout_id": f"tp{pid}"}
        tracks.append(track_from_fit(raw, source=source, account=acc, is_race=p.kind == "race_half",
                                     sport="hike" if p.sport == "hike" else None))
        infos.append({**info, "fit_bytes": len(raw), "kind": p.kind})
    return donor, tracks, infos


# ---------------------------------------------------------------------------
# storage estimate (§9.1), measured on the demo athlete
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE summaries (donor_id TEXT, activity_key TEXT, rel_day INT, season TEXT, time_of_day TEXT,
  sport TEXT, distance_km REAL, ascent_m INT, duration_min INT, pct_lthr REAL, pct_cp REAL, drift_pct REAL,
  tss REAL, rpe REAL, is_race INT, source TEXT);
CREATE TABLE splits (grade REAL, grade_sd REAL, ele_band_m INT, speed_ms REAL, pct_lthr REAL, w_per_kg REAL,
  cadence REAL, temp_c REAL, decile INT, sex TEXT, age_band TEXT);
CREATE TABLE daily (donor_id TEXT, rel_day INT, tss INT, duration_min INT, ascent_band_m INT, injury_off INT);
CREATE TABLE params (sex TEXT, age_band TEXT, easy_flat_speed_ms REAL, uphill_vam_mh REAL, drift_pct REAL);
"""


def _db_bytes(table: str, rows: list[dict]) -> int:
    """Bytes the rows add to a SQLite file (VACUUMed; the empty schema subtracted)."""
    with tempfile.TemporaryDirectory() as d:
        sizes = []
        for data in ([], rows):
            p = Path(d) / f"{table}{len(sizes)}.db"
            con = sqlite3.connect(p)
            con.executescript(SCHEMA)
            if data:
                cols = list(data[0])
                con.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                [tuple(r[c] for c in cols) for r in data])
            con.commit()
            con.execute("VACUUM")
            con.close()
            sizes.append(p.stat().st_size)
        return sizes[1] - sizes[0]


def channels_5s_bytes(tr: Track) -> int:
    """The optional 5-second channels without GPS: float32, numpy-compressed."""
    ch = {k: getattr(tr, k)[::5].astype(np.float32) for k in ("speed", "ele", "hr", "power", "cadence", "temp")}
    buf = io.BytesIO()
    np.savez_compressed(buf, **ch)
    return len(buf.getvalue())


def estimate(weeks: int = 52, seed: int = 20261002, per_person_acts: int = 330, per_person_days: int = 1100,
             people: int = 500, trim_splits: bool = True, demo=None) -> dict:
    donor, tracks, infos = demo or demo_tracks(seed=seed, weeks=weeks)
    links = LinkTable()
    ex = export_donor(donor, tracks, links, pool_key=b"estimate-only", rng=np.random.default_rng(seed),
                      trim_splits=trim_splits)
    n = len(tracks)
    b_sum = _db_bytes("summaries", ex.summaries)
    b_spl = _db_bytes("splits", ex.splits)
    b_day = _db_bytes("daily", ex.daily)
    b_par = _db_bytes("params", [ex.params])
    b_5s = sum(channels_5s_bytes(tr) for tr in tracks)
    fit = sum(i["fit_bytes"] for i in infos)
    per_act = {"summary": b_sum / n, "splits": b_spl / n, "channels_5s": b_5s / n, "fit": fit / n}
    per_day = b_day / max(1, len(ex.daily))
    per_person = {"params": b_par, "summary": per_act["summary"] * per_person_acts,
                  "splits": per_act["splits"] * per_person_acts, "daily": per_day * per_person_days,
                  "channels_5s": per_act["channels_5s"] * per_person_acts}
    total = sum(per_person.values())
    no5s = total - per_person["channels_5s"]
    road = [s for s in ex.summaries if s["sport"] == "run"]
    uniq = {}
    for s in ex.summaries:
        key = (s["sport"], s["distance_km"], s["ascent_m"], s["duration_min"])
        uniq[key] = uniq.get(key, 0) + 1
    return {
        "demo": {"weeks": weeks, "activities": n, "splits": len(ex.splits), "daily_rows": len(ex.daily),
                 "mean_distance_km": round(float(np.mean([t.dist[-1] for t in tracks])) / 1000, 1),
                 "trim_splits": trim_splits,
                 "kept_share": round(ex.kept_m / ex.total_m, 3) if ex.total_m else None,
                 "activities_without_splits": ex.acts_without_splits,
                 "road_runs": len(road),
                 "unique_summary_share": round(sum(1 for v in uniq.values() if v == 1) / n, 3)},
        "bytes_per_activity": {k: round(v) for k, v in per_act.items()},
        "bytes_per_daily_row": round(per_day),
        "per_person_mb": {k: round(v / 1e6, 3) for k, v in per_person.items()},
        "per_person_total_mb": round(total / 1e6, 2),
        "people": people,
        "total_gb": round(total * people / 1e9, 3),
        "total_gb_without_5s": round(no5s * people / 1e9, 3),
        "assumptions": {"activities_per_person": per_person_acts, "days_per_person": per_person_days},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    demo = demo_tracks(weeks=a.weeks)
    res = {"trimmed_splits": estimate(weeks=a.weeks, demo=demo),
           "untrimmed_splits": estimate(weeks=a.weeks, demo=demo, trim_splits=False)}
    txt = json.dumps(res, ensure_ascii=False, indent=2)
    print(txt)
    if a.json:
        a.json.write_text(txt, "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
