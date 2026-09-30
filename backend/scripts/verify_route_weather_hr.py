"""
Independent check of the 路線 page's per-effort weather and peaks
(docs/spec/route-progress.spec.md, Weather / Metrics).

For one segment, every effort's max HR (and max 30 s power) is recomputed
from the raw .wko4 samples with plain loops, and one effort's weather from a
direct Open-Meteo archive query (no cache, no batching code), then both are
compared with what GET /api/v1/routes/{id} serves. Nothing here imports
backend.engine.routes or backend.engine.route_weather: the formulas are
written out again from the spec.

    python -m backend.scripts.verify_route_weather_hr --base http://127.0.0.1:8091 [--id sXXXX]

The pure functions below are also what backend/tests/test_route_weather.py
runs against the API with the network mocked.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from typing import Callable, Optional

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


# ---------------------------------------------------------------------------
# peaks from raw samples
# ---------------------------------------------------------------------------

def max_hr_plain(hr: list, a: int, b: int) -> Optional[float]:
    """Max HR over raw samples a+1 .. b."""
    best = None
    for i in range(a + 1, b + 1):
        h = hr[i] if i < len(hr) else None
        if h is None or (isinstance(h, float) and math.isnan(h)):
            continue
        if best is None or h > best:
            best = h
    return best


def max_p30_plain(t: list, power: list, a: int, b: int, window: float = 30.0,
                  cover: float = 0.8) -> Optional[float]:
    """Brute force: for every end sample e in a+1 .. b, the start s is the
    latest sample with t_s <= t_e − 30; the window s+1 .. e counts when s >= a
    and power covers >= 80 % of t_e − t_s. Mean = Σ p·dt / Σ dt over samples
    with power, dt = t − previous valid t."""
    n = len(t)
    prev_t = [None] * n
    last = None
    for i in range(n):
        prev_t[i] = last
        if t[i] is not None:
            last = t[i]
    best = None
    for e in range(a + 1, b + 1):
        if t[e] is None:
            continue
        s = None
        for j in range(e - 1, -1, -1):
            if t[j] is not None and t[j] <= t[e] - window:
                s = j
                break
        if s is None or s < a:
            continue
        num = den = 0.0
        for i in range(s + 1, e + 1):
            if t[i] is None or prev_t[i] is None or power[i] is None:
                continue
            d = t[i] - prev_t[i]
            if d > 0:
                num += power[i] * d
                den += d
        if den > 0 and den >= cover * (t[e] - t[s]):
            v = num / den
            if best is None or v > best:
                best = v
    return best


# ---------------------------------------------------------------------------
# weather by a direct archive query
# ---------------------------------------------------------------------------

def http_get(url: str, params: dict, timeout: float = 20.0) -> dict:
    import httpx
    r = httpx.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def weather_plain(get: Callable, lat: float, lon: float, start_iso: str, t0: float, t1: float,
                  elev_m: Optional[float]) -> Optional[dict]:
    """Weather for one effort from the archive, one single-point query per
    day at (lat, lon) with elevation=elev_m (Open-Meteo downscales to it):
    the hourly rows within 30 min of [start + t0, start + t1], mean T and RH,
    dew point by Magnus (a = 18.678, b = 257.14, d = 234.5), Hadley = T °F + dew °F."""
    s = dt.datetime.fromisoformat(start_iso).replace(tzinfo=None)
    a = s + dt.timedelta(seconds=t0)
    b = s + dt.timedelta(seconds=t1)
    lo = a - dt.timedelta(minutes=30)
    hi = b + dt.timedelta(minutes=30)
    temps, rhs, grid_elev = [], [], None
    day = lo.date()
    while day <= hi.date():
        params = {"latitude": str(lat), "longitude": str(lon), "start_date": day.isoformat(),
                  "end_date": day.isoformat(), "timezone": "auto",
                  "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m"}
        if elev_m is not None:
            params["elevation"] = f"{elev_m:.0f}"
        js = get(ARCHIVE, params, 20.0)
        if grid_elev is None:
            grid_elev = js.get("elevation")
        h = js["hourly"]
        for i, ts in enumerate(h["time"]):
            when = dt.datetime.fromisoformat(ts)
            if lo <= when <= hi and h["temperature_2m"][i] is not None and h["relative_humidity_2m"][i] is not None:
                temps.append(float(h["temperature_2m"][i]))
                rhs.append(float(h["relative_humidity_2m"][i]))
        day += dt.timedelta(days=1)
    if not temps:
        return None
    t = sum(temps) / len(temps)
    rh = sum(rhs) / len(rhs)
    A, B, D = 18.678, 257.14, 234.5
    g = math.log(max(rh, 0.01) / 100.0 * math.exp((A - t / D) * (t / (B + t))))
    dew = B * g / (A - g)
    hadley = (t * 1.8 + 32.0) + (dew * 1.8 + 32.0)
    return {"temp_c": t, "rh_pct": rh, "dew_c": dew, "hadley": hadley,
            "archive_elev_m": grid_elev, "hours": len(temps), "lat": lat, "lon": lon}


def compare_weather(api_wx: dict, mine: dict) -> list[str]:
    """Differences beyond the API's rounding (0.1 °C, 1 %, 1 °F)."""
    bad = []
    for k, tol in (("temp_c", 0.051), ("rh_pct", 0.51), ("dew_c", 0.06), ("hadley", 0.6)):
        if api_wx.get(k) is None or abs(api_wx[k] - mine[k]) > tol:
            bad.append(f"{k}: api {api_wx.get(k)} vs plain {mine[k]:.3f}")
    return bad


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------

def _raw_channels(file: str):
    """Raw HR / power / time of one activity with the approved data corrections."""
    from backend.api.wko5views import ATHLETE_DIR
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.dataset import Dataset
    from backend.files.wko4_file import read_wko4
    global _DS
    try:
        ds = _DS
    except NameError:
        ds = _DS = Dataset(ATHLETE_DIR, config=EngineConfig.load())
    ch = read_wko4(ds.dir / file).channels
    t = list(ch["elapsedtime"].values)

    def vals(name):
        c = ch.get(name)
        if not c:
            return None
        v = c.values
        if name in ("heartrate", "power"):
            v = ds.corrections.apply(file, name, t, v)
        return [None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x) for x in v]

    return t, vals("heartrate"), vals("power"), vals("latitude"), vals("longitude"), vals("_elevation") or vals("elevation")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8091")
    ap.add_argument("--id", default=None, help="segment id; default: the climb with the most efforts")
    ap.add_argument("--effort", type=int, default=None, help="which effort gets the archive query (default: newest with weather)")
    args = ap.parse_args(argv)
    import httpx
    if args.id is None:
        rows = httpx.get(f"{args.base}/api/v1/routes", params={"kind": "climb"}, timeout=30).json()["rows"]
        args.id = rows[0]["id"]
    d = httpx.get(f"{args.base}/api/v1/routes/{args.id}", timeout=30).json()
    print(f"{d['name']} ({args.id}) — {len(d['efforts'])} efforts")
    ok_hr = ok_p = n_p = 0
    for e in d["efforts"]:
        t, hr, pw, *_ = _raw_channels(e["file"])
        mine = max_hr_plain(hr or [], e["raw_i0"], e["raw_i1"]) if hr else None
        same = (mine is None and e.get("max_hr") is None) or (mine is not None and e.get("max_hr") == round(mine))
        ok_hr += same
        line = f"  {e['start'][:16]}  max HR api {e.get('max_hr')} plain {mine}  {'OK' if same else 'DIFF'}"
        if pw:
            n_p += 1
            p = max_p30_plain(t, pw, e["raw_i0"], e["raw_i1"])
            sp = (p is None and e.get("max_p30") is None) or (p is not None and e.get("max_p30") == round(p))
            ok_p += sp
            line += f" | 30 s api {e.get('max_p30')} plain {None if p is None else round(p, 1)} {'OK' if sp else 'DIFF'}"
        print(line)
    print(f"max HR: {ok_hr}/{len(d['efforts'])} agree" + (f"; max 30 s power: {ok_p}/{n_p} agree" if n_p else ""))
    with_wx = [e for e in d["efforts"] if e.get("wx")]
    if not with_wx:
        print("no effort has weather yet")
        return 1
    e = with_wx[args.effort] if args.effort is not None else with_wx[-1]
    t, _hr, _pw, lat, lon, el = _raw_channels(e["file"])
    a, b = e["raw_i0"], e["raw_i1"]
    w = e["wx"]
    mine = weather_plain(http_get, w["lat"], w["lon"], e["start"], t[a], t[b], w["elev_m"])
    bad = compare_weather(w, mine)
    print(f"weather {e['start'][:16]} point {w['lat']}, {w['lon']}, {w['elev_m']} m (batch cell {w['cell']})")
    print(f"  api   T {w['temp_c']} RH {w['rh_pct']} dew {w['dew_c']} Hadley {w['hadley']}")
    print(f"  plain T {mine['temp_c']:.2f} RH {mine['rh_pct']:.1f} dew {mine['dew_c']:.2f} Hadley {mine['hadley']:.1f} "
          f"({mine['hours']} h)  {'OK' if not bad else 'DIFF ' + '; '.join(bad)}")
    # the point against the raw samples: position and elevation, and what the rounding costs
    ok = [i for i in range(a, b + 1) if lat[i] is not None and lon[i] is not None and (lat[i], lon[i]) != (0, 0)]
    rl = sum(lat[i] for i in ok) / len(ok)
    ro = sum(lon[i] for i in ok) / len(ok)
    els = [el[i] for i in ok if el and el[i] is not None]
    re_ = sum(els) / len(els) if els else None
    off = math.hypot((rl - w["lat"]) * 111320, (ro - w["lon"]) * 111320 * math.cos(math.radians(rl)))
    print(f"  point vs raw-sample mean: {off:.0f} m apart, elevation {w['elev_m']} vs {re_ and round(re_, 1)} m")
    exact = weather_plain(http_get, round(rl, 4), round(ro, 4), e["start"], t[a], t[b], re_)
    if exact:
        print(f"  archive at the unrounded raw-sample point: T {exact['temp_c']:.2f} (Δ {exact['temp_c'] - w['temp_c']:+.2f} °C)")
    return 0 if (ok_hr == len(d["efforts"]) and ok_p == n_p and not bad) else 2


if __name__ == "__main__":
    sys.exit(main())
