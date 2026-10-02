"""
穩定爬坡 VAM:HR (views kind "climbvam", 我的訓練 → 能力). Reference only:
no gate, no unlock.

Every trail run and hike (百岳 / 登山健行) with HR → engine/climb_vam.
extract_climbs (sustained climbs, walked or run, first 2 min dropped; no
power needed). Compared like for like only: the same route — the route
clustering of engine/routes.py (index.json "routes", ≥ 2 activities); one
done the other way round is its own series ("反向"), a mixed direction is
left out. Each climb is a point; the line is the 8-week rolling median per
route. Heat context: the heat-band work is not merged, so HR is also shown
moved to Hadley 120 with the athlete's own β (engine/heat.hr_heat_adjust,
推估).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from backend.engine import climb_vam as CV

SERIES_KEY = "climb_vam_v2"      # v2: glitch rule
WINDOW_DAYS = 56                 # 8-week rolling median
SOURCES_OVERRIDE: Optional[dict] = None   # tests: {"route_index", "weather", "names"}


def is_trail_run(w) -> bool:
    st = (w.sport_type or "").lower()
    if w.sport != "run" or "treadmill" in st or "indoor" in st or "runningtreadmill" in w.tags \
            or "runningindoor" in w.tags:
        return False
    return "runningtrail" in w.tags or st == "trail running"


def is_hike(w) -> bool:
    from backend.engine.racepower.athlete import is_hike as _h
    return _h(w)


def kind_of(w) -> Optional[str]:
    """"trail" / "hike" / None (not part of this chart)."""
    if is_trail_run(w):
        return "trail"
    if is_hike(w):
        return "hike"
    return None


def _ch(ds, w, *names):
    for n in names:
        v = ds.channel(w.idx, n)
        if v is not None:
            return v
    return None


def extract(ds, w) -> dict:
    t = ds.channel(w.idx, "elapsedtime")
    if t is None:
        return {"segments": [], "rejected": [], "reason": "short", "longest_s": 0.0}
    d = ds.channel(w.idx, "elapseddistance")
    dist_m = None if d is None else [None if v != v else v * 1000.0 for v in d]
    return CV.extract_climbs(list(t), dist_m, _ch(ds, w, "_elevation", "elevation"),
                             _ch(ds, w, "heartrate"), _ch(ds, w, "cadence"), _ch(ds, w, "speed"))


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


START_TOL_S = 180               # 推估: the same activity in another source starts within 3 min


def route_map(route_index: dict, names: dict) -> tuple[dict, dict, list]:
    """({file: route key}, {route key: name}, [(start, route key)]); routes
    with ≥ 2 runs; reversed runs get "<id>~rev", mixed-direction runs no key.
    The index is built on the WKO5 .wko4 files while the charts may read the
    COROS / TP FITs, so an activity is also matched by its start time (the
    efforts' "start")."""
    of, name, starts = {}, {}, []
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
        for e in r.get("efforts") or []:
            k = of.get(e.get("file"))
            if k and e.get("start"):
                try:
                    starts.append((dt.datetime.fromisoformat(str(e["start"])).replace(tzinfo=None), k))
                except ValueError:
                    pass
    starts.sort()
    return of, name, starts


def _nearest(w, starts: list):
    if not starts:
        return None
    s = w.entry.start.replace(tzinfo=None)
    best = min(starts, key=lambda x: abs((x[0] - s).total_seconds()))
    return best[1] if abs((best[0] - s).total_seconds()) <= START_TOL_S else None


def route_of(w, of: dict, starts: list) -> Optional[str]:
    return of.get(w.entry.file) or _nearest(w, starts)


_WKO4_START = re.compile(r"_(\d{4})_(\d\d)_(\d\d)_(\d\d)_(\d\d)\.wko4$")


def weather_starts(weather: dict) -> list:
    """[(start, file)] of the activity-weather entries, the start read from
    WKO5's file name (<athlete>_YYYY_MM_DD_HH_MM.wko4), for activities of
    another source."""
    out = []
    for f in weather:
        m = _WKO4_START.search(f)
        if m:
            out.append((dt.datetime(*map(int, m.groups())), f))
    return sorted(out)


def weather_of(w, weather: dict, wstarts: list) -> dict:
    v = weather.get(w.entry.file)
    if v is None:
        f = _nearest(w, wstarts)
        v = weather.get(f) if f else None
    return v or {}


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
    of, rname, starts = route_map(route_index, names)
    wstarts = weather_starts(weather)
    counts = {"trail_runs": 0, "hikes": 0, "no_hr": 0, "with_segments": 0, "segments": 0,
              "walked": 0, "ran": 0, "on_route": 0, "not_on_route": 0, "rejected": {}}
    by_route: dict[str, list] = {}
    runs_longest: list[dict] = []
    last_run_th = (None, None)          # hikes take the run thresholds of the latest run before them
    for w in ds.workouts:
        if w.sport == "run":
            last_run_th = (getattr(ds, "aethr", lambda _w: None)(w), ds.sport_setting("thr", w))
        kind = kind_of(w)
        if kind is None:
            continue
        in_range = b <= w.day < e + 1
        if w.day >= e + 1:
            continue
        r = _cached(ds, w)
        segs = r.get("segments") or []
        if in_range:
            counts["trail_runs" if kind == "trail" else "hikes"] += 1
            if r.get("reason") == "no_hr":
                counts["no_hr"] += 1
            # how close each activity came: its longest sustained climb (the empty-state chart)
            runs_longest.append({"date": w.entry.start.date().isoformat(), "workout": w.idx, "kind": kind,
                                 "longest_s": r.get("longest_s"), "segments": len(segs),
                                 "reason": r.get("reason")})
            for s in segs:
                if s.get("mode") == "走":
                    counts["walked"] += 1
                elif s.get("mode") == "跑":
                    counts["ran"] += 1
            for x in r.get("rejected") or []:
                counts["rejected"][x["reason"]] = counts["rejected"].get(x["reason"], 0) + 1
            if segs:
                counts["with_segments"] += 1
                counts["segments"] += len(segs)
        key = route_of(w, of, starts) if segs else None
        if in_range:
            counts["on_route" if key else "not_on_route"] += len(segs)
        if not key or not segs:
            continue
        wx = weather_of(w, weather, wstarts)
        had = wx.get("hadley")
        aet, lthr = last_run_th
        for s in segs:
            hr_adj = round(HT.hr_heat_adjust(s["avg_hr"], had), 1) if had is not None else None
            by_route.setdefault(key, []).append({
                "day": float(w.day), "date": w.entry.start.date().isoformat(), "kind": kind,
                "start": w.entry.start.isoformat(timespec="minutes"), "file": w.entry.file, "workout": w.idx,
                "at_min": round(s["start_s"] / 60.0, 1), "duration_s": s["duration_s"],
                "measured_s": s["measured_s"], "grade": s["grade"], "gain_m": s["gain_m"],
                "vam": s["vam"], "hr": s["avg_hr"], "vam_hr": s["vam_hr"],
                "mode": s["mode"], "run_share": s["run_share"], "cadence_spm": s["cadence_spm"],
                "hr_adj": hr_adj, "vam_hr_adj": round(s["vam"] / hr_adj, 3) if hr_adj else None,
                "hadley": had, "temp_c": wx.get("temp_c"),
                "zone": _zone(s["avg_hr"], aet, lthr), "aet": aet, "lthr": lthr,
                "in_range": in_range})
    routes = []
    for key, pts in by_route.items():
        pts.sort(key=lambda p: (p["day"], p["at_min"]))
        rng = [p for p in pts if p["in_range"]]
        # like for like needs at least two activities on the route (one direction) in the range
        if len({p["file"] for p in rng}) < 2:
            continue
        routes.append({"id": key, "name": rname.get(key, key), "runs": len({p["file"] for p in rng}),
                       "segments": len(rng), "last": rng[-1]["date"],
                       "kinds": sorted({p["kind"] for p in rng})})
    # default = the most-run route with qualifying segments (ties: more segments, more recent)
    routes.sort(key=lambda r: (-r["runs"], -r["segments"], tuple(-ord(c) for c in r["last"]), r["id"]))
    want = params.get("route")
    route = next((r for r in routes if r["id"] == want), routes[0] if routes else None)
    from backend.engine.heat_calib import hr_beta
    _hb = hr_beta()
    out = {"kind": "climbvam", "routes": routes, "route": route, "counts": counts,
           "window_days": WINDOW_DAYS, "beta": _hb["beta"], "beta_ref": HT.HR_BETA_REF,
           "beta_src": _hb["src"], "heat_basis": "beta", "points": [], "median": [], "median_adj": [],
           "runs_longest": runs_longest, "min_seg_s": CV.MIN_SEG_S, "grade_min": CV.GRADE_MIN}
    if route is None:
        top = max((x["longest_s"] or 0 for x in runs_longest), default=0)
        n = counts["trail_runs"] + counts["hikes"]
        rule = f"坡度 ≥ +{CV.GRADE_MIN * 100:.0f}%、連續 ≥ {CV.MIN_SEG_S / 60:.0f} 分、有心率"
        if n == 0:
            out["empty"] = "這段期間沒有越野跑或登山健行"
        elif counts["segments"] == 0:
            out["empty"] = (f"這段期間 {n} 次越野跑／登山，都沒有符合條件的穩定爬坡段（{rule}）："
                            f"最長的一段只有 {top / 60:.1f} 分鐘。下圖是每次活動裡最長的一段，虛線 = 門檻。")
        else:
            out["empty"] = (f"有 {counts['segments']} 段符合條件的爬坡，但沒有一條路線（同方向）在這段期間"
                            "有 2 次以上的活動可以比較。下圖是每次活動裡最長的一段。")
        return out
    pts = by_route[route["id"]]
    med = CV.rolling_median([(p["day"], p["vam_hr"]) for p in pts], WINDOW_DAYS)
    med_a = CV.rolling_median([(p["day"], p["vam_hr_adj"]) for p in pts], WINDOW_DAYS)
    keep = [i for i, p in enumerate(pts) if p["in_range"]]
    out["points"] = [{k: v for k, v in pts[i].items() if k not in ("in_range", "day")} for i in keep]
    out["median"] = [None if med[i] is None else round(med[i], 3) for i in keep]
    out["median_adj"] = [None if med_a[i] is None else round(med_a[i], 3) for i in keep]
    out["n_adj"] = sum(1 for i in keep if pts[i]["vam_hr_adj"] is not None)
    return out


def render(ds, ch: dict, b: float, e: float, params: dict) -> dict:
    """The JSON of one kind "climbvam" chart (wko5views._render)."""
    return {"title": ch.get("title"), "description": ch.get("description"), **compute(ds, b, e, params or {})}
