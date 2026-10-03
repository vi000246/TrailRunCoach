# Implementation Report: AI 教練知識驅動處方

## Summary

把 AI 教練升級為知識驅動處方：新增 curated 知識模組（Palladino 區間/間歇模板/CP/越野/使用者 profile）注入 system prompt、加 zone 計算（功率/心率邊界）與 `/ai/zones` 端點、把 zone 邊界加進 `build_context`、把 system prompt 改為處方導向（狀態判讀 + zone + 間歇處方），並在前端 chat 加 quick-prompt 按鈕。

## Assessment vs Reality

| Metric | Predicted | Actual |
|---|---|---|
| Complexity | Medium | Medium |
| Files Changed | ~8 | 4 created + 2 updated（後端）+ 1 updated（前端）+ 3 test |
| Schema 變更 | 無 | 無 |

## Tasks Completed

| # | Task | Status |
|---|---|---|
| M4-1 | zone 計算 + /ai/zones 端點 | ✅ |
| M4-2 | curated 知識模組 | ✅ |
| M4-3 | context + system prompt 強化 | ✅ |
| M4-4 | 前端 quick-prompt 按鈕 | ✅ |

## Validation Results

| Level | Status | Notes |
|---|---|---|
| Static (tsc) | ✅ Pass | |
| Lint | ✅ Pass | （AiChat 既有 `catch {}` no-empty 為前置，非本次新增） |
| Unit Tests | ✅ Pass | 63 passed（+3 ai 測試：zones/knowledge/context） |
| Build (vite) | ✅ Pass | |
| Integration | ✅ Pass | `/ai/zones` 回真實 zone：rFTP 220W → Threshold 198–231W、LTHR 160（範例跑者） |

## Files Changed

| File | Action |
|---|---|
| `backend/engine/ai/zones.py` | CREATED |
| `backend/engine/ai/knowledge.py` | CREATED |
| `backend/engine/ai/context.py` | UPDATED（system prompt + zones in context） |
| `backend/api/ai.py` | UPDATED（/ai/zones 端點） |
| `backend/tests/test_ai_zones.py`, `test_ai_knowledge.py`, `test_ai_context.py` | CREATED |
| `frontend/src/components/AiChat.tsx` | UPDATED（quick-prompt + send override） |

## Deviations from Plan

- 無重大偏差。`send` 依計畫 GOTCHA 改為可選參數 `send(override?)` 解決閉包問題；既有「送出」按鈕 onClick 同步改為 `() => send()`。

## Issues Encountered

- `send` 改簽章後，原 `onClick={send}` 把 MouseEvent 當 override 傳入 → TS2322；改 `() => send()` 解決。

## Tests Written

| Test File | Tests | Coverage |
|---|---|---|
| test_ai_zones.py | 3 | zone 邊界 + 缺值 + HR-only |
| test_ai_knowledge.py | 2 | 知識關鍵概念 |
| test_ai_context.py | 1 | context 含 zone + prompt 含知識/處方 |

## AC Verification Map

| AC | Description | Test / Evidence | Status |
|----|-------------|------|--------|
| AC-1 | zone 計算端點 | `test_ai_zones.py` + smoke（220W → 198–231W） | ✅ Pass |
| AC-2 | 知識注入 system prompt | `test_ai_knowledge.py` + `test_ai_context.py` | ✅ Pass |
| AC-3 | context 含 zone + 越野負荷 | `test_ai_context.py`（zone 已驗；越野負荷摘要初版以 zone 為主） | 🟢 zone 已驗；越野負荷為選配 |
| AC-4 | 前端 quick-prompt | AiChat 3 顆按鈕 + build 綠 | ✅ Pass（視覺） |

## SRS Drift Check
- 🟢 Matches (4)：AC-1～AC-4
- 🟡：AC-3 的「越野負荷摘要」初版以 zone 為主未加 hr_tss 總計（SRS 已標為選配）；不阻擋
- 🔴：無

## Next Steps
- [ ] 設定 AI key 後實測一輪處方品質（狀態判讀 + zone + 間歇）——需使用者 key
- [ ] 筆記框架若更新，手動同步 `knowledge.py`（SRS Open Question）
- [ ] Code review / PR
