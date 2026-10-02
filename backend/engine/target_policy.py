"""
Which number a session is run by — heart rate, power, or none — in ONE place
(docs/plans/workout-editor.plan.md §3.2.3–§3.2.4 `target_policy`). The
generator's text (plan_prefs.shape), the COROS push (sync/coros_workouts),
zones.WORKOUT_TARGETS' primary and the 課表 page all ask this function, which
replaces the four trail rules that disagreed (zones: trail = power;
plan_prefs._terrain_long: trail = HR; schedule.html autoText: non-road = HR;
the push: always HR).

Order: the session's own override (target_basis on the stored session, a user
edit) → 課表偏好 plan.prefs.target_basis (hr / power) → `auto`, by session
type (docs/research/vo2max-gate-and-trail-metric.md §2, zones-and-thresholds.md):
  road easy / long                                   功率 (% CP) with HR ≤ AeT as a cap (athlete's
                                                     call 2026-10-02; HR still guards heat / fatigue)
  trail easy, recovery, trail long days, hikes       心率 ≤ AeT (Uphill Athlete: the base
                                                     is built below AeT; long days: HR —
                                                     drift and the late-day cap protect)
  Zone 3 / Zone 5 intervals, hill repeats 3–8 %     功率 (Stryd ≈ fixed metabolic load on
                                                     0–8 % grades, van Rassel 2026; HR lags
                                                     55–70 s, Hunt 2015) — HR only as a cap note
  long climbs                                        none: suggestions only (power above 8 %
                                                     and HR on 20′+ climbs both partial)
  downhill practice                                  none (Stryd underestimates the eccentric
                                                     load, Kipp 2023; descend by skill)
  CP test                                            power (the all-out bouts are open)
  AeT test                                           by protocol: 徐國峰 90 / Friel → HR (or pace),
                                                     UA 60 / 40 / Evoke → a fixed power
A forced basis the thresholds can't fill falls back (power without CP → HR, HR
without LTHR/AeT → none) with the reason.
"""
from __future__ import annotations

from typing import Optional

BASES = ("auto", "hr", "power")
LABEL = {"auto": "自動（依課表類型）", "hr": "心率", "power": "功率", "pace": "配速", "none": "不設目標"}
SRC = {
    "hr_base": "Uphill Athlete（AeT 以下累積有氧基礎）；長天後段心率飄移（Coyle & González-Alonso 2001）",
    "power_easy": "路跑輕鬆／長跑看功率（Palladino Z1–Z2 % CP），心率 ≤ AeT 當上限：天熱、疲勞時心率先到就放慢",
    "power_iv":"Stryd 功率在 0–8% 坡≈固定代謝負荷（van Rassel 2026）；心率延遲 55–70 秒（Hunt 2015）",
    "climb": "長爬坡：> 8% 功率低估、心率在 20 分以上才準（vo2max-gate-and-trail-metric.md §2）",
    "down": "下坡：Stryd 功率低估離心負荷（Kipp 2023），看下降量與技術",
    "cp": "CP 測試：全力段不設上下限，事後用功率算 CP",
    "aet": "AeT 測試：依方式（徐國峰 90／Friel 看心率；UA／Evoke 固定功率）",
}
TIP = ("課表的目標用心率還是功率。\n自動：路跑的輕鬆跑、長跑用功率，心率 ≤ AeT 當上限（天熱、疲勞時心率先到就放慢）；"
       "越野輕鬆跑、山路長天、恢復跑用心率（≤ AeT）；3 區／5 區間歇和 3–8% 坡的爬坡重複用功率，"
       "心率只當上限提醒；長爬坡只給建議、不設目標；下坡練習不設目標；CP 測試用功率，AeT 測試依測試方式。\n"
       "心率：全部用心率區間（Friel % LTHR，輕鬆跑上限 AeT）。\n功率：全部用功率區間（Palladino % CP）；"
       "測試和下坡照它們自己的規則。\n每次課表也可以在編輯時改「目標用：自動／心率／功率」，只影響這次課表。")


def pref_basis(prefs) -> str:
    """課表偏好 target_basis (the old interval_target = hr reads as hr)."""
    b = getattr(prefs, "target_basis", "auto") if prefs is not None else "auto"
    if b == "auto" and getattr(prefs, "interval_target", "power") == "hr":
        return "hr"
    return b if b in BASES else "auto"


def _aet_basis(s: dict) -> str:
    from backend.engine.aet_test import protocol_of_title
    p = protocol_of_title(s.get("title"))
    return "hr" if p in ("xu90", "friel") else "power"


def session_type(s: dict) -> str:
    """easy / long / trail_long / hike / interval / hill / climb / downhill / cp_test / aet_test / other."""
    kind, title = s.get("kind"), str(s.get("title") or "")
    if kind == "test":
        from backend.engine.aet_test import is_aet_session
        return "aet_test" if is_aet_session(s) else "cp_test"
    if "下坡" in title and kind in ("easy", "long", "quality"):
        return "downhill"
    if "長爬坡" in title:
        return "climb"
    if kind == "mountain":
        return "trail_long"
    if kind == "quality":
        return "hill" if ("爬坡" in title or "上坡" in title or s.get("terrain") == "trail") else "interval"
    if kind == "hike":
        return "hike"
    if kind == "long":
        return "trail_long" if s.get("terrain") in ("trail", "hike") or "山路" in title else "long"
    if kind in ("easy", "heat_passive"):
        return "trail_easy" if s.get("terrain") in ("trail", "hike") or "山路" in title or "越野" in title else "easy"
    return "other"


# road easy / long: power first with an HR cap (the athlete's call, 2026-10-02: power has no lag and
# needs only CP; the HR cap still guards heat and fatigue). Trail stays HR (Stryd only validated 3–8 %).
AUTO = {"easy": ("power", "power_easy"), "long": ("power", "power_easy"), "trail_easy": ("hr", "hr_base"),
        "trail_long": ("hr", "hr_base"),
        "hike": ("hr", "hr_base"), "interval": ("power", "power_iv"), "hill": ("power", "power_iv"),
        "climb": ("none", "climb"), "downhill": ("none", "down"), "cp_test": ("power", "cp"),
        "aet_test": (None, "aet"), "other": ("hr", "hr_base")}


def target_policy(s: dict, prefs=None, th: Optional[dict] = None) -> dict:
    """{"basis": hr | power | none, "chosen": auto | hr | power (where it came from),
        "type", "why", "source", "hr_cap": bool (an HR cap note on power sessions),
        "fallback": "" | reason}."""
    t = session_type(s)
    own = s.get("target_basis")
    chosen = own if own in ("hr", "power") else pref_basis(prefs)
    base, key = AUTO[t]
    if base is None:
        base = _aet_basis(s)
    basis = base
    why = f"自動：{ {'easy': '輕鬆跑', 'trail_easy': '越野輕鬆跑', 'long': '長跑', 'trail_long': '山路長天', 'hike': '健行', 'interval': '間歇', 'hill': '爬坡重複', 'climb': '長爬坡', 'downhill': '下坡練習', 'cp_test': 'CP 測試', 'aet_test': 'AeT 測試', 'other': '其他'}[t] }看{LABEL[base]}"
    if chosen in ("hr", "power") and t not in ("cp_test", "aet_test", "downhill", "climb"):
        basis = chosen
        why = ("這次課表你選了" if own in ("hr", "power") else "課表偏好：") + LABEL[chosen]
    fb = ""
    th = th or {}
    if basis == "power" and th and not th.get("cp"):
        basis, fb = "hr", "沒有 CP：改用心率"
    if basis == "hr" and th and not (th.get("aet") or th.get("lthr")):
        basis, fb = "none", "沒有 AeT／LTHR：不設目標"
    return {"basis": basis, "chosen": own if own in ("hr", "power") else chosen, "type": t, "why": why + (f"（{fb}）" if fb else ""),
            "source": SRC[key], "hr_cap": basis == "power" and t in ("interval", "hill", "easy", "long"), "fallback": fb}


def target_text(target: str, basis: str) -> str:
    """The target text of the chosen basis: keep the 功率 or the 心率 parts (the other as
    a cap note on power sessions is the caller's choice)."""
    parts = [p.strip() for p in (target or "").split("·") if p.strip()]
    if basis == "hr":
        keep = [p for p in parts if p.startswith("心率")]
    elif basis == "power":
        keep = [p for p in parts if p.startswith(("功率", "RPE"))]
    else:
        keep = []
    return " · ".join(keep) if keep else ("" if basis == "none" else target or "")
