# Deploy Report: wko5coach

## Metadata
- **Deployed at**: 2026-06-13
- **Environment**: localhost:8000（Docker Compose，本機單機）
- **Mode**: LIVE
- **Deploy Type**: one-shot rebuild + 熱替換（docker compose up --build -d）
- **Status**: SUCCESS
- **Git commit deployed**: 37083ab（main，含 M1–M4 + Menu Bar 美化）

## Source Artifacts
- Deploy Guide: docs/deploy/wko5coach.deploy.md
- 本次部署涵蓋的功能：
  - sport-pages（M1+M2）：三頁面分流、越野圖表、trail 分類、圖表白話化、公式驗證
  - coros-sync（M3）：統一同步頁 + /sync/inventory + TP UI
  - ai-coach（M4）：knowledge module + zone 計算 + /ai/zones + 處方 prompt + quick-prompt
  - frontend hotfix：Menu Bar 美化
- 對應驗證 Linear issues：SP-386 ~ SP-392（WKO5逆向工程 專案）

## Pre-Deploy Checklist Status
| Item | Checked |
|---|---|
| 後端測試全綠（63 passed） | ✅ |
| 前端 tsc + vite build 綠 | ✅ |
| 所有功能已併入 main 並推送 | ✅ |
| Docker 運作中 | ✅ |
| DB volume (~/.wko5coach) 已含資料 + 回填 | ✅（1686 筆，分類/hrTSS 已回填）|

## Steps Executed
| # | Risk | Step | Command | Exit | Status |
|---|---|---|---|---|---|
| 1 | MEDIUM | 重建並熱替換容器 | `./deploy.sh`（docker compose up --build -d） | 0 | ✅ Done |

啟動時自動跑 idempotent `_migrate_schema()`；DB volume 持久化未受影響。Steps 2–4（bootstrap/scan）首次才需，本次略過（DB 已有 1012 local + 674 coros）。

## Verification Results
| Item | Result |
|---|---|
| `GET /api/v1/sports/facets` | ✅ 回真實運動別（running 688…） |
| `GET /api/v1/sync/inventory` | ✅ total 1686（coros 674 / local 1012） |
| `GET /api/v1/ai/zones`（新 M4 端點） | ✅ rFTP 200W、7 功率區 + 5 心率區 → 證明新 image 已上線 |
| `GET /api/v1/analytics/trail-load`（新 M1 端點） | ✅ 越野 PMC 311 點 |
| 前端 index | ✅ 載入正常（serve 新 build） |
| 容器狀態 | ✅ wko5coach Recreated + Started |

## Notes
- 新 image (`wko5reverse-wko5coach:latest`) 取代了原本已跑 11 天的舊容器（4 週前 image）。
- 順手修正部署指南內舊路徑 `…/Projects/WKO5reverse` → `…/Projects/Archive Project/WKO5reverse`。
- Source Linear Issue：本次為 whole-app 部署，非單一 plan；部署後人工驗證由 SP-386~392 追蹤，未綁定單一 issue 狀態。
- Rollback（若需要）：`git checkout <prev-sha>` 後 `./deploy.sh`；DB volume 不受 image 影響。
- 待人工驗證（需帳密/key/瀏覽器）：TP 同步（SP-388）、AI 處方品質（SP-389）、各頁面視覺（SP-386/387/390）。
