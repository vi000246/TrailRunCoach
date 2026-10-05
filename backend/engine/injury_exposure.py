"""
哪些訓練跟受傷有關 — the within-person case-crossover of the injury log
(docs/plans/injury-tracking.plan.md §3; owner decisions 2026-10-02: mild
injuries are included, severity is a filter).

One athlete, a handful of injuries a year: the app can only say "before your
injuries, these were higher than usual", never "this causes injuries".
Lövdal 2021 (74 runners, 575 injuries) found the best single load metric at
AUC 0.57 (docs/research/validation-lovdal.md §0).

Daily exposure (rest day = 0) from the Dataset's per-activity metrics; the
per-second extras (steep downhill time, impact, cadence, the HR intensity
split) come from the activity samples, disk-memoised per file
(`ds.cached_series`) and read within a time budget (the rest = unknown, never
0). Window metrics end the day BEFORE the onset (the onset day is excluded).

Design (§3.3): each analysed injury = a case window (21 days before the
onset); controls = the same athlete's 21-day windows every 7 days, excluding
  * windows ending within ±28 days of any onset;
  * windows ending inside an injury (onset … resolved) or ≤ 42 days after it;
  * windows with < 3 runs or < 2 h of running (推估: not "in training");
  * windows holding a day with a 痛 / 中斷 mark attached to an event.
Analysed injuries: kind overuse, not a draft, not a recurrence (same area ≤
42 days after the last one resolved), the chosen severities (default all).
Each metric: the case value's percentile among the controls (no distribution
assumption; fine for small n). "Higher than usual" = ≥ P75 (推估).

Honesty tiers (§3.4, injuries.honesty_tier): 1–4 injuries — the percentile
points only, no p / OR / CI keys at all; 5–14 — plus the median percentile
and "k of n above your median"; ≥ 15 — plus a within-person conditional
logistic OR per SD (each case vs its controls), 95 % CI, BH q, 「探索性」.
"""
from __future__ import annotations

import datetime as dt
import math
import time
from typing import Callable, Optional

import numpy as np

from backend.engine import injuries as INJ

WINDOW = 21
CONTROL_STEP = 7          # Lövdal sensitivity analysis: one day a week
NEAR_ONSET = 28
AFTER_RESOLVED = 42       # validation-lovdal.md §3.6: the return period inflates load changes
MIN_RUNS, MIN_RUN_H = 3, 2.0          # 推估
COVERAGE_MIN = 0.60       # 推估: a metric known in < 60 % of the windows is 「資料不夠」
HIGH_PCT = 75.0           # 推估: 「高於平常」
FEW_CONTROLS = 20
SEASON_DAYS = 91          # 「只比同一季」: ± 3 months by day of year
LONG_MIN = 90.0           # 徐國峰's 90-minute check length (推估 as a "long run" cut)
STEP_BASE_MIN = 0.60      # validation-lovdal.md §5: no step when the base week < 60 % of the 4-week mean
STEEP_DOWN = -0.10
AET_MARGIN = 3.0          # workout_review.AET_MARGIN: the ± 3 bpm band around AeT is not counted (wrist HR)
DETAIL_KEY = "injury_exposure_v1"
DETAIL_BUDGET_S = 25.0
ALERT_PCT = 85.0          # §4.1: the current window ≥ P85 of the same controls (推估)
ALERT_MEDIAN = 75.0
ALERT_SHARE = 2.0 / 3.0
NO_ALERT = ("acwr", "monotony", "step0", "ctl_ramp", "low_share")   # Lövdal / quality_gate already speak
CAUTION = "同一個人、次數很少，只能看出巧合還是規律的方向，不能證明因果。推估。"
WRIST = "用手腕光學心率算，誤差比胸帶大"

# key, label, unit, decimals, help
METRICS = (
    ("run_h", "跑步時間", "h", 1, "傷前 21 天的跑步時間合計（路跑、越野、跑步機）。"),
    ("run_km", "跑步距離", "km", 0, "傷前 21 天的跑步距離合計。"),
    ("desc", "下降", "m", 0, "傷前 21 天跑步＋登山的下降公尺。膝蓋痛的主要候選。"),
    ("climb", "爬升", "m", 0, "傷前 21 天跑步＋登山的爬升公尺。"),
    ("steep_h", "陡下坡時間", "h", 1, "坡度 < −10% 的移動時間（需要逐秒資料；沒讀到的活動不算 0，算未知）。"),
    ("impact", "衝擊量", "", 0, "Stryd ILR × 時間的合計，只有 Stryd 的跑步有；其他當未知。"),
    ("long", "長跑次數", "次", 0, "單次 ≥ 90 分的跑步次數（徐國峰 90 分鐘檢查長度；當長跑的切點是推估）。"),
    ("b2b", "連續長天", "天", 0, "連兩天以上長時間（和 B2B 同一個判定）的天數。"),
    ("pack", "負重天", "天", 0, "有記錄背負（kg > 0）的活動天數。"),
    ("max", "全力天", "天", 0, "你標成「全力」或手錶 RPE ≥ 9 的天數。"),
    ("sore", "痠的次數", "次", 0, "活動上點「痠」的次數：痛之前是不是先痠了好幾次。"),
    ("trail", "越野佔比", "%", 0, "越野跑時間 ÷ 跑步時間。"),
    ("hike_h", "登山／健行時間", "h", 1, "不算進跑步時間，但下降有算進「下降」。"),
    ("step0", "週增量", "%", 0, "傷前 7 天對再前 7 天的跑步時間。前一週太小（< 4 週平均 60%）不算。"),
    ("step1", "週增量（延遲一週）", "%", 0, "再前一週的增量：Lövdal 2021 的效果在增量後第 8 天。"),
    ("inc2", "2 週平均增幅", "%", 0, "(W0 − W2) ÷ (W1 + W2)，Lövdal 2021 驗證過最好的負荷變化指標之一（AUC 0.546）。"),
    ("ctl_ramp", "CTL 增加", "", 1, "傷前 7 天的 CTL 變化。"),
    ("monotony", "單調度", "", 2, "Foster 單調度（7 天 TSS 平均 ÷ 標準差），前 14 天的最大值。"),
    ("low_share", "低強度佔比", "%", 0, "心率 < AeT − 3 的時間 ÷（低於 AeT − 3 ＋高於 AeT + 3）。" + WRIST + "。"),
    ("cad_rel", "步頻差", "%", 1, "傷前 21 天的平均步頻，相對你自己 26 週中位的差（手錶加速度計）。"),
    ("acwr", "ACWR", "", 2, "7 天 ÷ 前 28 天（uncoupled）。只參考，不預測受傷（Lövdal 2021）。"),
)
META = {k: {"label": l, "unit": u, "dec": d, "help": h} for k, l, u, d, h in METRICS}
RELEVANT = {           # 推估: which metrics to list first for an area (§3.3)
    "knee": ("desc", "steep_h", "impact", "cad_rel"),
    "achilles": ("climb", "trail", "low_share", "cad_rel"),
    "shin_calf": ("climb", "trail", "low_share", "cad_rel"),
    "foot": ("run_km", "step0", "step1", "inc2"),
    "hip": ("long", "pack", "b2b"),
    "low_back": ("long", "pack", "b2b"),
    "ankle": ("trail", "desc"),
    "thigh": ("max", "step0"),
}


# ---------------------------------------------------------------------------
# per-activity samples (cached) and the daily series
# ---------------------------------------------------------------------------

def detail_of(ds, w) -> Optional[dict]:
    """Steep-downhill seconds, cadence, impact (ILR·s, Stryd) and the HR split
    of one run, from its samples. None without samples."""
    from backend.engine import workout_review as WR
    s = WR._samples(ds, w)
    if s is None:
        return None
    t = np.asarray(s["t"], dtype=float)
    n = len(t)
    mov = WR.moving_mask(t, s.get("speed"))
    d = np.where(mov, WR._dt(t), 0.0)
    out = {"mov_s": float(d.sum()), "steep_s": None, "cad": None, "impact": None, "low_s": None, "high_s": None}
    try:
        g = WR._rgrade(ds, w, s)
    except Exception:                       # noqa: BLE001
        g = None
    if g is not None and np.isfinite(np.asarray(g, dtype=float)).any():
        gg = WR._arr(g, n)
        out["steep_s"] = float(d[np.isfinite(gg) & (gg < STEEP_DOWN)].sum())
    if s.get("cadence") is not None:
        c = WR._arr(s["cadence"], n)
        m = np.isfinite(c) & (c > 0) & (d > 0)
        if d[m].sum() > 60:
            out["cad"] = float((c[m] * d[m]).sum() / d[m].sum())
    if s.get("ilr") is not None:
        i = np.nan_to_num(WR._arr(s["ilr"], n))
        out["impact"] = float((i * d).sum()) if (i > 0).any() else None
    try:
        aet = ds.aethr(w)
    except Exception:                       # noqa: BLE001
        aet = None
    if aet and s.get("hr") is not None:
        h = WR._arr(s["hr"], n)
        ok = np.isfinite(h) & (h > 40)
        if d[ok].sum() > 60:
            out["low_s"] = float(d[ok & (h < aet - AET_MARGIN)].sum())
            out["high_s"] = float(d[ok & (h > aet + AET_MARGIN)].sum())
    return out


def _cat(w) -> str:
    from backend.engine.overview import category
    return category(w)


def _mov(w) -> float:
    from backend.engine.overview import moving_s
    return moving_s(w)


def _wdate(w) -> dt.date:
    from backend.engine.wko5expr.dataset import day_to_date
    return day_to_date(w.day)


def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class Daily:
    """Per-day exposure arrays from d0 to `end` (inclusive). NaN = unknown."""

    FIELDS = ("run_s", "run_km", "tss", "climb", "desc", "trail_s", "hike_s", "long", "b2b", "pack", "max", "sore",
              "pain2", "runs", "d_steep", "d_cad_w", "d_cad_s", "d_impact", "d_impact_s", "d_low", "d_high",
              "d_known_s", "d_hr_s")

    def __init__(self, d0: dt.date, end: dt.date):
        self.d0, self.end = d0, end
        self.n = (end - d0).days + 1
        for f in self.FIELDS:
            setattr(self, f, np.zeros(self.n))
        self.cad_runs: list[tuple[int, float]] = []          # (day index, cadence) of each run with cadence
        self.pending = 0

    def i(self, d: dt.date) -> int:
        return (d - self.d0).days


def build_daily(ds, today: dt.date, tags: Optional[list] = None, recorded: Optional[list] = None,
                pack_meta: Optional[dict] = None, detail: Optional[Callable] = None,
                budget_s: float = DETAIL_BUDGET_S) -> Optional[Daily]:
    """The daily exposure of the whole dataset (None without workouts).
    `tags` / `recorded` / `pack_meta` default to the stores; `detail(ds, w)`
    to the cached per-second reader (time-budgeted, newest first)."""
    from backend.engine import activity_tags as AT
    from backend.engine import b2b as B2B
    ws = list(getattr(ds, "workouts", []) or [])
    if not ws:
        return None
    tags = AT.load() if tags is None else tags
    recorded = AT.load_recorded() if recorded is None else recorded
    if pack_meta is None:
        try:
            from backend.engine.racepower import athlete as A
            pack_meta = A.hike_meta()
        except Exception:                   # noqa: BLE001
            pack_meta = {}
    d0 = min(_wdate(w) for w in ws)
    D = Daily(d0, max(today, max(_wdate(w) for w in ws)))
    endur = []
    for w in ws:
        day = _wdate(w)
        i = D.i(day)
        cat = _cat(w)
        m = w.metrics
        mv = _mov(w)
        D.tss[i] += _f(m.get("tss")) or 0.0
        if cat in ("road", "trail", "hike"):
            D.climb[i] += _f(m.get("climbing")) or 0.0
            D.desc[i] += _f(m.get("descending")) or 0.0
        if cat in ("road", "trail", "hike", "bike"):
            endur.append((day, mv / 60.0, w.idx))
        if cat == "hike":
            D.hike_s[i] += mv
        if w.sport == "run":
            D.run_s[i] += mv
            D.run_km[i] += _f(m.get("distance")) or 0.0
            D.runs[i] += 1
            if cat == "trail":
                D.trail_s[i] += mv
            if mv / 60.0 >= LONG_MIN:
                D.long[i] = 1
        u = AT.find(tags, w.entry.start, getattr(w.entry, "file", None)) if tags else None
        rec = AT.recorded_of(recorded, w.entry.start, getattr(w.entry, "file", None)) if recorded else None
        if AT.user_effort(u) == "max" or ((rec or {}).get("rpe") or 0) >= 9:
            D.max[i] = 1
        if u and u.get("pain") == 1:
            D.sore[i] += 1
        if u and (u.get("pain") or 0) >= 2 and u.get("injury_id"):
            D.pain2[i] = 1
        if ((pack_meta or {}).get(getattr(w.entry, "file", None)) or {}).get("pack_kg"):
            D.pack[i] = 1
    for blk in B2B.detect(endur):
        a = dt.date.fromisoformat(blk["start"])
        for k in range(blk["days"]):
            D.b2b[D.i(a + dt.timedelta(days=k))] = 1
    # per-second extras, newest first, within the budget
    runs = sorted((w for w in ws if w.sport == "run"), key=lambda w: w.entry.start, reverse=True)
    reader = detail or _cached_detail
    t0 = time.monotonic()
    for w in runs:
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            D.pending += 1
            continue
        try:
            x = reader(ds, w)
        except Exception:                   # noqa: BLE001
            x = None
        if not x:
            continue
        i = D.i(_wdate(w))
        mv = _mov(w) or x.get("mov_s") or 0.0
        if x.get("steep_s") is not None:
            D.d_steep[i] += x["steep_s"]
            D.d_known_s[i] += mv
        if x.get("cad"):
            D.d_cad_w[i] += x["cad"] * mv
            D.d_cad_s[i] += mv
            D.cad_runs.append((i, x["cad"]))
        if x.get("impact") is not None:
            D.d_impact[i] += x["impact"]
            D.d_impact_s[i] += mv
        if x.get("low_s") is not None:
            D.d_low[i] += x["low_s"]
            D.d_high[i] += x["high_s"] or 0.0
            D.d_hr_s[i] += mv
    if hasattr(ds, "flush_series"):
        try:
            ds.flush_series()
        except Exception:                   # noqa: BLE001
            pass
    return D


def _cached_detail(ds, w):
    if hasattr(ds, "cached_series"):
        return ds.cached_series(DETAIL_KEY, w, lambda: detail_of(ds, w))
    return detail_of(ds, w)


# ---------------------------------------------------------------------------
# window metrics
# ---------------------------------------------------------------------------

def _pmc(tss: np.ndarray, const: float) -> np.ndarray:
    out = np.zeros(len(tss))
    k = 1.0 / max(1.0, const)
    v = 0.0
    for i, x in enumerate(tss):
        v = v + (x - v) * k
        out[i] = v
    return out


def window(D: Daily, e: int, ctl: np.ndarray, length: int = WINDOW, cad_median: Optional[float] = None) -> dict:
    """The metrics of the window [e − length, e) (day indices; e excluded).
    None = unknown (not enough per-second data, a too-small base week)."""
    a = max(0, e - length)
    sl = slice(a, e)
    run_s = float(D.run_s[sl].sum())
    out = {"run_h": run_s / 3600.0, "run_km": float(D.run_km[sl].sum()), "desc": float(D.desc[sl].sum()),
           "climb": float(D.climb[sl].sum()), "long": float(D.long[sl].sum()), "b2b": float(D.b2b[sl].sum()),
           "pack": float(D.pack[sl].sum()), "max": float(D.max[sl].sum()), "sore": float(D.sore[sl].sum()),
           "hike_h": float(D.hike_s[sl].sum()) / 3600.0,
           "trail": 100.0 * float(D.trail_s[sl].sum()) / run_s if run_s > 0 else None}

    def cover(known) -> bool:
        return run_s <= 0 or float(known[sl].sum()) >= COVERAGE_MIN * run_s

    out["steep_h"] = float(D.d_steep[sl].sum()) / 3600.0 if cover(D.d_known_s) else None
    out["impact"] = float(D.d_impact[sl].sum()) / 1000.0 if run_s > 0 and cover(D.d_impact_s) else None
    hr_lo, hr_hi = float(D.d_low[sl].sum()), float(D.d_high[sl].sum())
    out["low_share"] = 100.0 * hr_lo / (hr_lo + hr_hi) if cover(D.d_hr_s) and hr_lo + hr_hi > 0 else None
    cs = float(D.d_cad_s[sl].sum())
    out["cad_rel"] = (100.0 * (float(D.d_cad_w[sl].sum()) / cs / cad_median - 1.0)
                      if cad_median and cs > 0 and cover(D.d_cad_s) else None)

    def wk(k: int) -> float:                # week k back: [e − 7(k+1), e − 7k)
        lo, hi = e - 7 * (k + 1), e - 7 * k
        return float(D.run_s[max(0, lo):max(0, hi)].sum()) if hi > 0 else 0.0
    w0, w1, w2, w3, w4, w5 = (wk(k) for k in range(6))
    m4_1 = (w1 + w2 + w3 + w4) / 4.0
    m4_2 = (w2 + w3 + w4 + w5) / 4.0
    enough = e - 7 * 5 >= 0
    out["step0"] = 100.0 * (w0 / w1 - 1.0) if enough and w1 > 0 and w1 >= STEP_BASE_MIN * m4_1 else None
    out["step1"] = 100.0 * (w1 / w2 - 1.0) if enough and w2 > 0 and w2 >= STEP_BASE_MIN * m4_2 else None
    out["inc2"] = 100.0 * (w0 - w2) / (w1 + w2) if e - 21 >= 0 and w1 + w2 > 0 else None
    out["ctl_ramp"] = float(ctl[e - 1] - ctl[e - 8]) if e - 8 >= 0 else None

    def mono(lo, hi):
        x = D.tss[max(0, lo):max(0, hi)]
        if len(x) < 7:
            return None
        sd = float(np.std(x))
        return float(np.mean(x)) / sd if sd > 0 else None
    ms = [v for v in (mono(e - 7, e), mono(e - 14, e - 7)) if v is not None]
    out["monotony"] = max(ms) if ms else None
    acute = float(D.tss[max(0, e - 7):e].sum()) / 7.0
    chronic = float(D.tss[max(0, e - 35):max(0, e - 7)].sum()) / 28.0 if e - 35 >= 0 else 0.0
    out["acwr"] = acute / chronic if chronic > 0 else None
    out["_runs"] = int(D.runs[sl].sum())
    out["_pain2"] = bool(D.pain2[sl].any())
    return out


def _cad_median(D: Daily, e: int) -> Optional[float]:
    v = [c for i, c in D.cad_runs if e - 182 <= i < e]
    return float(np.median(v)) if len(v) >= 5 else None


# ---------------------------------------------------------------------------
# events → cases; controls
# ---------------------------------------------------------------------------

def classify_events(events: list[dict], severities=None, include_recurrence: bool = False) -> tuple[list, list]:
    """(analysed, excluded with the reason)."""
    sev = set(severities or INJ.SEVERITIES)
    inc, exc = [], []
    for e in events:
        why = None
        if e.get("status") == "draft":
            why = "補完細節才會進分析"
        elif e.get("kind") == "acute":
            why = "急性傷不進負荷分析"
        elif e.get("severity") not in sev:
            why = "不在選的嚴重度"
        elif e.get("recurrence_of") and not include_recurrence:
            why = "復發（同部位、上次好了 42 天內）"
        (exc if why else inc).append({**e, "why": why} if why else e)
    return inc, exc


def control_ends(D: Daily, events: list[dict], today: dt.date) -> list[int]:
    """Window end indices (exclusive) of the control windows (module docstring)."""
    blocked = np.zeros(D.n + 1, dtype=bool)
    for ev in events:
        o = INJ._d(ev.get("onset_date"))
        if o is None:
            continue
        oi = D.i(o)
        blocked[max(0, oi - NEAR_ONSET):min(D.n + 1, oi + NEAR_ONSET + 1)] = True
        ri = D.i(INJ.end_of(ev, today)) + AFTER_RESOLVED
        blocked[max(0, oi):min(D.n + 1, ri + 1)] = True
    out = []
    last = D.i(today) + 1
    for e in range(WINDOW + 14, last + 1, CONTROL_STEP):
        if blocked[min(e, D.n)]:
            continue
        sl = slice(e - WINDOW, e)
        if D.runs[sl].sum() < MIN_RUNS or D.run_s[sl].sum() < MIN_RUN_H * 3600.0:
            continue
        if D.pain2[sl].any():
            continue
        out.append(e)
    return out


def percentile(x: float, ref: list[float]) -> Optional[float]:
    if x is None or not ref:
        return None
    lo = sum(1 for v in ref if v < x)
    eq = sum(1 for v in ref if v == x)
    return 100.0 * (lo + 0.5 * eq) / len(ref)


def _doy_dist(a: dt.date, b: dt.date) -> int:
    d = abs(a.timetuple().tm_yday - b.timetuple().tm_yday)
    return min(d, 365 - d)


def clogit(cases: list[tuple[float, list[float]]]) -> Optional[dict]:
    """Conditional logistic regression with one covariate: each stratum = one
    case value and its control values. Newton–Raphson; Wald CI. Returns
    {beta, se} or None (no variation / no convergence)."""
    cases = [(x, cs) for x, cs in cases if x is not None and cs]
    if len(cases) < 2:
        return None
    b = 0.0
    for _ in range(50):
        g, h = 0.0, 0.0
        for x, cs in cases:
            vals = np.array([x] + list(cs), dtype=float)
            z = b * vals
            z -= z.max()
            p = np.exp(z)
            p /= p.sum()
            mu = float((p * vals).sum())
            g += x - mu
            h += float((p * (vals - mu) ** 2).sum())
        if h <= 1e-12:
            return None
        step = g / h
        b += max(-2.0, min(2.0, step))
        if abs(step) < 1e-8:
            break
    else:
        return None
    return {"beta": b, "se": 1.0 / math.sqrt(h)}


def _bh(ps: dict) -> dict:
    items = sorted((p, k) for k, p in ps.items() if p is not None)
    m = len(items)
    q, prev = {}, 1.0
    for rank in range(m, 0, -1):
        p, k = items[rank - 1]
        prev = min(prev, p * m / rank)
        q[k] = prev
    return q


def _norm_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def analysis(ds, events: list[dict], today: dt.date, *, severities=None, season: bool = False, area: Optional[str] = None,
             D: Optional[Daily] = None, include_recurrence: bool = False, **build_kw) -> dict:
    """§3.3–§3.4. The JSON the injury page draws (the tier decides what is in it)."""
    D = D or build_daily(ds, today, **build_kw)
    events = [e for e in events if not INJ.is_illness(e)]      # 生病 (SP-117) is not an injury case
    inc, exc = classify_events(events, severities, include_recurrence)
    if area:
        inc = [e for e in inc if e.get("area") == area]
    n = len(inc)
    tier = INJ.honesty_tier(n)
    base = {"tier": tier, "n": n, "caution": CAUTION, "high_pct": HIGH_PCT, "excluded": [
        {"id": e["id"], "label": INJ.full_label(e.get("area"), e.get("side")), "onset_date": e.get("onset_date"),
         "why": e["why"]} for e in exc],
        "drafts": sum(1 for e in events if e.get("status") == "draft"), "season": bool(season), "area": area,
        "severities": sorted(set(severities or INJ.SEVERITIES), key=lambda s: INJ.SEV_RANK[s]),
        "pending": getattr(D, "pending", 0) if D else 0, "acute": acute_cards(ds, events, D)}
    if D is None or tier == 0:
        return {**base, "metrics": [], "controls": 0}
    ctl = _pmc(D.tss, float(getattr(ds.athlete, "ctlconstant", 42) or 42))
    ends = control_ends(D, events, today)
    cmeds: dict = {}

    def win(e):
        if e not in cmeds:
            cmeds[e] = window(D, e, ctl, cad_median=_cad_median(D, e))
        return cmeds[e]
    ctrl = [(e, win(e)) for e in ends]
    cases = []
    for ev in inc:
        o = INJ._d(ev["onset_date"])
        e = D.i(o)
        if e - WINDOW < 0 or e > D.n:
            continue
        cases.append((ev, o, win(e)))
    rel = RELEVANT.get(area or "", ())
    rows = []
    pvals = {}
    for k, *_ in METRICS:
        cvals_all = [w[k] for _, w in ctrl if w.get(k) is not None]
        known = len(cvals_all) + sum(1 for _, _, w in cases if w.get(k) is not None)
        tot = len(ctrl) + len(cases)
        enough = tot > 0 and known / tot >= COVERAGE_MIN
        pts = []
        strata = []
        for ev, o, w in cases:
            if season:
                ref = [cw[k] for ce, cw in ctrl if cw.get(k) is not None
                       and _doy_dist(D.d0 + dt.timedelta(days=ce), o) <= SEASON_DAYS]
            else:
                ref = cvals_all
            x = w.get(k)
            pct = percentile(x, ref) if x is not None else None
            pts.append({"id": ev["id"], "date": ev["onset_date"], "area": ev.get("area"),
                        "area_label": INJ.area_label(ev.get("area")), "label": INJ.full_label(ev.get("area"), ev.get("side")),
                        "severity": ev.get("severity"), "value": x, "pct": None if pct is None else round(pct, 1),
                        "few": len(ref) < FEW_CONTROLS})
            strata.append((x, ref))
        q = np.percentile(cvals_all, [10, 25, 50, 75, 90]).tolist() if len(cvals_all) >= 3 else None
        row = {"key": k, **META[k], "relevant": k in rel, "enough": enough, "coverage": round(known / tot, 2) if tot else 0,
               "alertable": k not in NO_ALERT,
               "control": {"n": len(cvals_all), **({"p10": q[0], "p25": q[1], "p50": q[2], "p75": q[3], "p90": q[4]} if q else {})},
               "cases": pts}
        known_pct = [p["pct"] for p in pts if p["pct"] is not None]
        if tier >= 2 and known_pct:
            row["median_pct"] = float(np.median(known_pct))
            row["k_above"] = sum(1 for p in known_pct if p > 50.0)
            row["k_known"] = len(known_pct)
        if tier >= 3 and enough and len(cvals_all) >= 5:
            sd = float(np.std(cvals_all))
            if sd > 0:
                st = [(None if x is None else x / sd, [r / sd for r in ref]) for x, ref in strata]
                fit = clogit(st)
                if fit:
                    b, se = fit["beta"], fit["se"]
                    row["or"] = round(math.exp(b), 2)
                    row["ci"] = [round(math.exp(b - 1.96 * se), 2), round(math.exp(b + 1.96 * se), 2)]
                    row["p"] = _norm_p(b / se)
                    row["per"] = "每 1 個標準差"
                    pvals[k] = row["p"]
        rows.append(row)
    if pvals:
        qs = _bh(pvals)
        for r in rows:
            if r["key"] in qs:
                r["q"] = round(qs[r["key"]], 3)
                r["exploratory"] = True
    for r in rows:
        r["texts"] = [case_text(r, p) for p in r["cases"]] if tier >= 1 else []

    def order(r):
        m = r.get("median_pct")
        if m is None:
            ps = [p["pct"] for p in r["cases"] if p["pct"] is not None]
            m = float(np.median(ps)) if ps else -1.0
        return (not r["enough"], not r["relevant"], -m)
    rows.sort(key=order)
    return {**base, "metrics": rows, "controls": len(ctrl),
            "few_controls": len(ctrl) < FEW_CONTROLS,
            "cases": [{"id": ev["id"], "date": ev["onset_date"], "label": INJ.full_label(ev.get("area"), ev.get("side")),
                       "area": ev.get("area"), "severity": ev.get("severity"),
                       "top": top_pcts(rows, ev["id"])} for ev, _, _ in cases]}


def fmt(k: str, v) -> str:
    if v is None:
        return "未知"
    m = META[k]
    s = f"{v:.{m['dec']}f}"
    if m["unit"] == "%":
        return s + "%"
    return f"{s} {m['unit']}".strip()


def case_text(row: dict, p: dict) -> str:
    """「你受傷前，下降在你自己的第 85 百分位（比平常高）」 — never 造成 / 導致 / 風險."""
    if p.get("pct") is None:
        return f"{p['date']} {p['label']}：{row['label']}未知"
    hi = "比平常高" if p["pct"] >= HIGH_PCT else "比平常低" if p["pct"] <= 100 - HIGH_PCT else "跟平常差不多"
    return f"{p['date']} {p['label']}受傷前，{row['label']}在你自己的第 {p['pct']:.0f} 百分位（{hi}）"


def top_pcts(rows: list[dict], eid: int, n: int = 3) -> list[dict]:
    got = []
    for r in rows:
        if not r["enough"] or r["key"] == "acwr":
            continue
        p = next((c for c in r["cases"] if c["id"] == eid), None)
        if p and p["pct"] is not None:
            got.append({"key": r["key"], "label": r["label"], "pct": p["pct"], "value": fmt(r["key"], p["value"])})
    return sorted(got, key=lambda x: -x["pct"])[:n]


def acute_cards(ds, events: list[dict], D: Optional[Daily]) -> list[dict]:
    """Acute injuries (跌倒、扭到): where it happened, not the load (§3.3)."""
    out = []
    by_key = {}
    from backend.engine import activity_tags as AT
    for w in getattr(ds, "workouts", []) or []:
        by_key[AT.key_of(w.entry.start)] = w
    for e in events:
        if e.get("kind") != "acute":
            continue
        w = by_key.get(e.get("onset_key") or "")
        card = {"id": e["id"], "date": e.get("onset_date"), "label": INJ.full_label(e.get("area"), e.get("side")),
                "activity": None}
        if w is not None:
            m = w.metrics
            card["activity"] = {"trail": _cat(w) in ("trail", "hike"), "hours": round(_mov(w) / 3600.0, 1),
                                "desc": _f(m.get("descending")), "climb": _f(m.get("climbing"))}
        out.append(card)
    return out


# ---------------------------------------------------------------------------
# the timeline (§3.5 chart 1) and the single-event card
# ---------------------------------------------------------------------------

def timeline(ds, events: list[dict], marks: list[dict], today: dt.date, begin: Optional[dt.date] = None,
             end: Optional[dt.date] = None, D: Optional[Daily] = None, **build_kw) -> dict:
    end = end or today
    begin = begin or (end - dt.timedelta(days=548))
    D = D or build_daily(ds, today, budget_s=0.0, **build_kw)
    weeks = []
    if D is not None:
        ctl = _pmc(D.tss, float(getattr(ds.athlete, "ctlconstant", 42) or 42))
        mon = begin - dt.timedelta(days=begin.weekday())
        while mon <= end:
            a, b = D.i(mon), D.i(mon) + 7
            lo, hi = max(0, a), max(0, min(D.n, b))
            sl = slice(lo, hi)
            w = window(D, min(b, D.n), ctl, length=7) if hi > 0 else {}
            weeks.append({"week": mon.isoformat(), "run_h": round(float(D.run_s[sl].sum()) / 3600.0, 2),
                          "desc": round(float(D.desc[sl].sum())), "climb": round(float(D.climb[sl].sum())),
                          "trail_h": round(float(D.trail_s[sl].sum()) / 3600.0, 2),
                          "ctl": round(float(ctl[hi - 1]), 1) if hi > 0 else None,
                          "acwr": None if not w or w.get("acwr") is None else round(w["acwr"], 2)})
            mon += dt.timedelta(days=7)
    bands = []
    for e in events:
        o = INJ._d(e.get("onset_date"))
        if o is None:
            continue
        stop = INJ.end_of(e, today)
        if stop < begin or o > end:
            continue
        bands.append({"id": e["id"], "start": o.isoformat(), "end": stop.isoformat(), "open": INJ.is_open(e),
                      "area": e.get("area"), "label": INJ.event_label(e),
                      "category": "illness" if INJ.is_illness(e) else "injury",
                      "severity": e.get("severity"), "severity_label": INJ.SEVERITIES.get(e.get("severity"), ""),
                      "status": e.get("status"), "kind": e.get("kind")})
    ticks = [m for m in marks if m["pain"] >= 1 and begin.isoformat() <= m["date"] <= end.isoformat()]
    return {"begin": begin.isoformat(), "end": end.isoformat(), "weeks": weeks, "bands": bands,
            "ticks": [{"date": m["date"], "pain": m["pain"], "area": m.get("area"),
                       "area_label": INJ.area_label(m.get("area")) if m.get("area") else ""} for m in ticks]}


def event_days(ds, ev: dict, today: dt.date, D: Optional[Daily] = None, **build_kw) -> list[dict]:
    """The 21 days before an onset (+ the onset day): run minutes and descent per day."""
    D = D or build_daily(ds, today, budget_s=0.0, **build_kw)
    o = INJ._d(ev.get("onset_date"))
    if D is None or o is None:
        return []
    out = []
    for k in range(WINDOW, -1, -1):
        d = o - dt.timedelta(days=k)
        i = D.i(d)
        ok = 0 <= i < D.n
        out.append({"date": d.isoformat(), "run_min": round(float(D.run_s[i]) / 60.0) if ok else 0,
                    "desc": round(float(D.desc[i])) if ok else 0, "onset": k == 0})
    return out


# ---------------------------------------------------------------------------
# 「跟受傷前很像」 (§4.1; off by default, only with ≥ 5 analysed injuries)
# ---------------------------------------------------------------------------

def current_window(D: Daily, today: dt.date, ctl: np.ndarray, planned: Optional[list] = None) -> dict:
    """Today's 21-day window: the last 14 days plus the next 7 planned days
    (run time, km, climb ≈ descent from the plan; 推估), else the last 21."""
    e = D.i(today) + 1
    if not planned:
        return window(D, e, ctl, cad_median=_cad_median(D, e))
    past = window(D, e, ctl, length=WINDOW - 7, cad_median=_cad_median(D, e))
    add_s = sum((p.get("minutes") or 0) * 60.0 for p in planned)
    add_km = sum(p.get("distance_km") or 0 for p in planned)
    add_up = sum(p.get("climb_m") or 0 for p in planned)
    out = dict(window(D, e, ctl, cad_median=_cad_median(D, e)))
    out["run_h"] = past["run_h"] + add_s / 3600.0
    out["run_km"] = past["run_km"] + add_km
    out["climb"] = past["climb"] + add_up
    out["desc"] = past["desc"] + add_up
    return out


def pattern_alerts(an: dict, now: dict, ctrl_values: dict, today: dt.date) -> list[dict]:
    """The metrics where the injuries share a pattern (median pct ≥ 75 and ≥
    2/3 of them above the median) and today's window is ≥ P85 of the same
    controls. Information only; never ACWR / monotony / what quality_gate checks."""
    if an.get("n", 0) < INJ.PATTERN_MIN_N:
        return []
    out = []
    mon = (today - dt.timedelta(days=today.weekday())).isoformat()
    for r in an.get("metrics") or []:
        k = r["key"]
        if not r.get("enough") or k in NO_ALERT or r.get("median_pct") is None:
            continue
        if r["median_pct"] < ALERT_MEDIAN or r.get("k_above", 0) < ALERT_SHARE * max(1, r.get("k_known", 0)):
            continue
        pct = percentile(now.get(k), ctrl_values.get(k) or [])
        if pct is None or pct < ALERT_PCT:
            continue
        areas = sorted({c["area_label"] for c in r["cases"]})
        out.append({"id": f"injury_pattern:{k}:{mon}", "type": "injury_pattern", "pick": None, "metric": k,
                    "title": f"{r['label']}跟你受傷前很像",
                    "reason": f"你前 {r['k_known']} 次受傷（{'、'.join(areas)}），有 {r['k_above']} 次之前的{r['label']}"
                              f"偏高（推估）；這 3 週的{r['label']}在你的第 {pct:.0f} 百分位。",
                    "help": "只是提醒，不會改課表，也不會擋課。比的是你自己的紀錄，次數很少，不能證明因果（推估）。"})
    return out


def alerts(ds, events: list[dict], today: dt.date, planned: Optional[list] = None, **build_kw) -> list[dict]:
    D = build_daily(ds, today, **build_kw)
    if D is None:
        return []
    an = analysis(ds, events, today, D=D)
    if an["n"] < INJ.PATTERN_MIN_N:
        return []
    ctl = _pmc(D.tss, float(getattr(ds.athlete, "ctlconstant", 42) or 42))
    ends = control_ends(D, events, today)
    cv: dict = {}
    for e in ends:
        w = window(D, e, ctl, cad_median=_cad_median(D, e))
        for k, v in w.items():
            if v is not None and not k.startswith("_"):
                cv.setdefault(k, []).append(v)
    return pattern_alerts(an, current_window(D, today, ctl, planned), cv, today)
