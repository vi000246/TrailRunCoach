# SRS: AI Coach — 訓練分析與圖表理解

> ⛔ **CANCELED（2026-10-04）**：AI 教練（對話、Smart Dashboard、圖表解讀號誌、單次越野分析）隨 React SPA 一起取消並刪除：`backend/api/ai.py`、`backend/engine/ai/`、`/api/v1/analytics/*`、`/api/v1/workouts/{id}/trail`、`frontend/`。日後若重做，會以 static 頁新版設計另開規格。本文是當時的設計紀錄。

## Metadata
- **Source PRDs**:
  - `docs/prd/ai-coach-training-analysis.prd.md` — initial（chat/dashboard/trail analysis）
  - `docs/prd/wko5-trail-multipage-sync-coach.prd.md` — Milestone 4（知識驅動處方）
- **Source Linear Issue**: N/A
- **Owner**: maintainer
- **Status**: ACTIVE — 已實作（含 M4 知識驅動處方）；本文件與程式碼同步
- **Generated**: 2026-05-16
- **Last Updated**: 2026-10-04

## Change History

| Date | Source PRD | Feature SRS | Summary |
|------|------------|-------------|---------|
| 2026-05-16 | `ai-coach-training-analysis.prd.md` | (initial) | Created — In-app AI chat、Smart Dashboard、Trail Analysis |
| 2026-06-13 | `wko5-trail-multipage-sync-coach.prd.md` | `docs/srs/ai-coach-knowledge-driven-prescription.srs.md` | 知識驅動處方：curated 知識模組 + zone 計算 + 處方導向 system prompt + `/ai/zones` 端點 |
| 2026-10-04 | code-sync | N/A | 對齊實作：Gemini provider + `/ai/models`、chat 改單輪 `{message}` + `chunk` SSE、運動員特性區塊（由本人資料算）、Palladino 功率區間、資料來源去重、trail 回應形狀與 Pa:HR 解耦、dashboard-summary 移至總體頁且吃 sports 篩選；移除 workout_id 情境、is_hiking；補 Domain Model |

## Summary

在現有 FastAPI + React 訓練分析網站上新增三個系統：
(1) **In-app AI Coach Chat** — 後端組裝訓練 context（含 curated 訓練知識、功率/心率區間、由運動員本人資料算出的特性）後呼叫 Claude / OpenAI / Gemini API，以 SSE 串流回應，前端呈現對話 UI；
(2) **Smart Dashboard** — 新 analytics endpoint 彙整今日 TSB 狀態與近 7 天負荷，前端以 5 張狀態卡呈現（位於 React 總體頁頂部，吃該頁 sports 篩選）；
(3) **Trail Analysis** — 新 workouts trail endpoint 從 FIT 即時計算 GAP、VAM、心率解耦（Pa:HR），前端在訓練詳情頁自動展示越野跑圖表組。
所有新功能沿用現有 FastAPI router / SQLAlchemy async / React Query / Recharts 模式，不引入新框架。

> **部署範圍（2026-10-04）**：AI router 屬 owner-only，demo 模式不掛載（`backend/main.py:126-146`）。AI Chat / Smart Dashboard / Trail Analysis 的 UI 只存在於 React SPA（`frontend/`，dev 5173 或 Docker build 出 `frontend/dist` 時才由後端提供）；後端未提供 `frontend/dist` 時 `/` 轉向 server-rendered 的總覽頁（`backend/main.py:176-199`），該套 static 頁面不含 AI 對話。

---

## Domain Model

### Bounded Context
- **Context Name**: AiCoaching（AI 教練與訓練解讀）
- **Domain Layer**: Supporting Domain
- **Parent Module**: N/A（讀取 `wko5-engine` 的 PMC / WorkoutMetric、`sport-pages` 的 trail 分類、季計畫門檻 `backend/engine/planning.py`）

### Ubiquitous Language
| Term | Definition |
|------|-----------|
| AI provider | `claude` / `openai` / `gemini`；存在 `AthleteSettings.ai_provider`，Gemini 走 Google 的 OpenAI 相容端點 |
| 訓練 context | 每次提問時重建的文字區塊：門檻設定、最新 PMC、近 7 天訓練、功率/心率區間（`build_context`） |
| 知識庫 (KNOWLEDGE_BLOCK) | 靜態、無 RAG 的訓練知識（Palladino 區間、間歇模板、越野負荷、TSB 判讀），併入 system prompt |
| 運動員特性 (athlete traits) | 由本人資料算出的 CP、W′/CP 型態、LTHR/AeT、近 90 天越野每小時爬升；無資料時整段省略 |
| 處方 | system prompt 要求的輸出：狀態判讀 → 建議 zone（W / bpm）→ 間歇（時間 × 目標 × 組數 × 恢復） |
| GAP | 坡度調整配速（Strava 線性近似） |
| VAM | 爬坡段垂直速度（m/hr） |
| 解耦 (decoupling, Pa:HR) | 前後半「速度/心率」比的下降百分比；正值＝漂移，< 5% 視為通過 |
| tsb_state | dashboard-summary 的 4 態：`fresh` / `optimal` / `tired` / `overreached` |

### Domain Events
N/A — 讀取/解讀導向；對話不持久化，不對外發事件。

---

## System Context

### Scope & Boundaries
- **In scope**: AI chat API + SSE 串流、AI provider 設定 UI（key + model 選擇）、訓練知識 + zone 計算 + 運動員特性注入、`/ai/zones`、dashboard summary endpoint、trail analysis endpoint、越野跑圖表元件、Smart Dashboard section
- **Out of scope**: 對話歷史持久化（不存 DB）、多輪對話 context（後端每次只送單一問題，見 Data Flow）、針對單一訓練提問（原設計的 `workout_id` 未實作）、地圖可視化、騎車專屬圖表、推送通知、多運動員切換、demo 模式

### Actors
| Actor | Type | Interaction |
|---|---|---|
| 運動員（使用者） | Human | 瀏覽 Dashboard、開啟 AI Chat、查看訓練詳情 |
| Claude API | External Service | 接收 messages + system prompt，回傳 SSE token 串流 |
| OpenAI API | External Service | 同上，另一 provider 選項 |
| Gemini API | External Service | 同上，經 Google 的 OpenAI 相容端點 |
| FIT 檔案（COROS / TrainingPeaks 同步） | File System | Trail endpoint 讀取 FIT 計算 GAP/VAM |

### External Dependencies
| Dependency | Purpose | Failure Mode |
|---|---|---|
| Claude API (`api.anthropic.com`) | AI 回應（provider `claude`） | 串流中例外 → SSE 送 `{"error": "<例外訊息>"}`，前端顯示於對話 |
| OpenAI API (`api.openai.com`) | AI 回應（provider `openai`） | 同上 |
| Gemini API（`generativelanguage.googleapis.com` 的 OpenAI 相容端點） | AI 回應（provider `gemini`，沿用 `openai` SDK） | 同上 |
| FIT 檔案（`WorkoutFile.file_path`，同步資料目錄） | Trail 計算原始資料 | 非 FIT → 422 `NO_FIT_FILE`；前端 `TrailAnalysisSection` 於錯誤時不渲染 |
| 資料來源去重（`backend/sync/dedup.py`） | context / dashboard 只算「使用中」資料來源的活動 | — |

---

## Architecture

### High-Level Diagram
```
Browser (React SPA)
  ├── AiPage → AiChat (zustand useAiChatStore)
  │     └── fetch + ReadableStream → POST /api/v1/ai/chat (SSE)
  ├── OverviewPage → SmartDashboardSection
  │     └── useQuery → GET /api/v1/analytics/dashboard-summary?sports=
  ├── ConfigPage (AI 設定區塊) → GET /api/v1/ai/models, GET /api/v1/ai/status/{id}
  └── ActivityDetailPage
        └── useQuery → GET /api/v1/workouts/{id}/trail

FastAPI
  ├── api/ai.py          (新) chat / status / models / zones routes（owner-only）
  ├── api/analytics.py   (擴充) + dashboard-summary route
  ├── api/workouts.py    (擴充) + trail route
  ├── engine/ai/
  │     ├── context.py   (新) 訓練 context + system prompt（含運動員特性）
  │     ├── knowledge.py (新) 靜態訓練知識 + athlete_traits()
  │     ├── zones.py     (新) 功率（Palladino）/ 心率區間邊界
  │     └── client.py    (新) Claude / OpenAI / Gemini client + factory
  └── engine/algorithms/
        └── trail.py     (新) grade / GAP / climbs / VAM / HR decoupling

SQLite (AthleteSettings)
  └── 新增 ai_provider, ai_api_key, ai_model 欄位
```

### Components
| Component | Responsibility | Interface |
|---|---|---|
| `api/ai.py` | 驗證設定、組裝 context + system prompt、呼叫 client、串流 SSE；列模型；算 zones | `POST /api/v1/ai/chat`, `GET /api/v1/ai/status/{athlete_id}`, `GET /api/v1/ai/models`, `GET /api/v1/ai/zones`（`backend/api/ai.py:23-87`） |
| `engine/ai/context.py` | 查 DB 組裝訓練 context；system prompt = `SYSTEM_PROMPT` + 運動員特性 | `async build_context(db, athlete_id) -> str`、`async build_system_prompt(db, athlete_id, plan=None) -> str`（`backend/engine/ai/context.py:67-160`） |
| `engine/ai/knowledge.py` | 靜態知識區塊 + 由本人數字產生「運動員特性」 | `build_knowledge()`、`athlete_traits(**kw) -> str`（`backend/engine/ai/knowledge.py:26-69`） |
| `engine/ai/zones.py` | 功率（Palladino % CP）/ 心率（Friel % LTHR，5 區）邊界 | `compute_zones(run_ftp_w, lthr) -> {power, hr}`（`backend/engine/ai/zones.py:11-42`） |
| `engine/ai/client.py` | AI provider 抽象層（duck typing，無 Protocol class） | `get_ai_client(provider, api_key, model)`（`backend/engine/ai/client.py:109-116`） |
| `engine/algorithms/trail.py` | grade / GAP / climb 分段 / VAM / HR 解耦 | pure functions，input: numpy arrays |
| `api/workouts.py` (trail route) | 讀 FIT → 呼叫 trail.py → 回傳 JSON | `GET /api/v1/workouts/{id}/trail`（`backend/api/workouts.py:425-507`） |
| `api/analytics.py` (dashboard-summary) | 彙整 TSB + 近 7 天負荷，可依 sports 篩選 | `GET /api/v1/analytics/dashboard-summary`（`backend/api/analytics.py:32-113`） |
| `AiChat.tsx` | 對話 UI，消費 SSE 串流，3 個快捷問題 | zustand `useAiChatStore` |
| `TrailAnalysisSection.tsx` | 4 張越野跑卡片，條件顯示 | props: `workoutId`（自行 `useTrailAnalysis`） |
| `SmartDashboardSection.tsx` | 5 張狀態卡 + 「Ask AI Coach →」 | props: `athleteId?`, `sports?`；useQuery `dashboard-summary` |

### Data Flow

**AI Chat（主要路徑）：**
1. 前端 `AiChat` 送 `POST /api/v1/ai/chat` `{athlete_id, message}`（不送歷史）
2. `api/ai.py` 取最新 `AthleteSettings`；無 provider 或 key → HTTP 400 `AI_NOT_CONFIGURED`
3. `build_context()` 查 DB：門檻設定（FTP/LTHR/閾值配速）+ 近 42 天最新一筆 PMC + 近 7 天訓練（只算使用中資料來源）與週 TSS/時數 + 功率/心率區間
4. `build_system_prompt()`：`SYSTEM_PROMPT`（含知識庫）+ `athlete_traits()`（季計畫最新 CP / W′ / LTHR / AeT，缺則退回設定；近 90 天 trail 活動的每小時爬升與平均心率）
5. user 內容 = context 文字 + `=== 問題 ===` + message；呼叫 `client.stream(system=, user=)`（單輪，`ChatRequest.history` 欄位存在但未使用）
6. `EventSourceResponse` 逐段送 `data: {"chunk":"..."}`，結尾送 `data: {"done":true}`；串流例外送 `data: {"error":"..."}`
7. 前端以 `fetch` + `ReadableStream` 解析 `data:` 行，`appendAssistantChunk` 逐段 append

**Trail Analysis：**
1. `ActivityDetailPage` 若 `metrics.elevation_gain_m > 100` 或 `sport === 'trail_running'` → 渲染 `TrailAnalysisSection`（`frontend/src/pages/ActivityDetailPage.tsx:41`）
2. `GET /workouts/{id}/trail` → 讀 FIT → `compute_grade()` + `compute_gap()` + `segment_climbs()` + `compute_vam()` + `compute_hr_drift()`
3. 回傳 JSON，前端 `TrailAnalysisSection` 渲染 4 張卡片；API 錯誤或 `is_trail` 為假時整段不渲染

### Sequence Diagram — AI Chat

```
User          AiChat.tsx        POST /ai/chat      context.py       AIClient
  |               |                   |                 |               |
  |──送訊息──────>|                   |                 |               |
  |               |──POST {msgs}────>|                 |               |
  |               |                   |──build_context─>|               |
  |               |                   |<─system_prompt──|               |
  |               |                   |──stream()───────────────────────>|
  |               |                   |<─token "你"─────────────────────|
  |               |<─SSE token────────|                 |               |
  |<─字元出現─────|                   |<─token "的"─────────────────────|
  |               |<─SSE token────────|                 |               |
  ...（持續串流）
  |               |<─SSE {done:true}──|                 |               |
  |<─完整回應─────|                   |                 |               |
```

---

## Data Model

### Entities
| Entity | Owner | Lifecycle |
|---|---|---|
| `AthleteSettings` | `athletes` module | 每次更新設定時 upsert（現有） |
| Chat messages | Frontend only | React state / zustand，不持久化 |

### Schema — AthleteSettings 新增欄位

```sql
-- 沿用 _migrate_schema() 模式於 database.py
ALTER TABLE athlete_settings ADD COLUMN ai_provider TEXT;     -- 'claude' | 'openai' | 'gemini'
ALTER TABLE athlete_settings ADD COLUMN ai_api_key  TEXT;     -- 純文字 API key
ALTER TABLE athlete_settings ADD COLUMN ai_model    TEXT;     -- e.g. 'claude-sonnet-4-6'
```
（實作：`backend/db/database.py:129-131`）

對應 SQLAlchemy model（`backend/db/models.py:35-37`）`AthleteSettings` 新增：
```
ai_provider: Mapped[Optional[str]]
ai_api_key:  Mapped[Optional[str]]
ai_model:    Mapped[Optional[str]]
```

### Migration Strategy
- **Forward**: `_migrate_schema()` 加入三行 ALTER TABLE，`init_db()` 啟動時執行
- **Backward**: 欄位可為 NULL，舊版 app 忽略未知欄位，無破壞性
- **Backfill**: 不需要（新欄位 default NULL，用戶在設定頁填入）
- **Coexistence**: 舊 row 三欄均 NULL → `/ai/status/{id}` 回 `{"configured": false}`、`/ai/chat` 回 400 `AI_NOT_CONFIGURED`；有 provider 無 model 時 chat 以預設模型補（claude → `CLAUDE_MODELS[1]`，其餘 → `OPENAI_MODELS[0]`，見 Open Questions）

---

## API Contracts

### Endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/ai/chat` | AI 對話（單輪），SSE 串流回應 | none（本地 app；demo 不掛載）|
| GET | `/api/v1/ai/status/{athlete_id}` | 確認 AI provider + key 是否已設定 | none |
| GET | `/api/v1/ai/models` | 各 provider 可選模型清單 | none |
| GET | `/api/v1/ai/zones?athlete_id=` | 依設定算出的功率 / 心率區間邊界 | none |
| GET | `/api/v1/workouts/{id}/trail` | 越野跑分析資料 | none |
| GET | `/api/v1/analytics/dashboard-summary?athlete_id=&sports=` | Smart Dashboard 彙整資料 | none |

### Request / Response Shape

#### POST /api/v1/ai/chat
```json
// Request（ChatRequest；history 有預設 []，後端未使用）
{
  "athlete_id": 1,
  "message": "今天該練什麼？"
}

// Response — 200 OK, Content-Type: text/event-stream
data: {"chunk": "目前"}
data: {"chunk": " TSB 為 -8"}
data: {"done": true}

// 串流中例外（仍是 200 stream）
data: {"error": "<例外訊息>"}

// 未設定 provider / key — HTTP 400（非 SSE）
{"detail": "AI_NOT_CONFIGURED"}
```

#### GET /api/v1/ai/status/{athlete_id}
```json
// 200 — key 已設定
{ "configured": true, "provider": "claude", "model": "claude-sonnet-4-6" }

// 200 — 未設定
{ "configured": false }
```

#### GET /api/v1/ai/models
```json
// 200 — 清單來自 backend/engine/ai/client.py 的常數
{ "claude": ["claude-opus-4-7", "..."], "openai": ["gpt-4o", "..."], "gemini": ["gemini-2.0-flash", "..."] }
```

#### GET /api/v1/ai/zones
```json
// 200 — run_ftp_w 取 run_ftp_w 或 ftp_w；缺值時對應陣列為空
{
  "athlete_id": 1, "run_ftp_w": 250.0, "lthr": 170,
  "power": [{"zone": 1, "name": "<palladino 名稱>", "low_w": 0.0, "high_w": "<run_ftp_w × hi>"}, "..."],
  "hr":    [{"zone": 1, "name": "Recovery", "low_bpm": 0.0, "high_bpm": 144.5}, "..."]
}
```
（數值為示意；功率區間名稱與比例來自 `palladino_rows()`，最高區 `high_*` 為 null。）

#### GET /api/v1/workouts/{id}/trail
```json
// 200 OK
{
  "workout_id": 42,
  "is_trail": true,
  "series": [
    {"t": 0,  "dist_m": 0.0,  "alt_m": 1200.0, "grade_pct": 0.0,  "pace_s_km": 360.0, "gap_s_km": 360.0, "hr": 138, "cadence": 89},
    {"t": 10, "dist_m": 42.0, "alt_m": 1205.0, "grade_pct": 11.9, "pace_s_km": 238.0, "gap_s_km": 170.0, "hr": 142, "cadence": 86}
  ],
  "climb_segments": [
    {"start_m": 1200.0, "end_m": 3800.0, "gain_m": 240.0, "distance_m": 2600.0, "grade_pct": 9.2,
     "start_idx": 120, "end_idx": 410, "duration_s": 1660, "vam": 520}
  ],
  "hr_drift": {
    "hr_first_half": 145.0, "hr_second_half": 151.0,
    "gap_first_half_s_per_km": 390.0, "gap_second_half_s_per_km": 398.0,
    "decoupling_pct": 5.8
  },
  "grade_cadence": [{"grade_pct": 11.9, "cadence": 86}],
  "total_gain_m": 650.0
}
```
- `series` 降採樣至 ≤ ~1800 點；`hr` / `cadence` 只在 FIT 有對應長度的陣列時出現。
- `grade_cadence` 降採樣至 ~500 點，只收 cadence > 0。
- `hr_drift` 在無 HR 或樣本 < 60 時為 `null`。
- 無 `gap_tss`、`is_hiking`、`avg_hr` / `avg_cadence`（原設計未實作）。

```json
// 422
{"detail": "NO_FIT_FILE"}            // file_format != "fit"
{"detail": "PARSE_ERROR: <msg>"}     // FIT 解析失敗
{"detail": "NO_TRAIL_DATA"}          // 無高度或無距離
```

#### GET /api/v1/analytics/dashboard-summary
```json
// 200 OK（sports 省略 = 全部運動；週數字只算使用中資料來源）
{
  "tsb": -8.3,
  "tsb_state": "optimal",
  "ctl": 52.1,
  "ctl_trend": 0.4,
  "weekly_tss": 312,
  "weekly_hours": 6.5,
  "weekly_count": 5,
  "last_workout": { "id": 101, "date": "2026-05-15", "sport": "running", "duration_s": 5400.0 }
}
```
- `tsb` / `ctl` 取 `PmcCache` 最新一列（全運動，不受 sports 影響）；`ctl_trend` = 最新兩列 CTL 差值（數字，非 rising/falling）。
- 「週」= 今天往回 7 天（`today_local()`），非日曆週。
- `tsb_state`：TSB > 5 `fresh`、> -10 `optimal`、> -25 `tired`、否則 `overreached`。

### Error Codes
| Code | HTTP Status | Meaning | Caller Action |
|---|---|---|---|
| `AI_NOT_CONFIGURED` | 400 | AI provider / key 未設定 | 導向設定頁 |
| （例外訊息字串） | 200 SSE `error` | 外部 API 或 SDK 失敗（含 SDK 未安裝） | 顯示 message，讓用戶重試 |
| `NO_FIT_FILE` | 422 | 該訓練非 FIT 檔 | 前端隱藏 trail section |
| `PARSE_ERROR: …` | 422 | FIT 解析失敗 | 前端隱藏 trail section |
| `NO_TRAIL_DATA` | 422 | FIT 無高度或距離資料 | 前端隱藏 trail section |
| `WORKOUT_NOT_FOUND` | 404 | 訓練 ID 不存在 | 前端 redirect |

### Versioning Strategy
所有新 endpoint 置於 `/api/v1/` 前綴下，與現有路由一致。本 app 無外部 consumer，不需 deprecation 計畫。

---

## Non-Functional Requirements

| Category | Target | Measurement | How Achieved |
|---|---|---|---|
| 首 token 延遲 | < 2s（Claude/OpenAI/Gemini 回傳第一 token）| 瀏覽器 DevTools Network | SSE streaming — 不等完整回應 |
| Trail 計算延遲 | < 1.5s for 2hr FIT（7200 samples）| local timing | numpy 向量運算，不用迴圈 |
| Dashboard summary | < 300ms | FastAPI log | PMC cache 已算好；週彙整與最近一筆各一個 query |
| API key 安全性 | 本地 SQLite，不暴露網路 | 架構 review | app 監聽 localhost only，CORS 限制 |
| Context 大小 | < 8k tokens | 估算 | 近 7 天訓練、最新一筆 PMC、區間表、固定知識區塊；回應 `max_tokens=2048` |

---

## Technology Choices

| Concern | Choice | Alternatives | Rationale |
|---|---|---|---|
| AI 串流協定 | SSE via `sse-starlette`（已在 requirements.txt）| WebSocket | 已有依賴，單向串流夠用 |
| Claude SDK | `anthropic` Python library | raw httpx | 官方 SDK 處理 retry/streaming |
| OpenAI SDK | `openai` Python library | raw httpx | 官方 SDK，streaming API 一致介面 |
| Gemini | `openai` SDK + `base_url` 指向 Google OpenAI 相容端點 | `google-genai` SDK | 不增依賴（`backend/engine/ai/client.py:25-27`） |
| SDK 載入 | 在 `stream()` 內 lazy import，缺套件時 raise RuntimeError | 模組頂層 import | 未裝 SDK 也能啟動 app |
| 訓練知識 | 靜態知識區塊（無 RAG）+ 由本人資料算的特性 | RAG / 寫死個人數字 | 穩定、零成本、可測；公開 repo 不含特定跑者數字 |
| AI provider 抽象 | duck typing（三個 class 都有 `stream(system, user)`，未宣告 Protocol）| ABC | 更輕量 |
| GAP 公式 | Strava 線性近似（MVP）| Minetti 曲線 | 誤差 < 5%，實作 3 行，可日後升級 |
| 前端 SSE 消費 | `fetch` + `ReadableStream`（非 `EventSource`）| `EventSource` | POST 請求 `EventSource` 不支援 request body |
| Chat state | `zustand` store（已有 `tabStore`）| `useState` | 跨元件共用，且頁面切換不丟失 |
| 前端 AI model 選單 | 原生 `<select>`，選項來自 `GET /ai/models`（`frontend/src/pages/ConfigPage.tsx:326-370`）| `@radix-ui/react-select` | 實作採原生 select |

---

## Integration Points

| Touchpoint | Type | Contract | Backwards Compat |
|---|---|---|---|
| `AthleteSettings.ai_*` | SQLAlchemy model + DB | 三欄 nullable，舊 row 均 NULL | Yes — nullable，無破壞 |
| `GET /athletes/{id}/settings` | HTTP JSON | response 新增 `ai_provider`, `ai_model`（不含 key）| Yes — additive only |
| `PUT /athletes/{id}/settings` | HTTP JSON | `SettingsUpdate` Pydantic model 新增三欄 optional | Yes — optional fields |
| `ActivityDetailPage` | React import | 新增 `useTrailAnalysis(id)` hook + `TrailAnalysisSection` | Yes — additive |
| `OverviewPage`（原 `SeasonTab`，已由 sport-pages 取代） | React import | `SmartDashboardSection sports={...}` 置於篩選器下方（`frontend/src/pages/OverviewPage.tsx:37`） | Yes — additive |
| `AiPage` | React | 完全重寫為 `AiChat` 全頁 + 「清除對話」（`frontend/src/pages/AiPage.tsx:4-22`）| Breaking — 可接受，功能取代 |
| `backend/main.py` owner-only | FastAPI 掛載 | `ai.router` 在 demo 模式不掛載 | — |

### Rollout Strategy
無 feature flag（本地 personal app）。直接部署。失敗回滾方式：`git revert` + 重啟 uvicorn。

---

## Codebase Patterns to Follow

| Pattern | Where to Find | Why Follow |
|---|---|---|
| FastAPI router 結構 | `backend/api/workouts.py:1-20` | `APIRouter(prefix=, tags=)` + `Depends(get_db)` |
| HTTPException 錯誤碼 | `backend/api/workouts.py:35-42` | `raise HTTPException(404, "SCREAMING_SNAKE")` |
| AthleteSettings upsert | `backend/api/athletes.py:124-170` | select-then-update-or-insert，`await db.commit()` |
| Schema forward migration | `backend/db/database.py:101`（`_migrate_schema()`） | PRAGMA table_info → ALTER TABLE if col missing |
| SSE 現有用法 | `backend/api/sync.py:78`（`_sse_sync`）| `EventSourceResponse` + async generator |
| React Query hook 模式 | `frontend/src/api/hooks.ts:useRunLoad()` | `useQuery({queryKey, queryFn})` |
| Chart 主題 | `frontend/src/lib/chartTheme.ts` | `CHART_COLORS`, `BASE_GRID_PROPS`, `BASE_TOOLTIP_STYLE` |
| Chart Card wrapper | `frontend/src/components/charts/TimeseriesChart.tsx:37-50` | `<Card><CardHeader>...<CardContent>` |
| 設定頁 input 表單 | `frontend/src/pages/ConfigPage.tsx:73-95`（`handleAiSubmit`；舊 `tabs/ConfigTab.tsx` 只被未掛路由的 `Dashboard.tsx` 引用） | FormData → `update.mutate({...})` |
| Timeseries 降採樣 | `backend/api/workouts.py:323-341`（`get_workout_timeseries()`） | `step = max(1, n//1800)`，回傳 ≤ 1800 點 |

---

## Trail Algorithm Design

`backend/engine/algorithms/trail.py`（新檔）

### compute_grade（`backend/engine/algorithms/trail.py:7-12`）
```
Input:  altitude (np.ndarray), distance (np.ndarray, 累積 m)
Output: grade_pct (np.ndarray)
Method: diff(altitude) / max(diff(distance), 0.1) * 100（逐樣本，無平滑、無 clip）
```

### compute_gap（`backend/engine/algorithms/trail.py:15-22`）
```
Input:  pace_s_per_m (np.ndarray), grade_pct (np.ndarray)
Output: gap_s_per_m (np.ndarray)
Method:
  uphill   (grade >= 0): factor = 1 + 0.033 * grade_pct
  downhill (grade < 0):  factor = max(0.5, 1 + 0.015 * grade_pct)
  GAP = pace / factor
```
pace 由 FIT `speed_ms`（下限 0.1 m/s）換算；無速度時用距離/時間差（trail route 內處理）。

### segment_climbs + compute_vam（`backend/engine/algorithms/trail.py:25-79`）
```
segment_climbs(altitude, distance, min_gain_m=30.0, min_grade_pct=3.0) -> list[dict]
  1. grade >= min_grade_pct 為「上坡」樣本
  2. 連續上坡樣本且爬升 >= min_gain_m 為一段
  3. 每段回 start_m / end_m / gain_m / distance_m / grade_pct / start_idx / end_idx
compute_vam(segments, time) -> list[dict]
  每段加 duration_s 與 vam = gain_m / duration_s * 3600
```
原設計的 `is_hiking`（踏頻 < 155 spm）與每段 avg HR / cadence **未實作**。

### compute_hr_drift（`backend/engine/algorithms/trail.py:82-124`）
```
Input:  hr (np.ndarray), gap_s_per_m (np.ndarray)
Output: {hr_first_half, hr_second_half, gap_first_half_s_per_km, gap_second_half_s_per_km, decoupling_pct} | None
Method（Pa:HR 有氧解耦，TrainingPeaks / Uphill Athlete 慣例）:
  依樣本數對半切；ratio = (1 / mean GAP) / mean HR（每心跳速度）
  decoupling_pct = (ratio_first - ratio_second) / ratio_first * 100
  正值 = 漂移；< 5% 視為通過；樣本 < 60 或任一均值 <= 0 → None
```

---

## AI Context Assembly Design

`backend/engine/ai/context.py` + `backend/engine/ai/knowledge.py`

### System Prompt（`backend/engine/ai/context.py:17-26`）
```
越野跑教練 AI 助理，一律繁體中文
+ 知識庫 KNOWLEDGE_BLOCK（Palladino 7 區功率、CP / TTE、VO2max / 無氧 / 閾值間歇模板、
  越野以 hrTSS 為主負荷 + VAM + GAP、無功率計改用心率或配速處方、
  TSB 判讀帶取自 backend/engine/status.py，與總覽一致、ACWR > 1.5 高風險）
+ 回覆結構：(1) 狀態判讀（TSB/CTL/ATL）(2) 建議 zone（附 W 或 bpm）(3) 間歇處方
+ [有資料時] 運動員特性區塊（build_system_prompt）
```

### 運動員特性（`backend/engine/ai/knowledge.py:44-69`，輸入見 `backend/engine/ai/context.py:31-64`）
```
=== 運動員特性（由本人資料算出） ===
- CP（跑步臨界功率）：{cp} W
- W′ {kJ}（W′/CP {s} 秒）：偏無氧型 / 偏有氧型 / 有氧／無氧均衡（推估；依性別先驗 mean ± 0.5 SD）
- LTHR：{lthr} bpm；AeT 心率：{aethr} bpm
- 越野跑：近 90 天 {n} 次，平均每小時爬升 {m} m @ 平均心率 {hr}
```
來源優先序：季計畫（`Plan.load()`）中今天有效的 CP / LTHR / AeT 與最近一次兩點測出的 W′ → 缺則退回 `AthleteSettings`（run_ftp_w / lthr）；性別取季計畫 profile。全無資料時整段省略。

### Training Context Block（`build_context`，作為 user 內容前綴；`backend/engine/ai/context.py:74-160`）
```
=== 運動員訓練數據 ===
FTP (跑步功率): {run_ftp_w or ftp_w} W
LTHR: {lthr} bpm
閾值配速: {m:ss} /km

=== 訓練狀態 (最新: {date}) ===        ← 近 42 天 PmcCache 最新一列
CTL (體能) / ATL (疲勞) / TSB (狀態)

=== 近7天訓練 ({n} 次) ===              ← 僅使用中資料來源（dedup.in_use）
- {date} [{sport}] {h}h
週TSS合計: {tss}, 訓練時數: {h}h

=== 功率區間 (W) === / === 心率區間 (bpm) ===   ← compute_zones()
```
原設計的「最近 10 次訓練」「MMP 峰值」「體重」「本次訓練 #workout_id」**未實作 / 已移除**。

### TSB 狀態判讀規則
兩套並存（見 Open Questions）：
- **dashboard-summary / chart-interpretation**（`backend/api/analytics.py:52-60`、`backend/engine/algorithms/interpret.py:10-33`）：> 5 `fresh` 新鮮、> -10 `optimal` 最佳、> -25 `tired` 疲勞、否則 `overreached` 過度訓練。
- **AI system prompt**：取 `backend/engine/status.py:48-51` 的 `TSB_A` / `TSB_PRODUCTIVE` / `TSB_OVERREACH` / `TSB_STALE`（與總覽頁同一套）。
原設計的 `peak_form` / `training` 兩態未實作。

---

## AI Client Abstraction

`backend/engine/ai/client.py`（新檔）

### 介面（duck typing）
```
async def stream(system: str, user: str) -> AsyncIterator[str]   # 單一 user 訊息
```

### ClaudeClient
```
Uses: anthropic SDK（lazy import）
Stream: AsyncAnthropic(api_key).messages.stream(model, max_tokens=2048, system, messages=[user])
```

### OpenAIClient / GeminiClient
```
Uses: openai SDK（lazy import）；Gemini 另設 base_url = Google OpenAI 相容端點
Stream: AsyncOpenAI(...).chat.completions.create(model, max_tokens=2048, stream=True, messages=[system, user])
```

### Factory（`backend/engine/ai/client.py:109-116`）
```
get_ai_client(provider, api_key, model) -> ClaudeClient | OpenAIClient | GeminiClient
未知 provider → raise ValueError（設定檢查在 api/ai.py 先做）
```

### 可選模型（`backend/engine/ai/client.py:5-23`，經 `GET /ai/models` 供 UI 選單）
```
Claude: claude-opus-4-7, claude-sonnet-4-6, claude-haiku-4-5-20251001
OpenAI: gpt-4o, gpt-4o-mini, gpt-4-turbo, o1-mini
Gemini: gemini-2.0-flash, gemini-2.0-flash-lite, gemini-1.5-pro, gemini-1.5-flash
```

---

## Frontend Component Design

### AiChat.tsx（新，`frontend/src/components/AiChat.tsx:10-143`）
- 位置：`/ai` 路由的 `AiPage`（標題「AI 教練」+「清除對話」按鈕）
- zustand store `useAiChatStore`：`messages`, `isStreaming`, `addUserMessage` / `startStreaming` / `appendAssistantChunk` / `finalizeAssistant` / `setError` / `clear`（無 `currentWorkoutId`）
- 未設定時（`/ai/status` 的 `configured` 為假）顯示「尚未設定 AI Coach，請在『設定』頁面輸入 API Key」
- SSE 消費：`fetch('/api/v1/ai/chat', {method:'POST', body:{athlete_id, message}})` → `getReader()` → 解析 `data: {...}` 行的 `chunk` / `done` / `error`
- 快捷問題按鈕（`frontend/src/components/AiChat.tsx:21`）：「今天該練什麼？」、「我的間歇怎麼排？」、「現在該做 zone 幾？」
- Enter 送出、Shift+Enter 換行

### ConfigPage AI 設定區塊（`frontend/src/pages/ConfigPage.tsx:326-370`）
- Provider 原生 select：Gemini (Google)（預設）/ Claude (Anthropic) / OpenAI
- Model select：選項來自 `useAiModels()`（`GET /ai/models`），依 provider 切換
- API Key 輸入（type="password"）
- 儲存：`PUT /athletes/{id}/settings` 帶 `ai_provider` / `ai_api_key` / `ai_model`；GET settings 回 provider / model，不回 key

### SmartDashboardSection.tsx（新，置於 OverviewPage）
```
5 張 StatCard：
  狀態 (TSB) {value}  標籤：{tsb_state 中文：新鮮 / 最佳 / 疲勞 / 過度訓練}
  體能 (CTL) {value}  ctl_trend
  週 TSS {weekly_tss}
  週訓練時間 {weekly_hours}
  最近訓練 {sport} {date}

「Ask AI Coach →」按鈕 → navigate('/ai')
```
props `sports` 由 OverviewPage 的運動篩選帶入（`frontend/src/components/charts/SmartDashboardSection.tsx:46-48`）。

### TrailAnalysisSection.tsx（新，置於 ActivityDetailPage）
- 條件顯示：`metrics.elevation_gain_m > 100` 或 `sport === 'trail_running'`（`frontend/src/pages/ActivityDetailPage.tsx:41`）；API 錯誤或 `!is_trail` 時回 null
- 標題列：「越野跑分析 — 總爬升 {total_gain_m}m」
- 4 張卡片：
  - 海拔 & 坡度調整配速 (GAP)：ComposedChart，海拔（Bar）+ GAP 線，X 軸距離 (km)
  - 坡度 vs 步頻：ScatterChart（`grade_cadence`），無趨勢線
  - 爬坡段分析：table（# / 起點 / 距離 / 爬升 / 坡度 / VAM）
  - 心率漂移 (解耦程度)：前後半 HR / GAP + `decoupling_pct`，|值| > 5% 顯示「超過5%建議加強有氧基礎」，否則「耦合良好」

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Claude / OpenAI / Gemini API key 明文存 SQLite | L（本地 app）| M | 接受；GET settings 不回 key；AI router 在 demo 模式不掛載 |
| Trail FIT 計算每次從磁碟讀取（> 2hr FIT ~50MB）| M | M | timeseries 降採樣已有 step 機制；trail 計算純 numpy，< 1s |
| AI context 超 token 限制（歷史很多訓練時）| L | H | 只取近 7 天訓練與最新一筆 PMC，區間表固定長度 |
| OpenAI / Claude API 版本更新 SDK breaking change | M | L | SDK 版本 pin 在 requirements.txt，升級前測試 |
| `AiPage` 完全替換導致 MCP 說明消失 | L | L | 舊 MCP 說明移至 ConfigTab 下方摺疊區塊 |
| 單輪對話：追問時模型看不到前一輪回答 | M | M | 目前接受；`ChatRequest.history` 已預留欄位（見 Open Questions） |
| 兩套 TSB 判讀帶（dashboard vs prompt）說法不一 | M | L | 見 Open Questions |

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| AI 串流方式 | SSE via `sse-starlette` | WebSocket | 已有依賴，單向串流需求，比 WebSocket 簡單 |
| 前端 SSE 消費 | `fetch` + ReadableStream | `EventSource` | EventSource 不支援 POST body |
| Chat 歷史持久化 | 不存 DB（React state） | 存 DB | 用戶選擇 A，簡化 schema；對話 context 從 DB 訓練資料重建 |
| Provider 抽象 | duck typing（無 Protocol 宣告） | ABC | 更 Pythonic，不需 super().__init__ 模板 |
| GAP 公式 | 線性近似 | Minetti 曲線 | 誤差 < 5%，2 行實作；Minetti 需 lookup table |
| Trail 資料來源 | FIT 即時計算（不 cache） | 計算後存 DB | 與現有 timeseries endpoint 一致；FIT 讀取 < 1s |
| Model 選擇 UI | 設定頁下拉選單 | 每次對話選 | 減少對話 UI 複雜度；模型不常換 |
| Gemini 接法 | OpenAI 相容端點 + 既有 `openai` SDK | Google 專屬 SDK | 不增依賴 |
| 個人化知識 | 由本人資料即時算「運動員特性」 | 在知識庫寫死某位跑者的數字 | 公開 repo、任何跑者可用；無資料時不亂講 |
| HR 漂移定義 | Pa:HR 解耦（速度/心率，正值＝漂移） | HR/配速比 | 對齊 TrainingPeaks / Uphill Athlete 慣例 |

---

## Open Questions

- [ ] GAP 公式後期升級 Minetti 曲線的時機：當有實測數據驗證線性近似誤差 > 8% 時（`backend/engine/algorithms/minetti.py` 已存在，trail route 尚未使用）
- [ ] `anthropic` + `openai` Python SDK 加入 `requirements.txt` 後，冷啟動時間是否可接受？（已改為 lazy import，影響應僅在首次對話）
- [x] ~~越野跑 `ClimbSegment.is_hiking` 的踏頻閾值 155 spm~~ — `is_hiking` 未實作，題目作廢
- [ ] 多輪對話：`ChatRequest.history` 已在 schema 但前後端都未使用，是否要把歷史帶進 prompt？
- [ ] Gemini 未選 model 時，chat 的預設會落到 `OPENAI_MODELS[0]`（`backend/api/ai.py:76`），應改用 `GEMINI_MODELS` 預設
- [ ] dashboard-summary / chart-interpretation 的 4 態 TSB 帶與 `engine/status.py`（AI prompt、總覽頁）不同，是否統一？

---

*Generated: 2026-05-16*
*Status: ACTIVE — code-synced 2026-10-04*
