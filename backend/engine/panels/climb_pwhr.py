"""
越野穩定爬坡 Pw:HR (views kind "climbpwhr", 我的訓練 → 能力). Reference only:
no gate, no unlock.

Every trail run with Stryd power → engine/climb_pwhr.extract_segments (steady
+3…+8 % running climbs ≥ 10 min, first 2 min dropped). Compared like for
like only:
  * the same route — the route clustering of engine/routes.py (index.json
    "routes", ≥ 2 runs); a run done the other way round is its own series
    ("反向"), a run whose direction is mixed is left out;
  * the same power source — Stryd only (engine/power_source.py), never watch
    power, whatever power.accept_watch_power says (Berzosa 2024: Stryd and
    another device disagree on uphill power; the same device is consistent).
Each segment is a point; the line is the 8-week rolling median per route.
Heat context: the heat-band work is not merged, so HR is also shown moved to
Hadley 120 with the athlete's own β (engine/heat.hr_heat_adjust, 推估).
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.engine import climb_pwhr as CP

SERIES_KEY = "climb_pwhr_v1"
WINDOW_DAYS = 56                 # 8-week rolling median
SOURCES_OVERRIDE: Optional[dict] = None   # tests: {"route_index", "weather", "names"}


def is_trail_run(w) -> bool:
    st = (w.sport_type or "").lower()
    if w.sport != "run" or "treadmill" in st or "indoor" in st or "runningtreadmill" in w.tags \
            or "runningindoor" in w.tags:
        return False
    return "runningtrail" in w.tags or st == "trail running"


def _source(ds, w) -> str:
    f = getattr(ds, "power_source", None)
    return f(w) if f is not None else "stryd"


def _ch(ds, w, *names):
    for n in names:
        v = ds.channel(w.idx, n)
        if v is not None:
            return v
    return None


def extract(ds, w) -> dict:
    t = ds.channel(w.idx, "elapsedtime")
    if t is None:
        return {"segments": [], "rejected": [], "reason": "short"}
    d = ds.channel(w.idx, "elapseddistance")
    dist_m = None if d is None else [None if v != v else v * 1000.0 for v in d]
    return CP.extract_segments(list(t), dist_m, _ch(ds, w, "_elevation", "elevation"),
                               _ch(ds, w, "power"), _ch(ds, w, "heartrate"), _ch(ds, w, "cadence"),
                               _ch(ds, w, "speed"))


def _cached(ds, w) -> dict:
    f = getattr(ds, "cached_series", None)
    if f is None:
        return extract(ds, w)
    return f(SERIES_KEY, w, lambda: extract(ds, w)) or {"segments": [], "rejected": [], "reason": None}


def sources() -> dict:
    if SOURCES_OVERRIDE is not None:
        return SOURCES_OVERRIDE
    from backend.engine import route_weather as RW
    from backend.engine.routes import RouteStore
    store = RouteStore()
    acts = (RW.load_activity_weather(store.root).get("activities") or {})
    return {"route_index": store.load_index() or {}, "names": store.names(),
            "weather": {f: v for f, v in acts.items() if v}}


def route_map(route_index: dict, names: dict) -> tuple[dict, dict]:
    """({file: route key}, {route key: name}); routes with ≥ 2 runs; reversed
    runs get "<id>~rev", mixed-direction runs no key."""
    of, name = {}, {}
    for r in route_index.get("routes") or []:
        members = r.get("members") or []
        if len(members) < 2:
            continue
        base = names.get(r["id"]) or r.get("name") or r.get("auto_name") or r["id"]
        dirs = r.get("dirs") or {}
        for f in members:
            d = dirs.get(f, "same")
            if d == "mixed":
                continue
            key = r["id"] if d == "same" else f"{r['id']}~rev"
            of[f] = key
            name[key] = base if d == "same" else f"{base}（反向）"
    return of, name


def _zone(hr: float, aet: Optional[float], lthr: Optional[float]) -> Optional[str]:
    if lthr and hr >= lthr:
        return "≥ LTHR"
    if aet and hr < aet:
        return "< AeT"
    if aet and lthr:
        return "AeT–LTHR"
    return None


def compute(ds, b: float, e: float, params: dict, route_index: Optional[dict] = None,
            weather: Optional[dict] = None, names: Optional[dict] = None) -> dict:
    from backend.engine import heat as HT
    if route_index is None or weather is None or names is None:
        src = sources()
        route_index = src["route_index"] if route_index is None else route_index
        weather = src["weather"] if weather is None else weather
        names = src["names"] if names is None else names
    of, rname = route_map(route_index, names)
    counts = {"trail_runs": 0, "stryd": 0, "watch_power": 0, "no_power": 0, "with_segments": 0,
              "segments": 0, "on_route": 0, "not_on_route": 0, "rejected": {}}
    by_route: dict[str, list] = {}
    for w in ds.workouts:
        if not is_trail_run(w):
            continue
        in_range = b <= w.day < e + 1
        if w.day >= e + 1:
            continue
        src = _source(ds, w)
        if in_range:
            counts["trail_runs"] += 1
            counts[{"stryd": "stryd", "watch": "watch_power"}.get(src, "no_power")] += 1
        if src != "stryd":
            continue
        r = _cached(ds, w)
        segs = r.get("segments") or []
        if in_range:
            for x in r.get("rejected") or []:
                counts["rejected"][x["reason"]] = counts["rejected"].get(x["reason"], 0) + 1
            if segs:
                counts["with_segments"] += 1
                counts["segments"] += len(segs)
        key = of.get(w.entry.file)
        if in_range:
            counts["on_route" if key else "not_on_route"] += len(segs)
        if not key or not segs:
            continue
        wx = weather.get(w.entry.file) or {}
        had = wx.get("hadley")
        aet = getattr(ds, "aethr", lambda _w: None)(w)
        lthr = ds.sport_setting("thr", w)
        for s in segs:
            hr_adj = round(HT.hr_heat_adjust(s["avg_hr"], had), 1) if had is not None else None
            by_route.setdefault(key, []).append({
                "day": float(w.day), "date": w.entry.start.date().isoformat(),
                "start": w.entry.start.isoformat(timespec="minutes"), "file": w.entry.file, "workout": w.idx,
                "at_min": round(s["start_s"] / 60.0, 1), "duration_s": s["duration_s"],
                "measured_s": s["measured_s"], "grade": s["grade"], "gain_m": s["gain_m"],
                "power": s["avg_power"], "hr": s["avg_hr"], "pwhr": s["pwhr"], "vi": s["vi"],
                "cadence_spm": s["cadence_spm"],
                "hr_adj": hr_adj, "pwhr_adj": round(s["avg_power"] / hr_adj, 4) if hr_adj else None,
                "hadley": had, "temp_c": wx.get("temp_c"),
                "zone": _zone(s["avg_hr"], aet, lthr), "aet": aet, "lthr": lthr,
                "in_range": in_range})
    routes = []
    for key, pts in by_route.items():
        pts.sort(key=lambda p: (p["day"], p["at_min"]))
        rng = [p for p in pts if p["in_range"]]
        if not rng:
            continue
        routes.append({"id": key, "name": rname.get(key, key), "runs": len({p["file"] for p in rng}),
                       "segments": len(rng), "last": rng[-1]["date"]})
    # default = the most-run route with qualifying segments (ties: more segments, more recent)
    routes.sort(key=lambda r: (-r["runs"], -r["segments"], tuple(-ord(c) for c in r["last"]), r["id"]))
    want = params.get("route")
    route = next((r for r in routes if r["id"] == want), routes[0] if routes else None)
    out = {"kind": "climbpwhr", "routes": routes, "route": route, "counts": counts,
           "window_days": WINDOW_DAYS, "beta": HT.HR_BETA, "beta_ref": HT.HR_BETA_REF,
           "beta_src": HT.HR_BETA_SRC, "heat_basis": "beta", "points": [], "median": [], "median_adj": []}
    if route is None:
        if counts["trail_runs"] == 0:
            out["empty"] = "這段期間沒有越野跑"
        elif counts["stryd"] == 0:
            out["empty"] = "這段期間的越野跑都沒有 Stryd 功率"
        elif counts["segments"] == 0:
            out["empty"] = "這段期間沒有符合條件的穩定爬坡段（+3～+8%、連續 ≥ 10 分、用跑的、功率穩定）"
        else:
            out["empty"] = "有符合條件的爬坡段，但都不在重複路線上（同一路線跑過 2 次以上才能比較）"
        return out
    pts = by_route[route["id"]]
    med = CP.rolling_median([(p["day"], p["pwhr"]) for p in pts], WINDOW_DAYS)
    med_a = CP.rolling_median([(p["day"], p["pwhr_adj"]) for p in pts], WINDOW_DAYS)
    keep = [i for i, p in enumerate(pts) if p["in_range"]]
    out["points"] = [{k: v for k, v in pts[i].items() if k not in ("in_range", "day")} for i in keep]
    out["median"] = [None if med[i] is None else round(med[i], 4) for i in keep]
    out["median_adj"] = [None if med_a[i] is None else round(med_a[i], 4) for i in keep]
    out["n_adj"] = sum(1 for i in keep if pts[i]["pwhr_adj"] is not None)
    return out


def render(ds, ch: dict, b: float, e: float, params: dict) -> dict:
    """The JSON of one kind "climbpwhr" chart (wko5views._render)."""
    return {"title": ch.get("title"), "description": ch.get("description"), **compute(ds, b, e, params or {})}
