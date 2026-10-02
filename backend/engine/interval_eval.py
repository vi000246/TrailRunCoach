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

The 間歇 dashboard is shown for every run (`card`, the user 2026-10-02):
  * a CP / AeT test (workout_review.classify) is judged against its protocol
    (`evaluate_test`): the protocol's bouts are the planned reps; a CP-test
    bout's intent is all-out, so the reference is the CP model's
    P = CP + W′/t (Monod & Scherrer 1965; with the W′ prior: 推估), and the
    verdict is the pacing — was each bout even (2nd half within ±5 % of the
    1st, last minute ≤ 1.08 × the bout, cp_protocols.LAST_MIN_RATIO; the
    5 % 推估). The AeT test's block is fixed power: ±3 % (推估);
  * an unplanned, unclassified run: 「這次不是間歇課」 with 「當作間歇判讀」,
    which adds the activity tag FLAG_TAG (activity_tags; remembered per
    activity, removable on the 活動編輯 page); a flagged run is evaluated on
    its detected bouts;
  * no power / no CP: one short card saying why;
  * the W′ battery (`battery`, dFRC + Skiba) for every run with power.
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
           "unknown": ("無法判定", ""),
           # a test (evaluate_test): the pacing of the protocol's bouts, not a target band
           "even": ("測試配速分配：每段都平均", "good"), "uneven": ("測試配速分配：有一段不平均", "warn")}
FLAG_TAG = "當作間歇"            # the activity tag the 「當作間歇判讀」 button adds
EVEN_TOL = 0.05                  # 推估: a CP-test bout's 2nd-half power within ±5 % of the 1st = even
EVEN_TOL_AET = 0.03              # 推估: the AeT test's block is fixed power
WPRIME_SRC_PRIOR = "W′ 先驗 13.1 kJ（Ruiz-Alias 2025；推估）"


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


def battery(ds, w, s: Optional[dict], cp: Optional[float], tau: float = TAU_JOG) -> Optional[dict]:
    """W′ over the whole activity: WKO5's dFRC and Skiba's W′bal, as a share of W′
    (the 「功率電池」 card) — every run with power, not only intervals. None
    without power or CP. `_dfrc` / `_pg` (numpy, 1-s grid) are for the callers'
    per-bout numbers; `series` is the card's."""
    if s is None or s.get("power") is None or not cp:
        return None
    from backend.engine.wko5expr.evaluator import _dfrc
    grid, pg = _grid(s["t"], s["power"])
    if grid is None or len(pg) < 2:
        return None
    wprime = float(getattr(ds, "wprime_j", None) or WPRIME_PRIOR)
    dfrc = _dfrc(np.nan_to_num(pg), np.ones(len(pg)), wprime, cp) * 1000.0
    sk = skiba(pg, cp, wprime, tau)
    i_min = int(np.nanargmin(dfrc)) if np.isfinite(dfrc).any() else 0
    return {"wprime_j": wprime, "wprime_src": WPRIME_SRC_PRIOR if not getattr(ds, "wprime_j", None) else "W′（資料集）",
            "tau": tau, "cp": cp, "dfrc_min_pct": float(dfrc[i_min] / wprime) if len(dfrc) else None,
            "dfrc_min_t": float(i_min),
            # the whole session's work above CP (reps, strides, a hard climb home)
            "wprime_used_j": float(np.nansum(np.clip(np.nan_to_num(pg) - cp, 0, None))),
            "series": {"t": grid.tolist(), "power": pg.tolist(),
                       "dfrc_pct": (dfrc / wprime).tolist(), "skiba_pct": (sk / wprime).tolist()},
            "_dfrc": dfrc, "_pg": pg}


def _public(b: dict) -> dict:
    return {k: v for k, v in b.items() if not k.startswith("_")}


def _seg_w(b: dict, a: int, e: int) -> tuple[Optional[float], float]:
    """(dFRC minimum as a share of W′, J above CP) over grid seconds [a, e)."""
    seg = b["_dfrc"][max(0, a):max(1, e)]
    used = float(np.nansum(np.clip(np.nan_to_num(b["_pg"][max(0, a):max(0, e)]) - b["cp"], 0, None)))
    return (float(np.nanmin(seg) / b["wprime_j"]) if len(seg) else None), used


def flagged(w) -> bool:
    """The user pressed 「當作間歇判讀」 on this activity (activity tag FLAG_TAG)."""
    try:
        from backend.engine import activity_tags as AT
        return FLAG_TAG.lower() in {t.lower() for t in AT.tags_of(AT.user_of(w))}
    except Exception:                       # noqa: BLE001 — no tag store: not flagged
        return False


def evaluate(ds, w, with_peers: bool = True, as_interval: bool = False) -> Optional[dict]:
    """The evaluation of one activity, or None when it isn't an interval session.
    `as_interval` (the 「當作間歇判讀」 mark): evaluate an unplanned run on its
    detected bouts even when it isn't classified quality."""
    from backend.engine import interval_reps as IR
    from backend.engine import quality_gate as QG
    from backend.engine import workout_review as WR
    m = WR.measure(ds, w)
    if not m:
        return None
    row = _planned(ds, w)
    c = WR.classify(ds, w, m)
    v, spec, label = _spec(ds, w, row, m)
    if spec is None and c["type"] != "quality" and not as_interval:
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
    from backend.engine import interval_library as IL
    is5 = (v.cls == "Z5") if v is not None else (lo + (hi if hi is not None else lo)) / 2 >= IL.CLASS_RANGE["Z5"][0]
    tiz = tiz_seconds(t, p, cp, lo, hi, is5)
    plan_tiz = (sum(v.works) if v is not None else n_plan * spec[3] * 60.0)
    ratio = (tiz / plan_tiz) if tiz is not None and plan_tiz else None
    ver = verdict_of(o.get("outcome"), ratio)
    # ---- W′ / dFRC ------------------------------------------------------------------
    tau = TAU_WALK if (v is not None and v.rest_mode == "walk") else TAU_JOG
    bat = battery(ds, w, s, cp, tau)
    for r in reps:
        a, e = int(r["start_s"]), int(r["start_s"] + r["duration_s"])
        r["dfrc_min_pct"], r["wprime_used_j"] = _seg_w(bat, a, e) if bat else (None, 0.0)
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
           "kind": "detected" if spec[0] == "detected" else "plan", "flagged": bool(as_interval),
           **(_public(bat) if bat else {"wprime_j": WPRIME_PRIOR, "wprime_src": WPRIME_SRC_PRIOR, "tau": tau,
                                        "dfrc_min_pct": None, "dfrc_min_t": 0.0, "wprime_used_j": 0.0,
                                        "series": {"t": [], "power": [], "dfrc_pct": [], "skiba_pct": []}})}
    out["cp"] = cp
    out["pdc5"] = best_5min(ds, w)
    if with_peers:
        out["peers"] = peers(ds, w, out)
    return out


def _pacing_word(split: float, last: Optional[float], tol: float) -> str:
    from backend.engine.cp_protocols import LAST_MIN_RATIO
    if split < -tol:
        return "前快後掉（起步太快）"
    if split > tol or (last is not None and last + 1.0 > LAST_MIN_RATIO):
        return "後段較快（前面有保留，這段只是下限）"
    return "平均"


def _test_bouts(ds, w, m: dict, c: dict, n_grid: int):
    """(bouts [{start_s, duration_s, hr_peak}], label, n_plan, intent "max" / "steady", extra lines)
    of a CP / AeT test by its protocol, or None."""
    from backend.engine import workout_review as WR
    if c["type"] == "test_cp":
        from backend.engine import cp_protocols as CPP
        ev = WR.cp_eval(ds, w, m, c)
        if not ev or not ev.get("bouts"):
            return None
        p = ev["protocol"]
        bouts = sorted(ev["bouts"], key=lambda b: b["start_s"])
        # in the order run (the athlete's 9/30 test was 3′ first)
        label = "CP 測試 " + " + ".join(f"{b['duration_s'] / 60:.0f} 分" for b in bouts) + "（全力）"
        return bouts, label, len(CPP.TABLE[p]["bouts"]) or len(bouts), "max", \
            [f"這次 CP {ev['cp']:.0f} W（{ev['method_label']}，品質 {ev['quality']}）：結果看「本次重點」的 CP 測試卡"]
    if c["type"] == "test_aet":
        from backend.engine import aet_test as AET
        r = AET.analyze_workout(ds, w, m)
        if r is None:
            return None
        proto = r.get("protocol") or "ua60"
        warm = float(r.get("warm_s") or AET.WARM_STD_S)
        main = AET.PROTOCOLS[proto]["main"] * 60.0
        a, e = int(warm), int(min(n_grid, warm + main))
        if e - a < 600:
            return None
        return [{"start_s": float(a), "duration_s": float(e - a), "hr_peak": None}], AET.PROTOCOLS[proto]["title"], 1, \
            "steady", AET.lines(r, m.get("aet"))[:1]
    return None


def cp_before(ds, w) -> Optional[float]:
    """The CP in effect before the test day (the latest earlier activity's): a test
    applied the same day (the 9/30 CP 204 W came from that test's own 12′ bout)
    would make the all-out reference circular."""
    if not hasattr(ds, "cp"):
        return None
    d0 = math.floor(w.day)
    prev = [x for x in ds.workouts if math.floor(x.day) < d0]
    if not prev:
        return None
    try:
        v = ds.cp(max(prev, key=lambda x: x.day))
        return float(v) if v else None
    except Exception:                       # noqa: BLE001
        return None


def evaluate_test(ds, w, m: dict, c: dict, s: dict, cp: float) -> Optional[dict]:
    """A CP / AeT test judged against its protocol: the protocol's bouts are the
    planned reps; the verdict is the pacing (each bout even?), not a target band.
    CP test: reference = all-out power for the bout's length by the CP model
    P = CP + W′/t (Monod & Scherrer 1965; the CP in effect before the test and
    the W′ prior: 推估)."""
    from backend.engine import workout_review as WR
    bat = battery(ds, w, s, cp)
    if bat is None:
        return None
    pg = bat["_pg"]
    got = _test_bouts(ds, w, m, c, len(pg))
    if got is None:
        return None
    bouts, label, n_plan, intent, extra = got
    tol = EVEN_TOL if intent == "max" else EVEN_TOL_AET
    wp = bat["wprime_j"]
    cp_ref = (cp_before(ds, w) or cp) if intent == "max" else cp
    reps, reasons = [], []
    for k, b in enumerate(bouts):
        a, e = int(round(b["start_s"])), int(round(b["start_s"] + b["duration_s"]))
        seg = np.nan_to_num(pg[max(0, a):max(0, e)])
        if len(seg) < 60:
            continue
        mean = float(seg.mean())
        h = len(seg) // 2
        p1, p2 = float(seg[:h].mean()), float(seg[h:].mean())
        split = (p2 / p1 - 1.0) if p1 > 0 else 0.0
        last = (float(seg[-60:].mean()) / mean - 1.0) if len(seg) >= 120 and mean > 0 else None
        word = _pacing_word(split, last, tol)
        exp = (cp_ref + wp / len(seg)) if intent == "max" else None
        dmin, used = _seg_w(bat, a, e)
        name = f"{len(seg) / 60:.0f} 分段"
        reps.append({"k": k + 1, "start_s": float(a), "duration_s": float(len(seg)), "power": mean,
                     "pct_cp": mean / cp, "in_band": word == "平均", "even": word == "平均", "split": split,
                     "last_ratio": last, "pacing": word, "expected": exp,
                     "pct_expected": (mean / exp) if exp else None, "name": name,
                     "hr_end": _hr_last_half(s["t"], s["hr"], a, e), "hr_peak": b.get("hr_peak"),
                     "dfrc_min_pct": dmin, "wprime_used_j": used, "source": "test"})
        ref = (f"；預期全力 ≈ {exp:.0f} W（測試前 CP {cp_ref:.0f} + W′/{len(seg):.0f} 秒，推估），"
               f"做到 {mean / exp * 100:.0f}%" if exp else "")
        reasons.append(f"{name} {mean:.0f} W（{mean / cp * 100:.0f}% CP{ref}）：前半 {p1:.0f} → 後半 {p2:.0f} W"
                       f"（{split * 100:+.0f}%）" + (f"，最後 1 分 {last * 100:+.0f}%" if last is not None else "")
                       + f"——{word}")
    if not reps:
        return None
    n_even = sum(1 for r in reps if r["even"])
    ver = "even" if n_even == len(reps) else "uneven"
    lab = VERDICT[ver][0] if ver == "even" or len(reps) == 1 else f"測試配速分配：{len(reps) - n_even} 段不平均"
    if ver == "uneven" and len(reps) == 1:
        lab = "測試配速分配：不平均"
    reasons.append(f"平均＝後半和前半差 ±{tol * 100:.0f}% 內、最後 1 分 ≤ 該段 × 1.08（推估）")
    reasons += extra
    return {"ok": True, "kind": "test", "test": c["type"], "intent": intent, "label": label, "planned": True,
            "cp_ref": cp_ref,
            "variant_key": None, "rung_key": None, "equiv": None, "cp": cp, "lo": None, "hi": None, "floor": None,
            "n_plan": n_plan, "works": [r["duration_s"] for r in reps], "reps": reps, "rep_source": "test",
            "outcome": None, "outcome_why": None, "hit": n_even, "hit_rate": n_even / len(reps), "fade": None,
            "sdec": None, "tiz_s": None, "tiz_plan_s": None, "tiz_ratio": None, "z5": False,
            "verdict": ver, "verdict_label": lab, "level": VERDICT[ver][1], "reasons": reasons, "flagged": False,
            **_public(bat), "pdc5": best_5min(ds, w), "peers": []}


def _no_power_why(ds, w, s) -> str:
    try:
        if s is not None and s.get("power") is not None and hasattr(ds, "power_ok") and not ds.power_ok(w):
            return "只有手錶推估功率（未採用，設定可改）：不判讀間歇、不算 W′"
    except Exception:                       # noqa: BLE001
        pass
    return "沒有功率（沒戴 Stryd）：不判讀間歇、不算 W′"


def card(ds, w) -> dict:
    """What the 間歇 dashboard shows for any activity (never hidden):
    {"ok": True, "kind": plan / detected / test, …evaluate()} or
    {"ok": False, "state": "none" | "no_power" | "offer", "why", …}; the
    "offer" state (not an interval session) carries the W′ battery and the
    detected bout count for the 「當作間歇判讀」 button."""
    from backend.engine import interval_reps as IR
    from backend.engine import workout_review as WR
    m = WR.measure(ds, w)
    if not m:
        return {"ok": False, "state": "none", "why": "這筆活動沒有逐秒資料"}
    if w.sport != "run":
        return {"ok": False, "state": "none", "why": "不是跑步：不判讀間歇"}
    s = WR._samples(ds, w)
    cp = m.get("cp")
    power_ok = s is not None and s.get("power") is not None
    try:
        power_ok = power_ok and (not hasattr(ds, "power_ok") or bool(ds.power_ok(w)))
    except Exception:                       # noqa: BLE001
        pass
    if not power_ok:
        return {"ok": False, "state": "no_power", "why": _no_power_why(ds, w, s)}
    if not cp:
        return {"ok": False, "state": "no_power", "why": "還沒有 CP：不判讀間歇、不算 W′（先做一次 CP 測試）"}
    c = WR.classify(ds, w, m)
    if c["type"] in ("test_cp", "test_aet"):
        e = evaluate_test(ds, w, m, c, s, cp)
        if e:
            return e
    fl = flagged(w)
    e = evaluate(ds, w, as_interval=fl)
    if e and e.get("ok"):
        return e
    if e:
        return {**e, "state": "no_power"}
    bat = battery(ds, w, s, cp)
    n = len(IR.find_reps(ds, w, s, cp, None)["bouts"])
    why = ("標了「當作間歇」，但找不到用力段（≥ 95% CP 的短趟或 3 區以上 ≥ 2.5 分）" if fl else
           f"這次算{c['type_label']}，但找不到一趟一趟的用力段（例如一路爬坡），沒有趟可判讀" if c["type"] == "quality" else
           f"這次不是間歇課（{c['type_label']}：有閾值強度，算硬課、不算間歇次數）" if c["type"] == "hard_long" else
           f"這次不是間歇課（{c['type_label']}，課表也沒有對應的間歇）")
    return {"ok": False, "state": "offer", "why": why, "flagged": fl, "n_bouts": n, "type_label": c["type_label"],
            **(_public(bat) if bat else {})}


def card_cached(ds, w) -> dict:
    """card() memoised per dataset / activity / the 「當作間歇」 mark."""
    memo = getattr(ds, "memo", None)
    key = ("interval_card", w.idx, flagged(w))
    if isinstance(memo, dict) and key in memo:
        return memo[key]
    r = card(ds, w)
    if isinstance(memo, dict):
        memo[key] = r
    return r


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
