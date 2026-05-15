# SRS: Coros Sync — 非官方 API 自動下載 FIT

## Metadata
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md` — Milestone 4
- **Source Linear Issue**: N/A — standalone
- **Owner**: vi000246
- **Status**: DRAFT
- **Generated**: 2026-05-15

---

## Summary

實作 Coros 非官方 API 客戶端，讓使用者以 Coros Training Hub 帳密登入，自動下載 .fit 檔案到 `~/.wko5coach/fits/{athlete}/{year}/`，完全不依賴 WKO5 資料夾或 TrainingPeaks 帳號。下載完成後觸發現有的 FIT 解析與指標計算流程。

---

## Coros 非官方 API（逆向工程現況，2026-05-15）

### 社群參考實作

| 專案 | 語言 | 功能 |
|------|------|------|
| `xballoy/coros-api` | TypeScript/NestJS | Bulk FIT export，有日期篩選 |
| `cygnusb/coros-mcp` | Python | MCP server，sleep/HRV/activity |
| `CuberL/coros-mcp` | Python | MCP server |
| `rowlando/coros-workout-mcp` | Python | MCP，Claude Desktop 整合 |

### 認證流程

```
POST https://teamcnapi.coros.com/account/login
Content-Type: application/json

{
  "account":  "<email>",
  "passwd":   "<md5(password)>",  // MD5 hex digest，不是明文
  "accountType": 2                 // 2 = email login
}
```

**回應**:
```json
{
  "apiCode": "1",
  "message": "OK",
  "result": {
    "userId":       "<user_id>",
    "accessToken":  "<token>",
    "tokenExpiry":  "<unix_timestamp_ms>"
  }
}
```

後續 API 呼叫帶 header：`accessToken: <token>`

### 活動列表

```
GET https://teamcnapi.coros.com/activity/query
    ?size=20&pageNumber=1&startDay=20260101&endDay=20260515
Headers: accessToken: <token>
```

**回應結構** （關鍵欄位）:
```json
{
  "apiCode": "1",
  "result": {
    "totalCount": 523,
    "dataList": [
      {
        "labelId":   "<activity_id>",     // unique activity ID
        "name":      "Cycling",
        "sportType": 100,                  // 100=cycling, 200=run, etc.
        "startTime": 20260514103000,       // YYYYMMDDHHmmss
        "endTime":   20260514120000,
        "totalTime": 3600,                 // seconds
        "fitUrl":    "<presigned_s3_url>"  // 直接下載 URL（有時效）
      }
    ]
  }
}
```

### FIT 檔案下載

```
GET <fitUrl>    // presigned S3 URL，直接下載原始 .fit bytes
```

或部分實作使用：
```
GET https://teamcnapi.coros.com/activity/fit/url?labelId=<id>&sportType=<type>
Headers: accessToken: <token>
```

**注意**：各社群實作使用的 endpoint 略有差異，需以實際回應為準。

### Token 刷新 / 重新登入
Coros API token 有過期時間（tokenExpiry），過期後需重新登入。目前社群實作多為重新 POST `/account/login`，尚無 refresh token 流程確認。

---

## Scope

### In Scope
- Coros 帳密登入（MD5 password）
- 活動列表分頁拉取（含日期篩選）
- .fit 檔案下載到 `~/.wko5coach/fits/{athlete_name}/{year}/`
- 新活動自動觸發 FIT 解析 + MMP 計算
- 重複活動跳過（依 `coros_activity_id` 去重）
- SSE 串流同步進度（複用現有 `/api/v1/sync/start` 模式）
- Token 持久化到 DB（`sync_state` 表）
- Docker volume 配置（`~/.wko5coach/` 已掛載）

### Out of Scope
- WKO5 資料夾讀寫（完全獨立）
- TrainingPeaks 作為主要資料源
- 心跳同步 / WebSocket push（手動觸發或定時 cron 足夠）
- Coros Training Plans / Structured Workouts 解析
- 多運動員帳號切換

---

## System Context

### Actors

| Actor | Type | Interaction |
|-------|------|-------------|
| 個人運動員 | Human — Browser | 觸發 sync、查看進度 |
| Coros Training Hub API | External Service | 提供活動列表 + FIT 下載 URL |
| 本機 FileSystem | Storage | `~/.wko5coach/fits/` 儲存 .fit |

### External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| `teamcnapi.coros.com` | 身份驗證 + 活動列表 | 降級：手動匯入 .fit |
| Coros S3 presigned URL | FIT 檔案下載 | 重試 3 次，記錄失敗 |
| 現有 `file_service.py` | FIT 解析 + DB 匯入 | 同步失敗，不影響已存 |

---

## Architecture

### 新增元件

```
backend/
├── sync/
│   ├── tp_client.py        # 保留（TP optional）
│   └── coros_client.py     # NEW: Coros API 客戶端
├── api/
│   └── sync.py             # 新增 /api/v1/sync/coros/* endpoints
└── db/
    └── models.py           # 新增 coros_activity_id 欄位
```

### FIT 儲存路徑

```
~/.wko5coach/
├── wko5coach.db           # SQLite DB（已有）
└── fits/                  # NEW: 獨立 FIT 資料夾
    └── {athlete_name}/
        └── {year}/
            └── {coros_id}_{YYYY-MM-DD}_{sport}.fit
```

### 同步流程

```
POST /api/v1/sync/coros/start?since=YYYY-MM-DD
  ↓
coros_client.login() → 取得 / 驗證 accessToken
  ↓
coros_client.list_activities(since) → 活動列表（分頁）
  ↓
for each activity:
  if coros_activity_id in DB → skip
  coros_client.download_fit(activity) → bytes
  save to ~/.wko5coach/fits/{athlete}/{year}/{filename}.fit
  file_service._import_one_file(path, source="coros", coros_activity_id=id)
  SSE: {"status": "downloaded", "activity_id": id, "date": ...}
  ↓
SSE: {"status": "complete", "total_downloaded": N, "total_checked": M}
```

---

## Data Model 變更

### `workout_files` 表新增欄位

```sql
ALTER TABLE workout_files ADD COLUMN coros_activity_id TEXT;
ALTER TABLE workout_files ADD COLUMN coros_sport_type  INTEGER;
-- source 欄位新增值: 'coros'（原有 'local' | 'trainingpeaks'）
```

### `sync_state` 表新增欄位

```sql
ALTER TABLE sync_state ADD COLUMN coros_access_token TEXT;
ALTER TABLE sync_state ADD COLUMN coros_token_expires DATETIME;
ALTER TABLE sync_state ADD COLUMN coros_last_sync_at  DATETIME;
ALTER TABLE sync_state ADD COLUMN coros_email         TEXT;  -- 記錄已登入帳號
```

---

## API Contracts

### 新增 Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/auth/coros/login` | Coros 登入（email + password） |
| GET | `/api/v1/auth/coros/status` | 登入狀態 + 帳號資訊 |
| POST | `/api/v1/auth/coros/logout` | 清除 token |
| POST | `/api/v1/sync/coros/start` | 觸發同步（SSE stream） |

### Request / Response

**POST /api/v1/auth/coros/login**
```json
// Request
{ "email": "user@example.com", "password": "plaintext_password" }

// Response 200
{
  "authenticated": true,
  "coros_user_id": "...",
  "email": "user@example.com",
  "token_expires": "2026-06-15T00:00:00Z"
}

// Response 401
{ "detail": "COROS_LOGIN_FAILED: Invalid credentials" }
```

**POST /api/v1/sync/coros/start** — SSE stream
```
data: {"status": "started", "since": "2026-01-01"}
data: {"status": "checking", "activity_id": "...", "date": "2026-05-14"}
data: {"status": "downloaded", "activity_id": "...", "file": "...fit", "sport": "cycling"}
data: {"status": "skipped", "activity_id": "...", "reason": "already_imported"}
data: {"status": "error", "activity_id": "...", "error": "download_failed"}
data: {"status": "complete", "total_downloaded": 3, "total_checked": 10}
```

---

## Implementation Plan

### 優先順序

1. `backend/sync/coros_client.py` — 登入 + 活動列表 + 下載
2. `backend/api/auth.py` — `/auth/coros/*` endpoints
3. `backend/api/sync.py` — `/sync/coros/start` SSE endpoint
4. `backend/db/models.py` — 新增欄位 migration
5. `backend/files/file_service.py` — `source="coros"` 支援（已有架構）

### coros_client.py 設計

```python
class CorosClient:
    BASE_URL = "https://teamcnapi.coros.com"

    async def login(self, email: str, password: str, db: AsyncSession) -> dict:
        """MD5-hash password, POST /account/login, persist token to sync_state."""

    async def _ensure_token(self, db: AsyncSession) -> str:
        """Return valid token; re-login if expired."""

    async def list_activities(
        self, db: AsyncSession, since: date, page_size: int = 20
    ) -> AsyncIterator[dict]:
        """Yield activity dicts (all pages) with sportType + labelId."""

    async def download_fit(self, activity: dict) -> bytes:
        """Download raw .fit bytes via fitUrl or fit/url endpoint."""

    async def sync_workouts(
        self, db: AsyncSession, athlete_id: int, since: date
    ) -> AsyncIterator[dict]:
        """Full sync flow: list → filter new → download → import → yield progress."""
```

---

## Non-Functional Requirements

| NFR | Target |
|-----|--------|
| Coros API timeout | 30s per request |
| FIT download timeout | 60s per file |
| 重試 | 失敗重試最多 3 次，指數退避 |
| Token 儲存 | DB 加密欄位（明文儲存可接受，因本機 SQLite） |
| Rate limit | 活動列表間隔 ≥1s，FIT 下載間隔 ≥0.5s |
| API 不穩定容錯 | 單一 activity 下載失敗不影響其他 activity |

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Coros API endpoint 改版 | Medium | High | 追蹤 `xballoy/coros-api` issue，降級為手動 .fit 匯入 |
| MD5 password 認證改版 | Low | High | 登入失敗時提示使用者手動匯入 |
| Rate limit / IP ban | Low | Medium | 同步間隔限速，避免短時間大量請求 |
| fitUrl presigned URL 時效 | Low | Low | 列表後立即下載，不快取 URL |

---

*Generated: 2026-05-15*
*Status: DRAFT*
