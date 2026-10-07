# Actual Budget 的本機儲存與跨裝置同步：能不能拿來同步 FIT（SP-307）

- **查證日期：2026-10-07。** Actual Budget 的原始碼看的是 GitHub `actualbudget/actual` 的 main（commit `1ad8477`，2026-10-06）。我把它淺層 clone 到本機後離線閱讀，沒有登入或呼叫任何 Actual 伺服器。
- **標記方式：**
  - **已驗證**：我在原始碼或官方文件裡讀到。
  - **摘要**：整理自官方或二手文件，沒有逐行對照程式碼。
  - **推估**：我的推論或粗估。
  - **未找到來源**：查不到。
- **擁有者的資料**：只做唯讀量測（NAS 容器 `trailruncoach` 的 `/data`），文件裡只放總數和大小。沒有讀 `secret.key`、token 或 `.env`。
- **相關文件：**
  - `docs/plans/todo-multi-user-sharing.plan.md` §1(c) 純瀏覽器 PWA、§10 免費離線版加付費同步版。
  - `docs/plans/auth-and-demo.plan.md` §2.2 每人一個 SQLite、§10 雲端儲存與 `FitStore`。
  - 本文件不推翻這兩份已經決定的事。它補上「多台裝置之間怎麼同步」這一塊。

---

## 0. 一句話結論

Actual 的同步做法很適合 TrailRunCoach 的**小筆、使用者自己改的資料**，例如設定、活動標記、課表修改、傷病紀錄。這些資料總共不到 1 MB。

它**不適合 FIT 原檔和衍生資料**：
- FIT 是幾百 KB 到幾 MB 的二進位檔，寫一次就不再改，應該當成「用內容雜湊命名、只傳一次的不可變物件」來同步。
- 衍生資料（逐秒通道、PMC、門檻、圖表快取）不該同步，應該在算得動的地方重算。

**建議走「混合式」**：
- 伺服器仍是 FIT 和運算的中心。
- FIT 改成用雜湊命名的物件存放。
- 只有小筆資料採用「變更紀錄」同步，讓裝置可以離線改、上線再合併。
- **先不做端對端加密。** 一旦加密，伺服器就不能幫忙算模型、自動調課表、推課表到 COROS，而這些正是本 app 的核心功能。

---

# 第一部分：現況

## 1. Actual Budget 怎麼存資料

### 1.1 裝置上（客戶端）

| 平台 | 怎麼存 | 標記 |
|---|---|---|
| 瀏覽器版 | SQLite 編成 WebAssembly（`@jlongster/sql.js`）。資料庫的分頁透過 `absurd-sql` 存進 **IndexedDB**。`.sqlite` 檔在虛擬檔案系統裡是一個 symlink，指到 `/blocked/<id>`，真正的內容在 IndexedDB（`packages/loot-core/src/platform/server/fs/index.ts`、`sqlite/index.ts`） | 已驗證 |
| 桌面版（Electron） | 本機磁碟上的一般 SQLite 檔，用 `better-sqlite3` 讀寫（`sqlite/index.electron.ts`） | 已驗證 |
| 執行位置 | 客戶端的「server」程式碼（loot-core 的 `src/server/`）在瀏覽器裡跑在背景 worker。名字叫 server，其實是裝置上的資料層 | 摘要 |

- **一份預算（budget file）**就是一個資料夾，裡面有 `db.sqlite` 和 `metadata.json`。上傳到伺服器時打包成 zip（`cloud-storage.ts` 的 `exportBuffer`）。已驗證。
- 上傳前會先清掉衍生快取表 `kvcache`、`kvcache_key`。程式註解寫明：「We NEVER upload the cache with the database; this forces new downloads to always recompute everything」。已驗證。
- 資料表是一般的關聯式資料表：`accounts`、`transactions`、`categories`、`payees`、`rules`、`schedules` 等。主鍵是字串 id（UUID）。刪除不是真的刪，而是把 `tombstone` 欄設成 1（`src/server/sql/init.sql`、`migrations/*.sql`、`sync/reset.ts`）。已驗證。
- 和同步有關的兩張表：
  - `messages_crdt (timestamp UNIQUE, dataset, row, column, value)`：每一次欄位修改的完整歷史。
  - `messages_clock`：本機時鐘加上 merkle 樹。
  - 兩張都已驗證（`init.sql:73-85`）。

### 1.2 伺服器上（`packages/sync-server`）

| 檔案 | 內容 | 標記 |
|---|---|---|
| `account.sqlite` 的 `files` 表 | 每份預算一列：`id`、`group_id`（同步 id）、`encrypt_salt`、`encrypt_keyid`、`encrypt_test`、`encrypt_meta`、`name`、`owner`、`deleted` | 已驗證（`migrations/1694360479680-create-account-db.js`、`1719409568000-multiuser.js`） |
| `user-files/file-<fileId>.blob` | 最近一次上傳的整份 zip。啟用加密時，整份 zip 是加密的 | 已驗證（`util/paths.ts`、`app-sync.ts:298-367`） |
| `user-files/group-<groupId>.sqlite` | `messages_binary (timestamp PK, is_encrypted, content)` 加上一列 `messages_merkles` | 已驗證（`sql/messages.sql`、`sync-simple.js`） |

- **伺服器不懂預算內容。** `/sync` 只做三件事（`sync-simple.js`）：
  1. 把收到的訊息 `INSERT OR IGNORE` 存起來。
  2. 更新 merkle 樹。
  3. 回傳 `since` 之後的所有訊息。

  它不會把訊息套用到任何資料庫，也沒有算餘額或報表的程式。已驗證。
- 訊息在伺服器上**永遠不刪**，要等使用者「重設同步」才會整份清掉（`/reset-user-file` 刪掉 `group-*.sqlite`）。已驗證。
- 上傳上限（`load-config.js`）都可以用環境變數調整。已驗證。
  - 同步請求預設 20 MB；加密的同步請求 50 MB。
  - 一般上傳 20 MB。
- 整份 zip 快照**每 7 天**最多上傳一次（`UPLOAD_FREQUENCY_IN_DAYS = 7`，`possiblyUpload`）。重設同步或第一次註冊時也會上傳。已驗證。

### 1.3 擁有者自己的 Actual 是怎麼跑的（`~/Projects/actual-budget-automation`，只讀說明文件）

- NAS 上除了 Actual server，還跑了 `actual-http-api` 和 `actual-ai`。兩者都內含 `@actual-app/api`，也就是**無畫面的 Actual 客戶端**：
  1. 先把整份預算下載下來。
  2. 在 Node 裡跑 loot-core。
  3. 讀寫完再同步回去。

  dashboard 透過 REST 讀它。已驗證（該 repo 的 `CLAUDE.md`、`README.md`）。
- 該 repo 記了一個實際踩過的坑：「每個 `@actual-app/api` 客戶端的版本都必須 ≥ server 版本」。新版客戶端打開或重設同步時會把資料庫遷移到新版本，舊客戶端就讀不了（`out-of-sync-migrations`）。已驗證。
- **這對本研究的意義：**
  - 在 Actual 的架構下，「伺服器端自動做事」得另外養一個握有完整資料（和加密密碼）的客戶端。
  - 每台裝置的 schema 版本都要一起升級。

## 2. Actual Budget 怎麼同步

### 2.1 變更怎麼記錄：每個欄位一則訊息

- **一則訊息是 `(dataset, row, column, value)` 加一個時間戳**，也就是「某張表、某一列、某一欄，改成什麼值」（`packages/crdt/src/proto/sync.proto` 的 `Message`）。已驗證。
- **新增一列**就是對每個欄位各送一則訊息。**刪除**就是把 `tombstone` 欄改成 1。已驗證（`sync/index.ts` 的 `apply`：先查這一列在不在，在就 `UPDATE`，不在就 `INSERT (id, column)`）。
- **合併規則：每個欄位各自「最後寫入者勝」**（LWW，last-writer-wins）。已驗證（`compareMessages`，`sync/index.ts:282-364`）。
  - 收到一則訊息時，先查同一個 `(dataset,row,column)` 在本機紀錄裡有沒有時間更晚的訊息。
  - 有的話，這則標成 `old`：不套用，但仍寫進紀錄和 merkle 樹，這樣兩邊的雜湊才會一致。
  - 沒有的話就套用。
  - 因為比的是單一欄位，兩台裝置改同一筆交易的**不同欄位**會被合併（例如一台改金額、一台改分類，兩個改動都留下）。改**同一欄**時，時間戳晚的贏。
- **遇到看不懂的訊息**（新版客戶端加的資料表或欄位）：先暫存到 `messages_pending`，等升級後再套用（`deferMessage`）。已驗證。

### 2.2 時間戳：混合邏輯時鐘（HLC）

- 格式是 `2015-04-24T22:23:42.123Z-0000-0123456789ABCDEF`：
  - 前段是實體時間（毫秒）。
  - 中段是 16 進位計數器（最大 `0xFFFF`）。
  - 末段是 16 字元的裝置 id。

  這樣的字串可以直接排序，而且全域唯一（`packages/crdt/src/crdt/timestamp.ts`）。已驗證。
- 送出或收到訊息時都會推進本機時鐘。超過實體時間 5 分鐘就丟出 `ClockDriftError`（`maxDrift: 5 * 60 * 1000`）。已驗證。
- 依據是 HLC 論文（Kulkarni 等，2014），檔頭有附連結。摘要。

### 2.3 找出差異：merkle 樹

- 每則訊息的時間戳會用 murmurhash 算成一個數字，再插進一棵**三進位字典樹**（`merkle.ts`）。已驗證。
  - 樹的路徑是「訊息發生在第幾分鐘」的三進位表示。
  - 每個節點存子樹所有雜湊的 XOR。
- 同步時，伺服器會回傳它的樹。客戶端用 `merkle.diff` 從根往下找第一個雜湊不同的分支，得出「從哪一分鐘開始不一致」，再從那個時間點重新同步一次。已驗證。
- 為了控制大小，每層只保留最近 2 個分支（`prune(trie, n = 2)`）。所以差異點只是大概的位置，可能要來回好幾輪。
  - 程式碼最多試 100 輪。
  - 同一個差異點連續 10 次都對不齊，就丟出 `out-of-sync`。

  已驗證（`_fullSync`，`sync/index.ts:878-1049`）。

### 2.4 一次同步的流程

1. 客戶端取出本機 `messages_crdt` 裡 `timestamp > since` 的訊息。`since` 是上次對齊的時間，沒有的話用 5 分鐘前。
2. 用 protobuf 打包（`SyncRequest`），POST 到 `/sync`。
3. 伺服器存下新訊息，回傳 `since` 之後別人送的訊息和自己的 merkle 樹。
4. 客戶端套用收到的訊息，比對 merkle：
   - 一樣：記下 `lastSyncedTimestamp`，同步結束。
   - 不一樣：從差異點再來一輪。

已驗證。本機有改動時，大約 1 秒後觸發同步（`FULL_SYNC_DELAY = 1000`）。已驗證。

### 2.5 新裝置怎麼拿到完整資料

1. 先下載最近一次的整份 zip 快照（`/download-user-file`）。有加密的話，要輸入密碼才能解開。
2. 匯入成本機的 `db.sqlite`。
3. 從快照的 `lastSyncedTimestamp` 開始，把之後的訊息補齊（`downloadBudget` 接著呼叫 `initialFullSync`）。

已驗證。所以新裝置不需要重播全部歷史，只要「快照加上之後的訊息」。快照最多 7 天才更新一次，所以最多補 7 天左右的訊息（推估）。

### 2.6 重設同步

- 重設同步的步驟：
  1. 清掉伺服器上的訊息紀錄。
  2. 把本機刪掉 `tombstone=1` 的列、清空 `messages_crdt`。
  3. 把這台裝置的資料當成「正確版本」重新上傳。
  4. 換一個新的 `groupId`。

  已驗證（`sync/reset.ts`、`/reset-user-file`）。
- 其他裝置下次同步時會被告知要「還原到最新版」，也就是丟掉本機資料、重新下載。**沒同步上去的改動會不見。** 已驗證（官方文件 `getting-started/sync.md`）。
- 官方文件說重設也會讓檔案「明顯變小」，因為歷史訊息被壓成一份快照。已驗證（官方文件）。

### 2.7 端對端加密

| 項目 | 做法 | 標記 |
|---|---|---|
| 金鑰 | 使用者另設一組密碼。用 PBKDF2-SHA512 跑 **10,000 次**、32 bytes 隨機 salt，導出 AES-256 金鑰 | 已驗證（`encryption-internals.ts`、`encryption/app.ts`） |
| 加密對象 | **每一則同步訊息**各自用 AES-256-GCM 加密（12 bytes 隨機 IV），以及**整份 zip 快照** | 已驗證（`sync/encoder.ts`、`cloud-storage.ts` 的 `upload`） |
| 確認密碼對不對 | 伺服器存一則用金鑰加密的測試訊息（`encrypt_test`）和 salt。新裝置輸入密碼後試著解它 | 已驗證 |
| 金鑰放哪 | 裝置本機（`asyncStorage` 的 `encrypt-keys`）。伺服器沒有 | 已驗證 |
| 啟用或換金鑰 | 一定會觸發重設同步：伺服器忘掉舊的未加密資料，重新上傳。官方文件說加密**無法關閉** | 已驗證 |
| 忘記密碼 | 本機也沒有資料的話，**永遠救不回來** | 已驗證（官方文件） |
| 裝置上的資料 | 不加密。官方建議開全磁碟加密 | 已驗證（官方文件） |
| 疊代次數 | 10,000 次遠低於 OWASP 目前對 PBKDF2-SHA512 的建議（約 21 萬次） | 摘要（OWASP Password Storage Cheat Sheet） |

**伺服器看得到什麼：**
- 每則訊息的**時間戳**，也就是什麼時候改了東西、改了幾筆。
- 訊息大小。
- 預算名稱（上傳時的 `X-ACTUAL-NAME` header、`files.name` 欄）。
- `groupId`。
- 銀行同步的 token。

**看不到的：** 內容，包括哪張表、哪一列、改成什麼值。已驗證。

官方文件明說，銀行同步的 token「not covered by end-to-end encryption」。已驗證。

### 2.8 和本 app 最像的一段：銀行同步

- Actual 的「銀行同步」流程：
  1. **客戶端**請伺服器呼叫銀行 API（`GOCARDLESS_SERVER + '/transactions'` 等，`src/server/accounts/sync.ts`）。
  2. 伺服器保管銀行 token，把交易回給客戶端。
  3. 客戶端把交易寫進本機資料庫，再變成同步訊息發出去。

  已驗證。
- 這跟「COROS 同步」是同一種問題：外部資料源的帳號在伺服器上，資料**一定會以明文經過伺服器**。端對端加密只保護「之後存放」那一段，保護不到「從外部拿進來」那一段。推估。

## 3. TrailRunCoach 現在怎麼存、怎麼算

### 3.1 多租戶

- `backend/tenancy.py`：每個租戶是一個 `Tenant`，有 `root`（可寫）和 `shared`（FIT、快取）兩個目錄，用 `ContextVar` 決定目前是誰。DB 是 `root/wko5coach.db`，**每人一個 SQLite 檔**（`auth-and-demo.plan.md` §2.2 已決定）。
- 租戶種類：`owner`、`user`（還沒實作）、`demo_base`、`demo_sandbox`。
- `backend/tenancy_mw.py`：只在示範模式生效。用 cookie 找沙盒、檢查 CSRF、寫入白名單和限流，第一次寫入時建立複本沙盒。
- 現在沒有 app 層的登入，`/api/v1/session` 回 `user: null`。正式登入的設計在 `auth-and-demo.plan.md` §4–5（`accounts.db` 加每人一個目錄）。
- DB 連線池依 DB 檔分，最多 32 個（`backend/db/database.py`），開 WAL。

### 3.2 資料分四類

| 類別 | 內容 | 位置 |
|---|---|---|
| **使用者改的小筆資料** | `activity_tags`（類型、強度、備註、標籤、疼痛、排除）、`plan_sessions` 的手動修改（有 `edited`／`origin` 旗標）、`injury_events`、`athlete_settings`（依日期的門檻）、`user_settings`（鍵值）、`workout_templates_user`／`workout_template_cats`、`race_calc`、`event_gpx` 的中繼資料 | `wko5coach.db`（`backend/db/models.py`） |
| 同上，但是整份 JSON 檔 | `plan.json`（賽事、分期、門檻）、`engine.json`、`corrections.json`、`annotations.json`、`views/`、`racepower_solo_hikes.json` 等 | 租戶目錄 |
| **匯入的原始資料** | FIT 檔：`<shared>/fit/{coros,tp}/<年>/<labelId>_<日期>_<運動>.fit`（`backend/sync/storage.py`、`coros_client.py`）。`workout_files` 表記中繼資料，裡面混了少數使用者欄位（分類覆寫、RPE） | `shared/fit/`、DB |
| **衍生資料** | 逐秒通道 `cache/fit/<…>/ch/*.npz`（float64）、`workout_metrics`、`mmp_cache`、dataset 的 JSON 快取（`tp_tss.json`、`series_*.json`…）、`workout_review` 快取、`cache/render/` 圖表快取（LRU 上限 300 MB）、`achievements_cache.json`、`racepower_*.json`、`routes/` | `shared/`、DB |
| **只能在伺服器上的機密** | `sync_state`（COROS token、加密封存的密碼，用 `secret.key` 加密） | DB |

- `pmc_cache` 表有定義，但 `models.py` 以外沒有程式碼用它，看起來沒在用。實測 0 列。
- FIT **沒有內容雜湊**：
  - COROS 用 `coros_activity_id` 判斷有沒有下載過。
  - 跨來源（COROS 和 TP 是同一筆活動）用「開始時間差 2 分鐘內」判斷重複（`backend/sync/dedup.py`）。
- FIT 解析用純 Python 的 `fitparse`／`fitdecode`。所有模型都是純 Python 加 numpy，沒有 scipy／pandas。`backend/engine` 共約 8.1 萬行，`backend` 全部約 11.2 萬行（不含測試）。

### 3.3 伺服器自動做的事

- **COROS 同步**（`backend/sync/coros_client.py`、`runner.py`、`scheduler.py`）。觸發方式有三種：
  - app 啟動時開一個 asyncio 迴圈，每天定時跑。
  - 「立即同步」按鈕。
  - 打開頁面時的自動同步（`static/autosync.js`）。

  同步會下載新的 FIT、匯入，並讀 COROS 的自評和訓練負荷。
- **同步之後**接著跑：
  1. `plan_auto`：對完成或漏掉的課、調整、重產未來幾週、推下 N 天的課表到 COROS。
  2. 時區更新。
  3. 校正。
  4. dataset 預熱。

  `plan_auto` 不會改 `edited`／`custom` 的課，大改動會等使用者核准，每次改動都記在 `plan_change_log`，可以復原。
- 也就是說，**伺服器不只是存資料，它會自己讀 FIT、算模型、改課表、對外推送。** 這是和 Actual 最大的不同：Actual 的伺服器什麼都不算。

### 3.4 已有的相關功能

- 備份：zip 裡有 `manifest.json`、DB 快照，可選擇加上 `fit/*.gz`。可以還原和檢查（`backend/engine/backup.py`）。
- 沒有 FIT 上傳端點。`upload.fit` 權限已定義但沒有程式在用。沒有 service worker，也沒有離線支援。
- 示範版已經用 **Pyodide** 在瀏覽器的 Web Worker 裡跑 `engine/racepower/calc.py`（`backend/demo/static_racepower.py`，Pyodide 314.0.7 從 CDN 載入）。這證明「小型計算搬到瀏覽器」做得到。

### 3.5 擁有者的資料有多大（2026-10-07，NAS 唯讀量測）

| 項目 | 數量 | 大小 | 補充 |
|---|---|---|---|
| FIT（`fit/coros/`） | 812 個 | **122.7 MB** | 中位數 93 KB、第 90 百分位 296 KB、最大 2.17 MB、最小 0.6 KB |
| FIT gzip 後 | — | 約 **44 MB**（推估：抽 40 個樣本，壓縮後是原本的 0.36 倍） | 單檔壓縮比 0.38–0.53。大檔比較好壓 |
| 舊資料夾 `fits/` | 737 個 | 109.0 MB | 用 sha256 比對，**737 個全部和 `fit/coros/` 裡的某個檔案一模一樣**。這是 `backend/scripts/migrate_fit_folders.py` 搬移後留下的舊目錄，程式已不讀它（`LEGACY_COROS_FITS_ROOT`） |
| `wko5coach.db` | — | 2.4 MB（另有 WAL 3.7 MB） | `workout_files` 1,824 列、`workout_metrics` 3,053 列、`mmp_cache` 18,175 列 |
| 使用者改的列 | — | 遠小於 1 MB | `user_settings` 58、`plan_change_log` 15、`plan_sessions` 10、`athlete_settings` 6、`activity_tags` 2、`injury_events` 0 列 |
| 逐秒快取 `cache/fit/` | 807 個 npz | 42.8 MB（npz）＋ 87 個 JSON 62.1 MB；磁碟佔用 102 MB | 可以從 FIT 重建 |
| 圖表快取 `cache/render/` | 264 個 | 4.4 MB | 可重建 |
| 路線與天氣 `routes/` | 936 個 | 約 13.4 MB | |
| 根目錄的衍生 JSON | — | 約 2.3 MB | `workout_curves`、`racepower_*`、`achievements` 等 |
| 整個資料目錄 | | **358 MB** | 其中將近三分之一是 `fits/` 重複檔 |

**FIT 解析速度**（NAS 容器，4 核心，`fitdecode` 單執行緒讀完全部訊息）：

| 檔案 | 解析時間 | 逐秒紀錄 |
|---|---|---|
| 中位數大小的檔案 | 0.65 秒 | 3,695 筆 |
| 第 90 百分位 | 1.76 秒 | 5,472 筆 |
| 最大的檔案 | 12.3 秒 | 21,873 筆 |

`fitcache.py` 的註解說，冷啟動時 808 個檔「要好幾分鐘」，所以才用多個 process 平行跑。已驗證。

**和 10 月 2 日的量測比**（`auth-and-demo.plan.md` §10.1，另一個資料目錄，含 TP 來源，共 536 MB）：結論一樣，**DB 只佔 1% 左右，大的是 FIT 原檔和衍生快取**。

---

# 第二部分：落差

## 4. 資料長相的比較

| 面向 | Actual Budget | TrailRunCoach |
|---|---|---|
| 主要資料 | 很多小筆交易，每筆幾十 bytes，**會被改** | 約 800 個 FIT，每個 0.6 KB–2 MB，**寫入後不再改** |
| 使用者改的資料 | 幾乎全部 | 只有小部分：標記、課表修改、設定、傷病、賽事規劃，**合計不到 1 MB** |
| 衍生資料 | 報表、預算格子（`kvcache`、`spreadsheet_cells`），都在裝置上即時算，**不同步** | 逐秒通道、PMC、門檻、功率曲線、課表產生、圖表快取。**算起來很重**（冷啟動要幾分鐘），目前全在伺服器 |
| 誰會寫入 | 只有使用者（或使用者自己架的無畫面客戶端） | 使用者，**加上伺服器自己**（COROS 同步、`plan_auto`、校正） |
| 外部資料來源 | 銀行同步：伺服器代拿，客戶端寫入 | COROS／TP：伺服器代拿，**伺服器寫入也伺服器分析** |
| 伺服器角色 | 只負責轉送訊息和存快照，不懂內容 | 資料中心加運算中心加對外推送 |
| 資料量（每人） | 一般是 MB 級（推估） | 原檔約 120 MB（gzip 後約 44 MB），衍生約 50–100 MB |

## 5. 每一類資料能不能用 Actual 的方式同步

### 5.1 適合：小筆、使用者改的資料

| 資料 | 適合度 | 要先改的地方 |
|---|---|---|
| `user_settings`（鍵值） | 很適合。row 是 key，column 是 value，直接 LWW | 加上變更紀錄就能用 |
| `activity_tags` | 適合 | 不能再用自動遞增的 `id`，因為兩台裝置會撞號。改用穩定的鍵：現在的唯一鍵 `start_local`，或之後的 FIT 雜湊。`tags` 是一整個清單，兩台裝置同時各加一個標籤時，用 LWW 會掉一個。Actual 也有同樣限制 |
| `injury_events`、`workout_templates_user`、`race_calc`、`event_gpx` 中繼資料 | 適合 | 主鍵改成 UUID |
| `athlete_settings`（門檻） | 適合 | 改了之後要觸發重算衍生資料。在哪裡重算見 §6 |
| `plan_change_log` | 很適合。只會新增、不會改 | 主鍵改成 UUID |
| `plan.json`、`engine.json`、`corrections.json`、`annotations.json`、`views/` | **整份 JSON 不適合直接用** | 照 Actual 的做法，整份檔是「一格」，兩台裝置各改一個賽事時，後寫的會蓋掉先寫的。要拆成「一個賽事一列」「一筆修正一列」。推估工作量 M |

### 5.2 要小心：課表 `plan_sessions`

- 課表**同時被使用者和伺服器改**：
  - `plan_auto` 會整週重產、標記完成或漏掉、推到 COROS。
  - 使用者會手動改時間、內容。
- Actual 的規則是「每欄各自 LWW」。如果伺服器重產了一堂課（改 `kind`、`title`、`minutes`），同時使用者在離線的手機上改了 `minutes`，合併後可能變成「伺服器的標題，配上使用者的分鐘數」這種混搭的課。推估。
- 現有的 `edited` 旗標可以變成合併規則：**`edited=1` 的列，伺服器的重產永遠不蓋。** 也就是說，使用者的手動修改一律勝過自動產生，不比時間。這比純 LWW 更符合本 app 的規則（`plan_auto` 本來就不改 edited 的課）。
- 重產一次會改很多列、很多欄。照 Actual「一欄一則訊息」的方式，一次重產可能就是幾百則訊息。數量本身不大，但會讓紀錄長得快。推估。
- 建議：**課表的產生只在一個地方做**（伺服器），手動修改才是可以離線的變更。

### 5.3 不適合：FIT 原檔

- 塞進 Actual 那種訊息裡不合理：
  - 一個 2 MB 的檔就是一則巨大訊息。
  - 同步請求預設上限 20 MB（已驗證），新裝置一次補多筆就會超過。
  - 伺服器上的訊息永遠不刪（已驗證），等於每個 FIT 都留在紀錄裡，還要跟著快照每 7 天再上傳一次。
  - FIT 不會被改，「每欄 LWW」完全用不上。
- **比較好的做法：用內容雜湊命名的不可變物件。** 推估，但這是 git、Restic、多數照片同步服務的通用做法（摘要）。
  1. 匯入 FIT 時算 `sha256`，用雜湊當檔名或物件 key（例如 `fit/<前 2 碼>/<sha256>.fit.gz`），一律 gzip。
  2. 另外有一張小表 `fit_objects(sha256, size, source, coros_activity_id, start_time, deleted)`。**這張小表**用 §5.1 的變更紀錄同步，FIT 本身不走那套。
  3. 裝置要 FIT 時，比對「我有的雜湊」和「表上列的雜湊」，缺什麼就下載什麼。800 個雜湊的清單只有約 26 KB（800 × 32 bytes），不需要 merkle 樹。
  4. 已經存在的雜湊就不再上傳或儲存。**擁有者的 `fits/` 舊資料夾 737 個重複檔，這樣就會自動被擋掉。** 已驗證重複，推估擋得掉。
  5. 刪除只是把小表那列標成 `deleted`，原檔之後再統一清。
- 雜湊**擋不掉**「COROS 和 TP 的同一筆活動」，因為兩邊各自編碼，bytes 不一樣（推估）。現有用開始時間判斷重複（`dedup.py`）的做法還是要留著。
- 放在哪裡：沿用 `auth-and-demo.plan.md` §10.3 的 `FitStore` 介面。現在先存本機目錄（`LocalDirStore`），之後可以換成 R2 或任何 S3 相容儲存（`S3Store`）。**唯一要改的是：物件 key 從「路徑」改成「雜湊」。**

### 5.4 不同步：衍生資料

- Actual 自己就不同步衍生資料：上傳前清掉 `kvcache`，收到 `spreadsheet_cells` 訊息也直接忽略。已驗證。TrailRunCoach 也該這樣。
- 衍生資料都可以從 FIT 加上設定重算，問題只在**誰來算**：

| 在哪算 | 可行性 |
|---|---|
| 伺服器（現在） | 已經在做。COROS 同步後會自動預熱 |
| 桌面 app（Python 照用） | 可行。同一套程式碼，冷啟動要幾分鐘，跟現在的伺服器一樣 |
| 瀏覽器或手機 | 見 §7。小型計算可以，整套 dataset 很吃力 |

- 新裝置不想重算時，可以**從伺服器下載算好的結果**（npz、metrics）當作唯讀快照。npz 共約 43 MB，比 FIT 原檔小。推估。

### 5.5 絕不同步到裝置：機密

`sync_state` 裡的 COROS token、加密封存的密碼只留在伺服器。這跟 Actual 把銀行 token 留在伺服器、不納入端對端加密的做法相同。

## 6. 端對端加密在這個 app 的代價

如果照 Actual 的方式做端對端加密（只有使用者有金鑰，伺服器看不懂）：

| 功能 | 加密後還能不能做 | 原因 |
|---|---|---|
| 伺服器算模型（PMC、門檻、功率曲線、`workout_review`） | **不能** | 伺服器讀不懂 FIT 和設定 |
| `plan_auto` 同步後自動調課表 | **不能**（除非裝置開著） | 調課表要讀活動和課表 |
| 推課表到 COROS 手錶 | **不能**（除非裝置開著） | 推送內容就是課表本身 |
| 每天定時 COROS 同步 | 可以拿，但**拿到的時候就是明文** | 伺服器從 COROS 下載，本來就看得到內容。加密只能保護「之後存放」，而且要用裝置的公鑰加密（推估的設計，Actual 沒有這種機制） |
| 行事曆 ICS feed、分享連結 | 不能，或要另外做明文副本 | 需要伺服器讀課表 |
| 忘記密碼 | 資料救不回來 | Actual 官方文件明說 |
| 加密後還想要伺服器自動做事 | 只能讓伺服器上跑一個**握有密碼**的客戶端 | 這就是擁有者 NAS 上 `actual-http-api`／`actual-ai` 的做法。但這樣伺服器就看得到資料，等於不是端對端加密了 |

**結論：** 本 app 的核心價值是「跑完、同步完，伺服器自動算好、自動調好課表、推到手錶」，端對端加密會讓這些都做不到。比較合理的是：
- 傳輸用 TLS。
- 伺服器上的檔案和 DB 用伺服器保管的金鑰加密存放，例如 R2 的伺服器端加密，或 Litestream 的加密選項（未查證 Litestream 是否支援，待確認）。
- 用隱私權政策說清楚誰看得到。

如果之後真的有人要求端對端加密，可以只對**自由文字欄位**（備註、傷病描述）做，模型用不到這些欄位。推估。

## 7. 手機或瀏覽器上能不能解析 FIT、跑模型

| 問題 | 判斷 | 標記 |
|---|---|---|
| 瀏覽器能不能讀 FIT | 能。Garmin 官方有 JavaScript FIT SDK（npm `@garmin/fitsdk`），可以在瀏覽器跑。Python 的 `fitdecode` 也可以在 Pyodide 裡跑 | 摘要（npm 頁面、Garmin 論壇公告） |
| 解析多快 | NAS 上 Python 中位數檔案 0.65 秒、最大 12 秒。在手機瀏覽器全量重解析 800 個檔，估計要好幾分鐘到十幾分鐘，而且要在背景 worker 做，不能卡畫面 | 推估（以 NAS 實測外推，沒在手機上測） |
| 存得下嗎 | 存得下。Chrome 每個網站最多可用磁碟的 60%；Safari 17 起瀏覽器 app 也是 60%。gzip 後 FIT 約 44 MB 加 npz 約 43 MB，不到 100 MB | 已驗證（MDN 儲存配額頁、WebKit 部落格 2023 儲存政策） |
| 會不會被系統清掉 | **會。** Safari 開啟追蹤防護時，網站 7 天沒被點就會清掉它用程式寫入的資料。加到主畫面的 web app 比較容易拿到「持久儲存」。空間不夠時，瀏覽器會整個網站一起清 | 已驗證（MDN、WebKit） |
| 跑得動整套模型嗎 | 小型計算可以：示範版已經用 Pyodide 跑賽事功率計算。**整套 dataset 和 `workout_review` 很吃力**：引擎約 8 萬行 Python，有些用到檔案 I/O、SQLAlchemy；Pyodide 要先下載約十幾 MB 的執行環境 | 推估（`todo-multi-user-sharing.plan.md` §1(c) 估 Pyodide 可沿用約 60%） |
| 手機真正需要什麼 | 看課表、改課表、標記活動、看摘要圖表。這些只要 `workout_metrics`、`mmp_cache` 這類摘要（DB 才幾 MB），**不需要原始 FIT 和逐秒資料** | 推估 |

**結論：**
- 瀏覽器和手機當「只讀摘要，加上小筆離線編輯」的客戶端是可行的。
- 要它們當「完整運算節點」不划算，而且瀏覽器資料可能被清掉，所以**完整資料一定要有伺服器（或桌面 app）那份**。

## 8. COROS 同步要在哪跑

| 位置 | 可行性 |
|---|---|
| 伺服器（現在） | 可以排程、不必開 app、token 集中保管。缺點是伺服器要負責 token 安全，所有使用者都從同一個 IP 呼叫 API |
| 桌面 app | 可行，不會被 CORS 擋，但要開著 app 才會同步。這是 `todo-multi-user-sharing.plan.md` §10 免費版的做法 |
| 瀏覽器或手機網頁 | COROS 和 TP 的 API 預期不會開放跨網站呼叫（CORS），所以要經過代理，等於又回到伺服器。**這一點仍未實測**（同 §1(c)） |

**建議：** COROS 同步維持在伺服器。這跟 Actual 的銀行同步一樣：伺服器代拿資料，所以伺服器會看到明文。

## 9. 怎麼接到現在的多租戶架構

照現在的「每人一個目錄加一個 SQLite」（§2.2 已決定），可以這樣疊上去：

1. **FIT 物件存放**：`FitStore` 的 key 改成 sha256，`workout_files.file_path` 改存雜湊。`LocalDirStore(root=tenant.shared/"fit")` 照用。示範沙盒共用基底的 FIT，用雜湊後更自然。
2. **變更紀錄**：每個租戶的 DB 加一張 `changes`（時間戳、表、列 id、欄、值、裝置 id），再加上要同步的那幾張表的穩定 id。
3. **同步端點**：`POST /api/v1/sync/changes`。租戶由登入 session 決定，走 `tenancy_mw` 加正式登入（§4.2 的 session、CSRF）。
4. **伺服器也是一份副本**，這點和 Actual 不同：伺服器收到變更後，**直接套用到租戶 DB**，然後觸發重算或 `plan_auto`。所以伺服器可以替變更編一個遞增的序號，裝置只要記「我拿到第幾號」。這樣比 Actual 的 HLC 加 merkle 簡單很多：
   - 不需要 merkle 樹，序號就知道缺哪些。
   - HLC 只用來決定同一欄誰比較新。伺服器也可以直接用「收到的順序」決定。
   - 這是 Replicache、PowerSync 這類「伺服器為準」同步工具的常見設計（摘要）。
5. **schema 版本**：Actual 用 `messages_pending` 暫存看不懂的訊息，擁有者也踩過「客戶端比伺服器舊就讀不了」的坑。本 app 的變更紀錄要帶 schema 版本，伺服器拒收過舊客戶端的變更，並提示更新。
6. **COROS 同步、`plan_auto`、校正**：完全不用動，照樣在伺服器、照樣以租戶為單位跑。跑完產生的課表變更也寫進 `changes`，裝置下次同步就會收到。

---

# 第三部分：結論與後續

## 10. 架構選項

| | **A. 維持伺服器集中** | **B. 混合式（建議）** | **C. 完全 local-first（像 Actual）** |
|---|---|---|---|
| 資料在哪 | 全在伺服器。裝置只是網頁 | FIT 和運算在伺服器；裝置有摘要和小筆資料的副本，可以離線改 | 每台裝置都有完整資料（FIT、DB、衍生資料）；伺服器只轉送加密的變更和物件 |
| FIT 怎麼同步 | 不用同步，只存在伺服器（加上雜湊去重和 gzip） | 用雜湊命名的不可變物件。伺服器是主檔；桌面 app 可以選擇整份拉下來 | 用雜湊命名的加密物件。每台裝置都要整份拉下來 |
| 小筆資料 | 直接寫伺服器 | 變更紀錄，伺服器編序號，欄位 LWW，`edited` 優先 | Actual 式 CRDT 加 HLC 加 merkle |
| 衍生資料在哪算 | 伺服器 | 伺服器；桌面 app 可以自己算 | 每台裝置自己算（手機很吃力） |
| COROS 同步、`plan_auto`、推課表 | 伺服器，全自動 | 伺服器，全自動 | 只有裝置開著才會跑，或伺服器握有金鑰（那就不是端對端加密） |
| 離線 | 只能看（加 service worker 快取） | 小筆資料可以離線改，上線後合併 | 全部離線可用 |
| 端對端加密 | 不能 | 不做（可以只對自由文字欄位做） | 可以做 |
| 新裝置 | 登入就好 | 下載摘要（幾 MB）；桌面 app 要全量的話，再拉約 44 MB gzip FIT | 拉全部 FIT（約 44 MB）加重算幾分鐘，或拉加密的衍生快照 |
| 工作量 | **S–M**：`auth-and-demo` 的 C1–C3（FitStore、gzip、WAL＋Litestream），加雜湊 key、PWA 唯讀快取 | **M–L**：A 的全部，加穩定 id（M）、變更紀錄和同步端點（M）、客戶端離線佇列（M–L）、課表合併規則（M）、JSON 設定檔拆列（M） | **XL**：引擎要在客戶端跑（Pyodide 或改寫成 TS）、金鑰管理、客戶端 COROS 同步、伺服器改成只轉送，`plan_auto` 和推課表要重新設計 |
| 主要風險 | 沒網路就不能改；伺服器要負全部資料保管責任 | 合併規則寫錯會「默默蓋掉」修改，要測試；兩套寫入路徑（直接 API 和變更紀錄）並存期間比較複雜 | 失去全自動；忘記密碼就沒資料；每台裝置的 schema 版本要一起升；手機算不動；瀏覽器資料可能被清 |
| 和已有決定的關係 | 等於 `todo-multi-user-sharing` §10.2 的做法 B | 落在 §10.2 做法 A 和 B 之間：桌面 app 可以本機分析，伺服器照樣自動 | 比 §10.2 做法 A 更進一步，再加上多裝置和加密 |

**建議：B（混合式），分階段做，而且前幾步跟 A 完全相同。**

1. 先做 A 本來就要做的事，並把 FIT 的 key 改成雜湊（建議單 1、2）。這一步不管最後選哪個方案都用得到。
2. 等真的出現「要在沒網路時改課表或標記」或「桌面 app 和手機要互相同步」的需求，再加變更紀錄（建議單 3–5）。
3. C 只有在「隱私是賣點、而且願意放棄伺服器自動調課表」時才值得做。目前的產品方向（同步後自動調整、推到手錶）跟 C 衝突。

## 11. 建議開的單

| # | 標題 | 大小 | 依賴 | 說明 |
|---|---|---|---|---|
| 1 | FIT 物件改用內容雜湊命名（擴充 `auth-and-demo` C1） | M | `FitStore` 介面（C1） | 匯入時算 sha256，key 用雜湊，一律 gzip，新增 `fit_objects` 表。已有的雜湊直接跳過不存。`fitcache` 的有效條件改成雜湊（比 size＋mtime 更準） |
| 2 | 清掉 NAS 上的舊 `fits/` 重複資料夾 | S | 擁有者同意 | 737 個檔、109 MB，已用 sha256 確認全部和 `fit/coros/` 重複。先備份再刪；要改正式機，需要擁有者自己做或明確授權 |
| 3 | 使用者改的資料表改用穩定 id，並加 `updated_at` | M | — | `activity_tags`、`injury_events`、`plan_sessions`（已有 `uid`）、`workout_templates_user`、`race_calc`、`plan_change_log` 加 UUID。任何同步方案都要先有這個 |
| 4 | 資料分類登錄表 | S | — | 用程式碼列出每個租戶的檔案和資料表屬於「使用者改的／匯入的／衍生的／機密」哪一類。備份、同步、刪除帳號都讀這份清單，取代各處各寫一份 |
| 5 | 試做：小筆資料的變更紀錄同步（只做 `user_settings` 和 `activity_tags`） | M | 3 | `changes` 表、伺服器序號、`/api/v1/sync/changes`。用兩個瀏覽器分頁或兩個 process 模擬離線再合併，寫合併測試。**只有選了 B 才開** |
| 6 | 試做：手機瀏覽器解析 FIT 的實測 | S | — | 在 iPhone Safari、Android Chrome 上，用 `@garmin/fitsdk` 和 Pyodide＋`fitdecode` 各解析中位數、第 90 百分位、最大的檔案，記時間和記憶體。結果決定 §7 的推估要不要改 |
| 7 | PWA 唯讀離線快取 | S–M | — | 加 service worker，快取最近一次的課表和摘要。沒訊號時（山上）還能看今天的課。不涉及同步 |
| 8 | 補測 COROS／TP 的 CORS | S | — | 沿用 `todo-multi-user-sharing` §1(c) 一直沒做的 preflight 實測 |
| 9 | `plan.json` 等整份 JSON 設定拆成資料列 | M | 3 | 一個賽事、一筆修正各一列。**只有選了 B 或 C 才需要** |

## 12. 要請使用者決定的問題

1. **需不需要「離線修改」？**
   - 例如在山上沒訊號時，在手機上改課表或標記活動，下山再合併。
   - 如果只要「沒訊號時能看今天的課」，單 7（PWA 快取）就夠，不用做同步（建議 A）。
   - 如果要能改，才需要 B。
2. **端對端加密（伺服器看不到資料）是不是產品要求或賣點？**
   - 是的話，就要接受「伺服器不能自動調課表、不能推到手錶」，或改成「伺服器握有金鑰」的折衷，但那就不算端對端加密。
   - 我的建議是先不做。
3. **未來使用者用什麼裝置？** 只用網頁？`todo-multi-user-sharing` §10 決定的桌面 app？還是也要手機？手機需要完整分析，還是只要課表加摘要？
4. **課表以誰為準？** 我建議維持「伺服器的 `plan_auto` 產生，使用者手動改的（`edited`）永遠優先」。請確認這個規則。
5. **§10.2 的做法 A**（伺服器只當中繼，分析在桌面 app）**還要維持嗎？**
   - 如果同一個人會用桌面 app 加手機，做法 A 就得讓兩台裝置互相同步，複雜度接近本文件的 C。
   - 本研究比較傾向「伺服器也分析」（B）。
6. **能不能刪 NAS 上的舊 `fits/` 資料夾？**（109 MB，全部是重複檔；單 2）

---

## 附錄：查證過的檔案與來源

**Actual Budget（`github.com/actualbudget/actual`，commit `1ad8477`）**
- `packages/crdt/src/crdt/timestamp.ts`（HLC、5 分鐘漂移上限）
- `packages/crdt/src/crdt/merkle.ts`（三進位 merkle、prune、diff）
- `packages/crdt/src/proto/sync.proto`
- `packages/loot-core/src/server/sync/index.ts`（`apply`、`compareMessages`、`_applyMessages`、`_fullSync`）
- `packages/loot-core/src/server/sync/encoder.ts`、`sync/reset.ts`
- `packages/loot-core/src/server/encryption/{index,app,encryption-internals}.ts`
- `packages/loot-core/src/server/cloud-storage.ts`（`exportBuffer`、`upload`、`possiblyUpload`、`UPLOAD_FREQUENCY_IN_DAYS`）
- `packages/loot-core/src/server/budgetfiles/app.ts`（`downloadBudget`）
- `packages/loot-core/src/server/accounts/sync.ts`（銀行同步）
- `packages/loot-core/src/platform/server/{fs,sqlite}/index*.ts`
- `packages/loot-core/src/server/sql/init.sql`
- `packages/sync-server/src/{app-sync.ts,sync-simple.js,sql/messages.sql,util/paths.ts,load-config.js}`
- `packages/sync-server/migrations/*.js`
- 官方文件：`packages/docs/docs/getting-started/sync.md`（同 actualbudget.org/docs/getting-started/sync）

**外部**
- MDN〈Storage quotas and eviction criteria〉
- WebKit 部落格〈Updates to Storage Policy〉（2023）
- npm `@garmin/fitsdk`
- OWASP Password Storage Cheat Sheet（PBKDF2 疊代次數，摘要）

**擁有者的 Actual 部署**
- `~/Projects/actual-budget-automation/{README.md,CLAUDE.md}`（只讀說明文件，沒有讀 `.env`）

**TrailRunCoach**
- `backend/tenancy.py`、`backend/tenancy_mw.py`、`backend/db/{models,database}.py`
- `backend/sync/{storage,coros_client,runner,scheduler,dedup}.py`
- `backend/engine/{plan_auto,backup,workout_review}.py`
- `backend/engine/wko5expr/{fitcache,dataset,render_cache}.py`
- `backend/demo/static_racepower.py`、`backend/scripts/migrate_fit_folders.py`
- `docs/plans/todo-multi-user-sharing.plan.md`、`docs/plans/auth-and-demo.plan.md`

**量測**
- 2026-10-07 在 NAS 容器內用唯讀 Python 腳本統計 `/data`：檔案數、大小分位數、sha256 重複、資料表列數、`fitdecode` 解析時間、gzip 壓縮比。沒有寫入，也沒有讀機密檔。
