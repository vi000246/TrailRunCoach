# Implementation Report: 統一同步頁面 + 已載入資料盤點

## Summary

新增 `/sync/inventory` 後端端點（依來源/運動別筆數、日期範圍、各來源最後同步時間，零 schema 變更），並把 COROS-only 的 `CorosPage` 重構為統一 `SyncPage`：盤點面板 + COROS 同步區 + TrainingPeaks 同步區（接既有但未在 UI 暴露的 TP login/status/sync 後端）。`/coros` 重導 `/sync`，nav 改「同步」。

## Assessment vs Reality

| Metric | Predicted (Plan) | Actual |
|---|---|---|
| Complexity | Medium | Medium（偏小——TP 後端全部已存在） |
| Confidence | — | 單一 pass 完成 |
| Files Changed | ~8 | 4 created + 4 updated + 1 deleted |

## Tasks Completed

| # | Task | Status | Notes |
|---|---|---|---|
| M3-1 | /sync/inventory 端點 | ✅ | 純讀聚合，2 測試 |
| M3-2 | 前端型別 + hooks | ✅ | useSyncInventory/useTpStatus/useTpLogin |
| M3-3 | ProviderSyncCard | ✅ | 抽 CorosPage 帳號+log 為可重用 |
| M3-4 | SyncInventoryPanel | ✅ | |
| M3-5 | SyncPage 組裝 + 路由 + nav + 清理 | ✅ | /coros→/sync 重導 |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static (tsc) | ✅ Pass | `tsc -b` 零錯誤 |
| Lint | ✅ Pass | 新檔 eslint 乾淨 |
| Unit Tests | ✅ Pass | 57 passed（+2 新 test_sync_inventory） |
| Build (vite) | ✅ Pass | |
| Integration | ✅ Pass | `/sync/inventory` 回真實資料：約 1700 筆（coros + local 兩個來源）、日期範圍、COROS last-sync 時間、TP 從未 |

## Files Changed

| File | Action |
|---|---|
| `backend/api/sync.py` | UPDATED（+inventory 端點） |
| `backend/tests/test_sync_inventory.py` | CREATED |
| `frontend/src/api/client.ts` | UPDATED（SyncInventory 型別） |
| `frontend/src/api/hooks.ts` | UPDATED（3 hook） |
| `frontend/src/components/ProviderSyncCard.tsx` | CREATED |
| `frontend/src/components/SyncInventoryPanel.tsx` | CREATED |
| `frontend/src/pages/SyncPage.tsx` | CREATED |
| `frontend/src/App.tsx`, `layouts/AppShell.tsx` | UPDATED（路由 + nav） |
| `frontend/src/pages/CorosPage.tsx` | DELETED |

## Deviations from Plan

- **TP 後端比預期更完整**：盤點時發現 `/auth/tp/login`、`/auth/tp/status`、`/sync/start`(TP SSE) 與 `useSync` hook 全部已存在；M3 純為 UI 接線 + 盤點端點，無新後端能力。SRS 列的「新 TP 端點」其實是接既有。
- **零 schema 變更**：盤點純讀既有 `WorkoutFile`/`SyncState`。

## Issues Encountered

- 一次 `git add` 因含已 `git rm` 的 CorosPage pathspec 而整批失敗 → 拆成兩個 commit 補上 SyncPage/App/AppShell。

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| `backend/tests/test_sync_inventory.py` | 2 | 盤點分組 + 空 athlete |
| 前端 | 0 | 無框架；tsc + build + smoke |

## AC Verification Map

| AC | Description | Test / Evidence | Status |
|----|-------------|------|--------|
| AC-1 | 已載入資料盤點 | `test_sync_inventory.py` + smoke 回真實資料 | ✅ Pass |
| AC-2 | TP 同步端點接既有 client | 既有 `/sync/start`（tp_client.sync_workouts）+ SyncPage TP 卡接線 | ✅ Pass（接既有） |
| AC-3 | 同步頁三區塊 + /coros 重導 | SyncPage（盤點+COROS+TP）+ App.tsx redirect | ✅ Pass（build + 視覺） |
| AC-4 | 同步後盤點更新 | ProviderSyncCard `onSynced` → invalidate `['sync_inventory']` | ✅ Pass |

## SRS Drift Check
- 🟢 Matches (4)：AC-1～AC-4 皆有實作 + 證據
- 🟡 / 🔴：無

## Next Steps
- [ ] 首次實跑一次 TP 同步以驗證 token（last_sync.tp 目前為 null）——需使用者帳密，屬手動操作
- [ ] Code review / PR
