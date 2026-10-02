"""
The 賽事計算機's goal (目標): which kind of goal the athlete is offered, and how
the goal compares with the model's own prediction.

Basis (the training basis setting): 課表偏好 「目標基準」 hr / power wins; on
自動 the 「使用功率」 setting decides (off = heart rate). Heart rate → the goal
is a pace (average pace or a finish time); power → an average power (W or % CP).
No goal = the model's own prediction (the default).

The comparison is a speed ratio: model_time / goal_time − 1, so +8 % means the
goal is 8 % faster than the model predicts. The thresholds are 推估 (no
primary source): faster than the model by more than FAST_WARN may not hold;
slower by more than SLOW_NOTE leaves a lot in reserve.
"""
from __future__ import annotations

from typing import Optional

FAST_WARN = 0.03       # 推估
FAST_BAD = 0.08        # 推估: 「很可能撐不住」
SLOW_NOTE = 0.10       # 推估


def basis(target_basis: Optional[str], use_power: Optional[bool]) -> dict:
    """{"basis": "hr" | "power", "how": "target_basis" | "use_power"}."""
    if target_basis in ("hr", "power"):
        return {"basis": target_basis, "how": "target_basis"}
    return {"basis": "power" if use_power is not False else "hr", "how": "use_power"}


def check(goal_time_s: float, model_time_s: Optional[float], mode: str) -> dict:
    """The goal against the model: {"goal_time_s", "model_time_s", "faster",
    "level": ok | fast | too_fast | slow, "message"}. `faster` > 0 = the goal is
    faster than the model."""
    out = {"mode": mode, "goal_time_s": goal_time_s, "model_time_s": model_time_s,
           "faster": None, "level": "ok", "message": None, "badge": "推估"}
    if not (goal_time_s and model_time_s and goal_time_s > 0 and model_time_s > 0):
        return out
    faster = model_time_s / goal_time_s - 1.0
    out["faster"] = faster
    if faster > FAST_BAD:
        out.update(level="too_fast", message=f"比模型預測快 {faster:.0%}，很可能撐不住")
    elif faster > FAST_WARN:
        out.update(level="fast", message=f"比模型預測快 {faster:.0%}，可能撐不住")
    elif faster < -SLOW_NOTE:
        out.update(level="slow", message=f"比模型預測慢 {-faster:.0%}：很保守，體力還有餘裕")
    return out
