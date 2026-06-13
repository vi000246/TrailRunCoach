---
linear_issue: null
---
# SRS: 統一同步頁面 + 已載入資料盤點（TP 同步接上 UI）

## Metadata
- **Module**: `coros-sync`
- **Module Spec**: `docs/spec/wko5-coros-sync.spec.md`
- **Source PRD**: `docs/prd/wko5-trail-multipage-sync-coach.prd.md`（Milestone 3）
- **Source Linear Issue**: N/A
- **Created**: 2026-06-13
- **Grill level**: 1

## Feature Summary

把現有 COROS-only 的同步頁擴成統一「同步」頁：接上**既有但未在 UI 暴露的 TrainingPeaks 下載**（`tp_client.py` 已驗證可下載+解析 base64-gzip FIT）、新增一個**已載入資料盤點**面板（依來源/運動別筆數、日期範圍、各來源最後同步時間），讓使用者一頁完成同步並看到「哪些資料進到圖表」。

## Delta from Current Module State

> 既有架構詳見 `docs/spec/wko5-coros-sync.spec.md`。本節只描述變更。COROS 與 TP client 後端皆已存在；本功能主要補 UI 暴露與盤點端點。

### New / Changed API Endpoints

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/sync/inventory?athlete_id=` | 已載入資料盤點：依 source（coros/local/tp）與 sport 的筆數、總筆數、workout_date min/max、各來源 last-sync 時間 | athlete |
| POST | `/api/v1/sync/tp/start?athlete_id=&since=` | 觸發 TP 下載同步（SSE 串流進度，比照既有 `/sync/coros/start`）— 接既有 `tp_client.sync_workouts` | athlete |
| POST | `/api/v1/auth/tp/login` | TP 帳密登入取得 OAuth token（若尚未在 UI 暴露；後端 `login_password` 已存在） | athlete |
| GET | `/api/v1/auth/tp/status?athlete_id=` | TP 連線狀態（token 是否有效、athlete 是否可下載） | athlete |

### New / Changed Data Models

無 schema 變更。盤點端點純讀既有 `WorkoutFile`（source/sport/workout_date）與 `SyncState`（`coros_last_sync_at`、`last_sync_at`=TP）。TP token 欄位已存在於 `SyncState`（`tp_access_token` 等）。

### Changed Business Logic

- 既有 `tp_client.sync_workouts`（已驗證）接到新的 `/sync/tp/start` SSE 端點（比照 `sync.py` 既有 coros 串流）。
- 前端 `CorosPage` 重構為 `SyncPage`：COROS 區塊 + TP 區塊（登入 + 同步 + log）+ 盤點面板。`/coros` 路由保留重導到 `/sync`。

### Explicitly Out of Scope

- 寫回 / 上傳到 TrainingPeaks（PRD 已明確排除）。
- 自動排程同步（cron / 背景）——本功能只做手動觸發。
- intervals.icu 中介路線（研究列為後備，本次不實作）。
- 重新逆向 TP 格式——已驗證為 base64-gzip FIT，直接沿用既有 `_decode_filedata_response`/`_inflate_any`。

## Functional Requirements

- [ ] FR1：同步頁顯示「已載入資料盤點」——總筆數、依來源（coros/local/tp）與依運動別的筆數、workout_date 範圍、各來源最後同步時間。
- [ ] FR2：使用者能在同一頁觸發 TrainingPeaks 同步（帳密登入 → 下載 → 解析），並看到即時進度 log（比照 COROS）。
- [ ] FR3：使用者能在同一頁觸發 COROS 同步（沿用既有）。
- [ ] FR4：同步完成後盤點面板能反映新載入的資料（重新查詢）。
- [ ] FR5：`/coros` 舊路由重導到新 `/sync`；nav 顯示「同步」。

## Non-Functional Requirements

| Category | Target | How Achieved |
|---|---|---|
| Reliability | TP/COROS 任一失敗不影響另一；token 失效有清楚錯誤 | 各自 try/except + 既有 401/refresh 流程 |
| Observability | 同步進度即時可見、可診斷 | 既有 SSE log 串流模式 |
| Performance | 盤點端點 p95 < 500ms | 單表 group-by 聚合（既有 ~1700 筆） |
| Security | TP/COROS 帳密只用於取 token，不落明文於前端 | 沿用既有 SyncState token 儲存 |

## Architecture Notes

沿用既有 SSE 同步串流架構（`sync.py` 的 coros pattern）與 token 儲存（`SyncState`）。盤點端點是純讀聚合，與 `/sports/facets` 同類但多了 source 維度與 sync 時間。前端把 CorosPage 的 live-log 元件抽成可重用，COROS/TP 共用。詳見 `docs/spec/wko5-coros-sync.spec.md`。

## Acceptance Criteria

### AC-1: 已載入資料盤點
- **Given**: DB 內有 coros 與 local 來源的活動
- **When**: 呼叫 `GET /api/v1/sync/inventory?athlete_id=1`
- **Then**: 回傳總筆數、依 source 的筆數（coros/local/...）、依 sport 的筆數、workout_date 的 min/max，以及各來源 last-sync 時間
- **Test**: `backend/tests/test_sync_inventory.py::test_inventory_groups_by_source_and_sport`

### AC-2: TP 同步串流端點存在且接上既有 client
- **Given**: 已存在有效 TP token
- **When**: 呼叫 `POST /api/v1/sync/tp/start`
- **Then**: 回傳 SSE 串流，事件含 started / downloaded / complete，並實際呼叫 `tp_client.sync_workouts`
- **Test**: `backend/tests/test_tp_sync_endpoint.py::test_tp_start_streams_progress`（可 mock client）

### AC-3: 同步頁三區塊呈現
- **Given**: 開啟 `/sync` 頁面
- **When**: 頁面載入
- **Then**: 顯示（a）盤點面板（b）COROS 同步區（c）TP 同步區；`/coros` 重導到 `/sync`
- **Test**: 前端視覺驗證（無測試框架，build + 手測）

### AC-4: 同步後盤點更新
- **Given**: 盤點顯示 N 筆
- **When**: 完成一次同步並有新活動
- **Then**: 重新查詢盤點，總筆數與對應來源筆數增加、last-sync 時間更新
- **Test**: 前端 React Query invalidate `['sync_inventory']` 後重抓

## Open Questions

- [ ] TP token 目前是否仍有效？（last_sync_at=None 表示從未成功跑過一次 TP 同步）——首次 UI 觸發時驗證；失效則走帳密重登。
- [ ] 盤點是否要顯示「未進圖表」的活動（如缺 TSS / 無功率者）？初版只顯示總覽，細分留後續。
