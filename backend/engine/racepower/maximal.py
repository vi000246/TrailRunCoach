"""
Which activities may test the race-power CAPACITY model (CP / W′ / TTE / k):
only efforts that were maximal for their duration. Fixed 2026-10-01 after the
back-test showed that the HR "race-like" class (intensity.classify: ≥ half the
time ≥ 0.95 × LTHR) catches 5 km training runs slower than the athlete's own
half-marathon pace — which are not maximal and made the
model look 16.5 % too fast.

A capacity sample is one of (in this order):

1. a season-plan race (any priority, past date) matched to the activity by
   date, kind and distance (`match_events`);
2. a formal CP test bout (cptest / workout_review test_cp) — handled by the
   back-test, not here;
3. a self-paced maximal ROAD effort (`road_maximal`), all of:
   * distance within ±10 % of 5K / 10K / HM / M (the user's rule);
   * last-quarter HR ≥ f × LTHR, f = 1.00 for 5K / 10K, 0.95 HM, 0.90 M.
     Anchor: Friel's LTHR is the average HR of the last 20 min of a solo
     30-min all-out time trial (docs/research/uphill-athlete-mountain-
     metrics.md §161), so a maximal 20–60 min effort ends at or above LTHR;
     the 0.95 / 0.90 for the longer races are Friel's Z4 / Z3 lower bounds
     (zones.FRIEL_HR) — the mapping is 推估;
   * 5K / 10K only: the 30-s peak HR ≥ the observed HRmax − 10 bpm. Reaching
     near-maximal HR is a classic criterion of a maximal effort (Howley,
     Bassett & Welch 1995, MSSE 27:1292–1301: "some percentage of an
     age-adjusted estimate of maximal heart rate"); the 10-bpm tolerance and
     the observed (not age-predicted) HRmax — median of the top-5 per-run
     peaks held ≥ 120 s (cumulative, moving, after the shared HR cleaning
     hr_quality.clean — SP-265) in the 365 days up to the run — are 推估;
   * an even or negative split: second-half speed ≥ 0.98 × first half
     (pacing taxonomy: Abbiss & Laursen 2008, Sports Med 38:239–252; the 2 %
     tolerance is 推估);
   * power-duration monotonicity: the moving average power is not below the
     best moving average of any earlier road run (365 days) at least 1.5 ×
     as long. A mean-max power–duration curve is non-increasing by
     definition (a power held for 60 min was also available for 30 min), so
     a maximal 38-min effort below a power already held for an hour was not
     maximal. Needed because heat alone drives HR to "maximal" values on
     summer 5 km runs (e.g. ~15 % below the power the same runner held for
     over 2 h in a half marathon, at a near-HRmax 30-s peak). It reads raw past
     activities, not the fitted model; the 1.5 × is 推估;
4. a race-like TRAIL effort (`trail_maximal`; user correction 2026-10-01:
   trail races are > 10 km, never standard distances, and always slow in the
   second half — no distance bucket, no split rule), all of:
   * ≥ 10 km and ≥ 90 min moving (the user's description of their races;
     the user allowed 60–90 min, 90 taken as their races all exceed 10 km);
   * average HR ≥ x*(T) − 0.03 × LTHR (2026-10-02, unsourced-rules.md §A2:
     the literature prior of the full-effort HR curve, trailhr.auto_max_frac
     — 0.90 at 2 h, 0.83 at 8 h; it replaced 0.90 × LTHR and ≥ 2/3 of the
     time above AeT, which a full-effort race of ≥ 8 h cannot meet,
     Fornasiero 2018) — a sustained race effort, not a hike-paced outing;
   * or a race word in the title / tags (賽, race, 越野賽, 馬拉松, marathon)
     — then only the duration rule applies.

None of the rules uses the model's own sustainable power: selecting the
samples with the model being tested would be circular. Every number is in
MAXIMAL so the spec can list them.
"""
from __future__ import annotations

import re
from statistics import median
from typing import Optional

import numpy as np

from backend.engine.racepower import riegel as R

MAXIMAL = {
    "dist_tol": 0.10,                       # user rule (±10 % of a standard distance)
    "q4_frac": {"5k": 1.00, "10k": 1.00, "half": 0.95, "marathon": 0.90},
    "hrmax_tol_bpm": 10.0,                  # 推估 (Howley 1995 criterion, tolerance ours)
    "hrmax_cats": ("5k", "10k"),
    "split_min": 0.98,                      # 推估 tolerance on "even or negative"
    "longer_ratio": 1.5,                    # 推估: "much longer" = ≥ 1.5 × the effort's moving time
    "peak_hold_s": 30.0,                    # 推估: a run's peak HR = highest bpm held ≥ 30 s (cumulative)
    "hrmax_hold_s": 120.0,                  # 推估: for HRmax the per-run peak must be held ≥ 120 s (strap spikes)
    "hrmax_top_n": 5,                       # 推估: observed HRmax = median of the top-5 per-run peaks
    "trail_min_km": 10.0,
    "trail_min_s": 90 * 60.0,
    "trail_avg_frac": 0.90,                 # Friel Z3 lower bound
    "trail_above_aet": 2.0 / 3.0,           # 推估
    "event_km_tol": 0.10,                   # 推估: plan event distance vs the watch
}
RACE_WORDS = re.compile(r"賽|馬拉松|race|marathon", re.I)
EVENT_KINDS = {"road": "road", "race": "trail"}   # planning.KINDS → activity category


def race_category(km: Optional[float], tol: float = MAXIMAL["dist_tol"]) -> Optional[str]:
    """5k / 10k / half / marathon when `km` is within ±tol of the standard."""
    if not km:
        return None
    for cat, (std, _, _) in R.STD_DISTANCES.items():
        if abs(km * 1000.0 / std - 1.0) <= tol:
            return cat
    return None


def peak_hr(hist, lo: int = 40, hold_s: float = MAXIMAL["peak_hold_s"]) -> Optional[float]:
    """Highest bpm with ≥ hold_s seconds at or above it (1-bpm histogram)."""
    if not hist:
        return None
    h = np.asarray(hist, float)
    cs = np.cumsum(h[::-1])[::-1]
    ok = np.nonzero(cs >= hold_s)[0]
    return float(lo + ok[-1]) if len(ok) else None


def hrmax_observed(peaks, top_n: int = MAXIMAL["hrmax_top_n"]) -> Optional[float]:
    """Median of the top-n per-run peaks (each held ≥ hrmax_hold_s): robust to
    a strap spike (in one runner's data the top 30-s peaks were 15–30 bpm
    above the rest; held 120 s the top five sat within ~10 bpm)."""
    v = sorted((float(p) for p in peaks if p), reverse=True)[:top_n]
    return float(median(v)) if v else None


def run_hrmax_peak(t, hr, kmh=None, cadence_spm=None, min_kmh: float = 1.0,
                   hold_s: float = MAXIMAL["hrmax_hold_s"]) -> Optional[float]:
    """One run's peak for hrmax_observed: the highest bpm with ≥ hold_s seconds
    (cumulative) at or above it, on the moving seconds (kmh > min_kmh) of the
    HR after the shared cleaning (hr_quality.clean: gaps, range, spikes,
    cadence lock — SP-265)."""
    from backend.engine import hr_quality as HQ
    c = HQ.clean(t, hr, cadence_spm, speed_kmh=kmh)
    if c is None:
        return None
    g, y = c
    ok = np.isfinite(y)
    if kmh is not None:
        v = HQ.to_grid(t, kmh, g, max_gap=30.0, positive=False)
        if v is not None:
            ok &= np.nan_to_num(v[1]) > min_kmh
    if ok.sum() < hold_s:
        return None
    lo, hi = 40, 221
    h = np.clip(np.round(y[ok]), lo, hi - 1).astype(int) - lo
    return peak_hr(np.bincount(h, minlength=hi - lo).astype(float).tolist(), lo, hold_s)


def _check(cid: str, ok: bool, text: str) -> dict:
    return {"id": cid, "ok": bool(ok), "text": text}


def road_maximal(s: dict, lthr: Optional[float], hrmax: Optional[float]) -> dict:
    """s = {km, q4_hr, peak_hr, split}. See the module docstring (rule 3)."""
    k = MAXIMAL
    cat = race_category(s.get("km"))
    checks = [_check("distance", cat is not None,
                     f"{(s.get('km') or 0):.2f} km {'≈ ' + R.CATEGORY_LABEL[cat] if cat else '不在 5K/10K/半馬/全馬 ±10 %'}")]
    if cat:
        f = k["q4_frac"][cat]
        q4 = s.get("q4_hr")
        checks.append(_check("q4_hr", bool(q4 and lthr and q4 >= f * lthr),
                             f"最後 1/4 平均心率 {q4 or 0:.0f} vs {f:.2f} × LTHR {lthr or 0:.0f} = {f * (lthr or 0):.0f}"))
        if cat in k["hrmax_cats"]:
            pk = s.get("peak_hr")
            need = (hrmax or 0) - k["hrmax_tol_bpm"]
            checks.append(_check("hrmax", bool(pk and hrmax and pk >= need),
                                 f"30 秒最高心率 {pk or 0:.0f} vs 觀測最大心率 {hrmax or 0:.0f} − {k['hrmax_tol_bpm']:.0f}"))
        sp = s.get("split")
        checks.append(_check("split", sp is not None and sp >= k["split_min"],
                             f"後半／前半速度 {sp or 0:.3f}（≥ {k['split_min']} = 平均或負分段）"))
        lp = s.get("longer_p")
        if lp:
            pa = s.get("p_avg") or 0.0
            checks.append(_check("monotone", pa >= lp,
                                 f"平均功率 {pa:.0f} W 低於之前 ≥ {k['longer_ratio']:g} 倍時長跑過的 {lp:.0f} W"
                                 if pa < lp else f"平均功率 {pa:.0f} W ≥ 更長時間跑過的 {lp:.0f} W"))
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "kind": "road_maximal", "category": cat, "checks": checks,
            "reason": "自配速全力（" + R.CATEGORY_LABEL[cat] + "）" if ok else
            "；".join(c["text"] for c in checks if not c["ok"])}


def trail_maximal(s: dict, lthr: Optional[float], aet: Optional[float], title: str = "",
                  tags=()) -> dict:
    """s = {km, moving_s, hr_avg, above_aet}. See the module docstring (rule 4)."""
    k = MAXIMAL
    word = bool(RACE_WORDS.search(title or "") or any(RACE_WORDS.search(str(t)) for t in tags or ()))
    from backend.engine import effort_calib as EC      # per athlete (generalize-athlete P8)
    mkm, ms = EC.trail_min_km(), EC.trail_min_s()
    checks = [_check("km", (s.get("km") or 0) >= mkm, f"{s.get('km') or 0:.1f} km（≥ {mkm:.0f}）"),
              _check("time", (s.get("moving_s") or 0) >= ms,
                     f"移動 {(s.get('moving_s') or 0) / 60:.0f} 分（≥ {ms / 60:.0f}）")]
    if not word:
        # 2026-10-02 (unsourced-rules.md §A2): the full-effort HR level depends on the
        # duration — x*(T) − 0.03 (trailhr.auto_max_frac) instead of 0.90 × LTHR + 2/3 above AeT
        from backend.engine.racepower import trailhr as TH
        avg = s.get("hr_avg")
        mv = s.get("moving_s")
        f = TH.auto_max_frac(mv / 3600.0 if mv else None)
        checks.append(_check("hr_avg", bool(avg and lthr and avg >= f * lthr),
                             f"平均心率 {avg or 0:.0f} vs x*(T) − 0.03 = {f:.2f} × LTHR {lthr or 0:.0f}（推估）"))
    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "kind": "trail_race_like", "category": "trail", "checks": checks, "title_word": word,
            "reason": ("標題／標籤是比賽" if word else "心率顯示持續比賽強度") if ok else
            "；".join(c["text"] for c in checks if not c["ok"])}


def match_events(events, runs: list[dict]) -> dict[int, dict]:
    """Past season-plan events → activities. events: objects with date, kind,
    distance_km, priority, name, id; runs: [{idx, date, km, trail}]. Same
    date; road events take non-trail runs and 越野賽 (kind race) trail runs;
    with a distance, the watch distance within ±event_km_tol (else the
    nearest run that day, when it is within ±25 %, 推估 for a shortened
    course). Several runs that day: the one nearest the distance (else the
    longest). 百岳 / other events are never runs."""
    out: dict[int, dict] = {}
    by_date: dict = {}
    for r in runs:
        by_date.setdefault(r["date"], []).append(r)
    for e in events:
        cat = EVENT_KINDS.get(getattr(e, "kind", None))
        if cat is None:
            continue
        cand = [r for r in by_date.get(str(e.date)[:10], []) if bool(r.get("trail")) == (cat == "trail")]
        if not cand:
            continue
        dk = getattr(e, "distance_km", None)
        if dk:
            best = min(cand, key=lambda r: abs((r.get("km") or 0) / dk - 1.0))
            err = abs((best.get("km") or 0) / dk - 1.0)
            if err > 0.25:
                continue
        else:
            best, err = max(cand, key=lambda r: r.get("km") or 0), None
        out[best["idx"]] = {"id": getattr(e, "id", None), "name": getattr(e, "name", ""), "date": str(e.date)[:10],
                            "kind": e.kind, "priority": (getattr(e, "priority", None) or "").upper(),
                            "distance_km": dk, "km_err": err,
                            "km_ok": err is None or err <= MAXIMAL["event_km_tol"]}
    return out
