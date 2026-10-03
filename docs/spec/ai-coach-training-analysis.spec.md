# SRS: AI Coach — 訓練分析與圖表理解

## Metadata
- **Source PRDs**:
  - `docs/prd/ai-coach-training-analysis.prd.md` — initial（chat/dashboard/trail analysis）
  - `docs/prd/wko5-trail-multipage-sync-coach.prd.md` — Milestone 4（知識驅動處方）
- **Source Linear Issue**: N/A
- **Owner**: maintainer
- **Status**: DRAFT — needs architectural review（M4 delta 進行中）
- **Generated**: 2026-05-16
- **Last Updated**: 2026-06-13

## Change History

| Date | Source PRD | Feature SRS | Summary |
|------|------------|-------------|---------|
| 2026-05-16 | `ai-coach-training-analysis.prd.md` | (initial) | Created — In-app AI chat、Smart Dashboard、Trail Analysis |
| 2026-06-13 | `wko5-trail-multipage-sync-coach.prd.md` | `docs/srs/ai-coach-knowledge-driven-prescription.srs.md` | 知識驅動處方：curated 知識模組 + zone 計算 + 處方導向 system prompt + `/ai/zones` 端點 |

## Summary

在現有 FastAPI + React 訓練分析網站上新增三個系統：
(1) **In-app AI Coach Chat** — 後端組裝訓練 context 後呼叫 Claude / OpenAI API，以 SSE 串流回應，前端呈現對話 UI；
(2) **Smart Dashboard** — 新 analytics endpoint 彙整今日 TSB 狀態與本週負荷，前端以 5 張狀態卡取代圖表堆疊；
(3) **Trail Analysis** — 新 workouts trail endpoint 從 FIT 即時計算 GAP、VAM、心率漂移，前端在訓練詳情頁自動展示越野跑圖表組。
所有新功能沿用現有 FastAPI router / SQLAlchemy async / React Query / Recharts 模式，不引入新框架。

---

## System Context

### Scope & Boundaries
- **In scope**: AI chat API + SSE 串流、AI provider 設定 UI（key + model 選擇）、dashboard summary endpoint、trail analysis endpoint、越野跑圖表元件、Smart Dashboard section
- **Out of scope**: 對話歷史持久化（本 milestone 不存 DB）、地圖可視化、騎車專屬圖表、推送通知、多運動員切換

### Actors
| Actor | Type | Interaction |
|---|---|---|
| 運動員（使用者） | Human | 瀏覽 Dashboard、開啟 AI Chat、查看訓練詳情 |
| Claude API | External Service | 接收 messages + system prompt，回傳 SSE token 串流 |
| OpenAI API | External Service | 同上，另一 provider 選項 |
| Coros FIT 檔案 | File System | Trail endpoint 讀取 FIT 計算 GAP/VAM |

### External Dependencies
| Dependency | Purpose | Failure Mode |
|---|---|---|
| Claude API (`api.anthropic.com`) | AI 回應（provider A） | `AIClient` 回傳 503；前端顯示「API 無回應，請確認 key」 |
| OpenAI API (`api.openai.com`) | AI 回應（provider B） | 同上 |
| FIT 檔案（`~/.wko5coach/fits/`） | Trail 計算原始資料 | 回傳 422 `NO_FIT_FILE`，前端隱藏 trail section |

---

## Architecture

### High-Level Diagram
```
Browser (React)
  ├── AiChat (zustand messages store)
  │     └── EventSource → POST /api/v1/ai/chat (SSE)
  ├── SmartDashboard
  │     └── useQuery → GET /api/v1/analytics/dashboard-summary
  └── ActivityDetailPage
        └── useQuery → GET /api/v1/workouts/{id}/trail

FastAPI
  ├── api/ai.py          (新) chat + status routes
  ├── api/analytics.py   (擴充) + dashboard-summary route
  ├── api/workouts.py    (擴充) + trail route
  ├── engine/ai/
  │     ├── context.py   (新) 組裝訓練 context
  │     └── client.py    (新) AIClient protocol + Claude/OpenAI impl
  └── engine/algorithms/
        └── trail.py     (新) GAP / VAM / HR drift

SQLite (AthleteSettings)
  └── 新增 ai_provider, ai_api_key, ai_model 欄位
```

### Components
| Component | Responsibility | Interface |
|---|---|---|
| `api/ai.py` | 驗證請求、組裝 context、呼叫 AIClient、串流 SSE | `POST /api/v1/ai/chat`, `GET /api/v1/ai/status` |
| `engine/ai/context.py` | 查詢 DB，組裝 system prompt + athlete context block | `async build_context(db, athlete_id, workout_id?) -> str` |
| `engine/ai/client.py` | AI provider 抽象層 | `get_ai_client(settings) -> AIClient \| None` |
| `engine/algorithms/trail.py` | GAP / VAM / HR drift 計算 | pure functions，input: numpy arrays |
| `api/workouts.py` (trail route) | 讀 FIT → 呼叫 trail.py → 回傳 JSON | `GET /api/v1/workouts/{id}/trail` |
| `api/analytics.py` (dashboard-summary) | 彙整 TSB + 本週負荷 | `GET /api/v1/analytics/dashboard-summary` |
| `AiChat.tsx` | 對話 UI，消費 SSE 串流 | zustand `useAiChatStore` |
| `TrailAnalysisSection.tsx` | 4 張越野跑圖表，條件顯示 | props: `trail: TrailResponse` |
| `SmartDashboardSection.tsx` | 5 張狀態卡 + AI 問答入口 | useQuery `dashboard-summary` |

### Data Flow

**AI Chat（主要路徑）：**
1. 前端 `AiChat` 送 `POST /api/v1/ai/chat` `{messages, athlete_id, workout_id?}`
2. `api/ai.py` 驗證 settings 中有 api_key → 否則 SSE 送 `{"error":"NO_API_KEY"}`
3. `context.py` 查 DB：PMC 最新 90 天 + 最近 10 次訓練 + MMP peaks + athlete settings
4. 若有 `workout_id`：再查該訓練 metrics；若為越野跑，附加 trail summary
5. 組裝 `system prompt` + `messages`，呼叫 `AIClient.stream()`
6. `EventSourceResponse` 逐 token 送 `data: {"token":"..."}\n\n`，結尾送 `data: {"done":true}\n\n`
7. 前端 `EventSource` listener 逐字 append 到 `currentMessage`

**Trail Analysis：**
1. `ActivityDetailPage` 若 `workout.elevation_gain_m > 100` → 觸發 `useTrailAnalysis(workoutId)`
2. `GET /workouts/{id}/trail` → 讀 FIT → `compute_gap()` + `segment_climbs()` + `compute_hr_drift()`
3. 回傳 JSON，前端 `TrailAnalysisSection` 渲染 4 張圖表

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
ALTER TABLE athlete_settings ADD COLUMN ai_provider TEXT;     -- 'claude' | 'openai'
ALTER TABLE athlete_settings ADD COLUMN ai_api_key  TEXT;     -- 純文字 API key
ALTER TABLE athlete_settings ADD COLUMN ai_model    TEXT;     -- e.g. 'claude-haiku-4-5'
```

對應 SQLAlchemy model (`db/models.py`) `AthleteSettings` 新增：
```
ai_provider: Mapped[Optional[str]]
ai_api_key:  Mapped[Optional[str]]
ai_model:    Mapped[Optional[str]]
```

### Migration Strategy
- **Forward**: `_migrate_schema()` 加入三行 ALTER TABLE，`init_db()` 啟動時執行
- **Backward**: 欄位可為 NULL，舊版 app 忽略未知欄位，無破壞性
- **Backfill**: 不需要（新欄位 default NULL，用戶在設定頁填入）
- **Coexistence**: 舊 row 三欄均 NULL → `get_ai_client()` 回傳 None → API 回傳 `NO_API_KEY`

---

## API Contracts

### Endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/ai/chat` | AI 對話，SSE 串流回應 | none（本地 app）|
| GET | `/api/v1/ai/status` | 確認 API key 是否已設定 | none |
| GET | `/api/v1/workouts/{id}/trail` | 越野跑分析資料 | none |
| GET | `/api/v1/analytics/dashboard-summary` | Smart Dashboard 彙整資料 | none |

### Request / Response Shape

#### POST /api/v1/ai/chat
```json
// Request
{
  "athlete_id": 1,
  "messages": [
    {"role": "user",      "content": "本週 TSS 是多少？"},
    {"role": "assistant", "content": "本週累計 TSS 為 312..."},
    {"role": "user",      "content": "跟上週比呢？"}
  ],
  "workout_id": null
}

// Response — 200 OK, Content-Type: text/event-stream
data: {"token": "上"}
data: {"token": "週"}
data: {"token": " TSS "}
data: {"token": "為 287"}
data: {"done": true}

// Error (inline SSE, 仍是 200 stream)
data: {"error": "NO_API_KEY", "message": "請先在設定頁填入 API Key"}
data: {"error": "API_ERROR",  "message": "Claude API 回傳 429 Too Many Requests"}
```

#### GET /api/v1/ai/status
```json
// 200 — key 已設定
{
  "configured": true,
  "provider": "claude",
  "model": "claude-haiku-4-5"
}

// 200 — 未設定
{
  "configured": false,
  "provider": null,
  "model": null
}
```

#### GET /api/v1/workouts/{id}/trail
```json
// 200 OK
{
  "workout_id": 42,
  "is_trail": true,
  "total_elevation_gain_m": 650.0,
  "gap_tss": 87.3,
  "timeseries": [
    {"t": 0,   "dist_m": 0,    "altitude": 1200.0, "pace": 360.0, "gap": 390.0, "grade": 0.0,  "hr": 138, "cadence": 178},
    {"t": 10,  "dist_m": 42.0, "altitude": 1205.0, "pace": 238.0, "gap": 310.0, "grade": 11.9, "hr": 142, "cadence": 172}
  ],
  "climb_segments": [
    {
      "start_km": 1.2, "end_km": 3.8,
      "elevation_gain_m": 240.0,
      "vam_m_per_hr": 520.0,
      "avg_hr": 158.0,
      "avg_cadence": 168.0,
      "is_hiking": false
    }
  ],
  "hr_drift": {
    "first_half_ratio": 0.82,
    "second_half_ratio": 0.89,
    "drift_pct": 8.5
  }
}

// 422 — 非 FIT 檔或 elevation 資料不足
{"detail": "NO_FIT_FILE"}
{"detail": "NO_ALTITUDE_DATA"}
```

#### GET /api/v1/analytics/dashboard-summary
```json
// 200 OK
{
  "today_tsb": -8.3,
  "tsb_state": "training",
  "ctl": 52.1,
  "atl": 60.4,
  "ctl_trend": "rising",
  "this_week_tss": 312.0,
  "this_week_hours": 6.5,
  "last_workout": {
    "date": "2026-05-15",
    "sport": "running",
    "tss": 87.0,
    "duration_s": 5400.0
  }
}
```

### Error Codes
| Code | HTTP Status | Meaning | Caller Action |
|---|---|---|---|
| `NO_API_KEY` | 200 SSE | AI key 未設定 | 導向設定頁 |
| `API_ERROR` | 200 SSE | 外部 API 失敗 | 顯示 message，讓用戶重試 |
| `NO_FIT_FILE` | 422 | 該訓練無 FIT 檔 | 前端隱藏 trail section |
| `NO_ALTITUDE_DATA` | 422 | FIT 無高度資料 | 前端隱藏 trail section |
| `WORKOUT_NOT_FOUND` | 404 | 訓練 ID 不存在 | 前端 redirect |

### Versioning Strategy
所有新 endpoint 置於 `/api/v1/` 前綴下，與現有路由一致。本 app 無外部 consumer，不需 deprecation 計畫。

---

## Non-Functional Requirements

| Category | Target | Measurement | How Achieved |
|---|---|---|---|
| 首 token 延遲 | < 2s（Claude/OpenAI 回傳第一 token）| 瀏覽器 DevTools Network | SSE streaming — 不等完整回應 |
| Trail 計算延遲 | < 1.5s for 2hr FIT（7200 samples）| local timing | numpy 向量運算，不用迴圈 |
| Dashboard summary | < 300ms | FastAPI log | 單一 SQL query，PMC cache 已算好 |
| API key 安全性 | 本地 SQLite，不暴露網路 | 架構 review | app 監聽 localhost only，CORS 限制 |
| Context 大小 | < 8k tokens（避免超 Claude Haiku limit）| 估算 | 最近 10 次訓練摘要，PMC 90 天 |

---

## Technology Choices

| Concern | Choice | Alternatives | Rationale |
|---|---|---|---|
| AI 串流協定 | SSE via `sse-starlette`（已在 requirements.txt）| WebSocket | 已有依賴，單向串流夠用 |
| Claude SDK | `anthropic` Python library | raw httpx | 官方 SDK 處理 retry/streaming |
| OpenAI SDK | `openai` Python library | raw httpx | 官方 SDK，streaming API 一致介面 |
| AI provider 抽象 | Protocol class（duck typing）| ABC | Python 3.12+，Protocol 更輕量 |
| GAP 公式 | Strava 線性近似（MVP）| Minetti 曲線 | 誤差 < 5%，實作 3 行，可日後升級 |
| 前端 SSE 消費 | `fetch` + `ReadableStream`（非 `EventSource`）| `EventSource` | POST 請求 `EventSource` 不支援 request body |
| Chat state | `zustand` store（已有 `tabStore`）| `useState` | 跨元件共用，且頁面切換不丟失 |
| 前端 AI model 選單 | `@radix-ui/react-select`（已安裝）| HTML select | 已有依賴，UI 一致 |

---

## Integration Points

| Touchpoint | Type | Contract | Backwards Compat |
|---|---|---|---|
| `AthleteSettings.ai_*` | SQLAlchemy model + DB | 三欄 nullable，舊 row 均 NULL | Yes — nullable，無破壞 |
| `GET /athletes/{id}/settings` | HTTP JSON | response 新增 `ai_provider`, `ai_model`（不含 key）| Yes — additive only |
| `PUT /athletes/{id}/settings` | HTTP JSON | `SettingsUpdate` Pydantic model 新增三欄 optional | Yes — optional fields |
| `ActivityDetailPage` | React import | 新增 `useTrailAnalysis(id)` hook + `TrailAnalysisSection` | Yes — additive |
| `SeasonTab` | React import | 新增 `SmartDashboardSection` at top | Yes — additive |
| `AiPage` | React | 完全重寫為 `AiChat` 全頁（舊頁面是 MCP 說明，廢棄）| Breaking — 可接受，功能取代 |

### Rollout Strategy
無 feature flag（本地 personal app）。直接部署。失敗回滾方式：`git revert` + 重啟 uvicorn。

---

## Codebase Patterns to Follow

| Pattern | Where to Find | Why Follow |
|---|---|---|
| FastAPI router 結構 | `backend/api/workouts.py:1-20` | `APIRouter(prefix=, tags=)` + `Depends(get_db)` |
| HTTPException 錯誤碼 | `backend/api/workouts.py:36-40` | `raise HTTPException(404, "SCREAMING_SNAKE")` |
| AthleteSettings upsert | `backend/api/athletes.py:100-130` | select-then-update-or-insert，`await db.commit()` |
| Schema forward migration | `backend/db/database.py:_migrate_schema()` | PRAGMA table_info → ALTER TABLE if col missing |
| SSE 現有用法 | `backend/api/sync.py`（Coros sync）| `EventSourceResponse` + async generator |
| React Query hook 模式 | `frontend/src/api/hooks.ts:useRunLoad()` | `useQuery({queryKey, queryFn})` |
| Chart 主題 | `frontend/src/lib/chartTheme.ts` | `CHART_COLORS`, `BASE_GRID_PROPS`, `BASE_TOOLTIP_STYLE` |
| Chart Card wrapper | `frontend/src/components/charts/TimeseriesChart.tsx:37-50` | `<Card><CardHeader>...<CardContent>` |
| 設定頁 input 表單 | `frontend/src/tabs/ConfigTab.tsx:handleSubmit()` | FormData → `update.mutate({...})` |
| Timeseries 降採樣 | `backend/api/workouts.py:get_workout_timeseries()` | `step = max(1, n//1800)`，回傳 ≤ 1800 點 |

---

## Trail Algorithm Design

`backend/engine/algorithms/trail.py`（新檔）

### compute_grade
```
Input:  altitude_m (np.ndarray), distance_m (np.ndarray)
Output: grade_pct (np.ndarray)
Method: diff(altitude) / diff(distance) * 100，加 30s rolling smooth，clip to ±45%
```

### compute_gap
```
Input:  pace_s_per_km (np.ndarray), grade_pct (np.ndarray)
Output: gap_s_per_km (np.ndarray)
Method:
  uphill   (grade > 0): factor = 1 + 0.033 * grade_pct
  downhill (grade < 0): factor = 1 - 0.015 * |grade_pct|
  GAP = pace * factor，clip pace to [30, 1800] s/km
```

### segment_climbs
```
Input:  altitude_m, distance_m, time_s, hr_bpm, cadence_rpm,
        min_grade_pct=5.0, min_gain_m=50.0
Output: list[ClimbSegment]
Method:
  1. grade > min_grade_pct 為「上坡」
  2. 連續上坡段且爬升 > min_gain_m 為一個 ClimbSegment
  3. 每段計算 VAM = elevation_gain / elapsed_time * 3600
  4. is_hiking = avg_cadence < 155 spm
```

### compute_hr_drift
```
Input:  hr_bpm, gap_pace_s_per_km, time_s
Output: {"first_half_ratio", "second_half_ratio", "drift_pct"}
Method:
  ratio = HR / GAP_pace（越高代表同配速心率越高）
  split at time_s[-1] / 2
  drift_pct = (second - first) / first * 100
  只有 > 10 分鐘訓練才計算（否則回傳 null）
```

---

## AI Context Assembly Design

`backend/engine/ai/context.py`（新檔）

### System Prompt（固定）
```
你是一位專業的耐力運動教練，專精於越野跑、山岳跑和路跑。
請用繁體中文回答所有問題。語氣像一位關心學員的教練：具體、直接、給數字、給建議。
不要說「根據您的資料」，直接說「你的 CTL 是 52」。
```

### Athlete Context Block（動態，附加於 system prompt）
```
---
【運動員資料 — {today}】

基本設定：
  FTP {ftp}W | 閾值配速 {threshold_pace} min/km | LTHR {lthr}bpm | 體重 {weight}kg

體能狀態 (PMC)：
  CTL(體能) {ctl} | ATL(疲勞) {atl} | TSB(狀態) {tsb}
  狀態判讀：{tsb_state_text}  // e.g. "輕度疲勞，適合中低強度訓練"

最近 10 次訓練：
  {YYYY-MM-DD} {sport} {duration} TSS:{tss} NP/Pace:{power_or_pace}
  ...

最佳成績 (MMP，全歷史)：
  5min: {w}W | 20min: {w}W | 60min: {w}W

[若有 workout_id:]
本次訓練 #{workout_id}：
  {date} {sport} {duration} TSS:{tss} NP:{np}W IF:{if}
  [若越野跑:] 爬升:{elev}m GAP-TSS:{gap_tss} VAM:{vam}m/hr 心率漂移:{drift}%
---
```

### Context 大小估算
| 部分 | 估計 tokens |
|------|------------|
| System prompt | ~80 |
| 基本設定 | ~40 |
| PMC 狀態 | ~30 |
| 最近 10 次訓練 | ~200 |
| MMP 峰值 | ~30 |
| 本次訓練（有時）| ~60 |
| **Total** | **~440**（遠低於 Haiku 128k limit）|

### TSB 狀態判讀規則
| TSB 範圍 | state | 中文解讀 |
|---------|-------|---------|
| > +25 | `peak_form` | 峰值狀態，適合比賽 |
| +5 ~ +25 | `optimal` | 最佳訓練狀態 |
| -10 ~ +5 | `training` | 正常訓練負荷 |
| -25 ~ -10 | `tired` | 輕度疲勞，建議降量 |
| < -25 | `overreached` | 過度訓練警示 |

---

## AI Client Abstraction

`backend/engine/ai/client.py`（新檔）

### Protocol
```
Protocol AIClient:
  async def stream(messages: list[dict], system: str) -> AsyncIterator[str]
```

### ClaudeClient
```
Uses: anthropic Python SDK
Init: anthropic.AsyncAnthropic(api_key=key)
Stream: client.messages.stream(model=model, messages=..., system=..., max_tokens=1024)
```

### OpenAIClient
```
Uses: openai Python SDK
Init: openai.AsyncOpenAI(api_key=key)
Stream: client.chat.completions.create(model=model, messages=[system_msg, ...], stream=True)
```

### Factory
```
def get_ai_client(settings: AthleteSettings) -> AIClient | None:
    if not settings.ai_api_key or not settings.ai_provider:
        return None
    if settings.ai_provider == "claude":
        return ClaudeClient(key=settings.ai_api_key, model=settings.ai_model)
    if settings.ai_provider == "openai":
        return OpenAIClient(key=settings.ai_api_key, model=settings.ai_model)
    return None
```

### 可選模型（UI 選單選項）
```
Claude:
  claude-haiku-4-5      — 最快 / 最省費
  claude-sonnet-4-6     — 均衡（推薦）
  claude-opus-4-7       — 最強分析力

OpenAI:
  gpt-4o-mini           — 最快 / 最省費
  gpt-4o                — 均衡
  gpt-4.1               — 最新
```

---

## Frontend Component Design

### AiChat.tsx（新）
- 位置：AI tab（`AiPage.tsx` 完全替換）
- zustand store `useAiChatStore`：`messages: Message[]`, `isStreaming: bool`, `currentWorkoutId: number | null`
- SSE 消費：`fetch('/api/v1/ai/chat', {method:'POST', body:JSON.stringify(...)})` → `response.body.getReader()` decode UTF-8 → parse `data: {...}` lines
- 渲染：streaming 中的 `currentMessage` 直接 append，完成後 push to `messages`
- 快捷問題按鈕：「本週狀態？」、「昨天練得怎樣？」、「今天適合高強度嗎？」

### ConfigTab 新增 AI 設定區塊
- Provider 選單（radix-ui/react-select）：「Claude」、「OpenAI」
- Model 選單（依 provider 動態切換選項）
- API Key 輸入（type="password"，`autocomplete="off"`）
- 儲存：沿用 `PUT /athletes/{id}/settings` + `SettingsUpdate` Pydantic model 新增三欄

### SmartDashboardSection.tsx（新，置於 SeasonTab 頂部）
```
5 張 MetricCard（現有元件）：
  TSB {value}  標籤：{tsb_state 中文}
  CTL {value}  趨勢箭頭
  本週 TSS {value}
  本週時數 {value}h
  最近訓練 {sport} {date}

「問 AI 教練」按鈕 → 導向 AI tab（useTabStore.setTab('ai')）
```

### TrailAnalysisSection.tsx（新，置於 ActivityDetailPage）
- 條件顯示：`workout.elevation_gain_m > 100` 時渲染
- 4 個子元件：
  - `ElevationGapChart`：ComposedChart，altitude（面積）+ pace + gap 三線，X軸距離(km)
  - `GradeCadenceScatter`：ScatterChart，grade_pct vs cadence，加線性趨勢線
  - `ClimbSegmentsTable`：HTML table，每段爬升 + VAM + avg HR + 是否健行
  - `HrDriftCard`：MetricCard 顯示漂移百分比 + 文字解讀

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Claude / OpenAI API key 明文存 SQLite | L（本地 app）| M | 接受，app 僅 localhost，提醒用戶不要共享 DB 檔 |
| Trail FIT 計算每次從磁碟讀取（> 2hr FIT ~50MB）| M | M | timeseries 降採樣已有 step 機制；trail 計算純 numpy，< 1s |
| AI context 超 token 限制（歷史很多訓練時）| L | H | 只取最近 10 次，MMP 只取 3 個時長，固定 < 500 tokens |
| OpenAI / Claude API 版本更新 SDK breaking change | M | L | SDK 版本 pin 在 requirements.txt，升級前測試 |
| `AiPage` 完全替換導致 MCP 說明消失 | L | L | 舊 MCP 說明移至 ConfigTab 下方摺疊區塊 |

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| AI 串流方式 | SSE via `sse-starlette` | WebSocket | 已有依賴，單向串流需求，比 WebSocket 簡單 |
| 前端 SSE 消費 | `fetch` + ReadableStream | `EventSource` | EventSource 不支援 POST body |
| Chat 歷史持久化 | 不存 DB（React state） | 存 DB | 用戶選擇 A，簡化 schema；對話 context 從 DB 訓練資料重建 |
| Provider 抽象 | Protocol（duck typing） | ABC | 更 Pythonic，不需 super().__init__ 模板 |
| GAP 公式 | 線性近似 | Minetti 曲線 | 誤差 < 5%，2 行實作；Minetti 需 lookup table |
| Trail 資料來源 | FIT 即時計算（不 cache） | 計算後存 DB | 與現有 timeseries endpoint 一致；FIT 讀取 < 1s |
| Model 選擇 UI | 設定頁下拉選單 | 每次對話選 | 減少對話 UI 複雜度；模型不常換 |

---

## Open Questions

- [ ] GAP 公式後期升級 Minetti 曲線的時機：當有實測數據驗證線性近似誤差 > 8% 時
- [ ] `anthropic` + `openai` Python SDK 加入 `requirements.txt` 後，冷啟動時間是否可接受？（估計各 +0.3s import）
- [ ] 越野跑 `ClimbSegment.is_hiking` 的踏頻閾值 155 spm 是否符合實際資料？需跑一次真實 FIT 驗證

---

*Generated: 2026-05-16*
*Status: DRAFT — needs architectural review*
*Next: `/prp-plan docs/spec/ai-coach-training-analysis.spec.md`*
