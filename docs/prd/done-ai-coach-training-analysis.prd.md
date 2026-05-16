# AI Coach — 訓練分析與圖表理解

## Problem Statement

運動員自己分析訓練資料時，面臨「圖表太多、無從下手」的認知超載問題：
PMC、負荷曲線、MMP 等圖表資料齊全，但沒有任何解說，等同於有儀表板沒有飛行員手冊。
不知道「現在的數字代表什麼」、「今天該怎麼訓練」，導致訓練決策仍靠直覺而非資料。

## Evidence

- 用戶直述：「圖表太多，要分析無從下手，理解困難」
- 用戶直述：「WKO5 沒有 AI 功能很難用，不好閱讀理解圖表」
- 現有 AiPage 只有 MCP 設定說明 — 代表用戶已嘗試走 AI 路線但體驗斷裂
- 所有計算基礎（NP、TSS、CTL/ATL/TSB、MMP）皆已完成，缺的是「解讀層」

## Proposed Solution

在網站內嵌 AI 教練對話介面，讓 AI 直接存取訓練資料庫並以自然語言解釋圖表、
給出訓練建議。同時簡化主儀表板，改以「今日狀態卡片 + AI 週摘要」取代原本的
圖表堆疊，並針對越野跑 / 山岳跑補充缺少的爬坡配速與有氧效率分析圖表。

## Key Hypothesis

We believe 在網站內嵌 AI 教練問答 will 讓運動員在 3 分鐘內理解自己的訓練狀態 for 自我訓練的跑步 / 越野跑運動員.
We'll know we're right when 用戶每週主動開啟 AI 教練對話 ≥ 3 次，且能說出本週訓練的具體數字。

## What We're NOT Building

- 騎車專屬分析圖表 — 現階段無騎車資料，架構留介面但不實作
- 訓練計畫生成器 — AI 分析現有訓練，但不自動排課表
- 多運動員管理介面 — 目前 athlete_id=1 單人使用，不擴展到教練管多人
- 即時訓練同步推送 — AI 教練分析歷史資料，不做即時裝置監控

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| AI 對話啟用率 | 每週 ≥ 3 次對話 | 後端 chat API 呼叫計數 |
| 首次開啟到第一個 AI 回應 | < 5 秒 | API 回應時間 |
| 儀表板關鍵指標一眼可讀 | 主頁只顯示 ≤ 5 個數字 | UI review |
| 訓練後 AI 分析準確性 | 用戶不需要反覆追問 | 對話輪次 ≤ 3 per session |

## Open Questions

- [x] ~~AI 要用 Claude API 還是 OpenAI API？~~ → **兩者都支援**，設定頁讓用戶選擇 provider 並輸入 key
- [x] ~~AI 回應語言~~ → **固定回覆中文**（system prompt 明確指定）
- [x] ~~GAP TSS~~ → **是，納入計畫**，越野跑場景需要坡度調整後的負荷計算（見越野跑分析設計）
- [x] ~~API Key 管理~~ → **設定頁 UI 輸入**，存於 DB（AthleteSettings 擴充）
- [ ] GAP 修正公式選擇：Minetti 實驗曲線（更準確）vs 線性近似（更快實作）？建議先用線性

---

## Users & Context

**Primary User**
- **Who**: 自我訓練的跑步 / 越野跑 / 爬山運動員，有基礎訓練知識但非專業教練
- **Current behavior**: 同步 Coros 資料後打開網站，看一堆圖表但不確定該重視哪個
- **Trigger**: 訓練後想知道「這次練得怎樣」、週末前想知道「這週可以衝強度嗎」
- **Success state**: 打開網站 → 看到今日狀態卡 → 問 AI 一個問題 → 得到具體建議 → 關閉

**Job to Be Done**
When 我剛完成訓練或計劃明天的訓練, I want to 快速了解我的訓練負荷與身體狀態, so I can 做出有依據的訓練決策（練還是休）。

**Non-Users**
- 需要教練管理多名學員的場景 — 不是這個產品的定位
- 完全不看資料只靠感覺訓練的運動員

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | **In-app AI Coach Chat** | 核心差異化，解決「圖表看不懂」的根本問題 |
| Must | **AI 週訓練摘要** | 自動產生，讓用戶不需主動提問也能得到洞察 |
| Must | **API Key 設定介面** | 支援 Claude API / OpenAI API，用戶自備 key |
| Should | **Smart Status Dashboard** | 簡化首頁，用 ≤ 5 個卡片取代圖表堆疊 |
| Should | **單次訓練 AI 分析** | 訓練後自動生成「這次訓練重點」文字摘要 |
| Should | **越野跑爬坡分析圖** | 海拔 + 配速疊圖，心率漂移，踏頻 vs 坡度 |
| Should | **越野跑分析圖表組** | 海拔疊圖、GAP、VAM、心率漂移、踏頻坡度散點 — 見下方詳細設計 |
| Could | **GAP TSS** | 用坡度調整配速重新計算 TSS，讓山路訓練負荷不被低估 |
| Could | **訓練對比圖** | 相似路線不同日期的表現對比 |
| Won't | 騎車專屬圖表（本期） | 無騎車資料，架構預留但不實作 |
| Won't | 訓練計畫自動生成 | 超出分析工具範疇 |

### MVP Scope

1. **AI Coach Chat**（in-app，有資料存取）
2. **API Key 設定**（設定頁輸入 Claude 或 OpenAI key）
3. **Smart Dashboard 首頁**（今日狀態：TSB / CTL / 本週 TSS + AI 週摘要）

越野跑圖表在 Milestone 2 驗證 AI 使用率後再加。

### 越野跑訓練分析設計（Milestone 4 詳細規格）

越野跑與公路跑的核心差異：**坡度改變了所有指標的基準值**。
同樣配速在爬坡時負荷遠大於平路，直接比較配速無意義。需要「坡度正規化」後才能分析。

#### 可用的原始資料
FIT 檔案已解析：`altitude_m`（每秒）、`distance_m`、`speed_ms`、`heart_rate_bpm`、`cadence_rpm`

從這些可以派生：
- `grade_%` = Δaltitude / Δdistance × 100（每秒坡度）
- `pace_s_per_km` = 1000 / speed_ms
- `gap_s_per_km` = grade-adjusted pace（坡度修正配速）
- `vam_m_per_hr` = Δaltitude / Δtime × 3600（垂直速度）

#### 圖表 1 — 海拔 + 配速雙軸疊圖（最重要）
```
Y軸左：海拔 (m)     Y軸右：配速 (min/km) 或 GAP
X軸：距離 (km) 或 時間
呈現：海拔曲線 + 實際配速 + GAP 配速
洞察：AI 能指出「km 3–5 爬升 200m，配速從 6:00 掉到 12:00，GAP 維持在 7:30，代表爬坡效率正常」
```

#### 圖表 2 — 坡度 vs 踏頻散點圖（技術效率）
```
X軸：坡度 % (-20% 到 +30%)
Y軸：踏頻 spm
呈現：每個資料點，加上趨勢線
洞察：平路踏頻 180spm，>15% 坡降到 160spm 以下 → 代表上坡技術需要加強
AI 解讀：「你在陡坡（>12%）踏頻明顯下降，建議針對性加強上坡跑步技術」
```

#### 圖表 3 — 心率漂移分析（有氧效率）
```
呈現：以 GAP 正規化後的「心率 / GAP 比值」隨時間變化
方法：將訓練分前半 / 後半，比較 HR:GAP ratio 的變化
< 5% 漂移：有氧效率良好
> 8% 漂移：訓練強度過高或疲勞
洞察：排除爬坡造成的心率上升後，看「純有氧漂移」
```

#### 圖表 4 — VAM（垂直速度）分段表（爬坡能力）
```
對每段上坡（坡度 > 5%，持續 > 200m）計算：
- 爬升距離 (m)
- VAM (m/hr)
- 平均心率
- 是否跑步 or 健行（踏頻 < 155 spm 判定健行）
呈現：表格 + 小橫條圖
洞察：「這次爬升共 650m，分 3 段，平均 VAM 450m/hr，高強度區段佔 40%」
```

#### Grade-Adjusted Pace (GAP) 計算方式
採用 Strava/Garmin 使用的線性近似（MVP 階段）：
```
correction_factor = 1 + 0.033 × grade_pct   (上坡)
correction_factor = 1 - 0.015 × grade_pct   (下坡，grade_pct 為負值)
GAP = actual_pace × correction_factor
```
後期可升級為 Minetti 實驗曲線（更準確，但計算更複雜）。

#### GAP TSS 計算（越野跑 TSS）
```python
# 用 GAP 速度替代實際速度計算強度
gap_intensity = threshold_pace / gap_pace  # > 1 = 比閾值快
tss_gap = sum(gap_intensity² × Δt) / (threshold_s × 3600) × 100
```
讓一次 2 小時、爬升 800m 的越野跑，TSS 不再被低估為 50（實際負荷接近 90）。

#### AI 如何使用這些圖表
AI 接收到單次訓練資料時，優先判斷：
1. 爬升 > 100m → 觸發越野跑分析模式
2. 提供 GAP TSS（若已算）vs 原始 TSS 差異
3. 指出最陡段、VAM、踏頻效率
4. 給出具體建議（例：「你的下坡踏頻過低，建議加強下坡技術訓練」）

### User Flow

```
打開網站
  → 首頁顯示「今日狀態」（TSB 值 + 文字標籤：適合訓練 / 建議恢復 / 比賽狀態）
  → AI 自動產生本週摘要（50 字以內）
  → 用戶點擊「問 AI 教練」→ 對話框開啟
  → 輸入「昨天練得怎樣？」→ AI 查詢昨日訓練資料並回答
  → 用戶滿意 → 關閉
```

---

## Feasibility

**Verdict**: HIGH — 後端所有資料管道已完成（MMP/PMC/TSS 計算），MCP 工具層已存在可複用。
新增 AI Chat 只需一個 `/api/v1/ai/chat` endpoint + 前端 Chat UI 元件。

> 架構、資料模型、API 合約、技術選型細節請見 SRS。Run `/prp-srs docs/prd/ai-coach-training-analysis.prd.md`

---

## Product Milestones

| # | Milestone | User-Visible Value | Status | Depends | SRS | Plan |
|---|-----------|--------------------|--------|---------|-----|------|
| 1 | **AI Coach Chat** | 能在網站內用中文問 AI 教練任何訓練問題並得到有資料支撐的回答 | pending | - | - | - |
| 2 | **Smart Dashboard** | 首頁改為 5 個卡片 + AI 週摘要，10 秒內看懂本週狀態 | pending | 1 | - | - |
| 3 | **單次訓練 AI 分析** | 點開任一訓練，下方顯示 AI 生成的分析重點與建議 | pending | 1 | - | - |
| 4 | **越野跑爬坡分析** | 新增海拔疊圖、心率漂移、踏頻坡度圖，AI 能解釋這些圖 | pending | 2,3 | - | - |

### Milestone Details

**Milestone 1: AI Coach Chat**
- **User can now**: 在網站頁面開啟對話框，輸入問題如「本週 CTL 是多少？狀態好嗎？」並得到有資料的回答
- **Success signal**: 每週使用 ≥ 3 次，第一輪對話不超過 1 個追問
- **Out of scope**: 主動推送通知、語音輸入

**Milestone 2: Smart Dashboard**
- **User can now**: 首頁一眼看到今日 TSB 狀態文字（「適合高強度」/ 「建議恢復」），本週 TSS，AI 自動生成的 50 字摘要
- **Success signal**: 用戶在首頁停留 < 30 秒就能做出訓練決策
- **Out of scope**: 客製化 widget 拖拉排列

**Milestone 3: 單次訓練 AI 分析**
- **User can now**: 打開任一訓練記錄，看到「AI 教練分析」區塊，包含：這次訓練強度評估、心率表現、與近期平均比較
- **Success signal**: 用戶訓練後開啟訓練詳情頁率 > 50%
- **Out of scope**: 影片分析、地圖可視化

**Milestone 4: 越野跑爬坡分析**
- **User can now**: 訓練詳情頁自動偵測爬升 > 100m 的訓練，顯示：① 海拔 + GAP 配速疊圖 ② 坡度 vs 踏頻散點 ③ VAM 分段表 ④ 心率漂移（GAP 正規化後）；AI 自動生成爬坡效率摘要
- **Success signal**: AI 能正確解釋越野跑訓練的爬坡效率數據，用戶不需追問就能理解
- **Out of scope**: Minetti 精確 GAP 公式（MVP 用線性近似），地圖可視化，騎車分析

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| AI 整合位置 | In-app Chat（網站內） | Claude Desktop MCP | 用戶不需要安裝桌面 app，體驗更流暢 |
| AI Provider | 兩者都支援：Claude API + OpenAI API | 固定單一 provider | 用戶偏好，不鎖定單一廠商 |
| API Key 管理 | 設定頁 UI 輸入，存於 DB | .env 環境變數 | 用戶明確選擇設定頁方式 |
| AI 回應語言 | 固定中文（system prompt 指定） | 自動偵測語言 | 用戶只使用中文，固定更穩定 |
| GAP 公式 | 線性近似（MVP），預留升級 Minetti | 直接用 Minetti | 線性近似誤差 < 5%，實作快；Minetti 準確但計算重 |
| 圖表簡化策略 | 新增 Smart Dashboard，舊圖表保留在 Season tab | 刪掉舊圖表 | 已有功能不刪，但引導用戶從簡化版進入 |
| 運動優先級 | 跑步 / 越野跑優先，騎車架構預留 | 同步支援 | 用戶無騎車資料，避免過度設計 |
| 越野跑偵測 | 爬升 > 100m 自動觸發越野分析模式 | 手動標記 | 自動偵測減少用戶操作摩擦 |

---

## Research Summary

**Technical Context**
- 後端：FastAPI + SQLAlchemy + SQLite，已有 `/api/v1/analytics/*` 和 `/api/v1/workouts/*`
- 資料已計算：NP、TSS、IF、CTL/ATL/TSB、MMP curve、weekly/monthly volume
- MCP server 工具層已存在（`backend/mcp_server`），可複用 tool definitions 給 AI API endpoint
- 前端：React + TypeScript + Recharts，charts/ 資料夾有 8 個圖表元件
- AI Chat 需新增：`POST /api/v1/ai/chat` endpoint + 前端 `<AiChat />` 元件
- 設定頁（ConfigTab）已存在，可新增 API key 輸入欄位

**Market Context**
- WKO5：功能強大但無 AI、學習曲線陡峭（用戶的直接痛點）
- TrainingPeaks：有 AI 功能但訂閱費高，且圖表也複雜
- Garmin Coach / Coros Training Hub：AI 建議但資料淺，無深度分析
- **差異化機會**：自建 + 自備 API Key = 零訂閱費 + 完全個人化資料存取

---

*Generated: 2026-05-16*
*Status: DRAFT - needs validation*
*Source Linear Issue: N/A — standalone*
