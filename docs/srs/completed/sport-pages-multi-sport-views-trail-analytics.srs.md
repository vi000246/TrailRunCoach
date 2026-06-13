---
linear_issue: null
---
# SRS: 多運動分流頁面、越野跑專屬圖表與圖表公式驗證

## Metadata
- **Module**: `sport-pages`
- **Module Spec**: `docs/spec/sport-pages.spec.md`
- **Source PRD**: `docs/prd/wko5-trail-multipage-sync-coach.prd.md`（Milestone 1 + 2）
- **Source Linear Issue**: N/A
- **Created**: 2026-06-13
- **Grill level**: 1
- **Plans**: `docs/plans/sport-pages-backend-foundation.plan.md`、`docs/plans/sport-pages-frontend-pages.plan.md`

## Feature Summary

新增一個 `sport-pages` 模組，把現有單一 `SeasonPage` 拆成三個彼此資料獨立的頁面（總體 / 跑步 / 越野跑），在解析時持久化 trail/road 分類欄位，為越野跑頁面提供爬升導向的專屬圖表（GAP、VAM、爬升負荷、越野 PMC），並對所有圖表加上「規則產生的白話解讀 + 狀態號誌」可讀性層；同時建立一套離線驗證工具，把計算結果對照 WKO5 逆向素材的權威公式。涵蓋 PRD 的 M1（圖表驗證 + 越野跑頁面）與 M2（三頁面分流 + 總體篩選）。

## Delta from Current Module State

> 既有架構詳見 `docs/spec/sport-pages.spec.md`。本節只描述變更。

### New / Changed API Endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/analytics/pmc?sports=trail_running,running&...` | 既有 PMC/run-load 端點擴充 `sports[]` 多選篩選參數（取代寫死 `sport=="running"`） | athlete |
| GET | `/api/v1/analytics/trail-load?athlete_id=` | 越野跑專屬 PMC：以 hrTSS 為主負荷、rTSS/NGP 並陳、含爬升負荷序列 | athlete |
| GET | `/api/v1/analytics/trail-summary?athlete_id=` | 越野跑頁聚合：本期總爬升、VAM 趨勢、爬升能力指標、PI 對照 | athlete |
| GET | `/api/v1/sports/facets?athlete_id=` | 回傳該運動員實際擁有的運動別清單（供總體頁 checkbox 動態產生） | athlete |
| GET | `/api/v1/analytics/chart-interpretation?chart=&athlete_id=` | 規則產生的白話解讀 + 狀態號誌（fresh/optimal/tired/overreached 等），AI 解讀為選配後續 | athlete |
| PATCH | `/api/v1/workouts/{id}/classification` | 手動覆寫單筆活動的 trail/road 分類 | athlete |

### New / Changed Data Models

- `WorkoutFile` 新增欄位 `trail_classification: str`（`road` / `trail` / `unknown`），解析時依爬升率與坡度統計計算寫入；可由 PATCH 端點手動覆寫（新增 `classification_overridden: bool`）。
- 既有 `sport` 正規化維持 4 類不變（running/cycling/...），trail 為 running 的子分類，避免破壞既有 sport 篩選。
- `PmcCache` 既有結構沿用；越野負荷與爬升負荷以新增 `metric_key`（如 `hr_tss`、`climb_load`）存入 `WorkoutMetric`，或擴充 cache key——細節留待 Plan。

### Changed Business Logic

- **Sport 篩選**：`backend/api/analytics.py` 現有寫死 `WHERE sport == "running"`（:168/:209/:275/:294）改為接受 `sports[]` 集合與 `trail_classification` 條件；三頁面以不同篩選組合呼叫同一批端點。
- **Trail 分類**：`backend/files/fit_reader.py:294-304` 正規化後，新增分類步驟（依 `elevation_gain_m`、距離、坡度分佈）寫入 `trail_classification`，取代目前 `workouts.py:238-241` 的查詢期 `elevation>100` 動態判斷。
- **可讀性層**：沿用 `SmartDashboardSection`（`frontend/.../SmartDashboardSection.tsx:4-79`）的 TSB 號誌+白話模式，抽成可重用的圖表解讀規則，套用到每個圖表。

### Explicitly Out of Scope

- M3 資料同步頁面、M4 AI 教練處方強化（各自獨立 SRS）。
- 單車 / 游泳專屬頁面（本次只做總體 / 跑步 / 越野跑三頁）。
- AI 即時生成圖表解讀的完整實作（本次只留端點與規則層；AI 為選配後續）。
- 寫回 TrainingPeaks / 上傳。

## Functional Requirements

- [ ] FR1：解析 FIT 時計算並持久化 `trail_classification`，可經 PATCH 手動覆寫。
- [ ] FR2：提供三個資料獨立的頁面——總體、跑步、越野跑——各頁圖表只含對應活動。
- [ ] FR3：總體頁提供多選 checkbox 活動篩選（動態依該運動員實有運動別產生），即時套用到該頁所有圖表；篩選僅作用於總體頁。
- [ ] FR4：越野跑頁提供爬升導向專屬圖表——越野 PMC（hrTSS 為主、rTSS 並陳）、爬升負荷、VAM/垂直速度趨勢、GAP，沿用既有 `trail.py` 計算。
- [ ] FR5：每個圖表附「白話解讀一句話重點 + 狀態號誌（紅/黃/綠等）」，由規則產生。
- [ ] FR6：建立離線驗證工具，把核心圖表計算（PMC、bike/run FTP、TSS、MMP、run-load）對照 WKO5 逆向素材的權威公式，輸出 pass/diff 報告。

## Non-Functional Requirements

| Category | Target | How Achieved |
|---|---|---|
| Performance | 頁面圖表載入 p95 < 1.5s（既有資料量 ~1100 筆） | 沿用 PmcCache / MmpCache；分類欄位避免查詢期重算 |
| Correctness | 核心圖表 100% 對照 WKO5 公式通過驗證 | 離線驗證工具 + 逆向素材常數比對 |
| Maintainability | 三頁面共用同一批端點與圖表元件，僅篩選參數不同 | sport[] 參數化、解讀規則抽成共用層 |
| Observability | 驗證報告可重跑、diff 可追溯 | 驗證輸出落檔 docs/reports/ |

## Architecture Notes

採「同端點 + 篩選參數化」而非「每頁一套端點」：三頁面差異只在 `sports[]` / `trail_classification` 篩選組合，最小化重複。Trail 分類採持久化欄位（解析時算、可手動覆寫），優於查詢期動態判斷的邊緣不穩。可讀性層抽自既有 `SmartDashboardSection`。詳見 `docs/spec/sport-pages.spec.md`。

## Acceptance Criteria

### AC-1: Trail 分類持久化與覆寫
- **Given**: 一筆爬升顯著的越野跑 FIT 被解析匯入
- **When**: 解析完成
- **Then**: 該活動 `trail_classification = "trail"`，且出現在越野跑頁、不出現在純跑步頁的 road-only 視圖
- **Test**: `tests/test_trail_classification.py::test_parse_sets_trail_classification`

### AC-2: 手動覆寫分類
- **Given**: 一筆被自動分類為 `trail` 的活動
- **When**: 呼叫 `PATCH /workouts/{id}/classification` 設為 `road`
- **Then**: `classification_overridden=true`，且重新解析不會覆蓋使用者的選擇
- **Test**: `tests/test_trail_classification.py::test_manual_override_persists`

### AC-3: 三頁面資料獨立
- **Given**: 同時有跑步、越野跑、單車、肌力活動
- **When**: 分別開啟總體 / 跑步 / 越野跑頁面
- **Then**: 跑步頁不含越野跑與單車；越野跑頁只含 trail 活動；總體頁預設含全部
- **Test**: `tests/test_sport_pages.py::test_page_activity_isolation`

### AC-4: 總體頁多選篩選
- **Given**: 在總體頁取消勾選「桌球」「肌力」
- **When**: 篩選套用
- **Then**: 該頁所有圖表即時排除桌球與肌力活動，且此篩選不影響跑步/越野跑頁
- **Test**: `tests/test_sport_pages.py::test_overview_multiselect_filter`

### AC-5: 越野跑負荷雙指標
- **Given**: 一筆有 HR 與 GPS/爬升的越野跑活動
- **When**: 查 `/analytics/trail-load`
- **Then**: 回傳以 hrTSS 為主的 PMC，並同時提供 rTSS/NGP 值供對照
- **Test**: `tests/test_trail_load.py::test_hr_tss_primary_rtss_alongside`

### AC-6: 圖表白話化
- **Given**: 任一已渲染圖表
- **When**: 頁面載入
- **Then**: 圖表附一句話白話重點與狀態號誌（顏色/標籤），由規則產生、無 LLM 呼叫
- **Test**: `frontend tests/chart-interpretation.test.tsx::renders_status_and_summary`

### AC-7: 公式驗證通過
- **Given**: WKO5 逆向素材記錄的權威公式常數（CTL τ=42、ATL τ=7、TSS 公式等）
- **When**: 對既有計算跑離線驗證工具
- **Then**: PMC、run/bike FTP、TSS、MMP、run-load 全數 pass；任何 diff 列入報告
- **Test**: `tests/test_formula_validation.py::test_core_charts_match_wko5`

## Open Questions

- [ ] 爬升負荷（climb_load）若採自訂公式，無 WKO5 素材可驗證——是否標記為「原創指標、不納入 AC-7 驗證集」？
- [ ] hrTSS 計算所需的 LTHR / HR zones 來源：沿用 `AthleteSettings.lthr` 是否對所有歷史活動都有值？缺值時 fallback？
- [ ] 總體頁跨運動（肌力、桌球）無功率/配速者的負荷如何估算（hrTSS？固定 TSS？手動）——可能延到後續。
