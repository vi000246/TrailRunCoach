# Implementation Report: sport-pages 前端（三頁面、篩選、越野圖表、白話化層）

## Summary

把單一 `SeasonPage` 拆成三個資料獨立頁面（總體 / 跑步 / 越野跑），更新側欄 nav 為中文三項，總體頁加多選 sport checkbox 篩選（state 局部於該頁），越野頁加爬升導向專屬圖表（越野 PMC：hrTSS 主 + rTSS 並陳、爬升負荷 + VAM），並為圖表加一層規則產生的「白話一句話 + 狀態號誌」可讀性層（ChartCard）。另補一支歷史 hrTSS 回填腳本，讓越野 PMC 立即有資料。

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Large | Large |
| Confidence | 8/10 | 單一 pass 完成 |
| Files Changed | ~12 | 9 created/updated + 1 deleted + 1 backfill |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| FE-1 | API 型別與 hooks | ✅ | axios `indexes:null` 修正 sports[] 序列化 |
| FE-2 | ChartCard 白話化外殼 | ✅ | 號誌圓點 + 一句話 |
| FE-3 | 三頁面路由與 nav | ✅ | /overview /running /trail；/season 重導 |
| FE-4 | 跑步頁內容 | ✅ | |
| FE-5 | 總體頁 + SportFilter | ✅ | 篩選 state 局部，不外溢 |
| FE-6 | 越野 PMC 圖表 | ✅ | hrTSS 主 + rTSS 並陳 |
| FE-7 | 爬升負荷圖 + 越野頁組裝 | ✅ | 總爬升/VAM |
| FE-8 | 清理 SeasonPage + 收尾 | ✅ | 移除 effect anti-pattern |
| 補 | 歷史 hrTSS 回填腳本 | ✅ | 計畫外、使越野 PMC 立即有資料 |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static Analysis (tsc) | ✅ Pass | `tsc -b` 零錯誤 |
| Lint (eslint) | ✅ Pass | 修正 set-state-in-effect |
| Build (vite) | ✅ Pass | 2531 modules，build 成功 |
| Integration | ✅ Pass | 後端啟動，6 新端點皆回應；分類回填 1686 筆 |
| Edge Cases | ✅ Pass | facets 空、trail 空、interpretation 失敗皆不崩 |

## Files Changed

| File | Action | Lines |
|---|---|---|
| `frontend/src/api/client.ts` | UPDATED | +~50（型別 + paramsSerializer） |
| `frontend/src/api/hooks.ts` | UPDATED | +~70（5 hook + sports 參數） |
| `frontend/src/components/ChartCard.tsx` | CREATED | +45 |
| `frontend/src/components/SportFilter.tsx` | CREATED | +45 |
| `frontend/src/pages/OverviewPage.tsx` | CREATED | +55 |
| `frontend/src/pages/RunningPage.tsx` | CREATED | +45 |
| `frontend/src/pages/TrailPage.tsx` | CREATED | +30 |
| `frontend/src/components/charts/TrailPmcChart.tsx` | CREATED | +65 |
| `frontend/src/components/charts/ClimbLoadChart.tsx` | CREATED | +70 |
| `frontend/src/components/charts/PmcChart.tsx` | UPDATED | +5（sports prop） |
| `frontend/src/components/charts/WeeklyLoadChart.tsx` | UPDATED | +5（sports prop） |
| `frontend/src/App.tsx` | UPDATED | 三路由 |
| `frontend/src/layouts/AppShell.tsx` | UPDATED | nav 三項 |
| `frontend/src/pages/SeasonPage.tsx` | DELETED | 拆分後移除 |
| `backend/scripts/backfill_hr_load.py` | CREATED | +60（計畫外） |

## Deviations from Plan

- **計畫外補 hrTSS 回填腳本**：整合測試發現越野 PMC 對歷史資料是空的（hr_tss 只在新匯入時寫）。因 `lthr=182` 已設且歷史活動有 `avg_hr_bpm`，補了 `backfill_hr_load.py` 以 avg-HR 近似回填（正是 plan Task 8 GOTCHA 預期的 fallback），使越野 PMC 立即有 311 點資料。TDD 完成，2 測試。
- **執行了資料回填（修改真實 DB）**：跑了 `backfill_classification`（1686 筆分類）與 `backfill_hr_load`（247 筆 hrTSS）使三頁面有真實資料。兩者皆 idempotent、additive、可重跑。
- **前端無測試框架**：以 `tsc -b` + `vite build` + 後端整合 smoke test 為 gate（balanced rigor 允許）。
- **axios sports[] 序列化**：預設 `sports[]=` 與 FastAPI 不符，改 `paramsSerializer: { indexes: null }` 產生 `sports=a&sports=b`。

## Issues Encountered

- ESLint `react-hooks/set-state-in-effect`：初版用 useEffect 初始化篩選 state；改為 render 期由 facets 衍生 `effectiveSelected`，移除 effect。

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_backfill_hr_load.py` | 2 | hrTSS 回填 + 無 lthr 跳過 |
| 前端 | 0 | 無測試框架；tsc + build + smoke 為 gate |

## AC Verification Map

> 對應 `docs/srs/sport-pages-multi-sport-views-trail-analytics.srs.md`（兩個 plan 合計）。

| AC | Description | Test / Evidence | Status |
|----|-------------|------|--------|
| AC-1 | Trail 分類持久化 | `test_import_sets_classification.py` + 回填 1686 筆 | ✅ Pass |
| AC-2 | 手動覆寫分類 | `test_classification_patch.py` + `useUpdateClassification` hook | 🟡 API+hook 就緒，尚無 UI 觸發按鈕 |
| AC-3 | 三頁面資料獨立 | smoke：trail-summary 僅 trail、run-load 僅 running；三頁面元件 | ✅ Pass |
| AC-4 | 總體頁多選篩選 | `SportFilter` + state 局部於 OverviewPage | ✅ Pass |
| AC-5 | 越野負荷雙指標 | `test_trail_load.py`；越野 PMC 311 點（hr_tss + r_tss） | ✅ Pass |
| AC-6 | 圖表白話化 | `ChartCard` + `/chart-interpretation` `test_interpretation.py` | ✅ Pass |
| AC-7 | 公式驗證通過 | `test_formula_validation.py` + 報告 | ✅ Pass |

## SRS Drift Check

- 🟢 Matches (6)：AC-1, AC-3, AC-4, AC-5, AC-6, AC-7 — 實作 + 證據齊備
- 🟡 Issues (1)：AC-2 手動覆寫——PATCH 端點與 `useUpdateClassification` hook 已就緒，但尚未在 UI（如 ActivityDetailPage）加觸發按鈕。屬小型 follow-up，不阻擋。
- 🔴 Unfulfilled (0)

## Next Steps
- [ ] （小）在活動詳情頁加「改分類 road/trail」按鈕串 `useUpdateClassification`（完成 AC-2 UI 側）
- [ ] 新活動同步後 hrTSS 走 per-second（已自動）；歷史用 avg-HR 近似（已回填）
- [ ] Code review via `/code-review`；PR via `/prp-pr`
- [ ] 之後：M3 同步頁面、M4 AI 教練處方（各自 SRS/plan）
