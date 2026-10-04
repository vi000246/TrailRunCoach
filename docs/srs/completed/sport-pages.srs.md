# Spec: sport-pages（多運動分流頁面與越野跑分析）

> ⛔ **CANCELED（2026-10-04）**：React SPA 的總體／跑步／越野跑三頁、Smart Dashboard、圖表白話化與越野負荷 API（`/api/v1/analytics/*`、`/api/v1/sports/facets`、`backend/engine/algorithms/trail.py`、`interpret.py`）已隨 `frontend/` 刪除。仍在的只有 trail 分類持久化（`PATCH /api/v1/workouts/{id}/classification`，見 `docs/spec/workouts.spec.md`）。本文是當時的設計紀錄。

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
- **Last Updated**: 2026-10-04

## Change History

| Date | Source PRD | Feature SRS | Summary |
|------|------------|-------------|---------|
| 2026-06-13 | `docs/prd/wko5-trail-multipage-sync-coach.prd.md` | `docs/srs/sport-pages-multi-sport-views-trail-analytics.srs.md` | Created from brownfield analysis — 把單一 SeasonPage 拆成總體/跑步/越野跑三頁、持久化 trail 分類、越野跑專屬圖表、圖表白話化層、公式驗證工具 |
| 2026-10-04 | code-sync | N/A | 對齊實作：PMC 端點實為 `/api/v1/pmc`、各 analytics 端點的 `sports[]` 與資料來源去重（in_use）、分類寫入點在 file_service、PATCH 支援 `auto`、覆寫 UI 移到 static 活動頁、`is_trail` 單一判準；trail-summary / chart-interpretation 回應形狀；climb_load / ngp / NO_HR_DATA 未實作；React 頁與 static shell 並存說明 |

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
| 圖表解讀層 | 規則產生的「一句話白話重點 + 狀態號誌」，套用於有 `chart` prop 的 `ChartCard`（目前一律依最新 TSB） |
| is_trail | 引擎層唯一的越野判準：`runningtrail` tag 或 sport type `trail running`；同步的 FIT 由 DB 分類帶上 tag（`backend/engine/algorithms/classify.py:27-34`） |
| 使用中資料來源 (in_use) | 同一時間只採 COROS 或 TrainingPeaks 其一的 canonical 活動；analytics 查詢一律套用 |

### Domain Events
N/A — 本模組為讀取/視圖導向，不對外發事件。

---

## System Context

### Scope & Boundaries
- **In scope**: 三頁面分流與路由、總體頁多選篩選、trail 分類持久化與覆寫、越野跑專屬圖表與端點、圖表白話化層、離線公式驗證工具
- **Out of scope**: 資料同步頁面（M3，coros-sync 模組）、AI 教練處方（M4，ai-coach 模組）、單車/游泳專屬頁、AI 即時圖表解讀完整實作、上傳/寫回 TP、運動成就頁（`/achievements`，另一功能）
- **部署現況（2026-10-04）**：三頁面是 React SPA（`frontend/`）的路由；SPA 只在 dev（5173）或 Docker build 出 `frontend/dist` 時由後端提供，否則 `/` 轉向 server-rendered 總覽頁（`backend/main.py:176-199`），其 static shell 導覽（`backend/static/shell.js:19-29`）不含總體/跑步/越野跑三頁。後端端點兩邊共用；trail/road 手動覆寫目前只在 static 活動頁有 UI。

### Actors
| Actor | Type | Interaction |
|---|---|---|
| 運動員（使用者） | Human | 在三頁面間切換、調整總體頁篩選、檢視越野跑圖表、手動改分類 |
| FIT 檔案（COROS / TrainingPeaks 同步的資料目錄） | File System | 解析時計算 trail 分類與越野指標來源 |
| WKO5 逆向素材 | Reference | 提供權威公式常數供離線驗證對照 |

### External Dependencies
| Dependency | Purpose | Failure Mode |
|---|---|---|
| 既有 analytics/PMC 端點 | 三頁面圖表資料來源 | 沿用既有錯誤處理；無資料回空序列 |
| AthleteSettings（LTHR / 閾值配速） | hrTSS / rTSS 計算輸入 | 缺 LTHR 或 HR → 不寫 `hr_tss` 列（`compute_load_metrics` 只寫正值）；trail-load 該日 hrTSS 視為 0，無 fallback |
| 資料來源去重（`backend/sync/dedup.py`） | 所有 analytics 查詢只算「使用中」資料來源的 canonical 活動 | facets 例外：計數未去重 |

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
 FastAPI    │ pmc + analytics router (參數化 sport 篩選)    │
            │ trail-load / trail-summary / sports/facets    │
            │ chart-interpretation (規則)                   │
            │ workouts/{id}/classification (PATCH)          │
            └───────┬───────────────────────┬──────────────┘
                    │                        │
        engine/algorithms/metrics.py   files/file_service.py
        engine/algorithms/classify.py  (解析時寫 trail_classification)
        engine/algorithms/validator.py(離線, 對照 WKO5 素材)
                    │
              SQLite (WorkoutFile + trail_classification, WorkoutMetric, PmcCache)
```

### Components
| Component | Responsibility | Interface |
|---|---|---|
| pmc + analytics router（擴充） | 接受 `sports[]` 篩選（`_sport_clause`，同時套資料來源去重），回 PMC/load 序列；trail 端點以 `trail_classification == 'trail'` 篩選 | HTTP GET |
| trail-load / trail-summary endpoint | 越野跑專屬負荷（hrTSS 驅動 PMC、rTSS 並陳）與每次活動爬升/VAM 聚合 | HTTP GET |
| sports/facets endpoint | 動態回傳該運動員實有運動別供 checkbox | HTTP GET |
| chart-interpretation（規則） | 由最新 TSB 產生白話一句話 + 狀態號誌（`interpret_tsb`，不分 chart） | HTTP GET；前端 `ChartCard` 經 `useInterpretation` 呼叫 |
| classification PATCH | 手動覆寫 trail/road/unknown；`auto` 清除覆寫並重套規則 | HTTP PATCH |
| file_service 分類步驟 | 解析時呼叫 `classify_trail`（爬升率 ≥ 20 m/km → trail）寫入 `trail_classification`；已覆寫者跳過 | 函式呼叫（`backend/files/file_service.py:22-31`） |
| backfill 腳本 | 既有活動回填分類，idempotent、不碰已覆寫 | CLI `python -m backend.scripts.backfill_classification` |
| validator（離線） | 對照 WKO5 常數驗證 CTL/ATL/TSB/rTSS 恆等式，輸出 Markdown 表 | CLI `python -m backend.engine.algorithms.validator` / 測試 |
| 前端三頁面 + 解讀層 | 路由分流、篩選狀態、套用解讀規則到圖表 | React Router + React Query |

### Data Flow
解析（push 一次）：FIT → fit_reader 正規化 sport（含 trail → `running`）→ file_service 計算 trail_classification 與 hr_tss / r_tss → 寫 DB。檢視（pull）：頁面依自身篩選組合呼叫共用 analytics 端點 → 後端用 PmcCache/即時計算 → 前端渲染圖表 + 規則解讀層。驗證（離線/批次）：validator 讀計算結果對照 WKO5 公式常數 → 報告。

### Sequence Diagrams (key flows)
```
Overview filter:
  User toggles checkbox → 前端更新 sports[] state
    → GET /pmc?sports=running&sports=cycling ...（另有 /analytics/weekly、/analytics/dashboard-summary 同參數）
      → 後端 WHERE <in_use> AND sport IN (...) → 序列
    （未勾過篩選時不帶 sports = 全部運動）
    → 圖表重繪（解讀號誌依全運動最新 TSB，不隨篩選變）
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
-- 越野負荷以新 metric_key 進既有 workout_metrics：hr_tss, r_tss（compute_load_metrics，只寫正值）
-- climb_load / ngp 未實作；爬升能力由 trail-summary 即時以 elevation_gain_m / duration 算 VAM
```

### Migration Strategy
- **Forward**: 新增兩欄（預設 unknown / false）；對既有活動跑一次回填分類腳本。
- **Backward**: 欄位可空，移除不影響既有 sport 篩選。
- **Backfill**: `backend/scripts/backfill_classification.py` 依 `sport` + `total_distance_m` + `elevation_gain_m`（爬升率）回填 `trail_classification`；未用坡度分佈。
- **Coexistence**: 未回填者為 `unknown`，越野跑頁以 `trail` 為準，過渡期不顯示 unknown。

---

## API Contracts

### Endpoints
| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/pmc?sports=` | 參數化 sport 篩選的 PMC（省略 = 全部運動；`backend/api/pmc.py:17-52`） | athlete |
| GET | `/api/v1/analytics/weekly?sports=` | 每週 TSS / 時數 / 次數（省略 = 全部運動） | athlete |
| GET | `/api/v1/analytics/dashboard-summary?sports=` | 總體頁狀態卡（見 ai-coach spec） | athlete |
| GET | `/api/v1/analytics/run-load` · `/intensity-load` · `/run-volume` `?sports=` | 跑步頁序列；省略 sports 時預設 `["running"]`（向後相容） | athlete |
| GET | `/api/v1/analytics/trail-load` | 越野 PMC：hrTSS 驅動 CTL/ATL/TSB + 每日 hr_tss / r_tss（`backend/api/analytics.py:218-277`） | athlete |
| GET | `/api/v1/analytics/trail-summary` | 越野聚合：總爬升、活動數、每次活動爬升/距離/VAM（`backend/api/analytics.py:390-441`） | athlete |
| GET | `/api/v1/sports/facets` | 該運動員實有運動別清單（`backend/api/sports.py:18-40`） | athlete |
| GET | `/api/v1/analytics/chart-interpretation?chart=` | 規則白話解讀 + 號誌（`backend/api/analytics.py:365-387`） | athlete |
| PATCH | `/api/v1/workouts/{id}/classification` | 手動覆寫 trail/road/unknown，或 `auto` 還原（`backend/api/workouts.py:53-80`） | athlete |

日期參數 `date_from` / `date_to` 預設為今天（`today_local()`）往回 365 天。

### Request / Response Shape
```json
// GET /api/v1/sports/facets → 200（key 為正規化後的 sport；trail 已併入 running）
{ "sports": [
  {"key": "running", "count": 412, "label": "跑步"},
  {"key": "cycling", "count": 60, "label": "單車"},
  {"key": "strength", "count": 30, "label": "strength"}
]}

// GET /api/v1/analytics/trail-load → 200
{ "series": [{"date": "YYYY-MM-DD", "ctl": 40.1, "atl": 45.0, "tsb": -4.9, "hr_tss": 85.0, "r_tss": 62.0}],
  "athlete_id": 1, "primary_load": "hr_tss" }
// r_tss 當日無資料時為 null；CTL/ATL 不帶 AthleteSettings 的初始種子

// GET /api/v1/analytics/trail-summary → 200（recent 依日期新到舊）
{ "athlete_id": 1, "total_gain_m": 12000, "activity_count": 25,
  "recent": [{"date": "YYYY-MM-DD", "gain_m": 650, "distance_km": 12.3, "vam": 420}] }
// vam = 整趟 elevation_gain_m × 3600 / duration_s（含平路與下坡，非純爬坡 VAM）

// GET /api/v1/analytics/chart-interpretation?chart=pmc → 200
{ "status": "optimal", "color": "blue", "label": "最佳",
  "summary": "目前 TSB -3，狀態：最佳。狀態最佳，維持訓練節奏。", "chart": "pmc" }
// status: fresh / optimal / tired / overreached / unknown（無 PMC 時）

// PATCH /api/v1/workouts/{id}/classification
// Request（"auto" = 清除覆寫並重套 classify_trail）
{ "trail_classification": "road" }
// Response 200
{ "id": 123, "trail_classification": "road", "classification_overridden": true }
```
（數字為示意值。）

### Error Codes
| Code | HTTP Status | Meaning | Caller Action |
|---|---|---|---|
| `WORKOUT_NOT_FOUND` | 404 | 覆寫不存在的活動 | 檢查 id |
| `INVALID_CLASSIFICATION` | 400 | 值非 road/trail/unknown/auto | 修正請求 |

原設計的 `NO_HR_DATA`（422）**未實作**：trail-load 缺 hr_tss 時直接回較少/空的序列。

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
| Trail 分類 | 解析時持久化欄位 + 可覆寫；規則為爬升率 ≥ 20 m/km（`TRAIL_CLIMB_RATE_M_PER_KM`） | 查詢期動態 elevation 閾值 | 邊緣穩定、可手動修正、查詢快 |
| 越野負荷 | hrTSS 主 + rTSS 並陳 | 純 rTSS / 自訂爬升 TSS | 技術地形 TP 自承 rTSS 低估 |
| 圖表解讀 | 規則為主 + AI 選配 | 純 AI 即時生成 | 無成本、一致、可離線；AI 後續再加 |
| 圖表庫 | 沿用 Recharts | 換庫 | 既有 11 個圖表元件已用 |

---

## Integration Points

| Touchpoint | Type | Contract | Backwards Compat |
|---|---|---|---|
| pmc + analytics router | HTTP（擴充參數） | 新增 sports[]，預設行為不變（run-load / intensity-load / run-volume 省略時仍為 running） | Yes |
| file_service 解析管線 | 函式（新增分類步驟） | 不改既有 sport 正規化 | Yes |
| static 活動頁 terrain 選單 | HTTP PATCH classification | `backend/api/wko5views.py:845-865` 提供 `workout_file_id` / 是否可改；WKO5 來源不可改 | Yes |
| wko5expr FIT 資料集 | 讀 DB 分類（含覆寫）決定 sport type | `backend/engine/wko5expr/fitdataset.py:111-125` | Yes |
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
| Sport 正規化 | `backend/files/fit_reader.py:294-304` | trail 會被正規化成 running，越野靠分類欄位區分 |
| 解析時分類 | `backend/files/file_service.py:22-31` | 已覆寫者跳過，re-import 不覆蓋 |
| 越野判準（引擎層） | `backend/engine/algorithms/classify.py:27-34` | 引擎模組一律用 `is_trail()` |
| sports[] 篩選 + 資料來源去重 | `backend/api/analytics.py:16-29` | 新端點一律 `.where(_sport_clause(sports, await in_use(db, athlete_id)))` |
| FastAPI router 註冊 | `backend/main.py:128-147` | 新端點加入 `routers`；owner-only 者另列入 `owner_only` |
| 狀態號誌 + 白話 | `frontend/src/components/charts/SmartDashboardSection.tsx:4-79` | 抽成共用解讀層 |
| 圖表組成 | `frontend/src/components/charts/RunLoadChart.tsx:14-76` | 越野圖表比照 ComposedChart + ReferenceArea |
| React Query hooks | `frontend/src/api/hooks.ts` | 新端點比照加 hook |
| 圖表卡 + 解讀號誌 | `frontend/src/components/ChartCard.tsx:21-23` | 傳 `chart` prop 才顯示號誌 |
| React Router | `frontend/src/App.tsx:16-32` | 三頁面路由比照；`/` 與舊 `/season` 轉向 `/overview` |
| Nav 項目 | `frontend/src/layouts/AppShell.tsx:5-14` | 三頁面入口加入 NAV_ITEMS |

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| trail 分類閾值錯置（平路爬坡跑被歸 trail 或反之） | M | M | 解析時用爬升率+坡度分佈而非單一閾值；保留手動覆寫 |
| hrTSS 缺 LTHR/HR 歷史值 | M | M | 缺值 fallback rTSS 或標 N/A；Open Question 確認 |
| climb_load 原創無法對 WKO5 驗證 | M | L | 未實作 climb_load；爬升能力只呈現爬升 + 整趟 VAM，validator 不含此項 |
| React 三頁未在 static shell 導覽出現 | M | L | 端點共用；只有跑 SPA（dev / Docker）才看得到三頁 |
| 分類 PATCH 後 analytics 不會自動失效快取 | L | L | trail 端點即時查 DB，無快取；PmcCache 不受分類影響 |
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

- [x] ~~climb_load 若採自訂公式，標記為原創、排除 AC-7 驗證集？~~ — 未實作；ClimbLoadChart 改畫 trail-summary 的爬升 + VAM
- [ ] hrTSS 所需 LTHR/HR 對歷史活動覆蓋率？目前缺值無 fallback（該日 hrTSS 視為 0）
- [ ] React 三頁是否要併入 static shell，或正式退場？
- [ ] facets 計數未套資料來源去重，雙來源時次數會偏高
- [ ] 總體頁跨運動（肌力、球類）無功率/配速者負荷如何估算？（可能延後）
