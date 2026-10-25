"""
跟團 / 自己 suggestion for a past hike (SP-731 shows it; the rule is SP-716's, SP-724 refines it).

A group hike is paced by the group: the athlete's heart rate sits low in their own range AND they climb
slower than their own trail runs climb at that heart rate and grade. Rule (SP-716 提案表 E, 推估):

    hr_pct    = where the hike's moving HR falls in the athlete's own trail-run moving HRs (0…1; relative,
                not ÷ LTHR — SP-716: the LTHR in effect may be off, SP-719)
    vam_ratio = the hike's climbs' VAM ÷ the VAM the athlete's own-paced trail-run climbs reach at the
                same HR and grade (median over the hike's climbs; engine/climb_vam.extract_climbs, the
                VAM:HR chart's climbs; the expected VAM by OLS of ln VAM on HR and grade)

    hr_pct < 0.35 and vam_ratio < 0.90 → suggest 跟團
    hr_pct > 0.50 and vam_ratio > 0.90 → suggest 自己
    otherwise (or too little data)     → no suggestion

SP-724 adds the stops as a third vote (the SP-716 diary check: group days stop 0.85–2.0 × their moving
time, the owner's own runs ~0.44):

    stop_share = the hike's stopped ÷ moving time (all its days; nonmoving.run_row, > 1 km/h moving)

    each sign votes: hr_pct < 0.35 → 跟團, > 0.50 → 自己 (between: no vote); vam_ratio < 0.90 → 跟團,
    > 0.90 → 自己; stop_share ≥ 0.80 → 跟團, < 0.80 → 自己
    ≥ 2 votes and more than the other side → that suggestion; otherwise none

On the four diary-checked group trips of SP-716 (hr_pct / vam_ratio / stop_share: 0.06 / 0.77 / 1.22,
0.04 / 0.91 / 2.01, 0.54 / 0.64 / 0.85, 0.04 / 0.68 / 1.49) the two-sign rule suggests 跟團 for two, the
three votes for all four. Without the stops (no samples) the two-sign rule above stays.

Only a suggestion: the list shows it faded and one click makes it the user's mark (activity_tags.GROUPS);
nothing reads the suggestion itself. Cached per activity (the climbs: climb_vam's cached series; the HR:
athlete.intensity_stats) and per dataset (ds.memo).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

RULE = {
    "group_hr_pct": 0.35, "group_vam": 0.90,      # SP-716 (推估)
    "solo_hr_pct": 0.50, "solo_vam": 0.90,        # SP-716 (推估)
    "min_ref_runs": 5,                             # 推估: own trail runs with HR for the percentile
    "min_ref_climbs": 20,                          # 推估: own-paced climbs for the expected VAM
    "min_climbs": 1,                               # a hike needs ≥ 1 climb with HR for its ratio
    "group_stop": 0.80,                            # SP-724 (推估): stopped ÷ moving ≥ this votes 跟團
}
SOURCE = ("跟團建議（推估）：心率在你自己越野跑心率裡的百分位（< 0.35 投跟團、> 0.5 投自己）、爬坡 VAM ÷ 你自己同心率同坡度的預期"
          "（< 0.9 投跟團、> 0.9 投自己）、停留 ÷ 移動時間（≥ 0.8 投跟團、< 0.8 投自己），兩票以上而且比另一邊多就建議；"
          "沒有停留資料時：百分位 < 0.35 且 VAM 比 < 0.9 → 跟團，> 0.5 且 > 0.9 → 自己")


def hr_percentile(hr: Optional[float], ref) -> Optional[float]:
    """The share of `ref` below `hr` (ties count half); None without enough reference runs."""
    v = [float(x) for x in ref or [] if x and math.isfinite(x)]
    if hr is None or not math.isfinite(hr) or len(v) < RULE["min_ref_runs"]:
        return None
    below = sum(1 for x in v if x < hr)
    ties = sum(1 for x in v if x == hr)
    return (below + 0.5 * ties) / len(v)


def fit_vam(climbs) -> Optional[dict]:
    """ln VAM = a + b·HR + c·grade over the own-paced climbs [{vam, avg_hr, grade}]; None with too few."""
    c = [x for x in climbs or [] if x.get("vam") and x["vam"] > 0 and x.get("avg_hr") and x.get("grade") is not None]
    if len(c) < RULE["min_ref_climbs"]:
        return None
    X = np.column_stack([np.ones(len(c)), [x["avg_hr"] for x in c], [x["grade"] for x in c]])
    y = np.log([x["vam"] for x in c])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    return {"a": float(coef[0]), "b": float(coef[1]), "c": float(coef[2]), "n": len(c)}


def vam_ratio(model: Optional[dict], climbs) -> Optional[float]:
    """exp(median(ln VAM − expected)) over the hike's climbs; None without a model or a climb."""
    if not model:
        return None
    r = [math.log(x["vam"]) - (model["a"] + model["b"] * x["avg_hr"] + model["c"] * x["grade"])
         for x in climbs or [] if x.get("vam") and x["vam"] > 0 and x.get("avg_hr") and x.get("grade") is not None]
    if len(r) < RULE["min_climbs"]:
        return None
    return float(math.exp(float(np.median(r))))


def suggest(hr_pct: Optional[float], ratio: Optional[float], stop_share: Optional[float] = None) -> Optional[str]:
    """"group" / "solo" / None by the SP-716 rule; with `stop_share` (stopped ÷ moving) the SP-724 three
    votes (module docstring)."""
    if hr_pct is None or ratio is None:
        return None
    k = RULE
    if stop_share is None or not math.isfinite(stop_share):
        if hr_pct < k["group_hr_pct"] and ratio < k["group_vam"]:
            return "group"
        if hr_pct > k["solo_hr_pct"] and ratio > k["solo_vam"]:
            return "solo"
        return None
    votes = []
    votes.append("group" if hr_pct < k["group_hr_pct"] else "solo" if hr_pct > k["solo_hr_pct"] else None)
    votes.append("group" if ratio < k["group_vam"] else "solo" if ratio > k["solo_vam"] else None)
    votes.append("group" if stop_share >= k["group_stop"] else "solo")
    g, s = votes.count("group"), votes.count("solo")
    if g >= 2 and g > s:
        return "group"
    if s >= 2 and s > g:
        return "solo"
    return None


def stop_share(ds, w) -> Optional[float]:
    """SP-724: one hike's stopped ÷ moving time over all its days (athlete.nonmoving_day_rows, the stop
    model's own definition); None without samples."""
    from backend.engine.racepower import athlete as A
    try:
        rows = [r for r in A.nonmoving_day_rows(ds, w) if r]
    except Exception:                       # noqa: BLE001 — no samples: no stop vote
        return None
    mv = sum(float(r.get("moving_s") or 0.0) for r in rows)
    st = sum(float(r.get("stopped_s") or 0.0) for r in rows)
    return st / mv if mv > 0 else None


def _climbs(ds, w) -> list[dict]:
    from backend.engine.panels import climb_vam as PCV
    try:
        return (PCV._cached(ds, w) or {}).get("segments") or []
    except Exception:                       # noqa: BLE001 — no samples: no climbs
        return []


def _hr(ds, w) -> Optional[float]:
    from backend.engine.racepower import athlete as A
    try:
        return (A.intensity_stats(ds, w) or {}).get("hr_avg")
    except Exception:                       # noqa: BLE001
        return None


def compute(ds, tags: Optional[list] = None) -> dict:
    """{workout idx: {suggest, hr_pct, vam_ratio}} for every hike (athlete.is_hike, not a run); memoised
    on the dataset (by the tag rows: a 跟團 / 自己 mark moves a trail run in / out of the reference)."""
    from backend.engine import activity_tags as AT
    from backend.engine.racepower import athlete as A
    if tags is None:
        tags = AT.load()
    memo = getattr(ds, "memo", None)
    key = ("group_hint", id(tags), len(tags or []))
    if isinstance(memo, dict) and key in memo:
        return memo[key]
    solo = A.solo_hikes(tags=tags)
    runs = [w for w in ds.workouts if A.outdoor(w) and A.is_trail(w) and A.own_paced(w, tags, solo)]
    ref_hr = [h for h in (_hr(ds, w) for w in runs) if h]
    model = fit_vam([c for w in runs for c in _climbs(ds, w)])
    out = {}
    for w in ds.workouts:
        if not (A.is_hike(w) and w.sport != "run"):
            continue
        p = hr_percentile(_hr(ds, w), ref_hr)
        r = vam_ratio(model, _climbs(ds, w))
        st = stop_share(ds, w)
        out[w.idx] = {"suggest": suggest(p, r, st), "hr_pct": p, "vam_ratio": r, "stop_share": st}
    flush = getattr(ds, "flush_series", None)
    if flush:
        flush()
    if isinstance(memo, dict):
        memo[key] = out
    return out
