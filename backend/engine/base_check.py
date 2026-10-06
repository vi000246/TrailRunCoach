"""
有氧基礎確認與 Zone 5 的生命週期 — the aerobic-base checks
(aerobic-base-readiness.md §1.1, §4).

Sources: 徐國峰's public 90-minute test (blog 2016-12,
http://rocky549.blogspot.com/2016/12/rq.html; 《跑者都該懂的跑步數據》); the
Zone 3-first rule, the Zone 5 limits and the 25 °C line are 台灣教練. Our
proxies / interpretations are 推估.

xu_run(ds, w)
    徐國峰's 90-min test on one run: ≥ 90 min, flat, every stop ≤ 30 s, HR
    in Z1, drift = (HR@90′ − HR@10′) / HR@10′ < 10 % (his own definition —
    minute 10 vs minute 90, ±1 min means; NOT drift_of's half-vs-half
    Pa:HR). The test can be the weekend long run: any qualifying run counts,
    scheduled or not. The ≤ 25 °C condition (台灣教練) is advice in the session text, not a
    refusal (heat bands): the run carries its temperature band; a pass in
    heat counts (heat only inflates the drift — conservative), a fail in
    heat is marked 「熱環境，結果可能偏高」.
z5_status(ds, today, …)
    Zone 5 opens only once the base is confirmed by a MEASURED AeT (SP-39, 2026-10-04;
    coach-schools-zones-periodization.md R3): a measured AeT and a measured LTHR with
    LTHR ÷ AeT − 1 ≤ 10 % (UA gap), or ≥ 60 min near a measured AeT with drift < 5 %
    (Friel). The 90-min test is NOT an AeT test (it yields no AeT number): it belongs to
    the Zone 3 gate (quality_gate.z3_gate) and no longer confirms Zone 5; neither do the
    plateau / weeks methods (they open Zone 3). Once confirmed it stays open with no expiry
    while (docs/research/detraining.md §6.1) weekly Z1 time is not < 2/3 of
    the level at confirmation for 3 weeks in a row (Hickson 1982; 3 weeks
    推估; recovery / taper weeks don't count). A break ≥ 6 days without
    running starts a re-entry block (engine/reentry.py, Daniels table 9.2):
    no Zone 3 / 5 inside it, then Zone 3 first; Zone 5 after 1–2 Zone 3
    sessions, the post-break long-run drift check (long_check), or — after
    ≥ 29 days — a new confirmation dated after the break.
a_race_rebase(plan, today)
    A 賽後重新打底 (SP-116, owner 2026-10-05): after an A race — never a B / C race — the Zone 3
    and Zone 5 gates lock again whatever the break length, and only a confirmation dated on or
    after the first day after the race's 恢復期 + 轉換期 + 回量期 (planning.POST_RACE_KINDS, SP-98) counts (the method in 課表偏好 間歇門檻,
    run with the post-race E pace / CP / LTHR). 徐國峰's cycle 「打底 → 練強度 → 比賽 → 跑力提升 →
    用新的 E 配速重新打底」 (coach-level, no controlled trial; periodization-cross-sport.md §4.10).
    Two A races so close that no 基礎期 follows the post-race phases: no rebuild (the second
    race's build keeps the gate as it was; 推估).

Zone 1 = the app's easy rule: avg HR ≤ AeT + 3 and ≤ 10 % of the time above
AeT + 3 (workout_review). 徐國峰's 心率 1 區 is the E zone of Daniels'
system (his RQ zones E/M/T/A/I/R); mapping E to "below AeT" is 推估.

RQ 訓練指數 = Daniels' intensity points: E 0.2 points/min, M 0.4, T 0.6,
A 0.8, I 1.0, R 1.5 (徐國峰, http://rocky549.blogspot.com/2016/05/vs_17.html,
「這些權重來自《丹尼爾博士跑步方程式》」 — coach, verified 2026-10-01; it is
not VDOT / 跑力). 30–42 points of E = 150–210 min. In TSS (Coggan: TSS =
hours × IF² × 100) at an E-run IF of 0.70 (推估, 未驗證 for running): 0.82
TSS/min, ≈ 4.1 TSS per RQ point → 30–42 points ≈ 120–170 TSS.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.i18n import N_, _

# ---- the 90-minute test (徐國峰 blog 2016-12; the 25 °C line: 台灣教練) ----------
XU_MIN_S = 90 * 60
XU_A_S, XU_B_S = 600.0, 5400.0       # HR at minute 10 (A) and minute 90 (B)
XU_GOOD = 0.10                       # < 10 % = 有氧基礎夠（5 % 內國家級）
XU_STOP_S = 30.0                     # 補給每次停不超過 30 秒
XU_HEAT_C = 25.0                     # 當天氣溫 25 °C 以下（課表文字的建議；不再拒絕，heat bands）
XU_FLAT_M_PER_KM = 20.0              # 「全程平坦」 = drift_of's flat rule (推估 mapping)
SRC_XU = "徐國峰部落格（2016-12，有氧基礎檢測）；徐國峰《跑者都該懂的跑步數據》"
# SP-275 (lthr-low-confidence-testing.md §2.2, §6.1 第 3 點): every drift test holds the output and
# lets the HR drift; slowing down hides the drift (a false pass). A scheduled 90-minute test whose
# minutes 80–90 are > 5 % slower (pace; power when there is power) than minutes 10–20 is refused
# (推估, UA's 5 %). Only the scheduled test — a passive long run keeps the Zone 1 rule (_z1).
XU_HOLD_A = (600.0, 1200.0)          # minutes 10–20
XU_HOLD_B = (4800.0, 5400.0)         # minutes 80–90
XU_HOLD_DROP = 0.05                  # 推估 (UA's 5 %)
XU_HOLD_COVER = 0.5                  # 推估: a window needs half its samples valid (moving / power > 0)
SRC_Z3_FIRST = "台灣教練：入門轉進階先練 3 區，3 區穩定、恢復正常後再加 5 區"
SRC_Z5_LIMIT = "台灣教練：5 區每趟至少 2 分鐘；一週最多 2 次、間隔至少 2 天"

# ---- weekly Z1 volume (the chart's reference band; the pause rule's measure) -----
RQ_E_PER_MIN = 0.2                   # Daniels E intensity points / min (RQ 訓練指數; 徐國峰 blog, verified)
XU_WEEK_POINTS = 30.0                # 台灣教練：一週約 150–210 分鐘 1 區（訓練指數 30–42 點）— the lower end
XU_WEEK_POINTS_HI = 42.0
IF_E = 0.70                          # 推估 (未驗證): an E run's intensity factor for the TSS conversion

# ---- the post-break long-run drift check (re-entry, UA: re-read after a layoff) ----
LONG_MIN_S = 75 * 60                 # workout_review.LONG_MIN_S
LONG_DAYS = 28                       # 推估
LONG_HR_RISE = 0.05                  # 推估: last third's HR ≤ first third's + 5 %
LONG_PACE_DROP = 0.05                # 推估: last third's speed ≥ first third's − 5 %
LONG_LAST_N = 3                      # 推估: the latest 3 long runs must all pass

# ---- the Z5 lifecycle (docs/research/detraining.md §6.1) ---------------------------
Z1_KEEP = 2.0 / 3.0                  # weekly Z1 time < 2/3 of the level at confirmation … (Hickson 1982:
                                     # 2/3 of the volume kept long endurance, 1/3 lost 10 % — peer-reviewed)
Z1_LOW_WEEKS = 3                     # … 3 complete weeks in a row → pause Z5 only (推估: 2 weeks trip on one
                                     # recovery week + one busy week). Recovery / taper / event / transition
                                     # weeks and weeks touching a break or its re-entry block don't count
# A break ≥ 6 days without running = a re-entry block (engine/reentry.py, Daniels table 9.2): no Z3 /
# Z5 inside it; after it Z5 needs 1 (6–13 d) / 2 (≥ 14 d) Z3 sessions, the last long run's drift check
# (14–28 d), or a re-confirmation dated after the break (≥ 29 d — Mujika & Padilla 2000)
LOOKBACK_DAYS = 182                  # how far back a confirmation is looked for
NO_DATA_DAYS = 42                    # no interpretable data for ~6 weeks → schedule the AeT test
                                     # (推估 / owner's choice; UA's drift-test page says "every 4-6
                                     # months" — months, not weeks: aerobic-base-readiness.md §8)

# ---- A 賽後重新打底 (SP-116) -----------------------------------------------------------
REBASE_SCAN_DAYS = 400               # how far back the latest A race is looked for
SRC_REBASE = N_("徐國峰：打底 → 練強度 → 比賽 → 跑力提升 → 用新的 E 配速重新打底（教練級，沒有對照試驗）")

# No stable-weekly-volume precondition before a test (owner 2026-10-03): the rule
# (3 weeks within ±15 %) had no source, so tests are suggested and counted without it.

STATE_LABEL = {"unconfirmed": "未確認", "confirmed": "已確認", "paused": "暫停", "open": "不設門檻",
               "reentry": "恢復期"}
PATH_LABEL = {"xu90": "徐國峰 90 分鐘飄移", "aet_ua_gap": "實測 AeT（UA 差距法）",
              "aet_friel_drift": "實測 AeT（Friel 飄移）", "method": "你選的間歇門檻"}


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


# ---- a per-run memo for the history replay (quality_gate.z5_history) ----------------
# Replaying z5_status day by day re-reads the same runs hundreds of times; inside
# replay_memo() the per-run results (measure, the 90-min test, the long-run halves)
# are kept for the replay only. Outside it nothing is cached here — the rules are the
# same either way, only the speed differs.
_MEMO: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("base_check_memo", default=None)


@contextlib.contextmanager
def replay_memo():
    tok = _MEMO.set({}) if _MEMO.get() is None else None
    try:
        yield
    finally:
        if tok is not None:
            _MEMO.reset(tok)


def _memo(key: tuple, fn):
    memo = _MEMO.get()
    if memo is None:
        return fn()
    if key not in memo:
        memo[key] = fn()
    return memo[key]


def _measure(ds, w) -> Optional[dict]:
    from backend.engine import workout_review as WR
    return _memo(("m", id(ds), w.idx), lambda: WR.measure(ds, w))


def monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def rq_points(z1_s: float) -> float:
    return z1_s / 60.0 * RQ_E_PER_MIN


def tss_of_points(points: float, if_e: float = IF_E) -> float:
    """RQ E points → TSS (Coggan TSS = h × IF² × 100; IF 0.70 推估): 30 → ≈ 122."""
    minutes = points / RQ_E_PER_MIN
    return minutes / 60.0 * if_e ** 2 * 100.0


def _runs(ds, today: dt.date, days: int):
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    return [w for w in sorted(ds.workouts, key=lambda x: x.day)
            if w.sport == "run" and tday - days < math.floor(w.day) <= tday]


def _z1(m: dict) -> bool:
    """徐國峰's 心率 1 區 on the whole run = the app's easy rule (推估 mapping)."""
    aet, hr = m.get("aet"), m.get("avg_hr")
    over, tot = m.get("over_aet_s"), m.get("hr_s") or 0
    return bool(aet and hr and hr <= aet + 3.0 and (over is None or tot <= 0 or over / tot <= 0.10))


# ---------------------------------------------------------------------------
# the 90-min test
# ---------------------------------------------------------------------------

def longest_stop(t, speed, a_s: float = XU_A_S, b_s: float = XU_B_S) -> float:
    """The longest stop (≤ 1.6 km/h, or a recording gap) between minute 10 and 90, s."""
    from backend.engine import workout_review as WR
    t = np.asarray(t, dtype=float)
    if not np.isfinite(t).any():
        return 0.0
    rel = t - t[np.isfinite(t)][0]
    sel = (rel >= a_s) & (rel <= b_s)
    best = 0.0
    d = np.diff(rel, prepend=rel[0])
    if sel.any():
        best = float(np.nanmax(np.where(sel, d, 0.0)))
        best = best if best > WR.MAX_DT else 0.0
    if speed is not None:
        s = WR._arr(speed, len(t))
        stop = sel & np.isfinite(s) & (s <= WR.STOP_KMH)
        for a, b in WR._stop_segments(rel, stop):
            best = max(best, b - a + 1.0)
    return best


def output_hold(t, speed=None, power=None) -> dict:
    """Was the output held (SP-275)? Minutes 80–90 vs 10–20: the mean power (> 0) when there is
    power, else the mean moving speed (> 1.6 km/h; a ≤ 30-s feeding stop doesn't count).
    {"basis": power | pace | None (neither: can't tell), "drop": the slowdown (pace: v1/v2 − 1;
    power: 1 − p2/p1), "ok": drop ≤ XU_HOLD_DROP (None without a basis)}."""
    from backend.engine import workout_review as WR
    t = np.asarray(t, dtype=float)
    out = {"basis": None, "drop": None, "ok": None}
    if not np.isfinite(t).any():
        return out
    rel = t - float(np.nanmin(t))

    def mean_in(x, win, floor):
        sel = (rel >= win[0]) & (rel < win[1])
        if not sel.any():
            return None
        v = x[sel]
        good = np.isfinite(v) & (v > floor)
        if good.sum() < XU_HOLD_COVER * sel.sum():
            return None
        return float(v[good].mean())
    for basis, ch, floor in (("power", power, 0.0), ("pace", speed, WR.STOP_KMH)):
        if ch is None:
            continue
        x = WR._arr(ch, len(t))
        a, b = mean_in(x, XU_HOLD_A, floor), mean_in(x, XU_HOLD_B, floor)
        if a and b:
            drop = (a / b - 1.0) if basis == "pace" else (1.0 - b / a)
            return {"basis": basis, "drop": drop, "ok": drop <= XU_HOLD_DROP}
    return out


def hold_text(h: dict) -> str:
    """The refusal (drop > 5 %) or the 「can't tell」 note; "" when held."""
    if h.get("basis") is None:
        return _("沒辦法確認配速有沒有維持（沒有速度也沒有功率）：只看心率")
    if not h.get("ok"):
        return _("後段放慢了 {n:.1f}%：飄移會偏小，下次配速固定", n=h["drop"] * 100)
    return ""


def is_scheduled_xu(ds, w) -> bool:
    """The run is a scheduled 徐國峰 90-minute test: the plan's test session done by it, or its own
    title is the test's (aet_test.TITLE_RE)."""
    from backend.engine import aet_test as AT
    from backend.engine import workout_review as WR
    try:
        sched = WR.scheduled_aet_test(ds, w)
    except Exception:                       # noqa: BLE001 — no plan sessions (tests, WKO5-only)
        sched = None
    return bool(sched and AT.is_xu(sched)) or AT.protocol_of_title(WR._title(w)) == "xu90"


def xu_run(ds, w, m: Optional[dict] = None) -> Optional[dict]:
    """徐國峰's 90-minute test on one run (None: not a ≥ 90-min run).
    {"idx", "date", "ok", "drift", "hr10", "hr90", "why", "scheduled", "hold"} — `why` lists
    every condition that failed (empty when ok). SP-275: a scheduled test (is_scheduled_xu) is
    run at a pace, so the Zone 1 rule doesn't apply; instead the output must hold (output_hold).
    A passive long run keeps the Zone 1 rule."""
    from backend.engine import quality_gate as QG
    from backend.engine import workout_review as WR
    if w.sport != "run":
        return None
    m = m if m is not None else WR.measure(ds, w)
    if not m or (m.get("moving_s") or 0) < XU_MIN_S:
        return None
    s = WR._samples(ds, w)
    if s is None:
        return None
    r = QG.xu_drift_of(s["t"], s["hr"])
    out = {"idx": w.idx, "date": WR._wdate(w).isoformat(), "drift": r and r["drift"],
           "hr10": r and r["hr10"], "hr90": r and r["hr90"], "why": []}
    why = out["why"]
    if r is None:
        why.append("第 10／90 分鐘沒有心率")
    cpm = m.get("climb_m_per_km")
    if "runningtrail" in w.tags or (cpm is not None and cpm >= XU_FLAT_M_PER_KM):
        why.append("不是平路（越野或每公里爬升 ≥ 20 m）")
    out["scheduled"] = sched = is_scheduled_xu(ds, w)
    if sched:
        h = out["hold"] = output_hold(s["t"], s.get("speed"), s.get("power"))
        if h["basis"] is not None and not h["ok"]:
            why.append(hold_text(h))
    elif not _z1(m):
        why.append("心率不是全程 1 區（平均 ≤ AeT+3、超過的時間 ≤ 10%）")
    # heat bands: the temperature is a band, not a refusal (> 25 °C was one before). Heat inflates
    # the drift, so a pass in heat is conservative and counts; a fail in heat says it may be the heat
    tc, src = WR.activity_temp(ds, w, m)
    band = WR.temp_band(tc)
    out.update(temp_c=tc, temp_src=src, band=band, heat=WR.is_heat(band),
               chip="🌡 " + WR.TEMP_BAND_LABEL.get(band, "溫度不明"))
    stop = longest_stop(s["t"], s["speed"])
    if stop > XU_STOP_S:
        why.append(f"第 10–90 分鐘停了 {stop:.0f} 秒（每次 ≤ 30 秒）")
    if r is not None and r["drift"] >= XU_GOOD:
        why.append(f"飄移 {r['drift'] * 100:.1f}%（≥ 10%）" + (f"（{out['chip']}，{WR.HEAT_NOTE}，可能是熱造成的）"
                                                              if out["heat"] else ""))
    out["ok"] = r is not None and not why
    return out


def xu_runs(ds, today: dt.date, days: int = LOOKBACK_DAYS) -> list[dict]:
    from backend.engine import workout_review as WR
    out = []
    for w in _runs(ds, today, days):
        dur = _f(w.metrics.get("duration")) or 0.0
        if dur < XU_MIN_S:
            continue
        r = _memo(("xu", id(ds), w.idx), lambda: xu_run(ds, w))
        if r is not None:
            out.append(r)
    WR._flush(ds)
    return out


def xu_text(r: dict) -> str:
    if r.get("drift") is None:
        return f"{r['date']} 90 分鐘跑：" + "；".join(r["why"])
    head = (f"{r['date']} 90 分鐘：第 10 分 {r['hr10']:.0f} → 第 90 分 {r['hr90']:.0f} bpm，"
            f"飄移 {r['drift'] * 100:.1f}%")
    heat = f"（{r['chip']}，熱環境，結果可能偏高；熱天通過仍算數）" if r["ok"] and r.get("heat") else ""
    h = r.get("hold") or {}
    note = ("；" + hold_text(h)) if r.get("scheduled") and h and h.get("basis") is None else ""
    return head + ("（< 10%：有氧基礎夠）" + heat if r["ok"] else "：" + "；".join(r["why"])) + note


# ---------------------------------------------------------------------------
# weekly Z1 time (the maintenance / pause rule and the chart's bars)
# ---------------------------------------------------------------------------

def z1_seconds(m: dict) -> float:
    """Moving time below AeT (workout_review zones["low"]); without LTHR the
    time not above AeT + 3 (推估 fallback)."""
    z = m.get("zones")
    if z:
        return float(z.get("low") or 0.0)
    if m.get("aet") and m.get("hr_s"):
        return max(0.0, float(m["hr_s"]) - float(m.get("over_aet_s") or 0.0))
    return 0.0


def weekly(ds, today: dt.date, weeks: int) -> list[dict]:
    """[{"monday", "z1_s", "run_s", "complete"}] for the last `weeks` weeks up
    to today's, oldest first, runs only."""
    from backend.engine import workout_review as WR
    m0 = monday(today) - dt.timedelta(weeks=weeks - 1)
    rows = {m0 + dt.timedelta(weeks=i): {"z1_s": 0.0, "run_s": 0.0} for i in range(weeks)}
    for w in _runs(ds, today, (today - m0).days + 1):
        m = _measure(ds, w)
        if not m:
            continue
        k = monday(WR._wdate(w))
        if k in rows:
            rows[k]["z1_s"] += z1_seconds(m)
            rows[k]["run_s"] += float(m.get("moving_s") or 0.0)
    WR._flush(ds)
    cur = monday(today)
    return [{"monday": k.isoformat(), **v, "complete": k < cur} for k, v in sorted(rows.items())]


# ---------------------------------------------------------------------------
# the post-break long-run drift check (re-entry after 14–28 days off; z5_status)
# — not an unlock signal and not part of the maintenance pause
# ---------------------------------------------------------------------------

def long_late_vs_early(t, hr, speed) -> Optional[dict]:
    """Last third vs first third of the moving time after minute 10: HR rise
    and speed drop (推估 thresholds)."""
    from backend.engine import workout_review as WR
    t = np.asarray(t, dtype=float)
    n = len(t)
    h, s = WR._arr(hr, n), WR._arr(speed, n)
    d = WR._dt(t)
    rel = t - t[np.isfinite(t)][0]
    mv = WR.moving_mask(t, s) & (rel >= 600) & np.isfinite(h) & (h > 0) & np.isfinite(s) & (s > 0)
    cum = np.cumsum(np.where(mv, d, 0.0))
    if cum[-1] < 1800:
        return None
    a, b = mv & (cum <= cum[-1] / 3), mv & (cum > 2 * cum[-1] / 3)
    h1, h3, v1, v3 = WR._wmean(h, d, a), WR._wmean(h, d, b), WR._wmean(s, d, a), WR._wmean(s, d, b)
    if not all((h1, h3, v1, v3)):
        return None
    return {"hr_rise": h3 / h1 - 1.0, "pace_drop": 1.0 - v3 / v1, "hr1": h1, "hr3": h3}


def long_check(ds, today: dt.date, days: int = LONG_DAYS) -> dict:
    """The latest LONG_LAST_N road long runs (≥ 75 min) in `days`; trail
    long runs are left out (climbs move pace and HR — 推估). state ok / fail
    / missing."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    runs = []
    for w in _runs(ds, today, days):
        if category(w) != "road" or (_f(w.metrics.get("duration")) or 0) < LONG_MIN_S:
            continue
        m = _measure(ds, w)
        if not m or (m.get("moving_s") or 0) < LONG_MIN_S:
            continue

        def halves(w=w):
            s = WR._samples(ds, w)
            return long_late_vs_early(s["t"], s["hr"], s["speed"]) if s is not None else None
        r = _memo(("long", id(ds), w.idx), halves)
        if r is None:
            continue
        r = dict(r)
        r.update(idx=w.idx, date=WR._wdate(w).isoformat(),
                 ok=r["hr_rise"] <= LONG_HR_RISE and r["pace_drop"] <= LONG_PACE_DROP)
        runs.append(r)
    WR._flush(ds)
    runs = runs[-LONG_LAST_N:]
    if not runs:
        return {"state": "missing", "runs": [], "why": f"{days} 天內沒有 ≥ 75 分鐘的路跑長跑"}
    bad = [r for r in runs if not r["ok"]]
    if bad:
        r = bad[-1]
        return {"state": "fail", "runs": runs, "date": runs[-1]["date"],
                "why": f"{r['date']} 長跑後段心率 {r['hr_rise'] * 100:+.0f}%、配速 {-r['pace_drop'] * 100:+.0f}%"
                       f"（各 ±5% 內，推估）"}
    return {"state": "ok", "runs": runs, "date": runs[-1]["date"],
            "why": f"最近 {len(runs)} 次長跑後段心率、配速都穩（±5% 內，推估）"}


# ---------------------------------------------------------------------------
# faster at the same HR: easy targets from the recent EF (推估)
# ---------------------------------------------------------------------------

EF_RUNS = 6                          # 推估: the last 6 easy road runs (the drift aggregate's n)
EF_DAYS = 56


def easy_targets(ds, today: dt.date, aet: Optional[float]) -> Optional[dict]:
    """Pace and power at the AeT HR from the median EF (speed / HR, power /
    HR) of the last EF_RUNS easy road runs (avg HR ≤ AeT + 3, ≥ 30 min) in 8
    weeks — when the athlete gets faster at the same HR the easy targets
    follow without a retest (the AeT HR itself is unchanged). Method 推估.
    {"pace_s_km", "power", "n", "text"}; None with < 3 runs or no AeT."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    if not aet:
        return None
    ev, ep = [], []
    for w in reversed(_runs(ds, today, EF_DAYS)):
        if category(w) != "road":
            continue
        m = WR.measure(ds, w)
        if not m or (m.get("moving_s") or 0) < 1800 or not m.get("avg_hr") or m["avg_hr"] > aet + 3.0:
            continue
        dist = _f(w.metrics.get("distance"))
        if dist and m["moving_s"]:
            ev.append(dist / (m["moving_s"] / 3600.0) / m["avg_hr"])
        if m.get("avg_power"):
            ep.append(m["avg_power"] / m["avg_hr"])
        if len(ev) >= EF_RUNS:
            break
    WR._flush(ds)
    if len(ev) < 3:
        return None
    kmh = float(np.median(ev)) * aet
    pw = float(np.median(ep)) * aet if len(ep) >= 3 else None
    pace = 3600.0 / kmh if kmh > 0 else None
    txt = (f"最近 {len(ev)} 次輕鬆跑：AeT {aet:.0f} bpm ≈ "
           + (f"{int(pace // 60)}:{int(pace % 60):02d}/km" if pace else "")
           + (f"、{pw:.0f} W" if pw else "") + "（同心率跑更快就自動跟著調；推估）")
    return {"pace_s_km": pace, "power": pw, "n": len(ev), "text": txt}


# ---------------------------------------------------------------------------
# the Zone 5 lifecycle
# ---------------------------------------------------------------------------

def _paths_for(mode: str) -> tuple:
    """The Zone 5 confirmation tests a 間歇門檻 mode uses (SP-39): both measured-AeT tests, a
    forced ua_gap / friel_drift only its own. The 90-min test (xu_drift) and plateau / weeks open
    Zone 3 only — Zone 5 still needs a measured AeT; none = no gate."""
    return {"auto": ("aet_ua_gap", "aet_friel_drift"), "xu_drift": ("aet_ua_gap", "aet_friel_drift"),
            "plateau": ("aet_ua_gap", "aet_friel_drift"), "weeks": ("aet_ua_gap", "aet_friel_drift"),
            "ua_gap": ("aet_ua_gap",), "friel_drift": ("aet_friel_drift",)}.get(mode, ())


def _skip_week(ds, mon: dt.date, brk: Optional[dict]) -> bool:
    """Weeks the Z1 rule doesn't count: recovery / taper / event / transition
    phases and weeks touching a break or its re-entry block (detraining.md §6.1)."""
    if brk:
        end = (mon + dt.timedelta(days=6)).isoformat()
        start = (dt.date.fromisoformat(brk["last_run"]) + dt.timedelta(days=1)).isoformat()
        if start <= end and mon.isoformat() < brk["end"]:
            return True
    plan = getattr(ds, "plan", None)
    if plan is not None:
        try:
            from backend.engine.planning import phase_on
            p = phase_on(plan, mon + dt.timedelta(days=3))
            if p is not None and p.kind in ("recovery", "taper", "event", "transition", "rebuild"):
                return True
        except Exception:                   # noqa: BLE001
            pass
    return False


def maintenance(ds, today: dt.date, since: dt.date, brk: Optional[dict] = None) -> dict:
    """The weekly check after a confirmation on `since`: {"ok", "why", "at",
    "z1_level_min", "weeks"}. Z1 time < 2/3 of the level at confirmation
    (mean of the 4 weeks up to it — 推估) for 3 complete weeks in a row
    (Hickson 1982; 3 weeks 推估) → pause Z5 until the next confirmation.
    Breaks are the re-entry rule's (z5_status)."""
    n = max(5, (monday(today) - monday(since)).days // 7 + 5)
    rows = weekly(ds, today, n)
    cm = monday(since).isoformat()
    upto = [r for r in rows if r["monday"] <= cm and r["complete"] or r["monday"] == cm][-4:]
    level = float(np.mean([r["z1_s"] for r in upto])) if upto else 0.0
    out = {"ok": True, "why": "", "at": None, "z1_level_min": level / 60.0, "weeks": []}
    low = 0
    for r in rows:
        if r["monday"] <= cm or not r["complete"]:
            continue
        mon = dt.date.fromisoformat(r["monday"])
        if _skip_week(ds, mon, brk):
            continue
        frac = r["z1_s"] / level if level > 0 else None
        out["weeks"].append({"monday": r["monday"], "z1_min": r["z1_s"] / 60.0, "frac": frac})
        low = low + 1 if frac is not None and frac < Z1_KEEP else 0
        if low >= Z1_LOW_WEEKS and out["ok"]:
            out.update(ok=False, at=r["monday"],
                       why=f"連續 {Z1_LOW_WEEKS} 週 1 區時間 < 確認時的 2/3（{level / 60:.0f} 分／週；"
                           "Hickson 1982；3 週推估）")
    return out


def a_race_rebase(plan, today: dt.date) -> Optional[dict]:
    """The A 賽後重新打底 in effect on `today` (SP-116), or None: the latest A event (priority A,
    any kind) that ended before `today`, the first day after its 恢復期 / 轉換期 / 回量期 (planning phases
    read through phase_days — the post-race phases themselves are planned elsewhere) and whether
    a 基礎期 follows them. {"event_id", "name", "race_end", "from" (ISO: confirmations from this
    day count), "text", "src"}. Never raises."""
    try:
        from backend.engine import planning as PL
        evs = [e for e in getattr(plan, "events", None) or () if getattr(e, "priority", "A") == "A"
               and e.end < today and (today - e.end).days <= REBASE_SCAN_DAYS]
        if not evs:
            return None
        ev = max(evs, key=lambda e: e.end)
        one = dt.timedelta(days=1)
        horizon = ev.end + dt.timedelta(days=REBASE_SCAN_DAYS)
        post = PL.phase_days(plan, ev.end + one, horizon, PL.POST_RACE_KINDS)
        d = ev.end + one
        while d in post:
            d += one
        nxt = PL.phase_on(plan, d)
        if nxt is not None and nxt.kind != "base":
            return None                      # no 基礎期 before the next build: nothing to rebuild in
    except Exception:                        # noqa: BLE001 — the gates must still evaluate
        return None
    return {"event_id": ev.id, "name": ev.name, "race_end": ev.end.isoformat(), "from": d.isoformat(),
            "text": _("A 賽「{name}」後重新打底", name=ev.name),
            "src": _(SRC_REBASE)}


def z5_status(ds, today: dt.date, mode: str = "auto", method_state: Optional[str] = None,
              aet_paths: Optional[dict] = None, brk: Optional[dict] = None,
              quality_dates: Optional[list] = None, rebase: Optional[dict] = None) -> dict:
    """z5_status_base, then the re-entry rules of the latest break `brk`
    (engine/reentry.plan; detraining.md §6.2). `quality_dates`: ISO dates of
    the interval sessions done (quality_gate.dose_history) — after a block
    the first ones are Zone 3 (Zone 5 is closed then). `rebase` (a_race_rebase, SP-116): only
    confirmations from rebase["from"] count, whatever the break."""
    if rebase and mode != "none":
        st = _z5_status(ds, today, mode, method_state, aet_paths, brk, quality_dates, rebase["from"])
        if not st["open"] and st["state"] == "unconfirmed":
            d = dt.date.fromisoformat(rebase["from"])
            why = _("{text}：{d} 起做一次 AeT 測試（UA 差距或 Friel 飄移），用賽後的 E 配速、CP、LTHR",
                    text=rebase["text"], d=f"{d.month}/{d.day}")
            st = {**st, "reason": why, "rebase": rebase, "text": _("Zone 5：未確認（{why}）", why=why)}
        return st
    return _z5_status(ds, today, mode, method_state, aet_paths, brk, quality_dates)


def _z5_status(ds, today: dt.date, mode: str, method_state: Optional[str], aet_paths: Optional[dict],
               brk: Optional[dict], quality_dates: Optional[list], rebase_from: Optional[str] = None) -> dict:
    if brk and brk.get("return") and brk["return"] > today.isoformat():
        brk = None                                   # a planned break ahead: nothing yet
    if rebase_from:
        # A 賽後重新打底: confirmations before rebase_from no longer count (like a ≥ 4-week break)
        aet_paths = {k: d for k, d in (aet_paths or {}).items() if d and str(d)[:10] >= rebase_from}
    if mode == "none" or not brk:
        return z5_status_base(ds, today, mode, method_state, aet_paths)
    ret, end, qf = brk["return"], brk["end"], brk["quality_from"]
    iso = today.isoformat()
    if iso < qf:
        why = f"{brk['text']}：恢復期內 3 區、5 區都不排（Daniels：只有 E 日）"
        return {"state": "reentry", "label": "恢復期", "open": False, "since": None, "path": None, "path_label": "",
                "reason": why, "maintenance": None, "xu_last": None, "reentry": brk, "pause": None,
                "text": f"Zone 5：恢復期（{why}）"}
    after = ret if brk.get("reconfirm") else None
    st = z5_status_base(ds, today, mode, method_state, aet_paths, after=after, brk=brk)
    st["reentry"] = brk
    if not st["open"]:
        if brk.get("reconfirm") and st["state"] == "unconfirmed":
            st["reason"] = f"停跑 {brk['days']} 天（≥ 4 週）：要在 {ret} 之後重新做 AeT 測試（UA 差距或 Friel 飄移）"
            st["text"] = f"Zone 5：未確認（{st['reason']}；Mujika & Padilla 2000）"
        return st
    n = sum(1 for d in (quality_dates or []) if d >= qf)
    need = int(brk.get("z3_before_z5") or 1)
    if n < need:
        why = f"恢復期後先完成 {need} 堂 3 區（已 {n} 堂；台灣教練：先 3 區後 5 區，堂數推估）"
        return {**st, "state": "paused", "label": STATE_LABEL["paused"], "open": False, "reason": why,
                "pause": {"kind": "reentry_z3", "done": n, "need": need},
                "text": f"Zone 5：暫停（{why}）"}
    if brk.get("drift_check"):
        # the post-break long-run check (UA: re-read after a layoff) — a re-entry rule, not an unlock signal
        lc = long_check(ds, today, days=max(7, (today - dt.date.fromisoformat(ret)).days + 1))
        if lc["state"] == "fail":
            why = f"恢復期後的長跑飄移檢查沒過（{lc['why']}；UA：中斷後重新讀）"
            return {**st, "state": "paused", "label": STATE_LABEL["paused"], "open": False, "reason": why,
                    "pause": {"kind": "drift_check", "why": lc["why"]},
                    "text": f"Zone 5：暫停（{why}）"}
    return st


def z5_status_base(ds, today: dt.date, mode: str = "auto", method_state: Optional[str] = None,
                   aet_paths: Optional[dict] = None, after: Optional[str] = None,
                   brk: Optional[dict] = None) -> dict:
    """The Zone 5 state for `today`: {"state" (unconfirmed / confirmed /
    paused / open), "label", "open", "since", "path", "path_label",
    "reason", "maintenance", "xu_last", "pause"}. `aet_paths`:
    {"aet_ua_gap": date, "aet_friel_drift": date} from quality_gate's methods
    (a measured AeT passing the UA gap with a measured LTHR / the Friel drift — the only
    confirmations since SP-39; `method_state` is no longer a path); none = no gate (Seiler).
    `pause`: why a paused state is paused ({"kind": "z1" | "reentry_z3" |
    "drift_check", …}), None otherwise."""
    if mode == "none":
        return {"state": "open", "label": STATE_LABEL["open"], "open": True, "since": None, "path": None,
                "path_label": "", "reason": "不設門檻（Seiler）：5 區照 80/20 安排", "pause": None,
                "maintenance": None, "xu_last": None, "text": "Zone 5：不設門檻（Seiler）"}
    paths = _paths_for(mode)
    events: list[tuple[str, str, str]] = []
    for k, d in (aet_paths or {}).items():
        if d and k in paths:
            events.append((str(d)[:10], k, PATH_LABEL[k]))
    if after:
        # a break ≥ 4 weeks: confirmations from before it no longer count (Mujika & Padilla 2000)
        events = [e for e in events if e[0] >= after]
    base = {"xu_last": None, "maintenance": None, "pause": None}
    if not events:
        why = ("還沒有實測 AeT 通過：實測 AeT＋實測 LTHR 差距 ≤ 10%，或在 AeT 附近 Friel 飄移 < 5%"
               if len(paths) > 1 else "這個測試還沒做到" if paths else "這個間歇門檻不開 5 區")
        return {**base, "state": "unconfirmed", "label": STATE_LABEL["unconfirmed"], "open": False, "since": None,
                "path": None, "path_label": "", "reason": why,
                "text": f"Zone 5：未確認（{why}）"}
    # the latest confirmation; on a tie the first listed (UA, then Friel)
    since_s, path, detail = max(events, key=lambda e: e[0])
    since = dt.date.fromisoformat(since_s)
    mt = maintenance(ds, today, since, brk)
    base["maintenance"] = mt
    common = {"since": since_s, "path": path, "path_label": PATH_LABEL[path], "detail": detail}
    if not mt["ok"]:
        return {**base, **common, "state": "paused", "label": STATE_LABEL["paused"], "open": False,
                "reason": mt["why"], "pause": {"kind": "z1", "at": mt.get("at")},
                "text": f"Zone 5：暫停（{mt['why']}）；3 區照排，下次確認後恢復"}
    return {**base, **common, "state": "confirmed", "label": STATE_LABEL["confirmed"], "open": True,
            "reason": detail, "text": f"Zone 5：已確認（{since_s}，{PATH_LABEL[path]}）"}
