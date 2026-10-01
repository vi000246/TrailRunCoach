"""
Did an interval session hit its training goal? — the per-session evaluation
behind the single-workout 間歇判讀 cards (views/workout.json) and the
progression state machine (quality_gate.dose_step).
docs/research/interval-prescription.md Part B; interval-adaptation.md §2.

Judged against the CHOSEN variant's own plan only (the user, 2026-10-01): the
equivalence between variants was settled when the variant was picked
(interval_library.fit), so a shorter equivalent is not compared with the
standard one here.

Metrics (sources in brackets):
  reps          laps of the pushed COROS steps, else the power pattern
                (interval_reps.find_reps)
  in band       rep mean ≥ 0.98 × the planned lower bound (quality_gate.IN_BAND_TOL;
                0.98 推估); hit rate = reps in band ÷ planned reps
  outcome       quality_gate.interval_outcome — the WKO5 speakers' rule (Golich,
                IT2:84-86): which rep fell out of the band, not "−5 % = stop"
  fade          last ÷ first − 1; Sdec = 100 × (1 − Σ P̄k / (n × max P̄k)) (Glaister 2008,
                power instead of time: 推估)
  TIZ           Σ s with 10-s power in [lo·CP, hi·CP·1.05], runs ≥ 30 s (doc §B3 #4; the run
                length 推估) ÷ the variant's planned TIZ
  W′ expended   WKO5's dFRC (evaluator._dfrc: linear depletion above CP, 30 % / 25 s +
                70 % / 300 s recovery — the WKO5 dfrc function, disassembled) per rep and
                in all; Skiba's differential W′bal with the running τ of Vassallo 2020
                (119 s after walk rests, 190 s after jog rests) for comparison. W′ =
                the 9/3 prior 13.1 kJ (Ruiz-Alias 2025; 推估) unless the dataset has one.
  HR @ matched power  mean HR over the last 50 % of each rep vs the previous 3–5 sessions
                of the same class with mean rep power within ±3 % (Buchheit 2014: HRex
                CV ≈ 3 %, the most reliable fitness signal)

Verdict (thresholds 推估 unless noted):
  達到訓練目標  outcome 達標 and TIZ ≥ 85 % of the variant's plan (the ±15 % of §C2)
  部分達到      outcome 邊界, or 達標 with TIZ 60–85 %
  未達到        outcome 未適應 / 目標太高, or TIZ < 60 %
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

TIZ_GOAL = 0.85                  # 推估 (§C2 ±15 %)
TIZ_PART = 0.60                  # 推估
TIZ_RUN_S = 30                   # 推估: a stretch in the zone counts from 30 s
TIZ_HI_TOL = 1.05                # doc §B3 #4: up to hi × 1.05
WPRIME_PRIOR = 13100.0           # workout_review.CP_TEST_WPRIME_PRIOR (Ruiz-Alias 2025)
TAU_WALK, TAU_JOG = 119.0, 190.0  # Vassallo 2020 (running, abstract)
MATCH_PCT = 0.03                 # Buchheit 2014: HRex CV ≈ 3 %
PEER_N, PEER_DAYS = 5, 120
VERDICT = {"met": ("達到訓練目標", "good"), "partial": ("部分達到", "warn"), "missed": ("未達到", "bad"),
           "unknown": ("無法判定", "")}


def _grid(t, x):
    from backend.engine.workout_review import _grid1
    return _grid1(t, x)


def tiz_seconds(t, power, cp: Optional[float], lo: float, hi: float, z5: bool = False) -> Optional[float]:
    """Seconds of 10-s power in [lo·CP, hi·CP·1.05] (Zone 5: no upper bound), stretches ≥ 30 s."""
    if power is None or not cp:
        return None
    grid, p = _grid(t, power)
    if grid is None or len(p) < 30:
        return None
    p10 = np.convolve(np.nan_to_num(p), np.ones(10) / 10, "same")
    on = (p10 >= lo * cp) & ((p10 <= hi * cp * TIZ_HI_TOL) | z5)
    edges = np.diff(np.concatenate([[0], on.astype(int), [0]]))
    tot = 0
    for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
        if b - a >= TIZ_RUN_S:
            # the centred 10-s mean crosses the threshold ~5 s inside each edge of a rep:
            # give the half window back on both sides (推估), else a 2′ rep reads 92 %
            tot += min(len(p), b + 4) - max(0, a - 4)
    return float(tot)


def sdec(ps: list[float]) -> Optional[float]:
    ps = [p for p in ps if p]
    if len(ps) < 2:
        return None
    return 100.0 * (1.0 - sum(ps) / (len(ps) * max(ps)))


def skiba(p: np.ndarray, cp: float, wprime: float, tau: float) -> np.ndarray:
    """Skiba's differential W′bal (J), 1-s samples."""
    out = np.empty(len(p))
    w = wprime
    k = 1.0 - math.exp(-1.0 / tau)
    for i, x in enumerate(np.nan_to_num(p)):
        if x > cp:
            w -= (x - cp)
        else:
            w += (wprime - w) * k
        out[i] = w
    return out


def verdict_of(outcome: Optional[str], tiz_ratio: Optional[float]) -> str:
    if outcome in (None, "unknown"):
        return "unknown"
    if outcome in ("unadapted", "too_high") or (tiz_ratio is not None and tiz_ratio < TIZ_PART):
        return "missed"
    if outcome == "border" or (tiz_ratio is not None and tiz_ratio < TIZ_GOAL):
        return "partial"
    return "met"


def _planned(ds, w) -> dict:
    rows = getattr(ds, "plan_rows", None)
    if rows is None:
        try:
            from backend.engine.plan_store import done_plan
            rows = done_plan()
        except Exception:                   # noqa: BLE001
            rows = {}
    return rows.get(w.idx) or {}


def _spec(ds, w, row: dict, m: dict):
    """(library variant or None, ladder-like spec tuple or None, label)."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    v = IL.resolve(row.get("variant_key"), row.get("variant_reps"), row.get("variant_adj")) if row else None
    if v is not None:
        return v, QG.variant_tuple(v), IL.title(v)
    spec = QG.spec_by_title(row.get("title")) if row else None
    if spec is not None:
        return None, spec, spec[1]
    return None, None, None


def _hr_last_half(t, hr, a: float, b: float) -> Optional[float]:
    if hr is None:
        return None
    grid, h = _grid(t, hr)
    if grid is None:
        return None
    lo, hi = int(a + (b - a) / 2), int(b)
    seg = h[max(0, lo):max(0, hi)]
    seg = seg[np.isfinite(seg) & (seg > 0)]
    return float(seg.mean()) if len(seg) else None


def evaluate(ds, w, with_peers: bool = True) -> Optional[dict]:
    """The evaluation of one activity, or None when it isn't an interval session."""
    from backend.engine import interval_reps as IR
    from backend.engine import quality_gate as QG
    from backend.engine import workout_review as WR
    m = WR.measure(ds, w)
    if not m:
        return None
    row = _planned(ds, w)
    c = WR.classify(ds, w, m)
    v, spec, label = _spec(ds, w, row, m)
    if spec is None and c["type"] != "quality":
        return None
    cp = m.get("cp")
    s = WR._samples(ds, w)
    if s is None or s.get("power") is None or not cp:
        return {"ok": False, "why": "沒有功率或 CP，無法判讀間歇", "label": label}
    found = IR.find_reps(ds, w, s, cp, spec)
    bouts = found["bouts"]
    if spec is None:
        if not bouts:
            return None
        med = float(np.median([b["power"] for b in bouts])) / cp
        band = WR.band_of(med) or ("", med - 0.03, med + 0.03)
        spec = ("detected", f"偵測到 {len(bouts)} 趟（{band[0] or '用力段'}）", len(bouts),
                float(np.median([b["duration_s"] for b in bouts])) / 60.0, 2, band[1], band[2], False, "")
        label = spec[1]
    lo, hi, n_plan = spec[5], spec[6], int(spec[2])
    floor = QG.IN_BAND_TOL * lo * cp
    t, p, hr = s["t"], s["power"], s["hr"]
    reps = []
    for k, b in enumerate(bouts[:max(n_plan, len(bouts))]):
        a, e = b["start_s"], b["start_s"] + b["duration_s"]
        h = _hr_last_half(t, hr, a, e)
        reps.append({"k": k + 1, "start_s": a, "duration_s": b["duration_s"], "power": b["power"],
                     "pct_cp": b["power"] / cp, "in_band": b["power"] >= floor, "hr_end": h,
                     "source": b.get("source") or found["source"]})
    o = QG.interval_outcome([{"power": r["power"]} for r in reps], spec, cp, m.get("aet")) if reps else \
        {"outcome": "unknown", "why": "找不到趟"}
    is5 = (v.cls == "Z5") if v is not None else lo >= 1.02
    tiz = tiz_seconds(t, p, cp, lo, hi, is5)
    plan_tiz = (sum(v.works) if v is not None else n_plan * spec[3] * 60.0)
    ratio = (tiz / plan_tiz) if tiz is not None and plan_tiz else None
    ver = verdict_of(o.get("outcome"), ratio)
    # ---- W′ / dFRC ------------------------------------------------------------------
    from backend.engine.wko5expr.evaluator import _dfrc
    grid, pg = _grid(t, p)
    wprime = float(getattr(ds, "wprime_j", None) or WPRIME_PRIOR)
    tau = TAU_WALK if (v is not None and v.rest_mode == "walk") else TAU_JOG
    dfrc = _dfrc(np.nan_to_num(pg), np.ones(len(pg)), wprime, cp) * 1000.0
    sk = skiba(pg, cp, wprime, tau)
    for r in reps:
        a, e = int(r["start_s"]), int(r["start_s"] + r["duration_s"])
        seg = dfrc[max(0, a):max(1, e)]
        r["dfrc_min_pct"] = float(np.nanmin(seg) / wprime) if len(seg) else None
        r["wprime_used_j"] = float(np.nansum(np.clip(np.nan_to_num(pg[a:e]) - cp, 0, None)))
    i_min = int(np.nanargmin(dfrc)) if len(dfrc) else 0
    ps = [r["power"] for r in reps]
    fade = (ps[-1] / ps[0] - 1.0) if len(ps) >= 2 and ps[0] else None
    reasons = []
    hit = sum(1 for r in reps if r["in_band"])
    reasons.append(f"{hit}/{n_plan} 趟在目標帶（≥ {floor:.0f} W＝目標下限 {lo * 100:.0f}% CP × 0.98）")
    if ratio is not None:
        reasons.append(f"目標區時間 {tiz / 60:.1f} 分，是這份課表計畫 {plan_tiz / 60:.0f} 分的 {ratio * 100:.0f}%"
                       f"（≥ 85% 算達到，推估）")
    if o.get("why"):
        reasons.append(f"逐趟判定：{QG.OUTCOME_LABEL.get(o.get('outcome'), o.get('outcome'))}（{o['why']}）")
    if fade is not None:
        reasons.append(f"掉速 {fade * 100:+.0f}%（最後一趟比第一趟），Sdec {sdec(ps):.1f}%（Glaister 2008；只顯示）")
    out = {"ok": True, "label": label, "variant_key": v.key if v is not None else None,
           "rung_key": row.get("rung_key"), "equiv": row.get("equiv"), "planned": bool(row),
           "cp": cp, "lo": lo, "hi": hi, "floor": floor, "n_plan": n_plan, "works": list(v.works) if v else None,
           "reps": reps, "rep_source": found["source"], "outcome": o.get("outcome"), "outcome_why": o.get("why"),
           "hit": hit, "hit_rate": hit / n_plan if n_plan else None, "fade": fade, "sdec": sdec(ps),
           "tiz_s": tiz, "tiz_plan_s": plan_tiz, "tiz_ratio": ratio, "z5": is5,
           "verdict": ver, "verdict_label": VERDICT[ver][0], "level": VERDICT[ver][1], "reasons": reasons,
           "wprime_j": wprime, "wprime_src": "W′ 先驗 13.1 kJ（Ruiz-Alias 2025；推估）"
           if not getattr(ds, "wprime_j", None) else "W′（資料集）",
           "tau": tau, "dfrc_min_pct": float(dfrc[i_min] / wprime) if len(dfrc) else None,
           "dfrc_min_t": float(i_min),
           # the whole session's work above CP (reps, strides, a hard climb home)
           "wprime_used_j": float(np.nansum(np.clip(np.nan_to_num(pg) - cp, 0, None))),
           "series": {"t": grid.tolist() if grid is not None else [], "power": pg.tolist(),
                      "dfrc_pct": (dfrc / wprime).tolist(), "skiba_pct": (sk / wprime).tolist()}}
    out["pdc5"] = best_5min(ds, w)
    if with_peers:
        out["peers"] = peers(ds, w, out)
    return out


def best_5min(ds, w, days: int = 90) -> Optional[float]:
    """The 90-day best 5-min power before (and including) this run — the WKO5 chart's
    「5 Min PDC」 reference. Memoised in ds.memo."""
    from backend.engine import workout_review as WR
    memo = getattr(ds, "memo", None)
    key = ("interval_pdc5", w.idx)
    if isinstance(memo, dict) and key in memo:
        return memo[key]
    best = None
    d0 = math.floor(w.day)
    for x in ds.workouts:
        if x.sport != "run" or not (d0 - days < math.floor(x.day) <= d0):
            continue
        s = WR._samples(ds, x)
        if s is None or s.get("power") is None:
            continue
        _, pg = _grid(s["t"], s["power"])
        if pg is None or len(pg) < 300:
            continue
        c = np.cumsum(np.concatenate([[0.0], np.nan_to_num(pg)]))
        v = float((c[300:] - c[:-300]).max() / 300.0)
        best = v if best is None else max(best, v)
    if isinstance(memo, dict):
        memo[key] = best
    return best


def peers(ds, w, cur: dict) -> list[dict]:
    """Earlier sessions of the same class with mean rep power within ±3 % of this one:
    [{"date", "pct_cp", "hr_end", "current"}] (last PEER_N, then this one)."""
    from backend.engine import workout_review as WR
    if not cur.get("reps"):
        return []
    mine = float(np.mean([r["pct_cp"] for r in cur["reps"]]))
    hr_now = [r["hr_end"] for r in cur["reps"] if r.get("hr_end")]
    out = []
    d0 = math.floor(w.day)
    for x in sorted(ds.workouts, key=lambda q: q.day):
        if x.idx == w.idx or x.sport != "run" or not (d0 - PEER_DAYS <= math.floor(x.day) < d0):
            continue
        try:
            e = evaluate(ds, x, with_peers=False)
        except Exception:                   # noqa: BLE001
            e = None
        if not e or not e.get("ok") or not e.get("reps") or e.get("z5") != cur.get("z5"):
            continue
        pct = float(np.mean([r["pct_cp"] for r in e["reps"]]))
        hs = [r["hr_end"] for r in e["reps"] if r.get("hr_end")]
        if abs(pct - mine) > MATCH_PCT or not hs:
            continue
        out.append({"date": WR._wdate(x).isoformat(), "pct_cp": pct, "hr_end": float(np.mean(hs)), "current": False})
    out = out[-PEER_N:]
    if hr_now:
        out.append({"date": WR._wdate(w).isoformat(), "pct_cp": mine, "hr_end": float(np.mean(hr_now)), "current": True})
    WR._flush(ds)
    return out


def cached(ds, w) -> Optional[dict]:
    """evaluate() memoised per dataset / activity (the six 間歇判讀 cards share it)."""
    memo = getattr(ds, "memo", None)
    key = ("interval_eval", w.idx)
    if isinstance(memo, dict) and key in memo:
        return memo[key]
    r = evaluate(ds, w)
    if isinstance(memo, dict):
        memo[key] = r
    return r
