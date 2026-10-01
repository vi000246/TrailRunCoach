"""
Event-driven zone updates — docs/research/zones-and-thresholds.md §2.5
「事件觸發」 and §3.4 change 4: the zones follow the thresholds in effect (a
test applied today re-zones today, wko5views.plan_changed), and these
detectors say when a retest is worth it. They only SUGGEST: nothing here
schedules a test (overview.week_plan never reads them); the 「建議做測試 —
要排在哪一天？」 UI (feat/interval-library) renders the objects.

Detectors (numbers 推估 unless a source is named):
  test_applied  a CP / LTHR / AeT row dated in the last 14 days (推估 window):
                informational — the zones were recomputed from that day.
  hr_shift      on cool days (activity temp < 25 °C — the cut-off of 徐國峰's
                「等天氣轉涼再做」, §2.3), the HR at the same power moved > 5 bpm
                and stays moved: the last 6 cool steady runs against the line
                HR = a + b·P fitted on the cool steady runs of the 365 days
                before them (≥ 8 runs), median residual > 5 bpm with ≥ 5 of
                the 6 on the same side. 6 runs / 5 bpm are B3's shift rule
                (unsourced-rules.md B3, §2.5 item 3 — 推估); the one allowed
                outlier and the 8-run baseline are 推估 (wrist HR noise).
  break         ≥ 4 weeks without running (engine/reentry.py, a block of
                29+ days): thresholds are stale (detraining: submaximal HR
                +11 bpm within 2–4 weeks, Coyle 1986 / Houmard 1992,
                docs/research/detraining.md) — retest after the re-entry block.
  cool_season   the first cool spell after summer: 3 road-run days in a row
                below 25 °C after a summer (≥ 10 run days ≥ 25 °C and ≥ half
                of them in the 60 days before; hikes / trail runs left out —
                a mountain is cool in August) → the 30-min test and an
                AeT test (§3.3). 3 days / 60 days / the summer test are 推估.

Wrist optical HR (the athlete has no chest strap): the steady segments drop
the first 2 minutes after every change (optical HR lags a change of effort
— τ 55–70 s for HR itself, Hunt 2015/2019, plus the wrist's slower response,
徐國峰), take the median HR of a run (spikes: 223 / 220 bpm raw peaks in the
data, §1.5) and compare runs, not single segments.

Suggestion object (stable, documented for the UI):
  {"id": "hr_shift" | "break" | "cool_season",
   "kind": "test_suggestion",
   "tests": ["tt30", "aet", "cp"],   # tt30 = 30-min solo TT (LTHR + CP check),
                                     # aet = AeT drift test, cp = CP test
   "title", "text",                  # Traditional Chinese, one line each
   "earliest": "YYYY-MM-DD" | None,  # not before this day (break: block end)
   "detected": "YYYY-MM-DD",
   "conditions": [str],              # how to run it (no chest strap needed)
   "caveat": WRIST_NOTE,
   "estimate": True,                 # the trigger rule is 推估
   "source": str, "evidence": {...}}
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Optional

COOL_C = 25.0                 # 徐國峰（xu-guofeng-reply.md:22）：臺灣半年白天 ≥ 25 °C，熱天心率偏高
SHIFT_BPM = 5.0               # B3 shift rule (unsourced-rules.md; §2.5 item 3) — 推估
N_RECENT = 6                  # B3: the last 6 points — 推估
N_SAME_SIDE = 5               # 推估: one wrist-HR outlier allowed out of 6
BASE_MIN = 8                  # 推估: cool steady runs needed for the reference line
BASE_DAYS = 365               # 推估: the reference line's window before the recent runs
RECENT_DAYS = 60              # 推估: the 6 recent runs must be this fresh
SETTLE_S = 120.0              # 推估: optical HR after a change of effort is not used (first 2 min)
SEG_MIN_S = 600.0             # heat_data.steady_segments: ≥ 10 min steady, flat, after the first 10 min
FLAT_G, POWER_CV, SKIP_S = 0.03, 0.10, 600.0
POWER_RANGE_W = 10.0          # 推估: compare only within the reference runs' power range ± 10 W
BREAK_DAYS = 28               # ≥ 4 weeks (detraining.md; reentry 29–56 / long blocks)
SPELL_DAYS = 3                # 推估: activity days in a row below 25 °C
SUMMER_DAYS, SUMMER_MIN, SUMMER_SHARE = 60, 10, 0.5   # 推估
SEASON_ACTIVE_DAYS = 60       # 推估: the cool-season suggestion stays this long after the spell starts
APPLIED_DAYS = 14             # 推估

WRIST_NOTE = ("手腕光學心率在變速和低溫時誤差較大（推估）：用固定強度、穩定配速，"
              "每次變速後的前 1–2 分鐘心率不採用。腕式對胸帶 rc 0.67–0.92 vs 0.996（Gillinov 2017），"
              "光學 MAE 4.5–14 bpm（Gielen 2026），腕式反應較慢（徐國峰）")
TEST_LABEL = {"tt30": "30 分鐘獨跑測試（LTHR＋CP 檢查）", "aet": "AeT 測試", "cp": "CP 測試"}
CONDITIONS = {
    "tt30": "< 25 °C、平路；暖身 15 分後 30 分鐘均勻用力（不要衝開頭），第 10 分按 lap；"
            "LTHR＝後 20 分平均心率，30 分平均功率和 CP 比對（Friel；Jones 2019）",
    "aet": "< 25 °C、平地；固定功率 40–60 分（Uphill Athlete）或固定 E 配速 90 分（徐國峰），不要調速",
    "cp": "< 25 °C；照課表偏好的 CP 測試方式，全力段要真的全力",
}
SRC = "docs/research/zones-and-thresholds.md §2.5 事件觸發、§3.3"


def _iso(d) -> str:
    return d.isoformat() if isinstance(d, dt.date) else str(d)[:10]


def _date(s) -> dt.date:
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


# ---------------------------------------------------------------------------
# weather per run
# ---------------------------------------------------------------------------

def weather_of(ds, acts: list[dict]) -> dict:
    """{workout idx: {"temp_c", "hadley"}} from activity_weather rows: by file,
    else the only row of that date (COROS file names don't always match)."""
    by_file = {a.get("file"): a for a in acts if a.get("file")}
    by_date: dict = {}
    for a in acts:
        if a.get("date"):
            by_date.setdefault(a["date"], []).append(a)
    out = {}
    for w in ds.workouts:
        f = getattr(getattr(w, "entry", None), "file", None)
        a = by_file.get(f)
        if a is None:
            try:
                d = w.entry.start.date().isoformat()
            except AttributeError:
                continue
            xs = by_date.get(d) or []
            a = xs[0] if len(xs) == 1 else None
        if a is not None and _f(a.get("temp_c")) is not None:
            out[w.idx] = {"temp_c": _f(a["temp_c"]), "hadley": _f(a.get("hadley"))}
    return out


# ---------------------------------------------------------------------------
# steady points (one per run), wrist-HR safe
# ---------------------------------------------------------------------------

def run_point(windows: list[dict]) -> Optional[dict]:
    """One run's steady HR-at-power from its 100 m grade windows (racepower
    grade_samples rows: g, v, p, hr, hr_lag, run, k, t): consecutive flat
    running windows after the first 10 minutes; in each stretch the first
    2 minutes are dropped (wrist HR still catching up); a stretch counts
    with ≥ 10 min left and power CV < 10 %. Pooled over the stretches:
    time-weighted mean power and the median HR (robust to optical spikes)."""
    ws = sorted((x for x in windows if x.get("k") is not None), key=lambda x: x["k"])
    keep: list = []
    cur: list = []

    def flush():
        if not cur:
            return
        tt, acc = [], 0.0
        for x in cur:
            dur = 100.0 / x["v"]
            if acc >= SETTLE_S:
                tt.append((x, dur))
            acc += dur
        secs = sum(d for _, d in tt)
        ps = [x["p"] for x, _ in tt]
        if secs >= SEG_MIN_S and ps and statistics.pstdev(ps) / max(1e-9, statistics.fmean(ps)) < POWER_CV:
            keep.extend(tt)
    for x in ws:
        hr = x.get("hr_lag") if x.get("hr_lag") else x.get("hr")
        ok = (x.get("g") is not None and abs(x["g"]) < FLAT_G and (x.get("run") is None or x["run"] >= 0.5)
              and (x.get("t") or 0) >= SKIP_S and x.get("p") and hr and x.get("v"))
        if ok and cur and x["k"] == cur[-1]["k"] + 1:
            cur.append({**x, "_hr": hr})
        else:
            flush()
            cur = [{**x, "_hr": hr}] if ok else []
    flush()
    if not keep:
        return None
    secs = sum(d for _, d in keep)
    p = sum(x["p"] * d for x, d in keep) / secs
    return {"p": p, "hr": statistics.median(x["_hr"] for x, _ in keep), "seconds": secs}


def steady_points(ds, today: dt.date, weather: dict, days: int = BASE_DAYS + RECENT_DAYS) -> list[dict]:
    """[{date, idx, p, hr, seconds, temp_c, hadley}] of the cool (< 25 °C) road
    runs with power in the last `days` days — one point per run."""
    from backend.engine.racepower import athlete as A
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(today)
    runs = [w for w in ds.workouts if w.sport == "run" and tday - days < w.day <= tday + 1
            and (weather.get(w.idx) or {}).get("temp_c") is not None and weather[w.idx]["temp_c"] < COOL_C
            and "runningtrail" not in (w.tags or [])]
    out = []
    for w in runs:
        pt = run_point(A.grade_samples(ds, [w]))
        if pt:
            out.append({"date": w.entry.start.date().isoformat(), "idx": w.idx, **pt, **weather[w.idx]})
    return sorted(out, key=lambda x: x["date"])


# ---------------------------------------------------------------------------
# detectors (pure)
# ---------------------------------------------------------------------------

def _fit(pts) -> Optional[tuple[float, float]]:
    n = len(pts)
    if n < 2:
        return None
    mx = sum(p for p, _ in pts) / n
    my = sum(h for _, h in pts) / n
    sxx = sum((p - mx) ** 2 for p, _ in pts)
    if sxx <= 0:
        return None
    b = sum((p - mx) * (h - my) for p, h in pts) / sxx
    return my - b * mx, b


def hr_shift(points: list[dict], today: dt.date) -> dict:
    """The cool-day HR-at-power shift (module doc): {"fired", "shift_bpm",
    "direction" (up / down), "n", "same_side", "noise_bpm", "line", "recent",
    "reason"}. `points` are cool steady runs (steady_points), any order."""
    pts = sorted((p for p in points if p.get("temp_c") is not None and p["temp_c"] < COOL_C),
                 key=lambda p: p["date"])
    out = {"fired": False, "shift_bpm": None, "direction": None, "n": 0, "same_side": 0, "noise_bpm": None,
           "line": None, "recent": [], "reason": None}
    fresh = [p for p in pts if (today - _date(p["date"])).days <= RECENT_DAYS]
    if len(fresh) < N_RECENT:
        out["reason"] = f"最近 {RECENT_DAYS} 天 < {COOL_C:.0f} °C 的穩定路跑只有 {len(fresh)} 次（要 {N_RECENT} 次）"
        return out
    first = _date(fresh[-N_RECENT]["date"])
    base = [p for p in pts if first - dt.timedelta(days=BASE_DAYS) <= _date(p["date"]) < first]
    if len(base) < BASE_MIN:
        out["reason"] = f"之前 {BASE_DAYS} 天的涼天穩定路跑只有 {len(base)} 次（要 {BASE_MIN} 次才畫得出基準線）"
        return out
    f = _fit([(p["p"], p["hr"]) for p in base])
    if f is None or f[1] <= 0:
        out["reason"] = "涼天心率–功率沒有正斜率，基準線不能用"
        return out
    a, b = f
    lo, hi = min(p["p"] for p in base) - POWER_RANGE_W, max(p["p"] for p in base) + POWER_RANGE_W
    recent = [p for p in fresh if lo <= p["p"] <= hi][-N_RECENT:]
    if len(recent) < N_RECENT:
        out["reason"] = f"最近的涼天穩定跑只有 {len(recent)} 次落在基準線的功率範圍（{lo:.0f}–{hi:.0f} W）"
        return out
    res = [p["hr"] - (a + b * p["p"]) for p in recent]
    noise = [p["hr"] - (a + b * p["p"]) for p in base]
    shift = statistics.median(res)
    side = sum(1 for r in res if (r > 0) == (shift > 0) and r != 0)
    out.update(shift_bpm=round(shift, 1), direction="up" if shift > 0 else "down", n=len(recent), same_side=side,
               noise_bpm=round(statistics.pstdev(noise), 1), line={"a": round(a, 2), "b": round(b, 4),
                                                                   "n": len(base)},
               recent=[{"date": p["date"], "p": round(p["p"]), "hr": round(p["hr"]), "temp_c": p["temp_c"],
                        "residual": round(r, 1)} for p, r in zip(recent, res)])
    if abs(shift) > SHIFT_BPM and side >= N_SAME_SIDE:
        out["fired"] = True
    else:
        out["reason"] = (f"最近 {len(recent)} 次涼天同功率心率偏 {shift:+.1f} bpm（{side}/{len(recent)} 次同方向）："
                         f"沒有持續偏 > {SHIFT_BPM:.0f} bpm")
    return out


def day_temps(acts: list[dict]) -> list[tuple[dt.date, float]]:
    """[(date, median temp °C)] of the days with activity weather, oldest first."""
    by: dict = {}
    for a in acts:
        t = _f(a.get("temp_c"))
        if t is None or not a.get("date"):
            continue
        by.setdefault(a["date"][:10], []).append(t)
    return sorted((dt.date.fromisoformat(d), statistics.median(v)) for d, v in by.items())


def cool_season(acts: list[dict], today: dt.date) -> Optional[dict]:
    """The latest summer → cool transition on or before `today` (module doc):
    {"start", "days", "summer_days", "summer_share"}; None without one."""
    days = [(d, t) for d, t in day_temps(acts) if d <= today]
    found = None
    for i in range(len(days) - SPELL_DAYS + 1):
        spell = days[i:i + SPELL_DAYS]
        if not all(t < COOL_C for _, t in spell):
            continue
        if i > 0 and days[i - 1][1] < COOL_C:
            continue                                  # not the spell's first day
        start = spell[0][0]
        before = [t for d, t in days if start - dt.timedelta(days=SUMMER_DAYS) <= d < start]
        hot = sum(1 for t in before if t >= COOL_C)
        if before and hot >= SUMMER_MIN and hot / len(before) >= SUMMER_SHARE:
            found = {"start": start.isoformat(), "days": [d.isoformat() for d, _ in spell],
                     "summer_days": hot, "summer_share": round(hot / len(before), 2)}
    return found


# ---------------------------------------------------------------------------
# what was tested since
# ---------------------------------------------------------------------------

def tested_since(plan, since: str) -> set:
    """Which of tt30 / cp / aet the plan has a measured result for, dated ≥ `since`."""
    from backend.engine.planning import threshold_method
    out = set()
    for t in getattr(plan, "thresholds", None) or []:
        if (t.date or "")[:10] < since:
            continue
        if t.cp is not None:
            out.add("cp")
        if t.lthr is not None and threshold_method(t, "lthr") != "estimate":
            out.add("tt30")
        if t.aethr is not None and threshold_method(t, "aethr") != "estimate":
            out.add("aet")
    return out


def _suggestion(sid: str, tests: list, title: str, text: str, detected, earliest=None, evidence=None,
                source: str = SRC) -> dict:
    return {"id": sid, "kind": "test_suggestion", "tests": list(tests), "title": title, "text": text,
            "earliest": None if earliest is None else _iso(earliest), "detected": _iso(detected),
            "conditions": [f"{TEST_LABEL[t]}：{CONDITIONS[t]}" for t in tests], "caveat": WRIST_NOTE,
            "estimate": True, "source": source, "evidence": evidence or {}}


def applied_events(plan, today: dt.date) -> list[dict]:
    """Tests applied in the last 14 days: the zones were recomputed from that day."""
    from backend.engine.planning import threshold_row
    out = []
    for name, label in (("cp", "CP"), ("lthr", "LTHR"), ("aethr", "AeT")):
        rows = [t for t in getattr(plan, "thresholds", None) or [] if getattr(t, name, None) is not None
                and 0 <= (today - _date(t.date)).days <= APPLIED_DAYS]
        if not rows:
            continue
        t = max(rows, key=lambda r: r.date)
        r = threshold_row(plan, name, today) if name != "cp" else None
        unit = "W" if name == "cp" else "bpm"
        how = (r or {}).get("label") or f"測試 {t.date[:10]}"
        out.append({"id": "test_applied", "kind": "zone_update", "field": name, "date": t.date[:10],
                    "value": float(getattr(t, name)),
                    "text": f"{label} {float(getattr(t, name)):.0f} {unit}（{how}）：從 {t.date[:10]} 起區間已重算"})
    return out


def suggestions(ds, plan, today: dt.date, acts: Optional[list] = None, brk: Optional[dict] = None,
                points: Optional[list] = None) -> dict:
    """{"suggestions": [...], "events": [...], "checks": {...}} — see the module doc.
    `acts`: activity_weather rows (heat_data.exposures); `brk`: reentry.find;
    `points`: steady_points (tests pass them)."""
    if acts is None:
        from backend.engine import heat_data as HD
        acts = HD.exposures()[0]
    out, checks = [], {}
    weather = weather_of(ds, acts) if acts else {}
    # road runs only: a hike or trail run up a mountain is cool in August
    road = {w.idx: w for w in ds.workouts if w.sport == "run" and "runningtrail" not in (w.tags or [])}
    run_days = [{"date": road[i].entry.start.date().isoformat(), "temp_c": v["temp_c"]}
                for i, v in weather.items() if i in road]
    # ≥ 4 weeks without running
    if brk is None:
        try:
            from backend.engine import reentry as RE
            brk = RE.find(ds, today)
        except Exception:                           # noqa: BLE001
            brk = None
    if brk and brk.get("days", 0) >= BREAK_DAYS and brk.get("return") and brk["return"] <= today.isoformat():
        todo = [t for t in ("cp", "tt30", "aet") if t not in tested_since(plan, brk["return"])]
        checks["break"] = {"days": brk["days"], "return": brk["return"], "end": brk.get("end"), "stale": bool(todo)}
        if todo:
            out.append(_suggestion(
                "break", todo, f"停跑 {brk['days']} 天：門檻已過期，恢復期後重測",
                f"停跑 ≥ 4 週，同功率心率會升高（停訓 2–4 週次大心率約 +11 bpm）；區間先照用，"
                f"{brk.get('end') or brk['return']} 恢復期結束後再測", today, brk.get("end") or brk["return"],
                {"break_days": brk["days"], "return": brk["return"], "block_end": brk.get("end")},
                SRC + "；docs/research/detraining.md（Coyle 1986、Houmard 1992）；engine/reentry.py"))
    # cool-day HR-at-power shift
    if points is None:
        try:
            points = steady_points(ds, today, weather) if weather else []
        except Exception:                           # noqa: BLE001 — no detector, no suggestion
            points = []
    sh = hr_shift(points, today)
    checks["hr_shift"] = {k: sh[k] for k in ("fired", "shift_bpm", "direction", "n", "same_side", "noise_bpm",
                                             "reason")}
    if sh["fired"]:
        last = sh["recent"][-1]["date"]
        first = sh["recent"][0]["date"]
        todo = [t for t in ("tt30", "aet") if t not in tested_since(plan, first)]
        if todo:
            up = sh["direction"] == "up"
            out.append(_suggestion(
                "hr_shift", todo,
                f"涼天同功率心率持續{'升高' if up else '下降'} {abs(sh['shift_bpm']):.0f} bpm：建議重測",
                f"最近 {sh['n']} 次 < {COOL_C:.0f} °C 的穩定路跑，同功率心率比基準線{'高' if up else '低'} "
                f"{abs(sh['shift_bpm']):.1f} bpm（{sh['same_side']}/{sh['n']} 次同方向，基準線雜訊 ±{sh['noise_bpm']} bpm）"
                + ("：可能累積疲勞或體能下降，先確認恢復再測" if up else "：體能可能進步了，CP／AeT 可能偏低"),
                last, None, {k: sh[k] for k in ("shift_bpm", "n", "same_side", "noise_bpm", "line", "recent")}))
    # the first cool spell of the season
    cs = cool_season(run_days, today)
    checks["cool_season"] = cs
    if cs and (today - _date(cs["start"])).days <= SEASON_ACTIVE_DAYS:
        todo = [t for t in ("tt30", "aet") if t not in tested_since(plan, cs["start"])]
        if todo:
            out.append(_suggestion(
                "cool_season", todo, "天氣轉涼了：做一次 30 分鐘測試和 AeT 測試",
                f"{cs['start']} 起連續 {SPELL_DAYS} 個路跑日 < {COOL_C:.0f} °C（之前 {SUMMER_DAYS} 天有 "
                f"{cs['summer_days']} 天 ≥ {COOL_C:.0f} °C）：夏天測的門檻受熱影響，涼天重測比較準",
                cs["start"], None, cs, SRC + "；徐國峰：等天氣轉涼再做（xu-guofeng-reply.md）"))
    return {"suggestions": out, "events": applied_events(plan, today), "checks": checks}
