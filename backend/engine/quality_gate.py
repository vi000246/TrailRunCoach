"""
間歇門檻 — does the base phase get an interval session this week, and which one.

Design: docs/research/aerobic-base-readiness.md §4–§5. Replaces the old
「連續 3 次輕鬆路跑飄移 < 5%」 rule, which had no source (Uphill Athlete does
not gate intensity on drift; Friel's < 5 % is for 1–2 h *at* AeT; 「3 次」 was ours).

Two questions, answered separately:

1. Is the aerobic base ready (the *method*, `plan.prefs.quality_gate`)?
     auto         measured AeT (a plan aethr row ≤ 16 weeks old) and a LTHR that is
                  not WKO5's default → ua_gap, with friel_drift as a second way to
                  unlock; otherwise → none
     ua_gap       Uphill Athlete: LTHR / AeT − 1 ≤ 10 % (both measured)
     friel_drift  Friel: one run in 8 weeks, avg HR AeT−5…AeT+3, ≥ 60 min after the
                  warm-up, a fair drift_of drift < 5 %
     xu_drift     徐國峰: 90-min flat E run, (HR@90′ − HR@10′) / HR@10′ < 10 %
     plateau      EF flat (< +2 %) after ≥ 8 base weeks (徐國峰 VO2max plateau; EF
                  stands in for the watch VO2max — our choice)
     weeks        ≥ N base weeks (plan.prefs.quality_gate_weeks, default 8; Palladino,
                  Cusick)
     none         no method (Seiler / Koop): the guardrails alone
   Result: unlocked / locked (data there, criterion not met) / missing (the data
   the method needs isn't there). A forced method with missing data is WATCH with
   the reason and falls back to the guardrails — it never locks for good. That
   fallback is our own choice (a missing test shouldn't stop intervals forever).

2. Can this week take one (the *guardrails*, §4.4; base phase, every mode)?
     low-intensity time share ≥ 75 % (and run power < 80 % CP ≥ 75 % when known)
     CTL ramp: ≥ 5 /week → sub-threshold only; ≥ 8 → none (Friel 5–8, coach)
     last week's volume step: > 20 % → none (Nielsen 2014, Damsted 2019); 10–20 % → hold the dose (推估)
     TSB −30…−20 → hold the dose (Friel / TrainingPeaks; < −30 is already a recovery week)
     3:1 recovery week → a 4×1′ fartlek instead of intervals (Palladino)
     48 h from the long run / other hard days → plan_prefs.place() / week_plan
   Base phase gets at most one interval session a week.

The dose steps through the ladder (interval-prescription.md §A5.3), one step
per planned interval session 達標 in the last 8 weeks: Zone 3 3×6′ → 3×8′ →
2×12′, then (Zone 5 open) 5×2′ → 4×3′ → 5×3′ → 4×4′, then V3 / V4 / T+
maintenance. Each step is a library variant fitted to the day
(engine/interval_library.py). The recovery-week fartlek is not a step. A held week repeats
the last step. The step moves by the progression state machine of
docs/research/interval-adaptation.md §4.3 (interval_outcome / dose_step):
達標 forward, 邊界 repeat, 未適應 rest +1 min then back one step, first rep
short = target −5 %. The old "last rep 5 % below the first -> back one" rule is
gone (the WKO5 speakers oppose it).

Other phases keep the old rule: intensity and drift not bad.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

# "xu_signals" (三訊號) was dropped 2026-10-01: stored prefs that still say it fall back to
# auto (plan_prefs.from_settings; evaluate() maps any unknown mode to auto too)
MODES = ("auto", "ua_gap", "friel_drift", "xu_drift", "plateau", "weeks", "none")
WEEKS_RANGE = (2, 16)
LABEL = {"auto": "自動", "ua_gap": "Uphill Athlete 差距法", "friel_drift": "Friel 飄移法",
         "xu_drift": "徐國峰 90 分鐘法", "plateau": "有氧停滯法", "weeks": "週數法",
         "none": "不設門檻（Seiler）"}

SRC_UA = "Uphill Athlete：When to add intensity（AnT/AeT − 1 ≤ 10%，先加 Zone 3）"
SRC_FRIEL = "Friel（TrainingPeaks：Aerobic decoupling < 5%，跑步在 AeT 1–2 小時）"
SRC_XU = "徐國峰（你的筆記：跑者都該懂的跑步數據）"
SRC_SEILER = "Seiler 2010；Seiler & Tønnessen 2009（整個週期都有少量高強度，每週 1–3 次）"
SRC_PALLADINO = "Palladino 基礎期（你的筆記：palladino基礎期訓練）"
SRC_KOOP = "Koop／CTS（6×3 分 RI、上坡）"
SRC_HELGERUD = "Helgerud 2007（4×4 分）"
SRC_OWN = "自訂"

# ---- numbers (the doc's §7 lists which are ours) ----------------------------
UA_GAP_MAX = 0.10
AET_FRESH_DAYS = 16 * 7        # 自訂: a plan AeT older than this is stale for `auto`
LOOKBACK_DAYS = 56             # 自訂: 8-week window for friel / xu / the dose count
FRIEL_HR_BAND = (-5.0, 3.0)    # 自訂: "at AeT" = AeT−5 … AeT+3
FRIEL_MIN_S = 70 * 60          # ≥ 60 min after drift_of's 10-min warm-up
FRIEL_GOOD = 0.05
XU_MIN_S = 90 * 60
XU_GOOD = 0.10
XU_HEAT_C = 25.0               # 徐國峰's condition: advice in the session text, not a refusal (heat bands)
PLATEAU_WEEKS = 8              # 自訂
EF_PLATEAU = 0.02              # status.EF_TREND
LOW_SHARE_MIN = 0.75           # status.LOW_SHARE_GOOD (Seiler, by time)
RAMP_SUB, RAMP_BLOCK = 5.0, 8.0            # status.RAMP elite / short: Friel 5–8, 10 the ceiling (coach; B2)
STEP_HOLD, STEP_BLOCK = 0.10, 0.20         # > 20 % block: Nielsen 2014, Damsted 2019 (peer-reviewed); 10–20 % hold 推估
TSB_HOLD = -20.0                           # Friel / TrainingPeaks TSB bands (coach)
ZONE3_SESSIONS = 3             # 自訂: ua_gap unlock → this many Zone 3 sessions, then the dose table
REP_PCT, REP_MIN_S, DOSE_MIN_REPS = 0.95, 40, 4   # 自訂: a short-rep session = ≥ 4 bouts ≥ 40 s at ≥ 95 % CP
FADE = 0.05                    # workout_review.FADE

# ---- the dose ladder: Zone 3 first, then Zone 5 (徐國峰, 私訊 2026-10-01) -----------
# 「第一個加進來的質量課表我會先選強度 3 區…等 3 區跑順了、恢復也跟得上，再把 5 區間歇排進來」;
# Zone 5 reps ≥ 2 min, ≤ 2 sessions a week, ≥ 2 days apart (徐國峰). Zone 5 also needs the
# aerobic base confirmed (engine/base_check.z5_status). The old ladder started with 5×1′ @ 98–101 % CP —
# in his terms too short to train VO2max yet a Zone 5 load (xu-guofeng-reply.md §3).
# key, title, reps, work min, rest min, %CP lo, hi, uphill, source — each rung's standard
# session (the canonical variant of engine/interval_library.py; interval-prescription.md §A5.3,
# corrected 2026-10-01): Zone 3 3×6 → 3×8 → 2×12 at 90–95 % CP (Haugen 2022 / Palladino / Daniels —
# not Seiler 2013, whose 4×8′ ran at ~90 % HRpeak, 9.6 mmol/L: severe, not Zone 3); Zone 5 5×2
# (106–112 %, 2′ walk) → 4×3 (3′ jog) → 5×3 (2.5′ walk) → 4×4 (104–108 %, 3′ jog; Helgerud 2007).
# Rests < 2–3 min are walks (Buchheit & Laursen 2013: passive recovery below 2–3 min).
from backend.engine import interval_library as _IL  # noqa: E402


def _rung_row(rung: str) -> tuple:
    v = _IL.canonical(rung)
    return (rung, _IL.title(v), v.n, v.work_s / 60.0, v.rest_s / 60.0, v.lo, v.hi, v.terrain == "hill", v.src)


Z3 = tuple(_rung_row(r) for r in ("z3a", "z3b", "z3c"))
Z5 = tuple(_rung_row(r) for r in ("z5a", "z5b", "z5c", "z5d"))
TP = _rung_row("tp")             # T+ near-threshold: maintenance once the Z5 rungs are done (§A5.3)
LADDER = Z3 + Z5
Z3_MET_FOR_Z5 = len(Z3)        # 推估: 3 sessions 達標 at Zone 3 (the Z3 rungs) = 「3 區跑順了」
# legacy titles of the old ladders: not counted as steps any more (neutral in planned_spec).
# The old z3a 「閾值 3×8 分」 is the new second rung's title: a title-only row reads as z3b now.
LEGACY_TITLES = ("短間歇 5×1 分", "短間歇 6×1 分", "爬坡間歇 4×3 分", "間歇 5×3 分", "VO2max 間歇 4×4 分",
                 "閾值下 3×8 分", "閾值下 4×8 分", "閾值 4×8 分", "閾值 3×10 分")
DOSE = Z3                      # kept for callers that read the first rungs (adapt._downgrade)
RECOVERY = ("r1", "恢復週 fartlek 4×1 分", 4, 1, 2, 0.98, 1.01, False, "Palladino 恢復週保留 98–101% CP fartlek")
# the ramp-week session (CTL ramp ≥ 5: threshold only) — Z3[0]'s content under its own key / title so
# it is never mistaken for the ladder's first rung (planned_spec: neutral)
SUB = ("sub", "閾值 3×6 分（只排閾值）", 3, 6, 1.5, 0.90, 0.95, False, "CTL ramp ≥ 5（Friel）：只排閾值；90–95% CP")
ZONE3 = ("z3", "Zone 3 間歇", 3, 6, 2, None, None, False, "Uphill Athlete：先加 Zone 3（AeT–LTHR），約週有氧量的 5%")


def dose_spec(step: int, z5_open: bool = True) -> tuple:
    """The ladder rung for `step` (達標 count): Z3 rungs first; from step 3
    Z5 rungs only while Zone 5 is open — else the top Z3 rungs alternating
    (Zone 3 continues; 徐國峰). After the Z5 rungs: maintenance rotating V3, V4
    and T+ (near-threshold) — T+ every 3rd week (interval-prescription.md §C5.2-4, 推估)."""
    step = max(0, int(step))
    if step < len(Z3):
        return Z3[step]
    if not z5_open:
        return Z3[1 + step % 2]
    k = step - len(Z3)
    if k < len(Z5):
        return Z5[k]
    return (Z5[2], Z5[3], TP)[(k - len(Z5)) % 3]


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


# ---------------------------------------------------------------------------
# thresholds
# ---------------------------------------------------------------------------

def aet_row(plan, today: dt.date):
    """The plan threshold row whose aethr is in effect on `today` (the
    threshold_on rule: latest on or before, else the earliest)."""
    rows = sorted((t for t in (getattr(plan, "thresholds", None) or []) if t.aethr is not None),
                  key=lambda t: t.date)
    best = None
    for t in rows:
        if dt.date.fromisoformat(t.date) <= today:
            best = t
    return best or (rows[0] if rows else None)


def aet_info(plan, today: dt.date) -> dict:
    r = aet_row(plan, today)
    if r is None:
        return {"value": None, "date": None, "measured": False, "fresh": False, "age_days": None, "label": ""}
    d = dt.date.fromisoformat(r.date)
    age = (today - d).days
    note = r.note or ""
    how = "活動資料估算" if "自動估算" in note else f"{r.date} 飄移測試" if "飄移測試" in note else f"{r.date} 實測"
    return {"value": float(r.aethr), "date": r.date, "measured": True, "fresh": age <= AET_FRESH_DAYS,
            "age_days": age, "label": f"AeT {r.aethr:.0f}（{how}）"}


def lthr_info(ds, plan, today: dt.date) -> dict:
    """LTHR in effect and whether it is still WKO5's untouched default (i_data's rule)."""
    v = plan.threshold_on("lthr", today) if plan is not None else None
    if v is not None:
        return {"value": float(v), "default": False, "source": "plan"}
    ath = getattr(ds, "athlete", None)
    hist = (getattr(ath, "settings", None) or {}).get("runthr") or []
    val = None
    try:
        val = _f(ath.setting_on("runthr", today)) if ath is not None else None
    except Exception:
        val = None
    default = bool(hist) and all(d == dt.date(1980, 1, 1) for d, _ in hist)
    return {"value": val, "default": default, "source": "wko5"}


def ua_gap(aet: Optional[float], lthr: Optional[float]) -> Optional[float]:
    """Uphill Athlete's spread: AnT / AeT − 1 (150 ÷ 128 = 1.17 → 17 %)."""
    if not aet or not lthr:
        return None
    return lthr / aet - 1.0


def ua_gap_method(ae: dict, lt: dict) -> dict:
    """The ua_gap method on aet_info / lthr_info: unlocked / locked / missing
    (evaluate's method("ua_gap") and the Zone 5 path aet_ua_gap)."""
    if not ae.get("measured"):
        return {"state": "missing", "verdict": "沒有實測 AeT，差距法算不出來"}
    if lt.get("value") is None or lt.get("default"):
        return {"state": "missing", "verdict": "LTHR 還是 WKO5 預設值，差距法算不出來"}
    g = ua_gap(ae["value"], lt["value"])
    txt = f"AeT {ae['value']:.0f} / LTHR {lt['value']:.0f}：差距 {g * 100:.0f}%"
    if g <= UA_GAP_MAX:
        return {"state": "unlocked", "verdict": f"{txt} ≤ 10%：可以加 Zone 3",
                "prefix": f"AeT–LTHR 差距 {g * 100:.0f}% ≤ 10%：", "gap": g}
    return {"state": "locked", "verdict": f"{txt}（> 10%，有氧不足）",
            "action": "繼續基礎：輕鬆跑壓在 AeT 以下＋坡衝刺（3 區照排）；聚合估計不準或偏移時再測 AeT", "gap": g}


# ---------------------------------------------------------------------------
# drift methods
# ---------------------------------------------------------------------------

def _runs(ds, today: dt.date, days: int = LOOKBACK_DAYS):
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    return [w for w in ds.workouts if tday - days < math.floor(w.day) <= tday and w.sport == "run"]


def _band_fields(dr: dict) -> dict:
    """The run's temperature band for the gate texts (workout_review.temp_band)."""
    from backend.engine import workout_review as WR
    band = dr.get("temp_band") or WR.temp_band(dr.get("temp_c"))
    return {"band": band, "heat": WR.is_heat(band), "chip": "🌡 " + WR.TEMP_BAND_LABEL.get(band, "溫度不明"),
            "temp_c": dr.get("temp_c")}


def heat_suffix(run: dict, passed: bool) -> str:
    """Heat bands and the gates (Friel / 徐國峰 / AeT test): a hot-band run
    still counts. Heat inflates the drift (Lafrenz 2008; Beiter 2025), so a
    pass in heat is conservative and unlocks; a fail in heat is reported as
    possibly heat-inflated (and stays a fail)."""
    if not run.get("heat"):
        return ""
    from backend.engine import workout_review as WR
    return (f"（{run['chip']}，{WR.HEAT_NOTE}；熱天通過仍算數）" if passed
            else f"（{run['chip']}，{WR.HEAT_NOTE}，可能是熱造成的）")


def friel_check(ds, today: dt.date, aet: Optional[float]) -> dict:
    """Friel: one steady run near AeT, ≥ 60 min after the warm-up, fair drift < 5 %.
    missing = no such run in 8 weeks; locked = runs there, all ≥ 5 %. Every
    temperature band counts (heat_suffix: a pass in heat unlocks)."""
    from backend.engine import workout_review as WR
    if not aet:
        return {"state": "missing", "reason": "沒有實測 AeT，飄移法沒有基準"}
    lo, hi = aet + FRIEL_HR_BAND[0], aet + FRIEL_HR_BAND[1]
    cands = []
    for w in _runs(ds, today):
        if "runningtrail" in w.tags or (_f(w.metrics.get("duration")) or 0) < FRIEL_MIN_S:
            continue
        m = WR.measure(ds, w)
        if not m or (m.get("moving_s") or 0) < FRIEL_MIN_S:
            continue
        hr, dr = m.get("avg_hr"), m.get("drift") or {}
        if hr is None or not lo <= hr <= hi or not dr.get("ok"):
            continue
        cands.append({"idx": w.idx, "date": WR._wdate(w).isoformat(), "drift": dr["drift"], "hr": hr,
                      **_band_fields(dr)})
    WR._flush(ds)
    if not cands:
        return {"state": "missing", "reason": f"8 週內沒有 ≥ 60 分鐘、平均心率 {lo:.0f}–{hi:.0f} 的平路穩定跑"}
    good = [c for c in cands if c["drift"] < FRIEL_GOOD]
    if good:
        return {"state": "unlocked", "run": good[-1]}
    return {"state": "locked", "run": cands[-1]}


def xu_drift_of(t, hr, a_s: float = 600.0, b_s: float = 5400.0, half_s: float = 60.0) -> Optional[dict]:
    """徐國峰: (HR@90′ − HR@10′) / HR@10′, each the mean over ±1 min."""
    t = np.asarray(t, dtype=float)
    h = np.asarray(hr, dtype=float) if hr is not None else None
    if h is None or len(t) != len(h) or not np.isfinite(t).any():
        return None
    t0 = float(np.nanmin(t))
    if float(np.nanmax(t)) - t0 < b_s + half_s:
        return None

    def at(x):
        sel = (t - t0 >= x - half_s) & (t - t0 <= x + half_s) & np.isfinite(h) & (h > 0)
        return float(h[sel].mean()) if sel.any() else None
    a, b = at(a_s), at(b_s)
    if not a or not b:
        return None
    return {"hr10": a, "hr90": b, "drift": (b - a) / a}


def xu_check(ds, today: dt.date) -> dict:
    """The latest flat ≥ 90-min E run in 8 weeks (drift_of's flat / stop / steady /
    fast-finish checks must pass). Every temperature band counts (heat bands:
    workout_review.heat_band; a pass in heat unlocks, a fail in heat is
    marked possibly heat-inflated — heat_suffix)."""
    # the method stays strict-tier (drift_of's fairness, as before); the Zone 5 path uses
    # base_check.xu_run with 徐國峰's own conditions (stops ≤ 30 s, Zone 1, ≤ 25 °C)
    from backend.engine import workout_review as WR
    last = None
    for w in sorted(_runs(ds, today), key=lambda x: x.day):
        if "runningtrail" in w.tags or (_f(w.metrics.get("duration")) or 0) < XU_MIN_S:
            continue
        m = WR.measure(ds, w)
        if not m or not (m.get("drift") or {}).get("ok"):
            continue
        s = WR._samples(ds, w)
        if s is None:
            continue
        r = xu_drift_of(s["t"], s["hr"])
        if r is not None:
            last = {"idx": w.idx, "date": WR._wdate(w).isoformat(), **r, **_band_fields(m["drift"])}
    WR._flush(ds)
    if last is None:
        return {"state": "missing", "reason": "8 週內沒有 ≥ 90 分鐘、平路、不停的 E 配速跑"}
    return {"state": "unlocked" if last["drift"] < XU_GOOD else "locked", "run": last}


# ---------------------------------------------------------------------------
# the dose history
# ---------------------------------------------------------------------------

def count_reps(t, power, cp: Optional[float]) -> list[dict]:
    """Short work bouts: 10-s power ≥ 95 % CP for ≥ 40 s (gaps < 5 s bridged).
    detect_efforts' 30-s smoothing and 60-s floor miss 1-minute reps."""
    if power is None or not cp:
        return []
    from backend.engine.workout_review import _grid1
    grid, p = _grid1(t, power)
    if grid is None or len(p) < 60:
        return []
    pz = np.nan_to_num(p)
    p10 = np.convolve(pz, np.ones(10) / 10, "same")
    on = p10 >= REP_PCT * cp
    edges = np.diff(np.concatenate([[0], on.astype(int), [0]]))
    segs: list[list[int]] = []
    for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
        if segs and a - segs[-1][1] < 5:
            segs[-1][1] = int(b)
        else:
            segs.append([int(a), int(b)])
    return [{"start_s": float(a), "duration_s": float(b - a), "power": float(pz[a:b].mean())}
            for a, b in segs if b - a >= REP_MIN_S]


def _with_hr_at60(reps: list[dict], s: Optional[dict]) -> list[dict]:
    """count_reps bouts + the HR 60 s after each one ends (徐國峰's 60-s check,
    a brake only); None when the next rep starts before that or there is no HR."""
    from backend.engine.workout_review import _grid1
    h = None
    if s is not None and s.get("hr") is not None:
        h = _grid1(s["t"], s["hr"])[1]
    out = []
    for i, r in enumerate(reps):
        at = int(r["start_s"] + r["duration_s"] + 60)
        nxt = reps[i + 1]["start_s"] if i + 1 < len(reps) else None
        v = None
        if h is not None and at < len(h) and (nxt is None or nxt >= at) and np.isfinite(h[at]):
            v = float(h[at])
        out.append({"power": r["power"], "duration_s": r["duration_s"], "start_s": r["start_s"], "hr_at60": v,
                    **({"source": r["source"]} if r.get("source") else {})})
    return out


def spec_by_title(title: Optional[str]) -> Optional[tuple]:
    """The ladder / recovery / sub row whose title is `title` (None when unknown)."""
    if not title:
        return None
    return next((s for s in LADDER + (RECOVERY, SUB) if s[1] == str(title)), None)


def dose_history(ds, today: dt.date, days: int = LOOKBACK_DAYS) -> list[dict]:
    """Interval sessions in the `days` before `today`, oldest first: a done
    plan quality session, workout_review's `quality` class, a road run with
    ≥ 4 short reps (count_reps) or ≥ 2 Zone 3 reps (interval_reps.find_reps).
    {"idx", "date", "title", "reps", "faded", "bouts", "cp", "rep_source"}.

    The reps come from interval_reps.find_reps against the planned session
    (laps first, then 0.95 × the planned lower bound) — before, a Zone 3
    session at 88–95 % CP had no time ≥ 95 % CP and no bout above
    detect_efforts' median threshold, so it never counted (bug b,
    interval-prescription.md §A5.2-2)."""
    from backend.engine import interval_reps as IR
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    out = []
    try:
        from backend.engine.plan_store import done_plan, plan_in_use
        planned = done_plan()                  # activity index -> the planned session (title, variant)
        in_use = plan_in_use()
    except Exception:                          # noqa: BLE001
        planned, in_use = {}, False
    for w in sorted(ds.workouts, key=lambda x: x.day):
        if not (tday - days <= math.floor(w.day) < tday) or category(w) not in WR.QUALITY_CATEGORIES:
            continue
        if (_f(w.metrics.get("duration")) or 0) < 1200:
            continue
        m = WR.measure(ds, w)
        if not m:
            continue
        row = planned.get(w.idx) or {}
        spec = planned_variant_spec(row)
        c = WR.classify(ds, w, m)
        s = WR._samples(ds, w) if m.get("cp") else None
        found = IR.find_reps(ds, w, s, m.get("cp"), spec) if m.get("cp") and (
            category(w) == "road" or spec is not None) else {"bouts": [], "source": None}
        reps = found["bouts"]
        z3 = found["source"] == "z3" and len(reps) >= 2
        if not row and c["type"] != "quality" and not z3 and \
                (found["source"] != "short" or len(reps) < DOSE_MIN_REPS):
            continue
        if not row and (m.get("hard_s") or 0) < 120 and not z3 and len(reps) < DOSE_MIN_REPS:
            continue
        fade = None
        if len(reps) >= 2 and reps[0].get("power"):
            fade = reps[-1]["power"] / reps[0]["power"] - 1.0
        elif not reps:
            fade = (m.get("intervals") or {}).get("fade")
        if reps:
            bouts = _with_hr_at60(reps, s)
        elif spec is None:
            bouts = [{"power": e.get("power"), "duration_s": e.get("duration_s"), "start_s": e.get("start_s"),
                      "hr_at60": (e["hr_max"] - e["hr_drop60"]) if e.get("hr_max") is not None
                      and e.get("hr_drop60") is not None else None} for e in (m.get("efforts") or [])]
        else:
            bouts = []                         # planned, nothing found: 無法判定 (dose_step), not 「目標太高」
        tiz_ratio = None
        if spec is not None and s is not None and m.get("cp"):
            from backend.engine import interval_eval as IE
            from backend.engine.interval_reps import lo_of, works_of
            lo = lo_of(spec)
            hi = getattr(spec, "hi", None) if hasattr(spec, "hi") else spec[6]
            plan_tiz = sum(works_of(spec))
            t_in = IE.tiz_seconds(s["t"], s["power"], m["cp"], lo, hi, lo >= 1.02)
            tiz_ratio = (t_in / plan_tiz) if t_in is not None and plan_tiz else None
        out.append({"idx": w.idx, "date": WR._wdate(w).isoformat(), "title": row.get("title"),
                    **{k: row.get(k) for k in ("variant_key", "rung_key", "equiv", "swap", "variant_reps",
                                               "variant_adj", "variant_blocks", "steps")
                       if row.get(k) is not None},
                    "reps": len(reps) or (m.get("intervals") or {}).get("n") or 0,
                    # informational only now: dose_step judges the bouts (interval_outcome)
                    "faded": fade is not None and fade < -FADE,
                    "bouts": bouts[:20], "cp": m.get("cp"), "rep_source": found["source"], "tiz_ratio": tiz_ratio,
                    # the stored plan is in use and this run matched none of its quality sessions:
                    # a hard run, not a ladder session (real data 2026-10-01: steady runs at
                    # ~95 % CP were judged 「目標太高」 against 3×8′ and moved the ladder)
                    **({"unplanned": True} if in_use and not row else {})})
    WR._flush(ds)
    return out


def planned_variant_spec(row: dict):
    """The planned spec of a stored plan row: its library variant (variant_key) when
    there is one, else the ladder row by title; None for an unplanned activity."""
    if not row:
        return None
    if user_steps(row):
        # the structure the user edited: its reps / band are what the reps are matched against
        try:
            from backend.engine import interval_library as IL
            from backend.engine import workout_steps as WS
            v = WS.variant_from_steps(row["steps"], row.get("rung_key") or getattr(IL.get(row.get("variant_key")), "rung", None))
            if v is not None:
                return v
        except Exception:                       # noqa: BLE001
            pass
    if row.get("variant_key"):
        try:
            from backend.engine import interval_library as IL
            v = IL.resolve(row["variant_key"], row.get("variant_reps"), row.get("variant_adj"))
            if v is not None:
                return v
        except Exception:                       # noqa: BLE001
            pass
    return spec_by_title(row.get("title"))


# ---------------------------------------------------------------------------
# guardrails (§4.4)
# ---------------------------------------------------------------------------

def guard(low_share: Optional[float] = None, power_low_share: Optional[float] = None,
          ramp: Optional[float] = None, step: Optional[float] = None, tsb: Optional[float] = None,
          aet: Optional[float] = None, injury: Optional[str] = None) -> dict:
    """This week's check: {"block", "sub", "hold", "verdict", "action"} — the
    first failing rule speaks. Missing numbers don't block. `injury`: an open
    傷病紀錄 with 「受傷期間暫停強度課」 ticked (engine/injuries.pause_reason)
    blocks intervals until it is resolved — the user's own choice, so it
    speaks first."""
    out = {"block": False, "sub": False, "hold": False, "verdict": "", "action": "", "rule": ""}
    aet_t = f"{aet:.0f} bpm" if aet else "AeT"

    def say(rule, verdict, action, **flags):
        if not out["rule"]:
            out.update(rule=rule, verdict=verdict, action=action)
        out.update(flags)
    if injury:
        say("injury", injury, "傷病紀錄按「好了」後恢復", block=True)
    if low_share is not None and low_share < LOW_SHARE_MIN:
        say("intensity", f"本週不排間歇：低強度只有 {low_share * 100:.0f}%（< 75%）",
            f"輕鬆跑壓在 {aet_t} 以下，下週再看", block=True)
    if power_low_share is not None and power_low_share < LOW_SHARE_MIN:
        say("intensity", f"本週不排間歇：跑步功率 < 80% CP 只有 {power_low_share * 100:.0f}%（< 75%）",
            f"輕鬆跑壓在 {aet_t} 以下，下週再看", block=True)
    if ramp is not None and ramp >= RAMP_BLOCK:
        say("ramp", f"CTL 每週 +{ramp:.1f}（≥ {RAMP_BLOCK:.0f}，Friel）：本週不排間歇", "先穩住量", block=True)
    elif ramp is not None and ramp >= RAMP_SUB:
        say("ramp", f"CTL 每週 +{ramp:.1f}（≥ {RAMP_SUB:.0f}，Friel）：本週只排閾值下", "先穩住量", sub=True)
    if step is not None and step > STEP_BLOCK:
        say("volume", f"上週量增 {step * 100:+.0f}%（> 20%，Nielsen 2014／Damsted 2019）：本週不排間歇",
            "本週維持上週的量", block=True)
    elif step is not None and step > STEP_HOLD:
        say("volume", f"上週量增 {step * 100:+.0f}%（10–20%，推估）：間歇維持上次的量，不往上加", "", hold=True)
    if tsb is not None and -30.0 <= tsb < TSB_HOLD:
        say("tsb", f"TSB {tsb:+.0f}（−30～−20，Friel／TrainingPeaks）：間歇維持上次的量，不往上加", "", hold=True)
    return out


# ---- the progression state machine (docs/research/interval-adaptation.md §4.3) --
# Power decides, HR only brakes. The old「最後一組比第一組低 > 5% → 退一步」 is
# gone: the WKO5 speakers (Golich, IT2:84-86) judge *which* rep fell out of the
# band — the last one falling off is fine, rep 2 … second-to-last means the
# session was set wrong.
IN_BAND_TOL = 0.98      # 推估 (doc §4.2): a rep is in band at ≥ 98 % of the planned lower bound
TARGET_DOWN = 0.95      # ROLE:499「下修 5～10%」: first rep already short -> target −5 %
AET60_MIN_SHARE = 0.5   # 推估 (doc §4.3): HR back under AeT 60 s into the rest on < half the reps = brake
LAST_FADE = 0.05        # 推估 (doc §4.3): only the last rep missed and it fell > 5 % = 邊界
TIZ_GOAL = 0.85         # 推估 (interval_eval.TIZ_GOAL): time in zone ≥ 85 % of the chosen variant's plan
OUTCOME_LABEL = {"met": "達標", "border": "邊界", "unadapted": "未適應", "too_high": "未適應（目標太高）",
                 "unknown": "無法判定"}


def interval_outcome(bouts: list[dict], spec: tuple, cp: Optional[float], aet: Optional[float] = None) -> dict:
    """未適應 / 邊界 / 達標 for one interval session against the planned `spec`
    (DOSE row). `bouts`: [{"power", "hr_at60"?}] in order. Checked in the
    doc's order: 未適應 first, then 邊界, else 達標. RPE is not recorded, so
    its rows are skipped. {"outcome": None} without CP / a %CP band."""
    planned, lo = int(spec[2]), spec[5]
    if not cp or lo is None or not planned:
        return {"outcome": None, "why": "沒有 CP 或目標功率帶"}
    floor = IN_BAND_TOL * lo * cp
    ps = [float(b.get("power") or 0.0) for b in bouts[:planned]]
    inb = [p >= floor for p in ps]
    done = len(ps) / planned
    miss = next((i + 1 for i, ok in enumerate(inb) if not ok), None)
    if miss is None and done < 1:
        miss = len(ps) + 1                        # stopped early: the first rep not done
    fade = (ps[-1] / ps[0] - 1.0) if len(ps) >= 2 and ps[0] else None
    base = {"first_miss": miss, "done": round(done, 2), "fade": fade}
    if miss == 1:
        return {**base, "outcome": "too_high", "why": f"第 1 趟就沒到 {floor:.0f} W：目標功率下修 5%"}
    if done < 1 or (miss is not None and 2 <= miss <= planned - 1):
        return {**base, "outcome": "unadapted",
                "why": f"第 {miss} 趟掉出目標帶（共 {planned} 趟）" if done >= 1 else f"只完成 {len(ps)}/{planned} 趟"}
    at60 = [b.get("hr_at60") for b in bouts[:planned] if b.get("hr_at60") is not None]
    if aet and at60:
        share = sum(1 for h in at60 if h <= aet) / len(at60)
        if share < AET60_MIN_SHARE:
            return {**base, "outcome": "border", "why": f"休息 60 秒心率回到 AeT 以下只有 {share * 100:.0f}% 的趟"}
    if miss == planned and fade is not None and fade < -LAST_FADE:
        return {**base, "outcome": "border", "why": f"只有最後一趟沒到、掉 {-fade * 100:.0f}%：同一份課表再做一次"}
    return {**base, "outcome": "met", "why": "每一趟都在目標帶" if miss is None else "只有最後一趟略掉（≤ 5%）"}


def dose_step(history: list[dict], aet: Optional[float] = None) -> dict:
    """Next DOSE step by replaying the interval sessions done (oldest first),
    each judged against the step it was planned at (interval_outcome):
      達標 -> next step (the ladder adds reps, then rep length, then power)
      邊界 -> the same step again
      未適應 -> same step, rest + 1 min; a second 未適應 in a row -> back one step
      未適應（目標太高）-> same step, target power −5 %
    A session that can't be judged (no bouts or no CP) is 無法判定 and
    repeats the step — progress only on 達標 (unsourced-rules.md §B4; the
    old rule counted it as 達標 unless it `faded`). A missed session isn't in
    the history: the next week repeats the step (engine/adapt.py rule B).
    `faded` stays for the week card."""
    step, streak, adjust, last = 0, 0, {}, None
    for h in history:
        if h.get("unplanned"):
            h["outcome"] = "neutral"           # not one of the plan's quality sessions
            continue
        by_steps = steps_spec(h, step)
        if by_steps is not None:
            # a structure the user edited in the 課表 editor (engine/workout_steps.py): judged by
            # its own reps / band, counted only when it is an equivalent of the rung (§C2)
            spec, neutral, counted = by_steps
        elif h.get("variant_key"):
            # judged by the stored variant (interval-prescription.md §C5.4) — not by the title,
            # which a shortened session changed (bug a: the 4×8′ / 3×10′ steps never moved)
            spec, neutral, counted = variant_spec(h, step)
        else:
            spec, neutral = planned_spec(h.get("title"), step)
            counted = True
        if neutral:
            # a recovery fartlek / sub-threshold (ramp week) / Zone 3 session the plan
            # prescribed outside the ladder: not a step, never judged against it
            h["outcome"] = "neutral"
            continue
        if h.get("bouts") and h.get("cp"):
            o = interval_outcome(h["bouts"], spec, h["cp"], aet)
        else:
            o = {"outcome": "unknown", "why": "沒有功率或 CP，無法判定達標：同一階再做一次"}
        oc = o.get("outcome") or "unknown"
        r = h.get("tiz_ratio")
        if oc == "met" and r is not None and r < TIZ_GOAL:
            # interval_eval's verdict: every rep in band but too little time in the zone (stopped
            # early, reps short) = 部分達到 → the same step again (≥ 85 % of the plan: 推估, §C2)
            oc = "border"
            o = {**o, "outcome": oc, "why": f"目標區時間只有計畫的 {r * 100:.0f}%（< 85%）"}
        h["outcome"] = oc
        if not counted:
            # a 縮量版 / non-equivalent swap / the step before under a tight cap: shown, but the
            # rung doesn't move (§C5.4 「判定結果只顯示，不影響階數」)
            h["counted"] = False
            continue
        last = {**o, "outcome": oc, "date": h.get("date"), "step": step}
        if oc == "met":
            step, streak, adjust = step + 1, 0, {}
        elif oc in ("border", "unknown"):
            streak, adjust = 0, {}
        elif oc == "too_high":
            streak, adjust = 0, {"power": TARGET_DOWN}
        else:
            if streak >= 1:
                step, adjust = max(0, step - 1), {}
            else:
                adjust = {"rest_add": 1}
            streak += 1
    out = {"done": sum(1 for h in history if not h.get("unplanned")),
           "faded": bool(last and last["outcome"] != "met"), "step": step}
    if last is not None:
        out.update(outcome=last["outcome"], adjust=adjust,
                   note="" if last["outcome"] == "met" else f"上次間歇{OUTCOME_LABEL[last['outcome']]}（{last.get('why') or ''}）：")
    return out


def ladder_keys() -> tuple:
    return tuple(s[0] for s in LADDER) + (TP[0],)


def variant_tuple(v) -> tuple:
    """A library variant as a ladder row (interval_outcome's spec): n reps, minutes, band."""
    from backend.engine import interval_library as IL
    return (v.key, IL.title(v), v.n, v.works[0] / 60.0, v.rest_s / 60.0, v.lo, v.hi, v.terrain == "hill", v.src)


def variant_spec(h: dict, step: int) -> tuple[tuple, bool, bool]:
    """(spec, neutral, counted) of a history row that carries a variant_key:
    neutral when its rung isn't where the ladder stands (or it isn't a ladder
    rung: T+ maintenance, 30/15); counted = equiv (a 縮量版 / non-equivalent swap is
    judged but doesn't move the rung)."""
    from backend.engine import interval_library as IL
    v = IL.resolve(h.get("variant_key"), h.get("variant_reps"), h.get("variant_adj"))
    want = dose_spec(step, True)
    if v is None:
        return want, True, False
    rung = h.get("rung_key") or v.rung
    neutral = rung != want[0]
    return variant_tuple(v), neutral, h.get("equiv") is not False


def user_steps(h: dict) -> Optional[dict]:
    st = h.get("steps")
    return st if isinstance(st, dict) and st.get("origin") == "user" and st.get("items") else None


def steps_spec(h: dict, step: int) -> Optional[tuple[tuple, bool, bool]]:
    """(spec, neutral, counted) of a row whose structure the user edited
    (workout_steps.variant_from_steps), or None (no such structure / no timed work
    step with an intensity: the variant / title path decides). The rung is the
    session's own (rung_key / its variant's); a structure without one is judged at
    the ladder's current rung when it is the same class, else neutral. An HR-only
    structure's band is the class's (推估: h["steps_estimated"])."""
    st = user_steps(h)
    if st is None:
        return None
    from backend.engine import interval_library as IL
    from backend.engine import workout_steps as WS
    rung = h.get("rung_key") or getattr(IL.get(h.get("variant_key")), "rung", None)
    v = WS.variant_from_steps(st, rung)
    if v is None:
        return None
    want = dose_spec(step, True)
    if not rung or rung not in IL.LIBRARY:
        c = IL.canonical(want[0])
        if c is None or c.cls != v.cls:
            return variant_tuple(v), True, False
        rung = want[0]
    ok = IL.equivalent(v, IL.canonical(rung))[0]
    h["steps_equiv"] = ok
    h["steps_estimated"] = v.src_kind == "推估"
    return variant_tuple(v), rung != want[0], ok


def planned_spec(title: Optional[str], step: int) -> tuple[tuple, bool]:
    """(the spec the session was planned at, neutral). By the stored plan's
    title when there is one (dose_history reads it), else the ladder's step.
    Judged only when the title is the rung the ladder stands at (with Zone 5
    open); neutral = a session outside that position: RECOVERY, ZONE3, a SUB
    (ramp week) or Zone 3 maintenance while Zone 5 is paused, and the old
    ladder's titles (LEGACY_TITLES)."""
    want = dose_spec(step, True)
    if title:
        t = str(title)
        if t == RECOVERY[1] or t.startswith("Zone 3"):
            return RECOVERY if t == RECOVERY[1] else ZONE3, True
        if t in LEGACY_TITLES or t == SUB[1]:
            return (SUB if t == SUB[1] else want), True
        for s in LADDER:
            if s[1] == t:
                return s, s[1] != want[1]
    return want, False


def adjusted_spec(spec: tuple, adjust: Optional[dict]) -> tuple:
    """The dose row with the state machine's tweak: rest + N min, or the %CP band × factor."""
    if not adjust:
        return spec
    key, title, reps, work, rest, lo, hi, uphill, src = spec
    if adjust.get("rest_add"):
        rest = rest + int(adjust["rest_add"])
        src = f"{src}；上次未適應：組休 +{int(adjust['rest_add'])} 分（interval-adaptation.md §4.3）"
    if adjust.get("power") and lo is not None:
        f = float(adjust["power"])
        lo, hi = round(lo * f, 3), round(hi * f, 3)
        src = f"{src}；上次第 1 趟沒到：目標 −{round((1 - f) * 100)}%（ROLE:499）"
    return (key, title, reps, work, rest, lo, hi, uphill, src)


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------

def _extra(by: dict, iid: str) -> dict:
    return getattr(by.get(iid), "extra", None) or {}


def _value(by: dict, iid: str):
    return getattr(by.get(iid), "value", None)


def _break_on(ds, today: dt.date) -> Optional[dict]:
    """The re-entry block that matters on `today` (reentry.find); None on any failure."""
    try:
        from backend.engine import reentry as RE
        return RE.find(ds, today)
    except Exception:                           # noqa: BLE001
        return None


def evaluate(ds, plan, today: dt.date, prefs=None, by: Optional[dict] = None, phase=None) -> dict:
    """The gate for `today`: the method result, this week's guardrails, the
    dose step and the per-mode availability. JSON-serialisable; week_plan()
    returns it as `quality_gate` and projection.project_weeks re-reads it."""
    by = by or {}
    mode = getattr(prefs, "quality_gate", "auto") if prefs is not None else "auto"
    mode = mode if mode in MODES else "auto"
    need_weeks = int(getattr(prefs, "quality_gate_weeks", 8) or 8) if prefs is not None else 8
    kind = getattr(phase, "kind", None) or "base"
    base_start = getattr(phase, "start", None) if kind == "base" and phase is not None else None
    ae = aet_info(plan, today) if plan is not None else aet_info(None, today)
    lt = lthr_info(ds, plan, today)
    lthr_ok = lt["value"] is not None and not lt["default"]
    # B3 (unsourced-rules.md): the AeT is valid when the aggregated drift estimate has SE ≤ 3 bpm
    # and no one-way shift > 5 bpm over the last 6 points (推估) — no fixed expiry any more
    from backend.engine import drift_agg as DA
    val = DA.aet_validity(ds, today, lthr=lt["value"] if lthr_ok else None)
    brk = _break_on(ds, today)                  # breaks from the runs (detraining.md §6)
    if brk and brk.get("aet_stale") and brk["return"] <= today.isoformat() and ae.get("measured") and \
            str(ae.get("date") or "") < brk["return"]:
        # a break ≥ 4 weeks: the AeT from before it is stale (UA: re-read after a layoff)
        val = {**val, "valid": False, "reason": f"停跑 {brk['days']} 天（≥ 4 週）：之前的 AeT 視同過期（Uphill Athlete）"}
    ae = {**ae, "valid": bool(val.get("valid")), "fresh": bool(ae["measured"] and val.get("valid")),
          "validity": val}
    gap = ua_gap(ae["value"], lt["value"]) if ae["measured"] and lthr_ok else None
    levels = {i: getattr(by.get(i), "level", "na") for i in ("intensity", "drift")}
    ef = _extra(by, "efficiency").get("change")
    base_weeks = None
    if base_start:
        base_weeks = (today - dt.date.fromisoformat(str(base_start)[:10])).days // 7 + 1

    cache: dict = {}

    def friel():
        if "friel" not in cache:
            cache["friel"] = friel_check(ds, today, ae["value"] if ae["measured"] else None)
        return cache["friel"]

    def xu():
        if "xu" not in cache:
            cache["xu"] = xu_check(ds, today)
        return cache["xu"]

    def method(m: str) -> dict:
        """{"state": unlocked | locked | missing | none, "verdict", "action", "prefix"}."""
        if m == "none":
            return {"state": "none"}
        if m == "ua_gap":
            return ua_gap_method(ae, lt)
        if m == "friel_drift":
            r = friel()
            if r["state"] == "missing":
                return {"state": "missing", "verdict": r["reason"] + "，飄移法算不出來"}
            run = r["run"]
            if r["state"] == "unlocked":
                return {"state": "unlocked", "verdict": f"{run['date']} 在 AeT 附近跑 ≥ 60 分鐘，飄移 {run['drift'] * 100:.1f}% < 5%"
                                                        + heat_suffix(run, True),
                        "prefix": f"Friel 飄移 {run['drift'] * 100:.1f}% < 5%（{run['date']}）：", "run": run}
            return {"state": "locked", "verdict": f"8 週內在 AeT 附近 ≥ 60 分鐘的平路跑，飄移都 ≥ 5%（最近 {run['drift'] * 100:.1f}%）"
                                                  + heat_suffix(run, False),
                    "action": "排一次 60–90 分鐘平路跑，心率壓在 AeT 附近" + ("，選氣溫 25 °C 以下的時段" if run.get("heat") else ""),
                    "run": run}
        if m == "xu_drift":
            r = xu()
            if r["state"] == "missing":
                return {"state": "missing", "verdict": r["reason"] + "，90 分鐘法算不出來"}
            run = r["run"]
            if r["state"] == "unlocked":
                return {"state": "unlocked", "verdict": f"{run['date']} 90 分鐘 E 跑飄移 {run['drift'] * 100:.0f}% < 10%"
                                                        + heat_suffix(run, True),
                        "prefix": f"90 分鐘 E 跑飄移 {run['drift'] * 100:.0f}% < 10%：", "run": run}
            return {"state": "locked", "verdict": f"最近一次 90 分鐘 E 跑飄移 {run['drift'] * 100:.0f}%（≥ 10%）"
                                                  + heat_suffix(run, False),
                    "action": "繼續低強度長跑，下次選 < 25 °C 的日子再測", "run": run}
        if m == "plateau":
            if base_weeks is None or ef is None:
                return {"state": "missing", "verdict": "沒有基礎期起點或 EF 趨勢（輕鬆路跑不夠多），停滯法算不出來"}
            if base_weeks < PLATEAU_WEEKS:
                return {"state": "locked", "info": True, "verdict": f"基礎期第 {base_weeks} 週（停滯法至少 {PLATEAU_WEEKS} 週）"}
            if ef >= EF_PLATEAU:
                return {"state": "locked", "info": True, "verdict": f"EF 還在進步（{ef * 100:+.0f}%）：基礎還在長，先不加"}
            return {"state": "unlocked", "verdict": f"基礎期第 {base_weeks} 週、EF 持平（{ef * 100:+.1f}%）：可以加間歇",
                    "prefix": "EF 持平，基礎差不多了："}
        if m == "weeks":
            if base_weeks is None:
                return {"state": "missing", "verdict": "沒有基礎期起點，週數法算不出來"}
            if base_weeks <= need_weeks:
                return {"state": "locked", "info": True, "verdict": f"基礎期第 {base_weeks} 週 / {need_weeks} 週"}
            return {"state": "unlocked", "verdict": f"基礎期第 {base_weeks} 週 > {need_weeks} 週：可以加間歇",
                    "prefix": f"基礎期已過 {need_weeks} 週："}
        return {"state": "none"}

    # ---- resolve the mode --------------------------------------------------
    resolved, stale = mode, False
    if mode == "auto":
        if ae["measured"] and ae["fresh"] and lthr_ok:
            resolved = "ua_gap+friel_drift"
        else:
            resolved = "none"
            stale = ae["measured"] and not ae["fresh"]
    if resolved == "ua_gap+friel_drift":
        r = method("ua_gap")
        if r["state"] != "unlocked":
            f = method("friel_drift")
            if f["state"] == "unlocked":
                r = {**f, "via": "friel_drift"}
        else:
            r = {**r, "via": "ua_gap"}
    else:
        r = method(resolved)
        if r.get("state") == "unlocked":
            r = {**r, "via": resolved}
    state = r.get("state", "none")

    # ---- this week's guardrails (base phase) ------------------------------
    ie = _extra(by, "intensity")
    g = guard(low_share=ie.get("low_share"), power_low_share=ie.get("power_low_share"),
              ramp=_extra(by, "fitness").get("ramp_week"), step=_extra(by, "volume").get("step"),
              tsb=_value(by, "form"), aet=ae["value"] if ae["measured"] else None,
              injury=_injury_pause(today))
    hist = []
    try:
        hist = dose_history(ds, today)
    except Exception:
        hist = []
    dose = dose_step(hist, ae.get("value"))
    # ---- Zone 5 (engine/base_check.py) and the AeT test's reason -----------
    z5 = _z5(ds, today, mode, state, ae, lt, brk, [h.get("date") for h in hist], friel)
    test_reason = aet_test_reason(ds, today, ae, z5, brk)
    out = {
        "mode": mode, "mode_label": LABEL[mode], "resolved": resolved, "state": state,
        "via": r.get("via"), "verdict": r.get("verdict", ""), "action": r.get("action", ""),
        "prefix": r.get("prefix", ""), "info": bool(r.get("info")),
        "fallback": state == "missing",        # forced mode, data missing → the guardrails (自訂)
        "stale_aet": stale, "weeks_need": need_weeks, "base_start": str(base_start)[:10] if base_start else None,
        "base_weeks": base_weeks, "aet": ae, "lthr": {"value": lt["value"], "default": lt["default"]},
        "gap": gap, "ef_change": ef, "levels": levels, "guard": g,
        "dose": {**dose, "history": hist[-8:]},
        "kind": kind, "week_hours": _extra(by, "volume").get("last_week"),
        "z5": z5, "aet_test_reason": test_reason, "reentry": brk,
        "volume": (test_reason or {}).get("volume") or _volume(ds, today),
    }
    out["options"] = options(out, ae, lt, cache, friel, xu, base_weeks, ef, need_weeks)
    return out


def _volume(ds, today: dt.date) -> Optional[dict]:
    """base_check.volume_stable for today (the Z5 card's precondition item), None on failure."""
    from backend.engine import base_check as BC
    try:
        return BC.volume_stable(ds, today)
    except Exception:                       # noqa: BLE001
        return None


def _injury_pause(today: dt.date) -> Optional[str]:
    try:
        from backend.engine import injuries as INJ
        return INJ.pause_reason(INJ.load_events(), today)
    except Exception:                       # noqa: BLE001 — the gate must still evaluate
        return None


def _z5(ds, today: dt.date, mode: str, state: Optional[str], ae: dict, lt: dict,
        brk: Optional[dict] = None, quality_dates: Optional[list] = None, friel=None) -> dict:
    """base_check.z5_status with the AeT paths (a measured AeT passing the UA
    gap → its row date; a Friel run → its date). `friel`: evaluate's cached
    friel_check (else computed here). Never raises."""
    from backend.engine import base_check as BC
    try:
        paths = {}
        if ae.get("measured") and mode in ("auto", "ua_gap"):
            if ua_gap_method(ae, lt).get("state") == "unlocked":
                paths["aet_ua_gap"] = ae.get("date")
        if ae.get("measured") and mode in ("auto", "friel_drift"):
            f = friel() if friel is not None else friel_check(ds, today, ae.get("value"))
            if f.get("state") == "unlocked":
                paths["aet_friel_drift"] = f["run"]["date"]
        return {**BC.z5_status(ds, today, mode, state, paths, brk, quality_dates), "aet_paths": paths}
    except Exception as e:                  # noqa: BLE001 — Z5 stays closed, the plan still builds
        return {"state": "unconfirmed", "label": BC.STATE_LABEL["unconfirmed"], "open": False, "since": None,
                "path": None, "path_label": "", "reason": f"算不出來（{type(e).__name__}）",
                "text": f"Zone 5：未確認（算不出來：{type(e).__name__}）"}


def aet_test_reason(ds, today: dt.date, ae: dict, z5: dict, brk: Optional[dict] = None) -> Optional[dict]:
    """Why an AeT test should be scheduled now, or None (unsourced-rules.md §B3
    and the Z5 lifecycle; numbers 推估 unless noted): {"code", "text"}.
      no_data  no interpretable run (a drift_of value or a 90-min 徐國峰 run) for
               ~6 weeks (UA's 4–6-week retest, coach; wording 未驗證)
      se       the aggregated AeT estimate is missing or its SE > 3 bpm
      shift    the last 6 points shift one way > 5 bpm
      moved    the estimate is more than max(SE, 3 bpm) away from the plan's AeT
               (UA: AeT rises toward AnT as the base improves — confirm it)
    A passive re-confirmation (Zone 5 confirmed by a passing 90-min 徐國峰 run
    in the last 6 weeks) stands in for a test on no_data / se: no test then."""
    from backend.engine import base_check as BC
    from backend.engine import drift_agg as DA
    val = ae.get("validity") or {}
    try:
        recent = DA.aet_points(ds, today, BC.NO_DATA_DAYS, beta={"beta": 0.0})   # presence only: no β fit
        xu_recent = [r for r in BC.xu_runs(ds, today, BC.NO_DATA_DAYS)]
    except Exception:                       # noqa: BLE001
        recent, xu_recent = [], []
    passive = z5.get("state") == "confirmed" and z5.get("path") == "xu90" and z5.get("since") and \
        (today - dt.date.fromisoformat(z5["since"])).days <= BC.NO_DATA_DAYS
    r = _aet_test_reason(today, ae, z5, brk, val, recent, xu_recent, passive)
    if r is None:
        return None
    # the stable-volume precondition (base_check.volume_stable, 推估): a test taken now would
    # not count, so the suggestion waits (aet_test.due) and says why
    vol = BC.volume_stable(ds, today)
    r = {**r, "volume": vol}
    if vol.get("ok") is False:
        r.update(wait=True, text=r["text"] + f"；先讓週量穩定 {BC.VOL_WEEKS} 週再測（現在 {vol['value']}；推估）")
    return r


def _aet_test_reason(today: dt.date, ae: dict, z5: dict, brk: Optional[dict], val: dict, recent, xu_recent,
                     passive) -> Optional[dict]:
    from backend.engine import base_check as BC
    v, se = val.get("value"), val.get("se")
    if brk and brk.get("aet_stale") and brk["end"] <= today.isoformat() and not (
            z5.get("state") == "confirmed" and (z5.get("since") or "") >= brk["return"]):
        # after the re-entry block of a break ≥ 4 weeks (UA: re-read after a layoff); a
        # passive re-confirmation after the break stands in for it
        return {"code": "break", "text": f"停跑 {brk['days']} 天（≥ 4 週）：恢復期結束後重新讀一次 AeT（Uphill Athlete）"}
    if v is not None and se is not None and se <= 3.0 and val.get("shift_bpm") is not None and \
            abs(val["shift_bpm"]) > 5.0:
        return {"code": "shift", "text": val.get("reason") or "最近 6 次的飄移有系統性偏移"}
    # a lower bound (val["lower_bound"], se None) never fires shift / moved: it is not a point estimate
    if ae.get("measured") and not val.get("lower_bound") and v is not None and se is not None and se <= 3.0 and \
            abs(v - float(ae["value"])) > max(se, 3.0):
        return {"code": "moved", "text": f"聚合估計 AeT {v:.0f} ± {se:.1f} bpm，和目前 {ae['value']:.0f} 差 "
                                         f"{v - float(ae['value']):+.0f}（> 標準誤）：測一次確認（UA：基礎變好 AeT 會往 AnT 靠）"}
    if passive:
        return None
    if not recent and not xu_recent:
        return {"code": "no_data", "text": f"{BC.NO_DATA_DAYS // 7} 週內沒有可判讀的跑步（UA 4–6 週重測；推估 6 週）"}
    if not val.get("valid"):
        return {"code": "se", "text": val.get("reason") or "AeT 聚合估計還不夠準"}
    return None


# ---------------------------------------------------------------------------
# the Zone 5 opening process: history replay and the overview card
# ---------------------------------------------------------------------------

SRC_Z5 = {
    "week": "台灣教練：一週約 210 分鐘 1 區（RQ 訓練指數 30–42 點＝Daniels 強度點數，徐國峰部落格）"
            "——圖上的參考帶，不是解鎖條件",
    "xu90": "台灣教練：平路、≤ 25 °C、停 ≤ 30 秒、心率 1 區，飄移 < 10%",
    "ua": SRC_UA,
    "friel": SRC_FRIEL,
    "z3": "台灣教練：先 3 區、跑順了再加 5 區；「3 堂達標」是推估",
    "keep": "Hickson 1982：1 區時間保有 2/3 就維持耐力；連 3 週是推估",
    "reentry": "Daniels 表 9.2（恢復期＝停訓天數，期間只有 E 日）；先 3 區：徐國峰；堂數推估；"
               "≥ 4 週要重新確認：Mujika & Padilla 2000",
}


def _z5_day(ds, plan, day: dt.date, mode: str, method_state: Optional[str], dates: list) -> dict:
    """evaluate()'s Zone 5 state for `day` — the same inputs: the plan's AeT /
    LTHR in effect that day, the break that mattered then and the interval
    sessions of the 8 weeks before it."""
    return _z5(ds, day, mode, method_state, aet_info(plan, day), lthr_info(ds, plan, day), _break_on(ds, day),
               [d for d in dates if (day - dt.timedelta(days=LOOKBACK_DAYS)).isoformat() <= d < day.isoformat()])


def z5_history(ds, plan, begin: dt.date, end: dt.date, prefs=None, method_state: Optional[str] = None,
               step_days: int = 1) -> dict:
    """Replay the Zone 5 lifecycle over [begin, end]: z5_status (through _z5, as
    evaluate() calls it) on every `step_days`-th day and on `end`, so the
    history is what the planner would have said on each day. JSON:
      days      [{"date", "state", "label", "path", "since", "reason", "level_min"}]
      segments  the days merged into runs of one state (and one confirmation)
      weeks     [{"monday", "z1_min", "complete", "keep_min"}]: weekly Zone 1
                minutes (base_check.weekly) and the pause line in effect at the
                week's end (2/3 of the level at confirmation; None when not confirmed)
      events    confirmations (path), pauses (reason), 90-min runs (drift, ok),
                breaks and their re-entry blocks, measured AeT rows
      target    [150, 210] minutes (RQ 30–42 points), current (the state on `end`)
    `method_state`: the method's state on `end` (plateau / weeks unlock by
    their own method, which needs the status indicators of that day — the
    replay passes it on `end` only; earlier days of those modes are not
    reconstructable and say so in `note`)."""
    from backend.engine import base_check as BC
    from backend.engine import reentry as RE
    mode = getattr(prefs, "quality_gate", "auto") if prefs is not None else "auto"
    mode = mode if mode in MODES else "auto"
    if end < begin:
        begin = end
    span = (end - begin).days
    try:
        sessions = dose_history(ds, end + dt.timedelta(days=1), days=span + LOOKBACK_DAYS + 1)
    except Exception:                           # noqa: BLE001 — evaluate() treats a failure as none too
        sessions = []
    dates = [h["date"] for h in sessions if h.get("date")]
    days: list[dict] = []
    with BC.replay_memo():
        step = max(1, int(step_days))
        todo = [begin + dt.timedelta(days=i) for i in range(0, span + 1, step)]
        if todo[-1] != end:
            todo.append(end)
        for d in todo:
            z = _z5_day(ds, plan, d, mode, method_state if d == end else None, dates)
            mt = z.get("maintenance") or {}
            days.append({"date": d.isoformat(), "state": z.get("state"), "label": z.get("label"),
                         "path": z.get("path"), "path_label": z.get("path_label") or "", "since": z.get("since"),
                         "reason": z.get("reason") or "", "level_min": mt.get("z1_level_min")})
            current = z
        n_weeks = (BC.monday(end) - BC.monday(begin)).days // 7 + 1
        rows = BC.weekly(ds, end, n_weeks)
        xs = BC.xu_runs(ds, end, span + 1)
    try:
        blocks = RE.find_all(ds, end, horizon_days=span + 1)
    except Exception:                           # noqa: BLE001
        blocks = []

    # segments: runs of one state / confirmation
    segs: list[dict] = []
    for i, r in enumerate(days):
        key = (r["state"], r["since"], r["path"])
        nxt = days[i + 1]["date"] if i + 1 < len(days) else (end + dt.timedelta(days=1)).isoformat()
        if segs and segs[-1]["_k"] == key:
            segs[-1]["end"] = nxt
            segs[-1]["reason"] = r["reason"]
        else:
            segs.append({"_k": key, "start": r["date"], "end": nxt, "state": r["state"], "label": r["label"],
                         "path": r["path"], "path_label": r["path_label"], "since": r["since"], "reason": r["reason"]})
    for s in segs:
        s.pop("_k")

    # the pause line per week: the level at confirmation in effect on the week's last replayed day
    by_day = {r["date"]: r for r in days}
    weeks = []
    for w in rows:
        mon = dt.date.fromisoformat(w["monday"])
        last = None
        for k in range(6, -1, -1):
            last = by_day.get((mon + dt.timedelta(days=k)).isoformat())
            if last is not None:
                break
        lvl = last["level_min"] if last and last["state"] in ("confirmed", "paused") else None
        weeks.append({"monday": w["monday"], "z1_min": round(w["z1_s"] / 60.0, 1), "complete": w["complete"],
                      "keep_min": round(lvl * BC.Z1_KEEP, 1) if lvl else None,
                      "level_min": round(lvl, 1) if lvl else None})

    events: list[dict] = []
    seen = set()
    for r in days:
        if r["since"] and r["state"] in ("confirmed", "paused") and (r["since"], r["path"]) not in seen:
            seen.add((r["since"], r["path"]))
            if r["since"] >= begin.isoformat():
                events.append({"date": r["since"], "kind": "confirm", "path": r["path"],
                               "label": f"確認有氧基礎（{r['path_label']}）"})
    prev = None
    for r in days:
        if r["state"] == "paused" and prev != "paused":
            events.append({"date": r["date"], "kind": "pause", "label": f"5 區暫停：{r['reason']}"})
        prev = r["state"]
    for x in xs:
        events.append({"date": x["date"], "kind": "xu_run", "ok": bool(x.get("ok")), "drift": x.get("drift"),
                       "label": BC.xu_text(x)})
    for b in blocks:
        if b["return"] < begin.isoformat() or b["return"] > end.isoformat():
            continue
        events.append({"date": (dt.date.fromisoformat(b["last_run"]) + dt.timedelta(days=1)).isoformat(),
                       "kind": "break", "days": b["days"], "return": b["return"], "end": b["end"],
                       "quality_from": b["quality_from"], "reconfirm": bool(b.get("reconfirm")),
                       "label": b["text"] + ("；≥ 4 週：之前的確認不算" if b.get("reconfirm") else "")})
    for t in sorted((t for t in (getattr(plan, "thresholds", None) or []) if t.aethr is not None),
                    key=lambda t: t.date):
        if begin.isoformat() <= t.date <= end.isoformat():
            events.append({"date": t.date, "kind": "aet", "value": float(t.aethr),
                           "label": f"AeT {t.aethr:.0f} bpm（{t.note or '實測'}）"})
    events.sort(key=lambda e: e["date"])
    note = ("停滯法／週數法用自己的條件解鎖，過去的狀態沒辦法重播，只有最後一天是當天的結果"
            if mode in ("plateau", "weeks") else "")
    return {"begin": begin.isoformat(), "end": end.isoformat(), "mode": mode, "mode_label": LABEL[mode],
            "days": days, "segments": segs, "weeks": weeks, "events": events,
            "target": list(z1_target_min()),
            "current": {k: current.get(k) for k in ("state", "label", "open", "since", "path", "path_label",
                                                    "reason", "text")},
            "note": note, "sources": SRC_Z5}


def z5_card(gate: dict, today: dt.date) -> dict:
    """The overview's 「5 區（最大攝氧量間歇）狀態」 card from evaluate()'s result
    (status.i_gate's extra — the same object week_plan decides with):
    {"state", "label", "headline", "since", "path_label", "reason", "test",
     "reentry", "paths": [{"key", "label", "ok", "src", "items": [...]}],
     "z3": {"done", "need", "ok", "src"}, "keep": {...} | None, "open"}.
    Every item: {"label", "ok" (True / False / None = no data), "value", "need", "src"}."""
    from backend.engine import base_check as BC
    z = gate.get("z5") or {}
    state = z.get("state") or "unconfirmed"
    out = {"state": state, "label": z.get("label") or BC.STATE_LABEL.get(state, state), "open": bool(z.get("open")),
           "since": z.get("since"), "path": z.get("path"), "path_label": z.get("path_label") or "",
           "reason": z.get("reason") or "", "text": z.get("text") or "", "mode": gate.get("mode"),
           "mode_label": LABEL.get(gate.get("mode") or "auto", ""), "test": gate.get("aet_test_reason"),
           "reentry": None, "keep": None}
    brk = z.get("reentry") or None
    if brk:
        iso = today.isoformat()
        left = (dt.date.fromisoformat(brk["quality_from"]) - today).days if iso < brk["quality_from"] else 0
        out["reentry"] = {"days": brk["days"], "return": brk["return"], "end": brk["end"],
                          "quality_from": brk["quality_from"], "days_left": max(0, left),
                          "z3_before_z5": int(brk.get("z3_before_z5") or 1), "reconfirm": bool(brk.get("reconfirm")),
                          "drift_check": bool(brk.get("drift_check")), "text": brk.get("text") or "",
                          "src": SRC_Z5["reentry"]}
    mode = gate.get("mode") or "auto"
    pause = z.get("pause") or {}
    ae = gate.get("aet") or {}
    lt = gate.get("lthr") or {}
    # the base step is done once a confirmation stands; a Z1-rule pause needs a new one
    base_done = state in ("confirmed", "open") or (state == "paused" and z.get("since") and
                                                   pause.get("kind") in ("reentry_z3", "drift_check"))
    # a test only counts after the Z1 pause / a ≥ 4-week break (the confirmation before it no longer does)
    after = pause.get("at") if state == "paused" and pause.get("kind") == "z1" else \
        brk.get("return") if brk and brk.get("reconfirm") else None

    def counts(date: Optional[str]) -> bool:
        return bool(date) and (after is None or str(date)[:10] >= str(after)[:10])

    tests = []
    if mode_has(mode, "xu90"):
        x = z.get("xu_last")
        cur = z.get("path") == "xu90" and base_done
        tests.append({"key": "xu90", "label": "徐國峰 90 分鐘測試：平路 1 區跑 90 分鐘，第 90 分 vs 第 10 分心率飄移 < 10%",
                      "ok": True if cur else (bool(x.get("ok")) and counts(x.get("date"))) if x else None,
                      "value": (f"確認於 {z.get('since')}" if cur and not (x and x.get("ok")) else
                                BC.xu_text(x) if x else "—（還沒做過：半年內沒有 ≥ 90 分鐘的跑步）"),
                      "need": "< 10%（≤ 25 °C、補給停 ≤ 30 秒）", "src": SRC_Z5["xu90"]})
    g = gate.get("gap")
    ap = z.get("aet_paths") or {}
    if mode_has(mode, "aet_ua_gap"):
        tests.append({"key": "aet_ua_gap", "label": "UA 差距法：實測 AeT，LTHR ÷ AeT − 1 ≤ 10%",
                      "ok": (g <= UA_GAP_MAX and counts(ae.get("date"))) if g is not None else None,
                      "value": (f"AeT {ae['value']:.0f} / LTHR {lt['value']:.0f} → {g * 100:.0f}%" if g is not None
                                else "—（還沒做過：沒有實測 AeT）" if not ae.get("measured") else "—（LTHR 還是預設值）"),
                      "need": "≤ 10%", "src": SRC_Z5["ua"]})
    if mode_has(mode, "aet_friel_drift"):
        fr = (gate.get("options") or {}).get("friel_drift") or {}
        tests.append({"key": "aet_friel_drift", "label": "Friel 飄移：實測 AeT 附近跑 ≥ 60 分鐘，前後半飄移 < 5%",
                      "ok": (("aet_friel_drift" in ap) and counts(ap.get("aet_friel_drift"))) if fr.get("usable") else None,
                      "value": fr.get("why") or "—", "need": "< 5%", "src": SRC_Z5["friel"]})
    method = None
    if mode in ("plateau", "weeks"):
        method = {"key": "method", "label": LABEL[mode], "ok": gate.get("state") == "unlocked",
                  "value": gate.get("verdict") or "", "need": "", "src": source_of_mode(mode)}
        tests.append(method)
    # the stable-volume precondition (base_check.volume_stable, 推估): a checklist item, shown while
    # a test is still needed (unconfirmed / a Z1 pause); evaluate() stores today's check in gate["volume"]
    vol = gate.get("volume")
    pre = None
    if vol and tests and not base_done and state != "reentry":
        pre = {"key": "volume", "label": vol.get("label") or "前提：週量穩定", "ok": vol.get("ok"),
               "value": vol.get("value") or "—", "need": vol.get("need") or "", "src": vol.get("src") or ""}
    out["base"] = {"label": "確認有氧基礎（三選一，做了且達標）" if len(tests) > 1 else
                   f"確認有氧基礎（{tests[0]['label'].split('：')[0]}）" if tests else "確認有氧基礎",
                   "ok": bool(base_done), "tests": tests, "pre": pre,
                   "empty": ("恢復期內不判斷" if state == "reentry" else
                             "不設門檻（Seiler）" if state == "open" else "" if tests else "這個間歇門檻不開 5 區")}
    d = gate.get("dose") or {}
    step = int(d.get("step") or 0)
    z3 = out["z3"] = {"done": min(step, Z3_MET_FOR_Z5), "need": Z3_MET_FOR_Z5, "ok": step >= Z3_MET_FOR_Z5,
                      "src": SRC_Z5["z3"]}
    mt = z.get("maintenance") or {}
    if state in ("confirmed", "paused") and mt.get("z1_level_min"):
        wk = mt.get("weeks") or []
        last = wk[-1] if wk else None
        out["keep"] = {"level_min": mt["z1_level_min"], "line_min": mt["z1_level_min"] * BC.Z1_KEEP,
                       "last_week": last, "ok": bool(mt.get("ok", True)), "why": mt.get("why") or "",
                       "src": SRC_Z5["keep"]}
    out["next"] = _z5_next(out, z, gate, tests)
    z5_ok = out["open"] and z3["ok"]
    out["steps"] = [
        {"key": "base", "label": "確認有氧基礎（三選一）" if len(tests) > 1 else "確認有氧基礎",
         "status": "done" if base_done else "wait" if state == "reentry" else "active"},
        {"key": "z3", "label": f"3 區達標 {z3['done']}/{z3['need']}",
         "status": "done" if z3["ok"] else "active" if base_done else "todo"},
        {"key": "z5", "label": "5 區開放",
         "status": "done" if z5_ok else "paused" if state in ("paused", "reentry") else "todo"},
    ]
    out["headline"] = {
        "confirmed": f"已確認（{z.get('since')}，{out['path_label']}）",
        "paused": "暫停", "reentry": "恢復期", "open": "不設門檻",
    }.get(state, "未確認")
    return out


def source_of_mode(mode: str) -> str:
    return OPTION_INFO.get(mode, {}).get("source", "")


def _z5_next(card: dict, z: dict, gate: dict, tests: list) -> dict:
    """The one plain-language line 「還缺什麼」 under the current step (card and chart
    alike): {"kind": open | reentry | paused | missing | done, "text"}."""
    state, z3 = card["state"], card["z3"]
    pause = z.get("pause") or {}
    R = card.get("reentry")
    if state == "open":
        return {"kind": "open", "text": "不設門檻（Seiler）：5 區照 80/20 安排"}
    if state == "reentry" and R:
        after = f"之後先 {R['z3_before_z5']} 堂 3 區" + ("，並重新確認有氧基礎（三選一）" if R["reconfirm"] else "")
        return {"kind": "reentry", "text": f"恢復期還剩 {R['days_left']} 天（到 {R['quality_from']} 前只排輕鬆跑）；{after}"}
    if state == "paused":
        if pause.get("kind") == "reentry_z3":
            left = max(0, int(pause.get("need") or 1) - int(pause.get("done") or 0))
            return {"kind": "paused", "text": f"還缺：恢復期後再 {left} 堂 3 區（已 {pause.get('done', 0)}／{pause.get('need', 1)}）"}
        if pause.get("kind") == "drift_check":
            return {"kind": "paused", "text": "還缺：恢復期後的長跑飄移檢查——下一次 ≥ 75 分鐘的路跑長跑，"
                                              "後段心率、配速各在 ±5% 內（推估）"}
        K = card.get("keep") or {}
        line = f"（現在是 {K['line_min']:.0f} 分）" if K.get("line_min") else ""
        return {"kind": "paused", "text": "還缺：重新確認有氧基礎（三選一，例如再做一次 90 分鐘測試）；"
                                          f"之後每週 1 區時間別連 3 週低於確認時的 2/3{line}"}
    if state == "confirmed":
        if z3["ok"]:
            return {"kind": "done", "text": "都做到了：5 區可以排（每趟 ≥ 2 分、一週最多 2 次、隔 ≥ 2 天）"}
        left = z3["need"] - z3["done"]
        return {"kind": "missing", "text": f"還缺：再 {left} 堂 3 區達標（{z3['done']}/{z3['need']}；3 區只要護欄通過就照排）"}
    # unconfirmed: what to do for the cheapest test the mode uses
    mode = gate.get("mode") or "auto"
    by = {t["key"]: t for t in tests}
    pre = f"停跑 ≥ 4 週：{R['return']} 之後" if R and R.get("reconfirm") else ""
    if mode in ("plateau", "weeks"):
        return {"kind": "missing", "text": f"還缺：{gate.get('verdict') or '方法還沒解鎖'}"}
    parts = []
    if "xu90" in by:
        x = z.get("xu_last")
        last = ""
        if x and not x.get("ok"):
            last = (f"（上次 {x['date'][5:]} 飄移 {x['drift'] * 100:.1f}%）" if x.get("drift") is not None and
                    all("飄移" in w for w in x.get("why") or []) else
                    f"（上次 {x['date'][5:]} 沒過：{(x.get('why') or [''])[0].split('（')[0]}）")
        parts.append(f"做一次 90 分鐘平路 1 區測試（氣溫 25 °C 以下、補給停 ≤ 30 秒），飄移 < 10%{last}")
    ae = gate.get("aet") or {}
    g = gate.get("gap")
    if "aet_ua_gap" in by or "aet_friel_drift" in by:
        if not ae.get("measured"):
            parts.append("做一次 AeT 測試" + ("（LTHR ÷ AeT − 1 ≤ 10% 就算）" if "aet_ua_gap" in by else
                                             "，再在 AeT 附近跑 ≥ 60 分鐘、飄移 < 5%"))
        elif "aet_ua_gap" in by and g is not None and len(by) == 1:
            parts.append(f"AeT 和 LTHR 的差距降到 ≤ 10%（現在 {g * 100:.0f}%）：繼續有氧基礎，之後重測 AeT")
        elif "aet_friel_drift" in by:
            lo, hi = float(ae["value"]) + FRIEL_HR_BAND[0], float(ae["value"]) + FRIEL_HR_BAND[1]
            parts.append(f"在 AeT 附近（{lo:.0f}–{hi:.0f} bpm）跑一次 ≥ 60 分鐘平路穩定跑，前後半飄移 < 5%")
    if (gate.get("volume") or {}).get("ok") is False:
        # the stable-volume precondition (推估): a test now wouldn't count
        from backend.engine.base_check import VOL_WEEKS
        pre = (pre + "，" if pre else "") + f"先讓週量穩定 {VOL_WEEKS} 週，再"
    if not parts:
        return {"kind": "missing", "text": f"還缺：{z.get('reason') or '確認有氧基礎'}"}
    return {"kind": "missing", "text": "還缺：" + pre + "；或".join(parts)}


def z1_target_min() -> tuple[float, float]:
    """徐國峰's weekly Zone 1 band in minutes: RQ 30–42 points ÷ 0.2 = 150–210."""
    from backend.engine.base_check import RQ_E_PER_MIN, XU_WEEK_POINTS, XU_WEEK_POINTS_HI
    return XU_WEEK_POINTS / RQ_E_PER_MIN, XU_WEEK_POINTS_HI / RQ_E_PER_MIN


def mode_has(mode: Optional[str], path: str) -> bool:
    """Which confirmation tests a 間歇門檻 mode uses (base_check._paths_for):
    xu90 / aet_ua_gap / aet_friel_drift, or "aet" for either AeT test."""
    from backend.engine.base_check import _paths_for
    p = _paths_for(mode or "auto")
    if path == "aet":
        return "aet_ua_gap" in p or "aet_friel_drift" in p
    return path in p


def options(gate: dict, ae: dict, lt: dict, cache: dict, friel, xu, base_weeks, ef, need_weeks) -> dict:
    """Per mode: can it run on the athlete's data right now (the hover's last line)."""
    lthr_ok = lt["value"] is not None and not lt["default"]
    aet_txt = ae["label"] if ae["measured"] else "沒有實測 AeT"
    out = {}
    out["auto"] = {"usable": True, "why": (f"目前用差距法＋飄移法（{aet_txt}）" if gate["resolved"] != "none"
                                           else f"目前不設門檻，只看護欄（{aet_txt}"
                                           + ("；聚合估計還不夠準或有偏移" if gate["stale_aet"] else "") + "）")}
    if ae["measured"] and lthr_ok:
        out["ua_gap"] = {"usable": True, "why": f"{aet_txt}、LTHR {lt['value']:.0f} → 差距 {gate['gap'] * 100:.0f}%"}
    else:
        out["ua_gap"] = {"usable": False, "why": "沒有實測 AeT" if not ae["measured"] else "LTHR 還是 WKO5 預設值"}
    try:
        f = cache.get("friel") or friel()
        out["friel_drift"] = {"usable": f["state"] != "missing",
                              "why": f.get("reason") or f"最近一次 {f['run']['date']} 飄移 {f['run']['drift'] * 100:.1f}%"}
    except Exception as e:  # never let the hover text break the plan
        out["friel_drift"] = {"usable": False, "why": f"算不出來（{type(e).__name__}）"}
    try:
        x = cache.get("xu") or xu()
        out["xu_drift"] = {"usable": x["state"] != "missing",
                           "why": x.get("reason") or f"最近一次 {x['run']['date']} 飄移 {x['run']['drift'] * 100:.0f}%"}
    except Exception as e:
        out["xu_drift"] = {"usable": False, "why": f"算不出來（{type(e).__name__}）"}
    out["plateau"] = {"usable": base_weeks is not None and ef is not None,
                      "why": (f"基礎期第 {base_weeks} 週，EF 近 6 週 {ef * 100:+.1f}%" if base_weeks is not None and ef is not None
                              else "EF 趨勢算不出來（輕鬆路跑不夠多）" if base_weeks is not None else "現在不是基礎期")}
    out["weeks"] = {"usable": base_weeks is not None,
                    "why": f"基礎期第 {base_weeks} 週 / {need_weeks} 週" if base_weeks is not None else "現在不是基礎期"}
    out["none"] = {"usable": True, "why": "隨時可用：只看護欄"}
    return out


# ---------------------------------------------------------------------------
# per week
# ---------------------------------------------------------------------------

def legacy(g: Optional[dict]) -> bool:
    return g is not None and "state" not in g


def week_decision(gate: dict, kind: str, mode: str, monday: Optional[dt.date] = None,
                  step: Optional[int] = None, first: bool = True) -> dict:
    """{"allow", "spec", "advance", "note"} for one week. `first` = this week
    (today's guardrails apply); projected weeks only keep the slow-moving
    intensity guard — ramp, volume and TSB are re-checked when the week comes."""
    kind = kind or "base"
    levels = gate.get("levels") or {}
    gi = gate.get("guard") or {}
    if gi.get("rule") == "injury" and gi.get("block"):
        # 傷病紀錄「受傷期間暫停強度課」: every phase, every week until the event is resolved
        return {"allow": False, "spec": None, "advance": False, "note": gi.get("verdict", "")}
    if kind != "base":
        ok = levels.get("intensity") != "bad" and levels.get("drift") != "bad"
        return {"allow": ok, "spec": None, "advance": False, "note": ""}
    # two gates (徐國峰, 私訊 2026-10-01): Zone 3 whenever the guardrails pass; Zone 5 only
    # while the aerobic base is confirmed (gate["z5"], engine/base_check.z5_status). A locked
    # method no longer stops Zone 3 — it only keeps Zone 5 closed.
    z5 = gate.get("z5") or {}
    z5_open = bool(z5.get("open"))
    if first and z5.get("state") == "reentry":
        # inside a re-entry block: E days only (Daniels table 9.2; engine/reentry.py)
        return {"allow": False, "spec": None, "advance": False, "note": z5.get("text", "")}
    if gate.get("resolved") == "weeks" and monday is not None and gate.get("base_start") and \
            gate.get("mode") == "weeks":
        wk = (monday - dt.date.fromisoformat(gate["base_start"])).days // 7 + 1
        z5_open = wk > int(gate.get("weeks_need") or 8)
    g = gate.get("guard") or {}
    if not first:
        g = {"block": g.get("block") and g.get("rule") == "intensity", "sub": False, "hold": False}
    if g.get("block"):
        return {"allow": False, "spec": None, "advance": False, "note": g.get("verdict", "")}
    if mode == "recovery_week":
        return {"allow": True, "spec": RECOVERY, "advance": False, "note": ""}
    if g.get("sub"):
        return {"allow": True, "spec": SUB, "advance": False, "note": g.get("verdict", "")}
    d = gate.get("dose") or {}
    s = d.get("step", 0) if step is None else step
    if first and g.get("hold") and d.get("done"):
        s = min(s, max(0, d["done"] - 1))              # repeat the last step, never go up
        adv = False
    else:
        adv = True
    spec = dose_spec(s, z5_open)
    if s >= len(Z3) and not z5_open:
        adv = False                                       # Zone 3 maintenance: the Z5 step waits
    adj = None
    if first and step is None and s == d.get("step", 0):
        adj = d.get("adjust") or None
        spec = adjusted_spec(spec, adj)                   # the state machine's tweak, this week only
    note = "" if z5_open or s < len(Z3) else (z5.get("text") or "Zone 5 還沒開：先排 3 區")
    return {"allow": True, "spec": spec, "advance": adv, "note": note, "adjust": adj}


def zone3_work(hours: Optional[float], reps: int = 3) -> int:
    """Minutes per Zone 3 rep: about 5 % of the week's aerobic time (UA), 12–30 min in all."""
    total = max(12.0, min(30.0, 0.05 * (hours or 4.0) * 60.0))
    return max(4, int(round(total / reps)))


# HR range (× LTHR) per dose step; 1-minute reps are too short for HR to settle
_HR_FRAC = {"z3c": (0.95, 1.00), "z5b": (1.00, 1.03), "z5c": (1.00, 1.03), "z5d": (1.03, 1.06)}


def session(spec: tuple, th: dict, prefix: str = "", hours: Optional[float] = None,
            lthr_default: bool = False) -> dict:
    """A week-plan session dict for a dose spec. Title / detail keep the tokens
    the COROS step builder and trim_quality parse (N×M 分 in the title; 休 N 分,
    暖身 / 緩和 and lo–hi% CP in the detail; 爬坡 for uphill)."""
    key, title, reps, work, rest, lo, hi, uphill, src = spec
    cp, lthr, aet = th.get("cp"), th.get("lthr"), th.get("aet")
    use_hr = bool(lthr) and not lthr_default          # LTHR still WKO5's default → power / RPE only
    if key == "z3":
        work = zone3_work(hours, reps)
        title = f"Zone 3 間歇 {reps}×{work} 分"
        parts = [f"心率 {aet:.0f}–{lthr:.0f} bpm"] if aet and lthr else ["心率 AeT–LTHR"]
        what = f"心率在 AeT–LTHR（Zone 3），總量約 {reps * work} 分（週有氧量的 5%）"
    else:
        what = f"{lo * 100:.0f}–{hi * 100:.0f}% CP"
        parts = [f"功率 {lo * cp:.0f}–{hi * cp:.0f} W（{what}）"] if cp else [f"RPE 8（{what}）"]
        if key in ("z3a", "z3b", "sub") and aet and use_hr:
            parts.append(f"心率 {aet:.0f}–{lthr:.0f} bpm")
        elif key in _HR_FRAC and use_hr:
            a, b = _HR_FRAC[key]
            parts.append(f"心率 {a * lthr:.0f}–{b * lthr:.0f} bpm")
    fmt = lambda x: f"{x:g}"
    # Buchheit & Laursen 2013: rests < 2–3 min passive — a Zone 5 rest ≤ 2.5 min is a walk
    how = "走路或極慢跑" if key.startswith("z5") and rest <= 2.5 else "慢跑"
    if uphill:
        body = f"上坡 {fmt(work)} 分鐘（6–10% 坡），慢跑或走下來恢復；{what}；休 {fmt(rest)} 分鐘"
    else:
        body = f"{what}；休 {fmt(rest)} 分鐘（{how}）"
    # no rest after the last rep (interval-prescription.md §A5.2-3)
    minutes = int(round(15 + reps * work + (reps - 1) * rest + 10))
    rate = {"z3": 60.0, "z3a": 65.0, "z3b": 65.0, "sub": 65.0, "z3c": 68.0, "r1": 55.0}.get(key, 72.0)
    return {"id": "quality", "kind": "quality", "title": title, "minutes": minutes,
            "target": " · ".join(parts), "detail": f"{prefix}{body}；暖身 15 分、緩和 10 分",
            "source": src, "tss": minutes / 60.0 * rate}


def prefix(gate: dict) -> str:
    """The detail prefix for the week card (§4.6)."""
    if gate.get("state") == "unlocked":
        return gate.get("prefix", "")
    if gate.get("fallback"):
        return f"（{LABEL.get(gate.get('mode'), '')}缺資料，先照護欄排）"
    return "沒有 AeT 實測：照 80/20 原則每週 1 次間歇，" if gate.get("mode") == "auto" else "不設門檻：照 80/20 原則每週 1 次間歇，"


def source(gate: dict, spec: tuple) -> str:
    via = gate.get("via")
    head = {"ua_gap": SRC_UA, "friel_drift": SRC_FRIEL, "xu_drift": SRC_XU, "plateau": SRC_XU + "；自訂（EF 代替 VO2max）",
            "weeks": SRC_PALLADINO + "；Cusick 第一階段 4–8 週"}.get(via, SRC_SEILER)
    return f"{head}；{spec[-1]}"


def guardrail_mode(gate: Optional[dict]) -> bool:
    """No method is unlocking (none / auto without AeT / forced with missing data):
    base phase is capped at one interval a week."""
    return bool(gate) and gate.get("state") in ("none", "missing")


def hard_need(title: str, default: float, variant_key: Optional[str] = None,
              variant_reps: Optional[int] = None) -> float:
    """Seconds at/above threshold that mark a planned interval session done:
    short reps (5×1′) never reach 10 min, so 60 % of the planned work (a
    library variant: 60 % of its time in zone)."""
    import re
    if variant_key:
        from backend.engine import interval_library as IL
        v = IL.resolve(variant_key, variant_reps)
        if v is not None:
            return min(default, 0.6 * IL.tiz_s(v))
    m = re.search(r"(\d+)\s*[×xX]\s*(\d+)\s*分", title or "")
    if not m:
        return default
    return min(default, 0.6 * int(m.group(1)) * int(m.group(2)) * 60)


def is_z3_variant(key: Optional[str]) -> bool:
    from backend.engine import interval_library as IL
    v = IL.get(key)
    return v is not None and v.cls == "Z3sub"


# ---------------------------------------------------------------------------
# the status indicator (§4.6)
# ---------------------------------------------------------------------------

def indicator(gate: dict) -> dict:
    """{"level", "text", "verdict", "why", "action", "source"} for status.i_gate."""
    k = gate.get("kind") or "base"
    ae = gate.get("aet") or {}
    why_parts = [f"模式：{gate['mode_label']}" + ("（" + ("差距法＋飄移法" if gate["resolved"] != "none" else "不設門檻") + "）"
                                                   if gate["mode"] == "auto" else "")]
    why_parts.append(ae["label"] if ae.get("measured") else "AeT 沒有實測（用 0.89×LTHR 估）")
    if (ae.get("validity") or {}).get("lower_bound"):
        # the temporary lower bound (drift_agg.aet_validity): valid, said as a bound with its rule
        why_parts.append(ae["validity"]["reason"])
    if gate.get("stale_aet"):
        why_parts.append(f"AeT 目前不算有效：{(ae.get('validity') or {}).get('reason') or '聚合估計還不夠準'}"
                         "（標準誤 ≤ 3 bpm、最近 6 次沒有偏移才算；推估），改用不設門檻")
    d = gate.get("dose") or {}
    why_parts.append(f"8 週內 {d.get('done', 0)} 次間歇")
    z5 = gate.get("z5") or {}
    if z5.get("text"):
        why_parts.append(z5["text"] + "（3 區先、5 區後：徐國峰）")
    tr = gate.get("aet_test_reason")
    if tr:
        why_parts.append(f"建議測試：{tr['text']}")
    why = "；".join(why_parts)
    if k != "base":
        return {"level": "info", "text": "—", "verdict": "非基礎期：間歇照周期安排（強度分配不是 bad 就排）",
                "why": why, "action": "", "source": SRC_SEILER}
    dec = week_decision(gate, "base", "base")
    step_txt = ""
    if dec["spec"] is not None:
        step_txt = f"（第 {d.get('step', 0) + 1} 步：{dec['spec'][1]}）" if dec["spec"] not in (SUB, ZONE3, RECOVERY) \
            else f"（{dec['spec'][1]}）"
    g = gate.get("guard") or {}
    state = gate.get("state")
    src = {"ua_gap": SRC_UA, "friel_drift": SRC_FRIEL, "xu_drift": SRC_XU}.get(gate.get("via") or gate["mode"], SRC_SEILER)
    if state == "locked":
        # the method keeps Zone 5 closed; Zone 3 still goes on when the guardrails pass (徐國峰)
        v = gate["verdict"] + (f"；本週 3 區{step_txt}" if dec["allow"] and dec["spec"] is not None else "")
        if g.get("block"):
            v += "；" + g["verdict"]
        return {"level": "info" if gate.get("info") else "watch", "text": "5 區未開", "verdict": v,
                "why": why, "action": gate.get("action", ""), "source": src}
    if state == "missing":
        v = gate["verdict"] + "：先照護欄排（推估：缺資料不永久鎖住）"
        if g.get("block"):
            v += "；" + g["verdict"]
        return {"level": "watch", "text": "缺資料", "verdict": v, "why": why,
                "action": "先做 AeT 飄移測試，或把間歇門檻改回自動" if gate["mode"] in ("ua_gap", "friel_drift")
                else "照說明補一次測試跑，或把間歇門檻改回自動", "source": src}
    if g.get("block") or g.get("sub") or g.get("hold"):
        return {"level": "watch" if (g.get("block") or g.get("sub")) else "info", "text": "護欄",
                "verdict": g["verdict"], "why": why, "action": g.get("action", ""), "source": SRC_SEILER}
    if state == "unlocked":
        spec = dec["spec"]
        act = ""
        if spec is ZONE3:
            act = f"本週 1 次 Zone 3（AeT–LTHR），總量約 {ZONE3[2] * zone3_work(gate.get('week_hours'))} 分"
        return {"level": "good", "text": "已解鎖", "verdict": gate["verdict"] + step_txt, "why": why,
                "action": act, "source": src}
    # none / auto without a measured AeT, every guardrail passes
    head = "沒有 AeT 實測" if gate["mode"] == "auto" else "不設門檻"
    return {"level": "info", "text": "護欄", "verdict": f"{head}：照 80/20 原則每週 1 次間歇{step_txt}",
            "why": why, "action": "", "source": SRC_SEILER}


# ---------------------------------------------------------------------------
# the selector's hover texts (schedule.html 課表偏好 → 間歇門檻)
# ---------------------------------------------------------------------------

OPTION_INFO = {
    "auto": {"source": "台灣教練、Uphill Athlete、Friel、Seiler",
             "rule": "3 區（閾值）只要護欄通過就排；5 區（每趟 ≥ 2 分、一週最多 2 次、隔 ≥ 2 天：徐國峰）要先確認有氧基礎："
                     "三種測試做了其中一種而且達標——① 徐國峰 90 分鐘測試（平路 1 區，第 90 分 vs 第 10 分心率飄移 < 10%）、"
                     "② 實測 AeT 的 UA 差距法（LTHR ÷ AeT − 1 ≤ 10%）、③ 實測 AeT 的 Friel 飄移（AeT 附近 ≥ 60 分鐘，前後半 < 5%）。"
                     "確認後沒有到期日，每週檢查：1 區時間連 3 週 < 確認時的 2/3 就暫停，到下次確認為止（Hickson 1982；3 週推估）；"
                     "停跑 ≥ 6 天進恢復期（Daniels 表 9.2），期間 3 區、5 區都不排，之後先 3 區；暫停時 3 區照排。"
                     "AeT 有效＝聚合估計標準誤 ≤ 3 bpm、最近 6 次沒偏移（推估），有效時才用差距法。",
             "todo": "週末的 LSD 改成 90 分鐘平路 1 區、配速不變，跑完就自動確認；不用另外測。"},
    "ua_gap": {"source": "Uphill Athlete：When to add intensity",
               "rule": "AnT ÷ AeT − 1 ≤ 10%（Uphill Athlete）：用 LTHR 當 AnT、實測 AeT。差距越小代表有氧基礎越好。"
                       "解鎖後先排 Zone 3（AeT–LTHR），每週 1 次，約週有氧時數的 5%。",
               "todo": "需要一次 AeT 測試（和 LTHR 測試）；之後只在聚合估計標準誤 > 3 bpm、有偏移或約 6 週沒有可判讀的跑步時再測（推估）。"},
    "friel_drift": {"source": "Friel（TrainingPeaks：Aerobic decoupling）",
                    "rule": "8 週內有一次在 AeT 附近（平均心率 AeT−5～AeT+3，範圍自訂）、暖身後 ≥ 60 分鐘的平路穩定跑，"
                            "前後半 Pa:HR 飄移 < 5%。一次就夠。",
                    "todo": "需要實測 AeT，並排一次 60–90 分鐘平路跑，心率壓在 AeT 附近、不停、不加速。"},
    "xu_drift": {"source": "徐國峰（你的筆記：跑者都該懂的跑步數據）",
                 "rule": "平地、< 25 °C、E 配速 90 分鐘：(第 90 分心率 − 第 10 分心率) ÷ 第 10 分心率 < 10% 就可以練間歇（< 5% 是國家級）。",
                 "todo": "排一次 90 分鐘平路 E 配速跑，選 < 25 °C 的日子，補給停不超過 30 秒。"},
    "plateau": {"source": "徐國峰（錶上 VO2max 不再提升）；Cusick（指標先到平台期）",
                "rule": "基礎期 ≥ 8 週，而且有氧效率 EF 近 6 週和之前比 < +2%（持平）。用 EF 代替錶上 VO2max、8 週和 2% 都是自訂。"
                        "不叫「MAF 停滯」：Maffetone 把停滯當警訊。",
                "todo": "繼續輕鬆路跑（心率 ≤ AeT、≥ 30 分鐘），EF 才算得出來。"},
    "weeks": {"source": "Palladino 基礎期分段；Cusick 第一階段 4–8 週",
              "rule": "基礎期開始後滿 N 週（預設 8，範圍 2–16）才排間歇。8 週取中間值，屬自訂。",
              "todo": "不用測試；只要基礎期有起點（賽事周期）。"},
    "none": {"source": "Seiler 2010、Seiler & Tønnessen 2009、Koop／CTS",
             "rule": "不設門檻：整個週期都有少量高強度。基礎期每週最多 1 次，由護欄決定：低強度 ≥ 75%、CTL 每週 < +5（≥ 5 只排閾值下）、"
                     "週增量 ≤ 20%（10–20% 維持）、3:1 恢復週改 4×1 分 fartlek、TSB、離長跑 ≥ 2 天。"
                     "劑量 3 區 3×6 → 3×8 → 2×12（90–95% CP），5 區 5×2 → 4×3 → 5×3 → 4×4（徐國峰：3 區先）；"
                     "時間足夠排標準版，平日上限放不下時換同等較短版（interval-prescription.md）。",
             "todo": "不用測試。"},
}


def option_texts() -> dict:
    """{mode: {"label", "tip"}} — the static part of the hover (availability is added by the page)."""
    return {m: {"label": LABEL[m], "tip": f"{LABEL[m]}\n來源：{v['source']}\n\n怎麼算：{v['rule']}\n\n要做的事：{v['todo']}"}
            for m, v in OPTION_INFO.items()}
