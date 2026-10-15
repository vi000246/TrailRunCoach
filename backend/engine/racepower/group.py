"""
A group's timetable next to the calculator's own times (SP-576…581; research SP-575).

The timetable comes from a 疊石 Cairn trip (engine/cairn.py `schedule_of`: each timed visit on OUR
track, by day). It is read only: the calculator's own numbers never change, the group's times are laid
over them.

    leg            = two timed rows next to each other on the same day (Cairn's day)
    group clock    = eta(next) − eta(this)                         (+ 24 h past midnight)
    group moving   = clock − rest(this)                            the day has a stop written in Cairn
                   ≈ clock × the past group days' moving ratio     no stop written that day (推估: a
                                                                   timetable often puts the stops
                                                                   inside its times; 80 % without
                                                                   records — SP-575 Q1-3)

Cairn's estimate chain adds a row's stop before leaving it, except at the day's first row (the
picked start time is the leaving time) — the same here. Legs shorter than MERGE_MIN_S are merged
with the next one (a timetable rounds to 15–30 min: a short leg's speed is mostly rounding, 推估).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from backend.engine.racepower import hike as HK

MERGE_MIN_S = 45 * 60          # 推估 (SP-575 §3.4)
PREFILL_COVER = 0.90           # the timetable covers ≥ 90 % of the route: its total can be the target


def moving_ratio(rows: Sequence[dict]) -> dict:
    """The group days' moving ÷ elapsed ratio (hike.moving_ratio): {"ratio", "n"}; n None = the default."""
    r, _src = HK.moving_ratio(rows or [])
    ok = [x for x in rows or [] if x.get("moving_s") and x.get("elapsed_s") and 0 < x["elapsed_s"] < 20 * 3600
          and 0.3 < x["moving_s"] / x["elapsed_s"] <= 1.0]
    return {"ratio": r, "n": len(ok) if len(ok) >= 3 else None}


def _hhmm(m: float) -> str:
    m = int(round(m)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _min(eta: str) -> int:
    h, m = eta.split(":")[:2]
    return int(h) * 60 + int(m)


def legs(points: Sequence[dict], ratio: float) -> dict:
    """The timetable as legs: {"legs": [...], "days": [...], "moving_s", "clock_s", "rest_s", "km",
    "ratio_used"}. A leg: {day, from, to, from_km, to_km, from_eta, to_eta, clock_s, rest_s, moving_s,
    rest: "cairn" | "ratio", n (rows merged into it + 1)}."""
    by_day: dict[int, list[dict]] = {}
    for p in points or []:
        by_day.setdefault(int(p["day"]), []).append(p)
    out, days = [], []
    for day in sorted(by_day):
        ps = by_day[day]
        if len(ps) < 2:
            continue
        known = any((p.get("rest_min") or 0) > 0 for p in ps[1:-1])
        raw = []
        for i in range(1, len(ps)):
            a, b = ps[i - 1], ps[i]
            clock = (_min(b["eta"]) - _min(a["eta"])) % 1440 * 60.0
            rest = (a.get("rest_min") or 0) * 60.0 if (known and i > 1) else 0.0
            mv = clock - rest if known else clock * ratio
            if clock <= 0 or mv <= 0 or b["km"] <= a["km"]:
                continue
            raw.append({"day": day, "from": a["name"], "to": b["name"], "from_km": a["km"], "to_km": b["km"],
                        "from_eta": a["eta"], "to_eta": b["eta"], "clock_s": clock, "rest_s": rest, "moving_s": mv,
                        "rest": "cairn" if known else "ratio", "n": 1})
        merged: list[dict] = []
        for lg in raw:
            if merged and merged[-1]["clock_s"] < MERGE_MIN_S:
                merged[-1] = _join(merged[-1], lg)
            else:
                merged.append(lg)
        if len(merged) > 1 and merged[-1]["clock_s"] < MERGE_MIN_S:
            last = merged.pop()
            merged[-1] = _join(merged[-1], last)
        if not merged:
            continue
        out.extend(merged)
        days.append({"day": day, "start": merged[0]["from_eta"], "moving_s": sum(x["moving_s"] for x in merged),
                     "clock_s": sum(x["clock_s"] for x in merged), "rest": merged[0]["rest"],
                     "from_km": merged[0]["from_km"], "to_km": merged[-1]["to_km"]})
    return {"legs": out, "days": days, "moving_s": sum(x["moving_s"] for x in out),
            "clock_s": sum(x["clock_s"] for x in out), "rest_s": sum(x["rest_s"] for x in out),
            "km": sum(x["to_km"] - x["from_km"] for x in out),
            "ratio_used": any(x["rest"] == "ratio" for x in out)}


def _join(a: dict, b: dict) -> dict:
    return {**a, "to": b["to"], "to_km": b["to_km"], "to_eta": b["to_eta"], "clock_s": a["clock_s"] + b["clock_s"],
            "rest_s": a["rest_s"] + b["rest_s"], "moving_s": a["moving_s"] + b["moving_s"], "n": a["n"] + b["n"]}


def for_import(schedule: Optional[dict], route_km: float, ratio_rows: Sequence[dict]) -> Optional[dict]:
    """What an import shows of the timetable (SP-576): the points (kept with the trip), the legs, the
    group's total moving time and whether it fills the target time (it covers the route)."""
    if not schedule or not schedule.get("points"):
        return None
    mr = moving_ratio(ratio_rows)
    lg = legs(schedule["points"], mr["ratio"])
    if not lg["legs"]:
        return None
    cover = lg["km"] / route_km if route_km else 0.0
    return {**schedule, **{k: lg[k] for k in ("legs", "days", "moving_s", "clock_s", "rest_s", "km", "ratio_used")},
            "ratio": mr["ratio"], "ratio_n": mr["n"], "cover": cover, "prefill": cover >= PREFILL_COVER}



# ---- SP-578: leg by leg, how hard keeping up is (research SP-575 §3) --------------------------------
#
# Your time on a leg is the calculator's capacity time at AeT (λ = 1, the AeT band: whatever mode or band
# the page shows), interpolated by km over its segments. r = your time ÷ the group's moving time is how
# much faster than your AeT walking the group goes. On a climb or the flat the capacity model is run the
# other way (Pandolf forwards — it is the speed ↔ metabolic rate of the model itself):
#
#     v′ = r · v_you ÷ (A(z) · f_time(h) · f_day · H · e^δ · 1/snow)     the modifiers taken off
#     e  = Pandolf(W, L, v′, G, η) ÷ Pandolf(W, L, v′/r, G, η)            1.0 = your capacity pace
#
# On a climb your capacity pace is v₀·e^δ, where Pandolf(v₀) = Ė_AeT: e is the group's rate ÷ your AeT
# rate. Where the flat walk caps your speed (the flat, gentle grades) it is ÷ the rate of that walk.
#
# Downhill Pandolf does not hold (the calculator caps the descent speed instead): there e = r, a speed
# ratio — the legs, not the heart. A leg's e is the time-weighted mean over its pieces.
#
# The tiers (SP-575 Q1-5, all 推估 — to be checked against the athlete's own group days, G6):
CUT_WAIT = 0.80                # below: the group is much slower — waiting, HR held under AeT
CUT_EASY = 0.95                # 0.80–0.95 easy
CUT_RIGHT = 1.05               # 0.95–1.05 about AeT (right for a multi-day trip); 1.05–ceiling hard
BEHIND_R = 1.10                # your AeT time > the group's × 1.1: you fall behind
CAP_R_DEFAULT = 1.15           # the ceiling (your faster quarter on steep windows) when none was measured
LONG_HARD_H = 3.0              # above AeT for longer than this in a day: hard to hold (CAP_BAND_MAX_H)
TIERS = ("wait", "easy", "right", "hard", "behind")


def tier(e: float, r: float, e_cap: float) -> str:
    if r > BEHIND_R or e > e_cap:
        return "behind"
    if e >= CUT_RIGHT:
        return "hard"
    if e >= CUT_EASY:
        return "right"
    if e >= CUT_WAIT:
        return "easy"
    return "wait"


def _pieces(rows: Sequence[dict], a: float, b: float) -> list[tuple[dict, float]]:
    """(row, share of it) of the segments between km a and b."""
    out = []
    for r in rows:
        lo, hi = max(a, r["start_km"]), min(b, r["end_km"])
        span = r["end_km"] - r["start_km"]
        if hi > lo and span > 0:
            out.append((r, (hi - lo) / span))
    return out


def effort_at(pieces: list[tuple[dict, float]], r: float, cap) -> float:
    """The leg's e at a speed ratio r (time-weighted over its pieces)."""
    num = den = 0.0
    for row, f in pieces:
        t = row["t_aet"] * f
        if cap is not None and row.get("base") and row["grade"] >= 0:
            b = row["base"] / row["e_delta"]
            g = 100.0 * row["grade"]
            # ÷ the rate at your own capacity speed: Ė_AeT on a climb (v₀·e^δ), less where the flat walk
            # caps the speed — there the group at your own pace is 1.0, not 「below AeT, waiting」
            e = HK.pandolf(cap.weight, row["L"], r * b, g, row["eta"]) / HK.pandolf(cap.weight, row["L"], b, g, row["eta"])
        else:
            e = r
        num += e * t
        den += t
    return num / den if den else r


def compare(rows: Sequence[dict], lg: dict, cap=None, cap_r: Optional[float] = None,
            sigma: float = 0.10) -> Optional[dict]:
    """Each leg of the timetable against your capacity: {"legs": [...], "days": [...], "tiers": {tier: n},
    "speed_only"}. rows: the plan's segments with {start_km, end_km, grade, day, t_aet (s at AeT, λ 1),
    base (m/s without A, f_time, f_day, H, band, snow; None = speed ratio only), e_delta, L, eta}.
    The plan's own numbers are not touched."""
    if not rows or not lg or not lg.get("legs"):
        return None
    cap_r = cap_r if cap_r and cap_r > 1.0 else CAP_R_DEFAULT
    out = []
    for x in lg["legs"]:
        pcs = _pieces(rows, x["from_km"], x["to_km"])
        you = sum(row["t_aet"] * f for row, f in pcs)
        if you <= 0 or x["moving_s"] <= 0:
            continue
        r = you / x["moving_s"]
        e = effort_at(pcs, r, cap)
        e_cap = max(CUT_RIGHT + 0.01, effort_at(pcs, cap_r, cap))
        up = sum(row["t_aet"] * f for row, f in pcs if row["grade"] >= 0) / you
        mid = 0.5 * (x["from_km"] + x["to_km"])
        day = next((row["day"] for row in rows if row["start_km"] <= mid <= row["end_km"]), x["day"])
        t = tier(e, r, e_cap)
        out.append({**x, "trip_day": int(day or 1), "you_s": you, "r": r, "e": e, "e_cap": e_cap,
                    "e_range": [effort_at(pcs, r * math.exp(-sigma), cap), effort_at(pcs, r * math.exp(sigma), cap)],
                    "tier": t, "up_share": up, "behind_s": max(0.0, you - x["moving_s"]) if t == "behind" else 0.0,
                    # downhill e is a speed ratio (the legs), not a heart rate above AeT
                    "above_aet": e >= CUT_RIGHT and up >= 0.5})
    if not out:
        return None
    days = []
    for n in sorted({x["trip_day"] for x in out}):
        ls = [x for x in out if x["trip_day"] == n]
        above = sum(x["moving_s"] for x in ls if x["above_aet"])
        days.append({"day": n, "group_s": sum(x["moving_s"] for x in ls), "you_s": sum(x["you_s"] for x in ls),
                     "above_aet_s": above, "long_hard": above > LONG_HARD_H * 3600.0,
                     "behind_n": sum(1 for x in ls if x["tier"] == "behind"),
                     "behind_s": sum(x["behind_s"] for x in ls),
                     "wait_n": sum(1 for x in ls if x["tier"] == "wait"),
                     "worst": max((x["tier"] for x in ls), key=TIERS.index),
                     "start": ls[0]["from_eta"], "legs": len(ls)})
    share = {k: sum(x["moving_s"] for x in out if x["tier"] == k) for k in TIERS}
    return {"legs": out, "days": days, "tiers": {k: sum(1 for x in out if x["tier"] == k) for k in TIERS},
            "share_s": share, "main": max(TIERS, key=lambda k: share[k]), "speed_only": cap is None,
            "cuts": {"wait": CUT_WAIT, "easy": CUT_EASY, "right": CUT_RIGHT, "behind_r": BEHIND_R, "cap_r": cap_r},
            "group_s": sum(x["moving_s"] for x in out), "you_s": sum(x["you_s"] for x in out),
            "rest_ratio": lg.get("ratio_used"), "badge": "推估"}
