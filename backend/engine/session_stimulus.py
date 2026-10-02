"""
Did a run stimulate VO2max for long enough (Zone 5), or only threshold (Zone 3)?
Design and sources: docs/research/vo2max-session-detection.md (owner's decisions 2026-10-02).

The stimulus measure is T@VO2max — time at ≥ 90 % VO2max (Buchheit & Laursen 2013,
Sports Med 43:313, §1 / §3) — estimated without gas analysis:

  power path (where Stryd is trusted: road; trail at −3…8 % grade, van Rassel 2026;
  never on hikes — walking power is not comparable, Uphill Athlete)
    a VO2 bout = 30-s power ≥ 1.03 × CP (gaps < 5 s bridged) whose mean is
      ≥ 1.06 CP for ≥ 2 min   (your note 如何進入VO2max.md: 106–120 % 2–5 min; 徐國峰 ≥ 2 min)
      1.03–1.06 CP for ≥ 5 min (same note 100–105 % 5–8 min; Palladino MAP from 103 %;
                                the 3 % margin over CP is 推估)
    each bout counts minus the VO2 on-kinetics (Buchheit §3.1.1.2: τ 20–35 s, VO2max within
    1:20–2:20): 60 s (the day's first bout 90 s) on ≥ 1.06 bouts, 180 s on 1.03–1.06 (推估,
    Hill 2002: the lower the power, the slower)
  HR path (only where power is not trusted: no power, trail > 8 %)
    30-s wrist HR ≥ 0.93 × HRpeak in runs ≥ 60 s, while moving and not downhill (HR inertia,
    Buchheit §2.3), cadence lock excluded (Bent 2020 'signal crossover'); no on-kinetics cut
    (HR lags VO2). Counted ÷ 1.6 (Fleckenstein 2025: time > 90 % HRmax ≈ 1.7 × time
    > 90 % VO2max, chest strap). 0.93, 1.6, 60 s: 推估.
  equivalent T@VO2max = T_p + T_h / 1.6; Zone 5 at ≥ 4 min (推估: Buchheit "at least several
  minutes"; the ladder's first Zone 5 rung 5×2′ gives 4.5 min). Goal ≥ 10 min (Buchheit §3.1.2.4).

  Zone 3 time: power-trusted samples with 30-s power ≥ 0.88 CP in runs ≥ 150 s
  (interval_reps.Z3_FLOOR / Z3_MIN_S), plus HR-only samples ≥ 0.95 LTHR minus the first 3 min
  of each run (HR lag) when ≥ 150 s remain. Zone 3 session at ≥ 10 min (overview.HARD_SESSION_S).

  Cadence lock: HR within 3 bpm of the cadence (spm) in a 60-s window AND following its
  changes (correlation ≥ 0.8 with ≥ 1.5 spm of cadence variation) — HR merely close to the
  cadence is common on easy runs (backtest: 144 / 188 runs) and is not a lock (推估).

The per-run part (`measure`) does not depend on HRpeak: the HR path is stored as seconds per
threshold bpm, so `verdict` can read it at the HRpeak in effect (`hr_peak`: the plan's HRmax,
else the 3rd-highest per-run 60-s HR peak in 365 days — the top ones are optical errors).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

import numpy as np

P_LO, P_HI = 1.03, 1.06
P_HI_MIN_S, P_LO_MIN_S = 120, 300
LAG_HI_S, LAG_FIRST_S, LAG_LO_S = 60, 30, 180
BRIDGE_S = 5
HR_VO2 = 0.93
HR_FACTOR = 1.6
HR_MIN_S = 60
Z5_MIN_S = 240
Z5_GOAL_S = 600
Z3_P = 0.88
Z3_HR = 0.95
Z3_MIN_S = 150
Z3_HR_SKIP_S = 180
Z3_NEED_S = 600
GRADE_MAX, GRADE_DOWN = 0.08, -0.03
MOVING_MS = 0.5
LOCK_TOL, LOCK_WIN, LOCK_CORR, LOCK_CAD_SD = 3.0, 60, 0.8, 1.5
HR_BPM_LO, HR_BPM_HI = 130, 215          # the HR path's stored threshold range
HRPEAK_RANK, HRPEAK_DAYS = 3, 365

SRC = {
    "t_vo2": "Buchheit & Laursen 2013（Sports Med 43:313）：每堂 ≥ 90% VO2max「at least several minutes」，目標約 10 分",
    "z5_min": "推估：等效 T@VO2max ≥ 4 分（「幾分鐘」；5×2 分做完是 4.5 分）",
    "bouts": "你的筆記「如何進入VO2max」：106–120% CP 2–5 分、100–105% 5–8 分（作者未標）；徐國峰：每趟 ≥ 2 分；"
             "103% 是 Palladino MAP 下限，比 CP 高 3% 的緩衝是推估",
    "lag": "Buchheit §3.1.1.2：攝氧量 1:20–2:20 才到最大；每段扣 60／90／180 秒是推估",
    "hr": "手腕心率只當替代：≥ 93% 最高心率（推估；Swain 1994 換算 90% VO2max ≈ 95% HRmax、Daniels T 上緣 92%），"
          "時間 ÷ 1.6（Fleckenstein 2025：胸帶心率 > 90% 的時間是攝氧量的 1.7 倍）",
    "z3": "Zone 3：功率 ≥ 88% CP、每段 ≥ 2.5 分；心率 ≥ 95% LTHR、扣每段前 3 分（心率延遲）；累積 ≥ 10 分",
    "trail": "越野只在 −3…8% 坡用功率（van Rassel 2026），陡坡用心率，下坡都不算；百岳不自動判 5 區",
    "hrpeak": "最高心率：計畫裡的最大心率；沒填時用 365 天內每趟 60 秒最高心率的第 3 高（推估：最高的幾個常是光學錯誤）",
}


def _grid(t: np.ndarray, x, g: np.ndarray) -> Optional[np.ndarray]:
    """x on the 1-s grid `g` (linear), NaN where the source has a gap > 30 s."""
    if x is None:
        return None
    x = np.asarray(x, dtype=float)
    if len(x) != len(t):
        return None
    ok = np.isfinite(t) & np.isfinite(x)
    if ok.sum() < 2:
        return None
    tt, xx = t[ok], x[ok]
    y = np.interp(g, tt, xx, left=np.nan, right=np.nan)
    j = np.clip(np.searchsorted(tt, g), 1, len(tt) - 1)
    y[(tt[j] - tt[j - 1]) > 30] = np.nan
    return y


def _roll(x: Optional[np.ndarray], n: int) -> Optional[np.ndarray]:
    """Centred n-s mean ignoring NaN (NaN where < half the window is finite)."""
    if x is None:
        return None
    fin = np.isfinite(x)
    k = np.ones(n) / n
    s = np.convolve(np.where(fin, x, 0.0), k, "same")
    c = np.convolve(fin.astype(float), k, "same")
    return np.where(c > 0.5, s / np.maximum(c, 1e-9), np.nan)


def runs_of(mask: np.ndarray, bridge: int = 0) -> list[tuple[int, int]]:
    """[a, b) index runs where `mask` is True; gaps shorter than `bridge` are joined."""
    m = np.asarray(mask, dtype=bool)
    e = np.diff(np.concatenate([[0], m.astype(int), [0]]))
    out: list[list[int]] = []
    for a, b in zip(np.where(e == 1)[0], np.where(e == -1)[0]):
        if out and bridge and a - out[-1][1] < bridge:
            out[-1][1] = int(b)
        else:
            out.append([int(a), int(b)])
    return [(a, b) for a, b in out]


def cadence_lock(hr: Optional[np.ndarray], cad_spm: Optional[np.ndarray]) -> np.ndarray:
    """Samples where the optical HR follows the cadence (module doc)."""
    if hr is None or cad_spm is None:
        return np.zeros(0 if hr is None else len(hr), dtype=bool)
    n = len(hr)
    out = np.zeros(n, dtype=bool)
    step = LOCK_WIN // 2
    for a in range(0, max(1, n - LOCK_WIN + 1), step):
        h, c = hr[a:a + LOCK_WIN], cad_spm[a:a + LOCK_WIN]
        ok = np.isfinite(h) & np.isfinite(c)
        if ok.sum() < LOCK_WIN * 0.8:
            continue
        h, c = h[ok], c[ok]
        if abs(float(np.mean(h - c))) > LOCK_TOL or float(np.std(c)) < LOCK_CAD_SD or float(np.std(h)) < 1e-6:
            continue
        if float(np.corrcoef(h, c)[0, 1]) >= LOCK_CORR:
            out[a:a + LOCK_WIN] = True
    return out


def measure(t, hr=None, power=None, speed=None, cadence_spm=None, grade=None,
            cp: Optional[float] = None, lthr: Optional[float] = None, category: str = "road") -> Optional[dict]:
    """The per-run part (no HRpeak): VO2 bouts and T_p, the HR path's seconds per bpm,
    Zone 3 time, the run's own 60-s HR peak. `grade` is a fraction per sample (rgrade)."""
    t = np.asarray(t, dtype=float)
    if len(t) < 2 or not np.isfinite(t).any():
        return None
    g = np.arange(np.nanmin(t), np.nanmax(t) + 1.0)
    n = len(g)
    h1, p1 = _grid(t, hr, g), _grid(t, power, g)
    v1, c1 = _grid(t, speed, g), _grid(t, cadence_spm, g)
    gr = _roll(_grid(t, grade, g), 30)
    h30, p30 = _roll(h1, 30), _roll(p1, 30)
    lock = cadence_lock(h1, c1) if h1 is not None else np.zeros(n, dtype=bool)
    down = np.isfinite(gr) & (gr < GRADE_DOWN) if gr is not None else np.zeros(n, dtype=bool)
    moving = np.nan_to_num(v1) > MOVING_MS if v1 is not None else np.ones(n, dtype=bool)

    # where power is trusted
    if p30 is not None and cp and category in ("road", "trail"):
        pvalid = np.isfinite(p30)
        if category == "trail":
            pvalid &= (np.isfinite(gr) & (gr <= GRADE_MAX) & (gr >= GRADE_DOWN)) if gr is not None else False
    else:
        pvalid = np.zeros(n, dtype=bool)

    # ---- power path
    bouts, t_p, first = [], 0.0, True
    best = None
    if pvalid.any():
        p10 = _roll(p1, 10)
        raw_on = np.nan_to_num(p1) >= P_LO * cp
        for a, b in runs_of(pvalid & (p10 >= P_LO * cp), BRIDGE_S):
            # the smoothing trims each edge: extend over the raw seconds still ≥ 1.03 CP
            while a > 0 and raw_on[a - 1] and pvalid[a - 1]:
                a -= 1
            while b < n and raw_on[b] and pvalid[b]:
                b += 1
            d = b - a
            pm = float(np.nanmean(p1[a:b])) / cp
            best = pm if best is None else max(best, pm)
            if pm >= P_HI and d >= P_HI_MIN_S - BRIDGE_S:      # 5 s slack: a late lap press
                lag = LAG_HI_S + (LAG_FIRST_S if first else 0)
            elif pm >= P_LO and d >= P_LO_MIN_S - BRIDGE_S:
                lag = LAG_LO_S
            else:
                continue
            first = False
            c = max(0, d - lag)
            t_p += c
            bouts.append({"start_s": float(a), "duration_s": float(d), "pct_cp": round(pm, 3), "counted_s": float(c)})
    top = None
    if pvalid.any():
        q = np.where(pvalid, p30, np.nan)
        if np.isfinite(q).any():
            top = float(np.nanmax(q)) / cp

    # ---- HR path (power not trusted) and the run's own HR peak
    hr_secs, hr_peak60 = [], None
    if h30 is not None:
        ok = np.isfinite(h30) & ~lock
        h60 = _roll(np.where(ok, h1, np.nan), 60)
        if np.isfinite(h60).any():
            hr_peak60 = float(np.nanmax(h60))
        elig = ok & ~pvalid & moving & ~down
        if elig.any():
            for bpm in range(HR_BPM_LO, HR_BPM_HI + 1):
                s = sum(b - a for a, b in runs_of(elig & (h30 >= bpm)) if b - a >= HR_MIN_S)
                hr_secs.append(float(s))
                if s == 0:
                    break                      # higher thresholds are 0 too
    # ---- Zone 3
    z3_p = 0.0
    if pvalid.any():
        z3_p = float(sum(b - a for a, b in runs_of(pvalid & (p30 >= Z3_P * cp), BRIDGE_S) if b - a >= Z3_MIN_S))
    z3_h = 0.0
    if h30 is not None and lthr:
        ok = np.isfinite(h30) & ~lock & ~pvalid & ~down
        for a, b in runs_of(ok & (h30 >= Z3_HR * lthr), BRIDGE_S):
            if b - a - Z3_HR_SKIP_S >= Z3_MIN_S:
                z3_h += b - a - Z3_HR_SKIP_S
    return {"t_vo2_power_s": float(t_p), "vo2_bouts": bouts[:20], "top_pct_cp": top,
            "best_bout_pct_cp": best, "hr_path": {"lo": HR_BPM_LO, "secs": hr_secs}, "hr_peak60": hr_peak60,
            "z3_s": z3_p + z3_h, "z3_power_s": z3_p, "z3_hr_s": z3_h,
            "power_valid_share": float(pvalid.mean()) if n else 0.0, "lock_s": float(lock.sum())}


def hr_path_s(stim: Optional[dict], thr_bpm: Optional[float]) -> float:
    """HR-path seconds at ≥ `thr_bpm` (stored per whole bpm, rounded up)."""
    if not stim or not thr_bpm:
        return 0.0
    hp = stim.get("hr_path") or {}
    secs, lo = hp.get("secs") or [], int(hp.get("lo") or HR_BPM_LO)
    i = int(math.ceil(thr_bpm)) - lo
    if i < 0:
        i = 0
    return float(secs[i]) if i < len(secs) else 0.0


def hr_peak(peaks: Sequence[float], rank: int = HRPEAK_RANK) -> Optional[float]:
    """The rank-th highest per-run 60-s peak (fewer runs: the lowest of them)."""
    xs = sorted((float(p) for p in peaks if p is not None and math.isfinite(float(p))), reverse=True)
    if not xs:
        return None
    return xs[min(rank, len(xs)) - 1]


def verdict(stim: Optional[dict], category: str, hrpeak: Optional[float] = None, easy_hr: bool = False) -> dict:
    """{"stimulus": "z5" | "z3" | None, "t_vo2_eq_s", "t_vo2_power_s", "t_vo2_hr_s", "hr_thr", "z3_s", "why"}.
    Zone 5 only on road / trail (百岳 never auto-Z5; the 「當作間歇判讀」 mark still works).
    ≥ 4 min of power evidence is Zone 5 even when the average HR stayed ≤ AeT + 3
    (long warm-up / rests); otherwise an easy average HR means no stimulus."""
    stim = stim or {}
    t_p = float(stim.get("t_vo2_power_s") or 0.0)
    thr = HR_VO2 * hrpeak if hrpeak else None
    t_h = hr_path_s(stim, thr) if category in ("road", "trail") else 0.0
    eq = t_p + t_h / HR_FACTOR
    z3 = float(stim.get("z3_s") or 0.0)
    out = {"t_vo2_eq_s": eq, "t_vo2_power_s": t_p, "t_vo2_hr_s": t_h, "hr_thr": thr, "hrpeak": hrpeak, "z3_s": z3,
           "goal_s": Z5_GOAL_S, "need_s": Z5_MIN_S}
    if category in ("road", "trail") and (t_p >= Z5_MIN_S or (not easy_hr and eq >= Z5_MIN_S)):
        return {**out, "stimulus": "z5", "why": f"等效 T@VO2max {eq / 60:.1f} 分 ≥ {Z5_MIN_S // 60} 分"}
    if not easy_hr and z3 >= Z3_NEED_S:
        return {**out, "stimulus": "z3", "why": f"Zone 3 {z3 / 60:.0f} 分 ≥ {Z3_NEED_S // 60} 分；"
                                                 f"等效 T@VO2max {eq / 60:.1f} 分 < {Z5_MIN_S // 60} 分"}
    return {**out, "stimulus": None, "why": "平均心率 ≤ AeT+3" if easy_hr else
            f"Zone 3 {z3 / 60:.0f} 分 < {Z3_NEED_S // 60} 分、等效 T@VO2max {eq / 60:.1f} 分 < {Z5_MIN_S // 60} 分"}
