# Spec: sport-pages（多運動分流頁面與越野跑分析）

## Metadata
- **Module**: sport-pages
- **Parent Module**: N/A
- **Sub-modules**: N/A
- **Source PRDs**:
  - `docs/prd/wko5-trail-multipage-sync-coach.prd.md` — initial creation（M1 + M2）
- **Source Linear Issue**: N/A
- **Owner**: maintainer
- **Status**: ACTIVE — living document
- **Created**: 2026-06-13
- **Last Updated**: 2026-06-13

## Change History

| Date | Source PRD | Feature SRS | Summary |
|------|------------|-------------|---------|
| 2026-06-13 | `docs/prd/wko5-trail-multipage-sync-coach.prd.md` | `docs/srs/sport-pages-multi-sport-views-trail-analytics.srs.md` | Created from brownfield analysis — 把單一 SeasonPage 拆成總體/跑步/越野跑三頁、持久化 trail 分類、越野跑專屬圖表、圖表白話化層、公式驗證工具 |

## Summary

`sport-pages` 在既有 FastAPI + SQLAlchemy async + React/Recharts 訓練分析網站上，把目前單一的 `SeasonPage` 重構為三個資料彼此獨立的頁面（總體 / 跑步 / 越野跑）。核心架構選擇是「同一批 analytics 端點 + `sports[]`/`trail_classification` 篩選參數化」，避免每頁一套重複端點；trail/road 改為解析時持久化的分類欄位（可手動覆寫）。越野跑頁沿用既有 `trail.py` 的 GAP/VAM/climb 計算並加上以 hrTSS 為主的越野負荷；所有圖表加一層由規則產生的「白話解讀 + 狀態號誌」（抽自既有 SmartDashboardSection 模式）。另含一套離線公式驗證工具對照 WKO5 逆向素材。

---

## Domain Model

### Bounded Context
- **Context Name**: TrainingAnalyticsViews（訓練分析視圖）
- **Domain Layer**: Core Domain
- **Parent Module**: N/A

### Ubiquitous Language
| Term | Definition |
|------|-----------|
| 總體頁 (Overview) | 預設含所有運動別、提供多選 checkbox 篩選的頁面；篩選只作用於本頁 |
| 跑步頁 (Running) | 只含 `sport=running` 活動的頁面 |
| 越野跑頁 (Trail) | 只含 `trail_classification=trail` 活動、提供爬升導向圖表的頁面 |
| trail_classification | 持久化欄位：`road`/`trail`/`unknown`，解析時依爬升率與坡度計算，可手動覆寫 |
| hrTSS | 以心率（相對 LTHR）計算的訓練壓力分數；越野跑技術地形主負荷 |
| rTSS / NGP | 以配速+坡度（Normalized Graded Pace）計算的跑步壓力分數；越野跑並陳參考 |
| climb_load | 爬升/垂直負荷指標（候選自訂公式，無 WKO5 來源可驗證） |
| 圖表解讀層 | 規則產生的「一句話白話重點 + 狀態號誌」，套用於每個圖表 |

### Domain Events
N/A — 本模組為讀取/視圖導向，不對外發事件。

---

## System Context

### Scope & Boundaries
- **In scope**: 三頁面分流與路由、總體頁多選篩選、trail 分類持久化與覆寫、越野跑專屬圖表與端點、圖表白話化層、離線公式驗證工具
- **Out of scope**: 資料同步頁面（M3，coros-sync 模組）、AI 教練處方（M4，ai-coach 模組）、單車/游泳專屬頁、AI 即時圖表解讀完整實作、上傳/寫回 TP

### Actors
| Actor | Type | Interaction |
|---|---|---|
| 運動員（自己） | Human | 在三頁面間切換、調整總體頁篩選、檢視越野跑圖表、手動改分類 |
| FIT 檔案（`~/.wko5coach/fits/`） | File System | 解析時計算 trail 分類與越野指標來源 |
| WKO5 逆向素材 | Reference | 提供權威公式常數供離線驗證對照 |

### External Dependencies
| Dependency | Purpose | Failure Mode |
|---|---|---|
| 既有 analytics/PMC 端點 | 三頁面圖表資料來源 | 沿用既有錯誤處理；無資料回空序列 |
| AthleteSettings（LTHR/zones） | hrTSS 計算輸入 | 缺值時 hrTSS 不可算，fallback rTSS 或標記 N/A（Open Question） |

---

## Architecture

### High-Level Diagram
```
            ┌─────────────────────────────────────────────┐
 React      │  OverviewPage   RunningPage   TrailPage      │
 (Recharts) │      │  (sports[] checkbox)   │              │
            │      └──── 共用圖表元件 + 解讀層(規則) ───────┘
            └───────────────────┬─────────────────────────┘
                                │ /analytics/* ?sports[]=&trail=
            ┌───────────────────┴─────────────────────────┐
 FastAPI    │ analytics router (參數化 sport 篩選)          │
            │ trail-load / trail-summary / sports/facets    │
            │ chart-interpretation (規則)                   │
            │ workouts/{id}/classification (PATCH)          │
            └───────┬───────────────────────┬──────────────┘
                    │                        │
        engine/algorithms/metrics.py   files/fit_reader.py
        engine/algorithms/trail.py     (解析時寫 trail_classification)
        engine/algorithms/validator.py(離線, 對照 WKO5 素材)
                    │
              SQLite (WorkoutFile + trail_classification, WorkoutMetric, PmcCache)
```

### Components
| Component | Responsibility | Interface |
|---|---|---|
| analytics router（擴充） | 接受 `sports[]`/`trail_classification` 篩選，回 PMC/load 序列 | HTTP GET |
| trail-load / trail-summary endpoint | 越野跑專屬負荷（hrTSS 主、rTSS 並陳）與聚合 | HTTP GET |
| sports/facets endpoint | 動態回傳該運動員實有運動別供 checkbox | HTTP GET |
| chart-interpretation（規則） | 由指標值產生白話一句話 + 狀態號誌 | HTTP GET（或前端共用函式） |
| classification PATCH | 手動覆寫 trail/road | HTTP PATCH |
| fit_reader 分類步驟 | 解析時計算並寫入 `trail_classification` | 函式呼叫（解析管線內） |
| validator（離線） | 對照 WKO5 素材驗證核心公式，輸出報告 | CLI / 測試 |
| 前端三頁面 + 解讀層 | 路由分流、篩選狀態、套用解讀規則到圖表 | React Router + React Query |

### Data Flow
解析（push 一次）：FIT → fit_reader 正規化 sport + 計算 trail_classification → 寫 DB。檢視（pull）：頁面依自身篩選組合呼叫共用 analytics 端點 → 後端用 PmcCache/即時計算 → 前端渲染圖表 + 規則解讀層。驗證（離線/批次）：validator 讀計算結果對照 WKO5 公式常數 → 報告。

### Sequence Diagrams (key flows)
```
Overview filter:
  User toggles checkbox → 前端更新 sports[] state
    → GET /analytics/pmc?sports=running,cycling,strength,...
      → 後端 WHERE sport IN (...) → 序列
    → 圖表重繪 + 解讀層依新值更新號誌
```

---

## Data Model

### Entities
| Entity | Owner | Lifecycle |
|---|---|---|
| WorkoutFile（擴充 trail_classification） | sport-pages（共用核心表） | 解析時建立/更新；可手動覆寫 |

### Schema (new / changed)
```sql
ALTER TABLE workout_files ADD COLUMN trail_classification VARCHAR(20) DEFAULT 'unknown';
ALTER TABLE workout_files ADD COLUMN classification_overridden BOOLEAN DEFAULT 0;
-- 越野/爬升負荷以新 metric_key 進既有 workout_metrics（hr_tss, climb_load, ngp, r_tss）
```

### Migration Strategy
- **Forward**: 新增兩欄（預設 unknown / false）；對既有 ~1100 筆跑一次回填分類腳本。
- **Backward**: 欄位可空，移除不影響既有 sport 篩選。
- **Backfill**: 依既有 `elevation_gain_m` 與（可得時）坡度統計回填 `trail_classification`。
- **Coexistence**: 未回填者為 `unknown`，越野跑頁以 `trail` 為準，過渡期不顯示 unknown。

---

## API Contracts

### Endpoints
| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/analytics/pmc?sports=` | 參數化 sport 篩選的 PMC | athlete |
| GET | `/api/v1/analytics/trail-load` | 越野 PMC：hrTSS 主 + rTSS 並陳 + climb 序列 | athlete |
| GET | `/api/v1/analytics/trail-summary` | 越野聚合：總爬升/VAM/爬升能力/PI | athlete |
| GET | `/api/v1/sports/facets` | 該運動員實有運動別清單 | athlete |
| GET | `/api/v1/analytics/chart-interpretation` | 規則白話解讀 + 號誌 | athlete |
| PATCH | `/api/v1/workouts/{id}/classification` | 手動覆寫 trail/road | athlete |

### Request / Response Shape
```json
// GET /api/v1/sports/facets → 200
{ "sports": [
  {"key": "running", "count": 412, "label": "跑步"},
  {"key": "trail_running", "count": 88, "label": "越野跑"},
  {"key": "strength", "count": 60, "label": "肌力"}
]}

// PATCH /api/v1/workouts/{id}/classification
// Request
{ "trail_classification": "road" }
// Response 200
{ "id": "string", "trail_classification": "road", "classification_overridden": true }
```

### Error Codes
| Code | HTTP Status | Meaning | Caller Action |
|---|---|---|---|
| `WORKOUT_NOT_FOUND` | 404 | 覆寫不存在的活動 | 檢查 id |
| `INVALID_CLASSIFICATION` | 400 | 值非 road/trail/unknown | 修正請求 |
| `NO_HR_DATA` | 422 | trail-load 缺 HR 無法算 hrTSS | fallback rTSS 或隱藏 |

### Versioning Strategy
沿用既有 `/api/v1` path 版本；本次為向後相容的擴充（新增參數/端點），不破壞既有呼叫。

---

## Non-Functional Requirements

| Category | Target | Measurement | How Achieved |
|---|---|---|---|
| Performance | 圖表載入 p95 < 1.5s | 前端計時 / 後端 log | PmcCache、持久化分類避免查詢期重算 |
| Correctness | 核心圖表 100% 對照 WKO5 | validator 報告 | 逆向素材常數比對 + 測試 |
| Maintainability | 三頁共用端點，差異僅篩選 | code review | sports[] 參數化、解讀規則共用層 |
| Observability | 驗證可重跑、diff 可追溯 | docs/reports/ | validator 落檔報告 |

---

## Technology Choices

| Concern | Choice | Alternatives | Rationale |
|---|---|---|---|
| Sport 分頁 | 同端點 + sports[] 參數化 | 每頁一套端點 | 三頁差異僅篩選，避免重複 |
| Trail 分類 | 解析時持久化欄位 + 可覆寫 | 查詢期動態 elevation 閾值 | 邊緣穩定、可手動修正、查詢快 |
| 越野負荷 | hrTSS 主 + rTSS 並陳 | 純 rTSS / 自訂爬升 TSS | 技術地形 TP 自承 rTSS 低估 |
| 圖表解讀 | 規則為主 + AI 選配 | 純 AI 即時生成 | 無成本、一致、可離線；AI 後續再加 |
| 圖表庫 | 沿用 Recharts | 換庫 | 既有 11 個圖表元件已用 |

---

## Integration Points

| Touchpoint | Type | Contract | Backwards Compat |
|---|---|---|---|
| analytics router | HTTP（擴充參數） | 新增 sports[]，預設行為不變 | Yes |
| fit_reader 解析管線 | 函式（新增分類步驟） | 不改既有 sport 正規化 | Yes |
| SmartDashboardSection 模式 | 前端共用 | 抽成可重用解讀層 | Yes |
| WorkoutMetric | DB（新 metric_key） | 既有 key 不變 | Yes |

### Rollout Strategy
分類欄位 migration + 回填腳本一次完成；三頁面可先上越野跑頁（M1），再上總體/跑步分流（M2）。回填可重跑（idempotent），手動覆寫者不被回填覆蓋。

---

## Codebase Patterns to Follow

| Pattern | Where to Find | Why Follow |
|---|---|---|
| Run-specific PMC | `backend/engine/algorithms/metrics.py:200-248` | 越野 PMC 比照 sport-filtered 模式 |
| Trail 計算（GAP/VAM/climb） | `backend/engine/algorithms/trail.py:1-100` | 越野圖表直接複用 |
| Sport 正規化 | `backend/files/fit_reader.py:294-304` | 分類步驟接在其後 |
| 寫死 sport 篩選（待參數化） | `backend/api/analytics.py:168,209,275,294` | 改為 sports[] 的起點 |
| FastAPI router 註冊 | `backend/main.py:10-43` | 新端點比照註冊 |
| 狀態號誌 + 白話 | `frontend/src/components/charts/SmartDashboardSection.tsx:4-79` | 抽成共用解讀層 |
| 圖表組成 | `frontend/src/components/charts/RunLoadChart.tsx:14-76` | 越野圖表比照 ComposedChart + ReferenceArea |
| React Query hooks | `frontend/src/api/hooks.ts` | 新端點比照加 hook |
| React Router | `frontend/src/App.tsx:1-26` | 三頁面路由比照 |
| Nav 項目 | `frontend/src/layouts/AppShell.tsx:4-43` | 三頁面入口加入 NAV_ITEMS |

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| trail 分類閾值錯置（平路爬坡跑被歸 trail 或反之） | M | M | 解析時用爬升率+坡度分佈而非單一閾值；保留手動覆寫 |
| hrTSS 缺 LTHR/HR 歷史值 | M | M | 缺值 fallback rTSS 或標 N/A；Open Question 確認 |
| climb_load 原創無法對 WKO5 驗證 | M | L | 明確標記為原創指標、排除於 AC-7 驗證集 |
| 參數化 sport 篩選改動既有端點引入回歸 | L | M | 預設參數維持原行為；validator + 既有測試把關 |

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| 分頁架構 | 同端點 + sports[] 參數化 | 每頁一套端點 | 減重複、易維護 |
| trail 分類 | 持久化欄位 + 可覆寫 | 查詢期動態 | 穩定、可修正、查詢快 |
| 越野負荷 | hrTSS 主 + rTSS 並陳 | 純 rTSS / 自訂 | 技術地形 rTSS 低估 |
| 圖表解讀 | 規則為主 + AI 選配 | 純 AI | 無成本一致可離線 |
| SRS 切分 | 先 M1+M2 一份 | 四份一次 | MVP 優先、控節奏 |

---

## Open Questions

- [ ] climb_load 若採自訂公式，標記為原創、排除 AC-7 驗證集？
- [ ] hrTSS 所需 LTHR/HR zones 對歷史活動覆蓋率？缺值 fallback 策略？
- [ ] 總體頁跨運動（肌力、桌球）無功率/配速者負荷如何估算？（可能延後）
