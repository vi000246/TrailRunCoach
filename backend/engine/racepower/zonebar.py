"""
The calculator's absolute-intensity bar (賽事計算機 → 預估結果, under 努力度; SP-118).

The 努力度 bar above it is RELATIVE intensity: this power against what the athlete
can hold for the race's duration. This bar is ABSOLUTE intensity: which zone the
race's average falls in. It follows the training basis (/goal-basis): power →
Palladino power zones (% CP, the plan's `zones`); heart rate → a heart-rate zone
system (zones.HR_SYSTEMS) at the race HR the calculator itself predicts
(watch_export.race_hr: trail = the trail HR model's x* × LTHR).

`hr_bar` is what the page needs for the heart-rate version; the page picks the
system (default = the 課表心率區間 model, hr_profile.plan_model, so there is no
second place to set it) and falls back to the power bar with `reason` when there
is no predicted HR (road: the road prediction is power only).
"""
from __future__ import annotations

from typing import Optional

from backend.i18n import N_, _

# 設定 → 課表心率區間 (hr_profile.PLAN_MODELS) → the matching zones.SYSTEMS id
SYSTEM_OF_MODEL = {"lthr": "coroslthr", "hrr": "coroshrr", "hrmax": "coroshrmax"}
DEFAULT_SYSTEM = "coroslthr"
# the short name the page's line uses: 「落在 Friel 2 區」
SHORT = {"frielhr": "Friel", "classichr": "Classic"}

NO_HR_ROAD = N_("路跑的預估只算功率，沒有平均心率，所以用功率區間")
NO_HR_LTHR = N_("沒有 LTHR，算不出這場的平均心率，所以用功率區間")
NO_HR_MODEL = N_("越野心率配速模型算不出這場的平均心率，所以用功率區間")


def hr_systems(lthr: Optional[float], mhr: Optional[float] = None, rhr: Optional[float] = None,
               acc: Optional[dict] = None) -> dict:
    """{system id: {"title", "short", "rows": [{id, name, lo, hi}] bpm (zone 1 from 0,
    the last open: hi None), "basis_text"} or {"title", "short", "reason"}} for every
    zones.HR_SYSTEMS table, in that order."""
    from backend.engine import hr_profile as HP
    from backend.engine.zones import HR_SYSTEMS, SYSTEMS
    out = {}
    for sid in HR_SYSTEMS:
        spec = SYSTEMS[sid]
        kind = spec.get("coros")
        head = {"title": _(spec["title"]), "short": _(HP.MODEL_SHORT[kind]) if kind else SHORT[sid]}
        if kind:
            z = HP.zone_rows(kind, lthr, mhr, rhr, acc)
            if "reason" in z:
                out[sid] = {**head, "reason": z["reason"]}
                continue
            rows = [{"id": i, "name": n, "lo": float(lo or 0.0), "hi": hi} for i, n, lo, hi in z["rows"]]
            out[sid] = {**head, "rows": rows, "basis_text": z["basis_text"]}
            continue
        if not lthr:
            out[sid] = {**head, "reason": _("沒有 LTHR，區間算不出來")}
            continue
        rows = [{"id": i, "name": _(n), "lo": round(float(lo or 0.0) * lthr, 1),
                 "hi": None if hi is None else round(hi * lthr, 1)} for i, n, lo, hi in spec["zones"]]
        out[sid] = {**head, "rows": rows, "basis_text": f"LTHR {lthr:.0f} bpm"}
    return out


def hr_bar(plan: dict, lthr: Optional[float], basis: Optional[dict] = None) -> dict:
    """The heart-rate version of the bar for a road / trail plan: {"hr" (bpm or None),
    "hr_source", "reason" (why there is no HR, else None), "default" (the system to show
    first), "systems" (hr_systems)}. `basis`: LiveContext.hr_basis() — max / rest HR, the
    COROS account and the 課表心率區間 model; without it only LTHR-based tables exist."""
    from backend.engine.racepower import watch_export as WE
    b = basis or {}
    hr, src = WE.race_hr(plan, {"lthr": lthr})
    reason = None
    if hr is None:
        reason = _(NO_HR_ROAD) if plan.get("type") == "road" else _(NO_HR_LTHR) if not lthr else _(NO_HR_MODEL)
    return {"hr": round(hr, 1) if hr else None, "hr_source": src, "reason": reason,
            "default": SYSTEM_OF_MODEL.get(b.get("model") or "", DEFAULT_SYSTEM),
            "systems": hr_systems(lthr, b.get("mhr"), b.get("rhr"), b.get("acc"))}
