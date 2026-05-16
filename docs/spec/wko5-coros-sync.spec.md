# SRS: Coros Sync — 非官方 API 自動下載 FIT

## Metadata
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md` — Milestone 4
- **Source Linear Issue**: N/A — standalone
- **Owner**: vi000246
- **Status**: IMPLEMENTED
- **Generated**: 2026-05-15
- **Last updated**: 2026-05-15

---

## Summary

實作 Coros 非官方 API 客戶端，讓使用者以 Coros Training Hub 帳密登入，自動下載 .fit 檔案到 `~/.wko5coach/fits/{athlete}/{year}/`，完全不依賴 WKO5 資料夾或 TrainingPeaks 帳號。下載完成後觸發 FIT 解析、指標計算、TSS/PMC 流程。

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
    "userId":      "467711295934709760",
    "accessToken": "<token>",           // 在 data.accessToken，不是 result.accessToken
    "zoneData": {
      "ftp":  200,    // 單位 W，從 Coros profile 自動帶入
      "lthr": 182,    // 心率閾值
      "rhr":  53
    },
    "weight": 70.5,
    "maxHr":  202,
    "criticalPower": 186
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

**偵測策略**：登入成功後，對每個 base URL 發一次 `/activity/query` 測試請求，找第一個回傳 `result=="0000"` 的 server，儲存為 `coros_base_url`。

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
        "labelId":   "477483755548737936",   // unique activity ID
        "name":      "台北市 跑步",
        "sportType": 100,        // 100=cycling, 102=trail run, 105=hiking, 200=run, 402=strength, 9904=custom
        "date":      20260514,   // YYYYMMDD 8位整數，不是 Unix timestamp
        "startTime": 1747282306, // Unix timestamp（不用於日期解析）
        "totalTime": 4691,       // seconds
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
- .fit 檔案下載到 `~/.wko5coach/fits/{athlete_name}/{year}/`
- 新活動自動觸發 FIT 解析 + TSS/MMP 計算
- 重複活動跳過（依 `coros_activity_id` 去重）
- SSE 串流同步進度
- Token 持久化到 DB（`sync_state` 表）
- FTP/LTHR/weight 從登入回應自動匯入 `athlete_settings`
- PMC recompute endpoint（FTP 更新後重新計算 TSS）

### Out of Scope
- WKO5 資料夾讀寫（完全獨立）
- TrainingPeaks 作為主要資料源
- 心跳同步 / WebSocket push
- Coros Training Plans / Structured Workouts 解析
- 多運動員帳號切換

---

## System Context

### Actors

| Actor | Type | Interaction |
|-------|------|-------------|
| 個人運動員 | Human — Browser | 觸發 sync、查看 PMC、設定 FTP |
| Coros Training Hub API | External Service | 提供活動列表 + FIT 下載 URL |
| 本機 FileSystem | Storage | `~/.wko5coach/fits/` 儲存 .fit |

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
│   ├── tp_client.py            # 保留（TP optional）
│   └── coros_client.py         # Coros API 客戶端（已實作）
├── api/
│   ├── auth.py                 # /auth/coros/* endpoints（已實作）
│   ├── sync.py                 # /sync/coros/start SSE endpoint（已實作）
│   └── pmc.py                  # /pmc/recompute endpoint（已實作）
├── db/
│   ├── models.py               # 含 coros_* 欄位（已實作）
│   └── database.py             # _migrate_schema() 自動 ALTER TABLE（已實作）
└── files/
    └── file_service.py         # _import_one_file(coros_activity_id=...)（已更新）

frontend/
└── src/
    ├── components/
    │   └── PmcChart.tsx        # CTL/ATL/TSB Recharts LineChart（已實作）
    ├── pages/
    │   └── Dashboard.tsx       # CorosPanel + PmcChart（已實作）
    └── api/
        ├── client.ts           # CorosLoginRequest/Response/Status types（已實作）
        └── hooks.ts            # useCorosStatus/Login/Sync hooks（已實作）
```

### FIT 儲存路徑

```
~/.wko5coach/
├── wko5coach.db           # SQLite DB
└── fits/
    └── {athlete_name}/
        └── {year}/
            └── {coros_id}_{YYYY-MM-DD}_{sport}.fit
```

### 同步流程

```
POST /api/v1/sync/coros/start?since=YYYY-MM-DD
  ↓
_get_token_and_base(db) → (token, base_url, user_id)
  ↓
_list_page(token, base, user_id, since, end, page) → activities[]
  ↓
for each activity:
  if coros_activity_id in DB → SSE: skipped
  _download_fit(token, base, user_id, activity):
    1. try fitUrl (presigned S3, if present)
    2. POST /activity/detail/download?labelId=...&sportType=...&fileType=4
    3. GET presigned URL → bytes
  save to ~/.wko5coach/fits/{athlete}/{year}/{filename}.fit
  _import_one_file(db, athlete_id, dest, source="coros", coros_activity_id=id)
    → parse FIT, compute metrics (TSS if FTP available, MMP, HR zones)
  SSE: downloaded / error
  ↓
update sync_state.coros_last_sync_at
SSE: complete {total_downloaded, total_checked}
```

---

## Data Model

### `workout_files` 表新增欄位

```sql
ALTER TABLE workout_files ADD COLUMN coros_activity_id TEXT;  -- index, unique per activity
ALTER TABLE workout_files ADD COLUMN coros_sport_type INTEGER;
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
```

### `athlete_settings` 表（已有，從 Coros 登入自動填入）

登入成功後自動 upsert：
- `ftp_w` ← `data.zoneData.ftp`（200W 實測）
- `lthr` ← `data.zoneData.lthr`（182 實測）
- `weight_kg` ← `data.weight`（70.5kg 實測）
- `effective_date` ← 今日

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

### Request / Response

**POST /api/v1/auth/coros/login**
```json
// Request
{ "email": "user@example.com", "password": "plaintext_password", "athlete_id": 1 }

// Response 200
{
  "authenticated": true,
  "coros_user_id": "467711295934709760",
  "email": "user@example.com",
  "region": "eu",
  "ftp_w": 200,
  "lthr": 182,
  "token_expires": "2026-05-16T02:15:00Z"
}

// Response 401
{ "detail": "COROS_LOGIN_FAILED: The login credentials you entered do not match our records." }
```

**GET /api/v1/auth/coros/status**
```json
{
  "authenticated": true,
  "email": "user@example.com",
  "token_expires": "2026-05-16T02:15:00Z",
  "last_sync": "2026-05-15T02:10:00Z"
}
```

**POST /api/v1/sync/coros/start** — SSE stream
```
event: sync_progress
data: {"status": "started", "since": "20260514", "until": "20260515"}

event: sync_progress
data: {"status": "checking", "activity_id": "477483755548737936", "date": "2026-05-14"}

event: sync_progress
data: {"status": "downloaded", "activity_id": "477483755548737936", "file": "..._cycling.fit", "sport": "cycling"}

event: sync_progress
data: {"status": "skipped", "activity_id": "...", "reason": "already_imported"}

event: sync_progress
data: {"status": "error", "activity_id": "...", "error": "detail/download error: ..."}

event: sync_progress
data: {"status": "complete", "total_downloaded": 2, "total_checked": 2}
```

**POST /api/v1/pmc/recompute**
```json
// Response
{ "updated": 59, "ftp_w": 200.0 }
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

---

## Non-Functional Requirements

| NFR | Target |
|-----|--------|
| Coros API timeout | 30s per list/auth request |
| FIT download timeout | 60s per file |
| 單次活動失敗 | 記錄 error event，繼續下一筆，不中斷 sync |
| Token 儲存 | DB 明文（本機 SQLite，可接受） |
| Rate limit | 活動下載無強制延遲，依實測調整 |
| Token 刷新 | 過期前需重新登入（UI 顯示 "token expired" 提示） |

---

## Risks

| Risk | Likelihood | Impact | Mitigation | 現況 |
|------|-----------|--------|------------|------|
| Coros API endpoint 改版 | Medium | High | 追蹤 `xballoy/coros-api` issue | 已偵測到 `/activity/fit/url` 失效，改用 `/activity/detail/download` |
| Region server 變動 | Low | High | 每次 sync 重新偵測 base URL | 已實作 `_detect_data_base()` |
| `yfheader` 要求改版 | Low | Medium | 動態讀取 userId，易調整 | 已實作 |
| Token TTL 縮短 | Low | Medium | 長時間 sync 時 token 可能 mid-sync 失效 | 發生時記 error，提示重新登入 |
| 運動類型無 FIT | Low | Low | `detail/download` 返回 error，已 graceful skip | 已測試 table tennis (9904)、strength (402) 均 skip |

---

## 已知限制

1. **Token mid-sync 失效**：長時間 sync（>200 筆）偶見 "Access token is invalid"，原因未知（可能 Coros server-side invalidation）。重新登入後繼續 sync 可恢復。
2. **無功率資料活動的 TSS**：hiking、table tennis 等活動無 power 也無 HR zones，TSS=0，不計入 PMC。
3. **PMC 起始點（歷史資料缺口）**：完整 Coros 歷史資料（2020-11 起）現已匯入（667 筆 Coros 活動，321 筆跑步）。但 WKO5 本機 `.wko4` 二進位格式的跑步活動（2023–2025/11）仍無法解析 power/HR channel，貢獻 0 TSS。這導致 ATL 與 WKO5 顯示值有差異——WKO5 能讀取 wko4 跑步功率，我們不能。
4. **檔名 sport 標籤不準確**：`SPORT_NAMES` 映射（e.g., `200="run"`）只影響 FIT 檔名，不影響 DB 中的 `sport` 欄位。`sport` 由 `fit_reader.py` 解析 FIT session 內的實際運動類型後正規化（`"running"`）。
5. **損壞的 FIT 檔案**：部分 Coros FIT 檔案無效（e.g., `476897474257125477_2026-04-19_other.fit`，FitParseError: Invalid field size）。已修正：`coros_client.py` 現在對無法解析的 FIT 建立 `file_format="corrupt"` 的 stub DB 記錄，避免每次 sync 重複下載。
6. **Coros 功率尖峰**：跑步功率由 Coros 手錶從加速度計/GPS 估算，偶有短暫尖峰（e.g., 2026-04-26 有 4 個樣本達 400–432W）。對 3-30 分鐘 MMP 的 CP 模型計算（runFTP）無影響，但會污染 1–3 秒 MMP 顯示值。

---

*Generated: 2026-05-15*
*Last updated: 2026-05-16*
*Status: IMPLEMENTED — M4 + runFTP bug fix + corrupt FIT handling + historical data expansion*
