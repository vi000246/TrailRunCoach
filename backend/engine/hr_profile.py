"""
Maximum / resting heart rate and the COROS heart-rate zone models — one place
that every chart and the workout targets read (user request 2026-10-03:
「最大心率要自動算，設定裡可以自行設定；靜息心率也要能在設定裡設定；可以選心率區間」).

Where the numbers come from (resolution order, per date):

  max HR   1. the user's own value: plan thresholds `mhr` dated on or before the
              day (設定 → 心率; the same dated plan.thresholds rows)        「你的設定 YYYY-MM-DD」
           2. the watch account (COROS zoneData.maxHr, stored at login / sync,
              「來自手錶」; the Garmin account's zones while Garmin is the 資料來源,
              SP-91) — owner
              2026-10-03: 「就用手錶的資料」
           3. thresholds.estimate_mhr (runs of the 365 days up to the day,
              「推估」), only without a watch value — training runs rarely reach
              HRmax, so it is a floor (the user's last 365 days peak at 191,
              the watch says 202)
           4. the age formula (Tanaka, 「推估」): without 1–3, or (SP-294, owner
              2026-10-14) when 3 is more than AGE_OVER_RUNS_BPM under it and the
              runs hold no hard effort (thresholds.hard_efforts: a race, a test,
              ≥ 95 % CP / LTHR for 30 min or ≥ CP / LTHR for 5 min; the LTHR the
              plan's, else 0.90 × the age formula) — an easy-only
              runner's peak is the top of his easy runs, not his max
           How far each is believed (threshold_confidence): 1 test → high, typed →
           medium; 2 medium once a run of the last 90 days held 10 s within 5 bpm
           of it (SP-493 R1), else low; 3 and 4 low. Only a believed max HR lets
           自動 pick 儲備心率.
  rest HR  1. plan thresholds `rhr` dated on or before the day            「你的設定 YYYY-MM-DD」
           2. the COROS account (zoneData.rhr)                            「來自手錶」
           3. none → the HRR model says 「沒有靜息心率，到設定填」

FIT files carry no zone / user_profile messages (checked on COROS FITs), so the
COROS account API is the only place the watch's settings can be read. The
account's zoneData (GET /account/query, read 2026-10-03) holds maxHr / rhr /
lthr and, per model, the zone edges as `ratio` (percent) + `hr` (bpm):
lthrZone 80/90/95/102/106/(255), rhrZone 59/74/84/88/95/100,
maxHrZone 50/60/70/80/90/100. Each array lists the UPPER edge of zones 1–6
(the user's HRR zone 2 = 141–163 = rhrZone[0]..[1], verified against the app),
so zone 1 is everything below the first ratio and zone 6 runs to the last.
maxHrZone reads the same way: zone 1 < 50 % HRmax (confirmed in the COROS app
by the owner, 2026-10-03).

The COROS edges are rounded to whole bpm as COROS shows them (53 + 0.59 × 149 =
140.9 → 141), so with the same max / rest HR the app and the watch agree.

The three COROS models (6 zones each: Recovery, Aerobic Endurance, Aerobic
Power, Threshold, Anaerobic Endurance, Anaerobic Power):
  lthr   % LTHR <80 / 80–90 / 90–95 / 95–102 / 102–106 / >106 (coros.com 「COROS
         Heart Rate Zones: The Ultimate Guide」; same ratios in the account)
  hrr    % HRR 59 / 74 / 84 / 88 / 95 — the same table as 徐國峰 RQ (zones.RQ_HRR_ZONES)
  hrmax  % HRmax 50 / 60 / 70 / 80 / 90 (from the account; no published source)

自動 (SP-493, the default; owner 2026-10-14): the 課表心率區間 setting `auto` picks the model by
which numbers are believable (threshold_confidence.auto_pick): a reliable LTHR (a test / an LTHR
you entered within AUTO_LTHR_DAYS, or an automatic estimate nothing contradicts) → lthr; else a
believable max HR and resting HR → hrr; else hrmax. A runner who never chose keeps `auto`; a model
chosen by hand stays locked. The model in effect ("生效區間") is what every 課表 HR target reads.

Two independent uses (owner 2026-10-03):
  A. 設定 → 課表心率區間 (`plan.hr_zone_model`: auto default | lthr | hrr | hrmax) sets the
     HR targets of the 課表 only (plan_hr_zones below; zones.training_targets,
     coros_workouts / workout_steps easy_hr + _work_hr) and the structure editor's
     HR 區間 choice (workout_steps.hr_model_zones; SP-30).
  B. the HR-zone charts keep their own selector (default Friel, as WKO5);
     the COROS models are extra choices there (activity_charts, period_zones,
     zones.SYSTEMS). Not linked to A.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from backend.i18n import N_, _

ACCOUNT_KEY = "athlete.coros_profile"       # user_settings: the COROS account's HR settings
MODEL_KEY = "plan.hr_zone_model"            # user_settings: 課表心率區間
PLAN_MODELS = ("lthr", "hrr", "hrmax")      # the zone models (what a 生效區間 can be)
AUTO = "auto"                               # SP-493: pick the model by which numbers are believable
SETTING_MODELS = (AUTO,) + PLAN_MODELS      # what the setting can hold
DEFAULT_SETTING = AUTO                      # SP-493: a runner who never chose (no stored row) is 自動
DEFAULT_PLAN_MODEL = "lthr"                 # the zones' own default (plan_hr_zones without a model)
AUTO_LTHR_DAYS = 183                        # SP-493 (owner 2026-10-14):「近半年實測」

ZONE_IDS = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6")
ZONE_NAMES = (N_("恢復"), N_("有氧耐力"), N_("有氧動力"), N_("閾值"), N_("無氧耐力"), N_("無氧動力"))
DEFAULT_RATIOS = {
    "lthr": (0.80, 0.90, 0.95, 1.02, 1.06),
    "hrr": (0.59, 0.74, 0.84, 0.88, 0.95),
    "hrmax": (0.50, 0.60, 0.70, 0.80, 0.90),
}
MODEL_LABEL = {"lthr": N_("乳酸閾值心率（COROS % LTHR 6 區）"), "hrr": N_("儲備心率（COROS % HRR 6 區）"),
               "hrmax": N_("最大心率（COROS % HRmax 6 區）"), AUTO: N_("自動（看哪個數字可信）")}
MODEL_SHORT = {"lthr": N_("COROS 乳酸閾"), "hrr": N_("COROS 儲備心率"), "hrmax": N_("COROS 最大心率"),
               AUTO: N_("自動")}
SOURCE = {
    AUTO: N_("自動：LTHR 可信（半年內實測或你填的，或自動估算沒有矛盾）→ 乳酸閾值區間；否則最大心率和靜息心率可信 → "
             "儲備心率區間；都不可信 → 最大心率區間。生效的區間換了會通知一次，已推到手錶的課會重推"),
    "lthr": N_("COROS 乳酸閾值心率區間：<80／80–90／90–95／95–102／102–106／>106% LTHR"
               "（coros.com「COROS Heart Rate Zones: The Ultimate Guide」）"),
    "hrr": N_("COROS 儲備心率區間：59／74／84／88／95% HRR，HRR = 最大心率 − 靜息心率"
              "（從 COROS 帳號讀到，和徐國峰 RQ 跑力的儲備心率表相同）"),
    "hrmax": N_("COROS 最大心率區間：50／60／70／80／90% 最大心率（從 COROS 帳號讀到，沒有公開文件）。"
                "限制：同樣 % 最大心率，每個人的乳酸閾值可以落在 60–90% HRmax（Iannetta 2020），"
                "所以這組區間對個人的強度不準，Friel／乳酸閾區間比較可靠"),
}
NO_REST = N_("沒有靜息心率，到設定填")
# 沒有 LTHR 時的先驗 (SP-289; docs/research/cold-start.md §1.2, §4.3, T2): the LAST layer of each, 推估.
# Max HR: 設定 → 手錶 → 跑步資料 → Tanaka 2001 [C3] 208 − 0.7 × age (r = −0.90; Friel [C7] warns
# against age formulas, so only when nothing else exists; the individual SD ~10 bpm is 未驗證).
# LTHR: 測試 → 本人資料估算 → 手錶 → 0.90 × max HR (Nuuttila 2025: LT2 at 90.6 ± 2.5 % HRmax,
# zones-and-thresholds.md; unsourced-rules.md §0.5.5). Owner 2026-10-06: the age formula last, labelled.
TANAKA_A, TANAKA_B = 208.0, 0.7
LTHR_OF_MHR = 0.90
AGE_MHR_SOURCE = N_("推估（年齡公式，個人可以差 10 bpm 以上）")
# SP-294 (owner 2026-10-14): the runs' max more than this under the age formula, and no hard effort in
# them → the age formula. 10 bpm: the individual SD of Tanaka's formula is about that size (cold-start.md
# §4.3, 未驗證) — a smaller gap is within the formula's own error, so the runner's own value stays.
AGE_OVER_RUNS_BPM = 10.0
AGE_OVER_RUNS_SOURCE = N_("推估（年齡公式 {age_v:.0f}，個人可以差 10 bpm 以上；近 365 天跑步最高 {run_v:.0f}，"
                          "但沒有比賽、測試或閾值以上的跑步，只能算下限，低了 {d:.0f} bpm 不採用）")
LTHR_PRIOR_SOURCE = N_("推估（最大心率的 90 %）")
# the easy runs' backup when the HR numbers are a prior (§4.3: the talk test, 教練常見做法, 未驗證)
TALK_TEST = N_("心率數字是推估的，以能完整講一句話的強度為準（說話測試）")
NO_MAX = N_("沒有最大心率，到設定填")

# 課表 session class → COROS zones (owner 2026-10-03): recovery Z1, easy / long / hike Z2,
# aerobic power / tempo Z3, threshold Z4, VO2 / anaerobic endurance Z5, sprints Z6 (no HR target)
CLASS_ZONES = {"Z3sub": (3, 3), "Z3near": (4, 4), "Z4": (5, 5), "Z5": (5, 5)}
TARGET_ZONES = {"recovery": (1, 1), "z2": (2, 2), "long": (2, 2), "trail": (2, 2), "climb": (3, 4),
                "hill": (4, 4), "threshold": (4, 4), "supra": (5, 5), "vo2": (5, 5)}


# ---------------------------------------------------------------------------
# the COROS account (written by sync/coros_client on login / sync)
# ---------------------------------------------------------------------------

def _ratios(arr) -> Optional[tuple]:
    """The five inner upper edges (fractions) of a COROS zone array, or None."""
    try:
        r = [float(z["ratio"]) / 100.0 for z in sorted(arr, key=lambda z: z.get("index", 0))]
    except (TypeError, KeyError, ValueError):
        return None
    if len(r) < 6 or any(b <= a for a, b in zip(r[:5], r[1:5])):
        return None
    return tuple(round(x, 4) for x in r[:5])


def parse_account(data: Optional[dict]) -> Optional[dict]:
    """The HR part of a COROS login / account response (`data`): {"max_hr",
    "rest_hr", "lthr", "ratios": {lthr, hrr, hrmax}, "hr_zone_type"} or None."""
    if not isinstance(data, dict):
        return None
    zd = data.get("zoneData") if isinstance(data.get("zoneData"), dict) else {}

    def num(*vals):
        for v in vals:
            try:
                if v is not None and float(v) > 0:
                    return int(round(float(v)))
            except (TypeError, ValueError):
                continue
        return None
    out = {"max_hr": num(zd.get("maxHr"), data.get("maxHr")),
           "rest_hr": num(zd.get("rhr"), data.get("rhr")),
           "lthr": num(zd.get("lthr")),
           "ratios": {k: v for k, v in (("lthr", _ratios(zd.get("lthrZone"))), ("hrr", _ratios(zd.get("rhrZone"))),
                                         ("hrmax", _ratios(zd.get("maxHrZone")))) if v},
           "hr_zone_type": data.get("hrZoneType")}
    if not (out["max_hr"] or out["rest_hr"] or out["ratios"]):
        return None
    return out


GARMIN_KEY = "athlete.garmin_profile"      # user_settings: the Garmin account's HR settings (SP-91)


def account(user_id: int = 1) -> Optional[dict]:
    """The stored watch-account HR settings (read-only), or None: the COROS account's; while Garmin
    is the 資料來源 (SP-91 trial) the Garmin account's (its zones, sync/garmin_client.py) first."""
    from backend.engine.wko5expr.datasource import read_setting
    from backend.sync import primary as P
    if P.normalize(read_setting(P.SETTING_KEY, None, user_id)) == "garmin":
        g = read_setting(GARMIN_KEY, None, user_id)
        if isinstance(g, dict):
            return g
    v = read_setting(ACCOUNT_KEY, None, user_id)
    return v if isinstance(v, dict) else None


def plan_model(user_id: int = 1) -> str:
    """The 課表心率區間 SETTING: auto | lthr | hrr | hrmax. No stored row = never chosen = auto
    (SP-493); a model the user picked (stored) stays. effective_model() says which zones are in effect."""
    from backend.engine.wko5expr.datasource import read_setting
    v = read_setting(MODEL_KEY, DEFAULT_SETTING, user_id)
    return v if v in SETTING_MODELS else DEFAULT_SETTING


def effective_model(ds=None, day: Optional[dt.date] = None, setting: Optional[str] = None) -> dict:
    """The model in effect (生效區間): {"model": lthr | hrr | hrmax, "setting", "auto": bool,
    "reason": text | "", "why": code}. A locked setting is itself; `auto` asks
    threshold_confidence.auto_choice on the Dataset's today (its own memo) — without a Dataset,
    or when it fails, the LTHR model (the zones' default)."""
    st = setting if setting in SETTING_MODELS else plan_model()
    if st != AUTO:
        return {"model": st, "setting": st, "auto": False, "reason": "", "why": "locked"}
    pick = None
    if ds is not None:
        try:
            from backend.engine import threshold_confidence as TC
            from backend.engine.wko5expr.dataset import day_to_date
            today = day_to_date(int(ds.today)) if getattr(ds, "today", None) is not None else (day or dt.date.today())
            pick = TC.auto_choice(ds, getattr(ds, "plan", None), today)
        except Exception:                   # noqa: BLE001 — a dataset without a plan / samples (tests)
            pick = None
    if not pick:
        return {"model": DEFAULT_PLAN_MODEL, "setting": AUTO, "auto": True, "reason": "", "why": "none"}
    return {"model": pick["model"], "setting": AUTO, "auto": True, "reason": pick.get("reason") or "",
            "why": pick.get("why") or ""}


def stamp(user_id: int = 1) -> str:
    """What the HR targets / zone charts read outside plan.json and the data:
    for cache keys (a changed setting or a new COROS value must not serve old charts)."""
    return json.dumps([account(user_id), plan_model(user_id)], sort_keys=True, default=str)


def ratios(model: str, acc: Optional[dict] = None) -> tuple:
    """The model's five inner edges: the COROS account's own when stored (the user may
    have changed them in the app), else the defaults."""
    r = ((acc or {}).get("ratios") or {}).get(model)
    return tuple(r) if r and len(r) == 5 else DEFAULT_RATIOS[model]


# ---------------------------------------------------------------------------
# max / resting HR in effect on a date
# ---------------------------------------------------------------------------

def _plan_row(ds, name: str, day: dt.date) -> Optional[tuple]:
    """(date, value, method) of the latest plan row with `name` on or before `day`."""
    plan = getattr(ds, "plan", None)
    rows = []
    for t in getattr(plan, "thresholds", None) or []:
        v = getattr(t, name, None)
        if v is not None and str(t.date)[:10] <= day.isoformat():
            rows.append((str(t.date)[:10], float(v), getattr(t, f"{name}_method", None)))
    return max(rows, key=lambda r: r[0]) if rows else None


# how a plan `mhr` row was obtained (planning.MHR_METHODS, SP-64) → the source text
MHR_METHOD_LABEL = {"test": N_("最大心率測試"), "race": N_("比賽"), "lab": N_("實驗室測試"),
                    "estimate": N_("撐 120 秒的心率（推估）")}


def max_hr(ds, day: dt.date, acc: Optional[dict] = None, use_account: bool = True) -> dict:
    """{"value", "kind": manual | estimate | coros | None, "source", "estimate"}."""
    r = _plan_row(ds, "mhr", day)
    est = None
    if r is not None:
        # kind stays "manual" (the user's own value, 設定 → 心率 「改回自動」); `method` says how
        lab = MHR_METHOD_LABEL.get(r[2] or "")
        return {"value": r[1], "kind": "manual", "method": r[2] or "manual",
                "source": f"{_(lab)} {r[0]}" if lab else _("你的設定 {day}", day=r[0]), "estimate": None}
    try:
        from backend.engine.thresholds import estimate_mhr
        est = estimate_mhr(ds, day)
    except Exception:                       # noqa: BLE001 — a dataset without samples
        est = None
    acc = account() if acc is None and use_account else acc
    watch = float(acc["max_hr"]) if acc and acc.get("max_hr") else None
    ev = float(est["value"]) if est and est.get("value") else None
    if watch is not None:
        note = _("；近 365 天跑步最高 {v:.0f}（推估）", v=ev) if ev is not None else ""
        return {"value": watch, "kind": "coros", "source": _("來自手錶（COROS 帳號）") + note, "estimate": est}
    a = _age_on(ds, day)
    if ev is not None:
        am = age_max_hr(a) if a is not None else None
        if am is not None and am - ev > AGE_OVER_RUNS_BPM and not _hard_efforts(ds, day, LTHR_OF_MHR * am):
            # SP-294: an easy-only runner's peak is a floor far under his max — the age formula instead
            return {"value": am, "kind": "age", "estimate": est, "age": a, "runs_value": ev,
                    "source": _(AGE_OVER_RUNS_SOURCE, age_v=am, run_v=ev, d=am - ev)}
        return {"value": ev, "kind": "estimate", "source": _("推估（近 365 天跑步）"), "estimate": est}
    if a is not None:
        # SP-289: the last layer — Tanaka's age formula, labelled 推估
        return {"value": age_max_hr(a), "kind": "age", "source": _(AGE_MHR_SOURCE), "estimate": est, "age": a}
    return {"value": None, "kind": None, "source": None, "estimate": est,
            "reason": _(NO_MAX) if not est else f"{_(NO_MAX)}（{est.get('reason')}）"}


def _hard_efforts(ds, day: dt.date, lthr_ref: Optional[float]) -> list:
    """thresholds.hard_efforts; on an error [[None, "error"]] — never 「no hard effort」 by accident."""
    try:
        from backend.engine.thresholds import hard_efforts
        return hard_efforts(ds, day, lthr_ref)
    except Exception:                       # noqa: BLE001
        return [[None, "error"]]


def age_max_hr(age: Optional[int]) -> Optional[float]:
    """Tanaka 2001 [C3]: 208 − 0.7 × age (whole bpm); None without a sane age."""
    if not isinstance(age, int) or isinstance(age, bool) or not 10 <= age <= 100:
        return None
    return float(round(TANAKA_A - TANAKA_B * age))


def _age_on(ds, day: dt.date) -> Optional[int]:
    """The age from the 一般設定 birth year (SP-211's 精靈) on `day`; None without one."""
    try:
        from backend.engine.athlete_profile import age
        a = age(getattr(getattr(ds, "plan", None), "profile", None) or {}, day)
    except Exception:                       # noqa: BLE001
        return None
    return a if age_max_hr(a) is not None else None


def lthr_prior(ds, day: dt.date, acc: Optional[dict] = None, use_account: bool = True) -> Optional[dict]:
    """The LTHR prior (SP-289): LTHR_OF_MHR × the max HR in effect (max_hr's whole order, the age
    formula last). {"value", "mhr", "mhr_kind", "mhr_source", "source"}; None without a max HR.
    Used only when there is no test, no estimate from the runner's data and no watch LTHR."""
    m = max_hr(ds, day, acc, use_account)
    if not m.get("value"):
        return None
    return {"value": float(round(LTHR_OF_MHR * float(m["value"]))), "mhr": float(m["value"]),
            "mhr_kind": m.get("kind"), "mhr_source": m.get("source"), "source": _(LTHR_PRIOR_SOURCE)}


def rest_hr(ds, day: dt.date, acc: Optional[dict] = None, use_account: bool = True) -> dict:
    """{"value", "kind": manual | coros | None, "source", "reason"}."""
    r = _plan_row(ds, "rhr", day)
    if r is not None:
        return {"value": r[1], "kind": "manual", "source": _("你的設定 {day}", day=r[0])}
    acc = account() if acc is None and use_account else acc
    if acc and acc.get("rest_hr"):
        return {"value": float(acc["rest_hr"]), "kind": "coros", "source": _("來自手錶（COROS 帳號）")}
    return {"value": None, "kind": None, "source": None, "reason": _(NO_REST)}


# ---------------------------------------------------------------------------
# zone edges in bpm
# ---------------------------------------------------------------------------

def zone_rows(model: str, lthr: Optional[float] = None, mhr: Optional[float] = None,
              rhr: Optional[float] = None, acc: Optional[dict] = None) -> dict:
    """{"rows": [(id, name, lo, hi)] bpm (zone 1 from 0, zone 6 open), "basis_text"}
    or {"reason"}. Edges rounded to whole bpm, as COROS shows them."""
    rs = ratios(model, acc)
    if model == "lthr":
        if not lthr:
            return {"reason": _("沒有 LTHR，區間算不出來")}
        edges = [round(r * lthr) for r in rs]
        text = f"LTHR {lthr:.0f} bpm"
    elif model == "hrr":
        if not mhr:
            return {"reason": _(NO_MAX)}
        if not rhr:
            return {"reason": _(NO_REST)}
        if mhr - rhr < 40:
            return {"reason": _("最大心率 {mhr:.0f} − 靜息心率 {rhr:.0f} 太小，檢查設定", mhr=mhr, rhr=rhr)}
        edges = [round(rhr + r * (mhr - rhr)) for r in rs]
        text = _("最大心率 {mhr:.0f}、靜息心率 {rhr:.0f} bpm（HRR {hrr:.0f}）", mhr=mhr, rhr=rhr, hrr=mhr - rhr)
    elif model == "hrmax":
        if not mhr:
            return {"reason": _(NO_MAX)}
        edges = [round(r * mhr) for r in rs]
        text = _("最大心率 {mhr:.0f} bpm", mhr=mhr)
    else:
        raise ValueError(model)
    lo = [0.0] + [float(e) for e in edges]
    hi = [float(e) for e in edges] + [None]
    return {"rows": [(ZONE_IDS[i], _(ZONE_NAMES[i]), lo[i], hi[i]) for i in range(6)], "basis_text": text}


def band(rows: list, z_from: int, z_to: int) -> tuple:
    """(lo, hi) bpm from zone z_from's lower edge to zone z_to's upper edge (1-based);
    lo None for zone 1, hi None for zone 6."""
    lo = rows[z_from - 1][2] or None
    hi = rows[z_to - 1][3]
    return lo, hi


# ---------------------------------------------------------------------------
# A. the 課表 HR targets
# ---------------------------------------------------------------------------

def plan_hr_zones(lthr: Optional[float], aet: Optional[float], aet_measured: bool,
                  mhr: Optional[float], rhr: Optional[float], model: Optional[str] = None,
                  acc: Optional[dict] = None, mhr_source: Optional[str] = None,
                  rhr_source: Optional[str] = None, auto: Optional[dict] = None) -> Optional[dict]:
    """The workouts' HR zones under the 課表心率區間 model (JSON-able), or None
    without any HR basis. A model without its data falls back to the LTHR model
    with the reason. A MEASURED AeT still caps easy runs in every model (a
    measured threshold beats a % formula); an estimated / 0.89 × LTHR AeT does not.

    {"model", "requested", "label", "fallback", "basis_text", "rows": [{id, name, lo, hi}], "mhr",
     "easy": [lo, hi], "easy_source", "work": {class: [lo, hi]}, "targets": {target id: [lo, hi]},
     "lthr_ratios": the LTHR model's edges (frac_hr), "setting": auto | the model, "auto": the
     effective_model dict when the setting is 自動 (SP-493), else None}"""
    want = model if model in PLAN_MODELS else DEFAULT_PLAN_MODEL
    use, fallback = want, None
    z = zone_rows(want, lthr, mhr, rhr, acc)
    if "reason" in z and want != "lthr":
        fallback = _("{model}：{reason}，改用 COROS 乳酸閾區間", model=_(MODEL_SHORT[want]), reason=z["reason"])
        use = "lthr"
        z = zone_rows("lthr", lthr, mhr, rhr, acc)
    if "reason" in z:
        return None
    rows = z["rows"]
    e_lo, e_hi = band(rows, 2, 2)
    e_src = f"{_(MODEL_SHORT[use])} Z2（{e_lo:.0f}–{e_hi:.0f} bpm）"
    if aet and aet_measured:
        e_hi = float(aet)
        e_src = _("量到的 AeT {aet:.0f} bpm（測試值優先於 {model} Z2 上緣）", aet=aet, model=_(MODEL_SHORT[use]))
    e_lo = min(e_lo or e_hi - 25, e_hi - 10)
    basis = z["basis_text"]
    if use in ("hrr", "hrmax"):
        basis += "；" + "、".join(s for s in (_("最大心率：{src}", src=mhr_source) if mhr_source else "",
                                            _("靜息心率：{src}", src=rhr_source) if rhr_source and use == "hrr" else "") if s)
    return {"model": use, "requested": want, "label": _(MODEL_LABEL[use]), "short": _(MODEL_SHORT[use]),
            "setting": AUTO if auto else want, "auto": auto or None,
            "lthr_ratios": list(ratios("lthr", acc)),
            "fallback": fallback, "basis_text": basis, "source": _(SOURCE[use]),
            "rows": [{"id": i, "name": n, "lo": lo, "hi": hi} for i, n, lo, hi in rows],
            "mhr": float(mhr) if mhr and use in ("hrr", "hrmax") else None,    # Z6's top (workout_steps)
            "rhr": float(rhr) if rhr and use == "hrr" else None,               # % HRR of a step (SP-642)
            "easy": [round(e_lo), round(e_hi)], "easy_source": e_src, "aet_measured": bool(aet and aet_measured),
            "work": {k: list(band(rows, *v)) for k, v in CLASS_ZONES.items()},
            "targets": {k: list(band(rows, *v)) for k, v in TARGET_ZONES.items()}}


def plan_hr_zones_for(ds, day: dt.date, lthr: Optional[float], aet: Optional[float],
                      aet_measured: bool) -> Optional[dict]:
    """plan_hr_zones with the model in effect (effective_model: the setting, or 自動's pick — SP-493),
    max / rest HR in effect on `day` and the COROS account."""
    eff = effective_model(ds, day)
    model = eff["model"]
    acc = account()
    m = r = None
    if model in ("hrr", "hrmax"):
        m = max_hr(ds, day, acc)
        r = rest_hr(ds, day, acc) if model == "hrr" else None
    return plan_hr_zones(lthr, aet, aet_measured, (m or {}).get("value"), (r or {}).get("value"), model, acc,
                         (m or {}).get("source"), (r or {}).get("source"), eff if eff["auto"] else None)


# ---------------------------------------------------------------------------
# what the texts call the easy-run cap (owner 2026-10-03: not 「AeT」 — with a COROS
# model it is the Z2 top; 「AeT」 only for a MEASURED one)
# ---------------------------------------------------------------------------

EASY_CAP = "輕鬆跑上限"
EASY_CAP_MEASURED = "（實測 AeT）"
EASY_CAP_TIP = N_("輕鬆跑上限＝課表心率區間的 Z2 上緣；有實測 AeT 時用實測值。"
                  "活動判讀和自動調整也用同一個上限：平均心率不超過上限 +3 bpm 算輕鬆（+3 是心率每天自然的落差）")   # _() where shown (overview)


def easy_cap_measured(src) -> bool:
    """Whether the easy cap is a measured AeT. `src`: plan_hr_zones' dict, or a plan's
    thresholds dict ({"hr_model", "aet_measured"}; week_plan / projection), or None."""
    if not isinstance(src, dict):
        return False
    if "easy" in src and "model" in src:
        return bool(src.get("aet_measured"))
    hrz = src.get("hr_model")
    if isinstance(hrz, dict):
        return bool(hrz.get("aet_measured"))
    return bool(src.get("aet_measured"))


def easy_cap_label(src=None, bpm: Optional[float] = None, measured: Optional[bool] = None) -> str:
    """「輕鬆跑上限 148 bpm」 / 「輕鬆跑上限 150 bpm（實測 AeT）」 / 「輕鬆跑上限」 — the one
    name of the easy-run HR cap in the 課表 texts. `src` as easy_cap_measured; `bpm`
    defaults to plan_hr_zones' easy top."""
    if measured is None:
        measured = easy_cap_measured(src)
    if bpm is None and isinstance(src, dict):
        e = src.get("easy") if "easy" in src else ((src.get("hr_model") or {}).get("easy") or [None, src.get("aet")])
        bpm = e[1] if isinstance(e, (list, tuple)) and len(e) == 2 else None
    return EASY_CAP + (f" {float(bpm):.0f} bpm" if bpm else "") + (EASY_CAP_MEASURED if measured else "")


def below(label: str) -> str:
    """「輕鬆跑上限 148 bpm 以下」 / 「輕鬆跑上限以下」 (a space only after 「bpm」)."""
    return label + (" 以下" if label.endswith("bpm") else "以下")


def easy_cap_hr(bpm: Optional[float] = None, measured: bool = False) -> str:
    """The HR target text of an easy / long / hike session: 「心率 ≤ 輕鬆跑上限 148 bpm」."""
    return "心率 ≤ " + easy_cap_label(None, bpm, measured)


# ---------------------------------------------------------------------------
# 「輕鬆」: one definition (SP-565, owner 2026-10-14). Before, three: the 課表's cap (the Z2 top
# above), the activity review's 「avg HR ≤ AeT + 3」 (AeT = 0.89 × LTHR without a plan AeT) and
# adapt's 偏強 「avg HR > 94 % LTHR」 — under 儲備心率 the same run could be easy by the 課表 and too
# hard by the review. Now every one reads easy_cap: the Z2 top of the zones in effect (自動's pick or
# the locked model, SP-493), a measured AeT when there is one. A run is easy when its average HR is
# ≤ the cap + EASY_TOL_BPM (is_easy).
# ---------------------------------------------------------------------------

EASY_TOL_BPM = 3.0     # HR's natural day-to-day spread (Lamberts & Lambert 2009: 3 ± 1 bpm), not a margin
                       # the coaches allow (= workout_review.AET_MARGIN, SP-528)


def easy_cap(hrz: Optional[dict]) -> Optional[dict]:
    """The easy-run HR cap of plan_hr_zones' dict: {"value" (bpm), "source", "measured", "model"};
    None without one. The one reader of the cap (the 課表, the activity review, adapt)."""
    e = (hrz or {}).get("easy")
    if not isinstance(e, (list, tuple)) or len(e) != 2 or not e[1]:
        return None
    return {"value": float(e[1]), "source": hrz.get("easy_source") or "", "measured": bool(hrz.get("aet_measured")),
            "model": hrz.get("model")}


def easy_cap_for(ds, day: dt.date, lthr: Optional[float], aet: Optional[float] = None) -> Optional[dict]:
    """easy_cap on `day` for an activity (workout_review, adapt): the zones in effect
    (plan_hr_zones_for: the model in effect, the max / rest HR on `day`) with the plan's MEASURED
    AeT on `day` (planning.threshold_row; an applied estimate is not a measured one) — what
    zones.training_targets builds for the 課表. Without zones (no LTHR and no max HR, or the WKO5
    cross-check mode, which keeps Friel as the 課表 does) the AeT `aet` given (the review's own:
    the plan's AeT, else 0.89 × LTHR), as zones.target_hr. Memoised on the Dataset per day,
    LTHR, the plan's dated thresholds, the language (the source text) and the HR settings (stamp)."""
    parity = bool(getattr(getattr(ds, "config", None), "parity", False))
    memo = getattr(ds, "memo", None)
    key = None
    if isinstance(memo, dict):
        from backend.i18n import current_locale
        try:
            rows = repr(getattr(getattr(ds, "plan", None), "thresholds", None))
        except Exception:                   # noqa: BLE001
            rows = None
        try:
            st = stamp()
        except Exception:                   # noqa: BLE001 — no settings DB (tests)
            st = None
        key = ("easy_cap", day.isoformat(), lthr, aet, parity, current_locale(), rows, st)
        if key in memo:
            return memo[key]
    cap = None
    if not parity:
        try:
            from backend.engine.planning import threshold_row
            plan = getattr(ds, "plan", None)
            ar = threshold_row(plan, "aethr", day) if plan is not None else None
            measured = bool(ar and ar["measured"])
            cap = easy_cap(plan_hr_zones_for(ds, day, lthr, ar["value"] if measured else None, measured))
        except Exception:                   # noqa: BLE001 — a dataset without a plan / settings (tests)
            cap = None
    if cap is None and aet:
        cap = {"value": float(aet), "source": "AeT", "measured": False, "model": None}
    if key is not None:
        memo[key] = cap
    return cap


def is_easy(avg_hr: Optional[float], cap) -> bool:
    """「輕鬆」 by heart rate: the average HR ≤ the easy cap (easy_cap's dict or bpm) + EASY_TOL_BPM.
    False without either number."""
    v = cap.get("value") if isinstance(cap, dict) else cap
    return bool(avg_hr and v and float(avg_hr) <= float(v) + EASY_TOL_BPM)


def over_easy(avg_hr: Optional[float], cap) -> bool:
    """The other side of is_easy: an average HR above the cap + EASY_TOL_BPM (False without either)."""
    v = cap.get("value") if isinstance(cap, dict) else cap
    return bool(avg_hr and v and float(avg_hr) > float(v) + EASY_TOL_BPM)


# ---------------------------------------------------------------------------
# the uphill HR cap of a walking session (登山 / 健行 / 百岳 / 陡坡健走; SP-115, owner
# 2026-10-05: AeT is too low for real climbing). 萩原正大・山本正嘉 2011〈歩行路の傾斜、
# 歩行速度、および担荷重量との関連からみた登山時の生理的負担度の体系的な評価〉體力科學
# 60:327–341 (full text; docs/research/mountaineering-physiology-scholars.md §1.1): 40
# treadmill conditions of mountain walking, the uphill LT at 62 % VO2max, 78 % HRmax, RPE
# 13–14; 山本's pace rule 「75 % HRmax 以下、または RPE 13 以下」 confirmed 「妥当」. Without a
# max HR: 長野縣 Safety Book (千島康稔): 「(220 − 年齡) × 0.75 以下的心率走是安全的」 (推估).
# Never below the easy-run cap (the cap does not get lower than before). Altitude: the same
# % HRmax (the same strain; a 3,000–3,950 m HRmax correction was not found — 待查, 推估).
# Downhill: no HR (萩原: downhill costs 35–50 % of the uphill O2 but the legs' RPE is higher).
# ---------------------------------------------------------------------------

WALK_PCT = 0.75                     # 萩原・山本 2011: ≤ 75 % HRmax
WALK_RPE = 13                       # 萩原・山本 2011: or RPE ≤ 13 (Borg 6–20 「有點吃力」)
AGE_MHR = 220                       # 長野縣 Safety Book: (220 − 年齡) × 0.75 (推估)
WALK_CAP = N_("爬坡上限")
WALK_DOWN = N_("下坡看腿的感覺，不看心率")
WALK_WHY = N_("這比一般輕鬆跑的上限高，因為是爬坡步行")
WALK_SRC = N_("萩原・山本 2011（體力科學 60:327–341）：模擬登山的上坡乳酸閾值在 78% 最大心率、RPE 13–14，"
              "「75% 最大心率以下、或 RPE 13 以下」的配速是妥當的；長野縣 Safety Book（千島康稔）："
              "(220 − 年齡) × 0.75 以下的心率走是安全的")


def walk_cap(mhr: Optional[float], aet: Optional[float] = None, age: Optional[int] = None,
             mhr_source: Optional[str] = None, mhr_estimate: bool = False) -> Optional[dict]:
    """The uphill HR cap of a walking session: 75 % of the max HR; without one 0.75 ×
    (220 − age) (推估); never below `aet` (the easy-run cap). None without a max HR or an age.

    {"value", "pct", "mhr", "basis": hrmax | age, "estimate", "floor": capped up to `aet`,
     "aet", "source", "rpe"} (JSON-able)."""
    if mhr:
        m, basis, src, est = float(mhr), "hrmax", mhr_source, bool(mhr_estimate)
    elif age and 10 <= int(age) <= 100:
        m, basis, est = float(AGE_MHR - int(age)), "age", True
        src = _("220 − 年齡 {age}（推估）", age=int(age))
    else:
        return None
    v = WALK_PCT * m
    floor = bool(aet and v < float(aet))
    return {"value": round(float(aet) if floor else v), "pct": WALK_PCT, "mhr": round(m), "basis": basis,
            "estimate": est, "floor": floor, "aet": round(float(aet)) if aet else None, "source": src,
            "rpe": WALK_RPE}


def walk_cap_for(ds, day: dt.date, aet: Optional[float], profile: Optional[dict] = None) -> Optional[dict]:
    """walk_cap with the max HR in effect on `day` (max_hr: 設定 → 手錶 → 推估) and the age from
    the 一般設定 birth year. Never raises (None)."""
    try:
        m = max_hr(ds, day)
    except Exception:                       # noqa: BLE001 — a dataset without a plan / samples
        m = {}
    age = None
    try:
        from backend.engine.athlete_profile import age as _age
        if profile is None:
            profile = getattr(getattr(ds, "plan", None), "profile", None)
        age = _age(profile or {}, day)
    except Exception:                       # noqa: BLE001
        age = None
    if (m or {}).get("kind") == "age":
        m = {}                              # the walk cap keeps its own (220 − age) × 0.75 (長野縣, SP-115)
    return walk_cap((m or {}).get("value"), aet, age, (m or {}).get("source"), (m or {}).get("kind") == "estimate")


def walk_cap_label(w: Optional[dict]) -> str:
    """「爬坡上限 146 bpm（75% 最大心率）」; 「（75% × (220 − 年齡)，推估）」 without a max HR;
    「（75% 最大心率 141 低於輕鬆跑上限，取輕鬆跑上限）」 when the floor applies."""
    if not w:
        return _(WALK_CAP)
    pct = round(w["pct"] * 100)
    if w.get("floor"):
        how = _("{pct}% 最大心率 {v:.0f} 低於輕鬆跑上限，取輕鬆跑上限", pct=pct, v=w["pct"] * w["mhr"])
    elif w.get("basis") == "age":
        how = _("{pct}% × (220 − 年齡)，推估", pct=pct)
    elif w.get("estimate"):
        how = _("{pct}% 最大心率，最大心率是推估", pct=pct)
    else:
        how = _("{pct}% 最大心率", pct=pct)
    return f"{_(WALK_CAP)} {w['value']:.0f} bpm（{how}）"


def walk_cap_hint(w: Optional[dict]) -> str:
    """The 技術地形 session's HR guidance (owner 2026-10-05: text only — its target, step bands and
    push stay as they are): 「心率參考上限約 146 bpm（75% 最大心率）或 RPE ≤ 13；技術路段以安全為主，
    不用硬壓心率」; 「（75% × (220 − 年齡)，推估）」 without a max HR; "" without a max HR or an age."""
    if not w or not w.get("value"):
        return ""
    pct = round(w["pct"] * 100)
    if w.get("floor"):
        how = _("{pct}% 最大心率 {v:.0f} 低於輕鬆跑上限，取輕鬆跑上限", pct=pct, v=w["pct"] * w["mhr"])
    elif w.get("basis") == "age":
        how = _("{pct}% × (220 − 年齡)，推估", pct=pct)
    elif w.get("estimate"):
        how = _("{pct}% 最大心率，最大心率是推估", pct=pct)
    else:
        how = _("{pct}% 最大心率", pct=pct)
    return _("心率參考上限約 {v:.0f} bpm（{how}）或 RPE ≤ {rpe}；技術路段以安全為主，不用硬壓心率",
             v=w["value"], how=how, rpe=WALK_RPE)


def walk_cap_hr(w: Optional[dict], aet: Optional[float] = None, measured: bool = False) -> str:
    """The target text of a walking session: 「心率 ≤ 爬坡上限 146 bpm（75% 最大心率）或 RPE ≤ 13，
    先到的為準」 — without a cap (no max HR, no age) the easy-run cap stands in."""
    hr = ("心率 ≤ " + walk_cap_label(w)) if w else easy_cap_hr(aet, measured)
    return _("{hr} 或 RPE ≤ {rpe}，先到的為準", hr=hr, rpe=WALK_RPE)


def walk_step_name() -> str:
    """The watch step name of a walking session's main step: 「上坡心率 ≤ 爬坡上限或 RPE ≤ 13；下坡看感覺」."""
    return _("上坡心率 ≤ {cap}或 RPE ≤ {rpe}；下坡看感覺", cap=_(WALK_CAP), rpe=WALK_RPE)


def walk_band(w: Optional[dict], easy: Optional[tuple]) -> Optional[tuple]:
    """("hr", lo, hi) of a walking step: the easy band's floor up to the walk cap
    (sync/coros_workouts, engine/workout_steps); `easy` when there is no cap."""
    if not w or not w.get("value"):
        return easy
    hi = int(round(w["value"]))
    lo = easy[1] if easy and easy[0] == "hr" else hi - 25
    lo = min(int(lo), hi - 10)
    return ("hr", lo, hi)


def work_band(hrz: Optional[dict], cls: Optional[str]) -> Optional[tuple]:
    """(lo, hi) bpm of an interval class (CLASS_ZONES) under the 課表 zones, or None."""
    if not hrz or not cls or cls not in (hrz.get("work") or {}):
        return None
    lo, hi = hrz["work"][cls]
    if hi is None or lo is None:
        return None
    return round(lo), round(hi)


def not_lthr(hrz: Optional[dict]) -> bool:
    """The 課表 zones in effect are not the LTHR model (儲備心率 / 最大心率): the × LTHR targets of the
    課表 (interval texts, a template's % LTHR) follow those zones instead (SP-493)."""
    return bool(hrz) and hrz.get("model") in ("hrr", "hrmax") and bool(hrz.get("rows"))


def frac_hr(frac: Optional[float], hrz: Optional[dict], lthr: Optional[float]) -> Optional[float]:
    """frac × LTHR as a 課表 target under the zones in effect (SP-493): the LTHR model → frac × LTHR;
    儲備心率 / 最大心率 → the same place in the same COROS zone of that model (0.95 × LTHR is the
    bottom of the threshold zone Z4 in both; linear inside a zone, the open Z1 / Z6 go on with
    Z2's / Z5's slope, never above the max HR). None without a basis. Only for PRESCRIBED
    fractions (a template's % LTHR, the race calculator's 「LTHR」 cap): a ratio fitted on the
    runner's own HR ÷ LTHR (the trail HR model) is already in bpm and stays × LTHR."""
    if frac is None:
        return None
    if not not_lthr(hrz):
        return float(frac) * float(lthr) if lthr else None
    edges = [float(x) for x in (hrz.get("lthr_ratios") or DEFAULT_RATIOS["lthr"])][:5]
    bpm = [float(r["hi"]) for r in hrz["rows"][:5]]
    if len(edges) != 5 or len(bpm) != 5 or any(b is None for b in bpm):
        return float(frac) * float(lthr) if lthr else None
    f = float(frac)
    if f <= edges[0]:
        slope = (bpm[1] - bpm[0]) / (edges[1] - edges[0])
        v = bpm[0] - (edges[0] - f) * slope
    elif f >= edges[4]:
        slope = (bpm[4] - bpm[3]) / (edges[4] - edges[3])
        v = bpm[4] + (f - edges[4]) * slope
    else:
        i = next(k for k in range(4) if edges[k] <= f <= edges[k + 1])
        v = bpm[i] + (f - edges[i]) / (edges[i + 1] - edges[i]) * (bpm[i + 1] - bpm[i])
    top = hrz.get("mhr")
    return min(v, float(top)) if top else v


def class_hr(hrz: Optional[dict], cls: Optional[str]) -> Optional[tuple]:
    """(lo, hi) bpm of an interval class for the 課表 TEXT (SP-493): the class's COROS zone
    (work_band, what the push gives a library step) when the zones in effect are 儲備心率 / 最大心率;
    None under the LTHR model (the texts keep their × LTHR bands there)."""
    return work_band(hrz, cls) if not_lthr(hrz) else None
