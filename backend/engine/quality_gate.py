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

The dose (§4.5) steps through DOSE, one step per interval session done in the
last 8 weeks: 5×1′ → 6×1′ → 4×3′ uphill → 5×3′ → 4×4′, then sub-threshold 3×8′ /
4×8′ alternating. The recovery-week fartlek is not a step. A held week repeats
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

MODES = ("auto", "ua_gap", "friel_drift", "xu_drift", "xu_signals", "plateau", "weeks", "none")
WEEKS_RANGE = (2, 16)
LABEL = {"auto": "自動", "ua_gap": "Uphill Athlete 差距法", "friel_drift": "Friel 飄移法",
         "xu_drift": "徐國峰 90 分鐘法", "xu_signals": "三訊號", "plateau": "有氧停滯法", "weeks": "週數法",
         "none": "不設門檻（Seiler）"}

SRC_UA = "Uphill Athlete：When to add intensity（AnT/AeT − 1 ≤ 10%，先加 Zone 3）"
SRC_FRIEL = "Friel（TrainingPeaks：Aerobic decoupling < 5%，跑步在 AeT 1–2 小時）"
SRC_XU = "徐國峰（你的筆記：跑者都該懂的跑步數據）"
SRC_SEILER = "Seiler 2010；Seiler & Tønnessen 2009（整個週期都有少量高強度，每週 1–3 次）"
SRC_PALLADINO = "Palladino 基礎期（你的筆記：palladino基礎期訓練）"
SRC_KOOP = "Koop／CTS（6×3 分 RI、上坡）"
SRC_HELGERUD = "Helgerud 2007（4×4 分）"
SRC_SEILER13 = "Seiler 2013（4×8 分）"
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
XU_HEAT_C = 25.0
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
# key, title, reps, work min, rest min, %CP lo, hi, uphill, source
Z3 = (
    ("z3a", "閾值 3×8 分", 3, 8, 2, 0.88, 0.95, False,
     "徐國峰：先練 3 區（私訊）；88–95% CP = Palladino 3A"),
    ("z3b", "閾值 4×8 分", 4, 8, 2, 0.88, 0.95, False, "徐國峰：先練 3 區；Seiler 2013：4×8 分對休閒選手效果最好"),
    ("z3c", "閾值 3×10 分", 3, 10, 3, 0.95, 1.01, False, "徐國峰：先練 3 區；Palladino 3B（95–101% CP、3×10 分）"),
)
Z5 = (
    ("z5a", "VO2max 5×2 分", 5, 2, 2, 1.06, 1.12, False,
     "徐國峰：5 區每趟最短 2 分鐘（私訊）；106–112% CP = Palladino Z5 的下段（推估）"),
    ("z5b", "VO2max 4×3 分", 4, 3, 3, 1.05, 1.10, False, "Koop 3 分 RI；105–110% CP 取自 Palladino 後期"),
    ("z5c", "VO2max 5×3 分", 5, 3, 3, 1.05, 1.10, False, "Koop 12–24 分總量"),
    ("z5d", "VO2max 4×4 分", 4, 4, 3, 1.03, 1.07, False, "Helgerud 2007 4×4（約 105% CP 的換算推估）"),
)
LADDER = Z3 + Z5
Z3_MET_FOR_Z5 = len(Z3)        # 推估: 3 sessions 達標 at Zone 3 (the Z3 rungs) = 「3 區跑順了」
# legacy titles of the old ladder: not counted as steps any more (neutral in planned_spec)
LEGACY_TITLES = ("短間歇 5×1 分", "短間歇 6×1 分", "爬坡間歇 4×3 分", "間歇 5×3 分", "VO2max 間歇 4×4 分",
                 "閾值下 3×8 分", "閾值下 4×8 分")
DOSE = Z3                      # kept for callers that read the first rungs (adapt._downgrade)
RECOVERY = ("r1", "恢復週 fartlek 4×1 分", 4, 1, 2, 0.98, 1.01, False, "Palladino 恢復週保留 98–101% CP fartlek")
SUB = Z3[0]
ZONE3 = ("z3", "Zone 3 間歇", 3, 6, 2, None, None, False, "Uphill Athlete：先加 Zone 3（AeT–LTHR），約週有氧量的 5%")


def dose_spec(step: int, z5_open: bool = True) -> tuple:
    """The ladder rung for `step` (達標 count): Z3 rungs first; from step 3
    Z5 rungs only while Zone 5 is open — else the top Z3 rungs alternating
    (Zone 3 continues; 徐國峰). After the Z5 rungs: 4×4 and 3×10 alternating."""
    step = max(0, int(step))
    if step < len(Z3):
        return Z3[step]
    if not z5_open:
        return Z3[1 + step % 2]
    k = step - len(Z3)
    if k < len(Z5):
        return Z5[k]
    return (Z5[-1], Z3[-1])[(k - len(Z5)) % 2]


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


# ---------------------------------------------------------------------------
# drift methods
# ---------------------------------------------------------------------------

def _runs(ds, today: dt.date, days: int = LOOKBACK_DAYS):
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    return [w for w in ds.workouts if tday - days < math.floor(w.day) <= tday and w.sport == "run"]


def friel_check(ds, today: dt.date, aet: Optional[float]) -> dict:
    """Friel: one steady run near AeT, ≥ 60 min after the warm-up, fair drift < 5 %.
    missing = no such run in 8 weeks; locked = runs there, all ≥ 5 %."""
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
        cands.append({"idx": w.idx, "date": WR._wdate(w).isoformat(), "drift": dr["drift"], "hr": hr})
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
    fast-finish checks must pass). Over 25 °C it doesn't count: drift_of's heat
    rule (workout_review.heat_gate — the route_weather archive's air
    temperature, else the watch's; XU_HEAT_C == WR.DRIFT_HEAT_C)."""
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
            last = {"idx": w.idx, "date": WR._wdate(w).isoformat(), **r}
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
        out.append({"power": r["power"], "duration_s": r["duration_s"], "hr_at60": v})
    return out


def dose_history(ds, today: dt.date, days: int = LOOKBACK_DAYS) -> list[dict]:
    """Interval sessions in the `days` before `today`, oldest first:
    workout_review's `quality` class, or a road run with ≥ 4 short reps
    (count_reps). {"idx", "date", "reps", "faded"}."""
    from backend.engine import workout_review as WR
    from backend.engine.overview import category
    from backend.engine.wko5expr.dataset import date_to_day
    tday = math.floor(date_to_day(today))
    out = []
    try:
        from backend.engine.plan_store import done_titles
        titles = done_titles()                 # activity index -> the planned session's title
    except Exception:                          # noqa: BLE001
        titles = {}
    for w in sorted(ds.workouts, key=lambda x: x.day):
        if not (tday - days <= math.floor(w.day) < tday) or category(w) not in WR.QUALITY_CATEGORIES:
            continue
        if (_f(w.metrics.get("duration")) or 0) < 1200:
            continue
        m = WR.measure(ds, w)
        if not m or (m.get("hard_s") or 0) < 120:
            continue
        c = WR.classify(ds, w, m)
        reps, fade = [], None
        if category(w) == "road" and m.get("cp"):
            s = WR._samples(ds, w)
            reps = count_reps(s["t"], s["power"], m["cp"]) if s is not None else []
        if c["type"] != "quality" and len(reps) < DOSE_MIN_REPS:
            continue
        if len(reps) >= 2 and reps[0]["power"]:
            fade = reps[-1]["power"] / reps[0]["power"] - 1.0
        else:
            fade = (m.get("intervals") or {}).get("fade")
        if reps:
            bouts = _with_hr_at60(reps, s)
        else:
            bouts = [{"power": e.get("power"),
                      "hr_at60": (e["hr_max"] - e["hr_drop60"]) if e.get("hr_max") is not None
                      and e.get("hr_drop60") is not None else None} for e in (m.get("efforts") or [])]
        out.append({"idx": w.idx, "date": WR._wdate(w).isoformat(), "title": titles.get(w.idx),
                    "reps": len(reps) or (m.get("intervals") or {}).get("n") or 0,
                    # informational only now: dose_step judges the bouts (interval_outcome)
                    "faded": fade is not None and fade < -FADE,
                    "bouts": bouts[:20], "cp": m.get("cp")})
    WR._flush(ds)
    return out


# ---------------------------------------------------------------------------
# guardrails (§4.4)
# ---------------------------------------------------------------------------

def guard(low_share: Optional[float] = None, power_low_share: Optional[float] = None,
          ramp: Optional[float] = None, step: Optional[float] = None, tsb: Optional[float] = None,
          aet: Optional[float] = None) -> dict:
    """This week's check: {"block", "sub", "hold", "verdict", "action"} — the
    first failing rule speaks. Missing numbers don't block."""
    out = {"block": False, "sub": False, "hold": False, "verdict": "", "action": "", "rule": ""}
    aet_t = f"{aet:.0f} bpm" if aet else "AeT"

    def say(rule, verdict, action, **flags):
        if not out["rule"]:
            out.update(rule=rule, verdict=verdict, action=action)
        out.update(flags)
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
IN_BAND_TOL = 0.98      # 自組 (doc §4.2): a rep is in band at ≥ 98 % of the planned lower bound
TARGET_DOWN = 0.95      # ROLE:499「下修 5～10%」: first rep already short -> target −5 %
AET60_MIN_SHARE = 0.5   # 自組 (doc §4.3): HR back under AeT 60 s into the rest on < half the reps = brake
LAST_FADE = 0.05        # 自組 (doc §4.3): only the last rep missed and it fell > 5 % = 邊界
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
        spec, neutral = planned_spec(h.get("title"), step)
        if neutral:
            # a recovery fartlek / sub-threshold (ramp week) / Zone 3 session the plan
            # prescribed outside the ladder: not a step, never judged against it
            h["outcome"] = "neutral"
            continue
        if h.get("bouts") is not None and h.get("cp"):
            o = interval_outcome(h["bouts"], spec, h["cp"], aet)
        else:
            o = {"outcome": "unknown", "why": "沒有功率或 CP，無法判定達標：同一階再做一次"}
        oc = o.get("outcome") or "unknown"
        h["outcome"] = oc
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
    out = {"done": len(history), "faded": bool(last and last["outcome"] != "met"), "step": step}
    if last is not None:
        out.update(outcome=last["outcome"], adjust=adjust,
                   note="" if last["outcome"] == "met" else f"上次間歇{OUTCOME_LABEL[last['outcome']]}（{last.get('why') or ''}）：")
    return out


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
        if t in LEGACY_TITLES:
            return want, True
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

    def signals():
        if "signals" not in cache:
            from backend.engine import base_check as BC
            cache["signals"] = BC.three_signals(ds, today)
        return cache["signals"]

    def method(m: str) -> dict:
        """{"state": unlocked | locked | missing | none, "verdict", "action", "prefix"}."""
        if m == "none":
            return {"state": "none"}
        if m == "ua_gap":
            if not ae["measured"]:
                return {"state": "missing", "verdict": "沒有實測 AeT，差距法算不出來"}
            if not lthr_ok:
                return {"state": "missing", "verdict": "LTHR 還是 WKO5 預設值，差距法算不出來"}
            g = gap
            txt = f"AeT {ae['value']:.0f} / LTHR {lt['value']:.0f}：差距 {g * 100:.0f}%"
            if g <= UA_GAP_MAX:
                return {"state": "unlocked", "verdict": f"{txt} ≤ 10%：可以加 Zone 3",
                        "prefix": f"AeT–LTHR 差距 {g * 100:.0f}% ≤ 10%：", "gap": g}
            return {"state": "locked", "verdict": f"{txt}（> 10%，有氧不足）",
                    "action": "繼續基礎：輕鬆跑壓在 AeT 以下＋坡衝刺（3 區照排）；聚合估計不準或偏移時再測 AeT", "gap": g}
        if m == "friel_drift":
            r = friel()
            if r["state"] == "missing":
                return {"state": "missing", "verdict": r["reason"] + "，飄移法算不出來"}
            run = r["run"]
            if r["state"] == "unlocked":
                return {"state": "unlocked", "verdict": f"{run['date']} 在 AeT 附近跑 ≥ 60 分鐘，飄移 {run['drift'] * 100:.1f}% < 5%",
                        "prefix": f"Friel 飄移 {run['drift'] * 100:.1f}% < 5%（{run['date']}）：", "run": run}
            return {"state": "locked", "verdict": f"8 週內在 AeT 附近 ≥ 60 分鐘的平路跑，飄移都 ≥ 5%（最近 {run['drift'] * 100:.1f}%）",
                    "action": "排一次 60–90 分鐘平路跑，心率壓在 AeT 附近", "run": run}
        if m == "xu_drift":
            r = xu()
            if r["state"] == "missing":
                return {"state": "missing", "verdict": r["reason"] + "，90 分鐘法算不出來"}
            run = r["run"]
            if r["state"] == "unlocked":
                return {"state": "unlocked", "verdict": f"{run['date']} 90 分鐘 E 跑飄移 {run['drift'] * 100:.0f}% < 10%",
                        "prefix": f"90 分鐘 E 跑飄移 {run['drift'] * 100:.0f}% < 10%：", "run": run}
            return {"state": "locked", "verdict": f"最近一次 90 分鐘 E 跑飄移 {run['drift'] * 100:.0f}%（≥ 10%）",
                    "action": "繼續低強度長跑，下次選 < 25 °C 的日子再測", "run": run}
        if m == "xu_signals":
            from backend.engine import base_check as BC
            r = signals()
            if r["ok"]:
                return {"state": "unlocked", "verdict": f"三訊號都做到：{r['text']}",
                        "prefix": "三訊號都做到：", "signals": r}
            missing = r["s1"]["run"] is None
            return {"state": "missing" if missing else "locked", "verdict": f"三訊號還沒全部做到：{r['text']}",
                    "action": "週末排一次 90 分鐘平路 1 區長跑（氣溫 25 °C 以下時開始）；每週 1 區約 210 分鐘",
                    "signals": r, "src": BC.SRC_XU_SIGNALS}
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
              tsb=_value(by, "form"), aet=ae["value"] if ae["measured"] else None)
    hist = []
    try:
        hist = dose_history(ds, today)
    except Exception:
        hist = []
    dose = dose_step(hist, ae.get("value"))
    # ---- Zone 5 (engine/base_check.py) and the AeT test's reason -----------
    z5 = _z5(ds, today, mode, state, ae, lthr_ok, method, friel)
    test_reason = aet_test_reason(ds, today, ae, z5)
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
        "z5": z5, "aet_test_reason": test_reason,
    }
    out["options"] = options(out, ae, lt, cache, friel, xu, base_weeks, ef, need_weeks)
    return out


def _z5(ds, today: dt.date, mode: str, state: str, ae: dict, lthr_ok: bool, method, friel) -> dict:
    """base_check.z5_status with the AeT paths (a measured AeT passing the UA
    gap → its row date; a Friel run → its date). Never raises."""
    from backend.engine import base_check as BC
    try:
        paths = {}
        if ae.get("measured") and lthr_ok and mode in ("auto", "ua_gap"):
            if method("ua_gap").get("state") == "unlocked":
                paths["aet_ua_gap"] = ae.get("date")
        if ae.get("measured") and mode in ("auto", "friel_drift"):
            f = friel()
            if f.get("state") == "unlocked":
                paths["aet_friel_drift"] = f["run"]["date"]
        return BC.z5_status(ds, today, mode, state, paths)
    except Exception as e:                  # noqa: BLE001 — Z5 stays closed, the plan still builds
        return {"state": "unconfirmed", "label": BC.STATE_LABEL["unconfirmed"], "open": False, "since": None,
                "path": None, "path_label": "", "reason": f"算不出來（{type(e).__name__}）",
                "text": f"Zone 5：未確認（算不出來：{type(e).__name__}）"}


def aet_test_reason(ds, today: dt.date, ae: dict, z5: dict) -> Optional[dict]:
    """Why an AeT test should be scheduled now, or None (unsourced-rules.md §B3
    and the Z5 lifecycle; numbers 推估 unless noted): {"code", "text"}.
      no_data  no interpretable run (a drift_of value or a 90-min 徐國峰 run) for
               ~6 weeks (UA's 4–6-week retest, coach; wording 未驗證)
      se       the aggregated AeT estimate is missing or its SE > 3 bpm
      shift    the last 6 points shift one way > 5 bpm
      moved    the estimate is more than max(SE, 3 bpm) away from the plan's AeT
               (UA: AeT rises toward AnT as the base improves — confirm it)
    A passive re-confirmation (Zone 5 confirmed by a 徐國峰 run / 三訊號 in the
    last 6 weeks) stands in for a test on no_data / se: no test then."""
    from backend.engine import base_check as BC
    from backend.engine import drift_agg as DA
    val = ae.get("validity") or {}
    try:
        recent = [p for p in DA.aet_points(ds, today, BC.NO_DATA_DAYS)]
        xu_recent = [r for r in BC.xu_runs(ds, today, BC.NO_DATA_DAYS)]
    except Exception:                       # noqa: BLE001
        recent, xu_recent = [], []
    passive = z5.get("state") == "confirmed" and z5.get("path") in ("xu90", "xu_signals") and z5.get("since") and \
        (today - dt.date.fromisoformat(z5["since"])).days <= BC.NO_DATA_DAYS
    v, se = val.get("value"), val.get("se")
    if v is not None and se is not None and se <= 3.0 and val.get("shift_bpm") is not None and \
            abs(val["shift_bpm"]) > 5.0:
        return {"code": "shift", "text": val.get("reason") or "最近 6 次的飄移有系統性偏移"}
    if ae.get("measured") and v is not None and se is not None and se <= 3.0 and \
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
    try:
        s = cache.get("signals")
        out["xu_signals"] = {"usable": True, "why": s["text"] if s else "三訊號：90 分鐘 1 區飄移 < 10%、"
                             "每週 1 區約 210 分（RQ 30–42 點）、長跑後段不飄"}
    except Exception as e:  # noqa: BLE001
        out["xu_signals"] = {"usable": False, "why": f"算不出來（{type(e).__name__}）"}
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
    if kind != "base":
        ok = levels.get("intensity") != "bad" and levels.get("drift") != "bad"
        return {"allow": ok, "spec": None, "advance": False, "note": ""}
    # two gates (徐國峰, 私訊 2026-10-01): Zone 3 whenever the guardrails pass; Zone 5 only
    # while the aerobic base is confirmed (gate["z5"], engine/base_check.z5_status). A locked
    # method no longer stops Zone 3 — it only keeps Zone 5 closed.
    z5 = gate.get("z5") or {}
    z5_open = bool(z5.get("open"))
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
    if first and step is None and s == d.get("step", 0):
        spec = adjusted_spec(spec, d.get("adjust"))       # the state machine's tweak, this week only
    note = "" if z5_open or s < len(Z3) else (z5.get("text") or "Zone 5 還沒開：先排 3 區")
    return {"allow": True, "spec": spec, "advance": adv, "note": note}


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
        if key in ("z3a", "z3b") and aet and use_hr:
            parts.append(f"心率 {aet:.0f}–{lthr:.0f} bpm")
        elif key in _HR_FRAC and use_hr:
            a, b = _HR_FRAC[key]
            parts.append(f"心率 {a * lthr:.0f}–{b * lthr:.0f} bpm")
    if uphill:
        body = f"上坡 {work} 分鐘（6–10% 坡），慢跑或走下來恢復；{what}；休 {rest} 分鐘"
    else:
        body = f"{what}；休 {rest} 分鐘（慢跑）"
    minutes = 15 + reps * (work + rest) + 10
    rate = {"z3": 60.0, "z3a": 65.0, "z3b": 65.0, "z3c": 68.0, "r1": 55.0}.get(key, 72.0)
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


def hard_need(title: str, default: float) -> float:
    """Seconds at/above threshold that mark a planned interval session done:
    short reps (5×1′) never reach 10 min, so 60 % of the planned work."""
    import re
    m = re.search(r"(\d+)\s*[×xX]\s*(\d+)\s*分", title or "")
    if not m:
        return default
    return min(default, 0.6 * int(m.group(1)) * int(m.group(2)) * 60)


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
             "rule": "3 區（閾值）只要護欄通過就排；5 區（每趟 ≥ 2 分、一週最多 2 次、隔 ≥ 2 天：徐國峰）要先確認有氧基礎，"
                     "任一條：三訊號、徐國峰 90 分鐘飄移 < 10%、或實測 AeT 通過 UA 差距法／Friel 飄移。確認後沒有到期日，"
                     "每週檢查：1 區時間沒有連 2 週 < 確認時的 70%、長跑後段沒變差、沒有連續 14 天沒跑（都推估）；不符就暫停 5 區、3 區照排。"
                     "AeT 有效＝聚合估計標準誤 ≤ 3 bpm、最近 6 次沒偏移（推估），有效時才用差距法。",
             "todo": "週末的 LSD 改成 90 分鐘平路 1 區、配速不變，跑完就自動確認；不用另外測。"},
    "xu_signals": {"source": "台灣教練",
                   "rule": "三個都要做到：① 連續 90 分鐘心率 1 區，(第 90 分心率 − 第 10 分) ÷ 第 10 分 < 10%；"
                           "② 一週約 210 分鐘 1 區（RQ 訓練指數 30–42 點＝丹尼爾強度點數 E 每分 0.2；約 120–170 TSS，換算推估），"
                           "隔週跑量 ≥ 70%（推估）；③ 最近的長跑後段心率沒上飄、配速沒掉（各 ±5%，推估）。",
                   "todo": "週末跑一次 90 分鐘平路 1 區；每週 1 區約 210 分鐘。"},
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
                     "劑量 3 區 3×8 → 4×8 → 3×10，5 區 5×2 → 4×3 → 5×3 → 4×4（徐國峰：3 區先）。",
             "todo": "不用測試。"},
}


def option_texts() -> dict:
    """{mode: {"label", "tip"}} — the static part of the hover (availability is added by the page)."""
    return {m: {"label": LABEL[m], "tip": f"{LABEL[m]}\n來源：{v['source']}\n\n怎麼算：{v['rule']}\n\n要做的事：{v['todo']}"}
            for m, v in OPTION_INFO.items()}
