# SRS: Coros Sync — 非官方 API 自動下載 FIT

## Metadata
- **Source PRDs**:
  - `docs/prd/wko5-training-ai.prd.md` — Milestone 4（initial: Coros 非官方 API）
  - `docs/prd/wko5-trail-multipage-sync-coach.prd.md` — Milestone 3（統一同步頁 + 已載入資料盤點 + TP 接 UI）
- **Source Linear Issue**: N/A — standalone
- **Owner**: maintainer
- **Status**: IMPLEMENTED（M3 delta 進行中）
- **Generated**: 2026-05-15
- **Last updated**: 2026-10-04

## Change History

| Date | Source PRD | Feature SRS | Summary |
|------|------------|-------------|---------|
| 2026-05-15 | `wko5-training-ai.prd.md` | (initial) | Created — Coros 非官方 API 自動下載 FIT，已驗證 1088 筆 |
| 2026-06-13 | `wko5-trail-multipage-sync-coach.prd.md` | `docs/srs/coros-sync-unified-sync-page-data-inventory.srs.md` | 統一同步頁：TP 下載接上 UI、新增 `/sync/inventory` 盤點端點、CorosPage→SyncPage |
| 2026-09-30 | code-sync | — | 同步強化：增量 cursor、錯誤不推進 cursor、失敗 rollback、跨來源去重（`duplicate_of`）、本地日期（`start_time_utc`）、token 以 Fernet 加密。TP 改走網站登入 / WKO5-client OAuth，檔案改用 `details` + `rawfiledata` 下載 |
| 2026-09-30 | code-sync | — | 設定頁「資料同步」區塊、每來源互斥鎖（409 `SYNC_BUSY`）、每日排程（lifespan task）、`POST /sync/auto` + `autosync.js`、每來源獨立 FIT 資料夾與遷移腳本、刪除單一來源檔案、`FitFolderDataset` 與 `/sync/compare` |
| 2026-09-30 | code-sync | N/A | 掃描改走 `fit/<source>/` 並標 source + provider id、`FitFolderDataset` 時區取 `athlete.timezone`、`charts.map.basemap` / `charts.map.overlays` 設定鍵、COROS 課表推送改指向 overview.spec.md；路徑改寫成使用者資料夾相對形式 |
| 2026-10-01 | feat/auto-replan | N/A | 同步結束時，若這次下載 ≥ 1 筆活動（狀態 ok／partial），`runner.stream` 會呼叫 `plan_auto.after_sync`，在背景 task 裡用自己的 DB session 自動調整課表並推送（`docs/spec/plan-auto.spec.md`）。失敗不影響同步結果 |
| 2026-10-01 | bugfix | N/A | `FitFolderDataset` 前置修正：越野分類讀 app DB（含覆寫、跨來源重複）、sub_sport 後備；門檻／體重改成計畫 → `athlete_settings` → as-of 估算，WKO5 athlete 檔改為選用（`charts.fit_settings_from_wko5`）；`source_stamp` 含 DB 簽章 |
| 2026-10-01 | bugfix | user request (COROS vs TP back-test) | 每筆活動的功率來源（`stryd` / `watch` / `none`，`backend/engine/power_source.py`）；手錶推估功率預設不進功率模型、不算功率 TSS（設定 `power.accept_watch_power`，預設 false）；記錄 COROS 與 TP 檔案集合的差異（TP 獨有的一筆垃圾功率檔、TP 缺一段時期的 Stryd 跑步） |
| 2026-10-01 | feature | user request (bad activity files) | 壞掉的活動檔（忘了停錶騎車／開車、功率不可能）整筆排除：`FitFolderDataset`／`Dataset` 不放進 `ds.workouts`、`cptest.bad_files`；覆寫 `activity_tags.exclusion`，設定 `activities.exclude_bad`（預設 true），併入 `source_stamp`（見 workouts.spec.md） |
| 2026-10-02 | feat/primary-source-2 | user request (主要資料來源 follow-ups) | 以開始時間認活動（`backend/engine/activity_key.py`：同檔、去掉 `coros/`／`tp/` 前綴的同檔，否則 ±3 分內最近的開始時間；WKO5 檔名帶開始時間）：背負重量、獨攀標記、山名註記（新紀錄附 `start`，舊的就地沿用，不改鍵）、活動標籤／當作間歇／RPE、活動天氣與路線索引對應、課表完成紀錄的 `done_by.index`（依 `start` 改指目前資料來源的 index，`plan_store.load`／`_plan_rows`／`plan_match.assign`／月曆）。圖表來源讀取時遷移：存的是舊的 `coros` 且沒在新 UI 選過（`charts.data_source.chosen`）→ 視為 `synced`，不寫 DB。`cptest.curves`／`scan` 跟主要來源（`secondary_duplicates`，同 merge 規則；TP 檔名也讀）。實測（DB 與快取複本）：三筆課表完成紀錄的 index 都改指到合併資料裡的同一活動；10/10 標籤、2/2 山名找得到；cptest 跳過 807 個 TP 重複檔 |
| 2026-10-02 | feat/primary-source | user request (主要資料來源) | `sync.primary_source` 改為 自動（預設）／COROS／TrainingPeaks（`backend/sync/primary.py`；自動＝最新活動日最新者，同日看上次同步是否完整、近 90 天筆數，再 COROS）。新圖表來源 `synced`（`charts.data_source` 預設）：兩個 FIT 資料夾合併，同一活動（開始時間 ±2 分，同 dedup）只用主要來源的檔，另一來源只補主要來源沒有的活動；同來源重複檔留樣本數多的；不逐值互補（「算不出來就不要補了」）。`MultiFitStore` 沿用各資料夾既有快取。自動同步（開網站／每日）只同步主要來源，另一個要開 `sync.secondary.auto`（進階）；主要來源停用或登出時改同步另一個。自動模式同步下載後重建 dedup。活動編輯頁顯示來源 badge（`origin`）。實測（唯讀，快取複本）：主要 COROS → 1068 筆（COROS 799、TP 補 269，多為較早年份）；主要 TP → 1068 筆 |
| 2026-10-01 | perf/dataset-load | user request (login / token) | 登入一次：某 region 發了 token 後不再登入其他 region；同時兩個登入回 409 `COROS_LOGIN_BUSY`（COROS 只認最後一次登入）。資料 server 偵測順序：上次偵測到的 → US → 登入 server → 其餘；全部探測失敗（2026-10-01：dataset 建置卡住 event loop，探測全部逾時）時沿用上次的，否則 US。「記住密碼」（預設關）：密碼以 `secrets.seal` 存 `sync_state.coros_password_sealed`／`tp_password_sealed`，token 過期或 result 1019 時自動登入一次、重試一次，取消勾選或登出即刪除（`docs/secrets-and-keys.md`）。同步時 FIT 解析改在 thread，同步下載到新檔後背景重建圖表 Dataset |
| 2026-10-01 | bugfix | user request (charts on COROS) | 圖表分析在 COROS 來源：FIT `vam` 與登山標籤、Stryd-only PD 擬合的圖表 CP（計畫測試之前）、閾值配速推估（CP × 速度／功率比）、區間表來源與日期、越野／爬坡課表看功率、訓練量週增幅改 4 週平均（見「圖表分析在 COROS 來源」） |
| 2026-09-30 | bugfix | N/A | `charts.data_source` 接上圖表 / 總覽 / 功率計算機的 Dataset 工廠與圖表頁資料來源切換；掃描去重的 COROS id 也限定 athlete；`_sync_ids` 接受 `tp` |
| 2026-10-04 | code-sync | N/A | 一次只用一個資料來源（COROS 或 TP，取代 10-02 的自動／合併 `synced`；`charts.data_source` = `source` / `wko5`）；登入有效性檢查 `session_check` + 「登入已過期」橫幅（`/auth/session-alerts`）；COROS 心率設定（`athlete.coros_profile`）登入與每次同步更新；跑步功率 TSS 改用 Stryd-only PD mFTP；`cached_series` 改磁碟快取；時區自動偵測；新端點（`/auth/{source}/remember`、`/sync/timezone/browser`、`/sync/dedup/rebuild` 等）；demo 模式不掛同步路由；`file:line` 指標全面更新；新增 Domain Model |
| 2026-10-04 | feat/sp-34-35-schedule | SP-34 | `GET /sync/primary`（資料來源＋登入／啟用／忙碌）；SSE 讀取抽成 `static/syncrun.js`，設定頁立即同步與課表頁「從 COROS 抓活動」共用 |
| 2026-10-04 | sp-38-load-step | SP-37／SP-38 | 活動列表的 `trainingLoad`（COROS TL）存進 `workout_files.coros_training_load`（新欄位，同步時新活動寫入、已匯入的補上，不多打 API）；同步後的每人校正一併重擬 TSS → TL 換算（`engine/coros_tl.py`：依 TSS 來源分組、收縮到預設、近期加權、門檻大改前的活動不用、最近 30 天時間序回測不比舊的差才換上）與「負荷」步驟的實跑校正；設定頁顯示換算模型（推估） |
| 2026-10-04 | feature | SP-38 follow-up | 推送「負荷」步驟時，重新擬合讓 TL 變動 < 3（推估）就沿用上次送出的 TL，不標「需更新」、不重推；≥ 3 才換 |

---

## Summary

實作 Coros 非官方 API 客戶端，讓使用者以 Coros Training Hub 帳密登入，自動下載 .fit 檔案到使用者資料夾的 `.wko5coach/fit/coros/{year}/`（`fit_root`，`backend/sync/storage.py:28`；路徑取自 tenant 的 shared 資料夾，`backend/tenancy.py:138`），完全不依賴 WKO5 資料夾或 TrainingPeaks 帳號。下載完成後觸發 FIT 解析、指標計算、TSS/PMC 流程。

**已驗證（2026-05-15）**：1088 筆活動匯入成功，PMC 圖表（CTL/ATL/TSB）正常顯示。

---

## Coros 非官方 API（逆向工程已驗證，2026-05-15）

### 社群參考實作

| 專案 | 語言 | 功能 |
|------|------|------|
| `xballoy/coros-api` | TypeScript/NestJS | Bulk FIT export，有日期篩選；`/activity/detail/download` endpoint |
| `cygnusb/coros-mcp` | Python | MCP server，sleep/HRV/activity；`yfheader` 要求已記錄 |
| `CuberL/coros-mcp` | Python | MCP server |
| `rowlando/coros-workout-mcp` | Python | MCP，Claude Desktop 整合 |

### 認證流程（已驗證）

```
POST https://teameuapi.coros.com/account/login
Content-Type: application/json
User-Agent: Mozilla/5.0 ...Chrome/145.0.0.0 Safari/537.36

{
  "account":     "<email>",
  "pwd":         "<md5(password)>",   // 欄位名是 pwd，不是 passwd
  "accountType": 2                    // 2 = email login
}
```

**重要修正（vs 社群部分舊文件）**：
- 欄位名：`"pwd"`（不是 `"passwd"`）
- User-Agent header 必須帶（Chrome UA）

**成功回應**:
```json
{
  "result": "0000",           // 成功判斷用 result=="0000"，不是 apiCode=="1"
  "apiCode": "...",
  "message": "OK",
  "data": {
    "userId":      "<user_id>",
    "accessToken": "<token>",           // 在 data.accessToken，不是 result.accessToken
    "zoneData": {
      "ftp":  230,    // 單位 W，從 Coros profile 自動帶入
      "lthr": 178,    // 心率閾值
      "rhr":  50
    },
    "weight": 70,
    "maxHr":  190,
    "criticalPower": 220
  }
}
```

Token TTL：24 小時（登入回應不含 tokenExpiry，固定 +24h）。

### Region 偵測（關鍵）

Coros 有多個 region server，登入成功的 server 不一定是活動資料的 server：

| Region | Login URL | Data URL |
|--------|-----------|----------|
| EU | `teameuapi.coros.com` | `teamapi.coros.com`（US/global） |
| US | `teamapi.coros.com` | `teamapi.coros.com` |
| CN/Asia | `teamcnapi.coros.com` | `teamcnapi.coros.com` |

**台灣帳號實測**：EU server 登入成功，但 token 對資料 API 有效的是 `teamapi.coros.com`（US server）。

**偵測策略**：登入成功後，依序對「上次偵測到的 server（同一 userId）→ US → 登入 server → 其餘」發一次 `/activity/query` 測試請求，找第一個回傳 `result=="0000"` 的 server，儲存為 `coros_base_url`。全部失敗（逾時、網路）時沿用上次的，否則 US，並在 log 記下每個 server 的原因。登入本身只做一次：一個 region 回 `0000` 之後就不再對其他 region 登入（再登入會讓前一個 token 失效）。

**記住密碼 / 自動重新登入**（2026-10-01）：見 `docs/secrets-and-keys.md`。活動列表或 Training Hub 回 result 1019／1030（Access token is invalid）、或 token 過期時，有存密碼就自動登入一次、重試一次（`relogin`，`backend/sync/coros_client.py:359`）；同步事件流會多一筆 `{"status": "relogin"}`。

**登入有效性檢查**（2026-10-03，`backend/sync/session_check.py`）：存著 token 不代表還登入著（COROS token 約 24 h 失效，或帳號在別處登入就失效）。`check()`（`backend/sync/session_check.py:94`）對每個來源最多每 `CHECK_TTL_S`（300 s）打一次便宜的認證呼叫（COROS：`activity/query` size=1，`probe_token`，`backend/sync/coros_client.py:100`；TP：`users/v3/user`），結果放記憶體快取；連不上伺服器 = `unknown`，頁面仍顯示已登入、60 s 後再查。任何 COROS／TP 呼叫拿到「需重新登入」就 `mark_expired()`（`backend/sync/session_check.py:56`），狀態立即翻成「登入已過期」；有記住密碼時先走自動重新登入，失敗或沒存密碼才算過期。`runner.logged_in`（`backend/sync/runner.py:160`）只讀快取判斷，所以過期的來源不會被自動同步。總覽／課表頁的橫幅 `session_banner.js` 讀 `GET /api/v1/auth/session-alerts`（`backend/api/auth.py:164`），只列「正在用」的登入（資料來源的同步、COROS 課表推送）。

**COROS 心率設定**（2026-10-03）：登入回應與每次同步結束後的 `GET /account/query`（唯讀，`refresh_hr_profile`，`backend/sync/coros_client.py:302`）解析出最大心率、安靜心率與三組 COROS 區間表（`zoneData.lthrZone` / `rhrZone` / `maxHrZone`），存在設定 `athlete.coros_profile`（`store_hr_profile`，`backend/sync/coros_client.py:285`；`backend/engine/hr_profile.py:57`）。失敗不影響登入或同步。讀取端（最大／安靜心率解析、圖表與課表的心率區間）在 `backend/engine/hr_profile.py`，不屬本規格。

### 所有 API 呼叫的必要 Headers

```
accessToken: <token>
yfheader: {"userId": "<user_id>"}    // 缺少此 header 會返回 "Access token is invalid"
User-Agent: Mozilla/5.0 ...Chrome/145.0.0.0 Safari/537.36
```

### 活動列表（已驗證）

```
GET https://teamapi.coros.com/activity/query
    ?size=20&pageNumber=1&startDay=YYYYMMDD&endDay=YYYYMMDD
Headers: accessToken + yfheader + User-Agent
```

**回應結構**（關鍵欄位）:
```json
{
  "result": "0000",
  "data": {
    "dataList": [               // 欄位在 data.dataList，不是 result.dataList
      {
        "labelId":   "<label_id>",           // unique activity ID
        "name":      "<城市> 跑步",
        "sportType": 100,        // 100=run（已對 FIT 驗證）, 102=trail run, 104=hike, 105=mountaineering, 200=cycling, 402=strength, 9904=custom（backend/sync/coros_sport.py:23）
        "date":      20260514,   // YYYYMMDD 8位整數，不是 Unix timestamp
        "startTime": 1747282306, // Unix timestamp（不用於日期解析）
        "totalTime": 4691,       // seconds
        "trainingLoad": 87,      // COROS 的訓練負荷 TL（多數活動有；SP-37 探測）→ workout_files.coros_training_load
        "fitUrl":    null        // 多數活動為 null，需用 detail/download
      }
    ]
  }
}
```

**重要修正**：`date` 欄位是 `YYYYMMDD` 8位整數（如 `20260514`），`startTime` 是 Unix timestamp，日期解析應使用 `date` 欄位。

### FIT 檔案下載（已驗證）

**正確 endpoint**（`xballoy/coros-api` 確認）：
```
POST https://teamapi.coros.com/activity/detail/download
    ?labelId=<id>&sportType=<type>&fileType=4
Headers: accessToken + yfheader + User-Agent
```

- `fileType=4` = FIT（0=csv, 1=gpx, 2=kml, 3=tcx, 4=fit）

**錯誤的 endpoint**（舊文件，返回 500）：
- `GET /activity/fit/url` — 此 endpoint 對本帳號無效，返回 Internal Server Error

**回應**：
```json
{
  "result": "0000",
  "data": { "fileUrl": "<presigned_s3_url>" }
}
```

接著 GET presigned URL 取得原始 .fit bytes。

若活動有 `fitUrl` 欄位（非 null），可直接 GET 下載，優先於 `detail/download`。

### Token 刷新
Token 約 24h 過期，需重新 POST `/account/login`。無 refresh token 流程。

---

## Scope

### In Scope
- Coros 帳密登入（MD5 password，`pwd` 欄位）
- Region 自動偵測（EU/US/CN 依序測試）
- 活動列表分頁拉取（含日期篩選）
- .fit 檔案下載到使用者資料夾的 `.wko5coach/fit/coros/{year}/`
- 新活動自動觸發 FIT 解析 + TSS/MMP 計算
- 重複活動跳過（依 `coros_activity_id` 去重）
- SSE 串流同步進度
- Token 持久化到 DB（`sync_state` 表，`secrets.seal` 加密）
- FTP/LTHR/weight 從登入回應自動匯入 `athlete_settings`；心率設定存 `athlete.coros_profile`
- 登入有效性檢查與「登入已過期」提示
- 「資料來源」二選一（COROS 或 TrainingPeaks，`sync.primary_source`）：只同步、只讀所選來源
- PMC recompute endpoint（FTP 更新後重新計算 TSS）

### Out of Scope
- WKO5 資料夾讀寫（完全獨立；WKO5 只當對照來源）
- 兩個來源合併或互補（2026-10-02 的合併設計已於同日撤回，見 Change History）
- demo 模式的同步（`build_app(demo=True)` 不掛 sync / auth / scan 路由，`backend/main.py:139`）
- 心跳同步 / WebSocket push
- Coros Training Plans / Structured Workouts 解析（反方向的「把本專案課表推送到 COROS」已實作，見下方指標）
- 多運動員帳號切換

---

## System Context

### Actors

| Actor | Type | Interaction |
|-------|------|-------------|
| 個人運動員 | Human — Browser | 觸發 sync、查看 PMC、設定 FTP |
| Coros Training Hub API | External Service | 提供活動列表 + FIT 下載 URL |
| 本機 FileSystem | Storage | 使用者資料夾的 `.wko5coach/fit/<source>/` 儲存 .fit |

### External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| `teameuapi.coros.com` | 身份驗證 | 降級：手動匯入 .fit |
| `teamapi.coros.com` | 活動列表 + FIT URL | 重試，記錄失敗，其他活動繼續 |
| Coros S3 presigned URL | FIT 檔案下載 | 失敗記錄 error event，繼續下一筆 |
| 現有 `file_service.py` | FIT 解析 + DB 匯入 | 同步失敗，不影響已存 |

---

## Architecture

### 元件結構

```
backend/
├── sync/
│   ├── tp_client.py            # TrainingPeaks 客戶端（網站登入 / WKO5-client OAuth）
│   ├── coros_client.py         # Coros API 客戶端（登入、region 偵測、記住密碼、同步、心率設定）
│   ├── coros_sport.py          # COROS 檔名的運動字（FIT session sport 優先，sportType 後備）
│   ├── runner.py               # 共用 runner：每來源互斥鎖、last_result、同步後掛鉤、auto_plan
│   ├── scheduler.py            # 每日排程（lifespan task）
│   ├── primary.py              # 資料來源（COROS／TP 二選一）與舊設定遷移
│   ├── dedup.py                # 跨來源去重（duplicate_of）、in_use_clause
│   ├── purge.py                # 刪除單一來源檔案、來源統計
│   ├── storage.py              # fit/<source>/ 路徑、confined()
│   ├── session_check.py        # 登入有效性檢查（2026-10-03）
│   ├── http.py                 # 共用 httpx client（測試可換 MockTransport）
│   ├── coros_workouts.py       # 課表推送到 COROS（見 overview.spec.md）
│   └── workout_targets/        # 課表推送 provider 介面
├── api/
│   ├── auth.py                 # /auth/coros/*、/auth/tp/*、/auth/session-alerts
│   └── sync.py                 # /sync/*（SSE 同步、設定、來源、比對、刪除）
├── db/
│   ├── models.py               # 含 coros_* 欄位（已實作）
│   └── database.py             # _migrate_schema() 自動 ALTER TABLE（已實作）
└── files/
    └── file_service.py         # _import_one_file(coros_activity_id=...)（已更新）


backend/static/                 # 同步 UI 實際所在（設定頁等靜態頁）
├── settings.html               # 設定 →「資料同步」：資料來源切換、登入、同步、狀態
├── autosync.js                 # 開網站自動同步（shell.js 載入）
├── session_banner.js           # 「登入已過期」橫幅（總覽、課表）
└── compare.html                # 資料來源比對
```

### FIT 儲存路徑

```
<home>/.wko5coach/
├── wko5coach.db           # SQLite DB
└── fit/                   # backend/sync/storage.py（2026-09-30 起每個來源分開）
    ├── coros/{year}/{coros_id}_{YYYY-MM-DD}_{sport}.fit
    └── tp/{year}/tp_{YYYY_MM_DD}_{workout_id}.fit
```

舊位置是 `.wko5coach/fits/{athlete}/…`（COROS）和 `.wko5coach/fit/athlete_1/…`（TP），都在使用者資料夾底下。用 `python -m backend.scripts.migrate_fit_folders` 遷移：預設 dry run，加 `--apply` 才執行，可重複執行。它會搬移檔案、更新 DB 路徑、刪掉清空的舊資料夾。2026-09-30 在這台機器上實際執行：COROS 17 個、TP 17 個。

所有刪除都經過 `storage.confined()`：路徑先 resolve、拒絕 symlink，超出 `fit/<source>/` 一律拒絕。

### 資料夾掃描（`scan_and_import`）

`scan_and_import`（`backend/files/file_service.py:104`）對 athlete 的 `data_dir` 掃描（原本的 `POST /api/v1/scan` 端點只有 React SPA 在用，2026-10-04 隨 SPA 刪除；現在由 demo 建置等內部流程直接呼叫）：

- `discover_tagged_files`（`backend/files/file_service.py:66`）：資料夾底下若有 `storage.SOURCES`（`backend/sync/storage.py:20`）列的 `coros/`、`tp/` 子資料夾，就逐一走 `<source>/<year>/`，檔案標上 DB source（`coros` / `trainingpeaks`）；同一資料夾的傳統 `<year>/*.wko4|.fit` 版面照舊標 `local`。symlink 跳過。
- `_sync_ids`（`backend/files/file_service.py:49`）：從同步寫出的檔名反推 provider id——COROS `<labelId>_<日期>_<sport>.fit` → `coros_activity_id`，TP `tp_<日期>_<workoutId>.fit` → `tp_workout_id`。`source` 收 DB 名稱（`coros` / `trainingpeaks`）也收資料夾 / API 名稱（`tp`），經 `storage.SOURCES` 對應。
- `_already_imported`（`backend/files/file_service.py:81`）：路徑（原樣與 resolve 後）、同一 athlete 的 `coros_activity_id` 或 `tp_workout_id` 任一已在 DB 就跳過（兩種 provider id 都限定 athlete，`backend/files/file_service.py:93`、`backend/files/file_service.py:98`），所以同步已記錄的檔案不會被掃描重複匯入；可重複執行。
- 每個檔案一個 savepoint（`backend/files/file_service.py:123`），失敗不留半筆資料；回傳 `new` / `skipped` / `errors` / `total` / `new_by_source`。

測試：`backend/tests/test_scan_and_tz.py:31`（per-source 版面、冪等）、`backend/tests/test_scan_and_tz.py:47`（跳過同步已記錄者）、`backend/tests/test_scan_and_tz.py:62`（provider id 去重限定 athlete）、`backend/tests/test_scan_and_tz.py:87`（傳統版面仍為 local）。

### 同步流程

```
POST /api/v1/sync/coros/start?since=YYYY-MM-DD
  ↓
_get_token_and_base(db) → (token, base_url, user_id)
  ↓
_list_page(token, base, user_id, since, end, page) → activities[]
  ↓
for each activity:
  if coros_activity_id in DB → 列表的 trainingLoad 補進 coros_training_load（有變才寫）→ SSE: skipped
  _download_fit(token, base, user_id, activity):
    1. try fitUrl (presigned S3, if present)
    2. POST /activity/detail/download?labelId=...&sportType=...&fileType=4
    3. GET presigned URL → bytes
  save to <home>/.wko5coach/fit/coros/{year}/{labelId}_{date}_{sport}.fit
    (sport = coros_sport.sport_token：FIT session sport/sub_sport，讀不到才用 sportType)
  _import_one_file(db, athlete_id, dest, source="coros", coros_activity_id=id)
    → parse FIT, compute metrics (TSS if FTP available, MMP, HR zones)
    → 解析失敗：record_corrupt stub（不再重複下載）
  SSE: downloaded / error
  ↓
沒有錯誤才更新 sync_state.coros_last_sync_at（增量 cursor，下次從這天減 overlap 開始）
refresh_hr_profile（GET /account/query，失敗不影響）
SSE: complete {total_downloaded, total_checked, errors}
  ↓（runner.stream finally，backend/sync/runner.py:67）
寫 sync.<src>.last_result → plan_auto.after_sync → 有新檔時 localtime.refresh_from_fits
→ calibrate.after_sync（每人校正，含 COROS TL 換算重擬）→ 有新檔時 wko5views.warm_up（背景重建圖表 Dataset）
```

#### COROS Training Load（SP-37／SP-38，2026-10-04）

- **儲存**：列表項目的 `trainingLoad`（`list_training_load`，`backend/sync/coros_client.py:472`）在匯入時寫進新活動，已匯入的在下次列表掃到時補上或更新（`backend/sync/coros_client.py:569`）；只用同步本來就抓的列表，不多打任何 COROS API。欄位 `workout_files.coros_training_load`（`backend/db/models.py:69`，加法遷移 `backend/db/database.py:115`）。
- **重擬**：同步有新活動、或只補了已匯入活動的 TL（`complete` 事件的 `tl_filled`，經 `runner` 的 `last_result`）都會觸發每人校正；`calibrate.calibrate` 跑完 Item 後呼叫 `coros_tl.refit_and_store`（`backend/engine/calibrate.py:246`，`backend/engine/coros_tl.py:743`）：有 TL 的 COROS 活動（以檔名的 labelId 對到圖表 Dataset）× app 的 TSS → 三組（功率 TSS、hrTSS 依 IF、hrTSS 比例，`group_samples`，`backend/engine/coros_tl.py:436`）。
  - 只用最後一次門檻（FTP／LTHR）變動 > 5 % 之後的活動（`since_threshold_change`，`backend/engine/coros_tl.py:459`），近期權重較高（半衰期 120 天，`recency`，`backend/engine/coros_tl.py:473`）。
  - 模型族用 LOO MAE 選；樣本 < 60 時只比 A（比例）／C（冪次），避免二次式在小樣本爆掉（`choose_family`，`backend/engine/coros_tl.py:484`）。
  - 收縮：換算 = w·本人 + (1 − w)·預設，w = n ÷ (n + 30)（`SHRINK_K`，`backend/engine/coros_tl.py:79`）；預設只是先驗（推估）。
  - 時間序回測：最近 30 天當 holdout（`HOLDOUT_DAYS`，`backend/engine/coros_tl.py:85`），新擬合在 holdout 上的 MAE 不比目前存的差才換上；存回測與 LOO 誤差（`refit_group`，`backend/engine/coros_tl.py:511`）。結果存設定 `coros.tl_model`。
  - 實跑校正：推上 COROS 的「負荷」步驟（計畫 TSS、送出的 TL、強度、當時的係數）記在 `coros.tl_load_calib`；那堂課完成且活動的圈數＝推送的步驟數時，那一圈累積的 TSS ÷「未校正模型對送出 TL 的 TSS」是一個樣本（沒有時用 計畫 TSS ÷ 推送時的係數；照計畫跑完不會把係數拉回 1），收縮後（w = n ÷ (n + 5)）的係數在換算前除掉（`refresh_load`／`load_factor`，`backend/engine/coros_tl.py:648`、`backend/engine/coros_tl.py:672`）。
  - 重推門檻：重新擬合讓某個「負荷」步驟的 TL 變動 < 3（`TL_RESEND_MIN`，推估）時，推送沿用上次送出的 TL（同一計畫 TSS／依據／強度，從 `coros.tl_load_calib` 的紀錄讀，`_sent_tl`，`backend/sync/coros_workouts.py:613`；`sent_tl`，`backend/engine/workout_steps.py:1056`），指紋不變、不標「需更新」；≥ 3 才換新值重推（SP-38）。
- **顯示**：`GET /sync/settings` 回 `coros_tl`（`describe`，`backend/api/sync.py:264`），設定頁「課表推送到」下方列出每組的模型、n、權重、回測誤差（推估）。

主流程在 `sync_workouts`（`backend/sync/coros_client.py:485`）。所有同步入口（手動 SSE、`/sync/auto`、每日排程）都走 `runner.stream`；自動同步只跑「資料來源」那一個（`auto_plan`，`backend/sync/runner.py:187`），另一個來源回 `not_in_use`。

---

## Data Model

### `workout_files` 表新增欄位

```sql
ALTER TABLE workout_files ADD COLUMN coros_activity_id TEXT;  -- index, unique per activity
ALTER TABLE workout_files ADD COLUMN coros_sport_type INTEGER;
ALTER TABLE workout_files ADD COLUMN coros_training_load REAL;  -- 列表的 trainingLoad（SP-38），NULL = 沒有
-- source 欄位新增值: 'coros'（原有 'local' | 'trainingpeaks'）
```

### `sync_state` 表新增欄位

```sql
ALTER TABLE sync_state ADD COLUMN coros_access_token TEXT;
ALTER TABLE sync_state ADD COLUMN coros_token_expires DATETIME;
ALTER TABLE sync_state ADD COLUMN coros_last_sync_at  DATETIME;
ALTER TABLE sync_state ADD COLUMN coros_email         TEXT;
ALTER TABLE sync_state ADD COLUMN coros_base_url      TEXT;  -- 偵測到的資料 server URL
ALTER TABLE sync_state ADD COLUMN coros_user_id       TEXT;  -- 用於 yfheader
-- 2026-10-01 「記住密碼」（勾選才有；secrets.seal 加密；取消勾選／登出即 NULL）
ALTER TABLE sync_state ADD COLUMN coros_password_sealed TEXT;
ALTER TABLE sync_state ADD COLUMN tp_username           TEXT;
ALTER TABLE sync_state ADD COLUMN tp_password_sealed    TEXT;
```

### `athlete_settings` 表（已有，從 Coros 登入自動填入）

登入成功後自動 upsert：
- `ftp_w` ← `data.zoneData.ftp`
- `lthr` ← `data.zoneData.lthr`
- `weight_kg` ← `data.weight`
- `effective_date` ← 今日

---

## Domain Model

### Bounded Context
- **Context Name**: Activity Sync（活動同步）
- **Domain Layer**: Supporting（外部平台資料的取得與整理；分析與教練邏輯在下游）
- **Parent Module**: TrailRunCoach backend — `backend/sync/`、`backend/api/sync.py`、`backend/api/auth.py`；下游讀者為圖表 Dataset（`backend/engine/wko5expr/`）與計畫（plan-auto.spec.md）

### Ubiquitous Language

| Term | Meaning | Code |
|------|---------|------|
| 來源（Source） | 一個外部活動平台：COROS 或 TrainingPeaks。資料夾／API 名 `coros` / `tp`，DB 名 `coros` / `trainingpeaks` | `backend/sync/storage.py:20`、`backend/sync/primary.py:25` |
| 資料來源（Primary source） | 使用者選的那一個來源，全 app 唯一被讀與自動同步的來源；另一個原封保留 | `sync.primary_source`，`current`，`backend/sync/primary.py:87` |
| 同步（Sync run） | 一次列活動 → 下載 FIT → 匯入的執行，以 SSE 事件回報；每來源同時只能一個 | `runner.stream`，`backend/sync/runner.py:67` |
| 同步忙碌（SYNC_BUSY） | 同一來源已在同步時的拒絕（409） | `SyncBusy`，`backend/sync/runner.py:34` |
| 同步結果（Last result） | 每次同步的 `status`（ok／partial／failed／aborted）、下載數、錯誤數、觸發方式 | `sync.<src>.last_result` |
| 增量 cursor | 上次「無錯誤」同步的時間；下次從它減 overlap 天開始列，有錯就不推進 | `sync_state.coros_last_sync_at` / `last_sync_at` |
| 自動同步（Auto sync） | 開網站或每日排程觸發，只對資料來源、已啟用、已登入、閒置且超過門檻小時數者 | `auto_plan`，`backend/sync/runner.py:187` |
| 登入狀態（Session status） | `logged_in` / `expired`（登入已過期）/ `logged_out`；檢查結果另有 `unknown`（連不上，仍顯示已登入） | `backend/sync/session_check.py:41` |
| 記住密碼（Remembered password） | 勾選才存、加密存放的密碼，只用於 token 失效時自動重新登入一次 | `coros_password_sealed` / `tp_password_sealed` |
| 資料 server（Data base URL） | COROS 實際接受 token 的資料 API region，登入時偵測 | `sync_state.coros_base_url` |
| 跨來源重複（Duplicate） | 兩個來源的同一活動（開始時間 ±2 分）；非資料來源那列標 `duplicate_of`，不進總數 | `backend/sync/dedup.py` |
| 損壞檔 stub | 解析不了的 FIT 也記一筆 `file_format="corrupt"`，避免每次重下載 | `record_corrupt`，`backend/files/file_service.py:235` |

### Domain Events

| Event | Raised by | Consumers |
|-------|-----------|-----------|
| 同步完成（`status: complete`，含下載數） | `coros_client.sync_workouts` / `tp_client.sync_workouts` | `runner.stream`：`plan_auto.after_sync`、`localtime.refresh_from_fits`、`calibrate.after_sync`、`wko5views.warm_up` |
| 登入過期（`mark_expired`） | 任何回「需重新登入」的 COROS／TP 呼叫 | `/auth/*/status`、`/auth/session-alerts`、`runner.logged_in` |
| 資料來源切換（PUT `/sync/settings` 帶 `primary_source`） | 設定頁 | `dedup.rebuild`；圖表 Dataset 依 `source_stamp` 重建 |

---

## API Contracts

### 已實作 Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/auth/coros/login` | Coros 登入，自動偵測 region + 儲存 FTP |
| GET | `/api/v1/auth/coros/status` | 登入狀態 + email + 最後同步時間 |
| POST | `/api/v1/auth/coros/logout` | 清除 token |
| POST | `/api/v1/sync/coros/start` | 觸發同步（SSE stream） |
| POST | `/api/v1/pmc/recompute` | 用當前 FTP 重新計算所有 TSS |

### M3 新增 Endpoints（2026-06-13，統一同步頁）

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/sync/start` | 觸發 TrainingPeaks 下載同步（SSE stream，經 `runner.stream` 接 `tp_client.sync_workouts`；沒有 `/sync/tp/start`） |
| POST | `/api/v1/auth/tp/login` | TP 帳密登入取 OAuth token（`tp_client.login_password` 已驗證） |
| GET | `/api/v1/auth/tp/status` | TP 連線狀態 |

### 2026-09-30 新增 Endpoints（設定頁「資料同步」）

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/sync/sources` | 每個來源的登入狀態、是否啟用、是否同步中、上次同步時間與結果、檔案數 / 大小 / 活動期間 |
| GET/PUT | `/api/v1/sync/settings` | 資料來源（`primary_source`：`coros` / `trainingpeaks`）、各來源開關、時區、`daily_sync_time`（每日同步時間）、`auto_on_open`（開網站時自動同步）+ 門檻小時數、`chart_data_source`（`source` = 資料來源 / `wko5`）、`map_basemap` / `map_overlays`（單次活動路線圖的預設底圖與疊加層）、TP OAuth 開關；2026-10 起另有 `exclude_bad_activities`、`use_power`、`accept_watch_power`、`push_provider`、`region_override`、`primary_sport`（欄位對應 `_SETTING_KEYS`，`backend/api/sync.py:194`）。GET 另回傳生效值（`timezone_effective`、`region`、`power_source`、`wko5_available`…）與 secret 來源、金鑰狀態，都只給標籤、不給值。PUT 換資料來源時重建去重 |
| POST | `/api/v1/sync/start`、`/api/v1/sync/coros/start` | 走共用 runner：同一來源已在同步時回 409 `SYNC_BUSY`，結果寫進 `sync.<src>.last_result` |
| GET | `/api/v1/sync/primary` | 2026-10-04：目前資料來源（`source` coros／tp、`label`）、是否登入（含已知過期）、是否啟用、是否同步中；不算檔案統計、不載 Dataset。課表頁「從 COROS 抓活動」用（`backend/api/sync.py:126`） |
| POST | `/api/v1/sync/auto` | 開網站時呼叫。對「已啟用、已登入、閒置、且超過 N 小時」的來源在背景啟動同步，立刻回傳；新鮮、忙碌或關閉時什麼都不做 |
| DELETE | `/api/v1/sync/{coros\|tp}/files[?date_from&date_to]` | 刪掉該來源的 FIT 與 DB 紀錄，重建去重、重設 cursor，並拿該來源的鎖（同步中回 409）。若它正是圖表讀的資料來源且有 WKO5 資料夾，圖表改回 WKO5（`backend/sync/purge.py:100`） |
| GET | `/api/v1/sync/compare?a=&b=&since=` | 兩個資料來源逐筆活動比對：時長、距離、爬升、NP、TSS（含所用 FTP 與來源）。頁面是 `/api/v1/static/compare.html`；沒有 WKO5 資料夾時比 `wko5` 回 404 `NO_WKO5_FOLDER` |
| POST | `/api/v1/auth/tp/login` | 新增 `method` 參數：`auto`（預設）/ `web` / `oauth` |

### 2026-10-02 ~ 10-03 新增 Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/auth/session-alerts` | 正在用的登入（資料來源的同步、COROS 課表推送）中已過期者，給總覽／課表的橫幅（`backend/api/auth.py:164`） |
| PUT | `/api/v1/auth/{coros\|tp}/remember` | 取消勾選「記住密碼」立即刪除已存密碼；勾選本身不存任何東西（密碼只隨登入送出）（`backend/api/auth.py:205`） |
| POST | `/api/v1/auth/tp/logout` | TP 登出 |
| GET | `/api/v1/auth/tp/oauth`、`/api/v1/auth/tp/callback` | TP OAuth 轉址流程（密碼登入的替代） |
| GET | `/api/v1/sync/status` | TP 舊狀態（`authenticated`、`last_sync`、`cursor`） |
| POST | `/api/v1/sync/timezone/browser` | shell.js 送一次瀏覽器的 Intl 時區，當自動時區的輸入之一（`backend/api/sync.py:303`） |
| POST | `/api/v1/sync/dedup/rebuild` | 手動重建跨來源去重 |
| GET | `/api/v1/sync/tp/settings` | 讀 TP 運動員設定（FTP、體重、LTHR），只回傳、不寫 DB |

`GET /auth/coros/status` 與 `/auth/tp/status` 改回 `status`（`logged_in` / `expired` / `logged_out`）、`expired`、`check`（`ok` / `expired` / `unknown`）、`password_saved`（COROS 另有 `auto_relogin`）；不回 token 或密碼（`_session_fields`，`backend/api/auth.py:128`）。`/sync/sources` 的 `logged_in` 也排除已知過期的登入。

**資料來源二選一**（2026-10-02，`backend/sync/primary.py`）：`sync.primary_source` = `coros` | `trainingpeaks`，所選來源是唯一被讀的——圖表 Dataset 只讀它的資料夾（`current_source`，`backend/engine/wko5expr/datasource.py:76`）、自動同步只同步它（`auto_plan`）、CP 測試／比賽功率掃描只讀它的檔（`unused_folder`，`backend/engine/racepower/cptest.py:136`）、DB 總數只算它的列（`in_use_clause`，`backend/sync/dedup.py:34`）。另一個來源的檔案、DB 列和已存登入原封不動，切回來就能用。舊值（`auto`、未設定）在啟動時（lifespan 呼叫 `primary.migrate`，`backend/main.py:76`）或第一次讀取時改成舊「自動」會選的來源：最新活動日較新者，同日或沒資料選 COROS（`choose_initial`，`backend/sync/primary.py:62`）。活動以開始時間為鍵（`backend/engine/activity_key.py`），所以標籤、RPE、背負、課表配對在切換後仍在。設定頁切換前會先確認。

**每日排程**：`backend/sync/scheduler.py`，在 app lifespan 啟動。每分鐘檢查一次，每個本地日期到了 `daily_sync_time` 之後執行一次，只同步資料來源（`runner.auto_plan`）。設 `WKO5COACH_NO_SCHEDULER=1` 可關閉（`backend/main.py:94`；demo 模式改跑 demo 自己的 loop）。

**開網站自動同步**：`backend/static/autosync.js`，已由 shell.js 自動載入（`backend/static/shell.js:415`；demo 模式不載入）。也可以在頁面裡放 `<script src="/api/v1/static/autosync.js" defer></script>`。它每個瀏覽器每 10 分鐘最多呼叫一次，狀態顯示在 `#nav-sync-status`（沒有這個元素就在右上角加一個小徽章）。

**圖表資料來源**（`charts.data_source` = `source`（預設，資料來源的資料夾）| `wko5`；舊值 `synced` / `coros` / `tp` 讀成 `source`）：`backend/engine/wko5expr/fitdataset.py` 用 FIT 資料夾建 `FitFolderDataset`，每筆活動的指標用本專案自己的公式計算。`datasource.current_source()` / `source_stamp()` 提供 Dataset 工廠。9 月 17 筆活動實測對照 WKO5（當時門檻取自 WKO5 athlete 檔）：時長、距離相同，NP ±0.5%，TSS ±0.2，爬升 1–4%。
**手動同步的前端共用**（2026-10-04）：`backend/static/syncrun.js` 的 `TRCSync.run(src, {since, onEvent})` 打 SSE start 端點並解析進度（`total`／`checked`／`downloaded`／`errors`／`fatal`／`finished`，409 → `busy`），`TRCSync.primary()` 讀 `/sync/primary`。設定頁「立即同步」（`backend/static/settings.html:1033`）與課表頁「從 COROS 抓活動」共用，各自只負責顯示文字。

**FIT 資料集的前置（2026-10-01，`docs/research/unsourced-rules.md` §0.10 第 0 步）**：app 的資料要從 TP／COROS 來，WKO5 只當對照。

- **越野／路跑**：`sport_of`（`backend/engine/wko5expr/fitdataset.py:110`）先看 app DB 的 `workout_files.trail_classification`（唯讀開啟，`load_classifications`，`backend/engine/wko5expr/fitdataset.py:150`），FIT 的 session sub_sport 只當後備（COROS 的 FIT 沒有 trail sub_sport，原本整批越野都被當路跑，回測越野 n = 0；sub_sport 先前也根本沒被讀進來，`fit_to_channels` 現在帶出 `sub_sport`）。跨來源重複（`duplicate_of`）視為同一筆活動：群組裡任一列的使用者覆寫優先（自己這列 → 主紀錄 → 其他重複列），否則用自己這列的自動值，再退到主紀錄（`classification_for`，`backend/engine/wko5expr/fitdataset.py:201`）。每個來源的資料集仍保留自己的檔案（不因為是重複列就丟掉，否則該來源會少活動）。越野跑同時加上 WKO5 的 `runningtrail` 標籤，因為 thresholds／品質門檻／status／成就只看標籤。
- **門檻與體重**，依序：賽季計畫的 dated 列（`Dataset.setting` / `cp`）→ app DB 的 `athlete_settings`（體重、`run_ftp_w`、閾值配速；`_load_db_settings`，`backend/engine/wko5expr/fitdataset.py:725`）→ 從這些 FIT 估算的 as-of LTHR（`_estimate_settings`，`backend/engine/wko5expr/fitdataset.py:775`：每 30 天一個格點，推估；格點日只用當天以前的跑步，`thresholds.estimate`，每次跑步對照它自己日期的 `racepower.athlete.cp_as_of`，估出的值只套用到格點日以後；只在有 app DB 時自動估算）→ 未設定。跑步 FTP（功率 TSS）**不**用 `cp_as_of` 的估算值補：它的 PD 重擬在第一筆計畫 CP 之前沒有合理性參考，在一位跑者的 COROS 資料上，手錶功率時期的擬合值比計畫 CP 高約 70%，會讓那段時間的功率 TSS 少到約三分之一。**2026-10-03 起**跑步功率 TSS 的 FTP 改為 `tss_ftp`（`backend/engine/wko5expr/fitdataset.py:996`）＝圖表用的同一個 CP：計畫 CP 測試 → `run_ftp_w` → **只用 Stryd 跑步**的 PD 模型 mFTP（推估，手錶功率永遠不進擬合；見「圖表分析在 COROS 來源」）→ 都沒有時用 rTSS／hrTSS（移動時間 hrTSS ＋ 爬升加成）。標籤寫出來源（`cp_info`），`/workouts` 與來源比對頁顯示所用 FTP。parity 模式與 WKO5 opt-in 仍用 WKO5 規則（`Dataset.tss_ftp`）。測試：`backend/tests/test_run_ftp_tss.py`。實測（2026-10-01，一位跑者同步進行中的 COROS 803 筆）：LTHR 估算只有前後兩段時期有值，中間約 17 個月沒有 LTHR，那段 COROS 跑步在 app 路徑上沒有 TSS。WKO5 athlete 檔只有在設定 `charts.fit_settings_from_wko5 = true`（預設 false，`backend/settings/repository.py:68`）時才讀（`dataset_for_source`，`backend/engine/wko5expr/fitdataset.py:1097`）。各處的來源標籤改走 `Dataset.setting_label`，FIT 資料集不再顯示「WKO5 設定」。
- **`athlete_settings.lthr` / `ftp_w` 不當跑步門檻**：唯一的自動寫入者是 `coros_client.login`（COROS 帳號 `zoneData.lthr` / `.ftp` 與體重，日期 = 登入當天 UTC），沒有記錄是哪個運動；TP 的 `fetch_tp_settings` 只回傳 JSON、不寫 DB。實測時 DB 裡那列是 COROS 登入寫的，不是 TP；它的 LTHR 高於同一天 12′ 全力測試的峰值心率，不可能是現在的跑步 LTHR。這兩欄留在 `settings_ignored` 供顯示，體重照用。
- **快取**：`source_stamp` 多帶 `db_stamp()`（`backend/engine/wko5expr/datasource.py:97`），分類覆寫、去重或 `athlete_settings` 變了，即使 FIT 檔沒變也會重建 Dataset。`FitFolderDataset.cached_series`（`backend/engine/wko5expr/fitdataset.py:1029`）是每個 FIT 檔的磁碟快取（fitcache 資料夾的 `series_<key>.json`），key 含檔案 stamp、修正與當時的門檻，每檔保留幾組門檻版本（估算前／後）。
- 測試：`backend/tests/test_fit_dataset_prereqs.py`（合成 FIT ＋ tmp SQLite，不碰 WKO5 資料夾與真實 DB）。

**時區**：`FitFolderDataset` 把 FIT 的 UTC 起始時間換成運動員當地時間再取日期（`backend/engine/wko5expr/fitdataset.py:494`、`backend/engine/wko5expr/fitdataset.py:548-550`；naive 時間視為 UTC），時區來源與同步一致：`athlete.timezone` 設定 → `WKO5COACH_TZ` → 自動偵測（`athlete.timezone.auto`：同步下載新檔後由最新 FIT 的當地時間偏移決定，瀏覽器 Intl 時區一致時優先、含日光節約；`backend/engine/localtime.py`）→ 系統時區（`athlete_tz`，`backend/engine/wko5expr/datasource.py:90`；`resolve_tz`，`backend/settings/repository.py:425`）。測試：`backend/tests/test_scan_and_tz.py:115`、`backend/tests/test_scan_and_tz.py:124`、`backend/tests/test_scan_and_tz.py:130`、`backend/tests/test_region_time.py`。

**路線圖設定**（`charts.map.basemap` / `charts.map.overlays`，`backend/settings/repository.py:100-101`）：預設底圖 `None` = 依地區（tw `rudy`、intl `osm`，`backend/api/sync.py:244-246`）、無疊加層。底圖限 `MAP_BASEMAPS`、疊加層須為 `MAP_OVERLAYS` 內不重複的清單（`backend/settings/repository.py:203-204`、`backend/settings/repository.py:293-298`），不合法時 `PUT /sync/settings` 回 400。API 欄位對應在 `backend/api/sync.py:194-206`。地圖本身屬 viewer，見 wko5-engine.spec.md。

**接線**：`_dataset()`（`backend/api/wko5views.py:133`）經 `_dataset_key`（`backend/api/wko5views.py:122`）讀 `current_source()`——資料來源的資料夾（`coros` / `tp`），只有 `charts.data_source = wko5` 且有 WKO5 athlete 檔時才是 `wko5`——並以 `source_stamp()` 當快取 key（`backend/api/wko5views.py:129`）；`_dataset_cfg`（`backend/api/wko5views.py:82`）呼叫 `dataset_for_source(source, ATHLETE_DIR, config)`（`backend/api/wko5views.py:94`）：`coros` / `tp` 建 `FitFolderDataset`（只讀一個資料夾，不合併），`wko5` 照舊是 WKO5 `Dataset`。總覽（`backend/api/overview.py:29`）與功率計算機（`backend/api/racepower.py:52`）都走同一個 `_dataset()`。render cache 把 dataset 的 `source` / `source_stamp` 放進 key（`backend/engine/wko5expr/render_cache.py:104`），圖表請求本身也帶 `source`，所以換來源或同步新檔案都不會拿到舊圖。圖表頁右上角有資料來源切換（`#source-chip` + `sourcechip.js`，`backend/static/wko5_viewer.html:393`），設定頁的說明也改成已生效（`backend/static/settings.html:265`）。實測（2026-09-30，本機資料）：`coros` 17 筆活動，5 個 view 共 186 張圖 0 錯誤；`wko5` 預設的輸出與改動前相同（只少了地圖面板不再使用的 `track`）。

**COROS 課表推送**（`backend/sync/coros_workouts.py`，把本專案的計畫課表依日 / 週 / 期推到 COROS 並記錄在 `coros_plan_push` 表）：屬於計畫功能，規格見 overview.spec.md。

> TP client（`backend/sync/tp_client.py`，649 行）已於 2026-05-15 對 live TP OAuth 驗證：password grant → athlete download → `filedata` 端點回 base64-gzip FIT → decode/inflate。M3 主要是把它接到 UI 並加盤點，不需重新逆向格式。

### Request / Response

**POST /api/v1/auth/coros/login**
```json
// Request
{ "email": "user@example.com", "password": "plaintext_password", "athlete_id": 1, "remember": false }

// Response 200
{
  "authenticated": true,
  "coros_user_id": "<user_id>",
  "email": "user@example.com",
  "region": "eu",
  "data_server": "https://teamapi.coros.com",
  "ftp_w": 230,
  "lthr": 178,
  "token_expires": "2026-05-16T02:15:00Z",
  "password_saved": false
}

// Response 409 — 另一個登入進行中（COROS_LOGIN_BUSY）；503 — 本機金鑰缺少（SECRET_KEY_MISSING）

// Response 401
{ "detail": "COROS_LOGIN_FAILED: The login credentials you entered do not match our records." }
```

**GET /api/v1/auth/coros/status**
```json
{
  "authenticated": true,
  "expired": false,
  "status": "logged_in",            // logged_in | expired | logged_out
  "check": "ok",                    // ok | expired | unknown（session_check）
  "email": "user@example.com",
  "token_expires": "2026-05-16T02:15:00Z",
  "last_sync": "2026-05-15T02:10:00Z",
  "password_saved": false,
  "auto_relogin": false
}
```

**POST /api/v1/sync/coros/start** — SSE stream
```
event: sync_progress
data: {"status": "started", "since": "20260514", "until": "20260515"}

event: sync_progress
data: {"status": "checking", "activity_id": "<label_id>", "date": "2026-05-14"}

event: sync_progress
data: {"status": "downloaded", "activity_id": "<label_id>", "file": "..._trail_run.fit", "sport": "trail_run"}

event: sync_progress
data: {"status": "skipped", "activity_id": "...", "reason": "already_imported"}

event: sync_progress
data: {"status": "error", "activity_id": "...", "error": "detail/download error: ..."}

event: sync_progress
data: {"status": "complete", "total_downloaded": 2, "total_checked": 2, "errors": []}
```

**POST /api/v1/pmc/recompute**
```json
// Response
{ "updated": 59, "ftp_w": 230.0 }
```

---

## TSS 計算

### 目前實作（暫用 Coggan 標準公式）

```
TSS = (NP / FTP)² × (duration_s / 3600) × 100
```

- `NP` = normalized_power_w（已從 FIT 計算，或 fallback avg_power_w）
- `FTP` = athlete_settings.ftp_w（從 Coros 登入自動匯入）

此為 `POST /api/v1/pmc/recompute` 使用的公式，對既有無 TSS 的活動批次計算。

### 已實作（2026-05-16 更新）

**Running TSS** 使用 power-based TSS，FTP 為 runFTP（從 90 天跑步 MMP 曲線動態計算）：
```python
runFTP = compute_run_ftp_from_mmp(mmp_90day)   # CP model: P=CP+W'/t
TSS = (duration × NP × IF) / (runFTP × 3600) × 100
# WKO5 formula: tl(if(sport="run", tss), ctlconstant)
```
`get_run_ftp(db, athlete_id, as_of_date)` 在 `backend/files/file_service.py` 計算，先查 `athlete_settings.run_ftp_w`（手動設定），無則從 MMP 自動算。

**重新計算端點**：`POST /api/v1/athletes/{id}/recalculate-running-metrics` — 重算所有跑步 FIT 活動的 TSS、intensity 指標。

| 運動類型 | 目標公式 | 狀態 |
|----------|----------|------|
| Cycling（有功率） | WKO5 TSS（cycling ftp_w） | ✅ 已實作 |
| Running（Coros 跑步功率） | power-based TSS（runFTP） | ✅ 已修正 |
| HR-only | hrTSS（by LTHR） | ⬜ 待實作 |
| 其他（strength, custom）| 0 或不計入 PMC | ✅ 不計入 |

### 功率來源（2026-10-01，`backend/engine/power_source.py`）

同一個 FIT `power` 欄位裝了兩種功率，WKO5／TrainingPeaks 都分不出來（它們讀的是同一份
COROS 上傳的 FIT；抽查的兩筆，兩邊的檔案功率完全相同）：

| 來源 | 判定 |
|---|---|
| `stryd` | 紀錄裡有 Stryd 開發者欄位（Form Power、Air Power、Leg Spring Stiffness，經 COROS 轉寫，`developer_data_id` 是 COROS 的），或 `device_info` 有 Stryd（manufacturer 95） |
| `watch` | 有功率但沒有上述欄位／裝置：手錶從手腕推估（配了 Stryd 的跑者也會有零星幾次沒配對） |
| `none` | 沒有 > 0 的功率 |

把有 Form Power 等欄位的跑步當成 Stryd、沒有的當成手錶推估，是推估（手錶本身不算 form power）。

- `FitChannels.power_source`（`backend/files/fit_to_channels.py`，同時讀 `device_info`）；
  `FitFolderDataset` 載入時記在每筆 workout；WKO5 `.wko4` 由 `Dataset.power_source` 從 channel
  判定（依檔案 stamp 快取在 `power_source_v1.json`）。`.wko4` 存的是同一個 `power` channel
  加上裝置名稱，沒有來源旗標。
- 設定 `power.accept_watch_power`（預設 false；parity 模式一律讀全部功率，同 WKO5）：
  false 時手錶推估功率不算功率 TSS（改用 rTSS／hrTSS，`metrics.power_tss_blocked`），也不進
  功率模型（`docs/spec/racepower.spec.md`）。心率、配速路徑照常使用這些跑步。設定值併入
  `source_stamp`，切換後 Dataset 會重建。
- API：`GET /api/v1/wko5/workouts` 每筆多 `power_source`、`power_label`（手錶功率未採用時為
  「手錶推估功率（未採用）」）；單次活動卡顯示「功率來源」。

### 壞掉的活動檔（2026-10-01，`backend/engine/bad_activity.py`）

忘了停錶就騎車／開車的「跑步」整筆排除：留在 DB 與活動清單（標「已排除：疑似交通工具／騎車（均速
43 km/h）」），但 `FitFolderDataset` 載入時就不放進 `ds.workouts`（WKO5 `Dataset` 在建 index 前濾掉、
重新編號），所以 PMC／TSS、功率曲線、比賽功率、圖表都讀不到；`cptest.curves`／`scan` 讀的 COROS
檔也一樣濾掉（`cptest.bad_files`）。規則、門檻與來源見 `docs/spec/workouts.spec.md`「Bad activity
files」：均速或持續 60 秒／5 分／20 分的速度超過同時間世界紀錄均速 × 1.15（推估），或平均功率
> 10 W/kg（推估）。使用者覆寫存在 `activity_tags.exclusion`（`keep` / `exclude`），設定
`activities.exclude_bad`（預設 true）；兩者都併入 `source_stamp`。parity 模式不排除。
TP 的那筆垃圾檔（下節）就是這條規則抓的：不再只靠「沒有 Stryd 欄位」擋下（已知限制 7）。

### 圖表分析在 COROS 來源（2026-10-01，fix/charts-coros-source）

資料來源為 COROS、圖表讀資料來源（當時設定值為 `charts.data_source = coros`）後，「周期化訓練」幾張圖空白或數字不對，原因與修正（實測 802 筆，唯讀）：
- **VAM 圖空白**：FIT 資料集沒算 4224 `vam`（全部 NaN），登山／健行也沒有 `hiking` / `mountaineering`
  標籤（圖用 `hastag()` 選）。`workout_fields` 補 `vam = round(climbing / duration · 3600)`（WKO5 定義，
  含停留時間，所以多日百岳的 VAM 很低：例如三天行程約 45 m/h），`TYPE_TAGS` 依運動類型加標籤。
  近一年：越野 20 點、登山 4 點（之前 0／0）。
- **Palladino 區間沒資料**：計畫 CP 從第一次 CP 測試那天才有，之前的跑步沒有 CP（WKO5 設定不再讀、
  `run_ftp_w` 空），近 30 天 18 次跑步只算到測試那一次。`FitFolderDataset.cp`：計畫測試 →
  `run_ftp_w` → **只用 Stryd 跑步**的 PD 模型（`racepower.athlete.pd_model`，90 天窗、每 30 天一格、
  窗內 ≥ 5 次 Stryd 跑步，推估；手錶功率永遠不進來，壞檔已在 `ds.workouts`／`cptest.curves` 外）→ 未設定。
  擬合值大致落在計畫 CP 的 0.77–0.92 倍；窗內只有 1 次跑步的格點（擬合值明顯偏低）被門檻擋下。
  2026-10-03 起跑步功率 TSS 也用這個值（`tss_ftp`，見上方「門檻與體重」）。區間表的秒數改用表上印的門檻（當天的 CP）計算，表頭寫出來源與日期、W′
  （有測才有；PD 擬合時顯示 FRC ≈ W′，推估）。近 30 天：39 999 s，1C+Z2 佔 52%。
- **Friel 配速區間沒資料**：計畫沒有閾值配速欄位，`athlete_settings.threshold_pace_s_per_km` 是空的（TP
  沒寫進 DB；COROS 登入那列的 LTHR／FTP 本來就不用）。`thresholds.estimate_tpace`（推估）：
  先用 CP × 近 90 天 Stryd 路跑的速度／功率比（中位數）＝ CP 對應的平路配速；沒有 Stryd 時才用「心率在
  LTHR ±3% 的最快 20 分鐘」中位數（Friel 30 分鐘測試）。後者在夏天讀得太慢（心率飄移：夏天心率在
  LTHR 時配速明顯變慢），比一場半程路跑賽的實際配速還慢約 17%，所以排第二。實測：例如 CP 220 W、
  速度／功率比 12.6 mm/s/W（40 次）→ 6:01 /km；近 30 天 Z1+Z2 81%。估算值只給區間表，不當 rTSS 的閾值配速。
- **課表建議強度**：新增「山路長天／越野輕鬆」（功率 0.75–0.88 CP、心率 ≤ AeT）與「爬坡重複」（功率
  0.95–1.06 CP）兩列，看功率、心率第二；「長跑」改成路跑。依據與限制寫在 `zones.TERRAIN_NOTE`：心率延遲
  τ ≈ 60 s（Hunt 2015／2019）、Stryd 在 0–8% 坡 ≈ 固定代謝負荷（van Rassel 2026）、Stryd 自己說陡峭技術
  地形不能用單一功率數字（陡的技術下坡不看功率）、陡坡負重健行 UA 仍以心率為主；> 8% 坡套功率區間是推估。
  總覽的「長時間輕鬆（山路）」仍用 `long` 那列（心率）——還沒改。
- **訓練量週增幅太大**：公式（本週 ÷ 上週 − 1，所有運動的移動時間）沒算錯，也沒有重複或壞檔灌水
  （COROS 資料夾近一年沒有來源內重複、排除 1 筆；TP 的重複是另一個來源）。數字大是因為單週比單週：
  某週有三天百岳（經過時間約 49 h），讓那週 +131%、下週 −86%；其他週也有 +30%～+90% 的單週跳動。
  UA 說的是「平均」每週 > 10% 持續約 8 週，所以改成「近 4 週平均」的週變化（沒有活動的週算 0）：
  近 20 週落在 −45%～+44%，最大 +44% 仍是百岳那週、−45% 是它離開 4 週窗的那週。

### COROS 與 TP 資料集差異（2026-10-01 實測，唯讀）

TP 1086 筆（796 筆標 `duplicate_of`），COROS 808 筆。功率回測差異的來源：
- **TP 獨有的一筆「跑步」**：約 17 分鐘 12 km（約 43 km/h）、平均功率約 900 W，手錶錄的，沒有
  Stryd 欄位；COROS 資料夾沒有這筆（推定：在 COROS 端刪除過，TP 留著）。它落在一場半程路跑賽
  前的 90 天窗內，讓 PD 模型擬合失敗，詳見 racepower.spec.md「COROS vs TP」。
- TP 缺 COROS 有的十筆早期 Stryd 跑步（TP 的 Stryd 檔晚約一個月才開始）；另有三筆 TP 獨有的
  短跑。其餘配對到的跑步 mean-max 完全相同。
- （當時）`cptest.curves` / `scan` 只讀檔名有 `YYYY-MM-DD` 的 FIT，TP 的 `tp_YYYY_MM_DD_…` 檔名不會進去，
  所以 TP 回測的 PD 擬合其實也混進了 COROS 的檔案。現況：`_file_date`（`backend/engine/racepower/cptest.py:126`）
  兩種檔名都讀，且只掃資料來源的資料夾、跳過另一個（`unused_folder`，`backend/engine/racepower/cptest.py:136`）。
- 同來源內的重複（coros→coros 9 筆、tp→tp 7 筆）`FitFolderDataset` 不去掉；mean-max 取最大值，
  所以不影響 envelope。

---

## Non-Functional Requirements

| NFR | Target |
|-----|--------|
| Coros API timeout | 30s per list/auth request |
| FIT download timeout | 60s per file |
| 單次活動失敗 | 記錄 error event，繼續下一筆，不中斷 sync |
| Token 儲存 | DB 內以 `secrets.seal`（Fernet，本機金鑰）加密；API 永不回傳 token／密碼 |
| Rate limit | 活動下載無強制延遲，依實測調整 |
| Token 刷新 | 有記住密碼時自動重新登入一次；否則狀態為「登入已過期」，設定頁重新顯示登入表單、總覽／課表顯示橫幅 |
| 登入檢查頻率 | 每來源最多每 300 s 一次認證呼叫（連不上時 60 s 後重試） |

---

## Risks

| Risk | Likelihood | Impact | Mitigation | 現況 |
|------|-----------|--------|------------|------|
| Coros API endpoint 改版 | Medium | High | 追蹤 `xballoy/coros-api` issue | 已偵測到 `/activity/fit/url` 失效，改用 `/activity/detail/download` |
| Region server 變動 | Low | High | 每次登入重新偵測 base URL | 已實作 `_detect_data_base()`（`backend/sync/coros_client.py:125`） |
| `yfheader` 要求改版 | Low | Medium | 動態讀取 userId，易調整 | 已實作 |
| Token TTL 縮短 | Low | Medium | 長時間 sync 時 token 可能 mid-sync 失效 | 發生時記 error，提示重新登入 |
| 運動類型無 FIT | Low | Low | `detail/download` 返回 error，已 graceful skip | 已測試 custom (9904)、strength (402) 均 skip |

---

## 已知限制

1. **Token mid-sync 失效**：長時間 sync（>200 筆）偶見 "Access token is invalid"，原因未知（可能 Coros server-side invalidation）。重新登入後繼續 sync 可恢復；有記住密碼時同步中會自動重新登入一次並重試該頁（`{"status": "relogin"}`），再失敗才標成登入已過期。
2. **無功率資料活動的 TSS**：hiking、球類等活動無 power 也無 HR zones，TSS=0，不計入 PMC。
3. **PMC 起始點（歷史資料缺口）**：完整 Coros 歷史資料現已匯入（667 筆 Coros 活動，321 筆跑步）。但 WKO5 本機 `.wko4` 二進位格式的跑步活動仍無法解析 power/HR channel，貢獻 0 TSS。這導致 ATL 與 WKO5 顯示值有差異——WKO5 能讀取 wko4 跑步功率，我們不能。
4. ~~檔名 sport 標籤不準確~~（2026-09-30 已處理）：檔名的運動字改由 FIT session sport／sub_sport 決定，COROS `sportType` 只當後備（`sport_token`，`backend/sync/coros_sport.py`；`SPORT_NAMES` 現為 `COROS_SPORT_TYPES` 的別名，100 = run）；舊的 `*_cycling.fit` 跑步檔用 `backend/scripts/migrate_coros_sport_names.py` 改名。DB 的 `sport` 欄位一直由 `fit_reader.py` 解析 FIT 決定，不受影響。
5. **損壞的 FIT 檔案**：部分 Coros FIT 檔案無效（e.g., 某個 `<labelId>_<date>_other.fit`，FitParseError: Invalid field size）。已修正：`coros_client.py` 現在對無法解析的 FIT 建立 `file_format="corrupt"` 的 stub DB 記錄，避免每次 sync 重複下載。
6. **Coros 功率尖峰**：跑步功率由 Coros 手錶從加速度計/GPS 估算，偶有短暫尖峰（e.g., 某次有 4 個樣本約達 2 × CP）。對 3-30 分鐘 MMP 的 CP 模型計算（runFTP）無影響，但會污染 1–3 秒 MMP 顯示值。配了 Stryd 之後的跑步功率多半來自 Stryd（見「功率來源」）。
7. ~~垃圾功率檔只靠來源規則擋下~~（2026-10-01 已處理）：TP 的那筆約 900 W 的檔現在被「壞掉的活動檔」規則整筆排除（均速 43 km/h），打開 `power.accept_watch_power` 也不會再進 PD 擬合；除非使用者把它標成「這筆是正常的」。
8. **圖表引擎**（WKO5 clone 的 `meanmax(power)`、`ftp(meanmax(power))` 等）照 WKO5 讀全部功率，不套用來源規則。

---

*Generated: 2026-05-15*
*Last updated: 2026-10-04*
*Status: IMPLEMENTED — M4 + runFTP bug fix + corrupt FIT handling + historical data expansion*
