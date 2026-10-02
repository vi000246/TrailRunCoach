"""
Repeated segments and routes API (backend/engine/routes.py;
docs/spec/route-progress.spec.md).

Nothing here waits for a build: the first request starts one in the
background and every endpoint answers from the last saved index, with the
build's progress in `status`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.engine import routes as R
from backend.engine.algorithms import route_match as RM

router = APIRouter(prefix="/api/v1/routes", tags=["routes"])
workout_router = APIRouter(prefix="/api/v1/wko5", tags=["routes"])

def _weather_client():
    """Open-Meteo archive client (racepower/weather.py); WKO5COACH_ROUTES_WEATHER=0 turns it off."""
    if os.getenv("WKO5COACH_ROUTES_WEATHER", "1") == "0":
        return None
    from backend.engine.racepower import weather as WX
    return WX._http_get


STORE = R.RouteStore()
BUILDER = R.Builder(STORE, weather_get=_weather_client(), activity_weather=True)
_CHECKED = {"at": 0.0}
RECHECK_S = 60.0


def _ds():
    """The shared Dataset of the viewer (index -> file, plan, thresholds)."""
    from backend.api.wko5views import _dataset
    return _dataset()


def _source():
    """(workouts, reader) for the builder — built inside the build thread."""
    from backend.engine.wko5expr.config import EngineConfig
    from backend.engine.wko5expr.dataset import Dataset
    from backend.api.wko5views import ATHLETE_DIR
    from backend.engine.wko5expr.datasource import wko5_available
    if not wko5_available(ATHLETE_DIR):
        # the route builder reads .wko4 tracks only: no WKO5 folder = no routes yet
        return [], (lambda f, p, meta: None)
    ds = Dataset(ATHLETE_DIR, config=EngineConfig.load())
    return _workouts(ds), (lambda f, p, meta: R.read_track(f, p, meta, ds.corrections))


def _workouts(ds) -> list[tuple[str, Path, dict]]:
    out = []
    for w in ds.workouts:
        if R.family_of(w.sport, w.sport_type) is None:
            continue
        p = ds.dir / w.entry.file
        if not p.exists():
            continue
        out.append((w.entry.file, p, {"start": w.entry.start.isoformat(), "sport": w.sport,
                                      "sport_type": w.sport_type, "lthr": ds.sport_setting("thr", w),
                                      "climbing": w.metrics.get("climbing")}))
    return out


def _ensure_fresh() -> None:
    """Start an (incremental) build when files changed; checked at most once a minute."""
    import time
    if BUILDER.running():
        return
    now = time.time()
    if STORE.load_index() is not None and now - _CHECKED["at"] < RECHECK_S:
        return
    _CHECKED["at"] = now
    try:
        if BUILDER.stale(_workouts(_ds())):
            BUILDER.start(_source)
    except Exception:          # noqa: BLE001 — never block a page on the check
        BUILDER.start(_source)


# ---------------------------------------------------------------------------
# index -> rows
# ---------------------------------------------------------------------------

_CACHE: dict = {"mtime": None, "view": None}


def _index() -> Optional[dict]:
    try:
        m = STORE.index_path.stat().st_mtime_ns
    except OSError:
        return None
    if _CACHE["mtime"] != m:
        idx = STORE.load_index()
        _CACHE.update(mtime=m, view=_materialise(idx) if idx else None)
    return _CACHE["view"]


def eid(file: str, i0: int) -> str:
    return hashlib.sha1(f"{file}@{i0}".encode()).hexdigest()[:10]


def _materialise(idx: dict) -> dict:
    """Efforts with metrics, ranks and the list rows — computed once per index.
    `aliases`: ids that no longer have an item of their own (a route merged
    into another, a stretch that is a route, links of a merged chain) -> the
    item that holds them now, so old links and the viewer card still open."""
    items = {}
    aliases: dict[str, str] = {}
    for s in idx["segments"]:
        efforts = [{"id": eid(e["file"], e["i0"]), **e} for e in s["efforts"]]
        items[s["id"]] = _item(s, efforts, key="elapsed_s")
        for m in s.get("merged_from") or []:
            aliases.setdefault(m, s["id"])
    for r in idx["routes"]:
        efforts = [{"id": eid(e["file"], e["i0"]), **e} for e in r["efforts"]]
        items[r["id"]] = _item(r, efforts, key="moving_s")
        for a in (r.get("aliases") or []) + (r.get("stretches") or []):
            aliases.setdefault(a, r["id"])
    for r in idx["routes"]:
        it = items[r["id"]]
        it["sub_routes"] = [q["id"] for q in idx["routes"] if q.get("parent") == r["id"]]
    aliases = {a: t for a, t in aliases.items() if a not in items and t in items}
    return {"built_at": idx.get("built_at"), "items": items, "aliases": aliases,
            "weather": _weather_summary(idx.get("weather"))}


def _weather_summary(w: Optional[dict]) -> Optional[dict]:
    if not w:
        return None
    return {k: w.get(k) for k in ("needed", "points", "calls", "cache_hits", "failed", "skipped", "empty", "recent",
                                  "efforts", "with_weather", "errors", "attribution", "at", "cell_deg", "error")}


def _item(s: dict, efforts: list[dict], key: str) -> dict:
    """Segments rank by elapsed time (Strava's segment convention); routes by
    moving time (a whole activity includes stops you chose to make). A route
    run the other way round (dir = reversed / mixed) ranks among the runs of
    its own direction: the climbs are not the same."""
    groups: dict[str, list] = {}
    for e in efforts:
        if e.get(key):
            groups.setdefault(e.get("dir") or "same", []).append(e)
    rank, best_of = {}, {}
    for d, g in groups.items():
        g.sort(key=lambda e: e[key])
        best_of[d] = g[0][key]
        for k, e in enumerate(g):
            rank[e["id"]] = k + 1
    for e in efforts:
        e["rank"] = rank.get(e["id"])
        b = best_of.get(e.get("dir") or "same")
        e["delta_best_s"] = (e[key] - b) if (b is not None and e.get(key)) else None
    main = [e for e in efforts if (e.get("dir") or "same") == "same"]
    best = best_of.get("same")
    last = main[-1] if main else (efforts[-1] if efforts else None)
    lat, lon = s["lat"], s["lon"]
    sports = sorted({e["sport_type"] or e["sport"] for e in efforts})
    return {
        "id": s["id"], "kind": s["kind"], "kind_zh": R.KIND_ZH[s["kind"]], "family": s["family"],
        "direction": s.get("direction", "flat"), "time_key": key,
        "auto_name": s.get("auto_name") or R.KIND_ZH[s["kind"]],
        "length_m": s["length_m"], "gain_m": s["gain_m"], "ref_file": s["ref_file"],
        "lat": lat, "lon": lon, "sports": sports,
        "thumb": RM.thumbnail(lat, lon),
        "n_efforts": len(efforts), "n_activities": len({e["file"] for e in efforts}),
        "n_reversed": sum(1 for e in efforts if (e.get("dir") or "same") != "same"),
        "best_s": best, "last_s": last.get(key) if last else None,
        "last_date": last["start"] if last else None, "first_date": efforts[0]["start"] if efforts else None,
        "parent": s.get("parent"), "partials": list(s.get("partials") or []),
        "merged_from": list(s.get("merged_from") or []), "derived": bool(s.get("derived")),
        "efforts": efforts,
    }


def _row(it: dict, names: dict) -> dict:
    row = {k: v for k, v in it.items() if k not in ("efforts", "lat", "lon", "partials")}
    row["name"] = names.get(it["id"]) or it["auto_name"]
    row["renamed"] = it["id"] in names
    key = it["time_key"]
    main = [e for e in it["efforts"] if (e.get("dir") or "same") == "same"]
    row["spark"] = [[e["start"][:10], e.get(key)] for e in main[-20:]]
    row["start_ll"] = [it["lat"][0], it["lon"][0]]
    row["n_partials"] = len(it.get("partials") or [])
    return row


def _nest(rows: list[dict]) -> list[dict]:
    """Sub-routes right under their parent (when it is listed), `depth` 1."""
    by_parent: dict[str, list] = {}
    present = {r["id"] for r in rows}
    top = []
    for r in rows:
        p = r.get("parent")
        if p and p in present:
            by_parent.setdefault(p, []).append(r)
        else:
            top.append(r)
    out = []

    def put(r, depth):
        r["depth"] = depth
        out.append(r)
        for c in by_parent.get(r["id"], []):
            put(c, depth + 1)

    for r in top:
        put(r, 0)
    return out


def _status() -> dict:
    return dict(BUILDER.status)


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------

@router.get("")
def list_routes(kind: Optional[str] = None, direction: Optional[str] = None,
                sport: Optional[str] = None, min_efforts: int = 2, limit: Optional[int] = None):
    """Segments and routes, most-done and most-recent first.
    kind: segment | route | climb | descent | stretch; direction: up | down | flat;
    sport: a sport type ("trail running") or group ("run")."""
    _ensure_fresh()
    view = _index()
    if view is None:
        return {"status": _status(), "built_at": None, "total": 0, "rows": [], "sports": []}
    names = STORE.names()
    rows = []
    all_sports = set()
    for it in view["items"].values():
        all_sports.update(it["sports"])
        if kind == "segment" and it["kind"] == "route" or kind == "route" and it["kind"] != "route":
            continue
        if kind in ("climb", "descent", "stretch") and it["kind"] != kind:
            continue
        if direction and it["direction"] != direction:
            continue
        if sport and not any(sport == e["sport_type"] or sport == e["sport"] for e in it["efforts"]):
            continue
        if it["n_activities"] < min_efforts:
            continue
        rows.append(_row(it, names))
    rows.sort(key=lambda r: (r["n_efforts"], r["last_date"] or ""), reverse=True)
    rows = _nest(rows)
    total = len(rows)
    return {"status": _status(), "built_at": view["built_at"], "total": total,
            "rows": rows[:limit] if limit else rows, "sports": sorted(all_sports),
            "counts": _counts(view), "weather": view.get("weather")}


def _counts(view) -> dict:
    """Per kind; a sub-route counts as `sub_route`, not as one more route."""
    c: dict = {}
    for it in view["items"].values():
        k = "sub_route" if it["kind"] == "route" and it.get("parent") else it["kind"]
        c[k] = c.get(k, 0) + 1
    return c


@router.get("/status")
def build_status():
    return _status()


class RebuildBody(BaseModel):
    full: bool = False


@router.post("/rebuild")
def rebuild(body: Optional[RebuildBody] = None):
    full = bool(body and body.full)
    started = BUILDER.start(_source, full=full)
    return {"started": started, "status": _status()}


@router.get("/page", include_in_schema=False)
def page():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "routes.html")


def _get(rid: str) -> dict:
    """The item, also by an alias (an id merged into another item)."""
    view = _index()
    if view is not None and rid not in view["items"]:
        rid = view.get("aliases", {}).get(rid, rid)
    if view is None or rid not in view["items"]:
        raise HTTPException(404, "route not found")
    return view["items"][rid]


def _file_to_idx() -> dict[str, int]:
    """{file: workout index} of the current Dataset. The route index is built
    from the WKO5 files: a ByStartDict (engine/activity_key.py) maps a WKO5
    file name to the same activity of a COROS / TP / 同步資料 source by start."""
    from backend.engine.activity_key import ByStartDict
    try:
        ws = _ds().workouts
        return ByStartDict({w.entry.file: {"idx": w.idx, "start": w.entry.start.isoformat()} for w in ws})
    except Exception:      # noqa: BLE001
        return ByStartDict()




def _idx_of(f2i, file: Optional[str], start=None) -> Optional[int]:
    """The current Dataset's index of a route effort (its WKO5 file, else its start)."""
    if not file:
        return None
    hit = f2i.find(file, start) if hasattr(f2i, "find") else f2i.get(file)
    return hit.get("idx") if isinstance(hit, dict) else hit


def _phase_labels(days: list[dt.date]) -> list[Optional[str]]:
    try:
        from backend.engine.planning import Plan, phases
        plan = Plan.load()
        if not days:
            return []
        ph = phases(plan, min(days) - dt.timedelta(days=400), max(days) + dt.timedelta(days=400))
        out = []
        for d in days:
            p = next((p for p in ph if dt.date.fromisoformat(p.start) <= d <= dt.date.fromisoformat(p.end)), None)
            out.append(p.label if p else None)
        return out
    except Exception:      # noqa: BLE001 — phases are context, not required
        return [None] * len(days)


@router.get("/{rid}")
def detail(rid: str):
    it = _get(rid)
    rid = it["id"]
    names = STORE.names()
    f2i = _file_to_idx()
    efforts = [dict(e) for e in it["efforts"]]
    labels = _phase_labels([dt.date.fromisoformat(e["start"][:10]) for e in efforts])
    for e, lab in zip(efforts, labels):
        e["phase"] = lab
        e["workout"] = _idx_of(f2i, e["file"], e.get("start"))
    out = {k: v for k, v in it.items() if k != "efforts"}
    out["name"] = names.get(rid) or it["auto_name"]
    out["renamed"] = rid in names
    out["efforts"] = efforts
    out["partials"] = [dict(p, workout=_idx_of(f2i, p["file"], p.get("start"))) for p in it.get("partials") or []]
    items = _index()["items"]
    ref = lambda i: {"id": i, "name": names.get(i) or items[i]["auto_name"], "length_m": items[i]["length_m"],
                     "n_efforts": items[i]["n_efforts"]}
    out["parent"] = ref(it["parent"]) if it.get("parent") in items else None
    out["sub_routes"] = [ref(i) for i in it.get("sub_routes") or [] if i in items]
    out["status"] = _status()
    out["weather"] = _index().get("weather")
    return out


class RenameBody(BaseModel):
    name: str


@router.patch("/{rid}")
def rename(rid: str, body: RenameBody):
    rid = _get(rid)["id"]
    STORE.set_name(rid, body.name.strip())
    return {"id": rid, "name": body.name.strip() or _get(rid)["auto_name"], "renamed": bool(body.name.strip())}


@router.get("/{rid}/compare")
def compare(rid: str, a: str, b: str, step: float = 25.0):
    """Two efforts on the segment's distance axis: each effort's points are
    projected in order onto the reference path (route_match.along), then
    elapsed time, HR, power and elevation are interpolated every `step` m.
    gap_s = t_b − t_a at the same distance (positive: b is behind)."""
    it = _get(rid)
    rid = it["id"]
    by_id = {e["id"]: e for e in it["efforts"]}
    if a not in by_id or b not in by_id:
        raise HTTPException(404, "effort not found")
    ref_lat, ref_lon = it["lat"], it["lon"]
    if it["kind"] == "route":
        da, db = (by_id[a].get("dir") or "same"), (by_id[b].get("dir") or "same")
        if da != db or da == "mixed":
            # one distance axis needs one direction; a mixed run follows neither
            raise HTTPException(400, "兩次方向不同，無法沿同一條路線逐點比較")
        tr = BUILDER.track(it["ref_file"])
        ref_lat, ref_lon = list(tr.lat), list(tr.lon)
        if da == "reversed":
            ref_lat, ref_lon = ref_lat[::-1], ref_lon[::-1]
        # the comparison walks each run from the reference's start: a run of
        # the same loop started elsewhere has no common distance axis
        for k in (a, b):
            t = BUILDER.track(by_id[k]["file"])
            if t is None:
                raise HTTPException(404, "track missing")
            if RM.haversine_m(float(t.lat[0]), float(t.lon[0]), ref_lat[0], ref_lon[0]) > R.ROUTE_END_TOL_M:
                raise HTTPException(400, "這次的起點和參考路線不同，無法逐點比較")
    lat0, lon0 = ref_lat[0], ref_lon[0]
    ref = RM.project(ref_lat, ref_lon, lat0, lon0)
    L = float(RM.path_length(ref)[-1])
    grid = np.arange(0.0, L + 1e-6, step)
    if grid[-1] < L:
        grid = np.append(grid, L)
    cum = RM.path_length(ref)
    glat = np.interp(grid, cum, ref_lat)
    glon = np.interp(grid, cum, ref_lon)
    series = {}
    for key in (a, b):
        e = by_id[key]
        tr = BUILDER.track(e["file"])
        if tr is None:
            raise HTTPException(404, "track missing")
        series[key] = _along_series(tr, e["i0"], e["i1"], ref, grid, lat0, lon0,
                                    end_tol=R.ROUTE_END_TOL_M if it["kind"] == "route" else RM.END_TOL_M)
    ta, tb = np.asarray(series[a].pop("_t")), np.asarray(series[b].pop("_t"))
    gap = tb - ta
    return {
        "id": rid, "length_m": round(L, 1), "x_m": [round(float(x), 1) for x in grid],
        "lat": [round(float(x), 6) for x in glat], "lon": [round(float(x), 6) for x in glon],
        "a": {"effort": _brief(by_id[a]), **series[a]}, "b": {"effort": _brief(by_id[b]), **series[b]},
        "gap_s": [None if not np.isfinite(g) else round(float(g), 1) for g in gap],
    }


def _brief(e: dict) -> dict:
    return {k: e.get(k) for k in ("id", "start", "sport_type", "elapsed_s", "moving_s", "avg_hr",
                                  "avg_power", "vam", "rank", "max_hr", "max_p30", "wx")}


def _along_series(tr, i0: int, i1: int, ref: np.ndarray, grid: np.ndarray, lat0, lon0,
                  end_tol: float = RM.END_TOL_M) -> dict:
    xy = tr.xy(lat0, lon0)[i0:i1 + 1]
    s = RM.along(ref, xy)
    t = tr.t[i0:i1 + 1] - tr.t[i0]
    # strictly increasing x for interpolation: keep the last point of each tie
    s2, keep = [], []
    for k in range(len(s)):
        if keep and s[k] <= s2[-1] + 1e-6:
            s2[-1], keep[-1] = s[k], k
        else:
            s2.append(s[k]); keep.append(k)
    s2 = np.asarray(s2)
    keep = np.asarray(keep)
    # an effort starts / ends within 60 m of the segment's ends (the match
    # rule): up to that far past its own first / last projection it holds its
    # end values, beyond that it has no data
    outside = (grid < s2[0] - end_tol) | (grid > s2[-1] + end_tol)

    def interp(v):
        out = np.interp(grid, s2, v)
        out[outside] = np.nan
        return out

    tt = interp(t[keep])
    c = tr.cols

    def interval_avg(num, den):
        """Mean of a channel over each interval between kept points, placed at the interval's end."""
        n = np.diff(c[num][i0:i1 + 1])
        d = np.diff(c[den][i0:i1 + 1])
        with np.errstate(invalid="ignore", divide="ignore"):
            v = np.where(d > 0, n / d, np.nan)
        v = np.concatenate(([v[0] if len(v) else np.nan], v))
        return interp(v[keep])

    hr = interval_avg("chr", "chrs")
    pw = interval_avg("cpw", "cpws")
    elev = interp(tr.e[i0:i1 + 1][keep])
    # pace over a 100 m window of the common axis
    win = max(1, int(round(100.0 / max(1.0, grid[1] - grid[0] if len(grid) > 1 else 25.0))))
    pace = np.full(len(grid), np.nan)
    for k in range(len(grid)):
        lo = max(0, k - win)
        if k > lo and np.isfinite(tt[k]) and np.isfinite(tt[lo]) and grid[k] > grid[lo]:
            pace[k] = (tt[k] - tt[lo]) / 60.0 / ((grid[k] - grid[lo]) / 1000.0)
    j = lambda arr, nd: [None if not np.isfinite(v) else round(float(v), nd) for v in arr]
    return {"t_s": j(tt, 1), "hr": j(hr, 0), "power": j(pw, 0), "elev": j(elev, 1),
            "pace_min_km": j(pace, 2), "_t": tt}


@workout_router.get("/workouts/{idx}/segments")
def workout_segments(idx: int):
    """Segments and routes this activity matched, with its rank on each."""
    ds = _ds()
    if not 0 <= idx < len(ds.workouts):
        raise HTTPException(404, "workout not found")
    _ensure_fresh()
    w = ds.workouts[idx]
    file = w.entry.file
    view = _index()
    names = STORE.names()
    out = []
    if view is not None:
        # the route index is built from the WKO5 files: this activity's file there,
        # by start when the current source names it otherwise (engine/activity_key.py)
        from backend.engine.activity_key import ByStartDict
        files = ByStartDict({e["file"]: {"start": e.get("start")} for it in view["items"].values()
                             for e in it["efforts"] if e.get("file")})
        rfile = files.key_for(file, w.entry.start)
        for it in view["items"].values():
            if it["n_activities"] < 2:
                continue
            for e in it["efforts"]:
                if e["file"] != rfile:
                    continue
                n_timed = sum(1 for x in it["efforts"] if x.get(it["time_key"]))
                out.append({"id": it["id"], "name": names.get(it["id"]) or it["auto_name"],
                            "kind": it["kind"], "kind_zh": it["kind_zh"], "length_m": it["length_m"],
                            "gain_m": it["gain_m"], "rank": e["rank"], "of": n_timed,
                            "time_s": e.get(it["time_key"]), "time_key": it["time_key"],
                            "delta_best_s": e["delta_best_s"], "effort": e["id"],
                            "n_efforts": it["n_efforts"]})
    out.sort(key=lambda r: (r["kind"] == "route", r["rank"] or 999, -r["n_efforts"]))
    return {"workout": idx, "file": file, "segments": out, "status": _status(),
            "built": view is not None}
