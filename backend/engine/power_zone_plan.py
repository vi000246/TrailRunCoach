"""
課表功率區間 (SP-363): which power zone set the 課表 writes its power targets in — like 設定 → 課表心率區間
(engine/hr_profile.py) for the HR ones.

    palladino  Palladino 10 zones, % CP (the default; zones.PALLADINO_POWER_ZONES)
    stryd      Stryd 5 zones, % CP (zones.STRYD_POWER_ZONES)
    classic    the classic 7 levels, % threshold power (Allen & Coggan, Training and Racing with a
               Power Meter: < 55 active recovery, 56–75 endurance, 76–90 tempo, 91–105 threshold,
               106–120 VO2max, 121–150 anaerobic capacity, > 150 neuromuscular; here the threshold
               power is the 課表's CP)
    personal   個人化區間: 9 levels from the runner's own power–duration model (Stryd PD fit in effect:
               the model's threshold power, anaerobic capacity and its time constant) — the algorithm
               is in the calculation core (algorithms/wko5_levels); without the core (the public
               mirror) or without a fit, the 課表 falls back to Palladino and says so

How a session follows the chosen set (the user 2026-10-15, SP-363 Q2-4 a): each kind keeps its
intent and takes the nearest level of the set —
    easy kinds (輕鬆跑、長跑、山路長天) → the set's easy level: the one holding 72.5 % CP (the middle
                                         of the easy run's 65–80 %)
    恢復跑 (a cap only)              → up to the top of the level holding its cap (75 % CP)
    every other band (間歇、爬坡…)   → the level overlapping the band the most (ties: the lower)
Palladino itself maps nothing: every band stays exactly as it was. The watch band (zones.watch_band:
the upper bound × 1.05, Zone 5 open) is applied after the mapping, as before.

What follows the setting: the 課表's power texts (zones.training_targets → overview._targets,
projection.target_texts, interval_library.session_for), the structure's auto power steps
(workout_steps.resolve → the push to the watch, the TSS) and the charts' default power zones.
Sessions already on the watch / edited by the user follow plan_store / plan_auto's rules (a re-plan
starts after a switch, as for the 課表心率區間). Under 個人化區間 the intervals at / above threshold go
further — 個人化間歇 (SP-590, optimized() below): the model's rep duration and power, the coaches'
reps and rests. SP-618: the interval judging (interval_eval, quality_gate's state machine:
judge_spec), the structure editor's zone choice (workout_steps.zones_table) and the activity
classifier's edges (session_stimulus via class_edges; new activities only — the per-run measurements
are cached) follow it too.
"""
from __future__ import annotations

import json
import math
from typing import Optional

from backend.i18n import N_, _

SETTING_KEY = "plan.power_zone_system"
SYSTEMS = ("palladino", "stryd", "classic", "personal")
DEFAULT = "palladino"
EASY_ANCHOR = 0.725              # × CP: the middle of the easy run's band (zones.EASY_BAND 65–80 %)
OPEN_TOP = 1.8                   # × CP: an open top level's upper bound, for a band that maps to it

TITLE = {"palladino": N_("Palladino 10 區（% CP）"), "stryd": N_("Stryd 5 區（% CP）"),
         "classic": N_("經典 7 級（% 閾值功率）"), "personal": N_("個人化區間（依你的功率–時間模型）")}

# the classic 7 levels (Allen & Coggan): (id, name, lo, hi) × the threshold power, hi exclusive
CLASSIC_LEVELS = [
    ("1", N_("恢復"), 0.0, 0.56),
    ("2", N_("耐力"), 0.56, 0.76),
    ("3", N_("節奏"), 0.76, 0.91),
    ("4", N_("閾值"), 0.91, 1.06),
    ("5", N_("VO2max"), 1.06, 1.21),
    ("6", N_("無氧容量"), 1.21, 1.51),
    ("7", N_("神經肌肉"), 1.51, None),
]
CLASSIC_SOURCE = ("Allen & Coggan, Training and Racing with a Power Meter (classic power levels, "
                  "% threshold power)")

# 個人化區間's level names — the ONE place they are written (the user settled them 2026-10-15, SP-363
# Q2-3: 恢復／耐力／節奏／甜蜜點／閾值／閾值以上（VO2max）／無氧容量／無氧衝刺／最大功率; English in
# backend/i18n/locales/en.json)
PERSONAL_NAMES = (N_("恢復"), N_("耐力"), N_("節奏"), N_("甜蜜點"), N_("閾值"), N_("閾值以上（VO2max）"),
                  N_("無氧容量"), N_("無氧衝刺"), N_("最大功率"))
PERSONAL_SOURCE = N_("依你的功率–時間模型（Stryd 功率 PD 模型：模型閾值功率、無氧容量、時間常數）算出的 9 級")
NO_FIT = N_("沒有功率–時間模型（近 90 天的 Stryd 跑步不夠），課表先用 Palladino 區間")
NO_CORE = N_("這個版本沒有個人化區間的計算，課表先用 Palladino 區間")

# SP-363 Q3-1 (the user 2026-10-15: (a) + (c)): the 個人化區間 stay on the model's threshold power (as the
# algorithm defines them), and a gap between it and the 課表's CP above GAP_WARN is said out loud — the
# targets then sit that much below / above what a CP-based set gives. 10 %: about two zone widths of
# the plan's own sets (Palladino 3B is 6 % CP wide, Stryd's Zone 3 10 %), well past a test's
# day-to-day spread (3–5 %) and the fit's own 3 W switch; under it the two thresholds agree for
# training purposes (推估).
GAP_WARN = 0.10
GAP_LOW = N_("模型閾值功率 {m} W 比課表的 CP {cp} W 低 {pct}%（超過 {lim}%）：個人化區間照模型閾值功率算，"
             "功率目標會比照 CP 算的區間低。可以重做 CP 測試或多跑幾次全力讓模型更新，或改選 Palladino／Stryd 區間。")
GAP_HIGH = N_("模型閾值功率 {m} W 比課表的 CP {cp} W 高 {pct}%（超過 {lim}%）：個人化區間照模型閾值功率算，"
              "功率目標會比照 CP 算的區間高。可以重做 CP 測試，或改選 Palladino／Stryd 區間。")


def setting(user_id: int = 1) -> str:
    """The 課表功率區間 setting; no stored row = Palladino."""
    from backend.engine.wko5expr.datasource import read_setting
    v = read_setting(SETTING_KEY, DEFAULT, user_id)
    return v if v in SYSTEMS else DEFAULT


def stamp(user_id: int = 1) -> str:
    """For cache keys (the plan inputs, the charts): a switch must not serve the old targets."""
    return json.dumps(["pz", setting(user_id)])


def choices() -> list[dict]:
    return [{"id": k, "title": _(TITLE[k])} for k in SYSTEMS]


def static_levels(system: str) -> Optional[list[list]]:
    """[[id, name msgid, lo, hi]] × CP of a set that needs no model (None for personal)."""
    from backend.engine import zones as Z
    if system == "palladino":
        return [[z, n, lo, hi] for z, n, lo, hi in Z.PALLADINO_POWER_ZONES]
    if system == "stryd":
        return [[z, n, lo, hi] for z, n, lo, hi in Z.STRYD_POWER_ZONES]
    if system == "classic":
        return [[z, n, lo, hi] for z, n, lo, hi in CLASSIC_LEVELS]
    return None


def personal_levels(ds, day, cp: Optional[float]) -> dict:
    """{"levels": [[id, name, lo, hi]] × `cp`, "basis": {date, mftp, pmax, tte}} of the 個人化區間 on
    `day`, or {"reason": text} (no core / no fit / no CP)."""
    from backend.engine.core_stub import core_included
    if not core_included():
        return {"reason": NO_CORE}
    if not cp:
        return {"reason": NO_FIT}
    f = getattr(ds, "stryd_pd_params", None)
    try:
        pd = f(day) if f is not None else None
    except Exception:                       # noqa: BLE001 — a dataset without the fit (WKO5 folder, tests)
        pd = None
    if not pd or not pd.get("params"):
        return {"reason": NO_FIT}
    from backend.engine.algorithms.wko5_levels import levels
    lv = levels(pd["params"])
    if not lv:
        return {"reason": NO_FIT}
    rows = []
    for i, ((lo, hi), name) in enumerate(zip(lv, PERSONAL_NAMES)):
        rows.append([str(i + 1), name, lo / cp, None if hi != hi else hi / cp])
    d = pd.get("date")
    # the levels are the model's (its own FTP); a session's intent (% of a threshold) is read against
    # them in that frame: scale = the model's FTP ÷ the CP the rows are written in (band())
    return {"levels": rows, "scale": float(pd["params"][2]) / cp,
            # the model itself, for the 個人化間歇 (SP-590, optimized())
            "pd": {"params": [float(x) for x in pd["params"]], "tte": float(pd.get("tte") or pd["params"][4])},
            "basis": {"date": d.isoformat() if hasattr(d, "isoformat") else d,
                      "mftp": pd.get("mftp"), "pmax": pd.get("pmax"), "tte": pd.get("tte")}}


# ---- 個人化間歇 (SP-590): the intervals above threshold take the model's rep duration and power ----------
# Under 個人化區間 (only; the user 2026-10-15, SP-590 Q1-5) an interval session at or above threshold is
# re-cut to its level of the model's optimized intervals — the rep duration and power from the PD
# model, the reps and rests from the coaches' suggestions (algorithms/wko5_levels.prescription, in the
# calculation core). Which level (Q1-3): the session's band middle in Palladino's frame (the plan's
# own intent) —
#     3B threshold          95–101 % CP   → level 0
#     4  supra-threshold    101–106 %     → level 1
#     5  maximal aerobic    106–116 %     → level 2 (VO2max)
#     6  anaerobic          116–150 %     → level 3 (reps nearer its duration) or 4
#     7  sprint             ≥ 150 %       → level 5
# Below 95 % (the 有氧間歇 / 巡航 sub-threshold work) stays on the SP-363 mapping. The model gives
# one power per level; the 課表 writes it as a band of ±OPT_BAND around it.
OPT_FROM = 0.95                  # × CP: Palladino 3B's lower edge (threshold stimulus, intensive)
OPT_EDGES = ((1.01, 0), (1.06, 1), (1.16, 2), (1.50, None))      # (band middle below, level); None = 3 or 4
OPT_SPRINT = 5
OPT_BAND = 0.03                  # ± around the model's power (推估: the rep-mean spread of a steady interval)
OPT_NAMES = (N_("閾值"), N_("超閾值"), N_("最大有氧（VO2max）"), N_("無氧耐力"), N_("無氧強度"), N_("最大衝刺"))


def opt_level(lo: float, hi: float, rep_s: float, pm: Optional[dict]) -> Optional[int]:
    """The 個人化間歇 level (0–5) of a Palladino-intent band (lo, hi) × CP with reps of `rep_s` seconds;
    None below threshold."""
    m = (float(lo) + float(hi)) / 2.0
    if m < OPT_FROM - 1e-9:
        return None
    for top, lv in OPT_EDGES:
        if m < top - 1e-9:
            if lv is not None:
                return lv
            # anaerobic: the level whose rep duration is nearer (log scale) to the session's reps
            from backend.engine.algorithms.wko5_levels import target_duration
            pd = (pm or {}).get("pd") or {}
            d3, d4 = (target_duration(i, pd.get("params"), pd.get("tte") or 0.0) for i in (3, 4))
            if not (d3 == d3 and d4 == d4 and d3 > 0 and d4 > 0) or not rep_s:
                return 3
            return 3 if abs(math.log(rep_s / d3)) <= abs(math.log(rep_s / d4)) else 4
    return OPT_SPRINT


def optimized(lo: float, hi: float, rep_s: float, ref_work_s: float, pm: Optional[dict],
              cp: Optional[float]) -> Optional[dict]:
    """The 個人化間歇 a session of band (lo, hi) × CP (Palladino intent), reps of `rep_s` and `ref_work_s`
    of work in all becomes under the 課表功率區間 `pm`: {"level", "work_s", "reps", "rest_s", "lo", "hi"
    (× `cp`), "w" (the model's power), "min_reps"} — None when `pm` is not 個人化區間 in effect, without
    the model / the core / a CP, or below threshold. What is stored with the session
    (variant_adj["opt"]): the push, the structure and the judging read it back."""
    if not isinstance(pm, dict) or pm.get("system") != "personal" or not cp:
        return None
    pd = pm.get("pd") or {}
    if not pd.get("params"):
        return None
    from backend.engine.core_stub import core_included
    if not core_included():
        return None
    lv = opt_level(lo, hi, rep_s, pm)
    if lv is None:
        return None
    from backend.engine.algorithms.wko5_levels import prescription
    pr = prescription(lv, pd["params"], pd.get("tte") or pd["params"][4], ref_work_s)
    if not pr:
        return None
    w = pr["power"]
    return {"level": lv, "work_s": pr["work_s"], "reps": pr["reps"], "rest_s": pr["rest_s"],
            "lo": round(w * (1 - OPT_BAND) / cp, 4), "hi": round(w * (1 + OPT_BAND) / cp, 4), "w": round(w),
            "min_reps": pr["min_reps"]}


def opt_rule(level: int) -> str:
    """「3–8 趟、合計 12–30 分、工休比 1:1（研討會建議）」 of a level (translated)."""
    from backend.engine.algorithms.wko5_levels import PRESCRIPTION, SEMINAR_LEVELS
    r_lo, r_hi, t_lo, t_hi, ratio = PRESCRIPTION[level]
    reps = f"{r_lo}–{r_hi}"
    ratio_txt = f"1:{ratio:g}" if ratio >= 1 else f"{1 / ratio:g}:1"
    return _(N_("{reps} 趟、合計 {a:g}–{b:g} 分、工休比 {r}（{src}）"), reps=reps, a=t_lo / 60, b=t_hi / 60,
             r=ratio_txt, src=_(N_("教練研討會建議") if level in SEMINAR_LEVELS else N_("常見教練比例，推估")))


# ---- SP-618: the judging, the structure editor and the activity classifier follow the set too -----------

def model_on(ds, day, cp: Optional[float]) -> Optional[dict]:
    """plan_model for an activity's day (the judging / the classifier), memoised on the dataset;
    None in the WKO5 cross-check mode (parity: Palladino as WKO5 has no such setting)."""
    if getattr(getattr(ds, "config", None), "parity", False):
        return None
    st = setting()
    if st == DEFAULT:
        return None
    memo = getattr(ds, "memo", None)
    key = ("pz_model", str(day), round(float(cp), 1) if cp else None, st)
    if isinstance(memo, dict) and key in memo:
        return memo[key]
    try:
        pm = plan_model(ds, day, cp, st)
    except Exception:                       # noqa: BLE001 — a dataset without a plan / a fit
        pm = None
    if isinstance(memo, dict):
        memo[key] = pm
    return pm


def judge_spec(spec, pm: Optional[dict]):
    """A planned spec (an interval_library Variant or a ladder tuple (key, title, reps, work, rest, lo,
    hi, …)) with its band in the 課表功率區間 `pm` — what the judging reads its 達標 line (0.98 × lo)
    and time in zone against (SP-618). Palladino / no set / a 個人化間歇 (already the model's band) /
    a detected set / an open band: the spec itself."""
    if spec is None or pm is None or pm.get("system") in (None, DEFAULT):
        return spec
    from dataclasses import is_dataclass, replace
    if is_dataclass(spec):
        if getattr(spec, "opt", -1) >= 0 or spec.lo is None or spec.hi is None:
            return spec
        lo, hi = band(spec.lo, spec.hi, pm)
        return replace(spec, lo=lo, hi=hi)
    if not isinstance(spec, tuple) or len(spec) < 7 or spec[0] == "detected" or spec[5] is None or spec[6] is None:
        return spec
    lo, hi = band(spec[5], spec[6], pm)
    return spec[:5] + (lo, hi) + spec[7:]


# the session classifier's edges (engine/session_stimulus.py) on Palladino: Zone 3 from 3A (88 % CP), a
# VO2 bout ≥ 106 % (Zone 5) for 2 min or ≥ 103 % for 5 min
STIM_EDGES = {"z3": 0.88, "p_lo": 1.03, "p_hi": 1.06}


def class_edges(pm: Optional[dict]) -> dict:
    """{"z3", "p_lo", "p_hi"} × CP the activity classifier uses under the 課表功率區間 `pm` (SP-618):
    Zone 3's floor = the lower edge of the set's level for 3A (88–95 %), Zone 5's = the set's level
    for Palladino 5 (106–116 %); the 5-min path's 103 % in the set's frame (× the model's FTP ÷ CP
    for 個人化區間), never above Zone 5's floor. Palladino / no set: STIM_EDGES."""
    if pm is None or pm.get("system") in (None, DEFAULT) or not pm.get("levels"):
        return dict(STIM_EDGES)
    z3 = band(0.88, 0.95, pm)[0]
    p_hi = band(1.06, 1.16, pm)[0]
    k = float(pm.get("scale") or 1.0) if pm.get("system") == "personal" else 1.0
    return {"z3": round(float(z3), 4), "p_lo": round(min(STIM_EDGES["p_lo"] * k, float(p_hi)), 4),
            "p_hi": round(float(p_hi), 4)}


def gap_warning(pm: Optional[dict], cp: Optional[float]) -> str:
    """The SP-363 Q3-1 (c) reminder of a 個人化區間 in effect whose model threshold power is more than
    GAP_WARN away from the 課表's CP (translated), else ""."""
    if not isinstance(pm, dict) or pm.get("system") != "personal" or not cp:
        return ""
    k = pm.get("scale")
    if not k:
        return ""
    gap = float(k) - 1.0
    if abs(gap) <= GAP_WARN + 1e-9:
        return ""
    return _(GAP_LOW if gap < 0 else GAP_HIGH, m=round(float(k) * cp), cp=round(cp),
             pct=round(abs(gap) * 100), lim=round(GAP_WARN * 100))


def plan_model(ds=None, day=None, cp: Optional[float] = None, chosen: Optional[str] = None) -> dict:
    """The 課表功率區間 in effect: {"system" (what the targets use), "setting" (what was chosen),
    "levels" [[id, name, lo, hi]] × CP, "fallback" (why personal fell back, else ""), "basis"}."""
    st = chosen if chosen in SYSTEMS else setting()
    out = {"system": st, "setting": st, "fallback": "", "basis": None}
    if st == "personal":
        p = personal_levels(ds, day, cp)
        if "levels" in p:
            return {**out, "levels": p["levels"], "basis": p["basis"], "scale": p["scale"], "pd": p["pd"]}
        out = {**out, "system": DEFAULT, "fallback": p["reason"]}
    return {**out, "levels": static_levels(out["system"])}


def _levels(pm: Optional[dict]) -> Optional[list]:
    if not isinstance(pm, dict) or pm.get("system") in (None, DEFAULT):
        return None
    lv = pm.get("levels")
    return lv if lv else None


def _top(hi) -> float:
    return OPEN_TOP if hi is None else float(hi)


def _holding(lv: list, x: float) -> list:
    for row in lv:
        if (row[2] or 0.0) <= x < _top(row[3]):
            return row
    return lv[0] if x < (lv[0][2] or 0.0) else lv[-1]


def band(lo: Optional[float], hi: Optional[float], pm: Optional[dict], role: str = "band") -> tuple:
    """(lo, hi) × CP of a Palladino-intent band in the 課表功率區間 `pm` (plan_model). role: "band"
    (the most overlapping level), "easy" (the level holding EASY_ANCHOR), "cap" (lo None: up to the
    top of the level holding hi). Palladino / no model: the band itself."""
    lv = _levels(pm)
    if lv is None or (lo is None and hi is None):
        return lo, hi
    # 個人化區間: the levels are the PD model's — the intent is read against them as fractions of the
    # model's own FTP (scale = model FTP ÷ CP), the level found is written back × CP
    k = float(pm.get("scale") or 1.0)
    fr = [[r[0], r[1], (r[2] or 0.0) / k, None if r[3] is None else r[3] / k] for r in lv]

    def out(row):
        return round(float(row[2] or 0.0) * k, 4), round(_top(row[3]) * k if row[3] is not None else OPEN_TOP, 4)
    if role == "cap" or lo is None:
        return None, out(_holding(fr, float(hi) - 1e-9))[1]
    if role == "easy":
        return out(_holding(fr, EASY_ANCHOR))
    best, best_ov = None, -1.0
    for row in fr:
        ov = min(float(hi), _top(row[3])) - max(float(lo), float(row[2] or 0.0))
        if ov > best_ov + 1e-12:
            best, best_ov = row, ov
    return out(best)


# WORKOUT_TARGETS ids → how they map (the module doc)
ROLE = {"recovery": "cap", "z2": "easy", "long": "easy", "trail": "easy"}


def target_band(tid: str, plo, phi, pm: Optional[dict]) -> tuple:
    """A zones.WORKOUT_TARGETS row's power band in the 課表功率區間."""
    if plo is None and phi is None:
        return plo, phi
    return band(plo, phi, pm, ROLE.get(tid, "band"))


def easy_band(frac: tuple, pm: Optional[dict]) -> tuple:
    """zones.EASY_BAND / LONG_BAND / TRAIL_BAND in the 課表功率區間 (an easy kind: the easy level)."""
    return band(frac[0], frac[1], pm, "easy")


def level_name(f: Optional[float], pm: Optional[dict]) -> Optional[str]:
    """「Z1 Easy」-style label of the level holding `f` × CP in the set (None: Palladino / no model,
    the caller keeps its own Palladino label)."""
    lv = _levels(pm)
    if lv is None or f is None:
        return None
    row = _holding(lv, float(f))            # f and the levels are both × CP
    return f"{row[0]} {_(row[1])}"


def label(pm: Optional[dict]) -> str:
    """The set's name for the target card (「Palladino % CP」 for the default)."""
    if not isinstance(pm, dict) or pm.get("system") in (None, DEFAULT):
        return "Palladino % CP"
    return _(TITLE[pm["system"]])


def rows_json(pm: dict, cp: Optional[float]) -> list[dict]:
    """The set's levels for the settings page: [{id, name, lo, hi, watts}]."""
    out = []
    for z, n, lo, hi in pm.get("levels") or []:
        out.append({"id": z, "name": _(n), "lo": lo, "hi": hi,
                    "watts": None if not cp else [round((lo or 0) * cp), None if hi is None else round(hi * cp)]})
    return out
