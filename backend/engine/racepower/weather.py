"""
Race-day and training weather for the environment multiplier
(docs/research/superpower-calculator.md §3.3).

Provider chain for race day (first success wins; every value stays editable):
  1. CWA 中央氣象署 mountain forecasts (needs a free 授權碼):
       F-B0053-035 登山三天3小時 (hourly day 1, 3-hourly to day 3)
       F-B0053-033 登山一週日夜 (06–18 / 18–06 blocks, 7 days)
     Whole-file downloads (~12 / ~7.5 MB) → compacted and cached on disk,
     refreshed when ≥ 3 h old.
  2. Open-Meteo forecast (no key, ≤ 16 days).
  3. Open-Meteo archive climatology (SP-210): the month centred on the race
     date (±15 days) in each of the last 10 years, at the target elevation
     (Open-Meteo downscales; −6.5 °C/km from the answer's elevation when it
     differs), each hour the mean of three reanalyses (ERA5, ERA5-Land,
     ECMWF IFS 9 km). Gives the daytime mean and a 24-hour profile (the mean
     of each clock hour), so far-off races still get per-segment heat.
     Cached on disk for good (past years do not change).
  4. Manual values.

Why three models (SP-210, checked 2026-10-06 against CWA 1991–2020 station
normals — 玉山 3845 m, 阿里山 2413 m, 日月潭 1018 m, 鞍部 838 m, 臺北, 臺中;
2017–2020 daily means at the station elevation, monthly bias; the period
itself runs ≈ +0.3 °C warm vs 1991–2020):
    ECMWF IFS alone (Open-Meteo's best_match since 2017)  bias −0.68  MAE 0.80 °C
    ERA5 alone                                             bias +0.42  MAE 0.93 °C
    ERA5-Land alone                                        bias +0.03  MAE 0.87 °C
    mean of the three                                      bias −0.08  MAE 0.57 °C
Each model misses a different station (ERA5 −1.5 °C at 玉山, ERA5-Land
−1.1 °C on the plains, IFS cold almost everywhere), so their mean is the
best of the four. 6 stations, one 4-year period: 教練級, not a published
validation.

Parsing is separated from fetching so tests never touch the network.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean
from typing import Callable, Optional

from backend.engine.racepower.env import dew_point, rh_from_dew_point
from backend.i18n import N_, _

HOME = None      # fixed folder (tests); None = the tenant's shared root
KEY_PATH = None  # fixed file (tests); None = home()/weather.json (owner only: the demo has no weather.key cap)


def home() -> Path:
    if HOME is not None:
        return Path(HOME)
    from backend import tenancy
    return tenancy.shared_path()


def key_path() -> Path:
    return Path(KEY_PATH) if KEY_PATH is not None else home() / "weather.json"
PEAKS_PATH = Path(__file__).resolve().parents[2] / "data" / "baiyue.json"

CWA_URL = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/{id}"
CWA_HOURLY, CWA_WEEKLY = "F-B0053-035", "F-B0053-033"
CWA_MAX_AGE_H = 3.0
CWA_MATCH_KM = 5.0
OM_FORECAST = "https://api.open-meteo.com/v1/forecast"
OM_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OM_HORIZON_DAYS = 16
LAPSE_C_PER_M = -0.0065
DAY_HOURS = range(6, 18)          # daytime 06:00–17:59 local
TZ = dt.timezone(dt.timedelta(hours=8))    # CWA (Taiwan) timestamps; Open-Meteo answers in the location's zone ("auto")
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
# climatology (SP-210; the numbers in the module docstring)
CLIM_YEARS = 10                   # 推估: 10 years halve the year-to-year noise of 5, warming bias ≈ −0.15 °C
CLIM_HALF_DAYS = 15               # ±15 days = the month centred on the race date, not the calendar month:
                                  # 臺北 cools 2.7 °C from Oct to Nov, so 31 Oct would be ~1.3 °C off
CLIM_MODELS = ("era5", "era5_land", "ecmwf_ifs")    # IFS starts 2017: older years average the other two
CLIM_HOURLY = "temperature_2m,relative_humidity_2m,dew_point_2m"
CLIM_WORKERS = 4                  # parallel archive calls (well under Open-Meteo's 600 / min)
CLIM_VERSION = 2                  # bump to ignore older cache files
ARCHIVE_LAG_DAYS = 6              # the archive's newest days are still missing

PROVIDER_LABEL = {"cwa_hourly": "中央氣象署 登山三天預報", "cwa_weekly": "中央氣象署 登山一週預報",
                  "open_meteo": "Open-Meteo 預報", "climatology": N_("近 10 年同月平均（Open-Meteo 歷史資料）"),
                  "manual": "手動 / 預設"}


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------

def load_key(path: Optional[Path] = None) -> Optional[str]:
    path = path or key_path()
    env = os.getenv("CWA_API_KEY")
    if env:
        return env.strip()
    try:
        return (json.loads(path.read_text("utf-8")).get("cwa_api_key") or "").strip() or None
    except (OSError, ValueError):
        return None


def save_key(key: str, path: Optional[Path] = None) -> None:
    path = path or key_path()
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        data = {}
    key = (key or "").strip()
    if key:
        data["cwa_api_key"] = key
    else:
        data.pop("cwa_api_key", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), "utf-8")


def mask_key(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return key[:8] + "…" if len(key) > 8 else key[:2] + "…"


def key_status(path: Optional[Path] = None) -> dict:
    path = path or key_path()
    k = load_key(path)
    return {"configured": bool(k), "masked": mask_key(k),
            "source": "env CWA_API_KEY" if os.getenv("CWA_API_KEY") else ("weather.json" if k else None)}


# ---------------------------------------------------------------------------
# peaks
# ---------------------------------------------------------------------------

def load_peaks(path: Path = PEAKS_PATH) -> list[dict]:
    try:
        return json.loads(path.read_text("utf-8")).get("peaks", [])
    except (OSError, ValueError):
        return []


def find_peak(text: Optional[str], peaks: Optional[list[dict]] = None) -> Optional[dict]:
    """Exact name, else the longest peak name contained in `text`
    (e.g. event "玉山主峰 2 日" → 玉山), 百岳 before 小百岳."""
    if not text:
        return None
    peaks = load_peaks() if peaks is None else peaks
    t = text.strip().replace("主峰", "")
    for p in peaks:
        if p["name"] == text.strip() or p["name"] == t:
            return p
    hits = [p for p in peaks if p["name"] in text or p["name"] in t]
    if not hits:
        return None
    return max(hits, key=lambda p: (len(p["name"]), p.get("baiyue", False), p["elevation_m"]))


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# CWA parsing
# ---------------------------------------------------------------------------

def _num(v) -> Optional[float]:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _first_num(values: dict, prefer: tuple[str, ...]) -> Optional[float]:
    for k in prefer:
        if k in values:
            x = _num(values.get(k))
            if x is not None:
                return x
    return None


def _locations(doc: dict) -> list[dict]:
    ds = (doc.get("cwaopendata") or doc).get("Dataset") or {}
    locs = ds.get("Locations") or {}
    if isinstance(locs, list):
        out = []
        for l in locs:
            out += l.get("Location") or []
        return out
    v = locs.get("Location") or []
    return v if isinstance(v, list) else [v]


def _series(loc: dict) -> dict[str, list[dict]]:
    out = {}
    for el in loc.get("WeatherElement") or []:
        rows = []
        for t in el.get("Time") or []:
            v = t.get("ElementValue") or {}
            if isinstance(v, list):
                v = v[0] if v else {}
            rows.append({"t": t.get("DataTime"), "start": t.get("StartTime"), "end": t.get("EndTime"), "v": v})
        out[el.get("ElementName")] = rows
    return out


def _head(loc: dict) -> Optional[dict]:
    ps = (loc.get("ParameterSet") or {}).get("Parameter") or {}
    if isinstance(ps, list):
        ps = next((p for p in ps if p.get("ParameterName") == "id"), ps[0] if ps else {})
    lat, lon = _num(loc.get("Latitude")), _num(loc.get("Longitude"))
    name = loc.get("LocationName")
    if not name or lat is None or lon is None:
        return None
    return {"id": ps.get("ParameterValue"), "name": name, "lat": lat, "lon": lon}


def parse_cwa(doc: dict) -> dict:
    """Compact a CWA mountain document (either dataset) into
    {issued, locations: [{id, name, lat, lon, hourly: [{t, temp, dew, rh}],
    blocks: [{start, end, temp, dew, rh, tmax, tmin}]}]}."""
    issued = ((doc.get("cwaopendata") or {}).get("Sent")) or None
    locs = []
    for loc in _locations(doc):
        h = _head(loc)
        if h is None:
            continue
        s = _series(loc)
        temp = {r["t"]: r["v"] for r in s.get("溫度", []) if r["t"]}
        dew = {r["t"]: r["v"] for r in s.get("露點溫度", []) if r["t"]}
        rh = {r["t"]: r["v"] for r in s.get("相對濕度", []) if r["t"]}
        hourly = [{"t": t,
                   "temp": _first_num(v, ("Temperature",)),
                   "dew": _first_num(dew.get(t, {}), ("DewPoint",)),
                   "rh": _first_num(rh.get(t, {}), ("RelativeHumidity",))}
                  for t, v in temp.items()]
        by = {}
        for name, key, prefer in (("平均溫度", "temp", ("Temperature",)),
                                  ("平均露點溫度", "dew", ("DewPoint",)),
                                  ("平均相對濕度", "rh", ("RelativeHumidity",)),
                                  ("最高溫度", "tmax", ("MaxTemperature",)),
                                  ("最低溫度", "tmin", ("MinTemperature",))):
            for r in s.get(name, []):
                if not r["start"]:
                    continue
                b = by.setdefault(r["start"], {"start": r["start"], "end": r["end"]})
                b[key] = _first_num(r["v"], prefer)
        locs.append({**h, "hourly": hourly, "blocks": sorted(by.values(), key=lambda b: b["start"])})
    return {"issued": issued, "locations": locs}


def match_location(locs: list[dict], name: Optional[str], lat: Optional[float],
                   lon: Optional[float], max_km: float = CWA_MATCH_KM) -> Optional[dict]:
    if name:
        n = name.replace("主峰", "")
        for l in locs:
            if l["name"] in (name, n):
                return {**l, "match": "name", "distance_km": 0.0}
    if lat is not None and lon is not None and locs:
        best = min(locs, key=lambda l: haversine_km(lat, lon, l["lat"], l["lon"]))
        d = haversine_km(lat, lon, best["lat"], best["lon"])
        if d <= max_km:
            return {**best, "match": "nearest", "distance_km": d}
    return None


def _ts(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=TZ)


def _dates(date: dt.date, days: int) -> set[dt.date]:
    return {date + dt.timedelta(days=i) for i in range(max(1, days))}


def _conditions(temps, rhs, dews) -> Optional[dict]:
    temps = [x for x in temps if x is not None]
    if not temps:
        return None
    t = mean(temps)
    rh = [x for x in rhs if x is not None]
    dw = [x for x in dews if x is not None]
    if rh:
        r = mean(rh)
    elif dw:
        r = rh_from_dew_point(t, mean(dw))
    else:
        return None
    return {"temp_c": t, "rh_pct": r, "dew_c": mean(dw) if dw else dew_point(t, r)["dew_c"], "n": len(temps)}


def cwa_day_conditions(loc: dict, date: dt.date, days: int = 1) -> Optional[dict]:
    """Daytime (06–18) mean over the event days. Hourly rows first; else the
    06–18 blocks of the weekly product."""
    want = _dates(date, days)
    rows = [r for r in loc.get("hourly", []) if r.get("t")
            and _ts(r["t"]).astimezone(TZ).date() in want and _ts(r["t"]).astimezone(TZ).hour in DAY_HOURS]
    c = _conditions([r["temp"] for r in rows], [r["rh"] for r in rows], [r["dew"] for r in rows])
    if c:
        cover = {_ts(r["t"]).astimezone(TZ).date() for r in rows}
        if cover >= want:
            return {**c, "product": "hourly"}
    blocks = [b for b in loc.get("blocks", []) if b.get("start")
              and _ts(b["start"]).astimezone(TZ).date() in want and _ts(b["start"]).astimezone(TZ).hour == 6]
    temps = [b.get("temp") if b.get("temp") is not None else
             (mean([b["tmax"], b["tmin"]]) if b.get("tmax") is not None and b.get("tmin") is not None else None)
             for b in blocks]
    c2 = _conditions(temps, [b.get("rh") for b in blocks], [b.get("dew") for b in blocks])
    if c2:
        return {**c2, "product": "weekly"}
    return None


# ---------------------------------------------------------------------------
# Open-Meteo parsing
# ---------------------------------------------------------------------------

def merged(h: dict, key: str) -> Optional[list]:
    """One hourly column of an Open-Meteo answer: `key` itself, else (a
    multi-model request answers `key_<model>`) each hour's mean over the
    CLIM_MODELS columns that have a value there."""
    if h.get(key) is not None:
        return h[key]
    cols = [h[f"{key}_{m}"] for m in CLIM_MODELS if isinstance(h.get(f"{key}_{m}"), list)]
    if not cols:
        return None
    out = []
    for i in range(max(len(c) for c in cols)):
        v = [float(c[i]) for c in cols if i < len(c) and c[i] is not None]
        out.append(mean(v) if v else None)
    return out


def parse_open_meteo(js: dict, dates: set[dt.date], hours=DAY_HOURS) -> Optional[dict]:
    h = js.get("hourly") or {}
    times = h.get("time") or []
    temps, rhs, dews, press = [], [], [], []
    cols = {key: merged(h, key) for key in ("temperature_2m", "relative_humidity_2m", "dew_point_2m", "surface_pressure")}
    for i, t in enumerate(times):
        ts = dt.datetime.fromisoformat(t)
        if ts.date() not in dates or ts.hour not in hours:
            continue
        for arr, key in ((temps, "temperature_2m"), (rhs, "relative_humidity_2m"),
                         (dews, "dew_point_2m"), (press, "surface_pressure")):
            vals = cols[key]
            if vals and i < len(vals) and vals[i] is not None:
                arr.append(float(vals[i]))
    c = _conditions(temps, rhs, dews)
    if not c:
        return None
    return {**c, "grid_elevation_m": js.get("elevation"),
            "pressure_hpa": mean(press) if press else None}


# ---------------------------------------------------------------------------
# hourly rows (per-segment, time-of-day heat)
# ---------------------------------------------------------------------------

HOURLY_EDGE_H = 1.5       # a clock this far past the first / last row still takes that row


def _local_key(t: str) -> Optional[str]:
    """Any feed timestamp → naive local 'YYYY-MM-DDTHH:MM' (UTC+8)."""
    try:
        ts = dt.datetime.fromisoformat(t)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo:
        ts = ts.astimezone(TZ).replace(tzinfo=None)
    return ts.strftime("%Y-%m-%dT%H:%M")


def _hour_row(t, temp, rh, dew) -> Optional[dict]:
    key = _local_key(t)
    if key is None or temp is None or (rh is None and dew is None):
        return None
    if dew is None:
        dew = dew_point(temp, rh)["dew_c"]
    if rh is None:
        rh = rh_from_dew_point(temp, dew)
    return {"t": key, "temp_c": float(temp), "rh_pct": float(rh), "dew_c": float(dew)}


def _in_window(rows: list[dict], dates: set[dt.date]) -> list[dict]:
    """Rows on the event days plus the day after (a race can run past midnight)."""
    keep = dates | {max(dates) + dt.timedelta(days=1)}
    out = [r for r in rows if r and dt.date.fromisoformat(r["t"][:10]) in keep]
    return sorted(out, key=lambda r: r["t"])


def cwa_hourly_rows(loc: dict, dates: set[dt.date]) -> list[dict]:
    return _in_window([_hour_row(r.get("t"), r.get("temp"), r.get("rh"), r.get("dew"))
                       for r in loc.get("hourly") or []], dates)


def open_meteo_hourly_rows(js: dict, dates: set[dt.date]) -> list[dict]:
    h = js.get("hourly") or {}

    def at(key, i):
        v = h.get(key) or []
        return float(v[i]) if i < len(v) and v[i] is not None else None
    return _in_window([_hour_row(t, at("temperature_2m", i), at("relative_humidity_2m", i), at("dew_point_2m", i))
                       for i, t in enumerate(h.get("time") or [])], dates)


def hourly_at(rows: list[dict], when: dt.datetime) -> Optional[dict]:
    """Conditions at a local clock time: linear between the two bracketing
    rows (CWA's day-2/3 rows are 3-hourly), temperature and dew point
    interpolated and RH rebuilt from them (the exact Magnus inverse). Up to
    HOURLY_EDGE_H past either end the edge row is used; beyond → None."""
    if not rows:
        return None
    pts = [(dt.datetime.fromisoformat(r["t"]), r) for r in rows]
    edge = dt.timedelta(hours=HOURLY_EDGE_H)
    if when <= pts[0][0]:
        return dict(pts[0][1]) if pts[0][0] - when <= edge else None
    if when >= pts[-1][0]:
        return dict(pts[-1][1]) if when - pts[-1][0] <= edge else None
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if t0 <= when <= t1:
            w = (when - t0).total_seconds() / max(1.0, (t1 - t0).total_seconds())
            temp = a["temp_c"] + w * (b["temp_c"] - a["temp_c"])
            dew = a["dew_c"] + w * (b["dew_c"] - a["dew_c"])
            return {"t": when.strftime("%Y-%m-%dT%H:%M"), "temp_c": temp, "dew_c": dew,
                    "rh_pct": rh_from_dew_point(temp, dew)}
    return None


def lapse(temp_c: float, from_m: Optional[float], to_m: Optional[float]) -> float:
    if from_m is None or to_m is None:
        return temp_c
    return temp_c + LAPSE_C_PER_M * (to_m - from_m)


def climatology_from(years: list[dict], elevation_m: Optional[float]) -> Optional[dict]:
    """Combine per-year archive results (each from parse_open_meteo) into one
    climatology, temperature corrected to `elevation_m`."""
    ok = [y for y in years if y]
    if not ok:
        return None
    temps = [lapse(y["temp_c"], y.get("grid_elevation_m"), elevation_m) for y in ok]
    return {"temp_c": mean(temps), "rh_pct": mean(y["rh_pct"] for y in ok),
            "grid_elevation_m": ok[0].get("grid_elevation_m"), "years": len(ok),
            "lapse_corrected": elevation_m is not None and ok[0].get("grid_elevation_m") is not None}


# ---------------------------------------------------------------------------
# fetching (httpx) + cache
# ---------------------------------------------------------------------------

def _http_get(url: str, params: dict, timeout: float) -> dict:
    import httpx
    r = httpx.get(url, params=params, timeout=timeout, follow_redirects=True)
    if r.status_code in (401, 403):
        raise RuntimeError(f"HTTP {r.status_code}（授權碼無效）")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


def fetch_cwa(dataset: str, key: Optional[str], *, cache_dir: Optional[Path] = None,
              max_age_h: float = CWA_MAX_AGE_H, now: Optional[dt.datetime] = None,
              get: Callable = _http_get) -> dict:
    """Compact CWA doc for one dataset, from the disk cache when < max_age_h
    old, else downloaded (the whole file) and re-compacted."""
    now = now or dt.datetime.now(TZ)
    cache_dir = cache_dir or home()
    path = cache_dir / f"cwa_{dataset}.json"
    cached = None
    try:
        cached = json.loads(path.read_text("utf-8"))
        age = (now - dt.datetime.fromisoformat(cached["fetched_at"])).total_seconds() / 3600
        if age < max_age_h:
            return {**cached, "cache": "hit"}
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    if not key:
        if cached:
            return {**cached, "cache": "stale"}
        raise RuntimeError("未設定 CWA 授權碼")
    try:
        raw = get(CWA_URL.format(id=dataset),
                  {"Authorization": key, "downloadType": "WEB", "format": "JSON"}, 45.0)
    except Exception:
        if cached:
            return {**cached, "cache": "stale"}
        raise
    doc = {**parse_cwa(raw), "dataset": dataset, "fetched_at": now.isoformat()}
    if not doc["locations"]:
        raise RuntimeError("CWA 回應沒有任何地點")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), "utf-8")
    return {**doc, "cache": "miss"}


# ---------------------------------------------------------------------------
# climatology (SP-210): the month around the race date, 10 years, 3 models
# ---------------------------------------------------------------------------

def _same_day(date: dt.date, year: int) -> dt.date:
    try:
        return date.replace(year=year)
    except ValueError:                    # 29 Feb
        return date.replace(year=year, day=28)


def clim_windows(date: dt.date, days: int = 1, today: Optional[dt.date] = None,
                 years: int = CLIM_YEARS, half: int = CLIM_HALF_DAYS) -> list[tuple[dt.date, dt.date, bool]]:
    """(first day, last day, whole?) of each past year's window: the race date
    ±`half` days (plus the other event days) in each of the `years` years
    before the race year. A window past the archive's newest day is cut there
    (whole = False); one with nothing left is dropped."""
    if today is None:
        from backend.engine.localtime import today_local
        today = today_local()
    newest = today - dt.timedelta(days=ARCHIVE_LAG_DAYS)
    out = []
    for back in range(1, years + 1):
        mid = _same_day(date, date.year - back)
        lo = mid - dt.timedelta(days=half)
        hi = mid + dt.timedelta(days=half + max(1, days) - 1)
        if min(hi, newest) >= lo:
            out.append((lo, min(hi, newest), hi <= newest))
    return out


def diurnal_profile(answers: list[tuple[dict, set[dt.date]]]) -> Optional[list[dict]]:
    """Mean temperature and RH of each local clock hour 0–23 over every archive
    hour on the windows' days (answers = [(archive answer, its days)]); None
    unless all 24 hours have data."""
    acc = {hr: ([], []) for hr in range(24)}
    for js, days in answers:
        h = js.get("hourly") or {}
        T, RH, D = (merged(h, k) or [] for k in ("temperature_2m", "relative_humidity_2m", "dew_point_2m"))
        for i, t in enumerate(h.get("time") or []):
            ts = dt.datetime.fromisoformat(t)
            temp = T[i] if i < len(T) else None
            if ts.date() not in days or temp is None:
                continue
            rh = RH[i] if i < len(RH) else None
            if rh is None and i < len(D) and D[i] is not None:
                rh = rh_from_dew_point(temp, D[i])
            if rh is not None:
                acc[ts.hour][0].append(float(temp))
                acc[ts.hour][1].append(float(rh))
    if any(not v[0] for v in acc.values()):
        return None
    return [{"hour": hr, "temp_c": mean(v[0]), "rh_pct": mean(v[1]), "n": len(v[0])} for hr, v in sorted(acc.items())]


def climatology_hourly_rows(profile: list[dict], dates: set[dt.date], offset_c: float = 0.0) -> list[dict]:
    """The 24-hour profile laid on the event days plus the day after (the same
    hours each day), temperature moved by the lapse offset and RH kept — as
    climatology_from treats the daytime mean."""
    keep = sorted(dates | {max(dates) + dt.timedelta(days=1)})
    return [_hour_row(f"{d.isoformat()}T{p['hour']:02d}:00", p["temp_c"] + offset_c, p["rh_pct"], None)
            for d in keep for p in profile]


def clim_point(lat: float, lon: float, elevation_m: Optional[float]) -> tuple:
    """The queried point (and cache key): 0.01° (~1 km) and 10 m, like route_weather."""
    return round(lat, 2), round(lon, 2), None if elevation_m is None else round(elevation_m / 10.0) * 10.0


def clim_cache_path(cache_dir: Path, point: tuple, date: dt.date, days: int) -> Path:
    z = "dem" if point[2] is None else f"{point[2]:.0f}"
    return cache_dir / "climatology" / f"{point[0]:.2f}_{point[1]:.2f}_{z}_{date.isoformat()}_{max(1, days)}d.json"


def fetch_climatology(lat: float, lon: float, elevation_m: Optional[float], date: dt.date, days: int = 1, *,
                      today: Optional[dt.date] = None, get: Callable = _http_get,
                      cache_dir: Optional[Path] = None) -> dict:
    """Climatology of the race days at a place: climatology_from's fields plus
    `profile` (diurnal_profile), `offset_c` (its lapse shift), `failed` (years
    that did not answer) and `cache`. From the disk cache, else one archive
    call per year (CLIM_WORKERS in parallel, the three models in each call).
    Saved only when every year answered over its whole window; past years do
    not change, so the file never expires. Raises RuntimeError when no year
    answered."""
    cache_dir = cache_dir or home()
    pt = clim_point(lat, lon, elevation_m)
    path = clim_cache_path(cache_dir, pt, date, days)
    try:
        doc = json.loads(path.read_text("utf-8"))
        if doc.get("version") == CLIM_VERSION:
            return {**doc["result"], "cache": "hit"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    wins = clim_windows(date, days, today)
    if not wins:
        raise RuntimeError(_("沒有可用的歷史年份"))

    def one(w):
        params = {"latitude": pt[0], "longitude": pt[1], "start_date": w[0].isoformat(), "end_date": w[1].isoformat(),
                  "timezone": "auto", "hourly": CLIM_HOURLY, "models": ",".join(CLIM_MODELS)}
        if pt[2] is not None:
            params["elevation"] = pt[2]
        try:
            return get(OM_ARCHIVE, params, 15.0), None
        except Exception as e:           # noqa: BLE001 — a year that fails is left out
            return None, str(e)[:80]
    with ThreadPoolExecutor(max_workers=min(CLIM_WORKERS, len(wins))) as ex:
        got = list(ex.map(one, wins))
    years, answers, errs = [], [], []
    for (lo, hi, _whole), (js, err) in zip(wins, got):
        if js is None:
            errs.append(err)
            continue
        window = {lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)}
        y = parse_open_meteo(js, window)
        if y is None:
            errs.append("")
        years.append(y)
        answers.append((js, window))
    c = climatology_from(years, elevation_m)
    if c is None:
        raise RuntimeError("; ".join(e for e in errs if e) or "沒有資料")
    result = {**c, "profile": diurnal_profile(answers), "offset_c": lapse(0.0, c["grid_elevation_m"], elevation_m),
              "failed": len(errs)}
    if not errs and all(w[2] for w in wins):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": CLIM_VERSION, "models": list(CLIM_MODELS), "result": result}), "utf-8")
    return {**result, "cache": "miss"}


def race_conditions(*, date: dt.date, days: int = 1, lat: Optional[float] = None,
                    lon: Optional[float] = None, elevation_m: Optional[float] = None,
                    name: Optional[str] = None, today: Optional[dt.date] = None,
                    key: Optional[str] = None, get: Callable = _http_get,
                    cache_dir: Optional[Path] = None, use_cwa: bool = True) -> dict:
    """Run the provider chain. Returns {provider, label, values|None, tried,
    location, fetched_at}."""
    cache_dir = cache_dir or home()
    from backend.engine.localtime import today_local
    today = today or today_local()
    lead = (date - today).days
    tried = []
    loc = {"name": name, "lat": lat, "lon": lon, "elevation_m": elevation_m}
    now = dt.datetime.now(TZ).isoformat(timespec="seconds")

    want = _dates(date, days)

    def done(provider, values, extra=None, hourly=None):
        v = {"altitude_m": elevation_m, "temp_c": values["temp_c"], "rh_pct": values["rh_pct"],
             "dew_c": values.get("dew_c", dew_point(values["temp_c"], values["rh_pct"])["dew_c"])}
        # hourly rows from an hourly forecast (CWA 3-day, Open-Meteo) or the
        # climatology's 24-hour profile; weekly blocks have none → one heat value
        return {"provider": provider, "label": _(PROVIDER_LABEL[provider]), "values": v, "tried": tried,
                "location": loc, "fetched_at": now, "detail": extra or {}, "lead_days": lead,
                "hourly": hourly or None}

    # 1. CWA
    if not use_cwa:
        tried.append({"provider": "cwa", "ok": False, "reason": "未使用"})
    elif lead < 0 or lead > 6:
        tried.append({"provider": "cwa", "ok": False, "reason": f"日期不在一週預報範圍（還有 {lead} 天）"})
    else:
        key = key if key is not None else load_key()
        if not key and not any((cache_dir / f"cwa_{d}.json").exists() for d in (CWA_HOURLY, CWA_WEEKLY)):
            tried.append({"provider": "cwa", "ok": False, "reason": "未設定 CWA 授權碼"})
        else:
            for ds_id, prov in ((CWA_HOURLY, "cwa_hourly"), (CWA_WEEKLY, "cwa_weekly")):
                if ds_id == CWA_HOURLY and lead + max(1, days) - 1 > 2:
                    tried.append({"provider": prov, "ok": False, "reason": "超出三天預報範圍"})
                    continue
                try:
                    doc = fetch_cwa(ds_id, key, cache_dir=cache_dir, get=get)
                    m = match_location(doc["locations"], name, lat, lon)
                    if m is None:
                        tried.append({"provider": prov, "ok": False, "reason": f"{CWA_MATCH_KM:g} km 內沒有 CWA 登山預報點"})
                        break
                    c = cwa_day_conditions(m, date, days)
                    if c is None:
                        tried.append({"provider": prov, "ok": False, "reason": "預報沒有涵蓋這些日期"})
                        continue
                    tried.append({"provider": prov, "ok": True})
                    now = doc.get("fetched_at", now)
                    return done(prov, c, {"cwa_location": m["name"], "cwa_id": m.get("id"),
                                          "match": m["match"], "distance_km": m["distance_km"],
                                          "issued": doc.get("issued"), "cache": doc.get("cache")},
                                cwa_hourly_rows(m, want) if c.get("product") == "hourly" else None)
                except Exception as e:          # noqa: BLE001 — fall through to the next provider
                    tried.append({"provider": prov, "ok": False, "reason": str(e)[:120]})
    if lat is None or lon is None:
        tried.append({"provider": "open_meteo", "ok": False, "reason": "沒有座標"})
        return {"provider": "manual", "label": _(PROVIDER_LABEL["manual"]), "values": None, "tried": tried,
                "location": loc, "fetched_at": None, "lead_days": lead, "hourly": None}
    end = date + dt.timedelta(days=max(1, days) - 1)
    # 2. Open-Meteo forecast
    if 0 <= lead and (end - today).days < OM_HORIZON_DAYS:
        try:
            # one extra day when the horizon allows: the hourly rows for a race
            # that runs past midnight (the daytime mean still uses `want` only)
            q_end = end + dt.timedelta(days=1) if (end - today).days + 1 < OM_HORIZON_DAYS else end
            params = {"latitude": lat, "longitude": lon,
                      "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,surface_pressure",
                      "timezone": "auto", "start_date": date.isoformat(), "end_date": q_end.isoformat()}
            if elevation_m is not None:
                params["elevation"] = elevation_m
            js = get(OM_FORECAST, params, 10.0)
            c = parse_open_meteo(js, want)
            if c:
                tried.append({"provider": "open_meteo", "ok": True})
                return done("open_meteo", c, {"grid_elevation_m": c.get("grid_elevation_m"),
                                              "attribution": ATTRIBUTION}, open_meteo_hourly_rows(js, want))
            tried.append({"provider": "open_meteo", "ok": False, "reason": "回應沒有這些日期"})
        except Exception as e:               # noqa: BLE001
            tried.append({"provider": "open_meteo", "ok": False, "reason": str(e)[:120]})
    else:
        tried.append({"provider": "open_meteo", "ok": False, "reason": f"超出 {OM_HORIZON_DAYS} 天預報範圍"})
    # 3. climatology: the month around the race date, 10 years, 3 models (fetch_climatology)
    try:
        c = fetch_climatology(lat, lon, elevation_m, date, days, today=today, get=get, cache_dir=cache_dir)
    except Exception as e:                   # noqa: BLE001
        tried.append({"provider": "climatology", "ok": False, "reason": str(e)[:120] or "沒有資料"})
        return {"provider": "manual", "label": _(PROVIDER_LABEL["manual"]), "values": None, "tried": tried,
                "location": loc, "fetched_at": None, "lead_days": lead, "hourly": None}
    tried.append({"provider": "climatology", "ok": True})
    rows = climatology_hourly_rows(c["profile"], want, c["offset_c"]) if c.get("profile") else None
    return done("climatology", c, {"years": c["years"], "models": list(CLIM_MODELS),
                                   "window_days": 2 * CLIM_HALF_DAYS + 1, "partial": c["failed"] > 0,
                                   "grid_elevation_m": c["grid_elevation_m"], "lapse_corrected": c["lapse_corrected"],
                                   "cache": c["cache"], "attribution": ATTRIBUTION}, rows)


def activities_conditions(js: dict, windows: list[tuple[dt.datetime, dt.datetime]]) -> Optional[dict]:
    """Mean T / RH over the archive hours overlapping each activity window
    (local naive datetimes)."""
    h = js.get("hourly") or {}
    times = [dt.datetime.fromisoformat(t) for t in h.get("time") or []]
    T, RH = h.get("temperature_2m") or [], h.get("relative_humidity_2m") or []
    temps, rhs, used = [], [], 0
    for a, b in windows:
        hit = False
        for i, t in enumerate(times):
            if a - dt.timedelta(minutes=30) <= t <= b + dt.timedelta(minutes=30):
                if i < len(T) and T[i] is not None and i < len(RH) and RH[i] is not None:
                    temps.append(float(T[i]))
                    rhs.append(float(RH[i]))
                    hit = True
        used += hit
    if not temps:
        return None
    return {"temp_c": mean(temps), "rh_pct": mean(rhs), "activities": used,
            "grid_elevation_m": js.get("elevation")}


def fetch_activities_conditions(lat: float, lon: float, windows, get: Callable = _http_get) -> Optional[dict]:
    if not windows:
        return None
    lo = min(a for a, _ in windows).date()
    hi = max(b for _, b in windows).date()
    js = get(OM_ARCHIVE, {"latitude": lat, "longitude": lon, "start_date": lo.isoformat(),
                          "end_date": hi.isoformat(), "timezone": "auto",
                          "hourly": "temperature_2m,relative_humidity_2m"}, 15.0)
    return activities_conditions(js, windows)
