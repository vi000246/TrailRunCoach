# 同步手動驗證清單（COROS + TrainingPeaks）

分支：`feat/tis-dashboard`。自動測試（`backend/tests/test_sync_e2e.py`、`test_sync_settings.py`）只用假造的 HTTP，從不連真實帳號。所以真實帳號的端對端驗證要你自己跑一次，步驟如下。

## 0. 準備

- [ ] 在 worktree 啟動後端：`uvicorn backend.main:app --port 8000`。啟動時會自動執行 migration：新增 `workout_files.start_time_utc` / `duplicate_of` 兩個欄位和 `user_settings` 表。只加欄位與表，不刪資料。
- [ ] 設定時區（本地日期要用）：
  `curl -X PUT localhost:8000/api/v1/sync/settings -H "Content-Type: application/json" -d "{\"timezone\":\"Asia/Taipei\"}"`
- [ ] 視需要設定主要來源（兩邊都同步時，同一次活動只算這個來源；不設就以先匯入的為主）：
  `... -d "{\"primary_source\":\"coros\"}"`
- [ ] 如果 DB 裡已有舊資料，先補開始時間：
  `python -m backend.scripts.backfill_start_time`（dry run，看數量）→ 確認後加 `--apply`。

## 1. COROS

- [ ] 在 UI「同步」頁登入 COROS（或 `POST /api/v1/auth/coros/login`）。回應要有 `authenticated: true`、`region`。
- [ ] 第一次同步最近 30 天：`POST /api/v1/sync/coros/start?since=YYYY-MM-DD`（填 30 天前）。
  - 事件依序為 `started`、每筆 `checking` → `downloaded`，最後是 `complete`，且 `errors` 應為 `[]`。
  - 有 `error` 時記下 `activity_id` 和訊息。有錯時 cursor 不會前進，下次會重試。
- [ ] 再按一次同步，不帶 `since`：`started.since` 應該是「今天 − 3 天」，每筆都是 `already_imported`，沒有 `downloaded`。
- [ ] `GET /api/v1/sync/inventory`：`by_source.coros` 的筆數應該等於 COROS app 裡這段期間的活動數。
- [ ] 抽 2–3 筆清晨（08:00 前）的活動：活動列表上的日期應該是台灣當天，不是前一天。
- [ ] 抽一筆沒有功率的跑步，確認它有 `hr_tss`（活動詳細資料裡看得到）。

## 2. TrainingPeaks

注意：這個 client 冒用 WKO5 的 client_id / User-Agent，有違反 TP 服務條款的風險，請自行評估。帳號必須是 premium 或 coach 才能下載檔案。

- [ ] 登入 TP（`POST /api/v1/auth/tp/login`）。回應的 `can_download` 應為 true。
- [ ] 同步：`POST /api/v1/sync/start?since=YYYY-MM-DD`（同一段 30 天）。
  - 以前遇到 401/403/404 會被當成 `no_file` 默默略過，現在會是 `error`，而且 `SyncState.last_sync_cursor` 不前進。
  - `no_file` 只代表該課本來就沒有裝置檔（例如手動輸入的課）。
- [ ] 登入超過 1 小時後再同步一次，確認不會出現 500 / TypeError（token 會自動 refresh）。
- [ ] 再同步一次：全部都是 `already_imported`。

## 3. 重複與一致性

- [ ] `GET /api/v1/sync/inventory` 的 `duplicates` 應該約等於兩邊都有的活動數。
- [ ] PMC（總覽的 CTL/ATL/TSB）不應該因為同步了第二個來源而跳升。比對方法：同步 TP 前記下今天的 CTL，同步後應該不變或只有小差異。
- [ ] 跑一致性報告：
  `python -m backend.scripts.compare_sources --since YYYY-MM-DD --athlete-dir "<你的 WKO5 athlete 資料夾>" --csv compare.csv`
  - 「COROS vs TrainingPeaks」：配對筆數應該接近兩邊共有的活動數。
  - 標 `!!` 的列代表超過容差：時長 / 距離 2%、爬升 10%、功率 / NP 3%、HR 2%、TSS 5%。
  - 最常見的差異是爬升（裝置 `total_ascent` vs WKO5 平滑後的 elevation）。同步列沒有 TSS（匯入不再存 TSS，2026-10-08）；TSS 的比對看 app 內的資料來源比對（`GET /api/v1/sync/compare`）。
  - `only coros` / `only tp` 清單：確認是否真的只存在於一邊。例如 TP 裡的手動課沒有 FIT，就只會出現在 TP。
- [ ] 把 `primary_source` 切換成另一邊（`PUT /api/v1/sync/settings`），`dedup.duplicates` 數量應該不變，只是改由另一邊為主。

## 回報

有異常時，把以下資料交給開發者：
- SSE 事件中的 `error` 列
- `compare.csv`
- `GET /api/v1/sync/inventory` 的輸出

## 已知、尚未處理

- COROS token 效期 24 h，沒有 refresh grant，過期要重新登入。同步會回傳 `COROS_AUTH_REQUIRED` 事件。
- WKO5 對齊的圖表讀的是 WKO5 資料夾裡的 `.wko4`，不是 SQLite 裡的同步資料。所以同步進來的 COROS 活動目前還不會出現在 WKO5 圖表上；這需要另外一個「FIT → Dataset」的整合階段。
- token 目前仍以明文存在 SQLite。之後的使用者設定階段會改成 Fernet 加密。
