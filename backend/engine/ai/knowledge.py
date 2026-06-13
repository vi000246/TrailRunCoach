"""Curated training knowledge from the athlete's notes power-training notes.

Static (no RAG) — single-user, stable framework, zero API cost, offline, testable.
Update this module when the notes' training framework changes.
"""

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
- 運動員特性：偏無氧型（RWC 充足），間歇強度可略高於典型 Palladino 上緣；越野 PI 屬中階，
  爬升配速約 30 min/km @ HR 160（山地）。
- 訓練狀態判讀（TSB）：>5 新鮮（適合高強度 / 比賽）、-10~5 最佳、-25~-10 疲勞（注意恢復）、
  <-25 過度訓練（建議減量）。ACWR（急慢性比）>1.5 為高風險。
"""


def build_knowledge() -> str:
    return KNOWLEDGE_BLOCK
