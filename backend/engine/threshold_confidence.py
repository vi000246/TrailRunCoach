"""
Threshold confidence — is the LTHR in effect believable, is the max HR, and
which one is likely wrong (SP-64; docs/research/zones-and-thresholds.md §2.6).

LTHR anchors the HR zones, the easy-run cap, the Zone 3 / Zone 5 gates and the
race hrTSS; HRmax (and the resting HR) anchor the COROS %HRR / %HRmax 課表心率區間
(engine/hr_profile.py). Each can be wrong on its own: an LTHR estimated on hot,
sub-threshold runs; an HRmax taken from the watch account or one optical spike.
This module only REPORTS — it never writes a threshold. The suggestions it makes
join the 測試 indicator's box (status.i_testing → zone_events suggestion objects,
engine/suggestions.zone_rows) with a 「安排課表」 deep link (quality_gate.schedule_action).

Signals (level: error > strong > weak > hint; hint never lowers the confidence),
in the order of docs/research/zones-and-thresholds.md §2.6:

  LTHR
  1 source        not a test (自動估算 / 手錶匯入 / WKO5 預設) → low from the start
                  (watch LTHR mean error 9–11 bpm, COROS 8.9 — Lu 2025)
  2 premise       an applied estimate whose CP (the CP on the estimate's day) differs
                  from today's CP by > 5 % (推估) → strong
  3 consistency   an easy-run cap of any model ≥ LTHR → error; LTHR outside 80–98 % HRmax
                  or 73–95 % HRR → strong; outside 85–95 % / 80–94 % → weak (推估) —
                  Davis's 90 % range / Nuuttila 2025 (LT2 90.6 ± 2.5 % HRmax, 87.0 ± 3.4 % HRR).
                  Attributed by the diagnosis below (the HRmax may be the wrong one).
  4 long efforts  the best 60-min mean HR, or 0.95 × the best 20-min mean HR, above
                  LTHR (TrainingPeaks threshold notifications) in a cool run → strong, up;
                  in a hot / unknown-temperature run → hint only. Spikes filtered.
  5 race too low  a 40–60 min race whose mean HR is < 95 % LTHR → weak, down (推估)
  6 CP band       cool road runs, 30-s power 97–103 % CP for ≥ 10 min (≥ 3 such stretches):
                  median HR − LTHR > 5 bpm → weak, either way (推估; the cross-check of
                  Micheli 2025 — never a formula)
  7 events        ≥ 4 weeks off, the first cool spell, CP change > 5 % since the LTHR
                  date → weak. A wrist → chest-strap switch is not detectable: the FIT
                  files read here carry no HR-sensor type (left out).
  8 age           the LTHR test is > 8 weeks old (Friel: retest every 4–8 weeks) → hint

  HRmax
  source          watch account / 推估 from runs → weak (the account's value is often a
                  formula or a spike; hr_profile.max_hr resolution order)
  plausibility    the highest HR HELD ≥ 120 s across the runs of 365 days, optical spikes
                  and cadence lock removed by the shared cleaning (engine/hr_quality.clean): stored HRmax > that + 8 bpm → strong, down (推估); the
                  candidate is that sustained value (never applied by itself). Stored
                  HRmax < the highest 60-s hold − 3 bpm → strong, up (推估).
  rest HR         outside 30–90 bpm → strong; from the watch → hint

Diagnosis (which value is wrong): LTHR evidence = signals 4–6 (direction) and its
support (a 40–60 min race at ≥ 95 % LTHR, a CP-band median within 5 bpm, a 60-min
mean within 5 bpm below); HRmax evidence = the plausibility check. LTHR contradicted
and HRmax not → 「LTHR 測試」; HRmax contradicted and LTHR not → 「最大心率測試」;
both → both; an inconsistency with no evidence either way → both (undetermined).

Confidence: low = a low source or any error / strong signal; medium = a manual
source or a weak signal; high = otherwise. Workout targets in HR show a warning
badge when the LTHR (or, under the %HRR / %HRmax model, the HRmax) is low.

Every threshold whose source is not named above is 推估.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import statistics
from typing import Optional

import numpy as np

from backend.i18n import N_, _

# ---- named constants -------------------------------------------------------
# 3 consistency: Davis's 90 % range / Nuuttila 2025 (PMC12354492) — strong outside the
# individual range, weak outside about ±2 SD of the mean (推估)
LTHR_HRMAX_RANGE = (0.80, 0.98)
LTHR_HRR_RANGE = (0.73, 0.95)
LTHR_HRMAX_TYPICAL = (0.85, 0.95)       # 推估
LTHR_HRR_TYPICAL = (0.80, 0.94)         # 推估
# 2 / 7 premise and CP change
CP_CHANGE = 0.05                        # 推估 (Friel: retest when CP moves; 5 % = the CP-test note's line)
# 4 long efforts (TrainingPeaks threshold notifications: 60-min peak, or 95 % of the 20-min peak)
PEAK20_FACTOR = 0.95
EFFORT_DAYS = 120                       # 推估: the LTHR evidence window
# 5 race
RACE_MIN_S, RACE_MAX_S = 40 * 60, 60 * 60
RACE_LOW = 0.95                         # 推估
RACE_SUPPORT_HI = 1.03                  # 推估: a race at 95–103 % LTHR supports it
RACE_WORDS = re.compile(r"賽|馬拉松|race|marathon|半馬|10\s*[kK]|5\s*[kK]", re.I)
# 6 CP band
CPBAND = (0.97, 1.03)                   # 推估
CPBAND_MIN_S = 600
CPBAND_SETTLE_S = 120                   # 推估: HR still rising during the first 2 min
CPBAND_DIFF_BPM = 5.0                   # 推估
CPBAND_MIN_N = 3                        # 推估
SUPPORT_BPM = 5.0                       # 推估: a 60-min mean within 5 bpm below LTHR supports it
# 7 events / 8 age
BREAK_DAYS = 28                         # ≥ 4 weeks (detraining.md)
COOL_ACTIVE_DAYS = 60                   # zone_events.SEASON_ACTIVE_DAYS
TEST_AGE_DAYS = 56                      # Friel: every 4–8 weeks (the default; per athlete: threshold_calib, SP-69)
# HRmax plausibility (all 推估)
HRMAX_DAYS = 365
HOLD_LONG_S, HOLD_SHORT_S = 120, 60
PEAK_HOLD_S, PEAK_HOLD_LONG_S = 5, 10   # a test's peak: held ≥ 5–10 s
# the spike / range / gap / cadence-lock numbers: engine/hr_quality.py (SP-265, the one place)
HRMAX_GAP_BPM = 8.0
HRMAX_LOW_BPM = 3.0
HRMAX_OUTLIER_BPM = 5.0                 # thresholds.MHR_OUTLIER: a lone top run is dropped
HRMAX_MIN_RUNS = 3
RHR_RANGE = (30.0, 90.0)
# test conditions
TEST_MAX_C = 25.0                       # 台灣教練 / Friel's 30-min test: < 25 °C
HOT_LOOKBACK_DAYS = 14
HARD_GAP_H = 48
TAPER_DAYS = 10                         # i_testing: no test within 10 days of the A race
# test results
TT30_S, TT30_SKIP_S = 30 * 60, 10 * 60  # Friel: LTHR = mean HR of minutes 10–30
TT30_HR_COVER = 0.8
RESULT_DAYS = 60

LEVELS = ("error", "strong", "weak", "hint")
CONF_LABEL = {"high": N_("高"), "medium": N_("中"), "low": N_("低")}
LOW_LTHR_SOURCES = ("estimate", "watch", "default")
MID_SOURCES = ("manual", "wko5")
LOW_HRMAX_SOURCES = ("watch", "estimate")

TT30_TITLE = re.compile(r"(30\s*[′'分].*(閾值心率|LTHR))|(LTHR.*30\s*[′'分])|閾值心率測試|LTHR\s*(測試|test)", re.I)
HRMAX_TITLE = re.compile(r"最大心率測試|max(imum)?\s*h(eart\s*)?r(ate)?\s*test|hrmax\s*test", re.I)

SRC_RANGE = N_("Nuuttila 2025：休閒跑者 LT2 = 90.6 ± 2.5% 最大心率、87.0 ± 3.4% 儲備心率；Davis 90% 範圍 80–98%／73–95%")
SRC_TP = N_("TrainingPeaks 閾值通知：60 分鐘峰值心率，或 20 分鐘峰值 × 95%，高於現值就建議調高")
SRC_WATCH = N_("手錶 LTHR 平均誤差 9–11 bpm（COROS 8.9；Lu 2025）")
SRC_MICHELI = N_("CP 心率與 MLSS 心率平均差 0.6 bpm，但個人誤差 −16～+17 bpm（Micheli 2025）：只當交叉檢查")
SRC_FRIEL = N_("Friel：每 4–8 週測一次；30 分鐘獨跑測試的第 10–30 分平均心率＝LTHR（McGehee 2005 驗證：SEE 8 bpm）")
SRC_HRMAX = N_("撐 120 秒的最高心率（濾掉 3 秒內跳 ≥ 15 bpm 的尖峰和步頻鎖定）；8 bpm 是推估。腕式對胸帶 rc 0.67–0.92 vs 0.996（Gillinov 2017）")


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def signal(sid: str, target: str, level: str, text: str, direction: Optional[str] = None,
           estimate: bool = True, source: str = "", evidence: Optional[dict] = None) -> dict:
    assert level in LEVELS, level
    return {"id": sid, "target": target, "level": level, "direction": direction, "text": text,
            "estimate": estimate, "source": source, "evidence": evidence or {}}


# ---------------------------------------------------------------------------
# per-run HR: the 1-s grid, spikes and cadence lock removed (pure)
# ---------------------------------------------------------------------------

def clean_hr(t, hr, cadence_spm=None, speed_kmh=None) -> Optional[tuple]:
    """(grid seconds, HR) on a 1-s grid with NaN where the HR is not to be trusted:
    the shared cleaning (engine/hr_quality.clean, SP-265) — gaps > 5 s, out of
    range, spikes (a rise ≥ 15 bpm within 3 s that comes back within 30 s), the
    plateau of a moving up-step (one that doesn't come back; after a stop it is
    a real restart, kept — needs `speed_kmh`) and cadence lock. None without HR."""
    from backend.engine import hr_quality as HQ
    return HQ.clean(t, hr, cadence_spm, speed_kmh=speed_kmh)


def held_peak(y: np.ndarray, hold_s: int) -> Optional[float]:
    """The highest HR held for ≥ `hold_s` seconds (hr_quality.held_peak)."""
    from backend.engine import hr_quality as HQ
    return HQ.held_peak(y, hold_s)


def best_mean(y: np.ndarray, win_s: int, cover: float = 0.9) -> Optional[float]:
    """The best rolling `win_s`-second mean HR (windows ≥ `cover` valid)."""
    if y is None or len(y) < win_s:
        return None
    fin = np.isfinite(y)
    cs = np.concatenate([[0.0], np.cumsum(np.where(fin, y, 0.0))])
    cn = np.concatenate([[0], np.cumsum(fin.astype(int))])
    s = cs[win_s:] - cs[:-win_s]
    c = cn[win_s:] - cn[:-win_s]
    ok = c >= cover * win_s
    if not ok.any():
        return None
    return float((s[ok] / c[ok]).max())


def cp_band_stretches(g: np.ndarray, y: np.ndarray, t, power, cp: Optional[float]) -> list[dict]:
    """[{"start_s", "dur_s", "hr_med", "pct_cp"}] of the stretches where the 30-s power
    stays at 97–103 % CP for ≥ 10 min; HR median after the first 2 min."""
    if power is None or not cp or g is None:
        return []
    from backend.engine.session_stimulus import _grid, _roll, runs_of
    p1 = _grid(np.asarray(t, float), power, g)
    if p1 is None:
        return []
    p30 = _roll(p1, 30)
    m = np.isfinite(p30) & (p30 >= CPBAND[0] * cp) & (p30 <= CPBAND[1] * cp)
    out = []
    for a, b in runs_of(m, 10):
        if b - a < CPBAND_MIN_S:
            continue
        h = y[a + CPBAND_SETTLE_S:b]
        h = h[np.isfinite(h)]
        if h.size < (b - a - CPBAND_SETTLE_S) * 0.8:
            continue
        out.append({"start_s": float(a), "dur_s": float(b - a), "hr_med": float(np.median(h)),
                    "pct_cp": round(float(np.nanmean(p1[a:b])) / cp, 3)})
    return out


def run_summary(t, hr, power=None, cadence_spm=None, cp: Optional[float] = None,
                speed_kmh=None) -> Optional[dict]:
    """One run's HR evidence: held peaks (5 / 10 / 60 / 120 s), the best 20- / 60-min
    mean HR, the mean HR, the duration, the CP-band stretches."""
    c = clean_hr(t, hr, cadence_spm, speed_kmh)
    if c is None:
        return None
    g, y = c
    fin = y[np.isfinite(y)]
    if fin.size < 60:
        return None
    return {"s5": held_peak(y, PEAK_HOLD_S), "s10": held_peak(y, PEAK_HOLD_LONG_S),
            "s60": held_peak(y, HOLD_SHORT_S), "s120": held_peak(y, HOLD_LONG_S),
            "hr20": best_mean(y, 20 * 60), "hr60": best_mean(y, 60 * 60), "avg_hr": float(fin.mean()),
            "dur_s": float(len(g)), "cp_band": cp_band_stretches(g, y, t, power, cp), "cp": cp}


# ---------------------------------------------------------------------------
# the signals (pure: `runs` = [{"date", "s60", "s120", "hr20", "hr60", "avg_hr", "dur_s",
# "cp_band", "race", "temp": "cool" | "hot" | None}])
# ---------------------------------------------------------------------------

def _src_label(kind: Optional[str]) -> str:
    return {"test": _("測試"), "manual": _("手動輸入"), "estimate": _("自動估算"), "watch": _("手錶帳號"),
            "default": _("WKO5 預設值"), "wko5": _("WKO5 設定")}.get(kind or "", kind or "–")


def source_signals(lthr: dict) -> list[dict]:
    k = lthr.get("source_kind")
    if k in LOW_LTHR_SOURCES:
        return [signal("source", "lthr", "strong" if k == "default" else "weak",
                       _("LTHR 來源是{src}，不是測試：一開始就是低信心", src=_src_label(k)),
                       estimate=False, source=_(SRC_WATCH))]
    return []


def premise_signal(lthr: dict, cp_now: Optional[float]) -> list[dict]:
    """2: an applied estimate made at another CP."""
    cp0 = _f(lthr.get("cp_at_date"))
    if lthr.get("source_kind") != "estimate" or not cp0 or not cp_now:
        return []
    d = cp_now / cp0 - 1.0
    if abs(d) <= CP_CHANGE:
        return []
    return [signal("premise", "lthr", "strong",
                   _("LTHR 是 {date} 的自動估算，當時 CP {cp0:.0f} W、現在 {cp1:.0f} W（{d:+.0%}）：估算的前提已失效（推估 > 5%）",
                     date=lthr.get("date") or "–", cp0=cp0, cp1=cp_now, d=d),
                   "up" if d > 0 else "down", evidence={"cp_then": cp0, "cp_now": cp_now})]


def consistency_signals(lthr: Optional[float], mhr: Optional[float], rhr: Optional[float],
                        easy_caps: dict) -> list[dict]:
    """3: easy caps ≥ LTHR (error), LTHR as % HRmax / % HRR (strong / weak). Target "pair":
    the diagnosis decides which value they count against."""
    out = []
    if not lthr:
        return out
    for model, cap in sorted((easy_caps or {}).items()):
        if cap is not None and cap >= lthr:
            out.append(signal("easy_cap", "pair", "error",
                              _("{model} 的輕鬆跑上限 {cap:.0f} bpm ≥ LTHR {lthr:.0f}：生理上不可能",
                                model=model, cap=cap, lthr=lthr), estimate=False,
                              evidence={"model": model, "cap": cap}))
    if mhr:
        r = lthr / mhr
        lv = None
        if not LTHR_HRMAX_RANGE[0] <= r <= LTHR_HRMAX_RANGE[1]:
            lv = "strong"
        elif not LTHR_HRMAX_TYPICAL[0] <= r <= LTHR_HRMAX_TYPICAL[1]:
            lv = "weak"
        if lv:
            out.append(signal("pct_hrmax", "pair", lv,
                              _("LTHR {lthr:.0f} ＝ 最大心率 {mhr:.0f} 的 {r:.0%}（一般 85–95%，個人範圍 80–98%）",
                                lthr=lthr, mhr=mhr, r=r),
                              "up" if r < 0.9 else "down", source=_(SRC_RANGE), evidence={"ratio": round(r, 3)}))
        if rhr and mhr - rhr > 0:
            q = (lthr - rhr) / (mhr - rhr)
            lv = None
            if not LTHR_HRR_RANGE[0] <= q <= LTHR_HRR_RANGE[1]:
                lv = "strong"
            elif not LTHR_HRR_TYPICAL[0] <= q <= LTHR_HRR_TYPICAL[1]:
                lv = "weak"
            if lv:
                out.append(signal("pct_hrr", "pair", lv,
                                  _("LTHR 是儲備心率的 {q:.0%}（一般 80–94%，個人範圍 73–95%）", q=q),
                                  "up" if q < 0.87 else "down", source=_(SRC_RANGE), evidence={"ratio": round(q, 3)}))
    return out


def effort_signals(lthr: float, runs: list[dict]) -> tuple[list[dict], dict]:
    """4–6 on the runs of the evidence window: (signals, support) where support says what
    corroborates the LTHR ({"race": [...], "cp_band": {...}, "hr60": ...})."""
    out, support = [], {}
    # 4 long efforts above LTHR
    best = None
    for r in runs:
        for key, val in (("hr60", _f(r.get("hr60"))), ("hr20", None if r.get("hr20") is None
                                                       else PEAK20_FACTOR * float(r["hr20"]))):
            if val is not None and round(val) > lthr and (best is None or val > best[1]):
                best = (r, val, key)
    if best:
        r, val, key = best
        cool = r.get("temp") == "cool"
        what = _("60 分鐘平均心率") if key == "hr60" else _("20 分鐘峰值心率 × 95%")
        out.append(signal("long_effort", "lthr", "strong" if cool else "hint",
                          _("{date} {what} {v:.0f} bpm > LTHR {lthr:.0f}：撐得住比閾值高的心率，LTHR 可能設低了",
                            date=r["date"], what=what, v=val, lthr=lthr)
                          + ("" if cool else _("（熱天或不知道氣溫：只當提示）")),
                          "up", estimate=False, source=_(SRC_TP),
                          evidence={"date": r["date"], "value": round(val, 1), "kind": key, "temp": r.get("temp")}))
    cool_60 = [float(r["hr60"]) for r in runs if r.get("temp") == "cool" and _f(r.get("hr60"))]
    if cool_60 and lthr - SUPPORT_BPM <= max(cool_60) <= lthr:
        support["hr60"] = round(max(cool_60), 1)
    # 5 races of 40–60 min
    races = [r for r in runs if r.get("race") and RACE_MIN_S <= (r.get("dur_s") or 0) <= RACE_MAX_S
             and _f(r.get("avg_hr"))]
    low = [r for r in races if r["avg_hr"] < RACE_LOW * lthr]
    ok = [r for r in races if RACE_LOW * lthr <= r["avg_hr"] <= RACE_SUPPORT_HI * lthr]
    if low and not ok:
        r = min(low, key=lambda x: x["avg_hr"])
        out.append(signal("race_low", "lthr", "weak",
                          _("{date} 的 40–60 分鐘比賽平均心率 {hr:.0f} < 95% LTHR（{lo:.0f}）：LTHR 可能設高了（推估）",
                            date=r["date"], hr=r["avg_hr"], lo=RACE_LOW * lthr), "down",
                          evidence={"date": r["date"], "avg_hr": round(r["avg_hr"], 1)}))
    if ok:
        support["race"] = [{"date": r["date"], "avg_hr": round(r["avg_hr"], 1)} for r in ok]
    # 6 CP band, cool runs only
    st = [s["hr_med"] for r in runs if r.get("temp") == "cool" for s in (r.get("cp_band") or [])]
    if len(st) >= CPBAND_MIN_N:
        med = statistics.median(st)
        diff = med - lthr
        ev = {"n": len(st), "median": round(med, 1), "diff": round(diff, 1)}
        if abs(diff) > CPBAND_DIFF_BPM:
            out.append(signal("cp_band", "lthr", "weak",
                              _("涼天、97–103% CP 撐 ≥ 10 分的 {n} 段，心率中位數 {med:.0f}，和 LTHR 差 {d:+.0f} bpm（推估 > 5）",
                                n=len(st), med=med, d=diff), "up" if diff > 0 else "down",
                              source=_(SRC_MICHELI), evidence=ev))
        else:
            support["cp_band"] = ev
    return out, support


def event_signals(lthr: dict, today: dt.date, cp_now: Optional[float], brk: Optional[dict] = None,
                  cool: Optional[dict] = None) -> list[dict]:
    """7 events since the LTHR date, 8 its age."""
    out = []
    d0 = lthr.get("date")
    if brk and (brk.get("days") or 0) >= BREAK_DAYS and brk.get("return") and (not d0 or brk["return"] > d0) \
            and brk["return"] <= today.isoformat():
        out.append(signal("break", "lthr", "weak", _("停跑 {n} 天後還沒重測 LTHR", n=brk["days"]),
                          estimate=False, source=_("停訓：Coyle 1986、Houmard 1992")))
    if cool and cool.get("start") and (not d0 or cool["start"] > d0) and \
            (today - dt.date.fromisoformat(cool["start"])).days <= COOL_ACTIVE_DAYS:
        out.append(signal("cool_season", "lthr", "weak",
                          _("{date} 起天氣轉涼，LTHR 是夏天定的", date=cool["start"])))
    cp0 = _f(lthr.get("cp_at_date"))
    if lthr.get("source_kind") != "estimate" and cp0 and cp_now and abs(cp_now / cp0 - 1.0) > CP_CHANGE:
        out.append(signal("cp_change", "lthr", "weak",
                          _("LTHR 定下以後 CP 變了 {d:+.0%}（{cp0:.0f} → {cp1:.0f} W）", d=cp_now / cp0 - 1.0,
                            cp0=cp0, cp1=cp_now), "up" if cp_now > cp0 else "down"))
    if lthr.get("source_kind") == "test" and d0:
        age = (today - dt.date.fromisoformat(d0[:10])).days
        from backend.engine import calibrate as CAL
        from backend.engine import threshold_calib as TCAL
        limit = TCAL.lthr_test_age()            # TEST_AGE_DAYS or the athlete's own (SP-69)
        if age > limit:
            out.append(signal("age", "lthr", "hint",
                              _("上次 LTHR 測試是 {n} 天前（超過 {d:.0f} 天，{basis}；Friel：每 4–8 週一次）",
                                n=age, d=limit, basis=CAL.basis(TCAL.AGE)),
                              estimate=False, source=_(SRC_FRIEL)))
    return out


def hrmax_check(mhr: dict, runs: list[dict]) -> tuple[list[dict], Optional[dict]]:
    """(signals, candidate) for the stored HRmax vs the sustained peaks of `runs`."""
    out = []
    v = _f(mhr.get("value"))
    k = mhr.get("source_kind")
    if v and k in LOW_HRMAX_SOURCES:
        out.append(signal("source", "hrmax", "weak",
                          _("最大心率來自{src}，不是測試", src=_src_label(k)), estimate=False))
    s120 = sorted(((r["s120"], r["date"]) for r in runs if _f(r.get("s120"))), reverse=True)
    s60 = sorted(((r["s60"], r["date"]) for r in runs if _f(r.get("s60"))), reverse=True)
    if len(s120) < HRMAX_MIN_RUNS or not v:
        return out, None

    def top(xs):
        if len(xs) >= 2 and xs[0][0] - xs[1][0] > HRMAX_OUTLIER_BPM:
            return xs[1]
        return xs[0]
    t120, t60 = top(s120), top(s60) if s60 else (None, None)
    cand = {"value": round(t120[0]), "date": t120[1], "hold_s": HOLD_LONG_S,
            "s60": None if t60[0] is None else round(t60[0]),
            "top": [[d, round(x)] for x, d in s120[:5]], "estimate": True}
    if v - t120[0] > HRMAX_GAP_BPM:
        out.append(signal("sustained", "hrmax", "strong",
                          _("最大心率 {v:.0f}，但 365 天內撐 120 秒的最高心率只有 {s:.0f}（{date}）：差 {d:.0f} bpm > 8（推估），"
                            "高的數字多半是尖峰", v=v, s=t120[0], date=t120[1], d=v - t120[0]),
                          "down", source=_(SRC_HRMAX), evidence=cand))
    elif t60[0] is not None and t60[0] - v > HRMAX_LOW_BPM:
        cand = {**cand, "value": round(t60[0]), "date": t60[1], "hold_s": HOLD_SHORT_S}
        out.append(signal("sustained", "hrmax", "strong",
                          _("撐 60 秒的心率到過 {s:.0f}（{date}），比最大心率 {v:.0f} 還高：最大心率設低了（推估）",
                            s=t60[0], date=t60[1], v=v), "up", source=_(SRC_HRMAX), evidence=cand))
    return out, cand


def rhr_check(rhr: dict) -> list[dict]:
    v = _f(rhr.get("value"))
    if not v:
        return []
    if not RHR_RANGE[0] <= v <= RHR_RANGE[1]:
        return [signal("range", "rhr", "strong", _("靜息心率 {v:.0f} 不太合理（30–90）", v=v))]
    if rhr.get("source_kind") == "watch":
        return [signal("source", "rhr", "hint", _("靜息心率來自手錶帳號"), estimate=False)]
    return []


def confidence(source_kind: Optional[str], signals: list[dict], low_sources=LOW_LTHR_SOURCES) -> str:
    lv = {s["level"] for s in signals}
    if lv & {"error", "strong"} or source_kind in low_sources:
        return "low"
    if "weak" in lv or source_kind in MID_SOURCES:
        return "medium"
    return "high"


def diagnose(lthr_sigs: list[dict], pair_sigs: list[dict], hrmax_sigs: list[dict], support: dict,
             hrmax_source: Optional[str]) -> dict:
    """Which value is likely wrong: {"outlier": lthr | hrmax | both | undetermined | None,
    "tests": [...], "text"}."""
    lthr_bad = [s for s in lthr_sigs if s["id"] in ("long_effort", "race_low", "cp_band", "premise")
                and s["level"] in ("strong", "weak")]
    hr_bad = [s for s in hrmax_sigs if s["id"] == "sustained"]
    inconsistent = [s for s in pair_sigs if s["level"] in ("error", "strong", "weak")]
    lthr_ok = bool(support)
    hr_ok = not hr_bad and hrmax_source == "test"
    if lthr_bad and hr_bad:
        out = "both"
    elif hr_bad:
        out = "hrmax"
    elif lthr_bad:
        out = "lthr"
    elif inconsistent:
        out = "hrmax" if lthr_ok and not hr_ok else "lthr" if hr_ok and not lthr_ok else "undetermined"
    else:
        out = None
    tests = {"hrmax": ["hrmax"], "lthr": ["tt30"], "both": ["hrmax", "tt30"], "undetermined": ["hrmax", "tt30"],
             None: []}[out]
    sup = []
    if support.get("race"):
        sup.append(_("比賽 {n} 次平均心率在 95–103% LTHR", n=len(support["race"])))
    if support.get("cp_band"):
        sup.append(_("CP 附近 {n} 段心率中位數 {m:.0f}", n=support["cp_band"]["n"], m=support["cp_band"]["median"]))
    if support.get("hr60") is not None:
        sup.append(_("涼天 60 分鐘最高平均心率 {v:.0f}", v=support["hr60"]))
    text = {
        "hrmax": _("最可能錯的是最大心率") + (_("（LTHR 有獨立證據：{s}）", s=_("；").join(sup)) if sup else ""),
        "lthr": _("最可能錯的是 LTHR"),
        "both": _("LTHR 和最大心率都有問題"),
        "undetermined": _("LTHR 和最大心率互相矛盾，資料分不出是哪一個錯"),
        None: "",
    }[out]
    return {"outlier": out, "tests": tests, "text": text, "support": support}


TEST_TITLE = {"hrmax": N_("最大心率測試"), "tt30": N_("LTHR 測試（30 分鐘獨跑）")}


def test_conditions(test: str, hot: Optional[bool], kind: Optional[str], days_to_a: Optional[int],
                    today: dt.date) -> dict:
    """{"notes": [str], "earliest": iso | None, "wait_cool": bool}: < 25 °C for the LTHR test
    (hot → 「等天氣轉涼」, still schedulable), no hard session in the 48 h before, not in the
    taper / race week (earliest = the day after the A race)."""
    notes, earliest = [], None
    if test == "tt30" and hot:
        notes.append(_("最近氣溫 ≥ 25 °C：等天氣轉涼再做比較準（還是可以先排）"))
    notes.append(_("前 {h} 小時不要有長跑、強度課或比賽", h=HARD_GAP_H))
    if (kind in ("taper", "event")) or (days_to_a is not None and 0 <= days_to_a < TAPER_DAYS):
        notes.append(_("減量期／比賽週不測，賽後再測"))
        if days_to_a is not None and days_to_a >= 0:
            earliest = (today + dt.timedelta(days=days_to_a + 1)).isoformat()
    return {"notes": notes, "earliest": earliest, "wait_cool": bool(test == "tt30" and hot)}


def assess(lthr: dict, mhr: dict, rhr: dict, today: dt.date, runs: list[dict], easy_caps: dict,
           cp_now: Optional[float] = None, brk: Optional[dict] = None, cool: Optional[dict] = None,
           hot: Optional[bool] = None, kind: Optional[str] = None, days_to_a: Optional[int] = None,
           hr_model: str = "lthr", tested: Optional[set] = None) -> dict:
    """The whole check on plain inputs (pure; module doc). `lthr` = {"value", "source_kind",
    "date", "cp_at_date", "label"}; `mhr` / `rhr` = {"value", "source_kind", "label"}; `runs`
    as effort_signals; `tested` = tests done since the signals (no suggestion for them)."""
    lv = _f(lthr.get("value"))
    ev_from = (today - dt.timedelta(days=EFFORT_DAYS)).isoformat()
    recent = [r for r in runs if r.get("date", "") >= ev_from]
    lsig = source_signals(lthr) + premise_signal(lthr, cp_now)
    support: dict = {}
    if lv:
        es, support = effort_signals(lv, recent)
        lsig += es
    lsig += event_signals(lthr, today, cp_now, brk, cool)
    pair = consistency_signals(lv, _f(mhr.get("value")), _f(rhr.get("value")), easy_caps)
    hsig, cand = hrmax_check(mhr, runs)
    rsig = rhr_check(rhr)
    dg = diagnose(lsig, pair, hsig, support, mhr.get("source_kind"))
    # the consistency signals count against the value the diagnosis blames
    for s in pair:
        to = {"hrmax": ["hrmax"], "lthr": ["lthr"]}.get(dg["outlier"], ["lthr", "hrmax"])
        for t in to:
            (lsig if t == "lthr" else hsig).append({**s, "target": t})
    lconf = confidence(lthr.get("source_kind"), lsig)
    hconf = confidence(mhr.get("source_kind"), hsig, LOW_HRMAX_SOURCES) if mhr.get("value") else "low"
    # suggestions: the diagnosis, else a low-confidence value (a low source alone → low priority)
    tests = list(dg["tests"])
    if not tests:
        if lconf != "high" and lv:
            tests.append("tt30")
        if hconf == "low" and mhr.get("value"):
            tests.insert(0, "hrmax")
    tests = [t for t in tests if t not in (tested or set())]
    firm = any(s["level"] in ("error", "strong") for s in lsig + hsig if s["id"] != "source") or \
        bool(dg["outlier"] and dg["outlier"] != "undetermined")
    sugs = []
    if tests:
        reasons = [s["text"] for s in lsig + hsig if s["level"] != "hint"][:4]
        conds = {t: test_conditions(t, hot, kind, days_to_a, today) for t in tests}
        earliest = max((c["earliest"] for c in conds.values() if c["earliest"]), default=None)
        names = re.sub(r"(?<=[^\x00-\x7f])(?=[A-Za-z])|(?<=[A-Za-z)])(?=[^\x00-\x7f（）])", " ",
                       _("和").join(_(TEST_TITLE[t]) for t in tests))
        title = _("建議：做{tests}", tests=(" " if names[:1].isascii() else "") + names)
        sugs.append({"id": "thr_check:" + "+".join(tests), "kind": "test_suggestion", "tests": tests,
                     "title": title, "text": _("；").join(([dg["text"]] if dg["text"] else []) + reasons),
                     "earliest": earliest, "detected": today.isoformat(),
                     "conditions": [_(TEST_TITLE[t]) + _("：") + _("；").join(conds[t]["notes"]) for t in tests],
                     "wait_cool": any(c["wait_cool"] for c in conds.values()),
                     "caveat": _(SRC_HRMAX) if "hrmax" in tests else _(SRC_FRIEL), "estimate": True,
                     "source": _(SRC_FRIEL), "evidence": {"diagnosis": dg["outlier"]},
                     "links": [schedule_link(t, earliest) for t in tests],
                     **({} if firm else {"priority": "low"})})
    warn = {"lthr": {"low": lconf == "low", "text": _("LTHR 可信度低：心率目標可能不準（{why}）",
                                                         why=(lsig[0]["text"] if lsig else ""))},
            "hrmax": {"low": hconf == "low" and hr_model in ("hrr", "hrmax"),
                      "text": _("最大心率可信度低：儲備心率／最大心率區間的目標可能不準，測準之前建議改用乳酸閾區間")},
            "model": hr_model}
    return {"lthr": {**lthr, "confidence": lconf, "signals": lsig},
            "hrmax": {**mhr, "confidence": hconf, "signals": hsig, "candidate": cand},
            "rhr": {**rhr, "confidence": confidence(rhr.get("source_kind"), rsig, ()) if rhr.get("value") else None,
                    "signals": rsig},
            "diagnosis": dg, "suggestions": sugs, "warn": warn}


# ---------------------------------------------------------------------------
# 「安排課表」 deep links (SP-39: quality_gate.schedule_action)
# ---------------------------------------------------------------------------

TEST_TEMPLATE = {"tt30": ("lib:friel_lthr30", "race"), "hrmax": ("lib:maxhr_hill", None)}


def schedule_link(test: str, day: Optional[str] = None) -> dict:
    from backend.engine.quality_gate import schedule_action
    key, proto = TEST_TEMPLATE[test]
    a = schedule_action("template", key, proto)
    if day:
        a["href"] += f"&day={day}"
    return {**a, "test": test, "label": _("安排課表：{test}", test=_(TEST_TITLE[test]))}


# ---------------------------------------------------------------------------
# test results (pure): Friel's 30-min TT and the max-HR test
# ---------------------------------------------------------------------------

def tt30_result(t, hr, power=None, speed=None, cp: Optional[float] = None) -> Optional[dict]:
    """The 30-min all-out block (the best 30-min mean power, else speed, else HR):
    {"lthr" = mean HR of its minutes 10–30, "hr30", "power", "vs_cp", "start_s"}."""
    c = clean_hr(t, hr, None, speed)
    if c is None:
        return None
    g, y = c
    if len(g) < TT30_S:
        return None
    from backend.engine.session_stimulus import _grid
    tt = np.asarray([np.nan if v is None else v for v in t], float)
    p1 = _grid(tt, power, g) if power is not None else None
    v1 = _grid(tt, speed, g) if speed is not None else None
    key = p1 if p1 is not None and np.isfinite(p1).sum() > TT30_S * 0.8 else \
        v1 if v1 is not None and np.isfinite(v1).sum() > TT30_S * 0.8 else y
    fin = np.isfinite(key)
    cs = np.concatenate([[0.0], np.cumsum(np.where(fin, key, 0.0))])
    cn = np.concatenate([[0], np.cumsum(fin.astype(int))])
    s = cs[TT30_S:] - cs[:-TT30_S]
    n = cn[TT30_S:] - cn[:-TT30_S]
    ok = n >= 0.8 * TT30_S
    if not ok.any():
        return None
    i = int(np.argmax(np.where(ok, s / np.maximum(n, 1), -np.inf)))
    last = y[i + TT30_SKIP_S:i + TT30_S]
    lf = last[np.isfinite(last)]
    if lf.size < TT30_HR_COVER * (TT30_S - TT30_SKIP_S):
        return None
    whole = y[i:i + TT30_S]
    pw = float(np.nanmean(p1[i:i + TT30_S])) if p1 is not None and np.isfinite(p1[i:i + TT30_S]).any() else None
    return {"lthr": round(float(lf.mean())), "hr30": round(float(np.nanmean(whole)), 1), "start_s": float(i),
            "power": None if pw is None else round(pw), "vs_cp": None if pw is None or not cp else round(pw / cp - 1, 3)}


def hrmax_result(t, hr, cadence_spm=None, speed_kmh=None) -> Optional[dict]:
    """The filtered peak: the highest HR held ≥ 5 s (and ≥ 10 s) after spikes / cadence lock."""
    c = clean_hr(t, hr, cadence_spm, speed_kmh)
    if c is None:
        return None
    s5, s10 = held_peak(c[1], PEAK_HOLD_S), held_peak(c[1], PEAK_HOLD_LONG_S)
    if s5 is None:
        return None
    return {"value": round(s5), "s10": None if s10 is None else round(s10)}


def result_apply(kind: str, r: dict, date: str) -> Optional[dict]:
    """POST /api/v1/plan/thresholds/apply-estimate body (never sent by the app itself)."""
    if kind == "tt30" and r.get("lthr"):
        return {"lthr": r["lthr"], "date": date, "lthr_method": "friel30",
                "note": _("Friel 30 分鐘測試 {date}：第 10–30 分平均心率", date=date)}
    if kind == "hrmax" and r.get("value"):
        return {"mhr": r["value"], "date": date, "mhr_method": "test",
                "note": _("最大心率測試 {date}：撐 ≥ 5 秒的最高心率（濾掉尖峰）", date=date)}
    return None


def test_kind(titles: list[str]) -> Optional[str]:
    for t in titles:
        if not t:
            continue
        if HRMAX_TITLE.search(t):
            return "hrmax"
        if TT30_TITLE.search(t):
            return "tt30"
    return None


# ---------------------------------------------------------------------------
# the dataset side
# ---------------------------------------------------------------------------

RUN_KEY = "thr_conf_run_v2"      # v2 (SP-265): the shared HR cleaning (hr_quality.clean, with speed)


def _ch(ds, w, name):
    try:
        return ds.channel(w.idx, name)
    except Exception:                       # noqa: BLE001
        return None


def _cad_spm(ds, w):
    c = _ch(ds, w, "cadence")
    if c is None:
        return None
    return [None if v is None else float(v) * 2.0 for v in c]      # strides → steps (workout_review)


def _summary(ds, w) -> Optional[dict]:
    cp = None
    try:
        cp = _f(ds.cp(w))
    except Exception:                       # noqa: BLE001
        cp = None
    road = "runningtrail" not in (w.tags or [])
    return run_summary(_ch(ds, w, "elapsedtime"), _ch(ds, w, "heartrate"),
                       _ch(ds, w, "power") if road else None, _cad_spm(ds, w), cp, _ch(ds, w, "speed"))


def _temp_class(h: Optional[dict]) -> Optional[str]:
    if not h or h.get("src") == "season":
        return None
    from backend.engine import zone_events as ZE
    t, hd = _f(h.get("temp_c")), _f(h.get("hadley"))
    if t is None and hd is None:
        return None
    if ZE._cool(t if t is not None else 0.0, hd):
        return "cool"
    return "hot"


def _race(ds, w, title: str) -> bool:
    from backend.engine import workout_review as WR
    iso = WR._wdate(w).isoformat()
    plan = getattr(ds, "plan", None)
    if any(e.date == iso and e.kind in ("race", "road") for e in getattr(plan, "events", None) or []):
        return True
    return bool(RACE_WORDS.search(title or ""))


def gather_runs(ds, today: dt.date, acts: Optional[list] = None) -> list[dict]:
    """effort_signals' rows for the runs of the last 365 days (cached per run)."""
    from backend.engine import workout_review as WR
    from backend.engine import zone_events as ZE
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    runs = [w for w in ds.workouts if w.sport == "run" and tday - HRMAX_DAYS < math.floor(w.day) <= tday]
    if acts is None:
        try:
            from backend.engine import heat_data as HD
            acts = HD.exposures()[0]
        except Exception:                   # noqa: BLE001
            acts = []
    try:
        heat = ZE.run_heat(ds, runs, acts)
    except Exception:                       # noqa: BLE001
        heat = {}
    out = []
    for w in runs:
        cached = getattr(ds, "cached_series", None)
        s = cached(RUN_KEY, w, lambda w=w: _summary(ds, w)) if cached else _summary(ds, w)
        if not s:
            continue
        title = WR._title(w)
        out.append({**s, "date": WR._wdate(w).isoformat(), "idx": w.idx, "race": _race(ds, w, title),
                    "temp": _temp_class(heat.get(w.idx)), "title": title})
    if hasattr(ds, "flush_series"):
        ds.flush_series()
    return out


def recent_hot(runs: list[dict], today: dt.date) -> Optional[bool]:
    """Were the road runs of the last 14 days mostly hot? None without temperatures."""
    lo = (today - dt.timedelta(days=HOT_LOOKBACK_DAYS)).isoformat()
    xs = [r["temp"] for r in runs if r.get("date", "") >= lo and r.get("temp")]
    if not xs:
        return None
    return sum(1 for x in xs if x == "hot") > len(xs) / 2.0


def lthr_info(ds, plan, today: dt.date) -> dict:
    from backend.engine.planning import threshold_row
    r = threshold_row(plan, "lthr", today) if plan is not None else None
    if r is not None:
        m = r["method"]
        kind = "estimate" if m == "estimate" else "test" if m in ("friel30", "lab", "race") else "manual"
        cp0 = plan.threshold_on("cp", dt.date.fromisoformat(r["date"]))
        return {"value": r["value"], "source_kind": kind, "date": r["date"], "label": r["label"],
                "method": m, "cp_at_date": cp0}
    hist = (getattr(getattr(ds, "athlete", None), "settings", None) or {}).get("runthr") or []
    if not hist:
        return {"value": None, "source_kind": None, "date": None, "label": None}
    d, v = max(((d, v) for d, v in hist if d <= today), default=hist[0])
    default = all(x == dt.date(1980, 1, 1) for x, _v in hist)
    return {"value": _f(v), "source_kind": "default" if default else "wko5",
            "date": None if default else d.isoformat(), "label": _("WKO5 設定"), "cp_at_date": None}


def mhr_info(ds, today: dt.date, acc=None) -> dict:
    from backend.engine import hr_profile as HP
    m = HP.max_hr(ds, today, acc)
    kind = {"coros": "watch", "estimate": "estimate"}.get(m.get("kind"))
    if m.get("kind") == "manual":
        meth = m.get("method")
        kind = "test" if meth in ("test", "race", "lab") else "estimate" if meth == "estimate" else "manual"
    return {"value": m.get("value"), "source_kind": kind, "label": m.get("source")}


def rhr_info(ds, today: dt.date, acc=None) -> dict:
    from backend.engine import hr_profile as HP
    r = HP.rest_hr(ds, today, acc)
    return {"value": r.get("value"), "source_kind": {"coros": "watch", "manual": "manual"}.get(r.get("kind")),
            "label": r.get("source")}


def easy_caps(lthr: Optional[float], mhr: Optional[float], rhr: Optional[float], acc=None,
              aet: Optional[float] = None) -> dict:
    """The easy-run cap (Z2 top) of each 課表心率區間 model the data allows, + a measured AeT."""
    from backend.engine import hr_profile as HP
    out = {}
    for m in HP.PLAN_MODELS:
        z = HP.zone_rows(m, lthr, mhr, rhr, acc)
        if "rows" in z:
            out[_(HP.MODEL_SHORT[m])] = float(HP.band(z["rows"], 2, 2)[1])
    if aet:
        out[_("實測 AeT")] = float(aet)
    return out


def tested_since(plan, since: str) -> set:
    from backend.engine import zone_events as ZE
    out = ZE.tested_since(plan, since)
    for t in getattr(plan, "thresholds", None) or []:
        if (t.date or "")[:10] >= since and t.mhr is not None and getattr(t, "mhr_method", None) in ("test", "race", "lab"):
            out.add("hrmax")
    return out


def test_results(ds, plan, today: dt.date, days: int = RESULT_DAYS) -> list[dict]:
    """The latest LTHR 30-min / max-HR test activities of the last `days`: a planned test
    session done by the activity, or the activity's title, says which (never a guess
    from the data). Each: {kind, date, idx, result, apply, applied}."""
    from backend.engine import workout_review as WR
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    sess = WR._plan_test_sessions(ds)
    found: dict = {}
    for w in sorted(ds.workouts, key=lambda x: x.day):
        if w.sport != "run" or not (tday - days < math.floor(w.day) <= tday):
            continue
        iso = WR._wdate(w).isoformat()
        titles = [s.get("title") or "" for s in sess if WR._done_by_this(s, w, iso)] + [WR._title(w)]
        k = test_kind(titles)
        if not k:
            continue
        t, hr = _ch(ds, w, "elapsedtime"), _ch(ds, w, "heartrate")
        if k == "tt30":
            cp = None
            try:
                cp = _f(ds.cp(w))
            except Exception:               # noqa: BLE001
                pass
            r = tt30_result(t, hr, _ch(ds, w, "power"), _ch(ds, w, "speed"), cp)
        else:
            r = hrmax_result(t, hr, _cad_spm(ds, w), _ch(ds, w, "speed"))
        if not r:
            found[k] = {"kind": k, "date": iso, "idx": w.idx, "result": None, "apply": None, "applied": False,
                        "reason": _("資料不足，算不出結果（心率斷掉或不到 30 分鐘）")}
            continue
        field_ = "lthr" if k == "tt30" else "mhr"
        applied = any(getattr(x, field_, None) is not None and (x.date or "")[:10] >= iso
                      for x in getattr(plan, "thresholds", None) or [])
        found[k] = {"kind": k, "date": iso, "idx": w.idx, "result": r, "applied": applied,
                    "apply": None if applied else result_apply(k, r, iso)}
    return list(found.values())


def _cp_now(ds, plan, today: dt.date) -> Optional[float]:
    """The plan's CP on `today`, else the dataset's CP of the latest run."""
    cp_now = plan.threshold_on("cp", today) if plan is not None else None
    if cp_now is None:
        rs = [w for w in ds.workouts if w.sport == "run"]
        try:
            cp_now = _f(ds.cp(rs[-1])) if rs else None
        except Exception:                   # noqa: BLE001
            cp_now = None
    return cp_now


# The signals that are EVIDENCE against the LTHR (not its source, its age, a break or the
# season): they invalidate a measured LTHR for the Zone 5 gate's UA path (quality_gate.lthr_invalid;
# owner 2026-10-05). A hot / unknown-temperature long effort is only a hint and doesn't count.
EVIDENCE_IDS = ("long_effort", "race_low", "cp_band", "cp_change")


def lthr_evidence(ds, plan, today: dt.date, runs: Optional[list] = None) -> list[dict]:
    """The evidence signals (EVIDENCE_IDS, level weak or above) against the LTHR in effect from
    the runs AFTER its date (within EFFORT_DAYS): 4 a long effort above it in a cool run, 5 a
    40–60-min race < 95 % of it, 6 the CP-band cross-check; 7 the CP changed > 5 % since its
    date. A dateless LTHR reads the whole window. `runs`: gather_runs' rows (default: gathered)."""
    lt = lthr_info(ds, plan, today)
    lv = _f(lt.get("value"))
    if not lv or lt.get("source_kind") == "default":
        return []
    since = str(lt.get("date") or "")[:10]
    lo = (today - dt.timedelta(days=EFFORT_DAYS)).isoformat()
    rows = gather_runs(ds, today) if runs is None else runs
    rs = [r for r in rows if r.get("date", "") >= lo and (not since or r.get("date", "") > since)]
    sigs, _support = effort_signals(lv, rs)
    sigs += [s for s in event_signals(lt, today, _cp_now(ds, plan, today)) if s["id"] == "cp_change"]
    return [s for s in sigs if s["id"] in EVIDENCE_IDS and s["level"] in ("error", "strong", "weak")]


def check(ds, plan, today: dt.date, brk: Optional[dict] = None, cool: Optional[dict] = None,
          kind: Optional[str] = None, days_to_a: Optional[int] = None, acts: Optional[list] = None) -> dict:
    """assess() on the dataset: the thresholds in effect, the runs, the test results."""
    memo = getattr(ds, "memo", None)
    import json
    mk = ("threshold_confidence", today.isoformat(), json.dumps(
        [t.__dict__ for t in getattr(plan, "thresholds", None) or []], sort_keys=True, default=str),
        len(getattr(ds, "workouts", []) or []), kind, days_to_a)
    if isinstance(memo, dict) and mk in memo:
        return memo[mk]
    from backend.engine import hr_profile as HP
    acc = HP.account()
    lt = lthr_info(ds, plan, today)
    mh, rh = mhr_info(ds, today, acc), rhr_info(ds, today, acc)
    runs = gather_runs(ds, today, acts)
    cp_now = _cp_now(ds, plan, today)
    aet = None
    from backend.engine.planning import threshold_row
    ar = threshold_row(plan, "aethr", today) if plan is not None else None
    if ar and ar["measured"]:
        aet = ar["value"]
    caps = easy_caps(_f(lt.get("value")), _f(mh.get("value")), _f(rh.get("value")), acc, aet)
    since = lt.get("date") or "0000"
    out = assess(lt, mh, rh, today, runs, caps, cp_now, brk, cool, recent_hot(runs, today), kind, days_to_a,
                 HP.plan_model(), tested_since(plan, max(since, (today - dt.timedelta(days=14)).isoformat())))
    out["test_results"] = test_results(ds, plan, today)
    out["easy_caps"] = caps
    out["cp_now"] = cp_now
    if isinstance(memo, dict):
        memo[mk] = out
    return out


def warn_of(chk: Optional[dict]) -> Optional[dict]:
    """The badge data for HR-target templates / sessions (workout_editor.js), or None."""
    if not chk:
        return None
    w = chk.get("warn") or {}
    if not ((w.get("lthr") or {}).get("low") or (w.get("hrmax") or {}).get("low")):
        return None
    return w
