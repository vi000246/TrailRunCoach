"""
AeT 飄移測試 — schedule it, analyse it, offer 「套用這次的 AeT」.

Design: docs/research/aerobic-base-readiness.md §6. Uphill Athlete's heart-rate
drift test (https://uphillathlete.com/aerobic-training/heart-rate-drift/), run at
a fixed *power* (Stryd is steadier than pace; Pa:HR is kept as a cross-check):

Two lengths, chosen by the 課表偏好 weekday cap (`variant_for`):
  * standard (no cap, or a cap ≥ 80 min): 15′ warm-up + 60′ fixed power + 5′
    cool-down = 80 min (UA 40–60′; Evoke 60′);
  * short (a weekday cap < 80 min): UA's minimum — 10′ warm-up (until
    sweating, HR ≤ the start HR) + 40′ fixed power ("If you only have 40
    minutes, do that."), cool-down optional = 50 min; never shorter, even
    under a 45-min cap.
  Both on a weekday first (the athlete trail-runs on weekends; pick_day): the
  short one only Mon–Fri, the standard one may fall back to a weekend day that
  isn't the long run's. An air-conditioned treadmill 2–3 % with a fan first,
  else an early flat loop (not trails). Note the temperature: heat inflates
  the drift. Evoke's early abort: HR already 10 above the start at minute 10
  of the block and rising → started too high, stop, retest another day at a
  slower pace (Evoke); "about 5 bpm lower" is ours (推估 — neither UA nor Evoke
  gives a number: aerobic-base-readiness.md §8).

Analysis (`analyze`): the main block is the time after the warm-up (15′ for
the standard test, 10′ for the short one — `warm_for`, from the title, else
the length), up to 60′ of it, cool-down trimmed; Pw:HR over its halves (Pa:HR
without power) — on the short test the first 20′ vs the last 20′. Both are
the strict tier (≥ 40′ after the warm-up). UA's bands: < 3.5 % → below AeT
(next time start 5 bpm higher — UA's own number), 3.5–5 % → the first-half HR is
the AeT, > 5 % → started above AeT (UA: start lower; the 5 bpm is 推估, LOWER_BPM).

The same three checks as workout_review.drift_of (the daily runs):
  * the 40-min floor counts *after* the warm-up;
  * a fast finish (last 10 % of the block > 5 % above the rest) is refused — 自訂;
  * heat is a band, not a refusal (heat bands, 2026-10-02): the result
    carries `temp_band` / `heat` (the route_weather archive's air
    temperature when it has the activity, else the watch's minus the
    athlete's wrist bias: their own with ≥ 10 paired runs, else 3.7 °C —
    workout_review.activity_temp / watch_air / watch_bias_of). In heat the verdict
    adds 「熱環境，結果可能偏高」: a pass (or UA's 「at」 band, whose AeT is then
    on the low side) still counts — conservative; a fail may be the heat.
    The session text keeps 「氣溫 25 °C 以下時開始」 as advice (HEAT_TEXT).

The activity is the AeT test when the plan says so first: a done test session
that is the AeT test (is_aet_session: protocol / kind aet, gen_key test_aet)
done_by this activity — workout_review.scheduled_aet_test, the way
cp_protocols' CP tests are matched; then the title, a plan AeT row that day,
or a ≥ 55-min steady run (workout_review.classify).
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Optional

import numpy as np

from backend.i18n import M_, N_, _

# (warm-up, main, cool-down) minutes per length
VARIANTS = {"standard": (15, 60, 5),      # 80′: UA 40–60′ after a 10–15′ warm-up; Evoke 60′
            "short": (10, 40, 0)}         # 50′: UA's minimum ("If you only have 40 minutes, do that.")
STD_MIN = sum(VARIANTS["standard"])       # 80: a weekday cap below this → the short test
SHORT_MIN = sum(VARIANTS["short"])        # 50
WARM_S = 10 * 60                # the short test's warm-up (and the shortest)
WARM_STD_S = 15 * 60            # the standard test's
MAIN_MAX_S = 60 * 60            # the analysis window: up to 60′ after the warm-up (UA 40–60)
MAIN_MIN_S = 40 * 60            # the planned minimum = UA's
UA_MIN_S = 40 * 60              # UA: "We don't recommend relying on tests less than 40 minutes long"
UA_SLACK_S = 30                 # 推估: a few lost samples (GPS / Stryd dropouts) don't fail a 40′ test
BAND_LOW, BAND_HIGH = 0.035, 0.05
FAST_FINISH = 0.05              # 自訂
HEAT_C = 25.0                   # 台灣教練's condition: the session text's advice (HEAT_TEXT), not a refusal
MAX_STOPPED = 0.05
MAX_CV = 0.15                   # the old unsourced 30-s CV rule: information only now (drift v2 uses VI)
START_BELOW = 5.0               # 自訂: 0.89 × LTHR − 5 as the starting HR without an estimate
POWER_OF_CP = 0.75              # 自訂: starting power when nothing better is known (Palladino easy ≤ 80 % CP)
RECENT_DAYS = 28                # 推估: a test in the last 4 weeks → don't suggest another (minimum spacing)
LOWER_BPM = 5                   # 推估: > 5 % → start lower — UA says "lower", Evoke "a slower pace"; neither
                                # gives a number (aerobic-base-readiness.md §8, 2026-10-06 verbatim check)
# B3 (unsourced-rules.md): no fixed expiry / cadence any more (16 weeks, 4–6 weeks, every 5 base
# weeks: no source). The test is due only for a reason (quality_gate.aet_test_reason).
HEAT_TEXT = N_("氣溫 25 °C 以下時開始（熱會讓心率偏高、飄移失真；台灣教練、Lafrenz 2008）")

SRC_UA_TEST = N_("Uphill Athlete 心率飄移測試（https://uphillathlete.com/aerobic-training/heart-rate-drift/，教練）")

# ---- the protocols (課表偏好 plan.prefs.aet_test_protocol) -------------------------
# judge: ua = halves, UA's 3.5 / 5 % bands (finds the AeT HR); evoke = halves, > 5 % = above
# AeT; friel = halves < 5 % / 5–10 / > 10 (aerobic endurance at AeT); xu = HR at minute 10 vs
# minute 90, < 10 % = the base is sufficient (徐國峰's published comparison, not halves).
# SP-905: the texts are msgids (a 「；」 outside brackets joins two: each is read back on its own)
PROTOCOLS = {
    "xu90": {"label": N_("徐國峰 90 分鐘平路 1 區"), "warm": 10, "main": 80, "cool": 0, "judge": "xu",
             "title": "AeT 飄移測試 徐國峰 90 分", "terrain": N_("平坦路段（排在週末長跑日）"),
             "hold": N_("配速固定在 E 配速，心率自然變"),
             "rule": N_("第 10 分鐘心率 A、第 90 分鐘心率 B：(B − A) ÷ A < 10% 有氧基礎夠（5% 內國家級）"),
             "source": "；".join((N_("徐國峰部落格（2016-12，有氧基礎檢測）"), N_("徐國峰《跑者都該懂的跑步數據》")))},
    "ua60": {"label": N_("Uphill Athlete 60 分"), "warm": 15, "main": 60, "cool": 5, "judge": "ua",
             "title": "AeT 飄移測試 60 分", "terrain": N_("跑步機 2–3% 或平路環線（不要山路）"),
             "hold": N_("固定功率（UA 原文固定配速；有 Stryd 用功率較穩）"),
             "rule": N_("前半對後半：< 3.5% 低於 AeT、3.5–5% 前半心率就是 AeT、> 5% 起始太高"),
             "source": SRC_UA_TEST},
    "ua40": {"label": N_("Uphill Athlete 40 分（最短版）"), "warm": 10, "main": 40, "cool": 0, "judge": "ua",
             "title": "AeT 飄移測試 40 分", "terrain": N_("跑步機 2–3% 或平路環線"),
             "hold": N_("固定功率"), "rule": N_("同 UA 60 分（\"If you only have 40 minutes, do that.\"、不建議短於 40 分）"),
             "source": SRC_UA_TEST},
    "evoke60": {"label": N_("Evoke 60 分"), "warm": 10, "main": 60, "cool": 5, "judge": "evoke",
                "title": "AeT 飄移測試 Evoke 60 分", "terrain": N_("跑步機 2% 或平的環線（每 1.6 km 爬升 < 30 m、不要折返）"),
                "hold": N_("固定速度（\"DO NOT TOUCH THE SPEED CONTROL\"）"),
                "rule": "；".join((N_("1 小時內心率升 > 5% → 起始在 AeT 以上"), N_("第 10 分鐘已高 10 下還在升 → 提早放棄"))),
                "source": N_("Evoke（https://evokeendurance.com/resources/our-latest-thinking-on-aerobic-assessment-for-the-mountain-athlete/）")},
    "friel": {"label": N_("Friel 1 小時心率飄移"), "warm": 10, "main": 60, "cool": 5, "judge": "friel",
              "title": "AeT 飄移測試 Friel 60 分", "terrain": N_("穩定的平路"),
              "hold": N_("在 AeT 心率附近穩定跑（1–2 小時取下限 1 小時）"),
              "rule": N_("前後半心率飄移 < 5% 有氧耐力夠、5–10% 還在進步、> 10% 不足"),
              "source": N_("Friel（https://www.trainingpeaks.com/blog/aerobic-endurance-and-decoupling/，教練）")},
}


def _parts(text: str) -> list[str]:
    """A table text cut at its 「；」 outside brackets (the msgids it is joined from)."""
    out, buf, depth = [], "", 0
    for c in text:
        depth += (c in "（(《") - (c in "）)》")
        if c == "；" and depth == 0:
            out.append(buf)
            buf = ""
        else:
            buf += c
    return out + [buf]


def _joined(text: str) -> str:
    """A table text that is msgids joined with 「；」 (PROTOCOLS, SRC): each one through _() (SP-905)."""
    return "；".join(_(x) for x in _parts(text))
STANDARD = "xu90"               # the auto choice — justification in aerobic-base-readiness.md §6.3
BACKUP = "ua40"                 # when the standard doesn't fit (a long-day cap < 90 min)
PROTOCOL_CHOICES = ("auto",) + tuple(PROTOCOLS)
XU_MIN = sum((PROTOCOLS["xu90"]["warm"], PROTOCOLS["xu90"]["main"]))      # 90
TITLE_RE = re.compile(r"飄移測試\s*(?:(徐國峰|Evoke|Friel)\s*)?(\d+)\s*分")

SRC = "；".join((N_("Uphill Athlete 心率飄移測試（https://uphillathlete.com/aerobic-training/heart-rate-drift/："
                    "\"If you only have 40 minutes, do that.\"、不建議短於 40 分；前 20 分對後 20 分，< 3.5% / 3.5–5% / > 5%）"),
                 N_("Evoke 提早中止（https://evokeendurance.com/resources/our-latest-thinking-on-aerobic-assessment-for-the-"
                    "mountain-athlete/）")))
TITLES = {"standard": "AeT 飄移測試 60 分", "short": "AeT 飄移測試 40 分"}   # the main block's length
TITLE = TITLES["standard"]
AT_TITLE_LEN = re.compile(r"飄移測試\s*(\d+)\s*分")
PROTOCOL = "aet"                # the stored session's `protocol` (not a cp_protocols protocol)
BAND_LABEL = {"below": "低於 AeT", "at": "就是 AeT", "above": "高於 AeT",
              "base_ok": "有氧基礎夠", "base_mid": "還在進步", "base_not": "有氧基礎還不夠"}
XU_GOOD, FRIEL_GOOD, FRIEL_BAD = 0.10, 0.05, 0.10


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def band_of(drift: Optional[float], judge: str = "ua") -> Optional[str]:
    """The protocol's verdict: ua below / at / above (3.5 / 5 %); evoke at
    (≤ 5 %: the start HR is at or below AeT) / above; friel base_ok (< 5 %) /
    base_mid / base_not (> 10 %); xu base_ok (< 10 %) / base_not."""
    if drift is None:
        return None
    if judge == "xu":
        return "base_ok" if drift < XU_GOOD else "base_not"
    if judge == "friel":
        return "base_ok" if drift < FRIEL_GOOD else "base_mid" if drift <= FRIEL_BAD else "base_not"
    if judge == "evoke":
        return "at" if drift <= BAND_HIGH else "above"
    if drift < BAND_LOW:
        return "below"
    if drift <= BAND_HIGH:
        return "at"
    return "above"


def protocol_of_title(title: Optional[str]) -> Optional[str]:
    """The protocol key of a stored AeT-test title (TITLE_RE), None when unknown."""
    m = TITLE_RE.search(title or "")
    if not m:
        return None
    who, n = m.group(1), int(m.group(2))
    return {"徐國峰": "xu90", "Evoke": "evoke60", "Friel": "friel"}.get(who) or ("ua60" if n >= 60 else "ua40")


def resolve_protocol(pref: Optional[str], cap_weekday: Optional[int] = None,
                     cap_long: Optional[int] = None) -> str:
    """The protocol to schedule. auto = STANDARD (徐國峰 90 min, on the long
    day) unless the long-day cap can't fit 90 min → BACKUP (UA 40). ua60 under
    a weekday cap < 80 → ua40 (the old variant_for rule). Others as chosen."""
    p = pref if pref in PROTOCOLS else "auto"
    if p == "auto":
        lc = cap_long if cap_long is not None else None
        return STANDARD if lc is None or lc >= XU_MIN else BACKUP
    if p == "ua60" and cap_weekday is not None and cap_weekday < STD_MIN:
        return "ua40"
    if p == "xu90" and cap_long is not None and cap_long < XU_MIN:
        return BACKUP
    return p


def is_xu(s: dict) -> bool:
    return protocol_of_title(s.get("title")) == "xu90"


def analyze(t, hr, speed=None, power=None, temp=None, climb_m_per_km: Optional[float] = None,
            trail: bool = False, warm_s: float = WARM_S, main_s: float = MAIN_MAX_S,
            temp_c: Optional[float] = None, temp_src: Optional[str] = None, judge: str = "ua",
            watch_bias: Optional[dict] = None, cadence_spm=None) -> dict:
    """Halves drift test on one recording (UA / Evoke / Friel by `judge`;
    徐國峰's 10-vs-90 is analyze_xu). `ok` False with `reason` when it isn't a
    fair test. `temp_c` (with `temp_src`, route_weather / watch) overrides the
    mean of the `temp` channel over the block; that mean is air minus
    `watch_bias` (zone_events.dataset_watch_bias's dict; None = the 3.7 °C default)."""
    if judge == "xu":
        return analyze_xu(t, hr, speed, temp, climb_m_per_km, trail, temp_c, temp_src, watch_bias, power=power)
    from backend.engine.workout_review import DRIFT_MAX_VI, MAX_DT, STOP_KMH, _arr, _grid1, _hms, power_vi
    out = {"ok": False, "reason": "", "drift": None, "pw_drift": None, "pa_drift": None, "hr1": None, "hr2": None,
           "main_s": None, "band": None, "basis": None, "judge": judge, "vi": None, "cv30": None}
    if hr is None or not np.isfinite(np.asarray(hr, dtype=float)).any():
        out["reason"] = "沒有心率"
        return out
    if trail or (climb_m_per_km is not None and climb_m_per_km >= 20.0):
        out["reason"] = "有坡（越野或每公里爬升 ≥ 20 m）：AeT 測試要在平路或跑步機"
        return out
    grid, h = _grid1(t, hr)
    if grid is None:
        out["reason"] = "資料不夠"
        return out
    n = len(grid)
    series = {}
    for name, ch in (("power", power), ("speed", speed), ("temp", temp)):
        if ch is not None and np.isfinite(np.asarray(ch, dtype=float)).any() and \
                (np.nan_to_num(np.asarray(ch, dtype=float)) != 0).any():
            g2, y = _grid1(t, ch)
            series[name] = _arr(y, n) if g2 is not None else None
    rel = grid - grid[0]
    win = (rel >= warm_s) & (rel < warm_s + main_s)
    outp = series.get("power") if series.get("power") is not None else series.get("speed")
    if outp is None:
        out["reason"] = "沒有功率或速度"
        return out
    # trim the cool-down: trailing minutes whose 60-s output is < 85 % of the block's median
    idx = np.where(win)[0]
    if len(idx) == 0:
        out["reason"] = f"暖身 {warm_s / 60:.0f} 分之後沒有資料"
        return out
    o = np.nan_to_num(outp)
    # 60-s mean over the samples that exist: at the recording's end a plain
    # "same" convolution pads zeros and would trim the last ~20 s of a test
    # stopped right at 40′ (no cool-down)
    o60 = np.convolve(o, np.ones(60), "same") / np.convolve(np.ones(len(o)), np.ones(60), "same")
    med = float(np.median(o[idx][o[idx] > 0])) if (o[idx] > 0).any() else 0.0
    end = idx[-1]
    while end > idx[0] and o60[end] < 0.85 * med:
        end -= 1
    win &= np.arange(n) <= end
    moving = np.isfinite(h) & (h > 0) & (o > 0)
    if series.get("speed") is not None:
        moving &= ~(np.nan_to_num(series["speed"]) <= STOP_KMH)
    span = float(win.sum())
    stopped = float((win & ~moving).sum())
    if span > 0 and stopped / span > MAX_STOPPED:
        out["reason"] = f"測試段停了 {_hms(stopped)}（> 5%）：中途不要停"
        return out
    m = win & moving
    main = float(m.sum())
    out["main_s"] = main
    if main < UA_MIN_S - UA_SLACK_S:
        out["reason"] = f"暖身後只有 {main // 60:.0f} 分鐘（< 40 分，UA 不建議採用）"
        return out
    ow = o[m]
    if series.get("power") is not None:
        # drift v2: VI = NP30 / AP on the block's moving samples (the same function as drift_of);
        # the old 30-s CV > 15 % (no source) stays as information
        vi, cv = power_vi(np.where(m, series["power"], np.nan), m)
        out.update(vi=vi, cv30=cv)
        if vi is not None and vi > DRIFT_MAX_VI:
            out["reason"] = f"功率起伏大（VI {vi:.3f} > {DRIFT_MAX_VI:.2f}，推估）：要固定功率"
            return out
    k = max(1, int(len(ow) * 0.1))
    if ow[:-k].mean() > 0 and ow[-k:].mean() > (1 + FAST_FINISH) * ow[:-k].mean():
        out["reason"] = f"最後 10% 比前段快 {(ow[-k:].mean() / ow[:-k].mean() - 1) * 100:.0f}%（> 5%）：快速結尾會讓飄移看起來比較小"
        return out
    tp = series.get("temp")
    if temp_c is None and tp is not None and np.isfinite(tp[m]).any():
        temp_c, temp_src = _watch_air(float(np.nanmean(tp[m])), watch_bias), "watch"
    _tag_heat(out, temp_c, temp_src, watch_bias)
    cum = np.cumsum(m.astype(float))
    half = cum[-1] / 2.0
    a, b = m & (cum <= half), m & (cum > half)
    h1, h2 = float(h[a].mean()), float(h[b].mean())

    def dec(x):
        if x is None:
            return None
        x1, x2 = float(np.nanmean(x[a])), float(np.nanmean(x[b]))
        if not (x1 > 0 and x2 > 0 and h1 > 0 and h2 > 0):
            return None
        r1, r2 = x1 / h1, x2 / h2
        return (r1 - r2) / r1
    out["pw_drift"] = dec(series.get("power"))
    out["pa_drift"] = dec(series.get("speed"))
    basis = "Pw:HR" if out["pw_drift"] is not None else "Pa:HR"
    d = out["pw_drift"] if out["pw_drift"] is not None else out["pa_drift"]
    if d is None:
        out["reason"] = "有效資料不夠"
        return out
    out.update(ok=True, drift=d, hr1=h1, hr2=h2, band=band_of(d, judge), basis=basis)
    from backend.engine.workout_review import aet_hr_check      # SP-266: bad HR → reference only
    aet_hr_check(out, t, hr, grid, m, cadence_spm, speed)
    return out


def analyze_xu(t, hr, speed=None, temp=None, climb_m_per_km: Optional[float] = None, trail: bool = False,
               temp_c: Optional[float] = None, temp_src: Optional[str] = None,
               watch_bias: Optional[dict] = None, power=None) -> dict:
    """徐國峰's 90-minute test (blog 2016-12): flat, every stop ≤ 30 s (the
    ≤ 25 °C line, 台灣教練: a temperature band on the result, _tag_heat); HR at minute 10
    (A) vs minute 90 (B), each the ±1-min mean;
    drift = (B − A) ÷ A; < 10 % = the base is sufficient. Not halves.
    SP-275: the output must hold — minutes 80–90 not > 5 % slower than 10–20 (power when
    there is power; base_check.output_hold); without speed or power `hold_note` says so."""
    from backend.engine import base_check as BC
    from backend.engine.quality_gate import xu_drift_of
    out = {"ok": False, "reason": "", "drift": None, "pw_drift": None, "pa_drift": None, "hr1": None, "hr2": None,
           "main_s": None, "band": None, "basis": "HR 第 10→90 分", "judge": "xu"}
    if trail or (climb_m_per_km is not None and climb_m_per_km >= BC.XU_FLAT_M_PER_KM):
        out["reason"] = "有坡（越野或每公里爬升 ≥ 20 m）：徐國峰的測試要全程平坦"
        return out
    r = xu_drift_of(t, hr)
    if r is None:
        tt = np.asarray(t, dtype=float)
        dur = float(np.nanmax(tt) - np.nanmin(tt)) if np.isfinite(tt).any() else 0.0
        out["reason"] = f"只跑了 {dur / 60:.0f} 分鐘：要連續跑到第 91 分鐘（測試長度 90 分鐘：徐國峰部落格）"
        return out
    stop = BC.longest_stop(t, speed)
    if stop > BC.XU_STOP_S:
        out["reason"] = f"第 10–90 分鐘停了 {stop:.0f} 秒：補給每次不能停超過 30 秒（徐國峰）"
        return out
    h = out["hold"] = BC.output_hold(t, speed, power)
    if h["basis"] is None:
        out["hold_note"] = BC.hold_text(h)
    elif not h["ok"]:
        out["reason"] = BC.hold_text(h)
        return out
    if temp_c is None and temp is not None:
        tp = np.asarray(temp, dtype=float)
        if np.isfinite(tp).any():
            temp_c, temp_src = _watch_air(float(np.nanmean(tp)), watch_bias), "watch"
    _tag_heat(out, temp_c, temp_src, watch_bias)
    d = r["drift"]
    out.update(ok=True, drift=d, hr1=r["hr10"], hr2=r["hr90"], main_s=80 * 60.0, band=band_of(d, "xu"))
    return out


def _watch_air(t: float, bias: Optional[dict] = None) -> float:
    """The block's watch mean as air: minus the dataset's wrist bias (None = 3.7 °C)."""
    from backend.engine.workout_review import watch_air
    return watch_air(t, (bias or {}).get("bias_c"))


def _tag_heat(out: dict, temp_c: Optional[float], temp_src: Optional[str], bias: Optional[dict] = None) -> None:
    """Heat bands: the temperature is a band on the result, not a refusal
    (> 25 °C was one). `temp_band`, `heat` (warm / hot), `chip`; on the
    watch, `temp_bias` (workout_review.bias_of_watch) when the bias is known."""
    from backend.engine import workout_review as WR
    band = WR.temp_band(temp_c)
    out.update(temp_c=temp_c, temp_src=temp_src if temp_c is not None else None, temp_band=band,
               heat=WR.is_heat(band), chip="🌡 " + WR.TEMP_BAND_LABEL.get(band, "溫度不明"))
    if temp_c is not None and temp_src == "watch" and bias:
        out["temp_bias"] = WR.bias_of_watch(bias)


def lines(r: dict, aet_now: Optional[float] = None) -> list[str]:
    """The review card's verdict (UA's three bands), with the heat note when
    the test ran above 25 °C (heat_line)."""
    out = _lines(r, aet_now)
    if r.get("ok") and r.get("heat"):
        out = out + [heat_line(r)]
    from backend.engine.workout_review import aet_hr_line          # SP-266
    hl = aet_hr_line(r)
    return out + [hl] if hl else out


def heat_line(r: dict) -> str:
    """Heat bands and the AeT test: heat inflates the drift (Lafrenz 2008;
    Beiter 2025). A result that passes / lands in UA's 「at」 band in heat
    still counts — the true (cool) drift is lower, so the first-half HR as
    AeT can only be on the low side (conservative); a fail / 「above」 in heat
    may be the heat."""
    from backend.engine.workout_review import HEAT_NOTE
    passed = r.get("band") in ("at", "below", "base_ok")
    return (f"{r.get('chip') or '🌡'}：{HEAT_NOTE}" +
            ("；熱天通過仍算數（保守）" if passed else "，可能是熱造成的：涼一點（25 °C 以下）的日子再測"))


def _lines(r: dict, aet_now: Optional[float] = None) -> list[str]:
    if not r.get("ok"):
        return [r.get("reason") or "不是有效的 AeT 測試"]
    d, h1 = r["drift"], r["hr1"]
    judge = r.get("judge") or "ua"
    if judge == "xu":
        head = f"徐國峰 90 分鐘：第 10 分 {h1:.0f} → 第 90 分 {r['hr2']:.0f} bpm，飄移 {d * 100:.1f}%"
        note = [r["hold_note"]] if r.get("hold_note") else []
        if r["band"] == "base_ok":
            return [f"{head} < 10%：有氧基礎夠（5% 內國家級），可以加 5 區",
                    _("這次不給 AeT 數字：這個測試看的是有氧基礎，AeT 由平常多次輕鬆跑的飄移推估")] + note
        return [f"{head} ≥ 10%：有氧基礎還不夠，繼續 1 區長跑", "5 區先不排；3 區照排"] + note
    if judge == "friel":
        head = _("心率飄移 {d:.1f}%（Friel 1 小時）", d=d * 100)
        return [{"base_ok": f"{head} < 5%：有氧耐力夠", "base_mid": f"{head}（5–10%）：有氧耐力還在進步",
                 "base_not": f"{head} > 10%：有氧耐力不足"}[r["band"]]]
    if judge == "evoke":
        head = _("心率飄移 {d:.1f}%（Evoke 60 分）", d=d * 100)
        if r["band"] == "at":
            return [f"{head} ≤ 5%：起始心率 {h1:.0f} bpm 在 AeT 或以下", "可以按「套用這次的 AeT」（保守：取起始心率）"]
        return [f"{head} > 5%：起始心率 {h1:.0f} bpm 高於 AeT", _lower_line(h1)]
    head = _("心率飄移 {d:.1f}%（暖身後 {m:.0f} 分）", d=d * 100, m=r["main_s"] / 60)
    now = f"（目前 {aet_now:.0f}）" if aet_now else ""
    if r["band"] == "below":
        return [f"{head} < 3.5%：前半心率 {h1:.0f} bpm 還在 AeT 以下", f"下次起始心率 +5 bpm（約 {h1 + 5:.0f}）再測一次{now}"]
    if r["band"] == "at":
        return [f"{head}，在 3.5–5%：AeT = 前半平均心率 {h1:.0f} bpm{now}", "可以按「套用這次的 AeT」寫進門檻"]
    return [f"{head} > 5%：起始心率 {h1:.0f} bpm 高於 AeT", _lower_line(h1) + now]


def _lower_line(h1: float) -> str:
    """> 5 %: start lower next time. The 5 bpm is 推估 (LOWER_BPM): UA only says
    a lower start, Evoke a slower pace another day."""
    return _("下次起始心率降 {n} bpm（約 {hr:.0f}；{n} bpm 是推估）再測一次", n=LOWER_BPM, hr=h1 - LOWER_BPM)


def warm_for(title: str, duration_s: float) -> float:
    """The warm-up to cut: from the test's title (「AeT 飄移測試 60 分」 → the
    standard 15′, 「… 40 分」 → the short 10′), else 15′ when the run is long
    enough for 40′ after it, else 10′ (推估)."""
    p = protocol_of_title(title)
    if p:
        return PROTOCOLS[p]["warm"] * 60.0
    m = AT_TITLE_LEN.search(title or "")
    if m:
        return WARM_STD_S if int(m.group(1)) >= 60 else WARM_S
    return WARM_STD_S if duration_s >= WARM_STD_S + UA_MIN_S else WARM_S


def planned_warm_s(s: Optional[dict]) -> Optional[float]:
    """The warm-up the scheduled session asked for (「暖身 N 分」 in its target / detail), in
    seconds, or None — 每堂課前加熱身 (SP-364) can make it longer than the protocol's."""
    from backend.engine.warmup import WARM_RE
    m = WARM_RE.search(f"{(s or {}).get('target') or ''} {(s or {}).get('detail') or ''}")
    return int(m.group(1)) * 60.0 if m else None


def analyze_workout(ds, w, m: Optional[dict] = None) -> Optional[dict]:
    """The analysis of the protocol the title (or the scheduled session's)
    names: warm-up cut, window and judging rule (PROTOCOLS); untitled = UA."""
    from backend.engine import workout_review as WR
    s = WR._samples(ds, w)
    if s is None:
        return None
    m = m if m is not None else (WR.measure(ds, w) or {})
    temp = ds.channel(w.idx, "temperature")
    tc, src = WR.activity_temp(ds, w, None)       # the archive only; the watch is averaged over the block
    wb = WR.watch_bias_of(ds) if tc is None and temp is not None else None   # …minus the athlete's wrist bias
    sched = WR.scheduled_aet_test(ds, w) or {}
    own = WR._title(w) or ""
    title = own if (TITLE_RE.search(own) or AT_TITLE_LEN.search(own)) else (sched.get("title") or own)
    proto = protocol_of_title(title) or "ua60"
    judge = PROTOCOLS[proto]["judge"]
    warm = warm_for(title, _f(w.metrics.get("duration")) or float(s["t"][-1] - s["t"][0]))
    # the scheduled session's own (longer) warm-up — 每堂課前加熱身 (SP-364) — is cut too
    warm = max(warm, planned_warm_s(sched) or 0.0)
    main = PROTOCOLS[proto]["main"] * 60.0 if proto in ("evoke60", "friel") else MAIN_MAX_S
    return {**analyze(s["t"], s["hr"], s["speed"], s["power"], temp, m.get("climb_m_per_km"),
                      trail="runningtrail" in w.tags, warm_s=warm, main_s=main, temp_c=tc, temp_src=src,
                      judge=judge, watch_bias=wb,
                      cadence_spm=None if s["cadence"] is None else np.asarray(s["cadence"], float) * 2.0),
            "warm_s": warm, "protocol": proto}


def latest_aet_test(ds, today: dt.date, days: int = 120) -> Optional[dict]:
    """The latest run classified test_aet in `days`: {date, idx, hr1, drift,
    pw_drift, pa_drift, band, aethr_suggest, aethr_now, delta, ok, reason}.
    aethr_suggest only when band == "at" (the first-half HR, rounded)."""
    from backend.engine import workout_review as WR
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    found = None
    for w in sorted(ds.workouts, key=lambda x: x.day):
        if not (tday - days < math.floor(w.day) <= tday) or w.sport != "run":
            continue
        # too short to be a fair test — unless the plan says it was the test
        # (then analyze() refuses it and the card / i_testing say why)
        if (_f(w.metrics.get("duration")) or 0) < WARM_S + UA_MIN_S - UA_SLACK_S and \
                WR.scheduled_aet_test(ds, w) is None:
            continue
        m = WR.measure(ds, w)
        if not m or WR.classify(ds, w, m)["type"] != "test_aet":
            continue
        r = analyze_workout(ds, w, m)
        if r is None:
            continue
        now = m.get("aet")
        sug = round(r["hr1"]) if r.get("ok") and r["band"] == "at" and not r.get("hr_ref") else None
        found = {"idx": w.idx, "date": WR._wdate(w).isoformat(), **{k: r.get(k) for k in (
            "ok", "reason", "hr1", "drift", "pw_drift", "pa_drift", "band", "basis", "main_s", "judge",
            "protocol", "temp_c", "temp_band", "heat", "hr_ref")},
            "aethr_suggest": sug, "aethr_now": now, "delta": (sug - now) if sug is not None and now else None}
    WR._flush(ds)
    return found


def apply_body(t: dict) -> Optional[dict]:
    """POST /api/v1/plan/thresholds/apply-estimate body for a test in band "at"."""
    if not t or t.get("aethr_suggest") is None:
        return None
    return {"aethr": t["aethr_suggest"], "date": t["date"],
            "note": _("AeT 飄移測試 {date}：心率飄移 {d:.1f}%", date=t["date"], d=t["drift"] * 100)}


def applied(plan, t: dict) -> bool:
    """The plan already has an aethr row dated on / after the test."""
    return any(r.aethr is not None and r.date >= t["date"] for r in (getattr(plan, "thresholds", None) or []))


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------

def start_hr(aet_estimate: Optional[float], lthr: Optional[float]) -> Optional[float]:
    """thresholds.estimate()'s aethr suggestion, else 0.89 × LTHR − 5 (自訂, conservative)."""
    if aet_estimate:
        return float(aet_estimate)
    return 0.89 * lthr - START_BELOW if lthr else None


def start_power(cp: Optional[float]) -> Optional[float]:
    """CP × 0.75 (POWER_OF_CP, 自訂): the fallback when the athlete's own HR → power relation is
    not known (start_target)."""
    return POWER_OF_CP * cp if cp else None


# ---- SP-949 (owner 2026-10-27): the start of the next test chained to the last one ----------------
# UA: below AeT → "start 5 bpm higher next time" (UA's own number); above → start lower (LOWER_BPM,
# 推估); at → the first-half HR is the AeT. The review card says the same (_lines: 「約 h1 ± 5」), so the
# chain starts from the last test's first-half HR, not from the HR the session asked for.
CHAIN_BPM = {"below": 5, "at": 0, "above": -LOWER_BPM}
CHAIN_WHY = {"below": N_("起始心率 {hr:.0f}：上次 AeT 測試（{date}）前半 {h1:.0f} bpm 還在 AeT 以下，+5 bpm"),
             "at": N_("起始心率 {hr:.0f}：上次 AeT 測試（{date}）前半 {h1:.0f} bpm 就是 AeT"),
             "above": N_("起始心率 {hr:.0f}：上次 AeT 測試（{date}）前半 {h1:.0f} bpm 高於 AeT，降 {n} bpm（推估）")}


def chained_start_hr(last: Optional[dict], aet_estimate: Optional[float], lthr: Optional[float]) -> dict:
    """{"hr", "why" (None = the estimate), "from": test | estimate}: the start HR of the next UA /
    Evoke test. A valid last test (latest_aet_test: ok, HR not flagged — hr_ref —, judged by UA's
    or Evoke's halves) moves the start from its first-half HR (CHAIN_BPM); otherwise start_hr()
    (the AeT estimate, else 0.89 × LTHR − 5). 徐國峰 / Friel results say nothing about the AeT HR."""
    t = last if isinstance(last, dict) else {}
    band, h1 = t.get("band"), _f(t.get("hr1"))
    if t.get("ok") and not t.get("hr_ref") and (t.get("judge") or "ua") in ("ua", "evoke") and h1 \
            and band in CHAIN_BPM:
        hr = round(h1) + CHAIN_BPM[band]
        return {"hr": float(hr), "from": "test",
                "why": _(CHAIN_WHY[band], hr=hr, date=str(t.get("date") or "")[:10], h1=h1, n=LOWER_BPM)}
    return {"hr": start_hr(aet_estimate, lthr), "why": None, "from": "estimate"}


# ---- SP-949: the fixed power / pace from the athlete's own HR → power / pace relation ------------
# CP × 0.75 and the AeT estimate were two unrelated numbers (the 2026-10-26 test held 136 W at 149 bpm;
# 0.75 × CP 195 W ≈ 147 W would have started near 155). The recent flat easy road runs give the
# athlete's own line: one-minute segments after the warm-up, grade within ± 2 % (the owner's rule) for
# the segment and the minute before it (HR lags 55–70 s, Hunt 2015), moving, HR not flagged
# (workout_review.run_hr_quality), power only from a Stryd (watch power never counts: SP-950).
# A linear fit of power (or speed) on HR, read at the start HR. All thresholds 推估.
FIT_DAYS = 42           # 推估: the last 6 weeks (the CP's shelf life, power_state.fresh_days)
FIT_MAX_RUNS = 10       # 推估: the latest 10 qualifying runs
FIT_MIN_RUNS = 3        # 推估: ≥ 3 runs (base_check.easy_targets' minimum)
FIT_RUN_SEGS = 10       # 推估: a run counts with ≥ 10 flat minutes after its warm-up
FIT_WARM_S = WARM_S     # the warm-up cut: UA's 10 minutes
FIT_SEG_S = 60
FIT_LEAD_S = 60         # 推估: the minute before the segment flat and moving too (the HR lag)
FIT_GRADE = 0.02        # the owner's ± 2 % (SP-949)
FIT_MIN_KMH = 5.0       # 推估: running, not walking
FIT_EXTRAP_BPM = 5.0    # 推估: the start HR at most 5 bpm outside the runs' HR range (5th–95th percentile)
FIT_BAND = 0.03         # ± 3 %: the fixed power's band (UA's text), the fixed pace's too
FIT_KEY = "aet_start_segments_v1"
R_FEW = {"power": N_("最近 {d} 天只有 {n} 次有 Stryd 功率、心率可信的平路輕鬆跑（要 {k} 次）"),
         "speed": N_("最近 {d} 天只有 {n} 次心率可信的平路輕鬆跑（要 {k} 次）")}
R_SLOPE = {"power": N_("平路輕鬆跑的心率和功率看不出一起上升"),
           "speed": N_("平路輕鬆跑的心率和配速看不出一起變快")}
R_RANGE = N_("起始心率 {hr:.0f} bpm 在平路輕鬆跑的心率範圍（{lo:.0f}–{hi:.0f}）外")
R_NO_HR = N_("沒有起始心率")
WHY_FIT = {"power": N_("固定功率依據：最近 {n} 次平路輕鬆跑（{a}–{b}）的心率–功率關係，心率 {hr:.0f} bpm ≈ {w:.0f} W"),
           "speed": N_("固定配速依據：最近 {n} 次平路輕鬆跑（{a}–{b}）的心率–配速關係，心率 {hr:.0f} bpm ≈ {p} /km")}
WHY_CP = N_("固定功率依據：{why}，先用 CP × 0.75（推估）")
WHY_NO_PACE = N_("固定配速依據：{why}")
PACE_TARGET = N_("配速固定 {p} /km（±3%）")


def flat_segments(t, hr, power=None, speed=None, dist_km=None, elev=None, warm_s: float = FIT_WARM_S) -> dict:
    """{"pw": [[HR, W], …], "sp": [[HR, km/h], …]}: the one-minute flat segments of one run after
    `warm_s` (module notes above). No distance or elevation → none (the grade can't be checked)."""
    from backend.engine.climb_vam import grade_1s
    from backend.engine.workout_review import _at_grid
    out = {"pw": [], "sp": []}
    if hr is None or dist_km is None or elev is None:
        return out
    tt = np.asarray(t, dtype=float)
    if np.isfinite(tt).sum() < 2:
        return out
    grid = np.arange(np.nanmin(tt), np.nanmax(tt) + 1.0)
    h = _at_grid(tt, hr, grid)
    d = _at_grid(tt, dist_km, grid) * 1000.0
    g = grade_1s(d, _at_grid(tt, elev, grid))
    v = _at_grid(tt, speed, grid) if speed is not None else np.gradient(np.nan_to_num(d)) * 3.6
    p = _at_grid(tt, power, grid) if power is not None else np.full(len(grid), np.nan)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(g) & (np.abs(g) <= FIT_GRADE) & np.isfinite(v) & (v >= FIT_MIN_KMH) \
            & np.isfinite(h) & (h > 0)
    n = len(grid)
    for a in range(int(max(warm_s, FIT_LEAD_S)), n - FIT_SEG_S + 1, FIT_SEG_S):
        if not ok[a - FIT_LEAD_S:a + FIT_SEG_S].all():
            continue
        hh = float(h[a:a + FIT_SEG_S].mean())
        out["sp"].append([hh, float(v[a:a + FIT_SEG_S].mean())])
        pp = p[a:a + FIT_SEG_S]
        if np.isfinite(pp).all() and (pp > 0).all():
            out["pw"].append([hh, float(pp.mean())])
    return out


def _stryd(ds, w) -> bool:
    """This run's power is a Stryd's (power_source.usable without watch power — SP-950: watch power
    never counts, whatever 進階 says); True for a Dataset that cannot tell (test doubles)."""
    src = getattr(ds, "power_source", None)
    if not callable(src):
        return True
    try:
        from backend.engine import power_source as PS
        return bool(PS.usable(src(w), False))
    except Exception:                       # noqa: BLE001
        return False


def _run_segments(ds, w) -> dict:
    """flat_segments of one run, disk-memoised per activity (Dataset.cached_series)."""
    from backend.engine import workout_review as WR

    def compute():
        s = WR._samples(ds, w)
        if s is None:
            return {"pw": [], "sp": []}
        return flat_segments(s["t"], s.get("hr"), s.get("power"), s.get("speed"), s.get("dist"), s.get("elev"))
    cache = getattr(ds, "cached_series", None)
    return (cache(FIT_KEY, w, compute) if cache is not None else compute()) or {"pw": [], "sp": []}


def own_relation(ds, today: dt.date, what: str = "power", cap: Optional[float] = None) -> dict:
    """The athlete's own HR → power (`what` power) or HR → speed (speed) line from the recent flat
    easy road runs: {"ok", "what", "n" (runs), "segs", "from", "to" (ISO), "slope", "icept",
    "hr_lo", "hr_hi", "reason" (when not ok)}. A run counts with ≥ FIT_RUN_SEGS segments whose mean
    HR is ≤ `cap` + workout_review.AET_MARGIN (easy; no cap = any)."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    from backend.engine.wko5expr.dataset import date_to_day
    key = "pw" if what == "power" else "sp"
    tday = math.floor(date_to_day(today))
    runs = [w for w in sorted(ds.workouts, key=lambda x: x.day, reverse=True)
            if w.sport == "run" and tday - FIT_DAYS < math.floor(w.day) <= tday]
    used, pairs = [], []
    for w in runs:
        try:
            if category(w) != "road" or (what == "power" and not _stryd(ds, w)):
                continue
            if WR.run_hr_quality(ds, w) == "low":
                continue
            seg = [p for p in (_run_segments(ds, w).get(key) or []) if p[0] and p[1]]
        except Exception:                   # noqa: BLE001 — an unreadable run is skipped
            continue
        if len(seg) < FIT_RUN_SEGS:
            continue
        if cap and float(np.mean([p[0] for p in seg])) > float(cap) + WR.AET_MARGIN:
            continue
        used.append(WR._wdate(w))
        pairs += seg
        if len(used) >= FIT_MAX_RUNS:
            break
    WR._flush(ds)
    out = {"ok": False, "what": what, "n": len(used), "segs": len(pairs),
           "from": min(used).isoformat() if used else None, "to": max(used).isoformat() if used else None,
           "slope": None, "icept": None, "hr_lo": None, "hr_hi": None, "reason": None}
    if len(used) < FIT_MIN_RUNS:
        out["reason"] = _(R_FEW[what], d=FIT_DAYS, n=len(used), k=FIT_MIN_RUNS)
        return out
    x = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    if float(np.ptp(x)) <= 0:
        out["reason"] = _(R_SLOPE[what])
        return out
    b, a = np.polyfit(x, y, 1)
    if not b > 0:
        out["reason"] = _(R_SLOPE[what])
        return out
    out.update(ok=True, slope=float(b), icept=float(a), hr_lo=float(np.percentile(x, 5)),
               hr_hi=float(np.percentile(x, 95)))
    return out


def value_at(rel: Optional[dict], hr: Optional[float]) -> tuple[Optional[float], Optional[str]]:
    """(the relation's power / speed at `hr`, None) or (None, why not)."""
    if not rel:
        return None, None
    if not rel.get("ok"):
        return None, rel.get("reason")
    if not hr:
        return None, _(R_NO_HR)
    lo, hi = rel["hr_lo"] - FIT_EXTRAP_BPM, rel["hr_hi"] + FIT_EXTRAP_BPM
    if not lo <= hr <= hi:
        return None, _(R_RANGE, hr=hr, lo=rel["hr_lo"], hi=rel["hr_hi"])
    v = rel["icept"] + rel["slope"] * float(hr)
    return (v, None) if v > 0 else (None, _(R_SLOPE[rel["what"]]))


def _md(iso: Optional[str]) -> str:
    d = dt.date.fromisoformat(iso) if iso else None
    return f"{d.month}/{d.day}" if d else ""


def start_target(ds, today: dt.date, hr0: Optional[float], basis: str = "power", cp: Optional[float] = None,
                 cap: Optional[float] = None) -> dict:
    """The fixed intensity of a UA / Evoke test (and 徐國峰's when the E pace is missing):
    {"basis": power | pace, "value" (W | s/km, None = unknown), "fit" (from own_relation),
     "target" (the session's 目標 part, None = the caller's fallback), "why" (a 說明 line), "n", "from", "to"}.
    power (① — power in effect): the HR → power line at `hr0`, else CP × 0.75 (推估, why says why);
    pace (② / ③, or a pace protocol): the HR → speed line at `hr0` as a pace, else nothing (the caller
    falls back to the E pace / the talk test, xu_target) with the reason."""
    what = "power" if basis == "power" else "speed"
    rel = None
    if ds is not None:
        try:
            rel = own_relation(ds, today, what, cap)
        except Exception:                   # noqa: BLE001 — the fallback below
            rel = None
    v, why_not = value_at(rel, hr0)
    out = {"basis": basis, "value": None, "fit": False, "target": None, "why": None,
           "n": (rel or {}).get("n"), "from": (rel or {}).get("from"), "to": (rel or {}).get("to")}
    if v is not None:
        a, b = _md(rel["from"]), _md(rel["to"])
        if what == "power":
            out.update(value=float(v), fit=True, target=_("固定功率 {w:.0f} W（±3%）", w=v),
                       why=_(WHY_FIT[what], n=rel["n"], a=a, b=b, hr=hr0, w=v))
        else:
            pace = 3600.0 / v
            from backend.engine.e_pace import fmt_pace
            out.update(value=pace, fit=True, target=_(PACE_TARGET, p=fmt_pace(pace)),
                       why=_(WHY_FIT[what], n=rel["n"], a=a, b=b, hr=hr0, p=fmt_pace(pace)))
        return out
    why_not = why_not or _(R_FEW[what], d=FIT_DAYS, n=0, k=FIT_MIN_RUNS)
    if what == "power":
        p0 = start_power(cp)
        if p0:
            out.update(value=p0, target=_("固定功率 {w:.0f} W（±3%）", w=p0), why=_(WHY_CP, why=why_not))
        return out
    out["why"] = _(WHY_NO_PACE, why=why_not)
    return out


def due(today: dt.date, kind: Optional[str], base_start: Optional[str], reason,
        last_test: Optional[str]) -> bool:
    """Suggest the AeT test this week? Base phase, a reason
    (quality_gate.aet_test_reason: no data for ~6 weeks, the aggregate's SE
    too large, a shift, the estimate moved — B3 and the Z5 lifecycle), and no
    test in the last RECENT_DAYS (推估 spacing). No fixed cadence any more.
    `reason` may be the reason dict or any truthy value; a date string (the
    old signature) is not a reason."""
    if (kind or "base") != "base":
        return False
    if not reason or isinstance(reason, str):
        return False
    if last_test and (today - dt.date.fromisoformat(last_test)).days < RECENT_DAYS:
        return False
    return True


def variant_for(cap_weekday: Optional[int]) -> str:
    """UA lengths only: standard (80′) without a weekday cap or with one ≥ 80
    min; short (UA's 40′ minimum, 50′ in all) under a smaller cap."""
    return "short" if cap_weekday is not None and cap_weekday < STD_MIN else "standard"


def is_short(s: dict) -> bool:
    """A planned AeT test of the short (≤ 50-min) length."""
    p = protocol_of_title(s.get("title"))
    if p:
        return sum(PROTOCOLS[p][k] for k in ("warm", "main", "cool")) <= SHORT_MIN
    m = AT_TITLE_LEN.search(s.get("title") or "")
    return int(m.group(1)) < 60 if m else (s.get("minutes") or STD_MIN) <= SHORT_MIN


def protocol_tip(key: str) -> str:
    """The 課表偏好 hover: duration, terrain, what is held, how it is judged, source."""
    if key == "auto":
        # SP-840: the joins too (the settings page's AeT ? shows this tip)
        return (_("自動：標準版＝{label}（放在週末長跑日，取代那次長跑）", label=_(PROTOCOLS[STANDARD]["label"])) + _("；")
                + _("長跑日上限 < 90 分放不下時改用備案 {label}（平日）", label=_(PROTOCOLS[BACKUP]["label"])) + _("。")
                + _("選標準版的理由：90 分鐘是徐國峰公開的測試長度（部落格 2016-12），測得太短看不出後段心率飄移") + _("；")
                + _("UA 接受 40 分、Evoke 60 分、Friel 1–2 小時") + _("；") + _("沒有任何長度有同儕審查的驗證") + _("。")).strip()
    p = PROTOCOLS[key]
    total = p["warm"] + p["main"] + p["cool"]
    head = (_("{label}：共 {total} 分（暖身 {warm}＋測試 {main}＋緩和 {cool} 分）", label=_(p["label"]), total=total,
              warm=p["warm"], main=p["main"], cool=p["cool"]) if p["cool"] else
            _("{label}：共 {total} 分（暖身 {warm}＋測試 {main} 分）", label=_(p["label"]), total=total, warm=p["warm"],
              main=p["main"]))
    rule, src = _parts(p["rule"]), _parts(p["source"])
    return _("；").join([head, _("場地：{terrain}", terrain=_(p["terrain"])), _("固定：{hold}", hold=_(p["hold"])),
                     _("判讀：{rule}", rule=_(rule[0]))] + [_(x) for x in rule[1:]]
                    + [_("來源：{src}", src=_(src[0]))] + [_(x) for x in src[1:]])


def session(th: dict, hr0: Optional[float], p0: Optional[float], cap_weekday: Optional[int] = None,
            protocol: Optional[str] = None, cap_long: Optional[int] = None, start: Optional[dict] = None,
            hr_why: Optional[str] = None) -> dict:
    """The schedulable session (kind test, id test_aet) of the chosen protocol
    (課表偏好 aet_test_protocol; resolve_protocol). No protocol given = the old
    UA behaviour by the weekday cap (variant_for). The detail keeps 「暖身 N
    分」「測試 N 分」「緩和 N 分」 for the COROS step builder.
    SP-949: `start` (start_target) is the fixed intensity — power (①: the athlete's own HR → power at
    the start HR, else CP × 0.75) or pace (② / ③: HR → pace, else the E pace / the talk test); without
    it the old `p0` watts. `hr_why`: the start HR's line when chained to the last test."""
    if protocol is None:
        key = "ua40" if variant_for(cap_weekday) == "short" else "ua60"
    else:
        key = resolve_protocol(protocol, cap_weekday, cap_long)
    p = PROTOCOLS[key]
    warm, main, cool = p["warm"], p["main"], p["cool"]
    tgt = []
    xu = None
    whys = []                               # SP-949: the fixed intensity's / the start HR's 依據 lines
    # SP-905: the detail is msgids joined with 「；」「。」 (an English page reads each back on its own); the
    # reason (why) and the place are one msgid, 「{why}：…」
    if key == "xu90":
        fit = start if start and start.get("basis") == "pace" and start.get("fit") else None
        xu = xu_target(th if isinstance(th, dict) else {}, fit)
        tgt += [xu["text"], _("不設心率上限：心率升高也不要放慢（放慢會讓飄移偏小）")]
        why = _("自動：標準版徐國峰 90 分鐘（取代週末那次長跑）") if protocol == "auto" else _("徐國峰 90 分鐘")
        body = (_("暖身 {warm} 分（心率不超過輕鬆跑上限），接著測試 {main} 分：平坦路段、配速固定不要調、心率讓它自己變，"
                  "記下第 10 分鐘和第 90 分鐘的心率", warm=warm, main=main) + "；"
                + _("補給每次停不超過 30 秒") + "；" + _("(第 90 分 − 第 10 分) ÷ 第 10 分 < 10% 有氧基礎夠") + "。")
        place = _("{why}：平坦路段（河濱）、不要山路", why=why) + "；" + xu["why"]
    else:
        pace = bool(start and start.get("basis") == "pace") and key != "friel"
        if pace:
            # ② / ③ (SP-949): a fixed pace — the athlete's own HR → pace at the start HR, else the E pace
            # / the talk test (xu_target without a CP: no watts without power in effect)
            if start.get("fit"):
                tgt.append(start["target"])
                whys.append(start["why"])
            else:
                e = xu_target({k: v for k, v in (th if isinstance(th, dict) else {}).items() if k != "cp"})
                tgt.append(e["text"])
                whys += [x for x in (start.get("why"), e["why"].rstrip("。")) if x]
        elif start and start.get("basis") == "power" and start.get("target"):
            tgt.append(start["target"])
            if start.get("why"):
                whys.append(start["why"])
        elif p0:
            tgt.append(_("固定功率 {w:.0f} W（±3%）", w=p0))
        if hr0:
            tgt.append(_("心率從 {hr:.0f} 附近開始", hr=hr0))
        if hr_why and key != "friel":
            whys.append(hr_why)
        if key == "ua40" and protocol in (None, "auto", "ua60", "xu90"):
            if cap_weekday is None:
                why = _("長跑日放不下 90 分 → 備案 UA 最短 40 分版本")
            elif cap_weekday < SHORT_MIN:
                why = _("平日上限 {cap} 分 → 用 UA 最短 40 分版本（還是要 50 分：UA 不建議短於 40 分，不受上限）",
                        cap=cap_weekday)
            else:
                why = _("平日上限 {cap} 分 → 用 UA 最短 40 分版本", cap=cap_weekday)
        elif key == "ua60":
            why = _("沒有平日時間上限 → UA 標準版 80 分") if cap_weekday is None else \
                _("平日上限 {cap} 分放得下 → UA 標準版 80 分", cap=cap_weekday)
        else:
            why = _(p["label"])
        held = (_("心率在 AeT 附近穩定跑") if key == "friel" else
                (_("固定配速不要調（至少 40 分）") if pace else _("固定功率不要調（至少 40 分）")) if key == "ua60"
                else _("固定配速不要調") if pace else _("固定功率不要調"))
        body = (_("暖身 {warm} 分到開始流汗（心率不超過起始心率），接著測試 {main} 分{held}", warm=warm, main=main,
                  held=held) + "；" + _("中途不停") + "；"
                + (_("緩和 {n} 分", n=cool) if cool else _("緩和可省略（0–5 分慢跑）")) + "。")
        place = _("{why}：冷氣房跑步機 2–3%＋電扇（首選），或平路環線，不要山路", why=why) + "；"
    # the abort rule is Evoke's; Evoke says "a slower pace another day" — the 5 bpm is ours (推估)
    early = (_("主課第 10 分鐘心率已經比起始高 10 下還在升 → 起始太高，停掉改天用較慢的配速再測（Evoke）") + "；"
             + _("起始心率約降 {n} bpm（推估）", n=LOWER_BPM)
             if p["judge"] in ("ua", "evoke") else "")
    basis = "".join(w + "。" for w in whys)
    return {"id": "test_aet", "kind": "test", "protocol": PROTOCOL, "title": p["title"], "minutes": warm + main + cool,
            "target": "；".join(tgt) or _("固定功率（±3%），不要調"),
            "detail": place + body + basis + _(HEAT_TEXT) + "；" + _("記下溫度") + "。" + early,
            "source": _joined(p["source"] if key != "ua60" and key != "ua40" else SRC),
            "tss": (warm + main + cool) / 60 * 50,
            **({"xu_basis": xu["basis"]} if xu else {})}


# ---- the 90-minute test's intensity (SP-274; lthr-low-confidence-testing.md §2.1, §4.2, §6.1 第 1 點) ----
# 徐國峰 holds the E pace and lets the HR drift; an HR cap would hold the drift down (a false pass),
# and without a measured AeT the cap was 0.89 × LTHR. Order (owner 2026-10-06): the E pace of a
# race the athlete entered / confirmed (engine/e_pace.py) → 75–80 % of a tested CP (Palladino 1C
# 「EZ aerobic」, zones.py) → no target, the talk test. Never an HR cap on the main block.
XU_PACE_BAND = 0.03             # 推估: ± 3 % around the middle of the E range (a fixed pace, like ± 3 % power)
XU_CP = (0.75, 0.80)            # Palladino 1C 75–80 % CP
PACE_RE = re.compile(r"(\d+):(\d\d)\s*[–-]\s*(\d+):(\d\d)\s*/km")     # the stored target's pace range
WATTS_RE = re.compile(r"(\d+)\s*[–-]\s*(\d+)\s*W\b")                     # …or power range


CP_TEST_FIT_DAYS = 90           # = racepower.athlete.CP_WINDOW_DAYS: the PD fit's mean-max window


def cp_tested(plan, day: dt.date, cp_test_date: Optional[str] = None) -> bool:
    """The CP in effect on `day` counts as tested for the 90-minute test (owner 2026-10-06).
    With a plan row with a CP on or before `day`: the latest one is not marked as typed by hand
    (planning.Threshold.cp_manual, set by 設定 when the CP is typed) — a row 「套用這次的 CP」 wrote
    before SP-468 (cp_method) and a legacy row (CP, no cp_method) both count. Without one the CP
    in effect is WKO5's mFTP (the Stryd PD fit): SP-468 (the user 2026-10-13, 「完全比照 WKO5 用
    自動計算的」) made it a CP test's own result — nothing is applied any more — so it counts when
    a CP test (`cp_test_date`, workout_review.latest_cp_test) is inside the fit's 90-day window;
    a fit without a test still does not (owner 2026-10-06)."""
    rows = sorted((t for t in getattr(plan, "thresholds", None) or []
                   if t.cp is not None and str(t.date)[:10] <= day.isoformat()), key=lambda t: t.date)
    if rows:
        return not getattr(rows[-1], "cp_manual", None)
    if not cp_test_date:
        return False
    try:
        d = dt.date.fromisoformat(str(cp_test_date)[:10])
    except ValueError:
        return False
    return day - dt.timedelta(days=CP_TEST_FIT_DAYS) < d <= day


def xu_target(th: dict, fit: Optional[dict] = None) -> dict:
    """{"basis": pace | power | fit | talk, "lo", "hi" (s/km or W; None for talk), "text" (the
    target, its numbers parsed back by the step builders: main_target), "name" (the
    main step), "why" (the detail's line)}. th: the week plan's thresholds with "e_pace"
    (e_pace.current) and "cp_measured" (cp_tested). A race older than e_pace.STALE_DAYS is not
    used (owner 2026-10-06): the test falls back to the CP / the talk test. SP-949: `fit` (a pace
    start_target from the athlete's own HR → pace) comes before the talk test."""
    from backend.engine import e_pace as EP
    e = th.get("e_pace") if isinstance(th.get("e_pace"), dict) else None
    if e and e.get("e_fast") and e.get("e_slow") and not e.get("stale"):
        mid = (float(e["e_fast"]) + float(e["e_slow"])) / 2.0
        lo, hi = round(mid * (1 - XU_PACE_BAND)), round(mid * (1 + XU_PACE_BAND))
        return {"basis": "pace", "lo": lo, "hi": hi,
                "text": _("配速固定 {lo}–{hi} /km（E 配速 {mid} ±3%，推估），不要調",
                          lo=EP.fmt_pace(lo), hi=EP.fmt_pace(hi), mid=EP.fmt_pace(mid)),
                "name": _("固定 E 配速，不要調"),
                "why": _("強度：{e}", e=EP.label(e)) + "。"}
    cp = th.get("cp")
    if cp and th.get("cp_measured"):
        lo, hi = round(XU_CP[0] * float(cp)), round(XU_CP[1] * float(cp))
        return {"basis": "power", "lo": lo, "hi": hi,
                "text": _("功率固定 {lo}–{hi} W（75–80% CP，Palladino 1C），不要調", lo=lo, hi=hi),
                "name": _("固定功率 75–80% CP，不要調"),
                "why": _("強度：沒有 180 天內的比賽成績可以算 E 配速，用實測 CP {cp:.0f} W 的 75–80%（Palladino 1C）",
                         cp=float(cp)) + "。" + _("設定頁填一場比賽成績就會改用 E 配速") + "。"}
    if fit and fit.get("fit") and fit.get("value") and fit.get("target"):
        pc = float(fit["value"])
        return {"basis": "fit", "lo": round(pc * (1 - FIT_BAND)), "hi": round(pc * (1 + FIT_BAND)),
                "text": fit["target"], "name": _("固定配速，不要調"),
                "why": fit["why"] + "。" + _("設定頁填一場比賽成績就會改用 E 配速") + "。"}
    return {"basis": "talk", "lo": None, "hi": None,
            "text": _("能講完整句子的配速，固定不要調"),
            "name": _("能講完整句子的配速，固定不要調"),
            "why": _("強度：沒有 180 天內的比賽成績可以算 E 配速，也沒有 CP 測試的結果：前 10 分鐘找能講完整句子的最快配速，"
                     "之後就固定這個配速（講話測試，Foster 2008）") + "。" + _("設定頁填一場比賽成績就會改用 E 配速") + "。"}


FIXED_PACE_RE = re.compile(r"配速固定\s*(\d+):(\d\d)\s*/km")                 # SP-949: 「配速固定 8:40 /km（±3%）」
FIXED_W_RE = re.compile(r"固定功率\s*(\d+)\s*W")                               # 「固定功率 140 W（±3%）」


def main_target(text: str) -> Optional[tuple]:
    """The main block's target parsed from a stored AeT test's target text: ("pace", fast, slow)
    s/km — a range, or SP-949's one pace ± 3 % —, ("power", lo, hi) W — a range, or a fixed power
    ± 3 % —, or None (the talk test, or an old row)."""
    m = PACE_RE.search(text or "")
    if m:
        a, b = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
        return ("pace", min(a, b), max(a, b))
    m = FIXED_PACE_RE.search(text or "")
    if m:
        pc = int(m.group(1)) * 60 + int(m.group(2))
        return ("pace", round(pc * (1 - FIT_BAND)), round(pc * (1 + FIT_BAND)))
    m = WATTS_RE.search(text or "")
    if m:
        return ("power", int(m.group(1)), int(m.group(2)))
    m = FIXED_W_RE.search(text or "")
    if m:
        w = int(m.group(1))
        return ("power", round(w * (1 - FIT_BAND)), round(w * (1 + FIT_BAND)))
    return None


def xu_main_target(text: str) -> Optional[tuple]:
    """The 90-minute test's main block (main_target)."""
    return main_target(text)


def main_name(text: str) -> str:
    """The UA / Evoke main step's name (SP-949): fixed power, fixed pace, or the talk test."""
    t = main_target(text)
    if t is None:
        return _("能講完整句子的配速，固定不要調") if "講完整句子" in (text or "") else _("固定功率，不要調")
    return _("固定配速，不要調") if t[0] == "pace" else _("固定功率，不要調")


def xu_main_name(text: str) -> str:
    """The main step's name for xu_main_target's result."""
    t = xu_main_target(text)
    if t is None:
        return _("能講完整句子的配速，固定不要調")
    if t[0] == "pace":
        return _("固定 E 配速，不要調") if PACE_RE.search(text or "") else _("固定配速，不要調")
    return _("固定功率 75–80% CP，不要調")


def pick_day_xu(avail: list, long_wd: int, cap_weekday: Optional[int] = None) -> Optional[dt.date]:
    """The 徐國峰 test is the weekend LSD: the long weekday first, then the
    other weekend day, then (only without a weekday cap, or one ≥ 90) any day."""
    order = [d for d in avail if d.weekday() == long_wd] + \
            [d for d in avail if d.weekday() in (5, 6) and d.weekday() != long_wd]
    if cap_weekday is None or cap_weekday >= XU_MIN:
        order += [d for d in avail if d.weekday() < 5]
    return order[0] if order else None


# 課表偏好 plan.prefs.aet_test_days: the athlete trail-runs on weekends, so the
# test defaults to a weekday (Mon–Fri); "any" = the interval placement.
TEST_DAYS = {"weekday": (0, 1, 2, 3, 4)}
DAY_ORDER = (1, 2, 3, 0, 4, 5, 6)      # Tue, Wed, Thu, Mon, Fri — plan_prefs.QUALITY_ORDER
NOTE_NONE = N_("AeT 測試只排平日，本週平日沒有可練的日子：這週先不測")


def test_days(prefs) -> Optional[tuple]:
    """The weekdays the AeT test may go on (None = no restriction: the caller's
    interval rule). Default Mon–Fri, also without stored preferences."""
    return TEST_DAYS.get(getattr(prefs, "aet_test_days", None) or "weekday")


def pick_day(avail: list, long_day: Optional[dt.date], hard_days=(), days=TEST_DAYS["weekday"],
             weekend_ok: bool = False) -> dict:
    """Where the AeT test goes in a week whose long run (`long_day`, may be
    None) is already placed. `avail` = the free allowed days left; `days` =
    test_days(); `weekend_ok` = the standard 80-min test (no weekday cap) may
    fall back to a weekend day — never the long run's, which is not in
    `avail`. Returns {"day", "note"}; candidates in DAY_ORDER (Tue first):

      1. a preferred day ≥ 2 days from the long run and from other hard
         days — so the day before is a rest / easy day, not the long run;
      2. else a preferred day that is not the day after the long run (a
         tired test drifts more) and not next to another hard day;
      3. else any preferred day left;
      4. else (weekend_ok) the same three steps on the weekend days;
      5. none: `day` None — not placed this week (`note`).
    推估 (no source gives a placement rule; the reasons are UA's: test
    rested, flat, ≥ 40 min — docs/research/aerobic-base-readiness.md §6.4)."""
    def best(pool):
        cands = sorted((d for d in avail if d.weekday() in pool), key=lambda d: (DAY_ORDER.index(d.weekday()), d))
        far = [d for d in cands if (long_day is None or abs((d - long_day).days) >= 2)
               and all(abs((d - h).days) >= 2 for h in hard_days)]
        near = [d for d in cands if (long_day is None or d != long_day + dt.timedelta(days=1))
                and all(abs((d - h).days) >= 1 for h in hard_days)]
        return (far or near or cands or [None])[0]
    pick = best(days)
    if pick is None and weekend_ok:
        pick = best(tuple(d for d in range(7) if d not in days))
    return {"day": pick, "note": None if pick is not None else M_(NOTE_NONE)}


def is_aet_session(s: dict) -> bool:
    """A planned / stored session that is the AeT test: protocol or kind
    `aet`, id / gen_key `test_aet` (rows stored before the protocol field
    was set), or an AeT title (a custom session)."""
    return (s.get("protocol") == PROTOCOL or s.get("kind") == PROTOCOL or s.get("id") == "test_aet"
            or s.get("gen_key") == "test_aet" or "AeT" in (s.get("title") or ""))
