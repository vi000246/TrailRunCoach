# Implementation Report: sport-pages 後端基礎

## Summary

實作 `sport-pages` 模組的後端基礎：在 `WorkoutFile` 新增持久化 `trail_classification`（解析時計算、可手動覆寫）、把 analytics/pmc 端點的寫死 sport 篩選改為 `sports[]` 參數化（向後相容）、新增越野跑專屬端點（trail-load 以 hrTSS 為主、trail-summary、sports/facets、chart-interpretation 規則解讀、classification PATCH），並建立離線公式驗證器對照 WKO5 逆向素材。前端三頁面與圖表由 `sport-pages-frontend-pages.plan.md` 承接。

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Large | Large |
| Confidence | 8/10 | 單一 pass 完成，無重大障礙 |
| Files Changed | ~12 | 8 source + 11 test/report |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| 1 | trail_classification 欄位 + migration | ✅ Complete | |
| 2 | trail/road 分類純函式 | ✅ Complete | 閾值 20 m/km，可調 |
| 3 | 解析時寫入分類 | ✅ Complete | 抽 `_apply_classification` helper，尊重 override |
| 4 | 回填腳本 | ✅ Complete | idempotent，跳過 overridden |
| 5 | PATCH classification 端點 | ✅ Complete | 400/404 錯誤碼 |
| 6 | analytics sports[] 參數化 | ✅ Complete | 含 pmc.py；向後相容預設 |
| 7 | GET /sports/facets | ✅ Complete | 動態運動別 + 中文 label |
| 8 | hrTSS + trail-load 端點 | ✅ Complete | + compute_load_metrics 寫入管線 (8b) |
| 9 | trail-summary 端點 | ✅ Complete | 總爬升 + VAM |
| 10 | chart-interpretation 端點 | ✅ Complete | 規則 interpret_tsb |
| 11 | 公式驗證器 | ✅ Complete | 5 檢查全 pass，報告落檔 |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis | ✅ Pass | app 模組全部 import 成功 |
| Unit Tests | ✅ Pass | 53 passed（含既有 13 無回歸） |
| Build | N/A | Python 後端，無 build 步驟 |
| Integration | ✅ Pass | 6 個新路由皆註冊；app 載入成功 |
| Edge Cases | ✅ Pass | 缺資料→unknown、override 不被覆蓋、向後相容、缺 HR→0 |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `backend/db/models.py` | UPDATED | +2 |
| `backend/db/database.py` | UPDATED | +2 |
| `backend/engine/algorithms/classify.py` | CREATED | +30 |
| `backend/engine/algorithms/metrics.py` | UPDATED | +~55 (compute_hr_tss, compute_load_metrics) |
| `backend/engine/algorithms/interpret.py` | CREATED | +40 |
| `backend/engine/algorithms/validator.py` | CREATED | +75 |
| `backend/files/file_service.py` | UPDATED | +~30 (_apply_classification, load metrics) |
| `backend/api/analytics.py` | UPDATED | +~110 (_sport_clause, trail-load, trail-summary, interpretation) |
| `backend/api/pmc.py` | UPDATED | +3 (sports param) |
| `backend/api/sports.py` | CREATED | +42 |
| `backend/api/workouts.py` | UPDATED | +28 (PATCH classification) |
| `backend/main.py` | UPDATED | +2 (sports router) |
| `backend/scripts/backfill_classification.py` | CREATED | +45 |
| `backend/tests/*` | CREATED | 10 test files |

## Deviations from Plan

- **僅歸檔 plan，不歸檔 SRS/PRD**：本計畫只實作 SRS 的後端 AC（1/2/5/7 + AC-6 後端側）；AC-3/4/6 前端側由 `sport-pages-frontend-pages.plan.md` 承接，故 SRS 與 PRD 仍為 active，未移至 completed/。
- **測試 runner**：pytest 不在 requirements 也未裝進 venv，改用系統現成 `/opt/homebrew/bin/pytest`（python 3.12，含全部依賴）。未安裝任何套件。
- **Task 6 範圍微擴**：除計畫列的 run-load/intensity/run-volume/weekly，另把 `pmc.py` 的 general PMC 也參數化（總體頁跨運動篩選需要）。
- **Task 4 測試斷言修正**：model `default="unknown"` 在 INSERT 已套用，故 bike 列無變更，`updated==2` 而非 3（測試預期更正，非實作問題）。

## Issues Encountered

- FastAPI 直呼 handler 時，未提供的 `Query(None)` 預設值是 `FieldInfo` 物件而非 `None`；測試以 `sports=None` 模擬「省略參數」（即真實 HTTP 行為）解決。

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| test_classification_migration.py | 2 | 欄位 + migration idempotency |
| test_classify.py | 5 | 分類邊界 |
| test_import_sets_classification.py | 4 | 匯入寫入 + override |
| test_backfill_classification.py | 1 | 回填 idempotent |
| test_classification_patch.py | 3 | PATCH 200/400/404 |
| test_sports_filter.py | 4 | _sport_clause + 向後相容 |
| test_sports_facets.py | 2 | facets 計數 |
| test_hr_tss.py | 3 | hrTSS 公式 |
| test_trail_load.py | 1 | trail-load 篩選 + 雙指標 |
| test_load_metrics.py | 2 | hr_tss/r_tss 管線 helper |
| test_trail_summary.py | 1 | 聚合僅 trail |
| test_interpretation.py | 4 | 規則 + 端點 |
| test_formula_validation.py | 2 | WKO5 對照 |

## AC Verification Map

> 對應 `docs/srs/sport-pages-multi-sport-views-trail-analytics.srs.md`。AC-3/4/6 前端側由 Plan 2 承接。

| AC | Description | Test | Status |
|----|-------------|------|--------|
| AC-1 | Trail 分類持久化（解析時） | `test_import_sets_classification.py` / `test_classification_migration.py` | ✅ Pass |
| AC-2 | 手動覆寫分類 | `test_classification_patch.py` / `test_import_sets_classification.py::test_apply_respects_override` | ✅ Pass |
| AC-3 | 三頁面資料獨立 | 後端篩選就緒（`_sport_clause`、trail_classification）；頁面為前端 | 🟡 後端就緒，前端 Plan 2 |
| AC-4 | 總體頁多選篩選 | 後端 `sports[]` + `/sports/facets` 就緒；UI 為前端 | 🟡 後端就緒，前端 Plan 2 |
| AC-5 | 越野負荷雙指標 | `test_trail_load.py` / `test_hr_tss.py` | ✅ Pass |
| AC-6 | 圖表白話化 | 後端 `/chart-interpretation` `test_interpretation.py`；渲染為前端 | 🟡 後端就緒，前端 Plan 2 |
| AC-7 | 公式驗證通過 | `test_formula_validation.py` + `docs/reports/sport-pages-formula-validation.md` | ✅ Pass |

## Next Steps
- [ ] 部署後執行 `python -m backend.scripts.backfill_classification` 回填既有 ~1100 筆
- [ ] 實作前端 `sport-pages-frontend-pages.plan.md`（AC-3/4/6）
- [ ] Code review via `/code-review`；PR via `/prp-pr`
- [ ] hrTSS 需 `AthleteSettings.lthr`；歷史缺值之 fallback 待觀察（SRS Open Question）
