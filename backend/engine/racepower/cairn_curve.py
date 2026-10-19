"""
The 時間曲線 for 疊石 Cairn (`cairn-curve` v1, SP-653; format and Cairn's rules:
docs/spec/cairn-curve.md; why: docs/analysis/sp570-cairn-itinerary-times.md).

Cairn plans a trip's arrival times itself (its 推估鏈: each day's start, the stops, the
nights); TRC gives it the PACE only — the cumulative MOVING time along the route of a
calculation, `points: [[m, cum_moving_s], …]`, one point per TRC segment end. Cairn's
`curve` pace answers a leg's moving minutes as T(to) − T(from). The segment times
already hold the night slow-down and the heat of the hour TRC computed them for; that
hour goes along as `start_times` (Cairn warns when its own start is far off).

Distances are measured the way Cairn measures them — haversine summed over every
<trkpt> of the GPX file, in document order — so `total_m` is the number Cairn gets for
the same file; TRC's own (resampled) km are scaled onto it. `gpx_sha256` names the file.

The `fallback` (used by Cairn when walking backwards, outside the curve, or on another
file) is a `munter-split` pace (EPH up / down) solved on CAIRN's statistics of the same
file — rangeStats copied below (haversine, a 50 m moving average of the elevation, the
up / flat / down buckets by Δh) — never on TRC's own climb (other smoothing, other
numbers). The fit matches TRC's total moving time exactly (one free scale) and picks the
up/down ratio by least squares over the segments.

`rest_hint` is a suggestion per day (Cairn shows it, never writes it): the non-moving
prediction (nonmoving.py: by the plan's stop kind and moving time, SP-721) of a race, or
each day's stop prediction of a 百岳 trip (its 跟團 / 自己 hike kind, SP-732; the old
moving-ratio rest for a plan without a stop profile).

No coordinates, no stops, no ids or tokens leave in the curve.

Nothing here reads the athlete's data or the private core (SP-500): the curve is a
reshaping of a stored result plus arithmetic on the stored GPX file.
"""
from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from typing import Optional, Sequence

from backend.i18n import _

VERSION = 1
SOURCE = "trailruncoach"
SMOOTH_WINDOW_M = 50.0          # Cairn's ELEV_SMOOTH_WINDOW_M (shared/src/geo.ts)
EARTH_R = 6371008.8             # Cairn's haversineM radius
LENGTH_TOL = 0.01               # TRC's km vs Cairn's: more than 1 % apart = not the same file
EPH_MIN, EPH_MAX = 0.5, 40.0    # Cairn's pace form range (pace-form.ts)
MIN_RATE = 0.01                 # Cairn's pace.ts guard
TEXTBOOK_RATIO = 2.5            # down / up EPH Cairn's calibration fixes (4 : 10), the tie-break


class CurveError(ValueError):
    """code: NO_RESULT (404) | NO_GPX | GPX_CHANGED | GPX_MISMATCH | NO_TRACK | NO_SEGMENTS (409) | BAD_ID (400)."""

    STATUS = {"NO_RESULT": 404, "BAD_ID": 400}

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.status = self.STATUS.get(code, 409)


# ---- Cairn's arithmetic (inReachLiveTrack v2: shared/src/geo.ts, app/src/lib/elevation.ts, pace.ts) ----

def _js_number(raw: Optional[str]) -> Optional[float]:
    """JavaScript's Number(raw.trim()) as Cairn's gpx-parse `num` uses it: '' → 0, junk → None."""
    if raw is None:
        return None
    s = raw.strip()
    if s == "":
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1] if isinstance(tag, str) else ""


def gpx_points(data: bytes) -> list[tuple[float, float, Optional[float]]]:
    """[(lat, lng, ele | None)] of every <trkpt> in document order, as Cairn's parseGpx reads them
    (no <rtept>; <ele> from a direct child; a point without a usable lat / lon is skipped)."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise CurveError("NO_TRACK", _("Cairn 只讀 GPX：這個檔案不是 GPX"))
    if _local(root.tag) != "gpx":
        raise CurveError("NO_TRACK", _("Cairn 只讀 GPX：這個檔案不是 GPX"))
    out = []
    for el in root.iter():
        if _local(el.tag) != "trkpt":
            continue
        lat, lng = _js_number(el.get("lat")), _js_number(el.get("lon"))
        if lat is None or lng is None:
            continue
        ele = None
        for ch in el:
            if _local(ch.tag) == "ele":
                ele = _js_number(ch.text or "")
                break
        out.append((lat, lng, ele))
    return out


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = math.radians
    a = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lng2 - lng1) / 2) ** 2)
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def cum_distance(pts: Sequence) -> list[float]:
    """Cumulative haversine metres per point (Cairn's buildElevationSeries, in m)."""
    out, acc = [0.0] * len(pts), 0.0
    for i in range(1, len(pts)):
        acc += haversine_m(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1])
        out[i] = acc
    return out


def smooth_elevations(pts: Sequence, window_m: float = SMOOTH_WINDOW_M) -> list[Optional[float]]:
    """Cairn's smoothElevations: the mean of the elevations within ±window_m along the track;
    a point without <ele> stays None (never filled from its neighbours)."""
    n = len(pts)
    if n == 0:
        return []
    dist = cum_distance(pts)
    psum, pcnt = [0.0] * (n + 1), [0] * (n + 1)
    for i, p in enumerate(pts):
        e = p[2]
        psum[i + 1] = psum[i] + (e if e is not None else 0.0)
        pcnt[i + 1] = pcnt[i] + (0 if e is None else 1)
    out: list[Optional[float]] = [None] * n
    lo = hi = 0
    for i in range(n):
        while dist[lo] < dist[i] - window_m:
            lo += 1
        while hi + 1 < n and dist[hi + 1] <= dist[i] + window_m:
            hi += 1
        if pts[i][2] is None:
            continue
        cnt = pcnt[hi + 1] - pcnt[lo]
        out[i] = pts[i][2] if cnt == 0 else (psum[hi + 1] - psum[lo]) / cnt
    return out


def range_basis(pts: Sequence, ele: Sequence, i0: int, i1: int) -> dict:
    """Cairn's rangeStats (forward, `reversed` false) restricted to what munter-split uses:
    distanceM, ascentM, descentM and the horizontal distance per bucket (up / flat / down by
    the smoothed Δh; a step without elevation is flat)."""
    z = {"distanceM": 0.0, "ascentM": 0.0, "descentM": 0.0, "distUpM": 0.0, "distFlatM": 0.0, "distDownM": 0.0}
    if not pts:
        return z
    last = len(pts) - 1
    a = max(0, min(last, int(min(i0, i1))))
    b = max(0, min(last, int(max(i0, i1))))
    for i in range(a + 1, b + 1):
        p, q = pts[i - 1], pts[i]
        d = haversine_m(p[0], p[1], q[0], q[1])
        z["distanceM"] += d
        ea, eb = ele[i - 1], ele[i]
        if ea is not None and eb is not None:
            de = eb - ea
            if de > 0:
                z["ascentM"] += de
            else:
                z["descentM"] -= de
            if d > 0:
                key = "distUpM" if de > 0 else "distDownM" if de < 0 else "distFlatM"
                z[key] += d
        elif d > 0:
            z["distFlatM"] += d
    return z


def _pos(v: float) -> float:
    return v if math.isfinite(v) and v > MIN_RATE else MIN_RATE


def munter_split_min(b: dict, up_eph: float, down_eph: float) -> float:
    """Cairn's paceMinutes for {kind: 'munter-split', upEph, downEph} (walking minutes, no rest)."""
    up, down = _pos(up_eph), _pos(down_eph)
    avg = (up + down) / 2
    return ((b["distUpM"] / 1000 + b["ascentM"] / 100) / up * 60
            + (b["distDownM"] / 1000 + b["descentM"] / 100) / down * 60
            + (b["distFlatM"] / 1000) / avg * 60)


def index_at_m(cum: Sequence[float], m: float) -> int:
    """Cairn's indexAtKm: the point nearest to distance m (a tie goes to the later one)."""
    n = len(cum)
    if n == 0:
        return -1
    km = m / 1000.0
    lo, hi = 0, n - 1
    while lo < hi:
        mid = (lo + hi) >> 1
        if cum[mid] / 1000.0 < km:
            lo = mid + 1
        else:
            hi = mid
    if lo > 0 and abs(cum[lo - 1] / 1000.0 - km) < abs(cum[lo] / 1000.0 - km):
        return lo - 1
    return lo


# ---- the fallback: munter-split solved on Cairn's statistics -----------------------------------

def _efforts(b: dict) -> tuple[float, float, float]:
    """(effort points up, down, flat km) of a basis."""
    return (b["distUpM"] / 1000 + b["ascentM"] / 100, b["distDownM"] / 1000 + b["descentM"] / 100,
            b["distFlatM"] / 1000)


def solve_munter_split(bases: Sequence[dict], times_s: Sequence[float]) -> dict:
    """(upEph, downEph) whose Cairn time of the whole route equals sum(times_s) and whose ratio
    down / up fits the parts best (least squares, seconds). Time is 1/u-homogeneous, so the
    ratio r = down / up is searched (log grid, then refined) and u follows from the total.
    A ratio the data can't tell (no downhill, say) falls back to Cairn's textbook 2.5."""
    E = [_efforts(b) for b in bases]
    T = [float(t) for t in times_s]
    tot = sum(T)
    if tot <= 0 or not any(sum(e) > 0 for e in E):
        raise CurveError("NO_SEGMENTS", _("算不出 Cairn 的腳程：路線沒有距離或沒有移動時間"))

    def g(r: float) -> list[float]:
        return [3600.0 * (a + d / r + 2.0 * f / (1.0 + r)) for a, d, f in E]   # seconds × up EPH

    def fit(r: float) -> tuple[float, float]:
        gs = g(r)
        u = sum(gs) / tot
        return sum((x / u - t) ** 2 for x, t in zip(gs, T)), u

    def best(rs):
        scored = [(fit(r)[0], abs(math.log(r / TEXTBOOK_RATIO)), r) for r in rs]
        lo = min(s for s, _d, _r in scored)
        tie = [x for x in scored if x[0] <= lo * (1 + 1e-9) + 1e-9]
        return min(tie, key=lambda x: x[1])[2]

    lr = [math.log(0.2) + (math.log(10.0) - math.log(0.2)) * i / 400 for i in range(401)]
    r = best([math.exp(x) for x in lr])
    step = (lr[1] - lr[0])
    for _i in range(3):                     # refine twice around the best grid point
        c = math.log(r)
        r = best([math.exp(c + step * (k / 20.0 - 1.0)) for k in range(41)])
        step /= 20.0
    sse, u = fit(r)
    up, down = u, u * r
    clamped = not (EPH_MIN <= up <= EPH_MAX and EPH_MIN <= down <= EPH_MAX)
    up, down = min(EPH_MAX, max(EPH_MIN, up)), min(EPH_MAX, max(EPH_MIN, down))
    return {"upEph": round(up, 3), "downEph": round(down, 3), "clamped": clamped,
            "rmse_s": math.sqrt(sse / len(T)) if T else 0.0}


# ---- the curve -----------------------------------------------------------------------------------

def _day(s: dict) -> int:
    try:
        return int(s.get("day") or 1)
    except (TypeError, ValueError):
        return 1


def start_times_of(result: dict, start_time: Optional[str]) -> list:
    """Each day's start time the plan was computed for: the 百岳 plan's summary.day_starts
    (SP-580), else the one start time (None when the page had none: no clock-time effects)."""
    ds = ((result or {}).get("summary") or {}).get("day_starts") or []
    if ds:
        return [x or None for x in ds]
    return [start_time or None]


def rest_hint(result: dict) -> Optional[dict]:
    """Suggested non-moving seconds per day — shown by Cairn, never written to its stops.
    A race: the non-moving prediction (nonmoving.py) with its p25–p75 range, on one day.
    A 百岳 trip: each day's stop prediction with its p25–p75 (summary.rest, SP-732), else each day's
    clock minus moving time (the moving ratio). None without either."""
    s = (result or {}).get("summary") or {}
    nm = s.get("nonmoving")
    if isinstance(nm, dict) and nm.get("total_s") is not None:
        rng = [round(nm["p25_s"]), round(nm["p75_s"])] if nm.get("p25_s") is not None and nm.get("p75_s") is not None \
            else None
        # SP-721: the prediction by the plan's stop kind (race / solo), share growing with the moving time
        return {"basis": f"nonmoving:{nm.get('method') or 'rate'}", "kind": nm.get("kind"),
                "days": [{"day": 1, "rest_s": round(nm["total_s"]), "range_s": rng}]}
    rest = s.get("rest")
    if isinstance(rest, dict) and rest.get("days"):
        # SP-732: a 百岳 trip — each day's stops from the stop model of its trip kind, with p25–p75
        return {"basis": "nonmoving:hike", "kind": rest.get("kind"),
                "days": [{"day": int(d["day"]), "rest_s": round(d["rest_s"]),
                          "range_s": [round(d["p25_s"]), round(d["p75_s"])]} for d in rest["days"]]}
    days = [d for d in (result or {}).get("days") or [] if d.get("moving_h") is not None and d.get("clock_h") is not None]
    if days and s.get("moving_ratio"):
        return {"basis": "moving_ratio",
                "days": [{"day": int(d.get("day") or i + 1), "rest_s": round(max(0.0, d["clock_h"] - d["moving_h"]) * 3600),
                          "range_s": None} for i, d in enumerate(days)]}
    return None


def _model(result: dict) -> str:
    s = result.get("summary") or {}
    m = s.get("total_method") or s.get("mode")
    return f"{result.get('type') or 'plan'}" + (f"/{m}" if m else "")


def build(result: dict, gpx: bytes, *, start_times: Sequence, computed_at: Optional[str] = None) -> dict:
    """The cairn-curve v1 of a /plan result computed on the GPX file `gpx` (the same file)."""
    segs = [s for s in (result or {}).get("segments") or []
            if s.get("t") is not None and s.get("end_km") is not None]
    if not segs:
        raise CurveError("NO_SEGMENTS", _("這次計算沒有分段時間，做不出時間曲線"))
    pts = gpx_points(gpx)
    if len(pts) < 2:
        raise CurveError("NO_TRACK", _("GPX 裡沒有軌跡點（trkpt）：Cairn 只讀軌跡點"))
    cum = cum_distance(pts)
    total = cum[-1]
    trc_total = float(segs[-1]["end_km"]) * 1000.0
    if total <= 0 or trc_total <= 0 or abs(total / trc_total - 1.0) > LENGTH_TOL:
        raise CurveError("GPX_MISMATCH", _("這次計算的路線長度跟 GPX 對不上（差超過 1 %）：重算一次再拿曲線"))
    scale = total / trc_total

    # points: [m, cumulative moving s] at each segment end (a zero-length segment folds into the last point)
    points = [[0, 0]]
    ends, acc = [], 0.0                     # (m, cum s, day) per kept point
    for s in segs:
        acc += float(s["t"])
        m = min(total, float(s["end_km"]) * 1000.0 * scale)
        if ends and m <= ends[-1][0] + 1e-9:
            ends[-1] = (ends[-1][0], acc, _day(s))
        else:
            ends.append((m, acc, _day(s)))
    if ends:
        ends[-1] = (total, ends[-1][1], ends[-1][2])
    for m, t, _d in ends:
        points.append([round(m, 1), round(t, 1)])

    # the day ends: where a segment of a later day starts
    splits, day = [], _day(segs[0])
    for s in segs:
        if _day(s) != day:
            day = _day(s)
            splits.append(round(float(s["start_km"]) * scale, 3))

    # the fallback on Cairn's statistics, one basis per TRC segment (consecutive index ranges: additive)
    ele = smooth_elevations(pts)
    idx = [index_at_m(cum, 0.0)] + [index_at_m(cum, m) for m, _t, _d in ends]
    bases = [range_basis(pts, ele, idx[i], idx[i + 1]) for i in range(len(ends))]
    times = [ends[0][1]] + [ends[i][1] - ends[i - 1][1] for i in range(1, len(ends))]
    fb = solve_munter_split(bases, times)
    pred = [munter_split_min(b, fb["upEph"], fb["downEph"]) * 60 for b in bases]
    trc_s, fb_s = sum(times), sum(pred)
    per_day: dict[int, list[float]] = {}
    for (m, _t, d), t, p in zip(ends, times, pred):
        x = per_day.setdefault(d, [0.0, 0.0])
        x[0] += t
        x[1] += p
    check = {"trc_s": round(trc_s), "fallback_s": round(fb_s),
             "total_err_pct": round((fb_s - trc_s) / trc_s * 100, 2) if trc_s else 0.0,
             "days": [{"day": d, "trc_s": round(a), "fallback_s": round(b),
                       "err_pct": round((b - a) / a * 100, 1) if a else None} for d, (a, b) in sorted(per_day.items())],
             "rmse_s": round(fb["rmse_s"], 1), "clamped": fb["clamped"]}
    return {
        "v": VERSION,
        "source": SOURCE,
        "gpx_sha256": hashlib.sha256(gpx).hexdigest(),
        "total_m": round(total, 1),
        "day_splits_km": splits,
        "start_times": list(start_times),
        "points": points,
        "fallback": {"kind": "munter-split", "upEph": fb["upEph"], "downEph": fb["downEph"]},
        "fallback_check": check,
        "rest_hint": rest_hint(result),
        "computed_at": computed_at,
        "model": _model(result),
    }


# ---- where the result and its file come from ---------------------------------------------------

def _event_gpx(eid: Optional[str], course_id: Optional[str]) -> bytes:
    """The GPX file stored with plan event `eid`, checked to be the file `course_id` (sha1) was computed on."""
    from backend.engine import event_gpx as EG
    if not course_id or not eid:
        raise CurveError("NO_GPX", _("時間曲線要用賽事存的 GPX 算：這次計算不是用 GPX 路線"))
    try:
        row = EG.get(eid)
    except EG.EventGpxError as e:
        raise CurveError("BAD_ID", str(e))
    data = EG.read_bytes(eid) if row else None
    if not row or data is None:
        raise CurveError("NO_GPX", _("時間曲線要用賽事存的 GPX 算：這場賽事沒有存 GPX"))
    if row.get("sha1") != course_id:
        raise CurveError("GPX_CHANGED", _("賽事的 GPX 換過了：用新的 GPX 重算一次再拿曲線"))
    return data


def for_saved(eid: str) -> dict:
    """The cairn-curve of event `eid`'s saved calculation (race_calc_store) on the event's stored GPX.
    The interface SP-654 (link token API) reads; raises CurveError (.code, .status)."""
    from backend.engine import race_calc_store as RC
    try:
        saved = RC.get(eid)
    except RC.RaceCalcError as e:
        raise CurveError("BAD_ID", str(e))
    res = (saved or {}).get("result")
    if not isinstance(res, dict) or not res.get("segments"):
        raise CurveError("NO_RESULT", _("這場賽事還沒有存過計算結果"))
    form = ((saved.get("inputs") or {}).get("form") or {}) if isinstance(saved.get("inputs"), dict) else {}
    data = _event_gpx(eid, res.get("course_id"))
    at = saved.get("saved_at")
    return build(res, data, start_times=start_times_of(res, form.get("start")),
                 computed_at=at.replace(" ", "T") if isinstance(at, str) else None)


def for_plan(plan: dict, event_id: Optional[str], start_time: Optional[str], computed_at: Optional[str] = None) -> dict:
    """The cairn-curve of a fresh /plan result (the share snapshot's opt-in curve)."""
    data = _event_gpx(event_id, plan.get("course_id"))
    return build(plan, data, start_times=start_times_of(plan, start_time), computed_at=computed_at)
