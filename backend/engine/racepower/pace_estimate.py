"""
腳程估算 (SP-805, for 疊石 Cairn's 揪團報名, SP-734): one recorded hike (a GPX / FIT with times) → this
person's walking speed per grade and stop habit, applied to a route (another GPX) → the moving and
total time on it, with a range, and how it compares with an ordinary hiker on the same route.

Everything here is computed from the two files the caller hands in; nothing is read from or written
to any tenant, nothing is kept (the caller drops the bytes when the request ends).

The model is the 百岳 calculator's walking model, fitted from one track instead of an account
(docs/research/racepower-v2.md §3 F13, SP-716):

* speed per 2 % grade bin from the track's moving 100 m windows (grade_model.windows, the same
  sampler as athlete.hike_samples; moving = faster than athlete.HIKE_REST_MS), shrunk with
  n / (n + 30) like grade_model.HikeSpeed — but to Tobler's hiking function × the person's own level
  (LeveledSpeed: their speed ÷ Tobler's over all windows, shrunk to 1.0 = the 全體先驗; per 上坡 /
  平路 / 下坡 shrunk to that). One track has few windows per 2 % bin; against the bare Tobler prior
  every track would read alike. Uphill, flat and downhill speeds are the curve's values, so the
  descent cost (SP-723's point: descents are slower than an effort-km rule says) is in the person's
  own downhill level and bins;
* 跟團 (the caller says the track was walked with a group): the group's pace is not the person's,
  so its windows count GROUP_WEIGHT (½) — the curve leans more on the prior — and the range widens;
* the pack: the track's speeds are brought to REF_PACK_KG with the v1 linear load factor
  (hike.pack_factor, (W + L₀)/(W + L), W = DEFAULT_WEIGHT_KG since the body weight is not known),
  then to the route's pack;
* altitude: each route piece's speed × env.segment_factors (unacclimatised, Wehrlin) against the
  track's median elevation;
* stops (nonmoving.py, SP-721 / 732): the track's own stop ÷ moving share, each calendar day apart
  (a stopped spell ≥ NIGHT_S is a night, not a stop), fitted like the hike kind with the hike prior
  (0.60 at 1 h, K_LEVEL pseudo-days) — one day says little, so the prior dominates; capped at 2.5 h
  like every kind. The range: the prior range ratios STOP_RANGE (推估) until a track has
  nonmoving.RANGE_MIN_N days.

The common ruler (the 一般登山客 the page compares with; never shown by name on a page — the owner,
SP-734 Q1-3): Munter's method as the Swiss walking-time rule (4 km/h, 100 m of climb = 1 km) plus a
fixed descent cost (200 m of descent = 1 km), per 100 m piece — GENERAL. 經驗法則 + 推估.

The moving-time range: ± (MOVE_BASE + MOVE_EXTRA × the route time share not covered by the track's own
windows) (+ GROUP_EXTRA for a 跟團 track) — SP-716 found the given-HR moving model at 6.5 % median error;
one track is less (推估). All the numbers here are 推估.

An account (the signed-in person, `account_ability`): the same walking curve the 百岳 calculator
uses (athlete.walk models' HikeSpeed — solo hikes and steep HR windows, shrunk to Tobler) and the
person's stop profile (hike kind); the same estimate() — so "比你" compares like with like.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from backend.i18n import N_, _

MODEL = "sp805-v1"
REST_MS = 0.3                    # athlete.HIKE_REST_MS: slower than this is not walking
WIN_M = 100.0                    # the sampler's window (grade_model.windows)
GROUP_WEIGHT = 0.5               # 推估: a 跟團 window counts half (the group's pace, not the person's)
DEFAULT_WEIGHT_KG = 65.0         # 推估: the pack factor's body weight (the person's is not known)
REF_PACK_KG = 4.0                # capacity.PACK_DEFAULT_SINGLE: the track's speeds are brought to this pack
NIGHT_S = 4 * 3600.0             # 推估: a stopped spell this long is a night (a day boundary), not a stop
MIN_KM = 1.0                     # a track shorter than this, or
MIN_MOVING_S = 20 * 60.0         # moving less than this, says nothing (nonmoving.NONMOVING["min_s"])
MIN_WINDOWS = 10
MOVE_BASE = 0.08                 # 推估 (SP-716: the moving model's median error 6.5 %)
MOVE_EXTRA = 0.12                # 推估: the part of the route the track has no windows for
GROUP_EXTRA = 0.05               # 推估
COVER_N = 10                     # a grade bin with this many windows of the track counts as covered
STOP_RANGE = (0.6, 1.6)          # 推估: stop range ratios while the track has few days
FLAT_PCT = 5.0                   # 推估: 平路 = |grade| ≤ 5 % for a hike (the race calculator's 2 % is a runner's)
GENERAL = {"flat_kmh": 4.0, "up_m_per_km": 100.0, "down_m_per_km": 200.0}   # Munter + a fixed descent cost
SIMILAR_PCT = 3                  # |difference| below this reads 「差不多」 (the page; 推估)
TRIP_KINDS = ("group", "solo")
MAX_DAYS = 10

BASIS_TRACK = N_("依一趟紀錄估計")
BASIS_ACCOUNT = N_("依你帳號的登山紀錄估計")


class EstimateError(ValueError):
    """The file cannot be used (code for the API, message for the person)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Ability:
    """A person's walking curve and stop habit, wherever it came from."""
    speed: object                                   # grade_model.HikeSpeed (m/s per grade, pack REF_PACK_KG)
    stop: dict                                      # nonmoving kind fit: c, b, q25, q75, n, prior
    basis: dict = field(default_factory=dict)
    group: bool = False

    def covered(self, g: float) -> bool:
        from backend.engine.racepower import grade_model as GM
        s = self.speed.bins.get(GM._bin_of(g))
        return bool(s) and s["n"] >= COVER_N


# ---------------------------------------------------------------------------- the leveled curve

LEVEL_K = 10.0                   # 推估: pseudo-windows pulling the person's overall level towards Tobler's (1.0)
PART_K = 20.0                    # 推估: pseudo-windows pulling the 上坡 / 平路 / 下坡 level towards the overall one
BLEND = (0.02, 0.08)             # the flat level blends into the up / down one between these grades


def _parts_w(g: float) -> tuple[float, float]:
    """(up weight, down weight) of grade g: 0 below BLEND[0], 1 above BLEND[1], linear between."""
    lo, hi = BLEND
    up = min(1.0, max(0.0, (g - lo) / (hi - lo)))
    dn = min(1.0, max(0.0, (-g - lo) / (hi - lo)))
    return up, dn


def levels_of(bins: dict) -> dict:
    """The person's speed ÷ Tobler's, time-weighted over the windows (Σ t_Tobler ÷ Σ t_own), overall
    and per part (up > BLEND[1], down < −BLEND[1], flat between ±BLEND[0]), each shrunk: the overall
    one to 1.0 (Tobler) with LEVEL_K windows, the parts to the overall one with PART_K. `bins`:
    grade_model bin → {"n", "v"} (the median speed of that bin's windows)."""
    from backend.engine.racepower import grade_model as GM
    acc = {k: [0.0, 0.0, 0.0] for k in ("all", "up", "flat", "down")}      # n, Σ n/tobler, Σ n/v
    for b, s in bins.items():
        n, v = float(s.get("n") or 0), s.get("v")
        if n <= 0 or not v or v <= 0:
            continue
        g = b * GM.BIN
        tob = GM.tobler_kmh(g) / 3.6
        keys = ["all"] + (["up"] if g > BLEND[1] else ["down"] if g < -BLEND[1] else ["flat"] if abs(g) <= BLEND[0] else [])
        for k in keys:
            acc[k][0] += n
            acc[k][1] += n / tob
            acc[k][2] += n / v
    def raw(k):
        n, a, b = acc[k]
        return (a / b if b > 0 else None), n
    lam, n = raw("all")
    lam = 1.0 if lam is None else (n * lam + LEVEL_K * 1.0) / (n + LEVEL_K)
    out = {"all": lam, "n": acc["all"][0]}
    for k in ("up", "flat", "down"):
        x, m = raw(k)
        out[k] = lam if x is None else (m * x + PART_K * lam) / (m + PART_K)
        out["n_" + k] = m
    return out


@dataclass
class LeveledSpeed:
    """grade_model.HikeSpeed with its prior scaled to the person: Tobler × the person's own level
    (levels_of: overall, then 上坡 / 平路 / 下坡) instead of Tobler alone, the bins shrunk to it with
    the same n / (n + 30). A single track has only a few windows in each 2 % bin, so with the bare
    Tobler prior every track would read close to Tobler; its level over all windows is well measured,
    and the bins still shape the curve where the track has enough of them (推估)."""
    bins: dict
    ref_alt_m: Optional[float]
    n_samples: int
    n_days: int
    level: dict

    def lam(self, g: float) -> float:
        up, dn = _parts_w(g)
        return up * self.level["up"] + dn * self.level["down"] + (1.0 - up - dn) * self.level["flat"]

    def _shrunk(self, b: int, g: float) -> float:
        from backend.engine.racepower import grade_model as GM
        prior = self.lam(g) * GM.tobler_kmh(g) / 3.6
        s = self.bins.get(b)
        if not s or not s["n"]:
            return prior
        w = s["n"] / (s["n"] + GM.SHRINK_N)
        return w * s["v"] + (1 - w) * prior

    def v(self, g: float) -> float:
        from backend.engine.racepower import grade_model as GM
        g = max(GM.G_MIN, min(GM.G_MAX, g))
        x = g / GM.BIN
        lo = math.floor(x)
        f = x - lo
        a = self._shrunk(lo, lo * GM.BIN)
        if f < 1e-9:
            return a
        return a + (self._shrunk(lo + 1, (lo + 1) * GM.BIN) - a) * f


def leveled(hs) -> LeveledSpeed:
    """A HikeSpeed (grade_model) → its LeveledSpeed (the same bins)."""
    bins = {b: dict(s) for b, s in (hs.bins or {}).items()}
    return LeveledSpeed(bins, hs.ref_alt_m, int(hs.n_samples or 0), int(hs.n_days or 0), levels_of(bins))


# ---------------------------------------------------------------------------- the track

def _parse(data: bytes, filename: str, what: str):
    from backend.engine.racepower import gpx as GPX
    try:
        return GPX.parse(data, filename or "")
    except GPX.GpxError as e:
        raise EstimateError("BAD_FILE", _("{what}：{e}", what=what, e=str(e))) from None


def _smoothed(d: np.ndarray, z: np.ndarray) -> np.ndarray:
    """The elevation smoothed along the distance (the course's 10 m grid, 50 m Gaussian), read back at
    the track's own points: barometric steps and GPS noise would read as steep grades in 100 m windows."""
    from backend.engine.racepower import course as CO
    if len(d) < 3 or d[-1] <= CO.STEP_M:
        return z
    xs, raw = CO.resample(d, z, CO.STEP_M)
    zs = CO.smooth(raw, CO.STEP_M, CO.DEFAULT_SIGMA_M)
    return np.interp(d, xs, zs)


def _day_rows(t: np.ndarray, moving: np.ndarray) -> list[dict]:
    """nonmoving.run_row per day: the track cut at every stopped spell ≥ NIGHT_S (that spell left out)."""
    from backend.engine.racepower import nonmoving as NM
    dt_ = np.diff(t)
    stopped = (dt_ > NM.NONMOVING["max_dt"]) | ~moving[1:]
    cuts, run_start, run = [], None, 0.0
    for i, (x, s) in enumerate(zip(dt_, stopped)):
        if s:
            if run_start is None:
                run_start = i
            run += x
        else:
            if run_start is not None and run >= NIGHT_S:
                cuts.append((run_start, i))
            run_start, run = None, 0.0
    if run_start is not None and run >= NIGHT_S:
        cuts.append((run_start, len(t)))
    rows, a = [], 0
    for s, e in cuts + [(len(t), len(t))]:
        if s - a >= 2:
            r = NM.run_row(t[a:s + 1], moving[a:s + 1])
            if r and r["moving_s"] > 0:
                rows.append({**r, "kind": "hike"})
        a = e
    return rows


def track_ability(data: bytes, filename: str = "", pack_kg: Optional[float] = None,
                  trip_kind: Optional[str] = None) -> Ability:
    """One recorded hike → Ability (raises EstimateError). `pack_kg`: the pack on that trip (None →
    REF_PACK_KG); `trip_kind`: "group" (跟團) or "solo" (自己走; None → solo)."""
    from backend.engine.racepower import course as CO
    from backend.engine.racepower import grade_model as GM
    from backend.engine.racepower import hike as HK
    from backend.engine.racepower import nonmoving as NM
    track = _parse(data, filename, _("紀錄"))
    if not track.time or sum(1 for x in track.time if x is not None) < 10:
        raise EstimateError("NO_TIME", _("這份紀錄沒有時間，算不出走多快：請用手錶或登山 App 記錄的 GPX／FIT"))
    td = CO.track_distance(track)
    d, z, t = td["d"], td["z"], td["t"]
    if t is None or len(d) < 10 or d[-1] < MIN_KM * 1000.0:
        raise EstimateError("TOO_SHORT", _("這份紀錄太短（不到 1 公里）"))
    if not np.all(np.isfinite(t)) or np.any(np.diff(t) < 0):
        raise EstimateError("BAD_TIME", _("這份紀錄的時間有缺或倒退"))
    z = _smoothed(d, z)
    dd, dt_ = np.diff(d, prepend=d[0]), np.diff(t, prepend=t[0])
    v = np.where(dt_ > 0, dd / np.where(dt_ > 0, dt_, 1.0), 0.0)
    moving = v > REST_MS
    rows = GM.windows(t, d, z, None, moving, win_m=WIN_M)
    pack = float(pack_kg) if pack_kg is not None else REF_PACK_KG
    pf = HK.pack_factor(DEFAULT_WEIGHT_KG, pack, REF_PACK_KG)       # the track's speeds at REF_PACK_KG
    samples = [{"g": r["g"], "v": r["v"] * pf, "z": r["z"], "a": 0} for r in rows if r["v"] >= REST_MS]
    days = _day_rows(t, moving)
    moving_s = sum(r["moving_s"] for r in days)
    if len(samples) < MIN_WINDOWS or moving_s < MIN_MOVING_S:
        raise EstimateError("TOO_SHORT", _("這份紀錄移動不到 20 分鐘，估不出腳程"))
    if len(days) > MAX_DAYS:
        raise EstimateError("TOO_LONG", _("這份紀錄超過 {n} 天", n=MAX_DAYS))
    group = trip_kind == "group"
    hs = GM.fit_hike_speed(samples)
    if group:
        for b in hs.bins.values():
            b["n"] = b["n"] * GROUP_WEIGHT
    hs = leveled(hs)
    kp = NM._fit_kind(days, "hike")
    kp.pop("resid", None)
    kp["q25"], kp["q75"] = STOP_RANGE
    gain, loss = CO.gain_loss(z, CO.HYST_M)
    basis = {"kind": "track", "text": BASIS_TRACK, "trip_kind": "group" if group else "solo", "pack_kg": pack,
             "km": float(d[-1]) / 1000.0, "gain_m": float(gain), "loss_m": float(loss),
             "moving_s": float(moving_s), "elapsed_s": float(sum(r["elapsed_s"] for r in days)),
             "stopped_s": float(sum(r["stopped_s"] for r in days)), "days": len(days), "windows": len(samples)}
    return Ability(hs, kp, basis, group)


def account_ability(hike_speed, nonmoving_profile: Optional[dict], trip_kind: Optional[str] = None,
                    hike_days: Optional[int] = None) -> Ability:
    """The signed-in person's own walking curve (the 百岳 calculator's HikeSpeed) and stop fit (their
    profile's hike kind; 跟團 / 自己 when they marked such days). Raises EstimateError without a curve."""
    from backend.engine.racepower import nonmoving as NM
    if hike_speed is None or not getattr(hike_speed, "n_samples", 0):
        raise EstimateError("NO_DATA", _("你的帳號還沒有登山或陡坡的紀錄，估不出你的腳程"))
    kinds = (nonmoving_profile or {}).get("kinds") or {}
    want = NM.HIKE_SUB.get(trip_kind or "", "hike")
    kp = kinds.get(want) if (kinds.get(want) or {}).get("n") else kinds.get("hike")
    if not kp:
        kp = NM._fit_kind([], "hike")
        kp.pop("resid", None)
        kp["q25"], kp["q75"] = STOP_RANGE
    basis = {"kind": "account", "text": BASIS_ACCOUNT, "windows": int(hike_speed.n_samples),
             "days": int(hike_days if hike_days is not None else getattr(hike_speed, "n_days", 0) or 0),
             "stop_days": int(kp.get("n") or 0)}
    return Ability(leveled(hike_speed), dict(kp), basis, False)


# ---------------------------------------------------------------------------- the route

@dataclass
class Route:
    pieces: list                  # [{"dist_m", "g", "z", "up", "down", "cls"}]
    km: float
    gain_m: float
    loss_m: float
    top_m: float
    days: int


def route_of(data: bytes, filename: str = "", days: Optional[int] = None) -> Route:
    """The route GPX / FIT → 100 m pieces of the smoothed profile (raises EstimateError)."""
    from backend.engine.racepower import course as CO
    track = _parse(data, filename, _("路線"))
    td = CO.track_distance(track)
    if len(td["d"]) < 2 or td["d"][-1] < MIN_KM * 1000.0:
        raise EstimateError("TOO_SHORT", _("路線太短（不到 1 公里）"))
    xs, raw = CO.resample(td["d"], td["z"], CO.STEP_M)
    zs = CO.smooth(raw, CO.STEP_M, CO.DEFAULT_SIGMA_M)
    edges = list(np.arange(0.0, float(xs[-1]), WIN_M)) + [float(xs[-1])]
    zi = np.interp(edges, xs, zs)
    pieces = []
    for a, b, za, zb in zip(edges[:-1], edges[1:], zi[:-1], zi[1:]):
        dist = b - a
        if dist <= 0.5:
            continue
        g = float((zb - za) / dist)
        pieces.append({"dist_m": dist, "g": g, "z": float(0.5 * (za + zb)), "up": max(0.0, float(zb - za)),
                       "down": max(0.0, float(za - zb)), "cls": _part(g)})
    gain, loss = CO.gain_loss(zs, CO.HYST_M)
    n = int(days) if days else 1
    return Route(pieces, float(xs[-1]) / 1000.0, float(gain), float(loss), float(np.max(zs)),
                 max(1, min(MAX_DAYS, n)))


def _part(g: float) -> str:
    """上坡 / 平路 / 下坡 of a hike piece (|grade| ≤ FLAT_PCT is flat)."""
    p = g * 100.0
    return "up" if p > FLAT_PCT else "down" if p < -FLAT_PCT else "flat"


def general_s(p: dict) -> float:
    """The common ruler's time of one piece (s): Munter's 4 km/h with 100 m of climb = 1 km, plus a
    fixed descent cost (200 m down = 1 km). 經驗法則 + 推估."""
    G = GENERAL
    km = p["dist_m"] / 1000.0 + p["up"] / G["up_m_per_km"] + p["down"] / G["down_m_per_km"]
    return km / G["flat_kmh"] * 3600.0


# ---------------------------------------------------------------------------- the estimate

def default_route_pack(days: int) -> float:
    from backend.engine.racepower.capacity import PACK_DEFAULT_MULTI, PACK_DEFAULT_SINGLE
    return PACK_DEFAULT_MULTI if days > 1 else PACK_DEFAULT_SINGLE


def pct_of(ratio: float) -> int:
    """A time ratio → a whole percent (positive: slower)."""
    return int(round((ratio - 1.0) * 100.0))


def estimate(ab: Ability, route: Route, pack_kg: Optional[float] = None) -> dict:
    """The person on the route: moving and total time (with ranges), the stops, the 上坡 / 平路 / 下坡
    parts and the ratio to the common ruler (time ÷ the ruler's: > 1 = slower)."""
    from backend.engine.racepower import env as ENV
    from backend.engine.racepower import hike as HK
    from backend.engine.racepower import nonmoving as NM
    pack = float(pack_kg) if pack_kg is not None else default_route_pack(route.days)
    pf = HK.pack_factor(DEFAULT_WEIGHT_KG, REF_PACK_KG, pack)
    ref = {"altitude_m": ab.speed.ref_alt_m if ab.speed.ref_alt_m is not None else route.pieces[0]["z"]}
    alt = ENV.segment_factors([p["z"] for p in route.pieces], ref, ref, "unacclimatised")
    parts = {k: {"km": 0.0, "gain_m": 0.0, "loss_m": 0.0, "person_s": 0.0, "general_s": 0.0} for k in ("up", "flat", "down")}
    moving = general = uncovered = 0.0
    for p, a in zip(route.pieces, alt):
        v = ab.speed.v(p["g"]) * pf * max(0.3, a)
        t = p["dist_m"] / v if v > 0 else math.inf
        gs = general_s(p)
        q = parts[p["cls"]]
        q["km"] += p["dist_m"] / 1000.0
        q["gain_m"] += p["up"]
        q["loss_m"] += p["down"]
        q["person_s"] += t
        q["general_s"] += gs
        moving += t
        general += gs
        if not ab.covered(p["g"]):
            uncovered += t
    if not math.isfinite(moving) or moving <= 0:
        raise EstimateError("NO_ESTIMATE", _("這條路線算不出時間"))
    for q in parts.values():
        q["ratio"] = q["person_s"] / q["general_s"] if q["general_s"] > 0 else None
        q["pct"] = pct_of(q["ratio"]) if q["ratio"] else None
        q["vam_m_h"] = q["gain_m"] / q["person_s"] * 3600.0 if q["person_s"] > 0 and q["gain_m"] > 0 else None
        q["speed_kmh"] = q["km"] / q["person_s"] * 3600.0 if q["person_s"] > 0 else None
    cover = 1.0 - uncovered / moving
    r = MOVE_BASE + MOVE_EXTRA * (1.0 - cover) + (GROUP_EXTRA if ab.group else 0.0)
    # stops: per day (the share curve is capped at 2.5 h, so a long day's share is the cap's)
    day_mv = moving / route.days
    share = NM.share_at(ab.stop, day_mv)
    stop = share * moving
    stop_lo, stop_hi = stop * min(1.0, ab.stop["q25"]), stop * max(1.0, ab.stop["q75"])
    ratio = moving / general
    return {
        "model": MODEL, "badge": N_("推估"),
        "route": {"km": route.km, "gain_m": route.gain_m, "loss_m": route.loss_m, "top_m": route.top_m,
                  "days": route.days, "pack_kg": pack},
        "moving_s": moving, "moving_range_s": [moving * (1 - r), moving * (1 + r)],
        "stop": {"total_s": stop, "range_s": [stop_lo, stop_hi], "share": share, "track_days": int(ab.stop.get("n") or 0),
                 "prior_share": ab.stop.get("prior")},
        "total_s": moving + stop, "total_range_s": [moving * (1 - r) + stop_lo, moving * (1 + r) + stop_hi],
        "general": {"moving_s": general, "ratio": ratio, "pct": pct_of(ratio)},
        "parts": {k: {x: q[x] for x in ("km", "gain_m", "loss_m", "person_s", "ratio", "pct", "vam_m_h", "speed_kmh")}
                  for k, q in parts.items()},
        "speeds": {"flat_kmh": ab.speed.v(0.0) * pf * 3.6,
                   "up_vam_m_h": ab.speed.v(0.20) * pf * 0.20 * 3600.0,
                   "down_kmh": ab.speed.v(-0.20) * pf * 3.6},
        "coverage": cover,
        "basis": dict(ab.basis),
    }


def compare(a: dict, b: dict) -> dict:
    """Two estimates on the same route: a's time ÷ b's (> 1 = a is slower), overall and per part."""
    def ratio(x, y):
        return x / y if x and y else None
    out = {"moving": ratio(a["moving_s"], b["moving_s"]), "total": ratio(a["total_s"], b["total_s"]), "parts": {}}
    for k in ("up", "flat", "down"):
        out["parts"][k] = ratio(a["parts"][k]["person_s"], b["parts"][k]["person_s"])
    out["pct"] = pct_of(out["moving"]) if out["moving"] else None
    out["parts_pct"] = {k: pct_of(v) if v else None for k, v in out["parts"].items()}
    return out

