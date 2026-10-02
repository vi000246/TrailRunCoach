"""
Intensity class of one activity — easy / steady / race — for the race-power
back-test and the per-intensity calibrations (docs/research/racepower-v2.md
§3B, user feedback 2026-09-30: 「回測要搭配心率吧，如果我是 zone2 區間，感覺會不準」).

Why: running economy and the cost of grade differ by intensity, and a race
model must not be validated on Zone-2 training runs. Every number below is
one of the app's existing, cited zone definitions; the combination rule
(`classify`) is our own composite (推估) and says so.

INTENSITY (the one constant):
    Three zones after Seiler & Kjerland 2006 (Scand J Med Sci Sports 16:49–56;
    the app's status.SRC_SEILER / 強度分配 card: low < AeT, moderate AeT–LTHR,
    high ≥ LTHR), with the race boundary at Friel's Zone 4 (SubThreshold)
    lower bound, 0.95 × LTHR (zones.FRIEL_HR, checked against WKO5's own
    Friel HR level table).
      aet_frac_lthr  0.89   AeT when no test / estimate: top of Friel Z2
                            (dataset.aethr, zones.training_targets)
      race_frac_lthr 0.95   Friel Z4 lower bound
      easy_tol_bpm   3      "easy" = avg HR ≤ AeT + 3 (workout_review.
                            AET_MARGIN, equivalence.EASY_HR_TOL)
      power_low      0.80   Palladino three-zone low < 80 % CP
      power_high     0.95   Palladino three-zone high ≥ 95 % CP
                            (zones.PALLADINO_3ZONE)
      drift_easy     0.05   Pw:HR decoupling < 5 % = aerobic (Uphill Athlete
                            AeT drift test; status.DRIFT_GOOD)
      majority       0.50   推估: a zone "holds" the activity when it holds at
                            least half of the moving time with HR
      run_cadence    65     strides/min (130 spm): below it the athlete is
                            walking (workout_review.RUN_CADENCE)
      race_min_f     0.90   power check on an HR-"race" activity: P̄ ÷ P_sus(T)
                            with F1 (Riegel, k −0.07 ≈ Stryd's table, TTE
                            3000 s the workbook default) must reach the effort
                            bar's 吃力 cut (90 %, 推估). Used only to DEMOTE:
                            with CP a lower bound P_sus is understated, so a
                            run below 90 % even then was surely not maximal.

classify (推估): race = a season-plan race that day, or ≥ majority of the
moving time at ≥ 0.95·LTHR while the power reaches race_min_f of the
sustainable power for that duration (HR high but power below it = a
conflict → steady; with CP a lower bound that test is conservative — it
guards against an LTHR that is set too low), or moving average power
≥ 0.95·CP (sustained)
unless the HR says easy (a CP that is too low must not turn easy runs into
races).
easy = ≥ majority of the moving time below AeT and average HR < AeT + 3; when
the average sits within ±3 bpm of AeT (the ambiguous band) the Pw:HR drift
breaks the tie (> 5 % → steady, Uphill Athlete's reading of a drifting run).
Everything else is steady. Without HR the power three zones decide alone.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

INTENSITY = {
    "aet_frac_lthr": 0.89,
    "race_frac_lthr": 0.95,
    "easy_tol_bpm": 3.0,
    "power_low": 0.80,
    "power_high": 0.95,
    "drift_easy": 0.05,
    "majority": 0.50,
    "run_cadence": 65.0,
    "race_min_f": 0.90,
    "tte_s": 3000.0,
    "k": -0.07,
}
SOURCES = {
    "zones": "Seiler & Kjerland 2006 三區（低 < AeT、中 AeT–LTHR、高 ≥ LTHR）",
    "race": "Friel 心率 Z4（SubThreshold）下緣 0.95 × LTHR",
    "aet": "AeT：測試 > 自動估算 > 0.89 × LTHR（Friel Z2 上緣）",
    "power": "Palladino 三區：低 < 80 % CP、高 ≥ 95 % CP",
    "drift": "Uphill Athlete：Pw:HR 飄移 < 5 % = 有氧",
    "gait": "步頻 < 130 spm = 走路（workout_review）",
    "rule": "組合規則為推估（多數時間所在的區）",
}
CLASSES = ("easy", "steady", "race")
CLASS_ZH = {"easy": "輕鬆", "steady": "穩定", "race": "比賽強度"}
HR_LO, HR_HI = 40, 221          # histogram range (bpm)
WARMUP_S = 600.0                # drift excludes the first 10 min (workout_review)
DRIFT_MIN_S = 2400.0            # drift needs ≥ 40 min (workout_review.DRIFT_MIN_S)
MAX_DT = 30.0


def _dt(t: np.ndarray) -> np.ndarray:
    d = np.diff(t, prepend=t[0])
    d[~np.isfinite(d) | (d < 0) | (d > MAX_DT)] = 0.0
    return d


def stats(t, hr, power, kmh, cadence=None, min_kmh: float = 1.0) -> Optional[dict]:
    """Threshold-independent per-activity numbers (disk-cached by athlete.py):
    the moving-time HR histogram (1 bpm bins), moving average HR / power,
    the Pw:HR decoupling and the walking share of the moving time.
    Moving = kmh > min_kmh (the racepower RUN_MOVING_KMH rule)."""
    t = np.asarray(t, float)
    n = len(t)
    if n < 10:
        return None

    def fit(a):
        if a is None:
            return None
        a = np.asarray(a, float)[:n]
        return a if len(a) == n else np.concatenate([a, np.full(n - len(a), np.nan)])
    hr, p, v, cad = fit(hr), fit(power), fit(kmh), fit(cadence)
    d = _dt(t)
    mv = d > 0
    if v is not None:
        mv &= np.nan_to_num(v) > min_kmh
    moving_s = float(d[mv].sum())
    if moving_s <= 0:
        return None
    out = {"moving_s": moving_s, "hist_lo": HR_LO, "hist": None, "hr_s": 0.0, "hr_avg": None,
           "p_avg": None, "drift": None, "walk_share": None}
    if hr is not None:
        mh = mv & np.isfinite(hr) & (hr > HR_LO)
        if d[mh].sum() > 0:
            h = np.clip(np.round(hr[mh]), HR_LO, HR_HI - 1).astype(int) - HR_LO
            hist = np.bincount(h, weights=d[mh], minlength=HR_HI - HR_LO)
            out["hist"] = [round(float(x), 1) for x in hist]
            out["hr_s"] = float(d[mh].sum())
            out["hr_avg"] = float((hr[mh] * d[mh]).sum() / d[mh].sum())
    if p is not None and np.any(np.nan_to_num(p) > 0):
        out["p_avg"] = float((np.nan_to_num(p[mv]) * d[mv]).sum() / moving_s)
    if cad is not None and np.any(np.nan_to_num(cad) > 0):
        mc = mv & np.isfinite(cad)
        if d[mc].sum() > 0:
            out["walk_share"] = float(d[mc & (cad < INTENSITY["run_cadence"])].sum() / d[mc].sum())
    out["drift"] = pw_hr_drift(t, hr, p, mv, d)
    return out


def pw_hr_drift(t, hr, p, mv, d) -> Optional[float]:
    """Pw:HR decoupling (TrainingPeaks / Uphill Athlete): r = P/HR over the
    two halves of the moving time after a 10-minute warm-up, (r1 − r2)/r1.
    None without HR and power or with < 40 min after the warm-up."""
    if hr is None or p is None:
        return None
    after = (t - t[np.isfinite(t)][0]) >= WARMUP_S
    m = mv & after & np.isfinite(hr) & (hr > HR_LO) & np.isfinite(p) & (p > 0)
    if d[m].sum() < DRIFT_MIN_S - WARMUP_S:
        return None
    cum = np.cumsum(np.where(m, d, 0.0))
    half = cum[-1] / 2.0
    a, b = m & (cum <= half), m & (cum > half)
    if d[a].sum() <= 0 or d[b].sum() <= 0:
        return None
    r1 = (p[a] * d[a]).sum() / (hr[a] * d[a]).sum()
    r2 = (p[b] * d[b]).sum() / (hr[b] * d[b]).sum()
    return float((r1 - r2) / r1) if r1 > 0 else None


def shares(st: dict, aet: float, lthr: float) -> Optional[dict]:
    """Share of the HR-moving time below AeT, AeT–0.95·LTHR, ≥ 0.95·LTHR."""
    if not st or not st.get("hist") or not st.get("hr_s"):
        return None
    h = np.asarray(st["hist"], float)
    bpm = np.arange(len(h)) + st.get("hist_lo", HR_LO)
    hi_b = INTENSITY["race_frac_lthr"] * lthr
    tot = h.sum()
    low = h[bpm < aet].sum() / tot
    high = h[bpm >= hi_b].sum() / tot
    return {"low": float(low), "mid": float(1.0 - low - high), "high": float(high)}


def classify(st: Optional[dict], lthr: Optional[float], aet: Optional[float] = None,
             cp: Optional[float] = None, is_race: bool = False, cp_is_floor: bool = False) -> dict:
    """easy / steady / race (see the module docstring) with every number used
    and the reason. cls None when there is neither HR nor power. With
    `cp_is_floor` (CP only known as a lower bound) power can only demote a
    class, never promote it to race."""
    k = INTENSITY
    if aet is None and lthr:
        aet = k["aet_frac_lthr"] * lthr
    out = {"cls": None, "reason": "", "basis": None, "shares": None, "hr_avg": None,
           "hr_pct_lthr": None, "if": None, "drift": None, "walk_share": None,
           "lthr": lthr, "aet": aet, "cp": cp}
    if not st:
        out["reason"] = "沒有資料"
        return out
    out.update(hr_avg=st.get("hr_avg"), drift=st.get("drift"), walk_share=st.get("walk_share"))
    if cp and st.get("p_avg"):
        from backend.engine.racepower import difficulty as DF
        out["if"] = st["p_avg"] / cp
        out["f_power"] = st["p_avg"] / DF.p_sus(st["moving_s"], cp, None, k["tte_s"], k["k"])
    if lthr and st.get("hr_avg"):
        out["hr_pct_lthr"] = st["hr_avg"] / lthr
    if is_race:
        out.update(cls="race", reason="賽季計畫的比賽", basis="plan")
        return out
    p_high = out["if"] is not None and out["if"] >= k["power_high"] and not cp_is_floor
    out["cp_is_floor"] = cp_is_floor
    has_hr = bool(st.get("hr_s")) and st["hr_s"] >= k["majority"] * st["moving_s"] and lthr and aet
    if p_high and not has_hr:
        out.update(cls="race", reason=f"平均功率 {out['if']:.0%} CP ≥ {k['power_high']:.0%}", basis="power")
        return out
    if has_hr:
        sh = shares(st, aet, lthr)
        out["shares"] = sh
        out["basis"] = "hr"
        if sh["high"] >= k["majority"]:
            f = out.get("f_power")
            if f is not None and f < k["race_min_f"]:
                out.update(cls="steady", conflict=True,
                           reason=f"{sh['high']:.0%} 時間 ≥ 0.95 × LTHR，但功率只有這個時長可持續功率的 {f:.0%}"
                                  f"（< {k['race_min_f']:.0%}）：心率與功率不一致")
                return out
            out.update(cls="race", reason=f"{sh['high']:.0%} 時間 ≥ 0.95 × LTHR")
            return out
        avg = st["hr_avg"]
        if sh["low"] >= k["majority"] and avg < aet + k["easy_tol_bpm"]:
            dr = st.get("drift")
            if abs(avg - aet) <= k["easy_tol_bpm"] and dr is not None and dr > k["drift_easy"]:
                out.update(cls="steady", reason=f"平均心率貼近 AeT，Pw:HR 飄移 {dr:.1%} > 5 %")
            else:
                out.update(cls="easy", reason=f"{sh['low']:.0%} 時間 < AeT，平均 {avg:.0f} bpm")
            return out
        if p_high:
            out.update(cls="race", reason=f"平均功率 {out['if']:.0%} CP ≥ {k['power_high']:.0%}（心率不在低區）")
            return out
        out.update(cls="steady", reason=f"低區 {sh['low']:.0%}、高區 {sh['high']:.0%}，平均 {avg:.0f} bpm")
        return out
    if out["if"] is not None and not cp_is_floor:
        x = out["if"]
        out["basis"] = "power"
        out.update(cls="easy" if x < k["power_low"] else "steady",
                   reason=f"沒有心率：平均功率 {x:.0%} CP")
        return out
    out["reason"] = "沒有心率也沒有功率"
    return out
