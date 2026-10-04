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
  hr_shift      the HR at the same power moved and stays moved, heat-adjusted
                (§2.5 item 3). Every steady road run counts, hot or cool —
                in Taiwan days < 25 °C are a minority of the year, and a
                filter on them left the detector without runs most months.
                Each run's median steady HR is moved to Hadley 120 with the
                athlete's own β (engine/heat_calib.hr_beta; one runner's fit 0.224 ± 0.036 bpm per Hadley unit,
                the heat back-test): HR' = HR − β·(Hadley − 120). Reference
                line HR' = a + b·P over the steady runs of the 365 days before
                the recent ones (≥ 8 runs); recent = the last 6–8 steady runs
                of the last 90 days inside the line's power range. Shift =
                median residual, SE² = (the line's SE at the recent mean
                power)² + (√(π/2)·σ/√m, σ² = residual var + (β·σ_H)² for runs
                whose heat is uncertain)² + (SE_β·|H̄_recent − H̄_base|)². Fires
                when |shift| > max(5 bpm, 2·SE) and ≥ 5/6 of the recent runs
                are on the same side. 5 bpm / 6 runs are B3's shift rule
                (unsourced-rules.md B3 — 推估); 2·SE, the 8 / 90 days and the
                5/6 share are 推估.
                Seasonal sanity check: the same residual on last year's runs
                within ±30 days of the recent dates. If ≥ 4 of them exist, the
                shift must also exceed the threshold after subtracting last
                year's same-season residual — the back-test's β differs by
                season (early summer 0.149 vs late summer 0.260), so a single
                β can leave a few bpm of season in the residual; last year's
                same season shows how much, and a shift that the season
                explains is not a fitness change (推估).
  break         ≥ 4 weeks without running (engine/reentry.py, a block of
                29+ days): thresholds are stale (detraining: submaximal HR
                +11 bpm within 2–4 weeks, Coyle 1986 / Houmard 1992,
                docs/research/detraining.md) — retest after the re-entry block.
  cool_season   the first cool spell after summer: 3 road-run days in a row
                whose DAWN (05–07 h, Open-Meteo archive at home, heat_data.
                morning_weather) is < 25 °C and below Hadley 150, after a
                summer (≥ 10 run days with a dawn ≥ Hadley 150 and ≥ half of
                them in the 60 days before; hikes / trail runs left out). The
                dawn, not the run's own time: the test would be run at dawn,
                and an evening run after rain at 24 °C is not a season. Days
                without a cached dawn use the run's own archive weather (a
                later hour is warmer — it can only delay the spell). The watch
                temperature is never used here (the wrist warms it: +3.7 °C
                median vs the archive on one runner's 72 paired route
                efforts). 25 °C is 台灣教練's line; Hadley 150 is Hadley's hot
                band (route_weather.HOT_HADLEY); 3 days / 60 days / the summer
                test are 推估.

  aet_bound     the AeT is only a lower bound (drift_agg.aet_validity: the
                regression found no crossing, threshold_estimate.aet_lower_bound
                holds): one AeT test every 8 weeks since the last AeT test
                (推估), "priority": "low" — the box only, the testing indicator
                stays as it is. Id aet_bound:<cycle start>: a dismissal holds
                for that 8-week cycle.

Temperature source per run (hr_shift), best first:
  route_weather  Open-Meteo archive at the run's place and hours
                 (activity_weather.json), Hadley from T + dew point. σ_H 0.
  watch          the FIT temperature channel, mean after the first 10 min,
                 minus the wrist bias (median watch − archive on this
                 dataset's paired runs when ≥ 10, else the athlete's route
                 efforts: +3.7 ± 2.7 °C, 72 pairs) and the RH of the
                 athlete's archive rows within ±15 days of the year. σ_H from
                 the bias SD and an RH ±10 % — lower confidence, 推估.
  season         no temperature at all (the archive lags a few days): the
                 median archive Hadley of the athlete's days within ±15 days
                 of the year, σ_H = their SD (≥ 5 rows) — lowest confidence.
  none           left out (counted in the evidence).

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

from backend.engine import heat as HT

COOL_C = 25.0                 # 台灣教練：熱天心率偏高、飄移失真
HOT_HADLEY = HT.HOT_HADLEY    # Hadley's 151–160 band (route_weather.HOT_HADLEY)
SHIFT_BPM = 5.0               # B3 shift rule (unsourced-rules.md; §2.5 item 3) — 推估
SE_K = 2.0                    # 推估: the shift must also exceed 2 SE of itself
N_RECENT = 6                  # B3: the last 6 points — 推估 (the minimum)
N_RECENT_MAX = 8              # 推估: up to the last 8 runs of the window
SAME_SIDE_SHARE = 5.0 / 6.0   # 推估: one wrist-HR outlier allowed out of 6
BASE_MIN = 8                  # 推估: steady runs needed for the reference line
BASE_DAYS = 365               # 推估: the reference line's window before the recent runs
RECENT_DAYS = 90              # 推估: the recent runs must be this fresh
SEASON_WIN_D = 30             # 推估: last year's same season = the recent dates − 365 ± 30 days
SEASON_MIN = 4                # 推估: same-season runs needed to use the check
MEDIAN_EFF = math.sqrt(math.pi / 2.0)   # SE of a median ≈ 1.2533 × SE of a mean (normal)
SETTLE_S = 120.0              # 推估: optical HR after a change of effort is not used (first 2 min)
SEG_MIN_S = 600.0             # heat_data.steady_segments: ≥ 10 min steady, flat, after the first 10 min
FLAT_G, POWER_CV, SKIP_S = 0.03, 0.10, 600.0
POWER_RANGE_W = 10.0          # 推估: compare only within the reference runs' power range ± 10 W
# watch temperature → air: the wrist warms the sensor. The athlete's own route
# efforts with both (routes index temp_c vs wx.temp_c, 72 pairs, measured
# 2026-10-02): watch − air median +3.7 °C, SD 2.7 °C. A dataset with ≥ 10 pairs
# of its own uses those instead. No literature source (heat-acclimation.md §2.4).
WATCH_BIAS_C, WATCH_BIAS_SD_C, WATCH_PAIR_MIN = 3.7, 2.7, 10
# RH when a season has too few archive days: the athlete's own median (engine/heat_calib
# humidity_default, ≥ 20 activities; one runner's 608 gave 83 %), else 60 % (推估); ±10 % 推估
RH_DEFAULT, RH_SD = 60.0, 10.0
CLIMATE_WIN_D, CLIMATE_MIN = 15, 5   # 推估 (heat_data.HOT_MONTH_WINDOW_D)
BREAK_DAYS = 28               # ≥ 4 weeks (detraining.md; reentry 29–56 / long blocks)
SPELL_DAYS = 3                # 推估: activity days in a row with a cool dawn
SUMMER_DAYS, SUMMER_MIN, SUMMER_SHARE = 60, 10, 0.5   # 推估
SEASON_ACTIVE_DAYS = 60       # 推估: the cool-season suggestion stays this long after the spell starts
APPLIED_DAYS = 14             # 推估

SRC_LABEL = {"route_weather": "Open-Meteo 路線歷史天氣", "watch": "手錶溫度（扣掉手腕體溫偏差，較不準）",
             "season": "同季節的歷史平均（沒有當天溫度，最不準）"}
WRIST_NOTE = ("手腕光學心率在變速和低溫時誤差較大（推估）：用固定強度、穩定配速，"
              "每次變速後的前 1–2 分鐘心率不採用。腕式對胸帶 rc 0.67–0.92 vs 0.996（Gillinov 2017），"
              "光學 MAE 4.5–14 bpm（Gielen 2026），腕式反應較慢（徐國峰）")
TEST_LABEL = {"tt30": "30 分鐘獨跑測試（LTHR＋CP 檢查）", "aet": "AeT 測試", "cp": "CP 測試",
              "hrmax": "最大心率測試"}          # hrmax: engine/threshold_confidence.py (SP-64)
CONDITIONS = {
    "tt30": "< 25 °C、平路；暖身 15 分後 30 分鐘均勻用力（不要衝開頭），第 10 分按 lap；"
            "LTHR＝後 20 分平均心率，30 分平均功率和 CP 比對（Friel；Jones 2019）",
    "aet": "< 25 °C、平地；固定功率 40–60 分（Uphill Athlete）或固定 E 配速 90 分（徐國峰），不要調速",
    "cp": "< 25 °C；照課表偏好的 CP 測試方式，全力段要真的全力",
    "hrmax": "戴胸帶、休息充足、沒生病；暖身 15 分後 3 趟 2–3 分鐘上坡，最後一趟全力（Polar）；"
             "有心血管風險的人不要做，不舒服立刻停",
}
SRC = "門檻在事件後重測：CP 變化、停跑、季節轉換（Friel；推估）"


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


def _doy_gap(a: dt.date, b: dt.date) -> int:
    """Days between two dates' places in the year (wrapping at New Year)."""
    dd = abs((a.replace(year=2000) - b.replace(year=2000)).days)
    return min(dd, 366 - dd)


# ---------------------------------------------------------------------------
# weather per run
# ---------------------------------------------------------------------------

def weather_of(ds, acts: list[dict]) -> dict:
    """{workout idx: {"temp_c", "hadley"}} from activity_weather rows: by file,
    else the same activity by start (engine/activity_key.py: another source's
    file, a WKO5 name's start), else the only row of that date."""
    from backend.engine.activity_key import ByStartDict
    by_file = ByStartDict({a.get("file"): a for a in acts if a.get("file")})
    by_date: dict = {}
    for a in acts:
        if a.get("date"):
            by_date.setdefault(a["date"], []).append(a)
    out = {}
    for w in ds.workouts:
        f = getattr(getattr(w, "entry", None), "file", None)
        a = by_file.find(f, getattr(getattr(w, "entry", None), "start", None))
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


def watch_temp(ds, w) -> Optional[float]:
    """Mean FIT temperature channel after the first 10 minutes (the whole run
    when shorter); None without one."""
    try:
        tc = ds.channel(w.idx, "temperature")
    except Exception:                               # noqa: BLE001 — a test double, a broken file
        return None
    if tc is None:
        return None
    try:
        vals = [float(v) for v in tc]
    except (TypeError, ValueError):
        return None
    try:
        t = ds.channel(w.idx, "elapsedtime")
    except Exception:                               # noqa: BLE001
        t = None
    good = [v for v in vals if math.isfinite(v)]
    if t is not None and len(t) == len(vals):
        t0 = next((float(x) for x in t if math.isfinite(float(x))), 0.0)
        late = [v for v, x in zip(vals, t) if math.isfinite(v) and math.isfinite(float(x)) and float(x) - t0 >= SKIP_S]
        good = late or good
    return statistics.fmean(good) if good else None


def _season_rows(acts: list[dict], day: dt.date, key: str) -> list[float]:
    out = []
    for a in acts:
        v = _f(a.get(key))
        try:
            d = dt.date.fromisoformat(str(a.get("date"))[:10])
        except ValueError:
            continue
        if v is not None and _doy_gap(d, day) <= CLIMATE_WIN_D:
            out.append(v)
    return out


def watch_bias(pairs: list[tuple[float, float]]) -> dict:
    """{"bias_c", "sd_c", "n", "src"}: median and SD of watch − archive °C over
    this dataset's paired runs when ≥ 10, else the athlete's route efforts."""
    if len(pairs) >= WATCH_PAIR_MIN:
        d = [wt - air for air, wt in pairs]
        return {"bias_c": statistics.median(d), "sd_c": statistics.pstdev(d), "n": len(d), "src": "dataset"}
    # not enough pairs of the athlete's own: one runner's 72 pairs (推估, single user)
    return {"bias_c": WATCH_BIAS_C, "sd_c": WATCH_BIAS_SD_C, "n": 72, "src": "default_single_user"}


def rh_default() -> float:
    """The RH to assume without a season of archive days (engine/heat_calib humidity_default)."""
    try:
        from backend.engine.heat_calib import current
        return float(current("humidity_default")["value"])
    except Exception:                       # noqa: BLE001
        return RH_DEFAULT


def heat_from_watch(t_watch: float, day: dt.date, acts: list[dict], bias: dict) -> dict:
    """Hadley from a watch temperature: minus the wrist bias, with the RH of the
    athlete's archive days of that season; σ_H from the bias SD and RH ±10 %."""
    rhs = _season_rows(acts, day, "rh_pct")
    rh = statistics.median(rhs) if len(rhs) >= CLIMATE_MIN else rh_default()
    t = t_watch - bias["bias_c"]
    h = HT.hadley_sum(t, rh)
    dh_t = HT.hadley_sum(t + bias["sd_c"], rh) - h
    dh_rh = HT.hadley_sum(t, min(100.0, rh + RH_SD)) - h
    return {"temp_c": round(t, 1), "hadley": round(h, 1), "sigma_h": round(math.hypot(dh_t, dh_rh), 1),
            "src": "watch", "watch_c": round(t_watch, 1), "rh_pct": round(rh)}


def heat_from_season(day: dt.date, acts: list[dict]) -> Optional[dict]:
    hs = _season_rows(acts, day, "hadley")
    if len(hs) < CLIMATE_MIN:
        return None
    return {"temp_c": None, "hadley": round(statistics.median(hs), 1),
            "sigma_h": round(statistics.pstdev(hs), 1), "src": "season"}


def run_heat(ds, runs: list, acts: list[dict], weather: Optional[dict] = None) -> dict:
    """{workout idx: {"hadley", "temp_c", "sigma_h", "src", ...}} for `runs` by
    the module doc's precedence: route_weather → watch → season; a run
    with none of them is left out."""
    weather = weather_of(ds, acts) if weather is None else weather
    watch = {}
    for w in runs:
        t = watch_temp(ds, w)
        if t is not None:
            watch[w.idx] = t
    bias = watch_bias([(weather[i]["temp_c"], t) for i, t in watch.items() if i in weather])
    out = {}
    for w in runs:
        wx = weather.get(w.idx)
        try:
            day = w.entry.start.date()
        except AttributeError:
            continue
        if wx and wx.get("hadley") is not None:
            out[w.idx] = {"temp_c": wx["temp_c"], "hadley": wx["hadley"], "sigma_h": 0.0, "src": "route_weather"}
        elif w.idx in watch:
            out[w.idx] = {**heat_from_watch(watch[w.idx], day, acts, bias), "bias": bias}
        else:
            s = heat_from_season(day, acts)
            if s is not None:
                out[w.idx] = s
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


def road_runs(ds, today: dt.date, days: int) -> list:
    from backend.engine.wko5expr.dataset import date_to_day
    tday = date_to_day(today)
    return [w for w in ds.workouts if w.sport == "run" and tday - days < w.day <= tday + 1
            and "runningtrail" not in (w.tags or [])]


def steady_points(ds, today: dt.date, heat: dict,
                  days: int = BASE_DAYS + RECENT_DAYS + SEASON_WIN_D) -> list[dict]:
    """[{date, idx, p, hr, seconds, hadley, sigma_h, src, temp_c}] of every
    road run with power in the last `days` days — one point per run, any
    temperature; hadley None when `heat` (run_heat) has nothing for it."""
    from backend.engine.racepower import athlete as A
    out = []
    for w in road_runs(ds, today, days):
        pt = run_point(A.grade_samples(ds, [w]))
        if pt:
            h = heat.get(w.idx) or {}
            out.append({"date": w.entry.start.date().isoformat(), "idx": w.idx, **pt,
                        "hadley": h.get("hadley"), "sigma_h": h.get("sigma_h") or 0.0, "src": h.get("src"),
                        "temp_c": h.get("temp_c")})
    return sorted(out, key=lambda x: x["date"])


# ---------------------------------------------------------------------------
# detectors (pure)
# ---------------------------------------------------------------------------

def _fit(pts) -> Optional[dict]:
    """OLS HR' = a + b·P: {"a", "b", "s" (residual SD, n − 2), "n", "mx", "sxx"}."""
    n = len(pts)
    if n < 3:
        return None
    mx = sum(p for p, _ in pts) / n
    my = sum(h for _, h in pts) / n
    sxx = sum((p - mx) ** 2 for p, _ in pts)
    if sxx <= 0:
        return None
    b = sum((p - mx) * (h - my) for p, h in pts) / sxx
    a = my - b * mx
    rss = sum((h - (a + b * p)) ** 2 for p, h in pts)
    return {"a": a, "b": b, "s": math.sqrt(rss / (n - 2)), "n": n, "mx": mx, "sxx": sxx}


def beta_check(pts: list[dict]) -> Optional[dict]:
    """A cross-check only: β fitted jointly on these runs, HR = a + b·P +
    β·(Hadley − 120) (OLS). Over a year, heat and fitness change together
    (summer is also the base / race season), so this β is confounded — the
    detector keeps the back-test's β, which compared the same routes."""
    n = len(pts)
    if n < 10 or statistics.pstdev(p["hadley"] for p in pts) < 1.0:
        return None                                 # one heat level: β is not identified
    import numpy as np
    X = np.array([[1.0, p["p"], p["hadley"] - HT.HR_BETA_REF] for p in pts])
    y = np.array([p["hr"] for p in pts], float)
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return None
    c = xtx_inv @ X.T @ y
    r = y - X @ c
    s2 = float(r @ r) / (n - 3)
    if not xtx_inv[2, 2] > 0:
        return None
    return {"beta": round(float(c[2]), 3), "se": round(math.sqrt(s2 * xtx_inv[2, 2]), 3), "n": n}


def _count(xs, key="src") -> dict:
    out: dict = {}
    for x in xs:
        out[x.get(key)] = out.get(x.get(key), 0) + 1
    return out


def heat_confidence(recent: list[dict]) -> str:
    """high: every recent run's heat from the route archive; medium: at most a
    third from the watch / the season; low: more (推估)."""
    other = sum(1 for p in recent if p.get("src") != "route_weather")
    if other == 0:
        return "high"
    return "medium" if other <= len(recent) / 3.0 else "low"


CONF_ZH = {"high": "高", "medium": "中", "low": "低"}


def hr_shift(points: list[dict], today: dt.date, beta: Optional[float] = None,
             beta_se: Optional[float] = None) -> dict:
    """The heat-adjusted HR-at-power shift (module doc): {"fired", "shift_bpm",
    "se_bpm", "threshold_bpm", "direction" (up / down), "n", "same_side",
    "need_same_side", "noise_bpm", "line", "recent", "heat", "seasonal",
    "reason"}. `points` are steady runs (steady_points), any order; a point
    without "hadley" is left out (no temperature of any kind)."""
    from backend.engine.heat_calib import hr_beta
    hb = hr_beta()
    beta = hb["beta"] if beta is None else beta
    beta_se = hb["se"] if beta_se is None else beta_se
    allp = sorted(points, key=lambda p: p["date"])
    pts = []
    for p in allp:
        h = _f(p.get("hadley"))
        if h is None:
            continue
        pts.append({**p, "hadley": h, "sigma_h": _f(p.get("sigma_h")) or 0.0,
                    "hr_adj": HT.hr_heat_adjust(p["hr"], h, beta)})
    heat = {"adjusted": True, "beta": beta, "beta_se": beta_se, "ref_hadley": HT.HR_BETA_REF,
            "source": hb["src"], "beta_source": hb["source"] if beta == hb["beta"] else "given", "n_no_temp": len(allp) - len(pts), "confidence": None,
            "recent_sources": {}, "base_sources": {}}
    out = {"fired": False, "shift_bpm": None, "se_bpm": None, "threshold_bpm": None, "direction": None, "n": 0,
           "same_side": 0, "need_same_side": None, "noise_bpm": None, "line": None, "recent": [], "heat": heat,
           "seasonal": None, "reason": None}
    fresh = [p for p in pts if 0 <= (today - _date(p["date"])).days <= RECENT_DAYS]
    if len(fresh) < N_RECENT:
        out["reason"] = (f"最近 {RECENT_DAYS} 天有溫度的穩定路跑只有 {len(fresh)} 次（要 {N_RECENT} 次）"
                         + (f"；{heat['n_no_temp']} 次沒有任何溫度，不能熱校正" if heat["n_no_temp"] else ""))
        return out
    first = _date(fresh[-min(N_RECENT_MAX, len(fresh))]["date"])
    base = [p for p in pts if first - dt.timedelta(days=BASE_DAYS) <= _date(p["date"]) < first]
    if len(base) < BASE_MIN:
        out["reason"] = f"之前 {BASE_DAYS} 天的穩定路跑只有 {len(base)} 次（要 {BASE_MIN} 次才畫得出基準線）"
        return out
    f = _fit([(p["p"], p["hr_adj"]) for p in base])
    if f is None or f["b"] <= 0:
        out["reason"] = "熱校正後的心率–功率沒有正斜率，基準線不能用"
        return out
    a, b, s = f["a"], f["b"], f["s"]
    lo, hi = min(p["p"] for p in base) - POWER_RANGE_W, max(p["p"] for p in base) + POWER_RANGE_W
    recent = [p for p in fresh if lo <= p["p"] <= hi and _date(p["date"]) >= first][-N_RECENT_MAX:]
    if len(recent) < N_RECENT:
        out["reason"] = f"最近的穩定跑只有 {len(recent)} 次落在基準線的功率範圍（{lo:.0f}–{hi:.0f} W）"
        return out
    m = len(recent)
    res = [p["hr_adj"] - (a + b * p["p"]) for p in recent]
    shift = statistics.median(res)
    # SE of the shift: the line at the recent power, the recent runs' own
    # scatter (plus their heat uncertainty), and β's error times the heat gap
    pr = statistics.fmean(p["p"] for p in recent)
    se_line = s * math.sqrt(1.0 / f["n"] + (pr - f["mx"]) ** 2 / f["sxx"])
    var_i = [s * s + (beta * p["sigma_h"]) ** 2 for p in recent]
    se_mean = MEDIAN_EFF * math.sqrt(statistics.fmean(var_i) / m)
    h_r = statistics.fmean(p["hadley"] for p in recent)
    h_b = statistics.fmean(p["hadley"] for p in base)
    se_beta = beta_se * abs(h_r - h_b)
    se = math.sqrt(se_line ** 2 + se_mean ** 2 + se_beta ** 2)
    thr = max(SHIFT_BPM, SE_K * se)
    need = math.ceil(m * SAME_SIDE_SHARE - 1e-9)
    side = sum(1 for r in res if (r > 0) == (shift > 0) and r != 0)
    heat.update(confidence=heat_confidence(recent), recent_sources=_count(recent), base_sources=_count(base),
                recent_hadley=round(h_r, 1), base_hadley=round(h_b, 1), beta_check=beta_check(base),
                se_parts={"line": round(se_line, 2), "runs": round(se_mean, 2), "beta": round(se_beta, 2)})
    # last year's same season, on the same line
    lo_d = _date(recent[0]["date"]) - dt.timedelta(days=365 + SEASON_WIN_D)
    hi_d = _date(recent[-1]["date"]) - dt.timedelta(days=365 - SEASON_WIN_D)
    ly = [p for p in pts if lo_d <= _date(p["date"]) <= hi_d and lo <= p["p"] <= hi]
    seasonal = None
    if len(ly) >= SEASON_MIN:
        r_ly = statistics.median(p["hr_adj"] - (a + b * p["p"]) for p in ly)
        seasonal = {"n": len(ly), "residual_bpm": round(r_ly, 1), "from": lo_d.isoformat(), "to": hi_d.isoformat(),
                    "net_bpm": round(shift - r_ly, 1), "used": True}
    else:
        seasonal = {"n": len(ly), "residual_bpm": None, "net_bpm": None, "used": False,
                    "from": lo_d.isoformat(), "to": hi_d.isoformat()}
    out.update(shift_bpm=round(shift, 1), se_bpm=round(se, 1), threshold_bpm=round(thr, 1),
               direction="up" if shift > 0 else "down", n=m, same_side=side, need_same_side=need,
               noise_bpm=round(s, 1), seasonal=seasonal,
               line={"a": round(a, 2), "b": round(b, 4), "n": f["n"], "power_range": [round(lo), round(hi)]},
               recent=[{"date": p["date"], "p": round(p["p"]), "hr": round(p["hr"]), "hr_adj": round(p["hr_adj"], 1),
                        "hadley": p["hadley"], "src": p.get("src"), "temp_c": p.get("temp_c"),
                        "residual": round(r, 1)} for p, r in zip(recent, res)])
    big = abs(shift) > thr and side >= need
    if big and seasonal["used"] and abs(shift - seasonal["residual_bpm"]) <= thr:
        out["reason"] = (f"同功率心率偏 {shift:+.1f} ± {se:.1f} bpm（已熱校正），但去年同季也偏 "
                         f"{seasonal['residual_bpm']:+.1f} bpm（{seasonal['n']} 次）：扣掉季節只剩 "
                         f"{shift - seasonal['residual_bpm']:+.1f} bpm，比較像季節（熱校正沒完全吃掉），不是體能變化")
    elif big:
        out["fired"] = True
    else:
        out["reason"] = (f"最近 {m} 次穩定路跑同功率心率偏 {shift:+.1f} ± {se:.1f} bpm（已熱校正，{side}/{m} 次同方向）："
                         f"沒有持續偏 > {thr:.1f} bpm（max({SHIFT_BPM:.0f}, {SE_K:.0f}·SE)，推估）")
    return out


def heat_note(sh: dict) -> str:
    """「已依熱指數校正（β …；天氣來源 …；信心 …）」 for the message."""
    h = sh.get("heat") or {}
    srcs = "、".join(f"{SRC_LABEL.get(k, k)} {v} 次" for k, v in sorted((h.get("recent_sources") or {}).items(),
                                                                       key=lambda kv: -kv[1]))
    return (f"已依熱指數校正（β {h.get('beta', 0.0):.2f} ± {h.get('beta_se', 0.0):.2f} bpm/Hadley，"
            f"{'本人回測' if h.get('beta_source') == 'fitted' else '手動' if h.get('beta_source') == 'user' else '預設，推估'}；天氣：{srcs or '—'}；校正信心{CONF_ZH.get(h.get('confidence'), '—')}）")


def day_weather(rows: list[dict], mornings: Optional[dict] = None) -> list[tuple[dt.date, float, Optional[float], str]]:
    """[(date, temp °C, hadley, src)] of the days in `rows` (run days with the
    run's archive weather), oldest first: the day's dawn (`mornings`,
    heat_data.morning_weather) when there is one, else the median of the
    day's run weather."""
    mornings = mornings or {}
    by: dict = {}
    for a in rows:
        if not a.get("date"):
            continue
        by.setdefault(a["date"][:10], []).append(a)
    out = []
    for d, xs in by.items():
        mw = mornings.get(d)
        if mw and _f(mw.get("temp_c")) is not None:
            out.append((dt.date.fromisoformat(d), _f(mw["temp_c"]), _f(mw.get("hadley")), "morning"))
            continue
        ts = [_f(x.get("temp_c")) for x in xs if _f(x.get("temp_c")) is not None]
        if not ts:
            continue
        hs = [_f(x.get("hadley")) for x in xs if _f(x.get("hadley")) is not None]
        out.append((dt.date.fromisoformat(d), statistics.median(ts), statistics.median(hs) if hs else None, "run"))
    return sorted(out)


def day_temps(acts: list[dict]) -> list[tuple[dt.date, float]]:
    """[(date, median temp °C)] of the days with activity weather, oldest first."""
    return [(d, t) for d, t, _, _ in day_weather(acts)]


def _cool(t: float, h: Optional[float]) -> bool:
    return t < COOL_C and (h is None or h < HOT_HADLEY)


def _hot(t: float, h: Optional[float]) -> bool:
    return h >= HOT_HADLEY if h is not None else t >= COOL_C


def cool_season(acts: list[dict], today: dt.date, mornings: Optional[dict] = None) -> Optional[dict]:
    """The latest summer → cool transition on or before `today` (module doc):
    {"start", "days", "summer_days", "summer_share", "basis"}; None without one.
    `acts` = run days ({"date", "temp_c", "hadley"}); `mornings` = their dawns."""
    days = [x for x in day_weather(acts, mornings) if x[0] <= today]
    found = None
    for i in range(len(days) - SPELL_DAYS + 1):
        spell = days[i:i + SPELL_DAYS]
        if not all(_cool(t, h) for _, t, h, _ in spell):
            continue
        if i > 0 and _cool(days[i - 1][1], days[i - 1][2]):
            continue                                  # not the spell's first day
        start = spell[0][0]
        before = [(t, h) for d, t, h, _ in days if start - dt.timedelta(days=SUMMER_DAYS) <= d < start]
        hot = sum(1 for t, h in before if _hot(t, h))
        if before and hot >= SUMMER_MIN and hot / len(before) >= SUMMER_SHARE:
            found = {"start": start.isoformat(), "days": [d.isoformat() for d, *_ in spell],
                     "summer_days": hot, "summer_share": round(hot / len(before), 2),
                     "basis": [src for *_, src in spell],
                     "dawn": [{"date": d.isoformat(), "temp_c": t, "hadley": h} for d, t, h, _ in spell]}
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


SHIFT_KEYS = ("fired", "shift_bpm", "se_bpm", "threshold_bpm", "direction", "n", "same_side", "need_same_side",
              "noise_bpm", "reason")


def suggestions(ds, plan, today: dt.date, acts: Optional[list] = None, brk: Optional[dict] = None,
                points: Optional[list] = None, mornings: Optional[dict] = None,
                aet_validity: Optional[dict] = None) -> dict:
    """{"suggestions": [...], "events": [...], "checks": {...}} — see the module doc.
    `acts`: activity_weather rows (heat_data.exposures); `brk`: reentry.find;
    `points`: steady_points; `mornings`: heat_data.morning_weather (tests
    pass them; with `acts` given and no `mornings`, no dawn is read)."""
    real = acts is None
    if real:
        from backend.engine import heat_data as HD
        acts = HD.exposures()[0]
    out, checks = [], {}
    weather = weather_of(ds, acts) if acts else {}
    # road runs only: a hike or trail run up a mountain is cool in August
    road = {w.idx: w for w in ds.workouts if w.sport == "run" and "runningtrail" not in (w.tags or [])}
    run_days = [{"date": road[i].entry.start.date().isoformat(), "temp_c": v["temp_c"], "hadley": v.get("hadley")}
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
                SRC + "；停訓：Coyle 1986、Houmard 1992"))
    # heat-adjusted HR-at-power shift
    if points is None:
        try:
            runs = road_runs(ds, today, BASE_DAYS + RECENT_DAYS + SEASON_WIN_D)
            points = steady_points(ds, today, run_heat(ds, runs, acts, weather))
        except Exception:                           # noqa: BLE001 — no detector, no suggestion
            points = []
    sh = hr_shift(points, today)
    checks["hr_shift"] = {**{k: sh[k] for k in SHIFT_KEYS},
                          "heat": {k: sh["heat"].get(k) for k in ("adjusted", "beta", "beta_se", "confidence",
                                                                   "recent_sources", "n_no_temp")},
                          "seasonal": sh["seasonal"]}
    if sh["fired"]:
        last = sh["recent"][-1]["date"]
        first = sh["recent"][0]["date"]
        todo = [t for t in ("tt30", "aet") if t not in tested_since(plan, first)]
        if todo:
            up = sh["direction"] == "up"
            ss = sh["seasonal"] or {}
            season_txt = (f"；去年同季偏 {ss['residual_bpm']:+.1f} bpm（{ss['n']} 次），扣掉後仍偏 {ss['net_bpm']:+.1f} bpm"
                          if ss.get("used") else "；去年同季資料不足，沒做季節對照")
            out.append(_suggestion(
                "hr_shift", todo,
                f"同功率心率持續{'升高' if up else '下降'} {abs(sh['shift_bpm']):.0f} bpm（已熱校正）：建議重測",
                f"最近 {sh['n']} 次穩定路跑，同功率心率比基準線{'高' if up else '低'} "
                f"{abs(sh['shift_bpm']):.1f} ± {sh['se_bpm']:.1f} bpm（推估；門檻 {sh['threshold_bpm']:.1f} bpm＝"
                f"max({SHIFT_BPM:.0f}, {SE_K:.0f}·SE)，{sh['same_side']}/{sh['n']} 次同方向，基準線雜訊 ±{sh['noise_bpm']} bpm）；"
                + heat_note(sh) + season_txt
                + ("：可能累積疲勞或體能下降，先確認恢復再測" if up else "：體能可能進步了，CP／AeT 可能偏低"),
                last, None, {k: sh[k] for k in ("shift_bpm", "se_bpm", "threshold_bpm", "n", "same_side",
                                                 "noise_bpm", "line", "recent", "heat", "seasonal")},
                SRC + "；" + (sh.get("heat") or {}).get("source", "")))
    # the first cool spell of the season (the dawn at home, not the run's hour)
    if mornings is None:
        mornings = {}
        if real:
            try:
                from backend.engine import heat_data as HD
                mornings = HD.morning_weather({r["date"] for r in run_days})
            except Exception:                       # noqa: BLE001 — fall back to the runs' own weather
                mornings = {}
    cs = cool_season(run_days, today, mornings)
    checks["cool_season"] = cs
    if cs and (today - _date(cs["start"])).days <= SEASON_ACTIVE_DAYS:
        todo = [t for t in ("tt30", "aet") if t not in tested_since(plan, cs["start"])]
        if todo:
            basis = "清晨（05–07 時，Open-Meteo）" if all(b == "morning" for b in cs["basis"]) else "清晨或跑步當時"
            out.append(_suggestion(
                "cool_season", todo, "天氣轉涼了：做一次 30 分鐘測試和 AeT 測試",
                f"{cs['start']} 起連續 {SPELL_DAYS} 個路跑日{basis} < {COOL_C:.0f} °C、Hadley < {HOT_HADLEY:.0f}"
                f"（之前 {SUMMER_DAYS} 天有 {cs['summer_days']} 天 Hadley ≥ {HOT_HADLEY:.0f}）：夏天測的門檻受熱影響，"
                f"清晨涼的時候重測比較準",
                cs["start"], None, cs, SRC + "；台灣教練：等天氣轉涼再做；Hadley 150"))
    # the AeT is only a lower bound (drift_agg.aet_validity): one AeT test every 8 weeks, low priority
    val = aet_validity
    if val is None and real:
        try:
            from backend.engine import drift_agg as DA
            val = DA.aet_validity(ds, today)
        except Exception:                           # noqa: BLE001
            val = None
    if val and val.get("lower_bound"):
        due = bound_reminder_due(plan, today)
        checks["aet_bound"] = {"value": val.get("value"), "due": due}
        if due:
            from backend.engine import drift_agg as DA
            x = float(val["value"])
            sg = _suggestion(
                f"aet_bound:{due}", ["aet"], f"AeT 目前只知道下限（≥ {x:.0f} bpm）：有空做一次 AeT 測試",
                f"{DA.bound_label(x)}。每 {BOUND_REMIND_DAYS // 7} 週建議一次（推估），不擋課表、可以關掉；"
                f"多跑 {x - 20:.0f}–{x - 10:.0f} bpm 的輕鬆跑，回歸就能自己找出 AeT",
                due, None, {"bound": val.get("bound"), "value": x},
                SRC + "；下限規則與 8 週提醒為推估")
            out.append({**sg, "priority": "low"})
    return {"suggestions": out, "events": applied_events(plan, today), "checks": checks}


BOUND_REMIND_DAYS = 56          # 推估: one AeT test every 8 weeks while the AeT is only a lower bound
BOUND_EPOCH = dt.date(2026, 1, 5)   # a Monday: the 8-week cycles' anchor without any AeT test


def bound_reminder_due(plan, today: dt.date) -> Optional[str]:
    """The start of the current 8-week cycle (its id: one dismissal per cycle) since the last
    AeT test — None while the last test is younger than BOUND_REMIND_DAYS."""
    from backend.engine.planning import threshold_method
    tests = [_date(t.date) for t in getattr(plan, "thresholds", None) or []
             if t.aethr is not None and threshold_method(t, "aethr") != "estimate" and _date(t.date) <= today]
    anchor = max(tests) if tests else BOUND_EPOCH
    days = (today - anchor).days
    if days < (BOUND_REMIND_DAYS if tests else 0):
        return None
    return (anchor + dt.timedelta(days=days // BOUND_REMIND_DAYS * BOUND_REMIND_DAYS)).isoformat()
