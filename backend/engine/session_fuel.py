"""
「課前要吃」 on the app's session text (SP-286; docs/research/carb-periodization.md §2.1, §2.2,
§3 row 4, §5 C-2).

A 強度課 (kind quality) and a long run of ≥ 2 h get one line: eat a meal or snack with carbohydrate
1–4 h before, don't do it fasted. Eating before helps prolonged aerobic performance (Aird 2018
meta-analysis); the hard sessions need the fuel (Impey 2018 「fuel for the work required」; Mata
2019: 1–4 g/kg 1–4 h before — the owner chose no g/kg, 「一餐或點心」, 2026-10-06). Easy runs get
nothing, and nothing schedules a fasted / low-carb session (§2.1: no proven benefit).

The line is a display field (`pre_meal` on the session view, api/plan_sessions._view): it is not
part of `detail`, so the watch push (plan_store.push_dict → coros_workouts) never carries it.
"""
from __future__ import annotations

from typing import Optional

from backend.i18n import _

LONG_KINDS = ("long", "mountain")
LONG_MIN = 120                   # ≥ 2 h long runs (SP-286 驗收; Aird 2018: the fed state helps prolonged exercise)
HARD_KINDS = ("quality",)        # 強度課 / 間歇 (Impey 2018; Mata 2019)


def pre_meal(s: dict) -> Optional[dict]:
    """{"text"} for a not-done 強度課 or a ≥ 2 h long run; None otherwise (easy runs, short long runs,
    strength, tests, races, done / missed sessions)."""
    if (s.get("state") or "active") != "active" or s.get("done"):
        return None
    kind = s.get("kind")
    if kind in HARD_KINDS or (kind in LONG_KINDS and float(s.get("minutes") or 0) >= LONG_MIN):
        return {"text": _("課前 1–4 小時吃含碳水的一餐或點心，不要空腹做（Aird 2018、Mata 2019、Impey 2018）")}
    return None
