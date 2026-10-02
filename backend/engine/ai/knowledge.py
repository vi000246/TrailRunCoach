"""Curated training knowledge for the AI coach's system prompt, plus the
athlete's own traits built from their data at request time.

The framework part is static (no RAG): stable, zero API cost, offline,
testable. Nothing about one particular runner is written here any more
(generalize-athlete plan P14): `athlete_traits` derives CP, W′ / CP, the
thresholds and the trail climbing rate from the athlete's own plan and
activities, and says nothing when there is no data. The TSB bands come from
engine/status.py so the coach and the overview read form the same way.
"""
from __future__ import annotations

from typing import Optional

from backend.engine import status as ST


def _tsb_text() -> str:
    lo, hi = ST.TSB_PRODUCTIVE
    a_lo, a_hi = ST.TSB_A
    return (f"- 訓練狀態判讀（TSB，與總覽相同）：{a_lo:+.0f}~{a_hi:+.0f} 為 A 級賽前減量後的狀態（Palladino）；"
            f"{lo:.0f}~{hi:.0f} 為有效訓練區（Friel）；< {ST.TSB_OVERREACH:.0f} 過度負荷（建議減量）；"
            f"> {ST.TSB_STALE:+.0f} 休太久、體能在流失。ACWR（急慢性比）>1.5 為高風險。")


KNOWLEDGE_BLOCK = """=== 訓練知識庫（Palladino 個人化功率訓練） ===
- 功率區間（Palladino，依個人化 rFTP）：Z1 恢復 / Z2 耐力 / Z3 節奏 / Z4 閾值 /
  Z5 VO2max / Z6 無氧 / Z7 神經肌肉。各區的瓦數範圍依運動員 rFTP 計算。
- CP（臨界功率）由 3–30 分鐘 MMP 曲線擬合；TTE（耐受時間）約 30 分鐘為閾值耐受參考。
- 間歇模板（給處方時請具體化「時間 × 目標瓦數或 zone × 組數 × 恢復」）：
  · VO2max：3–5 分鐘 @ Z5，組間等量或略短恢復，4–6 組。
  · 無氧 / RWC（無氧儲備）：30 秒–2 分鐘 @ Z6+，充分恢復（1:2~1:3），重質不重量。
  · 閾值：8–20 分鐘 @ Z4，組間 2–5 分鐘恢復，2–4 組。
- 越野 / 爬升：技術地形以心率（hrTSS）為主負荷，配速型 rTSS 會低估；爬升用 VAM
  （垂直速度 m/hr）評估爬升能力；GAP（坡度調整配速）用來比對平路等效強度。
- 沒有功率計的跑者：處方改用心率區間（LTHR）或配速，不要給瓦數。
""" + _tsb_text() + "\n"


def build_knowledge() -> str:
    return KNOWLEDGE_BLOCK


def athlete_traits(*, cp: Optional[float] = None, wprime_j: Optional[float] = None,
                   lthr: Optional[float] = None, aethr: Optional[float] = None,
                   sex: Optional[str] = None, trail_climb_m_per_h: Optional[float] = None,
                   trail_hr: Optional[float] = None, trail_n: int = 0) -> str:
    """The 「運動員特性」 block from the athlete's own numbers; "" without any.
    W′ / CP is compared with the single-bout prior for the athlete's sex
    (cp_protocols.wprime_prior): above mean + 0.5 SD reads as an anaerobic
    profile, below mean − 0.5 SD as an aerobic one (推估 cut-offs)."""
    lines: list[str] = []
    if cp:
        lines.append(f"- CP（跑步臨界功率）：{cp:.0f} W")
    if cp and wprime_j:
        from backend.engine import cp_protocols as CPP
        w0, sd, _ = CPP.wprime_prior(sex)
        kind = ("偏無氧型（W′ 充足，間歇強度可略高於典型上緣）" if wprime_j > w0 + 0.5 * sd else
                "偏有氧型（W′ 偏小，短間歇以完成組數為主）" if wprime_j < w0 - 0.5 * sd else "有氧／無氧均衡")
        lines.append(f"- W′ {wprime_j / 1000:.1f} kJ（W′/CP {wprime_j / cp:.0f} 秒）：{kind}（推估）")
    if lthr:
        lines.append(f"- LTHR：{lthr:.0f} bpm" + (f"；AeT 心率：{aethr:.0f} bpm" if aethr else ""))
    if trail_climb_m_per_h and trail_n:
        hr = f" @ 平均心率 {trail_hr:.0f}" if trail_hr else ""
        lines.append(f"- 越野跑：近 90 天 {trail_n} 次，平均每小時爬升 {trail_climb_m_per_h:.0f} m{hr}"
                     "（整趟平均，含平路與下坡，不是純爬坡 VAM）")
    if not lines:
        return ""
    return "=== 運動員特性（由本人資料算出） ===\n" + "\n".join(lines) + "\n"
