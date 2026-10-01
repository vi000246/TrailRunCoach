"""
有氧基礎確認與 Zone 5 的生命週期 — 徐國峰's aerobic-base checks
(docs/research/xu-guofeng-reply.md, aerobic-base-readiness.md §1.1, §4).

Sources: 台灣教練and the user's notes on 徐國峰's book
(跑者都該懂的跑步數據 L58–L77). Our proxies / interpretations are 推估.

xu_run(ds, w)
    徐國峰's 90-min test on one run: ≥ 90 min, flat, ≤ 25 °C, every stop
    ≤ 30 s, HR in Z1, drift = (HR@90′ − HR@10′) / HR@10′ < 10 % (his own
    definition — minute 10 vs minute 90, ±1 min means; NOT drift_of's
    half-vs-half Pa:HR). The test "就是你週末那一次 LSD": any qualifying run
    counts, scheduled or not.
three_signals(ds, today)
    徐國峰's three signals when there is no formal test, all three needed:
      ① a ≥ 90-min Z1 run judged by the same 10-vs-90 drift < 10 % (the
        refinement: the drift result is the criterion itself);
      ② ~210 min of Z1 in a week (RQ 訓練指數 30–42), and the week after not
        collapsing (≥ 70 % of that week's running time — 推估);
      ③ recent long runs: late HR not drifting up, pace not dropping
        (last third vs first third ≤ 5 % each — 推估).
z5_status(ds, today, …)
    Zone 5 opens only once the base is confirmed (徐國峰: Zone 3 first, then
    Zone 5), by any path: 三訊號, the 90-min test, or a measured AeT that
    passes the UA gap / Friel drift. Once confirmed it stays open with no
    expiry while (docs/research/detraining.md §6.1): weekly Z1 time is not
    < 2/3 of the level at confirmation for 3 weeks in a row (Hickson 1982;
    3 weeks 推估; recovery / taper weeks don't count) and the recent long
    runs still pass ③. A break ≥ 6 days without running starts a re-entry
    block (engine/reentry.py, Daniels table 9.2): no Zone 3 / 5 inside it,
    then Zone 3 first; Zone 5 after 1–2 Zone 3 sessions, a drift check, or
    — after ≥ 29 days — a new confirmation dated after the break.

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

import datetime as dt
import math
from typing import Optional

import numpy as np

# ---- 徐國峰 (notes L58–L67; 私訊 2026-10-01) -----------------------------------
XU_MIN_S = 90 * 60
XU_A_S, XU_B_S = 600.0, 5400.0       # HR at minute 10 (A) and minute 90 (B)
XU_GOOD = 0.10                       # < 10 % = 有氧基礎夠（5 % 內國家級）
XU_STOP_S = 30.0                     # 補給每次停不超過 30 秒
XU_HEAT_C = 25.0                     # 當天氣溫 25 °C 以下
XU_FLAT_M_PER_KM = 20.0              # 「全程平坦」 = drift_of's flat rule (推估 mapping)
SRC_XU = "台灣教練"
SRC_XU_SIGNALS = "三訊號（私訊，2026-10-01）"
SRC_Z3_FIRST = "台灣教練：入門轉進階先練 3 區，3 區跑順、恢復跟得上再加 5 區"
SRC_Z5_LIMIT = "台灣教練：5 區每趟最短 2 分鐘；一週最多兩次、兩次之間至少隔兩天"

# ---- ② weekly Z1 volume --------------------------------------------------------
RQ_E_PER_MIN = 0.2                   # Daniels E intensity points / min (RQ 訓練指數; 徐國峰 blog, verified)
XU_WEEK_POINTS = 30.0                # 徐國峰：210 分鐘左右（訓練指數約 30～42 點）— the lower end
XU_WEEK_POINTS_HI = 42.0
IF_E = 0.70                          # 推估 (未驗證): an E run's intensity factor for the TSS conversion
NEXT_WEEK_KEEP = 0.70                # 推估: 「練完隔週不會累到練不下去」 = next week ≥ 70 % of its running time
SIGNAL2_WEEKS = 5                    # 推估: the qualifying week within the last 5 complete weeks

# ---- ③ long runs ----------------------------------------------------------------
LONG_MIN_S = 75 * 60                 # workout_review.LONG_MIN_S
LONG_DAYS = 28                       # 推估: 「近期」
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
                                     # (UA's 4–6-week retest, coach; the original wording 未驗證)
SIGNAL1_DAYS = 56                    # ① within 8 weeks (the gate's LOOKBACK_DAYS)

STATE_LABEL = {"unconfirmed": "未確認", "confirmed": "已確認", "paused": "暫停", "open": "不設門檻",
               "reentry": "恢復期"}
PATH_LABEL = {"xu_signals": "三訊號", "xu90": "徐國峰 90 分鐘飄移", "aet_ua_gap": "實測 AeT（UA 差距法）",
              "aet_friel_drift": "實測 AeT（Friel 飄移）", "method": "你選的間歇門檻"}


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


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
# ① / the 90-min test
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


def xu_run(ds, w, m: Optional[dict] = None) -> Optional[dict]:
    """徐國峰's 90-minute test on one run (None: not a ≥ 90-min run).
    {"idx", "date", "ok", "drift", "hr10", "hr90", "why"} — `why` lists
    every condition that failed (empty when ok)."""
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
    if not _z1(m):
        why.append("心率不是全程 1 區（平均 ≤ AeT+3、超過的時間 ≤ 10%）")
    tc, src = WR.activity_temp(ds, w, m)
    out["temp_c"] = tc
    if tc is not None and tc > XU_HEAT_C:
        why.append(f"氣溫 {tc:.0f} °C（> 25 °C）")
    stop = longest_stop(s["t"], s["speed"])
    if stop > XU_STOP_S:
        why.append(f"第 10–90 分鐘停了 {stop:.0f} 秒（每次 ≤ 30 秒）")
    if r is not None and r["drift"] >= XU_GOOD:
        why.append(f"飄移 {r['drift'] * 100:.1f}%（≥ 10%）")
    out["ok"] = r is not None and not why
    return out


def xu_runs(ds, today: dt.date, days: int = LOOKBACK_DAYS) -> list[dict]:
    from backend.engine import workout_review as WR
    out = []
    for w in _runs(ds, today, days):
        dur = _f(w.metrics.get("duration")) or 0.0
        if dur < XU_MIN_S:
            continue
        r = xu_run(ds, w)
        if r is not None:
            out.append(r)
    WR._flush(ds)
    return out


def xu_text(r: dict) -> str:
    if r.get("drift") is None:
        return f"{r['date']} 90 分鐘跑：" + "；".join(r["why"])
    head = (f"{r['date']} 90 分鐘：第 10 分 {r['hr10']:.0f} → 第 90 分 {r['hr90']:.0f} bpm，"
            f"飄移 {r['drift'] * 100:.1f}%")
    return head + ("（< 10%：有氧基礎夠）" if r["ok"] else "：" + "；".join(r["why"]))


# ---------------------------------------------------------------------------
# ② weekly Z1 time
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
        m = WR.measure(ds, w)
        if not m:
            continue
        k = monday(WR._wdate(w))
        if k in rows:
            rows[k]["z1_s"] += z1_seconds(m)
            rows[k]["run_s"] += float(m.get("moving_s") or 0.0)
    WR._flush(ds)
    cur = monday(today)
    return [{"monday": k.isoformat(), **v, "complete": k < cur} for k, v in sorted(rows.items())]


def signal2(rows: list[dict]) -> dict:
    """② on weekly(): a complete week with ≥ 30 RQ points of Z1 (≈ 150–210
    min, 徐國峰) among the last SIGNAL2_WEEKS, followed by a complete week
    with ≥ 70 % of its running time (推估)."""
    done = [r for r in rows if r["complete"]][-(SIGNAL2_WEEKS + 1):]
    best = None
    for i, r in enumerate(done):
        pts = rq_points(r["z1_s"])
        if pts < XU_WEEK_POINTS:
            continue
        nxt = done[i + 1] if i + 1 < len(done) else None
        cand = {"week": r["monday"], "points": pts, "minutes": r["z1_s"] / 60.0, "tss_eq": tss_of_points(pts),
                "next_ratio": (nxt["run_s"] / r["run_s"]) if nxt and r["run_s"] else None}
        cand["ok"] = cand["next_ratio"] is not None and cand["next_ratio"] >= NEXT_WEEK_KEEP
        cand["pending"] = nxt is None
        if best is None or cand["ok"] or not best["ok"]:
            best = cand
    if best is None:
        top = max(done, key=lambda r: r["z1_s"], default=None)
        pts = rq_points(top["z1_s"]) if top else 0.0
        return {"ok": False, "points": pts, "minutes": (top or {}).get("z1_s", 0.0) / 60.0,
                "tss_eq": tss_of_points(pts), "week": (top or {}).get("monday"),
                "why": f"最近 {SIGNAL2_WEEKS} 週最多 {pts:.0f} 點（1 區 {pts / RQ_E_PER_MIN:.0f} 分）< 30 點"}
    if not best["ok"]:
        best["why"] = ("那週之後還沒有完整的一週可以看" if best["pending"] else
                       f"隔週跑量只有那週的 {best['next_ratio'] * 100:.0f}%（< 70%，推估）")
    return best


def signal2_text(s: dict) -> str:
    base = (f"1 區 {s.get('minutes', 0):.0f} 分 ≈ RQ 訓練指數 {s.get('points', 0):.0f} 點"
            f"（≈ {s.get('tss_eq', 0):.0f} TSS）")
    return base + ("" if s.get("ok") else f"：{s.get('why', '')}")


# ---------------------------------------------------------------------------
# ③ recent long runs
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
    """③: the latest LONG_LAST_N road long runs (≥ 75 min) in `days`; trail
    long runs are left out (climbs move pace and HR — 推估). state ok / fail
    / missing."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    runs = []
    for w in _runs(ds, today, days):
        if category(w) != "road" or (_f(w.metrics.get("duration")) or 0) < LONG_MIN_S:
            continue
        m = WR.measure(ds, w)
        if not m or (m.get("moving_s") or 0) < LONG_MIN_S:
            continue
        s = WR._samples(ds, w)
        r = long_late_vs_early(s["t"], s["hr"], s["speed"]) if s is not None else None
        if r is None:
            continue
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


def three_signals(ds, today: dt.date) -> dict:
    """{"ok", "s1", "s2", "s3", "date", "text"} — all three needed (徐國峰)."""
    xs = [r for r in xu_runs(ds, today, SIGNAL1_DAYS)]
    ok1 = [r for r in xs if r["ok"]]
    s1 = {"ok": bool(ok1), "run": (ok1 or xs or [None])[-1]}
    s2 = signal2(weekly(ds, today, SIGNAL2_WEEKS + 2))
    s3 = long_check(ds, today)
    ok = s1["ok"] and s2.get("ok") and s3["state"] == "ok"
    dates = [s1["run"]["date"] if s1["run"] else None]
    if s2.get("week"):
        dates.append((dt.date.fromisoformat(s2["week"]) + dt.timedelta(days=13)).isoformat())
    dates.append(s3.get("date"))
    t1 = xu_text(s1["run"]) if s1["run"] else "8 週內沒有 ≥ 90 分鐘的跑步"
    text = (f"① {t1}；② {signal2_text(s2)}；③ {s3['why']}")
    return {"ok": bool(ok), "s1": s1, "s2": s2, "s3": s3,
            "date": min(max(d for d in dates if d), today.isoformat()) if ok else None, "text": text}


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
    return {"auto": ("xu_signals", "xu90", "aet_ua_gap", "aet_friel_drift"), "xu_signals": ("xu_signals",),
            "xu_drift": ("xu90",), "ua_gap": ("aet_ua_gap",), "friel_drift": ("aet_friel_drift",)}.get(mode, ())


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
            if p is not None and p.kind in ("recovery", "taper", "event", "transition"):
                return True
        except Exception:                   # noqa: BLE001
            pass
    return False


def maintenance(ds, today: dt.date, since: dt.date, brk: Optional[dict] = None) -> dict:
    """The weekly checks after a confirmation on `since`: {"ok", "why", "at",
    "z1_level_min", "weeks", "long"}. Z1 time < 2/3 of the level at
    confirmation (mean of the 4 weeks up to it — 推估) for 3 complete weeks in
    a row (Hickson 1982; 3 weeks 推估) → pause Z5; the recent long runs
    failing ③ → pause. Breaks are the re-entry rule's (z5_status)."""
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
    lc = long_check(ds, today)
    out["long"] = lc
    if out["ok"] and lc["state"] == "fail" and lc.get("date", "") > since.isoformat():
        out.update(ok=False, at=lc["date"], why=lc["why"])
    return out


def z5_status(ds, today: dt.date, mode: str = "auto", method_state: Optional[str] = None,
              aet_paths: Optional[dict] = None, brk: Optional[dict] = None,
              quality_dates: Optional[list] = None) -> dict:
    """z5_status_base, then the re-entry rules of the latest break `brk`
    (engine/reentry.plan; detraining.md §6.2). `quality_dates`: ISO dates of
    the interval sessions done (quality_gate.dose_history) — after a block
    the first ones are Zone 3 (Zone 5 is closed then)."""
    if brk and brk.get("return") and brk["return"] > today.isoformat():
        brk = None                                   # a planned break ahead: nothing yet
    if mode == "none" or not brk:
        return z5_status_base(ds, today, mode, method_state, aet_paths)
    ret, end, qf = brk["return"], brk["end"], brk["quality_from"]
    iso = today.isoformat()
    if iso < qf:
        why = f"{brk['text']}：恢復期內 3 區、5 區都不排（Daniels：只有 E 日）"
        return {"state": "reentry", "label": "恢復期", "open": False, "since": None, "path": None, "path_label": "",
                "reason": why, "signals": None, "maintenance": None, "xu_last": None, "reentry": brk,
                "text": f"Zone 5：恢復期（{why}）"}
    after = ret if brk.get("reconfirm") else None
    st = z5_status_base(ds, today, mode, method_state, aet_paths, after=after, brk=brk)
    st["reentry"] = brk
    if not st["open"]:
        if brk.get("reconfirm") and st["state"] == "unconfirmed":
            st["reason"] = f"停跑 {brk['days']} 天（≥ 4 週）：要在 {ret} 之後重新確認有氧基礎（90 分飄移或三訊號）"
            st["text"] = f"Zone 5：未確認（{st['reason']}；Mujika & Padilla 2000）"
        return st
    n = sum(1 for d in (quality_dates or []) if d >= qf)
    need = int(brk.get("z3_before_z5") or 1)
    if n < need:
        why = f"恢復期後先完成 {need} 堂 3 區（已 {n} 堂；徐國峰：先 3 區後 5 區，堂數推估）"
        return {**st, "state": "paused", "label": STATE_LABEL["paused"], "open": False, "reason": why,
                "text": f"Zone 5：暫停（{why}）"}
    if brk.get("drift_check"):
        lc = long_check(ds, today, days=max(7, (today - dt.date.fromisoformat(ret)).days + 1))
        if lc["state"] == "fail":
            why = f"恢復期後的長跑飄移檢查沒過（{lc['why']}；UA：中斷後重新讀）"
            return {**st, "state": "paused", "label": STATE_LABEL["paused"], "open": False, "reason": why,
                    "text": f"Zone 5：暫停（{why}）"}
    return st


def z5_status_base(ds, today: dt.date, mode: str = "auto", method_state: Optional[str] = None,
                   aet_paths: Optional[dict] = None, after: Optional[str] = None,
                   brk: Optional[dict] = None) -> dict:
    """The Zone 5 state for `today`: {"state" (unconfirmed / confirmed /
    paused / open), "label", "open", "since", "path", "path_label",
    "reason", "signals", "maintenance", "xu_last"}. `aet_paths`:
    {"aet_ua_gap": date, "aet_friel_drift": date} from quality_gate's methods
    (a measured AeT passing the UA gap / Friel drift). Modes plateau / weeks
    use the method's own unlock (dated today); none = no gate (Seiler)."""
    if mode == "none":
        return {"state": "open", "label": STATE_LABEL["open"], "open": True, "since": None, "path": None,
                "path_label": "", "reason": "不設門檻（Seiler）：5 區照 80/20 安排", "signals": None,
                "maintenance": None, "xu_last": None, "text": "Zone 5：不設門檻（Seiler）"}
    paths = _paths_for(mode)
    events: list[tuple[str, str, str]] = []
    xs = xu_runs(ds, today) if ("xu90" in paths or "xu_signals" in paths) else []
    for r in xs:
        if r["ok"] and "xu90" in paths:
            events.append((r["date"], "xu90", xu_text(r)))
    sig = three_signals(ds, today) if "xu_signals" in paths else None
    if sig and sig["ok"]:
        events.append((sig["date"], "xu_signals", sig["text"]))
    for k, d in (aet_paths or {}).items():
        if d and k in paths:
            events.append((str(d)[:10], k, PATH_LABEL[k]))
    if mode in ("plateau", "weeks") and method_state == "unlocked":
        events.append((today.isoformat(), "method", PATH_LABEL["method"]))
    if after:
        # a break ≥ 4 weeks: confirmations from before it no longer count (Mujika & Padilla 2000)
        events = [e for e in events if e[0] >= after]
    base = {"signals": sig, "xu_last": xs[-1] if xs else None, "maintenance": None}
    if not events:
        why = sig["text"] if sig else ("沒有符合的確認" if paths else "這個間歇門檻不開 5 區")
        return {**base, "state": "unconfirmed", "label": STATE_LABEL["unconfirmed"], "open": False, "since": None,
                "path": None, "path_label": "", "reason": why,
                "text": f"Zone 5：未確認（{why}）"}
    # prefer 三訊號 on a tie: the label the user asked for
    since_s, path, detail = max(events, key=lambda e: (e[0], e[1] == "xu_signals"))
    since = dt.date.fromisoformat(since_s)
    mt = maintenance(ds, today, since, brk)
    base["maintenance"] = mt
    common = {"since": since_s, "path": path, "path_label": PATH_LABEL[path], "detail": detail}
    if not mt["ok"]:
        return {**base, **common, "state": "paused", "label": STATE_LABEL["paused"], "open": False,
                "reason": mt["why"],
                "text": f"Zone 5：暫停（{mt['why']}）；3 區照排，下次確認後恢復"}
    return {**base, **common, "state": "confirmed", "label": STATE_LABEL["confirmed"], "open": True,
            "reason": detail, "text": f"Zone 5：已確認（{since_s}，{PATH_LABEL[path]}）"}
