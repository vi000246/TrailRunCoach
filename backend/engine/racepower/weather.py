"""
Race-day and training weather for the environment multiplier
(docs/research/superpower-calculator.md §3.3).

Provider chain for race day (first success wins; every value stays editable).
SP-552: every tenant starts with CWA (its open data may be used commercially,
with the source named); Open-Meteo fills only what CWA has not got — past 7
days, outside Taiwan, the climatology, heights:
  1. CWA 中央氣象署 mountain forecasts (needs a free 授權碼):
       F-B0053-035 登山三天3小時 (hourly day 1, 3-hourly to day 3)
       F-B0053-033 登山一週日夜 (06–18 / 18–06 blocks, 7 days)
     Whole-file downloads (~12 / ~7.5 MB) → compacted and cached on disk,
     refreshed when ≥ 3 h old.
  2. CWA 鄉鎮天氣預報 (SP-234; same 授權碼, same whole-file download + cache):
       F-D0047-089 臺灣未來3天 (temperature / dew point / RH hourly on day 1,
                   3-hourly to day 3; 3-hour PoP)
       F-D0047-091 臺灣未來1週 (12-hour blocks; 12-hour PoP)
     (CWA 產品說明文件「預報-精緻化天氣預報-368 鄉鎮預報」, 2024-12-10,
     opendata.cwa.gov.tw/opendatadoc/Forecast/F-D0047-001_093.pdf; IDs from
     data.gov.tw datasets 9307 / 9308.) Each point is the 鄉鎮市區公所, usually
     in the valley or on the plain, and the file has no elevation: the point's
     and (when not given) the race's height come from Open-Meteo's elevation
     API (Copernicus GLO-90, cached for good) and the temperature is moved
     by −0.65 °C / 100 m to the race elevation, RH kept (as SP-210).
     Was after Open-Meteo (owner decision 2026-10-06, until a station
     comparison); SP-552 puts it first (CWA_TOWN_FIRST, one switch back).
  3. Open-Meteo forecast (no key, ≤ 16 days).
  4. Open-Meteo archive climatology (SP-210): the month centred on the race
     date (±15 days) in each of the last 10 years, at the target elevation
     (Open-Meteo downscales; −6.5 °C/km from the answer's elevation when it
     differs), each hour the mean of three reanalyses (ERA5, ERA5-Land,
     ECMWF IFS 9 km). Gives the daytime mean and a 24-hour profile (the mean
     of each clock hour), so far-off races still get per-segment heat.
     Cached on disk for good (past years do not change). The daytime mean is
     the single value; a plan run outside 06–18 widens it to its own hours on
     course from the profile (climatology_to, SP-257; no further call).
  5. Manual values.

Shared (SP-552): the CWA files and the town heights are one copy for the whole
site (site_dir(): the server root, not a tenant folder), fetched with the
site's 授權碼 (site_key(): env CWA_API_KEY, else the owner's weather.json),
one download at a time per file (_CWA_LOCKS) and parsed once per process
(cwa_doc()); a tenant never sees the key itself.

Rain (SP-249): the forecasts' probability of precipitation (CWA 3- / 12-hour
PoP, Open-Meteo's hourly PoP and amount) go out as `rain` rows; rain_alert()
turns the rows in the race window into a reminder. It never changes the
predicted time (docs/research/wet-muddy-terrain.md §4: no study gives a
wet-trail slowdown to apply).

Wind (SP-251): Open-Meteo's hourly wind_speed_10m / wind_gusts_10m /
apparent_temperature and the CWA mountain 3-day product's 風速 (m/s, 3-hourly)
go out as `wind` rows; cold.py turns them into the wind chill at each segment
and the 冷風 reminder. The 鄉鎮 product's wind is left out on purpose: its point
is the 公所 in the valley, not the ridge; the weekly products give only 12-hour
spans and the climatology none. Never changes the predicted time.

Sun (SP-254): `sun` = sunrise / sunset of the event days and the day after,
from Open-Meteo's `daily=sunrise,sunset` when the forecast answered, else
NOAA's solar formula (sun_times; no network). night.py marks the segments run
in the dark.

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
import threading
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


SITE_DIR = None  # fixed folder (tests); None = the server root (SP-552), else HOME when a test fixed that


def site_dir() -> Path:
    """Where the CWA files and the town heights live (SP-552): one copy for the whole site, in
    the server root (accounts.server_root(): the owner's folder unless TRC_SERVER_ROOT says
    otherwise), never in a tenant folder — every tenant reads the same download."""
    if SITE_DIR is not None:
        return Path(SITE_DIR)
    if HOME is not None:
        return Path(HOME)
    from backend import accounts
    return accounts.server_root()
PEAKS_PATH = Path(__file__).resolve().parents[2] / "data" / "baiyue.json"

CWA_URL = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/{id}"
CWA_HOURLY, CWA_WEEKLY = "F-B0053-035", "F-B0053-033"
CWA_MAX_AGE_H = 3.0               # a file this old is downloaded again (SP-552: once for the whole site)
CWA_TOWN_FIRST = True             # SP-552: 鄉鎮 before the Open-Meteo forecast (False: the 2026-10-06 order)
CWA_MATCH_KM = 5.0
CWA_TOWN_HOURLY, CWA_TOWN_WEEKLY = "F-D0047-089", "F-D0047-091"   # 鄉鎮預報 全臺 3 天 / 1 週 (SP-234)
CWA_TOWN_MATCH_KM = 20.0          # 推估: beyond this the nearest 鄉鎮公所 is another valley / coast;
                                  # also keeps races outside Taiwan off the product
ELEV_CACHE = "cwa_town_elevation.json"
OM_FORECAST = "https://api.open-meteo.com/v1/forecast"
OM_ELEVATION = "https://api.open-meteo.com/v1/elevation"   # Copernicus GLO-90 DEM, ≤ 100 points per call
# SP-249 rain reminder: docs/research/wet-muddy-terrain.md §5 #1 (thresholds 推估; kept as proposed, owner 2026-10-06)
RAIN_POP_PCT = 50.0               # any hour of the race window with this probability of precipitation
RAIN_MM = 5.0                     # or this much forecast precipitation summed over the window
# SP-485 (docs/research/ui-hint-audit.md §5 R1, owner 2026-10-13 Q1 第 5 題; 推估): the reminder goes on top of the
# result only when the rain can change the time or the kit — PoP ≥ RAIN_POP_PCT and ≥ RAIN_TOP_MM, or
# ≥ RAIN_TOP_MM_ALONE whatever the PoP; road twice the amounts. A lighter one is a line on the 環境 card.
RAIN_TOP_MM = 5.0
RAIN_TOP_MM_ALONE = 10.0
RAIN_ROAD_FACTOR = 2.0
OM_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OM_HORIZON_DAYS = 16
LAPSE_C_PER_M = -0.0065
DAY_HOURS = range(6, 18)          # daytime 06:00–17:59 local
TZ = dt.timezone(dt.timedelta(hours=8))    # CWA (Taiwan) timestamps; Open-Meteo answers in the location's zone ("auto")
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
# SP-552: CWA open data (政府資料開放授權條款) may be used commercially when the source is named
CWA_ATTRIBUTION = N_("天氣資料來源：中央氣象署")
CWA_TOWN_ATTRIBUTION = N_("天氣資料來源：中央氣象署；海拔：Open-Meteo.com（CC BY 4.0）")
# climatology (SP-210; the numbers in the module docstring)
CLIM_YEARS = 10                   # 推估: 10 years halve the year-to-year noise of 5, warming bias ≈ −0.15 °C
CLIM_HALF_DAYS = 15               # ±15 days = the month centred on the race date, not the calendar month:
                                  # 臺北 cools 2.7 °C from Oct to Nov, so 31 Oct would be ~1.3 °C off
CLIM_MODELS = ("era5", "era5_land", "ecmwf_ifs")    # IFS starts 2017: older years average the other two
CLIM_HOURLY = "temperature_2m,relative_humidity_2m,dew_point_2m"
CLIM_WORKERS = 4                  # parallel archive calls (well under Open-Meteo's 600 / min)
CLIM_VERSION = 2                  # bump to ignore older cache files
ARCHIVE_LAG_DAYS = 6              # the archive's newest days are still missing

PROVIDER_LABEL = {"cwa_hourly": N_("中央氣象署 登山三天預報"), "cwa_weekly": N_("中央氣象署 登山一週預報"),
                  "cwa_town_hourly": N_("中央氣象署 鄉鎮三天預報"), "cwa_town_weekly": N_("中央氣象署 鄉鎮一週預報"),
                  "open_meteo": N_("Open-Meteo 預報"), "climatology": N_("近 10 年同月平均（Open-Meteo 歷史資料）"),
                  "manual": N_("手動 / 預設")}


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------

def load_key(path: Optional[Path] = None) -> Optional[str]:
    """The CWA authorisation code: the owner's (env CWA_API_KEY or weather.json). Every other
    tenant has none (SP-384, auth-and-demo plan §5): its forecasts fall back to Open-Meteo."""
    from backend import tenancy
    if path is None and not tenancy.is_owner():
        return None
    path = path or key_path()
    from backend.settings.env_secrets import secret_env
    env = secret_env("CWA_API_KEY")          # or CWA_API_KEY_FILE (a Docker secret, SP-387)
    if env:
        return env.strip()
    try:
        return (json.loads(path.read_text("utf-8")).get("cwa_api_key") or "").strip() or None
    except (OSError, ValueError):
        return None


def save_key(key: str, path: Optional[Path] = None) -> None:
    from backend import tenancy
    if path is None and not tenancy.is_owner():
        raise PermissionError("the CWA key is the owner's (weather.key)")
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


def site_key() -> Optional[str]:
    """The key the site fetches the shared CWA files with (SP-552), whoever asked: env
    CWA_API_KEY (or its _FILE), else the owner's weather.json. Only the downloads use it; a
    tenant other than the owner still never reads or sets it (load_key, save_key, key_status)."""
    from backend import tenancy
    from backend.settings.env_secrets import secret_env
    env = secret_env("CWA_API_KEY")
    if env:
        return env.strip()
    path = Path(KEY_PATH) if KEY_PATH is not None else tenancy.owner().shared / "weather.json"
    try:
        return (json.loads(path.read_text("utf-8")).get("cwa_api_key") or "").strip() or None
    except (OSError, ValueError, AttributeError):
        return None


def key_status(path: Optional[Path] = None) -> dict:
    k = load_key(path)                   # None for anyone but the owner (SP-384)
    # `shared`: the site has a key, so this tenant's race weather comes from CWA too (SP-552);
    # a yes / no only, never the key or its mask
    return {"configured": bool(k), "masked": mask_key(k),
            "source": "env CWA_API_KEY" if os.getenv("CWA_API_KEY") else ("weather.json" if k else None),
            "shared": bool(k) or bool(site_key())}


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
    # mountain points carry an id parameter; 鄉鎮 points a Geocode
    return {"id": ps.get("ParameterValue") or loc.get("Geocode"), "name": name, "lat": lat, "lon": lon}


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
        # probability of precipitation (SP-249): 「3小時降雨機率」 / 「12小時降雨機率」, start–end spans
        pop = [{"start": r["start"], "end": r["end"], "pop": _first_num(r["v"], ("ProbabilityOfPrecipitation",))}
               for name, rows in s.items() if name and name.endswith("降雨機率")
               for r in rows if r["start"] and r["end"]]
        # wind (SP-251): the element whose values carry `WindSpeed` (m/s; 「風速」 in the 3-day product's
        # 3-hourly rows, with BeaufortScale) — CWA 產品說明文件「預報-精緻化天氣預報-休閒旅遊預報」
        # (F-B0053-001…073, 2024-12-10). Only rows with a DataTime (the weekly 12-hour 「最大風速」 spans
        # are left out); CWA gives no gusts.
        wind = [{"t": r["t"], "ms": _first_num(r["v"], ("WindSpeed",))}
                for rows in s.values() for r in rows
                if r["t"] and isinstance(r["v"], dict) and "WindSpeed" in r["v"]]
        locs.append({**h, "hourly": hourly, "blocks": sorted(by.values(), key=lambda b: b["start"]),
                     "pop": sorted((p for p in pop if p["pop"] is not None), key=lambda p: p["start"]),
                     "wind": sorted((w for w in wind if w["ms"] is not None), key=lambda w: w["t"])})
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


# ---------------------------------------------------------------------------
# rain (SP-249): rows {start, end, pop_pct, mm} in naive local time
# ---------------------------------------------------------------------------

def _rain_window(rows: list[dict], dates: set[dt.date]) -> list[dict]:
    keep = dates | {max(dates) + dt.timedelta(days=1)}
    out = [r for r in rows if r and (r["pop_pct"] is not None or r["mm"] is not None)
           and dt.date.fromisoformat(r["start"][:10]) in keep]
    return sorted(out, key=lambda r: r["start"])


def cwa_rain_rows(loc: dict, dates: set[dt.date]) -> list[dict]:
    """The CWA PoP spans (3-hour or 12-hour) on the event days; CWA gives no amount."""
    rows = []
    for p in loc.get("pop") or []:
        a, b = _local_key(p.get("start")), _local_key(p.get("end"))
        if a and b:
            rows.append({"start": a, "end": b, "pop_pct": p.get("pop"), "mm": None})
    return _rain_window(rows, dates)


def open_meteo_rain_rows(js: dict, dates: set[dt.date]) -> list[dict]:
    """Open-Meteo's hourly `precipitation` / `precipitation_probability` are
    of the preceding hour, so the row at t covers t − 1 h … t."""
    h = js.get("hourly") or {}
    P, PP = h.get("precipitation") or [], h.get("precipitation_probability") or []
    rows = []
    for i, t in enumerate(h.get("time") or []):
        key = _local_key(t)
        if key is None:
            continue
        end = dt.datetime.fromisoformat(key)
        mm = _num(P[i]) if i < len(P) else None
        pop = _num(PP[i]) if i < len(PP) else None
        rows.append({"start": (end - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                     "end": key, "pop_pct": pop, "mm": mm})
    return _rain_window(rows, dates)


# ---------------------------------------------------------------------------
# wind (SP-251): rows {t, temp_c, wind_kmh, gust_kmh, apparent_c} in naive local time
# ---------------------------------------------------------------------------

def cwa_wind_rows(loc: dict, dates: set[dt.date]) -> list[dict]:
    """The CWA mountain product's wind (m/s → km/h) on the event days, each row with the
    product's own temperature at that time (hourly_at on its temperature rows; None when
    it has none there). CWA has no gusts."""
    temps = cwa_hourly_rows(loc, dates)
    rows = []
    for w in loc.get("wind") or []:
        key = _local_key(w.get("t"))
        if key is None or w.get("ms") is None:
            continue
        h = hourly_at(temps, dt.datetime.fromisoformat(key)) if temps else None
        rows.append({"t": key, "temp_c": h["temp_c"] if h else None, "wind_kmh": float(w["ms"]) * 3.6,
                     "gust_kmh": None, "apparent_c": None})
    return _in_window(rows, dates)


def open_meteo_wind_rows(js: dict, dates: set[dt.date]) -> list[dict]:
    """Open-Meteo's hourly wind_speed_10m / wind_gusts_10m (km/h, the API's default unit) with
    temperature_2m and apparent_temperature of the same hour; hours without a wind speed are
    left out (no wind columns → [])."""
    h = js.get("hourly") or {}

    def at(key, i):
        v = h.get(key) or []
        return _num(v[i]) if i < len(v) else None
    rows = []
    for i, t in enumerate(h.get("time") or []):
        key, w = _local_key(t), at("wind_speed_10m", i)
        if key is None or w is None:
            continue
        rows.append({"t": key, "temp_c": at("temperature_2m", i), "wind_kmh": w,
                     "gust_kmh": at("wind_gusts_10m", i), "apparent_c": at("apparent_temperature", i)})
    return _in_window(rows, dates)


# ---------------------------------------------------------------------------
# sunrise / sunset (SP-254): rows {date, sunrise, sunset, src}, naive local 'YYYY-MM-DDTHH:MM'
# ---------------------------------------------------------------------------

SUN_ZENITH_DEG = 90.833           # refraction + the sun's radius (NOAA)


def sun_times(lat: float, lon: float, date: dt.date, tz_h: float = 8.0) -> Optional[tuple[dt.datetime, dt.datetime]]:
    """(sunrise, sunset) as naive local times (UTC + tz_h) by NOAA's 「General Solar Position
    Calculations」 (NOAA Global Monitoring Laboratory, gml.noaa.gov/grad/solcalc/solareqns.PDF:
    the fractional-year series for the equation of time and the declination, zenith 90.833°);
    about a minute off at Taiwan's latitudes. None when the sun does not rise or set that day."""
    doy = date.timetuple().tm_yday
    g = 2.0 * math.pi / 365.0 * (doy - 1)           # at 12:00 the hour term is 0
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    x = math.cos(math.radians(SUN_ZENITH_DEG)) / (math.cos(la) * math.cos(decl)) - math.tan(la) * math.tan(decl)
    if not -1.0 <= x <= 1.0:
        return None
    ha = math.degrees(math.acos(x))
    day0 = dt.datetime(date.year, date.month, date.day) + dt.timedelta(hours=tz_h)
    rise = day0 + dt.timedelta(minutes=720.0 - 4.0 * (lon + ha) - eqtime)
    sset = day0 + dt.timedelta(minutes=720.0 - 4.0 * (lon - ha) - eqtime)
    return rise, sset


def sun_rows(lat: Optional[float], lon: Optional[float], dates: set[dt.date],
             js: Optional[dict] = None) -> Optional[list[dict]]:
    """Sunrise and sunset of the event days and the day after (a race can run into the next
    morning): Open-Meteo's `daily=sunrise,sunset` when the forecast answer has them (`js`),
    else sun_times() — UTC+8 unless the answer gives its utc_offset_seconds. None without
    coordinates."""
    if lat is None or lon is None or not dates:
        return None
    keep = sorted(dates | {max(dates) + dt.timedelta(days=1)})
    om = {}
    d = (js or {}).get("daily") or {}
    rises, sets = d.get("sunrise") or [], d.get("sunset") or []
    for i, day in enumerate(d.get("time") or []):
        rise = rises[i] if i < len(rises) else None
        sset = sets[i] if i < len(sets) else None
        a, b = _local_key(rise) if rise else None, _local_key(sset) if sset else None
        if a and b:
            om[day[:10]] = (a, b)
    tz_h = float((js or {}).get("utc_offset_seconds", 8 * 3600)) / 3600.0
    out = []
    for day in keep:
        if day.isoformat() in om:
            a, b = om[day.isoformat()]
            out.append({"date": day.isoformat(), "sunrise": a, "sunset": b, "src": "open_meteo"})
            continue
        st = sun_times(float(lat), float(lon), day, tz_h)
        if st:
            out.append({"date": day.isoformat(), "sunrise": st[0].strftime("%Y-%m-%dT%H:%M"),
                        "sunset": st[1].strftime("%Y-%m-%dT%H:%M"), "src": "noaa"})
    return out or None


Window = tuple[dt.datetime, dt.datetime]


def race_window(date: Optional[str], start_time: Optional[str], days: int,
                duration_s: Optional[float]) -> Optional[list[Window]]:
    """The race's clock windows (owner decision 2026-10-06): start → start +
    duration for a one-day race with a start time; otherwise daytime
    (DAY_HOURS, 06:00–18:00 local) of each event day — day 1 from the start
    time when that is earlier (a pre-dawn 百岳 start)."""
    try:
        d = dt.date.fromisoformat(str(date)[:10])
    except (TypeError, ValueError):
        return None
    n = max(1, days or 1)
    day0 = dt.datetime(d.year, d.month, d.day)
    start = None
    if start_time:
        try:
            h, m = (int(x) for x in str(start_time).split(":")[:2])
            start = day0.replace(hour=h, minute=m)
        except ValueError:
            start = None
    if n == 1 and start is not None and duration_s:
        return [(start, start + dt.timedelta(seconds=float(duration_s)))]
    lo, hi = dt.timedelta(hours=DAY_HOURS.start), dt.timedelta(hours=DAY_HOURS.stop)
    wins = [(day0 + dt.timedelta(days=i) + lo, day0 + dt.timedelta(days=i) + hi) for i in range(n)]
    if start is not None and start < wins[0][0]:
        wins[0] = (start, wins[0][1])
    return wins


def rain_alert(rows: Optional[list[dict]], window, kind: str = "trail") -> Optional[dict]:
    """The SP-249 reminder for the rows overlapping the window(s) (one
    (start, end) or a list of them): alert when any row's PoP ≥ RAIN_POP_PCT
    or their summed amount ≥ RAIN_MM. None when there is no rain data in the
    window (nothing is shown). Never touches the time. `kind` "road" gets a
    road wording (no poles, no technical sections).
    `top` (SP-485, R1): the reminder belongs on top of the result — PoP ≥ RAIN_POP_PCT and the amount
    ≥ RAIN_TOP_MM, or the amount ≥ RAIN_TOP_MM_ALONE (road: × RAIN_ROAD_FACTOR). Without an amount (CWA
    gives PoP only) R1 cannot tell a drizzle from a downpour: `top` follows `alert`, as before."""
    if not rows or not window:
        return None
    wins = [window] if isinstance(window, tuple) else list(window)
    hit = []
    for r in rows:
        try:
            s, e = dt.datetime.fromisoformat(r["start"]), dt.datetime.fromisoformat(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if any(s < b and e > a for a, b in wins):
            hit.append(r)
    pops = [float(r["pop_pct"]) for r in hit if r.get("pop_pct") is not None]
    mms = [float(r["mm"]) for r in hit if r.get("mm") is not None]
    if not pops and not mms:
        return None
    max_pop = max(pops) if pops else None
    total = sum(mms) if mms else None
    alert = (max_pop is not None and max_pop >= RAIN_POP_PCT) or (total is not None and total >= RAIN_MM)
    f = RAIN_ROAD_FACTOR if kind == "road" else 1.0
    if total is None:
        top = alert
    else:
        top = (max_pop is not None and max_pop >= RAIN_POP_PCT and total >= RAIN_TOP_MM * f) \
            or total >= RAIN_TOP_MM_ALONE * f
    if not alert:
        msg = None
    elif kind == "road":
        msg = _("預報有雨：路面濕滑，轉彎和下坡注意抓地，跌倒風險較高")
    else:
        msg = _("預報有雨：下坡和技術路段可能比預估慢，跌倒風險較高；建議用杖、注意鞋底抓地")
    return {"alert": alert, "top": bool(alert and top), "max_pop_pct": max_pop, "total_mm": total, "rows": len(hit),
            "message": msg}


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


_CWA_LOCKS: dict[str, threading.Lock] = {}     # one download at a time per file (SP-552)
_CWA_LOCKS_GUARD = threading.Lock()
_CWA_MEMO: dict[str, tuple] = {}               # path -> ((mtime_ns, size), compact doc): parsed once


def _cwa_lock(path: Path) -> threading.Lock:
    with _CWA_LOCKS_GUARD:
        return _CWA_LOCKS.setdefault(str(path), threading.Lock())


def cwa_doc(path: Path) -> Optional[dict]:
    """The compact CWA file at `path`, parsed once per process while the file is unchanged
    (SP-552: every tenant and every point of a course read the same object — never mutate it)."""
    try:
        st = path.stat()
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    hit = _CWA_MEMO.get(str(path))
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    _CWA_MEMO[str(path)] = (sig, doc)
    return doc


def _age_h(doc: Optional[dict], now: dt.datetime) -> Optional[float]:
    try:
        return (now - dt.datetime.fromisoformat(doc["fetched_at"])).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        return None


def _write_atomic(path: Path, text: str) -> None:
    """tmp + replace: a reader in another thread never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)


def fetch_cwa(dataset: str, key: Optional[str], *, cache_dir: Optional[Path] = None,
              max_age_h: float = CWA_MAX_AGE_H, now: Optional[dt.datetime] = None,
              get: Callable = _http_get) -> dict:
    """Compact CWA doc for one dataset, from the disk cache when < max_age_h
    old, else downloaded (the whole file) and re-compacted. One copy for the whole
    site (SP-552, site_dir()); while one request downloads a file, the others get the
    old copy at once (or wait for the new one when there is none)."""
    now = now or dt.datetime.now(TZ)
    cache_dir = cache_dir or site_dir()
    path = cache_dir / f"cwa_{dataset}.json"
    cached = cwa_doc(path)
    age = _age_h(cached, now)
    if cached is not None and age is not None and age < max_age_h:
        return {**cached, "cache": "hit"}
    if age is None:
        cached = None
    if not key:
        if cached:
            return {**cached, "cache": "stale"}
        raise RuntimeError("未設定 CWA 授權碼")
    lock = _cwa_lock(path)
    if not lock.acquire(blocking=cached is None):
        return {**cached, "cache": "stale"}            # someone is downloading it right now
    try:
        again = cwa_doc(path)                          # downloaded while this one waited
        a2 = _age_h(again, now)
        if again is not None and a2 is not None and a2 < max_age_h:
            return {**again, "cache": "hit"}
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
        _write_atomic(path, json.dumps(doc, ensure_ascii=False))
        try:
            st = path.stat()
            _CWA_MEMO[str(path)] = ((st.st_mtime_ns, st.st_size), doc)
        except OSError:
            pass
        return {**doc, "cache": "miss"}
    finally:
        lock.release()


def _elev_key(lat: float, lon: float) -> str:
    return f"{lat:.4f},{lon:.4f}"


def elevations(points: list[tuple[float, float]], *, cache_dir: Optional[Path] = None,
               get: Callable = _http_get) -> list[Optional[float]]:
    """Ground height (m) of each (lat, lon) from Open-Meteo's elevation API,
    one call for the points not in the cache file yet (ELEV_CACHE; terrain
    does not change, so it never expires). A point the API cannot answer → None.
    One file for the whole site (SP-552)."""
    cache_dir = cache_dir or site_dir()
    path = cache_dir / ELEV_CACHE
    try:
        cache = json.loads(path.read_text("utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    miss = [p for p in points if _elev_key(*p) not in cache]
    if miss:
        js = get(OM_ELEVATION, {"latitude": ",".join(f"{p[0]:.4f}" for p in miss),
                                "longitude": ",".join(f"{p[1]:.4f}" for p in miss)}, 10.0)
        got = js.get("elevation") or []
        added = False
        for i, p in enumerate(miss):
            z = _num(got[i]) if i < len(got) else None
            if z is not None:
                cache[_elev_key(*p)] = z
                added = True
        if added:
            _write_atomic(path, json.dumps(cache))
    return [cache.get(_elev_key(*p)) for p in points]


def town_lapsed(loc: dict, c: dict, rows: list[dict], to_m: Optional[float]) -> tuple[dict, list[dict]]:
    """A 鄉鎮 forecast moved from its point (`loc["elevation_m"]`) to `to_m`:
    temperature by LAPSE_C_PER_M, RH kept, dew point rebuilt (SP-210's rule)."""
    off = lapse(0.0, loc["elevation_m"], to_m)
    t = c["temp_c"] + off
    vals = {"temp_c": t, "rh_pct": c["rh_pct"], "dew_c": dew_point(t, c["rh_pct"])["dew_c"], "altitude_m": to_m}
    out = [_hour_row(r["t"], r["temp_c"] + off, r["rh_pct"], None) for r in rows]
    return vals, [r for r in out if r]


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


CLIM_TO_STEP_MIN = 10            # SP-257: the on-course windows are read in 10-minute pieces (06:00 / 18:00 on a mark)


def _clock_profile(rows: list[dict]) -> Optional[dict[int, tuple[float, float]]]:
    """{hour of day: (temp_c, dew_c)} of climatology rows (the same 24-hour profile on every day,
    climatology_hourly_rows), each hour's mean over the days; None unless all 24 hours are there."""
    acc: dict[int, list[tuple[float, float]]] = {}
    for r in rows or []:
        x = _hour_row(r.get("t"), r.get("temp_c"), r.get("rh_pct"), r.get("dew_c"))
        if x:
            acc.setdefault(int(x["t"][11:13]), []).append((x["temp_c"], x["dew_c"]))
    if len(acc) < 24:
        return None
    return {h: (mean(v[0] for v in vs), mean(v[1] for v in vs)) for h, vs in acc.items()}


def climatology_to(rows: list[dict], windows: list[Window], temp_c: float,
                   rh_pct: float) -> Optional[dict]:
    """SP-257 (docs/research/night-and-sleep.md §3 G5 / §4.2 單 4): the climatology's single
    race-day value over the hours actually on course. `temp_c` / `rh_pct` = its daytime mean
    (`values`, DAY_HOURS 06:00–17:59); `windows` = the local (start, end) clock spans on course;
    `rows` = its /weather hourly rows (the 24-hour profile on each event day). Each
    CLIM_TO_STEP_MIN piece of the windows takes the daytime mean when its clock is in DAY_HOURS —
    so the daytime part is what it was — else the profile at its midpoint, read by the hour of
    day (the profile repeats each day, so a day past the rows reads the same hours), temperature
    and dew point linear between hours and RH rebuilt (as hourly_at). Returns the time-weighted
    {temp_c, rh_pct, dew_c, day_temp_c, day_rh_pct, hours, night_h}; None when no piece is outside
    DAY_HOURS (the value stays as it is), or without rows / windows / a full profile."""
    prof = _clock_profile(rows) if windows else None
    if prof is None:
        return None
    step = dt.timedelta(minutes=CLIM_TO_STEP_MIN)
    w_t = w_rh = tot = night = 0.0
    for a, b in windows:
        t = a
        while t < b:
            mark = t.replace(second=0, microsecond=0) - dt.timedelta(minutes=t.minute % CLIM_TO_STEP_MIN)
            e = min(b, mark + step)
            mid, w = t + (e - t) / 2, (e - t).total_seconds()
            if mid.hour in DAY_HOURS:
                temp, rh = temp_c, rh_pct
            else:
                (t0, d0), (t1, d1) = prof[mid.hour], prof[(mid.hour + 1) % 24]
                f = (mid.minute * 60 + mid.second + mid.microsecond / 1e6) / 3600.0
                temp, dew = t0 + f * (t1 - t0), d0 + f * (d1 - d0)
                rh = rh_from_dew_point(temp, dew)
                night += w
            w_t, w_rh, tot = w_t + w * temp, w_rh + w * rh, tot + w
            t = e
    if night <= 0 or tot <= 0:
        return None
    t_on, rh_on = w_t / tot, w_rh / tot
    return {"temp_c": t_on, "rh_pct": rh_on, "dew_c": dew_point(t_on, rh_on)["dew_c"],
            "day_temp_c": temp_c, "day_rh_pct": rh_pct, "hours": tot / 3600.0, "night_h": night / 3600.0}


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
                    cache_dir: Optional[Path] = None, use_cwa: bool = True, forecast_only: bool = False) -> dict:
    """Run the provider chain. Returns {provider, label, values|None, tried,
    location, fetched_at}. `cache_dir` (tests) holds every file; else the CWA files and
    heights are the site's (site_dir(), SP-552) and the climatology the tenant's (home()).
    `forecast_only` (SP-553, the points along a course): no climatology step — its ten archive
    calls per point are what the call cap is for; such a point is `manual` (no rows)."""
    cwa_dir = cache_dir or site_dir()
    clim_dir = cache_dir or home()
    from backend.engine.localtime import today_local
    today = today or today_local()
    lead = (date - today).days
    tried = []
    loc = {"name": name, "lat": lat, "lon": lon, "elevation_m": elevation_m}
    now = dt.datetime.now(TZ).isoformat(timespec="seconds")

    want = _dates(date, days)

    def done(provider, values, extra=None, hourly=None, rain=None, wind=None, sun_js=None, at=None, fetched=None):
        v = {"altitude_m": values.get("altitude_m", elevation_m), "temp_c": values["temp_c"], "rh_pct": values["rh_pct"],
             "dew_c": values.get("dew_c", dew_point(values["temp_c"], values["rh_pct"])["dew_c"])}
        # hourly rows from an hourly forecast (CWA 3-day, Open-Meteo) or the
        # climatology's 24-hour profile; weekly blocks have none → one heat value
        return {"provider": provider, "label": _(PROVIDER_LABEL[provider]), "values": v, "tried": tried,
                "location": loc, "fetched_at": fetched or now, "detail": extra or {}, "lead_days": lead,
                "hourly": hourly or None, "rain": rain or None,   # rain: SP-249 rows (forecasts only)
                "wind": wind or None,   # wind: SP-251 rows (CWA mountain 3-day, Open-Meteo forecast)
                # SP-254: sunrise / sunset of the event days (Open-Meteo's daily, else NOAA's formula)
                "sun": sun_rows(*(at or (lat, lon)), want, sun_js)}

    cwa_key = []                       # the key, read once (only when a CWA step runs)

    def get_key():
        if not cwa_key:
            # SP-552: the site's key for every tenant (the downloads are shared; the key is not shown)
            cwa_key.append(key if key is not None else site_key())
        return cwa_key[0]

    def have_files(ids) -> bool:
        return any((cwa_dir / f"cwa_{d}.json").exists() for d in ids)

    # 1. CWA
    if not use_cwa:
        tried.append({"provider": "cwa", "ok": False, "reason": "未使用"})
    elif lead < 0 or lead > 6:
        tried.append({"provider": "cwa", "ok": False, "reason": f"日期不在一週預報範圍（還有 {lead} 天）"})
    else:
        ckey = get_key()
        if not ckey and not have_files((CWA_HOURLY, CWA_WEEKLY)):
            tried.append({"provider": "cwa", "ok": False, "reason": "未設定 CWA 授權碼"})
        else:
            for ds_id, prov in ((CWA_HOURLY, "cwa_hourly"), (CWA_WEEKLY, "cwa_weekly")):
                if ds_id == CWA_HOURLY and lead + max(1, days) - 1 > 2:
                    tried.append({"provider": prov, "ok": False, "reason": "超出三天預報範圍"})
                    continue
                try:
                    doc = fetch_cwa(ds_id, ckey, cache_dir=cwa_dir, get=get)
                    m = match_location(doc["locations"], name, lat, lon)
                    if m is None:
                        tried.append({"provider": prov, "ok": False, "reason": f"{CWA_MATCH_KM:g} km 內沒有 CWA 登山預報點"})
                        break
                    c = cwa_day_conditions(m, date, days)
                    if c is None:
                        tried.append({"provider": prov, "ok": False, "reason": "預報沒有涵蓋這些日期"})
                        continue
                    tried.append({"provider": prov, "ok": True})
                    return done(prov, c, {"cwa_location": m["name"], "cwa_id": m.get("id"),
                                          "match": m["match"], "distance_km": m["distance_km"],
                                          "issued": doc.get("issued"), "cache": doc.get("cache"),
                                          "attribution": _(CWA_ATTRIBUTION)},
                                cwa_hourly_rows(m, want) if c.get("product") == "hourly" else None,
                                cwa_rain_rows(m, want), cwa_wind_rows(m, want),
                                at=(lat, lon) if lat is not None and lon is not None else (m["lat"], m["lon"]),
                                fetched=doc.get("fetched_at"))
                except Exception as e:          # noqa: BLE001 — fall through to the next provider
                    tried.append({"provider": prov, "ok": False, "reason": str(e)[:120]})
    if lat is None or lon is None:
        tried.append({"provider": "open_meteo", "ok": False, "reason": "沒有座標"})
        tried.append({"provider": "cwa_town", "ok": False, "reason": _("沒有座標")})
        return {"provider": "manual", "label": _(PROVIDER_LABEL["manual"]), "values": None, "tried": tried,
                "location": loc, "fetched_at": None, "lead_days": lead, "hourly": None, "rain": None, "wind": None, "sun": None}
    end = date + dt.timedelta(days=max(1, days) - 1)

    def open_meteo() -> Optional[dict]:
        """Open-Meteo forecast: what CWA has not got (8–16 days out, outside Taiwan, CWA down)."""
        if not (0 <= lead and (end - today).days < OM_HORIZON_DAYS):
            tried.append({"provider": "open_meteo", "ok": False, "reason": f"超出 {OM_HORIZON_DAYS} 天預報範圍"})
            return None
        try:
            # one extra day when the horizon allows: the hourly rows for a race
            # that runs past midnight (the daytime mean still uses `want` only)
            q_end = end + dt.timedelta(days=1) if (end - today).days + 1 < OM_HORIZON_DAYS else end
            params = {"latitude": lat, "longitude": lon,
                      "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,surface_pressure,"
                                "precipitation,precipitation_probability,"          # SP-249
                                "wind_speed_10m,wind_gusts_10m,apparent_temperature",   # SP-251
                      "daily": "sunrise,sunset",                                     # SP-254
                      "timezone": "auto", "start_date": date.isoformat(), "end_date": q_end.isoformat()}
            if elevation_m is not None:
                params["elevation"] = elevation_m
            js = om_forecast(params, get)
            c = parse_open_meteo(js, want)
            if c:
                tried.append({"provider": "open_meteo", "ok": True})
                return done("open_meteo", c, {"grid_elevation_m": c.get("grid_elevation_m"),
                                              "attribution": ATTRIBUTION}, open_meteo_hourly_rows(js, want),
                            open_meteo_rain_rows(js, want), open_meteo_wind_rows(js, want), js)
            tried.append({"provider": "open_meteo", "ok": False, "reason": "回應沒有這些日期"})
        except Exception as e:               # noqa: BLE001
            tried.append({"provider": "open_meteo", "ok": False, "reason": str(e)[:120]})
        return None

    def town() -> Optional[dict]:
        """CWA 鄉鎮預報 (SP-234): the nearest 鄉鎮公所, temperature lapsed to the race elevation."""
        if not use_cwa:
            tried.append({"provider": "cwa_town", "ok": False, "reason": _("未使用")})
            return None
        if lead < 0 or lead > 6:
            tried.append({"provider": "cwa_town", "ok": False, "reason": _("日期不在一週預報範圍（還有 {n} 天）", n=lead)})
            return None
        tkey = get_key()
        if not tkey and not have_files((CWA_TOWN_HOURLY, CWA_TOWN_WEEKLY)):
            tried.append({"provider": "cwa_town", "ok": False, "reason": _("未設定 CWA 授權碼")})
            return None
        for ds_id, prov in ((CWA_TOWN_HOURLY, "cwa_town_hourly"), (CWA_TOWN_WEEKLY, "cwa_town_weekly")):
            if ds_id == CWA_TOWN_HOURLY and lead + max(1, days) - 1 > 2:
                tried.append({"provider": prov, "ok": False, "reason": _("超出三天預報範圍")})
                continue
            try:
                doc = fetch_cwa(ds_id, tkey, cache_dir=cwa_dir, get=get)
                m = match_location(doc["locations"], None, lat, lon, CWA_TOWN_MATCH_KM)
                if m is None:
                    tried.append({"provider": prov, "ok": False,
                                  "reason": _("{km:g} km 內沒有鄉鎮預報點", km=CWA_TOWN_MATCH_KM)})
                    return None
                c = cwa_day_conditions(m, date, days)
                if c is None:
                    tried.append({"provider": prov, "ok": False, "reason": _("預報沒有涵蓋這些日期")})
                    continue
                pts = [(m["lat"], m["lon"])] + ([(lat, lon)] if elevation_m is None else [])
                zs = elevations(pts, cache_dir=cwa_dir, get=get)
                to_m = elevation_m if elevation_m is not None else zs[1]
                if zs[0] is None or to_m is None:
                    tried.append({"provider": prov, "ok": False, "reason": _("查不到預報點或比賽地點的海拔")})
                    return None
                m = {**m, "elevation_m": zs[0]}
                rows = cwa_hourly_rows(m, want) if c.get("product") == "hourly" else []
                vals, rows = town_lapsed(m, c, rows, to_m)
                tried.append({"provider": prov, "ok": True})
                return done(prov, vals, {"cwa_location": m["name"], "cwa_id": m.get("id"), "town": True,
                                         "match": m["match"], "distance_km": m["distance_km"],
                                         "town_elevation_m": zs[0], "target_elevation_m": to_m,
                                         "lapse_m": to_m - zs[0], "offset_c": lapse(0.0, zs[0], to_m),
                                         "issued": doc.get("issued"), "cache": doc.get("cache"),
                                         "attribution": _(CWA_TOWN_ATTRIBUTION)}, rows or None,
                            cwa_rain_rows(m, want), fetched=doc.get("fetched_at"))
            except Exception as e:          # noqa: BLE001 — the weekly file, then the next provider
                tried.append({"provider": prov, "ok": False, "reason": str(e)[:120]})
        return None

    # 2.–3. SP-552: the 鄉鎮 forecast before Open-Meteo (CWA_TOWN_FIRST); the 2026-10-06 order the other way
    for step in ((town, open_meteo) if CWA_TOWN_FIRST else (open_meteo, town)):
        r = step()
        if r is not None:
            return r
    if forecast_only:
        tried.append({"provider": "climatology", "ok": False, "reason": _("沿線的點不查歷史平均")})
        return {"provider": "manual", "label": _(PROVIDER_LABEL["manual"]), "values": None, "tried": tried,
                "location": loc, "fetched_at": None, "lead_days": lead, "hourly": None, "rain": None, "wind": None,
                "sun": None}
    # 4. climatology: the month around the race date, 10 years, 3 models (fetch_climatology)
    try:
        c = fetch_climatology(lat, lon, elevation_m, date, days, today=today, get=get, cache_dir=clim_dir)
    except Exception as e:                   # noqa: BLE001
        tried.append({"provider": "climatology", "ok": False, "reason": str(e)[:120] or "沒有資料"})
        return {"provider": "manual", "label": _(PROVIDER_LABEL["manual"]), "values": None, "tried": tried,
                "location": loc, "fetched_at": None, "lead_days": lead, "hourly": None, "rain": None, "wind": None,
                "sun": sun_rows(lat, lon, want)}
    tried.append({"provider": "climatology", "ok": True})
    rows = climatology_hourly_rows(c["profile"], want, c["offset_c"]) if c.get("profile") else None
    return done("climatology", c, {"years": c["years"], "models": list(CLIM_MODELS),
                                   "window_days": 2 * CLIM_HALF_DAYS + 1, "partial": c["failed"] > 0,
                                   "grid_elevation_m": c["grid_elevation_m"], "lapse_corrected": c["lapse_corrected"],
                                   "cache": c["cache"], "attribution": ATTRIBUTION}, rows)


# ---------------------------------------------------------------------------
# Open-Meteo forecast memo (SP-553): the same point and days within an hour → no new call
# ---------------------------------------------------------------------------

OM_MEMO_TTL_S = 3600.0            # 推估: Open-Meteo's models update hourly at most
OM_MEMO_MAX = 256                 # entries (one per point × days; a few kB each)
_OM_MEMO: dict = {}
_OM_MEMO_LOCK = threading.Lock()


def om_forecast(params: dict, get: Callable = _http_get) -> dict:
    """The Open-Meteo forecast answer for `params`, from a per-process memo for OM_MEMO_TTL_S when
    the real client asks (a test's fake `get` always gets the call)."""
    if get is not _http_get:
        return get(OM_FORECAST, params, 10.0)
    key = tuple(sorted((k, str(v)) for k, v in params.items()))
    now = dt.datetime.now().timestamp()
    with _OM_MEMO_LOCK:
        hit = _OM_MEMO.get(key)
        if hit is not None and now - hit[0] < OM_MEMO_TTL_S:
            return hit[1]
    js = get(OM_FORECAST, params, 10.0)
    with _OM_MEMO_LOCK:
        _OM_MEMO[key] = (now, js)
        while len(_OM_MEMO) > OM_MEMO_MAX:
            _OM_MEMO.pop(next(iter(_OM_MEMO)))
    return js


# ---------------------------------------------------------------------------
# along the course (SP-553): a long course's weather at several points, each section's
# rows read at the clock the plan reaches it (planner / calc / cold pick the rows)
# ---------------------------------------------------------------------------

ROUTE_POINT_KM = 10.0             # 推估: one point per 10 km of course
ROUTE_MIN_KM = 8.0                # 推估: a shorter course keeps its one point
ROUTE_MAX_POINTS = 6              # the cap per race: ≤ 6 Open-Meteo forecast calls (memo above), the CWA
                                  # files shared (SP-552: no call), no climatology (forecast_only)
FORECAST_PROVIDERS = ("cwa_hourly", "cwa_weekly", "cwa_town_hourly", "cwa_town_weekly", "open_meteo")


def route_sections(lat: list, lon: list, ele: list, *, every_km: float = ROUTE_POINT_KM,
                   max_points: int = ROUTE_MAX_POINTS) -> list[dict]:
    """[{i, from_km, to_km, km, lat, lon, elevation_m}]: the course cut into n equal sections
    (n = ⌈length / every_km⌉, at most max_points), each section's weather point the track
    sample nearest its middle. [] when the course is shorter than ROUTE_MIN_KM or has no points
    (the race keeps its one weather point)."""
    n_pts = min(len(lat or []), len(lon or []))
    if n_pts < 2:
        return []
    cum = [0.0]
    for k in range(1, n_pts):
        cum.append(cum[-1] + haversine_km(lat[k - 1], lon[k - 1], lat[k], lon[k]))
    total = cum[-1]
    if total < ROUTE_MIN_KM:
        return []
    n = max(1, min(int(max_points), math.ceil(total / every_km)))
    if n < 2:
        return []
    out, j = [], 0
    for i in range(n):
        a, b = total * i / n, total * (i + 1) / n
        m = (a + b) / 2
        while j + 1 < n_pts and cum[j + 1] <= m:
            j += 1
        k = j + 1 if j + 1 < n_pts and abs(cum[j + 1] - m) < abs(cum[j] - m) else j
        z = ele[k] if ele and k < len(ele) else None
        out.append({"i": i, "from_km": round(a, 3), "to_km": round(b, 3), "km": round(cum[k], 3),
                    "lat": float(lat[k]), "lon": float(lon[k]), "elevation_m": None if z is None else float(z)})
    return out


def route_conditions(sections: list[dict], *, date: dt.date, days: int = 1, today: Optional[dt.date] = None,
                     key: Optional[str] = None, get: Callable = _http_get, cache_dir: Optional[Path] = None,
                     use_cwa: bool = True) -> list[dict]:
    """Each section's forecast (race_conditions at its point, no name match: each point is matched by
    its own coordinates; forecast_only). [{i, from_km, to_km, km, lat, lon, elevation_m, provider,
    label, detail, hourly, rain, wind}] — `elevation_m` is the height the rows refer to (z_ref)."""
    out = []
    for s in sections[:ROUTE_MAX_POINTS]:
        r = race_conditions(date=date, days=days, lat=s["lat"], lon=s["lon"], elevation_m=s.get("elevation_m"),
                            name=None, today=today, key=key, get=get, cache_dir=cache_dir, use_cwa=use_cwa,
                            forecast_only=True)
        d = r.get("detail") or {}
        z = (r.get("values") or {}).get("altitude_m")
        out.append({**{k: s.get(k) for k in ("i", "from_km", "to_km", "km", "lat", "lon")},
                    "elevation_m": z if z is not None else s.get("elevation_m"),
                    "provider": r["provider"], "label": r["label"],
                    "detail": {k: d.get(k) for k in ("cwa_location", "distance_km", "town", "attribution", "cache")
                               if d.get(k) is not None},
                    "hourly": r.get("hourly"), "rain": r.get("rain"), "wind": r.get("wind")})
    return out


def section_for(secs: Optional[list[dict]], start_km: Optional[float], end_km: Optional[float]) -> Optional[dict]:
    """The weather section a stretch of course belongs to: the one holding its middle, else the
    nearest (a course re-cut by the planner can run a few metres past the last section)."""
    if not secs or start_km is None:
        return None
    m = (float(start_km) + float(end_km if end_km is not None else start_km)) / 2
    for s in secs:
        if float(s.get("from_km") or 0.0) <= m <= float(s.get("to_km") or 0.0):
            return s
    return min(secs, key=lambda s: min(abs(m - float(s.get("from_km") or 0.0)), abs(m - float(s.get("to_km") or 0.0))))


def section_rain_rows(secs: list[dict], pts: list[dict], rows: Optional[list[dict]] = None) -> list[dict]:
    """The rain rows the plan actually runs through: each section's own rows (else `rows`, the race's
    one point) over the clock spans of the segments in that section (cold.points). A row is kept
    once per section that runs in it."""
    spans: dict[int, list] = {}
    for p in pts:
        s = section_for(secs, p.get("start_km"), p.get("end_km"))
        if s is not None:
            spans.setdefault(id(s), []).append((p["begin"], p["end"]))
    out = []
    for s in secs:
        sp = spans.get(id(s))
        if not sp:
            continue
        for r in (s.get("rain") or rows or []):
            try:
                a, b = dt.datetime.fromisoformat(r["start"]), dt.datetime.fromisoformat(r["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if any(a < e and b > x for x, e in sp):
                out.append(r)
    return out


def local_rows(secs: list[dict], wind: Optional[list[dict]], hourly: Optional[list[dict]],
               z_ref: Optional[float]) -> Callable[[dict], dict]:
    """For cold.py: a segment point → {wind, hourly, z} of its section (its own rows and the height
    they refer to), else the race's one point (`wind`, `hourly`, `z_ref`)."""
    def hrs(rows):
        return sorted((x for x in (_hour_row(r.get("t"), r.get("temp_c"), r.get("rh_pct"), r.get("dew_c"))
                                   for r in rows or []) if x), key=lambda r: r["t"])
    glob = {"wind": wind or None, "hourly": hrs(hourly), "z": z_ref}
    own = {id(s): {"wind": s.get("wind") or None, "hourly": hrs(s.get("hourly")), "z": s.get("elevation_m")}
           for s in secs}

    def at(p: dict) -> dict:
        s = section_for(secs, p.get("start_km"), p.get("end_km"))
        if s is None:
            return glob
        o = own[id(s)]
        if not o["wind"] and not o["hourly"]:
            return glob
        return o
    return at


# ---------------------------------------------------------------------------
# 疊石 Cairn (SP-553, SP-531 Q2: links both ways, no integration)
# ---------------------------------------------------------------------------

CAIRN_ORIGIN = "https://map.yichlin.com"          # Cairn's only production host (inReachLiveTrack CLAUDE.md)
_CAIRN_RX = None


def cairn_url(text: Optional[str]) -> Optional[str]:
    """The Cairn route link in a plan event's note: Cairn opens a route with `/?gpx=<id>` (its route
    pick, v2/app/src/lib/route-pick.ts) and a shared one with `&share=<token>` (entry-view.ts). Rebuilt
    on CAIRN_ORIGIN from the two ids only — None without one (nothing is shown)."""
    global _CAIRN_RX
    if not text:
        return None
    import re
    from urllib.parse import parse_qs, urlparse
    if _CAIRN_RX is None:
        _CAIRN_RX = re.compile(r"https?://map\.yichlin\.com/[^\s，。；、）)]*", re.I)
    for m in _CAIRN_RX.finditer(text):
        q = parse_qs(urlparse(m.group(0)).query)
        gid = (q.get("gpx") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", gid):
            continue
        tok = (q.get("share") or [""])[0]
        url = f"{CAIRN_ORIGIN}/?gpx={gid}"
        if re.fullmatch(r"[A-Za-z0-9_-]{8,128}", tok):
            url += f"&share={tok}"
        return url
    return None


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
