"""
Non-moving time of a trail race, predicted separately from the moving time
(docs/research/unsourced-rules.md §0.8, 2026-10-02; per kind and by duration
since SP-721, 2026-10-19).

The trail HR pace model (trailhr.py) predicts MOVING time — the validated
target. A race's clock time also holds stops: aid stations, queues at the
start or on single track, gates, photo stops. The doc found 22–48 % of the
diary races "not moving" by a speed rule but only 0–5 % in stops ≥ 5 min;
the difference is short stops and slow walking. So the two parts are kept
apart and predicted with the SAME moving definition as the model (athlete.
RUN_MOVING_KMH, > 1 km/h; a recording gap > 30 s — a watch auto-pause — is
stopped time, as activity_tags.rest_spells):

* LONG stops = stopped spells ≥ LONG_S (60 s, 推估): aid stations, queues;
* SHORT stops = the rest of the stopped time (gates, GPS speed dropouts on a
  steep climb, a few seconds at a junction).

SP-721 — the stop share depends on the kind of outing and grows with the
moving time (SP-716: stop ÷ moving 0.31 under 1 h, 0.42 at 1–2 h, 0.51 at
2–3 h, ~1 over 3 h on this athlete's trail runs; races 0.62, hikes 0.72; a
fixed share was −10 % on the total time of the outings ≥ 2 h moving). A pure
power law (stop ∝ T^1.4–1.7) extrapolates wildly past the data, so:

* three KINDS, each from the athlete's own activities of that kind
  (athlete.nonmoving_history): "race" 比賽 (activity type 比賽 or 全力 —
  trail_hr_model's race_idx), "solo" 自主長程 (the other trail runs), "hike"
  健行／百岳 (hikes that are not runs, each day of a multi-day trip apart;
  split by 跟團 / 自己 since SP-732, below — athlete.nonmoving_kind /
  nonmoving_history are the places that classify);
* per kind, the stop share at moving time T (hours):

      share(T) = min(SHARE_MAX, c × clip(T, T_LO, T_CAP) ^ b)

  — flat below T_LO (1 h), growing as T^b up to T_CAP (2.5 h), flat after it
  (the cap: a 10 h race keeps the 2.5 h share, it never extrapolates), and
  never more than SHARE_MAX (1.0: stops ≤ the moving time);
* b = the kind's log-log slope of (share + 0.05) on clip(T) (OLS over its
  activities, only those longer than T_LO count as evidence), shrunk to the
  prior B_PRIOR (0.3) with K_SLOPE (5) pseudo-activities, clipped to
  [0, B_MAX]: the share never falls with the duration;
* c = the median of share ÷ clip(T)^b over the kind's activities, shrunk to
  the kind's prior share at 1 h (PRIOR: race 0.30, solo 0.40, hike 0.60) with
  K_LEVEL (5) pseudo-activities — a kind with no activities is its prior;
* the range = the 25th / 75th percentile of the kind's own share ÷ fit
  ratios (all kinds pooled while the kind has fewer than RANGE_MIN_N).

SP-732 — the hike kind is split by the user's 跟團 / 自己 mark (SP-731,
athlete.own_paced: a row's "trip" "group" / "solo"): two sub-kinds
"hike_group" and "hike_solo", each fitted like a kind but shrunk to the HIKE
fit (its c and b, K_LEVEL / K_SLOPE pseudo-days) instead of a fixed prior, so
a sub-kind with few marked days is the hike kind as a whole; its range falls
back to the hike kind's while it has fewer than RANGE_MIN_N days. The 百岳
calculator predicts each day of a trip apart (planner.hike_rest) with the
sub-kind of its trip kind (PlanIn.trip_kind; plan_kind).

SP-739 — inside each hike kind the stops are learnt per altitude class of the
route's top (郊山 < 1500 m, 中級山 1500–2999 m, 百岳 ≥ 3000 m; ALT_BANDS and
choose_alt below), with the fallbacks ≥ 3000 / < 3000 and then the hike kind
as a whole while a class has few trips.

All of these numbers are 推估: no literature has aid-station or stop numbers
(Kerhervé 2015 only reports that faster runners stop less, §0.8 "未找到來源");
the priors are rough values of one athlete's SP-716 data and only matter for a
kind with few activities. Leave-one-out on that athlete's 161 outings ≥ 20 min
(SP-721, moving time known): the total-time bias of the outings ≥ 2 h moving
went from −13.3 % (the old fixed race rate) to −1.0 %, the median |error| from
18.8 % to 18.1 %, and the actual stops fell inside p25–p75 in 47 % of them.

Prediction for an event of moving time T and kind k:

1. the user typed minutes at the aid stations (race calculator) → those
   minutes are the long stops (the user wins), plus the short part of the
   kind's share;
2. aid stations listed without minutes (types aid / big / medical) → stations
   × the kind's median long-stop length, plus the short part;
3. no stations → share(T) × T (its long part = the kind's median long share);
with p25 / p75 = the modelled part × the range ratios. No history at all
(fewer than MIN_RUNS activities of any kind) → nothing is predicted (None);
the page then shows the moving time only. A profile stored before SP-721
(no "kinds") is still predicted the old way (predict_legacy).
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional

import numpy as np

from backend.i18n import N_, _

NONMOVING = {
    "long_s": 60.0,            # 推估: a stop ≥ 60 s = an aid station / queue, shorter = a short stop
    "max_dt": 30.0,            # as activity_tags.rest_spells: a gap > 30 s is stopped (auto-pause)
    "min_runs": 2,             # 推估: a profile needs ≥ 2 earlier activities (any kind)
    "min_s": 20 * 60.0,        # 推估: activities with < 20 min moving say nothing about a race's stops
    "station_types": ("aid", "big", "medical"),   # fuel.STOP_TYPES that are real stops (water = on the move)
    # SP-721 (推估, see the module docstring)
    "kinds": ("race", "solo", "hike"),
    "prior": {"race": 0.30, "solo": 0.40, "hike": 0.60},   # stop ÷ moving at 1 h moving
    "b_prior": 0.3,            # share ∝ T^b between T_LO and T_CAP
    "k_level": 5.0,            # pseudo-activities of the prior level
    "k_slope": 5.0,            # pseudo-activities of the prior slope
    "t_lo_h": 1.0,             # flat below
    "t_cap_h": 2.5,            # flat above: the cap that stops the extrapolation
    "b_max": 1.0,
    "share_max": 1.0,          # stops never more than the moving time
    "log_eps": 0.05,           # log(share + eps): a run without stops still has a log
    "range_min_n": 8,          # fewer activities of the kind → the range from all kinds pooled
    "long_frac_default": 0.5,  # long ÷ all stops with no stops to read it from
}
KIND_LABEL = {"race": N_("比賽"), "solo": N_("自主長程"), "hike": N_("登山"),
              "hike_group": N_("登山（跟團）"), "hike_solo": N_("登山（自己走）")}
HIKE_SUB = {"group": "hike_group", "solo": "hike_solo"}     # SP-732: a hike row's "trip" → its sub-kind

# SP-739 — inside each hike kind (hike, hike_group, hike_solo) the stops are learnt per altitude
# class of the route's highest point (the user's decision, 2026-10-19): ≥ 3000 m 百岳, 1500–2999 m
# 中級山, < 1500 m 郊山. A past hike day is classed by the top of its whole trip (the plan classes a
# route by its top, every day alike). A class with fewer than ALT_MIN_TRIPS trips or ALT_MIN_DAYS
# days (推估) falls back to the two classes ≥ 3000 / < 3000 (only the 郊山 / 中級山 side can: ≥ 3000
# is the same class in both), then to the hike kind as a whole. Each class is fitted like a sub-kind,
# shrunk to its hike kind's c and b (K_LEVEL / K_SLOPE pseudo-days).
ALT_BANDS = ("low", "mid", "high", "lt3000")
ALT_HIGH_M = 3000.0
ALT_MID_M = 1500.0
ALT_MIN_TRIPS = 3           # 推估
ALT_MIN_DAYS = 5            # 推估
ALT_LABEL = {"low": N_("郊山"), "mid": N_("中級山"), "high": N_("百岳"), "lt3000": N_("3000 m 以下")}
ALT_RANGE = {"low": "< 1500 m", "mid": "1500–2999 m", "high": "≥ 3000 m", "lt3000": "< 3000 m"}


def alt_band(top_m) -> Optional[str]:
    """SP-739: the altitude class of a route / trip top: "high" ≥ 3000 m 百岳, "mid" 1500–2999 m 中級山,
    "low" < 1500 m 郊山; None without a top."""
    try:
        z = float(top_m)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(z):
        return None
    return "high" if z >= ALT_HIGH_M else "mid" if z >= ALT_MID_M else "low"


def _in_band(r: dict, band: str) -> bool:
    b = alt_band(r.get("top_m"))
    return b in ("low", "mid") if band == "lt3000" else b == band


def _n_trips(rs: list[dict]) -> int:
    """Trips of some rows (a row without a "trip_id" is a trip of its own)."""
    return len({r.get("trip_id", ("row", i)) for i, r in enumerate(rs)})
SOURCE = N_("非移動時間：依活動類型（比賽／自主長程／登山）用你之前同類活動的停留比例，隨移動時間增加、"
            "2.5 小時後不再增加，資料少時收縮到先驗；≥ 60 秒算補給站／排隊，其餘算零碎停頓，"
            "移動定義和心率配速模型一樣（> 1 km/h）；補給站停留沒有文獻數字（Kerhervé 2015 只說快的人停得少）；推估")
SOURCE_LEGACY = N_("非移動時間：你之前比賽／全力越野跑的停留（≥ 60 秒算補給站／排隊，其餘算零碎停頓），"
                   "移動定義和心率配速模型一樣（> 1 km/h）；補給站停留沒有文獻數字（Kerhervé 2015 只說快的人停得少）；推估")


def run_row(t, moving, long_s: float = NONMOVING["long_s"], max_dt: float = NONMOVING["max_dt"]) -> Optional[dict]:
    """One activity's stops from its time array and moving mask: elapsed,
    moving and stopped seconds, the long stops (spells ≥ long_s: total,
    count, lengths) and the short-stop seconds."""
    t = np.asarray(t, float)
    mv = np.asarray(moving, bool)
    n = min(len(t), len(mv))
    t, mv = t[:n], mv[:n]
    ok = np.isfinite(t)
    t, mv = t[ok], mv[ok]
    if len(t) < 2:
        return None
    d = np.diff(t)
    d[d < 0] = 0.0
    stop = (d > max_dt) | ~mv[1:]
    spells, run = [], 0.0
    for x, s in zip(d, stop):
        if s:
            run += x
        elif run > 0:
            spells.append(run)
            run = 0.0
    if run > 0:
        spells.append(run)
    elapsed = float(d.sum())
    stopped = float(d[stop].sum())
    longs = [s for s in spells if s >= long_s]
    moving_s = elapsed - stopped
    if moving_s <= 0:
        return None
    return {"elapsed_s": elapsed, "moving_s": moving_s, "stopped_s": stopped, "long_s": float(sum(longs)),
            "n_long": len(longs), "long_lengths": [float(x) for x in longs],
            "short_s": stopped - float(sum(longs))}


def _clip_h(T_h: float) -> float:
    return min(max(T_h, NONMOVING["t_lo_h"]), NONMOVING["t_cap_h"])


def share_at(kp: dict, moving_s: float) -> float:
    """The kind's stop ÷ moving share at `moving_s` (the capped curve)."""
    return min(NONMOVING["share_max"], kp["c"] * _clip_h(moving_s / 3600.0) ** kp["b"])


def _fit_kind(rs: list[dict], kind: str, parent: Optional[dict] = None) -> dict:
    """c, b (and the residual ratios) of one kind's rows; the prior with no rows. `parent` (SP-732,
    a hike sub-kind): shrunk to that fit's c and b instead of the fixed priors."""
    P = NONMOVING
    b_prior = parent["b"] if parent else P["b_prior"]
    b = b_prior
    T = [r["moving_s"] / 3600.0 for r in rs]
    sh = [r["stopped_s"] / r["moving_s"] for r in rs]
    n_ev = sum(1 for x in T if x > P["t_lo_h"])
    b_data = None
    if n_ev >= 3:
        x = np.log([_clip_h(v) for v in T])
        y = np.log(np.asarray(sh) + P["log_eps"])
        if float(np.ptp(x)) > 0:
            b_data = float(np.polyfit(x, y, 1)[0])
            b = (n_ev * b_data + P["k_slope"] * b_prior) / (n_ev + P["k_slope"])
    b = float(min(P["b_max"], max(0.0, b)))
    prior = parent["c"] if parent else P["prior"][kind]
    q = [s / _clip_h(v) ** b for s, v in zip(sh, T)]
    c_data = float(median(q)) if q else None
    c = ((len(q) * c_data if q else 0.0) + P["k_level"] * prior) / (len(q) + P["k_level"])
    stopped = [r for r in rs if r["stopped_s"] > 0]
    lengths = [x for r in rs for x in r.get("long_lengths") or []]
    kp = {"n": len(rs), "c": c, "b": b, "c_data": c_data, "b_data": b_data, "n_slope": n_ev, "prior": prior,
          "long_frac": float(median([r["long_s"] / r["stopped_s"] for r in stopped])) if stopped else None,
          "stop_len_s": float(median(lengths)) if lengths else None, "n_stops": len(lengths),
          "t_max_h": max(T) if T else None}
    kp["resid"] = [s / share_at(kp, v * 3600.0) for s, v in zip(sh, T) if share_at(kp, v * 3600.0) > 0]
    return kp


def profile(rows) -> Optional[dict]:
    """The personal stop profile from run_row()s of earlier activities, each
    with its "kind" (race / solo / hike; a row without one is a race, as
    before SP-721): per kind the share curve (c, b), the long-stop share and
    length, and the p25 / p75 range ratios. None with fewer than min_runs
    rows in all."""
    P = NONMOVING
    rs = [r for r in rows or [] if r and r.get("moving_s") and r["moving_s"] > 0 and r.get("stopped_s") is not None]
    if len(rs) < P["min_runs"]:
        return None
    kinds = {k: _fit_kind([r for r in rs if (r.get("kind") or "race") == k], k) for k in P["kinds"]}
    pooled_resid = [x for kp in kinds.values() for x in kp["resid"]]
    pooled_frac = [r["long_s"] / r["stopped_s"] for r in rs if r["stopped_s"] > 0]
    pooled_len = [x for r in rs for x in r.get("long_lengths") or []]

    def finish(kp: dict, res_fallback: list, from_label: str, frac_fallback, len_fallback) -> None:
        mine = kp.pop("resid")
        own = len(mine) >= P["range_min_n"]
        res = mine if own else res_fallback
        q = np.percentile(res, [25, 75]) if len(res) >= 2 else [1.0, 1.0]
        kp["q25"], kp["q75"] = float(min(1.0, q[0])), float(max(1.0, q[1]))
        kp["range_from"] = "kind" if own else from_label
        if kp["long_frac"] is None:
            kp["long_frac"] = frac_fallback
        if kp["stop_len_s"] is None and len_fallback:
            kp["stop_len_s"] = len_fallback
    # SP-732: the hike sub-kinds (跟團 / 自己) shrunk to the hike fit, before the hike fit loses its residuals
    hike = kinds["hike"]
    hike_rows = [r for r in rs if (r.get("kind") or "race") == "hike"]
    subs = {sub: _fit_kind([r for r in hike_rows if r.get("trip") == trip], sub, parent=hike)
            for trip, sub in HIKE_SUB.items()}
    # SP-739: the altitude classes of each hike kind, shrunk to that kind's fit
    bases = {"hike": hike_rows, **{sub: [r for r in hike_rows if r.get("trip") == trip] for trip, sub in HIKE_SUB.items()}}
    fits = {"hike": hike, **subs}
    alts = {}
    for base, brs in bases.items():
        for band in ALT_BANDS:
            bs = [r for r in brs if _in_band(r, band)]
            kp = _fit_kind(bs, f"{base}@{band}", parent=fits[base])
            kp.update(n_trips=_n_trips(bs), parent=base, alt=band)
            alts[f"{base}@{band}"] = kp
    hike_resid = list(hike["resid"])
    for k, kp in kinds.items():
        finish(kp, pooled_resid, "pooled",
               float(median(pooled_frac)) if pooled_frac else P["long_frac_default"],
               float(median(pooled_len)) if pooled_len else None)
    for sub, kp in subs.items():
        own_hike = len(hike_resid) >= P["range_min_n"]
        finish(kp, hike_resid if own_hike else pooled_resid, "hike" if own_hike else "pooled",
               hike["long_frac"], hike.get("stop_len_s"))
        kp["parent"] = "hike"
        kinds[sub] = kp
    for key, kp in alts.items():
        base = kinds[kp["parent"]]
        mine = kp.pop("resid")
        if len(mine) >= P["range_min_n"]:
            q = np.percentile(mine, [25, 75])
            kp["q25"], kp["q75"], kp["range_from"] = float(min(1.0, q[0])), float(max(1.0, q[1])), "alt"
        else:                               # a thin class keeps its hike kind's range
            kp["q25"], kp["q75"], kp["range_from"] = base["q25"], base["q75"], base["range_from"]
        if kp["long_frac"] is None:
            kp["long_frac"] = base["long_frac"]
        if kp["stop_len_s"] is None:
            kp["stop_len_s"] = base.get("stop_len_s")
        kp["n_trips_parent"] = _n_trips(bases[kp["parent"]])
        kinds[key] = kp
    return {"v": 3, "n": len(rs), "kinds": kinds,
            "params": {k: P[k] for k in ("t_lo_h", "t_cap_h", "share_max", "b_prior", "k_level", "k_slope")},
            "source": SOURCE, "label": "推估"}


def legacy_profile(rows) -> Optional[dict]:
    """The pre-SP-721 profile (races only, fixed per-hour rates) — kept for comparison in the
    back-test; predict() reads it through predict_legacy."""
    rs = [r for r in rows or [] if r and r.get("moving_s")]
    if len(rs) < NONMOVING["min_runs"]:
        return None
    rate = [r["long_s"] / (r["moving_s"] / 3600.0) for r in rs]
    short = [r["short_s"] / r["moving_s"] for r in rs]
    total = [r["stopped_s"] / (r["moving_s"] / 3600.0) for r in rs]
    lengths = [x for r in rs for x in r.get("long_lengths") or []]
    q = np.percentile(total, [25, 75]) if len(total) >= 2 else [total[0], total[0]]
    return {"n": len(rs), "long_per_h_s": float(median(rate)), "short_share": float(median(short)),
            "stop_len_s": float(median(lengths)) if lengths else None, "n_stops": len(lengths),
            "total_per_h_s": float(median(total)), "total_per_h_p25_s": float(q[0]), "total_per_h_p75_s": float(q[1]),
            "source": SOURCE_LEGACY, "label": "推估"}


def _user_and_stations(stops) -> tuple[float, list]:
    stops = stops or []
    user_min = sum(float(s.get("minutes") or 0.0) for s in stops)
    stations = [s for s in stops if (s.get("type") or "aid") in NONMOVING["station_types"]]
    return user_min, stations


def _alt_enough(kp: Optional[dict]) -> bool:
    return bool(kp) and kp.get("n_trips", 0) >= ALT_MIN_TRIPS and kp.get("n", 0) >= ALT_MIN_DAYS


def choose_alt(prof: dict, kind: str, top_m) -> Optional[dict]:
    """SP-739: which altitude class of hike kind `kind` a route with top `top_m` uses: {key (the
    prof["kinds"] key to predict with), band (the route's class), used ("low" / "mid" / "high" /
    "lt3000", or None = the hike kind as a whole), scheme ("three" / "two" / "all"), n_trips, n_days,
    n_band (the trips of the route's own class), top_m, why}. None for a kind that is not a hike, a
    profile without classes (stored before SP-739) or no top. A sub-kind with no days of its own
    (跟團 / 自己 not marked yet) is the hike kind as a whole (SP-732), so its classes are the hike
    kind's."""
    ks = prof.get("kinds") or {}
    band = alt_band(top_m)
    if band is None or not str(kind).startswith("hike") or f"{kind}@high" not in ks:
        return None
    base = kind if ks.get(kind, {}).get("n") or kind == "hike" else "hike"
    own = ks.get(f"{base}@{band}")
    lt = ks.get(f"{base}@lt3000")
    lab = {b: _(ALT_LABEL[b]) for b in ALT_BANDS}
    common = {"band": band, "top_m": float(top_m), "n_band": (own or {}).get("n_trips", 0),
              "label": lab[band], "range": ALT_RANGE[band]}
    if _alt_enough(own):
        used, scheme, kp = band, "three", own
        why = _("路線最高 {top:,.0f} m → {band}（{rng}），用你之前{band}的 {t} 趟（{d} 天）",
                top=float(top_m), band=lab[band], rng=ALT_RANGE[band], t=kp["n_trips"], d=kp["n"])
    elif band != "high" and _alt_enough(lt):
        used, scheme, kp = "lt3000", "two", lt
        why = _("路線最高 {top:,.0f} m → {band}（{rng}），但{band}只有 {t0} 趟 {d0} 天（要 {min_t} 趟、{min_d} 天以上，推估），"
                "改用 3000 m 以下的 {t} 趟（{d} 天）",
                top=float(top_m), band=lab[band], rng=ALT_RANGE[band], t0=common["n_band"], d0=(own or {}).get("n", 0),
                min_t=ALT_MIN_TRIPS, min_d=ALT_MIN_DAYS, t=kp["n_trips"], d=kp["n"])
    else:
        used, scheme, kp = None, "all", ks.get(base) or {}
        t_all = (own or lt or {}).get("n_trips_parent", 0)
        why = (_("路線最高 {top:,.0f} m → {band}（{rng}），但{band}只有 {t0} 趟 {d0} 天（要 {min_t} 趟、{min_d} 天以上，推估），"
                 "改用所有登山的 {t} 趟", top=float(top_m), band=lab[band], rng=ALT_RANGE[band], t0=common["n_band"], d0=(own or {}).get("n", 0),
                 min_t=ALT_MIN_TRIPS, min_d=ALT_MIN_DAYS, t=t_all) if band == "high" else
               _("路線最高 {top:,.0f} m → {band}（{rng}），但{band}只有 {t0} 趟 {d0} 天、3000 m 以下也只有 {t2} 趟 {d2} 天"
                 "（要 {min_t} 趟、{min_d} 天以上，推估），改用所有登山的 {t} 趟",
                 top=float(top_m), band=lab[band], rng=ALT_RANGE[band], t0=common["n_band"], d0=(own or {}).get("n", 0),
                 t2=(lt or {}).get("n_trips", 0), d2=(lt or {}).get("n", 0), min_t=ALT_MIN_TRIPS, min_d=ALT_MIN_DAYS, t=t_all))
        return {**common, "key": base, "used": None, "scheme": scheme, "n_trips": t_all, "n_days": kp.get("n", 0),
                "short": _("所有登山 {t} 趟", t=t_all), "why": why}
    return {**common, "key": f"{base}@{used}", "used": used, "scheme": scheme, "n_trips": kp["n_trips"],
            "n_days": kp["n"], "short": _("{band} {t} 趟", band=lab[used], t=kp["n_trips"]), "why": why}


def predict(prof: Optional[dict], moving_s: Optional[float], stops=None, kind: str = "race",
            top_m: Optional[float] = None) -> Optional[dict]:
    """Predicted non-moving seconds for an event of `moving_s` and `kind`
    (race / solo / hike; see the module docstring). `stops` = the race
    calculator's aid stations [{km, minutes, type}]. `top_m` (SP-739, a hike
    kind): the route's highest point → its altitude class (choose_alt). None
    without a profile or a moving time."""
    if not prof or not moving_s or moving_s <= 0 or not math.isfinite(moving_s):
        return None
    if "kinds" not in prof:
        return predict_legacy(prof, moving_s, stops)
    if kind not in prof["kinds"]:
        # a hike sub-kind on a profile stored before SP-732 → the hike kind; anything else → race
        kind = "hike" if str(kind).startswith("hike") and "hike" in prof["kinds"] else "race"
    alt = choose_alt(prof, kind, top_m) if top_m is not None else None
    kp = prof["kinds"][alt["key"]] if alt and alt["used"] else prof["kinds"][kind]
    share = share_at(kp, moving_s)
    s_model = share * moving_s
    long_model = s_model * kp["long_frac"]
    short = s_model - long_model
    user_min, stations = _user_and_stations(stops)
    if user_min > 0:
        long_, method, part = user_min * 60.0, "user", short
        why = _("補給站停留用你填的 {m:g} 分", m=user_min)
    elif stations and kp.get("stop_len_s"):
        long_, method, part = len(stations) * kp["stop_len_s"], "stations", short
        why = _("{n} 個補給站 × 你過去每次停留中位 {m:.1f} 分", n=len(stations), m=kp["stop_len_s"] / 60)
    else:
        long_, method, part = long_model, "rate", s_model
        why = _("沒有補給站資料：補給站／排隊佔停留的 {f:.0%}", f=kp["long_frac"])
    total = long_ + short
    lo = max(0.0, total + part * (kp["q25"] - 1.0))
    hi = max(total, total + part * (kp["q75"] - 1.0))
    capped = moving_s / 3600.0 > NONMOVING["t_cap_h"]
    sub = kp.get("parent") == "hike" and not kp.get("alt")
    if alt and alt["used"]:
        basis = _("{label}：{why}，停留約移動時間的 {s:.0%}", label=_(KIND_LABEL[kind]), why=alt["why"], s=share)
        if kp["n"] < 2 * NONMOVING["k_level"]:
            basis += _("（天數少，往{parent}所有登山日的 {p:.0%} 收縮）", parent=_(KIND_LABEL[kp["parent"]]), p=kp["prior"])
    else:
        if sub and not kp["n"]:
            basis = _("{label}：你還沒有標成這一類的健行日，用你所有健行日估，停留約移動時間的 {s:.0%}",
                      label=_(KIND_LABEL[kind]), s=share)
        elif kp["n"]:
            basis = _("{label}：用你之前 {n} 次同類活動估，停留約移動時間的 {s:.0%}", label=_(KIND_LABEL[kind]), n=kp["n"], s=share)
        else:
            basis = _("{label}：你還沒有同類活動，停留用先驗的移動時間 {s:.0%}", label=_(KIND_LABEL[kind]), s=share)
        if kp["n"] and kp["n"] < 2 * NONMOVING["k_level"]:
            basis += (_("（次數少，往你所有健行日的 {p:.0%} 收縮）", p=kp["prior"]) if sub else
                      _("（次數少，往先驗 {p:.0%} 收縮）", p=kp["prior"]))
        if alt:                             # SP-739: no class with enough trips → the hike kind as a whole
            basis = alt["why"] + "；" + basis
    if capped:
        basis += _("；移動超過 {h:g} 小時後比例不再增加", h=NONMOVING["t_cap_h"])
    rng = {"kind": _("範圍是你同類活動停留的 25–75 百分位"),
           "alt": _("範圍是你這一類（海拔）登山日停留的 25–75 百分位"),
           "hike": _("範圍是你所有健行日停留的 25–75 百分位（這一類次數少）")}.get(
        kp.get("range_from"), _("範圍是你所有活動停留的 25–75 百分位（同類次數少）"))
    label = _(KIND_LABEL[kind]) + (" · " + alt["short"] if alt else "")
    return {"total_s": total, "long_s": long_, "short_s": short, "p25_s": lo, "p75_s": hi, "method": method,
            "kind": kind, "kind_label": label, "alt": alt, "share": share, "n_kind": kp["n"], "capped": capped,
            "why": basis + "；" + why, "range_why": rng, "n_runs": prof["n"],
            "source": _(SOURCE), "badge": "推估"}


def predict_legacy(prof: dict, moving_s: float, stops=None) -> dict:
    """The pre-SP-721 prediction (a stored profile without kinds): the race long-stop rate × T
    (or the stations / the user's minutes) plus the short-stop share × T."""
    h = moving_s / 3600.0
    user_min, stations = _user_and_stations(stops)
    short = prof["short_share"] * moving_s
    if user_min > 0:
        long_, method = user_min * 60.0, "user"
        why = _("補給站停留用你填的 {m:g} 分", m=user_min)
    elif stations and prof.get("stop_len_s"):
        long_, method = len(stations) * prof["stop_len_s"], "stations"
        why = _("{n} 個補給站 × 你過去每次停留中位 {m:.1f} 分", n=len(stations), m=prof["stop_len_s"] / 60)
    else:
        long_, method = prof["long_per_h_s"] * h, "rate"
        why = _("沒有補給站資料：你過去比賽每移動小時停 {m:.1f} 分（≥ 60 秒的停留）", m=prof["long_per_h_s"] / 60)
    total = long_ + short
    # the range scales the history's per-hour quartiles to this race, keeping the user's / station part
    lo = max(0.0, total + (prof["total_per_h_p25_s"] - prof["total_per_h_s"]) * h)
    hi = max(total, total + (prof["total_per_h_p75_s"] - prof["total_per_h_s"]) * h)
    return {"total_s": total, "long_s": long_, "short_s": short, "p25_s": lo, "p75_s": hi, "method": method,
            "kind": "race", "kind_label": _(KIND_LABEL["race"]),
            "why": why + _("；零碎停頓 {p:.1%} 的移動時間", p=prof["short_share"]), "n_runs": prof["n"],
            "range_why": _("範圍是你過去每移動小時停留的 25–75 百分位"),
            "source": _(prof.get("source") or SOURCE_LEGACY), "badge": "推估"}


def plan_kind(plan_type: Optional[str], event_kind: Optional[str] = None, trip_kind: Optional[str] = None) -> str:
    """SP-721: which kind's stops a calculator plan uses. 百岳 (type baiyue) → hike, split by the
    trip kind (SP-732: 跟團 → hike_group, 自己 → hike_solo; none → the hike kind as a whole); a
    trail plan whose plan event is a 越野賽 (planning kind "race") → race; any other trail plan (no
    event, or an event of kind 其他) → solo (自主長程)."""
    if plan_type == "baiyue":
        return HIKE_SUB.get(trip_kind or "", "hike")
    if event_kind == "race":
        return "race"
    return "solo"
