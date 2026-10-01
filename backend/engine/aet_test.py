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
  of the block and rising → started too high, stop, retest 5 bpm lower.

Analysis (`analyze`): the main block is the time after the warm-up (15′ for
the standard test, 10′ for the short one — `warm_for`, from the title, else
the length), up to 60′ of it, cool-down trimmed; Pw:HR over its halves (Pa:HR
without power) — on the short test the first 20′ vs the last 20′. Both are
the strict tier (≥ 40′ after the warm-up). UA's bands: < 3.5 % → below AeT
(next time start 5 bpm higher), 3.5–5 % → the first-half HR is the AeT, > 5 %
→ started above AeT (5 bpm lower).

The same three checks as workout_review.drift_of (the daily runs):
  * the 40-min floor counts *after* the warm-up;
  * a fast finish (last 10 % of the block > 5 % above the rest) is refused — 自訂;
  * heat: a mean temperature > 25 °C is refused — 自訂. The route_weather
    archive's air temperature when it has the activity, else the watch's
    (workout_review.activity_temp); the reason says which.

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
UA_SLACK_S = 30                 # 自組: a few lost samples (GPS / Stryd dropouts) don't fail a 40′ test
BAND_LOW, BAND_HIGH = 0.035, 0.05
FAST_FINISH = 0.05              # 自訂
HEAT_C = 25.0                   # 自訂 (徐國峰's condition applied to the analysis)
MAX_STOPPED = 0.05
MAX_CV = 0.15                   # threshold_estimate.AET_MAX_POWER_CV
START_BELOW = 5.0               # 自訂: 0.89 × LTHR − 5 as the starting HR without an estimate
POWER_OF_CP = 0.75              # 自訂: starting power when nothing better is known (Palladino easy ≤ 80 % CP)
EVERY_WEEKS = 5                 # 自訂: suggest at most once every 5 base weeks (the doc: 4–6)
RECENT_DAYS = 28                # a test in the last 4 weeks → don't suggest another
STALE_DAYS = 42                 # plan AeT older than 6 weeks → due again (i_testing's 4–6 weeks)

SRC = ("Uphill Athlete 心率飄移測試（https://uphillathlete.com/aerobic-training/heart-rate-drift/："
       "\"If you only have 40 minutes, do that.\"、不建議短於 40 分；前 20 分對後 20 分，< 3.5% / 3.5–5% / > 5%）；"
       "Evoke 提早中止（https://evokeendurance.com/resources/our-latest-thinking-on-aerobic-assessment-for-the-mountain-athlete/）")
TITLES = {"standard": "AeT 飄移測試 60 分", "short": "AeT 飄移測試 40 分"}   # the main block's length
TITLE = TITLES["standard"]
AT_TITLE_LEN = re.compile(r"飄移測試\s*(\d+)\s*分")
PROTOCOL = "aet"                # the stored session's `protocol` (not a cp_protocols protocol)
BAND_LABEL = {"below": "低於 AeT", "at": "就是 AeT", "above": "高於 AeT"}


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def band_of(drift: Optional[float]) -> Optional[str]:
    if drift is None:
        return None
    if drift < BAND_LOW:
        return "below"
    if drift <= BAND_HIGH:
        return "at"
    return "above"


def analyze(t, hr, speed=None, power=None, temp=None, climb_m_per_km: Optional[float] = None,
            trail: bool = False, warm_s: float = WARM_S, main_s: float = MAIN_MAX_S,
            temp_c: Optional[float] = None, temp_src: Optional[str] = None) -> dict:
    """UA drift test on one recording. `ok` False with `reason` when it isn't a
    fair test. `temp_c` (with `temp_src`, route_weather / watch) overrides the
    mean of the `temp` channel over the block."""
    from backend.engine.workout_review import MAX_DT, STOP_KMH, _arr, _grid1, _hms
    out = {"ok": False, "reason": "", "drift": None, "pw_drift": None, "pa_drift": None, "hr1": None, "hr2": None,
           "main_s": None, "band": None, "basis": None}
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
        p30 = np.convolve(ow, np.ones(30) / 30, "valid")
        if p30.mean() > 0 and p30.std() / p30.mean() > MAX_CV:
            out["reason"] = f"功率起伏大（變異 {p30.std() / p30.mean() * 100:.0f}% > 15%）：要固定功率"
            return out
    k = max(1, int(len(ow) * 0.1))
    if ow[:-k].mean() > 0 and ow[-k:].mean() > (1 + FAST_FINISH) * ow[:-k].mean():
        out["reason"] = f"最後 10% 比前段快 {(ow[-k:].mean() / ow[:-k].mean() - 1) * 100:.0f}%（> 5%）：快速結尾會讓飄移看起來比較小"
        return out
    tp = series.get("temp")
    if temp_c is None and tp is not None and np.isfinite(tp[m]).any():
        temp_c, temp_src = float(np.nanmean(tp[m])), "watch"
    out["temp_c"], out["temp_src"] = temp_c, temp_src if temp_c is not None else None
    if temp_c is not None and temp_c > HEAT_C:
        from backend.engine.workout_review import TEMP_SRC_LABEL
        out["reason"] = (f"{TEMP_SRC_LABEL.get(temp_src, '平均氣溫')} {temp_c:.0f} °C（> 25 °C）："
                         "熱會讓心率飄，換涼一點的時段再測")
        return out
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
    out.update(ok=True, drift=d, hr1=h1, hr2=h2, band=band_of(d), basis=basis)
    return out


def lines(r: dict, aet_now: Optional[float] = None) -> list[str]:
    """The review card's verdict (UA's three bands)."""
    if not r.get("ok"):
        return [r.get("reason") or "不是有效的 AeT 測試"]
    d, h1 = r["drift"], r["hr1"]
    head = f"{r['basis']} 飄移 {d * 100:.1f}%（暖身後 {r['main_s'] / 60:.0f} 分）"
    now = f"（目前 {aet_now:.0f}）" if aet_now else ""
    if r["band"] == "below":
        return [f"{head} < 3.5%：前半心率 {h1:.0f} bpm 還在 AeT 以下", f"下次起始心率 +5 bpm（約 {h1 + 5:.0f}）再測一次{now}"]
    if r["band"] == "at":
        return [f"{head}，在 3.5–5%：AeT = 前半平均心率 {h1:.0f} bpm{now}", "可以按「套用這次的 AeT」寫進門檻"]
    return [f"{head} > 5%：起始心率 {h1:.0f} bpm 高於 AeT", f"下次起始心率 −5 bpm（約 {h1 - 5:.0f}）再測一次{now}"]


def warm_for(title: str, duration_s: float) -> float:
    """The warm-up to cut: from the test's title (「AeT 飄移測試 60 分」 → the
    standard 15′, 「… 40 分」 → the short 10′), else 15′ when the run is long
    enough for 40′ after it, else 10′ (自組)."""
    m = AT_TITLE_LEN.search(title or "")
    if m:
        return WARM_STD_S if int(m.group(1)) >= 60 else WARM_S
    return WARM_STD_S if duration_s >= WARM_STD_S + UA_MIN_S else WARM_S


def analyze_workout(ds, w, m: Optional[dict] = None) -> Optional[dict]:
    from backend.engine import workout_review as WR
    s = WR._samples(ds, w)
    if s is None:
        return None
    m = m if m is not None else (WR.measure(ds, w) or {})
    temp = ds.channel(w.idx, "temperature")
    tc, src = WR.activity_temp(ds, w, None)       # the archive only; the watch is averaged over the block
    sched = WR.scheduled_aet_test(ds, w) or {}
    title = WR._title(w) if AT_TITLE_LEN.search(WR._title(w) or "") else (sched.get("title") or WR._title(w))
    warm = warm_for(title, _f(w.metrics.get("duration")) or float(s["t"][-1] - s["t"][0]))
    return {**analyze(s["t"], s["hr"], s["speed"], s["power"], temp, m.get("climb_m_per_km"),
                      trail="runningtrail" in w.tags, warm_s=warm, temp_c=tc, temp_src=src), "warm_s": warm}


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
        sug = round(r["hr1"]) if r.get("ok") and r["band"] == "at" else None
        found = {"idx": w.idx, "date": WR._wdate(w).isoformat(), **{k: r.get(k) for k in (
            "ok", "reason", "hr1", "drift", "pw_drift", "pa_drift", "band", "basis", "main_s")},
            "aethr_suggest": sug, "aethr_now": now, "delta": (sug - now) if sug is not None and now else None}
    WR._flush(ds)
    return found


def apply_body(t: dict) -> Optional[dict]:
    """POST /api/v1/plan/thresholds/apply-estimate body for a test in band "at"."""
    if not t or t.get("aethr_suggest") is None:
        return None
    return {"aethr": t["aethr_suggest"], "date": t["date"],
            "note": f"AeT 飄移測試 {t['date']}：{t.get('basis') or 'Pw:HR'} {t['drift'] * 100:.1f}%"}


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
    return POWER_OF_CP * cp if cp else None


def due(today: dt.date, kind: Optional[str], base_start: Optional[str], aet_date: Optional[str],
        last_test: Optional[str]) -> bool:
    """Suggest the AeT test this week? Base phase; no measured AeT or one older
    than 6 weeks; no test in the last 4 weeks; and at most once every
    EVERY_WEEKS base weeks (week 2, 7, 12… of the base phase — 自訂)."""
    if (kind or "base") != "base":
        return False
    if aet_date and (today - dt.date.fromisoformat(aet_date)).days <= STALE_DAYS:
        return False
    if last_test and (today - dt.date.fromisoformat(last_test)).days < RECENT_DAYS:
        return False
    monday = today - dt.timedelta(days=today.weekday())
    if base_start:
        b = dt.date.fromisoformat(base_start)
        k = (monday - (b - dt.timedelta(days=b.weekday()))).days // 7
    else:
        k = monday.toordinal() // 7
    return k % EVERY_WEEKS == 1


def variant_for(cap_weekday: Optional[int]) -> str:
    """standard (80′) without a weekday cap or with one ≥ 80 min; short (UA's
    40′ minimum, 50′ in all) when the cap can't fit the standard test."""
    return "short" if cap_weekday is not None and cap_weekday < STD_MIN else "standard"


def is_short(s: dict) -> bool:
    """A planned AeT test of the short (50-min) length."""
    m = AT_TITLE_LEN.search(s.get("title") or "")
    return int(m.group(1)) < 60 if m else (s.get("minutes") or STD_MIN) <= SHORT_MIN


def session(th: dict, hr0: Optional[float], p0: Optional[float], cap_weekday: Optional[int] = None) -> dict:
    """The schedulable session (kind test, id test_aet), its length by the 課表偏好
    weekday cap (variant_for): standard 15′ + 60′ + 5′ = 80 min, or UA's minimum
    10′ + 40′ (cool-down optional) = 50 min — the detail says which and why. It
    keeps 「暖身 N 分」「測試 N 分」「緩和 N 分」 for the COROS step builder."""
    v = variant_for(cap_weekday)
    warm, main, cool = VARIANTS[v]
    tgt = []
    if p0:
        tgt.append(f"固定功率 {p0:.0f} W（±3%）")
    if hr0:
        tgt.append(f"心率從 {hr0:.0f} 附近開始")
    if v == "short":
        why = (f"平日上限 {cap_weekday} 分 → 用 UA 最短 40 分版本"
               + ("（還是要 50 分：UA 不建議短於 40 分，不受上限）" if cap_weekday < SHORT_MIN else "") + "：")
        body = (f"暖身 {warm} 分到開始流汗（心率不超過起始心率），接著測試 {main} 分固定功率不要調；中途不停；"
                "緩和可省略（0–5 分慢跑）。")
    else:
        why = "沒有平日時間上限 → 標準版 80 分：" if cap_weekday is None else \
            f"平日上限 {cap_weekday} 分放得下 → 標準版 80 分："
        body = (f"暖身 {warm} 分到開始流汗（心率不超過起始心率），接著測試 {main} 分固定功率不要調"
                f"（至少 40 分）；中途不停；緩和 {cool} 分。")
    return {"id": "test_aet", "kind": "test", "protocol": PROTOCOL, "title": TITLES[v], "minutes": warm + main + cool,
            "target": "；".join(tgt) or "固定功率（±3%），不要調",
            "detail": why + "冷氣房跑步機 2–3%＋電扇（首選），或清晨平路環線，不要山路；" + body +
                      "記下溫度；熱的時候結果會偏高。"
                      "主課第 10 分鐘心率已經比起始高 10 下還在升 → 起始太高，停掉改天降 5 bpm 再測（Evoke）",
            "source": SRC, "tss": (warm + main + cool) / 60 * 50}


# 課表偏好 plan.prefs.aet_test_days: the athlete trail-runs on weekends, so the
# test defaults to a weekday (Mon–Fri); "any" = the interval placement.
TEST_DAYS = {"weekday": (0, 1, 2, 3, 4)}
DAY_ORDER = (1, 2, 3, 0, 4, 5, 6)      # Tue, Wed, Thu, Mon, Fri — plan_prefs.QUALITY_ORDER
NOTE_NONE = "AeT 測試只排平日，本週平日沒有可練的日子：這週先不測"


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
    自組 (no source gives a placement rule; the reasons are UA's: test
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
    return {"day": pick, "note": None if pick is not None else NOTE_NONE}


def is_aet_session(s: dict) -> bool:
    """A planned / stored session that is the AeT test: protocol or kind
    `aet`, id / gen_key `test_aet` (rows stored before the protocol field
    was set), or an AeT title (a custom session)."""
    return (s.get("protocol") == PROTOCOL or s.get("kind") == PROTOCOL or s.get("id") == "test_aet"
            or s.get("gen_key") == "test_aet" or "AeT" in (s.get("title") or ""))
