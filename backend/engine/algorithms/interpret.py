"""Rule-based plain-language interpretation for charts.

Deterministic, no LLM calls — produces a status, a coloured signal, a label,
and a one-line Traditional-Chinese summary so charts read at a glance.
The TSB thresholds mirror the dashboard-summary endpoint (analytics.py).
"""
from typing import Optional


def interpret_tsb(tsb: Optional[float]) -> dict:
    if tsb is None:
        return {
            "status": "unknown", "color": "gray", "label": "—",
            "summary": "尚無足夠資料判讀狀態。",
        }
    if tsb > 5:
        status, color, label = "fresh", "green", "新鮮"
        advice = "狀態新鮮，適合高強度或比賽。"
    elif tsb > -10:
        status, color, label = "optimal", "blue", "最佳"
        advice = "狀態最佳，維持訓練節奏。"
    elif tsb > -25:
        status, color, label = "tired", "yellow", "疲勞"
        advice = "略為疲勞，注意恢復與強度安排。"
    else:
        status, color, label = "overreached", "red", "過度訓練"
        advice = "負荷過高，建議減量恢復。"
    return {
        "status": status,
        "color": color,
        "label": label,
        "summary": f"目前 TSB {tsb:.0f}，狀態：{label}。{advice}",
    }
