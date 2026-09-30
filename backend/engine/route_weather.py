"""
Historical weather for every segment / route effort (docs/spec/route-progress.spec.md,
Weather): air temperature, humidity and dew point at the effort's time and
place from the Open-Meteo historical archive, plus the Hadley heat sum.

Reuses racepower/weather.py's archive client (`_http_get`, `OM_ARCHIVE`),
its per-window rule (`activities_conditions`: mean of the hourly rows within
±30 min of the effort) and attribution, and env.py's Magnus dew point and
Hadley penalty. Nothing in racepower/ is changed.

Place: each effort's own point — its mean position (0.01°) and mean
elevation (10 m), passed as `elevation` so Open-Meteo downscales T and dew
point to the effort's height. (Querying a shared cell centre and correcting
by −6.5 °C/km instead was 2.6 °C too warm on 七星山: the cell centre's model
cell is the basin.)

Batching: efforts are grouped by (local date, 0.25° cell). Each group is ONE
archive call for that single day carrying all of its points (Open-Meteo takes
comma-separated coordinates and returns one result per point), cached on disk
(`<store>/weather/<cell lat>_<cell lon>_<date>.json`), so a full build makes
one call per distinct (day, cell) and a rebuild none; a point new to a cached
group costs one call for the new points only. A failed call is not cached
(retried next build); after MAX_CONSEC_FAIL failures in a row the rest are
skipped, so an offline build costs a few timeouts, never the whole list.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from backend.engine.racepower import weather as WX
from backend.engine.racepower.env import dew_point, heat_penalty_pct

CELL_DEG = 0.25                  # batching cell
POINT_DEG = 0.01                 # an effort's point, ~1 km
ELEV_STEP_M = 10.0
HOURLY = "temperature_2m,relative_humidity_2m,dew_point_2m"
TIMEZONE = "auto"                # local wall-clock time at the point = the activity's start clock
# Hadley sum (°F + °F) above which pace suffers >= ~4.5 % (Hadley's 151–160
# band). 130 (~2 %) flagged 75 % of the real efforts — Taiwan's usual summer
# evening — and so explained nothing.
HOT_HADLEY = 150.0
MAX_CONSEC_FAIL = 5
MAX_CALLS = 2000                 # per build; the rest wait for the next build
MAX_POINTS = 50                  # per call (URL length); more points = more calls
WORKERS = 3                      # well under Open-Meteo's 600 calls / min
RECENT_DAYS = 10                 # the archive lags a few days: an empty recent day is not cached
TIMEOUT_S = 20.0


def _q(v: float, step: float) -> float:
    return round(round(v / step) * step, 6)


def cell_of(lat: float, lon: float) -> tuple[float, float]:
    """Centre of the 0.25° cell holding (lat, lon) — the batching key only."""
    f = lambda v: round((math.floor(v / CELL_DEG) + 0.5) * CELL_DEG, 3)
    return f(lat), f(lon)


def point_of(lat: float, lon: float, elev_m: Optional[float]) -> tuple:
    """The queried point: position to 0.01°, elevation to 10 m (None = Open-Meteo's DEM)."""
    return (round(_q(lat, POINT_DEG), 2), round(_q(lon, POINT_DEG), 2),
            None if elev_m is None else _q(elev_m, ELEV_STEP_M))


def point_key(p: tuple) -> str:
    return f"{p[0]:.2f},{p[1]:.2f},{'dem' if p[2] is None else f'{p[2]:.0f}'}"


def cache_path(root: Path, cell: tuple[float, float], day: dt.date) -> Path:
    return root / f"{cell[0]:.3f}_{cell[1]:.3f}_{day.isoformat()}.json"


def archive_params(points: list[tuple], day: dt.date) -> dict:
    """One archive request for several points of one day (all with or all without elevation)."""
    p = {"latitude": ",".join(f"{x[0]:.2f}" for x in points),
         "longitude": ",".join(f"{x[1]:.2f}" for x in points),
         "start_date": day.isoformat(), "end_date": day.isoformat(), "timezone": TIMEZONE, "hourly": HOURLY}
    if points[0][2] is not None:
        p["elevation"] = ",".join(f"{x[2]:.0f}" for x in points)
    return p


def effort_window(start_iso: str, t0: float, t1: float) -> tuple[dt.datetime, dt.datetime]:
    s = dt.datetime.fromisoformat(start_iso)
    if s.tzinfo:
        s = s.replace(tzinfo=None)
    return s + dt.timedelta(seconds=float(t0)), s + dt.timedelta(seconds=float(t1))


def window_days(a: dt.datetime, b: dt.datetime) -> list[dt.date]:
    """Days whose hours can fall within ±30 min of [a, b] (activities_conditions' rule)."""
    lo = (a - dt.timedelta(minutes=30)).date()
    hi = (b + dt.timedelta(minutes=30)).date()
    return [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)]


def place(tr, i0: int, i1: int) -> Optional[dict]:
    """Mean position and elevation of an effort's kept points."""
    la, lo = tr.lat[i0:i1 + 1], tr.lon[i0:i1 + 1]
    if not len(la):
        return None
    e = tr.e[i0:i1 + 1]
    e = e[np.isfinite(e)]
    return {"lat": float(la.mean()), "lon": float(lo.mean()),
            "elev_m": float(e.mean()) if len(e) else None}


def conditions(days_js: list[dict], a: dt.datetime, b: dt.datetime) -> Optional[dict]:
    """The effort's weather from the archive days of its point: T and RH =
    `WX.activities_conditions` over [a, b] (hourly rows within ±30 min), already
    at the effort's elevation (Open-Meteo's downscaling); dew point =
    env.dew_point(T, RH) (Magnus); Hadley sum = T °F + dew °F, penalty =
    env.heat_penalty_pct."""
    if not days_js:
        return None
    merged = {"hourly": {"time": [], "temperature_2m": [], "relative_humidity_2m": []},
              "elevation": days_js[0].get("elevation")}
    for js in days_js:
        h = js.get("hourly") or {}
        n = len(h.get("time") or [])
        merged["hourly"]["time"] += h.get("time") or []
        for k in ("temperature_2m", "relative_humidity_2m"):
            v = list(h.get(k) or [])
            merged["hourly"][k] += (v + [None] * n)[:n]
    c = WX.activities_conditions(merged, [(a, b)])
    if c is None:
        return None
    t, rh = c["temp_c"], c["rh_pct"]
    dw = dew_point(t, rh)
    x = t * 1.8 + 32.0 + dw["dew_f"]
    return {"temp_c": round(t, 1), "rh_pct": round(rh, 0), "dew_c": round(dw["dew_c"], 1),
            "hadley": round(x, 0), "heat_pct": round(heat_penalty_pct(t, rh), 1), "hot": x > HOT_HADLEY,
            "archive_elev_m": merged["elevation"]}


class Fetcher:
    """Archive days of points, grouped by (cell, day): from the disk cache or
    one call per group (per MAX_POINTS points)."""

    def __init__(self, root: Path, get: Callable = WX._http_get, today: Optional[dt.date] = None):
        self.root = Path(root)
        self.get = get
        self.today = today or dt.date.today()
        self.stats = {"needed": 0, "points": 0, "cache_hits": 0, "calls": 0, "failed": 0, "skipped": 0,
                      "empty": 0, "recent": 0}
        self.errors: list[str] = []
        self._lock = threading.Lock()

    def load(self, cell, day) -> dict:
        try:
            return json.loads(cache_path(self.root, cell, day).read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _fetch(self, job) -> tuple:
        (cell, day), pts = job
        try:
            js = self.get(WX.OM_ARCHIVE, archive_params(pts, day), TIMEOUT_S)
        except Exception as e:           # noqa: BLE001 — degrade, never fail the build
            return job, None, f"{type(e).__name__}: {e}"[:120]
        res = js if isinstance(js, list) else [js]
        if len(res) != len(pts) or not all(isinstance(r, dict) and (r.get("hourly") or {}).get("time") for r in res):
            return job, None, "回應沒有逐時資料"
        return job, res, None

    def _save(self, cell, day, doc: dict) -> None:
        p = cache_path(self.root, cell, day)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")), "utf-8")
        tmp.replace(p)

    def fetch_all(self, need: dict, progress: Callable = lambda *_: None) -> dict:
        """need: {(cell, day): {point, ...}} -> {(cell, day, point): archive json}."""
        out, jobs, docs = {}, [], {}
        self.stats["needed"] = len(need)
        self.stats["points"] = sum(len(v) for v in need.values())
        for key in sorted(need):
            doc = docs[key] = self.load(*key)
            have = doc.get("points", {})
            missing = []
            for p in sorted(need[key], key=point_key):
                js = have.get(point_key(p))
                if js is not None:
                    out[(*key, p)] = js
                else:
                    missing.append(p)
            if not missing:
                self.stats["cache_hits"] += 1
                continue
            for with_elev in (True, False):
                grp = [p for p in missing if (p[2] is not None) == with_elev]
                for s in range(0, len(grp), MAX_POINTS):
                    jobs.append((key, grp[s:s + MAX_POINTS]))
        consec = done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for s in range(0, len(jobs), WORKERS):
                if consec >= MAX_CONSEC_FAIL or self.stats["calls"] >= MAX_CALLS:
                    self.stats["skipped"] += len(jobs) - s
                    break
                batch = jobs[s:s + WORKERS]
                self.stats["calls"] += len(batch)
                for (key, pts), res, err in pool.map(self._fetch, batch):
                    done += 1
                    if res is None:
                        self.stats["failed"] += 1
                        consec += 1
                        if len(self.errors) < 5:
                            self.errors.append(err)
                        continue
                    consec = 0
                    doc = docs[key]
                    doc.setdefault("cell", list(key[0]))
                    doc.setdefault("date", key[1].isoformat())
                    store = doc.setdefault("points", {})
                    for p, r in zip(pts, res):
                        js = {"elevation": r.get("elevation"), "timezone": r.get("timezone"), "hourly": r["hourly"]}
                        out[(*key, p)] = js
                        if not [v for v in r["hourly"].get("temperature_2m") or [] if v is not None]:
                            self.stats["empty"] += 1
                            if (self.today - key[1]).days <= RECENT_DAYS:
                                self.stats["recent"] += 1
                                continue          # not in the archive yet: ask again next build
                        store[point_key(p)] = js
                    doc["fetched_at"] = dt.datetime.now().isoformat(timespec="seconds")
                    self._save(*key, doc)
                progress("歷史天氣", done, len(jobs))
        return out


def fill(idx: dict, tracks: dict, root: Path, get: Callable = WX._http_get,
         progress: Callable = lambda *_: None, today: Optional[dt.date] = None) -> dict:
    """Set `wx` on every segment / route effort in idx (None when unknown) and
    idx["weather"] = the build's call counts + attribution."""
    efforts = []
    need: dict = {}
    for s in idx.get("segments", []) + idx.get("routes", []):
        for e in s.get("efforts", []):
            tr = tracks.get(e["file"])
            e["wx"] = None
            if tr is None:
                continue
            t0, t1 = tr.t[e["i0"]], tr.t[e["i1"]]
            pl = place(tr, e["i0"], e["i1"])
            if pl is None or not (np.isfinite(t0) and np.isfinite(t1)):
                continue
            a, b = effort_window(tr.start, t0, t1)
            cell = cell_of(pl["lat"], pl["lon"])
            pt = point_of(pl["lat"], pl["lon"], pl["elev_m"])
            keys = [(cell, d) for d in window_days(a, b)]
            for k in keys:
                need.setdefault(k, set()).add(pt)
            efforts.append((e, a, b, pl, pt, keys))
    f = Fetcher(root, get, today)
    got = f.fetch_all(need, progress)
    n_ok = 0
    for e, a, b, pl, pt, keys in efforts:
        days = [got[(*k, pt)] for k in keys if (*k, pt) in got]
        wx = conditions(days, a, b) if len(days) == len(keys) else None
        if wx is not None:
            wx.update(lat=pt[0], lon=pt[1], elev_m=pt[2], cell=list(keys[0][0]),
                      window=[a.isoformat(timespec="seconds"), b.isoformat(timespec="seconds")])
        e["wx"] = wx
        n_ok += wx is not None
    stats = {**f.stats, "efforts": len(efforts), "with_weather": n_ok, "errors": f.errors,
             "attribution": WX.ATTRIBUTION, "at": dt.datetime.now().isoformat(timespec="seconds"),
             "cell_deg": CELL_DEG}
    idx["weather"] = stats
    return stats


def retry_wanted(idx: dict) -> bool:
    """A no-change build still fills weather when the last pass left gaps it can close."""
    w = idx.get("weather")
    return w is None or bool(w.get("failed") or w.get("skipped") or w.get("recent"))
