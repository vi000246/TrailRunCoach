"""
Maximum / resting heart rate and the COROS heart-rate zone models — one place
that every chart and the workout targets read (user request 2026-10-03:
「最大心率要自動算，設定裡可以自行設定；靜息心率也要能在設定裡設定；可以選心率區間」).

Where the numbers come from (resolution order, per date):

  max HR   1. the user's own value: plan thresholds `mhr` dated on or before the
              day (設定 → 心率; the same dated plan.thresholds rows)        「你的設定 YYYY-MM-DD」
           2. the watch account (COROS zoneData.maxHr, stored at login / sync,
              「來自手錶」; a future Garmin source goes here too) — owner
              2026-10-03: 「就用手錶的資料」
           3. thresholds.estimate_mhr (runs of the 365 days up to the day,
              「推估」), only without a watch value — training runs rarely reach
              HRmax, so it is a floor (the user's last 365 days peak at 191,
              the watch says 202)
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

Two independent uses (owner 2026-10-03):
  A. 設定 → 課表心率區間 (`plan.hr_zone_model`: lthr default | hrr | hrmax) sets the
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
PLAN_MODELS = ("lthr", "hrr", "hrmax")
DEFAULT_PLAN_MODEL = "lthr"

ZONE_IDS = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6")
ZONE_NAMES = (N_("恢復"), N_("有氧耐力"), N_("有氧動力"), N_("閾值"), N_("無氧耐力"), N_("無氧動力"))
DEFAULT_RATIOS = {
    "lthr": (0.80, 0.90, 0.95, 1.02, 1.06),
    "hrr": (0.59, 0.74, 0.84, 0.88, 0.95),
    "hrmax": (0.50, 0.60, 0.70, 0.80, 0.90),
}
MODEL_LABEL = {"lthr": N_("乳酸閾值心率（COROS % LTHR 6 區）"), "hrr": N_("儲備心率（COROS % HRR 6 區）"),
               "hrmax": N_("最大心率（COROS % HRmax 6 區）")}
MODEL_SHORT = {"lthr": N_("COROS 乳酸閾"), "hrr": N_("COROS 儲備心率"), "hrmax": N_("COROS 最大心率")}
SOURCE = {
    "lthr": N_("COROS 乳酸閾值心率區間：<80／80–90／90–95／95–102／102–106／>106% LTHR"
               "（coros.com「COROS Heart Rate Zones: The Ultimate Guide」）"),
    "hrr": N_("COROS 儲備心率區間：59／74／84／88／95% HRR，HRR = 最大心率 − 靜息心率"
              "（從 COROS 帳號讀到，和徐國峰 RQ 跑力的儲備心率表相同）"),
    "hrmax": N_("COROS 最大心率區間：50／60／70／80／90% 最大心率（從 COROS 帳號讀到，沒有公開文件）。"
                "限制：同樣 % 最大心率，每個人的乳酸閾值可以落在 60–90% HRmax（Iannetta 2020），"
                "所以這組區間對個人的強度不準，Friel／乳酸閾區間比較可靠"),
}
NO_REST = N_("沒有靜息心率，到設定填")
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


def account(user_id: int = 1) -> Optional[dict]:
    """The stored COROS account HR settings (read-only), or None."""
    from backend.engine.wko5expr.datasource import read_setting
    v = read_setting(ACCOUNT_KEY, None, user_id)
    return v if isinstance(v, dict) else None


def plan_model(user_id: int = 1) -> str:
    from backend.engine.wko5expr.datasource import read_setting
    v = read_setting(MODEL_KEY, DEFAULT_PLAN_MODEL, user_id)
    return v if v in PLAN_MODELS else DEFAULT_PLAN_MODEL


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
    if ev is not None:
        return {"value": ev, "kind": "estimate", "source": _("推估（近 365 天跑步）"), "estimate": est}
    return {"value": None, "kind": None, "source": None, "estimate": est,
            "reason": _(NO_MAX) if not est else f"{_(NO_MAX)}（{est.get('reason')}）"}


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
                  rhr_source: Optional[str] = None) -> Optional[dict]:
    """The workouts' HR zones under the 課表心率區間 model (JSON-able), or None
    without any HR basis. A model without its data falls back to the LTHR model
    with the reason. A MEASURED AeT still caps easy runs in every model (a
    measured threshold beats a % formula); an estimated / 0.89 × LTHR AeT does not.

    {"model", "requested", "label", "fallback", "basis_text", "rows": [{id, name, lo, hi}], "mhr",
     "easy": [lo, hi], "easy_source", "work": {class: [lo, hi]}, "targets": {target id: [lo, hi]}}"""
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
            "fallback": fallback, "basis_text": basis, "source": _(SOURCE[use]),
            "rows": [{"id": i, "name": n, "lo": lo, "hi": hi} for i, n, lo, hi in rows],
            "mhr": float(mhr) if mhr and use in ("hrr", "hrmax") else None,    # Z6's top (workout_steps)
            "easy": [round(e_lo), round(e_hi)], "easy_source": e_src, "aet_measured": bool(aet and aet_measured),
            "work": {k: list(band(rows, *v)) for k, v in CLASS_ZONES.items()},
            "targets": {k: list(band(rows, *v)) for k, v in TARGET_ZONES.items()}}


def plan_hr_zones_for(ds, day: dt.date, lthr: Optional[float], aet: Optional[float],
                      aet_measured: bool) -> Optional[dict]:
    """plan_hr_zones with the setting, max / rest HR in effect on `day` and the COROS account."""
    model = plan_model()
    acc = account()
    m = r = None
    if model in ("hrr", "hrmax"):
        m = max_hr(ds, day, acc)
        r = rest_hr(ds, day, acc) if model == "hrr" else None
    return plan_hr_zones(lthr, aet, aet_measured, (m or {}).get("value"), (r or {}).get("value"), model, acc,
                         (m or {}).get("source"), (r or {}).get("source"))


# ---------------------------------------------------------------------------
# what the texts call the easy-run cap (owner 2026-10-03: not 「AeT」 — with a COROS
# model it is the Z2 top; 「AeT」 only for a MEASURED one)
# ---------------------------------------------------------------------------

EASY_CAP = "輕鬆跑上限"
EASY_CAP_MEASURED = "（實測 AeT）"
EASY_CAP_TIP = N_("輕鬆跑上限＝課表心率區間的 Z2 上緣；有實測 AeT 時用實測值")   # _() where shown (overview)


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


def work_band(hrz: Optional[dict], cls: Optional[str]) -> Optional[tuple]:
    """(lo, hi) bpm of an interval class (CLASS_ZONES) under the 課表 zones, or None."""
    if not hrz or not cls or cls not in (hrz.get("work") or {}):
        return None
    lo, hi = hrz["work"][cls]
    if hi is None or lo is None:
        return None
    return round(lo), round(hi)
