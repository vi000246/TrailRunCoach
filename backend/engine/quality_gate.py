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
     low-intensity time share ≥ 75 % (and run power < 80 % CP ≥ 75 % when known) — Zone 5 only since
       SP-31: for Zone 3 a warning (the AeT is often estimated, climbs inflate HR); since SP-39 it
       blocks Zone 5 only with a tested AeT in effect — with an estimated AeT a warning for both
     CTL ramp: ≥ 5 /week → sub-threshold only; ≥ 8 → none (Friel 5–8, coach)
     last week's volume step: > 20 % → none (Nielsen 2014, Damsted 2019); 10–20 % → hold the dose (推估)
     TSB −30…−20 → hold the dose (Friel / TrainingPeaks; < −30 is already a recovery week)
     3:1 recovery week → a 4×1′ fartlek instead of intervals (Palladino)
     48 h from the long run / other hard days → plan_prefs.place() / week_plan
   Base phase gets at most one interval session a week without a measured AeT (guardrail_mode).

Two tracks (SP-31, 2026-10-04; coach-schools-zones-periodization.md R2/R3), each with its own
ladder, dose step and 達標 count (dose_tracks):
  Zone 3 (有氧間歇／節奏, 88–95 % CP, reps 15–30 min): A1 2×15′ → A2 3×12′ → A3 2×20′ → A4 1×30′,
     then A3 / A4 / T+ maintenance. Opens on the Zone 3 gate (z3_gate, any one): 4 complete weeks
     of actual training with ≥ 3 runs a week and no 7-day gap (推估; sticky, a ≥ 21-day break
     re-locks), the 90-min drift test < 10 %, or a measured UA gap ≤ 10 %. Its time in zone ≤ 10 %
     of the week (Daniels; 5 % for the first session, UA): over that the 巡航版 T1–T3 (3×6′ / 3×8′
     / 2×12′, the old Zone 3 rungs) stands in and still counts. Zone 3 + Zone 5 ≤ 20 % of the
     week's running time (QUALITY_SHARE_MAX, 推估; overview.quality_sessions shortens and notes).
  Zone 5: 5×2′ → 4×3′ → 5×3′ → 4×4′, then V3 / V4 maintenance — its own gate (SP-39, z5_track):
     a MEASURED AeT (base_check.z5_status: a tested AeT + a measured LTHR within 10 %, or Friel
     drift < 5 % at the tested AeT; the 90-min test is not an AeT test) and the soft 「3 區先」:
     ≥ Z5_Z3_NEED Zone 3 sessions in the last 6 weeks (推估), or the Zone 5 track already under way.
Zone 3 keeps going after Zone 5 opens. 課表偏好 2 a week → one of each; 1 a week with both open →
alternate by the A race (track_ratio: ≤ 10 km road 1:1, else 2:1; 推估). A step moves one rung per
planned session 達標 in the last 8 weeks. Each step is a library variant fitted to the day
(engine/interval_library.py). The recovery-week fartlek is not a step. A held week repeats
the last step. The step moves by the progression state machine of
docs/research/interval-adaptation.md §4.3 (interval_outcome / dose_step):
達標 forward, 邊界 repeat, 未適應 rest +1 min then back one step, first rep
short = target −5 %. The old "last rep 5 % below the first -> back one" rule is
gone (the WKO5 speakers oppose it).

專項期 / 減量期 run the same two-track choice with their own sessions (overview.quality_sessions);
their guard: intensity and drift not bad, and in 專項期 (owner 2026-10-04) this week's CTL ramp
(RAMP_SUB threshold only / RAMP_BLOCK none) and > STEP_BLOCK volume step on both tracks, as in the
base phase — 減量期, race / recovery weeks and the re-entry block stay exempt.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

import numpy as np

from backend.i18n import _

# "xu_signals" (an old unlock path) was dropped 2026-10-01: stored prefs that still say it fall back to
# auto (plan_prefs.from_settings; evaluate() maps any unknown mode to auto too)
MODES = ("auto", "ua_gap", "friel_drift", "xu_drift", "plateau", "weeks", "none")
WEEKS_RANGE = (2, 16)
LABEL = {"auto": "自動", "ua_gap": "Uphill Athlete 差距法", "friel_drift": "Friel 飄移法",
         "xu_drift": "徐國峰 90 分鐘法", "plateau": "有氧停滯法", "weeks": "週數法",
         "none": "不設門檻（Seiler）"}

SRC_UA = "Uphill Athlete：When to add intensity（AnT/AeT − 1 ≤ 10%，先加 Zone 3）"
SRC_FRIEL = "Friel（TrainingPeaks：Aerobic decoupling < 5%，跑步在 AeT 1–2 小時）"
SRC_XU = "徐國峰《跑者都該懂的跑步數據》"
SRC_SEILER = "Seiler 2010；Seiler & Tønnessen 2009（整個週期都有少量高強度，每週 1–3 次）"
SRC_PALLADINO = "Palladino 基礎期訓練"
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
XU_HEAT_C = 25.0               # 台灣教練's condition: advice in the session text, not a refusal (heat bands)
PLATEAU_WEEKS = 8              # 自訂
EF_PLATEAU = 0.02              # status.EF_TREND
LOW_SHARE_MIN = 0.75           # status.LOW_SHARE_GOOD (Seiler, by time)
RAMP_SUB, RAMP_BLOCK = 5.0, 8.0            # status.RAMP elite / short: Friel 5–8, 10 the ceiling (coach; B2)
STEP_HOLD, STEP_BLOCK = 0.10, 0.20         # > 20 % block: Nielsen 2014, Damsted 2019 (peer-reviewed); 10–20 % hold 推估
TSB_HOLD = -20.0                           # Friel / TrainingPeaks TSB bands (coach)
ZONE3_SESSIONS = 3             # 自訂: ua_gap unlock → this many Zone 3 sessions, then the dose table
REP_PCT, REP_MIN_S, DOSE_MIN_REPS = 0.95, 40, 4   # 自訂: a short-rep session = ≥ 4 bouts ≥ 40 s at ≥ 95 % CP
FADE = 0.05                    # workout_review.FADE

# ---- the dose ladder: Zone 3 first, then Zone 5 (台灣教練) ----------------------------
# the first quality session is Zone 3; Zone 5 once Zone 3 is steady and recovery keeps up;
# Zone 5 reps ≥ 2 min, ≤ 2 sessions a week, ≥ 2 days apart (台灣教練). Zone 5 also needs the
# aerobic base confirmed (engine/base_check.z5_status). The old ladder started with 5×1′ @ 98–101 % CP —
# too short to train VO2max yet a Zone 5 load.
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


# ---- two tracks (SP-31, 2026-10-04; coach-schools-zones-periodization.md R2) -------------------
# Zone 3 (有氧間歇／節奏) and Zone 5 each have their own ladder, dose step and 達標 count. Zone 3 keeps
# being scheduled after Zone 5 opens (UA 專項期 1 堂 Z3 + 1 堂 Z4; Daniels 主課 + T 次課 — Finding 6:
# no school's norm is two sessions of one intensity).
Z3 = tuple(_rung_row(r) for r in _IL.Z3_TRACK)        # A1 2×15′ → A2 3×12′ → A3 2×20′ → A4 1×30′
CRUISE = tuple(_rung_row(r) for r in _IL.CRUISE_RUNGS)  # T1 3×6′ / T2 3×8′ / T3 2×12′: weekday / low-volume fallback
Z5 = tuple(_rung_row(r) for r in _IL.Z5_TRACK)
TP = _rung_row("tp")             # T+ near-threshold: a Zone 3 track maintenance variant (§A5.3)
LADDER = Z3 + CRUISE + Z5        # every rung row (spec_by_title / ladder_keys)
Z5_Z3_NEED = 2                 # 推估 (coach-schools-zones-periodization.md R3: UA 「Start with Zone 3」, Pfitzinger LT
                               # before VO2max; Daniels / CTS the other way round, no RCT): Zone 5's soft 「3 區先」
Z5_Z3_DAYS = 42                # 推估: … ≥ Z5_Z3_NEED Zone 3 sessions in the last 6 weeks (SP-39; replaces the hard
                               # 「3 堂 3 區達標」 tied to the ladder step). A Zone 5 track already under way keeps it open
Z3_WEEKS_NEED = 4              # 推估: the Zone 3 gate's consistency path — 4 complete weeks of actual training
Z3_RUNS_PER_WEEK = 3           # 推估: … with ≥ 3 runs every week
Z3_MAX_GAP_DAYS = 7            # 推估: … and no stretch of ≥ 7 days without running inside them
Z3_RELOCK_DAYS = 21            # 推估: ≥ 21 days without running re-locks Zone 3 (Coyle 1984: VO2max −7 % at
                               # 21 days; detraining.md §1 「3–8 週開始傷到有氧基礎」). 6–20 days: the re-entry block only
Z3_HISTORY_DAYS = 365          # 自訂: how far back the run dates are read
QUALITY_SHARE_MAX = 0.20       # 推估 (Seiler 80/20, Koop): the week's interval work (Zone 3 + Zone 5 time in zone)
                               # ≤ 20 % of the planned running time — a planning rule, not a gate
Z3_SHARE_START = 0.05          # UA: Zone 3 starts at about 5 % of the weekly aerobic volume (the track's first session)
Z3_SHARE_MAX = 0.10            # Daniels: T running ≤ 10 % of the weekly volume — the per-week Zone 3 cap
# legacy titles of the old ladders: not counted as steps any more (neutral in planned_spec).
# The old z3a 「閾值 3×8 分」 is T2's title: a title-only row reads as z3b now.
LEGACY_TITLES = ("短間歇 5×1 分", "短間歇 6×1 分", "爬坡間歇 4×3 分", "間歇 5×3 分", "VO2max 間歇 4×4 分",
                 "閾值下 3×8 分", "閾值下 4×8 分", "閾值 4×8 分", "閾值 3×10 分")
DOSE = Z3                      # kept for callers that read the first rungs
RECOVERY = ("r1", "恢復週 fartlek 4×1 分", 4, 1, 2, 0.98, 1.01, False, "Palladino 恢復週保留 98–101% CP fartlek")
# the ramp-week session (CTL ramp ≥ 5: threshold only) — T1's content under its own key / title so
# it is never mistaken for a ladder rung (planned_spec: neutral)
SUB = ("sub", "閾值 3×6 分（只排閾值）", 3, 6, 1.5, 0.90, 0.95, False, "CTL ramp ≥ 5（Friel）：只排閾值；90–95% CP")
ZONE3 = ("z3", "Zone 3 間歇", 3, 6, 2, None, None, False, "Uphill Athlete：先加 Zone 3（AeT–LTHR），約週有氧量的 5%")
TRACK_LABEL = {"z3": "3 區（有氧間歇）", "z5": "5 區（VO2max 間歇）"}


def z3_spec(step: int) -> tuple:
    """The Zone 3 track's rung for `step` (its 達標 count): A1–A4, then maintenance rotating
    A3, A4 and T+ (near-threshold; T+ every 3rd session — interval-prescription.md §C5.2-4, 推估)."""
    step = max(0, int(step))
    if step < len(Z3):
        return Z3[step]
    return (Z3[2], Z3[3], TP)[(step - len(Z3)) % 3]


def z5_spec(step: int) -> tuple:
    """The Zone 5 track's rung for `step`: V1–V4, then maintenance rotating V3 / V4 (推估)."""
    step = max(0, int(step))
    if step < len(Z5):
        return Z5[step]
    return (Z5[2], Z5[3])[(step - len(Z5)) % 2]


def track_spec(track: str, step: int) -> tuple:
    return z5_spec(step) if track == "z5" else z3_spec(step)


def dose_spec(step: int, z5_open: bool = True) -> tuple:
    """Legacy single-ladder reading (Zone 3 rungs, then Zone 5 while open): kept for old callers.
    The plan reads the two tracks (z3_spec / z5_spec)."""
    step = max(0, int(step))
    if step < len(CRUISE) or not z5_open:
        return z3_spec(step)
    return z5_spec(step - len(CRUISE))


def z3_budget_min(hours: Optional[float], first: bool = False) -> Optional[float]:
    """The week's Zone 3 time in zone budget (minutes): 10 % of the planned week (Daniels), 5 % for
    the track's first session (UA). None without a week volume."""
    if not hours or hours <= 0:
        return None
    return (Z3_SHARE_START if first else Z3_SHARE_MAX) * float(hours) * 60.0


def cruise_for(rung: str, budget_min: Optional[float]) -> str:
    """The 巡航版 rung a Zone 3 track rung falls back to when its time in zone is over the
    week's budget: T1 / T2 / T3 by the rung's position (A1 → T1, A2 → T2, A3 / A4 → T3), stepping
    down while it is still over the budget (T1 is the floor)."""
    i = min(_IL.Z3_TRACK.index(rung) if rung in _IL.Z3_TRACK else 0, len(CRUISE) - 1)
    while i > 0 and budget_min is not None and _IL.tiz_s(_IL.canonical(CRUISE[i][0])) / 60.0 > budget_min + 1e-6:
        i -= 1
    return CRUISE[i][0]


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
    """The plan's AeT in effect. `measured` = there is a plan row (an applied estimate too —
    the old meaning, kept for the method resolution); `tested` = that row was obtained by a test /
    lab / by hand, not an applied estimate (planning.threshold_method; SP-39: the Zone 5 gate and
    the low-intensity share's Zone 5 block need a tested AeT)."""
    r = aet_row(plan, today)
    if r is None:
        return {"value": None, "date": None, "measured": False, "tested": False, "method": None, "fresh": False,
                "age_days": None, "label": ""}
    from backend.engine.planning import threshold_method
    d = dt.date.fromisoformat(r.date)
    age = (today - d).days
    note = r.note or ""
    how = "活動資料估算" if "自動估算" in note else f"{r.date} 飄移測試" if "飄移測試" in note else f"{r.date} 實測"
    m = threshold_method(r, "aethr")
    return {"value": float(r.aethr), "date": r.date, "measured": True, "tested": m != "estimate", "method": m,
            "fresh": age <= AET_FRESH_DAYS, "age_days": age, "label": f"AeT {r.aethr:.0f}（{how}）"}


def aet_tested(plan, today: dt.date) -> Optional[dict]:
    """The latest plan AeT row on or before `today` that was tested (not an applied estimate):
    {"value", "date", "method", "label"}, else None — the Zone 5 gate's AeT (SP-39). An estimate
    applied after a test doesn't undo the test."""
    if plan is None:
        return None
    from backend.engine.planning import threshold_method
    best = None
    for t in sorted((t for t in (getattr(plan, "thresholds", None) or []) if t.aethr is not None),
                    key=lambda t: t.date):
        if t.date[:10] <= today.isoformat() and threshold_method(t, "aethr") != "estimate":
            best = t
    if best is None:
        return None
    m = threshold_method(best, "aethr")
    return {"value": float(best.aethr), "date": best.date[:10], "method": m,
            "label": f"AeT {best.aethr:.0f}（{best.date[:10]} 實測）"}


def lthr_info(ds, plan, today: dt.date) -> dict:
    """LTHR in effect and whether it is still WKO5's untouched default (i_data's rule).
    `measured`: a plan row from a test / race / lab / by hand (planning.threshold_row), or the
    athlete's own WKO5 setting (not the default) — not an applied estimate (SP-39)."""
    v = plan.threshold_on("lthr", today) if plan is not None else None
    if v is not None:
        r = None
        try:
            from backend.engine.planning import threshold_row
            r = threshold_row(plan, "lthr", today)
        except Exception:                   # noqa: BLE001 — a plan stub without the row helpers
            r = None
        return {"value": float(v), "default": False, "source": "plan",
                "measured": bool(r["measured"]) if r else True, "date": r["date"] if r else None}
    ath = getattr(ds, "athlete", None)
    hist = (getattr(ath, "settings", None) or {}).get("runthr") or []
    val = None
    try:
        val = _f(ath.setting_on("runthr", today)) if ath is not None else None
    except Exception:
        val = None
    default = bool(hist) and all(d == dt.date(1980, 1, 1) for d, _ in hist)
    return {"value": val, "default": default, "source": "wko5", "measured": val is not None and not default,
            "date": None}


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
    # base_check.xu_run with the test's conditions (stops ≤ 30 s, Zone 1, ≤ 25 °C)
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
    return next((s for s in LADDER + (TP, RECOVERY, SUB) if s[1] == str(title)), None)


def row_track(h: dict) -> Optional[str]:
    """The track a dose_history row belongs to: "z3" / "z5", None for an unplanned run of a
    stored plan (neutral on both). By the stored rung / variant / edited structure / title; a
    run without a plan row by its stimulus (a Zone 5 run or ≥ 4 short reps → Zone 5, else Zone 3)."""
    if h.get("unplanned"):
        return None
    rung = h.get("rung_key")
    v = _IL.get(h.get("variant_key"))
    if not rung and v is not None:
        rung = v.rung
    t = _IL.track_of(rung)
    if t is None and v is not None:
        t = "z5" if v.cls == "Z5" else "z3"
    if t is None and user_steps(h):
        try:
            from backend.engine import workout_steps as WS
            sv = WS.variant_from_steps(h["steps"], None)
            if sv is not None:
                t = "z5" if sv.cls == "Z5" else "z3"
        except Exception:                       # noqa: BLE001
            t = None
    if t is None and h.get("title"):
        sp = spec_by_title(h.get("title"))
        t = _IL.track_of(sp[0]) if sp is not None else None
        if t is None and sp is not None:
            t = "z3"                            # RECOVERY / SUB: neutral rows on the Zone 3 side
    if t is None:
        t = "z5" if h.get("stimulus") == "z5" or h.get("rep_source") == "short" else "z3"
    return t


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
        if not row and c["type"] == "hard_long":
            continue                           # 高強度長跑 / 長天: a hard day, not an interval session (owner 2026-10-02)
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
            # Zone 5 by the band middle on Palladino (≥ 106 % CP; interval_library.CLASS_RANGE)
            z5 = (lo + (hi if hi is not None else lo)) / 2 >= _IL.CLASS_RANGE["Z5"][0]
            t_in = IE.tiz_seconds(s["t"], s["power"], m["cp"], lo, hi, z5)
            tiz_ratio = (t_in / plan_tiz) if t_in is not None and plan_tiz else None
        out.append({"idx": w.idx, "date": WR._wdate(w).isoformat(), "title": row.get("title"),
                    **{k: row.get(k) for k in ("variant_key", "rung_key", "equiv", "swap", "variant_reps",
                                               "variant_adj", "variant_blocks", "steps")
                       if row.get(k) is not None},
                    "reps": len(reps) or (m.get("intervals") or {}).get("n") or 0,
                    # informational only now: dose_step judges the bouts (interval_outcome)
                    "faded": fade is not None and fade < -FADE,
                    "bouts": bouts[:20], "cp": m.get("cp"), "rep_source": found["source"], "tiz_ratio": tiz_ratio,
                    "stimulus": c.get("stimulus"),
                    # the stored plan is in use and this run matched none of its quality sessions:
                    # a hard run, not a ladder session (real data 2026-10-01: steady runs at
                    # ~95 % CP were judged 「目標太高」 against 3×8′ and moved the ladder)
                    **({"unplanned": True} if in_use and not row else {})})
        out[-1]["track"] = row_track(out[-1])
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
          aet: Optional[float] = None, injury: Optional[str] = None, aet_tested: bool = True) -> dict:
    """This week's check: {"block", "sub", "hold", "verdict", "action"} — the
    first failing rule speaks. Missing numbers don't block. `injury`: an open
    傷病紀錄 with 「受傷期間暫停強度課」 ticked (engine/injuries.pause_reason)
    blocks intervals until it is resolved — the user's own choice, so it
    speaks first. `aet_tested`: the AeT in effect is a tested one (aet_info["tested"]) — without
    it the low-intensity share is only a warning for Zone 5 too (SP-39)."""
    out = {"block": False, "sub": False, "hold": False, "verdict": "", "action": "", "rule": "", "blocks": [],
           "verdicts": {}, "warn": ""}
    aet_t = f"{aet:.0f} bpm" if aet else "AeT"

    def say(rule, verdict, action, **flags):
        if not out["rule"]:
            out.update(rule=rule, verdict=verdict, action=action)
        out["verdicts"].setdefault(rule, verdict)
        if flags.get("block") and rule not in out["blocks"]:
            out["blocks"].append(rule)
        out.update(flags)
    if injury:
        say("injury", injury, "傷病紀錄按「好了」後恢復", block=True)
    # the low-intensity share (SP-31, owner 2026-10-04): blocks Zone 5 only — for Zone 3 it is a
    # warning (the AeT is often estimated, trail climbs inflate HR); 75 % is the floor, the base
    # phase's ≥ 90 % a target (engine/panels/period_zones.py)
    # SP-39 (owner's Zone 3 decision applied to Zone 5): with an estimated AeT the measured share is
    # noisy (the easy line moves with the estimate), so it only warns for Zone 5 too; a tested AeT
    # keeps the 75 % floor blocking Zone 5
    for share, what in ((low_share, "低強度只有"), (power_low_share, "跑步功率 < 80% CP 只有")):
        if share is not None and share < LOW_SHARE_MIN and not aet_tested:
            out["warn"] = out["warn"] or (f"輕鬆跑心率偏高：{what} {share * 100:.0f}%（底線 75%、基礎期目標 ≥ 90%）"
                                          "——AeT 是估計值、占比不準，只是提醒：3 區、5 區照排")
        elif share is not None and share < LOW_SHARE_MIN:
            say("intensity", f"{what} {share * 100:.0f}%（< 75%，底線）：本週 5 區先不排，3 區照排",
                f"輕鬆跑壓在 {aet_t} 以下，下週再看", block=True)
            out["warn"] = out["warn"] or (f"輕鬆跑心率偏高：{what} {share * 100:.0f}%（底線 75%、基礎期目標 ≥ 90%）"
                                          "——只是提醒，3 區照排；5 區先不排")
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


def guard_blocks(g: dict) -> tuple[Optional[str], Optional[str]]:
    """(why Zone 3 is blocked, why Zone 5 is blocked) — None when it isn't. The low-intensity share
    blocks Zone 5 only (SP-31). A guard without "blocks" (older gates) blocks both by its rule."""
    if not g.get("block"):
        return None, None
    blocks = g.get("blocks")
    if blocks is None:
        blocks = [g.get("rule") or ""]
    vs = g.get("verdicts") or {}
    other = [r for r in blocks if r != "intensity"]
    z3 = (vs.get(other[0]) or g.get("verdict") or "") if other else None
    if other and not z3:
        z3 = g.get("verdict", "")
    z5 = g.get("verdict", "") if blocks else None
    return z3, z5


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


def dose_step(history: list[dict], aet: Optional[float] = None, track: str = "z3") -> dict:
    """Next step of one track (`track` "z3" / "z5"; SP-31: each track has its own ladder and
    達標 count) by replaying that track's interval sessions done (oldest first; row_track), each
    judged against the step it was planned at (interval_outcome):
      達標 -> next step (the ladder adds reps, then rep length, then power)
      邊界 -> the same step again
      未適應 -> same step, rest + 1 min; a second 未適應 in a row -> back one step
      未適應（目標太高）-> same step, target power −5 %
    A session that can't be judged (no bouts or no CP) is 無法判定 and
    repeats the step — progress only on 達標 (unsourced-rules.md §B4; the
    old rule counted it as 達標 unless it `faded`). A missed session isn't in
    the history: the next week repeats the step (engine/adapt.py rule B).
    Zone 3 track: a Zone 3 session off the rung (巡航版 T1–T3 — the old rungs —, T+) is still
    judged and its 達標 counts in `met` (Zone 5's 「3 區達標」), but it doesn't move the rung.
    `faded` stays for the week card."""
    step, streak, adjust, last, met = 0, 0, {}, None, 0
    rows = [h for h in history if (h.get("track") or row_track(h)) == track or h.get("unplanned")]
    for h in rows:
        if h.get("unplanned"):
            h["outcome"] = "neutral"           # not one of the plan's quality sessions
            continue
        by_steps = steps_spec(h, step, track)
        if by_steps is not None:
            # a structure the user edited in the 課表 editor (engine/workout_steps.py): judged by
            # its own reps / band, counted only when it is an equivalent of the rung (§C2)
            spec, neutral, counted = by_steps
        elif h.get("variant_key"):
            # judged by the stored variant (interval-prescription.md §C5.4) — not by the title,
            # which a shortened session changed (bug a: the 4×8′ / 3×10′ steps never moved)
            spec, neutral, counted = variant_spec(h, step, track)
        else:
            spec, neutral = planned_spec(h.get("title"), step, track)
            counted = True
        off_rung = neutral and track == "z3" and spec not in (RECOVERY, SUB, ZONE3) and \
            str(h.get("title") or "") not in LEGACY_TITLES and _IL.track_of(_row_rung(h, spec)) == "z3"
        if neutral and not off_rung:
            # a recovery fartlek / sub-threshold (ramp week) / a session the plan
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
        if off_rung:
            # a Zone 3 session off the track's rung (巡航版 / T+ / the old Zone 3 rungs): judged, its
            # 達標 counts for Zone 5's 「3 區達標」, the rung doesn't move (backward compatible)
            h["counted"] = False
            met += 1 if oc == "met" and counted else 0
            continue
        if not counted:
            # a 縮量版 / non-equivalent swap / the step before under a tight cap: shown, but the
            # rung doesn't move (§C5.4 「判定結果只顯示，不影響階數」)
            h["counted"] = False
            continue
        last = {**o, "outcome": oc, "date": h.get("date"), "step": step}
        if oc == "met":
            step, streak, adjust, met = step + 1, 0, {}, met + 1
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
    out = {"track": track, "done": sum(1 for h in rows if not h.get("unplanned")),
           "faded": bool(last and last["outcome"] != "met"), "step": step, "met": met}
    if last is not None:
        out.update(outcome=last["outcome"], adjust=adjust,
                   note="" if last["outcome"] == "met" else f"上次 {TRACK_LABEL[track].split('（')[0]}間歇"
                                                            f"{OUTCOME_LABEL[last['outcome']]}（{last.get('why') or ''}）：")
    return out


def _row_rung(h: dict, spec: tuple) -> Optional[str]:
    """The rung a history row was planned at: its rung_key, its variant's rung, else the spec's."""
    if h.get("rung_key"):
        return h["rung_key"]
    v = _IL.get(h.get("variant_key"))
    if v is not None:
        return v.rung
    if spec[0] in _IL.LIBRARY:
        return spec[0]
    return getattr(_IL.get(spec[0]), "rung", None)


def dose_tracks(history: list[dict], aet: Optional[float] = None) -> dict:
    """gate["dose"]: {"z3": dose_step(z3), "z5": dose_step(z5), "history"} plus the legacy
    top-level keys (the Zone 3 track's step / adjust / note; done = every session) for older readers."""
    d3 = dose_step(history, aet, "z3")
    d5 = dose_step(history, aet, "z5")
    return {**{k: v for k, v in d3.items() if k != "track"}, "done": sum(1 for h in history if not h.get("unplanned")),
            "z3": d3, "z5": d5}


def ladder_keys() -> tuple:
    return tuple(s[0] for s in LADDER) + (TP[0],)


def variant_tuple(v) -> tuple:
    """A library variant as a ladder row (interval_outcome's spec): n reps, minutes, band."""
    from backend.engine import interval_library as IL
    return (v.key, IL.title(v), v.n, v.works[0] / 60.0, v.rest_s / 60.0, v.lo, v.hi, v.terrain == "hill", v.src)


def variant_spec(h: dict, step: int, track: str = "z3") -> tuple[tuple, bool, bool]:
    """(spec, neutral, counted) of a history row that carries a variant_key:
    neutral when its rung isn't where the track's ladder stands (or it isn't a ladder
    rung: 30/15); counted = equiv (a 縮量版 / non-equivalent swap is
    judged but doesn't move the rung)."""
    from backend.engine import interval_library as IL
    v = IL.resolve(h.get("variant_key"), h.get("variant_reps"), h.get("variant_adj"))
    want = track_spec(track, step)
    if v is None:
        return want, True, False
    rung = h.get("rung_key") or v.rung
    neutral = rung != want[0]
    return variant_tuple(v), neutral, h.get("equiv") is not False


def user_steps(h: dict) -> Optional[dict]:
    st = h.get("steps")
    return st if isinstance(st, dict) and st.get("origin") == "user" and st.get("items") else None


def steps_spec(h: dict, step: int, track: str = "z3") -> Optional[tuple[tuple, bool, bool]]:
    """(spec, neutral, counted) of a row whose structure the user edited
    (workout_steps.variant_from_steps), or None (no such structure / no timed work
    step with an intensity: the variant / title path decides). The rung is the
    session's own (rung_key / its variant's); a structure without one is judged at
    the track's current rung when it is the same class, else neutral. An HR-only
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
    want = track_spec(track, step)
    if not rung or rung not in IL.LIBRARY:
        c = IL.canonical(want[0])
        if c is None or c.cls != v.cls:
            return variant_tuple(v), True, False
        rung = want[0]
    ok = IL.equivalent(v, IL.canonical(rung))[0]
    h["steps_equiv"] = ok
    h["steps_estimated"] = v.src_kind == "推估"
    return variant_tuple(v), rung != want[0], ok


def planned_spec(title: Optional[str], step: int, track: str = "z3") -> tuple[tuple, bool]:
    """(the spec the session was planned at, neutral). By the stored plan's
    title when there is one (dose_history reads it), else the track's step.
    Judged only when the title is the rung the track stands at; neutral = a
    session outside that position: RECOVERY, ZONE3, a SUB (ramp week), another
    rung, and the old ladder's titles (LEGACY_TITLES)."""
    want = track_spec(track, step)
    if title:
        t = str(title)
        if t == RECOVERY[1] or t.startswith("Zone 3"):
            return RECOVERY if t == RECOVERY[1] else ZONE3, True
        if t in LEGACY_TITLES or t == SUB[1]:
            return (SUB if t == SUB[1] else want), True
        for s in LADDER + (TP,):
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
        src = f"{src}；上次未適應：組休 +{int(adjust['rest_add'])} 分"
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
              injury=_injury_pause(today), aet_tested=bool(ae.get("tested")))
    hist = []
    try:
        hist = dose_history(ds, today)
    except Exception:
        hist = []
    dose = dose_tracks(hist, ae.get("value"))
    # ---- Zone 5 (engine/base_check.py) and the AeT test's reason -----------
    z5 = _z5(ds, today, mode, state, ae, lt, brk, [h.get("date") for h in hist], friel,
             aet_tested(plan, today))
    test_reason = aet_test_reason(ds, today, ae, z5, brk)
    # ---- the Zone 3 gate (SP-31) and the 1-a-week track ratio ---------------
    z3 = z3_gate(ds, today, mode, state, ae, lt, z5, dose)
    out = {
        "mode": mode, "mode_label": LABEL[mode], "resolved": resolved, "state": state,
        "via": r.get("via"), "verdict": r.get("verdict", ""), "action": r.get("action", ""),
        "prefix": r.get("prefix", ""), "info": bool(r.get("info")),
        "fallback": state == "missing",        # forced mode, data missing → the guardrails (自訂)
        "stale_aet": stale, "weeks_need": need_weeks, "base_start": str(base_start)[:10] if base_start else None,
        "base_weeks": base_weeks, "aet": ae,
        "lthr": {"value": lt["value"], "default": lt["default"], "measured": bool(lt.get("measured")),
                 "date": lt.get("date")},
        "gap": gap, "ef_change": ef, "levels": levels, "guard": g,
        "dose": {**dose, "history": hist[-8:]},
        "kind": kind, "week_hours": _extra(by, "volume").get("last_week"),
        "z5": z5, "aet_test_reason": test_reason, "reentry": brk,
        "z3": z3, "ratio": track_ratio(getattr(plan, "events", None) or (), today),
        "monday": (today - dt.timedelta(days=today.weekday())).isoformat(),
        "z3_recent": z3_recent(hist, today),
    }
    out["z5_gate"] = z5_track(out)
    out["options"] = options(out, ae, lt, cache, friel, xu, base_weeks, ef, need_weeks)
    return out


def _injury_pause(today: dt.date) -> Optional[str]:
    try:
        from backend.engine import injuries as INJ
        return INJ.pause_reason(INJ.load_events(), today)
    except Exception:                       # noqa: BLE001 — the gate must still evaluate
        return None


# ---------------------------------------------------------------------------
# the Zone 3 gate (SP-31; coach-schools-zones-periodization.md R3) and the track ratio
# ---------------------------------------------------------------------------

SRC_Z3 = {
    "weeks": "推估：連續 4 週規律訓練（每週 ≥ 3 次、沒有 ≥ 7 天沒跑）——UA 登山計畫 4 週基礎後才出現第一堂 Z3；"
             "Pfitzinger 第一個週期 5 週耐力；停跑 ≥ 21 天重新累積（Coyle 1984：21 天 VO2max −7%）",
    "xu90": "徐國峰部落格（2016-12）：90 分鐘平路 1 區，飄移 < 10%",
    "ua_gap": SRC_UA,
    "ratio": "推估（研究 Finding 6 的週內配置：UA 專項期 1 堂 Z3＋1 堂 Z4、Daniels 主課＋T 次課）："
             "每週 1 堂時，目標 ≤ 10 km 路跑 3 區：5 區 = 1:1，半馬以上／越野／沒有 A 賽 2:1",
    "volume": "Daniels：T 每週不超過週量 10%；Uphill Athlete：Zone 3 起步約週有氧量 5%",
    "share": "推估：一週間歇（3 區＋5 區的目標區時間）≤ 跑步時間 20%（Seiler 80/20；Koop）",
}


def run_days(ds, today: dt.date, days: int = Z3_HISTORY_DAYS) -> list[dt.date]:
    """The dates with a run, up to `today`, oldest first (imported history counts)."""
    from backend.engine.wko5expr.dataset import date_to_day, day_to_date
    tday = math.floor(date_to_day(today))
    return sorted({day_to_date(math.floor(w.day)) for w in ds.workouts
                   if w.sport == "run" and tday - days < math.floor(w.day) <= tday})


def z3_consistency(days: list, today: dt.date, need: int = Z3_WEEKS_NEED) -> dict:
    """The Zone 3 gate's consistency path on the run dates (SP-31): a window of `need` complete
    weeks, each with ≥ Z3_RUNS_PER_WEEK runs and no Z3_MAX_GAP_DAYS-day stretch without running,
    after the last break of ≥ Z3_RELOCK_DAYS days (which re-locks; a break still going on too).
    Once such a window exists the path stays open (sticky — a 6–20-day break only gets the
    re-entry block). {"open", "since", "weeks" (the trailing complete weeks that pass, for
    the progress line), "rows" (the last `need` weeks: monday, runs, ok), "break"}."""
    mon = today - dt.timedelta(days=today.weekday())
    brk = None
    prev = None
    for d in list(days) + [today + dt.timedelta(days=1)]:
        if prev is not None and (d - prev).days - 1 >= Z3_RELOCK_DAYS:
            brk = {"last_run": prev.isoformat(), "days": (d - prev).days - 1,
                   "return": d.isoformat() if d <= today else None}
        prev = d
    if not days:
        brk = None
    start = dt.date.fromisoformat(brk["return"]) if brk and brk["return"] else (days[0] if days else today)
    first_mon = start - dt.timedelta(days=start.weekday())
    if brk and brk["return"] and start != first_mon:
        first_mon += dt.timedelta(weeks=1)              # the return week isn't complete training
    weeks = []
    m = first_mon
    while m < mon:
        weeks.append(m)
        m += dt.timedelta(weeks=1)
    runs = {w: sum(1 for d in days if w <= d < w + dt.timedelta(weeks=1)) for w in weeks}

    def gap_ok(w0: dt.date, w1: dt.date) -> bool:
        ds_ = [d for d in days if w0 <= d < w1]
        return all((b - a).days - 1 < Z3_MAX_GAP_DAYS for a, b in zip(ds_, ds_[1:]))
    since = None
    if not (brk and brk["return"] is None):
        for i in range(len(weeks) - need + 1):
            win = weeks[i:i + need]
            if all(runs[w] >= Z3_RUNS_PER_WEEK for w in win) and gap_ok(win[0], win[-1] + dt.timedelta(weeks=1)):
                since = (win[-1] + dt.timedelta(weeks=1)).isoformat()
                break
    trail = 0
    for k in range(len(weeks), 0, -1):
        win = weeks[k - 1:]
        if runs[weeks[k - 1]] >= Z3_RUNS_PER_WEEK and gap_ok(win[0], mon):
            trail += 1
        else:
            break
    rows = []
    for k in range(need, 0, -1):
        w = mon - dt.timedelta(weeks=k)
        n = sum(1 for d in days if w <= d < w + dt.timedelta(weeks=1))
        rows.append({"monday": w.isoformat(), "runs": n, "ok": n >= Z3_RUNS_PER_WEEK and w >= first_mon})
    return {"open": since is not None, "since": since, "weeks": trail, "rows": rows, "break": brk}


def z3_gate(ds, today: dt.date, mode: str, state: Optional[str], ae: dict, lt: dict, z5: dict,
            dose: dict) -> dict:
    """Is the Zone 3 track open (SP-31, the owner's rule 2026-10-04)? Any one of:
      weeks   consistency: Z3_WEEKS_NEED complete weeks of actual training (imported history counts,
              whatever the phase label) with ≥ Z3_RUNS_PER_WEEK runs every week and no
              Z3_MAX_GAP_DAYS-day stretch without running (z3_consistency; 推估) — sticky once met
      xu90    a 徐國峰 90-min test with drift < 10 % (base_check.xu_runs)
      ua_gap  a measured AeT with LTHR / AeT − 1 ≤ 10 % (Uphill Athlete)
    plus what already shows the base is there: mode none (no gate, Seiler), the chosen 間歇門檻
    method unlocked, an aerobic-base confirmation of the Zone 5 process, the re-entry rule asking
    for Zone 3, or a Zone 3 session 達標 in the last 8 weeks. No low-intensity-share condition
    (the AeT is often estimated and climbs inflate HR). A break of ≥ Z3_RELOCK_DAYS days without
    running re-locks it: only what comes after the break counts (and a break still going on
    locks). This week's guardrails apply on top (week_decision).
    {"open", "path", "path_label", "weeks", "weeks_need", "weekly", "tests", "reason", "text", "src", "break"}."""
    need = Z3_WEEKS_NEED
    try:
        cons = z3_consistency(run_days(ds, today), today, need)
    except Exception:                       # noqa: BLE001 — the gate must still evaluate
        cons = {"open": False, "since": None, "weeks": 0, "rows": [], "break": None}
    brk = cons.get("break")
    after = brk.get("return") if brk else None          # evidence before a ≥ 21-day break doesn't count
    resting = bool(brk) and not brk.get("return")
    ok_after = lambda d: not brk or (after is not None and bool(d) and str(d)[:10] >= after)
    try:
        from backend.engine import base_check as BC
        xs = BC.xu_runs(ds, today)
    except Exception:                       # noqa: BLE001
        xs = []
    xu_ok = next((x for x in reversed(xs) if x.get("ok") and ok_after(x.get("date"))), None)
    ua = ua_gap_method(ae, lt)
    ua_ok = ua["state"] == "unlocked" and ok_after(ae.get("date"))
    d3 = dose.get("z3") or {}
    pause = z5.get("pause") or {}
    short = [r for r in cons.get("rows") or [] if not r["ok"]]
    tests = [
        {"key": "weeks", "label": f"連續 {need} 週，每週跑 ≥ {Z3_RUNS_PER_WEEK} 次、沒有 ≥ {Z3_MAX_GAP_DAYS} 天沒跑（推估）",
         "ok": bool(cons["open"]),
         "value": (f"{cons['since']} 起達成" if cons["open"] else f"{min(cons['weeks'], need)}/{need} 週"
                   + (f"（{short[-1]['monday'][5:]} 那週跑 {short[-1]['runs']} 次）" if short else "")),
         "need": f"{need} 週", "src": SRC_Z3["weeks"]},
        {"key": "xu90", "label": "徐國峰 90 分鐘測試：飄移 < 10%", "ok": True if xu_ok else (False if xs else None),
         "value": _xu_value(xu_ok or (xs[-1] if xs else None)), "need": "< 10%", "src": SRC_Z3["xu90"]},
        {"key": "ua_gap", "label": "UA 差距法：實測 AeT，LTHR ÷ AeT − 1 ≤ 10%",
         "ok": True if ua_ok else (False if ua["state"] == "locked" else None),
         "value": ua.get("verdict") or "", "need": "≤ 10%", "src": SRC_Z3["ua_gap"]},
    ]
    path, label = None, ""
    if mode == "none":
        path, label = "none", "不設門檻（Seiler）"
    elif resting:
        path = None
    elif cons["open"]:
        path, label = "weeks", f"連續 {need} 週規律訓練（{cons['since']} 起）"
    elif xu_ok:
        path, label = "xu90", f"90 分鐘飄移 {xu_ok['drift'] * 100:.1f}% < 10%（{xu_ok['date']}）"
    elif ua_ok:
        path, label = "ua_gap", f"UA 差距 {ua['gap'] * 100:.0f}% ≤ 10%"
    elif state == "unlocked" and not brk:
        path, label = "method", f"間歇門檻已解鎖（{LABEL.get(mode, mode)}）"
    elif (z5.get("since") and ok_after(z5.get("since"))) or z5.get("open"):
        path, label = "z5", "有氧基礎已確認（5 區流程）"
    elif pause.get("kind") == "reentry_z3":
        path, label = "reentry", "恢復期後先排 3 區"
    elif not brk and int(d3.get("met") or 0) + int(d3.get("step") or 0) > 0:
        path, label = "track", "8 週內有 3 區達標，繼續階梯"
    if resting:
        reason = f"3 區還沒解鎖：已經 {brk['days']} 天沒跑（≥ {Z3_RELOCK_DAYS} 天要重新累積；推估）"
    elif path:
        reason = ""
    else:
        reason = (f"3 區還沒解鎖：連續 {min(cons['weeks'], need)}/{need} 週每週跑 ≥ {Z3_RUNS_PER_WEEK} 次（推估）"
                  + (f"——{short[-1]['monday'][5:]} 那週跑 {short[-1]['runs']} 次" if short else "")
                  + (f"；停跑 {brk['days']} 天（≥ {Z3_RELOCK_DAYS} 天）後重新累積" if brk else "")
                  + "；或做一次 90 分鐘平路 1 區測試（飄移 < 10%）；或實測 AeT 且 UA 差距 ≤ 10%")
    return {"open": path is not None, "path": path, "path_label": label, "weeks": cons["weeks"],
            "weeks_need": need, "weekly": cons.get("rows") or [], "tests": tests, "reason": reason,
            "since": cons.get("since"), "break": brk,
            "text": f"Zone 3：已解鎖（{label}）" if path else f"Zone 3：未解鎖（{reason.split('：', 1)[-1]}）",
            "src": SRC_Z3["weeks"]}


def _xu_value(x: Optional[dict]) -> str:
    if not x:
        return "—（還沒做過：半年內沒有 ≥ 90 分鐘的跑步）"
    from backend.engine import base_check as BC
    try:
        return BC.xu_text(x)
    except Exception:                       # noqa: BLE001
        return x.get("date") or ""


def z3_open_on(z3: Optional[dict], monday: Optional[dt.date], gate_monday: Optional[str]) -> bool:
    """The Zone 3 gate on the week of `monday`: open as evaluated; locked only by the time path
    opens in a projected week once the streak would reach Z3_WEEKS_NEED (each projected week is
    assumed to pass its guardrails — they are re-checked when it comes; 推估). A gate without a
    Zone 3 part (older stored gates, legacy callers) is open."""
    if not isinstance(z3, dict):
        return True
    if z3.get("open"):
        return True
    if monday is None or not gate_monday:
        return False
    ahead = (monday - dt.date.fromisoformat(str(gate_monday)[:10])).days // 7
    return ahead > 0 and int(z3.get("weeks") or 0) + ahead >= int(z3.get("weeks_need") or Z3_WEEKS_NEED)


def track_ratio(events, today: dt.date) -> dict:
    """The 1-a-week alternation (推估): the next A race a road race ≤ 10 km → Zone 3 : Zone 5 = 1:1;
    a half marathon or longer, a trail race / 百岳, or no A race → 2:1. {"z3", "z5", "why"}."""
    ahead = sorted((e for e in events or () if getattr(e, "priority", "A") == "A" and getattr(e, "kind", "") in
                    ("race", "road", "baiyue") and e.start >= today), key=lambda e: e.start)
    e = ahead[0] if ahead else None
    if e is not None and e.kind == "road" and e.distance_km and e.distance_km <= 10.0:
        return {"z3": 1, "z5": 1, "why": f"A 賽 {e.distance_km:g} km 路跑（≤ 10 km）"}
    if e is None:
        return {"z3": 2, "z5": 1, "why": "沒有 A 賽"}
    what = "越野" if e.kind != "road" else f"{e.distance_km:g} km 路跑" if e.distance_km else "路跑"
    return {"z3": 2, "z5": 1, "why": f"A 賽{what}"}


def z5_ua_gap(ta: Optional[dict], lt: dict) -> Optional[dict]:
    """The Zone 5 gate's UA path (SP-39): a tested AeT (aet_tested) and a measured LTHR
    (lthr_info["measured"]) with LTHR ÷ AeT − 1 ≤ 10 % → {"gap", "date" (the later of the two
    rows), "ok"}; None when either isn't measured."""
    if not ta or lt.get("value") is None or lt.get("default") or not lt.get("measured"):
        return None
    g = ua_gap(ta["value"], lt["value"])
    if g is None:
        return None
    d = max(str(ta.get("date") or ""), str(lt.get("date") or ""))
    return {"gap": g, "date": d or ta.get("date"), "ok": g <= UA_GAP_MAX}


def z3_recent(history: list[dict], today: dt.date, days: int = Z5_Z3_DAYS) -> dict:
    """Zone 5's soft condition (SP-39): the Zone 3 sessions done in the `days` before `today`
    (dose_history rows on the Zone 3 track — ladder, 巡航版, T+, the ramp week's 閾值 — not an
    unplanned hard run, not the recovery-week fartlek). Done counts, 達標 or not.
    {"done", "need", "days", "dates", "ok"}."""
    lo = (today - dt.timedelta(days=days)).isoformat()
    dates = sorted(h["date"] for h in history or () if h.get("date") and lo <= h["date"] < today.isoformat()
                   and not h.get("unplanned") and h.get("title") != RECOVERY[1]
                   and (h.get("track") or row_track(h)) == "z3")
    return {"done": len(dates), "need": Z5_Z3_NEED, "days": days, "dates": dates, "ok": len(dates) >= Z5_Z3_NEED}


def z5_track(gate: dict, monday: Optional[dt.date] = None, steps=None) -> dict:
    """Is the Zone 5 track open (SP-39)? The one flag week_decision, the flow and the change log
    read: the measured-AeT gate (gate["z5"]: base_check.z5_status through _z5) AND the soft
    「3 區先」 — ≥ Z5_Z3_NEED Zone 3 sessions in the last Z5_Z3_DAYS days (z3_recent) or the Zone 5
    track already under way (a step or a session done). `monday` + `steps["z3_dates"]`: a
    projected week counts the dates in its own 6-week window. A gate from before SP-39 (no
    z3_recent) counts its Zone 3 達標. {"open", "aet_ok", "z3_ok", "under_way", "done", "need",
    "reason", "text"}."""
    z5 = gate.get("z5") or {}
    d3, d5 = _track_doses(gate)
    st = steps if isinstance(steps, dict) else {}
    s5 = int(st.get("z5", d5.get("step") or 0))
    under = s5 > 0 or int(d5.get("done") or 0) > 0
    rec = gate.get("z3_recent")
    if st.get("z3_dates") is not None and monday is not None:
        lo, hi = (monday - dt.timedelta(days=Z5_Z3_DAYS)).isoformat(), monday.isoformat()
        n = sum(1 for d in st["z3_dates"] if lo <= str(d)[:10] < hi)
    elif isinstance(rec, dict):
        n = int(rec.get("done") or 0)
    else:
        n = int(st.get("met", d3.get("met") or 0))
    aet_ok = bool(z5.get("open"))
    z3_ok = n >= Z5_Z3_NEED or under
    soft = f"近 {Z5_Z3_DAYS // 7} 週 {min(n, Z5_Z3_NEED)}/{Z5_Z3_NEED} 堂 3 區（推估）"
    if aet_ok and z3_ok:
        how = "5 區階梯進行中" if under and n < Z5_Z3_NEED else soft.replace("（推估）", "")
        reason = ""
        text = f"Zone 5：已解鎖（{z5.get('path_label') or z5.get('label') or ''}；{how}）"
    elif aet_ok:
        reason = f"還差 3 區：{soft}"
        text = f"Zone 5：未解鎖（AeT 已通過，{reason}）"
    else:
        reason = z5.get("reason") or "還沒有實測 AeT 通過"
        text = f"Zone 5：未解鎖（{reason}）"
    return {"open": aet_ok and z3_ok, "aet_ok": aet_ok, "z3_ok": z3_ok, "under_way": under, "done": n,
            "need": Z5_Z3_NEED, "reason": reason, "text": text}


def _z5(ds, today: dt.date, mode: str, state: Optional[str], ae: dict, lt: dict,
        brk: Optional[dict] = None, quality_dates: Optional[list] = None, friel=None,
        ta: Optional[dict] = None) -> dict:
    """base_check.z5_status with the measured-AeT paths (SP-39): a tested AeT `ta` (aet_tested)
    and a measured LTHR within 10 % (UA gap) → the later row's date; a Friel run < 5 % near the
    tested AeT → its date. The 90-min test is not one (Zone 3 gate only). `friel`: evaluate's
    cached friel_check (used when the AeT in effect is the tested one). Never raises."""
    from backend.engine import base_check as BC
    try:
        paths = {}
        p = BC._paths_for(mode)
        if ta is None and ae.get("measured") and ae.get("tested", True) and ae.get("value") is not None:
            ta = {"value": ae["value"], "date": ae.get("date")}      # a caller without the plan: the AeT in effect
        if ta and "aet_ua_gap" in p:
            u = z5_ua_gap(ta, lt)
            if u and u["ok"]:
                paths["aet_ua_gap"] = u["date"]
        if ta and "aet_friel_drift" in p:
            same = ae.get("value") is not None and float(ae["value"]) == float(ta["value"])
            f = friel() if friel is not None and same else friel_check(ds, today, ta["value"])
            if f.get("state") == "unlocked":
                paths["aet_friel_drift"] = f["run"]["date"]
        return {**BC.z5_status(ds, today, mode, state, paths, brk, quality_dates), "aet_paths": paths,
                "aet_tested": ta}
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
    SP-39: the 90-min run no longer confirms Zone 5, so it no longer stands in for the test
    (the passive re-confirmation is gone); it still counts as interpretable data for no_data."""
    from backend.engine import base_check as BC
    from backend.engine import drift_agg as DA
    val = ae.get("validity") or {}
    try:
        recent = DA.aet_points(ds, today, BC.NO_DATA_DAYS, beta={"beta": 0.0})   # presence only: no β fit
        xu_recent = [r for r in BC.xu_runs(ds, today, BC.NO_DATA_DAYS)]
    except Exception:                       # noqa: BLE001
        recent, xu_recent = [], []
    return _aet_test_reason(today, ae, z5, brk, val, recent, xu_recent, False)


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
        return {"code": "moved", "text": _("從最近的輕鬆跑推估 AeT 約 {v:.0f} bpm，和目前 {now:.0f} 差 {d:+.0f}："
                                           "測一次確認（UA：基礎變好 AeT 會往 AnT 靠）",
                                           v=v, now=float(ae["value"]), d=v - float(ae["value"]))}
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
    "week": "台灣教練：一週約 150–210 分鐘 1 區（訓練指數 30–42 點＝Daniels 強度點數，徐國峰部落格）"
            "——圖上的參考帶，不是解鎖條件",
    "xu90": "徐國峰部落格（2016-12）：平路、停 ≤ 30 秒、心率 1 區，飄移 < 10%；≤ 25 °C：台灣教練",
    "ua": SRC_UA,
    "friel": SRC_FRIEL,
    "z3": "推估（coach-schools-zones-periodization.md R3）：近 6 週 ≥ 2 堂 3 區——UA「Start with Zone 3」、"
          "Pfitzinger 先 LT 後 VO2max、台灣教練先 3 區後 5 區；Daniels／CTS 順序相反，沒有 RCT，所以是軟條件",
    "aet": "Uphill Athlete（AnT/AeT − 1 ≤ 10%，AeT 用 40–60 分飄移測試量）；Friel（在 AeT 跑 ≥ 60 分，decoupling < 5%）",
    "keep": "Hickson 1982：1 區時間保有 2/3 就維持耐力；連 3 週是推估",
    "reentry": "Daniels 表 9.2（恢復期＝停訓天數，期間只有 E 日）；先 3 區：台灣教練；堂數推估；"
               "≥ 4 週要重新確認：Mujika & Padilla 2000",
}


def _z5_day(ds, plan, day: dt.date, mode: str, method_state: Optional[str], dates: list) -> dict:
    """evaluate()'s Zone 5 state for `day` — the same inputs: the plan's AeT /
    LTHR in effect that day, the break that mattered then and the interval
    sessions of the 8 weeks before it."""
    return _z5(ds, day, mode, method_state, aet_info(plan, day), lthr_info(ds, plan, day), _break_on(ds, day),
               [d for d in dates if (day - dt.timedelta(days=LOOKBACK_DAYS)).isoformat() <= d < day.isoformat()],
               ta=aet_tested(plan, day))


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


SCHEDULE_PAGE = "/api/v1/overview/plan/schedule/page"
AET_TEST_PROTOCOL = "ua60"     # the 五區 gate's AeT test: one that yields an AeT number (UA 60′; the dialog offers the rest)


def schedule_action(kind: str, key: str, proto: Optional[str] = None) -> dict:
    """「安排課表」 on an unticked flow item (SP-39): opens the 課表 page's new-session dialog with
    the session preselected, the user picks the day. kind "variant" = an interval-library variant
    (`?add=<variant key>`, saved through POST /sessions with its variant_key, so it counts on its
    ladder); "test" = the dialog's 測試 kind (`?test=aet|cp&proto=<protocol>`); "template" = a
    test template row (`?add=<template key>&proto=<protocol>`)."""
    from urllib.parse import urlencode
    if kind == "variant":
        q = {"add": key}
    elif kind == "test":
        q = {"test": key, "proto": proto or ""}
    else:
        q = {"add": key, **({"proto": proto} if proto else {})}
    return {"type": kind, "key": key, "proto": proto, "href": f"{SCHEDULE_PAGE}?{urlencode(q)}"}


def _rung_action(rung: str) -> Optional[dict]:
    v = _IL.canonical(rung)
    return schedule_action("variant", v.key) if v is not None else None


def z5_card(gate: dict, today: dt.date) -> dict:
    """The overview's 「3 區／5 區解鎖」 card from evaluate()'s result (status.i_gate's extra —
    the same object week_plan decides with). SP-39: two independent gates —
      Zone 3: z3_gate (gate["z3"]) — consistency, the 90-min test or a measured UA gap;
      Zone 5: a measured AeT (UA gap with a measured LTHR, or Friel drift at the tested AeT;
              gate["z5"]) AND the soft 「3 區先」 (z5_track: ≥ 2 Zone 3 sessions in 6 weeks).
    {"state", "label", "headline", "since", "path_label", "reason", "test", "reentry",
     "base": {"label", "ok", "tests"} (the Zone 5 AeT tests), "z3": the soft condition
     {"done", "need", "ok", "src", "step", "z5_step"}, "z3_gate", "z5_gate" (z5_track), "keep",
     "open" (the Zone 5 track), "steps", "next", "flow"}.
    Every test: {"key", "label", "ok" (True / False / None = no data), "value", "need", "src"}."""
    from backend.engine import base_check as BC
    z = gate.get("z5") or {}
    state = z.get("state") or "unconfirmed"
    zt = gate.get("z5_gate") if isinstance(gate.get("z5_gate"), dict) else z5_track(gate)
    out = {"state": state, "label": z.get("label") or BC.STATE_LABEL.get(state, state), "open": bool(zt["open"]),
           "aet_ok": bool(zt["aet_ok"]), "since": z.get("since"), "path": z.get("path"),
           "path_label": z.get("path_label") or "", "reason": z.get("reason") or "", "text": z.get("text") or "",
           "mode": gate.get("mode"), "mode_label": LABEL.get(gate.get("mode") or "auto", ""),
           "test": gate.get("aet_test_reason"), "reentry": None, "keep": None, "z5_gate": zt}
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
    ta = z.get("aet_tested")
    # the AeT step is done once a confirmation stands; a Z1-rule pause needs a new one
    base_done = state in ("confirmed", "open") or (state == "paused" and z.get("since") and
                                                   pause.get("kind") in ("reentry_z3", "drift_check"))
    # a test only counts after the Z1 pause / a ≥ 4-week break (the confirmation before it no longer does)
    after = pause.get("at") if state == "paused" and pause.get("kind") == "z1" else \
        brk.get("return") if brk and brk.get("reconfirm") else None

    def counts(date: Optional[str]) -> bool:
        return bool(date) and (after is None or str(date)[:10] >= str(after)[:10])

    tests = []
    ap = z.get("aet_paths") or {}
    if mode_has(mode, "aet_ua_gap"):
        u = z5_ua_gap(ta, lt)
        if u is not None:
            val = f"AeT {ta['value']:.0f}（{ta['date']} 實測）/ LTHR {lt['value']:.0f} → {u['gap'] * 100:.0f}%"
        elif not ta:
            val = ("—（AeT 是估計值，不算：要做一次 AeT 測試）" if ae.get("measured") or ae.get("value")
                   else "—（還沒做過：沒有實測 AeT）")
        else:
            val = "—（LTHR 不是實測：要做一次 30 分鐘 LTHR 測試）"
        tests.append({"key": "aet_ua_gap", "label": "實測 AeT＋實測 LTHR：LTHR ÷ AeT − 1 ≤ 10%",
                      "ok": (("aet_ua_gap" in ap) and counts(ap.get("aet_ua_gap"))) if u is not None else None,
                      "value": val, "need": "≤ 10%", "src": SRC_Z5["ua"],
                      "missing": "aet" if not ta else "lthr" if u is None else ("gap" if not u["ok"] else "")})
    if mode_has(mode, "aet_friel_drift"):
        fr = (gate.get("options") or {}).get("friel_drift") or {}
        tests.append({"key": "aet_friel_drift", "label": "Friel 飄移：實測 AeT 附近跑 ≥ 60 分鐘，前後半飄移 < 5%",
                      "ok": (("aet_friel_drift" in ap) and counts(ap.get("aet_friel_drift"))) if ta and fr.get("usable")
                      else None,
                      "value": (fr.get("why") or "—") if ta else "—（還沒有實測 AeT）", "need": "< 5%",
                      "src": SRC_Z5["friel"], "missing": "aet" if not ta else ""})
    out["base"] = {"label": "實測 AeT（二選一，做了且達標）" if len(tests) > 1 else
                   f"實測 AeT（{tests[0]['label'].split('：')[0]}）" if tests else "實測 AeT",
                   "ok": bool(base_done), "tests": tests,
                   "empty": ("恢復期內不判斷" if state == "reentry" else
                             "不設門檻（Seiler）" if state == "open" else "" if tests else "這個間歇門檻不開 5 區")}
    d3, d5 = _track_doses(gate)
    out["z3"] = {"done": min(int(zt["done"]), Z5_Z3_NEED), "need": Z5_Z3_NEED, "ok": bool(zt["z3_ok"]),
                 "under_way": bool(zt["under_way"]), "days": Z5_Z3_DAYS, "src": SRC_Z5["z3"],
                 "step": int(d3.get("step") or 0), "z5_step": int(d5.get("step") or 0)}
    z3g = gate.get("z3")
    out["z3_gate"] = z3g if isinstance(z3g, dict) else {"open": True, "path": None, "path_label": "", "tests": [],
                                                       "reason": "", "text": ""}
    mt = z.get("maintenance") or {}
    if state in ("confirmed", "paused") and mt.get("z1_level_min"):
        wk = mt.get("weeks") or []
        last = wk[-1] if wk else None
        out["keep"] = {"level_min": mt["z1_level_min"], "line_min": mt["z1_level_min"] * BC.Z1_KEEP,
                       "last_week": last, "ok": bool(mt.get("ok", True)), "why": mt.get("why") or "",
                       "src": SRC_Z5["keep"]}
    out["next"] = _z5_next(out, z, gate, tests)
    out["steps"] = [
        {"key": "base", "label": out["base"]["label"],
         "status": "done" if base_done else "wait" if state == "reentry" else "active"},
        {"key": "z3", "label": f"近 {Z5_Z3_DAYS // 7} 週 3 區 {out['z3']['done']}/{Z5_Z3_NEED} 堂",
         "status": "done" if zt["z3_ok"] else "active"},
        {"key": "z5", "label": "5 區開放",
         "status": "done" if zt["open"] else "paused" if state in ("paused", "reentry") else "todo"},
    ]
    out["headline"] = ("已解鎖" if zt["open"] else {
        "confirmed": f"AeT 已通過（{z.get('since')}，{out['path_label']}）",
        "paused": "暫停", "reentry": "恢復期", "open": "不設門檻",
    }.get(state, "未解鎖"))
    out["flow"] = z5_flow(out, z, gate, tests, out["z3"]["step"])
    return out


def _test_todo(t: dict, gate: dict, z: dict) -> tuple[str, Optional[dict]]:
    """(the 「what to do next」 line, its 安排課表 action) for a Zone 5 AeT test that isn't passed."""
    from backend.i18n import _
    if t.get("ok") is True:
        return "", None
    k, miss = t["key"], t.get("missing") or ""
    aet_test = schedule_action("test", "aet", AET_TEST_PROTOCOL)
    if miss == "aet":
        return _("做 1 次 AeT 測試（UA 40–60 分，量出 AeT 數字；90 分鐘測試不算）"), aet_test
    if k == "aet_ua_gap":
        if miss == "lthr":
            return _("做 1 次 30 分鐘 LTHR 測試（LTHR 也要實測）"), schedule_action("template", "lib:friel_lthr30", "race")
        if miss == "gap":
            u = z5_ua_gap(z.get("aet_tested"), gate.get("lthr") or {})
            return _("差距 {gap}% → 繼續打底，之後重測 AeT", gap=f"{(u or {}).get('gap', 0) * 100:.0f}"), None
        return _("重測 1 次 AeT（暫停前的不算）"), aet_test
    if k == "aet_friel_drift":
        ta = z.get("aet_tested") or {}
        if ta.get("value"):
            lo, hi = float(ta["value"]) + FRIEL_HR_BAND[0], float(ta["value"]) + FRIEL_HR_BAND[1]
            return (_("在 AeT 附近（{lo}–{hi} bpm）跑 1 次 ≥ 60 分鐘平路，飄移 < 5%", lo=f"{lo:.0f}", hi=f"{hi:.0f}"),
                    schedule_action("test", "aet", "friel"))
        return _("在 AeT 附近跑 1 次 ≥ 60 分鐘，飄移 < 5%"), schedule_action("test", "aet", "friel")
    return "", None


def z5_flow(card: dict, z: dict, gate: dict, tests: list, step: int) -> dict:
    """The 3 區／5 區解鎖流程 as two independent, parallel tracks (SP-39; presentation only:
    every flag comes from z5_card / evaluate() — the Zone 5 one is z5_track, the same flag
    week_decision reads):
      tracks  [{"key": "z3" | "z5", "title", "open", "here", "stages"}]
        三區軌: 3 區解鎖 (z3_gate: three ways in) → 3 區階梯 A1–A4
        五區軌: 5 區解鎖 (a measured AeT — UA gap with a measured LTHR or Friel drift — and the
                soft 「近 6 週 ≥ 2 堂 3 區」) → 5 區階梯 V1–V4
      here    per track {"stage", "title", "next", "action", "also"}: 「你現在在這裡，下一步：…」
      stages  [{"key", "title", "sub", "status": done | current | locked, "items", "any",
                "any_label", "note", "note_tip", "unlocks", "tip"}]
    Every item: {"text", "ok" (True / False / None = unknown), "value", "todo", "tip", "action"}.
    `action` (unticked sessions / tests only): schedule_action — the 課表 page's 「安排課表」.
    `step` is the Zone 3 track's step; the Zone 5 track's comes from card["z3"]["z5_step"]."""
    from backend.i18n import _
    state, B, Z3s, R, K = card["state"], card["base"], card["z3"], card.get("reentry"), card.get("keep")
    zt = card["z5_gate"]
    pause = z.get("pause") or {}
    pk = pause.get("kind") if state == "paused" else None

    def need_src(label, need, src):
        return "\n".join(x for x in (label or "", _("需要 {x}", x=need) if need else "",
                                     _("來源：{x}", x=src) if src else "") if x)

    def item(text, ok, value="", todo="", tip="", action=None):
        return {"text": text, "ok": ok, "value": value or "", "todo": "" if ok is True else (todo or ""),
                "tip": tip or "", "action": None if ok is True else action}

    LEAD = {_("停跑後的恢復期結束")}

    def ladder(rows, at: int, active: bool):
        return [item(r[1], at > i, "", _("完成 1 堂「{t}」，達標就往上一階", t=r[1]) if at == i else "", "",
                     _rung_action(r[0]) if at == i and active else None) for i, r in enumerate(rows)]

    # ---- 三區軌 -------------------------------------------------------------------------
    G = card.get("z3_gate") or {"open": True}
    s1, any1, note1 = [], [], ""
    if state == "reentry" and R:
        s1.append(item(_("停跑後的恢復期結束"), False, _("停跑 {d} 天", d=R["days"]),
                       _("還剩 {n} 天：{date} 前只排輕鬆跑", n=R["days_left"], date=R["quality_from"]),
                       (R.get("text") or "") + "\n" + _("來源：") + R["src"]))
    if G.get("open"):
        if G.get("path_label"):
            s1.append(item(_("3 區已解鎖"), True, G["path_label"]))
    else:
        todo1 = {"weeks": (_("規律跑：每週 ≥ {n} 次、別連續 {g} 天沒跑", n=Z3_RUNS_PER_WEEK, g=Z3_MAX_GAP_DAYS), None),
                 "xu90": (_("做 1 次 90 分鐘平路 1 區測試"), schedule_action("test", "aet", "xu90")),
                 "ua_gap": (_("做 1 次 AeT 測試（LTHR ÷ AeT − 1 ≤ 10% 就算）"),
                            schedule_action("test", "aet", AET_TEST_PROTOCOL))}
        for t in G.get("tests") or []:
            td, act = todo1.get(t["key"], ("", None))
            any1.append(item(t["label"], t.get("ok"), t.get("value"), td,
                             need_src(t["label"], t.get("need"), t.get("src")), act))
        note1 = _("3 區解鎖：三選一")
    lo, hi = z1_target_min()
    tip1 = _("輕鬆跑（1 區）打底。參考：每週約 {lo}–{hi} 分鐘 1 區", lo=f"{lo:.0f}", hi=f"{hi:.0f}") + \
        "\n" + _("來源：") + SRC_Z5["week"]
    done1 = state != "reentry" and bool(G.get("open"))
    s2 = ladder(Z3, step, done1)
    tip2 = (_("3 區解鎖後、護欄通過就照排，5 區開放後也照排") + "\n"
            + _("每週 3 區量 ≤ 週量 10%（Daniels），放不下排巡航版 3×6／3×8／2×12"))
    z3_stages = [
        {"key": "z3_gate", "title": _("3 區解鎖"), "sub": _("有基礎了就能做") if not done1 else "", "items": s1,
         "done": done1, "any": any1, "any_label": note1, "unlocks": _("可以開始排 3 區（有氧間歇／節奏跑）"),
         "tip": tip1, "note": ""},
        {"key": "z3", "title": _("3 區階梯"), "sub": _("A1–A4"), "items": s2, "done": False,
         "unlocks": _("之後維持：A3／A4／T+ 輪替；每週 3 區量 ≤ 週量 10%"), "tip": tip2, "note": ""},
    ]

    # ---- 五區軌 -------------------------------------------------------------------------
    s3, any3, note3, tip_note3 = [], [], "", ""
    if state == "reentry" and R:
        s3.append(item(_("停跑後的恢復期結束"), False, _("停跑 {d} 天", d=R["days"]),
                       _("還剩 {n} 天：{date} 前只排輕鬆跑", n=R["days_left"], date=R["quality_from"]),
                       (R.get("text") or "") + "\n" + _("來源：") + R["src"]))
    short = {"aet_ua_gap": _("AeT＋LTHR 實測：差距 ≤ 10%"), "aet_friel_drift": _("AeT 附近 Friel 飄移 < 5%")}
    if B.get("ok") or state == "open":
        s3.append(item(_("不設門檻（Seiler）") if state == "open" else _("實測 AeT 已通過"), True,
                       "" if state == "open" else f"{card.get('since') or ''} · {card.get('path_label') or ''}"))
    else:
        for t in tests:
            td, act = _test_todo(t, gate, z)
            any3.append(item(short.get(t["key"], t["label"]), t.get("ok"), t.get("value"), td,
                             need_src(t["label"], t.get("need"), t.get("src")), act))
    # the soft 「3 區先」 (推估): the Zone 3 track's next session is what ticks it
    soft_val = (_("5 區階梯進行中") if Z3s.get("under_way") and Z3s["done"] < Z3s["need"]
                else f"{Z3s['done']}/{Z3s['need']}")
    soft_todo = (_("完成 1 堂 3 區（「{t}」）", t=Z3[min(step, len(Z3) - 1)][1]) if done1 else _("先解鎖 3 區"))
    s3.append(item(_("近 {w} 週做過 ≥ {n} 堂 3 區（推估）", w=Z5_Z3_DAYS // 7, n=Z5_Z3_NEED), bool(Z3s["ok"]),
                   soft_val, soft_todo, _("來源：") + Z3s["src"],
                   _rung_action(Z3[min(step, len(Z3) - 1)][0]) if done1 else None))
    if pk == "reentry_z3":
        left = max(0, int(pause.get("need") or 1) - int(pause.get("done") or 0))
        s3.append(item(_("恢復期後的 3 區"), False, f"{pause.get('done', 0)}/{pause.get('need', 1)}",
                       _("再 {n} 堂 3 區", n=left)))
    if pk == "drift_check":
        s3.append(item(_("恢復期後的長跑飄移檢查"), False, "",
                       _("下一次 ≥ 75 分鐘路跑，後段心率、配速各在 ±5% 內"), _("推估")))
    if K:  # keeping the confirmation
        lw = K.get("last_week")
        s3.append(item(_("維持：每週 1 區 ≥ {m} 分", m=f"{K['line_min']:.0f}"), bool(K.get("ok")),
                       _("上週 {m} 分", m=f"{lw['z1_min']:.0f}") if lw else "",
                       _("1 區時間連 3 週低於這條線，5 區會暫停"), _("來源：") + K["src"]))
    if pk == "z1":
        line = f"{K['line_min']:.0f}" if K and K.get("line_min") else "?"
        note3 = _("暫停：每週 1 區時間連 3 週低於確認時的 2/3（{m} 分），要重新做 AeT 測試", m=line)
    elif B.get("empty") and state != "open":
        note3 = B["empty"]
    g = gate.get("guard") or {}
    if zt["open"] and "intensity" in (g.get("blocks") or []):
        note3 = (note3 + "\n" if note3 else "") + _("本週低強度占比 < 75%（實測 AeT）：5 區先不排")
    if card.get("test"):            # 建議測試: a short line, the reason (often long) behind ?
        note3 = (note3 + "\n" if note3 else "") + _("建議做一次測試")
        tip_note3 = card["test"].get("text") or ""
    done3 = bool(zt["open"]) and state not in ("reentry", "paused")
    k5 = int(Z3s.get("z5_step") or 0) if done3 else -1
    s4 = ladder(Z5, k5, done3)
    z5_stages = [
        {"key": "z5_gate", "title": _("5 區解鎖"), "sub": _("一定要實測 AeT") if not done3 else "", "items": s3,
         "done": done3, "any": any3, "any_label": _("實測 AeT：二選一，做了且達標") if len(any3) > 1 else "",
         "unlocks": _("5 區間歇可以排：每趟 2–5 分、一週最多 2 次、隔 ≥ 2 天"),
         "tip": _("90 分鐘測試量不出 AeT 數字，只算 3 區的關卡") + "\n" + _("來源：{x}", x=SRC_Z5["aet"]),
         "note": note3, "note_tip": tip_note3},
        {"key": "z5", "title": _("5 區階梯"), "sub": _("V1–V4"), "items": s4, "done": False,
         "unlocks": _("之後維持：V3／V4 輪替；3 區照排"), "tip": _("每趟 ≥ 2 分、一週最多 2 次、隔 ≥ 2 天"), "note": ""},
    ]

    def track(key, title, stages, open_):
        cur = next(i for i, s in enumerate(stages) if not s["done"])
        for i, s in enumerate(stages):
            s["status"] = "done" if s["done"] else "current" if i == cur else "locked"
            del s["done"]
        c = stages[cur]
        # the order of 「下一步」: what blocks everything (the re-entry block), then the any-of tests
        # (the AeT for Zone 5), then the other conditions
        todos = [it for it in c["items"] if it["ok"] is not True and it["todo"]]
        anys = [it for it in c.get("any") or [] if it["todo"]]
        lead = [it for it in todos if it["text"] in LEAD]
        order = lead + anys + [it for it in todos if it not in lead]
        first = order[0] if order else None
        rest = order[1:]
        nxt = first["todo"] if first else ""
        if key == "z5" and c["key"] == "z5_gate" and not first:
            nxt = card["next"]["text"]
        return {"key": key, "title": title, "open": open_, "stages": stages,
                "here": {"stage": c["key"], "title": c["title"], "next": nxt,
                         "action": first.get("action") if first else None,
                         "also": _("，或").join(x["todo"] for x in rest[:2]) if rest else ""}}

    return {"tracks": [track("z3", _("3 區（有氧間歇／節奏跑）"), z3_stages, done1),
                       track("z5", _("5 區（VO2max 間歇）"), z5_stages, bool(zt["open"]))],
            "full": card["next"]["text"]}


def source_of_mode(mode: str) -> str:
    return OPTION_INFO.get(mode, {}).get("source", "")


def _z5_next(card: dict, z: dict, gate: dict, tests: list) -> dict:
    """The one plain-language line 「5 區還缺什麼」 (card and chart alike):
    {"kind": open | reentry | paused | missing | done, "text"}."""
    state, z3 = card["state"], card["z3"]
    zt = card["z5_gate"]
    pause = z.get("pause") or {}
    R = card.get("reentry")
    soft = (f"近 {Z5_Z3_DAYS // 7} 週再 {max(0, z3['need'] - z3['done'])} 堂 3 區"
            f"（{z3['done']}/{z3['need']}；推估）")
    if state == "reentry" and R:
        after = f"之後先 {R['z3_before_z5']} 堂 3 區" + ("，並重新做 AeT 測試" if R["reconfirm"] else "")
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
        return {"kind": "paused", "text": "還缺：重新做 AeT 測試（UA 差距或 Friel 飄移）；"
                                          f"之後每週 1 區時間別連 3 週低於確認時的 2/3{line}"}
    if zt["aet_ok"]:
        if zt["z3_ok"]:
            if state == "open":
                return {"kind": "open", "text": "不設門檻（Seiler）：5 區照 80/20 安排"}
            return {"kind": "done", "text": "都做到了：5 區可以排（每趟 2–5 分、一週最多 2 次、隔 ≥ 2 天）"}
        return {"kind": "missing", "text": f"還缺：{soft}（3 區解鎖後、護欄通過就照排）"}
    # no measured AeT passing yet: what the cheapest way in needs
    by = {t["key"]: t for t in tests}
    pre = f"停跑 ≥ 4 週：{R['return']} 之後" if R and R.get("reconfirm") else ""
    parts = []
    ua, fr = by.get("aet_ua_gap"), by.get("aet_friel_drift")
    miss = (ua or fr or {}).get("missing")
    if miss == "aet":
        parts.append("做一次 AeT 測試（UA 40–60 分，量出 AeT 數字；90 分鐘測試不算）"
                     + ("，LTHR 也要實測，差距 ≤ 10%" if ua else "") + ("；或之後在 AeT 附近跑 ≥ 60 分鐘、飄移 < 5%" if fr else ""))
    else:
        if ua and ua.get("missing") == "lthr":
            parts.append("做一次 30 分鐘 LTHR 測試（LTHR 也要實測，差距 ≤ 10% 就算）")
        elif ua and ua.get("missing") == "gap":
            parts.append("AeT 和 LTHR 的差距降到 ≤ 10%：繼續有氧基礎，之後重測 AeT")
        elif ua and ua.get("ok") is not True:
            parts.append("重測一次 AeT（暫停前的不算）")
        if fr and fr.get("ok") is not True:
            ta = z.get("aet_tested") or {}
            if ta.get("value"):
                lo, hi = float(ta["value"]) + FRIEL_HR_BAND[0], float(ta["value"]) + FRIEL_HR_BAND[1]
                parts.append(f"在 AeT 附近（{lo:.0f}–{hi:.0f} bpm）跑一次 ≥ 60 分鐘平路穩定跑，前後半飄移 < 5%")
    if not parts:
        return {"kind": "missing", "text": f"還缺：{z.get('reason') or '實測 AeT'}"}
    tail = "" if z3["ok"] else f"；另外 {soft}"
    return {"kind": "missing", "text": "還缺：" + pre + "；或".join(parts) + tail}


def z1_target_min() -> tuple[float, float]:
    """The weekly Zone 1 band in minutes (台灣教練): RQ 30–42 points ÷ 0.2 = 150–210."""
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


def _track_doses(gate: dict) -> tuple[dict, dict]:
    """(Zone 3 dose, Zone 5 dose) of a gate. A gate from before the two tracks (one ladder:
    Zone 3 rungs, then Zone 5 from step 3) reads as its Zone 3 count and its Zone 5 position."""
    d = gate.get("dose") or {}
    if isinstance(d.get("z3"), dict):
        return d["z3"], d.get("z5") or {"step": 0, "done": 0, "met": 0}
    s = int(d.get("step") or 0)
    return ({"step": min(s, len(CRUISE)), "met": min(s, len(CRUISE)), "done": d.get("done", 0),
             "adjust": d.get("adjust"), "faded": d.get("faded"), "note": d.get("note")},
            {"step": max(0, s - len(CRUISE)), "met": 0, "done": max(0, s - len(CRUISE))})


def rung_now(gate: Optional[dict], track: Optional[str] = None) -> Optional[str]:
    """The ladder rung a track stands at (SP-31: each track has its own). `track` None: this
    week's first ladder session (week_decision), else the Zone 3 track's. None without a gate."""
    if not gate or not gate.get("state"):
        return None
    if track is None:
        spec = week_decision(gate, "base", "base").get("spec")
        if spec is not None and spec[0] in _IL.LIBRARY:
            return spec[0]
        track = "z3"
    d3, d5 = _track_doses(gate)
    return track_spec(track, int((d5 if track == "z5" else d3).get("step") or 0))[0]


def _week_index(monday: Optional[dt.date]) -> int:
    return 0 if monday is None else (monday.toordinal() - 1) // 7


def week_decision(gate: dict, kind: str, mode: str, monday: Optional[dt.date] = None,
                  step=None, first: bool = True, n: int = 1) -> dict:
    """One week's interval sessions (SP-31: two tracks). {"allow", "items", "spec",
    "advance", "adjust", "track", "note", "z3_note"}; `items` = [{"track" z3 | z5 | None,
    "spec", "advance", "adjust", "first"}] in schedule order and `spec` / `advance` /
    `adjust` / `track` are the first item's (older callers). `first` = this week (today's
    guardrails apply); projected weeks only keep the slow-moving intensity guard — ramp,
    volume and TSB are re-checked when the week comes. `step`: the track steps reached by a
    projected week ({"z3", "z5", "met"}; an int = the Zone 3 step). `n`: intervals wanted
    this week (課表偏好 每週品質課 2 → one Zone 3 + one Zone 5 when both are open).
    Tracks: Zone 3 when its gate is open (z3_gate); Zone 5 when z5_track says so (SP-39: a
    measured AeT passed — gate["z5"] — and ≥ Z5_Z3_NEED Zone 3 sessions in the last 6 weeks, or
    the Zone 5 track already under way; the same flag as the flow). `step["z3_dates"]`: the
    Zone 3 session dates a projected week counts. One a week with both open: alternate by
    gate["ratio"] (track_ratio), by week.
    `z3_note`: why this week has no Zone 3 session (the 總覽／課表 note), "" when it has one."""
    kind = kind or "base"
    levels = gate.get("levels") or {}
    gi = gate.get("guard") or {}

    def none(note: str, z3_note: Optional[str] = None, allow: bool = False) -> dict:
        return {"allow": allow, "spec": None, "advance": False, "adjust": None, "track": None, "items": [],
                "note": note, "z3_note": z3_note if z3_note is not None else (f"本週沒排 3 區：{note}" if note else "")}
    if gi.get("rule") == "injury" and gi.get("block"):
        # 傷病紀錄「受傷期間暫停強度課」: every phase, every week until the event is resolved
        return none(gi.get("verdict", ""))
    z5 = gate.get("z5") or {}
    d3, d5 = _track_doses(gate)
    steps = step if isinstance(step, dict) else ({"z3": int(step)} if step is not None else {})
    s3 = int(steps.get("z3", d3.get("step") or 0))
    s5 = int(steps.get("z5", d5.get("step") or 0))
    met = int(steps.get("met", d3.get("met") or 0))
    z3g = gate.get("z3")
    z3_open = z3_open_on(z3g, monday, gate.get("monday"))
    # SP-39: the weeks method no longer opens Zone 5 by itself (a measured AeT does)
    zt = z5_track(gate, monday if (step is not None or not first) else None, steps)
    z5_ok = zt["open"]
    avail = [t for t, ok in (("z3", z3_open), ("z5", z5_ok)) if ok]
    lock = "" if z3_open else \
        "本週沒排 3 區（還沒解鎖）：" + ((z3g or {}).get("reason") or "").removeprefix("3 區還沒解鎖：")
    if kind != "base":
        ok = levels.get("intensity") != "bad" and levels.get("drift") != "bad"
        if kind not in ("specific", "taper"):
            return {**none(""), "allow": ok, "z3_note": ""}
        if levels.get("drift") == "bad":
            return none("", "本週沒排 3 區：心率飄移是 bad，先不排強度課")
        if kind == "specific" and first and mode != "reentry":
            # this week's load-progression guardrails apply in 專項期 too, to both tracks, as in the base
            # phase (owner 2026-10-04: no school exempts it — Friel ramp 5–8, Nielsen 2014 / Damsted 2019
            # > 20 % steps; unsourced-rules.md B2): CTL ramp ≥ RAMP_BLOCK or a > STEP_BLOCK volume step →
            # no interval, ramp ≥ RAMP_SUB → threshold only. 減量期, race / recovery weeks and the re-entry
            # block stay exempt; projected weeks are re-checked when they come
            g = gate.get("guard") or {}
            blocks = g.get("blocks") if g.get("blocks") is not None else ([g.get("rule")] if g.get("block") else [])
            load = [r for r in blocks if r in ("ramp", "volume")]
            if load:
                return none((g.get("verdicts") or {}).get(load[0]) or g.get("verdict", ""))
            if g.get("sub"):
                v = (g.get("verdicts") or {}).get("ramp") or g.get("verdict", "")
                if "z3" not in avail:
                    return none(v)
                return {"allow": True, "spec": SUB, "advance": False, "adjust": None, "track": "z3", "note": v,
                        "items": [{"track": "z3", "spec": SUB, "advance": False, "adjust": None, "first": False}],
                        "z3_note": ""}
        warn = ""
        if levels.get("intensity") == "bad":
            # the low-intensity share keeps Zone 5 out, Zone 3 goes on with a warning (SP-31); with an
            # estimated AeT the share is noisy: a warning for Zone 5 too (SP-39)
            if (gate.get("aet") or {}).get("tested", True):
                avail = [t for t in avail if t != "z5"]
                warn = "輕鬆跑心率偏高（強度分配是 bad）——只是提醒，3 區照排；5 區先不排"
            else:
                warn = "輕鬆跑心率偏高（強度分配是 bad）——AeT 是估計值、占比不準，只是提醒：3 區、5 區照排"
        items = _pick_tracks(avail, n, monday, gate, s3, s5, d3, first and step is None, met)
        out = _decision(items, avail, gate, n, monday, lock, zt)
        return {**out, "allow": True if kind == "taper" else bool(items), "warn": warn if items else ""}
    if first and z5.get("state") == "reentry":
        # inside a re-entry block: E days only (Daniels table 9.2; engine/reentry.py)
        return none(z5.get("text", ""))
    g = gate.get("guard") or {}
    if not first:
        # a projected week keeps only the slow-moving intensity guard (Zone 5 only, SP-31)
        keep = bool(g.get("block")) and "intensity" in (g.get("blocks") if g.get("blocks") is not None
                                                          else [g.get("rule")])
        g = {"block": keep, "blocks": ["intensity"] if keep else [], "rule": "intensity" if keep else "",
             "verdict": (g.get("verdicts") or {}).get("intensity") or g.get("verdict", "") if keep else "",
             "sub": False, "hold": False, "warn": g.get("warn", "") if keep else ""}
    b3, b5 = guard_blocks(g)
    if b3 is not None:
        return none(b3)                                    # ramp / volume / … : no interval at all
    if b5 is not None:
        avail = [t for t in avail if t != "z5"]            # the low-intensity share: Zone 5 waits
    if mode == "recovery_week":
        return {"allow": True, "spec": RECOVERY, "advance": False, "adjust": None, "track": None, "note": "",
                "items": [{"track": None, "spec": RECOVERY, "advance": False, "adjust": None, "first": False}],
                "z3_note": "恢復週：只排 4×1 分 fartlek，3 區下週再排"}
    if not avail:
        return none("", lock)
    if g.get("sub"):
        if "z3" not in avail:
            return none(g.get("verdict", ""))
        return {"allow": True, "spec": SUB, "advance": False, "adjust": None, "track": "z3", "note": g.get("verdict", ""),
                "items": [{"track": "z3", "spec": SUB, "advance": False, "adjust": None, "first": False}], "z3_note": ""}
    hold = first and g.get("hold")
    if hold:
        # repeat the last step of each track, never go up
        if d3.get("done"):
            s3 = min(s3, max(0, int(d3["done"]) - 1))
        if d5.get("done"):
            s5 = min(s5, max(0, int(d5["done"]) - 1))
    items = _pick_tracks(avail, n, monday, gate, s3, s5, d3, first and step is None and not hold, met, d5)
    if hold:
        items = [{**it, "advance": False} for it in items]
    out = _decision(items, avail, gate, n, monday, lock, zt)
    out["warn"] = g.get("warn", "") if items else ""
    return out


def _pick_tracks(avail: list, n: int, monday: Optional[dt.date], gate: dict, s3: int, s5: int, d3: dict,
                 tweak: bool, met: int, d5: Optional[dict] = None) -> list[dict]:
    """The week's items: n ≥ 2 → every open track (Zone 3 first); one a week with both open →
    the track gate["ratio"] gives this week (Zone 3 the first `z3` weeks of each cycle); else the
    open one. `tweak`: this week's state-machine tweak (d3 / d5 "adjust") applies."""
    if not avail:
        return []
    if n >= 2 or len(avail) == 1:
        tracks = list(avail)
    else:
        r = gate.get("ratio") or {"z3": 2, "z5": 1}
        a, b = max(1, int(r.get("z3") or 1)), max(0, int(r.get("z5") or 0))
        mon = monday or (dt.date.fromisoformat(gate["monday"]) if gate.get("monday") else None)
        tracks = ["z5"] if b and _week_index(mon) % (a + b) >= a else ["z3"]
    out = []
    for t in tracks:
        d = d3 if t == "z3" else (d5 or {})
        s = s3 if t == "z3" else s5
        spec = track_spec(t, s)
        adj = None
        if tweak and s == int(d.get("step") or 0):
            adj = d.get("adjust") or None
            spec = adjusted_spec(spec, adj)             # the state machine's tweak, this week only
        out.append({"track": t, "spec": spec, "advance": True, "adjust": adj,
                    "first": t == "z3" and s == 0 and met == 0})
    if n >= 2 and tracks == ["z3"]:
        # 2 a week with only Zone 3 open (owner 2026-10-04): the second session is a different Zone 3
        # session — the 巡航版 of the rung's position (overview.quality_sessions sizes it to the first
        # one's time in zone, within the Zone 3 ≤ 10 % and the week's ≤ 20 % caps), not a copy; it
        # doesn't move the rung (its 達標 counts in `met`)
        rung = out[0]["spec"][0]
        out.append({"track": "z3", "spec": next(r for r in CRUISE if r[0] == cruise_for(rung, None))
                    if rung in _IL.Z3_TRACK else CRUISE[0], "advance": False, "adjust": None, "first": False,
                    "cruise": True})
    return out


def _decision(items: list, avail: list, gate: dict, n: int, monday: Optional[dt.date], lock: str,
              zt: Optional[dict] = None) -> dict:
    tracks = [it["track"] for it in items]
    z5 = gate.get("z5") or {}
    if "z3" in tracks:
        z3_note = ""
    elif lock:
        z3_note = lock
    elif "z3" in avail:
        r = gate.get("ratio") or {"z3": 2, "z5": 1, "why": ""}
        z3_note = (f"本週輪到 5 區（每週 1 堂時 3 區：5 區 = {r.get('z3')}:{r.get('z5')}，{r.get('why') or ''}；推估），"
                   "3 區下週排")
    else:
        z3_note = ""
    note = ""
    zt = zt or z5_track(gate)
    if tracks and set(tracks) == {"z3"} and not zt["open"]:
        # SP-39: the soft 「3 區先」 is met but the AeT isn't (or the other way round): say what Zone 5 waits for
        if zt["z3_ok"] and not zt["aet_ok"]:
            note = z5.get("text") or "Zone 5 還沒開：先排 3 區"
        elif zt["aet_ok"] and not zt["z3_ok"]:
            note = zt["text"]
    first = items[0] if items else {}
    return {"allow": bool(items), "items": items, "spec": first.get("spec"), "advance": bool(first.get("advance")),
            "adjust": first.get("adjust"), "track": first.get("track"), "note": note, "z3_note": z3_note}


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


def is_z5_variant(key: Optional[str]) -> bool:
    """A planned Zone 5 session: only a run classified Z5 (workout_review stimulus "z5")
    ticks it (owner 2026-10-02) — a threshold climb no longer does."""
    from backend.engine import interval_library as IL
    v = IL.get(key)
    return v is not None and v.cls == "Z5"


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
        why_parts.append(f"AeT 目前不算有效：{(ae.get('validity') or {}).get('reason') or '推估的 AeT 還不夠準'}"
                         + _("（推估的 AeT 要夠準、最近幾次沒有往同一邊偏才算；推估），改用不設門檻"))
    d = gate.get("dose") or {}
    d3, d5 = _track_doses(gate)
    why_parts.append(f"8 週內 {d.get('done', 0)} 次間歇（3 區達標 {d3.get('met') or 0} 次、5 區 {d5.get('done') or 0} 次）")
    z3g = gate.get("z3") or {}
    if z3g.get("text"):
        why_parts.append(z3g["text"])
    z5 = gate.get("z5") or {}
    if z5.get("text"):
        zt = gate.get("z5_gate") if isinstance(gate.get("z5_gate"), dict) else z5_track(gate)
        why_parts.append(zt["text"] if zt["aet_ok"] else z5["text"])
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
        parts = []
        for it in dec["items"]:
            if it["spec"] in (SUB, ZONE3, RECOVERY) or it["track"] is None:
                parts.append(it["spec"][1])
            elif it.get("cruise"):
                parts.append(f"3 區第二堂（巡航版）：{it['spec'][1]}")
            else:
                dt_ = d3 if it["track"] == "z3" else d5
                parts.append(f"{TRACK_LABEL[it['track']].split('（')[0]}第 {int(dt_.get('step') or 0) + 1} 步：{it['spec'][1]}")
        step_txt = f"（{'；'.join(parts)}）"
    if dec.get("z3_note") and "z3" not in [it["track"] for it in dec["items"]]:
        step_txt += f"（{dec['z3_note']}）"
    g = gate.get("guard") or {}
    state = gate.get("state")
    src = {"ua_gap": SRC_UA, "friel_drift": SRC_FRIEL, "xu_drift": SRC_XU}.get(gate.get("via") or gate["mode"], SRC_SEILER)
    if state == "locked":
        # the method keeps Zone 5 closed; Zone 3 still goes on when the guardrails pass (台灣教練)
        v = gate["verdict"] + (f"；本週{step_txt}" if dec["allow"] and dec["spec"] is not None else
                               f"；{dec['z3_note']}" if dec.get("z3_note") else "")
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
    if not dec["allow"] and z3g and not z3g.get("open"):
        # the Zone 3 gate (SP-31): easy running until the base is there
        return {"level": "info", "text": "3 區未開", "verdict": dec.get("z3_note") or z3g.get("reason") or "",
                "why": why, "action": f"規律跑（每週 ≥ {Z3_RUNS_PER_WEEK} 次、別連續 {Z3_MAX_GAP_DAYS} 天沒跑）；或做一次 90 分鐘平路 1 區測試（飄移 < 10% 就解鎖）",
                "source": z3g.get("src") or SRC_Z3["weeks"]}
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
    "auto": {"source": "台灣教練、徐國峰部落格、Uphill Athlete、Friel、Seiler",
             "rule": "3 區（有氧間歇／節奏，每趟 15–30 分：2×15 → 3×12 → 2×20 → 1×30，88–95% CP）解鎖後、護欄通過就排——解鎖三選一：連續 4 週規律訓練（每週 ≥ 3 次、沒有 ≥ 7 天沒跑；推估，停跑 ≥ 21 天要重新累積）、徐國峰 90 分鐘測試飄移 < 10%、或實測 AeT 的 UA 差距 ≤ 10%；低強度占比不擋 3 區（只提醒；5 區照舊要 ≥ 75%）；每週 3 區量 ≤ 週量 10%（Daniels），放不下就排巡航版 3×6／3×8／2×12。5 區開放後 3 區照排：每週 2 堂＝3 區＋5 區各 1，每週 1 堂時輪替（目標 ≤ 10 km 路跑 1:1，其他 2:1；推估）；一週間歇總量 ≤ 跑步時間 20%（推估）。5 區（每趟 ≥ 2 分、一週最多 2 次、隔 ≥ 2 天：台灣教練）是另一道關卡，一定要實測 AeT："
                     "① 實測 AeT＋實測 LTHR，LTHR ÷ AeT − 1 ≤ 10%（UA 差距法）、或 ② 在實測 AeT 附近跑 ≥ 60 分鐘，前後半飄移 < 5%（Friel）；"
                     "90 分鐘測試量不出 AeT，只算 3 區的關卡。另外近 6 週要做過 ≥ 2 堂 3 區（軟條件，推估）。"
                     "低強度占比 < 75% 在實測 AeT 時擋 5 區；AeT 是估計值時只提醒。"
                     "確認後沒有到期日，每週檢查：1 區時間連 3 週 < 確認時的 2/3 就暫停，到下次確認為止（Hickson 1982；3 週推估）；"
                     "停跑 ≥ 6 天進恢復期（Daniels 表 9.2），期間 3 區、5 區都不排，之後先 3 區；暫停時 3 區照排。"
                     "AeT 有效＝從輕鬆跑推估的 AeT 誤差 ≤ 3 bpm、最近 6 次沒有往同一邊偏（推估），有效時才用差距法。",
             "todo": "3 區：規律跑 4 週，或週末的 LSD 改成 90 分鐘平路 1 區；5 區：做一次 AeT 測試（UA 40–60 分）和 30 分鐘 LTHR 測試。"},
    "ua_gap": {"source": "Uphill Athlete：When to add intensity",
               "rule": "AnT ÷ AeT − 1 ≤ 10%（Uphill Athlete）：用 LTHR 當 AnT、實測 AeT。差距越小代表有氧基礎越好。"
                       "解鎖後先排 Zone 3（AeT–LTHR），每週 1 次，約週有氧時數的 5%。",
               "todo": "需要一次 AeT 測試（和 LTHR 測試）；之後只在推估的 AeT 不夠準（誤差 > 3 bpm）、有偏移或約 6 週沒有可判讀的跑步時再測（推估）。"},
    "friel_drift": {"source": "Friel（TrainingPeaks：Aerobic decoupling）",
                    "rule": "8 週內有一次在 AeT 附近（平均心率 AeT−5～AeT+3，範圍自訂）、暖身後 ≥ 60 分鐘的平路穩定跑，"
                            "前後半心率飄移 < 5%。一次就夠。",
                    "todo": "需要實測 AeT，並排一次 60–90 分鐘平路跑，心率壓在 AeT 附近、不停、不加速。"},
    "xu_drift": {"source": "徐國峰《跑者都該懂的跑步數據》",
                 "rule": "平地、E 配速 90 分鐘（< 25 °C：台灣教練）：(第 90 分心率 − 第 10 分心率) ÷ 第 10 分心率 < 10% 就可以練 3 區（< 5% 是國家級）。"
                         "5 區還是要實測 AeT（UA 差距或 Friel 飄移）。",
                 "todo": "排一次 90 分鐘平路 E 配速跑，選 < 25 °C 的日子，補給停不超過 30 秒。"},
    "plateau": {"source": "徐國峰（錶上 VO2max 不再提升）；Cusick（指標先到平台期）",
                "rule": "基礎期 ≥ 8 週，而且有氧效率 EF 近 6 週和之前比 < +2%（持平）。用 EF 代替錶上 VO2max、8 週和 2% 都是推估。"
                        "不叫「MAF 停滯」：Maffetone 把停滯當警訊。解鎖的是 3 區；5 區還是要實測 AeT。",
                "todo": "繼續輕鬆路跑（心率 ≤ AeT、≥ 30 分鐘），EF 才算得出來。"},
    "weeks": {"source": "Palladino 基礎期分段；Cusick 第一階段 4–8 週",
              "rule": "基礎期開始後滿 N 週（預設 8，範圍 2–16）才排間歇。8 週取中間值，屬推估。解鎖的是 3 區；5 區還是要實測 AeT。",
              "todo": "不用測試；只要基礎期有起點（賽事周期）。"},
    "none": {"source": "Seiler 2010、Seiler & Tønnessen 2009、Koop／CTS",
             "rule": "不設門檻：整個週期都有少量高強度，3 區、5 區都不用先解鎖。基礎期每週最多 1 次，由護欄決定：低強度 ≥ 75%、CTL 每週 < +5（≥ 5 只排閾值下）、"
                     "週增量 ≤ 20%（10–20% 維持）、3:1 恢復週改 4×1 分 fartlek、TSB、離長跑 ≥ 2 天。"
                     "兩條階梯各自進階：3 區 2×15 → 3×12 → 2×20 → 1×30（88–95% CP；量 ≤ 週量 10%，放不下排巡航版 3×6／3×8／2×12），"
                     "5 區 5×2 → 4×3 → 5×3 → 4×4（近 6 週做過 ≥ 2 堂 3 區後；推估）；"
                     "時間足夠排標準版，平日上限放不下時換同等較短版。",
             "todo": "不用測試。"},
}


def option_texts() -> dict:
    """{mode: {"label", "tip"}} — the static part of the hover (availability is added by the page)."""
    return {m: {"label": LABEL[m], "tip": f"{LABEL[m]}\n來源：{v['source']}\n\n怎麼算：{v['rule']}\n\n要做的事：{v['todo']}"}
            for m, v in OPTION_INFO.items()}
