"""
Non-moving time of a trail race, predicted separately from the moving time
(docs/research/unsourced-rules.md §0.8, 2026-10-02).

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

Personal profile from the athlete's earlier races / 全力 trail runs ≥ 90 min
(the same samples as x*(T)): per run, long-stop seconds per moving hour,
the median long-stop length and the short-stop share of the moving time.

Prediction for a race of moving time T (all 推估; the literature has no
aid-station numbers — Kerhervé 2015 only reports that faster runners stop
less, §0.8 "未找到來源"):

1. the user typed minutes at the aid stations (race calculator) → those
   minutes are the long stops (the user wins);
2. aid stations listed without minutes (types aid / big / medical) → stations
   × the median long-stop length;
3. no stations → the long-stop rate × T hours;
plus the short-stop share × T, with p25 / p75 of the per-run totals as the
range. No history → nothing is predicted (None); the page then shows the
moving time only.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Optional

import numpy as np

NONMOVING = {
    "long_s": 60.0,            # 推估: a stop ≥ 60 s = an aid station / queue, shorter = a short stop
    "max_dt": 30.0,            # as activity_tags.rest_spells: a gap > 30 s is stopped (auto-pause)
    "min_runs": 2,             # 推估: a profile needs ≥ 2 earlier races
    "station_types": ("aid", "big", "medical"),   # fuel.STOP_TYPES that are real stops (water = on the move)
}
SOURCE = ("非移動時間：你之前比賽／全力越野跑的停留（≥ 60 秒算補給站／排隊，其餘算零碎停頓），"
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


def profile(rows) -> Optional[dict]:
    """The personal stop profile from run_row()s of earlier races; None with
    fewer than min_runs."""
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
            "source": SOURCE, "label": "推估"}


def predict(prof: Optional[dict], moving_s: Optional[float], stops=None) -> Optional[dict]:
    """Predicted non-moving seconds for a race of `moving_s` (see the module
    docstring). `stops` = the race calculator's aid stations [{km, minutes,
    type}]. None without a profile or a moving time."""
    if not prof or not moving_s or moving_s <= 0 or not math.isfinite(moving_s):
        return None
    h = moving_s / 3600.0
    stops = stops or []
    user_min = sum(float(s.get("minutes") or 0.0) for s in stops)
    stations = [s for s in stops if (s.get("type") or "aid") in NONMOVING["station_types"]]
    short = prof["short_share"] * moving_s
    if user_min > 0:
        long_, method = user_min * 60.0, "user"
        why = f"補給站停留用你填的 {user_min:g} 分"
    elif stations and prof.get("stop_len_s"):
        long_, method = len(stations) * prof["stop_len_s"], "stations"
        why = f"{len(stations)} 個補給站 × 你過去每次停留中位 {prof['stop_len_s'] / 60:.1f} 分"
    else:
        long_, method = prof["long_per_h_s"] * h, "rate"
        why = f"沒有補給站資料：你過去比賽每移動小時停 {prof['long_per_h_s'] / 60:.1f} 分（≥ 60 秒的停留）"
    total = long_ + short
    # the range scales the history's per-hour quartiles to this race, keeping the user's / station part
    lo = max(0.0, total + (prof["total_per_h_p25_s"] - prof["total_per_h_s"]) * h)
    hi = max(total, total + (prof["total_per_h_p75_s"] - prof["total_per_h_s"]) * h)
    return {"total_s": total, "long_s": long_, "short_s": short, "p25_s": lo, "p75_s": hi, "method": method,
            "why": why + f"；零碎停頓 {prof['short_share']:.1%} 的移動時間", "n_runs": prof["n"],
            "source": SOURCE, "badge": "推估"}
