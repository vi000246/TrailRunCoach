---
linear_issue: null
---
# SRS: AI 教練知識驅動處方（狀態判讀 + zone/間歇）

## Metadata
- **Module**: `ai-coach`
- **Module Spec**: `docs/spec/ai-coach-training-analysis.spec.md`
- **Source PRD**: `docs/prd/wko5-trail-multipage-sync-coach.prd.md`（Milestone 4）
- **Source Linear Issue**: N/A
- **Created**: 2026-06-13
- **Grill level**: 1

## Feature Summary

把 AI 教練從通用越野教練升級為「知識驅動處方」：注入使用者 筆記的 curated 訓練知識（Palladino 個人化功率區間、CP 測試、間歇模板、越野 PI/爬升概念），在 context 中加入由設定計算的功率/心率 zone 邊界與越野負荷，並把系統提示改為輸出「目前訓練狀態判讀 + 該練 zone 幾 + 間歇處方（時間/瓦數）」。

## Delta from Current Module State

> 既有架構詳見 `docs/spec/ai-coach-training-analysis.spec.md`。本節只描述變更。現有 chat SSE、`build_context`、`SYSTEM_PROMPT` 皆已存在。

### New / Changed API Endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/ai/zones?athlete_id=` | 回計算後的功率/心率 zone 邊界（依 rFTP/LTHR + Palladino 模型）+ 來源說明 | athlete |
| POST | `/api/v1/ai/chat`（既有，行為強化） | 沿用既有 SSE chat，但 context 含 zone+知識、system prompt 走處方導向 | athlete |

> 不新增 chat 端點——處方透過既有 chat + 前端 quick-prompt（「今天該練什麼」「我的間歇怎麼排」）觸發。

### New / Changed Data Models

無 schema 變更。zone 由 `AthleteSettings`（run_ftp_w/ftp_w/lthr/threshold_pace）即時計算；知識為靜態程式碼模組。

### Changed Business Logic

- **新增** `backend/engine/ai/knowledge.py`：curated 訓練知識常數（Palladino 區間定義與訓練目的、CP 測試協定、間歇模板、越野/爬升概念、使用者 profile 事實如 CP/TTE/Trail PI），組成知識區塊。
- **新增** zone 計算（功率以 rFTP × Palladino %、心率以 LTHR × %，沿用 `workouts.py` 既有 `POWER_ZONES_DEF`/`HR_ZONES_DEF` 帶權），輸出每區的 W/bpm 範圍。
- **改** `build_context`：加入 zone 邊界與越野負荷摘要（hrTSS/trail summary）。
- **改** `SYSTEM_PROMPT`：注入知識區塊 + 指示輸出「狀態判讀 → 建議 zone → 間歇處方（時間×瓦數/組數）」。

### Explicitly Out of Scope

- RAG / 向量檢索整個 notes vault（本次採 curated 知識；筆記更新需手動同步知識模組）。
- 對話歷史持久化（既有 spec 已排除）。
- 自動排課 / 寫入行事曆。
- 重新設計 chat UI（只加 quick-prompt 按鈕）。

## Functional Requirements

- [ ] FR1：`/ai/zones` 回功率與心率 zone 邊界（具體 W/bpm），依使用者 rFTP/LTHR 計算。
- [ ] FR2：AI 教練 context 含 zone 邊界與越野負荷，使回覆能引用具體區間數值。
- [ ] FR3：系統提示注入 curated 知識（Palladino 區間、間歇模板、CP、越野概念、使用者 profile）。
- [ ] FR4：AI 教練回覆能輸出「目前狀態判讀 + 建議 zone + 間歇處方（時間/瓦數）」。
- [ ] FR5：前端 AI 頁加 quick-prompt 按鈕（「今天該練什麼」「我的間歇怎麼排」「現在該做 zone 幾」）。

## Non-Functional Requirements

| Category | Target | How Achieved |
|---|---|---|
| Determinism | zone 計算純函式可測、無 LLM | 從設定算邊界，單元測試 |
| Cost | 知識為靜態注入，無額外 API 呼叫 | curated 常數，非 RAG |
| Correctness | zone 邊界符合 Palladino %、與既有 zone 定義一致 | 沿用 workouts.py 既有 zone def |
| Latency | chat 首 token 與現況相當（context 增量小） | 知識區塊精簡、context 只加摘要 |

## Architecture Notes

採 curated 知識（靜態程式碼）而非 RAG——單一使用者、知識穩定、零成本、可離線、可測。zone 計算沿用既有 `POWER_ZONES_DEF`/`HR_ZONES_DEF`（`workouts.py`）避免兩套定義。處方能力靠 context（具體 zone 數值 + 負荷）+ system prompt 指示達成，不需新模型或工具。詳見 `docs/spec/ai-coach-training-analysis.spec.md`。

## Acceptance Criteria

### AC-1: zone 計算端點
- **Given**: 使用者設定 run_ftp_w=220、lthr=160（範例跑者）
- **When**: 呼叫 `GET /api/v1/ai/zones?athlete_id=1`
- **Then**: 回 7 個功率區與 5 個心率區，每區有 name 與 low/high 的 W/bpm 邊界（如 zone4 threshold ≈ 0.90–1.05×220W）
- **Test**: `backend/tests/test_ai_zones.py::test_zones_from_settings`

### AC-2: 知識注入 system prompt
- **Given**: knowledge 模組存在
- **When**: 組裝 AI 請求
- **Then**: system prompt 含 Palladino 區間目的、間歇模板、使用者 profile（可斷言關鍵字如「無氧」「間歇」「Palladino」出現）
- **Test**: `backend/tests/test_ai_knowledge.py::test_system_prompt_includes_knowledge`

### AC-3: context 含 zone 與越野負荷
- **Given**: 有設定與 PMC 資料
- **When**: `build_context`
- **Then**: 回傳文字含功率/心率 zone 邊界與越野負荷摘要
- **Test**: `backend/tests/test_ai_context.py::test_context_includes_zones`

### AC-4: 前端 quick-prompt
- **Given**: 開啟 AI 頁
- **When**: 點「今天該練什麼」
- **Then**: 以該提示送出 chat 請求
- **Test**: 前端視覺驗證（無框架，build + 手測）

## Open Questions

- [ ] Palladino 區間 % 是否完全沿用 `workouts.py` 既有 7 區（Coggan-style），或筆記有自訂個人化邊界？初版沿用既有 def，筆記若有特例再覆寫。
- [ ] 使用者 profile 事實（CP 等）放知識模組常數，未來變動需手動更新——是否改讀 settings？CP 與 rFTP 關係待確認。
- [ ] 間歇處方的 RWC（無氧儲備）個人化參數是否要納入計算，還是交給 LLM 依知識文字判斷？初版交 LLM。
