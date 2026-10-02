"""
"What should I train next" — a rule list over the fatigue band, the 4-week
load focus and the training phase. Every rule states the numbers it used, so
the athlete can see why (and disagree). A coaching heuristic [ours], not a
prediction.

Order of precedence:
  1. fatigue band overreaching (ATL/CTL >= 150 %)  -> rest / easy only
  2. phase taper / event / recovery                -> follow the phase
  3. the load-focus bucket with the largest shortfall
  4. nothing short: keep the mix, grow volume if the band is below 100 %
"""
from __future__ import annotations

from typing import Optional

from backend.i18n import _, N_

SESSIONS = {
    "rest": N_("休息或 30–45 分鐘恢復跑（Z1）"),
    "low": N_("長時間輕鬆跑 / 越野長距離（Z2，功率 < 85% mFTP 或心率 < 89% LTHR）"),
    "high": N_("節奏跑 / 門檻間歇（85–105% mFTP，例如 3×12 分鐘），或長上坡穩定爬升"),
    "anaerobic": N_("短間歇 / 上坡衝刺（> 105% mFTP，例如 8×2 分鐘上坡、30/30）"),
    "grow": N_("維持目前的比例，週量增加 5–10%"),
    "taper": N_("減量：維持強度、總量減 40–60%"),
    "recovery": N_("賽後恢復：輕鬆活動為主，不排強度"),
}


def _fmt(v: Optional[float], d: int = 0) -> str:
    return "—" if v is None else f"{v:.{d}f}"


def recommend(band: Optional[dict], focus: Optional[dict], phase: Optional[str],
              tsb: Optional[float] = None) -> dict:
    reasons: list[str] = []
    r = band.get("ratio") if band else None
    if band:
        reasons.append(_("疲勞比 ATL/CTL = {ratio}%（{label}）", ratio=_fmt(r), label=band["label"]))
    if tsb is not None:
        reasons.append(f"TSB = {_fmt(tsb, 1)}")

    if band and band["key"] == "overreaching":
        return {"key": "rest", "headline": _(SESSIONS["rest"]),
                "reasons": reasons + [_("疲勞比 ≥ 150%：先恢復，下一課不要排強度")]}
    if phase in ("taper", "event"):
        return {"key": "taper", "headline": _(SESSIONS["taper"]),
                "reasons": reasons + [_("目前是減量期") if phase == "taper" else _("目前是賽事週")]}
    if phase == "recovery":
        return {"key": "recovery", "headline": _(SESSIONS["recovery"]),
                "reasons": reasons + [_("目前是恢復期")]}

    short = [b for b in (focus or {}).get("buckets", []) if b.get("state") == "short"]
    if short:
        # largest relative shortfall first
        b = max(short, key=lambda x: (x["target"][0] - (x["pct"] or 0)) / max(x["target"][0], 1))
        reasons.append(_("4 週負荷焦點：{label} {pct}%，目標 {lo}–{hi}%",
                         label=b["label"], pct=_fmt(b["pct"]), lo=b["target"][0], hi=b["target"][1]))
        key = b["key"]
        if key != "low" and tsb is not None and tsb < -25:
            reasons.append(_("TSB < -25：強度課之前先安排一天輕鬆"))
        return {"key": key, "headline": _(SESSIONS[key]), "reasons": reasons}

    if focus and focus.get("total"):
        reasons.append(_("4 週負荷焦點三項都在目標範圍內"))
    if r is not None and r >= 100:
        return {"key": "low", "headline": _(SESSIONS["low"]),
                "reasons": reasons + [_("疲勞比已在最佳化區間，用輕鬆量維持")]}
    return {"key": "grow", "headline": _(SESSIONS["grow"]), "reasons": reasons}
